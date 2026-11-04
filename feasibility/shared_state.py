"""Atomic shared scenario state for all clients of the local calculator."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import uuid

from feasibility import assumptions, engine
from feasibility.file_lock import file_lock
from feasibility.schedule import LEGACY_HEATING_KEYS, migrate_and_resolve_schedule

if os.name == 'nt':
    from feasibility.windows_storage import move

HERE = Path(__file__).resolve().parent
STATE_PATH = HERE / 'data' / 'current_scenario.json'
_LOCK = threading.RLock()


@contextmanager
def _file_lock(path):
    with file_lock(path, _LOCK):
        yield



def _state_path(path=None):
    return Path(STATE_PATH if path is None else path)


def _defaults_stamp():
    path = Path(assumptions.USER_DEFAULTS_PATH)
    try:
        raw = path.read_bytes()
        stat = path.stat()
        return f'{stat.st_mtime_ns:x}-{hashlib.sha256(raw).hexdigest()}'
    except FileNotFoundError:
        return 'missing'


def _read_saved(path):
    if not path.exists():
        return None
    state = json.loads(path.read_text())
    if not isinstance(state, dict):
        raise ValueError('Saved scenario must be an object')
    if not isinstance(state.get('settings'), dict) or not isinstance(state.get('quote'), dict):
        raise ValueError('Saved scenario settings and quote must be objects')
    if not isinstance(state.get('state_revision'), str):
        raise ValueError('Saved scenario revision is missing')
    return state


def _revision(state, defaults_stamp):
    scenario_revision = state['state_revision'] if state else 'initial'
    return f'{scenario_revision}:{defaults_stamp}'


def _snapshot(path):
    state = _read_saved(path)
    if state is None:
        quote = {'line_items': []}
        state_revision = 'initial'
        state_for_revision = {'state_revision': state_revision}
    else:
        # Merge schema additions into older saved scenarios without writing to
        # the scenario file. Existing user choices retain precedence.
        old_settings = dict(state['settings'])
        if 'planning_heat_pump' not in old_settings:
            old_settings['planning_heat_pump'] = False
        if 'ac_heating_windows' not in old_settings and any(k in old_settings for k in LEGACY_HEATING_KEYS):
            old_settings['ac_heating_windows'] = migrate_and_resolve_schedule(old_settings, base_windows=assumptions.persisted_defaults()['ac_heating_windows'])
            for k in LEGACY_HEATING_KEYS:
                old_settings.pop(k, None)
        settings = engine.validate({**assumptions.persisted_defaults(), **old_settings})
        quote = state['quote']
        state_for_revision = state
    # Defaults and records are part of the shared state contract. Include them
    # in the response even when the current scenario is still at its default.
    for _ in range(3):
        defaults_stamp = _defaults_stamp()
        defaults = assumptions.persisted_defaults()
        records = assumptions.persisted_records()
        if defaults_stamp == _defaults_stamp():
            break
    if state is None:
        settings = defaults
    return {
        'revision': _revision(state_for_revision, defaults_stamp),
        'settings': settings,
        'quote': quote,
        'defaults': defaults,
        'records': records,
    }


def get_state(revision=None, path=None):
    """Return the full shared snapshot, or a compact unchanged response."""
    from feasibility.tenant_store import TenantState
    if isinstance(path, TenantState):
        return path.get_state(revision)
    target = _state_path(path)
    with _file_lock(target):
        snapshot = _snapshot(target)
    if revision is not None and revision == snapshot['revision']:
        return {'revision': snapshot['revision'], 'unchanged': True}
    return snapshot


def _atomic_write(path, state):
    if isinstance(state, dict) and 'settings' in state and isinstance(state['settings'], dict):
        state = dict(state)
        state['settings'] = {k: v for k, v in state['settings'].items() if k not in LEGACY_HEATING_KEYS}
    payload = json.dumps(state, indent=2, allow_nan=False) + '\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        if os.name == 'nt':
            move(temporary, path, replace=True)
        else:
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def update_state(payload, path=None):
    """Merge a partial edit into the latest scenario, or replace it explicitly."""
    from feasibility.tenant_store import TenantState
    if isinstance(path, TenantState):
        return path.update_state(payload)
    if not isinstance(payload, dict):
        raise ValueError('Request body must be an object')
    unknown = set(payload) - {'settings', 'quote', 'replace'}
    if unknown:
        raise ValueError('Unknown state fields: ' + ', '.join(sorted(unknown)))
    replace = payload.get('replace', False)
    if not isinstance(replace, bool):
        raise ValueError('replace must be boolean')
    if 'settings' in payload and not isinstance(payload['settings'], dict):
        raise ValueError('settings must be an object')
    if 'quote' in payload and not isinstance(payload['quote'], dict):
        raise ValueError('quote must be an object')
    if replace and ('settings' not in payload or 'quote' not in payload):
        raise ValueError('replace requires full settings and quote objects')

    target = _state_path(path)
    with _file_lock(target):
        saved = _read_saved(target)
        if replace:
            settings = dict(payload['settings'])
            quote = dict(payload['quote'])
            if 'ac_heating_windows' not in settings and any(k in settings for k in LEGACY_HEATING_KEYS):
                settings['ac_heating_windows'] = migrate_and_resolve_schedule(settings)
                for k in LEGACY_HEATING_KEYS:
                    settings.pop(k, None)
            if 'ac_heating_additional_demand_pct' not in settings:
                settings['ac_heating_additional_demand_pct'] = 0
            missing = (set(assumptions.persisted_defaults()) - LEGACY_HEATING_KEYS) - (set(settings) - LEGACY_HEATING_KEYS)
            if missing:
                raise ValueError('replace settings must include all inputs')
            settings = engine.validate(settings)
        else:
            old_settings = dict(saved['settings']) if (saved and 'settings' in saved) else {}
            if saved and 'planning_heat_pump' not in old_settings:
                old_settings['planning_heat_pump'] = False
            if 'ac_heating_windows' not in old_settings and any(k in old_settings for k in LEGACY_HEATING_KEYS):
                old_settings['ac_heating_windows'] = migrate_and_resolve_schedule(old_settings, base_windows=assumptions.persisted_defaults()['ac_heating_windows'])
                for k in LEGACY_HEATING_KEYS:
                    old_settings.pop(k, None)
            saved_settings = {**assumptions.persisted_defaults(), **old_settings}
            quote = dict(saved['quote'] if saved else {'line_items': []})
            supplied_settings = payload.get('settings', {})
            if supplied_settings:
                settings = engine.validate(supplied_settings, base=saved_settings, supplied=set(supplied_settings))
            else:
                settings = saved_settings
            quote_update = payload.get('quote', {})
            # Quote line items form one array-valued field; replace that array
            # as a whole while merging other quote evidence by key.
            quote.update(quote_update)

        # These were sent as a partial patch before the objects were merged;
        # retain the dependency direction selected by that patch.
        supplied_settings = payload['settings'] if replace else payload.get('settings', {})
        assumptions._normalize_battery(settings, set(supplied_settings))

        # Validate the merged raw inputs and quote together. Persist the raw
        # values so equipment details remain separate from normalized settings.
        settings = engine.validate(settings)
        engine.quote_evaluate(settings, quote)
        state = {
            'state_revision': str(uuid.uuid4()),
            'settings': settings,
            'quote': quote,
        }
        _atomic_write(target, state)
        return _snapshot(target)
