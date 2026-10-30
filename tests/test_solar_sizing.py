"""Numerical and route integration tests for solar sizing and joint storage optimization."""
from copy import deepcopy
import io
import json
import math
import unittest
from unittest.mock import patch

import numpy as np

import feasibility.engine as e
import feasibility.server as server_module
from historical_weather import frozen_baseline_weather_context
try:
    from test_support import IsolatedDefaultsTestCase
except ImportError:
    from tests.test_support import IsolatedDefaultsTestCase


class SolarSizingNumericalTests(IsolatedDefaultsTestCase):
    def setUp(self):
        super().setUp()
        self.base_settings = {
            'pv_dispatch_model': 'physical',
            'south_panels': 12,
            'west_panels': 0,
            'east_panels': 0,
            'dormer_panels': 0,
            'flat_panels': 0,
            'secondary_south_panels': 0,
            'battery_kwh': 5.0,
            'roundtrip_efficiency': 0.9,
            'inverter_kw': 6.0,
            'charge_kw': 5.0,
            'discharge_kw': 5.0,
            'horizon_years': 20,
            'panel_watts': 440,
            'pv_fixed_cost': 1500,
            'pv_cost_kwp': 900,
            'battery_cost_kwh': 550,
            'deduction_rate': 0.5,
            'load_scenario': 'electrified'
        }

    def test_zero_pv_boundary_and_no_install(self):
        """At 0 panels, only 0 kWh storage is physically valid; no-install baseline has zero capex and NPV."""
        res = e.solar_sizing(self.base_settings, {
            'panel_maxima': {'south': 3, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_kwh': 0,
            'battery_max_kwh': 5,
            'battery_step_kwh': 5,
            'evaluation_budget': 100
        })
        size_curve = res['size_curve']
        c0 = next((c for c in size_curve if c['panel_count'] == 0), None)
        self.assertIsNotNone(c0, "Size curve contains 0-panel baseline")
        self.assertEqual(c0['kwp'], 0.0)
        self.assertEqual(c0['battery_kwh'], 0.0)
        self.assertEqual(c0['capex_eur'], 0.0)
        self.assertEqual(c0['npv_median'], 0.0)
        self.assertEqual(c0['autarky_median'], 0.0)
        self.assertEqual(c0['self_consumption_median'], 0.0)
        self.assertIsNone(c0['discounted_payback_years'])

        # Check comparisons list contains no_install baseline
        no_install = next(c for c in res['comparisons'] if c['id'] == 'no_install')
        self.assertEqual(no_install['panel_count'], 0)
        self.assertEqual(no_install['battery_kwh'], 0.0)
        self.assertEqual(no_install['capex_eur'], 0.0)

    def test_integer_roof_bounds_and_exact_kwp(self):
        """Panel allocations must be whole numbers, respect roof maxima, and derive exact kWp."""
        panel_watts = 440
        roof_max = {'south': 8, 'west': 4, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        res = e.solar_sizing(self.base_settings, {
            'panel_maxima': roof_max,
            'battery_min_kwh': 0,
            'battery_max_kwh': 10,
            'battery_step_kwh': 5
        })
        rec = res['recommended_candidate']
        counts = rec['roof_counts']
        for r, limit in roof_max.items():
            self.assertIsInstance(counts[r], int)
            self.assertGreaterEqual(counts[r], 0)
            self.assertLessEqual(counts[r], limit)

        total_panels = sum(counts.values())
        self.assertEqual(rec['panel_count'], total_panels)
        expected_kwp = round(total_panels * panel_watts / 1000.0, 3)
        self.assertAlmostEqual(rec['kwp'], expected_kwp, places=3)

    def test_fixed_fractional_battery_capacity(self):
        """In fixed battery mode, exact fractional capacity is preserved without module rounding."""
        settings_fractional = {**self.base_settings, 'battery_kwh': 3.75, 'battery_modules': 1}
        res = e.solar_sizing(settings_fractional, {
            'battery_mode': 'fixed',
            'panel_maxima': {'south': 4, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        })
        rec = res['recommended_candidate']
        self.assertEqual(rec['battery_kwh'], 3.75)
        self.assertEqual(res['search_summary']['battery_mode'], 'fixed')
        self.assertEqual(res['search_summary']['battery_capacities_evaluated'], [3.75])

        for c in res['size_curve']:
            if c['panel_count'] > 0:
                self.assertEqual(c['battery_kwh'], 3.75)

    def test_joint_search_improves_or_matches_fixed(self):
        """Joint optimization over battery range [0..10] must match or beat fixed battery within that range."""
        roof_max = {'south': 6, 'west': 2, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        # Fixed at 5 kWh
        res_fixed = e.solar_sizing(self.base_settings, {
            'objective': 'npv_eur',
            'battery_mode': 'fixed',
            'panel_maxima': roof_max
        })
        # Joint search including 5 kWh
        res_joint = e.solar_sizing(self.base_settings, {
            'objective': 'npv_eur',
            'battery_mode': 'joint',
            'battery_min_kwh': 0,
            'battery_max_kwh': 10,
            'battery_step_kwh': 5,
            'panel_maxima': roof_max
        })
        npv_fixed = res_fixed['recommended_candidate']['npv_median']
        npv_joint = res_joint['recommended_candidate']['npv_median']
        self.assertGreaterEqual(npv_joint, npv_fixed - 1e-6, "Joint optimization must be >= fixed battery candidate")

    def test_orientation_preservation_and_inverter_clipping(self):
        """Orientations are maintained separately; inverter AC limit is held fixed."""
        roof_max = {'south': 6, 'west': 6, 'east': 4, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        res = e.solar_sizing(self.base_settings, {
            'panel_maxima': roof_max,
            'battery_min_kwh': 0,
            'battery_max_kwh': 5,
            'battery_step_kwh': 5
        })
        rec = res['recommended_candidate']
        self.assertTrue(rec['inverter_held_fixed'])
        self.assertEqual(rec['inverter_kw'], 6.0)
        self.assertEqual(res['search_summary']['inverter_kw'], 6.0)
        self.assertIn('Fixed selected inverter', rec['inverter_status'])

    def test_scenario_and_ev_timing(self):
        """Different load scenarios produce coherent load demands and financial returns."""
        roof_max = {'south': 5, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        res_base = e.solar_sizing(self.base_settings, {'load_scenario': 'baseline', 'panel_maxima': roof_max, 'battery_max_kwh': 5})
        res_elec = e.solar_sizing(self.base_settings, {'load_scenario': 'electrified', 'panel_maxima': roof_max, 'battery_max_kwh': 5})
        res_ev = e.solar_sizing(self.base_settings, {'load_scenario': 'ev', 'panel_maxima': roof_max, 'battery_max_kwh': 5})

        self.assertEqual(res_base['recommended_candidate']['settings']['load_scenario'], 'baseline')
        self.assertEqual(res_elec['recommended_candidate']['settings']['load_scenario'], 'electrified')
        self.assertEqual(res_ev['recommended_candidate']['settings']['load_scenario'], 'ev')

        # Electrified and EV demand are higher than baseline, leading to higher self-consumption
        c_base = next(c for c in res_base['size_curve'] if c['panel_count'] == 5)
        c_elec = next(c for c in res_elec['size_curve'] if c['panel_count'] == 5)
        self.assertGreater(c_elec['self_consumption_median'], c_base['self_consumption_median'])

    def test_capex_scales_with_size(self):
        """Capex strictly increases with solar panel count on the size curve."""
        roof_max = {'south': 6, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        res = e.solar_sizing(self.base_settings, {
            'battery_mode': 'fixed',
            'panel_maxima': roof_max
        })
        curve = res['size_curve']
        for i in range(1, len(curve)):
            self.assertGreater(curve[i]['capex_eur'], curve[i - 1]['capex_eur'])

    def test_exhaustive_small_space_certified_optimal(self):
        """When total search space <= 200, exhaustive integer enumeration certifies global optimum."""
        roof_max = {'south': 2, 'west': 1, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        # (3 south * 2 west) * 2 batteries = 12 combinations <= 200
        res = e.solar_sizing(self.base_settings, {
            'objective': 'npv_eur',
            'battery_min_kwh': 0,
            'battery_max_kwh': 5,
            'battery_step_kwh': 5,
            'panel_maxima': roof_max
        })
        summary = res['search_summary']
        self.assertTrue(summary['exact'])
        self.assertTrue(summary['certified_global_optimum'])
        self.assertFalse(summary['best_found'])
        self.assertEqual(summary['search_method'], 'exhaustive_integer_enumeration')
        # 11 feasible combinations: 5 non-zero panel combinations * 2 batteries + 1 zero-PV baseline (storage without PV excluded)
        self.assertEqual(summary['evaluated_count'], 11)

        # Check winner against brute-force reference
        rec = res['recommended_candidate']
        all_cells = res['tradeoff_matrix']['cells']
        max_npv_in_cells = max(c['npv_median'] for c in all_cells)
        self.assertAlmostEqual(rec['npv_median'], max_npv_in_cells, places=3)

    def test_bounded_sampling_labeled_best_found(self):
        """When search space > 200, results are explicitly labeled best_found within budget."""
        roof_max = {'south': 10, 'west': 6, 'east': 4, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        res = e.solar_sizing(self.base_settings, {
            'battery_min_kwh': 0,
            'battery_max_kwh': 15,
            'battery_step_kwh': 5,
            'panel_maxima': roof_max,
            'evaluation_budget': 150
        })
        summary = res['search_summary']
        self.assertFalse(summary['exact'])
        self.assertFalse(summary['certified_global_optimum'])
        self.assertTrue(summary['best_found'])
        self.assertEqual(summary['search_method'], 'bounded_discrete_sampling_and_refinement')
        self.assertLessEqual(summary['evaluated_count'], 150)
        self.assertIn('best-found rather than certified global optima', summary['limitations'][0])

    def test_marginal_diminishing_returns(self):
        """Size curve reports incremental marginal returns per panel and per kWp."""
        roof_max = {'south': 6, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        res = e.solar_sizing(self.base_settings, {
            'battery_mode': 'fixed',
            'panel_maxima': roof_max
        })
        curve = res['size_curve']
        self.assertIsNone(curve[0]['marginal_npv_per_panel'])
        for i in range(1, len(curve)):
            self.assertIn('npv_median', curve[i])
            self.assertTrue(math.isfinite(curve[i]['npv_median']))
            dn = curve[i]['panel_count'] - curve[i - 1]['panel_count']
            dnpv = curve[i]['npv_mean'] - curve[i - 1]['npv_mean']
            expected_m = round(dnpv / dn, 2)
            self.assertAlmostEqual(curve[i]['marginal_npv_per_panel'], expected_m, places=2)

    def test_null_payback_not_treated_as_zero(self):
        """Unrecovered payback is represented as None or finite year, never 0."""
        res = e.solar_sizing(self.base_settings, {
            'panel_maxima': {'south': 4, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_max_kwh': 5
        })
        for c in res['size_curve']:
            pb = c['discounted_payback_years']
            if pb is not None:
                self.assertGreater(pb, 0.0)
            if c['panel_count'] == 0:
                self.assertIsNone(pb)

    def test_options_validation_and_malformed_rejection(self):
        """Rejects malformed inputs, booleans masquerading as numbers, inverted bounds and invalid objectives."""
        with self.assertRaises(ValueError):
            e.solar_sizing(self.base_settings, {'unknown_key': 123})
        with self.assertRaises(ValueError):
            e.solar_sizing(self.base_settings, {'objective': 'invalid_obj'})
        with self.assertRaises(ValueError):
            e.solar_sizing(self.base_settings, {'battery_min_kwh': True})  # bool disguised as number
        with self.assertRaises(ValueError):
            e.solar_sizing(self.base_settings, {'battery_min_kwh': 10, 'battery_max_kwh': 5})  # inverted
        with self.assertRaises(ValueError):
            e.solar_sizing(self.base_settings, {'battery_min_kwh': -1})  # negative
        with self.assertRaises(ValueError):
            e.solar_sizing(self.base_settings, {'deduction_rate': 0.75})  # invalid deduction
        with self.assertRaises(ValueError):
            e.solar_sizing(self.base_settings, {'panel_maxima': {'unknown_roof': 5}})
        with self.assertRaises(ValueError):
            e.solar_sizing(self.base_settings, {'evaluation_budget': 10})  # below 50

    def test_independent_oracle_enumeration(self):
        """Verify recommended system and all cell evaluations match an independent brute-force evaluation using e.evaluate."""
        roof_max = {'south': 2, 'west': 1, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        options = {
            'objective': 'npv_eur',
            'battery_mode': 'joint',
            'battery_min_kwh': 0,
            'battery_max_kwh': 5,
            'battery_step_kwh': 5,
            'panel_maxima': roof_max
        }
        res = e.solar_sizing(self.base_settings, options)
        rec = res['recommended_candidate']

        best_oracle = None
        oracle_cells = {}
        for s in range(3):
            for w in range(2):
                for b in [0.0, 5.0]:
                    if s == 0 and w == 0 and b > 0:
                        continue
                    cand_settings = {
                        **self.base_settings,
                        'south_panels': s,
                        'west_panels': w,
                        'east_panels': 0,
                        'dormer_panels': 0,
                        'flat_panels': 0,
                        'secondary_south_panels': 0,
                        'battery_kwh': b,
                        'battery_modules': 1 if b > 0 else 0,
                        'battery_module_kwh': b if b > 0 else 5.0,
                        'dormer_south_proxy': False,
                        'quote_total': None
                    }
                    if s == 0 and w == 0 and b == 0:
                        eval_npv = 0.0
                        eval_capex = 0.0
                    else:
                        eval_res = e.evaluate(cand_settings)
                        eval_npv = eval_res['finance']['pv_incremental']['npv']['median']
                        eval_capex = eval_res['finance']['capex_eur']

                    entry = {
                        'south': s,
                        'west': w,
                        'panel_count': s + w,
                        'battery_kwh': b,
                        'capex_eur': eval_capex,
                        'npv_median': eval_npv
                    }
                    oracle_cells[(s, w, b)] = entry

                    if best_oracle is None:
                        best_oracle = entry
                    else:
                        better = False
                        if eval_npv > best_oracle['npv_median'] + 1e-6:
                            better = True
                        elif abs(eval_npv - best_oracle['npv_median']) <= 1e-6:
                            if eval_capex < best_oracle['capex_eur'] - 1e-6:
                                better = True
                            elif abs(eval_capex - best_oracle['capex_eur']) <= 1e-6:
                                if (s + w) < best_oracle['panel_count']:
                                    better = True
                                elif (s + w) == best_oracle['panel_count'] and b < best_oracle['battery_kwh']:
                                    better = True
                        if better:
                            best_oracle = entry

        self.assertEqual(rec['roof_counts']['south'], best_oracle['south'])
        self.assertEqual(rec['roof_counts']['west'], best_oracle['west'])
        self.assertEqual(rec['panel_count'], best_oracle['panel_count'])
        self.assertEqual(rec['battery_kwh'], best_oracle['battery_kwh'])
        self.assertAlmostEqual(rec['capex_eur'], best_oracle['capex_eur'], places=2)
        self.assertAlmostEqual(rec['npv_median'], best_oracle['npv_median'], places=2)

        for cell in res['tradeoff_matrix']['cells']:
            b_kwh = cell['battery_kwh']
            p_cnt = cell['panel_count']
            matches = [o for o in oracle_cells.values() if o['panel_count'] == p_cnt and o['battery_kwh'] == b_kwh]
            best_cell_oracle = max(matches, key=lambda x: x['npv_median'])
            self.assertAlmostEqual(cell['npv_median'], best_cell_oracle['npv_median'], places=2)
            self.assertAlmostEqual(cell['capex_eur'], best_cell_oracle['capex_eur'], places=2)

    def test_marginal_panel_cost_sensitivity(self):
        """Expensive panels produce negative marginal returns and select 0 panels; cheap panels produce positive marginal returns."""
        # Case A: Expensive panels (€5000/kWp, no deduction)
        expensive_settings = {
            **self.base_settings,
            'pv_fixed_cost': 2000,
            'pv_cost_kwp': 5000,
            'deduction_rate': 0.0,
            'battery_cost_kwh': 1000
        }
        res_exp = e.solar_sizing(expensive_settings, {
            'panel_maxima': {'south': 4, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_kwh': 0,
            'battery_max_kwh': 0,
            'battery_step_kwh': 5
        })
        curve_exp = res_exp['size_curve']
        for i in range(1, len(curve_exp)):
            self.assertLess(curve_exp[i]['marginal_npv_per_panel'], 0.0)
            self.assertLess(curve_exp[i]['npv_median'], 0.0)
        self.assertEqual(res_exp['recommended_candidate']['panel_count'], 0)

        # Case B: Cheap panels (€150/kWp, 50% deduction)
        cheap_settings = {
            **self.base_settings,
            'pv_fixed_cost': 200,
            'pv_cost_kwp': 150,
            'deduction_rate': 0.5,
            'battery_cost_kwh': 200
        }
        res_cheap = e.solar_sizing(cheap_settings, {
            'panel_maxima': {'south': 4, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_kwh': 0,
            'battery_max_kwh': 0,
            'battery_step_kwh': 5
        })
        curve_cheap = res_cheap['size_curve']
        for i in range(2, len(curve_cheap)):
            self.assertGreater(curve_cheap[i]['marginal_npv_per_panel'], 0.0)
        self.assertEqual(res_cheap['recommended_candidate']['panel_count'], 4)
        self.assertGreater(res_cheap['recommended_candidate']['npv_median'], 3000.0)

    def test_inverter_clipping_reduces_generation_and_npv(self):
        """Restricting inverter AC capacity clips generation on large arrays, reducing PV output and NPV."""
        large_pv_settings = {
            **self.base_settings,
            'south_panels': 33,
            'panel_watts': 480,
            'pv_max_kwp': 16.0,
            'west_panels': 0,
            'east_panels': 0,
            'dormer_panels': 0,
            'flat_panels': 0,
            'secondary_south_panels': 0,
            'battery_kwh': 0.0,
            'battery_modules': 0,
            'quote_total': None
        }
        eval_clipped = e.evaluate({**large_pv_settings, 'inverter_kw': 4.0})
        eval_unclipped = e.evaluate({**large_pv_settings, 'inverter_kw': 16.0})

        pv_clipped = eval_clipped['metrics']['pv_kwh']['median']
        pv_unclipped = eval_unclipped['metrics']['pv_kwh']['median']
        self.assertLess(pv_clipped, pv_unclipped * 0.85, "4 kW AC inverter must clip at least 15% of a 15.84 kWp array")
        self.assertLess(eval_clipped['finance']['pv_incremental']['npv']['median'],
                        eval_unclipped['finance']['pv_incremental']['npv']['median'])

        res = e.solar_sizing({**large_pv_settings, 'inverter_kw': 3.0}, {
            'panel_maxima': {'south': 15, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_kwh': 0,
            'battery_max_kwh': 0,
            'battery_step_kwh': 5
        })
        rec = res['recommended_candidate']
        self.assertEqual(rec['inverter_kw'], 3.0)
        self.assertTrue(rec['inverter_held_fixed'])
        self.assertIn('Fixed selected inverter (3.00 kW)', rec['inverter_status'])

    def test_delayed_ev_demand_and_no_install_parity(self):
        """No-install baseline and candidates respect delayed EV activation across all 19 weather years."""
        delayed_ev_settings = {
            **self.base_settings,
            'load_scenario': 'ev',
            'ev_start_year': 5,
            'summer_ac_multiplier': 1.5
        }
        res = e.solar_sizing(delayed_ev_settings, {
            'panel_maxima': {'south': 3, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_kwh': 0,
            'battery_max_kwh': 0,
            'battery_step_kwh': 5
        })
        no_install_comp = next(c for c in res['comparisons'] if c['id'] == 'no_install')

        zero_settings = {
            **delayed_ev_settings,
            'south_panels': 0, 'west_panels': 0, 'east_panels': 0,
            'dormer_panels': 0, 'flat_panels': 0, 'secondary_south_panels': 0,
            'battery_kwh': 0.0, 'battery_modules': 0,
            'quote_total': None
        }
        eval_zero = e.evaluate(zero_settings)
        expected_import = eval_zero['metrics']['grid_import_kwh']['median']

        self.assertAlmostEqual(no_install_comp['annual_grid_import_kwh_median'], expected_import, places=1)
        c0 = next(c for c in res['size_curve'] if c['panel_count'] == 0)
        self.assertAlmostEqual(c0['annual_grid_import_kwh_median'], expected_import, places=1)

    def test_joint_mode_canonicalizes_existing_storage_cost(self):
        """Joint mode rejects or canonicalizes storage_cost_mode='existing' to 'purchased'; batteries are not free."""
        res = e.solar_sizing(self.base_settings, {
            'battery_mode': 'joint',
            'storage_cost_mode': 'existing',
            'battery_min_kwh': 0,
            'battery_max_kwh': 5,
            'battery_step_kwh': 5,
            'panel_maxima': {'south': 3, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        })
        self.assertEqual(res['search_summary']['storage_cost_mode'], 'purchased')
        cells = res['tradeoff_matrix']['cells']
        cell_0b = next(c for c in cells if c['battery_kwh'] == 0 and c['panel_count'] == 3)
        cell_5b = next(c for c in cells if c['battery_kwh'] == 5 and c['panel_count'] == 3)
        self.assertGreater(cell_5b['capex_eur'], cell_0b['capex_eur'] + 1000)

    def test_fixed_mode_space_and_zero_roof_maxima(self):
        """Fixed mode evaluates only current battery capacity; zero roof maxima returns graceful NO_FEASIBLE_CONFIGURATIONS."""
        res_fixed = e.solar_sizing(self.base_settings, {
            'battery_mode': 'fixed',
            'panel_maxima': {'south': 50, 'west': 20, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'evaluation_budget': 100
        })
        self.assertEqual(res_fixed['search_summary']['battery_capacities_evaluated'], [5.0])
        for c in res_fixed['size_curve']:
            if c['panel_count'] > 0:
                self.assertEqual(c['battery_kwh'], 5.0)

        res_zero_roof = e.solar_sizing(self.base_settings, {
            'battery_mode': 'fixed',
            'panel_maxima': {'south': 0, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        })
        self.assertEqual(res_zero_roof['status'], 'NO_FEASIBLE_CONFIGURATIONS')
        self.assertIsNone(res_zero_roof['recommended_candidate'])
        self.assertEqual(res_zero_roof['search_summary']['evaluated_count'], 0)

    def test_linked_battery_fields_consistency(self):
        """Fractional battery capacities have modules >= 1 and matching module_kwh * modules == battery_kwh."""
        res = e.solar_sizing(self.base_settings, {
            'battery_mode': 'joint',
            'battery_min_kwh': 2.5,
            'battery_max_kwh': 7.5,
            'battery_step_kwh': 2.5,
            'panel_maxima': {'south': 3, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        })
        for c in res['size_curve']:
            b = c['battery_kwh']
            mods = c['battery_modules']
            mod_kwh = c['battery_module_kwh']
            cand_s = c['settings']
            if b > 0:
                self.assertGreaterEqual(mods, 1, f"Modules must be >= 1 for positive storage {b}")
                self.assertAlmostEqual(mods * mod_kwh, b, places=3)
                self.assertEqual(cand_s['battery_modules'], mods)
                self.assertAlmostEqual(cand_s['battery_module_kwh'], mod_kwh, places=3)

        for cell in res['tradeoff_matrix']['cells']:
            b = cell['battery_kwh']
            mods = cell['battery_modules']
            mod_kwh = cell['battery_module_kwh']
            if b > 0:
                self.assertGreaterEqual(mods, 1)
                self.assertAlmostEqual(mods * mod_kwh, b, places=3)

    def test_battery_step_preflight_and_endpoints(self):
        """Validates hardware module capacity, rejects >50 step candidate count, supports 0.4 and >25 kWh modules."""
        # Non-positive or invalid legacy step options are rejected
        with self.assertRaises(ValueError):
            e.solar_sizing(self.base_settings, {'battery_step_kwh': 0})
        with self.assertRaises(ValueError):
            e.solar_sizing(self.base_settings, {'battery_step_kwh': -1.0})
        with self.assertRaises(ValueError):
            e.solar_sizing(self.base_settings, {'battery_step_kwh': True})

        # Huge candidate-count rejection (> 50 steps from integer modules)
        with self.assertRaises(ValueError) as ctx:
            e.solar_sizing({**self.base_settings, 'battery_module_kwh': 0.4, 'battery_modules': 1, 'battery_kwh': 0.4}, {'battery_min_kwh': 0, 'battery_max_kwh': 50})
        self.assertIn('too many candidate capacities', str(ctx.exception))

        # Joint positive-storage search with zero/invalid module capacity raises actionable error directing to left menu
        with self.assertRaises(ValueError) as ctx:
            e.solar_sizing({**self.base_settings, 'battery_module_kwh': 0.0, 'battery_modules': 0, 'battery_kwh': 0.0}, {
                'battery_mode': 'joint',
                'battery_max_kwh': 10.0
            })
        self.assertIn('left menu', str(ctx.exception))

        # 0.4 kWh module within capacity bounds (10 steps <= 50) succeeds without legacy 0.5-25 restriction
        res_04 = e.solar_sizing({**self.base_settings, 'battery_module_kwh': 0.4, 'battery_modules': 5, 'battery_kwh': 2.0}, {
            'battery_mode': 'joint',
            'battery_min_kwh': 0,
            'battery_max_kwh': 4.0,
            'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        })
        self.assertEqual(res_04['search_summary']['battery_modules_evaluated'], list(range(11)))
        self.assertIn(0.4, res_04['search_summary']['battery_capacities_evaluated'])
        self.assertIn(4.0, res_04['search_summary']['battery_capacities_evaluated'])

        # >25 kWh module within bounds (e.g. 28.0 kWh) succeeds without legacy step restriction
        res_28 = e.solar_sizing({**self.base_settings, 'battery_module_kwh': 28.0, 'battery_modules': 1, 'battery_kwh': 28.0}, {
            'battery_mode': 'joint',
            'battery_min_kwh': 0,
            'battery_max_kwh': 30.0,
            'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        })
        self.assertEqual(res_28['search_summary']['battery_modules_evaluated'], [0, 1])
        self.assertEqual(res_28['search_summary']['battery_capacities_evaluated'], [0.0, 28.0])

        # Zero capacity joint search (battery_max_kwh = 0) succeeds and preserves 0 without inventing module capacity
        res_zero = e.solar_sizing({**self.base_settings, 'battery_module_kwh': 0.0, 'battery_modules': 0, 'battery_kwh': 0.0}, {
            'battery_mode': 'joint',
            'battery_min_kwh': 0,
            'battery_max_kwh': 0,
            'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        })
        self.assertEqual(res_zero['search_summary']['battery_module_kwh'], 0.0)
        self.assertEqual(res_zero['search_summary']['battery_capacities_evaluated'], [0.0])

        # Fixed storage mode with module=0 and battery=0 preserves current values without step validation
        res_fixed_zero = e.solar_sizing({**self.base_settings, 'battery_module_kwh': 0.0, 'battery_modules': 0, 'battery_kwh': 0.0}, {
            'battery_mode': 'fixed',
            'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        })
        self.assertEqual(res_fixed_zero['search_summary']['battery_module_kwh'], 0.0)
        self.assertEqual(res_fixed_zero['search_summary']['battery_capacities_evaluated'], [0.0])

        # Direct API zero-step form is supported in fixed mode and joint zero-only mode
        res_fixed_zero_step = e.solar_sizing({'south_panels': 1, 'battery_modules': 0, 'battery_kwh': 0, 'battery_module_kwh': 0}, {
            'battery_mode': 'fixed',
            'battery_max_kwh': 0,
            'battery_step_kwh': 0,
            'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        })
        self.assertIsNotNone(res_fixed_zero_step['recommended_candidate'])
        self.assertEqual(res_fixed_zero_step['recommended_candidate']['battery_kwh'], 0.0)

        res_joint_zero_step = e.solar_sizing({'south_panels': 1, 'battery_modules': 0, 'battery_kwh': 0, 'battery_module_kwh': 0}, {
            'battery_mode': 'joint',
            'battery_max_kwh': 0,
            'battery_step_kwh': 0,
            'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        })
        self.assertIsNotNone(res_joint_zero_step['recommended_candidate'])
        self.assertEqual(res_joint_zero_step['recommended_candidate']['battery_kwh'], 0.0)

        # Fixed mode with positive battery also ignores irrelevant zero step
        res_fixed_pos_zero = e.solar_sizing({**self.base_settings, 'battery_kwh': 5.0, 'battery_modules': 1, 'battery_module_kwh': 5.0}, {
            'battery_mode': 'fixed',
            'battery_step_kwh': 0,
            'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        })
        self.assertIsNotNone(res_fixed_pos_zero['recommended_candidate'])
        self.assertEqual(res_fixed_pos_zero['recommended_candidate']['battery_kwh'], 5.0)

        # Invalid types and negative step in fixed / zero-only mode remain rejected
        with self.assertRaises(ValueError):
            e.solar_sizing({'south_panels': 1, 'battery_modules': 0, 'battery_kwh': 0, 'battery_module_kwh': 0}, {
                'battery_mode': 'fixed',
                'battery_step_kwh': -1
            })
        with self.assertRaises(ValueError):
            e.solar_sizing({'south_panels': 1, 'battery_modules': 0, 'battery_kwh': 0, 'battery_module_kwh': 0}, {
                'battery_mode': 'fixed',
                'battery_step_kwh': True
            })
        with self.assertRaises(ValueError):
            e.solar_sizing({'south_panels': 1, 'battery_modules': 0, 'battery_kwh': 0, 'battery_module_kwh': 0}, {
                'battery_mode': 'joint',
                'battery_max_kwh': 0,
                'battery_step_kwh': -1
            })
        with self.assertRaises(ValueError):
            e.solar_sizing({'south_panels': 1, 'battery_modules': 0, 'battery_kwh': 0, 'battery_module_kwh': 0}, {
                'battery_mode': 'joint',
                'battery_max_kwh': 0,
                'battery_step_kwh': True
            })

        # 2.35 kWh module with [2.0, 8.0] evaluates whole module candidates within bounds
        res = e.solar_sizing({**self.base_settings, 'battery_module_kwh': 2.35, 'battery_modules': 4, 'battery_kwh': 9.4}, {
            'battery_mode': 'joint',
            'battery_min_kwh': 2.0,
            'battery_max_kwh': 8.0,
            'panel_maxima': {'south': 2, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        })
        grid = res['search_summary']['battery_capacities_evaluated']
        self.assertNotIn(0.0, grid)
        self.assertIn(2.35, grid)
        self.assertIn(4.7, grid)
        self.assertIn(3 * 2.35, grid)
        self.assertNotIn(2.0, grid)
        self.assertNotIn(8.0, grid)
        self.assertEqual(res['search_summary']['battery_modules_evaluated'], [1, 2, 3])

    def test_exact_module_products_and_evaluate_parity(self):
        """Preserves exact module products for 2.35001 kWh modules without premature rounding; independent evaluate parity."""
        mod_kwh = 2.35001
        res = e.solar_sizing({
            **self.base_settings,
            'battery_module_kwh': mod_kwh,
            'battery_modules': 4,
            'battery_kwh': 4 * mod_kwh
        }, {
            'battery_mode': 'joint',
            'battery_min_kwh': 0,
            'battery_max_kwh': 10.0,
            'panel_maxima': {'south': 5, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        })
        self.assertEqual(res['search_summary']['battery_module_kwh'], mod_kwh)
        self.assertIn(mod_kwh * 4, res['search_summary']['battery_capacities_evaluated'])
        # Verify every candidate derives exact battery_kwh product matching modules * module_kwh
        for pt in res['size_curve']:
            m = pt['battery_modules']
            b = pt['battery_kwh']
            self.assertEqual(b, 0.0 if m == 0 else m * mod_kwh, "battery_kwh must be exact product of modules * module_kwh")
            self.assertEqual(pt['battery_module_kwh'], mod_kwh)

        rec = res['recommended_candidate']
        self.assertEqual(rec['battery_kwh'], 0.0 if rec['battery_modules'] == 0 else rec['battery_modules'] * mod_kwh)
        # Parity with independent evaluate
        eval_cand = e.evaluate(rec['settings'], detail=False)
        self.assertAlmostEqual(rec['capex_eur'], eval_cand['finance']['capex_eur'], places=2)
        self.assertAlmostEqual(rec['npv_median'], eval_cand['finance']['pv_incremental']['npv']['median'], places=2)
        # Shared validate preserves exact module capacity from settings
        val_s = e.validate(rec['settings'])
        self.assertEqual(val_s['battery_module_kwh'], mod_kwh)

    def test_roof_ranking_uses_actual_production_assumptions_with_proxy(self):
        """Input with dormer_south_proxy=True ranks roofs using actual candidate assumptions (proxy=False) and coherent yields."""
        proxy_settings = {
            **self.base_settings,
            'dormer_south_proxy': True,
            'south_panels': 10,
            'dormer_panels': 4,
            'west_panels': 0,
            'east_panels': 0,
            'flat_panels': 0
        }
        res = e.solar_sizing(proxy_settings, {
            'panel_maxima': {'south': 5, 'west': 5, 'east': 5, 'dormer': 5, 'flat': 5, 'secondary_south': 5},
            'battery_mode': 'fixed'
        })
        prio = res['search_summary']['roof_yield_priority']
        prod = res['search_summary']['roof_production_kwh_kwp']
        # Dormer must reflect shaded dormer yield (~816 kWh/kWp), NOT south (~1382 kWh/kWp)
        self.assertLess(prod['dormer'], prod['west'], "Dormer yield must be lower than West under shading")
        self.assertEqual(prio, sorted(prio, key=lambda r: -prod[r]))
        self.assertLess(prod['dormer'], prod['south'])
        self.assertEqual(prio[0], 'south')
        # Recommended candidate settings have dormer_south_proxy=False
        self.assertFalse(res['recommended_candidate']['settings']['dormer_south_proxy'])

    def test_inverter_ceiling_9kw_465w(self):
        """Array sizing enforces inverter kW ceiling: 9 kW with 465 W panels limits array to <= 19 panels (8.835 kWp)."""
        settings_9kw = {
            **self.base_settings,
            'inverter_kw': 9.0,
            'panel_watts': 465,
            'south_panels': 30,  # Currently oversized at 13.95 kWp
            'battery_module_kwh': 2.35,
            'battery_modules': 4,
            'battery_kwh': 9.4
        }
        res = e.solar_sizing(settings_9kw, {
            'panel_maxima': {'south': 30, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_mode': 'joint',
            'battery_min_kwh': 0.0,
            'battery_max_kwh': 15.0,
            'battery_step_kwh': 2.35
        })
        summary = res['search_summary']
        self.assertTrue(summary['inverter_ceiling_enforced'])
        self.assertEqual(summary['inverter_panel_ceiling'], 19)
        self.assertAlmostEqual(summary['inverter_kwp_ceiling'], 8.835, places=3)
        self.assertEqual(summary['effective_roof_limits']['south'], 19)

        rec = res['recommended_candidate']
        self.assertIsNotNone(rec)
        self.assertLessEqual(rec['panel_count'], 19)
        self.assertLessEqual(rec['kwp'], 9.0 + 1e-6)
        self.assertNotEqual(rec['panel_count'], 30, "30 panels (13.95 kWp) must not be recommended when inverter is 9 kW")

        # Verify all candidates in size curve and tradeoff matrix observe the ceiling
        for c in res['size_curve']:
            self.assertLessEqual(c['panel_count'], 19)
            self.assertLessEqual(c['kwp'], 9.0 + 1e-6)

        for cell in res['tradeoff_matrix']['cells']:
            self.assertLessEqual(cell['panel_count'], 19)
            self.assertLessEqual(cell['pv_kwp'], 9.0 + 1e-6)

        # Current layout (30 panels = 13.95 kWp) is preserved for honest comparison, but flagged as exceeding inverter ceiling
        comps = {c['id']: c for c in res['comparisons']}
        self.assertIn('current', comps)
        curr_c = comps['current']
        self.assertEqual(curr_c['panel_count'], 30)
        self.assertAlmostEqual(curr_c['kwp'], 13.95, places=2)
        self.assertTrue(curr_c['exceeds_inverter_ceiling'])
        self.assertIn('Exceeds selected PV array ceiling (9.00 kWp)', curr_c['inverter_ceiling_note'])

    def test_different_inverter_limits(self):
        """Different inverter capacities strictly bound maximum integer panel counts."""
        # Case A: 4 kW inverter with 465 W panels -> max 8 panels (3.72 kWp)
        res_4kw = e.solar_sizing({**self.base_settings, 'inverter_kw': 4.0, 'panel_watts': 465}, {
            'panel_maxima': {'south': 15, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_mode': 'fixed'
        })
        self.assertEqual(res_4kw['search_summary']['inverter_panel_ceiling'], 8)
        self.assertLessEqual(res_4kw['recommended_candidate']['panel_count'], 8)
        self.assertLessEqual(max(c['panel_count'] for c in res_4kw['size_curve']), 8)

        # Case B: 6 kW inverter with 400 W panels -> max 15 panels (6.00 kWp)
        res_6kw = e.solar_sizing({**self.base_settings, 'inverter_kw': 6.0, 'panel_watts': 400}, {
            'panel_maxima': {'south': 20, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_mode': 'fixed'
        })
        self.assertEqual(res_6kw['search_summary']['inverter_panel_ceiling'], 15)
        self.assertLessEqual(res_6kw['recommended_candidate']['panel_count'], 15)
        self.assertLessEqual(max(c['panel_count'] for c in res_6kw['size_curve']), 15)

    def test_joint_mode_whole_2_35_modules_and_left_menu_sync(self):
        """Joint sizing uses whole integer modules matching battery_module_kwh; ignores stale step option."""
        settings = {
            **self.base_settings,
            'battery_module_kwh': 2.35,
            'battery_modules': 4,
            'battery_kwh': 9.4
        }
        # Pass a stale battery_step_kwh (e.g. 5.0) in options - engine must synchronize to 2.35 kWh
        res = e.solar_sizing(settings, {
            'battery_mode': 'joint',
            'battery_min_kwh': 0.0,
            'battery_max_kwh': 10.0,
            'battery_step_kwh': 5.0,
            'panel_maxima': {'south': 3, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        })
        summary = res['search_summary']
        self.assertEqual(summary['battery_module_kwh'], 2.35)
        self.assertEqual(summary['battery_step_kwh'], 2.35)
        # Evaluated modules: [0, 1, 2, 3, 4] -> exact products 0, 2.35, 4.70, 7.05, 9.40
        self.assertEqual(summary['battery_modules_evaluated'], [0, 1, 2, 3, 4])
        self.assertEqual(summary['battery_capacities_evaluated'], [m * 2.35 for m in range(5)])

        rec = res['recommended_candidate']
        self.assertEqual(rec['battery_module_kwh'], 2.35)
        self.assertIsInstance(rec['battery_modules'], int)
        self.assertEqual(rec['battery_kwh'], rec['battery_modules'] * 2.35)
        self.assertEqual(rec['settings']['battery_module_kwh'], 2.35)
        self.assertEqual(rec['settings']['battery_modules'], rec['battery_modules'])

    def test_dynamic_roof_priority_production_basis(self):
        """Roof priority is established by actual modeled annual generation per kWp, adapting to shading."""
        # Under standard shading (shade_strength = 1.0)
        res_shaded = e.solar_sizing(self.base_settings, {
            'panel_maxima': {'south': 5, 'west': 5, 'east': 5, 'dormer': 5, 'flat': 5, 'secondary_south': 5},
            'battery_mode': 'fixed'
        })
        prio_shaded = res_shaded['search_summary']['roof_yield_priority']
        prod_shaded = res_shaded['search_summary']['roof_production_kwh_kwp']
        # East produces more than West under modeled PVGIS data
        self.assertGreater(prod_shaded['west'], prod_shaded['east'])
        self.assertLess(prio_shaded.index('west'), prio_shaded.index('east'))
        # South is top tier, Dormer is lowest due to shading
        self.assertEqual(prio_shaded[0], 'south')
        self.assertEqual(prio_shaded, sorted(prio_shaded, key=lambda r: -prod_shaded[r]))

        # Under unshaded conditions (shade_strength = 0.0), Dormer has high tilt & southern orientation
        res_unshaded = e.solar_sizing({**self.base_settings, 'shade_strength': 0.0}, {
            'panel_maxima': {'south': 5, 'west': 5, 'east': 5, 'dormer': 5, 'flat': 5, 'secondary_south': 5},
            'battery_mode': 'fixed'
        })
        prio_unshaded = res_unshaded['search_summary']['roof_yield_priority']
        prod_unshaded = res_unshaded['search_summary']['roof_production_kwh_kwp']
        # When unshaded, Dormer produces more than South (~1451 vs ~1382 kWh/kWp)
        self.assertGreater(prod_unshaded['dormer'], prod_shaded['dormer'])
        self.assertEqual(prio_unshaded, sorted(prio_unshaded, key=lambda r: -prod_unshaded[r]))

    def test_exact_and_sampled_respect_inverter_ceiling_and_modules(self):
        """Both exhaustive enumeration and bounded sampling enforce inverter ceiling and whole modules."""
        # Exact mode (space <= 200)
        res_exact = e.solar_sizing({**self.base_settings, 'inverter_kw': 3.0, 'panel_watts': 440, 'battery_module_kwh': 2.5}, {
            'panel_maxima': {'south': 40, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_kwh': 0,
            'battery_max_kwh': 5,
            'battery_step_kwh': 2.5
        })
        self.assertTrue(res_exact['search_summary']['exact'])
        self.assertEqual(res_exact['search_summary']['inverter_panel_ceiling'], 6)
        self.assertLessEqual(res_exact['recommended_candidate']['panel_count'], 6)
        for c in res_exact['size_curve']:
            self.assertLessEqual(c['panel_count'], 6)
            self.assertIn(c['battery_modules'], [0, 1, 2])

        # Sampled mode (space > 200)
        res_sampled = e.solar_sizing({
            **self.base_settings,
            'inverter_kw': 5.0,
            'panel_watts': 440,
            'battery_module_kwh': 2.35,
            'battery_modules': 4,
            'battery_kwh': 9.4
        }, {
            'panel_maxima': {'south': 15, 'west': 8, 'east': 6, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_kwh': 0,
            'battery_max_kwh': 15,
            'battery_step_kwh': 2.35,
            'evaluation_budget': 100
        })
        self.assertFalse(res_sampled['search_summary']['exact'])
        self.assertEqual(res_sampled['search_summary']['inverter_panel_ceiling'], 11)  # 5000/440 = 11.36 -> 11
        self.assertLessEqual(res_sampled['recommended_candidate']['panel_count'], 11)
        for c in res_sampled['size_curve']:
            self.assertLessEqual(c['panel_count'], 11)
            self.assertAlmostEqual(c['battery_module_kwh'], 2.35, places=3)

    def test_current_quoted_vs_modeled_comparison_and_cost_source(self):
        """Preserves quoted current comparison when quote_total is present, and labels cost source accurately."""
        quote = {
            'quote_total': 16000,
            'installer': 'Contractor Z',
            'quote_10kwh_eur': 8000,
            'quote_15kwh_eur': 11000
        }
        res = e.solar_sizing(self.base_settings, {
            'deduction_rate': 0.36,
            'panel_maxima': {'south': 3, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        }, quote=quote)
        comps = {c['id']: c for c in res['comparisons']}
        self.assertIn('current_quoted', comps)
        self.assertIn('current', comps)
        self.assertEqual(comps['current_quoted']['capex_eur'], 16000)
        self.assertEqual(comps['current_quoted']['deduction_rate'], self.base_settings['deduction_rate'])
        self.assertEqual(comps['current']['deduction_rate'], 0.36)
        self.assertIn('quote_variants', res['search_summary']['storage_cost_source'])

    def test_parent_reproduction_canonical_module_and_positive_apply_preservation(self):
        """Parent reproduction: module_kwh is taken from canonical user settings (2.35), not quote capacity (2.4)."""
        settings = {
            'south_panels': 2,
            'west_panels': 0,
            'east_panels': 0,
            'dormer_panels': 0,
            'flat_panels': 0,
            'secondary_south_panels': 0,
            'battery_modules': 4,
            'battery_module_kwh': 2.35,
            'battery_kwh': 9.4,
            'horizon_years': 1,
            'panel_watts': 440,
            'pv_fixed_cost': 1500,
            'pv_cost_kwp': 900,
            'battery_cost_kwh': 550,
            'deduction_rate': 0.5,
            'inverter_kw': 6.0,
            'charge_kw': 2.5,
            'discharge_kw': 2.5,
            'roundtrip_efficiency': 0.9,
            'load_scenario': 'electrified'
        }
        options = {
            'battery_mode': 'joint',
            'battery_min_kwh': 0,
            'battery_max_kwh': 10,
            'panel_maxima': {r: (2 if r == 'south' else 0) for r in e.OPTIMIZE_ROOFS}
        }
        quote = {'battery_nameplate_kwh': 12, 'battery_dod': 0.8}

        res = e.solar_sizing(settings, options, quote)
        summary = res['search_summary']

        self.assertEqual(summary['battery_module_kwh'], 2.35)
        self.assertEqual(summary['battery_step_kwh'], 2.35)
        self.assertEqual(summary['battery_modules_evaluated'], [0, 1, 2, 3, 4])
        expected_caps = [0.0 if m == 0 else m * 2.35 for m in [0, 1, 2, 3, 4]]
        self.assertEqual(summary['battery_capacities_evaluated'], expected_caps)
        self.assertEqual(res['recommended_candidate']['battery_module_kwh'], 2.35)
        self.assertEqual(res['recommended_candidate']['settings']['battery_module_kwh'], 2.35)

        # Force positive storage winner via autarky objective to verify positive winner settings and Apply
        res_pos = e.solar_sizing(settings, {**options, 'panel_maxima': {r: (10 if r == 'south' else 0) for r in e.OPTIMIZE_ROOFS}, 'objective': 'autarky'}, quote)
        rec = res_pos['recommended_candidate']
        self.assertGreater(rec['battery_modules'], 0)
        self.assertEqual(rec['battery_module_kwh'], 2.35)
        self.assertEqual(rec['battery_kwh'], rec['battery_modules'] * 2.35)
        self.assertEqual(rec['settings']['battery_modules'], rec['battery_modules'])
        self.assertEqual(rec['settings']['battery_module_kwh'], 2.35)
        self.assertEqual(rec['settings']['battery_kwh'], rec['battery_kwh'])

        # Simulate Apply: clear conflicting quote capacity overrides and evaluate
        applied_settings = {**settings, **rec['settings']}
        applied_quote = {**quote, 'price_basis': 'modeled'}
        applied_quote.pop('battery_nameplate_kwh', None)
        applied_quote.pop('battery_dod', None)
        eval_res = e.quote_evaluate(applied_settings, applied_quote)['result']
        self.assertEqual(eval_res['settings']['battery_module_kwh'], 2.35)
        self.assertEqual(eval_res['settings']['battery_kwh'], rec['battery_kwh'])
        self.assertAlmostEqual(eval_res['finance']['capex_eur'], rec['capex_eur'], places=2)
        self.assertAlmostEqual(eval_res['metrics']['autarky']['median'], rec['autarky_median'], places=4)

    def test_conflicting_quote_preserves_quoted_and_modeled_comparisons(self):
        """Preserves quoted current comparison at quote 9.6 while joint search uses 2.35 and fixed uses quote 9.6."""
        settings = {
            'south_panels': 2,
            'west_panels': 0,
            'east_panels': 0,
            'dormer_panels': 0,
            'flat_panels': 0,
            'secondary_south_panels': 0,
            'battery_modules': 4,
            'battery_module_kwh': 2.35,
            'battery_kwh': 9.4,
            'horizon_years': 1,
            'panel_watts': 440,
            'pv_fixed_cost': 1500,
            'pv_cost_kwp': 900,
            'battery_cost_kwh': 550,
            'deduction_rate': 0.5,
            'inverter_kw': 6.0,
            'load_scenario': 'electrified'
        }
        quote = {
            'battery_nameplate_kwh': 12,
            'battery_dod': 0.8,
            'line_items': [{'description': 'System', 'net_eur': 5000, 'vat': 0.1}]
        }

        # Joint mode: quoted comparison is 9.6, modeled current is 9.4, search uses 2.35
        options_joint = {
            'battery_mode': 'joint',
            'battery_min_kwh': 0,
            'battery_max_kwh': 10,
            'panel_maxima': {r: (2 if r == 'south' else 0) for r in e.OPTIMIZE_ROOFS}
        }
        res_joint = e.solar_sizing(settings, options_joint, quote)
        comps_joint = {c['id']: c for c in res_joint['comparisons']}
        self.assertIn('current_quoted', comps_joint)
        self.assertIn('current', comps_joint)
        self.assertAlmostEqual(comps_joint['current_quoted']['battery_kwh'], 9.6, places=3)
        self.assertAlmostEqual(comps_joint['current']['battery_kwh'], 9.4, places=3)
        self.assertEqual(res_joint['search_summary']['battery_module_kwh'], 2.35)
        self.assertEqual(res_joint['search_summary']['battery_capacities_evaluated'], [0.0 if m == 0 else m * 2.35 for m in [0, 1, 2, 3, 4]])

        # Fixed mode: retains quote-normalized battery 9.6
        options_fixed = {
            'battery_mode': 'fixed',
            'panel_maxima': {r: (2 if r == 'south' else 0) for r in e.OPTIMIZE_ROOFS}
        }
        res_fixed = e.solar_sizing(settings, options_fixed, quote)
        comps_fixed = {c['id']: c for c in res_fixed['comparisons']}
        self.assertAlmostEqual(comps_fixed['current_quoted']['battery_kwh'], 9.6, places=3)
        self.assertAlmostEqual(comps_fixed['current']['battery_kwh'], 9.6, places=3)
        self.assertAlmostEqual(res_fixed['search_summary']['battery_capacities_evaluated'][0], 9.6, places=3)
        self.assertAlmostEqual(res_fixed['recommended_candidate']['battery_kwh'], 9.6, places=3)

    def test_quote_positive_capacity_cannot_mask_zero_module_in_joint_search(self):
        """Quote positive capacity cannot bypass left-menu module 0 validation in joint positive search."""
        settings_zero = {
            'south_panels': 2,
            'west_panels': 0,
            'east_panels': 0,
            'dormer_panels': 0,
            'flat_panels': 0,
            'secondary_south_panels': 0,
            'battery_modules': 0,
            'battery_module_kwh': 0.0,
            'battery_kwh': 0.0,
            'horizon_years': 1,
            'inverter_kw': 6.0
        }
        quote_pos = {'battery_nameplate_kwh': 12, 'battery_dod': 0.8}
        options_pos = {
            'battery_mode': 'joint',
            'battery_min_kwh': 0,
            'battery_max_kwh': 10,
            'panel_maxima': {r: (2 if r == 'south' else 0) for r in e.OPTIMIZE_ROOFS}
        }

        # Joint positive search must fail even though quote provides positive battery
        with self.assertRaises(ValueError) as ctx:
            e.solar_sizing(settings_zero, options_pos, quote_pos)
        self.assertIn('positive battery module capacity', str(ctx.exception))

        # Malformed quotes must still be rejected
        with self.assertRaises(ValueError):
            e.solar_sizing(settings_zero, options_pos, {'battery_nameplate_kwh': 12, 'battery_dod': 1.5})
        with self.assertRaises(ValueError):
            e.solar_sizing(settings_zero, options_pos, {'line_items': [{'net_eur': -50}]})

        # Zero-only joint search succeeds
        options_zero = {
            'battery_mode': 'joint',
            'battery_min_kwh': 0,
            'battery_max_kwh': 0,
            'panel_maxima': {r: (2 if r == 'south' else 0) for r in e.OPTIMIZE_ROOFS}
        }
        res_zero = e.solar_sizing(settings_zero, options_zero, quote_pos)
        self.assertEqual(res_zero['recommended_candidate']['battery_kwh'], 0.0)

    def test_high_precision_module_stale_step_and_apply_metric_parity(self):
        """Preserves high precision module 2.35001 despite stale step option and conflicting quote; Apply parity."""
        settings_prec = {
            **self.base_settings,
            'battery_modules': 2,
            'battery_module_kwh': 2.35001,
            'battery_kwh': 2 * 2.35001
        }
        options_stale = {
            'objective': 'autarky',
            'battery_mode': 'joint',
            'battery_min_kwh': 0,
            'battery_max_kwh': 10,
            'battery_step_kwh': 2.35,  # stale step option
            'panel_maxima': {'south': 4, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        }
        quote_conflicting = {'battery_nameplate_kwh': 12, 'battery_dod': 0.8}

        res = e.solar_sizing(settings_prec, options_stale, quote_conflicting)
        summary = res['search_summary']
        rec = res['recommended_candidate']

        self.assertEqual(summary['battery_module_kwh'], 2.35001)
        self.assertEqual(summary['battery_step_kwh'], 2.35001)
        self.assertEqual(summary['battery_modules_evaluated'], [0, 1, 2, 3, 4])
        expected_caps = [0.0 if m == 0 else m * 2.35001 for m in [0, 1, 2, 3, 4]]
        self.assertEqual(summary['battery_capacities_evaluated'], expected_caps)
        self.assertEqual(rec['battery_module_kwh'], 2.35001)
        self.assertEqual(rec['settings']['battery_module_kwh'], 2.35001)
        self.assertEqual(rec['battery_kwh'], rec['battery_modules'] * 2.35001)

        # Apply parity
        applied_settings = {**settings_prec, **rec['settings']}
        applied_quote = {**quote_conflicting, 'price_basis': 'modeled'}
        applied_quote.pop('battery_nameplate_kwh', None)
        applied_quote.pop('battery_dod', None)
        eval_res = e.quote_evaluate(applied_settings, applied_quote)['result']
        self.assertEqual(eval_res['settings']['battery_module_kwh'], 2.35001)
        self.assertEqual(eval_res['settings']['battery_kwh'], rec['battery_kwh'])
        self.assertAlmostEqual(eval_res['finance']['capex_eur'], rec['capex_eur'], places=2)
        self.assertAlmostEqual(eval_res['metrics']['autarky']['median'], rec['autarky_median'], places=4)

    def test_positive_min_range_fractional_module_exhaustive_search(self):
        """Synthetic 2.76 kWh module with range 8..20 kWh generates precisely 3..7 modules, never 0."""
        settings = {
            **self.base_settings,
            'battery_module_kwh': 2.76,
            'battery_modules': 3,
            'battery_kwh': 8.28
        }
        res = e.solar_sizing(settings, {
            'battery_mode': 'joint',
            'battery_min_kwh': 8.0,
            'battery_max_kwh': 20.0,
            'panel_maxima': {'south': 3, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        })
        summary = res['search_summary']
        self.assertTrue(summary['exact'])
        self.assertEqual(summary['battery_modules_evaluated'], [3, 4, 5, 6, 7])
        expected_caps = [round(m * 2.76, 3) for m in [3, 4, 5, 6, 7]]
        self.assertEqual([round(c, 3) for c in summary['battery_capacities_evaluated']], expected_caps)
        self.assertNotIn(0.0, summary['battery_capacities_evaluated'])

        rec = res['recommended_candidate']
        self.assertIn(rec['battery_modules'], [3, 4, 5, 6, 7])
        self.assertAlmostEqual(rec['battery_kwh'], rec['battery_modules'] * 2.76, places=3)
        self.assertGreaterEqual(rec['battery_kwh'], 8.0)
        self.assertLessEqual(rec['battery_kwh'], 20.0)

        # Size curve entries must all have positive in-bounds storage and panels
        for c in res['size_curve']:
            self.assertGreater(c['panel_count'], 0)
            self.assertIn(c['battery_modules'], [3, 4, 5, 6, 7])
            self.assertAlmostEqual(c['battery_kwh'], c['battery_modules'] * 2.76, places=3)
            self.assertGreaterEqual(c['battery_kwh'], 8.0)
            self.assertLessEqual(c['battery_kwh'], 20.0)

        # Tradeoff heatmap must have no 0 kWh storage entries
        self.assertEqual([round(b, 3) for b in res['tradeoff_matrix']['battery_axis_kwh']], expected_caps)
        for cell in res['tradeoff_matrix']['cells']:
            self.assertGreaterEqual(cell['battery_kwh'], 8.0)
            self.assertIn(cell['battery_modules'], [3, 4, 5, 6, 7])
            self.assertAlmostEqual(cell['battery_kwh'], cell['battery_modules'] * 2.76, places=3)

        # Comparisons retain clearly distinguished separate no-install baseline
        no_install = next((c for c in res['comparisons'] if c['id'] == 'no_install'), None)
        self.assertIsNotNone(no_install)
        self.assertEqual(no_install['panel_count'], 0)
        self.assertEqual(no_install['battery_kwh'], 0.0)
        self.assertEqual(no_install['capex_eur'], 0.0)
        self.assertEqual(no_install['npv_median'], 0.0)

    def test_positive_min_range_fractional_module_bounded_search(self):
        """Bounded non-exhaustive search strictly enforces positive storage bounds [8..20] with 2.76 kWh modules."""
        settings = {
            **self.base_settings,
            'battery_module_kwh': 2.76,
            'battery_modules': 3,
            'battery_kwh': 8.28
        }
        res = e.solar_sizing(settings, {
            'battery_mode': 'joint',
            'battery_min_kwh': 8.0,
            'battery_max_kwh': 20.0,
            'panel_maxima': {'south': 15, 'west': 8, 'east': 6, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'evaluation_budget': 100
        })
        summary = res['search_summary']
        self.assertFalse(summary['exact'])
        self.assertEqual(summary['battery_modules_evaluated'], [3, 4, 5, 6, 7])
        self.assertNotIn(0.0, summary['battery_capacities_evaluated'])

        rec = res['recommended_candidate']
        self.assertIn(rec['battery_modules'], [3, 4, 5, 6, 7])
        self.assertAlmostEqual(rec['battery_kwh'], rec['battery_modules'] * 2.76, places=3)
        self.assertGreaterEqual(rec['battery_kwh'], 8.0)

        for c in res['size_curve']:
            self.assertGreater(c['panel_count'], 0)
            self.assertGreaterEqual(c['battery_kwh'], 8.0)
            self.assertIn(c['battery_modules'], [3, 4, 5, 6, 7])

        for cell in res['tradeoff_matrix']['cells']:
            self.assertGreaterEqual(cell['battery_kwh'], 8.0)

    def test_empty_module_interval_raises_validation_error(self):
        """Positive minimum range with no whole module in interval raises actionable ValueError."""
        # Module size 5.0, range 6..9 kWh -> no integer multiple in range
        with self.assertRaises(ValueError) as ctx:
            e.solar_sizing({**self.base_settings, 'battery_module_kwh': 5.0}, {
                'battery_mode': 'joint',
                'battery_min_kwh': 6.0,
                'battery_max_kwh': 9.0
            })
        self.assertIn('No whole battery module capacities exist', str(ctx.exception))
        self.assertIn('5', str(ctx.exception))

        # Module size 2.76, range 4.0..4.5 kWh -> no integer multiple in range
        with self.assertRaises(ValueError) as ctx:
            e.solar_sizing({**self.base_settings, 'battery_module_kwh': 2.76}, {
                'battery_mode': 'joint',
                'battery_min_kwh': 4.0,
                'battery_max_kwh': 4.5
            })
        self.assertIn('No whole battery module capacities exist', str(ctx.exception))

    def test_no_pv_positive_storage_raises_validation_error(self):
        """Positive storage requested when no PV panels can be installed raises actionable ValueError."""
        with self.assertRaises(ValueError) as ctx:
            e.solar_sizing({**self.base_settings, 'battery_module_kwh': 2.76}, {
                'battery_mode': 'joint',
                'battery_min_kwh': 8.0,
                'battery_max_kwh': 20.0,
                'panel_maxima': {'south': 0, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
            })
        self.assertIn('Positive storage capacity cannot be installed without solar PV panels', str(ctx.exception))

    def test_fixed_fractional_capacity_unaffected_by_joint_bounds(self):
        """In fixed battery mode, exact fractional capacity is preserved regardless of joint bounds."""
        settings = {
            **self.base_settings,
            'battery_kwh': 3.75,
            'battery_modules': 1,
            'battery_module_kwh': 3.75
        }
        res = e.solar_sizing(settings, {
            'battery_mode': 'fixed',
            'battery_min_kwh': 10.0,
            'battery_max_kwh': 20.0,
            'panel_maxima': {'south': 3, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        })
        self.assertEqual(res['search_summary']['battery_mode'], 'fixed')
        self.assertEqual(res['search_summary']['battery_capacities_evaluated'], [3.75])
        rec = res['recommended_candidate']
        self.assertEqual(rec['battery_kwh'], 3.75)
        for c in res['size_curve']:
            if c['panel_count'] > 0:
                self.assertEqual(c['battery_kwh'], 3.75)

    def test_negative_npv_constrained_best_candidate_and_advisory(self):
        """When all constrained candidates have negative NPV, recommend best feasible candidate within bounds, not 0."""
        expensive_settings = {
            **self.base_settings,
            'pv_fixed_cost': 5000,
            'pv_cost_kwp': 5000,
            'battery_cost_kwh': 2000,
            'deduction_rate': 0.0,
            'battery_module_kwh': 2.76,
            'battery_modules': 3,
            'battery_kwh': 8.28
        }
        res = e.solar_sizing(expensive_settings, {
            'battery_mode': 'joint',
            'battery_min_kwh': 8.0,
            'battery_max_kwh': 20.0,
            'panel_maxima': {'south': 3, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        })
        rec = res['recommended_candidate']
        # Must recommend a feasible system within [8..20] kWh, NOT 0 kWh storage!
        self.assertGreaterEqual(rec['battery_kwh'], 8.0)
        self.assertIn(rec['battery_modules'], [3, 4, 5, 6, 7])
        self.assertGreater(rec['panel_count'], 0)
        self.assertLess(rec['npv_median'], 0.0)

        # Separate no-install baseline remains in comparisons with 0 NPV
        no_install = next((c for c in res['comparisons'] if c['id'] == 'no_install'), None)
        self.assertIsNotNone(no_install)
        self.assertEqual(no_install['panel_count'], 0)
        self.assertEqual(no_install['battery_kwh'], 0.0)
        self.assertEqual(no_install['npv_median'], 0.0)

        # Economic advisory is included
        self.assertLess(rec['npv_mean'], 0.0)
        self.assertIn('advisory_note', res['search_summary'])
        self.assertIn('negative mean project NPV', res['search_summary']['advisory_note'])
        self.assertIn('no-install baseline', rec['bounds_note'])

    def test_fixed_storage_existing_vs_purchased_capex_and_parity(self):
        """Fixed storage mode correctly distinguishes existing (€0 battery capex) from purchased storage."""
        synth = {
            **self.base_settings,
            'battery_kwh': 10.0,
            'battery_modules': 2,
            'battery_module_kwh': 5.0,
            'battery_cost_kwh': 550.0,
            'panel_watts': 400.0,
            'pv_fixed_cost': 1500.0,
            'pv_cost_kwp': 1000.0,
            'horizon_years': 1,
            'deduction_rate': 0.0,
            'quote_total': None
        }
        opts = {
            'battery_mode': 'fixed',
            'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        }
        # 1. Benchmark storage: existing vs purchased
        res_exist = e.solar_sizing(synth, {**opts, 'storage_cost_mode': 'existing'})
        res_purch = e.solar_sizing(synth, {**opts, 'storage_cost_mode': 'purchased'})
        rec_exist = res_exist['recommended_candidate']
        rec_purch = res_purch['recommended_candidate']

        self.assertEqual(rec_exist['capex_eur'], 1900.0)
        self.assertEqual(rec_exist['settings']['battery_cost_kwh'], 0.0)
        self.assertEqual(rec_purch['capex_eur'], 7400.0)
        self.assertEqual(rec_purch['settings']['battery_cost_kwh'], 550.0)

        # Full evaluate parity
        eval_exist = e.evaluate(rec_exist['settings'])
        eval_purch = e.evaluate(rec_purch['settings'])
        self.assertEqual(eval_exist['finance']['capex_eur'], 1900.0)
        self.assertEqual(eval_purch['finance']['capex_eur'], 7400.0)

        # 2. Quote variants: existing vs purchased
        quote_opts = {**opts, 'quote_10kwh_eur': 6000.0, 'quote_15kwh_eur': 8500.0}
        res_q_exist = e.solar_sizing(synth, {**quote_opts, 'storage_cost_mode': 'existing'})
        res_q_purch = e.solar_sizing(synth, {**quote_opts, 'storage_cost_mode': 'purchased'})
        rec_q_exist = res_q_exist['recommended_candidate']
        rec_q_purch = res_q_purch['recommended_candidate']

        self.assertEqual(rec_q_exist['capex_eur'], 1900.0)
        self.assertEqual(rec_q_exist['settings']['battery_cost_kwh'], 0.0)
        self.assertEqual(rec_q_purch['capex_eur'], 7900.0)
        self.assertEqual(rec_q_purch['settings']['battery_cost_kwh'], 600.0)

        eval_q_exist = e.evaluate(rec_q_exist['settings'])
        eval_q_purch = e.evaluate(rec_q_purch['settings'])
        self.assertEqual(eval_q_exist['finance']['capex_eur'], 1900.0)
        self.assertEqual(eval_q_purch['finance']['capex_eur'], 7900.0)

    def test_out_of_bounds_derived_quote_cost_raises_validation_error(self):
        """Materially over-limit quote-derived rates are not silently clamped and raise validation error."""
        synth = {
            **self.base_settings,
            'battery_kwh': 10.0,
            'battery_modules': 2,
            'battery_module_kwh': 5.0,
            'horizon_years': 1,
            'quote_total': None
        }
        opts = {
            'battery_mode': 'fixed',
            'storage_cost_mode': 'purchased',
            'quote_10kwh_eur': 25000.0,
            'quote_15kwh_eur': 40000.0,
            'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        }
        with self.assertRaises(ValueError) as ctx:
            e.solar_sizing(synth, opts)
        self.assertIn('battery_cost_kwh outside modeling bounds', str(ctx.exception))

    def test_non_npv_objective_negative_npv_winner_advisory(self):
        """Non-NPV objective whose winner has negative NPV emits factual advisory without universal claims."""
        expensive_settings = {
            **self.base_settings,
            'pv_fixed_cost': 5000,
            'pv_cost_kwp': 5000,
            'battery_cost_kwh': 1900,
            'deduction_rate': 0.0,
            'battery_module_kwh': 5.0,
            'battery_modules': 1,
            'battery_kwh': 5.0
        }
        res = e.solar_sizing(expensive_settings, {
            'battery_mode': 'joint',
            'objective': 'autarky',
            'battery_min_kwh': 5.0,
            'battery_max_kwh': 10.0,
            'panel_maxima': {'south': 3, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        })
        rec = res['recommended_candidate']
        self.assertLess(rec['npv_median'], 0.0)
        self.assertLess(rec['npv_mean'], 0.0)
        self.assertIn('advisory_note', res['search_summary'])
        self.assertIn('negative mean project NPV', res['search_summary']['advisory_note'])
        # Confirms wording does not make invalid universal claims like 'All evaluated'
        self.assertNotIn('All evaluated configurations', res['search_summary']['advisory_note'])
        self.assertNotIn('All feasible constrained configurations', res['search_summary']['advisory_note'])

    def test_fifty_positive_module_steps_allowed_with_zero_min(self):
        """Up to 50 positive module steps are permitted when minimum battery is 0."""
        s = {
            **self.base_settings,
            'battery_kwh': 0.5,
            'battery_modules': 1,
            'battery_module_kwh': 0.5
        }
        opts = {
            'battery_min_kwh': 0.0,
            'battery_max_kwh': 25.0,  # 25 / 0.5 = exactly 50 positive steps
            'battery_mode': 'joint',
            'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        }
        res = e.solar_sizing(s, opts)
        self.assertIsNotNone(res['recommended_candidate'])


class SolarSizingServerRouteTests(IsolatedDefaultsTestCase):
    def setUp(self):
        super().setUp()
        self.handler = server_module.LegacyLocalHandler
        self.valid_payload = {
            'settings': {
                'south_panels': 10,
                'west_panels': 0,
                'east_panels': 0,
                'dormer_panels': 0,
                'flat_panels': 0,
                'secondary_south_panels': 0,
                'battery_kwh': 5.0,
                'inverter_kw': 6.0,
                'load_scenario': 'electrified'
            },
            'options': {
                'panel_maxima': {'south': 3, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
                'battery_min_kwh': 0,
                'battery_max_kwh': 5,
                'battery_step_kwh': 5
            }
        }

    def _call_post_solar_sizing(self, payload):
        body_bytes = json.dumps(payload).encode('utf-8')
        handler = self.handler.__new__(self.handler)
        handler.rfile = io.BytesIO(body_bytes)
        handler.headers = {'Content-Length': str(len(body_bytes))}
        handler.path = '/api/solar-sizing'
        response_code = None
        response_headers = {}
        response_body = io.BytesIO()

        def send_response(code):
            nonlocal response_code
            response_code = code

        def send_header(k, v):
            response_headers[k] = v

        def end_headers():
            pass

        handler.send_response = send_response
        handler.send_header = send_header
        handler.end_headers = end_headers
        handler.wfile = response_body

        handler.do_POST()
        response_body.seek(0)
        return response_code, json.loads(response_body.read().decode('utf-8'))

    def test_post_solar_sizing_success(self):
        """Route returns status 200, valid JSON response with recommended candidate, size curve, and compute_seconds."""
        code, body = self._call_post_solar_sizing(self.valid_payload)
        self.assertEqual(code, 200)
        self.assertEqual(body['status'], 'SCENARIO_PENDING')
        self.assertIn('recommended_candidate', body)
        self.assertIn('size_curve', body)
        self.assertIn('tradeoff_matrix', body)
        self.assertIn('comparisons', body)
        self.assertIn('search_summary', body)
        self.assertIn('compute_seconds', body)
        self.assertIsInstance(body['compute_seconds'], (int, float))

    def test_post_solar_sizing_malformed_options_returns_400(self):
        """Route returns status 400 when invalid options are provided."""
        bad_payload = {**self.valid_payload, 'options': {'battery_min_kwh': 20, 'battery_max_kwh': 5}}
        code, body = self._call_post_solar_sizing(bad_payload)
        self.assertEqual(code, 400)
        self.assertIn('error', body)

    def test_post_solar_sizing_battery_step_non_positive_returns_400(self):
        """Route returns status 400 when battery_step_kwh is non-positive or invalid."""
        bad_payload = {**self.valid_payload, 'options': {'battery_step_kwh': 0}}
        code, body = self._call_post_solar_sizing(bad_payload)
        self.assertEqual(code, 400)
        self.assertIn('error', body)

    def test_post_solar_sizing_zero_module_positive_storage_returns_400(self):
        """Route returns status 400 when battery_module_kwh is 0 in joint positive-storage search."""
        bad_payload = {
            **self.valid_payload,
            'settings': {**self.valid_payload['settings'], 'battery_modules': 0, 'battery_kwh': 0.0, 'battery_module_kwh': 0.0},
            'options': {'battery_mode': 'joint', 'battery_max_kwh': 10}
        }
        code, body = self._call_post_solar_sizing(bad_payload)
        self.assertEqual(code, 400)
        self.assertIn('left menu', body['error'])

    def test_post_solar_sizing_fixed_and_joint_zero_only_with_zero_step_succeeds(self):
        """Route returns status 200 when battery_step_kwh is 0 for fixed-zero and joint-zero-only modes."""
        zero_settings = {
            **self.valid_payload['settings'],
            'battery_modules': 0,
            'battery_kwh': 0.0,
            'battery_module_kwh': 0.0
        }

        # Fixed-zero payload with battery_step_kwh=0
        fixed_payload = {
            'settings': zero_settings,
            'options': {
                'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
                'battery_mode': 'fixed',
                'battery_max_kwh': 0,
                'battery_step_kwh': 0
            }
        }
        code, body = self._call_post_solar_sizing(fixed_payload)
        self.assertEqual(code, 200)
        self.assertEqual(body['status'], 'SCENARIO_PENDING')
        self.assertEqual(body['recommended_candidate']['battery_kwh'], 0.0)

        # Joint-zero-only payload with battery_step_kwh=0
        joint_payload = {
            'settings': zero_settings,
            'options': {
                'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
                'battery_mode': 'joint',
                'battery_min_kwh': 0,
                'battery_max_kwh': 0,
                'battery_step_kwh': 0
            }
        }
        code, body = self._call_post_solar_sizing(joint_payload)
        self.assertEqual(code, 200)
        self.assertEqual(body['status'], 'SCENARIO_PENDING')
        self.assertEqual(body['recommended_candidate']['battery_kwh'], 0.0)

    def test_post_solar_sizing_positive_quote_cannot_mask_zero_module_returns_400(self):
        """Route returns status 400 when battery_module_kwh is 0 even if quote supplies positive battery."""
        bad_payload = {
            **self.valid_payload,
            'settings': {**self.valid_payload['settings'], 'battery_modules': 0, 'battery_kwh': 0.0, 'battery_module_kwh': 0.0},
            'options': {'battery_mode': 'joint', 'battery_max_kwh': 10},
            'quote': {'battery_nameplate_kwh': 12, 'battery_dod': 0.8}
        }
        code, body = self._call_post_solar_sizing(bad_payload)
        self.assertEqual(code, 400)
        self.assertIn('left menu', body['error'])

    def test_post_solar_sizing_empty_interval_and_no_pv_return_400(self):
        """API route /api/solar-sizing returns 400 for empty interval or positive storage with 0 PV."""
        empty_payload = {
            **self.valid_payload,
            'settings': {**self.valid_payload['settings'], 'battery_module_kwh': 5.0},
            'options': {'battery_mode': 'joint', 'battery_min_kwh': 6.0, 'battery_max_kwh': 9.0}
        }
        code, body = self._call_post_solar_sizing(empty_payload)
        self.assertEqual(code, 400)
        self.assertIn('No whole battery module capacities exist', body['error'])

        no_pv_payload = {
            **self.valid_payload,
            'settings': {**self.valid_payload['settings'], 'battery_module_kwh': 2.76},
            'options': {
                'battery_mode': 'joint',
                'battery_min_kwh': 8.0,
                'battery_max_kwh': 20.0,
                'panel_maxima': {'south': 0, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
            }
        }
        code, body = self._call_post_solar_sizing(no_pv_payload)
        self.assertEqual(code, 400)
        self.assertIn('Positive storage capacity cannot be installed without solar PV panels', body['error'])


class SolarSizingRecommendationChoiceTests(IsolatedDefaultsTestCase):
    def setUp(self):
        super().setUp()
        self.base_settings = {
            'pv_dispatch_model': 'physical',
            'south_panels': 10,
            'west_panels': 0,
            'east_panels': 0,
            'dormer_panels': 0,
            'flat_panels': 0,
            'secondary_south_panels': 0,
            'battery_kwh': 5.0,
            'battery_modules': 1,
            'battery_module_kwh': 5.0,
            'roundtrip_efficiency': 0.9,
            'inverter_kw': 6.0,
            'charge_kw': 5.0,
            'discharge_kw': 5.0,
            'horizon_years': 20,
            'panel_watts': 440,
            'pv_fixed_cost': 1500,
            'pv_cost_kwp': 900,
            'battery_cost_kwh': 550,
            'deduction_rate': 0.5,
            'load_scenario': 'electrified'
        }

    def test_synthetic_selection_user_example(self):
        """Reproduce user specification fixture:
        Financial winner: 10,000 NPV, 61.3% coverage, 0 kWh battery.
        Candidate B: 9,630 NPV (96.3% retained), 81.3% coverage, 8.28 kWh battery.
        Candidate C: 9,920 NPV (99.2% retained), 74.0% coverage, 5.52 kWh battery.
        - At 5% sacrifice, Candidate B (8.28 kWh) is selected.
        - At 1% sacrifice, Candidate C (5.52 kWh) is selected.
        - At 3.5% sacrifice, Candidate C (5.52 kWh) is selected.
        - Budget choice at 70% selects C (lowest capex); at 75.5% selects B; at 85% returns None.
        """
        c_fin = {
            'panel_count': 14, 'kwp': 6.16, 'roof_counts': {'south': 14, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_kwh': 0.0, 'battery_modules': 0, 'battery_module_kwh': 2.76, 'capex_eur': 7044.0,
            'npv': {'median': 10000.0, 'mean': 10000.0, 'min': 8000.0, 'max': 12000.0},
            'npv_median': 10000.0, 'npv_mean': 10000.0, 'npv_min': 8000.0, 'npv_max': 12000.0,
            'project_npv': {'median': 10000.0, 'mean': 10000.0, 'min': 8000.0, 'max': 12000.0},
            'project_npv_median': 10000.0, 'project_npv_mean': 10000.0, 'project_npv_min': 8000.0, 'project_npv_max': 12000.0,
            'equity_npv': {'median': 10000.0, 'mean': 10000.0, 'min': 8000.0, 'max': 12000.0},
            'equity_npv_median': 10000.0, 'equity_npv_mean': 10000.0, 'equity_npv_min': 8000.0, 'equity_npv_max': 12000.0,
            'autarky_median': 0.613, 'self_consumption_median': 0.85,
            'annual_pv_kwh_median': 8500.0, 'annual_grid_import_kwh_median': 2200.0, 'annual_grid_export_kwh_median': 4000.0,
            'discounted_payback_years': 6.2, 'simple_payback_years': 5.5,
            'settings': {'south_panels': 14, 'battery_kwh': 0.0, 'battery_modules': 0, 'battery_module_kwh': 2.76}
        }
        c_b = {
            'panel_count': 14, 'kwp': 6.16, 'roof_counts': {'south': 14, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_kwh': 8.28, 'battery_modules': 3, 'battery_module_kwh': 2.76, 'capex_eur': 11598.0,
            'npv': {'median': 9630.0, 'mean': 9630.0, 'min': 7600.0, 'max': 11700.0},
            'npv_median': 9630.0, 'npv_mean': 9630.0, 'npv_min': 7600.0, 'npv_max': 11700.0,
            'project_npv': {'median': 9630.0, 'mean': 9630.0, 'min': 7600.0, 'max': 11700.0},
            'project_npv_median': 9630.0, 'project_npv_mean': 9630.0, 'project_npv_min': 7600.0, 'project_npv_max': 11700.0,
            'equity_npv': {'median': 9630.0, 'mean': 9630.0, 'min': 7600.0, 'max': 11700.0},
            'equity_npv_median': 9630.0, 'equity_npv_mean': 9630.0, 'equity_npv_min': 7600.0, 'equity_npv_max': 11700.0,
            'autarky_median': 0.813, 'self_consumption_median': 0.92,
            'annual_pv_kwh_median': 8500.0, 'annual_grid_import_kwh_median': 1060.0, 'annual_grid_export_kwh_median': 1500.0,
            'discounted_payback_years': 7.8, 'simple_payback_years': 6.8,
            'settings': {'south_panels': 14, 'battery_kwh': 8.28, 'battery_modules': 3, 'battery_module_kwh': 2.76}
        }
        c_c = {
            'panel_count': 14, 'kwp': 6.16, 'roof_counts': {'south': 14, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_kwh': 5.52, 'battery_modules': 2, 'battery_module_kwh': 2.76, 'capex_eur': 10080.0,
            'npv': {'median': 9920.0, 'mean': 9920.0, 'min': 7900.0, 'max': 11950.0},
            'npv_median': 9920.0, 'npv_mean': 9920.0, 'npv_min': 7900.0, 'npv_max': 11950.0,
            'project_npv': {'median': 9920.0, 'mean': 9920.0, 'min': 7900.0, 'max': 11950.0},
            'project_npv_median': 9920.0, 'project_npv_mean': 9920.0, 'project_npv_min': 7900.0, 'project_npv_max': 11950.0,
            'equity_npv': {'median': 9920.0, 'mean': 9920.0, 'min': 7900.0, 'max': 11950.0},
            'equity_npv_median': 9920.0, 'equity_npv_mean': 9920.0, 'equity_npv_min': 7900.0, 'equity_npv_max': 11950.0,
            'autarky_median': 0.740, 'self_consumption_median': 0.89,
            'annual_pv_kwh_median': 8500.0, 'annual_grid_import_kwh_median': 1480.0, 'annual_grid_export_kwh_median': 2200.0,
            'discounted_payback_years': 7.1, 'simple_payback_years': 6.2,
            'settings': {'south_panels': 14, 'battery_kwh': 5.52, 'battery_modules': 2, 'battery_module_kwh': 2.76}
        }
        candidates = [c_fin, c_b, c_c]

        # 5% allowed sacrifice: Candidate B (8.28 kWh) selected
        choices_5, alts_5 = e.select_solar_recommendations(candidates, options={'npv_sacrifice_pct': 5.0, 'coverage_target_pct': 70.0})
        self.assertEqual(choices_5['financial']['battery_kwh'], 0.0)
        self.assertEqual(choices_5['balanced']['battery_kwh'], 8.28)
        self.assertAlmostEqual(choices_5['balanced']['retained_npv_pct'], 96.3, places=1)
        self.assertAlmostEqual(choices_5['balanced']['autarky_median'], 0.813, places=3)
        self.assertAlmostEqual(choices_5['balanced']['coverage_gain_pct'], 20.0, places=1)
        self.assertEqual(choices_5['budget']['battery_kwh'], 5.52)

        # 1% allowed sacrifice: Candidate C (5.52 kWh) selected
        choices_1, _ = e.select_solar_recommendations(candidates, options={'npv_sacrifice_pct': 1.0, 'coverage_target_pct': 70.0})
        self.assertEqual(choices_1['balanced']['battery_kwh'], 5.52)
        self.assertAlmostEqual(choices_1['balanced']['retained_npv_pct'], 99.2, places=1)
        self.assertAlmostEqual(choices_1['balanced']['autarky_median'], 0.740, places=3)

        # 3.5% custom sacrifice: Candidate C (5.52 kWh) selected (9650 threshold > 9630)
        choices_35, _ = e.select_solar_recommendations(candidates, options={'npv_sacrifice_pct': 3.5, 'coverage_target_pct': 70.0})
        self.assertEqual(choices_35['balanced']['battery_kwh'], 5.52)

        # Budget choice targets: 70%, 75.5%, 85%
        choices_bud_70, _ = e.select_solar_recommendations(candidates, options={'npv_sacrifice_pct': 5.0, 'coverage_target_pct': 70.0})
        self.assertEqual(choices_bud_70['budget']['battery_kwh'], 5.52)

        choices_bud_755, _ = e.select_solar_recommendations(candidates, options={'npv_sacrifice_pct': 5.0, 'coverage_target_pct': 75.5})
        self.assertEqual(choices_bud_755['budget']['battery_kwh'], 8.28)

        choices_bud_85, _ = e.select_solar_recommendations(candidates, options={'npv_sacrifice_pct': 5.0, 'coverage_target_pct': 85.0})
        self.assertIsNone(choices_bud_85['budget'])

    def test_synthetic_tie_breaking(self):
        """Tie-breaking rules:
        - Balanced: coverage > capex (lower) > smaller array > smaller battery.
        - Budget: capex (lower) > coverage (higher) > npv (higher) > smaller array.
        """
        cand1 = {
            'panel_count': 10, 'kwp': 4.4, 'roof_counts': {'south': 10, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_kwh': 5.0, 'battery_modules': 1, 'capex_eur': 8000.0,
            'npv': {'median': 10000.0, 'mean': 10000.0, 'min': 10000.0, 'max': 10000.0},
            'npv_median': 10000.0, 'npv_mean': 10000.0, 'npv_min': 10000.0, 'npv_max': 10000.0,
            'project_npv': {'median': 10000.0, 'mean': 10000.0, 'min': 10000.0, 'max': 10000.0},
            'project_npv_median': 10000.0, 'project_npv_mean': 10000.0, 'project_npv_min': 10000.0, 'project_npv_max': 10000.0,
            'equity_npv': {'median': 10000.0, 'mean': 10000.0, 'min': 10000.0, 'max': 10000.0},
            'equity_npv_median': 10000.0, 'equity_npv_mean': 10000.0, 'equity_npv_min': 10000.0, 'equity_npv_max': 10000.0,
            'autarky_median': 0.70,
            'settings': {'south_panels': 10, 'battery_kwh': 5.0}
        }
        cand2 = {
            'panel_count': 10, 'kwp': 4.4, 'roof_counts': {'south': 10, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_kwh': 5.0, 'battery_modules': 1, 'capex_eur': 7500.0,
            'npv': {'median': 9800.0, 'mean': 9800.0, 'min': 9800.0, 'max': 9800.0},
            'npv_median': 9800.0, 'npv_mean': 9800.0, 'npv_min': 9800.0, 'npv_max': 9800.0,
            'project_npv': {'median': 9800.0, 'mean': 9800.0, 'min': 9800.0, 'max': 9800.0},
            'project_npv_median': 9800.0, 'project_npv_mean': 9800.0, 'project_npv_min': 9800.0, 'project_npv_max': 9800.0,
            'equity_npv': {'median': 9800.0, 'mean': 9800.0, 'min': 9800.0, 'max': 9800.0},
            'equity_npv_median': 9800.0, 'equity_npv_mean': 9800.0, 'equity_npv_min': 9800.0, 'equity_npv_max': 9800.0,
            'autarky_median': 0.70,
            'settings': {'south_panels': 10, 'battery_kwh': 5.0}
        }
        # Equal coverage (0.70) within 5% threshold: cand2 has lower capex (7500 vs 8000), so cand2 wins balanced
        choices, _ = e.select_solar_recommendations([cand1, cand2], options={'npv_sacrifice_pct': 5.0, 'coverage_target_pct': 70.0})
        self.assertEqual(choices['balanced']['capex_eur'], 7500.0)

        # Budget tie: equal capex (7500), higher coverage wins
        cand3 = {
            'panel_count': 10, 'kwp': 4.4, 'roof_counts': {'south': 10, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_kwh': 5.0, 'battery_modules': 1, 'capex_eur': 7500.0,
            'npv': {'median': 9200.0, 'mean': 9200.0, 'min': 9200.0, 'max': 9200.0},
            'npv_median': 9200.0, 'npv_mean': 9200.0, 'npv_min': 9200.0, 'npv_max': 9200.0,
            'project_npv': {'median': 9200.0, 'mean': 9200.0, 'min': 9200.0, 'max': 9200.0},
            'project_npv_median': 9200.0, 'project_npv_mean': 9200.0, 'project_npv_min': 9200.0, 'project_npv_max': 9200.0,
            'equity_npv': {'median': 9200.0, 'mean': 9200.0, 'min': 9200.0, 'max': 9200.0},
            'equity_npv_median': 9200.0, 'equity_npv_mean': 9200.0, 'equity_npv_min': 9200.0, 'equity_npv_max': 9200.0,
            'autarky_median': 0.75,
            'settings': {'south_panels': 10, 'battery_kwh': 5.0}
        }
        choices_b, _ = e.select_solar_recommendations([cand2, cand3], options={'npv_sacrifice_pct': 5.0, 'coverage_target_pct': 70.0})
        self.assertEqual(choices_b['budget']['autarky_median'], 0.75)

    def test_nonpositive_npv_and_empty_candidates(self):
        """When NPV <= 0 or candidates empty, balanced choice is None and budget is None."""
        neg_cand = {
            'panel_count': 8, 'kwp': 3.52, 'roof_counts': {'south': 8, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_kwh': 5.0, 'battery_modules': 1, 'capex_eur': 6500.0,
            'npv': {'median': -450.0, 'mean': -450.0, 'min': -450.0, 'max': -450.0},
            'npv_median': -450.0, 'npv_mean': -450.0, 'npv_min': -450.0, 'npv_max': -450.0,
            'project_npv': {'median': -450.0, 'mean': -450.0, 'min': -450.0, 'max': -450.0},
            'project_npv_median': -450.0, 'project_npv_mean': -450.0, 'project_npv_min': -450.0, 'project_npv_max': -450.0,
            'equity_npv': {'median': -450.0, 'mean': -450.0, 'min': -450.0, 'max': -450.0},
            'equity_npv_median': -450.0, 'equity_npv_mean': -450.0, 'equity_npv_min': -450.0, 'equity_npv_max': -450.0,
            'autarky_median': 0.65,
            'settings': {'south_panels': 8, 'battery_kwh': 5.0}
        }
        choices, alts = e.select_solar_recommendations([neg_cand], options={'npv_sacrifice_pct': 5.0, 'coverage_target_pct': 60.0})
        self.assertIsNotNone(choices['financial'])
        self.assertFalse(choices['financial']['is_positive_npv'])
        self.assertIsNone(choices['balanced'])
        self.assertIsNone(choices['budget'])
        self.assertIsNone(choices['main_recommendation'])

        # Empty candidates: financial no-install baseline always available, not counted as valid installation
        empty_choices, empty_alts = e.select_solar_recommendations([], options={'npv_sacrifice_pct': 5.0})
        self.assertIsNotNone(empty_choices['financial'])
        self.assertTrue(empty_choices['financial']['is_no_install'])
        self.assertIsNone(empty_choices['balanced'])
        self.assertIsNone(empty_choices['budget'])
        self.assertIsNone(empty_choices['main_recommendation'])
        self.assertEqual(empty_alts, [])

    def test_top_alternatives_diversity_and_exports(self):
        """Top alternatives pool returns <= 5 deduplicated configurations with signed deltas and export metrics."""
        cand_fin = {
            'panel_count': 16, 'kwp': 7.04, 'roof_counts': {'south': 16, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_kwh': 5.0, 'battery_modules': 1, 'capex_eur': 10500.0,
            'npv': {'median': 12000.0, 'mean': 12000.0, 'min': 12000.0, 'max': 12000.0},
            'npv_median': 12000.0, 'npv_mean': 12000.0, 'npv_min': 12000.0, 'npv_max': 12000.0,
            'project_npv': {'median': 12000.0, 'mean': 12000.0, 'min': 12000.0, 'max': 12000.0},
            'project_npv_median': 12000.0, 'project_npv_mean': 12000.0, 'project_npv_min': 12000.0, 'project_npv_max': 12000.0,
            'equity_npv': {'median': 12000.0, 'mean': 12000.0, 'min': 12000.0, 'max': 12000.0},
            'equity_npv_median': 12000.0, 'equity_npv_mean': 12000.0, 'equity_npv_min': 12000.0, 'equity_npv_max': 12000.0,
            'autarky_median': 0.68,
            'annual_grid_import_kwh_median': 1800.0, 'annual_grid_export_kwh_median': 5000.0,
            'settings': {'south_panels': 16, 'battery_kwh': 5.0}
        }
        cand_smaller = {
            'panel_count': 8, 'kwp': 3.52, 'roof_counts': {'south': 8, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_kwh': 0.0, 'battery_modules': 0, 'capex_eur': 4668.0,
            'npv': {'median': 7000.0, 'mean': 7000.0, 'min': 7000.0, 'max': 7000.0},
            'npv_median': 7000.0, 'npv_mean': 7000.0, 'npv_min': 7000.0, 'npv_max': 7000.0,
            'project_npv': {'median': 7000.0, 'mean': 7000.0, 'min': 7000.0, 'max': 7000.0},
            'project_npv_median': 7000.0, 'project_npv_mean': 7000.0, 'project_npv_min': 7000.0, 'project_npv_max': 7000.0,
            'equity_npv': {'median': 7000.0, 'mean': 7000.0, 'min': 7000.0, 'max': 7000.0},
            'equity_npv_median': 7000.0, 'equity_npv_mean': 7000.0, 'equity_npv_min': 7000.0, 'equity_npv_max': 7000.0,
            'autarky_median': 0.45,
            'annual_grid_import_kwh_median': 3100.0, 'annual_grid_export_kwh_median': 1200.0,
            'settings': {'south_panels': 8, 'battery_kwh': 0.0}
        }
        cand_max_cov = {
            'panel_count': 20, 'kwp': 8.8, 'roof_counts': {'south': 20, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_kwh': 15.0, 'battery_modules': 3, 'capex_eur': 17500.0,
            'npv': {'median': 11500.0, 'mean': 11500.0, 'min': 11500.0, 'max': 11500.0},
            'npv_median': 11500.0, 'npv_mean': 11500.0, 'npv_min': 11500.0, 'npv_max': 11500.0,
            'project_npv': {'median': 11500.0, 'mean': 11500.0, 'min': 11500.0, 'max': 11500.0},
            'project_npv_median': 11500.0, 'project_npv_mean': 11500.0, 'project_npv_min': 11500.0, 'project_npv_max': 11500.0,
            'equity_npv': {'median': 11500.0, 'mean': 11500.0, 'min': 11500.0, 'max': 11500.0},
            'equity_npv_median': 11500.0, 'equity_npv_mean': 11500.0, 'equity_npv_min': 11500.0, 'equity_npv_max': 11500.0,
            'autarky_median': 0.88,
            'annual_grid_import_kwh_median': 700.0, 'annual_grid_export_kwh_median': 6200.0,
            'settings': {'south_panels': 20, 'battery_kwh': 15.0}
        }
        candidates = [cand_fin, cand_smaller, cand_max_cov]
        choices, alts = e.select_solar_recommendations(candidates, options={'npv_sacrifice_pct': 5.0, 'coverage_target_pct': 70.0})
        self.assertLessEqual(len(alts), 5)
        # Verify deduplication by signature
        sigs = [f"{a['panel_count']}_{a['battery_kwh']}" for a in alts]
        self.assertEqual(len(sigs), len(set(sigs)))
        # Verify smaller array included
        smaller_in_alts = [a for a in alts if a['panel_count'] < cand_fin['panel_count']]
        self.assertTrue(len(smaller_in_alts) > 0)
        # Verify signed extra capex vs financial choice
        fin_alt = [a for a in alts if a['panel_count'] == cand_fin['panel_count'] and a['battery_kwh'] == cand_fin['battery_kwh']][0]
        self.assertAlmostEqual(fin_alt['extra_capex_vs_financial'], 0.0)
        self.assertAlmostEqual(fin_alt['npv_diff_vs_financial'], 0.0)

        small_alt = [a for a in alts if a['panel_count'] == cand_smaller['panel_count']][0]
        self.assertLess(small_alt['extra_capex_vs_financial'], 0)  # saves capex
        self.assertLess(small_alt['npv_diff_vs_financial'], 0)
        self.assertIn('annual_grid_export_kwh_median', small_alt)

    def test_options_validation_rejects_invalid_sacrifice_and_coverage(self):
        """Backend validation strictly rejects non-finite, out-of-range, and boolean options."""
        # Sacrifice out of range
        with self.assertRaises(ValueError):
            e.solar_sizing(self.base_settings, {'npv_sacrifice_pct': -1.0})
        with self.assertRaises(ValueError):
            e.solar_sizing(self.base_settings, {'npv_sacrifice_pct': 101.0})
        with self.assertRaises(ValueError):
            e.solar_sizing(self.base_settings, {'npv_sacrifice_pct': True})
        with self.assertRaises(ValueError):
            e.solar_sizing(self.base_settings, {'npv_sacrifice_pct': float('nan')})
        with self.assertRaises(ValueError):
            e.solar_sizing(self.base_settings, {'npv_sacrifice_pct': float('inf')})

        # Coverage target out of range
        with self.assertRaises(ValueError):
            e.solar_sizing(self.base_settings, {'coverage_target_pct': -0.1})
        with self.assertRaises(ValueError):
            e.solar_sizing(self.base_settings, {'coverage_target_pct': 100.5})
        with self.assertRaises(ValueError):
            e.solar_sizing(self.base_settings, {'coverage_target_pct': False})
        with self.assertRaises(ValueError):
            e.solar_sizing(self.base_settings, {'coverage_target_pct': float('nan')})
        with self.assertRaises(ValueError):
            e.solar_sizing(self.base_settings, {'coverage_target_pct': float('inf')})

    def test_e2e_recommendation_choices_returned_in_sizing(self):
        """solar_sizing returns recommendation_choices and top_alternatives regardless of objective."""
        res_autarky = e.solar_sizing(self.base_settings, {
            'panel_maxima': {'south': 3, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_kwh': 0,
            'battery_max_kwh': 5,
            'battery_step_kwh': 5,
            'objective': 'autarky',
            'npv_sacrifice_pct': 5.0,
            'coverage_target_pct': 50.0
        })
        self.assertIn('recommendation_choices', res_autarky)
        self.assertIn('top_alternatives', res_autarky)
        choices = res_autarky['recommendation_choices']
        self.assertIn('financial', choices)
        self.assertIn('balanced', choices)
        self.assertIn('budget', choices)
        # Even with autarky objective, financial choice is NPV winner
        self.assertGreaterEqual(choices['financial']['npv_median'], res_autarky['recommended_candidate']['npv_median'])

    def test_exact_capacity_signature_preserved(self):
        """Battery capacity preserves exact floating point value (e.g. 2.35001) without rounding."""
        c1 = {
            'panel_count': 10, 'kwp': 4.4, 'roof_counts': {'south': 10},
            'battery_kwh': 2.35001, 'capex_eur': 6000,
            'npv': {'median': 2000.0, 'mean': 2000.0, 'min': 2000.0, 'max': 2000.0},
            'npv_median': 2000, 'npv_mean': 2000.0, 'npv_min': 2000.0, 'npv_max': 2000.0,
            'project_npv': {'median': 2000.0, 'mean': 2000.0, 'min': 2000.0, 'max': 2000.0},
            'project_npv_median': 2000.0, 'project_npv_mean': 2000.0, 'project_npv_min': 2000.0, 'project_npv_max': 2000.0,
            'equity_npv': {'median': 2000.0, 'mean': 2000.0, 'min': 2000.0, 'max': 2000.0},
            'equity_npv_median': 2000.0, 'equity_npv_mean': 2000.0, 'equity_npv_min': 2000.0, 'equity_npv_max': 2000.0,
            'autarky_median': 0.65, 'tie_key': 1
        }
        c2 = {
            'panel_count': 10, 'kwp': 4.4, 'roof_counts': {'south': 10},
            'battery_kwh': 2.35002, 'capex_eur': 6001,
            'npv': {'median': 2001.0, 'mean': 2001.0, 'min': 2001.0, 'max': 2001.0},
            'npv_median': 2001, 'npv_mean': 2001.0, 'npv_min': 2001.0, 'npv_max': 2001.0,
            'project_npv': {'median': 2001.0, 'mean': 2001.0, 'min': 2001.0, 'max': 2001.0},
            'project_npv_median': 2001.0, 'project_npv_mean': 2001.0, 'project_npv_min': 2001.0, 'project_npv_max': 2001.0,
            'equity_npv': {'median': 2001.0, 'mean': 2001.0, 'min': 2001.0, 'max': 2001.0},
            'equity_npv_median': 2001.0, 'equity_npv_mean': 2001.0, 'equity_npv_min': 2001.0, 'equity_npv_max': 2001.0,
            'autarky_median': 0.65, 'tie_key': 2
        }
        res, alts = e.select_solar_recommendations([c1, c2])
        sigs = [a['battery_kwh'] for a in alts]
        self.assertIn(2.35001, sigs)
        self.assertIn(2.35002, sigs)

    def test_near_equivalent_roof_permutations_filtered(self):
        """Top alternatives filters redundant roof permutations with nearly identical metrics."""
        c_fin = {
            'panel_count': 12, 'kwp': 5.28, 'roof_counts': {'south': 12, 'west': 0},
            'battery_kwh': 5.0, 'capex_eur': 8000,
            'npv': {'median': 3000.0, 'mean': 3000.0, 'min': 3000.0, 'max': 3000.0},
            'npv_median': 3000, 'npv_mean': 3000.0, 'npv_min': 3000.0, 'npv_max': 3000.0,
            'project_npv': {'median': 3000.0, 'mean': 3000.0, 'min': 3000.0, 'max': 3000.0},
            'project_npv_median': 3000.0, 'project_npv_mean': 3000.0, 'project_npv_min': 3000.0, 'project_npv_max': 3000.0,
            'equity_npv': {'median': 3000.0, 'mean': 3000.0, 'min': 3000.0, 'max': 3000.0},
            'equity_npv_median': 3000.0, 'equity_npv_mean': 3000.0, 'equity_npv_min': 3000.0, 'equity_npv_max': 3000.0,
            'autarky_median': 0.70, 'tie_key': 1
        }
        # c_perm has same panel count & battery, shifted roof, but essentially identical metrics (<50€ NPV, <0.5% cov)
        c_perm = {
            'panel_count': 12, 'kwp': 5.28, 'roof_counts': {'south': 11, 'west': 1},
            'battery_kwh': 5.0, 'capex_eur': 8010,
            'npv': {'median': 2995.0, 'mean': 2995.0, 'min': 2995.0, 'max': 2995.0},
            'npv_median': 2995, 'npv_mean': 2995.0, 'npv_min': 2995.0, 'npv_max': 2995.0,
            'project_npv': {'median': 2995.0, 'mean': 2995.0, 'min': 2995.0, 'max': 2995.0},
            'project_npv_median': 2995.0, 'project_npv_mean': 2995.0, 'project_npv_min': 2995.0, 'project_npv_max': 2995.0,
            'equity_npv': {'median': 2995.0, 'mean': 2995.0, 'min': 2995.0, 'max': 2995.0},
            'equity_npv_median': 2995.0, 'equity_npv_mean': 2995.0, 'equity_npv_min': 2995.0, 'equity_npv_max': 2995.0,
            'autarky_median': 0.699, 'tie_key': 2
        }
        c_small = {
            'panel_count': 6, 'kwp': 2.64, 'roof_counts': {'south': 6, 'west': 0},
            'battery_kwh': 2.5, 'capex_eur': 4500,
            'npv': {'median': 1800.0, 'mean': 1800.0, 'min': 1800.0, 'max': 1800.0},
            'npv_median': 1800, 'npv_mean': 1800.0, 'npv_min': 1800.0, 'npv_max': 1800.0,
            'project_npv': {'median': 1800.0, 'mean': 1800.0, 'min': 1800.0, 'max': 1800.0},
            'project_npv_median': 1800.0, 'project_npv_mean': 1800.0, 'project_npv_min': 1800.0, 'project_npv_max': 1800.0,
            'equity_npv': {'median': 1800.0, 'mean': 1800.0, 'min': 1800.0, 'max': 1800.0},
            'equity_npv_median': 1800.0, 'equity_npv_mean': 1800.0, 'equity_npv_min': 1800.0, 'equity_npv_max': 1800.0,
            'autarky_median': 0.52, 'tie_key': 3
        }
        res, alts = e.select_solar_recommendations([c_fin, c_perm, c_small])
        # The near-equivalent permutation should be filtered out
        perm_alts = [a for a in alts if a.get('roof_counts', {}).get('west') == 1]
        self.assertEqual(len(perm_alts), 0)
        # c_small is preserved
        self.assertTrue(any(a['panel_count'] == 6 for a in alts))

    def test_missing_metrics_remain_none_not_zero(self):
        """Top alternatives preserves None (JSON null) for missing metrics without inventing 0.0."""
        c = {
            'panel_count': 8, 'kwp': 3.52, 'roof_counts': {'south': 8},
            'battery_kwh': 0.0, 'capex_eur': 5000,
            'npv': {'median': 1500.0, 'mean': 1500.0, 'min': 1500.0, 'max': 1500.0},
            'npv_median': 1500, 'npv_mean': 1500.0, 'npv_min': 1500.0, 'npv_max': 1500.0,
            'project_npv': {'median': 1500.0, 'mean': 1500.0, 'min': 1500.0, 'max': 1500.0},
            'project_npv_median': 1500.0, 'project_npv_mean': 1500.0, 'project_npv_min': 1500.0, 'project_npv_max': 1500.0,
            'equity_npv': {'median': 1500.0, 'mean': 1500.0, 'min': 1500.0, 'max': 1500.0},
            'equity_npv_median': 1500.0, 'equity_npv_mean': 1500.0, 'equity_npv_min': 1500.0, 'equity_npv_max': 1500.0,
            'autarky_median': 0.55, 'annual_grid_export_kwh_median': None,
            'self_consumption_median': None, 'simple_payback_years': None,
            'discounted_payback_years': None, 'tie_key': 1
        }
        res, alts = e.select_solar_recommendations([c])
        alt = alts[0]
        self.assertIsNone(alt['annual_grid_export_kwh_median'])
        self.assertIsNone(alt['self_consumption_median'])
        self.assertIsNone(alt['simple_payback_years'])
        self.assertIsNone(alt['discounted_payback_years'])

    def test_asymmetric_mean_vs_median_candidate_selection_and_positive_eligibility(self):
        """Asymmetric distribution where mean and median prefer different winners verifies mean-based selection, preserved median fields, and mean NPV positive eligibility."""
        cand_a = {
            'panel_count': 12, 'kwp': 5.28, 'roof_counts': {'south': 12},
            'battery_kwh': 5.0, 'battery_modules': 1, 'capex_eur': 8000.0,
            'npv_by_weather': [1000., 4000., 4000., 6000., 15000.],
            'npv': {'median': 4000.0, 'mean': 6000.0, 'min': 1000.0, 'max': 15000.0},
            'npv_median': 4000.0, 'npv_mean': 6000.0, 'npv_min': 1000.0, 'npv_max': 15000.0,
            'project_npv': {'median': 4000.0, 'mean': 6000.0, 'min': 1000.0, 'max': 15000.0},
            'project_npv_median': 4000.0, 'project_npv_mean': 6000.0, 'project_npv_min': 1000.0, 'project_npv_max': 15000.0,
            'equity_npv': {'median': 4000.0, 'mean': 6000.0, 'min': 1000.0, 'max': 15000.0},
            'equity_npv_median': 4000.0, 'equity_npv_mean': 6000.0, 'equity_npv_min': 1000.0, 'equity_npv_max': 15000.0,
            'autarky_median': 0.70, 'self_consumption_median': 0.85,
            'settings': {'south_panels': 12, 'battery_kwh': 5.0}
        }
        cand_b = {
            'panel_count': 10, 'kwp': 4.4, 'roof_counts': {'south': 10},
            'battery_kwh': 5.0, 'battery_modules': 1, 'capex_eur': 7000.0,
            'npv_by_weather': [3000., 4000., 5500., 5500., 7000.],
            'npv': {'median': 5500.0, 'mean': 5000.0, 'min': 3000.0, 'max': 7000.0},
            'npv_median': 5500.0, 'npv_mean': 5000.0, 'npv_min': 3000.0, 'npv_max': 7000.0,
            'project_npv': {'median': 5500.0, 'mean': 5000.0, 'min': 3000.0, 'max': 7000.0},
            'project_npv_median': 5500.0, 'project_npv_mean': 5000.0, 'project_npv_min': 3000.0, 'project_npv_max': 7000.0,
            'equity_npv': {'median': 5500.0, 'mean': 5000.0, 'min': 3000.0, 'max': 7000.0},
            'equity_npv_median': 5500.0, 'equity_npv_mean': 5000.0, 'equity_npv_min': 3000.0, 'equity_npv_max': 7000.0,
            'autarky_median': 0.65, 'self_consumption_median': 0.85,
            'settings': {'south_panels': 10, 'battery_kwh': 5.0}
        }

        # Mean NPV prefers cand_a (6000 > 5000), while median NPV prefers cand_b (5500 > 4000)
        choices, alts = e.select_solar_recommendations([cand_a, cand_b])
        self.assertEqual(choices['financial']['panel_count'], 12)
        self.assertEqual(choices['financial']['npv_mean'], 6000.0)
        self.assertEqual(choices['financial']['npv_median'], 4000.0)
        self.assertEqual(choices['financial']['project_npv_mean'], 6000.0)
        self.assertEqual(choices['financial']['project_npv_median'], 4000.0)
        self.assertEqual(choices['financial']['equity_npv_mean'], 6000.0)
        self.assertEqual(choices['financial']['equity_npv_median'], 4000.0)

        # Top alternatives pool preserves candidate-specific median and mean diagnostics
        alt_b = next(a for a in alts if a['panel_count'] == 10)
        self.assertEqual(alt_b['npv_mean'], 5000.0)
        self.assertEqual(alt_b['npv_median'], 5500.0)
        self.assertEqual(alt_b['project_npv_mean'], 5000.0)
        self.assertEqual(alt_b['project_npv_median'], 5500.0)

        # Mean NPV positive eligibility: positive mean with negative median is eligible
        cand_pos_mean = {
            'panel_count': 6, 'kwp': 2.64, 'roof_counts': {'south': 6},
            'battery_kwh': 0.0, 'capex_eur': 4000.0,
            'npv_by_weather': [-500., -200., -200., -200., -200., -200., 1000., 1500., 1500., 1500., 1500.],
            'npv': {'mean': 500.0, 'median': -200.0, 'min': -500.0, 'max': 1500.0},
            'npv_mean': 500.0, 'npv_median': -200.0, 'npv_min': -500.0, 'npv_max': 1500.0,
            'project_npv': {'mean': 500.0, 'median': -200.0, 'min': -500.0, 'max': 1500.0},
            'project_npv_mean': 500.0, 'project_npv_median': -200.0, 'project_npv_min': -500.0, 'project_npv_max': 1500.0,
            'equity_npv': {'mean': 500.0, 'median': -200.0, 'min': -500.0, 'max': 1500.0},
            'equity_npv_mean': 500.0, 'equity_npv_median': -200.0, 'equity_npv_min': -500.0, 'equity_npv_max': 1500.0,
            'autarky_median': 0.50,
        }
        c_pos, _ = e.select_solar_recommendations([cand_pos_mean])
        self.assertFalse(c_pos['financial']['is_no_install'])
        self.assertTrue(c_pos['financial']['is_positive_npv'])
        self.assertEqual(c_pos['financial']['panel_count'], 6)
        self.assertEqual(c_pos['financial']['npv_mean'], 500.0)
        self.assertEqual(c_pos['financial']['npv_median'], -200.0)

        # Negative mean with positive median is ineligible for installation roles and falls back to no-install
        cand_neg_mean = {
            'panel_count': 6, 'kwp': 2.64, 'roof_counts': {'south': 6},
            'battery_kwh': 0.0, 'capex_eur': 4000.0,
            'npv_by_weather': [-1700., 400., 1000.],
            'npv': {'mean': -100.0, 'median': 400.0, 'min': -1700.0, 'max': 1000.0},
            'npv_mean': -100.0, 'npv_median': 400.0, 'npv_min': -1700.0, 'npv_max': 1000.0,
            'project_npv': {'mean': -100.0, 'median': 400.0, 'min': -1700.0, 'max': 1000.0},
            'project_npv_mean': -100.0, 'project_npv_median': 400.0, 'project_npv_min': -1700.0, 'project_npv_max': 1000.0,
            'equity_npv': {'mean': -100.0, 'median': 400.0, 'min': -1700.0, 'max': 1000.0},
            'equity_npv_mean': -100.0, 'equity_npv_median': 400.0, 'equity_npv_min': -1700.0, 'equity_npv_max': 1000.0,
            'autarky_median': 0.50,
        }
        for cand in (cand_a, cand_b, cand_pos_mean, cand_neg_mean):
            self.assertAlmostEqual(np.mean(cand['npv_by_weather']), cand['npv_mean'], places=10)
            self.assertAlmostEqual(np.median(cand['npv_by_weather']), cand['npv_median'], places=10)
        c_neg, _ = e.select_solar_recommendations([cand_neg_mean])
        self.assertTrue(c_neg['financial']['is_no_install'])
        self.assertFalse(c_neg['financial']['is_positive_npv'])
        self.assertEqual(c_neg['financial']['panel_count'], 0)
        self.assertIsNone(c_neg['balanced'])
        self.assertIsNone(c_neg['budget'])
        self.assertIsNone(c_neg['smaller'])

    def test_choice_inverter_ceiling_metadata_enrichment(self):
        """Balanced and financial choices and top alternatives are enriched with inverter ceiling metadata."""
        res = e.solar_sizing(self.base_settings, {
            'panel_maxima': {'south': 15, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_kwh': 0,
            'battery_max_kwh': 5,
            'objective': 'npv_eur',
            'npv_sacrifice_pct': 5.0,
            'coverage_target_pct': 60.0
        })
        bal = res['recommendation_choices']['balanced']
        fin = res['recommendation_choices']['financial']
        self.assertIsNotNone(bal)
        self.assertIn('reached_inverter_ceiling', bal)
        self.assertIn('reached_roof_bound', bal)
        self.assertIn('inverter_status', bal)
        self.assertIn('bounds_note', bal)
        self.assertIn('detail', bal)

        # Check comparisons label
        rec_comp = next(c for c in res['comparisons'] if c['id'] == 'recommended')
        self.assertEqual(rec_comp['label'], 'Selected-objective winner')

        # Check top alternatives bounds flags
        for alt in res['top_alternatives']:
            self.assertIn('reached_inverter_ceiling', alt)
            self.assertIn('reached_roof_bound', alt)
            self.assertIn('inverter_kw', alt)


class SolarSizingMixedBatteryTests(IsolatedDefaultsTestCase):
    def setUp(self):
        super().setUp()
        self.base_settings = {
            'pv_dispatch_model': 'physical',
            'south_panels': 1,
            'west_panels': 0,
            'east_panels': 0,
            'dormer_panels': 0,
            'flat_panels': 0,
            'secondary_south_panels': 0,
            'battery_kwh': 11.9,
            'battery_modules': 2,
            'battery_secondary_modules': 1,
            'battery_module_kwh': 5.0,
            'battery_secondary_module_kwh': 6.9,
            'battery_mixed_sizes': True,
            'roundtrip_efficiency': 0.9,
            'inverter_kw': 6.0,
            'charge_kw': 5.0,
            'discharge_kw': 5.0,
            'horizon_years': 1,
            'panel_watts': 440,
            'pv_fixed_cost': 1500,
            'pv_cost_kwp': 900,
            'battery_cost_kwh': 550,
            'deduction_rate': 0.5,
            'load_scenario': 'electrified'
        }

    def test_mixed_battery_tiny_roof_and_short_horizon(self):
        """Mixed battery sizing runs on tiny roof bounds and short horizon with correct metadata."""
        settings = {**self.base_settings, 'horizon_years': 1}
        options = {
            'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_kwh': 0,
            'battery_max_kwh': 12,
        }
        res = e.solar_sizing(settings, options)
        summary = res['search_summary']
        self.assertTrue(summary['battery_mixed_sizes'])
        self.assertEqual(summary['battery_step_mode'], 'combinations')
        self.assertIsNone(summary['battery_step_kwh'])
        self.assertEqual(summary['battery_secondary_module_kwh'], 6.9)
        self.assertEqual(summary['battery_module_kwh'], 5.0)

        # Zero baseline clears both counts
        c0 = next(c for c in res['size_curve'] if c['panel_count'] == 0)
        self.assertEqual(c0['battery_kwh'], 0.0)
        self.assertEqual(c0['battery_modules'], 0)
        self.assertEqual(c0['battery_secondary_modules'], 0)
        self.assertEqual(c0['first_modules'], 0)
        self.assertTrue(c0['battery_mixed_sizes'])

        no_inst = next(c for c in res['comparisons'] if c['id'] == 'no_install')
        self.assertEqual(no_inst['battery_kwh'], 0.0)
        self.assertEqual(no_inst['battery_modules'], 0)
        self.assertEqual(no_inst['battery_secondary_modules'], 0)

        rec = res['recommended_candidate']
        self.assertTrue(rec['battery_mixed_sizes'])
        self.assertIn('first_modules', rec)
        self.assertEqual(rec['battery_kwh'], rec['first_modules'] * 5.0 + rec['battery_secondary_modules'] * 6.9)
        self.assertEqual(rec['battery_modules'], rec['first_modules'] + rec['battery_secondary_modules'])

        # Choices and alternatives retain full composition fields
        for key, choice in res['recommendation_choices'].items():
            if choice is not None and isinstance(choice, dict) and 'battery_mixed_sizes' in choice:
                self.assertTrue(choice['battery_mixed_sizes'])
                self.assertIn('first_modules', choice)
                self.assertEqual(choice['battery_kwh'], choice['first_modules'] * 5.0 + choice['battery_secondary_modules'] * 6.9)

        for alt in res['top_alternatives']:
            self.assertTrue(alt['battery_mixed_sizes'])
            self.assertEqual(alt['battery_kwh'], alt['first_modules'] * 5.0 + alt['battery_secondary_modules'] * 6.9)

    def test_mixed_battery_all_nine_screenshot_compositions_admissible(self):
        """All nine 5/6.9 screenshot compositions (1..3 modules) are admissible when capacity range includes them."""
        options = {
            'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_kwh': 0,
            'battery_max_kwh': 21,
        }
        res = e.solar_sizing(self.base_settings, options)
        comps = res['search_summary']['battery_compositions_evaluated']
        eval_pairs = {(c['first_modules'], c['battery_secondary_modules']) for c in comps}

        screenshot_pairs = {
            (1, 0),  # 5.0 kWh
            (0, 1),  # 6.9 kWh
            (2, 0),  # 10.0 kWh
            (1, 1),  # 11.9 kWh
            (0, 2),  # 13.8 kWh
            (3, 0),  # 15.0 kWh
            (2, 1),  # 16.9 kWh
            (1, 2),  # 18.8 kWh
            (0, 3),  # 20.7 kWh
        }
        self.assertTrue(screenshot_pairs.issubset(eval_pairs), f"Missing screenshot compositions: {screenshot_pairs - eval_pairs}")
        self.assertIn((0, 0), eval_pairs)

        # Every composition derives exact unrounded capacity
        for c in comps:
            self.assertEqual(c['battery_kwh'], c['first_modules'] * 5.0 + c['battery_secondary_modules'] * 6.9)

    def test_mixed_battery_interval_11_to_12_includes_11_9(self):
        """Capacity range [11, 12] includes exactly 11.9 kWh (1 first + 1 secondary module)."""
        options = {
            'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_kwh': 11,
            'battery_max_kwh': 12,
        }
        res = e.solar_sizing(self.base_settings, options)
        summary = res['search_summary']
        self.assertEqual(summary['battery_capacities_evaluated'], [11.9])
        comps = summary['battery_compositions_evaluated']
        self.assertEqual(len(comps), 1)
        c = comps[0]
        self.assertEqual(c['first_modules'], 1)
        self.assertEqual(c['battery_secondary_modules'], 1)
        self.assertEqual(c['battery_modules'], 2)
        self.assertEqual(c['battery_kwh'], 11.9)
        self.assertEqual(c['id'], '2m_1sec')

        rec = res['recommended_candidate']
        self.assertEqual(rec['battery_kwh'], 11.9)
        self.assertEqual(rec['first_modules'], 1)
        self.assertEqual(rec['battery_secondary_modules'], 1)
        self.assertEqual(rec['battery_modules'], 2)

    def test_mixed_battery_positive_empty_interval_raises_actionable_error(self):
        """A positive capacity range with no integer module combinations raises an actionable error."""
        options = {
            'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_kwh': 1,
            'battery_max_kwh': 4,
        }
        with self.assertRaises(ValueError) as cm:
            e.solar_sizing(self.base_settings, options)
        self.assertIn("No whole battery module compositions exist between 1 kWh and 4 kWh", str(cm.exception))
        self.assertIn("5 kWh", str(cm.exception))
        self.assertIn("6.9 kWh", str(cm.exception))

    def test_mixed_battery_zero_only_search(self):
        """When min and max capacity are 0, search evaluates only zero storage and clears counts."""
        options = {
            'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_kwh': 0,
            'battery_max_kwh': 0,
        }
        res = e.solar_sizing(self.base_settings, options)
        summary = res['search_summary']
        self.assertEqual(summary['battery_capacities_evaluated'], [0.0])
        self.assertEqual(summary['battery_modules_evaluated'], [0])
        self.assertEqual(len(summary['battery_compositions_evaluated']), 1)
        c0 = summary['battery_compositions_evaluated'][0]
        self.assertEqual(c0['battery_modules'], 0)
        self.assertEqual(c0['battery_secondary_modules'], 0)
        self.assertEqual(c0['first_modules'], 0)
        self.assertEqual(c0['battery_kwh'], 0.0)

        rec = res['recommended_candidate']
        self.assertEqual(rec['battery_kwh'], 0.0)
        self.assertEqual(rec['battery_modules'], 0)
        self.assertEqual(rec['battery_secondary_modules'], 0)
        self.assertEqual(rec['first_modules'], 0)

    def test_mixed_battery_fixed_mode_retention(self):
        """Fixed battery mode retains exact mixed composition, counts, sizes, and cost modes."""
        settings = {
            **self.base_settings,
            'battery_modules': 3,
            'battery_secondary_modules': 1,
            'battery_kwh': 16.9,  # 2 * 5.0 + 1 * 6.9
        }
        # Purchased storage cost mode
        res_purchased = e.solar_sizing(settings, {
            'battery_mode': 'fixed',
            'storage_cost_mode': 'purchased',
            'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        })
        rec_p = res_purchased['recommended_candidate']
        self.assertEqual(rec_p['battery_kwh'], 16.9)
        self.assertEqual(rec_p['battery_modules'], 3)
        self.assertEqual(rec_p['battery_secondary_modules'], 1)
        self.assertEqual(rec_p['first_modules'], 2)
        self.assertTrue(rec_p['battery_mixed_sizes'])
        self.assertEqual(rec_p['settings']['battery_cost_kwh'], 550.0)
        self.assertEqual(res_purchased['search_summary']['storage_cost_mode'], 'purchased')
        self.assertEqual(res_purchased['search_summary']['battery_capacities_evaluated'], [16.9])

        # Existing storage cost mode
        res_existing = e.solar_sizing(settings, {
            'battery_mode': 'fixed',
            'storage_cost_mode': 'existing',
            'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        })
        rec_e = res_existing['recommended_candidate']
        self.assertEqual(rec_e['battery_kwh'], 16.9)
        self.assertEqual(rec_e['battery_modules'], 3)
        self.assertEqual(rec_e['battery_secondary_modules'], 1)
        self.assertEqual(rec_e['first_modules'], 2)
        self.assertEqual(rec_e['settings']['battery_cost_kwh'], 0.0)
        self.assertEqual(res_existing['search_summary']['storage_cost_mode'], 'existing')
        # Sunk cost difference: existing capex is exactly 16.9 * 550 lower
        self.assertAlmostEqual(rec_p['capex_eur'] - rec_e['capex_eur'], 16.9 * 550.0, places=2)

    def test_mixed_battery_stale_quoted_total_does_not_redefine_physical_search_sizes(self):
        """A quote with line items and single size does not redefine physical module sizes or search grid."""
        quote = {
            'battery_nameplate_kwh': 12,
            'battery_dod': 0.8,
            'line_items': [{'description': 'System', 'net_eur': 5000, 'vat': 0.1}]
        }
        res = e.solar_sizing(self.base_settings, {
            'battery_mode': 'joint',
            'battery_min_kwh': 0,
            'battery_max_kwh': 12,
            'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        }, quote=quote)
        summary = res['search_summary']
        self.assertEqual(summary['battery_module_kwh'], 5.0)
        self.assertEqual(summary['battery_secondary_module_kwh'], 6.9)
        self.assertTrue(summary['battery_mixed_sizes'])
        self.assertIn(11.9, summary['battery_capacities_evaluated'])
        self.assertNotIn(9.6, summary['battery_capacities_evaluated'])

        comps = {c['id']: c for c in res['comparisons']}
        self.assertIn('current_quoted', comps)
        self.assertIn('current', comps)
        self.assertAlmostEqual(comps['current_quoted']['battery_kwh'], 11.9, places=3)
        self.assertAlmostEqual(comps['current']['battery_kwh'], 11.9, places=3)
        self.assertEqual(comps['current']['battery_modules'], 2)
        self.assertEqual(comps['current']['battery_secondary_modules'], 1)

    def test_mixed_battery_equal_capacity_distinct_mixes_deterministic(self):
        """Distinct compositions with identical capacity have stable IDs, deterministic choice, and separate metadata."""
        settings = {
            **self.base_settings,
            'battery_module_kwh': 3.0,
            'battery_secondary_module_kwh': 2.0,
            'battery_modules': 2,
            'battery_secondary_modules': 0,
            'battery_kwh': 6.0,
        }
        options = {
            'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_kwh': 6.0,
            'battery_max_kwh': 6.0,
        }
        res1 = e.solar_sizing(settings, options)
        res2 = e.solar_sizing(settings, options)

        # Search evaluated both distinct compositions with 6.0 kWh
        comps = res1['search_summary']['battery_compositions_evaluated']
        self.assertEqual(len(comps), 2)
        comp_ids = {c['id'] for c in comps}
        self.assertEqual(comp_ids, {'2m_0sec', '3m_3sec'})

        c_first = next(c for c in comps if c['id'] == '2m_0sec')
        self.assertEqual(c_first['first_modules'], 2)
        self.assertEqual(c_first['battery_secondary_modules'], 0)
        self.assertEqual(c_first['battery_kwh'], 6.0)

        c_sec = next(c for c in comps if c['id'] == '3m_3sec')
        self.assertEqual(c_sec['first_modules'], 0)
        self.assertEqual(c_sec['battery_secondary_modules'], 3)
        self.assertEqual(c_sec['battery_kwh'], 6.0)

        # Deterministic choice between runs
        rec1 = res1['recommended_candidate']
        rec2 = res2['recommended_candidate']
        self.assertEqual(rec1['tie_key'], rec2['tie_key'])
        self.assertEqual(rec1['battery_modules'], rec2['battery_modules'])
        self.assertEqual(rec1['battery_secondary_modules'], rec2['battery_secondary_modules'])
        self.assertEqual(rec1['first_modules'], rec2['first_modules'])
        self.assertEqual(rec1['battery_kwh'], 6.0)
        self.assertEqual(rec1['battery_modules'], 2)  # Tie-breaker favored fewer total modules

    def test_mixed_battery_tiny_sizes_reject_promptly(self):
        """Tiny module sizes that would blow up candidate count reject promptly before allocating."""
        settings_tiny_first = {**self.base_settings, 'battery_module_kwh': 0.01}
        with self.assertRaises(ValueError) as cm1:
            e.solar_sizing(settings_tiny_first, {
                'battery_min_kwh': 0,
                'battery_max_kwh': 50,
                'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
            })
        self.assertIn("Mixed battery module sizes generate too many candidate capacities", str(cm1.exception))

        settings_tiny_sec = {**self.base_settings, 'battery_secondary_module_kwh': 0.01}
        with self.assertRaises(ValueError) as cm2:
            e.solar_sizing(settings_tiny_sec, {
                'battery_min_kwh': 0,
                'battery_max_kwh': 50,
                'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
            })
        self.assertIn("Mixed battery module sizes generate too many candidate capacities", str(cm2.exception))

    def test_mixed_battery_high_precision_capacities_and_evaluate_parity(self):
        """High-precision capacities in mixed recommendation pass unchanged to evaluate with matching metrics."""
        m1 = 5.123456
        m2 = 6.789012
        settings = {
            **self.base_settings,
            'battery_module_kwh': m1,
            'battery_secondary_module_kwh': m2,
            'battery_modules': 2,
            'battery_secondary_modules': 1,
            'battery_kwh': m1 + m2,
            'horizon_years': 1,
        }
        options = {
            'panel_maxima': {'south': 10, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_kwh': 11,
            'battery_max_kwh': 12,
        }
        res = e.solar_sizing(settings, options)
        rec = res['recommended_candidate']
        expected_kwh = m1 + m2
        self.assertEqual(rec['battery_kwh'], expected_kwh)
        self.assertEqual(rec['first_modules'], 1)
        self.assertEqual(rec['battery_secondary_modules'], 1)

        # Test apply / evaluate parity
        eval_res = e.evaluate(rec['settings'], detail=False)
        self.assertAlmostEqual(rec['npv_median'], eval_res['finance']['pv_incremental']['npv']['median'], places=2)
        self.assertAlmostEqual(rec['autarky_median'], eval_res['metrics']['autarky']['median'], places=3)
        self.assertAlmostEqual(rec['self_consumption_median'], eval_res['metrics']['self_consumption']['median'], places=3)
        self.assertAlmostEqual(rec['capex_eur'], eval_res['finance']['capex_eur'], places=2)


class ConstrainedSearchReliabilityTests(IsolatedDefaultsTestCase):
    """Focused regression and exhaustive reference tests for constrained search reliability."""

    def setUp(self):
        super().setUp()
        self.base_settings = {
            'pv_dispatch_model': 'physical',
            'south_panels': 12,
            'west_panels': 0,
            'east_panels': 0,
            'dormer_panels': 0,
            'flat_panels': 0,
            'secondary_south_panels': 0,
            'battery_kwh': 5.0,
            'roundtrip_efficiency': 0.9,
            'inverter_kw': 6.0,
            'charge_kw': 5.0,
            'discharge_kw': 5.0,
            'horizon_years': 20,
            'panel_watts': 440,
            'pv_fixed_cost': 1500,
            'pv_cost_kwp': 900,
            'battery_cost_kwh': 550,
            'deduction_rate': 0.5,
            'load_scenario': 'electrified'
        }

    def test_independent_exhaustive_reference_manageable_domain(self):
        """Independent brute-force reference validates exact enumeration, candidate counts, and role winners."""
        # Manageable small domain: south up to 3, west up to 2 (max sum 5 <= 12)
        # 400W modules, 5kW inverter (max 12 panels)
        # Joint search with mixed battery 5.0 and 6.9 kWh from 0 to 12 kWh:
        # candidate compositions: (0,0), (5.0, 1x5.0), (6.9, 1x6.9), (10.0, 2x5.0), (11.9, 1x5.0+1x6.9) -> 5 comps
        # 11 positive roof pairs * 5 comps + 1 zero pair (at 0 kWh) = 56 valid candidates.
        settings = {
            **self.base_settings,
            'panel_watts': 400,
            'inverter_kw': 5.0,
            'battery_mixed_sizes': True,
            'battery_module_kwh': 5.0,
            'battery_secondary_module_kwh': 6.9,
            'battery_modules': 1,
            'battery_secondary_modules': 0,
            'battery_kwh': 5.0,
            'pv_fixed_cost': 0,
            'pv_cost_kwp': 1000,
            'battery_cost_kwh': 500,
            'load_scenario': 'baseline',
            'horizon_years': 20
        }
        options = {
            'panel_maxima': {'south': 3, 'west': 2, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_mode': 'joint',
            'battery_min_kwh': 0.0,
            'battery_max_kwh': 12.0,
            'coverage_target_pct': 80.0,
            'npv_sacrifice_pct': 5.0
        }

        # Independent brute-force evaluation: does NOT invoke solar_sizing or internal selection path
        independent_candidates = []
        comps = [
            {'battery_kwh': 0.0, 'battery_modules': 0, 'battery_secondary_modules': 0},
            {'battery_kwh': 5.0, 'battery_modules': 1, 'battery_secondary_modules': 0},
            {'battery_kwh': 6.9, 'battery_modules': 1, 'battery_secondary_modules': 1},
            {'battery_kwh': 10.0, 'battery_modules': 2, 'battery_secondary_modules': 0},
            {'battery_kwh': 11.9, 'battery_modules': 2, 'battery_secondary_modules': 1},
        ]
        for s_cnt in range(4):
            for w_cnt in range(3):
                tot_p = s_cnt + w_cnt
                if tot_p == 0:
                    cand_s = {
                        **settings,
                        'south_panels': 0, 'west_panels': 0,
                        'battery_kwh': 0.0, 'battery_modules': 0, 'battery_secondary_modules': 0,
                        'quote_total': None
                    }
                    eval_out = e.evaluate(cand_s, detail=False)
                    independent_candidates.append({
                        'counts': (s_cnt, w_cnt),
                        'panels': 0,
                        'battery_kwh': 0.0,
                        'npv': eval_out['finance']['pv_incremental']['npv']['median'],
                        'capex': eval_out['finance']['capex_eur'],
                        'autarky': eval_out['metrics']['autarky']['median']
                    })
                else:
                    for comp in comps:
                        cand_s = {
                            **settings,
                            'south_panels': s_cnt, 'west_panels': w_cnt,
                            'battery_kwh': comp['battery_kwh'],
                            'battery_modules': comp['battery_modules'],
                            'battery_secondary_modules': comp['battery_secondary_modules'],
                            'quote_total': None
                        }
                        eval_out = e.evaluate(cand_s, detail=False)
                        independent_candidates.append({
                            'counts': (s_cnt, w_cnt),
                            'panels': tot_p,
                            'battery_kwh': comp['battery_kwh'],
                            'npv': eval_out['finance']['pv_incremental']['npv']['median'],
                            'capex': eval_out['finance']['capex_eur'],
                            'autarky': eval_out['metrics']['autarky']['median']
                        })

        self.assertEqual(len(independent_candidates), 56)

        # Independent selector:
        best_fin_ref = max(independent_candidates, key=lambda c: (c['npv'], -c['capex'], -c['panels']))
        best_npv = best_fin_ref['npv']
        thresh_npv = (1.0 - 0.05) * best_npv
        eligible_bal = [c for c in independent_candidates if c['npv'] >= thresh_npv - 1e-9]
        best_bal_ref = max(eligible_bal, key=lambda c: (c['autarky'], -c['capex'], -c['panels']))

        # Run engine solar_sizing
        res = e.solar_sizing(settings, options)
        summary = res['search_summary']
        self.assertTrue(summary['exact'])
        self.assertTrue(summary['certified_global_optimum'])
        self.assertEqual(summary['search_space_size'], 56)
        self.assertEqual(summary['evaluated_count'], 56)
        self.assertEqual(summary['evaluated_fraction'], 1.0)
        self.assertIsNone(summary['optimality_gap'])

        rec_fin = res['recommendation_choices']['financial']
        self.assertEqual(rec_fin['panel_count'], best_fin_ref['panels'])
        self.assertAlmostEqual(rec_fin['battery_kwh'], best_fin_ref['battery_kwh'], places=2)
        self.assertAlmostEqual(rec_fin['npv_median'], best_fin_ref['npv'], places=1)

        rec_bal = res['recommendation_choices']['balanced']
        self.assertEqual(rec_bal['panel_count'], best_bal_ref['panels'])
        self.assertAlmostEqual(rec_bal['battery_kwh'], best_bal_ref['battery_kwh'], places=2)
        self.assertAlmostEqual(rec_bal['autarky_median'], best_bal_ref['autarky'], places=3)

    def test_selected_pv_ceiling_and_wattage_variation(self):
        """PV array sizing follows its separate kWp limit while AC capacity stays fixed."""
        roof_max = {'south': 40, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        for inverter_kw, pv_max_kwp, panel_watts, expected_panels in (
            (16.0, None, 480, 33), (16.0, None, 500, 32), (12.0, None, 500, 24),
            (10.0, None, 400, 25), (6.0, None, 500, 12), (6.0, 16.0, 480, 33),
        ):
            with self.subTest(inverter_kw=inverter_kw, pv_max_kwp=pv_max_kwp, panel_watts=panel_watts):
                settings = {**self.base_settings, 'inverter_kw': inverter_kw, 'panel_watts': panel_watts}
                if pv_max_kwp is not None:
                    settings['pv_max_kwp'] = pv_max_kwp
                result = e.solar_sizing(
                    settings,
                    {'panel_maxima': roof_max, 'battery_mode': 'fixed', 'evaluation_budget': 100},
                )
                summary = result['search_summary']
                self.assertIsNone(summary['hard_kwp_ceiling'])
                self.assertIsNone(summary['hard_panel_ceiling'])
                self.assertEqual(summary['inverter_panel_ceiling'], expected_panels)
                self.assertAlmostEqual(summary['inverter_kwp_ceiling'], expected_panels * panel_watts / 1000)
                self.assertAlmostEqual(summary['pv_max_kwp'], pv_max_kwp if pv_max_kwp is not None else inverter_kw)
                self.assertEqual(summary['inverter_kw'], inverter_kw)
                if pv_max_kwp is None:
                    self.assertEqual(max(c['panel_count'] for c in result['size_curve']), expected_panels)
                else:
                    self.assertLessEqual(max(c['panel_count'] for c in result['size_curve']), expected_panels)
                    self.assertGreater(summary['physical_constraints']['rejected'], 0)
                for candidate in result['size_curve'] + [result['recommended_candidate']]:
                    self.assertLessEqual(candidate['kwp'], summary['pv_max_kwp'] + 1e-6)
                    self.assertLessEqual(candidate['kwp'], inverter_kw * settings.get('ilr_ac_max', 1.35) + 1e-6)
                    self.assertLessEqual(candidate['panel_count'], expected_panels)

    def test_pv_array_limit_validation_and_legacy_default(self):
        """The independent array limit is nonnegative; omitted values retain the inverter-based limit."""
        self.assertIsNone(e.validate({**self.base_settings, 'inverter_kw': 6.0})['pv_max_kwp'])
        for bad in (-0.1, float('inf'), float('nan')):
            with self.subTest(pv_max_kwp=bad), self.assertRaises(ValueError):
                e.validate({**self.base_settings, 'pv_max_kwp': bad})

    def test_solar_sizing_respects_selected_pv_battery_topology(self):
        """Candidate dispatch and its explanation use the selected coupling approximation."""
        options = {
            'panel_maxima': {'south': 2, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_mode': 'fixed', 'evaluation_budget': 50,
        }
        ac = e.solar_sizing({**self.base_settings, 'pv_battery_coupling': 'ac_equivalent'}, options)
        dc = e.solar_sizing({**self.base_settings, 'pv_battery_coupling': 'dc_coupled'}, options)
        self.assertIn('PVGIS inputs are AC-equivalent', ' '.join(dc['search_summary']['limitations']))
        self.assertIn('PV charging is independent of inverter AC output', ' '.join(dc['search_summary']['limitations']))
        self.assertIn('PV is inverter-AC-capped before battery charging', ' '.join(ac['search_summary']['limitations']))
        self.assertTrue(all(row['settings']['pv_battery_coupling'] == 'ac_equivalent' for row in ac['_evaluated_candidates']))
        self.assertTrue(all(row['settings']['pv_battery_coupling'] == 'dc_coupled' for row in dc['_evaluated_candidates']))
        candidate = dc['recommendation_choices']['balanced'] or dc['recommendation_choices']['financial']
        self.assertIn('annual_clipped_kwh_median', candidate)
        self.assertGreaterEqual(candidate['annual_clipped_pct_median'], 0.0)

    def test_authoritative_roof_bounds_and_no_south_15_cap(self):
        """User panel_maxima are authoritative; south roof is not capped to 15 when theoretical roofs are excluded."""
        settings = {
            **self.base_settings,
            'panel_watts': 500,
            'inverter_kw': 9.0,
            'battery_kwh': 5.0
        }
        options = {
            'panel_maxima': {'south': 18, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_mode': 'fixed',
            'include_theoretical': False
        }
        res = e.solar_sizing(settings, options)
        summary = res['search_summary']
        self.assertEqual(summary['effective_roof_limits']['south'], 18)
        self.assertEqual(summary['effective_roof_limits']['dormer'], 0)
        self.assertEqual(summary['effective_roof_limits']['flat'], 0)
        self.assertEqual(summary['effective_roof_limits']['secondary_south'], 0)
        max_p = max(c['panel_count'] for c in res['size_curve'])
        self.assertEqual(max_p, 18, "South-only 18-panel array must not be artificially clamped to 15 panels")

    def test_incomplete_search_honest_status_and_null_gap(self):
        """Sampled searches report honest best-found status, exact DP space size, and null gap."""
        settings = {
            **self.base_settings,
            'inverter_kw': 6.0,
            'panel_watts': 440,
            'battery_module_kwh': 2.5
        }
        options = {
            'panel_maxima': {'south': 12, 'west': 6, 'east': 4, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_mode': 'joint',
            'battery_min_kwh': 0.0,
            'battery_max_kwh': 15.0,
            'battery_step_kwh': 2.5,
            'evaluation_budget': 100
        }
        res = e.solar_sizing(settings, options)
        summary = res['search_summary']
        self.assertFalse(summary['exact'])
        self.assertTrue(summary['best_found'])
        self.assertFalse(summary['certified_global_optimum'])
        self.assertGreater(summary['search_space_size'], 250)
        self.assertIsNone(summary['optimality_gap'])
        self.assertIsNone(summary['optimality_bound'])
        self.assertFalse(summary['infeasibility_proved'])
        self.assertAlmostEqual(summary['evaluated_fraction'], round(summary['evaluated_count'] / summary['search_space_size'], 6))

    def test_focused_regression_intermediate_storage_budget_choice(self):
        """In a larger search, intermediate battery (6.9 kWh) qualifies for cheaper budget choice than base storage."""
        # Invented equipment costs make a 6.9-kWh module cheaper than one 5-kWh module.
        disk_settings = {
            'routing_priority': 'battery_first', 'panel_watts': 500.,
            'inverter_kw': 9., 'battery_mixed_sizes': True,
            'battery_module_kwh': 5., 'battery_secondary_module_kwh': 6.9,
            'battery_module_cost_override': 3000.,
            'battery_secondary_module_cost_override': 1000.,
            'pv_fixed_cost': 0., 'pv_cost_kwp': 1000.,
            'retail_commodity': .20, 'export_market_price': .05,
            'horizon_years': 20, 'load_scenario': 'baseline',
        }
        options = {
            'panel_maxima': {'south': 20, 'west': 10, 'east': 8, 'dormer': 4, 'flat': 6, 'secondary_south': 4},
            'battery_mode': 'joint', 'battery_min_kwh': 0., 'battery_max_kwh': 20.,
            'coverage_target_pct': 80., 'npv_sacrifice_pct': 5., 'include_theoretical': True,
        }
        res = e.solar_sizing(disk_settings, options)
        budget_c = res['recommendation_choices']['budget']
        self.assertIsNotNone(budget_c, "Budget choice must be found")
        pool = res['_evaluated_candidates']
        eligible = [c for c in pool if c['npv_median'] > 0 and c['autarky_median'] >= .8 - 1e-9]
        oracle = min(eligible, key=lambda c: (c['capex_eur'], -c['autarky_median'], -c['npv_median'], c['panel_count']))
        self.assertEqual(budget_c['roof_counts'], oracle['roof_counts'])
        self.assertAlmostEqual(budget_c['capex_eur'], oracle['capex_eur'], places=8)
        self.assertAlmostEqual(budget_c['battery_kwh'], 6.9, places=8)
        self.assertLess(budget_c['capex_eur'], 10000.)
        self.assertGreaterEqual(budget_c['autarky_median'], 0.80)
        self.assertGreater(budget_c['npv_median'], 0.0)


class SolarSizingBasisAndRolesTests(IsolatedDefaultsTestCase):
    def setUp(self):
        super().setUp()
        self.base_settings = {
            'pv_dispatch_model': 'physical',
            'south_panels': 10,
            'west_panels': 0,
            'east_panels': 0,
            'dormer_panels': 0,
            'flat_panels': 0,
            'secondary_south_panels': 0,
            'battery_kwh': 5.0,
            'roundtrip_efficiency': 0.9,
            'inverter_kw': 6.0,
            'charge_kw': 5.0,
            'discharge_kw': 5.0,
            'horizon_years': 20,
            'panel_watts': 440,
            'pv_fixed_cost': 1500,
            'pv_cost_kwp': 900,
            'battery_cost_kwh': 550,
            'deduction_rate': 0.5,
            'load_scenario': 'electrified',
            'loan_mode': 'fraction',
            'loan_fraction': 1.0,
            'loan_apr': 0.06,
            'loan_years': 10,
            'loan_include_electrification': False
        }

    def test_smaller_role_configurable_retained_profit_pct_and_tie_breaking(self):
        """Smaller role minimizes capex retaining configurable retained_profit_pct of best positive NPV, breaking ties deterministically."""
        yield_priority = ['south', 'west', 'east', 'dormer', 'flat', 'secondary_south']
        
        cand_fin = {
            'panel_count': 14, 'kwp': 6.16, 'roof_counts': {'south': 14}, 'battery_kwh': 5.0,
            'capex_eur': 12000.0,
            'npv': {'median': 4000.0, 'mean': 4000.0, 'min': 4000.0, 'max': 4000.0},
            'npv_median': 4000.0, 'npv_mean': 4000.0, 'npv_min': 4000.0, 'npv_max': 4000.0,
            'project_npv': {'median': 4000.0, 'mean': 4000.0, 'min': 4000.0, 'max': 4000.0},
            'project_npv_median': 4000.0, 'project_npv_mean': 4000.0, 'project_npv_min': 4000.0, 'project_npv_max': 4000.0,
            'equity_npv': {'median': 4000.0, 'mean': 4000.0, 'min': 4000.0, 'max': 4000.0},
            'equity_npv_median': 4000.0, 'equity_npv_mean': 4000.0, 'equity_npv_min': 4000.0, 'equity_npv_max': 4000.0,
            'autarky_median': 0.75, 'tie_key': 1
        }
        cand_b = {
            'panel_count': 10, 'kwp': 4.4, 'roof_counts': {'south': 10}, 'battery_kwh': 5.0,
            'battery_modules': 1, 'battery_secondary_modules': 0,
            'capex_eur': 8500.0,
            'npv': {'median': 3850.0, 'mean': 3850.0, 'min': 3850.0, 'max': 3850.0},
            'npv_median': 3850.0, 'npv_mean': 3850.0, 'npv_min': 3850.0, 'npv_max': 3850.0,
            'project_npv': {'median': 3850.0, 'mean': 3850.0, 'min': 3850.0, 'max': 3850.0},
            'project_npv_median': 3850.0, 'project_npv_mean': 3850.0, 'project_npv_min': 3850.0, 'project_npv_max': 3850.0,
            'equity_npv': {'median': 3850.0, 'mean': 3850.0, 'min': 3850.0, 'max': 3850.0},
            'equity_npv_median': 3850.0, 'equity_npv_mean': 3850.0, 'equity_npv_min': 3850.0, 'equity_npv_max': 3850.0,
            'autarky_median': 0.65, 'tie_key': 2
        }
        cand_c = {
            'panel_count': 8, 'kwp': 3.52, 'roof_counts': {'south': 8}, 'battery_kwh': 5.0,
            'battery_modules': 1, 'battery_secondary_modules': 0,
            'capex_eur': 7200.0,
            'npv': {'median': 3650.0, 'mean': 3650.0, 'min': 3650.0, 'max': 3650.0},
            'npv_median': 3650.0, 'npv_mean': 3650.0, 'npv_min': 3650.0, 'npv_max': 3650.0,
            'project_npv': {'median': 3650.0, 'mean': 3650.0, 'min': 3650.0, 'max': 3650.0},
            'project_npv_median': 3650.0, 'project_npv_mean': 3650.0, 'project_npv_min': 3650.0, 'project_npv_max': 3650.0,
            'equity_npv': {'median': 3650.0, 'mean': 3650.0, 'min': 3650.0, 'max': 3650.0},
            'equity_npv_median': 3650.0, 'equity_npv_mean': 3650.0, 'equity_npv_min': 3650.0, 'equity_npv_max': 3650.0,
            'autarky_median': 0.60, 'tie_key': 3
        }
        cand_d = {
            'panel_count': 9, 'kwp': 3.96, 'roof_counts': {'south': 9}, 'battery_kwh': 5.5,
            'battery_modules': 1, 'battery_secondary_modules': 0,
            'capex_eur': 8500.0,
            'npv': {'median': 3850.0, 'mean': 3850.0, 'min': 3850.0, 'max': 3850.0},
            'npv_median': 3850.0, 'npv_mean': 3850.0, 'npv_min': 3850.0, 'npv_max': 3850.0,
            'project_npv': {'median': 3850.0, 'mean': 3850.0, 'min': 3850.0, 'max': 3850.0},
            'project_npv_median': 3850.0, 'project_npv_mean': 3850.0, 'project_npv_min': 3850.0, 'project_npv_max': 3850.0,
            'equity_npv': {'median': 3850.0, 'mean': 3850.0, 'min': 3850.0, 'max': 3850.0},
            'equity_npv_median': 3850.0, 'equity_npv_mean': 3850.0, 'equity_npv_min': 3850.0, 'equity_npv_max': 3850.0,
            'autarky_median': 0.65, 'tie_key': 4
        }
        candidates = [cand_fin, cand_b, cand_c, cand_d]

        # 1. At retained_profit_pct=95.0, cand_c (91.25%) is below threshold (3800€).
        # Between cand_b (10 panels) and cand_d (9 panels) at capex 8500: cand_d wins due to fewer panels!
        choices_95, alts_95 = e.select_solar_recommendations(
            candidates, yield_priority,
            {'retained_profit_pct': 95.0, 'exact': True}
        )
        smaller_95 = choices_95['smaller']
        self.assertIsNotNone(smaller_95)
        self.assertEqual(smaller_95['panel_count'], 9)
        self.assertEqual(smaller_95['capex_eur'], 8500.0)
        self.assertEqual(smaller_95['retained_profit_pct'], 95.0)
        self.assertAlmostEqual(smaller_95['retained_npv_pct'], 96.25, places=2)
        self.assertEqual(smaller_95['sacrificed_npv_eur'], 150.0)
        self.assertAlmostEqual(smaller_95['sacrificed_npv_pct'], 3.75, places=2)
        self.assertEqual(smaller_95['capex_savings_vs_financial'], 3500.0)
        self.assertEqual(choices_95['role_status']['smaller']['status'], 'certified_optimum')

        # Check propagation to top alternatives
        alt_smaller = next(a for a in alts_95 if a.get('is_smaller'))
        self.assertEqual(alt_smaller['panel_count'], 9)
        self.assertEqual(alt_smaller['retained_profit_pct'], 95.0)

        # 2. At retained_profit_pct=90.0, threshold is 3600€. cand_c (3650€, capex 7200) qualifies and wins over D (capex 8500)!
        choices_90, _ = e.select_solar_recommendations(
            candidates, yield_priority,
            {'retained_profit_pct': 90.0, 'exact': True}
        )
        smaller_90 = choices_90['smaller']
        self.assertIsNotNone(smaller_90)
        self.assertEqual(smaller_90['panel_count'], 8)
        self.assertEqual(smaller_90['capex_eur'], 7200.0)
        self.assertAlmostEqual(smaller_90['retained_npv_pct'], 91.25, places=2)

    def test_no_positive_installation_baseline_and_reasons(self):
        """When no installation achieves positive NPV, financial role falls back to no-install baseline and constrained roles report explicit reasons."""
        yield_priority = ['south', 'west', 'east', 'dormer', 'flat', 'secondary_south']
        cand_neg1 = {
            'panel_count': 6, 'kwp': 2.64, 'roof_counts': {'south': 6}, 'battery_kwh': 5.0,
            'capex_eur': 6000.0,
            'npv': {'median': -500.0, 'mean': -500.0, 'min': -500.0, 'max': -500.0},
            'npv_median': -500.0, 'npv_mean': -500.0, 'npv_min': -500.0, 'npv_max': -500.0,
            'project_npv': {'median': -500.0, 'mean': -500.0, 'min': -500.0, 'max': -500.0},
            'project_npv_median': -500.0, 'project_npv_mean': -500.0, 'project_npv_min': -500.0, 'project_npv_max': -500.0,
            'equity_npv': {'median': -500.0, 'mean': -500.0, 'min': -500.0, 'max': -500.0},
            'equity_npv_median': -500.0, 'equity_npv_mean': -500.0, 'equity_npv_min': -500.0, 'equity_npv_max': -500.0,
            'autarky_median': 0.60, 'tie_key': 1
        }
        cand_neg2 = {
            'panel_count': 10, 'kwp': 4.4, 'roof_counts': {'south': 10}, 'battery_kwh': 10.0,
            'capex_eur': 11000.0,
            'npv': {'median': -1200.0, 'mean': -1200.0, 'min': -1200.0, 'max': -1200.0},
            'npv_median': -1200.0, 'npv_mean': -1200.0, 'npv_min': -1200.0, 'npv_max': -1200.0,
            'project_npv': {'median': -1200.0, 'mean': -1200.0, 'min': -1200.0, 'max': -1200.0},
            'project_npv_median': -1200.0, 'project_npv_mean': -1200.0, 'project_npv_min': -1200.0, 'project_npv_max': -1200.0,
            'equity_npv': {'median': -1200.0, 'mean': -1200.0, 'min': -1200.0, 'max': -1200.0},
            'equity_npv_median': -1200.0, 'equity_npv_mean': -1200.0, 'equity_npv_min': -1200.0, 'equity_npv_max': -1200.0,
            'autarky_median': 0.75, 'tie_key': 2
        }

        # 1. Exact search
        choices_exact, _ = e.select_solar_recommendations(
            [cand_neg1, cand_neg2], yield_priority,
            {'exact': True, 'allowed_sacrifice_pct': 100.0, 'coverage_target_pct': 50.0}
        )
        fin = choices_exact['financial']
        self.assertIsNotNone(fin)
        self.assertTrue(fin['is_no_install'])
        self.assertEqual(fin['panel_count'], 0)
        self.assertEqual(fin['capex_eur'], 0.0)
        self.assertEqual(fin['npv_median'], 0.0)
        self.assertEqual(fin['status'], 'certified_no_install')

        # Balanced requires positive NPV even at 100% sacrifice!
        self.assertIsNone(choices_exact['balanced'])
        self.assertIn('requires strictly positive', choices_exact['role_status']['balanced']['reason'].lower())
        
        # Budget proved unavailable
        self.assertIsNone(choices_exact['budget'])
        self.assertEqual(choices_exact['role_status']['budget']['status'], 'proved_unavailable')
        self.assertTrue(choices_exact['role_status']['budget']['infeasibility_proved'])

        # Smaller requires positive NPV
        self.assertIsNone(choices_exact['smaller'])
        self.assertIn('positive', choices_exact['role_status']['smaller']['reason'].lower())

        # 2. Incomplete search (exact=False)
        choices_approx, _ = e.select_solar_recommendations(
            [cand_neg1, cand_neg2], yield_priority,
            {'exact': False, 'coverage_target_pct': 50.0}
        )
        self.assertEqual(choices_approx['financial']['status'], 'best_found_no_install')
        self.assertEqual(choices_approx['role_status']['budget']['status'], 'target_not_met_among_evaluated')
        self.assertFalse(choices_approx['role_status']['budget']['infeasibility_proved'])

    def test_budget_infeasible_proved_vs_incomplete(self):
        """Budget unreachable reports proved_unavailable when exact, but target_not_met_among_evaluated when approximate."""
        yield_priority = ['south', 'west', 'east', 'dormer', 'flat', 'secondary_south']
        cand_pos = {
            'panel_count': 6, 'kwp': 2.64, 'roof_counts': {'south': 6}, 'battery_kwh': 0.0,
            'capex_eur': 4000.0,
            'npv': {'median': 1200.0, 'mean': 1200.0, 'min': 1200.0, 'max': 1200.0},
            'npv_median': 1200.0, 'npv_mean': 1200.0, 'npv_min': 1200.0, 'npv_max': 1200.0,
            'project_npv': {'median': 1200.0, 'mean': 1200.0, 'min': 1200.0, 'max': 1200.0},
            'project_npv_median': 1200.0, 'project_npv_mean': 1200.0, 'project_npv_min': 1200.0, 'project_npv_max': 1200.0,
            'equity_npv': {'median': 1200.0, 'mean': 1200.0, 'min': 1200.0, 'max': 1200.0},
            'equity_npv_median': 1200.0, 'equity_npv_mean': 1200.0, 'equity_npv_min': 1200.0, 'equity_npv_max': 1200.0,
            'autarky_median': 0.50, 'tie_key': 1
        }

        choices_exact, _ = e.select_solar_recommendations(
            [cand_pos], yield_priority,
            {'exact': True, 'coverage_target_pct': 80.0}
        )
        self.assertIsNone(choices_exact['budget'])
        self.assertEqual(choices_exact['role_status']['budget']['status'], 'proved_unavailable')
        self.assertTrue(choices_exact['role_status']['budget']['infeasibility_proved'])

        choices_approx, _ = e.select_solar_recommendations(
            [cand_pos], yield_priority,
            {'exact': False, 'coverage_target_pct': 80.0}
        )
        self.assertIsNone(choices_approx['budget'])
        self.assertEqual(choices_approx['role_status']['budget']['status'], 'target_not_met_among_evaluated')
        self.assertFalse(choices_approx['role_status']['budget']['infeasibility_proved'])

    def test_basis_switching_rankings_and_envelopes(self):
        """Changing financial_basis between project and after_financing switches ranking and controls objective metrics."""
        opts_base = {
            'panel_maxima': {'south': 5, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_mode': 'fixed',
            'battery_min_kwh': 0.0,
            'battery_max_kwh': 0.0,
            'evaluation_budget': 50
        }
        res_proj = e.solar_sizing(self.base_settings, {**opts_base, 'financial_basis': 'project'})
        res_eq = e.solar_sizing(self.base_settings, {**opts_base, 'financial_basis': 'after_financing'})

        self.assertEqual(res_proj['search_summary']['financial_basis'], 'project')
        self.assertEqual(res_eq['search_summary']['financial_basis'], 'after_financing')

        for res, expected_basis in [(res_proj, 'project'), (res_eq, 'after_financing')]:
            rec = res['recommended_candidate']
            self.assertIn('project_npv', rec)
            self.assertIn('project_npv_median', rec)
            self.assertIn('equity_npv', rec)
            self.assertIn('equity_npv_median', rec)
            self.assertIn('solar_capex_eur', rec)
            self.assertIn('solar_loan_principal_eur', rec)
            self.assertIn('initial_solar_equity_eur', rec)
            self.assertIn('loan_principal_eur', rec)

            if expected_basis == 'project':
                self.assertAlmostEqual(rec['npv_median'], rec['project_npv_median'], places=4)
            else:
                self.assertAlmostEqual(rec['npv_median'], rec['equity_npv_median'], places=4)

            for comp in res['comparisons']:
                self.assertEqual(comp['financial_basis'], expected_basis)
                self.assertIn('project_npv_median', comp)
                self.assertIn('equity_npv_median', comp)
                self.assertIn('initial_solar_equity_eur', comp)

            for curve_pt in res['size_curve']:
                self.assertEqual(curve_pt['financial_basis'], expected_basis)
                self.assertIn('project_npv_median', curve_pt)
                self.assertIn('equity_npv_median', curve_pt)

    def test_cache_invalidation_basis_and_options(self):
        """Cache keys distinguish financial_basis, retained_profit_pct, and loan settings."""
        opts_p = {
            'panel_maxima': {'south': 2, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_mode': 'fixed',
            'evaluation_budget': 50,
            'financial_basis': 'project'
        }
        opts_af = {**opts_p, 'financial_basis': 'after_financing'}
        opts_ret = {**opts_p, 'retained_profit_pct': 80.0}

        r1 = e.solar_sizing(self.base_settings, opts_p)
        r2 = e.solar_sizing(self.base_settings, opts_af)
        r3 = e.solar_sizing(self.base_settings, opts_ret)

        self.assertEqual(r1['search_summary']['financial_basis'], 'project')
        self.assertEqual(r2['search_summary']['financial_basis'], 'after_financing')
        self.assertEqual(r3['search_summary']['retained_profit_pct'], 80.0)

        s_diff_loan = {**self.base_settings, 'loan_apr': 0.12}
        r4 = e.solar_sizing(s_diff_loan, opts_af)
        self.assertNotEqual(r2['recommended_candidate']['equity_npv_median'], r4['recommended_candidate']['equity_npv_median'])

    def test_independent_exhaustive_oracle_manageable_domain_both_bases_all_roles(self):
        """Independent brute-force oracle enumerating small domain verifies all 4 roles under both bases."""
        oracle_settings = {
            **self.base_settings,
            'battery_kwh': 0.0,
            'battery_modules': 0,
            'pv_fixed_cost': 0.0,
            'pv_cost_kwp': 800.0
        }
        domain_opts = {
            'panel_maxima': {'south': 3, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_mode': 'fixed',
            'coverage_target_pct': 10.0,
            'npv_sacrifice_pct': 5.0,
            'retained_profit_pct': 95.0,
            'evaluation_budget': 100
        }

        for basis in ('project', 'after_financing'):
            res = e.solar_sizing(oracle_settings, {**domain_opts, 'financial_basis': basis})
            self.assertTrue(res['search_summary']['certified_global_optimum'])

            candidates = []
            for n in [1, 2, 3]:
                cand_s = {**oracle_settings, 'south_panels': n, 'quote_total': None, 'dormer_south_proxy': False}
                ev = e.evaluate(cand_s, detail=False)
                fin = ev['finance']
                capex = fin['capex_eur']
                proj_npv = fin['pv_incremental']['npv']['median']
                eq_npv = fin['solar_equity']['npv']['median']
                sel_npv = eq_npv if basis == 'after_financing' else proj_npv
                autarky = ev['metrics']['autarky']['median']
                candidates.append({
                    'panel_count': n,
                    'capex_eur': capex,
                    'npv_median': sel_npv,
                    'autarky_median': autarky
                })

            pos_cands = [c for c in candidates if c['npv_median'] > 0]
            self.assertTrue(len(pos_cands) > 0, "At least one positive candidate must exist in test domain")
            oracle_fin = max(pos_cands, key=lambda c: (c['npv_median'], -c['capex_eur'], -c['panel_count']))
            rec_fin = res['recommendation_choices']['financial']
            self.assertEqual(rec_fin['panel_count'], oracle_fin['panel_count'])
            self.assertAlmostEqual(rec_fin['npv_median'], oracle_fin['npv_median'], places=2)

            thresh_bal = 0.95 * oracle_fin['npv_median']
            el_bal = [c for c in pos_cands if c['npv_median'] >= thresh_bal - 1e-9]
            oracle_bal = max(el_bal, key=lambda c: (c['autarky_median'], c['npv_median'], -c['capex_eur'], -c['panel_count']))
            rec_bal = res['recommendation_choices']['balanced']
            self.assertEqual(rec_bal['panel_count'], oracle_bal['panel_count'])
            self.assertAlmostEqual(rec_bal['autarky_median'], oracle_bal['autarky_median'], places=4)

            el_bud = [c for c in pos_cands if c['autarky_median'] >= 0.10 - 1e-9]
            oracle_bud = min(el_bud, key=lambda c: (c['capex_eur'], -c['autarky_median'], -c['npv_median'], c['panel_count']))
            rec_bud = res['recommendation_choices']['budget']
            self.assertEqual(rec_bud['panel_count'], oracle_bud['panel_count'])
            self.assertAlmostEqual(rec_bud['capex_eur'], oracle_bud['capex_eur'], places=2)

            thresh_smaller = 0.95 * oracle_fin['npv_median']
            el_smaller = [c for c in pos_cands if c['npv_median'] >= thresh_smaller - 1e-9]
            oracle_smaller = min(el_smaller, key=lambda c: (c['capex_eur'], c['panel_count']))
            rec_smaller = res['recommendation_choices']['smaller']
            self.assertEqual(rec_smaller['panel_count'], oracle_smaller['panel_count'])
            self.assertAlmostEqual(rec_smaller['capex_eur'], oracle_smaller['capex_eur'], places=2)

    def test_basis_switching_different_winner_and_constrained_roles(self):
        """Controlled reproducible fixture switches financial winner and shifts all constrained thresholds."""
        self.enterContext(frozen_baseline_weather_context())
        fixture_s = {
            # Isolate financial-basis behavior from conversion and C-rate penalties.
            'inverter_efficiency': 1., 'battery_c_rate_max': 1., 'battery_inverter_kw': 6.,
            'load_scenario': 'electrified',
            'scop': 2.0, 'pv_fixed_cost': 0., 'pv_cost_kwp': 800.,
            'inverter_kw': 6.0,
            'battery_cost_kwh': 350.0,
            'loan_fraction': 1.0,
            'loan_apr': 0.15,
            'loan_years': 10,
            'export_market_price': 0.05,
            'pmg_election': False
        }
        res_proj = e.solar_sizing(settings=fixture_s, options={'financial_basis': 'project', 'evaluation_budget': 50, 'coverage_target_pct': 45})
        res_eq = e.solar_sizing(settings=fixture_s, options={'financial_basis': 'after_financing', 'evaluation_budget': 50, 'coverage_target_pct': 45})

        win_proj = res_proj['recommendation_choices']['financial']
        win_eq = res_eq['recommendation_choices']['financial']

        # 1. Financial winners must have different compositions
        for result, winner in ((res_proj, win_proj), (res_eq, win_eq)):
            oracle = max(result['_evaluated_candidates'], key=lambda c: (c['npv_median'], -c['capex_eur'], -c['panel_count']))
            self.assertEqual(winner['roof_counts'], oracle['roof_counts'])
            self.assertEqual(winner['battery_kwh'], oracle['battery_kwh'])
            self.assertAlmostEqual(winner['npv_median'], oracle['npv_median'], places=8)
        self.assertNotEqual(win_proj['panel_count'], win_eq['panel_count'])
        self.assertNotEqual(win_proj['battery_kwh'], win_eq['battery_kwh'])
        self.assertGreater(win_proj['capex_eur'], win_eq['capex_eur'])

        # Financial basis semantics on financial winners
        self.assertAlmostEqual(win_proj['npv_median'], win_proj['project_npv_median'], places=4)
        self.assertAlmostEqual(win_eq['npv_median'], win_eq['equity_npv_median'], places=4)

        # 2. Constrained roles must reflect basis differences
        bal_proj = res_proj['recommendation_choices']['balanced']
        bal_eq = res_eq['recommendation_choices']['balanced']
        for result, winner, financial in ((res_proj, bal_proj, win_proj), (res_eq, bal_eq, win_eq)):
            eligible = [c for c in result['_evaluated_candidates'] if c['npv_median'] >= .95*financial['npv_median'] - 1e-9]
            oracle = max(eligible, key=lambda c: (c['autarky_median'], c['npv_median'], -c['capex_eur'], -c['panel_count']))
            self.assertEqual(winner['roof_counts'], oracle['roof_counts'])
            self.assertEqual(winner['battery_kwh'], oracle['battery_kwh'])
        self.assertNotEqual(bal_proj['battery_kwh'], bal_eq['battery_kwh'])

        # Budget role independent invariant verification over actual candidate table in response
        for res, basis in [(res_proj, 'project'), (res_eq, 'after_financing')]:
            # Inspect every evaluated composition, including candidates absent from the size-curve frontier.
            pool = res['_evaluated_candidates']
            self.assertTrue(len(pool) > 0, f"Candidate pool must not be empty under {basis}")

            # Independently filter positive basis NPV and 45% coverage
            eligible = [
                c for c in pool
                if c['npv_median'] > 0.0 and c['autarky_median'] >= 0.45 - 1e-9
            ]
            self.assertTrue(len(eligible) > 0, f"Eligible candidates meeting 45% coverage and positive NPV must exist under {basis}")

            # Minimum capex with deterministic tie-breaking:
            # (capex ascending, autarky descending, npv descending, panel_count ascending)
            independent_winner = min(
                eligible,
                key=lambda c: (
                    c['capex_eur'],
                    -c['autarky_median'],
                    -c['npv_median'],
                    int(c['panel_count'])
                )
            )

            bud = res['recommendation_choices']['budget']
            self.assertIsNotNone(bud)
            self.assertEqual(bud['panel_count'], independent_winner['panel_count'])
            self.assertAlmostEqual(bud['capex_eur'], independent_winner['capex_eur'], places=2)
            self.assertAlmostEqual(bud['autarky_median'], independent_winner['autarky_median'], places=4)
            self.assertAlmostEqual(bud['npv_median'], independent_winner['npv_median'], places=2)

            if basis == 'project':
                self.assertAlmostEqual(bud['npv_median'], bud['project_npv_median'], places=4)
            else:
                self.assertAlmostEqual(bud['npv_median'], bud['equity_npv_median'], places=4)

        # Budget role switches layout and capex under basis change
        bud_proj = res_proj['recommendation_choices']['budget']
        bud_eq = res_eq['recommendation_choices']['budget']
        self.assertIsNotNone(bud_proj)
        self.assertIsNotNone(bud_eq)
        # Cheapest coverage can be the same hardware under both bases.
        # The financial values must still use their selected project/equity basis.
        self.assertNotEqual(bud_proj['npv_median'], bud_eq['npv_median'])

        # Smaller role winner differs
        sm_proj = res_proj['recommendation_choices']['smaller']
        sm_eq = res_eq['recommendation_choices']['smaller']
        self.assertIsNotNone(sm_proj)
        self.assertIsNotNone(sm_eq)
        self.assertNotEqual(sm_proj['panel_count'], sm_eq['panel_count'])
        for result, winner, best in ((res_proj, sm_proj, win_proj), (res_eq, sm_eq, win_eq)):
            eligible = [c for c in result['_evaluated_candidates'] if c['npv_median'] >= .95*best['npv_median'] - 1e-9]
            self.assertAlmostEqual(winner['capex_eur'], min(c['capex_eur'] for c in eligible), places=8)

    def test_review_repair_1_coherent_no_install_and_empty_domain(self):
        """No-positive branch and empty installation domain provide coherent no-install baseline with baseline grid import."""
        # 1. All negative NPV scenario (within valid spec bounds)
        negative_npv_settings = {
            **self.base_settings,
            'pv_fixed_cost': 15000.0,
            'pv_cost_kwp': 4000.0,
            'battery_cost_kwh': 2000.0,
            'export_market_price': 0.0,
            'battery_kwh': 5.0,
            'battery_modules': 1
        }
        res_neg = e.solar_sizing(negative_npv_settings, {'battery_mode': 'fixed'})
        self.assertIsNone(res_neg['recommendation_choices']['main_recommendation'])

        fin_neg = res_neg['recommendation_choices']['financial']
        self.assertTrue(fin_neg['is_no_install'])
        self.assertEqual(fin_neg['panel_count'], 0)
        self.assertEqual(fin_neg['battery_kwh'], 0.0)
        self.assertEqual(fin_neg['capex_eur'], 0.0)
        self.assertEqual(fin_neg['npv_median'], 0.0)
        self.assertFalse(fin_neg['is_positive_npv'])
        # Annual grid import must be actual household baseline import, not 0
        self.assertGreater(fin_neg['annual_grid_import_kwh_median'], 2000.0)
        self.assertEqual(fin_neg['detail']['finance']['capex_eur'], 0.0)
        self.assertEqual(fin_neg['detail']['finance']['pv_incremental']['npv']['median'], 0.0)

        # 2. Empty installation domain (zero roof panels and zero battery)
        zero_domain_opts = {
            'panel_maxima': {r: 0 for r in e.OPTIMIZE_ROOFS},
            'battery_mode': 'joint',
            'battery_min_kwh': 0.0,
            'battery_max_kwh': 0.0,
            'evaluation_budget': 50
        }
        res_empty = e.solar_sizing(self.base_settings, zero_domain_opts)
        self.assertEqual(res_empty['recommended_candidate']['panel_count'], 0)
        self.assertEqual(res_empty['recommended_candidate']['battery_kwh'], 0.0)
        self.assertIsNone(res_empty['recommendation_choices']['main_recommendation'])
        fin_empty = res_empty['recommendation_choices']['financial']
        self.assertTrue(fin_empty['is_no_install'])
        self.assertEqual(fin_empty['panel_count'], 0)
        self.assertEqual(fin_empty['capex_eur'], 0.0)
        self.assertGreater(fin_empty['annual_grid_import_kwh_median'], 2000.0)

    def test_review_repair_2_and_3_qualified_labels_and_explanations(self):
        """Approximate sizing and after_financing basis use strictly qualified explanations and basis labels."""
        res_af = e.solar_sizing(self.base_settings, {
            'financial_basis': 'after_financing',
            'evaluation_budget': 50
        })
        summary = res_af['search_summary']
        self.assertEqual(summary['financial_basis'], 'after_financing')
        self.assertEqual(summary['objective_label'], 'Maximize mean discounted incremental equity NPV')

        # Role explanation must refer to evaluated configurations and equity NPV
        fin = res_af['recommendation_choices']['financial']
        self.assertIn('evaluated', fin['explanation'].lower())
        self.assertIn('equity npv', fin['explanation'].lower())
        self.assertNotIn('optimum (unqualified)', fin['explanation'])

    def test_economic_cost_additions_roof_extras_and_module_overrides(self):
        """Roof placement extras and battery module overrides are accurately itemized and preserve apply/evaluate parity."""
        s = {
            **self.base_settings,
            'south_panels': 10,
            'west_panels': 5,
            'south_extra_cost_per_panel': 40.0,
            'west_extra_cost_per_panel': 80.0,
            'battery_kwh': 10.0,
            'battery_modules': 2,
            'battery_module_kwh': 5.0,
            'battery_module_cost_override': 2800.0,
            'battery_module_extra_cost': 200.0,
            'quote_total': None
        }
        # 1. VAT included (quote_vat_included: True)
        capex = e.installed_capex(s, 6.6)
        self.assertAlmostEqual(capex, 14240.0, places=2)

        bd = e.detailed_installed_capex_breakdown(s, 6.6)
        self.assertAlmostEqual(bd['pv_fixed_cost_eur'], 1500.0)
        self.assertAlmostEqual(bd['pv_marginal_cost_eur'], 5940.0)
        self.assertAlmostEqual(bd['roof_placement_extra_eur'], 800.0)
        self.assertAlmostEqual(bd['battery_marginal_cost_eur'], 6000.0)
        self.assertAlmostEqual(bd['unadjusted_capex_eur'], 14240.0)
        self.assertAlmostEqual(bd['installed_capex_eur'], 14240.0)

        # 2. VAT excluded (quote_vat_included: False)
        s_excl = {**s, 'quote_vat_included': False}
        self.assertAlmostEqual(e.installed_capex(s_excl, 6.6), 15664.0, places=2)

        # Apply / evaluate parity
        ev = e.evaluate(s, detail=True)
        self.assertAlmostEqual(ev['finance']['capex_eur'], 14240.0, places=2)
        self.assertAlmostEqual(ev['finance']['solar_loan_principal_eur'], 14240.0, places=2)

    def test_solar_sizing_sensitivity_shrink_winner_and_direct_call_parity(self):
        """Downside sensitivity shifts financial winner to smaller layout and matches independent direct sizing."""
        self.enterContext(frozen_baseline_weather_context())
        s = {
            'inverter_kw': 6.0,
            'pv_fixed_cost': 0., 'pv_cost_kwp': 800., 'battery_cost_kwh': 400.,
            'export_market_price': 0.08
        }
        sens_opts = {
            'installed_cost_delta_pct': 25.0,
            'export_revenue_delta_pct': 40.0,
            'household_consumption_delta_pct': 10.0,
            'financing_apr_delta_pp': 2.0
        }
        res_sens = e.solar_sizing_sensitivity(settings=s, sensitivity_options=sens_opts)
        self.assertEqual(res_sens['status'], 'SUCCESS')

        # 1. Base vs Lower Export vs Higher Cost
        base_fin = res_sens['scenarios']['base']['roles']['financial']
        exp_fin = res_sens['scenarios']['lower_export']['roles']['financial']
        cost_fin = res_sens['scenarios']['higher_installed_cost']['roles']['financial']

        self.assertEqual(base_fin['panel_count'], 15)
        self.assertEqual(base_fin['battery_kwh'], 5.0)

        # Lower export shrinks panel count
        self.assertLess(exp_fin['panel_count'], base_fin['panel_count'])
        self.assertEqual(exp_fin['panel_count'], 9)
        self.assertEqual(exp_fin['battery_kwh'], 5.0)
        self.assertEqual(res_sens['scenarios']['lower_export']['base_preferred_repriced']['delta_panels'], -6)

        # Higher installed cost shrinks both panels and battery
        self.assertEqual(cost_fin['panel_count'], 10)
        self.assertEqual(cost_fin['battery_kwh'], 0.0)
        self.assertTrue(res_sens['scenarios']['higher_installed_cost']['base_preferred_repriced']['changed_layout'])
        self.assertEqual(res_sens['scenarios']['higher_installed_cost']['base_preferred_repriced']['delta_panels'], -5)
        self.assertEqual(res_sens['scenarios']['higher_installed_cost']['base_preferred_repriced']['delta_battery_kwh'], -5.0)

        # 2. Direct call parity: lower_export matches direct solar_sizing
        direct_exp = e.solar_sizing(settings={**s, 'export_market_price': 0.08 * 0.6})
        direct_exp_fin = direct_exp['recommendation_choices']['financial']
        self.assertEqual(exp_fin['panel_count'], direct_exp_fin['panel_count'])
        self.assertEqual(exp_fin['battery_kwh'], direct_exp_fin['battery_kwh'])
        self.assertAlmostEqual(exp_fin['npv_median'], direct_exp_fin['npv_median'], places=2)

        # 3. Weather envelope preservation across scenarios
        for sid, scen in res_sens['scenarios'].items():
            fin_cand = scen['roles']['financial']
            self.assertIn('project_npv', fin_cand)
            self.assertIn('equity_npv', fin_cand)
            self.assertIn('min', fin_cand['project_npv'])
            self.assertIn('max', fin_cand['project_npv'])
            self.assertLess(fin_cand['project_npv']['min'], fin_cand['project_npv']['max'])

        # 4. Input validation
        with self.assertRaises(ValueError):
            e.solar_sizing_sensitivity(sensitivity_options={'export_revenue_delta_pct': -5.0})
        with self.assertRaises(ValueError):
            e.solar_sizing_sensitivity(sensitivity_options={'unknown_param': 10})

    def test_solar_sizing_sensitivity_panel_maxima_cache_freezing_thawing(self):
        """Blocker fix: passing panel_maxima dict in options freezes and thaws cleanly without error."""
        opt = {'panel_maxima': dict.fromkeys(e.OPTIMIZE_ROOFS, 0), 'battery_max_kwh': 0}
        # First call: executes simulation and populates cache
        res1 = e.solar_sizing_sensitivity(settings={}, options=opt)
        self.assertEqual(res1['status'], 'SUCCESS')
        fin1 = res1['scenarios']['base']['roles']['financial']
        self.assertTrue(fin1['is_no_install'])
        self.assertEqual(fin1['panel_count'], 0)

        # Second call: exercises lru_cache hit with thawed panel_maxima
        res2 = e.solar_sizing_sensitivity(settings={}, options=opt)
        self.assertEqual(res2['status'], 'SUCCESS')
        fin2 = res2['scenarios']['base']['roles']['financial']
        self.assertEqual(fin2['panel_count'], 0)

    def test_solar_sizing_sensitivity_multi_roof_repriced_base_vs_cell_winner(self):
        """Original physical candidate repricing evaluates exact original roofs, not cell winner with different layout."""
        s = {
            'inverter_kw': 3.0,
            'pv_cost_kwp': 300.0,
            'pv_fixed_cost': 0.0,
            'west_extra_cost_per_panel': 50.0,
            'export_market_price': 0.25,
            'loan_mode': 'fraction',
            'loan_fraction': 0.5,
            'loan_apr': 0.05,
            'loan_years': 10
        }
        # Force a base layout of 1 south + 1 west by restricting panel_maxima
        opt = {
            'panel_maxima': {'south': 1, 'west': 1, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_max_kwh': 0.0,
            'financial_basis': 'project'
        }
        sens_opts = {
            'installed_cost_delta_pct': 30.0,
            'export_revenue_delta_pct': 20.0
        }
        s_orig_copy = deepcopy(s)
        opt_orig_copy = deepcopy(opt)

        res = e.solar_sizing_sensitivity(settings=s, options=opt, sensitivity_options=sens_opts)
        self.assertEqual(res['status'], 'SUCCESS')

        # Verify inputs remained immutable
        self.assertEqual(s, s_orig_copy)
        self.assertEqual(opt, opt_orig_copy)

        base_bp = res['scenarios']['base']['base_preferred_repriced']
        cost_bp = res['scenarios']['higher_installed_cost']['base_preferred_repriced']

        # Under higher installed cost (+30%), evaluate exact 1 south + 1 west layout independently
        c_factor = 1.30
        s_stressed = {
            **s,
            'pv_fixed_cost': s['pv_fixed_cost'] * c_factor,
            'pv_cost_kwp': s['pv_cost_kwp'] * c_factor,
            'west_extra_cost_per_panel': s['west_extra_cost_per_panel'] * c_factor,
            'south_panels': 1,
            'west_panels': 1,
            'east_panels': 0,
            'dormer_panels': 0,
            'flat_panels': 0,
            'secondary_south_panels': 0,
            'battery_kwh': 0.0
        }
        ev_stressed = e.evaluate(s_stressed, detail=True)

        # Repriced base capex and NPV median must match independent evaluate exactly
        self.assertAlmostEqual(cost_bp['capex_eur'], ev_stressed['finance']['capex_eur'], places=2)
        self.assertAlmostEqual(cost_bp['npv_median'], ev_stressed['finance']['pv_incremental']['npv']['median'], places=2)
        self.assertEqual(cost_bp['roof_counts']['south'], 1)
        self.assertEqual(cost_bp['roof_counts']['west'], 1)

        # Cashflow / loan principal parity: loan in fraction mode scales with capex
        self.assertAlmostEqual(ev_stressed['finance']['solar_loan_principal_eur'], ev_stressed['finance']['capex_eur'] * 0.5, places=2)

        # Defect 1 check: Cell winner vs exact repricing difference
        # In a search allowing 2 south panels, a 2-panel cell winner is 2 south + 0 west (lower cost, higher yield).
        # But repricing base_preferred (1 south + 1 west) evaluates 1 south + 1 west, NOT the cell winner.
        opt_wider = {
            'panel_maxima': {'south': 2, 'west': 1, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_max_kwh': 0.0,
        }
        scen_res = e.solar_sizing(s_stressed, opt_wider)
        cell_2p = next(c for c in scen_res['size_curve'] if c['panel_count'] == 2)
        self.assertEqual(cell_2p['roof_counts']['south'], 2)
        self.assertEqual(cell_2p['roof_counts']['west'], 0)

        # Repriced base candidate has 1 west panel and extra cost, so capex differs from cell winner
        repriced_from_helper = e._find_repriced_base_candidate(base_bp, s_stressed, opt_wider)
        self.assertEqual(repriced_from_helper['roof_counts']['south'], 1)
        self.assertEqual(repriced_from_helper['roof_counts']['west'], 1)
        self.assertNotEqual(repriced_from_helper['capex_eur'], cell_2p['capex_eur'])
        self.assertAlmostEqual(repriced_from_helper['capex_eur'], ev_stressed['finance']['capex_eur'], places=2)

    def test_solar_sizing_sensitivity_unavailable_role_compact_null(self):
        """Compact candidate for unavailable roles returns null with role_status, not a false no-install alternative."""
        cand_none = e._compact_candidate(None, is_no_install_baseline=False)
        self.assertIsNone(cand_none)

        cand_baseline = e._compact_candidate(None, is_no_install_baseline=True)
        self.assertIsNotNone(cand_baseline)
        self.assertTrue(cand_baseline['is_no_install'])

    def test_fixed_existing_storage_zeroes_all_initial_capex_and_preserves_replacements(self):
        """Fixed existing storage zeroes all initial battery capex including overrides and extras, preserving replacements."""
        s = {
            'inverter_kw': 6.0,
            'battery_kwh': 10.0,
            'battery_cost_kwh': 900.0,
            'battery_module_cost_override': 3500.0,
            'battery_secondary_module_cost_override': 3500.0,
            'battery_module_extra_cost': 300.0,
            'battery_secondary_module_extra_cost': 300.0,
            'south_panels': 10,
            'west_panels': 0,
            'east_panels': 0,
            'dormer_panels': 0,
            'flat_panels': 0,
            'secondary_south_panels': 0
        }
        # In installed_capex and detailed_installed_capex_breakdown with storage_cost_mode='existing'
        s_existing = e.validate({**s, 'storage_cost_mode': 'existing'})
        breakdown = e.detailed_installed_capex_breakdown(s_existing, kwp=4.0)
        self.assertEqual(breakdown['battery_marginal_cost_eur'], 0.0)
        self.assertEqual(breakdown['battery_breakdown']['total_battery_cost_eur'], 0.0)
        self.assertEqual(breakdown['battery_breakdown']['status'], 'existing')

        # Sizing with fixed existing storage
        opt = {
            'storage_cost_mode': 'existing',
            'battery_mode': 'fixed',
            'panel_maxima': {'south': 10, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'evaluation_budget': 50
        }
        res = e.solar_sizing(settings=s, options=opt)
        rec = res['recommended_candidate']
        self.assertEqual(rec['battery_kwh'], 10.0)
        # Battery capex is 0 € sunk cost; capex equals PV-only capex
        self.assertAlmostEqual(rec['capex_eur'], breakdown['installed_capex_eur'], places=2)

        # Replacements at year 15 are still evaluated (npv is lower with positive replacement cost)
        ev_with_rep = e.evaluate(s_existing)
        ev_no_rep = e.evaluate({**s_existing, 'battery_replacement_cost_kwh': 0.0})
        self.assertLess(ev_with_rep['finance']['pv_incremental']['npv']['median'], ev_no_rep['finance']['pv_incremental']['npv']['median'])

    def test_solar_sizing_sensitivity_boundary_violations_without_silent_clamping(self):
        """Boundary-violating economic perturbations report UNAVAILABLE with explicit reasons without silent clamping."""
        # 1. Lower consumption pushing load_growth < 0.5 SPEC bound
        s = {'load_growth': 0.8}
        # 60% reduction -> 0.8 * 0.4 = 0.32 (< 0.5 limit)
        sens_opts = {'household_consumption_delta_pct': 60.0}
        res = e.solar_sizing_sensitivity(settings=s, options={'panel_maxima': dict.fromkeys(e.OPTIMIZE_ROOFS, 0), 'battery_max_kwh': 0}, sensitivity_options=sens_opts)
        self.assertEqual(res['status'], 'SUCCESS')
        scen_cons = res['scenarios']['lower_consumption']
        self.assertEqual(scen_cons['status'], 'UNAVAILABLE')
        self.assertIn('outside engine limits', scen_cons['unavailable_reason'])
        self.assertIsNone(scen_cons['roles']['financial'])

        # Base scenario is preserved
        self.assertEqual(res['scenarios']['base']['status'], 'SUCCESS')

        # 2. Higher APR pushing loan_apr > 30% SPEC bound
        s_apr = {'loan_apr': 0.10}
        # +25 pp -> 35% (> 30% limit)
        sens_apr = {'financing_apr_delta_pp': 25.0}
        res_apr = e.solar_sizing_sensitivity(settings=s_apr, options={'panel_maxima': dict.fromkeys(e.OPTIMIZE_ROOFS, 0), 'battery_max_kwh': 0}, sensitivity_options=sens_apr)
        scen_apr = res_apr['scenarios']['higher_apr']
        self.assertEqual(scen_apr['status'], 'UNAVAILABLE')
        self.assertIn('exceeds the maximum allowable borrowing rate', scen_apr['unavailable_reason'])
        self.assertIsNone(scen_apr['roles']['financial'])

    def test_changed_layout_signature_equal_capacity_distinct_composition(self):
        """Layout signature and changed_layout distinguish distinct integer battery compositions of equal kWh."""
        cand_uniform = {
            'panel_count': 10,
            'battery_kwh': 10.0,
            'battery_modules': 2,
            'battery_secondary_modules': 0,
            'roof_counts': {'south': 10, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        }
        cand_single = {
            'panel_count': 10,
            'battery_kwh': 10.0,
            'battery_modules': 1,
            'battery_secondary_modules': 0,
            'roof_counts': {'south': 10, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        }
        cand_diff_roofs = {
            'panel_count': 10,
            'battery_kwh': 10.0,
            'battery_modules': 2,
            'battery_secondary_modules': 0,
            'roof_counts': {'south': 6, 'west': 4, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        }
        sig_a = e._layout_sig(cand_uniform)
        sig_b = e._layout_sig(cand_single)
        sig_c = e._layout_sig(cand_diff_roofs)

        self.assertNotEqual(sig_a, sig_b)
        self.assertNotEqual(sig_a, sig_c)

    def test_direct_solar_sizing_and_sensitivity_base_parity_with_quote(self):
        """Base and cost-stress in sensitivity achieve exact parity with direct solar_sizing and independent candidate."""
        quote = {
            'quote_10kwh_eur': 5000.0,
            'quote_15kwh_eur': 7000.0,
        }
        s = {
            'inverter_kw': 6.0,
            'load_scenario': 'baseline',
            'battery_module_kwh': 5.0,
            'battery_secondary_module_kwh': 6.9,
            'battery_mixed_sizes': True,
            'battery_kwh': 11.9,
        }
        opt = {
            'load_scenario': 'electrified',
            'panel_maxima': {'south': 8, 'west': 4, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_kwh': 11.9,
            'battery_max_kwh': 11.9,
            'battery_step_kwh': 5.0,
            'battery_mode': 'joint',
            'quote_10kwh_eur': 5000.0,
            'quote_15kwh_eur': 7000.0,
        }

        direct_res = e.solar_sizing(settings=s, options=opt, quote=quote)
        sens_res = e.solar_sizing_sensitivity(settings=s, options=opt, quote=quote)

        direct_fin = direct_res['recommendation_choices']['financial']
        sens_base_repriced = sens_res['scenarios']['base']['base_preferred_repriced']
        sens_cost_repriced = sens_res['scenarios']['higher_installed_cost']['base_preferred_repriced']

        self.assertGreater(sens_base_repriced['panel_count'], 0)
        self.assertAlmostEqual(sens_base_repriced['battery_kwh'], 11.9, places=2)

        self.assertEqual(direct_fin['panel_count'], sens_base_repriced['panel_count'])
        self.assertEqual(direct_fin['battery_kwh'], sens_base_repriced['battery_kwh'])
        self.assertAlmostEqual(direct_fin['capex_eur'], sens_base_repriced['capex_eur'], places=2)
        self.assertAlmostEqual(direct_fin['npv_median'], sens_base_repriced['npv_median'], places=2)

        c_factor = 1.20
        s_cost = deepcopy(s)
        s_cost['pv_fixed_cost'] = float(e.defaults()['pv_fixed_cost']) * c_factor
        s_cost['pv_cost_kwp'] = float(e.defaults()['pv_cost_kwp']) * c_factor
        s_cost['battery_cost_kwh'] = float(e.defaults()['battery_cost_kwh']) * c_factor
        opt_cost = deepcopy(opt)
        opt_cost['quote_10kwh_eur'] = 5000.0 * c_factor
        opt_cost['quote_15kwh_eur'] = 7000.0 * c_factor
        q_cost = deepcopy(quote)
        q_cost['quote_10kwh_eur'] = 5000.0 * c_factor
        q_cost['quote_15kwh_eur'] = 7000.0 * c_factor

        direct_cost_res = e.solar_sizing(settings=s_cost, options=opt_cost, quote=q_cost)
        stress_winner = direct_cost_res['recommendation_choices']['financial']
        reported_winner = sens_res['scenarios']['higher_installed_cost']['roles']['financial']
        self.assertEqual(stress_winner['roof_counts'], reported_winner['roof_counts'])
        self.assertAlmostEqual(stress_winner['npv_median'], reported_winner['npv_median'], places=8)
        direct_cost_fin = next(c for c in direct_cost_res['_evaluated_candidates']
            if c['roof_counts'] == sens_cost_repriced['roof_counts']
            and c['battery_modules'] == sens_cost_repriced['battery_modules']
            and c.get('battery_secondary_modules', 0) == sens_cost_repriced['battery_secondary_modules'])

        self.assertEqual(sens_cost_repriced['panel_count'], direct_cost_fin['panel_count'])
        self.assertEqual(sens_cost_repriced['battery_kwh'], direct_cost_fin['battery_kwh'])
        self.assertAlmostEqual(sens_cost_repriced['capex_eur'], direct_cost_fin['capex_eur'], places=2)
        self.assertAlmostEqual(sens_cost_repriced['npv_median'], direct_cost_fin['npv_median'], places=2)

    def test_solar_sizing_sensitivity_incomplete_search_pool_baseline_restoration(self):
        """Incomplete search pool scenario restores repriced base, reselects 4 roles, and preserves smaller threshold."""
        orig_sizing = e.solar_sizing

        def mock_solar_sizing(settings=None, options=None, quote=None):
            res = orig_sizing(settings=settings, options=options, quote=quote)
            eval_cands = res.get('_evaluated_candidates', [])
            filtered = [c for c in eval_cands if c['panel_count'] <= 6]
            if filtered:
                res['_evaluated_candidates'] = filtered
                res['search_summary']['evaluated_count'] = len(filtered)
                res['search_summary']['valid_candidates_count'] = len(eval_cands)
                res['search_summary']['evaluated_fraction'] = len(filtered) / max(1, len(eval_cands))
                res['recommendation_choices'], _ = e.select_solar_recommendations(filtered, options=options)
            return res

        s = {'inverter_kw': 6.0}
        opt = {'panel_maxima': {'south': 10, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}, 'battery_max_kwh': 0.0}

        base_full = e.solar_sizing(settings=s, options=opt)
        base_pref = base_full['recommendation_choices']['financial']

        call_count = 0
        def mocked_scenario_sizing(settings=None, options=None, quote=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return orig_sizing(settings=settings, options=options, quote=quote)
            return mock_solar_sizing(settings=settings, options=options, quote=quote)

        with patch('feasibility.engine.solar_sizing', side_effect=mocked_scenario_sizing):
            sens_res = e.solar_sizing_sensitivity(settings=s, options=opt)

        export_scen = sens_res['scenarios']['lower_export']
        fin_scen = export_scen['roles']['financial']
        smaller_scen = export_scen['roles']['smaller']

        self.assertEqual(fin_scen['panel_count'], base_pref['panel_count'])
        self.assertEqual(export_scen['search_summary']['evaluated_count'], 8)
        self.assertEqual(export_scen['search_summary']['valid_candidates_count'], 11)
        self.assertFalse(export_scen['search_summary']['exact'])
        self.assertFalse(export_scen['search_summary'].get('certified_global_optimum', False))
        if smaller_scen:
            self.assertGreaterEqual(smaller_scen['npv_median'], fin_scen['npv_median'] * 0.95 - 1e-4)

    def test_solar_sizing_sensitivity_cost_override_maxbounds_unavailable(self):
        """Perturbed cost override exceeding parameter limits marks scenario UNAVAILABLE without aborting."""
        s_high = {'inverter_kw': 6.0, 'battery_module_cost_override': 18000.0}
        opt = {'panel_maxima': dict.fromkeys(e.OPTIMIZE_ROOFS, 0), 'battery_max_kwh': 0.0}
        res = e.solar_sizing_sensitivity(settings=s_high, options=opt)
        h_cost = res['scenarios']['higher_installed_cost']
        self.assertEqual(h_cost['status'], 'UNAVAILABLE')
        self.assertIn('battery_module_cost_override', h_cost['unavailable_reason'])
        self.assertEqual(res['scenarios']['base']['status'], 'SUCCESS')

    def test_solar_sizing_sensitivity_quote_derived_rate_exceeds_bounds_unavailable(self):
        """When quote-derived unit battery cost under cost stress exceeds modeling bounds, mark higher_installed_cost UNAVAILABLE."""
        s = {
            'battery_module_kwh': 5.0,
            'battery_kwh': 10.0,
            'battery_mixed_sizes': False,
            'quote_vat_included': False,
            'inverter_kw': 6.0
        }
        opt = {
            'panel_maxima': {'south': 2, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_kwh': 10.0,
            'battery_max_kwh': 10.0,
            'battery_mode': 'joint'
        }
        quote = {
            'quote_10kwh_eur': 19000.0,
            'quote_15kwh_eur': 24000.0
        }
        # Direct base solar_sizing succeeds
        base_res = e.solar_sizing(settings=s, options=opt, quote=quote)
        self.assertIsNotNone(base_res['recommended_candidate'])

        # Sensitivity should run without raising unhandled ValueError
        sens_res = e.solar_sizing_sensitivity(settings=s, options=opt, quote=quote)
        self.assertIn('scenarios', sens_res)
        scens = sens_res['scenarios']

        # base, lower_export, lower_consumption, higher_apr succeed
        for k in ['base', 'lower_export', 'lower_consumption', 'higher_apr']:
            self.assertEqual(scens[k]['status'], 'SUCCESS', f'Scenario {k} should succeed')

        # higher_installed_cost is cleanly marked UNAVAILABLE
        h_cost = scens['higher_installed_cost']
        self.assertEqual(h_cost['status'], 'UNAVAILABLE')
        self.assertIn('battery_cost_kwh', h_cost['unavailable_reason'])

    def test_quote_only_variants_base_and_cost_stress_parity(self):
        """Quote variants in quote object only (not options) match between base candidate and sensitivity base repricing under base and cost stress."""
        s = {
            'battery_mixed_sizes': True,
            'battery_module_kwh': 5.0,
            'battery_secondary_module_kwh': 6.9,
            'battery_kwh': 11.9,
            'battery_module_cost_override': 3000.0,
            'battery_secondary_module_cost_override': 4000.0,
            'load_scenario': 'baseline',
            'inverter_kw': 6.0
        }
        opt = {
            'load_scenario': 'electrified',
            'panel_maxima': {'south': 8, 'west': 4, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_kwh': 11.9,
            'battery_max_kwh': 11.9,
            'battery_mode': 'joint'
        }
        quote = {
            'quote_10kwh_eur': 5000.0,
            'quote_15kwh_eur': 7000.0
        }
        base_res = e.solar_sizing(settings=s, options=opt, quote=quote)
        fin_cand = base_res['recommendation_choices']['financial']

        sens_res = e.solar_sizing_sensitivity(settings=s, options=opt, quote=quote)
        sens_base_repriced = sens_res['scenarios']['base']['base_preferred_repriced']
        sens_cost_repriced = sens_res['scenarios']['higher_installed_cost']['base_preferred_repriced']

        # Base parity
        self.assertEqual(fin_cand['panel_count'], sens_base_repriced['panel_count'])
        self.assertEqual(fin_cand['battery_kwh'], sens_base_repriced['battery_kwh'])
        self.assertAlmostEqual(fin_cand['capex_eur'], sens_base_repriced['capex_eur'], places=2)
        self.assertAlmostEqual(fin_cand['npv_median'], sens_base_repriced['npv_median'], places=2)

        # Cost stress parity
        c_factor = 1.20
        s_cost = deepcopy(s)
        s_cost['pv_fixed_cost'] = float(e.defaults()['pv_fixed_cost']) * c_factor
        s_cost['pv_cost_kwp'] = float(e.defaults()['pv_cost_kwp']) * c_factor
        s_cost['battery_cost_kwh'] = float(e.defaults()['battery_cost_kwh']) * c_factor
        s_cost['battery_module_cost_override'] = 3000.0 * c_factor
        s_cost['battery_secondary_module_cost_override'] = 4000.0 * c_factor
        q_cost = deepcopy(quote)
        q_cost['quote_10kwh_eur'] = 5000.0 * c_factor
        q_cost['quote_15kwh_eur'] = 7000.0 * c_factor

        direct_cost_res = e.solar_sizing(settings=s_cost, options=opt, quote=q_cost)
        stress_winner = direct_cost_res['recommendation_choices']['financial']
        reported_winner = sens_res['scenarios']['higher_installed_cost']['roles']['financial']
        self.assertEqual(stress_winner['roof_counts'], reported_winner['roof_counts'])
        self.assertAlmostEqual(stress_winner['npv_median'], reported_winner['npv_median'], places=8)
        direct_cost_fin = next(c for c in direct_cost_res['_evaluated_candidates']
            if c['roof_counts'] == sens_cost_repriced['roof_counts']
            and c['battery_modules'] == sens_cost_repriced['battery_modules']
            and c.get('battery_secondary_modules', 0) == sens_cost_repriced['battery_secondary_modules'])

        self.assertEqual(sens_cost_repriced['panel_count'], direct_cost_fin['panel_count'])
        self.assertEqual(sens_cost_repriced['battery_kwh'], direct_cost_fin['battery_kwh'])
        self.assertAlmostEqual(sens_cost_repriced['capex_eur'], direct_cost_fin['capex_eur'], places=2)
        self.assertAlmostEqual(sens_cost_repriced['npv_median'], direct_cost_fin['npv_median'], places=2)

    def test_existing_to_purchased_cost_mode_canonicalization_and_repricing(self):
        """When input settings specify storage_cost_mode='existing' but options request 'purchased',
        canonicalize candidate effective cost mode, reprice accurately (5128.67 vs 2378.67),
        maintain parity with clean request, and verify independent quote_evaluate and replacement."""
        s = e.defaults()
        s['battery_module_kwh'] = 5.0
        s['battery_kwh'] = 5.0
        s['storage_cost_mode'] = 'existing'

        opts = {
            'panel_maxima': {'south': 2, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_kwh': 5.0,
            'battery_max_kwh': 5.0,
            'storage_cost_mode': 'purchased',
            'battery_mode': 'joint'
        }

        # 1. Candidate pricing under options storage_cost_mode='purchased'
        res = e.solar_sizing(s, opts)
        cand = res['recommended_candidate']
        self.assertAlmostEqual(cand['capex_eur'], 4300.0, places=3)
        self.assertEqual(cand['storage_cost_mode'], 'purchased')
        self.assertEqual(cand['settings']['storage_cost_mode'], 'purchased')

        # 2. Identical request with existing flag removed yields identical capex
        s_clean = e.defaults()
        s_clean['battery_module_kwh'] = 5.0
        s_clean['battery_kwh'] = 5.0
        res_clean = e.solar_sizing(s_clean, opts)
        cand_clean = res_clean['recommended_candidate']
        self.assertAlmostEqual(cand['capex_eur'], cand_clean['capex_eur'], places=4)
        self.assertAlmostEqual(cand['npv_median'], cand_clean['npv_median'], places=2)

        # 3. Independent evaluation with quote_evaluate agrees with sizing candidate capex
        eval_cand = e.quote_evaluate(cand['settings'])
        self.assertAlmostEqual(eval_cand['result']['finance']['capex_eur'], cand['capex_eur'], places=4)

        # 4. Fixed transition with existing storage retains sunk cost 0 € initial battery capex
        opts_fixed_existing = {
            'panel_maxima': {'south': 2, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_mode': 'fixed',
            'storage_cost_mode': 'existing'
        }
        res_fixed = e.solar_sizing(s, opts_fixed_existing)
        cand_fixed = res_fixed['recommended_candidate']
        self.assertAlmostEqual(cand_fixed['capex_eur'], 1800.0, places=3)
        self.assertEqual(cand_fixed['storage_cost_mode'], 'existing')
        self.assertEqual(cand_fixed['settings']['storage_cost_mode'], 'existing')

        eval_fixed = e.quote_evaluate(cand_fixed['settings'])
        self.assertAlmostEqual(eval_fixed['result']['finance']['capex_eur'], 1800.0, places=3)

        # Replacement cashflow in project year battery_replace_year is preserved
        flows = eval_fixed['result']['annual_cashflows']
        replace_yr = cand_fixed['settings']['battery_replace_year']
        rep_flow = next(c['replacement'] for c in flows if c['year'] == replace_yr)
        self.assertGreater(rep_flow, 0.0)

    def test_sensitivity_higher_installed_cost_admissible_capacities_valid_quote_variants(self):
        """Stress rejection checks only effective rates for admissible candidate capacities.
        When searched capacity is 5 kWh with effective rate 600 €/kWh, higher_installed_cost
        succeeds even if unused extrapolation slope 2640 > 2000 €/kWh."""
        s = e.defaults()
        s['battery_module_kwh'] = 5.0
        s['battery_kwh'] = 5.0
        s['quote_vat_included'] = True

        opts = {
            'panel_maxima': {'south': 2, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_kwh': 5.0,
            'battery_max_kwh': 5.0,
            'quote_10kwh_eur': 5000.0,
            'quote_15kwh_eur': 16000.0,
        }

        # Direct sizing succeeds
        res = e.solar_sizing(s, opts)
        self.assertIn(res['status'], ('SCENARIO_PENDING', 'SUCCESS'))

        # Sensitivity analysis higher_installed_cost succeeds without overbroad rejection
        sens = e.solar_sizing_sensitivity(s, opts)
        h_cost = sens['scenarios']['higher_installed_cost']
        self.assertEqual(h_cost['status'], 'SUCCESS')
        self.assertIsNone(h_cost.get('unavailable_reason'))
        self.assertIn('financial', h_cost['roles'])

        # Previous genuinely invalid quote (19000/24000 at 10kWh searched) remains cleanly UNAVAILABLE
        s_invalid = {
            'battery_module_kwh': 5.0,
            'battery_kwh': 10.0,
            'battery_mixed_sizes': False,
            'quote_vat_included': False,
            'inverter_kw': 6.0
        }
        opt_invalid = {
            'panel_maxima': {'south': 2, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_kwh': 10.0,
            'battery_max_kwh': 10.0,
            'battery_mode': 'joint'
        }
        quote_invalid = {
            'quote_10kwh_eur': 19000.0,
            'quote_15kwh_eur': 24000.0
        }
        sens_inv = e.solar_sizing_sensitivity(s_invalid, opt_invalid, quote_invalid)
        h_cost_inv = sens_inv['scenarios']['higher_installed_cost']
        self.assertEqual(h_cost_inv['status'], 'UNAVAILABLE')
        self.assertIn('battery_cost_kwh', h_cost_inv['unavailable_reason'])

    def test_battery_benchmark_zero_and_overrides_parity_across_modes_and_bases(self):
        """Parity across (a) 0 benchmark with no overrides, (b) 0 benchmark with module overrides/extras,
        (c) normal 550 benchmark, and (d) existing storage mode, across both financial bases."""
        cases = [
            ('case_a_zero_no_override', {'battery_cost_kwh': 0.0}, {}, 1800.0),
            ('case_b_zero_with_overrides', {
                'battery_cost_kwh': 0.0,
                'battery_module_cost_override': 1000.0,
                'battery_module_extra_cost': 150.0
            }, {}, 2950.0),
            ('case_c_normal_550', {'battery_cost_kwh': 550.0}, {}, 4550.0),
            ('case_d_existing_mode', {
                'storage_cost_mode': 'existing'
            }, {'battery_mode': 'fixed', 'storage_cost_mode': 'existing'}, 1800.0)
        ]

        for label, s_upd, opt_upd, exp_capex in cases:
            for basis in ['project', 'after_financing']:
                s = e.defaults()
                s['battery_module_kwh'] = 5.0
                s['battery_kwh'] = 5.0
                s.update(s_upd)

                opts = {
                    'panel_maxima': {'south': 2, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
                    'battery_min_kwh': 5.0,
                    'battery_max_kwh': 5.0,
                    'battery_mode': 'joint',
                    'financial_basis': basis
                }
                opts.update(opt_upd)

                res = e.solar_sizing(s, opts)
                cand = res['recommended_candidate']
                self.assertAlmostEqual(cand['capex_eur'], exp_capex, places=3, msg=f'{label} capex mismatch')

                if label == 'case_a_zero_no_override':
                    self.assertEqual(cand['settings']['battery_cost_kwh'], 0.0)
                elif label == 'case_b_zero_with_overrides':
                    self.assertEqual(cand['settings']['battery_cost_kwh'], 0.0)
                    self.assertEqual(cand['settings']['battery_module_cost_override'], 1000.0)
                    self.assertEqual(cand['settings']['battery_module_extra_cost'], 150.0)
                elif label == 'case_c_normal_550':
                    self.assertEqual(cand['settings']['battery_cost_kwh'], 550.0)
                elif label == 'case_d_existing_mode':
                    self.assertEqual(cand['storage_cost_mode'], 'existing')
                    self.assertEqual(cand['settings']['storage_cost_mode'], 'existing')
                    flows = e.evaluate(cand['settings'])['annual_cashflows']
                    rep_flow = next(c['replacement'] for c in flows if c['year'] == cand['settings']['battery_replace_year'])
                    self.assertGreater(rep_flow, 0.0)

                # Parity with independent evaluate
                eval_res = e.evaluate(cand['settings'])
                self.assertAlmostEqual(cand['capex_eur'], eval_res['finance']['capex_eur'], places=5)
                if basis == 'project':
                    self.assertAlmostEqual(cand['npv_median'], eval_res['finance']['pv_incremental']['npv']['median'], places=5)
                else:
                    self.assertAlmostEqual(cand['npv_median'], eval_res['finance']['solar_equity']['npv']['median'], places=5)

                # Parity with exact base repricer
                repriced = e._find_repriced_base_candidate(cand, s, opts, basis=basis)
                self.assertAlmostEqual(repriced['capex_eur'], cand['capex_eur'], places=5)
                self.assertAlmostEqual(repriced['npv_median'], cand['npv_median'], places=5)


if __name__ == '__main__':
    unittest.main()



