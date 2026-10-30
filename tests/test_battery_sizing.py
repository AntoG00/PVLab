"""Numerical and integration tests for battery sizing engine and API route."""
import io
import json
import math
import unittest
from unittest.mock import patch

import numpy as np

import feasibility.engine as e
import feasibility.server as server_module
from test_support import IsolatedDefaultsTestCase


class BatterySizingNumericalTests(IsolatedDefaultsTestCase):
    def setUp(self):
        super().setUp()
        self.base_settings = {
            'south_panels': 12,
            'west_panels': 0,
            'east_panels': 0,
            'battery_kwh': 5.0,
            'roundtrip_efficiency': 0.9,
            'inverter_kw': 6.0,
            'charge_kw': 5.0,
            'discharge_kw': 5.0,
            'horizon_years': 20,
            'battery_replace_year': 12,
            'deduction_rate': 0.5,
            'principal_residence_confirmed': True, 'deduction_tax_capacity': 10000.,
            'load_scenario': 'electrified'
        }

    def test_zero_capacity_behavior(self):
        """At C=0, battery discharge is 0, savings are 0, and import equals no-battery baseline."""
        res = e.battery_sizing(self.base_settings, {'step': 2, 'max_capacity': 10})
        c0 = res['chart1_capacity_dispatch']['capacities'][0]
        self.assertEqual(c0['capacity_kwh'], 0.0)
        self.assertEqual(c0['battery_discharge_kwh']['min'], 0.0)
        self.assertEqual(c0['battery_discharge_kwh']['median'], 0.0)
        self.assertEqual(c0['battery_discharge_kwh']['max'], 0.0)
        self.assertEqual(c0['annual_savings_eur']['min'], 0.0)
        self.assertEqual(c0['annual_savings_eur']['median'], 0.0)
        self.assertEqual(c0['annual_savings_eur']['max'], 0.0)
        self.assertEqual(c0['efc_physical']['median'], 0.0)
        self.assertEqual(c0['efc_delivered']['median'], 0.0)

        # Non-zero capacity has positive discharge and savings
        c_pos = res['chart1_capacity_dispatch']['capacities'][1]
        self.assertGreater(c_pos['capacity_kwh'], 0)
        self.assertGreater(c_pos['battery_discharge_kwh']['median'], 0)
        self.assertGreater(c_pos['annual_savings_eur']['median'], 0)
        self.assertLess(c_pos['grid_import_kwh']['median'], c0['grid_import_kwh']['median'])

    def test_weather_coverage_and_envelopes(self):
        """Covers all active historical PVGIS analogues and preserves min <= median <= max envelopes."""
        res = e.battery_sizing(self.base_settings, {'step': 1, 'max_capacity': 25})
        years = res['chart1_capacity_dispatch']['weather_years']
        wp = e.weather_period()
        self.assertEqual(len(years), wp.count)
        self.assertEqual(min(years), wp.min_year)
        self.assertEqual(max(years), wp.max_year)
        np.testing.assert_array_equal(years, e.data()['years'])

        caps = res['chart1_capacity_dispatch']['capacities']
        self.assertEqual(len(caps), 26)  # 0..25 inclusive
        for row in caps:
            for key in ['grid_import_kwh', 'grid_export_kwh', 'pv_kwh', 'self_consumption', 'autarky', 'annual_savings_eur']:
                env = row[key]
                self.assertLessEqual(env['min'], env['median'] + 1e-9, f"min <= median for {key} at {row['capacity_kwh']}")
                self.assertLessEqual(env['median'], env['max'] + 1e-9, f"median <= max for {key} at {row['capacity_kwh']}")

    def test_one_kwh_marginal_differences_and_unforced_monotonicity(self):
        """1-kWh steps reveal diminishing incremental returns without artificial monotonicity smoothing."""
        res = e.battery_sizing(self.base_settings, {'step': 1, 'max_capacity': 25})
        steps = res['chart2_incremental_savings']['steps']
        self.assertEqual(len(steps), 25)

        # First kWh step (0 -> 1 kWh) provides highest incremental savings
        step0_1 = steps[0]
        self.assertEqual(step0_1['capacity_from'], 0)
        self.assertEqual(step0_1['capacity_to'], 1)
        first_kwh_median = step0_1['incremental_savings_eur_kwh']['median']
        self.assertGreater(first_kwh_median, 30.0, "First kWh yields substantial annual savings (>30 €/yr)")

        # Last kWh step (24 -> 25 kWh) provides diminishing marginal return
        step24_25 = steps[-1]
        self.assertEqual(step24_25['capacity_from'], 24)
        self.assertEqual(step24_25['capacity_to'], 25)
        last_kwh_median = step24_25['incremental_savings_eur_kwh']['median']
        self.assertLess(last_kwh_median, first_kwh_median)
        self.assertGreaterEqual(last_kwh_median, 0.0)

        # Verify that differences are unforced: check delta_capacity_kwh is 1.0 and values are exact diffs
        caps = res['chart1_capacity_dispatch']['capacities']
        for i, s in enumerate(steps):
            expected_diffs = np.array(caps[i + 1]['annual_savings_by_weather']) - np.array(caps[i]['annual_savings_by_weather'])
            actual_diffs = np.array(s['incremental_savings_by_weather'])
            np.testing.assert_allclose(actual_diffs, expected_diffs, rtol=1e-7)

    def test_needed_capacity_cdf_calendar_and_heuristic(self):
        """Chart 5 pools daily needed capacity across all 21 weather years in ensemble."""
        res = e.battery_sizing(self.base_settings, {'step': 2, 'max_capacity': 25})
        cdf = res['chart5_needed_capacity_cdf']
        points = cdf['cdf_points']
        self.assertEqual(len(points), 26)  # 0..25

        # Monotonicity of fraction of days met
        for i in range(len(points) - 1):
            self.assertLessEqual(points[i]['fraction_days_met'], points[i + 1]['fraction_days_met'] + 1e-9)
            self.assertGreaterEqual(points[i]['fraction_days_full_utilization'], points[i + 1]['fraction_days_full_utilization'] - 1e-9)

        # 21-year ensemble metadata and pool dimensions
        wp = e.weather_period()
        self.assertEqual(cdf.get('weather_basis'), 'ensemble_mean')
        self.assertEqual(cdf.get('weather_count'), wp.count)
        self.assertEqual(cdf.get('day_count'), wp.count * 365)

        # Numeric daily_needed_kwh and structured daily_records
        daily_needed = cdf['daily_needed_kwh']
        self.assertEqual(len(daily_needed), wp.count * 365)
        self.assertTrue(all(isinstance(v, (int, float)) for v in daily_needed))

        daily_records = cdf['daily_records']
        self.assertEqual(len(daily_records), wp.count * 365)
        years_in_pool = [r['weather_year'] for r in daily_records]
        days_in_pool = [r['day'] for r in daily_records]
        for y in e.data()['years']:
            self.assertEqual(years_in_pool.count(y), 365)
        self.assertEqual(set(days_in_pool), set(range(365)))
        for r, val in zip(daily_records, daily_needed):
            self.assertAlmostEqual(r['needed_kwh'], float(val), places=2)

        self.assertAlmostEqual(cdf['mean_needed_kwh'], float(np.mean(daily_needed)), places=3)

        # Percentiles order
        pct = cdf['percentiles']
        self.assertLessEqual(pct['min'], pct['p10'])
        self.assertLessEqual(pct['p10'], pct['p25'])
        self.assertLessEqual(pct['p25'], pct['p50'])
        self.assertLessEqual(pct['p50'], pct['p75'])
        self.assertLessEqual(pct['p75'], pct['p90'])
        self.assertLessEqual(pct['p90'], pct['max'])
        self.assertAlmostEqual(pct['p50'], float(np.percentile(daily_needed, 50)), places=2)

        # Heuristic description explicitly documents roundtrip efficiency and nights
        desc = cdf['heuristic_description']
        self.assertIn('sqrt(RTE)', desc)
        self.assertIn('night', desc.lower())

    def test_soc_heatmap_continuous_annual_dispatch(self):
        """Chart 6 generates 8760 continuous dispatch hours with ensemble-mean fractions and matrix."""
        res = e.battery_sizing(self.base_settings, {'step': 2, 'max_capacity': 25})
        soc = res['chart6_soc_heatmap']
        wp = e.weather_period()
        self.assertEqual(soc.get('weather_basis'), 'ensemble_mean')
        self.assertEqual(soc.get('weather_count'), wp.count)
        self.assertIsNone(soc.get('weather_year'))

        matrix = soc['hourly_soc_matrix']
        self.assertEqual(len(matrix), 365)
        for day_row in matrix:
            self.assertEqual(len(day_row), 24)
            for val in day_row:
                self.assertGreaterEqual(val, 0)
                self.assertLessEqual(val, 100)

        self.assertIsInstance(soc['empty_hours'], float)
        self.assertIsInstance(soc['full_hours'], float)
        self.assertIsInstance(soc['mid_hours'], float)
        total_hours = soc['empty_hours'] + soc['full_hours'] + soc['mid_hours']
        self.assertAlmostEqual(total_hours, 8760.0, places=2)
        total_fraction = soc['empty_fraction'] + soc['full_fraction'] + soc['mid_fraction']
        self.assertAlmostEqual(total_fraction, 1.0, places=4)
        self.assertAlmostEqual(soc['empty_hours'] / 8760.0, soc['empty_fraction'], places=4)
        self.assertAlmostEqual(soc['full_hours'] / 8760.0, soc['full_fraction'], places=4)

    def test_soc_heatmap_and_cdf_independent_direct_dispatch_comparison(self):
        """Numerical verification: SOC matrix/fractions and pooled CDF match independent 21-year dispatch."""
        res = e.battery_sizing(self.base_settings, {'step': 2, 'max_capacity': 10})
        soc = res['chart6_soc_heatmap']
        cdf = res['chart5_needed_capacity_cdf']
        matrix = np.array(soc['hourly_soc_matrix'])
        self.assertEqual(matrix.shape, (365, 24))

        # 1. Independent per-weather direct dispatch simulation with normalized settings
        s_norm = res['settings']
        years = list(map(int, e.data()['years']))
        mapped_data = e._forecast_mapped_series(s_norm)
        mapped_pv = mapped_data['mapped_pv']
        mapped_load = mapped_data['mapped_load']
        mapped_pzo = mapped_data['mapped_pzo']
        active_cap = float(s_norm['battery_kwh'])
        sqrt_rte = math.sqrt(s_norm['roundtrip_efficiency'])
        s_route = e._candidate_battery_settings(s_norm, active_cap)
        s_route = e._routing_settings(s_route, enforce_clipping=True)
        params_route = e.physical_parameters(s_route)
        valid = np.ones(mapped_data['target_len'], dtype=bool)
        _t_months = mapped_data['target_months']
        _t_hours = mapped_data['target_std_hours']

        raw_matrices = []
        raw_traces = []
        expected_empty_hours = []
        expected_full_hours = []
        expected_daily_needed = []

        for wi in range(len(years)):
            _, trace = e.dispatch_year(
                mapped_pv[wi], mapped_load[wi], active_cap,
                s_norm['charge_kw'], s_norm['discharge_kw'],
                sqrt_rte, s_norm['inverter_kw'], 0.0,
                mapped_pzo[wi], valid,
                s_norm.get('pv_battery_coupling') == 'dc_coupled',
                params_route, _t_months, _t_hours
            )
            raw_traces.append(trace[:, 4])
            soc_series = trace[:, 4]
            expected_empty_hours.append(float((soc_series <= 0.01 * active_cap).sum()))
            expected_full_hours.append(float((soc_series >= 0.99 * active_cap).sum()))
            raw_soc_w = np.clip(soc_series / active_cap * 100.0, 0.0, 100.0)
            raw_matrices.append(raw_soc_w.reshape((365, 24)))

            # Independent daily needed computation for year wi
            clipped_pv = np.minimum(mapped_pv[wi], s_norm['inverter_kw'])
            charge_pv = mapped_pv[wi] if s_norm.get('pv_battery_coupling') == 'dc_coupled' else clipped_pv
            load_w = mapped_load[wi]
            for d in range(365):
                h0 = d * 24; h1 = h0 + 24
                pv_d = clipped_pv[h0:h1]; chg_d = charge_pv[h0:h1]; ld_d = load_w[h0:h1]
                dir_pos = np.minimum(np.minimum(chg_d, ld_d), s_norm['inverter_kw'])
                surp = float(np.minimum(np.maximum(0.0, chg_d - dir_pos), s_norm['charge_kw']).sum())
                day_idx = np.where(pv_d > 0.05)[0]
                sunset = int(day_idx[-1]) if len(day_idx) > 0 else -1
                eve = float(np.maximum(0.0, ld_d[sunset + 1:] - pv_d[sunset + 1:]).sum()) if sunset >= 0 else float(np.maximum(0.0, ld_d - pv_d).sum())
                next_d = (d + 1) % 365
                next_pv = clipped_pv[next_d * 24:(next_d + 1) * 24]
                next_ld = load_w[next_d * 24:(next_d + 1) * 24]
                next_idx = np.where(next_pv > 0.05)[0]
                sunrise = int(next_idx[0]) if len(next_idx) > 0 else 0
                morn = float(np.maximum(0.0, next_ld[:sunrise] - next_pv[:sunrise]).sum()) if len(next_idx) > 0 else float(np.maximum(0.0, next_ld - next_pv).sum())
                expected_daily_needed.append(float(min(surp * sqrt_rte, (eve + morn) / sqrt_rte)))

        # Average raw SOC across years before rounding to require exact rounded mean parity
        mean_raw_soc = np.mean(raw_matrices, axis=0)
        expected_mean_matrix = np.round(mean_raw_soc).astype(int).tolist()
        expected_mean_empty = float(np.mean(expected_empty_hours))
        expected_mean_full = float(np.mean(expected_full_hours))

        np.testing.assert_array_equal(matrix, expected_mean_matrix)
        self.assertAlmostEqual(soc['empty_hours'], expected_mean_empty, places=2)
        self.assertAlmostEqual(soc['full_hours'], expected_mean_full, places=2)
        self.assertAlmostEqual(soc['empty_fraction'], expected_mean_empty / 8760.0, places=4)
        self.assertAlmostEqual(soc['full_fraction'], expected_mean_full / 8760.0, places=4)

        # Monthly summaries verification: mean normalized SOC and mean of per-weather indicators
        monthly = soc.get('monthly_summaries', [])
        self.assertEqual(len(monthly), 12)
        has_divergence = False
        for m in range(1, 13):
            m_summary = next(s for s in monthly if s['month'] == m)
            m_mask = (_t_months == m)
            exp_m_empty = float(np.mean([(tr[:mapped_data['target_len']][m_mask] <= 0.01 * active_cap).mean() for tr in raw_traces]))
            exp_m_full = float(np.mean([(tr[:mapped_data['target_len']][m_mask] >= 0.99 * active_cap).mean() for tr in raw_traces]))
            exp_m_soc = float(np.mean([(tr[:mapped_data['target_len']][m_mask] / active_cap * 100.0).mean() for tr in raw_traces]))
            self.assertAlmostEqual(m_summary['empty_fraction'], exp_m_empty, places=4)
            self.assertAlmostEqual(m_summary['full_fraction'], exp_m_full, places=4)
            self.assertAlmostEqual(m_summary['mid_fraction'], 1.0 - exp_m_empty - exp_m_full, places=4)
            self.assertAlmostEqual(m_summary['mean_soc_pct'], exp_m_soc, places=3)

            # Thresholding the ensemble-mean SOC produces collapsed fractions, proving threshold-of-mean cannot pass
            mean_soc_m = mean_raw_soc.reshape(-1)[m_mask]
            thresh_mean_empty = float((mean_soc_m <= 1.0).mean())
            thresh_mean_full = float((mean_soc_m >= 99.0).mean())
            if abs(thresh_mean_empty - exp_m_empty) > 0.01 or abs(thresh_mean_full - exp_m_full) > 0.01:
                has_divergence = True
                self.assertTrue(
                    abs(m_summary['empty_fraction'] - thresh_mean_empty) > 1e-4 or
                    abs(m_summary['full_fraction'] - thresh_mean_full) > 1e-4,
                    f"Month {m} must diverge from flawed threshold-of-mean SOC"
                )
        self.assertTrue(has_divergence, "At least one month must diverge from threshold-of-mean SOC")

        # Pooled CDF numerical parity and exact CDF fractions
        sorted_needed = np.sort(expected_daily_needed)
        expected_mean_needed = float(np.mean(expected_daily_needed))
        np.testing.assert_allclose(cdf['daily_needed_kwh'], np.round(expected_daily_needed, 2), rtol=1e-5, atol=1e-5)
        self.assertAlmostEqual(cdf['mean_needed_kwh'], expected_mean_needed, places=4)

        for pt in cdf['cdf_points']:
            c = pt['capacity_kwh']
            self.assertAlmostEqual(pt['fraction_days_met'], float((sorted_needed <= c).mean()), places=5)
            self.assertAlmostEqual(pt['fraction_days_full_utilization'], float((sorted_needed >= c).mean()), places=5)

        # 2. Legacy weather_representative options must all yield identical ensemble CDF and SOC
        for rep in ('low', 'median', 'high'):
            res_rep = e.battery_sizing(self.base_settings, {'weather_representative': rep, 'step': 2, 'max_capacity': 10})
            self.assertEqual(res_rep['chart5_needed_capacity_cdf']['weather_basis'], 'ensemble_mean')
            self.assertEqual(res_rep['chart6_soc_heatmap']['weather_basis'], 'ensemble_mean')
            self.assertEqual(res_rep['chart5_needed_capacity_cdf']['weather_count'],len(e.data()['years']))
            self.assertEqual(res_rep['chart6_soc_heatmap']['weather_count'], len(e.data()['years']))
            np.testing.assert_array_equal(res_rep['chart6_soc_heatmap']['hourly_soc_matrix'], soc['hourly_soc_matrix'])
            self.assertEqual(res_rep['chart6_soc_heatmap']['empty_hours'], soc['empty_hours'])
            self.assertEqual(res_rep['chart5_needed_capacity_cdf']['mean_needed_kwh'], cdf['mean_needed_kwh'])

    def test_cost_sensitivity_quote_variants_and_breakeven_formula(self):
        """Marginal battery cost respects delta quote / delta capacity and matches break-even cycles formula."""
        # 1. Fallback to benchmark when quotes missing
        res_no_quote = e.battery_sizing(self.base_settings, {'quote_10kwh_eur': None, 'quote_15kwh_eur': None})
        eco_no_quote = res_no_quote['economics']
        self.assertEqual(eco_no_quote['marginal_cost_source'], 'benchmark')
        self.assertEqual(eco_no_quote['effective_marginal_battery_cost_eur_kwh'], e.validate(self.base_settings)['battery_cost_kwh'])
        self.assertEqual(eco_no_quote['quote_variants_status'], 'missing')
        self.assertIn('quote_10kwh_eur', eco_no_quote['missing_fields'])
        self.assertIn('quote_15kwh_eur', eco_no_quote['missing_fields'])

        # 2. Provided quote variants
        res_quote = e.battery_sizing(self.base_settings, {
            'quote_10kwh_eur': 6000.0,
            'quote_15kwh_eur': 8500.0,
            'battery_lifetime_years': 10,
            'deduction_rate': 0.5
        })
        eco = res_quote['economics']
        self.assertEqual(eco['marginal_cost_source'], 'quote_variants')
        self.assertEqual(eco['quote_variants_status'], 'provided')
        expected_marginal = (8500.0 - 6000.0) / (15.0 - 10.0)  # 500 €/kWh
        self.assertAlmostEqual(eco['effective_marginal_battery_cost_eur_kwh'], expected_marginal, places=2)

        # Check annualized costs
        expected_gross = expected_marginal / 10.0  # 50 €/kWh/yr
        self.assertAlmostEqual(eco['annualized_marginal_cost_gross'], expected_gross, places=2)
        expected_net = eco['annualized_marginal_cost_net']
        self.assertAlmostEqual(expected_net, 30.7517874522, places=4)
        self.assertLess(expected_net, expected_gross)
        self.assertGreater(expected_net, expected_gross * 0.5)

        # Check break-even cycles formula
        rte = eco['roundtrip_efficiency']
        margin_charge = eco['margin_per_charge_cycle_eur_kwh']
        p_import = eco['import_price_eur_kwh']
        p_export = eco['export_price_eur_kwh']
        expected_margin_charge = p_import * rte - p_export
        self.assertAlmostEqual(margin_charge, expected_margin_charge, places=4)

        expected_breakeven_gross = expected_gross / margin_charge
        expected_breakeven_net = expected_net / margin_charge
        self.assertAlmostEqual(eco['breakeven_charge_cycles_gross'], expected_breakeven_gross, places=1)
        self.assertAlmostEqual(eco['breakeven_charge_cycles_net'], expected_breakeven_net, places=1)

        # Cycle conversion consistency: delivered cycles * margin_deliv == charge cycles * margin_charge
        margin_deliv = eco['margin_per_delivered_cycle_eur_kwh']
        # delivered cycles = charge cycles * rte
        deliv_cycles_net = eco['breakeven_charge_cycles_net'] * rte
        annual_margin_from_deliv = deliv_cycles_net * margin_deliv
        annual_margin_from_charge = eco['breakeven_charge_cycles_net'] * margin_charge
        self.assertAlmostEqual(annual_margin_from_deliv, annual_margin_from_charge, places=4)

    def test_financial_consistency_npv_heatmap(self):
        """NPV heatmap cells compute genuine discounted NPV and capex reflects configuration alternatives."""
        res = e.battery_sizing(self.base_settings, {'step': 2, 'max_capacity': 10})
        heatmap = res['chart4_npv_heatmap']
        cells = heatmap['cells']
        self.assertGreater(len(cells), 20)

        # Verify envelope consistency in each cell
        for cell in cells:
            self.assertLessEqual(cell['npv_min'], cell['npv_median'] + 1e-6)
            self.assertLessEqual(cell['npv_median'], cell['npv_max'] + 1e-6)
            self.assertGreater(cell['capex_eur'], 0)

        # Verify that capex scales with PV and battery capacity (not fixed quote across all alternatives)
        min_pv = heatmap['pv_grid_kwp'][0]
        max_pv = heatmap['pv_grid_kwp'][-1]
        cell_min_0kwh = next(c for c in cells if abs(c['pv_kwp'] - min_pv) < 1e-4 and c['battery_kwh'] == 0.0)
        cell_max_0kwh = next(c for c in cells if abs(c['pv_kwp'] - max_pv) < 1e-4 and c['battery_kwh'] == 0.0)
        cell_min_10kwh = next(c for c in cells if abs(c['pv_kwp'] - min_pv) < 1e-4 and c['battery_kwh'] == 10.0)

        self.assertGreater(cell_max_0kwh['capex_eur'], cell_min_0kwh['capex_eur'], "Larger PV increases capex")
        self.assertGreater(cell_min_10kwh['capex_eur'], cell_min_0kwh['capex_eur'], "Larger battery increases capex")

    def test_financial_parity_with_evaluate(self):
        """Heatmap cell NPV at current configuration matches evaluate(s) pv_incremental median NPV exactly."""
        res = e.battery_sizing(self.base_settings, {'step': 2, 'max_capacity': 10})
        heatmap = res['chart4_npv_heatmap']
        cur_pv = heatmap['current_configuration']['pv_kwp']
        cur_bat = heatmap['current_configuration']['battery_kwh']
        matching_cell = next(
            c for c in heatmap['cells']
            if abs(c['pv_kwp'] - cur_pv) < 1e-4 and abs(c['battery_kwh'] - cur_bat) < 1e-4
        )
        eval_res = e.evaluate(self.base_settings)
        expected_npv = eval_res['finance']['pv_incremental']['npv']['median']
        self.assertAlmostEqual(matching_cell['npv_median'], expected_npv, places=2)

    def test_deferred_ev_preserved(self):
        """Deferred EV in lifetime dispatch preserves ev_start_year and matches evaluate(s_ev) NPV."""
        s_ev = {**self.base_settings, 'load_scenario': 'ev', 'ev_start_year': 3}
        res_ev = e.battery_sizing(s_ev, {'step': 2, 'max_capacity': 10})
        heatmap = res_ev['chart4_npv_heatmap']
        cur_pv = heatmap['current_configuration']['pv_kwp']
        cur_bat = heatmap['current_configuration']['battery_kwh']
        matching_cell = next(
            c for c in heatmap['cells']
            if abs(c['pv_kwp'] - cur_pv) < 1e-4 and abs(c['battery_kwh'] - cur_bat) < 1e-4
        )
        eval_res = e.evaluate(s_ev)
        expected_npv = eval_res['finance']['pv_incremental']['npv']['median']
        self.assertAlmostEqual(matching_cell['npv_median'], expected_npv, places=2)

        # In first operating year (year 1), EV charging has not started (ev_start_year=3),
        # so first-year annual demand in dispatch matches the electrified (no-EV) case
        s_elec = {**self.base_settings, 'load_scenario': 'electrified'}
        res_elec = e.battery_sizing(s_elec, {'step': 2, 'max_capacity': 10})
        c0_ev = res_ev['chart1_capacity_dispatch']['capacities'][0]
        c0_elec = res_elec['chart1_capacity_dispatch']['capacities'][0]
        self.assertAlmostEqual(c0_ev['grid_import_kwh']['median'], c0_elec['grid_import_kwh']['median'], places=1)

    def test_zero_battery_soc_no_invented_storage(self):
        """When battery_kwh is 0, active_capacity is 0 and status is NO_STORAGE with 0 hours."""
        s_no_bat = {**self.base_settings, 'battery_kwh': 0.0}
        res = e.battery_sizing(s_no_bat, {'step': 2, 'max_capacity': 10})
        soc = res['chart6_soc_heatmap']
        wp = e.weather_period()
        self.assertEqual(soc['battery_capacity_kwh'], 0.0)
        self.assertEqual(soc['empty_hours'], 0)
        self.assertEqual(soc['full_hours'], 0)
        self.assertEqual(soc['mid_hours'], 0)
        self.assertEqual(soc['status'], 'NO_STORAGE')
        self.assertEqual(soc.get('weather_basis'), 'ensemble_mean')
        self.assertEqual(soc.get('weather_count'), wp.count)
        self.assertTrue(np.all(np.array(soc['hourly_soc_matrix']) == 0))
        self.assertEqual(len(soc['monthly_summaries']), 12)
        for ms in soc['monthly_summaries']:
            self.assertEqual(ms['mean_soc_pct'], 0.0)
            self.assertEqual(ms['empty_fraction'], 0.0)
            self.assertEqual(ms['full_fraction'], 0.0)
            self.assertEqual(ms['mid_fraction'], 0.0)

    def test_spread_sensitivity_affects_savings_and_optimum(self):
        """Varying sensitivity_spread_delta shifts incremental savings."""
        res_base = e.battery_sizing(self.base_settings, {'sensitivity_spread_delta': 0.0})
        res_high = e.battery_sizing(self.base_settings, {'sensitivity_spread_delta': 0.10})
        res_low = e.battery_sizing(self.base_settings, {'sensitivity_spread_delta': -0.10})
        s_base = res_base['chart2_incremental_savings']['steps'][0]['incremental_savings_eur_kwh']['median']
        s_high = res_high['chart2_incremental_savings']['steps'][0]['incremental_savings_eur_kwh']['median']
        s_low = res_low['chart2_incremental_savings']['steps'][0]['incremental_savings_eur_kwh']['median']
        # Higher export price reduces battery savings vs grid export; lower export price increases battery savings
        self.assertLess(s_high, s_base, "Higher export price reduces battery savings vs exporting")
        self.assertGreater(s_low, s_base, "Lower export price increases battery savings vs exporting")

    def test_strict_option_validation(self):
        """Rejects boolean for int/float, float step, invalid deduction rates, unknown options."""
        with self.assertRaisesRegex(ValueError, 'step must be integer 1 or 2'):
            e.battery_sizing(self.base_settings, {'step': True})
        with self.assertRaisesRegex(ValueError, 'step must be integer 1 or 2'):
            e.battery_sizing(self.base_settings, {'step': 1.5})
        with self.assertRaisesRegex(ValueError, 'battery_lifetime_years must be integer'):
            e.battery_sizing(self.base_settings, {'battery_lifetime_years': True})
        with self.assertRaisesRegex(ValueError, 'deduction_rate must be exactly'):
            e.battery_sizing(self.base_settings, {'deduction_rate': 0.499999})
        with self.assertRaisesRegex(ValueError, 'weather_representative must be low, median, or high'):
            e.battery_sizing(self.base_settings, {'weather_representative': 'invalid_enum'})
        with self.assertRaisesRegex(ValueError, 'Unknown options'):
            e.battery_sizing(self.base_settings, {'bogus_option': 123})

    def test_bounded_validation_errors(self):
        """Invalid bounds, steps, and quote order produce explicit ValueErrors."""
        with self.assertRaisesRegex(ValueError, 'step must be integer 1 or 2'):
            e.battery_sizing(self.base_settings, {'step': 5})

        with self.assertRaisesRegex(ValueError, 'max_capacity must be integer between 10 and 50'):
            e.battery_sizing(self.base_settings, {'max_capacity': 5})

        with self.assertRaisesRegex(ValueError, 'load_scenario must be'):
            e.battery_sizing(self.base_settings, {'load_scenario': 'invalid_scenario'})

        with self.assertRaisesRegex(ValueError, 'quote_15kwh_eur must exceed quote_10kwh_eur'):
            e.battery_sizing(self.base_settings, {'quote_10kwh_eur': 8000, 'quote_15kwh_eur': 7000})

        with self.assertRaisesRegex(ValueError, 'battery_lifetime_years must be integer between 1 and 30'):
            e.battery_sizing(self.base_settings, {'battery_lifetime_years': 45})


class BatterySizingRouteTests(unittest.TestCase):
    def test_battery_sizing_route_integration(self):
        """POST /api/battery-sizing responds 200 with compute_seconds and valid structure."""
        payload = {
            'settings': {
                'south_panels': 12,
                'west_panels': 0,
                'east_panels': 0,
                'battery_kwh': 5.0,
                'load_scenario': 'baseline'
            },
            'options': {
                'step': 2,
                'max_capacity': 10
            }
        }
        encoded = json.dumps(payload).encode('utf-8')

        class MockRequest:
            headers = {'Content-Length': str(len(encoded))}
            rfile = io.BytesIO(encoded)
            path = '/api/battery-sizing'
            response = None
            status = None

            def send(self, obj, status=200):
                self.response = obj
                self.status = status

        req = MockRequest()
        server_module.LegacyLocalHandler.do_POST(req)
        self.assertEqual(req.status, 200)
        self.assertIn('chart1_capacity_dispatch', req.response)
        self.assertIn('chart2_incremental_savings', req.response)
        self.assertIn('chart3_efc_breakeven', req.response)
        self.assertIn('chart4_npv_heatmap', req.response)
        self.assertIn('chart5_needed_capacity_cdf', req.response)
        self.assertIn('chart6_soc_heatmap', req.response)
        self.assertEqual(req.response['chart5_needed_capacity_cdf']['weather_basis'], 'ensemble_mean')
        self.assertEqual(req.response['chart6_soc_heatmap']['weather_basis'], 'ensemble_mean')
        self.assertIn('economics', req.response)
        self.assertIn('source_vs_scenario', req.response)
        self.assertIn('load_representativeness_note', req.response)
        self.assertIsInstance(req.response['compute_seconds'], float)



class MixedBatterySizingAndSweepTests(IsolatedDefaultsTestCase):
    def setUp(self):
        super().setUp()
        self.mixed_settings = {
            'south_panels': 12,
            'west_panels': 0,
            'east_panels': 0,
            'battery_mixed_sizes': True,
            'battery_modules': 2,
            'battery_secondary_modules': 1,
            'battery_module_kwh': 5.0,
            'battery_secondary_module_kwh': 6.9,
            'battery_kwh': 11.9,
            'roundtrip_efficiency': 0.9,
            'inverter_kw': 6.0,
            'charge_kw': 5.0,
            'discharge_kw': 5.0,
            'horizon_years': 20,
            'battery_replace_year': 12,
            'deduction_rate': 0.5,
            'principal_residence_confirmed': True, 'deduction_tax_capacity': 10000.,
            'load_scenario': 'electrified'
        }

    def test_battery_mixed_grid_has_attainable_capacities(self):
        """Mixed battery analysis evaluates attainable module combinations (5, 6.9, 10, 11.9, 13.8, etc.) within max."""
        res = e.battery_sizing(self.mixed_settings, {'max_capacity': 15})
        chart1 = res['chart1_capacity_dispatch']
        caps = [round(c['capacity_kwh'], 1) for c in chart1['capacities']]
        expected_caps = [0.0, 5.0, 6.9, 10.0, 11.9, 13.8, 15.0]
        self.assertEqual(caps, expected_caps)

        # Chart 1 metadata exposed
        self.assertEqual(chart1.get('grid_mode'), 'mixed')
        step_meta = chart1.get('step_metadata', {})
        self.assertEqual(step_meta.get('mode'), 'attainable_hardware_compositions')
        self.assertFalse(step_meta.get('subsampling_applied'))

        # Row composition and valid settings metadata
        for row in chart1['capacities']:
            self.assertIn('composition', row)
            self.assertIn('available_compositions', row)
            self.assertIn('settings', row)
            comp = row['composition']
            self.assertTrue(comp['battery_mixed_sizes'])
            self.assertAlmostEqual(row['settings']['battery_kwh'], row['capacity_kwh'], places=9)
            self.assertEqual(row['settings']['battery_modules'], comp['battery_modules'])
            self.assertEqual(row['settings']['battery_secondary_modules'], comp['battery_secondary_modules'])

    def test_fractional_optimum_bands_survive_without_integer_coercion(self):
        """Fractional capacities like 11.9 survive in mixed optimum band and primary ensemble optimums match argmax mean net benefit."""
        res = e.battery_sizing(self.mixed_settings, {'max_capacity': 15})
        opt_band = res['chart2_incremental_savings']['optimum_band']
        for key in ['ensemble_optimum_kwh', 'gross_ensemble_optimum_kwh', 'weather_min_kwh', 'weather_max_kwh', 'weather_median_kwh', 'gross_optimum_min_kwh', 'gross_optimum_max_kwh', 'sensitivity_min_kwh', 'sensitivity_max_kwh']:
            val = opt_band[key]
            self.assertIsInstance(val, float, f"{key} must be float to preserve fractional sizes")

        # Verify ensemble optimum matches argmax of mean savings minus annualized costs
        caps = res['chart1_capacity_dispatch']['capacities']
        capacities = [float(c['capacity_kwh']) for c in caps]
        mean_savings = [float(np.mean(c['annual_savings_by_weather'])) for c in caps]
        s = res['settings']
        deg_annual = float(s.get('battery_calendar_fade', 0.015))
        lifetime = int(s.get('battery_replace_year', 12) if s.get('battery_replace_year', 0) > 0 else 12)
        rnom = (1.0 + s['real_discount_rate']) * (1.0 + s['inflation']) - 1.0
        denom_pv = sum(1.0 / ((1.0 + rnom) ** t) for t in range(1, lifetime + 1))
        deg_pv = sum(((1.0 - deg_annual) ** (t - 1)) / ((1.0 + rnom) ** t) for t in range(1, lifetime + 1))
        deg_factor = deg_pv / denom_pv if denom_pv > 0 else 1.0

        steps = res['chart2_incremental_savings']['steps']
        cum_net_cost = [0.0]
        cum_gross_cost = [0.0]
        for st in steps:
            cum_net_cost.append(cum_net_cost[-1] + st['annualized_cost_net'] * st['delta_capacity_kwh'])
            cum_gross_cost.append(cum_gross_cost[-1] + st['annualized_cost_gross'] * st['delta_capacity_kwh'])

        net_benefits = [mean_savings[i] * deg_factor - cum_net_cost[i] for i in range(len(capacities))]
        gross_benefits = [mean_savings[i] * deg_factor - cum_gross_cost[i] for i in range(len(capacities))]
        expected_ens_opt = capacities[int(np.argmax(net_benefits))]
        expected_gross_opt = capacities[int(np.argmax(gross_benefits))]
        self.assertAlmostEqual(opt_band['ensemble_optimum_kwh'], expected_ens_opt, places=4)
        self.assertAlmostEqual(opt_band['gross_ensemble_optimum_kwh'], expected_gross_opt, places=4)

        # Attainable hardware grid contains 11.9 kWh
        caps_rounded = [round(c, 1) for c in capacities]
        self.assertIn(11.9, caps_rounded)

    def test_duplicate_capacity_sizes_avoid_zero_deltas(self):
        """Duplicate-capacity compositions reuse numeric results with deterministic representative and avoid zero deltas."""
        settings_dup = {
            **self.mixed_settings,
            'battery_module_kwh': 5.0,
            'battery_secondary_module_kwh': 10.0,
            'battery_modules': 1,
            'battery_secondary_modules': 1,
            'battery_kwh': 10.0,
        }
        res = e.battery_sizing(settings_dup, {'max_capacity': 10})
        steps = res['chart2_incremental_savings']['steps']
        # No zero delta in steps; all incremental steps have strictly positive delta_capacity_kwh
        for s in steps:
            self.assertGreater(s['delta_capacity_kwh'], 0.0, "Delta capacity must be strictly positive")
            for diff in s['incremental_savings_by_weather']:
                self.assertTrue(math.isfinite(diff), "Incremental savings must be finite without zero-width division")

        # 10 kWh has two valid compositions: 2x5m (2m_0sec) and 1x10m (1m_1sec)
        row_10 = next(r for r in res['chart1_capacity_dispatch']['capacities'] if math.isclose(r['capacity_kwh'], 10.0))
        self.assertEqual(len(row_10['available_compositions']), 2)
        # Deterministic representative selected fewest total modules: 1 module of 10 kWh
        self.assertEqual(row_10['composition']['battery_modules'], 1)
        self.assertEqual(row_10['composition']['battery_secondary_modules'], 1)

    def test_mixed_heatmap_metadata_and_settings_match_evaluate(self):
        """Mixed heatmap cells carry composition and settings matching evaluate() for benchmark scenarios."""
        res = e.battery_sizing(self.mixed_settings, {'max_capacity': 15})
        heatmap = res['chart4_npv_heatmap']
        cells = heatmap['cells']
        self.assertIn(11.9, heatmap['battery_grid_kwh'])

        # Find benchmark cell at 6.0 kWp and 11.9 kWh
        cell_11_9 = next(c for c in cells if math.isclose(c['pv_kwp'], 6.0, abs_tol=1e-3) and math.isclose(c['battery_kwh'], 11.9, abs_tol=1e-3))
        self.assertIn('composition', cell_11_9)
        self.assertIn('settings', cell_11_9)
        self.assertEqual(cell_11_9['composition']['battery_modules'], 2)
        self.assertEqual(cell_11_9['composition']['battery_secondary_modules'], 1)

        # Directly evaluate cell settings and compare
        ev = e.evaluate(cell_11_9['settings'])
        self.assertAlmostEqual(ev['settings']['battery_kwh'], cell_11_9['battery_kwh'], places=9)
        self.assertEqual(ev['settings']['battery_modules'], 2)
        self.assertEqual(ev['settings']['battery_secondary_modules'], 1)
        self.assertTrue(ev['settings']['battery_mixed_sizes'])
        self.assertAlmostEqual(ev['metrics']['npv_eur']['median'], cell_11_9['npv_median'], places=2)

    def test_active_current_mix_retained_and_labeled(self):
        """Current selected mix is retained, labeled, and marked appropriately when outside search range."""
        # Mix outside search range: 3 modules, 1 secondary = 16.9 kWh with max_capacity = 10
        settings_out = {
            **self.mixed_settings,
            'battery_modules': 3,
            'battery_secondary_modules': 1,
            'battery_kwh': 16.9,
        }
        res = e.battery_sizing(settings_out, {'max_capacity': 10})
        chart1 = res['chart1_capacity_dispatch']
        row_16_9 = next((r for r in chart1['capacities'] if math.isclose(r['capacity_kwh'], 16.9, abs_tol=1e-3)), None)
        self.assertIsNotNone(row_16_9, "Current mix outside max_capacity must be retained in dispatch")
        self.assertTrue(row_16_9['outside_analysis_search_range'])
        self.assertEqual(row_16_9['search_range_status'], 'outside_search_range')

        curr_cfg = res['chart4_npv_heatmap']['current_configuration']
        self.assertAlmostEqual(curr_cfg['battery_kwh'], 16.9, places=3)
        self.assertTrue(curr_cfg['outside_analysis_search_range'])
        self.assertIsNotNone(curr_cfg['composition'])

    def test_tiny_size_generation_rejects_promptly(self):
        """Tiny module sizes that would blow up candidate count reject promptly before heavy work."""
        tiny_settings = {**self.mixed_settings, 'battery_module_kwh': 0.01}
        with self.assertRaisesRegex(ValueError, 'too many candidate capacities'):
            e.battery_sizing(tiny_settings, {'max_capacity': 25})

        tiny_sec_settings = {**self.mixed_settings, 'battery_secondary_module_kwh': 0.01}
        with self.assertRaisesRegex(ValueError, 'too many candidate capacities'):
            e.battery_sizing(tiny_sec_settings, {'max_capacity': 25})

    def test_mixed_sweep_zero_no_pv_and_row_settings_agree(self):
        """Mixed sweep produces zero storage for no_pv, preserves custom mix, and maintains row/settings capacity parity."""
        sweep_res = e.sweep(self.mixed_settings)
        candidates = sweep_res['candidates']

        # 1. no_pv rows must zero BOTH counts and have 0 kWh
        no_pv_rows = [c for c in candidates if c['label'] == 'no_pv']
        self.assertGreater(len(no_pv_rows), 0)
        for c in no_pv_rows:
            self.assertEqual(c['battery_kwh'], 0.0)
            self.assertEqual(c['battery_modules'], 0)
            self.assertEqual(c['battery_secondary_modules'], 0)
            self.assertEqual(c['settings']['battery_modules'], 0)
            self.assertEqual(c['settings']['battery_secondary_modules'], 0)
            self.assertEqual(c['settings']['battery_kwh'], 0.0)
            self.assertIn('0sec', c['id'])

        # 2. custom rows preserve the selected mix (11.9 kWh: 2 modules, 1 sec)
        custom_rows = [c for c in candidates if c['label'] == 'custom']
        self.assertGreater(len(custom_rows), 0)
        for c in custom_rows:
            self.assertAlmostEqual(c['battery_kwh'], 11.9, places=9)
            self.assertEqual(c['battery_modules'], 2)
            self.assertEqual(c['battery_secondary_modules'], 1)
            self.assertAlmostEqual(c['settings']['battery_kwh'], 11.9, places=9)
            self.assertIn('11.9_1sec', c['id'])

        # 3. All candidates carry valid actual compositions and match evaluate()
        for c in candidates:
            self.assertAlmostEqual(c['battery_kwh'], c['settings']['battery_kwh'], places=9)
        sample = candidates[::10] + [no_pv_rows[0], custom_rows[0]]
        for c in sample:
            ev = e.evaluate(c['settings'])
            self.assertAlmostEqual(ev['settings']['battery_kwh'], c['battery_kwh'], places=9)

    def test_daily_needed_capacity_cdf_continuous_diagnostic_metadata(self):
        """Daily needed-capacity CDF includes diagnostic and physical hardware distinction metadata."""
        res = e.battery_sizing(self.mixed_settings, {'max_capacity': 15})
        cdf = res['chart5_needed_capacity_cdf']
        self.assertEqual(cdf.get('diagnostic_nature'), 'continuous_diagnostic')
        self.assertIn('distinct', cdf.get('hardware_distinction_note', '').lower())


if __name__ == '__main__':
    unittest.main()

