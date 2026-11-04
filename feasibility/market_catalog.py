"""Administrator-only immutable GME catalog storage (catalog-v1).

Monthly blobs and revision manifests are content addressed. A locked, fsynced
pointer is the only mutable publication state. Existing profiles remain pinned to
their revision; this module does not refresh them. Operator supplied archives only:
market-data rights and redistribution must be reviewed separately.
"""
import calendar
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import uuid

import numpy as np

from .file_lock import exclusive_lock
from .prepare_market_prices import (SERIES, SOURCE_SCHEMAS, PARSER_VERSION,
                                    civil_date, coverage_dates, day_bounds, compute_empirical_window)

if os.name == 'nt':
    from .windows_storage import mkdir_private, move, native_path


CATALOG_VERSION = 'gme-catalog-v1'


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def content_hash(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('Invalid catalog digest')
    return value


def _plain_path(path):
    path = Path(path).absolute()
    if os.name == 'nt':
        path = Path(native_path(path))
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or (
                os.name == 'nt' and info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError('Symlinks and reparse points are not permitted in catalog paths')
    return path


def _directory(path):
    path = _plain_path(path)
    if os.name == 'nt':
        for parent in reversed((path, *path.parents)):
            if not parent.exists():
                try:
                    mkdir_private(parent)
                except FileExistsError:
                    pass
    else:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def _replace(source, destination):
    if os.name == 'nt':
        move(source, _plain_path(destination), replace=True)
    else:
        os.replace(source, destination)


def _sync_dir(path):
    if os.name == 'nt':
        # Windows publication uses write-through renames instead of directory fsync.
        return
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write(path, value):
    path = _plain_path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0), 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(canonical_bytes(value))
        stream.flush()
        os.fsync(stream.fileno())


def _read(path):
    path = _plain_path(path)
    with path.open('rb') as stream:
        raw = stream.read()
    value = json.loads(raw)
    if canonical_bytes(value) != raw:
        raise ValueError('Noncanonical catalog JSON')
    return value


def _products(days, start, end):
    result = {}
    for series in SERIES:
        product = compute_empirical_window(days, start, end, series=series)
        result[series] = {key: value.tolist() if isinstance(value, np.ndarray) else value
                          for key, value in product.items()}
    return result


def _validate_days(days):
    if not isinstance(days, dict) or not days:
        raise ValueError('Empty catalog days')
    dates = sorted(days)
    start = civil_date(dates[0])
    end = civil_date(dates[-1]) + timedelta(days=1)
    if dates != [d.isoformat() for d in coverage_dates(start, end)]:
        raise ValueError('Catalog delivery dates must be contiguous')
    for stamp, day in days.items():
        if (day.get('date') != stamp or day.get('source_schema') not in SOURCE_SCHEMAS
                or day.get('parser_version') != PARSER_VERSION
                or not day.get('source_hashes')
                or day['source_hashes'] != sorted(set(day['source_hashes']))):
            raise ValueError('Invalid daily source provenance')
        for sha in day['source_hashes']:
            _digest(sha)
        if not isinstance(day.get('records'), list) or any(not isinstance(row, list) or len(row) != 4 or row[0] not in SERIES for row in day['records']):
            raise ValueError('Invalid canonical series record')
        overlap = day.get('overlap', {})
        if (overlap.get('selected_resolution_seconds') not in (900, 3600)
                or not isinstance(overlap.get('compared_series_hours'), int)
                or overlap['compared_series_hours'] < 0
                or not 0 <= overlap.get('max_abs_diff_eur_mwh', float('inf')) <= 1e-4):
            raise ValueError('Invalid overlap provenance')
        if any(row[2] != overlap['selected_resolution_seconds'] for row in day['records']):
            raise ValueError('Inconsistent source resolution')
        begin, finish = day_bounds(stamp)
        duration = overlap['selected_resolution_seconds']
        expected = int((finish - begin).total_seconds()) // duration * len(SERIES)
        if len(day['records']) != expected:
            raise ValueError('Incomplete series in canonical day')
        for i, row in enumerate(day['records']):
            point = begin + timedelta(seconds=(i // len(SERIES)) * duration)
            if (row[0] != SERIES[i % len(SERIES)] or row[1] != point.isoformat()
                    or type(row[2]) is not int or type(row[3]) not in (int, float)
                    or not math.isfinite(row[3])):
                raise ValueError('Invalid canonical price timeline')
    return start.isoformat(), end.isoformat()


def read_revision(catalog_dir, revision=None):
    """Verify pinned/current content and products; no writes or engine imports."""
    root = _plain_path(catalog_dir)
    if revision is None:
        pointer = _read(root / 'current.json')
        if set(pointer) != {'revision', 'schema_version'} or pointer['schema_version'] != CATALOG_VERSION:
            raise ValueError('Invalid catalog pointer')
        revision = pointer['revision']
    revision = _digest(revision)
    manifest = _read(root / 'revisions' / revision / 'manifest.json')
    if (content_hash(manifest) != revision or manifest.get('schema_version') != CATALOG_VERSION
            or manifest.get('parser_version') != PARSER_VERSION or manifest.get('series') != list(SERIES)):
        raise ValueError('Catalog revision hash/schema mismatch')
    days = {}
    for month, digest in manifest['months'].items():
        if not re.fullmatch(r'\d{4}-\d{2}', month):
            raise ValueError('Invalid catalog month')
        blob = _read(root / 'months' / (_digest(digest) + '.json'))
        if content_hash(blob) != digest or blob.get('month') != month or blob.get('schema_version') != CATALOG_VERSION:
            raise ValueError('Catalog month hash/schema mismatch')
        for stamp, day in blob['days'].items():
            if stamp[:7] != month or stamp in days:
                raise ValueError('Invalid month delivery date')
            days[stamp] = day
    coverage = list(_validate_days(days))
    if manifest.get('coverage') != coverage:
        raise ValueError('Catalog coverage mismatch')
    window = manifest['window']
    if manifest['products'] != _products(days, *window):
        raise ValueError('Catalog product validation failed')
    return {'revision': revision, 'manifest': manifest, 'days': days}


@contextmanager
def _publication_lock(root):
    path = _plain_path(root / '.publish.lock')
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0), 0o600)
    try:
        with exclusive_lock(fd):
            yield
    finally:
        os.close(fd)


def _assemble(previous, ingest, replace, window_start, window_end):
    if (ingest.get('source_schema') not in SOURCE_SCHEMAS
            or ingest.get('parser_version') != PARSER_VERSION):
        raise ValueError('Unsupported ingest schema/version')
    incoming = ingest['days']
    incoming_coverage = list(_validate_days(incoming))
    if ingest.get('coverage') != incoming_coverage:
        raise ValueError('Ingest coverage mismatch')
    days = dict(previous['days']) if previous else {}
    corrections = list(previous['manifest']['corrections']) if previous else []
    for stamp, day in incoming.items():
        old = days.get(stamp)
        if old is not None and old != day:
            scientific_keys = ('records', 'source_schema', 'parser_version', 'overlap')
            changed = any(old[key] != day[key] for key in scientific_keys)
            if changed and not replace:
                raise ValueError(f'Conflicting day {stamp}; explicit --replace required')
            if not changed:
                day = dict(day, source_hashes=sorted(set(old['source_hashes']) | set(day['source_hashes'])))
            if day != old:
                corrections.append({'date': stamp, 'previous_day': content_hash(old),
                                    'replacement_day': content_hash(day),
                                    'kind': 'prices-or-resolution' if changed else 'source-provenance'})
        days[stamp] = day
    start, end = _validate_days(days)
    if (window_start is None) != (window_end is None):
        raise ValueError('Supply both window boundaries')
    if window_start is not None:
        window = [civil_date(window_start).isoformat(), civil_date(window_end).isoformat()]
    else:
        years = sorted({int(d[:4]) for d in days})
        complete_years = [
            y for y in years
            if all(
                f"{y}-{m:02d}-{d:02d}" in days and days[f"{y}-{m:02d}-{d:02d}"].get('records')
                for m in range(1, 13)
                for d in range(1, calendar.monthrange(y, m)[1] + 1)
            )
        ]
        if complete_years:
            latest = max(complete_years)
            window = [f"{latest}-01-01", f"{latest + 1}-01-01"]
        else:
            window = [start, end]
    blobs = {}
    for stamp, day in sorted(days.items()):
        month = stamp[:7]
        blob = blobs.setdefault(month, {'schema_version': CATALOG_VERSION, 'month': month, 'days': {}})
        blob['days'][stamp] = day
    manifest = {'schema_version': CATALOG_VERSION, 'parser_version': PARSER_VERSION,
                'series': list(SERIES), 'coverage': [start, end], 'window': window,
                'months': {month: content_hash(blob) for month, blob in blobs.items()},
                'corrections': corrections, 'products': _products(days, *window)}
    return manifest, blobs


def _publish(root, revision, manifest, blobs):
    """Complete/validate immutable files before the single mutable pointer swap."""
    months, revisions = _directory(root / 'months'), _directory(root / 'revisions')
    stage = (_directory(revisions / ('.staging-' + uuid.uuid4().hex)) if os.name == 'nt'
             else Path(tempfile.mkdtemp(prefix='.staging-', dir=revisions)))
    pointer_tmp = root / ('.current-' + uuid.uuid4().hex)
    try:
        for month, blob in blobs.items():
            digest = manifest['months'][month]
            destination = months / (digest + '.json')
            if destination.exists():
                if _read(destination) != blob:
                    raise ValueError('Existing immutable month differs')
                continue
            temporary = stage / (digest + '.json')
            _write(temporary, blob)
            if _read(temporary) != blob:
                raise ValueError('Staged month validation failed')
            _replace(temporary, destination)
        _sync_dir(months)
        _write(stage / 'manifest.json', manifest)
        _sync_dir(stage)
        destination = revisions / revision
        if destination.exists():
            if _read(destination / 'manifest.json') != manifest:
                raise ValueError('Existing immutable revision differs')
        else:
            _replace(stage, destination)
        _sync_dir(revisions)
        # Independently reopen and recompute products before publication.
        read_revision(root, revision)
        _write(pointer_tmp, {'schema_version': CATALOG_VERSION, 'revision': revision})
        _replace(pointer_tmp, root / 'current.json')
        _sync_dir(root)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
        pointer_tmp.unlink(missing_ok=True)


def update_catalog(catalog_dir, ingest, *, publish=False, replace=False,
                   window_start=None, window_end=None):
    """Merge a bounded import with prior months; dry-run unless publish=True.

    --start/--end describe the incoming archive coverage. By default statistics
    cover the resulting catalog; optional window boundaries select a subwindow.
    Repeated identical imports do not rewrite the current pointer.
    """
    root = _plain_path(catalog_dir)

    def perform():
        previous = read_revision(root) if (root / 'current.json').exists() else None
        manifest, blobs = _assemble(previous, ingest, replace, window_start, window_end)
        revision = content_hash(manifest)
        unchanged = previous is not None and previous['revision'] == revision
        if publish and not unchanged:
            _publish(root, revision, manifest, blobs)
        return {'revision': revision, 'published': bool(publish), 'unchanged': unchanged,
                'coverage': manifest['coverage'], 'window': manifest['window'],
                'months': manifest['months'], 'corrections': manifest['corrections'],
                'shape_available': {s: manifest['products'][s]['normalized_shape'] is not None for s in SERIES}}

    if not publish:
        return perform()
    _directory(root)
    with _publication_lock(root):
        return perform()
