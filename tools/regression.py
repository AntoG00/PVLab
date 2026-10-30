#!/usr/bin/env python3
"""Exact private legacy regression capture. Never refresh an existing reference.

Manifest paths are relative to its private directory. Inputs include explicit
absence records. Capture copies current Python source into a disposable tree,
then installs only manifest-bound inputs before importing weather_dataset/engine.
The original live-file test guard remains installed throughout the worker.

Only root compute_seconds/generated_at_utc are ignored by compare_outputs.
Report verification.report_generated_utc is removed at its exact known path.
No numerical, physical, financial, or provenance-version fields are omitted.
No identity mapping is currently needed: the original pointer ID is retained.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, fields, is_dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

# The documented generic command also works from a different system Python.
# Bootstrap uses stdlib only; scientific imports happen on the pinned runtime.
if __name__ == '__main__' and '--manifest' in sys.argv:
    try:
        boot_manifest = Path(sys.argv[sys.argv.index('--manifest') + 1])
        boot = json.loads(boot_manifest.read_text())['runtime_paths']
        if Path(sys.executable).resolve() != Path(boot['python']).resolve():
            os.environ['PYTHONPATH'] = os.pathsep.join((boot['vendor'], str(Path(__file__).resolve().parents[1])))
            os.execv(boot['python'], [boot['python'], *sys.argv])
    except (OSError, ValueError, KeyError, IndexError):
        pass  # The normal CLI emits the validation error.

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
THREAD_ENV = {k: '1' for k in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS',
              'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS', 'NUMEXPR_NUM_THREADS')}
THREAD_ENV.update(PYTHONHASHSEED='0', TZ='Europe/Rome')
IGNORED_ROOT_FIELDS = frozenset(('compute_seconds', 'generated_at_utc'))


@dataclass(frozen=True)
class Difference:
    path: str
    reason: str


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def compare_outputs(expected, actual):
    """Return exact structural/value differences; never apply a tolerance."""
    differences = []

    def visit(a, b, path):
        if type(a) is not type(b):
            differences.append(Difference(path, 'type changed'))
        elif isinstance(a, np.ndarray):
            if a.shape != b.shape:
                differences.append(Difference(path, 'array shape changed'))
            elif a.dtype != b.dtype:
                differences.append(Difference(path, 'array dtype changed'))
            elif (a.flags.c_contiguous, a.flags.f_contiguous) != (b.flags.c_contiguous, b.flags.f_contiguous):
                differences.append(Difference(path, 'array order changed'))
            elif a.tobytes(order='A') != b.tobytes(order='A'):
                differences.append(Difference(path, 'array values changed'))
        elif isinstance(a, dict):
            keys_a, keys_b = set(a), set(b)
            if not path:
                keys_a -= IGNORED_ROOT_FIELDS
                keys_b -= IGNORED_ROOT_FIELDS
            for key in sorted(keys_a | keys_b):
                child = f'{path}/{key}'
                if key not in keys_a or key not in keys_b:
                    differences.append(Difference(child, 'field added or missing'))
                else:
                    visit(a[key], b[key], child)
        elif isinstance(a, (list, tuple)):
            if len(a) != len(b):
                differences.append(Difference(path, 'sequence length changed'))
            else:
                for i, (aa, bb) in enumerate(zip(a, b)):
                    visit(aa, bb, f'{path}/{i}')
        elif isinstance(a, np.generic):
            if a.dtype != b.dtype or a.tobytes() != b.tobytes():
                differences.append(Difference(path, 'numpy scalar changed'))
        elif isinstance(a, float):
            if a.hex() != b.hex():
                differences.append(Difference(path, 'numeric value changed'))
        elif a != b:
            differences.append(Difference(path, 'value changed'))

    visit(expected, actual, '')
    return differences


def contained(base, relative):
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts or not rel.parts:
        raise ValueError('Unsafe manifest path')
    path = base / rel
    for part in [path, *path.parents]:
        if part == base:
            break
        if part.is_symlink():
            raise ValueError('Manifest symlink forbidden')
    if not path.resolve().is_relative_to(base.resolve()):
        raise ValueError('Manifest path escape')
    return path


def validate_manifest(manifest_path):
    manifest_path = Path(manifest_path)
    if manifest_path.is_symlink():
        raise ValueError('Manifest symlink forbidden')
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('schema_version') != 1 or not isinstance(manifest.get('files'), list) or not manifest['files']:
        raise ValueError('Invalid manifest schema')
    seen = set()
    for entry in manifest['files']:
        path = contained(manifest_path.parent, entry['path'])
        if entry['path'] in seen:
            raise ValueError('Duplicate manifest file')
        seen.add(entry['path'])
        if entry.get('absent') is True:
            if path.exists() or path.is_symlink():
                raise ValueError('Absent input appeared')
        elif not path.is_file() or sha256(path) != entry['sha256']:
            raise ValueError('Missing or hash-modified manifest input')
    return manifest


def runtime_lock(node=None):
    import importlib.metadata
    import platform
    import numba
    import scipy
    try:
        tzdata = importlib.metadata.version('tzdata')
    except importlib.metadata.PackageNotFoundError:
        tzdata = 'system'
    timezone_file = Path('/usr/share/zoneinfo/Europe/Rome')
    result = dict(python=sys.version, python_sha256=sha256(sys.executable), platform=platform.platform(),
                  numpy=np.__version__, numba=numba.__version__, scipy=scipy.__version__,
                  tzdata=tzdata, timezone_sha256=sha256(timezone_file) if timezone_file.exists() else None,
                  threads={k: os.environ.get(k) for k in THREAD_ENV})
    if node:
        result['node'] = subprocess.check_output([node, '--version'], text=True).strip()
        result['node_sha256'] = sha256(node)
    return result



def model_records(value):
    """Represent internal dataclasses by qualified type and every typed field.

    Reading an oracle never imports model classes or initializes live inputs.
    """
    if is_dataclass(value) and not isinstance(value, type):
        return {'__record_type__': type(value).__module__ + '.' + type(value).__qualname__,
                '__record_fields__': {f.name: model_records(getattr(value, f.name)) for f in fields(value)}}
    if isinstance(value, dict):
        return {key: model_records(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        items = [model_records(item) for item in value]
        return tuple(items) if isinstance(value, tuple) else items
    return value


def write_outputs(value, dest):
    """Canonical typed JSON tree plus NPZ; numeric lists keep scalar types."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=False, mode=0o700)
    arrays = {}

    def encode(obj):
        if isinstance(obj, np.ndarray):
            if obj.dtype.hasobject or not (obj.flags.c_contiguous or obj.flags.f_contiguous):
                raise ValueError('Unsupported array storage')
            name = f'a{len(arrays):06d}'
            arrays[name] = obj
            yield json.dumps({'kind': 'array', 'key': name}, sort_keys=True, separators=(',', ':'))
        elif isinstance(obj, np.generic):
            yield '{"dtype":' + json.dumps(obj.dtype.str) + ',"kind":"numpy_scalar","value":'
            yield from encode(obj.item())
            yield '}'
        elif isinstance(obj, dict):
            if any(not isinstance(k, str) for k in obj):
                raise ValueError('Non-string output key')
            yield '{"items":['
            for index, key in enumerate(sorted(obj)):
                if index:
                    yield ','
                yield '[' + json.dumps(key) + ','
                yield from encode(obj[key])
                yield ']'
            yield '],"kind":"dict"}'
        elif isinstance(obj, (list, tuple)):
            yield '{"items":['
            for index, value in enumerate(obj):
                if index:
                    yield ','
                yield from encode(value)
            yield '],"kind":' + json.dumps(type(obj).__name__) + '}'
        elif isinstance(obj, float):
            yield json.dumps({'kind': 'float', 'value': obj.hex()}, sort_keys=True, separators=(',', ':'))
        elif obj is None or type(obj) in (str, bool, int):
            yield json.dumps({'kind': type(obj).__name__, 'value': obj}, sort_keys=True, separators=(',', ':'))
        else:
            raise ValueError(f'Unsupported output type {type(obj).__name__}')

    # Stream the typed tree: a large capture must not retain an extra encoded
    # Python tree and a second full JSON string beside simulation buffers.
    with (dest / 'scalars.json').open('w') as stream:
        for fragment in encode(value):
            stream.write(fragment)
    np.savez_compressed(dest / 'arrays.npz', **arrays)
    for path in dest.iterdir():
        path.chmod(0o600)


def read_outputs(dest):
    dest = Path(dest)
    with np.load(dest / 'arrays.npz', allow_pickle=False) as archive:
        arrays = {k: archive[k] for k in archive.files}
    used = set()

    def decode(obj):
        kind = obj['kind']
        if kind == 'array':
            if obj['key'] in used:
                raise ValueError('Duplicate output array reference')
            used.add(obj['key'])
            return arrays[obj['key']]
        if kind == 'dict':
            return {k: decode(v) for k, v in obj['items']}
        if kind in ('list', 'tuple'):
            values = [decode(v) for v in obj['items']]
            return tuple(values) if kind == 'tuple' else values
        if kind == 'numpy_scalar':
            return np.asarray(decode(obj['value']), dtype=obj['dtype'])[()]
        if kind == 'float':
            return float.fromhex(obj['value'])
        return obj['value']

    result = decode(json.loads((dest / 'scalars.json').read_text()))
    if used != set(arrays):
        raise ValueError('Unreferenced output arrays')
    return result


class _JSONReader:
    """Incremental canonical JSON reader; storage is bounded by one scalar token."""
    def __init__(self, stream):
        self.stream, self.buffer, self.offset, self.eof = stream, '', 0, False
        self.decoder = json.JSONDecoder()
        self.used_arrays = set()

    def more(self):
        self.buffer = self.buffer[self.offset:]
        self.offset = 0
        block = self.stream.read(65536)
        self.buffer += block
        self.eof = not block

    def peek(self):
        while True:
            while self.offset < len(self.buffer) and self.buffer[self.offset].isspace():
                self.offset += 1
            if self.offset < len(self.buffer):
                return self.buffer[self.offset]
            if self.eof:
                return ''
            self.more()

    def expect(self, value):
        if self.peek() != value:
            raise ValueError('Invalid typed JSON structure')
        self.offset += 1

    def scalar(self):
        self.peek()
        while True:
            try:
                value, end = self.decoder.raw_decode(self.buffer, self.offset)
                if end == len(self.buffer) and not self.eof:
                    self.more()
                    continue
                if isinstance(value, (dict, list)):
                    raise ValueError('Scalar token required')
                self.offset = end
                return value
            except json.JSONDecodeError:
                if self.eof:
                    raise ValueError('Truncated typed JSON')
                self.more()


def _typed_events(reader, path=()):
    """Visit every typed field without constructing the saved result tree."""
    reader.expect('{')
    key = reader.scalar()
    reader.expect(':')
    if key == 'items':
        reader.expect('[')
        index = 0
        mapping = None
        while reader.peek() != ']':
            if index:
                reader.expect(',')
            pair = reader.peek() == '['
            if mapping is not None and pair != mapping:
                raise ValueError('Mixed typed container')
            mapping = pair
            if pair:
                reader.expect('[')
                name = reader.scalar()
                if type(name) is not str:
                    raise ValueError('String dictionary key required')
                reader.expect(',')
            else:
                name = index
            ignored = pair and not path and name in IGNORED_ROOT_FIELDS
            child = _typed_events(reader, (*path, name))
            if ignored:
                for _ in child:
                    pass
            else:
                yield ('member', path, name)
                yield from child
            if pair:
                reader.expect(']')
            index += 1
        reader.expect(']')
        reader.expect(',')
        if reader.scalar() != 'kind':
            raise ValueError('Typed container kind missing')
        reader.expect(':')
        kind = reader.scalar()
        if kind not in ('dict', 'list', 'tuple') or (mapping is not None and mapping != (kind == 'dict')):
            raise ValueError('Invalid typed container kind')
        yield ('container', path, kind)
    elif key == 'key':
        name = reader.scalar()
        if name in reader.used_arrays:
            raise ValueError('Duplicate output array reference')
        reader.used_arrays.add(name)
        reader.expect(',')
        if reader.scalar() != 'kind':
            raise ValueError('Array kind missing')
        reader.expect(':')
        if reader.scalar() != 'array':
            raise ValueError('Invalid array reference')
        yield ('array', path, name)
    elif key == 'dtype':
        dtype = reader.scalar()
        reader.expect(',')
        if reader.scalar() != 'kind':
            raise ValueError('Scalar kind missing')
        reader.expect(':')
        if reader.scalar() != 'numpy_scalar':
            raise ValueError('Invalid numpy scalar')
        yield ('dtype', path, dtype)
        reader.expect(',')
        if reader.scalar() != 'value':
            raise ValueError('Scalar value missing')
        reader.expect(':')
        yield from _typed_events(reader, (*path, '<numpy>'))
    elif key == 'kind':
        kind = reader.scalar()
        if kind not in ('NoneType', 'bool', 'int', 'float', 'str'):
            raise ValueError('Unknown typed scalar')
        reader.expect(',')
        if reader.scalar() != 'value':
            raise ValueError('Typed scalar value missing')
        reader.expect(':')
        value = reader.scalar()
        yield ('scalar', path, (kind, type(value).__name__, value))
    else:
        raise ValueError('Unknown typed node')
    reader.expect('}')


def compare_saved_outputs(expected, actual):
    """Exact typed comparison with one scalar token and two arrays resident.

    The saved format remains unchanged. Array dtype, shape, memory order and
    every byte are checked; no numerical tolerance is applied.
    """
    from itertools import zip_longest
    import zipfile
    differences = []
    sentinel = object()
    with (Path(expected) / 'scalars.json').open() as left, (Path(actual) / 'scalars.json').open() as right, \
            zipfile.ZipFile(Path(expected) / 'arrays.npz') as la, zipfile.ZipFile(Path(actual) / 'arrays.npz') as ra:
        readers = (_JSONReader(left), _JSONReader(right))
        used = (set(), set())
        for a, b in zip_longest(_typed_events(readers[0]), _typed_events(readers[1]), fillvalue=sentinel):
            if a is sentinel or b is sentinel or a[:2] != b[:2]:
                return [Difference('', 'typed structure changed')]
            path = '/' + '/'.join(str(x) for x in a[1]) if a[1] else ''
            if a[0] != 'array':
                if a != b:
                    differences.append(Difference(path, 'typed field changed'))
                continue
            arrays = []
            for index, (event, archive) in enumerate(((a, la), (b, ra))):
                name = event[2]
                if name in used[index]:
                    raise ValueError('Duplicate output array reference')
                used[index].add(name)
                with archive.open(name + '.npy') as stream:
                    arrays.append(np.lib.format.read_array(stream, allow_pickle=False))
            x, y = arrays
            if x.dtype != y.dtype:
                differences.append(Difference(path, 'array dtype changed'))
            elif x.shape != y.shape:
                differences.append(Difference(path, 'array shape changed'))
            elif (x.flags.c_contiguous, x.flags.f_contiguous) != (y.flags.c_contiguous, y.flags.f_contiguous):
                differences.append(Difference(path, 'array order changed'))
            else:
                xx, yy = x.ravel(order='A').view(np.uint8), y.ravel(order='A').view(np.uint8)
                if any(not np.array_equal(xx[i:i+65536], yy[i:i+65536]) for i in range(0, xx.size, 65536)):
                    differences.append(Difference(path, 'array values changed'))
                del xx, yy
            del arrays, x, y
        if any(reader.peek() for reader in readers):
            raise ValueError('Trailing typed JSON content')
        for names, archive in zip((r.used_arrays for r in readers), (la, ra)):
            if {name + '.npy' for name in names} != set(archive.namelist()):
                raise ValueError('Unreferenced output arrays')
    return differences


def live_guard_hashes(root=ROOT):
    result = {}
    data = root / 'feasibility/data'
    for name in ('current_scenario.json', 'user_defaults.json', 'weather-active.json',
                 'hourly.npz', 'audit.json', 'hourly_prices.npz', 'empirical_csud_prices.npz'):
        p = data / name
        result[str(p)] = sha256(p) if p.exists() else None
    return result


def copy_source(source, dest):
    """Copy code, never live data/reports/caches; dependencies are pinned separately."""
    for path in sorted((source / 'feasibility').rglob('*.py')):
        relative = path.relative_to(source)
        if any(part in ('vendor', 'data', '__pycache__', 'reports') for part in relative.parts):
            continue
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    (dest / 'tests').mkdir()
    shutil.copy2(source / 'tests/test_support.py', dest / 'tests/test_support.py')
    (dest / 'tools').mkdir()
    shutil.copy2(Path(__file__), dest / 'tools/regression.py')


def install_inputs(manifest, manifest_path, dest):
    seen = set()
    for entry in manifest['files']:
        if 'target' not in entry:
            continue
        if entry['target'] in seen:
            raise ValueError('Duplicate input target')
        seen.add(entry['target'])
        target = contained(dest, entry['target'])
        if entry.get('absent'):
            if target.exists():
                raise ValueError('Unexpected isolated input')
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(contained(manifest_path.parent, entry['path']), target)
        target.chmod(0o600)


def capture(manifest_path, output, source=ROOT):
    manifest_path, output = Path(manifest_path).resolve(), Path(output).absolute()
    manifest = validate_manifest(manifest_path)
    if output.exists():
        raise ValueError('Output exists; reference overwrite forbidden')
    if runtime_lock(manifest['runtime_paths']['node']) != manifest['runtime']:
        raise ValueError('Pinned runtime mismatch')
    before = live_guard_hashes(source)
    with tempfile.TemporaryDirectory(prefix='pv-regression-') as temp:
        checkout = Path(temp) / 'source'
        checkout.mkdir()
        copy_source(source, checkout)
        install_inputs(manifest, manifest_path, checkout)
        env = os.environ.copy()
        env.update(THREAD_ENV)
        env['PYTHONPATH'] = os.pathsep.join((str(checkout), str(checkout / 'tests'), manifest['runtime_paths']['vendor']))
        env['NUMBA_CACHE_DIR'] = str(Path(temp) / 'numba')
        argv = [manifest['runtime_paths']['python'], str(checkout / 'tools/regression.py'),
                '_worker', '--manifest', str(manifest_path), '--output', str(output)]
        try:
            subprocess.run(argv, cwd=checkout, env=env, check=True)
        finally:
            if before != live_guard_hashes(source):
                raise ValueError('Live input guard changed')
    validate_manifest(manifest_path)


def get_test_workers(env_var: str = 'PV_TEST_WORKERS', default: int = 1) -> int:
    raw = os.environ.get(env_var)
    if raw is None:
        return default
    try:
        val = int(raw)
    except (ValueError, TypeError) as err:
        raise ValueError(f'{env_var} must be a positive integer, got {raw!r}') from err
    if val <= 0:
        raise ValueError(f'{env_var} must be a positive integer, got {val}')
    return val


def run_bounded_searches(engine, bounded, search, workers: int = 1) -> dict:
    if not isinstance(workers, int) or isinstance(workers, bool) or workers <= 0:
        raise ValueError('workers must be a positive integer')
    if workers == 1:
        return {
            'optimizer': engine.optimize_configuration(bounded, search['optimizer']),
            'solar_sizing': engine.solar_sizing(bounded, search['solar']),
            'battery_sizing': engine.battery_sizing(bounded, search['battery']),
        }
    max_workers = min(3, workers)
    tasks = [
        ('optimizer', engine.optimize_configuration, (bounded, search['optimizer'])),
        ('solar_sizing', engine.solar_sizing, (bounded, search['solar'])),
        ('battery_sizing', engine.battery_sizing, (bounded, search['battery'])),
    ]
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [(key, executor.submit(fn, *args)) for key, fn, args in tasks]
        return {key: fut.result() for key, fut in futures}


def collect_matrix(manifest):
    # Bind the frozen checkout's saved defaults BEFORE test_support redirects
    # mutable storage. The second (warm) pass reuses this identical binding.
    from feasibility.runtime_context import runtime_if_initialized
    if runtime_if_initialized() is None:
        from feasibility.legacy_profile import bootstrap_legacy
        with tempfile.TemporaryDirectory(prefix='pv-reference-profile-') as profile_dir:
            bootstrap_legacy(root=Path(profile_dir) / 'profiles')
    # This import installs the existing guard; it initially redirects defaults to
    # process-temp files. Rebind only into this disposable checkout before engine.
    import test_support
    from feasibility import assumptions, shared_state, engine, weather_dataset, report
    assumptions.USER_DEFAULTS_PATH = ROOT / 'feasibility/data/user_defaults.json'
    shared_state.STATE_PATH = ROOT / 'feasibility/data/current_scenario.json'
    # Keep guard against the ORIGINAL checkout as well as test_support's temp tree.
    original = Path(manifest['runtime_paths']['source']) / 'feasibility/data'
    test_support.LIVE_GUARDED_FILES.update((original / n).resolve() for n in (
        'current_scenario.json', 'user_defaults.json', 'current_scenario.json.lock', 'user_defaults.json.lock'))
    # Guard intentionally forbids writing these copied paths too. State read is
    # pure via _snapshot; no lock files or implicit saved-state updates are needed.
    effective = shared_state._snapshot(shared_state.STATE_PATH)
    effective.pop('revision')  # documented mtime/storage revision, inputs hashed instead
    if effective != manifest['effective']:
        raise ValueError('Effective saved inputs differ from frozen manifest')
    results = {'effective': effective, 'weather_identity': weather_dataset.active_weather_dataset_identity(),
               'calibration': engine.calibration(), 'thermal_constants': {'GAS_MONTHLY': engine.GAS_MONTHLY,
               'COOLING_BALANCE_TEMP': engine.COOLING_BALANCE_TEMP}, 'scenarios': {}}
    base = effective['settings']
    results['effective_quote'] = engine.quote_evaluate(base, effective['quote'])
    for case in manifest['scenarios']:
        name = case['name']
        print(f'Capture scenario {name}', flush=True)
        settings = engine.validate({**base, **case['settings']})
        resolved = {**settings, 'routing_priority': engine.resolve_routing_priority(settings)}
        loads = engine.load_profiles(resolved)
        mapped = engine._lifetime_mapped_inputs(resolved)
        pv = engine.pv_profile(resolved)
        annual, enduses = engine._energy_cache(tuple((k, engine.freeze(resolved[k])) for k in engine.ENERGY_KEYS), engine.price_fingerprint(resolved))
        result = {'evaluation': engine.evaluate(settings, detail=True), 'load_profiles': loads,
                  'pv_profile': pv, 'mapped_inputs': mapped, 'annual_dispatch': annual, 'enduses': enduses,
                  'price_profile': engine.price_profile(settings), 'chart_data': engine.chart_data(settings),
                  'forecast_runs': engine._forecast_runs(tuple((k, engine.freeze(settings[k])) for k in engine.ENERGY_KEYS), engine.price_fingerprint(settings))}
        forecast = result['forecast_runs']
        forecast_months = forecast['mapped_data']['target_months']
        columns = [0, 1, 2, 3, 5, 6, 7]
        result['forecast_monthly_by_weather'] = {
            'columns': ['pv_kwh', 'load_kwh', 'grid_import_kwh', 'export_kwh', 'direct_kwh', 'charge_kwh', 'discharge_kwh'],
            'values': np.asarray([[trace[forecast_months == month][:, columns].sum(axis=0)
                                   for month in range(1, 13)] for trace in forecast['traces']])}
        if case.get('forecast_date'):
            result['forecast_day'] = engine.dayhour_forecast(settings, case['forecast_date'])
        if case.get('heat_pump'):
            from feasibility.heatpump_integration import get_source_heat_pump_hourly
            result['heat_pump'] = get_source_heat_pump_hourly(settings)
        results['scenarios'][name] = result
    search = manifest['search']
    print('Capture bounded searches and report', flush=True)
    bounded = engine.validate({**base, **search['settings']})
    searches = run_bounded_searches(engine, bounded, search, workers=get_test_workers())
    results['optimizer'] = searches['optimizer']
    results['solar_sizing'] = searches['solar_sizing']
    results['battery_sizing'] = searches['battery_sizing']
    # Actual report renderer, bounded study assembled exclusively from captured
    # model outputs (no fabricated numerical values or expensive unbounded study).
    selected = results['scenarios']['effective']['evaluation']
    study = dict(calibration=results['calibration'], baseline_settings=base, selected=selected,
                 base={'candidates': [dict(selected, label='effective', load_scenario=base['load_scenario'])]}, counts={'candidates': 1, 'financial_candidate_cases': 0, 'joint_stress_cases': 0},
                 candidate_joint_envelopes=[], battery_counterfactuals=[], rankings=[], scop_scenarios=[],
                 sensitivity={'cases': []}, notes=['Bounded regression report; searches captured separately.'])
    report_path = ROOT / 'feasibility/reports'
    report_path.mkdir(exist_ok=True)
    report.write_reports(study, dest=report_path)
    results['reports'] = {}
    for path in sorted(report_path.iterdir()):
        if path.suffix == '.json':
            value = json.loads(path.read_text())
            if path.name == 'verification.json':
                value.pop('report_generated_utc')
            results['reports'][path.name] = value
        else:
            results['reports'][path.name] = path.read_text()
    return model_records(results)


def worker(manifest_path, output):
    manifest = validate_manifest(manifest_path)
    cold = collect_matrix(manifest)
    write_outputs(cold, output)
    del cold
    import gc
    gc.collect()
    # Full second pass in the SAME process exercises warm caches and changed
    # scenario ordering; exact comparison also covers all bounded searches.
    warm_manifest = dict(manifest, scenarios=list(reversed(manifest['scenarios'])))
    warm = collect_matrix(warm_manifest)
    # Complete both passes in the SAME bound process before releasing results.
    # Compare their saved arrays incrementally instead of loading a second tree.
    with tempfile.TemporaryDirectory(prefix='pv-reference-warm-') as temp:
        warm_output = Path(temp) / 'warm'
        write_outputs(warm, warm_output)
        del warm
        gc.collect()
        differences = compare_saved_outputs(output, warm_output)
    if differences:
        raise ValueError(f'Warm/cold mismatch: {differences[:10]}')
    print('Warm/cold: exact', flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('capture', 'compare', '_worker'))
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == '_worker':
            from feasibility.worker_pool import run_local
            run_local('private-reference', {}, {'manifest': str(args.manifest), 'output': str(args.output)})
        else:
            # Parent also uses the controlled runtime settings before imports of
            # numerical dependencies (the CLI reexec below sets them early).
            manifest = validate_manifest(args.manifest)
            if args.command == 'compare':
                bound = {entry['path'] for entry in manifest['files'] if not entry.get('absent')}
                if any(str(Path(manifest['oracle']) / name) not in bound for name in ('scalars.json', 'arrays.npz')):
                    raise ValueError('Oracle files are not content-bound in manifest')
                expected = contained(args.manifest.resolve().parent, manifest['oracle'])
                if not expected.is_dir():
                    raise ValueError('Frozen oracle missing')
                if args.output.resolve().is_relative_to(args.manifest.resolve().parent):
                    raise ValueError('Comparison output must be outside the frozen reference')
                if args.output.exists():
                    if not args.output.is_dir() or args.output.is_symlink():
                        raise ValueError('Invalid comparison output directory')
                    args.output = args.output / ('run-' + uuid.uuid4().hex)
            capture(args.manifest, args.output)
            if args.command == 'compare':
                diffs = compare_saved_outputs(expected, args.output)
                (args.output / 'comparison.json').write_text(json.dumps([d.__dict__ for d in diffs], indent=2))
                if diffs:
                    print(f'Comparison differs: {len(diffs)} paths; see comparison.json', file=sys.stderr)
                    return 1
                print('Comparison: exact')
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f'Regression blocked: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    os.umask(0o077)
    if any(os.environ.get(k) != v for k, v in THREAD_ENV.items()):
        os.environ.update(THREAD_ENV)
        os.execv(sys.executable, [sys.executable, *sys.argv])
    raise SystemExit(main())
