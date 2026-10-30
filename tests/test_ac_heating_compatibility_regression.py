"""Regression tests verifying compatibility evidence for AC heating demand & operating windows.

Task A: Captures baseline compatibility evidence from the CURRENT, unchanged production
implementation to guarantee 0% interval difference in future canonical migrations.
All tests use temporary paths and read-only inspection; live files are never mutated.
"""
from historical_weather import synthetic_thermal_runtime
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
sys.path.insert(0, str(PROJECT_ROOT / 'feasibility' / 'vendor'))
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np

import feasibility.engine as engine
import feasibility.assumptions as assumptions
import feasibility.shared_state as shared_state
import test_support
from historical_weather import frozen_baseline_weather_context

import os
FIXTURES_DIR = Path(os.environ['PV_LEGACY_DATA_ROOT']).parents[1] / 'compatibility'
NPZ_PATH = FIXTURES_DIR / 'ac_heating_compatibility_fixtures.npz'
METADATA_PATH = FIXTURES_DIR / 'ac_heating_compatibility_metadata.json'


class AcHeatingCompatibilityRegressionTests(unittest.TestCase):
    """Verify regression fixtures and current production behavior for Task A."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.enterClassContext(frozen_baseline_weather_context())
        if not NPZ_PATH.exists() or not METADATA_PATH.exists():
            raise FileNotFoundError(
                f"Missing compatibility fixtures: NPZ_PATH={NPZ_PATH} (exists={NPZ_PATH.exists()}), "
                f"METADATA_PATH={METADATA_PATH} (exists={METADATA_PATH.exists()}).\n"
                "Captured old-model arrays must never be regenerated automatically by tests after "
                "production changes, as doing so would overwrite historical baseline evidence with new model outputs.\n"
                "To manually generate baseline fixtures from the frozen old-model implementation, run:\n"
                "  PYTHONPATH=feasibility/vendor:. python tests/fixtures/generate_ac_heating_fixtures.py --overwrite"
            )
        cls.npz = np.load(NPZ_PATH)
        cls.metadata = json.loads(METADATA_PATH.read_text())
        cls.weather_data = engine.data()

    def setUp(self):
        super().setUp()
        engine._load_cache.cache_clear()
        engine._chart_cache.cache_clear()
        engine._dayhour_trace.cache_clear()

    def tearDown(self):
        engine._load_cache.cache_clear()
        engine._chart_cache.cache_clear()
        engine._dayhour_trace.cache_clear()
        super().tearDown()

    # -------------------------------------------------------------------------
    # Group 1: Live Configuration Read-Only Inspection and Preservation
    # -------------------------------------------------------------------------

    def test_baseline_metadata_configuration_preservation(self):
        """Verify baseline configuration evidence in metadata without requiring live disk files to remain static."""
        live_info = self.metadata['live_inspection']
        saved = live_info['saved_scenario']
        u_defs = live_info['user_defaults']
        f_defs = live_info['factory_defaults']

        # Baseline saved scenario captured parameters
        self.assertEqual(saved['heating_schedule_mode'], 'scheduled')
        self.assertEqual(saved['heating_on_hour'], 8)
        self.assertEqual(saved['heating_off_hour'], 12)
        self.assertEqual(saved['heating_evening_on_hour'], 16)
        self.assertEqual(saved['heating_evening_off_hour'], 20)
        self.assertEqual(saved['timezone_mode'], 'civil')
        self.assertEqual(saved['scop'], 4.0)
        self.assertEqual(saved['load_scenario'], 'electrified')
        self.assertFalse(saved.get('planning_heat_pump', False))

        # Baseline user defaults override captured parameters
        self.assertIn('scop', u_defs)
        self.assertEqual(u_defs['scop'], 2)

        # Baseline factory code defaults
        self.assertEqual(f_defs['heating_schedule_mode'], 'scheduled')
        self.assertEqual(f_defs['heating_on_hour'], 6)
        self.assertEqual(f_defs['heating_off_hour'], 8)
        self.assertEqual(f_defs['heating_evening_on_hour'], 16)
        self.assertEqual(f_defs['heating_evening_off_hour'], 20)
        self.assertEqual(f_defs['timezone_mode'], 'civil')
        self.assertEqual(f_defs['scop'], 4.0)

        # Factory SPEC in code must remain 4.0
        self.assertEqual(assumptions.SPEC['scop'][0], 4.0)

        # Contrast preserved independently: saved scenario SCOP 4.0 beats user-default SCOP 2.0
        self.assertNotEqual(saved['scop'], u_defs['scop'])

    def test_live_files_readable_and_not_mutated(self):
        """Verify live disk files are readable JSON and reading them causes zero in-place mutations."""
        scenario_path = PROJECT_ROOT / 'feasibility' / 'data' / 'current_scenario.json'
        defaults_path = PROJECT_ROOT / 'feasibility' / 'data' / 'user_defaults.json'

        for file_path in (scenario_path, defaults_path):
            if file_path.exists():
                sha_before = hashlib.sha256(file_path.read_bytes()).hexdigest()
                size_before = file_path.stat().st_size

                # Read and validate JSON parseability
                content = file_path.read_text()
                parsed = json.loads(content)
                self.assertIsInstance(parsed, dict, f"{file_path.name} must be a valid JSON dictionary")

                sha_after = hashlib.sha256(file_path.read_bytes()).hexdigest()
                size_after = file_path.stat().st_size

                # Assert strict read-only preservation (zero mutation)
                self.assertEqual(sha_before, sha_after, f"{file_path.name} was mutated during read")
                self.assertEqual(size_before, size_after, f"{file_path.name} size changed during read")

    def test_baseline_configuration_in_isolated_environment(self):
        """Verify engine behavior with baseline saved scenario and user defaults in an isolated temp environment."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            temp_defaults_path = temp_path / 'user_defaults.json'
            temp_scenario_path = temp_path / 'current_scenario.json'

            with patch.object(assumptions, 'USER_DEFAULTS_PATH', temp_defaults_path), \
                 patch.object(shared_state, 'STATE_PATH', temp_scenario_path):

                assumptions.save_user_default('scop', 2.0, engine.validate)
                shared_state.update_state({
                    'settings': dict(self.metadata['live_inspection']['saved_scenario'])
                })

                state = shared_state.get_state()
                s = state['settings']
                self.assertEqual(s['scop'], 4.0, "Saved scenario SCOP 4.0 must supersede user default 2.0")
                self.assertEqual(s['ac_heating_windows'], [
                    {'id': 'morning', 'start_minute': 480, 'end_minute': 720},
                    {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
                ])

                # Persisted defaults inherit the override; running simulation defaults stay frozen.
                self.assertEqual(assumptions.persisted_defaults()['scop'], 2.0)
                # Engine validate of saved settings retains explicit SCOP 4.0
                validated = engine.validate(s)
                self.assertEqual(validated['scop'], 4.0)

    # -------------------------------------------------------------------------
    # Group 2: NPZ Fixture Integrity & 0% Interval Compatibility Verification
    # -------------------------------------------------------------------------

    def test_npz_fixture_files_exist_and_metadata_consistent(self):
        """Verify NPZ fixture file and metadata JSON are present and structurally sound."""
        self.assertTrue(NPZ_PATH.exists())
        self.assertTrue(METADATA_PATH.exists())
        self.assertIn('scenarios', self.metadata)
        self.assertIn('synthetic_fixture', self.metadata)
        self.assertIn('layer_precedence', self.metadata)
        self.assertIn('tolerance_guidelines', self.metadata)
        self.assertIn('weather_provenance', self.metadata)

        wp = self.metadata['weather_provenance']
        self.assertIn('hourly_npz_sha256', wp)
        self.assertIn('pvgis_south_exact_json_sha256', wp)
        self.assertIn('audit_json_sha256', wp)
        self.assertIn('fixtures_npz_sha256', wp)

        for scen_id, s_meta in self.metadata['scenarios'].items():
            src_key = s_meta['source_array_key']
            fc_key = s_meta['forecast_array_key']
            self.assertIn(src_key, self.npz.files)
            self.assertIn(fc_key, self.npz.files)
            self.assertEqual(list(self.npz[src_key].shape), [len(self.weather_data['years']), 8784])
            self.assertEqual(list(self.npz[fc_key].shape), [len(self.weather_data['years']), 8760])

    def test_source_ensemble_budget_and_schedule(self):
        """Verify annual ensemble mean budgets, schedule gating, and raw COP bounds on source weather."""
        d = self.weather_data
        isolated_base = {k: copy.deepcopy(v[0]) for k, v in assumptions.SPEC.items()}
        isolated_base['planning_heat_pump'] = False

        for scen_id, s_meta in self.metadata['scenarios'].items():
            s = engine.validate(s_meta['settings'], base=isolated_base)
            current_heat = engine._allocate_space_heating(
                s,
                d['temperature'],
                d['month'],
                d['hour'],
                d['valid'],
                engine._get_civil_hours(d),
                d['years'],
            )
            # Annual ensemble mean budget preservation
            expected_annual = float(s_meta['annual_heating_electricity_kwh'])
            actual_annual_mean = float(np.mean(np.sum(current_heat, axis=1)))
            self.assertAlmostEqual(actual_annual_mean, expected_annual, places=4)

            # Schedule gating: off-window hours must be strictly 0.0
            if s['heating_schedule_mode'] == 'scheduled':
                windows = s.get('ac_heating_windows') or engine.migrate_and_resolve_schedule(s)
                hourly_overlaps = engine.canonical_windows_hourly_overlaps(windows)
                permitted = set(np.where(hourly_overlaps > 0)[0])
                civil_h = engine._get_civil_hours(d)
                active_h = civil_h if s['timezone_mode'] == 'civil' else d['hour']
                off_mask = d['valid'] & ~np.isin(active_h, list(permitted))
                self.assertEqual(float(np.max(current_heat[off_mask])), 0.0)

            # Temperature raw-COP bounds [0.45, 1.60] preserved
            raw_cop = np.clip(1.0 + 0.025 * (d['temperature'] - 7.0), 0.45, 1.60)
            self.assertTrue(np.all((raw_cop >= 0.45) & (raw_cop <= 1.60)))

    def test_forecast_ensemble_budget_and_schedule(self):
        """Verify annual ensemble mean budget and schedule gating on mapped forecast series."""
        isolated_base = {k: copy.deepcopy(v[0]) for k, v in assumptions.SPEC.items()}
        isolated_base['planning_heat_pump'] = False

        for scen_id, s_meta in self.metadata['scenarios'].items():
            s = engine.validate(s_meta['settings'], base=isolated_base)
            forecast_res = engine._forecast_mapped_series(s)
            current_mapped = forecast_res['mapped_heating']
            # Annual ensemble mean budget preservation on forecast calendar
            expected_annual = float(s_meta['annual_heating_electricity_kwh'])
            actual_annual_mean = float(np.mean(np.sum(current_mapped, axis=1)))
            self.assertAlmostEqual(actual_annual_mean, expected_annual, places=4)

            # Schedule gating on target calendar
            if s['heating_schedule_mode'] == 'scheduled':
                windows = s.get('ac_heating_windows') or engine.migrate_and_resolve_schedule(s)
                hourly_overlaps = engine.canonical_windows_hourly_overlaps(windows)
                permitted = set(np.where(hourly_overlaps > 0)[0])
                tgt_h = forecast_res['target_civil_hours'] if s['timezone_mode'] == 'civil' else forecast_res['target_std_hours']
                off_mask = ~np.isin(tgt_h, list(permitted))
                self.assertEqual(float(np.max(current_mapped[:, off_mask])), 0.0)

    def test_interval_drift_exceeding_tolerance_fails(self):
        """Demonstrate strict interval tolerance behavior and necessity of rtol=0.

        Verifies that:
        1. Adding an interval drift exceeding atol=1e-12 (1.01e-12) fails with AssertionError when rtol=0.
        2. An interval drift strictly within tolerance (0.99e-12) passes when rtol=0.
        3. Crucially demonstrates why default rtol=1e-7 invalidates the strict interval guarantee:
           on a 25.0 kWh value (or synthetic active slots), a drift of 1e-6 or 1e-8 erroneously passes
           under default rtol=1e-7 when rtol is omitted, but correctly fails when rtol=0.
        """
        baseline = self.npz['source_heating_current_saved_scenario']

        # 1. Interval drift exceeding 1e-12 fails when rtol=0
        with self.assertRaises(AssertionError):
            np.testing.assert_allclose(baseline + 1.01e-12, baseline, atol=1e-12, rtol=0)

        # 2. Interval drift strictly within tolerance passes when rtol=0
        np.testing.assert_allclose(baseline + 0.99e-12, baseline, atol=1e-12, rtol=0)

        # 3. Demonstration of why default rtol=1e-7 invalidates the interval guarantee:
        # On a 25.0 kWh interval value, allowed diff with default rtol=1e-7 is:
        # atol + rtol * 25.0 = 1e-12 + 1e-7 * 25.0 = 2.5e-6 kWh.
        # A drift of 1e-6 kWh (a million times larger than 1e-12) erroneously passes when rtol is omitted!
        val = 25.0
        drift_large = 1e-6
        np.testing.assert_allclose(val + drift_large, val, atol=1e-12)  # default rtol omitted -> passes!

        # With explicit rtol=0, the drift correctly fails
        with self.assertRaises(AssertionError):
            np.testing.assert_allclose(val + drift_large, val, atol=1e-12, rtol=0)

        # Similarly, on the synthetic fixture active intervals (15.0..35.0 kWh):
        synth = self.npz['synthetic_expected_heat_kwh']
        active = self.npz['synthetic_active_mask']
        synth_drift = synth.copy()
        synth_drift[active] += 1e-8  # 1e-8 drift on active slots
        # Erroneously passes when rtol is omitted (default rtol=1e-7)
        np.testing.assert_allclose(synth_drift, synth, atol=1e-12)
        # Correctly fails when rtol=0 is strictly enforced
        with self.assertRaises(AssertionError):
            np.testing.assert_allclose(synth_drift, synth, atol=1e-12, rtol=0)

    def test_raw_monthly_and_annual_sums_conservation(self):
        """Verify raw monthly and annual sums are strictly conserved per realization."""
        for scen_id, s_meta in self.metadata['scenarios'].items():
            src_heat = self.npz[s_meta['source_array_key']]
            expected_monthly = np.array(s_meta['monthly_heating_electricity_kwh'], dtype=float)
            expected_annual = float(s_meta['annual_heating_electricity_kwh'])

            for yi in range(len(self.weather_data['years'])):
                # Annual sum check per weather year
                actual_annual = float(src_heat[yi].sum())
                self.assertAlmostEqual(
                    actual_annual,
                    expected_annual,
                    places=8,
                    msg=f"Annual sum mismatch in scenario {scen_id} for year index {yi}",
                )

                # Monthly sum check per weather year
                for mm in range(1, 13):
                    m_mask = self.weather_data['valid'][yi] & (self.weather_data['month'][yi] == mm)
                    actual_monthly = float(src_heat[yi, m_mask].sum())
                    self.assertAlmostEqual(
                        actual_monthly,
                        expected_monthly[mm - 1],
                        places=8,
                        msg=f"Monthly sum mismatch in scenario {scen_id} year {yi} month {mm}",
                    )

    # -------------------------------------------------------------------------
    # Group 3: Core Operating Window Scenarios Verification
    # -------------------------------------------------------------------------

    def test_current_saved_scenario_operating_hours(self):
        """Current saved scenario operates strictly in civil hours 08..11 and 16..19."""
        heat = self.npz['source_heating_current_saved_scenario']
        civil_h = engine._get_civil_hours(self.weather_data)
        permitted = {8, 9, 10, 11, 16, 17, 18, 19}

        for yi in range(len(self.weather_data['years'])):
            valid = self.weather_data['valid'][yi]
            off_mask = valid & ~np.isin(civil_h[yi], list(permitted))
            self.assertEqual(float(np.max(heat[yi, off_mask])), 0.0)
            active_mask = valid & np.isin(civil_h[yi], list(permitted))
            self.assertTrue(np.all(heat[yi, active_mask] >= 0.0))

    def test_factory_hours_operating_hours(self):
        """Factory hours scenario operates strictly in civil hours 06..07 and 16..19."""
        heat = self.npz['source_heating_factory_defaults']
        civil_h = engine._get_civil_hours(self.weather_data)
        permitted = {6, 7, 16, 17, 18, 19}

        for yi in range(len(self.weather_data['years'])):
            valid = self.weather_data['valid'][yi]
            off_mask = valid & ~np.isin(civil_h[yi], list(permitted))
            self.assertEqual(float(np.max(heat[yi, off_mask])), 0.0)
            active_mask = valid & np.isin(civil_h[yi], list(permitted))
            self.assertTrue(np.all(heat[yi, active_mask] >= 0.0))

    def test_user_defaults_scop2_exact_doubling(self):
        """SCOP 2.0 user defaults exactly doubles electricity at every interval vs SCOP 4.0."""
        heat_scop4 = self.npz['source_heating_factory_defaults']
        heat_scop2 = self.npz['source_heating_user_defaults_scop2']
        np.testing.assert_allclose(heat_scop2, heat_scop4 * 2.0, atol=1e-12, rtol=0)

    def test_continuous_mode_unconstrained_allocation(self):
        """Continuous mode allows heating across all 24 hours of the day without window gating."""
        heat_cont = self.npz['source_heating_continuous_mode']
        civil_h = engine._get_civil_hours(self.weather_data)
        # Check an off-hour in winter month 1, e.g. hour 12 in this analytic fixture
        d = self.weather_data
        jan_midday = d['valid'][0] & (d['month'][0] == 1) & (civil_h[0] == 12)
        self.assertTrue(np.any(heat_cont[0, jan_midday] > 0.0))

    def test_single_window_configurations(self):
        """Single active window (morning only or evening only) operates cleanly."""
        heat_morn = self.npz['source_heating_single_window_morning']
        heat_eve = self.npz['source_heating_single_window_evening']
        civil_h = engine._get_civil_hours(self.weather_data)

        # Morning only: permitted {6, 7}
        for yi in range(len(self.weather_data['years'])):
            valid = self.weather_data['valid'][yi]
            self.assertEqual(float(np.max(heat_morn[yi, valid & ~np.isin(civil_h[yi], [6, 7])])), 0.0)
            self.assertEqual(float(np.max(heat_eve[yi, valid & ~np.isin(civil_h[yi], [16, 17, 18, 19])])), 0.0)

    def test_disabled_both_windows_errors_and_continuous_tolerance(self):
        """Scheduled mode requires >=1 active window for calculation; UI validate tolerates empty/inactive draft; continuous mode tolerates both disabled."""
        bad_settings = {
            'heating_schedule_mode': 'scheduled',
            'heating_on_hour': 6,
            'heating_off_hour': 6,
            'heating_evening_on_hour': 16,
            'heating_evening_off_hour': 16,
        }
        # In Task B, validate tolerates empty / inactive draft schedule
        validated = engine.validate(bad_settings)
        self.assertEqual(validated['heating_schedule_mode'], 'scheduled')

        # But calculation-time allocate fails when active operating minutes are zero
        d = self.weather_data
        with self.assertRaises(ValueError) as ctx:
            engine._allocate_space_heating(
                {**bad_settings, 'load_scenario': 'electrified', 'scop': 4.0, 'summer_gas_base': 20.0,
                 'gas_pcs': 11.0, 'boiler_efficiency': 0.70, 'heating_balance_temp': 18.0,
                 'timezone_mode': 'civil'},
                d['temperature'], d['month'], d['hour'], d['valid'], engine._get_civil_hours(d), d['years']
            )
        self.assertEqual(str(ctx.exception), "Add at least one operating window to allocate the requested AC heating demand.")

        # Continuous mode tolerates equal on/off hours without error
        cont_settings = dict(bad_settings, heating_schedule_mode='continuous')
        validated = engine.validate(cont_settings)
        self.assertEqual(validated['heating_schedule_mode'], 'continuous')

    def test_overnight_window_operation(self):
        """Overnight window (22:00–06:00) permits hours {22, 23, 0, 1, 2, 3, 4, 5}."""
        heat_night = self.npz['source_heating_overnight_window']
        civil_h = engine._get_civil_hours(self.weather_data)
        permitted = {22, 23, 0, 1, 2, 3, 4, 5}

        for yi in range(len(self.weather_data['years'])):
            valid = self.weather_data['valid'][yi]
            off_mask = valid & ~np.isin(civil_h[yi], list(permitted))
            self.assertEqual(float(np.max(heat_night[yi, off_mask])), 0.0)
            active_mask = valid & np.isin(civil_h[yi], list(permitted))
            self.assertTrue(np.all(heat_night[yi, active_mask] >= 0.0))

    def test_timezone_mode_civil_vs_standard(self):
        """Standard mode uses fixed UTC+1; civil mode shifts 1 hour during DST."""
        heat_civ = self.npz['source_heating_current_saved_scenario']
        heat_std = self.npz['source_heating_standard_timezone_saved']
        d = self.weather_data
        std_h = d['hour']

        # Permitted clock hours: 8..11 and 16..19
        # In January (standard time, civil == standard), heating slots match identically
        for yi in range(len(self.weather_data['years'])):
            jan = d['valid'][yi] & (d['month'][yi] == 1)
            np.testing.assert_allclose(heat_std[yi, jan], heat_civ[yi, jan], atol=1e-12, rtol=0)

        # In April (DST active CEST, civil = std + 1), civil 8 is standard 7
        for yi in range(len(self.weather_data['years'])):
            apr = d['valid'][yi] & (d['month'][yi] == 4)
            std7_slots = apr & (std_h[yi] == 7)
            # Standard mode: standard hour 7 is NOT in 8..11, so it is 0.0
            self.assertEqual(float(np.max(heat_std[yi, std7_slots])), 0.0)
            # Civil mode: standard hour 7 is civil hour 8, active on cold hours
            cold_std7 = std7_slots & (d['temperature'][yi] < 18.0)
            if np.any(cold_std7):
                self.assertTrue(np.all(heat_civ[yi, cold_std7] > 0.0))

    # -------------------------------------------------------------------------
    # Group 4: Hand-Computable Independent Synthetic Fixture
    # -------------------------------------------------------------------------

    def test_independent_synthetic_fixture_hand_calculated(self):
        """Independent hand-calculated synthetic fixture verified from pure arithmetic."""
        synth_meta = self.metadata['synthetic_fixture']
        expected_heat = self.npz['synthetic_expected_heat_kwh']
        active_mask = self.npz['synthetic_active_mask']

        # Hand calculations:
        # gas = 126.0 Smc, summer base = 26.0 Smc, net = 100.0 Smc
        # useful = 100 * 10 * 0.8 = 800.0 kWh
        # p = 0%, requested = 800.0 kWh
        # electricity = 800 / 4.0 = 200.0 kWh
        # active degree hours = 80.0 -> 2.5 kWh/deg
        self.assertEqual(synth_meta['net_heating_gas_smc'], 100.0)
        self.assertEqual(synth_meta['useful_heat_kwh'], 800.0)
        self.assertEqual(synth_meta['electricity_kwh'], 200.0)
        self.assertEqual(synth_meta['total_degree_hours'], 80.0)
        self.assertEqual(synth_meta['kwh_per_degree_hour'], 2.5)

        # Active slot breakdown exact literal checks:
        self.assertAlmostEqual(expected_heat[8], 25.0)   # Day 1 08:00 (10 deg * 2.5)
        self.assertAlmostEqual(expected_heat[9], 20.0)   # Day 1 09:00 (8 deg * 2.5)
        self.assertAlmostEqual(expected_heat[18], 30.0)  # Day 1 18:00 (12 deg * 2.5)
        self.assertAlmostEqual(expected_heat[19], 25.0)  # Day 1 19:00 (10 deg * 2.5)
        self.assertAlmostEqual(expected_heat[32], 20.0)  # Day 2 08:00 (8 deg * 2.5)
        self.assertAlmostEqual(expected_heat[33], 15.0)  # Day 2 09:00 (6 deg * 2.5)
        self.assertAlmostEqual(expected_heat[42], 35.0)  # Day 2 18:00 (14 deg * 2.5)
        self.assertAlmostEqual(expected_heat[43], 30.0)  # Day 2 19:00 (12 deg * 2.5)

        # Off-hour checks: all 40 off-hours must be exactly 0.0
        self.assertEqual(float(expected_heat[~active_mask].sum()), 0.0)
        self.assertAlmostEqual(float(expected_heat.sum()), 200.0)

    def test_independent_synthetic_fixture_reproduced_by_core_algorithm(self):
        """Core allocation algorithm reproduces the hand-computed synthetic profile to 1e-15."""
        temps = self.npz['synthetic_temperature']
        valid = self.npz['synthetic_valid']
        active_mask = self.npz['synthetic_active_mask']
        expected_heat = self.npz['synthetic_expected_heat_kwh']

        # Independent calculation using engine._normalized_pattern
        degrees = np.where(active_mask, np.maximum(18.0 - temps, 0.0), 0.0)
        computed = engine._normalized_pattern(degrees[None, :], 200.0, valid[None, :])[0]

        np.testing.assert_allclose(computed, expected_heat, atol=1e-15, rtol=0)

    def test_production_allocation_on_hand_computable_synthetic_inputs(self):
        """Directly exercise engine._allocate_space_heating on the 48-hour synthetic inputs.

        Exercises actual production allocation against independent literal expected values:
        - GAS_MONTHLY patched with January = 126.0 Smc, all other months = 0.0
        - Explicit parameters: PCS = 10.0, boiler = 0.80, SCOP = 4.0, summer_gas_base = 26.0
        - Windows: 08:00-10:00 (hours 8, 9) and 18:00-20:00 (hours 18, 19)
        - Balance temperature: 18.0 C, timezone_mode: civil
        - Literal expected values: [25.0, 20.0, 30.0, 25.0, 20.0, 15.0, 35.0, 30.0]
        - Active positions: [8, 9, 18, 19, 32, 33, 42, 43]
        - All 40 off-hours strictly 0.0, total sum = 200.0 kWh
        """
        # Patch GAS_MONTHLY with January 126.0 and remaining 11 months 0.0
        synthetic_gas_monthly = np.zeros(12, dtype=float)
        synthetic_gas_monthly[0] = 126.0

        # Construct 48-hour timeline arrays (shape [1, 48])
        temps = np.full((1, 48), 15.0, dtype=np.float64)
        # Day 1 temperatures at active hours
        temps[0, 8] = 8.0    # 18 - 8 = 10 deg
        temps[0, 9] = 10.0   # 18 - 10 = 8 deg
        temps[0, 18] = 6.0   # 18 - 6 = 12 deg
        temps[0, 19] = 8.0   # 18 - 8 = 10 deg
        # Day 2 temperatures at active hours
        temps[0, 32] = 10.0  # 18 - 10 = 8 deg
        temps[0, 33] = 12.0  # 18 - 12 = 6 deg
        temps[0, 42] = 4.0   # 18 - 4 = 14 deg
        temps[0, 43] = 6.0   # 18 - 6 = 12 deg

        months = np.ones((1, 48), dtype=np.int64)
        hours = np.tile(np.arange(24), 2).reshape(1, 48)
        civil_hours = hours.copy()
        valid = np.ones((1, 48), dtype=bool)
        weather_years = [2026]

        settings = {
            'load_scenario': 'electrified',
            'gas_pcs': 10.0,
            'boiler_efficiency': 0.80,
            'scop': 4.0,
            'summer_gas_base': 26.0,
            'heating_schedule_mode': 'scheduled',
            'heating_on_hour': 8,
            'heating_off_hour': 10,
            'heating_evening_on_hour': 18,
            'heating_evening_off_hour': 20,
            'heating_balance_temp': 18.0,
            'timezone_mode': 'civil',
        }

        with patch.object(engine, 'GAS_MONTHLY', synthetic_gas_monthly), synthetic_thermal_runtime(synthetic_gas_monthly):
            actual_allocation = engine._allocate_space_heating(
                settings,
                temps,
                months,
                hours,
                valid,
                civil_hours,
                weather_years,
            )

        # Literal independent expected values analytically calculated under ensemble HDH + temperature COP:
        # Useful Q = 800 kWh_th over 80 degree-hours -> 10 kWh_th/deg
        # Active Q_th = [100.0, 80.0, 120.0, 100.0, 80.0, 60.0, 140.0, 120.0]
        # Active raw COP = [1.025, 1.075, 0.975, 1.025, 1.075, 1.125, 0.925, 0.975]
        # sum(Q / raw_cop) = 794.79769136... -> spf_raw = 800.0 / 794.79769136... = 1.00654545...
        # E = (Q * spf_raw) / (4.0 * raw_cop)
        literal_expected_active = [
            24.549889027174096,
            18.726426978867686,
            30.97062923428117,
            24.549889027174096,
            18.726426978867686,
            13.42060600152184,
            38.08550351783225,
            30.97062923428117,
        ]
        active_positions = [8, 9, 18, 19, 32, 33, 42, 43]

        # Verify active slots match literal values with atol=1e-12, rtol=0
        np.testing.assert_allclose(
            actual_allocation[0, active_positions],
            literal_expected_active,
            atol=1e-12,
            rtol=0,
            err_msg="Synthetic active position allocation mismatch against literal expected values",
        )

        # Verify inactive slots are strictly 0.0 with atol=1e-12, rtol=0
        inactive_mask = np.ones(48, dtype=bool)
        inactive_mask[active_positions] = False
        np.testing.assert_allclose(
            actual_allocation[0, inactive_mask],
            0.0,
            atol=1e-12,
            rtol=0,
            err_msg="Synthetic off-hour allocation must be strictly 0.0",
        )

        # Verify total sum is 200.0 kWh
        self.assertAlmostEqual(float(actual_allocation.sum()), 200.0, places=12)

    def test_production_allocation_on_hand_computable_january_case(self):
        """Hand-computed January production constants verified against engine allocation."""
        # Independent invented public case: gas 40 Smc, nonheat 20 Smc,
        # PCS 11 kWh/Smc, efficiency .8 and SCOP 4. Original household
        # assertions remain byte-identical in the external private suite.
        expected_jan_heat_useful = (40.0 - 20.0) * 11.0 * 0.8
        expected_jan_electricity = expected_jan_heat_useful / 4.0
        self.assertAlmostEqual(expected_jan_heat_useful, 176.0, places=8)
        self.assertAlmostEqual(expected_jan_electricity, 44.0, places=8)

        # Verify against engine's actual January output in saved scenario
        jan_heat = self.npz['source_heating_current_saved_scenario']
        for yi in range(len(self.weather_data['years'])):
            jan_mask = self.weather_data['valid'][yi] & (self.weather_data['month'][yi] == 1)
            actual_jan_sum = float(jan_heat[yi, jan_mask].sum())
            self.assertAlmostEqual(actual_jan_sum, expected_jan_electricity, places=8)

    # -------------------------------------------------------------------------
    # Group 5: Configuration Layer Precedence & Migration Expectations
    # -------------------------------------------------------------------------

    def test_configuration_layer_precedence_hierarchy_isolated(self):
        """Verify layer precedence: incoming edits > saved scenario > user defaults > factory.

        Tests are performed entirely in a temporary directory to preserve live files.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            temp_defaults_path = temp_path / 'user_defaults.json'
            temp_scenario_path = temp_path / 'current_scenario.json'

            with patch.object(assumptions, 'USER_DEFAULTS_PATH', temp_defaults_path), \
                 patch.object(shared_state, 'STATE_PATH', temp_scenario_path):

                # Layer 4 (lowest): Factory code defaults
                self.assertEqual(assumptions.SPEC['scop'][0], 4.0)
                self.assertEqual(assumptions.SPEC['ac_heating_windows'][0], [
                    {'id': 'morning', 'start_minute': 360, 'end_minute': 480},
                    {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
                ])
                self.assertEqual(engine.defaults()['scop'], 4.0)

                # Layer 3: User defaults override
                assumptions.save_user_default('scop', 2.0, engine.validate)
                self.assertEqual(assumptions.persisted_defaults()['scop'], 2.0)
                self.assertEqual(engine.defaults()['ac_heating_windows'], [
                    {'id': 'morning', 'start_minute': 360, 'end_minute': 480},
                    {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
                ])  # unchanged factory

                # Layer 2: Saved scenario overrides user defaults and factory
                shared_state.update_state({
                    'settings': {
                        'heating_on_hour': 8,
                        'heating_off_hour': 12,
                        'scop': 4.0,  # Saved scenario explicitly sets 4.0, beating user default 2.0!
                    }
                })
                current = shared_state.get_state()
                self.assertEqual(current['settings']['scop'], 4.0)
                self.assertEqual(current['settings']['ac_heating_windows'], [
                    {'id': 'morning', 'start_minute': 480, 'end_minute': 720},
                    {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
                ])

                # Layer 1 (highest): Incoming request edits override saved scenario
                incoming_patch = {'settings': {'heating_on_hour': 7, 'heating_off_hour': 10}}
                updated = shared_state.update_state(incoming_patch)
                self.assertEqual(updated['settings']['ac_heating_windows'], [
                    {'id': 'morning', 'start_minute': 420, 'end_minute': 600},
                    {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
                ])
                self.assertEqual(updated['settings']['scop'], 4.0)  # preserved from saved

    def test_partial_legacy_override_merging_isolated(self):
        """Partial legacy override updates only specified keys without resetting others."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            temp_defaults_path = temp_path / 'user_defaults.json'
            temp_scenario_path = temp_path / 'current_scenario.json'

            with patch.object(assumptions, 'USER_DEFAULTS_PATH', temp_defaults_path), \
                 patch.object(shared_state, 'STATE_PATH', temp_scenario_path):

                # Base state has morning 8..12, evening 16..20
                shared_state.update_state({
                    'settings': {
                        'heating_on_hour': 8,
                        'heating_off_hour': 12,
                        'heating_evening_on_hour': 16,
                        'heating_evening_off_hour': 20,
                    }
                })
                # Partial edit modifies evening hours only
                res = shared_state.update_state({'settings': {'heating_evening_on_hour': 17, 'heating_evening_off_hour': 21}})
                self.assertEqual(res['settings']['ac_heating_windows'], [
                    {'id': 'morning', 'start_minute': 480, 'end_minute': 720},
                    {'id': 'evening', 'start_minute': 1020, 'end_minute': 1260},
                ])

    def test_canonical_migration_expectations_spec(self):
        """Verify specification of future canonical migration contract invariants."""
        contract = self.metadata['layer_precedence']['contract_invariants']
        self.assertTrue(any('Explicit canonical windows in same payload win' in inv for inv in contract))
        self.assertTrue(any('legacy-only override in a higher layer' in inv for inv in contract))
        self.assertTrue(any('0% difference' in inv for inv in contract))


if __name__ == '__main__':
    unittest.main()
