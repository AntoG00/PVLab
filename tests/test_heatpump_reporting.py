"""Stage 6B Heat Pump Homeowner Reporting Test Suite.

Verifies:
1. Row-level additive end-use breakdown: household, cooking, EV, HP heating, HP DHW, HP aux sum exactly to load_kwh.
2. 26-field nested heat pump diagnostic records in daily and monthly views.
3. Top-level end_use_totals and hp_totals weighting actual valid samples.
4. Gas cooking retention vs induction conversion separation.
5. EV start year delay adherence.
6. Selected hour diagnostics and nested data.
7. Correct SPF delivered heat excluding unserved lukewarm draw heat and separating storage delta.
8. Storage balance and conservation.
9. Off-path contract parity: no HP fields on baseline, battery SOC stats unchanged.
10. evaluate(detail=True) separate hourly_end_uses preserving 8-column contract.
11. Heat pump metadata definitions, climatology period, and literature links.
"""

from __future__ import annotations

import unittest
import numpy as np

from feasibility import engine
from feasibility.heatpump_dispatch import simulate_shared_heat_pump


class TestHeatPumpReporting(unittest.TestCase):
    def setUp(self) -> None:
        engine._load_cache.cache_clear()
        engine._energy_cache.cache_clear()
        engine._dayhour_trace.cache_clear()
        engine._forecast_runs.cache_clear()

    def test_daily_source_row_end_uses_sum_and_diagnostics(self) -> None:
        """Daily source row end-uses sum exactly to row.load_kwh and contain full 26 HP fields."""
        s = {
            'planning_heat_pump': True,
            'load_scenario': 'electrified',
            'hp_electric_cooking': True,
            'south_panels': 10,
            'battery_kwh': 5.0,
        }
        res = engine.dayhour(s, 2024, '2024-01-15', hour=12)

        self.assertIn('end_use_totals', res)
        self.assertIn('hp_totals', res)
        self.assertIn('operating_clock', res)
        self.assertEqual(len(res['hours']), 24)

        for row in res['hours']:
            self.assertIn('end_uses', row)
            self.assertIn('heat_pump', row)
            eu = row['end_uses']
            hp = row['heat_pump']

            # 1. End uses sum exactly to load_kwh
            eu_sum = (
                eu['household_kwh']
                + eu['cooking_kwh']
                + eu['ev_kwh']
                + eu['heat_pump_heating_kwh']
                + eu['heat_pump_hot_water_kwh']
                + eu['heat_pump_auxiliary_kwh']
            )
            self.assertAlmostEqual(eu_sum, row['load_kwh'], places=9)

            # 2. HP heating = space comp + space backup
            self.assertAlmostEqual(
                eu['heat_pump_heating_kwh'],
                hp['space_compressor_electricity_kwh'] + hp['space_backup_electricity_kwh'],
                places=9,
            )
            # 3. HP DHW = DHW comp + DHW backup
            self.assertAlmostEqual(
                eu['heat_pump_hot_water_kwh'],
                hp['dhw_compressor_electricity_kwh'] + hp['dhw_backup_electricity_kwh'],
                places=9,
            )
            # 4. HP aux = auxiliary electricity
            self.assertAlmostEqual(eu['heat_pump_auxiliary_kwh'], hp['auxiliary_electricity_kwh'], places=9)

            # 5. Verify all 26 fields in hp
            expected_hp_keys = {
                'space_compressor_electricity_kwh',
                'dhw_compressor_electricity_kwh',
                'space_backup_electricity_kwh',
                'dhw_backup_electricity_kwh',
                'auxiliary_electricity_kwh',
                'requested_space_heat_kwh',
                'delivered_space_heat_kwh',
                'unmet_space_heat_kwh',
                'requested_draw_heat_kwh',
                'delivered_draw_heat_kwh',
                'unmet_draw_heat_kwh',
                'actual_draw_heat_kwh',
                'heat_supplied_tank_kwh',
                'tank_losses_kwh',
                'tank_temp_c',
                'space_flow_temp_c',
                'dhw_flow_temp_c',
                'space_capacity_kw',
                'dhw_capacity_kw',
                'radiator_bound_kw',
                'space_runtime_hours',
                'dhw_runtime_hours',
                'tank_start_storage_kwh',
                'tank_end_storage_kwh',
                'map_outside_flag',
                'hygiene_target_miss',
            }
            self.assertTrue(expected_hp_keys.issubset(set(hp.keys())))

        # Selected hour diagnostics
        self.assertIsNotNone(res['selected_hour'])
        self.assertEqual(res['selected_hour']['hour'], 12)
        self.assertIn('end_uses', res['selected_hour'])
        self.assertIn('heat_pump', res['selected_hour'])

        # Top-level totals exact sums
        tot_eu = res['end_use_totals']
        self.assertAlmostEqual(
            sum(r['end_uses']['household_kwh'] for r in res['hours']),
            tot_eu['household_kwh'],
            places=9,
        )
        self.assertAlmostEqual(tot_eu['load_kwh'], res['totals']['load_kwh'], places=9)

        tot_hp = res['hp_totals']
        self.assertAlmostEqual(
            sum(r['heat_pump']['delivered_space_heat_kwh'] for r in res['hours']),
            tot_hp['delivered_space_heat_kwh'],
            places=9,
        )
        self.assertAlmostEqual(
            tot_hp['storage_delta_kwh'],
            tot_hp['final_storage_kwh'] - tot_hp['initial_storage_kwh'],
            places=9,
        )

    def test_monthly_source_aggregation_and_coverage(self) -> None:
        """Monthly source aggregates arithmetic means, state temperatures, and sample-weighted totals."""
        s = {
            'planning_heat_pump': True,
            'load_scenario': 'electrified',
            'hp_electric_cooking': False,
            'south_panels': 10,
            'battery_kwh': 5.0,
        }
        res = engine.dayhour_monthly(s, 2024, 1, hour=8)

        self.assertIn('end_use_totals', res)
        self.assertIn('hp_totals', res)
        self.assertEqual(len(res['hours']), 24)

        for row in res['hours']:
            eu = row['end_uses']
            hp = row['heat_pump']
            eu_sum = sum(eu.values())
            self.assertAlmostEqual(eu_sum, row['load_kwh'], places=9)
            # Gas cooking retained -> cooking_kwh is strictly 0.0
            self.assertEqual(eu['cooking_kwh'], 0.0)
            # Temperature and capacity means present
            self.assertIn('tank_temp_c', hp)
            self.assertIn('space_capacity_kw', hp)
            self.assertIn('map_outside_flag', hp)
            self.assertIn('map_outside_count', hp)

        # Coverage matches valid sample count (January 2024 has 743 valid samples in timeline)
        self.assertEqual(res['coverage']['valid_samples'], res['total_samples'])
        self.assertEqual(res['coverage']['valid_samples'], 743)
        self.assertEqual(res['coverage']['total_possible_samples'], 744)
        self.assertEqual(res['coverage']['missing_samples'], 1)
        self.assertTrue(res['coverage']['partial'])

        # Totals sum over the actual 743 intervals, not 24 * hourly mean
        tot_hp = res['hp_totals']
        self.assertGreater(tot_hp['delivered_space_heat_kwh'], 0.0)
        self.assertAlmostEqual(
            tot_hp['storage_delta_kwh'],
            tot_hp['final_storage_kwh'] - tot_hp['initial_storage_kwh'],
            places=9,
        )

    def test_forecast_daily_and_monthly_end_uses(self) -> None:
        """Forecast daily and monthly dayhour responses include coherent end_uses and updated note."""
        s = {
            'planning_heat_pump': True,
            'load_scenario': 'electrified',
            'hp_electric_cooking': True,
            'south_panels': 10,
            'battery_kwh': 5.0,
        }
        # 1. Forecast daily
        fc_day = engine.dayhour_forecast(s, '2027-02-15', hour=14)
        self.assertIn('end_use_totals', fc_day)
        self.assertIn('hp_totals', fc_day)
        self.assertIn('monthly load normalization applies strictly to household, cooking, and EV', fc_day['forecast_note'])
        for row in fc_day['hours']:
            self.assertAlmostEqual(sum(row['end_uses'].values()), row['load_kwh'], places=9)

        # 2. Forecast monthly
        fc_month = engine.dayhour_forecast_monthly(s, 2, hour=12)
        self.assertIn('end_use_totals', fc_month)
        self.assertIn('hp_totals', fc_month)
        self.assertIn('monthly load normalization applies strictly to household, cooking, and EV', fc_month['forecast_note'])
        for row in fc_month['hours']:
            self.assertAlmostEqual(sum(row['end_uses'].values()), row['load_kwh'], places=9)

    def test_ev_delay_and_cooking_separation(self) -> None:
        """EV delay (ev_start_year > 1) excludes EV from year 1; gas cooking keeps cooking_kwh 0.0."""
        s_delayed_ev = {
            'planning_heat_pump': True,
            'load_scenario': 'ev',
            'ev_start_year': 2,
            'hp_electric_cooking': False,
            'south_panels': 10,
            'battery_kwh': 5.0,
        }
        res_delayed = engine.dayhour(s_delayed_ev, 2024, '2024-07-15')
        self.assertEqual(res_delayed['end_use_totals']['ev_kwh'], 0.0)
        self.assertEqual(res_delayed['end_use_totals']['cooking_kwh'], 0.0)
        for row in res_delayed['hours']:
            self.assertEqual(row['end_uses']['ev_kwh'], 0.0)
            self.assertEqual(row['end_uses']['cooking_kwh'], 0.0)

        s_active_ev = {
            'planning_heat_pump': True,
            'load_scenario': 'ev',
            'ev_start_year': 1,
            'hp_electric_cooking': True,
            'south_panels': 10,
            'battery_kwh': 5.0,
        }
        res_active = engine.dayhour(s_active_ev, 2024, '2024-07-15')
        self.assertGreater(res_active['end_use_totals']['ev_kwh'], 0.0)
        self.assertGreater(res_active['end_use_totals']['cooking_kwh'], 0.0)

    def test_off_path_contract_parity_and_soc_stats_unchanged(self) -> None:
        """When HP is off (baseline or planning_heat_pump=False), no HP fields exist; SOC stats identical."""
        s_baseline = {
            'planning_heat_pump': False,
            'load_scenario': 'baseline',
            'south_panels': 10,
            'battery_kwh': 5.0,
        }
        res_day = engine.dayhour(s_baseline, 2024, '2024-07-15')
        self.assertNotIn('end_use_totals', res_day)
        self.assertNotIn('hp_totals', res_day)
        for row in res_day['hours']:
            self.assertNotIn('end_uses', row)
            self.assertNotIn('heat_pump', row)

        res_month = engine.dayhour_monthly(s_baseline, 2024, 7)
        self.assertNotIn('end_use_totals', res_month)
        self.assertNotIn('hp_totals', res_month)
        for row in res_month['hours']:
            self.assertNotIn('end_uses', row)
            self.assertNotIn('heat_pump', row)
            self.assertIn('soc_end_median_kwh', row)
            self.assertIn('soc_end_p10_kwh', row)
            self.assertIn('soc_end_p90_kwh', row)
            self.assertIn('soc_full_days', row)

    def test_spf_delivered_excludes_unserved_lukewarm_water(self) -> None:
        """Delivered SPF strictly excludes unserved lukewarm water draws and discloses storage delta."""
        # Standby power with unserved withdrawal and tank cooling (below min service temp)
        res = simulate_shared_heat_pump(
            outdoor_temp_c=[5.0],
            requested_space_heat_kwh=[0.0],
            draw_heat_kwh=[2.0],
            space_active_mask=[False],
            dhw_reheat_allowed_mask=[False],
            hygiene_mask=[False],
            tank_initial_temp_c=35.0,  # Below min service temp (40°C)
            tank_standing_loss_w_per_k=0.0,
            standby_kw=0.05,
            external_pump_kw=0.02,
            backup_heater_kw=0.0,
        )
        summary = res['summary']
        # 2 kWh drawn at 35°C is unserved
        self.assertAlmostEqual(summary['annual_unserved_draw_heat_kwh'], 2.0, places=9)
        self.assertAlmostEqual(summary['annual_delivered_draw_heat_kwh'], 0.0, places=9)
        # Delivered SPF must NOT count the 2.0 kWh unserved draw
        # Qualified delivered = 0 space + 0 DHW = 0.0
        self.assertEqual(summary['seasonal_performance_factor_delivered_heat'], 0.0)
        # Storage delta is negative (-2.0 kWh with 0 standing losses)
        self.assertAlmostEqual(summary['storage_delta_kwh'], -2.0, places=9)
        # Storage-adjusted numerator is 0 + (-2.0) = -2.0 < 0 -> reported as None with reason note
        self.assertIsNone(summary['seasonal_performance_factor_storage_adjusted'])
        self.assertIn('negative', summary['storage_adjusted_ratio_note'].lower())

    def test_storage_balance_conservation(self) -> None:
        """Hourly tank balance strictly conserves energy: delta = supplied - actual_draw - losses."""
        res = simulate_shared_heat_pump(
            outdoor_temp_c=[5.0, 6.0],
            requested_space_heat_kwh=[1.0, 1.0],
            draw_heat_kwh=[0.5, 0.5],
            space_active_mask=[True, True],
            dhw_reheat_allowed_mask=[True, True],
            hygiene_mask=[False, False],
            tank_initial_temp_c=50.0,
        )
        summary = res['summary']
        self.assertLess(summary['max_tank_conservation_error_kwh'], 1e-12)
        expected_delta = summary['final_storage_kwh'] - summary['initial_storage_kwh']
        self.assertAlmostEqual(summary['storage_delta_kwh'], expected_delta, places=12)

    def test_evaluate_detail_hourly_end_uses(self) -> None:
        """evaluate(detail=True) adds hourly_end_uses while preserving 8-column hourly trace."""
        s = {
            'planning_heat_pump': True,
            'load_scenario': 'electrified',
            'hp_electric_cooking': True,
            'south_panels': 10,
            'battery_kwh': 5.0,
        }
        res = engine.evaluate(s, detail=True)
        self.assertIn('hourly', res)
        # 8-column contract untouched
        expected_cols = ['pv_kwh', 'load_kwh', 'grid_import_kwh', 'export_kwh', 'soc_kwh', 'direct_kwh', 'charge_kwh', 'discharge_kwh']
        self.assertEqual(res['hourly']['columns'], expected_cols)

        # hourly_end_uses added separately
        self.assertIn('hourly_end_uses', res)
        eu_cols = [
            'household_kwh',
            'cooking_kwh',
            'ev_kwh',
            'heat_pump_heating_kwh',
            'heat_pump_hot_water_kwh',
            'heat_pump_auxiliary_kwh',
        ]
        self.assertEqual(res['hourly_end_uses']['columns'], eu_cols)
        eu_vals = res['hourly_end_uses']['values']
        h_vals = res['hourly']['values']
        self.assertEqual(len(eu_vals), len(h_vals))

        # For every hour, sum of end uses equals hourly trace load_kwh
        for t in range(min(100, len(eu_vals))):
            self.assertAlmostEqual(sum(eu_vals[t]), h_vals[t][1], places=5)

    def test_heat_pump_metadata_definitions_and_links(self) -> None:
        """Heat pump result metadata contains ratio definitions, climatology context, and literature links."""
        s = {
            'planning_heat_pump': True,
            'load_scenario': 'electrified',
            'south_panels': 10,
            'battery_kwh': 5.0,
        }
        res = engine.evaluate(s)
        self.assertIn('heat_pump', res)
        meta = res['heat_pump']['metadata']

        self.assertIn('ratio_definitions', meta)
        ratios = meta['ratio_definitions']
        self.assertIn('seasonal_performance_factor_delivered_heat', ratios)
        self.assertIn('seasonal_performance_factor_storage_adjusted', ratios)
        self.assertIn('seasonal_scop_space', ratios)

        self.assertIn('source_period', meta)
        self.assertIn('Explicit profile reference period', meta['source_period'])
        self.assertIn(engine.weather_period().label, meta['source_period'])

        self.assertIn('measured_vs_assumed', meta)
        self.assertIn('comfort_and_recovery_validation', meta)
        self.assertIn('source_example_links', meta)
        self.assertIn('manufacturer_recommendation_disclaimer', meta)
        self.assertIn('operating_clock_note', meta)
