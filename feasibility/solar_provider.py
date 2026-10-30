"""PVGIS 6 solar and explicitly selected historical temperature; no runtime defaults.

Contract: pvgis6-sarah3-openmeteo-era5-v1. See solar_candidate module help for
source/time semantics. No 5.3, per-hour repair, or implicit model fallback.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import http.client
import json
import math
import multiprocessing
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from urllib.parse import urlencode, urlsplit

import numpy as np
from .dataset_compile import SolarArtifact
from .load_projection import checked_years, immutable_array, utc_slots
from .profiles import canonical_json, parse_profile, profile_to_dict, strict_json

PVGIS_URL = 'https://photovoltaic-geographic-information-system.ec.europa.eu/api/v6/power/broadband'
TEMPERATURE_URL = 'https://archive-api.open-meteo.com/v1/archive'
CONTRACT = 'pvgis6-sarah3-openmeteo-era5-v1'
TIME_POLICY = 'pvgis6-sarah3-utc-observation-v1'
SOURCE_POLICY = dict(solar='pvgis6_sarah3', temperature='openmeteo_era5', hour_mapping=TIME_POLICY)
MAX_RESPONSE_BYTES = 128 * 1024 * 1024
MAX_ATTEMPTS = 3
_HOUR_NS = 3600 * 10**9
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_POOL = threading.BoundedSemaphore(2)


@dataclass(frozen=True)
class Response:
    status: int
    url: str
    body: bytes


class RateLimiter:
    def __init__(self, clock=time.monotonic, sleep=time.sleep):
        self.clock, self.sleep = clock, sleep
        self.lock = threading.Lock()
        self.last = None

    def acquire(self):
        with self.lock:
            if self.last is not None:
                self.sleep(max(0, 1 - (self.clock() - self.last)))
            self.last = self.clock()


_LIMITER = RateLimiter()


def _http_child(pipe, endpoint, parameters, connect_timeout, read_timeout, total_timeout, max_bytes):
    """Killable I/O process also bounds DNS/TLS, which socket timeouts alone cannot."""
    conn = None
    try:
        u = urlsplit(endpoint)
        start = time.monotonic()
        conn = http.client.HTTPSConnection(u.hostname, timeout=connect_timeout)
        conn.connect()
        remaining = total_timeout - (time.monotonic() - start)
        if remaining <= 0:
            raise TimeoutError()
        conn.sock.settimeout(min(read_timeout, remaining))
        conn.request('GET', u.path + '?' + urlencode(parameters), headers={'Accept': 'application/json', 'Accept-Encoding': 'identity'})
        response = conn.getresponse()
        length = response.getheader('Content-Length')
        if length is not None and (not length.isdigit() or int(length) > max_bytes):
            raise ValueError('response size limit')
        chunks, size = [], 0
        while True:
            remaining = total_timeout - (time.monotonic() - start)
            if remaining <= 0:
                raise TimeoutError()
            conn.sock.settimeout(min(read_timeout, remaining))
            chunk = response.read(min(65536, max_bytes + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > max_bytes:
                raise ValueError('response size limit')
        # http.client never follows redirects.
        pipe.send(('ok', response.status, b''.join(chunks)))
    except (TimeoutError, OSError, http.client.HTTPException):
        pipe.send(('timeout', 0, b''))
    except Exception:
        pipe.send(('invalid', 0, b''))
    finally:
        if conn is not None:
            conn.close()
        pipe.close()


def network_transport(endpoint, parameters, *, connect_timeout, read_timeout, total_timeout, max_bytes):
    if endpoint not in (PVGIS_URL, TEMPERATURE_URL):
        raise ValueError('unapproved endpoint')
    ctx = multiprocessing.get_context('spawn')
    receiver, sender = ctx.Pipe(duplex=False)
    worker = ctx.Process(target=_http_child, args=(sender, endpoint, parameters, connect_timeout,
                                                 read_timeout, total_timeout, max_bytes))
    start = time.monotonic()
    worker.start()
    sender.close()
    try:
        if not receiver.poll(max(0, total_timeout - (time.monotonic() - start))):
            raise TimeoutError('provider total deadline')
        kind, status, body = receiver.recv()
        if kind == 'timeout':
            raise TimeoutError('provider I/O deadline')
        if kind != 'ok':
            raise ValueError('invalid provider response')
        return Response(status, endpoint, body)
    finally:
        receiver.close()
        if worker.is_alive():
            worker.terminate()
        worker.join(timeout=1)
        if worker.is_alive():
            worker.kill()
            worker.join()


class ProviderClient:
    def __init__(self, transport=None, *, clock=time.monotonic, sleep=time.sleep, limiter=None):
        self.transport = transport or network_transport
        self.clock, self.sleep = clock, sleep
        self.limiter = _LIMITER if limiter is None else limiter

    def get_json(self, endpoint, parameters):
        if endpoint not in (PVGIS_URL, TEMPERATURE_URL):
            raise ValueError('unapproved endpoint')
        for attempt in range(MAX_ATTEMPTS):
            with _POOL:
                self.limiter.acquire()
                start = self.clock()
                response = self.transport(endpoint, dict(parameters), connect_timeout=10.0,
                                          read_timeout=30.0, total_timeout=120.0,
                                          max_bytes=MAX_RESPONSE_BYTES)
                if self.clock() - start > 120:
                    raise TimeoutError('provider total deadline')
            if response.url != endpoint or 300 <= response.status < 400:
                raise ValueError('provider redirect rejected')
            if not isinstance(response.body, bytes) or len(response.body) > MAX_RESPONSE_BYTES:
                raise ValueError('response size limit')
            if response.status in (429, 529) or 500 <= response.status <= 599:
                if attempt + 1 == MAX_ATTEMPTS:
                    raise ValueError('provider retry limit')
                self.sleep(2**attempt)
                continue
            if response.status != 200:
                raise ValueError('provider request failed')
            try:
                value = strict_json(response.body)
            except (ValueError, UnicodeError):
                raise ValueError('invalid provider JSON') from None
            if type(value) is not dict:
                raise ValueError('provider JSON object required')
            return value
        raise AssertionError('unreachable')


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _private_dir(path, *, allow_shared=False):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError('symlink cache directory')
    if not allow_shared and (path.stat().st_mode & 0o077 or path.stat().st_uid != os.getuid()):
        raise ValueError('owner-only cache directory required')
    return path


def _atomic(path, raw):
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class TenantCache:
    """Local CLI ownership namespace; future service must supply its verified tenant ID."""
    def __init__(self, root, tenant_id):
        if type(tenant_id) is not str or re.fullmatch('[a-f0-9]{32}', tenant_id) is None:
            raise ValueError('opaque tenant id required')
        root = Path(root).resolve()
        repo = Path(__file__).resolve().parent.parent
        if root == repo or repo in root.parents:
            raise ValueError('cache must be external to repository')
        self.directory = _private_dir(_private_dir(_private_dir(root) / 'tenants') / tenant_id)
        self.directory = _private_dir(self.directory / 'solar-cache')

    def get(self, client, endpoint, parameters):
        key = _digest(canonical_json(dict(contract=CONTRACT, endpoint=endpoint, parameters=parameters)))
        path = self.directory / (key + '.json')
        if path.is_symlink():
            raise ValueError('symlink cache entry')
        if path.exists():
            if path.stat().st_size > MAX_RESPONSE_BYTES:
                raise ValueError('cache size limit')
            obj = strict_json(path.read_bytes())
            if (obj.get('key') != key or obj.get('sha256') != _digest(canonical_json(obj.get('response'))) or
                    type(obj.get('response')) is not dict):
                raise ValueError('cache integrity mismatch')
            return obj['response']
        value = client.get_json(endpoint, parameters)
        raw = canonical_json(dict(key=key, sha256=_digest(canonical_json(value)), response=value))
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError('cache size limit')
        _atomic(path, raw)
        return value


def _number(value, label, minimum=None, maximum=None):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('invalid ' + label)
    if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        raise ValueError('invalid ' + label)
    return float(value)


def timestamp_ns(raw):
    """Strict ISO timestamp, retaining up to nanoseconds before UTC conversion."""
    if type(raw) is not str:
        raise ValueError('invalid timestamp')
    m = re.fullmatch(r'(\d{4}-\d\d-\d\d[T ]\d\d:\d\d(?::\d\d)?)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)?', raw)
    if m is None:
        raise ValueError('invalid timestamp')
    base = datetime.fromisoformat(m[1] + (m[3] or '+00:00').replace('Z', '+00:00'))
    delta = base.astimezone(timezone.utc) - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 10**9 + int((m[2] or '').ljust(9, '0'))


def _sequence(times, year, solar):
    slots = utc_slots(year)
    if type(times) is not list or len(times) != len(slots):
        raise ValueError('incomplete year timestamps')
    ns = [timestamp_ns(t) for t in times]
    starts = [int((t - _EPOCH).total_seconds()) * 10**9 for t in slots]
    offsets = {a - b for a, b in zip(ns, starts)}
    if len(set(n // _HOUR_NS for n in ns)) != len(ns):
        raise ValueError('duplicate canonical hour')
    if len(offsets) != 1:
        raise ValueError('missing hour or irregular observation sequence')
    offset = next(iter(offsets))
    if not (0 <= offset < _HOUR_NS) or (not solar and offset != 0):
        raise ValueError('unsupported timestamp convention')
    return offset


def _array(value, size, label, nonnegative=True):
    if type(value) is not list or len(value) != size:
        raise ValueError('invalid array length: ' + label)
    if any(type(v) not in (int, float) for v in value):
        raise ValueError('invalid numeric array: ' + label)
    a = np.array(value, dtype=np.float64)
    if not np.all(np.isfinite(a)) or (nonnegative and np.any(a < 0)):
        raise ValueError('invalid physical array: ' + label)
    return a


@dataclass(frozen=True)
class SolarRequest:
    profile: object
    years: tuple
    loss_percent: float
    mounting: str
    technology: str
    source_policy: object
    cache: object = None
    progress: object = None


@dataclass(frozen=True)
class SolarSource:
    artifact: SolarArtifact
    provenance_bytes: bytes


def _solar_parameters(profile, plane, year, loss, mounting, technology):
    # v6 uses north-clockwise compass orientation directly, NOT v5 south aspect.
    return dict(latitude=profile.location.latitude, longitude=profile.location.longitude,
                surface_tilt=plane.tilt_deg, surface_orientation=plane.azimuth_deg % 360,
                start_time=f'{year}-01-01T00:00:00', end_time=f'{year}-12-31T23:59:59',
                timezone='UTC', frequency='Hourly', irradiance_source='SARAH-3',
                photovoltaic_module=f'{technology}:{mounting}', photovoltaic_module_type='Mono-Facial',
                system_efficiency=1-loss/100, **{'peak-power': 1}, horizon_profile='PVGIS',
                shading_model='PVGIS', angle_output_units='Degrees', verbose=8,
                surface_position_optimisation_mode='None')


def _validate_solar(obj, parameters, plane):
    if (obj.get('timezone', 'UTC') not in ('UTC', 'Etc/UTC', 'GMT') or
            obj.get('frequency', 'h') not in ('h', 'Hourly') or obj.get('power_unit', 'W') != 'W'):
        raise ValueError('unsupported provider time/power convention')
    unit = obj.get('unit')
    if unit not in ('degrees', 'Degrees', 'radians', 'Radians'):
        raise ValueError('missing angle unit')
    scale = 180 / math.pi if unit.lower() == 'radians' else 1
    tilt = _number(obj.get('surface_tilt'), 'returned tilt') * scale
    az = _number(obj.get('surface_orientation'), 'returned orientation') * scale
    if abs(tilt-plane.tilt_deg) > .01 or abs((az-plane.azimuth_deg+180) % 360-180) > .01:
        raise ValueError('plane metadata mismatch')
    if obj.get('peak_power_unit') != 'kWp' or abs(_number(obj.get('peak_power'), 'peak power')-1) > 1e-4:
        raise ValueError('power metadata mismatch')
    if (obj.get('technology') != parameters['photovoltaic_module'] or
            abs(_number(obj.get('system_efficiency'), 'system efficiency')-parameters['system_efficiency']) > 1e-6 or
            obj.get('shading') != 'PVGIS' or obj.get('azimuth_origin') != 'North'):
        raise ValueError('equipment/horizon metadata mismatch')
    confirmed = {}
    inputs = obj.get('inputs', {})
    if type(inputs) is not dict or type(inputs.get('location', {})) is not dict:
        raise ValueError('invalid returned location metadata')
    for coordinates in (obj, inputs, inputs.get('location', {})):
        for key in ('latitude', 'longitude'):
            if key in coordinates:
                if abs(_number(coordinates[key], 'returned coordinates') - parameters[key]) > 1e-4:
                    raise ValueError('coordinate mismatch')
                confirmed[key] = coordinates[key]
    return dict(provider_confirmed_coordinates=confirmed,
                coordinate_authority='provider' if len(confirmed) == 2 else 'validated_request',
                provider_confirmed_equipment=dict(technology=obj['technology'], peak_power=obj['peak_power'],
                    system_efficiency=obj['system_efficiency'], shading=obj['shading'],
                    surface_tilt=obj['surface_tilt'], surface_orientation=obj['surface_orientation'], unit=unit))


def fetch_pvgis(request, transport=None):
    """Fetch complete v6 SARAH3 years plus explicit Open-Meteo ERA5 temperature.

    Only the known UTC hourly observation policy is accepted. We validate every
    sequence before associating observations with slots; no arbitrary floor,
    sorting, shifting, interpolation or per-hour/provider fallback is performed.
    """
    if not isinstance(request, SolarRequest) or dict(request.source_policy) != SOURCE_POLICY:
        raise ValueError('explicit supported mixed-source policy required')
    profile = parse_profile(request.profile if isinstance(request.profile, dict) else profile_to_dict(request.profile))
    years = checked_years(request.years)
    loss = _number(request.loss_percent, 'loss', 0, 100)
    if request.mounting not in ('Free standing 2025', 'Integrated 2025') or request.technology != 'cSi':
        raise ValueError('unsupported equipment contract')
    client = transport if isinstance(transport, ProviderClient) else ProviderClient(transport)
    get = (lambda endpoint, params: request.cache.get(client, endpoint, params)) if request.cache is not None else client.get_json
    progress = request.progress or (lambda *args: None)
    nr, ny = len(profile.roofs), len(years)
    power = np.zeros((nr, ny, 8784)); temperature = np.zeros((ny, 8784))
    provenance = dict(contract=CONTRACT, policy=SOURCE_POLICY, response_digest_format='canonical-json-v1', request_location=dict(
        latitude=profile.location.latitude, longitude=profile.location.longitude),
        solar=[], temperature=[], timestamps_by_year={}, offset_nanoseconds_by_year={},
        limitations=['PVGIS hourly W/1000 is an AC-equivalent hourly energy proxy, not a new DC model.',
                    'Coarse beam_loss=P*shading_fraction, diffuse_loss=0 represents uniform additional attenuation.',
                    'PVGIS terrain horizon and explicit system loss are separate from user shading.',
                    'Historical temperature is separately sourced ERA5 model data on a nearby grid, not measured on-site.',
                    'Provider terms and full operational availability remain unverified; live contract may change.'])
    offsets, solar_times = {}, {}
    for yi, year in enumerate(years):
        n = len(utc_slots(year))
        for ri, plane in enumerate(profile.roofs):
            progress('solar', ri + yi*max(1, nr), ny*max(1, nr))
            params = _solar_parameters(profile, plane, year, loss, request.mounting, request.technology)
            obj = get(PVGIS_URL, params)
            metadata = _validate_solar(obj, params, plane)
            times = obj.get('timestamps')
            offset = _sequence(times, year, True)
            if year in offsets and (offsets[year] != offset or solar_times[year] != times):
                raise ValueError('cross-plane timestamp inconsistency')
            offsets[year], solar_times[year] = offset, times
            p = _array(obj.get('power'), n, 'power')
            for name in ('direct_inclined_irradiance_before_reflectivity',
                         'diffuse_inclined_irradiance_before_reflectivity',
                         'ground_reflected_inclined_irradiance_before_reflectivity'):
                _array(obj.get(name), n, name)
            power[ri, yi, :n] = p / 1000
            provenance['solar'].append(dict(year=year, plane_id=plane.id, endpoint=PVGIS_URL,
                provider='PVGIS', provider_version='6', dataset='SARAH-3',
                request_parameters=params, user_shading_fraction=plane.shading_fraction,
                response_sha256=_digest(canonical_json(obj)), **metadata))
        progress('temperature', yi, ny)
        params = dict(latitude=profile.location.latitude, longitude=profile.location.longitude,
                      start_date=f'{year}-01-01', end_date=f'{year}-12-31', hourly='temperature_2m',
                      timezone='UTC', temperature_unit='celsius', models='era5',
                      cell_selection='nearest', elevation='nan')
        obj = get(TEMPERATURE_URL, params)
        if (type(obj.get('utc_offset_seconds')) is not int or obj['utc_offset_seconds'] != 0 or
                obj.get('timezone', 'GMT') not in ('UTC', 'GMT', 'Etc/UTC') or
                obj.get('hourly_units', {}).get('temperature_2m') != '°C'):
            raise ValueError('temperature UTC/Celsius metadata required')
        grid = {k: _number(obj.get(k), 'temperature grid coordinates') for k in ('latitude', 'longitude')}
        if any(abs(grid[k]-params[k]) > .26 for k in grid):
            raise ValueError('temperature location grid mismatch')
        times = obj.get('hourly', {}).get('time')
        _sequence(times, year, False)
        temperature[yi, :n] = _array(obj.get('hourly', {}).get('temperature_2m'), n, 'temperature', False)
        provenance['temperature'].append(dict(year=year, endpoint=TEMPERATURE_URL, provider='Open-Meteo',
            model='ERA5', request_parameters=params,
            provider_grid_coordinates=grid, response_sha256=_digest(canonical_json(obj)), original_timestamps=times))
    provenance['timestamps_by_year'] = {str(k): v for k, v in solar_times.items()}
    provenance['offset_nanoseconds_by_year'] = {str(k): v for k, v in offsets.items()}
    provenance['minute_field_semantics'] = 'Compatibility minute component of first solar year only; exact times/offsets above are authoritative. Zero for no solar planes.'
    raw = canonical_json(provenance)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError('provenance size limit')
    losses = power * np.array([p.shading_fraction for p in profile.roofs])[:, None, None]
    artifact = SolarArtifact(years, tuple(p.id for p in profile.roofs), profile.location.latitude,
        profile.location.longitude, immutable_array(power), immutable_array(losses),
        immutable_array(np.zeros_like(power)), immutable_array(temperature), _digest(raw),
        int(next(iter(offsets.values()), 0) // (60*10**9)), raw)
    progress('validated', 1, 1)
    return SolarSource(artifact, raw)
