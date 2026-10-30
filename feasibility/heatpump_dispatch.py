"""Stage 3B shared compressor controller and hourly dispatch simulation.

Numerical simulation of an air-to-water heat pump servicing hydronic space heating
(radiators) and Domestic Hot Water (DHW) via a shared modulating compressor and
bounded shared resistance backup heater. Conservative substeps (<=15 min) resolve
intrahour water draws, standing cylinder losses, and thermostat deadband latching.

Order of priority:
1. DHW cylinder reheat takes priority on compressor runtime at leaving water flow
   temperature = active tank target + coil approach temperature.
2. Radiator space heating receives remaining compressor runtime (<=1 hour total compressor
   duty per hour) at weather-compensated flow temperature.
3. Delivered room heat is bounded by BOTH equipment capacity and EN 442 Delta-T 50
   radiator thermal emission limit. Hydronic backup cannot bypass the radiator limit.
4. Shared resistance backup budget (kW*h) is allocated to DHW cylinder first, then space.
5. Outside the data validity envelope, capacity is strictly zero (conservative unresolved-data
   policy); backup is used if available or unmet demand is explicitly reported.
"""

from __future__ import annotations

from functools import lru_cache
import math
from typing import Any, Sequence

import numpy as np

from feasibility.heatpump import (
    HeatPumpMapMetadata,
    HeatPumpPerformanceMap,
    WeatherCompensationCurve,
    calculate_radiator_output,
    create_provisional_fallback_map,
    interpolate_heat_pump_performance,
)
from feasibility.heatpump_tank import (
    DEFAULT_DEADBAND_K,
    DEFAULT_HYGIENE_TEMP_C,
    DEFAULT_INLET_TEMP_C,
    DEFAULT_MIN_SERVICE_TEMP_C,
    DEFAULT_STANDING_LOSS_W_PER_K,
    DEFAULT_TARGET_TEMP_C,
    DEFAULT_VOLUME_L,
    DHWTank,
    WATER_HEAT_CAPACITY_KWH_PER_L_K,
)


def _validate_scalar_float(
    val: Any,
    name: str,
    *,
    min_val: float | None = None,
    max_val: float | None = None,
    strictly_positive: bool = False,
    strictly_less_than_max: bool = False,
) -> float:
    if isinstance(val, bool) or not isinstance(val, (int, float, np.floating, np.integer)):
        raise ValueError(f"{name} must be a real number, got {type(val).__name__}")
    f_val = float(val)
    if not math.isfinite(f_val):
        raise ValueError(f"{name} must be finite, got {f_val}")
    if strictly_positive:
        if f_val <= 0.0:
            raise ValueError(f"{name} must be strictly positive (> 0), got {f_val}")
    elif min_val is not None:
        if f_val < min_val:
            raise ValueError(f"{name} must be >= {min_val}, got {f_val}")
    if max_val is not None:
        if strictly_less_than_max:
            if f_val >= max_val:
                raise ValueError(f"{name} must be < {max_val}, got {f_val}")
        else:
            if f_val > max_val:
                raise ValueError(f"{name} must be <= {max_val}, got {f_val}")
    return f_val


def _validate_1d_float(arr: Sequence[float] | np.ndarray, name: str, n_expected: int | None = None) -> np.ndarray:
    a = np.asarray(arr, dtype=np.float64)
    if a.ndim != 1:
        raise ValueError(f"{name} must be 1D array, got ndim={a.ndim}")
    if not np.all(np.isfinite(a)):
        raise ValueError(f"{name} contains non-finite values (NaN or Inf)")
    if n_expected is not None and len(a) != n_expected:
        raise ValueError(f"{name} length {len(a)} does not match expected length {n_expected}")
    return a


def _validate_1d_bool(arr: Sequence[bool] | np.ndarray, name: str, n_expected: int) -> np.ndarray:
    a = np.asarray(arr, dtype=bool)
    if a.ndim != 1:
        raise ValueError(f"{name} must be 1D boolean array, got ndim={a.ndim}")
    if len(a) != n_expected:
        raise ValueError(f"{name} length {len(a)} does not match expected length {n_expected}")
    return a


def simulate_shared_heat_pump(
    outdoor_temp_c: Sequence[float] | np.ndarray,
    requested_space_heat_kwh: Sequence[float] | np.ndarray,
    draw_heat_kwh: Sequence[float] | np.ndarray,
    space_active_mask: Sequence[bool] | np.ndarray,
    dhw_reheat_allowed_mask: Sequence[bool] | np.ndarray,
    hygiene_mask: Sequence[bool] | np.ndarray,
    *,
    equipment_map: HeatPumpPerformanceMap | None = None,
    flow_curve: WeatherCompensationCurve | None = None,
    rated_radiator_dt50_kw: float = 6.0,
    radiator_exponent: float = 1.3,
    radiator_return_drop_c: float = 5.0,
    indoor_temp_c: float = 18.0,
    tank: DHWTank | None = None,
    tank_volume_litres: float = DEFAULT_VOLUME_L,
    tank_target_temp_c: float = DEFAULT_TARGET_TEMP_C,
    tank_hygiene_temp_c: float = DEFAULT_HYGIENE_TEMP_C,
    tank_inlet_temp_c: float = DEFAULT_INLET_TEMP_C,
    tank_ambient_temp_c: float = 20.0,
    tank_standing_loss_w_per_k: float = DEFAULT_STANDING_LOSS_W_PER_K,
    tank_deadband_k: float = DEFAULT_DEADBAND_K,
    tank_min_service_temp_c: float = DEFAULT_MIN_SERVICE_TEMP_C,
    tank_initial_temp_c: float | None = None,
    coil_approach_delta_c: float = 5.0,
    external_pump_kw: float = 0.06,
    standby_kw: float = 0.02,
    backup_heater_kw: float = 3.0,
    backup_efficiency: float = 1.0,
    min_modulation_fraction: float = 0.25,
    cycling_degradation_penalty: float = 0.10,
    thermal_distribution_loss_fraction: float = 0.0,
    additional_defrost_cop_penalty: float = 0.0,
    force_defrost_adjustment: bool = False,
    additional_unit_auxiliary_kw: float = 0.0,
    force_unit_auxiliary_adjustment: bool = False,
    substeps_per_hour: int = 4,
) -> dict[str, Any]:
    """Simulate shared compressor heat pump servicing space heating and DHW storage.

    Parameters:
        substeps_per_hour: Number of simulation substeps per hour (integer >= 1). Ideally >= 4
            to guarantee <= 15 min intrahour resolution for draw dynamics, standing losses, and
            thermostat deadband latching; values >= 1 are permitted for coarse or fast runs.
    """
    # 1. Array and Scalar Input Validation (strict finite / bounded / non-negative checks, no clamping)
    t_out = _validate_1d_float(outdoor_temp_c, "outdoor_temp_c")
    n_hours = len(t_out)
    q_space_req = _validate_1d_float(requested_space_heat_kwh, "requested_space_heat_kwh", n_hours)
    q_draw_req = _validate_1d_float(draw_heat_kwh, "draw_heat_kwh", n_hours)
    m_space = _validate_1d_bool(space_active_mask, "space_active_mask", n_hours)
    m_reheat = _validate_1d_bool(dhw_reheat_allowed_mask, "dhw_reheat_allowed_mask", n_hours)
    m_hygiene = _validate_1d_bool(hygiene_mask, "hygiene_mask", n_hours)

    if np.any(q_space_req < 0.0) or np.any(q_draw_req < 0.0):
        raise ValueError("Space and draw heat demands must be non-negative")

    rated_radiator_dt50_kw = _validate_scalar_float(rated_radiator_dt50_kw, "rated_radiator_dt50_kw", min_val=0.0)
    radiator_exponent = _validate_scalar_float(radiator_exponent, "radiator_exponent", strictly_positive=True)
    radiator_return_drop_c = _validate_scalar_float(radiator_return_drop_c, "radiator_return_drop_c", strictly_positive=True)
    indoor_temp_c = _validate_scalar_float(indoor_temp_c, "indoor_temp_c")
    coil_approach_delta_c = _validate_scalar_float(coil_approach_delta_c, "coil_approach_delta_c", min_val=0.0)
    external_pump_kw = _validate_scalar_float(external_pump_kw, "external_pump_kw", min_val=0.0)
    standby_kw = _validate_scalar_float(standby_kw, "standby_kw", min_val=0.0)
    backup_heater_kw = _validate_scalar_float(backup_heater_kw, "backup_heater_kw", min_val=0.0)
    backup_efficiency = _validate_scalar_float(backup_efficiency, "backup_efficiency", strictly_positive=True, max_val=1.0)
    min_modulation_fraction = _validate_scalar_float(min_modulation_fraction, "min_modulation_fraction", min_val=0.0, max_val=1.0)
    cycling_degradation_penalty = _validate_scalar_float(cycling_degradation_penalty, "cycling_degradation_penalty", min_val=0.0, max_val=1.0, strictly_less_than_max=True)
    thermal_distribution_loss_fraction = _validate_scalar_float(thermal_distribution_loss_fraction, "thermal_distribution_loss_fraction", min_val=0.0, max_val=1.0, strictly_less_than_max=True)
    additional_defrost_cop_penalty = _validate_scalar_float(additional_defrost_cop_penalty, "additional_defrost_cop_penalty", min_val=0.0, max_val=1.0, strictly_less_than_max=True)
    additional_unit_auxiliary_kw = _validate_scalar_float(additional_unit_auxiliary_kw, "additional_unit_auxiliary_kw", min_val=0.0)

    if isinstance(substeps_per_hour, bool) or not isinstance(substeps_per_hour, (int, np.integer)):
        raise ValueError(f"substeps_per_hour must be an integer, got {type(substeps_per_hour).__name__}")
    n_sub = int(substeps_per_hour)
    if n_sub < 1:
        raise ValueError(f"substeps_per_hour must be >= 1, got {n_sub}")

    if not isinstance(force_defrost_adjustment, (bool, np.bool_)):
        raise ValueError(f"force_defrost_adjustment must be a boolean, got {type(force_defrost_adjustment).__name__}")
    force_defrost_adjustment = bool(force_defrost_adjustment)

    if not isinstance(force_unit_auxiliary_adjustment, (bool, np.bool_)):
        raise ValueError(f"force_unit_auxiliary_adjustment must be a boolean, got {type(force_unit_auxiliary_adjustment).__name__}")
    force_unit_auxiliary_adjustment = bool(force_unit_auxiliary_adjustment)

    tank_volume_litres = _validate_scalar_float(tank_volume_litres, "tank_volume_litres", strictly_positive=True)
    tank_target_temp_c = _validate_scalar_float(tank_target_temp_c, "tank_target_temp_c")
    tank_hygiene_temp_c = _validate_scalar_float(tank_hygiene_temp_c, "tank_hygiene_temp_c")
    tank_inlet_temp_c = _validate_scalar_float(tank_inlet_temp_c, "tank_inlet_temp_c")
    tank_ambient_temp_c = _validate_scalar_float(tank_ambient_temp_c, "tank_ambient_temp_c")
    tank_standing_loss_w_per_k = _validate_scalar_float(tank_standing_loss_w_per_k, "tank_standing_loss_w_per_k", min_val=0.0)
    tank_deadband_k = _validate_scalar_float(tank_deadband_k, "tank_deadband_k", strictly_positive=True)
    tank_min_service_temp_c = _validate_scalar_float(tank_min_service_temp_c, "tank_min_service_temp_c")
    if tank_initial_temp_c is not None:
        tank_initial_temp_c = _validate_scalar_float(tank_initial_temp_c, "tank_initial_temp_c")

    if tank is not None and not isinstance(tank, DHWTank):
        raise ValueError(f"tank must be a DHWTank instance, got {type(tank).__name__}")
    if equipment_map is not None and not isinstance(equipment_map, HeatPumpPerformanceMap):
        raise ValueError(f"equipment_map must be a HeatPumpPerformanceMap instance, got {type(equipment_map).__name__}")
    if flow_curve is not None and not isinstance(flow_curve, WeatherCompensationCurve):
        raise ValueError(f"flow_curve must be a WeatherCompensationCurve instance, got {type(flow_curve).__name__}")

    # 2. Components Setup
    if equipment_map is None:
        equipment_map = create_provisional_fallback_map(nominal_capacity_kw=9.0)
    if flow_curve is None:
        flow_curve = WeatherCompensationCurve()

    if tank is not None:
        t_model = DHWTank(
            volume_litres=tank.volume_litres, target_temp_c=tank.target_temp_c,
            inlet_temp_c=tank.inlet_temp_c, ambient_temp_c=tank.ambient_temp_c,
            standing_loss_w_per_k=tank.standing_loss_w_per_k, deadband_k=tank.deadband_k,
            min_service_temp_c=tank.min_service_temp_c,
            initial_temp_c=tank.temp_c if tank_initial_temp_c is None else tank_initial_temp_c,
            hygiene_temp_c=tank.hygiene_temp_c, thermostat_latch=tank.thermostat_latch,
        )
    else:
        init_t = tank_target_temp_c if tank_initial_temp_c is None else tank_initial_temp_c
        t_model = DHWTank(
            volume_litres=tank_volume_litres, target_temp_c=tank_target_temp_c,
            inlet_temp_c=tank_inlet_temp_c, ambient_temp_c=tank_ambient_temp_c,
            standing_loss_w_per_k=tank_standing_loss_w_per_k, deadband_k=tank_deadband_k,
            min_service_temp_c=tank_min_service_temp_c, initial_temp_c=init_t,
            hygiene_temp_c=tank_hygiene_temp_c,
        )

    # 3. Defrost / Unit Aux flags
    eff_defrost_penalty = 0.0
    if additional_defrost_cop_penalty > 0.0 and (not equipment_map.metadata.includes_defrost or force_defrost_adjustment):
        eff_defrost_penalty = additional_defrost_cop_penalty
    eff_unit_aux_kw = 0.0
    if additional_unit_auxiliary_kw > 0.0 and (not equipment_map.metadata.includes_unit_auxiliaries or force_unit_auxiliary_adjustment):
        eff_unit_aux_kw = additional_unit_auxiliary_kw

    # 4. Hourly Output Buffers
    out_space_comp_elec, out_dhw_comp_elec = np.zeros(n_hours), np.zeros(n_hours)
    out_space_backup_elec, out_dhw_backup_elec = np.zeros(n_hours), np.zeros(n_hours)
    out_ext_pump_elec, out_standby_elec = np.zeros(n_hours), np.zeros(n_hours)
    out_space_del, out_space_unmet = np.zeros(n_hours), np.zeros(n_hours)
    out_draw_del, out_draw_unmet = np.zeros(n_hours), np.zeros(n_hours)
    out_actual_draw_heat = np.zeros(n_hours)
    out_space_hp_heat = np.zeros(n_hours)
    out_dhw_hp_heat = np.zeros(n_hours)
    out_space_back_heat = np.zeros(n_hours)
    out_dhw_back_heat = np.zeros(n_hours)
    out_space_dist_loss = np.zeros(n_hours)
    out_tank_supplied, out_tank_loss, out_tank_temp = np.zeros(n_hours), np.zeros(n_hours), np.zeros(n_hours)
    out_start_storage, out_end_storage = np.zeros(n_hours), np.zeros(n_hours)
    out_dhw_cap, out_dhw_runtime = np.zeros(n_hours), np.zeros(n_hours)
    out_space_cap, out_space_runtime = np.zeros(n_hours), np.zeros(n_hours)
    out_rad_bound, out_space_flow_t, out_dhw_flow_t = np.zeros(n_hours), np.zeros(n_hours), np.zeros(n_hours)
    out_map_outside, out_hygiene_miss = np.zeros(n_hours, dtype=bool), np.zeros(n_hours, dtype=bool)

    c_tank = t_model.heat_capacity_kwh_per_k
    dt_sub = 1.0 / n_sub
    sub_backup_budget = backup_heater_kw * dt_sub
    initial_storage_kwh = float(c_tank * t_model.temp_c)

    # Reuse exact weather/flow lookups only within this equipment simulation.
    @lru_cache(maxsize=2048)
    def _cached_interpolate(ambient: float, water: float):
        return interpolate_heat_pump_performance(equipment_map, ambient, water)

    # 5. Simulation Loop
    for h in range(n_hours):
        t_amb = float(t_out[h])
        hr_space_req, hr_draw_req = float(q_space_req[h]), float(q_draw_req[h])
        hr_space_active, hr_reheat_allowed, hr_hygiene = bool(m_space[h]), bool(m_reheat[h]), bool(m_hygiene[h])

        target_dhw = t_model.hygiene_temp_c if hr_hygiene else t_model.target_temp_c
        t_water_dhw = target_dhw + coil_approach_delta_c
        t_water_space = flow_curve.calculate_flow_temp(t_amb)

        interp_dhw = _cached_interpolate(t_amb, t_water_dhw)
        cop_dhw = interp_dhw.cop * (1.0 - eff_defrost_penalty) if interp_dhw.is_within_envelope else 0.0
        cap_dhw = interp_dhw.capacity_kw if interp_dhw.is_within_envelope else 0.0

        interp_space = _cached_interpolate(t_amb, t_water_space)
        cop_space = interp_space.cop * (1.0 - eff_defrost_penalty) if interp_space.is_within_envelope else 0.0
        cap_space = interp_space.capacity_kw if interp_space.is_within_envelope else 0.0

        q_rad_kw = calculate_radiator_output(
            q_dt50_kw=rated_radiator_dt50_kw, flow_temp_c=t_water_space,
            return_drop_c=radiator_return_drop_c, room_temp_c=indoor_temp_c, n=radiator_exponent,
        )

        out_dhw_cap[h], out_space_cap[h], out_rad_bound[h] = cap_dhw, cap_space, q_rad_kw
        out_space_flow_t[h], out_dhw_flow_t[h] = t_water_space, t_water_dhw
        out_map_outside[h] = (not interp_space.is_within_envelope) or (
            (hr_reheat_allowed or hr_hygiene) and not interp_dhw.is_within_envelope
        )

        hr_start_storage = c_tank * t_model.temp_c
        hr_sub_draw = hr_draw_req / n_sub
        hr_sub_space = (hr_space_req / n_sub) if hr_space_active else 0.0

        tot_dhw_tau = tot_space_tau = 0.0
        tot_dhw_comp_e = tot_space_comp_e = 0.0
        tot_dhw_backup_e = tot_space_backup_e = 0.0
        tot_space_del = tot_draw_serv = tot_draw_act = tot_tank_loss = tot_tank_sup = 0.0
        tot_space_hp_heat = tot_dhw_hp_heat = 0.0
        tot_space_back_heat = tot_dhw_back_heat = 0.0
        tot_space_dist_loss = 0.0
        pump_active_runtime = 0.0

        for _ in range(n_sub):
            t_curr = t_model.temp_c
            # Draw
            q_draw_avail = c_tank * max(0.0, t_curr - t_model.inlet_temp_c)
            q_draw_act = min(hr_sub_draw, q_draw_avail)
            q_serv = min(hr_sub_draw, c_tank * max(0.0, t_curr - t_model.min_service_temp_c)) if t_curr >= t_model.min_service_temp_c else 0.0
            t_after_draw = t_curr - (q_draw_act / c_tank)
            tot_draw_act += q_draw_act
            tot_draw_serv += q_serv

            # Standing loss
            if t_after_draw > t_model.ambient_temp_c:
                p_loss = (t_model.standing_loss_w_per_k / 1000.0) * (t_after_draw - t_model.ambient_temp_c)
                q_loss = min(p_loss * dt_sub, c_tank * (t_after_draw - t_model.ambient_temp_c))
            else:
                q_loss = 0.0
            t_after_loss = t_after_draw - (q_loss / c_tank)
            tot_tank_loss += q_loss

            # Thermostat deadband latch
            latch = t_model.thermostat_latch
            if t_after_loss <= (target_dhw - t_model.deadband_k):
                latch = True
            elif t_after_loss >= target_dhw:
                latch = False

            reheat_active = (hr_reheat_allowed or hr_hygiene) and (latch or hr_hygiene)
            q_dhw_needed = c_tank * max(0.0, target_dhw - t_after_loss) if reheat_active else 0.0

            # 1. DHW Compressor Dispatch
            if q_dhw_needed > 0.0 and cap_dhw > 0.0:
                tau_dhw_sub = min(dt_sub, q_dhw_needed / cap_dhw)
                q_hp_dhw = cap_dhw * tau_dhw_sub
            else:
                tau_dhw_sub, q_hp_dhw = 0.0, 0.0
            tot_dhw_tau += tau_dhw_sub
            tot_dhw_hp_heat += q_hp_dhw
            tot_dhw_comp_e += (q_hp_dhw / cop_dhw) if cop_dhw > 0.0 else 0.0

            # 2. DHW Backup Dispatch
            rem_dhw_need = q_dhw_needed - q_hp_dhw
            if rem_dhw_need > 0.0 and sub_backup_budget > 0.0:
                q_back_dhw = min(rem_dhw_need, sub_backup_budget * backup_efficiency)
                e_back_dhw = q_back_dhw / backup_efficiency
            else:
                q_back_dhw, e_back_dhw = 0.0, 0.0
            sub_backup_rem = max(0.0, sub_backup_budget - e_back_dhw)
            tot_dhw_back_heat += q_back_dhw
            tot_dhw_backup_e += e_back_dhw

            # Update Tank
            q_sup_tank = q_hp_dhw + q_back_dhw
            tot_tank_sup += q_sup_tank
            t_end_sub = t_after_loss + (q_sup_tank / c_tank)
            if t_end_sub <= (target_dhw - t_model.deadband_k):
                t_model.thermostat_latch = True
            elif t_end_sub >= target_dhw:
                t_model.thermostat_latch = False
            else:
                t_model.thermostat_latch = latch
            t_model.temp_c = t_end_sub

            # 3. Space Compressor Dispatch (remaining runtime)
            tau_space_avail = max(0.0, dt_sub - tau_dhw_sub)
            eta_dist = 1.0 - thermal_distribution_loss_fraction
            rad_useful_max = q_rad_kw * dt_sub if q_rad_kw > 0.0 else 0.0
            target_useful_sub = min(hr_sub_space, rad_useful_max) if hr_space_active else 0.0
            q_equip_space_needed = (target_useful_sub / eta_dist) if (target_useful_sub > 0.0 and eta_dist > 0.0) else 0.0

            p_space_lim = min(cap_space, q_rad_kw / eta_dist) if (cap_space > 0.0 and q_rad_kw > 0.0 and eta_dist > 0.0) else 0.0
            if q_equip_space_needed > 0.0 and p_space_lim > 0.0 and tau_space_avail > 0.0:
                tau_space_sub = min(tau_space_avail, q_equip_space_needed / p_space_lim)
                q_hp_space = p_space_lim * tau_space_sub
            else:
                tau_space_sub, q_hp_space = 0.0, 0.0
            tot_space_tau += tau_space_sub
            tot_space_hp_heat += q_hp_space
            tot_space_comp_e += (q_hp_space / cop_space) if cop_space > 0.0 else 0.0

            # 4. Space Backup Dispatch (bounded by shared budget and remaining radiator useful capacity)
            q_hp_useful = q_hp_space * eta_dist
            rem_useful_need = max(0.0, target_useful_sub - q_hp_useful)
            rad_useful_rem = max(0.0, rad_useful_max - q_hp_useful)
            rem_useful_allowed = min(rem_useful_need, rad_useful_rem)

            rem_equip_back_needed = (rem_useful_allowed / eta_dist) if (rem_useful_allowed > 0.0 and eta_dist > 0.0) else 0.0
            backup_space_max = sub_backup_rem * backup_efficiency
            if rem_equip_back_needed > 0.0 and backup_space_max > 0.0:
                q_back_space = min(rem_equip_back_needed, backup_space_max)
                e_back_space = q_back_space / backup_efficiency
            else:
                q_back_space, e_back_space = 0.0, 0.0
            tot_space_back_heat += q_back_space
            tot_space_backup_e += e_back_space

            q_space_equip = q_hp_space + q_back_space
            q_space_del_sub = q_space_equip * eta_dist
            q_space_dist_loss_sub = q_space_equip * thermal_distribution_loss_fraction
            tot_space_del += q_space_del_sub
            tot_space_dist_loss += q_space_dist_loss_sub

            # Pump runtime tracking
            sub_active_tau = tau_dhw_sub + tau_space_sub
            if (q_back_space > 0.0 or q_back_dhw > 0.0) and sub_active_tau < dt_sub:
                sub_active_tau = dt_sub
            pump_active_runtime += sub_active_tau

        # 6. Hourly Aggregations & Part-Load / Cycling Penalty
        tau_hp_tot = min(1.0, tot_dhw_tau + tot_space_tau)
        if 0.0 < tau_hp_tot < min_modulation_fraction and cycling_degradation_penalty > 0.0:
            plr = tau_hp_tot / min_modulation_fraction
            f_cycle = max(0.01, 1.0 - cycling_degradation_penalty * (1.0 - plr))
            tot_space_comp_e /= f_cycle
            tot_dhw_comp_e /= f_cycle

        if eff_unit_aux_kw > 0.0:
            unit_aux_e = eff_unit_aux_kw * tau_hp_tot
            tot_space_comp_e += unit_aux_e * (tot_space_tau / tau_hp_tot if tau_hp_tot > 0.0 else 0.0)
            tot_dhw_comp_e += unit_aux_e * (tot_dhw_tau / tau_hp_tot if tau_hp_tot > 0.0 else 0.0)

        ext_pump_e = external_pump_kw * min(1.0, pump_active_runtime)
        standby_e = standby_kw * 1.0

        out_space_comp_elec[h], out_dhw_comp_elec[h] = tot_space_comp_e, tot_dhw_comp_e
        out_space_backup_elec[h], out_dhw_backup_elec[h] = tot_space_backup_e, tot_dhw_backup_e
        out_ext_pump_elec[h], out_standby_elec[h] = ext_pump_e, standby_e
        out_space_del[h] = tot_space_del
        out_space_unmet[h] = max(0.0, hr_space_req - tot_space_del)
        out_draw_del[h] = tot_draw_serv
        out_draw_unmet[h] = max(0.0, hr_draw_req - tot_draw_serv)
        out_actual_draw_heat[h] = tot_draw_act
        out_space_hp_heat[h] = tot_space_hp_heat
        out_dhw_hp_heat[h] = tot_dhw_hp_heat
        out_space_back_heat[h] = tot_space_back_heat
        out_dhw_back_heat[h] = tot_dhw_back_heat
        out_space_dist_loss[h] = tot_space_dist_loss
        out_tank_supplied[h], out_tank_loss[h], out_tank_temp[h] = tot_tank_sup, tot_tank_loss, t_model.temp_c
        out_start_storage[h], out_end_storage[h] = hr_start_storage, c_tank * t_model.temp_c
        out_dhw_runtime[h], out_space_runtime[h] = tot_dhw_tau, tot_space_tau
        out_hygiene_miss[h] = bool(hr_hygiene and (t_model.temp_c < (t_model.hygiene_temp_c - 0.05)))

    out_total_elec = out_space_comp_elec + out_dhw_comp_elec + out_space_backup_elec + out_dhw_backup_elec + out_ext_pump_elec + out_standby_elec
    final_storage_kwh = float(out_end_storage[-1]) if n_hours > 0 else initial_storage_kwh
    storage_delta_kwh = final_storage_kwh - initial_storage_kwh

    # 7. Energy Conservation and Summary Metrics
    tank_balance_errors = np.abs((out_end_storage - out_start_storage) - (out_tank_supplied - out_actual_draw_heat - out_tank_loss))
    max_tank_err = float(np.max(tank_balance_errors)) if n_hours > 0 else 0.0

    sum_space_del, sum_space_req = float(np.sum(out_space_del)), float(np.sum(q_space_req))
    sum_space_unmet = float(np.sum(out_space_unmet))
    sum_draw_del, sum_draw_req = float(np.sum(out_draw_del)), float(np.sum(q_draw_req))
    sum_draw_unmet = float(np.sum(out_draw_unmet))
    sum_actual_draw = float(np.sum(out_actual_draw_heat))
    sum_unserved_draw = float(np.sum(out_actual_draw_heat - out_draw_del))
    sum_tank_sup, sum_tank_loss = float(np.sum(out_tank_supplied)), float(np.sum(out_tank_loss))

    sum_space_comp_e, sum_dhw_comp_e = float(np.sum(out_space_comp_elec)), float(np.sum(out_dhw_comp_elec))
    sum_space_back_e, sum_dhw_back_e = float(np.sum(out_space_backup_elec)), float(np.sum(out_dhw_backup_elec))
    sum_pump_e, sum_standby_e = float(np.sum(out_ext_pump_elec)), float(np.sum(out_standby_elec))
    sum_aux_e = sum_pump_e + sum_standby_e
    sum_total_e = float(np.sum(out_total_elec))

    comp_heat_space = float(np.sum(out_space_hp_heat))
    comp_heat_dhw = float(np.sum(out_dhw_hp_heat))
    backup_heat_space = float(np.sum(out_space_back_heat))
    backup_heat_dhw = float(np.sum(out_dhw_back_heat))
    dist_loss_space = float(np.sum(out_space_dist_loss))

    seasonal_space_cop = max(0.0, comp_heat_space / sum_space_comp_e) if sum_space_comp_e > 0.0 else 0.0
    seasonal_dhw_cop_tank = max(0.0, comp_heat_dhw / sum_dhw_comp_e) if sum_dhw_comp_e > 0.0 else 0.0
    seasonal_comp_cop = max(0.0, (comp_heat_space + comp_heat_dhw) / (sum_space_comp_e + sum_dhw_comp_e)) if (sum_space_comp_e + sum_dhw_comp_e) > 0.0 else 0.0

    qualified_delivered_heat_kwh = sum_space_del + sum_draw_del
    seasonal_spf_delivered = max(0.0, qualified_delivered_heat_kwh / sum_total_e) if sum_total_e > 0.0 else 0.0
    seasonal_spf_tank = max(0.0, (sum_space_del + sum_tank_sup) / sum_total_e) if sum_total_e > 0.0 else 0.0
    seasonal_scop_space = max(0.0, sum_space_del / (sum_space_comp_e + sum_space_back_e)) if (sum_space_comp_e + sum_space_back_e) > 0.0 else 0.0

    storage_adjusted_heat_kwh = qualified_delivered_heat_kwh + storage_delta_kwh
    if sum_total_e <= 0.0:
        seasonal_spf_storage_adjusted = None
        storage_adjusted_note = "Zero electricity input in evaluation window."
    elif storage_adjusted_heat_kwh < 0.0:
        seasonal_spf_storage_adjusted = None
        storage_adjusted_note = "Net storage drawdown exceeds delivered heat in evaluation window; storage-adjusted numerator is negative."
    else:
        seasonal_spf_storage_adjusted = max(0.0, storage_adjusted_heat_kwh / sum_total_e)
        storage_adjusted_note = None

    net_dhw_service_kwh = max(0.0, sum_draw_del + storage_delta_kwh)
    seasonal_scop_dhw_net = max(0.0, net_dhw_service_kwh / (sum_dhw_comp_e + sum_dhw_back_e)) if (sum_dhw_comp_e + sum_dhw_back_e) > 0.0 else 0.0
    seasonal_scop_dhw_delivered = max(0.0, sum_draw_del / (sum_dhw_comp_e + sum_dhw_back_e)) if (sum_dhw_comp_e + sum_dhw_back_e) > 0.0 else 0.0

    summary = {
        "annual_requested_space_heat_kwh": sum_space_req, "annual_delivered_space_heat_kwh": sum_space_del,
        "annual_unmet_space_heat_kwh": sum_space_unmet, "annual_requested_draw_heat_kwh": sum_draw_req,
        "annual_delivered_draw_heat_kwh": sum_draw_del, "annual_unmet_draw_heat_kwh": sum_draw_unmet,
        "annual_actual_draw_heat_kwh": sum_actual_draw, "annual_unserved_draw_heat_kwh": sum_unserved_draw,
        "annual_heat_supplied_tank_kwh": sum_tank_sup, "annual_tank_losses_kwh": sum_tank_loss,
        "annual_space_compressor_heat_kwh": comp_heat_space, "annual_dhw_compressor_heat_kwh": comp_heat_dhw,
        "annual_space_backup_heat_kwh": backup_heat_space, "annual_dhw_backup_heat_kwh": backup_heat_dhw,
        "annual_space_distribution_loss_kwh": dist_loss_space,
        "initial_storage_kwh": initial_storage_kwh, "final_storage_kwh": final_storage_kwh,
        "storage_delta_kwh": storage_delta_kwh, "max_tank_conservation_error_kwh": max_tank_err,
        "annual_space_compressor_electricity_kwh": sum_space_comp_e, "annual_dhw_compressor_electricity_kwh": sum_dhw_comp_e,
        "annual_space_backup_electricity_kwh": sum_space_back_e, "annual_dhw_backup_electricity_kwh": sum_dhw_back_e,
        "annual_external_pump_electricity_kwh": sum_pump_e, "annual_standby_electricity_kwh": sum_standby_e,
        "annual_auxiliary_electricity_kwh": sum_aux_e, "annual_total_electricity_kwh": sum_total_e,
        "seasonal_space_cop": seasonal_space_cop, "seasonal_dhw_cop_heat_to_tank": seasonal_dhw_cop_tank,
        "seasonal_compressor_cop": seasonal_comp_cop,
        "seasonal_performance_factor_delivered_heat": seasonal_spf_delivered,
        "seasonal_performance_factor_storage_adjusted": seasonal_spf_storage_adjusted,
        "storage_adjusted_ratio_note": storage_adjusted_note,
        "storage_adjusted_heat_kwh": storage_adjusted_heat_kwh,
        "seasonal_performance_factor_heat_to_tank": seasonal_spf_tank,
        "seasonal_scop_space": seasonal_scop_space,
        "seasonal_scop_dhw_net": seasonal_scop_dhw_net,
        "seasonal_scop_dhw_delivered": seasonal_scop_dhw_delivered,
        "total_dhw_compressor_hours": float(np.sum(out_dhw_runtime)),
        "total_space_compressor_hours": float(np.sum(out_space_runtime)),
        "max_hourly_compressor_runtime_hours": float(np.max(out_dhw_runtime + out_space_runtime)) if n_hours > 0 else 0.0,
        "hygiene_target_miss_count": int(np.sum(out_hygiene_miss)), "map_outside_count": int(np.sum(out_map_outside)),
    }

    metadata = {
        "equipment_model": equipment_map.metadata.model, "equipment_source": equipment_map.metadata.source,
        "provisional_radiator_dt50_kw": rated_radiator_dt50_kw, "radiator_exponent": radiator_exponent,
        "radiator_return_drop_c": radiator_return_drop_c, "indoor_temp_c": indoor_temp_c,
        "radiator_rating_source": "Caller explicitly supplied provisional radiator rating; installed emitter sizing and dynamic building UA remain unverified.",
        "site_operating_context": "User operating report of ~60°C boiler flow roughly maintaining ~18°C indoor temperature provides insufficient data to identify radiator capacity or dwelling heat loss.",
        "coil_approach_delta_c": coil_approach_delta_c, "substeps_per_hour": substeps_per_hour,
        "min_modulation_fraction": min_modulation_fraction, "cycling_degradation_penalty": cycling_degradation_penalty,
        "backup_heater_kw": backup_heater_kw, "external_pump_kw": external_pump_kw, "standby_kw": standby_kw,
        "backup_efficiency": backup_efficiency,
        "thermal_distribution_loss_fraction": thermal_distribution_loss_fraction,
        "additional_defrost_cop_penalty": additional_defrost_cop_penalty,
        "force_defrost_adjustment": force_defrost_adjustment,
        "additional_unit_auxiliary_kw": additional_unit_auxiliary_kw,
        "force_unit_auxiliary_adjustment": force_unit_auxiliary_adjustment,
        "map_includes_defrost": equipment_map.metadata.includes_defrost,
        "map_includes_unit_auxiliaries": equipment_map.metadata.includes_unit_auxiliaries,
        "ratio_definitions": {
            "seasonal_performance_factor_delivered_heat": "Ratio of useful qualified delivered heat (space heating + DHW draw service) to total electricity input (compressor + resistance backup + auxiliary pump and standby). Excludes unserved lukewarm water withdrawals and does not add tank storage delta into delivered service.",
            "seasonal_performance_factor_storage_adjusted": "Ratio of useful delivered heat plus net change in DHW tank thermal storage to total electricity input. Discloses initial vs final tank storage energy; null if numerator is negative.",
            "seasonal_performance_factor_heat_to_tank": "Ratio of space delivered heat plus thermal heat delivered by compressor and backup to the DHW cylinder to total electricity input.",
            "seasonal_scop_space": "Ratio of useful delivered space heating to space heating electricity (compressor + hydronic resistance backup; excludes auxiliary pump and standby).",
            "seasonal_scop_dhw_net": "Ratio of net DHW service (delivered draw heat + storage delta) to DHW electricity (compressor + DHW resistance backup). Excludes unserved lukewarm water.",
            "seasonal_space_cop": "Ratio of space compressor thermal output at equipment boundary to space compressor electricity.",
            "seasonal_dhw_cop_heat_to_tank": "Ratio of DHW compressor thermal output to DHW compressor electricity.",
            "seasonal_compressor_cop": "Ratio of total compressor thermal output to total compressor electricity.",
        },
    }

    return {
        "space_compressor_electricity_kwh": out_space_comp_elec, "dhw_compressor_electricity_kwh": out_dhw_comp_elec,
        "space_backup_electricity_kwh": out_space_backup_elec, "dhw_backup_electricity_kwh": out_dhw_backup_elec,
        "external_pump_electricity_kwh": out_ext_pump_elec, "standby_electricity_kwh": out_standby_elec,
        "auxiliary_electricity_kwh": out_ext_pump_elec + out_standby_elec, "total_electricity_kwh": out_total_elec,
        "requested_space_heat_kwh": q_space_req, "delivered_space_heat_kwh": out_space_del, "unmet_space_heat_kwh": out_space_unmet,
        "space_delivered_heat_kwh": out_space_del, "space_unmet_heat_kwh": out_space_unmet,
        "requested_draw_heat_kwh": q_draw_req, "delivered_draw_heat_kwh": out_draw_del, "unmet_draw_heat_kwh": out_draw_unmet,
        "draw_delivered_heat_kwh": out_draw_del, "draw_unmet_heat_kwh": out_draw_unmet,
        "actual_draw_heat_kwh": out_actual_draw_heat, "draw_actual_heat_kwh": out_actual_draw_heat,
        "space_compressor_heat_kwh": out_space_hp_heat, "dhw_compressor_heat_kwh": out_dhw_hp_heat,
        "space_backup_heat_kwh": out_space_back_heat, "dhw_backup_heat_kwh": out_dhw_back_heat,
        "space_distribution_loss_kwh": out_space_dist_loss,
        "heat_supplied_tank_kwh": out_tank_supplied, "tank_losses_kwh": out_tank_loss, "tank_temp_c": out_tank_temp,
        "tank_start_storage_kwh": out_start_storage, "tank_end_storage_kwh": out_end_storage,
        "dhw_capacity_kw": out_dhw_cap, "dhw_runtime_hours": out_dhw_runtime,
        "space_capacity_kw": out_space_cap, "room_capacity_kw": out_space_cap,
        "space_runtime_hours": out_space_runtime, "room_runtime_hours": out_space_runtime,
        "radiator_bound_kw": out_rad_bound, "space_flow_temp_c": out_space_flow_t, "dhw_flow_temp_c": out_dhw_flow_t,
        "map_outside_flag": out_map_outside, "hygiene_target_miss": out_hygiene_miss,
        "summary": summary, "metadata": metadata,
    }
