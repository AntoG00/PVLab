"""Synthetic-only catalog timeline, products and atomic publication tests."""
import copy
from datetime import date, datetime, timedelta, timezone
import json
import multiprocessing as mp
import os
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from feasibility import market_catalog as catalog
from feasibility import prepare_market_prices as prep
from feasibility.runtime_context import available_price_catalog, resolve_profile_prices
try:
    from tests.test_profiles import valid_draft_payload
except ImportError:
    from test_profiles import valid_draft_payload


def synthetic_ingest(start, end, price=100):
    days = {}
    current, stop = date.fromisoformat(start), date.fromisoformat(end)
    while current < stop:
        following = current + timedelta(days=1)
        begin = datetime(current.year, current.month, current.day, tzinfo=ZoneInfo('Europe/Rome')).astimezone(timezone.utc)
        finish = datetime(following.year, following.month, following.day, tzinfo=ZoneInfo('Europe/Rome')).astimezone(timezone.utc)
        records, dt = [], begin
        while dt < finish:
            for series in prep.SERIES:
                value = price(dt, series) if callable(price) else price
                records.append([series, dt.isoformat(), 3600, value / 1000])
            dt += timedelta(hours=1)
        stamp = current.isoformat()
        days[stamp] = {'date': stamp, 'records': records,
                       'source_hashes': ['a' * 64], 'source_schema': prep.SOURCE_SCHEMA,
                       'parser_version': prep.PARSER_VERSION,
                       'overlap': {'compared_series_hours': 0, 'max_abs_diff_eur_mwh': 0.0,
                                   'selected_resolution_seconds': 3600}}
        current = following
    return {'days': days, 'source_schema': prep.SOURCE_SCHEMA, 'parser_version': prep.PARSER_VERSION,
            'coverage': [start, end], 'archive_hashes': ['a' * 64]}


@pytest.mark.parametrize('start,end,hours,feb', [('2024-01-01', '2025-01-01', 8784, 29), ('2025-10-01', '2026-10-01', 8760, 28)])
def test_complete_windows_counts_leap_and_spill(start, end, hours, feb):
    ingest = synthetic_ingest(start, end, lambda dt, s: 20 + dt.hour + prep.SERIES.index(s))
    result = prep.compute_empirical_window(ingest['days'], start, end, series='PUN', require_climatology=True)
    assert result['total_hours'] == hours
    assert result['counts_hours'].sum() == hours
    assert np.all(result['counts_hours'][1] == feb)
    assert result['observation_counts'].sum() == hours
    assert np.average(result['normalized_shape'], weights=result['counts_hours']) == pytest.approx(1)
    expected = np.mean([row[3] for day in ingest['days'].values() for row in day['records'] if row[0] == 'PUN'])
    assert result['mean_eur_kwh'] == pytest.approx(expected)
    if start.endswith('10-01'):
        assert result['boundaries_standard'][0] == '2025-09-30T23:00:00+01:00'
        assert result['boundaries_standard'][1] == '2026-09-30T23:00:00+01:00'
        assert result['counts_hours'][8, 23] == 30


@pytest.mark.parametrize('price', [0, -100])
def test_nonpositive_mean_has_absolute_prices_and_no_shape(price):
    days = synthetic_ingest('2025-01-01', '2026-01-01', price)['days']
    result = prep.compute_empirical_window(days, '2025-01-01', '2026-01-01', series='NORD')
    np.testing.assert_allclose(result['matrix_eur_kwh'], price / 1000, rtol=0, atol=1e-15)
    assert result['normalized_shape'] is None
    assert 'Nonpositive' in result['shape_unavailable_reason']


def test_signed_prices_positive_mean_and_partial_window():
    data = synthetic_ingest('2025-01-01', '2026-01-01', lambda dt, s: -10 if dt.hour == 0 else 100)
    full = prep.compute_empirical_window(data['days'], '2025-01-01', '2026-01-01', series='CSUD')
    assert full['normalized_shape'].min() < 0
    partial = prep.compute_empirical_window(data['days'], '2025-02-01', '2025-03-01', series='CSUD')
    assert partial['normalized_shape'] is None
    assert partial['counts_hours'].sum() == 28 * 24
    with pytest.raises(ValueError, match='twelve complete'):
        prep.compute_empirical_window(data['days'], '2025-02-01', '2025-03-01', series='CSUD', require_climatology=True)


def engine_calendar(year):
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    count = (datetime(year + 1, 1, 1, tzinfo=timezone.utc) - start).days * 24
    points = [start + timedelta(hours=i) for i in range(count)]
    month = np.zeros((1, 8784), dtype=int)
    hour = np.zeros_like(month)
    valid = np.zeros_like(month, dtype=bool)
    valid[0, :count] = True
    month[0, :count] = [p.month for p in points]
    hour[0, :count] = [p.hour for p in points]
    return month, hour, valid


@pytest.mark.parametrize('year', [2024, 2025])
def test_supplied_calendar_expansion(year):
    month, hour, valid = engine_calendar(year)
    shape = np.arange(1, 289).reshape(12, 24).astype(float)
    result = prep.expand_pzo_proxy(shape, month, hour, valid)
    assert result[valid].mean() == pytest.approx(1)
    assert np.all(result[~valid] == 0)
    assert result[0, 0] / result[0, 1] == pytest.approx(shape[0, 0] / shape[0, 1])
    with pytest.raises(ValueError):
        prep.expand_pzo_proxy(-shape, month, hour, valid)
    hour[0, 2] = 24
    with pytest.raises(ValueError):
        prep.expand_pzo_proxy(shape, month, hour, valid)


def test_dry_run_does_not_create_directory(tmp_path):
    root = tmp_path / 'catalog'
    result = catalog.update_catalog(root, synthetic_ingest('2025-01-01', '2025-02-01'))
    assert not result['published']
    assert not root.exists()


def test_monthly_updates_reuse_pinned_history_and_idempotence(tmp_path, monkeypatch):
    jan = synthetic_ingest('2025-01-01', '2025-02-01')
    first = catalog.update_catalog(tmp_path, jan, publish=True)
    pointer = (tmp_path / 'current.json').stat().st_mtime_ns
    assert catalog.update_catalog(tmp_path, jan, publish=True)['unchanged']
    assert (tmp_path / 'current.json').stat().st_mtime_ns == pointer
    old_bytes = (tmp_path / 'months' / (first['months']['2025-01'] + '.json')).read_bytes()
    second = catalog.update_catalog(tmp_path, synthetic_ingest('2025-02-01', '2025-03-01'), publish=True)
    assert second['revision'] != first['revision']
    assert second['coverage'] == ['2025-01-01', '2025-03-01']
    assert second['months']['2025-01'] == first['months']['2025-01']
    assert (tmp_path / 'months' / (first['months']['2025-01'] + '.json')).read_bytes() == old_bytes
    assert catalog.read_revision(tmp_path, first['revision'])['manifest']['coverage'] == first['coverage']
    assert catalog.read_revision(tmp_path)['revision'] == second['revision']
    assert not any(second['shape_available'].values())


def test_explicit_correction_new_digest_and_old_revision(tmp_path):
    jan = synthetic_ingest('2025-01-01', '2025-02-01')
    old = catalog.update_catalog(tmp_path, jan, publish=True)
    corrected = synthetic_ingest('2025-01-01', '2025-02-01', 120)
    corrected['days']['2025-01-01']['source_hashes'] = ['b' * 64]
    pointer = (tmp_path / 'current.json').read_bytes()
    with pytest.raises(ValueError, match='explicit --replace'):
        catalog.update_catalog(tmp_path, corrected, publish=True)
    assert (tmp_path / 'current.json').read_bytes() == pointer
    new = catalog.update_catalog(tmp_path, corrected, publish=True, replace=True)
    assert new['revision'] != old['revision']
    assert len(new['corrections']) == 31
    assert catalog.read_revision(tmp_path, old['revision'])['days']['2025-01-01']['records'][0][3] == .1
    assert catalog.read_revision(tmp_path)['days']['2025-01-01']['records'][0][3] == .12
    assert catalog.update_catalog(tmp_path, corrected, publish=True, replace=True)['unchanged']


def _publish_contender(root, started, assembled):
    original = catalog._assemble

    def observe_assemble(*args):
        assembled.set()
        return original(*args)

    catalog._assemble = observe_assemble
    started.set()
    catalog.update_catalog(root, synthetic_ingest('2025-01-02', '2025-01-04'), publish=True)


def test_concurrent_publication_merges_both_imports(tmp_path):
    ctx = mp.get_context('spawn')
    started, assembled = ctx.Event(), ctx.Event()
    contender = ctx.Process(target=_publish_contender, args=(tmp_path, started, assembled))
    try:
        with catalog._publication_lock(tmp_path):
            contender.start()
            assert started.wait(15)
            assert not assembled.wait(0.5)
            manifest, blobs = catalog._assemble(
                None, synthetic_ingest('2025-01-01', '2025-01-03'), False, None, None)
            catalog._publish(tmp_path, catalog.content_hash(manifest), manifest, blobs)
        contender.join(15)
        assert contender.exitcode == 0
        assert assembled.is_set()
        assert catalog.read_revision(tmp_path)['manifest']['coverage'] == ['2025-01-01', '2025-01-04']
    finally:
        if contender.pid is not None and contender.is_alive():
            contender.kill()
            contender.join()
        contender.close()


def test_source_hash_changes_recorded_without_silent_loss(tmp_path):
    data = synthetic_ingest('2025-01-01', '2025-01-02')
    old = catalog.update_catalog(tmp_path, data, publish=True)
    data['days']['2025-01-01']['source_hashes'] = ['b' * 64]
    new = catalog.update_catalog(tmp_path, data, publish=True)
    assert old['revision'] != new['revision']
    assert len(new['corrections']) == 1
    assert new['corrections'][0]['kind'] == 'source-provenance'
    assert catalog.read_revision(tmp_path)['days']['2025-01-01']['source_hashes'] == ['a' * 64, 'b' * 64]
    assert catalog.update_catalog(tmp_path, data, publish=True)['unchanged']


@pytest.mark.parametrize('fail_at', ['month', 'manifest', 'pointer'])
def test_interrupted_publication_preserves_current(tmp_path, monkeypatch, fail_at):
    old = catalog.update_catalog(tmp_path, synthetic_ingest('2025-01-01', '2025-02-01'), publish=True)
    pointer = (tmp_path / 'current.json').read_bytes()
    original = catalog._write

    def failed_write(path, value):
        if ((fail_at == 'month' and path.name.endswith('.json') and path.name != 'manifest.json')
                or (fail_at == 'manifest' and path.name == 'manifest.json')
                or (fail_at == 'pointer' and path.name.startswith('.current-'))):
            raise OSError('Simulated interrupted write')
        return original(path, value)

    with monkeypatch.context() as patch:
        patch.setattr(catalog, '_write', failed_write)
        with pytest.raises(OSError, match='interrupted'):
            catalog.update_catalog(tmp_path, synthetic_ingest('2025-02-01', '2025-03-01'), publish=True)
    assert (tmp_path / 'current.json').read_bytes() == pointer
    assert catalog.read_revision(tmp_path)['revision'] == old['revision']
    assert not list((tmp_path / 'revisions').glob('.staging-*'))
    assert catalog.update_catalog(tmp_path, synthetic_ingest('2025-02-01', '2025-03-01'), publish=True)['revision'] != old['revision']


def test_gap_tampering_and_symlink_rejected(tmp_path):
    catalog.update_catalog(tmp_path, synthetic_ingest('2025-01-01', '2025-02-01'), publish=True)
    with pytest.raises(ValueError, match='contiguous'):
        catalog.update_catalog(tmp_path, synthetic_ingest('2025-03-01', '2025-04-01'), publish=True)
    current = catalog.read_revision(tmp_path)
    path = tmp_path / 'months' / (current['manifest']['months']['2025-01'] + '.json')
    path.write_text('{}')
    with pytest.raises(ValueError, match='hash/schema'):
        catalog.read_revision(tmp_path)
    path.unlink()
    if os.name == 'nt':
        subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(path), str(tmp_path)],
                       check=True, capture_output=True, timeout=10)
    else:
        path.symlink_to(tmp_path / 'current.json')
    with pytest.raises(ValueError, match='Symlinks'):
        catalog.read_revision(tmp_path)


def test_invalid_canonical_timeline_and_provenance(tmp_path):
    data = synthetic_ingest('2025-01-01', '2025-01-02')
    bad = copy.deepcopy(data)
    bad['days']['2025-01-01']['records'].pop()
    with pytest.raises(ValueError, match='Incomplete series'):
        catalog.update_catalog(tmp_path, bad)
    bad = copy.deepcopy(data)
    bad['days']['2025-01-01']['source_hashes'] = ['../bad']
    with pytest.raises(ValueError, match='digest'):
        catalog.update_catalog(tmp_path, bad)


def test_catalog_has_no_active_engine_import():
    process = subprocess.run([sys.executable, '-c', "import sys; import feasibility.market_catalog; assert 'feasibility.engine' not in sys.modules; assert 'feasibility.weather_dataset' not in sys.modules"], capture_output=True, text=True)
    assert process.returncode == 0, process.stderr


def test_cli_publish_and_inspect(tmp_path):
    from tests.test_gme_ingest import day_xml, write_zip
    source, root = tmp_path / 'incoming', tmp_path / 'catalog'
    write_zip(source / 'day.zip', {'20250101MGPPrezzi.xml': day_xml('2025-01-01')})
    cmd = [sys.executable, '-m', 'feasibility.prepare_market_prices', '--input', str(source),
           '--catalog', str(root), '--start', '2025-01-01', '--end', '2025-01-02']
    inspected = subprocess.run(cmd, capture_output=True, text=True)
    assert inspected.returncode == 0, inspected.stderr
    assert not root.exists()
    published = subprocess.run(cmd + ['--publish'], capture_output=True, text=True)
    assert published.returncode == 0, published.stderr
    result = json.loads(published.stdout)
    assert result['published']
    assert catalog.read_revision(root)['revision'] == result['revision']
    if os.name != 'nt':
        assert stat_mode(root) == 0o700
        assert stat_mode(root / 'current.json') == 0o600
    bad = subprocess.run(cmd + ['--source-schema', 'unknown'], capture_output=True, text=True)
    assert bad.returncode == 2
    assert 'invalid choice' in bad.stderr


def stat_mode(path):
    return path.stat().st_mode & 0o777


def test_failed_pointer_swap_preserves_current(tmp_path, monkeypatch):
    old = catalog.update_catalog(tmp_path, synthetic_ingest('2025-01-01', '2025-02-01'), publish=True)
    original = catalog._replace

    def fail_pointer(source, destination):
        if Path(destination).name == 'current.json':
            raise OSError('Simulated pointer interruption')
        return original(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(catalog, '_replace', fail_pointer)
        with pytest.raises(OSError, match='pointer interruption'):
            catalog.update_catalog(tmp_path, synthetic_ingest('2025-02-01', '2025-03-01'), publish=True)
    assert catalog.read_revision(tmp_path)['revision'] == old['revision']
    assert not list(tmp_path.glob('.current-*'))


def test_catalog_explicit_window_and_missing_boundaries(tmp_path):
    data = synthetic_ingest('2025-01-01', '2026-02-01')
    result = catalog.update_catalog(tmp_path, data, publish=True, window_start='2025-01-01', window_end='2026-01-01')
    assert all(result['shape_available'].values())
    revision = catalog.read_revision(tmp_path)
    assert revision['manifest']['window'] == ['2025-01-01', '2026-01-01']
    with pytest.raises(ValueError, match='both window'):
        catalog.update_catalog(tmp_path, data, window_start='2025-01-01')
    with pytest.raises(ValueError, match='Missing delivery date'):
        catalog.update_catalog(tmp_path, data, window_start='2024-01-01', window_end='2025-01-01')


def test_bad_month_calendar_and_shifted_legacy_convention():
    month, hour, valid = engine_calendar(2025)
    # Legacy observation calendar starts at standard-time hour 1 and wraps.
    active = int(valid.sum())
    for array in (month, hour):
        array[0, :active] = np.roll(array[0, :active], -1)
    assert prep.expand_pzo_proxy(np.ones((12, 24)), month, hour, valid)[valid].mean() == 1
    month[valid] = 1
    with pytest.raises(ValueError, match='complete Gregorian year'):
        prep.expand_pzo_proxy(np.ones((12, 24)), month, hour, valid)


def test_invalid_day_outside_selected_window_rejected(tmp_path):
    data = synthetic_ingest('2025-01-01', '2025-03-01')
    data['days']['2025-02-01']['records'][0][1] = '2025-02-01T12:00:00+00:00'
    with pytest.raises(ValueError, match='canonical price timeline'):
        catalog.update_catalog(tmp_path, data, window_start='2025-01-01', window_end='2025-02-01')



def test_market_catalog_multi_year_and_completeness_matrix(tmp_path, valid_draft_payload):
    # 1. Complete 2025
    root_2025 = tmp_path / 'cat_2025'
    ingest_2025 = synthetic_ingest('2025-01-01', '2026-01-01')
    res_2025 = catalog.update_catalog(root_2025, ingest_2025, publish=True)
    cat_2025 = available_price_catalog(root_2025)
    assert cat_2025['coverage'] == ['2025-01-01', '2026-01-01']
    assert cat_2025['window'] == ['2025-01-01', '2026-01-01']
    assert all(cat_2025['shape_available'].values())
    assert cat_2025['missing_months'] == []
    assert cat_2025['years']['2025']['complete'] is True
    assert cat_2025['years']['2025']['missing_months'] == []
    assert cat_2025['years']['2025']['shape_eligible'] is True

    # 2. Complete 2025 plus Jan-Sep 2026 (2025-01-01 to 2026-10-01)
    ingest_sep = synthetic_ingest('2026-01-01', '2026-10-01')
    res_multi = catalog.update_catalog(root_2025, ingest_sep, publish=True)
    cat_multi = available_price_catalog(root_2025)
    assert cat_multi['coverage'] == ['2025-01-01', '2026-10-01']
    assert cat_multi['window'] == ['2025-01-01', '2026-01-01']
    assert all(cat_multi['shape_available'].values())
    assert ['2025-01-01', '2026-01-01'] in cat_multi['windows']
    assert ['2025-01-01', '2026-10-01'] in cat_multi['windows']
    assert ['2026-01-01', '2026-10-01'] in cat_multi['windows']
    assert cat_multi['years']['2025']['complete'] is True
    assert cat_multi['years']['2026']['complete'] is False
    assert cat_multi['years']['2026']['missing_months'] == ['2026-10', '2026-11', '2026-12']
    assert cat_multi['missing_months'] == ['2026-10', '2026-11', '2026-12']
    assert cat_multi['years']['2026']['shape_eligible'] is False
    assert cat_multi['years']['2026']['proxy_eligible'] is True

    # 3. Jan-Sep 2026 only
    root_partial = tmp_path / 'cat_partial'
    res_part = catalog.update_catalog(root_partial, ingest_sep, publish=True)
    cat_part = available_price_catalog(root_partial)
    assert cat_part['coverage'] == ['2026-01-01', '2026-10-01']
    assert cat_part['window'] == ['2026-01-01', '2026-10-01']
    assert not any(cat_part['shape_available'].values())
    assert cat_part['years']['2026']['complete'] is False
    assert cat_part['years']['2026']['missing_months'] == ['2026-10', '2026-11', '2026-12']
    assert cat_part['years']['2026']['shape_eligible'] is False
    assert cat_part['years']['2026']['proxy_eligible'] is True

    # 4. Pinning revision and window after selection
    p_shape = copy.deepcopy(valid_draft_payload)
    p_shape['market'].update(
        valuation_mode='shape_scaled',
        price_revision=res_multi['revision'],
        window_start='2025-01-01',
        window_end='2026-01-01',
        series='NORD'
    )
    resolved_shape = resolve_profile_prices(p_shape, root_2025)
    assert resolved_shape['profile_market']['product']['normalized_shape'] is not None

    p_proxy = copy.deepcopy(valid_draft_payload)
    p_proxy['market'].update(
        valuation_mode='absolute_proxy',
        price_revision=res_multi['revision'],
        window_start='2026-01-01',
        window_end='2026-10-01',
        series='NORD'
    )
    resolved_proxy = resolve_profile_prices(p_proxy, root_2025)
    assert resolved_proxy['profile_market']['product']['normalized_shape'] is None
    assert resolved_proxy['profile_market']['product']['total_hours'] == 6551.0
    assert resolved_proxy['profile_market']['product']['dates_count'] == 273

    p_invalid = copy.deepcopy(valid_draft_payload)
    p_invalid['market'].update(
        valuation_mode='shape_scaled',
        price_revision=res_multi['revision'],
        window_start='2026-01-01',
        window_end='2026-10-01',
        series='NORD'
    )
    with pytest.raises(ValueError, match='twelve complete calendar months'):
        resolve_profile_prices(p_invalid, root_2025)


def test_market_catalog_legacy_multi_year_revision_compatibility(tmp_path):
    root_legacy = tmp_path / 'cat_legacy'
    data = synthetic_ingest('2025-01-01', '2026-10-01')
    res = catalog.update_catalog(root_legacy, data, publish=True, window_start='2025-01-01', window_end='2026-10-01')
    rev_on_disk = catalog.read_revision(root_legacy)
    assert rev_on_disk['manifest']['window'] == ['2025-01-01', '2026-10-01']

    # available_price_catalog dynamically selects 2025 default window without altering on-disk revision
    meta = available_price_catalog(root_legacy)
    assert meta['window'] == ['2025-01-01', '2026-01-01']
    assert all(meta['shape_available'].values())
    assert meta['years']['2025']['complete'] is True
    assert meta['years']['2026']['complete'] is False
    assert catalog.read_revision(root_legacy)['manifest']['window'] == ['2025-01-01', '2026-10-01']
    assert catalog.read_revision(root_legacy)['revision'] == res['revision']


def test_market_catalog_nonpositive_mean_shape_ineligibility(tmp_path, valid_draft_payload):
    root_neg = tmp_path / 'cat_neg'
    def price_fn(dt, s):
        return -100 if s == 'PUN' else 50
    neg_data = synthetic_ingest('2025-01-01', '2026-01-01', price=price_fn)
    res = catalog.update_catalog(root_neg, neg_data, publish=True)
    meta = available_price_catalog(root_neg)
    assert meta['shape_available']['PUN'] is False
    assert meta['shape_available']['NORD'] is True
    assert meta['years']['2025']['shape_available']['PUN'] is False
    assert meta['years']['2025']['shape_available']['NORD'] is True

    p_neg = copy.deepcopy(valid_draft_payload)
    p_neg['market'].update(
        valuation_mode='shape_scaled',
        price_revision=res['revision'],
        window_start='2025-01-01',
        window_end='2026-01-01',
        series='PUN'
    )
    with pytest.raises(ValueError, match='positive annual mean'):
        resolve_profile_prices(p_neg, root_neg)


def test_catalog_publish_and_read_with_long_storage_path(tmp_path):
    root = tmp_path / ('long-root-' + 'a' * 70) / ('nested-' + 'b' * 70) / ('catalog-' + 'c' * 70)
    result = catalog.update_catalog(root, synthetic_ingest('2025-01-01', '2025-01-03'), publish=True)
    reopened = catalog.read_revision(root)
    assert reopened['revision'] == result['revision']
    assert reopened['manifest']['coverage'] == ['2025-01-01', '2025-01-03']
    assert catalog.update_catalog(root, synthetic_ingest('2025-01-01', '2025-01-03'), publish=True)['unchanged']
