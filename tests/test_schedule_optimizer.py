"""Tests for schedule optimizer switch hour tradeoff evaluation."""
from feasibility.runtime_context import current_runtime
from copy import deepcopy
from unittest.mock import patch
import numpy as np

import feasibility.engine as eng
from feasibility.schedule_optimizer import (
    switch_hour_tradeoff,
    _cached_switch_hour_tradeoff,
)
from test_support import IsolatedDefaultsTestCase


def _synthetic_annual(w_count=1):
    arr = np.zeros((1, w_count, 14), dtype=float)
    arr[0, :, 0] = 1000.0  # total load
    arr[0, :, 5] = 200.0   # grid import
    return arr


def _synthetic_monthly(clip=10.0, soc=98.0, op_val=50.0):
    return [
        {
            'load_kwh': 1000.0/12,
            'mean_peak_soc_pct': soc,
            'median_peak_soc_pct': soc,
            'p25_peak_soc_pct': soc,
            'clipped_kwh': float(clip[m] if isinstance(clip, (list, tuple)) else clip),
            'grid_import_kwh': float(200.0 - (op_val[m] if isinstance(op_val, (list, tuple)) else op_val)),
            'grid_export_kwh': 0.0,
            'export_price_weighted_kwh': 0.0,
        }
        for m in range(12)
    ]


class TestScheduleOptimizer(IsolatedDefaultsTestCase):
    """Test suite for feasibility.schedule_optimizer switch_hour_tradeoff."""

    def setUp(self):
        super().setUp()
        _cached_switch_hour_tradeoff.cache_clear()
        self.settings = eng.validate({
            'pv_dispatch_model': 'physical',
            'battery_kwh': 10.0,
        })
        self.fin_basis = getattr(eng, 'ROUTING_BASIS_DEFAULT', 'solar_equity')

    def _run_with_mocks(
        self,
        settings=None,
        base_monthly=None,
        cand_monthly=None,
        base_npv=1000.0,
        cand_npv_by_hour=None,
        dispatch_override=None,
        financial_override=None,
    ):
        _cached_switch_hour_tradeoff.cache_clear()
        target_settings = settings or self.settings
        b_monthly = base_monthly if base_monthly is not None else _synthetic_monthly(clip=10.0, soc=98.0, op_val=50.0)
        c_monthly = cand_monthly if cand_monthly is not None else _synthetic_monthly(clip=5.0, soc=98.0, op_val=60.0)

        def mock_dispatch(ekey, price_ver, forecast, scen, median):
            if dispatch_override:
                res = dispatch_override(ekey)
                if res is not None:
                    return res
            cfg = dict(ekey)
            if cfg.get('routing_priority') == 'battery_first':
                return {'monthly_clipping': deepcopy(b_monthly)}
            if cfg.get('routing_priority') == 'dynamic_reserve':
                return {'monthly_clipping': deepcopy(c_monthly)}
            start_m = cfg.get('scheduled_start_month')
            end_m = cfg.get('scheduled_end_month')
            if start_m is not None and end_m is not None:
                monthly = [
                    deepcopy(c_monthly[i]) if (start_m <= i + 1 <= end_m) else deepcopy(b_monthly[i])
                    for i in range(12)
                ]
                return {'monthly_clipping': monthly}
            return {'monthly_clipping': deepcopy(c_monthly)}

        sample_base = mock_dispatch((('routing_priority', 'battery_first'),), 0, None, None, None)
        if sample_base and 'weather_monthly_clipping' in sample_base:
            w_count = len(sample_base['weather_monthly_clipping'])
        else:
            w_count = 1

        def compute_val_delta(cfg_dict):
            cfg_tup = tuple((k, cfg_dict.get(k)) for k in ('routing_priority', 'routing_switch_hour', 'scheduled_start_month', 'scheduled_end_month'))
            disp = mock_dispatch(cfg_tup, 0, None, None, None)
            if not disp or 'monthly_clipping' not in disp or not sample_base or 'monthly_clipping' not in sample_base:
                return 0.0
            c_m = disp['monthly_clipping']
            b_m = sample_base['monthly_clipping']
            if not c_m or not b_m or len(c_m) != len(b_m):
                return 0.0
            return sum((b.get('grid_import_kwh', 0.0) - c.get('grid_import_kwh', 0.0)) for b, c in zip(b_m, c_m))

        def mock_financial(s, annual, kwp, calculate_irr=False, hp_summary=None):
            if financial_override:
                res = financial_override(s, annual, kwp)
                if res is not None:
                    return res
            prio = s.get('routing_priority')
            if prio == 'battery_first':
                npv = base_npv
            elif cand_npv_by_hour:
                h = s.get('routing_switch_hour')
                if prio == 'dynamic_reserve' and 'dynamic' in cand_npv_by_hour:
                    npv = cand_npv_by_hour['dynamic']
                elif h in cand_npv_by_hour:
                    npv = cand_npv_by_hour[h]
                else:
                    npv = base_npv
            else:
                val_delta = compute_val_delta(s)
                npv = base_npv + round(val_delta * 10.0, 2)
            cur_w = annual.shape[1] if annual is not None and hasattr(annual, 'shape') and len(annual.shape) >= 2 else w_count
            fin_equity = {
                'npv': {'median': npv},
                'npv_by_weather': [npv] * cur_w,
            }
            return {self.fin_basis: fin_equity, 'solar_equity': fin_equity}, None

        patches = [
            patch('feasibility.engine._energy_cache', side_effect=lambda key, price_ver=0: (_synthetic_annual(w_count), {})),
            patch('feasibility.engine._financial', side_effect=mock_financial),
            patch('feasibility.schedule_optimizer._trace_monthly_summary', side_effect=lambda engine, cfg, key, version, base_ri=None, prepared_context=None: (0, mock_dispatch(key, version, None, None, None))),
        ]
        if hasattr(eng, '_dispatch_matrix_fast'):
            patches.append(patch('feasibility.engine._dispatch_matrix_fast', side_effect=mock_dispatch))

        with patches[0], patches[1], patches[2] as summary, patch('feasibility.schedule_optimizer.prepare_dispatch_context', return_value={}):
            self.summary_mock = summary
            return switch_hour_tradeoff(target_settings)

    def test_six_hours_plus_baseline_rows(self):
        res = self._run_with_mocks()
        rows = res['rows']
        self.assertGreaterEqual(len(rows), 8)
        self.assertEqual(rows[0]['routing_priority'], 'battery_first')
        self.assertIsNone(rows[0]['routing_switch_hour'])
        self.assertEqual(rows[0]['status'], 'baseline')
        self.assertEqual(rows[0]['soc_statistic'], 'p25_daily_peak_all_weather')

        switch_hours = {r['routing_switch_hour'] for r in rows if r['routing_priority'] == 'scheduled_hybrid'}
        self.assertEqual(switch_hours, set(range(9, 15)))
        for r in rows:
            if r['routing_priority'] == 'scheduled_hybrid':
                self.assertEqual(r['status'], 'feasible')
                self.assertEqual(r['soc_statistic'], 'p25_daily_peak_all_weather')
        dyn_rows = [r for r in rows if r['routing_priority'] == 'dynamic_reserve']
        self.assertGreaterEqual(len(dyn_rows), 1)
        self.assertIsNone(dyn_rows[0]['routing_switch_hour'])
        self.assertIsNotNone(dyn_rows[0]['clipped_kwh'])
        self.assertEqual(dyn_rows[0]['soc_statistic'], 'p25_daily_peak_all_weather')
        self.assertIsNotNone(res['recommendation'])
        self.assertIsNotNone(res['recommendation_npv'])
        self.assertIsNotNone(res['recommendation_clipping'])
        self.assertEqual(res['recommendation'], res['recommendation_npv'])
        self.assertIn('horizon_years', res)
        self.assertEqual(res['recommendation']['routing_priority'], 'scheduled_hybrid')

    def test_disabled_metrics_none(self):
        legacy_s = eng.validate({'pv_dispatch_model': 'legacy', 'battery_kwh': 10.0})
        zero_bat_s = eng.validate({'pv_dispatch_model': 'physical', 'battery_kwh': 0.0})

        for s, expected_status in [(legacy_s, 'disabled'), (zero_bat_s, 'zero_battery')]:
            res = self._run_with_mocks(settings=s)
            self.assertIsNone(res['recommendation'])
            self.assertIsNone(res['recommendation_npv'])
            self.assertIsNone(res['recommendation_clipping'])
            self.assertEqual(len(res['rows']), 8)
            for r in res['rows']:
                self.assertEqual(r['status'], expected_status)
                self.assertIsNone(r['clipped_kwh'])
                self.assertIsNone(r['summer_p25_peak_soc_pct'])
                self.assertIsNone(r['grid_import_kwh'])
                self.assertIsNone(r['autarky'])
                self.assertIsNone(r['equity_npv_delta'])
                self.assertNotIn('soc_statistic', r)
            self.assertEqual(res['rows'][-1]['routing_priority'], 'dynamic_reserve')

    def test_soc_drop_or_missing_does_not_gate_eligibility(self):
        cand_soc_none = _synthetic_monthly(clip=5.0, soc=None, op_val=60.0)
        res_none = self._run_with_mocks(cand_monthly=cand_soc_none)
        self.assertIsNotNone(res_none['recommendation'])
        for r in res_none['rows'][1:7]:
            self.assertEqual(r['status'], 'feasible')

        cand_soc_low = _synthetic_monthly(clip=5.0, soc=94.4, op_val=60.0)
        res_low = self._run_with_mocks(cand_monthly=cand_soc_low)
        self.assertIsNotNone(res_low['recommendation'])
        for r in res_low['rows'][1:7]:
            self.assertEqual(r['status'], 'feasible')

    def test_negative_monthly_value_rejected(self):
        res = self._run_with_mocks(cand_monthly=_synthetic_monthly(op_val=40.0, clip=5.0))
        self.assertIsNone(res['recommendation'])
        for r in res['rows'][1:7]:
            self.assertEqual(r['status'], 'no_eligible_months')
            self.assertIsNone(r['clipped_kwh'])

    def test_candidate_requires_positive_operating_value_no_clipping_threshold(self):
        # Baseline clipping <= 0.5 everywhere is eligible when operating value improves
        base_low_clip = _synthetic_monthly(clip=0.4, soc=98.0, op_val=50.0)
        res_low_clip = self._run_with_mocks(base_monthly=base_low_clip)
        self.assertIsNotNone(res_low_clip['recommendation'])
        for r in res_low_clip['rows'][1:7]:
            self.assertEqual(r['status'], 'feasible')

        # Zero monthly value delta with positive clipping + nonnegative exact NPV is valid recommendation per spec
        cand_equal_op = _synthetic_monthly(clip=5.0, soc=98.0, op_val=50.0)
        res_op = self._run_with_mocks(cand_monthly=cand_equal_op)
        rec_op = res_op['recommendation']
        self.assertIsNotNone(rec_op)
        self.assertEqual(rec_op['net_energy_value_delta_eur'], 0.0)
        self.assertEqual(rec_op['equity_npv_delta'], 0.0)
        self.assertGreater(rec_op['clipping_saved_kwh'], 0.0)
        self.assertIn('clipping', rec_op['selection_objectives'])

    def test_splits_gaps_in_eligible_months(self):
        # Economic gaps: op_val=50.0 equal baseline outside chosen span
        # Positive value improvement in months 4, 5 and 7, 8, 9 (month 6 gap op_val=50.0)
        cand_op = [50.0]*3 + [60.0, 60.0, 50.0, 60.0, 60.0, 60.0] + [50.0]*3
        cand_monthly = _synthetic_monthly(clip=5.0, soc=98.0, op_val=cand_op)
        res = self._run_with_mocks(cand_monthly=cand_monthly)

        # Higher total value span [7, 8, 9] (len 3) must be chosen over [4, 5] (len 2)
        rec = next(r for r in res['rows'] if 'value' in r.get('selection_objectives', []))
        self.assertIsNotNone(rec)
        self.assertEqual(rec['scheduled_start_month'], 7)
        self.assertEqual(rec['scheduled_end_month'], 9)
        self.assertEqual(rec['active_months'], [7, 8, 9])

    def test_recommendation_clipping_tie_break(self):
        def override(key):
            cfg = dict(key)
            if cfg.get('routing_priority') in ('battery_first', 'dynamic_reserve'):
                return None
            h = cfg['routing_switch_hour']
            return {'monthly_clipping': _synthetic_monthly(
                clip=2.0 if h == 12 else 5.0,
                op_val=60.0)}
        res = self._run_with_mocks(dispatch_override=override)
        rec = res['recommendation_clipping'] or res['recommendation']
        self.assertEqual(rec['routing_switch_hour'], 12)
        self.assertEqual(rec['clipped_kwh'], 24.0)
        self.assertIsNotNone(rec['equity_npv_delta'])

    def test_relative_soc_keeps_spring_through_september(self):
        base = _synthetic_monthly(clip=[0.1]*2+[10.0]*7+[0.1]*3, soc=93.0)
        cand_op = [50.0]*2 + [60.0]*7 + [50.0]*3
        cand = _synthetic_monthly(clip=1.0, soc=89.5, op_val=cand_op)
        res = self._run_with_mocks(base_monthly=base, cand_monthly=cand)
        self.assertEqual(res['recommendation']['active_months'], list(range(3, 10)))
        self.assertEqual(self.summary_mock.call_count, 14)
        configs = [dict(call.args[2]) for call in self.summary_mock.call_args_list]
        self.assertEqual([c['routing_switch_hour'] for c in configs[1:7]], list(range(9,15)))
        self.assertTrue(all((c['scheduled_start_month'], c['scheduled_end_month']) == (1,12)
                            for c in configs[1:7]))
        self.assertEqual((configs[7]['scheduled_start_month'], configs[7]['scheduled_end_month']), (3,9))

    def test_verification_rejects_month_boundary_value_failure(self):
        cand_op = [50.0]*2 + [60.0]*7 + [50.0]*3
        cand = _synthetic_monthly(clip=1.0, soc=98.0, op_val=cand_op)
        def override(key):
            cfg = dict(key)
            if cfg.get('routing_priority') in ('battery_first', 'dynamic_reserve'):
                return None
            if cfg.get('routing_priority') == 'scheduled_hybrid' and cfg.get('scheduled_start_month') != 1:
                return {'monthly_clipping': _synthetic_monthly(clip=1.0, soc=98.0, op_val=40.0)}
        res = self._run_with_mocks(cand_monthly=cand, dispatch_override=override)
        # Verification month-boundary failure remains valid clipping if monthly value >= -0.25; assert NOT qualified 'value'
        rec = res['recommendation']
        self.assertIsNotNone(rec)
        self.assertNotIn('value', rec.get('selection_objectives', []))
        self.assertIn('clipping', rec.get('selection_objectives', []))

    def test_result_deepcopy_and_warm_cache_no_recomputation(self):
        res1 = self._run_with_mocks()
        self.assertIsNotNone(res1['recommendation'])
        res1['rows'][0]['status'] = 'mutated'
        res1['recommendation']['status'] = 'mutated'

        with patch('feasibility.schedule_optimizer._trace_monthly_summary') as mock_disp:
            res2 = switch_hour_tradeoff(self.settings)
            # Warm cache hit: dispatch matrix should not be recomputed
            mock_disp.assert_not_called()

        self.assertEqual(res2['rows'][0]['status'], 'baseline')
        self.assertEqual(res2['recommendation']['status'], 'feasible')

    def test_nested_schedule_settings_thaw_called(self):
        with patch('feasibility.engine.thaw_settings', side_effect=eng.thaw_settings) as mock_thaw:
            self._run_with_mocks()
            mock_thaw.assert_called()

    def test_all_hour_verification_changes_recommendation(self):
        base_clip = [0.1] * 2 + [10.0] * 7 + [0.1] * 3
        base_monthly = _synthetic_monthly(clip=base_clip, soc=98.0, op_val=50.0)
        cand_op = [50.0] * 2 + [60.0] * 7 + [50.0] * 3

        def dispatch_override(key):
            cfg = dict(key)
            if cfg.get('routing_priority') in ('battery_first', 'dynamic_reserve'):
                return None
            h = cfg.get('routing_switch_hour')
            start_m = cfg.get('scheduled_start_month')
            end_m = cfg.get('scheduled_end_month')
            if (start_m, end_m) == (1, 12):
                clip = 1.0 if h == 11 else (2.0 if h == 12 else 3.0)
                return {'monthly_clipping': _synthetic_monthly(clip=clip, soc=98.0, op_val=cand_op)}
            else:
                active_clip = 4.0 if h == 11 else (2.0 if h == 12 else 3.0)
                cand_month = _synthetic_monthly(clip=active_clip, soc=98.0, op_val=60.0)
                monthly = [
                    deepcopy(cand_month[i]) if (start_m <= i + 1 <= end_m) else deepcopy(base_monthly[i])
                    for i in range(12)
                ]
                return {'monthly_clipping': monthly}

        res = self._run_with_mocks(base_monthly=base_monthly, dispatch_override=dispatch_override)

        self.assertIsNotNone(res['recommendation'])
        best_hybrid = max((r for r in res['rows'] if r['routing_priority'] == 'scheduled_hybrid'
                           and r['status'] == 'feasible'),
                          key=lambda r: (r['equity_npv_delta'], r['clipping_saved_kwh']))
        self.assertEqual(best_hybrid['routing_switch_hour'], 12)
        feasible_rows = [r for r in res['rows'] if r['status'] == 'feasible']
        self.assertGreaterEqual(len(feasible_rows), 6)
        for r in feasible_rows:
            self.assertEqual(r['metrics_basis'], 'verified_trace')
        self.assertEqual(self.summary_mock.call_count, 14)

    def test_full_ensemble_matches_forecast_engine(self):
        settings = eng.validate({'pv_dispatch_model': 'physical', 'battery_kwh': 10.,
                                 'south_panels': 24, 'inverter_kw': 3.,
                                 'pv_battery_coupling': 'dc_coupled'})
        from feasibility.schedule_optimizer import _trace_monthly_summary
        with patch.object(eng, 'dispatch_weather_years', wraps=eng.dispatch_weather_years) as dispatch:
            result = switch_hour_tradeoff(settings)
        n_analogues = len(eng.data()['years'])
        verified_spans = sum(bool(r['active_months']) for r in result['rows']
                             if r['routing_priority'] == 'scheduled_hybrid')
        self.assertEqual(dispatch.call_count, 8 + verified_spans)
        self.assertIsNotNone(result['recommendation'])
        baseline_cfg = dict(settings, routing_priority='battery_first')
        key = tuple((k, eng.freeze(baseline_cfg[k])) for k in eng.ENERGY_KEYS)
        ri, baseline = _trace_monthly_summary(eng, baseline_cfg, key, eng.price_fingerprint(settings))
        reports = [(result['rows'][0], baseline)]
        candidate_rows = [r for r in result['rows'][1:] if r['routing_priority'] == 'scheduled_hybrid']
        self.assertGreaterEqual(len(candidate_rows), 6)
        for row in candidate_rows:
            self.assertEqual(row['status'], 'feasible')
            cfg = dict(settings, **{k: row[k] for k in (
                'routing_priority', 'routing_switch_hour', 'scheduled_start_month', 'scheduled_end_month')})
            key = tuple((k, eng.freeze(cfg[k])) for k in eng.ENERGY_KEYS)
            _, verified = _trace_monthly_summary(eng, cfg, key, eng.price_fingerprint(settings), base_ri=ri)
            reports.append((row, verified))
        self.assertEqual(result['rows'][-1]['routing_priority'], 'dynamic_reserve')
        self.assertEqual(result['rows'][-1]['status'], 'benchmark')
        self.assertIsNotNone(result['rows'][-1]['clipped_kwh'])
        base_clips = np.array([sum(m['clipped_kwh'] for m in weather)
                               for weather in baseline['weather_monthly_clipping']])
        for row, report in reports:
            annual_clips = [sum(m['clipped_kwh'] for m in weather)
                            for weather in report['weather_monthly_clipping']]
            annual_imps = [sum(m['grid_import_kwh'] for m in weather)
                           for weather in report['weather_monthly_clipping']]
            annual_exps = [sum(m['grid_export_kwh'] for m in weather)
                           for weather in report['weather_monthly_clipping']]
            annual_loads = [sum(m['load_kwh'] for m in weather)
                            for weather in report['weather_monthly_clipping']]
            self.assertAlmostEqual(row['clipped_kwh'], float(np.mean(annual_clips)), delta=1e-8)
            self.assertAlmostEqual(row['grid_import_kwh'], float(np.mean(annual_imps)), delta=1e-8)
            self.assertAlmostEqual(row['grid_export_kwh'], float(np.mean(annual_exps)), delta=1e-8)
            mean_imp = float(np.mean(annual_imps))
            mean_load = float(np.mean(annual_loads))
            mean_autarky = max(0., min(1., 1.0 - mean_imp / mean_load)) if mean_load > 0 else 0.
            self.assertAlmostEqual(row['autarky'], float(mean_autarky), delta=1e-8)
            imp_p10, imp_p90 = np.percentile(annual_imps, [10, 90])
            self.assertEqual(row['grid_import_p10_kwh'], round(float(imp_p10), 2))
            self.assertEqual(row['grid_import_p90_kwh'], round(float(imp_p90), 2))
            annual_autarkies = [
                (max(0., min(1., 1.0 - imp / load)) if load > 0 else 0.)
                for imp, load in zip(annual_imps, annual_loads)
            ]
            aut_p10, aut_p90 = np.percentile(annual_autarkies, [10, 90])
            self.assertEqual(row['autarky_p10'], round(float(aut_p10), 4))
            self.assertEqual(row['autarky_p90'], round(float(aut_p90), 4))
            self.assertEqual(row['energy_statistic'], 'weather_mean')
            self.assertEqual(row['autarky_basis'], 'one_minus_mean_import_over_mean_load')
            clips = np.array([sum(m['clipped_kwh'] for m in weather)
                              for weather in report['weather_monthly_clipping']])
            paired_savings = base_clips-clips
            self.assertAlmostEqual(row['clipping_saved_kwh'], float(np.mean(paired_savings)), delta=1e-8)
            self.assertAlmostEqual(result['rows'][0]['clipped_kwh'] - row['clipped_kwh'],
                                   row['clipping_saved_kwh'], delta=1e-8)
            self.assertEqual(row['clipping_saved_p10_kwh'], round(float(np.percentile(paired_savings,10)), 2))
            self.assertEqual(row['clipping_saved_p90_kwh'], round(float(np.percentile(paired_savings,90)), 2))
            self.assertEqual(row['clipping_improving_years'], int(np.count_nonzero(paired_savings>1e-9)))
            self.assertEqual(row['weather_year_count'], n_analogues)
            self.assertEqual(row['metrics_basis'], 'verified_trace')

    def test_eligible_when_mean_soc_drops_to_80_but_median_is_98(self):
        # Candidate mean=80% but median=98% matching baseline 98% -> eligible/feasible
        cand_monthly = _synthetic_monthly(clip=5.0, soc=80.0, op_val=60.0)
        for m in cand_monthly:
            m['median_peak_soc_pct'] = 98.0
        res = self._run_with_mocks(cand_monthly=cand_monthly)
        self.assertIsNotNone(res['recommendation'])
        for r in res['rows'][1:7]:
            self.assertEqual(r['status'], 'feasible')


    def test_candidate_median_soc_drop_remains_feasible_no_soc_gate(self):
        # No SoC gate: candidate median=94.4% with positive operating value remains feasible
        cand_monthly = _synthetic_monthly(clip=5.0, soc=98.0, op_val=60.0)
        for m in cand_monthly:
            m['median_peak_soc_pct'] = 94.4
        res = self._run_with_mocks(cand_monthly=cand_monthly)
        self.assertIsNotNone(res['recommendation'])
        for r in res['rows'][1:7]:
            self.assertEqual(r['status'], 'feasible')

    def test_annual_pairing_precedes_monthly_medians(self):
        base = _synthetic_monthly(clip=[20., 20.]+[.1]*10, op_val=50.0)
        savings = ([10., -1.], [-1., 10.], [1., 1.])
        def override(key):
            cfg = dict(key)
            if cfg['routing_priority'] == 'battery_first':
                weather = [deepcopy(base) for _ in savings]
            elif cfg.get('routing_priority') == 'dynamic_reserve':
                return None
            else:
                weather = []
                for changes in savings:
                    monthly = _synthetic_monthly(clip=[20.-v for v in changes]+[.1]*10, op_val=[60.0, 60.0] + [50.0]*10)
                    weather.append(monthly)
            return dict(monthly_clipping=weather[0], weather_monthly_clipping=weather)
        result = self._run_with_mocks(dispatch_override=override)
        self.assertEqual(result['recommendation']['active_months'], [1,2])
        self.assertAlmostEqual(result['recommendation']['clipping_saved_kwh'], 20.0 / 3.0, delta=1e-8)
        self.assertAlmostEqual(result['rows'][0]['clipped_kwh'] - result['recommendation']['clipped_kwh'],
                               result['recommendation']['clipping_saved_kwh'], delta=1e-8)
        self.assertEqual(result['recommendation']['clipping_improving_years'], 3)

    def test_paired_weather_clipping_savings_distribution(self):
        base_rep = _synthetic_monthly(clip=20.0, soc=98.0, op_val=50.0)
        base_weather = [_synthetic_monthly(clip=c, soc=98.0, op_val=50.0) for c in (10.0, 20.0, 30.0)]
        rep_cand = _synthetic_monthly(clip=5.0, soc=98.0, op_val=60.0)
        cand12_weather = [_synthetic_monthly(clip=c, soc=98.0, op_val=60.0) for c in (6.0, 12.0, 28.0)]
        cand11_weather = [_synthetic_monthly(clip=c, soc=98.0, op_val=60.0) for c in (9.0, 17.0, 28.0)]
        other_weather = [_synthetic_monthly(clip=25.0, soc=98.0, op_val=50.0) for _ in range(3)]

        def dispatch_override(key):
            cfg = dict(key)
            if cfg.get('routing_priority') == 'battery_first':
                return {'monthly_clipping': deepcopy(base_rep), 'weather_monthly_clipping': deepcopy(base_weather)}
            if cfg.get('routing_priority') == 'dynamic_reserve':
                return None
            h = cfg.get('routing_switch_hour')
            if h == 12:
                return {'monthly_clipping': deepcopy(rep_cand), 'weather_monthly_clipping': deepcopy(cand12_weather)}
            if h == 11:
                return {'monthly_clipping': deepcopy(rep_cand), 'weather_monthly_clipping': deepcopy(cand11_weather)}
            return {'monthly_clipping': deepcopy(other_weather[0]), 'weather_monthly_clipping': deepcopy(other_weather)}

        res = self._run_with_mocks(dispatch_override=dispatch_override)
        r11 = next(r for r in res['rows'] if r.get('routing_switch_hour') == 11)
        r12 = next(r for r in res['rows'] if r.get('routing_switch_hour') == 12)

        base_row = res['rows'][0]
        self.assertNotEqual(r12['clipped_kwh'], float(np.median([72.0, 144.0, 336.0])))
        self.assertNotEqual(r12['clipping_saved_kwh'], float(np.median([48.0, 96.0, 24.0])))
        self.assertAlmostEqual(r12['clipping_saved_kwh'], 56.0, places=1)
        self.assertAlmostEqual(base_row['clipped_kwh'] - r12['clipped_kwh'], r12['clipping_saved_kwh'], delta=0.02)
        self.assertAlmostEqual(r11['clipping_saved_kwh'], 24.0, places=1)
        self.assertAlmostEqual(base_row['clipped_kwh'] - r11['clipped_kwh'], r11['clipping_saved_kwh'], delta=0.02)
        self.assertGreater(r12['clipping_saved_kwh'], r11['clipping_saved_kwh'])
        self.assertEqual(r12['weather_year_count'], 3)
        self.assertEqual(r11['weather_year_count'], 3)
        self.assertEqual(r12['clipping_improving_years'], 3)
        self.assertEqual(r12['value_improving_years'], 3)
        self.assertAlmostEqual(r12['clipping_saved_p10_kwh'], float(np.percentile([24.0, 48.0, 96.0], 10)), places=1)
        self.assertAlmostEqual(r12['clipping_saved_p90_kwh'], float(np.percentile([24.0, 48.0, 96.0], 90)), places=1)
        self.assertEqual(res['recommendation']['routing_switch_hour'], 12)

    def test_paired_weather_candidate_bad_year_denominator_and_improving_years(self):
        base_rep = _synthetic_monthly(clip=10.0, soc=98.0, op_val=50.0)
        base_weather = [_synthetic_monthly(clip=10.0, soc=98.0, op_val=50.0) for _ in range(3)]
        cand12_rep = _synthetic_monthly(clip=5.0, soc=98.0, op_val=55.0)
        cand12_weather = [
            _synthetic_monthly(clip=4.0, soc=98.0, op_val=60.0),
            _synthetic_monthly(clip=6.0, soc=98.0, op_val=55.0),
            _synthetic_monthly(clip=14.0, soc=98.0, op_val=40.0),
        ]
        other_weather = [_synthetic_monthly(clip=20.0, soc=98.0, op_val=30.0) for _ in range(3)]

        def dispatch_override(key):
            cfg = dict(key)
            if cfg.get('routing_priority') == 'battery_first':
                return {'monthly_clipping': deepcopy(base_rep), 'weather_monthly_clipping': deepcopy(base_weather)}
            if cfg.get('routing_priority') == 'dynamic_reserve':
                return None
            if cfg.get('routing_switch_hour') == 12:
                return {'monthly_clipping': deepcopy(cand12_rep), 'weather_monthly_clipping': deepcopy(cand12_weather)}
            return {'monthly_clipping': deepcopy(other_weather[0]), 'weather_monthly_clipping': deepcopy(other_weather)}

        res = self._run_with_mocks(dispatch_override=dispatch_override)
        r12 = next(r for r in res['rows'] if r.get('routing_switch_hour') == 12)

        self.assertEqual(r12['status'], 'feasible')
        self.assertAlmostEqual(r12['clipping_saved_kwh'], 24.0, places=1)
        self.assertGreater(r12['clipping_saved_kwh'], 0.0)
        self.assertEqual(r12['weather_year_count'], 3)
        self.assertEqual(r12['clipping_improving_years'], 2)
        self.assertEqual(r12['value_improving_years'], 2)
        # Import savings [120, 60, -120] are valued at the Year 1 retail tariff.
        retail_y1 = eng.electric_bill(1, self.settings) - eng.electric_bill(0, self.settings)
        self.assertAlmostEqual(r12['net_energy_value_delta_eur'], 20.0 * retail_y1, delta=1e-8)
        self.assertAlmostEqual(res['rows'][0]['clipped_kwh'] - r12['clipped_kwh'],
                               r12['clipping_saved_kwh'], delta=1e-8)
        self.assertEqual(r12['net_energy_value_delta_p10_eur'], round(-84.0 * retail_y1, 6))
        self.assertEqual(r12['net_energy_value_delta_p90_eur'], round(108.0 * retail_y1, 6))
        self.assertEqual(res['recommendation']['routing_switch_hour'], 12)
        self.assertEqual(res['recommendation']['status'], 'feasible')

    def test_daily_peaks_and_fixed_weather_analogue(self):
        from feasibility.schedule_optimizer import _trace_monthly_summary
        trace = np.zeros((365*24, 8))
        for day in range(31):
            trace[day*24 + (8 if day%2 else 16),4] = 10
        forecast = dict(traces=[trace, np.zeros_like(trace)], picks={'median':1},
            forecast_year=2027, mapped_data={'mapped_pv':np.zeros((2,365*24)),
                                          'mapped_pzo':np.ones((2,365*24))})
        with patch.object(eng,'_forecast_2027_runs',return_value=forecast):
            ri, report = _trace_monthly_summary(eng,self.settings,(),0,base_ri=0)
        self.assertEqual(ri,0)
        self.assertEqual(report['monthly_clipping'][0]['mean_peak_soc_pct'],100)
        self.assertEqual(report['monthly_clipping'][0]['mean_peak_soc_kwh'],10)

    def test_conflicting_clipping_vs_value_hour_ranking(self):
        # Verified fixed hours rank (value, clipping):
        # 10h has €22 equivalent value delta but saves less clipping.
        # 13h has €11 equivalent value delta but saves more clipping.
        # 10h must be recommended over 13h.
        base_monthly = _synthetic_monthly(clip=15.0, soc=98.0, op_val=50.0)

        def dispatch_override(key):
            cfg = dict(key)
            if cfg.get('routing_priority') in ('battery_first', 'dynamic_reserve'):
                return None
            h = cfg.get('routing_switch_hour')
            if h == 10:
                return {'monthly_clipping': _synthetic_monthly(clip=12.0, soc=98.0, op_val=72.0)}
            elif h == 13:
                return {'monthly_clipping': _synthetic_monthly(clip=2.0, soc=98.0, op_val=61.0)}
            else:
                return {'monthly_clipping': _synthetic_monthly(clip=14.0, soc=98.0, op_val=55.0)}

        res = self._run_with_mocks(base_monthly=base_monthly, dispatch_override=dispatch_override)
        rec = res['recommendation']
        self.assertIsNotNone(rec)
        self.assertEqual(rec['routing_switch_hour'], 10)
        r10 = next(r for r in res['rows'] if r.get('routing_switch_hour') == 10)
        r13 = next(r for r in res['rows'] if r.get('routing_switch_hour') == 13)
        self.assertGreater(r10['net_energy_value_delta_eur'], r13['net_energy_value_delta_eur'])
        self.assertGreater(r13['clipping_saved_kwh'], r10['clipping_saved_kwh'])

    def test_zero_value_rejected_despite_clipping_reduction(self):
        # Max-NPV permits a zero delta, but Min Clipping requires a profit.
        cand_zero_val = _synthetic_monthly(clip=2.0, soc=98.0, op_val=50.0)
        res = self._run_with_mocks(cand_monthly=cand_zero_val)
        rec = res['recommendation']
        self.assertIsNotNone(rec)
        self.assertEqual(rec['net_energy_value_delta_eur'], 0.0)
        self.assertEqual(rec['equity_npv_delta'], 0.0)
        self.assertGreater(rec['clipping_saved_kwh'], 0.0)
        self.assertIn('clipping', rec['selection_objectives'])
        self.assertIsNone(res['recommendation_clipping'])

    def test_contiguous_best_span_ranked_by_value(self):
        # Spans rank: (annual median paired value, clipping).
        # Span A: months 4-5 (2 months) has higher total operating value.
        # Span B: months 7-9 (3 months) has lower operating value despite saving more clipping.
        cand_op = [50.0]*3 + [80.0, 80.0, 50.0, 60.0, 60.0, 60.0] + [50.0]*3
        cand_clip = [10.0]*3 + [8.0, 8.0, 10.0, 1.0, 1.0, 1.0] + [10.0]*3
        cand_monthly = _synthetic_monthly(clip=cand_clip, soc=98.0, op_val=cand_op)
        res = self._run_with_mocks(cand_monthly=cand_monthly)
        rec = next(r for r in res['rows'] if r['routing_priority'] == 'scheduled_hybrid'
                   and 'value' in r.get('selection_objectives', []))
        self.assertIsNotNone(rec)
        self.assertEqual(rec['scheduled_start_month'], 4)
        self.assertEqual(rec['scheduled_end_month'], 5)
        self.assertEqual(rec['active_months'], [4, 5])

    def test_dynamic_benchmark_nonzero_metrics_excluded_from_recommendation(self):
        res = self._run_with_mocks()
        rows = res['rows']
        benchmark_row = [r for r in rows if r['routing_priority'] == 'dynamic_reserve'][0]
        self.assertEqual(benchmark_row['routing_priority'], 'dynamic_reserve')
        self.assertIsNone(benchmark_row['routing_switch_hour'])
        self.assertIsNotNone(benchmark_row['clipped_kwh'])
        self.assertIsNotNone(benchmark_row['grid_import_kwh'])
        self.assertIsNotNone(benchmark_row['autarky'])
        self.assertIsNotNone(benchmark_row['summer_p25_peak_soc_pct'])
        rec = res['recommendation']
        self.assertIsNotNone(rec)
        self.assertEqual(rec['routing_priority'], 'scheduled_hybrid')
        self.assertIn(rec['routing_switch_hour'], list(range(9, 15)))

    def test_cached_switch_hour_tradeoff_three_tuple_return(self):
        frozen_s = tuple(sorted((k, eng.freeze(v)) for k, v in self.settings.items()))
        price_ver = eng.price_fingerprint(self.settings)
        with patch('feasibility.schedule_optimizer._trace_monthly_summary', side_effect=lambda engine, cfg, key, version, base_ri=None, prepared_context=None: (0, {'monthly_clipping': _synthetic_monthly(clip=5.0, soc=98.0, op_val=60.0)})),              patch('feasibility.engine._energy_cache', return_value=(_synthetic_annual(), {})),              patch('feasibility.engine._financial', return_value=({'solar_equity': {'npv': {'median': 1200.0}, 'npv_by_weather': [1200.0]}}, None)):
            res = _cached_switch_hour_tradeoff(current_runtime().key, frozen_s, price_ver)
            self.assertEqual(len(res), 3)
            rows, rec_npv, rec_clip = res
            self.assertIsInstance(rows, list)

    def test_regression_1_divergent_npv_vs_clipping_winners_with_arbitrage_months(self):
        # Divergent NPV vs clipping winners including zero-clipping arbitrage months outside physical span
        # Note: autarky/P25 guards ONLY required for clipping recommendations, MaxNPV can show import/SoC tradeoff
        base_monthly = _synthetic_monthly(
            clip=[0.0]*4 + [15.0]*4 + [0.0]*4,
            soc=98.0,
            op_val=50.0
        )
        def dispatch_override(key):
            cfg = dict(key)
            if cfg.get('routing_priority') in ('battery_first', 'dynamic_reserve'):
                return None
            h = cfg.get('routing_switch_hour')
            if h == 10:
                op = [50.0]*2 + [70.0]*8 + [50.0]*2
                clip = [0.0]*4 + [10.0]*4 + [0.0]*4
                return {'monthly_clipping': _synthetic_monthly(clip=clip, soc=98.0, op_val=op)}
            elif h == 13:
                op = [50.0]*4 + [62.0]*4 + [50.0]*4
                clip = [0.0]*4 + [2.0]*4 + [0.0]*4
                return {'monthly_clipping': _synthetic_monthly(clip=clip, soc=98.0, op_val=op)}
            return {'monthly_clipping': _synthetic_monthly(clip=12.0, soc=98.0, op_val=52.0)}

        def financial_override(s, annual, kwp):
            prio = s.get('routing_priority')
            if prio == 'battery_first':
                val = 1000.0
            elif s.get('routing_switch_hour') == 10:
                val = 1450.0
            elif s.get('routing_switch_hour') == 13:
                val = 1200.0
            else:
                val = 1100.0
            fin_eq = {'npv': {'median': val}, 'npv_by_weather': [val]}
            return {'solar_equity': fin_eq, self.fin_basis: fin_eq}, None

        res = self._run_with_mocks(base_monthly=base_monthly, dispatch_override=dispatch_override, financial_override=financial_override)
        rec_npv = res['recommendation_npv']
        rec_clip = res['recommendation_clipping']
        self.assertIsNotNone(rec_npv)
        self.assertIsNotNone(rec_clip)
        self.assertEqual(rec_npv['routing_switch_hour'], 10)
        self.assertEqual(rec_clip['routing_switch_hour'], 13)
        self.assertGreater(rec_npv['equity_npv_delta'], rec_clip['equity_npv_delta'])
        self.assertGreater(rec_clip['clipping_saved_kwh'], rec_npv['clipping_saved_kwh'])
        self.assertEqual(res['recommendation'], rec_npv)
        self.assertIn(3, rec_npv['active_months'])

    def test_regression_4_dynamic_can_win_both(self):
        # Dynamic can win both
        base_monthly = _synthetic_monthly(clip=15.0, soc=98.0, op_val=50.0)
        dyn_monthly = _synthetic_monthly(clip=1.0, soc=98.0, op_val=75.0)
        hybrid_monthly = _synthetic_monthly(clip=5.0, soc=98.0, op_val=60.0)

        def dispatch_override(key):
            cfg = dict(key)
            prio = cfg.get('routing_priority')
            if prio == 'battery_first':
                return {'monthly_clipping': base_monthly}
            if prio == 'dynamic_reserve':
                return {'monthly_clipping': dyn_monthly}
            return {'monthly_clipping': hybrid_monthly}

        def financial_override(s, annual, kwp):
            prio = s.get('routing_priority')
            if prio == 'battery_first':
                val = 1000.0
            elif prio == 'dynamic_reserve':
                val = 1800.0
            else:
                val = 1300.0
            fin_eq = {'npv': {'median': val}, 'npv_by_weather': [val]}
            return {'solar_equity': fin_eq, self.fin_basis: fin_eq}, None

        res = self._run_with_mocks(base_monthly=base_monthly, dispatch_override=dispatch_override, financial_override=financial_override)
        self.assertIsNotNone(res['recommendation_npv'])
        self.assertIsNotNone(res['recommendation_clipping'])
        self.assertEqual(res['recommendation_npv']['routing_priority'], 'dynamic_reserve')
        self.assertEqual(res['recommendation_clipping']['routing_priority'], 'dynamic_reserve')
        self.assertEqual(res['recommendation']['routing_priority'], 'dynamic_reserve')

    def test_regression_5_negative_npv_excluded_even_year1_positive(self):
        # Negative NPV excluded even Year1 positive
        base_monthly = _synthetic_monthly(clip=10.0, soc=98.0, op_val=50.0)
        cand_monthly = _synthetic_monthly(clip=5.0, soc=98.0, op_val=60.0)

        def financial_override(s, annual, kwp):
            prio = s.get('routing_priority')
            if prio == 'battery_first':
                val = 1000.0
            else:
                val = 800.0
            fin_eq = {'npv': {'median': val}, 'npv_by_weather': [val]}
            return {'solar_equity': fin_eq, self.fin_basis: fin_eq}, None

        res = self._run_with_mocks(base_monthly=base_monthly, cand_monthly=cand_monthly, financial_override=financial_override)
        self.assertIsNone(res['recommendation_npv'])

    def test_profitable_max_clipping_beats_dynamic_without_autarky_guard(self):
        base = _synthetic_monthly(clip=20., soc=98., op_val=50.)
        h13 = _synthetic_monthly(clip=[20.]*3 + [3.6]*3 + [20.]*6,
                                 op_val=[50.]*3 + [60.]*3 + [50.]*6)
        for month in h13[3:6]:
            month['p25_peak_soc_pct'] = month['median_peak_soc_pct'] = 70.
        h12 = _synthetic_monthly(clip=[20.]*2 + [9.05]*4 + [20.]*6,
                                 op_val=[50.]*2 + [60.]*4 + [50.]*6)
        dynamic = _synthetic_monthly(clip=[19.1] + [20.]*11, op_val=51.)

        def dispatch_override(key):
            cfg = dict(key)
            mode = cfg.get('routing_priority')
            monthly = base if mode == 'battery_first' else dynamic
            if mode == 'scheduled_hybrid':
                source = {12: h12, 13: h13}.get(cfg.get('routing_switch_hour'), base)
                start, end = cfg.get('scheduled_start_month'), cfg.get('scheduled_end_month')
                monthly = [source[i] if start <= i + 1 <= end else base[i] for i in range(12)]
            return {'monthly_clipping': deepcopy(monthly)}

        def financial_override(s, annual, kwp):
            mode = s.get('routing_priority')
            delta = (6. if mode == 'dynamic_reserve' else
                     {12: 148., 13: 90.}.get(s.get('routing_switch_hour'), 0.)
                     if mode == 'scheduled_hybrid' else 0.)
            eq = {'npv': {'median': 1000. + delta}, 'npv_by_weather': [1000. + delta]}
            return {'solar_equity': eq, self.fin_basis: eq}, None

        res = self._run_with_mocks(base_monthly=base, dispatch_override=dispatch_override,
                                   financial_override=financial_override)
        clip = res['recommendation_clipping']
        self.assertEqual(clip['routing_switch_hour'], 13)
        self.assertEqual(clip['active_months'], [4, 5, 6])
        self.assertFalse(clip['preserves_autarky_soc'])
        self.assertAlmostEqual(clip['clipping_saved_kwh'], 49.2)
        self.assertEqual(clip['equity_npv_delta'], 90.)
        self.assertEqual(res['recommendation_npv']['routing_switch_hour'], 12)
        r12 = next(r for r in res['rows'] if r.get('routing_switch_hour') == 12)
        dyn = next(r for r in res['rows'] if r.get('routing_priority') == 'dynamic_reserve')
        self.assertAlmostEqual(r12['clipping_saved_kwh'], 43.8)
        self.assertEqual(r12['equity_npv_delta'], 148.)
        self.assertAlmostEqual(dyn['clipping_saved_kwh'], .9)
        self.assertEqual(dyn['equity_npv_delta'], 6.)

    def test_regression_6_energy_keys_distinct_all_four_routing_keys(self):
        # ENERGY_KEYS distinct all 4 routing keys
        routing_keys = ['routing_priority', 'routing_switch_hour', 'scheduled_start_month', 'scheduled_end_month']
        for rk in routing_keys:
            self.assertIn(rk, eng.ENERGY_KEYS)
        self.assertEqual(len(eng.ENERGY_KEYS), len(set(eng.ENERGY_KEYS)))

        cfgs = [
            {'routing_priority': 'battery_first', 'routing_switch_hour': None, 'scheduled_start_month': None, 'scheduled_end_month': None},
            {'routing_priority': 'scheduled_hybrid', 'routing_switch_hour': 10, 'scheduled_start_month': 3, 'scheduled_end_month': 9},
            {'routing_priority': 'scheduled_hybrid', 'routing_switch_hour': 11, 'scheduled_start_month': 3, 'scheduled_end_month': 9},
            {'routing_priority': 'scheduled_hybrid', 'routing_switch_hour': 10, 'scheduled_start_month': 4, 'scheduled_end_month': 9},
            {'routing_priority': 'scheduled_hybrid', 'routing_switch_hour': 10, 'scheduled_start_month': 3, 'scheduled_end_month': 10},
            {'routing_priority': 'dynamic_reserve', 'routing_switch_hour': None, 'scheduled_start_month': None, 'scheduled_end_month': None},
        ]
        keys = []
        for cfg in cfgs:
            s_test = dict(self.settings, **cfg)
            k = tuple((k_name, eng.freeze(s_test.get(k_name))) for k_name in eng.ENERGY_KEYS)
            keys.append(k)
        self.assertEqual(len(keys), len(set(keys)))

    def test_multiweather_energy_statistics_and_autarky_ratio_of_means(self):
        def _make_year(annual_load, annual_imp, annual_exp, clip_kwh=120.0, soc=98.0, op_val=50.0):
            return [
                {
                    'load_kwh': float(annual_load) / 12.0,
                    'mean_peak_soc_pct': soc,
                    'median_peak_soc_pct': soc,
                    'p25_peak_soc_pct': soc,
                    'clipped_kwh': float(clip_kwh) / 12.0,
                    'grid_import_kwh': float(annual_imp) / 12.0,
                    'grid_export_kwh': float(annual_exp) / 12.0,
                    'export_price_weighted_kwh': float(annual_exp) / 12.0,
                    'operating_value_eur': op_val,
                }
                for _ in range(12)
            ]

        # 3 weather years where varying loads ensure ratio of means differs from
        # both the mean of individual autarkies and the median of individual autarkies.
        # Year 0: load=200, imp=100 -> autarky=0.50, exp=50
        # Year 1: load=1000, imp=100 -> autarky=0.90, exp=100
        # Year 2: load=800, imp=400 -> autarky=0.50, exp=450
        base_weather = [
            _make_year(200.0, 100.0, 50.0, clip_kwh=120.0, op_val=50.0),
            _make_year(1000.0, 100.0, 100.0, clip_kwh=240.0, op_val=50.0),
            _make_year(800.0, 400.0, 450.0, clip_kwh=360.0, op_val=50.0),
        ]
        base_rep = _make_year(666.67, 200.0, 200.0, clip_kwh=240.0, op_val=50.0)

        # Candidate with clipping reduction and value improvement across all 3 years
        # Year 0: load=200, imp=60 -> autarky=0.70, exp=80
        # Year 1: load=1000, imp=80 -> autarky=0.92, exp=120
        # Year 2: load=800, imp=200 -> autarky=0.75, exp=500
        cand12_weather = [
            _make_year(200.0, 60.0, 80.0, clip_kwh=24.0, op_val=60.0),
            _make_year(1000.0, 80.0, 120.0, clip_kwh=48.0, op_val=60.0),
            _make_year(800.0, 200.0, 500.0, clip_kwh=72.0, op_val=60.0),
        ]
        cand12_rep = _make_year(666.67, 113.33, 233.33, clip_kwh=48.0, op_val=60.0)

        def dispatch_override(key):
            cfg = dict(key)
            if cfg.get('routing_priority') == 'battery_first':
                return {'monthly_clipping': deepcopy(base_rep), 'weather_monthly_clipping': deepcopy(base_weather)}
            if cfg.get('routing_priority') == 'dynamic_reserve':
                return None
            h = cfg.get('routing_switch_hour')
            if h == 12:
                return {'monthly_clipping': deepcopy(cand12_rep), 'weather_monthly_clipping': deepcopy(cand12_weather)}
            return {'monthly_clipping': deepcopy(base_rep), 'weather_monthly_clipping': deepcopy(base_weather)}

        res = self._run_with_mocks(dispatch_override=dispatch_override)
        base_row = res['rows'][0]
        self.assertEqual(base_row['status'], 'baseline')

        # Baseline imports: [100.0, 100.0, 400.0] -> Mean = 200.0, Median = 100.0
        self.assertEqual(base_row['grid_import_kwh'], 200.0)
        self.assertNotEqual(base_row['grid_import_kwh'], 100.0)
        self.assertEqual(base_row['grid_import_p10_kwh'], round(float(np.percentile([100.0, 100.0, 400.0], 10)), 2))
        self.assertEqual(base_row['grid_import_p90_kwh'], round(float(np.percentile([100.0, 100.0, 400.0], 90)), 2))

        # Baseline exports: [50.0, 100.0, 450.0] -> Mean = 200.0, Median = 100.0
        self.assertEqual(base_row['grid_export_kwh'], 200.0)
        self.assertNotEqual(base_row['grid_export_kwh'], 100.0)

        # Baseline loads: [200.0, 1000.0, 800.0], Total load = 2000.0, Total import = 600.0
        # Ratio of means autarky: 1.0 - 600.0 / 2000.0 = 0.7000
        # Individual autarkies: [0.50, 0.90, 0.50]
        # Mean of individual autarkies = 1.9 / 3 = 0.6333
        # Median of individual autarkies = 0.50
        self.assertEqual(base_row['autarky'], 0.7000)
        self.assertNotEqual(base_row['autarky'], round(float(np.mean([0.5, 0.9, 0.5])), 4))
        self.assertNotEqual(base_row['autarky'], round(float(np.median([0.5, 0.9, 0.5])), 4))
        self.assertEqual(base_row['autarky_p10'], round(float(np.percentile([0.5, 0.9, 0.5], 10)), 4))
        self.assertEqual(base_row['autarky_p90'], round(float(np.percentile([0.5, 0.9, 0.5], 90)), 4))

        # Energy metadata
        self.assertEqual(base_row['energy_statistic'], 'weather_mean')
        self.assertEqual(base_row['autarky_basis'], 'one_minus_mean_import_over_mean_load')
        self.assertEqual(base_row['soc_statistic'], 'p25_daily_peak_all_weather')
        self.assertEqual(base_row['statistic_basis'], 'paired_annual_differences_all_weather')

        # Candidate row verification (12h)
        r12 = next(r for r in res['rows'] if r.get('routing_switch_hour') == 12 and r.get('status') == 'feasible')
        # Cand imports: [60.0, 80.0, 200.0] -> Mean = 340.0 / 3.0, Median = 80.0
        self.assertAlmostEqual(r12['grid_import_kwh'], 340.0 / 3.0, delta=1e-8)
        self.assertNotEqual(r12['grid_import_kwh'], 80.0)
        self.assertEqual(r12['grid_import_p10_kwh'], round(float(np.percentile([60.0, 80.0, 200.0], 10)), 2))
        self.assertEqual(r12['grid_import_p90_kwh'], round(float(np.percentile([60.0, 80.0, 200.0], 90)), 2))
        # Cand exports: [80.0, 120.0, 500.0] -> Mean = 700.0 / 3.0, Median = 120.0
        self.assertAlmostEqual(r12['grid_export_kwh'], 700.0 / 3.0, delta=1e-8)
        self.assertNotEqual(r12['grid_export_kwh'], 120.0)
        # Cand autarky: 1.0 - (340.0 / 2000.0) = 0.8300
        # Individual: [0.70, 0.92, 0.75] -> Mean = 0.7900, Median = 0.7500
        self.assertAlmostEqual(r12['autarky'], 0.83, delta=1e-8)
        self.assertAlmostEqual(base_row['clipped_kwh'] - r12['clipped_kwh'],
                               r12['clipping_saved_kwh'], delta=1e-8)
        self.assertNotEqual(r12['autarky'], round(float(np.mean([0.70, 0.92, 0.75])), 4))
        self.assertNotEqual(r12['autarky'], round(float(np.median([0.70, 0.92, 0.75])), 4))
        self.assertEqual(r12['autarky_p10'], round(float(np.percentile([0.70, 0.92, 0.75], 10)), 4))
        self.assertEqual(r12['autarky_p90'], round(float(np.percentile([0.70, 0.92, 0.75], 90)), 4))
        self.assertEqual(r12['energy_statistic'], 'weather_mean')
        self.assertEqual(r12['autarky_basis'], 'one_minus_mean_import_over_mean_load')

        # Zero demand handling:
        # 1. Partial zero load (one weather year has zero load)
        partial_zero_weather = [
            _make_year(0.0, 100.0, 50.0, clip_kwh=120.0, op_val=50.0),
            _make_year(1000.0, 200.0, 100.0, clip_kwh=120.0, op_val=50.0),
        ]
        res_partial = self._run_with_mocks(
            dispatch_override=lambda key: {
                'monthly_clipping': deepcopy(partial_zero_weather[0]),
                'weather_monthly_clipping': deepcopy(partial_zero_weather),
            }
        )
        base_part = res_partial['rows'][0]
        self.assertEqual(base_part['autarky'], 0.7000)
        self.assertEqual(base_part['autarky_p10'], round(float(np.percentile([0.0, 0.8], 10)), 4))
        self.assertEqual(base_part['autarky_p90'], round(float(np.percentile([0.0, 0.8], 90)), 4))

        # 2. Complete zero load (all weather years have zero load)
        all_zero_weather = [
            _make_year(0.0, 100.0, 50.0, clip_kwh=120.0, op_val=50.0),
            _make_year(0.0, 200.0, 100.0, clip_kwh=120.0, op_val=50.0),
        ]
        res_zero = self._run_with_mocks(
            dispatch_override=lambda key: {
                'monthly_clipping': deepcopy(all_zero_weather[0]),
                'weather_monthly_clipping': deepcopy(all_zero_weather),
            }
        )
        base_all_zero = res_zero['rows'][0]
        self.assertEqual(base_all_zero['autarky'], 0.0)
        self.assertEqual(base_all_zero['autarky_p10'], 0.0)
        self.assertEqual(base_all_zero['autarky_p90'], 0.0)
        self.assertEqual(base_all_zero['grid_import_kwh'], 150.0)
        self.assertEqual(base_all_zero['grid_export_kwh'], 75.0)

    def test_monthly_row_immutability_and_cache_isolation(self):
        raw_weather = [
            _synthetic_monthly(clip=10.0, soc=98.0, op_val=50.0),
            _synthetic_monthly(clip=8.0, soc=98.0, op_val=55.0),
        ]
        raw_snapshot = deepcopy(raw_weather)
        res1 = self._run_with_mocks(
            dispatch_override=lambda key: {
                'monthly_clipping': raw_weather[0],
                'weather_monthly_clipping': raw_weather,
            }
        )
        for w_idx, year in enumerate(raw_weather):
            self.assertEqual(year, raw_snapshot[w_idx])
            for m in year:
                self.assertNotIn('operating_value_eur', m)
                self.assertNotIn('lifetime_operating_value_eur', m)
        res1['rows'][0]['grid_import_kwh'] = 999999.0
        res2 = switch_hour_tradeoff(self.settings)
        self.assertNotEqual(res2['rows'][0]['grid_import_kwh'], 999999.0)
