"""Heat pump schema specifications, key definitions, map parsing, and cross-field validation.

Stage 4A settings and schema integration for optional air-to-water heat pump modeling.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
from typing import Any

import numpy as np

from feasibility.heatpump import HeatPumpMapMetadata, HeatPumpPerformanceMap


# Key groupings and metadata
HP_SPEC: dict[str, tuple[Any, str, Any, str, str]] = {
    'planning_heat_pump': (
        False, 'boolean', None, 'heat_pump',
        'Planning a heat pump',
    ),
    'hp_nominal_kw': (
        9.0, 'kW', [0.5, 50.0], 'heat_pump',
        'Nominal heat pump thermal capacity at A7/W35 reference condition (illustrative model input, no selected equipment, not verified capacity).',
    ),
    'hp_weather_sensitive': (
        False, 'boolean', None, 'heat_pump',
        'Weather-sensitive degree-day space heating demand scaling.',
    ),
    'hp_indoor_temp': (
        18.0, 'C', [10.0, 30.0], 'heat_pump',
        'Indoor heating comfort setpoint temperature.',
    ),
    'hp_setback_delta': (
        0.0, 'C', [0.0, 15.0], 'heat_pump',
        'Indoor temperature setback reduction during unheated hours.',
    ),
    'hp_schedule_mode': (
        'continuous', 'choice', ['continuous', 'scheduled'], 'heat_pump',
        'Heat pump space heating operating schedule mode (continuous 24h or scheduled daily windows).',
    ),
    'hp_morning_on': (
        6, 'hour', [0, 23], 'heat_pump',
        'Heat pump morning space heating window start hour (inclusive).',
    ),
    'hp_morning_off': (
        8, 'hour', [0, 23], 'heat_pump',
        'Heat pump morning space heating window stop hour (exclusive).',
    ),
    'hp_evening_on': (
        16, 'hour', [0, 23], 'heat_pump',
        'Heat pump evening space heating window start hour (inclusive).',
    ),
    'hp_evening_off': (
        20, 'hour', [0, 23], 'heat_pump',
        'Heat pump evening space heating window stop hour (exclusive).',
    ),
    'hp_curve_outdoor_cold': (
        -5.0, 'C', [-30.0, 15.0], 'heat_pump',
        'Outdoor design temperature at cold design point for weather compensation.',
    ),
    'hp_curve_outdoor_mild': (
        15.0, 'C', [0.0, 30.0], 'heat_pump',
        'Outdoor mild temperature for weather compensation.',
    ),
    'hp_curve_flow_cold': (
        55.0, 'C', [25.0, 80.0], 'heat_pump',
        'Flow temperature at outdoor cold design point.',
    ),
    'hp_curve_flow_mild': (
        35.0, 'C', [20.0, 60.0], 'heat_pump',
        'Flow temperature at outdoor mild design point.',
    ),
    'hp_radiator_dt50_kw': (
        10.0, 'kW', [0.5, 100.0], 'heat_pump',
        'Provisional total installed radiator capacity at Delta-T 50 (EN 442 rating; installed emitters unverified).',
    ),
    'hp_radiator_exponent': (
        1.3, 'exponent', [1.0, 2.0], 'heat_pump',
        'Radiator output temperature exponent n (provisional EN 442 default 1.3).',
    ),
    'hp_return_drop': (
        5.0, 'C', [0.5, 30.0], 'heat_pump',
        'Radiator return temperature drop (flow minus return temperature).',
    ),
    'hp_tank_litres': (
        200.0, 'litres', [10.0, 2000.0], 'heat_pump',
        'DHW cylinder storage volume.',
    ),
    'hp_tank_target_temp': (
        50.0, 'C', [30.0, 85.0], 'heat_pump',
        'DHW cylinder target storage temperature.',
    ),
    'hp_tank_inlet_temp': (
        15.0, 'C', [0.0, 40.0], 'heat_pump',
        'Cold water mains inlet supply temperature.',
    ),
    'hp_tank_ambient_temp': (
        20.0, 'C', [-20.0, 60.0], 'heat_pump',
        'Ambient air temperature surrounding DHW storage cylinder.',
    ),
    'hp_tank_loss_w_k': (
        1.0, 'W/K', [0.0, 10.0], 'heat_pump',
        'DHW cylinder standing heat loss coefficient.',
    ),
    'hp_tank_deadband': (
        5.0, 'C', [0.5, 25.0], 'heat_pump',
        'DHW tank thermostat hysteresis deadband.',
    ),
    'hp_tank_service_temp': (
        40.0, 'C', [25.0, 70.0], 'heat_pump',
        'Minimum hot water service delivery temperature threshold.',
    ),
    'hp_tank_initial_temp': (
        50.0, 'C', [10.0, 85.0], 'heat_pump',
        'Initial tank water temperature at simulation start.',
    ),
    'hp_coil_approach': (
        5.0, 'C', [0.5, 20.0], 'heat_pump',
        'Heat exchanger coil approach temperature delta for DHW reheat.',
    ),
    'hp_dhw_demand_mode': (
        'gas_calibrated', 'choice', ['gas_calibrated', 'litres'], 'heat_pump',
        'DHW thermal demand allocation basis (gas-calibrated monthly or litres/day).',
    ),
    'hp_dhw_litres_day': (
        150.0, 'litres/day', [0.0, 1000.0], 'heat_pump',
        'Daily DHW draw volume for litres demand mode.',
    ),
    'hp_prior_dhw_loss_fraction': (
        0.0, 'fraction', [0.0, 1.0], 'heat_pump',
        'Estimated prior gas water heating standing loss fraction to subtract from gross gas DHW heat.',
    ),
    'hp_draw_morning_on': (
        7, 'hour', [0, 23], 'heat_pump',
        'DHW morning draw window start hour (inclusive).',
    ),
    'hp_draw_morning_off': (
        9, 'hour', [0, 23], 'heat_pump',
        'DHW morning draw window stop hour (exclusive).',
    ),
    'hp_draw_evening_on': (
        18, 'hour', [0, 23], 'heat_pump',
        'DHW evening draw window start hour (inclusive).',
    ),
    'hp_draw_evening_off': (
        21, 'hour', [0, 23], 'heat_pump',
        'DHW evening draw window stop hour (exclusive).',
    ),
    'hp_draw_morning_fraction': (
        0.5, 'fraction', [0.0, 1.0], 'heat_pump',
        'Fraction of daily DHW draw allocated to morning window.',
    ),
    'hp_reheat_mode': (
        'continuous', 'choice', ['continuous', 'scheduled'], 'heat_pump',
        'DHW cylinder reheat schedule mode (continuous 24h or scheduled window).',
    ),
    'hp_reheat_on': (
        11, 'hour', [0, 23], 'heat_pump',
        'Scheduled DHW reheat window start hour (inclusive).',
    ),
    'hp_reheat_off': (
        16, 'hour', [0, 23], 'heat_pump',
        'Scheduled DHW reheat window stop hour (exclusive).',
    ),
    'hp_hygiene_enabled': (
        False, 'boolean', None, 'heat_pump',
        'Enable periodic high-temperature thermal hygiene/legionella disinfection.',
    ),
    'hp_hygiene_temp': (
        60.0, 'C', [50.0, 90.0], 'heat_pump',
        'Target water temperature during thermal hygiene disinfection cycle.',
    ),
    'hp_hygiene_weekday': (
        0, 'weekday', [0, 6], 'heat_pump',
        'Day of week for hygiene cycle (0=Monday, 6=Sunday).',
    ),
    'hp_hygiene_hour': (
        12, 'hour', [0, 23], 'heat_pump',
        'Start clock hour for weekly hygiene cycle.',
    ),
    'hp_hygiene_duration_hours': (
        1, 'hours', [1, 12], 'heat_pump',
        'Duration of hygiene disinfection cycle in hours.',
    ),
    'hp_backup_kw': (
        3.0, 'kW', [0.0, 30.0], 'heat_pump',
        'Electrical resistance auxiliary backup heater capacity.',
    ),
    'hp_backup_efficiency': (
        1.0, 'fraction', [0.5, 1.0], 'heat_pump',
        'Backup electric heater thermal conversion efficiency.',
    ),
    'hp_external_pump_kw': (
        0.06, 'kW', [0.0, 2.0], 'heat_pump',
        'Secondary hydronic distribution circulation pump power.',
    ),
    'hp_standby_kw': (
        0.02, 'kW', [0.0, 1.0], 'heat_pump',
        'Continuous heat pump standby electrical consumption.',
    ),
    'hp_min_modulation_fraction': (
        0.25, 'fraction', [0.05, 1.0], 'heat_pump',
        'Minimum compressor modulation output fraction before cycling occurs.',
    ),
    'hp_cycling_penalty': (
        0.1, 'fraction', [0.0, 0.5], 'heat_pump',
        'COP degradation penalty factor when modulating below minimum modulation fraction.',
    ),
    'hp_distribution_loss_fraction': (
        0.0, 'fraction', [0.0, 0.5], 'heat_pump',
        'Hydronic heating piping distribution thermal loss fraction.',
    ),
    'hp_additional_defrost_penalty': (
        0.0, 'fraction', [0.0, 0.5], 'heat_pump',
        'Additional seasonal defrost COP penalty (skipped if map metadata confirms defrost included).',
    ),
    'hp_additional_unit_aux_kw': (
        0.0, 'kW', [0.0, 2.0], 'heat_pump',
        'Additional internal unit auxiliary electrical power (skipped if map metadata confirms unit auxiliaries included).',
    ),
    'hp_capex': (
        10000.0, 'EUR', [0.0, 50000.0], 'cost',
        'Turnkey heat pump system installed capital expenditure (shared outdoor unit and DHW cylinder; explicit placeholder).',
    ),
    'hp_annual_maintenance': (
        100.0, 'EUR/year', [0.0, 2000.0], 'cost',
        'Annual heat pump maintenance and servicing allowance.',
    ),
    'hp_replacement_year': (
        0, 'year', [0, 30], 'aging',
        'Heat pump equipment replacement year (0 disables replacement).',
    ),
    'hp_replacement_cost': (
        6000.0, 'EUR', [0.0, 30000.0], 'cost',
        'Installed heat pump unit replacement cost in current euros (explicit placeholder).',
    ),
    'hp_electric_cooking': (
        False, 'boolean', None, 'heat_pump',
        'Convert cooking from gas to induction/electric cooktop.',
    ),
    'hp_cooking_capex': (
        500.0, 'EUR', [0.0, 10000.0], 'cost',
        'Induction hob / electric cooking equipment and installation cost.',
    ),
    'hp_disconnect_gas': (
        False, 'boolean', None, 'heat_pump',
        'Permanently disconnect gas utility meter and terminate gas service contract.',
    ),
    'hp_manufacturer_map_json': (
        '', 'json', None, 'heat_pump',
        'Optional manufacturer equipment performance map JSON string (empty string uses provisional fallback map with hp_nominal_kw scaling).',
    ),
}

HP_ALIASES: dict[str, str] = {
    'hp_tank_target': 'hp_tank_target_temp',
    'hp_tank_inlet': 'hp_tank_inlet_temp',
    'hp_tank_ambient': 'hp_tank_ambient_temp',
    'hp_tank_coil_approach': 'hp_coil_approach',
    'hp_tank_deadband_k': 'hp_tank_deadband',
}

HP_PHYSICAL_KEYS: list[str] = [
    'hp_nominal_kw',
    'hp_weather_sensitive',
    'hp_indoor_temp',
    'hp_setback_delta',
    'hp_schedule_mode',
    'hp_morning_on',
    'hp_morning_off',
    'hp_evening_on',
    'hp_evening_off',
    'hp_curve_outdoor_cold',
    'hp_curve_outdoor_mild',
    'hp_curve_flow_cold',
    'hp_curve_flow_mild',
    'hp_radiator_dt50_kw',
    'hp_radiator_exponent',
    'hp_return_drop',
    'hp_tank_litres',
    'hp_tank_target_temp',
    'hp_tank_inlet_temp',
    'hp_tank_ambient_temp',
    'hp_tank_loss_w_k',
    'hp_tank_deadband',
    'hp_tank_service_temp',
    'hp_tank_initial_temp',
    'hp_coil_approach',
    'hp_dhw_demand_mode',
    'hp_dhw_litres_day',
    'hp_prior_dhw_loss_fraction',
    'hp_draw_morning_on',
    'hp_draw_morning_off',
    'hp_draw_evening_on',
    'hp_draw_evening_off',
    'hp_draw_morning_fraction',
    'hp_reheat_mode',
    'hp_reheat_on',
    'hp_reheat_off',
    'hp_hygiene_enabled',
    'hp_hygiene_temp',
    'hp_hygiene_weekday',
    'hp_hygiene_hour',
    'hp_hygiene_duration_hours',
    'hp_backup_kw',
    'hp_backup_efficiency',
    'hp_external_pump_kw',
    'hp_standby_kw',
    'hp_min_modulation_fraction',
    'hp_cycling_penalty',
    'hp_distribution_loss_fraction',
    'hp_additional_defrost_penalty',
    'hp_additional_unit_aux_kw',
    'hp_manufacturer_map_json',
    'hp_electric_cooking',
]

HP_COST_KEYS: list[str] = [
    'hp_capex',
    'hp_annual_maintenance',
    'hp_replacement_year',
    'hp_replacement_cost',
    'hp_cooking_capex',
    'hp_disconnect_gas',
]

HP_ALL_KEYS: list[str] = ['planning_heat_pump'] + HP_PHYSICAL_KEYS + HP_COST_KEYS

HP_INTEGER_NAMES: set[str] = {
    'hp_morning_on',
    'hp_morning_off',
    'hp_evening_on',
    'hp_evening_off',
    'hp_draw_morning_on',
    'hp_draw_morning_off',
    'hp_draw_evening_on',
    'hp_draw_evening_off',
    'hp_reheat_on',
    'hp_reheat_off',
    'hp_hygiene_weekday',
    'hp_hygiene_hour',
    'hp_hygiene_duration_hours',
    'hp_replacement_year',
}


def _window_hours(on_h: int, off_h: int) -> set[int]:
    """Active clock hours (0..23) for a start-inclusive, end-exclusive window."""
    if on_h == off_h:
        return set()
    if on_h < off_h:
        return set(range(on_h, off_h))
    return set(range(on_h, 24)) | set(range(0, off_h))


def parse_manufacturer_map_json(raw_json: str | None) -> HeatPumpPerformanceMap | None:
    """Validate and deserialize an optional manufacturer map JSON string into HeatPumpPerformanceMap.

    Returns None if raw_json is empty or whitespace.
    Raises ValueError with a clear explanation for malformed JSON, partial/ragged grids,
    non-finite values, or invalid metadata.
    """
    if raw_json is None:
        return None
    text = raw_json.strip()
    if not text:
        return None

    try:
        data = json.loads(text)
    except Exception as exc:
        raise ValueError(f"hp_manufacturer_map_json is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("hp_manufacturer_map_json must be a JSON object")

    required = {'outdoor_temps', 'water_temps', 'cop_grid', 'capacity_grid_kw', 'metadata'}
    missing = required - set(data.keys())
    if missing:
        raise ValueError(f"hp_manufacturer_map_json missing required keys: {', '.join(sorted(missing))}")

    meta = data['metadata']
    if not isinstance(meta, dict):
        raise ValueError("Map metadata must be an object")

    for field in ('source', 'model', 'test_basis'):
        val = meta.get(field)
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"Map metadata requires non-empty string '{field}'")

    for req_bool in ('includes_defrost', 'includes_unit_auxiliaries'):
        if req_bool not in meta:
            raise ValueError(f"Map metadata missing required boolean '{req_bool}'")
        if not isinstance(meta[req_bool], bool):
            raise ValueError(f"Map metadata '{req_bool}' must be a boolean")

    metadata = HeatPumpMapMetadata(
        source=str(meta['source']),
        model=str(meta['model']),
        test_basis=str(meta['test_basis']),
        includes_defrost=bool(meta['includes_defrost']),
        includes_unit_auxiliaries=bool(meta['includes_unit_auxiliaries']),
        notes=str(meta.get('notes', '')),
    )

    out_temps = data['outdoor_temps']
    wat_temps = data['water_temps']
    cop_grid = data['cop_grid']
    cap_grid = data['capacity_grid_kw']

    if not isinstance(out_temps, (list, tuple)) or not isinstance(wat_temps, (list, tuple)):
        raise ValueError("outdoor_temps and water_temps must be lists of numbers")
    if not isinstance(cop_grid, (list, tuple)) or not isinstance(cap_grid, (list, tuple)):
        raise ValueError("cop_grid and capacity_grid_kw must be 2D lists of numbers")

    # Check finite numbers across all axes and grids
    try:
        out_arr = np.asarray(out_temps, dtype=np.float64)
        wat_arr = np.asarray(wat_temps, dtype=np.float64)
        cop_arr = np.asarray(cop_grid, dtype=np.float64)
        cap_arr = np.asarray(cap_grid, dtype=np.float64)
    except Exception as exc:
        raise ValueError(f"Failed to parse numeric arrays from map data: {exc}") from exc

    if not np.all(np.isfinite(out_arr)) or not np.all(np.isfinite(wat_arr)):
        raise ValueError("Map temperature axes must contain only finite numbers")
    if not np.all(np.isfinite(cop_arr)) or not np.all(np.isfinite(cap_arr)):
        raise ValueError("Map performance grids must contain only finite numbers")

    # Check for ragged 2D grids (rows with varying lengths)
    if len(cop_grid) != len(out_arr) or any(len(row) != len(wat_arr) for row in cop_grid):
        raise ValueError(
            f"cop_grid shape ({cop_arr.shape}) does not match axes "
            f"({len(out_arr)}, {len(wat_arr)})"
        )
    if len(cap_grid) != len(out_arr) or any(len(row) != len(wat_arr) for row in cap_grid):
        raise ValueError(
            f"capacity_grid_kw shape ({cap_arr.shape}) does not match axes "
            f"({len(out_arr)}, {len(wat_arr)})"
        )

    try:
        return HeatPumpPerformanceMap(
            outdoor_temps=out_arr,
            water_temps=wat_arr,
            cop_grid=cop_arr,
            capacity_grid_kw=cap_arr,
            metadata=metadata,
        )
    except Exception as exc:
        raise ValueError(f"Invalid heat pump performance map: {exc}") from exc


def serialize_manufacturer_map(hp_map: HeatPumpPerformanceMap) -> str:
    """Serialize HeatPumpPerformanceMap to canonical JSON string."""
    payload = {
        'outdoor_temps': hp_map.outdoor_temps.tolist(),
        'water_temps': hp_map.water_temps.tolist(),
        'cop_grid': hp_map.cop_grid.tolist(),
        'capacity_grid_kw': hp_map.capacity_grid_kw.tolist(),
        'metadata': {
            'source': hp_map.metadata.source,
            'model': hp_map.metadata.model,
            'test_basis': hp_map.metadata.test_basis,
            'includes_defrost': hp_map.metadata.includes_defrost,
            'includes_unit_auxiliaries': hp_map.metadata.includes_unit_auxiliaries,
            'notes': hp_map.metadata.notes,
        },
    }
    return json.dumps(payload, separators=(',', ':'))


def validate_heat_pump_cross_fields(s: dict[str, Any], supplied: set[str] | None = None) -> None:
    """Strict cross-field validation for heat pump assumptions.

    Inapplicable cross-field checks are bypassed when planning_heat_pump is False
    to safely preserve legacy scenarios.
    """
    # 1. Manufacturer map JSON validation (validated whenever non-empty)
    raw_map = s.get('hp_manufacturer_map_json', '')
    if raw_map:
        parse_manufacturer_map_json(raw_map)

    # 2. Gas disconnection requires electric cooking if cooking gas is positive
    if s.get('hp_disconnect_gas', False):
        cooking_smc = s.get('cooking_smc_annual', 25.0)
        if cooking_smc > 0 and not s.get('hp_electric_cooking', False):
            raise ValueError(
                'Disconnecting gas service (hp_disconnect_gas=True) requires electric cooking '
                '(hp_electric_cooking=True) when cooking gas demand is positive'
            )

    # If heat pump planning is disabled, skip remaining cross-field bounds to preserve old scenarios
    if not s.get('planning_heat_pump', False):
        return

    # 3. Weather-compensation flow curve: ascending outdoor temps, descending flow temps
    t_out_cold = s['hp_curve_outdoor_cold']
    t_out_mild = s['hp_curve_outdoor_mild']
    t_flow_cold = s['hp_curve_flow_cold']
    t_flow_mild = s['hp_curve_flow_mild']
    if t_out_cold >= t_out_mild:
        raise ValueError(
            f"hp_curve_outdoor_cold ({t_out_cold}) must be strictly less than "
            f"hp_curve_outdoor_mild ({t_out_mild})"
        )
    if t_flow_cold <= t_flow_mild:
        raise ValueError(
            f"hp_curve_flow_cold ({t_flow_cold}) must be strictly greater than "
            f"hp_curve_flow_mild ({t_flow_mild})"
        )

    # 4. Tank cross-fields: tank target, service temp, inlet temp, deadband, hygiene
    t_target = s['hp_tank_target_temp']
    t_service = s['hp_tank_service_temp']
    t_inlet = s['hp_tank_inlet_temp']
    deadband = s['hp_tank_deadband']

    if t_service > t_target:
        raise ValueError(
            f"hp_tank_service_temp ({t_service}) cannot exceed hp_tank_target_temp ({t_target})"
        )
    if t_inlet > t_service:
        raise ValueError(
            f"hp_tank_inlet_temp ({t_inlet}) cannot exceed hp_tank_service_temp ({t_service})"
        )
    if deadband <= 0:
        raise ValueError(
            f"hp_tank_deadband ({deadband}) must be strictly positive"
        )
    if (t_target - deadband) < t_inlet:
        raise ValueError(
            f"hp_tank_target_temp minus deadband ({t_target - deadband}) cannot be lower than "
            f"hp_tank_inlet_temp ({t_inlet})"
        )

    # Hygiene check
    if s.get('hp_hygiene_enabled', False):
        t_hygiene = s['hp_hygiene_temp']
        if t_hygiene < t_target:
            raise ValueError(
                f"hp_hygiene_temp ({t_hygiene}) cannot be lower than "
                f"hp_tank_target_temp ({t_target})"
            )

    # 5. Operating windows in scheduled modes
    if s.get('hp_schedule_mode') == 'scheduled':
        w_m = _window_hours(s['hp_morning_on'], s['hp_morning_off'])
        w_e = _window_hours(s['hp_evening_on'], s['hp_evening_off'])
        if not (w_m | w_e):
            raise ValueError(
                'Scheduled heat pump heating mode requires at least one active operating window; '
                'both morning and evening windows are disabled (equal on and off hours)'
            )

    if s.get('hp_reheat_mode') == 'scheduled':
        w_rh = _window_hours(s['hp_reheat_on'], s['hp_reheat_off'])
        if not w_rh:
            raise ValueError(
                'Scheduled DHW reheat mode requires an active reheat operating window '
                '(equal on and off hours disables reheat)'
            )

    w_dm = _window_hours(s['hp_draw_morning_on'], s['hp_draw_morning_off'])
    w_de = _window_hours(s['hp_draw_evening_on'], s['hp_draw_evening_off'])
    if not (w_dm | w_de):
        raise ValueError(
            'DHW draw schedule requires at least one active draw window; '
            'both morning and evening draw windows are disabled (equal on and off hours)'
        )


def get_heat_pump_schema() -> dict[str, Any]:
    """Expose complete heat pump settings schema and key groupings to assist subsequent tasks."""
    return {
        'spec': deepcopy(HP_SPEC),
        'aliases': deepcopy(HP_ALIASES),
        'physical_keys': list(HP_PHYSICAL_KEYS),
        'cost_keys': list(HP_COST_KEYS),
        'all_keys': list(HP_ALL_KEYS),
        'integer_keys': sorted(HP_INTEGER_NAMES),
    }
