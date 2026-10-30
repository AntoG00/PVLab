"""Bounded spawn workers. Scientific state is immutable for each process lifetime."""
from collections import OrderedDict, deque
from dataclasses import dataclass
import hashlib
import html
import json
import multiprocessing as mp
import os
from pathlib import Path
import signal
import threading
import time

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


def _bounded_environment():
    # Spawn imports the main module before invoking its target. Set these in
    # the parent too, so native libraries see the budget on their first import.
    for name in ('NUMBA_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[name] = '1'


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


def _compute(operation, settings, options):
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


def _child(pipe, manifest_raw):
    """Only the server invokes this target; requests are bounded JSON, never pickles."""
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
                        with exported.open('rb') as stream:
                            os.fsync(stream.fileno())
                for path in (report, result_path, *(destination / 'result').iterdir()):
                    with path.open('rb') as stream:
                        os.fsync(stream.fileno())
                import resource
                pipe.send_bytes(_json({'ok': True, 'identity': identity,
                    'peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024}))
            except Exception:
                pipe.send_bytes(_json({'ok': False}))
    except Exception:
        pass
    finally:
        pipe.close()


def _rss(pid):
    try:
        for line in Path(f'/proc/{pid}/status').read_text().splitlines():
            if line.startswith('VmRSS:'):
                return int(line.split()[1]) * 1024
    except (FileNotFoundError, ProcessLookupError):
        return 0
    raise RuntimeError('Worker RSS watchdog unavailable')


class WorkerPool:
    def __init__(self, store, config=None):
        _bounded_environment()
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
        worker = self._workers.pop(key)
        process = worker['process']
        if process.is_alive():
            try:
                if os.getpgid(process.pid) == process.pid:
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
        process.join(timeout=2)
        worker['pipe'].close()
        process.close()

    def _fail(self, item):
        job = item['job']
        try:
            self.store.fail_job(job['tenant_id'], job['id'])
        except StoreError:
            pass

    def _tick(self):
        while self._queue and time.monotonic() - self._queue[0]['enqueued'] > self.config.deadline_seconds:
            self._fail(self._queue.popleft())
        for key, worker in list(self._workers.items()):
            item = worker['item']
            if not worker['process'].is_alive() or _rss(worker['process'].pid) > self.config.memory_bytes or (
                    item and time.monotonic() - worker['started'] > self.config.deadline_seconds):
                self._kill(key)
                if item:
                    self._fail(item)
                continue
            if item and worker['pipe'].poll():
                try:
                    result = json.loads(worker['pipe'].recv_bytes(65536))
                    if not result['ok']:
                        raise ValueError('Computation failed')
                    if result['peak_rss_bytes'] > self.config.memory_bytes:
                        raise ValueError('Worker exceeded memory limit')
                    job = item['job']
                    self.store.publish_result(job['tenant_id'], job['id'], result['identity'])
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
            parent, child = self._ctx.Pipe()
            process = self._ctx.Process(target=_child, args=(child, _json(manifest)), daemon=False)
            process.start()
            child.close()
            self._workers[key] = {'process': process, 'pipe': parent, 'item': None, 'started': 0}
        worker = self._workers[key]
        if worker['item'] is not None:
            return
        self._queue.popleft()
        try:
            self.store.start_job(job['tenant_id'], job['id'])
            destination = self.store.job_directory(job['tenant_id'], job['id'])
            worker['pipe'].send_bytes(_json(dict(operation=item['operation'], settings=item['settings'],
                options=item['options'], destination=str(destination), job_id=job['id'], scenario_hash=job['scenario_hash'])))
            worker.update(item=item, started=time.monotonic())
        except Exception:
            self._fail(item)
            self._kill(key)

    def _run(self):
        while not self._closed:
            with self._lock:
                try:
                    self._tick()
                except Exception:
                    # Fail every pending item if monitoring itself fails; never leave ghost jobs.
                    for item in self._queue:
                        self._fail(item)
                    self._queue.clear()
                    for key in list(self._workers):
                        item = self._workers[key]['item']
                        self._kill(key)
                        if item:
                            self._fail(item)
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
    _bounded_environment()
    import tempfile
    from feasibility.runtime_context import model_build_digest
    config = config or PoolConfig()
    context = mp.get_context('spawn')
    parent, child = context.Pipe()
    manifest = {'kind': 'local', 'revision': 'local', 'model_build_id': model_build_digest()}
    with tempfile.TemporaryDirectory(prefix='pv-local-worker-') as temp:
        process = context.Process(target=_child, args=(child, _json(manifest)))
        process.start()
        child.close()
        started = time.monotonic()
        try:
            request = _json(dict(operation=operation, settings=settings, options=options,
                destination=temp, job_id='local', scenario_hash=hashlib.sha256(_json(settings)).hexdigest()))
            if len(request) > 2 * 1024**2:
                raise ValueError('Local request too large')
            parent.send_bytes(request)
            while True:
                if not process.is_alive():
                    raise RuntimeError('Local worker exited before completion')
                resident = _rss(process.pid)
                if resident > config.memory_bytes:
                    raise RuntimeError(f'Local worker exceeded RSS limit: {resident} > {config.memory_bytes} bytes')
                if time.monotonic() - started > config.deadline_seconds:
                    raise RuntimeError('Local worker deadline exceeded')
                if parent.poll(.05):
                    result = json.loads(parent.recv_bytes(65536))
                    if not result['ok']:
                        raise RuntimeError('Local computation failed')
                    if result['peak_rss_bytes'] > config.memory_bytes:
                        raise RuntimeError('Local worker peak RSS exceeded memory limit')
                    print(f"Worker peak RSS bytes: {result['peak_rss_bytes']}", flush=True)
                    return result['identity']
        finally:
            if process.is_alive():
                try:
                    if os.getpgid(process.pid) == process.pid:
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    pass
            process.join(timeout=2)
            process.close()
            parent.close()
