import json
from pathlib import Path
import pytest
from feasibility.api import create_service, get_verified_repo_url, ACTIONABLE_EXPLANATIONS
from feasibility.tenant_store import TenantStore
from feasibility.worker_pool import _sanitize_traceback, get_package_version


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
    import re
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


def test_verified_repo_url_parsing(tmp_path):
    git_dir = tmp_path / '.git'
    git_dir.mkdir(parents=True)
    cfg = git_dir / 'config'

    # Valid HTTPS origin
    cfg.write_text('[remote "origin"]\n\turl = https://github.com/owner-name/repo-project.git\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n')
    assert get_verified_repo_url(tmp_path) == 'https://github.com/owner-name/repo-project'

    # Valid SSH origin
    cfg.write_text('[remote "origin"]\n\turl = git@github.com:owner-name/repo_project\n')
    assert get_verified_repo_url(tmp_path) == 'https://github.com/owner-name/repo_project'

    # Non-GitHub host rejected
    cfg.write_text('[remote "origin"]\n\turl = https://gitlab.com/owner/repo.git\n')
    assert get_verified_repo_url(tmp_path) is None

    # Malicious injection / traversal rejected
    cfg.write_text('[remote "origin"]\n\turl = https://github.com/../../etc/passwd\n')
    assert get_verified_repo_url(tmp_path) is None

    # Missing config returns None
    assert get_verified_repo_url(tmp_path / 'nonexistent') is None


def test_traceback_sanitization_privacy():
    try:
        def inner_func():
            # simulate error with sensitive data in message and path
            raise ValueError('Secret coordinate: lat 45.12345, lon 9.67890 in /synthetic-fixture/private/file.py with token secret_token_xyz')
        inner_func()
    except Exception as exc:
        frames = _sanitize_traceback(exc)

    assert isinstance(frames, list)
    full_text = '\n'.join(frames)
    # Check that personal paths, private messages, and coordinates are stripped
    assert '/home/' not in full_text
    assert '/run/media/' not in full_text
    assert '45.12345' not in full_text
    assert '9.67890' not in full_text
    assert 'secret_token_xyz' not in full_text
    assert 'Secret coordinate' not in full_text


def test_fail_job_diagnostic_persistence(tmp_path):
    store = TenantStore(tmp_path / 'store')
    tenant_id = store.create_tenant()
    prof = store.create_profile(tenant_id)
    job = store.create_job(tenant_id, prof['id'], prof['revision'], '0' * 64)

    failure_info = {
        'job_id': job['id'],
        'operation': 'evaluate',
        'failure_type': 'EXCEPTION',
        'error_code': 'COMPUTE_EXCEPTION',
        'error_name': 'ZeroDivisionError',
        'version': get_package_version(),
        'model_build_id': 'abc123def456',
        'traceback': ['feasibility/engine.py:100 in evaluate'],
        'secret_private_token': 'DO_NOT_PERSIST_ME_12345',
    }
    store.fail_job(tenant_id, job['id'], failure_info=failure_info)

    job_db = store.job(tenant_id, job['id'])
    assert job_db['state'] == 'failed'
    assert job_db['error'] == 'Computation failed'

    diag_path = store.root / 'tenants' / tenant_id / 'jobs' / job['id'] / 'diagnostic.json'
    assert diag_path.exists()
    diag = json.loads(diag_path.read_text(encoding='utf-8'))
    assert diag['error_code'] == 'COMPUTE_EXCEPTION'
    assert diag['error_name'] == 'ZeroDivisionError'
    assert diag['job_id'] == job['id']
    assert 'secret_private_token' not in diag


def test_api_job_diagnostic_annotation(tmp_path):
    svc = _svc(tmp_path)
    r_sess = _req(svc, 'POST', '/api/session', body={})
    cookie = _cookie(r_sess)
    csrf = _body(r_sess)['csrf_token']
    tenant_id = _body(r_sess)['tenant_id']

    r_prof = _req(svc, 'POST', '/api/profiles', cookie=cookie, csrf=csrf, body={'name': 'Diag Test'})
    prof_id = _body(r_prof)['id']
    rev = _body(r_prof)['revision']

    job = svc.store.create_job(tenant_id, prof_id, rev, '1' * 64)
    private_injected_str = 'PRIVATE_API_TOKEN_9999_SECRET'
    failure_info = {
        'job_id': job['id'],
        'operation': 'study',
        'failure_type': 'EXCEPTION',
        'error_code': 'COMPUTE_EXCEPTION',
        'error_name': 'ZeroDivisionError',
        'version': get_package_version(),
        'raw_private_data': private_injected_str,
        'traceback': ['feasibility/engine.py:42 in solve'],
    }
    svc.store.fail_job(tenant_id, job['id'], failure_info=failure_info)

    r_job = _req(svc, 'GET', f"/api/jobs/{job['id']}", cookie=cookie)
    assert r_job.status == 200
    data = _body(r_job)
    assert data['state'] == 'failed'
    assert data['error_code'] == 'COMPUTE_EXCEPTION'
    assert data['error'] == ACTIONABLE_EXPLANATIONS['COMPUTE_EXCEPTION']
    assert 'diagnostic' in data
    assert data['diagnostic']['job_id'] == job['id']
    assert data['diagnostic']['error_name'] == 'ZeroDivisionError'
    assert data['diagnostic']['operation'] == 'study'
    assert data['version'] == get_package_version()
    assert 'model_build_id' in data
    assert data['diagnostic']['traceback'] == ['feasibility/engine.py:42 in solve']

    # Strict omission check: private string never appears anywhere in the response
    raw_response_text = json.dumps(data)
    assert private_injected_str not in raw_response_text


def test_api_timeout_memory_interrupted_classification(tmp_path):
    svc = _svc(tmp_path)
    r_sess = _req(svc, 'POST', '/api/session', body={})
    cookie = _cookie(r_sess)
    csrf = _body(r_sess)['csrf_token']
    tenant_id = _body(r_sess)['tenant_id']

    r_prof = _req(svc, 'POST', '/api/profiles', cookie=cookie, csrf=csrf, body={'name': 'Classify Test'})
    prof_id = _body(r_prof)['id']
    rev = _body(r_prof)['revision']

    # Timeout job: verify no hardcoded 900s
    j1 = svc.store.create_job(tenant_id, prof_id, rev, '2' * 64)
    svc.store.fail_job(tenant_id, j1['id'], failure_info={'job_id': j1['id'], 'failure_type': 'TIMEOUT', 'error_code': 'COMPUTE_TIMEOUT', 'error_name': 'WorkerDeadlineExceeded'})
    data1 = _body(_req(svc, 'GET', f"/api/jobs/{j1['id']}", cookie=cookie))
    assert data1['error_code'] == 'COMPUTE_TIMEOUT'
    assert 'timed out' in data1['error']
    assert '900s' not in data1['error']

    # Memory job: verify no hardcoded 2GB
    j2 = svc.store.create_job(tenant_id, prof_id, rev, '3' * 64)
    svc.store.fail_job(tenant_id, j2['id'], failure_info={'job_id': j2['id'], 'failure_type': 'MEMORY', 'error_code': 'COMPUTE_MEMORY', 'error_name': 'WorkerMemoryExceeded'})
    data2 = _body(_req(svc, 'GET', f"/api/jobs/{j2['id']}", cookie=cookie))
    assert data2['error_code'] == 'COMPUTE_MEMORY'
    assert 'memory limit' in data2['error']
    assert '2GB' not in data2['error']

    # Interrupted job
    j3 = svc.store.create_job(tenant_id, prof_id, rev, '4' * 64)
    with svc.store._transaction(write=True) as db:
        db.execute("UPDATE jobs SET state='failed',error='Computation interrupted' WHERE id=?", (j3['id'],))
    data3 = _body(_req(svc, 'GET', f"/api/jobs/{j3['id']}", cookie=cookie))
    assert data3['error_code'] == 'INTERRUPTED'
    assert 'interrupted' in data3['error']


def test_tenant_isolation_on_diagnostics(tmp_path):
    svc = _svc(tmp_path)
    # Tenant 1
    sess1 = _req(svc, 'POST', '/api/session', body={})
    cookie1 = _cookie(sess1)
    csrf1 = _body(sess1)['csrf_token']
    t1 = _body(sess1)['tenant_id']
    p1 = _body(_req(svc, 'POST', '/api/profiles', cookie=cookie1, csrf=csrf1, body={'name': 'T1'}))['id']
    r1 = _body(_req(svc, 'GET', f"/api/profiles/{p1}", cookie=cookie1))['revision']
    j1 = svc.store.create_job(t1, p1, r1, '5' * 64)
    svc.store.fail_job(t1, j1['id'], failure_info={'job_id': j1['id'], 'error_code': 'COMPUTE_EXCEPTION'})

    # Tenant 2
    sess2 = _req(svc, 'POST', '/api/session', body={})
    cookie2 = _cookie(sess2)

    # Tenant 2 cannot access Tenant 1's job diagnostics
    res = _req(svc, 'GET', f"/api/jobs/{j1['id']}", cookie=cookie2)
    assert res.status == 404


def test_synthetic_completion_job_diagnostic_mapping(tmp_path):
    svc = _svc(tmp_path)
    sess = _req(svc, 'POST', '/api/session', body={})
    cookie = _cookie(sess)
    csrf = _body(sess)['csrf_token']
    tenant_id = _body(sess)['tenant_id']

    prof = _body(_req(svc, 'POST', '/api/profiles', cookie=cookie, csrf=csrf, body={'name': 'Synth Test'}))
    prof_id = prof['id']
    rev = prof['revision']

    # 1. Exact known legacy frame load_projection.py:280 in project_load maps to SYNTHETIC_COMPLETION_REQUIRED
    raw_secret_error = 'missing month/hour shape cell; explicit synthetic completion required'
    job = svc.store.create_job(tenant_id, prof_id, rev, '6' * 64)
    failure_info = {
        'job_id': job['id'],
        'operation': 'solar',
        'failure_type': 'EXCEPTION',
        'error_code': 'COMPUTE_EXCEPTION',
        'error_name': 'ValueError',
        'version': get_package_version(),
        'traceback': ['feasibility/solar_candidate.py:98 in build_candidate', 'feasibility/load_projection.py:280 in project_load'],
    }
    svc.store.fail_job(tenant_id, job['id'], failure_info=failure_info)

    res = _req(svc, 'GET', f"/api/jobs/{job['id']}", cookie=cookie)
    assert res.status == 200
    data = _body(res)
    assert data['state'] == 'failed'
    assert data['error_code'] == 'SYNTHETIC_COMPLETION_REQUIRED'
    expected_msg = 'Interval training data does not cover required month/hour cells; enable explicit synthetic completion or supply complete coverage.'
    assert data['error'] == expected_msg
    assert 'diagnostic' in data
    assert data['diagnostic']['error_code'] == 'SYNTHETIC_COMPLETION_REQUIRED'
    assert data['diagnostic']['actionable_message'] == expected_msg
    assert data['diagnostic']['operation'] == 'solar'
    assert data['diagnostic']['error_name'] == 'ValueError'

    # Raw exception text is never leaked
    raw_json = json.dumps(data)
    assert raw_secret_error not in raw_json
    assert 'missing month/hour' not in raw_json


def test_unrelated_valueerror_and_arbitrary_message_excluded(tmp_path):
    svc = _svc(tmp_path)
    sess = _req(svc, 'POST', '/api/session', body={})
    cookie = _cookie(sess)
    csrf = _body(sess)['csrf_token']
    tenant_id = _body(sess)['tenant_id']

    prof = _body(_req(svc, 'POST', '/api/profiles', cookie=cookie, csrf=csrf, body={'name': 'Negative Test'}))
    prof_id = prof['id']
    rev = prof['revision']

    # Unrelated ValueError at a different line in project_load remains generic COMPUTE_EXCEPTION
    job = svc.store.create_job(tenant_id, prof_id, rev, '7' * 64)
    arbitrary_secret_msg = 'SECRET_INJECTED_ACTIONABLE_MESSAGE_SHOULD_NEVER_APPEAR'
    failure_info = {
        'job_id': job['id'],
        'operation': 'solar',
        'failure_type': 'EXCEPTION',
        'error_code': 'COMPUTE_EXCEPTION',
        'error_name': 'ValueError',
        'version': get_package_version(),
        'traceback': ['feasibility/solar_candidate.py:98 in build_candidate', 'feasibility/load_projection.py:215 in project_load'],
        'actionable_message': arbitrary_secret_msg,
    }
    svc.store.fail_job(tenant_id, job['id'], failure_info=failure_info)

    res = _req(svc, 'GET', f"/api/jobs/{job['id']}", cookie=cookie)
    assert res.status == 200
    data = _body(res)
    assert data['state'] == 'failed'
    # Must remain COMPUTE_EXCEPTION because line is 215, not exact legacy frame 280
    assert data['error_code'] == 'COMPUTE_EXCEPTION'
    assert data['error'] == ACTIONABLE_EXPLANATIONS['COMPUTE_EXCEPTION']
    assert data['diagnostic']['actionable_message'] == ACTIONABLE_EXPLANATIONS['COMPUTE_EXCEPTION']

    # Arbitrary injected actionable_message must NEVER be used or leaked
    raw_json = json.dumps(data)
    assert arbitrary_secret_msg not in raw_json
