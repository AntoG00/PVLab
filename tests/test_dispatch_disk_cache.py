"""Regression tests verifying Numba disk caching and cross-kernel invalidation."""
import test_support  # Install live path guards before other imports
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import numpy as np

import feasibility.physical_dispatch as physical_dispatch


def _run_dispatch_subprocess(mod_name, env):
    runner_code = f"""
import json
import numpy as np
import {mod_name} as pd_mod

pv = np.ones((1, 24), dtype=np.float64) * 2.0
load = np.ones((1, 24), dtype=np.float64) * 1.0
price_shape = np.ones(24, dtype=np.float64)
valid = np.ones(24, dtype=np.bool_)
physical = (1., 1., 1., 1., 1., 0., 1., 0., 11., 1., 12.)
month = np.ones(24, dtype=np.int64) * 6
hour = np.arange(24, dtype=np.int64)

totals, traces = pd_mod.dispatch_weather_years(
    pv, load, 0.0, 0.0, 0.0, 0.95, 3.0, 0.0,
    price_shape, valid, False, physical, month, hour
)

kernels = [pd_mod.dispatch_weather_years, pd_mod.dispatch_year, pd_mod.dispatch_physical_year]
total_hits = sum(sum(k.stats.cache_hits.values()) for k in kernels)
total_misses = sum(sum(k.stats.cache_misses.values()) for k in kernels)
wy_hits = sum(pd_mod.dispatch_weather_years.stats.cache_hits.values())
wy_misses = sum(pd_mod.dispatch_weather_years.stats.cache_misses.values())

payload = {{
    'totals': totals.tolist(),
    'traces': traces.tolist(),
    'cache_hits': total_hits,
    'cache_misses': total_misses,
    'weather_years_hits': wy_hits,
    'weather_years_misses': wy_misses,
}}
print(json.dumps(payload))
"""
    cmd = [sys.executable, "-c", runner_code]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False, timeout=120)
    assert proc.returncode == 0, f"Subprocess failed with stderr:\n{proc.stderr}"
    return json.loads(proc.stdout)


def test_dispatch_disk_cache_invalidation(tmp_path):
    src_file = Path(physical_dispatch.__file__).resolve()
    mod_name = "standalone_physical_dispatch"
    mod_file = tmp_path / f"{mod_name}.py"
    shutil.copy2(src_file, mod_file)

    cache_dir = tmp_path / "numba_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["NUMBA_CACHE_DIR"] = str(cache_dir)
    if os.name == "nt":
        from feasibility.windows_storage import native_path
        env["NUMBA_CACHE_DIR"] = native_path(cache_dir)
    env["NUMBA_NUM_THREADS"] = "2"
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(tmp_path) + (os.pathsep + existing_pythonpath if existing_pythonpath else "")

    # Run 1: Cold start, cache miss
    res1 = _run_dispatch_subprocess(mod_name, env)
    assert res1["cache_misses"] >= 1
    assert res1["cache_hits"] == 0
    assert res1["weather_years_misses"] >= 1
    assert res1["weather_years_hits"] == 0

    # Run 2: Warm start, cache hit with exact arrays
    res2 = _run_dispatch_subprocess(mod_name, env)
    assert res2["cache_hits"] >= 1
    assert res2["cache_misses"] == 0
    assert res2["weather_years_hits"] >= 1
    assert res2["weather_years_misses"] == 0
    np.testing.assert_array_equal(res1["totals"], res2["totals"])
    np.testing.assert_array_equal(res1["traces"], res2["traces"])

    # Mutate dispatch_physical_year in the standalone copy and bump mtime by 2 seconds
    content = mod_file.read_text(encoding="utf-8")
    target = "    return out, trace\n"
    assert content.count(target) == 1, f"Expected unique return statement, found {content.count(target)}"
    mutated = content.replace(target, "    out[0] += 123.0\n    return out, trace\n", 1)
    mod_file.write_text(mutated, encoding="utf-8")
    bumped_mtime = os.path.getmtime(mod_file) + 2.0
    os.utime(mod_file, (bumped_mtime, bumped_mtime))

    # Run 3: Invalidation triggered by physical dependency change
    res3 = _run_dispatch_subprocess(mod_name, env)
    assert res3["cache_misses"] >= 1
    assert res3["weather_years_misses"] >= 1

    t2 = np.array(res2["totals"])
    t3 = np.array(res3["totals"])
    assert t3[0, 0] == t2[0, 0] + 123.0
    np.testing.assert_array_equal(t3[0, 1:], t2[0, 1:])
    np.testing.assert_array_equal(res3["traces"], res2["traces"])
