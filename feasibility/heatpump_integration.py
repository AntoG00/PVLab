"""Stage 4B heat pump system integration adapter and cached source/forecast simulation.

Wires user specifications and equipment performance maps into the hourly shared heat pump dispatch,
connecting gas allocation, space heating demand, hot water cylinder draws, and controller logic.
Provides cached source series for load profiles and recomputed hourly series for forecast calendars.
"""
from __future__ import annotations
from feasibility.runtime_context import current_runtime, assert_runtime_key
from feasibility.legacy_profile import legacy_text

import calendar
import datetime as dt
from functools import lru_cache
import math
from typing import Any, Sequence, Tuple
from zoneinfo import ZoneInfo

import numpy as np

from feasibility.heatpump import (
    HeatPumpMapMetadata,
    HeatPumpPerformanceMap,
    WeatherCompensationCurve,
    create_provisional_fallback_map,
)
from feasibility.heatpump_settings import (
    HP_PHYSICAL_KEYS,
    parse_manufacturer_map_json,
    _window_hours,
)
from feasibility.heatpump_demand import (
    GasAllocationResult,
    HeatPumpDemandResult,
    allocate_gas_consumption,
    calculate_heat_demand,
    compute_reference_degree_hours,
)
from feasibility.heatpump_tank import (
    build_hourly_dhw_demand,
)
from feasibility.heatpump_dispatch import (
    simulate_shared_heat_pump,
)


# Physical parameter keys determining the source heat pump simulation (independent of PV, battery, and financing).
HP_SOURCE_CACHE_KEYS: tuple[str, ...] = tuple(
    [
        'timezone_mode',
        'load_scenario',
        'planning_heat_pump',
        'summer_gas_base',
        'cooking_smc_annual',
        'gas_pcs',
        'boiler_efficiency',
        'heating_balance_temp',
    ]
    + [k for k in HP_PHYSICAL_KEYS if k != 'hp_electric_cooking']
)


def envelope(values: Sequence[float] | np.ndarray) -> dict[str, float | None]:
    """Summary statistics (min, median, mean, max, p10, p90) matching engine convention."""
    a = np.asarray(values, dtype=float)
    a = a[np.isfinite(a)]
    if not len(a):
        return dict(min=None, median=None, mean=None, max=None, p10=None, p90=None)
    return {
        'min': float(a.min()),
        'median': float(np.median(a)),
        'mean': float(a.mean()),
        'max': float(a.max()),
        'p10': float(np.percentile(a, 10)),
        'p90': float(np.percentile(a, 90)),
    }


@lru_cache(maxsize=16)
def get_source_reference_degree_hours(runtime_key, balance_temp: float) -> np.ndarray:
    """Compute baseline mean all-hours degree-hours per month (1..12) across all 19 historical weather years (2005..2023).

    Serves as the explicit fixed reference denominator for weather-sensitive scaling in both source and forecast simulations.
    """
    assert_runtime_key(runtime_key)
    from feasibility import engine
    d = engine.data()
    return compute_reference_degree_hours(
        temperature=d['temperature'],
        valid=d['valid'],
        months=d['month'],
        balance_temp=float(balance_temp),
    )


def resolve_equipment_map(settings: dict[str, Any]) -> HeatPumpPerformanceMap:
    """Resolve HeatPumpPerformanceMap from settings.

    If hp_manufacturer_map_json is supplied and non-empty, deserializes the JSON
    and uses the map capacity as-is without silent nominal capacity scaling.
    If hp_manufacturer_map_json is empty or None, creates the provisional fallback map
    scaled by hp_nominal_kw.
    """
    raw_json = settings.get('hp_manufacturer_map_json', '')
    if raw_json and isinstance(raw_json, str) and raw_json.strip():
        parsed = parse_manufacturer_map_json(raw_json)
        if parsed is not None:
            return parsed
    nominal_kw = float(settings.get('hp_nominal_kw', 9.0))
    return create_provisional_fallback_map(nominal_capacity_kw=nominal_kw)


def resolve_weather_compensation_curve(settings: dict[str, Any]) -> WeatherCompensationCurve:
    """Construct WeatherCompensationCurve from settings."""
    return WeatherCompensationCurve(
        outdoor_design_c=float(settings.get('hp_curve_outdoor_cold', -5.0)),
        flow_max_c=float(settings.get('hp_curve_flow_cold', 55.0)),
        outdoor_mild_c=float(settings.get('hp_curve_outdoor_mild', 15.0)),
        flow_min_c=float(settings.get('hp_curve_flow_mild', 35.0)),
    )


def build_dhw_reheat_mask(
    n_hours: int,
    settings: dict[str, Any],
    clock_hours: np.ndarray,
) -> np.ndarray:
    """Build boolean mask for DHW storage reheat availability."""
    mode = settings.get('hp_reheat_mode', 'continuous')
    if mode == 'continuous':
        return np.ones(n_hours, dtype=bool)
    on_h = int(settings.get('hp_reheat_on', 11))
    off_h = int(settings.get('hp_reheat_off', 16))
    win = _window_hours(on_h, off_h)
    return np.isin(clock_hours, list(win))


def build_source_hygiene_mask(
    wi: int,
    n_valid: int,
    settings: dict[str, Any],
    civil_hours: np.ndarray,
    std_hours: np.ndarray,
) -> np.ndarray:
    """Derive weekly hygiene mask for historical weather year wi.

    Derives true dates and weekdays from source timeline.
    Converts fixed UTC+1 source timestamps to Europe/Rome for civil mode to avoid
    mismatched weekday near 00:00 and properly handle DST.
    Supports duration wrapping past midnight and week boundaries.
    """
    if not settings.get('hp_hygiene_enabled', False):
        return np.zeros(n_valid, dtype=bool)

    from feasibility import engine
    timeline = engine._local_standard_dates()[wi][:n_valid]
    w_start = int(settings.get('hp_hygiene_weekday', 0))
    h_start = int(settings.get('hp_hygiene_hour', 12))
    duration = int(settings.get('hp_hygiene_duration_hours', 1))
    tz_mode = settings.get('timezone_mode', 'standard')

    start_offset = (w_start * 24 + h_start) % 168
    rome = ZoneInfo('Europe/Rome')
    utc1 = dt.timezone(dt.timedelta(hours=1))
    mask = np.zeros(n_valid, dtype=bool)
    for i in range(n_valid):
        stamp, std_h, _ = timeline[i]
        dt_utc1 = dt.datetime.fromisoformat(stamp).replace(hour=int(std_h), tzinfo=utc1)
        if tz_mode == 'civil':
            c_cur = dt_utc1.astimezone(rome)
            w = c_cur.weekday()
            clk = c_cur.hour
        else:
            w = dt_utc1.weekday()
            clk = int(std_h)
        delta = ((w * 24 + clk) - start_offset) % 168
        if 0 <= delta < duration:
            mask[i] = True
    return mask


def build_forecast_hygiene_mask(
    forecast_year: int,
    target_len: int,
    settings: dict[str, Any],
    target_civil_hours: np.ndarray,
    target_std_hours: np.ndarray,
) -> np.ndarray:
    """Derive weekly hygiene mask for the forecast_year calendar.

    Derives true dates and weekdays on the target calendar.
    Supports duration wrapping past midnight and week boundaries.
    """
    if not settings.get('hp_hygiene_enabled', False):
        return np.zeros(target_len, dtype=bool)

    w_start = int(settings.get('hp_hygiene_weekday', 0))
    h_start = int(settings.get('hp_hygiene_hour', 12))
    duration = int(settings.get('hp_hygiene_duration_hours', 1))
    tz_mode = settings.get('timezone_mode', 'standard')

    start_offset = (w_start * 24 + h_start) % 168
    start_dt = dt.datetime(forecast_year, 1, 1, 0, 0, tzinfo=dt.timezone(dt.timedelta(hours=1)))
    rome = ZoneInfo('Europe/Rome')
    one_h = dt.timedelta(hours=1)

    mask = np.zeros(target_len, dtype=bool)
    cur = start_dt
    for i in range(target_len):
        if tz_mode == 'civil':
            c_cur = cur.astimezone(rome)
            w = c_cur.weekday()
            clk = c_cur.hour
        else:
            w = cur.weekday()
            clk = int(target_std_hours[i])
        delta = ((w * 24 + clk) - start_offset) % 168
        if 0 <= delta < duration:
            mask[i] = True
        cur += one_h
    return mask


def calculate_cooking_electricity(
    settings: dict[str, Any],
    gas_alloc: GasAllocationResult,
    valid: np.ndarray,
    month: np.ndarray,
    hour: np.ndarray,
) -> tuple[np.ndarray, float, float]:
    """Calculate induction cooking electrical hourly array and Smc/kWh totals.

    Returns:
        (cooking_elec_array, cooking_smc, cooking_kwh)
        If hp_electric_cooking is True:
          cooking_smc = 0.0
          cooking_kwh = sum(monthly_cook)
          cooking_elec_array = allocated induction profile
        If hp_electric_cooking is False:
          cooking_smc = sum(gas_alloc.cooking_smc)
          cooking_kwh = 0.0
          cooking_elec_array = zeros
    """
    from feasibility.engine import _normalized_pattern
    from . import engine
    if engine._thermal_reference() is not None:
        ref = engine._thermal_reference()
        use = ref.end_uses.get('cooking')
        applicable_smc = (float(sum(use.monthly_input)) if use is not None and use.carrier == 'gas'
                          and use.monthly_input is not None else None)
        if engine._thermal_action(settings, 'cooking') == 'unchanged':
            return np.zeros_like(valid, dtype=float), applicable_smc, 0.
        monthly_cook = engine._thermal_useful(settings, 'cooking') / settings['induction_efficiency']
        cooking_elec = np.zeros_like(valid, dtype=float)
        for mm in range(1,13):
            cook_hours = (((hour >= 12) & (hour <= 13)) | ((hour >= 19) & (hour <= 20))).astype(float)
            cooking_elec += _normalized_pattern(cook_hours, monthly_cook[mm-1], valid & (month == mm))
        return cooking_elec, applicable_smc, float(monthly_cook.sum())
    cooking_smc_arr = gas_alloc.cooking_smc
    cooking_smc_total = float(cooking_smc_arr.sum())

    if not settings.get('hp_electric_cooking', False):
        return np.zeros_like(valid, dtype=float), cooking_smc_total, 0.0

    pcs = float(settings.get('gas_pcs', 11.0))
    g_eff = float(settings.get('gas_cooking_efficiency', 0.45))
    i_eff = float(settings.get('induction_efficiency', 0.85))
    monthly_cook = cooking_smc_arr * pcs * g_eff / i_eff

    cooking_elec = np.zeros_like(valid, dtype=float)
    for mm in range(1, 13):
        use = valid & (month == mm)
        cook_hours = ((hour >= 12) & (hour <= 13) | (hour >= 19) & (hour <= 20)).astype(float)
        cooking_elec += _normalized_pattern(cook_hours, monthly_cook[mm - 1], use)

    return cooking_elec, 0.0, float(monthly_cook.sum())


def _thermal_demand_arguments(settings):
    from . import engine
    ref = engine._thermal_reference()
    if ref is None:
        return {'monthly_gas': current_runtime().thermal_reference['GAS_MONTHLY']}
    return {'monthly_heat_budgets': engine._thermal_useful(settings, 'space_heat', changed_only=True)}


def _thermal_dhw_budget(settings, demand):
    from . import engine
    if engine._thermal_reference() is None:
        return demand.gas_allocation.dhw_gross_useful_kwh
    return engine._thermal_useful(settings, 'dhw', changed_only=True)


def _simulate_source_ensemble(settings: dict[str, Any]) -> dict[str, Any]:
    """Execute complete 19-year historical simulation of the shared heat pump."""
    from feasibility import engine
    d = engine.data()
    years = list(map(int, d['years']))
    n_years = len(years)
    total_slots = 8784

    temperature = d['temperature']
    valid = d['valid']
    month = d['month']
    hour = d['hour']
    civil_hours = engine._get_civil_hours(d)
    tz_mode = settings.get('timezone_mode', 'standard')

    ref_degree_hours = get_source_reference_degree_hours(current_runtime().key, settings.get('heating_balance_temp', 18.0))
    equipment_map = resolve_equipment_map(settings)
    flow_curve = resolve_weather_compensation_curve(settings)

    # Output arrays shaped (19, 8784), zero-padded at invalid trailing slots
    arr_keys = [
        'total_electricity_kwh',
        'space_compressor_electricity_kwh',
        'dhw_compressor_electricity_kwh',
        'space_backup_electricity_kwh',
        'dhw_backup_electricity_kwh',
        'external_pump_electricity_kwh',
        'standby_electricity_kwh',
        'auxiliary_electricity_kwh',
        'requested_space_heat_kwh',
        'delivered_space_heat_kwh',
        'unmet_space_heat_kwh',
        'space_delivered_heat_kwh',
        'space_unmet_heat_kwh',
        'requested_draw_heat_kwh',
        'delivered_draw_heat_kwh',
        'unmet_draw_heat_kwh',
        'actual_draw_heat_kwh',
        'unserved_draw_heat_kwh',
        'draw_delivered_heat_kwh',
        'draw_unmet_heat_kwh',
        'space_compressor_heat_kwh',
        'dhw_compressor_heat_kwh',
        'space_backup_heat_kwh',
        'dhw_backup_heat_kwh',
        'space_distribution_loss_kwh',
        'heat_supplied_tank_kwh',
        'tank_losses_kwh',
        'tank_temp_c',
        'tank_start_storage_kwh',
        'tank_end_storage_kwh',
        'space_flow_temp_c',
        'dhw_flow_temp_c',
        'space_capacity_kw',
        'dhw_capacity_kw',
        'space_runtime_hours',
        'dhw_runtime_hours',
        'radiator_bound_kw',
        'map_outside_flag',
        'hygiene_target_miss',
    ]
    bool_keys = {'map_outside_flag', 'hygiene_target_miss'}
    arrays = {
        k: (np.zeros((n_years, total_slots), dtype=bool) if k in bool_keys else np.zeros((n_years, total_slots), dtype=float))
        for k in arr_keys
    }

    by_weather: list[dict[str, Any]] = []
    gas_allocation_out: GasAllocationResult | None = None

    for wi in range(n_years):
        n_valid = int(valid[wi].sum())
        t_val = temperature[wi, :n_valid]
        v_val = valid[wi, :n_valid]
        m_val = month[wi, :n_valid]
        h_val = hour[wi, :n_valid]
        civ_val = civil_hours[wi, :n_valid]
        clock_hours = civ_val if tz_mode == 'civil' else h_val

        # 1. Useful space heating demand calculation
        demand_res = calculate_heat_demand(
            temperature=t_val,
            valid=v_val,
            months=m_val,
            hours=h_val,
            civil_hours=civ_val,
            source_years=[years[wi]],
            **_thermal_demand_arguments(settings),
            summer_gas_base=float(settings.get('summer_gas_base', 20.0)),
            cooking_smc_annual=float(settings.get('cooking_smc_annual', 20.0)),
            gas_pcs=float(settings.get('gas_pcs', 11.0)),
            boiler_efficiency=float(settings.get('boiler_efficiency', 0.80)),
            heating_balance_temp=float(settings.get('heating_balance_temp', 18.0)),
            indoor_temp=float(settings.get('hp_indoor_temp', 18.0)),
            setback_delta=float(settings.get('hp_setback_delta', 0.0)),
            schedule_mode=settings.get('hp_schedule_mode', 'continuous'),
            timezone_mode=tz_mode,
            morning_window=(int(settings.get('hp_morning_on', 6)), int(settings.get('hp_morning_off', 8))),
            evening_window=(int(settings.get('hp_evening_on', 16)), int(settings.get('hp_evening_off', 20))),
            weather_sensitive=bool(settings.get('hp_weather_sensitive', False)),
            reference_degree_hours=ref_degree_hours,
        )
        if gas_allocation_out is None:
            gas_allocation_out = demand_res.gas_allocation

        # 2. Domestic hot water demand calculation
        dhw_mode = settings.get('hp_dhw_demand_mode', 'gas_calibrated')
        if dhw_mode == 'gas_calibrated' or engine._thermal_reference() is not None:
            q_draw = build_hourly_dhw_demand(
                valid=v_val,
                months=m_val,
                hours=clock_hours,
                monthly_dhw_kwh=_thermal_dhw_budget(settings, demand_res),
                prior_loss_fraction=float(settings.get('hp_prior_dhw_loss_fraction', 0.0)),
                morning_window=(int(settings.get('hp_draw_morning_on', 7)), int(settings.get('hp_draw_morning_off', 9))),
                evening_window=(int(settings.get('hp_draw_evening_on', 18)), int(settings.get('hp_draw_evening_off', 21))),
                morning_fraction=float(settings.get('hp_draw_morning_fraction', 0.5)),
                evening_fraction=1.0 - float(settings.get('hp_draw_morning_fraction', 0.5)),
            )
        else:
            q_draw = build_hourly_dhw_demand(
                valid=v_val,
                months=m_val,
                hours=clock_hours,
                litres_per_day=float(settings.get('hp_dhw_litres_day', 150.0)),
                service_temp_c=float(settings.get('hp_tank_service_temp', 40.0)),
                inlet_temp_c=float(settings.get('hp_tank_inlet_temp', 15.0)),
                prior_loss_fraction=0.0,
                morning_window=(int(settings.get('hp_draw_morning_on', 7)), int(settings.get('hp_draw_morning_off', 9))),
                evening_window=(int(settings.get('hp_draw_evening_on', 18)), int(settings.get('hp_draw_evening_off', 21))),
                morning_fraction=float(settings.get('hp_draw_morning_fraction', 0.5)),
                evening_fraction=1.0 - float(settings.get('hp_draw_morning_fraction', 0.5)),
            )

        # 3. Masks
        reheat_mask = build_dhw_reheat_mask(n_valid, settings, clock_hours)
        hygiene_mask = build_source_hygiene_mask(wi, n_valid, settings, civ_val, h_val)

        # 4. Tank hygiene and target temperatures:
        # When hygiene is disabled, controller's unused hygiene ceiling is max(actual_target, configured_hygiene)
        # to satisfy DHWTank while preserving saved value; when enabled, reject hygiene < target.
        t_target = float(settings.get('hp_tank_target_temp', 50.0))
        t_hygiene_conf = float(settings.get('hp_hygiene_temp', 60.0))
        hygiene_enabled = bool(settings.get('hp_hygiene_enabled', False))
        if hygiene_enabled and t_hygiene_conf < t_target:
            raise ValueError(
                f"hp_hygiene_temp ({t_hygiene_conf}) cannot be lower than "
                f"hp_tank_target_temp ({t_target}) when hygiene is enabled"
            )
        t_hygiene_eff = t_hygiene_conf if hygiene_enabled else max(t_target, t_hygiene_conf)

        # 5. Shared heat pump dispatch simulation
        sim = simulate_shared_heat_pump(
            outdoor_temp_c=t_val,
            requested_space_heat_kwh=demand_res.heat_demand_kwh,
            draw_heat_kwh=q_draw,
            space_active_mask=demand_res.active_mask,
            dhw_reheat_allowed_mask=reheat_mask,
            hygiene_mask=hygiene_mask,
            equipment_map=equipment_map,
            flow_curve=flow_curve,
            rated_radiator_dt50_kw=float(settings.get('hp_radiator_dt50_kw', 10.0)),
            radiator_exponent=float(settings.get('hp_radiator_exponent', 1.3)),
            radiator_return_drop_c=float(settings.get('hp_return_drop', 5.0)),
            indoor_temp_c=float(settings.get('hp_indoor_temp', 18.0)),
            tank_volume_litres=float(settings.get('hp_tank_litres', 200.0)),
            tank_target_temp_c=t_target,
            tank_hygiene_temp_c=t_hygiene_eff,
            tank_inlet_temp_c=float(settings.get('hp_tank_inlet_temp', 15.0)),
            tank_ambient_temp_c=float(settings.get('hp_tank_ambient_temp', 20.0)),
            tank_standing_loss_w_per_k=float(settings.get('hp_tank_loss_w_k', 1.0)),
            tank_deadband_k=float(settings.get('hp_tank_deadband', 5.0)),
            tank_min_service_temp_c=float(settings.get('hp_tank_service_temp', 40.0)),
            tank_initial_temp_c=float(settings.get('hp_tank_initial_temp', 50.0)),
            coil_approach_delta_c=float(settings.get('hp_coil_approach', 5.0)),
            external_pump_kw=float(settings.get('hp_external_pump_kw', 0.06)),
            standby_kw=float(settings.get('hp_standby_kw', 0.02)),
            backup_heater_kw=float(settings.get('hp_backup_kw', 3.0)),
            backup_efficiency=float(settings.get('hp_backup_efficiency', 1.0)),
            min_modulation_fraction=float(settings.get('hp_min_modulation_fraction', 0.25)),
            cycling_degradation_penalty=float(settings.get('hp_cycling_penalty', 0.10)),
            thermal_distribution_loss_fraction=float(settings.get('hp_distribution_loss_fraction', 0.0)),
            additional_defrost_cop_penalty=float(settings.get('hp_additional_defrost_penalty', 0.0)),
            additional_unit_auxiliary_kw=float(settings.get('hp_additional_unit_aux_kw', 0.0)),
            substeps_per_hour=4,
        )

        for k in arr_keys:
            if k in sim:
                arrays[k][wi, :n_valid] = sim[k]

        year_sum = dict(sim['summary'])
        year_sum['weather_year'] = years[wi]
        by_weather.append(year_sum)

    # Compute envelopes across the 19 weather years
    summary_fields = [
        'annual_total_electricity_kwh',
        'annual_space_compressor_electricity_kwh',
        'annual_dhw_compressor_electricity_kwh',
        'annual_space_backup_electricity_kwh',
        'annual_dhw_backup_electricity_kwh',
        'annual_external_pump_electricity_kwh',
        'annual_standby_electricity_kwh',
        'annual_auxiliary_electricity_kwh',
        'annual_requested_space_heat_kwh',
        'annual_delivered_space_heat_kwh',
        'annual_unmet_space_heat_kwh',
        'annual_requested_draw_heat_kwh',
        'annual_delivered_draw_heat_kwh',
        'annual_actual_draw_heat_kwh',
        'annual_unserved_draw_heat_kwh',
        'annual_unmet_draw_heat_kwh',
        'annual_space_compressor_heat_kwh',
        'annual_dhw_compressor_heat_kwh',
        'annual_space_backup_heat_kwh',
        'annual_dhw_backup_heat_kwh',
        'annual_space_distribution_loss_kwh',
        'annual_heat_supplied_tank_kwh',
        'annual_tank_losses_kwh',
        'seasonal_space_cop',
        'seasonal_dhw_cop_heat_to_tank',
        'seasonal_compressor_cop',
        'seasonal_performance_factor_delivered_heat',
        'seasonal_performance_factor_storage_adjusted',
        'seasonal_performance_factor_heat_to_tank',
        'seasonal_scop_space',
        'seasonal_scop_dhw_net',
        'seasonal_scop_dhw_delivered',
        'total_dhw_compressor_hours',
        'total_space_compressor_hours',
        'max_hourly_compressor_runtime_hours',
        'hygiene_target_miss_count',
        'map_outside_count',
        'storage_delta_kwh',
        'storage_adjusted_heat_kwh',
        'max_tank_conservation_error_kwh',
    ]
    envelopes = {}
    for f in summary_fields:
        vals = [bw[f] for bw in by_weather if f in bw and bw[f] is not None]
        envelopes[f] = envelope(vals)

    boiler_eff = float(settings.get('boiler_efficiency', 0.80))
    gas_ref_alloc = {
        'gas_reference_period': legacy_text('gas_period','Explicit profile reference period'),
        'total_reference_gas_smc': float(gas_allocation_out.monthly_gas.sum()) if gas_allocation_out is not None else None,
        'heating_reference_smc': float(gas_allocation_out.heating_smc.sum()) if gas_allocation_out is not None else 0.0,
        'dhw_reference_smc': float(gas_allocation_out.dhw_smc.sum()) if gas_allocation_out is not None else 0.0,
        'cooking_reference_smc': float(gas_allocation_out.cooking_smc.sum()) if gas_allocation_out is not None else 0.0,
        'heating_useful_kwh': float(gas_allocation_out.heating_useful_kwh.sum()) if gas_allocation_out is not None else 0.0,
        'dhw_gross_useful_kwh': float(gas_allocation_out.dhw_gross_useful_kwh.sum()) if gas_allocation_out is not None else 0.0,
        'cooking_gas_kwh': float(gas_allocation_out.cooking_gas_kwh.sum()) if gas_allocation_out is not None else 0.0,
        'monthly_gas_smc': gas_allocation_out.monthly_gas.tolist() if gas_allocation_out is not None else [],
        'monthly_heating_smc': gas_allocation_out.heating_smc.tolist() if gas_allocation_out is not None else [],
        'monthly_dhw_smc': gas_allocation_out.dhw_smc.tolist() if gas_allocation_out is not None else [],
        'monthly_cooking_smc': gas_allocation_out.cooking_smc.tolist() if gas_allocation_out is not None else [],
        'monthly_heating_useful_kwh': gas_allocation_out.heating_useful_kwh.tolist() if gas_allocation_out is not None else [],
        'monthly_dhw_gross_useful_kwh': gas_allocation_out.dhw_gross_useful_kwh.tolist() if gas_allocation_out is not None else [],
    }

    metadata = {
        'equipment_model': equipment_map.metadata.model,
        'equipment_source': equipment_map.metadata.source,
        'provisional_nominal_capacity_kw': float(settings.get('hp_nominal_kw', 9.0)),
        'provisional_radiator_dt50_kw': float(settings.get('hp_radiator_dt50_kw', 10.0)),
        'radiator_exponent': float(settings.get('hp_radiator_exponent', 1.3)),
        'boiler_efficiency': boiler_eff,
        'boiler_efficiency_status': f'UNVERIFIED USER ESTIMATE ({boiler_eff:.2f} {legacy_text('boiler_basis','explicit profile assumption')}; not a measured or rated seasonal efficiency)',
        'capacity_disclaimer': legacy_text('hp_capacity_disclaimer','Nominal equipment capacity is an explicit modeling assumption; equipment applicability is unverified.'),
        'emitter_disclaimer': 'Provisional EN 442 radiator rating; actual installed emitters and dwelling thermal characteristics remain unverified.',
        'boiler_efficiency_disclaimer': f'Estimated existing gas boiler seasonal efficiency (unverified user estimate {boiler_eff:.2f}).',
        'gas_elimination_status': 'Gas service is not eliminated if cooking remains on natural gas.',
        'gas_reference_period': legacy_text('gas_period','Explicit profile reference period'),
        'source_period': f'{legacy_text('gas_period','Explicit profile reference period')} gas reference and climatology {years[0]}–{years[-1]}' if len(years) > 1 else f'{legacy_text('gas_period','Explicit profile reference period')} gas reference and climatology {years[0]}',
        'climatology_period': f'{years[0]}–{years[-1]} PVGIS historical weather ensemble' if len(years) > 1 else f'{years[0]} PVGIS historical weather ensemble',
        'reference_gas_allocation': gas_ref_alloc,
        'weather_reference_basis': f'Fixed monthly thermal budgets vs weather-sensitive climatology reference ({years[0]}–{years[-1]} PVGIS ensemble; no coincident weather for {legacy_text('gas_period','Explicit profile reference period')})' if len(years) > 1 else f'Fixed monthly thermal budgets vs weather-sensitive climatology reference ({years[0]} PVGIS ensemble; no coincident weather for {legacy_text('gas_period','Explicit profile reference period')})',
        'measured_vs_assumed': legacy_text('hp_measured_vs_assumed','Gross electricity and thermal baseline quantities come from explicit profile inputs. Equipment performance, emitters, tank losses and seasonal efficiency are modeling assumptions unless separately evidenced.'),
        'balance_temperature_distinction': legacy_text('hp_balance_temperature_distinction','Configured indoor comfort temperature is conceptually distinct from outdoor heating balance temperature.'),
        'comfort_and_recovery_validation': 'No comfort or recovery validation: dynamic building RC network, room temperature decay during setback/unheated hours, and recovery morning reheat surges are not modeled; unmet thermal demands are explicitly reported and remain unmet.',
        'building_dynamics_disclaimer': 'No house RC network, unheated cooldown electricity, or fabricated reheat recovery peak spikes modeled.',
        'schedule_and_loss_limitations': 'Space heating and DHW draw/reheat subject to configured operating schedules and prior DHW standing loss limitations.',
        'source_example_links': [
            {
                'title': 'Vaillant aroTHERM plus Technical Brochure (Literature Modulation Example)',
                'url': 'https://www.vaillant.com/en/products/arothermplus/',
                'note': 'Cited in HEAT_PUMP_MODEL.md as literature example illustrating inverter modulating compressor turndown and part-load vs maximum-capacity trap; NOT a manufacturer recommendation or specific equipment endorsement.',
            },
            {
                'title': 'Stelrad Technical Guidance: Radiator Correction Factors (EN 442 Delta-T 50)',
                'url': 'https://www.stelrad.ie/wp-content/uploads/2021/10/Stelrad-Correction-Factor-Oct-21.pdf',
                'note': 'Cited in HEAT_PUMP_MODEL.md for standard EN 442 Delta-T 50 emitter derating formulation at reduced flow temperatures.',
            },
        ],
        'manufacturer_recommendation_disclaimer': 'References to equipment brochures or manufacturer literature in documentation serve solely as illustrative modeling examples of inverter compressor modulation and emitter derating; they do not constitute an engineering recommendation, brand endorsement, or sizing specification.',
        'operating_clock_note': 'API timeline and chart plots use fixed UTC+01:00 local standard time without daylight saving jumps. Operating schedule gates (heating schedule, DHW reheat window, hygiene cycles) are evaluated in Europe/Rome civil time when timezone_mode=civil.',
        'ratio_definitions': {
            'seasonal_performance_factor_delivered_heat': 'Ratio of useful qualified delivered heat (space heating + DHW draw service) to total electricity input (compressor + resistance backup + auxiliary pump and standby). Excludes unserved lukewarm water withdrawals and does not add tank storage delta into delivered service.',
            'seasonal_performance_factor_storage_adjusted': 'Storage-adjusted seasonal performance factor: (space qualified delivered + DHW qualified delivered + storage delta) / total electricity input. Discloses initial vs final tank storage energy; null if numerator is negative.',
            'seasonal_performance_factor_heat_to_tank': 'Ratio of space delivered heat plus thermal heat delivered by compressor and backup to the DHW cylinder to total electricity input.',
            'seasonal_scop_space': 'Seasonal coefficient of performance for space heating: space useful delivered heat / (space compressor electricity + space resistance backup electricity). Explicitly excludes auxiliaries.',
            'seasonal_scop_dhw_net': 'Seasonal coefficient of performance for DHW: net DHW service (delivered draw heat + storage delta) / (DHW compressor electricity + DHW backup electricity). Excludes unserved lukewarm water.',
            'seasonal_scop_dhw_delivered': 'Seasonal coefficient of performance for delivered DHW service: qualified delivered draw heat / (DHW compressor electricity + DHW backup electricity).',
            'seasonal_space_cop': 'Seasonal space heating compressor COP: space compressor thermal output at equipment boundary / space compressor electricity.',
            'seasonal_dhw_cop_heat_to_tank': 'Seasonal DHW compressor COP: DHW compressor thermal output to cylinder / DHW compressor electricity.',
            'seasonal_compressor_cop': 'Total compressor COP: (space compressor heat + DHW compressor heat) / (space compressor electricity + DHW compressor electricity).',
        },
    }

    ref = engine._thermal_reference()
    if ref is not None:
        from .profiles import profile_to_dict
        metadata = {k: v for k,v in metadata.items() if k not in (
            'boiler_efficiency', 'boiler_efficiency_status', 'boiler_efficiency_disclaimer',
            'gas_reference_period', 'reference_gas_allocation', 'source_period',
            'weather_reference_basis', 'measured_vs_assumed')}
        metadata.update(thermal_reference_version=ref.version,
            reference_gas_allocation=None,
            thermal_baseline={k: dict(carrier=v.carrier,
                monthly_useful_kwh=list(v.monthly_useful_kwh),
                monthly_carrier_input=None if v.monthly_input is None else list(v.monthly_input),
                carrier_unit=v.unit, included_in_electric_load=v.included_in_electric_load,
                action=engine._thermal_action(settings,k), provenance=profile_to_dict(v.provenance))
                for k,v in ref.end_uses.items()},
            source_period='Explicit household thermal coverage; historical weather analogues',
            weather_reference_basis='Explicit monthly useful heat with optional degree-hour scaling',
            measured_vs_assumed='See thermal input provenance; equipment parameters remain scenario assumptions')

    summary = {
        'by_weather': by_weather,
        'envelopes': envelopes,
        'metadata': metadata,
    }

    result = {**arrays, 'gas_allocation': gas_allocation_out, 'summary': summary}
    return result


@lru_cache(maxsize=4)
def _cached_source_heat_pump_hourly(runtime_key, key: tuple[tuple[str, Any], ...]) -> dict[str, Any]:
    """LRU-cached entrypoint for historical heat pump source simulation."""
    assert_runtime_key(runtime_key)
    settings = dict(key)
    return _simulate_source_ensemble(settings)


def get_source_heat_pump_hourly(settings: dict[str, Any]) -> dict[str, Any]:
    """Retrieve 19-year historical heat pump simulation series and summary metadata.

    Caches based strictly on physical heat pump and heating/DHW settings, ensuring
    independence from PV array layout, battery storage capacity, pricing, and financing.
    """
    key = tuple((k, settings.get(k)) for k in HP_SOURCE_CACHE_KEYS)
    return _cached_source_heat_pump_hourly(current_runtime().key, key)


def _simulate_forecast_ensemble(
    settings: dict[str, Any],
    forecast_year: int,
    mapped_temp: np.ndarray,
    target_std_hours: np.ndarray,
    target_civil_hours: np.ndarray,
    target_months: np.ndarray,
    target_len: int,
    historical_years: Optional[list[int]] = None,
) -> dict[str, Any]:
    """Execute target calendar simulation of the shared heat pump for forecast views."""
    from feasibility import engine
    n_years = len(mapped_temp)
    tz_mode = settings.get('timezone_mode', 'standard')
    clock_hours = target_civil_hours if tz_mode == 'civil' else target_std_hours

    ref_degree_hours = get_source_reference_degree_hours(current_runtime().key, settings.get('heating_balance_temp', 18.0))
    equipment_map = resolve_equipment_map(settings)
    flow_curve = resolve_weather_compensation_curve(settings)

    valid_target = np.ones((n_years, target_len), dtype=bool)

    # 1. Recompute space heating demand on target calendar
    demand_res = calculate_heat_demand(
        temperature=mapped_temp,
        valid=valid_target,
        months=target_months,
        hours=target_std_hours,
        civil_hours=target_civil_hours,
        source_years=(
            [str(y) for y in historical_years]
            if historical_years is not None
            and len(historical_years) == n_years
            and not all(int(y) == int(forecast_year) for y in historical_years)
            else [f"Analogue_{i}" for i in range(n_years)]
        ),
        **_thermal_demand_arguments(settings),
        summer_gas_base=float(settings.get('summer_gas_base', 20.0)),
        cooking_smc_annual=float(settings.get('cooking_smc_annual', 20.0)),
        gas_pcs=float(settings.get('gas_pcs', 11.0)),
        boiler_efficiency=float(settings.get('boiler_efficiency', 0.80)),
        heating_balance_temp=float(settings.get('heating_balance_temp', 18.0)),
        indoor_temp=float(settings.get('hp_indoor_temp', 18.0)),
        setback_delta=float(settings.get('hp_setback_delta', 0.0)),
        schedule_mode=settings.get('hp_schedule_mode', 'continuous'),
        timezone_mode=tz_mode,
        morning_window=(int(settings.get('hp_morning_on', 6)), int(settings.get('hp_morning_off', 8))),
        evening_window=(int(settings.get('hp_evening_on', 16)), int(settings.get('hp_evening_off', 20))),
        weather_sensitive=bool(settings.get('hp_weather_sensitive', False)),
        reference_degree_hours=ref_degree_hours,
    )

    # 2. Recompute DHW draw demand on target calendar
    dhw_mode = settings.get('hp_dhw_demand_mode', 'gas_calibrated')
    if dhw_mode == 'gas_calibrated' or engine._thermal_reference() is not None:
        q_draw = build_hourly_dhw_demand(
            valid=valid_target[0],
            months=target_months,
            hours=clock_hours,
            monthly_dhw_kwh=_thermal_dhw_budget(settings, demand_res),
            prior_loss_fraction=float(settings.get('hp_prior_dhw_loss_fraction', 0.0)),
            morning_window=(int(settings.get('hp_draw_morning_on', 7)), int(settings.get('hp_draw_morning_off', 9))),
            evening_window=(int(settings.get('hp_draw_evening_on', 18)), int(settings.get('hp_draw_evening_off', 21))),
            morning_fraction=float(settings.get('hp_draw_morning_fraction', 0.5)),
            evening_fraction=1.0 - float(settings.get('hp_draw_morning_fraction', 0.5)),
        )
    else:
        q_draw = build_hourly_dhw_demand(
            valid=valid_target[0],
            months=target_months,
            hours=clock_hours,
            litres_per_day=float(settings.get('hp_dhw_litres_day', 150.0)),
            service_temp_c=float(settings.get('hp_tank_service_temp', 40.0)),
            inlet_temp_c=float(settings.get('hp_tank_inlet_temp', 15.0)),
            prior_loss_fraction=0.0,
            morning_window=(int(settings.get('hp_draw_morning_on', 7)), int(settings.get('hp_draw_morning_off', 9))),
            evening_window=(int(settings.get('hp_draw_evening_on', 18)), int(settings.get('hp_draw_evening_off', 21))),
            morning_fraction=float(settings.get('hp_draw_morning_fraction', 0.5)),
            evening_fraction=1.0 - float(settings.get('hp_draw_morning_fraction', 0.5)),
        )

    reheat_mask = build_dhw_reheat_mask(target_len, settings, clock_hours)
    hygiene_mask = build_forecast_hygiene_mask(forecast_year, target_len, settings, target_civil_hours, target_std_hours)

    arr_keys = [
        'total_electricity_kwh',
        'space_compressor_electricity_kwh',
        'dhw_compressor_electricity_kwh',
        'space_backup_electricity_kwh',
        'dhw_backup_electricity_kwh',
        'external_pump_electricity_kwh',
        'standby_electricity_kwh',
        'auxiliary_electricity_kwh',
        'requested_space_heat_kwh',
        'delivered_space_heat_kwh',
        'unmet_space_heat_kwh',
        'space_delivered_heat_kwh',
        'space_unmet_heat_kwh',
        'requested_draw_heat_kwh',
        'delivered_draw_heat_kwh',
        'unmet_draw_heat_kwh',
        'actual_draw_heat_kwh',
        'unserved_draw_heat_kwh',
        'draw_delivered_heat_kwh',
        'draw_unmet_heat_kwh',
        'space_compressor_heat_kwh',
        'dhw_compressor_heat_kwh',
        'space_backup_heat_kwh',
        'dhw_backup_heat_kwh',
        'space_distribution_loss_kwh',
        'heat_supplied_tank_kwh',
        'tank_losses_kwh',
        'tank_temp_c',
        'tank_start_storage_kwh',
        'tank_end_storage_kwh',
        'space_flow_temp_c',
        'dhw_flow_temp_c',
        'space_capacity_kw',
        'dhw_capacity_kw',
        'space_runtime_hours',
        'dhw_runtime_hours',
        'radiator_bound_kw',
        'map_outside_flag',
        'hygiene_target_miss',
    ]
    bool_keys = {'map_outside_flag', 'hygiene_target_miss'}
    forecast_arrays = {
        k: (np.zeros((n_years, target_len), dtype=bool) if k in bool_keys else np.zeros((n_years, target_len), dtype=float))
        for k in arr_keys
    }

    t_target = float(settings.get('hp_tank_target_temp', 50.0))
    t_hygiene_conf = float(settings.get('hp_hygiene_temp', 60.0))
    hygiene_enabled = bool(settings.get('hp_hygiene_enabled', False))
    if hygiene_enabled and t_hygiene_conf < t_target:
        raise ValueError(
            f"hp_hygiene_temp ({t_hygiene_conf}) cannot be lower than "
            f"hp_tank_target_temp ({t_target}) when hygiene is enabled"
        )
    t_hygiene_eff = t_hygiene_conf if hygiene_enabled else max(t_target, t_hygiene_conf)

    by_weather = []
    for wi in range(n_years):
        sim = simulate_shared_heat_pump(
            outdoor_temp_c=mapped_temp[wi],
            requested_space_heat_kwh=demand_res.heat_demand_kwh[wi],
            draw_heat_kwh=q_draw,
            space_active_mask=demand_res.active_mask[wi],
            dhw_reheat_allowed_mask=reheat_mask,
            hygiene_mask=hygiene_mask,
            equipment_map=equipment_map,
            flow_curve=flow_curve,
            rated_radiator_dt50_kw=float(settings.get('hp_radiator_dt50_kw', 10.0)),
            radiator_exponent=float(settings.get('hp_radiator_exponent', 1.3)),
            radiator_return_drop_c=float(settings.get('hp_return_drop', 5.0)),
            indoor_temp_c=float(settings.get('hp_indoor_temp', 18.0)),
            tank_volume_litres=float(settings.get('hp_tank_litres', 200.0)),
            tank_target_temp_c=t_target,
            tank_hygiene_temp_c=t_hygiene_eff,
            tank_inlet_temp_c=float(settings.get('hp_tank_inlet_temp', 15.0)),
            tank_ambient_temp_c=float(settings.get('hp_tank_ambient_temp', 20.0)),
            tank_standing_loss_w_per_k=float(settings.get('hp_tank_loss_w_k', 1.0)),
            tank_deadband_k=float(settings.get('hp_tank_deadband', 5.0)),
            tank_min_service_temp_c=float(settings.get('hp_tank_service_temp', 40.0)),
            tank_initial_temp_c=float(settings.get('hp_tank_initial_temp', 50.0)),
            coil_approach_delta_c=float(settings.get('hp_coil_approach', 5.0)),
            external_pump_kw=float(settings.get('hp_external_pump_kw', 0.06)),
            standby_kw=float(settings.get('hp_standby_kw', 0.02)),
            backup_heater_kw=float(settings.get('hp_backup_kw', 3.0)),
            backup_efficiency=float(settings.get('hp_backup_efficiency', 1.0)),
            min_modulation_fraction=float(settings.get('hp_min_modulation_fraction', 0.25)),
            cycling_degradation_penalty=float(settings.get('hp_cycling_penalty', 0.10)),
            thermal_distribution_loss_fraction=float(settings.get('hp_distribution_loss_fraction', 0.0)),
            additional_defrost_cop_penalty=float(settings.get('hp_additional_defrost_penalty', 0.0)),
            additional_unit_auxiliary_kw=float(settings.get('hp_additional_unit_aux_kw', 0.0)),
            substeps_per_hour=4,
        )
        for k in arr_keys:
            if k in sim:
                forecast_arrays[k][wi] = sim[k]
        by_weather.append(sim['summary'])

    mapped_hp_electricity = forecast_arrays['total_electricity_kwh']
    mapped_hp_breakdown = {
        **forecast_arrays,
        'by_weather': by_weather,
        'forecast_year': forecast_year,
        'target_len': target_len,
    }

    return {
        'mapped_hp_electricity': mapped_hp_electricity,
        'mapped_hp_breakdown': mapped_hp_breakdown,
    }


@lru_cache(maxsize=4)
def _cached_forecast_heat_pump_hourly(runtime_key, key: tuple[tuple[str, Any], ...], forecast_year: int) -> dict[str, Any]:
    """LRU-cached entrypoint for target calendar forecast heat pump simulation."""
    assert_runtime_key(runtime_key)
    settings = dict(key)
    from feasibility import engine
    mappings, target_std_hours, target_civil_hours, target_months, is_target_leap, target_len = engine._forecast_calendar_mapping(current_runtime().key, forecast_year)
    d = engine.data()
    years = list(map(int, d['years']))
    mapped_temp = np.zeros((len(years), target_len))
    for wi in range(len(years)):
        mapped_temp[wi] = engine._apply_mapping(d['temperature'][wi], mappings[wi])

    return _simulate_forecast_ensemble(
        settings=settings,
        forecast_year=forecast_year,
        mapped_temp=mapped_temp,
        target_std_hours=target_std_hours,
        target_civil_hours=target_civil_hours,
        target_months=target_months,
        target_len=target_len,
        historical_years=years,
    )


def get_forecast_heat_pump_hourly(
    settings: dict[str, Any],
    forecast_year: int,
    mapped_temp: np.ndarray | None = None,
    target_std_hours: np.ndarray | None = None,
    target_civil_hours: np.ndarray | None = None,
    target_months: np.ndarray | None = None,
    target_len: int | None = None,
) -> dict[str, Any]:
    """Retrieve recomputed heat pump simulation on the target forecast calendar.

    Caches based on target forecast year and physical heat pump settings.
    Bounded to small cache budget (maxsize=4) due to heavy hourly memory buffers (~48MB/entry).

    Calendar and Weather Policy:
    Preserves exact paired-weather historical ensemble alignment using canonical engine
    forecast calendar mappings (engine._forecast_calendar_mapping) and canonical PVGIS
    temperature series. Caller-supplied `mapped_temp`, `target_std_hours`, etc., if provided,
    are accepted for call signature compatibility, but canonical engine calendar mappings and
    paired weather ensembles are used internally.
    """
    key = tuple((k, settings.get(k)) for k in HP_SOURCE_CACHE_KEYS)
    return _cached_forecast_heat_pump_hourly(current_runtime().key, key, int(forecast_year))
