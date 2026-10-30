"""Process lifetime, stored-default separation and exact legacy bootstrap."""
import pytest

from tests.test_runtime_context_arrays import _run_subp


_CONTEXT = '''
from feasibility.runtime_context import RuntimeContext, initialize_runtime, current_runtime
def context(**changes):
    args = dict(owner_id='synthetic', input_revision='revision-a', dataset_digest='dataset-a',
                price_revision='flat-a', defaults_snapshot={'scop': 4}, thermal_reference={},
                fiscal_revision='fiscal-a', calendar_version='legacy_pvgis_v1', dataset={},
                weather_identity=('synthetic', 'npz', 'audit'), records_snapshot=[], metadata={})
    args.update(changes)
    return RuntimeContext(**args)
'''


def test_binding_lifetime_and_frozen_context():
    _run_subp(_CONTEXT + '''
from dataclasses import FrozenInstanceError
ctx = context()
assert initialize_runtime(ctx) is ctx
assert initialize_runtime(ctx) is ctx
for other in (context(), context(input_revision='revision-b'), context(owner_id='another-owner')):
    try:
        initialize_runtime(other)
    except RuntimeError as exc:
        assert 'new process' in str(exc)
    else:
        raise AssertionError('Rebinding was allowed')
assert current_runtime() is ctx
try:
    ctx.input_revision = 'changed'
except FrozenInstanceError:
    pass
else:
    raise AssertionError('Context is mutable')
''')


def test_saved_defaults_do_not_change_running_simulation():
    _run_subp(_CONTEXT + '''
from pathlib import Path
from tempfile import TemporaryDirectory
from feasibility import engine, assumptions
with TemporaryDirectory() as tmp:
    path = Path(tmp) / 'defaults.json'
    assumptions.USER_DEFAULTS_PATH = path
    snapshot = assumptions.persisted_defaults(path)
    records = assumptions.persisted_records(path)
    initialize_runtime(context(defaults_snapshot=snapshot, records_snapshot=records))
    assumptions.save_user_default('scop', 2, engine.validate, path=path)
    assert assumptions.defaults(path=path)['scop'] == 2
    assert assumptions.persisted_defaults()['scop'] == 2
    assert engine.defaults()['scop'] == snapshot['scop']
    assert engine.validate({})['scop'] == snapshot['scop']
    assert engine.validate({'scop': 2})['scop'] == 2
    assert next(r for r in assumptions.persisted_records() if r['name'] == 'scop')['value'] == 2
    assert next(r for r in engine.records() if r['name'] == 'scop')['value'] == snapshot['scop']
    exposed = engine.defaults()
    exposed['ac_heating_windows'][0]['start_minute'] = 1
    exposed_records = engine.records()
    exposed_records[0]['value'] = 'changed'
    assert engine.defaults() == snapshot
    assert engine.records() == records
    assumptions.reset_user_default('scop', path=path)
    assert engine.defaults() == snapshot
''')


def test_legacy_bootstrap_exact_and_revision_changes_require_new_process():
    _run_subp('''
from pathlib import Path
from tempfile import TemporaryDirectory
import numpy as np
from feasibility import assumptions, engine, weather_dataset
from feasibility.legacy_profile import import_legacy_profile, load_legacy_profile
from feasibility.runtime_context import initialize_runtime, mutable_copy
original, identity = weather_dataset.load_active_weather_dataset()
with TemporaryDirectory() as tmp:
    root = Path(tmp) / 'profiles'
    defaults_path = Path(tmp) / 'defaults.json'
    revision = import_legacy_profile(root, defaults_path)
    assert import_legacy_profile(root, defaults_path) == revision
    ctx = load_legacy_profile(root, revision)
    initialize_runtime(ctx)
    assert ctx.calendar_version == 'legacy_pvgis_v1'
    assert len(ctx.metadata['source_planes']) == 5
    assert ctx.weather_identity == identity
    assert list(engine.data()['audit']['pvgis_yields']) == list(original['audit']['pvgis_yields'])
    assert engine.defaults() == assumptions.persisted_defaults(defaults_path)
    np.testing.assert_array_equal(ctx.thermal_reference['GAS_MONTHLY'], engine.GAS_MONTHLY)
    for name, value in original.items():
        if isinstance(value, np.ndarray):
            np.testing.assert_array_equal(engine.data()[name], value)
            assert engine.data()[name].dtype == value.dtype
        else:
            assert engine.data()[name] == value
    assumptions.save_user_default('scop', 2, engine.validate, path=defaults_path)
    next_revision = import_legacy_profile(root, defaults_path)
    assert next_revision != revision
    next_ctx = load_legacy_profile(root, next_revision)
    assert next_ctx.defaults_snapshot['scop'] == 2
    assert ctx.defaults_snapshot['scop'] != 2
    try:
        initialize_runtime(next_ctx)
    except RuntimeError:
        pass
    else:
        raise AssertionError('Default changes rebound a running process')
''')


@pytest.mark.parametrize('filename', ['audit.json', 'manifest.json', 'weather.npz'])
def test_legacy_revision_tampering_fails_closed(filename):
    _run_subp('''
from pathlib import Path
from tempfile import TemporaryDirectory
from feasibility.legacy_profile import import_legacy_profile, load_legacy_profile
with TemporaryDirectory() as tmp:
    root = Path(tmp) / 'profiles'
    revision = import_legacy_profile(root, Path(tmp) / 'defaults.json')
    path = root / 'legacy-profiles' / revision / FILENAME
    path.write_bytes(path.read_bytes() + b'corrupt')
    try:
        load_legacy_profile(root, revision)
    except ValueError as exc:
        assert 'hash mismatch' in str(exc)
    else:
        raise AssertionError('Corrupted snapshot was accepted')
'''.replace('FILENAME', repr(filename)))
