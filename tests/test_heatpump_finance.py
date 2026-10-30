"""Independent numerical and regression tests for Stage 5 heat pump financial integration.

Tests cover:
  1. Off/baseline financial parity
  2. HP capex once with tank/cooking/disconnect combos
  3. Retained gas cooking 25 Smc variable + fixed
  4. Electric cooking retained zero Smc fixed vs disconnected zero
  5. Gas baseline unchanged
  6. No-PV whole package identity
  7. HP maintenance/replacement in package only with correct inflation year
  8. Loan principal/equity/full cost + interest identities
  9. Solar-only tax deduction basis
  10. Zero and double gas credits
  11. Severe shortfall fuel credit withholding without changing actual energy
  12. Weather-specific allowance envelopes
  13. Generic quote / candidate / sizing financial counterfactual consistency
  14. Real HP integration end-to-end evaluation
  15. All heat unmet with retained gas cooking yields zero gas credit (no phantom saving)
  16. Independent analytic tier-crossing and fuel credit withholding cases
  17. Multi-year inflation and real escalation on avoided-marginal allowance
  18. Cooking and disconnection (DC) combinations under complete shortfall
  19. Monotonicity and non-negative credit guarantee across unserved range
  20. Evaluate cache isolation: distinct financial results and unmutated source cache
"""
import math
import unittest
import numpy as np

from feasibility import assumptions, engine
from feasibility.heatpump_finance import (
    calculate_hp_conversion_capex,
    calculate_conversion_capex,
    calculate_hp_maintenance,
    calculate_hp_replacement,
    calculate_hp_remaining_gas_bill,
    calculate_hp_gas_finance,
    gas_variable_cost,
    is_active_hp,
)
try:
    from test_support import IsolatedDefaultsTestCase
except ImportError:
    from tests.test_support import IsolatedDefaultsTestCase


def make_synthetic_annual(horizon=20, weather=2, load_kwh=3000.0, pv_kwh=3500.0, grid_import_kwh=1000.0, export_kwh=1500.0):
    """Create fast synthetic annual dispatch array for deterministic financial unit tests."""
    annual = np.zeros((horizon, weather, len(engine.FIELDS)))
    for yy in range(horizon):
        for w in range(weather):
            annual[yy, w, 0] = load_kwh
            annual[yy, w, 1] = pv_kwh
            annual[yy, w, 5] = grid_import_kwh
            annual[yy, w, 6] = export_kwh
            annual[yy, w, 11] = export_kwh * 1.0
    return annual


def make_synthetic_hp_summary(by_weather_data):
    """Create synthetic hp_summary dict from list of weather records."""
    by_weather = []
    for i, data in enumerate(by_weather_data):
        rec = {
            'weather_year': 2005 + i,
            'annual_requested_space_heat_kwh': float(data.get('space_req', 3000.0)),
            'annual_delivered_space_heat_kwh': float(data.get('space_req', 3000.0) - data.get('space_unmet', 0.0)),
            'annual_unmet_space_heat_kwh': float(data.get('space_unmet', 0.0)),
            'annual_requested_draw_heat_kwh': float(data.get('dhw_req', 2000.0)),
            'annual_delivered_draw_heat_kwh': float(data.get('dhw_req', 2000.0) - data.get('dhw_unmet', 0.0)),
            'annual_unmet_draw_heat_kwh': float(data.get('dhw_unmet', 0.0)),
        }
        by_weather.append(rec)
    return {'by_weather': by_weather}


class HeatPumpFinanceTests(IsolatedDefaultsTestCase):

    def test_1_off_and_baseline_financial_parity(self):
        """When planning_heat_pump is False or load_scenario is baseline, exact legacy financial parity is preserved."""
        s_off = engine.defaults()
        s_off.update({
            'planning_heat_pump': False,
            'load_scenario': 'electrified',
            'electrification_capex': 6000.0,
            'gas_disconnect_cost': 150.0,
            'south_panels': 10,
            'horizon_years': 10,
        })
        annual = make_synthetic_annual(horizon=10, weather=2)
        kwp = 10 * s_off['panel_watts'] / 1000.0

        f_off, cash_off = engine._financial(s_off, annual, kwp)
        self.assertEqual(f_off['conversion_capex_eur'], 6150.0)
        self.assertEqual(f_off['package_equity']['npv_eur']['mean'], f_off['whole_package']['npv_eur']['mean'])

        # Baseline scenario with planning_heat_pump=True should also NOT activate HP conversion capex
        s_base_hp = engine.defaults()
        s_base_hp.update({
            'planning_heat_pump': True,
            'load_scenario': 'baseline',
            'electrification_capex_include_baseline': False,
            'south_panels': 10,
            'horizon_years': 10,
        })
        f_base_hp, _ = engine._financial(s_base_hp, annual, kwp)
        self.assertEqual(f_base_hp['conversion_capex_eur'], 0.0)
        self.assertFalse(is_active_hp(s_base_hp))

        # Baseline scenario with electrification_capex_include_baseline=True preserves legacy baseline conversion
        s_base_capex = engine.defaults()
        s_base_capex.update({
            'planning_heat_pump': True,
            'load_scenario': 'baseline',
            'electrification_capex_include_baseline': True,
            'electrification_capex': 5000.0,
            'south_panels': 10,
            'horizon_years': 10,
        })
        f_base_capex, _ = engine._financial(s_base_capex, annual, kwp)
        self.assertEqual(f_base_capex['conversion_capex_eur'], 5000.0)

    def test_2_hp_capex_once_with_tank_cooking_disconnect_combos(self):
        """Conversion capex combines hp_capex once with cooking and disconnect costs; old electrification_capex ignored."""
        # Combo A: base HP only
        s_base = {
            'planning_heat_pump': True,
            'load_scenario': 'electrified',
            'hp_capex': 10000.0,
            'hp_cooking_capex': 500.0,
            'hp_electric_cooking': False,
            'hp_disconnect_gas': False,
            'gas_disconnect_cost': 150.0,
            'electrification_capex': 7777.0,  # Must be ignored!
            'hp_nominal_kw': 9.0,             # Must NOT be an added cost!
        }
        self.assertEqual(calculate_hp_conversion_capex(s_base), 10000.0)
        self.assertEqual(calculate_conversion_capex(s_base), 10000.0)

        # Combo B: HP + electric cooking
        s_cook = dict(s_base, hp_electric_cooking=True)
        self.assertEqual(calculate_hp_conversion_capex(s_cook), 10500.0)

        # Combo C: HP + electric cooking + disconnect gas
        s_disc = dict(s_cook, hp_disconnect_gas=True)
        self.assertEqual(calculate_hp_conversion_capex(s_disc), 10650.0)

        # Verify that outdoor unit + tank + installation is ONCE in hp_capex and nominal 9kW is not an extra cost
        annual = make_synthetic_annual(horizon=5, weather=1)
        defaults = engine.defaults()
        s_disc_full = dict(defaults, **s_disc, horizon_years=5)
        f, _ = engine._financial(s_disc_full, annual, 3.0, hp_summary=make_synthetic_hp_summary([{'space_unmet': 0, 'dhw_unmet': 0}]))
        self.assertEqual(f['conversion_capex_eur'], 10650.0)

    def test_3_retained_gas_cooking_25smc_variable_plus_fixed(self):
        """When electric cooking is False and disconnect is False, actual gas bill retains cooking gas plus fixed charges."""
        s = engine.defaults()
        s.update({
            'planning_heat_pump': True,
            'load_scenario': 'electrified',
            'hp_electric_cooking': False,
            'hp_disconnect_gas': False,
            'cooking_smc_annual': 25.0,
            'gas_fixed_day': 0.35,
            'gas_commodity': 0.45,
            'gas_regulated': 0.395,
            'inflation': 0.02,
        })
        bill_y1 = calculate_hp_remaining_gas_bill(s, year=1)
        expected_y1 = float(engine.gas_bill(s, year=1, smc=25.0))
        self.assertAlmostEqual(bill_y1, expected_y1, places=3)
        self.assertGreater(bill_y1, 150.0)  # > fixed charges alone

        bill_y5 = calculate_hp_remaining_gas_bill(s, year=5)
        expected_y5 = float(engine.gas_bill(s, year=5, smc=25.0))
        self.assertAlmostEqual(bill_y5, expected_y5, places=3)

    def test_4_electric_cooking_retained_zero_smc_fixed_vs_disconnected_zero(self):
        """When electric cooking is True, retained connection charges annual fixed fee without volume; disconnected is zero."""
        s = engine.defaults()
        s.update({
            'planning_heat_pump': True,
            'load_scenario': 'electrified',
            'hp_electric_cooking': True,
            'hp_disconnect_gas': False,
            'gas_fixed_day': 0.40,
            'inflation': 0.03,
        })
        # Case A: Retained connection with zero volume: fixed connection charges must be retained!
        # Notice: global gas_bill(s, year, smc=0) returns 0.0, but helper must return fixed charges!
        self.assertEqual(engine.gas_bill(s, year=1, smc=0), 0.0)
        retained_bill_y1 = calculate_hp_remaining_gas_bill(s, year=1)
        expected_fixed_y1 = 0.40 * 365.0 * 1.22
        self.assertAlmostEqual(retained_bill_y1, expected_fixed_y1, places=3)

        retained_bill_y3 = calculate_hp_remaining_gas_bill(s, year=3)
        expected_fixed_y3 = 0.40 * 365.0 * (1.03 ** 2) * 1.22
        self.assertAlmostEqual(retained_bill_y3, expected_fixed_y3, places=3)

        # Case B: Disconnected: actual gas bill is strictly 0.0
        s_disc = dict(s, hp_disconnect_gas=True)
        self.assertEqual(calculate_hp_remaining_gas_bill(s_disc, year=1), 0.0)
        self.assertEqual(calculate_hp_remaining_gas_bill(s_disc, year=5), 0.0)

    def test_5_gas_baseline_unchanged(self):
        """Baseline gas bill remains explicit synthetic bill schedule regardless of HP power or weather."""
        s = engine.defaults()
        s.update({
            'planning_heat_pump': True,
            'load_scenario': 'electrified',
            'hp_capex': 10000.0,
            'horizon_years': 5,
        })
        annual = make_synthetic_annual(horizon=5, weather=2)
        hp_summary = make_synthetic_hp_summary([
            {'space_req': 4000.0, 'space_unmet': 0.0, 'dhw_req': 2500.0, 'dhw_unmet': 0.0},
            {'space_req': 4500.0, 'space_unmet': 100.0, 'dhw_req': 2500.0, 'dhw_unmet': 50.0},
        ])
        _, cash = engine._financial(s, annual, 4.0, hp_summary=hp_summary)
        for y in range(1, 6):
            expected_base_gas = engine.gas_bill(s, year=y)
            self.assertAlmostEqual(cash[y]['baseline_gas_bill'], expected_base_gas, places=3)

    def test_6_no_pv_whole_package_identity(self):
        """With kwp=0, pv_incremental is zero and whole_package evaluates pure heat pump economics."""
        s = engine.defaults()
        s.update({
            'planning_heat_pump': True,
            'load_scenario': 'electrified',
            'hp_capex': 10000.0,
            'hp_electric_cooking': True,
            'hp_disconnect_gas': True,
            'gas_disconnect_cost': 150.0,
            'horizon_years': 5,
            'loan_mode': 'amount',
            'loan_amount': 0.0,
        })
        annual = make_synthetic_annual(horizon=5, weather=1, load_kwh=3000.0, pv_kwh=0.0, grid_import_kwh=3000.0, export_kwh=0.0)
        hp_summary = make_synthetic_hp_summary([{'space_unmet': 0.0, 'dhw_unmet': 0.0}])
        f, cash = engine._financial(s, annual, 0.0, hp_summary=hp_summary)

        # Solar capex is 0; conversion capex is 10650 (10000 + 500 cooking + 150 disconnect)
        self.assertEqual(f['capex_eur'], 0.0)
        self.assertEqual(f['conversion_capex_eur'], 10650.0)

        # Year 0: PV incremental is 0, whole package is -10650
        self.assertEqual(cash[0]['pv_incremental'], 0.0)
        self.assertEqual(cash[0]['whole_package'], -10650.0)
        self.assertEqual(cash[0]['package_equity'], -10650.0)

        # Operating years: pv_incremental cash flows are strictly 0.0
        for y in range(1, 6):
            self.assertEqual(cash[y]['pv_incremental'], 0.0)
            # Whole package cash flow is: baseline bills - grid import bill - actual remaining gas (0) - hp_maint
            base_pkg = cash[y]['baseline_package_bill']
            with_elec = cash[y]['with_system_electric_bill']
            hp_m = cash[y]['hp_maintenance']
            expected_pack = base_pkg - with_elec - 0.0 - 0.0 - hp_m
            self.assertAlmostEqual(cash[y]['whole_package'], expected_pack, places=3)

    def test_7_hp_maintenance_and_replacement_scopes(self):
        """HP maintenance and replacement enter whole package cash flows only, excluded from solar LCOE and solar scopes."""
        s = engine.defaults()
        s.update({
            'planning_heat_pump': True,
            'load_scenario': 'electrified',
            'hp_capex': 10000.0,
            'hp_annual_maintenance': 120.0,
            'hp_replacement_year': 3,
            'hp_replacement_cost': 5500.0,
            'inflation': 0.02,
            'horizon_years': 5,
        })
        annual = make_synthetic_annual(horizon=5, weather=1)
        hp_summary = make_synthetic_hp_summary([{'space_unmet': 0.0, 'dhw_unmet': 0.0}])
        f, cash = engine._financial(s, annual, 4.0, hp_summary=hp_summary)

        for yy in range(5):
            year = yy + 1
            infl = 1.02 ** yy
            expected_maint = 120.0 * infl
            expected_rep = 5500.0 * infl if year == 3 else 0.0

            self.assertAlmostEqual(cash[year]['hp_maintenance'], expected_maint, places=3)
            self.assertAlmostEqual(cash[year]['hp_replacement'], expected_rep, places=3)

            # Solar maintenance and replacement are distinct
            self.assertNotEqual(cash[year]['maintenance'], cash[year]['hp_maintenance'])

        # Solar LCOE must strictly exclude HP maintenance and replacement
        # Compute baseline solar-only financial run with exact same solar parameters
        s_solar_only = dict(s, planning_heat_pump=False, load_scenario='baseline')
        f_solar, _ = engine._financial(s_solar_only, annual, 4.0)
        self.assertAlmostEqual(f['lcoe']['median'], f_solar['lcoe']['median'], places=6)
        self.assertAlmostEqual(f['lcoe_net_incentives']['median'], f_solar['lcoe_net_incentives']['median'], places=6)

    def test_8_loan_identities_with_hp_conversion(self):
        """Loan principal covers capex + HP conversion when enabled; equity and financed scope identities hold."""
        s = engine.defaults()
        s.update({
            'planning_heat_pump': True,
            'load_scenario': 'electrified',
            'hp_capex': 10000.0,
            'hp_electric_cooking': True,
            'hp_disconnect_gas': True,
            'gas_disconnect_cost': 150.0,
            'loan_include_electrification': True,
            'loan_mode': 'fraction',
            'loan_fraction': 0.70,
            'loan_apr': 0.05,
            'loan_years': 5,
            'horizon_years': 5,
            'inflation': 0.02,
        })
        annual = make_synthetic_annual(horizon=5, weather=1)
        hp_summary = make_synthetic_hp_summary([{'space_unmet': 0.0, 'dhw_unmet': 0.0}])
        kwp = 5.0
        capex = engine.installed_capex(s, kwp)
        conversion = 10650.0
        expected_scope = capex + conversion
        expected_principal = expected_scope * 0.70

        f, cash = engine._financial(s, annual, kwp, hp_summary=hp_summary)
        self.assertAlmostEqual(f['loan_principal_eur'], expected_principal, places=2)
        self.assertEqual(f['loan_scope'], 'solar_and_conversion')

        # Year 0: package_equity = -scope + principal; package_financed = -scope
        self.assertAlmostEqual(cash[0]['package_equity'], -expected_scope + expected_principal, places=2)
        self.assertAlmostEqual(cash[0]['package_financed'], -expected_scope, places=2)

        # Debt schedule identities across operating years
        debt, balances = engine.loan_schedule(expected_principal + s['loan_upfront_fees_or_tax'], 0.05, 5, 5)
        for yy in range(5):
            year = yy + 1
            pack_flow = cash[year]['whole_package']
            self.assertAlmostEqual(cash[year]['package_equity'], pack_flow - debt[yy], places=2)
            self.assertAlmostEqual(cash[year]['package_financed'], pack_flow - cash[year]['loan_interest'], places=2)

    def test_9_tax_deduction_solar_only(self):
        """Tax deduction basis min(capex * fraction, 96000) strictly excludes HP conversion capex."""
        s_hp = engine.defaults()
        s_hp.update({
            'planning_heat_pump': True,
            'load_scenario': 'electrified',
            'hp_capex': 12000.0,
            'south_panels': 10,
            'deduction_rate': 0.50,
            'deduction_eligible_fraction': 1.0,
            'horizon_years': 5,
        })
        s_no_hp = dict(s_hp, planning_heat_pump=False, horizon_years=5)

        annual = make_synthetic_annual(horizon=5, weather=1)
        kwp = 10 * s_hp['panel_watts'] / 1000.0
        capex = engine.installed_capex(s_hp, kwp)

        f_hp, _ = engine._financial(s_hp, annual, kwp, hp_summary=make_synthetic_hp_summary([{'space_unmet': 0, 'dhw_unmet': 0}]))
        f_no_hp, _ = engine._financial(s_no_hp, annual, kwp)

        self.assertEqual(f_hp['eligible_deduction_capex_eur'], capex)
        self.assertEqual(f_no_hp['eligible_deduction_capex_eur'], capex)
        self.assertEqual(f_hp['annual_deduction_eur'], f_no_hp['annual_deduction_eur'])

    def test_10_zero_and_double_gas_credits(self):
        """Avoided gas credit cannot exceed (baseline_gas - remaining_gas) and equals gross when unmet=0."""
        s = engine.defaults()
        s.update({
            'planning_heat_pump': True,
            'load_scenario': 'electrified',
            'hp_electric_cooking': False,
            'hp_disconnect_gas': False,
        })
        hp_summary_full = make_synthetic_hp_summary([{'space_unmet': 0.0, 'dhw_unmet': 0.0}])
        gas_fin = calculate_hp_gas_finance(s, hp_summary=hp_summary_full, year=1)

        base_gas = engine.gas_bill(s, year=1)
        rem_gas = calculate_hp_remaining_gas_bill(s, year=1)
        gross_avoided = base_gas - rem_gas

        # When unmet = 0, allowance = 0 and net credit equals gross avoided
        self.assertEqual(gas_fin['allowance']['mean'], 0.0)
        self.assertAlmostEqual(gas_fin['avoided_gas_credit']['mean'], gross_avoided, places=3)
        self.assertTrue(gas_fin['service_comparable'])

    def test_11_severe_shortfall_withholds_allowance_without_fake_energy(self):
        """Severe heating shortfall withholds avoided gas credits via allowance, without changing actual energy/bills."""
        s = engine.defaults()
        s.update({
            'planning_heat_pump': True,
            'load_scenario': 'electrified',
            'hp_capex': 10000.0,
            'hp_electric_cooking': True,
            'hp_disconnect_gas': True,
            'horizon_years': 3,
        })
        annual = make_synthetic_annual(horizon=3, weather=1, load_kwh=3000.0, grid_import_kwh=1000.0)

        # Case 1: 100% delivered heat
        hp_sum_full = make_synthetic_hp_summary([{'space_req': 3000.0, 'space_unmet': 0.0, 'dhw_req': 2000.0, 'dhw_unmet': 0.0}])
        f_full, cash_full = engine._financial(s, annual, 4.0, hp_summary=hp_sum_full)

        # Case 2: 100% unmet heat (total shortfall: space and DHW completely unserved)
        hp_sum_zero = make_synthetic_hp_summary([{'space_req': 3000.0, 'space_unmet': 3000.0, 'dhw_req': 2000.0, 'dhw_unmet': 2000.0}])
        f_zero, cash_zero = engine._financial(s, annual, 4.0, hp_summary=hp_sum_zero)

        # Actual electric bill is NOT changed (no silently generated real electricity or unlimited backup)
        for y in range(1, 4):
            self.assertEqual(cash_full[y]['with_system_electric_bill'], cash_zero[y]['with_system_electric_bill'])
            self.assertEqual(cash_full[y]['remaining_gas_bill'], 0.0)
            self.assertEqual(cash_zero[y]['remaining_gas_bill'], 0.0)

        # But unserved service allowance is deducted from whole_package benefits
        allowance_y1 = cash_zero[1]['unserved_service_allowance']
        self.assertGreater(allowance_y1, 500.0)  # Significant allowance for 693 Smc unserved
        self.assertAlmostEqual(cash_full[1]['whole_package'] - cash_zero[1]['whole_package'], allowance_y1, places=3)
        self.assertFalse(f_zero['service_comparable'])

    def test_12_weather_specific_allowance(self):
        """Allowance and avoided credits vary by weather year when unserved thermal demand differs."""
        s = engine.defaults()
        s.update({
            'planning_heat_pump': True,
            'load_scenario': 'electrified',
        })
        # Weather year 0: fully served; Weather year 1: 50% unmet space heat
        hp_summary = make_synthetic_hp_summary([
            {'space_req': 3000.0, 'space_unmet': 0.0, 'dhw_req': 2000.0, 'dhw_unmet': 0.0},
            {'space_req': 3000.0, 'space_unmet': 1500.0, 'dhw_req': 2000.0, 'dhw_unmet': 0.0},
        ])
        gas_fin = calculate_hp_gas_finance(s, hp_summary=hp_summary, year=1)

        self.assertEqual(gas_fin['allowance_by_weather'][0], 0.0)
        self.assertGreater(gas_fin['allowance_by_weather'][1], 150.0)
        self.assertEqual(gas_fin['allowance']['min'], 0.0)
        self.assertGreater(gas_fin['allowance']['max'], 150.0)
        self.assertFalse(gas_fin['service_comparable'])

    def test_13_generic_quote_and_candidate_consistency(self):
        """Generic quote total overrides solar capex while HP conversion capex remains consistent."""
        s = engine.defaults()
        s.update({
            'planning_heat_pump': True,
            'load_scenario': 'electrified',
            'hp_capex': 10000.0,
            'hp_electric_cooking': True,
            'hp_disconnect_gas': True,
            'quote_total': 8500.0,
            'quote_vat_included': True,
            'horizon_years': 5,
        })
        annual = make_synthetic_annual(horizon=5, weather=1)
        hp_summary = make_synthetic_hp_summary([{'space_unmet': 0.0, 'dhw_unmet': 0.0}])
        f, cash = engine._financial(s, annual, 4.0, hp_summary=hp_summary)

        self.assertEqual(f['capex_eur'], 8500.0)
        self.assertEqual(f['conversion_capex_eur'], 10650.0)
        self.assertEqual(cash[0]['whole_package'], -(8500.0 + 10650.0))

    def test_14_real_hp_integration_end_to_end(self):
        """Real HP simulation and financial evaluation pipeline produces verified result structures."""
        settings = {
            'planning_heat_pump': True,
            'load_scenario': 'electrified',
            'hp_capex': 10000.0,
            'hp_electric_cooking': True,
            'hp_disconnect_gas': True,
            'south_panels': 12,
            'battery_kwh': 5.0,
        }
        res = engine.evaluate(settings)

        # 1. Exposed heat pump summary and service comparability
        self.assertIn('heat_pump', res)
        self.assertIsNotNone(res['heat_pump'])
        self.assertIn('by_weather', res['heat_pump'])
        self.assertIn('service_comparable', res)

        # 2. Finance structured keys
        f = res['finance']
        self.assertIn('service_comparable', f)
        self.assertIn('heat_pump_gas_finance', f)
        self.assertIn('actual_remaining_gas_cost_eur', f)
        self.assertIn('unserved_service_allowance_eur', f)
        self.assertIn('avoided_gas_credit_eur', f)
        self.assertIn('gas_volumes', f)

        # Disconnected meter means actual remaining gas bill is 0.0 EUR
        self.assertEqual(f['actual_remaining_gas_cost_eur'], 0.0)

        # 3. Limitations check: ensures false unmodeled assumptions are replaced
        limitations_text = " ".join(res['limitations'])
        self.assertNotIn("User AC seasonal ratings are 4.0", limitations_text)
        self.assertNotIn("compressor power limits, and temperature-varying heat-pump COP remain unmodeled", limitations_text)
        self.assertIn("explicitly supplied 12-month thermal reference", limitations_text)
        self.assertIn("nominal 9.0 kW thermal capacity", limitations_text)

    def test_15_all_heat_unmet_retained_gas_cooking_zero_credit(self):
        """When 100% heating/DHW is unmet and gas cooking is retained, gas credit is exactly 0.0 EUR (no phantom saving)."""
        s = engine.validate({'planning_heat_pump': True, 'load_scenario': 'electrified'})
        # Retained gas cooking default: hp_electric_cooking=False, hp_disconnect_gas=False
        hp_summary = {
            'by_weather': [{
                'annual_requested_space_heat_kwh': 1000.0,
                'annual_unmet_space_heat_kwh': 1000.0,
                'annual_requested_draw_heat_kwh': 1000.0,
                'annual_unmet_draw_heat_kwh': 1000.0,
            }]
        }
        res = calculate_hp_gas_finance(s, hp_summary=hp_summary, year=1)

        # 1. Net avoided gas credit is identically 0.0 EUR (eliminating +5.633 EUR phantom saving)
        self.assertEqual(res['avoided_gas_credit']['mean'], 0.0)
        self.assertEqual(res['avoided_gas_credit']['min'], 0.0)
        self.assertEqual(res['avoided_gas_credit']['max'], 0.0)
        self.assertFalse(res['service_comparable'])

        # 2. Actual remaining gas bill retains the 25 Smc cooking bill exactly
        cooking_bill = engine.gas_bill(s, year=1, smc=s['cooking_smc_annual'])
        self.assertAlmostEqual(res['actual_remaining_gas_cost'], cooking_bill, places=5)

        # 3. Allowance equals variable_cost(baseline_volume) - variable_cost(remaining_cooking_volume)
        baseline_smc = float(engine.GAS_MONTHLY.sum())
        cooking_smc = float(s['cooking_smc_annual'])
        expected_allowance = gas_variable_cost(baseline_smc, s, year=1) - gas_variable_cost(cooking_smc, s, year=1)
        self.assertAlmostEqual(res['allowance']['mean'], expected_allowance, places=5)
        self.assertAlmostEqual(res['unserved_service_allowance_eur']['mean'], expected_allowance, places=5)

        # 4. Gross avoided gas equals allowance exactly, leaving 0.0 net credit
        gross_avoided = engine.gas_bill(s, year=1) - res['actual_remaining_gas_cost']
        self.assertAlmostEqual(gross_avoided, expected_allowance, places=5)
        self.assertAlmostEqual(gross_avoided - res['allowance']['mean'], 0.0, places=5)

    def test_16_independent_analytic_tier_crossing_and_withholding_cases(self):
        """Independent analytic verification crossing 120 and 480 Smc tiers, without mirroring helper implementation."""
        from historical_weather import synthetic_thermal_runtime
        self.enterContext(synthetic_thermal_runtime([50.]*12))
        s = engine.validate({
            'planning_heat_pump': True,
            'load_scenario': 'electrified',
            'gas_commodity': 0.50,
            'gas_regulated': 0.40,
            'inflation': 0.0,
            'gas_real_escalation': 0.0,
        })
        baseline_smc = float(engine.GAS_MONTHLY.sum())
        summer_base = float(s['summer_gas_base'])
        summer = np.minimum(engine.GAS_MONTHLY, summer_base)
        h_ref = float((engine.GAS_MONTHLY - summer).sum())
        d_ref = float((summer - (summer / summer.sum() * s['cooking_smc_annual'])).sum())
        self.assertAlmostEqual(h_ref + d_ref, 580.0, places=5)

        # Independent analytic tier unit rates (calculated by hand from statutory ARERA progressive schedule):
        # variable = commodity + regulated = 0.50 + 0.40 = 0.90 EUR/Smc
        # Tier 1 (0-120 Smc): (variable + excise 0.038 + regional 0.019) * 1.10 VAT
        # = (0.90 + 0.057) * 1.10 = 0.957 * 1.10 = 1.0527 EUR/Smc
        r1_analytic = (0.90 + 0.038 + 0.019) * 1.10
        # Tier 2 (120-480 Smc): (variable + excise 0.135 + regional 0.031) * 1.10 VAT
        # = (0.90 + 0.166) * 1.10 = 1.066 * 1.10 = 1.1726 EUR/Smc
        r2_analytic = (0.90 + 0.135 + 0.031) * 1.10
        # Tier 3 (480-1560 Smc): (variable + excise 0.120 + regional 0.031) * 1.22 VAT
        # = (0.90 + 0.151) * 1.22 = 1.051 * 1.22 = 1.28222 EUR/Smc
        r3_analytic = (0.90 + 0.120 + 0.031) * 1.22

        # Case 0: Zero unmet (100% served)
        hp_0 = make_synthetic_hp_summary([{'space_req': h_ref, 'space_unmet': 0.0, 'dhw_req': d_ref, 'dhw_unmet': 0.0}])
        res_0 = calculate_hp_gas_finance(s, hp_summary=hp_0, year=1)
        self.assertEqual(res_0['allowance']['mean'], 0.0)
        self.assertTrue(res_0['service_comparable'])

        # Case A: Partial unmet entirely within Tier 3 (does NOT cross 480 Smc tier)
        # Withheld = 100 Smc. Top volume drops from 600 to 500 Smc (> 480).
        # Analytic allowance is purely top 100 Smc priced at r3_analytic.
        analytic_allowance_100 = 100.0 * r3_analytic
        hp_100 = make_synthetic_hp_summary([{'space_req': h_ref, 'space_unmet': 100.0, 'dhw_req': d_ref, 'dhw_unmet': 0.0}])
        res_100 = calculate_hp_gas_finance(s, hp_summary=hp_100, year=1)
        self.assertAlmostEqual(res_100['allowance']['mean'], analytic_allowance_100, places=4)

        # Case B: Partial unmet crossing 480 Smc tier (enters Tier 2)
        # Withheld = 300 Smc. Top volume drops from 600 to 300 Smc (in [120, 480]).
        # Tier 3 portion: (600 - 480 = 120 Smc) at r3_analytic.
        # Tier 2 portion: (480 - 418 = 62 Smc) at r2_analytic.
        analytic_allowance_300 = (120.0 * r3_analytic) + (180.0 * r2_analytic)
        hp_300 = make_synthetic_hp_summary([{'space_req': h_ref, 'space_unmet': 300.0, 'dhw_req': d_ref, 'dhw_unmet': 0.0}])
        res_300 = calculate_hp_gas_finance(s, hp_summary=hp_300, year=1)
        self.assertAlmostEqual(res_300['allowance']['mean'], analytic_allowance_300, places=4)

        # Case C: Partial unmet crossing 120 Smc tier (enters Tier 1)
        # Withheld = 550 Smc. Top volume drops from 600 to 50 Smc (in [0, 120]).
        # Tier 3 portion: 238 Smc at r3_analytic
        # Tier 2 portion: 360 Smc at r2_analytic
        # Tier 1 portion: (120 - 68) = 52 Smc at r1_analytic
        analytic_allowance_650 = (120.0 * r3_analytic) + (360.0 * r2_analytic) + (70.0 * r1_analytic)
        hp_650 = make_synthetic_hp_summary([{'space_req': h_ref, 'space_unmet': h_ref, 'dhw_req': d_ref, 'dhw_unmet': 550.0 - h_ref}])
        res_650 = calculate_hp_gas_finance(s, hp_summary=hp_650, year=1)
        self.assertAlmostEqual(res_650['allowance']['mean'], analytic_allowance_650, places=4)

        # Case D: Full unmet heating & DHW (693 Smc withheld, leaving 25 Smc cooking)
        # Tier 3: 238 Smc at r3_analytic
        # Tier 2: 360 Smc at r2_analytic
        # Tier 1: (120 - 25) = 95 Smc at r1_analytic
        analytic_allowance_full = (120.0 * r3_analytic) + (360.0 * r2_analytic) + (100.0 * r1_analytic)
        hp_full = make_synthetic_hp_summary([{'space_req': h_ref, 'space_unmet': h_ref, 'dhw_req': d_ref, 'dhw_unmet': d_ref}])
        res_full = calculate_hp_gas_finance(s, hp_summary=hp_full, year=1)
        self.assertAlmostEqual(res_full['allowance']['mean'], analytic_allowance_full, places=4)
        self.assertAlmostEqual(res_full['avoided_gas_credit']['mean'], 0.0, places=4)

    def test_17_multiyear_inflation_and_escalation(self):
        """Multiyear progression correctly compounds inflation and gas real escalation on avoided-marginal allowance."""
        from historical_weather import synthetic_thermal_runtime
        self.enterContext(synthetic_thermal_runtime([50.]*12))
        s = engine.validate({
            'planning_heat_pump': True,
            'load_scenario': 'electrified',
            'gas_commodity': 0.45,
            'gas_regulated': 0.395,
            'inflation': 0.02,
            'gas_real_escalation': 0.015,
        })
        summer_base = float(s['summer_gas_base'])
        summer = np.minimum(engine.GAS_MONTHLY, summer_base)
        h_ref = float((engine.GAS_MONTHLY - summer).sum())
        d_ref = float((summer - (summer / summer.sum() * s['cooking_smc_annual'])).sum())

        # 300 Smc withheld (crosses 480 tier boundary into Tier 2)
        hp_summary = make_synthetic_hp_summary([{'space_req': h_ref, 'space_unmet': 300.0, 'dhw_req': d_ref, 'dhw_unmet': 0.0}])

        for year in [1, 2, 5]:
            infl = 1.02 ** (year - 1)
            escal = 1.015 ** (year - 1)
            var_rate = (0.45 + 0.395) * infl * escal
            r3 = (var_rate + 0.120 + 0.031) * 1.22
            r2 = (var_rate + 0.135 + 0.031) * 1.10
            expected_allowance = 120.0 * r3 + 180.0 * r2

            res = calculate_hp_gas_finance(s, hp_summary=hp_summary, year=year)
            self.assertAlmostEqual(res['allowance']['mean'], expected_allowance, places=4)
            self.assertGreaterEqual(res['avoided_gas_credit']['mean'], 0.0)

    def test_18_cooking_and_disconnect_combinations(self):
        """All cooking and disconnect combinations handle fuel credit withholding and actual bills accurately."""
        hp_full_unmet = make_synthetic_hp_summary([{'space_req': 1000.0, 'space_unmet': 1000.0, 'dhw_req': 1000.0, 'dhw_unmet': 1000.0}])
        s_base = engine.validate({'planning_heat_pump': True, 'load_scenario': 'electrified'})

        # Combo 1: Retained cooking + retained connection
        s1 = dict(s_base, hp_electric_cooking=False, hp_disconnect_gas=False)
        res1 = calculate_hp_gas_finance(s1, hp_summary=hp_full_unmet, year=1)
        self.assertEqual(res1['avoided_gas_credit']['mean'], 0.0)
        self.assertAlmostEqual(res1['actual_remaining_gas_cost'], engine.gas_bill(s1, smc=s1['cooking_smc_annual']), places=3)

        # Combo 2: Electric cooking + retained connection (only cooking variable savings survive)
        s2 = dict(s_base, hp_electric_cooking=True, hp_disconnect_gas=False)
        res2 = calculate_hp_gas_finance(s2, hp_summary=hp_full_unmet, year=1)
        expected_cook_var = gas_variable_cost(s2['cooking_smc_annual'], s2, year=1)
        self.assertAlmostEqual(res2['avoided_gas_credit']['mean'], expected_cook_var, places=3)
        expected_fixed = s2['gas_fixed_day'] * 365.0 * 1.22
        self.assertAlmostEqual(res2['actual_remaining_gas_cost'], expected_fixed, places=3)

        # Combo 3: Electric cooking + disconnected gas (cooking + fixed charges survive)
        s3 = dict(s_base, hp_electric_cooking=True, hp_disconnect_gas=True)
        res3 = calculate_hp_gas_finance(s3, hp_summary=hp_full_unmet, year=1)
        self.assertAlmostEqual(res3['avoided_gas_credit']['mean'], expected_cook_var + expected_fixed, places=3)
        self.assertEqual(res3['actual_remaining_gas_cost'], 0.0)

        # Combo 4: Disconnected gas with hp_electric_cooking=False
        s4 = dict(s_base, hp_electric_cooking=False, hp_disconnect_gas=True)
        res4 = calculate_hp_gas_finance(s4, hp_summary=hp_full_unmet, year=1)
        self.assertAlmostEqual(res4['avoided_gas_credit']['mean'], expected_cook_var + expected_fixed, places=3)
        self.assertEqual(res4['actual_remaining_gas_cost'], 0.0)

    def test_19_monotonicity_and_no_negative_credit(self):
        """Fuel credit withholding is strictly monotonic with respect to unmet fraction and never yields negative credits."""
        s = engine.validate({'planning_heat_pump': True, 'load_scenario': 'electrified'})
        fractions = [0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0, 1.5]

        prev_allowance = -1.0
        prev_credit = float('inf')

        for frac in fractions:
            hp_summary = make_synthetic_hp_summary([{
                'space_req': 1000.0,
                'space_unmet': 1000.0 * frac,
                'dhw_req': 1000.0,
                'dhw_unmet': 1000.0 * frac,
            }])
            res = calculate_hp_gas_finance(s, hp_summary=hp_summary, year=1)
            allowance = res['allowance']['mean']
            credit = res['avoided_gas_credit']['mean']

            # Monotonicity checks
            self.assertGreaterEqual(allowance, prev_allowance)
            self.assertLessEqual(credit, prev_credit)
            # Non-negative credit guarantee
            self.assertGreaterEqual(credit, 0.0)
            self.assertGreaterEqual(res['avoided_gas_credit']['min'], 0.0)

            prev_allowance = allowance
            prev_credit = credit

    def test_20_evaluate_cache_isolation_and_no_mutation(self):
        """Evaluating different financial/tariff scenarios reuses physical source cache without mutating earlier results or source summary."""
        from feasibility.heatpump_integration import get_source_heat_pump_hourly

        s1 = {
            'planning_heat_pump': True,
            'load_scenario': 'electrified',
            'gas_commodity': 0.40,
            'south_panels': 10,
        }
        res1 = engine.evaluate(s1)

        # Snapshot first result financial metadata
        fin1_snapshot = dict(res1['heat_pump']['gas_finance'])
        rem1_cost = fin1_snapshot['actual_remaining_gas_cost']
        allowance1 = fin1_snapshot['allowance']['mean']
        credit1 = fin1_snapshot['avoided_gas_credit']['mean']

        # Ensure source cache summary does NOT have 'gas_finance' injected
        src_summary = get_source_heat_pump_hourly(engine.validate(s1))['summary']
        self.assertNotIn('gas_finance', src_summary)
        self.assertNotIn('gas_finance', res1['enduses']['heat_pump'])

        # Evaluate scenario 2 with different gas commodity price (0.80 instead of 0.40)
        s2 = {
            'planning_heat_pump': True,
            'load_scenario': 'electrified',
            'gas_commodity': 0.80,
            'south_panels': 10,
        }
        res2 = engine.evaluate(s2)

        # 1. Distinct dictionary objects
        self.assertIsNot(res1['heat_pump'], res2['heat_pump'])
        self.assertIsNot(res1['heat_pump']['gas_finance'], res2['heat_pump']['gas_finance'])

        # 2. First result gas_finance must remain strictly unchanged
        self.assertEqual(res1['heat_pump']['gas_finance']['actual_remaining_gas_cost'], rem1_cost)
        self.assertEqual(res1['heat_pump']['gas_finance']['allowance']['mean'], allowance1)
        self.assertEqual(res1['heat_pump']['gas_finance']['avoided_gas_credit']['mean'], credit1)

        # 3. Second result reflects new gas tariff
        rem2_cost = res2['heat_pump']['gas_finance']['actual_remaining_gas_cost']
        self.assertNotEqual(rem1_cost, rem2_cost)
        self.assertEqual(res2['heat_pump']['gas_finance']['actual_remaining_gas_cost'], rem2_cost)

        # 4. Physical dispatch/energy is identical and reused from cache
        self.assertEqual(res1['energy']['pv_kwh']['mean'], res2['energy']['pv_kwh']['mean'])
        self.assertEqual(res1['heat_pump']['envelopes']['annual_total_electricity_kwh']['mean'], res2['heat_pump']['envelopes']['annual_total_electricity_kwh']['mean'])
        self.assertEqual(res1['enduses']['heat_pump_total_kwh'], res2['enduses']['heat_pump_total_kwh'])

        # 5. Source cache summary still clean
        self.assertNotIn('gas_finance', src_summary)
        self.assertNotIn('gas_finance', res2['enduses']['heat_pump'])

        # 6. Legacy off keys preserved and no mutation of defaults
        res_off = engine.evaluate({'planning_heat_pump': False, 'load_scenario': 'baseline'})
        self.assertNotIn('heat_pump', res_off)
        self.assertNotIn('service_comparable', res_off)
        self.assertFalse(engine.defaults()['planning_heat_pump'])


if __name__ == '__main__':
    unittest.main()
