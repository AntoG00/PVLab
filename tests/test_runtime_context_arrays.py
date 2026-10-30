"""Tests for runtime context subprocess import and read-only array protections."""
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_subp(code: str) -> None:
    env = os.environ.copy()
    pp = [str(REPO_ROOT), str(REPO_ROOT / "feasibility/vendor")]
    if env.get("PYTHONPATH"):
        pp.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pp)
    res = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert res.returncode == 0, f"Subprocess failed ({res.returncode}):\n{res.stdout}\n{res.stderr}"


def test_subprocess_import_isolated_and_unset_error():
    script = """
import builtins
import io
import sys
import shutil
import tempfile
from pathlib import Path

isolated = tempfile.TemporaryDirectory()
package = Path(isolated.name) / 'feasibility'
package.mkdir()
for source in (Path.cwd() / 'feasibility').glob('*.py'):
    shutil.copyfile(source, package / source.name)
assert not (package / 'data').exists()
sys.path.insert(0, isolated.name)

orig_open = builtins.open
def guarded_open(file, *args, **kwargs):
    s = str(file)
    if "feasibility/data" in s or "CodexProject" in s:
        raise PermissionError(f"Prohibited data file open during import: {s}")
    return orig_open(file, *args, **kwargs)

builtins.open = guarded_open
io.open = guarded_open

import feasibility.weather_dataset as wd
import feasibility.engine as engine
from feasibility.runtime_context import current_runtime
assert Path(engine.__file__).parent == package

try:
    engine.data()
    sys.exit("engine.data() did not raise when runtime unset")
except RuntimeError as exc:
    if "initializ" not in str(exc).lower():
        sys.exit(f"engine.data() error message missing initialization requirement: {exc}")

try:
    current_runtime()
    sys.exit("current_runtime() did not raise when runtime unset")
except RuntimeError as exc:
    if "initializ" not in str(exc).lower():
        sys.exit(f"current_runtime() error message missing initialization requirement: {exc}")
"""
    _run_subp(script)


def test_subprocess_read_only_arrays_and_metadata_isolation():
    script = """
import sys
import numpy as np
import feasibility.weather_dataset as wd
import feasibility.engine as engine
from feasibility.runtime_context import RuntimeContext, initialize_runtime, current_runtime

src_power = np.ones((5, 1, 8784), dtype=float)
src_years = np.array([2024], dtype=int)
src_audit = {"site": "synthetic_site", "tags": ["pv"]}
src_metadata = {"schema_version": 1}

ctx = RuntimeContext(
    owner_id="test_owner",
    input_revision="rev_1",
    dataset_digest="sha256_mock",
    price_revision="price_1",
    defaults_snapshot={"default_k": 1},
    thermal_reference={"cooling_ref": 24.0},
    fiscal_revision="fiscal_1",
    calendar_version="2024_v1",
    dataset={"power": src_power, "years": src_years, "audit": src_audit},
    weather_identity=("synthetic_weather", "npz_mock", "audit_mock"),
    records_snapshot={"record_k": 2},
    metadata=src_metadata,
)

initialize_runtime(ctx)
if current_runtime() is not ctx:
    sys.exit("current_runtime() did not return initialized context")

initialize_runtime(ctx)

src_power[0, 0, 0] = 999.0
src_years[0] = 1900
src_audit["site"] = "mutated_site"
src_metadata["schema_version"] = 99

active = wd.get_active_dataset()
if float(active["power"][0, 0, 0]) != 1.0:
    sys.exit("Active power modified via source-array alias")
if int(active["years"][0]) != 2024:
    sys.exit("Active years modified via source-array alias")
if active["audit"]["site"] != "synthetic_site":
    sys.exit("Active audit metadata modified via source dict alias")

eng_active = engine.data()
if float(eng_active["power"][0, 0, 0]) != 1.0:
    sys.exit("engine.data() mismatch on source-array mutation")

try:
    active["power"][0, 0, 0] = 42.0
    sys.exit("Array assignment succeeded on read-only power array")
except (ValueError, RuntimeError):
    pass

try:
    active["power"].setflags(write=True)
    sys.exit("setflags(write=True) succeeded on active power array")
except (ValueError, RuntimeError):
    pass

try:
    active["years"].setflags(write=True)
    sys.exit("setflags(write=True) succeeded on active years array")
except (ValueError, RuntimeError):
    pass

try:
    active["audit"]["site"] = "modified_in_active"
    if wd.get_active_dataset()["audit"]["site"] == "modified_in_active":
        sys.exit("Active dataset metadata mutated in-place")
except (TypeError, ValueError):
    pass
"""
    _run_subp(script)
