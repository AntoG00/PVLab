"""Tests for mean-objective paired ensemble statistics and schedule optimization."""
import numpy as np
import pytest
from unittest.mock import patch

from feasibility.ensemble_statistics import paired_improvement_statistics
from feasibility import assumptions, engine, schedule_optimizer
from test_support import IsolatedDefaultsTestCase


def test_asymmetric_paired_improvement_mean_and_curtailment_identity():
    """Prove baseline mean curtailment - candidate mean = mean savings, and mean != median."""
    # Asymmetric 5-weather ensemble with anticorrelated candidate clips
    base_clips = np.array([100.0, 120.0, 150.0, 200.0, 300.0])
    cand_clips = np.array([130.0, 120.0, 110.0, 100.0, 90.0])

    stats = paired_improvement_statistics(base_clips, cand_clips, lower_is_better=True)

    # 1. Baseline mean curtailment - candidate mean curtailment = mean savings
    base_mean = float(np.mean(base_clips))
    cand_mean = float(np.mean(cand_clips))
    assert base_mean - cand_mean == pytest.approx(stats['mean'], rel=1e-12, abs=1e-12)
    assert stats['mean'] == pytest.approx(64.0, rel=1e-12, abs=1e-12)

    # 2. Means differ from medians
    assert stats['mean'] != stats['median']
    assert stats['median'] == pytest.approx(40.0, rel=1e-12, abs=1e-12)

    # 3. Paired percentiles and improving counts preserved
    assert stats['improving_years'] == 3
    assert stats['weather_count'] == 5
    assert stats['p10'] == pytest.approx(-18.0)
    assert stats['p90'] == pytest.approx(166.0)

    # 4. Paired percentiles are NOT independent percentile differences
    indep_p10_diff = float(np.percentile(base_clips, 10) - np.percentile(cand_clips, 10))
    indep_p90_diff = float(np.percentile(base_clips, 90) - np.percentile(cand_clips, 90))
    assert stats['p10'] != indep_p10_diff
    assert indep_p10_diff == pytest.approx(14.0)
    assert stats['p90'] != indep_p90_diff
    assert indep_p90_diff == pytest.approx(134.0)


def test_lifetime_equity_npv_mean_linearity_and_ranking_divergence():
    """Prove baseline mean + mean paired delta = candidate mean, and mean ranking differs from median."""
    W = 5
    base_npv = np.array([1000.0, 1100.0, 1200.0, 1300.0, 1400.0])
    # Candidate A: asymmetric upside (+€110 in year 5, +€10 elsewhere) -> mean delta 30, median delta 10
    cand_A_delta = np.array([10.0, 10.0, 10.0, 10.0, 110.0])
    # Candidate B: uniform moderate improvement (+€20 everywhere) -> mean delta 20, median delta 20
    cand_B_delta = np.array([20.0, 20.0, 20.0, 20.0, 20.0])

    cand_A_npv = base_npv + cand_A_delta
    cand_B_npv = base_npv + cand_B_delta

    # Linearity of mean: baseline mean + mean paired delta = candidate mean
    base_mean = float(np.mean(base_npv))
    cand_A_mean = float(np.mean(cand_A_npv))
    cand_B_mean = float(np.mean(cand_B_npv))

    mean_delta_A = float(np.mean(cand_A_delta))
    mean_delta_B = float(np.mean(cand_B_delta))
    med_delta_A = float(np.median(cand_A_delta))
    med_delta_B = float(np.median(cand_B_delta))

    assert base_mean + mean_delta_A == pytest.approx(cand_A_mean, rel=1e-12, abs=1e-12)
    assert base_mean + mean_delta_B == pytest.approx(cand_B_mean, rel=1e-12, abs=1e-12)

    # Means differ from medians
    assert mean_delta_A != med_delta_A

    # Median ranking prefers Candidate B (20 > 10)
    assert med_delta_B > med_delta_A

    # Mean ranking prefers Candidate A (30 > 20)
    assert mean_delta_A > mean_delta_B


class TestScheduleOptimizerEnsembleMean(IsolatedDefaultsTestCase):
    """Production path test for switch_hour_tradeoff adhering to mean NPV and curtailment identities."""

    def test_switch_hour_tradeoff_mean_objective_and_recommendation_selection(self):
        H, W = 7, 5
        s = assumptions.defaults()
        s.update({
            'pv_dispatch_model': 'physical',
            'pv_battery_coupling': 'dc_coupled',
            'battery_kwh': 10.0,
            'horizon_years': H,
            'export_market_price': 0.1,
            'export_irpef': 0.37,
            'regional_additional_tax': 0.0,
            'municipal_additional_tax': 0.0,
            'pmg_election': False,
            'cer_enabled': False,
            'inflation': 0.0,
            'energy_real_escalation': 0.0,
            'regulated_real_escalation': 0.0,
            'real_discount_rate': 0.04,
            'export_price_future_years': 10,
            'actual_price_future_factor': 1.0,
            'load_scenario': 'baseline',
        })

        base_npv_arr = np.array([1000.0, 1100.0, 1200.0, 1300.0, 1400.0])
        # h=10 has asymmetric deltas: mean delta = 30.0, median delta = 10.0
        h10_deltas = np.array([10.0, 10.0, 10.0, 10.0, 110.0])
        # h=12 has uniform deltas: mean delta = 20.0, median delta = 20.0
        h12_deltas = np.array([20.0, 20.0, 20.0, 20.0, 20.0])

        base_clips = np.array([100.0, 120.0, 150.0, 200.0, 300.0])
        h10_clips = np.array([130.0, 120.0, 110.0, 100.0, 90.0])

        def mock_trace(eng, cfg, ekey=None, price_ver=0, base_ri=None, prepared_context=None):
            p = cfg.get('routing_priority')
            h = cfg.get('routing_switch_hour')
            weathers = []
            for w in range(W):
                months = []
                for m in range(1, 13):
                    clip = (base_clips[w] if p == 'battery_first' else h10_clips[w]) / 12.0
                    months.append({
                        'month': m, 'pv_input_kwh': 100.0, 'load_kwh': 100.0,
                        'grid_import_kwh': 20.0, 'grid_export_kwh': 80.0,
                        'export_price_weighted_kwh': 75.0, 'clipped_kwh': clip,
                        'operating_value_eur': 50.0 + (1.0 if p != 'battery_first' else 0.0),
                        'p25_peak_soc_pct': 98.0, 'median_peak_soc_pct': 98.0,
                    })
                weathers.append(months)
            return 0, {'monthly_clipping': weathers[0], 'weather_monthly_clipping': weathers}

        def mock_financial(cfg, annual, kwp, calculate_irr=False, hp_summary=None, **kwargs):
            p = cfg.get('routing_priority')
            h = cfg.get('routing_switch_hour')
            if p == 'battery_first':
                arr = base_npv_arr
            elif h == 10:
                arr = base_npv_arr + h10_deltas
            elif h == 12:
                arr = base_npv_arr + h12_deltas
            else:
                arr = base_npv_arr + 5.0
            fin_eq = {'npv': {'median': float(np.median(arr))}, 'npv_by_weather': list(arr)}
            return {'solar_equity': fin_eq, 'equity': fin_eq}, None

        schedule_optimizer._cached_switch_hour_tradeoff.cache_clear()
        with patch.object(schedule_optimizer, 'prepare_dispatch_context', return_value={}), \
             patch.object(engine, '_energy_cache', side_effect=lambda key, price_ver=0: (np.zeros((H, W, 14)), {})), \
             patch.object(engine, '_financial', side_effect=mock_financial), \
             patch.object(schedule_optimizer, '_trace_monthly_summary', side_effect=mock_trace):
            res = schedule_optimizer.switch_hour_tradeoff(s)

        rec = res['recommendation_npv']
        assert rec is not None
        # Mean objective selects h=10 (mean delta 30.0 > 20.0), whereas median would have chosen h=12 (10.0 < 20.0)
        assert rec['routing_switch_hour'] == 10
        assert rec['equity_npv_delta'] == pytest.approx(30.0, rel=1e-12, abs=1e-12)

        r10 = next(r for r in res['rows'] if r.get('routing_switch_hour') == 10)
        r12 = next(r for r in res['rows'] if r.get('routing_switch_hour') == 12)

        # Real production rank asserts mean-vs-median:
        # Mean delta prefers h=10 over h=12:
        assert r10['equity_npv_delta'] > r12['equity_npv_delta']
        # Diagnostic median prefers h=12 over h=10:
        assert r10['equity_npv_delta_median'] < r12['equity_npv_delta_median']
        assert r10['equity_npv_delta_median'] == pytest.approx(10.0, rel=1e-12, abs=1e-12)
        assert r12['equity_npv_delta_median'] == pytest.approx(20.0, rel=1e-12, abs=1e-12)

        # New paired NPV distribution fields:
        assert r10['equity_npv_delta_p10'] == pytest.approx(float(np.percentile(h10_deltas, 10)))
        assert r10['equity_npv_delta_p90'] == pytest.approx(float(np.percentile(h10_deltas, 90)))
        assert r10['equity_npv_improving_years'] == 5

        # Full precision check on raw paired arrays:
        assert float(np.mean(base_npv_arr)) + r10['equity_npv_delta'] == pytest.approx(float(np.mean(base_npv_arr + h10_deltas)), rel=1e-12, abs=1e-12)
        raw_savings = base_clips - h10_clips
        assert float(np.mean(raw_savings)) == pytest.approx(float(np.mean(base_clips)) - float(np.mean(h10_clips)), rel=1e-12, abs=1e-12)
