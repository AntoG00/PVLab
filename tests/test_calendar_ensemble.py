import test_support
from unittest.mock import patch
import numpy as np
import pytest
import feasibility.engine as e

@pytest.fixture
def ensemble_mock():
    pv_v, soc_v, imp_v, exp_v, dir_v = [1.0, 2.0, 9.0], [1.0, 2.0, 9.0], [1.0, 0.0, 0.0], [0.0, 0.0, 7.0], [1.0, 2.0, 2.0]
    traces = []
    for i in range(3):
        tr = np.zeros((8760, 8), dtype=float)
        tr[:, 0], tr[:, 1], tr[:, 2], tr[:, 3], tr[:, 4], tr[:, 5] = pv_v[i], 2.0, imp_v[i], exp_v[i], soc_v[i], dir_v[i]
        traces.append(tr)
    annual = [{'weather_year': 2000 + i, 'pv_input_kwh': 8760 * pv_v[i], 'pv_kwh': 8760 * pv_v[i],
               'load_kwh': 8760 * 2.0, 'grid_import_kwh': 8760 * imp_v[i], 'grid_export_kwh': 8760 * exp_v[i],
               'direct_kwh': 8760 * dir_v[i], 'battery_loss_kwh': 0.0, 'end_soc_kwh': soc_v[i]} for i in range(3)]
    mock_data = {'traces': traces, 'annual': annual, 'picks': {'low': 0, 'median': 1, 'high': 2},
                 'mapped_data': {'mapped_pv': [np.full(8760, pv_v[i]) for i in range(3)],
                                 'mapped_load': [np.full(8760, 2.0) for i in range(3)],
                                 'target_months': np.zeros(8760, int), 'target_std_hours': np.zeros(8760, int)}}
    with patch('feasibility.engine._forecast_2027_runs', return_value=mock_data), \
         patch('feasibility.engine._trace_routing_telemetry', return_value=(np.zeros(8760), np.zeros(8760))), \
         patch('feasibility.engine.soc_ceiling_series', return_value=np.zeros(8760)):
        yield

def test_dayhour_forecast_representative_mean(ensemble_mock):
    s = e.validate({'load_scenario': 'baseline', 'planning_heat_pump': False, 'battery_kwh': 10.0})
    res = e.dayhour_forecast(s, '2027-06-21', hour=12, representative='mean')
    assert res['representative'] == 'mean'
    assert res['aggregation'] == 'weather_mean'
    assert res['weather_count'] == 3
    assert res['weather_year'] is None
    assert all(h['pv_kwh'] == pytest.approx(4.0) and h['soc_end_kwh'] == pytest.approx(4.0) for h in res['hours'])
    assert res['hours'][12]['pv_kwh'] != 2.0 and res['hours'][12]['soc_end_kwh'] != 2.0
    assert res['totals']['pv_kwh'] == pytest.approx(96.0)
    assert res['selected_hour']['hour'] == 12
    assert res['selected_hour']['pv_kwh'] == pytest.approx(4.0) and res['selected_hour']['soc_end_kwh'] == pytest.approx(4.0)
    p10, p90 = float(np.percentile([1.0, 2.0, 9.0], 10)), float(np.percentile([1.0, 2.0, 9.0], 90))
    assert res['uncertainty_by_hour'][12]['pv_kwh']['p10'] == pytest.approx(p10)
    assert res['uncertainty_by_hour'][12]['pv_kwh']['p90'] == pytest.approx(p90)
    assert res['uncertainty_by_hour'][12]['soc_end_kwh']['p10'] == pytest.approx(p10)
    assert res['uncertainty_by_hour'][12]['soc_end_kwh']['p90'] == pytest.approx(p90)

def test_dayhour_forecast_monthly_representative_mean(ensemble_mock):
    s = e.validate({'load_scenario': 'baseline', 'planning_heat_pump': False, 'battery_kwh': 10.0})
    res = e.dayhour_forecast_monthly(s, 1, hour=12, representative='mean')
    assert res['representative'] == 'mean'
    assert res['aggregation'] == 'weather_mean'
    assert res['weather_count'] == 3
    assert res['weather_year'] is None
    assert res['period'] == 'month' and res['month'] == 1 and res['days_in_month'] == 31 and res['month_name'] == 'January'
    assert all(h['pv_kwh'] == pytest.approx(4.0) and h['soc_end_kwh'] == pytest.approx(4.0) for h in res['hours'])
    assert res['hours'][12]['pv_kwh'] != 2.0 and res['hours'][12]['soc_end_kwh'] != 2.0
    assert res['totals']['pv_kwh'] == pytest.approx(31 * 24 * 4.0)
    assert res['daily_averages']['pv_kwh'] == pytest.approx(96.0)
    assert res['hourly_averages']['pv_kwh'] == pytest.approx(4.0)
    assert res['selected_hour']['hour'] == 12
    assert res['selected_hour']['soc_end_kwh'] == pytest.approx(4.0)
    p10, p90 = float(np.percentile([1.0, 2.0, 9.0], 10)), float(np.percentile([1.0, 2.0, 9.0], 90))
    assert res['uncertainty_by_hour'][12]['pv_kwh']['p10'] == pytest.approx(p10)
    assert res['uncertainty_by_hour'][12]['pv_kwh']['p90'] == pytest.approx(p90)

def test_dayhour_forecast_explicit_median_preserved(ensemble_mock):
    s = e.validate({'load_scenario': 'baseline', 'planning_heat_pump': False, 'battery_kwh': 10.0})
    day_res = e.dayhour_forecast(s, '2027-06-21', hour=12, representative='median')
    assert day_res['representative'] == 'median'
    assert day_res['weather_year'] == 2001
    assert day_res['hours'][12]['pv_kwh'] == pytest.approx(2.0)
    assert day_res['hours'][12]['soc_end_kwh'] == pytest.approx(2.0)

    month_res = e.dayhour_forecast_monthly(s, 1, hour=12, representative='median')
    assert month_res['representative'] == 'median'
    assert month_res['weather_year'] == 2001
    assert month_res['hours'][12]['pv_kwh'] == pytest.approx(2.0)
    assert month_res['hours'][12]['soc_end_kwh'] == pytest.approx(2.0)
