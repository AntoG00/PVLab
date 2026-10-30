"""Unit tests for air-to-water heat pump performance mapping, interpolation, weather compensation, and radiator output models.

Tests cover:
- Analytic 2x2 bilinear midpoint and edges with exact endpoints
- Bad axes, grids, non-finite values, negative capacity, and invalid COP validation
- Caller array buffer ownership, caller writeability preservation, and map immutability
- Out-of-envelope flags and zero-capacity enforcement (conservative unresolved-data policy; no optimistic extrapolation)
- Generic provisional fallback map thermodynamic trends (colder air / hotter water derating, bounded capacity, nominal scaling)
- Radiator EN 442 Delta-T 50 unity, low-temperature derating, and invalid return-water rejection
- Weather-compensation flow curve endpoints, slope interpolation, clamping, and validation
- Metadata flags preventing duplicate defrost and unit auxiliary adjustments
"""

import math
import unittest

import numpy as np

from feasibility.heatpump import (
    HeatPumpMapMetadata,
    HeatPumpPerformanceMap,
    InterpolationResult,
    WeatherCompensationCurve,
    apply_system_corrections,
    calculate_radiator_output,
    create_provisional_fallback_map,
    evaluate_radiator_output,
    interpolate_heat_pump_performance,
    weather_compensation_flow_temp,
)


class TestBilinearInterpolationAnalytic(unittest.TestCase):
    """Test bilinear interpolation against exact analytic values on a known 2x2 grid."""

    def setUp(self) -> None:
        # Minimal 2x2 grid:
        # outdoor: [0.0, 10.0]
        # water:   [30.0, 50.0]
        # COP values:
        #   (0, 30) -> 2.0,  (0, 50) -> 1.0
        #   (10, 30) -> 4.0, (10, 50) -> 3.0
        # Capacity (kW) values:
        #   (0, 30) -> 5.0,  (0, 50) -> 4.0
        #   (10, 30) -> 8.0, (10, 50) -> 7.0
        self.outdoor = [0.0, 10.0]
        self.water = [30.0, 50.0]
        self.cop_grid = [
            [2.0, 1.0],
            [4.0, 3.0],
        ]
        self.cap_grid = [
            [5.0, 4.0],
            [8.0, 7.0],
        ]
        self.metadata = HeatPumpMapMetadata(
            source="Analytic Test Fixture",
            model="Analytic 2x2",
            test_basis="Exact Synthetic",
            includes_defrost=True,
            includes_unit_auxiliaries=True,
        )
        self.hp_map = HeatPumpPerformanceMap(
            outdoor_temps=self.outdoor,
            water_temps=self.water,
            cop_grid=self.cop_grid,
            capacity_grid_kw=self.cap_grid,
            metadata=self.metadata,
        )

    def test_exact_vertices(self) -> None:
        """Interpolation at grid vertices must match exact values without floating point drift."""
        vertices = [
            (0.0, 30.0, 2.0, 5.0),
            (0.0, 50.0, 1.0, 4.0),
            (10.0, 30.0, 4.0, 8.0),
            (10.0, 50.0, 3.0, 7.0),
        ]
        for out_t, wat_t, exp_cop, exp_cap in vertices:
            with self.subTest(outdoor=out_t, water=wat_t):
                res = interpolate_heat_pump_performance(self.hp_map, out_t, wat_t)
                self.assertTrue(res.is_within_envelope)
                self.assertTrue(res.is_on_boundary)
                self.assertFalse(res.outside_outdoor)
                self.assertFalse(res.outside_water)
                self.assertAlmostEqual(res.cop, exp_cop, places=12)
                self.assertAlmostEqual(res.capacity_kw, exp_cap, places=12)
                self.assertAlmostEqual(res.electrical_input_kw, exp_cap / exp_cop, places=12)

    def test_exact_edges(self) -> None:
        """Midpoints along all 4 grid edges must match exact 1D linear interpolations."""
        # Bottom edge: water=30, outdoor from 0 to 10 -> midpoint outdoor=5.0
        res_bottom = interpolate_heat_pump_performance(self.hp_map, 5.0, 30.0)
        self.assertTrue(res_bottom.is_within_envelope)
        self.assertTrue(res_bottom.is_on_boundary)
        self.assertAlmostEqual(res_bottom.cop, 3.0, places=12)        # (2.0 + 4.0)/2
        self.assertAlmostEqual(res_bottom.capacity_kw, 6.5, places=12) # (5.0 + 8.0)/2

        # Top edge: water=50, outdoor from 0 to 10 -> midpoint outdoor=5.0
        res_top = interpolate_heat_pump_performance(self.hp_map, 5.0, 50.0)
        self.assertTrue(res_top.is_within_envelope)
        self.assertTrue(res_top.is_on_boundary)
        self.assertAlmostEqual(res_top.cop, 2.0, places=12)        # (1.0 + 3.0)/2
        self.assertAlmostEqual(res_top.capacity_kw, 5.5, places=12) # (4.0 + 7.0)/2

        # Left edge: outdoor=0, water from 30 to 50 -> midpoint water=40.0
        res_left = interpolate_heat_pump_performance(self.hp_map, 0.0, 40.0)
        self.assertTrue(res_left.is_within_envelope)
        self.assertTrue(res_left.is_on_boundary)
        self.assertAlmostEqual(res_left.cop, 1.5, places=12)        # (2.0 + 1.0)/2
        self.assertAlmostEqual(res_left.capacity_kw, 4.5, places=12) # (5.0 + 4.0)/2

        # Right edge: outdoor=10, water from 30 to 50 -> midpoint water=40.0
        res_right = interpolate_heat_pump_performance(self.hp_map, 10.0, 40.0)
        self.assertTrue(res_right.is_within_envelope)
        self.assertTrue(res_right.is_on_boundary)
        self.assertAlmostEqual(res_right.cop, 3.5, places=12)        # (4.0 + 3.0)/2
        self.assertAlmostEqual(res_right.capacity_kw, 7.5, places=12) # (8.0 + 7.0)/2

    def test_analytic_center_midpoint(self) -> None:
        """Center midpoint (5.0, 40.0) must equal exact average of all 4 corners."""
        res_mid = interpolate_heat_pump_performance(self.hp_map, 5.0, 40.0)
        self.assertTrue(res_mid.is_within_envelope)
        self.assertFalse(res_mid.is_on_boundary)
        self.assertFalse(res_mid.outside_outdoor)
        self.assertFalse(res_mid.outside_water)

        expected_cop = 0.25 * (2.0 + 1.0 + 4.0 + 3.0)  # 2.5
        expected_cap = 0.25 * (5.0 + 4.0 + 8.0 + 7.0)  # 6.0
        self.assertAlmostEqual(res_mid.cop, expected_cop, places=12)
        self.assertAlmostEqual(res_mid.capacity_kw, expected_cap, places=12)
        self.assertAlmostEqual(res_mid.electrical_input_kw, expected_cap / expected_cop, places=12)

    def test_arbitrary_internal_point(self) -> None:
        """Arbitrary interior point with t=0.25, u=0.25."""
        # outdoor=2.5 -> t=0.25; water=35.0 -> u=0.25
        res = interpolate_heat_pump_performance(self.hp_map, 2.5, 35.0)
        self.assertTrue(res.is_within_envelope)
        self.assertFalse(res.is_on_boundary)

        w00 = 0.75 * 0.75  # 0.5625
        w10 = 0.25 * 0.75  # 0.1875
        w01 = 0.75 * 0.25  # 0.1875
        w11 = 0.25 * 0.25  # 0.0625

        exp_cop = w00 * 2.0 + w10 * 4.0 + w01 * 1.0 + w11 * 3.0  # 2.25
        exp_cap = w00 * 5.0 + w10 * 8.0 + w01 * 4.0 + w11 * 7.0  # 5.5
        self.assertAlmostEqual(res.cop, exp_cop, places=12)
        self.assertAlmostEqual(res.capacity_kw, exp_cap, places=12)
        self.assertAlmostEqual(res.electrical_input_kw, exp_cap / exp_cop, places=12)


class TestPerformanceMapValidation(unittest.TestCase):
    """Test strict validation of equipment performance maps, axes, grids, and metadata."""

    def setUp(self) -> None:
        self.valid_meta = HeatPumpMapMetadata(
            source="Validation Fixture",
            model="Test Model",
            test_basis="EN 14511",
            includes_defrost=True,
            includes_unit_auxiliaries=True,
        )

    def test_axis_length_under_two_rejected(self) -> None:
        """Axes with fewer than 2 points cannot define a bilinear domain and must be rejected."""
        with self.assertRaises(ValueError):
            HeatPumpPerformanceMap([5.0], [35.0, 45.0], [[3.0, 2.0]], [[5.0, 4.0]], self.valid_meta)

        with self.assertRaises(ValueError):
            HeatPumpPerformanceMap([0.0, 10.0], [35.0], [[3.0], [4.0]], [[5.0], [6.0]], self.valid_meta)

    def test_non_strictly_increasing_axes_rejected(self) -> None:
        """Duplicate or descending axis points must be rejected."""
        # Duplicate outdoor
        with self.assertRaises(ValueError):
            HeatPumpPerformanceMap([0.0, 0.0], [30.0, 40.0], [[3.0, 2.0], [3.0, 2.0]], [[5.0, 4.0], [5.0, 4.0]], self.valid_meta)

        # Descending outdoor
        with self.assertRaises(ValueError):
            HeatPumpPerformanceMap([10.0, 0.0], [30.0, 40.0], [[3.0, 2.0], [3.0, 2.0]], [[5.0, 4.0], [5.0, 4.0]], self.valid_meta)

        # Duplicate water
        with self.assertRaises(ValueError):
            HeatPumpPerformanceMap([0.0, 10.0], [30.0, 30.0], [[3.0, 2.0], [3.0, 2.0]], [[5.0, 4.0], [5.0, 4.0]], self.valid_meta)

        # Descending water
        with self.assertRaises(ValueError):
            HeatPumpPerformanceMap([0.0, 10.0], [50.0, 30.0], [[3.0, 2.0], [3.0, 2.0]], [[5.0, 4.0], [5.0, 4.0]], self.valid_meta)

    def test_non_finite_axis_values_rejected(self) -> None:
        """NaN or Inf in axes must be rejected."""
        with self.assertRaises(ValueError):
            HeatPumpPerformanceMap([float("nan"), 10.0], [30.0, 40.0], [[3.0, 2.0], [4.0, 3.0]], [[5.0, 4.0], [6.0, 5.0]], self.valid_meta)

        with self.assertRaises(ValueError):
            HeatPumpPerformanceMap([0.0, 10.0], [30.0, float("inf")], [[3.0, 2.0], [4.0, 3.0]], [[5.0, 4.0], [6.0, 5.0]], self.valid_meta)

    def test_grid_shape_mismatches_rejected(self) -> None:
        """Grids not matching (len(outdoor), len(water)) must be rejected."""
        # COP grid transposed
        with self.assertRaises(ValueError):
            HeatPumpPerformanceMap(
                [0.0, 5.0, 10.0],  # len 3
                [35.0, 45.0],      # len 2
                [[3.0, 2.5, 2.0], [4.0, 3.5, 3.0]],  # shape (2, 3) instead of (3, 2)
                [[5.0, 4.0], [6.0, 5.0], [7.0, 6.0]],
                self.valid_meta,
            )

        # Capacity grid shape mismatch
        with self.assertRaises(ValueError):
            HeatPumpPerformanceMap(
                [0.0, 10.0],
                [35.0, 45.0],
                [[3.0, 2.0], [4.0, 3.0]],
                [[5.0, 4.0]],  # shape (1, 2) instead of (2, 2)
                self.valid_meta,
            )

    def test_invalid_cop_values_rejected(self) -> None:
        """Non-positive (<= 0) or non-finite COP values must be rejected."""
        # COP = 0
        with self.assertRaises(ValueError):
            HeatPumpPerformanceMap([0.0, 10.0], [35.0, 45.0], [[0.0, 2.0], [4.0, 3.0]], [[5.0, 4.0], [6.0, 5.0]], self.valid_meta)

        # Negative COP
        with self.assertRaises(ValueError):
            HeatPumpPerformanceMap([0.0, 10.0], [35.0, 45.0], [[-1.5, 2.0], [4.0, 3.0]], [[5.0, 4.0], [6.0, 5.0]], self.valid_meta)

        # NaN COP
        with self.assertRaises(ValueError):
            HeatPumpPerformanceMap([0.0, 10.0], [35.0, 45.0], [[float("nan"), 2.0], [4.0, 3.0]], [[5.0, 4.0], [6.0, 5.0]], self.valid_meta)

    def test_invalid_capacity_values_rejected(self) -> None:
        """Negative (< 0) or non-finite capacity values must be rejected."""
        # Negative capacity
        with self.assertRaises(ValueError):
            HeatPumpPerformanceMap([0.0, 10.0], [35.0, 45.0], [[3.0, 2.0], [4.0, 3.0]], [[-0.5, 4.0], [6.0, 5.0]], self.valid_meta)

        # Infinite capacity
        with self.assertRaises(ValueError):
            HeatPumpPerformanceMap([0.0, 10.0], [35.0, 45.0], [[3.0, 2.0], [4.0, 3.0]], [[float("inf"), 4.0], [6.0, 5.0]], self.valid_meta)

    def test_metadata_validation(self) -> None:
        """Metadata fields must be validated for proper types and non-empty strings."""
        with self.assertRaises(ValueError):
            HeatPumpMapMetadata(source="", model="M", test_basis="EN14511", includes_defrost=True, includes_unit_auxiliaries=True)

        with self.assertRaises(ValueError):
            HeatPumpMapMetadata(source="S", model="   ", test_basis="EN14511", includes_defrost=True, includes_unit_auxiliaries=True)

        with self.assertRaises(TypeError):
            HeatPumpMapMetadata(source="S", model="M", test_basis="EN14511", includes_defrost="yes", includes_unit_auxiliaries=True)  # type: ignore[arg-type]

        with self.assertRaises(TypeError):
            HeatPumpPerformanceMap([0.0, 10.0], [35.0, 45.0], [[3.0, 2.0], [4.0, 3.0]], [[5.0, 4.0], [6.0, 5.0]], metadata="not_metadata")  # type: ignore[arg-type]


class TestPerformanceMapArrayOwnership(unittest.TestCase):
    """Test that HeatPumpPerformanceMap creates owned copies of caller buffers to ensure independence."""

    def setUp(self) -> None:
        self.outdoor_caller = np.array([0.0, 10.0], dtype=np.float64)
        self.water_caller = np.array([30.0, 50.0], dtype=np.float64)
        self.cop_caller = np.array([[2.0, 1.0], [4.0, 3.0]], dtype=np.float64)
        self.cap_caller = np.array([[5.0, 4.0], [8.0, 7.0]], dtype=np.float64)
        self.metadata = HeatPumpMapMetadata(
            source="Ownership Fixture",
            model="Ownership Test",
            test_basis="Synthetic",
            includes_defrost=True,
            includes_unit_auxiliaries=True,
        )
        self.hp_map = HeatPumpPerformanceMap(
            outdoor_temps=self.outdoor_caller,
            water_temps=self.water_caller,
            cop_grid=self.cop_caller,
            capacity_grid_kw=self.cap_caller,
            metadata=self.metadata,
        )

    def test_caller_arrays_remain_writable(self) -> None:
        """Caller's original numpy arrays must remain writable after map construction."""
        self.assertTrue(self.outdoor_caller.flags.writeable)
        self.assertTrue(self.water_caller.flags.writeable)
        self.assertTrue(self.cop_caller.flags.writeable)
        self.assertTrue(self.cap_caller.flags.writeable)

    def test_caller_mutations_do_not_alter_map(self) -> None:
        """Later mutations to caller arrays must not alter the internal map state or interpolation results."""
        # Mutate caller arrays in place
        self.outdoor_caller[0] = -100.0
        self.water_caller[0] = 100.0
        self.cop_caller[0, 0] = 999.0
        self.cap_caller[0, 0] = 888.0

        # Map internal arrays must remain completely unchanged
        self.assertEqual(self.hp_map.outdoor_temps[0], 0.0)
        self.assertEqual(self.hp_map.water_temps[0], 30.0)
        self.assertEqual(self.hp_map.cop_grid[0, 0], 2.0)
        self.assertEqual(self.hp_map.capacity_grid_kw[0, 0], 5.0)

        # Bilinear interpolation must compute against original unmutated values
        res = interpolate_heat_pump_performance(self.hp_map, 0.0, 30.0)
        self.assertAlmostEqual(res.cop, 2.0, places=12)
        self.assertAlmostEqual(res.capacity_kw, 5.0, places=12)

    def test_map_internal_arrays_are_read_only(self) -> None:
        """Map internal arrays must be frozen read-only; in-place mutations must raise ValueError."""
        self.assertFalse(self.hp_map.outdoor_temps.flags.writeable)
        self.assertFalse(self.hp_map.water_temps.flags.writeable)
        self.assertFalse(self.hp_map.cop_grid.flags.writeable)
        self.assertFalse(self.hp_map.capacity_grid_kw.flags.writeable)

        with self.assertRaises(ValueError):
            self.hp_map.outdoor_temps[0] = 5.0
        with self.assertRaises(ValueError):
            self.hp_map.water_temps[0] = 35.0
        with self.assertRaises(ValueError):
            self.hp_map.cop_grid[0, 0] = 10.0
        with self.assertRaises(ValueError):
            self.hp_map.capacity_grid_kw[0, 0] = 20.0


class TestOutOfEnvelopeAndClamping(unittest.TestCase):
    """Test boundary detection, outside-domain flags, and strict zero capacity (no optimistic extrapolation)."""

    def setUp(self) -> None:
        self.hp_map = create_provisional_fallback_map(nominal_capacity_kw=7.0)
        self.min_out = self.hp_map.min_outdoor  # -15.0
        self.max_out = self.hp_map.max_outdoor  #  35.0
        self.min_wat = self.hp_map.min_water    #  30.0
        self.max_wat = self.hp_map.max_water    #  65.0

    def test_outdoor_too_cold_returns_zero_capacity(self) -> None:
        """Outdoor temperature colder than map minimum returns 0 kW and explicit flags."""
        res = interpolate_heat_pump_performance(self.hp_map, outdoor_temp=-20.0, water_temp=35.0)
        self.assertFalse(res.is_within_envelope)
        self.assertFalse(res.is_on_boundary)
        self.assertTrue(res.outside_outdoor)
        self.assertTrue(res.outdoor_below_min)
        self.assertFalse(res.outdoor_above_max)
        self.assertFalse(res.outside_water)
        self.assertEqual(res.capacity_kw, 0.0)
        self.assertEqual(res.cop, 0.0)
        self.assertEqual(res.electrical_input_kw, 0.0)

    def test_outdoor_too_warm_returns_zero_capacity(self) -> None:
        """Outdoor temperature above map maximum returns 0 kW and explicit flags."""
        res = interpolate_heat_pump_performance(self.hp_map, outdoor_temp=40.0, water_temp=35.0)
        self.assertFalse(res.is_within_envelope)
        self.assertFalse(res.is_on_boundary)
        self.assertTrue(res.outside_outdoor)
        self.assertTrue(res.outdoor_above_max)
        self.assertFalse(res.outdoor_below_min)
        self.assertFalse(res.outside_water)
        self.assertEqual(res.capacity_kw, 0.0)
        self.assertEqual(res.cop, 0.0)

    def test_water_too_cold_returns_zero_capacity(self) -> None:
        """Water temperature below map minimum returns 0 kW and explicit flags."""
        res = interpolate_heat_pump_performance(self.hp_map, outdoor_temp=7.0, water_temp=25.0)
        self.assertFalse(res.is_within_envelope)
        self.assertFalse(res.is_on_boundary)
        self.assertFalse(res.outside_outdoor)
        self.assertTrue(res.outside_water)
        self.assertTrue(res.water_below_min)
        self.assertFalse(res.water_above_max)
        self.assertEqual(res.capacity_kw, 0.0)
        self.assertEqual(res.cop, 0.0)

    def test_water_too_hot_returns_zero_capacity(self) -> None:
        """Water temperature above map maximum returns 0 kW and explicit flags."""
        res = interpolate_heat_pump_performance(self.hp_map, outdoor_temp=7.0, water_temp=70.0)
        self.assertFalse(res.is_within_envelope)
        self.assertFalse(res.is_on_boundary)
        self.assertFalse(res.outside_outdoor)
        self.assertTrue(res.outside_water)
        self.assertTrue(res.water_above_max)
        self.assertFalse(res.water_below_min)
        self.assertEqual(res.capacity_kw, 0.0)
        self.assertEqual(res.cop, 0.0)

    def test_both_axes_out_of_bounds(self) -> None:
        """Both outdoor and water out of bounds sets both flags and returns 0 kW."""
        res = interpolate_heat_pump_performance(self.hp_map, outdoor_temp=-25.0, water_temp=80.0)
        self.assertFalse(res.is_within_envelope)
        self.assertTrue(res.outside_outdoor)
        self.assertTrue(res.outside_water)
        self.assertTrue(res.outdoor_below_min)
        self.assertTrue(res.water_above_max)
        self.assertEqual(res.capacity_kw, 0.0)
        self.assertEqual(res.cop, 0.0)

    def test_exact_boundaries_are_within_envelope(self) -> None:
        """Exact envelope bounds are valid and marked is_on_boundary=True."""
        corners = [
            (self.min_out, self.min_wat),
            (self.min_out, self.max_wat),
            (self.max_out, self.min_wat),
            (self.max_out, self.max_wat),
        ]
        for out_t, wat_t in corners:
            res = interpolate_heat_pump_performance(self.hp_map, out_t, wat_t)
            self.assertTrue(res.is_within_envelope)
            self.assertTrue(res.is_on_boundary)
            self.assertFalse(res.outside_outdoor)
            self.assertFalse(res.outside_water)
            self.assertGreater(res.capacity_kw, 0.0)
            self.assertGreater(res.cop, 0.0)

    def test_non_finite_query_points_raise(self) -> None:
        """Querying with NaN or Inf raises ValueError."""
        with self.assertRaises(ValueError):
            interpolate_heat_pump_performance(self.hp_map, float("nan"), 35.0)

        with self.assertRaises(ValueError):
            interpolate_heat_pump_performance(self.hp_map, 7.0, float("inf"))


class TestProvisionalFallbackMapTrends(unittest.TestCase):
    """Test thermodynamic trends, nominal scaling, and documentation attributes of the fallback map."""

    def setUp(self) -> None:
        self.nominal_kw = 7.0
        self.hp_map = create_provisional_fallback_map(nominal_capacity_kw=self.nominal_kw)

    def test_cop_strictly_decreases_with_colder_outdoor_air(self) -> None:
        """For every water temperature column, COP must strictly decrease as outdoor temperature drops."""
        n_out, n_wat = self.hp_map.cop_grid.shape
        for j in range(n_wat):
            wat_t = self.hp_map.water_temps[j]
            cop_col = [self.hp_map.cop_grid[i, j] for i in range(n_out)]
            # As outdoor temp increases (bottom to top), COP must strictly increase
            for i in range(len(cop_col) - 1):
                self.assertLess(
                    cop_col[i],
                    cop_col[i + 1],
                    f"COP failed to increase with warmer outdoor air at water={wat_t}°C: "
                    f"A{self.hp_map.outdoor_temps[i]}={cop_col[i]} vs A{self.hp_map.outdoor_temps[i+1]}={cop_col[i+1]}",
                )

    def test_cop_strictly_decreases_with_hotter_leaving_water(self) -> None:
        """For every outdoor temperature row, COP must strictly decrease as leaving water temperature rises."""
        n_out, n_wat = self.hp_map.cop_grid.shape
        for i in range(n_out):
            out_t = self.hp_map.outdoor_temps[i]
            cop_row = [self.hp_map.cop_grid[i, j] for j in range(n_wat)]
            # As water temp increases (left to right), COP must strictly decrease
            for j in range(len(cop_row) - 1):
                self.assertGreater(
                    cop_row[j],
                    cop_row[j + 1],
                    f"COP failed to decrease with hotter leaving water at outdoor={out_t}°C: "
                    f"W{self.hp_map.water_temps[j]}={cop_row[j]} vs W{self.hp_map.water_temps[j+1]}={cop_row[j+1]}",
                )

    def test_capacity_decreases_with_colder_outdoor_air_and_is_bounded(self) -> None:
        """Capacity must drop as outdoor air gets cold, remain strictly positive, and never be infinite."""
        n_out, n_wat = self.hp_map.capacity_grid_kw.shape
        for j in range(n_wat):
            cap_col = [self.hp_map.capacity_grid_kw[i, j] for i in range(n_out)]
            for i in range(len(cap_col)):
                self.assertGreater(cap_col[i], 0.0)
                self.assertLess(cap_col[i], 20.0)  # sensible domestic bounds
            for i in range(len(cap_col) - 1):
                self.assertLess(
                    cap_col[i],
                    cap_col[i + 1],
                    f"Capacity failed to increase with warmer air at water={self.hp_map.water_temps[j]}°C",
                )

    def test_nominal_scaling_exact_at_rated_point(self) -> None:
        """Capacity at A7/W35 must exactly equal nominal_capacity_kw."""
        res_7 = interpolate_heat_pump_performance(self.hp_map, 7.0, 35.0)
        self.assertAlmostEqual(res_7.capacity_kw, 7.0, places=6)

        map_12 = create_provisional_fallback_map(nominal_capacity_kw=12.0)
        res_12 = interpolate_heat_pump_performance(map_12, 7.0, 35.0)
        self.assertAlmostEqual(res_12.capacity_kw, 12.0, places=6)

        # Scale factor must be identical across entire capacity grid
        ratio_grid = map_12.capacity_grid_kw / self.hp_map.capacity_grid_kw
        np.testing.assert_allclose(ratio_grid, 12.0 / 7.0, rtol=1e-12)

        # COP grid must be untouched
        np.testing.assert_allclose(map_12.cop_grid, self.hp_map.cop_grid, rtol=1e-12)

    def test_scale_capacity_method(self) -> None:
        """scale_capacity helper must scale capacity grid and preserve COP."""
        scaled_map = self.hp_map.scale_capacity(1.5)
        np.testing.assert_allclose(scaled_map.capacity_grid_kw, self.hp_map.capacity_grid_kw * 1.5, rtol=1e-12)
        np.testing.assert_allclose(scaled_map.cop_grid, self.hp_map.cop_grid, rtol=1e-12)

        with self.assertRaises(ValueError):
            self.hp_map.scale_capacity(0.0)
        with self.assertRaises(ValueError):
            self.hp_map.scale_capacity(-1.0)

    def test_nominal_capacity_input_validation(self) -> None:
        """Non-positive or non-finite nominal capacity must be rejected."""
        with self.assertRaises(ValueError):
            create_provisional_fallback_map(nominal_capacity_kw=0.0)
        with self.assertRaises(ValueError):
            create_provisional_fallback_map(nominal_capacity_kw=-5.0)
        with self.assertRaises(ValueError):
            create_provisional_fallback_map(nominal_capacity_kw=float("nan"))


class TestRadiatorOutputBound(unittest.TestCase):
    """Test radiator output model under EN 442 Delta-T 50 methodology, return water drop, and physics validation."""

    def test_exact_unity_at_dt50(self) -> None:
        """At EN 442 catalogue condition (75/65/20°C, Delta-T=50°C), output must exactly equal Q_DT50."""
        # Using return_temp_c = 65.0
        res1 = evaluate_radiator_output(q_dt50_kw=2.0, flow_temp_c=75.0, return_temp_c=65.0, room_temp_c=20.0, n=1.3)
        self.assertAlmostEqual(res1.mean_water_temp_c, 70.0, places=6)
        self.assertAlmostEqual(res1.delta_t_c, 50.0, places=6)
        self.assertAlmostEqual(res1.correction_factor, 1.0, places=12)
        self.assertAlmostEqual(res1.output_kw, 2.0, places=12)

        # Using return_drop_c = 10.0
        res2 = evaluate_radiator_output(q_dt50_kw=3.5, flow_temp_c=75.0, return_drop_c=10.0, room_temp_c=20.0, n=1.3)
        self.assertAlmostEqual(res2.return_temp_c, 65.0, places=6)
        self.assertAlmostEqual(res2.correction_factor, 1.0, places=12)
        self.assertAlmostEqual(res2.output_kw, 3.5, places=12)

        # Invariant with respect to exponent n at Delta-T = 50°C (1.0 ** n == 1.0)
        for test_n in [1.0, 1.25, 1.30, 1.35]:
            q_out = calculate_radiator_output(5.0, 75.0, return_drop_c=10.0, room_temp_c=20.0, n=test_n)
            self.assertAlmostEqual(q_out, 5.0, places=12)

    def test_reduced_output_at_low_temperature(self) -> None:
        """At lower flow temperatures, thermal output must follow the Stelrad power-law reduction."""
        # Condition: Flow 45°C, Return 40°C (drop 5°C), Room 20°C
        # Mean water = 42.5°C, Delta-T = 22.5°C
        # Ratio = 22.5 / 50.0 = 0.45
        # Correction = 0.45 ** 1.3 ≈ 0.353787
        q_dt50 = 2.0
        res = evaluate_radiator_output(q_dt50_kw=q_dt50, flow_temp_c=45.0, return_drop_c=5.0, room_temp_c=20.0, n=1.3)
        expected_ratio = 22.5 / 50.0
        expected_factor = expected_ratio ** 1.3
        self.assertAlmostEqual(res.correction_factor, expected_factor, places=9)
        self.assertAlmostEqual(res.output_kw, q_dt50 * expected_factor, places=9)
        self.assertLess(res.output_kw, q_dt50 * 0.40)  # Output is under 40% of rated Delta-T 50

        # Condition: Flow 55°C, Return 45°C (drop 10°C), Room 20°C
        # Mean = 50°C, Delta-T = 30°C
        # Ratio = 30 / 50 = 0.60
        res55 = evaluate_radiator_output(q_dt50_kw=2.0, flow_temp_c=55.0, return_drop_c=10.0, room_temp_c=20.0, n=1.3)
        self.assertAlmostEqual(res55.correction_factor, 0.60 ** 1.3, places=9)

    def test_mean_water_at_or_below_room_gives_zero(self) -> None:
        """If mean water temperature <= room temperature, output must be clamped at zero."""
        # Mean water equals room temp
        q_zero = calculate_radiator_output(2.0, 20.0, return_drop_c=0.0, room_temp_c=20.0)
        self.assertEqual(q_zero, 0.0)

        # Mean water below room temp (unheated summer or cold fill)
        q_cold = calculate_radiator_output(2.0, 15.0, return_drop_c=2.0, room_temp_c=20.0)
        self.assertEqual(q_cold, 0.0)

    def test_missing_or_contradictory_return_water_raises(self) -> None:
        """Either return_temp_c or return_drop_c must be supplied, and they must agree if both given."""
        # Neither specified
        with self.assertRaises(ValueError):
            calculate_radiator_output(2.0, 50.0, room_temp_c=20.0)

        # Both specified and agreeing
        q_agree = calculate_radiator_output(2.0, 50.0, return_temp_c=45.0, return_drop_c=5.0, room_temp_c=20.0)
        self.assertGreater(q_agree, 0.0)

        # Both specified and contradictory
        with self.assertRaises(ValueError):
            calculate_radiator_output(2.0, 50.0, return_temp_c=40.0, return_drop_c=5.0, room_temp_c=20.0)

    def test_physically_invalid_inputs_rejected(self) -> None:
        """Physically impossible inputs (return > flow, negative capacity, negative drop, invalid n) must raise."""
        # Return water hotter than flow water
        with self.assertRaises(ValueError):
            calculate_radiator_output(2.0, 45.0, return_temp_c=50.0, room_temp_c=20.0)

        # Negative return drop
        with self.assertRaises(ValueError):
            calculate_radiator_output(2.0, 45.0, return_drop_c=-5.0, room_temp_c=20.0)

        # Negative rated output
        with self.assertRaises(ValueError):
            calculate_radiator_output(-1.0, 50.0, return_drop_c=5.0, room_temp_c=20.0)

        # Exponent n <= 0
        with self.assertRaises(ValueError):
            calculate_radiator_output(2.0, 50.0, return_drop_c=5.0, room_temp_c=20.0, n=0.0)
        with self.assertRaises(ValueError):
            calculate_radiator_output(2.0, 50.0, return_drop_c=5.0, room_temp_c=20.0, n=-1.3)

        # Non-finite inputs
        with self.assertRaises(ValueError):
            calculate_radiator_output(float("nan"), 50.0, return_drop_c=5.0)
        with self.assertRaises(ValueError):
            calculate_radiator_output(2.0, float("inf"), return_drop_c=5.0)


class TestWeatherCompensationCurve(unittest.TestCase):
    """Test two-point weather-compensation curve endpoints, slope interpolation, and clamps."""

    def setUp(self) -> None:
        self.curve = WeatherCompensationCurve(
            outdoor_design_c=-5.0,
            flow_max_c=55.0,
            outdoor_mild_c=15.0,
            flow_min_c=30.0,
        )

    def test_endpoints_exact(self) -> None:
        """At outdoor design and mild design temperatures, flow temperature must match endpoints exactly."""
        # Cold design condition
        self.assertAlmostEqual(self.curve.calculate_flow_temp(-5.0), 55.0, places=12)
        # Mild condition
        self.assertAlmostEqual(self.curve.calculate_flow_temp(15.0), 30.0, places=12)

    def test_intermediate_slope_interpolation(self) -> None:
        """At midpoint outdoor temperature (5.0°C), flow temperature must equal (55 + 30)/2 = 42.5°C."""
        self.assertAlmostEqual(self.curve.calculate_flow_temp(5.0), 42.5, places=12)
        # Quarter point: outdoor = 0°C -> fraction = (0 - (-5)) / 20 = 0.25 -> 55 - 0.25*(55 - 30) = 48.75°C
        self.assertAlmostEqual(self.curve.calculate_flow_temp(0.0), 48.75, places=12)

    def test_clamping_behavior(self) -> None:
        """Temperatures colder than design or warmer than mild must clamp to upper and lower bounds."""
        # Colder than design (-10°C) clamps to flow_max_c (55.0°C)
        self.assertAlmostEqual(self.curve.calculate_flow_temp(-10.0), 55.0, places=12)
        self.assertAlmostEqual(self.curve.calculate_flow_temp(-25.0), 55.0, places=12)

        # Warmer than mild (20°C) clamps to flow_min_c (30.0°C)
        self.assertAlmostEqual(self.curve.calculate_flow_temp(20.0), 30.0, places=12)
        self.assertAlmostEqual(self.curve.calculate_flow_temp(35.0), 30.0, places=12)

    def test_custom_clamps(self) -> None:
        """Explicit custom clamps override default flow_min and flow_max bounds."""
        custom_curve = WeatherCompensationCurve(
            outdoor_design_c=-5.0,
            flow_max_c=55.0,
            outdoor_mild_c=15.0,
            flow_min_c=30.0,
            clamp_min_c=25.0,
            clamp_max_c=50.0,  # radiator cap at 50°C
        )
        # At -5°C, unclamped is 55.0, but clamped to 50.0
        self.assertAlmostEqual(custom_curve.calculate_flow_temp(-5.0), 50.0, places=12)
        # At 25°C, unclamped is 17.5, but clamped to 25.0
        self.assertAlmostEqual(custom_curve.calculate_flow_temp(25.0), 25.0, places=12)

    def test_standalone_helper(self) -> None:
        """Standalone weather_compensation_flow_temp helper matches class method."""
        val = weather_compensation_flow_temp(0.0, outdoor_design_c=-5.0, flow_max_c=55.0, outdoor_mild_c=15.0, flow_min_c=30.0)
        self.assertAlmostEqual(val, 48.75, places=12)

    def test_invalid_curve_points_rejected(self) -> None:
        """Inverted outdoor points, inverted flow points, or inverted clamps must raise ValueError."""
        # outdoor_design >= outdoor_mild
        with self.assertRaises(ValueError):
            WeatherCompensationCurve(outdoor_design_c=15.0, flow_max_c=55.0, outdoor_mild_c=15.0, flow_min_c=30.0)
        with self.assertRaises(ValueError):
            WeatherCompensationCurve(outdoor_design_c=20.0, flow_max_c=55.0, outdoor_mild_c=10.0, flow_min_c=30.0)

        # flow_max < flow_min
        with self.assertRaises(ValueError):
            WeatherCompensationCurve(outdoor_design_c=-5.0, flow_max_c=25.0, outdoor_mild_c=15.0, flow_min_c=30.0)

        # clamp_min > clamp_max
        with self.assertRaises(ValueError):
            WeatherCompensationCurve(
                outdoor_design_c=-5.0, flow_max_c=55.0, outdoor_mild_c=15.0, flow_min_c=30.0,
                clamp_min_c=50.0, clamp_max_c=40.0
            )

        # Non-finite outdoor input to calculate_flow_temp
        with self.assertRaises(ValueError):
            self.curve.calculate_flow_temp(float("nan"))


class TestSystemCorrectionsAndFlags(unittest.TestCase):
    """Test metadata flag handling to prevent duplicate defrost and auxiliary adjustments."""

    def setUp(self) -> None:
        self.hp_map = create_provisional_fallback_map(nominal_capacity_kw=7.0)
        self.point = interpolate_heat_pump_performance(self.hp_map, outdoor_temp=2.0, water_temp=45.0)

    def test_duplicate_defrost_penalty_skipped_when_metadata_flag_true(self) -> None:
        """When includes_defrost is True, additional_defrost_cop_penalty is ignored unless forced."""
        adj = apply_system_corrections(
            self.point,
            self.hp_map.metadata,
            additional_defrost_cop_penalty=0.10,
            force_defrost_adjustment=False,
        )
        self.assertFalse(adj.defrost_adjustment_applied)
        # COP must NOT be derated by 10%
        self.assertAlmostEqual(adj.adjusted_cop, self.point.cop, places=6)
        self.assertIn("skipped", adj.notes.lower())

    def test_defrost_penalty_applied_when_forced(self) -> None:
        """When force_defrost_adjustment is True, additional penalty is applied even if flag is True."""
        adj = apply_system_corrections(
            self.point,
            self.hp_map.metadata,
            additional_defrost_cop_penalty=0.10,
            force_defrost_adjustment=True,
        )
        self.assertTrue(adj.defrost_adjustment_applied)
        expected_cop = self.point.cop * 0.90
        self.assertAlmostEqual(adj.adjusted_cop, expected_cop, places=6)

    def test_unit_auxiliary_skipped_when_metadata_flag_true(self) -> None:
        """When includes_unit_auxiliaries is True, additional_unit_auxiliary_kw is ignored unless forced."""
        adj = apply_system_corrections(
            self.point,
            self.hp_map.metadata,
            additional_unit_auxiliary_kw=0.15,
            force_unit_auxiliary_adjustment=False,
        )
        self.assertFalse(adj.unit_auxiliary_adjustment_applied)
        self.assertIn("skipped", adj.notes.lower())

    def test_external_system_auxiliaries_and_losses_applied(self) -> None:
        """External radiator distribution pump and pipe losses must be cleanly accounted for."""
        ext_pump_kw = 0.05
        pipe_loss = 0.05  # 5% pipe loss
        adj = apply_system_corrections(
            self.point,
            self.hp_map.metadata,
            external_distribution_pump_kw=ext_pump_kw,
            thermal_distribution_loss_fraction=pipe_loss,
        )
        expected_thermal = self.point.capacity_kw * (1.0 - pipe_loss)
        expected_elec = self.point.electrical_input_kw + ext_pump_kw
        expected_cop = expected_thermal / expected_elec

        self.assertAlmostEqual(adj.adjusted_thermal_delivery_kw, expected_thermal, places=6)
        self.assertAlmostEqual(adj.adjusted_total_electrical_kw, expected_elec, places=6)
        self.assertAlmostEqual(adj.adjusted_cop, expected_cop, places=6)


if __name__ == "__main__":
    unittest.main()
