"""Focused unit tests for canonical AC heating schedule parser validation."""
import copy
import json
import math
import unittest

from feasibility.schedule import (
    get_diagnostics,
    validate_ac_heating_windows,
)


class AcScheduleParserTests(unittest.TestCase):
    """Test suite for pure validation function validate_ac_heating_windows."""

    # -------------------------------------------------------------------------
    # 1. Valid Canonical Inputs
    # -------------------------------------------------------------------------

    def test_empty_schedule(self):
        """Empty list is a valid UI draft schedule and returns a new empty list."""
        inp = []
        out = validate_ac_heating_windows(inp)
        self.assertEqual(out, [])
        self.assertIsNot(out, inp)

    def test_single_window_valid(self):
        """Single valid window passes validation."""
        inp = [{'id': 'morning', 'start_minute': 360, 'end_minute': 480}]
        out = validate_ac_heating_windows(inp)
        self.assertEqual(out, [{'id': 'morning', 'start_minute': 360, 'end_minute': 480}])

    def test_multiple_windows_preserve_order_and_ids(self):
        """Multiple windows preserve original order, ids, and minute bounds."""
        inp = [
            {'id': 'morning', 'start_minute': 360, 'end_minute': 480},
            {'id': 'lunch', 'start_minute': 720, 'end_minute': 780},
            {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
            {'id': 'night', 'start_minute': 1320, 'end_minute': 360},
        ]
        out = validate_ac_heating_windows(inp)
        self.assertEqual(len(out), 4)
        self.assertEqual([w['id'] for w in out], ['morning', 'lunch', 'evening', 'night'])
        self.assertEqual([w['start_minute'] for w in out], [360, 720, 960, 1320])
        self.assertEqual([w['end_minute'] for w in out], [480, 780, 1200, 360])

    def test_all_day_window(self):
        """All-day window 0..1440 is valid."""
        inp = [{'id': 'all_day', 'start_minute': 0, 'end_minute': 1440}]
        out = validate_ac_heating_windows(inp)
        self.assertEqual(out, [{'id': 'all_day', 'start_minute': 0, 'end_minute': 1440}])

    def test_overnight_wrapping_midnight(self):
        """Overnight windows (start > end) are valid and preserved."""
        cases = [
            ({'id': 'wrap1', 'start_minute': 1320, 'end_minute': 360}),
            ({'id': 'wrap2', 'start_minute': 1439, 'end_minute': 1}),
            ({'id': 'wrap3', 'start_minute': 1439, 'end_minute': 0}),
        ]
        for c in cases:
            out = validate_ac_heating_windows([c])
            self.assertEqual(out, [c])

    def test_inactive_equal_bounds(self):
        """Inactive windows where start == end are valid and preserved."""
        cases = [
            ({'id': 'inact_0', 'start_minute': 0, 'end_minute': 0}),
            ({'id': 'inact_mid', 'start_minute': 420, 'end_minute': 420}),
            ({'id': 'inact_1439', 'start_minute': 1439, 'end_minute': 1439}),
        ]
        for c in cases:
            out = validate_ac_heating_windows([c])
            self.assertEqual(out, [c])

    def test_extreme_valid_bounds(self):
        """Minute bounds at boundary extremes are valid."""
        inp = [
            {'id': 'min_start', 'start_minute': 0, 'end_minute': 1},
            {'id': 'max_start', 'start_minute': 1439, 'end_minute': 1440},
            {'id': 'zero_end', 'start_minute': 700, 'end_minute': 0},
            {'id': 'max_end', 'start_minute': 1000, 'end_minute': 1440},
        ]
        out = validate_ac_heating_windows(inp)
        self.assertEqual(out, inp)

    def test_auto_assigned_ids_when_omitted(self):
        """When id is omitted, assigns stable sequential window_1, window_2, ..."""
        inp = [
            {'start_minute': 360, 'end_minute': 480},
            {'start_minute': 960, 'end_minute': 1200},
        ]
        out = validate_ac_heating_windows(inp)
        self.assertEqual(out[0]['id'], 'window_1')
        self.assertEqual(out[1]['id'], 'window_2')
        self.assertEqual(out[0]['start_minute'], 360)
        self.assertEqual(out[1]['end_minute'], 1200)

    def test_mixed_provided_and_auto_ids(self):
        """Windows with explicit IDs and omitted IDs coexist when IDs do not collide."""
        inp = [
            {'id': 'custom_slot', 'start_minute': 360, 'end_minute': 480},
            {'start_minute': 960, 'end_minute': 1200},  # gets window_2
        ]
        out = validate_ac_heating_windows(inp)
        self.assertEqual(out[0]['id'], 'custom_slot')
        self.assertEqual(out[1]['id'], 'window_2')

    # -------------------------------------------------------------------------
    # 2. Immutability, Isolation and JSON Compatibility
    # -------------------------------------------------------------------------

    def test_no_mutation_or_aliasing_of_caller_input(self):
        """Input list and dicts are never mutated or aliased by validation."""
        inp = [
            {'id': 'morning', 'start_minute': 360, 'end_minute': 480},
            {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
        ]
        snapshot_before = copy.deepcopy(inp)
        out = validate_ac_heating_windows(inp)

        # Output is not input list
        self.assertIsNot(out, inp)
        # Elements are not input dicts
        self.assertIsNot(out[0], inp[0])
        self.assertIsNot(out[1], inp[1])

        # Input is unchanged
        self.assertEqual(inp, snapshot_before)

        # Mutating input does not affect output
        inp[0]['start_minute'] = 999
        inp.append({'id': 'extra', 'start_minute': 0, 'end_minute': 60})
        self.assertEqual(out[0]['start_minute'], 360)
        self.assertEqual(len(out), 2)

        # Mutating output does not affect input
        out[1]['end_minute'] = 1111
        self.assertEqual(inp[1]['end_minute'], 1200)

    def test_json_serializable_output(self):
        """Return values are strictly JSON-serializable independent data structures."""
        inp = [{'start_minute': 0, 'end_minute': 1440}]
        out = validate_ac_heating_windows(inp)
        dumped = json.dumps(out)
        loaded = json.loads(dumped)
        self.assertEqual(loaded, [{'id': 'window_1', 'start_minute': 0, 'end_minute': 1440}])

    # -------------------------------------------------------------------------
    # 3. Strict Rejection of Malformed Container and Elements
    # -------------------------------------------------------------------------

    def test_reject_non_list_container(self):
        """Reject non-list containers: None, string, int, bool, dict, tuple, set."""
        bad_containers = [
            None,
            'not_a_list',
            123,
            True,
            False,
            {'start_minute': 360, 'end_minute': 480},
            ({'id': 'w1', 'start_minute': 360, 'end_minute': 480},),
            {('id', 'w1')},
        ]
        for bad in bad_containers:
            with self.assertRaises(ValueError, msg=f"Should reject container: {bad!r}"):
                validate_ac_heating_windows(bad)

    def test_reject_non_dict_elements(self):
        """Reject non-dict elements inside the list."""
        bad_elements = [
            [None],
            ['a_string'],
            [123],
            [True],
            [False],
            [[{'start_minute': 360, 'end_minute': 480}]],
            [('start_minute', 360)],
        ]
        for bad in bad_elements:
            with self.assertRaises(ValueError, msg=f"Should reject element in: {bad!r}"):
                validate_ac_heating_windows(bad)

    def test_reject_unknown_fields(self):
        """Reject objects containing unexpected/unknown fields."""
        bad_cases = [
            [{'id': 'w1', 'start_minute': 360, 'end_minute': 480, 'name': 'morning'}],
            [{'id': 'w1', 'start_minute': 360, 'end_minute': 480, 'extra': 123}],
            [{'id': 'w1', 'start_minute': 360, 'end_minute': 480, 'enabled': True}],
        ]
        for bad in bad_cases:
            with self.assertRaises(ValueError, msg=f"Should reject unknown fields: {bad!r}"):
                validate_ac_heating_windows(bad)

    def test_reject_missing_required_fields(self):
        """Reject window objects missing start_minute or end_minute."""
        bad_cases = [
            [{}],
            [{'id': 'w1'}],
            [{'id': 'w1', 'start_minute': 360}],
            [{'id': 'w1', 'end_minute': 480}],
        ]
        for bad in bad_cases:
            with self.assertRaises(ValueError, msg=f"Should reject missing fields: {bad!r}"):
                validate_ac_heating_windows(bad)

    # -------------------------------------------------------------------------
    # 4. Strict Rejection of Non-Integers, Floats, Bools, NaN, Inf
    # -------------------------------------------------------------------------

    def test_reject_booleans_for_minutes(self):
        """Booleans must be strictly rejected even though bool is a subclass of int."""
        bad_cases = [
            [{'id': 'w1', 'start_minute': True, 'end_minute': 480}],
            [{'id': 'w1', 'start_minute': False, 'end_minute': 480}],
            [{'id': 'w1', 'start_minute': 360, 'end_minute': True}],
            [{'id': 'w1', 'start_minute': 360, 'end_minute': False}],
        ]
        for bad in bad_cases:
            with self.assertRaises(ValueError, msg=f"Should reject bool minute: {bad!r}"):
                validate_ac_heating_windows(bad)

    def test_reject_floats_including_integer_looking(self):
        """Floats must be rejected even when integer-valued (e.g. 360.0, 480.0)."""
        bad_cases = [
            [{'id': 'w1', 'start_minute': 360.0, 'end_minute': 480}],
            [{'id': 'w1', 'start_minute': 360, 'end_minute': 480.0}],
            [{'id': 'w1', 'start_minute': 360.5, 'end_minute': 480}],
            [{'id': 'w1', 'start_minute': 360, 'end_minute': 480.25}],
            [{'id': 'w1', 'start_minute': 0.0, 'end_minute': 1440}],
            [{'id': 'w1', 'start_minute': 0, 'end_minute': 1440.0}],
        ]
        for bad in bad_cases:
            with self.assertRaises(ValueError, msg=f"Should reject float minute: {bad!r}"):
                validate_ac_heating_windows(bad)

    def test_reject_nan_inf_and_strings_for_minutes(self):
        """Reject NaN, inf, -inf, strings, None, and collections for minute fields."""
        bad_cases = [
            [{'id': 'w1', 'start_minute': float('nan'), 'end_minute': 480}],
            [{'id': 'w1', 'start_minute': 360, 'end_minute': float('nan')}],
            [{'id': 'w1', 'start_minute': float('inf'), 'end_minute': 480}],
            [{'id': 'w1', 'start_minute': 360, 'end_minute': float('-inf')}],
            [{'id': 'w1', 'start_minute': '360', 'end_minute': 480}],
            [{'id': 'w1', 'start_minute': 360, 'end_minute': '480'}],
            [{'id': 'w1', 'start_minute': None, 'end_minute': 480}],
            [{'id': 'w1', 'start_minute': 360, 'end_minute': None}],
        ]
        for bad in bad_cases:
            with self.assertRaises(ValueError, msg=f"Should reject non-integer minute: {bad!r}"):
                validate_ac_heating_windows(bad)

    # -------------------------------------------------------------------------
    # 5. Strict Rejection of Out-of-Bounds Minute Ranges
    # -------------------------------------------------------------------------

    def test_reject_out_of_bounds_start_minute(self):
        """start_minute must be strictly 0..1439 (1440 is invalid for start)."""
        bad_cases = [
            [{'id': 'w1', 'start_minute': -1, 'end_minute': 480}],
            [{'id': 'w1', 'start_minute': -100, 'end_minute': 480}],
            [{'id': 'w1', 'start_minute': 1440, 'end_minute': 1440}],
            [{'id': 'w1', 'start_minute': 1441, 'end_minute': 1440}],
            [{'id': 'w1', 'start_minute': 2000, 'end_minute': 480}],
        ]
        for bad in bad_cases:
            with self.assertRaises(ValueError, msg=f"Should reject start_minute out of bounds: {bad!r}"):
                validate_ac_heating_windows(bad)

    def test_reject_out_of_bounds_end_minute(self):
        """end_minute must be strictly 0..1440 (1441 is invalid for end)."""
        bad_cases = [
            [{'id': 'w1', 'start_minute': 360, 'end_minute': -1}],
            [{'id': 'w1', 'start_minute': 360, 'end_minute': -50}],
            [{'id': 'w1', 'start_minute': 360, 'end_minute': 1441}],
            [{'id': 'w1', 'start_minute': 360, 'end_minute': 2000}],
        ]
        for bad in bad_cases:
            with self.assertRaises(ValueError, msg=f"Should reject end_minute out of bounds: {bad!r}"):
                validate_ac_heating_windows(bad)

    # -------------------------------------------------------------------------
    # 6. Strict Rejection of Invalid and Duplicate IDs
    # -------------------------------------------------------------------------

    def test_reject_invalid_id_types_and_empty_strings(self):
        """Window IDs if provided must be non-empty, non-whitespace strings."""
        bad_cases = [
            [{'id': '', 'start_minute': 360, 'end_minute': 480}],
            [{'id': '   ', 'start_minute': 360, 'end_minute': 480}],
            [{'id': '\t\n', 'start_minute': 360, 'end_minute': 480}],
            [{'id': 123, 'start_minute': 360, 'end_minute': 480}],
            [{'id': True, 'start_minute': 360, 'end_minute': 480}],
            [{'id': False, 'start_minute': 360, 'end_minute': 480}],
            [{'id': None, 'start_minute': 360, 'end_minute': 480}],
            [{'id': ['list_id'], 'start_minute': 360, 'end_minute': 480}],
        ]
        for bad in bad_cases:
            with self.assertRaises(ValueError, msg=f"Should reject invalid id: {bad!r}"):
                validate_ac_heating_windows(bad)

    def test_reject_duplicate_provided_ids(self):
        """Duplicate provided IDs must raise ValueError."""
        inp = [
            {'id': 'morning', 'start_minute': 360, 'end_minute': 480},
            {'id': 'morning', 'start_minute': 960, 'end_minute': 1200},
        ]
        with self.assertRaises(ValueError) as ctx:
            validate_ac_heating_windows(inp)
        self.assertIn("Duplicate window id 'morning'", str(ctx.exception))

    def test_reject_duplicate_provided_ids_among_multiple(self):
        """Duplicate IDs detected even in larger lists."""
        inp = [
            {'id': 'w1', 'start_minute': 100, 'end_minute': 200},
            {'id': 'w2', 'start_minute': 300, 'end_minute': 400},
            {'id': 'w3', 'start_minute': 500, 'end_minute': 600},
            {'id': 'w1', 'start_minute': 700, 'end_minute': 800},
        ]
        with self.assertRaises(ValueError) as ctx:
            validate_ac_heating_windows(inp)
        self.assertIn("Duplicate window id 'w1'", str(ctx.exception))

    def test_reject_collision_with_auto_assigned_id(self):
        """Collision between an auto-assigned ID and an explicit ID raises ValueError."""
        inp = [
            {'start_minute': 360, 'end_minute': 480},  # auto-assigned window_1
            {'id': 'window_1', 'start_minute': 960, 'end_minute': 1200},
        ]
        with self.assertRaises(ValueError) as ctx:
            validate_ac_heating_windows(inp)
        self.assertIn("Duplicate window id 'window_1'", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 7. Pure Function Properties (No Side Effects)
    # -------------------------------------------------------------------------

    def test_pure_function_no_side_effects(self):
        """Validating windows emits no diagnostics and causes no external side effects."""
        diags_before = get_diagnostics()
        inp = [
            {'id': 'morning', 'start_minute': 360, 'end_minute': 480},
            {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
        ]
        out1 = validate_ac_heating_windows(inp)
        out2 = validate_ac_heating_windows(inp)
        self.assertEqual(out1, out2)
        self.assertEqual(get_diagnostics(), diags_before)


if __name__ == '__main__':
    unittest.main()
