"""Negative safety-gate tests use synthetic, disposable repositories only."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

TOOL = Path(__file__).resolve().parents[1] / 'tools/plan_gate.py'


def invoke(root, private, *args):
    return subprocess.run([sys.executable, str(TOOL), *args, '--root', str(root)],
                          env={**os.environ, 'PV_PRIVATE_ROOT': str(private)},
                          text=True, capture_output=True)


def setup(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    private = tmp_path / 'private'
    if os.name == 'nt':
        from feasibility.windows_storage import directory
        with directory(private, (), create=True):
            pass
    else:
        private.mkdir(mode=0o700)
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    for key, value in [('user.name', 'Fixture'), ('user.email', 'fixture@example.invalid')]:
        subprocess.run(['git', '-C', str(root), 'config', key, value], check=True)
    (root / 'source.py').write_text('VALUE = 1\n')
    subprocess.run(['git', '-C', str(root), 'add', 'source.py'], check=True)
    subprocess.run(['git', '-C', str(root), 'commit', '-qm', 'fixture'], check=True)
    return root, private


def test_missing_gate_cannot_be_required(tmp_path):
    root, private = setup(tmp_path)
    assert invoke(root, private, 'require', '0').returncode == 1
    assert invoke(root, private, 'require', '1').returncode == 1


def test_missing_evidence_cannot_complete(tmp_path):
    root, private = setup(tmp_path)
    assert invoke(root, private, 'complete', '0').returncode == 1
    assert not (private / 'plan-state/phase-0.json').exists()


def test_no_approval_cannot_complete(tmp_path):
    root, private = setup(tmp_path)
    head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    evidence = private / 'evidence.json'
    evidence.write_text(json.dumps({'schema_version': 1, 'phase': 0,
                                   'status': 'complete', 'commits': [head],
                                   'reference_status': 'not-yet-established',
                                   'approvals': {}}))
    assert invoke(root, private, 'complete', '0', '--evidence', str(evidence)).returncode == 1
    assert not (private / 'plan-state/phase-0.json').exists()


def test_predecessor_required_even_with_evidence(tmp_path):
    root, private = setup(tmp_path)
    evidence = private / 'evidence.json'
    evidence.write_text('{}')
    assert invoke(root, private, 'complete', '1', '--evidence', str(evidence)).returncode == 1


def test_private_evidence_inside_checkout_rejected(tmp_path):
    root, private = setup(tmp_path)
    evidence = root / 'evidence.json'
    evidence.write_text('{}')
    assert invoke(root, private, 'complete', '0', '--evidence', str(evidence)).returncode == 1


def test_shared_private_directory_rejected(tmp_path):
    root, private = setup(tmp_path)
    if os.name == 'nt':
        private.rmdir()
        private.mkdir()  # Inherit the runner's ordinary ACL rather than a private DACL.
    else:
        private.chmod(0o755)
    assert invoke(root, private, 'require', '0').returncode == 1


def test_completed_gate_binds_evidence_and_source_bytes(tmp_path):
    root, private = setup(tmp_path)
    sys.path.insert(0, str(TOOL.parent))
    try:
        spec = importlib.util.spec_from_file_location('phase_gate_test_module', TOOL)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)

    def artifact(name, data):
        path = private / name
        path.write_text(json.dumps(data))
        return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

    backup = artifact('backup.json', {'verified': True})
    audit = artifact('audit.json', {'complete': True, 'objects': 1, 'findings': []})
    classifications = artifact('classifications.json', {'unresolved': 0, 'binary_unreviewed': 0, 'findings': []})
    leaks = artifact('leaks.json', [])
    log = artifact('run.log', {'result': 'passed'})
    head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    evidence = {
        'schema_version': 1, 'phase': 0, 'status': 'complete', 'commits': [head],
        'build': module.build(root), 'reference_status': 'not-yet-established',
        'approvals': {key: {'approved': True, 'source': 'Synthetic test fixture', 'by': 'Fixture'}
                      for key in ('owner_decisions', 'safety_clearance')},
        'commands': [{'argv': ['synthetic-check'], 'exit_code': 0, 'skipped': False, 'log': log}],
        'safety': {**{key: True for key in ('backup_verified', 'bundle_verified',
                   'all_objects_scanned', 'working_tree_scanned', 'all_findings_classified',
                   'binary_reviews_resolved', 'export_policy_fixed', 'pending_edits_preserved')},
                   'open_secret_incidents': 0, 'backup': backup, 'audit': audit,
                   'classifications': classifications, 'gitleaks_git': leaks, 'gitleaks_tree': leaks},
    }
    # Expected privacy findings may be reviewed; a failed test may never be hidden.
    finding = {'fingerprint': 'synthetic-finding', 'reason': 'private-literal'}
    evidence['safety']['audit'] = artifact('audit.json', {'complete': True, 'objects': 1, 'findings': [finding]})
    evidence['safety']['classifications'] = artifact('classifications.json', {
        'unresolved': 0, 'binary_unreviewed': 0,
        'findings': [{'fingerprint': 'synthetic-finding', 'disposition': 'private-only', 'reason': 'Synthetic private fixture'}],
    })
    command = evidence['commands'][0]
    command.update(exit_code=1, kind='privacy-findings', finding_report='audit',
                   argv=['python', 'tools/privacy_check.py', 'audit', '--report', evidence['safety']['audit']['path']])
    path = private / 'evidence.json'
    path.write_text(json.dumps(evidence))
    failed_test = {**command, 'argv': ['python', '-m', 'pytest', evidence['safety']['audit']['path']]}
    evidence['commands'] = [failed_test]
    path.write_text(json.dumps(evidence))
    assert invoke(root, private, 'complete', '0', '--evidence', str(path)).returncode == 1
    failed_test['argv'] = ['python', '-m', 'pytest', 'tools/privacy_check.py', 'audit', evidence['safety']['audit']['path']]
    path.write_text(json.dumps(evidence))
    assert invoke(root, private, 'complete', '0', '--evidence', str(path)).returncode == 1
    evidence['commands'] = [command]
    path.write_text(json.dumps(evidence))
    assert invoke(root, private, 'complete', '0', '--evidence', str(path)).returncode == 0
    assert invoke(root, private, 'require', '0').returncode == 0
    original = (root / 'source.py').stat()
    (root / 'source.py').write_text('VALUE = 2\n')
    os.utime(root / 'source.py', ns=(original.st_atime_ns, original.st_mtime_ns))
    assert invoke(root, private, 'require', '0').returncode == 1
    (root / 'source.py').write_text('VALUE = 1\n')
    assert invoke(root, private, 'require', '0').returncode == 0
    path.write_text(path.read_text() + '\n')
    assert invoke(root, private, 'require', '0').returncode == 1


def test_code_only_gate_checks_applicable_policy_and_retains_licensing_gate(tmp_path):
    import importlib.util
    import shutil
    import pytest
    sys.path.insert(0,str(TOOL.parent))
    try:
        spec=importlib.util.spec_from_file_location('code_release_gate',TOOL)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    finally:sys.path.pop(0)
    source=TOOL.parents[1];root=tmp_path/'source';root.mkdir();private=tmp_path/'private';private.mkdir(mode=0o700)
    for name in ['docs/RELEASE_POLICY.json','LICENSE','NOTICE','README.md','THIRD_PARTY_NOTICES.md','pyproject.toml','feasibility/server.py']:
        target=root/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/name,target)
    def artifact(name,obj):
        p=private/name;p.write_text(json.dumps(obj));return dict(path=str(p),sha256=module.digest(p))
    original_policy=(root/'docs/RELEASE_POLICY.json').read_bytes()
    policy=json.loads(original_policy)
    review=dict(unresolved_bundled_material=0,source_inventory_reviewed=True,
                third_party_terms_preserved=True,real_provider_data_bundled=False)
    release={k:True for k in ['private_exact','public_exact','privacy_secrets','dependency_audit','synthetic_provenance','clean_install','loopback_smoke']}
    release.update(policy=artifact('policy.json',policy),licensing_review=artifact('review.json',review))
    (private/'policy.json').write_bytes(original_policy)
    release['policy']['sha256']=module.digest(private/'policy.json')
    evidence=dict(release=release)
    module.validate_code_release(evidence,root,private)
    for key,value in [('hosted_service',True),('hosting_region','pending'),('default_host','0.0.0.0'),
                      ('fiscal_review','professional-review'),('specialist_review_release_blocker',True),('bundled_provider_data',True)]:
        changed={**policy,key:value};(root/'docs/RELEASE_POLICY.json').write_text(json.dumps(changed))
        with pytest.raises(module.Blocked):module.validate_code_release(evidence,root,private)
    (root/'docs/RELEASE_POLICY.json').write_bytes(original_policy)
    for field in ['private_exact','public_exact','privacy_secrets','dependency_audit','synthetic_provenance','clean_install','loopback_smoke']:
        with pytest.raises(module.Blocked):module.validate_code_release(dict(release={**release,field:False}),root,private)
    release['licensing_review']=artifact('bad-review.json',{**review,'unresolved_bundled_material':1})
    with pytest.raises(module.Blocked):module.validate_code_release(evidence,root,private)
    release['licensing_review']=artifact('review.json',review)
    (root/'LICENSE').write_text((root/'LICENSE').read_text()+'Modified terms\n')
    with pytest.raises(module.Blocked):module.validate_code_release(evidence,root,private)
