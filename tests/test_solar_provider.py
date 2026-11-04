"""Tests for feasibility.solar_provider fake-transport retry, timeout, serialization."""

import json
from types import MappingProxyType
import pytest

from feasibility import solar_provider
from feasibility.solar_provider import (
    MAX_ATTEMPTS,
    MAX_RESPONSE_BYTES,
    PVGIS_URL,
    TEMPERATURE_URL,
    ProviderClient,
    RateLimiter,
    Response,
)


class FakeClock:
    def __init__(self, initial_time: float = 100.0) -> None:
        self.now = initial_time
        self.sleep_calls = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.now += max(0.0, float(seconds))


@pytest.fixture
def fake_clock():
    return FakeClock()


def test_get_json_success_and_parameters_unchanged(fake_clock):
    called_args = []

    def fake_transport(endpoint, parameters, *, connect_timeout, read_timeout, total_timeout, max_bytes):
        assert connect_timeout > 0
        assert read_timeout > 0
        assert total_timeout > 0
        assert max_bytes > 0
        called_args.append((endpoint, parameters))
        return Response(status=200, url=endpoint, body=json.dumps({"key": "val"}).encode("utf-8"))

    client = ProviderClient(
        transport=fake_transport,
        clock=fake_clock.monotonic,
        sleep=fake_clock.sleep,
        limiter=RateLimiter(fake_clock.monotonic, fake_clock.sleep),
    )
    raw_params = {"lat": 45.0, "lon": 9.0}
    frozen_params = MappingProxyType(dict(raw_params))

    data = client.get_json(PVGIS_URL, frozen_params)
    assert data == {"key": "val"}
    assert raw_params == {"lat": 45.0, "lon": 9.0}
    assert called_args[0] == (PVGIS_URL, frozen_params)


def test_get_json_temperature_endpoint(fake_clock):
    def fake_transport(endpoint, parameters, **kwargs):
        return Response(status=200, url=endpoint, body=b'{"temp": 21.5}')

    client = ProviderClient(
        transport=fake_transport,
        clock=fake_clock.monotonic,
        sleep=fake_clock.sleep,
        limiter=RateLimiter(fake_clock.monotonic, fake_clock.sleep),
    )
    result = client.get_json(TEMPERATURE_URL, {"year": 2023})
    assert result == {"temp": 21.5}


def test_retry_on_429_529_5xx_eventual_success(fake_clock):
    attempts = 0

    def fake_transport(endpoint, parameters, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return Response(status=429, url=endpoint, body=b'{"error": "rate limited"}')
        if attempts == 2:
            return Response(status=529, url=endpoint, body=b'{"error": "overloaded"}')
        return Response(status=200, url=endpoint, body=b'{"success": true}')

    client = ProviderClient(
        transport=fake_transport,
        clock=fake_clock.monotonic,
        sleep=fake_clock.sleep,
        limiter=RateLimiter(fake_clock.monotonic, fake_clock.sleep),
    )
    res = client.get_json(PVGIS_URL, {})
    assert res == {"success": True}
    assert attempts == 3
    positive_sleeps = [s for s in fake_clock.sleep_calls if s > 0]
    assert len(positive_sleeps) == 2


def test_retry_exhausted_max_attempts(fake_clock):
    attempts = 0

    def fake_transport(endpoint, parameters, **kwargs):
        nonlocal attempts
        attempts += 1
        return Response(status=500, url=endpoint, body=b'{"error": "internal error"}')

    client = ProviderClient(
        transport=fake_transport,
        clock=fake_clock.monotonic,
        sleep=fake_clock.sleep,
        limiter=RateLimiter(fake_clock.monotonic, fake_clock.sleep),
    )
    with pytest.raises(ValueError):
        client.get_json(PVGIS_URL, {})

    assert attempts == MAX_ATTEMPTS
    positive_sleeps = [s for s in fake_clock.sleep_calls if s > 0]
    assert len(positive_sleeps) == MAX_ATTEMPTS - 1


def test_4xx_not_retried(fake_clock):
    attempts = 0

    def fake_transport(endpoint, parameters, **kwargs):
        nonlocal attempts
        attempts += 1
        return Response(status=400, url=endpoint, body=b'{"error": "bad request"}')

    client = ProviderClient(
        transport=fake_transport,
        clock=fake_clock.monotonic,
        sleep=fake_clock.sleep,
        limiter=RateLimiter(fake_clock.monotonic, fake_clock.sleep),
    )
    with pytest.raises(ValueError):
        client.get_json(PVGIS_URL, {})

    assert attempts == 1
    positive_sleeps = [s for s in fake_clock.sleep_calls if s > 0]
    assert len(positive_sleeps) == 0


def test_timeout_fails_immediately_no_retry(fake_clock):
    attempts = 0

    def fake_transport(endpoint, parameters, **kwargs):
        nonlocal attempts
        attempts += 1
        raise TimeoutError("connection timed out")

    client = ProviderClient(
        transport=fake_transport,
        clock=fake_clock.monotonic,
        sleep=fake_clock.sleep,
        limiter=RateLimiter(fake_clock.monotonic, fake_clock.sleep),
    )
    with pytest.raises(TimeoutError):
        client.get_json(PVGIS_URL, {})

    assert attempts == 1
    positive_sleeps = [s for s in fake_clock.sleep_calls if s > 0]
    assert len(positive_sleeps) == 0


def test_arbitrary_endpoints_rejected(fake_clock):
    def fake_transport(endpoint, parameters, **kwargs):
        return Response(status=200, url=endpoint, body=b"{}")

    client = ProviderClient(
        transport=fake_transport,
        clock=fake_clock.monotonic,
        sleep=fake_clock.sleep,
        limiter=RateLimiter(fake_clock.monotonic, fake_clock.sleep),
    )
    with pytest.raises(ValueError):
        client.get_json("https://evil.example.com/api", {})


def test_redirects_rejected(fake_clock):
    def fake_transport_302(endpoint, parameters, **kwargs):
        return Response(status=302, url="https://redirected.example.com/api", body=b"")

    client = ProviderClient(
        transport=fake_transport_302,
        clock=fake_clock.monotonic,
        sleep=fake_clock.sleep,
        limiter=RateLimiter(fake_clock.monotonic, fake_clock.sleep),
    )
    with pytest.raises(ValueError):
        client.get_json(PVGIS_URL, {})

    def fake_transport_url_mismatch(endpoint, parameters, **kwargs):
        return Response(status=200, url="https://other.example.com/endpoint", body=b"{}")

    client_mismatch = ProviderClient(
        transport=fake_transport_url_mismatch,
        clock=fake_clock.monotonic,
        sleep=fake_clock.sleep,
        limiter=RateLimiter(fake_clock.monotonic, fake_clock.sleep),
    )
    with pytest.raises(ValueError):
        client_mismatch.get_json(PVGIS_URL, {})


def test_reject_body_exceeding_max_bytes(fake_clock, monkeypatch):
    monkeypatch.setattr(solar_provider, "MAX_RESPONSE_BYTES", 50)

    def fake_transport(endpoint, parameters, **kwargs):
        return Response(status=200, url=endpoint, body=b'x' * 60)

    client = ProviderClient(
        transport=fake_transport,
        clock=fake_clock.monotonic,
        sleep=fake_clock.sleep,
        limiter=RateLimiter(fake_clock.monotonic, fake_clock.sleep),
    )
    with pytest.raises(ValueError):
        client.get_json(PVGIS_URL, {})


def test_reject_invalid_json(fake_clock):
    def fake_transport(endpoint, parameters, **kwargs):
        return Response(status=200, url=endpoint, body=b"not valid json")

    client = ProviderClient(
        transport=fake_transport,
        clock=fake_clock.monotonic,
        sleep=fake_clock.sleep,
        limiter=RateLimiter(fake_clock.monotonic, fake_clock.sleep),
    )
    with pytest.raises(ValueError):
        client.get_json(PVGIS_URL, {})


@pytest.mark.parametrize("non_object_body", [b'["array"]', b'"string"', b"123", b"true", b"null"])
def test_reject_non_object_json(fake_clock, non_object_body):
    def fake_transport(endpoint, parameters, **kwargs):
        return Response(status=200, url=endpoint, body=non_object_body)

    client = ProviderClient(
        transport=fake_transport,
        clock=fake_clock.monotonic,
        sleep=fake_clock.sleep,
        limiter=RateLimiter(fake_clock.monotonic, fake_clock.sleep),
    )
    with pytest.raises(ValueError):
        client.get_json(PVGIS_URL, {})


def test_rate_limiter_standalone_spacing(fake_clock):
    limiter = RateLimiter(fake_clock.monotonic, fake_clock.sleep)
    limiter.acquire()
    start_time = fake_clock.monotonic()
    limiter.acquire()
    assert fake_clock.monotonic() - start_time == 1


def test_simulated_elapsed_total_deadline_exceeded(fake_clock):
    def slow_transport(endpoint, parameters, **kwargs):
        fake_clock.now += 125.0
        return Response(status=200, url=endpoint, body=b'{"key": "val"}')

    client = ProviderClient(
        transport=slow_transport,
        clock=fake_clock.monotonic,
        sleep=fake_clock.sleep,
        limiter=RateLimiter(fake_clock.monotonic, fake_clock.sleep),
    )
    with pytest.raises(TimeoutError):
        client.get_json(PVGIS_URL, {})


def test_transport_timeout_and_deadline_kwargs_positive(fake_clock):
    received_kwargs = {}

    def fake_transport(endpoint, parameters, **kwargs):
        received_kwargs.update(kwargs)
        return Response(status=200, url=endpoint, body=b"{}")

    client = ProviderClient(
        transport=fake_transport,
        clock=fake_clock.monotonic,
        sleep=fake_clock.sleep,
        limiter=RateLimiter(fake_clock.monotonic, fake_clock.sleep),
    )
    client.get_json(PVGIS_URL, {})
    assert received_kwargs["connect_timeout"] > 0
    assert received_kwargs["read_timeout"] > 0
    assert received_kwargs["total_timeout"] > 0
    assert received_kwargs["max_bytes"] > 0


def test_default_transport_serializes_parameters_and_rejects_declared_oversize(monkeypatch):
    sent, requests = [], []
    class Pipe:
        def send(self, value): sent.append(value)
        def close(self): pass
    class Socket:
        def settimeout(self, value): assert 0 < value <= 30
    class Reply:
        status = 200
        def getheader(self, name): return '2'
        def read(self, size):
            if hasattr(self, 'done'): return b''
            self.done = True
            return b'{}'
    class Connection:
        def __init__(self, host, timeout):
            assert host == 'photovoltaic-geographic-information-system.ec.europa.eu'
            assert timeout == 10
            self.sock = Socket()
        def connect(self): pass
        def close(self): pass
        def request(self, method, path, headers): requests.append((method, path, headers))
        def getresponse(self): return Reply()
    monkeypatch.setattr(solar_provider.http.client, 'HTTPSConnection', Connection)
    solar_provider._http_child(Pipe(), PVGIS_URL, {'peak-power':1,'photovoltaic_module':'cSi:Free standing 2025'}, 10,30,120,100)
    assert sent == [('ok',200,b'{}')]
    assert requests[0][0] == 'GET'
    from urllib.parse import parse_qs, urlsplit
    assert parse_qs(urlsplit(requests[0][1]).query) == {'peak-power':['1'],'photovoltaic_module':['cSi:Free standing 2025']}
    sent.clear()
    monkeypatch.setattr(Reply,'getheader',lambda self,name:'1000')
    solar_provider._http_child(Pipe(),PVGIS_URL,{},10,30,120,100)
    assert sent == [('invalid',0,b'')]


def test_http_child_distinguishes_timeout_from_connection_and_http_errors(monkeypatch):
    sent = []

    class Pipe:
        def send(self, value):
            sent.append(value)
        def close(self):
            pass

    # 1. TimeoutError -> ('timeout', 0, b'')
    class TimeoutConnection:
        def __init__(self, host, timeout): pass
        def connect(self): raise TimeoutError("connect timeout")
        def close(self): pass

    monkeypatch.setattr(solar_provider.http.client, 'HTTPSConnection', TimeoutConnection)
    solar_provider._http_child(Pipe(), PVGIS_URL, {}, 10, 30, 120, 100)
    assert sent == [('timeout', 0, b'')]
    sent.clear()

    # 2. ConnectionRefusedError (subclass of ConnectionError/OSError) -> ('connection_error', 0, b'')
    class ConnectionRefusedConn:
        def __init__(self, host, timeout): pass
        def connect(self): raise ConnectionRefusedError("Connection refused")
        def close(self): pass

    monkeypatch.setattr(solar_provider.http.client, 'HTTPSConnection', ConnectionRefusedConn)
    solar_provider._http_child(Pipe(), PVGIS_URL, {}, 10, 30, 120, 100)
    assert sent == [('connection_error', 0, b'')]
    sent.clear()

    # 3. Generic OSError -> ('connection_error', 0, b'')
    class GenericOSErrorConn:
        def __init__(self, host, timeout): pass
        def connect(self): raise OSError("Network unreachable")
        def close(self): pass

    monkeypatch.setattr(solar_provider.http.client, 'HTTPSConnection', GenericOSErrorConn)
    solar_provider._http_child(Pipe(), PVGIS_URL, {}, 10, 30, 120, 100)
    assert sent == [('connection_error', 0, b'')]
    sent.clear()

    # 4. http.client.HTTPException -> ('connection_error', 0, b'')
    class HttpErrorConn:
        def __init__(self, host, timeout):
            self.sock = self
        def settimeout(self, timeout): pass
        def connect(self): pass
        def close(self): pass
        def request(self, method, path, headers):
            raise solar_provider.http.client.RemoteDisconnected("Remote disconnected")

    monkeypatch.setattr(solar_provider.http.client, 'HTTPSConnection', HttpErrorConn)
    solar_provider._http_child(Pipe(), PVGIS_URL, {}, 10, 30, 120, 100)
    assert sent == [('connection_error', 0, b'')]


def test_network_transport_connection_error_raises_connection_error(monkeypatch):
    class FakeProcess:
        def __init__(self, target, args):
            self.target = target
            self.args = args
        def start(self):
            pass
        def is_alive(self):
            return False
        def join(self, timeout=None):
            pass

    class FakeReceiver:
        def __init__(self, msg):
            self.msg = msg
        def poll(self, timeout):
            return True
        def recv(self):
            return self.msg
        def close(self):
            pass

    class FakeSender:
        def close(self):
            pass

    class FakeCtx:
        def __init__(self, msg):
            self.msg = msg
        def Pipe(self, duplex=False):
            return FakeReceiver(self.msg), FakeSender()
        def Process(self, target, args):
            return FakeProcess(target, args)

    # connection_error -> raises ConnectionError
    monkeypatch.setattr(solar_provider.multiprocessing, 'get_context', lambda method: FakeCtx(('connection_error', 0, b'')))
    with pytest.raises(ConnectionError, match='provider connection failed'):
        solar_provider.network_transport(PVGIS_URL, {}, connect_timeout=10, read_timeout=30, total_timeout=120, max_bytes=100)

    # timeout -> raises TimeoutError
    monkeypatch.setattr(solar_provider.multiprocessing, 'get_context', lambda method: FakeCtx(('timeout', 0, b'')))
    with pytest.raises(TimeoutError, match='provider I/O deadline'):
        solar_provider.network_transport(PVGIS_URL, {}, connect_timeout=10, read_timeout=30, total_timeout=120, max_bytes=100)


def test_connection_error_fails_immediately_no_retry(fake_clock):
    attempts = 0

    def fake_transport(endpoint, parameters, **kwargs):
        nonlocal attempts
        attempts += 1
        raise ConnectionError("connection refused")

    client = ProviderClient(
        transport=fake_transport,
        clock=fake_clock.monotonic,
        sleep=fake_clock.sleep,
        limiter=RateLimiter(fake_clock.monotonic, fake_clock.sleep),
    )
    with pytest.raises(ConnectionError):
        client.get_json(PVGIS_URL, {})

    assert attempts == 1
    positive_sleeps = [s for s in fake_clock.sleep_calls if s > 0]
    assert len(positive_sleeps) == 0


def test_solar_provider_gap_interpolation_linear_values_and_sensitivity():
    from tests.test_solar_candidate import Weather, client, request
    times_recorded = []
    def modify(endpoint, obj, params):
        if endpoint == solar_provider.PVGIS_URL:
            obj['power'][10] = None
            obj['power'][19] = 100.0
            obj['power'][20] = None
            obj['power'][21] = None
            obj['power'][22] = 400.0
            obj['power'][29:34] = [100.0, None, None, None, 500.0]
            times_recorded.extend([obj['timestamps'][i] for i in (10, 20, 21, 30, 31, 32)])

    r, _ = request(nr=1, years=(2025,))
    source = solar_provider.fetch_pvgis(r, client(Weather(modify)))
    a = source.artifact
    assert a.power[0, 0, 10] == 0.5
    assert a.power[0, 0, 20] == 0.2
    assert a.power[0, 0, 21] == 0.3
    assert a.power[0, 0, 30:33].tolist() == [0.2, 0.3, 0.4]

    prov = json.loads(source.provenance_bytes)
    sol = prov['solar'][0]
    assert sol['gap_method'] == 'linear_bounded_5h'
    assert sol['gap_counts']['power'] == 6
    assert sol['gap_counts']['union'] == 6
    assert sol['gap_timestamps_utc']['power'] == times_recorded
    assert sol['zero_fill_sensitivity']['label'] == 'sensitivity'
    assert sol['zero_fill_sensitivity']['delta_kwh_per_kwp'] == 1.9
    assert sol['zero_fill_sensitivity']['delta_percent'] > 0


def test_solar_provider_reject_gap_exceeding_5h():
    from tests.test_solar_candidate import Weather, client, request
    def modify(endpoint, obj, params):
        if endpoint == solar_provider.PVGIS_URL:
            obj['power'][10] = None
            obj['power'][11] = None
            obj['power'][12] = None
            obj['power'][13] = None
            obj['power'][14] = None
            obj['power'][15] = None

    r, _ = request(nr=1, years=(2025,))
    with pytest.raises(solar_provider.SolarDataGapError, match="exceeds maximum consecutive limit of 5 hours"):
        solar_provider.fetch_pvgis(r, client(Weather(modify)))


def test_solar_provider_reject_boundary_edge_gaps():
    from tests.test_solar_candidate import Weather, client, request
    def modify_first(endpoint, obj, params):
        if endpoint == solar_provider.PVGIS_URL:
            obj['power'][0] = None

    r, _ = request(nr=1, years=(2025,))
    with pytest.raises(solar_provider.SolarDataGapError, match="unbounded gap at sequence boundary"):
        solar_provider.fetch_pvgis(r, client(Weather(modify_first)))

    def modify_last(endpoint, obj, params):
        if endpoint == solar_provider.PVGIS_URL:
            obj['power'][8759] = None

    with pytest.raises(solar_provider.SolarDataGapError, match="unbounded gap at sequence boundary"):
        solar_provider.fetch_pvgis(r, client(Weather(modify_last)))


def test_solar_provider_reject_union_missing_count_over_limit():
    from tests.test_solar_candidate import Weather, client, request
    def modify(endpoint, obj, params):
        if endpoint == solar_provider.PVGIS_URL:
            for k in range(8):
                obj['power'][10 + k * 10] = None
                obj['power'][11 + k * 10] = None
            obj['direct_inclined_irradiance_before_reflectivity'][200] = None
            obj['direct_inclined_irradiance_before_reflectivity'][201] = None

    r, _ = request(nr=1, years=(2025,))
    with pytest.raises(solar_provider.SolarDataGapError, match="missing hour count exceeds limit: 16 > 15"):
        solar_provider.fetch_pvgis(r, client(Weather(modify)))


def test_solar_provider_union_missing_at_limit_accepted():
    from tests.test_solar_candidate import Weather, client, request
    def modify(endpoint, obj, params):
        if endpoint == solar_provider.PVGIS_URL:
            for k in range(7):
                obj['power'][10 + k * 10] = None
                obj['power'][11 + k * 10] = None
            obj['power'][200] = None
            obj['direct_inclined_irradiance_before_reflectivity'][10] = None
            obj['direct_inclined_irradiance_before_reflectivity'][200] = None

    r, _ = request(nr=1, years=(2025,))
    source = solar_provider.fetch_pvgis(r, client(Weather(modify)))
    prov = json.loads(source.provenance_bytes)
    assert prov['solar'][0]['gap_counts']['union'] == 15


def test_solar_provider_nonfinite_negative_and_invalid_types_rejected():
    from tests.test_solar_candidate import Weather, client, request
    n = 8760
    base_obj = {
        'power': [100.0] * n,
        'direct_inclined_irradiance_before_reflectivity': [50.0] * n,
        'diffuse_inclined_irradiance_before_reflectivity': [30.0] * n,
        'ground_reflected_inclined_irradiance_before_reflectivity': [10.0] * n,
    }
    for bad_val, err in [
        (float('nan'), "invalid physical array: power"),
        (float('inf'), "invalid physical array: power"),
        (-1.0, "invalid physical array: power"),
    ]:
        obj = dict(base_obj)
        obj['power'] = list(base_obj['power'])
        obj['power'][5] = bad_val
        with pytest.raises(ValueError, match=err):
            solar_provider._interpolate_solar_series(obj, ["t"] * n, n)

    for bad_val, err in [
        (-5.0, "invalid physical array: power"),
        ("invalid", "invalid numeric array: power"),
        (True, "invalid numeric array: power"),
    ]:
        def modify(endpoint, obj, params, bv=bad_val):
            if endpoint == solar_provider.PVGIS_URL:
                obj['power'][10] = bv

        r, _ = request(nr=1, years=(2023,))
        with pytest.raises(ValueError, match=err):
            solar_provider.fetch_pvgis(r, client(Weather(modify)))


def test_solar_provider_temperature_and_timestamps_never_relaxed():
    from tests.test_solar_candidate import Weather, client, request
    def modify_temp(endpoint, obj, params):
        if endpoint == solar_provider.TEMPERATURE_URL:
            obj['hourly']['temperature_2m'][10] = None

    r, _ = request(nr=1, years=(2023,))
    with pytest.raises(ValueError, match="invalid numeric array: temperature"):
        solar_provider.fetch_pvgis(r, client(Weather(modify_temp)))

    def modify_time(endpoint, obj, params):
        if endpoint == solar_provider.PVGIS_URL:
            obj['timestamps'] = obj['timestamps'][:-1]
            obj['power'] = obj['power'][:-1]

    with pytest.raises(ValueError, match="incomplete year timestamps"):
        solar_provider.fetch_pvgis(r, client(Weather(modify_time)))


def test_solar_provider_unchanged_complete_series_and_raw_cache_preservation(tmp_path):
    import copy
    from tests.test_solar_candidate import Weather, client, request
    r, _ = request(nr=1, years=(2023,))
    w = Weather()
    cache = solar_provider.TenantCache(tmp_path / 'cache', 'a' * 32)
    r_cached = solar_provider.SolarRequest(r.profile, r.years, r.loss_percent, r.mounting, r.technology, r.source_policy, cache=cache)
    source1 = solar_provider.fetch_pvgis(r_cached, client(w))
    prov1 = json.loads(source1.provenance_bytes)['solar'][0]
    assert prov1['gap_counts']['union'] == 0
    assert prov1['gap_counts']['power'] == 0
    assert prov1['gap_timestamps_utc']['power'] == []
    assert prov1['zero_fill_sensitivity']['delta_kwh_per_kwp'] == 0.0
    assert prov1['zero_fill_sensitivity']['delta_percent'] == 0.0

    # Verify original raw arrays with None and digest remain unmutated on interpolation
    n = 8760
    raw_obj = {
        'power': [500.0] * n,
        'direct_inclined_irradiance_before_reflectivity': [1.0] * n,
        'diffuse_inclined_irradiance_before_reflectivity': [1.0] * n,
        'ground_reflected_inclined_irradiance_before_reflectivity': [1.0] * n,
    }
    raw_obj['power'][10] = None
    raw_obj['direct_inclined_irradiance_before_reflectivity'][20] = None
    expected_digest = solar_provider._digest(solar_provider.canonical_json(raw_obj))
    obj_copy = copy.deepcopy(raw_obj)

    p_arr, interp, info = solar_provider._interpolate_solar_series(obj_copy, ["t"] * n, n)
    assert p_arr[10] == 500.0
    assert interp['direct_inclined_irradiance_before_reflectivity'][20] == 1.0
    assert obj_copy['power'][10] is None
    assert obj_copy['direct_inclined_irradiance_before_reflectivity'][20] is None
    assert solar_provider._digest(solar_provider.canonical_json(obj_copy)) == expected_digest


def test_solar_provider_multi_year_per_year_provenance():
    from tests.test_solar_candidate import Weather, client, request
    def modify(endpoint, obj, params):
        if endpoint == solar_provider.PVGIS_URL:
            year = int(params['start_time'][:4])
            if year == 2024:
                obj['power'][10] = None
            elif year == 2025:
                obj['power'][20] = None
                obj['power'][21] = None

    r, _ = request(nr=1, years=(2024, 2025))
    source = solar_provider.fetch_pvgis(r, client(Weather(modify)))
    prov = json.loads(source.provenance_bytes)
    assert len(prov['solar']) == 2
    assert prov['solar'][0]['year'] == 2024
    assert prov['solar'][0]['gap_counts']['power'] == 1
    assert prov['solar'][1]['year'] == 2025
    assert prov['solar'][1]['gap_counts']['power'] == 2


@pytest.fixture
def v5_payload_factory():
    from datetime import datetime, timedelta, timezone

    def _factory(year=2023, is_leap=False, offset_min=0, power_val=0.0):
        count = 8784 if is_leap else 8760
        start = datetime(year, 1, 1, 0, offset_min, tzinfo=timezone.utc)
        hourly = [
            {
                'time': (start + timedelta(hours=i)).strftime('%Y%m%d:%H%M'),
                'P': power_val,
                'Gb(i)': 0.0,
                'Gd(i)': 0.0,
                'Gr(i)': 0.0,
            }
            for i in range(count)
        ]
        params = {
            'lat': 45.0,
            'lon': 9.0,
            'angle': 30.0,
            'aspect': 0.0,
            'mountingplace': 'free',
            'peakpower': 1.0,
            'loss': 14.0,
        }
        obj = {
            'inputs': {
                'location': {'latitude': 45.0, 'longitude': 9.0},
                'meteo_data': {
                    'radiation_db': 'PVGIS-SARAH3',
                    'year_min': 2005,
                    'year_max': 2024,
                    'use_horizon': 1,
                },
                'mounting_system': {
                    'fixed': {
                        'slope': {'value': 30.0},
                        'azimuth': {'value': 0.0},
                        'type': 'free-standing',
                    }
                },
                'pv_module': {
                    'technology': 'c-Si2025',
                    'peak_power': 1.0,
                    'system_loss': 14.0,
                },
            },
            'meta': {
                'outputs': {
                    'hourly': {
                        'variables': {
                            'P': {'units': 'W'},
                            'Gb(i)': {'units': 'W/m2'},
                            'Gd(i)': {'units': 'W/m2'},
                            'Gr(i)': {'units': 'W/m2'},
                        }
                    }
                }
            },
            'outputs': {'hourly': hourly},
        }
        return obj, params, year

    return _factory


def test_normalize_v5_config_and_units_mismatch(v5_payload_factory):
    obj, params, year = v5_payload_factory()
    obj['inputs']['location']['latitude'] = 46.0
    with pytest.raises(ValueError, match="coordinate mismatch: latitude"):
        solar_provider._normalize_v5(obj, params, year)

    obj, params, year = v5_payload_factory()
    obj['inputs']['meteo_data']['radiation_db'] = 'OTHER-DB'
    with pytest.raises(ValueError, match="radiation_db mismatch"):
        solar_provider._normalize_v5(obj, params, year)

    obj, params, year = v5_payload_factory()
    obj['inputs']['meteo_data']['use_horizon'] = 0
    with pytest.raises(ValueError, match="horizon profile not applied"):
        solar_provider._normalize_v5(obj, params, year)

    obj, params, year = v5_payload_factory()
    obj['inputs']['mounting_system']['fixed']['slope']['value'] = 45.0
    with pytest.raises(ValueError, match="slope metadata mismatch"):
        solar_provider._normalize_v5(obj, params, year)

    obj, params, year = v5_payload_factory()
    obj['inputs']['mounting_system']['fixed']['type'] = 'building-integrated'
    with pytest.raises(ValueError, match="mounting type mismatch"):
        solar_provider._normalize_v5(obj, params, year)

    obj, params, year = v5_payload_factory()
    obj['inputs']['pv_module']['technology'] = 'CdTe'
    with pytest.raises(ValueError, match="technology mismatch: expected c-Si2025"):
        solar_provider._normalize_v5(obj, params, year)

    obj, params, year = v5_payload_factory()
    obj['meta']['outputs']['hourly']['variables']['P']['units'] = 'kW'
    with pytest.raises(ValueError, match="unit mismatch for P: expected W, got kW"):
        solar_provider._normalize_v5(obj, params, year)


def test_normalize_v5_leap_calendar(v5_payload_factory):
    obj, params, year = v5_payload_factory(year=2024, is_leap=True)
    res = solar_provider._normalize_v5(obj, params, year)
    assert len(res['power']) == 8784
    assert any('2024-02-29' in t for t in res['timestamps'])

    obj_bad, params_bad, year_bad = v5_payload_factory(year=2024, is_leap=False)
    with pytest.raises(ValueError, match="incomplete year timestamps"):
        solar_provider._normalize_v5(obj_bad, params_bad, year_bad)


def test_normalize_v5_timestamp_offset(v5_payload_factory):
    obj, params, year = v5_payload_factory(offset_min=10)
    res = solar_provider._normalize_v5(obj, params, year)
    assert res['timestamps'][0] == '2023-01-01T00:10:00Z'
    assert res['original_timestamps'][0] == '20230101:0010'

    obj_irreg, params_irreg, year_irreg = v5_payload_factory(offset_min=10)
    obj_irreg['outputs']['hourly'][5]['time'] = '20230101:0525'
    with pytest.raises(ValueError, match="missing hour or irregular observation sequence"):
        solar_provider._normalize_v5(obj_irreg, params_irreg, year_irreg)


def test_normalize_v5_duplicate_timestamps(v5_payload_factory):
    obj, params, year = v5_payload_factory()
    obj['outputs']['hourly'][1]['time'] = obj['outputs']['hourly'][0]['time']
    with pytest.raises(ValueError, match="duplicate canonical hour"):
        solar_provider._normalize_v5(obj, params, year)


def test_normalize_v5_zero_and_negative_physical_values(v5_payload_factory):
    obj, params, year = v5_payload_factory(power_val=0.0)


@pytest.fixture
def solar_request_factory():
    def _create(years=(2020,), nr=1, loss=14.0, mounting='Free standing 2025', technology='cSi'):
        from tests.test_dataset_compile import inputs
        profile, _, _ = inputs(nr=nr, years=years)
        return solar_provider.SolarRequest(
            profile=profile,
            years=years,
            loss_percent=loss,
            mounting=mounting,
            technology=technology,
            source_policy=solar_provider.SOURCE_POLICY,
        )

    return _create


@pytest.fixture
def synthetic_transport_factory():
    def _factory(v5_modify=None, v6_modify=None, temp_modify=None):
        calls = []

        def _transport(endpoint, params, **kwargs):
            calls.append((endpoint, dict(params)))
            if endpoint == solar_provider.PVGIS5_URL:
                year = int(params['startyear'])
                slots = solar_provider.utc_slots(year)
                n = len(slots)
                obj = {
                    'inputs': {
                        'location': {'latitude': params['lat'], 'longitude': params['lon']},
                        'meteo_data': {
                            'radiation_db': 'PVGIS-SARAH3',
                            'year_min': 2005,
                            'year_max': 2025,
                            'use_horizon': 1,
                        },
                        'mounting_system': {
                            'fixed': {
                                'slope': {'value': params['angle']},
                                'azimuth': {'value': params['aspect']},
                                'type': 'free-standing' if params['mountingplace'] == 'free' else 'building-integrated',
                            }
                        },
                        'pv_module': {
                            'technology': 'c-Si2025',
                            'peak_power': params['peakpower'],
                            'system_loss': params['loss'],
                        },
                    },
                    'meta': {
                        'outputs': {
                            'hourly': {
                                'variables': {
                                    'P': {'units': 'W'},
                                    'Gb(i)': {'units': 'W/m2'},
                                    'Gd(i)': {'units': 'W/m2'},
                                    'Gr(i)': {'units': 'W/m2'},
                                }
                            }
                        }
                    },
                    'outputs': {
                        'hourly': [
                            {
                                'time': t.strftime('%Y%m%d:%H%M'),
                                'P': 400.0,
                                'Gb(i)': 100.0,
                                'Gd(i)': 50.0,
                                'Gr(i)': 10.0,
                            }
                            for t in slots
                        ]
                    },
                }
                if v5_modify:
                    v5_modify(obj, params)
                return Response(status=200, url=endpoint, body=json.dumps(obj).encode('utf-8'))

            if endpoint == solar_provider.PVGIS_URL:
                year = int(params['start_time'][:4])
                slots = solar_provider.utc_slots(year)
                n = len(slots)
                obj = {
                    'timestamps': [t.strftime('%Y-%m-%dT%H:%M:00Z') for t in slots],
                    'power': [500.0] * n,
                    'surface_tilt': params['surface_tilt'],
                    'surface_orientation': params['surface_orientation'],
                    'unit': 'degrees',
                    'peak_power': 1.0,
                    'peak_power_unit': 'kWp',
                    'technology': params['photovoltaic_module'],
                    'system_efficiency': params['system_efficiency'],
                    'shading': 'PVGIS',
                    'azimuth_origin': 'North',
                    'frequency': 'h',
                    'timezone': 'UTC',
                    'power_unit': 'W',
                    'direct_inclined_irradiance_before_reflectivity': [1.0] * n,
                    'diffuse_inclined_irradiance_before_reflectivity': [1.0] * n,
                    'ground_reflected_inclined_irradiance_before_reflectivity': [1.0] * n,
                }
                if v6_modify:
                    v6_modify(obj, params)
                return Response(status=200, url=endpoint, body=json.dumps(obj).encode('utf-8'))

            if endpoint == solar_provider.TEMPERATURE_URL:
                year = int(params['start_date'][:4])
                slots = solar_provider.utc_slots(year)
                n = len(slots)
                obj = {
                    'latitude': params['latitude'],
                    'longitude': params['longitude'],
                    'utc_offset_seconds': 0,
                    'timezone': 'UTC',
                    'hourly_units': {'temperature_2m': '°C'},
                    'hourly': {
                        'time': [t.strftime('%Y-%m-%dT%H:%M') for t in slots],
                        'temperature_2m': [15.0] * n,
                    },
                }
                if temp_modify:
                    temp_modify(obj, params)
                return Response(status=200, url=endpoint, body=json.dumps(obj).encode('utf-8'))

            raise ValueError(f'unexpected endpoint: {endpoint}')

        clock = FakeClock()
        client = ProviderClient(
            transport=_transport,
            clock=clock.monotonic,
            sleep=clock.sleep,
            limiter=RateLimiter(clock.monotonic, clock.sleep),
        )
        return client, calls

    return _factory


def test_fetch_pvgis_early_year_routes_v5(synthetic_transport_factory, solar_request_factory):
    client, calls = synthetic_transport_factory()
    req = solar_request_factory(years=(2010,))
    source = solar_provider.fetch_pvgis(req, client)

    endpoints_called = [endpoint for endpoint, _ in calls]
    assert solar_provider.PVGIS5_URL in endpoints_called
    assert solar_provider.PVGIS_URL not in endpoints_called
    assert solar_provider.TEMPERATURE_URL in endpoints_called

    v5_params = next(params for endpoint, params in calls if endpoint == solar_provider.PVGIS5_URL)
    assert v5_params['startyear'] == 2010
    assert v5_params['endyear'] == 2010
    assert v5_params['mountingplace'] == 'free'

    prov = json.loads(source.provenance_bytes)
    assert len(prov['solar']) == 1
    solar_prov = prov['solar'][0]
    assert solar_prov['year'] == 2010
    assert solar_prov['provider_version'] == '5.3'
    assert solar_prov['endpoint'] == solar_provider.PVGIS5_URL
    assert solar_prov['fallback'] is None
    assert solar_prov['fallback_reason'] is None
    assert all(s == '5.3' for s in solar_prov['hourly_source'])
    assert solar_prov['original_timestamps'][0] == '20100101:0000'
    assert solar_prov['provider_confirmed_equipment']['radiation_db'] == 'PVGIS-SARAH3'

    assert source.artifact.power[0, 0, 0] == 0.4
    assert source.artifact.power.shape == (1, 1, 8784)
    assert solar_provider.np.all(source.artifact.power[0, 0, 8760:] == 0.0)


def test_fetch_pvgis_overlap_v6_preferred_even_valid_zero(synthetic_transport_factory, solar_request_factory):
    def modify_v6(obj, params):
        for i in range(6):
            obj['power'][i] = 0.0

    def modify_v5(obj, params):
        for i in range(6):
            obj['outputs']['hourly'][i]['P'] = 999.0

    client, calls = synthetic_transport_factory(v5_modify=modify_v5, v6_modify=modify_v6)
    req = solar_request_factory(years=(2020,))
    source = solar_provider.fetch_pvgis(req, client)

    endpoints_called = [endpoint for endpoint, _ in calls]
    assert solar_provider.PVGIS_URL in endpoints_called
    assert solar_provider.PVGIS5_URL not in endpoints_called
    assert solar_provider.TEMPERATURE_URL in endpoints_called

    assert source.artifact.power[0, 0, 0] == 0.0
    assert source.artifact.power[0, 0, 5] == 0.0
    assert source.artifact.power[0, 0, 12] == 0.5

    prov = json.loads(source.provenance_bytes)
    solar_prov = prov['solar'][0]
    assert solar_prov['year'] == 2020
    assert solar_prov['provider_version'] == '6'
    assert solar_prov['endpoint'] == solar_provider.PVGIS_URL
    assert solar_prov['fallback'] is None
    assert solar_prov['fallback_reason'] is None
    assert all(s == '6' for s in solar_prov['hourly_source'])
    assert solar_prov['gap_counts']['power'] == 0


def test_fetch_pvgis_v6_missing_power_gets_same_hour_v5_fallback(synthetic_transport_factory, solar_request_factory):
    def modify_v6(obj, params):
        obj['power'][10] = None
        obj['power'][11] = None

    def modify_v5(obj, params):
        obj['outputs']['hourly'][10]['P'] = 350.0
        obj['outputs']['hourly'][11]['P'] = 360.0

    client, calls = synthetic_transport_factory(v5_modify=modify_v5, v6_modify=modify_v6)
    req = solar_request_factory(years=(2020,))
    source = solar_provider.fetch_pvgis(req, client)

    endpoints_called = [endpoint for endpoint, _ in calls]
    assert solar_provider.PVGIS_URL in endpoints_called
    assert solar_provider.PVGIS5_URL in endpoints_called

    assert source.artifact.power[0, 0, 10] == 0.35
    assert source.artifact.power[0, 0, 11] == 0.36
    assert source.artifact.power[0, 0, 0] == 0.5

    prov = json.loads(source.provenance_bytes)
    solar_prov = prov['solar'][0]
    assert solar_prov['provider_version'] == '6'
    assert solar_prov['fallback_reason'] == 'primary_power_missing'
    assert solar_prov['fallback'] is not None
    assert solar_prov['fallback']['provider_version'] == '5.3'
    assert solar_prov['fallback']['endpoint'] == solar_provider.PVGIS5_URL
    assert solar_prov['fallback']['database'] == 'PVGIS-SARAH3'
    assert solar_prov['fallback']['original_timestamps'][10] == '20200101:1000'
    assert solar_prov['fallback']['original_timestamps'][11] == '20200101:1100'

    assert solar_prov['hourly_source'][10] == '5.3'
    assert solar_prov['hourly_source'][11] == '5.3'
    assert solar_prov['hourly_source'][0] == '6'
    assert solar_prov['gap_counts']['power'] == 0


def test_fetch_pvgis_unavailable_v5_plus_bounded_repair(synthetic_transport_factory, solar_request_factory):
    def modify_v6(obj, params):
        obj['power'][9] = 100.0
        obj['power'][10] = None
        obj['power'][11] = None
        obj['power'][12] = 400.0

    def modify_v5(obj, params):
        obj['outputs']['hourly'][10]['P'] = None
        obj['outputs']['hourly'][11]['P'] = None

    client, calls = synthetic_transport_factory(v5_modify=modify_v5, v6_modify=modify_v6)
    req = solar_request_factory(years=(2020,))
    source = solar_provider.fetch_pvgis(req, client)

    assert round(source.artifact.power[0, 0, 10], 6) == 0.2
    assert round(source.artifact.power[0, 0, 11], 6) == 0.3

    prov = json.loads(source.provenance_bytes)
    solar_prov = prov['solar'][0]
    assert solar_prov['hourly_source'][10] == 'interpolated'
    assert solar_prov['hourly_source'][11] == 'interpolated'
    assert solar_prov['gap_method'] == 'linear_bounded_5h'
    assert solar_prov['gap_counts']['power'] == 2

    def modify_v6_long_gap(obj, params):
        obj['power'][10] = None
        obj['power'][11] = None
        obj['power'][12] = None
        obj['power'][13] = None
        obj['power'][14] = None
        obj['power'][15] = None

    def modify_v5_none(obj, params):
        for idx in (10, 11, 12, 13, 14, 15):
            obj['outputs']['hourly'][idx]['P'] = None

    client_bad, _ = synthetic_transport_factory(v5_modify=modify_v5_none, v6_modify=modify_v6_long_gap)
    with pytest.raises(solar_provider.SolarDataGapError, match="gap exceeds maximum consecutive limit of 5 hours"):
        solar_provider.fetch_pvgis(req, client_bad)

    def modify_v6_edge(obj, params):
        obj['power'][0] = None

    def modify_v5_edge(obj, params):
        obj['outputs']['hourly'][0]['P'] = None

    client_edge, _ = synthetic_transport_factory(v5_modify=modify_v5_edge, v6_modify=modify_v6_edge)
    with pytest.raises(solar_provider.SolarDataGapError, match="unbounded gap at sequence boundary"):
        solar_provider.fetch_pvgis(req, client_edge)


def test_fetch_pvgis_malformed_source_rejected(synthetic_transport_factory, solar_request_factory):
    def modify_v5_coord(obj, params):
        obj['inputs']['location']['latitude'] = 0.0

    client_coord, _ = synthetic_transport_factory(v5_modify=modify_v5_coord)
    req_early = solar_request_factory(years=(2010,))
    with pytest.raises(ValueError, match="coordinate mismatch: latitude"):
        solar_provider.fetch_pvgis(req_early, client_coord)

    def modify_v5_db(obj, params):
        obj['inputs']['meteo_data']['radiation_db'] = 'PVGIS-ERA5'

    client_db, _ = synthetic_transport_factory(v5_modify=modify_v5_db)
    with pytest.raises(ValueError, match="radiation_db mismatch"):
        solar_provider.fetch_pvgis(req_early, client_db)

    def modify_v5_neg(obj, params):
        obj['outputs']['hourly'][0]['P'] = -10.0

    client_neg, _ = synthetic_transport_factory(v5_modify=modify_v5_neg)
    with pytest.raises(ValueError, match="invalid physical value in P"):
        solar_provider.fetch_pvgis(req_early, client_neg)

    def modify_v6_gap(obj, params):
        obj['power'][10] = None

    def modify_v5_bad_unit(obj, params):
        obj['meta']['outputs']['hourly']['variables']['P']['units'] = 'kW'

    client_fb_bad, _ = synthetic_transport_factory(v5_modify=modify_v5_bad_unit, v6_modify=modify_v6_gap)
    req_overlap = solar_request_factory(years=(2020,))
    with pytest.raises(ValueError, match="unit mismatch for P: expected W, got kW"):
        solar_provider.fetch_pvgis(req_overlap, client_fb_bad)


def test_fetch_pvgis_separate_v5_v6_provenance(synthetic_transport_factory, solar_request_factory):
    client, calls = synthetic_transport_factory()
    req = solar_request_factory(years=(2010, 2020))
    source = solar_provider.fetch_pvgis(req, client)

    prov = json.loads(source.provenance_bytes)
    assert len(prov['solar']) == 2

    solar_2010 = prov['solar'][0]
    assert solar_2010['year'] == 2010
    assert solar_2010['provider_version'] == '5.3'
    assert solar_2010['endpoint'] == solar_provider.PVGIS5_URL
    assert solar_2010['request_parameters']['startyear'] == 2010
    assert solar_2010['request_parameters']['mountingplace'] == 'free'
    assert solar_2010['original_timestamps'][0] == '20100101:0000'
    assert solar_2010['fallback'] is None
    assert all(s == '5.3' for s in solar_2010['hourly_source'])
    assert solar_2010['provider_confirmed_equipment']['radiation_db'] == 'PVGIS-SARAH3'

    solar_2020 = prov['solar'][1]
    assert solar_2020['year'] == 2020
    assert solar_2020['provider_version'] == '6'
    assert solar_2020['endpoint'] == solar_provider.PVGIS_URL
    assert solar_2020['request_parameters']['start_time'] == '2020-01-01T00:00:00'
    assert solar_2020['request_parameters']['photovoltaic_module'] == 'cSi:Free standing 2025'
    assert solar_2020['original_timestamps'][0] == '2020-01-01T00:00:00Z'
    assert solar_2020['fallback'] is None
    assert all(s == '6' for s in solar_2020['hourly_source'])
    assert solar_2020['provider_confirmed_equipment']['shading'] == 'PVGIS'

    assert '2010' in prov['timestamps_by_year']
    assert '2020' in prov['timestamps_by_year']
    assert '2010' in prov['offset_nanoseconds_by_year']
    assert '2020' in prov['offset_nanoseconds_by_year']

    power_arr = source.artifact.power
    assert power_arr.shape == (1, 2, 8784)
    assert power_arr[0, 0, 0] == 0.4
    assert solar_provider.np.all(power_arr[0, 0, 8760:] == 0.0)
    assert power_arr[0, 1, 0] == 0.5
    assert power_arr[0, 1, 8783] == 0.5




def test_check_solar_coverage_exact_2005_2025(synthetic_transport_factory, solar_request_factory):
    client, calls = synthetic_transport_factory()
    req = solar_request_factory()

    result = solar_provider.check_solar_coverage(req.profile, transport=client)

    assert result['supported'] is True
    assert result['years'] == list(range(2005, 2026))
    assert result['year_range'] == [2005, 2025]
    assert '5.3' in result['sources']
    assert result['sources']['5.3']['verified_boundary_years'] == [2005, 2023]
    assert result['sources']['5.3']['year_min'] == 2005
    assert result['sources']['5.3']['year_max'] == 2023
    assert result['sources']['5.3']['endpoint'] == solar_provider.PVGIS5_URL
    assert '6' in result['sources']
    assert result['sources']['6']['verified_boundary_years'] == [2014, 2025]
    assert result['sources']['6']['year_min'] == 2014
    assert result['sources']['6']['year_max'] == 2025
    assert result['sources']['6']['endpoint'] == solar_provider.PVGIS_URL

    v5_calls = [c for c in calls if c[0] == solar_provider.PVGIS5_URL]
    v6_calls = [c for c in calls if c[0] == solar_provider.PVGIS_URL]
    assert [int(c[1]['startyear']) for c in v5_calls] == [2005, 2023]
    assert [int(c[1]['start_time'][:4]) for c in v6_calls] == [2014, 2025]


def test_check_solar_coverage_invalid_v5_units_raise_value_error(synthetic_transport_factory, solar_request_factory):
    def modify_v5_bad_units(obj, params):
        obj['meta']['outputs']['hourly']['variables']['P']['units'] = 'kW'

    client, _ = synthetic_transport_factory(v5_modify=modify_v5_bad_units)
    req = solar_request_factory()

    with pytest.raises(ValueError, match="unit mismatch for P"):
        solar_provider.check_solar_coverage(req.profile, transport=client)


def test_check_solar_coverage_caching_and_bounded_freshness(synthetic_transport_factory, solar_request_factory, tmp_path):
    client, calls = synthetic_transport_factory()
    req = solar_request_factory()
    cache = solar_provider.TenantCache(tmp_path / 'cache', 'b' * 32)

    # First call: cache miss, probes provider (4 calls)
    res1 = solar_provider.check_solar_coverage(req.profile, transport=client, cache=cache)
    assert res1['supported'] is True
    assert len(calls) == 4
    first_date = res1['retrieval_date_utc']

    # Second call: cache hit, 0 provider calls, exact original retrieval timestamp preserved
    res2 = solar_provider.check_solar_coverage(req.profile, transport=client, cache=cache)
    assert res2['supported'] is True
    assert len(calls) == 4
    assert res2['retrieval_date_utc'] == first_date
    assert res2['retrieval_date'] == first_date
    assert res2['years'] == res1['years']

    # Expired freshness triggers live re-probe
    res3 = solar_provider.check_solar_coverage(req.profile, transport=client, cache=cache, max_age_seconds=0)
    assert res3['supported'] is True
    assert len(calls) == 8

    # Integrity mismatch raises ValueError (no silent fallback)
    cov_files = list(cache.directory.glob('coverage-*.json'))
    assert len(cov_files) == 1
    cov_files[0].write_text('{"bad": "json"}')
    with pytest.raises(ValueError, match="cache integrity mismatch"):
        solar_provider.check_solar_coverage(req.profile, transport=client, cache=cache)


def test_hourly_response_validation_before_cache_persistence(synthetic_transport_factory, solar_request_factory, tmp_path):
    def bad_v6(obj, params):
        obj['power'][10] = -50.0  # Physical violation

    client_bad, _ = synthetic_transport_factory(v6_modify=bad_v6)
    client_good, _ = synthetic_transport_factory()
    req = solar_request_factory(years=(2020,))
    cache = solar_provider.TenantCache(tmp_path / 'cache', 'c' * 32)
    req_cached = solar_provider.SolarRequest(
        req.profile, req.years, req.loss_percent, req.mounting, req.technology, req.source_policy, cache=cache
    )

    # Validation failure raises error
    with pytest.raises(ValueError, match="invalid physical array: power"):
        solar_provider.fetch_pvgis(req_cached, transport=client_bad)

    # CRITICAL: Verify NO cache file was persisted for the invalid response!
    assert list(cache.directory.glob('*.json')) == []

    # Valid response succeeds and is cached
    source_good = solar_provider.fetch_pvgis(req_cached, transport=client_good)
    assert len(list(cache.directory.glob('*.json'))) == 2  # 1 solar + 1 temperature

    # Subsequent call with failing client succeeds by reading the valid cache entry
    source_cached = solar_provider.fetch_pvgis(req_cached, transport=client_bad)
    assert source_cached.artifact.source_digest == source_good.artifact.source_digest



@pytest.mark.parametrize("invalid_loss", [-1.0, -0.01, 100.01, 105.0, float('nan'), float('inf'), float('-inf'), '14', None, True])
def test_loss_percent_backend_contract_rejections(solar_request_factory, synthetic_transport_factory, invalid_loss):
    client, calls = synthetic_transport_factory()
    req = solar_request_factory(years=(2020,), loss=invalid_loss)
    with pytest.raises(ValueError, match="invalid loss"):
        solar_provider.fetch_pvgis(req, transport=client)
    assert not calls


@pytest.mark.parametrize("valid_loss", [0.0, 0, 14.5, 99.9, 100.0, 100])
def test_loss_percent_backend_contract_boundaries_and_fractions(solar_request_factory, synthetic_transport_factory, valid_loss):
    client, calls = synthetic_transport_factory()
    req = solar_request_factory(years=(2020,), loss=valid_loss)
    source = solar_provider.fetch_pvgis(req, transport=client)
    assert source is not None
    v6_params = [p for endpoint, p in calls if endpoint == solar_provider.PVGIS_URL][0]
    expected_eff = 1.0 - float(valid_loss) / 100.0
    assert abs(v6_params['system_efficiency'] - expected_eff) < 1e-6



def test_v6_negative_power_clamping_and_provenance(synthetic_transport_factory, solar_request_factory):
    observed_sample = -0.0024951226
    raw_digest_holder = []

    def modify_v6(obj, params):
        # Inject observed live PVGIS 6 sample at index 100
        obj['power'][100] = observed_sample
        raw_digest_holder.append(solar_provider._digest(solar_provider.canonical_json(obj)))

    client, calls = synthetic_transport_factory(v6_modify=modify_v6)
    req = solar_request_factory(years=(2025,))

    source = solar_provider.fetch_pvgis(req, transport=client)
    assert source is not None

    # Power in artifact is clamped to zero
    power_kw = source.artifact.power[0, 0, :]
    assert power_kw[100] == 0.0

    # Provenance contains correction count and min
    prov = json.loads(source.provenance_bytes)['solar'][0]
    assert prov['correction_count'] == 1
    assert prov['correction_min'] == observed_sample

    # Raw provider response digest preserved
    assert prov['response_sha256'] == raw_digest_holder[0]


@pytest.mark.parametrize("invalid_power", [
    -0.010001,   # Larger negative than -0.01 W
    -0.02,       # Larger negative than -0.01 W
    -1.0,        # Substantial negative
])
def test_v6_negative_power_rejections(synthetic_transport_factory, solar_request_factory, invalid_power):
    def modify_v6(obj, params):
        obj['power'][50] = invalid_power

    client, _ = synthetic_transport_factory(v6_modify=modify_v6)
    req = solar_request_factory(years=(2025,))

    with pytest.raises(ValueError, match="invalid physical array: power"):
        solar_provider.fetch_pvgis(req, transport=client)


@pytest.mark.parametrize("nonfinite", [float('nan'), float('inf'), float('-inf')])
def test_v6_negative_power_nonfinite_rejected(nonfinite):
    slots = [100.0] * 8760
    slots[50] = nonfinite
    with pytest.raises(ValueError, match="invalid physical array: power"):
        solar_provider._validate_and_clamp_v6_power(slots, 8760)


def test_v6_repeated_isolated_negatives_rejected(synthetic_transport_factory, solar_request_factory):
    def modify_v6(obj, params):
        # Two isolated small negatives in same roof-year
        obj['power'][50] = -0.0024951226
        obj['power'][60] = -0.005

    client, _ = synthetic_transport_factory(v6_modify=modify_v6)
    req = solar_request_factory(years=(2025,))

    with pytest.raises(ValueError, match="invalid physical array: power"):
        solar_provider.fetch_pvgis(req, transport=client)


def test_v6_negative_boundary_exact_minus_point_zero_one(synthetic_transport_factory, solar_request_factory):
    def modify_v6(obj, params):
        obj['power'][50] = -0.01  # Exact inclusive boundary

    client, _ = synthetic_transport_factory(v6_modify=modify_v6)
    req = solar_request_factory(years=(2025,))

    source = solar_provider.fetch_pvgis(req, transport=client)
    assert source.artifact.power[0, 0, 50] == 0.0
    prov = json.loads(source.provenance_bytes)['solar'][0]
    assert prov['correction_count'] == 1
    assert prov['correction_min'] == -0.01


def test_v6_negative_power_coverage_and_cache_validation(synthetic_transport_factory, solar_request_factory, tmp_path):
    observed_sample = -0.0024951226

    def modify_v6_valid_neg(obj, params):
        obj['power'][10] = observed_sample

    # 1. check_solar_coverage succeeds with isolated small negative
    client_valid, _ = synthetic_transport_factory(v6_modify=modify_v6_valid_neg)
    req = solar_request_factory()
    res = solar_provider.check_solar_coverage(req.profile, transport=client_valid)
    assert res['supported'] is True

    # 2. check_solar_coverage rejects larger negative (-0.02)
    def modify_v6_large_neg(obj, params):
        obj['power'][10] = -0.02

    client_bad, _ = synthetic_transport_factory(v6_modify=modify_v6_large_neg)
    with pytest.raises(ValueError, match="invalid physical array: power"):
        solar_provider.check_solar_coverage(req.profile, transport=client_bad)

    # 3. Cache validation with fetch_pvgis: valid small negative is cached with raw digest
    cache = solar_provider.TenantCache(tmp_path / 'cache', 'd' * 32)
    req_cached = solar_provider.SolarRequest(
        req.profile, (2025,), req.loss_percent, req.mounting, req.technology, req.source_policy, cache=cache
    )
    source = solar_provider.fetch_pvgis(req_cached, transport=client_valid)
    assert source.artifact.power[0, 0, 10] == 0.0

    # Ensure cache entry was created and contains raw negative
    cache_files = list(cache.directory.glob('*.json'))
    assert len(cache_files) == 2  # solar + temp
    solar_cache = [f for f in cache_files if 'v6' in str(f.read_text()) or 'power' in str(f.read_text())][0]
    cached_data = solar_provider.strict_json(solar_cache.read_bytes())
    assert cached_data['response']['power'][10] == observed_sample
