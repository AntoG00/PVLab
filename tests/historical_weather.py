"""Helper for explicit, non-persistent historical baseline weather selection in frozen regression tests."""
from __future__ import annotations

import contextlib
from dataclasses import replace
import hashlib
from unittest.mock import patch
import numpy as np

import feasibility.engine as engine
import feasibility.weather_dataset as weather_dataset
from feasibility.runtime_context import current_runtime


@contextlib.contextmanager
def synthetic_thermal_runtime(gas_monthly):
    """Fixture-only binding; production still has no rebind API."""
    ctx = current_runtime()
    thermal = {**ctx.thermal_reference, 'GAS_MONTHLY': np.asarray(gas_monthly, dtype=float)}
    fixture = replace(ctx, thermal_reference=thermal)
    with patch('feasibility.runtime_context._bound', fixture):
        yield fixture


def clear_engine_weather_caches() -> None:
    """Clear all engine and dispatch caches that depend on weather data."""
    caches = [
        getattr(engine, 'data', None),
        getattr(engine, '_civil_hours_cached', None),
        getattr(engine, '_local_standard_dates_cached', None),
        getattr(engine, '_forecast_calendar_mapping', None),
        getattr(engine, '_forecast_calendar_slots', None),
        getattr(engine, '_load_cache', None),
        getattr(engine, '_load_cache_cached', None),
        getattr(engine, '_pv_profile_cached', None),
        getattr(engine, '_energy_cache', None),
        getattr(engine, '_energy_cache_cached', None),
        getattr(engine, '_resolve_routing_cached', None),
        getattr(engine, '_chart_cache', None),
        getattr(engine, '_dayhour_trace', None),
        getattr(engine, '_forecast_runs', None),
        getattr(engine, '_forecast_runs_cached', None),
    ]
    for c in caches:
        if c is not None and hasattr(c, 'cache_clear'):
            c.cache_clear()


@contextlib.contextmanager
def frozen_baseline_weather_context():
    """Context manager temporarily patching active weather dataset to verified synthetic baseline."""
    data = weather_dataset.verify_and_load_dataset(
        weather_dataset.BASELINE_NPZ, weather_dataset.BASELINE_AUDIT
    )
    npz_hash = weather_dataset._sha256(weather_dataset.BASELINE_NPZ)
    audit_hash = weather_dataset._sha256(weather_dataset.BASELINE_AUDIT)
    ctx = current_runtime()
    assert npz_hash == ctx.weather_identity[1], 'Synthetic weather archive provenance changed'
    assert audit_hash == ctx.weather_identity[2], 'Synthetic weather audit provenance changed'
    identity = ('synthetic-baseline', npz_hash, audit_hash)
    ctx = current_runtime()
    fixture = replace(ctx, dataset=data, weather_identity=identity,
                      dataset_digest=hashlib.sha256((npz_hash + audit_hash).encode()).hexdigest(),
                      input_revision=hashlib.sha256((ctx.input_revision + npz_hash + audit_hash).encode()).hexdigest())

    clear_engine_weather_caches()
    try:
        with (
            patch('feasibility.runtime_context._bound', fixture),
            patch('feasibility.weather_dataset.get_active_dataset', return_value=data),
            patch('feasibility.weather_dataset.active_weather_dataset_identity', return_value=identity),
        ):
            clear_engine_weather_caches()
            yield data
    finally:
        clear_engine_weather_caches()
