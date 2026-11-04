"""Tests for synthetic public demo generator.

Namespace collision safe import via importlib file path.
Asserts deterministic arrays, finite physics, leap/nonleap valid counts,
zero padding, seven planes support, hand-checked daily energy, solar night zero,
bounded losses, validation cases, and corrected calendar / axes.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile

import numpy as np
import pytest

GENERATOR_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "public_demo" / "generate.py"
)


def _load_generator_module():
    spec = importlib.util.spec_from_file_location("public_demo_generate", GENERATOR_PATH)
    assert spec is not None and spec.loader is not None, f"Cannot load {GENERATOR_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generator_mod():
    return _load_generator_module()


def test_deterministic_arrays(generator_mod):
    """Calling generate() twice must produce identical arrays."""
    d1 = generator_mod.generate(plane_count=2)
    d2 = generator_mod.generate(plane_count=2)
    assert set(d1.keys()) == set(d2.keys())
    for k in d1:
        assert np.array_equal(d1[k], d2[k]), f"Array mismatch for key {k}"


def test_finite_and_nonnegative_physics(generator_mod):
    """Physical and financial quantities must be finite, and nonnegative where applicable."""
    data = generator_mod.generate(plane_count=2)
    all_keys = [
        "power",
        "beam_loss",
        "diffuse_loss",
        "load_civil",
        "load_standard",
        "temperature",
        "thermal",
        "prices",
    ]
    for k in all_keys:
        assert np.all(np.isfinite(data[k])), f"Non-finite values found in {k}"

    nonnegative_keys = [
        "power",
        "beam_loss",
        "diffuse_loss",
        "load_civil",
        "load_standard",
        "thermal",
        "prices",
    ]
    for k in nonnegative_keys:
        assert np.all(data[k] >= 0.0), f"Negative values found in {k}"


def test_leap_nonleap_valid_counts_and_zero_padding(generator_mod):
    """Assert 8760 valid hours for 2023, 8784 for 2024, and zero padding."""
    data = generator_mod.generate(plane_count=2)
    valid = data["valid"]
    years = data["years"]

    assert list(years) == [2023, 2024]
    assert valid.shape == (2, 8784)

    # 2023: nonleap (365 days * 24 = 8760)
    assert np.count_nonzero(valid[0]) == 8760
    assert np.all(valid[0, :8760])
    assert not np.any(valid[0, 8760:])

    # 2024: leap (366 days * 24 = 8784)
    assert np.count_nonzero(valid[1]) == 8784
    assert np.all(valid[1, :8784])

    # Zero padding checks on invalid hours of 2023
    pad_slice = slice(8760, 8784)
    assert np.all(data["power"][:, 0, pad_slice] == 0.0)
    assert np.all(data["beam_loss"][:, 0, pad_slice] == 0.0)
    assert np.all(data["diffuse_loss"][:, 0, pad_slice] == 0.0)
    assert np.all(data["load_civil"][0, pad_slice] == 0.0)
    assert np.all(data["load_standard"][0, pad_slice] == 0.0)
    assert np.all(data["month"][0, pad_slice] == 0)
    assert np.all(data["hour"][0, pad_slice] == 0)
    assert np.all(data["temperature"][0, pad_slice] == 0.0)
    assert np.all(data["thermal"][0, pad_slice] == 0.0)
    assert np.all(data["prices"][0, pad_slice] == 0.0)
    assert np.all(data["utc_timestamp"][0, pad_slice] == "")


def test_hand_check_daily_energy(generator_mod):
    """Hand-check daily block sums: .2 * 24 + .1 * 4 = 5.2 kWh in standard load."""
    data = generator_mod.generate(plane_count=2)
    expected_daily = 0.2 * 24 + 0.1 * 4  # 5.2

    for day in range(365):
        s = slice(day * 24, (day + 1) * 24)
        std_energy = float(data["load_standard"][0, s].sum())
        assert np.isclose(std_energy, expected_daily, atol=1e-12)


def test_annual_energy_parity(generator_mod):
    """Annual energies for load_civil and load_standard match despite DST shifts."""
    data = generator_mod.generate(plane_count=2)
    assert np.isclose(data["load_civil"][0, :8760].sum(), data["load_standard"][0, :8760].sum())
    assert np.isclose(data["load_civil"][1, :8784].sum(), data["load_standard"][1, :8784].sum())


def test_corrected_calendar_and_timestamps(generator_mod):
    """UTC Jan1 00:00 -> standard hour 1, and UTC leap last 23:00 -> next Jan 1 00:00."""
    data = generator_mod.generate(plane_count=2)

    # UTC Jan 1 00:00 -> standard fixed UTC+1 hour 1, month 1
    assert data["utc_timestamp"][0, 0] == "2023-01-01T00:00:00Z"
    assert data["hour"][0, 0] == 1
    assert data["month"][0, 0] == 1

    # First 24 hours list: 1..23, 0
    expected_first_24 = list(range(1, 24)) + [0]
    assert list(data["hour"][0, :24]) == expected_first_24

    # 2023 nonleap last valid hour: UTC Dec 31 23:00 -> next Jan 1 00:00
    assert data["utc_timestamp"][0, 8759] == "2023-12-31T23:00:00Z"
    assert data["hour"][0, 8759] == 0
    assert data["month"][0, 8759] == 1

    # 2024 leap last valid hour: UTC Dec 31 23:00 -> next Jan 1 00:00
    assert data["utc_timestamp"][1, 8783] == "2024-12-31T23:00:00Z"
    assert data["hour"][1, 8783] == 0
    assert data["month"][1, 8783] == 1


def test_solar_night_zero_and_losses_bounded(generator_mod):
    """Solar power and losses must be zero at night and losses bounded by power."""
    data = generator_mod.generate(plane_count=2)
    hour = data["hour"]
    power = data["power"]
    beam_loss = data["beam_loss"]
    diffuse_loss = data["diffuse_loss"]
    valid = data["valid"]

    night_mask = valid & ((hour < 6) | (hour > 18))
    for p in range(2):
        assert np.all(power[p, night_mask] == 0.0)
        assert np.all(beam_loss[p, night_mask] == 0.0)
        assert np.all(diffuse_loss[p, night_mask] == 0.0)

    assert np.all(beam_loss <= power)
    assert np.all(beam_loss >= 0.0)
    assert np.all(diffuse_loss == 0.0)


def test_seven_planes_support_and_axes(generator_mod):
    """Explicit count=7 supported with (plane_count, 2, 8784) shape and plane scaling."""
    data7 = generator_mod.generate(plane_count=7)

    assert data7["power"].shape == (7, 2, 8784)
    assert data7["beam_loss"].shape == (7, 2, 8784)
    assert data7["diffuse_loss"].shape == (7, 2, 8784)

    h12_mask = data7["valid"] & (data7["hour"] == 12)
    p0 = data7["power"][0]
    for p in range(7):
        expected_ratio = 1.0 - p * 0.04
        actual_ratio = data7["power"][p][h12_mask] / p0[h12_mask]
        assert np.allclose(actual_ratio, expected_ratio, atol=1e-12)


def test_plane_count_validation(generator_mod):
    """plane_count must accept only exact int 1..12 (bool invalid)."""
    with pytest.raises(TypeError):
        generator_mod.generate(plane_count=True)
    with pytest.raises(TypeError):
        generator_mod.generate(plane_count=False)
    with pytest.raises(TypeError):
        generator_mod.generate(plane_count="2")
    with pytest.raises(ValueError):
        generator_mod.generate(plane_count=0)
    with pytest.raises(ValueError):
        generator_mod.generate(plane_count=13)


def test_cli_output_and_safety_checks(generator_mod):
    """CLI rejects output paths inside repo and avoids overwriting existing files."""
    repo_root = Path(generator_mod.__file__).resolve().parents[3]

    with pytest.raises(ValueError):
        generator_mod.main(["--output", str(repo_root / "tests" / "temp_out")])

    with tempfile.TemporaryDirectory() as tmp_dir:
        outside_dir = Path(tmp_dir) / "demo_output"
        generator_mod.main(["--output", str(outside_dir), "--planes", "3"])

        npz_file = outside_dir / "hourly.npz"
        prov_file = outside_dir / "provenance.json"

        assert npz_file.exists()
        assert prov_file.exists()

        with np.load(npz_file) as npz:
            assert npz["power"].shape == (3, 2, 8784)
            assert "load_civil" in npz

        with pytest.raises(FileExistsError):
            generator_mod.main(["--output", str(outside_dir), "--planes", "3"])


def test_seasonal_cosine_table_exact_and_stubbed(generator_mod, monkeypatch):
    """Ensure generate() uses the frozen seasonal cosine table and matches reference.json without host np.cos."""
    import hashlib

    table_path = Path(generator_mod.__file__).resolve().parent / "seasonal_cosine.json"
    assert table_path.exists()
    table = json.loads(table_path.read_text(encoding="utf-8"))
    assert set(table.keys()) >= {"365", "366"}
    assert len(table["365"]) == 365
    assert len(table["366"]) == 366

    # Validate 731 samples: shapes and all finite
    for n_str in ("365", "366"):
        samples = np.array([float.fromhex(h) for h in table[n_str]], dtype=np.float64)
        assert samples.shape == (int(n_str),)
        assert np.all(np.isfinite(samples))

    # Reset cached table before call to exercise file load
    monkeypatch.setattr(generator_mod, "_SEASONAL_COSINE_TABLE", None)

    def raising_cos(*args, **kwargs):
        raise RuntimeError("Host np.cos should not be called by generate()")

    monkeypatch.setattr(generator_mod.np, "cos", raising_cos)
    actual = generator_mod.generate(plane_count=2)

    ref_path = Path(generator_mod.__file__).resolve().parent / "reference.json"
    expected = json.loads(ref_path.read_text(encoding="utf-8"))["snapshots"]["2"]["arrays"]
    for k, spec in expected.items():
        arr = actual[k]
        assert list(arr.shape) == spec["shape"], f"{k} shape mismatch"
        assert arr.dtype.str == spec["dtype"], f"{k} dtype mismatch"
        assert hashlib.sha256(arr.tobytes(order="C")).hexdigest() == spec["sha256"], f"{k} sha mismatch"
