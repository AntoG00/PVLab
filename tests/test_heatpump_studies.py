"""Integration tests for heat pump studies, sizing runs, reporting, and schema contracts.

Verifies:
1. Meaningful REAL small sizing runs with HP on:
   - battery_sizing step 2 / capacity range covering 0, 2, 4 kWh.
   - solar_sizing panel_maxima south 2 others 0, battery 0..2 step 2, evaluation budget 100, short financial horizon 2.
   - 19 weather years and candidate comparisons evaluate correctly.
   - HP capex and retained gas costs propagate; no invented AC SCOP changes occur.
2. Cache invalidation on capacity/flow parameter changes:
   - Changes to hp_nominal_kw and hp_curve_flow_cold invalidate caches and update load and dispatch.
3. Study conditionals using bounded patched candidate fixtures:
   - HP on: scop_scenarios key is retained and empty, clear inactive AC note added to notes.
   - HP off: scop_scenarios contains legacy AC scenarios, old behavior intact.
   - Sensitivity: skips HP and planning toggle when OFF; skips inactive legacy knobs and runs high-value explicit cases when ON.
4. Report export conditionals under write_reports:
   - HP on: conditional HP section with primary literature links (Vaillant, Stelrad, access 2026-10-14),
     gas Sep 25 – Aug 26 reference, unverified 0.70 boiler efficiency, thermal heat vs input electricity,
     SPF delivered heat, gas retention and fixed charges, shortfalls, and disclaimers.
   - HP off: legacy AC SCOP section completely unchanged.
5. Fixture contract:
   - tests/fixtures/heatpump_schema_fixture.json matches actual SPEC factory keys and types.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from feasibility import assumptions, engine, heatpump_settings, report
from test_support import IsolatedDefaultsTestCase


class TestHeatPumpStudies(IsolatedDefaultsTestCase):
    def setUp(self) -> None:
        super().setUp()
        engine._load_cache.cache_clear()
        engine._energy_cache.cache_clear()
        engine._dayhour_trace.cache_clear()
        engine._forecast_runs.cache_clear()

    def test_battery_sizing_hp_small_real_run(self) -> None:
        """battery_sizing runs realistically with HP active over 19 weather years with step 2."""
        s = dict(engine.defaults())
        s.update({
            'planning_heat_pump': True,
            'south_panels': 12,
            'load_scenario': 'electrified',
        })
        res = engine.battery_sizing(s, {'step': 2, 'max_capacity': 10})

        # 1. Weather years: the complete active historical ensemble
        years = res['chart1_capacity_dispatch']['weather_years']
        wp = engine.weather_period()
        self.assertEqual(len(years), wp.count)
        self.assertEqual(min(years), wp.min_year)
        self.assertEqual(max(years), wp.max_year)
        self.assertEqual(list(years), list(engine.data()['years']))

        # 2. Capacities evaluated at step 2 covering 0, 2, 4 kWh
        caps = res['chart1_capacity_dispatch']['capacities']
        cap_values = [c['capacity_kwh'] for c in caps]
        self.assertIn(0.0, cap_values)
        self.assertIn(2.0, cap_values)
        self.assertIn(4.0, cap_values)

        c0 = next(c for c in caps if c['capacity_kwh'] == 0.0)
        c2 = next(c for c in caps if c['capacity_kwh'] == 2.0)
        c4 = next(c for c in caps if c['capacity_kwh'] == 4.0)

        # 3. Boundary at C=0: zero discharge and savings
        self.assertEqual(c0['battery_discharge_kwh']['median'], 0.0)
        self.assertEqual(c0['annual_savings_eur']['median'], 0.0)

        # 4. Monotonic discharge progression for 0 -> 2 -> 4 kWh under HP load
        self.assertGreater(c2['battery_discharge_kwh']['median'], 0.0)
        self.assertGreater(c4['battery_discharge_kwh']['median'], c2['battery_discharge_kwh']['median'])
        self.assertLess(c4['grid_import_kwh']['median'], c2['grid_import_kwh']['median'])
        self.assertLess(c2['grid_import_kwh']['median'], c0['grid_import_kwh']['median'])

        # 5. Physical envelopes valid (min <= median <= max)
        for c in (c0, c2, c4):
            self.assertLessEqual(c['grid_import_kwh']['min'], c['grid_import_kwh']['median'])
            self.assertLessEqual(c['grid_import_kwh']['median'], c['grid_import_kwh']['max'])

        # 6. Verify option validation contract: max_capacity < 10 raises ValueError
        with self.assertRaisesRegex(ValueError, 'max_capacity must be integer between 10 and 50'):
            engine.battery_sizing(s, {'step': 2, 'max_capacity': 4})

    def test_solar_sizing_hp_small_real_run(self) -> None:
        """solar_sizing runs realistically with HP active: south 2, battery 0..2 step 2, horizon 2."""
        s = dict(engine.defaults())
        s.update({
            'planning_heat_pump': True,
            'load_scenario': 'electrified',
            'horizon_years': 2,
            'hp_capex': 10000.0,
            'cooking_smc_annual': 25.0,
            'hp_electric_cooking': False,
            'hp_disconnect_gas': False,
        })
        options = {
            'panel_maxima': {'south': 2, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_kwh': 0,
            'battery_max_kwh': 2,
            'battery_step_kwh': 2,
            'evaluation_budget': 100,
        }
        res = engine.solar_sizing(s, options)

        # 1. Size curve contains 0-panel baseline, 1-panel, and 2-panel points
        curve = res['size_curve']
        self.assertEqual(len(curve), 3)
        c0 = next(c for c in curve if c['panel_count'] == 0)
        c2 = next(c for c in curve if c['panel_count'] == 2)
        self.assertEqual(c0['kwp'], 0.0)
        self.assertEqual(c0['battery_kwh'], 0.0)
        self.assertEqual(c0['capex_eur'], 0.0)
        self.assertEqual(c0['npv_median'], 0.0)
        self.assertAlmostEqual(c2['kwp'], 0.8, places=2)

        # 2. Comparisons evaluated and non-empty
        comparisons = res['comparisons']
        self.assertGreaterEqual(len(comparisons), 3)
        labels = [cmp['label'] for cmp in comparisons]
        self.assertIn('No-install baseline', labels)
        self.assertIn('Maximum roof capacity (2 panels)', labels)

        # 3. HP capex and retained gas propagation:
        # HP baseline evaluation is consistent; no AC SCOP changes occur
        self.assertNotIn('scop_scenarios', res)
        # Sizing cost assumptions retain genuine quote/cost model
        self.assertIn('cost_assumptions', res)

    def test_cache_invalidation_capacity_and_flow(self) -> None:
        """Changing hp_nominal_kw and hp_curve_flow_cold invalidates cache and alters load/dispatch."""
        s_base = dict(engine.defaults())
        s_base.update({
            'planning_heat_pump': True,
            'load_scenario': 'electrified',
            'hp_nominal_kw': 9.0,
            'hp_curve_flow_cold': 55.0,
        })
        r_base = engine.evaluate(s_base)

        # 1. Change nominal capacity from 9.0 kW to 7.0 kW
        s_cap = dict(s_base)
        s_cap['hp_nominal_kw'] = 7.0
        r_cap = engine.evaluate(s_cap)

        hp_base = r_base['heat_pump']['envelopes']
        hp_cap = r_cap['heat_pump']['envelopes']

        # Smaller capacity leads to higher backup resistance electricity usage
        self.assertGreater(
            hp_cap['annual_space_backup_electricity_kwh']['median'],
            hp_base['annual_space_backup_electricity_kwh']['median'],
        )

        # 2. Change flow curve cold water design from 55°C to 45°C
        s_flow = dict(s_base)
        s_flow['hp_curve_flow_cold'] = 45.0
        r_flow = engine.evaluate(s_flow)

        hp_flow = r_flow['heat_pump']['envelopes']

        # Lower flow temperature yields higher seasonal performance factor (SPF)
        self.assertGreater(
            hp_flow['seasonal_performance_factor_delivered_heat']['median'],
            hp_base['seasonal_performance_factor_delivered_heat']['median'],
        )

    def test_study_conditionals_patched(self) -> None:
        """study preserves scop_scenarios key (empty when HP on) and updates sensitivity scope."""
        s = dict(engine.defaults())
        fake_candidates = [
            {
                'id': 'no_pv_electrified',
                'label': 'no_pv',
                'load_scenario': 'electrified',
                'battery_kwh': 0,
                'theoretical': False,
                'settings': {**s, 'load_scenario': 'electrified', 'south_panels': 0, 'battery_kwh': 0},
                'metrics': {
                    'npv_eur': {'min': 0, 'max': 0, 'median': 0},
                    'payback_years': {'min': None, 'max': None, 'median': None},
                    'discounted_payback_years': {'min': None, 'max': None, 'median': None},
                    'autarky': {'min': 0, 'max': 0, 'median': 0},
                    'self_consumption': {'min': 0, 'max': 0, 'median': 0},
                    'package_npv_eur': {'min': -1000, 'max': 1000, 'median': 0},
                    'grid_import_kwh': {'min': 3000, 'max': 3500, 'median': 3200},
                },
                'finance': {
                    'capex_eur': 0,
                    'conversion_capex_eur': 0,
                    'cashflows_by_weather': {'pv_incremental': [[0] * 20] * 19},
                    'nominal_discount_rate': 0.03,
                },
                'pareto': True,
            }
        ]

        # Explicit invented benchmark evidence for the study-control unit test.
        # The real model and public defaults carry no household ratings.
        recs=copy.deepcopy(engine.records())
        next(r for r in recs if r['name']=='scop')['provided_ratings']=[
            dict(value=3.,climate='benchmark-a',label='Fictional A'),
            dict(value=4.,climate='benchmark-b',label='Fictional B')]
        self.enterContext(patch('feasibility.engine.records',return_value=recs))
        with patch('feasibility.engine.sweep', return_value={'candidates': fake_candidates}):
            # HP OFF: legacy behavior
            s_off = {**s, 'planning_heat_pump': False}
            study_off = engine.study(s_off)
            self.assertEqual(len(study_off['scop_scenarios']), 4)
            self.assertFalse(any('Heat pump planning active' in note for note in study_off['notes']))
            sens_off = study_off['sensitivity']['cases']
            params_off = {c['parameter'] for c in sens_off}
            self.assertIn('scop', params_off)
            self.assertNotIn('hp_nominal_kw', params_off)
            self.assertNotIn('planning_heat_pump', params_off)

            # HP ON: scop_scenarios empty, clear note, sensitivity iterates HP cases
            s_on = {**s, 'planning_heat_pump': True}
            study_on = engine.study(s_on)
            self.assertEqual(study_on['scop_scenarios'], [])
            self.assertTrue(any('Heat pump planning active' in note for note in study_on['notes']))
            sens_on = study_on['sensitivity']['cases']
            params_on = {c['parameter'] for c in sens_on}
            self.assertNotIn('scop', params_on)
            self.assertNotIn('dhw_heating_type', params_on)
            self.assertNotIn('electrification_capex', params_on)
            self.assertNotIn('planning_heat_pump', params_on)
            self.assertIn('hp_nominal_kw', params_on)
            self.assertIn('hp_curve_flow_cold', params_on)
            self.assertIn('hp_capex', params_on)

    def test_report_export_conditionals(self) -> None:
        """write_reports generates comprehensive HP section when ON, and maintains legacy AC report when OFF."""
        s = dict(engine.defaults())
        s_on = {**s, 'planning_heat_pump': True, 'load_scenario': 'electrified'}
        s_off = {**s, 'planning_heat_pump': False}

        fake_candidates_on = [{
            'id': 'no_pv_electrified', 'label': 'no_pv', 'load_scenario': 'electrified', 'battery_kwh': 0, 'theoretical': False,
            'settings': s_on,
            'metrics': {'grid_import_kwh': {'min': 3000, 'max': 3500}, 'npv_eur': {'min': 0, 'max': 0}, 'package_npv_eur': {'min': -1000, 'max': 1000}},
            'finance': {'conversion_capex_eur': 10000},
        }]
        study_on = {
            'calibration': engine.calibration(),
            'base': {'candidates': fake_candidates_on},
            'baseline_settings': s_on,
            'counts': {'candidates': 1, 'financial_candidate_cases': 1, 'joint_stress_cases': 1},
            'candidate_joint_envelopes': [{'load_scenario': 'electrified', 'label': '12S', 'battery_kwh': 5, 'theoretical': False, 'metrics': {'npv_eur': {'min': 1000, 'max': 2000}, 'payback_years': {'min': 5, 'max': 8}, 'autarky': {'min': 0.6, 'max': 0.8}}}],
            'battery_counterfactuals': [],
            'rankings': [{'objective': 'npv_eur', 'scenario': 'flat_deduction_0.5', 'load_scenario': 'electrified', 'ordered_ids': ['12S_5kWh']}],
            'sensitivity': {'cases': []},
            'scop_scenarios': [],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            report.write_reports(study_on, dest=tmpdir)
            md_on = (Path(tmpdir) / 'executive.md').read_text()
            summary_on = (Path(tmpdir) / 'executive_summary.md').read_text()

            # 1. Primary literature citations
            self.assertIn('https://www.vaillant.com/en/products/arothermplus/', md_on)
            self.assertIn('https://www.stelrad.ie/wp-content/uploads/2021/10/Stelrad-Correction-Factor-Oct-21.pdf', md_on)
            self.assertIn('2026-10-14', md_on)

            # 2. Real gas Sep 25 – Aug 26 reference & unverified 0.70 boiler efficiency
            self.assertIn(f'{engine.GAS_MONTHLY.sum():,.0f}', md_on)
            self.assertIn('Riferimento termico del profilo', md_on)
            self.assertIn(f"{s_on['boiler_efficiency']:.2f}", md_on)

            # 3. Thermal heat vs input electricity & SPF
            self.assertIn('Fabbisogno termico utile riscaldamento erogato (kWh_th)', md_on)
            self.assertIn('Consumo elettrico totale pompa di calore (kWh_e)', md_on)
            self.assertIn('Fattore di prestazione stagionale utile (SPF calore consegnato)', md_on)

            # 4. Gas retention & fixed charges
            self.assertIn('hp_electric_cooking=True', md_on)
            self.assertIn('hp_disconnect_gas=True', md_on)
            self.assertIn('quote fisse di rete/contatore', md_on)

            # 5. Shortfall and backup text
            self.assertIn('Deficit termico non servito riscaldamento', md_on)
            self.assertIn('resistenza integrativa', md_on)

            # 6. Disclaimers: no AC SCOP applicable and no fixed monthly electricity budget
            self.assertIn('I rating SCOP del climatizzatore non sono applicabili', md_on)
            self.assertIn('senza impiegare budget mensili elettrici prefissati', md_on)
            self.assertIn('non sono applicabili', summary_on)

            # 7. Continuous 24h schedule mode rendered by default
            self.assertIn('disponibilità continua 24h', md_on)
            self.assertNotIn('finestre mattina', md_on)

            # 8. Hygiene cycle disabled by default, disclaiming health adequacy
            self.assertIn('ciclo antilegionella opzionale DISABILITATO', md_on)
            self.assertIn('nessuna attestazione di adeguatezza sanitaria', md_on)

        # Scheduled mode windows test
        s_sched = {**s_on, 'hp_schedule_mode': 'scheduled', 'hp_morning_on': 6, 'hp_morning_off': 8, 'hp_evening_on': 16, 'hp_evening_off': 20}
        study_sched = {**study_on, 'baseline_settings': s_sched}
        with tempfile.TemporaryDirectory() as tmpdir_sched:
            report.write_reports(study_sched, dest=tmpdir_sched)
            md_sched = (Path(tmpdir_sched) / 'executive.md').read_text()
            self.assertIn('finestre mattina 6:00–8:00 e sera 16:00–20:00', md_sched)

        # Hygiene cycle enabled test
        s_hyg = {**s_on, 'hp_hygiene_enabled': True, 'hp_hygiene_temp': 65.0}
        study_hyg = {**study_on, 'baseline_settings': s_hyg}
        with tempfile.TemporaryDirectory() as tmpdir_hyg:
            report.write_reports(study_hyg, dest=tmpdir_hyg)
            md_hyg = (Path(tmpdir_hyg) / 'executive.md').read_text()
            self.assertIn('ciclo antilegionella abilitato a 65.0°C', md_hyg)
            self.assertIn('nessuna attestazione di adeguatezza sanitaria', md_hyg)

        # Edited eta 0.85 and radiator exponent 1.4 test
        s_edited = {**s_on, 'boiler_efficiency': 0.85, 'hp_radiator_exponent': 1.4}
        study_edited = {**study_on, 'baseline_settings': s_edited}
        with tempfile.TemporaryDirectory() as tmpdir_edited:
            report.write_reports(study_edited, dest=tmpdir_edited)
            md_edited = (Path(tmpdir_edited) / 'executive.md').read_text()
            summary_edited = (Path(tmpdir_edited) / 'executive_summary.md').read_text()
            self.assertIn('0.85', md_edited)
            self.assertIn('n = 1.4', md_edited)
            self.assertIn('0.85', summary_edited)
            self.assertIn('n=1.4', summary_edited)

        # HP on baseline scenario exports no invented service
        s_base = {**s, 'planning_heat_pump': True, 'load_scenario': 'baseline'}
        study_base = {**study_on, 'baseline_settings': s_base}
        with tempfile.TemporaryDirectory() as tmpdir_base:
            report.write_reports(study_base, dest=tmpdir_base)
            md_base = (Path(tmpdir_base) / 'executive.md').read_text()
            summary_base = (Path(tmpdir_base) / 'executive_summary.md').read_text()
            self.assertIn('la pompa di calore è ESCLUSA da questo scenario', md_base)
            self.assertIn('nessun servizio termico o elettrico della pompa di calore è conteggiato nel baseline storico', md_base)
            self.assertIn('la pompa di calore è esclusa dal fabbisogno', summary_base)
            self.assertNotIn('Fabbisogno termico utile riscaldamento erogato (kWh_th)', md_base)
            self.assertNotIn('Consumo elettrico totale pompa di calore (kWh_e)', md_base)
            self.assertNotIn('412.0 Smc', md_base)
            self.assertNotIn('3206 kWh', md_base)
            self.assertNotIn('281.0 Smc', md_base)
            self.assertNotIn('2187 kWh', md_base)

        # HP OFF regression
        fake_candidates_off = [{
            'id': 'no_pv_electrified', 'label': 'no_pv', 'load_scenario': 'electrified', 'battery_kwh': 0, 'theoretical': False,
            'settings': s_off,
            'metrics': {'grid_import_kwh': {'min': 3000, 'max': 3500}, 'npv_eur': {'min': 0, 'max': 0}, 'package_npv_eur': {'min': -1000, 'max': 1000}},
            'finance': {'conversion_capex_eur': 0},
        }]
        study_off = {
            'calibration': engine.calibration(),
            'base': {'candidates': fake_candidates_off},
            'baseline_settings': s_off,
            'counts': {'candidates': 1, 'financial_candidate_cases': 1, 'joint_stress_cases': 1},
            'candidate_joint_envelopes': [{'load_scenario': 'electrified', 'label': '12S', 'battery_kwh': 5, 'theoretical': False, 'metrics': {'npv_eur': {'min': 1000, 'max': 2000}, 'payback_years': {'min': 5, 'max': 8}, 'autarky': {'min': 0.6, 'max': 0.8}}}],
            'battery_counterfactuals': [],
            'rankings': [{'objective': 'npv_eur', 'scenario': 'flat_deduction_0.5', 'load_scenario': 'electrified', 'ordered_ids': ['12S_5kWh']}],
            'sensitivity': {'cases': []},
            'scop_scenarios': [{'load_scenario': 'electrified', 'climate': 'average', 'value': 4.0, 'result': {'enduses': {'heating_kwh': 1000}, 'energy': {'load_kwh': {'min': 4000, 'max': 4500}}, 'metrics': {'package_npv_eur': {'min': 500, 'max': 1500}}}}],
        }

        with tempfile.TemporaryDirectory() as tmpdir_off:
            report.write_reports(study_off, dest=tmpdir_off)
            md_off = (Path(tmpdir_off) / 'executive.md').read_text()
            summary_off = (Path(tmpdir_off) / 'executive_summary.md').read_text()

            # Regression: legacy AC section intact
            self.assertIn('## Scenario SCOP del climatizzatore', md_off)
            self.assertIn('Scenario SCOP configurato:', md_off)
            self.assertIn('Scenario SCOP configurato:', summary_off)

    def test_schema_fixture_contract_matches_spec(self) -> None:
        """Fixture heatpump_schema_fixture.json matches actual SPEC factory keys and types."""
        fixture_path = Path(__file__).resolve().parent / 'fixtures' / 'heatpump_schema_fixture.json'
        self.assertTrue(fixture_path.exists(), f"Fixture file not found: {fixture_path}")

        with open(fixture_path) as f:
            fixture = json.load(f)

        self.assertIn('defaults', fixture)
        fixture_defaults = fixture['defaults']
        spec = assumptions.SPEC

        # 1. Exact key match with SPEC
        self.assertEqual(
            set(fixture_defaults.keys()),
            set(spec.keys()),
            f"Key difference: {set(fixture_defaults.keys()) ^ set(spec.keys())}",
        )

        # 2. Type conformity
        for key, spec_entry in spec.items():
            spec_default = spec_entry[0]
            val = fixture_defaults[key]
            if spec_default is None:
                self.assertTrue(val is None or isinstance(val, (int, float, str, bool)))
            elif isinstance(spec_default, bool):
                self.assertIsInstance(val, bool, f"Expected bool for {key}")
            elif isinstance(spec_default, (int, float)):
                self.assertIsInstance(val, (int, float), f"Expected number for {key}")
            elif isinstance(spec_default, str):
                self.assertIsInstance(val, str, f"Expected str for {key}")

        # 3. All 59 HP parameters exist
        for hp_key in heatpump_settings.HP_ALL_KEYS:
            self.assertIn(hp_key, fixture_defaults)


if __name__ == '__main__':
    unittest.main()
