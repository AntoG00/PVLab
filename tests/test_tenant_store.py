import concurrent.futures
import pytest
from feasibility.tenant_store import Conflict, NotFound, TenantStore


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
        (folder / kind).mkdir()
        (folder / kind / 'sample').write_bytes(b'synthetic')
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
        path.mkdir(parents=True, mode=0o700)
        path.parent.chmod(0o700)
        (path / 'partial').write_bytes(b'partial')
    unknown = store.root / 'tenants' / new_id()
    unknown.mkdir(mode=0o700)
    (unknown / 'preserve').write_bytes(b'not created by TenantStore')
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
        assert path.read_bytes() == raw and stat.S_IMODE(path.stat().st_mode) == 0o400
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
    assert stat.S_IMODE(store.db_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
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
