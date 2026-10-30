"""Pure bounded v2 dataset compilation and explicit atomic candidate writing.

Provider fetching, NPZ uploads and active-pointer changes are deliberately absent.
SolarArtifact is a trusted validated provider-adapter result, never a user NPZ.
The dataset schema is independent of HouseholdProfileV1's metadata-only readiness
contract; activation/service wiring is not performed by this module.
"""
from dataclasses import dataclass
import hashlib
import io
import math
import os
from pathlib import Path
import shutil
import tempfile
from types import MappingProxyType
import zipfile
import numpy as np

from .load_projection import (LoadProjection, checked_years, immutable_array, utc_slots,
                              STANDARD, projection_digest, _totals)
from .profiles import canonical_json, parse_profile, profile_to_dict, identifier

DATASET_SCHEMA_VERSION = 2
CALENDAR_VERSION = 'utc_hourly_v2'
COMPILER_VERSION = 'dataset-compiler-v2'
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 128 * 1024 * 1024
FLOAT_ARRAYS = ('power', 'beam_loss', 'diffuse_loss', 'load_civil', 'load_standard', 'temperature')
ARRAY_NAMES = (*FLOAT_ARRAYS, 'month', 'hour', 'valid', 'years')


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class SolarArtifact:
    years: tuple[int, ...]
    plane_ids: tuple[str, ...]
    latitude: float
    longitude: float
    power: np.ndarray                 # hourly kWh proxy per 1 kWp
    beam_loss: np.ndarray
    diffuse_loss: np.ndarray
    temperature: np.ndarray
    source_digest: str
    provider_minute_offset: int
    source_provenance: bytes | None = None  # optional exact provider time/source contract


@dataclass(frozen=True)
class DatasetManifest:
    """Immutable artifacts; digest pins manifest, audit, archive and array schema."""
    arrays: object
    archive_bytes: bytes
    audit_bytes: bytes
    manifest_bytes: bytes
    digest: str
    source_bytes: bytes | None = None

    def write_candidate(self, destination):
        """Publish a complete new directory or nothing; never replace/activate one."""
        from .weather_dataset import verify_and_load_dataset
        destination = Path(destination)
        if destination.exists() or destination.is_symlink():
            raise ValueError('candidate destination already exists')
        staging = Path(tempfile.mkdtemp(prefix='.compile-', dir=destination.parent))
        try:
            for name, raw in (('hourly.npz', self.archive_bytes), ('audit.json', self.audit_bytes),
                              ('manifest.json', self.manifest_bytes),
                              *((('solar-source.json', self.source_bytes),) if self.source_bytes is not None else ())):
                with (staging / name).open('xb') as f:
                    os.chmod(staging / name, 0o600)
                    f.write(raw)
                    f.flush()
                    os.fsync(f.fileno())
            verify_and_load_dataset(staging / 'hourly.npz', staging / 'audit.json',
                                    staging / 'manifest.json', self.digest)
            # Reserve exclusively, then atomically replace our own empty
            # reservation with the complete verified directory.
            destination.mkdir(mode=0o700)
            try:
                os.rename(staging, destination)
            except BaseException:
                destination.rmdir()
                raise
            fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            return destination
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def validate_arrays(data, years, plane_ids):
    """Strict v2 physical/calendar contract, including R=0 and all padding."""
    years = checked_years(years)
    if (type(plane_ids) not in (list, tuple) or len(plane_ids) > 12 or
            len(set(plane_ids)) != len(plane_ids) or
            any(type(x) is not str or not x for x in plane_ids) or set(data) != set(ARRAY_NAMES)):
        raise ValueError('invalid planes or extra/missing arrays')
    for plane in plane_ids:
        identifier(plane, 'plane id')
    ny, nr = len(years), len(plane_ids)
    for name in ARRAY_NAMES:
        a = data[name]
        dtype = np.dtype('float64' if name in FLOAT_ARRAYS else
                         'int32' if name == 'years' else 'bool' if name == 'valid' else 'int16')
        shape = ((ny,) if name == 'years' else (nr, ny, 8784) if name in
                 ('power', 'beam_loss', 'diffuse_loss') else (ny, 8784))
        if not isinstance(a, np.ndarray) or a.dtype != dtype or a.shape != shape:
            raise ValueError('invalid shape/dtype: ' + name)
        if not np.all(np.isfinite(a)):
            raise ValueError('non-finite array: ' + name)
    if not np.array_equal(data['years'], years):
        raise ValueError('year alignment mismatch')
    for name in FLOAT_ARRAYS[:-1]:
        if np.any(data[name] < -1e-6):
            raise ValueError('negative physical array: ' + name)
    if np.any(data['beam_loss'] + data['diffuse_loss'] - data['power'] > 1e-5):
        raise ValueError('loss exceeds power')
    for yi, year in enumerate(years):
        slots = utc_slots(year)
        n = len(slots)
        if not np.all(data['valid'][yi, :n]) or np.any(data['valid'][yi, n:]):
            raise ValueError('incomplete weather coverage or active padding')
        for name in ARRAY_NAMES:
            if name == 'years':
                continue
            tail = data[name][:, yi, n:] if data[name].ndim == 3 else data[name][yi, n:]
            if np.any(tail != 0):
                raise ValueError('nonzero trailing values: ' + name)
        for name in ('month', 'hour'):
            expected = [getattr(t.astimezone(STANDARD), name) for t in slots]
            if not np.array_equal(data[name][yi, :n], expected):
                raise ValueError('calendar mismatch: ' + name)


def check_npz_bounds(raw):
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ValueError('archive size limit')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if (len(entries) != len(ARRAY_NAMES) or len({x.filename for x in entries}) != len(entries) or
                {x.filename for x in entries} != {x + '.npy' for x in ARRAY_NAMES} or
                sum(x.file_size for x in entries) > MAX_DECOMPRESSED_BYTES):
            raise ValueError('archive entries/decompressed size limit')
        # ZIP sizes alone do not bound an NPY header's claimed allocation.
        for entry in entries:
            with archive.open(entry) as stream:
                version = np.lib.format.read_magic(stream)
                if version == (1, 0):
                    shape, _, dtype = np.lib.format.read_array_header_1_0(stream, max_header_size=4096)
                elif version == (2, 0):
                    shape, _, dtype = np.lib.format.read_array_header_2_0(stream, max_header_size=4096)
                else:
                    raise ValueError('unsupported NPY header version')
                size = math.prod(shape) * dtype.itemsize
                if (dtype.hasobject or len(shape) > 3 or
                        any(x < 0 or x > 8784 for x in shape) or
                        size > MAX_DECOMPRESSED_BYTES or stream.tell() + size != entry.file_size):
                    raise ValueError('invalid NPY dtype/shape/allocation')


def compile_dataset(profile, solar_artifact, load_projection):
    """Compile an explicit profile and aligned complete weather/model artifacts."""
    profile = parse_profile(profile_to_dict(profile)) if not isinstance(profile, dict) else parse_profile(profile)
    s, load = solar_artifact, load_projection
    if not isinstance(s, SolarArtifact) or not isinstance(load, LoadProjection):
        raise ValueError('validated solar and load artifacts required')
    years = checked_years(s.years)
    if tuple(load.years) != years:
        raise ValueError('solar/load year alignment mismatch')
    if (s.latitude != profile.location.latitude or s.longitude != profile.location.longitude or
            tuple(s.plane_ids) != tuple(r.id for r in profile.roofs if r.id in s.plane_ids)):
        raise ValueError('profile location/plane alignment mismatch')
    if (type(s.provider_minute_offset) is not int or not 0 <= s.provider_minute_offset < 60 or
            type(s.source_digest) is not str or len(s.source_digest) != 64 or
            any(c not in '0123456789abcdef' for c in s.source_digest)):
        raise ValueError('explicit source digest/provider offset required')
    if (load.acknowledgement_required is not True or load.acknowledged is not True or
            not profile.consumption.acknowledged):
        raise ValueError('projection acknowledgement required')
    _totals(load.monthly_totals)
    if (load.synthesized_fraction != 1.0 or load.digest != projection_digest(
            years, load.monthly_totals, load.provenance,
            (load.load_civil, load.load_standard), load.acknowledged)):
        raise ValueError('load projection digest mismatch')
    mode = load.provenance.get('mode')
    if (mode not in ('measured', 'synthetic') or
            (mode == 'synthetic' and profile.consumption.projection_policy != 'synthetic_v1') or
            (mode == 'measured' and (profile.consumption.kind != 'interval' or
             profile.consumption.projection_policy not in ('civil_month_hour', 'standard_month_hour')))):
        raise ValueError('profile projection policy mismatch')
    if 'baseline_cooling_kwh' not in profile.default_settings:
        raise ValueError('explicit baseline_cooling_kwh required; no legacy estimator')
    # No conversion can conceal object arrays, wrong dtypes or invalid padding.
    data = {name: getattr(s, name) for name in ('power', 'beam_loss', 'diffuse_loss', 'temperature')}
    data.update(load_civil=load.load_civil, load_standard=load.load_standard,
                years=np.array(years, dtype=np.int32),
                valid=np.zeros((len(years), 8784), dtype=bool),
                month=np.zeros((len(years), 8784), dtype=np.int16),
                hour=np.zeros((len(years), 8784), dtype=np.int16))
    for yi, year in enumerate(years):
        slots = utc_slots(year)
        data['valid'][yi, :len(slots)] = True
        for name in ('month', 'hour'):
            data[name][yi, :len(slots)] = [getattr(t.astimezone(STANDARD), name) for t in slots]
    validate_arrays(data, years, s.plane_ids)
    data = {name: immutable_array(value) for name, value in data.items()}
    for name in ('load_civil', 'load_standard'):
        for yi in range(len(years)):
            for month, target in enumerate(load.monthly_totals, 1):
                if not np.isclose(data[name][yi, data['month'][yi] == month].sum(), target,
                                  rtol=1e-12, atol=1e-9):
                    raise ValueError('load monthly energy mismatch')
    audit = dict(dataset_schema_version=2, annual_kwh=sum(load.monthly_totals),
                 monthly_kwh=load.monthly_totals, baseline_cooling_kwh=profile.default_settings['baseline_cooling_kwh'],
                 projection=dict(load.provenance), synthesized_fraction=load.synthesized_fraction,
                 acknowledgement_required=load.acknowledgement_required,
                 acknowledged=load.acknowledged,
                 location=dict(latitude=s.latitude, longitude=s.longitude), plane_ids=s.plane_ids)
    if s.source_provenance is not None:
        if (type(s.source_provenance) is not bytes or len(s.source_provenance) > MAX_ARCHIVE_BYTES or
                sha256(s.source_provenance) != s.source_digest):
            raise ValueError('solar source provenance digest/size mismatch')
        audit['solar_source'] = dict(file='solar-source.json', sha256=s.source_digest,
                                     contract='pvgis6-sarah3-openmeteo-era5-v1')
    audit_raw = canonical_json(audit)
    archive = io.BytesIO()
    np.savez_compressed(archive, **data)
    archive_raw = archive.getvalue()
    check_npz_bounds(archive_raw)
    manifest = dict(schema_version=2, compiler_version=COMPILER_VERSION, calendar_version=CALENDAR_VERSION,
                    plane_ids=s.plane_ids, years=years, utc_interval_start='YYYY-01-01T00:00:00Z + index * PT1H',
                    provider_minute_offset=s.provider_minute_offset, month_hour_clock='fixed_utc_plus_1',
                    civil_clock='Europe/Rome', sha256_npz=sha256(archive_raw), sha256_audit=sha256(audit_raw),
                    profile_digest=sha256(canonical_json(profile)), solar_digest=s.source_digest,
                    load_digest=load.digest,
                    arrays={k: dict(dtype=v.dtype.name, shape=v.shape) for k, v in data.items()})
    raw = canonical_json(manifest)
    if max(len(raw), len(audit_raw)) > MAX_METADATA_BYTES:
        raise ValueError('metadata size limit')
    return DatasetManifest(MappingProxyType({k: immutable_array(v) for k, v in data.items()}),
                           archive_raw, audit_raw, raw, sha256(raw), s.source_provenance)
