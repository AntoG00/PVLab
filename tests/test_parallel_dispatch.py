"""Parallel weather paths must retain the serial dispatch model and input buffers."""
import test_support
import numpy as np
import pytest
from numba import njit, get_num_threads, set_num_threads
from feasibility import engine
from feasibility.assumptions import defaults
from feasibility.physical_dispatch import physical_parameters

dispatch_serial = njit(cache=False)(engine.dispatch_lifetime.py_func)


def _build_synthetic_weather():
    pv_day = np.array([0, 0, 0, 0, 0, 0, .5, 1.5, 3., 4.5, 5.5, 6.,
                       6., 5.5, 4.5, 3., 1.5, .5, 0, 0, 0, 0, 0, 0])
    load_day = np.array([.5, .4, .4, .4, .5, .8, 1.5, 2., 1.8, 1.2, 1., 1.1,
                         1.2, 1., 1.1, 1.3, 1.6, 2.2, 2.5, 2.2, 1.8, 1.4, .9, .6])
    pv = np.stack([pv_day * scale for scale in (1., .4, .75)])
    load = np.stack([load_day * scale for scale in (1., 1.3, .8)])
    ev = np.full_like(pv, .5)
    valid = np.ones(pv.shape, dtype=bool)
    valid[1, -2:] = False
    price = np.ones_like(pv)
    month = np.full(pv.shape, 6, dtype=np.int32)
    month[2] = 12
    hour = np.tile(np.arange(24, dtype=np.int32), (3, 1))
    return pv, load, ev, valid, price, month, hour


@pytest.mark.parametrize('dc,mode,capacity,replaceyear,ev_start,use_schedule', [
    (False, 'legacy', 10., 2, 2, False),
    (True, 'legacy', 0., 0, 0, False),
    (False, 'scheduled_hybrid', 12., 2, 2, True),
    (True, 'grid_first', 10., 3, 3, False),
    (True, 'battery_first', 10., 2, 2, True),
    (False, 'dynamic_reserve', 10., 2, 2, True),
])
def test_parallel_dispatch_matches_serial(dc, mode, capacity, replaceyear, ev_start, use_schedule):
    assert engine.dispatch_lifetime.targetoptions['parallel'] is True
    arrays = _build_synthetic_weather()
    originals = [a.copy() for a in arrays]
    pv, load, ev, valid, price, month, hour = arrays
    if mode == 'legacy':
        physical = (0., 1., 1., 1., 1., 0., 1.)
    else:
        physical = physical_parameters(dict(defaults(), pv_dispatch_model='physical', routing_priority=mode))
    args = (pv, load, ev, valid, price, 4, ev_start, 6., capacity, 5., 5., .95,
            .02, .005, .015, .0001, replaceyear, dc, physical,
            month if use_schedule else None, hour if use_schedule else None)
    expected = dispatch_serial(*args)
    prior_threads = get_num_threads()
    try:
        for threads in sorted({1, min(4, prior_threads)}):
            set_num_threads(threads)
            actual = engine.dispatch_lifetime(*args)
            assert np.isfinite(actual).all()
            assert (actual >= -1e-9).all()
            np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=1e-12)
    finally:
        set_num_threads(prior_threads)
    for array, original in zip(arrays, originals):
        np.testing.assert_array_equal(array, original)
