"""Transport-independent owned LAN routes. No scientific computation in parent threads."""
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import hmac
import html
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit, unquote

from feasibility.profiles import canonical_json, strict_json, parse_profile, profile_to_dict
from feasibility.sessions import Sessions, Forbidden, guard_host, guard_mutation
from feasibility.tenant_store import StoreError, NotFound, Conflict, TenantState, TenantDefaults
from feasibility.worker_pool import Busy, OPERATIONS


class TooLarge(StoreError):
    status = 413
    message = 'Request limit exceeded'


class InvalidDomain(StoreError):
    status = 422
    message = 'Input validation failed'


@dataclass
class Response:
    status: int = 200
    body: object = field(default_factory=dict)
    headers: dict = field(default_factory=dict)


ALIASES = {'evaluate': 'quote', 'loan-solve': 'loan', 'dayhour': 'dayhour',
    'fair-price': 'fair-price', 'sweep': 'sweep', 'optimize-configurations': 'sizing',
    'battery-sizing': 'battery-sizing', 'solar-sizing': 'solar-sizing',
    'solar-sizing-sensitivity': 'solar-sizing-sensitivity', 'sensitivity': 'sensitivity',
    'summary-sensitivity': 'summary-sensitivity', 'behavior-matrix': 'matrix', 'study': 'study'}


class Service:
    def __init__(self, config, store, pool):
        self.config, self.store, self.pool = dict(config), store, pool
        self.sessions = Sessions(store)
        self.hosts = tuple(config['allowed_hosts'])
        if not self.hosts or '*' in self.hosts or config.get('internet_hosted'):
            raise ValueError('Explicit local/LAN hosts required; internet hosting is unsupported')
        self.limits = dict(json_bytes=2 * 1024**2, upload_bytes=16 * 1024**2,
                           requests_per_minute=60, uploads_per_hour=10,
                           ip_per_minute=120, sessions_per_hour=10)
        for name, value in config.get('limits', {}).items():
            if name not in self.limits or type(value) is not int or not 0 < value <= self.limits[name]:
                raise ValueError('Limits must be positive and bounded')
            self.limits[name] = value
        self._csrf_key = secrets.token_bytes(32)
        self._rates, self._lock = {}, threading.Lock()
        self.store.worker_shutdown = pool.shutdown_tenant

    def _rate(self, key, maximum, window):
        now = time.monotonic()
        with self._lock:
            if key not in self._rates:
                self._rates = {k: v for k, v in self._rates.items() if v and v[-1] > now - 3600}
                if len(self._rates) >= 10000:
                    raise Busy()
                self._rates[key] = deque()
            times = self._rates[key]
            while times and times[0] <= now - window:
                times.popleft()
            if len(times) >= maximum:
                raise Busy()
            times.append(now)

    def _csrf(self, headers):
        token = self.sessions._token(headers)
        return hmac.new(self._csrf_key, token.encode(), hashlib.sha256).hexdigest()

    def _hosts_for(self, local_authority):
        return (*self.hosts, local_authority) if local_authority and self.config.get('socket_destination') else self.hosts

    def check_transport(self, headers, client_ip, local_authority=None):
        guard_host(headers, self._hosts_for(local_authority))
        self._rate(('ip', client_ip), self.limits['ip_per_minute'], 60)

    def _object(self, raw):
        if len(raw) > self.limits['json_bytes']:
            raise TooLarge()
        body = strict_json(raw)
        if not isinstance(body, dict):
            raise StoreError()
        return body

    def _document(self, owner, snap):
        if not snap['manifest_ref']:
            return {'profile': None, 'candidate': None, 'consumption': None}
        item = self.store.upload(owner, snap['manifest_ref'])
        if item['metadata'].get('purpose') != 'profile_manifest':
            raise Conflict()
        return strict_json(self.store.read_upload(owner, snap['manifest_ref']))

    def _publish(self, owner, profile_id, revision, document, defaults=None):
        item, _ = self.store.put_upload(owner, canonical_json(document), {'purpose': 'profile_manifest'})
        return self.store.publish(owner, profile_id, revision, manifest_ref=item['id'], defaults=defaults)

    def _snapshot(self, owner, profile_id):
        snap = self.store.snapshot(owner, profile_id)
        doc = self._document(owner, snap)
        return snap, doc

    def _manifest(self, owner, profile_id, revision, *, solar=False):
        snap, doc = self._snapshot(owner, profile_id)
        if snap['revision'] != revision:
            raise Conflict()
        from feasibility.runtime_context import model_build_digest
        manifest = {'tenant_id': owner, 'profile_id': profile_id, 'revision': revision,
                    'model_build_id': model_build_digest()}
        if solar:
            if doc['profile'] is None:
                raise InvalidDomain()
            return {**manifest, 'kind': 'solar', 'document': doc}
        candidate = doc.get('candidate')
        if not candidate or doc['profile'] is None:
            raise InvalidDomain()
        if doc['profile']['market']['valuation_mode'] != 'flat' and not self.config.get('catalog_root'):
            raise InvalidDomain()
        # A compiled candidate can only come from an owned completed solar job.
        producer = self.store.job(owner, candidate['job_id'])
        if producer['profile_id'] != profile_id or producer['state'] != 'complete':
            raise Conflict()
        directory = self.store.job_directory(owner, candidate['job_id']) / 'candidate'
        with self.store.files._directory(('tenants', owner, 'jobs', producer['id'], 'candidate')):
            pass
        defaults = dict(snap['defaults'].get('factory', {}))
        defaults.update({k: v['value'] for k, v in snap['defaults'].get('overrides', {}).items()})
        return {**manifest, 'kind': 'v2', 'candidate': str(directory),
            'dataset_digest': candidate['digest'], 'profile': doc['profile'], 'defaults': defaults,
            'catalog_root': self.config.get('catalog_root')}

    def _submit(self, owner, body):
        if set(body) - {'profile_id', 'revision', 'operation', 'settings', 'options'}:
            raise StoreError()
        profile_id, revision = body.get('profile_id'), body.get('revision')
        self.store.snapshot(owner, profile_id)
        operation = body.get('operation')
        if operation not in OPERATIONS or operation == 'solar':
            raise InvalidDomain()
        settings, options = body.get('settings', {}), body.get('options', {})
        if not isinstance(settings, dict) or not isinstance(options, dict):
            raise StoreError()
        if set(options) - {'quote', 'weather_year', 'date', 'hour', 'month', 'scenario', 'representative',
                           'mode', 'metric', 'target', 'options', 'search', 'sensitivity_options', 'scope',
                           'cash_mode', 'period', 'aggregation'}:
            raise InvalidDomain()
        manifest = self._manifest(owner, profile_id, revision)
        snap = self.store.snapshot(owner, profile_id, revision)
        settings = {**snap['scenario'].get('settings', {}), **settings}
        if len(canonical_json({'operation': operation, 'settings': settings, 'options': options})) > self.limits['json_bytes']:
            raise TooLarge()
        scenario_hash = hashlib.sha256(canonical_json({'operation': operation, 'settings': settings, 'options': options})).hexdigest()
        job = self.store.create_job(owner, profile_id, revision, scenario_hash)
        try:
            self.store.job_directory(owner, job['id'])
            with self.store.files._directory(('tenants', owner, 'jobs', job['id'])) as fd:
                self.store.files._write_once(fd, 'request.json', canonical_json({
                    'operation': operation, 'settings': settings, 'options': options}))
            self.pool.submit(job, manifest, operation, settings, options)
        except Exception:
            self.store.fail_job(owner, job['id'])
            raise
        return Response(202, {'id': job['id'], 'state': 'queued', 'profile_id': profile_id, 'revision': revision})

    def _result(self, owner, job_id):
        result = self.store.read_result(owner, job_id)
        from feasibility.runtime_context import model_build_digest
        if result.get('model_build_id') != model_build_digest():
            raise Conflict()
        return result

    def request(self, method, path, *, headers=None, body=b'', client_ip='127.0.0.1', _transport_checked=False, local_authority=None):
        headers = headers or {}
        try:
            if not isinstance(body, bytes) or len(path) > 4096:
                raise StoreError()
            hosts = self._hosts_for(local_authority)
            guard_host(headers, hosts)
            if not _transport_checked:
                self._rate(('ip', client_ip), self.limits['ip_per_minute'], 60)
            if method not in ('GET', 'POST', 'PATCH', 'DELETE'):
                return Response(405, {'error': 'Method not allowed'})
            if method != 'GET':
                guard_mutation(headers, hosts)
            parsed = urlsplit(path)
            if parsed.scheme or parsed.netloc or parsed.fragment or unquote(parsed.path) != parsed.path or '..' in parsed.path:
                raise NotFound()
            query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=16)
            if any(len(v) != 1 for v in query.values()):
                raise StoreError()
            path = parsed.path
            if path == '/api/session' and method == 'POST':
                self._rate(('create', client_ip), self.limits['sessions_per_hour'], 3600)
                if self._object(body) != {}:
                    raise StoreError()
                value, cookie = self.sessions.create()
                token = cookie.split(';')[0]
                return Response(201, {**value, 'csrf_token': self._csrf({'Cookie': token})}, {'Set-Cookie': cookie})
            if method == 'GET' and path == '/api/health':
                return Response(body={'status': 'ok'})
            owner = self.sessions.authenticate(headers)
            self._rate(('session', owner), self.limits['requests_per_minute'], 60)
            if method != 'GET':
                supplied = headers.get('X-CSRF-Token', '')
                if hasattr(headers, 'get_all') and len(headers.get_all('X-CSRF-Token', [])) != 1:
                    raise Forbidden()
                if not isinstance(supplied, str) or not hmac.compare_digest(supplied, self._csrf(headers)):
                    raise Forbidden()
            if path == '/api/session':
                if method == 'GET':
                    return Response(body={'tenant_id': owner, 'csrf_token': self._csrf(headers)})
                if method == 'DELETE':
                    return Response(body={'status': 'deleted'}, headers={'Set-Cookie': self.sessions.delete(headers)})
            if path == '/api/admin/restart' and method == 'POST' and self.config.get('restart_callback'):
                import ipaddress
                if not ipaddress.ip_address(client_ip).is_loopback:
                    raise Forbidden()
                if self._object(body):
                    raise StoreError()
                if self.pool.has_active():
                    raise Conflict()
                if hasattr(self.pool, 'prepare_restart') and not self.pool.prepare_restart():
                    raise Conflict()
                value, status = self.config['restart_callback']()
                return Response(status, value)
            if path.startswith('/api/evidence/datasets'):
                from feasibility.evidence_store import EvidenceStore
                evidence = EvidenceStore(self.store, owner, self.store.root / 'tenants' / owner)
                if method == 'GET' and path == '/api/evidence/datasets':
                    return Response(body=evidence.inventory())
                if method == 'GET' and path.endswith('/download') and len(path.split('/')) == 6:
                    return Response(body=evidence.download(path.split('/')[4]), headers={
                        'Content-Type': 'application/octet-stream', 'Content-Disposition': 'attachment; filename="evidence"'})
                if method == 'POST' and path == '/api/evidence/datasets':
                    self._rate(('upload', owner), self.limits['uploads_per_hour'], 3600)
                    if len(body) > self.limits['upload_bytes']:
                        raise TooLarge()
                    filename = unquote(headers.get('X-Dataset-Filename', ''), errors='strict')
                    if Path(filename).suffix.lower() == '.json' and len(body) > self.limits['json_bytes']:
                        raise TooLarge()
                    if headers.get('X-Dataset-Source') == 'google' or Path(filename).suffix.lower() not in ('.csv', '.json'):
                        raise StoreError()
                    return Response(body=evidence.save_upload(filename, body, headers.get('X-Dataset-Source', ''),
                        unquote(headers.get('X-Dataset-Series', ''), errors='strict')))
                if method == 'POST' and path == '/api/evidence/datasets/selection':
                    return Response(body=evidence.set_inclusion(self._object(body)))
                raise NotFound()
            if path in ('/api/session/revoke', '/api/tenant/delete') and method == 'POST':
                if self._object(body):
                    raise StoreError()
                cookie = self.sessions.delete(headers) if path.endswith('delete') else self.sessions.revoke(headers)
                return Response(body={'status': 'deleted' if path.endswith('delete') else 'revoked'}, headers={'Set-Cookie': cookie})
            if path == '/api/profiles':
                if method == 'GET':
                    return Response(body={'profiles': self.store.profiles(owner)})
                if method == 'POST':
                    value = self._object(body)
                    if set(value) - {'profile', 'defaults'}:
                        raise StoreError()
                    if not isinstance(value.get('defaults', {}), dict):
                        raise StoreError()
                    created = self.store.create_profile(owner, defaults={'factory': value.get('defaults', {}), 'overrides': {}})
                    if value.get('profile') is not None:
                        profile = dict(value['profile'], id=created['id'])
                        try:
                            profile = profile_to_dict(parse_profile(profile))
                        except ValueError as exc:
                            raise InvalidDomain() from exc
                        snap = self._publish(owner, created['id'], created['revision'],
                            {'profile': profile, 'candidate': None, 'consumption': None},
                            {'factory': profile['default_settings'], 'overrides': {}})
                        created['revision'] = snap['revision']
                    return Response(201, {**created, 'draft_only': True})
            if path == '/api/profiles/delete' and method == 'POST':
                value = self._object(body)
                if set(value) != {'profile_id'}:
                    raise StoreError()
                self.store.delete_profile(owner, value['profile_id'])
                return Response(body={'status': 'deleted'})
            parts = path.strip('/').split('/')
            if len(parts) >= 3 and parts[:2] == ['api', 'profiles']:
                profile_id = parts[2]
                snap, doc = self._snapshot(owner, profile_id)
                if len(parts) == 3:
                    if method == 'GET':
                        return Response(body={'id': profile_id, 'revision': snap['revision'], 'profile': doc['profile'],
                                              'compiled': bool(doc.get('candidate'))})
                    if method == 'PATCH':
                        value = self._object(body)
                        if set(value) != {'profile'} or headers.get('If-Match', '').strip('"') != snap['revision']:
                            raise Conflict()
                        try:
                            profile = profile_to_dict(parse_profile(dict(value['profile'], id=profile_id)))
                        except ValueError as exc:
                            raise InvalidDomain() from exc
                        result = self._publish(owner, profile_id, snap['revision'],
                            {'profile': profile, 'candidate': None, 'consumption': doc.get('consumption')
                             if profile['consumption']['kind'] == 'interval' and
                             profile['consumption']['artifact_id'] == (doc.get('consumption') or {}).get('artifact_id') else None},
                            {'factory': profile['default_settings'], 'overrides': {}})
                        return Response(body={'id': profile_id, 'revision': result['revision']})
                if len(parts) == 4 and parts[3] == 'validation' and method == 'GET':
                    blockers = []
                    profile = doc['profile']
                    fiscal_revision = None
                    if profile is None:
                        blockers.append('PROFILE_REQUIRED')
                    else:
                        from feasibility.fiscal import rule_digest, resolve_fiscal
                        from feasibility.thermal_baseline import resolve_thermal_baseline
                        fiscal_revision = rule_digest(profile['fiscal'])
                        try:
                            resolve_fiscal(profile['fiscal'], has_gas=any(
                                u['carrier'] == 'gas' for u in profile['thermal']['end_uses']))
                        except ValueError:
                            blockers.append('FISCAL_RULES_INVALID')
                        try:
                            resolve_thermal_baseline(profile)
                        except ValueError:
                            blockers.append('THERMAL_BASELINE_INVALID')
                        if not profile['consumption']['coverage']['complete']:
                            blockers.append('CONSUMPTION_COVERAGE_REQUIRED')
                        if not profile['consumption']['acknowledged']:
                            blockers.append('CONSUMPTION_ACKNOWLEDGEMENT_REQUIRED')
                        if (profile['market']['valuation_mode'] != 'flat' and
                                (not profile['market']['price_revision'] or not self.config.get('catalog_root'))):
                            blockers.append('PRICE_CATALOG_REQUIRED')
                    if not doc.get('candidate'):
                        blockers.append('COMPILED_CANDIDATE_REQUIRED')
                    return Response(body={'revision': snap['revision'], 'has_profile': profile is not None,
                        'compiled': bool(doc.get('candidate')), 'ready': not blockers,
                        'blockers': blockers, 'fiscal_rule_revision': fiscal_revision,
                        'consumption': doc.get('consumption')})
                if len(parts) == 4 and parts[3] == 'consumption' and method == 'POST':
                    self._rate(('upload', owner), self.limits['uploads_per_hour'], 3600)
                    if len(body) > self.limits['upload_bytes']:
                        raise TooLarge()
                    if headers.get('If-Match', '').strip('"') != snap['revision']:
                        raise Conflict()
                    metadata = self._object(headers.get('X-Consumption-Metadata', '').encode())
                    from feasibility.consumption_ingest import IngestOptions, parse_consumption, ConsumptionIngestError
                    from io import BytesIO
                    opts = dict(metadata)
                    for name in ('coverage_start', 'coverage_end'):
                        if opts.get(name) is not None:
                            opts[name] = datetime.fromisoformat(opts[name].replace('Z', '+00:00'))
                    try:
                        parsed_csv = parse_consumption(BytesIO(body), IngestOptions(**opts))
                    except ConsumptionIngestError as exc:
                        # Stable parser diagnostics contain no source text, filenames or paths.
                        return Response(422, {'error': 'Consumption validation failed', 'diagnostic': {
                            'code': exc.code, 'row': exc.row, 'field': exc.field,
                            'counts': dict(exc.summary_counts)}})
                    except (ValueError, TypeError) as exc:
                        raise InvalidDomain() from exc
                    metadata_scope = hashlib.sha256(canonical_json({'purpose': 'consumption', 'options': metadata})).hexdigest()
                    item, _ = self.store.put_upload(owner, body, {'purpose': 'consumption', 'options': metadata}, dedupe_scope=metadata_scope)
                    if doc['profile'] is not None:
                        doc['profile']['consumption']['artifact_id'] = item['id']
                    doc.update(consumption={'artifact_id': item['id'], 'rows': len(parsed_csv.intervals),
                        'total_energy_kwh': parsed_csv.total_energy_kwh,
                        'coverage_start': parsed_csv.coverage_start.isoformat(),
                        'coverage_end': parsed_csv.coverage_end.isoformat(),
                        'unit': parsed_csv.options.unit, 'timestamp_convention': parsed_csv.options.timestamp_convention,
                        'fixed_clock': parsed_csv.options.fixed_clock,
                        'counts': dict(parsed_csv.summary_counts)}, candidate=None)
                    result = self._publish(owner, profile_id, snap['revision'], doc)
                    return Response(201, {'revision': result['revision'], **doc['consumption']})
                if len(parts) == 4 and parts[3] == 'solar-jobs' and method == 'POST':
                    value = self._object(body)
                    if set(value) != {'revision', 'years', 'solar', 'load'}:
                        raise StoreError()
                    manifest = self._manifest(owner, profile_id, value['revision'], solar=True)
                    # No request can provide file paths, providers, URLs or cache locations.
                    if set(value['load']) - {'monthly_totals', 'policy'}:
                        raise InvalidDomain()
                    if set(value['solar']) != {'loss_percent', 'mounting', 'technology', 'source_policy'}:
                        raise InvalidDomain()
                    job = self.store.create_job(owner, profile_id, value['revision'], hashlib.sha256(canonical_json(value)).hexdigest())
                    directory = self.store.job_directory(owner, job['id'])
                    load = dict(value['load'])
                    if doc.get('consumption'):
                        upload = self.store.upload(owner, doc['consumption']['artifact_id'])
                        load.update(interval_csv=str(self.store.root / 'tenants' / owner / 'uploads' / upload['id'] / 'content'),
                                    ingest_options=upload['metadata']['options'])
                    source = {'profile': doc['profile'], 'years': value['years'], 'solar': value['solar'],
                        'load': load, 'cache_root': str(self.store.root / 'tenants' / owner / 'provider-cache'), 'tenant_id': owner}
                    with self.store.files._directory(('tenants', owner, 'jobs', job['id'])) as fd:
                        self.store.files._write_once(fd, 'source.json', canonical_json(source))
                    manifest.pop('document')
                    manifest['source_manifest'] = str(directory / 'source.json')
                    try:
                        self.pool.submit(job, manifest, 'solar', {}, {})
                    except Exception:
                        self.store.fail_job(owner, job['id'])
                        raise
                    return Response(202, {'id': job['id'], 'state': 'queued'})
                if len(parts) == 4 and parts[3] == 'compile' and method == 'POST':
                    value = self._object(body)
                    if set(value) != {'revision', 'job_id'} or value['revision'] != snap['revision']:
                        raise Conflict()
                    job = self.store.job(owner, value['job_id'])
                    if job['profile_id'] != profile_id or job['profile_revision'] != snap['revision'] or job['state'] != 'complete':
                        raise Conflict()
                    directory = self.store.job_directory(owner, job['id']) / 'candidate'
                    with self.store.files._directory(('tenants', owner, 'jobs', job['id'], 'candidate')) as fd:
                        raw = self.store.files._read(fd, 'manifest.json', self.limits['json_bytes'])
                    doc['candidate'] = {'job_id': job['id'], 'digest': hashlib.sha256(raw).hexdigest()}
                    result = self._publish(owner, profile_id, snap['revision'], doc)
                    return Response(body={'revision': result['revision'], 'compiled': True})
            if path in ('/api/state', '/api/defaults'):
                if method == 'GET':
                    profile_id = query.get('profile_id', [''])[0]
                    self.store.snapshot(owner, profile_id)
                    return Response(body=TenantState(self.store, owner, profile_id).get_state(query.get('revision', [None])[0]))
                if method == 'POST':
                    value = self._object(body)
                    profile_id = value.pop('profile_id', '')
                    self.store.snapshot(owner, profile_id)
                    if path == '/api/state':
                        return Response(body=TenantState(self.store, owner, profile_id).update_state(value))
                    from feasibility import assumptions
                    from feasibility.server import _draft_defaults
                    adapter = TenantDefaults(self.store, owner, profile_id, value.get('revision'))
                    if value.get('action') == 'reset':
                        assumptions.reset_user_default(value.get('name'), path=adapter)
                    else:
                        assumptions.save_user_default(value.get('name'), value['value'], _draft_defaults, path=adapter)
                    return Response(body={**TenantState(self.store, owner, profile_id).get_state(), 'draft_only': True})
            if path == '/api/job' and method == 'GET':
                job = self.store.job(owner, query.get('id', [''])[0])
                if job['state'] == 'complete':
                    self._result(owner, job['id'])
                return Response(body={k: job[k] for k in ('id', 'state', 'profile_id', 'profile_revision', 'error')})
            if path in ('/report', '/report.md', '/report.csv', '/assumptions.json', '/verification.json', '/api/study') and method == 'GET':
                job_id = query.get('job_id', [''])[0]
                job = self.store.job(owner, job_id)
                identity = self._result(owner, job_id)
                if path == '/verification.json':
                    return Response(body=identity)
                if path == '/assumptions.json':
                    with self.store.files._directory(('tenants', owner, 'jobs', job_id)) as fd:
                        request = strict_json(self.store.files._read(fd, 'request.json', self.limits['json_bytes']))
                    return Response(body={'identity': identity, 'request': request,
                        'records': TenantState(self.store, owner, job['profile_id']).get_state()['records']})
                name = 'report.html' if path == '/report' else 'report.csv' if path == '/report.csv' else 'report.md' if path == '/report.md' else 'result.json'
                with self.store.files._directory(('tenants', owner, 'jobs', job_id)) as fd:
                    raw = self.store.files._read(fd, name, 128 * 1024**2)
                return Response(body=raw, headers={'Content-Type': 'text/html' if path == '/report' else 'application/json' if path == '/api/study' else 'text/csv; charset=utf-8' if path == '/report.csv' else 'text/plain; charset=utf-8',
                    'Content-Disposition': 'inline; filename="report.html"' if path == '/report' else f'attachment; filename="{name}"'})
            if path == '/api/jobs' and method == 'POST':
                return self._submit(owner, self._object(body))
            if len(parts) >= 3 and parts[:2] == ['api', 'jobs']:
                job = self.store.job(owner, parts[2])
                if len(parts) == 3 and method == 'DELETE':
                    self.pool.cancel(owner, job['id'])
                    return Response(body={'id': job['id'], 'state': 'cancelled'})
                if len(parts) == 3 and method == 'GET':
                    result = {'id': job['id'], 'state': job['state'], 'profile_id': job['profile_id'], 'revision': job['profile_revision']}
                    if job['error']:
                        result['error'] = job['error']
                    if job['state'] == 'complete':
                        result['identity'] = self._result(owner, job['id'])
                    return Response(body=result)
                if len(parts) == 4 and parts[3] in ('report', 'result') and method == 'GET':
                    self._result(owner, job['id'])
                    directory = self.store.job_directory(owner, job['id'])
                    name = 'report.html' if parts[3] == 'report' else 'result.json'
                    with self.store.files._directory(('tenants', owner, 'jobs', job['id'])) as fd:
                        raw = self.store.files._read(fd, name, 128 * 1024**2)
                    return Response(body=raw, headers={'Content-Type': 'text/html' if name.endswith('.html') else 'application/json',
                        'Content-Disposition': 'inline; filename="report.html"' if name.endswith('.html') else 'attachment; filename="result.json"'})
            if path == '/api/schema' and method == 'GET':
                profile_id = query.get('profile_id', [''])[0]
                snap, doc = self._snapshot(owner, profile_id)
                state = TenantState(self.store, owner, profile_id).get_state()
                catalog = None
                if self.config.get('catalog_root'):
                    from feasibility.runtime_context import available_price_catalog
                    catalog = available_price_catalog(self.config['catalog_root'])
                # Descriptors only: values remain the selected profile's explicit inputs.
                from feasibility import assumptions
                records = []
                for record in state['records']:
                    row = assumptions.SPEC.get(record['name'])
                    records.append({**record, 'unit': row[1] if row else '',
                        'bounds': row[2] if row else None, 'group': row[3] if row else 'profile',
                        'reasoning': row[4] if row else 'Explicit profile input',
                        'description': row[4] if row else 'Explicit profile input',
                        'status': 'proposed', 'confidence': 'User input', 'owner': 'user'})
                return Response(body={'profile_id': profile_id, 'revision': snap['revision'], 'defaults': state['defaults'],
                    'records': records, 'profile': doc.get('profile'), 'catalog': catalog, 'compiled': bool(doc.get('candidate')), 'calibration_operation': 'calibration'})
            if path.startswith('/api/') and path[5:] in ALIASES and method == 'POST':
                value = self._object(body)
                operation = ALIASES[path[5:]]
                if path == '/api/dayhour':
                    monthly = value.get('period', value.get('aggregation')) in ('month', 'monthly') or ('month' in value and 'date' not in value)
                    forecast = value.get('mode') in ('forecast', 'forecast_2027')
                    operation = ('forecast-monthly' if monthly else 'forecast') if forecast else ('monthly' if monthly else 'dayhour')
                return self._submit(owner, {'profile_id': value.pop('profile_id', None), 'revision': value.pop('revision', None),
                    'operation': operation, 'settings': value.pop('settings', {}), 'options': value})
            # Static report/assumptions/verification aliases have no global authority.
            raise NotFound()
        except StoreError as exc:
            return Response(exc.status, {'error': exc.message}, {'Retry-After': '60'} if exc.status == 429 else {})
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            return Response(400, {'error': 'Invalid request'})
        except Exception:
            return Response(500, {'error': 'Storage operation failed'})


def create_service(config, store, pool):
    return Service(config, store, pool)
