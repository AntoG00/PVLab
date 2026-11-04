"""Bounded spawn workers. Scientific state is immutable for each process lifetime."""
from collections import OrderedDict, deque
from concurrent.futures import Future
import ctypes
from dataclasses import dataclass
import errno
from functools import cache
import hashlib
import html
import json
import multiprocessing as mp
import os
from pathlib import Path
import re
import signal
import sys
import threading
import time
import traceback

from feasibility.tenant_store import Conflict, StoreError

OPERATIONS = frozenset(('calibration', 'evaluate', 'quote', 'loan', 'dayhour', 'monthly',
    'forecast', 'forecast-monthly', 'sweep', 'fair-price', 'study', 'battery-sizing',
    'solar-sizing', 'sizing', 'matrix', 'sensitivity', 'summary-sensitivity',
    'solar-sizing-sensitivity', 'presentation-context', 'solar'))
LOCAL_OPERATIONS = frozenset(('private-reference', 'legacy-report'))


class Busy(StoreError):
    status = 429
    message = 'Compute capacity reached; retry later'


@dataclass(frozen=True)
class PoolConfig:
    workers: int = 2
    queue_capacity: int = 8
    deadline_seconds: float = 900
    memory_bytes: int = 2 * 1024**3

    def __post_init__(self):
        for name, ceiling in (('workers', 2), ('queue_capacity', 8),
                              ('deadline_seconds', 900), ('memory_bytes', 2 * 1024**3)):
            value = getattr(self, name)
            if type(value) not in (int, float) or not 0 < value <= ceiling:
                raise ValueError('Invalid positive bounded pool limit')
        if type(self.workers) is not int or type(self.queue_capacity) is not int:
            raise ValueError('Worker/queue counts must be integers')


def _json(value):
    return json.dumps(value, allow_nan=False, separators=(',', ':')).encode()


_THREAD_ENV = ('NUMBA_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS',
               'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS')
_SPAWN_ENV_LOCK = threading.Lock()


def _bounded_environment():
    for name in _THREAD_ENV:
        os.environ[name] = '1'


def _start_process(process):
    # Spawn imports the main module before invoking the target. Only the
    # child's inherited environment should retain the worker thread budget.
    with _SPAWN_ENV_LOCK:
        previous = {name: os.environ.get(name) for name in _THREAD_ENV}
        _bounded_environment()
        try:
            process.start()
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def render_owned_result(result, identity):
    """Escape every user-controlled label and filename in an owned HTML result."""
    raw = json.dumps({'identity': identity, 'disclosures': result.get('_presentation') if isinstance(result, dict) else None, 'result': result}, default=lambda v: v.tolist(),
                     ensure_ascii=False, allow_nan=False)
    return ('<!doctype html><meta charset="utf-8"><title>Simulation result</title><pre>' +
            html.escape(raw) + '</pre>').encode('utf-8')


def _profile_presentation(value):
    """Hide legacy controls/provenance in v2 output; calculations already finished."""
    obsolete = {'export_price_profile', 'export_price_window', 'dormer_south_proxy'}
    def walk(node):
        if isinstance(node, dict):
            for key, item in list(node.items()):
                if key in ('settings', 'selected_config') and isinstance(item, dict):
                    for name in list(item):
                        if name in obsolete or name.endswith('_extra_cost_per_panel'):
                            item.pop(name, None)
                elif key in ('assumptions', 'records') and isinstance(item, list):
                    node[key] = [record for record in item if record.get('name') not in obsolete
                                 and not record.get('name', '').endswith('_extra_cost_per_panel')]
                    for record in node[key]:
                        record.update(source='Explicit profile or proposed model input',
                            description='Model assumption; verify against the selected household and equipment.',
                            reasoning='Model assumption; requires explicit confirmation.')
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(value)


def _bind(manifest):
    from feasibility.runtime_context import RuntimeContext, initialize_runtime, model_build_digest
    if manifest['model_build_id'] != model_build_digest():
        raise ValueError('Model build changed')
    if manifest['kind'] == 'legacy':
        from feasibility.legacy_profile import load_legacy_profile
        ctx = load_legacy_profile(manifest['root'], manifest['legacy_revision'])
        from dataclasses import replace
        ctx = replace(ctx, owner_id=manifest['tenant_id'], input_revision=manifest['revision'])
    else:
        from feasibility.weather_dataset import verify_and_load_dataset
        from feasibility.runtime_context import resolve_profile_prices
        from feasibility.thermal_baseline import resolve_thermal_baseline
        from feasibility import assumptions
        candidate = Path(manifest['candidate'])
        dataset = verify_and_load_dataset(candidate / 'hourly.npz', candidate / 'audit.json',
            candidate / 'manifest.json', manifest['dataset_digest'])
        profile = manifest['profile']
        defaults = {k: row[0] for k, row in assumptions.SPEC.items()}
        defaults.update(profile['default_settings'])
        defaults.update(manifest['defaults'])
        defaults.pop('baseline_cooling_kwh', None)  # Profile/audit input, never a scenario setting.
        records = [{'name': k, 'value': defaults[k], 'unit': row[1], 'bounds': row[2],
                    'plausible_range': row[2], 'group': row[3], 'description': row[4],
                    'reasoning': row[4], 'source': 'Profile input / packaged model assumption',
                    'status': 'proposed', 'confidence': 'Pending confirmation',
                    'owner': 'user', 'date_accessed': None}
                   for k, row in assumptions.SPEC.items()]
        metadata = {'dataset_manifest': json.loads((candidate / 'manifest.json').read_bytes()),
                    'compiler_version': 'dataset-compiler-v2'}
        if 'solar_source' in dataset['audit']:
            source = json.loads((candidate / 'solar-source.json').read_bytes())
            metadata['solar_parameters'] = [{'plane_id': row.get('plane_id'), 'year': row.get('year'),
                'system_efficiency': row.get('request_parameters', {}).get('system_efficiency')}
                for row in source.get('solar', [])]
        ctx = RuntimeContext(owner_id=manifest['tenant_id'], input_revision=manifest['revision'],
            dataset_digest=manifest['dataset_digest'], price_revision=profile['market']['price_revision'],
            defaults_snapshot=defaults, records_snapshot=records,
            thermal_reference=resolve_thermal_baseline(profile), fiscal_revision=profile['fiscal']['rule_revision'],
            calendar_version='utc_hourly_v2', dataset=dataset,
            weather_identity=('owned-v2', manifest['dataset_digest']), metadata=metadata,
            household_profile=profile, price_data=resolve_profile_prices(profile, manifest.get('catalog_root')))
    return initialize_runtime(ctx)


def _clean_scenario(settings, options):
    if isinstance(settings, dict) and 'baseline_cooling_kwh' in settings:
        settings = dict(settings)
        settings.pop('baseline_cooling_kwh', None)
    if isinstance(options, dict) and isinstance(options.get('quote'), dict):
        ref = options['quote'].get('reference_settings')
        if isinstance(ref, dict) and 'baseline_cooling_kwh' in ref:
            ref = dict(ref)
            ref.pop('baseline_cooling_kwh', None)
            options = dict(options, quote=dict(options['quote'], reference_settings=ref))
    return settings, options


def _compute(operation, settings, options):
    settings, options = _clean_scenario(settings, options)
    if operation == 'presentation-context':
        from feasibility.runtime_context import current_runtime, mutable_copy
        from feasibility.fiscal import FISCAL_RATES, CONFIRMATIONS
        ctx = current_runtime()
        if ctx.household_profile is None:
            raise ValueError('Presentation context requires a household profile')
        values = mutable_copy(ctx.defaults_snapshot)
        from feasibility.engine import LEGACY_ROOF_KEYS
        for key in LEGACY_ROOF_KEYS:
            values.pop(key, None)
        values.update({k: v for k, v in ctx.household_profile['fiscal']['parameters'].items() if k in FISCAL_RATES})
        values['roof_panels'] = {r['id']: 0 for r in ctx.household_profile['roofs']}
        values.update(battery_kwh=0, battery_modules=0, battery_secondary_modules=0, pmg_election=False)
        values.update({key: False for key in CONFIRMATIONS})
        records = [r for r in mutable_copy(ctx.records_snapshot) if r['name'] in values]
        records.extend(dict(name=key, value=False, unit='boolean', bounds=None, group='tax',
                            status='proposed', confidence='Requires confirmation', owner='user') for key in CONFIRMATIONS)
        return {'settings': values, 'records': records}
    from feasibility import engine
    if operation == 'calibration':
        return engine.calibration()
    if operation == 'evaluate':
        return engine.evaluate(settings)
    if operation == 'quote':
        return engine.quote_evaluate(settings, options.get('quote'))
    if operation == 'loan':
        return engine.solve_loan_amount(settings, options.get('quote'))
    if operation == 'dayhour':
        return engine.dayhour(settings, options.get('weather_year'), options.get('date'), options.get('hour'))
    if operation == 'monthly':
        return engine.dayhour_monthly(settings, options.get('weather_year'), options.get('month'), options.get('hour'))
    if operation == 'forecast':
        return engine.dayhour_forecast(settings, options.get('date'), options.get('hour'),
            options.get('scenario'), options.get('representative', 'median'))
    if operation == 'forecast-monthly':
        return engine.dayhour_forecast_monthly(settings, options.get('month'), options.get('hour'),
            options.get('scenario'), options.get('representative', 'median'))
    if operation == 'sweep':
        return engine.sweep(settings)
    if operation == 'fair-price':
        return engine.fair_price(settings, options.get('metric', 'npv'), options.get('target', 0))
    if operation == 'study':
        return engine.study(settings)
    if operation == 'battery-sizing':
        return engine.battery_sizing(settings, options.get('options'), options.get('quote'))
    if operation == 'solar-sizing':
        return engine.solar_sizing(settings, options.get('options'), options.get('quote'))
    if operation == 'solar-sizing-sensitivity':
        return engine.solar_sizing_sensitivity(settings, options.get('options'), options.get('quote'), options.get('sensitivity_options'))
    if operation == 'sizing':
        return engine.optimize_configuration(settings, options.get('search'))
    if operation == 'matrix':
        return engine.dispatch_matrix(settings, options.get('mode', 'forecast_2027'), options.get('scenario'),
            options.get('representative', 'median'))
    if operation == 'summary-sensitivity':
        return engine.summary_sensitivity(settings, options.get('quote'), options.get('scope', 'solar'), options.get('cash_mode', 'cash'))
    if operation == 'sensitivity':
        return engine.sensitivity(settings)
    raise ValueError('Unknown operation')


def get_package_version():
    """Derive package version from installed distribution metadata or pyproject.toml."""
    try:
        import importlib.metadata
        return importlib.metadata.version('pv-lab')
    except Exception:
        pass
    try:
        pyproject = Path(__file__).resolve().parents[1] / 'pyproject.toml'
        if pyproject.is_file():
            match = re.search(r'version\s*=\s*["\']([^"\']+)["\']', pyproject.read_text(encoding='utf-8'))
            if match:
                return match.group(1)
    except Exception:
        pass
    return 'unknown'


def _sanitize_traceback(exc, max_frames=12, max_frame_len=120):
    """Extract safe repo-relative traceback frames, omitting all external paths and variables."""
    frames = []
    try:
        tb = exc.__traceback__
        repo_root = Path(__file__).resolve().parents[1]
        for frame, lineno in traceback.walk_tb(tb):
            filename = Path(frame.f_code.co_filename).resolve()
            func_name = frame.f_code.co_name
            try:
                rel_path = filename.relative_to(repo_root).as_posix()
            except ValueError:
                continue
            if rel_path.startswith(('feasibility/', 'tools/', 'tests/')) and '..' not in rel_path:
                func = re.sub(r'[^a-zA-Z0-9_<>]', '', func_name)[:40]
                entry = f"{rel_path}:{lineno} in {func}"
                frames.append(entry[:max_frame_len])
    except Exception:
        pass
    return frames[-max_frames:]


def _child(pipe, manifest_raw):
    """Only the server invokes this target; requests are bounded JSON, never pickles."""
    if os.name != 'nt':
        os.setsid()
    _bounded_environment()
    from numba import set_num_threads
    set_num_threads(1)
    manifest = json.loads(manifest_raw)
    ctx = None
    try:
        from feasibility.runtime_context import model_build_digest
        if manifest['model_build_id'] != model_build_digest():
            raise ValueError('Model build changed')
        if manifest['kind'] not in ('solar', 'local'):
            ctx = _bind(manifest)
        while True:
            raw = pipe.recv_bytes(2 * 1024**2)
            request = json.loads(raw)
            operation = request['operation']
            allowed = OPERATIONS | LOCAL_OPERATIONS if manifest['kind'] == 'local' else OPERATIONS
            if operation not in allowed:
                raise ValueError('Unknown operation')
            try:
                if operation in LOCAL_OPERATIONS:
                    if manifest['kind'] != 'local':
                        raise ValueError('Local operation requires local entry point')
                    options = request['options']
                    if operation == 'private-reference':
                        from tools.regression import worker
                        worker(options['manifest'], options['output'])
                        value = {'status': 'complete'}
                    else:
                        from feasibility.legacy_profile import bootstrap_legacy
                        from feasibility import engine
                        bootstrap_legacy(root=options['profile_root'])
                        value = engine.study(request['settings'])
                        from feasibility.report import write_reports
                        write_reports(value, dest=options['report_destination'])
                        value = {'status': 'complete'}
                    from feasibility.runtime_context import current_runtime
                    ctx = current_runtime()
                elif operation == 'solar':
                    if manifest['kind'] != 'solar':
                        raise ValueError('Wrong worker kind')
                    from feasibility.solar_candidate import build_candidate
                    result = build_candidate(manifest['source_manifest'], Path(request['destination']) / 'candidate')
                    value = {'dataset_digest': result.digest, 'candidate_job_id': request['job_id']}
                else:
                    if ctx is None:
                        raise ValueError('Unbound computation')
                    value = _compute(operation, request['settings'], request['options'])
                if ctx is not None and ctx.household_profile is not None and isinstance(value, dict):
                    from feasibility.report import profile_disclosures
                    from feasibility.runtime_context import mutable_copy
                    _profile_presentation(value)
                    value['_presentation'] = profile_disclosures(mutable_copy(ctx.household_profile),
                        mutable_copy(ctx.dataset['audit']), ctx.input_revision, ctx.dataset['years'].tolist(), mutable_copy(ctx.metadata.get('solar_parameters')))
                # Large arrays/results stay in private files, not IPC or SQLite.
                destination = Path(request['destination'])
                destination.mkdir(mode=0o700, parents=True, exist_ok=True)
                from tools.regression import write_outputs
                write_outputs(value, destination / 'result')
                result_raw = json.dumps(value, default=lambda v: v.tolist(), ensure_ascii=False, allow_nan=False).encode()
                result_path = destination / 'result.json'
                with result_path.open('xb') as stream:
                    os.chmod(result_path, 0o600)
                    stream.write(result_raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                identity = {'input_revision': ctx.input_revision if ctx else manifest['revision'], 'model_build_id': manifest['model_build_id'],
                    'scenario_hash': request['scenario_hash']}
                if ctx is not None:
                    identity.update(dataset_digest=ctx.dataset_digest, price_revision=ctx.price_revision,
                        fiscal_revision=ctx.fiscal_revision, calendar_version=ctx.calendar_version,
                        compiler_version=ctx.metadata.get('compiler_version'))
                report = destination / 'report.html'
                report.write_bytes(render_owned_result(value, identity))
                os.chmod(report, 0o600)
                if operation not in LOCAL_OPERATIONS:
                    from feasibility.report import owned_report_text, owned_report_csv
                    presentation = value.get('_presentation') if isinstance(value, dict) else None
                    for name, text in (('report.md', owned_report_text(value, identity, presentation)),
                                       ('report.csv', owned_report_csv(value, identity, presentation))):
                        exported = destination / name
                        exported.write_text(text, encoding='utf-8')
                        os.chmod(exported, 0o600)
                        with exported.open('r+b' if os.name == 'nt' else 'rb') as stream:
                            os.fsync(stream.fileno())
                for path in (report, result_path, *(destination / 'result').iterdir()):
                    with path.open('r+b' if os.name == 'nt' else 'rb') as stream:
                        os.fsync(stream.fileno())
                pipe.send_bytes(_json({'ok': True, 'identity': identity,
                    'peak_rss_bytes': _peak_rss()}))
            except Exception as exc:
                sanitized_tb = _sanitize_traceback(exc)
                err_code = 'COMPUTE_EXCEPTION'
                tb = exc.__traceback__
                last_func = None
                while tb is not None:
                    last_func = tb.tb_frame.f_code.co_name
                    tb = tb.tb_next
                if (
                    isinstance(exc, ValueError)
                    and str(exc) == 'missing month/hour shape cell; explicit synthetic completion required'
                    and last_func == 'project_load'
                ):
                    err_code = 'SYNTHETIC_COMPLETION_REQUIRED'
                pipe.send_bytes(_json({
                    'ok': False,
                    'failure_type': 'EXCEPTION',
                    'error_code': err_code,
                    'error_name': type(exc).__name__,
                    'traceback': sanitized_tb
                }))
    except Exception:
        pass
    finally:
        pipe.close()


def _maxrss_to_bytes(ru_maxrss, platform=None):
    plat = sys.platform if platform is None else platform
    if plat == 'darwin':
        return int(ru_maxrss)
    return int(ru_maxrss) * 1024


class _RusageInfoV0(ctypes.Structure):
    # Darwin's struct rusage_info_v0 from <sys/resource.h>.
    _fields_ = [
        ('ri_uuid', ctypes.c_uint8 * 16),
        ('ri_user_time', ctypes.c_uint64),
        ('ri_system_time', ctypes.c_uint64),
        ('ri_pkg_idle_wkups', ctypes.c_uint64),
        ('ri_interrupt_wkups', ctypes.c_uint64),
        ('ri_pageins', ctypes.c_uint64),
        ('ri_wired_size', ctypes.c_uint64),
        ('ri_resident_size', ctypes.c_uint64),
        ('ri_phys_footprint', ctypes.c_uint64),
        ('ri_proc_start_abstime', ctypes.c_uint64),
        ('ri_proc_exit_abstime', ctypes.c_uint64),
    ]


@cache
def _get_libproc():
    lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    lib.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    lib.proc_pid_rusage.restype = ctypes.c_int
    return lib


def _darwin_rss(pid):
    libproc = _get_libproc()
    info = _RusageInfoV0()
    ret = libproc.proc_pid_rusage(pid, 0, ctypes.byref(info))  # RUSAGE_INFO_V0
    if ret == 0:
        return int(info.ri_resident_size)
    err = ctypes.get_errno()
    if err == errno.ESRCH:
        return 0
    raise RuntimeError('Worker RSS watchdog unavailable')


class _MemoryCounters(ctypes.Structure):
    _fields_ = [('size', ctypes.c_uint32), ('faults', ctypes.c_uint32),
                ('peak', ctypes.c_size_t), ('resident', ctypes.c_size_t),
                ('other', ctypes.c_size_t * 6)]


class _JobLimits(ctypes.Structure):
    class _Basic(ctypes.Structure):
        _fields_ = [('time_limits', ctypes.c_int64 * 2), ('flags', ctypes.c_uint32),
                    ('working_set', ctypes.c_size_t * 2), ('active_processes', ctypes.c_uint32),
                    ('affinity', ctypes.c_size_t), ('priority', ctypes.c_uint32),
                    ('scheduling', ctypes.c_uint32)]

    _fields_ = [('basic', _Basic), ('io_counters', ctypes.c_uint64 * 6),
                ('memory_limits', ctypes.c_size_t * 4)]


@cache
def _windows_process_api():
    from ctypes.wintypes import BOOL, DWORD, HANDLE
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    for name, result, args in (
        ('GetCurrentProcess', HANDLE, ()),
        ('K32GetProcessMemoryInfo', BOOL, (HANDLE, ctypes.POINTER(_MemoryCounters), DWORD)),
        ('CreateJobObjectW', HANDLE, (ctypes.c_void_p, ctypes.c_wchar_p)),
        ('SetInformationJobObject', BOOL, (HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD)),
        ('AssignProcessToJobObject', BOOL, (HANDLE, HANDLE)),
        ('CloseHandle', BOOL, (HANDLE,)),
    ):
        func = getattr(api, name)
        func.restype, func.argtypes = result, args
    return api


def _windows_memory(handle):
    counters = _MemoryCounters()
    counters.size = ctypes.sizeof(counters)
    if not _windows_process_api().K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.size):
        raise ctypes.WinError(ctypes.get_last_error())
    return counters


def _peak_rss():
    if os.name == 'nt':
        return _windows_memory(_windows_process_api().GetCurrentProcess()).peak
    import resource
    return _maxrss_to_bytes(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


def _worker_rss(process):
    if os.name != 'nt':
        return _rss(process.pid)
    try:
        # On Windows the multiprocessing sentinel is an open process handle.
        return _windows_memory(process.sentinel).resident
    except OSError:
        if not process.is_alive():
            return 0
        raise


def _windows_job(process):
    api = _windows_process_api()
    handle = api.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = _JobLimits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if (not api.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))
            or not api.AssignProcessToJobObject(handle, process.sentinel)):
        error = ctypes.WinError(ctypes.get_last_error())
        api.CloseHandle(handle)
        raise error
    return handle


def _send_request(pipe, request):
    if os.name != 'nt':
        pipe.send_bytes(request)
        return None

    # Windows named-pipe writes can wait for the reader to drain the buffer.
    # Keep the watchdog and cancellation responsive during worker startup.
    sent = Future()

    def send():
        try:
            pipe.send_bytes(request)
        except Exception as exc:
            sent.set_exception(exc)
        else:
            sent.set_result(None)

    thread = threading.Thread(target=send, name='pv-worker-send', daemon=True)
    thread.start()
    return sent, thread


def _request_sent(sender):
    if sender is None:
        return True
    sent, _ = sender
    if not sent.done():
        return False
    sent.result()
    return True


def _stop_worker(process, pipe, job_handle, sender=None):
    if job_handle is not None:
        if not _windows_process_api().CloseHandle(job_handle):
            raise ctypes.WinError(ctypes.get_last_error())
    elif process.is_alive():
        try:
            if os.name != 'nt' and os.getpgid(process.pid) == process.pid:
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
    # Reap before the caller marks the job terminal or removes its files.
    process.join()
    if sender is not None:
        sender[1].join()
    pipe.close()
    process.close()


def _spawn_worker(context, manifest):
    parent, child = context.Pipe()
    process = context.Process(target=_child, args=(child, _json(manifest)), daemon=False)
    try:
        _start_process(process)
        # No request is sent until the worker and its future descendants are contained.
        job_handle = _windows_job(process) if os.name == 'nt' else None
    except BaseException:
        if process.pid is None:
            parent.close()
            process.close()
        else:
            _stop_worker(process, parent, None)
        raise
    finally:
        child.close()
    return process, parent, job_handle


def _rss(pid):
    if sys.platform == 'darwin':
        return _darwin_rss(pid)
    try:
        for line in Path(f'/proc/{pid}/status').read_text().splitlines():
            if line.startswith('VmRSS:'):
                return int(line.split()[1]) * 1024
    except (FileNotFoundError, ProcessLookupError):
        return 0
    raise RuntimeError('Worker RSS watchdog unavailable')


class WorkerPool:
    def __init__(self, store, config=None):
        self.store, self.config = store, config or PoolConfig()
        self._ctx = mp.get_context('spawn')
        self._queue, self._workers = deque(), OrderedDict()
        self._lock, self._closed = threading.RLock(), False
        self._accepting = True
        self.store.recover_jobs()
        self.store.worker_shutdown = self.shutdown_tenant
        self._thread = threading.Thread(target=self._run, name='pv-worker-monitor', daemon=True)
        self._thread.start()

    def submit(self, job, manifest, operation, settings, options):
        if operation not in OPERATIONS or not isinstance(settings, dict) or not isinstance(options, dict):
            raise StoreError()
        settings, options = _clean_scenario(settings, options)
        # Freeze before enqueue; no caller can mutate a queued request.
        item = json.loads(_json({'job': job, 'manifest': manifest, 'operation': operation,
                                 'settings': settings, 'options': options}))
        item['enqueued'] = time.monotonic()
        with self._lock:
            busy = [x['job']['tenant_id'] for x in self._queue]
            busy += [w['item']['job']['tenant_id'] for w in self._workers.values() if w['item']]
            if self._closed or not self._accepting or len(self._queue) >= self.config.queue_capacity or job['tenant_id'] in busy:
                raise Busy()
            self._queue.append(item)

    def _kill(self, key):
        worker = self._workers[key]
        _stop_worker(worker['process'], worker['pipe'], worker['job_handle'], worker.get('sender'))
        del self._workers[key]

    def _fail(self, item, failure_type='EXCEPTION', error_code='COMPUTE_EXCEPTION', error_name='ComputationError', traceback=None):
        job = item['job']
        try:
            from feasibility.runtime_context import model_build_digest
            failure_info = {
                'job_id': job['id'],
                'operation': item.get('operation', 'compute'),
                'failure_type': failure_type,
                'error_code': error_code,
                'error_name': error_name,
                'model_build_id': item.get('manifest', {}).get('model_build_id') or model_build_digest(),
                'version': get_package_version(),
                'traceback': traceback or [],
                'timestamp': time.time(),
            }
            self.store.fail_job(job['tenant_id'], job['id'], failure_info=failure_info)
        except StoreError:
            pass

    def _tick(self):
        while self._queue and time.monotonic() - self._queue[0]['enqueued'] > self.config.deadline_seconds:
            self._fail(self._queue.popleft(), failure_type='QUEUE_TIMEOUT', error_code='QUEUE_TIMEOUT', error_name='QueueTimeout')
        for key, worker in list(self._workers.items()):
            item = worker['item']
            alive = worker['process'].is_alive()
            over_memory = alive and _worker_rss(worker['process']) > self.config.memory_bytes
            if not alive or over_memory or (
                    item and time.monotonic() - worker['started'] > self.config.deadline_seconds):
                if not worker['process'].is_alive():
                    failure_type, error_code, error_name = 'PROCESS_EXIT', 'PROCESS_EXIT', 'WorkerProcessDied'
                elif over_memory:
                    failure_type, error_code, error_name = 'MEMORY', 'COMPUTE_MEMORY', 'WorkerMemoryExceeded'
                else:
                    failure_type, error_code, error_name = 'TIMEOUT', 'COMPUTE_TIMEOUT', 'WorkerDeadlineExceeded'
                self._kill(key)
                if item:
                    self._fail(item, failure_type=failure_type, error_code=error_code, error_name=error_name)
                continue
            try:
                ready = item and _request_sent(worker.get('sender')) and worker['pipe'].poll()
            except (EOFError, OSError):
                self._kill(key)
                self._fail(item, failure_type='PROCESS_EXIT', error_code='PROCESS_EXIT', error_name='WorkerProcessDied')
                continue
            if ready:
                try:
                    result = json.loads(worker['pipe'].recv_bytes(65536))
                    if not result['ok']:
                        failure_type = result.get('failure_type', 'EXCEPTION')
                        error_code = result.get('error_code', 'COMPUTE_EXCEPTION')
                        error_name = result.get('error_name', 'ComputationError')
                        tb = result.get('traceback', [])
                        self._fail(item, failure_type=failure_type, error_code=error_code, error_name=error_name, traceback=tb)
                        worker['item'] = None
                        self._workers.move_to_end(key)
                        continue
                    if result['peak_rss_bytes'] > self.config.memory_bytes:
                        self._kill(key)
                        self._fail(item, failure_type='MEMORY', error_code='COMPUTE_MEMORY', error_name='WorkerMemoryExceeded')
                        continue
                    if time.monotonic() - worker['started'] > self.config.deadline_seconds:
                        self._kill(key)
                        self._fail(item, failure_type='TIMEOUT', error_code='COMPUTE_TIMEOUT', error_name='WorkerDeadlineExceeded')
                        continue
                    job = item['job']
                    self.store.publish_result(job['tenant_id'], job['id'], result['identity'])
                except (EOFError, BrokenPipeError):
                    self._kill(key)
                    self._fail(item, failure_type='PROCESS_EXIT', error_code='PROCESS_EXIT', error_name='WorkerProcessDied')
                    continue
                except Exception:
                    self._fail(item)
                worker['item'] = None
                self._workers.move_to_end(key)
        if not self._queue:
            return
        item = self._queue[0]
        job, manifest = item['job'], item['manifest']
        key = (job['tenant_id'], job['profile_revision'], manifest['model_build_id'])
        if key not in self._workers:
            if len(self._workers) >= self.config.workers:
                idle = next((k for k, w in self._workers.items() if w['item'] is None), None)
                if idle is None:
                    return
                self._kill(idle)
            process, parent, job_handle = _spawn_worker(self._ctx, manifest)
            self._workers[key] = {'process': process, 'pipe': parent, 'job_handle': job_handle,
                                  'item': None, 'started': 0}
        worker = self._workers[key]
        if worker['item'] is not None:
            return
        self._queue.popleft()
        try:
            self.store.start_job(job['tenant_id'], job['id'])
            destination = self.store.job_directory(job['tenant_id'], job['id'])
            started = time.monotonic()
            sender = _send_request(worker['pipe'], _json(dict(operation=item['operation'], settings=item['settings'],
                options=item['options'], destination=str(destination), job_id=job['id'], scenario_hash=job['scenario_hash'])))
            worker.update(item=item, started=started, sender=sender)
        except Exception:
            self._kill(key)
            self._fail(item)

    def _run(self):
        while not self._closed:
            with self._lock:
                try:
                    self._tick()
                except Exception:
                    # Fail every pending item if monitoring itself fails; never leave ghost jobs.
                    for item in self._queue:
                        self._fail(item, failure_type='PROCESS_EXIT', error_code='PROCESS_EXIT', error_name='MonitorFailure')
                    self._queue.clear()
                    for key in list(self._workers):
                        item = self._workers[key]['item']
                        self._kill(key)
                        if item:
                            self._fail(item, failure_type='PROCESS_EXIT', error_code='PROCESS_EXIT', error_name='MonitorFailure')
            time.sleep(0.05)

    def cancel(self, tenant_id, job_id):
        self.store.job(tenant_id, job_id)
        with self._lock:
            self._queue = deque(x for x in self._queue if x['job']['id'] != job_id)
            for key, worker in list(self._workers.items()):
                if worker['item'] and worker['item']['job']['id'] == job_id:
                    self._kill(key)
            self.store.cancel_job(tenant_id, job_id)

    def shutdown_tenant(self, tenant_id):
        with self._lock:
            self._queue = deque(x for x in self._queue if x['job']['tenant_id'] != tenant_id)
            for key in list(self._workers):
                if key[0] == tenant_id:
                    self._kill(key)

    def has_active(self):
        with self._lock:
            return bool(self._queue or any(w['item'] for w in self._workers.values()))

    def prepare_restart(self):
        with self._lock:
            if self.has_active():
                return False
            self._accepting = False
            return True

    def close(self):
        with self._lock:
            self._closed = True
            for item in self._queue:
                self._fail(item)
            self._queue.clear()
            for key in list(self._workers):
                item = self._workers[key]['item']
                self._kill(key)
                if item:
                    self._fail(item)
        self._thread.join(timeout=3)


def run_local(operation, settings, options, *, config=None):
    """CLI/report and frozen-reference entry point, using the same child/watchdog."""
    if operation not in LOCAL_OPERATIONS:
        raise ValueError('Unsupported local operation')
    import tempfile
    from feasibility.runtime_context import model_build_digest
    config = config or PoolConfig()
    context = mp.get_context('spawn')
    manifest = {'kind': 'local', 'revision': 'local', 'model_build_id': model_build_digest()}
    with tempfile.TemporaryDirectory(prefix='pv-local-worker-') as temp:
        process, parent, job_handle = _spawn_worker(context, manifest)
        started = time.monotonic()
        sender = None
        try:
            request = _json(dict(operation=operation, settings=settings, options=options,
                destination=temp, job_id='local', scenario_hash=hashlib.sha256(_json(settings)).hexdigest()))
            if len(request) > 2 * 1024**2:
                raise ValueError('Local request too large')
            sender = _send_request(parent, request)
            while True:
                if not process.is_alive():
                    raise RuntimeError('Local worker exited before completion')
                resident = _worker_rss(process)
                if resident > config.memory_bytes:
                    raise RuntimeError(f'Local worker exceeded RSS limit: {resident} > {config.memory_bytes} bytes')
                if time.monotonic() - started > config.deadline_seconds:
                    raise RuntimeError('Local worker deadline exceeded')
                if not _request_sent(sender):
                    time.sleep(.05)
                    continue
                if parent.poll(.05):
                    result = json.loads(parent.recv_bytes(65536))
                    if not result['ok']:
                        raise RuntimeError('Local computation failed')
                    if result['peak_rss_bytes'] > config.memory_bytes:
                        raise RuntimeError('Local worker peak RSS exceeded memory limit')
                    if time.monotonic() - started > config.deadline_seconds:
                        raise RuntimeError('Local worker deadline exceeded')
                    print(f"Worker peak RSS bytes: {result['peak_rss_bytes']}", flush=True)
                    return result['identity']
        finally:
            _stop_worker(process, parent, job_handle, sender)
