"""Gas-calibrated useful thermal space heating and domestic hot water demand modeling.

Stage 2 implementation providing pure numerical helpers for:
1. Gas bill allocation into space heating, domestic hot water (DHW), and cooking end-uses,
   converting gas volumes (Smc) to useful thermal reference energy (kWh) via gas PCS
   and boiler seasonal efficiency (never electricity or AC SCOP).
2. Heat pump operating window scheduling (continuous 24h or scheduled morning/evening windows),
   with overnight wrapping, equal-hour disabling, and civil/standard timezone gating.
3. Useful space heating thermal demand allocation across active operating windows,
   distributing fixed monthly thermal budgets via positive degree-hours below balance temperature.
4. Optional weather-sensitive degree-day mode scaling monthly heat budgets relative to
   an explicit historical reference ensemble denominator.
5. Quasi-steady indoor comfort temperature and off-hours setback degree-demand sensitivity,
   anchored to the baseline 18°C indoor reference without fabricating unphysical thermal
   storage or recovery energy peaks.

Engineering & Literature Notes:
- Monthly gas, heating value and seasonal efficiency must come from explicit inputs.
- DHW useful heat is a gas-inferred gross reference. Tank losses and distribution losses
  must be accounted for separately by downstream equipment models.
- Cooking is kept separate and not automatically electrified in heat pump thermal budgets.
- Weather ensembles are explicit model inputs, not contemporaneous meter evidence.
- Building dynamics: Monthly billing data cannot identify building thermal mass or dynamic UA.
  Radiator cooldown and building thermal inertia are unresolved; no continued electric load
  is implied while off, and no arbitrary reheat energy spikes are fabricated.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Sequence, Tuple

import numpy as np
from .legacy_profile import legacy_text


# Standard defaults
DEFAULT_SUMMER_GAS_BASE: float = 20.0       # Smc/month non-heating base (DHW + cooking)
DEFAULT_COOKING_SMC_ANNUAL: float = 20.0    # Smc/year allocated within non-heating base
DEFAULT_GAS_PCS: float = 11.0            # kWh/Smc upper heating value
DEFAULT_BOILER_EFFICIENCY: float = 0.80     # Illustrative compatibility parameter; explicit profile efficiency required
DEFAULT_HEATING_BALANCE_TEMP: float = 18.0  # °C outdoor balance temperature proxy
DEFAULT_INDOOR_COMFORT_TEMP: float = 18.0   # °C indoor thermostat comfort reference
DEFAULT_SETBACK_DELTA: float = 0.0          # °C off-hours setback reduction
DEFAULT_SCHEDULE_MODE: str = "continuous"   # Provisional future heat pump default
DEFAULT_TIMEZONE_MODE: str = "civil"        # Europe/Rome civil clock with DST
DEFAULT_SAVED_MORNING_WINDOW: Tuple[int, int] = (8, 11)   # Clock hours 08:00..10:59
DEFAULT_SAVED_EVENING_WINDOW: Tuple[int, int] = (16, 20)  # Clock hours 16:00..19:59


@dataclass(frozen=True)
class GasAllocationResult:
    """Gas bill allocation and inferred useful thermal reference budgets.

    Attributes:
        monthly_gas: 12-element monthly natural gas consumption (Smc).
        heating_smc: 12-element space heating gas consumption (Smc).
        dhw_smc: 12-element domestic hot water gas consumption (Smc).
        cooking_smc: 12-element cooking gas consumption (Smc).
        nonheat_smc: 12-element non-heating gas base (DHW + cooking, Smc).
        heating_useful_kwh: 12-element useful space heating thermal energy (kWh).
        dhw_gross_useful_kwh: 12-element gross useful DHW thermal energy (kWh).
        cooking_gas_kwh: 12-element cooking gas energy (kWh).
        annual_totals: Dict of annual sums for all components.
        metadata: Disclaimers, technical parameters, and modeling assumptions.
    """
    monthly_gas: np.ndarray
    heating_smc: np.ndarray
    dhw_smc: np.ndarray
    cooking_smc: np.ndarray
    nonheat_smc: np.ndarray
    heating_useful_kwh: np.ndarray
    dhw_gross_useful_kwh: np.ndarray
    cooking_gas_kwh: np.ndarray
    annual_totals: dict[str, float]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class HeatPumpDemandResult:
    """Useful space heating thermal demand profile and modeling diagnostics.

    Attributes:
        heat_demand_kwh: Useful space heating thermal demand array (kWh heat).
            Shape matches temperature input ((n_years, n_hours) or (n_hours,)).
            Never electricity or AC SCOP.
        monthly_heat_kwh: Monthly total useful heating demand (kWh heat).
            Shape (n_years, 12) or (12,).
        annual_heat_kwh: Annual total useful heating demand (kWh heat).
            Shape (n_years,) or float.
        gas_allocation: GasAllocationResult containing reference bills and thermal budgets.
        active_mask: Boolean mask indicating permitted heat pump operating hours.
        weather_ratios: Weather-scaling ratio per year and month.
            Shape (n_years, 12) or (12,). (All 1.0 when weather-sensitive mode is off).
        metadata: Operational schedule, weather assumptions, and physical limits.
    """
    heat_demand_kwh: np.ndarray
    monthly_heat_kwh: np.ndarray
    annual_heat_kwh: np.ndarray | float
    gas_allocation: GasAllocationResult | None
    active_mask: np.ndarray
    weather_ratios: np.ndarray
    metadata: dict[str, Any]
    comfort_ratios: np.ndarray | None = None


def allocate_gas_consumption(
    monthly_gas: Sequence[float] | np.ndarray,
    summer_gas_base: float = DEFAULT_SUMMER_GAS_BASE,
    cooking_smc_annual: float = DEFAULT_COOKING_SMC_ANNUAL,
    gas_pcs: float = DEFAULT_GAS_PCS,
    boiler_efficiency: float = DEFAULT_BOILER_EFFICIENCY,
) -> GasAllocationResult:
    """Allocate monthly gas bills into space heating, DHW, and cooking end-uses,
    and convert to useful thermal reference energy.

    Allocation rules (reconciling with engine):
      nonheat = min(month_gas, summer_gas_base)
      cooking = (nonheat / sum(nonheat)) * cooking_smc_annual
      dhw = nonheat - cooking
      heating = month_gas - nonheat
      useful_heat = allocated_smc * gas_pcs * boiler_efficiency

    Parameters:
        monthly_gas: 12-element sequence of monthly natural gas consumption (Smc).
        summer_gas_base: Monthly non-heating gas base cap (Smc/month).
        cooking_smc_annual: Annual gas allocated to cooking (Smc/year).
        gas_pcs: Gas upper heating value (kWh/Smc).
        boiler_efficiency: Baseline boiler seasonal efficiency (dimensionless fraction).

    Returns:
        GasAllocationResult with month-by-month and annual allocations in Smc and kWh heat.
    """
    gas_arr = np.asarray(monthly_gas, dtype=float)
    if gas_arr.ndim != 1 or len(gas_arr) != 12:
        raise ValueError(f"monthly_gas must be a 1D array of 12 elements, got shape {gas_arr.shape}")
    if not np.all(np.isfinite(gas_arr)):
        raise ValueError("monthly_gas must contain finite values")
    if np.any(gas_arr < 0.0):
        raise ValueError("monthly_gas values cannot be negative")

    if isinstance(summer_gas_base, bool) or not isinstance(summer_gas_base, (int, float)):
        raise TypeError("summer_gas_base must be numeric")
    summer_gas_base = float(summer_gas_base)
    if not math.isfinite(summer_gas_base) or summer_gas_base < 0.0:
        raise ValueError(f"summer_gas_base must be finite and >= 0, got {summer_gas_base}")

    if isinstance(cooking_smc_annual, bool) or not isinstance(cooking_smc_annual, (int, float)):
        raise TypeError("cooking_smc_annual must be numeric")
    cooking_smc_annual = float(cooking_smc_annual)
    if not math.isfinite(cooking_smc_annual) or cooking_smc_annual < 0.0:
        raise ValueError(f"cooking_smc_annual must be finite and >= 0, got {cooking_smc_annual}")

    if isinstance(gas_pcs, bool) or not isinstance(gas_pcs, (int, float)):
        raise TypeError("gas_pcs must be numeric")
    gas_pcs = float(gas_pcs)
    if not math.isfinite(gas_pcs) or gas_pcs <= 0.0:
        raise ValueError(f"gas_pcs must be finite and > 0, got {gas_pcs}")

    if isinstance(boiler_efficiency, bool) or not isinstance(boiler_efficiency, (int, float)):
        raise TypeError("boiler_efficiency must be numeric")
    boiler_efficiency = float(boiler_efficiency)
    if not math.isfinite(boiler_efficiency) or boiler_efficiency <= 0.0 or boiler_efficiency > 1.0:
        raise ValueError(f"boiler_efficiency must be in range (0, 1], got {boiler_efficiency}")

    # Allocation calculations
    nonheat = np.minimum(gas_arr, summer_gas_base)
    heating = gas_arr - nonheat
    total_nonheat = float(np.sum(nonheat))

    if cooking_smc_annual > total_nonheat:
        raise ValueError(
            f"cooking_smc_annual ({cooking_smc_annual:.2f} Smc) cannot exceed "
            f"total non-heating gas ({total_nonheat:.2f} Smc)"
        )

    if total_nonheat > 0.0:
        cooking = (nonheat / total_nonheat) * cooking_smc_annual
    else:
        cooking = np.zeros(12, dtype=float)

    dhw = nonheat - cooking

    # Validation: no negative shares
    if np.any(heating < -1e-9) or np.any(dhw < -1e-9) or np.any(cooking < -1e-9):
        raise ValueError("Gas allocation produced negative end-use shares")

    # Clean tiny numerical negative zeros
    heating = np.maximum(heating, 0.0)
    dhw = np.maximum(dhw, 0.0)
    cooking = np.maximum(cooking, 0.0)

    # Conversion to useful thermal energy (kWh)
    # Useful heat = allocated_smc * gas_pcs * boiler_efficiency
    heating_useful_kwh = heating * gas_pcs * boiler_efficiency
    dhw_gross_useful_kwh = dhw * gas_pcs * boiler_efficiency
    cooking_gas_kwh = cooking * gas_pcs

    annual_totals = {
        "monthly_gas_smc": float(np.sum(gas_arr)),
        "heating_smc": float(np.sum(heating)),
        "dhw_smc": float(np.sum(dhw)),
        "cooking_smc": float(np.sum(cooking)),
        "nonheat_smc": float(np.sum(nonheat)),
        "heating_useful_kwh": float(np.sum(heating_useful_kwh)),
        "dhw_gross_useful_kwh": float(np.sum(dhw_gross_useful_kwh)),
        "cooking_gas_kwh": float(np.sum(cooking_gas_kwh)),
    }

    metadata = {
        "reference_period": legacy_text("gas_reference_compact", "Explicit profile reference period"),
        "reference_total_smc": float(np.sum(gas_arr)),
        "summer_gas_base": summer_gas_base,
        "cooking_smc_annual": cooking_smc_annual,
        "gas_pcs": gas_pcs,
        "boiler_efficiency": boiler_efficiency,
        "efficiency_status": "UNVERIFIED USER ESTIMATE (supplied by user for 20-year-old boiler; not a measured or rated seasonal efficiency)",
        "cooking_status": "Cooking separate and not automatically electrified",
        "dhw_reference_note": "DHW useful heat is gas-inferred gross reference; later tank code must subtract editable prior-loss fraction before adding new tank losses",
    }

    return GasAllocationResult(
        monthly_gas=gas_arr,
        heating_smc=heating,
        dhw_smc=dhw,
        cooking_smc=cooking,
        nonheat_smc=nonheat,
        heating_useful_kwh=heating_useful_kwh,
        dhw_gross_useful_kwh=dhw_gross_useful_kwh,
        cooking_gas_kwh=cooking_gas_kwh,
        annual_totals=annual_totals,
        metadata=metadata,
    )


def window_hours(on_h: int, off_h: int) -> set[int]:
    """Active clock hours (0..23) for a start-inclusive, end-exclusive window.

    - If on_h == off_h: window is disabled (returns empty set).
    - If on_h < off_h: returns set(range(on_h, off_h)).
    - If on_h > off_h: wraps overnight: set(range(on_h, 24)) | set(range(0, off_h)).

    Parameters:
        on_h: Start clock hour (0..23, inclusive).
        off_h: Off clock hour (0..23, exclusive).

    Returns:
        Set of active integer clock hours in {0, ..., 23}.
    """
    if isinstance(on_h, bool) or not isinstance(on_h, (int, np.integer)):
        raise TypeError(f"on_hour must be integer, got {type(on_h).__name__}")
    if isinstance(off_h, bool) or not isinstance(off_h, (int, np.integer)):
        raise TypeError(f"off_hour must be integer, got {type(off_h).__name__}")
    on_h = int(on_h)
    off_h = int(off_h)
    if not (0 <= on_h <= 23):
        raise ValueError(f"on_hour must be in 0..23, got {on_h}")
    if not (0 <= off_h <= 23):
        raise ValueError(f"off_hour must be in 0..23, got {off_h}")

    if on_h == off_h:
        return set()
    if on_h < off_h:
        return set(range(on_h, off_h))
    return set(range(on_h, 24)) | set(range(0, off_h))


def build_active_heating_mask(
    hours: np.ndarray,
    civil_hours: np.ndarray,
    valid: np.ndarray,
    schedule_mode: str = DEFAULT_SCHEDULE_MODE,
    timezone_mode: str = DEFAULT_TIMEZONE_MODE,
    morning_window: Tuple[int, int] = DEFAULT_SAVED_MORNING_WINDOW,
    evening_window: Tuple[int, int] = DEFAULT_SAVED_EVENING_WINDOW,
) -> Tuple[np.ndarray, set[int]]:
    """Build boolean mask of active heat pump operating hours.

    Parameters:
        hours: Standard local hours (UTC+1, 0..23).
        civil_hours: Europe/Rome civil hours (0..23, reflecting DST transitions).
        valid: Boolean mask of valid calendar hours.
        schedule_mode: 'continuous' (24h) or 'scheduled' (window restricted).
        timezone_mode: 'civil' (Rome clock with DST) or 'standard' (fixed UTC+1).
        morning_window: Tuple (on_h, off_h) for morning operation.
        evening_window: Tuple (on_h, off_h) for afternoon/evening operation.

    Returns:
        Tuple of (active_mask, active_hours_set).
    """
    if schedule_mode not in ("continuous", "scheduled"):
        raise ValueError(f"Invalid schedule_mode: {schedule_mode}. Expected 'continuous' or 'scheduled'")
    if timezone_mode not in ("civil", "standard"):
        raise ValueError(f"Invalid timezone_mode: {timezone_mode}. Expected 'civil' or 'standard'")

    valid_arr = np.asarray(valid, dtype=bool)

    if schedule_mode == "continuous":
        active_hours = set(range(24))
        active_mask = np.copy(valid_arr)
        return active_mask, active_hours

    # Scheduled mode
    w_m = window_hours(morning_window[0], morning_window[1])
    w_e = window_hours(evening_window[0], evening_window[1])
    active_hours = w_m | w_e

    if len(active_hours) == 0:
        raise ValueError(
            "In scheduled mode, all heating windows are disabled. "
            "At least one operating hour must be active."
        )

    target_hours = np.asarray(civil_hours if timezone_mode == "civil" else hours)
    active_mask = valid_arr & np.isin(target_hours, list(active_hours))
    return active_mask, active_hours


def compute_reference_degree_hours(
    temperature: Sequence | np.ndarray,
    valid: Sequence | np.ndarray,
    months: Sequence | np.ndarray,
    balance_temp: float = DEFAULT_HEATING_BALANCE_TEMP,
) -> np.ndarray:
    """Compute baseline mean all-hours degree-hours per month (1..12) across a weather ensemble.

    Calculates sum(max(0, balance_temp - T)) over all valid hours of each month,
    averaged across years.

    Parameters:
        temperature: Dry-bulb temperatures (°C), shape (n_years, n_hours) or (n_hours,).
        valid: Boolean array of valid hourly slots, broadcastable to temperature.
        months: Integer array of calendar months (1..12), broadcastable to temperature.
        balance_temp: Outdoor balance temperature threshold (°C).

    Returns:
        1D array of shape (12,) containing mean monthly degree-hours.
    """
    if isinstance(balance_temp, bool) or not isinstance(balance_temp, (int, float)):
        raise TypeError("balance_temp must be numeric")
    balance_temp = float(balance_temp)
    if not math.isfinite(balance_temp) or not (0.0 <= balance_temp <= 35.0):
        raise ValueError(f"balance_temp must be in range [0, 35] °C, got {balance_temp}")

    temp_arr = np.asarray(temperature, dtype=float)
    if temp_arr.ndim == 1:
        temp_arr = temp_arr[None, :]
    elif temp_arr.ndim != 2:
        raise ValueError(f"temperature must be 1D or 2D array, got shape {temp_arr.shape}")

    n_years, n_hours = temp_arr.shape

    valid_arr = np.asarray(valid, dtype=bool)
    if valid_arr.ndim == 1:
        valid_arr = np.broadcast_to(valid_arr, temp_arr.shape)
    elif valid_arr.shape != temp_arr.shape:
        raise ValueError(f"valid shape {valid_arr.shape} does not match temperature {temp_arr.shape}")

    if not np.any(valid_arr):
        raise ValueError("valid array contains no valid hours")

    # Validate finite temperatures only at valid slots (padding may be NaN)
    if not np.all(np.isfinite(temp_arr[valid_arr])):
        raise ValueError("temperature contains non-finite values at valid slots")

    months_arr = np.asarray(months, dtype=int)
    if months_arr.ndim == 1:
        months_arr = np.broadcast_to(months_arr, temp_arr.shape)
    elif months_arr.shape != temp_arr.shape:
        raise ValueError(f"months shape {months_arr.shape} does not match temperature {temp_arr.shape}")

    # Validate month range only at valid slots (padding may be 0 or -1)
    valid_months = months_arr[valid_arr]
    if np.any(valid_months < 1) or np.any(valid_months > 12):
        raise ValueError("months at valid slots must be in range 1..12")

    dh = np.zeros((n_years, 12), dtype=float)
    for yi in range(n_years):
        for m in range(1, 13):
            mask = valid_arr[yi] & (months_arr[yi] == m)
            if np.any(mask):
                pos_deg = np.maximum(balance_temp - temp_arr[yi, mask], 0.0)
                dh[yi, m - 1] = float(np.sum(pos_deg))

    return np.mean(dh, axis=0)


def calculate_heat_demand(
    temperature: Sequence | np.ndarray,
    valid: Sequence | np.ndarray,
    months: Sequence | np.ndarray,
    hours: Sequence | np.ndarray,
    civil_hours: Sequence | np.ndarray,
    source_years: Sequence[Any] | None = None,
    monthly_gas: Sequence[float] | None = None,
    monthly_heat_budgets: Sequence[float] | None = None,
    summer_gas_base: float = DEFAULT_SUMMER_GAS_BASE,
    cooking_smc_annual: float = DEFAULT_COOKING_SMC_ANNUAL,
    gas_pcs: float = DEFAULT_GAS_PCS,
    boiler_efficiency: float = DEFAULT_BOILER_EFFICIENCY,
    heating_balance_temp: float = DEFAULT_HEATING_BALANCE_TEMP,
    indoor_temp: float = DEFAULT_INDOOR_COMFORT_TEMP,
    setback_delta: float = DEFAULT_SETBACK_DELTA,
    schedule_mode: str = DEFAULT_SCHEDULE_MODE,
    timezone_mode: str = DEFAULT_TIMEZONE_MODE,
    morning_window: Tuple[int, int] = DEFAULT_SAVED_MORNING_WINDOW,
    evening_window: Tuple[int, int] = DEFAULT_SAVED_EVENING_WINDOW,
    morning_on_hour: int | None = None,
    morning_off_hour: int | None = None,
    evening_on_hour: int | None = None,
    evening_off_hour: int | None = None,
    weather_sensitive: bool = False,
    reference_degree_hours: Sequence[float] | None = None,
    ensemble_temperature: np.ndarray | None = None,
    ensemble_valid: np.ndarray | None = None,
    ensemble_months: np.ndarray | None = None,
    comfort_sensitivity: bool | None = None,
) -> HeatPumpDemandResult:
    """Calculate hourly useful thermal space heating demand calibrated from natural gas consumption.

    Core methodology:
    1. Reference Budgets: Monthly useful heat budgets are derived from gas bills
       (heating_smc * gas_pcs * boiler_efficiency), or supplied explicitly as thermal kWh.
       Units are purely useful thermal energy (kWh heat delivered); never electricity or AC SCOP.
    2. Gating: Heating operation is gated to permitted windows (continuous 24h, or morning/evening
       windows with overnight wrapping and DST-aware civil or standard hours).
    3. Hourly Allocation: Within active hours, the monthly budget is distributed by positive
       outdoor degree-hours below effective balance temperature (falling back to uniform distribution
       if active hours have zero degree-hours).
    4. Optional Weather Sensitivity: If weather_sensitive is True, monthly budgets scale by
       the ratio of all-hours degree-hours in the given year to an explicit fixed reference ensemble
       denominator. Individual years are never renormalized back to the fixed budget.
    5. Comfort & Setback: Quasi-steady degree proxy shift (indoor_temp - 18°C) and off-hours setback
       adjust degree-hours relative to baseline 18°C comfort. Baseline settings strictly retain the
       reference gas budget without fabricating artificial thermal mass or startup recovery energy.

    Parameters:
        temperature: Dry-bulb outdoor air temperatures (°C). 1D (n_hours,) or 2D (n_years, n_hours).
        valid: Valid hourly slot mask. 1D or 2D, broadcastable to temperature.
        months: Calendar month (1..12). 1D or 2D, broadcastable to temperature.
        hours: Standard local hours (0..23). 1D or 2D, broadcastable to temperature.
        civil_hours: Europe/Rome civil hours (0..23). 1D or 2D, broadcastable to temperature.
        source_years: Optional list of year identifiers. Length must match n_years.
        monthly_gas: Explicit 12-element gas consumption (Smc); required unless useful heat is supplied.
        monthly_heat_budgets: Optional explicitly supplied useful thermal monthly budgets (kWh heat).
        summer_gas_base: Non-heating monthly gas base (Smc/month).
        cooking_smc_annual: Cooking gas allocation (Smc/year).
        gas_pcs: Gas upper heating value (kWh/Smc).
        boiler_efficiency: Existing boiler seasonal efficiency estimate.
        heating_balance_temp: Outdoor balance temperature (°C).
        indoor_temp: Indoor thermostat comfort reference (°C).
        setback_delta: Off-hours setback temperature reduction (°C).
        schedule_mode: 'continuous' or 'scheduled'.
        timezone_mode: 'civil' or 'standard'.
        morning_window: (on_hour, off_hour) for morning operation.
        evening_window: (on_hour, off_hour) for afternoon/evening operation.
        morning_on_hour, morning_off_hour: Overrides for morning window.
        evening_on_hour, evening_off_hour: Overrides for evening window.
        weather_sensitive: True to scale monthly budgets by degree-hours vs reference ensemble.
        reference_degree_hours: 12-element explicit denominator for weather scaling.
        ensemble_temperature, ensemble_valid, ensemble_months: Weather ensemble to compute
            denominator if reference_degree_hours is not explicitly supplied.
        comfort_sensitivity: Explicit toggle for comfort/setback sensitivity (defaults to weather_sensitive).

    Returns:
        HeatPumpDemandResult containing hourly useful heat demand arrays, monthly/annual totals,
        active mask, weather ratios, and engineering metadata.
    """
    # Parse morning and evening window overrides if supplied
    if morning_on_hour is not None or morning_off_hour is not None:
        m_on = morning_window[0] if morning_on_hour is None else morning_on_hour
        m_off = morning_window[1] if morning_off_hour is None else morning_off_hour
        morning_window = (m_on, m_off)
    if evening_on_hour is not None or evening_off_hour is not None:
        e_on = evening_window[0] if evening_on_hour is None else evening_on_hour
        e_off = evening_window[1] if evening_off_hour is None else evening_off_hour
        evening_window = (e_on, e_off)

    # Validate temperature array
    temp_arr = np.asarray(temperature, dtype=float)
    is_1d_input = (temp_arr.ndim == 1)
    if temp_arr.ndim == 1:
        temp_arr = temp_arr[None, :]
    elif temp_arr.ndim != 2:
        raise ValueError(f"temperature must be 1D or 2D array, got shape {temp_arr.shape}")

    n_years, n_hours = temp_arr.shape

    # Broadcast calendar arrays to 2D
    valid_arr = np.asarray(valid, dtype=bool)
    if valid_arr.ndim == 1:
        valid_arr = np.broadcast_to(valid_arr, temp_arr.shape)
    elif valid_arr.shape != temp_arr.shape:
        raise ValueError(f"valid shape {valid_arr.shape} does not match temperature {temp_arr.shape}")

    if not np.any(valid_arr):
        raise ValueError("valid array contains no valid hours")

    # Validate finite temperatures only at valid slots (padding may be NaN)
    if not np.all(np.isfinite(temp_arr[valid_arr])):
        raise ValueError("temperature contains non-finite values at valid slots")

    months_arr = np.asarray(months, dtype=int)
    if months_arr.ndim == 1:
        months_arr = np.broadcast_to(months_arr, temp_arr.shape)
    elif months_arr.shape != temp_arr.shape:
        raise ValueError(f"months shape {months_arr.shape} does not match temperature {temp_arr.shape}")

    # Validate month range only at valid slots (padding may be 0 or -1)
    valid_months = months_arr[valid_arr]
    if np.any(valid_months < 1) or np.any(valid_months > 12):
        raise ValueError("months at valid slots must be in range 1..12")

    hours_arr = np.asarray(hours, dtype=int)
    if hours_arr.ndim == 1:
        hours_arr = np.broadcast_to(hours_arr, temp_arr.shape)
    elif hours_arr.shape != temp_arr.shape:
        raise ValueError(f"hours shape {hours_arr.shape} does not match temperature {temp_arr.shape}")

    # Validate hours range only at valid slots (padding may be 0 or -1)
    valid_hours = hours_arr[valid_arr]
    if np.any(valid_hours < 0) or np.any(valid_hours > 23):
        raise ValueError("hours at valid slots must be in range 0..23")

    civil_arr = np.asarray(civil_hours, dtype=int)
    if civil_arr.ndim == 1:
        civil_arr = np.broadcast_to(civil_arr, temp_arr.shape)
    elif civil_arr.shape != temp_arr.shape:
        raise ValueError(f"civil_hours shape {civil_arr.shape} does not match temperature {temp_arr.shape}")

    # Validate civil_hours range only at valid slots (padding may be 0 or -1)
    valid_civil = civil_arr[valid_arr]
    if np.any(valid_civil < 0) or np.any(valid_civil > 23):
        raise ValueError("civil_hours at valid slots must be in range 0..23")

    # Validate source years
    if source_years is None:
        source_year_ids = [f"Year_{i}" for i in range(n_years)]
    else:
        source_year_ids = list(source_years)
        if len(source_year_ids) != n_years:
            raise ValueError(
                f"Length of source_years ({len(source_year_ids)}) must match n_years ({n_years})"
            )

    # Validate numeric parameters
    if isinstance(heating_balance_temp, bool) or not isinstance(heating_balance_temp, (int, float)):
        raise TypeError("heating_balance_temp must be numeric")
    heating_balance_temp = float(heating_balance_temp)
    if not math.isfinite(heating_balance_temp) or not (0.0 <= heating_balance_temp <= 35.0):
        raise ValueError(f"heating_balance_temp must be in range [0, 35] °C, got {heating_balance_temp}")

    if isinstance(indoor_temp, bool) or not isinstance(indoor_temp, (int, float)):
        raise TypeError("indoor_temp must be numeric")
    indoor_temp = float(indoor_temp)
    if not math.isfinite(indoor_temp) or not (10.0 <= indoor_temp <= 30.0):
        raise ValueError(f"indoor_temp must be in range [10, 30] °C, got {indoor_temp}")

    if isinstance(setback_delta, bool) or not isinstance(setback_delta, (int, float)):
        raise TypeError("setback_delta must be numeric")
    setback_delta = float(setback_delta)
    if not math.isfinite(setback_delta) or setback_delta < 0.0 or setback_delta > 15.0:
        raise ValueError(f"setback_delta must be in range [0, 15] °C, got {setback_delta}")

    # Explicit carrier input or useful heat; never inherit a household vector.
    if monthly_gas is None and monthly_heat_budgets is None:
        raise ValueError('Supply explicit monthly_gas or monthly_heat_budgets')
    gas_alloc = None if monthly_gas is None else allocate_gas_consumption(
        monthly_gas=monthly_gas,
        summer_gas_base=summer_gas_base,
        cooking_smc_annual=cooking_smc_annual,
        gas_pcs=gas_pcs,
        boiler_efficiency=boiler_efficiency,
    )

    if monthly_heat_budgets is not None:
        budgets_arr = np.asarray(monthly_heat_budgets, dtype=float)
        if budgets_arr.ndim != 1 or len(budgets_arr) != 12:
            raise ValueError("monthly_heat_budgets must have length 12")
        if not np.all(np.isfinite(budgets_arr)) or np.any(budgets_arr < 0.0):
            raise ValueError("monthly_heat_budgets must be finite and non-negative")
        ref_monthly_heat_kwh = budgets_arr
    else:
        ref_monthly_heat_kwh = gas_alloc.heating_useful_kwh

    # Step 2: Build active heating operating mask
    active_mask, active_hours = build_active_heating_mask(
        hours=hours_arr,
        civil_hours=civil_arr,
        valid=valid_arr,
        schedule_mode=schedule_mode,
        timezone_mode=timezone_mode,
        morning_window=morning_window,
        evening_window=evening_window,
    )

    # Step 3: Quasi-steady degree shift and effective balance temperatures
    comfort_shift = indoor_temp - 18.0
    active_balance_temp = heating_balance_temp + comfort_shift
    off_balance_temp = heating_balance_temp + comfort_shift - setback_delta

    # Step 4: Orthogonal weather and comfort/setback sensitivity
    if comfort_sensitivity is None:
        is_comfort_changed = (
            not math.isclose(indoor_temp, DEFAULT_INDOOR_COMFORT_TEMP, abs_tol=1e-9)
            or not math.isclose(setback_delta, DEFAULT_SETBACK_DELTA, abs_tol=1e-9)
        )
        comfort_sensitivity = is_comfort_changed
    else:
        comfort_sensitivity = bool(comfort_sensitivity)

    if weather_sensitive:
        if reference_degree_hours is not None:
            dh_ref = np.asarray(reference_degree_hours, dtype=float)
            if dh_ref.ndim != 1 or len(dh_ref) != 12:
                raise ValueError("reference_degree_hours must be a 1D sequence/array of 12 elements")
            if not np.all(np.isfinite(dh_ref)) or np.any(dh_ref < 0.0):
                raise ValueError("reference_degree_hours must be finite and non-negative")
        elif (
            ensemble_temperature is not None
            and ensemble_valid is not None
            and ensemble_months is not None
        ):
            dh_ref = compute_reference_degree_hours(
                ensemble_temperature,
                ensemble_valid,
                ensemble_months,
                balance_temp=heating_balance_temp,
            )
        else:
            raise ValueError(
                "In weather_sensitive mode, explicit reference_degree_hours or fully supplied "
                "(ensemble_temperature, ensemble_valid, ensemble_months) must be provided."
            )

        # Validate zero reference for positive relevant budget explicitly
        for m in range(1, 13):
            if ref_monthly_heat_kwh[m - 1] > 0.0 and dh_ref[m - 1] <= 0.0:
                raise ValueError(
                    f"Zero reference degree-hours in month {m} ({dh_ref[m - 1]:.2f}) for positive "
                    f"heating budget ({ref_monthly_heat_kwh[m - 1]:.2f} kWh)"
                )

    weather_ratios = np.ones((n_years, 12), dtype=float)
    comfort_ratios = np.ones((n_years, 12), dtype=float)
    monthly_budgets_yi = np.zeros((n_years, 12), dtype=float)

    for yi in range(n_years):
        for m in range(1, 13):
            m_mask = valid_arr[yi] & (months_arr[yi] == m)
            if not np.any(m_mask):
                if ref_monthly_heat_kwh[m - 1] > 0.0:
                    raise ValueError(
                        f"No valid hours in month {m} for weather year '{source_year_ids[yi]}' "
                        f"with positive heating demand {ref_monthly_heat_kwh[m - 1]:.2f} kWh"
                    )
                weather_ratios[yi, m - 1] = 1.0
                comfort_ratios[yi, m - 1] = 1.0
                monthly_budgets_yi[yi, m - 1] = 0.0
                continue

            # Current baseline all-hours degree at selected heating_balance_temp
            deg_base = np.maximum(heating_balance_temp - temp_arr[yi, m_mask], 0.0)
            dh_base = float(np.sum(deg_base))

            # Effective current weather degree including indoor-18 shift and optional off-hours setback
            act_slots = m_mask & active_mask[yi]
            off_slots = m_mask & (~active_mask[yi])
            deg_act = np.maximum(active_balance_temp - temp_arr[yi, act_slots], 0.0)
            deg_off = np.maximum(off_balance_temp - temp_arr[yi, off_slots], 0.0)
            dh_effective = float(np.sum(deg_act) + np.sum(deg_off))

            # Weather ratio: current baseline degree / reference fixed ensemble degree
            if weather_sensitive:
                ref_val = float(dh_ref[m - 1])
                if ref_val > 0.0:
                    weather_ratios[yi, m - 1] = dh_base / ref_val
                else:
                    weather_ratios[yi, m - 1] = 0.0
            else:
                weather_ratios[yi, m - 1] = 1.0

            # Comfort ratio: effective current degree / current baseline degree
            if comfort_sensitivity:
                if dh_base > 0.0:
                    comfort_ratios[yi, m - 1] = dh_effective / dh_base
                else:
                    if ref_monthly_heat_kwh[m - 1] > 0.0:
                        raise ValueError(
                            f"Zero baseline degree-hours in month {m} for positive heating budget "
                            f"({ref_monthly_heat_kwh[m - 1]:.2f} kWh)"
                        )
                    comfort_ratios[yi, m - 1] = 1.0
            else:
                comfort_ratios[yi, m - 1] = 1.0

            monthly_budgets_yi[yi, m - 1] = (
                ref_monthly_heat_kwh[m - 1]
                * weather_ratios[yi, m - 1]
                * comfort_ratios[yi, m - 1]
            )

    # Step 6: Distribute monthly budgets across active hours by positive degree-hours
    heat_demand = np.zeros_like(temp_arr, dtype=float)

    for m in range(1, 13):
        for yi in range(n_years):
            budget = monthly_budgets_yi[yi, m - 1]
            if budget <= 0.0:
                continue

            m_mask = valid_arr[yi] & (months_arr[yi] == m)
            act_mask = m_mask & active_mask[yi]

            if not np.any(act_mask):
                raise ValueError(
                    f"No active valid heating hours in month {m} for weather year "
                    f"'{source_year_ids[yi]}' with positive heating demand {budget:.2f} kWh"
                )

            # Positive outdoor degree-hours below active_balance_temp (active proxy)
            pos_deg_act = np.maximum(active_balance_temp - temp_arr[yi, act_mask], 0.0)
            deg_sum = float(np.sum(pos_deg_act))

            if deg_sum == 0.0:
                # Fallback uniform distribution within active hours
                pattern_act = 1.0 / float(np.sum(act_mask))
            else:
                pattern_act = pos_deg_act / deg_sum

            heat_demand[yi, act_mask] += pattern_act * budget

    # Step 7: Calculate monthly and annual aggregated totals
    monthly_heat_kwh = np.zeros((n_years, 12), dtype=float)
    for yi in range(n_years):
        for m in range(1, 13):
            m_mask = valid_arr[yi] & (months_arr[yi] == m)
            monthly_heat_kwh[yi, m - 1] = float(np.sum(heat_demand[yi, m_mask]))

    annual_heat_kwh = np.sum(monthly_heat_kwh, axis=1)

    # Step 8: Build metadata and disclaimers
    numeric_years = []
    if source_years is not None:
        for y in source_year_ids:
            s = str(y).strip()
            if s.startswith("Analogue_"):
                s = s[len("Analogue_"):]
            elif s.startswith("Year_"):
                s = s[len("Year_"):]
            if s.isdigit():
                val = int(s)
                if 1900 <= val <= 2100:
                    numeric_years.append(val)

    if numeric_years and len(numeric_years) == len(source_year_ids):
        if len(numeric_years) > 1:
            clim_period = f"{min(numeric_years)}–{max(numeric_years)}"
        else:
            clim_period = str(numeric_years[0])
    else:
        clim_period = f"{n_years}-year historical" if n_years > 1 else "1-year historical"

    metadata = {
        "schedule_mode": schedule_mode,
        "timezone_mode": timezone_mode,
        "active_hours_daily": sorted(list(active_hours)),
        "heating_balance_temp": heating_balance_temp,
        "indoor_comfort_temp": indoor_temp,
        "comfort_shift": comfort_shift,
        "setback_delta": setback_delta,
        "active_balance_temp": active_balance_temp,
        "off_balance_temp": off_balance_temp,
        "weather_sensitive": weather_sensitive,
        "comfort_sensitivity": comfort_sensitivity,
        "gas_pcs": gas_pcs,
        "boiler_efficiency": boiler_efficiency,
        "boiler_efficiency_status": "Unverified modeling assumption; not a measured or rated seasonal efficiency",
        "cooking_status": "Cooking separate and not automatically electrified",
        "dhw_reference_note": "DHW useful heat is gas-inferred gross reference; later tank code must subtract editable prior-loss fraction before adding new tank losses",
        "climatology_proxy_label": f"Historical climatology PROXY ({clim_period} paired ensemble); {legacy_text('climatology_reference_suffix', 'no coincident measured weather is implied; cannot claim measured-weather-calibrated UA.')}",
        "unresolved_dynamics_disclaimer": "Monthly bills cannot identify building thermal mass, dynamic heat-loss coefficient (UA), or guarantee comfort/recovery peaks. No RC network or fabricated thermal storage modeled; radiator cooldown and building thermal inertia remain unresolved. No continued electric load is implied while off. Thermostat recovery dynamics omitted.",
        "no_warming_offset_applied": True,
        "recovery_energy_added": 0.0,
    }

    if gas_alloc is None:
        metadata.update(baseline_carrier='useful_heat', gas_pcs=None, boiler_efficiency=None,
                        boiler_efficiency_status='not applicable',
                        dhw_reference_note='Explicit useful heat; no gas allocation',
                        climatology_proxy_label=f'Historical climatology PROXY ({clim_period}); thermal coverage retained by caller')
    else:
        metadata['baseline_carrier'] = 'gas'

    # Step 9: Format output according to input dimensionality
    if is_1d_input:
        return HeatPumpDemandResult(
            heat_demand_kwh=heat_demand[0],
            monthly_heat_kwh=monthly_heat_kwh[0],
            annual_heat_kwh=float(annual_heat_kwh[0]),
            gas_allocation=gas_alloc,
            active_mask=active_mask[0],
            weather_ratios=weather_ratios[0],
            metadata=metadata,
            comfort_ratios=comfort_ratios[0],
        )

    return HeatPumpDemandResult(
        heat_demand_kwh=heat_demand,
        monthly_heat_kwh=monthly_heat_kwh,
        annual_heat_kwh=annual_heat_kwh,
        gas_allocation=gas_alloc,
        active_mask=active_mask,
        weather_ratios=weather_ratios,
        metadata=metadata,
        comfort_ratios=comfort_ratios,
    )
