"""Synthetic activation tests for weather candidate parser, loader, and pointer resolution."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
import numpy as np
import pytest

from feasibility.weather_candidate import parse_v6_source, load_temperature_archive
from feasibility.weather_dataset import (
    verify_and_load_dataset,
    load_active_weather_dataset,
)
import feasibility.weather_dataset as wd


def _write_v6_json(path: Path, times: list[str], powers: list[Any], direct=None, diff=None, grnd=None) -> Path:
    n = len(times)
    payload = {
        "inputs": {"location": {"latitude": 45.0, "longitude": 9.0}, "mounting_system": {"fixed": {"slope": {"value": 30}, "azimuth": {"value": 0}}}},
        "timestamps": times,
        "power": powers,
        "direct_inclined_irradiance_before_reflectivity": [100.0] * n if direct is None else direct,
        "diffuse_inclined_irradiance_before_reflectivity": [50.0] * n if diff is None else diff,
        "ground_reflected_inclined_irradiance_before_reflectivity": [10.0] * n if grnd is None else grnd,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_parse_v6_power_acceptance_and_skips(tmp_path: Path):
    t = ["2024-01-01T11:00:00Z", "2024-01-01T12:00:00Z", "2024-01-01T13:00:00Z", "2024-01-01T14:00:00Z"]
    p = [0.0, -5.0, None, 250.0]
    d = [None, 100.0, 100.0, 100.0]
    p_file = _write_v6_json(tmp_path / "v6.json", t, p, direct=d)
    cfg, rows, file_hash = parse_v6_source(p_file)
    assert isinstance(cfg, dict) and isinstance(file_hash, str) and len(file_hash) == 64
    assert "2024-01-01T11:00:00Z" in rows
    assert rows["2024-01-01T11:00:00Z"]["power_w"] == 0.0
    assert "2024-01-01T12:00:00Z" not in rows
    assert "2024-01-01T13:00:00Z" not in rows
    assert "2024-01-01T14:00:00Z" in rows


def test_parse_v6_rejections(tmp_path: Path):
    t_dup = ["2024-01-01T12:00:00Z", "2024-01-01T12:00:00Z"]
    f_dup = _write_v6_json(tmp_path / "dup.json", t_dup, [100.0, 100.0])
    with pytest.raises(ValueError, match="Duplicate"):
        parse_v6_source(f_dup)

    mismatch = tmp_path / "mismatch.json"
    _write_v6_json(mismatch, ["2024-01-01T12:00:00Z"], [100.0], direct=[100.0, 200.0])
    with pytest.raises(ValueError, match="mismatch|length"):
        parse_v6_source(mismatch)


def _write_temp_json(path: Path, times: list[str], temps: list[float], unit="°C", utc_offset=0) -> Path:
    payload = {
        "utc_offset_seconds": utc_offset,
        "timezone": "UTC",
        "hourly_units": {"temperature_2m": unit},
        "hourly": {"time": times, "temperature_2m": temps},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_temperature_archive_validations(tmp_path: Path):
    f_unit = _write_temp_json(tmp_path / "unit.json", ["2024-01-01T00:00"], [10.0], unit="degF")
    with pytest.raises(ValueError, match="hourly_units"):
        load_temperature_archive(f_unit)

    f_offset = _write_temp_json(tmp_path / "off.json", ["2024-01-01T00:00"], [10.0], utc_offset=3600)
    with pytest.raises(ValueError, match="utc_offset"):
        load_temperature_archive(f_offset)

    f_min = _write_temp_json(tmp_path / "min.json", ["2024-01-01T00:15"], [10.0])
    with pytest.raises(ValueError, match="timestamp|minute"):
        load_temperature_archive(f_min)

    f_dup = _write_temp_json(tmp_path / "dup.json", ["2024-01-01T00:00", "2024-01-01T00:00"], [10.0, 11.0])
    with pytest.raises(ValueError, match="Duplicate"):
        load_temperature_archive(f_dup)

    short_times = [f"2024-01-01T{h:02d}:00" for h in range(24)]
    f_short = _write_temp_json(tmp_path / "short.json", short_times, [5.0] * len(short_times))
    with pytest.raises(ValueError, match="coverage|missing"):
        load_temperature_archive(f_short, expected_years=[2024])


def _make_dummy_dataset(npz_path: Path, audit_path: Path, years: list[int], valid_override=None, nonfinite=False):
    ny = len(years)
    valid = np.zeros((ny, 8784), dtype=bool)
    for i, y in enumerate(years):
        h = 8784 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 8760
        valid[i, :h] = True
    if valid_override is not None:
        valid = valid_override

    power = np.zeros((5, ny, 8784), dtype=np.float32)
    if nonfinite:
        power[0, 0, 0] = np.nan

    data = {
        "years": np.array(years, dtype=np.int32),
        "power": power,
        "beam_loss": np.zeros((5, ny, 8784), dtype=np.float32),
        "diffuse_loss": np.zeros((5, ny, 8784), dtype=np.float32),
        "load_civil": np.zeros((ny, 8784), dtype=np.float32),
        "load_standard": np.zeros((ny, 8784), dtype=np.float32),
        "month": np.zeros((ny, 8784), dtype=np.int16),
        "hour": np.zeros((ny, 8784), dtype=np.int16),
        "temperature": np.zeros((ny, 8784), dtype=np.float32),
        "valid": valid,
    }
    import datetime as dt
    for yi, year in enumerate(years):
        count = int(valid[yi].sum())
        for hour in range(count):
            local = dt.datetime(year, 1, 1) + dt.timedelta(hours=hour + 1)
            data["month"][yi, hour] = local.month
            data["hour"][yi, hour] = local.hour
    np.savez_compressed(npz_path, **data)
    audit_path.write_text(json.dumps({"version": 1, "years": years}), encoding="utf-8")


def test_verify_and_load_dataset_rejections(tmp_path: Path):
    npz_path = tmp_path / "hourly.npz"
    audit_path = tmp_path / "audit.json"

    v_bad = np.zeros((1, 8784), dtype=bool)
    _make_dummy_dataset(npz_path, audit_path, [2024], valid_override=v_bad)
    with pytest.raises(ValueError, match="incomplete|coverage|valid"):
        verify_and_load_dataset(npz_path, audit_path)

    _make_dummy_dataset(npz_path, audit_path, [2024, 2024])
    with pytest.raises(ValueError, match="duplicate|Duplicate|unique|Invalid years"):
        verify_and_load_dataset(npz_path, audit_path)

    _make_dummy_dataset(npz_path, audit_path, [2024], nonfinite=True)
    with pytest.raises(ValueError, match="non-finite|NaN"):
        verify_and_load_dataset(npz_path, audit_path)


def test_pointer_path_traversal_and_hash_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cand_root = tmp_path / "weather_candidates"
    cand_root.mkdir()
    cand_dir = cand_root / "cand_v1"
    cand_dir.mkdir()
    npz = cand_dir / "hourly.npz"
    audit = cand_dir / "audit.json"
    _make_dummy_dataset(npz, audit, [2024])

    pointer_file = tmp_path / "weather-active.json"
    monkeypatch.setattr(wd, "CANDIDATES_ROOT", cand_root)
    monkeypatch.setattr(wd, "ACTIVE_POINTER_FILE", pointer_file)

    ptr_traversal = {"dataset_dir": "../../outside", "sha256_npz": "a" * 64, "sha256_audit": "b" * 64, "id": "x"}
    pointer_file.write_text(json.dumps(ptr_traversal), encoding="utf-8")
    with pytest.raises(ValueError, match="outside|traversal"):
        load_active_weather_dataset()

    ptr_bad_hash = {
        "dataset_dir": "cand_v1",
        "sha256_npz": "0" * 64,
        "sha256_audit": hashlib.sha256(audit.read_bytes()).hexdigest(),
        "id": "cand_v1",
    }
    pointer_file.write_text(json.dumps(ptr_bad_hash), encoding="utf-8")
    with pytest.raises(ValueError, match="mismatch|SHA256"):
        load_active_weather_dataset()


@pytest.mark.parametrize('payload', ['{broken', '[]', '{}'])
def test_malformed_pointer_never_falls_back_or_changes_binding(tmp_path, monkeypatch, payload):
    identity = wd.active_weather_dataset_identity()
    pointer = tmp_path / 'weather-active.json'
    pointer.write_text(payload)
    monkeypatch.setattr(wd, 'ACTIVE_POINTER_FILE', pointer)
    with pytest.raises(ValueError):
        load_active_weather_dataset()
    assert wd.active_weather_dataset_identity() == identity



def test_load_temp_full2024_missing_feb29_and_nonutc_offset(tmp_path: Path):
    import datetime as dt
    cur, end = dt.datetime(2024, 1, 1), dt.datetime(2025, 1, 1)
    all_2024 = []
    while cur < end:
        if not (cur.month == 2 and cur.day == 29):
            all_2024.append(cur.strftime("%Y-%m-%dT%H:00"))
        cur += dt.timedelta(hours=1)
    f_feb = _write_temp_json(tmp_path / "no_feb29.json", all_2024, [10.0] * len(all_2024))
    with pytest.raises(ValueError, match="coverage|missing"):
        load_temperature_archive(f_feb, expected_years=[2024])

    f_tz = _write_temp_json(tmp_path / "tz.json", ["2024-01-01T00:00+01:00"], [10.0])
    with pytest.raises(ValueError, match="UTC|offset|timezone"):
        load_temperature_archive(f_tz)


def test_parse_v6_duplicate_first_null_and_zero_p_null_components(tmp_path: Path):
    t = ["2024-01-01T12:00:00Z", "2024-01-01T12:00:00Z"]
    f_dup = _write_v6_json(tmp_path / "dup_null.json", t, [None, 150.0])
    with pytest.raises(ValueError, match="Duplicate"):
        parse_v6_source(f_dup)

    f_zero = _write_v6_json(tmp_path / "zero_null.json", ["2024-01-01T12:00:00Z"], [0.0], direct=[None], diff=[None], grnd=[None])
    _, rows, _ = parse_v6_source(f_zero)
    row = rows["2024-01-01T12:00:00Z"]
    assert row["power_w"] == 0.0 and row["null_components"] is True
    assert row["raw_components"]["direct_inclined_irradiance_before_reflectivity"] is None


def test_verify_and_load_dataset_happy_path(tmp_path: Path):
    npz, audit = tmp_path / "hourly.npz", tmp_path / "audit.json"
    _make_dummy_dataset(npz, audit, [2024])
    data = verify_and_load_dataset(npz, audit)
    assert int(data["years"][0]) == 2024
    assert data["power"].shape == (5, 1, 8784)


def test_mismatched_power_and_loss_roof_config_reject():
    from feasibility.weather_candidate import validate_orientation_config
    cfg = {"system_power_kwp": 2.5, "loss_percent": 14.0, "latitude": 45.0, "longitude": 9.0}
    with pytest.raises(ValueError, match="Power kWp mismatch"):
        validate_orientation_config(cfg, {"power_kwp": 3.0}, "south", "v6")
    with pytest.raises(ValueError, match="Loss percent mismatch"):
        validate_orientation_config(cfg, {"loss_percent": 10.0}, "south", "v6")
