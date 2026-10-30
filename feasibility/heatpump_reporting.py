"""Stage 6B Heat Pump Reporting Helper Module.

Provides additive end-use breakdowns, nested heat pump diagnostic records,
and sample-weighted interval aggregations for day/hour API responses.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence
import numpy as np


HP_ENERGY_KEYS = (
    "space_compressor_electricity_kwh",
    "dhw_compressor_electricity_kwh",
    "space_backup_electricity_kwh",
    "dhw_backup_electricity_kwh",
    "auxiliary_electricity_kwh",
    "requested_space_heat_kwh",
    "delivered_space_heat_kwh",
    "unmet_space_heat_kwh",
    "requested_draw_heat_kwh",
    "delivered_draw_heat_kwh",
    "unmet_draw_heat_kwh",
    "actual_draw_heat_kwh",
    "heat_supplied_tank_kwh",
    "tank_losses_kwh",
    "space_runtime_hours",
    "dhw_runtime_hours",
    "tank_start_storage_kwh",
    "tank_end_storage_kwh",
)

HP_STATE_TEMP_CAP_KEYS = (
    "tank_temp_c",
    "space_flow_temp_c",
    "dhw_flow_temp_c",
    "space_capacity_kw",
    "dhw_capacity_kw",
    "radiator_bound_kw",
)

HP_FLAG_KEYS = (
    "map_outside_flag",
    "hygiene_target_miss",
)

END_USE_KEYS = (
    "household_kwh",
    "cooking_kwh",
    "ev_kwh",
    "heat_pump_heating_kwh",
    "heat_pump_hot_water_kwh",
    "heat_pump_auxiliary_kwh",
)


def extract_single_hour_heat_pump(
    arrays: Mapping[str, Sequence[float] | np.ndarray],
    idx: int,
) -> dict[str, Any]:
    """Extract the 26-field heat pump record for a single time index."""
    s_comp_e = float(arrays["space_compressor_electricity_kwh"][idx])
    d_comp_e = float(arrays["dhw_compressor_electricity_kwh"][idx])
    s_back_e = float(arrays["space_backup_electricity_kwh"][idx])
    d_back_e = float(arrays["dhw_backup_electricity_kwh"][idx])
    aux_e = float(arrays["auxiliary_electricity_kwh"][idx])

    return {
        "space_compressor_electricity_kwh": s_comp_e,
        "dhw_compressor_electricity_kwh": d_comp_e,
        "space_backup_electricity_kwh": s_back_e,
        "dhw_backup_electricity_kwh": d_back_e,
        "auxiliary_electricity_kwh": aux_e,
        "requested_space_heat_kwh": float(arrays["requested_space_heat_kwh"][idx]),
        "delivered_space_heat_kwh": float(arrays["delivered_space_heat_kwh"][idx]),
        "unmet_space_heat_kwh": float(arrays["unmet_space_heat_kwh"][idx]),
        "requested_draw_heat_kwh": float(arrays["requested_draw_heat_kwh"][idx]),
        "delivered_draw_heat_kwh": float(arrays["delivered_draw_heat_kwh"][idx]),
        "unmet_draw_heat_kwh": float(arrays["unmet_draw_heat_kwh"][idx]),
        "actual_draw_heat_kwh": float(arrays["actual_draw_heat_kwh"][idx]),
        "heat_supplied_tank_kwh": float(arrays["heat_supplied_tank_kwh"][idx]),
        "tank_losses_kwh": float(arrays["tank_losses_kwh"][idx]),
        "tank_temp_c": float(arrays["tank_temp_c"][idx]),
        "space_flow_temp_c": float(arrays["space_flow_temp_c"][idx]),
        "dhw_flow_temp_c": float(arrays["dhw_flow_temp_c"][idx]),
        "space_capacity_kw": float(arrays["space_capacity_kw"][idx]),
        "dhw_capacity_kw": float(arrays["dhw_capacity_kw"][idx]),
        "radiator_bound_kw": float(arrays["radiator_bound_kw"][idx]),
        "space_runtime_hours": float(arrays["space_runtime_hours"][idx]),
        "dhw_runtime_hours": float(arrays["dhw_runtime_hours"][idx]),
        "tank_start_storage_kwh": float(arrays["tank_start_storage_kwh"][idx]),
        "tank_end_storage_kwh": float(arrays["tank_end_storage_kwh"][idx]),
        "map_outside_flag": bool(arrays["map_outside_flag"][idx]),
        "hygiene_target_miss": bool(arrays["hygiene_target_miss"][idx]),
    }


def build_additive_end_uses(
    load_kwh: float,
    hp_rec: dict[str, Any],
    cooking_kwh: float,
    ev_kwh: float,
) -> dict[str, float]:
    """Construct the 6 additive end uses summing exactly to load_kwh."""
    hp_heat = float(hp_rec["space_compressor_electricity_kwh"] + hp_rec["space_backup_electricity_kwh"])
    hp_dhw = float(hp_rec["dhw_compressor_electricity_kwh"] + hp_rec["dhw_backup_electricity_kwh"])
    hp_aux = float(hp_rec["auxiliary_electricity_kwh"])
    cook = float(cooking_kwh)
    ev = float(ev_kwh)

    # Household absorbs the balance so that sum(end_uses.values()) == load_kwh exactly.
    household = float(load_kwh - (cook + ev + hp_heat + hp_dhw + hp_aux))
    if household < 0.0 and household > -1e-9:
        household = 0.0

    return {
        "household_kwh": household,
        "cooking_kwh": cook,
        "ev_kwh": ev,
        "heat_pump_heating_kwh": hp_heat,
        "heat_pump_hot_water_kwh": hp_dhw,
        "heat_pump_auxiliary_kwh": hp_aux,
    }


def aggregate_month_hour_records(
    hour_buckets: list[list[dict[str, Any]]],
    records: list[dict[str, Any]],
) -> None:
    """In-place aggregate records for monthly dayhour with arithmetic means and flags."""
    for hh in range(24):
        bucket = hour_buckets[hh]
        if not bucket:
            continue
        k = len(bucket)
        rec = next((r for r in records if r["hour"] == hh), None)
        if rec is None:
            continue

        # Arithmetic means for end uses
        m_cook = sum(r["end_uses"]["cooking_kwh"] for r in bucket) / k
        m_ev = sum(r["end_uses"]["ev_kwh"] for r in bucket) / k
        m_hp_heat = sum(r["end_uses"]["heat_pump_heating_kwh"] for r in bucket) / k
        m_hp_dhw = sum(r["end_uses"]["heat_pump_hot_water_kwh"] for r in bucket) / k
        m_hp_aux = sum(r["end_uses"]["heat_pump_auxiliary_kwh"] for r in bucket) / k
        m_household = float(rec["load_kwh"] - (m_cook + m_ev + m_hp_heat + m_hp_dhw + m_hp_aux))
        if m_household < 0.0 and m_household > -1e-9:
            m_household = 0.0

        rec["end_uses"] = {
            "household_kwh": m_household,
            "cooking_kwh": m_cook,
            "ev_kwh": m_ev,
            "heat_pump_heating_kwh": m_hp_heat,
            "heat_pump_hot_water_kwh": m_hp_dhw,
            "heat_pump_auxiliary_kwh": m_hp_aux,
        }

        # Heat pump aggregated metrics:
        hp_out: dict[str, Any] = {}
        for key in HP_ENERGY_KEYS:
            hp_out[key] = float(sum(r["heat_pump"][key] for r in bucket) / k)
        for key in HP_STATE_TEMP_CAP_KEYS:
            hp_out[key] = float(sum(r["heat_pump"][key] for r in bucket) / k)

        map_out_count = sum(1 for r in bucket if r["heat_pump"]["map_outside_flag"])
        hygiene_miss_count = sum(1 for r in bucket if r["heat_pump"]["hygiene_target_miss"])

        hp_out["map_outside_flag"] = bool(map_out_count > 0)
        hp_out["map_outside_count"] = map_out_count
        hp_out["hygiene_target_miss"] = bool(hygiene_miss_count > 0)
        hp_out["hygiene_target_miss_count"] = hygiene_miss_count

        rec["heat_pump"] = hp_out


def compute_reporting_totals(
    interval_records: Sequence[dict[str, Any]],
) -> tuple[dict[str, float], dict[str, Any]]:
    """Compute exact sample-weighted top-level end_use_totals and hp_totals."""
    if not interval_records:
        return {}, {}

    eu_totals = {
        "household_kwh": float(sum(r["end_uses"]["household_kwh"] for r in interval_records)),
        "cooking_kwh": float(sum(r["end_uses"]["cooking_kwh"] for r in interval_records)),
        "ev_kwh": float(sum(r["end_uses"]["ev_kwh"] for r in interval_records)),
        "heat_pump_heating_kwh": float(sum(r["end_uses"]["heat_pump_heating_kwh"] for r in interval_records)),
        "heat_pump_hot_water_kwh": float(sum(r["end_uses"]["heat_pump_hot_water_kwh"] for r in interval_records)),
        "heat_pump_auxiliary_kwh": float(sum(r["end_uses"]["heat_pump_auxiliary_kwh"] for r in interval_records)),
        "load_kwh": float(sum(r["load_kwh"] if "load_kwh" in r else r["load"] for r in interval_records)),
    }

    first_storage = float(interval_records[0]["heat_pump"]["tank_start_storage_kwh"])
    last_storage = float(interval_records[-1]["heat_pump"]["tank_end_storage_kwh"])

    s_comp_e = float(sum(r["heat_pump"]["space_compressor_electricity_kwh"] for r in interval_records))
    d_comp_e = float(sum(r["heat_pump"]["dhw_compressor_electricity_kwh"] for r in interval_records))
    s_back_e = float(sum(r["heat_pump"]["space_backup_electricity_kwh"] for r in interval_records))
    d_back_e = float(sum(r["heat_pump"]["dhw_backup_electricity_kwh"] for r in interval_records))
    aux_e = float(sum(r["heat_pump"]["auxiliary_electricity_kwh"] for r in interval_records))
    total_e = s_comp_e + d_comp_e + s_back_e + d_back_e + aux_e

    hp_totals = {
        "requested_space_heat_kwh": float(sum(r["heat_pump"]["requested_space_heat_kwh"] for r in interval_records)),
        "delivered_space_heat_kwh": float(sum(r["heat_pump"]["delivered_space_heat_kwh"] for r in interval_records)),
        "unmet_space_heat_kwh": float(sum(r["heat_pump"]["unmet_space_heat_kwh"] for r in interval_records)),
        "requested_draw_heat_kwh": float(sum(r["heat_pump"]["requested_draw_heat_kwh"] for r in interval_records)),
        "delivered_draw_heat_kwh": float(sum(r["heat_pump"]["delivered_draw_heat_kwh"] for r in interval_records)),
        "unmet_draw_heat_kwh": float(sum(r["heat_pump"]["unmet_draw_heat_kwh"] for r in interval_records)),
        "actual_draw_heat_kwh": float(sum(r["heat_pump"]["actual_draw_heat_kwh"] for r in interval_records)),
        "heat_supplied_tank_kwh": float(sum(r["heat_pump"]["heat_supplied_tank_kwh"] for r in interval_records)),
        "tank_losses_kwh": float(sum(r["heat_pump"]["tank_losses_kwh"] for r in interval_records)),
        "space_compressor_electricity_kwh": s_comp_e,
        "dhw_compressor_electricity_kwh": d_comp_e,
        "space_backup_electricity_kwh": s_back_e,
        "dhw_backup_electricity_kwh": d_back_e,
        "auxiliary_electricity_kwh": aux_e,
        "total_electricity_kwh": total_e,
        "initial_storage_kwh": first_storage,
        "final_storage_kwh": last_storage,
        "storage_delta_kwh": float(last_storage - first_storage),
        "space_runtime_hours": float(sum(r["heat_pump"]["space_runtime_hours"] for r in interval_records)),
        "dhw_runtime_hours": float(sum(r["heat_pump"]["dhw_runtime_hours"] for r in interval_records)),
        "map_outside_count": int(sum(1 for r in interval_records if r["heat_pump"]["map_outside_flag"])),
        "hygiene_target_miss_count": int(sum(1 for r in interval_records if r["heat_pump"]["hygiene_target_miss"])),
    }

    return eu_totals, hp_totals
