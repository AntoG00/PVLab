"""Tests for connecting additional AC useful-heat demand percentage to load paths.

Covers Small Task C2 requirements:
1) 0pct current ensemble oracle plus untouched Task A legacy fixture verification.
2) fixed SCOP/windows 25pct -> every raw heating interval 1.25, monthly/annual 1.25 across all years; strict zero outside windows.
3) total load difference equals .25*baseline heating raw array; ev arrays unchanged; DHW/cooking/cooling/enduse historical gas savings unchanged. Include historical baseline load unchanged.
4) helper baseline/requested annual summary matches raw ensemble means.
5) cache changing 0->25->0 gives correct distinct/restored results without cache_clear between calls; schedule & SCOP unmodified.
6) forecast 2027 & 2028 requested budgets preserved and nonheating mapped component unchanged; raw interval sums, never typical averages.
7) baseline and air-to-water HP physical results unaffected by pct.
8) invalid pct rejection, temp persistence old complete export accepted at 0 without allowing other missing fields.
"""
from heating_oracle import heating_oracle
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

import test_support  # noqa: F401
from test_support import IsolatedDefaultsTestCase
from historical_weather import frozen_baseline_weather_context

import numpy as np
import feasibility.engine as engine
import feasibility.shared_state as shared_state

import os
FIXTURES_DIR = Path(os.environ['PV_LEGACY_DATA_ROOT']).parents[1] / 'compatibility'
NPZ_PATH = FIXTURES_DIR / 'ac_heating_compatibility_fixtures.npz'
METADATA_PATH = FIXTURES_DIR / 'ac_heating_compatibility_metadata.json'


class AcHeatingAdditionalDemandTests(IsolatedDefaultsTestCase):
    """Test suite for additional AC space heating demand percentage integration."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.enterClassContext(frozen_baseline_weather_context())
        if not NPZ_PATH.exists() or not METADATA_PATH.exists():
            raise FileNotFoundError("Compatibility fixtures or metadata missing")
        cls.npz = np.load(NPZ_PATH)
        with open(METADATA_PATH, 'r', encoding='utf-8') as f:
            cls.metadata = json.load(f)
        cls.weather_data = engine.data()
        cls.civil_hours = engine._get_civil_hours(cls.weather_data)

    def test_1_zero_pct_frozen_compatibility(self):
        """Zero additional demand matches the ensemble oracle; legacy oracle matches frozen fixtures."""
        # Helper baseline equals requested at 0%
        s_defaults = engine.defaults()
        res_0 = engine.ac_heating_budget(s_defaults, additional_demand_pct=0.0)
        np.testing.assert_array_equal(res_0['baseline_useful_heat_kwh'], res_0['requested_useful_heat_kwh'])
        np.testing.assert_array_equal(res_0['baseline_heating_electricity_kwh'], res_0['requested_heating_electricity_kwh'])

        # Factory defaults scenario from metadata settings
        factory_settings = self.metadata['scenarios']['factory_defaults']['settings']
        s_fact = engine.validate(factory_settings)
        self.assertEqual(s_fact.get('ac_heating_additional_demand_pct'), 0.0)

        # Allocated heating matches source_heating_factory_defaults
        d = self.weather_data
        alloc = engine._allocate_space_heating(
            s_fact, d['temperature'], d['month'], d['hour'], d['valid'], self.civil_hours, d['years']
        )
        legacy = heating_oracle(s_fact, d['temperature'], d['month'], d['hour'], d['valid'],
                                self.civil_hours, engine.GAS_MONTHLY, legacy=True)
        expected = heating_oracle(s_fact, d['temperature'], d['month'], d['hour'], d['valid'],
                                  self.civil_hours, engine.GAS_MONTHLY)
        np.testing.assert_allclose(alloc, expected, atol=1e-12, rtol=0)
        np.testing.assert_allclose(
            legacy,
            self.npz['source_heating_factory_defaults'],
            atol=1e-12,
            rtol=0,
            err_msg="0pct allocated heating drift against frozen source fixture exceeded strict tolerance",
        )

        # Forecast mapped heating matches forecast_heating_factory_defaults
        fc = engine._forecast_mapped_series(s_fact)
        legacy_fc = heating_oracle(s_fact, fc['mapped_temp'], fc['target_months'], fc['target_std_hours'],
                                   np.ones_like(fc['mapped_temp'], bool), fc['target_civil_hours'],
                                   engine.GAS_MONTHLY, legacy=True)
        expected_fc = heating_oracle(s_fact, fc['mapped_temp'], fc['target_months'], fc['target_std_hours'],
                                     np.ones_like(fc['mapped_temp'], bool), fc['target_civil_hours'], engine.GAS_MONTHLY)
        np.testing.assert_allclose(fc['mapped_heating'], expected_fc, atol=1e-12, rtol=0)
        np.testing.assert_allclose(
            legacy_fc,
            self.npz['forecast_heating_factory_defaults'],
            atol=1e-12,
            rtol=0,
            err_msg="0pct forecast mapped heating drift against frozen fixture exceeded strict tolerance",
        )

    def test_2_fixed_scop_windows_25pct_interval_and_sums(self):
        """At 25% demand: every heating interval, monthly sum, and annual sum scales by 1.25."""
        d = self.weather_data
        s0 = engine.validate({'load_scenario': 'electrified', 'ac_heating_additional_demand_pct': 0})
        s25 = engine.validate({'load_scenario': 'electrified', 'ac_heating_additional_demand_pct': 25})

        alloc_0 = engine._allocate_space_heating(
            s0, d['temperature'], d['month'], d['hour'], d['valid'], self.civil_hours, d['years']
        )
        alloc_25 = engine._allocate_space_heating(
            s25, d['temperature'], d['month'], d['hour'], d['valid'], self.civil_hours, d['years']
        )

        # 1. Every non-zero heating interval scales by exactly 1.25
        active = alloc_0 > 0
        self.assertTrue(np.any(active), "Expected active heating intervals in historical data")
        np.testing.assert_allclose(
            alloc_25[active],
            alloc_0[active] * 1.25,
            atol=1e-12,
            rtol=0,
            err_msg="Non-zero heating intervals did not scale by exactly 1.25",
        )

        # 2. Strict zero outside permitted heating hours / windows
        inactive = ~active
        self.assertTrue(np.all(alloc_25[inactive] == 0.0), "Found non-zero heating outside active windows")

        # 3. Monthly sum across all 19 weather years and 12 months scales by 1.25
        n_years = len(d['years'])
        for yi in range(n_years):
            for mm in range(1, 13):
                m_mask = d['valid'][yi] & (d['month'][yi] == mm)
                sum_0 = alloc_0[yi, m_mask].sum()
                sum_25 = alloc_25[yi, m_mask].sum()
                np.testing.assert_allclose(
                    sum_25,
                    sum_0 * 1.25,
                    atol=1e-12,
                    rtol=0,
                    err_msg=f"Monthly sum mismatch in year {yi}, month {mm}",
                )

        # 4. Annual sum across all 19 weather years scales by 1.25
        for yi in range(n_years):
            annual_0 = alloc_0[yi].sum()
            annual_25 = alloc_25[yi].sum()
            np.testing.assert_allclose(
                annual_25,
                annual_0 * 1.25,
                atol=1e-12,
                rtol=0,
                err_msg=f"Annual sum mismatch in year {yi}",
            )

    def test_3_total_load_difference_equals_quarter_baseline_and_enduse_invariance(self):
        """Total load difference is exactly .25*baseline heating; other enduses invariant."""
        d = self.weather_data
        s0 = engine.validate({'load_scenario': 'electrified', 'ac_heating_additional_demand_pct': 0})
        s25 = engine.validate({'load_scenario': 'electrified', 'ac_heating_additional_demand_pct': 25})

        load_0, ev_0, enduses_0 = engine.load_profiles(s0)
        load_25, ev_25, enduses_25 = engine.load_profiles(s25)

        alloc_0 = engine._allocate_space_heating(
            s0, d['temperature'], d['month'], d['hour'], d['valid'], self.civil_hours, d['years']
        )

        # Total load difference equals .25 * baseline heating
        load_diff = load_25 - load_0
        np.testing.assert_allclose(
            load_diff,
            0.25 * alloc_0,
            atol=1e-12,
            rtol=0,
            err_msg="Load profile difference does not match 0.25 * baseline heating array",
        )

        # Test EV scenario
        s_ev0 = engine.validate({'load_scenario': 'ev', 'ac_heating_additional_demand_pct': 0})
        s_ev25 = engine.validate({'load_scenario': 'ev', 'ac_heating_additional_demand_pct': 25})
        load_ev0, ev_arr0, _ = engine.load_profiles(s_ev0)
        load_ev25, ev_arr25, _ = engine.load_profiles(s_ev25)

        # EV array unchanged
        np.testing.assert_array_equal(ev_arr25, ev_arr0)
        self.assertGreater(float(np.sum(ev_arr0)), 0, "EV profile should be active in EV scenario")
        # Load difference in EV scenario also equals 0.25 * baseline heating
        np.testing.assert_allclose(
            load_ev25 - load_ev0,
            0.25 * alloc_0,
            atol=1e-12,
            rtol=0,
            err_msg="EV scenario load profile difference does not match 0.25 * baseline heating array",
        )

        # Invariance of non-space-heating enduses
        self.assertEqual(enduses_25['dhw_smc'], enduses_0['dhw_smc'])
        self.assertEqual(enduses_25['cooking_smc'], enduses_0['cooking_smc'])
        self.assertEqual(enduses_25['heating_smc'], enduses_0['heating_smc'])  # historical gas displacement unchanged
        self.assertEqual(enduses_25['dhw_kwh'], enduses_0['dhw_kwh'])
        self.assertEqual(enduses_25['cooking_kwh'], enduses_0['cooking_kwh'])
        self.assertEqual(enduses_25['summer_ac_kwh'], enduses_0['summer_ac_kwh'])
        self.assertEqual(enduses_25['summer_ac_baseline_kwh'], enduses_0['summer_ac_baseline_kwh'])
        self.assertEqual(enduses_25['summer_ac_extra_kwh'], enduses_0['summer_ac_extra_kwh'])

        # Historical baseline load unchanged
        s_base0 = engine.validate({'load_scenario': 'baseline', 'ac_heating_additional_demand_pct': 0})
        s_base25 = engine.validate({'load_scenario': 'baseline', 'ac_heating_additional_demand_pct': 25})
        load_base0, _, _ = engine.load_profiles(s_base0)
        load_base25, _, _ = engine.load_profiles(s_base25)
        np.testing.assert_array_equal(load_base25, load_base0)

    def test_4_helper_baseline_requested_annual_summary_matches_raw_allocation(self):
        """Enduse summary exposes explicit annual scalars matching raw allocation per year."""
        d = self.weather_data
        s25 = engine.validate({'load_scenario': 'electrified', 'ac_heating_additional_demand_pct': 25})
        _, _, enduses = engine.load_profiles(s25)
        budget = engine.ac_heating_budget(s25, additional_demand_pct=25.0)

        # Baseline useful heat and requested useful heat
        expected_base_useful = float(budget['baseline_useful_heat_kwh'].sum())
        expected_req_useful = float(budget['requested_useful_heat_kwh'].sum())
        self.assertAlmostEqual(enduses['ac_heating_baseline_useful_kwh'], expected_base_useful, places=10)
        self.assertAlmostEqual(enduses['ac_heating_requested_useful_kwh'], expected_req_useful, places=10)
        self.assertAlmostEqual(enduses['ac_heating_requested_useful_kwh'], expected_base_useful * 1.25, places=10)

        # Baseline and requested AC electricity
        expected_base_elec = float(budget['baseline_heating_electricity_kwh'].sum())
        expected_req_elec = float(budget['requested_heating_electricity_kwh'].sum())
        self.assertAlmostEqual(enduses['ac_heating_baseline_electricity_kwh'], expected_base_elec, places=10)
        self.assertAlmostEqual(enduses['ac_heating_requested_electricity_kwh'], expected_req_elec, places=10)
        self.assertAlmostEqual(enduses['ac_heating_requested_electricity_kwh'], expected_base_elec * 1.25, places=10)

        # Extra AC electricity
        expected_extra_elec = expected_req_elec - expected_base_elec
        self.assertAlmostEqual(enduses['ac_heating_extra_electricity_kwh'], expected_extra_elec, places=10)

        # Metadata pct and SCOP
        self.assertEqual(enduses['ac_heating_additional_demand_pct'], 25.0)
        self.assertEqual(enduses['ac_heating_scop'], float(s25['scop']))

        # Preserved heating_kwh = requested AC electricity, heating_smc = historical displacement
        self.assertAlmostEqual(enduses['heating_kwh'], expected_req_elec, places=10)
        self.assertAlmostEqual(enduses['heating_smc'], float(budget['heating_gas_smc'].sum()), places=10)

        # Match raw allocated array sums per year
        alloc_25 = engine._allocate_space_heating(
            s25, d['temperature'], d['month'], d['hour'], d['valid'], self.civil_hours, d['years']
        )
        n_years = len(d['years'])
        expected_profile = heating_oracle(s25, d['temperature'], d['month'], d['hour'], d['valid'],
                                          self.civil_hours, engine.GAS_MONTHLY)
        np.testing.assert_allclose(alloc_25, expected_profile, atol=1e-12, rtol=0)
        np.testing.assert_allclose(alloc_25.sum(axis=1).mean(), enduses['heating_kwh'], atol=1e-12, rtol=0)
        for yi in range(n_years):
            year_alloc_sum = alloc_25[yi].sum()
            np.testing.assert_allclose(
                year_alloc_sum,
                expected_profile[yi].sum(),
                atol=1e-12,
                rtol=0,
                err_msg=f"Raw allocated sum in year {yi} does not match enduse requested electricity",
            )

    def test_5_cache_invalidation_0_to_25_to_0(self):
        """Changing 0->25->0 gives distinct/restored results without cache_clear; schedule & SCOP unmodified."""
        s0 = engine.validate({'load_scenario': 'electrified', 'ac_heating_additional_demand_pct': 0})
        s25 = engine.validate({'load_scenario': 'electrified', 'ac_heating_additional_demand_pct': 25})

        # Do NOT call cache_clear between invocations
        load_a, _, _ = engine.load_profiles(s0)
        load_b, _, _ = engine.load_profiles(s25)
        load_c, _, _ = engine.load_profiles(s0)

        # Call 2 is distinct from Call 1
        self.assertFalse(np.array_equal(load_a, load_b), "Cached load profile should differ when pct changes")

        # Call 3 restores Call 1 bit-for-bit
        np.testing.assert_array_equal(load_c, load_a, err_msg="Cache restoration failed for pct=0")

        # Schedule and SCOP unmodified
        self.assertEqual(s0['ac_heating_windows'], s25['ac_heating_windows'])
        self.assertEqual(s0['heating_schedule_mode'], s25['heating_schedule_mode'])
        self.assertEqual(s0['scop'], s25['scop'])

    def test_6_forecast_2027_2028_requested_budgets_and_nonheating_component(self):
        """Forecast 2027 & 2028 preserve requested budgets and nonheating mapped components."""
        # 2027: installation_year=2026, 2028: installation_year=2027
        for inst_year in [2026, 2027]:
            s0 = engine.validate({'installation_year': inst_year, 'load_scenario': 'electrified', 'ac_heating_additional_demand_pct': 0})
            s25 = engine.validate({'installation_year': inst_year, 'load_scenario': 'electrified', 'ac_heating_additional_demand_pct': 25})

            fc0 = engine._forecast_mapped_series(s0)
            fc25 = engine._forecast_mapped_series(s25)

            # Nonheating mapped component must be identical
            nonheating_0 = fc0['mapped_load'] - fc0['mapped_heating']
            nonheating_25 = fc25['mapped_load'] - fc25['mapped_heating']
            np.testing.assert_allclose(
                nonheating_25,
                nonheating_0,
                atol=1e-12,
                rtol=0,
                err_msg=f"Nonheating mapped load component changed with pct for installation_year {inst_year}",
            )

            # Requested budgets preserved in raw interval sums across each weather analogue
            budget_0 = engine.ac_heating_budget(s0, additional_demand_pct=0.0)
            budget_25 = engine.ac_heating_budget(s25, additional_demand_pct=25.0)
            expected_0 = float(budget_0['requested_heating_electricity_kwh'].sum())
            expected_25 = float(budget_25['requested_heating_electricity_kwh'].sum())

            expected_profile = heating_oracle(s0, fc0['mapped_temp'], fc0['target_months'], fc0['target_std_hours'],
                                              np.ones_like(fc0['mapped_temp'], bool), fc0['target_civil_hours'],
                                              engine.GAS_MONTHLY)
            np.testing.assert_allclose(fc0['mapped_heating'], expected_profile, atol=1e-12, rtol=0)
            np.testing.assert_allclose(fc0['mapped_heating'].sum(axis=1).mean(), expected_0, atol=1e-12, rtol=0)
            np.testing.assert_allclose(fc25['mapped_heating'].sum(axis=1).mean(), expected_25, atol=1e-12, rtol=0)
            n_analogues = fc0['mapped_heating'].shape[0]
            for wi in range(n_analogues):
                raw_sum_0 = float(fc0['mapped_heating'][wi].sum())
                raw_sum_25 = float(fc25['mapped_heating'][wi].sum())
                np.testing.assert_allclose(
                    raw_sum_0,
                    expected_profile[wi].sum(),
                    atol=1e-12,
                    rtol=0,
                    err_msg=f"Forecast mapped heating sum mismatch for analogue {wi}, year {inst_year}",
                )
                np.testing.assert_allclose(
                    raw_sum_25,
                    (expected_profile[wi] * 1.25).sum(),
                    atol=1e-12,
                    rtol=0,
                    err_msg=f"Forecast mapped heating 25% sum mismatch for analogue {wi}, year {inst_year}",
                )
                np.testing.assert_allclose(
                    raw_sum_25,
                    raw_sum_0 * 1.25,
                    atol=1e-12,
                    rtol=0,
                    err_msg=f"Forecast mapped heating did not scale by 1.25 for analogue {wi}, year {inst_year}",
                )

    def test_7_baseline_and_hp_physical_results_unaffected_by_pct(self):
        """Baseline electricity and air-to-water HP simulation unaffected by pct."""
        # Baseline scenario
        s_base0 = engine.validate({'load_scenario': 'baseline', 'ac_heating_additional_demand_pct': 0})
        s_base50 = engine.validate({'load_scenario': 'baseline', 'ac_heating_additional_demand_pct': 50})
        load_b0, _, end_b0 = engine.load_profiles(s_base0)
        load_b50, _, end_b50 = engine.load_profiles(s_base50)
        np.testing.assert_array_equal(load_b50, load_b0)
        self.assertEqual(end_b0['heating_kwh'], 0.0)
        self.assertEqual(end_b50['heating_kwh'], 0.0)

        # Air-to-water heat pump scenario (limited representative settings)
        s_hp0 = engine.validate({'planning_heat_pump': True, 'load_scenario': 'electrified', 'ac_heating_additional_demand_pct': 0})
        s_hp50 = engine.validate({'planning_heat_pump': True, 'load_scenario': 'electrified', 'ac_heating_additional_demand_pct': 50})
        load_hp0, _, end_hp0 = engine.load_profiles(s_hp0)
        load_hp50, _, end_hp50 = engine.load_profiles(s_hp50)
        np.testing.assert_allclose(load_hp50, load_hp0, atol=1e-12, rtol=0)
        self.assertEqual(end_hp50['heat_pump_total_kwh'], end_hp0['heat_pump_total_kwh'])
        self.assertEqual(end_hp50['heating_kwh'], end_hp0['heating_kwh'])

    def test_8_invalid_pct_rejection_and_temp_persistence_old_complete_export(self):
        """Invalid pct rejected, bounds [0, None] supported, old complete export migrated at 0."""
        # Reject invalid types and values
        for invalid in [-1, -0.01, True, False, "25", float('nan'), float('inf')]:
            with self.assertRaises(ValueError, msg=f"Failed to reject invalid pct: {invalid}"):
                engine.validate({'ac_heating_additional_demand_pct': invalid})

        # Accept valid bounds [0, None]
        for valid_pct in [0, 0.0, 25, 25.5, 100, 1000.0]:
            validated = engine.validate({'ac_heating_additional_demand_pct': valid_pct})
            self.assertEqual(validated['ac_heating_additional_demand_pct'], float(valid_pct))

        # Temp persistence: full replacement export missing ac_heating_additional_demand_pct
        with tempfile.TemporaryDirectory() as td:
            temp_state_file = Path(td) / 'test_scenario.json'
            all_defs = dict(engine.defaults())
            self.assertIn('ac_heating_additional_demand_pct', all_defs)
            del all_defs['ac_heating_additional_demand_pct']

            # Full replacement without new setting succeeds and migrates to 0.0
            res = shared_state.update_state(
                {'settings': all_defs, 'quote': {'line_items': []}, 'replace': True},
                path=temp_state_file,
            )
            self.assertEqual(res['settings']['ac_heating_additional_demand_pct'], 0.0)

            # But missing an unrelated required field (e.g. scop) still fails
            broken_export = dict(all_defs)
            del broken_export['scop']
            with self.assertRaises(ValueError, msg="Expected ValueError when unrelated required field is missing"):
                shared_state.update_state(
                    {'settings': broken_export, 'quote': {'line_items': []}, 'replace': True},
                    path=temp_state_file,
                )


if __name__ == '__main__':
    unittest.main()
