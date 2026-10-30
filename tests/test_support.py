"""Shared test isolation for mutable, user-saved calculator defaults and scenarios."""
import atexit
import builtins
from contextlib import contextmanager
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

_this = sys.modules[__name__]
sys.modules['test_support'] = _this
sys.modules['tests.test_support'] = _this
try:
    import tests
    setattr(tests, 'test_support', _this)
except ImportError:
    pass

# All ordinary suites start from invented inputs, including private-mode suites.
# The sealed private comparator binds its own context before importing this file.
from feasibility.runtime_context import runtime_if_initialized
_SYNTHETIC_TEMP_DIR = None
if runtime_if_initialized() is None and not os.environ.get('PV_LEGACY_DATA_ROOT'):
    import importlib.util
    generator_path = Path(__file__).parent / 'fixtures/public_demo/generate.py'
    spec = importlib.util.spec_from_file_location('pv_public_fixture', generator_path)
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    _SYNTHETIC_TEMP_DIR = tempfile.TemporaryDirectory(prefix='pv-synthetic-inputs-')
    atexit.register(_SYNTHETIC_TEMP_DIR.cleanup)
    generator.prepare_test_inputs(Path(_SYNTHETIC_TEMP_DIR.name) / 'inputs')

from feasibility import assumptions, shared_state

CHECKOUT_DATA_DIR = (Path(__file__).resolve().parent.parent / 'feasibility' / 'data').resolve()
LIVE_DATA_DIR = Path(os.environ.get('PV_LEGACY_DATA_ROOT',CHECKOUT_DATA_DIR)).resolve()
LIVE_STATE_PATH = (LIVE_DATA_DIR / 'current_scenario.json').resolve()
LIVE_USER_DEFAULTS_PATH = (LIVE_DATA_DIR / 'user_defaults.json').resolve()
LIVE_GUARDED_FILES = {
    LIVE_STATE_PATH,
    LIVE_USER_DEFAULTS_PATH,
    (LIVE_DATA_DIR / 'current_scenario.json.lock').resolve(),
    (LIVE_DATA_DIR / 'user_defaults.json.lock').resolve(),
    *((CHECKOUT_DATA_DIR / name).resolve() for name in ('current_scenario.json','user_defaults.json','current_scenario.json.lock','user_defaults.json.lock')),
}

# -----------------------------------------------------------------------------
# Outer Process-Level Isolation
# -----------------------------------------------------------------------------
# Establish process-wide temporary defaults and scenario paths at import time.
# Even if a test omits a per-test patch, disk writes and state operations default
# to this outer temporary directory instead of the live files.
_PROCESS_TEMP_DIR = tempfile.TemporaryDirectory(prefix='process_test_isolation_')
atexit.register(_PROCESS_TEMP_DIR.cleanup)

PROCESS_STATE_PATH = Path(_PROCESS_TEMP_DIR.name) / 'current_scenario.json'
PROCESS_USER_DEFAULTS_PATH = Path(_PROCESS_TEMP_DIR.name) / 'user_defaults.json'

shared_state.STATE_PATH = PROCESS_STATE_PATH
assumptions.USER_DEFAULTS_PATH = PROCESS_USER_DEFAULTS_PATH


def initialize_test_runtime():
    """Explicit legacy bootstrap after redirecting all mutable test storage."""
    from feasibility.runtime_context import runtime_if_initialized
    if runtime_if_initialized() is None:
        from feasibility.legacy_profile import bootstrap_legacy
        bootstrap_legacy(root=Path(_PROCESS_TEMP_DIR.name) / 'profiles')


initialize_test_runtime()


# -----------------------------------------------------------------------------
# Live Path Write Guard
# -----------------------------------------------------------------------------
class LivePathWriteError(PermissionError):
    """Raised when an operation attempts to write to guarded live data files during tests."""
    pass


_ORIG_OPEN = builtins.open
_ORIG_IO_OPEN = io.open
_ORIG_OS_OPEN = os.open
_ORIG_OS_REPLACE = os.replace
_ORIG_ATOMIC_WRITE = shared_state._atomic_write
_ORIG_SAVE_DEFAULT = assumptions.save_user_default
_ORIG_RESET_DEFAULT = assumptions.reset_user_default
_GUARD_INSTALLED = False


def _check_target(path):
    if not isinstance(path, (str, bytes, Path, os.PathLike)):
        return
    try:
        p = Path(os.fsdecode(path)).resolve()
        if p in LIVE_GUARDED_FILES:
            raise LivePathWriteError(f"Test isolation violation: write attempt to guarded live file: {p}")
        if p.parent in {LIVE_DATA_DIR,CHECKOUT_DATA_DIR} and (
            p.name.startswith('current_scenario') or p.name.startswith('user_defaults')
        ):
            raise LivePathWriteError(f"Test isolation violation: write attempt to live data directory file: {p}")
    except (ValueError, TypeError):
        pass


def _guarded_open(file, mode='r', *args, **kwargs):
    if any(m in mode for m in ('w', 'a', 'x', '+')):
        _check_target(file)
    return _ORIG_OPEN(file, mode, *args, **kwargs)


def _guarded_os_open(path, flags, *args, **kwargs):
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | getattr(os, 'O_APPEND', 0)
    if flags & write_flags:
        _check_target(path)
    return _ORIG_OS_OPEN(path, flags, *args, **kwargs)


def _guarded_os_replace(src, dst, *args, **kwargs):
    _check_target(dst)
    return _ORIG_OS_REPLACE(src, dst, *args, **kwargs)


def _guarded_atomic_write(path, state):
    _check_target(path)
    return _ORIG_ATOMIC_WRITE(path, state)


def _guarded_save_default(name, value, validator, path=None, **kwargs):
    target = assumptions.USER_DEFAULTS_PATH if path is None else path
    _check_target(target)
    return _ORIG_SAVE_DEFAULT(name, value, validator, path=path, **kwargs)


def _guarded_reset_default(name, path=None):
    target = assumptions.USER_DEFAULTS_PATH if path is None else path
    _check_target(target)
    return _ORIG_RESET_DEFAULT(name, path=path)


def install_live_path_guard():
    global _GUARD_INSTALLED
    if not _GUARD_INSTALLED:
        builtins.open = _guarded_open
        io.open = _guarded_open
        os.open = _guarded_os_open
        os.replace = _guarded_os_replace
        shared_state._atomic_write = _guarded_atomic_write
        assumptions.save_user_default = _guarded_save_default
        assumptions.reset_user_default = _guarded_reset_default
        _GUARD_INSTALLED = True


def uninstall_live_path_guard():
    global _GUARD_INSTALLED
    if _GUARD_INSTALLED:
        builtins.open = _ORIG_OPEN
        io.open = _ORIG_IO_OPEN
        os.open = _ORIG_OS_OPEN
        os.replace = _ORIG_OS_REPLACE
        shared_state._atomic_write = _ORIG_ATOMIC_WRITE
        assumptions.save_user_default = _ORIG_SAVE_DEFAULT
        assumptions.reset_user_default = _ORIG_RESET_DEFAULT
        _GUARD_INSTALLED = False


@contextmanager
def guard_live_paths():
    install_live_path_guard()
    try:
        yield
    finally:
        pass


# Automatically install the guard on module import
install_live_path_guard()


class IsolatedDefaultsTestCase(unittest.TestCase):
    """Test case isolating defaults and shared state with guaranteed addCleanup."""

    def setUp(self):
        super().setUp()
        self._defaults_temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._defaults_temp.cleanup)
        tpath = Path(self._defaults_temp.name)
        self._defaults_path = tpath / 'nonexistent-user-defaults.json'
        self._state_path = tpath / 'current_scenario.json'

        self._defaults_patch = patch.object(assumptions, 'USER_DEFAULTS_PATH', self._defaults_path)
        self._state_patch = patch.object(shared_state, 'STATE_PATH', self._state_path)

        self._defaults_patch.start()
        self.addCleanup(self._defaults_patch.stop)
        self._state_patch.start()
        self.addCleanup(self._state_patch.stop)

    def tearDown(self):
        super().tearDown()
