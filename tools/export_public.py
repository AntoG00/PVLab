#!/usr/bin/env python3
"""Copy only committed reviewed files into a new, audited source-only export.

No repository metadata is copied or initialized. The executor audits the payload,
initializes fresh Git with an approved identity, and stops before publication.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {'.git', '.env', '.agent-out', 'private', 'runtime', 'uploads', 'quarantine',
             'backups', 'screenshots', 'plan-state', 'public-export', 'vendor', '__pycache__'}
GENERATED = {'PUBLIC_EXPORT_MANIFEST.json', 'SOFTWARE_MANIFEST.json'}


def git(root, *args):
    env = {k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    result = subprocess.run(['git','-C',str(root),*args],env=env,capture_output=True)
    if result.returncode:
        raise ValueError('Committed source is unavailable')
    return result.stdout


def relative_path(value):
    if not isinstance(value,str) or not value or '\\' in value or any(ord(c)<32 for c in value):
        raise ValueError('Invalid export path')
    p=PurePosixPath(value)
    if (p.is_absolute() or str(p)!=value or '..' in p.parts or set(p.parts)&FORBIDDEN
        or any(c in value for c in '*?[') or any(x.startswith('.env.') for x in p.parts)
        or value.startswith(('docs/public-plan/','feasibility/data/','feasibility/reports/'))
        or p.suffix.lower() in {'.pem','.key','.sqlite','.db','.npz','.npy','.tif','.tiff','.zip','.gz'}
        or value in {'AGENTS.md','INPUT_DATA_HANDOFF.md'}):
        raise ValueError('Disallowed export path')
    return value


def source_payload(root):
    root=Path(root).absolute()
    if Path(os.fsdecode(git(root,'rev-parse','--show-toplevel')).strip())!=root:
        raise ValueError('Source must be an explicit repository root')
    policy=json.loads(git(root,'show','HEAD:tools/export_allowlist.json'))
    files=policy.get('files')
    if policy.get('schema_version')!=1 or not isinstance(files,list) or not files or len(files)!=len(set(files)):
        raise ValueError('Invalid committed export inventory')
    result=[]
    for value in sorted(files):
        name=relative_path(value)
        if name in GENERATED:
            raise ValueError('Generated manifest cannot be a source entry')
        full=root/name
        if any(p.is_symlink() for p in (full,*full.parents)) or not full.is_file():
            raise ValueError('Source must be a regular file without symlink ancestors')
        entry=git(root,'ls-tree','-z','HEAD','--',name).rstrip(b'\0')
        if not entry:
            raise ValueError('Uncommitted or missing reviewed source')
        meta,actual=entry.split(b'\t',1);mode,kind,oid=meta.decode().split()
        if actual.decode()!=name or mode not in {'100644','100755'} or kind!='blob':
            raise ValueError('Source symlink, submodule or special file rejected')
        raw=git(root,'cat-file','blob',oid)
        result.append((name,raw,0o755 if mode=='100755' else 0o644))
    return result,policy.get('assets',[])


def manifest_for(destination, names):
    """The manifest hashes every payload file except itself; no private IDs/paths."""
    entries=[]
    for name in sorted(names):
        path=destination/name
        entries.append(dict(path=name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                            size=path.stat().st_size,mode=oct(stat.S_IMODE(path.stat().st_mode))))
    core=[x for x in entries if x['path'] not in {'docs/PUBLICATION_CHECKLIST.md','SOFTWARE_MANIFEST.json'}]
    core_digest=hashlib.sha256(json.dumps(core,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return dict(schema_version=1,distribution='source-code-only',history='fresh-local-Git-only; publication not performed',
                files=entries,reviewed_source_sha256=core_digest,
                digest_scope='reviewed_source_sha256 excludes checklist and generated manifests; files includes all payload except this manifest')


def export_committed(root, destination, patterns=()):
    root=Path(root).absolute();destination=Path(destination).absolute()
    if destination==root or destination in root.parents:
        raise ValueError('Destination overlaps source')
    if root in destination.parents and destination!=root/'public-export':
        raise ValueError('Only the owner-selected public-export directory may be nested')
    if any(p.is_symlink() for p in (destination,*destination.parents)):
        raise ValueError('Destination symlink rejected')
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError('Destination must be new or empty')
    payload,assets=source_payload(root)
    # Check all bytes before creating even a partial destination.
    sys.path.insert(0,str(ROOT/'tools'))
    from privacy_check import Scanner
    scanner=Scanner(list(patterns),assets)
    for name,raw,mode in payload:scanner.scan(raw,name)
    if scanner.findings:
        raise ValueError('Export privacy/secret review failed; no payload written')
    destination.mkdir(parents=False,mode=0o700,exist_ok=True)
    destination.chmod(0o700)
    for name,raw,mode in payload:
        path=destination/name;path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb') as f:f.write(raw)
        path.chmod(mode)
    software=dict(schema_version=1,project='pv-lab',distribution='source-code-only',classification='source-available',
        original_code_license='PolyForm-Noncommercial-1.0.0',third_party_notices='THIRD_PARTY_NOTICES.md',
        runtime='Linux CPython 3.12',tests_node='24.22.1',bundled_provider_data=False,bundled_dependencies=False,
        lock_sha256={name:hashlib.sha256(raw).hexdigest() for name,raw,_ in payload if name.startswith('requirements') and name.endswith('.lock')})
    (destination/'SOFTWARE_MANIFEST.json').write_text(json.dumps(software,indent=2)+'\n')
    (destination/'SOFTWARE_MANIFEST.json').chmod(0o644)
    manifest=manifest_for(destination,[name for name,_,_ in payload]+['SOFTWARE_MANIFEST.json'])
    (destination/'PUBLIC_EXPORT_MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (destination/'PUBLIC_EXPORT_MANIFEST.json').chmod(0o644)
    return manifest


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination',required=True,type=Path)
    args=parser.parse_args(argv)
    try:
        sys.path.insert(0,str(ROOT/'tools'))
        from plan_gate import require
        from privacy_check import load_patterns
        private=Path(os.environ['PV_PRIVATE_ROOT']).resolve(strict=True)
        # Phase 21 extends the cleared source; verify the immutable prerequisite
        # evidence without pretending its old build hash covers these new files.
        require(20,ROOT,private,current=False)
        dest=args.destination.absolute()
        if dest==ROOT/'public-export':
            exclude=ROOT/'.git/info/exclude'
            text=exclude.read_text() if exclude.exists() else ''
            if '/public-export/' not in text.splitlines():
                exclude.parent.mkdir(exist_ok=True);exclude.write_text(text.rstrip()+'\n/public-export/\n')
        manifest=export_committed(ROOT,dest,load_patterns(private/'audit/personal-patterns.txt'))
        print(f'Exported {len(manifest["files"])} reviewed/generated payload files; no Git metadata or publication action.')
        return 0
    except (OSError,ValueError,KeyError,TypeError) as error:
        print(f'Export blocked: {type(error).__name__}; inspect source/policy privately.',file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
