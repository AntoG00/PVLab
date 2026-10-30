"""Tests for ensemble-month Q allocation and temperature COP calibration in _allocate_space_heating."""
import numpy as np
import pytest
from feasibility import engine


@pytest.fixture
def synthetic_ensemble():
    n_years, n_hours = 21, 8760
    years = list(range(2005, 2005 + n_years))
    days = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    m_1d = np.array([m for m, d in enumerate(days, 1) for _ in range(d * 24)], dtype=int)
    h_1d = np.tile(np.arange(24), 365)

    months = np.tile(m_1d, (n_years, 1))
    hours = np.tile(h_1d, (n_years, 1))
    valid = np.ones((n_years, n_hours), dtype=bool)

    # Europe/Rome DST: approx hours 2040..7248 have civil = (hour + 1) % 24
    civil = np.copy(hours)
    civil[:, 2040:7248] = (civil[:, 2040:7248] + 1) % 24

    # Temperatures: Year 0 is cold (-4C anomaly), Year 20 is warm (+4C anomaly)
    doy = np.repeat(np.arange(365), 24)
    t_base = 10.0 + 10.0 * np.cos(2 * np.pi * (doy - 200) / 365.0)
    t_diurnal = 3.0 * np.cos(2 * np.pi * (h_1d - 14) / 24.0)
    temp = np.tile(t_base + t_diurnal, (n_years, 1))
    anomalies = np.linspace(-4.0, 4.0, n_years)
    for y in range(n_years):
        temp[y] += anomalies[y]

    settings = {
        'load_scenario': 'electrified',
        'heating_schedule_mode': 'scheduled',
        'timezone_mode': 'civil',
        'heating_balance_temp': 18.0,
        'scop': 4.0,
        'summer_gas_base': 26.0,
        'gas_pcs': 10.0,
        'boiler_efficiency': 0.8,
        'ac_heating_windows': [
            {'id': 'morning', 'start_minute': 360, 'end_minute': 480},
            {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
        ],
    }
    return {
        's': settings, 'temp': temp, 'm': months, 'h': hours,
        'valid': valid, 'civil': civil, 'years': years, 'n_years': n_years,
    }


def test_ensemble_monthly_thermal_and_annual_e_scop(synthetic_ensemble, monkeypatch):
    data = synthetic_ensemble
    s, temp, m, h = data['s'], data['temp'], data['m'], data['h']
    valid, civil, years, n_years = data['valid'], data['civil'], data['years'], data['n_years']

    # Simple 12-month synthetic useful heat budget (active winter, zero summer)
    mock_q = np.array([600, 500, 300, 0, 0, 0, 0, 0, 0, 100, 400, 700], dtype=float)
    monkeypatch.setattr(engine, '_ac_heating_budget', lambda *_, **__: {'requested_useful_heat_kwh': mock_q})

    e_prof = engine._allocate_space_heating(s, temp, m, h, valid, civil, years)

    # 1. Annual ensemble E mean == sum(Q) / SCOP
    annual_e_mean = np.sum(e_prof) / n_years
    expected_e_mean = np.sum(mock_q) / s['scop']
    assert np.isclose(annual_e_mean, expected_e_mean, rtol=1e-6)

    # 2. Recover thermal demand locally: Q = E * COP_calibrated
    raw_cop = np.clip(1.0 + 0.025 * (temp - 7.0), 0.45, 1.60)
    spf_raw = np.sum(e_prof * raw_cop) / np.sum(e_prof)
    cop_cal = (s['scop'] / spf_raw) * raw_cop
    q_recovered = e_prof * cop_cal

    for mm in range(1, 13):
        m_mask = (m == mm) & valid
        m_q_mean = np.sum(q_recovered[m_mask]) / n_years
        assert np.isclose(m_q_mean, mock_q[mm - 1], rtol=1e-5, atol=1e-8)

    # 3. Cold year annual E > warm year annual E
    e_cold_year = np.sum(e_prof[0])
    e_warm_year = np.sum(e_prof[-1])
    assert e_cold_year > e_warm_year


def test_temperature_cop_ratio_and_clamps(synthetic_ensemble, monkeypatch):
    data = synthetic_ensemble
    s, temp, m, h = dict(data['s']), data['temp'], data['m'], data['h']
    valid, civil, years = data['valid'], data['civil'], data['years']

    # Exercise both clamps through production with a custom balance temperature.
    s.update(heating_balance_temp=60.0, heating_schedule_mode='continuous')
    temp = np.tile(np.resize([-40., -30., -3., 7., 17., 40.], temp.shape[1]), (21, 1))
    # Clamping assertions
    raw_cop_fn = lambda t: np.clip(1.0 + 0.025 * (t - 7.0), 0.45, 1.60)
    assert np.isclose(raw_cop_fn(-40.0), 0.45)
    assert np.isclose(raw_cop_fn(40.0), 1.60)
    assert np.isclose(raw_cop_fn(7.0), 1.0)
    assert np.isclose(raw_cop_fn(17.0), 1.25)

    mock_q = np.array([500.0] + [0.0] * 11)
    monkeypatch.setattr(engine, '_ac_heating_budget', lambda *_, **__: {'requested_useful_heat_kwh': mock_q})
    e_prof = engine._allocate_space_heating(s, temp, m, h, valid, civil, years)

    # Equal-thermal: E / Q ratio equals 1 / COP_calibrated; colder hours require more E per unit Q
    raw_cop = raw_cop_fn(temp)
    active = (m == 1) & valid & (e_prof > 0)
    cold_hour = np.where(active & (raw_cop == np.min(raw_cop[active])))
    mild_hour = np.where(active & (raw_cop == np.max(raw_cop[active])))

    # Calibrated COP is proportional to raw_cop, so 1 / cop_cal is strictly higher for colder hour
    assert raw_cop[cold_hour][0] < raw_cop[mild_hour][0]
    per_degree = e_prof[active] / (s['heating_balance_temp'] - temp[active])
    np.testing.assert_allclose(per_degree * raw_cop[active], np.full(per_degree.shape, (per_degree * raw_cop[active])[0]), rtol=1e-12)
    assert e_prof[cold_hour][0] / (60.0 - temp[cold_hour][0]) > e_prof[mild_hour][0] / (60.0 - temp[mild_hour][0])


def test_custom_partial_hour_civil_standard_schedule(synthetic_ensemble, monkeypatch):
    data = synthetic_ensemble
    temp, m, h = data['temp'], data['m'], data['h']
    valid, civil, years = data['valid'], data['civil'], data['years']

    # Partial hour: 06:30 - 07:30 (start_minute=390, end_minute=450 -> 30 min in hr 6, 30 min in hr 7)
    custom_windows = [{'id': 'partial', 'start_minute': 390, 'end_minute': 450}]
    s_std = dict(data['s'], ac_heating_windows=custom_windows, timezone_mode='standard')
    s_civ = dict(data['s'], ac_heating_windows=custom_windows, timezone_mode='civil')

    mock_q = np.array([400.0] * 12)
    monkeypatch.setattr(engine, '_ac_heating_budget', lambda *_, **__: {'requested_useful_heat_kwh': mock_q})

    e_std = engine._allocate_space_heating(s_std, temp, m, h, valid, civil, years)
    e_civ = engine._allocate_space_heating(s_civ, temp, m, h, valid, civil, years)

    # In standard mode, only hours 6 and 7 receive non-zero heating
    active_std_hours = set(np.unique(h[e_std > 0]))
    assert active_std_hours.issubset({6, 7})

    # Civil and standard modes diverge during DST
    dst_diff = np.abs(e_civ[:, 2040:7248] - e_std[:, 2040:7248])
    assert np.sum(dst_diff) > 0.0


def test_zero_hdh_and_no_demand(synthetic_ensemble, monkeypatch):
    data = synthetic_ensemble
    s, temp, m, h = data['s'], data['temp'], data['m'], data['h']
    valid, civil, years = data['valid'], data['civil'], data['years']

    # 1. No demand: all monthly useful heat is 0
    monkeypatch.setattr(engine, '_ac_heating_budget', lambda *_, **__: {'requested_useful_heat_kwh': np.zeros(12)})
    e_zero = engine._allocate_space_heating(s, temp, m, h, valid, civil, years)
    assert np.all(e_zero == 0.0)
    assert not np.isnan(e_zero).any()
    monkeypatch.setattr(engine, '_ac_heating_budget', lambda *_, **__: {'requested_useful_heat_kwh': np.ones(12) * 400.0})
    e_warm = engine._allocate_space_heating(s, np.full_like(temp, 30.0), m, h, valid, civil, years)
    assert np.all(e_warm == 0.0)
