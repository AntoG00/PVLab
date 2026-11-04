"""Tests for AC space-heating backend report generation in write_reports and write_summary.

Covers:
1. Active AC 0% and 25% additional useful-heat demand scaling.
2. Canonical multiwindow/minute schedule formatting (arbitrary windows, minute precision,
   overnight wrapping midnight, inactive start==end, all-day, empty schedule, and continuous mode).
3. Civil Europe/Rome DST vs standard fixed UTC+1 context, minute boundaries in hourly model,
   and fixed UTC+1 plotting vs civil schedule labels.
4. Historical baseline load scenario distinction vs AC counterfactual.
5. Handling of unavailable / missing evaluated enduses and diagnostics fields (no fabrication).
6. Allocation diagnostics and power requirements (interval-average vs permitted-duration),
   with explicit unassessed equipment adequacy / comfort and no building dynamics limitations.
7. HP independence: isolation between planning_heat_pump and AC heating sections.
8. Single multiplier calculation (electricity = requested useful heat / SCOP, no second multiplier),
   fixed monthly budget from historical gas, and no extra gas bill savings for additional demand.
9. Bounded real engine evaluation report generation into temporary directory.
"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
sys.path.insert(0, str(PROJECT_ROOT / 'feasibility' / 'vendor'))
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(HERE))

# test_support MUST be imported first before persistent engine / state operations
import test_support  # noqa: F401
from test_support import IsolatedDefaultsTestCase

import feasibility.engine as engine
import feasibility.report as report
from feasibility.schedule import DEFAULT_AC_HEATING_WINDOWS


class AcHeatingReportsTests(IsolatedDefaultsTestCase):
    """Test suite for AC space heating in feasibility/report.py."""

    def _make_base_study(self, settings_override=None, selected_override=None):
        """Construct deterministic synthetic study fixture for isolated report testing."""
        s = dict(engine.defaults())
        s.update({
            'load_scenario': 'electrified',
            'planning_heat_pump': False,
            'scop': 4.0,
            'ac_heating_additional_demand_pct': 0.0,
            'heating_schedule_mode': 'scheduled',
            'ac_heating_windows': copy.deepcopy(DEFAULT_AC_HEATING_WINDOWS),
            'timezone_mode': 'civil',
            'heating_balance_temp': 18.0,
        })
        if settings_override:
            s.update(settings_override)

        cand = [{
            'id': f"{s['load_scenario']}_no_pv_0",
            'label': 'no_pv',
            'load_scenario': s['load_scenario'],
            'battery_kwh': 0,
            'theoretical': False,
            'settings': s,
            'metrics': {
                'grid_import_kwh': {'min': 3000.0, 'max': 3500.0},
                'npv_eur': {'min': 0.0, 'max': 0.0},
                'package_npv_eur': {'min': -500.0, 'max': 500.0},
            },
            'finance': {'conversion_capex_eur': 0.0},
        }]

        study = {
            'calibration': engine.calibration(),
            'base': {'candidates': cand},
            'baseline_settings': s,
            'counts': {'candidates': 1, 'financial_candidate_cases': 1, 'joint_stress_cases': 1},
            'candidate_joint_envelopes': [{
                'load_scenario': s['load_scenario'],
                'label': '12S',
                'battery_kwh': 5,
                'theoretical': False,
                'metrics': {
                    'npv_eur': {'min': 1000.0, 'max': 2000.0},
                    'payback_years': {'min': 5.0, 'max': 8.0},
                    'autarky': {'min': 0.6, 'max': 0.8},
                },
            }],
            'battery_counterfactuals': [],
            'rankings': [{
                'objective': 'npv_eur',
                'scenario': 'flat_deduction_0.5',
                'load_scenario': s['load_scenario'],
                'ordered_ids': ['12S_5kWh'],
            }],
            'sensitivity': {'cases': []},
            'scop_scenarios': [{
                'load_scenario': 'electrified',
                'climate': 'stagione media',
                'value': 4.0,
                'result': {
                    'enduses': {'heating_kwh': 1250.0},
                    'energy': {'load_kwh': {'min': 4000.0, 'max': 4500.0}},
                    'metrics': {'package_npv_eur': {'min': 100.0, 'max': 500.0}},
                },
            }, {
                'load_scenario': 'electrified',
                'climate': 'più calda',
                'value': 5.1,
                'result': {
                    'enduses': {'heating_kwh': 980.0},
                    'energy': {'load_kwh': {'min': 3700.0, 'max': 4200.0}},
                    'metrics': {'package_npv_eur': {'min': 200.0, 'max': 600.0}},
                },
            }],
        }

        if selected_override is not None:
            study['selected'] = selected_override
        return study

    def test_1_active_ac_0pct_demand_electrified(self):
        """Active AC with 0% additional demand: counterfactual equals requested, extra is 0."""
        selected_res = {
            'settings': {'load_scenario': 'electrified', 'scop': 4.0, 'ac_heating_additional_demand_pct': 0.0},
            'enduses': {
                'heating_kwh': 1200.0,
                'ac_heating_baseline_useful_kwh': 4800.0,
                'ac_heating_requested_useful_kwh': 4800.0,
                'ac_heating_baseline_electricity_kwh': 1200.0,
                'ac_heating_requested_electricity_kwh': 1200.0,
                'ac_heating_extra_electricity_kwh': 0.0,
                'ac_heating_additional_demand_pct': 0.0,
                'ac_heating_scop': 4.0,
                'ac_heating_allocation_diagnostics': {
                    'nominal_daily_union_duration_hours': 6.0,
                    'max_modeled_interval_average_electrical_demand_kw': 1.80,
                    'max_modeled_permitted_duration_electrical_requirement_kw': 1.80,
                    'max_modeled_permitted_duration_useful_heat_requirement_kw': 7.20,
                },
            },
            'assumptions': engine.records(),
        }

        study = self._make_base_study(
            settings_override={'ac_heating_additional_demand_pct': 0.0, 'load_scenario': 'electrified'},
            selected_override=selected_res,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            report.write_reports(study, dest=tmpdir)
            md = (Path(tmpdir) / 'executive.md').read_text(encoding='utf-8')
            summary = (Path(tmpdir) / 'executive_summary.md').read_text(encoding='utf-8')

            # 1. Demand percentage 0% present
            self.assertIn('0%', md)
            self.assertIn('0%', summary)

            # 2. Authoritative 0%-AC counterfactual and requested electricity match
            self.assertIn('1,200.00 kWh_e/anno', md)
            self.assertIn('0.00 kWh_e/anno', md)  # extra kWh
            self.assertIn('4,800 kWh_th/anno', md)

            # 3. Summary covers counterfactual and zero extra
            self.assertIn('controfattuale 0% AC 1,200.00 kWh/anno', summary)
            self.assertIn('extra richiesta 0.00 kWh/anno', summary)

            # 4. Single multiplier and no extra historical gas savings explanations
            self.assertIn('una sola volta', md)
            self.assertIn('una sola volta', summary)
            self.assertIn('non genera pertanto ulteriori risparmi sulla bolletta gas storica', md)
            self.assertIn('non genera ulteriori risparmi sulla bolletta gas storica', summary)

            # 5. Semantic formula verification (electricity = requested useful heat / SCOP)
            self.assertAlmostEqual(
                selected_res['enduses']['ac_heating_requested_electricity_kwh'],
                selected_res['enduses']['ac_heating_requested_useful_kwh'] / selected_res['enduses']['ac_heating_scop']
            )
            self.assertAlmostEqual(
                selected_res['enduses']['ac_heating_baseline_electricity_kwh'],
                selected_res['enduses']['ac_heating_baseline_useful_kwh'] / selected_res['enduses']['ac_heating_scop']
            )

    def test_2_active_ac_25pct_demand_scaling(self):
        """Active AC with 25% additional demand: requested is 1.25x baseline, extra is 0.25x."""
        base_th = 4800.0
        req_th = 4800.0 * 1.25  # 6000.0
        base_elec = 1200.0
        req_elec = 1200.0 * 1.25  # 1500.0
        extra_elec = req_elec - base_elec  # 300.0

        selected_res = {
            'settings': {'load_scenario': 'electrified', 'scop': 4.0, 'ac_heating_additional_demand_pct': 25.0},
            'enduses': {
                'heating_kwh': req_elec,
                'ac_heating_baseline_useful_kwh': base_th,
                'ac_heating_requested_useful_kwh': req_th,
                'ac_heating_baseline_electricity_kwh': base_elec,
                'ac_heating_requested_electricity_kwh': req_elec,
                'ac_heating_extra_electricity_kwh': extra_elec,
                'ac_heating_additional_demand_pct': 25.0,
                'ac_heating_scop': 4.0,
                'ac_heating_allocation_diagnostics': {
                    'nominal_daily_union_duration_hours': 6.0,
                    'max_modeled_interval_average_electrical_demand_kw': 2.25,
                    'max_modeled_permitted_duration_electrical_requirement_kw': 2.25,
                    'max_modeled_permitted_duration_useful_heat_requirement_kw': 9.00,
                },
            },
            'assumptions': engine.records(),
        }

        study = self._make_base_study(
            settings_override={'ac_heating_additional_demand_pct': 25.0, 'load_scenario': 'electrified'},
            selected_override=selected_res,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            report.write_reports(study, dest=tmpdir)
            md = (Path(tmpdir) / 'executive.md').read_text(encoding='utf-8')
            summary = (Path(tmpdir) / 'executive_summary.md').read_text(encoding='utf-8')

            # 1. 25% demand indicated
            self.assertIn('25%', md)
            self.assertIn('25%', summary)

            # 2. Figures appear accurately
            self.assertIn('4,800 kWh_th/anno', md)
            self.assertIn('6,000 kWh_th/anno', md)
            self.assertIn('1,200.00 kWh_e/anno', md)
            self.assertIn('1,500.00 kWh_e/anno', md)
            self.assertIn('300.00 kWh_e/anno', md)

            # 3. Summary displays counterfactual, requested, and extra
            self.assertIn('controfattuale 0% AC 1,200.00 kWh/anno', summary)
            self.assertIn('elettricità riscaldamento richiesta 1,500.00 kWh/anno', summary)
            self.assertIn('extra richiesta 300.00 kWh/anno', summary)
            self.assertIn('6,000 kWh_th/anno', summary)

    def test_2b_arbitrary_precision_demand_pct(self):
        """Arbitrary demand percentage (e.g. 12.3456789%) is rendered with exact precision without rounding."""
        base_th = 4800.0
        pct = 12.3456789
        req_th = base_th * (1.0 + pct / 100.0)
        base_elec = 1200.0
        req_elec = req_th / 4.0
        extra_elec = req_elec - base_elec

        selected_res = {
            'settings': {'load_scenario': 'electrified', 'scop': 4.0, 'ac_heating_additional_demand_pct': pct},
            'enduses': {
                'heating_kwh': req_elec,
                'ac_heating_baseline_useful_kwh': base_th,
                'ac_heating_requested_useful_kwh': req_th,
                'ac_heating_baseline_electricity_kwh': base_elec,
                'ac_heating_requested_electricity_kwh': req_elec,
                'ac_heating_extra_electricity_kwh': extra_elec,
                'ac_heating_additional_demand_pct': pct,
                'ac_heating_scop': 4.0,
            },
            'assumptions': engine.records(),
        }

        study = self._make_base_study(
            settings_override={'ac_heating_additional_demand_pct': pct, 'load_scenario': 'electrified'},
            selected_override=selected_res,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            report.write_reports(study, dest=tmpdir)
            md = (Path(tmpdir) / 'executive.md').read_text(encoding='utf-8')
            summary = (Path(tmpdir) / 'executive_summary.md').read_text(encoding='utf-8')

            # Exact precision preserved in both reports (no rounding to 6 digits or 12.3457%)
            self.assertIn('12.3456789%', md)
            self.assertIn('12.3456789%', summary)

    def test_3_canonical_windows_various_forms(self):
        """Covers minute windows, overnight wrapping midnight, inactive, all-day, and empty."""
        # A. Multiple minute windows (08:30-09:15 and 17:00-20:00)
        s_minute = {
            'ac_heating_windows': [
                {'id': 'morning_split', 'start_minute': 510, 'end_minute': 555},
                {'id': 'evening_block', 'start_minute': 1020, 'end_minute': 1200},
            ],
            'timezone_mode': 'civil',
        }
        study_min = self._make_base_study(settings_override=s_minute)
        with tempfile.TemporaryDirectory() as tmpdir:
            report.write_reports(study_min, dest=tmpdir)
            md = (Path(tmpdir) / 'executive.md').read_text(encoding='utf-8')
            summary = (Path(tmpdir) / 'executive_summary.md').read_text(encoding='utf-8')

            self.assertIn('morning_split: 08:30–09:15', md)
            self.assertIn('evening_block: 17:00–20:00', md)
            self.assertIn('morning_split: 08:30–09:15', summary)
            self.assertIn('evening_block: 17:00–20:00', summary)
            self.assertIn('Europe/Rome', md)
            self.assertIn('Europe/Rome', summary)

        # B. Overnight window wrapping midnight (22:00-06:00: 1320..360)
        s_overnight = {
            'ac_heating_windows': [
                {'id': 'night_wrap', 'start_minute': 1320, 'end_minute': 360},
            ],
            'timezone_mode': 'standard',
        }
        study_night = self._make_base_study(settings_override=s_overnight)
        with tempfile.TemporaryDirectory() as tmpdir:
            report.write_reports(study_night, dest=tmpdir)
            md = (Path(tmpdir) / 'executive.md').read_text(encoding='utf-8')
            summary = (Path(tmpdir) / 'executive_summary.md').read_text(encoding='utf-8')

            self.assertIn('night_wrap: notturna (22:00–06:00, scavalco mezzanotte)', md)
            self.assertIn('night_wrap: notturna (22:00–06:00, scavalco mezzanotte)', summary)
            self.assertIn('orario solare standard fisso UTC+1', md)
            self.assertIn('orario solare standard fisso UTC+1', summary)

        # C. Inactive window (start == end)
        s_inactive = {
            'ac_heating_windows': [
                {'id': 'dormant', 'start_minute': 480, 'end_minute': 480},
                {'id': 'active_run', 'start_minute': 960, 'end_minute': 1200},
            ],
        }
        study_inact = self._make_base_study(settings_override=s_inactive)
        with tempfile.TemporaryDirectory() as tmpdir:
            report.write_reports(study_inact, dest=tmpdir)
            md = (Path(tmpdir) / 'executive.md').read_text(encoding='utf-8')
            summary = (Path(tmpdir) / 'executive_summary.md').read_text(encoding='utf-8')

            self.assertIn('dormant: inattiva (08:00–08:00, 0 min)', md)
            self.assertIn('dormant: inattiva (08:00–08:00, 0 min)', summary)
            self.assertIn('active_run: 16:00–20:00', md)

        # D. All-day window (0..1440)
        s_allday = {
            'ac_heating_windows': [
                {'id': 'whole_day', 'start_minute': 0, 'end_minute': 1440},
            ],
        }
        study_allday = self._make_base_study(settings_override=s_allday)
        with tempfile.TemporaryDirectory() as tmpdir:
            report.write_reports(study_allday, dest=tmpdir)
            md = (Path(tmpdir) / 'executive.md').read_text(encoding='utf-8')
            summary = (Path(tmpdir) / 'executive_summary.md').read_text(encoding='utf-8')

            self.assertIn('whole_day: all-day 24h (00:00–24:00)', md)
            self.assertIn('whole_day: all-day 24h (00:00–24:00)', summary)

        # E. Empty schedule ([])
        s_empty = {'ac_heating_windows': []}
        study_empty = self._make_base_study(settings_override=s_empty)
        with tempfile.TemporaryDirectory() as tmpdir:
            report.write_reports(study_empty, dest=tmpdir)
            md = (Path(tmpdir) / 'executive.md').read_text(encoding='utf-8')
            summary = (Path(tmpdir) / 'executive_summary.md').read_text(encoding='utf-8')

            self.assertIn('nessuna finestra operativa definita (programmazione vuota / bozza)', md)
            self.assertIn('nessuna finestra operativa definita (programmazione vuota / bozza)', summary)

    def test_4_continuous_schedule_mode(self):
        """Continuous schedule mode preserves 24h legacy continuous availability semantics,
        both when canonical windows are retained in settings and when omitted.
        """
        # Case A: continuous with retained canonical windows in settings
        s_cont_retained = {
            'heating_schedule_mode': 'continuous',
            'ac_heating_windows': [
                {'id': 'morning', 'start_minute': 360, 'end_minute': 480},
                {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
            ],
        }
        study_a = self._make_base_study(settings_override=s_cont_retained)
        with tempfile.TemporaryDirectory() as tmpdir:
            report.write_reports(study_a, dest=tmpdir)
            md = (Path(tmpdir) / 'executive.md').read_text(encoding='utf-8')
            summary = (Path(tmpdir) / 'executive_summary.md').read_text(encoding='utf-8')

            self.assertIn('disponibilità continua 24h (modello storico continuo senza finestre di spegnimento; finestre canoniche conservate: morning: 06:00–08:00; evening: 16:00–20:00)', md)
            self.assertIn('disponibilità continua 24h (modello storico continuo senza finestre di spegnimento; finestre canoniche conservate: morning: 06:00–08:00; evening: 16:00–20:00)', summary)

        # Case B: continuous without retained canonical windows (None / omitted)
        s_cont_pure = {
            'heating_schedule_mode': 'continuous',
            'ac_heating_windows': None,
        }
        study_b = self._make_base_study(settings_override=s_cont_pure)
        with tempfile.TemporaryDirectory() as tmpdir:
            report.write_reports(study_b, dest=tmpdir)
            md = (Path(tmpdir) / 'executive.md').read_text(encoding='utf-8')
            summary = (Path(tmpdir) / 'executive_summary.md').read_text(encoding='utf-8')

            self.assertIn('disponibilità continua 24h (modello storico continuo senza finestre di spegnimento)', md)
            self.assertIn('disponibilità continua 24h (modello storico continuo senza finestre di spegnimento)', summary)
            self.assertNotIn('finestre canoniche conservate', md)
            self.assertNotIn('finestre canoniche conservate', summary)

    def test_5_historical_baseline_scenario_distinction(self):
        """Baseline load scenario explicitly distinguishes un-electrified load from AC counterfactual."""
        s_base = {'load_scenario': 'baseline', 'ac_heating_additional_demand_pct': 25.0}
        selected_res = {
            'settings': s_base,
            'enduses': {'heating_kwh': 0.0},
            'assumptions': engine.records(),
        }
        study = self._make_base_study(settings_override=s_base, selected_override=selected_res)

        with tempfile.TemporaryDirectory() as tmpdir:
            report.write_reports(study, dest=tmpdir)
            md = (Path(tmpdir) / 'executive.md').read_text(encoding='utf-8')
            summary = (Path(tmpdir) / 'executive_summary.md').read_text(encoding='utf-8')

            # Explicit baseline notice in full report
            self.assertIn('Scenario base storico selezionato (`baseline`)', md)
            self.assertIn('il climatizzatore per riscaldamento è ESCLUSO dal profilo di carico elettrico', md)
            self.assertIn('nessun consumo elettrico o calore utile del climatizzatore è conteggiato nel baseline storico', md)

            # Explicit baseline notice in summary
            self.assertIn('Nello scenario base storico (`baseline`), il climatizzatore è escluso dal profilo di carico elettrico', summary)
            self.assertIn('riscaldamento a gas invariato senza consumi AC', summary)

    def test_6_unavailable_missing_fields_handling(self):
        """Missing or unavailable evaluated fields display 'non disponibile' rather than fabricated values."""
        # Selected is None or lacking enduses
        selected_incomplete = {
            'settings': {'load_scenario': 'electrified'},
            'enduses': {},
            'assumptions': engine.records(),
        }
        study = self._make_base_study(settings_override={'load_scenario': 'electrified'}, selected_override=selected_incomplete)

        with tempfile.TemporaryDirectory() as tmpdir:
            report.write_reports(study, dest=tmpdir)
            md = (Path(tmpdir) / 'executive.md').read_text(encoding='utf-8')
            summary = (Path(tmpdir) / 'executive_summary.md').read_text(encoding='utf-8')

            # Values that were missing are shown as 'non disponibile'
            self.assertIn('non disponibile', md)
            self.assertIn('non disponibile', summary)

    def test_6b_missing_schedule_and_demand_metadata(self):
        """Missing schedule and demand metadata displays 'non disponibile' / 'non definita' without fabricating hours."""
        s_missing = {
            'load_scenario': 'electrified',
            'heating_schedule_mode': 'scheduled',
            'ac_heating_windows': None,
            'ac_heating_additional_demand_pct': None,
            'timezone_mode': None,
        }
        for k in ['heating_on_hour', 'heating_off_hour', 'heating_evening_on_hour', 'heating_evening_off_hour']:
            s_missing.pop(k, None)

        selected_res = {
            'settings': s_missing,
            'enduses': {},
            'assumptions': engine.records(),
        }
        study = self._make_base_study(settings_override=s_missing, selected_override=selected_res)

        with tempfile.TemporaryDirectory() as tmpdir:
            report.write_reports(study, dest=tmpdir)
            md = (Path(tmpdir) / 'executive.md').read_text(encoding='utf-8')
            summary = (Path(tmpdir) / 'executive_summary.md').read_text(encoding='utf-8')

            # Schedule is reported as undefined / unavailable, NOT falling back to factory 06:00-08:00
            self.assertIn('programmazione non definita / non disponibile', md)
            self.assertIn('programmazione non definita / non disponibile', summary)
            self.assertNotIn('06:00–08:00', md)
            self.assertNotIn('16:00–20:00', md)

            # Demand pct is reported as non disponibile
            self.assertIn('fabbisogno termico utile aggiuntivo selezionato pari a non disponibile', md)
            self.assertIn('Fabbisogno termico aggiuntivo selezionato: non disponibile', summary)

            # Clock context is reported as undefined / unavailable
            self.assertIn('contesto orario non definito / non disponibile', md)
            self.assertIn('contesto orario non definito / non disponibile', summary)

    def test_6c_nonfinite_and_large_demand_pct(self):
        """Nonfinite values (NaN, Inf) and large nonnegative percentages are handled safely without crashing."""
        # 1. Direct helper checks avoid int(nan) / int(inf) crashes (ValueError / OverflowError)
        for bad_val in [float('nan'), float('inf'), float('-inf'), 'nan', 'inf', '-inf']:
            res = report._format_demand_pct({'ac_heating_additional_demand_pct': bad_val})
            self.assertEqual(res, 'non valida (valore non finito)')

        # 2. Report generation with nonfinite string values (valid for JSON serialization)
        for bad_str in ['nan', 'inf']:
            study_bad = self._make_base_study(settings_override={'ac_heating_additional_demand_pct': bad_str})
            with tempfile.TemporaryDirectory() as tmpdir:
                report.write_reports(study_bad, dest=tmpdir)
                md = (Path(tmpdir) / 'executive.md').read_text(encoding='utf-8')
                summary = (Path(tmpdir) / 'executive_summary.md').read_text(encoding='utf-8')
                self.assertIn('non valida (valore non finito)', md)
                self.assertIn('non valida (valore non finito)', summary)

        # 3. Large nonnegative values
        for large_val, expected_str in [(100.0, '100%'), (150.75, '150.75%')]:
            study_large = self._make_base_study(settings_override={'ac_heating_additional_demand_pct': large_val})
            with tempfile.TemporaryDirectory() as tmpdir:
                report.write_reports(study_large, dest=tmpdir)
                md = (Path(tmpdir) / 'executive.md').read_text(encoding='utf-8')
                summary = (Path(tmpdir) / 'executive_summary.md').read_text(encoding='utf-8')
                self.assertIn(expected_str, md)
                self.assertIn(expected_str, summary)

    def test_7_allocation_diagnostics_and_power_requirements(self):
        """Diagnostics distinguish interval-average from permitted-duration power requirements."""
        diag = {
            'nominal_daily_union_duration_hours': 4.5,
            'max_modeled_interval_average_electrical_demand_kw': 1.60,
            'max_modeled_permitted_duration_electrical_requirement_kw': 2.13,
            'max_modeled_permitted_duration_useful_heat_requirement_kw': 8.52,
        }
        selected_res = {
            'settings': {'load_scenario': 'electrified', 'scop': 4.0},
            'enduses': {
                'heating_kwh': 1200.0,
                'ac_heating_baseline_useful_kwh': 4800.0,
                'ac_heating_requested_useful_kwh': 4800.0,
                'ac_heating_baseline_electricity_kwh': 1200.0,
                'ac_heating_requested_electricity_kwh': 1200.0,
                'ac_heating_extra_electricity_kwh': 0.0,
                'ac_heating_additional_demand_pct': 0.0,
                'ac_heating_scop': 4.0,
                'ac_heating_allocation_diagnostics': diag,
            },
            'assumptions': engine.records(),
        }
        study = self._make_base_study(selected_override=selected_res)

        with tempfile.TemporaryDirectory() as tmpdir:
            report.write_reports(study, dest=tmpdir)
            md = (Path(tmpdir) / 'executive.md').read_text(encoding='utf-8')
            summary = (Path(tmpdir) / 'executive_summary.md').read_text(encoding='utf-8')

            # Duration and power figures
            self.assertIn('4.50 ore/giorno', md)
            self.assertIn('1.60 kW_e', md)
            self.assertIn('2.13 kW_e', md)
            self.assertIn('8.52 kW_th', md)

            self.assertIn('1.60 kW_e', summary)
            self.assertIn('2.13 kW_e', summary)
            self.assertIn('8.52 kW_th', summary)

            # Limitations and boundaries
            self.assertIn('Requisiti operativi di potenza e limiti del modello', md)
            self.assertIn('L\'adeguatezza dell\'apparecchiatura e il comfort termico non sono valutati', md)
            self.assertIn('Non è simulata la dinamica termica dell\'involucro edilizio', md)
            self.assertIn('adeguatezza macchine, comfort e dinamica d\'involucro non modellati', summary)

    def test_8_hp_independence_in_reports(self):
        """Planning HP ON renders air-to-water section; HP OFF renders AC heating section."""
        study_hp_on = self._make_base_study(settings_override={'planning_heat_pump': True, 'load_scenario': 'electrified'})
        study_hp_off = self._make_base_study(settings_override={'planning_heat_pump': False, 'load_scenario': 'electrified'})

        with tempfile.TemporaryDirectory() as tmpdir_on:
            report.write_reports(study_hp_on, dest=tmpdir_on)
            md_on = (Path(tmpdir_on) / 'executive.md').read_text(encoding='utf-8')
            summary_on = (Path(tmpdir_on) / 'executive_summary.md').read_text(encoding='utf-8')

            # HP ON: Has HP section, lacks AC heating specific windows description
            self.assertIn('## Pompa di calore aria-acqua (pianificazione)', md_on)
            self.assertIn('Pianificazione pompa di calore aria-acqua attiva', summary_on)
            self.assertNotIn('## Scenario SCOP del climatizzatore', md_on)

        with tempfile.TemporaryDirectory() as tmpdir_off:
            report.write_reports(study_hp_off, dest=tmpdir_off)
            md_off = (Path(tmpdir_off) / 'executive.md').read_text(encoding='utf-8')
            summary_off = (Path(tmpdir_off) / 'executive_summary.md').read_text(encoding='utf-8')

            # HP OFF: Has AC section, lacks HP planning section
            self.assertIn('## Scenario SCOP del climatizzatore', md_off)
            self.assertIn('Scenario SCOP configurato:', summary_off)
            self.assertNotIn('## Pompa di calore aria-acqua (pianificazione)', md_off)

    def test_9_bounded_real_engine_evaluation_report(self):
        """End-to-end integration with a bounded real engine evaluation into a temp directory."""
        s = dict(engine.defaults())
        s.update({
            'load_scenario': 'electrified',
            'planning_heat_pump': False,
            'ac_heating_additional_demand_pct': 25.0,
            'heating_schedule_mode': 'scheduled',
            'ac_heating_windows': [
                {'id': 'morning', 'start_minute': 360, 'end_minute': 480},
                {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
            ],
            'timezone_mode': 'civil',
            'scop': 4.0,
        })

        # Evaluate real engine result
        real_selected = engine.evaluate(s)
        self.assertIn('enduses', real_selected)
        enduses = real_selected['enduses']
        self.assertIn('ac_heating_requested_electricity_kwh', enduses)
        self.assertIn('ac_heating_baseline_electricity_kwh', enduses)
        self.assertIn('ac_heating_extra_electricity_kwh', enduses)

        # Derive expected peak independently from raw source allocation under active weather
        d = engine.data()
        s_val = engine.validate(s)
        raw_heat_profile, raw_diag = engine._allocate_space_heating(
            s_val,
            d['temperature'],
            d['month'],
            d['hour'],
            d['valid'],
            engine._get_civil_hours(d),
            d['years'],
            return_diagnostics=True,
        )
        expected_peak_e = float(raw_diag['max_modeled_permitted_duration_electrical_requirement_kw'])
        expected_peak_th = float(raw_diag['max_modeled_permitted_duration_useful_heat_requirement_kw'])
        expected_peak_e_str = f"{expected_peak_e:.2f} kW_e"
        expected_peak_th_str = f"{expected_peak_th:.2f} kW_th"

        study = self._make_base_study(settings_override=s, selected_override=real_selected)

        with tempfile.TemporaryDirectory() as tmpdir:
            res_path = report.write_reports(study, dest=tmpdir)
            self.assertEqual(res_path, str(tmpdir))

            exec_md = Path(tmpdir) / 'executive.md'
            exec_sum = Path(tmpdir) / 'executive_summary.md'
            self.assertTrue(exec_md.exists())
            self.assertTrue(exec_sum.exists())

            md_content = exec_md.read_text(encoding='utf-8')
            sum_content = exec_sum.read_text(encoding='utf-8')

            # Verify real evaluated numbers appear in output
            self.assertIn('25%', md_content)
            self.assertIn('morning: 06:00–08:00', md_content)
            self.assertIn('evening: 16:00–20:00', md_content)
            self.assertIn('Controfattuale elettrico 0% AC', md_content)
            self.assertIn('Consumo elettrico aggiuntivo', md_content)

            # Assert exact real evaluated numeric values in full report
            self.assertIn(f"{enduses['ac_heating_baseline_useful_kwh']:,.0f} kWh_th/anno", md_content)
            self.assertIn(f"{enduses['ac_heating_requested_useful_kwh']:,.0f} kWh_th/anno", md_content)
            self.assertIn(f"{enduses['ac_heating_baseline_electricity_kwh']:,.2f} kWh_e/anno", md_content)
            self.assertIn(f"{enduses['ac_heating_requested_electricity_kwh']:,.2f} kWh_e/anno", md_content)
            self.assertIn(f"{enduses['ac_heating_extra_electricity_kwh']:,.2f} kWh_e/anno", md_content)
            self.assertIn(expected_peak_e_str, md_content)
            self.assertIn(expected_peak_th_str, md_content)

            # Assert exact real evaluated numeric values in executive summary
            self.assertIn('25%', sum_content)
            self.assertIn(f"controfattuale 0% AC {enduses['ac_heating_baseline_electricity_kwh']:,.2f} kWh/anno", sum_content)
            self.assertIn(f"elettricità riscaldamento richiesta {enduses['ac_heating_requested_electricity_kwh']:,.2f} kWh/anno", sum_content)
            self.assertIn(f"extra richiesta {enduses['ac_heating_extra_electricity_kwh']:,.2f} kWh/anno", sum_content)
            self.assertIn(f"{enduses['ac_heating_requested_useful_kwh']:,.0f} kWh_th/anno", sum_content)
            self.assertIn(expected_peak_e_str, sum_content)
            self.assertIn(expected_peak_th_str, sum_content)

    def test_write_reports_v2_synthetic_calibration(self):
        """write_reports must succeed on a complete structurally valid study with v2 synthetic calibration."""
        study = self._make_base_study()
        # v2 synthetic calibration as emitted by engine.calibration() on v2 datasets
        study['calibration'] = {
            'annual_kwh': 4212.1,
            'annual_gas_smc': 300.0,
            'annual_electric_bill_eur': 1091.53,
            'thermal_baseline': {'baseline_cost_eur': 360.0},
            'invoice': None,
            'research': None,
            'legacy_reference_applicable': False,
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = report.write_reports(study, dest=Path(tmpdir) / 'reports')
            self.assertTrue((Path(out_dir) / 'executive.md').exists())
            self.assertTrue((Path(out_dir) / 'executive_summary.md').exists())
            exec_md = (Path(out_dir) / 'executive.md').read_text(encoding='utf-8')
            summary_md = (Path(out_dir) / 'executive_summary.md').read_text(encoding='utf-8')
            # Verify truthful not-available labels and absence of fabricated invoice total
            self.assertIn('non disponibile', exec_md)
            self.assertIn('fattura non disponibile', summary_md)
            # Verify annual values are truthfully reported from calibration
            self.assertIn('4,212.10', exec_md)
            self.assertIn('300', exec_md)
            # Verify verification.json preserved with None invoice without fabrication
            verif = json.loads((Path(out_dir) / 'verification.json').read_text(encoding='utf-8'))
            self.assertIsNone(verif['calibration']['invoice'])
            self.assertEqual(verif['calibration']['annual_kwh'], 4212.1)


if __name__ == '__main__':
    unittest.main()
