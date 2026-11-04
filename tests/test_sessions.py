"""Capabilities, LAN cookie policy, CSRF and actual HTTP ownership routing."""
from contextlib import contextmanager
from email.message import Message
from http.cookies import SimpleCookie
import http.client
import json
import re
import threading

import pytest

from feasibility.sessions import Sessions, Forbidden, guard_mutation, token_hash
from feasibility.tenant_store import TenantStore, Unauthorized


def cookie_header(cookie):
    token = SimpleCookie(cookie)['pv_session'].value
    return {'Cookie': 'pv_session=' + token}


def test_tokens_are_random_256_bit_capabilities_and_only_hashes_are_stored(tmp_path):
    store = TenantStore(tmp_path / 'store')
    sessions = Sessions(store)
    one, cookie1 = sessions.create()
    two, cookie2 = sessions.create()
    token1 = SimpleCookie(cookie1)['pv_session'].value
    token2 = SimpleCookie(cookie2)['pv_session'].value
    assert len(token1) == 43 and token1 != token2
    assert one['tenant_id'] != two['tenant_id']
    assert sessions.authenticate(cookie_header(cookie1)) == one['tenant_id']
    assert sessions.authenticate(cookie_header(cookie2)) == two['tenant_id']
    assert 'HttpOnly' in cookie1 and 'SameSite=Lax' in cookie1 and 'Path=/' in cookie1
    assert 'Domain' not in cookie1 and 'Secure' not in cookie1 and '__Host-' not in cookie1
    assert 'Secure' in Sessions(store, secure=True)._cookie(token1)
    with store._connection() as db:
        rows = db.execute('SELECT * FROM sessions').fetchall()
        assert {r['token_hash'] for r in rows} == {token_hash(token1), token_hash(token2)}
        assert all(r['expires'] is None for r in rows)
        assert token1 not in repr([dict(r) for r in rows])
    assert token1.encode() not in store.db_path.read_bytes()


def test_expiry_revocation_and_persistence(tmp_path):
    now = [1000.0]
    store = TenantStore(tmp_path / 'store', clock=lambda: now[0])
    sessions = Sessions(store)
    _, limited = sessions.create(expires=1001.0)
    body, persistent = sessions.create()
    now[0] = 1002.0
    with pytest.raises(Unauthorized):
        sessions.authenticate(cookie_header(limited))
    now[0] += 365 * 24 * 3600
    assert sessions.authenticate(cookie_header(persistent)) == body['tenant_id']
    cleared = sessions.revoke(cookie_header(persistent))
    assert 'Max-Age=0' in cleared and 'HttpOnly' in cleared
    with pytest.raises(Unauthorized):
        sessions.authenticate(cookie_header(persistent))


@pytest.mark.parametrize('raw', ['', 'x', 'pv_session=x', 'pv_session=' + 'x' * 44,
    'pv_session=' + 'x' * 43 + '; pv_session=' + 'y' * 43,
    'pv_session="' + '../private/' * 5 + '"', 'a=' + 'x' * 9000])
def test_malformed_cookies_have_one_sanitized_error(tmp_path, raw):
    with pytest.raises(Unauthorized, match='^Session unavailable$'):
        Sessions(TenantStore(tmp_path / 'store')).authenticate({'Cookie': raw})


@pytest.mark.parametrize('host,origin', [
    ('127.0.0.1:8001', 'http://attacker.invalid:8001'),
    ('attacker.invalid:8001', 'http://attacker.invalid:8001'),
    ('127.0.0.1:8001', 'null'), ('127.0.0.1:8001', ''),
    ('127.0.0.1:8001', 'https://127.0.0.1:8001'),
    ('127.0.0.1:8001', 'http://127.0.0.1:8001/path'),
    ('127.0.0.1:8001', 'http://user@127.0.0.1:8001'),
    ('127.0.0.1:8001', 'http://127.0.0.1:8002'),
    ('127.0.0.1:8001', 'http://127.0.0.1:8001#fragment'),
])
def test_origin_and_host_rejections(host, origin):
    with pytest.raises(Forbidden):
        guard_mutation({'Host': host, 'Origin': origin}, ['127.0.0.1:8001'])


def test_same_origin_lan_and_duplicate_header_rules():
    guard_mutation({'Host': '192.0.2.10:8001', 'Origin': 'http://192.0.2.10:8001'}, ['192.0.2.10:8001'])
    guard_mutation({'Host': '[::1]:8001', 'Origin': 'http://[::1]:8001'}, ['[::1]:8001'])
    headers = Message()
    headers['Host'] = headers['Host'] = '127.0.0.1:8001'
    headers['Origin'] = 'http://127.0.0.1:8001'
    with pytest.raises(Forbidden):
        guard_mutation(headers, ['127.0.0.1:8001'])


def test_http_framing_limits_and_slow_incomplete_body(tmp_path):
    import socket
    with http_service(tmp_path) as (server, store):
        server.body_timeout = .1
        authority = f'127.0.0.1:{server.server_port}'
        for extra, body, status in [('', b'{}', 400), ('Content-Length: -1\r\n', b'', 400),
            ('Content-Length: 2\r\nContent-Length: 2\r\n', b'{}', 400),
            ('Content-Length: 2097153\r\n', b'{}', 413),
            ('Content-Length: 10\r\n', b'{', 400),
            ('Transfer-Encoding: chunked\r\nContent-Length: 2\r\n', b'{}', 400)]:
            with socket.create_connection(('127.0.0.1', server.server_port), timeout=2) as connection:
                raw = f'POST /api/session HTTP/1.1\r\nHost: {authority}\r\nOrigin: http://{authority}\r\n{extra}\r\n'.encode() + body
                connection.sendall(raw)
                response = connection.makefile('rb').readline()
                assert int(response.split()[1]) == status
    headers = {'Host': '127.0.0.1:8001', 'Origin': 'http://127.0.0.1:8001', 'Sec-Fetch-Site': 'cross-site'}
    with pytest.raises(Forbidden):
        guard_mutation(headers, ['127.0.0.1:8001'])


@contextmanager
def http_service(tmp_path):
    from http.server import ThreadingHTTPServer
    from feasibility.server import Handler, configure_tenant_storage
    store = TenantStore(tmp_path / 'store')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    configure_tenant_storage(server, store)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, store
    finally:
        server.shutdown()
        server.pool.close()
        server.server_close()
        thread.join(timeout=5)


def request(server, method, path, body=None, cookie=None, *, headers=None, raw=None):
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
    origin = f'http://127.0.0.1:{server.server_port}'
    supplied = {'Origin': origin}
    if cookie:
        supplied.update(cookie_header(cookie))
        if method != 'GET':
            status, session, _ = request(server, 'GET', '/api/session', cookie=cookie)
            if status == 200:
                supplied['X-CSRF-Token'] = session['csrf_token']
    supplied.update(headers or {})
    payload = raw if raw is not None else json.dumps(body if body is not None else {}).encode() if method == 'POST' else None
    connection.request(method, path, body=payload, headers=supplied)
    response = connection.getresponse()
    content = response.read()
    result = json.loads(content) if response.getheader('Content-Type', '').startswith('application/json') else content
    status, response_headers = response.status, dict(response.getheaders())
    connection.close()
    return status, result, response_headers


def test_http_two_browser_sessions_state_defaults_and_foreign_denial(tmp_path):
    with http_service(tmp_path) as (server, store):
        status, one, headers = request(server, 'POST', '/api/session')
        assert status == 201 and 'Set-Cookie' in headers
        cookie1 = headers['Set-Cookie']
        status, two, headers = request(server, 'POST', '/api/session')
        cookie2 = headers['Set-Cookie']
        assert status == 201 and one != two
        status, p1, _ = request(server, 'POST', '/api/profiles', {'defaults': {'rate': 1.0}}, cookie1)
        assert status == 201
        status, p2, _ = request(server, 'POST', '/api/profiles', {'defaults': {'rate': 9.0}}, cookie2)
        assert status == 201
        status, state, _ = request(server, 'GET', '/api/state?profile_id=' + p1['id'], cookie=cookie1)
        assert status == 200
        status, saved, _ = request(server, 'POST', '/api/state',
            {'profile_id': p1['id'], 'revision': state['revision'], 'settings': {'rate': 2.0}}, cookie1)
        assert status == 200 and saved['settings']['rate'] == 2.0
        status, error, _ = request(server, 'POST', '/api/state',
            {'profile_id': p1['id'], 'revision': state['revision'], 'settings': {}}, cookie1)
        assert status == 409
        foreign = [request(server, 'GET', '/api/state?profile_id=' + p1['id'], cookie=cookie2),
            request(server, 'POST', '/api/state', {'profile_id': p1['id'], 'revision': saved['revision']}, cookie2),
            request(server, 'POST', '/api/profiles/delete', {'profile_id': p1['id']}, cookie2)]
        assert all(item[0] == 404 and item[1] == {'error': 'Not found'} for item in foreign)
        status, defaults, _ = request(server, 'POST', '/api/defaults',
            {'profile_id': p1['id'], 'revision': saved['revision'], 'name': 'rate', 'value': 3.0}, cookie1)
        assert status == 200 and defaults['draft_only']
        status, _, _ = request(server, 'POST', '/api/defaults',
            {'profile_id': p1['id'], 'revision': saved['revision'], 'name': 'rate', 'value': 4.0}, cookie1)
        assert status == 409
        assert store.snapshot(two['tenant_id'], p2['id'])['defaults']['factory']['rate'] == 9.0


def test_http_csrf_auth_body_bounds_and_global_artifact_denial(tmp_path, monkeypatch):
    from feasibility import server as module
    monkeypatch.setattr(module.engine, 'evaluate', lambda *a, **k: pytest.fail('global engine reached'))
    with http_service(tmp_path) as (server, store):
        status, _, _ = request(server, 'POST', '/api/session', headers={'Origin': 'http://attacker.invalid'})
        assert status == 403
        status, _, _ = request(server, 'POST', '/api/session', headers={'Host': 'attacker.invalid', 'Origin': 'http://attacker.invalid'})
        assert status == 403
        assert request(server, 'GET', '/api/state?profile_id=' + '0' * 32)[0] == 401
        _, _, headers = request(server, 'POST', '/api/session')
        cookie = headers['Set-Cookie']
        for path in ('/report', '/report.md', '/assumptions.json', '/verification.json', '/api/schema', '/api/study'):
            assert request(server, 'GET', path, cookie=cookie)[0] == 404
        assert request(server, 'POST', '/api/evaluate', {'settings': {}}, cookie)[0] == 404
        assert request(server, 'POST', '/api/evidence/datasets', cookie=cookie,
            raw=b'{"solarPotential":{}}', headers={'X-Dataset-Filename': 'solar.json', 'X-Dataset-Source': 'google'})[0] == 400
        assert request(server, 'GET', '/api/evidence/datasets', cookie=cookie)[1]['datasets'] == []
        assert request(server, 'POST', '/api/session', raw=b'{"x":1,"x":2}')[0] == 400
        assert request(server, 'POST', '/api/session', raw=b'{"x":NaN}')[0] == 400
        assert request(server, 'POST', '/api/session', headers={'Transfer-Encoding': 'chunked'})[0] == 400
        assert request(server, 'POST', '/api/session', raw=b'{}', headers={'Content-Length': '2097153'})[0] == 413


def test_http_evidence_job_ownership_error_sanitization_and_deletion(tmp_path, monkeypatch):
    with http_service(tmp_path) as (server, store):
        _, one, headers = request(server, 'POST', '/api/session')
        cookie1 = headers['Set-Cookie']
        _, two, headers = request(server, 'POST', '/api/session')
        cookie2 = headers['Set-Cookie']
        raw = b'# PVGIS 5.3\ntime,P\n20240101:0010,0\n'
        status, uploaded, _ = request(server, 'POST', '/api/evidence/datasets', cookie=cookie1,
            raw=raw, headers={'X-Dataset-Filename': 'roof.csv', 'X-Dataset-Source': 'pvgis53', 'X-Dataset-Series': 'Roof'})
        assert status == 200
        upload_id = uploaded['dataset']['id']
        route = '/api/evidence/datasets/' + upload_id + '/download'
        assert request(server, 'GET', route, cookie=cookie1)[:2] == (200, raw)
        assert request(server, 'GET', route, cookie=cookie2)[:2] == (404, {'error': 'Not found'})
        profile = store.create_profile(one['tenant_id'])
        job = store.create_job(one['tenant_id'], profile['id'], profile['revision'], 'a' * 64)
        assert request(server, 'GET', '/api/job?id=' + job['id'], cookie=cookie2)[0] == 404
        store.fail_job(one['tenant_id'], job['id'], 'secret source path and coordinates')
        status, public_job, _ = request(server, 'GET', '/api/job?id=' + job['id'], cookie=cookie1)
        assert status == 200 and public_job['error'] == 'Computation failed'
        called = []
        store.worker_shutdown = called.append
        assert request(server, 'POST', '/api/tenant/delete', cookie=cookie1)[0] == 200
        assert called == [one['tenant_id']]
        assert request(server, 'GET', '/api/evidence/datasets', cookie=cookie1)[0] == 401
        assert request(server, 'GET', '/api/evidence/datasets', cookie=cookie2)[0] == 200
        assert not (store.root / 'tenants' / one['tenant_id']).exists()
        def fail(*args):
            raise OSError('secret source path')
        monkeypatch.setattr(store, 'uploads', fail)
        assert request(server, 'GET', '/api/evidence/datasets', cookie=cookie2)[:2] == (500, {'error': 'Storage operation failed'})


def test_sessions_pair_and_join_capabilities(tmp_path):
    store = TenantStore(tmp_path / 'store')
    sessions = Sessions(store)
    body1, cookie1 = sessions.create()
    t1 = body1['tenant_id']

    paired = sessions.pair(t1)
    assert 'code' in paired and paired['expires_in'] == 300
    code = paired['code']
    assert len(code) == 43 and re.fullmatch(r'[A-Za-z0-9_-]{43}', code)

    body_joined, cookie_joined = sessions.join(code)
    assert body_joined['tenant_id'] == t1
    assert 'HttpOnly' in cookie_joined and 'SameSite=Lax' in cookie_joined and 'Path=/' in cookie_joined
    assert cookie_joined != cookie1

    # Single-use: consuming again must raise Unauthorized
    with pytest.raises(Unauthorized):
        sessions.join(code)

    # Invalid code format raises Unauthorized
    with pytest.raises(Unauthorized):
        sessions.join('invalid_short')


def test_http_session_pairing_flow_over_network(tmp_path):
    with http_service(tmp_path) as (server, store):
        # Session 1
        status, one, h1 = request(server, 'POST', '/api/session')
        assert status == 201
        cookie1 = h1['Set-Cookie']

        # Session 1 pairs
        status, pair_data, pair_headers = request(server, 'POST', '/api/session/pair', cookie=cookie1)
        assert status == 200 and pair_headers.get('Cache-Control') == 'no-store'
        code = pair_data['code']
        assert pair_data['expires_in'] == 300

        # Session 2
        status, two, h2 = request(server, 'POST', '/api/session')
        assert status == 201
        cookie2 = h2['Set-Cookie']

        # Missing CSRF rejected
        status, _, _ = request(server, 'POST', '/api/session/join', body={'code': code}, cookie=cookie2,
                               headers={'X-CSRF-Token': 'bad-csrf'})
        assert status == 403

        # Cross-origin rejected
        status, _, _ = request(server, 'POST', '/api/session/join', body={'code': code}, cookie=cookie2,
                               headers={'Origin': 'http://attacker.invalid'})
        assert status == 403

        # Successful join
        status, join_data, join_headers = request(server, 'POST', '/api/session/join', body={'code': code}, cookie=cookie2)
        assert status == 200 and join_headers.get('Cache-Control') == 'no-store'
        assert join_data['tenant_id'] == one['tenant_id']
        cookie_joined = join_headers['Set-Cookie']
        assert cookie_joined != cookie2

        # Verify joined session over HTTP
        status, sess_check, _ = request(server, 'GET', '/api/session', cookie=cookie_joined)
        assert status == 200 and sess_check['tenant_id'] == one['tenant_id']

        # Single-use over HTTP: consuming same code again returns 401
        status, err_data, _ = request(server, 'POST', '/api/session/join', body={'code': code}, cookie=cookie2)
        assert status == 401 and err_data['error'] == 'Session unavailable'


def test_recover_profiles_cli_and_joining(tmp_path, monkeypatch, capsys):
    from feasibility import recover_profiles
    data_root = tmp_path / 'isolated_data'
    monkeypatch.setenv('PV_DATA_ROOT', str(data_root))
    store = TenantStore()

    # 1. Nonexistent name
    exit_code = recover_profiles.main(['--name', 'Missing Household'])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert 'No matching active household found' in captured.err

    # 2. Local recovery by exact profile name
    t1 = store.create_tenant()
    p1 = store.create_profile(t1, name='Unique Roof', defaults={'rate': 0.15})
    store.publish(t1, p1['id'], p1['revision'], scenario={'tilt': 25})

    exit_code = recover_profiles.main(['--name', 'Unique Roof'])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert 'One-time code' in captured.out
    code1 = captured.out.strip().splitlines()[-1]
    assert len(code1) == 43 and re.fullmatch(r'[A-Za-z0-9_-]{43}', code1)

    # 3. Generated code joining and accessing existing data
    sessions = Sessions(store)
    joined_body, joined_cookie = sessions.join(code1)
    assert joined_body['tenant_id'] == t1
    assert sessions.authenticate(cookie_header(joined_cookie)) == t1
    retrieved = store.snapshot(t1, p1['id'])
    assert retrieved['scenario'] == {'tilt': 25}
    assert retrieved['defaults'] == {'rate': 0.15}
    with pytest.raises(Unauthorized):
        sessions.join(code1)

    # 4. Ambiguity requiring tenant
    t2 = store.create_tenant()
    store.create_profile(t2, name='Shared Name')
    t3 = store.create_tenant()
    store.create_profile(t3, name='Shared Name')

    exit_code = recover_profiles.main(['--name', 'Shared Name'])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert 'Multiple tenants match; repeat with --tenant' in captured.err
    assert t2 in captured.err and t3 in captured.err

    # Disambiguation with --tenant
    exit_code = recover_profiles.main(['--name', 'Shared Name', '--tenant', t3])
    captured = capsys.readouterr()
    assert exit_code == 0
    code3 = captured.out.strip().splitlines()[-1]
    joined_t3, _ = sessions.join(code3)
    assert joined_t3['tenant_id'] == t3

    # Ambiguity resolved with wrong/non-matching tenant returns error
    exit_code = recover_profiles.main(['--name', 'Shared Name', '--tenant', 'nonexistent_tenant_id'])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert 'No matching active household found' in captured.err
