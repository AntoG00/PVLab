"""Air-to-water heat pump performance mapping, interpolation, weather compensation, and radiator output models.

Stage 1 implementation providing pure numerical helpers for equipment performance mapping,
bilinear interpolation with exact endpoints and strict boundary clamping (no optimistic extrapolation),
a provisional fallback map with editable nominal scaling, two-point weather-compensation curve,
and radiator output derating under EN 442 Delta-T 50 methodology.

Research basis and citations:
- Vaillant aroTHERM plus documentation: https://www.vaillant.com/en/products/arothermplus/
  (illustrates that published EN 14511 test points mix part-load modulating points with maximum capacities;
   unverified partial tables must not be treated as full maximum-capacity envelopes).
- Stelrad Radiator Correction Factors: https://www.stelrad.ie/wp-content/uploads/2021/10/Stelrad-Correction-Factor-Oct-21.pdf
  (documents Delta-T 50 thermal output scaling Q = Q_DT50 * ((mean_water - room)/50)^n).
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Optional, Sequence, Tuple

import numpy as np


@dataclass(frozen=True)
class HeatPumpMapMetadata:
    """Metadata describing the provenance and testing basis of a heat pump performance map.

    Attributes:
        source: Provenance string, literature citation, or manufacturer document URL.
        model: Commercial or generic equipment model name.
        test_basis: Testing standard or synthetic specification (e.g. 'EN 14511', 'Synthetic').
        includes_defrost: True if defrost cycle degradation is confirmed factored into COP and capacity.
        includes_unit_auxiliaries: True if internal unit auxiliary power (such as fan, electronics,
            crankcase heater, or internal circulation pump) is confirmed factored into COP and electrical power.
        notes: Optional engineering notes or part-load vs full-load qualifications.

    Note:
        Inclusion of crankcase heaters, standby consumption, every internal circulation pump,
        or defrost penalties in published manufacturer EN 14511 data depends on declared basis
        and operating test conditions; it cannot be universally assumed. Verification against
        specific manufacturer technical documentation is required for actual equipment.
    """
    source: str
    model: str
    test_basis: str
    includes_defrost: bool
    includes_unit_auxiliaries: bool
    notes: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("Metadata 'source' must be a non-empty string.")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("Metadata 'model' must be a non-empty string.")
        if not isinstance(self.test_basis, str) or not self.test_basis.strip():
            raise ValueError("Metadata 'test_basis' must be a non-empty string.")
        if not isinstance(self.includes_defrost, bool):
            raise TypeError("Metadata 'includes_defrost' must be a boolean.")
        if not isinstance(self.includes_unit_auxiliaries, bool):
            raise TypeError("Metadata 'includes_unit_auxiliaries' must be a boolean.")


@dataclass(frozen=True, eq=False)
class HeatPumpPerformanceMap:
    """Validated rectangular performance map for an air-to-water heat pump.

    The supplied map extent defines a data validity envelope for interpolation.
    Outside this envelope, zero capacity is returned under a conservative unresolved-data
    policy (no optimistic extrapolation), not evidence that the compressor physically cannot run.

    Array inputs are copied on initialization to guarantee ownership; caller arrays
    remain writable and insulated from map immutability.

    Axes:
        outdoor_temps: 1D array of ambient outdoor air dry-bulb temperatures (°C), strictly increasing.
        water_temps: 1D array of leaving-water temperatures (°C), strictly increasing.
    Grids:
        cop_grid: 2D array of Coefficient of Performance (COP) values, shape (len(outdoor_temps), len(water_temps)).
            Must be strictly positive finite floats (> 0).
        capacity_grid_kw: 2D array of maximum available thermal capacity (kW), shape (len(outdoor_temps), len(water_temps)).
            Must be non-negative finite floats (>= 0).
    Metadata:
        metadata: HeatPumpMapMetadata describing testing standard, defrost inclusion, and auxiliaries.
    """
    outdoor_temps: np.ndarray
    water_temps: np.ndarray
    cop_grid: np.ndarray
    capacity_grid_kw: np.ndarray
    metadata: HeatPumpMapMetadata

    def __init__(
        self,
        outdoor_temps: Sequence[float] | np.ndarray,
        water_temps: Sequence[float] | np.ndarray,
        cop_grid: Sequence[Sequence[float]] | np.ndarray,
        capacity_grid_kw: Sequence[Sequence[float]] | np.ndarray,
        metadata: HeatPumpMapMetadata,
    ) -> None:
        # Validate metadata
        if not isinstance(metadata, HeatPumpMapMetadata):
            raise TypeError(f"metadata must be an instance of HeatPumpMapMetadata, got {type(metadata).__name__}")

        # Outdoor temperature axis (owned copy ensures caller buffers remain writable and insulated)
        outdoor_arr = np.array(outdoor_temps, dtype=np.float64, copy=True)
        if outdoor_arr.ndim != 1:
            raise ValueError(f"outdoor_temps must be a 1D sequence, got ndim={outdoor_arr.ndim}")
        if len(outdoor_arr) < 2:
            raise ValueError(f"outdoor_temps must contain at least 2 points for bilinear interpolation, got {len(outdoor_arr)}")
        if not np.all(np.isfinite(outdoor_arr)):
            raise ValueError("outdoor_temps contains non-finite values (NaN or Inf)")
        if not np.all(np.diff(outdoor_arr) > 0.0):
            raise ValueError("outdoor_temps must be strictly increasing without duplicates")

        # Leaving water temperature axis (owned copy)
        water_arr = np.array(water_temps, dtype=np.float64, copy=True)
        if water_arr.ndim != 1:
            raise ValueError(f"water_temps must be a 1D sequence, got ndim={water_arr.ndim}")
        if len(water_arr) < 2:
            raise ValueError(f"water_temps must contain at least 2 points for bilinear interpolation, got {len(water_arr)}")
        if not np.all(np.isfinite(water_arr)):
            raise ValueError("water_temps contains non-finite values (NaN or Inf)")
        if not np.all(np.diff(water_arr) > 0.0):
            raise ValueError("water_temps must be strictly increasing without duplicates")

        expected_shape = (len(outdoor_arr), len(water_arr))

        # COP grid (owned copy)
        cop_arr = np.array(cop_grid, dtype=np.float64, copy=True)
        if cop_arr.shape != expected_shape:
            raise ValueError(f"cop_grid shape {cop_arr.shape} does not match axes shape {expected_shape} (outdoor, water)")
        if not np.all(np.isfinite(cop_arr)):
            raise ValueError("cop_grid contains non-finite values (NaN or Inf)")
        if not np.all(cop_arr > 0.0):
            raise ValueError("cop_grid values must be strictly positive (> 0)")

        # Capacity grid (kW) (owned copy)
        cap_arr = np.array(capacity_grid_kw, dtype=np.float64, copy=True)
        if cap_arr.shape != expected_shape:
            raise ValueError(f"capacity_grid_kw shape {cap_arr.shape} does not match axes shape {expected_shape} (outdoor, water)")
        if not np.all(np.isfinite(cap_arr)):
            raise ValueError("capacity_grid_kw contains non-finite values (NaN or Inf)")
        if not np.all(cap_arr >= 0.0):
            raise ValueError("capacity_grid_kw values must be non-negative (>= 0)")

        # Freeze internal owned arrays into read-only
        outdoor_arr.setflags(write=False)
        water_arr.setflags(write=False)
        cop_arr.setflags(write=False)
        cap_arr.setflags(write=False)

        object.__setattr__(self, "outdoor_temps", outdoor_arr)
        object.__setattr__(self, "water_temps", water_arr)
        object.__setattr__(self, "cop_grid", cop_arr)
        object.__setattr__(self, "capacity_grid_kw", cap_arr)
        object.__setattr__(self, "metadata", metadata)

    @property
    def min_outdoor(self) -> float:
        """Minimum outdoor temperature in data validity envelope (°C)."""
        return float(self.outdoor_temps[0])

    @property
    def max_outdoor(self) -> float:
        """Maximum outdoor temperature in data validity envelope (°C)."""
        return float(self.outdoor_temps[-1])

    @property
    def min_water(self) -> float:
        """Minimum leaving-water temperature in data validity envelope (°C)."""
        return float(self.water_temps[0])

    @property
    def max_water(self) -> float:
        """Maximum leaving-water temperature in data validity envelope (°C)."""
        return float(self.water_temps[-1])

    def scale_capacity(self, factor: float, *, new_model_suffix: str = " (scaled)") -> HeatPumpPerformanceMap:
        """Create a new map with thermal capacity scaled by factor while preserving COP.

        Args:
            factor: Positive scaling factor applied to all capacity grid points.
            new_model_suffix: Suffix appended to model name in metadata.

        Returns:
            New validated HeatPumpPerformanceMap with scaled capacity grid.
        """
        if not math.isfinite(factor) or factor <= 0.0:
            raise ValueError(f"Scaling factor must be a strictly positive finite float, got {factor}")

        new_notes = f"{self.metadata.notes} [Capacity scaled by factor {factor:.4f}]".strip()
        new_metadata = HeatPumpMapMetadata(
            source=self.metadata.source,
            model=f"{self.metadata.model}{new_model_suffix}",
            test_basis=self.metadata.test_basis,
            includes_defrost=self.metadata.includes_defrost,
            includes_unit_auxiliaries=self.metadata.includes_unit_auxiliaries,
            notes=new_notes,
        )
        return HeatPumpPerformanceMap(
            outdoor_temps=self.outdoor_temps,
            water_temps=self.water_temps,
            cop_grid=self.cop_grid,
            capacity_grid_kw=self.capacity_grid_kw * factor,
            metadata=new_metadata,
        )


@dataclass(frozen=True)
class InterpolationResult:
    """Result of bilinear interpolation on a heat pump performance map.

    When operating within or on the validated envelope:
        - cop: Interpolated COP
        - capacity_kw: Interpolated maximum available thermal capacity (kW)
        - electrical_input_kw: Electrical power input in kW (capacity_kw / cop)
        - is_within_envelope: True
        - is_on_boundary: True if point is exactly on an envelope boundary

    When operating outside the supplied data validity envelope:
        - Strict boundary clamping is enforced: NO optimistic extrapolation!
        - capacity_kw: 0.0 kW (zero capacity under conservative unresolved-data policy)
        - cop: 0.0
        - electrical_input_kw: 0.0 kW
        - is_within_envelope: False
        - Boundary flags specify which axis and direction exceeded envelope limits.
        - Note: zero capacity outside data bounds is a conservative unresolved-data policy
          to prevent ungrounded extrapolation, not evidence that the compressor physically cannot run.
    """
    outdoor_temp: float
    water_temp: float
    cop: float
    capacity_kw: float
    electrical_input_kw: float
    is_within_envelope: bool
    is_on_boundary: bool
    outside_outdoor: bool
    outside_water: bool
    outdoor_below_min: bool
    outdoor_above_max: bool
    water_below_min: bool
    water_above_max: bool


def interpolate_heat_pump_performance(
    hp_map: HeatPumpPerformanceMap,
    outdoor_temp: float,
    water_temp: float,
) -> InterpolationResult:
    """Perform bilinear interpolation on a heat pump rectangular performance map with exact endpoints.

    If the query point (outdoor_temp, water_temp) falls outside the data validity
    envelope defined by the map axes, no optimistic extrapolation is permitted:
    capacity_kw is returned as 0.0 kW, cop is 0.0, and explicit boundary flags are returned.
    This zero-capacity return is a conservative unresolved-data policy to prevent
    unverified extrapolation, not physical evidence that the compressor cannot mechanically run.

    At grid vertices, the output exactly matches the grid values.
    Along grid edges, 1D linear interpolation is exact.

    Args:
        hp_map: Validated HeatPumpPerformanceMap.
        outdoor_temp: Ambient outdoor air dry-bulb temperature (°C).
        water_temp: Leaving water flow temperature (°C).

    Returns:
        InterpolationResult containing performance values and envelope boundary diagnostics.
    """
    if not math.isfinite(outdoor_temp) or not math.isfinite(water_temp):
        raise ValueError(
            f"outdoor_temp and water_temp must be finite numbers, got outdoor={outdoor_temp}, water={water_temp}"
        )

    x = float(outdoor_temp)
    y = float(water_temp)

    x_min = hp_map.min_outdoor
    x_max = hp_map.max_outdoor
    y_min = hp_map.min_water
    y_max = hp_map.max_water

    out_below = x < x_min
    out_above = x > x_max
    wat_below = y < y_min
    wat_above = y > y_max

    outside_outdoor = out_below or out_above
    outside_water = wat_below or wat_above
    is_within = not (outside_outdoor or outside_water)

    if not is_within:
        return InterpolationResult(
            outdoor_temp=x,
            water_temp=y,
            cop=0.0,
            capacity_kw=0.0,
            electrical_input_kw=0.0,
            is_within_envelope=False,
            is_on_boundary=False,
            outside_outdoor=outside_outdoor,
            outside_water=outside_water,
            outdoor_below_min=out_below,
            outdoor_above_max=out_above,
            water_below_min=wat_below,
            water_above_max=wat_above,
        )

    # Point is within or on the boundary
    is_on_boundary = (
        math.isclose(x, x_min, abs_tol=1e-12) or
        math.isclose(x, x_max, abs_tol=1e-12) or
        math.isclose(y, y_min, abs_tol=1e-12) or
        math.isclose(y, y_max, abs_tol=1e-12)
    )

    # Locate cell interval [x_i, x_{i+1}]
    outdoor_axis = hp_map.outdoor_temps
    if math.isclose(x, x_max, abs_tol=1e-12) or x >= x_max:
        i = len(outdoor_axis) - 2
        t = 1.0
    elif math.isclose(x, x_min, abs_tol=1e-12) or x <= x_min:
        i = 0
        t = 0.0
    else:
        i = int(np.searchsorted(outdoor_axis, x, side="right") - 1)
        i = max(0, min(len(outdoor_axis) - 2, i))
        x_i = outdoor_axis[i]
        x_next = outdoor_axis[i + 1]
        t = (x - x_i) / (x_next - x_i)
        if math.isclose(x, x_i, abs_tol=1e-14):
            t = 0.0
        elif math.isclose(x, x_next, abs_tol=1e-14):
            t = 1.0

    # Locate cell interval [y_j, y_{j+1}]
    water_axis = hp_map.water_temps
    if math.isclose(y, y_max, abs_tol=1e-12) or y >= y_max:
        j = len(water_axis) - 2
        u = 1.0
    elif math.isclose(y, y_min, abs_tol=1e-12) or y <= y_min:
        j = 0
        u = 0.0
    else:
        j = int(np.searchsorted(water_axis, y, side="right") - 1)
        j = max(0, min(len(water_axis) - 2, j))
        y_j = water_axis[j]
        y_next = water_axis[j + 1]
        u = (y - y_j) / (y_next - y_j)
        if math.isclose(y, y_j, abs_tol=1e-14):
            u = 0.0
        elif math.isclose(y, y_next, abs_tol=1e-14):
            u = 1.0

    # Bilinear weights
    w00 = (1.0 - t) * (1.0 - u)
    w10 = t * (1.0 - u)
    w01 = (1.0 - t) * u
    w11 = t * u

    cop = float(
        w00 * hp_map.cop_grid[i, j] +
        w10 * hp_map.cop_grid[i + 1, j] +
        w01 * hp_map.cop_grid[i, j + 1] +
        w11 * hp_map.cop_grid[i + 1, j + 1]
    )

    capacity_kw = float(
        w00 * hp_map.capacity_grid_kw[i, j] +
        w10 * hp_map.capacity_grid_kw[i + 1, j] +
        w01 * hp_map.capacity_grid_kw[i, j + 1] +
        w11 * hp_map.capacity_grid_kw[i + 1, j + 1]
    )

    electrical_input_kw = (capacity_kw / cop) if (cop > 0.0 and capacity_kw > 0.0) else 0.0

    return InterpolationResult(
        outdoor_temp=x,
        water_temp=y,
        cop=cop,
        capacity_kw=capacity_kw,
        electrical_input_kw=electrical_input_kw,
        is_within_envelope=True,
        is_on_boundary=is_on_boundary,
        outside_outdoor=False,
        outside_water=False,
        outdoor_below_min=False,
        outdoor_above_max=False,
        water_below_min=False,
        water_above_max=False,
    )


# -----------------------------------------------------------------------------
# PROVISIONAL Generic Fallback Map Specification
# -----------------------------------------------------------------------------
# Grid axes:
# Outdoor air (°C): -15, -7, 2, 7, 12, 20, 35; Leaving water (°C): 30, 35, 45, 55, 65.
# These synthetic axes are chosen modelling points spanning typical ambient and hydronic
# regimes; they do NOT constitute a certified EN 14511 test grid.

PROVISIONAL_OUTDOOR_TEMPS: Tuple[float, ...] = (-15.0, -7.0, 2.0, 7.0, 12.0, 20.0, 35.0)
PROVISIONAL_WATER_TEMPS: Tuple[float, ...] = (30.0, 35.0, 45.0, 55.0, 65.0)

# Invented sensitivity COP grid:
# Plausible thermodynamic trends:
# - Strictly decreases as outdoor temperature decreases (for each water temperature)
# - Strictly decreases as leaving water temperature increases (for each outdoor temperature)
# - Includes unit defrost penalty in sub-zero points (-15, -7, 2)
# - Includes unit internal auxiliaries (fans, inverter driver, internal circulation pump)
PROVISIONAL_COP_GRID: Tuple[Tuple[float, ...], ...] = (
    # W30,  W35,  W45,  W55,  W65
    (2.50, 2.30, 1.85, 1.50, 1.20),  # A -15
    (3.10, 2.85, 2.35, 1.95, 1.55),  # A  -7
    (3.90, 3.55, 2.90, 2.40, 1.95),  # A   2
    (5.10, 4.65, 3.70, 3.00, 2.40),  # A   7 (reference A7/W35 = 4.65)
    (5.90, 5.40, 4.25, 3.45, 2.75),  # A  12
    (7.00, 6.40, 5.00, 4.05, 3.20),  # A  20
    (8.50, 7.70, 6.00, 4.80, 3.80),  # A  35
)

# Invented sensitivity capacity ratios normalized to nominal rating at A7/W35 = 1.00:
# Plausible thermodynamic trends:
# - Maximum compressor thermal capacity drops as suction density falls at cold outdoor temperatures
# - Moderate capacity degradation at elevated leaving water temperatures due to compressor discharge pressure
PROVISIONAL_CAPACITY_RATIO_GRID: Tuple[Tuple[float, ...], ...] = (
    # W30,  W35,  W45,  W55,  W65
    (0.65, 0.62, 0.56, 0.50, 0.42),  # A -15
    (0.85, 0.82, 0.76, 0.70, 0.60),  # A  -7
    (0.94, 0.91, 0.86, 0.80, 0.70),  # A   2
    (1.03, 1.00, 0.95, 0.90, 0.80),  # A   7 (reference A7/W35 = 1.00)
    (1.10, 1.07, 1.01, 0.95, 0.86),  # A  12
    (1.20, 1.16, 1.09, 1.02, 0.92),  # A  20
    (1.30, 1.25, 1.18, 1.10, 1.00),  # A  35
)


def create_provisional_fallback_map(
    nominal_capacity_kw: float = 7.0,
    *,
    nominal_outdoor_c: float = 7.0,
    nominal_water_c: float = 35.0,
) -> HeatPumpPerformanceMap:
    """Construct a clearly labelled PROVISIONAL generic air-to-water heat pump performance map.

    WARNING:
    This map is based on explicitly invented sensitivity assumptions representing typical
    inverter air-to-water thermodynamic degradation, NOT certified manufacturer data and NOT
    derived from air-to-air AC SCOP ratings. It provides a plausible, bounded numerical baseline
    for feasibility modeling with editable nominal scaling.

    Assumptions:
    1. Grid bounds: outdoor -15°C to 35°C; leaving water 30°C to 65°C.
    2. Rated reference point: A7/W35 maximum thermal capacity = nominal_capacity_kw (default 7.0 kW).
    3. COP decreases monotonically with colder outdoor air and higher leaving water temperatures.
    4. Sub-zero COP values incorporate unit defrost penalties (includes_defrost=True).
    5. Unit internal auxiliary power is included (includes_unit_auxiliaries=True).
    6. External distribution pump and cylinder standby losses are NOT included.

    Args:
        nominal_capacity_kw: Maximum available thermal capacity at A7/W35 in kW (default 7.0 kW).
        nominal_outdoor_c: Reference outdoor air temperature for scaling (default 7.0°C).
        nominal_water_c: Reference leaving water temperature for scaling (default 35.0°C).

    Returns:
        Validated HeatPumpPerformanceMap ready for bilinear interpolation.
    """
    if not math.isfinite(nominal_capacity_kw) or nominal_capacity_kw <= 0.0:
        raise ValueError(
            f"nominal_capacity_kw must be a strictly positive finite float, got {nominal_capacity_kw}"
        )

    # Locate reference point in grid
    out_idx = PROVISIONAL_OUTDOOR_TEMPS.index(nominal_outdoor_c)
    wat_idx = PROVISIONAL_WATER_TEMPS.index(nominal_water_c)
    base_ref_ratio = PROVISIONAL_CAPACITY_RATIO_GRID[out_idx][wat_idx]

    ratio_arr = np.array(PROVISIONAL_CAPACITY_RATIO_GRID, dtype=np.float64)
    scaled_cap_arr = (ratio_arr / base_ref_ratio) * float(nominal_capacity_kw)

    metadata = HeatPumpMapMetadata(
        source="PROVISIONAL synthetic fallback model (explicitly invented sensitivity assumptions; NOT manufacturer data)",
        model=f"Generic Air-to-Water Monobloc {nominal_capacity_kw:.1f}kW (Provisional)",
        test_basis="Synthetic modelling points (provisional trends; not a certified EN 14511 grid)",
        includes_defrost=True,
        includes_unit_auxiliaries=True,
        notes=(
            f"Provisional generic fallback map with nominal capacity {nominal_capacity_kw:.1f} kW at "
            f"A{nominal_outdoor_c:.0f}/W{nominal_water_c:.0f}. Invented sensitivities reflect thermodynamic "
            "Carnot degradation at low outdoor and high water temperatures. In this synthetic baseline, "
            "sub-zero defrost degradation and internal unit auxiliaries are assumed included (includes_defrost=True, "
            "includes_unit_auxiliaries=True) to avoid duplicate penalties downstream. In published manufacturer "
            "EN 14511 data, inclusion of crankcase heaters, standby, all internal pumps, or defrost varies by "
            "test condition and declaration; explicit verification of manufacturer data is required for actual equipment. "
            "Downstream callers must NOT apply duplicate unit defrost/auxiliary penalties to this provisional map."
        ),
    )

    return HeatPumpPerformanceMap(
        outdoor_temps=PROVISIONAL_OUTDOOR_TEMPS,
        water_temps=PROVISIONAL_WATER_TEMPS,
        cop_grid=PROVISIONAL_COP_GRID,
        capacity_grid_kw=scaled_cap_arr,
        metadata=metadata,
    )


# -----------------------------------------------------------------------------
# Weather-Compensation Flow Curve
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class WeatherCompensationCurve:
    """Editable two-point weather-compensation heating flow temperature curve.

    Maps ambient outdoor temperature to hydronic leaving-water flow temperature
    using a linear schedule between a cold design condition and a mild condition,
    with upper and lower clamps to ensure flow temperature stays within equipment
    and emitter physical limits.

    Design points:
        outdoor_design_c: Cold ambient outdoor design condition (°C), e.g. -5.0°C.
        flow_max_c: Target flow temperature at outdoor design condition (°C), e.g. 55.0°C.
        outdoor_mild_c: Mild ambient outdoor condition (°C), e.g. 15.0°C.
        flow_min_c: Target flow temperature at mild condition (°C), e.g. 30.0°C.

    Clamps:
        clamp_min_c: Lower bound clamp for flow temperature (°C). Defaults to flow_min_c.
        clamp_max_c: Upper bound clamp for flow temperature (°C). Defaults to flow_max_c.
    """
    outdoor_design_c: float = -5.0
    flow_max_c: float = 55.0
    outdoor_mild_c: float = 15.0
    flow_min_c: float = 30.0
    clamp_min_c: Optional[float] = None
    clamp_max_c: Optional[float] = None

    def __post_init__(self) -> None:
        for name, val in [
            ("outdoor_design_c", self.outdoor_design_c),
            ("flow_max_c", self.flow_max_c),
            ("outdoor_mild_c", self.outdoor_mild_c),
            ("flow_min_c", self.flow_min_c),
        ]:
            if not math.isfinite(val):
                raise ValueError(f"WeatherCompensationCurve parameter '{name}' must be finite, got {val}")

        if self.outdoor_design_c >= self.outdoor_mild_c:
            raise ValueError(
                f"outdoor_design_c ({self.outdoor_design_c}°C) must be strictly less than "
                f"outdoor_mild_c ({self.outdoor_mild_c}°C)"
            )

        if self.flow_max_c < self.flow_min_c:
            raise ValueError(
                f"flow_max_c ({self.flow_max_c}°C) must be >= flow_min_c ({self.flow_min_c}°C) "
                "for space-heating compensation"
            )

        # Resolve clamps
        eff_clamp_min = self.flow_min_c if self.clamp_min_c is None else self.clamp_min_c
        eff_clamp_max = self.flow_max_c if self.clamp_max_c is None else self.clamp_max_c

        if not math.isfinite(eff_clamp_min) or not math.isfinite(eff_clamp_max):
            raise ValueError("Clamps must be finite numbers")

        if eff_clamp_min > eff_clamp_max:
            raise ValueError(
                f"clamp_min_c ({eff_clamp_min}°C) cannot be greater than clamp_max_c ({eff_clamp_max}°C)"
            )

        # Physical sanity checks
        if not (-50.0 <= self.outdoor_design_c <= 40.0) or not (-50.0 <= self.outdoor_mild_c <= 40.0):
            raise ValueError("Outdoor design/mild temperatures must be between -50°C and 40°C")
        if not (15.0 <= self.flow_min_c <= 85.0) or not (15.0 <= self.flow_max_c <= 85.0):
            raise ValueError("Flow temperatures must be between 15°C and 85°C")

        object.__setattr__(self, "clamp_min_c", eff_clamp_min)
        object.__setattr__(self, "clamp_max_c", eff_clamp_max)

    def calculate_flow_temp(self, outdoor_temp_c: float) -> float:
        """Calculate clamped leaving water flow temperature for a given outdoor temperature.

        Args:
            outdoor_temp_c: Ambient outdoor air dry-bulb temperature (°C).

        Returns:
            Target hydronic leaving water flow temperature (°C) clamped to [clamp_min_c, clamp_max_c].
        """
        if not math.isfinite(outdoor_temp_c):
            raise ValueError(f"outdoor_temp_c must be a finite number, got {outdoor_temp_c}")

        slope = (self.flow_min_c - self.flow_max_c) / (self.outdoor_mild_c - self.outdoor_design_c)
        unclamped_flow = self.flow_max_c + slope * (float(outdoor_temp_c) - self.outdoor_design_c)
        return float(min(self.clamp_max_c, max(self.clamp_min_c, unclamped_flow)))  # type: ignore[type-var]


def weather_compensation_flow_temp(
    outdoor_temp_c: float,
    *,
    outdoor_design_c: float = -5.0,
    flow_max_c: float = 55.0,
    outdoor_mild_c: float = 15.0,
    flow_min_c: float = 30.0,
    clamp_min_c: Optional[float] = None,
    clamp_max_c: Optional[float] = None,
) -> float:
    """One-shot helper to compute weather-compensated flow temperature."""
    curve = WeatherCompensationCurve(
        outdoor_design_c=outdoor_design_c,
        flow_max_c=flow_max_c,
        outdoor_mild_c=outdoor_mild_c,
        flow_min_c=flow_min_c,
        clamp_min_c=clamp_min_c,
        clamp_max_c=clamp_max_c,
    )
    return curve.calculate_flow_temp(outdoor_temp_c)


# -----------------------------------------------------------------------------
# Radiator Thermal Output Model (EN 442 Delta-T 50)
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class RadiatorOutputResult:
    """Detailed evaluation of hydronic radiator thermal output under EN 442 Delta-T 50 methodology.

    Formula:
        Q = Q_DT50 * max(0, ((flow + return) / 2 - room) / 50) ** n

    Attributes:
        output_kw: Bounded thermal output (kW).
        q_dt50_kw: Rated thermal output at EN 442 standard Delta-T 50 (kW).
        flow_temp_c: Flow water temperature entering radiator (°C).
        return_temp_c: Return water temperature leaving radiator (°C).
        return_drop_c: Temperature drop across radiator flow - return (°C).
        room_temp_c: Ambient heated room air temperature (°C).
        mean_water_temp_c: Arithmetic mean water temperature (flow + return) / 2 (°C).
        delta_t_c: Operating temperature difference (mean_water_temp - room) (°C).
        correction_factor: Output derating factor relative to Delta-T 50 rating.
        n: Radiator thermal characteristic exponent (default 1.3 provisional).
    """
    output_kw: float
    q_dt50_kw: float
    flow_temp_c: float
    return_temp_c: float
    return_drop_c: float
    room_temp_c: float
    mean_water_temp_c: float
    delta_t_c: float
    correction_factor: float
    n: float


def evaluate_radiator_output(
    q_dt50_kw: float,
    flow_temp_c: float,
    *,
    return_temp_c: Optional[float] = None,
    return_drop_c: Optional[float] = None,
    room_temp_c: float = 20.0,
    n: float = 1.3,
) -> RadiatorOutputResult:
    """Evaluate hydronic radiator thermal output using EN 442 Delta-T 50 scaling.

    Formula:
        Q = Q_DT50 * max(0, ((flow + return) / 2 - room) / 50) ** n

    Citation & Site Observation Notes:
        Stelrad Technical Guidance: https://www.stelrad.ie/wp-content/uploads/2021/10/Stelrad-Correction-Factor-Oct-21.pdf
        Under EN 442, standard catalogue ratings are tested at 75°C flow, 65°C return, 20°C room (Delta-T = 50°C).
        At heat pump flow temperatures (e.g. 45°C - 55°C), thermal output decreases significantly.
        The exponent n=1.3 is an editable generic approximation (radiator-specific data needed for final design).
        User operating observation: User reported operating with ~60°C boiler flow roughly maintaining ~18°C indoor
        temperature during heating periods; because return temperature, radiator sizes/installed outputs, and heating
        equipment remain unknown, this observation alone does NOT identify radiator capacity or dwelling heat loss.

    Args:
        q_dt50_kw: Catalogue rated output at standard Delta-T 50 condition (kW). Must be >= 0.
        flow_temp_c: Flow water temperature entering radiator (°C).
        return_temp_c: Return water temperature leaving radiator (°C).
            Either return_temp_c or return_drop_c must be explicitly specified.
        return_drop_c: Temperature drop across radiator (flow_temp_c - return_temp_c) (°C).
            Either return_temp_c or return_drop_c must be explicitly specified.
        room_temp_c: Ambient heated room temperature (°C), default 20.0°C.
        n: Radiator thermal exponent, default 1.3 provisional. Must be > 0.

    Returns:
        RadiatorOutputResult containing output in kW and intermediate parameters.
    """
    for name, val in [("q_dt50_kw", q_dt50_kw), ("flow_temp_c", flow_temp_c), ("room_temp_c", room_temp_c), ("n", n)]:
        if not math.isfinite(val):
            raise ValueError(f"Radiator parameter '{name}' must be finite, got {val}")

    if q_dt50_kw < 0.0:
        raise ValueError(f"Rated radiator output q_dt50_kw must be non-negative, got {q_dt50_kw}")

    if n <= 0.0:
        raise ValueError(f"Radiator exponent n must be strictly positive, got {n}")

    # Explicit return-water condition validation
    if return_temp_c is None and return_drop_c is None:
        raise ValueError("Explicit return water condition required: specify either return_temp_c or return_drop_c.")

    if return_temp_c is not None and return_drop_c is not None:
        if not math.isfinite(return_temp_c) or not math.isfinite(return_drop_c):
            raise ValueError("return_temp_c and return_drop_c must be finite numbers")
        expected_return = flow_temp_c - return_drop_c
        if not math.isclose(expected_return, return_temp_c, abs_tol=1e-5):
            raise ValueError(
                f"Inconsistent return water condition: return_temp_c ({return_temp_c}°C) "
                f"contradicts flow_temp_c - return_drop_c ({expected_return}°C)"
            )
        eff_return_temp = float(return_temp_c)
        eff_return_drop = float(return_drop_c)
    elif return_drop_c is not None:
        if not math.isfinite(return_drop_c):
            raise ValueError("return_drop_c must be a finite number")
        eff_return_drop = float(return_drop_c)
        eff_return_temp = float(flow_temp_c - eff_return_drop)
    else:
        assert return_temp_c is not None
        if not math.isfinite(return_temp_c):
            raise ValueError("return_temp_c must be a finite number")
        eff_return_temp = float(return_temp_c)
        eff_return_drop = float(flow_temp_c - eff_return_temp)

    # Physical validity: return water cannot be hotter than flow water in heating mode
    if eff_return_drop < 0.0 or eff_return_temp > flow_temp_c:
        raise ValueError(
            f"Physically invalid return condition: return water temperature ({eff_return_temp}°C) "
            f"cannot exceed flow water temperature ({flow_temp_c}°C) in heating mode."
        )

    mean_water_temp = (flow_temp_c + eff_return_temp) / 2.0
    delta_t = mean_water_temp - room_temp_c

    # If mean water temperature is at or below room temperature, heat output is zero
    dt_ratio = delta_t / 50.0
    bounded_ratio = max(0.0, dt_ratio)
    correction_factor = float(bounded_ratio ** n)
    output_kw = float(q_dt50_kw * correction_factor)

    return RadiatorOutputResult(
        output_kw=output_kw,
        q_dt50_kw=float(q_dt50_kw),
        flow_temp_c=float(flow_temp_c),
        return_temp_c=eff_return_temp,
        return_drop_c=eff_return_drop,
        room_temp_c=float(room_temp_c),
        mean_water_temp_c=mean_water_temp,
        delta_t_c=delta_t,
        correction_factor=correction_factor,
        n=float(n),
    )


def calculate_radiator_output(
    q_dt50_kw: float,
    flow_temp_c: float,
    *,
    return_temp_c: Optional[float] = None,
    return_drop_c: Optional[float] = None,
    room_temp_c: float = 20.0,
    n: float = 1.3,
) -> float:
    """Calculate bounded radiator thermal output (kW) under EN 442 Delta-T 50 scaling."""
    return evaluate_radiator_output(
        q_dt50_kw=q_dt50_kw,
        flow_temp_c=flow_temp_c,
        return_temp_c=return_temp_c,
        return_drop_c=return_drop_c,
        room_temp_c=room_temp_c,
        n=n,
    ).output_kw


# -----------------------------------------------------------------------------
# System-Level Effects and Auxiliary Adjustment Helper
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class SystemPerformanceAdjustment:
    """Adjusted heat pump and system performance point incorporating downstream effects.

    Prevents duplicate unit-level adjustments by checking map metadata flags.
    """
    adjusted_cop: float
    adjusted_thermal_delivery_kw: float
    adjusted_total_electrical_kw: float
    compressor_electrical_kw: float
    external_auxiliary_kw: float
    defrost_adjustment_applied: bool
    unit_auxiliary_adjustment_applied: bool
    thermal_distribution_loss_fraction: float
    notes: str


def apply_system_corrections(
    point: InterpolationResult,
    metadata: HeatPumpMapMetadata,
    *,
    external_distribution_pump_kw: float = 0.0,
    external_standby_auxiliary_kw: float = 0.0,
    thermal_distribution_loss_fraction: float = 0.0,
    additional_defrost_cop_penalty: float = 0.0,
    force_defrost_adjustment: bool = False,
    additional_unit_auxiliary_kw: float = 0.0,
    force_unit_auxiliary_adjustment: bool = False,
) -> SystemPerformanceAdjustment:
    """Apply downstream system-level corrections to an interpolated operating point without duplicating unit effects.

    This helper inspects metadata.includes_defrost and metadata.includes_unit_auxiliaries:
    - If metadata.includes_defrost is True, additional_defrost_cop_penalty is SKIPPED (unless force_defrost_adjustment is True)
      to avoid duplicate defrost derating.
    - If metadata.includes_unit_auxiliaries is True, additional_unit_auxiliary_kw is SKIPPED (unless force_unit_auxiliary_adjustment is True)
      to avoid double-counting internal unit auxiliaries.
    - External system effects (external radiator distribution pump, standby losses, thermal pipe losses)
      are added cleanly to represent overall system delivery.

    Args:
        point: InterpolationResult from interpolate_heat_pump_performance.
        metadata: HeatPumpMapMetadata associated with the source map.
        external_distribution_pump_kw: Electrical power of secondary hydronic heating pump (kW).
        external_standby_auxiliary_kw: External standby electrical power (kW).
        thermal_distribution_loss_fraction: Fractional heat loss in distribution piping (0.0 to < 1.0).
        additional_defrost_cop_penalty: Additional fractional COP derating (0.0 to < 1.0).
        force_defrost_adjustment: Override metadata flag and force additional defrost penalty.
        additional_unit_auxiliary_kw: Additional internal unit auxiliary power (kW).
        force_unit_auxiliary_adjustment: Override metadata flag and force additional unit auxiliary power.

    Returns:
        SystemPerformanceAdjustment with transparently accounted powers and flags.
    """
    for name, val in [
        ("external_distribution_pump_kw", external_distribution_pump_kw),
        ("external_standby_auxiliary_kw", external_standby_auxiliary_kw),
        ("thermal_distribution_loss_fraction", thermal_distribution_loss_fraction),
        ("additional_defrost_cop_penalty", additional_defrost_cop_penalty),
        ("additional_unit_auxiliary_kw", additional_unit_auxiliary_kw),
    ]:
        if not math.isfinite(val):
            raise ValueError(f"System correction parameter '{name}' must be finite, got {val}")

    if external_distribution_pump_kw < 0.0:
        raise ValueError("external_distribution_pump_kw must be non-negative")
    if external_standby_auxiliary_kw < 0.0:
        raise ValueError("external_standby_auxiliary_kw must be non-negative")
    if not (0.0 <= thermal_distribution_loss_fraction < 1.0):
        raise ValueError("thermal_distribution_loss_fraction must be in [0.0, 1.0)")
    if not (0.0 <= additional_defrost_cop_penalty < 1.0):
        raise ValueError("additional_defrost_cop_penalty must be in [0.0, 1.0)")
    if additional_unit_auxiliary_kw < 0.0:
        raise ValueError("additional_unit_auxiliary_kw must be non-negative")

    # If heat pump compressor capacity is zero (off or outside operating envelope)
    if point.capacity_kw <= 0.0 or not point.is_within_envelope:
        external_aux = external_distribution_pump_kw + external_standby_auxiliary_kw
        return SystemPerformanceAdjustment(
            adjusted_cop=0.0,
            adjusted_thermal_delivery_kw=0.0,
            adjusted_total_electrical_kw=external_aux,
            compressor_electrical_kw=0.0,
            external_auxiliary_kw=external_aux,
            defrost_adjustment_applied=False,
            unit_auxiliary_adjustment_applied=False,
            thermal_distribution_loss_fraction=thermal_distribution_loss_fraction,
            notes="Heat pump inactive or outside validated envelope; zero compressor output.",
        )

    # 1. Defrost penalty handling
    defrost_applied = False
    effective_cop = point.cop
    notes_list = []

    if metadata.includes_defrost and not force_defrost_adjustment:
        if additional_defrost_cop_penalty > 0.0:
            notes_list.append("additional_defrost_cop_penalty skipped: map metadata indicates defrost is already included.")
    else:
        if additional_defrost_cop_penalty > 0.0:
            effective_cop *= (1.0 - additional_defrost_cop_penalty)
            defrost_applied = True
            notes_list.append(f"Applied defrost COP penalty factor (1 - {additional_defrost_cop_penalty:.3f}).")

    # 2. Unit auxiliaries handling
    unit_aux_applied = False
    unit_aux_kw = 0.0

    if metadata.includes_unit_auxiliaries and not force_unit_auxiliary_adjustment:
        if additional_unit_auxiliary_kw > 0.0:
            notes_list.append("additional_unit_auxiliary_kw skipped: map metadata indicates unit auxiliaries are already included.")
    else:
        if additional_unit_auxiliary_kw > 0.0:
            unit_aux_kw = additional_unit_auxiliary_kw
            unit_aux_applied = True
            notes_list.append(f"Added unit auxiliary electrical power {unit_aux_kw:.3f} kW.")

    # 3. Thermal delivery after distribution losses
    thermal_delivery_kw = point.capacity_kw * (1.0 - thermal_distribution_loss_fraction)

    # 4. Electrical powers
    compressor_elec_kw = point.capacity_kw / effective_cop if effective_cop > 0.0 else 0.0
    external_aux_kw = external_distribution_pump_kw + external_standby_auxiliary_kw
    total_elec_kw = compressor_elec_kw + unit_aux_kw + external_aux_kw

    system_cop = (thermal_delivery_kw / total_elec_kw) if total_elec_kw > 0.0 else 0.0

    return SystemPerformanceAdjustment(
        adjusted_cop=system_cop,
        adjusted_thermal_delivery_kw=thermal_delivery_kw,
        adjusted_total_electrical_kw=total_elec_kw,
        compressor_electrical_kw=compressor_elec_kw,
        external_auxiliary_kw=external_aux_kw,
        defrost_adjustment_applied=defrost_applied,
        unit_auxiliary_adjustment_applied=unit_aux_applied,
        thermal_distribution_loss_fraction=thermal_distribution_loss_fraction,
        notes="; ".join(notes_list) if notes_list else "Standard system corrections applied without duplication.",
    )
