"""Hand-checkable projection boundaries; all source data are synthetic."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from zoneinfo import ZoneInfo
import calendar
import io
import numpy as np
import pytest
from feasibility.consumption_ingest import IngestOptions, Interval, ValidatedIntervals, parse_consumption
from feasibility.load_projection import (ProjectionPolicy, project_load, synthetic_weights,
                                         utc_slots, STANDARD, ROME)


def policy(**changes):
    return replace(ProjectionPolicy('synthetic', 'february_month_hour', 2023, True), **changes)


def source(start, end, energy=1.0, fixed=False):
    options = IngestOptions(',', '.', {'timestamp': 't', 'value': 'e'}, 'kWh', 3600,
                            'Europe/Rome', 'start', 'gross', fixed_clock=fixed,
                            coverage_start=start, coverage_end=end)
    records = tuple(Interval(start + timedelta(hours=i), 3600, energy)
                    for i in range(int((end-start).total_seconds() / 3600)))
    return ValidatedIntervals(records, len(records)*energy, start, end, True, (), 'a'*64,
                              MappingProxyType({}), options)


@pytest.mark.parametrize('year', [2023, 2024, 2028])
@pytest.mark.parametrize('variant', ['flat', 'daytime', 'evening'])
def test_synthetic_energy_calendar_and_immutable(year, variant):
    load = project_load(None, [0, 280, *([310]*10)], [year], policy(variant=variant))
    months = np.array([t.astimezone(STANDARD).month for t in utc_slots(year)])
    for a in (load.load_civil, load.load_standard):
        for m, total in enumerate(load.monthly_totals, 1):
            assert a[0, :len(months)][months == m].sum() == pytest.approx(total, abs=1e-10)
        assert np.all(a[0, months.size:] == 0)
        with pytest.raises(ValueError):
            a.setflags(write=True)
    assert load.synthesized_fraction == 1 and load.acknowledgement_required
    assert load.digest == project_load(None, load.monthly_totals, [year], policy(variant=variant)).digest


@pytest.mark.parametrize('variant', ['flat', 'daytime', 'evening'])
def test_generator_deterministic_finite_weekend(variant):
    a = synthetic_weights(2024, variant)
    assert a.dtype == np.float64 and a.shape == (8784,)
    assert np.all(np.isfinite(a)) and np.all(a >= 0)
    np.testing.assert_array_equal(a, synthetic_weights(2024, variant))
    if variant == 'flat':
        assert np.all(a == 1)
    else:
        # Monday and Saturday at the same Rome hour have explicit factors.
        slots = utc_slots(2024)
        monday = next(i for i,t in enumerate(slots) if t.astimezone(ROME).weekday() == 0 and t.hour == 12)
        saturday = next(i for i,t in enumerate(slots) if t.astimezone(ROME).weekday() == 5 and t.hour == 12)
        assert a[monday] != a[saturday]
    assert synthetic_weights(2023, variant).shape == (8760,)


@pytest.mark.parametrize('year,variant', [(True, 'flat'), (1899, 'flat'), (2101, 'flat'),
                                          (2024, 'other'), (2024.0, 'flat')])
def test_generator_invalid(year, variant):
    with pytest.raises(ValueError):
        synthetic_weights(year, variant)


def test_annual_allocation_reference_year_no_heating():
    load = project_load(None, None, [2023, 2024], policy(annual_kwh=3660, reference_year=2024))
    assert load.monthly_totals[1] == 290
    assert load.monthly_totals[0] == 310
    assert sum(load.monthly_totals) == 3660
    assert load.provenance['reference_year'] == 2024


def test_shifted_full_source_window_keeps_measurements_and_leap_day():
    start = datetime(2023, 10, 1, tzinfo=ROME).astimezone(timezone.utc)
    end = datetime(2024, 10, 1, tzinfo=ROME).astimezone(timezone.utc)
    s = source(start, end)
    original = s.intervals
    result = project_load(s, None, [2023, 2024, 2028], policy(mode='measured'))
    assert result.monthly_totals[1] == 29*24
    assert result.monthly_totals[2] == 31*24-1
    assert result.monthly_totals[9] == 31*24+1
    assert s.intervals is original
    assert sum(r.energy_kwh for r in s.intervals if r.start_utc.astimezone(ROME).month == 2
               and r.start_utc.astimezone(ROME).day == 29) == 24
    assert result.provenance['completion_fraction'] == 0


def test_partial_requires_all_months_and_missing_cells_fail():
    s = source(datetime(2023, 1, 1, tzinfo=timezone.utc), datetime(2023, 2, 1, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match='partial'):
        project_load(s, None, [2024], policy(mode='measured'))
    with pytest.raises(ValueError, match='missing'):
        project_load(s, [100]*12, [2024], policy(mode='measured'))
    r = project_load(s, [100]*12, [2024], policy(mode='measured', synthetic_completion=True))
    assert 0 < r.provenance['completion_fraction'] < 1
    with pytest.raises(ValueError):
        project_load(None, [100]*11, [2024], policy())


def test_no_measured_gap_repair_even_with_completion():
    s = source(datetime(2023, 1, 1, tzinfo=timezone.utc), datetime(2023, 2, 1, tzinfo=timezone.utc))
    gap = replace(s, intervals=s.intervals[:5] + s.intervals[6:])
    with pytest.raises(ValueError, match='gaps'):
        project_load(gap, [100]*12, [2024], policy(mode='measured', synthetic_completion=True))


def test_positive_target_zero_shape_and_zero_months():
    s = source(datetime(2023, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 1, tzinfo=timezone.utc), 0)
    with pytest.raises(ValueError, match='zero shape mass'):
        project_load(s, [1]*12, [2024], policy(mode='measured'))
    r = project_load(s, [0]*12, [2024], policy(mode='measured'))
    assert np.count_nonzero(r.load_civil) == 0


@pytest.mark.parametrize('changes', [dict(leap_policy='interpolate'), dict(reference_year=True),
    dict(annual_kwh=float('nan')), dict(annual_kwh=-1), dict(mode='auto'), dict(acknowledged=1)])
def test_invalid_policy(changes):
    with pytest.raises(ValueError):
        project_load(None, None, [2024], policy(**changes))


def test_explicit_coverage_and_mode_selection():
    s = source(datetime(2023, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match='coverage'):
        project_load(replace(s, coverage_declared=False), [10]*12, [2024], policy(mode='measured'))
    with pytest.raises(ValueError, match='discard'):
        project_load(s, [10]*12, [2024], policy())
    with pytest.raises(ValueError, match='annual-only'):
        project_load(s, [10]*12, [2024], policy(mode='measured', annual_kwh=100))


def test_quarter_hours_and_both_dst_folds_train_elapsed_average():
    start = datetime(2023, 10, 29, tzinfo=ROME).astimezone(timezone.utc)
    end = datetime(2023, 10, 30, tzinfo=ROME).astimezone(timezone.utc)
    lines = ['t,e']
    for i in range(100):
        t = start + timedelta(minutes=15*i)
        lines.append(t.isoformat() + ',0.25')
    options = IngestOptions(',', '.', {'timestamp':'t', 'value':'e'}, 'kWh', 900,
                           'Europe/Rome', 'start', 'gross', coverage_start=start, coverage_end=end)
    s = parse_consumption(io.StringIO('\n'.join(lines)), options)
    r = project_load(s, [24]*12, [2024], policy(mode='measured', synthetic_completion=True))
    assert s.total_energy_kwh == 25
    assert r.load_civil.sum() == pytest.approx(288)
    assert r.load_standard.sum() == pytest.approx(288)


def test_dst_fold_hours_each_contribute_to_training_average():
    from feasibility.load_projection import _train
    start = datetime(2023, 10, 29, tzinfo=ROME).astimezone(timezone.utc)
    end = datetime(2023, 10, 30, tzinfo=ROME).astimezone(timezone.utc)
    s = source(start, end)
    records = tuple(replace(r, energy_kwh=3.0 if r.start_utc.astimezone(ROME).fold else 1.0)
                    for r in s.intervals)
    shape, hours = _train(replace(s, intervals=records), ROME)
    assert hours[9, 2] == 2
    assert shape[9, 2] == 2


def test_fixed_clock_source_month_energy_and_equal_variant_totals():
    start = datetime(2023, 1, 1, tzinfo=STANDARD).astimezone(timezone.utc)
    end = datetime(2024, 1, 1, tzinfo=STANDARD).astimezone(timezone.utc)
    measured = project_load(source(start, end, fixed=True), None, [2024], policy(mode='measured'))
    assert measured.monthly_totals[2] == 31*24
    assert measured.monthly_totals[9] == 31*24
    variants = [project_load(None, None, [2024], policy(annual_kwh=3650, variant=v))
                for v in ('flat', 'daytime', 'evening')]
    assert len({v.monthly_totals for v in variants}) == 1
    assert all(v.load_civil.sum() == pytest.approx(3650) for v in variants)
    assert not np.array_equal(variants[1].load_civil, variants[2].load_civil)
