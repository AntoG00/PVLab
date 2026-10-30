"""Focused tests for schedule-default saving and legacy hours migration."""
import json
import unittest

from feasibility import assumptions, engine, shared_state
from feasibility.schedule import DEFAULT_AC_HEATING_WINDOWS, LEGACY_HEATING_KEYS
import test_support


class ACScheduleDefaultSaveTests(test_support.IsolatedDefaultsTestCase):
    def setUp(self):
        super().setUp()
        self.defaults_path = assumptions.USER_DEFAULTS_PATH
        self.state_path = shared_state.STATE_PATH

    def test_exact_reproduction_legacy_hours_migrated_and_scop_preserved(self):
        """Reproduction: saving mode migrates legacy hours to canonical and preserves scop metadata."""
        initial = {
            "overrides": {
                "scop": {"value": 2, "owner": "original-owner", "status": "provided", "custom_note": "retain"},
                "heating_on_hour": {"value": 8},
                "heating_off_hour": {"value": 12},
            }
        }
        self.defaults_path.write_text(json.dumps(initial))

        # assumptions.persisted_defaults() resolves canonical morning 480-720 correctly
        defs_before = assumptions.persisted_defaults()
        self.assertEqual(defs_before['ac_heating_windows'], [
            {'id': 'morning', 'start_minute': 480, 'end_minute': 720},
            {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
        ])

        # Save heating_schedule_mode = continuous with context
        saved = assumptions.save_user_default(
            "heating_schedule_mode",
            "continuous",
            engine.validate,
            path=self.defaults_path,
            schedule_settings={"heating_schedule_mode": "continuous"},
        )
        self.assertEqual(saved['value'], "continuous")

        persisted = json.loads(self.defaults_path.read_text())['overrides']
        # Windows must be 480-720 + 960-1200 and mode continuous
        self.assertEqual(persisted['ac_heating_windows']['value'], [
            {'id': 'morning', 'start_minute': 480, 'end_minute': 720},
            {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
        ])
        self.assertEqual(persisted['heating_schedule_mode']['value'], "continuous")

        # No legacy overrides left
        for k in LEGACY_HEATING_KEYS:
            self.assertNotIn(k, persisted)

        # scop entry including custom_note unchanged
        self.assertEqual(persisted['scop'], {
            "value": 2,
            "owner": "original-owner",
            "status": "provided",
            "custom_note": "retain",
        })

    def test_partial_legacy_evening_defaults_inherit_other_bounds(self):
        """Partial legacy evening overrides inherit default morning bounds and unedited evening bound."""
        # 1. Partial evening_on: inherits evening_off (20 -> 1200) and morning factory (360-480)
        self.defaults_path.write_text(json.dumps({
            "overrides": {
                "scop": {"value": 2, "owner": "original-owner", "status": "provided", "custom_note": "retain"},
                "heating_evening_on_hour": {"value": 17},
            }
        }))
        assumptions.save_user_default("heating_schedule_mode", "scheduled", engine.validate, path=self.defaults_path)
        persisted = json.loads(self.defaults_path.read_text())['overrides']
        self.assertEqual(persisted['ac_heating_windows']['value'], [
            {'id': 'morning', 'start_minute': 360, 'end_minute': 480},
            {'id': 'evening', 'start_minute': 1020, 'end_minute': 1200},
        ])
        self.assertNotIn('heating_evening_on_hour', persisted)
        self.assertEqual(persisted['scop']['custom_note'], "retain")

        # 2. Partial evening_off: inherits evening_on (16 -> 960) and morning factory (360-480)
        self.defaults_path.write_text(json.dumps({
            "overrides": {
                "heating_evening_off_hour": {"value": 21},
            }
        }))
        assumptions.save_user_default("heating_schedule_mode", "scheduled", engine.validate, path=self.defaults_path)
        persisted2 = json.loads(self.defaults_path.read_text())['overrides']
        self.assertEqual(persisted2['ac_heating_windows']['value'], [
            {'id': 'morning', 'start_minute': 360, 'end_minute': 480},
            {'id': 'evening', 'start_minute': 960, 'end_minute': 1260},
        ])
        self.assertNotIn('heating_evening_off_hour', persisted2)

    def test_save_mode_with_and_without_context_retains_windows(self):
        """Saving mode retains resolved windows both with and without context."""
        custom_windows = [
            {'id': 'morning', 'start_minute': 420, 'end_minute': 540},
            {'id': 'evening', 'start_minute': 1020, 'end_minute': 1140},
        ]
        assumptions.save_user_default("ac_heating_windows", custom_windows, engine.validate, path=self.defaults_path)

        # Save mode WITHOUT context
        assumptions.save_user_default("heating_schedule_mode", "continuous", engine.validate, path=self.defaults_path)
        persisted = json.loads(self.defaults_path.read_text())['overrides']
        self.assertEqual(persisted['ac_heating_windows']['value'], custom_windows)
        self.assertEqual(persisted['heating_schedule_mode']['value'], "continuous")

        # Save mode WITH mode-only context
        assumptions.save_user_default(
            "heating_schedule_mode",
            "scheduled",
            engine.validate,
            path=self.defaults_path,
            schedule_settings={"heating_schedule_mode": "scheduled"},
        )
        persisted2 = json.loads(self.defaults_path.read_text())['overrides']
        self.assertEqual(persisted2['ac_heating_windows']['value'], custom_windows)
        self.assertEqual(persisted2['heating_schedule_mode']['value'], "scheduled")

    def test_explicit_custom_canonical_context_wins(self):
        """Explicit canonical windows in context win, including extra, overnight, and inactive windows."""
        # Initial legacy overrides on disk
        self.defaults_path.write_text(json.dumps({
            "overrides": {
                "heating_on_hour": {"value": 7},
                "heating_off_hour": {"value": 9},
            }
        }))
        custom_windows = [
            {'id': 'morning', 'start_minute': 400, 'end_minute': 500},
            {'id': 'inactive', 'start_minute': 600, 'end_minute': 600},
            {'id': 'extra', 'start_minute': 720, 'end_minute': 840},
            {'id': 'overnight', 'start_minute': 1320, 'end_minute': 360},
        ]
        assumptions.save_user_default(
            "heating_schedule_mode",
            "continuous",
            engine.validate,
            path=self.defaults_path,
            schedule_settings={"ac_heating_windows": custom_windows, "heating_schedule_mode": "continuous"},
        )
        persisted = json.loads(self.defaults_path.read_text())['overrides']
        self.assertEqual(persisted['ac_heating_windows']['value'], custom_windows)
        self.assertEqual(persisted['heating_schedule_mode']['value'], "continuous")
        self.assertNotIn("heating_on_hour", persisted)
        self.assertNotIn("heating_off_hour", persisted)

    def test_grouped_reset_returns_factory_group_and_leaves_unrelated_metadata(self):
        """Resetting schedule group restores factory defaults and preserves unrelated overrides byte-for-value."""
        self.defaults_path.write_text(json.dumps({
            "overrides": {
                "scop": {"value": 2, "owner": "original-owner", "status": "provided", "custom_note": "retain"},
                "heating_on_hour": {"value": 8},
                "heating_off_hour": {"value": 12},
                "ac_heating_windows": {
                    "value": [{'id': 'morning', 'start_minute': 480, 'end_minute': 720}],
                    "owner": "user",
                    "status": "provided",
                },
                "heating_schedule_mode": {"value": "continuous", "owner": "user", "status": "provided"},
            }
        }))

        res = assumptions.reset_user_default("ac_heating_windows", path=self.defaults_path)
        self.assertTrue(res)

        persisted = json.loads(self.defaults_path.read_text())['overrides']
        self.assertNotIn("ac_heating_windows", persisted)
        self.assertNotIn("heating_schedule_mode", persisted)
        self.assertNotIn("heating_on_hour", persisted)
        self.assertNotIn("heating_off_hour", persisted)

        # scop and its custom_note strictly retained byte-for-value
        self.assertEqual(persisted['scop'], {
            "value": 2,
            "owner": "original-owner",
            "status": "provided",
            "custom_note": "retain",
        })

        # Resolved defaults return factory schedule
        d = assumptions.persisted_defaults()
        self.assertEqual(d['ac_heating_windows'], DEFAULT_AC_HEATING_WINDOWS)
        self.assertEqual(d['heating_schedule_mode'], "scheduled")
        self.assertEqual(d['scop'], 2)

    def test_invalid_edits_cannot_partially_write(self):
        """Invalid edits raise ValueError and leave persisted defaults completely untouched."""
        initial_data = {
            "overrides": {
                "scop": {"value": 2, "owner": "original-owner", "status": "provided", "custom_note": "retain"},
                "heating_on_hour": {"value": 8},
                "heating_off_hour": {"value": 12},
            }
        }
        self.defaults_path.write_text(json.dumps(initial_data, indent=2) + "\n")
        before_bytes = self.defaults_path.read_bytes()

        # 1. Invalid schedule mode
        with self.assertRaises(ValueError):
            assumptions.save_user_default(
                "heating_schedule_mode",
                "invalid_mode",
                engine.validate,
                path=self.defaults_path,
            )
        self.assertEqual(self.defaults_path.read_bytes(), before_bytes)

        # 2. Invalid window format (not a list)
        with self.assertRaises(ValueError):
            assumptions.save_user_default(
                "ac_heating_windows",
                "not_a_list",
                engine.validate,
                path=self.defaults_path,
            )
        self.assertEqual(self.defaults_path.read_bytes(), before_bytes)

        # 3. Invalid window bounds (negative start_minute)
        with self.assertRaises(ValueError):
            assumptions.save_user_default(
                "ac_heating_windows",
                [{'id': 'w1', 'start_minute': -1, 'end_minute': 100}],
                engine.validate,
                path=self.defaults_path,
            )
        self.assertEqual(self.defaults_path.read_bytes(), before_bytes)

        # 4. Invalid context in schedule_settings
        with self.assertRaises(ValueError):
            assumptions.save_user_default(
                "heating_schedule_mode",
                "continuous",
                engine.validate,
                path=self.defaults_path,
                schedule_settings={"ac_heating_windows": [{'id': 'w1', 'start_minute': 2000, 'end_minute': 100}]},
            )
        self.assertEqual(self.defaults_path.read_bytes(), before_bytes)


if __name__ == '__main__':
    unittest.main()
