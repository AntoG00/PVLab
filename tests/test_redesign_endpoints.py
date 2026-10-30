"""Tests for redesign API endpoints: behavior-matrix and summary-sensitivity."""
import io
import json
import unittest
import numpy as np

from feasibility import assumptions, engine
import feasibility.server as server_module
try:
    from test_support import IsolatedDefaultsTestCase
except ImportError:
    from tests.test_support import IsolatedDefaultsTestCase


class RedesignEndpointsTests(IsolatedDefaultsTestCase):
    def test_dispatch_matrix_calculates_annual_and_seasonal_conservation(self):
        settings = {'south_panels': 14, 'battery_kwh': 5.0, 'load_scenario': 'baseline'}
        matrix = engine.dispatch_matrix(settings, mode='forecast_2027', scenario='baseline', representative='median')
        self.assertIn('grid_import_kwh', matrix)
        self.assertIn('matrix', matrix)
        self.assertIn('months', matrix['matrix'])
        self.assertIn('seasons', matrix['matrix'])
        self.assertEqual(len(matrix['matrix']['months']), 12)
        self.assertEqual(len(matrix['matrix']['months'][0]), 24)
        
        # Verify seasons
        seasons = matrix['matrix']['seasons']
        self.assertIn('winter', seasons)
        self.assertIn('summer', seasons)
        self.assertIn('spring', seasons)
        self.assertIn('autumn', seasons)
        self.assertIn('annual', seasons)
        self.assertEqual(len(seasons['summer']), 24)

        # Independent oracle derived from per-weather postdispatch traces cached by dispatch_matrix
        import calendar
        import datetime as dt

        s_oracle = engine._routing_settings(engine.validate({**engine.validate(settings), 'load_scenario': 'baseline'}))
        key = tuple((k, engine.freeze(s_oracle[k])) for k in engine.ENERGY_KEYS)
        price_ver = engine.price_fingerprint(s_oracle)
        cached_runs = engine._forecast_2027_runs(key, price_ver)

        year = cached_runs['forecast_year']
        days = 366 if calendar.isleap(year) else 365
        start_date = dt.date(year, 1, 1)
        months_per_day = [(start_date + dt.timedelta(days=d)).month for d in range(days)]

        factor = s_oracle['inverter_efficiency'] if (
            s_oracle.get('pv_dispatch_model') == 'physical'
            and s_oracle.get('pv_battery_coupling') == 'dc_coupled'
            and s_oracle.get('battery_kwh', 0) > 0
        ) else 1.0

        all_raw_traces = [t.reshape(days, 24, 8) for t in cached_runs['traces']]
        season_month_map = {
            'winter': [11, 0, 1],
            'spring': [2, 3, 4],
            'summer': [5, 6, 7],
            'autumn': [8, 9, 10],
            'annual': list(range(12))
        }
        metric_col_map = {
            'load': 1,
            'import': 2,
            'export': 3,
            'soc': 4,
            'direct': 5
        }

        # Verify seasonal envelopes and scalar metrics against independent per-weather oracle
        for season_name, hours in seasons.items():
            self.assertEqual(len(hours), 24)
            s_m_indices = season_month_map[season_name]
            s_days = [d for d in range(days) if (months_per_day[d] - 1) in s_m_indices]
            for h_idx, h_data in enumerate(hours):
                for metric in ('pv', 'load', 'import', 'export', 'soc', 'direct'):
                    self.assertIn(metric, h_data)
                    env_key = f"{metric}_envelope"
                    self.assertIn(env_key, h_data)
                    low, high = h_data[env_key]
                    self.assertLessEqual(low, high)

                    if metric == 'pv':
                        raw_w = [float((tr[s_days, h_idx, 0] - (1.0 - factor) * tr[s_days, h_idx, 6]).mean()) for tr in all_raw_traces]
                    else:
                        col = metric_col_map[metric]
                        raw_w = [float(tr[s_days, h_idx, col].mean()) for tr in all_raw_traces]

                    oracle_low = round(float(np.percentile(raw_w, 10)), 4)
                    oracle_high = round(float(np.percentile(raw_w, 90)), 4)
                    oracle_mean = round(float(np.mean(raw_w)), 4)

                    self.assertAlmostEqual(low, oracle_low, delta=1e-4)
                    self.assertAlmostEqual(high, oracle_high, delta=1e-4)
                    self.assertAlmostEqual(h_data[metric], oracle_mean, delta=1e-4)

                self.assertIn('statistic', h_data)
                self.assertEqual(h_data['weather_count'], engine.weather_period().count)

        # Verify annual energy flows and independent physical conservation identities
        self.assertIn('annual_flows', matrix)
        af = matrix['annual_flows']
        self.assertIn('weather_year', af)
        self.assertIn('initial_soc_kwh', af)
        self.assertIn('end_soc_kwh', af)
        self.assertIn('storage_delta_kwh', af)
        self.assertIn('clipped_kwh', af)
        self.assertIn('aggregation', af)
        self.assertIn('aggregation_note', af)

        pv = af['pv_generation_kwh']
        direct = af['direct_use_kwh']
        charge = af['battery_charge_kwh']
        discharge = af['battery_discharge_kwh']
        exp = af['grid_export_kwh']
        imp = af['grid_import_kwh']
        demand = af['load_demand_kwh']
        losses = af['storage_losses_kwh']
        delta = af['storage_delta_kwh']

        # 1. Solar generation conservation: PV = direct + charge + export
        self.assertAlmostEqual(pv, direct + charge + exp, delta=0.3)
        # 2. Demand conservation: Load = direct + discharge + grid_import
        self.assertAlmostEqual(demand, direct + discharge + imp, delta=0.3)
        # 3. Storage energy conservation: charge - discharge = storage_losses + delta_soc
        self.assertAlmostEqual(charge - discharge, losses + delta, delta=0.3)
        self.assertAlmostEqual(delta, af['end_soc_kwh'] - af['initial_soc_kwh'], places=1)

    def test_dispatch_matrix_no_storage_conservation(self):
        settings = {'south_panels': 14, 'battery_kwh': 0.0, 'load_scenario': 'baseline'}
        matrix = engine.dispatch_matrix(settings, mode='forecast_2027', scenario='baseline', representative='median')
        af = matrix['annual_flows']
        self.assertEqual(af['battery_charge_kwh'], 0.0)
        self.assertEqual(af['battery_discharge_kwh'], 0.0)
        self.assertEqual(af['storage_losses_kwh'], 0.0)
        self.assertEqual(af['storage_delta_kwh'], 0.0)
        self.assertEqual(af['initial_soc_kwh'], 0.0)
        self.assertEqual(af['end_soc_kwh'], 0.0)
        # No battery: PV = direct + export; Load = direct + import
        self.assertAlmostEqual(af['pv_generation_kwh'], af['direct_use_kwh'] + af['grid_export_kwh'], delta=0.2)
        self.assertAlmostEqual(af['load_demand_kwh'], af['direct_use_kwh'] + af['grid_import_kwh'], delta=0.2)

    def test_dispatch_matrix_reports_monthly_and_annual_inverter_clipping(self):
        # A one-hour physical check: clipping at the inverter cap happens before
        # the battery sees surplus, so the curtailed 1.4 kWh cannot be charged.
        one_hour, _ = engine.dispatch_year(
            np.array([8.0]), np.array([0.0]), 10.0, 10.0, 10.0,
            1.0, 6.6, 0.0, np.ones(1), np.ones(1, dtype=bool),
        )
        self.assertAlmostEqual(one_hour[1], 6.6, places=6)  # postclip PV
        self.assertAlmostEqual(one_hour[3], 6.6, places=6)  # maximum battery input from capped PV
        self.assertAlmostEqual(one_hour[9], 1.4, places=6)  # curtailed before charging

        # User's equipment class: 33 × 480 W = 15.84 kWp on a 6.6 kW AC inverter.
        settings = {
            'south_panels': 33, 'west_panels': 0, 'east_panels': 0,
            'panel_watts': 480, 'inverter_kw': 6.6, 'battery_kwh': 10.24,
            'load_scenario': 'electrified',
        }
        matrix = engine.dispatch_matrix(settings, mode='forecast_2027', representative='median')
        af = matrix['annual_flows']
        months = matrix['monthly_clipping']
        self.assertEqual(len(months), 12)
        self.assertAlmostEqual(af['pv_input_kwh'], af['pv_generation_kwh'] + af['clipped_kwh'], delta=0.2)
        self.assertGreater(af['clipped_kwh'], 0)
        self.assertGreater(af['clipping_hours'], 0)
        self.assertGreater(af['peak_curtailed_kw'], 0)
        self.assertAlmostEqual(sum(m['clipped_kwh'] for m in months), af['clipped_kwh'], delta=0.2)
        self.assertAlmostEqual(sum(m['pv_input_kwh'] for m in months), af['pv_input_kwh'], delta=0.2)
        self.assertAlmostEqual(sum(m['pv_delivered_kwh'] for m in months), af['pv_generation_kwh'], delta=0.2)
        self.assertAlmostEqual(sum(m['clipping_hours'] for m in months), af['clipping_hours'], delta=0.1)
        self.assertGreaterEqual(max(m['peak_curtailed_kw'] for m in months), af['peak_curtailed_kw'])

    def test_dc_coupled_dispatch_recovers_pv_surplus_with_shared_ac_output_cap(self):
        valid = np.ones(1, dtype=bool)
        prices = np.ones(1)

        # Charging can use PV above the 6.6 kW AC output ceiling when the
        # battery has headroom; only household output and export use that cap.
        recovered, trace = engine.dispatch_year(
            np.array([8.0]), np.array([0.0]), 10.0, 10.0, 10.0,
            1.0, 6.6, 0.0, prices, valid, True,
        )
        self.assertAlmostEqual(recovered[1], 8.0, places=6)
        self.assertAlmostEqual(recovered[3], 8.0, places=6)
        self.assertAlmostEqual(recovered[9], 0.0, places=6)
        self.assertAlmostEqual(trace[0, 6], 8.0, places=6)

        # With a full battery, direct PV plus export still cannot exceed AC;
        # all remaining PV is recorded as actual curtailment.
        full, trace = engine.dispatch_year(
            np.array([10.0]), np.array([2.0]), 0.0, 10.0, 10.0,
            1.0, 6.6, 0.0, prices, valid, True,
        )
        self.assertAlmostEqual(trace[0, 5], 2.0, places=6)  # direct PV
        self.assertAlmostEqual(trace[0, 3], 4.6, places=6)  # export
        self.assertAlmostEqual(full[9], 3.4, places=6)  # curtailed
        self.assertAlmostEqual(trace[0, 5] + trace[0, 3], 6.6, places=6)

        # A 2 kW charge limit recovers only 2 kWh before the same AC output
        # is used for direct demand and export.
        limited, trace = engine.dispatch_year(
            np.array([10.0]), np.array([2.0]), 2.0, 2.0, 10.0,
            1.0, 6.6, 0.0, prices, valid, True,
        )
        self.assertAlmostEqual(limited[3], 2.0, places=6)
        self.assertAlmostEqual(trace[0, 3], 4.6, places=6)
        self.assertAlmostEqual(limited[9], 1.4, places=6)
        self.assertAlmostEqual(limited[1] + limited[9], 10.0, places=6)

        defaults = engine.defaults()
        self.assertEqual(defaults['pv_battery_coupling'], 'ac_equivalent')
        with self.assertRaises(ValueError):
            engine.validate({'pv_battery_coupling': 'unsupported_topology'})

    def test_dc_coupled_dispatch_matrix_reports_actual_clipping(self):
        settings = {
            'south_panels': 33, 'west_panels': 0, 'east_panels': 0,
            'panel_watts': 480, 'inverter_kw': 6.6, 'battery_kwh': 10.24,
            'charge_kw': 10.0, 'pv_battery_coupling': 'dc_coupled',
            'load_scenario': 'electrified',
        }
        matrix = engine.dispatch_matrix(settings, mode='forecast_2027', representative='median')
        annual = matrix['annual_flows']
        self.assertEqual(matrix['selected_config']['pv_battery_coupling'], 'dc_coupled')
        self.assertAlmostEqual(annual['pv_input_kwh'], annual['pv_generation_kwh'] + annual['clipped_kwh'], delta=0.2)
        self.assertGreater(annual['battery_charge_kwh'], 0)
        self.assertGreater(annual['clipped_kwh'], 0)

    def test_dispatch_matrix_leap_year_season_weighting(self):
        # 2026 install -> 2027 forecast year (non-leap: 365 days)
        m_nonleap = engine.dispatch_matrix({'installation_year': 2026, 'south_panels': 14}, mode='forecast_2027')
        self.assertEqual(m_nonleap['year'], 2027)

        # 2027 install -> 2028 forecast year (leap: 366 days)
        m_leap = engine.dispatch_matrix({'installation_year': 2027, 'south_panels': 14}, mode='forecast_2027')
        self.assertEqual(m_leap['year'], 2028)
        self.assertIn('winter', m_leap['matrix']['seasons'])
        self.assertEqual(len(m_leap['matrix']['seasons']['winter']), 24)

    def test_dispatch_matrix_mode_validation_and_cache_immutability(self):
        settings = {'south_panels': 14, 'battery_kwh': 5.0}
        m1 = engine.dispatch_matrix(settings, mode='forecast_2027')
        m2 = engine.dispatch_matrix(settings, mode='forecast')
        self.assertEqual(m1['annual_flows']['pv_generation_kwh'], m2['annual_flows']['pv_generation_kwh'])
        self.assertEqual(m1['mode'], 'forecast_2027')
        self.assertEqual(m2['mode'], 'forecast')

        with self.assertRaises(ValueError):
            engine.dispatch_matrix(settings, mode='unsupported_arbitrary_mode')

        # Test cache immutability: mutating returned object does not corrupt cached data
        m1['annual_flows']['pv_generation_kwh'] = -9999.0
        m1['matrix']['months'][0][0]['pv'] = 9999.0
        m3 = engine.dispatch_matrix(settings, mode='forecast_2027')
        self.assertNotEqual(m3['annual_flows']['pv_generation_kwh'], -9999.0)
        self.assertNotEqual(m3['matrix']['months'][0][0]['pv'], 9999.0)

    def test_summary_sensitivity_evaluates_active_scope_and_cash(self):
        settings = {'south_panels': 14, 'battery_kwh': 5.0}
        sens = engine.summary_sensitivity(settings, quote={'line_items': []}, scope='solar', cash_mode='cash')
        self.assertEqual(sens['scope'], 'solar')
        self.assertEqual(sens['cash_mode'], 'cash')
        self.assertIn('median', sens['base_npv'])
        self.assertEqual(len(sens['base_npv']['by_weather']), engine.weather_period().count)
        
        # Energy price sensitivity
        self.assertIn('energy_price', sens)
        ep = sens['energy_price']
        self.assertIn('low_npv', ep)
        self.assertIn('high_npv', ep)
        self.assertIn('delta_low', ep)
        self.assertIn('delta_high', ep)
        self.assertIn('low_value', ep)
        self.assertIn('high_value', ep)
        self.assertEqual(ep['low_value'], round(settings.get('retail_commodity', assumptions.SPEC['retail_commodity'][0]) * 0.8, 4))
        
        # Discount rate sensitivity
        self.assertIn('discount_rate', sens)
        dr = sens['discount_rate']
        self.assertIn('low_npv', dr)
        self.assertIn('high_npv', dr)
        self.assertIn('low_value', dr)
        self.assertIn('high_value', dr)

        # Weather year risk
        self.assertIn('weather', sens)
        w = sens['weather']
        self.assertIn('worst_weather_npv', w)
        self.assertIn('best_weather_npv', w)
        self.assertIn('worst_weather_year', w)
        self.assertIn('best_weather_year', w)
        self.assertIsInstance(w['worst_weather_year'], int)
        self.assertIsInstance(w['best_weather_year'], int)
        self.assertEqual(w['worst_weather_npv'], sens['base_npv']['min'])
        self.assertEqual(w['best_weather_npv'], sens['base_npv']['max'])

    def test_summary_sensitivity_four_scope_cash_modes(self):
        settings = {'south_panels': 14, 'battery_kwh': 5.0, 'load_scenario': 'electrified', 'loan_amount': 5000.0}
        base_res = engine.evaluate(settings)
        f = base_res['finance']

        # 1. solar x cash -> pv_incremental
        s_c = engine.summary_sensitivity(settings, scope='solar', cash_mode='cash')
        self.assertEqual(s_c['base_npv']['median'], f['pv_incremental']['npv']['median'])

        # 2. solar x loan -> solar_equity
        s_l = engine.summary_sensitivity(settings, scope='solar', cash_mode='loan')
        self.assertEqual(s_l['base_npv']['median'], f['solar_equity']['npv']['median'])

        # 3. package x cash -> whole_package
        p_c = engine.summary_sensitivity(settings, scope='package', cash_mode='cash')
        self.assertEqual(p_c['base_npv']['median'], f['whole_package']['npv']['median'])

        # 4. package x loan -> package_equity
        p_l = engine.summary_sensitivity(settings, scope='package', cash_mode='loan')
        self.assertEqual(p_l['base_npv']['median'], f['package_equity']['npv']['median'])

    def test_summary_sensitivity_boundary_clamping(self):
        # Bound at lower limit for discount rate (0.0): minus 2% must clamp to 0.0
        settings = {'south_panels': 14, 'battery_kwh': 5.0, 'real_discount_rate': 0.01}
        sens = engine.summary_sensitivity(settings, scope='solar', cash_mode='cash')
        dr = sens['discount_rate']
        self.assertEqual(dr['low_value'], 0.0)
        # Evaluated low_npv must match direct engine evaluate with real_discount_rate=0.0
        expected_eval = engine.evaluate({**settings, 'real_discount_rate': 0.0})
        self.assertEqual(dr['low_npv'], expected_eval['finance']['pv_incremental']['npv']['mean'])

    def test_summary_sensitivity_itemized_capex_uses_actual_vat_inclusive_cost(self):
        settings = {'south_panels': 14, 'battery_kwh': 5.0, 'loan_mode': 'amount', 'loan_amount': 5000.0}
        quote = {'line_items': [{'net_eur': 10000, 'vat': 0.1, 'quantity': 1}]}
        sens = engine.summary_sensitivity(settings, quote=quote, scope='solar', cash_mode='loan')
        expected = engine.evaluate({**settings, 'quote_total': 12100.0, 'quote_vat_included': True})
        self.assertAlmostEqual(sens['capex']['plus_10_pct']['npv_eur'], expected['finance']['solar_equity']['npv']['mean'])
        self.assertNotEqual(sens['capex']['plus_10_pct']['delta_npv'], 0)

    def test_summary_sensitivity_new_driver_bounds_and_direct_results(self):
        settings = {'south_panels': 14, 'battery_kwh': 5.0,
                    'gas_commodity': assumptions.SPEC['gas_commodity'][2][1],
                    'battery_calendar_fade': assumptions.SPEC['battery_calendar_fade'][2][1],
                    'battery_cycle_fade': assumptions.SPEC['battery_cycle_fade'][2][1],
                    'inverter_replacement_cost': assumptions.SPEC['inverter_replacement_cost'][2][1],
                    'battery_replacement_cost_kwh': assumptions.SPEC['battery_replacement_cost_kwh'][2][1]}
        sens = engine.summary_sensitivity(settings, scope='solar', cash_mode='cash')
        self.assertEqual(sens['gas_price']['plus_20_pct']['price'], settings['gas_commodity'])
        self.assertEqual(sens['gas_price']['plus_20_pct']['delta_npv'], 0)
        self.assertEqual(sens['battery_degradation']['higher_fade']['delta_npv'], 0)
        self.assertEqual(sens['replacement_cost']['plus_20_pct']['delta_npv'], 0)
        self.assertLessEqual(sens['export_price_ramp']['ramp_down']['factor'], 1)

    def test_summary_sensitivity_input_validation(self):
        settings = {'south_panels': 14}
        with self.assertRaises(ValueError):
            engine.summary_sensitivity(settings, scope='invalid_scope', cash_mode='cash')
        with self.assertRaises(ValueError):
            engine.summary_sensitivity(settings, scope='solar', cash_mode='invalid_cash')

    def test_server_routes_behavior_matrix_and_summary_sensitivity(self):
        def make_post(path, payload):
            class Request: pass
            req = Request()
            req.headers = {'Content-Length': str(len(json.dumps(payload).encode()))}
            req.rfile = io.BytesIO(json.dumps(payload).encode())
            req.path = path
            req.response = None
            req.status = None
            def send(obj, status=200):
                req.response = obj
                req.status = status
            req.send = send
            server_module.LegacyLocalHandler.do_POST(req)
            return req

        # /api/behavior-matrix
        req1 = make_post('/api/behavior-matrix', {'settings': {'south_panels': 14}, 'scenario': 'baseline'})
        self.assertEqual(req1.status, 200)
        self.assertIn('matrix', req1.response)
        self.assertIn('annual_flows', req1.response)
        self.assertIn('compute_seconds', req1.response)

        # /api/summary-sensitivity
        req2 = make_post('/api/summary-sensitivity', {'settings': {'south_panels': 14}, 'scope': 'solar', 'cash_mode': 'cash'})
        self.assertEqual(req2.status, 200)
        self.assertIn('base_npv', req2.response)
        self.assertIn('energy_price', req2.response)
        self.assertIn('export_price_ramp', req2.response)
        self.assertIn('capex', req2.response)
        self.assertIn('replacement_cost', req2.response)
        self.assertIn('gas_price', req2.response)
        self.assertIn('battery_degradation', req2.response)
        self.assertIn('compute_seconds', req2.response)

        # Invalid route request error handling
        req3 = make_post('/api/summary-sensitivity', {'settings': {'south_panels': 14}, 'scope': 'unknown'})
        self.assertEqual(req3.status, 400)
        self.assertIn('error', req3.response)


if __name__ == '__main__':
    unittest.main()
