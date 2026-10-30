"""Tests for Small Task D1: Annual/financial AC percentage propagation and sensitivity hygiene.

Verifies:
1. REAL evaluate and raw _energy_cache annual intervals consume same scaled heating once
   and historical gas savings stay based on historical gas.
2. Modest valid horizon (1 or 2 years); raw annual electrical demand increment per realization
   equals exactly 0.25 * independently allocated annual heating (EV off or controlled).
3. Enduses reconcile; historical_annual_gas_bill_eur and historical_annual_electric_bill_eur unchanged;
   actual simulated dispatch imports/bills recalculate, not 1.25 whole-house.
4. Changing 0 -> 25 -> 0 without manual cache_clear restores identical results.
5. Structured ac_heating_windows retained as context, not swept as numeric/range choices.
6. Sensitivity hygiene:
   - Planning heat pump (air-to-water) excludes ac_heating_additional_demand_pct with other AC controls.
   - ac_heating_additional_demand_pct record provides explicit finite illustrative choices [0, 25, 50]
     with required wording: "illustrative useful-heat sensitivity scenarios, not uncertainty range or temperature/comfort prediction".
   - No None or infinite cases are ever generated. Validated pct has no arbitrary upper cap (bounds [0, None]).
   - AC sensitivity cases preserve schedule and SCOP; other sensitivity cases preserve selected pct.
   - Real evaluate 0/25 runs cleanly.
"""
from heating_oracle import heating_oracle
import copy
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, MagicMock

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
sys.path.insert(0, str(PROJECT_ROOT / 'feasibility' / 'vendor'))
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(HERE))

import test_support  # noqa: F401
from test_support import IsolatedDefaultsTestCase

import numpy as np
import feasibility.assumptions as assumptions
import feasibility.engine as engine
from feasibility.engine import (
    defaults,
    evaluate,
    sensitivity,
    validate,
    _energy_cache,
    ENERGY_KEYS,
    price_fingerprint,
    freeze,
)


class AcHeatingAnnualIntegrationTests(IsolatedDefaultsTestCase):
    """Test suite for Small Task D1 annual/financial propagation and sensitivity hygiene."""

    def setUp(self):
        super().setUp()
        # Non-trivial minute schedule: 06:15-08:30 (375..510) and 17:00-21:30 (1020..1290)
        self.nontrivial_windows = [
            {'start_minute': 375, 'end_minute': 510},
            {'start_minute': 1020, 'end_minute': 1290},
        ]

    def _make_base_settings(self, horizon_years=1, load_scenario='electrified', pct=0):
        s = defaults()
        s['horizon_years'] = horizon_years
        s['load_scenario'] = load_scenario
        s['ac_heating_windows'] = copy.deepcopy(self.nontrivial_windows)
        s['ac_heating_additional_demand_pct'] = pct
        # Remove legacy hour fields so explicit canonical windows take clean precedence
        for k in ['heating_on_hour', 'heating_off_hour', 'heating_evening_on_hour', 'heating_evening_off_hour']:
            s.pop(k, None)
        return validate(s)

    def test_1_real_evaluate_and_raw_energy_cache_annual_propagation_0_vs_25(self):
        """REAL evaluate and _energy_cache annual intervals scale heating once; historical gas invariant."""
        s0 = self._make_base_settings(horizon_years=1, load_scenario='electrified', pct=0)
        s25 = self._make_base_settings(horizon_years=1, load_scenario='electrified', pct=25)

        # 1. Run REAL evaluate for 0% and 25%
        r0 = evaluate(s0)
        r25 = evaluate(s25)

        # 2. Query raw _energy_cache for identical settings
        key0 = tuple((k, freeze(s0[k])) for k in ENERGY_KEYS)
        fp0 = price_fingerprint(s0)
        out0, enduses0 = _energy_cache(key0, fp0)

        key25 = tuple((k, freeze(s25[k])) for k in ENERGY_KEYS)
        fp25 = price_fingerprint(s25)
        out25, enduses25 = _energy_cache(key25, fp25)

        # 3. Check baseline and requested AC heating budget
        baseline_ac_elec = enduses0['ac_heating_baseline_electricity_kwh']
        requested_ac_elec_0 = enduses0['ac_heating_requested_electricity_kwh']
        requested_ac_elec_25 = enduses25['ac_heating_requested_electricity_kwh']

        self.assertAlmostEqual(requested_ac_elec_0, baseline_ac_elec, places=8)
        self.assertAlmostEqual(requested_ac_elec_25, baseline_ac_elec * 1.25, places=8)
        self.assertAlmostEqual(enduses25['ac_heating_extra_electricity_kwh'], baseline_ac_elec * 0.25, places=8)

        # 4. Raw annual electrical demand increment per realization equals .25*baseline AC heating annual budget
        # out shape: (horizon_years, n_weather_years, n_fields), field 0 is load_kwh
        n_weather = out0.shape[1]
        self.assertEqual(n_weather, engine.weather_period().count)
        load_year1_0 = out0[0, :, 0]
        load_year1_25 = out25[0, :, 0]
        raw_annual_increment = load_year1_25 - load_year1_0

        mapped = engine._forecast_mapped_series(s0)
        oracle = heating_oracle(s0, mapped['mapped_temp'], mapped['target_months'], mapped['target_std_hours'],
                                np.ones_like(mapped['mapped_temp'], bool), mapped['target_civil_hours'], engine.GAS_MONTHLY)
        expected_by_weather = 0.25 * oracle.sum(axis=1)
        self.assertAlmostEqual(float(raw_annual_increment.mean()), 0.25 * baseline_ac_elec, places=7)
        for w in range(n_weather):
            self.assertAlmostEqual(
                float(raw_annual_increment[w]),
                float(expected_by_weather[w]),
                places=7,
                msg=f"Realization {w} raw annual load increment did not equal 0.25 * baseline AC heating budget",
            )
        # Weather dependence must remain visible, with each increment checked against the oracle.
        self.assertGreater(float(np.ptp(raw_annual_increment)), 1e-9)

        # 5. Enduses reconcile
        self.assertAlmostEqual(enduses0['heating_kwh'], baseline_ac_elec, places=8)
        self.assertAlmostEqual(enduses25['heating_kwh'], baseline_ac_elec * 1.25, places=8)
        # Non-heating enduses strictly invariant
        self.assertEqual(enduses25['heating_smc'], enduses0['heating_smc'])
        self.assertEqual(enduses25['dhw_smc'], enduses0['dhw_smc'])
        self.assertEqual(enduses25['cooking_smc'], enduses0['cooking_smc'])
        self.assertEqual(enduses25['dhw_kwh'], enduses0['dhw_kwh'])
        self.assertEqual(enduses25['cooking_kwh'], enduses0['cooking_kwh'])

        # 6. Historical gas savings stay based on historical gas; bills unchanged
        hist_gas_0 = r0['finance']['historical_annual_gas_bill_eur']
        hist_gas_25 = r25['finance']['historical_annual_gas_bill_eur']
        hist_elec_0 = r0['finance']['historical_annual_electric_bill_eur']
        hist_elec_25 = r25['finance']['historical_annual_electric_bill_eur']

        self.assertEqual(hist_gas_25, hist_gas_0)
        self.assertEqual(hist_elec_25, hist_elec_0)
        self.assertGreater(hist_gas_0, 0.0)
        self.assertGreater(hist_elec_0, 0.0)

        # Year 0 capex outlay is identical between 0% and 25%
        for w in range(n_weather):
            self.assertEqual(
                r0['finance']['cashflows_by_weather']['whole_package'][w][0],
                r25['finance']['cashflows_by_weather']['whole_package'][w][0],
            )
            # Year 1 operating cashflow recalculates due to higher dispatch electricity cost at 25%
            # (not linear-scaled by 1.25)
            cf0_y1 = r0['finance']['cashflows_by_weather']['whole_package'][w][1]
            cf25_y1 = r25['finance']['cashflows_by_weather']['whole_package'][w][1]
            self.assertLess(cf25_y1, cf0_y1)
            self.assertNotEqual(cf25_y1, cf0_y1 * 1.25)
            self.assertNotEqual(cf25_y1, cf0_y1 * 0.75)

        # 7. Actual simulated dispatch imports/bills recalculate, not 1.25 whole-house
        median_load_0 = r0['energy']['load_kwh']['median']
        median_load_25 = r25['energy']['load_kwh']['median']
        expected_median_increment = float(np.median(load_year1_0 + expected_by_weather) - np.median(load_year1_0))
        self.assertAlmostEqual(median_load_25 - median_load_0, expected_median_increment, places=7)
        # Verify it is NOT 1.25 * whole-house load
        self.assertLess(median_load_25, 1.20 * median_load_0)

        # Grid imports increase according to simulated dispatch
        median_import_0 = r0['energy']['grid_import_kwh']['median']
        median_import_25 = r25['energy']['grid_import_kwh']['median']
        self.assertGreater(median_import_25, median_import_0)
        # Bound each weather record before taking a median; medians are not additive.
        import_increment = out25[0, :, 5] - out0[0, :, 5]
        np.testing.assert_array_less(import_increment, expected_by_weather + 1e-9)
        upper_median = float(np.median(out0[0, :, 5] + expected_by_weather))
        self.assertLess(median_import_25, upper_median + 1e-9)

        # 8. Change 0 -> 25 -> 0 without manual cache_clear, verify restored results
        r0_restored = evaluate(s0)
        self.assertEqual(r0_restored['energy']['load_kwh']['median'], r0['energy']['load_kwh']['median'])
        self.assertEqual(
            r0_restored['energy']['grid_import_kwh']['median'],
            r0['energy']['grid_import_kwh']['median'],
        )
        self.assertEqual(
            r0_restored['finance']['historical_annual_gas_bill_eur'],
            r0['finance']['historical_annual_gas_bill_eur'],
        )

    def test_2_ev_scenario_controlled_and_raw_annual_increment(self):
        """In EV scenario, EV demand is controlled and raw annual increment matches 0.25*baseline."""
        s_ev0 = self._make_base_settings(horizon_years=1, load_scenario='ev', pct=0)
        s_ev25 = self._make_base_settings(horizon_years=1, load_scenario='ev', pct=25)

        r_ev0 = evaluate(s_ev0)
        r_ev25 = evaluate(s_ev25)

        key0 = tuple((k, freeze(s_ev0[k])) for k in ENERGY_KEYS)
        key25 = tuple((k, freeze(s_ev25[k])) for k in ENERGY_KEYS)
        out_ev0, enduses_ev0 = _energy_cache(key0, price_fingerprint(s_ev0))
        out_ev25, enduses_ev25 = _energy_cache(key25, price_fingerprint(s_ev25))

        # EV kwh unchanged
        self.assertEqual(enduses_ev25['ev_kwh'], enduses_ev0['ev_kwh'])
        self.assertGreater(enduses_ev0['ev_kwh'], 0.0)

        # Load difference per realization is exactly 0.25 * baseline AC heating electricity
        baseline_elec = enduses_ev0['ac_heating_baseline_electricity_kwh']
        load_diff = out_ev25[0, :, 0] - out_ev0[0, :, 0]
        mapped = engine._forecast_mapped_series(s_ev0)
        oracle = heating_oracle(s_ev0, mapped['mapped_temp'], mapped['target_months'], mapped['target_std_hours'],
                                np.ones_like(mapped['mapped_temp'], bool), mapped['target_civil_hours'], engine.GAS_MONTHLY)
        self.assertAlmostEqual(float(load_diff.mean()), 0.25 * baseline_elec, places=7)
        for w in range(len(load_diff)):
            self.assertAlmostEqual(float(load_diff[w]), 0.25 * float(oracle[w].sum()), places=7)

    def test_3_sensitivity_hygiene_air_to_water_hp_excludes_ac_pct(self):
        """When planning_heat_pump is True, ac_heating_additional_demand_pct yields no sensitivity rows or evaluate calls."""
        s_hp = self._make_base_settings(horizon_years=1, load_scenario='electrified', pct=0)
        s_hp['planning_heat_pump'] = True
        s_hp = validate(s_hp)

        # Use targeted records subset including AC pct, SCOP, and HP nominal kw
        all_recs = assumptions.records()
        target_recs = [
            r for r in all_recs
            if r['name'] in ['ac_heating_additional_demand_pct', 'ac_heating_windows', 'scop', 'hp_nominal_kw']
        ]

        evaluated_params = []
        orig_evaluate = engine.evaluate

        def spy_evaluate(settings=None, detail=False):
            # Record what parameter differed from base s_hp
            diffs = [k for k in settings if settings[k] != s_hp.get(k)]
            evaluated_params.append(diffs)
            return orig_evaluate(settings, detail=detail)

        with patch('feasibility.engine.records', return_value=target_recs), \
             patch('feasibility.engine.evaluate', side_effect=spy_evaluate):
            res = sensitivity(s_hp)

        # ac_heating_additional_demand_pct MUST NOT be present in sensitivity cases
        pct_cases = [c for c in res['cases'] if c.get('parameter') == 'ac_heating_additional_demand_pct']
        self.assertEqual(len(pct_cases), 0, "Found AC pct sensitivity cases when planning_heat_pump is enabled")

        # ac_heating_additional_demand_pct MUST NOT have been evaluated
        for diff_keys in evaluated_params:
            self.assertNotIn(
                'ac_heating_additional_demand_pct',
                diff_keys,
                "evaluate was called with changed ac_heating_additional_demand_pct under heat pump",
            )

    def test_4_sensitivity_hygiene_canonical_windows_not_swept(self):
        """Structured ac_heating_windows must be retained as context, never swept as choices."""
        s = self._make_base_settings(horizon_years=1, load_scenario='electrified', pct=25)

        all_recs = assumptions.records()
        target_recs = [
            r for r in all_recs
            if r['name'] in ['ac_heating_windows', 'ac_heating_additional_demand_pct']
        ]

        with patch('feasibility.engine.records', return_value=target_recs):
            res = sensitivity(s)

        win_cases = [c for c in res['cases'] if c.get('parameter') == 'ac_heating_windows']
        self.assertEqual(len(win_cases), 0, "ac_heating_windows was erroneously swept as a sensitivity parameter")

    def test_5_sensitivity_hygiene_metadata_and_no_none_or_infinite_cases(self):
        """ac_heating_additional_demand_pct metadata provides explicit finite choices; no None/infinite generated."""
        recs = assumptions.records()
        pct_rec = next((r for r in recs if r['name'] == 'ac_heating_additional_demand_pct'), None)
        self.assertIsNotNone(pct_rec)

        # Check explicit finite choices in record metadata
        self.assertIn('choices', pct_rec)
        self.assertEqual(pct_rec['choices'], [0, 25, 50])

        # Check required wording in range_interpretation
        expected_wording = "illustrative useful-heat sensitivity scenarios, not uncertainty range or temperature/comfort prediction"
        self.assertIn('range_interpretation', pct_rec)
        self.assertEqual(pct_rec['range_interpretation'], expected_wording)

        # Validate that validated pct has bounds [0, None] without arbitrary upper cap
        self.assertEqual(pct_rec['bounds'], [0, None])
        validated_high = validate({'ac_heating_additional_demand_pct': 150})
        self.assertEqual(validated_high['ac_heating_additional_demand_pct'], 150.0)

        # Verify sensitivity robustness when records have span [0, None] or choices with None/inf
        s = self._make_base_settings(horizon_years=1, load_scenario='electrified', pct=0)

        mock_recs = [
            {
                'name': 'ac_heating_additional_demand_pct',
                'value': 0,
                'choices': None,
                'plausible_range': [0, None],
            },
            {
                'name': 'summer_ac_multiplier',
                'value': 1.0,
                'choices': [1.2, None, math.inf],
                'plausible_range': None,
            },
        ]

        evaluated_calls = []

        def spy_eval(settings=None, detail=False):
            evaluated_calls.append(settings)
            # Return dummy structure sufficient for sensitivity row assembly
            return {
                'metrics': {},
                'finance': {'capex_eur': 5000.0},
            }

        with patch('feasibility.engine.records', return_value=mock_recs), \
             patch('feasibility.engine.evaluate', side_effect=spy_eval):
            res = sensitivity(s)

        # Verify no case generated has None or non-finite value
        for c in res['cases']:
            val = c.get('value')
            if isinstance(val, (int, float)):
                self.assertTrue(math.isfinite(val))
            elif isinstance(val, dict):
                pass
            else:
                self.assertIsNotNone(val)

        # ac_heating_additional_demand_pct safely derived finite values [0, 25, 50], so 25 and 50 evaluated
        pct_vals = [c['value'] for c in res['cases'] if c.get('parameter') == 'ac_heating_additional_demand_pct']
        self.assertEqual(pct_vals, [25, 50])

    def test_6_sensitivity_hygiene_context_preservation(self):
        """AC sensitivity cases preserve schedule and SCOP; other sensitivity cases preserve selected pct."""
        s = self._make_base_settings(horizon_years=1, load_scenario='electrified', pct=25)
        s['scop'] = 4.2
        s = validate(s)

        all_recs = assumptions.records()
        target_recs = [
            r for r in all_recs
            if r['name'] in ['ac_heating_additional_demand_pct', 'scop']
        ]

        captured_eval_settings = []

        def spy_evaluate(settings=None, detail=False):
            captured_eval_settings.append(copy.deepcopy(settings))
            return {
                'metrics': {'npv': {'median': 1000.0}},
                'finance': {'capex_eur': 6000.0},
            }

        with patch('feasibility.engine.records', return_value=target_recs), \
             patch('feasibility.engine.evaluate', side_effect=spy_evaluate):
            res = sensitivity(s)

        # Filter the calls made for ac_heating_additional_demand_pct
        ac_pct_evals = [st for st in captured_eval_settings if st.get('ac_heating_additional_demand_pct') != 25]
        self.assertGreater(len(ac_pct_evals), 0)
        for st in ac_pct_evals:
            # Preserves schedule windows and SCOP exactly
            self.assertEqual(st['ac_heating_windows'], s['ac_heating_windows'])
            self.assertEqual(st['scop'], 4.2)

        # Filter the calls made for other parameters (e.g. scop)
        scop_evals = [st for st in captured_eval_settings if st.get('scop') != 4.2]
        self.assertGreater(len(scop_evals), 0)
        for st in scop_evals:
            # Preserves selected ac_heating_additional_demand_pct = 25
            self.assertEqual(st['ac_heating_additional_demand_pct'], 25)

    def test_7_targeted_real_sensitivity_evaluate_0_and_25(self):
        """Targeted real evaluate runs for sensitivity cases 0 and 25 without errors."""
        s0 = self._make_base_settings(horizon_years=1, load_scenario='electrified', pct=0)

        all_recs = assumptions.records()
        target_recs = [
            r for r in all_recs
            if r['name'] == 'ac_heating_additional_demand_pct'
        ]

        with patch('feasibility.engine.records', return_value=target_recs):
            res = sensitivity(s0)

        pct_cases = [c for c in res['cases'] if c.get('parameter') == 'ac_heating_additional_demand_pct']
        # For base pct=0, choices [0, 25, 50] yield cases 25 and 50
        self.assertEqual(len(pct_cases), 2)
        values = [c['value'] for c in pct_cases]
        self.assertEqual(values, [25, 50])

        for c in pct_cases:
            self.assertIn('metrics', c)
            self.assertIn('capex_eur', c)
            self.assertNotIn('error', c)


if __name__ == '__main__':
    unittest.main()
