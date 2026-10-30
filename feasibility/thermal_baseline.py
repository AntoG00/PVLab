"""Carrier-neutral thermal bookkeeping, independent of dispatch and taxation.

Monthly vectors are Jan--Dec totals in their declared measurement period. They
are not an hourly projection. Annual-only measured inputs remain draft data
until monthly quantities (or an explicit gas allocation) are supplied.
"""
from collections.abc import Mapping
from dataclasses import dataclass
import math
from types import MappingProxyType

import numpy as np

from .profiles import parse_profile, profile_to_dict

THERMAL_VERSION = 'thermal-baseline-v1'


def _validate_number(val, name, *, positive=False):
    if type(val) not in (int, float):
        raise TypeError(f'{name} must be int or float')
    if not math.isfinite(val):
        raise ValueError(f'{name} must be finite')
    if val < 0 or (positive and val == 0):
        raise ValueError(f'{name} outside allowed range')
    return float(val)


def input_to_useful(amount, unit, conversion, efficiency):
    """Convert Smc*PCS, kg*LHV or kWh input to useful kWh; COP may exceed 1."""
    if type(unit) is not str or unit not in ('smc', 'kg', 'kwh'):
        raise ValueError('Unsupported input unit')
    amount = _validate_number(amount, 'amount')
    conversion = _validate_number(conversion, 'conversion', positive=True)
    efficiency = _validate_number(efficiency, 'efficiency', positive=True)
    if unit == 'kwh' and conversion != 1:
        raise ValueError('kwh conversion must equal 1')
    result = amount * conversion * efficiency
    if not math.isfinite(result):
        raise OverflowError('Useful heat overflow')
    return result


def _months(value, name):
    if not isinstance(value, (list, tuple)) or len(value) != 12:
        raise ValueError(name + ': requires 12 monthly quantities')
    return tuple(_validate_number(x, name) for x in value)


@dataclass(frozen=True)
class EndUseReference:
    id: str
    carrier: str
    monthly_useful_kwh: tuple
    monthly_input: tuple | None
    unit: str | None
    included_in_electric_load: bool
    action: str
    artifact_id: str | None
    annual_fixed_cost: float
    annual_variable_cost: float
    annual_bill_eur: float | None
    provenance: Mapping
    incremental_annual_kwh: float | None = None


@dataclass(frozen=True)
class ThermalReference:
    end_uses: Mapping
    summer_ac_multiplier: float = 1.0
    version: str = THERMAL_VERSION

    @property
    def monthly_useful_kwh(self):
        return MappingProxyType({k: v.monthly_useful_kwh for k, v in self.end_uses.items()
                                 if k in ('space_heat', 'dhw', 'cooking')})

    @property
    def annual_gas_smc(self):
        quantities = [v.monthly_input for v in self.end_uses.values() if v.carrier == 'gas']
        if any(v is None for v in quantities):
            return None  # Useful-only gas does not identify a purchased volume.
        return math.fsum(x for v in quantities for x in v)


def allocate_gas_baseline(monthly_gas_smc, summer_gas_base, cooking_smc_annual):
    """Reuse the legacy allocation arithmetic with every input explicit.

    Only carrier quantities are reused. End-use-specific efficiencies are applied
    separately; the legacy cooking gross-energy convention is not useful heat.
    """
    gas = _months(monthly_gas_smc, 'monthly_gas_smc')
    base = _validate_number(summer_gas_base, 'summer_gas_base')
    cooking = _validate_number(cooking_smc_annual, 'cooking_smc_annual')
    from .heatpump_demand import allocate_gas_consumption
    result = allocate_gas_consumption(gas, base, cooking, gas_pcs=1., boiler_efficiency=1.)
    shares = {'space_heat': tuple(result.heating_smc), 'dhw': tuple(result.dhw_smc),
              'cooking': tuple(result.cooking_smc)}
    for i in range(12):
        values = [float(v[i]) for v in shares.values()]
        if min(values) < 0 or not math.isclose(math.fsum(values), gas[i], rel_tol=1e-12, abs_tol=1e-9):
            raise ValueError('Gas allocation must be nonnegative and conserve input')
    if not math.isclose(math.fsum(gas), math.fsum(x for v in shares.values() for x in v),
                        rel_tol=1e-12, abs_tol=1e-9):
        raise ValueError('Annual gas conservation failed')
    return MappingProxyType({k: tuple(float(x) for x in v) for k, v in shares.items()})


def resolve_thermal_baseline(profile):
    """Resolve explicit physical demand and bill metadata; never calculate taxes.

    Coverage and same/different electrical-consumption-period declarations are
    retained verbatim. A same-period declaration must match consumption dates.
    Costs are scenario inputs; annual_bill_eur is an unreconstructed direct bill
    alternative to the fixed/variable split, never an inferred tax breakdown.
    """
    profile = parse_profile(profile_to_dict(profile) if not isinstance(profile, dict) else profile)
    allocation = profile.thermal.gas_allocation
    shares = allocate_gas_baseline(**allocation) if allocation is not None else None
    references = {}
    for use in profile.thermal.end_uses:
        useful = use.monthly_useful_kwh
        quantities = use.monthly_input
        provenance = {'method': 'none', 'coverage': use.coverage, 'period_relation': use.period_relation,
                      'consumption_coverage': profile.consumption.coverage,
                      'conversion_kwh_per_unit': use.conversion_kwh_per_unit,
                      'efficiency': use.efficiency, 'cop': use.cop,
                      'gas_allocation': allocation if use.carrier == 'gas' else None,
                      'cost_method': 'direct_annual_bill' if use.annual_bill_eur is not None else 'explicit_fixed_variable'}
        if use.annual_bill_eur is not None and (use.annual_fixed_cost or use.annual_variable_cost):
            raise ValueError('Choose direct annual bill OR fixed/variable costs')
        if use.carrier == 'none':
            if use.annual_fixed_cost or use.annual_variable_cost or use.annual_bill_eur:
                raise ValueError('Carrier none cannot have baseline costs')
            useful = (0.,) * 12
        else:
            if use.coverage is None or use.period_relation is None:
                raise ValueError('Explicit thermal coverage and period_relation required')
            electrical = profile.consumption.coverage
            if electrical.start is None or electrical.end is None:
                raise ValueError('Consumption coverage required to identify thermal period relation')
            same = (use.coverage.start, use.coverage.end) == (electrical.start, electrical.end)
            if (use.period_relation == 'same') != same:
                raise ValueError('Thermal period_relation disagrees with consumption coverage')
            if not use.coverage.complete:
                raise ValueError('Complete thermal monthly totals required')
            if (use.measured_input is None) == (useful is None):
                raise ValueError('Choose measured carrier input OR monthly useful heat')
            if use.measured_input is not None:
                if shares is not None and use.carrier == 'gas':
                    allocated = shares[use.id]
                    if quantities is not None and not np.allclose(quantities, allocated, rtol=1e-12, atol=1e-9):
                        raise ValueError('Monthly input disagrees with gas allocation')
                    quantities = allocated
                    if not math.isclose(math.fsum(quantities), use.measured_input, rel_tol=1e-12, abs_tol=1e-9):
                        raise ValueError('Measured input disagrees with gas allocation')
                if quantities is None:
                    raise ValueError('Measured input requires explicit monthly quantities')
                factor = use.cop if use.carrier == 'electricity' else use.efficiency
                conversion = use.conversion_kwh_per_unit
                if factor is None or conversion is None:
                    raise ValueError('Explicit conversion and efficiency/COP required')
                useful = tuple(input_to_useful(float(x), use.unit, conversion, factor) for x in quantities)
                provenance['method'] = 'allocated_gas' if shares is not None and use.carrier == 'gas' else 'measured_input'
            else:
                provenance['method'] = 'useful_kwh'
                if use.carrier == 'electricity':
                    if use.cop is None:
                        raise ValueError('Electric useful heat requires explicit COP')
                    quantities = tuple(x / use.cop for x in useful)
                    if not all(math.isfinite(x) for x in quantities):
                        raise ValueError('Electrical input overflow')
        references[use.id] = EndUseReference(use.id, use.carrier, tuple(useful), quantities,
            ('kwh' if use.carrier == 'electricity' else use.unit) if quantities is not None else None, use.included_in_electric_load, use.action,
            use.artifact_id, use.annual_fixed_cost, use.annual_variable_cost, use.annual_bill_eur,
            MappingProxyType(provenance))
    if shares is not None:
        gas_uses = [u for u in profile.thermal.end_uses if u.carrier == 'gas']
        periods = {(u.coverage.start, u.coverage.end) for u in gas_uses}
        if len(periods) != 1:
            raise ValueError('Allocated gas end uses must share a measurement period')
        for key, share in shares.items():
            if any(share) and (key not in references or references[key].carrier != 'gas' or
                               references[key].provenance['method'] != 'allocated_gas'):
                raise ValueError('Every nonzero gas allocation must be claimed exactly once')
    for load in profile.thermal.electric_loads:
        if load.action == 'add' and load.incremental_annual_kwh is None:
            raise ValueError('Explicit incremental annual energy required for EV/cooling add')
        references[load.id] = EndUseReference(load.id, 'electricity', (0.,)*12, None, 'kwh',
            load.included_in_electric_load, load.action, load.artifact_id, 0., 0., None,
            MappingProxyType({'method': 'explicit_electrical_load'}), load.incremental_annual_kwh)
    return ThermalReference(MappingProxyType(references), profile.default_settings.get('summer_ac_multiplier', 1.))


def replacement_delta(reference, action, existing_hourly, proposed_hourly):
    """Return hourly incremental kWh for selected actions, with exact subtraction.

    action maps end-use ID to unchanged/add/replace. existing_hourly maps IDs to
    separated gross electrical kWh and includes 'household' when subtracting.
    proposed_hourly maps IDs to proposed electrical kWh on the identical hourly
    axis. Callers must align the axis; this function rejects shapes/broadcasting.
    Distinct artifact IDs establish independent end-use claims. The entire batch
    is checked against gross household demand before any new load is added.
    """
    if not all(isinstance(x, Mapping) for x in (action, existing_hourly, proposed_hourly)):
        raise ValueError('Actions and hourly loads must be mappings')
    if set(action) - reference.end_uses.keys() or set(proposed_hourly) - set(action):
        raise ValueError('Unknown or unclaimed end use')
    if set(existing_hourly) - (reference.end_uses.keys() | {'household'}):
        raise ValueError('Unknown existing end use')
    arrays = {}
    shape = None
    for kind, mapping in (('old', existing_hourly), ('new', proposed_hourly)):
        for key, value in mapping.items():
            raw = np.asarray(value)
            if raw.dtype.kind not in 'iuf' or raw.ndim != 1 or not raw.size:
                raise ValueError('Hourly series must be nonempty numeric one-dimensional arrays')
            a = np.array(raw, dtype=float, copy=True)
            if not np.all(np.isfinite(a)) or np.any(a < 0):
                raise ValueError('Hourly energy must be finite and nonnegative')
            if shape is not None and a.shape != shape:
                raise ValueError('Hourly shape mismatch')
            shape = a.shape
            arrays[kind, key] = a
    if shape is None:
        raise ValueError('Supply an hourly axis, including for unchanged zero delta')
    delta = np.zeros(shape)
    removed = np.zeros(shape)
    claims = set()
    for key, mode in action.items():
        use = reference.end_uses[key]
        if mode == 'replace' and use.included_in_electric_load and use.artifact_id is not None:
            if any(other.id != key and other.included_in_electric_load and
                   other.artifact_id == use.artifact_id for other in reference.end_uses.values()):
                raise ValueError('Overlapping end-use artifact claims')
    for key, mode in action.items():
        if mode not in ('unchanged', 'add', 'replace'):
            raise ValueError('Unsupported replacement action')
        use = reference.end_uses[key]
        if mode == 'unchanged':
            continue
        if mode == 'add' and use.included_in_electric_load:
            raise ValueError('Existing included load cannot be added again; select replace')
        if ('new', key) not in arrays:
            raise ValueError('Proposed hourly series required')
        proposed = arrays['new', key]
        if key in ('ev', 'cooling') and mode == 'add':
            if use.incremental_annual_kwh is None or not math.isclose(
                    float(np.sum(proposed)), use.incremental_annual_kwh, rel_tol=1e-12, abs_tol=1e-9):
                raise ValueError('Proposed series must conserve explicit incremental annual energy')
        if mode == 'replace' and use.included_in_electric_load:
            if use.artifact_id is None or ('old', key) not in arrays or ('old', 'household') not in arrays:
                raise ValueError('Replacement requires separated existing series, artifact and household demand')
            if use.artifact_id in claims:
                raise ValueError('Overlapping end-use artifact claims')
            claims.add(use.artifact_id)
            removed += arrays['old', key]
        delta += proposed
    if ('old', 'household') in arrays and np.any(arrays['old', 'household'] - removed < 0):
        raise ValueError('Negative residual household demand; overlapping or inconsistent existing loads')
    delta -= removed
    if not np.all(np.isfinite(delta)):
        raise ValueError('Incremental load overflow')
    delta.setflags(write=False)
    return delta
