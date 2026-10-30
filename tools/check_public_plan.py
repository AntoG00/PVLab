#!/usr/bin/env python3
"""Run the private Python, Node, acceptance and frozen numerical gate.

Usage: python tools/check_public_plan.py --mode private
Defaults: adaptive test workers (2 * allocated physical cores) reserving one physical core; use --workers 1 for serial tests.
Scientific kernels and the sealed oracle retain their one-thread settings.
PV_PRIVATE_ROOT must identify the external owner-only private evidence directory.
Public mode tests a reviewed allowlist copy with fictional inputs and no private manifest.
Each run retains separate logs, exact commands, durations and status; failed or
skipped tests stop the gate before comparison. No oracle is ever refreshed.
"""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

# Use the recorded interpreter before loading scientific dependencies.
if __name__ == '__main__' and os.environ.get('PV_PRIVATE_ROOT') and 'public' not in sys.argv:
    try:
        boot = json.loads((Path(os.environ['PV_PRIVATE_ROOT']) / 'reference/manifest.json').read_text())['runtime_paths']
        if Path(sys.executable).resolve() != Path(boot['python']).resolve():
            os.environ['PYTHONPATH'] = os.pathsep.join((boot['vendor'], str(Path(__file__).resolve().parents[1])))
            os.execv(boot['python'], [boot['python'], *sys.argv])
    except (OSError, ValueError, KeyError):
        pass

from regression import THREAD_ENV, ROOT, live_guard_hashes, validate_manifest


def verify_environment(node):
    """Fail clearly on unreviewed dependencies; never install or find a fallback."""
    import importlib.metadata
    import platform
    import tomllib
    if platform.python_implementation() != 'CPython' or sys.version_info[:2] != (3, 12):
        raise ValueError('Use CPython 3.12; other runtimes require exact-reference review')
    if sys.platform != 'linux':
        raise ValueError('The bounded service currently requires Linux')
    spec = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    pins = [line.split()[0] for line in (ROOT / 'requirements.lock').read_text().splitlines()
            if line and not line[0].isspace() and not line.startswith('#')]
    if not set(spec['project']['dependencies']).issubset(pins):
        raise ValueError('Production lock differs from project requirements')
    pins += ['pytest==9.1.1', 'pytest-xdist==3.8.0', 'execnet==2.1.2']
    for pin in pins:
        name, version = pin.split('==')
        try:
            dist = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            raise ValueError(f'Missing {name}; install requirements-dev.lock with hashes') from None
        if dist.version != version:
            raise ValueError(f'Unreviewed {name} version; use requirements-dev.lock')
        origin = Path(dist.locate_file('')).resolve()
        vendor = (ROOT / 'feasibility/vendor').resolve()
        if origin == vendor or vendor in origin.parents:
            raise ValueError('Bundled vendor dependencies are not a supported environment')
    if subprocess.check_output([str(node), '--version'], text=True).strip() != 'v24.22.1':
        raise ValueError('Use Node.js 24.22.1 for the reviewed browser suite')


class Budget(dict):
    """Resource budget resolved from CPU topology and affinity."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


def _parse_topology(allowed_cpus=None, topology_root=Path('/sys/devices/system/cpu')) -> tuple[set[int], dict[tuple[int, int], list[int]]]:
    if allowed_cpus is None:
        allowed = os.sched_getaffinity(0)
    else:
        allowed = set(allowed_cpus)
    if not allowed:
        raise ValueError('No allowed CPUs available for affinity')

    cores_map: dict[tuple[int, int], list[int]] = {}
    topology_root = Path(topology_root)
    for cpu in sorted(allowed):
        cpu_dir = topology_root / f'cpu{cpu}' / 'topology'
        pkg_file = cpu_dir / 'physical_package_id'
        core_file = cpu_dir / 'core_id'
        try:
            pkg_id = int(pkg_file.read_text().strip())
            core_id = int(core_file.read_text().strip())
        except (OSError, ValueError) as err:
            raise OSError(f'Cannot read CPU topology for CPU {cpu} from {cpu_dir}: {err}') from err
        key = (pkg_id, core_id)
        if key not in cores_map:
            cores_map[key] = []
        cores_map[key].append(cpu)

    return allowed, cores_map


def cpu_set_for_cores(core_count, allowed_cpus=None, topology_root=Path('/sys/devices/system/cpu')) -> tuple[int, ...]:
    if not isinstance(core_count, int) or isinstance(core_count, bool) or core_count <= 0:
        raise ValueError('core_count must be a positive integer')
    _, cores_map = _parse_topology(allowed_cpus=allowed_cpus, topology_root=topology_root)

    sorted_keys = sorted(cores_map.keys())
    if len(sorted_keys) < core_count:
        raise ValueError(f'Requested {core_count} physical cores, but only {len(sorted_keys)} available')

    selected_keys = sorted_keys[-core_count:]
    selected_cpus = []
    for k in selected_keys:
        selected_cpus.extend(cores_map[k])

    return tuple(sorted(selected_cpus))


def resolve_budget(workers=None, physical_cores=None, allowed_cpus=None,
                   topology_root=Path('/sys/devices/system/cpu')) -> Budget:
    allowed, cores_map = _parse_topology(allowed_cpus=allowed_cpus, topology_root=topology_root)
    sorted_keys = sorted(cores_map.keys())
    available_physical = len(sorted_keys)

    if available_physical < 2:
        raise ValueError(f'At least 2 physical cores required (found {available_physical}); cannot reserve a core')

    if physical_cores is not None:
        if not isinstance(physical_cores, int) or isinstance(physical_cores, bool) or physical_cores <= 0:
            raise ValueError('physical_cores must be a positive integer')
        max_physical = available_physical - 1
        if physical_cores > max_physical:
            raise ValueError(
                f'Requested {physical_cores} physical cores, but maximum allowed is {max_physical} '
                f'(one physical core must be reserved out of {available_physical} available)'
            )
        alloc_cores = physical_cores
    else:
        alloc_cores = available_physical - 1

    if workers is not None:
        if not isinstance(workers, int) or isinstance(workers, bool) or workers <= 0:
            raise ValueError('workers must be a positive integer')
        alloc_workers = workers
    else:
        alloc_workers = 2 * alloc_cores

    reserved_group = sorted_keys[0]
    reserved_ids = tuple(sorted(cores_map[reserved_group]))

    selected_keys = sorted_keys[-alloc_cores:]
    selected_cpus = []
    for k in selected_keys:
        selected_cpus.extend(cores_map[k])
    selected_ids = tuple(sorted(selected_cpus))
    available_ids = tuple(sorted(allowed))

    return Budget(
        workers=alloc_workers,
        physical_cores=alloc_cores,
        selected_ids=list(selected_ids),
        available_ids=list(available_ids),
        reserved_ids=list(reserved_ids),
        cpu_ids=list(selected_ids),
    )


def build_commands(runtime, root, manifest_path, run, workers):
    if not isinstance(workers, int) or isinstance(workers, bool) or workers <= 0:
        raise ValueError('workers must be a positive integer')
    root = Path(root)
    run = Path(run)
    manifest_path = Path(manifest_path)
    node_tests = sorted(str(p.relative_to(root)) for p in (root / 'tests').glob('test_*.js'))
    if not node_tests:
        raise ValueError('Node suite missing')
    if workers > 1:
        pytest_cmd = [runtime['python'], '-m', 'pytest', '-n', str(workers), '--dist=worksteal', 'tests']
    else:
        pytest_cmd = [runtime['python'], '-m', 'pytest', 'tests']
    return [
        ('python', pytest_cmd),
        ('node', [runtime['node'], '--test', f'--test-concurrency={workers}', *node_tests]),
        ('acceptance', [runtime['python'], '-c',
                        "import test_support, runpy; runpy.run_path('verify.py', run_name='__main__')"]),
        ('regression', [runtime['python'], 'tools/regression.py', 'compare',
                        '--manifest', str(manifest_path), '--output', str(run / 'compare')]),
    ]


def run_gate(private, workers=None, physical_cores=None):
    private = Path(private)
    if private == ROOT or ROOT in private.parents or private.stat().st_mode & 0o077:
        raise ValueError('Private root must be external and owner-only')
    if any((ancestor / '.git').exists() for ancestor in (private, *private.parents)):
        raise ValueError('Private root must be outside Git ancestry')

    budget = resolve_budget(workers=workers, physical_cores=physical_cores)
    workers = budget.workers
    physical_cores = budget.physical_cores
    cpus = budget.selected_ids

    os.sched_setaffinity(0, cpus)
    print(f'Selected {workers} workers across {physical_cores} physical cores (CPU IDs: {list(cpus)})', flush=True)

    manifest_path = private / 'reference/manifest.json'
    manifest = validate_manifest(manifest_path)
    runtime = manifest['runtime_paths']
    verify_environment(runtime['node'])

    if workers > 1:
        if runtime.get('vendor') and runtime['vendor'] not in sys.path:
            sys.path.insert(0, runtime['vendor'])
        if importlib.util.find_spec('xdist') is None:
            raise ValueError('Install requirements-test-parallel.txt before using more than one worker')

    env = os.environ.copy()
    env.update(THREAD_ENV)
    env['PV_TEST_WORKERS'] = str(workers)
    env.pop('PYTEST_ADDOPTS', None)
    env.pop('PYTEST_PLUGINS', None)
    env['PYTHONPATH'] = os.pathsep.join((runtime['vendor'], str(ROOT), str(ROOT / 'tests')))
    run = private / 'runs' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
    run.mkdir(parents=True, mode=0o700)
    temp_root = run / 'tmp'
    temp_root.mkdir(mode=0o700)
    env['TMPDIR'] = str(temp_root)
    env['PYTEST_DEBUG_TEMPROOT'] = str(temp_root)

    numba_workers = run / 'numba-workers'
    numba_workers.mkdir(mode=0o700)
    numba_controller = run / 'numba-controller'
    numba_controller.mkdir(mode=0o700)
    env['PV_TEST_NUMBA_CACHE_ROOT'] = str(numba_workers)
    env['NUMBA_CACHE_DIR'] = str(numba_controller)

    (run / 'resource-budget.json').write_text(json.dumps(budget, indent=2))

    generator = "import importlib.util; from pathlib import Path; p=Path('tests/fixtures/public_demo/generate.py'); s=importlib.util.spec_from_file_location('public_generate',p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); m.prepare_test_inputs(Path(__import__('sys').argv[1]))"
    assets = run / 'synthetic'
    ordinary = {**env, 'PV_LEGACY_DATA_ROOT': str(assets/'legacy/data'),
        'PV_DATA_ROOT': str(assets/'runtime'), 'PV_PYTHON': runtime['python'], 'PV_TEST_MODE':'private'}
    env.pop('PV_LEGACY_DATA_ROOT',None)
    commands = [('generate',[runtime['python'],'-c',generator,str(assets)]),
                *build_commands(runtime, ROOT, manifest_path, run, workers)]
    before = live_guard_hashes()
    records = []
    try:
        for name, argv in commands:
            started = time.monotonic()
            log = run / (name + '.log')
            print(f'Running {name}; log retained in private run directory', flush=True)
            with log.open('w') as stream:
                proc = subprocess.run(argv, cwd=ROOT, env=env if name=='regression' else ordinary, stdout=stream, stderr=subprocess.STDOUT)
            text = log.read_text(errors='replace')
            import re
            skipped = bool(re.search(r'\b[1-9][0-9]* skipped\b|(?:#|ℹ) skipped [1-9][0-9]*\b', text))
            records.append(dict(argv=argv, exit_code=proc.returncode, skipped=skipped,
                                seconds=time.monotonic() - started, log=str(log)))
            (run / 'commands.json').write_text(json.dumps(records, indent=2))
            print(f'{name}: exit {proc.returncode}, {records[-1]["seconds"]:.2f}s', flush=True)
            if proc.returncode or skipped:
                return proc.returncode or 1
        return 0
    finally:
        if before != live_guard_hashes():
            raise ValueError('Live input guard changed during full gate')



def export_files(root=ROOT):
    """Read the explicit reviewed file list; never expand globs or follow symlinks."""
    from pathlib import PurePosixPath
    value = json.loads((root / 'tools/export_allowlist.json').read_text())
    if value.get('schema_version') != 1 or not isinstance(value.get('files'), list):
        raise ValueError('Invalid export allowlist')
    files = value['files']
    if not files or len(set(files)) != len(files):
        raise ValueError('Empty or duplicate export file list')
    for relative in files:
        if not isinstance(relative, str):
            raise ValueError('Invalid export path')
        path = PurePosixPath(relative)
        if (path.is_absolute() or str(path) != relative or '..' in path.parts or
                any('*' in part or '?' in part or '[' in part for part in path.parts) or
                any(part == '.git' or part == '.env' or part.startswith('.env.') for part in path.parts)):
            raise ValueError('Unsafe export path')
        source = root / relative
        if not source.is_file() or any(x.is_symlink() for x in (source, *source.parents)):
            raise ValueError('Missing file or symlink in export list')
    return files


def run_public(workers=None, physical_cores=None, output=None, node=None):
    """Exercise only reviewed source, with empty home/storage and blocked remote sockets."""
    import shutil
    import tempfile
    budget = resolve_budget(workers=workers, physical_cores=physical_cores)
    os.sched_setaffinity(0, budget.selected_ids)
    node = node or os.environ.get('PV_NODE') or shutil.which('node')
    if not node or not Path(node).is_file():
        raise ValueError('Node.js required; install it or set PV_NODE')
    verify_environment(node)
    if output is None:
        output = Path(tempfile.mkdtemp(prefix='pv-public-gate-'))
    else:
        output = Path(output).resolve()
        if output == ROOT or ROOT in output.parents:
            raise ValueError('Public evidence must be outside checkout')
        output.mkdir(parents=True, mode=0o700, exist_ok=False)
    print(f'Public gate evidence: {output}', flush=True)
    checkout = output / 'checkout'
    checkout.mkdir(mode=0o700)
    for relative in export_files():
        target = checkout / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    # Both scanners have zero exemptions. Private patterns never enter the copy.
    from privacy_check import Scanner, load_patterns
    patterns = []
    if os.environ.get('PV_PRIVATE_ROOT'):
        patterns = load_patterns(Path(os.environ['PV_PRIVATE_ROOT']) / 'audit/personal-patterns.txt')
    scanner = Scanner(patterns,json.loads((ROOT/'tools/export_allowlist.json').read_text()).get('assets',[]))
    for path in sorted(checkout.rglob('*')):
        if path.is_file():
            scanner.scan(path.read_bytes(), path.relative_to(checkout).as_posix())
    (output / 'privacy.json').write_text(json.dumps(dict(scanned=scanner.count, findings=scanner.findings), indent=2))
    if scanner.findings:
        raise ValueError('Unresolved export privacy findings; inspect external privacy.json')
    home = output / 'home'; home.mkdir(mode=0o700)
    temp = output / 'tmp'; temp.mkdir(mode=0o700)
    assets = output / 'synthetic'
    guard = output / 'offline'; guard.mkdir(mode=0o700)
    # audit hooks apply to all Python children, including real HTTP integration
    # tests. Loopback and Unix sockets remain available for local service tests.
    (guard / 'sitecustomize.py').write_text("""import sys, ipaddress

def local(host):
    if host in (None, '', 'localhost'):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False

def guard(event, args):
    if event == 'socket.connect':
        address = args[1]
        if isinstance(address, tuple) and not local(address[0]):
            raise PermissionError('Public tests are offline')
    elif event == 'socket.getaddrinfo' and not local(args[0]):
        raise PermissionError('Public tests are offline')
sys.addaudithook(guard)
""")
    (guard / 'offline.cjs').write_text("""const net = require('node:net');
const original = net.Socket.prototype.connect;
net.Socket.prototype.connect = function(...args) {
  let options = args[0];
  if (Array.isArray(options)) options = options[0];
  const host = typeof options === 'object' ? options.host : args[1];
  if (host && !['localhost','127.0.0.1','::1'].includes(host)) throw new Error('Public tests are offline');
  return original.apply(this,args);
};
""")
    env = {k:v for k,v in os.environ.items() if not k.startswith('PV_') and k not in
           ('PYTEST_ADDOPTS','PYTEST_PLUGINS','NODE_OPTIONS','PYTHONPATH')}
    env.update(THREAD_ENV)
    env.update(HOME=str(home), XDG_DATA_HOME=str(home/'data'), XDG_CACHE_HOME=str(home/'cache'),
        TMPDIR=str(temp), PYTEST_DEBUG_TEMPROOT=str(temp), PYTHONDONTWRITEBYTECODE='1',
        PYTHONPATH=os.pathsep.join((str(guard),str(checkout),str(checkout/'tests'))),
        PV_PYTHON=sys.executable, PV_NODE=str(node), PV_TEST_MODE='public', PV_TEST_WORKERS=str(budget.workers),
        PV_LEGACY_DATA_ROOT=str(assets/'legacy/data'), PV_DATA_ROOT=str(assets/'runtime'),
        NUMBA_CACHE_DIR=str(output/'numba'), PV_TEST_NUMBA_CACHE_ROOT=str(output/'numba-workers'),
        NODE_OPTIONS='--require='+str(guard/'offline.cjs'))
    generator = "import importlib.util; from pathlib import Path; p=Path('tests/fixtures/public_demo/generate.py'); s=importlib.util.spec_from_file_location('public_generate',p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); m.prepare_test_inputs(Path(__import__('sys').argv[1]))"
    python_tests = [sys.executable,'-m','pytest']
    if budget.workers > 1:
        python_tests += ['-n',str(budget.workers),'--dist=worksteal']
    python_tests += ['-p','no:cacheprovider','tests']
    node_tests = sorted(str(p.relative_to(checkout)) for p in (checkout/'tests').glob('test_*.js'))
    commands = [('generate',[sys.executable,'-c',generator,str(assets)]),
        ('python',python_tests), ('node',[str(node),'--test',f'--test-concurrency={budget.workers}',*node_tests]),
        ('acceptance',[sys.executable,'-c',"import test_support,runpy,sys; sys.argv=['verify.py','--mode','public']; runpy.run_path('verify.py',run_name='__main__')"]),
        ('public-reference',[sys.executable,'tests/fixtures/public_demo/generate.py','--compare-reference','tests/fixtures/public_demo/reference.json'])]
    records = []
    (output/'resource-budget.json').write_text(json.dumps(budget,indent=2))
    for name,argv in commands:
        started=time.monotonic()
        log=output/(name+'.log')
        with log.open('w') as stream:
            proc=subprocess.run(argv,cwd=checkout,env=env,stdout=stream,stderr=subprocess.STDOUT)
        import re
        skipped=bool(re.search(r'\b[1-9][0-9]* skipped\b|(?:#|ℹ) skipped [1-9][0-9]*\b',log.read_text(errors='replace')))
        records.append(dict(name=name,argv=argv,exit_code=proc.returncode,skipped=skipped,seconds=time.monotonic()-started,log=str(log)))
        (output/'commands.json').write_text(json.dumps(records,indent=2))
        print(f'{name}: exit {proc.returncode}',flush=True)
        if proc.returncode or skipped:
            return proc.returncode or 1
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', required=True, choices=('private','public'))
    parser.add_argument('--workers', type=int, default=None,
                        help='Test worker count (default: 2 * allocated physical cores; 1 = serial)')
    parser.add_argument('--physical-cores', type=int, default=None,
                        help='Physical CPU core count to allocate (default: available physical cores - 1)')
    parser.add_argument('--output', type=Path, help='New external public evidence directory')
    parser.add_argument('--node', help='Node executable for public tests')
    parser.add_argument('--check-environment', action='store_true', help='Verify installed versions without running suites')
    args = parser.parse_args()
    try:
        if args.check_environment:
            import shutil
            node = args.node or os.environ.get('PV_NODE') or shutil.which('node')
            if not node:
                raise ValueError('Node.js required; install it or set PV_NODE')
            verify_environment(node)
            print('Environment: reviewed dependency and runtime versions')
            return 0
        if args.mode == 'public':
            return run_public(args.workers,args.physical_cores,args.output,args.node)
        private = Path(os.environ['PV_PRIVATE_ROOT']).resolve(strict=True)
        return run_gate(private, workers=args.workers, physical_cores=args.physical_cores)
    except (OSError, ValueError, KeyError) as exc:
        print(f'Full gate blocked: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    os.umask(0o077)
    raise SystemExit(main())
