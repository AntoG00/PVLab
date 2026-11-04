"""Unit tests for Task B canonical AC heating schedule and layer-aware migration."""
from heating_oracle import heating_oracle
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

import numpy as np

import feasibility.assumptions as assumptions
import feasibility.engine as engine
import feasibility.server as server
import feasibility.shared_state as shared_state
import test_support
from historical_weather import frozen_baseline_weather_context
from feasibility.schedule import (
    DEFAULT_AC_HEATING_WINDOWS,
    LEGACY_HEATING_KEYS,
    SCHEDULE_SETTINGS,
    canonical_windows_hourly_overlaps,
    canonical_windows_minute_mask,
    clear_diagnostics,
    freeze,
    get_diagnostics,
    migrate_and_resolve_schedule,
    thaw,
    thaw_settings,
    validate_ac_heating_windows,
)


class TaskBCanonicalScheduleTests(unittest.TestCase):
    def setUp(self):
        clear_diagnostics()
        engine._load_cache.cache_clear()
        engine._energy_cache.cache_clear()
        engine._chart_cache.cache_clear()
        engine._dayhour_trace.cache_clear()
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        tpath = Path(self._temp_dir.name)
        p_defaults = patch.object(assumptions, 'USER_DEFAULTS_PATH', tpath / 'user_defaults.json')
        p_state = patch.object(shared_state, 'STATE_PATH', tpath / 'current_scenario.json')
        p_defaults.start()
        p_state.start()
        self.addCleanup(p_defaults.stop)
        self.addCleanup(p_state.stop)

    def tearDown(self):
        clear_diagnostics()

    # -------------------------------------------------------------------------
    # 1. Canonical Schema & Strict Validation
    # -------------------------------------------------------------------------

    def test_valid_canonical_windows_formats(self):
        """Valid canonical windows pass schema validation and assign auto-ids."""
        # Standard 2 windows
        w = [
            {'id': 'morning', 'start_minute': 360, 'end_minute': 480},
            {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
        ]
        val = validate_ac_heating_windows(w)
        self.assertEqual(val, w)

        # Empty schedule (valid draft)
        self.assertEqual(validate_ac_heating_windows([]), [])

        # Inactive window (start == end)
        inactive = [{'id': 'w1', 'start_minute': 400, 'end_minute': 400}]
        self.assertEqual(validate_ac_heating_windows(inactive), inactive)

        # Overnight wrap (start > end, e.g. 22:00 to 06:00 = 1320 to 360)
        overnight = [{'id': 'night', 'start_minute': 1320, 'end_minute': 360}]
        self.assertEqual(validate_ac_heating_windows(overnight), overnight)

        # Auto-assignment of missing id
        no_id = [{'start_minute': 400, 'end_minute': 500}]
        val_no_id = validate_ac_heating_windows(no_id)
        self.assertEqual(val_no_id[0]['id'], 'window_1')

        # Multi-window (>2 windows, arbitrary custom IDs)
        three = [
            {'id': 'w1', 'start_minute': 360, 'end_minute': 480},
            {'id': 'lunch', 'start_minute': 720, 'end_minute': 780},
            {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
        ]
        self.assertEqual(validate_ac_heating_windows(three), three)

    def test_strict_rejection_of_invalid_windows(self):
        """Strictly reject booleans, floats, out of bounds, unknown fields, non-lists."""
        bad_cases = [
            'not_a_list',
            None,
            123,
            [{'start_minute': 360}],  # missing end_minute
            [{'end_minute': 480}],    # missing start_minute
            [{'start_minute': True, 'end_minute': 480}],   # boolean start
            [{'start_minute': 360, 'end_minute': False}],  # boolean end
            [{'start_minute': 360.5, 'end_minute': 480}],  # float start
            [{'start_minute': 360, 'end_minute': 480.0}],  # float end
            [{'start_minute': -1, 'end_minute': 480}],     # negative start
            [{'start_minute': 1440, 'end_minute': 1440}],  # start_minute max is 1439
            [{'start_minute': 360, 'end_minute': 1441}],   # end_minute max is 1440
            [{'id': '', 'start_minute': 360, 'end_minute': 480}],  # empty id
            [{'id': 123, 'start_minute': 360, 'end_minute': 480}], # non-string id
            [{'id': 'w1', 'start_minute': 360, 'end_minute': 480, 'unknown': 'field'}],  # extra field
            [{}],  # empty object
        ]
        for bad in bad_cases:
            with self.assertRaises(ValueError, msg=f"Should reject: {bad}"):
                validate_ac_heating_windows(bad)

    # -------------------------------------------------------------------------
    # 2. Minute Overlap Adapter & Task A Equivalence
    # -------------------------------------------------------------------------

    def test_minute_mask_and_hourly_overlaps(self):
        """Minute mask and hourly overlaps correctly compute minute weights."""
        # Disjoint daytime windows: 06:00..08:00 (360..480) and 16:00..20:00 (960..1200)
        windows = [
            {'id': 'morning', 'start_minute': 360, 'end_minute': 480},
            {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
        ]
        mask = canonical_windows_minute_mask(windows)
        self.assertEqual(mask.shape, (1440,))
        self.assertEqual(mask.dtype, bool)
        self.assertEqual(int(mask.sum()), (2 + 4) * 60)

        overlaps = canonical_windows_hourly_overlaps(windows)
        self.assertEqual(overlaps.shape, (24,))
        self.assertEqual(overlaps[6], 60.0)
        self.assertEqual(overlaps[7], 60.0)
        self.assertEqual(overlaps[8], 0.0)
        self.assertEqual(overlaps[16], 60.0)
        self.assertEqual(overlaps[17], 60.0)
        self.assertEqual(overlaps[18], 60.0)
        self.assertEqual(overlaps[19], 60.0)
        self.assertEqual(overlaps[20], 0.0)

        # Overnight window: 22:30 to 01:15 (1350 to 75)
        night_win = [{'id': 'night', 'start_minute': 1350, 'end_minute': 75}]
        night_overlaps = canonical_windows_hourly_overlaps(night_win)
        self.assertEqual(night_overlaps[22], 30.0)  # 22:30 to 23:00 = 30 min
        self.assertEqual(night_overlaps[23], 60.0)  # 23:00 to 24:00 = 60 min
        self.assertEqual(night_overlaps[0], 60.0)   # 00:00 to 01:00 = 60 min
        self.assertEqual(night_overlaps[1], 15.0)   # 01:00 to 01:15 = 15 min
        self.assertEqual(night_overlaps[2], 0.0)

        # Overlapping windows union without double counting
        overlapping = [
            {'id': 'w1', 'start_minute': 360, 'end_minute': 480},  # 6..8
            {'id': 'w2', 'start_minute': 420, 'end_minute': 540},  # 7..9
        ]
        union_overlaps = canonical_windows_hourly_overlaps(overlapping)
        self.assertEqual(union_overlaps[6], 60.0)
        self.assertEqual(union_overlaps[7], 60.0)
        self.assertEqual(union_overlaps[8], 60.0)
        self.assertEqual(union_overlaps[9], 0.0)

    def test_bit_for_bit_equivalence_with_task_a_fixtures(self):
        """Verify whole-hour canonical windows match Task A NPZ fixtures to atol=1e-12, rtol=0."""
        self.enterContext(frozen_baseline_weather_context())
        fixture_path = Path(os.environ['PV_LEGACY_DATA_ROOT']).parents[1] / 'compatibility/ac_heating_compatibility_fixtures.npz'
        self.assertTrue(fixture_path.exists())
        npz = self.enterContext(np.load(fixture_path))
        d = engine.data()

        # Test baseline saved scenario (morning 8..12, evening 16..20)
        baseline_windows = [
            {'id': 'morning', 'start_minute': 480, 'end_minute': 720},
            {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
        ]
        s_baseline = {
            'load_scenario': 'electrified',
            'scop': 4.0,
            'summer_gas_base': 20.0,
            'gas_pcs': 11.0,
            'boiler_efficiency': 0.80,
            'heating_balance_temp': 18.0,
            'heating_schedule_mode': 'scheduled',
            'ac_heating_windows': baseline_windows,
            'timezone_mode': 'civil',
        }
        res_baseline = engine._allocate_space_heating(
            s_baseline,
            d['temperature'], d['month'], d['hour'], d['valid'], engine._get_civil_hours(d), d['years']
        )
        expected = heating_oracle(s_baseline, d['temperature'], d['month'], d['hour'], d['valid'],
                                  engine._get_civil_hours(d), engine.GAS_MONTHLY)
        np.testing.assert_allclose(res_baseline, expected, rtol=0, atol=1e-12)
        legacy = heating_oracle(s_baseline, d['temperature'], d['month'], d['hour'], d['valid'],
                                engine._get_civil_hours(d), engine.GAS_MONTHLY, legacy=True)
        np.testing.assert_allclose(
            legacy,
            npz['source_heating_current_saved_scenario'],
            rtol=0.0,
            atol=1e-12,
            err_msg="Baseline saved scenario failed bit-for-bit equivalence with Task A fixture"
        )

        # Test factory hours (morning 6..8, evening 16..20)
        factory_windows = copy.deepcopy(DEFAULT_AC_HEATING_WINDOWS)
        s_factory = dict(s_baseline, ac_heating_windows=factory_windows)
        res_factory = engine._allocate_space_heating(
            s_factory,
            d['temperature'], d['month'], d['hour'], d['valid'], engine._get_civil_hours(d), d['years']
        )
        expected = heating_oracle(s_factory, d['temperature'], d['month'], d['hour'], d['valid'],
                                  engine._get_civil_hours(d), engine.GAS_MONTHLY)
        np.testing.assert_allclose(res_factory, expected, rtol=0, atol=1e-12)
        legacy = heating_oracle(s_factory, d['temperature'], d['month'], d['hour'], d['valid'],
                                engine._get_civil_hours(d), engine.GAS_MONTHLY, legacy=True)
        np.testing.assert_allclose(
            legacy,
            npz['source_heating_factory_defaults'],
            rtol=0.0,
            atol=1e-12,
            err_msg="Factory hours scenario failed bit-for-bit equivalence with Task A fixture"
        )

        # Test overnight window (22:00 to 06:00 = 1320 to 360)
        overnight_windows = [{'id': 'night', 'start_minute': 1320, 'end_minute': 360}]
        s_overnight = dict(s_baseline, ac_heating_windows=overnight_windows)
        res_overnight = engine._allocate_space_heating(
            s_overnight,
            d['temperature'], d['month'], d['hour'], d['valid'], engine._get_civil_hours(d), d['years']
        )
        expected = heating_oracle(s_overnight, d['temperature'], d['month'], d['hour'], d['valid'],
                                  engine._get_civil_hours(d), engine.GAS_MONTHLY)
        np.testing.assert_allclose(res_overnight, expected, rtol=0, atol=1e-12)
        legacy = heating_oracle(s_overnight, d['temperature'], d['month'], d['hour'], d['valid'],
                                engine._get_civil_hours(d), engine.GAS_MONTHLY, legacy=True)
        np.testing.assert_allclose(
            legacy,
            npz['source_heating_overnight_window'],
            rtol=0.0,
            atol=1e-12,
            err_msg="Overnight scenario failed bit-for-bit equivalence with Task A fixture"
        )

    # -------------------------------------------------------------------------
    # 3. Layer Precedence & Diagnostic Warnings
    # -------------------------------------------------------------------------

    def test_canonical_beats_legacy_in_same_payload_with_diagnostic(self):
        """When canonical and legacy keys collide in same payload, canonical wins and diagnostic is emitted."""
        payload = {
            'ac_heating_windows': [
                {'id': 'morning', 'start_minute': 420, 'end_minute': 540},  # 7..9
            ],
            'heating_on_hour': 6,
            'heating_off_hour': 8,
        }
        res = migrate_and_resolve_schedule(payload)
        self.assertEqual(res, [{'id': 'morning', 'start_minute': 420, 'end_minute': 540}])
        diags = get_diagnostics()
        self.assertTrue(len(diags) > 0)
        self.assertIn('Explicit canonical windows take precedence', diags[0]['message'])

    def test_legacy_higher_layer_beats_canonical_lower_layer(self):
        """Legacy keys in higher layer beat canonical keys in lower base layer."""
        base_canonical = [
            {'id': 'morning', 'start_minute': 360, 'end_minute': 480},   # 6..8
            {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},  # 16..20
        ]
        higher_legacy = {
            'heating_on_hour': 8,
            'heating_off_hour': 12,
        }
        res = migrate_and_resolve_schedule(higher_legacy, base_windows=base_canonical)
        # Morning should be 8..12 (480..720), evening preserved as 16..20 (960..1200)
        self.assertEqual(res, [
            {'id': 'morning', 'start_minute': 480, 'end_minute': 720},
            {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
        ])

    def test_partial_legacy_patch_preserves_extra_windows(self):
        """Partial legacy override updates only matching morning/evening windows and preserves extra windows."""
        base_windows = [
            {'id': 'morning', 'start_minute': 360, 'end_minute': 480},
            {'id': 'lunch', 'start_minute': 720, 'end_minute': 780},
            {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
        ]
        # Incoming patch only updates morning start hour
        patch_settings = {'heating_on_hour': 5}
        res = migrate_and_resolve_schedule(patch_settings, base_windows=base_windows)
        self.assertEqual(len(res), 3)
        self.assertEqual(res[0], {'id': 'morning', 'start_minute': 300, 'end_minute': 480})
        self.assertEqual(res[1], {'id': 'lunch', 'start_minute': 720, 'end_minute': 780})
        self.assertEqual(res[2], {'id': 'evening', 'start_minute': 960, 'end_minute': 1200})

    # -------------------------------------------------------------------------
    # 4. Shared State, Memory-Only Read Migration & Complete Old Export
    # -------------------------------------------------------------------------

    def test_memory_only_read_migration_does_not_modify_saved_file(self):
        """Reading an old scenario with legacy keys produces canonical windows in memory without touching file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            temp_defaults_path = temp_path / 'user_defaults.json'
            temp_scenario_path = temp_path / 'current_scenario.json'

            legacy_scenario = {
                'state_revision': 'test-rev-1',
                'settings': {
                    'heating_on_hour': 8,
                    'heating_off_hour': 12,
                    'heating_evening_on_hour': 16,
                    'heating_evening_off_hour': 20,
                },
                'quote': {'line_items': []},
            }
            raw_text = json.dumps(legacy_scenario, indent=2) + '\n'
            temp_scenario_path.write_text(raw_text)

            with patch.object(assumptions, 'USER_DEFAULTS_PATH', temp_defaults_path), \
                 patch.object(shared_state, 'STATE_PATH', temp_scenario_path):

                state = shared_state.get_state()
                # Memory snapshot has canonical windows
                self.assertIn('ac_heating_windows', state['settings'])
                self.assertEqual(state['settings']['ac_heating_windows'], [
                    {'id': 'morning', 'start_minute': 480, 'end_minute': 720},
                    {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
                ])
                # File on disk was NOT rewritten!
                self.assertEqual(temp_scenario_path.read_text(), raw_text)

    def test_atomic_write_persists_canonical_format(self):
        """Saving via update_state writes canonical ac_heating_windows to disk without legacy keys."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            temp_defaults_path = temp_path / 'user_defaults.json'
            temp_scenario_path = temp_path / 'current_scenario.json'

            with patch.object(assumptions, 'USER_DEFAULTS_PATH', temp_defaults_path), \
                 patch.object(shared_state, 'STATE_PATH', temp_scenario_path):

                # Save a patch using legacy keys
                shared_state.update_state({'settings': {'heating_on_hour': 7, 'heating_off_hour': 10}})
                saved_on_disk = json.loads(temp_scenario_path.read_text())
                disk_settings = saved_on_disk['settings']

                self.assertIn('ac_heating_windows', disk_settings)
                self.assertEqual(disk_settings['ac_heating_windows'], [
                    {'id': 'morning', 'start_minute': 420, 'end_minute': 600},
                    {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
                ])
                for k in LEGACY_HEATING_KEYS:
                    self.assertNotIn(k, disk_settings)

    def test_complete_old_export_replace_migrates_before_completeness_check(self):
        """replace=True with legacy heating keys migrates to canonical before completeness check."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            temp_defaults_path = temp_path / 'user_defaults.json'
            temp_scenario_path = temp_path / 'current_scenario.json'

            with patch.object(assumptions, 'USER_DEFAULTS_PATH', temp_defaults_path), \
                 patch.object(shared_state, 'STATE_PATH', temp_scenario_path):

                # Start with full default settings, replacing canonical with legacy
                full_settings = dict(engine.defaults())
                full_settings.pop('ac_heating_windows')
                full_settings['heating_on_hour'] = 8
                full_settings['heating_off_hour'] = 11
                full_settings['heating_evening_on_hour'] = 17
                full_settings['heating_evening_off_hour'] = 21

                # update_state with replace=True should succeed without complaining missing 'ac_heating_windows'
                res = shared_state.update_state({
                    'replace': True,
                    'settings': full_settings,
                    'quote': {'line_items': []},
                })
                self.assertIn('ac_heating_windows', res['settings'])
                self.assertEqual(res['settings']['ac_heating_windows'], [
                    {'id': 'morning', 'start_minute': 480, 'end_minute': 660},
                    {'id': 'evening', 'start_minute': 1020, 'end_minute': 1260},
                ])

    # -------------------------------------------------------------------------
    # 5. Grouped Save & Reset in assumptions.py and server.py
    # -------------------------------------------------------------------------

    def test_grouped_save_and_reset_schedule_settings(self):
        """Saving or resetting schedule settings operates on SCHEDULE_SETTINGS group and preserves unrelated overrides."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / 'user_defaults.json'
            temp_scenario_path = Path(temp_dir) / 'current_scenario.json'

            with patch.object(assumptions, 'USER_DEFAULTS_PATH', temp_path), \
                 patch.object(shared_state, 'STATE_PATH', temp_scenario_path):

                # 1. Save unrelated override: scop = 2.0
                assumptions.save_user_default('scop', 2.0, engine.validate, path=temp_path)
                # Verify scop is saved
                content = json.loads(temp_path.read_text())
                self.assertEqual(content['overrides']['scop']['value'], 2.0)

                # 2. Save schedule settings group
                custom_windows = [
                    {'id': 'morning', 'start_minute': 420, 'end_minute': 540},
                    {'id': 'evening', 'start_minute': 1020, 'end_minute': 1200},
                ]
                entry = assumptions.save_user_default('ac_heating_windows', custom_windows, engine.validate, path=temp_path)
                self.assertEqual(entry['value'], custom_windows)

                content = json.loads(temp_path.read_text())
                self.assertEqual(content['overrides']['ac_heating_windows']['value'], custom_windows)
                self.assertIn('heating_schedule_mode', content['overrides'])

                # 3. Reset schedule settings
                res = assumptions.reset_user_default('ac_heating_windows', path=temp_path)
                self.assertTrue(res)

                # Verify schedule group was removed, but scop=2.0 was strictly preserved!
                content = json.loads(temp_path.read_text())
                self.assertNotIn('ac_heating_windows', content['overrides'])
                self.assertNotIn('heating_schedule_mode', content['overrides'])
                self.assertEqual(content['overrides']['scop']['value'], 2.0)

    def test_server_defaults_route_grouping_and_reset(self):
        """Verify /api/defaults route handles grouped schedule settings in request and reset."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            temp_defaults_path = temp_path / 'user_defaults.json'
            temp_scenario_path = temp_path / 'current_scenario.json'

            with patch.object(assumptions, 'USER_DEFAULTS_PATH', temp_defaults_path), \
                 patch.object(shared_state, 'STATE_PATH', temp_scenario_path):

                # Test POST /api/defaults to save custom windows
                custom_windows = [
                    {'id': 'morning', 'start_minute': 480, 'end_minute': 600},
                    {'id': 'evening', 'start_minute': 1080, 'end_minute': 1260},
                ]
                # Simulating server handle_post
                body = {'name': 'ac_heating_windows', 'value': custom_windows}
                entry = assumptions.save_user_default(body['name'], body['value'], engine.validate)
                self.assertEqual(entry['value'], custom_windows)

                # Reset via reset_user_default
                reset_res = assumptions.reset_user_default('ac_heating_windows')
                self.assertTrue(reset_res)
                self.assertEqual(engine.defaults()['ac_heating_windows'], DEFAULT_AC_HEATING_WINDOWS)

    # -------------------------------------------------------------------------
    # 6. Calculation-Time Enforcement & UI Draft Tolerance
    # -------------------------------------------------------------------------

    def test_engine_validate_tolerates_draft_empty_and_inactive(self):
        """engine.validate tolerates empty [] and inactive start==end windows in scheduled mode."""
        s1 = engine.validate({'heating_schedule_mode': 'scheduled', 'ac_heating_windows': []})
        self.assertEqual(s1['ac_heating_windows'], [])

        s2 = engine.validate({
            'heating_schedule_mode': 'scheduled',
            'ac_heating_windows': [{'id': 'w1', 'start_minute': 500, 'end_minute': 500}],
        })
        self.assertEqual(s2['ac_heating_windows'], [{'id': 'w1', 'start_minute': 500, 'end_minute': 500}])

    def test_calculation_raises_exact_canonical_message(self):
        """Calculation raises exact canonical ValueError when active minutes are zero with positive budget."""
        s_empty = engine.validate({
            'load_scenario': 'electrified',
            'heating_schedule_mode': 'scheduled',
            'ac_heating_windows': [],
            'horizon_years': 1,
        })
        with self.assertRaises(ValueError) as ctx:
            engine.evaluate(s_empty)
        self.assertEqual(str(ctx.exception), "Add at least one operating window to allocate the requested AC heating demand.")

        s_inactive = engine.validate({
            'load_scenario': 'electrified',
            'heating_schedule_mode': 'scheduled',
            'ac_heating_windows': [
                {'id': 'morning', 'start_minute': 360, 'end_minute': 360},
                {'id': 'evening', 'start_minute': 960, 'end_minute': 960},
            ],
            'horizon_years': 1,
        })
        with self.assertRaises(ValueError) as ctx:
            engine.evaluate(s_inactive)
        self.assertEqual(str(ctx.exception), "Add at least one operating window to allocate the requested AC heating demand.")

    def test_continuous_mode_unaffected_by_empty_or_inactive_windows(self):
        """Continuous mode ignores ac_heating_windows and allocates 24h heating without error."""
        s_cont = engine.validate({
            'load_scenario': 'electrified',
            'heating_schedule_mode': 'continuous',
            'ac_heating_windows': [],
            'horizon_years': 1,
        })
        res = engine.evaluate(s_cont)
        self.assertIn('metrics', res)
        self.assertIn('energy', res)

    # -------------------------------------------------------------------------
    # 7. Deterministic Cache Freeze/Thaw
    # -------------------------------------------------------------------------

    def test_freeze_thaw_isomorphism_and_hashability(self):
        """freeze() makes mutable nested structures hashable; thaw() restores mutable equivalents."""
        data = [
            {'id': 'morning', 'start_minute': 360, 'end_minute': 480},
            {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
        ]
        frozen = freeze(data)
        self.assertIsInstance(frozen, tuple)
        self.assertIsInstance(frozen[0], tuple)
        # Verify hashability
        h = hash(frozen)
        self.assertIsInstance(h, int)

        # Frozen data can be used as dict keys or set elements
        test_cache = {frozen: 'cached_result'}
        self.assertEqual(test_cache[frozen], 'cached_result')

        # Thaw restores mutable lists and dicts
        thawed = thaw(frozen)
        self.assertEqual(thawed, data)
        self.assertIsInstance(thawed, list)
        self.assertIsInstance(thawed[0], dict)
        # Thawed is mutable
        thawed[0]['start_minute'] = 999
        self.assertEqual(thawed[0]['start_minute'], 999)
        # Frozen is immutable tuple
        with self.assertRaises(TypeError):
            frozen[0][0] = 'changed'

    def test_engine_cached_functions_accept_frozen_schedule(self):
        """Cached engine functions (_load_cache, _forecast_runs, etc.) evaluate deterministically."""
        s = engine.validate({'load_scenario': 'electrified', 'installation_year': 2026})
        load_key = tuple((k, freeze(s.get(k))) for k in engine.LOAD_KEYS)
        res1 = engine._load_cache(load_key)
        res2 = engine._load_cache(load_key)
        self.assertIs(res1, res2)  # cache hit

        energy_key = tuple((k, freeze(s[k])) for k in engine.ENERGY_KEYS)
        fc1 = engine._forecast_runs(energy_key)
        fc2 = engine._forecast_runs(energy_key)
        self.assertIs(fc1, fc2)  # cache hit

    # -------------------------------------------------------------------------
    # 8. Canonical-Only Defaults and Validated Internal State (No Legacy Leakage)
    # -------------------------------------------------------------------------

    def test_factory_defaults_and_validate_contain_no_legacy_keys(self):
        """Factory defaults and default validate return no LEGACY_HEATING_KEYS."""
        # assumptions.defaults()
        d_assumptions = assumptions.defaults()
        for k in LEGACY_HEATING_KEYS:
            self.assertNotIn(k, d_assumptions)
        self.assertEqual(d_assumptions['ac_heating_windows'], DEFAULT_AC_HEATING_WINDOWS)
        self.assertEqual(d_assumptions['heating_schedule_mode'], 'scheduled')

        # engine.defaults()
        d_engine = engine.defaults()
        for k in LEGACY_HEATING_KEYS:
            self.assertNotIn(k, d_engine)
        self.assertEqual(d_engine['ac_heating_windows'], DEFAULT_AC_HEATING_WINDOWS)
        self.assertEqual(d_engine['heating_schedule_mode'], 'scheduled')

        # engine.validate()
        s_val_none = engine.validate()
        for k in LEGACY_HEATING_KEYS:
            self.assertNotIn(k, s_val_none)
        self.assertEqual(s_val_none['ac_heating_windows'], DEFAULT_AC_HEATING_WINDOWS)

        s_val_empty = engine.validate({})
        for k in LEGACY_HEATING_KEYS:
            self.assertNotIn(k, s_val_empty)
        self.assertEqual(s_val_empty['ac_heating_windows'], DEFAULT_AC_HEATING_WINDOWS)

    def test_temporary_legacy_user_default_overrides_return_no_legacy_keys(self):
        """Temporary legacy user-default overrides migrate canonical windows and leak no legacy keys."""
        temp_defaults_path = Path(self._temp_dir.name) / 'user_defaults.json'
        legacy_overrides = {
            "overrides": {
                "heating_on_hour": {"value": 8},
                "heating_off_hour": {"value": 12},
                "heating_evening_on_hour": {"value": 17},
                "heating_evening_off_hour": {"value": 21},
            }
        }
        temp_defaults_path.write_text(json.dumps(legacy_overrides, indent=2))

        # assumptions.defaults() resolves canonical windows without legacy keys
        defs = assumptions.defaults(path=temp_defaults_path)
        self.assertEqual(defs['ac_heating_windows'], [
            {'id': 'morning', 'start_minute': 480, 'end_minute': 720},
            {'id': 'evening', 'start_minute': 1020, 'end_minute': 1260},
        ])
        for k in LEGACY_HEATING_KEYS:
            self.assertNotIn(k, defs)

        # An explicit stored-default snapshot validates without rebinding the runtime.
        eng_defs = engine.validate({}, base=defs)
        self.assertEqual(eng_defs['ac_heating_windows'], defs['ac_heating_windows'])
        for k in LEGACY_HEATING_KEYS:
            self.assertNotIn(k, eng_defs)

        # Validation of this explicit snapshot returns canonical state without legacy keys
        validated = engine.validate({}, base=defs)
        self.assertEqual(validated['ac_heating_windows'], defs['ac_heating_windows'])
        for k in LEGACY_HEATING_KEYS:
            self.assertNotIn(k, validated)

    def test_partial_legacy_patches_return_no_legacy_keys(self):
        """Partial legacy input patches migrate to canonical windows and return no legacy keys."""
        # Morning patch only
        s_morning = engine.validate({'heating_on_hour': 7})
        self.assertEqual(s_morning['ac_heating_windows'], [
            {'id': 'morning', 'start_minute': 420, 'end_minute': 480},
            {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
        ])
        for k in LEGACY_HEATING_KEYS:
            self.assertNotIn(k, s_morning)

        # Evening patch only
        s_evening = engine.validate({'heating_evening_off_hour': 22})
        self.assertEqual(s_evening['ac_heating_windows'], [
            {'id': 'morning', 'start_minute': 360, 'end_minute': 480},
            {'id': 'evening', 'start_minute': 960, 'end_minute': 1320},
        ])
        for k in LEGACY_HEATING_KEYS:
            self.assertNotIn(k, s_evening)

        # Legacy patch on base schedule missing target window raises clear error
        base_no_morning = dict(engine.defaults(), ac_heating_windows=[{'id': 'lunch', 'start_minute': 720, 'end_minute': 780}])
        with self.assertRaises(ValueError) as ctx:
            engine.validate({'heating_on_hour': 7}, base=base_no_morning)
        self.assertIn("morning", str(ctx.exception))

        # In same payload, canonical wins and legacy key is ignored with conflict diagnostic
        s_both = engine.validate({
            'heating_on_hour': 7,
            'ac_heating_windows': [{'id': 'morning', 'start_minute': 360, 'end_minute': 480}],
        })
        self.assertEqual(s_both['ac_heating_windows'], [{'id': 'morning', 'start_minute': 360, 'end_minute': 480}])
        for k in LEGACY_HEATING_KEYS:
            self.assertNotIn(k, s_both)

    def test_validate_with_legacy_bearing_base_cannot_leak_legacy_fields(self):
        """Validate with a legacy-bearing base cannot leak legacy fields into returned state."""
        # 1. Base with both defaults and legacy keys injected
        legacy_base = dict(engine.defaults())
        legacy_base['heating_on_hour'] = 8
        legacy_base['heating_off_hour'] = 12
        legacy_base['heating_evening_on_hour'] = 17
        legacy_base['heating_evening_off_hour'] = 21

        s_no_input = engine.validate({}, base=legacy_base)
        for k in LEGACY_HEATING_KEYS:
            self.assertNotIn(k, s_no_input)

        s_unrelated = engine.validate({'scop': 3.5}, base=legacy_base)
        self.assertEqual(s_unrelated['scop'], 3.5)
        for k in LEGACY_HEATING_KEYS:
            self.assertNotIn(k, s_unrelated)

        # 2. Base with legacy keys only (ac_heating_windows missing from base)
        legacy_only_base = dict(engine.defaults())
        del legacy_only_base['ac_heating_windows']
        legacy_only_base['heating_on_hour'] = 8
        legacy_only_base['heating_off_hour'] = 12

        s_migrated_base = engine.validate({}, base=legacy_only_base)
        self.assertEqual(s_migrated_base['ac_heating_windows'], [
            {'id': 'morning', 'start_minute': 480, 'end_minute': 720},
            {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
        ])
        for k in LEGACY_HEATING_KEYS:
            self.assertNotIn(k, s_migrated_base)

        # 3. Base with legacy keys receives an incoming partial legacy patch
        s_patched = engine.validate({'heating_off_hour': 11}, base=legacy_only_base)
        self.assertEqual(s_patched['ac_heating_windows'], [
            {'id': 'morning', 'start_minute': 480, 'end_minute': 660},
            {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
        ])
        for k in LEGACY_HEATING_KEYS:
            self.assertNotIn(k, s_patched)

    def test_canonical_multi_window_minute_schedule_returns_no_legacy_keys(self):
        """Canonical multi-window minute schedule preserves precision and returns no legacy keys."""
        minute_schedule = [
            {'id': 'morning', 'start_minute': 375, 'end_minute': 495},   # 06:15 to 08:15
            {'id': 'lunch', 'start_minute': 750, 'end_minute': 810},     # 12:30 to 13:30
            {'id': 'evening', 'start_minute': 990, 'end_minute': 1215},  # 16:30 to 20:15
        ]

        # Validation preserves minute precision, extra windows, and returns no legacy keys
        s = engine.validate({'ac_heating_windows': minute_schedule})
        self.assertEqual(s['ac_heating_windows'], minute_schedule)
        for k in LEGACY_HEATING_KEYS:
            self.assertNotIn(k, s)

        # Saving as user default persists canonical windows and defaults return no legacy keys
        temp_defaults_path = Path(self._temp_dir.name) / 'user_defaults.json'
        entry = assumptions.save_user_default('ac_heating_windows', minute_schedule, engine.validate, path=temp_defaults_path)
        self.assertEqual(entry['value'], minute_schedule)

        d = assumptions.defaults(path=temp_defaults_path)
        self.assertEqual(d['ac_heating_windows'], minute_schedule)
        for k in LEGACY_HEATING_KEYS:
            self.assertNotIn(k, d)

        s_reloaded = engine.validate({}, base=d)
        self.assertEqual(s_reloaded['ac_heating_windows'], minute_schedule)
        for k in LEGACY_HEATING_KEYS:
            self.assertNotIn(k, s_reloaded)


if __name__ == '__main__':
    unittest.main()
