import json
import re
import pytest
from feasibility.tenant_store import TenantStore
from feasibility.api import create_service


class FakePool:
    def __init__(self):
        self.calls = []

    def submit(self, job, manifest, operation, settings, options):
        self.calls.append(('submit', job, manifest, operation, settings, options))

    def cancel(self, tenant_id, job_id):
        self.calls.append(('cancel', tenant_id, job_id))

    def shutdown_tenant(self, tenant_id):
        self.calls.append(('shutdown_tenant', tenant_id))

    def has_active(self):
        return False


def _body(res):
    if isinstance(res.body, (bytes, bytearray)):
        return json.loads(res.body.decode('utf-8'))
    return res.body


def _cookie(res):
    hdr = res.headers.get('Set-Cookie') or res.headers.get('set-cookie', '')
    m = re.search(r'pv_session=[^;]+', hdr)
    return m.group(0) if m else ''


def _svc(tmp_path):
    store = TenantStore(tmp_path / 'store')
    config = {'allowed_hosts': ['localhost:8765']}
    return create_service(config, store, FakePool())


def _req(svc, method, path, *, cookie=None, csrf=None, origin='http://localhost:8765', host='localhost:8765', body=b''):
    headers = {'Host': host}
    if cookie:
        headers['Cookie'] = cookie
    if origin is not None:
        headers['Origin'] = origin
    if csrf is not None:
        headers['X-CSRF-Token'] = csrf
    if isinstance(body, (dict, list)):
        body = json.dumps(body).encode('utf-8')
    return svc.request(method, path, headers=headers, body=body)


def test_two_clients_isolation(tmp_path):
    svc = _svc(tmp_path)
    r1 = _req(svc, 'POST', '/api/session', body={})
    assert r1.status == 201
    c1, csrf1, t1 = _cookie(r1), _body(r1)['csrf_token'], _body(r1)['tenant_id']

    r2 = _req(svc, 'POST', '/api/session', body={})
    assert r2.status == 201
    c2, csrf2, t2 = _cookie(r2), _body(r2)['csrf_token'], _body(r2)['tenant_id']

    assert t1 != t2 and c1 != c2 and csrf1 != csrf2
    assert _body(_req(svc, 'GET', '/api/session', cookie=c1))['csrf_token'] == csrf1
    assert _body(_req(svc, 'GET', '/api/session', cookie=c2))['csrf_token'] == csrf2

    rp = _req(svc, 'POST', '/api/profiles', cookie=c1, csrf=csrf1, body={})
    assert rp.status in (200, 201)
    pid, rev = _body(rp)['id'], _body(rp)['revision']

    rs = _req(svc, 'POST', '/api/state', cookie=c1, csrf=csrf1, body={'profile_id': pid, 'revision': rev, 'settings': {}})
    assert rs.status in (200, 201)

    assert _req(svc, 'GET', f'/api/profiles/{pid}', cookie=c1).status == 200
    assert _req(svc, 'GET', f'/api/state?profile_id={pid}', cookie=c1).status == 200
    assert _req(svc, 'GET', f'/api/profiles/{pid}', cookie=c2).status == 404
    assert _req(svc, 'GET', f'/api/state?profile_id={pid}', cookie=c2).status == 404


def test_auth_and_mutation_guards(tmp_path):
    svc = _svc(tmp_path)
    assert _req(svc, 'GET', '/api/profiles/nonexistent').status == 401
    assert _req(svc, 'POST', '/api/profiles', body={}).status == 401

    dup = _req(svc, 'GET', '/api/session', cookie='pv_session=tok1; pv_session=tok2')
    assert dup.status == 401

    r = _req(svc, 'POST', '/api/session', body={})
    assert r.status == 201
    cookie, csrf = _cookie(r), _body(r)['csrf_token']

    assert _req(svc, 'POST', '/api/profiles', cookie=cookie, csrf=None, body={}).status == 403
    assert _req(svc, 'POST', '/api/profiles', cookie=cookie, csrf='wrong-csrf', body={}).status == 403
    assert _req(svc, 'POST', '/api/profiles', cookie=cookie, csrf=csrf, host='evil.com', body={}).status == 403
    assert _req(svc, 'POST', '/api/profiles', cookie=cookie, csrf=csrf, origin='http://evil.com', body={}).status == 403


def test_real_jobs_reports_stale_results_and_owner_binding(tmp_path):
    import shutil
    from tests.test_worker_pool import owned_manifest, wait_job
    from feasibility.worker_pool import WorkerPool
    from feasibility.profiles import canonical_json
    store = TenantStore(tmp_path / 'store')
    pool = WorkerPool(store)
    svc = create_service({'allowed_hosts': ['localhost:8765']}, store, pool)
    try:
        clients, submitted = [], []
        for monthly in (100, 200):
            session = _req(svc, 'POST', '/api/session', body={})
            owner, cookie, csrf = session.body['tenant_id'], _cookie(session), session.body['csrf_token']
            profile, manifest = owned_manifest(store, owner, monthly=monthly)
            document = {'profile': manifest['profile'], 'candidate': None, 'consumption': None}
            upload, _ = store.put_upload(owner, canonical_json(document), {'purpose': 'profile_manifest'})
            snap = store.publish(owner, profile['id'], profile['revision'], manifest_ref=upload['id'],
                defaults={'factory': {'pmg_election': False}, 'overrides': {}})
            producer = store.create_job(owner, profile['id'], snap['revision'], 'd'*64)
            shutil.copytree(manifest['candidate'], store.job_directory(owner, producer['id']) / 'candidate')
            store.publish_result(owner, producer['id'], {})
            compiled = _req(svc, 'POST', f"/api/profiles/{profile['id']}/compile", cookie=cookie, csrf=csrf,
                            body={'revision': snap['revision'], 'job_id': producer['id']})
            assert compiled.status == 200
            revision = compiled.body['revision']
            response = _req(svc, 'POST', '/api/jobs', cookie=cookie, csrf=csrf,
                body={'profile_id': profile['id'], 'revision': revision, 'operation': 'calibration', 'settings': {}, 'options': {}})
            assert response.status == 202
            clients.append((owner, cookie, csrf, profile['id'], revision))
            submitted.append(response.body['id'])
        for client, job_id in zip(clients, submitted):
            assert wait_job(store, client[0], {'id': job_id})['state'] == 'complete'
        data = []
        reports = []
        for client, job_id in zip(clients, submitted):
            result = _req(svc, 'GET', f'/api/jobs/{job_id}/result', cookie=client[1])
            assert result.status == 200
            data.append(json.loads(result.body))
            reports.append(_req(svc, 'GET', f'/report?job_id={job_id}', cookie=client[1]).body)
        assert [v['annual_kwh'] for v in data] == [1200, 2400]
        for path, content in (('/report.md', b'# Household simulation result'), ('/report.csv', b'section,field,value')):
            response = _req(svc, 'GET', f'{path}?job_id={submitted[0]}', cookie=clients[0][1])
            assert response.status == 200 and response.body.startswith(content)
            assert b'projected_output_fraction' in response.body
            assert _req(svc, 'GET', f'{path}?job_id={submitted[0]}', cookie=clients[1][1]).status == 404
        assert reports[0] != reports[1]
        assert _req(svc, 'GET', f'/api/jobs/{submitted[0]}/report', cookie=clients[1][1]).status == 404
        assert _req(svc, 'GET', f'/report?job_id={submitted[0]}', cookie=clients[1][1]).status == 404
        owner, cookie, csrf, profile_id, revision = clients[0]
        changed = _req(svc, 'POST', '/api/state', cookie=cookie, csrf=csrf,
            body={'profile_id': profile_id, 'revision': revision, 'settings': {'panel_watts': 500}})
        assert changed.status == 200
        for path in ('/report.md', '/report.csv'):
            assert _req(svc, 'GET', f'{path}?job_id={submitted[0]}', cookie=cookie).status == 409
        assert _req(svc, 'GET', f'/api/jobs/{submitted[0]}/report', cookie=cookie).status == 409
        assert _req(svc, 'POST', '/api/jobs', cookie=cookie, csrf=csrf,
            body={'profile_id': profile_id, 'revision': revision, 'operation': 'calibration'}).status == 409
    finally:
        pool.close()


def test_config_limits_and_unsafe_inputs(tmp_path):
    from feasibility.api import create_service
    store = TenantStore(tmp_path / 'store')
    for config in ({'allowed_hosts': ['*']}, {'allowed_hosts': ['localhost:8765'], 'internet_hosted': True},
                   {'allowed_hosts': ['localhost:8765'], 'limits': {'uploads_per_hour': 0}}):
        with pytest.raises(ValueError):
            create_service(config, store, FakePool())
    svc = create_service({'allowed_hosts': ['localhost:8765'], 'limits': {'json_bytes': 32, 'sessions_per_hour': 1}}, store, FakePool())
    assert _req(svc, 'POST', '/api/session', body=b'x'*33).status == 413
    # Session-creation attempts are IP limited, including malformed requests.
    assert _req(svc, 'POST', '/api/session', body={}).status == 429
    svc = _svc(tmp_path / 'other')
    r = _req(svc, 'POST', '/api/session', body={})
    cookie, csrf = _cookie(r), r.body['csrf_token']
    assert _req(svc, 'GET', '/api/profiles/%2e%2e', cookie=cookie).status == 404
    assert _req(svc, 'POST', '/api/jobs', cookie=cookie, csrf=csrf, body={'profile_id': '0'*32, 'operation': '__import__'}).status == 404
    for filename, source, raw in [('image.tiff', 'pvgis6', b'II*\0'), ('solar.json', 'google', b'{}')]:
        headers = {'Host': 'localhost:8765', 'Origin': 'http://localhost:8765', 'Cookie': cookie, 'X-CSRF-Token': csrf,
                   'X-Dataset-Filename': filename, 'X-Dataset-Source': source}
        assert svc.request('POST', '/api/evidence/datasets', headers=headers, body=raw).status == 400


def test_report_labels_and_filenames_cannot_create_markup():
    from feasibility.worker_pool import render_owned_result
    raw = render_owned_result({'label': '<script>alert(1)</script>', 'filename': '"><img src=x onerror=alert(1)>'}, {})
    assert b'<script>' not in raw and b'<img' not in raw
    assert b'&lt;script&gt;' in raw and b'&lt;img' in raw


def test_cli_settings_and_json_evidence_are_bounded(tmp_path):
    from feasibility.build_report import read_settings
    settings = tmp_path / 'settings.json'
    settings.write_bytes(b'{"panel_watts":400}')
    assert read_settings(settings) == {'panel_watts': 400}
    for raw in (b'[]', b'{"x":NaN}', b'x'*(2*1024**2+1)):
        settings.write_bytes(raw)
        with pytest.raises(ValueError):
            read_settings(settings)
    svc = _svc(tmp_path / 'service')
    result = _req(svc, 'POST', '/api/session', body={})
    headers = {'Host': 'localhost:8765', 'Origin': 'http://localhost:8765', 'Cookie': _cookie(result),
        'X-CSRF-Token': result.body['csrf_token'], 'X-Dataset-Filename': 'roof.json', 'X-Dataset-Source': 'pvgis6'}
    assert svc.request('POST', '/api/evidence/datasets', headers=headers, body=b' '*(2*1024**2+1)).status == 413


def test_same_csv_bytes_with_different_units_have_distinct_owned_identity(tmp_path):
    svc = _svc(tmp_path)
    session = _req(svc, 'POST', '/api/session', body={})
    cookie, csrf = _cookie(session), session.body['csrf_token']
    profile = _req(svc, 'POST', '/api/profiles', cookie=cookie, csrf=csrf, body={}).body
    raw = b'ts,val\n2023-01-01T00:00:00Z,1000\n'
    options = dict(adapter='timestamp', delimiter=',', decimal_separator='.', columns={'timestamp': 'ts', 'value': 'val'},
        unit='Wh', duration_seconds=3600, timezone='Europe/Rome', timestamp_convention='start', demand_basis='gross')
    headers = {'Host': 'localhost:8765', 'Origin': 'http://localhost:8765', 'Cookie': cookie,
        'X-CSRF-Token': csrf, 'If-Match': profile['revision'], 'X-Consumption-Metadata': json.dumps(options)}
    route = f"/api/profiles/{profile['id']}/consumption"
    first = svc.request('POST', route, headers=headers, body=raw)
    assert first.status == 201 and first.body['total_energy_kwh'] == 1
    options['unit'] = 'kWh'
    headers.update({'If-Match': first.body['revision'], 'X-Consumption-Metadata': json.dumps(options)})
    second = svc.request('POST', route, headers=headers, body=raw)
    assert second.status == 201 and second.body['total_energy_kwh'] == 1000
    assert first.body['artifact_id'] != second.body['artifact_id']
    for item, unit in ((first, 'Wh'), (second, 'kWh')):
        assert svc.store.upload(session.body['tenant_id'], item.body['artifact_id'])['metadata']['options']['unit'] == unit


def test_lan_destination_allowlist_and_explicit_loopback_restart(tmp_path):
    store = TenantStore(tmp_path / 'store')
    pool = FakePool()
    calls = []
    svc = create_service({'allowed_hosts': ['localhost:8765'], 'socket_destination': True,
        'restart_callback': lambda: (calls.append(True) or {'status': 'restarting'}, 200)}, store, pool)
    authority = '192.0.2.10:8765'
    headers = {'Host': authority, 'Origin': 'http://' + authority}
    result = svc.request('POST', '/api/session', headers=headers, body=b'{}', client_ip='192.0.2.20', local_authority=authority)
    assert result.status == 201
    headers.update(Cookie=_cookie(result), **{'X-CSRF-Token': result.body['csrf_token']})
    assert svc.request('POST', '/api/admin/restart', headers=headers, body=b'{}', client_ip='192.0.2.20', local_authority=authority).status == 403
    assert not calls
    assert svc.request('POST', '/api/admin/restart', headers=headers, body=b'{}', local_authority=authority).status == 200
    assert calls == [True]
    assert svc.request('POST', '/api/restart', headers=headers, body=b'{}', local_authority=authority).status == 404
    pool.has_active = lambda: True
    assert svc.request('POST', '/api/admin/restart', headers=headers, body=b'{}', local_authority=authority).status == 409


def test_onboarding_validation_and_csv_diagnostics_are_owned_and_sanitized(tmp_path):
    svc = _svc(tmp_path)
    session = _req(svc, 'POST', '/api/session', body={})
    cookie, csrf = _cookie(session), session.body['csrf_token']
    profile = _req(svc, 'POST', '/api/profiles', cookie=cookie, csrf=csrf, body={}).body
    path = '/api/profiles/' + profile['id']
    validation = _req(svc, 'GET', path + '/validation', cookie=cookie)
    assert validation.status == 200
    assert validation.body['ready'] is False
    assert validation.body['blockers'] == ['PROFILE_REQUIRED', 'COMPILED_CANDIDATE_REQUIRED']
    assert validation.body['fiscal_rule_revision'] is None
    other = _req(svc, 'POST', '/api/session', body={})
    assert _req(svc, 'GET', path + '/validation', cookie=_cookie(other)).status == 404
    metadata = dict(delimiter=';', decimal_separator=',', columns={'timestamp': 'time', 'value': 'energy'},
                    unit='kWh', duration_seconds=3600, timezone='Europe/Rome',
                    timestamp_convention='start', demand_basis='gross')
    headers = {'Host': 'localhost:8765', 'Origin': 'http://localhost:8765',
               'Cookie': cookie, 'X-CSRF-Token': csrf, 'If-Match': profile['revision'],
               'X-Consumption-Metadata': json.dumps(metadata)}
    bad = svc.request('POST', path + '/consumption', headers=headers,
                      body=b'time;energy\n2025-01-01T00:00:00+01:00;<private-label>\n')
    assert bad.status == 422
    assert bad.body['diagnostic'] == {'code': 'invalid_value', 'row': 2, 'field': 'value',
                                      'counts': {'rows_seen': 1, 'errors': 1}}
    assert '<private-label>' not in json.dumps(bad.body)
    assert _req(svc, 'GET', path, cookie=cookie).body['revision'] == profile['revision']
    good = svc.request('POST', path + '/consumption', headers=headers,
                       body=b'time;energy\n2025-01-01T00:00:00+01:00;0\n')
    assert good.status == 201
    assert good.body['unit'] == 'kWh'
    assert good.body['timestamp_convention'] == 'start'
    assert good.body['total_energy_kwh'] == 0
    assert good.body['coverage_start'] == '2024-12-31T23:00:00+00:00'
    assert good.body['coverage_end'] == '2025-01-01T00:00:00+00:00'


def test_onboarding_switch_to_coarse_input_drops_old_interval_source(tmp_path):
    from tests.test_profiles import valid_draft_payload
    svc = _svc(tmp_path)
    session = _req(svc, 'POST', '/api/session', body={})
    cookie, csrf = _cookie(session), session.body['csrf_token']
    payload = valid_draft_payload.__wrapped__()
    created = _req(svc, 'POST', '/api/profiles', cookie=cookie, csrf=csrf, body={'profile': payload})
    assert created.status == 201
    path = '/api/profiles/' + created.body['id']
    metadata = dict(delimiter=';', decimal_separator=',', columns={'timestamp': 'time', 'value': 'energy'},
                    unit='kWh', duration_seconds=3600, timezone='Europe/Rome',
                    timestamp_convention='start', demand_basis='gross')
    headers = {'Host': 'localhost:8765', 'Origin': 'http://localhost:8765', 'Cookie': cookie,
               'X-CSRF-Token': csrf, 'If-Match': created.body['revision'],
               'X-Consumption-Metadata': json.dumps(metadata)}
    upload = svc.request('POST', path + '/consumption', headers=headers,
                         body=b'time;energy\n2025-01-01T00:00:00+01:00;0\n')
    assert upload.status == 201
    current = _req(svc, 'GET', path, cookie=cookie).body['profile']
    assert current['consumption']['artifact_id'] == upload.body['artifact_id']
    current['consumption'].update(kind='monthly', artifact_id=None, projection_policy='synthetic_v1')
    headers['If-Match'] = upload.body['revision']
    changed = svc.request('PATCH', path, headers=headers, body=json.dumps({'profile': current}).encode())
    assert changed.status == 200
    validation = _req(svc, 'GET', path + '/validation', cookie=cookie)
    assert validation.body['consumption'] is None
    assert validation.body['ready'] is False
