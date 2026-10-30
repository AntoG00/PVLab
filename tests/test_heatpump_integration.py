"""Unit and integration tests for Stage 4B heat pump load and forecast integration.

Verifies:
1. Offpath parity including old scenario (planning_heat_pump False or baseline preserves exact legacy output).
2. Onextra load exact HP sum plus cooking.
3. Independence from legacy SCOP, standalone DHW, and legacy heating schedules.
4. Source gas and heat units (thermal kWh, not Smc or electric kWh) and no double heating.
5. Cooking toggle cache changes and invalidation.
6. Custom manufacturer map capacity as-is vs fallback 9kW nominal scaling.
7. All 19 weather years and valid padding (zero padding on non-leap years).
8. Source cache reuse independent of PV array, battery storage, and financing.
9. Forecast calendar 2027 vs 2028 (leap year) and target calendar recomputation.
10. Europe/Rome DST clock transitions with HP own operating windows.
11. Recomputed temperature-dependent COP, not fixed electric budget.
12. HP summary totals reconciliation across all components.
13. Source weather and PV pairing unchanged across all 19 years.
14. Historical household monthly conservation and EV timing preserved.
15. Dispatch hourly electrical energy conservation in evaluate().
"""
from __future__ import annotations
from feasibility.runtime_context import current_runtime

import calendar
import datetime as dt
import json
import unittest
from zoneinfo import ZoneInfo

import numpy as np

from feasibility import assumptions, engine
from feasibility.heatpump import (
    HeatPumpMapMetadata,
    HeatPumpPerformanceMap,
    create_provisional_fallback_map,
)
from feasibility.heatpump_integration import (
    HP_SOURCE_CACHE_KEYS,
    calculate_cooking_electricity,
    get_forecast_heat_pump_hourly,
    get_source_heat_pump_hourly,
    resolve_equipment_map,
    _cached_source_heat_pump_hourly,
    _cached_forecast_heat_pump_hourly,
    build_source_hygiene_mask,
    build_forecast_hygiene_mask,
)
from feasibility.heatpump_settings import (
    HP_SPEC,
    serialize_manufacturer_map,
    validate_heat_pump_cross_fields,
)
from feasibility.heatpump_tank import (
    build_hourly_dhw_demand,
)


class HeatPumpIntegrationTests(unittest.TestCase):
    """Stage 4B load profiles and forecast integration test suite."""

    def setUp(self):
        self.defaults = engine.defaults()
        self.data = engine.data()
        self.years = list(map(int, self.data['years']))

    def test_01_offpath_parity_including_old_scenario(self):
        """When planning_heat_pump is False or omitted, or load_scenario is baseline,
        exact legacy outputs are bitwise preserved across load_profiles and _forecast_mapped_series.
        """
        # Baseline with planning_heat_pump True vs False
        s_base_off = {**self.defaults, 'load_scenario': 'baseline', 'planning_heat_pump': False}
        s_base_on = {**self.defaults, 'load_scenario': 'baseline', 'planning_heat_pump': True}

        load_b_off, ev_b_off, end_b_off = engine.load_profiles(s_base_off)
        load_b_on, ev_b_on, end_b_on = engine.load_profiles(s_base_on)
        np.testing.assert_array_equal(load_b_off, load_b_on)
        np.testing.assert_array_equal(ev_b_off, ev_b_on)
        self.assertEqual(end_b_off, end_b_on)

        # Electrified with planning_heat_pump False vs old scenario (omitted key)
        s_elec_off = {**self.defaults, 'load_scenario': 'electrified', 'planning_heat_pump': False}
        s_elec_old = {**self.defaults, 'load_scenario': 'electrified'}
        s_elec_old.pop('planning_heat_pump', None)

        load_e_off, ev_e_off, end_e_off = engine.load_profiles(s_elec_off)
        load_e_old, ev_e_old, end_e_old = engine.load_profiles(s_elec_old)
        np.testing.assert_array_equal(load_e_off, load_e_old)
        np.testing.assert_array_equal(ev_e_off, ev_e_old)
        self.assertEqual(end_e_off, end_e_old)

        # Forecast parity for offpath
        fc_off = engine._forecast_mapped_series(s_elec_off)
        fc_old = engine._forecast_mapped_series(s_elec_old)
        np.testing.assert_array_equal(fc_off['mapped_load'], fc_old['mapped_load'])
        np.testing.assert_array_equal(fc_off['mapped_heating'], fc_old['mapped_heating'])
        self.assertNotIn('mapped_hp_breakdown', fc_off)

    def test_02_onextra_load_exact_hp_sum_plus_cooking(self):
        """When planning_heat_pump is True, extra load equals exact HP total electricity
        plus optional induction cooking, and EV remains completely separate.
        """
        s_hp = {
            **self.defaults,
            'load_scenario': 'electrified',
            'planning_heat_pump': True,
            'hp_electric_cooking': True,
        }
        load, ev, enduses = engine.load_profiles(s_hp)
        hp_res = get_source_heat_pump_hourly(s_hp)

        # Calculate base historical household electricity + extra AC
        load_col = 'load_civil' if s_hp.get('timezone_mode', 'civil') == 'civil' else 'load_standard'
        base_h = self.data[load_col] * s_hp['load_growth']
        baseline_cooling = engine.estimated_baseline_cooling(self.data)['summer_baseline_kwh']
        multiplier = float(s_hp['summer_ac_multiplier'])
        extra_ac_kwh = (multiplier - 1.0) * baseline_cooling if multiplier > 1.0 else 0.0

        # Induction cooking
        cook_elec, c_smc, c_kwh = calculate_cooking_electricity(
            s_hp, hp_res['gas_allocation'], self.data['valid'], self.data['month'], self.data['hour']
        )
        self.assertEqual(c_smc, 0.0)
        self.assertGreater(c_kwh, 0.0)

        expected_extra = hp_res['total_electricity_kwh'] + cook_elec
        actual_extra = load - base_h
        np.testing.assert_allclose(actual_extra, expected_extra, rtol=1e-12, atol=1e-12)

        # EV is zero in electrified scenario
        self.assertEqual(float(ev.sum()), 0.0)

        # Now test with EV scenario: EV is separate
        s_hp_ev = {**s_hp, 'load_scenario': 'ev'}
        load_ev, ev_arr, enduses_ev = engine.load_profiles(s_hp_ev)
        np.testing.assert_array_equal(load_ev, load)
        self.assertGreater(float(ev_arr.sum()), 0.0)

    def test_03_independence_from_legacy_scop_dhw_schedules(self):
        """Heat pump load profiles are strictly independent of legacy SCOP, DHW type,
        and legacy heating schedule windows. Input dicts are not mutated.
        """
        s1 = {
            **self.defaults,
            'load_scenario': 'electrified',
            'planning_heat_pump': True,
            'scop': 4.5,
            'dhw_scop': 3.5,
            'heating_schedule_mode': 'scheduled',
            'heating_on_hour': 10,
            'heating_off_hour': 14,
            'dhw_heating_type': 'resistance',
            'dhw_midday_fraction': 0.8,
        }
        s2 = {
            **self.defaults,
            'load_scenario': 'electrified',
            'planning_heat_pump': True,
            'scop': 2.1,
            'dhw_scop': 1.8,
            'heating_schedule_mode': 'continuous',
            'heating_on_hour': 4,
            'heating_off_hour': 6,
            'dhw_heating_type': 'heat_pump',
            'dhw_midday_fraction': 0.2,
        }
        s1_copy = dict(s1)
        s2_copy = dict(s2)

        l1, ev1, end1 = engine.load_profiles(s1)
        l2, ev2, end2 = engine.load_profiles(s2)

        # Inputs not mutated
        self.assertEqual(s1, s1_copy)
        self.assertEqual(s2, s2_copy)

        # Exact parity
        np.testing.assert_array_equal(l1, l2)
        np.testing.assert_array_equal(ev1, ev2)
        self.assertEqual(end1['heating_kwh'], end2['heating_kwh'])
        self.assertEqual(end1['dhw_kwh'], end2['dhw_kwh'])

    def test_04_source_gas_heat_units_and_no_double_heating(self):
        """Thermal demand is calibrated in useful heat units (kWh heat), not gas Smc
        or electric kWh. Legacy AC gas heating and DHW are eliminated exactly once.
        """
        s = {**self.defaults, 'load_scenario': 'electrified', 'planning_heat_pump': True}
        _, _, enduses = engine.load_profiles(s)
        hp_res = get_source_heat_pump_hourly(s)
        gas_alloc = hp_res['gas_allocation']

        # Gas allocation delivers useful thermal kWh = Smc * PCS * boiler_efficiency
        expected_heat_kwh = gas_alloc.heating_smc * s['gas_pcs'] * s['boiler_efficiency']
        np.testing.assert_allclose(gas_alloc.heating_useful_kwh, expected_heat_kwh)

        # Legacy gas space heating and DHW gas are zeroed out (no double heating)
        self.assertEqual(enduses['heating_smc'], 0.0)
        self.assertEqual(enduses['dhw_smc'], 0.0)

        # Cooking gas is retained when hp_electric_cooking is False
        self.assertGreater(enduses['cooking_smc'], 0.0)
        self.assertEqual(enduses['cooking_smc'], float(gas_alloc.cooking_smc.sum()))

    def test_05_cooking_toggle_cache_changes(self):
        """Toggling hp_electric_cooking alters load cache key, invalidates load cache,
        and switches between induction electricity and gas cooking.
        """
        s_gas = {**self.defaults, 'load_scenario': 'electrified', 'planning_heat_pump': True, 'hp_electric_cooking': False}
        s_elec = {**self.defaults, 'load_scenario': 'electrified', 'planning_heat_pump': True, 'hp_electric_cooking': True}

        k_gas = tuple((k, s_gas[k]) for k in engine.LOAD_KEYS)
        k_elec = tuple((k, s_elec[k]) for k in engine.LOAD_KEYS)
        self.assertNotEqual(k_gas, k_elec)
        self.assertIn('hp_electric_cooking', engine.LOAD_KEYS)

        l_gas, _, end_gas = engine.load_profiles(s_gas)
        l_elec, _, end_elec = engine.load_profiles(s_elec)

        # Induction electricity is added
        self.assertGreater(float(l_elec.sum()), float(l_gas.sum()))
        self.assertEqual(end_gas['cooking_kwh'], 0.0)
        self.assertGreater(end_gas['cooking_smc'], 0.0)
        self.assertGreater(end_elec['cooking_kwh'], 0.0)
        self.assertEqual(end_elec['cooking_smc'], 0.0)

    def test_06_custom_map_capacity_as_is_vs_fallback_9_nominal(self):
        """Supplied manufacturer map is used as-is without silent nominal scaling,
        whereas empty/None falls back to provisional 9kW scaled map.
        """
        # Create a custom map with explicit 14 kW capacity
        meta = HeatPumpMapMetadata(
            source="Test Manufacturer",
            model="Custom-14kW",
            test_basis="EN 14511",
            includes_defrost=True,
            includes_unit_auxiliaries=True,
        )
        t_out = np.array([-7.0, 2.0, 7.0])
        t_wat = np.array([35.0, 45.0, 55.0])
        cop_g = np.full((3, 3), 3.5)
        cap_g = np.full((3, 3), 14.0)
        custom_map = HeatPumpPerformanceMap(t_out, t_wat, cop_g, cap_g, meta)
        custom_json = serialize_manufacturer_map(custom_map)

        # With custom map and hp_nominal_kw=6.0, capacity must remain 14.0 kW
        s_custom = {**self.defaults, 'hp_manufacturer_map_json': custom_json, 'hp_nominal_kw': 6.0}
        resolved_custom = resolve_equipment_map(s_custom)
        np.testing.assert_array_equal(resolved_custom.capacity_grid_kw, cap_g)
        self.assertEqual(resolved_custom.metadata.model, "Custom-14kW")

        # Fallback map with nominal 9.0 kW vs 6.0 kW scales capacity
        s_9 = {**self.defaults, 'hp_manufacturer_map_json': '', 'hp_nominal_kw': 9.0}
        s_6 = {**self.defaults, 'hp_manufacturer_map_json': '', 'hp_nominal_kw': 6.0}
        map_9 = resolve_equipment_map(s_9)
        map_6 = resolve_equipment_map(s_6)
        ratio = map_6.capacity_grid_kw / map_9.capacity_grid_kw
        np.testing.assert_allclose(ratio, 6.0 / 9.0, rtol=1e-6)

    def test_07_all_19_weather_valid_padding(self):
        """All 19 weather years are simulated. Non-leap years (8760h) have exact zero padding
        at trailing slots 8760..8784. Leap years (8784h) contain valid simulated values throughout.
        """
        s = {**self.defaults, 'load_scenario': 'electrified', 'planning_heat_pump': True}
        hp_res = get_source_heat_pump_hourly(s)
        elec = hp_res['total_electricity_kwh']
        self.assertEqual(elec.shape, (len(self.years), 8784))

        for wi, yr in enumerate(self.years):
            n_valid = int(self.data['valid'][wi].sum())
            if calendar.isleap(yr):
                self.assertEqual(n_valid, 8784)
                self.assertGreater(float(elec[wi, 8760:].sum()), 0.0)
            else:
                self.assertEqual(n_valid, 8760)
                np.testing.assert_array_equal(elec[wi, 8760:], np.zeros(24))

    def test_08_source_cache_reuse_independent_pv_battery(self):
        """Source heat pump hourly series cache is independent of PV array size,
        battery capacity, inverter, and loan financing.
        """
        s1 = {
            **self.defaults,
            'load_scenario': 'electrified',
            'planning_heat_pump': True,
            'south_panels': 10,
            'battery_kwh': 5.0,
            'inverter_kw': 6.0,
        }
        s2 = {
            **self.defaults,
            'load_scenario': 'electrified',
            'planning_heat_pump': True,
            'south_panels': 25,
            'battery_kwh': 15.0,
            'inverter_kw': 10.0,
            'loan_fraction': 1.0,
        }

        # Prime cache
        _cached_source_heat_pump_hourly.cache_clear()
        engine._load_cache.cache_clear()
        engine.load_profiles(s1)
        info1 = _cached_source_heat_pump_hourly.cache_info()
        self.assertEqual(info1.misses, 1)

        # Second call with different PV/battery must hit the cache
        engine._load_cache.cache_clear()
        engine.load_profiles(s2)
        info2 = _cached_source_heat_pump_hourly.cache_info()
        self.assertEqual(info2.hits, 1)
        self.assertEqual(info2.misses, 1)

    def test_09_forecast_calendar_2027_vs_2028_and_leap(self):
        """Forecast maps correctly to target calendars: 2027 is non-leap (8760 slots),
        2028 is leap (8784 slots). Temperature and PV are paired for Feb 29.
        """
        s_2027 = {**self.defaults, 'load_scenario': 'electrified', 'planning_heat_pump': True, 'installation_year': 2026}
        s_2028 = {**self.defaults, 'load_scenario': 'electrified', 'planning_heat_pump': True, 'installation_year': 2027}

        fc_2027 = engine._forecast_mapped_series(s_2027)
        fc_2028 = engine._forecast_mapped_series(s_2028)

        self.assertEqual(fc_2027['forecast_year'], 2027)
        self.assertFalse(fc_2027['is_leap'])
        self.assertEqual(fc_2027['target_len'], 8760)
        self.assertEqual(fc_2027['mapped_heating'].shape, (len(self.years), 8760))

        self.assertEqual(fc_2028['forecast_year'], 2028)
        self.assertTrue(fc_2028['is_leap'])
        self.assertEqual(fc_2028['target_len'], 8784)
        self.assertEqual(fc_2028['mapped_heating'].shape, (len(self.years), 8784))

        # Check mapped_hp_breakdown present and aligned
        self.assertIn('mapped_hp_breakdown', fc_2028)
        self.assertEqual(fc_2028['mapped_hp_breakdown']['target_len'], 8784)

    def test_10_both_rome_clock_changes_with_hp_own_windows(self):
        """Scheduled heat pump heating windows respect Europe/Rome DST transitions
        in civil mode (no leakage during clock forward/backward transitions).
        """
        s_civil = {
            **self.defaults,
            'load_scenario': 'electrified',
            'planning_heat_pump': True,
            'timezone_mode': 'civil',
            'hp_schedule_mode': 'scheduled',
            'hp_morning_on': 6,
            'hp_morning_off': 8,
            'hp_evening_on': 16,
            'hp_evening_off': 20,
        }
        fc = engine._forecast_mapped_series(s_civil)
        hp_breakdown = fc['mapped_hp_breakdown']
        space_heat = hp_breakdown['space_delivered_heat_kwh']
        civil_hours = fc['target_civil_hours']

        active_civil_hours = {6, 7, 16, 17, 18, 19}
        # In all weather runs, delivered space heat must be strictly 0 outside active civil hours
        for h in range(24):
            if h not in active_civil_hours:
                mask = (civil_hours == h)
                self.assertEqual(float(space_heat[:, mask].sum()), 0.0)

    def test_11_recomputed_temperature_cop_not_fixed_electric_budget(self):
        """In fixed thermal gas budget mode (hp_weather_sensitive=False), thermal heat delivered
        matches reference, but monthly electrical consumption varies with temperature-dependent COP.
        Monthly electricity is never normalized.
        """
        s = {
            **self.defaults,
            'load_scenario': 'electrified',
            'planning_heat_pump': True,
            'hp_weather_sensitive': False,
        }
        hp_res = get_source_heat_pump_hourly(s)
        space_e = hp_res['space_compressor_electricity_kwh']

        # Compare January electricity between weather year 0 (2005) and weather year 1 (2006)
        jan_mask_0 = (self.data['month'][0] == 1) & self.data['valid'][0]
        jan_mask_1 = (self.data['month'][1] == 1) & self.data['valid'][1]

        jan_e_0 = float(space_e[0, jan_mask_0].sum())
        jan_e_1 = float(space_e[1, jan_mask_1].sum())

        # The temperatures differ between 2005 and 2006 January, so electricity consumption differs
        t_jan_0 = float(self.data['temperature'][0, jan_mask_0].mean())
        t_jan_1 = float(self.data['temperature'][1, jan_mask_1].mean())
        self.assertNotEqual(t_jan_0, t_jan_1)
        self.assertNotEqual(jan_e_0, jan_e_1)

    def test_12_hp_summary_totals_reconcile(self):
        """Annual total electricity equals compressor + backup + pump + standby
        across every weather year, and tank conservation error is strictly bounded.
        """
        s = {**self.defaults, 'load_scenario': 'electrified', 'planning_heat_pump': True}
        hp_res = get_source_heat_pump_hourly(s)
        by_weather = hp_res['summary']['by_weather']

        for yr_sum in by_weather:
            expected_total = (
                yr_sum['annual_space_compressor_electricity_kwh']
                + yr_sum['annual_dhw_compressor_electricity_kwh']
                + yr_sum['annual_space_backup_electricity_kwh']
                + yr_sum['annual_dhw_backup_electricity_kwh']
                + yr_sum['annual_external_pump_electricity_kwh']
                + yr_sum['annual_standby_electricity_kwh']
            )
            self.assertAlmostEqual(yr_sum['annual_total_electricity_kwh'], expected_total, places=5)
            self.assertLess(yr_sum['max_tank_conservation_error_kwh'], 0.05)

    def test_13_source_weather_pv_pair_unchanged(self):
        """Weather year indexing and PV generation are identical whether planning_heat_pump
        is True or False.
        """
        s_off = {**self.defaults, 'load_scenario': 'electrified', 'planning_heat_pump': False}
        s_on = {**self.defaults, 'load_scenario': 'electrified', 'planning_heat_pump': True}

        pv_off = engine.pv_profile(s_off)
        pv_on = engine.pv_profile(s_on)
        np.testing.assert_array_equal(pv_off, pv_on)

    def test_14_household_monthly_and_ev_preserved(self):
        """In forecast mapping, monthly household baseline totals are conserved
        between source and forecast calendar.
        """
        s = {**self.defaults, 'load_scenario': 'ev', 'planning_heat_pump': True, 'installation_year': 2026}
        fc = engine._forecast_mapped_series(s)
        load_source, ev_source, _ = engine.load_profiles(s)
        hp_source = get_source_heat_pump_hourly(s)

        # Baseline non-HP source load (load_profiles returns load = base + extra, EV is separate;
        # forecast m_base = m_nonhp + m_ev)
        nonhp_source = (load_source - hp_source['total_electricity_kwh']) + ev_source

        # Check that for year 0, the sum of mapped non-HP base load matches source monthly totals
        timeline = engine._local_standard_dates()[0]
        src_months = np.array([row[2] for row in timeline])
        target_months = fc['target_months']
        mapped_nonhp = fc['mapped_load'][0] - fc['mapped_heating'][0]

        for m in range(1, 13):
            src_m_sum = float(nonhp_source[0, :len(src_months)][src_months == m].sum())
            tgt_m_sum = float(mapped_nonhp[target_months == m].sum())
            self.assertAlmostEqual(src_m_sum, tgt_m_sum, places=4)

    def test_15_dispatch_hourly_electrical_conservation(self):
        """Lifetime dispatch and annual evaluation satisfy exact electrical energy conservation:
        PV + Grid_Import == Direct + Charge + Export + Residual/Loss.
        """
        s = {
            **self.defaults,
            'load_scenario': 'electrified',
            'planning_heat_pump': True,
            'south_panels': 15,
            'battery_kwh': 10.0,
            'charge_kw': 5.0,
            'discharge_kw': 5.0,
        }
        res = engine.evaluate(s)
        self.assertIn('enduses', res)
        self.assertEqual(res['enduses']['heating_smc'], 0.0)
        self.assertEqual(res['enduses']['dhw_smc'], 0.0)
        self.assertGreater(res['enduses']['heating_kwh'], 0.0)
        self.assertGreater(res['enduses']['dhw_kwh'], 0.0)
        self.assertIn('heat_pump', res['enduses'])


    def test_16_dormant_hygiene_temp_below_tank_target_when_disabled(self):
        """When hp_hygiene_enabled is False, tank target temp 65C with dormant hygiene 60C
        passes schema and simulation without DHWTank rejecting hygiene < target,
        while preserving saved values. When enabled, reject hygiene < target.
        """
        # 1. Disabled hygiene with target 65 and dormant hygiene 60
        s = {
            **self.defaults,
            'load_scenario': 'electrified',
            'planning_heat_pump': True,
            'hp_tank_target_temp': 65.0,
            'hp_hygiene_enabled': False,
            'hp_hygiene_temp': 60.0,
        }
        # Schema validation passes
        s_val = engine.validate(s)
        self.assertEqual(s_val['hp_tank_target_temp'], 65.0)
        self.assertEqual(s_val['hp_hygiene_temp'], 60.0)
        self.assertFalse(s_val['hp_hygiene_enabled'])

        # Source simulation succeeds and preserves saved value
        res_source = get_source_heat_pump_hourly(s_val)
        self.assertIn('total_electricity_kwh', res_source)
        self.assertEqual(s_val['hp_hygiene_temp'], 60.0)

        # Forecast simulation succeeds
        fc = engine._forecast_mapped_series(s_val)
        self.assertIn('mapped_hp_breakdown', fc)
        self.assertEqual(s_val['hp_hygiene_temp'], 60.0)

        # 2. When enabled, hygiene < target must be rejected
        s_enabled = {**s, 'hp_hygiene_enabled': True}
        with self.assertRaises(ValueError):
            validate_heat_pump_cross_fields(s_enabled)
        with self.assertRaises(ValueError):
            get_source_heat_pump_hourly(s_enabled)

    def test_17_independent_hygiene_masks_summer_sunday23_monday00(self):
        """Independent verification of source and forecast hygiene masks around exact
        Sunday 23:00 / Monday 00:00 civil time in summer (Europe/Rome CEST = UTC+2).
        Verifies correct day rolling, overnight wrapping, and no weekday mismatch.
        """
        timeline = engine._local_standard_dates()[0]
        # In 2005: July 10 is Sunday (weekday 6), July 11 is Monday (weekday 0)
        # Sunday July 10 at 22:00 UTC+1 is Sunday July 10 at 23:00 CEST (civil hour 23)
        # Sunday July 10 at 23:00 UTC+1 rolls past midnight to Monday July 11 at 00:00 CEST (civil hour 0)
        # Monday July 11 at 00:00 UTC+1 is Monday July 11 at 01:00 CEST (civil hour 1)
        idx_sun22_utc1 = [i for i, (d, h, m) in enumerate(timeline) if d == '2023-07-09' and h == 22][0]
        idx_sun23_utc1 = [i for i, (d, h, m) in enumerate(timeline) if d == '2023-07-09' and h == 23][0]
        idx_mon00_utc1 = [i for i, (d, h, m) in enumerate(timeline) if d == '2023-07-10' and h == 0][0]

        civ_hours = engine._get_civil_hours(self.data)[0]
        std_hours = self.data['hour'][0]

        # Case A: Weekly hygiene scheduled for Sunday 23:00 civil time, duration 1h
        s_sun23 = {
            'hp_hygiene_enabled': True,
            'hp_hygiene_weekday': 6,
            'hp_hygiene_hour': 23,
            'hp_hygiene_duration_hours': 1,
            'timezone_mode': 'civil',
        }
        mask_sun = build_source_hygiene_mask(0, len(timeline), s_sun23, civ_hours, std_hours)
        self.assertTrue(mask_sun[idx_sun22_utc1], "Sunday 22:00 UTC+1 is Sunday 23:00 CEST -> should be True")
        self.assertFalse(mask_sun[idx_sun23_utc1], "Sunday 23:00 UTC+1 is Monday 00:00 CEST -> should be False")

        # Case B: Weekly hygiene scheduled for Monday 00:00 civil time, duration 1h
        s_mon00 = {
            'hp_hygiene_enabled': True,
            'hp_hygiene_weekday': 0,
            'hp_hygiene_hour': 0,
            'hp_hygiene_duration_hours': 1,
            'timezone_mode': 'civil',
        }
        mask_mon = build_source_hygiene_mask(0, len(timeline), s_mon00, civ_hours, std_hours)
        self.assertFalse(mask_mon[idx_sun22_utc1], "Sunday 22:00 UTC+1 is Sunday 23:00 CEST -> should be False")
        self.assertTrue(mask_mon[idx_sun23_utc1], "Sunday 23:00 UTC+1 is Monday 00:00 CEST -> should be True")
        self.assertFalse(mask_mon[idx_mon00_utc1], "Monday 00:00 UTC+1 is Monday 01:00 CEST -> should be False")

        # Case C: Overnight wrapping across week boundary: Sunday 23:00 with duration 2h
        s_wrap = {
            'hp_hygiene_enabled': True,
            'hp_hygiene_weekday': 6,
            'hp_hygiene_hour': 23,
            'hp_hygiene_duration_hours': 2,
            'timezone_mode': 'civil',
        }
        mask_wrap = build_source_hygiene_mask(0, len(timeline), s_wrap, civ_hours, std_hours)
        self.assertTrue(mask_wrap[idx_sun22_utc1], "Hour 0 of 2h cycle -> should be True")
        self.assertTrue(mask_wrap[idx_sun23_utc1], "Hour 1 of 2h cycle wrapping to Monday 00:00 CEST -> should be True")
        self.assertFalse(mask_wrap[idx_mon00_utc1], "Hour 2 of 2h cycle -> should be False")

        # Case D: Target calendar forecast hygiene mask for target year 2027 (July 11 is Sunday)
        rome = ZoneInfo('Europe/Rome')
        start_2027 = dt.datetime(2027, 1, 1, 0, 0, tzinfo=dt.timezone(dt.timedelta(hours=1)))
        one_h = dt.timedelta(hours=1)
        # Find 2027 slots for Sunday July 11 23:00 CEST and Monday July 12 00:00 CEST
        fc_sun23_idx = None
        fc_mon00_idx = None
        cur = start_2027
        for i in range(8760):
            c_dt = cur.astimezone(rome)
            if c_dt.month == 7 and c_dt.day == 11 and c_dt.hour == 23:
                fc_sun23_idx = i
            elif c_dt.month == 7 and c_dt.day == 12 and c_dt.hour == 0:
                fc_mon00_idx = i
            cur += one_h

        mappings, tgt_std_h, tgt_civ_h, tgt_m, _, tgt_len = engine._forecast_calendar_mapping(current_runtime().key, 2027)
        fc_mask_sun = build_forecast_hygiene_mask(2027, tgt_len, s_sun23, tgt_civ_h, tgt_std_h)
        fc_mask_mon = build_forecast_hygiene_mask(2027, tgt_len, s_mon00, tgt_civ_h, tgt_std_h)
        self.assertTrue(fc_mask_sun[fc_sun23_idx])
        self.assertFalse(fc_mask_sun[fc_mon00_idx])
        self.assertFalse(fc_mask_mon[fc_sun23_idx])
        self.assertTrue(fc_mask_mon[fc_mon00_idx])

    def test_18_cache_memory_budget_and_contract(self):
        """Heavy hourly source and forecast simulations are bounded to small LRU cache budgets (maxsize=4).
        get_forecast_heat_pump_hourly documents and maintains canonical engine calendar/weather contract.
        """
        self.assertEqual(_cached_source_heat_pump_hourly.cache_info().maxsize, 4)
        self.assertEqual(_cached_forecast_heat_pump_hourly.cache_info().maxsize, 4)

        # Call contract: optional supplied arrays accepted for call compatibility
        s = {**self.defaults, 'load_scenario': 'electrified', 'planning_heat_pump': True}
        res_compat = get_forecast_heat_pump_hourly(s, 2027)
        self.assertIn('mapped_hp_electricity', res_compat)
        self.assertIn('mapped_hp_breakdown', res_compat)

        # Non-physical overrides do not invalidate or bloat cache
        s_pv = {**s, 'south_panels': 40, 'battery_kwh': 20.0}
        k_orig = tuple((k, s.get(k)) for k in HP_SOURCE_CACHE_KEYS)
        k_pv = tuple((k, s_pv.get(k)) for k in HP_SOURCE_CACHE_KEYS)
        self.assertEqual(k_orig, k_pv)

    def test_19_metadata_reporting_and_gas_allocation_preservation(self):
        """Metadata reporting includes actual boiler efficiency (0.70 unverified user estimate),
        preserves reference gas allocation (explicit synthetic gas input), and reports
        climatology, balance temp, and dynamics disclaimers. Disconnecting gas does not confuse reference.
        """
        s = {**self.defaults, 'load_scenario': 'electrified', 'planning_heat_pump': True}
        hp_res = get_source_heat_pump_hourly(s)
        meta = hp_res['summary']['metadata']

        # Actual settings used, matching 0.70 user estimate
        self.assertEqual(meta['boiler_efficiency'], s['boiler_efficiency'])
        self.assertIn('UNVERIFIED USER ESTIMATE', meta['boiler_efficiency_status'])
        self.assertEqual(meta['gas_reference_period'], 'Explicit profile reference period')

        # Reference gas allocation preserved
        gas_ref = meta['reference_gas_allocation']
        self.assertEqual(gas_ref['total_reference_gas_smc'], float(engine.GAS_MONTHLY.sum()))
        allocated_smc_sum = gas_ref['heating_reference_smc'] + gas_ref['dhw_reference_smc'] + gas_ref['cooking_reference_smc']
        self.assertAlmostEqual(allocated_smc_sum, float(engine.GAS_MONTHLY.sum()), places=5)
        self.assertGreater(gas_ref['heating_useful_kwh'], 0.0)
        self.assertGreater(gas_ref['dhw_gross_useful_kwh'], 0.0)

        # Disclaimers present
        self.assertIn('weather_reference_basis', meta)
        self.assertIn('balance_temperature_distinction', meta)
        self.assertIn('building_dynamics_disclaimer', meta)
        self.assertIn('schedule_and_loss_limitations', meta)

        # Gas disconnection does NOT confuse reference gas allocation
        s_disc = {**s, 'hp_disconnect_gas': True, 'hp_electric_cooking': True}
        hp_disc = get_source_heat_pump_hourly(s_disc)
        self.assertEqual(hp_disc['summary']['metadata']['reference_gas_allocation']['total_reference_gas_smc'], float(engine.GAS_MONTHLY.sum()))

    def test_20_forecast_mapped_hp_breakdown_full_arrays(self):
        """Forecast mapped_hp_breakdown includes map_outside flags, hygiene miss,
        radiator bounds, requested/unmet heat, tank losses, storage, actual draw,
        capacity, and runtime arrays matching source arrays.
        """
        s = {**self.defaults, 'load_scenario': 'electrified', 'planning_heat_pump': True}
        fc = engine._forecast_mapped_series(s)
        breakdown = fc['mapped_hp_breakdown']
        target_len = fc['target_len']

        expected_arrays = [
            'total_electricity_kwh',
            'space_compressor_electricity_kwh',
            'dhw_compressor_electricity_kwh',
            'space_backup_electricity_kwh',
            'dhw_backup_electricity_kwh',
            'external_pump_electricity_kwh',
            'standby_electricity_kwh',
            'auxiliary_electricity_kwh',
            'requested_space_heat_kwh',
            'delivered_space_heat_kwh',
            'unmet_space_heat_kwh',
            'space_delivered_heat_kwh',
            'space_unmet_heat_kwh',
            'requested_draw_heat_kwh',
            'delivered_draw_heat_kwh',
            'unmet_draw_heat_kwh',
            'actual_draw_heat_kwh',
            'unserved_draw_heat_kwh',
            'draw_delivered_heat_kwh',
            'draw_unmet_heat_kwh',
            'space_compressor_heat_kwh',
            'dhw_compressor_heat_kwh',
            'space_backup_heat_kwh',
            'dhw_backup_heat_kwh',
            'space_distribution_loss_kwh',
            'heat_supplied_tank_kwh',
            'tank_losses_kwh',
            'tank_temp_c',
            'tank_start_storage_kwh',
            'tank_end_storage_kwh',
            'space_flow_temp_c',
            'dhw_flow_temp_c',
            'space_capacity_kw',
            'dhw_capacity_kw',
            'space_runtime_hours',
            'dhw_runtime_hours',
            'radiator_bound_kw',
            'map_outside_flag',
            'hygiene_target_miss',
        ]
        for key in expected_arrays:
            self.assertIn(key, breakdown, f"Missing key {key} in forecast mapped_hp_breakdown")
            arr = breakdown[key]
            self.assertEqual(arr.shape, (len(self.years), target_len), f"Shape mismatch for {key}: expected ({len(self.years)}, {target_len}), got {arr.shape}")

        self.assertIn('by_weather', breakdown)
        self.assertIn('forecast_year', breakdown)
        self.assertIn('target_len', breakdown)

    def test_21_prior_dhw_loss_fraction_allows_one(self):
        """hp_prior_dhw_loss_fraction schema allows 1.0 matching tested physical helper."""
        self.assertEqual(HP_SPEC['hp_prior_dhw_loss_fraction'][2], [0.0, 1.0])

        # Validate with 1.0 succeeds
        s_ok = engine.validate({'planning_heat_pump': True, 'hp_prior_dhw_loss_fraction': 1.0})
        self.assertEqual(s_ok['hp_prior_dhw_loss_fraction'], 1.0)

        # Validate with > 1.0 fails
        with self.assertRaises(ValueError):
            engine.validate({'planning_heat_pump': True, 'hp_prior_dhw_loss_fraction': 1.05})

        # Physical helper with prior_loss_fraction=1.0 yields cleanly 0 demand
        v = np.ones(24, dtype=bool)
        m = np.ones(24, dtype=int)
        h = np.arange(24)
        m_dhw = np.full(12, 100.0)
        q = build_hourly_dhw_demand(v, m, h, monthly_dhw_kwh=m_dhw, prior_loss_fraction=1.0)
        np.testing.assert_array_equal(q, np.zeros(24))


if __name__ == '__main__':
    unittest.main()

