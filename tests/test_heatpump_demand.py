"""Unit tests for gas-calibrated useful thermal space heating and DHW demand modeling.

Tests cover:
1. Gas bill allocation:
   - Exact reconciliation with explicit synthetic monthly bills.
   - Linear scaling with boiler efficiency and gas PCS.
   - Independence from heat pump SCOP and electricity.
   - Input validation: negative gas, non-finite values, cooking exceeding nonheat.
   - Metadata disclaimers for boiler efficiency and DHW gross reference.
2. Heat pump operating window scheduling:
   - Disjoint windows, overnight wrapping, equal-hour disabling, and window union.
   - Strict input validation and rejection of all-disabled windows in scheduled mode.
   - Continuous mode activation of all 24 hours.
3. Timezone and civil clock gating:
   - Civil vs standard mode selection.
   - Handling of repeated and missing civil hours (DST transitions).
4. Thermal demand allocation:
   - Conservation of fixed monthly gas reference budgets in cold vs warm weather.
   - Zero demand outside active operating hours.
   - Uniform fallback distribution when all active hours have zero degree-hours.
   - Rejection when no active valid hours exist for a positive monthly budget.
5. Weather-sensitive mode:
   - Degree-hour scaling against explicit fixed reference denominator.
   - Multi-year weather sensitivity without artificial annual renormalization.
   - Requirement of explicit reference denominator for single target-year runs.
   - Climatology proxy and building dynamics metadata disclaimers.
6. Indoor comfort, balance temperature, and setback sensitivity:
   - Monotonic increase with indoor comfort temperature (17 -> 18 -> 20 °C).
   - Monotonic increase with outdoor balance temperature (16 -> 18 -> 20 °C).
   - Monotonic decrease with off-hours setback (0 -> 2 -> 4 °C) in scheduled mode.
   - Conservation of baseline gas reference at 18°C with zero extra recovery energy.
7. Dimensionality and broadcasting:
   - Broadcasting 1D calendar arrays to 2D weather matrices.
   - Single-year 1D input returning 1D demand arrays.
"""

from __future__ import annotations

import math
import unittest

import numpy as np

from feasibility.heatpump_demand import (
    DEFAULT_BOILER_EFFICIENCY,
    DEFAULT_COOKING_SMC_ANNUAL,
    DEFAULT_GAS_PCS,
    DEFAULT_HEATING_BALANCE_TEMP,
    DEFAULT_INDOOR_COMFORT_TEMP,
    DEFAULT_SAVED_EVENING_WINDOW,
    DEFAULT_SAVED_MORNING_WINDOW,
    DEFAULT_SUMMER_GAS_BASE,
    GasAllocationResult,
    HeatPumpDemandResult,
    allocate_gas_consumption as _allocate_gas_consumption,
    build_active_heating_mask,
    calculate_heat_demand as _calculate_heat_demand,
    compute_reference_degree_hours,
    window_hours,
)


# Legacy assertions use the explicitly bootstrapped external fixture, not a
# production default. New carrier diagnostics call the pure APIs directly.
from feasibility.runtime_context import current_runtime
CONFIRMED_GAS_MONTHLY_SMC = tuple(current_runtime().thermal_reference['GAS_MONTHLY'])


def allocate_gas_consumption(**kwargs):
    kwargs.setdefault('monthly_gas', CONFIRMED_GAS_MONTHLY_SMC)
    return _allocate_gas_consumption(**kwargs)


def calculate_heat_demand(**kwargs):
    kwargs.setdefault('monthly_gas', CONFIRMED_GAS_MONTHLY_SMC)
    return _calculate_heat_demand(**kwargs)


def make_synthetic_calendar(n_years: int = 1, hours_per_month: int = 24) -> dict[str, np.ndarray]:
    """Generate a compact synthetic calendar with all 12 months for rapid testing."""
    total_hours = 12 * hours_per_month
    months_1d = np.repeat(np.arange(1, 13), hours_per_month)
    hours_1d = np.tile(np.arange(24), total_hours // 24)
    civil_1d = np.copy(hours_1d)
    valid_1d = np.ones(total_hours, dtype=bool)

    if n_years == 1:
        return {
            "months": months_1d,
            "hours": hours_1d,
            "civil_hours": civil_1d,
            "valid": valid_1d,
        }

    return {
        "months": np.broadcast_to(months_1d, (n_years, total_hours)),
        "hours": np.broadcast_to(hours_1d, (n_years, total_hours)),
        "civil_hours": np.broadcast_to(civil_1d, (n_years, total_hours)),
        "valid": np.broadcast_to(valid_1d, (n_years, total_hours)),
    }


class TestGasAllocation(unittest.TestCase):
    """Test gas bill allocation and useful thermal energy conversion."""

    def test_gas_split_exact_defaults(self) -> None:
        """Synthetic monthly gas inputs must reconcile exactly under explicit parameters."""
        res = allocate_gas_consumption()
        self.assertAlmostEqual(res.annual_totals["monthly_gas_smc"], float(sum(CONFIRMED_GAS_MONTHLY_SMC)), places=9)
        self.assertAlmostEqual(res.annual_totals["heating_smc"], float(sum(max(0., g-DEFAULT_SUMMER_GAS_BASE) for g in CONFIRMED_GAS_MONTHLY_SMC)), places=9)
        self.assertAlmostEqual(res.annual_totals["dhw_smc"], float(sum(min(g,DEFAULT_SUMMER_GAS_BASE) for g in CONFIRMED_GAS_MONTHLY_SMC)-DEFAULT_COOKING_SMC_ANNUAL), places=9)
        self.assertAlmostEqual(res.annual_totals["cooking_smc"], DEFAULT_COOKING_SMC_ANNUAL, places=9)
        self.assertAlmostEqual(res.annual_totals["nonheat_smc"], float(sum(min(g,DEFAULT_SUMMER_GAS_BASE) for g in CONFIRMED_GAS_MONTHLY_SMC)), places=9)

    def test_gas_split_reconciles_monthly(self) -> None:
        """Every individual month must reconcile exactly:
        heating + nonheat == monthly_gas, and cooking + dhw == nonheat."""
        res = allocate_gas_consumption()
        for m in range(12):
            with self.subTest(month=m + 1):
                self.assertAlmostEqual(
                    res.heating_smc[m] + res.nonheat_smc[m],
                    res.monthly_gas[m],
                    places=10,
                )
                self.assertAlmostEqual(
                    res.cooking_smc[m] + res.dhw_smc[m],
                    res.nonheat_smc[m],
                    places=10,
                )
                self.assertGreaterEqual(res.heating_smc[m], 0.0)
                self.assertGreaterEqual(res.dhw_smc[m], 0.0)
                self.assertGreaterEqual(res.cooking_smc[m], 0.0)

    def test_useful_thermal_units_and_linearity(self) -> None:
        """Useful heat equals allocated Smc * PCS * boiler_efficiency.
        Must scale linearly with efficiency and PCS, and remain in thermal kWh."""
        res1 = allocate_gas_consumption(gas_pcs=11.0, boiler_efficiency=0.70)
        # 412 Smc * 11.0 kWh/Smc * 0.70 = 3205.97024 kWh
        expected_heating_useful = float(sum(max(0., g-DEFAULT_SUMMER_GAS_BASE) for g in CONFIRMED_GAS_MONTHLY_SMC)) * 11.0 * 0.70
        expected_dhw_useful = float(sum(min(g,DEFAULT_SUMMER_GAS_BASE) for g in CONFIRMED_GAS_MONTHLY_SMC)-DEFAULT_COOKING_SMC_ANNUAL) * 11.0 * 0.70
        self.assertAlmostEqual(res1.annual_totals["heating_useful_kwh"], expected_heating_useful, places=5)
        self.assertAlmostEqual(res1.annual_totals["dhw_gross_useful_kwh"], expected_dhw_useful, places=5)

        # Linearity check with higher efficiency
        res2 = allocate_gas_consumption(gas_pcs=11.0, boiler_efficiency=0.85)
        self.assertAlmostEqual(
            res2.annual_totals["heating_useful_kwh"],
            expected_heating_useful * (0.85 / 0.70),
            places=5,
        )

        # Linearity check with different PCS
        res3 = allocate_gas_consumption(gas_pcs=10.0, boiler_efficiency=0.70)
        self.assertAlmostEqual(
            res3.annual_totals["heating_useful_kwh"],
            float(sum(max(0., g-DEFAULT_SUMMER_GAS_BASE) for g in CONFIRMED_GAS_MONTHLY_SMC)) * 10.0 * 0.70,
            places=5,
        )

    def test_independence_from_scop(self) -> None:
        """Gas allocation and useful thermal demand depend on gas volume and boiler efficiency,
        never heat pump electricity or AC SCOP."""
        # allocate_gas_consumption takes no SCOP parameter
        res = allocate_gas_consumption()
        # Verify units are thermal kWh, not divided by any SCOP
        self.assertAlmostEqual(res.annual_totals["heating_useful_kwh"], float(sum(max(0., g-DEFAULT_SUMMER_GAS_BASE) for g in CONFIRMED_GAS_MONTHLY_SMC)) * DEFAULT_GAS_PCS * DEFAULT_BOILER_EFFICIENCY, places=5)

    def test_zero_cooking_allocation(self) -> None:
        """When cooking_smc_annual is 0, nonheat is entirely DHW."""
        res = allocate_gas_consumption(cooking_smc_annual=0.0)
        self.assertAlmostEqual(res.annual_totals["cooking_smc"], 0.0, places=10)
        self.assertAlmostEqual(res.annual_totals["dhw_smc"], res.annual_totals["nonheat_smc"], places=10)
        np.testing.assert_allclose(res.dhw_smc, res.nonheat_smc)

    def test_invalid_parameters_rejected(self) -> None:
        """Reject negative values, non-finite values, and cooking exceeding nonheat."""
        # cooking > nonheat
        with self.assertRaises(ValueError):
            allocate_gas_consumption(summer_gas_base=26.0, cooking_smc_annual=500.0)

        # Negative gas
        bad_gas = list(CONFIRMED_GAS_MONTHLY_SMC)
        bad_gas[0] = -10.0
        with self.assertRaises(ValueError):
            allocate_gas_consumption(monthly_gas=bad_gas)

        # Wrong length
        with self.assertRaises(ValueError):
            allocate_gas_consumption(monthly_gas=[100.0] * 11)

        # Non-finite values
        for val in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValueError):
                allocate_gas_consumption(summer_gas_base=val)
            with self.assertRaises(ValueError):
                allocate_gas_consumption(boiler_efficiency=val)
            with self.assertRaises(ValueError):
                allocate_gas_consumption(gas_pcs=val)

        # Efficiency <= 0 or > 1
        with self.assertRaises(ValueError):
            allocate_gas_consumption(boiler_efficiency=0.0)
        with self.assertRaises(ValueError):
            allocate_gas_consumption(boiler_efficiency=1.05)

        # Booleans rejected
        with self.assertRaises(TypeError):
            allocate_gas_consumption(summer_gas_base=True)

    def test_metadata_disclaimers(self) -> None:
        """Metadata must contain required unverified estimate and prior-loss disclaimers."""
        res = allocate_gas_consumption()
        self.assertIn("UNVERIFIED USER ESTIMATE", res.metadata["efficiency_status"])
        self.assertIn("prior-loss", res.metadata["dhw_reference_note"])
        self.assertIn("Cooking separate", res.metadata["cooking_status"])


class TestOperatingWindows(unittest.TestCase):
    """Test heat pump schedule windows: wrapping, equal-disabled, and union."""

    def test_window_hours_standard_and_wrap(self) -> None:
        """Standard window, overnight wrap, and single hour wrap."""
        # Standard daytime window
        self.assertEqual(window_hours(8, 11), {8, 9, 10})
        # Overnight wrapping window (e.g. 22:00 to 06:00)
        self.assertEqual(window_hours(22, 6), {22, 23, 0, 1, 2, 3, 4, 5})
        # Single-hour window
        self.assertEqual(window_hours(23, 0), {23})
        # Equal hours disables window
        self.assertEqual(window_hours(8, 8), set())
        self.assertEqual(window_hours(16, 16), set())

    def test_window_hours_overlap_and_union(self) -> None:
        """Overlapping morning and evening windows union without double counting."""
        w1 = window_hours(8, 12)
        w2 = window_hours(10, 16)
        self.assertEqual(w1 | w2, set(range(8, 16)))

    def test_window_hours_input_validation(self) -> None:
        """Non-integer, negative, or >= 24 hours must raise TypeError/ValueError."""
        for bad in (-1, 24, 100):
            with self.assertRaises(ValueError):
                window_hours(bad, 10)
            with self.assertRaises(ValueError):
                window_hours(10, bad)

        for bad_type in (8.5, "8", True, False, None):
            with self.assertRaises(TypeError):
                window_hours(bad_type, 10)
            with self.assertRaises(TypeError):
                window_hours(10, bad_type)

    def test_build_active_mask_continuous_vs_scheduled(self) -> None:
        """Continuous mode activates all valid hours; scheduled mode gates to permitted windows."""
        cal = make_synthetic_calendar(n_years=1, hours_per_month=24)
        mask_cont, hours_cont = build_active_heating_mask(
            cal["hours"], cal["civil_hours"], cal["valid"], schedule_mode="continuous"
        )
        self.assertEqual(hours_cont, set(range(24)))
        self.assertTrue(np.all(mask_cont == cal["valid"]))

        # Scheduled mode with default saved windows (8..11 and 16..20)
        mask_sched, hours_sched = build_active_heating_mask(
            cal["hours"], cal["civil_hours"], cal["valid"],
            schedule_mode="scheduled",
            morning_window=(8, 11),
            evening_window=(16, 20),
        )
        expected_active = {8, 9, 10, 16, 17, 18, 19}
        self.assertEqual(hours_sched, expected_active)
        self.assertTrue(np.all(cal["hours"][mask_sched] != 0))
        self.assertTrue(np.all(np.isin(cal["hours"][mask_sched], list(expected_active))))

    def test_all_disabled_in_scheduled_mode_raises(self) -> None:
        """In scheduled mode, if all windows are disabled, must raise ValueError."""
        cal = make_synthetic_calendar(n_years=1, hours_per_month=24)
        with self.assertRaises(ValueError) as ctx:
            build_active_heating_mask(
                cal["hours"], cal["civil_hours"], cal["valid"],
                schedule_mode="scheduled",
                morning_window=(8, 8),
                evening_window=(16, 16),
            )
        self.assertIn("all heating windows are disabled", str(ctx.exception))


class TestTimezoneModeAndCivilClock(unittest.TestCase):
    """Test civil vs standard timezone gating, including DST repeated and missing hours."""

    def test_civil_vs_standard_hour_selection(self) -> None:
        """Civil mode gates by civil_hours, standard mode gates by hours."""
        # Create a day where civil hour is standard hour + 1 (CEST summer time)
        hours = np.arange(24)
        civil_hours = (hours + 1) % 24
        valid = np.ones(24, dtype=bool)

        # Window 8..11 (hours 8, 9, 10)
        mask_civ, _ = build_active_heating_mask(
            hours, civil_hours, valid,
            schedule_mode="scheduled", timezone_mode="civil",
            morning_window=(8, 11), evening_window=(16, 16),
        )
        mask_std, _ = build_active_heating_mask(
            hours, civil_hours, valid,
            schedule_mode="scheduled", timezone_mode="standard",
            morning_window=(8, 11), evening_window=(16, 16),
        )

        # In civil mode, civil hours 8, 9, 10 correspond to standard hours 7, 8, 9
        self.assertEqual(set(hours[mask_civ]), {7, 8, 9})
        # In standard mode, standard hours 8, 9, 10 are active
        self.assertEqual(set(hours[mask_std]), {8, 9, 10})

    def test_dst_repeated_and_missing_hours(self) -> None:
        """Synthetic fixture with missing civil hour (spring DST) and repeated civil hour (autumn DST).
        Gating must handle irregular sequences without error."""
        # Spring forward: 0, 1, 3, 4 ... 23 (23 slots, hour 2 skipped)
        spring_hours = np.array([0, 1, 3, 4, 5, 6, 7, 8, 9, 10])
        valid = np.ones_like(spring_hours, dtype=bool)
        mask_spring, _ = build_active_heating_mask(
            spring_hours, spring_hours, valid,
            schedule_mode="scheduled", timezone_mode="civil",
            morning_window=(1, 4), evening_window=(8, 10),
        )
        # Permitted: 1, 2, 3, 8, 9. In spring_hours, 1, 3, 8, 9 are present
        self.assertEqual(set(spring_hours[mask_spring]), {1, 3, 8, 9})

        # Autumn back: 0, 1, 2, 2, 3, 4 (repeated hour 2)
        autumn_hours = np.array([0, 1, 2, 2, 3, 4, 8, 9])
        valid_autumn = np.ones_like(autumn_hours, dtype=bool)
        mask_autumn, _ = build_active_heating_mask(
            autumn_hours, autumn_hours, valid_autumn,
            schedule_mode="scheduled", timezone_mode="civil",
            morning_window=(2, 3), evening_window=(16, 16),
        )
        # Permitted: 2. Both repeated slots must be marked active
        self.assertEqual(np.sum(mask_autumn), 2)
        self.assertTrue(np.all(autumn_hours[mask_autumn] == 2))


class TestThermalDemandAllocation(unittest.TestCase):
    """Test fixed reference thermal budgets, degree-hour weighting, and active hour gating."""

    def setUp(self) -> None:
        self.cal = make_synthetic_calendar(n_years=1, hours_per_month=24)
        # Cold temperature profile: varies between 0°C and 10°C
        self.temp_cold = np.linspace(0.0, 10.0, len(self.cal["hours"]))

    def test_fixed_reference_monthly_sums_cold_vs_warm(self) -> None:
        """In default fixed budget mode (weather_sensitive=False), cold and warm weather cases
        must yield the EXACT same monthly useful thermal energy sums (matching gas reference)."""
        temp_cold = np.full(len(self.cal["hours"]), 2.0)
        temp_warm = np.full(len(self.cal["hours"]), 12.0)

        res_cold = calculate_heat_demand(
            temperature=temp_cold,
            valid=self.cal["valid"],
            months=self.cal["months"],
            hours=self.cal["hours"],
            civil_hours=self.cal["civil_hours"],
            schedule_mode="continuous",
            weather_sensitive=False,
        )
        res_warm = calculate_heat_demand(
            temperature=temp_warm,
            valid=self.cal["valid"],
            months=self.cal["months"],
            hours=self.cal["hours"],
            civil_hours=self.cal["civil_hours"],
            schedule_mode="continuous",
            weather_sensitive=False,
        )

        gas_alloc = allocate_gas_consumption()
        expected_monthly = gas_alloc.heating_useful_kwh

        np.testing.assert_allclose(res_cold.monthly_heat_kwh, expected_monthly, rtol=1e-10)
        np.testing.assert_allclose(res_warm.monthly_heat_kwh, expected_monthly, rtol=1e-10)
        self.assertAlmostEqual(res_cold.annual_heat_kwh, res_warm.annual_heat_kwh, places=8)

    def test_zero_heating_outside_active_hours(self) -> None:
        """In scheduled mode, hourly useful thermal demand must be strictly 0 outside active hours."""
        res = calculate_heat_demand(
            temperature=self.temp_cold,
            valid=self.cal["valid"],
            months=self.cal["months"],
            hours=self.cal["hours"],
            civil_hours=self.cal["civil_hours"],
            schedule_mode="scheduled",
            morning_window=(8, 11),
            evening_window=(16, 20),
            weather_sensitive=False,
        )
        # Hours outside {8, 9, 10, 16, 17, 18, 19} must have zero demand
        off_mask = ~res.active_mask
        self.assertTrue(np.all(res.heat_demand_kwh[off_mask] == 0.0))
        # Within active hours, demand is non-negative
        self.assertTrue(np.all(res.heat_demand_kwh[res.active_mask] >= 0.0))

    def test_uniform_fallback_when_zero_degrees(self) -> None:
        """When all active hours in a month have zero positive degree-hours (temperature >= balance),
        the monthly budget must be distributed uniformly across active hours without energy loss."""
        # Warm temperature 25°C throughout
        temp_warm = np.full(len(self.cal["hours"]), 25.0)
        res = calculate_heat_demand(
            temperature=temp_warm,
            valid=self.cal["valid"],
            months=self.cal["months"],
            hours=self.cal["hours"],
            civil_hours=self.cal["civil_hours"],
            heating_balance_temp=18.0,
            schedule_mode="scheduled",
            morning_window=(8, 11),
            evening_window=(16, 20),
            weather_sensitive=False,
        )
        gas_alloc = allocate_gas_consumption()
        np.testing.assert_allclose(res.monthly_heat_kwh, gas_alloc.heating_useful_kwh, rtol=1e-10)

        # Check uniformity for month 1 (where heating budget is positive)
        m1_active = (self.cal["months"] == 1) & res.active_mask
        demands_m1 = res.heat_demand_kwh[m1_active]
        self.assertTrue(np.allclose(demands_m1, demands_m1[0]))

    def test_no_active_hours_raises_error(self) -> None:
        """If a month has positive heating budget but zero active valid hours, must raise ValueError."""
        # Make valid=False during all operating hours for month 1
        valid_bad = np.copy(self.cal["valid"])
        active_hours = {8, 9, 10, 16, 17, 18, 19}
        m1_active_slots = (self.cal["months"] == 1) & np.isin(self.cal["hours"], list(active_hours))
        valid_bad[m1_active_slots] = False

        with self.assertRaises(ValueError) as ctx:
            calculate_heat_demand(
                temperature=self.temp_cold,
                valid=valid_bad,
                months=self.cal["months"],
                hours=self.cal["hours"],
                civil_hours=self.cal["civil_hours"],
                schedule_mode="scheduled",
                morning_window=(8, 11),
                evening_window=(16, 20),
            )
        self.assertIn("No active valid heating hours in month 1", str(ctx.exception))


class TestWeatherSensitiveMode(unittest.TestCase):
    """Test degree-hour weather scaling against explicit fixed reference denominator."""

    def test_weather_ratio_against_explicit_fixed_denominator(self) -> None:
        """When explicit reference_degree_hours is supplied, monthly demand scales exactly
        by (all_hours_degree_hours / ref_degree_hours)."""
        cal = make_synthetic_calendar(n_years=1, hours_per_month=24)
        # Constant outdoor temp of 10°C, balance temp 18°C -> (18 - 10) = 8 degree-hours per hour
        temp = np.full(len(cal["hours"]), 10.0)
        # Total degree-hours per month = 24 hours * 8 = 192 degree-hours
        # Set explicit reference denominator to 96 degree-hours -> expected ratio = 192 / 96 = 2.0
        ref_dh = np.full(12, 96.0)

        res = calculate_heat_demand(
            temperature=temp,
            valid=cal["valid"],
            months=cal["months"],
            hours=cal["hours"],
            civil_hours=cal["civil_hours"],
            heating_balance_temp=18.0,
            weather_sensitive=True,
            reference_degree_hours=ref_dh,
        )

        gas_alloc = allocate_gas_consumption()
        expected_monthly = gas_alloc.heating_useful_kwh * 2.0
        np.testing.assert_allclose(res.weather_ratios, 2.0, rtol=1e-10)
        np.testing.assert_allclose(res.monthly_heat_kwh, expected_monthly, rtol=1e-10)

    def test_weather_sensitive_multi_year_no_renormalization(self) -> None:
        """In a multi-year weather ensemble, cold years demand more heat than warm years,
        and individual years are NOT renormalized back to the fixed budget."""
        n_years = 2
        total_hours = 12 * 24
        temp_2y = np.zeros((n_years, total_hours))
        # Year 0: very cold (0°C)
        temp_2y[0] = 0.0
        # Year 1: mild (12°C)
        temp_2y[1] = 12.0

        cal = make_synthetic_calendar(n_years=2, hours_per_month=24)
        res = calculate_heat_demand(
            temperature=temp_2y,
            valid=cal["valid"],
            months=cal["months"],
            hours=cal["hours"],
            civil_hours=cal["civil_hours"],
            source_years=["ColdYear", "MildYear"],
            heating_balance_temp=18.0,
            weather_sensitive=True,
            ensemble_temperature=temp_2y,
            ensemble_valid=cal["valid"],
            ensemble_months=cal["months"],
        )

        # Cold year annual useful heat must be strictly greater than mild year
        self.assertGreater(res.annual_heat_kwh[0], res.annual_heat_kwh[1])
        # Neither year is forced to equal the baseline reference
        gas_alloc = allocate_gas_consumption()
        base_annual = gas_alloc.annual_totals["heating_useful_kwh"]
        self.assertNotEqual(res.annual_heat_kwh[0], base_annual)
        self.assertNotEqual(res.annual_heat_kwh[1], base_annual)

    def test_single_year_without_explicit_denominator_rejected(self) -> None:
        """Omitting reference_degree_hours and ensemble in weather_sensitive mode
        must raise ValueError for both single-year and multi-year runs (no silent self-calibration)."""
        # Single-year
        cal = make_synthetic_calendar(n_years=1, hours_per_month=24)
        temp = np.full(len(cal["hours"]), 5.0)

        with self.assertRaises(ValueError) as ctx:
            calculate_heat_demand(
                temperature=temp,
                valid=cal["valid"],
                months=cal["months"],
                hours=cal["hours"],
                civil_hours=cal["civil_hours"],
                weather_sensitive=True,
                reference_degree_hours=None,
            )
        self.assertIn("explicit reference_degree_hours", str(ctx.exception))

        # Multi-year without denominator must also raise (never silently self-calibrate)
        cal2 = make_synthetic_calendar(n_years=2, hours_per_month=24)
        temp2 = np.full((2, len(cal["hours"])), 5.0)
        with self.assertRaises(ValueError) as ctx2:
            calculate_heat_demand(
                temperature=temp2,
                valid=cal2["valid"],
                months=cal2["months"],
                hours=cal2["hours"],
                civil_hours=cal2["civil_hours"],
                weather_sensitive=True,
                reference_degree_hours=None,
            )
        self.assertIn("explicit reference_degree_hours", str(ctx2.exception))

        # Partial ensemble (missing ensemble_months) must also raise
        with self.assertRaises(ValueError) as ctx3:
            calculate_heat_demand(
                temperature=temp2,
                valid=cal2["valid"],
                months=cal2["months"],
                hours=cal2["hours"],
                civil_hours=cal2["civil_hours"],
                weather_sensitive=True,
                ensemble_temperature=temp2,
                ensemble_valid=cal2["valid"],
                ensemble_months=None,
            )
        self.assertIn("explicit reference_degree_hours", str(ctx3.exception))

    def test_metadata_climatology_and_dynamics_disclaimers(self) -> None:
        """Result metadata must clearly label historical climatology proxy and building inertia limits."""
        cal = make_synthetic_calendar(n_years=1, hours_per_month=24)
        temp = np.full(len(cal["hours"]), 5.0)
        res = calculate_heat_demand(
            temperature=temp,
            valid=cal["valid"],
            months=cal["months"],
            hours=cal["hours"],
            civil_hours=cal["civil_hours"],
        )
        self.assertIn("climatology PROXY", res.metadata["climatology_proxy_label"])
        self.assertIn("cannot identify building thermal mass", res.metadata["unresolved_dynamics_disclaimer"])
        self.assertIn("No RC network", res.metadata["unresolved_dynamics_disclaimer"])
        self.assertEqual(res.metadata["recovery_energy_added"], 0.0)


class TestComfortBalanceSetbackMonotonicity(unittest.TestCase):
    """Test quasi-steady comfort shift, balance temp, and off-hours setback monotonicity."""

    def setUp(self) -> None:
        self.cal = make_synthetic_calendar(n_years=1, hours_per_month=24)
        # Moderate temperature profile: 8°C throughout
        self.temp = np.full(len(self.cal["hours"]), 8.0)
        # 10 degree-hours per hour at baseline 18°C = 240 degree-hours/month
        self.ref_dh = np.full(12, 240.0)

    def test_indoor_comfort_temp_monotonicity(self) -> None:
        """Increasing indoor comfort temperature (17 -> 18 -> 20 °C) strictly increases heat demand."""
        demands = []
        for t_in in (17.0, 18.0, 20.0):
            res = calculate_heat_demand(
                temperature=self.temp,
                valid=self.cal["valid"],
                months=self.cal["months"],
                hours=self.cal["hours"],
                civil_hours=self.cal["civil_hours"],
                indoor_temp=t_in,
                weather_sensitive=True,
                reference_degree_hours=self.ref_dh,
            )
            demands.append(res.annual_heat_kwh)

        self.assertLess(demands[0], demands[1])
        self.assertLess(demands[1], demands[2])

    def test_heating_balance_temp_monotonicity(self) -> None:
        """Increasing outdoor balance temperature (16 -> 18 -> 20 °C) strictly increases heat demand."""
        demands = []
        for t_bal in (16.0, 18.0, 20.0):
            res = calculate_heat_demand(
                temperature=self.temp,
                valid=self.cal["valid"],
                months=self.cal["months"],
                hours=self.cal["hours"],
                civil_hours=self.cal["civil_hours"],
                heating_balance_temp=t_bal,
                weather_sensitive=True,
                reference_degree_hours=self.ref_dh,
            )
            demands.append(res.annual_heat_kwh)

        self.assertLess(demands[0], demands[1])
        self.assertLess(demands[1], demands[2])

    def test_setback_delta_monotonicity(self) -> None:
        """Increasing off-hours setback (0 -> 2 -> 4 °C) in scheduled mode strictly decreases heat demand."""
        demands = []
        for setback in (0.0, 2.0, 4.0):
            res = calculate_heat_demand(
                temperature=self.temp,
                valid=self.cal["valid"],
                months=self.cal["months"],
                hours=self.cal["hours"],
                civil_hours=self.cal["civil_hours"],
                schedule_mode="scheduled",
                morning_window=(8, 11),
                evening_window=(16, 20),
                setback_delta=setback,
                weather_sensitive=True,
                reference_degree_hours=self.ref_dh,
            )
            demands.append(res.annual_heat_kwh)

        self.assertGreater(demands[0], demands[1])
        self.assertGreater(demands[1], demands[2])

    def test_no_extra_recovery_energy_added(self) -> None:
        """In scheduled mode with setback, heating delivered in active hours strictly matches
        the calculated budget with zero extra startup recovery energy added."""
        res = calculate_heat_demand(
            temperature=self.temp,
            valid=self.cal["valid"],
            months=self.cal["months"],
            hours=self.cal["hours"],
            civil_hours=self.cal["civil_hours"],
            schedule_mode="scheduled",
            morning_window=(8, 11),
            evening_window=(16, 20),
            setback_delta=3.0,
            weather_sensitive=True,
            reference_degree_hours=self.ref_dh,
        )
        # Sum of hourly heat demands in active hours must equal monthly totals exactly
        for m in range(1, 13):
            m_mask = (self.cal["months"] == m) & self.cal["valid"]
            self.assertAlmostEqual(
                float(np.sum(res.heat_demand_kwh[m_mask])),
                res.monthly_heat_kwh[m - 1],
                places=10,
            )
        self.assertEqual(res.metadata["recovery_energy_added"], 0.0)


class TestBroadcastingAndTargetCalendars(unittest.TestCase):
    """Test input shapes, broadcasting 1D target calendars over 2D weather matrices, and 1D outputs."""

    def test_broadcasting_1d_calendar_to_2d_weather(self) -> None:
        """A 2D weather matrix (Y x T) paired with 1D calendar arrays (T,) broadcasts cleanly."""
        n_years = 3
        hours_per_month = 24
        total_hours = 12 * hours_per_month

        # 1D calendar arrays
        cal = make_synthetic_calendar(n_years=1, hours_per_month=hours_per_month)
        # 2D weather temperatures
        temp_2d = np.zeros((n_years, total_hours))
        temp_2d[0] = 2.0
        temp_2d[1] = 6.0
        temp_2d[2] = 10.0

        res = calculate_heat_demand(
            temperature=temp_2d,
            valid=cal["valid"],
            months=cal["months"],
            hours=cal["hours"],
            civil_hours=cal["civil_hours"],
            source_years=[2005, 2006, 2007],
            weather_sensitive=False,
        )

        self.assertEqual(res.heat_demand_kwh.shape, (n_years, total_hours))
        self.assertEqual(res.monthly_heat_kwh.shape, (n_years, 12))
        self.assertEqual(len(res.annual_heat_kwh), n_years)

        # In fixed budget mode, all 3 years conserve the exact same monthly reference budget
        gas_alloc = allocate_gas_consumption()
        for yi in range(n_years):
            np.testing.assert_allclose(res.monthly_heat_kwh[yi], gas_alloc.heating_useful_kwh, rtol=1e-10)

    def test_single_year_1d_input_and_output(self) -> None:
        """When 1D temperature input is supplied, output arrays are squeezed to 1D and annual heat is float."""
        cal = make_synthetic_calendar(n_years=1, hours_per_month=24)
        temp_1d = np.full(len(cal["hours"]), 5.0)

        res = calculate_heat_demand(
            temperature=temp_1d,
            valid=cal["valid"],
            months=cal["months"],
            hours=cal["hours"],
            civil_hours=cal["civil_hours"],
        )

        self.assertEqual(res.heat_demand_kwh.ndim, 1)
        self.assertEqual(res.monthly_heat_kwh.ndim, 1)
        self.assertEqual(res.active_mask.ndim, 1)
        self.assertEqual(res.weather_ratios.ndim, 1)
        self.assertIsInstance(res.annual_heat_kwh, float)


class TestStage2DefectRegressions(unittest.TestCase):
    """Independent regression tests for Stage 2 review defects identified by oracles:
    1. Comfort sensitivity responding orthogonally to weather sensitivity.
    2. Weather sensitivity baseline ensemble mean remaining 1.0 at non-default balance temp (e.g. 16°C).
    3. Weather sensitive True with comfort sensitivity False excluding comfort shift from weather ratio.
    4. Default comfort_sensitivity responding to explicitly changed indoor_temp / setback_delta.
    5. Denial of silent self-calibration and strict denominator requirements.
    6. Explicit validation of zero reference degree-hours for positive heating budget.
    7. Robustness to engine padding (-1/0 months and hours, NaN temperature at invalid slots).
    8. Enforcement of zero extra recovery energy.
    """

    def setUp(self) -> None:
        self.cal = make_synthetic_calendar(n_years=1, hours_per_month=24)
        # Constant 10°C synthetic temperature across all 12 months
        self.temp_10 = np.full(len(self.cal["hours"]), 10.0)

    def test_oracle_defect1_comfort_sensitivity_orthogonal_to_weather_sensitive(self) -> None:
        """Oracle defect: indoor 18 vs 22 with comfort_sensitivity=True and weather_sensitive=False
        must scale demand (by 1.5 at balance 18 and outdoor 10), never producing identical 3205.97 kWh."""
        res_18 = calculate_heat_demand(
            temperature=self.temp_10,
            valid=self.cal["valid"],
            months=self.cal["months"],
            hours=self.cal["hours"],
            civil_hours=self.cal["civil_hours"],
            indoor_temp=18.0,
            comfort_sensitivity=True,
            weather_sensitive=False,
        )
        res_22 = calculate_heat_demand(
            temperature=self.temp_10,
            valid=self.cal["valid"],
            months=self.cal["months"],
            hours=self.cal["hours"],
            civil_hours=self.cal["civil_hours"],
            indoor_temp=22.0,
            comfort_sensitivity=True,
            weather_sensitive=False,
        )

        gas_alloc = allocate_gas_consumption()
        base_annual = gas_alloc.annual_totals["heating_useful_kwh"]

        # Baseline at 18 must equal gas allocation
        self.assertAlmostEqual(res_18.annual_heat_kwh, base_annual, places=4)
        # At 22: (22 - 10) / (18 - 10) = 12 / 8 = 1.5 ratio
        self.assertAlmostEqual(res_22.annual_heat_kwh, base_annual * 1.5, places=4)
        self.assertNotAlmostEqual(res_18.annual_heat_kwh, res_22.annual_heat_kwh, places=2)

        # Weather ratio is 1.0, comfort ratio is 1.5
        np.testing.assert_allclose(res_22.weather_ratios, 1.0)
        np.testing.assert_allclose(res_22.comfort_ratios, 1.5)
        np.testing.assert_allclose(res_18.comfort_ratios, 1.0)

    def test_oracle_defect2_weather_sensitive_balance_temp_16_preserves_mean_unity(self) -> None:
        """Oracle defect: heating_balance_temp=16 with self-reference ensemble must produce
        weather ratio 1.0 (not 0.75 from hardcoded 18 denominator)."""
        res = calculate_heat_demand(
            temperature=self.temp_10,
            valid=self.cal["valid"],
            months=self.cal["months"],
            hours=self.cal["hours"],
            civil_hours=self.cal["civil_hours"],
            heating_balance_temp=16.0,
            indoor_temp=18.0,
            weather_sensitive=True,
            ensemble_temperature=self.temp_10,
            ensemble_valid=self.cal["valid"],
            ensemble_months=self.cal["months"],
        )

        # Weather ratio must be strictly 1.0 across all 12 months, NOT 0.75!
        np.testing.assert_allclose(res.weather_ratios, 1.0, rtol=1e-10)
        np.testing.assert_allclose(res.comfort_ratios, 1.0, rtol=1e-10)

        gas_alloc = allocate_gas_consumption()
        self.assertAlmostEqual(res.annual_heat_kwh, gas_alloc.annual_totals["heating_useful_kwh"], places=4)

    def test_weather_sensitive_true_comfort_sensitivity_false_excludes_comfort_from_weather_ratio(self) -> None:
        """When weather_sensitive=True and comfort_sensitivity=False, comfort shift is excluded
        from weather ratio numerator, while active balance temp proxy is retained for hourly shape."""
        # 8 degree-hours per hour at balance 18 -> 192 degree-hours per month
        ref_dh = np.full(12, 192.0)

        res = calculate_heat_demand(
            temperature=self.temp_10,
            valid=self.cal["valid"],
            months=self.cal["months"],
            hours=self.cal["hours"],
            civil_hours=self.cal["civil_hours"],
            heating_balance_temp=18.0,
            indoor_temp=22.0,
            weather_sensitive=True,
            comfort_sensitivity=False,
            reference_degree_hours=ref_dh,
        )

        # Weather ratio is based on baseline degree hours (192 / 192 = 1.0), NOT shifted (288 / 192 = 1.5)
        np.testing.assert_allclose(res.weather_ratios, 1.0, rtol=1e-10)
        np.testing.assert_allclose(res.comfort_ratios, 1.0, rtol=1e-10)

        gas_alloc = allocate_gas_consumption()
        self.assertAlmostEqual(res.annual_heat_kwh, gas_alloc.annual_totals["heating_useful_kwh"], places=4)
        self.assertEqual(res.metadata["active_balance_temp"], 22.0)

    def test_default_comfort_sensitivity_responds_to_changed_indoor_and_setback(self) -> None:
        """Default comfort_sensitivity=None must respond to explicitly changed comfort/setback,
        leaving baseline 18 / no setback unchanged."""
        # Explicitly changed indoor_temp (22°C vs baseline 18°C) without passing comfort_sensitivity
        res_default_changed = calculate_heat_demand(
            temperature=self.temp_10,
            valid=self.cal["valid"],
            months=self.cal["months"],
            hours=self.cal["hours"],
            civil_hours=self.cal["civil_hours"],
            indoor_temp=22.0,
            weather_sensitive=False,
        )
        gas_alloc = allocate_gas_consumption()
        base_annual = gas_alloc.annual_totals["heating_useful_kwh"]
        self.assertAlmostEqual(res_default_changed.annual_heat_kwh, base_annual * 1.5, places=4)
        self.assertTrue(res_default_changed.metadata["comfort_sensitivity"])

        # Baseline 18 / no setback unchanged
        res_default_base = calculate_heat_demand(
            temperature=self.temp_10,
            valid=self.cal["valid"],
            months=self.cal["months"],
            hours=self.cal["hours"],
            civil_hours=self.cal["civil_hours"],
            indoor_temp=18.0,
            setback_delta=0.0,
            weather_sensitive=False,
        )
        self.assertAlmostEqual(res_default_base.annual_heat_kwh, base_annual, places=4)
        self.assertFalse(res_default_base.metadata["comfort_sensitivity"])

        # Changed setback in scheduled mode reduces demand
        res_setback = calculate_heat_demand(
            temperature=self.temp_10,
            valid=self.cal["valid"],
            months=self.cal["months"],
            hours=self.cal["hours"],
            civil_hours=self.cal["civil_hours"],
            schedule_mode="scheduled",
            morning_window=(8, 11),
            evening_window=(16, 20),
            setback_delta=3.0,
            weather_sensitive=False,
        )
        self.assertLess(res_setback.annual_heat_kwh, base_annual)
        self.assertTrue(res_setback.metadata["comfort_sensitivity"])

    def test_zero_reference_degree_hours_validation(self) -> None:
        """Zero reference degree-hours for a month with positive heating budget must raise ValueError;
        zero reference for a month with zero heating budget (e.g. July) is allowed."""
        # Month 1 has positive gas heating budget (120 Smc)
        bad_ref_dh = np.full(12, 192.0)
        bad_ref_dh[0] = 0.0  # Zero reference in month 1

        with self.assertRaises(ValueError) as ctx:
            calculate_heat_demand(
                temperature=self.temp_10,
                valid=self.cal["valid"],
                months=self.cal["months"],
                hours=self.cal["hours"],
                civil_hours=self.cal["civil_hours"],
                weather_sensitive=True,
                reference_degree_hours=bad_ref_dh,
            )
        self.assertIn("Zero reference degree-hours in month 1", str(ctx.exception))

        # Month 7 (July) has 0.0 heating budget
        ok_ref_dh = np.full(12, 192.0)
        ok_ref_dh[6] = 0.0  # Month 7 has zero heating budget
        zero_july=list(CONFIRMED_GAS_MONTHLY_SMC);zero_july[6]=0.
        res = calculate_heat_demand(
            monthly_gas=zero_july,
            temperature=self.temp_10,
            valid=self.cal["valid"],
            months=self.cal["months"],
            hours=self.cal["hours"],
            civil_hours=self.cal["civil_hours"],
            weather_sensitive=True,
            reference_degree_hours=ok_ref_dh,
        )
        self.assertEqual(res.monthly_heat_kwh[6], 0.0)

    def test_zero_baseline_degree_hours_for_positive_budget_rejected(self) -> None:
        """When outdoor temperature exceeds balance temperature so baseline degree-hours is 0
        in a month with positive budget, comfort ratio scaling must raise ValueError."""
        temp_hot = np.full(len(self.cal["hours"]), 25.0)  # > 18.0 balance
        with self.assertRaises(ValueError) as ctx:
            calculate_heat_demand(
                temperature=temp_hot,
                valid=self.cal["valid"],
                months=self.cal["months"],
                hours=self.cal["hours"],
                civil_hours=self.cal["civil_hours"],
                heating_balance_temp=18.0,
                indoor_temp=22.0,  # comfort_sensitivity active
                weather_sensitive=False,
            )
        self.assertIn("Zero baseline degree-hours in month 1", str(ctx.exception))

    def test_invalid_slot_padding_tolerance_and_valid_slot_validation(self) -> None:
        """Invalid padding (NaN temperature, 0/-1 months/hours) at invalid slots (valid=False)
        must not cause errors; non-finite or out-of-range values at valid slots must raise ValueError."""
        total_hours = len(self.cal["hours"])
        valid = np.ones(total_hours, dtype=bool)
        temp = np.full(total_hours, 10.0)
        months = np.copy(self.cal["months"])
        hours = np.copy(self.cal["hours"])
        civil_hours = np.copy(self.cal["civil_hours"])

        # Mark 4 slots as invalid and pad them with engine-style invalid markers
        invalid_indices = [0, 1, 24, 25]
        valid[invalid_indices] = False
        temp[invalid_indices] = np.nan
        months[invalid_indices] = -1
        hours[invalid_indices] = -1
        civil_hours[invalid_indices] = -1

        # Also set one invalid slot to month 0
        months[invalid_indices[0]] = 0

        # Must succeed on compute_reference_degree_hours
        dh_ref = compute_reference_degree_hours(temp, valid, months, balance_temp=18.0)
        self.assertEqual(len(dh_ref), 12)
        self.assertTrue(np.all(np.isfinite(dh_ref)))

        # Must succeed on calculate_heat_demand
        res = calculate_heat_demand(
            temperature=temp,
            valid=valid,
            months=months,
            hours=hours,
            civil_hours=civil_hours,
            weather_sensitive=False,
        )
        self.assertTrue(np.all(np.isfinite(res.monthly_heat_kwh)))

        # Setting NaN at a VALID slot must raise in both functions
        bad_temp = np.copy(temp)
        bad_temp[10] = np.nan  # valid slot
        with self.assertRaises(ValueError) as ctx:
            calculate_heat_demand(
                temperature=bad_temp,
                valid=valid,
                months=months,
                hours=hours,
                civil_hours=civil_hours,
            )
        self.assertIn("temperature contains non-finite values at valid slots", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            compute_reference_degree_hours(bad_temp, valid, months, balance_temp=18.0)
        self.assertIn("temperature contains non-finite values at valid slots", str(ctx.exception))

        # Setting invalid month at a VALID slot must raise in both functions
        bad_months = np.copy(months)
        bad_months[10] = 13  # valid slot
        with self.assertRaises(ValueError) as ctx:
            calculate_heat_demand(
                temperature=temp,
                valid=valid,
                months=bad_months,
                hours=hours,
                civil_hours=civil_hours,
            )
        self.assertIn("months at valid slots must be in range 1..12", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            compute_reference_degree_hours(temp, valid, bad_months, balance_temp=18.0)
        self.assertIn("months at valid slots must be in range 1..12", str(ctx.exception))

        # Setting invalid hour at a VALID slot must raise in calculate_heat_demand
        bad_hours = np.copy(hours)
        bad_hours[10] = 25  # valid slot
        with self.assertRaises(ValueError) as ctx:
            calculate_heat_demand(
                temperature=temp,
                valid=valid,
                months=months,
                hours=bad_hours,
                civil_hours=civil_hours,
            )
        self.assertIn("hours at valid slots must be in range 0..23", str(ctx.exception))

    def test_never_extra_recovery_energy_enforced(self) -> None:
        """In scheduled mode with setback and comfort shift, delivered energy strictly equals
        calculated budget, never fabricating unphysical recovery spikes."""
        res = calculate_heat_demand(
            temperature=self.temp_10,
            valid=self.cal["valid"],
            months=self.cal["months"],
            hours=self.cal["hours"],
            civil_hours=self.cal["civil_hours"],
            schedule_mode="scheduled",
            morning_window=(8, 11),
            evening_window=(16, 20),
            indoor_temp=21.0,
            setback_delta=4.0,
            weather_sensitive=False,
        )
        for m in range(1, 13):
            m_mask = (self.cal["months"] == m) & self.cal["valid"]
            self.assertAlmostEqual(
                float(np.sum(res.heat_demand_kwh[m_mask])),
                res.monthly_heat_kwh[m - 1],
                places=10,
            )
        self.assertEqual(res.metadata["recovery_energy_added"], 0.0)


if __name__ == "__main__":
    unittest.main()
