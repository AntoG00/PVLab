"""Repeated performance-map queries preserve output and stay local to equipment."""
import test_support
from unittest import mock
import numpy as np
import feasibility.heatpump_dispatch as hp
from feasibility.heatpump import create_provisional_fallback_map


def _make_synthetic_inputs(n_hours=24):
    outdoor = np.array([2., 7.] * (n_hours // 2))
    space_req = np.full(n_hours, 3.)
    draw_req = np.full(n_hours, .5)
    space_active = np.array([True, False] * (n_hours // 2))
    reheat_allowed = np.ones(n_hours, dtype=bool)
    hygiene = np.zeros(n_hours, dtype=bool)
    hygiene[n_hours // 2] = True
    return outdoor, space_req, draw_req, space_active, reheat_allowed, hygiene


def test_heatpump_dispatch_caching_and_exact_outputs():
    inputs = _make_synthetic_inputs()
    original = hp.interpolate_heat_pump_performance
    calls = []

    def count_interp(*args):
        calls.append(1)
        return original(*args)

    with mock.patch.object(hp, 'interpolate_heat_pump_performance', count_interp):
        cached = hp.simulate_shared_heat_pump(*inputs)
    cached_calls = len(calls)
    calls.clear()
    with mock.patch.object(hp, 'interpolate_heat_pump_performance', count_interp), \
         mock.patch.object(hp, 'lru_cache', lambda **kwargs: lambda fn: fn):
        uncached = hp.simulate_shared_heat_pump(*inputs)
    assert cached_calls < len(calls)
    for key, value in cached.items():
        if isinstance(value, np.ndarray):
            np.testing.assert_array_equal(value, uncached[key])
        else:
            assert value == uncached[key]


def test_cache_per_run_different_equipment():
    inputs = _make_synthetic_inputs()
    small = create_provisional_fallback_map(nominal_capacity_kw=6.)
    large = create_provisional_fallback_map(nominal_capacity_kw=12.)
    first = hp.simulate_shared_heat_pump(*inputs, equipment_map=small)
    second = hp.simulate_shared_heat_pump(*inputs, equipment_map=large)
    for key in ('space_capacity_kw', 'dhw_capacity_kw'):
        assert np.all(second[key] > first[key])
