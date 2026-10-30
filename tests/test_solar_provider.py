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
