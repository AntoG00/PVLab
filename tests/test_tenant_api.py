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


def test_solar_coverage_requires_consent_and_current_revision(tmp_path, monkeypatch):
    from tests.test_profiles import valid_draft_payload
    from feasibility import solar_provider
    svc = _svc(tmp_path)
    session = _req(svc, 'POST', '/api/session', body={})
    cookie, csrf = _cookie(session), session.body['csrf_token']
    created = _req(svc, 'POST', '/api/profiles', cookie=cookie, csrf=csrf,
                   body={'profile': valid_draft_payload.__wrapped__()}).body
    path = '/api/profiles/' + created['id'] + '/solar-coverage'
    headers = {'Host': 'localhost:8765', 'Origin': 'http://localhost:8765',
               'Cookie': cookie, 'X-CSRF-Token': csrf, 'If-Match': created['revision']}
    calls = []
    monkeypatch.setattr(solar_provider, 'check_solar_coverage',
                        lambda profile, transport=None, cache=None: calls.append(profile) or
                        {'supported': True, 'years': [2024, 2025]})
    def post(consent, revision):
        return svc.request('POST', path, headers=headers,
                           body=json.dumps({'consent': consent, 'revision': revision}).encode())
    assert post(False, created['revision']).status == 400
    assert post(True, 'stale').status == 409
    assert not calls
    result = post(True, created['revision'])
    assert result.status == 200 and result.body['years'] == [2024, 2025]
    assert len(calls) == 1


def test_solar_jobs_requires_consent(tmp_path):
    from tests.test_profiles import valid_draft_payload
    svc = _svc(tmp_path)
    session = _req(svc, 'POST', '/api/session', body={})
    cookie, csrf = _cookie(session), session.body['csrf_token']
    created = _req(svc, 'POST', '/api/profiles', cookie=cookie, csrf=csrf,
                   body={'profile': valid_draft_payload.__wrapped__()}).body
    path = '/api/profiles/' + created['id'] + '/solar-jobs'
    headers = {'Host': 'localhost:8765', 'Origin': 'http://localhost:8765',
               'Cookie': cookie, 'X-CSRF-Token': csrf}
    base_payload = {
        'revision': created['revision'],
        'years': [2024],
        'solar': {'loss_percent': 14, 'mounting': 'free', 'technology': 'cSi', 'source_policy': 'policy'},
        'load': {'monthly_totals': [100] * 12, 'policy': {'mode': 'synthetic'}}
    }
    def post(p):
        return svc.request('POST', path, headers=headers, body=json.dumps(p).encode())

    assert post(base_payload).status == 400
    res_false = post({**base_payload, 'consent': False})
    assert res_false.status == 400
    assert res_false.body['consent_required'] is True
    assert res_false.body['error'] == 'Explicit user consent required before external network requests'
    assert post({**base_payload, 'consent': 'true'}).status == 400
    assert post({**base_payload, 'consent': 1}).status == 400
    assert post({**base_payload, 'consent': True, 'revision': 'stale'}).status == 409
    assert not svc.pool.calls
    res_true = post({**base_payload, 'consent': True})
    assert res_true.status == 202
    assert res_true.body['state'] == 'queued'
    assert len(svc.pool.calls) == 1
    assert svc.pool.calls[0][3] == 'solar'


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
    from pathlib import Path
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
            with store.files._directory(('tenants', owner, 'jobs', producer['id'], 'candidate'),
                                        create=True) as directory:
                for artifact in Path(manifest['candidate']).iterdir():
                    store.files._write_once(directory, artifact.name, artifact.read_bytes())
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


def test_profile_api_name_trimmed_persists_and_isolated(tmp_path):
    svc = _svc(tmp_path)
    s1 = _req(svc, 'POST', '/api/session', body={})
    c1, csrf1 = _cookie(s1), _body(s1)['csrf_token']
    s2 = _req(svc, 'POST', '/api/session', body={})
    c2 = _cookie(s2)

    r_default = _req(svc, 'POST', '/api/profiles', cookie=c1, csrf=csrf1, body={})
    assert r_default.status == 201
    pid_default = _body(r_default)['id']

    r_named = _req(svc, 'POST', '/api/profiles', cookie=c1, csrf=csrf1, body={'name': '   Rooftop West   '})
    assert r_named.status == 201
    pid_named = _body(r_named)['id']

    profiles1 = {p['id']: p for p in _body(_req(svc, 'GET', '/api/profiles', cookie=c1))['profiles']}
    assert profiles1[pid_default]['name'] == ''
    assert profiles1[pid_named]['name'] == 'Rooftop West'

    profiles2 = _body(_req(svc, 'GET', '/api/profiles', cookie=c2))['profiles']
    assert not any(p['id'] in (pid_default, pid_named) for p in profiles2)
    assert _req(svc, 'GET', f'/api/profiles/{pid_named}', cookie=c2).status == 404


@pytest.mark.parametrize(
    'invalid_name',
    [123, None, 'a' * 121, 'bad\nname', 'bad\x00name', 'bad\x7fname'],
)
def test_profile_api_invalid_name_fails_400_no_rows(tmp_path, invalid_name):
    svc = _svc(tmp_path)
    session = _req(svc, 'POST', '/api/session', body={})
    cookie, csrf = _cookie(session), _body(session)['csrf_token']

    res = _req(svc, 'POST', '/api/profiles', cookie=cookie, csrf=csrf, body={'name': invalid_name})
    assert res.status == 400

    list_res = _req(svc, 'GET', '/api/profiles', cookie=cookie)
    assert _body(list_res)['profiles'] == []
    with svc.store._connection() as db:
        assert db.execute('SELECT COUNT(*) FROM profiles').fetchone()[0] == 0



def test_consumption_load_persistence_reselect_and_restart(tmp_path):
    from copy import deepcopy
    from feasibility.api import create_service
    from feasibility.tenant_store import TenantStore
    from tests.test_profiles import valid_draft_payload

    svc = _svc(tmp_path)
    res = _req(svc, 'POST', '/api/session', body={})
    assert res.status == 201
    cookie, csrf = _cookie(res), _body(res)['csrf_token']

    base = valid_draft_payload.__wrapped__()

    # 1. Profile 1: Annual-only consumption (optional annual_kwh present, monthly_totals absent)
    p1_payload = deepcopy(base)
    p1_payload['consumption'].update(
        kind='annual',
        annual_kwh=3500.0,
        projection_policy='synthetic_v1',
        acknowledged=True,
    )
    r1 = _req(svc, 'POST', '/api/profiles', cookie=cookie, csrf=csrf, body={'name': 'Annual Household', 'profile': p1_payload})
    assert r1.status == 201
    p1_id, p1_rev = _body(r1)['id'], _body(r1)['revision']

    # 2. Profile 2: Monthly consumption (both annual_kwh and 12 monthly_totals present)
    p2_payload = deepcopy(base)
    months = [300.0, 320.0, 350.0, 400.0, 450.0, 500.0, 520.0, 480.0, 420.0, 380.0, 350.0, 330.0]
    p2_payload['consumption'].update(
        kind='monthly',
        annual_kwh=4800.0,
        monthly_totals=months,
        projection_policy='synthetic_v1',
        acknowledged=True,
    )
    r2 = _req(svc, 'POST', '/api/profiles', cookie=cookie, csrf=csrf, body={'name': 'Monthly Household', 'profile': p2_payload})
    assert r2.status == 201
    p2_id, p2_rev = _body(r2)['id'], _body(r2)['revision']

    # 3. Profile 3: Old profile backward compatibility (neither annual_kwh nor monthly_totals in payload)
    p3_payload = deepcopy(base)
    r3 = _req(svc, 'POST', '/api/profiles', cookie=cookie, csrf=csrf, body={'name': 'Legacy Household', 'profile': p3_payload})
    assert r3.status == 201
    p3_id, p3_rev = _body(r3)['id'], _body(r3)['revision']

    # Reselect across all 3 profiles and verify isolated fields
    p1_get = _req(svc, 'GET', f'/api/profiles/{p1_id}', cookie=cookie)
    assert p1_get.status == 200
    c1 = _body(p1_get)['profile']['consumption']
    assert c1['kind'] == 'annual'
    assert c1['annual_kwh'] == 3500.0
    assert 'monthly_totals' not in c1

    p2_get = _req(svc, 'GET', f'/api/profiles/{p2_id}', cookie=cookie)
    assert p2_get.status == 200
    c2 = _body(p2_get)['profile']['consumption']
    assert c2['kind'] == 'monthly'
    assert c2['annual_kwh'] == 4800.0
    assert c2['monthly_totals'] == months

    p3_get = _req(svc, 'GET', f'/api/profiles/{p3_id}', cookie=cookie)
    assert p3_get.status == 200
    c3 = _body(p3_get)['profile']['consumption']
    assert c3['kind'] == 'interval'
    assert 'annual_kwh' not in c3
    assert 'monthly_totals' not in c3

    # Schema endpoint also returns persisted profile consumption fields
    schema_res = _req(svc, 'GET', f'/api/schema?profile_id={p2_id}', cookie=cookie)
    assert schema_res.status == 200
    assert _body(schema_res)['profile']['consumption']['annual_kwh'] == 4800.0
    assert _body(schema_res)['profile']['consumption']['monthly_totals'] == months

    # Update Profile 1 via PATCH with new revision and reselect
    p1_update = _body(p1_get)['profile']
    p1_update['consumption']['annual_kwh'] = 3600.0
    p1_update['consumption']['monthly_totals'] = [300.0] * 12
    headers = {'Host': 'localhost:8765', 'Origin': 'http://localhost:8765',
               'Cookie': cookie, 'X-CSRF-Token': csrf, 'If-Match': p1_rev}
    patch_res = svc.request('PATCH', f'/api/profiles/{p1_id}', headers=headers,
                            body=json.dumps({'profile': p1_update}).encode())
    assert patch_res.status == 200
    p1_rev_updated = patch_res.body['revision']
    assert p1_rev_updated != p1_rev

    p1_after = _req(svc, 'GET', f'/api/profiles/{p1_id}', cookie=cookie)
    assert _body(p1_after)['profile']['consumption']['annual_kwh'] == 3600.0
    assert _body(p1_after)['profile']['consumption']['monthly_totals'] == [300.0] * 12

    # Reopen / restart service with same persistent database
    store_reopened = TenantStore(tmp_path / 'store')
    svc_reopened = create_service({'allowed_hosts': ['localhost:8765']}, store_reopened, FakePool())

    # Session remains active
    sess_res = _req(svc_reopened, 'GET', '/api/session', cookie=cookie)
    assert sess_res.status == 200

    # Reselect all 3 profiles after reopen/restart
    r_p1 = _req(svc_reopened, 'GET', f'/api/profiles/{p1_id}', cookie=cookie)
    assert r_p1.status == 200
    assert _body(r_p1)['revision'] == p1_rev_updated
    assert _body(r_p1)['profile']['consumption']['annual_kwh'] == 3600.0
    assert _body(r_p1)['profile']['consumption']['monthly_totals'] == [300.0] * 12

    r_p2 = _req(svc_reopened, 'GET', f'/api/profiles/{p2_id}', cookie=cookie)
    assert r_p2.status == 200
    assert _body(r_p2)['revision'] == p2_rev
    assert _body(r_p2)['profile']['consumption']['annual_kwh'] == 4800.0
    assert _body(r_p2)['profile']['consumption']['monthly_totals'] == months

    r_p3 = _req(svc_reopened, 'GET', f'/api/profiles/{p3_id}', cookie=cookie)
    assert r_p3.status == 200
    assert _body(r_p3)['revision'] == p3_rev
    assert 'annual_kwh' not in _body(r_p3)['profile']['consumption']
    assert 'monthly_totals' not in _body(r_p3)['profile']['consumption']

    # Invalid input testing (negative, non-finite, bool, invalid sequence length, non-numeric)
    for bad_consumption in [
        {'kind': 'annual', 'annual_kwh': -100.0},
        {'kind': 'annual', 'annual_kwh': True},
        {'kind': 'annual', 'annual_kwh': 'invalid_string'},
        {'kind': 'monthly', 'monthly_totals': [100.0] * 11},
        {'kind': 'monthly', 'monthly_totals': [100.0] * 13},
        {'kind': 'monthly', 'monthly_totals': [100.0] * 11 + [-10.0]},
        {'kind': 'monthly', 'monthly_totals': [100.0] * 11 + [True]},
        {'kind': 'monthly', 'monthly_totals': 'not_a_list'},
        {'kind': 'annual', 'unexpected_consumption_key': 123},
    ]:
        bad_payload = deepcopy(base)
        bad_payload['consumption'].update(bad_consumption)
        res_bad = _req(svc, 'POST', '/api/profiles', cookie=cookie, csrf=csrf, body={'profile': bad_payload})
        assert res_bad.status == 422



def test_solar_job_timeout_error_exposed(tmp_path):
    svc = _svc(tmp_path)
    r_sess = _req(svc, 'POST', '/api/session', body={})
    assert r_sess.status == 201
    cookie = _cookie(r_sess)
    csrf = _body(r_sess)['csrf_token']
    tenant_id = _body(r_sess)['tenant_id']

    r_prof = _req(svc, 'POST', '/api/profiles', cookie=cookie, csrf=csrf, body={'name': 'Timeout Test'})
    assert r_prof.status == 201
    prof_id = _body(r_prof)['id']
    rev = _body(r_prof)['revision']

    job = svc.store.create_job(tenant_id, prof_id, rev, '0' * 64)
    svc.store.job_directory(tenant_id, job['id'])
    with svc.store.files._directory(('tenants', tenant_id, 'jobs', job['id'])) as fd:
        svc.store.files._write_once(fd, 'candidate.status.json',
                                    json.dumps({'state': 'failed', 'error': 'TimeoutError', 'activated': False}).encode())
    svc.store.fail_job(tenant_id, job['id'])

    r_job = _req(svc, 'GET', f"/api/jobs/{job['id']}", cookie=cookie)
    assert r_job.status == 200
    data = _body(r_job)
    assert data['state'] == 'failed'
    assert data['error'] == 'Solar data request timed out; retry later.'
    assert data['error_code'] == 'SOLAR_TIMEOUT'
    assert 'TimeoutError' not in json.dumps(data)
    assert 'tenants' not in json.dumps(data)

    r_query = _req(svc, 'GET', f"/api/job?id={job['id']}", cookie=cookie)
    assert r_query.status == 200
    q_data = _body(r_query)
    assert q_data['error'] == 'Solar data request timed out; retry later.'
    assert q_data['error_code'] == 'SOLAR_TIMEOUT'


def test_solar_job_network_error_exposed(tmp_path):
    svc = _svc(tmp_path)
    r_sess = _req(svc, 'POST', '/api/session', body={})
    assert r_sess.status == 201
    cookie = _cookie(r_sess)
    csrf = _body(r_sess)['csrf_token']
    tenant_id = _body(r_sess)['tenant_id']

    r_prof = _req(svc, 'POST', '/api/profiles', cookie=cookie, csrf=csrf, body={'name': 'Network Test'})
    assert r_prof.status == 201
    prof_id = _body(r_prof)['id']
    rev = _body(r_prof)['revision']

    for error_type in ('ConnectionError', 'OSError', 'HTTPException', 'ConnectionRefusedError'):
        job = svc.store.create_job(tenant_id, prof_id, rev, '0' * 64)
        svc.store.job_directory(tenant_id, job['id'])
        with svc.store.files._directory(('tenants', tenant_id, 'jobs', job['id'])) as fd:
            svc.store.files._write_once(fd, 'candidate.status.json',
                                        json.dumps({'state': 'failed', 'error': error_type, 'activated': False}).encode())
        svc.store.fail_job(tenant_id, job['id'])

        r_job = _req(svc, 'GET', f"/api/jobs/{job['id']}", cookie=cookie)
        assert r_job.status == 200
        data = _body(r_job)
        assert data['state'] == 'failed'
        assert data['error'] == 'Solar data request network error; retry later.'
        assert data['error_code'] == 'SOLAR_NETWORK_ERROR'
        assert error_type not in json.dumps(data)
        assert 'tenants' not in json.dumps(data)

        r_query = _req(svc, 'GET', f"/api/job?id={job['id']}", cookie=cookie)
        assert r_query.status == 200
        q_data = _body(r_query)
        assert q_data['error'] == 'Solar data request network error; retry later.'
        assert q_data['error_code'] == 'SOLAR_NETWORK_ERROR'


def test_solar_job_unknown_error_not_leaked(tmp_path):
    svc = _svc(tmp_path)
    r_sess = _req(svc, 'POST', '/api/session', body={})
    assert r_sess.status == 201
    cookie = _cookie(r_sess)
    csrf = _body(r_sess)['csrf_token']
    tenant_id = _body(r_sess)['tenant_id']

    r_prof = _req(svc, 'POST', '/api/profiles', cookie=cookie, csrf=csrf, body={'name': 'Unknown Error Test'})
    assert r_prof.status == 201
    prof_id = _body(r_prof)['id']
    rev = _body(r_prof)['revision']

    job = svc.store.create_job(tenant_id, prof_id, rev, '0' * 64)
    svc.store.job_directory(tenant_id, job['id'])
    with svc.store.files._directory(('tenants', tenant_id, 'jobs', job['id'])) as fd:
        svc.store.files._write_once(fd, 'candidate.status.json',
                                    json.dumps({'state': 'failed', 'error': 'RuntimeErrorInternalLeak', 'activated': False}).encode())
    svc.store.fail_job(tenant_id, job['id'])

    r_job = _req(svc, 'GET', f"/api/jobs/{job['id']}", cookie=cookie)
    assert r_job.status == 200
    data = _body(r_job)
    assert data['state'] == 'failed'
    assert 'error_code' not in data
    assert 'RuntimeErrorInternalLeak' not in json.dumps(data)
    assert 'tenants' not in json.dumps(data)


def test_solar_job_data_gaps_error_exposed(tmp_path):
    svc = _svc(tmp_path)
    r_sess = _req(svc, 'POST', '/api/session', body={})
    assert r_sess.status == 201
    cookie = _cookie(r_sess)
    csrf = _body(r_sess)['csrf_token']
    tenant_id = _body(r_sess)['tenant_id']

    r_prof = _req(svc, 'POST', '/api/profiles', cookie=cookie, csrf=csrf, body={'name': 'Data Gap Test'})
    assert r_prof.status == 201
    prof_id = _body(r_prof)['id']
    rev = _body(r_prof)['revision']

    job = svc.store.create_job(tenant_id, prof_id, rev, '0' * 64)
    svc.store.job_directory(tenant_id, job['id'])
    with svc.store.files._directory(('tenants', tenant_id, 'jobs', job['id'])) as fd:
        svc.store.files._write_once(fd, 'candidate.status.json',
                                    json.dumps({'state': 'failed', 'error': 'SolarDataGapError', 'activated': False}).encode())
    svc.store.fail_job(tenant_id, job['id'])

    r_job = _req(svc, 'GET', f"/api/jobs/{job['id']}", cookie=cookie)
    assert r_job.status == 200
    data = _body(r_job)
    assert data['state'] == 'failed'
    assert data['error'] == 'PVGIS data gaps exceed the supported repair limits. Review the selected weather years; no years were omitted.'
    assert data['error_code'] == 'SOLAR_DATA_GAPS'
    assert 'SolarDataGapError' not in json.dumps(data)
    assert 'tenants' not in json.dumps(data)
    assert 'boundary' not in json.dumps(data)
    assert 'consecutive' not in json.dumps(data)

    r_query = _req(svc, 'GET', f"/api/job?id={job['id']}", cookie=cookie)
    assert r_query.status == 200
    q_data = _body(r_query)
    assert q_data['error'] == 'PVGIS data gaps exceed the supported repair limits. Review the selected weather years; no years were omitted.'
    assert q_data['error_code'] == 'SOLAR_DATA_GAPS'


def test_session_pair_and_join_flow_cookie_and_csrf(tmp_path):
    svc = _svc(tmp_path)

    # First authenticated session (source tenant)
    s1 = _req(svc, 'POST', '/api/session', body={})
    assert s1.status == 201
    c1, csrf1, t1 = _cookie(s1), _body(s1)['csrf_token'], _body(s1)['tenant_id']

    # Existing session POST /api/session/pair with CSRF returns code and expires_in (300)
    r_pair = _req(svc, 'POST', '/api/session/pair', cookie=c1, csrf=csrf1, body={})
    assert r_pair.status == 200
    assert r_pair.headers.get('Cache-Control') == 'no-store'
    pair_body = _body(r_pair)
    assert 'code' in pair_body and 'expires_in' in pair_body
    code = pair_body['code']
    assert isinstance(code, str) and len(code) == 43
    assert re.fullmatch(r'[A-Za-z0-9_-]{43}', code)
    assert pair_body['expires_in'] == 300

    # Second authenticated session
    s2 = _req(svc, 'POST', '/api/session', body={})
    assert s2.status == 201
    c2, csrf2, t2 = _cookie(s2), _body(s2)['csrf_token'], _body(s2)['tenant_id']
    assert t1 != t2 and c1 != c2 and csrf1 != csrf2

    # Second session POST /api/session/join with CSRF and JSON {code}
    r_join = _req(svc, 'POST', '/api/session/join', cookie=c2, csrf=csrf2, body={'code': code})
    assert r_join.status == 200
    assert r_join.headers.get('Cache-Control') == 'no-store'
    join_body = _body(r_join)

    # Attaches to source tenant and provides new CSRF token
    assert join_body['tenant_id'] == t1
    assert 'csrf_token' in join_body
    csrf_joined = join_body['csrf_token']
    c_joined = _cookie(r_join)

    # Changes cookie (different from c2 and c1)
    assert c_joined != ''
    assert c_joined != c2
    assert c_joined != c1
    assert csrf_joined != csrf2

    # Subsequent requests with new cookie and new CSRF act as source tenant
    s_check = _req(svc, 'GET', '/api/session', cookie=c_joined)
    assert s_check.status == 200
    assert _body(s_check)['tenant_id'] == t1
    assert _body(s_check)['csrf_token'] == csrf_joined

    # Original session continues to act as source tenant
    s1_check = _req(svc, 'GET', '/api/session', cookie=c1)
    assert s1_check.status == 200
    assert _body(s1_check)['tenant_id'] == t1


def test_session_pairing_single_use_and_expiry(tmp_path):
    now = [1000.0]
    store = TenantStore(tmp_path / 'store', clock=lambda: now[0])
    svc = create_service({'allowed_hosts': ['localhost:8765']}, store, FakePool())

    # Session 1 generates code
    s1 = _req(svc, 'POST', '/api/session', body={})
    c1, csrf1 = _cookie(s1), _body(s1)['csrf_token']
    code1 = _body(_req(svc, 'POST', '/api/session/pair', cookie=c1, csrf=csrf1, body={}))['code']

    # Session 2 consumes the code at 299s (within 300s expiration window)
    now[0] = 1299.0
    s2 = _req(svc, 'POST', '/api/session', body={})
    c2, csrf2 = _cookie(s2), _body(s2)['csrf_token']
    r_join1 = _req(svc, 'POST', '/api/session/join', cookie=c2, csrf=csrf2, body={'code': code1})
    assert r_join1.status == 200

    # Single-use: consuming the same code again fails
    s3 = _req(svc, 'POST', '/api/session', body={})
    c3, csrf3 = _cookie(s3), _body(s3)['csrf_token']
    r_join_repeat = _req(svc, 'POST', '/api/session/join', cookie=c3, csrf=csrf3, body={'code': code1})
    assert r_join_repeat.status == 401

    # New pairing code expires in 300s
    code2 = _body(_req(svc, 'POST', '/api/session/pair', cookie=c1, csrf=csrf1, body={}))['code']

    # Advance clock past 300s (+301s)
    now[0] = 1299.0 + 301.0
    s4 = _req(svc, 'POST', '/api/session', body={})
    c4, csrf4 = _cookie(s4), _body(s4)['csrf_token']
    r_join_expired = _req(svc, 'POST', '/api/session/join', cookie=c4, csrf=csrf4, body={'code': code2})
    assert r_join_expired.status == 401


def test_session_pairing_invalid_codes_and_payloads(tmp_path):
    svc = _svc(tmp_path)
    s = _req(svc, 'POST', '/api/session', body={})
    c, csrf = _cookie(s), _body(s)['csrf_token']

    # Unauthenticated requests rejected
    assert _req(svc, 'POST', '/api/session/pair', body={}).status == 401
    assert _req(svc, 'POST', '/api/session/join', body={'code': 'a' * 43}).status == 401

    # Pair requires empty JSON object body
    assert _req(svc, 'POST', '/api/session/pair', cookie=c, csrf=csrf, body={'invalid': 1}).status == 400
    assert _req(svc, 'POST', '/api/session/pair', cookie=c, csrf=csrf, body=b'not json').status == 400

    # Join with invalid code formats rejected with 401 (use different IPs to avoid join rate limits)
    bad_codes = ['', 'short', 'x' * 42, 'x' * 44, '!' * 43, 12345, None]
    for idx, bad_code in enumerate(bad_codes):
        ip = f'127.0.1.{idx + 1}'
        headers = {'Host': 'localhost:8765', 'Origin': 'http://localhost:8765', 'Cookie': c, 'X-CSRF-Token': csrf}
        body = json.dumps({'code': bad_code}).encode()
        res = svc.request('POST', '/api/session/join', headers=headers, body=body, client_ip=ip)
        assert res.status == 401, f'Expected 401 for code {bad_code!r}, got {res.status}'

    # Join with non-existent code of valid length rejected with 401
    res_nonexistent = svc.request('POST', '/api/session/join', headers=headers,
                                  body=json.dumps({'code': 'A' * 43}).encode(), client_ip='127.0.2.1')
    assert res_nonexistent.status == 401

    # Join with invalid payload structures rejected with 400
    assert _req(svc, 'POST', '/api/session/join', cookie=c, csrf=csrf, body={}).status == 400
    assert _req(svc, 'POST', '/api/session/join', cookie=c, csrf=csrf, body={'code': 'a' * 43, 'extra': 1}).status == 400
    assert _req(svc, 'POST', '/api/session/join', cookie=c, csrf=csrf, body={'wrong_key': 'a' * 43}).status == 400
    assert _req(svc, 'POST', '/api/session/join', cookie=c, csrf=csrf, body=b'not json').status == 400


def test_session_pairing_csrf_and_cross_origin_guards(tmp_path):
    svc = _svc(tmp_path)
    s = _req(svc, 'POST', '/api/session', body={})
    c, csrf = _cookie(s), _body(s)['csrf_token']

    # Missing or invalid CSRF on pair
    assert _req(svc, 'POST', '/api/session/pair', cookie=c, csrf=None, body={}).status == 403
    assert _req(svc, 'POST', '/api/session/pair', cookie=c, csrf='bad-csrf-token', body={}).status == 403

    # Cross-origin and invalid Host on pair
    assert _req(svc, 'POST', '/api/session/pair', cookie=c, csrf=csrf, origin='http://attacker.invalid', body={}).status == 403
    assert _req(svc, 'POST', '/api/session/pair', cookie=c, csrf=csrf, host='attacker.invalid', body={}).status == 403

    # Missing or invalid CSRF on join
    assert _req(svc, 'POST', '/api/session/join', cookie=c, csrf=None, body={'code': 'a' * 43}).status == 403
    assert _req(svc, 'POST', '/api/session/join', cookie=c, csrf='bad-csrf-token', body={'code': 'a' * 43}).status == 403

    # Cross-origin and invalid Host on join
    assert _req(svc, 'POST', '/api/session/join', cookie=c, csrf=csrf, origin='http://attacker.invalid', body={'code': 'a' * 43}).status == 403
    assert _req(svc, 'POST', '/api/session/join', cookie=c, csrf=csrf, host='attacker.invalid', body={'code': 'a' * 43}).status == 403


def test_session_pairing_original_tenant_data_stays_isolated(tmp_path):
    svc = _svc(tmp_path)

    # Session 1 (Tenant 1) creates Profile 1 and state
    s1 = _req(svc, 'POST', '/api/session', body={})
    c1, csrf1, t1 = _cookie(s1), _body(s1)['csrf_token'], _body(s1)['tenant_id']
    rp1 = _req(svc, 'POST', '/api/profiles', cookie=c1, csrf=csrf1, body={'name': 'Profile T1'})
    p1_id, p1_rev = _body(rp1)['id'], _body(rp1)['revision']
    _req(svc, 'POST', '/api/state', cookie=c1, csrf=csrf1,
         body={'profile_id': p1_id, 'revision': p1_rev, 'settings': {'tilt': 15}})

    # Session 2 (Tenant 2) creates Profile 2 and state before joining
    s2 = _req(svc, 'POST', '/api/session', body={})
    c2, csrf2, t2 = _cookie(s2), _body(s2)['csrf_token'], _body(s2)['tenant_id']
    rp2 = _req(svc, 'POST', '/api/profiles', cookie=c2, csrf=csrf2, body={'name': 'Profile T2'})
    p2_id, p2_rev = _body(rp2)['id'], _body(rp2)['revision']
    _req(svc, 'POST', '/api/state', cookie=c2, csrf=csrf2,
         body={'profile_id': p2_id, 'revision': p2_rev, 'settings': {'tilt': 25}})

    # Session 3 (Tenant 3) creates Profile 3 (unrelated third party)
    s3 = _req(svc, 'POST', '/api/session', body={})
    c3, csrf3, t3 = _cookie(s3), _body(s3)['csrf_token'], _body(s3)['tenant_id']
    rp3 = _req(svc, 'POST', '/api/profiles', cookie=c3, csrf=csrf3, body={'name': 'Profile T3'})
    p3_id = _body(rp3)['id']

    # Before pairing: Tenant 2 cannot access Profile 1
    assert _req(svc, 'GET', f'/api/profiles/{p1_id}', cookie=c2).status == 404
    assert _req(svc, 'GET', f'/api/state?profile_id={p1_id}', cookie=c2).status == 404

    # Pair Session 1 and join Session 2
    r_pair = _req(svc, 'POST', '/api/session/pair', cookie=c1, csrf=csrf1, body={})
    code = _body(r_pair)['code']
    r_join = _req(svc, 'POST', '/api/session/join', cookie=c2, csrf=csrf2, body={'code': code})
    c_joined = _cookie(r_join)

    # Joined session (now Tenant 1) accesses Profile 1
    assert _req(svc, 'GET', f'/api/profiles/{p1_id}', cookie=c_joined).status == 200
    st1 = _req(svc, 'GET', f'/api/state?profile_id={p1_id}', cookie=c_joined)
    assert st1.status == 200 and _body(st1)['settings']['tilt'] == 15

    # Original Tenant 2 data is isolated and NOT accessible under Tenant 1 session
    assert _req(svc, 'GET', f'/api/profiles/{p2_id}', cookie=c_joined).status == 404
    assert _req(svc, 'GET', f'/api/state?profile_id={p2_id}', cookie=c_joined).status == 404

    # Third tenant data is isolated from joined session
    assert _req(svc, 'GET', f'/api/profiles/{p3_id}', cookie=c_joined).status == 404

    # Third tenant cannot access Tenant 1 or Tenant 2 data
    assert _req(svc, 'GET', f'/api/profiles/{p1_id}', cookie=c3).status == 404
    assert _req(svc, 'GET', f'/api/profiles/{p2_id}', cookie=c3).status == 404


def test_session_pairing_access_to_saved_profile_and_results_persists(tmp_path):
    from feasibility.runtime_context import model_build_digest
    root = tmp_path / 'persist_store'
    store1 = TenantStore(root=root)
    svc1 = create_service({'allowed_hosts': ['localhost:8765']}, store1, FakePool())

    # Tenant 1 sets up profile, state and job result
    s1 = _req(svc1, 'POST', '/api/session', body={})
    c1, csrf1, t1 = _cookie(s1), _body(s1)['csrf_token'], _body(s1)['tenant_id']
    rp = _req(svc1, 'POST', '/api/profiles', cookie=c1, csrf=csrf1, body={'name': 'Production Roof'})
    p_id, rev = _body(rp)['id'], _body(rp)['revision']

    r_state = _req(svc1, 'POST', '/api/state', cookie=c1, csrf=csrf1,
                   body={'profile_id': p_id, 'revision': rev, 'settings': {'system_size': 8.5}})
    assert r_state.status == 200
    rev = _body(r_state)['revision']

    # Publish completed job result
    job = store1.create_job(t1, p_id, rev, '0' * 64)
    result_payload = {'model_build_id': model_build_digest(), 'annual_kwh': 9400, 'co2_avoided': 4.2}
    store1.publish_result(t1, job['id'], result_payload)

    # Session 2 pairs with Tenant 1
    code = _body(_req(svc1, 'POST', '/api/session/pair', cookie=c1, csrf=csrf1, body={}))['code']
    s2 = _req(svc1, 'POST', '/api/session', body={})
    c2, csrf2 = _cookie(s2), _body(s2)['csrf_token']
    r_join = _req(svc1, 'POST', '/api/session/join', cookie=c2, csrf=csrf2, body={'code': code})
    c_joined = _cookie(r_join)
    csrf_joined = _body(r_join)['csrf_token']

    # Verify access before recreation
    assert _req(svc1, 'GET', f'/api/profiles/{p_id}', cookie=c_joined).status == 200
    assert _req(svc1, 'GET', f'/api/job?id={job["id"]}', cookie=c_joined).status == 200

    # Simulate full restart / store and service recreation
    store2 = TenantStore(root=root)
    svc2 = create_service({'allowed_hosts': ['localhost:8765']}, store2, FakePool())

    # Joined session (Session 2) persists and retains full access to profile, state, and results
    sess_res = _req(svc2, 'GET', '/api/session', cookie=c_joined)
    assert sess_res.status == 200
    assert _body(sess_res)['tenant_id'] == t1
    csrf_recreated = _body(sess_res)['csrf_token']
    assert isinstance(csrf_recreated, str) and len(csrf_recreated) == 64

    prof_res = _req(svc2, 'GET', f'/api/profiles/{p_id}', cookie=c_joined)
    assert prof_res.status == 200
    assert _body(prof_res)['id'] == p_id
    assert _body(prof_res)['revision'] == rev

    list_res = _req(svc2, 'GET', '/api/profiles', cookie=c_joined)
    assert list_res.status == 200
    assert any(p['id'] == p_id and p['name'] == 'Production Roof' for p in _body(list_res)['profiles'])

    state_res = _req(svc2, 'GET', f'/api/state?profile_id={p_id}', cookie=c_joined)
    assert state_res.status == 200
    assert _body(state_res)['settings']['system_size'] == 8.5

    job_res = _req(svc2, 'GET', f'/api/job?id={job["id"]}', cookie=c_joined)
    assert job_res.status == 200
    assert _body(job_res)['state'] == 'complete'

    # Store-level result persistence check
    assert store2.read_result(t1, job['id'])['annual_kwh'] == 9400

    # Original Session 1 session also remains functional across recreation
    assert _req(svc2, 'GET', f'/api/profiles/{p_id}', cookie=c1).status == 200
    assert _req(svc2, 'GET', f'/api/job?id={job["id"]}', cookie=c1).status == 200

def test_compiled_candidate_metadata_and_solar_coverage_cache(tmp_path, monkeypatch):
    from tests.test_profiles import valid_draft_payload
    from feasibility import solar_provider
    from feasibility.solar_provider import TenantCache
    svc = _svc(tmp_path)
    session = _req(svc, 'POST', '/api/session', body={})
    cookie, csrf = _cookie(session), session.body['csrf_token']
    created = _req(svc, 'POST', '/api/profiles', cookie=cookie, csrf=csrf,
                   body={'profile': valid_draft_payload.__wrapped__()}).body
    profile_id = created['id']

    # Draft profile has no compiled candidate
    val = _req(svc, 'GET', f'/api/profiles/{profile_id}/validation', cookie=cookie, csrf=csrf)
    assert val.body.get('compiled_candidate') is None
    prof = _req(svc, 'GET', f'/api/profiles/{profile_id}', cookie=cookie, csrf=csrf)
    assert prof.body.get('compiled_candidate') is None

    # Solar coverage passes TenantCache
    cache_calls = []
    monkeypatch.setattr(solar_provider, 'check_solar_coverage',
                        lambda profile, transport=None, cache=None: cache_calls.append(cache) or {'supported': True, 'years': [2020, 2021]})
    cov_path = f'/api/profiles/{profile_id}/solar-coverage'
    headers = {'Host': 'localhost:8765', 'Origin': 'http://localhost:8765',
               'Cookie': cookie, 'X-CSRF-Token': csrf, 'If-Match': created['revision']}
    res = svc.request('POST', cov_path, headers=headers,
                      body=json.dumps({'consent': True, 'revision': created['revision']}).encode())
    assert res.status == 200
    assert len(cache_calls) == 1
    assert isinstance(cache_calls[0], TenantCache)
    assert cache_calls[0].directory.name == 'solar-cache'
