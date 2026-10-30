"""Tests for batched weather year and monthly dispatch parity and forecast consistency."""
from feasibility.runtime_context import current_runtime
import test_support  # Import test_support before engine
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import numpy as np
import pytest
import feasibility.engine as e
import feasibility.physical_dispatch as pd

def _settings(**kw):
    cfg = {'pv_dispatch_model': 'physical', 'routing_priority': 'battery_first'}
    cfg.update(kw)
    return e.validate(cfg)

def _calendar(is_leap=False):
    ml = [31, 29 if is_leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    days = 366 if is_leap else 365
    m = np.repeat(np.repeat(np.arange(1, 13), ml), 24).astype(np.int64)
    h = np.tile(np.arange(24), days).astype(np.int64)
    return m, h, days * 24

def _synthetic(weather, t_len, seed=42):
    rng = np.random.default_rng(seed)
    h = np.tile(np.arange(24), int(np.ceil(t_len / 24)))[:t_len]
    solar = 5.0 * np.maximum(0.0, np.sin((h - 6) * np.pi / 12.0))
    pv = np.array([np.maximum(0.0, solar * (1.0 + 0.3 * rng.uniform(-1, 1, t_len))) for _ in range(weather)])
    load = np.array([0.3 + 0.2 * np.sin(h / 3.0) + rng.uniform(0, 0.1, t_len) for _ in range(weather)])
    pzo = 0.10 + 0.05 * np.cos(h * np.pi / 12.0) + rng.uniform(-0.01, 0.01, (weather, t_len))
    return pv, load, pzo

def _serial(pv, load, cap, ch, dis, eff, inv, soc, price, valid, dc, phys, m, h):
    tots, trs = [], []
    for w in range(len(pv)):
        tot, tr = e.dispatch_year(pv[w], load[w], cap, ch, dis, eff, inv, soc, price, valid, dc, phys, m, h, True)
        tots.append(tot); trs.append(tr)
    return (np.zeros((0, 14)), np.zeros((0, pv.shape[1], 8))) if len(pv) == 0 else (np.array(tots), np.array(trs))

def _serial_monthly(pv, load, pzo, bins, pv_charge_loss, cap, ch, dis, eff, inv, soc, price, valid, dc, phys, m, h):
    W = len(pv)
    tots, monthly = np.zeros((W, 14)), np.zeros((W, 12, 5))
    pzo_2d = np.broadcast_to(pzo, pv.shape)
    for w in range(W):
        tot, tr = e.dispatch_year(pv[w], load[w], cap, ch, dis, eff, inv, soc, price, valid, dc, phys, m, h, True)
        tots[w] = tot
        cols = [pv[w], tr[:, 2], tr[:, 3], tr[:, 3] * pzo_2d[w], np.maximum(pv[w] - (tr[:, 0] - pv_charge_loss * tr[:, 6]), 0.0)]
        for i, col in enumerate(cols):
            monthly[w, :, i] = np.bincount(bins, weights=col, minlength=12)[:12]
    return tots, monthly

def _assert_match(pv, load, cap, ch, dis, eff, inv, soc, price, valid, dc, phys, m, h):
    b_tot, b_tr = e.dispatch_weather_years(pv, load, cap, ch, dis, eff, inv, soc, price, valid, dc, phys, m, h)
    s_tot, s_tr = _serial(pv, load, cap, ch, dis, eff, inv, soc, price, valid, dc, phys, m, h)
    np.testing.assert_array_equal(b_tot, s_tot); np.testing.assert_array_equal(b_tr, s_tr)

def test_batch_parity_matrix():
    m, h, t_len = _calendar(False)
    pv, load, pzo = _synthetic(3, t_len, seed=123)
    valid = np.ones(t_len, dtype=bool); valid[100:150] = False
    price = pzo[0]
    # Physical DC, scheduled wraparound months, nonzero battery, initial SOC, valid gaps
    s_dc = _settings(pv_battery_coupling='dc_coupled', routing_priority='scheduled_hybrid',
                     scheduled_start_month=10, scheduled_end_month=3)
    phys_dc = pd.physical_parameters(s_dc); assert phys_dc[0] == 1.0
    _assert_match(pv, load, 8.0, 3.0, 3.0, 0.95, 4.0, 2.5, price, valid, True, phys_dc, m, h)
    # Physical AC, full-year scheduled, zero battery, initial SOC 0
    s_ac = _settings(pv_battery_coupling='ac_equivalent', routing_priority='scheduled_hybrid',
                     scheduled_start_month=1, scheduled_end_month=12)
    phys_ac = pd.physical_parameters(s_ac); assert phys_ac[0] == 1.0
    _assert_match(pv, load, 0.0, 0.0, 0.0, 0.95, 4.0, 0.0, price, np.ones(t_len, bool), False, phys_ac, m, h)
    # Legacy 7-tuple parameters with None calendars
    _assert_match(pv, load, 5.0, 2.5, 2.5, 0.9, 3.0, 1.0, price, np.ones(t_len, bool), False, (0., 1., 1., 1., 1., 0., 1.), None, None)

def test_leap_year_19_weather_full_year():
    m, h, t_len = _calendar(True); assert t_len == 8784
    pv, load, pzo = _synthetic(19, t_len, seed=456)
    _assert_match(pv, load, 10.0, 3.3, 3.3, 0.95, 5.0, 0.0, pzo[0], np.ones(t_len, bool), True, pd.physical_parameters(_settings()), m, h)

def test_readonly_inputs_unchanged():
    m, h, t_len = _calendar(False)
    pv, load, pzo = _synthetic(2, t_len, seed=789)
    valid, price = np.ones(t_len, dtype=bool), pzo[0]
    orig = (pv.copy(), load.copy(), price.copy())
    for a in (pv, load, price, valid, m, h):
        a.flags.writeable = False
    b_tot, _ = e.dispatch_weather_years(pv, load, 5.0, 2.0, 2.0, 0.95, 3.0, 0.0, price, valid, False, pd.physical_parameters(_settings()), m, h)
    assert b_tot.shape == (2, 14)
    np.testing.assert_array_equal(pv, orig[0]); np.testing.assert_array_equal(load, orig[1]); np.testing.assert_array_equal(price, orig[2])

def test_empty_weather_stack():
    b_tot, b_tr = e.dispatch_weather_years(np.zeros((0, 48)), np.zeros((0, 48)), 5.0, 2.0, 2.0, 0.95, 3.0, 0.0, np.ones(48), np.ones(48, bool), False)
    assert b_tot.shape == (0, 14) and b_tr.shape == (0, 48, 8)

def test_malformed_dimensions_raise():
    args = (5.0, 2.0, 2.0, 0.95, 3.0, 0.0, np.ones(24), np.ones(24, bool))
    with pytest.raises(ValueError, match="2D"):
        e.dispatch_weather_years(np.zeros(24), np.zeros((1, 24)), *args)
    with pytest.raises(ValueError, match="2D"):
        e.dispatch_weather_years(np.zeros((1, 24)), np.zeros((1, 1, 24)), *args)
    with pytest.raises(ValueError, match="shapes must match"):
        e.dispatch_weather_years(np.zeros((2, 24)), np.zeros((3, 24)), *args)
    with pytest.raises(ValueError, match="price_shape and valid lengths"):
        e.dispatch_weather_years(np.zeros((1, 24)), np.zeros((1, 24)), 5.0, 2.0, 2.0, 0.95, 3.0, 0.0, np.ones(20), np.ones(24, bool))
    with pytest.raises(ValueError, match="month length"):
        e.dispatch_weather_years(np.zeros((1, 24)), np.zeros((1, 24)), *args, month=np.ones(20))
    with pytest.raises(ValueError, match="hour length"):
        e.dispatch_weather_years(np.zeros((1, 24)), np.zeros((1, 24)), *args, hour=np.ones(20))

def test_concurrent_batched_calls_parity():
    m, h, t_len = _calendar(False)
    pv1, load1, p1 = _synthetic(4, t_len, seed=101)
    pv2, load2, p2 = _synthetic(4, t_len, seed=202)
    valid, phys = np.ones(t_len, dtype=bool), pd.physical_parameters(_settings())
    def _call(pv, load, p):
        return e.dispatch_weather_years(pv, load, 6.0, 2.5, 2.5, 0.95, 3.5, 1.0, p, valid, True, phys, m, h)
    with ThreadPoolExecutor(max_workers=2) as ex:
        f1, f2 = ex.submit(_call, pv1, load1, p1[0]), ex.submit(_call, pv2, load2, p2[0])
        r1_tot, r1_tr = f1.result(); r2_tot, r2_tr = f2.result()
    s1_tot, s1_tr = _serial(pv1, load1, 6.0, 2.5, 2.5, 0.95, 3.5, 1.0, p1[0], valid, True, phys, m, h)
    s2_tot, s2_tr = _serial(pv2, load2, 6.0, 2.5, 2.5, 0.95, 3.5, 1.0, p2[0], valid, True, phys, m, h)
    np.testing.assert_array_equal(r1_tot, s1_tot); np.testing.assert_array_equal(r1_tr, s1_tr)
    np.testing.assert_array_equal(r2_tot, s2_tot); np.testing.assert_array_equal(r2_tr, s2_tr)

def test_forecast_list_order_picks_parity():
    m, h, t_len = _calendar(False)
    years = [2005 + i for i in range(5)]
    pv, load, _ = _synthetic(len(years), t_len, seed=333)
    s = _settings()
    mock_mapped = {
        'mapped_pv': pv, 'mapped_load': load, 'target_len': t_len, 'forecast_year': 2027,
        'is_leap': False, 'target_months': m, 'target_std_hours': h,
        'mappings': [(np.arange(t_len),) for _ in range(len(years))],
    }
    with patch('feasibility.engine.data', return_value={'years': [str(y) for y in years]}),          patch('feasibility.engine._forecast_mapped_series', return_value=mock_mapped):
        batch_res = e._forecast_runs_cached(current_runtime().key, e.freeze(s))
    ref_tot, ref_tr = _serial(pv, load, s['battery_kwh'], s['charge_kw'], s['discharge_kw'],
                              np.sqrt(s['roundtrip_efficiency']), s['inverter_kw'], 0.0,
                              np.ones(t_len), np.ones(t_len, bool), s['pv_battery_coupling'] == 'dc_coupled',
                              pd.physical_parameters(s), m, h)
    assert isinstance(batch_res['traces'], list)
    np.testing.assert_array_equal(np.array(batch_res['traces']), ref_tr)
    assert batch_res['source_indices'] == [list(range(t_len)) for _ in range(len(years))]
    ref_annual = []
    for wi, y in enumerate(years):
        tot = ref_tot[wi]
        ref_annual.append(dict(
            weather_year=y, pv_kwh=float(tot[1]), pv_input_kwh=float(pv[wi].sum()),
            load_kwh=float(load[wi].sum()), direct_kwh=float(tot[2]), charge_input_kwh=float(tot[3]),
            discharge_delivered_kwh=float(tot[4]), grid_import_kwh=float(tot[5]),
            grid_export_kwh=float(tot[6]), battery_loss_kwh=float(tot[7]), end_soc_kwh=float(tot[8]),
            clipped_kwh=float(tot[9]), max_balance_error_kwh=float(tot[13])
        ))
    assert batch_res['annual'] == ref_annual
    yields = np.array([r['pv_kwh'] for r in ref_annual])
    order = np.argsort(yields)
    expected_picks = {
        'low': int(order[np.argmin(abs(yields[order] - np.percentile(yields, 10)))]),
        'median': int(order[np.argmin(abs(yields[order] - np.percentile(yields, 50)))]),
        'high': int(order[np.argmin(abs(yields[order] - np.percentile(yields, 90)))]),
    }
    assert batch_res['picks'] == expected_picks

@pytest.mark.parametrize('is_leap,dc,cap', [(False, True, 10.0), (True, False, 0.0)])
def test_dispatch_weather_monthly_parity(is_leap, dc, cap):
    dwm = e.dispatch_weather_monthly
    m, h, t_len = _calendar(is_leap)
    bins = (m - 1).astype(np.int64)
    pv, load, pzo = _synthetic(3, t_len, seed=555)
    valid = np.ones(t_len, dtype=bool); valid[200:250] = False
    s = _settings(pv_battery_coupling='dc_coupled' if dc else 'ac_equivalent', battery_kwh=cap)
    phys = pd.physical_parameters(s)
    b_tot, b_m = dwm(pv, load, pzo, bins, 0.05, cap, 3.0, 3.0, 0.95, 4.0, 1.0, pzo[0], valid, dc, phys, m, h)
    s_tot, s_m = _serial_monthly(pv, load, pzo, bins, 0.05, cap, 3.0, 3.0, 0.95, 4.0, 1.0, pzo[0], valid, dc, phys, m, h)
    np.testing.assert_array_equal(b_tot, s_tot)
    np.testing.assert_array_equal(b_m, s_m)

def test_dispatch_weather_monthly_invalid_bins_rejected():
    dwm = e.dispatch_weather_monthly
    m, h, t_len = _calendar(False)
    pv, load, pzo = _synthetic(1, t_len, seed=666)
    valid = np.ones(t_len, dtype=bool)
    s = _settings()
    args = (pv, load, pzo, (m - 1).astype(np.int64), 0.05, 5.0, 2.5, 2.5, 0.95, 3.0, 0.0, pzo[0], valid, False, pd.physical_parameters(s), m, h)
    for bad_val in (-1, 12):
        bad_bins = (m - 1).copy(); bad_bins[10] = bad_val
        bad_args = list(args); bad_args[3] = bad_bins
        with pytest.raises(ValueError):
            dwm(*bad_args)
