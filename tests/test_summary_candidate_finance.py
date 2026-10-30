"""Candidate comparisons preserve quote pricing and compute capped-loan recovery."""
from copy import deepcopy
from unittest.mock import patch

import numpy as np

from feasibility import engine
from tests.test_support import IsolatedDefaultsTestCase


class SummaryCandidateFinanceTests(IsolatedDefaultsTestCase):
    def setUp(self):
        super().setUp()
        self.base = engine.validate({
            'south_panels': 19, 'west_panels': 0, 'east_panels': 0,
            'dormer_panels': 0, 'flat_panels': 0, 'secondary_south_panels': 0,
            'panel_watts': 480, 'quote_total': 16000, 'quote_vat_included': True,
            'pv_cost_kwp': 1996 / 1.92, 'deduction_rate': 0,
            'loan_mode': 'amount', 'loan_amount': 15000, 'loan_apr': .05,
            'loan_years': 10, 'loan_include_electrification': True,
            'load_scenario': 'electrified', 'planning_heat_pump': False,
            'electrification_capex': 1000, 'gas_disconnect_cost': 150,
            'horizon_years': 20, 'inflation': 0, 'real_discount_rate': .03,
            'om_annual': 0, 'insurance_annual': 0, 'cleaning_annual': 0,
            'inverter_replacement_cost': 0, 'battery_replacement_cost_kwh': 0,
            'residual_fraction': 0, 'gse_fee_eur_kw_year': 0, 'cer_enabled': False,
        })

    def normalize_candidate(self, reference=None, **changes):
        reference = self.base if reference is None else reference
        candidate = {**reference, 'south_panels': 23, **changes}
        return engine.normalize_quote(candidate, {
            'line_items': [], 'price_basis': 'summary_candidate',
            'reference_settings': reference,
        })[0]

    def test_quote_anchor_and_real_financial_cashflows(self):
        original = deepcopy(self.base)
        candidate = self.normalize_candidate(quote_total=14004.48)
        self.assertEqual(candidate['quote_total'], 17996)
        self.assertEqual(candidate['loan_amount'], 15000)
        self.assertEqual(self.base, original)
        annual = np.zeros((20, 1, len(engine.FIELDS)))
        annual[:, :, 0] = 3100
        annual[:, :, 1] = 3500
        # Synthetic €3,100 annual operating benefit, with actual loan schedule,
        # capital costs, discounting and payback computed by _financial.
        with patch.object(engine, 'data', return_value={'audit': {'annual_kwh': 3100}}), \
             patch.object(engine, 'price_profile', return_value=(None, 'flat_solar_capture_benchmark')), \
             patch.object(engine, 'electric_bill', side_effect=lambda kwh, *args: kwh), \
             patch.object(engine, 'gas_bill', return_value=0):
            current, _ = engine._financial(self.base, annual, 9.12, calculate_irr=False)
            proposed, _ = engine._financial(candidate, annual, 11.04, calculate_irr=False)
        self.assertEqual(proposed['loan_principal_eur'], 15000)
        self.assertEqual(current['cashflows_by_weather']['package_equity'][0][0], -2150)
        self.assertEqual(proposed['cashflows_by_weather']['package_equity'][0][0], -4146)
        recovery = proposed['package_equity']['discounted_payback']['median']
        self.assertGreater(recovery, 3.6)
        self.assertLess(recovery, 4.0)
        self.assertGreater(recovery, current['package_equity']['discounted_payback']['median'])

    def test_same_configuration_keeps_quote_including_vat(self):
        for vat_included in (True, False):
            reference = {**self.base, 'quote_vat_included': vat_included}
            normalized = self.normalize_candidate(reference, south_panels=19)
            expected = 16000 if vat_included else 17600
            self.assertAlmostEqual(normalized['quote_total'], expected)
            self.assertTrue(normalized['quote_vat_included'])
            larger = self.normalize_candidate(reference)
            self.assertAlmostEqual(larger['quote_total'], expected + 1996 * (1 if vat_included else 1.1))

    def test_more_panels_can_cost_less_when_removing_storage(self):
        candidate = self.normalize_candidate(
            battery_modules=0, battery_secondary_modules=0, battery_kwh=0)
        self.assertLess(candidate['quote_total'], self.base['quote_total'])

    def test_modeled_pricing_and_no_install_remain_valid(self):
        reference = {**self.base, 'quote_total': None}
        candidate = self.normalize_candidate(reference, quote_total=None)
        self.assertIsNone(candidate['quote_total'])
        no_install = self.normalize_candidate(
            south_panels=0, battery_modules=0, battery_secondary_modules=0, battery_kwh=0)
        self.assertEqual(no_install['quote_total'], 0)
