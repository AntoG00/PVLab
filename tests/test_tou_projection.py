"""Tests for Time-Of-Use (TOU) load projection, ARERA bands, and parity."""
from dataclasses import replace
from datetime import datetime
import numpy as np
import pytest

from feasibility.load_projection import (
    ProjectionPolicy,
    project_load,
    utc_slots,
    _synthetic_at,
    STANDARD,
    ROME,
    arera_band,
    aggregate_tou_to_monthly,
)


def policy(**changes):
    return replace(ProjectionPolicy('synthetic', 'february_month_hour', 2023, True), **changes)


@pytest.mark.parametrize('hour,expected', [
    (0, 3), (6, 3), (7, 2), (8, 1),
    (12, 1), (18, 1), (19, 2), (22, 2), (23, 3),
])
def test_arera_band_weekday_boundary_hours(hour, expected):
    dt = datetime(2024, 1, 10, hour, 0, tzinfo=ROME)
    assert arera_band(dt) == expected


@pytest.mark.parametrize('hour,expected', [
    (0, 3), (6, 3), (7, 2), (12, 2), (22, 2), (23, 3),
])
def test_arera_band_saturday_boundary_hours(hour, expected):
    dt = datetime(2024, 1, 13, hour, 0, tzinfo=ROME)
    assert arera_band(dt) == expected


@pytest.mark.parametrize('hour', [0, 6, 7, 8, 12, 18, 19, 22, 23])
def test_arera_band_sunday_all_f3(hour):
    dt = datetime(2024, 1, 14, hour, 0, tzinfo=ROME)
    assert arera_band(dt) == 3


@pytest.mark.parametrize('year', [2023, 2024, 2025])
@pytest.mark.parametrize('month,day', [
    (1, 1),
    (1, 6),
    (4, 25),
    (5, 1),
    (6, 2),
    (8, 15),
    (11, 1),
    (12, 8),
    (12, 25),
    (12, 26),
])
def test_arera_band_fixed_national_holidays_are_f3(year, month, day):
    dt = datetime(year, month, day, 12, 0, tzinfo=ROME)
    assert arera_band(dt) == 3


@pytest.mark.parametrize('date_str', [
    '2023-04-10',
    '2024-04-01',
    '2025-04-21',
    '2026-04-06',
    '2028-04-17',
])
@pytest.mark.parametrize('hour', [0, 8, 12, 18, 23])
def test_arera_band_easter_monday_multiple_years_is_f3(date_str, hour):
    d = datetime.fromisoformat(date_str)
    dt = datetime(d.year, d.month, d.day, hour, 0, tzinfo=ROME)
    assert arera_band(dt) == 3


def test_aggregate_tou_to_monthly_direct_vectors_and_keyword():
    f1 = (10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 35.0, 30.0, 25.0, 20.0, 15.0)
    f2 = (20.0, 25.0, 30.0, 35.0, 40.0, 45.0, 50.0, 45.0, 40.0, 35.0, 30.0, 25.0)
    f3 = (70.0, 60.0, 50.0, 40.0, 30.0, 20.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0)
    expected = tuple(f1[i] + f2[i] + f3[i] for i in range(12))

    sums_pos = aggregate_tou_to_monthly(f1, f2, f3)
    assert sums_pos == expected

    sums_kw = aggregate_tou_to_monthly(monthly_f1=f1, monthly_f2=f2, monthly_f3=f3)
    assert sums_kw == expected


@pytest.mark.parametrize('bad_vectors', [
    ((10.0,) * 11, (20.0,) * 12, (30.0,) * 12),
    ((10.0,) * 13, (20.0,) * 12, (30.0,) * 12),
    ((-1.0,) + (10.0,) * 11, (20.0,) * 12, (30.0,) * 12),
    ((float('nan'),) + (10.0,) * 11, (20.0,) * 12, (30.0,) * 12),
    ((10.0,) * 12, (float('inf'),) + (20.0,) * 11, (30.0,) * 12),
    ((True,) + (10.0,) * 11, (20.0,) * 12, (30.0,) * 12),
    ((1e308,) * 12, (1e308,) * 12, (1e308,) * 12),
])
def test_aggregate_tou_to_monthly_invalid_and_overflow(bad_vectors):
    with pytest.raises(ValueError):
        aggregate_tou_to_monthly(*bad_vectors)


def test_project_load_derives_totals_when_absent_and_provenance():
    f1 = (20.0,) * 12
    f2 = (30.0,) * 12
    f3 = (50.0,) * 12
    p = policy(monthly_f1=f1, monthly_f2=f2, monthly_f3=f3)
    load = project_load(None, None, [2024], p)
    assert load.monthly_totals == (100.0,) * 12
    assert load.provenance['tou_mode'] == 'arera_f1_f2_f3'

    legacy_load = project_load(None, [100.0] * 12, [2024], policy())
    assert 'tou_mode' not in legacy_load.provenance


@pytest.mark.parametrize('bad_policy', [
    dict(monthly_f1=(10.0,) * 12),
    dict(monthly_f2=(10.0,) * 12),
    dict(monthly_f3=(10.0,) * 12),
    dict(monthly_f1=(10.0,) * 12, monthly_f2=(20.0,) * 12),
    dict(monthly_f1=(10.0,) * 12, monthly_f3=(30.0,) * 12),
    dict(monthly_f2=(20.0,) * 12, monthly_f3=(30.0,) * 12),
    dict(monthly_f1=(10.0,) * 11, monthly_f2=(20.0,) * 12, monthly_f3=(30.0,) * 12),
    dict(monthly_f1=(10.0,) * 13, monthly_f2=(20.0,) * 12, monthly_f3=(30.0,) * 12),
    dict(monthly_f1=(-1.0,) + (10.0,) * 11, monthly_f2=(20.0,) * 12, monthly_f3=(30.0,) * 12),
    dict(monthly_f1=(float('nan'),) + (10.0,) * 11, monthly_f2=(20.0,) * 12, monthly_f3=(30.0,) * 12),
    dict(monthly_f1=(10.0,) * 12, monthly_f2=(float('inf'),) + (20.0,) * 11, monthly_f3=(30.0,) * 12),
    dict(mode='measured', monthly_f1=(10.0,) * 12, monthly_f2=(20.0,) * 12, monthly_f3=(30.0,) * 12),
    dict(annual_kwh=3600.0, monthly_f1=(10.0,) * 12, monthly_f2=(20.0,) * 12, monthly_f3=(30.0,) * 12),
])
def test_project_load_rejects_invalid_band_policies(bad_policy):
    with pytest.raises(ValueError):
        project_load(None, None, [2024], policy(**bad_policy))


def test_project_load_rejects_inconsistent_totals():
    f1 = (50.0,) * 12
    f2 = (50.0,) * 12
    f3 = (50.0,) * 12
    with pytest.raises(ValueError, match='(?i)inconsistent|mismatch|totals|conserve'):
        project_load(None, [100.0] * 12, [2024], policy(monthly_f1=f1, monthly_f2=f2, monthly_f3=f3))


@pytest.mark.parametrize('variant', ['flat', 'daytime', 'evening'])
def test_multi_year_leap_dst_band_conservation(variant):
    years = (2023, 2024, 2025)
    f1 = (50.0, 40.0, 60.0, 55.0, 70.0, 0.0, 65.0, 0.0, 50.0, 45.0, 55.0, 60.0)
    f2 = (70.0, 60.0, 80.0, 75.0, 90.0, 100.0, 85.0, 0.0, 70.0, 65.0, 75.0, 80.0)
    f3 = (180.0, 150.0, 160.0, 170.0, 140.0, 100.0, 150.0, 0.0, 180.0, 190.0, 170.0, 160.0)

    p = policy(variant=variant, monthly_f1=f1, monthly_f2=f2, monthly_f3=f3)
    load = project_load(None, None, years, p)

    assert np.all(load.load_civil >= 0)
    assert np.all(load.load_standard >= 0)

    for yi, year in enumerate(years):
        slots = utc_slots(year)
        norm_month = np.array([t.astimezone(STANDARD).month for t in slots])
        bands = np.array([arera_band(t.astimezone(ROME)) for t in slots])

        for arr in (load.load_civil[yi, :len(slots)], load.load_standard[yi, :len(slots)]):
            for m in range(1, 13):
                use_m = (norm_month == m)
                act_f1 = float(arr[use_m & (bands == 1)].sum())
                act_f2 = float(arr[use_m & (bands == 2)].sum())
                act_f3 = float(arr[use_m & (bands == 3)].sum())

                assert abs(act_f1 - f1[m - 1]) < 1e-6
                assert abs(act_f2 - f2[m - 1]) < 1e-6
                assert abs(act_f3 - f3[m - 1]) < 1e-6
                assert abs((act_f1 + act_f2 + act_f3) - load.monthly_totals[m - 1]) < 1e-6

            assert np.all(arr[norm_month == 8] == 0)


def _independent_no_band_reference(year, totals, variant):
    slots = utc_slots(year)
    norm_month = np.array([t.astimezone(STANDARD).month for t in slots])
    outputs = []
    for zone in (ROME, STANDARD):
        weights = _synthetic_at(slots, zone, variant).copy()
        for month, total in enumerate(totals, 1):
            use = (norm_month == month)
            if total == 0:
                weights[use] = 0
                continue
            mass = float(weights[use].sum())
            weights[use] *= total / mass
            idx = np.flatnonzero(use & (weights > 0))[-1]
            weights[idx] += total - float(weights[use].sum())
        outputs.append(weights)
    return outputs[0], outputs[1]


def test_no_band_legacy_parity_and_hardcoded_digest():
    load_2024 = project_load(None, [300.0] * 12, [2024], policy(variant='evening'))
    assert load_2024.digest == '09c56e9a451dd8de35649eeb3a10cc9e45f67cb75e99521d2371f41234727d83'

    ref_civil, ref_standard = _independent_no_band_reference(2024, (300.0,) * 12, 'evening')
    np.testing.assert_array_equal(load_2024.load_civil[0, :8784], ref_civil)
    np.testing.assert_array_equal(load_2024.load_standard[0, :8784], ref_standard)

    load_2023 = project_load(None, [100.0] * 12, [2023], policy(variant='flat'))
    assert load_2023.digest == '14beede717ae84a25ca41c67a7c2662cb7ade309c150ac1c778bf3c792553e9e'

    ref_civil_23, ref_standard_23 = _independent_no_band_reference(2023, (100.0,) * 12, 'flat')
    np.testing.assert_array_equal(load_2023.load_civil[0, :8760], ref_civil_23)
    np.testing.assert_array_equal(load_2023.load_standard[0, :8760], ref_standard_23)


def test_supplied_monthly_totals_tiny_discrepancy_reconciles_to_band_totals():
    f1 = (20.0,) * 12
    f2 = (30.0,) * 12
    f3 = (50.0,) * 12
    band_totals = aggregate_tou_to_monthly(f1, f2, f3)
    supplied = tuple(t + 5e-7 for t in band_totals)
    load = project_load(None, supplied, [2024], policy(monthly_f1=f1, monthly_f2=f2, monthly_f3=f3))
    assert load.monthly_totals == band_totals
    slots = utc_slots(2024)
    norm_month = np.array([t.astimezone(STANDARD).month for t in slots])
    for arr in (load.load_civil[0, :len(slots)], load.load_standard[0, :len(slots)]):
        for m, target in enumerate(load.monthly_totals, 1):
            assert np.isclose(arr[norm_month == m].sum(), target, rtol=1e-12, atol=1e-9)
