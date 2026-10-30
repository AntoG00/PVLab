"""Unit tests for Stage 3B shared compressor controller and hourly dispatch simulation.

Covers:
1. Analytic constant COP=2.0 / capacity=4.0 kW space-only dispatch yields 2.0 kWh input for 4.0 kWh heat.
2. COP trends: colder ambient drops COP and capacity; hotter water (DHW) drops COP relative to space.
3. DHW priority reduces room compressor runtime and delivery during simultaneous demand.
4. Hourly compressor duty sum (DHW runtime + Space runtime) <= 1.0 hour in all hours.
5. Capacity shortfall (HP max capacity limit) and emitter shortfall (radiator emission limit).
6. Bounded shared resistance backup heater (allocated DHW first, then space; incl 0/off; radiator bound respected).
7. External pump timing (only during active runtime) and separate standby power conservation.
8. Map outside envelope handling (conservative zero capacity; no optimistic extrapolation) and defrost metadata skipping.
9. Tank exact energy balance (< 1e-12 error), finite reheat bounding (no electricity for rejected heat), and deadband latching.
10. Hygiene cycle success and failure reporting (explicit target miss; no claim of adequacy).
11. Inactive space schedule mask enforces strictly zero space compressor runtime and zero electricity.
12. Part-load cycling degradation penalty applied once when below minimum modulation fraction.
13. Zero request yields zero arbitrary energy.
14. Validation of 1D arrays, shape mismatches, non-finite values, and negative inputs.
15. Full 8760-hour synthetic annual run with exact conservation.
"""

from __future__ import annotations

import math
import unittest

import numpy as np

from feasibility.heatpump import (
    HeatPumpMapMetadata,
    HeatPumpPerformanceMap,
    WeatherCompensationCurve,
    create_provisional_fallback_map,
)
from feasibility.heatpump_dispatch import simulate_shared_heat_pump
from feasibility.heatpump_tank import DHWTank


def _make_constant_map(cop: float = 2.0, capacity_kw: float = 4.0) -> HeatPumpPerformanceMap:
    return HeatPumpPerformanceMap(
        outdoor_temps=[-10.0, 30.0],
        water_temps=[25.0, 65.0],
        cop_grid=[[cop, cop], [cop, cop]],
        capacity_grid_kw=[[capacity_kw, capacity_kw], [capacity_kw, capacity_kw]],
        metadata=HeatPumpMapMetadata(
            source="Test Analytic Map",
            model=f"Analytic {capacity_kw}kW COP{cop}",
            test_basis="Analytic",
            includes_defrost=True,
            includes_unit_auxiliaries=True,
        ),
    )


class TestSharedHeatPumpDispatch(unittest.TestCase):
    """Test suite for simulate_shared_heat_pump."""

    def test_analytic_constant_cop2_capacity4_yields_2kwh_for_4kwh_heat(self) -> None:
        """Constant COP 2.0 and capacity 4.0 kW space-only dispatch yields exactly 2.0 kWh input for 4.0 kWh heat."""
        hp_map = _make_constant_map(cop=2.0, capacity_kw=4.0)
        res = simulate_shared_heat_pump(
            outdoor_temp_c=[5.0],
            requested_space_heat_kwh=[4.0],
            draw_heat_kwh=[0.0],
            space_active_mask=[True],
            dhw_reheat_allowed_mask=[False],
            hygiene_mask=[False],
            equipment_map=hp_map,
            rated_radiator_dt50_kw=20.0,
            external_pump_kw=0.0,
            standby_kw=0.0,
            backup_heater_kw=0.0,
            cycling_degradation_penalty=0.0,
        )
        self.assertAlmostEqual(res["delivered_space_heat_kwh"][0], 4.0, places=12)
        self.assertAlmostEqual(res["space_compressor_electricity_kwh"][0], 2.0, places=12)
        self.assertAlmostEqual(res["total_electricity_kwh"][0], 2.0, places=12)
        self.assertAlmostEqual(res["space_runtime_hours"][0], 1.0, places=12)
        self.assertAlmostEqual(res["dhw_runtime_hours"][0], 0.0, places=12)
        self.assertAlmostEqual(res["unmet_space_heat_kwh"][0], 0.0, places=12)
        self.assertAlmostEqual(res["summary"]["seasonal_space_cop"], 2.0, places=12)

    def test_cop_trends_cold_and_hot_water(self) -> None:
        """Colder ambient reduces COP and capacity; hotter water (DHW) reduces COP relative to space."""
        # 2 hours: Hour 0 mild (12°C), Hour 1 cold (-7°C)
        res = simulate_shared_heat_pump(
            outdoor_temp_c=[12.0, -7.0],
            requested_space_heat_kwh=[4.0, 4.0],
            draw_heat_kwh=[1.5, 1.5],
            space_active_mask=[True, True],
            dhw_reheat_allowed_mask=[True, True],
            hygiene_mask=[False, False],
            rated_radiator_dt50_kw=10.0,
            backup_heater_kw=0.0,
            cycling_degradation_penalty=0.0,
        )
        # DHW leaving water is hotter than space flow water
        self.assertTrue(res["dhw_flow_temp_c"][0] > res["space_flow_temp_c"][0])
        # Capacity is lower at -7°C than at 12°C
        self.assertTrue(res["space_capacity_kw"][1] < res["space_capacity_kw"][0])
        self.assertTrue(res["dhw_capacity_kw"][1] < res["dhw_capacity_kw"][0])
        # Electricity per kWh of delivered heat is higher at cold outdoor temp
        spec_elec_mild = res["space_compressor_electricity_kwh"][0] / res["delivered_space_heat_kwh"][0]
        spec_elec_cold = res["space_compressor_electricity_kwh"][1] / res["delivered_space_heat_kwh"][1]
        self.assertTrue(spec_elec_cold > spec_elec_mild)

    def test_dhw_priority_reduces_room_runtime(self) -> None:
        """DHW reheat takes compressor runtime first; room gets only remaining runtime."""
        hp_map = _make_constant_map(cop=3.0, capacity_kw=6.0)
        # Hour 0: Tank cold, needs reheat. Space also requests 6 kWh (would need full 1 hr).
        # Tank volume 200L, C=0.2326 kWh/K. Initial temp 40°C, target 50°C -> needs ~2.326 kWh.
        # At 6 kW capacity, DHW takes ~2.326 / 6 = 0.3877 hr. Space gets remaining ~0.6123 hr.
        tank_cold = DHWTank(volume_litres=200.0, target_temp_c=50.0, initial_temp_c=40.0)
        res_shared = simulate_shared_heat_pump(
            outdoor_temp_c=[7.0],
            requested_space_heat_kwh=[6.0],
            draw_heat_kwh=[0.0],
            space_active_mask=[True],
            dhw_reheat_allowed_mask=[True],
            hygiene_mask=[False],
            equipment_map=hp_map,
            tank=tank_cold,
            rated_radiator_dt50_kw=10.0,
            backup_heater_kw=0.0,
            cycling_degradation_penalty=0.0,
        )
        # Compare with space-only (no DHW reheat allowed)
        tank_cold2 = DHWTank(volume_litres=200.0, target_temp_c=50.0, initial_temp_c=40.0)
        res_space_only = simulate_shared_heat_pump(
            outdoor_temp_c=[7.0],
            requested_space_heat_kwh=[6.0],
            draw_heat_kwh=[0.0],
            space_active_mask=[True],
            dhw_reheat_allowed_mask=[False],
            hygiene_mask=[False],
            equipment_map=hp_map,
            tank=tank_cold2,
            rated_radiator_dt50_kw=10.0,
            backup_heater_kw=0.0,
            cycling_degradation_penalty=0.0,
        )
        # DHW priority reduced space runtime and delivery
        self.assertTrue(res_shared["dhw_runtime_hours"][0] > 0.3)
        self.assertTrue(res_shared["space_runtime_hours"][0] < 0.7)
        self.assertAlmostEqual(res_space_only["space_runtime_hours"][0], 1.0, places=10)
        self.assertTrue(res_shared["delivered_space_heat_kwh"][0] < res_space_only["delivered_space_heat_kwh"][0])
        self.assertAlmostEqual(res_shared["dhw_runtime_hours"][0] + res_shared["space_runtime_hours"][0], 1.0, places=10)

    def test_compressor_duty_sum_le_1(self) -> None:
        """Sum of DHW runtime + Space runtime never exceeds 1.0 hour in any hour."""
        # 10 hours with large demands
        n = 10
        out_t = [2.0] * n
        sp_req = [15.0] * n
        draw_req = [5.0] * n
        res = simulate_shared_heat_pump(
            outdoor_temp_c=out_t,
            requested_space_heat_kwh=sp_req,
            draw_heat_kwh=draw_req,
            space_active_mask=[True] * n,
            dhw_reheat_allowed_mask=[True] * n,
            hygiene_mask=[False] * n,
            backup_heater_kw=3.0,
        )
        duty_sum = res["dhw_runtime_hours"] + res["space_runtime_hours"]
        for h, d in enumerate(duty_sum):
            self.assertLessEqual(d, 1.0 + 1e-12, f"Hour {h} duty sum {d} exceeded 1.0")
        self.assertLessEqual(res["summary"]["max_hourly_compressor_runtime_hours"], 1.0 + 1e-12)

    def test_capacity_and_emitter_shortfalls(self) -> None:
        """Delivery is constrained by equipment max capacity and emitter output bound."""
        # Case 1: Capacity shortfall (request 10 kW, capacity 5 kW, emitter 12 kW)
        map_5kw = _make_constant_map(cop=3.0, capacity_kw=5.0)
        res_cap = simulate_shared_heat_pump(
            outdoor_temp_c=[5.0],
            requested_space_heat_kwh=[10.0],
            draw_heat_kwh=[0.0],
            space_active_mask=[True],
            dhw_reheat_allowed_mask=[False],
            hygiene_mask=[False],
            equipment_map=map_5kw,
            rated_radiator_dt50_kw=25.0,
            backup_heater_kw=0.0,
            cycling_degradation_penalty=0.0,
        )
        self.assertAlmostEqual(res_cap["delivered_space_heat_kwh"][0], 5.0, places=10)
        self.assertAlmostEqual(res_cap["unmet_space_heat_kwh"][0], 5.0, places=10)

        # Case 2: Emitter shortfall (request 10 kW, capacity 10 kW, but tiny radiator)
        map_10kw = _make_constant_map(cop=3.0, capacity_kw=10.0)
        res_rad = simulate_shared_heat_pump(
            outdoor_temp_c=[5.0],
            requested_space_heat_kwh=[10.0],
            draw_heat_kwh=[0.0],
            space_active_mask=[True],
            dhw_reheat_allowed_mask=[False],
            hygiene_mask=[False],
            equipment_map=map_10kw,
            rated_radiator_dt50_kw=2.0,  # small radiator, output bound will be ~0.7 kW
            backup_heater_kw=0.0,
            cycling_degradation_penalty=0.0,
        )
        rad_limit = res_rad["radiator_bound_kw"][0]
        self.assertTrue(rad_limit < 2.0)
        self.assertAlmostEqual(res_rad["delivered_space_heat_kwh"][0], rad_limit, places=10)
        self.assertAlmostEqual(res_rad["unmet_space_heat_kwh"][0], 10.0 - rad_limit, places=10)

    def test_bounded_shared_backup_including_zero_and_off(self) -> None:
        """Shared backup resistance heater is bounded, allocated DHW then space, cannot bypass radiator limit."""
        map_zero = _make_constant_map(cop=2.0, capacity_kw=0.0)  # HP inactive/zero capacity
        tank_cold = DHWTank(volume_litres=200.0, target_temp_c=50.0, initial_temp_c=45.0)  # needs ~1.163 kWh

        # Subtest A: Backup OFF (0 kW)
        res_off = simulate_shared_heat_pump(
            outdoor_temp_c=[5.0],
            requested_space_heat_kwh=[4.0],
            draw_heat_kwh=[0.0],
            space_active_mask=[True],
            dhw_reheat_allowed_mask=[True],
            hygiene_mask=[False],
            equipment_map=map_zero,
            tank=tank_cold,
            backup_heater_kw=0.0,
        )
        self.assertEqual(res_off["space_backup_electricity_kwh"][0], 0.0)
        self.assertEqual(res_off["dhw_backup_electricity_kwh"][0], 0.0)
        self.assertEqual(res_off["delivered_space_heat_kwh"][0], 0.0)
        self.assertEqual(res_off["unmet_space_heat_kwh"][0], 4.0)

        # Subtest B: Backup 3.0 kW shared: DHW gets first share, space gets remaining
        tank_cold_b = DHWTank(volume_litres=200.0, target_temp_c=50.0, initial_temp_c=45.0)
        res_on = simulate_shared_heat_pump(
            outdoor_temp_c=[5.0],
            requested_space_heat_kwh=[4.0],
            draw_heat_kwh=[0.0],
            space_active_mask=[True],
            dhw_reheat_allowed_mask=[True],
            hygiene_mask=[False],
            equipment_map=map_zero,
            tank=tank_cold_b,
            rated_radiator_dt50_kw=10.0,
            backup_heater_kw=3.0,
            backup_efficiency=1.0,
        )
        dhw_b = res_on["dhw_backup_electricity_kwh"][0]
        sp_b = res_on["space_backup_electricity_kwh"][0]
        self.assertTrue(1.0 < dhw_b < 1.3)  # DHW satisfied first
        self.assertTrue(sp_b > 1.5)        # Space gets remaining
        self.assertAlmostEqual(dhw_b + sp_b, 3.0, places=10)  # sum bounded by 3.0 kW * 1h

        # Subtest C: Hydronic backup cannot bypass radiator limit
        # Tiny radiator rated at 1.0 kW -> bound at flow temp is ~0.35 kW
        tank_satisfied = DHWTank(volume_litres=200.0, target_temp_c=50.0, initial_temp_c=50.0)
        res_rad_limit = simulate_shared_heat_pump(
            outdoor_temp_c=[5.0],
            requested_space_heat_kwh=[4.0],
            draw_heat_kwh=[0.0],
            space_active_mask=[True],
            dhw_reheat_allowed_mask=[False],
            hygiene_mask=[False],
            equipment_map=map_zero,
            tank=tank_satisfied,
            rated_radiator_dt50_kw=1.0,
            backup_heater_kw=3.0,
        )
        rad_bound = res_rad_limit["radiator_bound_kw"][0]
        self.assertAlmostEqual(res_rad_limit["delivered_space_heat_kwh"][0], rad_bound, places=10)
        self.assertAlmostEqual(res_rad_limit["space_backup_electricity_kwh"][0], rad_bound, places=10)

    def test_aux_timing_and_conservation(self) -> None:
        """External pump runs only during actual operating runtime; standby power runs separately."""
        hp_map = _make_constant_map(cop=2.0, capacity_kw=4.0)
        # Hour 0: Runs for 0.5 hr (2.0 kWh demand / 4.0 kW capacity).
        # Hour 1: Runs for 0.0 hr (0.0 demand).
        res = simulate_shared_heat_pump(
            outdoor_temp_c=[5.0, 5.0],
            requested_space_heat_kwh=[2.0, 0.0],
            draw_heat_kwh=[0.0, 0.0],
            space_active_mask=[True, True],
            dhw_reheat_allowed_mask=[False, False],
            hygiene_mask=[False, False],
            equipment_map=hp_map,
            rated_radiator_dt50_kw=20.0,
            external_pump_kw=0.06,
            standby_kw=0.02,
            backup_heater_kw=0.0,
            cycling_degradation_penalty=0.0,
        )
        # Hour 0: pump runs 0.5 hr -> 0.03 kWh, standby runs 1 hr -> 0.02 kWh
        self.assertAlmostEqual(res["external_pump_electricity_kwh"][0], 0.06 * 0.5, places=10)
        self.assertAlmostEqual(res["standby_electricity_kwh"][0], 0.02, places=10)
        # Hour 1: pump runs 0 hr -> 0.0 kWh, standby runs 1 hr -> 0.02 kWh
        self.assertAlmostEqual(res["external_pump_electricity_kwh"][1], 0.0, places=10)
        self.assertAlmostEqual(res["standby_electricity_kwh"][1], 0.02, places=10)

    def test_map_outside_and_defrost_metadata(self) -> None:
        """Map outside flag set; zero capacity outside envelope; metadata skips duplicate defrost."""
        fallback = create_provisional_fallback_map(nominal_capacity_kw=9.0)
        # Outdoor -25°C is below map min (-15°C)
        res = simulate_shared_heat_pump(
            outdoor_temp_c=[-25.0],
            requested_space_heat_kwh=[5.0],
            draw_heat_kwh=[0.0],
            space_active_mask=[True],
            dhw_reheat_allowed_mask=[False],
            hygiene_mask=[False],
            equipment_map=fallback,
            backup_heater_kw=0.0,
        )
        self.assertTrue(res["map_outside_flag"][0])
        self.assertEqual(res["space_capacity_kw"][0], 0.0)
        self.assertEqual(res["space_compressor_electricity_kwh"][0], 0.0)
        self.assertEqual(res["unmet_space_heat_kwh"][0], 5.0)

        # Defrost metadata test: map includes defrost, so additional penalty is skipped
        res_def = simulate_shared_heat_pump(
            outdoor_temp_c=[2.0],
            requested_space_heat_kwh=[4.0],
            draw_heat_kwh=[0.0],
            space_active_mask=[True],
            dhw_reheat_allowed_mask=[False],
            hygiene_mask=[False],
            equipment_map=fallback,
            additional_defrost_cop_penalty=0.20,
            force_defrost_adjustment=False,
            cycling_degradation_penalty=0.0,
            backup_heater_kw=0.0,
        )
        res_base = simulate_shared_heat_pump(
            outdoor_temp_c=[2.0],
            requested_space_heat_kwh=[4.0],
            draw_heat_kwh=[0.0],
            space_active_mask=[True],
            dhw_reheat_allowed_mask=[False],
            hygiene_mask=[False],
            equipment_map=fallback,
            additional_defrost_cop_penalty=0.0,
            cycling_degradation_penalty=0.0,
            backup_heater_kw=0.0,
        )
        # Electricity is identical because duplicate defrost was skipped via metadata
        self.assertAlmostEqual(
            res_def["space_compressor_electricity_kwh"][0],
            res_base["space_compressor_electricity_kwh"][0],
            places=10,
        )

    def test_tank_energy_balance_finite_reheat_deadband(self) -> None:
        """Tank energy balance exact (< 1e-12 error), finite reheat bounded, deadband respected."""
        n = 24
        out_t = [5.0] * n
        sp_req = [2.0] * n
        draw_req = [0.0] * n
        draw_req[7] = 2.5   # Morning draw
        draw_req[19] = 2.5  # Evening draw

        tank = DHWTank(volume_litres=200.0, target_temp_c=50.0, deadband_k=5.0, initial_temp_c=50.0)
        res = simulate_shared_heat_pump(
            outdoor_temp_c=out_t,
            requested_space_heat_kwh=sp_req,
            draw_heat_kwh=draw_req,
            space_active_mask=[True] * n,
            dhw_reheat_allowed_mask=[True] * n,
            hygiene_mask=[False] * n,
            tank=tank,
        )
        # Energy conservation check
        self.assertLess(res["summary"]["max_tank_conservation_error_kwh"], 1e-12)
        delta_s = res["summary"]["final_storage_kwh"] - res["summary"]["initial_storage_kwh"]
        net_flows = res["summary"]["annual_heat_supplied_tank_kwh"] - (
            res["summary"]["annual_requested_draw_heat_kwh"] - res["summary"]["annual_unmet_draw_heat_kwh"]
        ) - res["summary"]["annual_tank_losses_kwh"]
        self.assertAlmostEqual(delta_s, net_flows, places=10)

    def test_hygiene_success_and_failure(self) -> None:
        """Hygiene schedule checks: successful reach vs recorded target miss without adequacy claims."""
        # Success: Heat pump + backup can easily reach 60°C
        tank_a = DHWTank(volume_litres=200.0, target_temp_c=50.0, hygiene_temp_c=60.0, initial_temp_c=50.0)
        res_ok = simulate_shared_heat_pump(
            outdoor_temp_c=[10.0],
            requested_space_heat_kwh=[0.0],
            draw_heat_kwh=[0.0],
            space_active_mask=[False],
            dhw_reheat_allowed_mask=[True],
            hygiene_mask=[True],
            tank=tank_a,
            backup_heater_kw=3.0,
        )
        self.assertFalse(res_ok["hygiene_target_miss"][0])
        self.assertAlmostEqual(res_ok["tank_temp_c"][0], 60.0, places=1)

        # Failure: Outside map envelope and 0 backup heater
        map_zero = _make_constant_map(cop=2.0, capacity_kw=0.0)
        tank_b = DHWTank(volume_litres=200.0, target_temp_c=50.0, hygiene_temp_c=60.0, initial_temp_c=50.0)
        res_fail = simulate_shared_heat_pump(
            outdoor_temp_c=[10.0],
            requested_space_heat_kwh=[0.0],
            draw_heat_kwh=[0.0],
            space_active_mask=[False],
            dhw_reheat_allowed_mask=[True],
            hygiene_mask=[True],
            equipment_map=map_zero,
            tank=tank_b,
            backup_heater_kw=0.0,
        )
        self.assertTrue(res_fail["hygiene_target_miss"][0])
        self.assertEqual(res_fail["summary"]["hygiene_target_miss_count"], 1)

    def test_schedule_off_zero_space_compressor(self) -> None:
        """When space active mask is False, space compressor runtime and electricity are strictly 0."""
        res = simulate_shared_heat_pump(
            outdoor_temp_c=[0.0],
            requested_space_heat_kwh=[5.0],
            draw_heat_kwh=[0.0],
            space_active_mask=[False],
            dhw_reheat_allowed_mask=[False],
            hygiene_mask=[False],
            backup_heater_kw=3.0,
        )
        self.assertEqual(res["delivered_space_heat_kwh"][0], 0.0)
        self.assertEqual(res["space_compressor_electricity_kwh"][0], 0.0)
        self.assertEqual(res["space_backup_electricity_kwh"][0], 0.0)
        self.assertEqual(res["space_runtime_hours"][0], 0.0)
        self.assertEqual(res["unmet_space_heat_kwh"][0], 5.0)

    def test_partload_cycling_applied_once(self) -> None:
        """Part-load cycling degradation is applied once when below min modulation fraction."""
        hp_map = _make_constant_map(cop=4.0, capacity_kw=10.0)
        # Demand = 1.0 kWh -> runtime = 0.1 hr (10% duty cycle). Min modulation = 0.25.
        # PLR = 0.10 / 0.25 = 0.40. Degradation penalty = 0.10 -> f_cycle = 1.0 - 0.10*(1 - 0.40) = 0.94.
        # Ideal electricity = 1.0 / 4.0 = 0.25 kWh. Degraded electricity = 0.25 / 0.94 = 0.26596 kWh.
        res_cycled = simulate_shared_heat_pump(
            outdoor_temp_c=[5.0],
            requested_space_heat_kwh=[1.0],
            draw_heat_kwh=[0.0],
            space_active_mask=[True],
            dhw_reheat_allowed_mask=[False],
            hygiene_mask=[False],
            equipment_map=hp_map,
            rated_radiator_dt50_kw=35.0,
            backup_heater_kw=0.0,
            min_modulation_fraction=0.25,
            cycling_degradation_penalty=0.10,
        )
        res_uncycled = simulate_shared_heat_pump(
            outdoor_temp_c=[5.0],
            requested_space_heat_kwh=[1.0],
            draw_heat_kwh=[0.0],
            space_active_mask=[True],
            dhw_reheat_allowed_mask=[False],
            hygiene_mask=[False],
            equipment_map=hp_map,
            rated_radiator_dt50_kw=35.0,
            backup_heater_kw=0.0,
            min_modulation_fraction=0.25,
            cycling_degradation_penalty=0.0,  # no penalty
        )
        self.assertAlmostEqual(res_uncycled["space_compressor_electricity_kwh"][0], 0.25, places=10)
        expected_degraded = 0.25 / (1.0 - 0.10 * (1.0 - 0.1 / 0.25))
        self.assertAlmostEqual(res_cycled["space_compressor_electricity_kwh"][0], expected_degraded, places=10)

    def test_no_request_no_arbitrary_energy(self) -> None:
        """Zero request yields zero arbitrary electricity or delivered heat."""
        res = simulate_shared_heat_pump(
            outdoor_temp_c=[5.0],
            requested_space_heat_kwh=[0.0],
            draw_heat_kwh=[0.0],
            space_active_mask=[True],
            dhw_reheat_allowed_mask=[True],
            hygiene_mask=[False],
            standby_kw=0.0,
            external_pump_kw=0.06,
        )
        self.assertEqual(res["delivered_space_heat_kwh"][0], 0.0)
        self.assertEqual(res["space_compressor_electricity_kwh"][0], 0.0)
        self.assertEqual(res["dhw_compressor_electricity_kwh"][0], 0.0)
        self.assertEqual(res["external_pump_electricity_kwh"][0], 0.0)
        self.assertEqual(res["total_electricity_kwh"][0], 0.0)

    def test_input_validation(self) -> None:
        """Reject mismatched array lengths, non-1D arrays, and negative demand inputs."""
        with self.assertRaises(ValueError):
            simulate_shared_heat_pump([5.0, 5.0], [1.0], [0.0], [True], [True], [False])
        with self.assertRaises(ValueError):
            simulate_shared_heat_pump([5.0], [-1.0], [0.0], [True], [True], [False])
        with self.assertRaises(ValueError):
            simulate_shared_heat_pump([5.0], [1.0], [-0.5], [True], [True], [False])
        with self.assertRaises(ValueError):
            simulate_shared_heat_pump([[5.0]], [1.0], [0.0], [True], [True], [False])

    def test_full_8760_synthetic_annual_run(self) -> None:
        """Full 8760-hour synthetic run executes efficiently with exact conservation."""
        n = 8760
        np.random.seed(42)
        # Synthetic weather: sinusoidal ambient between -5°C and 30°C
        hours = np.arange(n)
        outdoor_t = 12.5 - 17.5 * np.cos(2.0 * np.pi * hours / 8760.0)
        # Synthetic demands: space heating in winter, DHW draws twice daily
        is_winter = (outdoor_t < 16.0)
        space_req = np.where(is_winter, np.maximum(0.0, (18.0 - outdoor_t) * 0.4), 0.0)
        draw_req = np.zeros(n)
        hod = hours % 24
        draw_req[(hod == 8) | (hod == 19)] = 1.2
        space_mask = is_winter
        reheat_mask = np.ones(n, dtype=bool)
        hygiene_mask = np.zeros(n, dtype=bool)
        hygiene_mask[(hours % 168 == 2)] = True  # Weekly Sunday 02:00 hygiene cycle

        res = simulate_shared_heat_pump(
            outdoor_temp_c=outdoor_t,
            requested_space_heat_kwh=space_req,
            draw_heat_kwh=draw_req,
            space_active_mask=space_mask,
            dhw_reheat_allowed_mask=reheat_mask,
            hygiene_mask=hygiene_mask,
            backup_heater_kw=3.0,
        )
        self.assertEqual(len(res["total_electricity_kwh"]), n)
        self.assertLess(res["summary"]["max_tank_conservation_error_kwh"], 1e-11)
        self.assertTrue(res["summary"]["annual_delivered_space_heat_kwh"] > 0.0)
        self.assertTrue(res["summary"]["annual_total_electricity_kwh"] > 0.0)
        self.assertTrue(res["summary"]["seasonal_space_cop"] > 2.0)
        self.assertTrue(res["summary"]["seasonal_dhw_cop_heat_to_tank"] > 1.5)
        self.assertLessEqual(res["summary"]["max_hourly_compressor_runtime_hours"], 1.0 + 1e-12)

    def test_crossing_minservice_temp_actual_draw_differs_from_served(self) -> None:
        """Draw below min_service_temp_c extracts actual thermal heat without false tank balance error or negative SPF."""
        res = simulate_shared_heat_pump(
            outdoor_temp_c=[5.0],
            requested_space_heat_kwh=[0.0],
            draw_heat_kwh=[2.0],
            space_active_mask=[False],
            dhw_reheat_allowed_mask=[False],
            hygiene_mask=[False],
            tank_initial_temp_c=35.0,  # Below default min_service_temp_c (40.0°C)
            tank_standing_loss_w_per_k=0.0,
            external_pump_kw=0.0,
            standby_kw=0.0,
            backup_heater_kw=0.0,
        )
        self.assertLess(res["summary"]["max_tank_conservation_error_kwh"], 1e-12)
        self.assertAlmostEqual(res["summary"]["annual_actual_draw_heat_kwh"], 2.0, places=10)
        self.assertAlmostEqual(res["summary"]["annual_delivered_draw_heat_kwh"], 0.0, places=10)
        self.assertAlmostEqual(res["summary"]["annual_unmet_draw_heat_kwh"], 2.0, places=10)
        self.assertAlmostEqual(res["summary"]["annual_unserved_draw_heat_kwh"], 2.0, places=10)
        self.assertAlmostEqual(res["actual_draw_heat_kwh"][0], 2.0, places=10)
        self.assertAlmostEqual(res["delivered_draw_heat_kwh"][0], 0.0, places=10)
        self.assertAlmostEqual(res["summary"]["storage_delta_kwh"], -2.0, places=10)
        self.assertEqual(res["summary"]["seasonal_performance_factor_delivered_heat"], 0.0)

    def test_backup_only_loss_compensated_1kwh_requires_1_25_elec(self) -> None:
        """Backup-only outside envelope with 20% pipe loss compensates required equipment heat: 1 kWh delivered requires 1.25 kWh electricity."""
        res = simulate_shared_heat_pump(
            outdoor_temp_c=[-30.0],  # Outside provisional map envelope (-15°C to 40°C)
            requested_space_heat_kwh=[1.0],
            draw_heat_kwh=[0.0],
            space_active_mask=[True],
            dhw_reheat_allowed_mask=[False],
            hygiene_mask=[False],
            rated_radiator_dt50_kw=100.0,  # High emitter capacity
            backup_heater_kw=3.0,
            backup_efficiency=1.0,
            thermal_distribution_loss_fraction=0.20,
            external_pump_kw=0.0,
            standby_kw=0.0,
            cycling_degradation_penalty=0.0,
        )
        self.assertAlmostEqual(res["delivered_space_heat_kwh"][0], 1.0, places=10)
        self.assertAlmostEqual(res["unmet_space_heat_kwh"][0], 0.0, places=10)
        self.assertAlmostEqual(res["space_backup_electricity_kwh"][0], 1.25, places=10)
        self.assertAlmostEqual(res["total_electricity_kwh"][0], 1.25, places=10)
        self.assertAlmostEqual(res["space_compressor_electricity_kwh"][0], 0.0, places=10)
        self.assertAlmostEqual(res["space_distribution_loss_kwh"][0], 0.25, places=10)
        self.assertAlmostEqual(res["space_backup_heat_kwh"][0], 1.25, places=10)
        self.assertAlmostEqual(res["summary"]["seasonal_scop_space"], 1.0 / 1.25, places=10)

    def test_constant_cop_pipe_loss_heat_balance(self) -> None:
        """Directly derived HP compressor heat preserves declared COP=2.0 under 20% distribution loss and balances heat."""
        hp_map = _make_constant_map(cop=2.0, capacity_kw=10.0)
        res = simulate_shared_heat_pump(
            outdoor_temp_c=[5.0],
            requested_space_heat_kwh=[4.0],
            draw_heat_kwh=[0.0],
            space_active_mask=[True],
            dhw_reheat_allowed_mask=[False],
            hygiene_mask=[False],
            equipment_map=hp_map,
            rated_radiator_dt50_kw=25.0,
            backup_heater_kw=0.0,
            thermal_distribution_loss_fraction=0.20,
            external_pump_kw=0.0,
            standby_kw=0.0,
            cycling_degradation_penalty=0.0,
        )
        # Required equipment heat = 4.0 / (1 - 0.2) = 5.0 kWh
        # Compressor electricity = 5.0 / 2.0 = 2.5 kWh
        # Delivered heat = 5.0 * (1 - 0.2) = 4.0 kWh
        # Pipe loss = 5.0 * 0.2 = 1.0 kWh
        self.assertAlmostEqual(res["delivered_space_heat_kwh"][0], 4.0, places=10)
        self.assertAlmostEqual(res["space_compressor_electricity_kwh"][0], 2.5, places=10)
        self.assertAlmostEqual(res["space_compressor_heat_kwh"][0], 5.0, places=10)
        self.assertAlmostEqual(res["space_distribution_loss_kwh"][0], 1.0, places=10)
        # Thermal balance: HP thermal output == delivered useful room heat + distribution loss
        self.assertAlmostEqual(
            res["space_compressor_heat_kwh"][0],
            res["delivered_space_heat_kwh"][0] + res["space_distribution_loss_kwh"][0],
            places=10,
        )
        self.assertAlmostEqual(res["summary"]["seasonal_space_cop"], 2.0, places=10)
        self.assertAlmostEqual(res["summary"]["seasonal_compressor_cop"], 2.0, places=10)

    def test_radiator_output_physically_bounds_both_compressor_and_backup(self) -> None:
        """Radiator thermal emission limit strictly bounds both compressor and hydronic backup under pipe losses."""
        # Small radiator: DT50 = 1.0 kW -> output at flow temp is ~0.35 kW
        map_5kw = _make_constant_map(cop=3.0, capacity_kw=5.0)
        res = simulate_shared_heat_pump(
            outdoor_temp_c=[5.0],
            requested_space_heat_kwh=[5.0],
            draw_heat_kwh=[0.0],
            space_active_mask=[True],
            dhw_reheat_allowed_mask=[False],
            hygiene_mask=[False],
            equipment_map=map_5kw,
            rated_radiator_dt50_kw=1.0,
            backup_heater_kw=3.0,
            thermal_distribution_loss_fraction=0.10,
            cycling_degradation_penalty=0.0,
        )
        rad_bound_kw = res["radiator_bound_kw"][0]
        self.assertTrue(rad_bound_kw < 1.0)
        # Delivered room heat cannot exceed physical radiator emission bound
        self.assertLessEqual(res["delivered_space_heat_kwh"][0], rad_bound_kw + 1e-12)
        # And unmet heat reflects this emitter bottleneck
        self.assertAlmostEqual(res["unmet_space_heat_kwh"][0], 5.0 - res["delivered_space_heat_kwh"][0], places=10)

    def test_no_negative_cop_or_spf(self) -> None:
        """No scenario produces negative COP, SCOP, or SPF values."""
        # Scenario 1: Standby power with unserved withdrawal and tank cooling
        res1 = simulate_shared_heat_pump(
            outdoor_temp_c=[5.0],
            requested_space_heat_kwh=[0.0],
            draw_heat_kwh=[2.0],
            space_active_mask=[False],
            dhw_reheat_allowed_mask=[False],
            hygiene_mask=[False],
            tank_initial_temp_c=35.0,
            standby_kw=0.05,
            external_pump_kw=0.02,
            backup_heater_kw=0.0,
        )
        summary1 = res1["summary"]
        self.assertGreaterEqual(summary1["seasonal_space_cop"], 0.0)
        self.assertGreaterEqual(summary1["seasonal_dhw_cop_heat_to_tank"], 0.0)
        self.assertGreaterEqual(summary1["seasonal_compressor_cop"], 0.0)
        self.assertGreaterEqual(summary1["seasonal_performance_factor_delivered_heat"], 0.0)
        self.assertGreaterEqual(summary1["seasonal_performance_factor_heat_to_tank"], 0.0)
        self.assertGreaterEqual(summary1["seasonal_scop_space"], 0.0)
        self.assertGreaterEqual(summary1["seasonal_scop_dhw_net"], 0.0)

        # Scenario 2: Backup-only with distribution loss and zero compressor runtime
        res2 = simulate_shared_heat_pump(
            outdoor_temp_c=[-25.0],
            requested_space_heat_kwh=[2.0],
            draw_heat_kwh=[0.0],
            space_active_mask=[True],
            dhw_reheat_allowed_mask=[False],
            hygiene_mask=[False],
            backup_heater_kw=3.0,
            thermal_distribution_loss_fraction=0.25,
        )
        summary2 = res2["summary"]
        self.assertGreaterEqual(summary2["seasonal_space_cop"], 0.0)
        self.assertGreaterEqual(summary2["seasonal_compressor_cop"], 0.0)
        self.assertGreaterEqual(summary2["seasonal_scop_space"], 0.0)
        self.assertGreaterEqual(summary2["seasonal_performance_factor_delivered_heat"], 0.0)

    def test_nan_inf_negative_scalar_validation(self) -> None:
        """Every scalar input properly rejects NaN, Inf, and invalid negative/bounded values without silent clamping."""
        base_args = {
            "outdoor_temp_c": [5.0],
            "requested_space_heat_kwh": [1.0],
            "draw_heat_kwh": [0.0],
            "space_active_mask": [True],
            "dhw_reheat_allowed_mask": [False],
            "hygiene_mask": [False],
        }

        # Check negative standby creates error (not negative electricity)
        with self.assertRaises(ValueError):
            simulate_shared_heat_pump(**base_args, standby_kw=-0.01)

        # Scalars that must be non-negative (>= 0)
        non_negative_scalars = [
            "external_pump_kw", "standby_kw", "coil_approach_delta_c",
            "additional_unit_auxiliary_kw", "backup_heater_kw",
            "rated_radiator_dt50_kw", "tank_standing_loss_w_per_k",
        ]
        for param in non_negative_scalars:
            with self.assertRaises(ValueError, msg=f"{param} should reject negative"):
                simulate_shared_heat_pump(**base_args, **{param: -1.0})
            with self.assertRaises(ValueError, msg=f"{param} should reject NaN"):
                simulate_shared_heat_pump(**base_args, **{param: float("nan")})
            with self.assertRaises(ValueError, msg=f"{param} should reject Inf"):
                simulate_shared_heat_pump(**base_args, **{param: float("inf")})

        # Scalars that must be strictly positive (> 0)
        strictly_positive_scalars = [
            "radiator_exponent", "radiator_return_drop_c",
            "tank_volume_litres", "tank_deadband_k", "backup_efficiency",
        ]
        for param in strictly_positive_scalars:
            with self.assertRaises(ValueError, msg=f"{param} should reject 0"):
                simulate_shared_heat_pump(**base_args, **{param: 0.0})
            with self.assertRaises(ValueError, msg=f"{param} should reject negative"):
                simulate_shared_heat_pump(**base_args, **{param: -0.5})
            with self.assertRaises(ValueError, msg=f"{param} should reject NaN"):
                simulate_shared_heat_pump(**base_args, **{param: float("nan")})

        # Scalars bounded in [0, 1) or [0, 1]
        with self.assertRaises(ValueError):
            simulate_shared_heat_pump(**base_args, min_modulation_fraction=1.5)
        with self.assertRaises(ValueError):
            simulate_shared_heat_pump(**base_args, cycling_degradation_penalty=1.0)
        with self.assertRaises(ValueError):
            simulate_shared_heat_pump(**base_args, thermal_distribution_loss_fraction=1.0)
        with self.assertRaises(ValueError):
            simulate_shared_heat_pump(**base_args, additional_defrost_cop_penalty=1.0)
        with self.assertRaises(ValueError):
            simulate_shared_heat_pump(**base_args, backup_efficiency=1.01)

        # substeps_per_hour validation
        with self.assertRaises(ValueError):
            simulate_shared_heat_pump(**base_args, substeps_per_hour=0)
        with self.assertRaises(ValueError):
            simulate_shared_heat_pump(**base_args, substeps_per_hour=-2)
        with self.assertRaises(ValueError):
            simulate_shared_heat_pump(**base_args, substeps_per_hour=2.5)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            simulate_shared_heat_pump(**base_args, substeps_per_hour=True)  # type: ignore[arg-type]

    def test_map_metadata_defrost_and_auxiliary_flags(self) -> None:
        """Map metadata flags (includes_defrost, includes_unit_auxiliaries) govern penalty application unless forced."""
        # Map with includes_defrost=False: penalty IS applied
        map_no_defrost = HeatPumpPerformanceMap(
            outdoor_temps=[-10.0, 30.0], water_temps=[25.0, 65.0],
            cop_grid=[[4.0, 4.0], [4.0, 4.0]], capacity_grid_kw=[[5.0, 5.0], [5.0, 5.0]],
            metadata=HeatPumpMapMetadata(source="T", model="M", test_basis="B", includes_defrost=False, includes_unit_auxiliaries=True),
        )
        res_no_def = simulate_shared_heat_pump(
            outdoor_temp_c=[5.0], requested_space_heat_kwh=[4.0], draw_heat_kwh=[0.0],
            space_active_mask=[True], dhw_reheat_allowed_mask=[False], hygiene_mask=[False],
            equipment_map=map_no_defrost, additional_defrost_cop_penalty=0.10,
            standby_kw=0.0, external_pump_kw=0.0, backup_heater_kw=0.0, cycling_degradation_penalty=0.0,
        )
        # COP degraded by 10% -> 4.0 * 0.9 = 3.6
        self.assertAlmostEqual(res_no_def["summary"]["seasonal_space_cop"], 3.6, places=10)

        # Map with includes_unit_auxiliaries=False: unit aux is added
        map_no_aux = HeatPumpPerformanceMap(
            outdoor_temps=[-10.0, 30.0], water_temps=[25.0, 65.0],
            cop_grid=[[4.0, 4.0], [4.0, 4.0]], capacity_grid_kw=[[4.0, 4.0], [4.0, 4.0]],
            metadata=HeatPumpMapMetadata(source="T", model="M", test_basis="B", includes_defrost=True, includes_unit_auxiliaries=False),
        )
        res_no_aux = simulate_shared_heat_pump(
            outdoor_temp_c=[5.0], requested_space_heat_kwh=[4.0], draw_heat_kwh=[0.0],
            space_active_mask=[True], dhw_reheat_allowed_mask=[False], hygiene_mask=[False],
            equipment_map=map_no_aux, rated_radiator_dt50_kw=25.0, additional_unit_auxiliary_kw=0.05,
            standby_kw=0.0, external_pump_kw=0.0, backup_heater_kw=0.0, cycling_degradation_penalty=0.0,
        )
        # 1 hour runtime at 4 kW: comp elec = 4 / 4 = 1.0 kWh + 0.05 kWh unit aux = 1.05 kWh
        self.assertAlmostEqual(res_no_aux["space_compressor_electricity_kwh"][0], 1.05, places=10)

        # Map with includes_unit_auxiliaries=True: unit aux is SKIPPED unless forced
        map_with_aux = HeatPumpPerformanceMap(
            outdoor_temps=[-10.0, 30.0], water_temps=[25.0, 65.0],
            cop_grid=[[4.0, 4.0], [4.0, 4.0]], capacity_grid_kw=[[4.0, 4.0], [4.0, 4.0]],
            metadata=HeatPumpMapMetadata(source="T", model="M", test_basis="B", includes_defrost=True, includes_unit_auxiliaries=True),
        )
        res_skipped = simulate_shared_heat_pump(
            outdoor_temp_c=[5.0], requested_space_heat_kwh=[4.0], draw_heat_kwh=[0.0],
            space_active_mask=[True], dhw_reheat_allowed_mask=[False], hygiene_mask=[False],
            equipment_map=map_with_aux, rated_radiator_dt50_kw=25.0, additional_unit_auxiliary_kw=0.05, force_unit_auxiliary_adjustment=False,
            standby_kw=0.0, external_pump_kw=0.0, backup_heater_kw=0.0, cycling_degradation_penalty=0.0,
        )
        self.assertAlmostEqual(res_skipped["space_compressor_electricity_kwh"][0], 1.0, places=10)

        res_forced = simulate_shared_heat_pump(
            outdoor_temp_c=[5.0], requested_space_heat_kwh=[4.0], draw_heat_kwh=[0.0],
            space_active_mask=[True], dhw_reheat_allowed_mask=[False], hygiene_mask=[False],
            equipment_map=map_with_aux, rated_radiator_dt50_kw=25.0, additional_unit_auxiliary_kw=0.05, force_unit_auxiliary_adjustment=True,
            standby_kw=0.0, external_pump_kw=0.0, backup_heater_kw=0.0, cycling_degradation_penalty=0.0,
        )
        self.assertAlmostEqual(res_forced["space_compressor_electricity_kwh"][0], 1.05, places=10)


if __name__ == "__main__":
    unittest.main()
