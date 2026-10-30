"""Comprehensive tests for schedule-aware space heating in engine and assumptions."""
import math
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo
import numpy as np

import feasibility.engine as e
from feasibility.assumptions import SPEC, defaults, records
from feasibility.schedule import freeze, DEFAULT_AC_HEATING_WINDOWS


def isolate_space_heating(s, d=None):
    """Isolate pure hourly space-heating electricity by cancelling baseline, DHW, and cooking.

    Since baseline load, DHW, cooking, and EV do not depend on heating SCOP, the difference
    between two evaluations with different SCOP values isolates space heating exactly:
        heating_profile = (load(scop1) - load(scop2)) / (1/scop1 - 1/scop2) * (1/scop1)
    """
    s1 = dict(s, load_scenario='electrified', scop=4.0)
    s2 = dict(s, load_scenario='electrified', scop=5.0)
    l1, _, _ = e.load_profiles(s1)
    l2, _, _ = e.load_profiles(s2)
    delta_inv_scop = 1.0 / 4.0 - 1.0 / 5.0
    # heating energy for s with its actual scop:
    unit_heating = (l1 - l2) / delta_inv_scop
    return unit_heating / s['scop']


class HeatingScheduleTests(unittest.TestCase):
    def setUp(self):
        e._load_cache.cache_clear()
        e._chart_cache.cache_clear()
        e._dayhour_trace.cache_clear()

    def tearDown(self):
        e._load_cache.cache_clear()
        e._chart_cache.cache_clear()
        e._dayhour_trace.cache_clear()

    def test_default_settings_and_records_provenance(self):
        defs = defaults()
        self.assertEqual(defs['heating_schedule_mode'], 'scheduled')
        self.assertEqual(defs['ac_heating_windows'], [
            {'id': 'morning', 'start_minute': 360, 'end_minute': 480},
            {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
        ])

        recs = {r['name']: r for r in records()}
        for k in ['heating_schedule_mode', 'ac_heating_windows']:
            self.assertIn(k, recs)
            self.assertEqual(recs[k]['status'], 'proposed')
            self.assertIsNone(recs[k]['date_accessed'])
            self.assertEqual(recs[k]['source'],'Neutral modeling example; profile-specific evidence required')

        # balance temperature confidence clarifies distinction from 18C thermostat
        self.assertIn('Outdoor degree-hour', recs['heating_balance_temp']['description'])
        self.assertIn('indoor comfort', recs['heating_balance_temp']['description'])

    def test_validation_bounds_integers_and_schedule_rules(self):
        # Invalid types and bounds
        for bad in [
            {'heating_on_hour': -1},
            {'heating_on_hour': 24},
            {'heating_off_hour': 24},
            {'heating_evening_on_hour': -1},
            {'heating_evening_off_hour': 24},
            {'heating_on_hour': 6.5},
            {'heating_on_hour': True},
            {'heating_off_hour': False},
            {'heating_schedule_mode': 'invalid_mode'},
            {'heating_on_hour': float('nan')},
            {'heating_off_hour': float('inf')},
            {'ac_heating_windows': 'not_a_list'},
            {'ac_heating_windows': [{'id': 'w', 'start_minute': -1, 'end_minute': 100}]},
            {'ac_heating_windows': [{'id': 'w', 'start_minute': 100, 'end_minute': 1441}]},
            {'ac_heating_windows': [{'id': 'w', 'start_minute': True, 'end_minute': 100}]},
            {'ac_heating_windows': [{'id': 'w', 'start_minute': 10.5, 'end_minute': 100}]},
        ]:
            with self.assertRaises(ValueError, msg=f"Should fail for {bad}"):
                e.validate(bad)

        # In scheduled mode, empty or inactive windows are tolerated by engine.validate for UI draft editing
        s_empty = e.validate({'heating_schedule_mode': 'scheduled', 'ac_heating_windows': []})
        self.assertEqual(s_empty['ac_heating_windows'], [])

        s_inactive = e.validate({
            'heating_schedule_mode': 'scheduled',
            'ac_heating_windows': [
                {'id': 'morning', 'start_minute': 360, 'end_minute': 360},
                {'id': 'evening', 'start_minute': 960, 'end_minute': 960},
            ],
        })
        self.assertEqual(len(s_inactive['ac_heating_windows']), 2)

        # But calculation-time allocate raises ValueError with canonical message
        with self.assertRaises(ValueError) as ctx:
            e.evaluate({**s_empty, 'load_scenario': 'electrified', 'horizon_years': 1})
        self.assertEqual(str(ctx.exception), "Add at least one operating window to allocate the requested AC heating demand.")

        with self.assertRaises(ValueError) as ctx:
            e.evaluate({**s_inactive, 'load_scenario': 'electrified', 'horizon_years': 1})
        self.assertEqual(str(ctx.exception), "Add at least one operating window to allocate the requested AC heating demand.")

        # Single active window: morning disabled, evening active -> valid
        s_single1 = e.validate({
            'heating_schedule_mode': 'scheduled',
            'heating_on_hour': 6,
            'heating_off_hour': 6,
            'heating_evening_on_hour': 16,
            'heating_evening_off_hour': 20,
        })
        self.assertEqual(s_single1['ac_heating_windows'], [
            {'id': 'morning', 'start_minute': 360, 'end_minute': 360},
            {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
        ])

        # Single active window: evening disabled, morning active -> valid
        s_single2 = e.validate({
            'heating_schedule_mode': 'scheduled',
            'heating_on_hour': 6,
            'heating_off_hour': 8,
            'heating_evening_on_hour': 16,
            'heating_evening_off_hour': 16,
        })
        self.assertEqual(s_single2['ac_heating_windows'], [
            {'id': 'morning', 'start_minute': 360, 'end_minute': 480},
            {'id': 'evening', 'start_minute': 960, 'end_minute': 960},
        ])

        # In continuous mode, equal hours on one or both windows are tolerated without rejection in validate or evaluate
        s_cont = e.validate({
            'heating_schedule_mode': 'continuous',
            'ac_heating_windows': [],
        })
        self.assertEqual(s_cont['heating_schedule_mode'], 'continuous')
        ev_cont = e.evaluate({**s_cont, 'load_scenario': 'electrified', 'horizon_years': 1})
        self.assertIn('metrics', ev_cont)

    def test_window_hours_helper_logic(self):
        # Disjoint windows
        w1 = e._window_hours(6, 8)
        w2 = e._window_hours(16, 20)
        self.assertEqual(w1, {6, 7})
        self.assertEqual(w2, {16, 17, 18, 19})
        self.assertEqual(w1 | w2, {6, 7, 16, 17, 18, 19})

        # Overlapping windows union without double counting
        w_overlap1 = e._window_hours(6, 18)
        w_overlap2 = e._window_hours(16, 22)
        self.assertEqual(w_overlap1 | w_overlap2, set(range(6, 22)))

        # Overnight wrapping window (e.g. 22 to 6)
        w_night = e._window_hours(22, 6)
        self.assertEqual(w_night, {22, 23, 0, 1, 2, 3, 4, 5})

        # Single-hour window
        self.assertEqual(e._window_hours(23, 0), {23})

        # Equal hours disables window
        self.assertEqual(e._window_hours(10, 10), set())

    def test_zero_heating_outside_operating_window(self):
        d = e.data()
        valid = d['valid']
        h = d['hour']

        # Test standard mode default: morning 6..8, evening 16..20
        s = e.validate({
            'load_scenario': 'electrified',
            'timezone_mode': 'standard',
            'heating_schedule_mode': 'scheduled',
            'heating_on_hour': 6,
            'heating_off_hour': 8,
            'heating_evening_on_hour': 16,
            'heating_evening_off_hour': 20,
        })
        heating = isolate_space_heating(s)

        permitted_hours = {6, 7, 16, 17, 18, 19}
        for yi in range(len(d['years'])):
            # Off-hour mask
            off_hours = valid[yi] & ~np.isin(h[yi], list(permitted_hours))
            self.assertEqual(np.max(heating[yi, off_hours]), 0.0,
                             msg=f"Found nonzero heating in off-hours for year {d['years'][yi]}")
            # Invalid padded slots (if any in non-leap years)
            if np.any(~valid[yi]):
                self.assertEqual(np.max(heating[yi, ~valid[yi]]), 0.0)

            # Within active hours, heating is non-negative
            active_slots = valid[yi] & np.isin(h[yi], list(permitted_hours))
            self.assertTrue(np.all(heating[yi, active_slots] >= 0.0))

    def test_ensemble_annual_heating_budget_conservation(self):
        d = e.data()
        valid = d['valid']
        m = d['month']

        s_sched = e.validate({'load_scenario': 'electrified', 'heating_schedule_mode': 'scheduled'})
        s_cont = e.validate({'load_scenario': 'electrified', 'heating_schedule_mode': 'continuous'})

        _, _, end_sched = e.load_profiles(s_sched)
        _, _, end_cont = e.load_profiles(s_cont)

        # Total modeled heating budget in enduses is identical
        self.assertAlmostEqual(end_sched['heating_kwh'], end_cont['heating_kwh'], places=10)
        self.assertAlmostEqual(end_sched['heating_smc'], end_cont['heating_smc'], places=10)

        heating_sched = isolate_space_heating(s_sched)
        heating_cont = isolate_space_heating(s_cont)

        # Monthly gas conversion values:
        summer = np.minimum(e.GAS_MONTHLY, s_sched['summer_gas_base'])
        expected_monthly_heat = (e.GAS_MONTHLY - summer) * s_sched['gas_pcs'] * s_sched['boiler_efficiency'] / s_sched['scop']

        # Ensemble mean annual heating electricity strictly matches expected budget
        expected_annual_heat = float(expected_monthly_heat.sum())
        self.assertAlmostEqual(float(heating_sched.sum(axis=1).mean()), expected_annual_heat, places=8)
        self.assertAlmostEqual(float(heating_cont.sum(axis=1).mean()), expected_annual_heat, places=8)

        # Weather variability: cold years demand more electricity than warm years
        annual_sched = heating_sched.sum(axis=1)
        self.assertGreater(float(annual_sched.max() - annual_sched.min()), 100.0)
        self.assertGreater(float(annual_sched.std()), 50.0)

        # Zero heating during zero-budget summer months
        for mm in [7, 8]:
            self.assertEqual(float(heating_sched[valid & (m == mm)].sum()), 0.0)

        # Gating integrity: heating is non-negative everywhere
        self.assertTrue(np.all(heating_sched[valid] >= 0.0))

    def test_continuous_mode_conserves_ensemble_budget(self):
        d = e.data()
        s_cont = e.validate({'load_scenario': 'electrified', 'heating_schedule_mode': 'continuous'})
        civil_hours = e._get_civil_hours(d)
        prof, diag = e._allocate_space_heating(
            s_cont, d['temperature'], d['month'], d['hour'], d['valid'], civil_hours, d['years'], return_diagnostics=True
        )

        summer = np.minimum(e.GAS_MONTHLY, s_cont['summer_gas_base'])
        expected_monthly_heat = (e.GAS_MONTHLY - summer) * s_cont['gas_pcs'] * s_cont['boiler_efficiency'] / s_cont['scop']

        # Continuous mode permits all 24 clock hours when valid
        self.assertEqual(diag['nominal_daily_union_duration_hours'], 24.0)
        # Ensemble mean annual electricity is conserved
        self.assertAlmostEqual(float(prof.sum(axis=1).mean()), float(expected_monthly_heat.sum()), places=8)
        # Weather variability across years
        self.assertGreater(float(prof.sum(axis=1).max() - prof.sum(axis=1).min()), 100.0)
        # Gating integrity: heating is non-negative everywhere
        self.assertTrue(np.all(prof[d['valid']] >= 0.0))
        # Zero heating during zero-budget summer months
        for mm in [7, 8]:
            self.assertEqual(float(prof[d['valid'] & (d['month'] == mm)].sum()), 0.0)

    def test_overnight_window_and_single_window_profiles(self):
        d = e.data()
        valid = d['valid']
        h = d['hour']

        # Overnight window: 22 to 6 (permitted: 22, 23, 0..5), evening disabled (16 to 16)
        s_night = e.validate({
            'load_scenario': 'electrified',
            'heating_schedule_mode': 'scheduled',
            'timezone_mode': 'standard',
            'heating_on_hour': 22,
            'heating_off_hour': 6,
            'heating_evening_on_hour': 16,
            'heating_evening_off_hour': 16,
        })
        heat_night = isolate_space_heating(s_night)
        night_hours = {22, 23, 0, 1, 2, 3, 4, 5}

        for yi in range(len(d['years'])):
            day_slots = valid[yi] & ~np.isin(h[yi], list(night_hours))
            self.assertEqual(np.max(heat_night[yi, day_slots]), 0.0)
            night_slots = valid[yi] & np.isin(h[yi], list(night_hours))
            self.assertTrue(np.all(heat_night[yi, night_slots] >= 0.0))

    def test_zero_heating_degrees_fallback_within_active_slots_only(self):
        # Synthetic test where temperature is 30C everywhere (zero positive heating degrees)
        fake_data = dict(e.data())
        fake_data['temperature'] = np.full_like(e.data()['temperature'], 30.0)

        with patch('feasibility.engine.data', return_value=fake_data):
            e._load_cache.cache_clear()
            try:
                s = e.validate({
                    'load_scenario': 'electrified',
                    'heating_schedule_mode': 'scheduled',
                    'timezone_mode': 'standard',
                    'heating_on_hour': 6,
                    'heating_off_hour': 8,
                    'heating_evening_on_hour': 16,
                    'heating_evening_off_hour': 20,
                })
                heat = isolate_space_heating(s)
                d = fake_data
                valid = d['valid']
                h = d['hour']
                m = d['month']

                permitted = {6, 7, 16, 17, 18, 19}
                for yi in range(len(d['years'])):
                    # Zero in off-hours
                    off = valid[yi] & ~np.isin(h[yi], list(permitted))
                    self.assertEqual(np.max(heat[yi, off]), 0.0)

                # Zero mean HDH yields zero heat across all slots (no duration fallback)
                self.assertEqual(float(np.max(heat)), 0.0)
            finally:
                e._load_cache.cache_clear()

    def test_no_active_slots_with_positive_budget_raises_error(self):
        fake_data = dict(e.data())
        # Suppose a synthetic test has valid=False during all operating hours
        fake_valid = np.copy(e.data()['valid'])
        fake_h = e.data()['hour']
        permitted = {6, 7, 16, 17, 18, 19}
        fake_valid[0, np.isin(fake_h[0], list(permitted))] = False
        fake_data['valid'] = fake_valid

        with patch('feasibility.engine.data', return_value=fake_data):
            e._load_cache.cache_clear()
            try:
                s = e.validate({
                    'load_scenario': 'electrified',
                    'heating_schedule_mode': 'scheduled',
                    'timezone_mode': 'standard',
                    'heating_on_hour': 6,
                    'heating_off_hour': 8,
                    'heating_evening_on_hour': 16,
                    'heating_evening_off_hour': 20,
                })
                with self.assertRaises(ValueError) as ctx:
                    e.load_profiles(s)
                self.assertIn('No active valid heating hours', str(ctx.exception))
            finally:
                e._load_cache.cache_clear()

    def test_clock_convention_civil_vs_standard(self):
        d = e.data()
        valid = d['valid']
        civil_h = e._civil_hours()
        std_h = d['hour']

        # Schedule 6..8 morning and 16..20 evening
        s_std = e.validate({'load_scenario': 'electrified', 'timezone_mode': 'standard', 'heating_schedule_mode': 'scheduled'})
        s_civ = e.validate({'load_scenario': 'electrified', 'timezone_mode': 'civil', 'heating_schedule_mode': 'scheduled'})

        heat_std = isolate_space_heating(s_std)
        heat_civ = isolate_space_heating(s_civ)

        permitted = {6, 7, 16, 17, 18, 19}

        for yi in range(len(d['years'])):
            # Standard mode heating occurs ONLY when standard hour is in permitted
            self.assertTrue(np.all(heat_std[yi, valid[yi] & ~np.isin(std_h[yi], list(permitted))] == 0.0))
            # Civil mode heating occurs ONLY when civil hour is in permitted
            self.assertTrue(np.all(heat_civ[yi, valid[yi] & ~np.isin(civil_h[yi], list(permitted))] == 0.0))

            # In winter (January), civil hour == standard hour, so active heating slots coincide exactly
            jan = valid[yi] & (d['month'][yi] == 1)
            np.testing.assert_array_equal(heat_std[yi, jan] > 0.0, heat_civ[yi, jan] > 0.0)
            np.testing.assert_allclose(
                heat_std[yi, jan] / heat_std[yi, jan].sum(),
                heat_civ[yi, jan] / heat_civ[yi, jan].sum(), atol=1e-12,
            )

            # In April (DST active CEST), civil hour == standard hour + 1.
            # So a 6..8 civil window occurs at standard hours 5..7 (shifted 1h earlier in standard time).
            apr = valid[yi] & (d['month'][yi] == 4)
            # Find slots where standard hour is 5 (which is civil hour 6):
            std5_slots = apr & (std_h[yi] == 5)
            # In civil mode, civil hour 6 is active!
            self.assertTrue(np.all(heat_civ[yi, std5_slots] > 0.0))
            # In standard mode, standard hour 5 is NOT in 6..8, so it is 0!
            self.assertTrue(np.all(heat_std[yi, std5_slots] == 0.0))

    def test_cache_keys_and_propagation_to_charts_and_evaluate(self):
        # Adding heating settings to LOAD_KEYS ensures they invalidate load_cache, chart_cache, and evaluate
        s_base = e.validate({'heating_schedule_mode': 'scheduled', 'heating_on_hour': 6, 'heating_off_hour': 8})
        s_mod = e.validate({'heating_schedule_mode': 'scheduled', 'heating_on_hour': 7, 'heating_off_hour': 9})

        key_base = tuple((k, freeze(s_base[k])) for k in e.LOAD_KEYS)
        key_mod = tuple((k, freeze(s_mod[k])) for k in e.LOAD_KEYS)
        self.assertNotEqual(key_base, key_mod)

        # Keys are in ENERGY_KEYS and LOAD_KEYS
        for k in ['heating_schedule_mode', 'ac_heating_windows']:
            self.assertIn(k, e.ENERGY_KEYS)
            self.assertIn(k, e.LOAD_KEYS)

        # Changing schedule changes evaluate() energy metrics in electrified scenario
        ev_base = e.evaluate({**s_base, 'load_scenario': 'electrified', 'horizon_years': 1})
        ev_mod = e.evaluate({**s_mod, 'load_scenario': 'electrified', 'horizon_years': 1})

        # Self consumption or grid import will differ because timing of heating shifted relative to solar
        self.assertNotEqual(ev_base['energy']['self_consumption']['median'],
                            ev_mod['energy']['self_consumption']['median'])

    def test_forecast_2027_dst_transition_leak_free(self):
        s = e.validate({
            'load_scenario': 'electrified',
            'installation_year': 2026,  # target year 2027
            'timezone_mode': 'civil',
            'heating_schedule_mode': 'scheduled',
            'heating_on_hour': 6,
            'heating_off_hour': 8,
            'heating_evening_on_hour': 16,
            'heating_evening_off_hour': 20,
        })
        mapped = e._forecast_mapped_series(s)
        self.assertEqual(mapped['forecast_year'], 2027)
        self.assertEqual(mapped['target_len'], 8760)
        self.assertFalse(mapped['is_leap'])

        permitted_civil = {6, 7, 16, 17, 18, 19}
        target_civ = mapped['target_civil_hours']
        target_heat = mapped['mapped_heating']

        for yi in range(target_heat.shape[0]):
            # 1. Zero leaks outside permitted civil hours across the entire year
            off_mask = ~np.isin(target_civ, list(permitted_civil))
            self.assertEqual(float(np.max(target_heat[yi, off_mask])), 0.0,
                             msg=f"Year index {yi} leaked heating outside civil window!")

            # 2. Specifically test March 25-31 and October 25-31 (around DST transitions)
            target_months = mapped['target_months']
            mar_slots = (target_months == 3)
            off_mar = mar_slots & off_mask
            self.assertEqual(float(np.max(target_heat[yi, off_mar])), 0.0)

            oct_slots = (target_months == 10)
            off_oct = oct_slots & off_mask
            self.assertEqual(float(np.max(target_heat[yi, off_oct])), 0.0)

    def test_forecast_dynamic_year_leap_and_non_leap(self):
        # 2027 (non-leap)
        s_2026 = e.validate({'installation_year': 2026})
        m_2027 = e._forecast_mapped_series(s_2026)
        self.assertEqual(m_2027['forecast_year'], 2027)
        self.assertEqual(m_2027['target_len'], 8760)
        self.assertFalse(m_2027['is_leap'])

        # 2028 (leap year)
        s_2027 = e.validate({'installation_year': 2027})
        m_2028 = e._forecast_mapped_series(s_2027)
        self.assertEqual(m_2028['forecast_year'], 2028)
        self.assertEqual(m_2028['target_len'], 8784)
        self.assertTrue(m_2028['is_leap'])

        # Verify Feb 29 exists and has 24 hours
        feb_mask = (m_2028['target_months'] == 2)
        self.assertEqual(int(feb_mask.sum()), 29 * 24)

        # 2036 (leap year)
        s_2035 = e.validate({'installation_year': 2035})
        m_2036 = e._forecast_mapped_series(s_2035)
        self.assertEqual(m_2036['forecast_year'], 2036)
        self.assertEqual(m_2036['target_len'], 8784)
        self.assertTrue(m_2036['is_leap'])

    def test_forecast_budget_conservation(self):
        for inst_year in [2026, 2027]:
            s = e.validate({
                'load_scenario': 'electrified',
                'installation_year': inst_year,
                'heating_schedule_mode': 'scheduled',
            })
            mapped = e._forecast_mapped_series(s)
            summer = np.minimum(e.GAS_MONTHLY, s['summer_gas_base'])
            expected_monthly_heat = (e.GAS_MONTHLY - summer) * s['gas_pcs'] * s['boiler_efficiency'] / s['scop']

            target_heat = mapped['mapped_heating']
            target_months = mapped['target_months']

            # Ensemble mean annual heating strictly conserved on forecast calendar
            self.assertAlmostEqual(float(target_heat.sum(axis=1).mean()), float(expected_monthly_heat.sum()), places=8)
            # Weather variability across years
            self.assertGreater(float(target_heat.sum(axis=1).max() - target_heat.sum(axis=1).min()), 100.0)
            # Zero heating during zero-budget summer months
            for mm in [7, 8]:
                self.assertEqual(float(target_heat[:, target_months == mm].sum()), 0.0)

    def test_forecast_overnight_window(self):
        s = e.validate({
            'load_scenario': 'electrified',
            'installation_year': 2027,  # target leap year 2028
            'timezone_mode': 'civil',
            'heating_schedule_mode': 'scheduled',
            'heating_on_hour': 22,
            'heating_off_hour': 6,
            'heating_evening_on_hour': 16,
            'heating_evening_off_hour': 16,
        })
        mapped = e._forecast_mapped_series(s)
        target_heat = mapped['mapped_heating']
        target_civ = mapped['target_civil_hours']
        night_hours = {22, 23, 0, 1, 2, 3, 4, 5}

        for yi in range(target_heat.shape[0]):
            day_mask = ~np.isin(target_civ, list(night_hours))
            self.assertEqual(float(np.max(target_heat[yi, day_mask])), 0.0)
            night_mask = np.isin(target_civ, list(night_hours))
            self.assertTrue(np.all(target_heat[yi, night_mask] >= 0.0))

    def test_installation_year_cache_invalidation(self):
        s1 = e.validate({'installation_year': 2026})
        s2 = e.validate({'installation_year': 2027})
        self.assertIn('installation_year', e.ENERGY_KEYS)
        key1 = tuple((k, freeze(s1[k])) for k in e.ENERGY_KEYS)
        key2 = tuple((k, freeze(s2[k])) for k in e.ENERGY_KEYS)
        self.assertNotEqual(key1, key2)

        runs1 = e._forecast_runs(key1)
        runs2 = e._forecast_runs(key2)
        self.assertEqual(runs1['forecast_year'], 2027)
        self.assertEqual(runs1['target_len'], 8760)
        self.assertEqual(runs2['forecast_year'], 2028)
        self.assertEqual(runs2['target_len'], 8784)


if __name__ == '__main__':
    unittest.main()
