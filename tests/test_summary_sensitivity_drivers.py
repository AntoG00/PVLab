"""Regression tests for summary sensitivity drivers and joint all-adverse recomputation."""
import copy
import math
import numpy as np
import unittest

from feasibility import assumptions, engine
from tests.test_support import IsolatedDefaultsTestCase


class SummarySensitivityDriversTests(IsolatedDefaultsTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.base_settings = engine.validate(None)
        cls.solar_cash = engine.summary_sensitivity(cls.base_settings, scope='solar', cash_mode='cash')
        cls.solar_loan = engine.summary_sensitivity(cls.base_settings, scope='solar', cash_mode='loan')
        cls.pkg_cash = engine.summary_sensitivity(cls.base_settings, scope='package', cash_mode='cash')
        cls.pkg_loan = engine.summary_sensitivity(cls.base_settings, scope='package', cash_mode='loan')

    def test_added_six_drivers_completeness_and_finite_npvs(self):
        expected_drivers = {
            'heat_pump_cop',
            'real_energy_price_escalation',
            'export_price_level',
            'loan_apr',
            'pv_yield',
            'tax_deduction',
            'all_adverse_together',
        }
        for driver in expected_drivers:
            self.assertIn(driver, self.solar_cash, f"Driver {driver} missing from summary_sensitivity")
            d = self.solar_cash[driver]
            self.assertIsInstance(d, dict)
            if driver == 'all_adverse_together':
                self.assertTrue(math.isfinite(d['npv_eur']), f"Non-finite npv_eur for {driver}")
                self.assertTrue(math.isfinite(d['delta_npv']), f"Non-finite delta_npv for {driver}")
            else:
                self.assertTrue(math.isfinite(d['base']['npv_eur']), f"Non-finite base npv_eur for {driver}")
                self.assertTrue(math.isfinite(d['delta_low']), f"Non-finite delta_low for {driver}")
                self.assertTrue(math.isfinite(d['delta_high']), f"Non-finite delta_high for {driver}")

    def test_four_scopes_baseline_npv_matches_direct_engine_evaluate(self):
        cases = [
            ('solar', 'cash', self.solar_cash, 'pv_incremental'),
            ('solar', 'loan', self.solar_loan, 'solar_equity'),
            ('package', 'cash', self.pkg_cash, 'whole_package'),
            ('package', 'loan', self.pkg_loan, 'package_equity'),
        ]
        direct_eval = engine.evaluate(self.base_settings)
        for scope, cash_mode, summary_res, finance_key in cases:
            with self.subTest(scope=scope, cash_mode=cash_mode):
                expected_mean = float(direct_eval['finance'][finance_key]['npv']['mean'])
                expected_median = float(direct_eval['finance'][finance_key]['npv']['median'])
                self.assertAlmostEqual(summary_res['base_npv_eur'], expected_mean, places=4)
                self.assertAlmostEqual(summary_res['base_npv']['mean'], expected_mean, places=4)
                self.assertAlmostEqual(summary_res['base_npv']['median'], expected_median, places=4)

    def test_actual_joint_npv_matches_independent_evaluate(self):
        adv = self.solar_cash['all_adverse_together']
        applied = adv['applied_settings']
        r_joint = engine.evaluate({**self.base_settings, **applied})
        joint_min_weather = float(r_joint['finance']['pv_incremental']['npv']['min'])
        self.assertAlmostEqual(adv['npv_eur'], joint_min_weather, places=4)

        by_weather = r_joint['finance']['pv_incremental']['npv_by_weather']
        weather_years = [int(y) for y in r_joint.get('weather_years', engine.data()['years'])]
        expected_worst_weather_year = weather_years[int(np.argmin(by_weather))]
        self.assertEqual(adv['weather_year'], expected_worst_weather_year)

        single_deltas = [
            self.solar_cash['electricity_price']['minus_20_pct']['delta_npv'],
            self.solar_cash['export_price_ramp']['delta_low'],
            self.solar_cash['capex']['delta_low'],
            self.solar_cash['replacement_cost']['delta_low'],
            self.solar_cash['gas_price']['delta_low'],
            self.solar_cash['heat_pump_cop']['delta_low'],
            self.solar_cash['real_energy_price_escalation']['delta_low'],
            self.solar_cash['export_price_level']['delta_low'],
            self.solar_cash['pv_yield']['delta_low'],
            self.solar_cash['tax_deduction']['delta_low'],
        ]
        self.assertNotEqual(adv['delta_npv'], sum(single_deltas))

    def test_baseline_inputs_and_quote_not_mutated(self):
        test_s = dict(self.base_settings)
        test_q = {'line_items': [{'net_eur': 5000, 'vat': 0.1, 'quantity': 1, 'scope': 'solar_storage'}]}
        s_copy = copy.deepcopy(test_s)
        q_copy = copy.deepcopy(test_q)
        engine.summary_sensitivity(test_s, test_q)
        self.assertEqual(test_s, s_copy)
        self.assertEqual(test_q, q_copy)

    def test_discount_rate_unchanged_in_joint(self):
        applied = self.solar_cash['all_adverse_together']['applied_settings']
        self.assertNotIn('real_discount_rate', applied)

    def test_upperbound_perturb_metadata_within_spec(self):
        s_upper = {
            **self.base_settings,
            'scop': assumptions.SPEC['scop'][2][1],
            'dhw_scop': assumptions.SPEC['dhw_scop'][2][1],
            'energy_real_escalation': assumptions.SPEC['energy_real_escalation'][2][1],
            'export_market_price': assumptions.SPEC['export_market_price'][2][1],
            'loan_apr': assumptions.SPEC['loan_apr'][2][1],
            'deduction_tax_capacity': assumptions.SPEC['deduction_tax_capacity'][2][1],
        }
        res_upper = engine.summary_sensitivity(settings=s_upper)
        driver_specs = [
            ('heat_pump_cop', 'scop', ['base', 'minus_20_pct', 'plus_20_pct'], assumptions.SPEC['scop'][2], 'plus_20_pct'),
            ('heat_pump_cop', 'dhw_scop', ['base', 'minus_20_pct', 'plus_20_pct'], assumptions.SPEC['dhw_scop'][2], 'plus_20_pct'),
            ('real_energy_price_escalation', 'rate', ['base', 'minus_1_pct', 'plus_1_pct'], assumptions.SPEC['energy_real_escalation'][2], 'plus_1_pct'),
            ('export_price_level', 'price', ['base', 'minus_20_pct', 'plus_20_pct'], assumptions.SPEC['export_market_price'][2], 'plus_20_pct'),
            ('loan_apr', 'rate', ['base', 'minus_2_pct', 'plus_2_pct'], assumptions.SPEC['loan_apr'][2], 'plus_2_pct'),
            ('pv_yield', 'watts', ['base', 'minus_10_pct', 'plus_10_pct'], assumptions.SPEC['panel_watts'][2], None),
            ('tax_deduction', 'capacity', ['base', 'low_capacity', 'high_capacity'], assumptions.SPEC['deduction_tax_capacity'][2], 'high_capacity'),
        ]
        for driver_name, field_name, endpoint_keys, bounds, upper_key in driver_specs:
            d = res_upper[driver_name]
            low_bound = bounds[0] if bounds[0] is not None else -float('inf')
            high_bound = bounds[1] if bounds[1] is not None else float('inf')
            for ep_key in endpoint_keys:
                ep_dict = d[ep_key]
                self.assertIn(field_name, ep_dict, f"Field {field_name} missing from {driver_name}[{ep_key}]")
                val = ep_dict[field_name]
                self.assertIsNotNone(val)
                self.assertGreaterEqual(val, low_bound, f"{driver_name}[{ep_key}][{field_name}] < low_bound")
                self.assertLessEqual(val, high_bound, f"{driver_name}[{ep_key}][{field_name}] > high_bound")
            if upper_key is not None and high_bound != float('inf'):
                clipped_val = d[upper_key][field_name]
                self.assertEqual(clipped_val, high_bound, f"Upper endpoint {driver_name}[{upper_key}][{field_name}] not clipped to max {high_bound}")

    def test_pv_yield_quote_capex_fixed_with_itemized_vat_normalized_fixtures(self):
        quote = {
            'line_items': [
                {'net_eur': 6000.0, 'vat': 0.10, 'quantity': 1, 'scope': 'solar_storage'}
            ]
        }
        res = engine.summary_sensitivity(settings=self.base_settings, quote=quote)
        base_capex = 6000.0 * 1.10

        # Independent recomputation of yield minus/plus with fixed normalized quote capex
        s_norm, _, _, _ = engine.normalize_quote(self.base_settings, quote)
        watts_val = s_norm.get('panel_watts', 400.0)
        low_watts = round(watts_val * 0.9, 1)
        high_watts = round(watts_val * 1.1, 1)

        r_low_y = engine.evaluate({**s_norm, 'panel_watts': low_watts, 'quote_total': base_capex, 'quote_vat_included': True})
        r_high_y = engine.evaluate({**s_norm, 'panel_watts': high_watts, 'quote_total': base_capex, 'quote_vat_included': True})

        self.assertEqual(r_low_y['finance']['capex_eur'], base_capex)
        self.assertEqual(r_high_y['finance']['capex_eur'], base_capex)

        expected_low_npv = float(r_low_y['finance']['pv_incremental']['npv']['mean'])
        expected_high_npv = float(r_high_y['finance']['pv_incremental']['npv']['mean'])

        self.assertAlmostEqual(res['pv_yield']['minus_10_pct']['npv_eur'], expected_low_npv, places=4)
        self.assertAlmostEqual(res['pv_yield']['plus_10_pct']['npv_eur'], expected_high_npv, places=4)

        # In all_adverse_together, capex change must NOT be overwritten back to base capex by pv_yield
        adverse_quote = res['all_adverse_together']['applied_settings'].get('quote_total')
        self.assertIsNotNone(adverse_quote)
        self.assertAlmostEqual(adverse_quote, base_capex * 1.10, places=2)

    def test_market_price_driver_changes_export_market_price_not_pmg_floor(self):
        s_pmg = {**self.base_settings, 'pmg_election': True, 'pmg_price': 0.05, 'export_market_price': 0.12}
        res_pmg = engine.summary_sensitivity(settings=s_pmg)
        exp_driver = res_pmg['export_price_level']
        self.assertEqual(exp_driver['parameter'], 'export_market_price')
        self.assertTrue(exp_driver['pmg_floor_active'])
        self.assertEqual(exp_driver['pmg_price'], 0.05)
        self.assertIn('export_market_price', res_pmg['all_adverse_together']['applied_settings'])
        self.assertNotIn('pmg_price', res_pmg['all_adverse_together']['applied_settings'])

    def test_loan_apr_zero_delta_on_cash_basis_valid(self):
        self.assertEqual(self.solar_cash['loan_apr']['delta_low'], 0.0)
        self.assertEqual(self.solar_cash['loan_apr']['delta_high'], 0.0)

        s_loan = {**self.base_settings, 'loan_fraction': 0.5}
        res_loan = engine.summary_sensitivity(settings=s_loan, cash_mode='loan')
        self.assertNotEqual(res_loan['loan_apr']['delta_low'], 0.0)


if __name__ == '__main__':
    unittest.main()
