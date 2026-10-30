"""Focused integration tests for gas tax kernel into engine and heat-pump finance."""
from __future__ import annotations

import copy
import math
import unittest
from unittest.mock import patch
import numpy as np

from feasibility import assumptions, engine, shared_state
from feasibility.gas_taxes import (
    DEFAULT_GAS_RATES,
    calculate_gas_cost,
    get_gas_tax_metadata,
)
from feasibility.heatpump_finance import (
    calculate_hp_remaining_gas_bill,
    calculate_hp_gas_finance,
    gas_variable_cost,
    is_active_hp,
)
from test_support import IsolatedDefaultsTestCase


def _legacy_annual_gas_bill(volume: float, year: int = 1, commodity: float = 0.5, regulated: float = 0.4, fixed_day: float = 0.3, inflation: float = 0.02, gas_real_escalation: float = 0.0) -> float:
    """Historical legacy gas bill formula with hardcoded rates."""
    if volume <= 0:
        return 0.0
    variable = (commodity + regulated) * (1.0 + inflation) ** (year - 1) * (1.0 + gas_real_escalation) ** (year - 1)
    fixed = fixed_day * 365.0 * (1.0 + inflation) ** (year - 1)
    breaks = [0.0, 120.0, 480.0, 1560.0, float('inf')]
    excise = [0.038, 0.135, 0.120, 0.150]
    net = []
    for i in range(4):
        quantity = max(0.0, min(volume, breaks[i + 1]) - breaks[i])
        regional = 0.019 if i == 0 else 0.031
        subtotal = quantity * (variable + excise[i] + regional)
        net.append(subtotal * (1.10 if i < 2 else 1.22))
    return float(sum(net) + fixed * 1.22)


def _legacy_gas_variable_cost(volume: float, year: int = 1, commodity: float = 0.5, regulated: float = 0.4, inflation: float = 0.02, gas_real_escalation: float = 0.0) -> float:
    """Historical legacy gas variable cost formula without fixed charges."""
    if volume <= 0.0:
        return 0.0
    variable = (commodity + regulated) * (1.0 + inflation) ** (year - 1) * (1.0 + gas_real_escalation) ** (year - 1)
    breaks = [0.0, 120.0, 480.0, 1560.0, float('inf')]
    excise = [0.038, 0.135, 0.120, 0.150]
    net = []
    for i in range(4):
        quantity = max(0.0, min(volume, breaks[i + 1]) - breaks[i])
        regional = 0.019 if i == 0 else 0.031
        subtotal = quantity * (variable + excise[i] + regional)
        net.append(subtotal * (1.10 if i < 2 else 1.22))
    return float(sum(net))


def _make_fast_synthetic_annual(horizon: int = 5, weather: int = 2) -> np.ndarray:
    """Deterministic annual dispatch array for rapid finance tests without full weather simulation."""
    annual = np.zeros((horizon, weather, len(engine.FIELDS)))
    for yy in range(horizon):
        for w in range(weather):
            annual[yy, w, 0] = 3000.0  # load_kwh
            annual[yy, w, 1] = 3500.0  # pv_kwh
            annual[yy, w, 5] = 1200.0  # grid_import_kwh
            annual[yy, w, 6] = 1700.0  # export_kwh
            annual[yy, w, 11] = 1700.0 # export_weighted
    return annual


def _make_synthetic_hp_summary(weather_count: int = 2, space_unmet: float = 0.0, dhw_unmet: float = 0.0) -> dict:
    """Synthetic heat pump summary with controllable unmet demand."""
    by_weather = []
    for i in range(weather_count):
        by_weather.append({
            'weather_year': 2005 + i,
            'annual_requested_space_heat_kwh': 3500.0,
            'annual_delivered_space_heat_kwh': 3500.0 - space_unmet,
            'annual_unmet_space_heat_kwh': float(space_unmet),
            'annual_requested_draw_heat_kwh': 2000.0,
            'annual_delivered_draw_heat_kwh': 2000.0 - dhw_unmet,
            'annual_unmet_draw_heat_kwh': float(dhw_unmet),
        })
    return {'by_weather': by_weather}


class GasTaxIntegrationParityTests(IsolatedDefaultsTestCase):
    """Parity tests between kernel wrapper, heatpump finance, and legacy formulas under default settings."""

    def test_gas_bill_default_parity_years_1_2_20(self):
        """engine.gas_bill() matches legacy formula and kernel across years 1, 2, and 20."""
        defs = engine.defaults()
        volumes = [0.01, 25.0, 120.0, 480.0, 600.0, 1560.0, 2200.0]
        for yr in [1, 2, 20]:
            for vol in volumes:
                with self.subTest(year=yr, volume=vol):
                    expected = _legacy_annual_gas_bill(vol, year=yr)
                    actual = engine.gas_bill(defs, year=yr, smc=vol)
                    self.assertAlmostEqual(actual, expected, places=8)

                    # Test gas_bill_breakdown helper
                    bd = engine.gas_bill_breakdown(defs, year=yr, smc=vol)
                    self.assertAlmostEqual(bd['total_eur'], expected, places=8)

            # Test default synthetic volume when smc is None
            expected_default = _legacy_annual_gas_bill(float(engine.GAS_MONTHLY.sum()), year=yr)
            self.assertAlmostEqual(engine.gas_bill(defs, year=yr), expected_default, places=8)

            # Test zero-volume compatibility (service eliminated)
            self.assertEqual(engine.gas_bill(defs, year=yr, smc=0.0), 0.0)
            self.assertEqual(engine.gas_bill(defs, year=yr, smc=-5.0), 0.0)

    def test_heatpump_variable_cost_parity_years_1_2_20(self):
        """heatpump_finance.gas_variable_cost() matches legacy variable formula across years 1, 2, 20."""
        defs = engine.defaults()
        volumes = [0.0, 10.0, 120.0, 300.0, 480.0, 600.0, 1560.0, 2000.0]
        for yr in [1, 2, 20]:
            for vol in volumes:
                with self.subTest(year=yr, volume=vol):
                    expected = _legacy_gas_variable_cost(vol, year=yr)
                    actual = gas_variable_cost(vol, defs, year=yr)
                    self.assertAlmostEqual(actual, expected, places=8)

    def test_heatpump_retained_fixed_parity_years_1_2_20(self):
        """calculate_hp_remaining_gas_bill() preserves exact fixed-only and cooking bills."""
        defs = engine.defaults()

        for yr in [1, 2, 20]:
            # 1. Retained connection with electric cooking (zero volume, fixed charges only)
            s_electric_cooking = dict(defs, hp_electric_cooking=True, hp_disconnect_gas=False)
            infl = (1.0 + defs['inflation']) ** (yr - 1)
            expected_fixed = defs['gas_fixed_day'] * 365.0 * infl * (1.0 + defs['gas_vat_standard'])
            actual_fixed = calculate_hp_remaining_gas_bill(s_electric_cooking, year=yr)
            self.assertAlmostEqual(actual_fixed, expected_fixed, places=8)

            # 2. Retained connection with gas cooking (25 Smc variable + fixed charges)
            s_gas_cooking = dict(defs, hp_electric_cooking=False, hp_disconnect_gas=False, cooking_smc_annual=25.0)
            expected_cooking = engine.gas_bill(s_gas_cooking, year=yr, smc=25.0)
            actual_cooking = calculate_hp_remaining_gas_bill(s_gas_cooking, year=yr)
            self.assertAlmostEqual(actual_cooking, expected_cooking, places=8)

            # 3. Disconnected service is strictly 0.0 EUR
            s_disconnected = dict(defs, hp_disconnect_gas=True)
            self.assertEqual(calculate_hp_remaining_gas_bill(s_disconnected, year=yr), 0.0)


class GasTaxRateSensitivityTests(IsolatedDefaultsTestCase):
    """Verify that editing each of the 8 statutory rates individually propagates properly."""

    ALL_TAX_KEYS = [
        'gas_excise_0_120',
        'gas_excise_120_480',
        'gas_excise_480_1560',
        'gas_excise_over_1560',
        'gas_regional_0_120',
        'gas_regional_over_120',
        'gas_vat_reduced',
        'gas_vat_standard',
    ]

    def test_all_eight_rates_individually_alter_baseline_or_high_volume(self):
        """Every individual tax rate edit changes the corresponding billing tier and total cost."""
        base_s = engine.defaults()
        default_baseline = engine.gas_bill(base_s, year=1, smc=600.0)
        default_high = engine.gas_bill(base_s, year=1, smc=2000.0)

        for key in self.ALL_TAX_KEYS:
            with self.subTest(key=key):
                # Test zero boundary
                s_zero = dict(base_s, **{key: 0.0})
                # Test higher boundary
                s_high = dict(base_s, **{key: 0.50 if 'vat' in key else 0.40})

                if key == 'gas_excise_over_1560':
                    # Over-1560 applies to high volume, not the ordinary synthetic baseline
                    self.assertEqual(engine.gas_bill(s_zero, year=1, smc=600.0), default_baseline)
                    self.assertLess(engine.gas_bill(s_zero, year=1, smc=2000.0), default_high)
                    self.assertGreater(engine.gas_bill(s_high, year=1, smc=2000.0), default_high)
                else:
                    self.assertLess(engine.gas_bill(s_zero, year=1, smc=600.0), default_baseline)
                    self.assertGreater(engine.gas_bill(s_high, year=1, smc=600.0), default_baseline)

    def test_retained_fixed_charges_sensitive_to_gas_vat_standard_and_gas_fixed_day(self):
        """Retained zero-volume connection charges depend dynamically on gas_vat_standard without hardcoded 1.22."""
        base_s = engine.defaults()
        base_s.update({'hp_electric_cooking': True, 'hp_disconnect_gas': False})

        # Default 22% VAT
        bill_default = calculate_hp_remaining_gas_bill(base_s, year=1)
        expected_default = 0.3 * 365.0 * 1.22
        self.assertAlmostEqual(bill_default, expected_default, places=6)

        # Zero VAT
        s_zero_vat = dict(base_s, gas_vat_standard=0.0)
        bill_zero = calculate_hp_remaining_gas_bill(s_zero_vat, year=1)
        self.assertAlmostEqual(bill_zero, 0.3 * 365.0 * 1.0, places=6)

        # 10% VAT
        s_ten_vat = dict(base_s, gas_vat_standard=0.10)
        bill_ten = calculate_hp_remaining_gas_bill(s_ten_vat, year=1)
        self.assertAlmostEqual(bill_ten, 0.3 * 365.0 * 1.10, places=6)

        # 30% VAT
        s_thirty_vat = dict(base_s, gas_vat_standard=0.30)
        bill_thirty = calculate_hp_remaining_gas_bill(s_thirty_vat, year=1)
        self.assertAlmostEqual(bill_thirty, 0.3 * 365.0 * 1.30, places=6)

    def test_retained_cooking_charges_sensitive_to_first_bracket_and_vat_rates(self):
        """Cooking gas (25 Smc) is sensitive to 0-120 excise, 0-120 regional, reduced VAT, and standard VAT on fixed."""
        base_s = engine.defaults()
        base_s.update({'hp_electric_cooking': False, 'hp_disconnect_gas': False, 'cooking_smc_annual': 25.0})

        default_cooking_bill = calculate_hp_remaining_gas_bill(base_s, year=1)

        # Excise 0-120 changed
        s_exc = dict(base_s, gas_excise_0_120=0.08)
        self.assertGreater(calculate_hp_remaining_gas_bill(s_exc, year=1), default_cooking_bill)

        # Regional 0-120 changed
        s_reg = dict(base_s, gas_regional_0_120=0.04)
        self.assertGreater(calculate_hp_remaining_gas_bill(s_reg, year=1), default_cooking_bill)

        # Reduced VAT changed
        s_red_vat = dict(base_s, gas_vat_reduced=0.15)
        self.assertGreater(calculate_hp_remaining_gas_bill(s_red_vat, year=1), default_cooking_bill)

        # Standard VAT changed (applies to fixed part)
        s_std_vat = dict(base_s, gas_vat_standard=0.25)
        self.assertGreater(calculate_hp_remaining_gas_bill(s_std_vat, year=1), default_cooking_bill)

    def test_unserved_allowance_and_avoided_gas_credit_propagation(self):
        """Fuel credit withholding and avoided gas credit respond dynamically to gas tax edits."""
        base_s = engine.defaults()
        base_s.update({
            'planning_heat_pump': True,
            'load_scenario': 'electrified',
            'hp_electric_cooking': False,
            'hp_disconnect_gas': False,
        })
        # Partial shortfall scenario
        hp_summary = _make_synthetic_hp_summary(weather_count=2, space_unmet=1000.0, dhw_unmet=500.0)

        fin_default = calculate_hp_gas_finance(base_s, hp_summary=hp_summary, year=1)
        allowance_default = fin_default['unserved_service_allowance']['mean']
        avoided_default = fin_default['avoided_gas_credit']['mean']

        # Increase excise on bracket 480-1560 (where marginal synthetic baseline minus withholding falls)
        s_higher_tax = dict(base_s, gas_excise_480_1560=0.25)
        fin_higher = calculate_hp_gas_finance(s_higher_tax, hp_summary=hp_summary, year=1)
        self.assertGreater(fin_higher['unserved_service_allowance']['mean'], allowance_default)
        self.assertGreater(fin_higher['avoided_gas_credit']['mean'], avoided_default)

        # Full shortfall scenario: all heating & DHW unmet (only cooking retained)
        hp_summary_full_unmet = _make_synthetic_hp_summary(weather_count=2, space_unmet=3500.0, dhw_unmet=2000.0)
        fin_full = calculate_hp_gas_finance(base_s, hp_summary=hp_summary_full_unmet, year=1)
        # Avoided credit must be 0.0 EUR (no phantom savings)
        self.assertEqual(fin_full['avoided_gas_credit']['mean'], 0.0)

    def test_financial_cashflows_npv_propagation_and_baseline_cancellation(self):
        """In baseline load scenario gas cancels; in electrified/HP scenarios tax edits propagate to NPV."""
        annual = _make_fast_synthetic_annual(horizon=5, weather=2)
        defs = engine.defaults()
        kwp = 3.0

        # Case 1: load_scenario == 'baseline' -> gas cancels completely out of whole_package NPV
        s_base_default = dict(defs, load_scenario='baseline', horizon_years=5)
        f_base_1, cash_base_1 = engine._financial(s_base_default, annual, kwp)

        s_base_tax_double = dict(defs, load_scenario='baseline', horizon_years=5, gas_excise_120_480=0.30, gas_vat_reduced=0.20)
        f_base_2, cash_base_2 = engine._financial(s_base_tax_double, annual, kwp)

        # Baseline gas bill increased in first operating year (cash[1])
        self.assertGreater(cash_base_2[1]['baseline_gas_bill'], cash_base_1[1]['baseline_gas_bill'])
        # But package cashflows and NPV are strictly identical because baseline gas cancels with remaining gas
        self.assertAlmostEqual(cash_base_1[1]['whole_package'], cash_base_2[1]['whole_package'], places=9)
        self.assertAlmostEqual(f_base_1['whole_package']['npv_eur']['mean'], f_base_2['whole_package']['npv_eur']['mean'], places=9)

        # Case 2: load_scenario == 'electrified' (gas eliminated) -> higher gas tax increases whole_package NPV savings!
        s_elec_default = dict(defs, load_scenario='electrified', planning_heat_pump=False, horizon_years=5)
        f_elec_1, cash_elec_1 = engine._financial(s_elec_default, annual, kwp)

        s_elec_tax_double = dict(defs, load_scenario='electrified', planning_heat_pump=False, horizon_years=5, gas_excise_120_480=0.30, gas_vat_reduced=0.20)
        f_elec_2, cash_elec_2 = engine._financial(s_elec_tax_double, annual, kwp)

        # Higher gas tax makes avoiding gas more valuable -> package NPV strictly increases
        self.assertGreater(cash_elec_2[1]['whole_package'], cash_elec_1[1]['whole_package'])
        self.assertGreater(f_elec_2['whole_package']['npv_eur']['mean'], f_elec_1['whole_package']['npv_eur']['mean'])

        # Case 3: Solar-only cashflows (pv_incremental) are 100% unaffected by gas tax changes
        self.assertAlmostEqual(f_elec_1['pv_incremental']['npv_eur']['mean'], f_elec_2['pv_incremental']['npv_eur']['mean'], places=9)
        self.assertAlmostEqual(cash_elec_1[1]['export_tax'], cash_elec_2[1]['export_tax'], places=9)


class GasTaxAuditedResultDataTests(IsolatedDefaultsTestCase):
    """Verify the structure, completeness, and scenario accuracy of gas_costs in evaluate()."""

    def test_gas_costs_structure_and_parity_in_evaluate(self):
        """evaluate() result contains gas_costs matching finance and cashflow first-year items."""
        s = engine.defaults()
        s.update({
            'south_panels': 8,
            'horizon_years': 5,
            'load_scenario': 'baseline',
        })
        res = engine.evaluate(s, detail=False)

        self.assertIn('gas_costs', res)
        gc = res['gas_costs']
        self.assertEqual(gc['year'], 1)
        self.assertIn('baseline', gc)
        self.assertIn('remaining', gc)
        self.assertIn('connection_status', gc)
        self.assertIn('reference_bill', gc)
        self.assertIn('tax_rules', gc)
        self.assertIn('notes', gc)

        # Baseline parity
        b = gc['baseline']
        self.assertEqual(b['volume'], float(engine.GAS_MONTHLY.sum()))
        self.assertEqual(b['days'], 365)
        self.assertEqual(b['prior_year_smc'], 0.0)
        self.assertEqual(b['connection_status'], 'connected')
        self.assertAlmostEqual(b['total_eur'], res['finance']['historical_annual_gas_bill_eur'], places=8)
        self.assertAlmostEqual(b['total_eur'], res['annual_cashflows'][1]['baseline_gas_bill'], places=8)

        # Remaining parity under baseline scenario
        r = gc['remaining']
        self.assertEqual(r['volume'], float(engine.GAS_MONTHLY.sum()))
        self.assertEqual(r['connection_status'], 'connected')
        self.assertAlmostEqual(r['total_eur'], res['annual_cashflows'][1]['remaining_gas_bill'], places=8)

    def test_gas_costs_across_all_connection_scenarios(self):
        """Verify remaining breakdown across baseline, electrified AC, HP retained cooking, HP zero volume, and HP disconnected."""
        defs = engine.defaults()

        # 1. AC Electrified (gas eliminated)
        s_ac = dict(defs, load_scenario='electrified', planning_heat_pump=False)
        gc_ac = engine.build_gas_costs_audit(s_ac)
        self.assertEqual(gc_ac['connection_status'], 'disconnected')
        self.assertEqual(gc_ac['remaining']['volume'], 0.0)
        self.assertEqual(gc_ac['remaining']['total_eur'], 0.0)
        self.assertEqual(gc_ac['remaining']['fixed_eur'], 0.0)
        self.assertEqual(gc_ac['remaining']['national_excise_eur'], 0.0)
        self.assertEqual(gc_ac['remaining']['vat_eur'], 0.0)

        # 2. HP Retained with gas cooking
        s_hp_cook = dict(defs, load_scenario='electrified', planning_heat_pump=True, hp_electric_cooking=False, hp_disconnect_gas=False, cooking_smc_annual=25.0)
        gc_hp_cook = engine.build_gas_costs_audit(s_hp_cook)
        self.assertEqual(gc_hp_cook['connection_status'], 'connected')
        self.assertEqual(gc_hp_cook['remaining']['volume'], 25.0)
        self.assertGreater(gc_hp_cook['remaining']['total_eur'], 120.0)
        self.assertGreater(gc_hp_cook['remaining']['variable_total_eur'], 0.0)

        # 3. HP Retained with electric cooking (retained zero volume: fixed fee only)
        s_hp_retained = dict(defs, load_scenario='electrified', planning_heat_pump=True, hp_electric_cooking=True, hp_disconnect_gas=False)
        gc_hp_retained = engine.build_gas_costs_audit(s_hp_retained)
        self.assertEqual(gc_hp_retained['connection_status'], 'retained_zero_volume')
        self.assertEqual(gc_hp_retained['remaining']['volume'], 0.0)
        self.assertEqual(gc_hp_retained['remaining']['variable_total_eur'], 0.0)
        expected_fixed_fee = 0.3 * 365.0 * 1.22
        self.assertAlmostEqual(gc_hp_retained['remaining']['total_eur'], expected_fixed_fee, places=6)
        self.assertAlmostEqual(gc_hp_retained['remaining']['fixed_total_eur'], expected_fixed_fee, places=6)

        # 4. HP Disconnected
        s_hp_disc = dict(defs, load_scenario='electrified', planning_heat_pump=True, hp_electric_cooking=True, hp_disconnect_gas=True)
        gc_hp_disc = engine.build_gas_costs_audit(s_hp_disc)
        self.assertEqual(gc_hp_disc['connection_status'], 'disconnected')
        self.assertEqual(gc_hp_disc['remaining']['volume'], 0.0)
        self.assertEqual(gc_hp_disc['remaining']['total_eur'], 0.0)

    def test_reference_bill_payload_and_residual_responsiveness(self):
        """Reference bill compares user reference 22 Smc / 31 days with live modeled prior 146 without hardcoding."""
        defs = engine.defaults()
        gc = engine.build_gas_costs_audit(defs)
        rb = gc['reference_bill']

        # Known invoice amounts
        inv = rb['invoice']
        self.assertEqual(inv['volume_smc'], 50.0)
        self.assertEqual(inv['period_days'], 30)
        self.assertEqual(inv['consumption_eur'], 45.0)
        self.assertEqual(inv['fixed_eur'], 9.0)
        self.assertEqual(inv['excise_including_regional_eur'], 8.3)
        self.assertEqual(inv['vat_eur'], 7.31)
        self.assertEqual(inv['total_eur'], 69.61)

        # Modeled amounts under factory defaults
        mod = rb['modeled']
        self.assertEqual(mod['volume'], 50.0)
        self.assertEqual(mod['days'], 30)
        self.assertEqual(mod['prior_year_smc'], 200.0)
        self.assertEqual(round(mod['variable_pretax_eur'], 2), 45.0)
        self.assertEqual(round(mod['fixed_eur'], 2), 9.0)
        self.assertEqual(round(mod['national_excise_eur'] + mod['regional_surcharge_eur'], 2), 8.3)
        self.assertEqual(round(mod['vat_eur'], 2), 7.31)
        self.assertEqual(round(mod['total_eur'], 2), 69.61)

        # Residuals under defaults should be negligible (< 0.01)
        resids = rb['residuals']
        self.assertLess(abs(resids['total_eur']), 0.01)

        # Disclosures and notes
        notes = " ".join(rb['notes'])
        self.assertIn('200', notes)
        self.assertIn('illustrative assumption', notes)
        self.assertIn('unknown', notes)
        self.assertIn('rolling 12-month model', notes)
        self.assertIn('cannot be inferred from', notes)

        # Editing settings changes modeled amounts and residuals while invoice amounts remain unchanged
        s_edited = dict(defs, gas_commodity=0.60, gas_excise_120_480=0.20)
        gc_edited = engine.build_gas_costs_audit(s_edited)
        rb_edited = gc_edited['reference_bill']

        # Invoice unchanged
        self.assertEqual(rb_edited['invoice']['total_eur'], 69.61)
        # Modeled strictly changed
        self.assertGreater(rb_edited['modeled']['total_eur'], 69.61)
        # Residual strictly changed
        self.assertGreater(rb_edited['residuals']['total_eur'], 0.01)

    def test_quote_evaluate_exposes_gas_costs(self):
        """quote_evaluate() exposes gas_costs top-level and inside result."""
        s = engine.defaults()
        s.update({'south_panels': 6, 'horizon_years': 5})
        quote_payload = {'line_items': [{'net_eur': 2000.0, 'vat': 0.10, 'scope': 'solar_storage'}]}
        out = engine.quote_evaluate(s, quote=quote_payload)

        self.assertIn('gas_costs', out)
        self.assertIn('gas_costs', out['result'])
        self.assertIs(out['gas_costs'], out['result']['gas_costs'])
        self.assertEqual(out['gas_costs']['baseline']['volume'], float(engine.GAS_MONTHLY.sum()))


class GasTaxTwoSuccessiveEvaluationsTests(IsolatedDefaultsTestCase):
    """Verify that successive evaluations differing only in a gas tax produce differing financials and current breakdown."""

    def test_successive_evaluations_produce_distinct_financials_and_breakdown(self):
        """Two successive evaluations differing only by gas_vat_reduced return distinct live values without cache pollution."""
        s_1 = engine.defaults()
        s_1.update({
            'south_panels': 8,
            'horizon_years': 5,
            'load_scenario': 'electrified',
            'gas_vat_reduced': 0.10,
        })
        res_1 = engine.evaluate(s_1, detail=False)

        s_2 = dict(s_1, gas_vat_reduced=0.05)
        res_2 = engine.evaluate(s_2, detail=False)

        # 1. Breakdowns reflect their respective edited settings
        self.assertEqual(res_1['gas_costs']['baseline']['reduced_vat_rate'], 0.10)
        self.assertEqual(res_2['gas_costs']['baseline']['reduced_vat_rate'], 0.05)
        self.assertNotEqual(res_1['gas_costs']['baseline']['reduced_vat_eur'], res_2['gas_costs']['baseline']['reduced_vat_eur'])
        self.assertNotEqual(res_1['gas_costs']['baseline']['total_eur'], res_2['gas_costs']['baseline']['total_eur'])

        # 2. Package financial data differs because avoided gas value changed
        self.assertNotEqual(res_1['finance']['historical_annual_gas_bill_eur'], res_2['finance']['historical_annual_gas_bill_eur'])
        self.assertNotEqual(res_1['finance']['whole_package']['npv_eur']['mean'], res_2['finance']['whole_package']['npv_eur']['mean'])
        self.assertNotEqual(res_1['annual_cashflows'][1]['whole_package'], res_2['annual_cashflows'][1]['whole_package'])

        # 3. Running s_1 again produces exact original result
        res_1_repeat = engine.evaluate(s_1, detail=False)
        self.assertEqual(res_1_repeat['gas_costs']['baseline']['reduced_vat_rate'], 0.10)
        self.assertAlmostEqual(res_1_repeat['gas_costs']['baseline']['total_eur'], res_1['gas_costs']['baseline']['total_eur'], places=9)


class GasTaxDefaultsPersistenceTests(IsolatedDefaultsTestCase):
    """Verify user defaults persistence and reset across all 8 gas tax settings."""

    ALL_TAX_KEYS = [
        'gas_excise_0_120',
        'gas_excise_120_480',
        'gas_excise_480_1560',
        'gas_excise_over_1560',
        'gas_regional_0_120',
        'gas_regional_over_120',
        'gas_vat_reduced',
        'gas_vat_standard',
    ]

    def test_save_and_reset_all_eight_gas_tax_defaults(self):
        """Each of the 8 gas tax rates can be saved as a user default and reset to factory default."""
        for key in self.ALL_TAX_KEYS:
            with self.subTest(key=key):
                orig_factory = assumptions.SPEC[key][0]
                test_override = round(orig_factory + 0.05, 4)

                # Save user default
                entry = assumptions.save_user_default(key, test_override, engine.validate)
                self.assertEqual(entry['value'], test_override)
                self.assertEqual(entry['status'], 'provided')
                self.assertEqual(entry['factory_default'], orig_factory)

                # Verify defaults() and records() reflect the override
                current_defs = assumptions.persisted_defaults()
                self.assertEqual(current_defs[key], test_override)

                rec = next(r for r in assumptions.persisted_records() if r['name'] == key)
                self.assertEqual(rec['value'], test_override)
                self.assertEqual(rec['status'], 'provided')

                # Reset back to factory default
                assumptions.reset_user_default(key)
                reset_defs = assumptions.persisted_defaults()
                self.assertEqual(reset_defs[key], orig_factory)


if __name__ == '__main__':
    unittest.main()
