import os
import sys
import threading
import time
from pathlib import Path
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TOOLS_DIR = _REPO_ROOT / 'tools'
for _p in (str(_REPO_ROOT), str(_TOOLS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from regression import get_test_workers, run_bounded_searches


class FakeEngine:
    def __init__(self, barrier=None, delay=0.0):
        self.barrier = barrier
        self.delay = delay
        self.calls = []
        self.active_threads = set()
        self.max_concurrent = 0
        self.lock = threading.Lock()

    def _track(self, name):
        with self.lock:
            self.calls.append(name)
            self.active_threads.add(threading.get_ident())
            if len(self.active_threads) > self.max_concurrent:
                self.max_concurrent = len(self.active_threads)
        if self.barrier:
            self.barrier.wait(timeout=2.0)
        if self.delay > 0:
            time.sleep(self.delay)
        with self.lock:
            self.active_threads.remove(threading.get_ident())

    def optimize_configuration(self, bounded, search_opt):
        self._track('optimizer')
        return {'res': 'opt', 'in': search_opt}

    def solar_sizing(self, bounded, search_solar):
        self._track('solar_sizing')
        return {'res': 'solar', 'in': search_solar}

    def battery_sizing(self, bounded, search_battery):
        self._track('battery_sizing')
        return {'res': 'battery', 'in': search_battery}


def test_run_bounded_searches_overlap_barrier():
    barrier = threading.Barrier(3)
    engine = FakeEngine(barrier=barrier)
    bounded = {'settings': 'active'}
    search = {'optimizer': 1, 'solar': 2, 'battery': 3}
    res = run_bounded_searches(engine, bounded, search, workers=3)
    assert res['optimizer'] == {'res': 'opt', 'in': 1}
    assert res['solar_sizing'] == {'res': 'solar', 'in': 2}
    assert res['battery_sizing'] == {'res': 'battery', 'in': 3}
    assert engine.max_concurrent == 3


def test_run_bounded_searches_cap3():
    barrier = threading.Barrier(3)
    engine = FakeEngine(barrier=barrier)
    bounded = {'settings': 'active'}
    search = {'optimizer': 1, 'solar': 2, 'battery': 3}
    res = run_bounded_searches(engine, bounded, search, workers=16)
    assert set(res.keys()) == {'optimizer', 'solar_sizing', 'battery_sizing'}
    assert engine.max_concurrent == 3


def test_run_bounded_searches_serial_order():
    engine = FakeEngine()
    bounded = {'settings': 'active'}
    search = {'optimizer': 1, 'solar': 2, 'battery': 3}
    res = run_bounded_searches(engine, bounded, search, workers=1)
    assert list(res.keys()) == ['optimizer', 'solar_sizing', 'battery_sizing']
    assert engine.calls == ['optimizer', 'solar_sizing', 'battery_sizing']
    assert engine.max_concurrent == 1


def test_run_bounded_searches_deterministic_results():
    class DelayEngine(FakeEngine):
        def optimize_configuration(self, bounded, search_opt):
            time.sleep(0.03)
            return {'id': 'opt'}

        def solar_sizing(self, bounded, search_solar):
            time.sleep(0.01)
            return {'id': 'solar'}

        def battery_sizing(self, bounded, search_battery):
            time.sleep(0.02)
            return {'id': 'battery'}

    engine = DelayEngine()
    res = run_bounded_searches(engine, {}, {'optimizer': 'o', 'solar': 's', 'battery': 'b'}, workers=3)
    assert list(res.keys()) == ['optimizer', 'solar_sizing', 'battery_sizing']
    assert res == {
        'optimizer': {'id': 'opt'},
        'solar_sizing': {'id': 'solar'},
        'battery_sizing': {'id': 'battery'},
    }


def test_run_bounded_searches_exceptions_propagate():
    class FailingEngine(FakeEngine):
        def solar_sizing(self, bounded, search_solar):
            raise ValueError('solar_sizing failure')

    engine = FailingEngine()
    with pytest.raises(ValueError, match='solar_sizing failure'):
        run_bounded_searches(engine, {}, {'optimizer': 1, 'solar': 2, 'battery': 3}, workers=3)


@pytest.mark.parametrize('bad_worker', [True, False, 0, -1, -10, '2', 1.5, None])
def test_run_bounded_searches_invalid_workers(bad_worker):
    engine = FakeEngine()
    with pytest.raises(ValueError, match='workers must be a positive integer'):
        run_bounded_searches(engine, {}, {'optimizer': 1, 'solar': 2, 'battery': 3}, workers=bad_worker)


def test_get_test_workers_valid(monkeypatch):
    monkeypatch.delenv('PV_TEST_WORKERS', raising=False)
    assert get_test_workers() == 1
    assert get_test_workers(default=2) == 2

    monkeypatch.setenv('PV_TEST_WORKERS', '3')
    assert get_test_workers() == 3


@pytest.mark.parametrize('bad_env', ['0', '-1', '-10', 'junk', '1.5', 'True', ''])
def test_get_test_workers_invalid(monkeypatch, bad_env):
    monkeypatch.setenv('PV_TEST_WORKERS', bad_env)
    with pytest.raises(ValueError, match='PV_TEST_WORKERS must be a positive integer'):
        get_test_workers()
