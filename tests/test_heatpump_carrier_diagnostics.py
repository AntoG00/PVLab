"""Carrier diagnostics and baseline carrier contract tests for calculate_heat_demand.

Tests the intended Phase 11 contract:
- Explicit monthly_heat_budgets without monthly_gas produces useful thermal kWh
  demand, sets gas_allocation=None, metadata baseline_carrier='useful_heat', and
  avoids generating fictitious Smc natural gas values.
- Missing both monthly_gas and monthly_heat_budgets raises a ValueError matching 'explicit'.
- Explicit monthly_gas preserves the GasAllocationResult object.
- Independent monthly heat budgets must be validated finite and non-negative.
"""

from __future__ import annotations

import numpy as np
import pytest

from feasibility.heatpump_demand import (
    GasAllocationResult,
    calculate_heat_demand,
)


@pytest.fixture
def synthetic_hourly_axis():
    """Synthetic 12-month hourly axis (one day per month, 288 hours) at constant 5 deg C."""
    n_months = 12
    hours_per_month = 24
    total_hours = n_months * hours_per_month

    temperature = np.full(total_hours, 5.0, dtype=float)
    valid = np.ones(total_hours, dtype=bool)
    months = np.repeat(np.arange(1, 13, dtype=int), hours_per_month)
    hours = np.tile(np.arange(24, dtype=int), n_months)
    civil_hours = np.tile(np.arange(24, dtype=int), n_months)

    return {
        "temperature": temperature,
        "valid": valid,
        "months": months,
        "hours": hours,
        "civil_hours": civil_hours,
    }


def test_calculate_heat_demand_explicit_useful_heat_budgets(synthetic_hourly_axis):
    """Explicit monthly_heat_budgets without monthly_gas gives useful-kWh demand,

    sets gas_allocation=None, metadata baseline_carrier='useful_heat', and produces
    no fictitious Smc.
    """
    synthetic_budgets = [
        120.0, 100.0, 80.0, 40.0, 15.0, 0.0, 0.0, 0.0, 10.0, 30.0, 70.0, 110.0
    ]

    res = calculate_heat_demand(
        temperature=synthetic_hourly_axis["temperature"],
        valid=synthetic_hourly_axis["valid"],
        months=synthetic_hourly_axis["months"],
        hours=synthetic_hourly_axis["hours"],
        civil_hours=synthetic_hourly_axis["civil_hours"],
        monthly_gas=None,
        monthly_heat_budgets=synthetic_budgets,
        schedule_mode="continuous",
        weather_sensitive=False,
    )

    # Useful-kWh demand verification
    assert res.heat_demand_kwh is not None
    assert np.allclose(res.monthly_heat_kwh, synthetic_budgets)
    assert np.isclose(res.annual_heat_kwh, sum(synthetic_budgets))
    assert np.isclose(np.sum(res.heat_demand_kwh), sum(synthetic_budgets))

    # Carrier contract: gas_allocation is None, baseline_carrier is 'useful_heat'
    assert res.gas_allocation is None
    assert res.metadata.get("baseline_carrier") == "useful_heat"

    # No fictitious Smc references
    assert "reference_total_smc" not in res.metadata
    assert "monthly_gas_smc" not in res.metadata


def test_calculate_heat_demand_missing_both_requires_explicit(synthetic_hourly_axis):
    """Missing both monthly_gas and monthly_heat_budgets must raise ValueError matching 'explicit'."""
    with pytest.raises(ValueError, match=r"(?i)explicit"):
        calculate_heat_demand(
            temperature=synthetic_hourly_axis["temperature"],
            valid=synthetic_hourly_axis["valid"],
            months=synthetic_hourly_axis["months"],
            hours=synthetic_hourly_axis["hours"],
            civil_hours=synthetic_hourly_axis["civil_hours"],
            monthly_gas=None,
            monthly_heat_budgets=None,
            schedule_mode="continuous",
            weather_sensitive=False,
        )


def test_calculate_heat_demand_explicit_monthly_gas_preserves_allocation(
    synthetic_hourly_axis,
):
    """Explicit monthly_gas preserves GasAllocationResult."""
    synthetic_gas = [
        110.0, 95.0, 80.0, 50.0, 30.0, 20.0, 20.0, 20.0, 25.0, 45.0, 70.0, 100.0
    ]

    res = calculate_heat_demand(
        temperature=synthetic_hourly_axis["temperature"],
        valid=synthetic_hourly_axis["valid"],
        months=synthetic_hourly_axis["months"],
        hours=synthetic_hourly_axis["hours"],
        civil_hours=synthetic_hourly_axis["civil_hours"],
        monthly_gas=synthetic_gas,
        monthly_heat_budgets=None,
        schedule_mode="continuous",
        weather_sensitive=False,
    )

    assert res.gas_allocation is not None
    assert isinstance(res.gas_allocation, GasAllocationResult)
    assert np.allclose(res.gas_allocation.monthly_gas, synthetic_gas)
    assert np.isclose(
        res.gas_allocation.annual_totals["monthly_gas_smc"], sum(synthetic_gas)
    )
    assert res.metadata.get("baseline_carrier") == "gas"
    assert res.annual_heat_kwh > 0.0


@pytest.mark.parametrize(
    "invalid_budgets, match_regex",
    [
        ([100.0, np.nan] + [100.0] * 10, r"(?i)finite"),
        ([100.0, np.inf] + [100.0] * 10, r"(?i)finite"),
        ([-5.0] + [100.0] * 11, r"(?i)negative"),
        ([100.0] * 11, r"12"),
    ],
)
def test_calculate_heat_demand_monthly_heat_budgets_validation(
    synthetic_hourly_axis, invalid_budgets, match_regex
):
    """Independent monthly heat budgets must be validated finite, non-negative, and length 12."""
    with pytest.raises(ValueError, match=match_regex):
        calculate_heat_demand(
            temperature=synthetic_hourly_axis["temperature"],
            valid=synthetic_hourly_axis["valid"],
            months=synthetic_hourly_axis["months"],
            hours=synthetic_hourly_axis["hours"],
            civil_hours=synthetic_hourly_axis["civil_hours"],
            monthly_gas=None,
            monthly_heat_budgets=invalid_budgets,
            schedule_mode="continuous",
            weather_sensitive=False,
        )
