"""Tests for Small Task D2: AC schedule/percentage propagation through PV/battery sizing and cached outputs.

Verifies:
1. Battery sizing Chart 4 current-configuration NPV cell equals REAL evaluate(s)
   at the same requested percentage (0% and 25%), following the existing
   tests/test_battery_sizing.py test_financial_parity_with_evaluate pattern.
   Panel counts and current battery align to the grid.
2. Solar sizing evaluated positive-PV candidate's NPV, energy metrics (autarky, self-consumption),
   and capex agree with independently evaluate(candidate settings) at selected pct.
3. Spying on actual feasibility.engine.load_profiles calls without substituting fake physical
   outputs proves ac_heating_additional_demand_pct and canonical ac_heating_windows propagate
   to all load modeling calls. Tests fail if selected pct is dropped.
4. Physical total load per raw realization increments by exactly 0.25 * baseline AC heating
   electricity budget for all 19 weather years (strict conservation per realization, not summing medians).
5. Nested structured schedule survives freeze/thaw roundtrip; changing 0 -> 25 -> 0 without cache clear
   returns correct distinct and restored values; exact value identity of copied window objects produces
   identical cache signature; changing minute window changes simulation context and cache keys.
6. Sizing consumes same source scaled load as evaluate, not linearly scaled NPV, costs, or optimum size.
   Capex outlay and historical gas bills remain invariant; no double multiplication or invented gas savings;
   financial parity holds without assuming artificial monotonicity on optimal sizes.
"""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
sys.path.insert(0, str(PROJECT_ROOT / 'feasibility' / 'vendor'))
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(HERE))

import test_support  # noqa: F401
from test_support import IsolatedDefaultsTestCase

import feasibility.engine as engine
from feasibility.engine import (
    battery_sizing,
    defaults,
    evaluate,
    solar_sizing,
    validate,
)
from feasibility.schedule import (
    freeze,
    thaw_settings,
)


class AcHeatingSizingIntegrationTests(IsolatedDefaultsTestCase):
    """Integration test suite for Small Task D2 sizing propagation and cache integrity."""

    def setUp(self):
        super().setUp()
        # Non-trivial canonical minute schedule: 06:15-08:30 (375..510) and 17:00-21:30 (1020..1290)
        self.nontrivial_windows = [
            {'id': 'morning', 'start_minute': 375, 'end_minute': 510},
            {'id': 'evening', 'start_minute': 1020, 'end_minute': 1290},
        ]

    def _make_base_settings(self, horizon_years=1, load_scenario='electrified', pct=0, panels=12, battery_kwh=5.0, windows=None):
        s = defaults()
        s['horizon_years'] = horizon_years
        s['load_scenario'] = load_scenario
        s['ac_heating_windows'] = copy.deepcopy(windows if windows is not None else self.nontrivial_windows)
        s['ac_heating_additional_demand_pct'] = pct
        s['planning_heat_pump'] = False
        s['south_panels'] = panels
        s['west_panels'] = 0
        s['east_panels'] = 0
        s['dormer_panels'] = 0
        s['flat_panels'] = 0
        s['secondary_south_panels'] = 0
        s['battery_kwh'] = battery_kwh
        s['battery_modules'] = 0 if battery_kwh == 0 else 1
        # Remove legacy hour fields so explicit canonical windows take clean precedence
        for k in ['heating_on_hour', 'heating_off_hour', 'heating_evening_on_hour', 'heating_evening_off_hour']:
            s.pop(k, None)
        return validate(s)

    def test_1_battery_sizing_financial_parity_chart4_with_real_evaluate_0_and_25(self):
        """Chart 4 current-config NPV cell equals REAL evaluate(s) at 0% and 25%; capex invariant; distinct NPVs."""
        s0 = self._make_base_settings(horizon_years=1, pct=0, panels=12, battery_kwh=5.0)
        s25 = self._make_base_settings(horizon_years=1, pct=25, panels=12, battery_kwh=5.0)

        # 1. Battery sizing runs with tightly bounded options
        b_opt = {'step': 2, 'max_capacity': 10}
        res0 = battery_sizing(s0, b_opt)
        res25 = battery_sizing(s25, b_opt)

        heatmap0 = res0['chart4_npv_heatmap']
        heatmap25 = res25['chart4_npv_heatmap']

        cur_pv0 = heatmap0['current_configuration']['pv_kwp']
        cur_bat0 = heatmap0['current_configuration']['battery_kwh']
        cur_pv25 = heatmap25['current_configuration']['pv_kwp']
        cur_bat25 = heatmap25['current_configuration']['battery_kwh']

        expected_kwp = round(12 * float(s0['panel_watts']) / 1000.0, 3)
        self.assertAlmostEqual(cur_pv0, expected_kwp, places=2)
        self.assertAlmostEqual(cur_bat0, 5.0, places=2)
        self.assertAlmostEqual(cur_pv25, expected_kwp, places=2)
        self.assertAlmostEqual(cur_bat25, 5.0, places=2)

        cell0 = next(
            c for c in heatmap0['cells']
            if abs(c['pv_kwp'] - cur_pv0) < 1e-4 and abs(c['battery_kwh'] - cur_bat0) < 1e-4
        )
        cell25 = next(
            c for c in heatmap25['cells']
            if abs(c['pv_kwp'] - cur_pv25) < 1e-4 and abs(c['battery_kwh'] - cur_bat25) < 1e-4
        )

        # 2. Independent real evaluate calls
        eval0 = evaluate(s0)
        eval25 = evaluate(s25)

        expected_npv0 = eval0['finance']['pv_incremental']['npv']['median']
        expected_npv25 = eval25['finance']['pv_incremental']['npv']['median']

        # Exact parity with evaluate(s)
        self.assertAlmostEqual(cell0['npv_median'], expected_npv0, places=6)
        self.assertAlmostEqual(cell25['npv_median'], expected_npv25, places=6)

        # Distinct NPVs between 0% and 25% (propagation affects dispatch economics)
        self.assertNotEqual(cell0['npv_median'], cell25['npv_median'])
        self.assertNotEqual(expected_npv0, expected_npv25)

        # Equipment cost (capex) identical between 0% and 25%
        self.assertEqual(cell0['capex_eur'], cell25['capex_eur'])
        self.assertEqual(cell0['capex_eur'], eval0['finance']['capex_eur'])
        self.assertEqual(cell25['capex_eur'], eval25['finance']['capex_eur'])

        # Parity would fail if pct were dropped to 0 in 25% sizing
        self.assertNotEqual(cell25['npv_median'], expected_npv0)

    def test_2_solar_sizing_positive_pv_candidate_parity_with_independent_evaluate(self):
        """Solar candidate NPV/energy/cost agrees with independent evaluate(candidate settings) at selected pct."""
        s25 = self._make_base_settings(horizon_years=1, pct=25, panels=4, battery_kwh=0.0)
        sol_opt = {
            'evaluation_budget': 50,
            'panel_maxima': {'south': 4, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_mode': 'fixed',
        }

        sol_res = solar_sizing(s25, sol_opt)
        evaluated_cands = sol_res['_evaluated_candidates']

        # Choose positive-PV candidate (2 panels on south roof)
        cand = next(c for c in evaluated_cands if c['panel_count'] == 2)
        cand_settings = cand['settings']

        # Verify candidate settings retained the AC heating context
        self.assertEqual(cand_settings['ac_heating_additional_demand_pct'], 25)
        self.assertEqual(cand_settings['ac_heating_windows'], s25['ac_heating_windows'])

        # Independent evaluate call on actual reported candidate settings
        eval_cand = evaluate(cand_settings)

        # NPV parity
        self.assertAlmostEqual(
            cand['npv']['median'],
            eval_cand['finance']['pv_incremental']['npv']['median'],
            places=6,
            msg="Candidate median NPV does not match evaluate(candidate settings)",
        )
        # Cost parity
        self.assertAlmostEqual(
            cand['capex_eur'],
            eval_cand['finance']['capex_eur'],
            places=6,
            msg="Candidate capex does not match evaluate(candidate settings)",
        )
        # Energy autarky parity
        self.assertAlmostEqual(
            cand['autarky']['median'],
            eval_cand['metrics']['autarky']['median'],
            places=6,
            msg="Candidate autarky does not match evaluate(candidate settings)",
        )
        # Energy self-consumption parity
        self.assertAlmostEqual(
            cand['self_consumption']['median'],
            eval_cand['metrics']['self_consumption']['median'],
            places=6,
            msg="Candidate self-consumption does not match evaluate(candidate settings)",
        )
        # Sizing kWp layout parity
        self.assertAlmostEqual(cand['kwp'], eval_cand['layout']['kwp'], places=6)

        # Verify parity also holds for pct=0 and that candidate NPV at pct=25 != candidate NPV at pct=0
        s0 = self._make_base_settings(horizon_years=1, pct=0, panels=4, battery_kwh=0.0)
        sol_res0 = solar_sizing(s0, sol_opt)
        cand0 = next(c for c in sol_res0['_evaluated_candidates'] if c['panel_count'] == 2)
        eval_cand0 = evaluate(cand0['settings'])

        self.assertAlmostEqual(cand0['npv']['median'], eval_cand0['finance']['pv_incremental']['npv']['median'], places=6)
        self.assertNotEqual(cand['npv']['median'], cand0['npv']['median'])

    def test_3_spying_physical_load_profiles_raw_realizations_and_no_pct_dropped(self):
        """Instrument load_profiles to verify pct/windows propagation and exact raw realization load conservation."""
        s0 = self._make_base_settings(horizon_years=1, pct=0, panels=12, battery_kwh=5.0)
        s25 = self._make_base_settings(horizon_years=1, pct=25, panels=12, battery_kwh=5.0)

        # Baseline AC electricity from real load profiles
        load0, _, enduses0 = engine.load_profiles(s0)
        base_ac_elec = enduses0['ac_heating_baseline_electricity_kwh']
        expected_increment = 0.25 * base_ac_elec

        # Clear LRU caches to ensure fresh evaluation is captured by the spy
        engine._battery_sizing_cached.cache_clear()
        engine._solar_sizing_cached.cache_clear()

        # 1. Spy during battery_sizing: verify no call drops the requested pct or windows
        with patch.object(engine, 'load_profiles', wraps=engine.load_profiles) as spy_bat_load:
            res_b25 = battery_sizing(s25, {'step': 2, 'max_capacity': 10})
            self.assertGreater(spy_bat_load.call_count, 0)
            for call_item in spy_bat_load.call_args_list:
                s_call = call_item.args[0]
                self.assertEqual(
                    s_call.get('ac_heating_additional_demand_pct'),
                    25,
                    "battery_sizing dropped ac_heating_additional_demand_pct in load_profiles call",
                )
                self.assertEqual(
                    s_call.get('ac_heating_windows'),
                    s25['ac_heating_windows'],
                    "battery_sizing dropped ac_heating_windows in load_profiles call",
                )

        # 2. Spy during solar_sizing: verify no call drops the requested pct or windows
        sol_opt = {
            'evaluation_budget': 50,
            'panel_maxima': {'south': 2, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_mode': 'fixed',
        }
        with patch.object(engine, 'load_profiles', wraps=engine.load_profiles) as spy_sol_load:
            res_sol25 = solar_sizing(s25, sol_opt)
            self.assertGreater(spy_sol_load.call_count, 0)
            for call_item in spy_sol_load.call_args_list:
                s_call = call_item.args[0]
                self.assertEqual(
                    s_call.get('ac_heating_additional_demand_pct'),
                    25,
                    "solar_sizing dropped ac_heating_additional_demand_pct in load_profiles call",
                )
                self.assertEqual(
                    s_call.get('ac_heating_windows'),
                    s25['ac_heating_windows'],
                    "solar_sizing dropped ac_heating_windows in load_profiles call",
                )

        # 3. Compare physical total load per raw realization (never sum medians)
        load25, _, enduses25 = engine.load_profiles(s25)
        raw_increments = load25.sum(axis=1) - load0.sum(axis=1)

        d = engine.data()
        h0 = engine._allocate_space_heating(
            s0, d['temperature'], d['month'], d['hour'], d['valid'], engine._get_civil_hours(d), d['years']
        )
        base_realization_heating = h0.sum(axis=1)

        self.assertEqual(len(raw_increments), engine.weather_period().count)
        for w in range(len(raw_increments)):
            self.assertAlmostEqual(
                float(raw_increments[w]),
                0.25 * float(base_realization_heating[w]),
                places=6,
                msg=f"Realization {w} raw load increment did not match 0.25 * baseline realization AC heating electricity",
            )
        # Ensemble increment equals configured mean increment
        self.assertAlmostEqual(float(raw_increments.mean()), expected_increment, places=6)
        # Weather variability across realizations reflects cold-year variation
        self.assertGreater(float(np.ptp(raw_increments)), 10.0)

    def test_4_schedule_freeze_thaw_cache_key_and_restoration_0_25_0(self):
        """Nested schedule survives freeze/thaw, 0->25->0 restored identically, window change alters cache."""
        # 1. Freeze / Thaw roundtrip
        frozen = freeze(self.nontrivial_windows)
        self.assertIsInstance(frozen, tuple)
        thawed_dict = thaw_settings({'ac_heating_windows': frozen})
        self.assertEqual(thawed_dict['ac_heating_windows'], self.nontrivial_windows)

        # Exact value identity of deep-copied window objects yields identical freeze signature
        windows_copy = copy.deepcopy(self.nontrivial_windows)
        self.assertEqual(freeze(windows_copy), freeze(self.nontrivial_windows))

        # 2. Changing 0 -> 25 -> 0 without cache clear returns distinct and then restored values
        s0 = self._make_base_settings(horizon_years=1, pct=0, panels=12, battery_kwh=5.0)
        s25 = self._make_base_settings(horizon_years=1, pct=25, panels=12, battery_kwh=5.0)
        s0_restored = self._make_base_settings(horizon_years=1, pct=0, panels=12, battery_kwh=5.0)

        b_opt = {'step': 2, 'max_capacity': 10}
        b0 = battery_sizing(s0, b_opt)
        b25 = battery_sizing(s25, b_opt)
        b0_rest = battery_sizing(s0_restored, b_opt)

        npv_b0 = b0['chart4_npv_heatmap']['cells'][0]['npv_median']
        npv_b25 = b25['chart4_npv_heatmap']['cells'][0]['npv_median']
        npv_b0_rest = b0_rest['chart4_npv_heatmap']['cells'][0]['npv_median']

        self.assertNotEqual(npv_b0, npv_b25)
        self.assertEqual(npv_b0, npv_b0_rest)

        # Same 0 -> 25 -> 0 verification in solar sizing
        sol_opt = {
            'evaluation_budget': 50,
            'panel_maxima': {'south': 4, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_mode': 'fixed',
        }
        sol0 = solar_sizing(s0, sol_opt)
        sol25 = solar_sizing(s25, sol_opt)
        sol0_rest = solar_sizing(s0_restored, sol_opt)

        cand_npv0 = next(c for c in sol0['_evaluated_candidates'] if c['panel_count'] == 2)['npv']['median']
        cand_npv25 = next(c for c in sol25['_evaluated_candidates'] if c['panel_count'] == 2)['npv']['median']
        cand_npv0_rest = next(c for c in sol0_rest['_evaluated_candidates'] if c['panel_count'] == 2)['npv']['median']

        self.assertNotEqual(cand_npv0, cand_npv25)
        self.assertEqual(cand_npv0, cand_npv0_rest)

        # 3. Changing minute window changes cache signature and simulation context
        windows_alt = [
            {'id': 'morning', 'start_minute': 300, 'end_minute': 600},
            {'id': 'evening', 'start_minute': 1020, 'end_minute': 1290},
        ]
        s_alt = self._make_base_settings(horizon_years=1, pct=25, panels=12, battery_kwh=5.0, windows=windows_alt)

        key_s25 = tuple(sorted((k, freeze(v)) for k, v in s25.items()))
        key_salt = tuple(sorted((k, freeze(v)) for k, v in s_alt.items()))
        self.assertNotEqual(key_s25, key_salt)

        b_alt = battery_sizing(s_alt, b_opt)
        npv_b_alt = b_alt['chart4_npv_heatmap']['cells'][0]['npv_median']
        self.assertNotEqual(npv_b25, npv_b_alt)

    def test_5_sizing_consumes_source_scaled_load_not_linear_scaled_npv_costs_or_optimum(self):
        """NPV and operating savings reflect dispatch non-linearities; capex and gas bills remain invariant."""
        s0 = self._make_base_settings(horizon_years=1, pct=0, panels=12, battery_kwh=5.0)
        s25 = self._make_base_settings(horizon_years=1, pct=25, panels=12, battery_kwh=5.0)

        eval0 = evaluate(s0)
        eval25 = evaluate(s25)

        # 1. Gas bills invariant: historical gas savings not invented
        self.assertEqual(
            eval0['finance']['historical_annual_gas_bill_eur'],
            eval25['finance']['historical_annual_gas_bill_eur'],
        )
        self.assertEqual(
            eval0['finance']['historical_annual_electric_bill_eur'],
            eval25['finance']['historical_annual_electric_bill_eur'],
        )
        self.assertGreater(eval0['finance']['historical_annual_gas_bill_eur'], 0.0)

        # 2. Capex strictly identical
        self.assertEqual(eval0['finance']['capex_eur'], eval25['finance']['capex_eur'])

        # 3. No double multiplication: requested AC heating electricity is exactly 1.25x baseline
        base_ac = eval0['enduses']['ac_heating_baseline_electricity_kwh']
        req_ac_0 = eval0['enduses']['ac_heating_requested_electricity_kwh']
        req_ac_25 = eval25['enduses']['ac_heating_requested_electricity_kwh']
        self.assertAlmostEqual(req_ac_0, base_ac, places=8)
        self.assertAlmostEqual(req_ac_25, base_ac * 1.25, places=8)
        self.assertNotAlmostEqual(req_ac_25, base_ac * 1.25 * 1.25, places=8)

        # 4. Sizing NPV is NOT linearly scaled (1.25 * NPV_0 or 0.75 * NPV_0)
        b_opt = {'step': 2, 'max_capacity': 10}
        b0 = battery_sizing(s0, b_opt)
        b25 = battery_sizing(s25, b_opt)

        cell0 = b0['chart4_npv_heatmap']['cells'][0]['npv_median']
        cell25 = b25['chart4_npv_heatmap']['cells'][0]['npv_median']

        self.assertNotEqual(cell25, cell0 * 1.25)
        self.assertNotEqual(cell25, cell0 * 0.75)
        self.assertNotEqual(cell25, cell0)

    def test_6_failure_modes_if_selected_pct_dropped(self):
        """Parity checks fail if either sizing path drops or reverts the selected percentage."""
        s0 = self._make_base_settings(horizon_years=1, pct=0, panels=12, battery_kwh=5.0)
        s25 = self._make_base_settings(horizon_years=1, pct=25, panels=12, battery_kwh=5.0)

        eval0 = evaluate(s0)
        eval25 = evaluate(s25)

        # Confirm evaluate NPVs are distinct across percentages
        eval_npv0 = eval0['finance']['pv_incremental']['npv']['median']
        eval_npv25 = eval25['finance']['pv_incremental']['npv']['median']
        self.assertNotEqual(eval_npv0, eval_npv25)

        # Battery sizing for s25
        b_res25 = battery_sizing(s25, {'step': 2, 'max_capacity': 10})
        cur_pv = b_res25['chart4_npv_heatmap']['current_configuration']['pv_kwp']
        cur_bat = b_res25['chart4_npv_heatmap']['current_configuration']['battery_kwh']
        cell25 = next(
            c for c in b_res25['chart4_npv_heatmap']['cells']
            if abs(c['pv_kwp'] - cur_pv) < 1e-4 and abs(c['battery_kwh'] - cur_bat) < 1e-4
        )

        # If battery sizing had dropped pct to 0, cell25['npv_median'] would equal eval_npv0
        # Assert that it matches eval_npv25 and does NOT match eval_npv0
        self.assertAlmostEqual(cell25['npv_median'], eval_npv25, places=6)
        self.assertNotEqual(cell25['npv_median'], eval_npv0)

        # Solar sizing for s25
        sol_opt = {
            'evaluation_budget': 50,
            'panel_maxima': {'south': 4, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_mode': 'fixed',
        }
        sol_res25 = solar_sizing(s25, sol_opt)
        cand25 = next(c for c in sol_res25['_evaluated_candidates'] if c['panel_count'] == 2)

        # Solar sizing for s0
        sol_res0 = solar_sizing(s0, sol_opt)
        cand0 = next(c for c in sol_res0['_evaluated_candidates'] if c['panel_count'] == 2)

        # If solar sizing had dropped pct to 0, cand25 NPV would equal cand0 NPV
        self.assertNotEqual(cand25['npv']['median'], cand0['npv']['median'])


if __name__ == '__main__':
    unittest.main()
