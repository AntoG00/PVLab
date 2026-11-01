"""PVGIS 5.3/6 solar and explicitly selected historical temperature.

Contract: pvgis53-pvgis6-sarah3-openmeteo-era5-v2. See solar_candidate for
source and time semantics.
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
PVGIS5_URL = 'https://re.jrc.ec.europa.eu/api/v5_3/seriescalc'
TEMPERATURE_URL = 'https://archive-api.open-meteo.com/v1/archive'
CONTRACT = 'pvgis53-pvgis6-sarah3-openmeteo-era5-v2'
TIME_POLICY = 'pvgis53-pvgis6-sarah3-utc-observation-v2'
SOURCE_POLICY = dict(solar='pvgis53_pvgis6_sarah3', temperature='openmeteo_era5', hour_mapping=TIME_POLICY)
MAX_RESPONSE_BYTES = 128 * 1024 * 1024
MAX_ATTEMPTS = 3
_HOUR_NS = 3600 * 10**9
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_POOL = threading.BoundedSemaphore(2)


class SolarDataGapError(ValueError):
    """Raised when PVGIS data gaps exceed supported repair limits."""


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
    except TimeoutError:
        pipe.send(('timeout', 0, b''))
    except (OSError, http.client.HTTPException):
        pipe.send(('connection_error', 0, b''))
    except Exception:
        pipe.send(('invalid', 0, b''))
    finally:
        if conn is not None:
            conn.close()
        pipe.close()


def network_transport(endpoint, parameters, *, connect_timeout, read_timeout, total_timeout, max_bytes):
    if endpoint not in (PVGIS_URL, PVGIS5_URL, TEMPERATURE_URL):
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
        if kind == 'connection_error':
            raise ConnectionError('provider connection failed')
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
        if endpoint not in (PVGIS_URL, PVGIS5_URL, TEMPERATURE_URL):
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


def _solar_parameters_v5(profile, plane, year, loss, mounting, technology):
    aspect = ((plane.azimuth_deg - 180 + 180) % 360) - 180
    mounting_v5 = 'free' if 'free' in mounting.lower() else 'building'
    tech_v5 = 'crystSi2025' if (technology == 'cSi' and '2025' in mounting) else technology
    return dict(lat=profile.location.latitude,
                lon=profile.location.longitude,
                usehorizon=1,
                raddatabase='PVGIS-SARAH3',
                startyear=year,
                endyear=year,
                pvcalculation=1,
                peakpower=1,
                loss=loss,
                mountingplace=mounting_v5,
                pvtechchoice=tech_v5,
                angle=plane.tilt_deg,
                aspect=aspect,
                components=1, outputformat='json')


def _normalize_v5(obj, params, year):
    if type(obj) is not dict:
        raise ValueError('provider JSON object required')

    inputs = obj.get('inputs')
    if type(inputs) is not dict:
        raise ValueError('invalid returned inputs metadata')

    loc = inputs.get('location')
    if type(loc) is not dict:
        raise ValueError('invalid returned location metadata')
    confirmed_coords = {}
    for k_src, k_param in (('latitude', 'lat'), ('longitude', 'lon')):
        if k_src not in loc:
            raise ValueError(f'missing returned coordinate: {k_src}')
        val = _number(loc[k_src], f'returned coordinate {k_src}')
        if abs(val - params[k_param]) > 1e-4:
            raise ValueError(f'coordinate mismatch: {k_src}')
        confirmed_coords[k_src] = loc[k_src]

    meteo = inputs.get('meteo_data')
    if type(meteo) is not dict:
        raise ValueError('invalid returned meteo_data metadata')
    if meteo.get('radiation_db') != 'PVGIS-SARAH3':
        raise ValueError('radiation_db mismatch')
    ymin = meteo.get('year_min')
    ymax = meteo.get('year_max')
    if type(ymin) is not int or type(ymax) is not int:
        raise ValueError('invalid year_min or year_max')
    if not (ymin <= year <= ymax):
        raise ValueError(f'year {year} outside meteo coverage [{ymin}, {ymax}]')
    use_horizon = meteo.get('use_horizon')
    if use_horizon not in (True, 1):
        raise ValueError('horizon profile not applied')

    ms = inputs.get('mounting_system')
    fixed = ms.get('fixed') if isinstance(ms, dict) else None
    if not isinstance(fixed, dict):
        fixed = inputs.get('fixed')
    if not isinstance(fixed, dict):
        raise ValueError('invalid returned fixed mounting metadata')

    slope_val = fixed.get('slope')
    if isinstance(slope_val, dict):
        slope_val = slope_val.get('value')
    slope = _number(slope_val, 'returned slope')
    if abs(slope - params['angle']) > 0.01:
        raise ValueError('slope metadata mismatch')

    az_val = fixed.get('azimuth')
    if isinstance(az_val, dict):
        az_val = az_val.get('value')
    azimuth = _number(az_val, 'returned azimuth')
    if abs(azimuth - params['aspect']) > 0.01:
        raise ValueError('azimuth metadata mismatch')

    mounting_type = fixed.get('type')
    if type(mounting_type) is not str:
        raise ValueError('missing fixed mounting type')
    req_mounting = params['mountingplace']
    if req_mounting == 'free' and 'free' not in mounting_type.lower():
        raise ValueError('mounting type mismatch')
    if req_mounting == 'building' and 'building' not in mounting_type.lower():
        raise ValueError('mounting type mismatch')

    pv_mod = inputs.get('pv_module')
    if type(pv_mod) is not dict:
        raise ValueError('invalid returned pv_module metadata')
    if pv_mod.get('technology') != 'c-Si2025':
        raise ValueError('technology mismatch: expected c-Si2025')
    peak_power = _number(pv_mod.get('peak_power'), 'returned peak_power')
    if abs(peak_power - params['peakpower']) > 1e-4:
        raise ValueError('peak_power metadata mismatch')
    system_loss = _number(pv_mod.get('system_loss'), 'returned system_loss')
    if abs(system_loss - params['loss']) > 1e-4:
        raise ValueError('system_loss metadata mismatch')

    meta = obj.get('meta')
    if type(meta) is not dict:
        raise ValueError('missing meta in provider response')
    outputs_meta = meta.get('outputs')
    if type(outputs_meta) is not dict:
        raise ValueError('missing outputs in meta')
    hourly_meta = outputs_meta.get('hourly')
    if type(hourly_meta) is not dict:
        raise ValueError('missing hourly in meta.outputs')
    variables = hourly_meta.get('variables')
    if type(variables) is not dict:
        raise ValueError('missing variables in meta.outputs.hourly')

    for var_name, expected_unit in (('P', 'W'), ('Gb(i)', 'W/m2'), ('Gd(i)', 'W/m2'), ('Gr(i)', 'W/m2')):
        var_meta = variables.get(var_name)
        if type(var_meta) is not dict:
            raise ValueError(f'missing variable metadata for {var_name}')
        unit = var_meta.get('units') or var_meta.get('unit')
        if unit != expected_unit:
            raise ValueError(f'unit mismatch for {var_name}: expected {expected_unit}, got {unit}')

    outputs = obj.get('outputs')
    if type(outputs) is not dict:
        raise ValueError('missing outputs in provider response')
    hourly = outputs.get('hourly')
    if type(hourly) is not list:
        raise ValueError('invalid hourly records list')

    orig_times = []
    iso_times = []
    power = []
    direct = []
    diffuse = []
    reflected = []

    for item in hourly:
        if type(item) is not dict:
            raise ValueError('invalid hourly record entry')
        raw_t = item.get('time')
        if type(raw_t) is not str:
            raise ValueError('invalid record timestamp')
        m = re.fullmatch(r'(\d{4})(\d\d)(\d\d):(\d\d)(\d\d)', raw_t)
        if m:
            iso_t = f'{m[1]}-{m[2]}-{m[3]}T{m[4]}:{m[5]}:00Z'
        else:
            m_iso = re.fullmatch(r'(\d{4}-\d\d-\d\d[T ]\d\d:\d\d(?::\d\d)?)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)?', raw_t)
            if m_iso:
                iso_t = raw_t
            else:
                raise ValueError('malformed timestamp')
        orig_times.append(raw_t)
        iso_times.append(iso_t)

        for key, target_list in (('P', power), ('Gb(i)', direct), ('Gd(i)', diffuse), ('Gr(i)', reflected)):
            if key not in item:
                raise ValueError(f'missing field in PVGIS 5.3 record: {key}')
            val = item.get(key)
            if val is None:
                target_list.append(None)
            else:
                if type(val) not in (int, float) or isinstance(val, bool):
                    raise ValueError(f'invalid number in {key}')
                vf = float(val)
                if not math.isfinite(vf) or vf < 0:
                    raise ValueError(f'invalid physical value in {key}: {vf}')
                target_list.append(vf)

    _sequence(iso_times, year, True)

    confirmed_equipment = dict(
        technology=pv_mod['technology'],
        peak_power=peak_power,
        system_loss=system_loss,
        radiation_db=meteo['radiation_db'],
        year_min=ymin,
        year_max=ymax,
        use_horizon=bool(use_horizon),
        surface_tilt=slope,
        surface_orientation=azimuth,
        mounting_type=mounting_type,
        unit='degrees',
    )

    return dict(
        timestamps=iso_times,
        original_timestamps=orig_times,
        power=power,
        v5_direct_inclined_irradiance=direct,
        v5_diffuse_inclined_irradiance=diffuse,
        v5_ground_reflected_inclined_irradiance=reflected,
        provider_confirmed_metadata=dict(
            provider_confirmed_coordinates=confirmed_coords,
            coordinate_authority='provider',
            provider_confirmed_equipment=confirmed_equipment,
        ),
    )


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



_PVGIS_SOLAR_FIELDS = (
    'power',
    'direct_inclined_irradiance_before_reflectivity',
    'diffuse_inclined_irradiance_before_reflectivity',
    'ground_reflected_inclined_irradiance_before_reflectivity',
)


def _interpolate_solar_series(obj, times, n, fields=_PVGIS_SOLAR_FIELDS):
    """Bounded linear interpolation of explicit None values in PVGIS power/irradiance."""
    missing_by_field = {}
    valid_values = {}
    for field in fields:
        raw_list = obj.get(field)
        if type(raw_list) is not list or len(raw_list) != n:
            raise ValueError('invalid array length: ' + field)
        missing = []
        parsed = []
        for i, v in enumerate(raw_list):
            if v is None:
                missing.append(i)
                parsed.append(None)
            else:
                if type(v) not in (int, float):
                    raise ValueError('invalid numeric array: ' + field)
                vf = float(v)
                if not math.isfinite(vf) or vf < 0:
                    raise ValueError('invalid physical array: ' + field)
                parsed.append(vf)
        missing_by_field[field] = missing
        valid_values[field] = parsed

    for field, missing in missing_by_field.items():
        if not missing:
            continue
        missing_set = set(missing)
        if 0 in missing_set or (n - 1) in missing_set:
            raise SolarDataGapError('unbounded gap at sequence boundary: ' + field)
        consecutive = 0
        for i in range(n):
            if i in missing_set:
                consecutive += 1
                if consecutive > 5:
                    raise SolarDataGapError('gap exceeds maximum consecutive limit of 5 hours: ' + field)
            else:
                consecutive = 0

    union_missing = set()
    for missing in missing_by_field.values():
        union_missing.update(missing)
    max_union = 15
    if len(union_missing) > max_union:
        raise SolarDataGapError(f'missing hour count exceeds limit: {len(union_missing)} > {max_union}')

    interpolated = {}
    for field, parsed in valid_values.items():
        missing = missing_by_field[field]
        arr = np.empty(n, dtype=np.float64)
        if not missing:
            arr[:] = parsed
        else:
            missing_set = set(missing)
            for i, v in enumerate(parsed):
                if v is not None:
                    arr[i] = v
            i = 0
            while i < n:
                if i in missing_set:
                    start = i
                    while i < n and i in missing_set:
                        i += 1
                    end = i - 1
                    v_left = arr[start - 1]
                    v_right = arr[end + 1]
                    gap_len = end - start + 1
                    for step, idx in enumerate(range(start, end + 1), start=1):
                        arr[idx] = v_left + (v_right - v_left) * (step / (gap_len + 1))
                else:
                    i += 1
        interpolated[field] = arr

    power_arr = interpolated['power']
    power_missing = missing_by_field['power']
    annual_kwh_per_kwp = float(np.sum(power_arr) / 1000.0)
    if power_missing:
        delta_kwh = float(np.sum(power_arr[power_missing]) / 1000.0)
    else:
        delta_kwh = 0.0
    delta_pct = (delta_kwh / annual_kwh_per_kwp * 100.0) if annual_kwh_per_kwp > 0 else 0.0

    gap_counts = {field: len(missing_by_field[field]) for field in fields}
    gap_counts['union'] = len(union_missing)
    gap_timestamps = {field: [times[i] for i in missing_by_field[field]] for field in fields}

    gap_info = dict(
        gap_method='linear_bounded_5h',
        gap_counts=gap_counts,
        gap_timestamps_utc=gap_timestamps,
        zero_fill_sensitivity=dict(
            delta_kwh_per_kwp=round(delta_kwh, 6),
            delta_percent=round(delta_pct, 6),
            annual_interpolated_kwh_per_kwp=round(annual_kwh_per_kwp, 6),
            label='sensitivity',
        ),
    )
    return power_arr, interpolated, gap_info


def fetch_pvgis(request, transport=None):
    """Fetch validated SARAH-3 generation with explicit version priority and ERA5 temperature."""
    if not isinstance(request, SolarRequest) or dict(request.source_policy) != SOURCE_POLICY:
        raise ValueError('explicit supported mixed-source policy required')
    profile = parse_profile(request.profile if isinstance(request.profile, dict) else profile_to_dict(request.profile))
    years = checked_years(request.years)
    if any(year < 2005 or year > 2025 for year in years):
        raise ValueError('supported SARAH-3 years are 2005–2025')
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
                    'SARAH-3 observations are mapped to their enclosing UTC hour after validating the complete hourly sequence and constant offset.',
                    'PVGIS 5.3 and 6 irradiance components are validated separately and are not combined; user shading attenuates aggregate power.',
                    'Coarse beam_loss=P*shading_fraction, diffuse_loss=0 represents uniform additional attenuation.',
                    'PVGIS terrain horizon and explicit system loss are separate from user shading.',
                    'Historical temperature is separately sourced ERA5 model data on a nearby grid, not measured on-site.',
                    'Provider terms and full operational availability remain unverified; live contract may change.'])
    offsets, solar_times = {}, {}
    for yi, year in enumerate(years):
        n = len(utc_slots(year))
        for ri, plane in enumerate(profile.roofs):
            progress('solar', ri + yi*max(1, nr), ny*max(1, nr))
            params_v5 = _solar_parameters_v5(profile, plane, year, loss, request.mounting, request.technology)
            if year < 2014:
                params = params_v5
                endpoint = PVGIS5_URL
                obj = get(endpoint, params)
                norm = _normalize_v5(obj, params, year)
                times = norm['timestamps']
                metadata = norm['provider_confirmed_metadata']
                raw_power = norm['power']
                primary_version = '5.3'
            else:
                params = _solar_parameters(profile, plane, year, loss, request.mounting, request.technology)
                endpoint = PVGIS_URL
                obj = get(endpoint, params)
                metadata = _validate_solar(obj, params, plane)
                times = obj.get('timestamps')
                raw_power = obj.get('power')
                primary_version = '6'
                for field in _PVGIS_SOLAR_FIELDS[1:]:
                    values = obj.get(field)
                    if type(values) is not list or len(values) != n:
                        raise ValueError('invalid array length: ' + field)
                    for value in values:
                        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                            raise ValueError('invalid physical array: ' + field)
            offset = _sequence(times, year, True)
            if year in offsets and (offsets[year] != offset or solar_times[year] != times):
                raise ValueError('cross-plane timestamp inconsistency')
            offsets[year], solar_times[year] = offset, times
            if type(raw_power) is not list or len(raw_power) != n:
                raise ValueError('invalid array length: power')
            if any(v is not None and type(v) not in (int, float) for v in raw_power):
                raise ValueError('invalid numeric array: power')
            if any(v is not None and (not math.isfinite(v) or v < 0) for v in raw_power):
                raise ValueError('invalid physical array: power')
            selected = list(raw_power)
            sources = [primary_version if v is not None else None for v in selected]
            fallback = None
            if 2014 <= year <= 2023 and any(v is None for v in selected):
                secondary_obj = get(PVGIS5_URL, params_v5)
                secondary = _normalize_v5(secondary_obj, params_v5, year)
                for i, value in enumerate(secondary['power']):
                    if selected[i] is None and value is not None:
                        selected[i] = value
                        sources[i] = '5.3'
                fallback = dict(provider_version='5.3', database='PVGIS-SARAH3', endpoint=PVGIS5_URL,
                    request_parameters=params_v5, response_sha256=_digest(canonical_json(secondary_obj)),
                    original_timestamps=secondary['original_timestamps'],
                    **secondary['provider_confirmed_metadata'])
            p, _, gap_info = _interpolate_solar_series({'power': selected}, times, n, fields=('power',))
            sources = [source if source is not None else 'interpolated' for source in sources]
            power[ri, yi, :n] = p / 1000
            provenance['solar'].append(dict(year=year, plane_id=plane.id, endpoint=endpoint,
                provider='PVGIS', provider_version=primary_version, dataset='SARAH-3',
                request_parameters=params, user_shading_fraction=plane.shading_fraction,
                response_sha256=_digest(canonical_json(obj)),
                original_timestamps=norm['original_timestamps'] if primary_version == '5.3' else times,
                hourly_source=sources, fallback=fallback,
                fallback_reason='primary_power_missing' if fallback else None,
                gap_method=gap_info['gap_method'],
                gap_counts=gap_info['gap_counts'],
                gap_timestamps_utc=gap_info['gap_timestamps_utc'],
                zero_fill_sensitivity=gap_info['zero_fill_sensitivity'],
                **metadata))
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



class _ProbePlane:
    tilt_deg = 35.0
    azimuth_deg = 180.0
    shading_fraction = 0.0
    id = 'probe'


def check_solar_coverage(profile, transport=None):
    """Probe PVGIS 5.3 (2005, 2023) and PVGIS 6 (2014, 2025) for explicit SARAH-3 coverage."""
    p = parse_profile(profile if isinstance(profile, dict) else profile_to_dict(profile))
    plane = p.roofs[0] if getattr(p, 'roofs', None) else _ProbePlane()
    client = transport if isinstance(transport, ProviderClient) else ProviderClient(transport)
    retrieval_date = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    sources = {}

    # Probe PVGIS 5.3 SARAH-3 at documented range boundaries 2005 and 2023
    v5_ok = True
    v5_meta = None
    v5_digests = []
    for y in (2005, 2023):
        try:
            params = _solar_parameters_v5(p, plane, y, 14.0, 'Free standing 2025', 'cSi')
            obj = client.get_json(PVGIS5_URL, params)
            norm = _normalize_v5(obj, params, y)
            _sequence(norm['timestamps'], y, True)
            v5_digests.append(_digest(canonical_json(obj)))
            if v5_meta is None:
                v5_meta = norm.get('provider_confirmed_metadata', {})
        except (TimeoutError, ConnectionError, OSError):
            raise
        except ValueError as exc:
            if str(exc) in ('provider retry limit', 'unapproved endpoint', 'response size limit'):
                raise
            raise
        except KeyError:
            raise

    if v5_ok:
        v5_years = list(range(2005, 2024))
        sources['5.3'] = dict(
            provider='PVGIS',
            provider_version='5.3',
            database='PVGIS-SARAH3',
            radiation_db='PVGIS-SARAH3',
            endpoint=PVGIS5_URL,
            year_min=2005,
            year_max=2023,
            years=v5_years,
            verified_boundary_years=[2005, 2023],
            evidence=dict(
                provider='PVGIS',
                provider_version='5.3',
                database='PVGIS-SARAH3',
                endpoint=PVGIS5_URL,
                verified_boundary_years=[2005, 2023],
                response_sha256=v5_digests,
                retrieval_date_utc=retrieval_date,
                **v5_meta,
            ),
        )

    # Probe PVGIS 6 SARAH-3 at documented range boundaries 2014 and 2025
    v6_ok = True
    v6_meta = None
    v6_digests = []
    for y in (2014, 2025):
        try:
            n = len(utc_slots(y))
            params = _solar_parameters(p, plane, y, 14.0, 'Free standing 2025', 'cSi')
            obj = client.get_json(PVGIS_URL, params)
            meta = _validate_solar(obj, params, plane)
            for src_field in ('irradiance_source', 'radiation_database', 'radiation_db'):
                val = obj.get(src_field) or (obj.get('inputs', {}) or {}).get(src_field)
                if val is not None and val not in ('SARAH-3', 'SARAH3', 'PVGIS-SARAH3'):
                    raise ValueError('radiation database mismatch')
            times = obj.get('timestamps')
            _sequence(times, y, True)
            raw_power = obj.get('power')
            if type(raw_power) is not list or len(raw_power) != n:
                raise ValueError('invalid array length: power')
            for v in raw_power:
                if v is not None and (type(v) not in (int, float) or not math.isfinite(v) or v < 0):
                    raise ValueError('invalid physical array: power')
            for field in _PVGIS_SOLAR_FIELDS[1:]:
                values = obj.get(field)
                if type(values) is not list or len(values) != n:
                    raise ValueError('invalid array length: ' + field)
                for value in values:
                    if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                        raise ValueError('invalid physical array: ' + field)
            v6_digests.append(_digest(canonical_json(obj)))
            if v6_meta is None:
                v6_meta = meta
        except (TimeoutError, ConnectionError, OSError):
            raise
        except ValueError as exc:
            if str(exc) in ('provider retry limit', 'unapproved endpoint', 'response size limit'):
                raise
            raise
        except KeyError:
            raise

    if v6_ok:
        v6_years = list(range(2014, 2026))
        sources['6'] = dict(
            provider='PVGIS',
            provider_version='6',
            database='SARAH-3',
            radiation_db='SARAH-3',
            endpoint=PVGIS_URL,
            year_min=2014,
            year_max=2025,
            years=v6_years,
            verified_boundary_years=[2014, 2025],
            evidence=dict(
                provider='PVGIS',
                provider_version='6',
                database='SARAH-3',
                endpoint=PVGIS_URL,
                verified_boundary_years=[2014, 2025],
                response_sha256=v6_digests,
                retrieval_date_utc=retrieval_date,
                **v6_meta,
            ),
        )

    all_years = sorted(set().union(*(s['years'] for s in sources.values()))) if sources else []

    return dict(
        supported=bool(sources),
        sources=sources,
        years=all_years,
        year_range=[all_years[0], all_years[-1]] if all_years else None,
        retrieval_date=retrieval_date,
        retrieval_date_utc=retrieval_date,
        location=dict(latitude=p.location.latitude, longitude=p.location.longitude),
    )
