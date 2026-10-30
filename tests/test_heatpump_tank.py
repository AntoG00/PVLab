"""Unit tests for Domestic Hot Water (DHW) storage tank component and demand helpers.

Covers:
1. Analytic no action and standing loss (including no ambient gain policy).
2. Draw and reheat exact energy balance under discrete substeps.
3. Inlet temperature change without phantom heat (absolute sensible energy C * T).
4. Empty/cold tank shortfall vs hot tank, and lukewarm draw service rejection (<40°C).
5. Thermostat deadband latching and hysteresis lifecycle.
6. Heat acceptance bounding, hygiene target (60°C), and rejection reporting.
7. Gas-mode exact monthly gross * (1 - prior_loss_fraction) conservation (1D and 2D).
8. Litre-mode exact C * dT daily and monthly calculation.
9. Morning/evening timing windows, equal-hour disables, and overnight wrap.
10. Validation and rejection of non-physical or invalid parameters.
"""

from __future__ import annotations

import math
import unittest

import numpy as np

from feasibility.heatpump_tank import (
    DAYS_IN_MONTH,
    DEFAULT_AMBIENT_TEMP_C,
    DEFAULT_DEADBAND_K,
    DEFAULT_HYGIENE_TEMP_C,
    DEFAULT_INLET_TEMP_C,
    DEFAULT_MIN_SERVICE_TEMP_C,
    DEFAULT_STANDING_LOSS_W_PER_K,
    DEFAULT_TARGET_TEMP_C,
    DEFAULT_VOLUME_L,
    DHWTank,
    DHWTankStepResult,
    WATER_HEAT_CAPACITY_KWH_PER_L_K,
    build_dhw_timing_weights,
    build_hourly_dhw_demand,
    calculate_daily_dhw_heat_from_litres,
    window_hours,
)


class TestDHWTankNoActionAndLoss(unittest.TestCase):
    """Analytic tests for idle state, zero duration, standing losses, and no ambient gain."""

    def test_no_action_preserves_state_exactly(self) -> None:
        tank = DHWTank(volume_litres=200.0, target_temp_c=50.0, ambient_temp_c=50.0)
        res = tank.step(duration_hours=1.0, draw_demand_kwh=0.0, heat_supplied_kwh=0.0)
        self.assertEqual(res.start_temp_c, 50.0)
        self.assertEqual(res.end_temp_c, 50.0)
        self.assertEqual(res.start_storage_kwh, res.end_storage_kwh)
        self.assertEqual(res.actual_draw_heat_kwh, 0.0)
        self.assertEqual(res.standing_loss_kwh, 0.0)
        self.assertEqual(res.accepted_heat_kwh, 0.0)
        self.assertEqual(res.rejected_heat_kwh, 0.0)
        # Exact balance
        delta_e = res.end_storage_kwh - res.start_storage_kwh
        net_flows = res.accepted_heat_kwh - res.actual_draw_heat_kwh - res.standing_loss_kwh
        self.assertAlmostEqual(delta_e, net_flows, places=12)

    def test_zero_duration_preserves_state(self) -> None:
        tank = DHWTank(volume_litres=200.0, target_temp_c=50.0, ambient_temp_c=20.0)
        res = tank.step(duration_hours=0.0, draw_demand_kwh=2.0, heat_supplied_kwh=3.0)
        self.assertEqual(res.start_temp_c, 50.0)
        self.assertEqual(res.end_temp_c, 50.0)
        self.assertEqual(res.actual_draw_heat_kwh, 0.0)
        self.assertEqual(res.standing_loss_kwh, 0.0)
        self.assertEqual(res.unmet_service_heat_kwh, 2.0)
        self.assertEqual(res.accepted_heat_kwh, 0.0)
        self.assertEqual(res.rejected_heat_kwh, 3.0)

    def test_standing_loss_analytic_and_balance(self) -> None:
        # 200 L, U = 1.0 W/K, initial = 50°C, ambient = 20°C
        # C = 200 * 0.001163 = 0.2326 kWh/K
        # Initial delta T = 30 K -> loss rate = 30 W = 0.03 kW
        tank = DHWTank(
            volume_litres=200.0, target_temp_c=50.0, ambient_temp_c=20.0,
            standing_loss_w_per_k=1.0, initial_temp_c=50.0,
        )
        res = tank.step(duration_hours=1.0, draw_demand_kwh=0.0, heat_supplied_kwh=0.0, substeps=4)
        # Standing loss should be slightly below 0.03 kWh due to cooling across the hour
        self.assertTrue(0.028 < res.standing_loss_kwh <= 0.030)
        self.assertAlmostEqual(res.end_temp_c, 50.0 - (res.standing_loss_kwh / tank.heat_capacity_kwh_per_k), places=10)
        delta_e = res.end_storage_kwh - res.start_storage_kwh
        self.assertAlmostEqual(delta_e, -res.standing_loss_kwh, places=12)

    def test_losses_no_ambient_gain_policy(self) -> None:
        # Tank at 15°C, ambient at 25°C. Tank must NOT heat up from ambient!
        tank = DHWTank(
            volume_litres=200.0, target_temp_c=50.0, ambient_temp_c=25.0,
            standing_loss_w_per_k=1.0, initial_temp_c=15.0,
        )
        res = tank.step(duration_hours=5.0, draw_demand_kwh=0.0, heat_supplied_kwh=0.0)
        self.assertEqual(res.standing_loss_kwh, 0.0)
        self.assertEqual(res.end_temp_c, 15.0)
        self.assertEqual(res.end_storage_kwh, res.start_storage_kwh)


class TestDHWTankDrawAndReheatBalance(unittest.TestCase):
    """Draw and reheat exact energy conservation tests."""

    def test_draw_only_exact_balance(self) -> None:
        tank = DHWTank(
            volume_litres=200.0, target_temp_c=50.0, ambient_temp_c=50.0,
            standing_loss_w_per_k=1.0, inlet_temp_c=15.0, initial_temp_c=50.0,
        )
        res = tank.step(duration_hours=1.0, draw_demand_kwh=1.5, heat_supplied_kwh=0.0)
        self.assertEqual(res.requested_draw_heat_kwh, 1.5)
        self.assertEqual(res.actual_draw_heat_kwh, 1.5)
        self.assertEqual(res.served_service_heat_kwh, 1.5)
        self.assertEqual(res.unmet_service_heat_kwh, 0.0)
        self.assertEqual(res.standing_loss_kwh, 0.0)  # ambient == 50, but temp dropped below 50 so no gain either
        delta_e = res.end_storage_kwh - res.start_storage_kwh
        self.assertAlmostEqual(delta_e, -1.5, places=12)

    def test_reheat_only_exact_balance(self) -> None:
        tank = DHWTank(
            volume_litres=200.0, target_temp_c=50.0, ambient_temp_c=45.0,
            initial_temp_c=45.0,
        )
        res = tank.step(duration_hours=1.0, draw_demand_kwh=0.0, heat_supplied_kwh=0.8, ambient_temp_c=45.0)
        # Capacity to target: C * (50 - 45) = 0.2326 * 5 = 1.163 kWh > 0.8 kWh
        self.assertEqual(res.accepted_heat_kwh, 0.8)
        self.assertEqual(res.rejected_heat_kwh, 0.0)
        delta_e = res.end_storage_kwh - res.start_storage_kwh
        net_flows = res.accepted_heat_kwh - res.actual_draw_heat_kwh - res.standing_loss_kwh
        self.assertAlmostEqual(delta_e, net_flows, places=12)

    def test_simultaneous_draw_loss_reheat_substeps_exact_balance(self) -> None:
        tank = DHWTank(
            volume_litres=250.0, target_temp_c=55.0, ambient_temp_c=18.0,
            standing_loss_w_per_k=1.2, inlet_temp_c=12.0, initial_temp_c=52.0,
        )
        for substeps_count in (1, 4, 10):
            res = tank.step(
                duration_hours=1.0, draw_demand_kwh=2.1, heat_supplied_kwh=2.8,
                substeps=substeps_count,
            )
            delta_e = res.end_storage_kwh - res.start_storage_kwh
            net_flows = res.accepted_heat_kwh - res.actual_draw_heat_kwh - res.standing_loss_kwh
            self.assertAlmostEqual(delta_e, net_flows, places=12)


class TestInletChangeNoPhantomHeat(unittest.TestCase):
    """Verifies absolute stored sensible energy (C * T) produces no phantom heat on inlet shifts."""

    def test_inlet_change_no_phantom_heat(self) -> None:
        tank = DHWTank(volume_litres=200.0, target_temp_c=50.0, inlet_temp_c=15.0, ambient_temp_c=50.0)
        expected_storage = 200.0 * WATER_HEAT_CAPACITY_KWH_PER_L_K * 50.0

        # Step 1 with standard inlet (15°C)
        res1 = tank.step(duration_hours=1.0, draw_demand_kwh=0.0, heat_supplied_kwh=0.0, inlet_temp_c=15.0)
        self.assertEqual(res1.start_storage_kwh, expected_storage)
        self.assertEqual(res1.end_storage_kwh, expected_storage)

        # Step 2: Inlet drops to 5°C (winter groundwater shift)
        res2 = tank.step(duration_hours=1.0, draw_demand_kwh=0.0, heat_supplied_kwh=0.0, inlet_temp_c=5.0)
        self.assertEqual(res2.start_storage_kwh, expected_storage)
        self.assertEqual(res2.end_storage_kwh, expected_storage)
        self.assertEqual(res2.end_temp_c, 50.0)

        # Step 3: Inlet jumps to 22°C (summer warm supply)
        res3 = tank.step(duration_hours=1.0, draw_demand_kwh=0.0, heat_supplied_kwh=0.0, inlet_temp_c=22.0)
        self.assertEqual(res3.start_storage_kwh, expected_storage)
        self.assertEqual(res3.end_storage_kwh, expected_storage)
        self.assertEqual(res3.end_temp_c, 50.0)


class TestEmptyColdShortfallVsHotTank(unittest.TestCase):
    """Draw capability for cold vs hot tanks, and lukewarm draw service rejection."""

    def test_cold_tank_at_inlet_shortfall(self) -> None:
        tank = DHWTank(
            volume_litres=200.0, target_temp_c=50.0, inlet_temp_c=15.0,
            min_service_temp_c=40.0, initial_temp_c=15.0, ambient_temp_c=15.0,
        )
        res = tank.step(duration_hours=1.0, draw_demand_kwh=2.5, heat_supplied_kwh=0.0)
        self.assertEqual(res.requested_draw_heat_kwh, 2.5)
        self.assertEqual(res.actual_draw_heat_kwh, 0.0)
        self.assertEqual(res.served_service_heat_kwh, 0.0)
        self.assertEqual(res.unmet_service_heat_kwh, 2.5)
        self.assertEqual(res.end_temp_c, 15.0)

    def test_hot_tank_serves_service(self) -> None:
        tank = DHWTank(
            volume_litres=200.0, target_temp_c=50.0, inlet_temp_c=15.0,
            min_service_temp_c=40.0, initial_temp_c=50.0, ambient_temp_c=50.0,
        )
        res = tank.step(duration_hours=1.0, draw_demand_kwh=2.0, heat_supplied_kwh=0.0)
        self.assertEqual(res.requested_draw_heat_kwh, 2.0)
        self.assertEqual(res.actual_draw_heat_kwh, 2.0)
        self.assertEqual(res.served_service_heat_kwh, 2.0)
        self.assertEqual(res.unmet_service_heat_kwh, 0.0)

    def test_lukewarm_draw_never_meets_service(self) -> None:
        # Tank at 35°C (lukewarm), inlet at 15°C, min service at 40°C
        tank = DHWTank(
            volume_litres=200.0, target_temp_c=50.0, inlet_temp_c=15.0,
            min_service_temp_c=40.0, initial_temp_c=35.0, ambient_temp_c=35.0,
        )
        res = tank.step(duration_hours=1.0, draw_demand_kwh=1.2, heat_supplied_kwh=0.0)
        # Heat was drawn from storage down toward inlet
        self.assertEqual(res.actual_draw_heat_kwh, 1.2)
        # BUT lukewarm draw NEVER qualifies as served service!
        self.assertEqual(res.served_service_heat_kwh, 0.0)
        self.assertEqual(res.unmet_service_heat_kwh, 1.2)
        self.assertLess(res.end_temp_c, 35.0)

    def test_draw_bounded_no_colder_than_inlet(self) -> None:
        # Tank at 20°C, inlet at 15°C. Total sensible heat above inlet = 200 * 0.001163 * 5 = 1.163 kWh
        tank = DHWTank(
            volume_litres=200.0, target_temp_c=50.0, inlet_temp_c=15.0,
            initial_temp_c=20.0, ambient_temp_c=20.0,
        )
        res = tank.step(duration_hours=1.0, draw_demand_kwh=10.0, heat_supplied_kwh=0.0)
        max_draw = 200.0 * WATER_HEAT_CAPACITY_KWH_PER_L_K * 5.0
        self.assertAlmostEqual(res.actual_draw_heat_kwh, max_draw, places=10)
        self.assertAlmostEqual(res.end_temp_c, 15.0, places=10)
        self.assertEqual(res.served_service_heat_kwh, 0.0)
        self.assertAlmostEqual(res.unmet_service_heat_kwh, 10.0, places=10)


class TestThermostatDeadbandLatch(unittest.TestCase):
    """Thermostat hysteresis and deadband latch lifecycle."""

    def test_deadband_latch_lifecycle(self) -> None:
        # Target 50°C, deadband 5K -> lower setpoint 45°C
        tank = DHWTank(
            volume_litres=200.0, target_temp_c=50.0, deadband_k=5.0,
            initial_temp_c=50.0, standing_loss_w_per_k=0.0,
        )
        self.assertFalse(tank.thermostat_latch)

        # 1. Cool to 47°C (draw enough to drop by 3°C: C * 3 = 0.2326 * 3 = 0.6978 kWh)
        c = tank.heat_capacity_kwh_per_k
        tank.step(duration_hours=1.0, draw_demand_kwh=c * 3.0)
        self.assertAlmostEqual(tank.temp_c, 47.0, places=5)
        self.assertFalse(tank.thermostat_latch)  # Above 45°C, latch stays False

        # 2. Cool to 44°C (drop by another 3°C)
        tank.step(duration_hours=1.0, draw_demand_kwh=c * 3.0)
        self.assertAlmostEqual(tank.temp_c, 44.0, places=5)
        self.assertTrue(tank.thermostat_latch)   # <= 45°C, latch flips to True

        # 3. Reheat partially to 48°C (add C * 4 = 0.9304 kWh)
        tank.step(duration_hours=1.0, heat_supplied_kwh=c * 4.0)
        self.assertAlmostEqual(tank.temp_c, 48.0, places=5)
        self.assertTrue(tank.thermostat_latch)   # < 50°C, latch remains True (hysteresis!)

        # 4. Reheat to 50°C (add C * 2 = 0.4652 kWh)
        tank.step(duration_hours=1.0, heat_supplied_kwh=c * 2.0)
        self.assertAlmostEqual(tank.temp_c, 50.0, places=5)
        self.assertFalse(tank.thermostat_latch)  # >= 50°C, latch flips to False

        # 5. Cool slightly to 49°C
        tank.step(duration_hours=1.0, draw_demand_kwh=c * 1.0)
        self.assertAlmostEqual(tank.temp_c, 49.0, places=5)
        self.assertFalse(tank.thermostat_latch)  # > 45°C, latch remains False

    def test_remaining_heat_required_exposed(self) -> None:
        tank = DHWTank(volume_litres=200.0, target_temp_c=50.0, initial_temp_c=44.0)
        c = tank.heat_capacity_kwh_per_k
        expected_rem = c * (50.0 - 44.0)
        self.assertAlmostEqual(tank.remaining_heat_required(), expected_rem, places=10)
        res = tank.step(duration_hours=0.0)
        self.assertAlmostEqual(res.remaining_heat_kwh, expected_rem, places=10)


class TestHeatAcceptanceAndHygieneTarget(unittest.TestCase):
    """Heat acceptance bounding, excess rejection, and hygiene target (60°C)."""

    def test_reheat_bounded_to_target_and_excess_rejected(self) -> None:
        tank = DHWTank(
            volume_litres=200.0, target_temp_c=50.0, initial_temp_c=48.0,
            standing_loss_w_per_k=0.0,
        )
        c = tank.heat_capacity_kwh_per_k
        room_to_target = c * 2.0  # 0.4652 kWh
        res = tank.step(duration_hours=1.0, heat_supplied_kwh=1.0)
        self.assertAlmostEqual(res.accepted_heat_kwh, room_to_target, places=10)
        self.assertAlmostEqual(res.rejected_heat_kwh, 1.0 - room_to_target, places=10)
        self.assertAlmostEqual(res.end_temp_c, 50.0, places=10)

    def test_hygiene_target_acceptance(self) -> None:
        tank = DHWTank(
            volume_litres=200.0, target_temp_c=50.0, hygiene_temp_c=60.0,
            initial_temp_c=50.0, standing_loss_w_per_k=0.0,
        )
        c = tank.heat_capacity_kwh_per_k
        # Standard step with target=50°C accepts 0 kWh
        res_std = tank.step(duration_hours=1.0, heat_supplied_kwh=2.0)
        self.assertEqual(res_std.accepted_heat_kwh, 0.0)
        self.assertEqual(res_std.rejected_heat_kwh, 2.0)

        # Step with hygiene target=60°C accepts heat up to 60°C
        room_to_hygiene = c * (60.0 - 50.0)  # 2.326 kWh
        res_hyg = tank.step(duration_hours=1.0, heat_supplied_kwh=3.0, target_temp_c=60.0)
        self.assertAlmostEqual(res_hyg.accepted_heat_kwh, room_to_hygiene, places=10)
        self.assertAlmostEqual(res_hyg.rejected_heat_kwh, 3.0 - room_to_hygiene, places=10)
        self.assertAlmostEqual(res_hyg.end_temp_c, 60.0, places=10)
        self.assertFalse(res_hyg.thermostat_latch)


class TestDHWDemandGasMode(unittest.TestCase):
    """Gas-calibrated DHW gross monthly useful heat to hourly demand."""

    def setUp(self) -> None:
        self.monthly_gross = [110.0, 95.0, 85.0, 70.0, 55.0, 45.0, 40.0, 40.0, 50.0, 65.0, 80.0, 105.0]
        # Build standard 8760-hour 1D calendar coordinates
        months = []
        hours = []
        for m_idx, days in enumerate(DAYS_IN_MONTH):
            m_num = m_idx + 1
            for _ in range(days):
                for h in range(24):
                    months.append(m_num)
                    hours.append(h)
        self.months_1d = np.array(months, dtype=int)
        self.hours_1d = np.array(hours, dtype=int)
        self.valid_1d = np.ones(len(months), dtype=bool)

    def test_gas_mode_exact_monthly_minus_prior_loss_1d(self) -> None:
        prior_loss = 0.15
        demand = build_hourly_dhw_demand(
            valid=self.valid_1d, months=self.months_1d, hours=self.hours_1d,
            monthly_dhw_kwh=self.monthly_gross, prior_loss_fraction=prior_loss,
            morning_window=(7, 9), evening_window=(18, 21),
            morning_fraction=0.4, evening_fraction=0.6,
        )
        self.assertEqual(demand.shape, (8760,))
        for m in range(1, 13):
            mask = self.months_1d == m
            expected_net = self.monthly_gross[m - 1] * (1.0 - prior_loss)
            self.assertAlmostEqual(float(np.sum(demand[mask])), expected_net, places=10)

    def test_gas_mode_exact_monthly_2d_ensemble(self) -> None:
        n_years = 3
        valid_2d = np.ones((n_years, 8760), dtype=bool)
        months_2d = np.tile(self.months_1d, (n_years, 1))
        hours_2d = np.tile(self.hours_1d, (n_years, 1))

        prior_loss = 0.20
        demand_2d = build_hourly_dhw_demand(
            valid=valid_2d, months=months_2d, hours=hours_2d,
            monthly_dhw_kwh=self.monthly_gross, prior_loss_fraction=prior_loss,
        )
        self.assertEqual(demand_2d.shape, (n_years, 8760))
        for y in range(n_years):
            for m in range(1, 13):
                mask = months_2d[y] == m
                expected_net = self.monthly_gross[m - 1] * (1.0 - prior_loss)
                self.assertAlmostEqual(float(np.sum(demand_2d[y, mask])), expected_net, places=10)

    def test_default_prior_loss_is_zero(self) -> None:
        demand = build_hourly_dhw_demand(
            valid=self.valid_1d, months=self.months_1d, hours=self.hours_1d,
            monthly_dhw_kwh=self.monthly_gross,
        )
        for m in range(1, 13):
            mask = self.months_1d == m
            self.assertAlmostEqual(float(np.sum(demand[mask])), self.monthly_gross[m - 1], places=10)


class TestDHWDemandLitreMode(unittest.TestCase):
    """Litre-mode DHW useful demand using C * dT."""

    def test_litre_mode_c_dt_analytic(self) -> None:
        # 150 L/day, service 40°C, inlet 15°C -> dT = 25 K
        # Q = 150 * 0.001163 * 25 = 4.36125 kWh/day
        daily_kwh = calculate_daily_dhw_heat_from_litres(
            litres_per_day=150.0, service_temp_c=40.0, inlet_temp_c=15.0,
        )
        expected = 150.0 * WATER_HEAT_CAPACITY_KWH_PER_L_K * 25.0
        self.assertAlmostEqual(daily_kwh, expected, places=12)

    def test_litre_mode_hourly_and_monthly_budgets(self) -> None:
        # Build 1D calendar coordinates
        months, hours = [], []
        for m_idx, days in enumerate(DAYS_IN_MONTH):
            for _ in range(days):
                for h in range(24):
                    months.append(m_idx + 1)
                    hours.append(h)
        months_arr = np.array(months, dtype=int)
        hours_arr = np.array(hours, dtype=int)
        valid_arr = np.ones(len(months), dtype=bool)

        demand = build_hourly_dhw_demand(
            valid=valid_arr, months=months_arr, hours=hours_arr,
            litres_per_day=100.0, service_temp_c=40.0, inlet_temp_c=15.0,
        )
        daily_kwh = 100.0 * WATER_HEAT_CAPACITY_KWH_PER_L_K * 25.0
        for m in range(1, 13):
            mask = months_arr == m
            expected_month = daily_kwh * DAYS_IN_MONTH[m - 1]
            self.assertAlmostEqual(float(np.sum(demand[mask])), expected_month, places=10)


class TestDHWDemandTimingAndWindows(unittest.TestCase):
    """Timing fractions, equal-hour disables, and overnight wrap."""

    def test_morning_evening_split(self) -> None:
        weights = build_dhw_timing_weights(
            morning_window=(7, 9), evening_window=(18, 22),
            morning_fraction=0.4, evening_fraction=0.6,
        )
        self.assertAlmostEqual(float(np.sum(weights)), 1.0, places=12)
        # Morning hours: 7, 8 (2 hours) -> 0.4 total -> 0.2 each
        self.assertAlmostEqual(weights[7], 0.2, places=12)
        self.assertAlmostEqual(weights[8], 0.2, places=12)
        # Evening hours: 18, 19, 20, 21 (4 hours) -> 0.6 total -> 0.15 each
        for h in (18, 19, 20, 21):
            self.assertAlmostEqual(weights[h], 0.15, places=12)
        # All other hours must be 0
        for h in range(24):
            if h not in (7, 8, 18, 19, 20, 21):
                self.assertEqual(weights[h], 0.0)

    def test_equal_hours_disables_window(self) -> None:
        # Morning window (8, 8) has on == off -> disabled
        weights = build_dhw_timing_weights(
            morning_window=(8, 8), evening_window=(18, 21),
            morning_fraction=0.5, evening_fraction=0.5,
        )
        self.assertAlmostEqual(float(np.sum(weights)), 1.0, places=12)
        # All demand goes to evening window (18, 19, 20: 3 hours)
        for h in (18, 19, 20):
            self.assertAlmostEqual(weights[h], 1.0 / 3.0, places=12)
        self.assertEqual(weights[8], 0.0)

    def test_overnight_wrap_window(self) -> None:
        # Window (22, 2) wraps overnight: hours 22, 23, 0, 1
        hours_set = window_hours(22, 2)
        self.assertEqual(hours_set, {22, 23, 0, 1})

        weights = build_dhw_timing_weights(
            morning_window=(8, 8), evening_window=(22, 2),
        )
        for h in (22, 23, 0, 1):
            self.assertAlmostEqual(weights[h], 0.25, places=12)


class TestInvalidInputsAndValidation(unittest.TestCase):
    """Validation and rejection of non-physical parameters."""

    def test_tank_volume_validation(self) -> None:
        with self.assertRaises(ValueError):
            DHWTank(volume_litres=0.0)
        with self.assertRaises(ValueError):
            DHWTank(volume_litres=-50.0)
        with self.assertRaises(ValueError):
            DHWTank(volume_litres=float("nan"))
        with self.assertRaises(TypeError):
            DHWTank(volume_litres=True)

    def test_temperature_order_validation(self) -> None:
        # min_service_temp_c > target_temp_c
        with self.assertRaises(ValueError):
            DHWTank(target_temp_c=45.0, min_service_temp_c=50.0)
        # inlet_temp_c > min_service_temp_c
        with self.assertRaises(ValueError):
            DHWTank(min_service_temp_c=40.0, inlet_temp_c=45.0)
        # hygiene_temp_c < target_temp_c
        with self.assertRaises(ValueError):
            DHWTank(target_temp_c=55.0, hygiene_temp_c=50.0)

    def test_step_input_validation(self) -> None:
        tank = DHWTank()
        with self.assertRaises(ValueError):
            tank.step(duration_hours=-1.0)
        with self.assertRaises(ValueError):
            tank.step(draw_demand_kwh=-0.5)
        with self.assertRaises(ValueError):
            tank.step(heat_supplied_kwh=-1.0)
        with self.assertRaises(ValueError):
            tank.step(substeps=0)
        with self.assertRaises(TypeError):
            tank.step(substeps=True)

    def test_window_hours_validation(self) -> None:
        with self.assertRaises(ValueError):
            window_hours(-1, 8)
        with self.assertRaises(ValueError):
            window_hours(8, 25)
        with self.assertRaises(TypeError):
            window_hours(True, 8)

    def test_timing_weights_all_disabled(self) -> None:
        with self.assertRaises(ValueError):
            build_dhw_timing_weights(morning_window=(8, 8), evening_window=(18, 18))

    def test_dhw_demand_mutually_exclusive(self) -> None:
        valid = np.ones(24, dtype=bool)
        months = np.ones(24, dtype=int)
        hours = np.arange(24, dtype=int)
        # Both provided
        with self.assertRaises(ValueError):
            build_hourly_dhw_demand(
                valid=valid, months=months, hours=hours,
                monthly_dhw_kwh=[50.0] * 12, litres_per_day=100.0,
            )
        # Neither provided
        with self.assertRaises(ValueError):
            build_hourly_dhw_demand(
                valid=valid, months=months, hours=hours,
            )

    def test_prior_loss_bounds(self) -> None:
        with self.assertRaises(ValueError):
            build_hourly_dhw_demand(
                valid=np.ones(24, dtype=bool), months=np.ones(24, dtype=int),
                hours=np.arange(24, dtype=int), monthly_dhw_kwh=[50.0] * 12,
                prior_loss_fraction=1.5,
            )
        with self.assertRaises(ValueError):
            build_hourly_dhw_demand(
                valid=np.ones(24, dtype=bool), months=np.ones(24, dtype=int),
                hours=np.arange(24, dtype=int), monthly_dhw_kwh=[50.0] * 12,
                prior_loss_fraction=-0.1,
            )


class TestDHWDemandProfileCorrectionsAndRegressions(unittest.TestCase):
    """Independent regression tests covering profile corrections and edge cases."""

    def test_litre_mode_29_day_february_heat_exact(self) -> None:
        # 29-day leap February: 29 * 24 = 696 hours
        # 100 L/day, service 40°C, inlet 15°C -> dT = 25 K
        # daily = 100 * 0.001163 * 25 = 2.9075 kWh
        # February total = 29 * 2.9075 = 84.3175 kWh (repro previously gave 81.41)
        valid = np.ones(29 * 24, dtype=bool)
        months = np.full(29 * 24, 2, dtype=int)
        hours = np.tile(np.arange(24), 29)

        demand = build_hourly_dhw_demand(
            valid=valid, months=months, hours=hours,
            litres_per_day=100.0, service_temp_c=40.0, inlet_temp_c=15.0,
        )
        self.assertEqual(demand.shape, (696,))
        self.assertAlmostEqual(float(np.sum(demand)), 84.3175, places=10)

    def test_litre_mode_31_day_month_heat_exact(self) -> None:
        # 31-day month (e.g. July): 31 * 24 = 744 hours
        # 100 L/day, service 40°C, inlet 15°C
        # month total = 31 * 2.9075 = 90.1325 kWh
        valid = np.ones(31 * 24, dtype=bool)
        months = np.full(31 * 24, 7, dtype=int)
        hours = np.tile(np.arange(24), 31)

        demand = build_hourly_dhw_demand(
            valid=valid, months=months, hours=hours,
            litres_per_day=100.0, service_temp_c=40.0, inlet_temp_c=15.0,
        )
        self.assertEqual(demand.shape, (744,))
        self.assertAlmostEqual(float(np.sum(demand)), 90.1325, places=10)

    def test_dst_actual_valid_slots_and_gas_budget_preservation(self) -> None:
        # DST spring-forward: 31 days with 1 hour skipped (e.g. 743 valid slots in March)
        daily_kwh = calculate_daily_dhw_heat_from_litres(100.0, service_temp_c=40.0, inlet_temp_c=15.0)
        valid = np.ones(743, dtype=bool)
        months = np.full(743, 3, dtype=int)
        # 30 days of 24h + 1 day of 23h (hour 2 skipped)
        regular_day = np.arange(24)
        dst_day = np.array([0, 1] + list(range(3, 24)))  # 23 hours
        hours = np.concatenate([np.tile(regular_day, 10), dst_day, np.tile(regular_day, 20)])

        # 1. Litre mode scales by actual valid intervals / 24
        demand_litre = build_hourly_dhw_demand(
            valid=valid, months=months, hours=hours,
            litres_per_day=100.0, service_temp_c=40.0, inlet_temp_c=15.0,
        )
        expected_litre = daily_kwh * (743.0 / 24.0)
        self.assertAlmostEqual(float(np.sum(demand_litre)), expected_litre, places=10)

        # 2. Gas mode preserves exact gas-derived monthly budget independent of DST / slot count
        monthly_kwh = [80.0] * 12
        demand_gas = build_hourly_dhw_demand(
            valid=valid, months=months, hours=hours,
            monthly_dhw_kwh=monthly_kwh, prior_loss_fraction=0.15,
        )
        expected_gas = 80.0 * (1.0 - 0.15)
        self.assertAlmostEqual(float(np.sum(demand_gas)), expected_gas, places=10)

    def test_calendar_1d_broadcasting_to_2d_valid(self) -> None:
        # 1D calendar coordinates (24,) broadcast across 2D valid (2, 24)
        months_1d = np.full(24, 6, dtype=int)
        hours_1d = np.arange(24, dtype=int)

        # Year 0: all 24 slots valid; Year 1: first 12 slots valid, last 12 False
        valid_2d = np.zeros((2, 24), dtype=bool)
        valid_2d[0, :] = True
        valid_2d[1, :12] = True

        demand_2d = build_hourly_dhw_demand(
            valid=valid_2d, months=months_1d, hours=hours_1d,
            litres_per_day=100.0, service_temp_c=40.0, inlet_temp_c=15.0,
            morning_window=(7, 9), evening_window=(18, 21),
            morning_fraction=1.0, evening_fraction=0.0,  # All draw in hours 7, 8 (within first 12h)
        )
        self.assertEqual(demand_2d.shape, (2, 24))
        daily_kwh = 100.0 * WATER_HEAT_CAPACITY_KWH_PER_L_K * 25.0
        # Year 0: 24/24 days = 1.0 day of heat
        self.assertAlmostEqual(float(np.sum(demand_2d[0])), daily_kwh, places=10)
        # Year 1: 12/24 days = 0.5 days of heat
        self.assertAlmostEqual(float(np.sum(demand_2d[1])), daily_kwh * 0.5, places=10)

    def test_prior_gas_loss_not_applied_to_litres(self) -> None:
        # prior_loss_fraction must only apply to gas mode, NOT litre mode
        valid = np.ones(24, dtype=bool)
        months = np.full(24, 4, dtype=int)
        hours = np.arange(24, dtype=int)

        demand_base = build_hourly_dhw_demand(
            valid=valid, months=months, hours=hours,
            litres_per_day=100.0, prior_loss_fraction=0.0,
        )
        demand_with_loss = build_hourly_dhw_demand(
            valid=valid, months=months, hours=hours,
            litres_per_day=100.0, prior_loss_fraction=0.2,
        )
        demand_loss_1 = build_hourly_dhw_demand(
            valid=valid, months=months, hours=hours,
            litres_per_day=100.0, prior_loss_fraction=1.0,
        )
        np.testing.assert_allclose(demand_with_loss, demand_base, rtol=1e-12)
        np.testing.assert_allclose(demand_loss_1, demand_base, rtol=1e-12)

    def test_gas_mode_prior_loss_fraction_one_limiting_sensitivity(self) -> None:
        # When prior_loss_fraction=1.0, all gross gas was standing loss -> net demand is 0
        valid = np.ones(24, dtype=bool)
        months = np.full(24, 1, dtype=int)
        hours = np.arange(24, dtype=int)

        demand = build_hourly_dhw_demand(
            valid=valid, months=months, hours=hours,
            monthly_dhw_kwh=[100.0] * 12, prior_loss_fraction=1.0,
        )
        self.assertEqual(demand.shape, (24,))
        self.assertEqual(float(np.sum(demand)), 0.0)

    def test_zero_demand_and_disabled_and_no_active_timing(self) -> None:
        valid = np.ones(24, dtype=bool)
        months = np.full(24, 1, dtype=int)
        # All valid slots in month 1 are outside draw timing (e.g. only hour 3)
        hours_outside = np.full(24, 3, dtype=int)

        # 1. Zero demand cases must succeed with all zeros without error
        demand_zero_l = build_hourly_dhw_demand(
            valid=valid, months=months, hours=hours_outside,
            litres_per_day=0.0,
        )
        self.assertEqual(float(np.sum(demand_zero_l)), 0.0)

        demand_zero_gas = build_hourly_dhw_demand(
            valid=valid, months=months, hours=hours_outside,
            monthly_dhw_kwh=[0.0] * 12,
        )
        self.assertEqual(float(np.sum(demand_zero_gas)), 0.0)

        demand_gas_loss_1 = build_hourly_dhw_demand(
            valid=valid, months=months, hours=hours_outside,
            monthly_dhw_kwh=[50.0] * 12, prior_loss_fraction=1.0,
        )
        self.assertEqual(float(np.sum(demand_gas_loss_1)), 0.0)

        # 2. Positive budget with insufficient/no active draw timing slots must raise ValueError
        with self.assertRaises(ValueError):
            build_hourly_dhw_demand(
                valid=valid, months=months, hours=hours_outside,
                litres_per_day=100.0,
            )
        with self.assertRaises(ValueError):
            build_hourly_dhw_demand(
                valid=valid, months=months, hours=hours_outside,
                monthly_dhw_kwh=[50.0] * 12,
            )

    def test_valid_slots_padding_ignored_and_slot_validation(self) -> None:
        # Invalid slots with padding (-1, 0) must be ignored
        valid = np.array([True, True, False, False])
        months = np.array([5, 5, -1, 0])
        hours = np.array([7, 8, -1, 0])

        demand = build_hourly_dhw_demand(
            valid=valid, months=months, hours=hours,
            litres_per_day=100.0,
        )
        self.assertEqual(demand.shape, (4,))
        self.assertEqual(demand[2], 0.0)
        self.assertEqual(demand[3], 0.0)

        # But invalid months at VALID slots must be rejected
        with self.assertRaises(ValueError):
            build_hourly_dhw_demand(
                valid=np.array([True]), months=np.array([0]), hours=np.array([8]),
                litres_per_day=50.0,
            )
        with self.assertRaises(ValueError):
            build_hourly_dhw_demand(
                valid=np.array([True]), months=np.array([13]), hours=np.array([8]),
                litres_per_day=50.0,
            )

        # And invalid hours at VALID slots must be rejected
        with self.assertRaises(ValueError):
            build_hourly_dhw_demand(
                valid=np.array([True]), months=np.array([5]), hours=np.array([-1]),
                litres_per_day=50.0,
            )
        with self.assertRaises(ValueError):
            build_hourly_dhw_demand(
                valid=np.array([True]), months=np.array([5]), hours=np.array([24]),
                litres_per_day=50.0,
            )

        # Incompatible shapes must be rejected
        with self.assertRaises(ValueError):
            build_hourly_dhw_demand(
                valid=np.ones((2, 24), dtype=bool), months=np.ones(20, dtype=int),
                hours=np.arange(24, dtype=int), litres_per_day=50.0,
            )

    def test_tank_step_target_and_inlet_parameter_relationships(self) -> None:
        # min_service_temp_c = 40.0, target_temp_c = 50.0, hygiene_temp_c = 60.0
        tank = DHWTank(
            volume_litres=200.0, target_temp_c=50.0, min_service_temp_c=40.0,
            hygiene_temp_c=60.0, inlet_temp_c=15.0, standing_loss_w_per_k=0.0,
        )

        # 1. Per-step inlet must not exceed min_service_temp_c
        with self.assertRaises(ValueError):
            tank.step(inlet_temp_c=45.0)

        # 2. Per-step target must not be lower than min_service_temp_c
        with self.assertRaises(ValueError):
            tank.step(target_temp_c=35.0)

        # 3. Per-step target must not exceed hygiene_temp_c
        with self.assertRaises(ValueError):
            tank.step(target_temp_c=65.0)

        # 4. remaining_heat_required must not accept target < min_service_temp_c
        with self.assertRaises(ValueError):
            tank.remaining_heat_required(target_temp_c=35.0)

        # 5. Valid parameter step succeeds
        res = tank.step(inlet_temp_c=20.0, target_temp_c=55.0, duration_hours=1.0)
        self.assertAlmostEqual(res.end_temp_c, 50.0, places=5)


if __name__ == "__main__":
    unittest.main()
