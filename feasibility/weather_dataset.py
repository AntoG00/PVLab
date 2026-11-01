"""Secure catalog-backed weather dataset loader with fail-closed integrity validation."""
from __future__ import annotations

import calendar
import hashlib
import json
import os
from pathlib import Path
from typing import Any
import numpy as np

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get('PV_LEGACY_DATA_ROOT', HERE / 'data'))
CANDIDATES_ROOT = DATA_DIR / 'weather_candidates'
ACTIVE_POINTER_FILE = DATA_DIR / 'weather-active.json'
BASELINE_NPZ = DATA_DIR / 'hourly.npz'
BASELINE_AUDIT = DATA_DIR / 'audit.json'

REQUIRED_ARRAYS = {
    'power', 'beam_loss', 'diffuse_loss', 'load_civil', 'load_standard',
    'month', 'hour', 'temperature', 'valid', 'years'
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _resolve_candidate_dir(dataset_dir: str) -> Path:
    if not isinstance(dataset_dir, str) or not dataset_dir.strip():
        raise ValueError('dataset_dir must be a non-empty string')
    resolved_root = CANDIDATES_ROOT.resolve()
    target = (CANDIDATES_ROOT / dataset_dir).resolve()
    try:
        target.relative_to(resolved_root)
    except ValueError:
        raise ValueError(f'dataset_dir {dataset_dir!r} resolves outside weather_candidates root')
    if not target.is_dir():
        raise ValueError(f'Candidate dataset directory {target} does not exist or is not a directory')
    return target


def verify_and_load_dataset(npz_path: Path, audit_path: Path,
                            manifest_path: Path | None = None,
                            expected_manifest_sha256: str | None = None) -> dict[str, Any]:
    if not npz_path.is_file():
        raise ValueError(f'Missing dataset archive: {npz_path}')
    if not audit_path.is_file():
        raise ValueError(f'Missing dataset audit: {audit_path}')

    # A v2 archive is never accepted through the unpinned legacy adapter.
    from .dataset_compile import MAX_METADATA_BYTES
    if audit_path.stat().st_size > MAX_METADATA_BYTES:
        raise ValueError('audit metadata size limit')
    from .profiles import strict_json
    header = strict_json(audit_path.read_bytes())
    if manifest_path is not None or (isinstance(header, dict) and header.get('dataset_schema_version') == 2):
        if manifest_path is None:
            raise ValueError('v2 dataset requires a pinned manifest')
        return _load_v2(npz_path, audit_path, manifest_path, expected_manifest_sha256)

    with np.load(npz_path, allow_pickle=False) as archive:
        missing = REQUIRED_ARRAYS - set(archive.files)
        if missing:
            raise ValueError(f'Dataset archive missing required arrays: {sorted(missing)}')
        data: dict[str, Any] = {k: archive[k] for k in archive.files}

    # Validate years integer sorted unique 1900..2100
    years = data['years']
    if years.ndim != 1 or len(years) < 1:
        raise ValueError('Invalid years array: must be 1D array with at least 1 year')
    if not np.issubdtype(years.dtype, np.integer):
        raise ValueError(f'years array must be integer dtype, got {years.dtype}')
    if not np.all(years >= 1900) or not np.all(years <= 2100):
        raise ValueError('years out of permitted range 1900..2100')
    if not np.array_equal(years, np.unique(years)) or not np.all(np.diff(years) > 0):
        raise ValueError('years must be strictly sorted and unique')
    n_years = len(years)
    if any(not np.issubdtype(data[name].dtype, np.integer) for name in ('month', 'hour')):
        raise ValueError('month and hour arrays must have integer dtype')

    # Validate valid dtype bool strictly (do not coerce integers)
    valid = data['valid']
    if valid.dtype != bool and valid.dtype != np.bool_:
        raise ValueError(f'valid array must have boolean dtype, got {valid.dtype}')

    # Validate shapes
    expected_shapes = {
        'power': (5, n_years, 8784),
        'beam_loss': (5, n_years, 8784),
        'diffuse_loss': (5, n_years, 8784),
        'load_civil': (n_years, 8784),
        'load_standard': (n_years, 8784),
        'month': (n_years, 8784),
        'hour': (n_years, 8784),
        'temperature': (n_years, 8784),
        'valid': (n_years, 8784),
    }
    for name, shape in expected_shapes.items():
        if data[name].shape != shape:
            raise ValueError(f'Array {name} has shape {data[name].shape}, expected {shape}')

    # Validate float arrays are finite
    for name in ('power', 'beam_loss', 'diffuse_loss', 'load_civil', 'load_standard', 'temperature'):
        if not np.all(np.isfinite(data[name])):
            raise ValueError(f'Array {name} contains non-finite (NaN/Inf) values')

    # Nonnegative physical arrays
    for name in ('power', 'beam_loss', 'diffuse_loss', 'load_civil', 'load_standard'):
        if float(data[name].min()) < -1e-6:
            raise ValueError(f'Array {name} contains negative values (min={float(data[name].min())})')

    # beam + diffuse <= power + epsilon
    loss_sum = data['beam_loss'] + data['diffuse_loss']
    if float((loss_sum - data['power']).max()) > 1e-5:
        raise ValueError('Sum of beam_loss and diffuse_loss exceeds total power beyond tolerance epsilon')

    # Full calendar valid coverage and actual valid slot month/hour matching
    for yi, y in enumerate(years):
        y_int = int(y)
        is_leap = calendar.isleap(y_int)
        hours_in_year = 8784 if is_leap else 8760
        if not np.all(valid[yi, :hours_in_year]):
            raise ValueError(f'Year {y_int} has incomplete valid hour coverage ({np.count_nonzero(valid[yi, :hours_in_year])}/{hours_in_year})')
        if np.any(valid[yi, hours_in_year:]):
            raise ValueError(f'Year {y_int} has invalid active trailing slots beyond {hours_in_year}')

        # Using datetime directly
        import datetime as dt_mod
        base_naive = dt_mod.datetime(y_int, 1, 1, 0, 10)
        for h in range(hours_in_year):
            if valid[yi, h]:
                local = base_naive + dt_mod.timedelta(hours=h + 1)
                if data['month'][yi, h] != local.month or data['hour'][yi, h] != local.hour:
                    raise ValueError(f'Slot {h} in year {y_int} has mismatching calendar month/hour: ({data["month"][yi, h]},{data["hour"][yi, h]}) vs expected ({local.month},{local.hour})')

    for yi, year in enumerate(years):
        n = 8784 if calendar.isleap(int(year)) else 8760
        for name in REQUIRED_ARRAYS - {'years', 'valid'}:
            a = data[name]
            tail = a[:, yi, n:] if a.ndim == 3 else a[yi, n:]
            count = int(np.count_nonzero(tail))
            if count:
                raise ValueError(f'Legacy dataset {name}: {count} nonzero trailing values')

    # Validate audit metadata
    try:
        audit = json.loads(audit_path.read_text(encoding='utf-8'))
        if not isinstance(audit, dict):
            raise ValueError('Audit metadata must be a JSON object')
    except Exception as e:
        raise ValueError(f'Malformed audit.json: {e}') from e

    data['audit'] = audit
    return data


def load_active_weather_dataset() -> tuple[dict[str, Any], tuple[str, str, str]]:
    if ACTIVE_POINTER_FILE.exists():
        try:
            raw = json.loads(ACTIVE_POINTER_FILE.read_text(encoding='utf-8'))
        except Exception as e:
            raise ValueError(f'Malformed weather-active.json pointer: {e}') from e

        if not isinstance(raw, dict):
            raise ValueError('weather-active.json must be a JSON object')

        for key in ('dataset_dir', 'sha256_npz', 'sha256_audit', 'id'):
            if key not in raw or not isinstance(raw[key], str) or not raw[key].strip():
                raise ValueError(f'weather-active.json missing valid {key!r} string attribute')

        candidate_dir = _resolve_candidate_dir(raw['dataset_dir'])
        npz_path = candidate_dir / 'hourly.npz'
        audit_path = candidate_dir / 'audit.json'

        npz_hash = _sha256(npz_path)
        if npz_hash.lower() != raw['sha256_npz'].lower():
            raise ValueError(f'Candidate hourly.npz SHA256 mismatch: got {npz_hash}, expected {raw["sha256_npz"]}')

        audit_hash = _sha256(audit_path)
        if audit_hash.lower() != raw['sha256_audit'].lower():
            raise ValueError(f'Candidate audit.json SHA256 mismatch: got {audit_hash}, expected {raw["sha256_audit"]}')

        identity = (raw['id'], npz_hash, audit_hash)
        data = verify_and_load_dataset(npz_path, audit_path)
        return data, identity

    if not BASELINE_NPZ.is_file() or not BASELINE_AUDIT.is_file():
        raise FileNotFoundError('Baseline weather data files missing')

    baseline_npz_hash = _sha256(BASELINE_NPZ)
    baseline_audit_hash = _sha256(BASELINE_AUDIT)
    identity = ('baseline_19y', baseline_npz_hash, baseline_audit_hash)
    data = verify_and_load_dataset(BASELINE_NPZ, BASELINE_AUDIT)
    return data, identity


def get_active_dataset() -> dict[str, Any]:
    """Return isolated metadata and immutable arrays from the explicit binding."""
    from .runtime_context import current_runtime, mutable_copy
    return mutable_copy(current_runtime().dataset)


def active_weather_dataset_identity() -> tuple[str, str, str]:
    """Return the immutable active weather dataset fingerprint (id, sha256_npz, sha256_audit)."""
    from .runtime_context import current_runtime
    return current_runtime().weather_identity


def _load_v2(npz_path, audit_path, manifest_path, expected_manifest_sha256):
    from .dataset_compile import (MAX_METADATA_BYTES, MAX_ARCHIVE_BYTES,
                                  CALENDAR_VERSION, COMPILER_VERSION, sha256,
                                  check_npz_bounds, validate_arrays)
    from .profiles import strict_json, canonical_json
    import io
    if (not isinstance(expected_manifest_sha256, str) or len(expected_manifest_sha256) != 64 or
            manifest_path is None):
        raise ValueError('v2 requires externally pinned manifest SHA256')
    manifest_path = Path(manifest_path)
    if manifest_path.stat().st_size > MAX_METADATA_BYTES or audit_path.stat().st_size > MAX_METADATA_BYTES:
        raise ValueError('metadata size limit')
    if npz_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError('archive size limit')
    raw = manifest_path.read_bytes()
    if sha256(raw) != expected_manifest_sha256:
        raise ValueError('manifest SHA256 mismatch')
    manifest = strict_json(raw)
    required = {'schema_version', 'compiler_version', 'calendar_version', 'plane_ids', 'years',
                'utc_interval_start', 'provider_minute_offset', 'month_hour_clock', 'civil_clock',
                'sha256_npz', 'sha256_audit', 'profile_digest', 'solar_digest', 'load_digest', 'arrays'}
    if type(manifest) is not dict or set(manifest) != required:
        raise ValueError('invalid v2 manifest fields')
    if (type(manifest['schema_version']) is not int or manifest['schema_version'] != 2 or
            manifest['calendar_version'] != CALENDAR_VERSION or manifest['compiler_version'] != COMPILER_VERSION or
            manifest['utc_interval_start'] != 'YYYY-01-01T00:00:00Z + index * PT1H' or
            manifest['month_hour_clock'] != 'fixed_utc_plus_1' or manifest['civil_clock'] != 'Europe/Rome' or
            type(manifest['provider_minute_offset']) is not int or not 0 <= manifest['provider_minute_offset'] < 60):
        raise ValueError('unsupported manifest version/calendar')
    from .profiles import identifier
    for name in ('sha256_npz', 'sha256_audit', 'profile_digest', 'solar_digest', 'load_digest'):
        identifier(manifest[name], name, digest=True)
    archive_raw, audit_raw = npz_path.read_bytes(), audit_path.read_bytes()
    if sha256(archive_raw) != manifest['sha256_npz'] or sha256(audit_raw) != manifest['sha256_audit']:
        raise ValueError('archive/audit SHA256 mismatch')
    check_npz_bounds(archive_raw)
    with np.load(io.BytesIO(archive_raw), allow_pickle=False) as archive:
        data = {name: archive[name] for name in archive.files}
    validate_arrays(data, manifest['years'], manifest['plane_ids'])
    actual_schema = {k: dict(dtype=v.dtype.name, shape=v.shape) for k, v in data.items()}
    if canonical_json(actual_schema) != canonical_json(manifest['arrays']):
        raise ValueError('manifest array schema mismatch')
    audit = strict_json(audit_raw)
    audit_fields = {'dataset_schema_version', 'annual_kwh', 'monthly_kwh', 'baseline_cooling_kwh',
                    'projection', 'synthesized_fraction', 'acknowledgement_required', 'acknowledged',
                    'location', 'plane_ids'}
    if (type(audit) is not dict or set(audit) not in (audit_fields, audit_fields | {'solar_source'}) or
            type(audit['dataset_schema_version']) is not int or audit['dataset_schema_version'] != 2 or
            audit['plane_ids'] != manifest['plane_ids'] or audit['acknowledged'] is not True or
            audit['acknowledgement_required'] is not True or audit['synthesized_fraction'] != 1.0):
        raise ValueError('invalid v2 audit schema')
    if 'solar_source' in audit:
        source = audit['solar_source']
        if (type(source) is not dict or set(source) not in
                ({'file', 'sha256', 'contract'}, {'file', 'sha256', 'contract', 'gap_records'}) or
                source['file'] != 'solar-source.json' or source['sha256'] != manifest['solar_digest'] or
                source['contract'] not in ('pvgis6-sarah3-openmeteo-era5-v1',
                                           'pvgis53-pvgis6-sarah3-openmeteo-era5-v2')):
            raise ValueError('invalid solar source metadata')
        source_path = manifest_path.parent / 'solar-source.json'
        if source_path.is_symlink() or source_path.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ValueError('invalid solar source size/path')
        source_raw = source_path.read_bytes()
        if sha256(source_raw) != source['sha256']:
            raise ValueError('solar source SHA256 mismatch')
        provenance = strict_json(source_raw)
        if type(provenance) is not dict or provenance.get('contract') != source['contract']:
            raise ValueError('invalid solar source contract')
        if 'gap_records' in source:
            entries = provenance.get('solar')
            if type(entries) is not list:
                raise ValueError('invalid solar gap provenance')
            keys = ('year', 'plane_id', 'gap_method', 'gap_counts',
                    'gap_timestamps_utc', 'zero_fill_sensitivity')
            expected = [{k: entry[k] for k in keys if k in entry}
                        for entry in entries if isinstance(entry, dict) and 'gap_method' in entry]
            if canonical_json(source['gap_records']) != canonical_json(expected):
                raise ValueError('solar gap audit/provenance mismatch')
    from .profiles import number
    number(audit['baseline_cooling_kwh'], 'baseline_cooling_kwh')
    monthly = audit.get('monthly_kwh')
    if (type(monthly) is not list or len(monthly) != 12 or
            any(type(x) not in (int, float) or not np.isfinite(x) or x < 0 for x in monthly) or
            audit.get('annual_kwh') != sum(monthly)):
        raise ValueError('invalid audit energy totals')
    from .load_projection import projection_digest
    if projection_digest(manifest['years'], monthly, audit['projection'],
                         (data['load_civil'], data['load_standard']), True) != manifest['load_digest']:
        raise ValueError('projection digest mismatch')
    for name in ('load_civil', 'load_standard'):
        for yi in range(len(manifest['years'])):
            for month, target in enumerate(monthly, 1):
                if not np.isclose(data[name][yi, data['month'][yi] == month].sum(), target,
                                  rtol=1e-12, atol=1e-9):
                    raise ValueError('audit monthly energy mismatch')
    from .load_projection import immutable_array
    data = {name: immutable_array(value) for name, value in data.items()}
    data['audit'] = audit
    return data
