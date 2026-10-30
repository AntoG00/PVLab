"""Domestic Hot Water (DHW) storage tank component and thermal demand modeling.

Stage 3A implementation providing:
1. Fully configurable well-mixed DHW cylinder thermal storage model using absolute stored sensible
   energy (C * T) to avoid phantom heat on inlet temperature variations.
2. Single-step simulation API with optional substeps (<=15 min), exact energy conservation,
   standing loss with no ambient gain policy, temperature-qualified service tracking (>=40°C),
   and thermostat deadband latching.
3. Hourly DHW thermal draw demand helper supporting gas-calibrated monthly gross heat (with editable
   prior loss subtraction) and configurable litres/day, customizable morning/evening timing windows,
   equal-hour disables, overnight wrap, and exact monthly budget normalization across 1D or 2D arrays.

Order of Operations & Physical Modeling Policy:
- Order within substep: Draw demand is applied first, extracting stored heat above inlet temperature;
  standing thermal loss to ambient is deducted second; external reheat heat is accepted third,
  bounded strictly by active target or hygiene temperature.
- Service qualification: Hot water delivered at or above min_service_temp_c (default 40°C) is credited
  as temperature-qualified served heat. Lukewarm draw extracted below min_service_temp_c is removed
  from sensible storage down to inlet, but never credited toward served service.
- Standing loss / No ambient gain: Standing losses cool the tank toward ambient when T_tank > T_amb.
  Under the no-ambient-gain policy, standing loss is strictly non-negative (never heats tank when T_tank < T_amb).
- Control interface: Component exposes remaining heat required and thermostat latch for external
  supervisory controllers; it does not allocate compressor capacity or backup heat on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Sequence, Tuple

import numpy as np


WATER_HEAT_CAPACITY_KWH_PER_L_K: float = 0.001163  # 4186 J/(kg*K) / 3.6e6 J/kWh
DEFAULT_VOLUME_L: float = 200.0
DEFAULT_TARGET_TEMP_C: float = 50.0
DEFAULT_HYGIENE_TEMP_C: float = 60.0
DEFAULT_INLET_TEMP_C: float = 15.0
DEFAULT_AMBIENT_TEMP_C: float = 20.0
DEFAULT_STANDING_LOSS_W_PER_K: float = 1.0
DEFAULT_DEADBAND_K: float = 5.0
DEFAULT_MIN_SERVICE_TEMP_C: float = 40.0
DEFAULT_INITIAL_TEMP_C: float = 50.0
DAYS_IN_MONTH: Tuple[int, ...] = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _validate_finite_float(val: Any, name: str, min_val: float | None = None, max_val: float | None = None) -> float:
    if isinstance(val, bool) or not isinstance(val, (int, float, np.floating)):
        raise TypeError(f"{name} must be a float, got {type(val).__name__}")
    val_f = float(val)
    if not math.isfinite(val_f):
        raise ValueError(f"{name} must be a finite float, got {val_f}")
    if min_val is not None and val_f < min_val:
        raise ValueError(f"{name} must be >= {min_val}, got {val_f}")
    if max_val is not None and val_f > max_val:
        raise ValueError(f"{name} must be <= {max_val}, got {val_f}")
    return val_f


@dataclass(frozen=True)
class DHWTankStepResult:
    """Detailed accounting of a DHW tank simulation step."""
    start_storage_kwh: float; end_storage_kwh: float
    start_temp_c: float; end_temp_c: float
    requested_draw_heat_kwh: float; actual_draw_heat_kwh: float
    served_service_heat_kwh: float; unmet_service_heat_kwh: float
    standing_loss_kwh: float; heat_supplied_kwh: float
    accepted_heat_kwh: float; rejected_heat_kwh: float
    thermostat_latch: bool; remaining_heat_kwh: float


class DHWTank:
    """Well-mixed Domestic Hot Water storage cylinder model."""

    def __init__(
        self, volume_litres: float = DEFAULT_VOLUME_L, target_temp_c: float = DEFAULT_TARGET_TEMP_C,
        inlet_temp_c: float = DEFAULT_INLET_TEMP_C, ambient_temp_c: float = DEFAULT_AMBIENT_TEMP_C,
        standing_loss_w_per_k: float = DEFAULT_STANDING_LOSS_W_PER_K, deadband_k: float = DEFAULT_DEADBAND_K,
        min_service_temp_c: float = DEFAULT_MIN_SERVICE_TEMP_C, initial_temp_c: float | None = None,
        hygiene_temp_c: float = DEFAULT_HYGIENE_TEMP_C, thermostat_latch: bool | None = None,
    ) -> None:
        self.volume_litres = _validate_finite_float(volume_litres, "volume_litres", min_val=1e-3)
        self.target_temp_c = _validate_finite_float(target_temp_c, "target_temp_c", min_val=0.0, max_val=100.0)
        self.inlet_temp_c = _validate_finite_float(inlet_temp_c, "inlet_temp_c", min_val=0.0, max_val=100.0)
        self.ambient_temp_c = _validate_finite_float(ambient_temp_c, "ambient_temp_c", min_val=-50.0, max_val=70.0)
        self.standing_loss_w_per_k = _validate_finite_float(standing_loss_w_per_k, "standing_loss_w_per_k", min_val=0.0)
        self.deadband_k = _validate_finite_float(deadband_k, "deadband_k", min_val=1e-3)
        self.min_service_temp_c = _validate_finite_float(min_service_temp_c, "min_service_temp_c", min_val=0.0, max_val=100.0)
        self.hygiene_temp_c = _validate_finite_float(hygiene_temp_c, "hygiene_temp_c", min_val=self.target_temp_c, max_val=100.0)

        if self.min_service_temp_c > self.target_temp_c:
            raise ValueError(f"min_service_temp_c ({self.min_service_temp_c}) cannot exceed target_temp_c ({self.target_temp_c})")
        if self.inlet_temp_c > self.min_service_temp_c:
            raise ValueError(f"inlet_temp_c ({self.inlet_temp_c}) cannot exceed min_service_temp_c ({self.min_service_temp_c})")

        init_t = self.target_temp_c if initial_temp_c is None else initial_temp_c
        self.temp_c = _validate_finite_float(init_t, "initial_temp_c", min_val=0.0, max_val=100.0)

        if thermostat_latch is None:
            self.thermostat_latch = self.temp_c <= (self.target_temp_c - self.deadband_k)
        elif isinstance(thermostat_latch, bool):
            self.thermostat_latch = thermostat_latch
        else:
            raise TypeError(f"thermostat_latch must be boolean, got {type(thermostat_latch).__name__}")

    @property
    def heat_capacity_kwh_per_k(self) -> float:
        return self.volume_litres * WATER_HEAT_CAPACITY_KWH_PER_L_K

    @property
    def storage_kwh(self) -> float:
        return self.heat_capacity_kwh_per_k * self.temp_c

    def remaining_heat_required(self, target_temp_c: float | None = None) -> float:
        tgt = self.target_temp_c if target_temp_c is None else _validate_finite_float(target_temp_c, "target_temp_c", min_val=0.0, max_val=100.0)
        if tgt < self.min_service_temp_c:
            raise ValueError(f"target_temp_c ({tgt}) cannot be lower than min_service_temp_c ({self.min_service_temp_c})")
        return self.heat_capacity_kwh_per_k * max(0.0, tgt - self.temp_c)

    def step(
        self,
        duration_hours: float = 1.0,
        draw_demand_kwh: float = 0.0,
        heat_supplied_kwh: float = 0.0,
        target_temp_c: float | None = None,
        inlet_temp_c: float | None = None,
        ambient_temp_c: float | None = None,
        substeps: int | None = None,
        max_substep_minutes: float = 15.0,
    ) -> DHWTankStepResult:
        dur = _validate_finite_float(duration_hours, "duration_hours", min_val=0.0)
        draw_req = _validate_finite_float(draw_demand_kwh, "draw_demand_kwh", min_val=0.0)
        h_sup = _validate_finite_float(heat_supplied_kwh, "heat_supplied_kwh", min_val=0.0)
        tgt = self.target_temp_c if target_temp_c is None else _validate_finite_float(target_temp_c, "target_temp_c", min_val=0.0, max_val=100.0)
        inlet = self.inlet_temp_c if inlet_temp_c is None else _validate_finite_float(inlet_temp_c, "inlet_temp_c", min_val=0.0, max_val=100.0)
        amb = self.ambient_temp_c if ambient_temp_c is None else _validate_finite_float(ambient_temp_c, "ambient_temp_c", min_val=-50.0, max_val=70.0)

        if inlet > self.min_service_temp_c:
            raise ValueError(f"inlet_temp_c ({inlet}) cannot exceed min_service_temp_c ({self.min_service_temp_c})")
        if tgt < self.min_service_temp_c:
            raise ValueError(f"target_temp_c ({tgt}) cannot be lower than min_service_temp_c ({self.min_service_temp_c})")
        if tgt > self.hygiene_temp_c:
            raise ValueError(f"target_temp_c ({tgt}) cannot exceed hygiene_temp_c ({self.hygiene_temp_c})")

        if substeps is None:
            max_sub_m = _validate_finite_float(max_substep_minutes, "max_substep_minutes", min_val=1e-3)
            n_sub = 1 if dur <= 0.0 else max(1, math.ceil(dur * 60.0 / max_sub_m))
        else:
            if isinstance(substeps, bool) or not isinstance(substeps, (int, np.integer)):
                raise TypeError(f"substeps must be an integer, got {type(substeps).__name__}")
            if substeps < 1:
                raise ValueError(f"substeps must be >= 1, got {substeps}")
            n_sub = int(substeps)

        c = self.heat_capacity_kwh_per_k
        start_t = self.temp_c
        start_e = c * start_t

        if dur <= 0.0:
            rem = c * max(0.0, tgt - start_t)
            return DHWTankStepResult(
                start_storage_kwh=start_e, end_storage_kwh=start_e, start_temp_c=start_t, end_temp_c=start_t,
                requested_draw_heat_kwh=draw_req, actual_draw_heat_kwh=0.0, served_service_heat_kwh=0.0,
                unmet_service_heat_kwh=draw_req, standing_loss_kwh=0.0, heat_supplied_kwh=h_sup,
                accepted_heat_kwh=0.0, rejected_heat_kwh=h_sup, thermostat_latch=self.thermostat_latch,
                remaining_heat_kwh=rem,
            )

        dt = dur / n_sub
        sub_draw, sub_sup = draw_req / n_sub, h_sup / n_sub
        t, latch = start_t, self.thermostat_latch
        tot_actual_draw = tot_served = tot_loss = tot_accepted = tot_rejected = 0.0

        for _ in range(n_sub):
            # 1. Water Draw
            q_avail_inlet = c * max(0.0, t - inlet)
            q_draw = min(sub_draw, q_avail_inlet)
            if t >= self.min_service_temp_c:
                q_avail_serv = c * max(0.0, t - self.min_service_temp_c)
                q_serv = min(sub_draw, q_avail_serv)
            else:
                q_serv = 0.0
            t -= (q_draw / c)
            tot_actual_draw += q_draw
            tot_served += q_serv

            # 2. Standing Loss (no ambient gain policy)
            if t > amb:
                p_loss_kw = (self.standing_loss_w_per_k / 1000.0) * (t - amb)
                q_loss = min(p_loss_kw * dt, c * (t - amb))
            else:
                q_loss = 0.0
            t -= (q_loss / c)
            tot_loss += q_loss

            # 3. Heat Supplied (bounded to target)
            q_room = c * max(0.0, tgt - t)
            q_acc = min(sub_sup, q_room)
            q_rej = sub_sup - q_acc
            t += (q_acc / c)
            tot_accepted += q_acc
            tot_rejected += q_rej

            # 4. Thermostat deadband latch
            if t <= (tgt - self.deadband_k):
                latch = True
            elif t >= tgt:
                latch = False

        self.temp_c, self.thermostat_latch = t, latch
        end_e = c * t
        unmet = max(0.0, draw_req - tot_served)
        rem = c * max(0.0, tgt - t)

        return DHWTankStepResult(
            start_storage_kwh=start_e, end_storage_kwh=end_e, start_temp_c=start_t, end_temp_c=t,
            requested_draw_heat_kwh=draw_req, actual_draw_heat_kwh=tot_actual_draw,
            served_service_heat_kwh=tot_served, unmet_service_heat_kwh=unmet,
            standing_loss_kwh=tot_loss, heat_supplied_kwh=h_sup, accepted_heat_kwh=tot_accepted,
            rejected_heat_kwh=tot_rejected, thermostat_latch=latch, remaining_heat_kwh=rem,
        )


def window_hours(on_h: int, off_h: int) -> set[int]:
    """Active clock hours (0..23) for start-inclusive, end-exclusive window with wrap and equal disable."""
    if isinstance(on_h, bool) or not isinstance(on_h, (int, np.integer)):
        raise TypeError(f"on_h must be integer, got {type(on_h).__name__}")
    if isinstance(off_h, bool) or not isinstance(off_h, (int, np.integer)):
        raise TypeError(f"off_h must be integer, got {type(off_h).__name__}")
    on_i, off_i = int(on_h), int(off_h)
    if not (0 <= on_i <= 23):
        raise ValueError(f"on_h must be in 0..23, got {on_i}")
    if not (0 <= off_i <= 23):
        raise ValueError(f"off_h must be in 0..23, got {off_i}")
    if on_i == off_i:
        return set()
    if on_i < off_i:
        return set(range(on_i, off_i))
    return set(range(on_i, 24)) | set(range(0, off_i))


def build_dhw_timing_weights(
    morning_window: Tuple[int, int] = (7, 9),
    evening_window: Tuple[int, int] = (18, 21),
    morning_fraction: float = 0.5,
    evening_fraction: float = 0.5,
) -> np.ndarray:
    """Build normalized 24-hour DHW draw timing weight profile."""
    f_m = _validate_finite_float(morning_fraction, "morning_fraction", min_val=0.0, max_val=1.0)
    f_e = _validate_finite_float(evening_fraction, "evening_fraction", min_val=0.0, max_val=1.0)
    if (f_m + f_e) <= 0.0:
        raise ValueError("Sum of morning_fraction and evening_fraction must be > 0")

    w_m = window_hours(morning_window[0], morning_window[1])
    w_e = window_hours(evening_window[0], evening_window[1])
    if len(w_m) == 0 and len(w_e) == 0:
        raise ValueError("All DHW timing windows are disabled. At least one window must have active hours.")

    weights = np.zeros(24, dtype=float)
    if len(w_m) > 0 and len(w_e) > 0:
        for h in w_m:
            weights[h] += f_m / len(w_m)
        for h in w_e:
            weights[h] += f_e / len(w_e)
    elif len(w_m) > 0:
        for h in w_m:
            weights[h] = 1.0 / len(w_m)
    else:
        for h in w_e:
            weights[h] = 1.0 / len(w_e)

    total_w = np.sum(weights)
    if total_w > 0.0:
        weights /= total_w
    return weights


def calculate_daily_dhw_heat_from_litres(
    litres_per_day: float,
    service_temp_c: float = DEFAULT_MIN_SERVICE_TEMP_C,
    inlet_temp_c: float = DEFAULT_INLET_TEMP_C,
) -> float:
    """Calculate daily useful DHW thermal demand in kWh via C * dT formula."""
    litres = _validate_finite_float(litres_per_day, "litres_per_day", min_val=0.0)
    t_serv = _validate_finite_float(service_temp_c, "service_temp_c", min_val=0.0, max_val=100.0)
    t_inlet = _validate_finite_float(inlet_temp_c, "inlet_temp_c", min_val=0.0, max_val=100.0)
    if t_serv < t_inlet:
        raise ValueError(f"service_temp_c ({t_serv}) cannot be lower than inlet_temp_c ({t_inlet})")
    return litres * WATER_HEAT_CAPACITY_KWH_PER_L_K * (t_serv - t_inlet)


def build_hourly_dhw_demand(
    valid: Sequence[bool] | np.ndarray,
    months: Sequence[int] | np.ndarray,
    hours: Sequence[int] | np.ndarray,
    *,
    monthly_dhw_kwh: Sequence[float] | np.ndarray | None = None,
    litres_per_day: float | None = None,
    service_temp_c: float = DEFAULT_MIN_SERVICE_TEMP_C,
    inlet_temp_c: float = DEFAULT_INLET_TEMP_C,
    prior_loss_fraction: float = 0.0,
    morning_window: Tuple[int, int] = (7, 9),
    evening_window: Tuple[int, int] = (18, 21),
    morning_fraction: float = 0.5,
    evening_fraction: float = 0.5,
) -> np.ndarray:
    """Distribute gross gas-calibrated or litre-specified DHW demand into hourly draw demand.

    DHW Demand Calculation Modes:
    1. Gas-Calibrated Mode ('monthly_dhw_kwh' provided):
       - Accepts 12 gross monthly heat totals in kWh.
       - Deducts prior gas losses via 'prior_loss_fraction':
           net_monthly_budget = monthly_dhw_kwh * (1.0 - prior_loss_fraction)
       - Preserves exact gas-derived monthly budgets independent of leap day count,
         calendar duration, or partial valid interval coverage. The full net monthly
         budget is distributed across active draw timing windows in that month.
       - A prior_loss_fraction of 1.0 is permitted (e.g. limiting sensitivity where all
         inferred gas consumption was standing loss), resulting in zero net demand.

    2. Litre-Specified Mode ('litres_per_day' provided):
       - Computes useful daily thermal demand via C * dT:
           daily_kwh = litres_per_day * C_water * (service_temp_c - inlet_temp_c)
       - Prior gas losses ('prior_loss_fraction') DO NOT apply to litre mode; litres
         directly represent requested end-use hot water volume.
       - Monthly demand is determined by actual valid interval coverage:
           month_demand_kwh = daily_kwh * (n_valid_intervals / 24.0)
       - Continuous full calendar: Leap February (29 days = 696 intervals) naturally yields
         29 days of demand (e.g. 100 L/day, 40°C service, 15°C inlet -> 84.3175 kWh),
         31-day months yield 31 days, and 28-day Februaries yield 28 days.
       - Partial-data coverage: If only a subset of days/intervals is valid, demand scales
         strictly with the actual valid interval coverage (coverage / 24) and is NEVER
         normalized back to a fixed 28/30/31-day monthly budget.
       - DST transitions: Daylight saving transitions (e.g. 23-hour or 25-hour days) naturally
         scale demand by the actual count of valid intervals divided by 24.

    Broadcasting & Validation:
    - Supports calendar broadcasting: 1D calendar arrays ('months', 'hours' of shape (N,))
      broadcast across 2D scenario/forecast ensembles ('valid' of shape (Y, N)).
    - Validates month (1..12) and hour (0..23) ranges strictly at valid slots (where valid is True).
      Padding sentinels (-1, 0, etc.) at invalid slots (where valid is False) are ignored.
    - If a positive demand budget cannot be allocated because no valid slots fall within
      active timing windows in that month, raises ValueError rather than silently dropping demand.
      Zero-demand cases (0 litres, 0 gas kWh, or prior_loss_fraction=1.0) allocate zero demand cleanly.
    """
    if (monthly_dhw_kwh is None) == (litres_per_day is None):
        raise ValueError("Must provide exactly one of 'monthly_dhw_kwh' or 'litres_per_day'")

    valid_arr = np.asarray(valid, dtype=bool)
    months_arr = np.asarray(months, dtype=int)
    hours_arr = np.asarray(hours, dtype=int)

    if valid_arr.ndim == 0 or months_arr.ndim == 0 or hours_arr.ndim == 0:
        raise ValueError("valid, months, and hours arrays must have at least 1 dimension")
    if valid_arr.ndim > 2 or months_arr.ndim > 2 or hours_arr.ndim > 2:
        raise ValueError("valid, months, and hours arrays must not exceed 2 dimensions")

    try:
        val_b, m_b, h_b = np.broadcast_arrays(valid_arr, months_arr, hours_arr)
    except ValueError as e:
        raise ValueError(
            f"Shape mismatch: valid {valid_arr.shape}, months {months_arr.shape}, hours {hours_arr.shape}"
        ) from e

    if val_b.ndim > 2:
        raise ValueError(f"Broadcasted shape cannot exceed 2 dimensions, got {val_b.shape}")

    valid_mask = val_b
    if np.any(valid_mask):
        val_m = m_b[valid_mask]
        if np.any((val_m < 1) | (val_m > 12)):
            raise ValueError(
                f"months at valid slots must be in 1..12, got min={int(np.min(val_m))}, max={int(np.max(val_m))}"
            )
        val_h = h_b[valid_mask]
        if np.any((val_h < 0) | (val_h > 23)):
            raise ValueError(
                f"hours at valid slots must be in 0..23, got min={int(np.min(val_h))}, max={int(np.max(val_h))}"
            )

    loss_frac = _validate_finite_float(prior_loss_fraction, "prior_loss_fraction", min_val=0.0, max_val=1.0)

    if monthly_dhw_kwh is not None:
        m_kwh = np.asarray(monthly_dhw_kwh, dtype=float)
        if m_kwh.shape != (12,):
            raise ValueError(f"monthly_dhw_kwh must have length 12, got shape {m_kwh.shape}")
        if np.any(~np.isfinite(m_kwh)) or np.any(m_kwh < 0.0):
            raise ValueError("All monthly_dhw_kwh entries must be non-negative finite floats")
        gas_budgets_12 = m_kwh * (1.0 - loss_frac)
        is_gas_mode = True
    else:
        daily_kwh = calculate_daily_dhw_heat_from_litres(litres_per_day, service_temp_c, inlet_temp_c)
        is_gas_mode = False

    timing_w = build_dhw_timing_weights(morning_window, evening_window, morning_fraction, evening_fraction)

    is_1d = val_b.ndim == 1
    val_2d = val_b[None, :] if is_1d else val_b
    m_2d = m_b[None, :] if is_1d else m_b
    h_2d = h_b[None, :] if is_1d else h_b

    n_years, n_slots = val_2d.shape
    demand_2d = np.zeros((n_years, n_slots), dtype=float)

    for y in range(n_years):
        for m in range(1, 13):
            mask = val_2d[y] & (m_2d[y] == m)
            if not np.any(mask):
                continue
            if is_gas_mode:
                month_budget = gas_budgets_12[m - 1]
            else:
                n_valid = int(np.count_nonzero(mask))
                month_budget = daily_kwh * (n_valid / 24.0)

            if month_budget <= 0.0:
                continue

            slot_h = h_2d[y][mask]
            slot_weights = timing_w[slot_h]
            sum_w = float(np.sum(slot_weights))
            if sum_w <= 0.0:
                raise ValueError(
                    f"Insufficient draw timing in month {m}: positive demand ({month_budget:.4f} kWh) "
                    "cannot be allocated because no valid slots fall within active timing windows."
                )
            demand_2d[y, mask] = month_budget * (slot_weights / sum_w)

    return demand_2d[0] if is_1d else demand_2d


calculate_hourly_dhw_demand = build_hourly_dhw_demand
