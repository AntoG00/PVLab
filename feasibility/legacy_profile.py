"""Local-only, content-addressed snapshots of already compiled legacy inputs.

This compatibility format is deliberately separate from HouseholdProfileV1:
it preserves the five-plane legacy calendar without rebuilding consumption,
weather or DST. It is not a new-profile compiler or a multi-user activation API.
"""
import hashlib
import io
import json

import numpy as np

from . import assumptions, weather_dataset
from .profile_store import ProfileStore, MAX_ARTIFACT_BYTES, MAX_MANIFEST_BYTES
from .profiles import canonical_json, strict_json, identifier
from .runtime_context import RuntimeContext, initialize_runtime, runtime_if_initialized

CALENDAR_VERSION = 'legacy_pvgis_v1'
COMPILER_VERSION = 'legacy-snapshot-v1'
_FILES = {'weather.npz', 'audit.json', 'metadata.json', 'hourly_prices.npz', 'empirical_csud_prices.npz'}

def legacy_presentation_inputs():
    """Read explicit external compatibility inputs; never infer household evidence."""
    store = ProfileStore()
    with store._directory(()) as fd:
        raw = store._read(fd, 'legacy-presentation.json', MAX_MANIFEST_BYTES)
    value = strict_json(raw)
    if not isinstance(value, dict):
        raise ValueError('Explicit legacy presentation inputs required')
    return value


def legacy_text(name, neutral):
    """Use optional explicitly supplied private prose only for a bound legacy profile."""
    ctx = runtime_if_initialized()
    if ctx is None or ctx.household_profile is not None:
        return neutral
    value = ctx.metadata.get('presentation', {}).get('text', {}).get(name, neutral)
    if not isinstance(value, str) or len(value) > 8192:
        raise ValueError('Invalid legacy presentation text')
    return value


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def import_legacy_profile(root=None, defaults_path=None):
    """Snapshot verified compiled arrays and effective local configuration once.

    Strict pointer failures propagate; this never falls back from a bad active
    pointer. Files are published without replacement; manifest publication is last.
    """
    from . import engine
    presentation = legacy_presentation_inputs()
    dataset, identity = weather_dataset.load_active_weather_dataset()
    archive = io.BytesIO()
    np.savez_compressed(archive, **{k: v for k, v in dataset.items() if isinstance(v, np.ndarray)})
    # Roof calibration pairs ordered yield entries with the original roof list.
    # Canonical key sorting here would silently change those associations.
    audit_raw = json.dumps(dataset['audit'], ensure_ascii=False, allow_nan=False,
                          separators=(',', ':')).encode('utf-8')
    blobs = {'weather.npz': archive.getvalue(), 'audit.json': audit_raw}
    snapshot_path = assumptions.USER_DEFAULTS_PATH if defaults_path is None else defaults_path
    with assumptions._defaults_file_lock(snapshot_path):
        from copy import deepcopy
        effective = deepcopy(presentation['factory_defaults'])
        saved = assumptions._saved_defaults(snapshot_path)
        effective.update({k: deepcopy(v['value']) for k, v in saved.items()
                          if k in assumptions.SPEC and isinstance(v, dict) and 'value' in v})
        assumptions._normalize_battery(effective, set(saved))
        if any(k in saved for k in (*assumptions.SCHEDULE_SETTINGS, *assumptions.LEGACY_HEATING_KEYS)):
            effective.update(assumptions._resolve_saved_schedule(saved,
                effective.get('ac_heating_windows'), effective.get('heating_schedule_mode')))
        records = deepcopy(presentation['factory_records'])
        for record in records:
            if record['name'] in saved:
                entry = saved[record['name']]
                record.update(value=deepcopy(entry['value']), status='provided',
                    source='User-saved shared app default', date_accessed=entry['date'],
                    owner=entry['owner'], factory_default=deepcopy(entry['factory_default']),
                    confidence='User-provided default; not measured or independently verified')
    price_files = {}
    for name in ('hourly_prices.npz', 'empirical_csud_prices.npz'):
        path = engine.HERE / 'data' / name
        if path.exists():
            blobs[name] = path.read_bytes()
            price_files[name] = _digest(blobs[name])
    # Preserve the exact legacy order, including its explicit flat choice and
    # historical missing-file fallback. New profiles cannot use this importer.
    if effective['export_price_profile'] == 'flat_benchmark':
        selected = 'flat_solar_capture_benchmark'
    elif 'hourly_prices.npz' in price_files:
        selected = 'actual_hourly_gme_profile'
    elif 'empirical_csud_prices.npz' in price_files:
        selected = 'empirical_csud_' + effective['export_price_window'] + '_proxy'
    else:
        selected = 'flat_solar_capture_benchmark'
    metadata = {
        'format': 'local-legacy-profile-v1', 'compiler_version': COMPILER_VERSION,
        'calendar_version': CALENDAR_VERSION, 'weather_identity': list(identity),
        'source_planes': list(dataset['audit']['pvgis_yields']),
        'defaults': effective, 'records': records,
        'sizing_reference': presentation['sizing_reference'],
        'presentation': presentation,
        'thermal': {'GAS_MONTHLY': engine.GAS_MONTHLY.tolist(),
                    'COOLING_BALANCE_TEMP': engine.COOLING_BALANCE_TEMP},
        'aliases': {'heat_pump': dict(engine.HP_ALIASES)},
        'calibration': presentation['calibration'],
        'price_source': selected, 'price_files': price_files,
        'price_window': effective['export_price_window'],
        'fiscal_revision': 'legacy-fiscal-v1', 'model_build_id': 'legacy-unbound-v1',
    }
    if len(metadata['source_planes']) != 5:
        raise ValueError('Legacy bootstrap requires the original five source planes')
    blobs['metadata.json'] = canonical_json(metadata)
    manifest = {'format': 'local-legacy-profile-v1',
                'files': {name: _digest(raw) for name, raw in blobs.items()}}
    manifest_raw = canonical_json(manifest)
    revision = _digest(manifest_raw)
    store = ProfileStore(root)
    with store._directory(('legacy-profiles', revision), create=True) as fd:
        for name, raw in (*blobs.items(), ('manifest.json', manifest_raw)):
            if len(raw) > MAX_ARTIFACT_BYTES:
                raise ValueError('Legacy snapshot exceeds artifact limit')
            try:
                store._write_once(fd, name, raw)
            except FileExistsError:
                if store._read(fd, name, MAX_ARTIFACT_BYTES) != raw:
                    raise ValueError('Legacy snapshot publication conflict')
    return revision


def load_legacy_profile(root, revision):
    """Verify a published local revision and construct a pure immutable context."""
    identifier(revision, 'legacy revision', digest=True)
    store = ProfileStore(root)
    with store._directory(('legacy-profiles', revision)) as fd:
        raw = store._read(fd, 'manifest.json', MAX_MANIFEST_BYTES)
        if _digest(raw) != revision:
            raise ValueError('Legacy manifest hash mismatch')
        manifest = strict_json(raw)
        if manifest.get('format') != 'local-legacy-profile-v1':
            raise ValueError('Unsupported legacy profile format')
        entries = manifest.get('files', {})
        if not {'weather.npz', 'audit.json', 'metadata.json'} <= entries.keys() <= _FILES:
            raise ValueError('Invalid legacy snapshot file manifest')
        blobs = {}
        for name, digest in entries.items():
            blobs[name] = store._read(fd, name, MAX_ARTIFACT_BYTES)
            if _digest(blobs[name]) != digest:
                raise ValueError('Legacy artifact hash mismatch')
    metadata = strict_json(blobs['metadata.json'])
    if metadata['calendar_version'] != CALENDAR_VERSION or metadata['compiler_version'] != COMPILER_VERSION:
        raise ValueError('Unsupported legacy calendar/compiler')
    with np.load(io.BytesIO(blobs['weather.npz']), allow_pickle=False) as archive:
        dataset = {k: archive[k] for k in archive.files}
    dataset['audit'] = strict_json(blobs['audit.json'])
    price_data = {}
    verified_prices = {name: entries[name] for name in
                       ('hourly_prices.npz', 'empirical_csud_prices.npz') if name in entries}
    if metadata['price_files'] != verified_prices:
        raise ValueError('Legacy price metadata differs from verified artifacts')
    for name in verified_prices:
        with np.load(io.BytesIO(blobs[name]), allow_pickle=False) as archive:
            price_data[name] = {key: archive[key] for key in archive.files}
    return RuntimeContext(
        owner_id='local-legacy', input_revision=revision,
        dataset_digest=_digest(canonical_json({n: entries[n] for n in ('weather.npz', 'audit.json')})),
        price_revision=_digest(canonical_json({'files': metadata['price_files'],
                                             'source': metadata['price_source'], 'window': metadata['price_window']})),
        defaults_snapshot=metadata['defaults'],
        thermal_reference={**metadata['thermal'],
                           'GAS_MONTHLY': np.asarray(metadata['thermal']['GAS_MONTHLY'], dtype=float)},
        fiscal_revision=metadata['fiscal_revision'], calendar_version=metadata['calendar_version'],
        dataset=dataset, weather_identity=tuple(metadata['weather_identity']),
        records_snapshot=metadata['records'], metadata=metadata, price_data=price_data)


def bootstrap_legacy(root=None, defaults_path=None):
    """Explicit local startup; a second bootstrap cannot replace a bound context."""
    if runtime_if_initialized() is not None:
        raise RuntimeError('Runtime already initialized; bootstrap requires a new process')
    revision = import_legacy_profile(root, defaults_path)
    return initialize_runtime(load_legacy_profile(root, revision))
