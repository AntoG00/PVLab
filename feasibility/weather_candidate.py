"""weather_candidate.py: Offline candidate builder module for 2005-2025 merged weather arrays.

Strictly enforces:
- Immutable output directory: out_dir must not already exist (mkdir exist_ok=False).
- Standard candidate loader convention: output files named hourly.npz and audit.json.
- No remote APIs and no live activation pointers.
- Bitwise identical legacy preservation for 2005-2013 slices.
- Baseline 2014-2023 preserved for load, month, hour, temperature, and valid;
  weather power and irradiance losses replaced from priority-merged v6 (fallback v5).
- 2024-2025 calendar and load mapped from baseline 2023 lookup and monthly renormalized
  to original audit monthly_kwh (Feb 29 averaged from Feb 28 and Mar 1).
- Mandatory temperature_file with UTC offset 0, hourly_units in °C, exact hourly timestamps.
- Mandatory full config matching (tilt, az, lat, lon, kwp, loss) against manifest.
  Demands manifest coordinates when absent in v6 metadata.
- South legacy v5 azimuth discrepancy permitted only with explicit manifest declaration
  (v5_metadata_azimuth_deg=2 and v5_requested_azimuth_deg=2.5) and recorded in audit.
- Before-reflectivity component arrays ONLY (no fallback post-reflectivity).
- Zero power (P==0) MUST win source priority even if incident components are null/None:
  zero generation implies zero beam/diffuse energy loss independent of irradiance;
  losses set to 0 and null components flag preserved without fabricating radiation.
  Positive P strictly requires valid, finite components.
- Hierarchy: primary v6 SARAH -> valid v5 fallback through 2023 -> v6_gap ERA5 only if no prior source.
- Distinct source label 'v6_era5_gap' with original timestamps and source hash; no interpolation.
- Rejects duplicate canonical hours and mismatched array lengths.
- Fails with full missing_hours.json written to out_dir if any required roof hour is missing.
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from zoneinfo import ZoneInfo

import numpy as np

ORIENTATIONS: list[str] = ["south_exact", "west", "east", "dormer", "flat"]
BASELINE_YEARS: list[int] = list(range(2005, 2024))   # 19 years (2005..2023)
CANDIDATE_YEARS: list[int] = list(range(2005, 2026))  # 21 years (2005..2025)


def diff_azimuth(a1: float, a2: float) -> float:
    """Calculate circular azimuth difference in degrees within [-180, 180]."""
    return abs((a1 - a2 + 180.0) % 360.0 - 180.0)


def validate_orientation_config(
    file_cfg: dict[str, Any],
    manifest_cfg: dict[str, Any],
    label: str,
    source_name: str,
    is_v5: bool = False,
    candidate_audit: Optional[dict[str, Any]] = None,
) -> None:
    """Validate full configuration metadata against manifest explicit parameters."""
    # 1. Slope / tilt validation
    exp_tilt = manifest_cfg.get("slope_deg") or manifest_cfg.get("tilt_deg")
    if exp_tilt is not None:
        file_tilt = file_cfg.get("tilt_deg")
        if file_tilt is None:
            raise ValueError(f"Missing tilt_deg in {source_name} for orientation '{label}'")
        if abs(float(file_tilt) - float(exp_tilt)) > 0.01:
            raise ValueError(
                f"Tilt mismatch in {source_name} for '{label}': "
                f"expected {float(exp_tilt):.2f} deg, got {float(file_tilt):.2f} deg"
            )

    # 2. Azimuth validation with documented South v5 rounding discrepancy allowance
    exp_az = manifest_cfg.get("azimuth_deg")
    if exp_az is not None:
        file_az = file_cfg.get("azimuth_deg")
        if file_az is None:
            raise ValueError(f"Missing azimuth_deg in {source_name} for orientation '{label}'")

        v5_meta_az = manifest_cfg.get("v5_metadata_azimuth_deg")
        v5_req_az = manifest_cfg.get("v5_requested_azimuth_deg")
        is_south_v5_special = (
            is_v5
            and label == "south_exact"
            and float(exp_az) == 2.5
            and v5_meta_az is not None
            and v5_req_az is not None
            and abs(float(v5_meta_az) - 2.0) <= 0.01
            and abs(float(v5_req_az) - 2.5) <= 0.01
            and abs(float(file_az) - 2.0) <= 0.01
        )
        if is_south_v5_special:
            if candidate_audit is not None:
                discrepancies = candidate_audit.setdefault("documented_discrepancies", [])
                discrepancies.append({
                    "orientation": label,
                    "source": source_name,
                    "v5_metadata_azimuth_deg": 2.0,
                    "v5_requested_azimuth_deg": 2.5,
                    "note": "Documented south rounding discrepancy: requested 2.5 deg, metadata 2.0 deg",
                })
        else:
            if diff_azimuth(float(file_az), float(exp_az)) > 0.01:
                raise ValueError(
                    f"Azimuth mismatch in {source_name} for '{label}': "
                    f"expected {float(exp_az):.2f} deg, got {float(file_az):.2f} deg"
                )

    # 3. Latitude and Longitude validation
    exp_lat = manifest_cfg.get("latitude")
    exp_lon = manifest_cfg.get("longitude")
    file_lat = file_cfg.get("latitude")
    file_lon = file_cfg.get("longitude")

    if file_lat is not None and exp_lat is not None:
        if abs(float(file_lat) - float(exp_lat)) > 1e-4:
            raise ValueError(f"Latitude mismatch in {source_name} for '{label}': expected {exp_lat}, got {file_lat}")
    elif file_lat is None:
        if exp_lat is None or exp_lon is None:
            raise ValueError(
                f"{source_name} for '{label}' lacks coordinates; manifest must provide 'latitude' and 'longitude' for provenance"
            )

    if file_lon is not None and exp_lon is not None:
        if abs(float(file_lon) - float(exp_lon)) > 1e-4:
            raise ValueError(f"Longitude mismatch in {source_name} for '{label}': expected {exp_lon}, got {file_lon}")

    # 4. Power kWp and Loss validation
    exp_kwp = manifest_cfg.get("power_kwp") or manifest_cfg.get("peak_power")
    file_kwp = file_cfg.get("system_power_kwp")
    if exp_kwp is not None and file_kwp is not None:
        if abs(float(file_kwp) - float(exp_kwp)) > 1e-4:
            raise ValueError(f"Power kWp mismatch in {source_name} for '{label}': expected {exp_kwp}, got {file_kwp}")

    exp_loss = manifest_cfg.get("loss_percent") or manifest_cfg.get("loss")
    file_loss = file_cfg.get("loss_percent")
    if exp_loss is not None and file_loss is not None:
        if abs(float(file_loss) - float(exp_loss)) > 1e-4:
            raise ValueError(f"Loss percent mismatch in {source_name} for '{label}': expected {exp_loss}, got {file_loss}")


def _parse_timestamp(val: str) -> dt.datetime:
    """Parse ISO or PVGIS compact timestamp string into UTC datetime."""
    s = val.strip().strip('"').strip("'")
    if not s:
        raise ValueError("Empty timestamp string")
    if len(s) >= 13 and s[8] == ":" and s[:8].isdigit():
        time_part = s[9:]
        fmt = "%Y%m%d:%H%M%S" if (len(time_part) == 6 or ":" in time_part) else "%Y%m%d:%H%M"
        return dt.datetime.strptime(s, fmt).replace(tzinfo=dt.timezone.utc)
    s_iso = s.replace("Z", "+00:00")
    if " " in s_iso and "T" not in s_iso:
        s_iso = s_iso.replace(" ", "T")
    d = dt.datetime.fromisoformat(s_iso)
    return d.replace(tzinfo=dt.timezone.utc) if d.tzinfo is None else d.astimezone(dt.timezone.utc)


def parse_v5_source(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]], str]:
    """Parse PVGIS 5.3 JSON hourly file.

    Mathematical rule:
    - P==0 zero power is valid and accepted even if irradiance components are null.
    - Positive P strictly requires valid finite components.
    - Rejects duplicate canonical hours. Power parsed in Watts consistent with evidence_formats.
    Returns (extracted_config, rows_by_canonical_hour, sha256_hash).
    """
    from feasibility.evidence_formats import _extract_v5_meta_and_cfg
    raw_bytes = path.read_bytes()
    file_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    raw_text = raw_bytes.decode("utf-8-sig")
    data = json.loads(raw_text)
    meta, cfg = _extract_v5_meta_and_cfg(data.get("inputs", {}))
    hourly = data.get("outputs", {}).get("hourly", [])
    rows: dict[str, dict[str, Any]] = {}
    seen_hours: set[str] = set()

    for item in hourly:
        t_raw = item.get("time") or item.get("timestamp")
        if not t_raw:
            continue
        d = _parse_timestamp(str(t_raw))
        canon_hour = d.strftime("%Y-%m-%dT%H:00:00Z")
        if canon_hour in seen_hours:
            raise ValueError(f"Duplicate canonical hour rejected in v5 file {path}: {canon_hour}")

        seen_hours.add(canon_hour)
        p_raw = item.get("P") if "P" in item else item.get("power")
        if p_raw is None or isinstance(p_raw, bool):
            continue
        try:
            p_val = float(p_raw)
        except (ValueError, TypeError):
            continue
        if not math.isfinite(p_val) or p_val < 0.0:
            continue

        gb = item.get("Gb(i)")
        gd = item.get("Gd(i)")
        gr = item.get("Gr(i)")

        if p_val == 0.0:
            # Zero power accepted even if components are null; losses are identically 0
            has_null_comp = (
                gb is None or gd is None or gr is None
                or isinstance(gb, bool) or isinstance(gd, bool) or isinstance(gr, bool)
                or not math.isfinite(float(gb)) or not math.isfinite(float(gd)) or not math.isfinite(float(gr))
            )
            direct = float(gb) if (gb is not None and not isinstance(gb, bool) and math.isfinite(float(gb))) else 0.0
            diffuse = float(gd) if (gd is not None and not isinstance(gd, bool) and math.isfinite(float(gd))) else 0.0
            ground = float(gr) if (gr is not None and not isinstance(gr, bool) and math.isfinite(float(gr))) else 0.0
            rows[canon_hour] = {
                "canonical_hour": canon_hour,
                "timestamp": str(t_raw),
                "power_w": 0.0,
                "direct": direct,
                "diffuse": diffuse + ground,
                "null_components": has_null_comp,
                "raw_components": {"Gb(i)": gb, "Gd(i)": gd, "Gr(i)": gr},
                "source": "v5",
                "source_hash": file_sha256,
            }
        else:
            if any(v is None or isinstance(v, bool) or not math.isfinite(float(v)) or float(v) < 0.0 for v in (gb, gd, gr)):
                continue
            direct = float(gb)
            diffuse = float(gd) + float(gr)
            rows[canon_hour] = {
                "canonical_hour": canon_hour,
                "timestamp": str(t_raw),
                "power_w": p_val,
                "direct": direct,
                "diffuse": diffuse,
                "null_components": False,
                "raw_components": {"Gb(i)": gb, "Gd(i)": gd, "Gr(i)": gr},
                "source": "v5",
                "source_hash": file_sha256,
            }
    return cfg, rows, file_sha256


def parse_v6_source(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]], str]:
    """Parse PVGIS 6 JSON hourly file.

    STRICT REQUIREMENTS:
    - Before-reflectivity component arrays ONLY:
      direct_inclined_irradiance_before_reflectivity
      diffuse_inclined_irradiance_before_reflectivity
      ground_reflected_inclined_irradiance_before_reflectivity
      (No fallback to post-reflectivity components).
    - Equal array lengths across timestamps, power, and all three before-reflectivity arrays.
    - Rejects duplicate canonical hours.
    - Zero power (P==0) MUST win source priority even if incident components are null/None:
      zero generation implies zero beam/diffuse energy loss independent of irradiance;
      losses set to 0 and null components flag preserved without fabricating radiation.
    - Positive P strictly requires valid, finite components.
    - Power parsed in Watts consistent with evidence_formats.
    Returns (extracted_config, rows_by_canonical_hour, sha256_hash).
    """
    from feasibility.evidence_formats import _extract_v6_meta_and_cfg
    raw_bytes = path.read_bytes()
    file_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    raw_text = raw_bytes.decode("utf-8-sig")
    data = json.loads(raw_text)
    meta, cfg = _extract_v6_meta_and_cfg(data)

    t_key = next((k for k in ("timestamps", "time", "timestamp") if k in data), None)
    p_key = next((k for k in ("power", "P") if k in data), None)
    if not t_key or not p_key:
        raise ValueError(f"PVGIS v6 file {path} missing timestamps or power arrays")

    times = data[t_key]
    powers = data[p_key]

    dir_key = "direct_inclined_irradiance_before_reflectivity"
    diff_key = "diffuse_inclined_irradiance_before_reflectivity"
    grnd_key = "ground_reflected_inclined_irradiance_before_reflectivity"

    for k in (dir_key, diff_key, grnd_key):
        if k not in data:
            raise ValueError(f"PVGIS v6 file {path} missing required before-reflectivity array: '{k}'")

    dir_arr = data[dir_key]
    diff_arr = data[diff_key]
    grnd_arr = data[grnd_key]

    if not (len(times) == len(powers) == len(dir_arr) == len(diff_arr) == len(grnd_arr)):
        raise ValueError(
            f"PVGIS v6 file {path} array length mismatch: "
            f"times={len(times)}, powers={len(powers)}, dir={len(dir_arr)}, "
            f"diff={len(diff_arr)}, grnd={len(grnd_arr)}"
        )

    rows: dict[str, dict[str, Any]] = {}
    seen_hours: set[str] = set()
    for idx, (t_raw, p_raw) in enumerate(zip(times, powers)):
        if not t_raw:
            continue
        d = _parse_timestamp(str(t_raw))
        canon_hour = d.strftime("%Y-%m-%dT%H:00:00Z")
        if canon_hour in seen_hours:
            raise ValueError(f"Duplicate canonical hour rejected in v6 file {path}: {canon_hour}")

        seen_hours.add(canon_hour)
        # Power validation
        if p_raw is None or isinstance(p_raw, bool):
            continue
        try:
            p_val = float(p_raw)
        except (ValueError, TypeError):
            continue
        if not math.isfinite(p_val) or p_val < 0.0:
            continue

        c_dir = dir_arr[idx]
        c_diff = diff_arr[idx]
        c_grnd = grnd_arr[idx]

        if p_val == 0.0:
            # Zero power accepted even if components are null; losses are identically 0
            has_null_comp = (
                c_dir is None or c_diff is None or c_grnd is None
                or isinstance(c_dir, bool) or isinstance(c_diff, bool) or isinstance(c_grnd, bool)
                or not math.isfinite(float(c_dir)) or not math.isfinite(float(c_diff)) or not math.isfinite(float(c_grnd))
            )
            direct = float(c_dir) if (c_dir is not None and not isinstance(c_dir, bool) and math.isfinite(float(c_dir))) else 0.0
            diffuse = float(c_diff) if (c_diff is not None and not isinstance(c_diff, bool) and math.isfinite(float(c_diff))) else 0.0
            ground = float(c_grnd) if (c_grnd is not None and not isinstance(c_grnd, bool) and math.isfinite(float(c_grnd))) else 0.0
            rows[canon_hour] = {
                "canonical_hour": canon_hour,
                "timestamp": str(t_raw),
                "power_w": 0.0,
                "direct": direct,
                "diffuse": diffuse + ground,
                "null_components": has_null_comp,
                "raw_components": {
                    "direct_inclined_irradiance_before_reflectivity": c_dir,
                    "diffuse_inclined_irradiance_before_reflectivity": c_diff,
                    "ground_reflected_inclined_irradiance_before_reflectivity": c_grnd,
                },
                "source": "v6",
                "source_hash": file_sha256,
            }
        else:
            # Positive power requires valid finite components
            if any(v is None or isinstance(v, bool) or not math.isfinite(float(v)) or float(v) < 0.0 for v in (c_dir, c_diff, c_grnd)):
                continue
            direct = float(c_dir)
            diffuse = float(c_diff) + float(c_grnd)
            rows[canon_hour] = {
                "canonical_hour": canon_hour,
                "timestamp": str(t_raw),
                "power_w": p_val,
                "direct": direct,
                "diffuse": diffuse,
                "null_components": False,
                "raw_components": {
                    "direct_inclined_irradiance_before_reflectivity": c_dir,
                    "diffuse_inclined_irradiance_before_reflectivity": c_diff,
                    "ground_reflected_inclined_irradiance_before_reflectivity": c_grnd,
                },
                "source": "v6",
                "source_hash": file_sha256,
            }
    return cfg, rows, file_sha256


def compute_sun_from_rasters(
    layers_dir: Path,
    solar_insights_path: Path,
    segments: list[list[int]],
) -> dict[tuple[int, int, int], np.ndarray]:
    """Preserved local shading calculation from prepare_data.py SOURCE.

    Derives monthly roof sample visibilities from private GeoTIFF rasters at runtime.
    """
    import rasterio
    from rasterio.warp import transform

    solar = json.loads(solar_insights_path.read_text(encoding="utf-8"))["solarPotential"]
    samples = []
    with rasterio.open(layers_dir / "annual_flux.tif") as flux:
        for ids in segments:
            panels = [p for p in solar["solarPanels"] if p["segmentIndex"] in ids]
            centers = [p["center"] for p in panels] or [solar["roofSegmentStats"][i]["center"] for i in ids]
            x, y = transform("EPSG:4326", flux.crs, [c["longitude"] for c in centers], [c["latitude"] for c in centers])
            samples.append(list(zip(x, y)))

    sun: dict[tuple[int, int, int], np.ndarray] = {}
    for m in range(1, 13):
        with rasterio.open(layers_dir / f"hourly_shade_m{m:02}.tif") as ds:
            for oi, xy in enumerate(samples):
                raw = np.array(list(ds.sample(xy)))
                if np.any(raw == ds.nodata):
                    raise ValueError(f"Nodata at roof sample in month {m}")
                bits = raw.astype(np.uint32)
                for day in range(1, calendar.monthrange(2025, m)[1] + 1):
                    sun[m, day, oi] = np.mean((bits >> (day - 1)) & 1, axis=0)
    return sun


def load_temperature_archive(
    temp_json_path: Path,
    expected_years: list[int] = [2024, 2025],
) -> dict[str, float]:
    """Parse Open-Meteo archive JSON schema: hourly.time and hourly.temperature_2m.

    MANDATORY VALIDATION:
    - UTC offset must be 0 (utc_offset_seconds == 0 or timezone == UTC).
    - hourly_units.temperature_2m must be '°C' (or equivalent).
    - Timestamps must be exact hourly (:00 or :00:00), no non-zero minutes/seconds.
    - Enforces full unique coverage for 2024 (8,784h) and 2025 (8,760h).
    """
    raw = json.loads(temp_json_path.read_text(encoding="utf-8"))

    # Validate UTC offset 0
    utc_offset = raw.get("utc_offset_seconds")
    tz_name = raw.get("timezone")
    if utc_offset is not None and int(utc_offset) != 0:
        raise ValueError(f"Temperature file {temp_json_path} must have utc_offset_seconds == 0, got {utc_offset}")
    if tz_name is not None and str(tz_name).upper() not in ("UTC", "GMT", "+00:00", "ETC/UTC", "ETC/GMT"):
        raise ValueError(f"Temperature file {temp_json_path} must be UTC timezone, got {tz_name}")

    if utc_offset is None and tz_name is None:
        raise ValueError("Temperature timezone/UTC offset metadata is required")

    # Validate hourly_units temperature in °C
    hourly_units = raw.get("hourly_units", {})
    temp_unit = hourly_units.get("temperature_2m")
    if temp_unit not in ("°C", "C", "degC", "celsius"):
        raise ValueError(
            f"Temperature file {temp_json_path} hourly_units.temperature_2m must be '°C', got {temp_unit!r}"
        )

    hourly = raw.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    if len(times) != len(temps):
        raise ValueError(
            f"Temperature file {temp_json_path} length mismatch: "
            f"{len(times)} timestamps vs {len(temps)} values"
        )

    temp_by_hour: dict[str, float] = {}
    for t_str, temp_val in zip(times, temps):
        if not t_str:
            raise ValueError(f"Empty timestamp in temperature file {temp_json_path}")
        s = t_str.strip()

        timestamp = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if timestamp.utcoffset() not in (None, dt.timedelta(0)):
            raise ValueError(f"Temperature timestamp must be UTC: {s}")
        if timestamp.minute or timestamp.second or timestamp.microsecond:
            raise ValueError(f"Temperature timestamp {s} is not exact hourly")
        canon = timestamp.strftime("%Y-%m-%dT%H:00:00Z")
        if canon in temp_by_hour:
            raise ValueError(f"Duplicate temperature timestamp rejected: {canon}")

        if temp_val is None or isinstance(temp_val, bool):
            raise ValueError(f"Null or boolean temperature at hour {canon}")
        f_val = float(temp_val)
        if not math.isfinite(f_val):
            raise ValueError(f"Non-finite temperature {f_val} at hour {canon}")
        temp_by_hour[canon] = f_val

    missing: list[str] = []
    for y in expected_years:
        hours_cnt = 8784 if calendar.isleap(y) else 8760
        for j in range(hours_cnt):
            utc = dt.datetime(y, 1, 1, 0, 0, tzinfo=dt.timezone.utc) + dt.timedelta(hours=j)
            c = utc.strftime("%Y-%m-%dT%H:00:00Z")
            if c not in temp_by_hour:
                missing.append(c)

    if missing:
        raise ValueError(
            f"Temperature file {temp_json_path} missing required hours (count {len(missing)}): "
            f"{missing[:20]}..."
        )
    return temp_by_hour


def derive_load_lookup_from_baseline_2023(
    base_load_standard: np.ndarray,
    base_month: np.ndarray,
    base_valid: np.ndarray,
    audit_monthly_kwh: list[float],
) -> dict[tuple[int, int, int], float]:
    """Derive unscaled hourly load lookup (month, day, hour) from baseline year 2023 (index 18).

    Inverts monthly normalization so standard profile can be projected to 2024/2025.
    Feb 29 is interpolated as the average of Feb 28 and Mar 1.
    """
    yi_2023 = 18
    inv_factors = np.ones(12)
    for m in range(1, 13):
        use = (base_month[yi_2023] == m) & base_valid[yi_2023]
        s = float(base_load_standard[yi_2023, use].sum())
        m_kwh = float(audit_monthly_kwh[m - 1])
        if s > 0 and m_kwh > 0:
            inv_factors[m - 1] = s / m_kwh

    lookup: dict[tuple[int, int, int], float] = {}
    for j in range(8760):
        utc = dt.datetime(2023, 1, 1, 0, 0, tzinfo=dt.timezone.utc) + dt.timedelta(hours=j)
        local = utc + dt.timedelta(hours=1)
        m, d, h = local.month, local.day, local.hour
        lookup[(m, d, h)] = float(base_load_standard[yi_2023, j]) * inv_factors[m - 1]

    for h in range(24):
        lookup[(2, 29, h)] = (lookup[(2, 28, h)] + lookup[(3, 1, h)]) / 2.0

    return lookup


def build_candidate(manifest_path: Union[str, Path], output_dir: Union[str, Path]) -> dict[str, Any]:
    """Build candidate 2005-2025 merged arrays matching original format.

    Strictly enforces:
    - out_dir must not already exist (mkdir exist_ok=False) to prevent overwrite.
    - Standard loader file naming: out_dir / 'hourly.npz' and out_dir / 'audit.json'.
    - Mandatory temperature_file.
    - Hierarchy: primary v6 SARAH -> valid v5 fallback through 2023 -> v6_gap ERA5.
    - Even on failure, exact ALL-source missing hours JSON is written to out_dir.
    """
    manifest_p = Path(manifest_path).resolve()
    manifest = json.loads(manifest_p.read_text(encoding="utf-8"))
    out_dir = Path(output_dir).resolve()

    # Baseline path resolution
    base_npz_path = Path(manifest.get("baseline_npz", "feasibility/data/hourly.npz")).resolve()
    base_audit_path = Path(manifest.get("baseline_audit", "feasibility/data/audit.json")).resolve()

    if out_dir == base_npz_path.parent:
        raise ValueError(
            f"Refusing to write to baseline directory: {out_dir}. "
            "Output must be a new, immutable candidate directory."
        )

    # Output directory must NOT already exist (prevent overwrite candidate)
    if out_dir.exists():
        raise FileExistsError(
            f"Candidate output directory already exists: {out_dir}. "
            "Output directory must be new and immutable."
        )
    out_dir.mkdir(parents=True, exist_ok=False)

    # Mandatory temperature_file check
    temp_file = manifest.get("temperature_file") or manifest.get("temperature_json")
    if not temp_file:
        raise ValueError("Manifest missing mandatory 'temperature_file' parameter")
    temp_path = Path(temp_file).resolve()
    if not temp_path.exists():
        raise FileNotFoundError(f"Mandatory temperature file not found: {temp_path}")

    # Load baseline
    base_npz = np.load(base_npz_path)
    base_audit = json.loads(base_audit_path.read_text(encoding="utf-8"))
    audit_monthly_kwh = base_audit["monthly_kwh"]

    # Candidate audit structure initialized early to collect documented discrepancies
    candidate_audit = dict(base_audit)
    candidate_audit["documented_discrepancies"] = []

    # Initialize 21-year candidate arrays (2005..2025)
    shape_5d = (5, 21, 8784)
    shape_2d = (21, 8784)

    power = np.zeros(shape_5d, dtype=np.float64)
    beam_loss = np.zeros(shape_5d, dtype=np.float64)
    diffuse_loss = np.zeros(shape_5d, dtype=np.float64)
    load_civil = np.zeros(shape_2d, dtype=np.float64)
    load_standard = np.zeros(shape_2d, dtype=np.float64)
    month = np.zeros(shape_2d, dtype=np.int16)
    hour = np.zeros(shape_2d, dtype=np.int16)
    temperature = np.zeros(shape_2d, dtype=np.float64)
    valid = np.zeros(shape_2d, dtype=bool)

    # 1. PARITY RULE: Legacy 2005-2013 slices MUST be bitwise unchanged
    power[:, :9, :] = base_npz["power"][:, :9, :]
    beam_loss[:, :9, :] = base_npz["beam_loss"][:, :9, :]
    diffuse_loss[:, :9, :] = base_npz["diffuse_loss"][:, :9, :]
    load_civil[:9, :] = base_npz["load_civil"][:9, :]
    load_standard[:9, :] = base_npz["load_standard"][:9, :]
    month[:9, :] = base_npz["month"][:9, :]
    hour[:9, :] = base_npz["hour"][:9, :]
    temperature[:9, :] = base_npz["temperature"][:9, :]
    valid[:9, :] = base_npz["valid"][:9, :]

    # 2. OVERLAPPING RULE: 2014-2023 leaves load, month, hour, temp baseline unchanged
    load_civil[9:19, :] = base_npz["load_civil"][9:19, :]
    load_standard[9:19, :] = base_npz["load_standard"][9:19, :]
    month[9:19, :] = base_npz["month"][9:19, :]
    hour[9:19, :] = base_npz["hour"][9:19, :]
    temperature[9:19, :] = base_npz["temperature"][9:19, :]
    valid[9:19, :] = base_npz["valid"][9:19, :]

    # 3. Derive 2024-2025 calendar and load
    lookup = derive_load_lookup_from_baseline_2023(
        base_npz["load_standard"], base_npz["month"], base_npz["valid"], audit_monthly_kwh
    )
    for yi_idx, year in [(19, 2024), (20, 2025)]:
        hours_cnt = 8784 if calendar.isleap(year) else 8760
        for j in range(hours_cnt):
            utc = dt.datetime(year, 1, 1, 0, 0, tzinfo=dt.timezone.utc) + dt.timedelta(hours=j)
            local = utc + dt.timedelta(hours=1)
            civil = utc.astimezone(ZoneInfo("Europe/Rome"))
            load_civil[yi_idx, j] = lookup[(civil.month, civil.day, civil.hour)]
            load_standard[yi_idx, j] = lookup[(local.month, local.day, local.hour)]
            month[yi_idx, j] = local.month
            hour[yi_idx, j] = local.hour
            valid[yi_idx, j] = True

    for load_arr in [load_civil, load_standard]:
        for yi_idx in [19, 20]:
            for m in range(1, 13):
                use = (month[yi_idx] == m) & valid[yi_idx]
                s_m = float(load_arr[yi_idx, use].sum())
                if s_m > 0:
                    load_arr[yi_idx, use] *= float(audit_monthly_kwh[m - 1]) / s_m

    # 4. Mandatory Temperature for 2024/2025
    temp_map = load_temperature_archive(temp_path, expected_years=[2024, 2025])
    for yi_idx, year in [(19, 2024), (20, 2025)]:
        hours_cnt = 8784 if calendar.isleap(year) else 8760
        for j in range(hours_cnt):
            utc = dt.datetime(year, 1, 1, 0, 0, tzinfo=dt.timezone.utc) + dt.timedelta(hours=j)
            c = utc.strftime("%Y-%m-%dT%H:00:00Z")
            temperature[yi_idx, j] = temp_map[c]

    # 5. Shading / sun matrix calculation
    shading_cfg = manifest.get("shading", {})
    if "layers_dir" in shading_cfg and "solar_building_insights" in shading_cfg:
        sun = compute_sun_from_rasters(
            Path(shading_cfg["layers_dir"]).resolve(),
            Path(shading_cfg["solar_building_insights"]).resolve(),
            [roof['segments'] for roof in base_audit['roofs']],
        )
    elif "precomputed_path" in shading_cfg:
        sun_p = Path(shading_cfg["precomputed_path"]).resolve()
        sun = np.load(sun_p, allow_pickle=True).item()
    else:
        raise ValueError(
            "Manifest shading section must provide 'layers_dir' + 'solar_building_insights' "
            "or 'precomputed_path'"
        )

    # 6. Parse and merge weather files for 2014-2025
    orientations_manifest = manifest.get("orientations", {})
    missing_hours: list[dict[str, str]] = []
    provenance_detail: dict[str, dict[str, Any]] = {}
    total_era5_gap_hours = 0

    for oi, name in enumerate(ORIENTATIONS):
        cfg_item = orientations_manifest.get(name)
        if not cfg_item:
            raise ValueError(f"Manifest missing orientation configuration for '{name}'")

        # Parse primary v6 (SARAH) and validate config
        v6_path = Path(cfg_item["v6_file"]).resolve()
        v6_cfg, v6_rows, v6_hash = parse_v6_source(v6_path)
        validate_orientation_config(
            v6_cfg, cfg_item, name, f"v6 file ({v6_path.name})",
            is_v5=False, candidate_audit=candidate_audit
        )

        # Parse v5 fallback and validate config
        v5_rows: dict[str, dict[str, Any]] = {}
        v5_hash: Optional[str] = None
        if "v5_fallback_file" in cfg_item or "v5_file" in cfg_item:
            v5_p = Path(cfg_item.get("v5_fallback_file") or cfg_item["v5_file"]).resolve()
            v5_cfg, v5_rows, v5_hash = parse_v5_source(v5_p)
            validate_orientation_config(
                v5_cfg, cfg_item, name, f"v5 fallback ({v5_p.name})",
                is_v5=True, candidate_audit=candidate_audit
            )

        # Parse optional v6_gap_file (ERA5) and validate config
        v6_gap_rows: dict[str, dict[str, Any]] = {}
        v6_gap_hash: Optional[str] = None
        gap_file_key = next((k for k in ("v6_gap_file", "v6_era5_gap_file", "v6_gap_path") if k in cfg_item), None)
        if gap_file_key:
            v6_gap_p = Path(cfg_item[gap_file_key]).resolve()
            v6_gap_cfg, v6_gap_rows, v6_gap_hash = parse_v6_source(v6_gap_p)
            validate_orientation_config(
                v6_gap_cfg, cfg_item, name, f"v6 ERA5 gap file ({v6_gap_p.name})",
                is_v5=False, candidate_audit=candidate_audit
            )

        v6_count, v5_count, v6_gap_count = 0, 0, 0
        hour_prov: dict[str, dict[str, Any]] = {}

        # Apply merged weather to 2014-2025 (yi: 9..20)
        # Hierarchy: primary v6 SARAH -> valid v5 fallback through 2023 -> v6_gap ERA5
        for year in range(2014, 2026):
            yi = year - 2005
            hours_cnt = 8784 if calendar.isleap(year) else 8760
            for j in range(hours_cnt):
                utc = dt.datetime(year, 1, 1, 0, 0, tzinfo=dt.timezone.utc) + dt.timedelta(hours=j)
                c_hour = utc.strftime("%Y-%m-%dT%H:00:00Z")
                local = utc + dt.timedelta(hours=1)

                chosen: Optional[dict[str, Any]] = None
                if c_hour in v6_rows:
                    chosen = v6_rows[c_hour]
                    v6_count += 1
                elif year <= 2023 and c_hour in v5_rows:
                    chosen = v5_rows[c_hour]
                    v5_count += 1
                elif v6_gap_rows and c_hour in v6_gap_rows:
                    chosen = dict(v6_gap_rows[c_hour])
                    chosen["source"] = "v6_era5_gap"
                    chosen["source_hash"] = v6_gap_hash
                    v6_gap_count += 1
                else:
                    missing_hours.append({"orientation": name, "canonical_hour": c_hour})
                    continue

                hour_prov[c_hour] = {
                    "source": chosen["source"],
                    "timestamp": chosen["timestamp"],
                    "power_w": chosen["power_w"],
                    "null_components": chosen.get("null_components", False),
                    "source_hash": chosen.get("source_hash"),
                }

                # Shading visibility calculation
                if local.month == 2 and local.day == 29:
                    vis = (sun[2, 28, oi][local.hour] + sun[3, 1, oi][local.hour]) / 2.0
                else:
                    vis = sun[local.month, local.day, oi][local.hour]

                p = float(chosen["power_w"]) / 1000.0
                if p == 0.0:
                    # Zero power: beam and diffuse energy losses are identically 0
                    power[oi, yi, j] = 0.0
                    beam_loss[oi, yi, j] = 0.0
                    diffuse_loss[oi, yi, j] = 0.0
                else:
                    dir_irr = float(chosen["direct"])
                    diff_irr = float(chosen["diffuse"])
                    tot_irr = max(dir_irr + diff_irr, 1e-12)

                    power[oi, yi, j] = p
                    beam_loss[oi, yi, j] = p * (1.0 - vis) * dir_irr / tot_irr
                    diffuse_loss[oi, yi, j] = p * (1.0 - vis) * diff_irr / tot_irr

        total_era5_gap_hours += v6_gap_count
        provenance_detail[name] = {
            "v6_sarah_hours": v6_count,
            "v5_fallback_hours": v5_count,
            "v6_era5_gap_hours": v6_gap_count,
            "zero_power_null_component_hours": sum(1 for h in hour_prov.values() if h.get("null_components")),
            "total_updated_hours": v6_count + v5_count + v6_gap_count,
            "sources": {
                "v6_sarah_hash": v6_hash,
                "v5_fallback_hash": v5_hash,
                "v6_era5_gap_hash": v6_gap_hash,
            },
            "hours": hour_prov,
        }

    # Missing roof hours MUST fail explicitly; write exact all-source missing hours to out_dir
    if missing_hours:
        missing_path = out_dir / "missing_hours.json"
        missing_path.write_text(json.dumps(missing_hours, indent=2), encoding="utf-8")
        raise ValueError(
            f"Candidate build failed: {len(missing_hours)} missing all-source roof hour(s) detected. "
            f"Exact missing hours list written to {missing_path}."
        )

    # Record supplementary reanalysis limitation if ERA5 gaps were used
    if total_era5_gap_hours > 0:
        limitations = candidate_audit.setdefault("limitations", [])
        limitations.append(
            f"Supplementary reanalysis: {total_era5_gap_hours} hour(s) filled using PVGIS-ERA5 supplemental data "
            "where primary SARAH was unavailable. Reanalysis exhibits differing spectral and spatial characteristics "
            "relative to satellite-derived SARAH."
        )

    # Calculate annual yields
    yields: dict[str, Any] = {}
    for oi, name in enumerate(ORIENTATIONS):
        yields[name] = {
            "unshaded_annual": power[oi].sum(axis=1).tolist(),
            "beam_shaded_annual": (power[oi] - beam_loss[oi]).sum(axis=1).tolist(),
            "all_radiation_shaded_annual": (power[oi] - beam_loss[oi] - diffuse_loss[oi]).sum(axis=1).tolist(),
        }

    # Save immutable outputs matching loader conventions
    candidate_npz_path = out_dir / "hourly.npz"
    np.savez_compressed(
        candidate_npz_path,
        power=power,
        beam_loss=beam_loss,
        diffuse_loss=diffuse_loss,
        load_civil=load_civil,
        load_standard=load_standard,
        month=month,
        hour=hour,
        temperature=temperature,
        valid=valid,
        years=np.array(CANDIDATE_YEARS),
    )

    candidate_audit["weather_years"] = CANDIDATE_YEARS
    candidate_audit["pvgis_yields"] = yields
    candidate_audit["candidate_provenance_summary"] = {
        name: {
            "v6_sarah_hours": p["v6_sarah_hours"],
            "v5_fallback_hours": p["v5_fallback_hours"],
            "v6_era5_gap_hours": p["v6_era5_gap_hours"],
            "zero_power_null_component_hours": p["zero_power_null_component_hours"],
        }
        for name, p in provenance_detail.items()
    }
    candidate_audit["temperature_extension"] = {
        "provider": "Open-Meteo / ECMWF ERA5-Land reanalysis", "years": [2024, 2025],
        "sha256": hashlib.sha256(temp_path.read_bytes()).hexdigest(), "hours": len(temp_map),
        "timezone": "UTC", "unit": "°C", "leap_day_hours": 24,
        "caveat": "0.1 degree gridded reanalysis with default elevation downscaling; not site measurements or TMY."
    }
    candidate_audit.setdefault("limitations", []).append(
        "2024–2025 hourly temperatures use ERA5-Land historical reanalysis via Open-Meteo; earlier temperatures remain original PVGIS. Grid/elevation differences are a source sensitivity."
    )
    candidate_audit["manifest_applied"] = manifest

    (out_dir / "audit.json").write_text(json.dumps(candidate_audit, indent=2))
    (out_dir / "provenance.json").write_text(json.dumps(provenance_detail, indent=2))
    (out_dir / "manifest_applied.json").write_text(json.dumps(manifest, indent=2))

    return {
        "status": "success",
        "output_dir": str(out_dir),
        "years": CANDIDATE_YEARS,
        "hours": int(valid.sum()),
        "provenance_summary": candidate_audit["candidate_provenance_summary"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build candidate 2005-2025 weather arrays")
    parser.add_argument("--manifest", required=True, help="Path to input JSON manifest")
    parser.add_argument("--out-dir", required=True, help="Path to new immutable output directory")
    args = parser.parse_args()
    res = build_candidate(args.manifest, args.out_dir)
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
