"""evidence_formats.py: Standard library parser for solar evidence uploads.

Supports PVGIS (CSV/TSV/JSON), Google Solar API, and GeoTIFF.
Exposes validate_dataset and iter_pvgis_rows.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import re
import struct
from typing import Any, Dict, Iterator, List, Optional, Tuple


def _first_not_none(*values: Any) -> Any:
    for v in values:
        if v is not None:
            return v
    return None


def _clean_str(val: Any) -> str:
    s = val if isinstance(val, str) else str(val)
    no_html = re.sub(r"<[^>]*>", "", s)
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "", no_html).strip()


def _check_finite_range(name: str, val: Any, min_v: Optional[float], max_v: Optional[float]) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise ValueError(f"Metadata {name} must be numeric, got {type(val).__name__}")
    f = float(val)
    if not math.isfinite(f):
        raise ValueError(f"Metadata {name} must be finite")
    if min_v is not None and f < min_v:
        raise ValueError(f"Metadata {name} out of range: {f} < {min_v}")
    if max_v is not None and f > max_v:
        raise ValueError(f"Metadata {name} out of range: {f} > {max_v}")
    return f


def _build_configuration(
    lat: Optional[float], lon: Optional[float], tilt: Optional[float],
    azimuth: Optional[float], power_kwp: Optional[float], loss: Optional[float]
) -> Dict[str, float]:
    cfg: Dict[str, float] = {}
    lat_f = _check_finite_range("latitude", lat, -90.0, 90.0)
    lon_f = _check_finite_range("longitude", lon, -180.0, 180.0)
    if lat_f is not None and lon_f is not None:
        cfg["latitude"], cfg["longitude"] = lat_f, lon_f
    tilt_f = _check_finite_range("tilt_deg", tilt, 0.0, 90.0)
    if tilt_f is not None: cfg["tilt_deg"] = tilt_f
    az_f = _check_finite_range("azimuth_deg", azimuth, -180.0, 180.0)
    if az_f is not None: cfg["azimuth_deg"] = az_f
    p_f = _check_finite_range("power_kwp", power_kwp, 0.0, None)
    if p_f is not None: cfg["power_kwp"] = p_f
    loss_f = _check_finite_range("loss_percent", loss, -100.0, 100.0)
    if loss_f is not None: cfg["loss_percent"] = loss_f
    return cfg


def _parse_timestamp(val: str) -> datetime:
    s = val.strip().strip('"').strip("'")
    if not s:
        raise ValueError("Empty timestamp string")
    if "." in s:
        s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    if len(s) >= 13 and s[8] == ":" and s[:8].isdigit():
        time_part = s[9:]
        fmt = "%Y%m%d:%H%M%S" if (len(time_part) == 6 or ":" in time_part) else "%Y%m%d:%H%M"
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError as e:
            raise ValueError(f"Invalid timestamp format: {s}") from e
    s_iso = s.replace("Z", "+00:00")
    if " " in s_iso and "T" not in s_iso:
        s_iso = s_iso.replace(" ", "T")
    try:
        dt = datetime.fromisoformat(s_iso)
    except ValueError as e:
        raise ValueError(f"Invalid timestamp format: {s}") from e
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _validate_tiff(data: bytes) -> Optional[Dict[str, Any]]:
    if len(data) < 4:
        return None
    magic = data[:4]
    if magic not in (b"II\x2a\x00", b"MM\x00\x2a", b"II\x2b\x00", b"MM\x00\x2b"):
        return None
    if magic in (b"II\x2a\x00", b"MM\x00\x2a"):
        if len(data) < 8: raise ValueError("Truncated classic TIFF header")
        endian = "<" if magic[:2] == b"II" else ">"
        offset = struct.unpack_from(f"{endian}I", data, 4)[0]
    else:
        if len(data) < 16: raise ValueError("Truncated BigTIFF header")
        endian = "<" if magic[:2] == b"II" else ">"
        b_sz, zero = struct.unpack_from(f"{endian}HH", data, 4)
        if b_sz != 8 or zero != 0: raise ValueError("Malformed BigTIFF header")
        offset = struct.unpack_from(f"{endian}Q", data, 8)[0]
    if not (0 <= offset < len(data)):
        raise ValueError("TIFF IFD offset out of range")
    return {
        "provider": "Raster", "format": "GeoTIFF/TIFF",
        "warnings": ["TIFF header identified; raster geometry and shading compatibility have not been checked."],
    }


def _process_time_series(records: List[Tuple[str, datetime, Optional[float]]], has_components: bool) -> Dict[str, Any]:
    if not records:
        raise ValueError("No valid data rows found")
    row_count = len(records)
    valid_count, missing_count, neg_count = 0, 0, 0
    prev_bucket: Optional[datetime] = None
    years_set = set()
    hourly_gaps = 0

    for _, dt, p in records:
        if p is None: missing_count += 1
        elif p < 0: neg_count += 1
        else: valid_count += 1
        bucket = dt.replace(minute=0, second=0, microsecond=0)
        if prev_bucket is not None:
            if bucket == prev_bucket:
                raise ValueError(f"Duplicate hour bucket rejected: {bucket.strftime('%Y-%m-%dT%H:00:00Z')}")
            if bucket < prev_bucket:
                raise ValueError(f"Out of order timestamp: {dt.isoformat()}")
            gap_h = int((bucket - prev_bucket).total_seconds() // 3600) - 1
            if gap_h > 0: hourly_gaps += gap_h
        prev_bucket = bucket
        years_set.add(dt.year)

    start_dt, end_dt = records[0][1], records[-1][1]
    years_list = sorted(years_set)
    warnings: List[str] = []
    if missing_count > 0:
        warnings.append(f"{missing_count} unavailable hour(s) (null power) detected in PVGIS time series.")
    if neg_count > 0:
        warnings.append(f"{neg_count} negative v6 power sample(s) excluded from coverage; fallback can supply these hours.")
    if hourly_gaps > 0:
        warnings.append(f"{hourly_gaps} hourly gap(s) detected in time series.")
    if not has_components:
        warnings.append("Components Gb(i), Gd(i), Gr(i) absent; directional shading not supported yet.")
    if valid_count < len(years_list) * 8760 * 0.95:
        warnings.append("Partial year(s) or incomplete coverage detected in time series.")

    return {
        "row_count": row_count, "valid_row_count": valid_count,
        "missing_power_rows": missing_count, "negative_power_rows": neg_count,
        "unavailable_power_rows": missing_count + neg_count,
        "start": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), "end": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "years": years_list, "hourly_gaps": hourly_gaps, "warnings": warnings,
    }


def _parse_csv(filename: str, lines: List[str]) -> Tuple[Dict[str, Any], Iterator[Tuple[str, datetime, Optional[float]]]]:
    meta: Dict[str, Any] = {}
    warnings: List[str] = []
    lat, lon, tilt_val, az_val, power_val, loss_val = None, None, None, None, None, None
    version = "unknown"
    fn = filename or ""
    is_v6 = bool(re.search(r"pvgis[-_ ]*6", fn, re.I))

    header_idx, delimiter = -1, None
    time_col, power_col, month_col = -1, -1, -1
    has_components, is_monthly = False, False
    time_names, power_names = {"time", "timestamp", "timestamps"}, {"p", "power"}

    for idx, line in enumerate(lines[:80]):
        cleaned = line.strip()
        if not cleaned: continue
        v_m = re.search(r"pvgis\s*(?:version[:\s]*)?([56](?:\.\d+)?)", cleaned, re.I)
        if v_m and version == "unknown":
            version = v_m.group(1)
            if version.startswith("6"): is_v6 = True

        for cand in ("\t", ",", ";"):
            cols = [c.strip().strip('"').strip("'").lower() for c in line.split(cand)]
            t_c = next((i for i, c in enumerate(cols) if c in time_names), -1)
            p_c = next((i for i, c in enumerate(cols) if c in power_names), -1)
            if t_c != -1 and p_c != -1:
                header_idx, delimiter, time_col, power_col = idx, cand, t_c, p_c
                raw_c = [c.strip().strip('"').strip("'").lower() for c in line.split(cand)]
                has_components = "gb(i)" in raw_c and "gd(i)" in raw_c and "gr(i)" in raw_c
                break
            if "month" in cols and any(k in cols for k in ("e_d", "e_m", "h(i)_d", "h(i)_m")):
                header_idx, delimiter, month_col = idx, cand, cols.index("month")
                is_monthly = True
                break
        if header_idx != -1:
            break

        if ":" in cleaned or "\t" in cleaned:
            parts = re.split(r"[:\t]", cleaned, maxsplit=1)
            if len(parts) >= 2:
                k, v = parts[0].strip().lower(), parts[1].strip()
                m_num = re.search(r"[-+]?\d+(?:\.\d+)?", v)
                val_f = float(m_num.group(0)) if m_num else None
                if "latitude" in k and val_f is not None: lat = val_f
                elif "longitude" in k and val_f is not None: lon = val_f
                elif "radiation database" in k: meta["radiation_database"] = _clean_str(v)
                elif ("slope" in k or "tilt" in k) and val_f is not None: tilt_val = val_f
                elif "azimuth" in k and val_f is not None: az_val = val_f
                elif ("nominal power" in k or "system power" in k) and val_f is not None: power_val = val_f
                elif "loss" in k and val_f is not None: loss_val = val_f

    if header_idx == -1 or delimiter is None:
        raise ValueError("PVGIS CSV/TSV header not found within 80 lines")
    if lat is not None and lon is not None: meta["coords"] = {"latitude": lat, "longitude": lon}
    if tilt_val is not None: meta["tilt"] = tilt_val
    if power_val is not None: meta["system_power_kwp"] = power_val
    if loss_val is not None: meta["loss_percent"] = loss_val

    header_block = "\n".join(lines[:header_idx])
    legacy_header_pattern = bool(
        re.search(r"Azimuth\s*:\s*[-+]?\d+(?:\.\d+)?\s*deg", header_block, re.I)
        or re.search(r"Slope\s*:\s*[-+]?\d+(?:\.\d+)?\s*deg", header_block, re.I)
    )
    az_norm: Optional[float] = None
    if az_val is not None:
        if legacy_header_pattern or version.startswith("5"):
            az_norm = az_val
            meta["azimuth"] = az_norm
        elif is_v6 or (is_monthly and az_val > 0) or re.search(r"Azimuth\s*\(deg\.\)", header_block, re.I):
            az_norm = (az_val % 360.0) - 180.0
            meta["azimuth"] = az_norm
            meta["surface_orientation_north_clockwise"] = az_val
        elif az_val <= 0:
            az_norm = az_val
            meta["azimuth"] = az_norm
        else:
            warnings.append("Ambiguous azimuth convention in CSV metadata; azimuth_deg omitted from configuration.")

    config = _build_configuration(lat, lon, tilt_val, az_norm, power_val, loss_val)

    if is_monthly:
        seen_months = set()
        for line in lines[header_idx + 1:]:
            raw = line.strip()
            if not raw: break
            if raw.lower().startswith("year"): continue
            if raw.lower().startswith("pvgis") or (":" in raw and not raw[0].isdigit()): break
            parts = [p.strip() for p in line.split(delimiter)]
            if len(parts) <= month_col or not parts[month_col].isdigit(): break
            m_num = int(parts[month_col])
            if not (1 <= m_num <= 12): raise ValueError(f"Invalid month number: {m_num}")
            if m_num in seen_months: raise ValueError(f"Duplicate month number: {m_num}")
            seen_months.add(m_num)
            for c_i, c_v in enumerate(parts):
                if c_i != month_col and c_v:
                    f_val = float(c_v)
                    if not math.isfinite(f_val): raise ValueError(f"Non-finite energy value in monthly row: '{c_v}'")

        if len(seen_months) != 12:
            raise ValueError(f"Monthly summary requires unique 12 months 1..12 (got {len(seen_months)})")
        info = {
            "provider": "PVGIS", "format": "CSV", "kind": "monthly_summary",
            "row_count": 12, "pvgis_version": version if version != "unknown" else ("6" if is_v6 else "unknown"),
            "configuration": config,
            "warnings": ["Monthly summary only; no hourly coverage. Cannot replace hourly production data."] + warnings,
            "metadata": meta,
        }
        return info, iter(())

    info = {
        "provider": "PVGIS", "format": "CSV", "kind": "hourly", "power_unit": "W",
        "pvgis_version": version if version != "unknown" else ("6" if is_v6 else "unknown"),
        "configuration": config, "has_components": has_components,
        "warnings": warnings, "metadata": meta,
    }
    def _gen_csv():
        seen_rows = 0
        in_footer = False
        for line in lines[header_idx + 1:]:
            raw = line.strip()
            if not raw:
                in_footer = True
                continue
            if in_footer: continue
            if seen_rows > 0 and (raw.startswith("P:") or raw.startswith("PVGIS") or (":" in raw and not raw[0].isdigit())):
                break
            parts = [p.strip() for p in line.split(delimiter)]
            if len(parts) <= max(time_col, power_col):
                raise ValueError(f"Malformed or short data row: '{line}'")
            t_str = parts[time_col].strip().strip('"').strip("'")
            if not t_str: raise ValueError("Empty timestamp in data row")
            dt = _parse_timestamp(t_str)
            p_str = parts[power_col]
            try: p_val = float(p_str)
            except ValueError: raise ValueError(f"Non-numeric power '{p_str}' in row: '{line}'")
            if not math.isfinite(p_val) or p_val < 0:
                raise ValueError(f"Invalid power '{p_val}' in row: '{line}'")
            seen_rows += 1
            yield (t_str, dt, p_val)
    return info, _gen_csv()


def _extract_v6_meta_and_cfg(obj: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, float]]:
    meta: Dict[str, Any] = {}
    inp = obj.get("inputs") if isinstance(obj.get("inputs"), dict) else {}
    is_rad = str(_first_not_none(obj.get("unit"), obj.get("units"), obj.get("angle_unit"), "")).lower() in ("radians", "rad")

    tilt_top = obj.get("surface_tilt")
    tilt_f = None
    if tilt_top is not None and isinstance(tilt_top, (int, float)) and not isinstance(tilt_top, bool):
        tilt_f = math.degrees(float(tilt_top)) if is_rad else float(tilt_top)
    else:
        tilt_inp = inp.get("surface_tilt")
        if tilt_inp is not None and isinstance(tilt_inp, (int, float)) and not isinstance(tilt_inp, bool):
            tilt_f = float(tilt_inp)
    if tilt_f is not None:
        tilt_f = round(tilt_f, 6)
        meta["tilt"] = tilt_f

    orient_top = obj.get("surface_orientation")
    orient_deg = None
    if orient_top is not None and isinstance(orient_top, (int, float)) and not isinstance(orient_top, bool):
        orient_deg = math.degrees(float(orient_top)) if is_rad else float(orient_top)
    else:
        orient_inp = inp.get("surface_orientation")
        if orient_inp is not None and isinstance(orient_inp, (int, float)) and not isinstance(orient_inp, bool):
            orient_deg = float(orient_inp)

    az_norm: Optional[float] = None
    if orient_deg is not None:
        orient_deg = round(orient_deg, 6)
        az_norm = round((orient_deg % 360.0) - 180.0, 6)
        meta["azimuth"] = az_norm
        meta["surface_orientation_north_clockwise"] = orient_deg

    loc = inp.get("location") if isinstance(inp.get("location"), dict) else {}
    lat = _first_not_none(obj.get("latitude"), inp.get("latitude"), loc.get("latitude"))
    lon = _first_not_none(obj.get("longitude"), inp.get("longitude"), loc.get("longitude"))
    lat_f = float(lat) if isinstance(lat, (int, float)) and not isinstance(lat, bool) else None
    lon_f = float(lon) if isinstance(lon, (int, float)) and not isinstance(lon, bool) else None
    if lat_f is not None and lon_f is not None:
        meta["coords"] = {"latitude": lat_f, "longitude": lon_f}

    mod = inp.get("pv_module") if isinstance(inp.get("pv_module"), dict) else {}
    pp = _first_not_none(obj.get("peak_power"), obj.get("system_power_kwp"), inp.get("peak_power"), mod.get("peak_power"))
    unit = _first_not_none(obj.get("peak_power_unit"), inp.get("peak_power_unit"), "kWp")
    kwp_f = None
    if isinstance(pp, (int, float)) and not isinstance(pp, bool):
        kwp_f = float(pp) / 1000.0 if str(unit).lower() == "w" else float(pp)
        meta["system_power_kwp"] = kwp_f

    eff = _first_not_none(obj.get("system_efficiency"), inp.get("system_efficiency"))
    loss_f = None
    if isinstance(eff, (int, float)) and not isinstance(eff, bool):
        loss_f = round((1.0 - float(eff)) * 100.0, 4)
        meta["loss_percent"] = loss_f
    else:
        loss = _first_not_none(obj.get("loss_percent"), inp.get("loss"), inp.get("system_losses"))
        if isinstance(loss, (int, float)) and not isinstance(loss, bool):
            loss_f = float(loss)
            meta["loss_percent"] = loss_f

    rdb = _first_not_none(inp.get("radiation_database"), inp.get("rad_database"), obj.get("radiation_database"))
    if isinstance(rdb, str): meta["radiation_database"] = _clean_str(rdb)

    cfg = _build_configuration(lat_f, lon_f, tilt_f, az_norm, kwp_f, loss_f)
    return meta, cfg


def _extract_v5_meta_and_cfg(inputs: Any) -> Tuple[Dict[str, Any], Dict[str, float]]:
    if not isinstance(inputs, dict): return {}, {}
    meta: Dict[str, Any] = {}
    loc = inputs.get("location") if isinstance(inputs.get("location"), dict) else {}
    lat, lon = loc.get("latitude"), loc.get("longitude")
    lat_f = float(lat) if isinstance(lat, (int, float)) and not isinstance(lat, bool) else None
    lon_f = float(lon) if isinstance(lon, (int, float)) and not isinstance(lon, bool) else None
    if lat_f is not None and lon_f is not None:
        meta["coords"] = {"latitude": lat_f, "longitude": lon_f}
    ms = inputs.get("mounting_system") if isinstance(inputs.get("mounting_system"), dict) else {}
    fixed = ms.get("fixed") if isinstance(ms.get("fixed"), dict) else ms
    s, a = fixed.get("slope"), fixed.get("azimuth")
    if isinstance(s, dict): s = s.get("value")
    if isinstance(a, dict): a = a.get("value")
    tilt_f = float(s) if isinstance(s, (int, float)) and not isinstance(s, bool) else None
    az_norm = float(a) if isinstance(a, (int, float)) and not isinstance(a, bool) else None
    if tilt_f is not None: meta["tilt"] = tilt_f
    if az_norm is not None: meta["azimuth"] = az_norm
    mod = inputs.get("pv_module") if isinstance(inputs.get("pv_module"), dict) else {}
    pp = mod.get("peak_power")
    kwp_f = float(pp) if isinstance(pp, (int, float)) and not isinstance(pp, bool) else None
    if kwp_f is not None: meta["system_power_kwp"] = kwp_f
    loss = inputs.get("loss")
    loss_f = float(loss) if isinstance(loss, (int, float)) and not isinstance(loss, bool) else None
    if loss_f is not None: meta["loss_percent"] = loss_f
    rdb = inputs.get("radiation_database") or inputs.get("rad_database")
    if isinstance(rdb, str): meta["radiation_database"] = _clean_str(rdb)
    cfg = _build_configuration(lat_f, lon_f, tilt_f, az_norm, kwp_f, loss_f)
    return meta, cfg


def _parse_pvgis_stream(filename: str, data: bytes) -> Tuple[Dict[str, Any], Iterator[Tuple[str, datetime, Optional[float]]]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ValueError(f"Data not valid UTF-8/BOM text: {e}") from e

    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        def _rej(c: str) -> None: raise ValueError(f"Strict JSON disallowed constant: {c}")
        try:
            obj = json.loads(text, parse_constant=_rej)
        except Exception as e:
            raise ValueError(f"Invalid JSON: {e}") from e
        if not isinstance(obj, dict):
            raise ValueError("JSON root must be an object")

        if "outputs" in obj and isinstance(obj["outputs"], dict) and "hourly" in obj["outputs"]:
            hourly = obj["outputs"]["hourly"]
            if not isinstance(hourly, list) or not hourly:
                raise ValueError("PVGIS outputs.hourly must be non-empty list")
            has_comp = all(k in hourly[0] for k in ("Gb(i)", "Gd(i)", "Gr(i)"))
            meta, cfg = _extract_v5_meta_and_cfg(obj.get("inputs"))
            info = {
                "provider": "PVGIS", "format": "JSON", "kind": "hourly", "power_unit": "W",
                "pvgis_version": "5.3", "has_components": has_comp,
                "configuration": cfg, "metadata": meta, "warnings": [],
            }
            def _gen_v5():
                for item in hourly:
                    if not isinstance(item, dict): raise ValueError("Malformed hourly entry")
                    t_raw = item.get("time") or item.get("timestamp")
                    if not t_raw: raise ValueError("Missing time in hourly entry")
                    dt = _parse_timestamp(str(t_raw))
                    p_raw = item.get("P") if "P" in item else item.get("power")
                    if p_raw is None or isinstance(p_raw, bool): raise ValueError(f"Missing or invalid power in v5 entry: '{p_raw}'")
                    try: p_val = float(p_raw)
                    except (ValueError, TypeError): raise ValueError(f"Non-numeric power '{p_raw}'")
                    if not math.isfinite(p_val) or p_val < 0: raise ValueError(f"Invalid power '{p_val}'")
                    yield (str(t_raw), dt, p_val)
            return info, _gen_v5()

        p_key = "power" if "power" in obj else ("P" if "P" in obj else None)
        t_key = "timestamps" if "timestamps" in obj else ("time" if "time" in obj else ("timestamp" if "timestamp" in obj else None))
        if p_key and t_key:
            powers, times = obj[p_key], obj[t_key]
            if not isinstance(powers, list) or not isinstance(times, list):
                raise ValueError("PVGIS v6 power and timestamps must be lists")
            if not powers or len(powers) != len(times):
                raise ValueError("PVGIS v6 power and timestamps must be equal non-zero length")

            v6_comps = (
                "direct_inclined_irradiance_before_reflectivity", "diffuse_inclined_irradiance_before_reflectivity",
                "ground_reflected_inclined_irradiance_before_reflectivity", "direct_inclined_irradiance",
                "diffuse_inclined_irradiance", "ground_reflected_inclined_irradiance", "optical", "before_reflectivity",
            )
            rec = []
            comp_warns = []
            for k in v6_comps:
                if k in obj:
                    c_arr = obj[k]
                    if not isinstance(c_arr, list) or len(c_arr) != len(powers):
                        raise ValueError(f"Component array {k} must match power array length ({len(powers)})")
                    null_with_finite = False
                    for idx_c, c_v in enumerate(c_arr):
                        if powers[idx_c] is None:
                            if c_v is not None and (isinstance(c_v, bool) or not isinstance(c_v, (int, float)) or not math.isfinite(float(c_v))):
                                raise ValueError(f"Invalid value in component array {k}: {c_v}")
                        else:
                            if c_v is None: null_with_finite = True
                            elif isinstance(c_v, bool) or not isinstance(c_v, (int, float)) or not math.isfinite(float(c_v)):
                                raise ValueError(f"Invalid value in component array {k}: {c_v}")
                    if null_with_finite:
                        comp_warns.append(f"Component '{k}' has null value(s) with finite power; not fabricating.")
                    rec.append(k)

            has_direct = any(k in obj for k in ("direct_inclined_irradiance_before_reflectivity", "direct_inclined_irradiance"))
            has_diffuse = any(k in obj for k in ("diffuse_inclined_irradiance_before_reflectivity", "diffuse_inclined_irradiance"))
            has_ground = any(k in obj for k in ("ground_reflected_inclined_irradiance_before_reflectivity", "ground_reflected_inclined_irradiance"))
            has_comp = has_direct and has_diffuse and has_ground

            meta, cfg = _extract_v6_meta_and_cfg(obj)
            info = {
                "provider": "PVGIS", "format": "JSON", "kind": "hourly", "power_unit": "W",
                "pvgis_version": "6", "has_components": has_comp,
                "configuration": cfg, "metadata": meta, "warnings": comp_warns,
            }
            if rec: info["recognized_arrays"] = rec
            def _gen_v6():
                for t_raw, p_raw in zip(times, powers):
                    dt = _parse_timestamp(str(t_raw))
                    if p_raw is None:
                        yield (str(t_raw), dt, None)
                    else:
                        if isinstance(p_raw, bool): raise ValueError("Boolean power invalid")
                        try: p_val = float(p_raw)
                        except (ValueError, TypeError): raise ValueError(f"Non-numeric power '{p_raw}'")
                        if not math.isfinite(p_val): raise ValueError(f"Invalid non-finite power '{p_val}'")
                        yield (str(t_raw), dt, p_val)
            return info, _gen_v6()

        raise ValueError("Not a recognized PVGIS JSON schema")

    return _parse_csv(filename, text.splitlines())


def iter_pvgis_rows(filename: str, data: bytes) -> Iterator[Dict[str, Any]]:
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError("data must be bytes")
    info, gen = _parse_pvgis_stream(filename, data)
    if info.get("kind") == "monthly_summary":
        return
    for raw_ts, dt, p_val in gen:
        if p_val is None or p_val < 0:
            continue
        yield {
            "timestamp": raw_ts,
            "hour": dt.strftime("%Y-%m-%dT%H:00:00Z"),
            "power_w": p_val,
        }


def validate_dataset(filename: str, data: bytes) -> Dict[str, Any]:
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError("data must be bytes")
    tiff_res = _validate_tiff(data)
    if tiff_res is not None:
        return tiff_res

    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ValueError(f"Data could not be decoded as UTF-8/BOM text: {e}") from e

    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        def _rej(c: str) -> None: raise ValueError(f"Strict JSON disallowed constant: {c}")
        try:
            obj = json.loads(text, parse_constant=_rej)
        except Exception as e:
            raise ValueError(f"Invalid JSON: {e}") from e
        if isinstance(obj, dict):
            if "solarPotential" in obj:
                sp = obj["solarPotential"]
                if not isinstance(sp, dict) or not sp:
                    raise ValueError("GoogleSolar solarPotential must be non-empty object")
                has_seg = "roofSegmentStats" in sp and isinstance(sp["roofSegmentStats"], list)
                has_wh = "wholeRoofStats" in sp and isinstance(sp["wholeRoofStats"], dict)
                if not (has_seg or has_wh):
                    raise ValueError("GoogleSolar solarPotential missing roofSegmentStats or wholeRoofStats")
                res = {"provider": "GoogleSolar", "format": "JSON", "subtype": "buildingInsights", "warnings": []}
                if has_seg: res["segment_count"] = len(sp["roofSegmentStats"])
                return res
            if any(k in obj for k in ("dsmUrl", "rgbUrl", "annualFluxUrl", "hourlyShadeUrls")):
                return {
                    "provider": "GoogleSolar", "format": "JSON", "subtype": "dataLayers",
                    "warnings": ["dataLayers JSON only references rasters; rasters must be provided directly."],
                }

    info, gen = _parse_pvgis_stream(filename, data)
    if info.get("kind") == "monthly_summary":
        if not info.get("metadata"): info.pop("metadata", None)
        return info

    records: List[Tuple[str, datetime, Optional[float]]] = []
    for item in gen:
        records.append(item)
    base_warnings = info.pop("warnings", [])
    ts_summary = _process_time_series(records, info.get("has_components", False))
    ts_summary["warnings"] = base_warnings + ts_summary["warnings"]
    summary = {**info, **ts_summary}
    if not summary.get("metadata"):
        summary.pop("metadata", None)
    return summary
