"""Regression checks for Windows storage and interprocess sidecar locks."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from feasibility import assumptions, shared_state
from feasibility.profile_store import ProfileStore
from feasibility.tenant_store import TenantStore


ROOT = Path(__file__).resolve().parents[1]


if os.name == 'nt':
    def _junction(link, target):
        subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(target)],
                       check=True, capture_output=True, timeout=10)


    def test_profile_store_rejects_junction_ancestor(tmp_path):
        outside = tmp_path / 'outside'
        outside.mkdir()
        junction = tmp_path / 'junction'
        _junction(junction, outside)
        try:
            with pytest.raises(ValueError):
                ProfileStore(junction / 'store')
            assert not list(outside.iterdir())
        finally:
            junction.rmdir()


    def test_profile_store_rejects_broad_acl(tmp_path):
        store = ProfileStore(tmp_path / 'store')
        icacls = shutil.which('icacls')
        if not icacls:
            pytest.skip('icacls unavailable')
        subprocess.run([icacls, str(store.root), '/grant', '*S-1-1-0:(OI)(CI)F'],
                       check=True, capture_output=True, timeout=10)
        with pytest.raises(ValueError, match='owner-only'):
            ProfileStore(store.root)


    def test_tenant_cleanup_rejects_junction(tmp_path):
        store = TenantStore(tmp_path / 'store')
        owner = store.create_tenant()
        outside = tmp_path / 'outside'
        outside.mkdir()
        sentinel = outside / 'sentinel'
        sentinel.write_bytes(b'outside')
        junction = store.root / 'tenants' / owner / 'junction'
        _junction(junction, outside)
        try:
            with pytest.raises(ValueError, match='Unsafe store path'):
                store.delete_tenant(owner)
            assert sentinel.read_bytes() == b'outside'
        finally:
            junction.rmdir()
        assert store.janitor()['deleted_tenants'] == 1


    def test_windows_tenant_reopens_with_immutable_upload(tmp_path):
        store = TenantStore(tmp_path / 'store')
        owner = store.create_tenant()
        profile = store.create_profile(owner)
        payload = b'Windows binary upload \x00\xff'
        item, _ = store.put_upload(owner, payload, {})
        reopened = TenantStore(store.root)
        assert reopened.snapshot(owner, profile['id'])['revision'] == profile['revision']
        assert reopened.read_upload(owner, item['id']) == payload
        with reopened.files._directory(()) as directory:
            reopened.files._write_once(directory, 'immutable', payload)
            with pytest.raises(FileExistsError):
                reopened.files._write_once(directory, 'immutable', b'other')
            assert reopened.files._read(directory, 'immutable', 100) == payload


    def test_windows_database_rejects_hard_linked_journal(tmp_path):
        store = TenantStore(tmp_path / 'store')
        sentinel = tmp_path / 'outside'
        sentinel.write_bytes(b'outside')
        journal = store.root / 'tenants.sqlite3-journal'
        os.link(sentinel, journal)
        with pytest.raises(ValueError):
            TenantStore(store.root)
        assert sentinel.read_bytes() == b'outside'


@pytest.mark.parametrize(('module', 'name'), [(shared_state, '_file_lock'),
                                             (assumptions, '_defaults_file_lock')])
def test_sidecar_lock_serializes_processes_and_releases_after_exception(tmp_path, module, name):
    path = tmp_path / 'state.json'
    ready = tmp_path / 'ready'
    sidecar = path.with_suffix('.json.lock')
    sidecar.write_bytes(b'preserve sidecar')
    child_code = f"""
from {module.__name__} import {name}
from pathlib import Path
import sys

path, ready = map(Path, sys.argv[1:])
ready.touch()
with {name}(path):
    pass
"""
    process = None
    try:
        with pytest.raises(RuntimeError, match='release lock'):
            with getattr(module, name)(path):
                process = subprocess.Popen(
                    [sys.executable, '-c', child_code, str(path), str(ready)], cwd=ROOT
                )
                deadline = time.monotonic() + 30
                while not ready.exists():
                    assert process.poll() is None, 'Child exited before attempting the lock'
                    assert time.monotonic() < deadline, 'Child failed to start'
                    time.sleep(0.02)
                with pytest.raises(subprocess.TimeoutExpired):
                    process.wait(timeout=0.2)
                raise RuntimeError('release lock')
        assert process.wait(timeout=15) == 0
        assert sidecar.read_bytes() == b'preserve sidecar'
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=10)
