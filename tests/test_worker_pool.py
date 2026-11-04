"""Real spawned immutable runtimes plus deterministic process lifecycle checks."""
import json
import os
from pathlib import Path
import time

import pytest

from feasibility.tenant_store import TenantStore, Conflict, NotFound
from feasibility.worker_pool import WorkerPool, PoolConfig, Busy


def wait_job(store, owner, job, timeout=40):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = store.job(owner, job['id'])
        if result['state'] not in ('queued', 'running'):
            return result
        time.sleep(.05)
    raise AssertionError('Worker did not finish')


def owned_manifest(store, owner, *, monthly=100, label='Plane 0'):
    from tests.test_dataset_compile import inputs
    from tests.test_fiscal_policy import configure_fiscal
    from tests.test_load_projection import policy
    from feasibility.dataset_compile import compile_dataset
    from feasibility.load_projection import project_load
    from feasibility.runtime_context import model_build_digest
    p = store.create_profile(owner)
    profile, solar, _ = inputs(1, years=(2023,))
    profile['id'] = p['id']
    profile['roofs'][0]['label'] = label
    configure_fiscal(profile)
    load = project_load(None, [monthly]*12, (2023,), policy())
    compiled = compile_dataset(profile, solar, load)
    producer = store.create_job(owner, p['id'], p['revision'], 'a'*64)
    candidate = compiled.write_candidate(store.job_directory(owner, producer['id']) / 'candidate')
    store.publish_result(owner, producer['id'], {})
    return p, dict(kind='v2', tenant_id=owner, profile_id=p['id'], revision=p['revision'],
        model_build_id=model_build_digest(), profile=profile, candidate=str(candidate),
        dataset_digest=compiled.digest, defaults={'pmg_election': False}, catalog_root=None)


def test_real_spawn_interleaves_distinct_owned_arrays_and_immutable_revision(tmp_path):
    store = TenantStore(tmp_path / 'store')
    owners = [store.create_tenant(), store.create_tenant()]
    profiles = [owned_manifest(store, owner, monthly=m, label='<script>alert(1)</script>') for owner, m in zip(owners, (100, 200))]
    with_pool = WorkerPool(store)
    try:
        jobs = []
        for owner, (profile, manifest) in zip(owners, profiles):
            job = store.create_job(owner, profile['id'], profile['revision'], 'b'*64)
            with_pool.submit(job, manifest, 'calibration', {}, {})
            jobs.append(job)
        for owner, job in zip(owners, jobs):
            assert wait_job(store, owner, job)['state'] == 'complete'
        values = [json.loads((store.job_directory(owner, job['id']) / 'result.json').read_bytes())
                  for owner, job in zip(owners, jobs)]
        assert [v['annual_kwh'] for v in values] == [1200, 2400]
        assert len(with_pool._workers) == 2
        assert len({w['process'].pid for w in with_pool._workers.values()}) == 2
        assert all(w['process'].pid != os.getpid() for w in with_pool._workers.values())
        assert with_pool._ctx.get_start_method() == 'spawn'
        evaluations = []
        for owner, (profile, manifest) in zip(owners, profiles):
            job = store.create_job(owner, profile['id'], profile['revision'], 'c'*64)
            with_pool.submit(job, manifest, 'evaluate', {'roof_panels': {'plane0': 0},
                'horizon_years': 1, 'battery_kwh': 0, 'battery_modules': 0}, {})
            evaluations.append(job)
        for owner, job, expected in zip(owners, evaluations, (1200, 2400)):
            assert wait_job(store, owner, job)['state'] == 'complete'
            directory = store.job_directory(owner, job['id'])
            evaluated = json.loads((directory / 'result.json').read_bytes())
            assert evaluated['energy']['load_kwh']['median'] == pytest.approx(expected)
            report = (directory / 'report.html').read_bytes()
            assert b'<script>' not in report and b'&lt;script&gt;' in report
            assert evaluated['_presentation']['input_revision'] == job['profile_revision']
            assert evaluated['_presentation']['quality']['projected_output_fraction'] == 1
            assert 'pending independent' in ' '.join(evaluated['_presentation']['limitations'])
            assert (directory / 'report.md').read_text().startswith('# Household simulation result')
            assert (directory / 'report.csv').read_text().startswith('section,field,value')
        with pytest.raises(NotFound):
            store.read_result(owners[1], jobs[0]['id'])
        profile, manifest = profiles[0]
        store.publish(owners[0], profile['id'], profile['revision'], scenario={'settings': {'panel_watts': 500}})
        with pytest.raises(Conflict):
            store.read_result(owners[0], jobs[0]['id'])
    finally:
        with_pool.close()


def waiting_child(pipe, manifest):
    """Spawn target for deterministic lifecycle tests, no scientific initialization."""
    if os.name != 'nt':
        os.setsid()
    pipe.recv_bytes()
    time.sleep(30)


def pool_job(store):
    owner = store.create_tenant()
    profile = store.create_profile(owner)
    job = store.create_job(owner, profile['id'], profile['revision'], 'b'*64)
    manifest = dict(tenant_id=owner, revision=profile['revision'], model_build_id='synthetic')
    return owner, job, manifest


@pytest.mark.parametrize('failure', ['timeout', 'crash', 'memory', 'cancel', 'delete'])
def test_worker_termination_failure_and_deletion(tmp_path, monkeypatch, failure):
    from feasibility import worker_pool as module
    store = TenantStore(tmp_path / 'store')
    monkeypatch.setattr(module, '_child', waiting_child)
    cfg = PoolConfig(deadline_seconds=.5 if failure == 'timeout' else 10,
                     memory_bytes=1 if failure == 'memory' else 2*1024**3)
    pool = WorkerPool(store, cfg)
    owner, job, manifest = pool_job(store)
    try:
        pool.submit(job, manifest, 'evaluate', {}, {})
        end = time.monotonic() + 5
        while not pool._workers and time.monotonic() < end:
            time.sleep(.01)
        assert pool._workers
        if failure == 'crash':
            next(iter(pool._workers.values()))['process'].kill()
        elif failure == 'cancel':
            pool.cancel(owner, job['id'])
        elif failure == 'delete':
            store.delete_tenant(owner)
            assert not (store.root / 'tenants' / owner).exists()
            assert not pool._workers
            return
        result = wait_job(store, owner, job, timeout=5)
        assert result['state'] == ('cancelled' if failure == 'cancel' else 'failed')
        assert result['result_ref'] is None
    finally:
        pool.close()
    if failure == 'memory':
        diagnostic = store.job_directory(owner, job['id']) / 'diagnostic.json'
        assert json.loads(diagnostic.read_bytes())['error_code'] == 'COMPUTE_MEMORY'


def test_queue_caps_tenant_caps_recovery_and_invalid_configuration(tmp_path, monkeypatch):
    from feasibility import worker_pool as module
    store = TenantStore(tmp_path / 'store')
    monkeypatch.setattr(module, '_child', waiting_child)
    pool = WorkerPool(store, PoolConfig(workers=1, queue_capacity=1))
    try:
        one = pool_job(store)
        two = pool_job(store)
        three = pool_job(store)
        with pool._lock:
            pool.submit(one[1], one[2], 'evaluate', {}, {})
            with pytest.raises(Busy):
                pool.submit(two[1], two[2], 'evaluate', {}, {})
        end = time.monotonic() + 5
        while store.job(one[0], one[1]['id'])['state'] != 'running' and time.monotonic() < end:
            time.sleep(.01)
        pool.submit(two[1], two[2], 'evaluate', {}, {})
        with pytest.raises(Busy):
            pool.submit(three[1], three[2], 'evaluate', {}, {})
        with pytest.raises(Busy):
            pool.submit(one[1], one[2], 'evaluate', {}, {})
    finally:
        pool.close()
    assert store.job(one[0], one[1]['id'])['state'] == 'failed'
    assert store.job(two[0], two[1]['id'])['state'] == 'failed'
    assert store.recover_jobs() == 1
    assert store.job(three[0], three[1]['id'])['state'] == 'failed'
    for kwargs in ({'workers': 0}, {'queue_capacity': 0}, {'deadline_seconds': 0}, {'memory_bytes': 0}, {'workers': 3}):
        with pytest.raises(ValueError):
            PoolConfig(**kwargs)


def test_owned_export_labels_formula_prefixes_and_disclosures():
    import csv
    import io
    from feasibility.report import owned_report_text, owned_report_csv, profile_disclosures
    from tests.test_dataset_compile import inputs
    profile, _, _ = inputs(1, years=(2023,))
    disclosure = profile_disclosures(profile, {'projection': {'measured_training_fraction': .2,
        'synthetic_training_fraction': .8}, 'synthesized_fraction': 1.0}, 'revision', [2023])
    malicious = {'=SUM(1,1)': ' \t=HYPERLINK("x")', 'label': '<img src=x onerror=alert(1)>'}
    markdown = owned_report_text(malicious, {'input_revision': 'revision'}, disclosure)
    parsed = json.loads('\n'.join(line[4:] for line in markdown.splitlines() if line.startswith('    ')))
    assert parsed['result'] == malicious
    from feasibility.worker_pool import render_owned_result
    html_report = render_owned_result(malicious, {'input_revision': 'revision'})
    assert b'&lt;img' in html_report and b'<img' not in html_report
    assert 'Synthetic timing scenario sensitivity: pending' in markdown
    rows = list(csv.reader(io.StringIO(owned_report_csv(malicious, {}, disclosure))))
    formula = next(row for row in rows if row[1].startswith("'=SUM"))
    assert formula[2].startswith("' \t=")
    assert any('projected_output_fraction' in row[2] for row in rows)
    assert disclosure['quality'] == {'measured_training_fraction': .2,
        'synthetic_training_fraction': .8, 'projected_output_fraction': 1.0}


def test_bound_profile_only_cooling_does_not_reenter_scenario_validation(tmp_path):
    import subprocess
    import sys
    store = TenantStore(tmp_path / 'store')
    owner = store.create_tenant()
    _, manifest = owned_manifest(store, owner)
    script = """
import json,sys
from feasibility.worker_pool import _bind, _compute
from feasibility import engine
ctx=_bind(json.loads(sys.argv[1]))
assert 'baseline_cooling_kwh' not in ctx.defaults_snapshot
presentation=_compute('presentation-context',{}, {})
assert presentation['settings']['roof_panels'] == {'plane0':0}
assert presentation['settings']['pmg_election'] is False
assert presentation['settings']['deduction_eligible_confirmed'] is False
assert 'south_panels' not in presentation['settings']
assert ctx.dataset['audit']['baseline_cooling_kwh'] == ctx.household_profile['default_settings']['baseline_cooling_kwh']
first=engine.validate({'roof_panels':{'plane0':0},'battery_kwh':0,'battery_modules':0})
assert engine.validate(first) == first
cooling_settings = {'roof_panels': {'plane0': 0}, 'battery_kwh': 0, 'battery_modules': 0, 'baseline_cooling_kwh': 120}
quote_res = _compute('quote', cooling_settings, {'quote': {}})
assert 'headline_energy_flows' in quote_res
quote_ref_res = _compute('quote', cooling_settings, {'quote': {'price_basis': 'summary_candidate', 'reference_settings': cooling_settings}})
assert 'headline_energy_flows' in quote_ref_res
sizing_res = _compute('sizing', cooling_settings, {'search': {}})
assert sizing_res['status'] in ('ok', 'SCENARIO_PENDING')
assert 'baseline_cooling_kwh' not in sizing_res['best_candidate']['settings']
"""
    result = subprocess.run([sys.executable, '-c', script, json.dumps(manifest)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_legacy_report_label_is_optional_private_metadata_and_never_read_for_v2(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from feasibility import report, profile_store, runtime_context
    store = profile_store.ProfileStore(tmp_path / 'labels')
    monkeypatch.setattr(profile_store, 'ProfileStore', lambda: store)
    monkeypatch.setattr(runtime_context, 'runtime_if_initialized', lambda: SimpleNamespace(household_profile=None))
    assert report._legacy_report_label() == 'PV Lab'
    (store.root / 'legacy-report-labels.json').write_text(json.dumps({'schema_version': 1, 'place_label': 'Synthetic Place'}))
    (store.root / 'legacy-report-labels.json').chmod(0o600)
    assert report._legacy_report_label() == 'Synthetic Place'
    monkeypatch.setattr(runtime_context, 'runtime_if_initialized', lambda: SimpleNamespace(household_profile={}))
    monkeypatch.setattr(profile_store, 'ProfileStore', lambda: (_ for _ in ()).throw(AssertionError('v2 read legacy metadata')))
    assert report._legacy_report_label() == 'PV Lab'



def test_quote_and_sizing_jobs_strip_cooling_setting(tmp_path):
    store = TenantStore(tmp_path / 'store')
    owner = store.create_tenant()
    p, manifest = owned_manifest(store, owner)
    pool = WorkerPool(store)
    try:
        settings = {'roof_panels': {'plane0': 0}, 'battery_kwh': 0, 'battery_modules': 0, 'baseline_cooling_kwh': 150}
        job_q = store.create_job(owner, p['id'], p['revision'], 'd'*64)
        pool.submit(job_q, manifest, 'quote', settings, {'quote': {}})
        assert wait_job(store, owner, job_q)['state'] == 'complete'
        q_res = json.loads((store.job_directory(owner, job_q['id']) / 'result.json').read_bytes())
        assert 'baseline_cooling_kwh' not in q_res.get('result', {}).get('settings', {})

        job_s = store.create_job(owner, p['id'], p['revision'], 'e'*64)
        pool.submit(job_s, manifest, 'sizing', settings, {'search': {}})
        assert wait_job(store, owner, job_s)['state'] == 'complete'
        s_res = json.loads((store.job_directory(owner, job_s['id']) / 'result.json').read_bytes())
        assert 'baseline_cooling_kwh' not in s_res.get('best_candidate', {}).get('settings', {})
    finally:
        pool.close()


@pytest.mark.parametrize('operation,options', [
    ('monthly', {'weather_year': 2023, 'month': 6}),
    ('forecast-monthly', {'month': 6}),
])
def test_monthly_dayhour_worker_serialization(tmp_path, operation, options):
    store = TenantStore(tmp_path / 'store')
    owner = store.create_tenant()
    profile, manifest = owned_manifest(store, owner)
    pool = WorkerPool(store)
    try:
        job = store.create_job(owner, profile['id'], profile['revision'], 'd' * 64)
        settings = {'roof_panels': {'plane0': 0}, 'battery_kwh': 0, 'battery_modules': 0}
        pool.submit(job, manifest, operation, settings, options)
        assert wait_job(store, owner, job)['state'] == 'complete'
        result = json.loads((store.job_directory(owner, job['id']) / 'result.json').read_bytes())
        assert set(result['coverage']['sample_counts']) == {str(hour) for hour in range(24)}
        assert all(count > 0 for count in result['coverage']['sample_counts'].values())
    finally:
        pool.close()


def test_darwin_ru_maxrss_byte_conversion():
    from feasibility.worker_pool import _maxrss_to_bytes
    # On Linux: ru_maxrss is in KiB and converted to bytes
    assert _maxrss_to_bytes(100, platform='linux') == 100 * 1024
    assert _maxrss_to_bytes(51200, platform='linux') == 52428800
    # On Darwin: ru_maxrss is already in bytes and preserved as-is
    assert _maxrss_to_bytes(100, platform='darwin') == 100
    assert _maxrss_to_bytes(52428800, platform='darwin') == 52428800


def test_darwin_live_rss_proc_pid_rusage(monkeypatch):
    import ctypes
    import sys
    from feasibility import worker_pool as module

    monkeypatch.setattr(sys, 'platform', 'darwin')

    class FakeLibProc:
        def __init__(self, resident_size, ret=0):
            self.resident_size = resident_size
            self.ret = ret

        def proc_pid_rusage(self, pid, flavor, buf):
            if self.ret == 0:
                ctypes.cast(buf, ctypes.POINTER(module._RusageInfoV0)).contents.ri_resident_size = self.resident_size
            return self.ret

    fake_lib = FakeLibProc(resident_size=64 * 1024 * 1024)
    monkeypatch.setattr(module, '_get_libproc', lambda: fake_lib)

    # Normal query returns resident memory in bytes
    assert module._rss(9999) == 64 * 1024 * 1024

    # Process exit (ESRCH) returns 0
    fake_lib.ret = -1
    monkeypatch.setattr(ctypes, 'get_errno', lambda: 3)
    assert module._rss(9999) == 0

    # Unexpected errno raises watchdog unavailable
    monkeypatch.setattr(ctypes, 'get_errno', lambda: 22)
    with pytest.raises(RuntimeError, match='watchdog unavailable'):
        module._rss(9999)


def test_darwin_live_limit_enforcement(tmp_path, monkeypatch):
    import ctypes
    import sys
    from feasibility import worker_pool as module

    store = TenantStore(tmp_path / 'store')
    monkeypatch.setattr(module, '_child', waiting_child)
    monkeypatch.setattr(sys, 'platform', 'darwin')

    class FakeLibProc:
        def proc_pid_rusage(self, pid, flavor, buf):
            ctypes.cast(buf, ctypes.POINTER(module._RusageInfoV0)).contents.ri_resident_size = 50 * 1024 * 1024
            return 0

    monkeypatch.setattr(module, '_get_libproc', lambda: FakeLibProc())

    # Limit is 10 MB, fake Darwin live RSS reports 50 MB
    cfg = PoolConfig(deadline_seconds=10, memory_bytes=10 * 1024 * 1024)
    pool = WorkerPool(store, cfg)
    owner, job, manifest = pool_job(store)
    try:
        pool.submit(job, manifest, 'evaluate', {}, {})
        result = wait_job(store, owner, job, timeout=5)
        assert result['state'] == 'failed'
    finally:
        pool.close()


def nonreading_child(pipe, manifest):
    """Simulate a worker that cannot drain its request during startup."""
    time.sleep(5)


if os.name == 'nt':
    def test_windows_timeout_while_request_is_blocked(tmp_path, monkeypatch):
        from feasibility import worker_pool as module

        store = TenantStore(tmp_path / 'store')
        monkeypatch.setattr(module, '_child', nonreading_child)
        pool = WorkerPool(store, PoolConfig(deadline_seconds=1))
        owner, job, manifest = pool_job(store)
        try:
            pool.submit(job, manifest, 'evaluate', {}, {'payload': 'x' * (1024 * 1024)})
            result = wait_job(store, owner, job, timeout=4)
            assert result['state'] == 'failed'
            assert result['result_ref'] is None
            diagnostic = store.job_directory(owner, job['id']) / 'diagnostic.json'
            assert json.loads(diagnostic.read_bytes())['error_code'] == 'COMPUTE_TIMEOUT'
            assert not pool._workers
        finally:
            pool.close()


    def descendant_child(pipe, manifest):
        import subprocess
        import sys

        request = json.loads(pipe.recv_bytes())
        proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        marker = Path(request['destination']) / 'child-pid'
        marker.with_suffix('.tmp').write_text(str(proc.pid))
        marker.with_suffix('.tmp').replace(marker)
        proc.wait()


    @pytest.mark.parametrize('action', ['cancel', 'timeout', 'close'])
    def test_windows_termination_reaps_descendants(tmp_path, monkeypatch, action):
        import _winapi
        from feasibility import worker_pool as module

        store = TenantStore(tmp_path / 'store')
        monkeypatch.setattr(module, '_child', descendant_child)
        pool = WorkerPool(store, PoolConfig(deadline_seconds=5 if action == 'timeout' else 30))
        owner, job, manifest = pool_job(store)
        try:
            pool.submit(job, manifest, 'evaluate', {}, {})
            marker = store.job_directory(owner, job['id']) / 'child-pid'
            until = time.monotonic() + 4
            while not marker.exists() and time.monotonic() < until:
                time.sleep(.02)
            handle = _winapi.OpenProcess(0x100000, False, int(marker.read_text()))  # SYNCHRONIZE
            try:
                if action == 'cancel':
                    pool.cancel(owner, job['id'])
                elif action == 'close':
                    pool.close()
                assert _winapi.WaitForSingleObject(handle, 8000) == 0
                result = wait_job(store, owner, job, timeout=5)
                assert result['state'] == ('cancelled' if action == 'cancel' else 'failed')
                assert result['result_ref'] is None
                assert not pool._workers
            finally:
                _winapi.CloseHandle(handle)
        finally:
            pool.close()
