#!/usr/bin/env python3
"""Offline Windows/Linux release verification through native subprocesses and HTTP.

Consumption is uploaded over HTTP. Fictional solar candidate files are seeded
locally because production solar acquisition requires external providers.
Run from a source checkout with hash-locked development dependencies installed.
"""
import ctypes
import http.client
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROCESS_HANDLES = {}  # Retain identities until cleanup; never kill a reused PID.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if sys.platform == 'win32':
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

    kernel32.GetConsoleWindow.restype = wintypes.HWND
    kernel32.GetConsoleWindow.argtypes = ()

    kernel32.AllocConsole.restype = wintypes.BOOL
    kernel32.AllocConsole.argtypes = ()

    kernel32.SetConsoleCtrlHandler.restype = wintypes.BOOL
    kernel32.SetConsoleCtrlHandler.argtypes = (ctypes.c_void_p, wintypes.BOOL)

    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)

    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))

    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)

    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)

    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)

    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ('dwSize', wintypes.DWORD),
            ('cntUsage', wintypes.DWORD),
            ('th32ProcessID', wintypes.DWORD),
            ('th32DefaultHeapID', ctypes.c_void_p),
            ('th32ModuleID', wintypes.DWORD),
            ('cntThreads', wintypes.DWORD),
            ('th32ParentProcessID', wintypes.DWORD),
            ('pcPriClassBase', wintypes.LONG),
            ('dwFlags', wintypes.DWORD),
            ('szExeFile', wintypes.WCHAR * 260)
        ]

    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))

    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))


def init_windows_console():
    """Ensure a native console exists for CTRL_BREAK_EVENT in noninteractive environments."""
    if sys.platform == 'win32':
        if kernel32.GetConsoleWindow() == 0:
            if not kernel32.AllocConsole():
                raise ctypes.WinError(ctypes.get_last_error())
        if not kernel32.SetConsoleCtrlHandler(None, True):
            raise ctypes.WinError(ctypes.get_last_error())


def resolve_safe_tmp_dir():
    """Find a temporary root guaranteed to be outside any git repository."""
    for cand in [tempfile.gettempdir(), '/var/tmp', str(Path.home() / '.cache')]:
        path = Path(cand).resolve()
        if path.is_dir() and not any((p / '.git').exists() for p in (path, *path.parents)):
            return str(path)
    raise RuntimeError("No temporary directory outside repository found")


def find_free_port():
    """Find an available ephemeral port on the loopback interface."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def open_process_handle(pid):
    """Open a process handle with SYNCHRONIZE and query rights on Windows."""
    if sys.platform == 'win32':
        SYNCHRONIZE = 0x00100000
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        if pid in PROCESS_HANDLES:
            return PROCESS_HANDLES[pid]
        h = kernel32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION | 0x0001, False, pid)
        if not h:
            err = ctypes.get_last_error()
            ERROR_INVALID_PARAMETER = 87
            if err == ERROR_INVALID_PARAMETER:
                return None
            raise ctypes.WinError(err)
        PROCESS_HANDLES[pid] = h
        return h
    return None


def is_process_alive(pid, handle=None):
    """Check if process is alive without third-party dependencies."""
    if sys.platform == 'win32':
        STILL_ACTIVE = 259
        opened = False
        h = handle
        if not h:
            SYNCHRONIZE = 0x00100000
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = kernel32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            opened = True
            if not h:
                return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(h, ctypes.byref(exit_code)):
                raise ctypes.WinError(ctypes.get_last_error())
            return exit_code.value == STILL_ACTIVE
        finally:
            if opened and h:
                kernel32.CloseHandle(h)
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True


def check_process_exit(pid, handle=None, timeout=15.0):
    """Wait for process exit and return exit code."""
    if sys.platform == 'win32':
        if handle:
            WAIT_OBJECT_0 = 0
            res = kernel32.WaitForSingleObject(handle, int(timeout * 1000))
            if res != WAIT_OBJECT_0:
                raise AssertionError(f"Process {pid} did not exit within {timeout}s (wait result: {res})")
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                raise ctypes.WinError(ctypes.get_last_error())
            return exit_code.value
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not is_process_alive(pid):
            return 0
        time.sleep(0.05)
    raise AssertionError(f"Process {pid} did not exit within {timeout}s")


def get_child_pids_windows(parent_pid):
    """List child PIDs on Windows using standard kernel32 Toolhelp API with fail-closed semantics."""
    hSnapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if hSnapshot == INVALID_HANDLE_VALUE or not hSnapshot:
        raise ctypes.WinError(ctypes.get_last_error())
    children = []
    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel32.Process32FirstW(hSnapshot, ctypes.byref(pe)):
            err = ctypes.get_last_error()
            ERROR_NO_MORE_FILES = 18
            if err == ERROR_NO_MORE_FILES:
                return []
            raise ctypes.WinError(err)
        while True:
            if pe.th32ParentProcessID == parent_pid:
                children.append(pe.th32ProcessID)
            if not kernel32.Process32NextW(hSnapshot, ctypes.byref(pe)):
                err = ctypes.get_last_error()
                ERROR_NO_MORE_FILES = 18
                if err != ERROR_NO_MORE_FILES:
                    raise ctypes.WinError(err)
                break
    finally:
        kernel32.CloseHandle(hSnapshot)
    return children


def get_child_pids(parent_pid):
    """Find direct child PIDs cross-platform (Windows + Linux)."""
    if sys.platform == 'win32':
        return get_child_pids_windows(parent_pid)
    proc_children = Path(f'/proc/{parent_pid}/task/{parent_pid}/children')
    if proc_children.is_file():
        try:
            pids = [int(x) for x in proc_children.read_text().split()]
            if pids:
                return pids
        except Exception:
            pass
    children = []
    proc_dir = Path('/proc')
    if proc_dir.is_dir():
        for p in proc_dir.iterdir():
            if p.name.isdigit():
                try:
                    stat_file = p / 'stat'
                    if stat_file.is_file():
                        fields = stat_file.read_text().split(') ')[-1].split()
                        if int(fields[1]) == parent_pid:
                            children.append(int(p.name))
                except Exception:
                    pass
    return children


def is_worker_process(pid):
    """Filter out multiprocessing internal infrastructure like resource_tracker on POSIX."""
    try:
        if sys.platform != 'win32':
            cmdline = Path(f'/proc/{pid}/cmdline').read_bytes()
            if b'resource_tracker' in cmdline:
                return False
        return True
    except Exception:
        return True


def get_all_descendants(pid):
    """Find all descendants recursively."""
    descendants = set()
    queue = [pid]
    while queue:
        curr = queue.pop()
        children = get_child_pids(curr)
        for child in children:
            if child not in descendants:
                descendants.add(child)
                queue.append(child)
    return descendants


def start_server(port, data_root):
    """Launch the documented native module command without overriding its host default."""
    env = dict(os.environ)
    env['PV_DATA_ROOT'] = str(data_root)
    env['PV_PYTHON'] = sys.executable
    env['PYTHONUNBUFFERED'] = '1'
    env['PYTHONPATH'] = str(REPO_ROOT)
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0

    cmd = [sys.executable, '-X', 'faulthandler', '-m', 'feasibility.server', '--port', str(port)]

    proc = subprocess.Popen(cmd, env=env, creationflags=creationflags)
    return proc


def stop_server_graceful(proc, timeout=120.0):
    """Graceful stop using native CTRL_BREAK_EVENT on Windows, SIGTERM on POSIX."""
    if proc.poll() is not None:
        return
    if sys.platform == 'win32':
        os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
    else:
        proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
        raise AssertionError(f'Server process did not exit within {timeout}s graceful timeout')


class HttpClient:
    """Session-authenticated HTTP client tracking cookies and CSRF tokens."""
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.cookie = None
        self.csrf_token = None

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=30)
        req_headers = {
            'Host': f'{self.host}:{self.port}',
            'Origin': f'http://{self.host}:{self.port}',
            'User-Agent': 'PV-Windows-Smoke-Test',
        }
        if self.cookie:
            req_headers['Cookie'] = self.cookie
        if self.csrf_token and method in ('POST', 'PATCH', 'DELETE'):
            req_headers['X-CSRF-Token'] = self.csrf_token
        if headers:
            req_headers.update(headers)

        payload = b''
        if isinstance(body, (dict, list)):
            payload = json.dumps(body).encode('utf-8')
            req_headers.setdefault('Content-Type', 'application/json')
        elif isinstance(body, (bytes, bytearray)):
            payload = bytes(body)
        elif isinstance(body, str):
            payload = body.encode('utf-8')

        try:
            conn.request(method, path, body=payload, headers=req_headers)
            res = conn.getresponse()
            res_body = res.read()
            res_headers = dict(res.getheaders())

            set_cookie = res.getheader('Set-Cookie') or res.getheader('set-cookie')
            if set_cookie:
                m = re.search(r'pv_session=[^;]+', set_cookie)
                if m:
                    self.cookie = m.group(0)

            conn.close()
            parsed = None
            ct = res_headers.get('Content-Type', '')
            if 'application/json' in ct:
                try:
                    parsed = json.loads(res_body.decode('utf-8'))
                except Exception:
                    pass
            return res.status, parsed if parsed is not None else res_body, res_headers
        except (ConnectionRefusedError, ConnectionResetError, OSError):
            try:
                conn.close()
            except Exception:
                pass
            return 0, None, {}


def wait_job_state(client, job_id, target_state, timeout=180.0, interval=2.0):
    """Bounded job status polling with immediate API failure reporting."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        st, res, _ = client.request('GET', f'/api/jobs/{job_id}')
        if st != 200 or not isinstance(res, dict):
            time.sleep(interval)
            continue
        current_state = res.get('state')
        if current_state == target_state:
            return res
        if current_state == 'failed':
            raise AssertionError(f"Job {job_id} unexpectedly failed while waiting for '{target_state}': "
                                 f"error={res.get('error')!r}, code={res.get('error_code')!r}, "
                                 f"diagnostic={res.get('diagnostic')!r}")
        if current_state == 'cancelled' and target_state != 'cancelled':
            raise AssertionError(f"Job {job_id} was cancelled while waiting for '{target_state}'")
        time.sleep(interval)
    raise AssertionError(f"Timed out after {timeout}s waiting for job {job_id} to reach state '{target_state}'")


def run_smoke():
    init_windows_console()
    safe_base = resolve_safe_tmp_dir()
    temp_dir = tempfile.TemporaryDirectory(dir=safe_base)
    # Let TenantStore create data_root with private ACLs; do not mkdir data_root in harness
    data_root = Path(temp_dir.name) / 'data'
    if os.name != 'nt':
        os.chmod(temp_dir.name, 0o700)

    port = find_free_port()
    tracked_procs = []
    tracked_all_pids = set()

    try:
        # Step 1: Start Server #1 via launcher without --host, verifying default loopback
        proc1 = start_server(port, data_root)
        tracked_procs.append(proc1)
        client = HttpClient('127.0.0.1', port)

        # Bounded startup wait (up to 120s for cold Numba compile)
        end_start = time.monotonic() + 120.0
        ready = False
        while time.monotonic() < end_start:
            st, health, _ = client.request('GET', '/api/health')
            if st == 200 and isinstance(health, dict) and health.get('status') == 'ok':
                ready = True
                break
            time.sleep(0.1)
        assert ready, "Server #1 did not reach healthy status on 127.0.0.1 within 120s"

        # Verify host header protection against untrusted authority
        st_bad, _, _ = client.request('GET', '/api/health', headers={'Host': 'evil.com'})
        assert st_bad == 403, f"Expected 403 on invalid Host header, got {st_bad}"

        # Step 2: Authenticate session and create profile
        st_sess, sess, _ = client.request('POST', '/api/session', body={})
        assert st_sess == 201, f"Session creation failed: {st_sess}"
        client.csrf_token = sess['csrf_token']
        tenant_id = sess['tenant_id']

        st_p, prof, _ = client.request('POST', '/api/profiles', body={'name': 'Smoke Test Profile'})
        assert st_p == 201, f"Profile creation failed: {st_p}"
        profile_id = prof['id']
        revision = prof['revision']

        # Step 3: Save draft fields and patch profile
        from tests.test_profiles import valid_draft_payload
        draft = valid_draft_payload.__wrapped__()
        draft['id'] = profile_id
        draft['location']['latitude'] = 45.0
        draft['location']['longitude'] = 9.0
        draft['roofs'] = [{'id': 'plane0', 'label': 'Plane 0', 'tilt_deg': 30.0,
                           'azimuth_deg': 180.0, 'max_kwp': 5.0, 'shading_fraction': 0.1}]
        st_patch, prof_patched, _ = client.request('PATCH', f'/api/profiles/{profile_id}',
                                                   body={'profile': draft},
                                                   headers={'If-Match': revision})
        assert st_patch == 200, f"Profile patch failed: {st_patch}"
        revision = prof_patched['revision']

        # Step 4: Upload synthetic consumption dataset
        csv_bytes = b'time;energy\n2025-01-01T00:00:00+01:00;10\n2025-01-01T01:00:00+01:00;15\n'
        meta = dict(delimiter=';', decimal_separator=',',
                    columns={'timestamp': 'time', 'value': 'energy'},
                    unit='kWh', duration_seconds=3600, timezone='Europe/Rome',
                    timestamp_convention='start', demand_basis='gross')
        st_csv_up, csv_res, _ = client.request('POST', f'/api/profiles/{profile_id}/consumption',
                                               body=csv_bytes,
                                               headers={'If-Match': revision,
                                                        'X-Consumption-Metadata': json.dumps(meta)})
        assert st_csv_up == 201, f"Consumption upload failed: {st_csv_up}"
        revision = csv_res['revision']
        uploaded_artifact_id = csv_res['artifact_id']
        assert uploaded_artifact_id, "Uploaded consumption missing artifact_id"

        # Snapshot the saved draft before creating the separate offline calculation fixture.
        st_saved, saved_draft, _ = client.request('GET', f'/api/profiles/{profile_id}')
        assert st_saved == 200
        saved_profile_id = profile_id
        st_fixture, fixture, _ = client.request('POST', '/api/profiles', body={'name': 'Synthetic calculation'})
        assert st_fixture == 201

        # Seed a fictional provider candidate; the consumption upload above uses the real HTTP route.
        from tests.test_dataset_compile import inputs
        from tests.test_fiscal_policy import configure_fiscal
        from tests.test_load_projection import policy
        from feasibility.dataset_compile import compile_dataset
        from feasibility.load_projection import project_load
        from feasibility.tenant_store import TenantStore
        from feasibility.profiles import canonical_json

        store = TenantStore(data_root)
        snap = store.snapshot(tenant_id, profile_id)
        assert snap['revision'] == revision

        # Read existing manifest document to preserve profile inputs and consumption reference
        doc_raw = store.read_upload(tenant_id, snap['manifest_ref'])
        current_doc = json.loads(doc_raw.decode('utf-8'))
        assert current_doc['consumption']['artifact_id'] == uploaded_artifact_id

        profile_id, revision = fixture['id'], fixture['revision']

        # Compile synthetic dataset using the calculation profile ID
        profile_to_compile, solar, _ = inputs(1, years=(2023,))
        profile_to_compile['id'] = profile_id
        # Keep the complete fictional monthly fixture separate from the uploaded draft.
        profile_to_compile['roofs'][0]['label'] = 'Plane 0'
        configure_fiscal(profile_to_compile)
        load = project_load(None, [100] * 12, (2023,), policy())
        compiled = compile_dataset(profile_to_compile, solar, load)

        # Store the independent fictional calculation profile
        doc = {'profile': profile_to_compile, 'candidate': None, 'consumption': None}
        upload, _ = store.put_upload(tenant_id, canonical_json(doc), {'purpose': 'profile_manifest'})
        snap = store.publish(tenant_id, profile_id, revision, manifest_ref=upload['id'],
                             defaults={'factory': {'pmg_election': False}, 'overrides': {}})

        # Seed completed producer job in store
        producer = store.create_job(tenant_id, profile_id, snap['revision'], 'd' * 64)
        candidate_dir = store.job_directory(tenant_id, producer['id']) / 'candidate'
        compiled.write_candidate(candidate_dir)
        store.publish_result(tenant_id, producer['id'], {})

        # Step 6: Compile profile via HTTP
        st_comp, comp, _ = client.request('POST', f'/api/profiles/{profile_id}/compile',
                                          body={'revision': snap['revision'], 'job_id': producer['id']})
        assert st_comp == 200 and comp.get('compiled') is True, f"Compile failed: {st_comp}"
        revision = comp['revision']

        # Step 7: Complete a real calculation (evaluate) and export reports
        eval_settings = {
            'roof_panels': {'plane0': 0},
            'horizon_years': 1,
            'battery_kwh': 0,
            'battery_modules': 0
        }
        st_job, job_resp, _ = client.request('POST', '/api/jobs',
                                             body={'profile_id': profile_id, 'revision': revision,
                                                   'operation': 'evaluate', 'settings': eval_settings, 'options': {}})
        assert st_job == 202, f"Job submit failed: {st_job}"
        completed_job_id = job_resp['id']

        wait_job_state(client, completed_job_id, 'complete', timeout=180.0)

        # Validate result payload (load energy median 1200 kWh)
        st_res, res_body, _ = client.request('GET', f'/api/jobs/{completed_job_id}/result')
        assert st_res == 200 and isinstance(res_body, dict), "Job result fetch failed"
        median_load = res_body.get('energy', {}).get('load_kwh', {}).get('median')
        assert median_load is not None and abs(median_load - 1200) < 1e-6, f"Expected median load 1200, got {median_load}"

        # Export reports and verify contents
        st_html, html_bytes, _ = client.request('GET', f'/report?job_id={completed_job_id}')
        assert st_html == 200 and b'Simulation result' in html_bytes, "HTML report export failed"

        st_csv_rep, csv_rep_bytes, _ = client.request('GET', f'/report.csv?job_id={completed_job_id}')
        assert st_csv_rep == 200 and csv_rep_bytes.startswith(b'section,field,value'), "CSV report export failed"

        st_md_rep, md_rep_bytes, _ = client.request('GET', f'/report.md?job_id={completed_job_id}')
        assert st_md_rep == 200 and md_rep_bytes.startswith(b'# Household simulation result'), "Markdown export failed"

        # Step 8: Graceful stop Server #1 - snapshot descendants BEFORE stopping
        server1_descendants = set(get_all_descendants(proc1.pid))
        if sys.platform == 'win32':
            assert server1_descendants, "Completed calculation must leave a tracked worker before shutdown"
        tracked_all_pids.update(server1_descendants)
        tracked_all_pids.add(proc1.pid)

        tracked_handles_1 = {}
        for pid in [proc1.pid, *server1_descendants]:
            h = open_process_handle(pid)
            if h:
                tracked_handles_1[pid] = h

        stop_server_graceful(proc1, timeout=120.0)
        assert proc1.poll() is not None, "Server #1 did not exit"
        if sys.platform == 'win32':
            assert proc1.returncode == 0, f"Server #1 exited with non-zero code {proc1.returncode}"
        else:
            assert proc1.returncode == 0, f"Server #1 exited with unexpected code {proc1.returncode}"

        for pid in server1_descendants:
            h = tracked_handles_1.get(pid)
            check_process_exit(pid, handle=h, timeout=15.0)

        # Step 9: Start Server #2 with the same data_root, verify preserved state
        proc2 = start_server(port, data_root)
        tracked_procs.append(proc2)

        end_start2 = time.monotonic() + 120.0
        ready2 = False
        while time.monotonic() < end_start2:
            st, health, _ = client.request('GET', '/api/health')
            if st == 200 and isinstance(health, dict) and health.get('status') == 'ok':
                ready2 = True
                break
            time.sleep(0.1)
        assert ready2, "Server #2 did not reach healthy status on 127.0.0.1 within 120s"

        # Verify session preserved
        st_s2, s2_body, _ = client.request('GET', '/api/session')
        assert st_s2 == 200 and s2_body.get('tenant_id') == tenant_id, "Session not preserved"
        client.csrf_token = s2_body['csrf_token']

        # Verify profile and draft fields preserved
        st_p2, p2_body, _ = client.request('GET', f'/api/profiles/{saved_profile_id}')
        assert st_p2 == 200 and p2_body == saved_draft, 'Saved draft changed across restart'
        assert store.read_upload(tenant_id, uploaded_artifact_id) == csv_bytes, 'Uploaded CSV changed'
        prof_obj = p2_body.get('profile')
        assert prof_obj is not None, "Profile object missing in GET response"
        assert prof_obj.get('location', {}).get('latitude') == 45.0, "Profile latitude draft field not preserved"
        assert prof_obj.get('location', {}).get('longitude') == 9.0, "Profile longitude draft field not preserved"
        assert prof_obj.get('consumption', {}).get('artifact_id') == uploaded_artifact_id, "Consumption artifact not preserved"

        st_profs, profs_list, _ = client.request('GET', '/api/profiles')
        assert st_profs == 200 and isinstance(profs_list, dict), "List profiles failed"
        matched = next((p for p in profs_list.get('profiles', []) if p.get('id') == saved_profile_id), None)
        assert matched is not None, "Profile missing from profile list"
        assert matched.get('name') == 'Smoke Test Profile', "Profile name not persisted"

        # Verify completed job and report export preserved
        st_j2, j2_body, _ = client.request('GET', f'/api/jobs/{completed_job_id}')
        assert st_j2 == 200 and j2_body.get('state') == 'complete', "Completed job state changed across restart"

        st_csv2, csv2_bytes, _ = client.request('GET', f'/report.csv?job_id={completed_job_id}')
        assert st_csv2 == 200 and csv2_bytes == csv_rep_bytes, "Report CSV content altered after restart"

        # Step 10: Cancellation test - exercise real calculation (sizing)
        baseline_descendants_2 = set(get_all_descendants(proc2.pid))
        st_cjob, cjob_resp, _ = client.request(
            'POST', '/api/jobs',
            body={'profile_id': profile_id, 'revision': revision,
                  'operation': 'sizing',
                  'settings': {'roof_panels': {'plane0': 10},
                               'battery_kwh': 0, 'battery_modules': 0},
                  'options': {'search': {}}}
        )
        assert st_cjob == 202, f"Cancel job submit failed: {st_cjob} {cjob_resp}"
        cancelled_job_id = cjob_resp['id']

        # Poll until observed in active running state
        wait_job_state(client, cancelled_job_id, 'running', timeout=120.0)

        # Snapshot worker descendants BEFORE issuing cancellation DELETE
        current_descendants_2 = set(get_all_descendants(proc2.pid))
        worker_descendants = {p for p in (current_descendants_2 - baseline_descendants_2) if is_worker_process(p)}
        assert worker_descendants, "Worker process must be active while calculation is in running state"
        tracked_all_pids.update(worker_descendants)

        tracked_worker_handles = {}
        for pid in worker_descendants:
            h = open_process_handle(pid)
            if h:
                tracked_worker_handles[pid] = h

        # Cancel calculation
        st_del, del_resp, _ = client.request('DELETE', f'/api/jobs/{cancelled_job_id}')
        assert st_del == 200, f"Delete failed: {st_del}"

        # Verify job marked cancelled
        wait_job_state(client, cancelled_job_id, 'cancelled', timeout=30.0)

        # Verify all worker descendants are reaped
        for pid in worker_descendants:
            h = tracked_worker_handles.get(pid)
            check_process_exit(pid, handle=h, timeout=15.0)

        # Step 11: Graceful stop Server #2 and start Server #3 to verify job states remain immutable
        server2_descendants = set(get_all_descendants(proc2.pid))
        tracked_all_pids.update(server2_descendants)
        tracked_all_pids.add(proc2.pid)

        tracked_handles_2 = {}
        for pid in [proc2.pid, *server2_descendants]:
            h = open_process_handle(pid)
            if h:
                tracked_handles_2[pid] = h

        stop_server_graceful(proc2, timeout=120.0)
        assert proc2.poll() is not None, "Server #2 did not exit"
        if sys.platform == 'win32':
            assert proc2.returncode == 0, f"Server #2 exited with non-zero code {proc2.returncode}"
        else:
            assert proc2.returncode == 0, f"Server #2 exited with unexpected code {proc2.returncode}"

        for pid in server2_descendants:
            h = tracked_handles_2.get(pid)
            check_process_exit(pid, handle=h, timeout=15.0)

        proc3 = start_server(port, data_root)
        tracked_procs.append(proc3)

        end_start3 = time.monotonic() + 120.0
        ready3 = False
        while time.monotonic() < end_start3:
            st, health, _ = client.request('GET', '/api/health')
            if st == 200 and isinstance(health, dict) and health.get('status') == 'ok':
                ready3 = True
                break
            time.sleep(0.1)
        assert ready3, "Server #3 did not reach healthy status on 127.0.0.1 within 120s"

        # Verify completed job and cancelled job states never change
        st_chk1, chk1_body, _ = client.request('GET', f'/api/jobs/{completed_job_id}')
        assert st_chk1 == 200 and chk1_body.get('state') == 'complete', "Completed job state changed after second restart"

        st_chk2, chk2_body, _ = client.request('GET', f'/api/jobs/{cancelled_job_id}')
        assert st_chk2 == 200 and chk2_body.get('state') == 'cancelled', "Cancelled job state changed after second restart"

        st_saved, persisted, _ = client.request('GET', f'/api/profiles/{saved_profile_id}')
        assert st_saved == 200 and persisted == saved_draft, 'Saved draft changed after cancellation/restart'
        assert store.read_upload(tenant_id, uploaded_artifact_id) == csv_bytes
        st_cancel_result, _, _ = client.request('GET', f'/api/jobs/{cancelled_job_id}/result')
        assert st_cancel_result == 404, f'Cancelled result unexpectedly available: HTTP {st_cancel_result}'
        assert store.job(tenant_id, cancelled_job_id)['result_ref'] is None

        # Final clean shutdown of Server #3
        server3_descendants = set(get_all_descendants(proc3.pid))
        tracked_all_pids.update(server3_descendants)
        tracked_all_pids.add(proc3.pid)

        tracked_handles_3 = {}
        for pid in [proc3.pid, *server3_descendants]:
            h = open_process_handle(pid)
            if h:
                tracked_handles_3[pid] = h

        stop_server_graceful(proc3, timeout=120.0)
        assert proc3.poll() is not None, "Server #3 did not exit"
        if sys.platform == 'win32':
            assert proc3.returncode == 0, f"Server #3 exited with non-zero code {proc3.returncode}"
        else:
            assert proc3.returncode == 0, f"Server #3 exited with unexpected code {proc3.returncode}"

        for pid in server3_descendants:
            h = tracked_handles_3.get(pid)
            check_process_exit(pid, handle=h, timeout=15.0)

        print('Release smoke passed: saved profile/CSV persistence, dispatch (1200 kWh), HTML/CSV/Markdown exports, running-job cancellation, no surviving workers, two restarts, clean shutdown.')

    finally:
        # Snapshot live trees before removing parents, retaining Windows handles.
        cleanup_pids = set()
        for process in tracked_procs:
            if process.poll() is None:
                for pid in get_all_descendants(process.pid):
                    cleanup_pids.add(pid)
                    open_process_handle(pid)
        for process in tracked_procs:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
        if sys.platform == 'win32':
            for pid, handle in PROCESS_HANDLES.items():
                try:
                    if is_process_alive(pid, handle):
                        if not kernel32.TerminateProcess(handle, 1):
                            raise ctypes.WinError(ctypes.get_last_error())
                        check_process_exit(pid, handle, timeout=10)
                finally:
                    kernel32.CloseHandle(handle)
            PROCESS_HANDLES.clear()
        else:
            for pid in cleanup_pids:
                if is_process_alive(pid):
                    os.kill(pid, signal.SIGKILL)
        temp_dir.cleanup()


if __name__ == '__main__':
    run_smoke()
