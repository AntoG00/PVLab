"""Tests for check_public_plan parallel runner API.

Covers build_commands (serial vs parallel worker dispatch) and
cpu_set_for_cores (topology parsing, SMT sibling handling, core selection).
"""
import os
import sys
from pathlib import Path
import pytest

# Ensure repo and tools directory are in sys.path so bare regression import resolves
_REPO_ROOT = Path(__file__).resolve().parents[1]
_TOOLS_DIR = _REPO_ROOT / 'tools'
for _p in (str(_REPO_ROOT), str(_TOOLS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import check_public_plan as runner


def _make_topology(root: Path, cpu_layout: list[tuple[int, int, int]]) -> Path:
    """Populate a fake /sys/devices/system/cpu topology hierarchy.

    cpu_layout is a list of (cpu_number, package_id, core_id).
    """
    for cpu_num, pkg_id, core_id in cpu_layout:
        top = root / f"cpu{cpu_num}" / "topology"
        top.mkdir(parents=True, exist_ok=True)
        (top / "physical_package_id").write_text(f"{pkg_id}\n")
        (top / "core_id").write_text(f"{core_id}\n")
    return root


@pytest.fixture
def runner_inputs(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    tests_dir = root / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_example.js").write_text("// sample test\n")

    runtime = {
        "python": "/fake/bin/python3",
        "node": "/fake/bin/node",
        "vendor": "/fake/vendor",
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    return runtime, root, manifest_path, run_dir


# ---------------------------------------------------------------------------
# build_commands tests
# ---------------------------------------------------------------------------

def test_build_commands_returns_four_ordered_stages(runner_inputs):
    runtime, root, manifest, run = runner_inputs
    commands = runner.build_commands(runtime, root, manifest, run, workers=1)
    assert len(commands) == 4
    labels = [label for label, _ in commands]
    assert labels == ["python", "node", "acceptance", "regression"]


def test_build_commands_serial_workers_1(runner_inputs):
    runtime, root, manifest, run = runner_inputs
    commands = runner.build_commands(runtime, root, manifest, run, workers=1)
    cmd_dict = dict(commands)

    # Serial pytest: no -n
    assert "-n" not in cmd_dict["python"]
    assert "tests" in cmd_dict["python"]

    # Serial Node: concurrency 1
    assert "--test-concurrency=1" in cmd_dict["node"]
    assert "-n" not in cmd_dict["node"]


def test_build_commands_parallel_workers_6(runner_inputs):
    runtime, root, manifest, run = runner_inputs
    commands = runner.build_commands(runtime, root, manifest, run, workers=6)
    cmd_dict = dict(commands)

    # Parallel pytest: -n 6 --dist=worksteal
    py_argv = cmd_dict["python"]
    assert "-n" in py_argv
    n_idx = py_argv.index("-n")
    assert py_argv[n_idx + 1] == "6"
    assert "--dist=worksteal" in py_argv

    # Parallel Node: concurrency 6
    assert "--test-concurrency=6" in cmd_dict["node"]


def test_build_commands_acceptance_and_regression_unchanged(runner_inputs):
    runtime, root, manifest, run = runner_inputs
    serial_cmds = dict(runner.build_commands(runtime, root, manifest, run, workers=1))
    parallel_cmds = dict(runner.build_commands(runtime, root, manifest, run, workers=6))

    assert serial_cmds["acceptance"] == parallel_cmds["acceptance"]
    assert any("verify.py" in arg for arg in serial_cmds["acceptance"])

    assert serial_cmds["regression"] == parallel_cmds["regression"]
    assert str(manifest) in serial_cmds["regression"]
    assert str(run / "compare") in serial_cmds["regression"]


def test_build_commands_parallel_workers_16(runner_inputs):
    runtime, root, manifest, run = runner_inputs
    serial_cmds = dict(runner.build_commands(runtime, root, manifest, run, workers=1))
    commands = runner.build_commands(runtime, root, manifest, run, workers=16)
    cmd_dict = dict(commands)

    # Parallel pytest: -n 16 --dist=worksteal
    py_argv = cmd_dict["python"]
    assert "-n" in py_argv
    n_idx = py_argv.index("-n")
    assert py_argv[n_idx + 1] == "16"
    assert "--dist=worksteal" in py_argv

    # Parallel Node: concurrency 16
    assert "--test-concurrency=16" in cmd_dict["node"]

    # Acceptance / oracle (regression) unchanged from workers=1
    assert cmd_dict["acceptance"] == serial_cmds["acceptance"]
    assert cmd_dict["regression"] == serial_cmds["regression"]


def test_build_commands_parallel_workers_above_16(runner_inputs):
    runtime, root, manifest, run = runner_inputs
    commands = runner.build_commands(runtime, root, manifest, run, workers=24)
    cmd_dict = dict(commands)
    assert "-n" in cmd_dict["python"]
    n_idx = cmd_dict["python"].index("-n")
    assert cmd_dict["python"][n_idx + 1] == "24"
    assert "--test-concurrency=24" in cmd_dict["node"]


def test_build_commands_invalid_workers(runner_inputs):
    runtime, root, manifest, run = runner_inputs
    # 17 is now valid since machine cap is removed; only <=0 or non-int are invalid
    for bad_w in (0, -1, True, False, "8"):
        with pytest.raises(ValueError):
            runner.build_commands(runtime, root, manifest, run, workers=bad_w)


# ---------------------------------------------------------------------------
# cpu_set_for_cores tests
# ---------------------------------------------------------------------------

def test_cpu_set_for_cores_fake_8cores_request_6(tmp_path):
    layout = [(i, 0, i) for i in range(8)] + [(i + 8, 0, i) for i in range(8)]
    topo_root = _make_topology(tmp_path / "sys_cpu", layout)
    selected = runner.cpu_set_for_cores(6, allowed_cpus=set(range(16)), topology_root=topo_root)
    assert set(selected) == set(range(2, 8)) | set(range(10, 16))


def test_cpu_set_for_cores_excludes_disallowed_siblings(tmp_path):
    layout = [(i, 0, i) for i in range(8)] + [(i + 8, 0, i) for i in range(8)]
    topo_root = _make_topology(tmp_path / "sys_cpu", layout)
    selected_no_smt = runner.cpu_set_for_cores(6, allowed_cpus=set(range(8)), topology_root=topo_root)
    assert set(selected_no_smt) == set(range(2, 8))

    allowed_without_15 = set(range(16)) - {15}
    selected_no_15 = runner.cpu_set_for_cores(6, allowed_cpus=allowed_without_15, topology_root=topo_root)
    assert set(selected_no_15) == (set(range(2, 8)) | set(range(10, 16))) - {15}


def test_cpu_set_for_cores_two_sockets_same_core_id_not_merged(tmp_path):
    layout = [(0, 0, 0), (1, 1, 0)]
    topo_root = _make_topology(tmp_path / "sys_cpu", layout)
    selected = runner.cpu_set_for_cores(2, allowed_cpus={0, 1}, topology_root=topo_root)
    assert set(selected) == {0, 1}


def test_cpu_set_for_cores_insufficient_cores_error(tmp_path):
    layout = [(0, 0, 0), (1, 0, 1)]
    topo_root = _make_topology(tmp_path / "sys_cpu", layout)
    with pytest.raises(ValueError):
        runner.cpu_set_for_cores(3, allowed_cpus={0, 1}, topology_root=topo_root)


def test_cpu_set_for_cores_missing_topology_error(tmp_path):
    missing_root = tmp_path / "nonexistent_cpu_sys"
    with pytest.raises((ValueError, OSError)):
        runner.cpu_set_for_cores(1, allowed_cpus={0}, topology_root=missing_root)


def test_cpu_set_for_cores_explicit_allowed_cpus_no_host_read(tmp_path):
    layout = [(0, 0, 0), (1, 0, 1), (2, 0, 2)]
    topo_root = _make_topology(tmp_path / "sys_cpu", layout)
    selected = runner.cpu_set_for_cores(2, allowed_cpus={0, 1, 2}, topology_root=topo_root)
    assert set(selected) == {1, 2}


def test_cpu_set_for_cores_fake_8physical_16logical_request_8(tmp_path):
    layout = [(i, 0, i) for i in range(8)] + [(i + 8, 0, i) for i in range(8)]
    topo_root = _make_topology(tmp_path / "sys_cpu", layout)
    selected = runner.cpu_set_for_cores(8, allowed_cpus=set(range(16)), topology_root=topo_root)
    assert set(selected) == set(range(16))


# ---------------------------------------------------------------------------
# resolve_budget adaptive tests
# ---------------------------------------------------------------------------

def test_resolve_budget_fake_8c16t_defaults(tmp_path):
    layout = [(i, 0, i) for i in range(8)] + [(i + 8, 0, i) for i in range(8)]
    topo_root = _make_topology(tmp_path / "sys_cpu", layout)
    budget = runner.resolve_budget(allowed_cpus=set(range(16)), topology_root=topo_root)

    assert budget.workers == 14
    assert budget.physical_cores == 7
    assert set(budget.reserved_ids) == {0, 8}
    assert set(budget.selected_ids) == (set(range(1, 8)) | set(range(9, 16)))
    assert set(budget.available_ids) == set(range(16))
    assert budget["workers"] == 14
    assert budget["physical_cores"] == 7
    assert set(budget["reserved_ids"]) == {0, 8}
    assert set(budget["selected_ids"]) == (set(range(1, 8)) | set(range(9, 16)))
    assert set(budget["available_ids"]) == set(range(16))
    assert set(budget["cpu_ids"]) == (set(range(1, 8)) | set(range(9, 16)))


def test_resolve_budget_greater_than_16_cpus(tmp_path):
    layout = [(i, 0, i) for i in range(12)] + [(i + 12, 0, i) for i in range(12)]
    topo_root = _make_topology(tmp_path / "sys_cpu", layout)
    budget = runner.resolve_budget(allowed_cpus=set(range(24)), topology_root=topo_root)

    assert budget.physical_cores == 11
    assert budget.workers == 22
    assert set(budget.reserved_ids) == {0, 12}
    assert set(budget.selected_ids) == (set(range(1, 12)) | set(range(13, 24)))
    assert set(budget.available_ids) == set(range(24))


def test_resolve_budget_single_physical_core_fails(tmp_path):
    layout_smt = [(0, 0, 0), (1, 0, 0)]
    topo_root_smt = _make_topology(tmp_path / "sys_cpu_smt", layout_smt)
    with pytest.raises(ValueError, match="At least 2 physical cores required"):
        runner.resolve_budget(allowed_cpus={0, 1}, topology_root=topo_root_smt)

    layout_single = [(0, 0, 0)]
    topo_root_single = _make_topology(tmp_path / "sys_cpu_single", layout_single)
    with pytest.raises(ValueError, match="At least 2 physical cores required"):
        runner.resolve_budget(allowed_cpus={0}, topology_root=topo_root_single)


def test_resolve_budget_partial_smt(tmp_path):
    # Core 0: 2 threads (0, 8); Core 1: 1 thread (1); Core 2: 2 threads (2, 10); Core 3: 1 thread (3)
    layout = [(0, 0, 0), (8, 0, 0), (1, 0, 1), (2, 0, 2), (10, 0, 2), (3, 0, 3)]
    topo_root = _make_topology(tmp_path / "sys_cpu", layout)
    budget = runner.resolve_budget(allowed_cpus={0, 1, 2, 3, 8, 10}, topology_root=topo_root)

    assert budget.physical_cores == 3
    assert budget.workers == 6
    assert set(budget.reserved_ids) == {0, 8}
    assert set(budget.selected_ids) == {1, 2, 3, 10}


def test_resolve_budget_multisocket(tmp_path):
    layout = [
        (0, 0, 0), (4, 0, 0),
        (1, 0, 1), (5, 0, 1),
        (2, 1, 0), (6, 1, 0),
        (3, 1, 1), (7, 1, 1),
    ]
    topo_root = _make_topology(tmp_path / "sys_cpu", layout)
    budget = runner.resolve_budget(allowed_cpus=set(range(8)), topology_root=topo_root)

    assert budget.physical_cores == 3
    assert budget.workers == 6
    assert set(budget.reserved_ids) == {0, 4}
    assert set(budget.selected_ids) == {1, 5, 2, 6, 3, 7}


def test_resolve_budget_explicit_overrides_valid(tmp_path):
    layout = [(i, 0, i) for i in range(8)] + [(i + 8, 0, i) for i in range(8)]
    topo_root = _make_topology(tmp_path / "sys_cpu", layout)

    budget_w = runner.resolve_budget(workers=4, allowed_cpus=set(range(16)), topology_root=topo_root)
    assert budget_w.workers == 4
    assert budget_w.physical_cores == 7
    assert set(budget_w.reserved_ids) == {0, 8}

    budget_c = runner.resolve_budget(physical_cores=3, allowed_cpus=set(range(16)), topology_root=topo_root)
    assert budget_c.physical_cores == 3
    assert budget_c.workers == 6
    assert set(budget_c.reserved_ids) == {0, 8}
    assert set(budget_c.selected_ids) == {5, 6, 7, 13, 14, 15}

    budget_both = runner.resolve_budget(workers=1, physical_cores=2, allowed_cpus=set(range(16)), topology_root=topo_root)
    assert budget_both.workers == 1
    assert budget_both.physical_cores == 2
    assert set(budget_both.selected_ids) == {6, 7, 14, 15}


def test_resolve_budget_explicit_overrides_invalid_and_reserved(tmp_path):
    layout = [(i, 0, i) for i in range(8)] + [(i + 8, 0, i) for i in range(8)]
    topo_root = _make_topology(tmp_path / "sys_cpu", layout)

    for bad_w in (0, -1, True, False, "14"):
        with pytest.raises(ValueError):
            runner.resolve_budget(workers=bad_w, allowed_cpus=set(range(16)), topology_root=topo_root)

    for bad_c in (0, -1, True, False, "7"):
        with pytest.raises(ValueError):
            runner.resolve_budget(physical_cores=bad_c, allowed_cpus=set(range(16)), topology_root=topo_root)

    with pytest.raises(ValueError, match="maximum allowed is 7"):
        runner.resolve_budget(physical_cores=8, allowed_cpus=set(range(16)), topology_root=topo_root)

    with pytest.raises(ValueError, match="maximum allowed is 7"):
        runner.resolve_budget(physical_cores=9, allowed_cpus=set(range(16)), topology_root=topo_root)


def test_resolve_budget_missing_topology_fails_closed(tmp_path):
    missing_root = tmp_path / "nonexistent_cpu_sys"
    with pytest.raises((ValueError, OSError)):
        runner.resolve_budget(allowed_cpus={0, 1}, topology_root=missing_root)


def test_run_gate_env_pv_test_workers_and_budget_json(tmp_path, monkeypatch):
    private = tmp_path / "private_gate"
    if os.name == 'nt':
        from feasibility.windows_storage import directory
        with directory(private, (), create=True):
            pass
    else:
        private.mkdir(mode=0o700)
    ref_dir = private / "reference"
    ref_dir.mkdir()
    (ref_dir / "manifest.json").write_text('{"runtime_paths": {"python": "python3", "node": "node", "vendor": ""}}')

    layout = [(i, 0, i) for i in range(8)] + [(i + 8, 0, i) for i in range(8)]
    topo_root = _make_topology(tmp_path / "sys_cpu", layout)

    captured = {}
    monkeypatch.setattr(runner, "validate_manifest", lambda p: {"runtime_paths": {"python": "python3", "node": "node", "vendor": ""}})
    monkeypatch.setattr(runner, "verify_environment", lambda node: None)
    monkeypatch.setattr(runner, "live_guard_hashes", lambda: "guard")
    monkeypatch.setattr(os, "sched_setaffinity", lambda pid, cpus: captured.setdefault("affinity", set(cpus)), raising=False)

    def fake_subprocess_run(argv, cwd=None, env=None, stdout=None, stderr=None):
        captured.setdefault("envs", []).append(dict(env))
        if stdout:
            stdout.write("1 passed\n")
            stdout.flush()
        class DummyProc:
            returncode = 0
        return DummyProc()

    monkeypatch.setattr(runner.subprocess, "run", fake_subprocess_run)
    orig_resolve = runner.resolve_budget
    monkeypatch.setattr(runner, "resolve_budget", lambda workers=None, physical_cores=None: orig_resolve(workers=workers, physical_cores=physical_cores, allowed_cpus=set(range(16)), topology_root=topo_root))
    monkeypatch.setattr(runner, "build_commands", lambda runtime, root, manifest_path, run, workers: [("dummy", ["echo", "ok"])])

    rc = runner.run_gate(private)
    assert rc == 0
    assert captured["affinity"] == (set(range(16)) - {0, 8})
    assert captured["envs"][0]["PV_TEST_WORKERS"] == "14"

    runs = list((private / "runs").iterdir())
    assert len(runs) == 1
    budget_file = runs[0] / "resource-budget.json"
    assert budget_file.exists()
    import json
    data = json.loads(budget_file.read_text())
    assert data["workers"] == 14
    assert data["physical_cores"] == 7
    assert set(data["reserved_ids"]) == {0, 8}
    assert set(data["selected_ids"]) == (set(range(16)) - {0, 8})
    assert set(data["available_ids"]) == set(range(16))


# ---------------------------------------------------------------------------
# CLI main() tests
# ---------------------------------------------------------------------------

def test_main_cli_mode_private_defaults_fullcpu(tmp_path, monkeypatch):
    monkeypatch.setenv("PV_PRIVATE_ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["check_public_plan", "--mode", "private"])

    captured = {}

    def spy_run_gate(private, workers=None, physical_cores=None, **kwargs):
        captured["private"] = private
        captured["workers"] = workers
        captured["physical_cores"] = physical_cores
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(runner, "run_gate", spy_run_gate)

    assert runner.main() == 0
    assert captured["private"] == tmp_path.resolve()
    assert captured["workers"] is None
    assert captured["physical_cores"] is None


def test_main_cli_mode_private_explicit_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("PV_PRIVATE_ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["check_public_plan", "--mode", "private", "--workers", "6", "--physical-cores", "3"])

    captured = {}

    def spy_run_gate(private, workers=None, physical_cores=None, **kwargs):
        captured["private"] = private
        captured["workers"] = workers
        captured["physical_cores"] = physical_cores
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(runner, "run_gate", spy_run_gate)

    assert runner.main() == 0
    assert captured["private"] == tmp_path.resolve()
    assert captured["workers"] == 6
    assert captured["physical_cores"] == 3


def test_export_allowlist_is_literal_and_does_not_include_new_files(tmp_path):
    import json
    root=tmp_path/'source';(root/'tools').mkdir(parents=True)
    (root/'reviewed.py').write_text('VALUE = 1\n')
    (root/'new-unreviewed.py').write_text('VALUE = 2\n')
    allowlist=root/'tools/export_allowlist.json'
    def write(files):
        allowlist.write_text(json.dumps(dict(schema_version=1,files=files)))
    write(['reviewed.py'])
    assert runner.export_files(root)==['reviewed.py']
    for unsafe in (['*.py'],['../reviewed.py'],['/reviewed.py'],['.env'],['reviewed.py','reviewed.py']):
        write(unsafe)
        with pytest.raises(ValueError): runner.export_files(root)
    (root/'link.py').symlink_to(root/'reviewed.py');write(['link.py'])
    with pytest.raises(ValueError):runner.export_files(root)


@pytest.fixture
def reviewed_environment(tmp_path, monkeypatch):
    import importlib.metadata
    from types import SimpleNamespace
    import platform
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'pyproject.toml').write_text('[project]\ndependencies = ["numpy==1.0"]\n')
    (root / 'requirements.lock').write_text('numpy==1.0 \\\n    --hash=sha256:fixture\nllvmlite==2.0\n')
    versions = {'numpy': '1.0', 'llvmlite': '2.0', 'pytest': '9.1.1',
                'pytest-xdist': '3.8.0', 'execnet': '2.1.2'}
    origins = {}
    def distribution(name):
        if name not in versions:
            raise importlib.metadata.PackageNotFoundError(name)
        return SimpleNamespace(version=versions[name],
                               locate_file=lambda relative: origins.get(name, tmp_path / 'site'))
    monkeypatch.setattr(runner, 'ROOT', root)
    monkeypatch.setattr(platform, 'python_implementation', lambda: 'CPython')
    monkeypatch.setattr(sys, 'version_info', (3, 12, 14))
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setattr(importlib.metadata, 'distribution', distribution)
    monkeypatch.setattr(runner.subprocess, 'check_output', lambda *args, **kwargs: 'v24.22.1\n')
    return root, versions, origins


def test_reviewed_environment_accepts_installed_transitive_pin(reviewed_environment):
    runner.verify_environment('node')


@pytest.mark.parametrize('change, message', [
    ('python', 'CPython 3.12'), ('platform', 'requires Linux'),
    ('missing', 'Missing llvmlite'), ('version', 'Unreviewed llvmlite'),
    ('vendor-root', 'Bundled vendor'), ('vendor-child', 'Bundled vendor'),
    ('node', 'Node.js 24.22.1'), ('lock', 'lock differs'),
])
def test_reviewed_environment_rejects_unreviewed_runtime(reviewed_environment, monkeypatch, change, message):
    root, versions, origins = reviewed_environment
    if change == 'python':
        monkeypatch.setattr(sys, 'version_info', (3, 13, 0))
    elif change == 'platform':
        monkeypatch.setattr(sys, 'platform', 'win32')
    elif change == 'missing':
        del versions['llvmlite']
    elif change == 'version':
        versions['llvmlite'] = '3.0'
    elif change.startswith('vendor'):
        origins['numpy'] = root / 'feasibility/vendor'
        if change == 'vendor-child':
            origins['numpy'] /= 'site-packages'
    elif change == 'node':
        monkeypatch.setattr(runner.subprocess, 'check_output', lambda *args, **kwargs: 'v18.0.0\n')
    else:
        (root / 'requirements.lock').write_text('numpy==2.0\n')
    with pytest.raises(ValueError, match=message):
        runner.verify_environment('node')


def test_package_payload_is_explicit_reviewed_application_source():
    import tomllib
    spec = tomllib.loads((_REPO_ROOT / 'pyproject.toml').read_text())
    allowed = set(runner.export_files())
    expected = {p for p in allowed if p.startswith('feasibility/') and
                (p.endswith('.py') or p.startswith('feasibility/web/'))}
    targets = spec['tool']['hatch']['build']['targets']
    assert set(targets['wheel']['only-include']) == expected
    assert set(targets['sdist']['only-include']).issubset(allowed)
    assert not any('*' in p for p in targets['wheel']['only-include'])
