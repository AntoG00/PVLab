#!/usr/bin/env python3
"""Fail-closed publication checks. Reports contain identifiers, never matched text.

Audit covers every Git object and ignored working file. Binary findings require
separate private review; a text scan never clears an archive or image. Exit codes:
0 clean, 1 findings, 2 incomplete/error. No network or third-party dependencies.
"""
import argparse
import hashlib
import json
import io
import tarfile
import zipfile
import gzip
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

LIMIT = 1024 * 1024
EXCLUDED = {'.git'}
PRIVATE_DIRS = {'private', 'uploads', 'quarantine', 'backups', 'screenshots',
                'Consumption data', 'MGP prezzi', 'Solar API', 'CodexProject',
                '.agent-out', 'plan-state'}
PRIVATE_PREFIXES = ('feasibility/data/', 'feasibility/reports/')
BINARY_SUFFIXES = {'.npz', '.npy', '.zip', '.gz', '.tar', '.tgz', '.7z', '.rar',
                   '.pdf', '.docx', '.xlsx', '.pptx', '.png', '.jpg', '.jpeg',
                   '.gif', '.webp', '.tif', '.tiff', '.ico', '.sqlite', '.db'}
SECRETS = (
    rb'AKIA[0-9A-Z]{16}', rb'ASIA[0-9A-Z]{16}',
    rb'AIza[0-9A-Za-z_-]{35}', rb'gh[pousr]_[A-Za-z0-9_]{20,}',
    rb'github_pat_[A-Za-z0-9_]{20,}', rb'sk-[A-Za-z0-9_-]{20,}',
    rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    rb'(?i)(?:password|passwd|api[_-]?key|secret|access[_-]?token)\s*[=:]\s*[\x22\x27]?[A-Za-z0-9_+/.-]{12,}',
    rb'(?i)(?:https?|postgres(?:ql)?|mysql)://[^\s/:]+:[^\s/@]+@',
)
SECRET_RE = [re.compile(x) for x in SECRETS]
GEO_RE = re.compile(r'(?i)(?:PVGIS|Timeseries)[_-].*\d|[-+]?\d{1,3}\.\d{3,}[_ ,;-]+[-+]?\d{1,3}\.\d{3,}')
PERSONAL_RE = re.compile(rb'(?:/(?:home|Users)/[^/\s\x22\x27]+|/(?:run/media)/[^/\s\x22\x27]+)')
COORDINATE_RE = re.compile(rb"(?i)\b(?:latitude|longitude|lat|lon)[\x22\x27 \t]{0,4}[:=][\x22\x27 \t]{0,4}[-+]?[0-9]{1,3}\.[0-9]{3,}")



def git(root, *args, data=None):
    p = subprocess.run(['git', '-C', str(root), *args], input=data,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       env={**os.environ, 'GIT_NO_REPLACE_OBJECTS': '1'})
    if p.returncode:
        raise ValueError('Git operation failed (details withheld)')
    return p.stdout


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, indent=2, ensure_ascii=True)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def outside(path, root):
    p = Path(path).resolve()
    if p == root or root in p.parents:
        raise ValueError('Private inputs and reports must be outside repository')
    return p


def load_patterns(path):
    if path is None:
        return []
    p = Path(path)
    if not p.is_file():
        raise ValueError('Private patterns file missing')
    lines = p.read_text().splitlines()
    return [line for line in lines if line.strip()]


def content_reasons(data, patterns):
    reasons = set()
    for pattern in patterns:
        if any(pattern.encode(enc) in data for enc in ('utf-8', 'utf-16le', 'utf-16be')):
            reasons.add('private-literal')
    if any(rx.search(data) for rx in SECRET_RE):
        reasons.add('credential-candidate')
    if PERSONAL_RE.search(data):
        reasons.add('personal-filesystem-path')
    if COORDINATE_RE.search(data):
        reasons.add('precise-coordinate')
    # Scan decoded UTF-16 and UTF-8 text without exposing its contents.
    for enc in ('utf-8', 'utf-16le', 'utf-16be'):
        text = data.decode(enc, errors='ignore').encode('utf-8')
        if any(rx.search(text) for rx in SECRET_RE):
            reasons.add('credential-candidate')
        if PERSONAL_RE.search(text):
            reasons.add('personal-filesystem-path')
        if COORDINATE_RE.search(text):
            reasons.add('precise-coordinate')
    return reasons


def path_reasons(path):
    p = Path(path)
    reasons = set()
    if set(p.parts) & PRIVATE_DIRS or path.startswith(PRIVATE_PREFIXES):
        reasons.add('private-path')
    if p.name == '.env' or p.name.startswith('.env.') or p.suffix.lower() in {'.pem', '.key', '.dump', '.sql'}:
        reasons.add('credential-or-dump-path')
    if GEO_RE.search(p.name):
        reasons.add('geographic-filename')
    if p.name in {'INPUT_DATA_HANDOFF.md', 'AGENTS.md'}:
        reasons.add('private-handoff')
    if '.git' in p.parts:
        reasons.add('git-metadata')
    return reasons


class Scanner:
    def __init__(self, patterns, allowlist=None):
        self.patterns = patterns
        self.findings = []
        self.allowlist = allowlist or []
        self.count = 0

    def scan(self, data, path, kind='file', oid=None, mode=None, depth=0,
             total_size=None, digest=None, extra_reasons=None):
        self.count += 1
        digest = digest or hashlib.sha256(data).hexdigest()
        total_size = len(data) if total_size is None else total_size
        reasons = content_reasons(data, self.patterns) | path_reasons(path)
        # Owner-approved public identity is content classification, never a
        # secret/path exemption. File recognition binds exact bytes and lines;
        # Git recognition binds only author/committer header identities.
        public_lines = set()
        for item in self.allowlist:
            if not isinstance(item.get('reason'), str) or not item['reason'].strip():
                continue
            if (item.get('public_metadata') is True and item.get('path') == path
                    and item.get('sha256') == digest):
                public_lines.update(item.get('line_sha256', []))
        remaining = []
        recognized_any = False
        headers = kind == 'commit'
        for raw_line in data.splitlines(keepends=True):
            line = raw_line.rstrip(b'\r\n')
            recognized = hashlib.sha256(line).hexdigest() in public_lines
            if headers and line.startswith((b'author ', b'committer ')):
                match = re.fullmatch(rb'(?:author|committer) (.+ <[^<>\r\n]+>) [0-9]+ [+-][0-9]{4}', line)
                if match:
                    identity = hashlib.sha256(match[1]).hexdigest()
                    recognized |= any(a.get('public_git_identity_sha256') == identity
                                      and a.get('reason') for a in self.allowlist)
            if not line:
                headers = False
            recognized_any |= recognized
            remaining.append(b'\n' if recognized else raw_line)
        if recognized_any and 'private-literal' not in content_reasons(b''.join(remaining), self.patterns):
            reasons.discard('private-literal')
        reasons.update(extra_reasons or ())
        reasons |= content_reasons(path.encode('utf-8', errors='surrogateescape'), self.patterns)
        if total_size > LIMIT:
            reasons.add('large-artifact')
        binary = (Path(path).suffix.lower() in BINARY_SUFFIXES or
                  (kind in {'blob', 'file'} and (b'\0' in data[:8192] or
                   data.startswith((b'PK\x03\x04', b'\x89PNG', b'\xff\xd8', b'II*\0', b'MM\0*', b'\x1f\x8b')))))
        if binary:
            reasons.add('manual-review-required')
            if total_size > len(data):
                reasons.add('archive-inspection-limit')
        if mode in {'120000', '160000'}:
            reasons.add('symlink-or-submodule')
        # Inspect compressed members in memory only; never extract onto disk.
        if depth < 2 and kind in {'blob', 'file', 'archive-member'}:
            try:
                members = []
                budget = 8 * LIMIT
                if data.startswith(b'PK\x03\x04'):
                    with zipfile.ZipFile(io.BytesIO(data)) as archive:
                        infos = archive.infolist()
                        if len(infos) > 64 or sum(x.file_size for x in infos) > budget:
                            reasons.add('archive-inspection-limit')
                        else:
                            for item in infos:
                                if not item.is_dir():
                                    members.append((item.filename, archive.read(item)))
                elif data.startswith(b'\x1f\x8b'):
                    with gzip.GzipFile(fileobj=io.BytesIO(data)) as archive:
                        payload = archive.read(budget + 1)
                    if len(payload) > budget:
                        reasons.add('archive-inspection-limit')
                    else:
                        members.append(('gzip-member', payload))
                elif Path(path).suffix.lower() == '.tar':
                    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                        for i, item in enumerate(archive):
                            if i >= 64 or item.size > budget:
                                reasons.add('archive-inspection-limit')
                                break
                            if item.isfile():
                                budget -= item.size
                                members.append((item.name, archive.extractfile(item).read()))
                            elif not item.isdir():
                                reasons.add('symlink-or-submodule')
                for name, payload in members:
                    self.scan(payload, path + '!/' + name, 'archive-member', oid, depth=depth+1)
            except (OSError, EOFError, ValueError, RuntimeError, zipfile.BadZipFile, tarfile.TarError):
                reasons.add('archive-inspection-error')
        elif depth >= 2 and binary:
            reasons.add('archive-inspection-limit')
        # Only reviewed synthetic assets may bypass size/binary review rules.
        if any(a.get('path') == path and a.get('sha256') == digest and
               a.get('synthetic') is True and isinstance(a.get('reason'), str) and a['reason'].strip()
               for a in self.allowlist):
            reasons -= {'manual-review-required', 'large-artifact'}
        for reason in sorted(reasons):
            item = {'object_id': oid, 'type': kind, 'path': path,
                    'reason': reason, 'sha256': digest}
            item['fingerprint'] = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
            self.findings.append(item)


def scan_file(scanner, full, path):
    # Bound memory for rasters/dumps; scan all bytes with overlap for split literals.
    initial = full.stat()
    if initial.st_size <= 8 * LIMIT:
        scanner.scan(full.read_bytes(), path)
        return
    hasher = hashlib.sha256()
    reasons = set()
    head = b''
    overlap = max(4096, max((len(x.encode('utf-16le')) for x in scanner.patterns), default=0))
    tail = b''
    size = 0
    with full.open('rb') as f:
        for chunk in iter(lambda: f.read(LIMIT), b''):
            size += len(chunk)
            hasher.update(chunk)
            if len(head) < 8 * LIMIT:
                head += chunk
            reasons.update(content_reasons(tail + chunk, scanner.patterns))
            tail = (tail + chunk)[-overlap:]
    final = full.stat()
    if (initial.st_size, initial.st_mtime_ns, initial.st_ino) != (final.st_size, final.st_mtime_ns, final.st_ino):
        raise ValueError('Input changed during scan')
    scanner.scan(head, path, total_size=size, digest=hasher.hexdigest(), extra_reasons=reasons)


def worktree(scanner, root):
    for current, dirs, files in os.walk(root, followlinks=False):
        rel = Path(current).relative_to(root)
        keep = []
        for name in sorted(dirs):
            path = (rel / name).as_posix()
            full = root / path
            if path in EXCLUDED:
                continue
            if full.is_symlink():
                scanner.scan(os.readlink(full).encode(), path, mode='120000')
            else:
                keep.append(name)
        dirs[:] = keep
        for name in sorted(files):
            path = (rel / name).as_posix()
            full = root / path
            if full.is_symlink():
                scanner.scan(os.readlink(full).encode(), path, mode='120000')
            elif full.is_file():
                scan_file(scanner, full, path)
            else:
                raise ValueError('Unreadable or special file')


def staged(scanner, root):
    changed = set(git(root, 'diff', '--cached', '--name-only', '--diff-filter=ACMRT', '-z').split(b'\0'))
    for entry in git(root, 'ls-files', '--stage', '-z').split(b'\0'):
        if not entry:
            continue
        meta, raw_path = entry.split(b'\t', 1)
        mode, oid, stage = meta.decode().split()
        if stage != '0':
            raise ValueError('Unmerged index')
        if raw_path not in changed:
            continue
        path = os.fsdecode(raw_path)
        scanner.scan(git(root, 'cat-file', 'blob', oid), path, 'blob', oid, mode)


def objects(scanner, root):
    inventory = []
    for line in git(root, 'cat-file', '--batch-all-objects', '--batch-check=%(objectname) %(objecttype) %(objectsize)').splitlines():
        oid, kind, size = line.decode().split()
        inventory.append((oid, kind, int(size)))
    paths = {}
    trees = set()
    for oid, kind, size in inventory:
        if kind == 'commit':
            data = git(root, 'cat-file', kind, oid)
            tree_line = next((x for x in data.splitlines() if x.startswith(b'tree ')), None)
            if tree_line is None:
                raise ValueError('Malformed commit')
            trees.add(tree_line.split()[1].decode())
    # Every tree is traversed as well, covering dangling trees without commits.
    trees.update(oid for oid, kind, _ in inventory if kind == 'tree')
    for tree in sorted(trees):
        for entry in git(root, 'ls-tree', '-r', '-z', tree).split(b'\0'):
            if not entry:
                continue
            meta, name = entry.split(b'\t', 1)
            mode, kind, oid = meta.decode().split()
            paths.setdefault(oid, set()).add((os.fsdecode(name), mode))
    for oid, kind, size in inventory:
        data = git(root, 'cat-file', kind, oid)
        if len(data) != size:
            raise ValueError('Object size mismatch')
        names = paths.get(oid, {(f'object/{oid}', None)})
        for path, mode in sorted(names, key=lambda p: p[0]):
            scanner.scan(data, path, kind, oid, mode)
    return {'objects': len(inventory), 'historical_trees': len(trees)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=('audit', 'staged', 'tree', 'export'))
    p.add_argument('--root', type=Path, default=Path('.'))
    p.add_argument('--private-dir', type=Path)
    p.add_argument('--patterns', type=Path)
    p.add_argument('--report', type=Path)
    p.add_argument('--allowlist', type=Path)
    p.add_argument('--baseline', type=Path, help='Exact private implementation fingerprints; forbidden for export')
    args = p.parse_args(argv)
    report = None
    try:
        root = args.root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError('Root must be a directory')
        if args.private_dir:
            private = outside(args.private_dir, root)
        else:
            private = None
        patterns = args.patterns
        if args.mode == 'audit':
            if private is None:
                raise ValueError('Audit requires private directory')
            patterns = patterns or private / 'personal-patterns.txt'
            report = outside(args.report or private / 'privacy-audit.json', root)
        elif args.report:
            report = outside(args.report, root)
        if patterns is None and os.environ.get('PV_PRIVATE_ROOT'):
            candidate = Path(os.environ['PV_PRIVATE_ROOT']) / 'audit/personal-patterns.txt'
            if candidate.exists():
                patterns = candidate
        if patterns:
            outside(patterns, root)
        if args.allowlist:
            allowlist = json.loads(args.allowlist.read_text())
        elif (root / 'tools/export_allowlist.json').is_file():
            # The enforcing staged hook uses the INDEX policy, never unstaged
            # approvals. Export/tree inspection uses the copied reviewed policy.
            raw = (git(root, 'show', ':tools/export_allowlist.json') if args.mode == 'staged'
                   else (root / 'tools/export_allowlist.json').read_bytes())
            allowlist = json.loads(raw).get('assets', [])
        else:
            allowlist = []
        if not isinstance(allowlist, list) or not all(isinstance(x, dict) for x in allowlist):
            raise ValueError('Allowlist must contain explicit asset records')
        scanner = Scanner(load_patterns(patterns), allowlist)
        summary = {}
        if args.mode == 'audit':
            summary.update(objects(scanner, root))
            for name, command in (('local-config', ('config', '--local', '--list')),
                                  ('reflogs', ('reflog', 'show', '--all')),
                                  ('refs', ('for-each-ref', '--format=%(refname) %(objectname)'))):
                scanner.scan(git(root, *command), 'git-metadata/' + name, 'metadata')
            worktree(scanner, root)
        elif args.mode == 'staged':
            staged(scanner, root)
        else:
            if args.mode == 'export' and (root / '.git').exists():
                scanner.scan(b'', '.git')
            worktree(scanner, root)
        exempt = set()
        if args.baseline:
            if args.mode not in {'staged', 'tree'}:
                raise ValueError('Legacy exceptions forbidden for audit/export')
            outside(args.baseline, root)
            baseline = json.loads(args.baseline.read_text())
            if baseline.get('gate0_approved') is not True:
                raise ValueError('Baseline requires approved gate 0')
            exempt = set(baseline['fingerprints'])
        active = [x for x in scanner.findings if x['fingerprint'] not in exempt]
        result = {'schema_version': 1, 'mode': args.mode, 'complete': True,
                  'scanned': scanner.count, **summary, 'findings': scanner.findings,
                  'active_findings': len(active), 'exempted': len(scanner.findings)-len(active),
                  'dependency_exclusions': [],
                  'storage_exclusions': ['.git (objects scanned separately in audit)']}
        if report:
            atomic_json(report, result)
        print(f'Privacy check: {scanner.count} items; {len(active)} findings. Details withheld.')
        return 1 if active else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if report:
            atomic_json(report, {'schema_version': 1, 'complete': False, 'error': type(exc).__name__})
        print('Privacy check incomplete; inspect inputs and private report. No clearance.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
