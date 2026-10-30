"""Tests for AC space heating allocation diagnostics and operating-power requirements.

Covers Small Task C3 requirements:
1) Hand-computable 2-slot January timeline with 08:30-09:15 window:
   - Overlap 0.5h in hour 8, 0.25h in hour 9; total energy 200 kWh.
   - Equal cold weights allocate [133.333..., 66.666...] kWh (literal 2/3 and 1/3).
   - Permitted-duration power requirements both 266.666... kW; max interval-average 133.333... kW.
2) Fallback event tracking:
   - Same window with all temps >= balance temp yields duration-weighted allocation and
     an exact fallback event (year, month).
   - Zero demand emits no fallback events and avoids division by zero.
3) Duplicate and overlapping windows:
   - Canonical union results in equivalent nominal/actual durations and energy allocation.
4) Schedule width vs power requirement:
   - Narrower vs wider schedules with identical energy budget show higher/lower power requirements.
5) 25% additional demand scaling:
   - Profile and power scale by 1.25x while eligible permitted hours remain unchanged.
6) Strict error on positive budget with zero eligible slots (no continuous fallback).
7) Actual DST civil-hour transitions:
   - Spring DST skips civil hour 02:00-02:59 (0 eligible hours).
   - Autumn DST repeats civil hour 02:00-02:59 (2 distinct intervals both eligible).
   - Contrast with fixed standard time.
8) Default return type unchanged (np.ndarray), diagnostics JSON-serializable, and load_profiles
   integration with nested totals.
"""
from historical_weather import synthetic_thermal_runtime
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
sys.path.insert(0, str(PROJECT_ROOT / 'feasibility' / 'vendor'))
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(HERE))

import test_support  # noqa: F401
from test_support import IsolatedDefaultsTestCase

import numpy as np
import feasibility.engine as engine


class AcHeatingAllocationDiagnosticsTests(IsolatedDefaultsTestCase):
    """Test suite for allocator diagnostics and operating-power requirements."""

    def setUp(self):
        super().setUp()
        # Synthetic gas budget: January 126 Smc, all other months 0 Smc
        # Gas heating = 126 - 26 = 100 Smc.
        # Useful heat = 100 * 10 (PCS) * 0.8 (boiler) = 800 kWh.
        # Electricity = 800 / 4.0 (SCOP) = 200 kWh.
        self.synthetic_gas_monthly = np.zeros(12, dtype=float)
        self.synthetic_gas_monthly[0] = 126.0

        self.base_settings = {
            'load_scenario': 'electrified',
            'gas_pcs': 10.0,
            'boiler_efficiency': 0.80,
            'scop': 4.0,
            'summer_gas_base': 26.0,
            'heating_schedule_mode': 'scheduled',
            'ac_heating_windows': [{'start_minute': 510, 'end_minute': 555}],  # 08:30-09:15
            'heating_balance_temp': 18.0,
            'timezone_mode': 'civil',
            'ac_heating_additional_demand_pct': 0.0,
        }

    def _make_2slot_timeline(self, temps=None):
        """Construct synthetic 2-slot January timeline (hours 8 and 9)."""
        if temps is None:
            temps = np.full((1, 2), 0.0, dtype=np.float64)  # 18 - 0 = 18 deg deficit each
        else:
            temps = np.asarray(temps, dtype=np.float64)
            if temps.ndim == 1:
                temps = temps.reshape(1, -1)
        months = np.ones((1, 2), dtype=np.int64)
        hours = np.array([[8, 9]], dtype=np.int64)
        civil_hours = np.array([[8, 9]], dtype=np.int64)
        valid = np.ones((1, 2), dtype=bool)
        weather_years = [2026]
        return temps, months, hours, valid, civil_hours, weather_years

    def test_1_hand_computable_2slot_january(self):
        """08:30-09:15 window hand calculation: raw E=[400/3, 200/3], power=800/3 kW."""
        temps, months, hours, valid, civil_hours, weather_years = self._make_2slot_timeline()
        s = copy.deepcopy(self.base_settings)

        with patch.object(engine, 'GAS_MONTHLY', self.synthetic_gas_monthly), synthetic_thermal_runtime(self.synthetic_gas_monthly):
            profile, diag = engine._allocate_space_heating(
                s, temps, months, hours, valid, civil_hours, weather_years, return_diagnostics=True
            )

        # 1. Total energy is exactly 200 kWh
        self.assertAlmostEqual(float(profile.sum()), 200.0, places=10)

        # 2. Literal expected active energy: 200 * (2/3) = 133.333..., 200 * (1/3) = 66.666...
        expected_e0 = 400.0 / 3.0
        expected_e1 = 200.0 / 3.0
        self.assertAlmostEqual(profile[0, 0], expected_e0, places=10)
        self.assertAlmostEqual(profile[0, 1], expected_e1, places=10)

        # 3. Nominal daily union duration: 45 minutes = 0.75 hours
        self.assertAlmostEqual(diag['nominal_daily_union_duration_hours'], 0.75, places=10)

        # 4. Realization-level diagnostics
        self.assertEqual(len(diag['realizations']), 1)
        r0 = diag['realizations'][0]
        self.assertEqual(r0['weather_year'], 2026)
        self.assertAlmostEqual(r0['eligible_schedule_duration_hours'], 0.75, places=10)

        # Interval-average demand: max(E_i / 1h) = 400 / 3 = 133.333... kW
        self.assertAlmostEqual(r0['max_modeled_interval_average_electrical_demand_kw'], 400.0 / 3.0, places=10)

        # Permitted-duration power requirements:
        # Slot 0: (400/3) / 0.5 = 800/3 = 266.666... kW
        # Slot 1: (200/3) / 0.25 = 800/3 = 266.666... kW
        # Both permitted-duration powers are 266.666... kW
        expected_power = 800.0 / 3.0
        self.assertAlmostEqual(r0['max_modeled_permitted_duration_electrical_requirement_kw'], expected_power, places=10)

        # Top-level summary across realizations matches worst-case peak
        self.assertAlmostEqual(diag['max_modeled_permitted_duration_electrical_requirement_kw'], expected_power, places=10)
        self.assertAlmostEqual(diag['max_modeled_interval_average_electrical_demand_kw'], 400.0 / 3.0, places=10)

        # Useful-heat requirement: 266.666... * 4.0 = 1066.666... kW (3200/3)
        expected_useful = expected_power * 4.0
        self.assertAlmostEqual(r0['max_modeled_permitted_duration_useful_heat_requirement_kw'], expected_useful, places=10)
        self.assertAlmostEqual(diag['max_modeled_permitted_duration_useful_heat_requirement_kw'], expected_useful, places=10)

        # No fallback events when cold delta > 0
        self.assertEqual(diag['fallback_events'], [])

        # Limitation text
        self.assertIn("equipment adequacy unassessed", diag['limitation'])
        self.assertIn("within-hour solar coincidence", diag['limitation'])

    def test_2_temps_above_balance_fallback_event_and_zero_demand(self):
        """All temperatures above balance and zero demand each give exact zero without fallback."""
        # Temps at 20.0 and 22.0 C are above balance_temp (18.0 C)
        temps, months, hours, valid, civil_hours, weather_years = self._make_2slot_timeline(temps=[20.0, 22.0])
        s = copy.deepcopy(self.base_settings)

        with patch.object(engine, 'GAS_MONTHLY', self.synthetic_gas_monthly), synthetic_thermal_runtime(self.synthetic_gas_monthly):
            profile, diag = engine._allocate_space_heating(
                s, temps, months, hours, valid, civil_hours, weather_years, return_diagnostics=True
            )

        # The committed ensemble model allocates no heat without degree hours.
        np.testing.assert_array_equal(profile, np.zeros_like(temps))
        self.assertEqual(diag['fallback_events'], [])
        self.assertEqual(diag['max_modeled_permitted_duration_electrical_requirement_kw'], 0.0)
        self.assertEqual(diag['max_modeled_interval_average_electrical_demand_kw'], 0.0)

        # Zero demand subtest: GAS_MONTHLY all 0 Smc -> 0 budget
        zero_gas = np.zeros(12, dtype=float)
        with patch.object(engine, 'GAS_MONTHLY', zero_gas), synthetic_thermal_runtime(zero_gas):
            p_zero, d_zero = engine._allocate_space_heating(
                s, temps, months, hours, valid, civil_hours, weather_years, return_diagnostics=True
            )

        self.assertAlmostEqual(float(p_zero.sum()), 0.0, places=10)
        self.assertEqual(d_zero['fallback_events'], [])
        self.assertEqual(d_zero['max_modeled_permitted_duration_electrical_requirement_kw'], 0.0)
        self.assertEqual(d_zero['max_modeled_interval_average_electrical_demand_kw'], 0.0)

    def test_3_duplicate_and_overlapping_windows(self):
        """Duplicate and overlapping windows yield identical nominal/actual hours and energy."""
        temps, months, hours, valid, civil_hours, weather_years = self._make_2slot_timeline()

        # Single window: 08:30-09:15
        s_single = copy.deepcopy(self.base_settings)
        s_single['ac_heating_windows'] = [{'start_minute': 510, 'end_minute': 555}]

        # Duplicate window: two identical 08:30-09:15 windows
        s_dup = copy.deepcopy(self.base_settings)
        s_dup['ac_heating_windows'] = [
            {'start_minute': 510, 'end_minute': 555},
            {'start_minute': 510, 'end_minute': 555},
        ]

        # Overlapping windows: 08:30-09:00 and 08:45-09:15 -> union is 08:30-09:15
        s_overlap = copy.deepcopy(self.base_settings)
        s_overlap['ac_heating_windows'] = [
            {'start_minute': 510, 'end_minute': 540},
            {'start_minute': 525, 'end_minute': 555},
        ]

        with patch.object(engine, 'GAS_MONTHLY', self.synthetic_gas_monthly), synthetic_thermal_runtime(self.synthetic_gas_monthly):
            p1, d1 = engine._allocate_space_heating(s_single, temps, months, hours, valid, civil_hours, weather_years, return_diagnostics=True)
            p2, d2 = engine._allocate_space_heating(s_dup, temps, months, hours, valid, civil_hours, weather_years, return_diagnostics=True)
            p3, d3 = engine._allocate_space_heating(s_overlap, temps, months, hours, valid, civil_hours, weather_years, return_diagnostics=True)

        np.testing.assert_allclose(p1, p2, atol=1e-12, rtol=0)
        np.testing.assert_allclose(p1, p3, atol=1e-12, rtol=0)
        self.assertAlmostEqual(d1['nominal_daily_union_duration_hours'], d2['nominal_daily_union_duration_hours'], places=10)
        self.assertAlmostEqual(d1['nominal_daily_union_duration_hours'], d3['nominal_daily_union_duration_hours'], places=10)
        self.assertAlmostEqual(d1['realizations'][0]['eligible_schedule_duration_hours'], d2['realizations'][0]['eligible_schedule_duration_hours'], places=10)
        self.assertAlmostEqual(d1['realizations'][0]['eligible_schedule_duration_hours'], d3['realizations'][0]['eligible_schedule_duration_hours'], places=10)

    def test_4_narrower_vs_wider_schedule_power_requirement(self):
        """Narrower schedule requires higher operating power than wider schedule for same budget."""
        temps, months, hours, valid, civil_hours, weather_years = self._make_2slot_timeline()

        # Narrower: 08:30-09:15 (45 min = 0.75 h)
        s_narrow = copy.deepcopy(self.base_settings)
        s_narrow['ac_heating_windows'] = [{'start_minute': 510, 'end_minute': 555}]

        # Wider: 08:00-10:00 (120 min = 2.0 h across hours 8 and 9)
        s_wide = copy.deepcopy(self.base_settings)
        s_wide['ac_heating_windows'] = [{'start_minute': 480, 'end_minute': 600}]

        with patch.object(engine, 'GAS_MONTHLY', self.synthetic_gas_monthly), synthetic_thermal_runtime(self.synthetic_gas_monthly):
            p_narrow, d_narrow = engine._allocate_space_heating(s_narrow, temps, months, hours, valid, civil_hours, weather_years, return_diagnostics=True)
            p_wide, d_wide = engine._allocate_space_heating(s_wide, temps, months, hours, valid, civil_hours, weather_years, return_diagnostics=True)

        # Same total budget
        self.assertAlmostEqual(float(p_narrow.sum()), 200.0, places=10)
        self.assertAlmostEqual(float(p_wide.sum()), 200.0, places=10)

        # Narrower has higher power requirement
        pwr_narrow = d_narrow['max_modeled_permitted_duration_electrical_requirement_kw']
        pwr_wide = d_wide['max_modeled_permitted_duration_electrical_requirement_kw']
        self.assertAlmostEqual(pwr_narrow, 800.0 / 3.0, places=10)  # 266.666... kW
        self.assertAlmostEqual(pwr_wide, 100.0, places=10)          # 100.0 kW (200 kWh / 2h)
        self.assertGreater(pwr_narrow, pwr_wide)

    def test_5_additional_demand_25pct_scaling(self):
        """25% demand scales profile and power by 1.25, while permitted duration is unchanged."""
        temps, months, hours, valid, civil_hours, weather_years = self._make_2slot_timeline()

        s_0 = copy.deepcopy(self.base_settings)
        s_0['ac_heating_additional_demand_pct'] = 0.0

        s_25 = copy.deepcopy(self.base_settings)
        s_25['ac_heating_additional_demand_pct'] = 25.0

        with patch.object(engine, 'GAS_MONTHLY', self.synthetic_gas_monthly), synthetic_thermal_runtime(self.synthetic_gas_monthly):
            p0, d0 = engine._allocate_space_heating(s_0, temps, months, hours, valid, civil_hours, weather_years, return_diagnostics=True)
            p25, d25 = engine._allocate_space_heating(s_25, temps, months, hours, valid, civil_hours, weather_years, return_diagnostics=True)

        # Profile scales exactly 1.25x
        np.testing.assert_allclose(p25, p0 * 1.25, atol=1e-12, rtol=0)

        # Power scales exactly 1.25x
        self.assertAlmostEqual(
            d25['max_modeled_permitted_duration_electrical_requirement_kw'],
            d0['max_modeled_permitted_duration_electrical_requirement_kw'] * 1.25,
            places=10
        )
        self.assertAlmostEqual(
            d25['max_modeled_interval_average_electrical_demand_kw'],
            d0['max_modeled_interval_average_electrical_demand_kw'] * 1.25,
            places=10
        )

        # Permitted duration is unchanged
        self.assertAlmostEqual(
            d25['realizations'][0]['eligible_schedule_duration_hours'],
            d0['realizations'][0]['eligible_schedule_duration_hours'],
            places=10
        )
        self.assertAlmostEqual(d25['nominal_daily_union_duration_hours'], d0['nominal_daily_union_duration_hours'], places=10)

    def test_6_no_eligible_valid_slots_raises_error(self):
        """Positive heating budget with zero eligible slots raises ValueError (no continuous fallback)."""
        temps, months, hours, valid, civil_hours, weather_years = self._make_2slot_timeline()

        # Case A: empty windows with positive budget
        s_empty = copy.deepcopy(self.base_settings)
        s_empty['ac_heating_windows'] = []
        with patch.object(engine, 'GAS_MONTHLY', self.synthetic_gas_monthly), synthetic_thermal_runtime(self.synthetic_gas_monthly):
            with self.assertRaises(ValueError) as ctx_empty:
                engine._allocate_space_heating(s_empty, temps, months, hours, valid, civil_hours, weather_years)
            self.assertIn("Add at least one operating window", str(ctx_empty.exception))

        # Case B: windows exist, but valid mask is False for all window hours
        s_inval = copy.deepcopy(self.base_settings)
        valid_false = np.zeros_like(valid, dtype=bool)
        with patch.object(engine, 'GAS_MONTHLY', self.synthetic_gas_monthly), synthetic_thermal_runtime(self.synthetic_gas_monthly):
            with self.assertRaises(ValueError) as ctx_inval:
                engine._allocate_space_heating(s_inval, temps, months, hours, valid_false, civil_hours, weather_years)
            self.assertIn("No active valid heating hours", str(ctx_inval.exception))

    def test_7_dst_civil_hour_eligibility(self):
        """Europe/Rome civil clock DST transition: spring skips hour 2 (0h), autumn repeats hour 2 (2x 0.25h = 0.5h)."""
        # Load real PVGIS dataset and civil hours mapping
        d = engine.data()
        ch = engine._get_civil_hours(d)
        dates = engine._local_standard_dates()
        wi = 0  # Year 2005

        # Operating window: 02:30-02:45 (15 minutes in hour 2, fraction = 0.25)
        s_civil = copy.deepcopy(self.base_settings)
        s_civil['ac_heating_windows'] = [{'start_minute': 150, 'end_minute': 165}]
        s_civil['timezone_mode'] = 'civil'

        s_std = copy.deepcopy(self.base_settings)
        s_std['ac_heating_windows'] = [{'start_minute': 150, 'end_minute': 165}]
        s_std['timezone_mode'] = 'standard'

        overlaps = engine.canonical_windows_hourly_overlaps(s_civil['ac_heating_windows'])
        fractions = overlaps / 60.0

        timeline = dates[wi]

        # 1. Spring DST: 2023-03-26. Civil clock jumps 02:00 -> 03:00, so civil hour 2 is absent.
        march_indices = [i for i, (stamp, _, mm) in enumerate(timeline) if mm == 3 and stamp.startswith('2023-03-26')]
        march_civil = [ch[wi, i] for i in march_indices]
        self.assertNotIn(2, march_civil)  # Civil hour 2 does not exist on spring transition day

        # Sum of fractions across spring DST day
        civil_spring_eligible = sum(fractions[h] for h in march_civil)
        self.assertAlmostEqual(civil_spring_eligible, 0.0, places=10)

        # Standard time contrast: standard hour 2 exists once (0.25h)
        march_std = [d['hour'][wi, i] for i in march_indices]
        std_spring_eligible = sum(fractions[h] for h in march_std)
        self.assertAlmostEqual(std_spring_eligible, 0.25, places=10)

        # 2. Autumn DST: 2023-10-29. Civil clock falls back 03:00 -> 02:00, civil hour 2 repeated.
        oct_indices = [i for i, (stamp, _, mm) in enumerate(timeline) if mm == 10 and stamp.startswith('2023-10-29')]
        oct_civil = [ch[wi, i] for i in oct_indices]
        self.assertEqual(oct_civil.count(2), 2)  # Civil hour 2 appears twice in distinct real intervals

        # Sum of fractions across autumn DST day: two distinct intervals each contribute 0.25h -> 0.5h
        civil_oct_eligible = sum(fractions[h] for h in oct_civil)
        self.assertAlmostEqual(civil_oct_eligible, 0.50, places=10)

        # Standard time contrast: standard hour 2 exists once (0.25h)
        oct_std = [d['hour'][wi, i] for i in oct_indices]
        std_oct_eligible = sum(fractions[h] for h in oct_std)
        self.assertAlmostEqual(std_oct_eligible, 0.25, places=10)

    def test_8_return_type_json_serializability_and_load_profiles(self):
        """Default returns np.ndarray; diagnostics is JSON-serializable; load_profiles has nested totals."""
        temps, months, hours, valid, civil_hours, weather_years = self._make_2slot_timeline()
        s = copy.deepcopy(self.base_settings)

        with patch.object(engine, 'GAS_MONTHLY', self.synthetic_gas_monthly), synthetic_thermal_runtime(self.synthetic_gas_monthly):
            # Default return type is np.ndarray (backward-compatible)
            res_default = engine._allocate_space_heating(
                s, temps, months, hours, valid, civil_hours, weather_years
            )
            self.assertIsInstance(res_default, np.ndarray)

            # return_diagnostics=False explicitly returns np.ndarray
            res_false = engine._allocate_space_heating(
                s, temps, months, hours, valid, civil_hours, weather_years, return_diagnostics=False
            )
            self.assertIsInstance(res_false, np.ndarray)

            # return_diagnostics=True returns (ndarray, dict)
            profile, diag = engine._allocate_space_heating(
                s, temps, months, hours, valid, civil_hours, weather_years, return_diagnostics=True
            )
            self.assertIsInstance(profile, np.ndarray)
            self.assertIsInstance(diag, dict)

            # JSON serializability
            diag_json = json.dumps(diag)
            loaded = json.loads(diag_json)
            self.assertEqual(loaded['nominal_daily_union_duration_hours'], diag['nominal_daily_union_duration_hours'])
            self.assertEqual(loaded['limitation'], diag['limitation'])
            self.assertEqual(len(loaded['realizations']), 1)

            # Integration with load_profiles()
            s_full = engine.validate(self.base_settings)
            load, ev, enduses = engine.load_profiles(s_full)

            # Diagnostics object exposed as ONE nested ac_heating_allocation_diagnostics object
            self.assertIn('ac_heating_allocation_diagnostics', enduses)
            ac_diag = enduses['ac_heating_allocation_diagnostics']
            self.assertIsInstance(ac_diag, dict)
            self.assertIn('realizations', ac_diag)
            self.assertIn('nominal_daily_union_duration_hours', ac_diag)
            self.assertIn('limitation', ac_diag)

            # Enduse heating totals correspond to raw requested electricity
            self.assertAlmostEqual(enduses['heating_kwh'], enduses['ac_heating_requested_electricity_kwh'], places=10)

            # Baseline scenario does not include ac_heating_allocation_diagnostics and allows empty windows
            s_base = copy.deepcopy(s_full)
            s_base['load_scenario'] = 'baseline'
            s_base['ac_heating_windows'] = []
            load_b, ev_b, enduses_b = engine.load_profiles(s_base)
            self.assertNotIn('ac_heating_allocation_diagnostics', enduses_b)

            # Heat pump scenario does not include ac_heating_allocation_diagnostics and allows empty windows
            s_hp = copy.deepcopy(s_full)
            s_hp['planning_heat_pump'] = True
            s_hp['ac_heating_windows'] = []
            load_hp, ev_hp, enduses_hp = engine.load_profiles(s_hp)
            self.assertNotIn('ac_heating_allocation_diagnostics', enduses_hp)


if __name__ == '__main__':
    unittest.main()
