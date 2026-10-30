"""Tests for Phase 4 profile cache identity, runtime keys, and price fingerprints."""
import os
import subprocess
import sys
from pathlib import Path
import ast
import inspect

import pytest

from feasibility import engine, heatpump_integration, schedule_optimizer, runtime_context, fiscal

REPO_ROOT = Path(__file__).resolve().parents[1]

# Complete feasibility cache classification (vendor/browser caches are not model caches):
# Runtime: all entries in RUNTIME_CACHES, including forecast_calendar_slots,
# which reads the runtime-dependent mapping and therefore cannot remain year-only.
# Pure calendar: civil_hours/local_standard_dates take complete year tuples;
# Italy/legacy timestamp semantics are fixed code, covered by model_build_digest.
# Pure artifact preview: evidence_store._coverage_cache is keyed by inventory
# root, immutable upload digests and selection; it never supplies simulation inputs.
# Request-local: fair_price.all_residuals; heatpump_dispatch._cached_interpolate
# closes over one equipment map; candidate pv_profile_cache/schedule_context_cache
# and schedule_optimizer.context_cache belong to one sizing evaluation.
# Request-local ContextVar: _SIZING_DISPATCH_MEMO, capped at 2048/128 MiB, reset
# in finally (including failures); no global registry or persisted result cache.
# Compiled code: physical_dispatch @njit(cache=True), array/scalar arguments only;
# runtime_context.model_build_digest caches source/runtime identity per process.
RUNTIME_CACHES = {
    engine: '''_load_cache_cached _pv_profile_cached _price_cache
        _forecast_calendar_mapping _forecast_calendar_slots _forecast_runs_cached
        _energy_cache_cached _chart_cache_cached _resolve_routing_cached
        _battery_sizing_cached _solar_sizing_cached _solar_sizing_sensitivity_cached
        _dayhour_trace_cached _dispatch_matrix_cached'''.split(),
    heatpump_integration: '''_cached_source_heat_pump_hourly
        _cached_forecast_heat_pump_hourly get_source_reference_degree_hours'''.split(),
    schedule_optimizer: ['_cached_switch_hour_tradeoff'],
    fiscal: ['_bound_policy'],
}


def test_all_cache_decorators_classified():
    expected = {(module.__name__.rsplit('.', 1)[-1], name)
                for module, names in RUNTIME_CACHES.items() for name in names}
    expected.update({('engine', '_civil_hours_cached'), ('engine', '_local_standard_dates_cached'),
                     ('engine', 'all_residuals'), ('heatpump_dispatch', '_cached_interpolate'),
                     ('runtime_context', 'model_build_digest')})
    found = set()
    for source in (REPO_ROOT / 'feasibility').glob('*.py'):
        for node in ast.walk(ast.parse(source.read_text())):
            if isinstance(node, ast.FunctionDef) and any(
                    isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
                    and d.func.id == 'lru_cache' for d in node.decorator_list):
                found.add((source.stem, node.name))
    assert found == expected, f'Unclassified or missing cache: {found ^ expected}'


@pytest.mark.parametrize('module,name', [(m, n) for m, names in RUNTIME_CACHES.items() for n in names])
def test_every_runtime_cache_rejects_wrong_key(module, name):
    fn = getattr(module, name)
    params = list(inspect.signature(fn).parameters.values())
    assert params[0].name == 'runtime_key'
    # Rejection must precede any inspection of otherwise invalid physical inputs.
    args = ['wrong-revision'] + [None for p in params[1:] if p.default is inspect.Parameter.empty]
    with pytest.raises(ValueError, match='runtime key'):
        fn(*args)


def _run_py(code: str) -> str:
    env = dict(os.environ, PYTHONPATH=f"{REPO_ROOT}:{REPO_ROOT / 'feasibility/vendor'}:{os.environ.get('PYTHONPATH', '')}")
    res = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert res.returncode == 0, f"Subprocess failed ({res.returncode}):\n{res.stderr}\n{res.stdout}"
    return res.stdout.strip()


_CTX_SETUP = """
import numpy as np
from feasibility.runtime_context import RuntimeContext, initialize_runtime, assert_runtime_key
from feasibility import engine

def make_ctx(**kwargs):
    base = dict(
        owner_id='owner-1', input_revision='rev-1', dataset_digest='ds-1',
        price_revision='pr-1', defaults_snapshot={'scop': 3.5},
        thermal_reference={'balance_temp': 18.0}, fiscal_revision='fisc-1',
        calendar_version='legacy_pvgis_v1', dataset={}, weather_identity=('synth', 'v1'),
        records_snapshot=(), metadata={'compiler_version': 'legacy-snapshot-v1'},
    )
    base.update(kwargs)
    return RuntimeContext(**base)
"""


def test_runtime_key_determinism_and_subprocess_reproducibility():
    script = _CTX_SETUP + """
ctx = make_ctx()
assert len(ctx.key) == 64 and all(c in '0123456789abcdef' for c in ctx.key.lower())
assert isinstance(ctx.model_build_id, str) and len(ctx.model_build_id) > 0
assert isinstance(ctx.price_data, (dict, type(ctx.defaults_snapshot)))
initialize_runtime(ctx)
assert assert_runtime_key(ctx.key) is ctx
try:
    assert_runtime_key('0' * 64)
except ValueError:
    pass
else:
    raise AssertionError('assert_runtime_key accepted mismatched key')
print(ctx.key)
"""
    key1 = _run_py(script)
    key2 = _run_py(script)
    assert key1 == key2 and len(key1) == 64


def test_runtime_key_differs_on_all_identities():
    script = _CTX_SETUP + """
ctx_base = make_ctx()
variations = [
    dict(owner_id='owner-2'),
    dict(input_revision='rev-2'),
    dict(dataset_digest='ds-2'),
    dict(price_revision='pr-2'),
    dict(fiscal_revision='fisc-2'),
    dict(calendar_version='cal-2'),
    dict(metadata={'compiler_version': 'compiler-2'}),
    dict(model_build_id='model-build-2'),
    dict(defaults_snapshot={'scop': 4.0}),
    dict(thermal_reference={'balance_temp': 20.0}),
]
for var in variations:
    other = make_ctx(**var)
    assert other.key != ctx_base.key, f"Key collision on {var}"
"""
    _run_py(script)


def test_synthetic_contexts_series_and_array_immutability():
    script = _CTX_SETUP + """
shape = (8784,)
load_a, load_b = np.full(shape, 1.0), np.full(shape, 2.0)
gas_a, gas_b = np.full(shape, 0.5), np.full(shape, 1.5)
solar_a, solar_b = np.full(shape, 0.8), np.full(shape, 0.2)
price_a, price_b = np.full(shape, 50.0), np.full(shape, 100.0)

ctx_a = make_ctx(dataset={'load': load_a, 'gas': gas_a, 'solar': solar_a},
                 price_data={'hourly': price_a}, dataset_digest='ds-a', price_revision='pr-a')
ctx_b = make_ctx(dataset={'load': load_b, 'gas': gas_b, 'solar': solar_b},
                 price_data={'hourly': price_b}, dataset_digest='ds-b', price_revision='pr-b')
assert ctx_a.key != ctx_b.key

load_a[0] = 999.0
price_a[0] = 999.0
assert float(ctx_a.dataset['load'][0]) == 1.0
assert float(ctx_a.price_data['hourly'][0]) == 50.0

try:
    ctx_a.dataset['load'][0] = 42.0
except (ValueError, TypeError, RuntimeError):
    pass
else:
    raise AssertionError("Dataset array allowed modification")

try:
    ctx_a.price_data['hourly'].setflags(write=True)
except (ValueError, TypeError, RuntimeError):
    pass
else:
    raise AssertionError("price_data array allowed write=True")
"""
    _run_py(script)


def test_price_fingerprint_modes_and_caching():
    script_base = _CTX_SETUP + """
ctx = make_ctx()
initialize_runtime(ctx)
settings = {'export_price_profile': 'flat_benchmark', 'export_price_window': '12m', 'export_price_mode': 'spot'}
fp = engine.price_fingerprint(settings)
assert isinstance(fp, int)
for mod in [{'export_price_profile': 'hourly'}, {'export_price_window': '24m'}, {'export_price_mode': 'average'}]:
    assert engine.price_fingerprint(dict(settings, **mod)) != fp
print(fp)
"""
    fp1 = int(_run_py(script_base))
    fp2 = int(_run_py(script_base))
    assert fp1 == fp2

    script_other_rev = _CTX_SETUP + """
ctx = make_ctx(price_revision='pr-changed')
initialize_runtime(ctx)
settings = {'export_price_profile': 'flat_benchmark', 'export_price_window': '12m', 'export_price_mode': 'spot'}
print(engine.price_fingerprint(settings))
"""
    fp_other = int(_run_py(script_other_rev))
    assert fp_other != fp1


def test_model_build_hashes_source_bytes_and_pins_process(tmp_path):
    script = '''
import os
from pathlib import Path
from unittest.mock import patch
from feasibility import runtime_context as rc
root = Path(ROOT)
source = root / 'kernel.py'
source.write_text('MODEL = 1')
stamp = source.stat().st_mtime_ns
with patch.object(rc, '__file__', str(root / 'runtime_context.py')):
    rc.model_build_digest.cache_clear()
    original = rc.model_build_digest()
    source.write_text('MODEL = 2')
    os.utime(source, ns=(stamp, stamp))
    assert rc.model_build_digest() == original
    rc.model_build_digest.cache_clear()
    assert rc.model_build_digest() != original
'''
    _run_py(script.replace('ROOT', repr(str(tmp_path))))


_SYNTHETIC_PROFILES = r'''
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch
import numpy as np
from feasibility import engine
from feasibility.legacy_profile import import_legacy_profile, load_legacy_profile

root = Path(ROOT)
engine.HERE = root / 'source' / 'feasibility'
(engine.HERE / 'data').mkdir(parents=True)
research = engine.HERE.parent / 'CodexProject'
research.mkdir()
(research / 'step1_research_results.json').write_text('{}')
shape = (1, 8784)
valid = np.zeros(shape, bool); valid[:, :8760] = True
stamps = [dt.datetime(2023, 1, 1, 1, 10) + dt.timedelta(hours=i) for i in range(8760)]
month = np.zeros(shape, np.int16); hour = month.copy()
month[0, :8760] = [s.month for s in stamps]
hour[0, :8760] = [s.hour for s in stamps]
price_path = engine.HERE / 'data/hourly_prices.npz'
revisions = []
for factor in (1., 2.):
    load = np.full(shape, .3 * factor) * valid
    power = np.tile(np.maximum(np.sin((hour - 6) * np.pi / 12), 0) * .6 * factor * valid, (5, 1, 1))
    monthly = [float(load[valid & (month == m)].sum()) for m in range(1, 13)]
    data = dict(years=np.array([2023]), valid=valid, month=month, hour=hour,
                load_standard=load, load_civil=load, temperature=np.full(shape, 14.),
                power=power, beam_loss=np.zeros_like(power), diffuse_loss=np.zeros_like(power),
                audit=dict(monthly_kwh=monthly, pvgis_yields={f'plane-{i}': {} for i in range(5)}))
    np.savez(price_path, pzo=(.1 + factor * .01 * (hour >= 12)) * valid)
    os.utime(price_path, ns=(123456789000, 123456789000))
    with patch('feasibility.weather_dataset.load_active_weather_dataset', return_value=(data, ('synthetic', 'weather', 'audit'))), \
         patch.object(engine, 'GAS_MONTHLY', np.full(12, 50. * factor)):
        revisions.append(import_legacy_profile(root / 'profiles', root / 'defaults.json'))
    assert price_path.stat().st_mtime_ns == 123456789000
assert revisions[0] != revisions[1]
first, second = [load_legacy_profile(root / 'profiles', r) for r in revisions]
assert first.price_revision != second.price_revision and first.key != second.key
assert first.dataset['power'].shape == second.dataset['power'].shape
print(json.dumps(revisions))
'''

_SYNTHETIC_EVALUATION = r'''
from dataclasses import is_dataclass, fields
import hashlib
import json
from pathlib import Path
from unittest.mock import patch
import numpy as np
from feasibility import engine as e, heatpump_integration as hp, schedule_optimizer as so
from feasibility.legacy_profile import load_legacy_profile
from feasibility.runtime_context import initialize_runtime
ctx = initialize_runtime(load_legacy_profile(Path(ROOT) / 'profiles', REVISION))
s = e.validate(dict(load_scenario='electrified', planning_heat_pump=False,
    routing_priority='battery_first', horizon_years=2, battery_kwh=0,
    south_panels=2, secondary_south_panels=0, west_panels=0, east_panels=0,
    dormer_panels=0, flat_panels=0, pv_dispatch_model='physical'))
key = tuple((k, e.freeze(s[k])) for k in e.ENERGY_KEYS)

def collect():
    return dict(load=e.load_profiles(s), pv=e.pv_profile(s), price=e.price_profile(s),
        mapping=e._forecast_calendar_mapping(ctx.key, 2027),
        degree=hp.get_source_reference_degree_hours(ctx.key, 18.),
        source_hp=hp.get_source_heat_pump_hourly(s),
        forecast_hp=hp.get_forecast_heat_pump_hourly(s, 2027),
        energy=e._energy_cache(key, e.price_fingerprint(s)),
        # Compare the cached numerical payload; the public wrapper adds timing.
        schedule=so._cached_switch_hour_tradeoff(ctx.key,
            tuple(sorted((k, e.freeze(v)) for k, v in s.items())), e.price_fingerprint(s)))

def equal(a, b):
    if isinstance(a, np.ndarray):
        assert a.dtype == b.dtype
        np.testing.assert_array_equal(a, b)
    elif isinstance(a, dict):
        assert a.keys() == b.keys()
        for k in a: equal(a[k], b[k])
    elif isinstance(a, (list, tuple)):
        assert len(a) == len(b)
        for x, y in zip(a, b): equal(x, y)
    elif is_dataclass(a):
        assert type(a) is type(b)
        for field in fields(a): equal(getattr(a, field.name), getattr(b, field.name))
    else:
        assert a == b, (a, b)

# The source artifact now contains the second revision, with identical mtime.
# No warm or cold calculation is allowed to open it or any NPZ after binding.
with patch.object(np, 'load', side_effect=AssertionError('Mutable price reload')):
    cold = collect()
    warm = collect()
    equal(cold, warm)
    for fn in (e._load_cache_cached, e._pv_profile_cached, e._price_cache,
               e._forecast_calendar_mapping, e._energy_cache_cached,
               hp.get_source_reference_degree_hours, hp._cached_source_heat_pump_hourly,
               hp._cached_forecast_heat_pump_hourly, so._cached_switch_hour_tradeoff):
        assert fn.cache_info().hits > 0, fn.__name__
    # Disable every LRU, including nested physical dependencies, in this worker.
    for module in (e, hp, so):
        for name, fn in list(vars(module).items()):
            if hasattr(fn, 'cache_info') and hasattr(fn, '__wrapped__'):
                setattr(module, name, fn.__wrapped__)
    disabled = collect()
    equal(cold, disabled)
raw = ctx.price_data['hourly_prices.npz']['pzo']
valid = ctx.dataset['valid']
mean = (raw * valid).sum(axis=1) / valid.sum(axis=1)
np.testing.assert_array_equal(cold['price'][0], raw / mean[:, None])
print(json.dumps(dict(key=ctx.key, fingerprint=e.price_fingerprint(s),
    load=float(cold['load'][0].sum()), pv=float(cold['pv'].sum()),
    heat=float(cold['load'][2]['heating_kwh']),
    prices=hashlib.sha256(cold['price'][0].tobytes()).hexdigest(), exact=True)))
'''


def test_separate_revisions_same_mtime_exact_disabled_cold_warm(tmp_path):
    import json
    setup = _SYNTHETIC_PROFILES.replace('ROOT', repr(str(tmp_path)))
    revisions = json.loads(_run_py(setup))
    outputs = []
    for revision in revisions:
        script = _SYNTHETIC_EVALUATION.replace('ROOT', repr(str(tmp_path))).replace('REVISION', repr(revision))
        outputs.append(json.loads(_run_py(script)))
    assert all(result['exact'] for result in outputs)
    for name in ('key', 'fingerprint', 'load', 'pv', 'heat', 'prices'):
        assert outputs[0][name] != outputs[1][name], name
