import concurrent.futures
import os
import pytest
from feasibility.tenant_store import Conflict, NotFound, StoreError, TenantStore, Unauthorized


def test_tenant_profile_lifecycle_and_partial_publish(tmp_path):
    store = TenantStore(root=tmp_path / "store")
    tenant_id = store.create_tenant()
    prof = store.create_profile(tenant_id, manifest_ref="synthetic")
    snap0 = store.snapshot(tenant_id, prof["id"])
    assert snap0["revision"] == prof["revision"]
    assert snap0["scenario"] == {}
    assert snap0["defaults"] == {}

    snap1 = store.publish(tenant_id, prof["id"], snap0["revision"], scenario={"tilt": 20})
    assert snap1["revision"] != snap0["revision"]
    assert snap1["scenario"] == {"tilt": 20}
    assert snap1["defaults"] == {}

    snap2 = store.publish(tenant_id, prof["id"], snap1["revision"], defaults={"azimuth": 180})
    assert snap2["revision"] != snap1["revision"]
    assert snap2["scenario"] == {"tilt": 20}
    assert snap2["defaults"] == {"azimuth": 180}


def test_cross_tenant_isolation_and_missing_denial(tmp_path):
    store = TenantStore(root=tmp_path / "store")
    t1 = store.create_tenant()
    t2 = store.create_tenant()
    p1 = store.create_profile(t1, manifest_ref="synthetic")
    p2 = store.create_profile(t2, manifest_ref="synthetic")

    for action in (
        lambda: store.snapshot(t1, p2["id"]),
        lambda: store.publish(t1, p2["id"], p2["revision"], scenario={"x": 1}),
        lambda: store.snapshot("missing-tenant", p1["id"]),
        lambda: store.snapshot(t1, "missing-profile"),
        lambda: store.publish("missing-tenant", p1["id"], p1["revision"], defaults={"y": 2}),
        lambda: store.publish(t1, "missing-profile", p1["revision"], defaults={"y": 2}),
    ):
        with pytest.raises(NotFound) as exc:
            action()
        assert exc.value.status == 404


def test_atomic_stale_write_preserves_state(tmp_path):
    store = TenantStore(root=tmp_path / "store")
    tenant_id = store.create_tenant()
    prof = store.create_profile(tenant_id, manifest_ref="synthetic")
    current = store.publish(tenant_id, prof["id"], prof["revision"], scenario={"k": "v1"})

    with pytest.raises(Conflict) as exc:
        store.publish(tenant_id, prof["id"], "stale-revision", scenario={"k": "v2"})
    assert exc.value.status == 409

    after = store.snapshot(tenant_id, prof["id"])
    assert after["revision"] == current["revision"]
    assert after["scenario"] == {"k": "v1"}
    assert after["defaults"] == {}


def test_simultaneous_independent_connections_cas(tmp_path):
    import threading
    root = tmp_path / "store"
    s1 = TenantStore(root=root)
    s2 = TenantStore(root=root)

    t1 = s1.create_tenant()
    t2 = s1.create_tenant()
    p1 = s1.create_profile(t1, manifest_ref="synthetic")
    p2 = s1.create_profile(t2, manifest_ref="synthetic")

    snap_t2_before = s2.snapshot(t2, p2["id"])

    ready = threading.Barrier(2)
    def run_publish(store, payload):
        ready.wait(timeout=10)
        return store.publish(t1, p1["id"], p1["revision"], defaults=payload)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(run_publish, s1, {"writer": "conn1"})
        f2 = executor.submit(run_publish, s2, {"writer": "conn2"})

    successes = []
    conflicts = []
    for f in (f1, f2):
        try:
            successes.append(f.result())
        except Conflict as exc:
            conflicts.append(exc)

    assert len(successes) == 1
    assert len(conflicts) == 1
    assert conflicts[0].status == 409

    winner = successes[0]
    final_snap = s1.snapshot(t1, p1["id"])
    assert final_snap["revision"] == winner["revision"]
    assert final_snap["defaults"] in ({"writer": "conn1"}, {"writer": "conn2"})

    snap_t2_after = s2.snapshot(t2, p2["id"])
    assert snap_t2_after == snap_t2_before


def test_failed_revision_publication_rolls_back_all_rows(tmp_path, monkeypatch):
    store = TenantStore(tmp_path / 'store')
    owner = store.create_tenant()
    profile = store.create_profile(owner)
    before = store.snapshot(owner, profile['id'])
    original = store._insert_revision
    def fail(*args):
        original(*args)
        raise OSError('synthetic publication failure')
    monkeypatch.setattr(store, '_insert_revision', fail)
    with pytest.raises(OSError):
        store.publish(owner, profile['id'], before['revision'], defaults={'rate': 2})
    assert store.snapshot(owner, profile['id']) == before
    with store._connection() as db:
        assert db.execute('SELECT count(*) FROM revisions').fetchone()[0] == 1


def test_uploads_jobs_profiles_deny_foreign_valid_ids(tmp_path):
    from feasibility.tenant_store import Unauthorized
    store = TenantStore(tmp_path / 'store')
    one, two = store.create_tenant(), store.create_tenant()
    p1, p2 = store.create_profile(one), store.create_profile(two)
    upload, duplicate = store.put_upload(one, b'synthetic upload', {'included': True})
    assert not duplicate
    job = store.create_job(one, p1['id'], p1['revision'], 'a' * 64)
    actions = [lambda: store.upload(two, upload['id']), lambda: store.read_upload(two, upload['id']),
               lambda: store.update_upload(two, upload['id'], included=False),
               lambda: store.job(two, job['id']), lambda: store.fail_job(two, job['id']),
               lambda: store.publish_result(two, job['id'], {}), lambda: store.read_result(two, job['id']),
               lambda: store.delete_profile(two, p1['id']),
               lambda: store.create_job(two, p1['id'], p1['revision'], 'a' * 64)]
    for action in actions:
        with pytest.raises(NotFound, match='^Not found$'):
            action()
    assert store.snapshot(two, p2['id'])['scenario'] == {}
    store.fail_job(one, job['id'], '/private/source TOKEN latitude CSV')
    assert store.job(one, job['id'])['error'] == 'Computation failed'
    store.delete_profile(one, p1['id'])
    with pytest.raises(NotFound):
        store.job(one, job['id'])


def test_evidence_deduplicates_only_within_tenant_and_preserves_preview(tmp_path):
    from feasibility.evidence_store import EvidenceStore
    store = TenantStore(tmp_path / 'store')
    one, two = store.create_tenant(), store.create_tenant()
    a = EvidenceStore(store, one, store.root / 'tenants' / one)
    b = EvidenceStore(store, two, store.root / 'tenants' / two)
    raw = b'# PVGIS 5.3\ntime,P\n20240101:0010,0\n20240101:0110,120\n'
    item = a.save_upload('roof.csv', raw, 'pvgis53', 'Roof')
    store.put_upload(one, b'non-evidence upload', {'purpose': 'consumption'})
    duplicate = a.save_upload('renamed.csv', raw, 'pvgis53', 'roof')
    other = b.save_upload('roof.csv', raw, 'pvgis53', 'Roof')
    assert duplicate['duplicate'] and duplicate['dataset']['id'] == item['dataset']['id']
    assert not other['duplicate'] and other['dataset']['id'] != item['dataset']['id']
    assert a.download(item['dataset']['id']) == raw
    with pytest.raises(NotFound):
        b.download(item['dataset']['id'])
    with pytest.raises(NotFound):
        b.set_inclusion({'id': item['dataset']['id'], 'included': False})
    assert a.inventory()['coverage'][0]['total_hours'] == 2
    assert len(a.inventory()['datasets']) == 1
    a.set_inclusion({'id': item['dataset']['id'], 'included': False})
    assert not a.inventory()['datasets'][0]['included']
    assert b.inventory()['datasets'][0]['included']
    assert item['dataset']['active_calculator_data_modified'] is False


def test_failed_upload_and_result_publication_cleanup(tmp_path, monkeypatch):
    store = TenantStore(tmp_path / 'store')
    owner = store.create_tenant()
    profile = store.create_profile(owner)
    original = store.files._write_once
    def fail(*args):
        original(*args)
        raise OSError('synthetic fsync failure')
    monkeypatch.setattr(store.files, '_write_once', fail)
    with pytest.raises(ValueError, match='Store path unavailable or unsafe'):
        store.put_upload(owner, b'synthetic', {})
    assert store.uploads(owner) == []
    assert list((store.root / 'tenants' / owner / 'uploads').iterdir()) == []
    job = store.create_job(owner, profile['id'], profile['revision'], 'a' * 64)
    with pytest.raises(ValueError, match='Store path unavailable or unsafe'):
        store.publish_result(owner, job['id'], {'value': 1})
    assert store.job(owner, job['id'])['state'] == 'queued'
    assert list((store.root / 'tenants' / owner / 'results').iterdir()) == []


def test_result_revision_and_content_are_pinned(tmp_path):
    store = TenantStore(tmp_path / 'store')
    owner = store.create_tenant()
    profile = store.create_profile(owner)
    job = store.create_job(owner, profile['id'], profile['revision'], 'a' * 64)
    store.publish_result(owner, job['id'], {'value': 2})
    assert store.read_result(owner, job['id']) == {'value': 2}
    store.publish(owner, profile['id'], profile['revision'], defaults={'rate': 3})
    with pytest.raises(Conflict):
        store.read_result(owner, job['id'])
    with pytest.raises(Conflict):
        store.create_job(owner, profile['id'], profile['revision'], 'a' * 64)


def test_delete_revokes_first_calls_shutdown_and_preserves_other_tenant(tmp_path):
    from feasibility.sessions import Sessions, token_hash
    from feasibility.tenant_store import Unauthorized
    from http.cookies import SimpleCookie
    store = TenantStore(tmp_path / 'store')
    body, cookie = Sessions(store).create()
    owner = body['tenant_id']
    token = SimpleCookie(cookie)['pv_session'].value
    profile = store.create_profile(owner)
    job = store.create_job(owner, profile['id'], profile['revision'], 'a' * 64)
    other = store.create_tenant()
    other_upload, _ = store.put_upload(other, b'other', {})
    folder = store.root / 'tenants' / owner
    for kind in ('quarantine', 'cache', 'results'):
        with store.files._directory(('tenants', owner, kind), create=True) as directory:
            store.files._write_once(directory, 'sample', b'synthetic')
    called = []
    def shutdown(tenant):
        called.append(tenant)
        assert folder.exists()
        with pytest.raises(Unauthorized):
            store.resolve_session(token_hash(token))
        with store._connection() as db:
            assert db.execute('SELECT state FROM jobs WHERE tenant_id=? AND id=?', (owner, job['id'])).fetchone()[0] == 'cancelled'
    store.worker_shutdown = shutdown
    store.delete_tenant(owner)
    assert called == [owner] and not folder.exists()
    assert store.read_upload(other, other_upload['id']) == b'other'
    with store._connection() as db:
        assert db.execute('SELECT count(*) FROM tenants WHERE id=?', (owner,)).fetchone()[0] == 0
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_failed_shutdown_is_tombstoned_then_recovered(tmp_path):
    from feasibility.sessions import Sessions
    from feasibility.tenant_store import Unauthorized
    store = TenantStore(tmp_path / 'store')
    body, cookie = Sessions(store).create()
    owner = body['tenant_id']
    def fail(owner):
        raise OSError('worker unavailable')
    store.worker_shutdown = fail
    with pytest.raises(OSError):
        store.delete_tenant(owner)
    with pytest.raises(Unauthorized):
        Sessions(store).authenticate({'Cookie': cookie})
    with pytest.raises(NotFound):
        store.create_profile(owner)
    called = []
    store.worker_shutdown = called.append
    assert store.janitor()['deleted_tenants'] == 1
    assert called == [owner]


def test_janitor_removes_owned_orphans_without_touching_foreign_files(tmp_path):
    from feasibility.profile_store import new_id
    store = TenantStore(tmp_path / 'store')
    owner, other = store.create_tenant(), store.create_tenant()
    item, _ = store.put_upload(owner, b'live', {})
    for kind in ('uploads', 'results', 'quarantine', 'cache'):
        path = store.root / 'tenants' / owner / kind / ('.stage-' + new_id())
        with store.files._directory(('tenants', owner, kind, path.name), create=True) as directory:
            store.files._write_once(directory, 'partial', b'partial')
    unknown = store.root / 'tenants' / new_id()
    with store.files._directory(('tenants', unknown.name), create=True) as directory:
        store.files._write_once(directory, 'preserve', b'not created by TenantStore')
    other_upload, _ = store.put_upload(other, b'other', {})
    assert store.janitor()['orphan_uploads'] == 4
    assert (unknown / 'preserve').exists()
    assert store.read_upload(owner, item['id']) == b'live'
    assert store.read_upload(other, other_upload['id']) == b'other'


def test_explicit_legacy_dry_run_apply_once_and_readonly_backup(tmp_path):
    import json
    import stat
    store = TenantStore(tmp_path / 'store')
    owner = store.create_tenant()
    profile = store.create_profile(owner, defaults={'factory': {'example': 1}, 'overrides': {}})
    scenario = tmp_path / 'scenario.json'
    defaults = tmp_path / 'defaults.json'
    blobs = [b'{"settings":{"example":2},"quote":{}}', b'{"overrides":{"example":{"value":3}}}']
    scenario.write_bytes(blobs[0])
    defaults.write_bytes(blobs[1])
    kwargs = dict(scenario_path=scenario, defaults_path=defaults)
    before = store.snapshot(owner, profile['id'])
    report = store.migrate_legacy(owner, profile['id'], profile['revision'], **kwargs)
    assert report['applied'] is False and store.snapshot(owner, profile['id']) == before
    assert not list((store.root / 'tenants' / owner).glob('migration-*'))
    applied = store.migrate_legacy(owner, profile['id'], profile['revision'], apply=True, **kwargs)
    assert applied['applied'] and applied['digest'] == report['digest']
    assert store.snapshot(owner, profile['id'])['scenario'] == json.loads(blobs[0])
    backup = list((store.root / 'tenants' / owner).glob('migration-*'))
    assert len(backup) == 1
    for index, raw in enumerate(blobs):
        path = backup[0] / f'{index}.json'
        assert path.read_bytes() == raw
        if os.name == 'nt':
            assert path.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
        else:
            assert stat.S_IMODE(path.stat().st_mode) == 0o400
    assert scenario.read_bytes() == blobs[0] and defaults.read_bytes() == blobs[1]
    with pytest.raises(Conflict):
        store.migrate_legacy(owner, profile['id'], applied['revision'], apply=True, **kwargs)
    assert store.janitor()['orphan_uploads'] == 0


def test_explicit_state_defaults_adapters_preserve_snapshots_and_cas(tmp_path):
    from feasibility import assumptions, shared_state
    from feasibility.tenant_store import TenantState, TenantDefaults
    store = TenantStore(tmp_path / 'store')
    one, two = store.create_tenant(), store.create_tenant()
    factory = {'synthetic_rate': 2.0}
    profile = store.create_profile(one, defaults={'factory': factory, 'overrides': {}})
    other = store.create_profile(two, defaults={'factory': {'synthetic_rate': 9.0}, 'overrides': {}})
    frozen = store.snapshot(one, profile['id'])
    a, b = TenantDefaults(store, one, profile['id']), TenantDefaults(store, one, profile['id'])
    def validate(candidate, *, base, supplied):
        return {**base, **candidate}
    assumptions.save_user_default('synthetic_rate', 3.0, validate, path=a)
    with pytest.raises(Conflict):
        assumptions.save_user_default('synthetic_rate', 4.0, validate, path=b)
    assert assumptions.defaults(path=a) == factory
    fresh = TenantDefaults(store, one, profile['id'])
    assert assumptions.persisted_defaults(fresh) == {'synthetic_rate': 3.0}
    assert assumptions.records(fresh)[0]['value'] == 3.0
    assumptions.reset_user_default('synthetic_rate', path=fresh)
    assert assumptions.persisted_defaults(TenantDefaults(store, one, profile['id'])) == factory
    assert store.snapshot(one, profile['id'], frozen['revision']) == frozen
    assert assumptions.persisted_defaults(TenantDefaults(store, two, other['id'])) == {'synthetic_rate': 9.0}
    adapter = TenantState(store, one, profile['id'])
    initial = shared_state.get_state(path=adapter)
    updated = shared_state.update_state({'revision': initial['revision'], 'settings': {'synthetic_rate': 4.0}}, path=adapter)
    assert updated['settings'] == {'synthetic_rate': 4.0}
    assert shared_state.get_state(updated['revision'], path=adapter)['unchanged']
    with pytest.raises(Conflict):
        shared_state.update_state({'revision': initial['revision'], 'settings': {}}, path=adapter)


def test_database_file_permissions_and_audit_payload_exclusion(tmp_path, caplog):
    import logging
    import stat
    caplog.set_level(logging.INFO, logger='feasibility.tenant_store')
    store = TenantStore(tmp_path / 'store')
    owner = store.create_tenant()
    profile = store.create_profile(owner)
    private = 'PRIVATE_COORDINATES_CSV_TOKEN_PATH'
    store.publish(owner, profile['id'], profile['revision'], scenario={'text': private})
    store.put_upload(owner, private.encode(), {'filename': private})
    assert private not in repr([r.__dict__ for r in caplog.records])
    if os.name == 'nt':
        from feasibility import windows_storage
        with windows_storage.directory(store.root, ()), windows_storage.file(store.db_path, mutable=True):
            pass
    else:
        assert stat.S_IMODE(store.db_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    if os.name == 'nt':
        import subprocess
        subprocess.run(['icacls', str(store.db_path), '/grant', '*S-1-1-0:R'],
                       check=True, capture_output=True)
    else:
        store.db_path.chmod(0o644)
    with pytest.raises(ValueError):
        TenantStore(store.root)


def test_legacy_description_metadata_is_explicit_and_never_used_by_tenant_defaults(tmp_path, monkeypatch):
    import json
    from feasibility import assumptions
    from feasibility.tenant_store import TenantDefaults, legacy_default_descriptions
    store = TenantStore(tmp_path / 'store')
    monkeypatch.setenv('PV_DATA_ROOT', str(store.root))
    assert legacy_default_descriptions() == {}
    path = store.root / 'legacy-default-descriptions.json'
    value = {'municipal_additional_tax': 'Synthetic compatibility description'}
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    assert legacy_default_descriptions() == value
    owner = store.create_tenant()
    profile = store.create_profile(owner, defaults={'factory': {'example': 2.0}, 'overrides': {}})
    assert 'compatibility' not in repr(assumptions.records(TenantDefaults(store, owner, profile['id'])))
    path.write_text('{"unexpected":"field"}')
    with pytest.raises(ValueError):
        legacy_default_descriptions()


def test_profile_name_durability_across_revisions_and_reopen(tmp_path):
    store = TenantStore(root=tmp_path / "store")
    tenant_id = store.create_tenant()
    prof = store.create_profile(tenant_id, name="  Production Profile  ")

    # Initial profile list has trimmed name and initial revision
    profiles_0 = store.profiles(tenant_id)
    assert len(profiles_0) == 1
    assert profiles_0[0] == {
        "id": prof["id"],
        "revision": prof["revision"],
        "name": "Production Profile",
    }

    # Name is durable across revision updates
    snap1 = store.publish(tenant_id, prof["id"], prof["revision"], scenario={"tilt": 20})
    profiles_1 = store.profiles(tenant_id)
    assert profiles_1 == [
        {
            "id": prof["id"],
            "revision": snap1["revision"],
            "name": "Production Profile",
        }
    ]

    snap2 = store.publish(tenant_id, prof["id"], snap1["revision"], defaults={"azimuth": 180})
    profiles_2 = store.profiles(tenant_id)
    assert profiles_2 == [
        {
            "id": prof["id"],
            "revision": snap2["revision"],
            "name": "Production Profile",
        }
    ]

    # Name is durable after closing and reopening the store
    reopened = TenantStore(root=tmp_path / "store")
    assert reopened.profiles(tenant_id) == [
        {
            "id": prof["id"],
            "revision": snap2["revision"],
            "name": "Production Profile",
        }
    ]


def test_profile_name_valid_default_and_whitespace_fallback(tmp_path):
    store = TenantStore(root=tmp_path / "store")
    tenant_id = store.create_tenant()

    p_default = store.create_profile(tenant_id)
    p_empty = store.create_profile(tenant_id, name="")
    p_spaces = store.create_profile(tenant_id, name="   ")

    profiles_by_id = {p["id"]: p for p in store.profiles(tenant_id)}
    assert profiles_by_id[p_default["id"]]["name"] == ""
    assert profiles_by_id[p_empty["id"]]["name"] == ""
    assert profiles_by_id[p_spaces["id"]]["name"] == ""
    assert set(profiles_by_id[p_default["id"]].keys()) == {"id", "revision", "name"}


@pytest.mark.parametrize(
    "invalid_name",
    [
        123,
        None,
        ["Profile"],
        {"name": "Profile"},
        "a" * 121,
        "  " + "b" * 121 + "  ",
        "bad\x00name",
        "bad\nname",
        "bad\rname",
        "bad\tname",
        "bad\x1fname",
        "bad\x7fname",
    ],
)
def test_profile_name_invalid_rejects_without_creating_rows(tmp_path, invalid_name):
    from feasibility.tenant_store import StoreError

    store = TenantStore(root=tmp_path / "store")
    tenant_id = store.create_tenant()

    with pytest.raises(StoreError) as exc:
        store.create_profile(tenant_id, name=invalid_name)
    assert exc.value.status == 400

    # Ensure no rows were inserted in any related tables
    assert store.profiles(tenant_id) == []
    with store._connection() as db:
        assert db.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM scenarios").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM defaults").fetchone()[0] == 0


def test_profile_schema_v1_migration_to_v2_idempotent(tmp_path):
    import sqlite3

    root = tmp_path / "store"
    s1 = TenantStore(root=root)
    t1 = s1.create_tenant()
    t2 = s1.create_tenant()

    p1 = s1.create_profile(t1)
    p2 = s1.create_profile(t2)
    published1 = s1.publish(t1, p1["id"], p1["revision"], scenario={"tilt": 30})
    snap1_expected = s1.snapshot(t1, p1["id"])
    snap2_expected = s1.snapshot(t2, p2["id"])

    # Synthesize legacy v1 database state: profiles table without 'name' and user_version = 1
    with sqlite3.connect(s1.db_path) as db:
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("""
            CREATE TABLE profiles_v1 (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                current_revision TEXT NOT NULL,
                UNIQUE(id, tenant_id),
                FOREIGN KEY(id, tenant_id, current_revision) REFERENCES revisions(profile_id, tenant_id, sha) DEFERRABLE INITIALLY DEFERRED
            )
        """)
        db.execute("INSERT INTO profiles_v1(id, tenant_id, current_revision) SELECT id, tenant_id, current_revision FROM profiles")
        db.execute("DROP TABLE profiles")
        db.execute("ALTER TABLE profiles_v1 RENAME TO profiles")
        db.execute("PRAGMA user_version = 1")
        db.commit()

    # Reopen store: triggers migration to v2
    s2 = TenantStore(root=root)
    with s2._connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        col_names = [row["name"] for row in db.execute("PRAGMA table_info(profiles)").fetchall()]
        assert "name" in col_names

    # Existing profiles preserved with default empty string name
    p_by_id = {p["id"]: p for p in s2.profiles(t1)}
    assert p_by_id[p1["id"]] == {"id": p1["id"], "revision": published1["revision"], "name": ""}
    p2_by_id = {p["id"]: p for p in s2.profiles(t2)}
    assert p2_by_id[p2["id"]] == {"id": p2["id"], "revision": p2["revision"], "name": ""}

    # Revisions and cross-tenant isolation preserved
    assert s2.snapshot(t1, p1["id"]) == snap1_expected
    assert s2.snapshot(t2, p2["id"]) == snap2_expected
    with pytest.raises(NotFound):
        s2.snapshot(t1, p2["id"])
    with pytest.raises(NotFound):
        s2.snapshot(t2, p1["id"])

    # Reopen store again: idempotent migration
    s3 = TenantStore(root=root)
    with s3._connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    assert {p["id"]: p for p in s3.profiles(t1)} == p_by_id
    assert {p["id"]: p for p in s3.profiles(t2)} == p2_by_id

    # New profiles can be created with names in migrated store
    p3 = s3.create_profile(t1, name="Post-Migration Profile")
    updated_profiles = {p["id"]: p for p in s3.profiles(t1)}
    assert updated_profiles[p3["id"]] == {
        "id": p3["id"],
        "revision": p3["revision"],
        "name": "Post-Migration Profile",
    }


def test_pairing_code_store_lifecycle_and_single_use(tmp_path):
    store = TenantStore(root=tmp_path / "store")
    tenant_id = store.create_tenant()
    code_hash = "a" * 64
    store.create_pairing_code(tenant_id, code_hash)

    with store._connection() as db:
        row = db.execute("SELECT * FROM pairing_codes WHERE code_hash=?", (code_hash,)).fetchone()
        assert row is not None
        assert row["tenant_id"] == tenant_id
        assert row["expires"] > store.clock()

    session_hash = "b" * 64
    assigned_tenant = store.consume_pairing_code(code_hash, session_hash)
    assert assigned_tenant == tenant_id

    # Session is active and bound to tenant
    assert store.resolve_session(session_hash) == tenant_id

    # Pairing code is deleted immediately upon consumption (single-use)
    with store._connection() as db:
        assert db.execute("SELECT * FROM pairing_codes WHERE code_hash=?", (code_hash,)).fetchone() is None

    # Subsequent consumption of same code must fail
    with pytest.raises(Unauthorized):
        store.consume_pairing_code(code_hash, "c" * 64)


def test_pairing_code_store_expiry_and_pruning(tmp_path):
    now = [1000.0]
    store = TenantStore(root=tmp_path / "store", clock=lambda: now[0])
    t1 = store.create_tenant()

    code_hash1 = "1" * 64
    store.create_pairing_code(t1, code_hash1)
    with store._connection() as db:
        row = db.execute("SELECT expires FROM pairing_codes WHERE code_hash=?", (code_hash1,)).fetchone()
        assert row["expires"] == 1300.0

    # Before expiry (299s later), consumption succeeds
    now[0] = 1299.0
    code_hash_valid = "2" * 64
    store.create_pairing_code(t1, code_hash_valid)
    assert store.consume_pairing_code(code_hash_valid, "a" * 64) == t1

    # After expiry (> 300s since creation), consumption fails
    now[0] = 1301.0
    with pytest.raises(Unauthorized):
        store.consume_pairing_code(code_hash1, "b" * 64)

    # Creating a new pairing code prunes expired records
    code_hash3 = "3" * 64
    store.create_pairing_code(t1, code_hash3)
    with store._connection() as db:
        assert db.execute("SELECT * FROM pairing_codes WHERE code_hash=?", (code_hash1,)).fetchone() is None
        assert db.execute("SELECT * FROM pairing_codes WHERE code_hash=?", (code_hash3,)).fetchone() is not None


def test_pairing_code_store_validation_and_errors(tmp_path):
    store = TenantStore(root=tmp_path / "store")
    tenant_id = store.create_tenant()

    # Invalid code_hash format
    with pytest.raises(StoreError):
        store.create_pairing_code(tenant_id, "invalid_hash")
    with pytest.raises(StoreError):
        store.create_pairing_code(tenant_id, "g" * 64)

    # Invalid consume inputs
    with pytest.raises(Unauthorized):
        store.consume_pairing_code("invalid", "a" * 64)
    with pytest.raises(Unauthorized):
        store.consume_pairing_code("a" * 64, "invalid")

    # Nonexistent code hash
    with pytest.raises(Unauthorized):
        store.consume_pairing_code("e" * 64, "f" * 64)

    # Pairing code created for tenant that gets deleted
    del_tenant = store.create_tenant()
    del_code = "d" * 64
    store.create_pairing_code(del_tenant, del_code)
    store.delete_tenant(del_tenant)
    with pytest.raises((NotFound, Unauthorized)):
        store.consume_pairing_code(del_code, "9" * 64)
