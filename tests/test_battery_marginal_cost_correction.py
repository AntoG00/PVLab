"""Dedicated regression tests for battery marginal-cost correction and candidate costing."""
import math
import unittest
import numpy as np

import feasibility.engine as e
from test_support import IsolatedDefaultsTestCase


class BatteryMarginalCostCorrectionTests(IsolatedDefaultsTestCase):
    def setUp(self):
        super().setUp()
        self.base_settings = e.validate({
            'south_panels': 12,
            'west_panels': 0,
            'east_panels': 0,
            'battery_kwh': 10.0,
            'battery_modules': 2,
            'battery_module_kwh': 5.0,
            'battery_cost_kwh': 500.0,
            'roundtrip_efficiency': 0.9,
            'inverter_kw': 6.0,
            'charge_kw': 5.0,
            'discharge_kw': 5.0,
            'horizon_years': 20,
            'battery_replace_year': 12,
            'deduction_rate': 0.5,
            'principal_residence_confirmed': True, 'deduction_tax_capacity': 10000.0,
            'load_scenario': 'electrified',
            'quote_vat_included': False,
            'installation_vat': 0.10,
        })

    def assert_synthetic_optimum(self, result, settings, unit_cost):
        """Independent discounted capital and degradation arithmetic over real dispatch savings."""
        settings = e.validate(settings)
        r = (1+settings['real_discount_rate'])*(1+settings['inflation'])-1
        discount = [1/(1+r)**year for year in range(1,13)]
        degradation = sum((1-settings['battery_calendar_fade'])**(year-1)*discount[year-1] for year in range(1,13))/sum(discount)
        relief = .5/10*sum(discount[:10])
        rows = result['chart1_capacity_dispatch']['capacities']
        expected = []
        for wi in range(len(e.data()['years'])):
            winner = max(rows, key=lambda row: row['annual_savings_by_weather'][wi]*degradation - row['capacity_kwh']*unit_cost*(1-relief)/12)
            expected.append(winner['capacity_kwh'])
        band = result['chart2_incremental_savings']['optimum_band']
        self.assertEqual(band['weather_min_kwh'], min(expected))
        self.assertEqual(band['weather_max_kwh'], max(expected))
        median = np.median(expected)
        self.assertEqual(band['weather_median_kwh'], median if settings['battery_mixed_sizes'] else int(median))

    def test_marginal_unit_cost_invariant_to_installed_module_count(self):
        """Benchmark marginal storage unit cost is invariant to current installed module count."""
        results = []
        for mod_count in [0, 1, 2, 5, 8]:
            s = dict(self.base_settings)
            s['battery_modules'] = mod_count
            s['battery_kwh'] = float(mod_count * 5.0)
            res = e.battery_sizing(s, {'step': 1, 'max_capacity': 10})
            eco = res['economics']
            results.append((mod_count, eco['effective_marginal_battery_cost_eur_kwh']))

        first_cost = results[0][1]
        self.assertAlmostEqual(first_cost, 550.0, places=2)  # 500 €/kWh * 1.10 VAT = 550 €/kWh
        for mod_count, cost in results:
            self.assertAlmostEqual(
                cost, first_cost, places=4,
                msg=f"Marginal cost at installed {mod_count} modules ({cost}) differs from 0 modules ({first_cost})"
            )

    def test_exact_regression_reference_numbers(self):
        """Two 5 kWh modules @ 500 EUR/kWh with electrified demand and 12-year lifetime match reference numbers.
        
        Buggy behavior: gross annualized cost ~416.67 €/kWh/yr, net after 50% deduction ~256.26 €/kWh/yr,
        causing first-year heuristic optimum to be 0 kWh.
        Corrected behavior: gross annualized cost ~41.67 €/kWh/yr (excl. VAT / 45.83 with VAT),
        net ~25.63 €/kWh/yr (or ~28.19 with VAT), first increment saves ~38.98 €/kWh/yr,
        and first-year heuristic optimum is 5–6.9 kWh across weather analogues (median 5 kWh).
        """
        # Excl VAT setup to test exact base numbers (500 €/kWh)
        s = dict(self.base_settings)
        s['quote_vat_included'] = True  # 500 €/kWh turnkey
        res = e.battery_sizing(s, {'step': 1, 'max_capacity': 25, 'battery_lifetime_years': 12, 'deduction_rate': 0.5})
        eco = res['economics']

        self.assertAlmostEqual(eco['effective_marginal_battery_cost_eur_kwh'], 500.0, places=2)
        # Gross annualized cost = 500 / 12 = 41.67 €/kWh/yr (corrected vs 416.67 buggy)
        self.assertAlmostEqual(eco['annualized_marginal_cost_gross'], 41.67, places=2)
        # Net annualized cost after 50% deduction discounted over 10 years / 12 = 25.63 €/kWh/yr (corrected vs 256.26 buggy)
        self.assertAlmostEqual(eco['annualized_marginal_cost_net'], 25.63, places=2)

        inc = res['chart2_incremental_savings']
        first_step = inc['steps'][0]
        self.assertEqual(first_step['capacity_from'], 0)
        self.assertEqual(first_step['capacity_to'], 1)
        self.assertGreater(first_step['incremental_savings_eur_kwh']['median'], 35.0)

        # Optimum band now recognizes storage as economically beneficial under the first-year heuristic
        opt_band = inc['optimum_band']
        self.assertGreaterEqual(opt_band['weather_median_kwh'], 5.0)
        self.assertGreaterEqual(opt_band['weather_max_kwh'], 5.0)
        self.assert_synthetic_optimum(res, s, 500.)

    def test_independent_installed_capex_difference_oracle_for_benchmark_steps(self):
        """Every discrete capacity increment step cost matches independent installed_capex difference oracle for attainable hardware endpoints."""
        res = e.battery_sizing(self.base_settings, {'step': 1, 'max_capacity': 10})
        steps = res['chart2_incremental_savings']['steps']
        kwp = 12 * 400.0 / 1000.0

        # Construct explicit module-count candidate settings independently for attainable endpoints
        def indep_cand(modules):
            return {
                **self.base_settings,
                'battery_mixed_sizes': False,
                'battery_modules': modules,
                'battery_secondary_modules': 0,
                'battery_module_kwh': 5.0,
                'battery_kwh': float(modules * 5.0),
                'quote_total': None,
            }

        capex0 = e.installed_capex(indep_cand(0), kwp)
        capex1 = e.installed_capex(indep_cand(1), kwp)
        capex2 = e.installed_capex(indep_cand(2), kwp)

        delta_0_5 = capex1 - capex0
        delta_5_10 = capex2 - capex1
        unit_cost_0_5 = delta_0_5 / 5.0
        unit_cost_5_10 = delta_5_10 / 5.0

        # Discrete steps in Chart 2 must match attainable endpoint unit marginal cost
        # and sum of delta_capex across each module increment must match installed_capex difference
        steps_0_5 = [st for st in steps if 0.0 <= st['capacity_from'] and st['capacity_to'] <= 5.0]
        steps_5_10 = [st for st in steps if 5.0 <= st['capacity_from'] and st['capacity_to'] <= 10.0]

        self.assertEqual(len(steps_0_5), 5)
        self.assertEqual(len(steps_5_10), 5)

        for st in steps_0_5:
            self.assertAlmostEqual(st['marginal_cost_eur_kwh'], unit_cost_0_5, places=4)
            self.assertAlmostEqual(st['delta_capex_eur'], unit_cost_0_5 * st['delta_capacity_kwh'], places=4)
        self.assertAlmostEqual(sum(st['delta_capex_eur'] for st in steps_0_5), delta_0_5, places=4)

        for st in steps_5_10:
            self.assertAlmostEqual(st['marginal_cost_eur_kwh'], unit_cost_5_10, places=4)
            self.assertAlmostEqual(st['delta_capex_eur'], unit_cost_5_10 * st['delta_capacity_kwh'], places=4)
        self.assertAlmostEqual(sum(st['delta_capex_eur'] for st in steps_5_10), delta_5_10, places=4)

    def test_mixed_independent_oracle_multi_step_and_signed_changes(self):
        """Multi-step mixed battery costs match independent installed_capex oracle and retain signed changes."""
        mixed_s = e.validate({
            'battery_mixed_sizes': True,
            'battery_modules': 2,
            'battery_secondary_modules': 1,
            'battery_module_kwh': 5.0,
            'battery_secondary_module_kwh': 6.9,
            'battery_module_cost_override': 3000.0,
            'battery_secondary_module_cost_override': 2000.0,   # Cheaper secondary produces signed step reduction
            'battery_module_extra_cost': 150.0,
            'battery_secondary_module_extra_cost': 100.0,
            'deduction_price_premium': 0.05,
            'deduction_rate': 0.50,
            'installation_vat': 0.10,
            'quote_vat_included': False,
            'south_panels': 12,
            'load_scenario': 'electrified'
        })
        kwp = 12 * 400.0 / 1000.0
        res = e.battery_sizing(mixed_s, {'max_capacity': 15})
        steps = res['chart2_incremental_savings']['steps']

        def indep_cand(m_tot, m_sec, kwh):
            return {
                **mixed_s,
                'battery_mixed_sizes': True,
                'battery_modules': m_tot,
                'battery_secondary_modules': m_sec,
                'battery_module_kwh': 5.0,
                'battery_secondary_module_kwh': 6.9,
                'battery_kwh': float(kwh),
                'quote_total': None,
            }

        known_endpoints = {
            0.0: (0, 0),
            5.0: (1, 0),
            6.9: (1, 1),
            10.0: (2, 0),
            11.9: (2, 1),
            13.8: (2, 2),
            15.0: (3, 0),
        }

        for st in steps:
            c0 = st['capacity_from']
            c1 = st['capacity_to']
            delta_c = st['delta_capacity_kwh']
            if c0 in known_endpoints and c1 in known_endpoints:
                m0, s0_cnt = known_endpoints[c0]
                m1, s1_cnt = known_endpoints[c1]
                s0 = indep_cand(m0, s0_cnt, c0)
                s1 = indep_cand(m1, s1_cnt, c1)
                capex0 = e.installed_capex(s0, kwp)
                capex1 = e.installed_capex(s1, kwp)
                oracle_delta = capex1 - capex0
                self.assertAlmostEqual(st['delta_capex_eur'], oracle_delta, places=4)
                self.assertAlmostEqual(st['marginal_cost_eur_kwh'], oracle_delta / delta_c, places=4)

        # 5.0 -> 6.9 step switch: primary unit (3150) replaced by secondary unit (2100)
        # Delta before premium/VAT is 2100 - 3150 = -1050; with 1.05 * 1.10 = -1212.75
        step_5_69 = next(st for st in steps if math.isclose(st['capacity_from'], 5.0) and math.isclose(st['capacity_to'], 6.9))
        self.assertAlmostEqual(step_5_69['delta_capex_eur'], -1212.75, places=2)
        self.assertLess(step_5_69['marginal_cost_eur_kwh'], 0.0)
        self.assertLess(step_5_69['annualized_cost_gross'], 0.0)
        self.assertLess(step_5_69['annualized_cost_net'], 0.0)

    def test_mixed_battery_overrides_extras_vat_and_premium(self):
        """Mixed battery candidate costing preserves module overrides, extras, VAT, and deduction premium."""
        mixed_s = e.validate({
            'battery_mixed_sizes': True,
            'battery_modules': 2,
            'battery_secondary_modules': 1,
            'battery_module_kwh': 5.0,
            'battery_secondary_module_kwh': 6.9,
            'battery_module_cost_override': 2400.0,
            'battery_secondary_module_cost_override': 3100.0,
            'battery_module_extra_cost': 150.0,
            'battery_secondary_module_extra_cost': 200.0,
            'deduction_price_premium': 0.05,
            'deduction_rate': 0.50,
            'installation_vat': 0.10,
            'quote_vat_included': False,
            'south_panels': 12,
            'load_scenario': 'electrified'
        })
        kwp = 12 * 400.0 / 1000.0
        res = e.battery_sizing(mixed_s, {'max_capacity': 15})

        c_6_9 = {
            **mixed_s,
            'battery_mixed_sizes': True,
            'battery_modules': 1,
            'battery_secondary_modules': 1,
            'battery_module_kwh': 5.0,
            'battery_secondary_module_kwh': 6.9,
            'battery_kwh': 6.9,
            'quote_total': None,
        }
        base_s = {
            **mixed_s,
            'battery_mixed_sizes': True,
            'battery_modules': 0,
            'battery_secondary_modules': 0,
            'battery_module_kwh': 5.0,
            'battery_secondary_module_kwh': 6.9,
            'battery_kwh': 0.0,
            'quote_total': None,
        }
        base_capex = e.installed_capex(base_s, kwp)
        capex_6_9 = e.installed_capex(c_6_9, kwp)

        # Storage portion: (3100 + 200) * 1.05 * 1.10 = 3300 * 1.155 = 3811.5
        expected_bat_capex = (3100.0 + 200.0) * 1.05 * 1.10
        self.assertAlmostEqual(capex_6_9 - base_capex, expected_bat_capex, places=4)

        # Steps in Chart 2 verify per-step marginal cost
        steps = res['chart2_incremental_savings']['steps']
        step_0_5 = next(s for s in steps if math.isclose(s['capacity_from'], 0.0) and math.isclose(s['capacity_to'], 5.0))
        # 1 primary module: (2400 + 150) * 1.05 * 1.10 = 2550 * 1.155 = 2945.25; / 5.0 = 589.05 €/kWh
        self.assertAlmostEqual(step_0_5['marginal_cost_eur_kwh'], 2945.25 / 5.0, places=2)

    def test_explicit_zero_module_cost_override(self):
        """Explicit EUR 0 override is respected and does not fall back to battery_cost_kwh."""
        zero_override_s = {
            **self.base_settings,
            'battery_module_cost_override': 0.0,
            'battery_module_extra_cost': 0.0,
            'quote_vat_included': True,
        }
        res = e.battery_sizing(zero_override_s, {'step': 1, 'max_capacity': 10})
        eco = res['economics']
        self.assertEqual(eco['effective_marginal_battery_cost_eur_kwh'], 0.0)
        self.assertEqual(eco['annualized_marginal_cost_gross'], 0.0)
        self.assertEqual(eco['annualized_marginal_cost_net'], 0.0)

    def test_mixed_explicit_zero_override_with_extras_and_premiums(self):
        """Mixed battery with explicit EUR 0 module overrides respects override and prices only extras + VAT/premium."""
        zero_mixed = e.validate({
            'battery_mixed_sizes': True,
            'battery_modules': 2,
            'battery_secondary_modules': 1,
            'battery_module_kwh': 5.0,
            'battery_secondary_module_kwh': 6.9,
            'battery_module_cost_override': 0.0,
            'battery_secondary_module_cost_override': 0.0,
            'battery_module_extra_cost': 150.0,
            'battery_secondary_module_extra_cost': 200.0,
            'deduction_price_premium': 0.05,
            'deduction_rate': 0.50,
            'installation_vat': 0.10,
            'quote_vat_included': False,
            'south_panels': 12,
            'load_scenario': 'electrified'
        })
        kwp = 12 * 400.0 / 1000.0
        res = e.battery_sizing(zero_mixed, {'max_capacity': 10})
        steps = res['chart2_incremental_savings']['steps']
        # 0 -> 5.0 kWh: 1 primary module with cost = (0 + 150) * 1.05 * 1.10 = 173.25 EUR
        step_0_5 = next(st for st in steps if math.isclose(st['capacity_from'], 0.0) and math.isclose(st['capacity_to'], 5.0))
        self.assertAlmostEqual(step_0_5['delta_capex_eur'], 173.25, places=2)
        self.assertAlmostEqual(step_0_5['marginal_cost_eur_kwh'], 173.25 / 5.0, places=2)

    def test_quote_variants_precedence_and_piecewise_slope(self):
        """Quote variants supersede benchmark pricing, define piecewise marginal cost slope and correct baseline."""
        q_options = {
            'quote_10kwh_eur': 6000.0,
            'quote_15kwh_eur': 8500.0,
            'battery_lifetime_years': 10,
        }
        res = e.battery_sizing(self.base_settings, q_options)
        eco = res['economics']
        self.assertEqual(eco['marginal_cost_source'], 'quote_variants')
        self.assertEqual(eco['reference_candidate_description'], 'Quote variants (10 → 15 kWh increment)')
        expected_marginal = (8500.0 - 6000.0) / 5.0  # 500 €/kWh
        self.assertAlmostEqual(eco['effective_marginal_battery_cost_eur_kwh'], expected_marginal, places=2)
        self.assertAlmostEqual(eco['annualized_marginal_cost_gross'], 50.0, places=2)

        # Chart 2 steps between 10 and 15 kWh should have 500 €/kWh marginal cost
        steps = res['chart2_incremental_savings']['steps']
        step_10_11 = next(s for s in steps if s['capacity_from'] == 10 and s['capacity_to'] == 11)
        self.assertAlmostEqual(step_10_11['marginal_cost_eur_kwh'], 500.0, places=2)

    def test_deduction_tax_capacity_cap_and_step_variation(self):
        """When tax capacity is reached, deduction caps out and marginal cost variance is flagged."""
        kwp = 12 * 400.0 / 1000.0
        base_s = {
            **self.base_settings,
            'battery_mixed_sizes': False,
            'battery_modules': 0,
            'battery_secondary_modules': 0,
            'battery_kwh': 0.0,
            'quote_total': None,
        }
        base_capex = e.installed_capex(base_s, kwp)
        base_annual_ded = base_capex * self.base_settings['deduction_eligible_fraction'] * 0.50 / 10.0
        capped_s = e.validate({
            **self.base_settings,
            'deduction_tax_capacity': float(base_annual_ded + 50.0),
            'deduction_rate': 0.50,
            'battery_cost_kwh': 800.0,
        })
        res = e.battery_sizing(capped_s, {'step': 1, 'max_capacity': 10})
        inc = res['chart2_incremental_savings']
        self.assertTrue(inc['marginal_cost_varies_by_step'])

    def test_quoted_mixed_cells_survive_validate_and_evaluate_parity(self):
        """Mixed heatmap cells with quote variants preserve coherent composition and survive validate/evaluate."""
        s = e.validate({
            'south_panels': 12,
            'battery_mixed_sizes': True,
            'battery_modules': 2,
            'battery_secondary_modules': 1,
            'battery_module_kwh': 5.0,
            'battery_secondary_module_kwh': 6.9,
            'battery_module_cost_override': 2400.0,
            'battery_secondary_module_cost_override': 3100.0,
            'battery_module_extra_cost': 150.0,
            'battery_secondary_module_extra_cost': 200.0,
            'load_scenario': 'electrified',
        })
        opt = {'max_capacity': 10, 'quote_10kwh_eur': 6000.0, 'quote_15kwh_eur': 8500.0}
        res = e.battery_sizing(s, opt)
        hm = res['chart4_npv_heatmap']
        cells = hm['cells']
        self.assertGreater(len(cells), 0)

        # Check the 6.9-kWh cell specifically (root failure case)
        c_69 = next(c for c in cells if math.isclose(c['battery_kwh'], 6.9, abs_tol=1e-4) and math.isclose(c['pv_kwp'], 4.8, abs_tol=1e-4))
        self.assertEqual(c_69['settings']['battery_modules'], 1)
        self.assertEqual(c_69['settings']['battery_secondary_modules'], 1)
        self.assertEqual(c_69['settings']['battery_kwh'], 6.9)

        # Check ALL heatmap cells for validated capacity, capex, and NPV parity
        for cell in cells:
            cell_settings = cell['settings']
            val_s = e.validate(cell_settings)
            self.assertAlmostEqual(val_s['battery_kwh'], cell['battery_kwh'], places=5)
            if cell['battery_kwh'] > 0:
                self.assertGreater(val_s['battery_modules'], 0)
                if val_s['battery_mixed_sizes']:
                    self.assertEqual(val_s['battery_modules'], cell['composition']['battery_modules'])
                    self.assertEqual(val_s['battery_secondary_modules'], cell['composition']['battery_secondary_modules'])
            ev = e.evaluate(cell_settings)
            self.assertAlmostEqual(ev['finance']['capex_eur'], cell['capex_eur'], places=2)
            self.assertAlmostEqual(ev['finance']['pv_incremental']['npv']['median'], cell['npv_median'], places=2)

    def test_all_heatmap_cells_including_analytical_samples_match_evaluate(self):
        """Every cell in single-size heatmap (including 2.5, 7.5, 12 kWh) matches evaluate() capex and NPV."""
        s = e.validate({
            'south_panels': 12,
            'battery_modules': 2,
            'battery_module_kwh': 5.0,
            'battery_cost_kwh': 500.0,
            'battery_module_cost_override': 2400.0,
            'battery_module_extra_cost': 150.0,
            'load_scenario': 'electrified',
        })
        res = e.battery_sizing(s, {'step': 1, 'max_capacity': 15})
        hm = res['chart4_npv_heatmap']
        cells = hm['cells']
        self.assertGreater(len(cells), 0)

        for cell in cells:
            val_s = e.validate(cell['settings'])
            self.assertAlmostEqual(val_s['battery_kwh'], cell['battery_kwh'], places=5)
            if cell['battery_kwh'] in (2.5, 7.5, 12.0):
                self.assertTrue(cell.get('is_analytical_sample'))
                self.assertEqual(cell.get('capacity_nature'), 'analytical_pro_rata')
            elif cell['battery_kwh'] > 0:
                self.assertFalse(cell.get('is_analytical_sample'))
                self.assertEqual(cell.get('capacity_nature'), 'hardware_multiple')
            ev = e.evaluate(cell['settings'])
            self.assertAlmostEqual(ev['finance']['capex_eur'], cell['capex_eur'], places=2)
            self.assertAlmostEqual(ev['finance']['pv_incremental']['npv']['median'], cell['npv_median'], places=2)

    def test_input_quote_normalized_pricing_coverage(self):
        """Supplying an itemized quote normalizes equipment specs while decoupling turnkey total for sizing sweep."""
        quote = {
            'line_items': [
                {'description': 'Turnkey PV + Battery Package', 'net_eur': 14000.0, 'vat': 0.10, 'scope': 'solar_storage'}
            ],
            'roundtrip_efficiency': 0.94,
            'continuous_charge_kw': 6.0,
            'continuous_discharge_kw': 6.0,
        }
        res = e.battery_sizing(self.base_settings, {'step': 1, 'max_capacity': 10}, quote=quote)
        self.assertAlmostEqual(res['economics']['roundtrip_efficiency'], 0.94)
        sample_cell = res['chart4_npv_heatmap']['cells'][0]
        self.assertIsNone(sample_cell['settings']['quote_total'])
        ev = e.evaluate(sample_cell['settings'])
        self.assertAlmostEqual(sample_cell['capex_eur'], ev['finance']['capex_eur'], places=2)

    def test_mixed_exact_regression_reference_numbers(self):
        """Mixed battery benchmark fixture evaluates 0->5 savings ~38.98 and weather optimum 5-6.9 (median 5)."""
        mixed_benchmark = e.validate({
            'south_panels': 12,
            'battery_mixed_sizes': True,
            'battery_modules': 2,
            'battery_module_kwh': 5.0,
            'battery_secondary_module_kwh': 6.9,
            'battery_cost_kwh': 500.0,
            'quote_vat_included': False,
            'installation_vat': 0.10,
            'battery_replace_year': 12,
            'deduction_rate': 0.5,
            'principal_residence_confirmed': True, 'deduction_tax_capacity': 10000.0,
            'load_scenario': 'electrified',
        })
        res = e.battery_sizing(mixed_benchmark, {'max_capacity': 25, 'battery_lifetime_years': 12, 'deduction_rate': 0.5})
        inc = res['chart2_incremental_savings']
        st0 = inc['steps'][0]
        self.assertEqual(st0['capacity_from'], 0.0)
        self.assertEqual(st0['capacity_to'], 5.0)
        rows = res['chart1_capacity_dispatch']['capacities']
        expected = (np.array(rows[1]['annual_savings_by_weather'])-np.array(rows[0]['annual_savings_by_weather']))/5.
        np.testing.assert_allclose(st0['incremental_savings_by_weather'], expected, rtol=0, atol=1e-12)
        self.assertGreater(st0['incremental_savings_eur_kwh']['median'], 35.)

        opt_band = inc['optimum_band']
        self.assert_synthetic_optimum(res, mixed_benchmark, 550.)
        self.assertGreaterEqual(opt_band['weather_min_kwh'], 5.)

    def test_mixed_equal_capacity_current_configuration_parity_and_representative_behavior(self):
        """Equal-capacity mixed hardware: current cell composition metadata matches actual evaluated settings,
        while other PV cells retain deterministic representative behavior."""
        settings = {
            'south_panels': 12,
            'west_panels': 0,
            'east_panels': 0,
            'battery_mixed_sizes': True,
            'battery_modules': 2,
            'battery_secondary_modules': 0,
            'battery_module_kwh': 5.0,
            'battery_secondary_module_kwh': 10.0,
            'battery_module_cost_override': 3000.0,
            'battery_secondary_module_cost_override': 2000.0,
            'load_scenario': 'electrified',
        }
        res = e.battery_sizing(settings, {'max_capacity': 10})
        hm = res['chart4_npv_heatmap']
        cur_cfg = hm['current_configuration']
        cur_pv = cur_cfg['pv_kwp']
        cells = hm['cells']

        # Locate exact current cell (matching current PV and selected battery capacity)
        current_cell = next(
            c for c in cells
            if math.isclose(c['pv_kwp'], cur_pv, abs_tol=1e-4) and math.isclose(c['battery_kwh'], 10.0, abs_tol=1e-4)
        )
        # Locate another PV cell at the same 10.0 kWh capacity
        other_pv_cell = next(
            c for c in cells
            if not math.isclose(c['pv_kwp'], cur_pv, abs_tol=1e-4) and math.isclose(c['battery_kwh'], 10.0, abs_tol=1e-4)
        )

        # 1. Parity at current cell: composition metadata matches actual evaluated cell settings
        self.assertEqual(current_cell['composition']['battery_modules'], 2)
        self.assertEqual(current_cell['composition']['battery_secondary_modules'], 0)
        self.assertEqual(current_cell['composition']['first_count'], 2)
        self.assertEqual(current_cell['composition']['secondary_count'], 0)
        self.assertEqual(current_cell['composition']['battery_kwh'], 10.0)
        self.assertEqual(current_cell['composition']['id'], '2m_0sec')
        self.assertEqual(current_cell['composition'], cur_cfg['composition'])

        self.assertEqual(current_cell['settings']['battery_modules'], 2)
        self.assertEqual(current_cell['settings']['battery_secondary_modules'], 0)
        self.assertEqual(current_cell['settings']['battery_kwh'], 10.0)

        # Capex matches 2x5 kWh cost (override 2*3000 = 6000 plus base)
        self.assertAlmostEqual(current_cell['capex_eur'], 1000.0 + 12*.4*1000.0 + 2*3000.0, places=2)
        self.assertAlmostEqual(
            current_cell['capex_eur'],
            e.installed_capex(current_cell['settings'], current_cell['pv_kwp']),
            places=2
        )
        ev_cur = e.evaluate(current_cell['settings'])
        self.assertAlmostEqual(current_cell['capex_eur'], ev_cur['finance']['capex_eur'], places=2)
        self.assertAlmostEqual(current_cell['npv_median'], ev_cur['finance']['pv_incremental']['npv']['median'], places=2)

        # 2. Representative behavior retained at another PV (1x10 kWh secondary module)
        self.assertEqual(other_pv_cell['composition']['battery_modules'], 1)
        self.assertEqual(other_pv_cell['composition']['battery_secondary_modules'], 1)
        self.assertEqual(other_pv_cell['composition']['first_count'], 0)
        self.assertEqual(other_pv_cell['composition']['secondary_count'], 1)
        self.assertEqual(other_pv_cell['composition']['battery_kwh'], 10.0)
        self.assertEqual(other_pv_cell['composition']['id'], '1m_1sec')

        self.assertEqual(other_pv_cell['settings']['battery_modules'], 1)
        self.assertEqual(other_pv_cell['settings']['battery_secondary_modules'], 1)
        self.assertEqual(other_pv_cell['settings']['battery_kwh'], 10.0)

        self.assertAlmostEqual(
            other_pv_cell['capex_eur'],
            e.installed_capex(other_pv_cell['settings'], other_pv_cell['pv_kwp']),
            places=2
        )
        ev_other = e.evaluate(other_pv_cell['settings'])
        self.assertAlmostEqual(other_pv_cell['capex_eur'], ev_other['finance']['capex_eur'], places=2)
        self.assertAlmostEqual(other_pv_cell['npv_median'], ev_other['finance']['pv_incremental']['npv']['median'], places=2)

        # 3. Available compositions retain full candidate mix
        chart1_caps = res['chart1_capacity_dispatch']['capacities']
        cap_10_row = next(r for r in chart1_caps if math.isclose(r['capacity_kwh'], 10.0, abs_tol=1e-4))
        avail_ids = [c['id'] for c in cap_10_row['available_compositions']]
        self.assertIn('1m_1sec', avail_ids)
        self.assertIn('2m_0sec', avail_ids)


if __name__ == '__main__':
    unittest.main()


