"""Evidence dataset grouping and hourly priority coverage builder for user uploads."""
from __future__ import annotations

import math
from typing import Any, Callable, Iterable

CONFIG_KEYS = ("latitude", "longitude", "tilt_deg", "azimuth_deg", "power_kwp", "loss_percent")


def _diff_azimuth(a1: float, a2: float) -> float:
    return abs((a1 - a2 + 180.0) % 360.0 - 180.0)


def _validate_group_configs(records: list[dict[str, Any]]) -> str | None:
    for i in range(len(records)):
        c1 = records[i].get("summary", {}).get("configuration", {})
        for j in range(i + 1, len(records)):
            c2 = records[j].get("summary", {}).get("configuration", {})
            for key in ("latitude", "longitude", "power_kwp", "loss_percent"):
                v1, v2 = c1.get(key), c2.get(key)
                if v1 is not None and v2 is not None and abs(float(v1) - float(v2)) > 1e-6:
                    return f"Configuration conflict on '{key}': {v1} vs {v2}"
            v1, v2 = c1.get("tilt_deg"), c2.get("tilt_deg")
            if v1 is not None and v2 is not None and abs(float(v1) - float(v2)) > 0.01:
                return f"Configuration conflict on 'tilt_deg': {v1} vs {v2}"
            v1, v2 = c1.get("azimuth_deg"), c2.get("azimuth_deg")
            if v1 is not None and v2 is not None and _diff_azimuth(float(v1), float(v2)) > 0.01:
                return f"Configuration conflict on 'azimuth_deg': {v1} vs {v2}"
    return None


def _is_hourly_pvgis(rec: dict[str, Any]) -> bool:
    src = str(rec.get("source", "")).lower()
    kind = str(rec.get("summary", {}).get("kind", "")).lower()
    return src in ("pvgis6", "pvgis53") and kind == "hourly"


def _process_group_rows(
    group_records: list[dict[str, Any]],
    load_rows: Callable[[dict[str, Any]], Iterable[dict[str, Any]]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    v6_by_hour: dict[str, dict[str, Any]] = {}
    v53_by_hour: dict[str, dict[str, Any]] = {}

    # Sort records deterministically by ID so tie-breaking identical data is stable
    sorted_records = sorted(group_records, key=lambda r: str(r.get("id", "")))

    for rec in sorted_records:
        src = str(rec.get("source", "")).lower()
        rec_id = str(rec.get("id", ""))
        target = v6_by_hour if src == "pvgis6" else v53_by_hour

        for row in load_rows(rec):
            h = row.get("hour")
            if not isinstance(h, str) or not h.strip():
                raise ValueError(f"Invalid canonical UTC hour bucket at row in dataset '{rec_id}': {h!r}")

            pw = row.get("power_w")
            if isinstance(pw, bool) or not isinstance(pw, (int, float)) or not math.isfinite(pw) or pw < 0:
                raise ValueError(
                    f"Invalid power_w {pw!r} at hour '{h}' in dataset '{rec_id}' (must be non-negative finite float, not bool)"
                )

            ts = row.get("timestamp")
            if not isinstance(ts, str) or not ts.strip():
                raise ValueError(f"Missing or invalid timestamp string at hour '{h}' in dataset '{rec_id}': {ts!r}")

            if h in target:
                existing = target[h]
                if abs(existing["power_w"] - float(pw)) > 1e-9:
                    raise ValueError(
                        f"Conflict: duplicate overlapping hour '{h}' in {src} with differing power: "
                        f"{existing['power_w']} W vs {pw} W ({existing['dataset_id']} vs {rec_id}). User must choose files."
                    )
                # Deduplicate identical data with deterministic smaller dataset_id preference
                if rec_id < existing["dataset_id"]:
                    target[h] = {"hour": h, "power_w": float(pw), "source": src, "dataset_id": rec_id, "timestamp": ts}
            else:
                target[h] = {"hour": h, "power_w": float(pw), "source": src, "dataset_id": rec_id, "timestamp": ts}
    return v6_by_hour, v53_by_hour


def effective_rows(
    records: list[dict[str, Any]],
    load_rows: Callable[[dict[str, Any]], Iterable[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Build unified hourly solar rows for one explicit series group, applying v6 > v5.3 priority."""
    pvgis_records = [r for r in records if _is_hourly_pvgis(r)]
    if not pvgis_records:
        return []

    # Enforce exactly one non-empty casefolded series label group
    labels = {str(r.get("series_label", "")).strip().casefold() for r in pvgis_records}
    if not labels or "" in labels:
        raise ValueError("effective_rows requires all records to have a non-empty series_label")
    if len(labels) > 1:
        raise ValueError(f"effective_rows received multiple distinct series labels: {sorted(labels)}; expected exactly one")

    err = _validate_group_configs(pvgis_records)
    if err:
        raise ValueError(err)

    v6_map, v53_map = _process_group_rows(pvgis_records, load_rows)
    all_hours = sorted(set(v6_map.keys()) | set(v53_map.keys()))
    return [v6_map[h] if h in v6_map else v53_map[h] for h in all_hours]


def build_coverage(
    records: list[dict[str, Any]],
    load_rows: Callable[[dict[str, Any]], Iterable[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Group PVGIS records by series_label and calculate hourly priority coverage summaries."""
    groups: dict[str, list[dict[str, Any]]] = {}
    display_labels: dict[str, str] = {}

    for rec in records:
        if not _is_hourly_pvgis(rec):
            continue
        label = str(rec.get("series_label", "")).strip()
        if not label:
            raise ValueError(f"Hourly PVGIS dataset '{rec.get('id')}' missing required series_label")
        key = label.casefold()
        if key not in groups:
            groups[key] = []
            display_labels[key] = label
        groups[key].append(rec)

    coverage_list: list[dict[str, Any]] = []

    for key, group_recs in groups.items():
        disp_label = display_labels[key]
        warnings: list[str] = []

        if any(rec.get("summary", {}).get("configuration", {}).get(k) is None for rec in group_recs for k in CONFIG_KEYS):
            warnings.append("Matching relies on label; some records lack complete configuration metadata.")

        cfg_conflict = _validate_group_configs(group_recs)
        if cfg_conflict:
            coverage_list.append({
                "series_label": disp_label, "status": "conflict", "v6_hours": 0, "v53_hours": 0,
                "total_hours": 0, "overlap_hours": 0, "start": None, "end": None,
                "warnings": warnings, "error": cfg_conflict, "energy_kwh": 0.0,
            })
            continue

        try:
            v6_map, v53_map = _process_group_rows(group_recs, load_rows)
            v6_hours = len(v6_map)
            all_hours = sorted(set(v6_map.keys()) | set(v53_map.keys()))
            overlap_hours = sum(1 for h in all_hours if h in v6_map and h in v53_map)
            v53_hours = len(all_hours) - v6_hours
            total_hours = len(all_hours)
            selected = [v6_map[h] if h in v6_map else v53_map[h] for h in all_hours]
            energy_kwh = math.fsum(float(r["power_w"]) for r in selected) / 1000.0 if selected else 0.0
            from datetime import datetime
            uncovered_hours = 0
            if selected:
                span = datetime.fromisoformat(selected[-1]["hour"]) - datetime.fromisoformat(selected[0]["hour"])
                uncovered_hours = max(0, int(span.total_seconds() // 3600) + 1 - total_hours)
                if uncovered_hours:
                    warnings.append(f"{uncovered_hours} hour(s) have no valid value in either uploaded version; not filled.")

            coverage_list.append({
                "series_label": disp_label,
                "status": "ready" if total_hours > 0 else "empty",
                "v6_hours": v6_hours,
                "v53_hours": v53_hours,
                "total_hours": total_hours,
                "uncovered_hours": uncovered_hours,
                "overlap_hours": overlap_hours,
                "start": selected[0]["hour"] if selected else None,
                "end": selected[-1]["hour"] if selected else None,
                "warnings": warnings,
                "error": None,
                "energy_kwh": energy_kwh,
            })
        except ValueError as err:
            coverage_list.append({
                "series_label": disp_label, "status": "conflict", "v6_hours": 0, "v53_hours": 0,
                "total_hours": 0, "overlap_hours": 0, "start": None, "end": None,
                "warnings": warnings, "error": str(err), "energy_kwh": 0.0,
            })

    return coverage_list
