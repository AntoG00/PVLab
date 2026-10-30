"""Tests for authoritative AC space heating budget calculation and deduplication.

Verifies:
- Independent hand-calculated literal synthetic cases.
- Argument validation (finite, non-negative, numeric, bool rejected).
- Zero-demand and zero-month edge cases.
- Memory isolation and absence of array aliasing.
- Invariance across heating schedules, operating windows, and timezone modes.
- Consistency between helper and production allocator / load_profiles at p0.
- Preserved strict tolerances against frozen regression fixtures.
"""
import copy
import math
import os
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
sys.path.insert(0, str(PROJECT_ROOT / 'feasibility' / 'vendor'))
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(HERE))

# test_support MUST be imported first before persistent engine / state operations
import test_support  # noqa: F401

import numpy as np
import feasibility.engine as engine
from feasibility.engine import _ac_heating_budget, ac_heating_budget, GAS_MONTHLY
from feasibility.assumptions import defaults
from feasibility.schedule import DEFAULT_AC_HEATING_WINDOWS
from historical_weather import frozen_baseline_weather_context


class AcHeatingBudgetTests(unittest.TestCase):
    """Authoritative test suite for AC heating budget calculation."""

    def test_independent_synthetic_literal_hand_calculation(self):
        """Independent hand-calculated synthetic test case.

        Inputs:
          gas_monthly = [126.0]
          summer_gas_base = 26.0
          gas_pcs = 10.0
          boiler_efficiency = 0.8
          scop = 4.0

        Arithmetic:
          heating_gas_smc = 126.0 - 26.0 = 100.0 Smc
          baseline_useful_heat_kwh = 100.0 * 10.0 * 0.8 = 800.0 kWh_th
          baseline_heating_electricity_kwh = 800.0 / 4.0 = 200.0 kWh_e

        At explicit 25% additional demand (p = 25.0):
          requested_useful_heat_kwh = 800.0 * (1 + 25 / 100) = 1000.0 kWh_th
          requested_heating_electricity_kwh = 1000.0 / 4.0 = 250.0 kWh_e
        """
        s = {
            'summer_gas_base': 26.0,
            'gas_pcs': 10.0,
            'boiler_efficiency': 0.8,
            'scop': 4.0,
        }

        # Baseline (p = 0.0)
        res_p0 = _ac_heating_budget(s, additional_demand_pct=0.0, gas_monthly=[126.0])
        self.assertEqual(res_p0['heating_gas_smc'][0], 100.0)
        self.assertEqual(res_p0['baseline_useful_heat_kwh'][0], 800.0)
        self.assertEqual(res_p0['baseline_heating_electricity_kwh'][0], 200.0)
        self.assertEqual(res_p0['requested_useful_heat_kwh'][0], 800.0)
        self.assertEqual(res_p0['requested_heating_electricity_kwh'][0], 200.0)

        # Explicit 25% (p = 25.0)
        res_p25 = _ac_heating_budget(s, additional_demand_pct=25.0, gas_monthly=[126.0])
        # Baseline remains literal 800 and 200
        self.assertEqual(res_p25['heating_gas_smc'][0], 100.0)
        self.assertEqual(res_p25['baseline_useful_heat_kwh'][0], 800.0)
        self.assertEqual(res_p25['baseline_heating_electricity_kwh'][0], 200.0)
        # Requested scales to literal 1000 and 250
        self.assertEqual(res_p25['requested_useful_heat_kwh'][0], 1000.0)
        self.assertEqual(res_p25['requested_heating_electricity_kwh'][0], 250.0)

        # Public alias test
        res_alias = ac_heating_budget(s, additional_demand_pct=25.0, gas_monthly=[126.0])
        self.assertEqual(res_alias['requested_useful_heat_kwh'][0], 1000.0)
        self.assertEqual(res_alias['requested_heating_electricity_kwh'][0], 250.0)

    def test_multi_month_synthetic_with_zeros_months(self):
        """Synthetic 12-month array with active months and zero-heating summer months."""
        s = {
            'summer_gas_base': 26.0,
            'gas_pcs': 10.0,
            'boiler_efficiency': 0.8,
            'scop': 4.0,
        }
        # Months 1, 2, 11, 12: 126 Smc; Months 3..10: <= 26 Smc (no heating)
        gas_12m = np.array([126.0, 126.0, 26.0, 22.0, 18.0, 12.0, 10.0, 10.0, 15.0, 26.0, 126.0, 126.0])

        res = _ac_heating_budget(s, additional_demand_pct=25.0, gas_monthly=gas_12m)

        # Check shapes
        self.assertEqual(len(res['heating_gas_smc']), 12)
        self.assertEqual(len(res['requested_heating_electricity_kwh']), 12)

        # Active months: 0, 1, 10, 11
        for m_idx in [0, 1, 10, 11]:
            self.assertEqual(res['heating_gas_smc'][m_idx], 100.0)
            self.assertEqual(res['baseline_useful_heat_kwh'][m_idx], 800.0)
            self.assertEqual(res['baseline_heating_electricity_kwh'][m_idx], 200.0)
            self.assertEqual(res['requested_useful_heat_kwh'][m_idx], 1000.0)
            self.assertEqual(res['requested_heating_electricity_kwh'][m_idx], 250.0)

        # Inactive / summer months: 2..9
        for m_idx in range(2, 10):
            self.assertEqual(res['heating_gas_smc'][m_idx], 0.0)
            self.assertEqual(res['baseline_useful_heat_kwh'][m_idx], 0.0)
            self.assertEqual(res['baseline_heating_electricity_kwh'][m_idx], 0.0)
            self.assertEqual(res['requested_useful_heat_kwh'][m_idx], 0.0)
            self.assertEqual(res['requested_heating_electricity_kwh'][m_idx], 0.0)

    def test_zero_demand_scenario(self):
        """When measured gas is entirely baseline (gas <= summer_gas_base for all months)."""
        s = {
            'summer_gas_base': 30.0,
            'gas_pcs': 10.0,
            'boiler_efficiency': 0.8,
            'scop': 4.0,
        }
        gas_all_low = np.full(12, 20.0)
        res = _ac_heating_budget(s, additional_demand_pct=50.0, gas_monthly=gas_all_low)

        self.assertTrue(np.all(res['heating_gas_smc'] == 0.0))
        self.assertTrue(np.all(res['baseline_useful_heat_kwh'] == 0.0))
        self.assertTrue(np.all(res['requested_useful_heat_kwh'] == 0.0))
        self.assertTrue(np.all(res['baseline_heating_electricity_kwh'] == 0.0))
        self.assertTrue(np.all(res['requested_heating_electricity_kwh'] == 0.0))

    def test_invalid_pct_validation(self):
        """Validate that additional_demand_pct rejects bool, non-numeric, negative, and non-finite."""
        s = defaults()

        # Reject booleans (bool is a subclass of int in Python)
        with self.assertRaises(TypeError):
            _ac_heating_budget(s, additional_demand_pct=True)
        with self.assertRaises(TypeError):
            _ac_heating_budget(s, additional_demand_pct=False)

        # Reject non-numeric types
        for invalid in ["25", "0.0", None, [25], {"pct": 25}]:
            with self.assertRaises(TypeError):
                _ac_heating_budget(s, additional_demand_pct=invalid)

        # Reject negative numbers
        for negative in [-0.001, -1.0, -25.0, -100.0]:
            with self.assertRaises(ValueError):
                _ac_heating_budget(s, additional_demand_pct=negative)

        # Reject non-finite values (inf, -inf, nan)
        for non_finite in [math.inf, -math.inf, math.nan, float('nan'), float('inf')]:
            with self.assertRaises(ValueError):
                _ac_heating_budget(s, additional_demand_pct=non_finite)

    def test_array_isolation_no_aliasing_and_nonmutation(self):
        """Verify returned arrays have independent memory buffers and do not mutate inputs."""
        s = defaults()
        gas_in = np.array(GAS_MONTHLY, copy=True)
        res = _ac_heating_budget(s, additional_demand_pct=0.0, gas_monthly=gas_in)

        arr_keys = [
            'heating_gas_smc',
            'baseline_useful_heat_kwh',
            'requested_useful_heat_kwh',
            'baseline_heating_electricity_kwh',
            'requested_heating_electricity_kwh',
        ]

        # Verify pairwise distinct memory
        for i in range(len(arr_keys)):
            for j in range(i + 1, len(arr_keys)):
                k1, k2 = arr_keys[i], arr_keys[j]
                self.assertFalse(
                    np.shares_memory(res[k1], res[k2]),
                    f"Arrays {k1} and {k2} must not share memory.",
                )

        # Verify not sharing memory with input or module constant
        for k in arr_keys:
            self.assertFalse(np.shares_memory(res[k], gas_in))
            self.assertFalse(np.shares_memory(res[k], GAS_MONTHLY))

        # Mutate requested array and verify baseline array remains unchanged
        val_before = float(res['baseline_useful_heat_kwh'][0])
        res['requested_useful_heat_kwh'][0] += 999.0
        self.assertEqual(res['baseline_useful_heat_kwh'][0], val_before)

        # Verify gas_in was not mutated
        np.testing.assert_array_equal(gas_in, GAS_MONTHLY)

    def test_invariance_to_schedules_and_timezone(self):
        """Budgets are physical monthly energy invariants independent of daily schedules or timezone."""
        base_s = defaults()

        b_continuous = _ac_heating_budget(
            dict(base_s, heating_schedule_mode='continuous', timezone_mode='civil')
        )
        b_scheduled = _ac_heating_budget(
            dict(base_s, heating_schedule_mode='scheduled', timezone_mode='civil')
        )
        b_standard = _ac_heating_budget(
            dict(base_s, heating_schedule_mode='scheduled', timezone_mode='standard')
        )
        b_custom_windows = _ac_heating_budget(
            dict(
                base_s,
                heating_schedule_mode='scheduled',
                ac_heating_windows=[{'start_minute': 600, 'end_minute': 1200}],
            )
        )

        for key in b_continuous:
            np.testing.assert_array_equal(b_continuous[key], b_scheduled[key])
            np.testing.assert_array_equal(b_continuous[key], b_standard[key])
            np.testing.assert_array_equal(b_continuous[key], b_custom_windows[key])

    def test_agreement_with_allocator_and_load_profiles_at_p0(self):
        """Actual space heating allocator and load_profiles agree with helper at p0."""
        s = dict(defaults(), load_scenario='electrified', planning_heat_pump=False)
        budget = _ac_heating_budget(s, additional_demand_pct=0.0)

        d = engine.data()
        civil_hours = engine._get_civil_hours(d)

        allocated = engine._allocate_space_heating(
            s, d['temperature'], d['month'], d['hour'], d['valid'], civil_hours, d['years']
        )

        # Ensemble mean annual allocated electricity strictly matches requested helper budget
        self.assertAlmostEqual(
            float(allocated.sum(axis=1).mean()),
            float(budget['requested_heating_electricity_kwh'].sum()),
            places=8,
        )
        # Weather variability: cold years demand more electricity than warm years
        self.assertGreater(float(allocated.sum(axis=1).max() - allocated.sum(axis=1).min()), 100.0)
        # Zero heating during zero-budget summer months
        for mm in [7, 8]:
            self.assertEqual(float(allocated[d['valid'] & (d['month'] == mm)].sum()), 0.0)

        # Baseline scenario test: allocator returns zeros
        s_baseline = dict(s, load_scenario='baseline')
        allocated_baseline = engine._allocate_space_heating(
            s_baseline, d['temperature'], d['month'], d['hour'], d['valid'], civil_hours, d['years']
        )
        self.assertTrue(np.all(allocated_baseline == 0.0))

        # Verify load_profiles reporting path matches helper
        _, _, enduses = engine.load_profiles(s)

        self.assertAlmostEqual(enduses['heating_smc'], float(budget['heating_gas_smc'].sum()), places=10)
        self.assertAlmostEqual(
            enduses['heating_kwh'],
            float(budget['requested_heating_electricity_kwh'].sum()),
            places=10,
        )

    def test_frozen_compatibility_fixture_agreement(self):
        """Verify strict equivalence against saved frozen fixture array."""
        self.enterContext(frozen_baseline_weather_context())
        fixtures_path = Path(os.environ['PV_LEGACY_DATA_ROOT']).parents[1] / 'compatibility/ac_heating_compatibility_fixtures.npz'
        metadata_path = Path(os.environ['PV_LEGACY_DATA_ROOT']).parents[1] / 'compatibility/ac_heating_compatibility_metadata.json'
        if not fixtures_path.exists():
            self.fail(f"Compatibility fixture file not found: {fixtures_path}")
        if not metadata_path.exists():
            self.fail(f"Compatibility metadata file not found: {metadata_path}")

        import json
        with open(metadata_path, 'r', encoding='utf-8') as f:
            meta = json.load(f)

        if 'scenarios' not in meta or 'factory_defaults' not in meta['scenarios']:
            self.fail("factory_defaults scenario missing from compatibility metadata")

        scenario_meta = meta['scenarios']['factory_defaults']
        captured_settings = scenario_meta.get('settings', {})
        s = engine.validate(captured_settings)

        d = engine.data()
        civil_hours = engine._get_civil_hours(d)
        allocated = engine._allocate_space_heating(
            s, d['temperature'], d['month'], d['hour'], d['valid'], civil_hours, d['years']
        )

        with np.load(fixtures_path) as fixtures:
            if 'source_heating_factory_defaults' not in fixtures:
                self.fail("source_heating_factory_defaults missing from compatibility fixtures")
            expected_heating = fixtures['source_heating_factory_defaults']

            # Ensemble mean annual allocated heating strictly conserved against fixture expectation
            self.assertAlmostEqual(
                float(allocated.sum(axis=1).mean()),
                float(expected_heating.sum(axis=1).mean()),
                places=8,
            )
            # Weather variability preserved
            self.assertGreater(float(allocated.sum(axis=1).max() - allocated.sum(axis=1).min()), 50.0)


if __name__ == '__main__':
    unittest.main()
