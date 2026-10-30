"""Tests for Stage 1 of optional mixed battery module sizes."""
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from feasibility import assumptions, engine, shared_state
from test_support import IsolatedDefaultsTestCase


class MixedBatteryTests(IsolatedDefaultsTestCase):
    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_path = Path(self.temp_dir.name) / 'current_scenario.json'
        self.state_patch = patch.object(shared_state, 'STATE_PATH', self.state_path)
        self.state_patch.start()

    def tearDown(self):
        self.state_patch.stop()
        self.temp_dir.cleanup()
        super().tearDown()

    def test_spec_definitions_and_defaults(self):
        """Verify SPEC entries, units, bounds, and groups for all mixed battery fields."""
        spec = assumptions.SPEC
        self.assertIn('battery_mixed_sizes', spec)
        self.assertIn('battery_secondary_module_kwh', spec)
        self.assertIn('battery_secondary_modules', spec)

        self.assertEqual(spec['battery_mixed_sizes'][0], False)
        self.assertEqual(spec['battery_mixed_sizes'][1], 'boolean')
        self.assertIsNone(spec['battery_mixed_sizes'][2])
        self.assertEqual(spec['battery_mixed_sizes'][3], 'equipment')

        self.assertEqual(spec['battery_secondary_module_kwh'][0], 6.9)
        self.assertEqual(spec['battery_secondary_module_kwh'][1], 'usable kWh/module')
        self.assertEqual(spec['battery_secondary_module_kwh'][2], [0, None])
        self.assertEqual(spec['battery_secondary_module_kwh'][3], 'equipment')

        self.assertEqual(spec['battery_secondary_modules'][0], 0)
        self.assertEqual(spec['battery_secondary_modules'][1], 'modules')
        self.assertEqual(spec['battery_secondary_modules'][2], [0, None])
        self.assertEqual(spec['battery_secondary_modules'][3], 'equipment')

        self.assertIn('battery_secondary_modules', engine.INTEGER_NAMES)

        d = assumptions.defaults()
        self.assertFalse(d['battery_mixed_sizes'])
        self.assertEqual(d['battery_secondary_modules'], 0)
        self.assertEqual(d['battery_secondary_module_kwh'], 6.9)

    def test_legacy_single_size_mode_preservation(self):
        """Single-size mode preserves legacy normalization and keeps secondary fields dormant."""
        # Baseline single size mode
        s = engine.validate({
            'battery_mixed_sizes': False,
            'battery_modules': 3,
            'battery_module_kwh': 5.0,
            'battery_secondary_modules': 2,
            'battery_secondary_module_kwh': 6.9,
        })
        # Dormant secondary values must not influence capacity: 3 * 5.0 = 15.0
        self.assertEqual(s['battery_kwh'], 15.0)
        self.assertEqual(s['battery_modules'], 3)
        self.assertEqual(s['battery_module_kwh'], 5.0)
        self.assertEqual(s['battery_secondary_modules'], 2)
        self.assertEqual(s['battery_secondary_module_kwh'], 6.9)

        # Legacy total derivation from module capacity change
        s2 = engine.validate({'battery_modules': 2, 'battery_module_kwh': 4.0})
        self.assertEqual(s2['battery_kwh'], 8.0)

        # Legacy rewriting of module capacity when battery_kwh is supplied
        s3 = engine.validate({'battery_modules': 2, 'battery_kwh': 12.0})
        self.assertEqual(s3['battery_module_kwh'], 6.0)
        self.assertEqual(s3['battery_kwh'], 12.0)

        # Combinations helper in single-size mode generates count * size
        combos = assumptions.enumerate_battery_combinations(
            min_modules=1, max_modules=3, module_kwh=5.0, mixed_sizes=False
        )
        self.assertEqual(len(combos), 3)
        self.assertEqual([c['battery_kwh'] for c in combos], [5.0, 10.0, 15.0])
        self.assertTrue(all(c['secondary_modules'] == 0 for c in combos))

    def test_all_nine_screenshot_combinations(self):
        """Capacities 5 and 6.9 kWh generate the 9 exact combinations for 1..3 modules."""
        # 1 mod: 5.0, 6.9
        # 2 mod: 10.0, 11.9, 13.8
        # 3 mod: 15.0, 16.9, 18.8, 20.7
        expected_capacities = [5.0, 6.9, 10.0, 11.9, 13.8, 15.0, 16.9, 18.8, 20.7]
        expected_specs = [
            # (total, sec, first, expected_kwh)
            (1, 0, 1, 5.0),
            (1, 1, 0, 6.9),
            (2, 0, 2, 10.0),
            (2, 1, 1, 11.9),
            (2, 2, 0, 13.8),
            (3, 0, 3, 15.0),
            (3, 1, 2, 16.9),
            (3, 2, 1, 18.8),
            (3, 3, 0, 20.7),
        ]

        combos = assumptions.enumerate_battery_combinations(
            min_modules=1,
            max_modules=3,
            module_kwh=5.0,
            secondary_module_kwh=6.9,
            mixed_sizes=True,
        )
        self.assertEqual(len(combos), 9)
        for c, exp_kwh in zip(combos, expected_capacities):
            self.assertAlmostEqual(c['battery_kwh'], exp_kwh, places=9)

        # Check each composition's details and identities
        ids = set()
        for c, (total, sec, first, kwh) in zip(combos, expected_specs):
            self.assertEqual(c['battery_modules'], total)
            self.assertEqual(c['battery_secondary_modules'], sec)
            self.assertEqual(c['first_modules'], first)
            self.assertAlmostEqual(c['battery_kwh'], kwh, places=9)
            self.assertEqual(c['battery_module_kwh'], 5.0)
            self.assertEqual(c['battery_secondary_module_kwh'], 6.9)
            self.assertTrue(c['battery_mixed_sizes'])
            self.assertNotIn(c['id'], ids, 'Every composition must have a unique stable ID')
            ids.add(c['id'])

            # Direct calculate_battery_composition test
            direct = assumptions.calculate_battery_composition(
                total_modules=total,
                secondary_modules=sec,
                module_kwh=5.0,
                secondary_module_kwh=6.9,
                mixed_sizes=True,
            )
            self.assertEqual(direct['battery_kwh'], c['battery_kwh'])
            self.assertEqual(direct['id'], c['id'])

            # Engine validate test
            validated = engine.validate({
                'battery_mixed_sizes': True,
                'battery_modules': total,
                'battery_secondary_modules': sec,
                'battery_module_kwh': 5.0,
                'battery_secondary_module_kwh': 6.9,
            })
            self.assertAlmostEqual(validated['battery_kwh'], kwh, places=9)

    def test_zero_storage(self):
        """Zero module count means zero usable storage."""
        # Single size mode zero storage
        s_single = engine.validate({'battery_modules': 0})
        self.assertEqual(s_single['battery_kwh'], 0.0)

        # Mixed size mode zero storage
        s_mixed = engine.validate({
            'battery_mixed_sizes': True,
            'battery_modules': 0,
            'battery_secondary_modules': 0,
            'battery_module_kwh': 5.0,
            'battery_secondary_module_kwh': 6.9,
        })
        self.assertEqual(s_mixed['battery_kwh'], 0.0)

        # Helper with zero modules
        c_zero = assumptions.calculate_battery_composition(
            total_modules=0, secondary_modules=0, mixed_sizes=True
        )
        self.assertEqual(c_zero['battery_kwh'], 0.0)

        # Enumeration starting at 0
        combos = assumptions.enumerate_battery_combinations(
            min_modules=0, max_modules=1, module_kwh=5.0, secondary_module_kwh=6.9, mixed_sizes=True
        )
        self.assertEqual(len(combos), 3)  # 0m_0sec, 1m_0sec, 1m_1sec
        self.assertEqual(combos[0]['battery_modules'], 0)
        self.assertEqual(combos[0]['battery_kwh'], 0.0)

    def test_invalid_types_fractional_negative_nonfinite(self):
        """Validation rejects non-integer, negative, boolean, and non-finite values."""
        bad_cases = [
            {'battery_modules': 1.5},
            {'battery_modules': -1},
            {'battery_modules': float('nan')},
            {'battery_modules': float('inf')},
            {'battery_modules': True},
            {'battery_secondary_modules': 1.5},
            {'battery_secondary_modules': -1},
            {'battery_secondary_modules': float('nan')},
            {'battery_secondary_modules': float('inf')},
            {'battery_secondary_modules': True},
            {'battery_secondary_module_kwh': -6.9},
            {'battery_secondary_module_kwh': float('nan')},
            {'battery_secondary_module_kwh': float('inf')},
            {'battery_secondary_module_kwh': True},
            {'battery_mixed_sizes': 'yes'},
            {'battery_mixed_sizes': 1},
        ]
        for bad in bad_cases:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    engine.validate(bad)

    def test_secondary_greater_than_total_rejected(self):
        """Mixed mode rejects configurations where secondary module count exceeds total."""
        with self.assertRaisesRegex(ValueError, 'Secondary battery module count cannot exceed total'):
            engine.validate({
                'battery_mixed_sizes': True,
                'battery_modules': 2,
                'battery_secondary_modules': 3,
            })

        with self.assertRaisesRegex(ValueError, 'Secondary battery module count cannot exceed total'):
            engine.validate({
                'battery_mixed_sizes': True,
                'battery_modules': 0,
                'battery_secondary_modules': 1,
            })

        with self.assertRaisesRegex(ValueError, 'Secondary module count .* cannot exceed total'):
            assumptions.calculate_battery_composition(
                total_modules=2, secondary_modules=3, mixed_sizes=True
            )

    def test_strictly_positive_capacity_required_for_used_sizes_in_mixed_mode(self):
        """Each module size actually used in mixed mode must have strictly positive capacity."""
        # First size is used (first_count = 2 - 1 = 1 > 0), but capacity is 0 -> reject
        with self.assertRaisesRegex(ValueError, 'First battery module size must be strictly positive'):
            engine.validate({
                'battery_mixed_sizes': True,
                'battery_modules': 2,
                'battery_secondary_modules': 1,
                'battery_module_kwh': 0.0,
                'battery_secondary_module_kwh': 6.9,
            })

        # Secondary size is used (secondary_modules = 1 > 0), but capacity is 0 -> reject
        with self.assertRaisesRegex(ValueError, 'Secondary battery module size must be strictly positive'):
            engine.validate({
                'battery_mixed_sizes': True,
                'battery_modules': 2,
                'battery_secondary_modules': 1,
                'battery_module_kwh': 5.0,
                'battery_secondary_module_kwh': 0.0,
            })

        # When first size is NOT used (total=2, secondary=2 -> first=0), 0 capacity for first size is tolerated
        s_all_sec = engine.validate({
            'battery_mixed_sizes': True,
            'battery_modules': 2,
            'battery_secondary_modules': 2,
            'battery_module_kwh': 0.0,
            'battery_secondary_module_kwh': 6.9,
        })
        self.assertEqual(s_all_sec['battery_kwh'], 13.8)

        # When secondary size is NOT used (total=2, secondary=0), 0 capacity for secondary size is tolerated
        s_all_first = engine.validate({
            'battery_mixed_sizes': True,
            'battery_modules': 2,
            'battery_secondary_modules': 0,
            'battery_module_kwh': 5.0,
            'battery_secondary_module_kwh': 0.0,
        })
        self.assertEqual(s_all_first['battery_kwh'], 10.0)

    def test_mismatched_stale_total_in_mixed_mode(self):
        """Supplied stale battery_kwh in mixed mode is ignored and does not rewrite physical sizes."""
        # Pass a mismatched total of 999.0
        s = engine.validate({
            'battery_mixed_sizes': True,
            'battery_modules': 3,
            'battery_secondary_modules': 1,
            'battery_module_kwh': 5.0,
            'battery_secondary_module_kwh': 6.9,
            'battery_kwh': 999.0,
        })
        # Derived: 2 * 5.0 + 1 * 6.9 = 16.9
        self.assertAlmostEqual(s['battery_kwh'], 16.9, places=9)
        # Neither size rewritten or averaged
        self.assertEqual(s['battery_module_kwh'], 5.0)
        self.assertEqual(s['battery_secondary_module_kwh'], 6.9)

        # Also when stale total matches single size mode (3 * 5.0 = 15.0)
        s2 = engine.validate({
            'battery_mixed_sizes': True,
            'battery_modules': 3,
            'battery_secondary_modules': 1,
            'battery_module_kwh': 5.0,
            'battery_secondary_module_kwh': 6.9,
            'battery_kwh': 15.0,
        })
        self.assertAlmostEqual(s2['battery_kwh'], 16.9, places=9)
        self.assertEqual(s2['battery_module_kwh'], 5.0)
        self.assertEqual(s2['battery_secondary_module_kwh'], 6.9)

    def test_persistence_in_shared_defaults(self):
        """All six battery fields save and reset coherently in shared user defaults."""
        defaults_path = Path(self.temp_dir.name) / 'user_defaults.json'
        with patch.object(assumptions, 'USER_DEFAULTS_PATH', defaults_path):
            assumptions.save_user_default('battery_mixed_sizes', True, engine.validate, path=defaults_path)
            assumptions.save_user_default('battery_modules', 3, engine.validate, path=defaults_path)
            assumptions.save_user_default('battery_secondary_modules', 1, engine.validate, path=defaults_path)
            assumptions.save_user_default('battery_module_kwh', 5.0, engine.validate, path=defaults_path)
            assumptions.save_user_default('battery_secondary_module_kwh', 6.9, engine.validate, path=defaults_path)

            stored = json.loads(defaults_path.read_text())['overrides']
            for key in assumptions.BATTERY_SETTINGS:
                self.assertIn(key, stored)

            d = assumptions.defaults(path=defaults_path)
            self.assertTrue(d['battery_mixed_sizes'])
            self.assertEqual(d['battery_modules'], 3)
            self.assertEqual(d['battery_secondary_modules'], 1)
            self.assertEqual(d['battery_module_kwh'], 5.0)
            self.assertEqual(d['battery_secondary_module_kwh'], 6.9)
            self.assertAlmostEqual(d['battery_kwh'], 16.9, places=9)

    def test_partial_state_edits_against_saved_composition(self):
        """Partial updates in shared_state normalize against the saved scenario rather than defaults."""
        # 1. Initialize a mixed-battery saved scenario (modules=3, sec=1, sizes 5.0 and 6.9 -> 16.9 kWh)
        init_state = shared_state.update_state({
            'settings': {
                'battery_mixed_sizes': True,
                'battery_modules': 3,
                'battery_secondary_modules': 1,
                'battery_module_kwh': 5.0,
                'battery_secondary_module_kwh': 6.9,
            }
        })
        self.assertAlmostEqual(init_state['settings']['battery_kwh'], 16.9, places=9)

        # 2. Send partial update: only change battery_secondary_modules to 2.
        # Defaults have battery_modules = 1; if validated against defaults, 2 > 1 would error.
        # Validated against saved scenario (modules = 3), 2 <= 3 is valid and yields 18.8 kWh.
        updated = shared_state.update_state({'settings': {'battery_secondary_modules': 2}})
        self.assertEqual(updated['settings']['battery_modules'], 3)
        self.assertEqual(updated['settings']['battery_secondary_modules'], 2)
        # first=1 (5.0) + sec=2 (13.8) = 18.8
        self.assertAlmostEqual(updated['settings']['battery_kwh'], 18.8, places=9)
        self.assertEqual(updated['settings']['battery_module_kwh'], 5.0)
        self.assertEqual(updated['settings']['battery_secondary_module_kwh'], 6.9)

        # 3. Partial update with stale battery_kwh must not override composition or rewrite sizes
        updated2 = shared_state.update_state({
            'settings': {'battery_modules': 2, 'battery_kwh': 999.0}
        })
        self.assertEqual(updated2['settings']['battery_modules'], 2)
        self.assertEqual(updated2['settings']['battery_secondary_modules'], 2)  # retained from saved state
        # first=0 (0) + sec=2 (13.8) = 13.8
        self.assertAlmostEqual(updated2['settings']['battery_kwh'], 13.8, places=9)
        self.assertEqual(updated2['settings']['battery_module_kwh'], 5.0)
        self.assertEqual(updated2['settings']['battery_secondary_module_kwh'], 6.9)

        # 4. Partial update in single-size mode preserves single-size patch semantics
        shared_state.update_state({
            'settings': {'battery_mixed_sizes': False, 'battery_modules': 2, 'battery_module_kwh': 5.0}
        })
        # Sending only battery_kwh in single mode rewrites module capacity: 12.0 / 2 = 6.0
        updated_single = shared_state.update_state({'settings': {'battery_kwh': 12.0}})
        self.assertEqual(updated_single['settings']['battery_kwh'], 12.0)
        self.assertEqual(updated_single['settings']['battery_module_kwh'], 6.0)

    def test_combination_enumeration_bounds_and_safety(self):
        """Generator bounds generation before allocating and rejects invalid parameters."""
        # Exceeding max_combinations raises ValueError before allocating
        with self.assertRaisesRegex(ValueError, 'exceeds maximum allowed limit'):
            assumptions.enumerate_battery_combinations(
                min_modules=0, max_modules=100, mixed_sizes=True, max_combinations=50
            )

        # Negative min_modules
        with self.assertRaises(ValueError):
            assumptions.enumerate_battery_combinations(min_modules=-1, max_modules=5)

        # max_modules < min_modules
        with self.assertRaises(ValueError):
            assumptions.enumerate_battery_combinations(min_modules=5, max_modules=3)

        # Missing max_modules
        with self.assertRaises(ValueError):
            assumptions.enumerate_battery_combinations(min_modules=0, max_modules=None)

    def test_distinct_identities_when_totals_match(self):
        """Compositions with equal total capacity have distinct stable identities."""
        # 2 modules of 5.0 = 10.0 kWh (id: '2m_0sec')
        comp_a = assumptions.calculate_battery_composition(
            total_modules=2,
            secondary_modules=0,
            module_kwh=5.0,
            secondary_module_kwh=10.0,
            mixed_sizes=True,
        )
        # 1 module of 10.0 = 10.0 kWh (id: '1m_1sec')
        comp_b = assumptions.calculate_battery_composition(
            total_modules=1,
            secondary_modules=1,
            module_kwh=5.0,
            secondary_module_kwh=10.0,
            mixed_sizes=True,
        )
        self.assertEqual(comp_a['battery_kwh'], 10.0)
        self.assertEqual(comp_b['battery_kwh'], 10.0)
        self.assertNotEqual(comp_a['id'], comp_b['id'])
        self.assertEqual(comp_a['id'], '2m_0sec')
        self.assertEqual(comp_b['id'], '1m_1sec')

    def test_arbitrary_high_precision_capacity_equality(self):
        """Arbitrary high-precision capacities retain exact unrounded direct arithmetic."""
        first_cap = 2.35001234567891
        sec_cap = 6.90123456789123
        first_count = 2
        sec_count = 3
        total_count = first_count + sec_count
        expected_direct = first_count * first_cap + sec_count * sec_cap

        # Helper
        comp = assumptions.calculate_battery_composition(
            total_modules=total_count,
            secondary_modules=sec_count,
            module_kwh=first_cap,
            secondary_module_kwh=sec_cap,
            mixed_sizes=True,
        )
        self.assertEqual(comp['battery_kwh'], expected_direct)

        # Validation
        validated = engine.validate({
            'battery_mixed_sizes': True,
            'battery_modules': total_count,
            'battery_secondary_modules': sec_count,
            'battery_module_kwh': first_cap,
            'battery_secondary_module_kwh': sec_cap,
        })
        self.assertEqual(validated['battery_kwh'], expected_direct)


class MixedBatteryOptimizerTests(IsolatedDefaultsTestCase):
    def test_small_exhaustive_search_includes_all_nine_combinations(self):
        """Small exhaustive search evaluates all nine 1..3 module combinations for 5/6.9 kWh."""
        settings = {
            'battery_mixed_sizes': True,
            'battery_module_kwh': 5.0,
            'battery_secondary_module_kwh': 6.9,
            'horizon_years': 1,
        }
        search = {
            'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_modules': 1,
            'battery_max_modules': 3,
        }
        result = engine.optimize_configuration(settings, search)
        self.assertTrue(result['search']['exact'])
        self.assertTrue(result['search']['certified_global_optimum'])
        self.assertFalse(result['search']['best_found'])
        self.assertEqual(result['search']['search_method'], 'exhaustive_integer_enumeration')

        # south=0 has no PV, so positive modules are rejected. south=1 evaluates all 9 combinations.
        pv_candidates = [c for c in result['candidates'] if c['settings']['south_panels'] == 1]
        self.assertEqual(len(pv_candidates), 9)

        expected_pairs = [(1, 0), (1, 1), (2, 0), (2, 1), (2, 2), (3, 0), (3, 1), (3, 2), (3, 3)]
        pairs = [(c['battery_modules'], c['battery_secondary_modules']) for c in pv_candidates]
        self.assertEqual(pairs, expected_pairs)

        expected_kwh = [5.0, 6.9, 10.0, 11.9, 13.8, 15.0, 16.9, 18.8, 20.7]
        for c, exp in zip(pv_candidates, expected_kwh):
            self.assertAlmostEqual(c['battery_kwh'], exp, places=9)
            self.assertEqual(c['battery_kwh'], c['settings']['battery_kwh'])

        # Check readable labels
        labels_by_pair = {
            (c['battery_modules'], c['battery_secondary_modules']): c['label']
            for c in pv_candidates
        }
        self.assertIn('1 × 5 kWh', labels_by_pair[(1, 0)])
        self.assertIn('1 × 6.9 kWh', labels_by_pair[(1, 1)])
        self.assertIn('1 × 5 + 1 × 6.9 kWh', labels_by_pair[(2, 1)])
        self.assertIn('1 × 5 + 2 × 6.9 kWh', labels_by_pair[(3, 2)])

    def test_nonzero_saved_secondary_count_does_not_corrupt_baseline(self):
        """Saved scenario with nonzero secondary count does not fail zero baseline validation."""
        settings = {
            'battery_mixed_sizes': True,
            'battery_modules': 3,
            'battery_secondary_modules': 2,
            'battery_module_kwh': 5.0,
            'battery_secondary_module_kwh': 6.9,
            'horizon_years': 1,
        }
        search = {
            'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_max_modules': 1,
        }
        result = engine.optimize_configuration(settings, search)
        self.assertIsNotNone(result['best_candidate'])

    def test_zero_only_battery_search_works(self):
        """Searching exclusively 0 modules in mixed mode produces valid 0-storage candidates."""
        settings = {
            'battery_mixed_sizes': True,
            'battery_module_kwh': 5.0,
            'battery_secondary_module_kwh': 6.9,
            'horizon_years': 1,
        }
        search = {
            'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_modules': 0,
            'battery_max_modules': 0,
        }
        result = engine.optimize_configuration(settings, search)
        for c in result['candidates']:
            self.assertEqual(c['battery_modules'], 0)
            self.assertEqual(c['battery_secondary_modules'], 0)
            self.assertEqual(c['battery_kwh'], 0.0)

    def test_equal_capacity_distinct_mixes_remain_distinct(self):
        """Compositions with equal total capacity have distinct candidate IDs and signatures."""
        settings = {
            'battery_mixed_sizes': True,
            'battery_module_kwh': 5.0,
            'battery_secondary_module_kwh': 10.0,
            'horizon_years': 1,
        }
        search = {
            'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_modules': 1,
            'battery_max_modules': 2,
        }
        result = engine.optimize_configuration(settings, search)
        ten_kwh_candidates = [
            c for c in result['candidates']
            if c['settings']['south_panels'] == 1 and math.isclose(c['battery_kwh'], 10.0)
        ]
        self.assertEqual(len(ten_kwh_candidates), 2)
        c_2m0s = next(c for c in ten_kwh_candidates if c['battery_modules'] == 2 and c['battery_secondary_modules'] == 0)
        c_1m1s = next(c for c in ten_kwh_candidates if c['battery_modules'] == 1 and c['battery_secondary_modules'] == 1)
        self.assertNotEqual(c_2m0s['id'], c_1m1s['id'])
        self.assertIn('_2_0', c_2m0s['id'])
        self.assertIn('_1_1', c_1m1s['id'])

    def test_arbitrary_capacities_retain_arithmetic_precision(self):
        """High-precision capacities in optimizer candidates match direct unrounded arithmetic."""
        first_cap = 2.35001234567891
        sec_cap = 6.90123456789123
        settings = {
            'battery_mixed_sizes': True,
            'battery_module_kwh': first_cap,
            'battery_secondary_module_kwh': sec_cap,
            'horizon_years': 1,
        }
        search = {
            'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_modules': 2,
            'battery_max_modules': 2,
        }
        result = engine.optimize_configuration(settings, search)
        c_mixed = next(
            c for c in result['candidates']
            if c['settings']['south_panels'] == 1 and c['battery_modules'] == 2 and c['battery_secondary_modules'] == 1
        )
        expected_kwh = 1 * first_cap + 1 * sec_cap
        self.assertEqual(c_mixed['battery_kwh'], expected_kwh)
        self.assertEqual(c_mixed['settings']['battery_kwh'], expected_kwh)

    def test_each_candidate_can_be_passed_to_evaluate_unchanged(self):
        """Every candidate row can be passed directly to evaluate() with identical capacity."""
        settings = {
            'battery_mixed_sizes': True,
            'battery_module_kwh': 5.0,
            'battery_secondary_module_kwh': 6.9,
            'horizon_years': 1,
        }
        search = {
            'panel_maxima': {'south': 1, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
            'battery_min_modules': 1,
            'battery_max_modules': 2,
        }
        result = engine.optimize_configuration(settings, search)
        for c in result['candidates']:
            ev = engine.evaluate(c['settings'])
            self.assertEqual(ev['settings']['battery_kwh'], c['battery_kwh'])
            self.assertEqual(ev['settings']['battery_modules'], c['battery_modules'])
            self.assertEqual(ev['settings']['battery_secondary_modules'], c['battery_secondary_modules'])

    def test_large_mixed_search_remains_bounded_and_honestly_labeled(self):
        """Large mixed search evaluates deterministically, stays within budget, and never claims certified optimum."""
        maxima = {'south': 30, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0}
        settings = {
            'battery_mixed_sizes': True,
            'battery_module_kwh': 5.0,
            'battery_secondary_module_kwh': 6.9,
        }
        def fake_evaluate(s_candidate):
            valid = engine.validate(s_candidate)
            val = valid['south_panels'] + valid['battery_modules']
            metrics = {k: {'median': float(val), 'mean': float(val)} for k in ['npv_eur', 'autarky', 'self_consumption', 'payback_years', 'discounted_payback_years']}
            metrics.update(simple_payback_of_mean_cash_flows=float(val), discounted_payback_of_mean_cash_flows=float(val))
            finance = {'capex_eur': float(val), 'pv_incremental': {'npv': float(val)}, 'whole_package': {'npv': float(val)}, 'lcoe': {}}
            return {'settings': valid, 'layout': {'theoretical': val > 15, 'kwp': val * 0.4, 'panel_count': val, 'counts': {}}, 'metrics': metrics, 'finance': finance}

        with patch('feasibility.engine.evaluate', side_effect=fake_evaluate):
            result = engine.optimize_configuration(settings, {'panel_maxima': maxima, 'battery_min_modules': 0, 'battery_max_modules': 50})

        self.assertFalse(result['search']['exact'])
        self.assertTrue(result['search']['best_found'])
        self.assertFalse(result['search']['certified_global_optimum'])
        self.assertEqual(result['search']['search_method'], 'sampled_discrete_coordinate_refinement')
        self.assertLessEqual(result['search']['evaluated_count'], result['search']['evaluation_budget'])
        self.assertLessEqual(result['search']['evaluation_budget'], 400)
        # Search space for mixed mode: 31 roofs * sum(m+1 for m in 0..50) = 31 * (51 * 52 // 2) = 31 * 1326 = 41106
        self.assertEqual(result['search']['search_space_size'], 31 * 1326)
        self.assertIn('Large searches are best-found results', result['limitations'][0])

    def test_battery_sizing_high_precision_unrounded_parity(self):
        """Smoke test verifying that high-precision module sizes do not cause capacity/settings mismatches."""
        s = {
            'battery_mixed_sizes': True,
            'battery_module_kwh': 5.123456789123,
            'battery_secondary_module_kwh': 6.789012345678,
            'battery_modules': 2,
            'battery_secondary_modules': 1,
            'south_panels': 12,
        }
        res = engine.battery_sizing(s, {'max_capacity': 15})
        for row in res['chart1_capacity_dispatch']['capacities']:
            self.assertEqual(row['capacity_kwh'], row['settings']['battery_kwh'])
            self.assertEqual(row['capacity_kwh'], row['composition']['battery_kwh'])
        for cell in res['chart4_npv_heatmap']['cells']:
            self.assertEqual(cell['battery_kwh'], cell['settings']['battery_kwh'])
            self.assertEqual(cell['battery_kwh'], cell['composition']['battery_kwh'])
        cur = res['chart4_npv_heatmap']['current_configuration']
        self.assertEqual(cur['battery_kwh'], cur['composition']['battery_kwh'])
        self.assertEqual(cur['battery_kwh'], 5.123456789123 + 6.789012345678)

    def test_save_mixed_battery_defaults_with_context(self):
        """Saving a battery setting with battery_settings context atomically validates and updates the full group."""
        with tempfile.TemporaryDirectory() as temp_dir:
            defaults_path = Path(temp_dir) / 'user_defaults.json'
            # Default is single mode with 1 module, 0 secondary
            self.assertFalse(assumptions.defaults()['battery_mixed_sizes'])
            self.assertEqual(assumptions.defaults()['battery_modules'], 1)

            # Context has 3 modules, 2 secondary, 5.0 and 6.9 kWh
            ctx = {
                'battery_mixed_sizes': True,
                'battery_modules': 3,
                'battery_secondary_modules': 2,
                'battery_module_kwh': 5.0,
                'battery_secondary_module_kwh': 6.9,
                'battery_kwh': 18.8,
            }

            # Saving battery_secondary_modules = 2 with context should succeed even though previous default modules was 1
            saved = assumptions.save_user_default(
                'battery_secondary_modules',
                2,
                engine.validate,
                path=defaults_path,
                battery_settings=ctx
            )
            self.assertEqual(saved['value'], 2)

            # Verify saved file overrides directly
            saved_file = json.loads(defaults_path.read_text())['overrides']
            for k in assumptions.BATTERY_SETTINGS:
                self.assertIn(k, saved_file)
            self.assertEqual(saved_file['battery_mixed_sizes']['value'], True)
            self.assertEqual(saved_file['battery_modules']['value'], 3)
            self.assertEqual(saved_file['battery_secondary_modules']['value'], 2)
            self.assertEqual(saved_file['battery_module_kwh']['value'], 5.0)
            self.assertEqual(saved_file['battery_secondary_module_kwh']['value'], 6.9)
            self.assertEqual(saved_file['battery_kwh']['value'], 1 * 5.0 + 2 * 6.9)

    def test_save_single_battery_defaults_over_mixed(self):
        """Saving a single-mode composition atomically clears/overwrites previous mixed defaults."""
        with tempfile.TemporaryDirectory() as temp_dir:
            defaults_path = Path(temp_dir) / 'user_defaults.json'
            # Start with mixed defaults
            mixed_ctx = {
                'battery_mixed_sizes': True,
                'battery_modules': 3,
                'battery_secondary_modules': 2,
                'battery_module_kwh': 5.0,
                'battery_secondary_module_kwh': 6.9,
                'battery_kwh': 18.8,
            }
            assumptions.save_user_default('battery_modules', 3, engine.validate, path=defaults_path, battery_settings=mixed_ctx)

            # Now save a single composition: 2 modules of 4.5 kWh (total 9.0)
            single_ctx = {
                'battery_mixed_sizes': False,
                'battery_modules': 2,
                'battery_secondary_modules': 0,
                'battery_module_kwh': 4.5,
                'battery_secondary_module_kwh': 6.9,
                'battery_kwh': 9.0,
            }
            saved = assumptions.save_user_default('battery_modules', 2, engine.validate, path=defaults_path, battery_settings=single_ctx)
            self.assertEqual(saved['value'], 2)

            saved_file = json.loads(defaults_path.read_text())['overrides']
            self.assertEqual(saved_file['battery_mixed_sizes']['value'], False)
            self.assertEqual(saved_file['battery_modules']['value'], 2)
            self.assertEqual(saved_file['battery_secondary_modules']['value'], 0)
            self.assertEqual(saved_file['battery_module_kwh']['value'], 4.5)
            self.assertEqual(saved_file['battery_kwh']['value'], 9.0)


if __name__ == '__main__':
    unittest.main()


