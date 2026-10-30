"""Focused regression tests for manual quote total overrides and price basis normalization."""
import copy
import math
import unittest

from feasibility import engine as e
from test_support import IsolatedDefaultsTestCase


class QuoteTotalRegressionTests(IsolatedDefaultsTestCase):
    def setUp(self):
        super().setUp()
        self.base_settings = {
            'south_panels': 12,
            'west_panels': 0,
            'east_panels': 0,
            'dormer_panels': 0,
            'flat_panels': 0,
            'secondary_south_panels': 0,
            'panel_watts': 400.0,
            'inverter_kw': 6.0,
            'battery_kwh': 10.0,
            'battery_modules': 2,
            'battery_module_kwh': 5.0,
            'charge_kw': 5.0,
            'discharge_kw': 5.0,
            'roundtrip_efficiency': 0.9,
            'horizon_years': 20,
            'deduction_rate': 0.5,
            'deduction_price_premium': 0.15,
            'pv_fixed_cost': 1500.0,
            'pv_cost_kwp': 1100.0,
            'battery_cost_kwh': 550.0,
            'installation_vat': 0.10,
            'quote_vat_included': True,
            'quote_total': 16000.0,
            'load_scenario': 'electrified'
        }

    def test_modeled_price_basis_with_manual_quote_overrides_benchmark_formula(self):
        """A manual quote_total overrides fixed PV, per-kWp and battery cost even when price_basis='modeled'."""
        quote = {'price_basis': 'modeled', 'line_items': []}
        settings = dict(self.base_settings)
        norm_s, norm_q, total, populated = e.normalize_quote(settings, quote)

        self.assertEqual(norm_s['quote_total'], 16000.0)
        self.assertTrue(norm_s['quote_vat_included'])

        kwp = 12 * 400.0 / 1000.0  # 4.8 kWp
        capex = e.installed_capex(norm_s, kwp)
        self.assertEqual(capex, 16000.0)

        # Full quote evaluation also yields 16000.0 capex
        eval_res = e.quote_evaluate(settings, quote)
        self.assertEqual(eval_res['result']['finance']['capex_eur'], 16000.0)

    def test_changing_component_costs_leaves_quoted_capex_unchanged(self):
        """When manual quote_total is active, changing pv_fixed_cost, pv_cost_kwp and battery_cost_kwh has no effect."""
        quote = {'price_basis': 'modeled', 'line_items': []}
        settings = dict(self.base_settings)

        norm_s, _, _, _ = e.normalize_quote(settings, quote)
        kwp = 12 * 400.0 / 1000.0
        initial_capex = e.installed_capex(norm_s, kwp)
        self.assertEqual(initial_capex, 16000.0)

        # Drastically change all three component benchmark costs
        altered_settings = {
            **settings,
            'pv_fixed_cost': 5000.0,
            'pv_cost_kwp': 3000.0,
            'battery_cost_kwh': 1200.0
        }
        altered_norm_s, _, _, _ = e.normalize_quote(altered_settings, quote)
        altered_capex = e.installed_capex(altered_norm_s, kwp)
        self.assertEqual(altered_capex, 16000.0)

        eval_res = e.quote_evaluate(altered_settings, quote)
        self.assertEqual(eval_res['result']['finance']['capex_eur'], 16000.0)

    def test_manual_quote_zero_is_valid_override(self):
        """A manual quote_total of 0.0 is valid and overrides benchmark formula."""
        quote = {'price_basis': 'modeled', 'line_items': []}
        settings = {**self.base_settings, 'quote_total': 0.0}

        norm_s, _, _, _ = e.normalize_quote(settings, quote)
        self.assertEqual(norm_s['quote_total'], 0.0)

        kwp = 12 * 400.0 / 1000.0
        capex = e.installed_capex(norm_s, kwp)
        self.assertEqual(capex, 0.0)

        eval_res = e.quote_evaluate(settings, quote)
        self.assertEqual(eval_res['result']['finance']['capex_eur'], 0.0)

    def test_clearing_quote_total_to_null_restores_modeled_benchmark_formula(self):
        """Setting quote_total to None restores the modeled benchmark formula."""
        quote = {'price_basis': 'modeled', 'line_items': []}
        settings = {**self.base_settings, 'quote_total': None}

        norm_s, _, _, _ = e.normalize_quote(settings, quote)
        self.assertIsNone(norm_s['quote_total'])

        kwp = 12 * 400.0 / 1000.0  # 4.8 kWp
        # Benchmark capex: (pv_fixed_cost + kwp * pv_cost_kwp + battery_kwh * battery_cost_kwh) * (1 + deduction_price_premium)
        base_cost = 1500.0 + 4.8 * 1100.0 + 10.0 * 550.0  # 1500 + 5280 + 5500 = 12280
        expected_cost = base_cost * (1.0 + 0.15)  # 12280 * 1.15 = 14122.0
        capex = e.installed_capex(norm_s, kwp)
        self.assertAlmostEqual(capex, expected_cost, places=2)

        # Modifying component costs now modifies effective capex
        altered_settings = {**settings, 'pv_fixed_cost': 2000.0}
        altered_norm_s, _, _, _ = e.normalize_quote(altered_settings, quote)
        altered_base = 2000.0 + 4.8 * 1100.0 + 10.0 * 550.0  # 12780
        self.assertAlmostEqual(e.installed_capex(altered_norm_s, kwp), altered_base * 1.15, places=2)

    def test_itemized_precedence_over_manual_quote_and_modeled_fallback(self):
        """In itemized mode, populated lines take precedence over manual total; empty lines fallback to manual total."""
        quote_with_lines = {
            'price_basis': 'itemized',
            'line_items': [
                {'description': 'Panels', 'quantity': 1, 'net_eur': 6000.0, 'vat': 0.10},
                {'description': 'Storage', 'quantity': 1, 'net_eur': 4000.0, 'vat': 0.10}
            ]
        }
        settings = dict(self.base_settings)  # quote_total = 16000.0

        # With lines in itemized mode: line items override manual quote total
        norm_s, _, total, populated = e.normalize_quote(settings, quote_with_lines)
        self.assertEqual(populated, 2)
        self.assertEqual(total, 11000.0)  # (6000 + 4000) * 1.1 = 11000
        self.assertEqual(norm_s['quote_total'], 11000.0)
        self.assertTrue(norm_s['quote_vat_included'])

        kwp = 12 * 400.0 / 1000.0
        self.assertEqual(e.installed_capex(norm_s, kwp), 11000.0)

        # In itemized mode with NO lines: manual total is used if present
        quote_no_lines = {'price_basis': 'itemized', 'line_items': []}
        norm_s_empty, _, _, populated_empty = e.normalize_quote(settings, quote_no_lines)
        self.assertEqual(populated_empty, 0)
        self.assertEqual(norm_s_empty['quote_total'], 16000.0)
        self.assertEqual(e.installed_capex(norm_s_empty, kwp), 16000.0)

        # In itemized mode with NO lines and NULL manual total: falls back to modeled formula
        settings_null = {**settings, 'quote_total': None}
        norm_s_null, _, _, _ = e.normalize_quote(settings_null, quote_no_lines)
        self.assertIsNone(norm_s_null['quote_total'])
        base_cost = (1500.0 + 4.8 * 1100.0 + 10.0 * 550.0) * 1.15
        self.assertAlmostEqual(e.installed_capex(norm_s_null, kwp), base_cost, places=2)

    def test_modeled_retained_lines_behavior(self):
        """In modeled mode, populated line items are kept as reference-only and do not override manual total or benchmark."""
        quote_with_lines = {
            'price_basis': 'modeled',
            'line_items': [
                {'description': 'Panels', 'quantity': 1, 'net_eur': 6000.0, 'vat': 0.10},
                {'description': 'Storage', 'quantity': 1, 'net_eur': 4000.0, 'vat': 0.10}
            ]
        }
        kwp = 12 * 400.0 / 1000.0

        # With manual quote present: manual total is used, lines remain reference-only
        settings = dict(self.base_settings)  # quote_total = 16000.0
        norm_s, norm_q, total, populated = e.normalize_quote(settings, quote_with_lines)
        self.assertEqual(populated, 2)
        self.assertEqual(total, 11000.0)
        self.assertEqual(norm_s['quote_total'], 16000.0)
        self.assertEqual(e.installed_capex(norm_s, kwp), 16000.0)

        eval_res = e.quote_evaluate(settings, quote_with_lines)
        self.assertEqual(eval_res['result']['finance']['capex_eur'], 16000.0)
        self.assertEqual(eval_res['itemized_total_eur'], 11000.0)

        # With null manual quote: modeled formula is used, lines remain reference-only
        settings_null = {**settings, 'quote_total': None}
        norm_s_null, _, total_null, populated_null = e.normalize_quote(settings_null, quote_with_lines)
        self.assertEqual(populated_null, 2)
        self.assertEqual(total_null, 11000.0)
        self.assertIsNone(norm_s_null['quote_total'])
        base_cost = (1500.0 + 4.8 * 1100.0 + 10.0 * 550.0) * 1.15
        self.assertAlmostEqual(e.installed_capex(norm_s_null, kwp), base_cost, places=2)

        eval_null = e.quote_evaluate(settings_null, quote_with_lines)
        self.assertAlmostEqual(eval_null['result']['finance']['capex_eur'], base_cost, places=2)
        self.assertEqual(eval_null['itemized_total_eur'], 11000.0)

    def test_vat_not_double_counted_and_no_deduction_premium_on_explicit_quote(self):
        """Explicit quote skips deduction_price_premium and does not double-count VAT when quote_vat_included is true."""
        kwp = 12 * 400.0 / 1000.0

        # When quote_vat_included is True: capex is exactly quote_total
        s_vat_incl = {**self.base_settings, 'quote_total': 16000.0, 'quote_vat_included': True, 'deduction_price_premium': 0.25}
        norm_s1, _, _, _ = e.normalize_quote(s_vat_incl, {'price_basis': 'modeled'})
        self.assertEqual(e.installed_capex(norm_s1, kwp), 16000.0)

        # When quote_vat_included is False: VAT is applied once, premium still skipped
        s_vat_excl = {**self.base_settings, 'quote_total': 16000.0, 'quote_vat_included': False, 'installation_vat': 0.10, 'deduction_price_premium': 0.25}
        norm_s2, _, _, _ = e.normalize_quote(s_vat_excl, {'price_basis': 'modeled'})
        self.assertEqual(e.installed_capex(norm_s2, kwp), 16000.0 * 1.10)

    def test_loan_solve_normalizes_quote_consistently(self):
        """Loan solver uses the same quote normalization and respects manual quote in modeled mode."""
        quote = {'price_basis': 'modeled', 'line_items': []}
        settings = dict(self.base_settings)  # quote_total = 16000.0

        solved = e.solve_loan_amount(settings, quote)
        self.assertEqual(solved['eligible_cap_eur'], 16000.0)

    def test_reproduced_current_scenario_evaluates_at_manual_quote_without_mutation(self):
        """Inline reproduction fixture: manual quote total 16000 with modeled price basis evaluates to 16000 without input mutation."""
        settings = {
            **self.base_settings,
            'quote_total': 16000.0,
            'pv_fixed_cost': 0.0,
            'pv_cost_kwp': 1209.0,
            'battery_cost_kwh': 550.0,
            'quote_vat_included': True,
            'battery_kwh': 9.6,
            'battery_modules': 2,
            'battery_module_kwh': 4.8,
            'south_panels': 14,
            'panel_watts': 440.0
        }
        quote = {
            'price_basis': 'modeled',
            'line_items': []
        }
        orig_settings = copy.deepcopy(settings)
        orig_quote = copy.deepcopy(quote)

        norm_s, norm_q, total, populated = e.normalize_quote(settings, quote)
        self.assertEqual(norm_s['quote_total'], 16000.0)
        self.assertEqual(settings, orig_settings)
        self.assertEqual(quote, orig_quote)

        res = e.quote_evaluate(settings, quote)
        self.assertEqual(res['result']['finance']['capex_eur'], 16000.0)
        self.assertEqual(settings, orig_settings)
        self.assertEqual(quote, orig_quote)

    def test_settings_quote_total_canonical_and_stale_quote_quote_total_ignored(self):
        """settings.quote_total is canonical: clearing settings to None ignores retained quote.quote_total, while explicit settings wins."""
        quote_with_stale_total = {
            'price_basis': 'modeled',
            'quote_total': 16000.0,
            'line_items': []
        }
        # 1. When settings['quote_total'] is None, stale quote['quote_total'] is NOT resurrected
        settings_cleared = {**self.base_settings, 'quote_total': None}
        norm_s, _, _, _ = e.normalize_quote(settings_cleared, quote_with_stale_total)
        self.assertIsNone(norm_s['quote_total'])
        kwp = 12 * 400.0 / 1000.0  # 4.8 kWp
        base_cost = (1500.0 + 4.8 * 1100.0 + 10.0 * 550.0) * 1.15
        self.assertAlmostEqual(e.installed_capex(norm_s, kwp), base_cost, places=2)

        # Full evaluation also uses modeled benchmark formula
        eval_cleared = e.quote_evaluate(settings_cleared, quote_with_stale_total)
        self.assertAlmostEqual(eval_cleared['result']['finance']['capex_eur'], base_cost, places=2)

        # 2. When settings['quote_total'] has an explicit value, it wins over any conflicting quote['quote_total']
        settings_explicit = {**self.base_settings, 'quote_total': 14000.0}
        norm_s2, _, _, _ = e.normalize_quote(settings_explicit, quote_with_stale_total)
        self.assertEqual(norm_s2['quote_total'], 14000.0)
        self.assertEqual(e.installed_capex(norm_s2, kwp), 14000.0)


if __name__ == '__main__':
    unittest.main()
