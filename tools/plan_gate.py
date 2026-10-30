#!/usr/bin/env python3
"""Validate private phase evidence. Completion never implies publication consent.

Records live only beneath PV_PRIVATE_ROOT/plan-state. Evidence must list commands
with zero exits, retained logs, commits, approvals, artifacts and reference status.
Phase zero additionally requires a verified backup, classified all-object audit,
reviewed binary findings and no open secret incidents. Missing facts block.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
from privacy_check import atomic_json


class Blocked(ValueError):
    pass


def git(root, *args):
    p = subprocess.run(['git', '-C', str(root), *args], capture_output=True)
    if p.returncode:
        raise Blocked('Git prerequisite failed')
    return p.stdout


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def build(root):
    """Bind evidence to HEAD, index, and actual source bytes, not mtimes."""
    h = hashlib.sha256(git(root, 'rev-parse', 'HEAD'))
    h.update(git(root, 'ls-files', '--stage', '-z'))
    names = set(git(root, 'ls-files', '-z').split(b'\0'))
    names.update(git(root, 'ls-files', '--others', '--exclude-standard', '-z').split(b'\0'))
    for name in sorted(names - {b''}):
        path = root / os.fsdecode(name)
        h.update(name + b'\0')
        if path.is_symlink():
            h.update(b'link:' + os.fsencode(os.readlink(path)))
        elif path.is_file():
            h.update(digest(path).encode())
        elif not path.exists():
            h.update(b'deleted')
        else:
            raise Blocked('Unsupported source entry')
    return h.hexdigest()


def private_path(value, root, private):
    path = Path(value).resolve(strict=True)
    if path == root or root in path.parents or (path != private and private not in path.parents):
        raise Blocked('Evidence must remain inside external private root')
    return path


def checked_artifact(item, root, private):
    path = private_path(item['path'], root, private)
    if not path.is_file() or digest(path) != item['sha256']:
        raise Blocked('Evidence artifact missing or changed')
    return path


def validate_code_release(evidence, root, private):
    """Apply only the confirmed source-code release requirements at Gate 20."""
    import ast
    import tomllib
    policy = json.loads((root / 'docs/RELEASE_POLICY.json').read_text())
    expected = dict(schema_version=1, distribution='source-code-only',
        project_classification='source-available', country='IT', hosted_service=False,
        hosted_operator_contact='not-applicable', hosting_region='not-applicable',
        default_host='127.0.0.1', license='PolyForm-Noncommercial-1.0.0',
        fiscal_review='multiple-LLMs-only', independent_fiscal_specialist_review=False,
        specialist_review_release_blocker=False, no_current_legal_correctness_claim=True,
        bundled_provider_data=False, publication_authorized=False,
        commercial_permission='separate-explicit-written-permission')
    if any(policy.get(k) != v or type(policy.get(k)) is not type(v) for k,v in expected.items()):
        raise Blocked('Code-only release policy is missing or inconsistent')
    if digest(root / 'LICENSE') != 'ffcca38841adb694b6f380647e15f17c446a4d1656fed51a1e2041d064c94cc8':
        raise Blocked('Official unmodified PolyForm Noncommercial 1.0.0 required')
    notice = 'Noncommercial use is permitted under the included license. Commercial use, including customer assessments and paid services, requires separate written permission from the copyright holder.'
    if notice not in (root / 'README.md').read_text() or not (root / 'NOTICE').read_text().startswith('Required Notice: Copyright '):
        raise Blocked('Required license/copyright notices missing')
    project = tomllib.loads((root / 'pyproject.toml').read_text())['project']
    if project.get('license') != policy['license'] or not {'LICENSE','NOTICE','THIRD_PARTY_NOTICES.md'} <= set(project.get('license-files', [])):
        raise Blocked('Package license/third-party notices missing')
    host_defaults = [kw.value.value for call in ast.walk(ast.parse((root / 'feasibility/server.py').read_text()))
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == 'add_argument'
        and call.args and isinstance(call.args[0], ast.Constant) and call.args[0].value == '--host'
        for kw in call.keywords if kw.arg == 'default' and isinstance(kw.value, ast.Constant)]
    if host_defaults != ['127.0.0.1']:
        raise Blocked('Default startup must bind to loopback')
    release = evidence.get('release', {})
    bound = checked_artifact(release['policy'], root, private)
    if bound.read_bytes() != (root / 'docs/RELEASE_POLICY.json').read_bytes():
        raise Blocked('Release evidence does not bind current owner policy')
    for key in ('private_exact','public_exact','privacy_secrets','dependency_audit',
                'synthetic_provenance','clean_install','loopback_smoke'):
        if release.get(key) is not True:
            raise Blocked('Applicable source-code release check incomplete')
    review = json.loads(checked_artifact(release['licensing_review'], root, private).read_text())
    if (type(review.get('unresolved_bundled_material')) is not int or
            review.get('unresolved_bundled_material') != 0 or
            review.get('source_inventory_reviewed') is not True or
            review.get('third_party_terms_preserved') is not True or
            review.get('real_provider_data_bundled') is not False):
        raise Blocked('Unresolved bundled code/data licensing')


def validate(evidence, phase, root, private):
    if evidence.get('schema_version') != 1 or evidence.get('phase') != phase:
        raise Blocked('Evidence version/phase mismatch')
    if evidence.get('status') != 'complete':
        raise Blocked('Evidence is not complete')
    if not evidence.get('commits'):
        raise Blocked('Missing commit evidence')
    for commit in evidence['commits']:
        git(root, 'merge-base', '--is-ancestor', commit, 'HEAD')
    expected_reference = {'not-yet-established'} if phase == 0 else {'exact', 'approved-delta'}
    if evidence.get('reference_status') not in expected_reference:
        raise Blocked('Reference status missing or invalid')
    if evidence.get('reference_status') == 'approved-delta' and not evidence.get('delta_approval'):
        raise Blocked('Numerical delta approval missing')
    approvals = evidence.get('approvals', {})
    required = ['owner_decisions', 'safety_clearance'] if phase == 0 else ['phase_acceptance']
    for key in required:
        item = approvals.get(key, {})
        if item.get('approved') is not True or not item.get('source') or not item.get('by'):
            raise Blocked('Required explicit approval missing')
    commands = evidence.get('commands', [])
    if not commands:
        raise Blocked('Command evidence missing')
    for item in commands:
        if not item.get('argv') or item.get('skipped') is not False:
            raise Blocked('Skipped or unspecified command')
        if item.get('exit_code') != 0:
            # A contaminated private repository is expected to produce findings.
            # Only these three audited reports may explain exit 1; tests never may.
            key = item.get('finding_report')
            argv = [str(x) for x in item['argv']]
            tool = Path(argv[0]).name
            scanner = ((tool.startswith('python') and len(argv) > 2 and
                        Path(argv[1]).name == 'privacy_check.py' and argv[2] == 'audit') or
                       (tool == 'privacy_check.py' and len(argv) > 1 and argv[1] == 'audit'))
            leak_mode = 'git' if key == 'gitleaks_git' else 'dir'
            leaks = bool(re.fullmatch(r'gitleaks(?:-v[0-9]+\.[0-9]+\.[0-9]+)?', tool)) and len(argv) > 1 and argv[1] == leak_mode
            if (phase != 0 or item.get('exit_code') != 1 or
                    item.get('kind') != 'privacy-findings' or
                    not ((key == 'audit' and scanner) or
                         (key in {'gitleaks_git', 'gitleaks_tree'} and leaks))):
                raise Blocked('Failed command cannot be cleared by a finding exemption')
            report = checked_artifact(evidence['safety'][key], root, private)
            if str(report) not in argv:
                raise Blocked('Finding command must name its bound report')
            contents = json.loads(report.read_text())
            findings = contents.get('findings', []) if key == 'audit' else contents
            if not isinstance(findings, list) or not findings:
                raise Blocked('Finding exit without a finding report')
        log_path = checked_artifact(item['log'], root, private)
        text = log_path.read_text(errors='replace')
        if re.search(r'\b[1-9][0-9]* skipped\b|(?:#|ℹ) skipped [1-9][0-9]*\b', text):
            raise Blocked('Skipped tests in verification log')
    for item in evidence.get('artifacts', []):
        checked_artifact(item, root, private)
    if phase == 0:
        safety = evidence.get('safety', {})
        for key in ('backup_verified', 'bundle_verified', 'all_objects_scanned',
                    'working_tree_scanned', 'all_findings_classified',
                    'binary_reviews_resolved', 'export_policy_fixed',
                    'pending_edits_preserved'):
            if safety.get(key) is not True:
                raise Blocked('Phase 0 safety evidence incomplete')
        if safety.get('open_secret_incidents') != 0:
            raise Blocked('Open or unassessed secret incidents')
        leak_findings = []
        for key in ('backup', 'audit', 'classifications', 'gitleaks_git', 'gitleaks_tree'):
            path = checked_artifact(safety[key], root, private)
            obj = json.loads(path.read_text())
            if key.startswith('gitleaks_'):
                if not isinstance(obj, list):
                    raise Blocked('Invalid Gitleaks report')
                leak_findings.extend(obj)
            if key == 'backup' and obj.get('verified') is not True:
                raise Blocked('Backup not verified')
            if key == 'audit' and (obj.get('complete') is not True or not obj.get('objects')):
                raise Blocked('Object audit incomplete')
            if key == 'classifications' and (obj.get('unresolved') != 0 or obj.get('binary_unreviewed') != 0):
                raise Blocked('Unresolved audit classifications')
        audit = json.loads(private_path(safety['audit']['path'], root, private).read_text())
        classified = json.loads(private_path(safety['classifications']['path'], root, private).read_text())
        leak_reviews = {x['fingerprint']: x for x in classified.get('gitleaks_reviews', [])}
        for finding in leak_findings:
            fingerprint = hashlib.sha256(json.dumps(finding, sort_keys=True).encode()).hexdigest()
            review = leak_reviews.get(fingerprint, {})
            if review.get('resolution') not in {'false-positive', 'revoked'} or not review.get('evidence'):
                raise Blocked('Unreviewed secret candidate')
        entries = {x['fingerprint']: x for x in classified.get('findings', [])}
        for finding in audit['findings']:
            review = entries.get(finding['fingerprint'], {})
            if review.get('disposition') not in {'private-only', 'sanitized', 'nonpersonal'} or not review.get('reason'):
                raise Blocked('Unclassified audit finding')
            if finding['reason'] == 'manual-review-required' and not review.get('review_evidence'):
                raise Blocked('Binary review evidence missing')
    if phase == 20:
        validate_code_release(evidence, root, private)
    return evidence


def require(phase, root, private, current=True):
    for n in range(phase + 1):
        state_path = private / 'plan-state' / f'phase-{n}.json'
        if not state_path.exists() and (private / 'plan-state' / f'phase-{n}-blocked.json').exists():
            raise Blocked(f'Unresolved phase {n}; review $PV_PRIVATE_ROOT/plan-state/phase-{n}-blocked.json privately')
        record = json.loads(state_path.read_text())
        ep = checked_artifact(record['evidence'], root, private)
        validate(json.loads(ep.read_text()), n, root, private)
        if current and n == phase and record.get('build') != build(root):
            raise Blocked('Working build changed since clearance')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('require', 'complete'))
    p.add_argument('phase', type=int, choices=range(22))
    p.add_argument('--evidence', type=Path)
    p.add_argument('--root', type=Path, default=Path('.'))
    args = p.parse_args(argv)
    try:
        root = args.root.resolve(strict=True)
        if not os.environ.get('PV_PRIVATE_ROOT'):
            raise Blocked('PV_PRIVATE_ROOT required')
        private = Path(os.environ['PV_PRIVATE_ROOT']).resolve(strict=True)
        if private == root or root in private.parents:
            raise Blocked('Private root must be outside checkout')
        if any((x / '.git').exists() for x in [private, *private.parents]):
            raise Blocked('Private root is inside a repository')
        if private.stat().st_mode & 0o077:
            raise Blocked('Private root must be owner-only')
        if args.command == 'require':
            require(args.phase, root, private)
        else:
            if args.phase:
                require(args.phase - 1, root, private, current=False)
            if args.evidence is None:
                raise Blocked('Evidence required')
            path = private_path(args.evidence, root, private)
            evidence = validate(json.loads(path.read_text()), args.phase, root, private)
            if evidence.get('build') != build(root):
                raise Blocked('Evidence does not match working build')
            state = private / 'plan-state' / f'phase-{args.phase}.json'
            if state.exists():
                raise Blocked('Completed gate exists; retain original evidence')
            atomic_json(state, {'schema_version': 1, 'phase': args.phase,
                               'build': evidence['build'],
                               'evidence': {'path': str(path), 'sha256': digest(path)}})
        print(f'Phase {args.phase}: verified.')
        return 0
    except Blocked as exc:
        print(f'Phase {args.phase}: BLOCKED ({exc}).', file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError):
        print(f'Phase {args.phase}: BLOCKED (missing, invalid or incompatible evidence).', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
