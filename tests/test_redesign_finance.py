"""Independent numerical tests for redesign backend scope financial paths and mathematics."""
import math
import unittest
import numpy as np

from feasibility import assumptions, engine
try:
    from test_support import IsolatedDefaultsTestCase
except ImportError:
    from tests.test_support import IsolatedDefaultsTestCase


class RedesignFinanceTests(IsolatedDefaultsTestCase):
    def test_solar_allocation_with_principal_exceeding_solar_capex(self):
        settings = {
            'south_panels': 10,
            'battery_kwh': 0.0,
            'load_scenario': 'electrified',
            'loan_include_electrification': True,
            'loan_mode': 'amount',
            'loan_amount': 25000.0,
            'loan_upfront_fees_or_tax': 0.0,
            'loan_apr': 0.05,
            'loan_years': 8,
            'horizon_years': 8,
            'real_discount_rate': 0.03,
            'inflation': 0.02
        }
        res = engine.evaluate(settings)
        f = res['finance']

        capex = f['capex_eur']
        conv = f['conversion_capex_eur']
        total_loan_scope = capex + conv
        principal = min(total_loan_scope, 25000.0)

        # Solar capex must be strictly less than total principal
        self.assertGreater(principal, capex)
        self.assertEqual(f['solar_loan_principal_eur'], capex)
        expected_ratio = capex / principal
        self.assertAlmostEqual(f['solar_loan_ratio'], expected_ratio, places=6)
        self.assertLess(f['solar_loan_ratio'], 1.0)

        # Independently calculate debt schedule
        debt, balances = engine.loan_schedule(principal, 0.05, 8, 8)
        loan_principal = np.zeros(8)
        loan_interest = np.zeros(8)
        prev = principal
        for yy in range(8):
            loan_principal[yy] = max(prev - balances[yy], 0.0)
            loan_interest[yy] = max(debt[yy] - loan_principal[yy], 0.0)
            prev = balances[yy]

        # Verify annual debt and interest are allocated strictly by solar_loan_ratio
        proj_flows = np.array(f['cashflows_by_weather']['pv_incremental'])  # Weather paths x 9 (t=0..8)
        solar_eq_flows = np.array(f['cashflows_by_weather']['solar_equity'])
        solar_fin_flows = np.array(f['cashflows_by_weather']['solar_financed'])

        # Year 0: solar capex is fully covered by solar-allocated principal
        self.assertEqual(solar_eq_flows[0, 0], -capex + capex)
        self.assertEqual(solar_fin_flows[0, 0], -capex)

        # Years 1..8: independent array verification
        for yy in range(8):
            t = yy + 1
            allocated_debt = debt[yy] * expected_ratio
            allocated_interest = loan_interest[yy] * expected_ratio
            np.testing.assert_allclose(solar_eq_flows[:, t], proj_flows[:, t] - allocated_debt, rtol=1e-5)
            np.testing.assert_allclose(solar_fin_flows[:, t], proj_flows[:, t] - allocated_interest, rtol=1e-5)

        # Independent NPV computation for solar equity
        rnom = (1 + 0.03) * (1 + 0.02) - 1
        r_m = 0.05 / 12.0
        pmt = principal * r_m / (1 - (1 + r_m) ** -(8 * 12))
        monthly_pv = sum(pmt * (1 + rnom) ** (-m / 12.0) for m in range(1, 8 * 12 + 1))
        allocated_pv = monthly_pv * expected_ratio
        indep_npvs = [engine.npv(proj_flows[w], rnom) + f['solar_loan_principal_eur'] - allocated_pv for w in range(len(proj_flows))]
        self.assertAlmostEqual(f['solar_equity']['npv']['median'], float(np.median(indep_npvs)), places=2)
        self.assertAlmostEqual(f['solar_equity']['npv']['min'], float(np.min(indep_npvs)), places=2)
        self.assertAlmostEqual(f['solar_equity']['npv']['max'], float(np.max(indep_npvs)), places=2)

        # Verify explanatory definitions
        self.assertIn('proportional', f['solar_equity']['definition'].lower())
        self.assertIn('solar_loan_principal', f['solar_equity']['definition'])
        self.assertIn('solar_loan_ratio', f['solar_equity']['definition'])
        self.assertIn('interest', f['solar_financed']['definition'].lower())

    def test_package_full_service(self):
        settings = {
            'south_panels': 10,
            'battery_kwh': 5.0,
            'load_scenario': 'electrified',
            'loan_include_electrification': True,
            'loan_mode': 'fraction',
            'loan_fraction': 0.8,
            'loan_upfront_fees_or_tax': 0.0,
            'loan_apr': 0.06,
            'loan_years': 10,
            'horizon_years': 10,
            'real_discount_rate': 0.03,
            'inflation': 0.02
        }
        res = engine.evaluate(settings)
        f = res['finance']
        capex = f['capex_eur']
        conv = f['conversion_capex_eur']
        total_scope = capex + conv
        principal = total_scope * 0.8
        self.assertAlmostEqual(f['loan_principal_eur'], principal, places=2)

        debt, balances = engine.loan_schedule(principal, 0.06, 10, 10)
        loan_interest = np.zeros(10)
        prev = principal
        for yy in range(10):
            lp = max(prev - balances[yy], 0.0)
            loan_interest[yy] = max(debt[yy] - lp, 0.0)
            prev = balances[yy]

        pack_flows = np.array(f['cashflows_by_weather']['whole_package'])
        pack_eq_flows = np.array(f['cashflows_by_weather']['package_equity'])
        pack_fin_flows = np.array(f['cashflows_by_weather']['package_financed'])

        # Year 0
        self.assertAlmostEqual(pack_eq_flows[0, 0], -total_scope + principal, places=2)
        self.assertAlmostEqual(pack_fin_flows[0, 0], -total_scope, places=2)

        # Years 1..10
        for yy in range(10):
            t = yy + 1
            np.testing.assert_allclose(pack_eq_flows[:, t], pack_flows[:, t] - debt[yy], rtol=1e-5)
            np.testing.assert_allclose(pack_fin_flows[:, t], pack_flows[:, t] - loan_interest[yy], rtol=1e-5)

        # Definitions
        self.assertIn('package equity', f['package_equity']['definition'].lower())
        self.assertIn('package financed', f['package_financed']['definition'].lower())

    def test_zero_loan(self):
        settings = {'south_panels': 12, 'battery_kwh': 5.0, 'loan_mode': 'amount', 'loan_amount': 0.0}
        res = engine.evaluate(settings)
        f = res['finance']
        self.assertEqual(f['loan_principal_eur'], 0.0)
        self.assertEqual(f['solar_loan_principal_eur'], 0.0)

        # With 0 loan: solar_equity equals pv_incremental; package_equity equals whole_package
        proj = np.array(f['cashflows_by_weather']['pv_incremental'])
        solar_eq = np.array(f['cashflows_by_weather']['solar_equity'])
        solar_fin = np.array(f['cashflows_by_weather']['solar_financed'])
        np.testing.assert_allclose(solar_eq, proj)
        np.testing.assert_allclose(solar_fin, proj)

        pack = np.array(f['cashflows_by_weather']['whole_package'])
        pack_eq = np.array(f['cashflows_by_weather']['package_equity'])
        pack_fin = np.array(f['cashflows_by_weather']['package_financed'])
        np.testing.assert_allclose(pack_eq, pack)
        np.testing.assert_allclose(pack_fin, pack)

    def test_zero_apr(self):
        settings = {'south_panels': 10, 'battery_kwh': 5.0, 'loan_apr': 0.0, 'loan_years': 5, 'horizon_years': 5, 'loan_mode': 'amount', 'loan_amount': 5000.0, 'loan_upfront_fees_or_tax': 0.0}
        res = engine.evaluate(settings)
        f = res['finance']
        debt, balances = engine.loan_schedule(5000.0, 0.0, 5, 5)

        # Zero interest throughout
        for y in range(5):
            self.assertAlmostEqual(debt[y], 1000.0, places=2)
            self.assertAlmostEqual(balances[y], 5000.0 - 1000.0 * (y + 1), places=2)

        proj = np.array(f['cashflows_by_weather']['pv_incremental'])
        solar_fin = np.array(f['cashflows_by_weather']['solar_financed'])
        # Financed project subtracts interest (which is 0): operating flows equal proj
        np.testing.assert_allclose(solar_fin[:, 1:], proj[:, 1:])

    def test_amount_and_fraction_modes(self):
        settings_amt = {'south_panels': 10, 'battery_kwh': 0.0, 'loan_mode': 'amount', 'loan_amount': 3500.0, 'loan_upfront_fees_or_tax': 0.0}
        res_amt = engine.evaluate(settings_amt)
        self.assertEqual(res_amt['finance']['loan_principal_eur'], 3500.0)

        capex = res_amt['finance']['capex_eur']
        settings_frac = {'south_panels': 10, 'battery_kwh': 0.0, 'loan_mode': 'fraction', 'loan_fraction': 0.5, 'loan_upfront_fees_or_tax': 0.0}
        res_frac = engine.evaluate(settings_frac)
        self.assertAlmostEqual(res_frac['finance']['loan_principal_eur'], capex * 0.5, places=2)

    def test_horizon_shorter_than_term_settlement(self):
        # 15-year loan with 10-year horizon: remaining balance paid in year 10
        settings = {'south_panels': 10, 'loan_years': 15, 'horizon_years': 10, 'loan_mode': 'amount', 'loan_amount': 5000.0, 'loan_apr': 0.05, 'loan_upfront_fees_or_tax': 0.0}
        res = engine.evaluate(settings)
        f = res['finance']
        principal = f['loan_principal_eur']
        self.assertEqual(principal, 5000.0)
        debt, balances = engine.loan_schedule(principal, 0.05, 15, 10)

        # Final year balances is 0 because remaining balance is settled
        self.assertEqual(balances[9], 0.0)
        # Year 10 payment includes remaining principal balloon
        annual_normal = engine.loan_schedule(principal, 0.05, 15, 15)[0][0]
        self.assertGreater(debt[9], annual_normal)

        cash = res['annual_cashflows']
        self.assertAlmostEqual(cash[10]['debt_service'], debt[9], places=2)

    def test_real_nominal_npv_invariance(self):
        settings_nominal = {'south_panels': 14, 'battery_kwh': 5.0, 'cashflow_mode': 'nominal', 'real_discount_rate': 0.03, 'inflation': 0.02}
        settings_real = {'south_panels': 14, 'battery_kwh': 5.0, 'cashflow_mode': 'real', 'real_discount_rate': 0.03, 'inflation': 0.02}

        res_nom = engine.evaluate(settings_nominal)
        res_real = engine.evaluate(settings_real)

        f_nom = res_nom['finance']
        f_real = res_real['finance']

        for key in ('pv_incremental', 'whole_package', 'equity', 'financed_project', 'solar_equity', 'solar_financed', 'package_equity', 'package_financed'):
            self.assertEqual(f_nom[key]['npv']['median'], f_real[key]['npv']['median'])
            self.assertEqual(f_nom[key]['npv']['min'], f_real[key]['npv']['min'])
            self.assertEqual(f_nom[key]['npv']['max'], f_real[key]['npv']['max'])
            self.assertEqual(f_nom[key]['npv_by_weather'], f_real[key]['npv_by_weather'])

    def test_annual_cashflow_reconciliation_and_median_totals(self):
        settings = {
            'south_panels': 14,
            'battery_kwh': 5.0,
            'load_scenario': 'electrified',
            'loan_include_electrification': True,
            'loan_mode': 'amount',
            'loan_amount': 8000.0,
            'loan_upfront_fees_or_tax': 0.0,
            'loan_apr': 0.05,
            'loan_years': 10,
            'horizon_years': 10
        }
        res = engine.evaluate(settings)
        f = res['finance']
        ratio = f['solar_loan_ratio']
        cash = res['annual_cashflows']

        for y in range(1, 11):
            c = cash[y]
            # Component diagnostic weather means must linearly sum to mean pv_incremental
            component_sum = (
                c['avoided_import']
                + c['net_export']
                + c['cer']
                + c['tax_deduction']
                - c['maintenance']
                - c['replacement']
                + c['residual']
            )
            self.assertAlmostEqual(c['pv_incremental'], component_sum, delta=0.05)

            # Solar equity reconciles to pv_incremental minus allocated debt service
            self.assertAlmostEqual(c['solar_equity'], c['pv_incremental'] - c['debt_service'] * ratio, delta=0.05)
            # Solar financed reconciles to pv_incremental minus allocated interest
            self.assertAlmostEqual(c['solar_financed'], c['pv_incremental'] - c['loan_interest'] * ratio, delta=0.05)

            # Median totals exist and match the envelope range medians
            self.assertEqual(c['pv_incremental_median'], c['pv_incremental_range']['median'])
            self.assertEqual(c['solar_equity_median'], c['solar_equity_range']['median'])
            self.assertEqual(c['whole_package_median'], c['whole_package_range']['median'])
            self.assertEqual(c['package_equity_median'], c['package_equity_range']['median'])

    def test_quote_normalized_settings_consistency(self):
        quote = {
            'line_items': [
                {'net_eur': 4500, 'vat': 0.1, 'quantity': 1},
                {'net_eur': 2500, 'vat': 0.1, 'quantity': 1}
            ],
            'roundtrip_efficiency': 0.92,
            'continuous_charge_kw': 3.5,
            'continuous_discharge_kw': 3.5
        }
        settings = {'south_panels': 14, 'battery_kwh': 5.0}
        s_norm, q, total, pop = engine.normalize_quote(settings, quote)
        self.assertEqual(s_norm['roundtrip_efficiency'], 0.92)
        self.assertEqual(s_norm['charge_kw'], 3.5)
        self.assertEqual(s_norm['discharge_kw'], 3.5)
        self.assertEqual(s_norm['quote_total'], 7700.0)

        # evaluate(s_norm) matches quote_evaluate(settings, quote)['finance']
        eval_norm = engine.evaluate(s_norm)
        quote_eval = engine.quote_evaluate(settings, quote)
        self.assertEqual(eval_norm['finance']['capex_eur'], quote_eval['result']['finance']['capex_eur'])
        self.assertEqual(eval_norm['finance']['pv_incremental']['npv']['median'], quote_eval['result']['finance']['pv_incremental']['npv']['median'])

    def test_electrification_capex_variation_no_loan(self):
        """Increasing conversion capex with no loan increases t0 personal cash outlay by full capex delta."""
        base_s = {
            'south_panels': 10,
            'battery_kwh': 0.0,
            'load_scenario': 'electrified',
            'electrification_capex': 4000.0,
            'gas_disconnect_cost': 150.0,
            'loan_mode': 'amount',
            'loan_amount': 0.0,
        }
        res_a = engine.evaluate(base_s)
        res_b = engine.evaluate({**base_s, 'electrification_capex': 7500.0})

        fa, fb = res_a['finance'], res_b['finance']
        capex = fa['capex_eur']
        conv_a, conv_b = fa['conversion_capex_eur'], fb['conversion_capex_eur']
        delta_conv = conv_b - conv_a
        self.assertEqual(delta_conv, 3500.0)
        self.assertEqual(fa['capex_eur'], fb['capex_eur'])
        self.assertEqual(fa['loan_principal_eur'], 0.0)
        self.assertEqual(fb['loan_principal_eur'], 0.0)
        self.assertEqual(fa['eligible_deduction_capex_eur'], fb['eligible_deduction_capex_eur'])
        self.assertEqual(fa['annual_deduction_eur'], fb['annual_deduction_eur'])

        # t0 personal equity cash outlay changes by full capex delta
        eq_flows_a = np.array(fa['cashflows_by_weather']['equity'])
        eq_flows_b = np.array(fb['cashflows_by_weather']['equity'])
        self.assertAlmostEqual(eq_flows_a[0, 0], -capex - conv_a, places=2)
        self.assertAlmostEqual(eq_flows_b[0, 0], -capex - conv_b, places=2)
        self.assertAlmostEqual(eq_flows_a[0, 0] - eq_flows_b[0, 0], delta_conv, places=2)
        self.assertAlmostEqual(res_a['annual_cashflows'][0]['equity'], -capex - conv_a, places=2)
        self.assertAlmostEqual(res_b['annual_cashflows'][0]['equity'], -capex - conv_b, places=2)

        # Explicit solar-only paths remain strictly identical
        np.testing.assert_allclose(fa['cashflows_by_weather']['pv_incremental'], fb['cashflows_by_weather']['pv_incremental'])
        np.testing.assert_allclose(fa['cashflows_by_weather']['solar_equity'], fb['cashflows_by_weather']['solar_equity'])
        np.testing.assert_allclose(fa['cashflows_by_weather']['solar_financed'], fb['cashflows_by_weather']['solar_financed'])

        # Package equity matches generic equity for active electrified household
        np.testing.assert_allclose(eq_flows_a, np.array(fa['cashflows_by_weather']['package_equity']))
        np.testing.assert_allclose(eq_flows_b, np.array(fb['cashflows_by_weather']['package_equity']))

    def test_electrification_capex_variation_solar_only_loan_scope(self):
        """Increasing conversion capex with solar-only loan changes t0 by full delta; loan principal and deductions unchanged."""
        # 1. Fixed loan amount mode
        base_amt = {
            'south_panels': 10,
            'battery_kwh': 0.0,
            'load_scenario': 'electrified',
            'electrification_capex': 4000.0,
            'gas_disconnect_cost': 150.0,
            'loan_include_electrification': False,
            'loan_mode': 'amount',
            'loan_amount': 5000.0,
            'loan_upfront_fees_or_tax': 0.0,
            'loan_apr': 0.05,
            'loan_years': 8,
            'horizon_years': 8,
        }
        res_amt_a = engine.evaluate(base_amt)
        res_amt_b = engine.evaluate({**base_amt, 'electrification_capex': 7500.0})
        fa, fb = res_amt_a['finance'], res_amt_b['finance']
        capex = fa['capex_eur']
        conv_a, conv_b = fa['conversion_capex_eur'], fb['conversion_capex_eur']
        delta_conv = conv_b - conv_a
        self.assertEqual(delta_conv, 3500.0)

        # Principal is capped by solar capex (or loan_amount) and is unchanged
        principal_expected = min(capex, 5000.0)
        self.assertEqual(fa['loan_principal_eur'], principal_expected)
        self.assertEqual(fb['loan_principal_eur'], principal_expected)
        self.assertEqual(fa['eligible_deduction_capex_eur'], fb['eligible_deduction_capex_eur'])
        self.assertEqual(fa['annual_deduction_eur'], fb['annual_deduction_eur'])

        # t0 changes by full capex delta
        eq_amt_a = np.array(fa['cashflows_by_weather']['equity'])
        eq_amt_b = np.array(fb['cashflows_by_weather']['equity'])
        self.assertAlmostEqual(eq_amt_a[0, 0], -capex - conv_a + principal_expected, places=2)
        self.assertAlmostEqual(eq_amt_b[0, 0], -capex - conv_b + principal_expected, places=2)
        self.assertAlmostEqual(eq_amt_a[0, 0] - eq_amt_b[0, 0], delta_conv, places=2)

        # Annual cash flows after t0 are identical since debt and operational savings are identical
        np.testing.assert_allclose(eq_amt_a[:, 1:], eq_amt_b[:, 1:])

        # 2. Loan fraction mode
        base_frac = {
            'south_panels': 10,
            'battery_kwh': 0.0,
            'load_scenario': 'electrified',
            'electrification_capex': 4000.0,
            'gas_disconnect_cost': 150.0,
            'loan_include_electrification': False,
            'loan_mode': 'fraction',
            'loan_fraction': 0.6,
            'loan_upfront_fees_or_tax': 0.0,
            'loan_apr': 0.05,
            'loan_years': 8,
            'horizon_years': 8,
        }
        res_frac_a = engine.evaluate(base_frac)
        res_frac_b = engine.evaluate({**base_frac, 'electrification_capex': 7500.0})
        fa_frac, fb_frac = res_frac_a['finance'], res_frac_b['finance']
        expected_frac_principal = capex * 0.6
        self.assertAlmostEqual(fa_frac['loan_principal_eur'], expected_frac_principal, places=2)
        self.assertAlmostEqual(fb_frac['loan_principal_eur'], expected_frac_principal, places=2)
        self.assertEqual(fa_frac['annual_deduction_eur'], fb_frac['annual_deduction_eur'])

        eq_frac_a = np.array(fa_frac['cashflows_by_weather']['equity'])
        eq_frac_b = np.array(fb_frac['cashflows_by_weather']['equity'])
        self.assertAlmostEqual(eq_frac_a[0, 0], -capex - conv_a + expected_frac_principal, places=2)
        self.assertAlmostEqual(eq_frac_b[0, 0], -capex - conv_b + expected_frac_principal, places=2)
        self.assertAlmostEqual(eq_frac_a[0, 0] - eq_frac_b[0, 0], delta_conv, places=2)

    def test_electrification_capex_variation_borrowing_conversion_and_full_financing(self):
        """When conversion borrowing is enabled or full financing is used, increased capex can legitimately be covered by loan."""
        # 1. Partial fraction financing with borrowing conversion enabled
        base_frac = {
            'south_panels': 10,
            'battery_kwh': 0.0,
            'load_scenario': 'electrified',
            'electrification_capex': 4000.0,
            'gas_disconnect_cost': 150.0,
            'loan_include_electrification': True,
            'loan_mode': 'fraction',
            'loan_fraction': 0.5,
            'loan_upfront_fees_or_tax': 0.0,
            'loan_apr': 0.04,
            'loan_years': 10,
            'horizon_years': 10,
        }
        res_a = engine.evaluate(base_frac)
        res_b = engine.evaluate({**base_frac, 'electrification_capex': 7500.0})
        fa, fb = res_a['finance'], res_b['finance']
        delta_conv = 3500.0

        # Loan principal expands by fraction * delta_conv
        self.assertAlmostEqual(fb['loan_principal_eur'] - fa['loan_principal_eur'], 0.5 * delta_conv, places=2)
        # t0 personal cash outlay increases by remaining (1 - fraction) * delta_conv
        eq_a = np.array(fa['cashflows_by_weather']['equity'])
        eq_b = np.array(fb['cashflows_by_weather']['equity'])
        self.assertAlmostEqual(eq_a[0, 0] - eq_b[0, 0], 0.5 * delta_conv, places=2)
        self.assertEqual(fa['annual_deduction_eur'], fb['annual_deduction_eur'])

        # 2. 100% full financing edge case
        full_frac = {**base_frac, 'loan_fraction': 1.0}
        res_full_a = engine.evaluate(full_frac)
        res_full_b = engine.evaluate({**full_frac, 'electrification_capex': 7500.0})
        fa_full, fb_full = res_full_a['finance'], res_full_b['finance']

        # Loan covers 100% of capex increase
        self.assertAlmostEqual(fb_full['loan_principal_eur'] - fa_full['loan_principal_eur'], delta_conv, places=2)
        eq_full_a = np.array(fa_full['cashflows_by_weather']['equity'])
        eq_full_b = np.array(fb_full['cashflows_by_weather']['equity'])
        self.assertAlmostEqual(eq_full_a[0, 0], 0.0, places=2)
        self.assertAlmostEqual(eq_full_b[0, 0], 0.0, places=2)

        # 3. Fixed amount full-financing edge case (amount comfortably exceeds total scope)
        amt_full = {**base_frac, 'loan_mode': 'amount', 'loan_amount': 50000.0}
        res_amt_full_a = engine.evaluate(amt_full)
        res_amt_full_b = engine.evaluate({**amt_full, 'electrification_capex': 7500.0})
        fa_amt_full, fb_amt_full = res_amt_full_a['finance'], res_amt_full_b['finance']
        self.assertAlmostEqual(fb_amt_full['loan_principal_eur'] - fa_amt_full['loan_principal_eur'], delta_conv, places=2)
        eq_amt_full_a = np.array(fa_amt_full['cashflows_by_weather']['equity'])
        eq_amt_full_b = np.array(fb_amt_full['cashflows_by_weather']['equity'])
        self.assertAlmostEqual(eq_amt_full_a[0, 0], 0.0, places=2)
        self.assertAlmostEqual(eq_amt_full_b[0, 0], 0.0, places=2)

    def test_baseline_load_scenario_zero_conversion_costs(self):
        """Baseline load scenario strictly isolates zero conversion costs in equity and package metrics."""
        s_base_0 = {
            'south_panels': 10,
            'battery_kwh': 0.0,
            'load_scenario': 'baseline',
            'electrification_capex': 0.0,
            'gas_disconnect_cost': 0.0,
            'loan_mode': 'fraction',
            'loan_fraction': 0.5,
            'loan_upfront_fees_or_tax': 0.0,
        }
        s_base_high = {
            **s_base_0,
            'electrification_capex': 8000.0,
            'gas_disconnect_cost': 500.0,
            'loan_include_electrification': True,
        }
        res_0 = engine.evaluate(s_base_0)
        res_high = engine.evaluate(s_base_high)
        f0, fhigh = res_0['finance'], res_high['finance']

        self.assertEqual(f0['conversion_capex_eur'], 0.0)
        self.assertEqual(fhigh['conversion_capex_eur'], 0.0)
        self.assertEqual(f0['loan_principal_eur'], fhigh['loan_principal_eur'])
        self.assertEqual(f0['annual_deduction_eur'], fhigh['annual_deduction_eur'])

        eq_0 = np.array(f0['cashflows_by_weather']['equity'])
        eq_high = np.array(fhigh['cashflows_by_weather']['equity'])
        np.testing.assert_allclose(eq_0, eq_high)
        self.assertEqual(res_0['annual_cashflows'][0]['equity'], res_high['annual_cashflows'][0]['equity'])
        self.assertEqual(res_0['metrics']['equity_npv_eur']['median'], res_high['metrics']['equity_npv_eur']['median'])

    def test_equity_definition_present(self):
        """Verify that finance['equity'] carries an explanatory definition string."""
        res = engine.evaluate({'south_panels': 10, 'battery_kwh': 0.0})
        f = res['finance']
        self.assertIn('definition', f['equity'])
        self.assertIn('equity', f['equity']['definition'].lower())
        self.assertIn('debt service', f['equity']['definition'].lower())

    def test_loan_schedule_maturity_beyond_horizon_discounted_sum(self):
        """Loan schedule truncating before maturity settles terminal debt balance in the final horizon year."""
        principal = 24000.0
        apr = 0.05
        loan_years = 15
        horizon_years = 10

        payments, balances = engine.loan_schedule(principal, apr, loan_years, horizon_years)
        self.assertEqual(len(payments), horizon_years)
        self.assertEqual(len(balances), horizon_years)
        self.assertEqual(balances[-1], 0.0)

        # Independently calculate regular monthly payment and balance at month 120
        monthly_r = apr / 12.0
        n_months_total = loan_years * 12
        pmt_monthly = principal * (monthly_r * (1 + monthly_r)**n_months_total) / ((1 + monthly_r)**n_months_total - 1)
        
        # Balance after 120 months (10 years)
        n_months_horizon = horizon_years * 12
        balance_10yr = principal * ((1 + monthly_r)**n_months_total - (1 + monthly_r)**n_months_horizon) / ((1 + monthly_r)**n_months_total - 1)

        # In horizon year 10, annual payment should be 12 regular monthly payments + remaining terminal balance
        regular_annual_pmt = pmt_monthly * 12.0
        self.assertAlmostEqual(payments[0], regular_annual_pmt, places=2)
        self.assertAlmostEqual(payments[-1], regular_annual_pmt + balance_10yr, places=2)

        # Full loan schedule without truncation
        full_payments, full_balances = engine.loan_schedule(principal, apr, loan_years, loan_years)
        self.assertEqual(len(full_payments), loan_years)
        self.assertAlmostEqual(full_balances[horizon_years - 1], balance_10yr, places=2)

    def test_zero_borrowing_project_equals_equity_solar_scope(self):
        """Zero borrowing strictly equates pv_incremental and solar_equity across all weather scenarios."""
        settings = {
            'south_panels': 14,
            'battery_kwh': 5.0,
            'loan_fraction': 0.0,
            'loan_amount': 0.0,
            'loan_mode': 'fraction',
            'horizon_years': 20,
            'real_discount_rate': 0.03,
            'inflation': 0.02
        }
        res = engine.evaluate(settings)
        f = res['finance']

        self.assertEqual(f['loan_principal_eur'], 0.0)
        self.assertEqual(f['solar_loan_principal_eur'], 0.0)
        self.assertEqual(f['solar_loan_ratio'], 1.0)
        self.assertEqual(f['initial_solar_equity_eur'], f['capex_eur'])

        proj_flows = np.array(f['cashflows_by_weather']['pv_incremental'])
        eq_flows = np.array(f['cashflows_by_weather']['solar_equity'])
        np.testing.assert_allclose(proj_flows, eq_flows, rtol=1e-6)

        self.assertAlmostEqual(f['pv_incremental']['npv']['median'], f['solar_equity']['npv']['median'], places=4)
        self.assertAlmostEqual(f['pv_incremental']['npv']['min'], f['solar_equity']['npv']['min'], places=4)
        self.assertAlmostEqual(f['pv_incremental']['npv']['max'], f['solar_equity']['npv']['max'], places=4)
        self.assertEqual(f['pv_incremental']['discounted_payback']['median'], f['solar_equity']['discounted_payback']['median'])

    def test_package_loan_allocation_and_no_double_deduction(self):
        """Under package loan, capex and principal are not double deducted and allocation follows min(principal, capex)."""
        settings = {
            'south_panels': 12,
            'battery_kwh': 5.0,
            'load_scenario': 'electrified',
            'loan_include_electrification': True,
            'loan_mode': 'amount',
            'loan_amount': 15000.0,
            'loan_upfront_fees_or_tax': 0.0,
            'loan_apr': 0.045,
            'loan_years': 10,
            'horizon_years': 10
        }
        res = engine.evaluate(settings)
        f = res['finance']

        capex = f['capex_eur']
        conv = f['conversion_capex_eur']
        total_scope = capex + conv
        principal = min(total_scope, 15000.0)

        self.assertEqual(f['total_loan_scope_eur'], total_scope)
        self.assertEqual(f['loan_principal_eur'], principal)
        
        solar_principal = min(principal, capex)
        self.assertEqual(f['solar_loan_principal_eur'], solar_principal)
        expected_ratio = solar_principal / principal
        self.assertAlmostEqual(f['solar_loan_ratio'], expected_ratio, places=6)
        
        # Year 0 solar equity outlay is -(capex - solar_principal)
        expected_t0_outlay = -(capex - solar_principal)
        solar_eq_flows = np.array(f['cashflows_by_weather']['solar_equity'])
        self.assertAlmostEqual(solar_eq_flows[0, 0], expected_t0_outlay, places=4)
        self.assertEqual(f['initial_solar_equity_eur'], capex - solar_principal)

        # Clamping test: loan_amount exceeds total scope
        over_settings = {**settings, 'loan_amount': 99999.0}
        res_over = engine.evaluate(over_settings)
        f_over = res_over['finance']
        self.assertEqual(f_over['loan_principal_eur'], total_scope)
        self.assertEqual(f_over['solar_loan_principal_eur'], capex)
        self.assertEqual(f_over['initial_solar_equity_eur'], 0.0)


if __name__ == '__main__':
    unittest.main()

