"""Synthetic persistence, integrity and readiness contracts; no household data."""
import copy
from dataclasses import replace
import os
from pathlib import Path
import stat

import pytest

from feasibility import profile_store as storage
from feasibility.profiles import (
    COMPILER_VERSION, PARSER_VERSION, canonical_json, parse_profile,
    strict_json, validate_ready,
)
from feasibility.profile_store import ProfileStore, new_id, resolve_data_root
from test_profiles import valid_draft_payload, valid_thermal_end_use


@pytest.fixture
def store(tmp_path):
    return ProfileStore(tmp_path / 'private')


@pytest.fixture
def owner():
    return new_id()


def artifact_path(store, owner, ref):
    return store.root / 'tenants' / owner / 'uploads' / ref.id / 'content'


def revision_path(store, owner, rev):
    return store.root / 'tenants' / owner / 'profiles' / rev.profile_id / 'revisions' / rev.revision / 'manifest.json'


def test_draft_roundtrip_and_permissions(store, owner, valid_draft_payload):
    profile = parse_profile(valid_draft_payload)
    rev = store.publish_revision(owner, profile, [])
    assert store.read_revision(owner, profile.id, rev.revision) == rev
    assert rev.profile == profile
    with pytest.raises(ValueError, match='Draft not ready'):
        store.validate_ready(owner, profile.id, rev.revision)
    for path in store.root.rglob('*'):
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)


def test_canonical_keys_refs_and_no_timestamp_identity(store, owner, valid_draft_payload):
    a = store.put_artifact(owner, b'first synthetic artifact', 'consumption')
    b = store.put_artifact(owner, b'second synthetic artifact', 'solar')
    p = parse_profile(valid_draft_payload)
    rev = store.publish_revision(owner, p, [a, b])
    path = revision_path(store, owner, rev)
    before = path.stat().st_mtime_ns
    os.utime(path, ns=(1, 1))
    reversed_keys = {k: valid_draft_payload[k] for k in reversed(valid_draft_payload)}
    assert store.publish_revision(owner, parse_profile(reversed_keys), [b, a]) == rev
    assert path.stat().st_mtime_ns == 1 != before


def test_roof_order_and_defaults_change_revision(store, owner, valid_draft_payload):
    valid_draft_payload['roofs'].append({**valid_draft_payload['roofs'][0], 'id': 'other'})
    first = store.publish_revision(owner, parse_profile(valid_draft_payload), [])
    valid_draft_payload['roofs'].reverse()
    second = store.publish_revision(owner, parse_profile(valid_draft_payload), [])
    assert first.revision != second.revision
    valid_draft_payload['default_settings']['panel_watts'] = 500
    third = store.publish_revision(owner, parse_profile(valid_draft_payload), [])
    assert third.revision != second.revision
    assert store.read_revision(owner, first.profile_id, first.revision) == first


def test_tenant_identity_separate_from_content_hash(store, owner, valid_draft_payload):
    other = new_id()
    profile = parse_profile(valid_draft_payload)
    first = store.publish_revision(owner, profile, [])
    with pytest.raises(ValueError):
        store.read_revision(other, profile.id, first.revision)
    second = store.publish_revision(other, profile, [])
    assert first.revision == second.revision
    a = store.put_artifact(owner, b'identical', 'consumption')
    b = store.put_artifact(other, b'identical', 'consumption')
    assert a.id != b.id and a.sha256 == b.sha256
    with pytest.raises(ValueError):
        store.read_artifact(other, a)
    with pytest.raises(ValueError):
        store.publish_revision(other, profile, [a])


@pytest.mark.parametrize('field', ['content', 'artifact.json', 'manifest.json'])
def test_changed_content_same_path_and_mtime_rejected(store, owner, valid_draft_payload, field):
    ref = store.put_artifact(owner, b'original', 'consumption')
    rev = store.publish_revision(owner, parse_profile(valid_draft_payload), [ref])
    path = revision_path(store, owner, rev) if field == 'manifest.json' else artifact_path(store, owner, ref).with_name(field)
    original_stat = path.stat()
    path.write_bytes(b'changed!' if field == 'content' else b'{}')
    os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    with pytest.raises(ValueError):
        store.read_revision(owner, rev.profile_id, rev.revision)


@pytest.mark.parametrize('bad', ['../escape', '/tmp/escape', '', 'a/b', 'A' * 32, 'x' * 32, True])
def test_invalid_owner_denied(store, bad):
    with pytest.raises(ValueError):
        store.put_artifact(bad, b'content', 'solar')


@pytest.mark.parametrize('profile_id,revision', [('../escape', 'a' * 64), ('a' * 32, '/tmp/escape'), ('a' * 32, '../escape')])
def test_invalid_revision_paths_denied(store, owner, profile_id, revision):
    with pytest.raises(ValueError):
        store.read_revision(owner, profile_id, revision)


@pytest.mark.parametrize('level', ['root', 'tenant', 'artifact', 'content', 'manifest'])
def test_symlinks_denied(tmp_path, owner, valid_draft_payload, level):
    store = ProfileStore(tmp_path / 'private')
    ref = store.put_artifact(owner, b'synthetic', 'consumption')
    rev = store.publish_revision(owner, parse_profile(valid_draft_payload), [ref])
    paths = dict(root=store.root, tenant=store.root / 'tenants' / owner,
                 artifact=artifact_path(store, owner, ref).parent,
                 content=artifact_path(store, owner, ref), manifest=revision_path(store, owner, rev))
    path = paths[level]
    target = path.with_name(path.name + '-moved')
    path.rename(target)
    path.symlink_to(target, target_is_directory=target.is_dir())
    with pytest.raises(ValueError):
        store.read_revision(owner, rev.profile_id, rev.revision)


def test_root_symlink_ancestor_denied(tmp_path):
    target = tmp_path / 'target'
    target.mkdir(mode=0o700)
    link = tmp_path / 'link'
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError):
        ProfileStore(link / 'new')
    assert not (target / 'new').exists()


def test_atomic_failure_does_not_publish_revision(store, owner, valid_draft_payload, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('injected link failure')
    monkeypatch.setattr(storage.os, 'link', fail)
    with pytest.raises(ValueError):
        store.publish_revision(owner, parse_profile(valid_draft_payload), [])
    assert not list(store.root.rglob('manifest.json'))
    assert not list(store.root.rglob('.tmp-*'))


def test_fsync_failure_before_publication(store, owner, valid_draft_payload, monkeypatch):
    def fail(fd):
        raise OSError('injected fsync failure')
    monkeypatch.setattr(storage.os, 'fsync', fail)
    with pytest.raises(ValueError):
        store.publish_revision(owner, parse_profile(valid_draft_payload), [])
    assert not list(store.root.rglob('manifest.json'))


def test_artifact_references_cannot_be_forged(store, owner, valid_draft_payload):
    ref = store.put_artifact(owner, b'synthetic', 'consumption')
    for forged in (replace(ref, sha256='0' * 64), replace(ref, kind='solar'), replace(ref, size=100)):
        with pytest.raises(ValueError):
            store.publish_revision(owner, parse_profile(valid_draft_payload), [forged])
    with pytest.raises(ValueError):
        store.publish_revision(owner, parse_profile(valid_draft_payload), [ref, ref])


def test_no_replace_primitive(store):
    with store._directory(()) as fd:
        store._write_once(fd, 'immutable', b'first')
        with pytest.raises(FileExistsError):
            store._write_once(fd, 'immutable', b'second')
        assert store._read(fd, 'immutable', 100) == b'first'


def test_versions_bound_to_hash(store, owner, valid_draft_payload, monkeypatch):
    first = store.publish_revision(owner, parse_profile(valid_draft_payload), [])
    monkeypatch.setattr(storage, 'MODEL_VERSION', 'another-explicit-model')
    second = store.publish_revision(owner, parse_profile(valid_draft_payload), [])
    assert first.revision != second.revision
    with pytest.raises(ValueError, match='versions'):
        store.read_revision(owner, first.profile_id, first.revision)


def test_data_root_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv('PV_DATA_ROOT', str(tmp_path / 'custom'))
    assert resolve_data_root() == tmp_path / 'custom'
    monkeypatch.delenv('PV_DATA_ROOT')
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'xdg'))
    if storage.sys.platform not in ('win32', 'darwin'):
        assert resolve_data_root() == tmp_path / 'xdg' / 'pv-lab'
    with pytest.raises(ValueError, match='repositories'):
        ProfileStore(Path(__file__).absolute().parents[1] / 'new-profiles')
    repo = tmp_path / 'other-checkout'
    repo.mkdir()
    (repo / '.git').mkdir()
    with pytest.raises(ValueError):
        ProfileStore(repo / 'data')


def test_insecure_existing_root_rejected(tmp_path):
    root = tmp_path / 'insecure'
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    with pytest.raises(ValueError, match='owner-only'):
        ProfileStore(root)


def test_strict_json_and_canonical_unicode():
    assert canonical_json({'z': 1, 'a': 'è'}) == '{"a":"è","z":1}'.encode('utf-8')
    for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'\xff'):
        with pytest.raises(ValueError):
            strict_json(raw)
    with pytest.raises(ValueError):
        canonical_json({1: 'not a JSON key'})


def ready_inputs(store, owner, payload):
    payload = copy.deepcopy(payload)
    refs = [store.put_artifact(owner, ('synthetic ' + kind).encode(), kind)
            for kind in ('consumption', 'solar', 'weather')]
    payload['consumption']['artifact_id'] = refs[0].id
    payload['consumption']['coverage'] = {'start': '2025-01-01', 'end': '2026-01-01', 'complete': True}
    from tests.test_fiscal_policy import configure_fiscal
    configure_fiscal(payload)
    payload['market']['valuation_mode'] = 'flat'
    manifest = dict(kind='dataset_manifest', schema_version=1, parser_version=PARSER_VERSION,
                    compiler_version=COMPILER_VERSION, calendar_version='utc_hourly_v2',
                    years=[2025], roof_ids=['main'], location=payload['location'],
                    coverage=payload['consumption']['coverage'],
                    components=[dict(artifact_id=r.id, kind=r.kind, sha256=r.sha256) for r in refs])
    return payload, refs, manifest


def test_ready_verifies_content_coverage_and_alignment(store, owner, valid_draft_payload):
    payload, refs, manifest = ready_inputs(store, owner, valid_draft_payload)
    refs.append(store.put_artifact(owner, canonical_json(manifest), 'dataset_manifest'))
    rev = store.publish_revision(owner, parse_profile(payload), refs)
    assert store.validate_ready(owner, rev.profile_id, rev.revision) is None
    artifact_path(store, owner, refs[1]).write_bytes(b'changed')
    with pytest.raises(ValueError, match='digest/content'):
        store.validate_ready(owner, rev.profile_id, rev.revision)


@pytest.mark.parametrize('mutation', ['hash', 'coverage', 'roof', 'location', 'years', 'year_bool', 'version', 'missing', 'kind', 'duplicate'])
def test_ready_rejects_mismatched_manifest(store, owner, valid_draft_payload, mutation):
    payload, refs, m = ready_inputs(store, owner, valid_draft_payload)
    if mutation == 'hash':
        m['components'][0]['sha256'] = '0' * 64
    elif mutation == 'coverage':
        m['coverage'] = {**m['coverage'], 'complete': False}
    elif mutation == 'roof':
        m['roof_ids'] = ['other']
    elif mutation == 'location':
        m['location'] = {'latitude': 40, 'longitude': 10}
    elif mutation == 'years':
        m['years'] = list(range(1980, 2011))
    elif mutation == 'year_bool':
        m['years'] = [True]
    elif mutation == 'version':
        m['compiler_version'] = 'wrong'
    elif mutation == 'missing':
        m['components'].pop()
    elif mutation == 'kind':
        m['components'][0]['kind'] = 'solar'
    elif mutation == 'duplicate':
        m['components'].append(m['components'][0])
    refs.append(store.put_artifact(owner, canonical_json(m), 'dataset_manifest'))
    rev = store.publish_revision(owner, parse_profile(payload), refs)
    with pytest.raises(ValueError):
        store.validate_ready(owner, rev.profile_id, rev.revision)


def test_ready_requires_confirmation_and_pinned_prices(store, owner, valid_draft_payload):
    payload, refs, m = ready_inputs(store, owner, valid_draft_payload)
    blobs = {r.id: store.read_artifact(owner, r) for r in refs}
    blobs[new_id()] = canonical_json(m)
    payload['consumption']['kind'] = 'annual'
    with pytest.raises(ValueError, match='acknowledgement'):
        validate_ready(parse_profile(payload), blobs)
    payload['consumption']['acknowledged'] = True
    validate_ready(parse_profile(payload), blobs)
    payload['fiscal']['parameters']['confirmed'] = False
    with pytest.raises(ValueError, match='fiscal'):
        validate_ready(parse_profile(payload), blobs)
    payload['fiscal']['parameters']['confirmed'] = True
    payload['market']['valuation_mode'] = 'shape_scaled'
    with pytest.raises(ValueError, match='price'):
        validate_ready(parse_profile(payload), blobs)


def test_parsed_input_revalidated_at_storage_boundary(store, owner, valid_draft_payload):
    p = parse_profile(valid_draft_payload)
    forged = replace(p, default_settings={'unexpected': 1})
    with pytest.raises(ValueError, match='Unknown'):
        store.publish_revision(owner, forged, [])


@pytest.mark.parametrize('raw', [b'null', b'[]', b'{"kind":"weather"}', b'{"kind":"dataset_manifest"}'])
def test_malformed_ready_manifest_rejected(store, owner, valid_draft_payload, raw):
    ref = store.put_artifact(owner, raw, 'dataset_manifest')
    rev = store.publish_revision(owner, parse_profile(valid_draft_payload), [ref])
    with pytest.raises(ValueError):
        store.validate_ready(owner, rev.profile_id, rev.revision)


@pytest.mark.parametrize('key,value', [('panel_watts', True), ('horizon_years', 2.5),
                                      ('retail_commodity', float('nan'))])
def test_default_numeric_boundaries(valid_draft_payload, key, value):
    valid_draft_payload['default_settings'][key] = value
    with pytest.raises(ValueError):
        parse_profile(valid_draft_payload)


def test_non_string_json_keys_rejected(valid_draft_payload):
    valid_draft_payload[1] = 'extra'
    with pytest.raises(ValueError):
        parse_profile(valid_draft_payload)


def test_thermal_values_frozen_and_exclusive(valid_draft_payload, valid_thermal_end_use):
    use = valid_thermal_end_use
    use['measured_input'] = None
    use['unit'] = None
    use['monthly_useful_kwh'] = [100] * 12
    valid_draft_payload['thermal']['end_uses'] = [use]
    p = parse_profile(valid_draft_payload)
    use['monthly_useful_kwh'][0] = 200
    assert p.thermal.end_uses[0].monthly_useful_kwh == (100,) * 12
    use['measured_input'] = 100
    use['unit'] = 'kwh'
    with pytest.raises(ValueError, match='OR'):
        parse_profile(valid_draft_payload)


def test_frozen_settings_and_retail_index_rejection(valid_draft_payload):
    valid_draft_payload['default_settings'] = {'retail_contract': 'fixed', 'panel_watts': 500}
    p = parse_profile(valid_draft_payload)
    with pytest.raises(TypeError):
        p.default_settings['panel_watts'] = 600
    valid_draft_payload['default_settings']['retail_contract'] = 'pun_indexed'
    with pytest.raises(ValueError):
        parse_profile(valid_draft_payload)
