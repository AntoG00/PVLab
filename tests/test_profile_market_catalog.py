import calendar
import io
import json
import re
import zipfile
from copy import deepcopy
from pathlib import Path
import pytest

from feasibility.tenant_store import TenantStore
from feasibility.api import create_service
from feasibility.runtime_context import resolve_profile_prices
from tests.test_gme_ingest import day_xml
from tests.test_profiles import valid_draft_payload


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


def _svc(tmp_path, catalog_root=None):
    store = TenantStore(tmp_path / 'store')
    config = {'allowed_hosts': ['localhost:8765']}
    if catalog_root:
        config['catalog_root'] = str(catalog_root)
    return create_service(config, store, FakePool())


def _req(svc, method, path, *, cookie=None, csrf=None, origin='http://localhost:8765', host='localhost:8765', body=b'', headers=None):
    hdrs = {'Host': host}
    if cookie:
        hdrs['Cookie'] = cookie
    if origin is not None:
        hdrs['Origin'] = origin
    if csrf is not None:
        hdrs['X-CSRF-Token'] = csrf
    if headers:
        hdrs.update(headers)
    if isinstance(body, (dict, list)):
        body = json.dumps(body).encode('utf-8')
    return svc.request(method, path, headers=hdrs, body=body)


def _client(svc):
    res = _req(svc, 'POST', '/api/session', body={})
    assert res.status == 201
    return _cookie(res), _body(res)['csrf_token']


def _create_profile(svc, cookie, csrf, name='Test Profile'):
    p = deepcopy(valid_draft_payload.__wrapped__())
    p['market']['valuation_mode'] = 'flat'
    res = _req(svc, 'POST', '/api/profiles', cookie=cookie, csrf=csrf, body={'name': name, 'profile': p})
    assert res.status == 201
    return _body(res)['id']


def _make_month_zip(year, month):
    num_days = calendar.monthrange(year, month)[1]
    members = {}
    for d in range(1, num_days + 1):
        day_str = f"{year:04d}-{month:02d}-{d:02d}"
        file_name = f"{year:04d}{month:02d}{d:02d}MGPPrezzi.xml"
        members[file_name] = day_xml(day_str)
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name, content in members.items():
            z.writestr(name, content)
    return bio.getvalue()


def test_market_catalog_get_status_fallback_and_none(tmp_path):
    svc = _svc(tmp_path)
    c, csrf = _client(svc)
    pid = _create_profile(svc, c, csrf)
    res = _req(svc, 'GET', f'/api/profiles/{pid}/market-catalog', cookie=c)
    assert res.status == 200
    assert _body(res)['catalog'] is None

    from feasibility.prepare_market_prices import ingest_mgp_archive
    from feasibility.market_catalog import update_catalog
    admin_root = Path('/var/tmp') / 'pv-lab-test' / f"admin-{tmp_path.name}"
    admin_root.mkdir(parents=True, exist_ok=True)
    ingest = ingest_mgp_archive(_make_month_zip(2025, 1))
    update_catalog(admin_root, ingest, publish=True)

    svc_admin = _svc(tmp_path, catalog_root=admin_root)
    c2, csrf2 = _client(svc_admin)
    pid2 = _create_profile(svc_admin, c2, csrf2)
    res_admin = _req(svc_admin, 'GET', f'/api/profiles/{pid2}/market-catalog', cookie=c2)
    assert res_admin.status == 200
    cat = _body(res_admin)['catalog']
    assert cat is not None
    assert cat['coverage'] == ['2025-01-01', '2025-02-01']
    assert cat['shape_available']['NORD'] is False
    assert not any(cat['shape_available'].values())
    assert '2025-02' in cat['missing_months']


def test_market_catalog_upload_single_month_and_status(tmp_path):
    svc = _svc(tmp_path)
    c, csrf = _client(svc)
    pid = _create_profile(svc, c, csrf)

    zip_bytes = _make_month_zip(2025, 1)
    res = _req(svc, 'POST', f'/api/profiles/{pid}/market-catalog',
               cookie=c, csrf=csrf,
               headers={'X-Filename': 'MGP_Prezzi_202501.zip'},
               body=zip_bytes)
    assert res.status == 200
    data = _body(res)
    cat = data['catalog']
    assert cat is not None
    assert cat['coverage'] == ['2025-01-01', '2025-02-01']
    assert cat['series'] == ['NORD', 'CNOR', 'CSUD', 'SUD', 'CALA', 'SICI', 'SARD', 'PUN']
    assert cat['shape_available']['NORD'] is False
    assert not any(cat['shape_available'].values())
    expected_missing = [f"2025-{m:02d}" for m in range(2, 13)]
    assert cat['missing_months'] == expected_missing

    prof_res = _req(svc, 'GET', f'/api/profiles/{pid}', cookie=c)
    prof_doc = _body(prof_res)['profile']
    assert prof_doc['market']['price_revision'] is None
    assert prof_doc['market']['valuation_mode'] == 'flat'

    get_res = _req(svc, 'GET', f'/api/profiles/{pid}/market-catalog', cookie=c)
    assert get_res.status == 200
    assert _body(get_res)['catalog']['revision'] == cat['revision']

    schema_res = _req(svc, 'GET', f'/api/schema?profile_id={pid}', cookie=c)
    assert schema_res.status == 200
    assert _body(schema_res)['catalog']['revision'] == cat['revision']


def test_market_catalog_partial_month_marked_missing(tmp_path):
    svc = _svc(tmp_path)
    c, csrf = _client(svc)
    pid = _create_profile(svc, c, csrf)

    # Ingest a partial January (only first 10 days)
    members = {
        f"202501{d:02d}MGPPrezzi.xml": day_xml(f"2025-01-{d:02d}")
        for d in range(1, 11)
    }
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, 'w') as z:
        for name, content in members.items():
            z.writestr(name, content)

    res = _req(svc, 'POST', f'/api/profiles/{pid}/market-catalog',
               cookie=c, csrf=csrf,
               headers={'X-Filename': 'MGP_202501_partial.zip'},
               body=bio.getvalue())
    assert res.status == 200
    cat = _body(res)['catalog']
    # 2025-01 only has 10 days out of 31, so it must be marked missing!
    assert '2025-01' in cat['missing_months']
    assert cat['shape_available']['NORD'] is False


def test_market_catalog_incremental_uploads_merge(tmp_path):
    svc = _svc(tmp_path)
    c, csrf = _client(svc)
    pid = _create_profile(svc, c, csrf)

    res1 = _req(svc, 'POST', f'/api/profiles/{pid}/market-catalog',
                cookie=c, csrf=csrf,
                headers={'X-Filename': 'MGP_Prezzi_202501.zip'},
                body=_make_month_zip(2025, 1))
    assert res1.status == 200
    cat1 = _body(res1)['catalog']
    assert cat1['coverage'] == ['2025-01-01', '2025-02-01']
    assert '2025-02' in cat1['missing_months']

    res2 = _req(svc, 'POST', f'/api/profiles/{pid}/market-catalog',
                cookie=c, csrf=csrf,
                headers={'X-Filename': 'MGP_Prezzi_202502.zip'},
                body=_make_month_zip(2025, 2))
    assert res2.status == 200
    cat2 = _body(res2)['catalog']
    assert cat2['coverage'] == ['2025-01-01', '2025-03-01']
    assert '2025-02' not in cat2['missing_months']
    assert '2025-03' in cat2['missing_months']
    assert cat2['revision'] != cat1['revision']


def test_market_catalog_skipped_month_rejected_with_diagnostic(tmp_path):
    svc = _svc(tmp_path)
    c, csrf = _client(svc)
    pid = _create_profile(svc, c, csrf)

    # 1. Upload Jan 2025
    r1 = _req(svc, 'POST', f'/api/profiles/{pid}/market-catalog',
              cookie=c, csrf=csrf,
              headers={'X-Filename': 'MGP_202501.zip'},
              body=_make_month_zip(2025, 1))
    assert r1.status == 200

    # 2. Try uploading March 2025 (skipping Feb) -> should fail with actionable diagnostic
    r_skip = _req(svc, 'POST', f'/api/profiles/{pid}/market-catalog',
                  cookie=c, csrf=csrf,
                  headers={'X-Filename': 'MGP_202503.zip'},
                  body=_make_month_zip(2025, 3))
    assert r_skip.status == 422
    err_body = _body(r_skip)
    assert err_body['code'] == 'DELIVERY_DATES_NONCONTIGUOUS'
    assert 'contiguous' in err_body['error'].lower()
    assert err_body['diagnostic']['code'] == 'DELIVERY_DATES_NONCONTIGUOUS'


def test_market_catalog_full_year_readiness(tmp_path):
    svc = _svc(tmp_path)
    c, csrf = _client(svc)
    pid = _create_profile(svc, c, csrf)

    # Construct complete 12 months for 2025
    all_members = {}
    for m in range(1, 13):
        days_in_m = calendar.monthrange(2025, m)[1]
        for d in range(1, days_in_m + 1):
            all_members[f"2025{m:02d}{d:02d}MGPPrezzi.xml"] = day_xml(f"2025-{m:02d}-{d:02d}")
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name, content in all_members.items():
            z.writestr(name, content)

    res = _req(svc, 'POST', f'/api/profiles/{pid}/market-catalog',
               cookie=c, csrf=csrf,
               headers={'X-Filename': 'MGP_2025_full.zip'},
               body=bio.getvalue())
    assert res.status == 200
    cat = _body(res)['catalog']
    assert cat['coverage'] == ['2025-01-01', '2026-01-01']
    assert cat['missing_months'] == []
    assert cat['shape_available']['NORD'] is True
    assert all(cat['shape_available'].values())


def test_market_catalog_invalid_zip_and_guards(tmp_path):
    svc = _svc(tmp_path)
    c, csrf = _client(svc)
    pid = _create_profile(svc, c, csrf)

    valid_zip = _make_month_zip(2025, 1)

    r_no_fn = _req(svc, 'POST', f'/api/profiles/{pid}/market-catalog',
                   cookie=c, csrf=csrf, body=valid_zip)
    assert r_no_fn.status == 400

    r_bad_ext = _req(svc, 'POST', f'/api/profiles/{pid}/market-catalog',
                     cookie=c, csrf=csrf,
                     headers={'X-Filename': 'prices.csv'}, body=valid_zip)
    assert r_bad_ext.status == 400

    r_corrupt = _req(svc, 'POST', f'/api/profiles/{pid}/market-catalog',
                     cookie=c, csrf=csrf,
                     headers={'X-Filename': 'test.zip'}, body=b'not a zip file')
    assert r_corrupt.status == 422
    assert _body(r_corrupt)['code'] in ('INVALID_ZIP_ARCHIVE', 'PARSE_ERROR')

    empty_bio = io.BytesIO()
    with zipfile.ZipFile(empty_bio, 'w') as z:
        z.writestr('readme.txt', 'empty')
    r_empty = _req(svc, 'POST', f'/api/profiles/{pid}/market-catalog',
                   cookie=c, csrf=csrf,
                   headers={'X-Filename': 'empty.zip'}, body=empty_bio.getvalue())
    assert r_empty.status == 422
    assert _body(r_empty)['code'] == 'EMPTY_OR_INVALID_ARCHIVE'

    r_no_csrf = _req(svc, 'POST', f'/api/profiles/{pid}/market-catalog',
                     cookie=c, csrf=None,
                     headers={'X-Filename': 'test.zip'}, body=valid_zip)
    assert r_no_csrf.status == 403


def test_market_catalog_tenant_isolation_and_delete(tmp_path):
    svc = _svc(tmp_path)
    c1, csrf1 = _client(svc)
    c2, csrf2 = _client(svc)

    p1 = _create_profile(svc, c1, csrf1, name='P1')

    res = _req(svc, 'POST', f'/api/profiles/{p1}/market-catalog',
               cookie=c1, csrf=csrf1,
               headers={'X-Filename': 'MGP_Prezzi_202501.zip'},
               body=_make_month_zip(2025, 1))
    assert res.status == 200

    r_get = _req(svc, 'GET', f'/api/profiles/{p1}/market-catalog', cookie=c2)
    assert r_get.status == 404

    r_post = _req(svc, 'POST', f'/api/profiles/{p1}/market-catalog',
                  cookie=c2, csrf=csrf2,
                  headers={'X-Filename': 'MGP_Prezzi_202501.zip'},
                  body=_make_month_zip(2025, 1))
    assert r_post.status == 404

    tenant1_id = _body(_req(svc, 'GET', '/api/session', cookie=c1))['tenant_id']
    cat_dir = svc.store.profile_catalog_path(tenant1_id, p1)
    assert cat_dir.is_dir()

    del_res = _req(svc, 'POST', '/api/profiles/delete', cookie=c1, csrf=csrf1, body={'profile_id': p1})
    assert del_res.status == 200
    assert not cat_dir.exists()


def test_market_catalog_pinning_and_runtime_integration(tmp_path):
    svc = _svc(tmp_path)
    c, csrf = _client(svc)
    pid = _create_profile(svc, c, csrf)

    res1 = _req(svc, 'POST', f'/api/profiles/{pid}/market-catalog',
                cookie=c, csrf=csrf,
                headers={'X-Filename': 'MGP_Prezzi_202501.zip'},
                body=_make_month_zip(2025, 1))
    assert res1.status == 200
    rev1 = _body(res1)['catalog']['revision']

    snap_res = _req(svc, 'GET', f'/api/profiles/{pid}', cookie=c)
    snap = _body(snap_res)
    prof = snap['profile']
    prof['market'].update(
        valuation_mode='absolute_proxy',
        price_revision=rev1,
        window_start='2025-01-01',
        window_end='2025-02-01'
    )
    patch_res = _req(svc, 'PATCH', f'/api/profiles/{pid}',
                     cookie=c, csrf=csrf,
                     headers={'If-Match': snap['revision']},
                     body={'profile': prof})
    assert patch_res.status == 200

    val_res = _req(svc, 'GET', f'/api/profiles/{pid}/validation', cookie=c)
    assert val_res.status == 200
    blockers = _body(val_res)['blockers']
    assert 'PRICE_CATALOG_REQUIRED' not in blockers

    res2 = _req(svc, 'POST', f'/api/profiles/{pid}/market-catalog',
                cookie=c, csrf=csrf,
                headers={'X-Filename': 'MGP_Prezzi_202502.zip'},
                body=_make_month_zip(2025, 2))
    assert res2.status == 200
    rev2 = _body(res2)['catalog']['revision']
    assert rev2 != rev1

    tenant_id = _body(_req(svc, 'GET', '/api/session', cookie=c))['tenant_id']
    cat_dir = svc._resolve_catalog_dir(tenant_id, pid, pinned_rev=rev1)
    assert cat_dir is not None
    assert (cat_dir / 'revisions' / rev1 / 'manifest.json').is_file()

    resolved = resolve_profile_prices(prof, cat_dir)
    assert resolved['profile_market']['product'] is not None
    assert resolved['profile_market']['selection']['price_revision'] == rev1
