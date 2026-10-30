#!/usr/bin/env python3
"""Run the deterministic study and save reports using the same LAN engine."""
import sys,json,argparse
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'vendor'));sys.path.insert(0,str(HERE.parent))
from feasibility.worker_pool import run_local


def read_settings(path):
    from feasibility.profiles import strict_json
    with path.open('rb') as stream:
        raw=stream.read(2*1024**2+1)
    if len(raw)>2*1024**2:
        raise ValueError('Settings JSON exceeds limit')
    value=strict_json(raw)
    if not isinstance(value,dict):
        raise ValueError('Settings must be an object')
    return value


if __name__=='__main__':
    from feasibility.profile_store import ProfileStore
    import uuid
    parser=argparse.ArgumentParser();parser.add_argument('--settings',type=Path);parser.add_argument('--output',type=Path);args=parser.parse_args()
    settings=read_settings(args.settings) if args.settings else {}
    store=ProfileStore()
    destination=args.output or store.root / 'cli-reports' / uuid.uuid4().hex
    destination.mkdir(mode=0o700,parents=True,exist_ok=False)
    run_local('legacy-report', settings, {'profile_root':str(store.root / 'cli-profiles'), 'report_destination':str(destination)})
    print(destination,flush=True)
