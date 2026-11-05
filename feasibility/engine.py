"""Hourly physical dispatch and scenario cash flows, using only local audited data.

Weather envelopes repeat each historic weather year throughout the asset life;
they are sensitivity bounds, not stochastic forecasts or confidence intervals.
"""
from .legacy_profile import legacy_text
from feasibility.runtime_context import current_runtime, assert_runtime_key, runtime_if_initialized, mutable_copy
from .fiscal import current_policy, CONFIRMATIONS
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import os
from collections import OrderedDict, namedtuple
from contextvars import ContextVar
from dataclasses import dataclass
import json, math, datetime as dt, calendar, hashlib
from zoneinfo import ZoneInfo
from .runtime import check_native_runtime
check_native_runtime()
from . import weather_dataset
from .physical_dispatch import (
    dispatch_physical_year,
    dispatch_year,
    dispatch_weather_years,
    dispatch_weather_monthly,
    dispatch_lifetime,
    physical_parameters,
    normalize_routing_priority,
    routing_switch_hour,
    scheduled_month_active,
    charge_source_split,
    soc_ceiling_series,
    ROUTING_MODES,
    ROUTING_AUTO,
    SCHEDULED_MONTHS,
)
import numpy as np
from numba import njit, prange
from scipy.optimize import brentq
from .assumptions import (
    defaults,
    records,
    SPEC,
    STRUCTURAL_SETTINGS,
    thaw_roof_settings,
    _normalize_battery,
    calculate_battery_composition,
    battery_composition,
    enumerate_battery_combinations,
    battery_combinations,
    enumerate_battery_compositions_by_capacity,
    battery_compositions_by_capacity,
)
from .heatpump_settings import (
    HP_PHYSICAL_KEYS,
    HP_ALIASES,
    HP_INTEGER_NAMES,
    validate_heat_pump_cross_fields,
)
from .schedule import (
    LEGACY_HEATING_KEYS,
    migrate_and_resolve_schedule,
    canonical_windows_hourly_overlaps,
    freeze,
    thaw_settings as _thaw_schedule_settings,
)

HERE=Path(__file__).resolve().parent
if os.environ.get('PV_LEGACY_DATA_ROOT'):
    HERE=Path(os.environ['PV_LEGACY_DATA_ROOT']).resolve().parent
COOLING_BALANCE_TEMP=24.0
FIELDS=['load_kwh','pv_kwh','direct_kwh','charge_kwh','discharge_kwh','grid_import_kwh','export_kwh','battery_loss_kwh','end_soc_kwh','clipped_kwh','cycles','export_price_weighted_kwh','capacity_kwh','max_balance_error']
INTEGER_NAMES=({n for n,row in SPEC.items() if row[1] in ['panels','modules','year','years','hour','hours','weekday','month']} | HP_INTEGER_NAMES)

def __getattr__(name):
    # Compatibility view for existing private diagnostics, with no default data.
    if name == 'GAS_MONTHLY':
        ctx = runtime_if_initialized()
        if ctx is None:
            from .runtime_context import legacy_thermal_inputs
            return legacy_thermal_inputs()['GAS_MONTHLY']
        return ctx.thermal_reference['GAS_MONTHLY']
    raise AttributeError(name)


def _thermal_reference():
    from .thermal_baseline import ThermalReference
    ctx = runtime_if_initialized()
    ref = None if ctx is None else ctx.thermal_reference
    return ref if isinstance(ref, ThermalReference) else None


def _uses_heat_pump(s):
    enabled = bool(s.get('planning_heat_pump', False)) and s.get('load_scenario') != 'baseline'
    if not enabled or _thermal_reference() is None:
        return enabled
    return any(_thermal_action(s,k) != 'unchanged' for k in ('space_heat','dhw'))


def _thermal_action(s, key):
    ref = _thermal_reference()
    use = ref.end_uses.get(key) if ref is not None else None
    if use is None or s['load_scenario'] == 'baseline':
        return 'unchanged'
    if key == 'ev' and s['load_scenario'] != 'ev':
        return 'unchanged'
    if key == 'ev' and use.action == 'replace':
        raise ValueError('EV scenarios support unchanged or incremental planned additions only')
    if key == 'cooking' and s.get('planning_heat_pump') and not s.get('hp_electric_cooking'):
        return 'unchanged'
    return use.action


def _thermal_useful(s, key, *, changed_only=False):
    ref = _thermal_reference()
    use = ref.end_uses.get(key) if ref is not None else None
    if use is None or (changed_only and _thermal_action(s, key) == 'unchanged'):
        return np.zeros(12)
    return np.asarray(use.monthly_useful_kwh, dtype=float)


def _baseline_gas_volume():
    ref = _thermal_reference()
    if ref is None:
        return float(current_runtime().thermal_reference['GAS_MONTHLY'].sum())
    volume = ref.annual_gas_smc
    if volume is None:
        raise ValueError('Gas billing requires explicit purchased Smc, not useful heat alone')
    return float(volume)


def data():
    return weather_dataset.get_active_dataset()

LEGACY_ROOFS = ('south', 'west', 'east', 'dormer', 'flat', 'secondary_south')
LEGACY_ROOF_KEYS = frozenset(r + '_panels' for r in LEGACY_ROOFS) | {'dormer_south_proxy'}


def thaw_settings(settings):
    return thaw_roof_settings(_thaw_schedule_settings(settings))


def _dynamic_roofs():
    ctx = runtime_if_initialized()
    if ctx is not None and ctx.calendar_version == 'utc_hourly_v2':
        if ctx.household_profile is None:
            raise ValueError('v2 evaluation requires a bound household profile')
        return ctx.household_profile['roofs']
    return None


def _panel_counts(s):
    if s.get('roof_panels') is not None:
        return dict(s['roof_panels'])
    return {r: s.get(r + '_panels', 0) for r in LEGACY_ROOFS}


def _panel_total(s):
    return sum(_panel_counts(s).values())


VALUATION_UNITS = ('EUR', 'weighted_kWh')
VALUATION_MODES = ('flat', 'shape_scaled', 'absolute_proxy')
VALID_VALUATION_COMBINATIONS = {
    ('flat', 'weighted_kWh'),
    ('shape_scaled', 'weighted_kWh'),
    ('absolute_proxy', 'EUR'),
}


@dataclass(frozen=True)
class ValuationDescriptor:
    mode: str
    unit: str

    def __post_init__(self):
        m = self.mode
        u = self.unit
        if m not in VALUATION_MODES:
            raise ValueError(f"Invalid valuation mode: {m!r}. Expected one of {VALUATION_MODES}")
        if u not in VALUATION_UNITS:
            raise ValueError(f"Invalid valuation unit: {u!r}. Expected one of {VALUATION_UNITS}")
        if (m, u) not in VALID_VALUATION_COMBINATIONS:
            raise ValueError(
                f"Inconsistent valuation mode and unit combination: mode={m!r}, unit={u!r}. "
                f"Valid combinations are {VALID_VALUATION_COMBINATIONS}"
            )

    def settlement_multiplier(
        self,
        anchor=1.0,
        capture=1.0,
        future_factor=1.0,
        inflation_factor=1.0,
        escalation_factor=1.0,
    ) -> float:
        for name, factor in (
            ('future_factor', future_factor),
            ('inflation_factor', inflation_factor),
            ('escalation_factor', escalation_factor),
        ):
            if factor is None or not math.isfinite(float(factor)):
                raise ValueError(f"{name} must be a finite number, got {factor!r}")

        ff = float(future_factor)
        inf = float(inflation_factor)
        esc = float(escalation_factor)

        if self.unit == 'EUR':
            mult = ff * inf * esc
        elif self.mode == 'flat':
            if anchor is None or not math.isfinite(float(anchor)):
                raise ValueError(f"anchor must be a finite number, got {anchor!r}")
            if capture is None or not math.isfinite(float(capture)):
                raise ValueError(f"capture must be a finite number, got {capture!r}")
            mult = float(anchor) * float(capture) * ff * inf * esc
        elif self.mode == 'shape_scaled':
            if anchor is None or not math.isfinite(float(anchor)):
                raise ValueError(f"anchor must be a finite number, got {anchor!r}")
            mult = float(anchor) * ff * inf * esc
        else:
            raise ValueError(f"Unhandled valuation combination: mode={self.mode!r}, unit={self.unit!r}")

        if not math.isfinite(mult):
            raise ValueError(f"Resulting settlement multiplier is not finite: {mult}")
        return mult

    def settle(
        self,
        base_value: float,
        anchor=1.0,
        capture=1.0,
        future_factor=1.0,
        inflation_factor=1.0,
        escalation_factor=1.0,
    ) -> float:
        if base_value is None or not math.isfinite(float(base_value)):
            raise ValueError(f"base_value must be a finite number, got {base_value!r}")
        mult = self.settlement_multiplier(
            anchor=anchor,
            capture=capture,
            future_factor=future_factor,
            inflation_factor=inflation_factor,
            escalation_factor=escalation_factor,
        )
        result = float(base_value) * mult
        if not math.isfinite(result):
            raise ValueError(f"Settlement result is not finite: {result}")
        return result


def export_valuation(settings=None):
    ctx = current_runtime()
    if ctx.household_profile is not None:
        mode = ctx.household_profile['market']['valuation_mode']
    else:
        mode = 'flat' if price_profile(settings)[1] == 'flat_solar_capture_benchmark' else 'shape_scaled'
    return ValuationDescriptor(mode=mode, unit='EUR' if mode == 'absolute_proxy' else 'weighted_kWh')


def price_identity(settings=None):
    """Retained report identity; an older runtime remains pinned after refresh."""
    ctx = current_runtime()
    desc = export_valuation(settings)
    market = dict(ctx.household_profile['market']) if ctx.household_profile is not None else {}
    scenario = defaults(); scenario.update(settings or {})
    scenario = {k: scenario[k] for k in ('export_market_price','export_capture_factor',
        'actual_price_future_factor','export_price_future_years','inflation','energy_real_escalation',
        'pmg_election','pmg_price','pmg_nominal_escalation') if k in scenario}
    return {**market, 'scenario': scenario, 'runtime_key': ctx.key, 'price_revision': ctx.price_revision,
            'fingerprint': price_fingerprint(settings), 'unit': desc.unit, 'mode': desc.mode,
            'future_policy': 'existing_linear_ramp_then_hold_inflation_and_real_escalation_once'}


def price_result_stale(identity, settings=None):
    return identity != price_identity(settings)


def validate_price_selection(settings, profile=None):
    """Scenario anchors may vary; a worker's market selection cannot change."""
    if settings.get('retail_contract', 'fixed') != 'fixed':
        raise ValueError('Unsupported retail contract: only fixed retail is supported')
    if profile is None:
        if (settings.get('export_price_profile') == 'profile_market' or
                settings.get('export_price_window') == 'profile_market'):
            raise ValueError('Profile price selection requires a pinned household profile')
        return
    mode = profile['market']['valuation_mode']
    preference = 'flat_benchmark' if mode == 'flat' else 'profile_market'
    if (settings.get('export_price_profile', preference) != preference or
            settings.get('export_price_window', 'profile_market') != 'profile_market'):
        raise ValueError('Market selection is pinned; create a new profile revision and worker')


def price_fingerprint(s=None):
    ctx=current_runtime()
    pref=s.get('export_price_profile','empirical_csud') if isinstance(s,dict) else 'empirical_csud'
    win=s.get('export_price_window','oct2025_sep2026') if isinstance(s,dict) else 'oct2025_sep2026'
    mode=s.get('export_price_mode','shape_scaled') if isinstance(s,dict) else 'shape_scaled'
    if ctx.household_profile is not None:
        market = ctx.household_profile['market']
        pref = market['series']; win = [market['window_start'],market['window_end']]; mode = market['valuation_mode']
    payload=json.dumps([ctx.key,ctx.price_revision,pref,win,mode],separators=(',',':')).encode('utf-8')
    return int.from_bytes(hashlib.sha256(payload).digest()[:8],'big',signed=True)

def estimated_baseline_cooling(d=None):
    """Estimate baseline summer cooling electricity from measured household source data.

    Uses max(0, August mean daily kWh - October mean daily kWh), normalizing each month
    by its actual calendar day coverage (31 days in August, 31 days in October) from the audited
    measured household electricity data. This August-minus-October subtraction serves
    as a practical proxy for summer cooling demand embedded in historical consumption,
    recognizing it may also include non-AC seasonal usage differences. Extrapolated across
    the 122 calendar days of the June–September summer window.
    """
    if d is None:d=data()
    if d['audit'].get('dataset_schema_version') == 2:
        return dict(summer_baseline_kwh=float(d['audit']['baseline_cooling_kwh']), method='explicit', inference_disabled=True)
    monthly=d['audit']['monthly_kwh']
    august_daily=float(monthly[7])/31.0
    october_daily=float(monthly[9])/31.0
    daily_cooling=max(0.0,august_daily-october_daily)
    summer_days=122
    summer_baseline_kwh=daily_cooling*summer_days
    return dict(august_daily_kwh=august_daily,october_daily_kwh=october_daily,daily_cooling_kwh=daily_cooling,summer_days=summer_days,summer_baseline_kwh=summer_baseline_kwh)


def _window_hours(on_h, off_h):
    """Active clock hours (0..23) for a start-inclusive, end-exclusive window.

    If on_h == off_h, the window is disabled (returns empty set).
    If on_h < off_h, returns set(range(on_h, off_h)).
    If on_h > off_h, wraps overnight: set(range(on_h, 24)) | set(range(0, off_h)).
    """
    if on_h == off_h:
        return set()
    if on_h < off_h:
        return set(range(on_h, off_h))
    return set(range(on_h, 24)) | set(range(0, off_h))


WeatherPeriod = namedtuple('WeatherPeriod', ['count', 'min_year', 'max_year', 'label'])


def weather_period(d=None):
    """Return summary metadata (count, minyear, maxyear, formatted_label) for available historical weather years."""
    if d is None:
        d = data()
    years = [int(y) for y in d.get('years', [])]
    count = len(years)
    min_year = min(years) if years else 0
    max_year = max(years) if years else 0
    label = f"{min_year}–{max_year}" if min_year != max_year else f"{min_year}"
    return WeatherPeriod(count, min_year, max_year, label)


@lru_cache(maxsize=4)
def _civil_hours_cached(years_key):
    rome = ZoneInfo('Europe/Rome')
    n_years = len(years_key)
    arr = np.full((n_years, 8784), -1, dtype=np.int16)
    for yi, y in enumerate(years_key):
        is_leap = calendar.isleap(y)
        n_hours = 8784 if is_leap else 8760
        base_utc = dt.datetime(y, 1, 1, 0, 10, tzinfo=dt.timezone.utc)
        for h in range(n_hours):
            arr[yi, h] = (base_utc + dt.timedelta(hours=h)).astimezone(rome).hour
    return arr


def _civil_hours(d=None):
    """Europe/Rome civil clock hours (0..23 with DST) aligned to PVGIS hourly slots."""
    if d is None:
        d = data()
    years_key = tuple(int(y) for y in d['years'])
    return _civil_hours_cached(years_key)


def _get_civil_hours(d):
    """Retrieve civil hours array, compatible with cached PVGIS data or test fixtures."""
    if 'civil_hour' in d:
        return d['civil_hour']
    cached = _civil_hours(d)
    if d['hour'].shape == cached.shape and np.array_equal(d['years'], data()['years']):
        return cached
    return d['hour']


@lru_cache(maxsize=4)
def _local_standard_dates_cached(years_key):
    dates = []
    for yi, y in enumerate(years_key):
        is_leap = calendar.isleap(y)
        n_hours = 8784 if is_leap else 8760
        base_utc = dt.datetime(y, 1, 1, 0, 10)
        y_dates = []
        for h in range(n_hours):
            local = base_utc + dt.timedelta(hours=h + 1)
            y_dates.append((local.date().isoformat(), local.hour, local.month))
        dates.append(tuple(y_dates))
    return tuple(dates)


def _local_standard_dates(d=None):
    """Local-standard date for each PVGIS slot, derived from its source timestamps."""
    if d is None:
        d = data()
    years = [int(y) for y in d['years']]
    years_key = tuple(years)
    dates = _local_standard_dates_cached(years_key)
    for yi, year in enumerate(years):
        is_leap = calendar.isleap(year)
        n_hours = 8784 if is_leap else 8760
        if len(dates[yi]) != n_hours:
            raise ValueError(f'PVGIS timestamp count does not match stored weather year {year}')
        if 'valid' in d and 'month' in d and 'hour' in d:
            valid_mask = d['valid'][yi, :n_hours]
            valid_indices = np.where(valid_mask)[0]
            slots = [dates[yi][i] for i in valid_indices]
            expected_month = np.array([row[2] for row in slots])
            expected_hour = np.array([row[1] for row in slots])
            actual_month = d['month'][yi, valid_indices]
            actual_hour = d['hour'][yi, valid_indices]
            if not np.array_equal(expected_month, actual_month) or not np.array_equal(expected_hour, actual_hour):
                raise ValueError(f'PVGIS local-standard month/hour timestamps do not match stored profiles for weather year {year}')
            if len(set((row[0], row[1]) for row in slots)) != len(slots):
                raise ValueError(f'Duplicate local-standard hourly slots for weather year {year}')
    return dates

@lru_cache(maxsize=128)
def _dayhour_trace_cached(runtime_key, key,weather_index,price_version=0):
    assert_runtime_key(runtime_key)
    s=thaw_settings(dict(key));d=data();load,ev,_=load_profiles(s);demand=load[weather_index]+ev[weather_index] if s['ev_start_year']<=1 else load[weather_index]
    pv=pv_profile(s)[weather_index]*(1-s['pv_first_year_loss'])
    s=dict(s);s['routing_priority']=resolve_routing_priority(s)
    _,trace=dispatch_year(pv,demand,s['battery_kwh'],s['charge_kw'],s['discharge_kw'],math.sqrt(s['roundtrip_efficiency']),s['inverter_kw'],0.,synthetic_prices()[weather_index],d['valid'][weather_index],s['pv_battery_coupling']=='dc_coupled',physical_parameters(s),d['month'][weather_index],d['hour'][weather_index])
    return trace

def _dayhour_trace(key,weather_index,price_version=0):
    return _dayhour_trace_cached(current_runtime().key, freeze(key),weather_index,price_version)
_dayhour_trace.cache_clear = _dayhour_trace_cached.cache_clear
_dayhour_trace.cache_info = _dayhour_trace_cached.cache_info

def _battery_trace_loss(s,charge,discharge):
    efficiency=math.sqrt(s['roundtrip_efficiency'])
    if s['pv_dispatch_model']!='physical':
        return charge*(1-efficiency)+discharge*(1/efficiency-1)
    eta=s['inverter_efficiency']
    eta_ch=s['battery_charge_efficiency'] or efficiency
    eta_dis=s['battery_discharge_efficiency'] or efficiency
    dc=s['pv_battery_coupling']=='dc_coupled' and s['battery_kwh']>0
    ch_dc=charge if dc else charge*eta
    dis_dc=discharge/eta
    return (ch_dc*(1-eta_ch)+(0. if dc else ch_dc*(1/eta-1))
            +dis_dc*(1/eta_dis-1)+dis_dc*(1-eta))


def _trace_routing_telemetry(s,pv,load,trace,months,hours,valid=None):
    physical=s['pv_dispatch_model']=='physical'
    dc=physical and s['pv_battery_coupling']=='dc_coupled' and s['battery_kwh']>0
    eta=s['inverter_efficiency']
    rec=np.minimum(np.maximum((pv-s['inverter_kw'])/eta,0.),np.maximum(trace[:,6],0.)) if dc else np.zeros(len(pv))
    ceiling=soc_ceiling_series(pv,load,s['battery_kwh'],s['charge_kw'],s['inverter_kw'],eta,
                               s['battery_charge_efficiency'] or math.sqrt(s['roundtrip_efficiency']),
                               dc,physical_parameters(s),months,hours,valid=valid)
    return ceiling,rec

MONTH_NAMES=('January','February','March','April','May','June','July','August','September','October','November','December')

def dayhour(settings,weather_year,date_text,hour=None,period='day',month=None):
    """Bounded day slice from a complete first-year dispatch; SOC carries across midnight."""
    if period is None:
        period = 'month' if (date_text is None and month is not None) else 'day'
    if period not in ('day','daily','month','monthly'):
        raise ValueError('period must be day or month')
    if period in ('month','monthly') or (date_text is None and month is not None):
        return dayhour_monthly(settings,weather_year,month,hour=hour)
    s=_routing_settings(validate(settings));d=data();years=[int(y) for y in d['years']]
    if isinstance(weather_year,bool) or not isinstance(weather_year,int) or weather_year not in years:raise ValueError('weather_year must be one of '+', '.join(map(str,years)))
    try:day=dt.date.fromisoformat(date_text)
    except (ValueError,TypeError):raise ValueError('date must be an ISO calendar date')
    if day.year!=weather_year:raise ValueError('date year must match the selected weather year')
    if hour is not None and (isinstance(hour,bool) or not isinstance(hour,int) or not 0<=hour<=23):raise ValueError('hour must be an integer from 0 to 23')
    wi=years.index(weather_year);timeline=_local_standard_dates()[wi]
    indices=[i for i,(stamp,_,_) in enumerate(timeline) if stamp==date_text]
    if not indices:raise ValueError(f'{date_text} is unavailable in weather year {weather_year}; source data may not include that local-standard boundary hour')
    key=_energy_key(s);version=price_fingerprint(s)
    trace=_dayhour_trace(key,wi,version);eff=math.sqrt(s['roundtrip_efficiency']);records=[]
    resolved=resolve_routing_priority(s);s_route={**s,'routing_priority':resolved}
    load_all,ev_all,_=load_profiles(s_route)
    demand_all=(load_all[wi]+ev_all[wi]) if s['ev_start_year']<=1 else load_all[wi]
    pv_all=pv_profile(s_route)[wi]*(1-s['pv_first_year_loss'])
    eta=float(s['inverter_efficiency'])
    dc_eff=bool(s['pv_dispatch_model']=='physical' and s['pv_battery_coupling']=='dc_coupled' and s['battery_kwh']>0)
    ceiling_all=soc_ceiling_series(pv_all,demand_all,s['battery_kwh'],s['charge_kw'],s['inverter_kw'],eta,
                                   float(s['battery_charge_efficiency'] or eff),dc_eff,
                                   physical_parameters(s_route),d['month'][wi],d['hour'][wi],valid=d['valid'][wi])
    if dc_eff:
        rec_all=np.minimum(np.maximum(0.0,(pv_all-float(s['inverter_kw']))/eta),np.maximum(trace[:,6],0.))
    else:
        rec_all=np.zeros(len(pv_all))
    for i in indices:
        _,hh,_=timeline[i];pv,load,imp,export,soc,direct,charge,discharge=map(float,trace[i])
        previous=float(trace[i-1,4]) if i>0 else 0.
        loss=_battery_trace_loss(s,charge,discharge)
        records.append(dict(hour=hh,pv_kwh=pv,load_kwh=load,direct_kwh=direct,charge_input_kwh=charge,discharge_delivered_kwh=discharge,grid_import_kwh=imp,grid_export_kwh=export,battery_loss_kwh=loss,soc_start_kwh=previous,soc_end_kwh=soc,balance_error_kwh=pv+imp-load-export-loss-(soc-previous),soc_ceiling_sub_kwh=float(ceiling_all[i]),charged_from_recaptured_clipping_kwh=float(rec_all[i]),charged_from_normal_solar_kwh=float(max(trace[i,6],0.)-rec_all[i])))
    active_hp = _uses_heat_pump(s)
    end_use_totals = None
    hp_totals = None
    if active_hp:
        from feasibility.heatpump_integration import get_source_heat_pump_hourly, calculate_cooking_electricity
        from feasibility.heatpump_reporting import (
            extract_single_hour_heat_pump,
            build_additive_end_uses,
            compute_reporting_totals,
        )
        hp_source = get_source_heat_pump_hourly(s)
        gas_alloc = hp_source['gas_allocation']
        cook_arr, _, _ = calculate_cooking_electricity(s, gas_alloc, d['valid'], d['month'], d['hour'])
        _, ev_arr, _ = load_profiles(s)
        ev_enabled = (s.get('load_scenario') == 'ev') and (s.get('ev_start_year', 1) <= 1)
        hp_arrays = {k: hp_source[k][wi] for k in hp_source if isinstance(hp_source[k], np.ndarray) and hp_source[k].ndim == 2}

        for idx, i in enumerate(indices):
            load_kwh = records[idx]['load_kwh']
            c_kwh = float(cook_arr[wi, i])
            e_kwh = float(ev_arr[wi, i]) if ev_enabled else 0.0
            hp_rec = extract_single_hour_heat_pump(hp_arrays, i)
            end_uses = build_additive_end_uses(load_kwh, hp_rec, c_kwh, e_kwh)
            records[idx]['end_uses'] = end_uses
            records[idx]['heat_pump'] = hp_rec

        end_use_totals, hp_totals = compute_reporting_totals(records)
    totals={name:float(sum(row[name] for row in records)) for name in ['pv_kwh','load_kwh','direct_kwh','charge_input_kwh','discharge_delivered_kwh','grid_import_kwh','grid_export_kwh','battery_loss_kwh','charged_from_recaptured_clipping_kwh','charged_from_normal_solar_kwh']}
    selected=next((row for row in records if row['hour']==hour),None) if hour is not None else None
    ret = dict(period='day',weather_year=weather_year,date=date_text,timezone='UTC+01:00 fixed local standard time',resolution='Hourly energy intervals; PVGIS hourly and measured 15-minute household data summed hourly.',settings=s,resolved_routing_priority=resolved,available_hours=[row['hour'] for row in records],unavailable_hours=sorted(set(range(24))-{row['hour'] for row in records}),hours=records,totals=totals,battery_capacity_kwh=s['battery_kwh'],soc_start_kwh=records[0]['soc_start_kwh'],soc_end_kwh=records[-1]['soc_end_kwh'],max_balance_error_kwh=max(abs(row['balance_error_kwh']) for row in records),selected_hour=selected,final_local_slot_outside_picker=timeline[-1][0],first_year=True)
    if active_hp:
        ret['end_use_totals'] = end_use_totals
        ret['hp_totals'] = hp_totals
        ret['operating_clock'] = 'Europe/Rome civil clock for heating/reheat/hygiene operating schedules; hour field is fixed UTC+01:00 local standard time for continuous plotting.'
    return ret

def _soc_daily_stats(soc_values, battery_capacity_kwh):
    """Within-month marginal day SOC statistics for a single clock hour.

    Calculates median, 10th percentile, 90th percentile, and full-battery day count
    across the individual days in the selected month for the chosen coherent weather run.
    Full means end-of-hour SOC at selected usable capacity within numerical tolerance.
    Zero battery shows 0 full days, 0 full fraction, and 0 quantiles.
    """
    cap = float(battery_capacity_kwh) if battery_capacity_kwh is not None else 0.0
    k = len(soc_values)
    if k == 0:
        return {
            'soc_end_median_kwh': 0.0,
            'soc_end_p10_kwh': 0.0,
            'soc_end_p90_kwh': 0.0,
            'soc_full_days': 0,
            'soc_sample_days': 0,
            'soc_full_fraction': 0.0,
        }
    vals = np.asarray(soc_values, dtype=float)
    if cap <= 1e-6:
        return {
            'soc_end_median_kwh': 0.0,
            'soc_end_p10_kwh': 0.0,
            'soc_end_p90_kwh': 0.0,
            'soc_full_days': 0,
            'soc_sample_days': int(k),
            'soc_full_fraction': 0.0,
        }
    p10, p50, p90 = np.percentile(vals, [10, 50, 90])
    p10 = float(np.clip(p10, 0.0, cap))
    p50 = float(np.clip(p50, 0.0, cap))
    p90 = float(np.clip(p90, 0.0, cap))
    tol = max(1e-8, cap * 1e-8)
    full_days = int(np.sum(vals >= (cap - tol)))
    full_fraction = float(full_days / k) if k > 0 else 0.0
    return {
        'soc_end_median_kwh': p50,
        'soc_end_p10_kwh': p10,
        'soc_end_p90_kwh': p90,
        'soc_full_days': full_days,
        'soc_sample_days': int(k),
        'soc_full_fraction': full_fraction,
    }

def dayhour_monthly(settings,weather_year,month,hour=None):
    """Monthly hourly average from a complete first-year dispatch; uninterrupted annual timeline."""
    s=_routing_settings(validate(settings));d=data();years=[int(y) for y in d['years']]
    if isinstance(weather_year,bool) or not isinstance(weather_year,int) or weather_year not in years:
        raise ValueError('weather_year must be one of '+', '.join(map(str,years)))
    if isinstance(month,bool) or not isinstance(month,int) or not 1<=month<=12:
        raise ValueError('month must be an integer from 1 to 12')
    if hour is not None and (isinstance(hour,bool) or not isinstance(hour,int) or not 0<=hour<=23):
        raise ValueError('hour must be an integer from 0 to 23')
    wi=years.index(weather_year);timeline=_local_standard_dates()[wi]
    indices=[i for i,(stamp,_,mm) in enumerate(timeline) if int(stamp.split('-')[0])==weather_year and mm==month]
    if not indices:
        raise ValueError(f'Month {month} in weather year {weather_year} is unavailable in source data')
    key=_energy_key(s);version=price_fingerprint(s)
    trace=_dayhour_trace(key,wi,version);eff=math.sqrt(s['roundtrip_efficiency'])
    resolved=resolve_routing_priority(s);s_route={**s,'routing_priority':resolved}
    load_all,ev_all,_=load_profiles(s_route)
    demand_all=(load_all[wi]+ev_all[wi]) if s['ev_start_year']<=1 else load_all[wi]
    pv_all=pv_profile(s_route)[wi]*(1-s['pv_first_year_loss'])
    eta_inv=float(s['inverter_efficiency'])
    dc_eff=bool(s['pv_dispatch_model']=='physical' and s['pv_battery_coupling']=='dc_coupled' and s['battery_kwh']>0)
    ceiling_all=soc_ceiling_series(pv_all,demand_all,s['battery_kwh'],s['charge_kw'],s['inverter_kw'],eta_inv,
                                   float(s['battery_charge_efficiency'] or eff),dc_eff,
                                   physical_parameters(s_route),d['month'][wi],d['hour'][wi],valid=d['valid'][wi])
    if dc_eff:
        rec_all=np.minimum(np.maximum(0.0,(pv_all-float(s['inverter_kw']))/eta_inv),np.maximum(trace[:,6],0.))
    else:
        rec_all=np.zeros(len(pv_all))
    active_hp = _uses_heat_pump(s)
    if active_hp:
        from feasibility.heatpump_integration import get_source_heat_pump_hourly, calculate_cooking_electricity
        from feasibility.heatpump_reporting import (
            extract_single_hour_heat_pump,
            build_additive_end_uses,
            aggregate_month_hour_records,
            compute_reporting_totals,
        )
        hp_source = get_source_heat_pump_hourly(s)
        gas_alloc = hp_source['gas_allocation']
        cook_arr, _, _ = calculate_cooking_electricity(s, gas_alloc, d['valid'], d['month'], d['hour'])
        _, ev_arr, _ = load_profiles(s)
        ev_enabled = (s.get('load_scenario') == 'ev') and (s.get('ev_start_year', 1) <= 1)
        hp_arrays = {k: hp_source[k][wi] for k in hp_source if isinstance(hp_source[k], np.ndarray) and hp_source[k].ndim == 2}
    hour_buckets=[[] for _ in range(24)]
    interval_records=[]
    for i in indices:
        _,hh,_=timeline[i];pv,load,imp,export,soc,direct,charge,discharge=map(float,trace[i])
        previous=float(trace[i-1,4]) if i>0 else 0.
        loss=_battery_trace_loss(s,charge,discharge)
        err=pv+imp-load-export-loss-(soc-previous)
        rec=dict(pv=pv,load=load,direct=direct,charge=charge,discharge=discharge,imp=imp,export=export,loss=loss,previous=previous,soc=soc,error=err,ceiling=float(ceiling_all[i]),rec=float(rec_all[i]),normal=float(max(trace[i,6],0.)-rec_all[i]))
        if active_hp:
            hp_rec = extract_single_hour_heat_pump(hp_arrays, i)
            c_kwh = float(cook_arr[wi, i])
            e_kwh = float(ev_arr[wi, i]) if ev_enabled else 0.0
            end_uses = build_additive_end_uses(load, hp_rec, c_kwh, e_kwh)
            rec['end_uses'] = end_uses
            rec['heat_pump'] = hp_rec
        hour_buckets[hh].append(rec);interval_records.append(rec)
    records=[]
    for hh in range(24):
        bucket=hour_buckets[hh]
        if not bucket:continue
        k=len(bucket)
        m_pv=sum(r['pv'] for r in bucket)/k
        m_load=sum(r['load'] for r in bucket)/k
        m_direct=sum(r['direct'] for r in bucket)/k
        m_charge=sum(r['charge'] for r in bucket)/k
        m_discharge=sum(r['discharge'] for r in bucket)/k
        m_imp=sum(r['imp'] for r in bucket)/k
        m_export=sum(r['export'] for r in bucket)/k
        m_loss=sum(r['loss'] for r in bucket)/k
        m_start=sum(r['previous'] for r in bucket)/k
        m_end=sum(r['soc'] for r in bucket)/k
        m_err=m_pv+m_imp-m_load-m_export-m_loss-(m_end-m_start)
        soc_stats=_soc_daily_stats([r['soc'] for r in bucket],s['battery_kwh'])
        records.append(dict(
            hour=hh,pv_kwh=m_pv,load_kwh=m_load,direct_kwh=m_direct,
            charge_input_kwh=m_charge,discharge_delivered_kwh=m_discharge,
            grid_import_kwh=m_imp,grid_export_kwh=m_export,battery_loss_kwh=m_loss,
            soc_start_kwh=m_start,soc_end_kwh=m_end,balance_error_kwh=m_err,sample_count=k,
            soc_end_median_kwh=soc_stats['soc_end_median_kwh'],
            soc_end_p10_kwh=soc_stats['soc_end_p10_kwh'],
            soc_end_p90_kwh=soc_stats['soc_end_p90_kwh'],
            soc_full_days=soc_stats['soc_full_days'],
            soc_sample_days=soc_stats['soc_sample_days'],
            soc_full_fraction=soc_stats['soc_full_fraction'],
            soc_daily_stats=soc_stats,
            soc_ceiling_sub_kwh=sum(r['ceiling'] for r in bucket)/k,
            charged_from_recaptured_clipping_kwh=sum(r['rec'] for r in bucket)/k,
            charged_from_normal_solar_kwh=sum(r['normal'] for r in bucket)/k,
        ))
    end_use_totals = None
    hp_totals = None
    if active_hp:
        aggregate_month_hour_records(hour_buckets, records)
        end_use_totals, hp_totals = compute_reporting_totals(interval_records)
    totals={
        'pv_kwh':float(sum(r['pv'] for r in interval_records)),
        'load_kwh':float(sum(r['load'] for r in interval_records)),
        'direct_kwh':float(sum(r['direct'] for r in interval_records)),
        'charge_input_kwh':float(sum(r['charge'] for r in interval_records)),
        'discharge_delivered_kwh':float(sum(r['discharge'] for r in interval_records)),
        'grid_import_kwh':float(sum(r['imp'] for r in interval_records)),
        'grid_export_kwh':float(sum(r['export'] for r in interval_records)),
        'battery_loss_kwh':float(sum(r['loss'] for r in interval_records)),
        'charged_from_recaptured_clipping_kwh':float(sum(r['rec'] for r in interval_records)),
        'charged_from_normal_solar_kwh':float(sum(r['normal'] for r in interval_records)),
    }
    flow_keys=['pv_kwh','load_kwh','direct_kwh','charge_input_kwh','discharge_delivered_kwh','grid_import_kwh','grid_export_kwh','battery_loss_kwh']
    is_leap=(weather_year%4==0 and weather_year%100!=0) or (weather_year%400==0)
    days_in_month=29 if (month==2 and is_leap) else (28 if month==2 else (30 if month in (4,6,9,11) else 31))
    daily_averages={name:float(totals[name]/days_in_month) for name in flow_keys}
    hourly_averages={name:float(totals[name]/len(indices)) for name in flow_keys}
    selected=next((row for row in records if row['hour']==hour),None) if hour is not None else None
    ret = dict(
        period='month',
        month=month,
        month_name=MONTH_NAMES[month-1],
        weather_year=weather_year,
        year=weather_year,
        date=f'{weather_year}-{month:02d}',
        timezone='UTC+01:00 fixed local standard time',
        resolution=f'Monthly average by hour ({days_in_month} calendar days, {len(indices)} hourly intervals; {len(indices)}/{days_in_month*24} covered intervals); PVGIS hourly and measured 15-minute household data summed hourly.',
        settings=s,
        resolved_routing_priority=resolved,
        available_hours=[row['hour'] for row in records],
        unavailable_hours=sorted(set(range(24))-{row['hour'] for row in records}),
        hours=records,
        totals=totals,
        month_totals=totals,
        daily_averages=daily_averages,
        hourly_averages=hourly_averages,
        days_in_month=days_in_month,
        total_samples=len(indices),
        battery_capacity_kwh=s['battery_kwh'],
        soc_start_kwh=records[0]['soc_start_kwh'],
        soc_end_kwh=records[-1]['soc_end_kwh'],
        mean_soc_kwh=float(np.mean([r['soc'] for r in interval_records])),
        first_interval_soc_start_kwh=float(trace[indices[0]-1,4]) if indices[0]>0 else 0.,
        last_interval_soc_end_kwh=float(trace[indices[-1],4]),
        max_balance_error_kwh=float(max(abs(r['error']) for r in interval_records)),
        max_mean_balance_error_kwh=float(max(abs(row['balance_error_kwh']) for row in records)),
        selected_hour=selected,
        first_year=True,
        coverage=dict(
            days=days_in_month,
            valid_samples=len(indices),
            total_possible_samples=days_in_month*24,
            missing_samples=days_in_month*24-len(indices),
            partial=len(indices)<days_in_month*24,
            sample_counts={str(row['hour']):row['sample_count'] for row in records}
        )
    )
    if active_hp:
        ret['end_use_totals'] = end_use_totals
        ret['hp_totals'] = hp_totals
        ret['operating_clock'] = 'Europe/Rome civil clock for heating/reheat/hygiene operating schedules; hour field is fixed UTC+01:00 local standard time for continuous plotting.'
    return ret


def _normalized_pattern(weights,total,valid):
    weights=np.where(valid,np.maximum(weights,0),0)
    denominator=weights.sum(axis=1)
    return np.divide(weights*total,denominator[:,None],out=np.zeros_like(weights,float),where=denominator[:,None]>0)


def _ac_heating_budget(s, additional_demand_pct=0.0, gas_monthly=None):
    """Authoritative pure budget calculation for air-to-air AC space heating.

    Returns explicit monthly arrays:
      - 'heating_gas_smc': Monthly natural gas displaced for space heating [Smc/month]
      - 'baseline_useful_heat_kwh': Monthly baseline useful thermal heating delivered [kWh_th/month]
      - 'requested_useful_heat_kwh': Monthly requested useful thermal heating delivered [kWh_th/month]
        (scaled by 1 + additional_demand_pct / 100)
      - 'baseline_heating_electricity_kwh': Monthly baseline heat pump electricity demand [kWh_e/month]
        (baseline_useful_heat_kwh / SCOP)
      - 'requested_heating_electricity_kwh': Monthly requested heat pump electricity demand [kWh_e/month]
        (requested_useful_heat_kwh / SCOP)

    Arithmetic order:
      heating_gas_smc = GAS_MONTHLY - minimum(GAS_MONTHLY, s['summer_gas_base'])
      baseline_useful_heat_kwh = heating_gas_smc * s['gas_pcs'] * s['boiler_efficiency']
      requested_useful_heat_kwh = baseline_useful_heat_kwh * (1 + p / 100)
      baseline_heating_electricity_kwh = baseline_useful_heat_kwh / s['scop']
      requested_heating_electricity_kwh = requested_useful_heat_kwh / s['scop']

    At additional_demand_pct=0.0, this preserves bit-for-bit equivalence with the original
    production expression heating * pcs * eff / scop (atol 1e-12 / rtol 0).
    """
    if isinstance(additional_demand_pct, bool) or not isinstance(
        additional_demand_pct, (int, float, np.integer, np.floating)
    ):
        raise TypeError(
            f"additional_demand_pct must be numeric and not boolean, got {type(additional_demand_pct).__name__}"
        )
    p = float(additional_demand_pct)
    if not math.isfinite(p):
        raise ValueError(f"additional_demand_pct must be finite, got {p}")
    if p < 0.0:
        raise ValueError(f"additional_demand_pct must be non-negative, got {p}")

    if gas_monthly is None and _thermal_reference() is not None:
        use = _thermal_reference().end_uses.get('space_heat')
        useful = _thermal_useful(s, 'space_heat', changed_only=True)
        requested = useful * (1.0 + p / 100.0)
        return dict(heating_gas_smc=(np.asarray(use.monthly_input) if use is not None and
                    use.carrier == 'gas' and use.monthly_input is not None else None),
                    baseline_useful_heat_kwh=useful, requested_useful_heat_kwh=requested,
                    baseline_heating_electricity_kwh=useful / s['scop'],
                    requested_heating_electricity_kwh=requested / s['scop'])
    if gas_monthly is None:
        gas_monthly = current_runtime().thermal_reference['GAS_MONTHLY']
    gas = np.atleast_1d(np.asarray(gas_monthly, dtype=float))

    summer = np.minimum(gas, s['summer_gas_base'])
    heating_gas_smc = gas - summer
    baseline_useful_heat_kwh = heating_gas_smc * s['gas_pcs'] * s['boiler_efficiency']
    requested_useful_heat_kwh = baseline_useful_heat_kwh * (1.0 + (p / 100.0))
    baseline_heating_electricity_kwh = baseline_useful_heat_kwh / s['scop']
    requested_heating_electricity_kwh = requested_useful_heat_kwh / s['scop']

    return {
        'heating_gas_smc': heating_gas_smc,
        'baseline_useful_heat_kwh': baseline_useful_heat_kwh,
        'requested_useful_heat_kwh': requested_useful_heat_kwh,
        'baseline_heating_electricity_kwh': baseline_heating_electricity_kwh,
        'requested_heating_electricity_kwh': requested_heating_electricity_kwh,
    }


ac_heating_budget = _ac_heating_budget


def _allocate_space_heating(
    s, temperature, months, hours, valid, civil_hours, weather_years, return_diagnostics=False
):
    """Allocate space heating electricity across active operating hours.

    Conserves ensemble mean monthly useful heat budgets while gating heating
    to permitted operating windows (Europe/Rome civil clock in civil mode, fixed UTC+1
    in standard mode). Temperature-dependent hourly COP is calibrated to the
    configured SCOP across the full weather ensemble.
    """
    if s.get('load_scenario') == 'baseline':
        heat_profile = np.zeros_like(temperature, dtype=float)
        if not return_diagnostics:
            return heat_profile
        nominal_daily = 24.0 if s.get('heating_schedule_mode') == 'continuous' else 0.0
        n_years = len(weather_years)
        realizations = [
            {
                'weather_year': int(weather_years[yi]),
                'eligible_schedule_duration_hours': 0.0,
                'max_modeled_interval_average_electrical_demand_kw': 0.0,
                'max_modeled_permitted_duration_electrical_requirement_kw': 0.0,
                'max_modeled_permitted_duration_useful_heat_requirement_kw': 0.0,
            }
            for yi in range(n_years)
        ]
        diagnostics = {
            'nominal_daily_union_duration_hours': float(nominal_daily),
            'realizations': realizations,
            'fallback_events': [],
            'limitation': (
                "equipment adequacy unassessed; hourly aggregation cannot resolve "
                "within-hour solar coincidence or instantaneous/cycling peaks."
            ),
            'max_modeled_interval_average_electrical_demand_kw': 0.0,
            'max_modeled_permitted_duration_electrical_requirement_kw': 0.0,
            'max_modeled_permitted_duration_useful_heat_requirement_kw': 0.0,
        }
        return heat_profile, diagnostics

    ac_budget = _ac_heating_budget(
        s, additional_demand_pct=s.get('ac_heating_additional_demand_pct', 0)
    )
    monthly_useful_heat = ac_budget['requested_useful_heat_kwh']

    if s['heating_schedule_mode'] == 'continuous':
        nominal_daily_union_duration_hours = 24.0
        heating_mask = valid
        slot_weights = np.where(valid, 1.0, 0.0)
    else:
        if 'ac_heating_windows' in s:
            windows = s['ac_heating_windows']
        else:
            windows = migrate_and_resolve_schedule(s)
        hourly_overlaps = canonical_windows_hourly_overlaps(windows)
        nominal_daily_union_duration_hours = float(hourly_overlaps.sum() / 60.0)
        if monthly_useful_heat.sum() > 0 and hourly_overlaps.sum() == 0:
            raise ValueError("Add at least one operating window to allocate the requested AC heating demand.")
        if monthly_useful_heat.sum() == 0 or hourly_overlaps.sum() == 0:
            heat_profile = np.zeros_like(temperature, dtype=float)
            if not return_diagnostics:
                return heat_profile
            n_years = len(weather_years)
            hourly_fractions = hourly_overlaps / 60.0
            heat_h = civil_hours if s['timezone_mode'] == 'civil' else hours
            slot_weights = hourly_fractions[heat_h]
            realizations = []
            for yi in range(n_years):
                val = valid[yi] if valid.ndim > 1 else valid
                frac = slot_weights[yi] if slot_weights.ndim > 1 else slot_weights
                elig = float(np.sum(frac[val])) if np.any(val) else 0.0
                realizations.append({
                    'weather_year': int(weather_years[yi]),
                    'eligible_schedule_duration_hours': elig,
                    'max_modeled_interval_average_electrical_demand_kw': 0.0,
                    'max_modeled_permitted_duration_electrical_requirement_kw': 0.0,
                    'max_modeled_permitted_duration_useful_heat_requirement_kw': 0.0,
                })
            diagnostics = {
                'nominal_daily_union_duration_hours': nominal_daily_union_duration_hours,
                'realizations': realizations,
                'fallback_events': [],
                'limitation': (
                    "equipment adequacy unassessed; hourly aggregation cannot resolve "
                    "within-hour solar coincidence or instantaneous/cycling peaks."
                ),
                'max_modeled_interval_average_electrical_demand_kw': 0.0,
                'max_modeled_permitted_duration_electrical_requirement_kw': 0.0,
                'max_modeled_permitted_duration_useful_heat_requirement_kw': 0.0,
            }
            return heat_profile, diagnostics

        hourly_fractions = hourly_overlaps / 60.0
        heat_h = civil_hours if s['timezone_mode'] == 'civil' else hours
        slot_weights = hourly_fractions[heat_h]
        heating_mask = (slot_weights > 0) & valid

    n_years = len(weather_years)
    q_profile = np.zeros_like(temperature, dtype=float)
    for mm in range(1, 13):
        use = valid & (months == mm)
        q_budget = monthly_useful_heat[mm - 1]
        if q_budget > 0:
            heat_use = use & heating_mask
            degrees = np.zeros_like(temperature, dtype=float)
            for yi in range(n_years):
                act = heat_use[yi] if heat_use.ndim > 1 else heat_use
                if not np.any(act):
                    raise ValueError(
                        f"No active valid heating hours in month {mm} for weather year "
                        f"{weather_years[yi]} with positive heating budget {q_budget:.2f} kWh"
                    )
                frac = slot_weights[yi] if slot_weights.ndim > 1 else slot_weights
                temp_yi = temperature[yi] if temperature.ndim > 1 else temperature
                deg = np.where(act, frac * np.maximum(s['heating_balance_temp'] - temp_yi, 0.0), 0.0)
                if degrees.ndim > 1:
                    degrees[yi] = deg
                else:
                    degrees = deg

            mean_hdh = float(np.sum(degrees)) / n_years
            if mean_hdh > 0.0:
                q_profile += q_budget * (degrees / mean_hdh)

    scop = float(s.get('scop', 1.0))
    if scop <= 0.0:
        scop = 1.0
    raw_cop = np.clip(1.0 + 0.025 * (temperature - 7.0), 0.45, 1.60)
    total_q = float(np.sum(q_profile))
    if total_q > 0.0:
        sum_inv = float(np.sum(q_profile / raw_cop))
        spf_raw = total_q / sum_inv if sum_inv > 0.0 else 1.0
        heat_profile = np.where(q_profile > 0.0, (q_profile * spf_raw) / (scop * raw_cop), 0.0)
    else:
        heat_profile = np.zeros_like(temperature, dtype=float)

    if not return_diagnostics:
        return heat_profile

    realizations = []
    for yi in range(n_years):
        val = valid[yi] if valid.ndim > 1 else valid
        frac = slot_weights[yi] if slot_weights.ndim > 1 else slot_weights
        prof = heat_profile[yi] if heat_profile.ndim > 1 else heat_profile
        q_prof = q_profile[yi] if q_profile.ndim > 1 else q_profile
        elig_hours = float(np.sum(frac[val])) if np.any(val) else 0.0
        max_int_avg = float(np.max(prof[val])) if np.any(val) and len(prof) > 0 else 0.0
        max_int_avg = max(0.0, max_int_avg)

        permitted_mask = (frac > 0) & val
        if np.any(permitted_mask):
            req_powers = prof[permitted_mask] / frac[permitted_mask]
            max_req = float(np.max(req_powers))
            max_req = max(0.0, max_req)
            q_powers = q_prof[permitted_mask] / frac[permitted_mask]
            max_useful = float(np.max(q_powers))
            max_useful = max(0.0, max_useful)
        else:
            max_req = 0.0
            max_useful = 0.0

        realizations.append({
            'weather_year': int(weather_years[yi]),
            'eligible_schedule_duration_hours': elig_hours,
            'max_modeled_interval_average_electrical_demand_kw': max_int_avg,
            'max_modeled_permitted_duration_electrical_requirement_kw': max_req,
            'max_modeled_permitted_duration_useful_heat_requirement_kw': max_useful,
        })

    max_int_avg_all = max((r['max_modeled_interval_average_electrical_demand_kw'] for r in realizations), default=0.0)
    max_p_all = max((r['max_modeled_permitted_duration_electrical_requirement_kw'] for r in realizations), default=0.0)
    max_useful_all = max((r['max_modeled_permitted_duration_useful_heat_requirement_kw'] for r in realizations), default=0.0)

    diagnostics = {
        'nominal_daily_union_duration_hours': float(nominal_daily_union_duration_hours),
        'realizations': realizations,
        'fallback_events': [],
        'limitation': (
            "equipment adequacy unassessed; hourly aggregation cannot resolve "
            "within-hour solar coincidence or instantaneous/cycling peaks."
        ),
        'max_modeled_interval_average_electrical_demand_kw': float(max_int_avg_all),
        'max_modeled_permitted_duration_electrical_requirement_kw': float(max_p_all),
        'max_modeled_permitted_duration_useful_heat_requirement_kw': float(max_useful_all),
    }
    return heat_profile, diagnostics


@lru_cache(maxsize=16)
def _forecast_calendar_mapping(runtime_key, forecast_year):
    """Deterministic mapping and target clock hours for the forecast_year calendar."""
    assert_runtime_key(runtime_key)
    is_target_leap = calendar.isleap(forecast_year)
    n_hours = 8784 if is_target_leap else 8760
    d = data()
    years = list(map(int, d['years']))
    timeline = _local_standard_dates()

    start = dt.datetime(forecast_year, 1, 1, 0, 0, tzinfo=dt.timezone(dt.timedelta(hours=1)))
    rome = ZoneInfo('Europe/Rome')
    one_h = dt.timedelta(hours=1)
    
    target_std_hours = np.zeros(n_hours, dtype=np.int16)
    target_civil_hours = np.zeros(n_hours, dtype=np.int16)
    target_months = np.zeros(n_hours, dtype=np.int16)
    
    cur = start
    for i in range(n_hours):
        target_std_hours[i] = cur.hour
        target_months[i] = cur.month
        target_civil_hours[i] = cur.astimezone(rome).hour
        cur += one_h

    mappings = []
    for wi, sy in enumerate(years):
        index = {(stamp, hh): i for i, (stamp, hh, _) in enumerate(timeline[wi])}
        p_slots = [len(timeline[wi]) - 1]
        s_slots = [len(timeline[wi]) - 1]
        p_weights = [1.0]
        s_weights = [0.0]
        is_source_leap = calendar.isleap(sy)
        for m in range(1, 13):
            days = 31 if m in (1, 3, 5, 7, 8, 10, 12) else 30 if m in (4, 6, 9, 11) else (29 if is_target_leap else 28)
            for d_num in range(1, days + 1):
                hours = range(1, 24) if m == 1 and d_num == 1 else range(24)
                if m == 2 and d_num == 29 and not is_source_leap:
                    # Non-leap source year mapped to leap target Feb 29: deterministic average of Feb 28 and Mar 1
                    for hh in hours:
                        idx28 = index[(f'{sy:04d}-02-28', hh)]
                        idx01 = index[(f'{sy:04d}-03-01', hh)]
                        p_slots.append(idx28)
                        s_slots.append(idx01)
                        p_weights.append(0.5)
                        s_weights.append(0.5)
                else:
                    stamp = f'{sy:04d}-{m:02d}-{d_num:02d}'
                    for hh in hours:
                        idx = index[(stamp, hh)]
                        p_slots.append(idx)
                        s_slots.append(idx)
                        p_weights.append(1.0)
                        s_weights.append(0.0)
        assert len(p_slots) == n_hours
        mappings.append((
            np.array(p_slots, dtype=np.int32),
            np.array(s_slots, dtype=np.int32),
            np.array(p_weights, dtype=float),
            np.array(s_weights, dtype=float)
        ))
    return mappings, target_std_hours, target_civil_hours, target_months, is_target_leap, n_hours


def _apply_mapping(source_arr, mapping):
    ps, ss, pw, sw = mapping
    if np.all(pw == 1.0):
        return source_arr[ps]
    return source_arr[ps] * pw + source_arr[ss] * sw


@lru_cache(maxsize=16)
def _forecast_calendar_slots(runtime_key, target_year=None):
    """Deterministic slot indices mapping each PVGIS source year to the target calendar."""
    assert_runtime_key(runtime_key)
    if target_year is None:
        target_year = 2027
    mapping_data = _forecast_calendar_mapping(current_runtime().key, target_year)
    return [mapping[0].tolist() for mapping in mapping_data[0]]


def _forecast_thermal_series(s, d, forecast_year, mapping_info):
    mappings, target_std_hours, target_civil_hours, target_months, leap, target_len = mapping_info
    years = d['years']
    timeline = _local_standard_dates(d)
    def mapped(source, conserve=False):
        out = np.zeros((len(years), target_len))
        for yi in range(len(years)):
            out[yi] = _apply_mapping(source[yi], mappings[yi])
            if conserve:
                source_months = np.array([row[2] for row in timeline[yi]])
                for mm in range(1, 13):
                    selected = target_months == mm
                    wanted = source[yi, :len(source_months)][source_months == mm].sum()
                    got = out[yi, selected].sum()
                    if got > 0:
                        out[yi, selected] *= wanted / got
                    elif wanted > 0:
                        raise ValueError('Mapped thermal input loses monthly coverage')
        return out
    base = mapped(d['load_'+('civil' if s['timezone_mode']=='civil' else 'standard')] * s['load_growth'], True)
    temp = mapped(d['temperature'])
    valid = np.ones_like(base, dtype=bool)
    hp = None
    active_hp = bool(s.get('planning_heat_pump')) and any(
        _thermal_action(s,k) != 'unchanged' for k in ('space_heat','dhw'))
    if active_hp:
        from .heatpump_integration import get_forecast_heat_pump_hourly
        hp_result = get_forecast_heat_pump_hourly(s, forecast_year, temp, target_std_hours,
            target_civil_hours, target_months, target_len)
        hp = hp_result['mapped_hp_breakdown']
    proposed, _ = _thermal_proposed(s, temp, target_months, target_std_hours, valid,
        target_civil_hours, years, hp)
    existing = {k: mapped(a, True) for k,a in _thermal_existing(s).items()}
    non_ev = _thermal_apply_delta(s, base, proposed, existing)
    total = _thermal_apply_delta(s, base, proposed, existing, include_ev=True)
    ev = total - non_ev
    # Preserve the existing EV lifetime control without re-adding embedded EV.
    load = total if s['ev_start_year'] <= 1 else non_ev
    pv_raw = mapped(pv_profile(s))
    heating = proposed.get('space_heat', np.zeros_like(base))
    result = dict(forecast_year=forecast_year, is_leap=leap, target_len=target_len,
        mappings=mappings, target_months=target_months, target_std_hours=target_std_hours,
        target_civil_hours=target_civil_hours, mapped_pv_raw=pv_raw,
        mapped_pv=pv_raw*(1-s['pv_first_year_loss']), mapped_load=load, mapped_load_base=non_ev,
        mapped_ev=ev if s['ev_start_year'] <= 1 else np.zeros_like(ev), mapped_ev_lifetime=ev,
        mapped_pzo=mapped(price_profile(s)[0]), mapped_temp=temp, mapped_heating=heating)
    if hp is not None:
        result.update(mapped_hp_breakdown=hp, mapped_heating=hp['total_electricity_kwh'],
                      mapped_cooking=proposed.get('cooking', np.zeros_like(base)),
                      mapped_household=non_ev-hp['total_electricity_kwh']-proposed.get('cooking', 0.))
    return result


def _forecast_mapped_series(s):
    d = data()
    years = list(map(int, d['years']))
    timeline = _local_standard_dates()
    forecast_year = int(s.get('installation_year', 2026)) + 1
    mappings, target_std_hours, target_civil_hours, target_months, is_target_leap, target_len = _forecast_calendar_mapping(current_runtime().key, forecast_year)
    if _thermal_reference() is not None:
        return _forecast_thermal_series(s, d, forecast_year, (mappings, target_std_hours, target_civil_hours, target_months, is_target_leap, target_len))
    
    pv_source = pv_profile(s)
    load_source, ev_source, _ = load_profiles(s)
    ev_enabled = s['ev_start_year'] <= 1
    pzo_source = price_profile(s)[0]
    
    mapped_pv_raw = np.zeros((len(years), target_len))
    mapped_pv = np.zeros((len(years), target_len))
    mapped_pzo = np.zeros((len(years), target_len))
    mapped_temp = np.zeros((len(years), target_len))
    mapped_load_base = np.zeros((len(years), target_len))
    mapped_ev = np.zeros((len(years), target_len))
    mapped_load = np.zeros((len(years), target_len))
    
    use_heat_pump = _uses_heat_pump(s)
    
    if not use_heat_pump:
        heating_source = _allocate_space_heating(s, d['temperature'], d['month'], d['hour'], d['valid'], _get_civil_hours(d), d['years'])
        nonheating_source = load_source - heating_source
        
        for wi in range(len(years)):
            mapping = mappings[wi]
            mapped_pv_raw[wi] = _apply_mapping(pv_source[wi], mapping)
            mapped_pv[wi] = mapped_pv_raw[wi] * (1 - s['pv_first_year_loss'])
            mapped_pzo[wi] = _apply_mapping(pzo_source[wi], mapping)
            mapped_temp[wi] = _apply_mapping(d['temperature'][wi], mapping)
            
            m_nonheat = _apply_mapping(nonheating_source[wi], mapping)
            m_ev = _apply_mapping(ev_source[wi], mapping)
            
            source_months = np.array([row[2] for row in timeline[wi]])
            for mm in range(1, 13):
                use = target_months == mm
                target_nh = float(nonheating_source[wi][:len(source_months)][source_months == mm].sum())
                got_nh = float(m_nonheat[use].sum())
                if got_nh > 0:
                    m_nonheat[use] *= target_nh / got_nh

                target_ev = float(ev_source[wi][:len(source_months)][source_months == mm].sum())
                got_ev = float(m_ev[use].sum())
                if got_ev > 0:
                    m_ev[use] *= target_ev / got_ev

            mapped_load_base[wi] = m_nonheat
            mapped_ev[wi] = m_ev

        valid_target = np.ones((len(years), target_len), dtype=bool)
        mapped_heating = _allocate_space_heating(
            s, mapped_temp, target_months, target_std_hours, valid_target, target_civil_hours, d['years']
        )
        mapped_load_base += mapped_heating
        mapped_load = mapped_load_base + (mapped_ev if ev_enabled else 0.0)

        return {
            'forecast_year': forecast_year,
            'is_leap': is_target_leap,
            'target_len': target_len,
            'mappings': mappings,
            'target_months': target_months,
            'target_std_hours': target_std_hours,
            'target_civil_hours': target_civil_hours,
            'mapped_pv_raw': mapped_pv_raw,
            'mapped_pv': mapped_pv,
            'mapped_load': mapped_load,
            'mapped_load_base': mapped_load_base,
            'mapped_ev': mapped_ev if ev_enabled else np.zeros_like(mapped_ev),
            'mapped_ev_lifetime': mapped_ev,
            'mapped_pzo': mapped_pzo,
            'mapped_temp': mapped_temp,
            'mapped_heating': mapped_heating,
        }
    else:
        from feasibility.heatpump_integration import (
            get_source_heat_pump_hourly,
            get_forecast_heat_pump_hourly,
            calculate_cooking_electricity,
        )
        hp_source = get_source_heat_pump_hourly(s)
        hp_source_elec = hp_source['total_electricity_kwh']
        nonhp_source = load_source - hp_source_elec
        
        gas_alloc = hp_source['gas_allocation']
        cook_source, _, _ = calculate_cooking_electricity(s, gas_alloc, d['valid'], d['month'], d['hour'])
        household_source = nonhp_source - cook_source
        
        mapped_household = np.zeros((len(years), target_len))
        mapped_cooking = np.zeros((len(years), target_len))

        for wi in range(len(years)):
            mapping = mappings[wi]
            mapped_pv_raw[wi] = _apply_mapping(pv_source[wi], mapping)
            mapped_pv[wi] = mapped_pv_raw[wi] * (1 - s['pv_first_year_loss'])
            mapped_pzo[wi] = _apply_mapping(pzo_source[wi], mapping)
            mapped_temp[wi] = _apply_mapping(d['temperature'][wi], mapping)
            
            m_hh = _apply_mapping(household_source[wi], mapping)
            m_ck = _apply_mapping(cook_source[wi], mapping)
            m_ev = _apply_mapping(ev_source[wi], mapping)
            
            source_months = np.array([row[2] for row in timeline[wi]])
            for mm in range(1, 13):
                use = target_months == mm
                target_hh = float(household_source[wi][:len(source_months)][source_months == mm].sum())
                got_hh = float(m_hh[use].sum())
                if got_hh > 0:
                    m_hh[use] *= target_hh / got_hh

                target_ck = float(cook_source[wi][:len(source_months)][source_months == mm].sum())
                got_ck = float(m_ck[use].sum())
                if got_ck > 0:
                    m_ck[use] *= target_ck / got_ck

                target_ev = float(ev_source[wi][:len(source_months)][source_months == mm].sum())
                got_ev = float(m_ev[use].sum())
                if got_ev > 0:
                    m_ev[use] *= target_ev / got_ev

            mapped_household[wi] = m_hh
            mapped_cooking[wi] = m_ck
            mapped_load_base[wi] = m_hh + m_ck
            mapped_ev[wi] = m_ev

        fc_hp = get_forecast_heat_pump_hourly(
            settings=s,
            forecast_year=forecast_year,
            mapped_temp=mapped_temp,
            target_std_hours=target_std_hours,
            target_civil_hours=target_civil_hours,
            target_months=target_months,
            target_len=target_len,
        )
        mapped_hp_elec = fc_hp['mapped_hp_electricity']
        mapped_load_base += mapped_hp_elec
        mapped_load = mapped_load_base + (mapped_ev if ev_enabled else 0.0)
        mapped_heating = mapped_hp_elec

        return {
            'forecast_year': forecast_year,
            'is_leap': is_target_leap,
            'target_len': target_len,
            'mappings': mappings,
            'target_months': target_months,
            'target_std_hours': target_std_hours,
            'target_civil_hours': target_civil_hours,
            'mapped_pv_raw': mapped_pv_raw,
            'mapped_pv': mapped_pv,
            'mapped_load': mapped_load,
            'mapped_load_base': mapped_load_base,
            'mapped_household': mapped_household,
            'mapped_cooking': mapped_cooking,
            'mapped_ev': mapped_ev if ev_enabled else np.zeros_like(mapped_ev),
            'mapped_ev_lifetime': mapped_ev,
            'mapped_pzo': mapped_pzo,
            'mapped_temp': mapped_temp,
            'mapped_heating': mapped_heating,
            'mapped_hp_breakdown': fc_hp['mapped_hp_breakdown'],
        }


def _map_pv_profile(pv_source, mappings, target_len):
    """Map PV profile array onto the target calendar slots."""
    mapped_pv = np.zeros((len(mappings), target_len), dtype=float)
    for wi, mapping in enumerate(mappings):
        mapped_pv[wi] = _apply_mapping(pv_source[wi], mapping)
    return mapped_pv


def _lifetime_mapped_inputs(s):
    """Return mapped canonical physical series and calendar arrays for lifetime dispatch."""
    mapped_data = _forecast_mapped_series(s)
    d = data()
    years = list(map(int, d['years']))
    target_len = mapped_data['target_len']
    mappings = mapped_data['mappings']
    return {
        'load': mapped_data['mapped_load_base'],
        'ev': mapped_data['mapped_ev_lifetime'],
        'valid': np.ones((len(years), target_len), dtype=bool),
        'pzo': mapped_data['mapped_pzo'],
        'month': np.tile(mapped_data['target_months'], (len(years), 1)),
        'hour': np.tile(mapped_data['target_std_hours'], (len(years), 1)),
        'mappings': mappings,
        'target_len': target_len,
        'years': years,
        'forecast_year': mapped_data['forecast_year'],
        'is_leap': mapped_data['is_leap'],
    }


@lru_cache(maxsize=128)
def _forecast_runs_cached(runtime_key, key, price_version=0):
    """Dispatch historic weather analogues over a complete, normalized forecast calendar."""
    assert_runtime_key(runtime_key)
    s = thaw_settings(dict(key))
    d = data()
    years = list(map(int, d['years']))
    s['routing_priority'] = resolve_routing_priority(s)
    mapped_data = _forecast_mapped_series(s)
    target_len = mapped_data['target_len']
    forecast_year = mapped_data['forecast_year']
    valid = np.ones(target_len, dtype=bool)
    ones_price = np.ones(target_len)
    params = physical_parameters(s)
    pv_batch = np.asarray(mapped_data['mapped_pv'])
    load_batch = np.asarray(mapped_data['mapped_load'])

    totals, batch_traces = dispatch_weather_years(
        pv_batch,
        load_batch,
        s['battery_kwh'],
        s['charge_kw'],
        s['discharge_kw'],
        math.sqrt(s['roundtrip_efficiency']),
        s['inverter_kw'],
        0.0,
        ones_price,
        valid,
        s['pv_battery_coupling'] == 'dc_coupled',
        params,
        mapped_data['target_months'],
        mapped_data['target_std_hours']
    )
    traces = [batch_traces[wi] for wi in range(len(years))]
    annual = []
    source_indices = []
    for wi, source_year in enumerate(years):
        total = totals[wi]
        annual.append(dict(
            weather_year=source_year,
            pv_kwh=float(total[1]),
            pv_input_kwh=float(mapped_data['mapped_pv'][wi].sum()),
            load_kwh=float(mapped_data['mapped_load'][wi].sum()),
            direct_kwh=float(total[2]),
            charge_input_kwh=float(total[3]),
            discharge_delivered_kwh=float(total[4]),
            grid_import_kwh=float(total[5]),
            grid_export_kwh=float(total[6]),
            battery_loss_kwh=float(total[7]),
            end_soc_kwh=float(total[8]),
            clipped_kwh=float(total[9]),
            max_balance_error_kwh=float(total[13])
        ))
        source_indices.append(mapped_data['mappings'][wi][0].tolist())
        
    yields = np.array([r['pv_kwh'] for r in annual])
    order = np.argsort(yields)
    def nearest(p):
        target = float(np.percentile(yields, p))
        return int(order[np.argmin(abs(yields[order] - target))])
    picks = {'low': nearest(10), 'median': nearest(50), 'high': nearest(90)}
    return dict(
        traces=traces,
        annual=annual,
        source_indices=source_indices,
        picks=picks,
        forecast_year=forecast_year,
        is_leap=mapped_data['is_leap'],
        target_len=target_len,
        mapped_data=mapped_data
    )


def _forecast_runs(key, price_version=0):
    return _forecast_runs_cached(current_runtime().key, freeze(key), price_version)
_forecast_runs.cache_clear = _forecast_runs_cached.cache_clear
_forecast_runs.cache_info = _forecast_runs_cached.cache_info


def _forecast_2027_runs(key, price_version=0):
    return _forecast_runs(freeze(key), price_version)

def _aggregate_dayhour_mean_records(run_responses, is_monthly=False):
    flow_fields = [
        "pv_kwh", "load_kwh", "direct_kwh", "charge_input_kwh",
        "discharge_delivered_kwh", "grid_import_kwh", "grid_export_kwh",
        "battery_loss_kwh", "soc_start_kwh", "soc_end_kwh",
        "soc_ceiling_sub_kwh", "charged_from_recaptured_clipping_kwh",
        "charged_from_normal_solar_kwh"
    ]
    hours = []
    for hh in range(24):
        rec = {"hour": hh}
        for f in flow_fields:
            vals = [run["hours"][hh].get(f) for run in run_responses if run["hours"][hh].get(f) is not None]
            rec[f] = float(np.mean(vals)) if vals else 0.0

        rec["balance_error_kwh"] = float(
            rec["pv_kwh"] + rec["grid_import_kwh"] - rec["load_kwh"] - rec["grid_export_kwh"] -
            rec["battery_loss_kwh"] - (rec["soc_end_kwh"] - rec["soc_start_kwh"])
        )

        if is_monthly:
            rec["sample_count"] = run_responses[0]["hours"][hh].get("sample_count", 0)
            weather_soc_means = [run["hours"][hh]["soc_end_kwh"] for run in run_responses]
            rec["soc_end_p10_kwh"] = float(np.percentile(weather_soc_means, 10))
            rec["soc_end_p90_kwh"] = float(np.percentile(weather_soc_means, 90))

        if "end_uses" in run_responses[0]["hours"][hh]:
            eu = {}
            for k in run_responses[0]["hours"][hh]["end_uses"]:
                vals = [run["hours"][hh]["end_uses"].get(k) for run in run_responses if run["hours"][hh]["end_uses"].get(k) is not None]
                eu[k] = float(np.mean(vals)) if vals else 0.0
            rec["end_uses"] = eu

        if "heat_pump" in run_responses[0]["hours"][hh]:
            hp = {}
            first_hp = run_responses[0]["hours"][hh]["heat_pump"]
            for k, val in first_hp.items():
                if isinstance(val, bool):
                    hp[k] = any(run["hours"][hh]["heat_pump"].get(k, False) for run in run_responses)
                elif isinstance(val, (int, float)):
                    vals = [run["hours"][hh]["heat_pump"][k] for run in run_responses if run["hours"][hh]["heat_pump"].get(k) is not None]
                    hp[k] = float(np.mean(vals)) if vals else 0.0
                else:
                    hp[k] = val
            rec["heat_pump"] = hp

        hours.append(rec)
    return hours


def _aggregate_dayhour_mean_totals(run_responses):
    totals = {}
    for k in run_responses[0]["totals"]:
        vals = [run["totals"].get(k) for run in run_responses if run["totals"].get(k) is not None]
        totals[k] = float(np.mean(vals)) if vals else 0.0

    end_use_totals = None
    hp_totals = None
    if "end_use_totals" in run_responses[0] and run_responses[0]["end_use_totals"] is not None:
        end_use_totals = {}
        for k in run_responses[0]["end_use_totals"]:
            vals = [run["end_use_totals"].get(k) for run in run_responses if run["end_use_totals"].get(k) is not None]
            end_use_totals[k] = float(np.mean(vals)) if vals else 0.0

    if "hp_totals" in run_responses[0] and run_responses[0]["hp_totals"] is not None:
        hp_totals = {}
        for k, val in run_responses[0]["hp_totals"].items():
            if isinstance(val, bool):
                hp_totals[k] = any(run["hp_totals"].get(k, False) for run in run_responses)
            elif isinstance(val, (int, float)):
                vals = [run["hp_totals"][k] for run in run_responses if run["hp_totals"].get(k) is not None]
                hp_totals[k] = float(np.mean(vals)) if vals else 0.0
            else:
                hp_totals[k] = val

    return totals, end_use_totals, hp_totals

def dayhour_forecast(settings,date_text,hour=None,scenario=None,representative='median',period='day',month=None,_weather_index=None):
    """First-year forecast using complete historical weather analogues, never a weather prediction."""
    if period is None:
        period = 'month' if (date_text is None and month is not None) else 'day'
    if period not in ('day','daily','month','monthly'):
        raise ValueError('period must be day or month')
    if period in ('month','monthly') or (date_text is None and month is not None):
        return dayhour_forecast_monthly(settings,month,hour=hour,scenario=scenario,representative=representative,_weather_index=_weather_index)
    requested=validate(settings);scenario=scenario or requested['load_scenario']
    if scenario not in ('baseline','electrified','ev'):raise ValueError('scenario must be baseline, electrified or ev')
    s=_routing_settings(validate({**requested,'load_scenario':scenario}))
    if representative not in ('low','median','high','mean'):raise ValueError('representative must be low, median, high or mean')
    forecast_year=int(s.get('installation_year',2026))+1
    is_leap=calendar.isleap(forecast_year)
    days_in_year=366 if is_leap else 365
    total_slots=days_in_year*24
    try:day=dt.date.fromisoformat(date_text)
    except (ValueError,TypeError):raise ValueError('date must be an ISO calendar date')
    if day.year!=forecast_year:raise ValueError(f'forecast date must be in {forecast_year}')
    if hour is not None and (isinstance(hour,bool) or not isinstance(hour,int) or not 0<=hour<=23):raise ValueError('hour must be an integer from 0 to 23')
    key=_energy_key(s);version=price_fingerprint(s)
    result=_forecast_2027_runs(key,version)
    if representative=='mean' and _weather_index is None:
        traces=result['traces']
        run_responses=[dayhour_forecast(settings,date_text,hour=hour,scenario=scenario,representative='median',period='day',_weather_index=wi) for wi in range(len(traces))]
        records=_aggregate_dayhour_mean_records(run_responses,is_monthly=False)
        totals,end_use_totals,hp_totals=_aggregate_dayhour_mean_totals(run_responses)
        offset=(day.timetuple().tm_yday-1)*24;indices=range(offset,offset+24)
        fields=['pv_kwh','load_kwh','direct_kwh','charge_input_kwh','discharge_delivered_kwh','grid_import_kwh','grid_export_kwh','soc_end_kwh']
        envelope=[]
        for i in indices:
            row={'hour':i%24}
            for field,col in zip(fields,[0,1,5,6,7,2,3,4]):
                vals=np.array([tr[i,col] for tr in traces]);row[field]={f'p{p}':float(np.percentile(vals,p)) for p in (10,50,90)}
            envelope.append(row)
        annual=[]
        for label,index in result['picks'].items():annual.append({**result['annual'][index],'label':label})
        selected=next((row for row in records if row['hour']==hour),None) if hour is not None else None
        active_hp=_uses_heat_pump(s)
        ret=dict(
            mode='forecast_2027',period='day',forecast_year=forecast_year,year=forecast_year,date=date_text,
            weather_year=None,representative='mean',aggregation='weather_mean',weather_count=len(traces),
            scenario=scenario,timezone='UTC+01:00 fixed local standard time',
            resolution=f'{total_slots:,} hourly intervals; {len(traces)}-year arithmetic mean across historical weather years mapped to the {"leap " if is_leap else "non-leap "}{forecast_year} calendar.',
            settings=s,resolved_routing_priority=resolve_routing_priority(s),available_hours=list(range(24)),unavailable_hours=[],
            hours=records,uncertainty_by_hour=envelope,annual_scenarios=annual,totals=totals,
            battery_capacity_kwh=s['battery_kwh'],soc_start_kwh=records[0]['soc_start_kwh'],soc_end_kwh=records[-1]['soc_end_kwh'],
            max_balance_error_kwh=float(max(abs(row['balance_error_kwh']) for row in records)),
            selected_hour=selected,first_year=True,
            forecast_note=f'These are {len(traces)}-year arithmetic means across historical weather-year analogues ({len(traces)} weather years) simulated over the {forecast_year} calendar, not an actual {forecast_year} weather forecast. Hourly energy flows and state of charge are arithmetic averages over all {len(traces)} analogues; hourly uncertainty ranges (P10–P90) are marginal across all {len(traces)} runs. Note that this composite mean profile is not a single chronological weather year. {"Feb 29 is synthesized for non-leap source years" if is_leap else "Feb 29 is omitted"}; monthly load normalization applies strictly to household, cooking, and EV baseline components; heat pump thermal demand and compressor electricity are dynamically recomputed hour-by-hour for each weather year analogue. Jan 1 00:00 uses the source year final local-standard slot.'
        )
        if active_hp:
            ret['end_use_totals']=end_use_totals
            ret['hp_totals']=hp_totals
            ret['operating_clock']='Europe/Rome civil clock for heating/reheat/hygiene operating schedules; hour field is fixed UTC+01:00 local standard time for continuous plotting.'
        return ret
    ri=_weather_index if _weather_index is not None else result['picks'][representative];trace=result['traces'][ri]
    offset=(day.timetuple().tm_yday-1)*24;indices=range(offset,offset+24);eff=math.sqrt(s['roundtrip_efficiency']);records=[]
    md=result['mapped_data']
    ceiling_all,rec_all=_trace_routing_telemetry(s,md['mapped_pv'][ri],md['mapped_load'][ri],trace,md['target_months'],md['target_std_hours'])
    for i in indices:
        hh=i%24;pv,load,imp,export,soc,direct,charge,discharge=map(float,trace[i]);previous=float(trace[i-1,4]) if i else 0.
        loss=_battery_trace_loss(s,charge,discharge)
        records.append(dict(hour=hh,pv_kwh=pv,load_kwh=load,direct_kwh=direct,charge_input_kwh=charge,discharge_delivered_kwh=discharge,grid_import_kwh=imp,grid_export_kwh=export,battery_loss_kwh=loss,soc_start_kwh=previous,soc_end_kwh=soc,balance_error_kwh=pv+imp-load-export-loss-(soc-previous),soc_ceiling_sub_kwh=float(ceiling_all[i]),charged_from_recaptured_clipping_kwh=float(rec_all[i]),charged_from_normal_solar_kwh=float(charge-rec_all[i])))
    active_hp = _uses_heat_pump(s)
    end_use_totals = None
    hp_totals = None
    if active_hp:
        from feasibility.heatpump_reporting import (
            extract_single_hour_heat_pump,
            build_additive_end_uses,
            compute_reporting_totals,
        )
        mapped_data = result['mapped_data']
        fc_breakdown = mapped_data['mapped_hp_breakdown']
        years = [int(y) for y in data()['years']]
        wi = years.index(result['annual'][ri]['weather_year'])
        hp_arrays = {k: fc_breakdown[k][wi] for k in fc_breakdown if isinstance(fc_breakdown[k], np.ndarray) and fc_breakdown[k].ndim == 2}
        ck_arr = mapped_data['mapped_cooking'][wi]
        ev_arr = mapped_data['mapped_ev'][wi]

        for idx, i in enumerate(indices):
            load_kwh = records[idx]['load_kwh']
            c_kwh = float(ck_arr[i])
            e_kwh = float(ev_arr[i])
            hp_rec = extract_single_hour_heat_pump(hp_arrays, i)
            end_uses = build_additive_end_uses(load_kwh, hp_rec, c_kwh, e_kwh)
            records[idx]['end_uses'] = end_uses
            records[idx]['heat_pump'] = hp_rec

        end_use_totals, hp_totals = compute_reporting_totals(records)
    fields=['pv_kwh','load_kwh','direct_kwh','charge_input_kwh','discharge_delivered_kwh','grid_import_kwh','grid_export_kwh','soc_end_kwh']
    envelope=[]
    if _weather_index is None:
        for i in indices:
            row={'hour':i%24}
            for field,col in zip(fields,[0,1,5,6,7,2,3,4]):
                vals=np.array([tr[i,col] for tr in result['traces']]);row[field]={f'p{p}':float(np.percentile(vals,p)) for p in (10,50,90)}
            envelope.append(row)
    annual=[]
    for label,index in result['picks'].items():annual.append({**result['annual'][index],'label':label})
    totals={name:float(sum(row[name] for row in records)) for name in ['pv_kwh','load_kwh','direct_kwh','charge_input_kwh','discharge_delivered_kwh','grid_import_kwh','grid_export_kwh','battery_loss_kwh']}
    selected=next((row for row in records if row['hour']==hour),None) if hour is not None else None
    ret = dict(
        mode='forecast_2027',period='day',forecast_year=forecast_year,year=forecast_year,date=date_text,weather_year=result['annual'][ri]['weather_year'],representative=representative,scenario=scenario,timezone='UTC+01:00 fixed local standard time',resolution=f'{total_slots:,} hourly intervals; PVGIS historical weather years mapped to the {"leap " if is_leap else "non-leap "}{forecast_year} calendar.',settings=s,resolved_routing_priority=resolve_routing_priority(s),available_hours=list(range(24)),unavailable_hours=[],hours=records,uncertainty_by_hour=envelope,annual_scenarios=annual,totals=totals,battery_capacity_kwh=s['battery_kwh'],soc_start_kwh=records[0]['soc_start_kwh'],soc_end_kwh=records[-1]['soc_end_kwh'],max_balance_error_kwh=max(abs(row['balance_error_kwh']) for row in records),selected_hour=selected,first_year=True,
        forecast_note=f'These are historical weather-year analogues simulated over the {forecast_year} calendar, not an actual {forecast_year} weather forecast. Low, median and high select coherent annual-PV-ranked weather runs; hourly uncertainty ranges are marginal across all {len(result["traces"])} runs. {"Feb 29 is synthesized for non-leap source years" if is_leap else "Feb 29 is omitted"}; monthly load normalization applies strictly to household, cooking, and EV baseline components; heat pump thermal demand and compressor electricity are dynamically recomputed hour-by-hour for each weather year analogue. Jan 1 00:00 uses the source year final local-standard slot.'
    )
    if active_hp:
        ret['end_use_totals'] = end_use_totals
        ret['hp_totals'] = hp_totals
        ret['operating_clock'] = 'Europe/Rome civil clock for heating/reheat/hygiene operating schedules; hour field is fixed UTC+01:00 local standard time for continuous plotting.'
    return ret

def dayhour_forecast_monthly(settings,month,hour=None,scenario=None,representative='median',_weather_index=None):
    """First-year forecast monthly average by hour using historical weather analogues."""
    requested=validate(settings);scenario=scenario or requested['load_scenario']
    if scenario not in ('baseline','electrified','ev'):raise ValueError('scenario must be baseline, electrified or ev')
    s=_routing_settings(validate({**requested,'load_scenario':scenario}))
    if representative not in ('low','median','high','mean'):raise ValueError('representative must be low, median, high or mean')
    if isinstance(month,bool) or not isinstance(month,int) or not 1<=month<=12:
        raise ValueError('month must be an integer from 1 to 12')
    if hour is not None and (isinstance(hour,bool) or not isinstance(hour,int) or not 0<=hour<=23):
        raise ValueError('hour must be an integer from 0 to 23')
    forecast_year=int(s.get('installation_year',2026))+1
    is_leap=calendar.isleap(forecast_year)
    days_in_month=(29 if is_leap else 28) if month==2 else (30 if month in (4,6,9,11) else 31)
    start_doy=dt.date(forecast_year,month,1).timetuple().tm_yday-1
    start_hour=start_doy*24
    end_hour=start_hour+days_in_month*24
    key=_energy_key(s);version=price_fingerprint(s)
    result=_forecast_2027_runs(key,version)
    if representative=='mean' and _weather_index is None:
        traces=result['traces']
        run_responses=[dayhour_forecast_monthly(settings,month,hour=hour,scenario=scenario,representative='median',_weather_index=wi) for wi in range(len(traces))]
        records=_aggregate_dayhour_mean_records(run_responses,is_monthly=True)
        totals,end_use_totals,hp_totals=_aggregate_dayhour_mean_totals(run_responses)
        flow_keys=['pv_kwh','load_kwh','direct_kwh','charge_input_kwh','discharge_delivered_kwh','grid_import_kwh','grid_export_kwh','battery_loss_kwh']
        daily_averages={name:float(totals[name]/days_in_month) for name in flow_keys}
        hourly_averages={name:float(totals[name]/(days_in_month*24)) for name in flow_keys}
        selected=next((row for row in records if row['hour']==hour),None) if hour is not None else None
        fields=['pv_kwh','load_kwh','direct_kwh','charge_input_kwh','discharge_delivered_kwh','grid_import_kwh','grid_export_kwh','soc_end_kwh']
        field_cols=[0,1,5,6,7,2,3,4]
        per_run_means=np.array([
            tr[start_hour:end_hour].reshape(days_in_month,24,8).mean(axis=0)
            for tr in traces
        ])
        uncertainty_by_hour=[]
        for hh in range(24):
            row={'hour':hh}
            for field,col in zip(fields,field_cols):
                vals=per_run_means[:,hh,col]
                row[field]={f'p{p}':float(np.percentile(vals,p)) for p in (10,50,90)}
            uncertainty_by_hour.append(row)
        annual=[]
        for label,index in result['picks'].items():annual.append({**result['annual'][index],'label':label})
        resolved=resolve_routing_priority(s)
        active_hp=_uses_heat_pump(s)
        ret=dict(
            mode='forecast_2027',
            period='month',
            month=month,
            month_name=MONTH_NAMES[month-1],
            forecast_year=forecast_year,
            year=forecast_year,
            date=f'{forecast_year}-{month:02d}',
            weather_year=None,
            representative='mean',
            aggregation='weather_mean',
            weather_count=len(traces),
            scenario=scenario,
            timezone='UTC+01:00 fixed local standard time',
            resolution=f'Monthly {len(traces)}-year weather mean by hour ({days_in_month} calendar days, {days_in_month*24} hourly intervals); PVGIS historical weather years mapped to the {"leap " if is_leap else "non-leap "}{forecast_year} calendar.',
            settings=s,
            resolved_routing_priority=resolved,
            available_hours=list(range(24)),
            unavailable_hours=[],
            hours=records,
            uncertainty_by_hour=uncertainty_by_hour,
            annual_scenarios=annual,
            totals=totals,
            month_totals=totals,
            daily_averages=daily_averages,
            hourly_averages=hourly_averages,
            days_in_month=days_in_month,
            total_samples=days_in_month*24,
            battery_capacity_kwh=s['battery_kwh'],
            soc_start_kwh=records[0]['soc_start_kwh'],
            soc_end_kwh=records[-1]['soc_end_kwh'],
            mean_soc_kwh=float(np.mean([r['soc_end_kwh'] for r in records])),
            first_interval_soc_start_kwh=float(np.mean([r['first_interval_soc_start_kwh'] for r in run_responses])),
            last_interval_soc_end_kwh=float(np.mean([r['last_interval_soc_end_kwh'] for r in run_responses])),
            max_balance_error_kwh=float(max(abs(row['balance_error_kwh']) for row in records)),
            max_mean_balance_error_kwh=float(max(abs(row['balance_error_kwh']) for row in records)),
            selected_hour=selected,
            first_year=True,
            forecast_note=f'These are {len(traces)}-year arithmetic means across historical weather-year analogues ({len(traces)} weather years) simulated over the {forecast_year} calendar, not an actual {forecast_year} weather forecast. Hourly energy flows and state of charge are arithmetic averages over all {len(traces)} analogues; hourly uncertainty ranges (P10–P90) reflect the marginal weather spread across each run’s monthly hourly mean. Note that this composite mean profile is not a single chronological weather year. {"Feb 29 is synthesized for non-leap source years" if is_leap else "Feb 29 is omitted"}; monthly load normalization applies strictly to household, cooking, and EV baseline components; heat pump thermal demand and compressor electricity are dynamically recomputed hour-by-hour for each weather year analogue.',
            coverage=run_responses[0]['coverage']
        )
        if active_hp:
            ret['end_use_totals']=end_use_totals
            ret['hp_totals']=hp_totals
            ret['operating_clock']='Europe/Rome civil clock for heating/reheat/hygiene operating schedules; hour field is fixed UTC+01:00 local standard time for continuous plotting.'
        return ret
    ri=_weather_index if _weather_index is not None else result['picks'][representative];trace=result['traces'][ri]
    eff=math.sqrt(s['roundtrip_efficiency'])
    resolved=resolve_routing_priority(s);s_route={**s,'routing_priority':resolved}
    _md=result['mapped_data']
    _pv_all=np.asarray(_md['mapped_pv'][ri],float)
    _demand_all=np.asarray(_md['mapped_load'][ri],float)
    _eta_inv=float(s['inverter_efficiency'])
    _dc_eff=bool(s['pv_dispatch_model']=='physical' and s['pv_battery_coupling']=='dc_coupled' and s['battery_kwh']>0)
    ceiling_all=soc_ceiling_series(_pv_all,_demand_all,s['battery_kwh'],s['charge_kw'],s['inverter_kw'],_eta_inv,
                                   float(s['battery_charge_efficiency'] or eff),_dc_eff,
                                   physical_parameters(s_route),_md['target_months'],_md['target_std_hours'])
    if _dc_eff:
        rec_all=np.minimum(np.maximum(0.0,(_pv_all-float(s['inverter_kw']))/_eta_inv),np.maximum(trace[:,6],0.))
    else:
        rec_all=np.zeros(len(_pv_all))
    days_in_month=(29 if is_leap else 28) if month==2 else (30 if month in (4,6,9,11) else 31)
    start_doy=dt.date(forecast_year,month,1).timetuple().tm_yday-1
    start_hour=start_doy*24
    end_hour=start_hour+days_in_month*24
    indices=range(start_hour,end_hour)
    active_hp = _uses_heat_pump(s)
    if active_hp:
        from feasibility.heatpump_reporting import (
            extract_single_hour_heat_pump,
            build_additive_end_uses,
            aggregate_month_hour_records,
            compute_reporting_totals,
        )
        mapped_data = result['mapped_data']
        fc_breakdown = mapped_data['mapped_hp_breakdown']
        years = [int(y) for y in data()['years']]
        wi = years.index(result['annual'][ri]['weather_year'])
        hp_arrays = {k: fc_breakdown[k][wi] for k in fc_breakdown if isinstance(fc_breakdown[k], np.ndarray) and fc_breakdown[k].ndim == 2}
        ck_arr = mapped_data['mapped_cooking'][wi]
        ev_arr = mapped_data['mapped_ev'][wi]
    hour_buckets=[[] for _ in range(24)]
    interval_records=[]
    for i in indices:
        hh=i%24
        pv,load,imp,export,soc,direct,charge,discharge=map(float,trace[i])
        previous=float(trace[i-1,4]) if i>0 else 0.
        loss=_battery_trace_loss(s,charge,discharge)
        err=pv+imp-load-export-loss-(soc-previous)
        rec=dict(pv=pv,load=load,direct=direct,charge=charge,discharge=discharge,imp=imp,export=export,loss=loss,previous=previous,soc=soc,error=err,ceiling=float(ceiling_all[i]),rec=float(rec_all[i]),normal=float(max(trace[i,6],0.)-rec_all[i]))
        if active_hp:
            hp_rec = extract_single_hour_heat_pump(hp_arrays, i)
            c_kwh = float(ck_arr[i])
            e_kwh = float(ev_arr[i])
            end_uses = build_additive_end_uses(load, hp_rec, c_kwh, e_kwh)
            rec['end_uses'] = end_uses
            rec['heat_pump'] = hp_rec
        hour_buckets[hh].append(rec);interval_records.append(rec)
    records=[]
    for hh in range(24):
        bucket=hour_buckets[hh]
        k=len(bucket)
        m_pv=sum(r['pv'] for r in bucket)/k
        m_load=sum(r['load'] for r in bucket)/k
        m_direct=sum(r['direct'] for r in bucket)/k
        m_charge=sum(r['charge'] for r in bucket)/k
        m_discharge=sum(r['discharge'] for r in bucket)/k
        m_imp=sum(r['imp'] for r in bucket)/k
        m_export=sum(r['export'] for r in bucket)/k
        m_loss=sum(r['loss'] for r in bucket)/k
        m_start=sum(r['previous'] for r in bucket)/k
        m_end=sum(r['soc'] for r in bucket)/k
        m_err=m_pv+m_imp-m_load-m_export-m_loss-(m_end-m_start)
        soc_stats=_soc_daily_stats([r['soc'] for r in bucket],s['battery_kwh'])
        records.append(dict(
            hour=hh,pv_kwh=m_pv,load_kwh=m_load,direct_kwh=m_direct,
            charge_input_kwh=m_charge,discharge_delivered_kwh=m_discharge,
            grid_import_kwh=m_imp,grid_export_kwh=m_export,battery_loss_kwh=m_loss,
            soc_start_kwh=m_start,soc_end_kwh=m_end,balance_error_kwh=m_err,sample_count=k,
            soc_end_median_kwh=soc_stats['soc_end_median_kwh'],
            soc_end_p10_kwh=soc_stats['soc_end_p10_kwh'],
            soc_end_p90_kwh=soc_stats['soc_end_p90_kwh'],
            soc_full_days=soc_stats['soc_full_days'],
            soc_sample_days=soc_stats['soc_sample_days'],
            soc_full_fraction=soc_stats['soc_full_fraction'],
            soc_daily_stats=soc_stats,
            soc_ceiling_sub_kwh=sum(r['ceiling'] for r in bucket)/k,
            charged_from_recaptured_clipping_kwh=sum(r['rec'] for r in bucket)/k,
            charged_from_normal_solar_kwh=sum(r['normal'] for r in bucket)/k,
        ))
    end_use_totals = None
    hp_totals = None
    if active_hp:
        aggregate_month_hour_records(hour_buckets, records)
        end_use_totals, hp_totals = compute_reporting_totals(interval_records)
    fields=['pv_kwh','load_kwh','direct_kwh','charge_input_kwh','discharge_delivered_kwh','grid_import_kwh','grid_export_kwh','soc_end_kwh']
    field_cols=[0,1,5,6,7,2,3,4]
    per_run_means=np.array([
        tr[start_hour:end_hour].reshape(days_in_month,24,8).mean(axis=0)
        for tr in result['traces']
    ])
    uncertainty_by_hour=[]
    if _weather_index is None:
        for hh in range(24):
            row={'hour':hh}
            for field,col in zip(fields,field_cols):
                vals=per_run_means[:,hh,col]
                row[field]={f'p{p}':float(np.percentile(vals,p)) for p in (10,50,90)}
            uncertainty_by_hour.append(row)
    annual=[]
    for label,index in result['picks'].items():annual.append({**result['annual'][index],'label':label})
    totals={
        'pv_kwh':float(sum(r['pv'] for r in interval_records)),
        'load_kwh':float(sum(r['load'] for r in interval_records)),
        'direct_kwh':float(sum(r['direct'] for r in interval_records)),
        'charge_input_kwh':float(sum(r['charge'] for r in interval_records)),
        'discharge_delivered_kwh':float(sum(r['discharge'] for r in interval_records)),
        'grid_import_kwh':float(sum(r['imp'] for r in interval_records)),
        'grid_export_kwh':float(sum(r['export'] for r in interval_records)),
        'battery_loss_kwh':float(sum(r['loss'] for r in interval_records)),
        'charged_from_recaptured_clipping_kwh':float(sum(r['rec'] for r in interval_records)),
        'charged_from_normal_solar_kwh':float(sum(r['normal'] for r in interval_records)),
    }
    flow_keys=['pv_kwh','load_kwh','direct_kwh','charge_input_kwh','discharge_delivered_kwh','grid_import_kwh','grid_export_kwh','battery_loss_kwh']
    daily_averages={name:float(totals[name]/days_in_month) for name in flow_keys}
    hourly_averages={name:float(totals[name]/(days_in_month*24)) for name in flow_keys}
    selected=next((row for row in records if row['hour']==hour),None) if hour is not None else None
    ret = dict(
        mode='forecast_2027',
        period='month',
        month=month,
        month_name=MONTH_NAMES[month-1],
        forecast_year=forecast_year,
        year=forecast_year,
        date=f'{forecast_year}-{month:02d}',
        weather_year=result['annual'][ri]['weather_year'],
        representative=representative,
        scenario=scenario,
        timezone='UTC+01:00 fixed local standard time',
        resolution=f'Monthly average by hour ({days_in_month} calendar days, {days_in_month*24} hourly intervals); PVGIS historical weather years mapped to the {"leap " if is_leap else "non-leap "}{forecast_year} calendar.',
        settings=s,
        resolved_routing_priority=resolved,
        available_hours=list(range(24)),
        unavailable_hours=[],
        hours=records,
        uncertainty_by_hour=uncertainty_by_hour,
        annual_scenarios=annual,
        totals=totals,
        month_totals=totals,
        daily_averages=daily_averages,
        hourly_averages=hourly_averages,
        days_in_month=days_in_month,
        total_samples=days_in_month*24,
        battery_capacity_kwh=s['battery_kwh'],
        soc_start_kwh=records[0]['soc_start_kwh'],
        soc_end_kwh=records[-1]['soc_end_kwh'],
        mean_soc_kwh=float(np.mean([r['soc'] for r in interval_records])),
        first_interval_soc_start_kwh=float(trace[start_hour-1,4]) if start_hour>0 else 0.,
        last_interval_soc_end_kwh=float(trace[end_hour-1,4]),
        max_balance_error_kwh=float(max(abs(r['error']) for r in interval_records)),
        max_mean_balance_error_kwh=float(max(abs(row['balance_error_kwh']) for row in records)),
        selected_hour=selected,
        first_year=True,
        forecast_note=f'These are monthly hourly means from historical weather-year analogues simulated over the {forecast_year} calendar, not an actual {forecast_year} weather forecast. Low, median and high select coherent annual-PV-ranked weather runs; hourly uncertainty ranges are marginal across all {len(result["traces"])} runs (computed by obtaining each analogue’s 24 monthly hourly means, then taking percentiles across runs). {"Feb 29 is synthesized for non-leap source years" if is_leap else "Feb 29 is omitted"}; monthly load normalization applies strictly to household, cooking, and EV baseline components; heat pump thermal demand and compressor electricity are dynamically recomputed hour-by-hour for each weather year analogue.',
        coverage=dict(
            days=days_in_month,
            valid_samples=days_in_month*24,
            total_possible_samples=days_in_month*24,
            missing_samples=0,
            partial=False,
            sample_counts={str(row['hour']):row['sample_count'] for row in records}
        )
    )
    if active_hp:
        ret['end_use_totals'] = end_use_totals
        ret['hp_totals'] = hp_totals
        ret['operating_clock'] = 'Europe/Rome civil clock for heating/reheat/hygiene operating schedules; hour field is fixed UTC+01:00 local standard time for continuous plotting.'
    return ret

def validate(settings=None, base=None, supplied=None):
    if settings is None: settings={}
    if not isinstance(settings,dict): raise ValueError('settings must be a JSON object')
    settings = dict(settings)
    ctx = runtime_if_initialized()
    validate_price_selection(settings, None if ctx is None else ctx.household_profile)
    settings.pop('retail_contract', None)
    for alias, canonical in HP_ALIASES.items():
        if alias in settings and canonical not in settings:
            settings[canonical] = settings[alias]
        settings.pop(alias, None)
    storage_cost_mode = settings.pop('storage_cost_mode', None)
    unknown=set(settings)-set(SPEC)-LEGACY_HEATING_KEYS-STRUCTURAL_SETTINGS-set(CONFIRMATIONS)-{_PV_YIELD_SETTING}
    if unknown: raise ValueError('Unknown settings: '+', '.join(sorted(unknown)))
    if _PV_YIELD_SETTING in settings:
        factor = settings[_PV_YIELD_SETTING]
        if isinstance(factor, bool) or not isinstance(factor, (int, float)) or not math.isfinite(factor) or not 0 <= factor <= 2:
            raise ValueError('PV yield multiplier must be a finite number between 0 and 2')
    roofs = _dynamic_roofs()
    if roofs is not None:
        if LEGACY_ROOF_KEYS.intersection(settings):
            raise ValueError('Legacy roof controls cannot be mixed with dynamic roof settings')
    elif 'roof_panels' in settings:
        raise ValueError('roof_panels requires a v2 household profile')
    s=dict(defaults() if base is None else base)
    s_base_windows = s.get('ac_heating_windows')
    if s_base_windows is None and any(k in s for k in LEGACY_HEATING_KEYS):
        s_base_windows = migrate_and_resolve_schedule(s)
    s['ac_heating_windows'] = migrate_and_resolve_schedule(settings, base_windows=s_base_windows)
    if 'heating_schedule_mode' in settings:
        mode = settings['heating_schedule_mode']
        if mode not in ('scheduled', 'continuous'):
            raise ValueError("heating_schedule_mode must be 'scheduled' or 'continuous'")
        s['heating_schedule_mode'] = mode
    clean_settings = {k: v for k, v in settings.items() if k not in LEGACY_HEATING_KEYS and k != 'ac_heating_windows'}
    s.update(clean_settings)
    validate_price_selection(s, None if ctx is None else ctx.household_profile)
    for k in LEGACY_HEATING_KEYS:
        s.pop(k, None)
    if storage_cost_mode is not None:
        if storage_cost_mode not in ('purchased', 'existing'):
            raise ValueError("storage_cost_mode must be 'purchased' or 'existing'")
        s['storage_cost_mode'] = storage_cost_mode
    if base is None and 'planning_heat_pump' not in settings:
        s['planning_heat_pump'] = False
    if supplied is None:
        supplied = set(settings)
    for n,(default,unit,bounds,group,description) in SPEC.items():
        if (ctx is not None and ctx.household_profile is not None and
                n in ('export_price_profile', 'export_price_window')):
            continue
        if n == 'ac_heating_windows' or (roofs is not None and n in LEGACY_ROOF_KEYS):
            continue
        v=s[n]
        if isinstance(default,bool):
            if not isinstance(v,bool):raise ValueError(n+' must be boolean')
        elif isinstance(default,str):
            if not isinstance(v,str):raise ValueError(n+' must be a string')
            if bounds is not None and v not in bounds:raise ValueError(n+' must be one of '+str(bounds))
        elif v is None and default is None:continue
        else:
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v):raise ValueError(n+' must be a finite number')
            if bounds and (v<bounds[0] or (bounds[1] is not None and v>bounds[1])):raise ValueError(n+' outside modeling bounds '+str(bounds))
            if n in INTEGER_NAMES and int(v)!=v:raise ValueError(n+' must be an integer')
            s[n]=int(v) if n in INTEGER_NAMES else float(v)
    if s['deduction_rate'] not in (0,.36,.5):raise ValueError('deduction_rate must be 0, 0.36 or 0.50')
    if s['export_irpef']+s['regional_additional_tax']+s['municipal_additional_tax']>=1:raise ValueError('Combined export tax must be below 100%')
    # Battery normalization and validation
    _normalize_battery(s, supplied)
    battery_num_keys = ['battery_modules', 'battery_module_kwh', 'battery_kwh', 'battery_secondary_modules', 'battery_secondary_module_kwh']
    if not all(math.isfinite(s[k]) for k in battery_num_keys) or any(s[k] < 0 for k in battery_num_keys):
        raise ValueError('Battery counts and capacities must be nonnegative')
    if s['battery_mixed_sizes']:
        if s['battery_secondary_modules'] > s['battery_modules']:
            raise ValueError('Secondary battery module count cannot exceed total battery modules')
        first_modules = s['battery_modules'] - s['battery_secondary_modules']
        if first_modules > 0 and s['battery_module_kwh'] <= 0:
            raise ValueError('First battery module size must be strictly positive when used')
        if s['battery_secondary_modules'] > 0 and s['battery_secondary_module_kwh'] <= 0:
            raise ValueError('Secondary battery module size must be strictly positive when used')
        if s['battery_modules'] == 0:
            s['battery_kwh'] = 0.0
    if roofs is not None:
        from .roof_config import validate_roof_counts
        s['roof_panels'] = dict(validate_roof_counts(current_runtime().household_profile,
                                s.get('roof_panels', {}), s['panel_watts']))
        planes = data()['audit']['plane_ids']
        if any(count and roof not in planes for roof, count in s['roof_panels'].items()):
            raise ValueError('Selected roof has no compiled solar generation; regenerate solar data')
        for name in LEGACY_ROOF_KEYS:
            s.pop(name, None)
    count=_panel_total(s)
    if not count and s['battery_kwh']>0:raise ValueError('Battery without PV is outside this no-grid-charging model')
    if roofs is None and s['dormer_south_proxy'] and (s['west_panels'] or s['east_panels'] or s['flat_panels']):raise ValueError('31south proxy cannot include east/west/flat panels')
    if s['battery_soc_min'] >= s['battery_soc_max']:
        raise ValueError('battery_soc_min must be below battery_soc_max')
    if _thermal_reference() is not None:
        cooking = _thermal_reference().end_uses.get('cooking')
        gas_cooking = (float(sum(cooking.monthly_input)) if cooking is not None and
                       cooking.carrier == 'gas' and cooking.monthly_input is not None else 0.)
        validate_heat_pump_cross_fields({**s, 'cooking_smc_annual': gas_cooking}, supplied)
    else:
        validate_heat_pump_cross_fields(s, supplied)
    for key in CONFIRMATIONS:
        if key in s and type(s[key]) is not bool:
            raise ValueError(key + ' must be boolean')
    policy = current_policy()
    if not policy.legacy_mode:
        policy.settings(s)
        policy.settings(s, s['horizon_years'])
    return s

def _effective_pv_max_kwp(settings):
    """Resolve a blank/legacy PV limit to the AC inverter size for sizing only."""
    value = settings.get('pv_max_kwp')
    return float(settings['inverter_kw'] if value is None else value)

def electric_bill(kwh,s,year=1,power=None):
    return current_policy().electric_bill(kwh,s,year,power)

def current_invoice_check(s=None, kwh=None):
    if s is None:
        s=defaults()
    elif not isinstance(s, dict) or not (set(SPEC) -
            (LEGACY_ROOF_KEYS if _dynamic_roofs() is not None else set())).issubset(s):
        s=validate(s)
    if kwh is None:
        presentation = current_runtime().metadata.get('presentation')
        if presentation is None:
            return None
        kwh = presentation['electric_invoice_kwh']
    if type(kwh) not in (int,float) or not math.isfinite(kwh) or kwh < 0:
        raise ValueError('Invoice electricity must be finite and nonnegative')
    energy=round(kwh*s['retail_commodity'],2);regulated=round(kwh*s['retail_regulated'],2);excise=round(kwh*s['electric_excise'],2)
    fixed=round(s['supplier_fixed_month'],2)+round(s['network_fixed_month'],2)+round(s['power_fixed_kw_month']*s['baseline_power_kw'],2)
    taxable=energy+regulated+excise+fixed;vat=round(taxable*s['electric_vat'],2)
    return dict(kwh=kwh,commodity_eur=energy,regulated_eur=regulated,excise_eur=excise,fixed_eur=fixed,vat_eur=vat,total_eur=round(taxable+vat,2),rounding='Invoice components rounded to cents; annual dispatch billing retains precision.')

def gas_bill(s, year=1, smc=None):
    if smc is None and _thermal_reference() is not None:
        from .heatpump_finance import calculate_thermal_finance
        return calculate_thermal_finance({**s, 'load_scenario': 'baseline'}, year=year)['baseline_gas_cost_eur'] or 0.
    volume = _baseline_gas_volume() if smc is None else float(smc)
    if volume <= 0:
        return 0.0
    from .fiscal import gas_cost as calculate_gas_cost
    cost = calculate_gas_cost(
        volume=volume,
        days=365,
        year=year,
        prior_year_smc=0.0,
        include_fixed=True,
        settings=s,
    )
    return float(cost['total_eur'])

def gas_bill_breakdown(s, year=1, smc=None, prior_year_smc=0.0, include_fixed=True, days=365):
    if smc is None and _thermal_reference() is not None:
        uses = [u for u in _thermal_reference().end_uses.values() if u.carrier == 'gas']
        policy = current_policy()
        if uses and policy.parameters['mode'] == 'parameters':
            return policy.thermal_bill(dict(volume_smc=_thermal_reference().annual_gas_smc,
                prior_year_smc=prior_year_smc,include_fixed=include_fixed,days=days),s,year)
        return dict(applicable=bool(uses), total_eur=gas_bill(s, year) if uses else None,
                    volume_smc=_thermal_reference().annual_gas_smc if uses else None,
                    tax_breakdown_applicable=False, cost_basis='explicit_thermal_bills')
    volume = _baseline_gas_volume() if smc is None else float(smc)
    from .fiscal import gas_cost as calculate_gas_cost
    return calculate_gas_cost(
        volume=volume,
        days=days,
        year=year,
        prior_year_smc=prior_year_smc,
        include_fixed=include_fixed,
        settings=s,
    )

def build_gas_costs_audit(s):
    """Build first-year auditable gas cost breakdown, remaining scenario bill, reference comparison, and statutory tax metadata."""
    from feasibility.gas_taxes import get_gas_tax_metadata
    from .fiscal import gas_cost as calculate_gas_cost

    if _thermal_reference() is not None:
        from .heatpump_finance import calculate_thermal_finance
        costs = calculate_thermal_finance(s)
        applicable = costs['baseline_gas_cost_eur'] is not None
        return dict(year=1, applicable=applicable, baseline=gas_bill_breakdown(s),
            remaining=dict(total_eur=costs['remaining_gas_cost_eur']),
            connection_status=('not_applicable' if not applicable else
                'disconnected' if costs['gas_disconnected'] else 'retained'),
            reference_bill=None, tax_rules=current_policy().provenance() if applicable else None,
            notes=['Explicit household thermal costs and selected fiscal mode; no inherited invoice.'])

    # 1. Baseline: explicit annual input, 365 days, prior 0, year 1
    baseline_smc = _baseline_gas_volume()
    baseline_breakdown = calculate_gas_cost(
        volume=baseline_smc,
        days=365,
        year=1,
        prior_year_smc=0.0,
        include_fixed=True,
        settings=s,
    )
    baseline_breakdown['connection_status'] = 'connected'

    # 2. Remaining scenario bill
    load_scenario = s.get('load_scenario', 'baseline')
    active_hp = bool(s.get('planning_heat_pump', False)) and (load_scenario != 'baseline')

    if load_scenario == 'baseline':
        remaining_breakdown = deepcopy(baseline_breakdown)
        connection_status = 'connected'
        remaining_breakdown['connection_status'] = 'connected'
    elif not active_hp:
        # Non-HP electrified or EV: gas service eliminated
        remaining_breakdown = calculate_gas_cost(
            volume=0.0,
            days=365,
            year=1,
            prior_year_smc=0.0,
            include_fixed=False,
            settings=s,
        )
        connection_status = 'disconnected'
        remaining_breakdown['connection_status'] = 'disconnected'
    else:
        # Heat pump scenario
        if s.get('hp_disconnect_gas', False):
            remaining_breakdown = calculate_gas_cost(
                volume=0.0,
                days=365,
                year=1,
                prior_year_smc=0.0,
                include_fixed=False,
                settings=s,
            )
            connection_status = 'disconnected'
            remaining_breakdown['connection_status'] = 'disconnected'
        else:
            cooking_smc = 0.0 if s.get('hp_electric_cooking', False) else float(s.get('cooking_smc_annual', 20.0))
            if cooking_smc > 0.0:
                remaining_breakdown = calculate_gas_cost(
                    volume=cooking_smc,
                    days=365,
                    year=1,
                    prior_year_smc=0.0,
                    include_fixed=True,
                    settings=s,
                )
                connection_status = 'connected'
                remaining_breakdown['connection_status'] = 'connected'
            else:
                # Retained connection with zero volume: fixed connection fee only
                remaining_breakdown = calculate_gas_cost(
                    volume=0.0,
                    days=365,
                    year=1,
                    prior_year_smc=0.0,
                    include_fixed=True,
                    settings=s,
                )
                connection_status = 'retained_zero_volume'
                remaining_breakdown['connection_status'] = 'retained_zero_volume'

    reference = current_runtime().metadata['presentation']
    known_invoice = mutable_copy(reference['known_invoice'])
    modeled_reference = calculate_gas_cost(
        volume=known_invoice['volume_smc'],
        days=known_invoice['period_days'],
        year=1,
        prior_year_smc=reference['illustrative_prior_smc'],
        include_fixed=True,
        settings=s,
    )
    mod_cons = float(modeled_reference['variable_pretax_eur'])
    mod_fixed = float(modeled_reference['fixed_eur'])
    mod_exc = float(modeled_reference['national_excise_eur'] + modeled_reference['regional_surcharge_eur'])
    mod_vat = float(modeled_reference['vat_eur'])
    mod_tot = float(modeled_reference['total_eur'])

    residuals = {
        'consumption_eur': float(round(mod_cons - known_invoice['consumption_eur'], 4)),
        'fixed_eur': float(round(mod_fixed - known_invoice['fixed_eur'], 4)),
        'excise_including_regional_eur': float(round(mod_exc - known_invoice['excise_including_regional_eur'], 4)),
        'vat_eur': float(round(mod_vat - known_invoice['vat_eur'], 4)),
        'total_eur': float(round(mod_tot - known_invoice['total_eur'], 4)),
    }
    reference_notes = mutable_copy(reference['reference_notes'])
    reference_payload = {
        'invoice': known_invoice,
        'modeled': modeled_reference,
        'residuals': residuals,
        'illustrative_prior_smc': reference['illustrative_prior_smc'],
        'bracket_assumed': reference['bracket_assumed'],
        'notes': reference_notes,
    }

    # 4. Tax rules and audit notes
    tax_rules = get_gas_tax_metadata()
    audit_notes = [
        "First-year (Year 1) statutory gas accounting based on current scenario settings.",
        reference['baseline_note'],
        f"Remaining scenario gas bill reflects '{load_scenario}' scenario (connection status: {connection_status}).",
        f"Statutory taxes include progressive national excise (Mezzogiorno), Campania regional surcharge (ARISGAN), and differentiated VAT (statutory defaults: 10% up to 480 Smc/year, 22% over and fixed charges; current settings: {baseline_breakdown['reduced_vat_rate']*100:.1f}% reduced, {baseline_breakdown['standard_vat_rate']*100:.1f}% standard).",
    ]

    return {
        'year': 1,
        'baseline': baseline_breakdown,
        'remaining': remaining_breakdown,
        'connection_status': connection_status,
        'reference_bill': reference_payload,
        'tax_rules': tax_rules,
        'notes': audit_notes,
    }

def _thermal_proposed(s, temperature, months, hours, valid, civil_hours, years, hp=None):
    """Proposed equipment electricity on an explicit source or forecast axis."""
    ref = _thermal_reference()
    arrays = {k: np.zeros_like(temperature, dtype=float) for k in ref.end_uses}
    diagnostics = {}
    active = lambda key: _thermal_action(s, key) != 'unchanged'
    if s.get('planning_heat_pump'):
        if hp is not None:
            heat = hp['space_compressor_electricity_kwh'] + hp['space_backup_electricity_kwh']
            dhw = hp['dhw_compressor_electricity_kwh'] + hp['dhw_backup_electricity_kwh']
            aux = hp['auxiliary_electricity_kwh']
            # Shared auxiliary electricity is counted once, against an active use.
            if active('space_heat'):
                arrays['space_heat'] = heat + aux + (dhw if not active('dhw') else 0.)
            if active('dhw'):
                arrays['dhw'] = dhw + (aux + heat if not active('space_heat') else 0.)
    elif active('space_heat'):
        arrays['space_heat'], diagnostics = _allocate_space_heating(
            s, temperature, months, hours, valid, civil_hours, years, return_diagnostics=True)
    for key in ('dhw', 'cooking'):
        if not active(key) or (key == 'dhw' and s.get('planning_heat_pump')):
            continue
        efficiency = (s['dhw_resistance_efficiency'] if s['dhw_heating_type'] == 'resistance'
                      else s['dhw_scop']) if key == 'dhw' else s['induction_efficiency']
        monthly = _thermal_useful(s, key) / efficiency
        for mm in range(1, 13):
            use = valid & (months == mm)
            if key == 'dhw':
                arrays[key] += _normalized_pattern(((hours >= 11) & (hours <= 15)).astype(float),
                    monthly[mm-1] * s['dhw_midday_fraction'], use)
                arrays[key] += _normalized_pattern((((hours >= 6) & (hours <= 8)) |
                    ((hours >= 18) & (hours <= 22))).astype(float),
                    monthly[mm-1] * (1-s['dhw_midday_fraction']), use)
            else:
                arrays[key] += _normalized_pattern((((hours >= 12) & (hours <= 13)) |
                    ((hours >= 19) & (hours <= 20))).astype(float), monthly[mm-1], use)
    for key in ('cooling', 'ev'):
        if not active(key):
            continue
        use = ref.end_uses[key]
        if use.incremental_annual_kwh is None:
            raise ValueError(f'{key} requires explicit proposed annual electricity')
        energy = use.incremental_annual_kwh
        if key == 'cooling':
            summer = valid & (months >= 6) & (months <= 9)
            weights = np.where(summer, np.maximum(temperature - COOLING_BALANCE_TEMP, 0.), 0.)
            for yi in range(len(years)):
                if not weights[yi].sum():
                    weights[yi, summer[yi]] = 1.
            arrays[key] = _normalized_pattern(weights, energy, summer)
        else:
            arrays[key] = (_normalized_pattern(((hours >= 11) & (hours <= 16)).astype(float),
                energy * s['ev_day_fraction'], valid) +
                _normalized_pattern(((hours >= 18) & (hours <= 23)).astype(float),
                energy * (1-s['ev_day_fraction']), valid))
    if float(s.get('summer_ac_multiplier', 1.)) != 1. or ref.summer_ac_multiplier != 1.:
        raise ValueError('v2 cooling requires explicit incremental energy; seasonal multipliers are unavailable')
    return arrays, diagnostics


def _thermal_apply_delta(s, base, proposed, existing, *, include_ev=False):
    from .thermal_baseline import replacement_delta
    ref = _thermal_reference()
    actions = {k: _thermal_action(s, k) for k in ref.end_uses if include_ev or k != 'ev'}
    result = np.array(base, copy=True)
    for yi in range(len(base)):
        old = {k: a[yi] for k, a in existing.items() if k in actions}
        old['household'] = base[yi]
        new = {k: proposed[k][yi] for k, mode in actions.items() if mode != 'unchanged'}
        result[yi] += replacement_delta(ref, actions, old, new)
    return result


def _thermal_existing(s):
    items = current_runtime().metadata.get('thermal_inputs', {}).get('separated_electricity', {})
    clock = 'civil' if s['timezone_mode'] == 'civil' else 'standard'
    return {k: np.asarray(v[clock]) * s['load_growth'] for k, v in items.items()}


def _thermal_provenance(value):
    from .profiles import profile_to_dict
    return profile_to_dict(value)


def _thermal_load_profiles(s, d, base):
    ref = _thermal_reference()
    hp = None
    active_hp = bool(s.get('planning_heat_pump')) and any(
        _thermal_action(s, k) != 'unchanged' for k in ('space_heat', 'dhw'))
    if active_hp:
        from .heatpump_integration import get_source_heat_pump_hourly
        hp = get_source_heat_pump_hourly(s)
    proposed, diagnostics = _thermal_proposed(s, d['temperature'], d['month'], d['hour'],
        d['valid'], _get_civil_hours(d), d['years'], hp)
    existing = _thermal_existing(s)
    result = _thermal_apply_delta(s, base, proposed, existing)
    # EV is a separate dispatch input with its existing lifetime start-year control.
    ev = np.zeros_like(base)
    if 'ev' in ref.end_uses:
        ev_actions = {k: ('unchanged' if k != 'ev' else _thermal_action(s, k)) for k in ref.end_uses}
        from .thermal_baseline import replacement_delta
        for yi in range(len(base)):
            old = {k: a[yi] for k, a in existing.items()}
            old['household'] = base[yi]
            new = {'ev': proposed['ev'][yi]} if ev_actions['ev'] != 'unchanged' else {}
            ev[yi] = replacement_delta(ref, ev_actions, old, new)
    # Validate all replacement claims together, including EV, before dispatch.
    _thermal_apply_delta(s, base, proposed, existing, include_ev=True)
    annual = lambda a: float(np.mean(np.sum(a, axis=1)))
    enduses = dict(heating_kwh=annual(proposed.get('space_heat', np.zeros_like(base))),
        dhw_kwh=annual(proposed.get('dhw', np.zeros_like(base))),
        cooking_kwh=annual(proposed.get('cooking', np.zeros_like(base))), ev_kwh=annual(ev),
        heating_smc=None, dhw_smc=None, cooking_smc=None,
        thermal_baseline={k: dict(carrier=v.carrier, monthly_useful_kwh=list(v.monthly_useful_kwh),
            monthly_carrier_input=None if v.monthly_input is None else list(v.monthly_input),
            carrier_unit=v.unit, included_in_electric_load=v.included_in_electric_load,
            action=_thermal_action(s, k), provenance=_thermal_provenance(v.provenance))
            for k, v in ref.end_uses.items()},
        additional_electricity_kwh=annual(result-base)+annual(ev),
        embedded_electricity_removed_kwh=sum(annual(existing[k]) for k in existing
            if _thermal_action(s,k) == 'replace'),
        consumption_quality=d['audit'].get('projection', {}),
        replacement_requirements='Embedded replacement requires an identified, aligned separated electrical artifact.',
        summer_ac_baseline_kwh=float(d['audit']['baseline_cooling_kwh']),
        summer_ac_extra_kwh=annual(proposed.get('cooling', np.zeros_like(base))),
        summer_ac_multiplier=1.)
    for key, field in (('space_heat','heating_smc'), ('dhw','dhw_smc'), ('cooking','cooking_smc')):
        use = ref.end_uses.get(key)
        if use is not None and use.carrier == 'gas' and use.monthly_input is not None:
            enduses[field] = float(sum(use.monthly_input))
    enduses['summer_ac_kwh'] = enduses['summer_ac_baseline_kwh'] + enduses['summer_ac_extra_kwh']
    if diagnostics:
        enduses['ac_heating_allocation_diagnostics'] = diagnostics
    if s.get('planning_heat_pump') and s['load_scenario'] != 'baseline':
        enduses['heat_pump'] = hp['summary'] if hp is not None else {'by_weather': [], 'metadata': {}}
    return result, ev, enduses


@lru_cache(maxsize=64)
def _load_cache_cached(runtime_key, key):
    assert_runtime_key(runtime_key)
    s=thaw_settings(dict(key));d=data();valid=d['valid'];m=d['month'];h=d['hour']
    base=d['load_'+('civil' if s['timezone_mode']=='civil' else 'standard')]*s['load_growth']
    if _thermal_reference() is not None:
        return _thermal_load_profiles(s, d, base)
    extra=np.zeros_like(base);enduses={'heating_smc':0.,'dhw_smc':0.,'cooking_smc':0.,'heating_kwh':0.,'dhw_kwh':0.,'cooking_kwh':0.}
    use_heat_pump = _uses_heat_pump(s)
    if s['load_scenario']!='baseline':
        if use_heat_pump:
            from feasibility.heatpump_integration import get_source_heat_pump_hourly, calculate_cooking_electricity
            hp_res = get_source_heat_pump_hourly(s)
            extra += hp_res['total_electricity_kwh']
            gas_alloc = hp_res['gas_allocation']
            cook_elec, cooking_smc, cooking_kwh = calculate_cooking_electricity(s, gas_alloc, valid, m, h)
            extra += cook_elec
            annual_space_comp_e = float(np.mean(hp_res['space_compressor_electricity_kwh'].sum(axis=1)))
            annual_space_back_e = float(np.mean(hp_res['space_backup_electricity_kwh'].sum(axis=1)))
            annual_ext_pump_e = float(np.mean(hp_res['external_pump_electricity_kwh'].sum(axis=1)))
            annual_dhw_comp_e = float(np.mean(hp_res['dhw_compressor_electricity_kwh'].sum(axis=1)))
            annual_dhw_back_e = float(np.mean(hp_res['dhw_backup_electricity_kwh'].sum(axis=1)))
            annual_standby_e = float(np.mean(hp_res['standby_electricity_kwh'].sum(axis=1)))
            annual_hp_total_e = float(np.mean(hp_res['total_electricity_kwh'].sum(axis=1)))
            heating_kwh = annual_space_comp_e + annual_space_back_e + annual_ext_pump_e
            dhw_kwh = annual_dhw_comp_e + annual_dhw_back_e
            enduses = dict(
                heating_smc=0.0,
                dhw_smc=0.0,
                cooking_smc=cooking_smc,
                heating_kwh=heating_kwh,
                dhw_kwh=dhw_kwh,
                cooking_kwh=cooking_kwh,
                standby_kwh=annual_standby_e,
                heat_pump_total_kwh=annual_hp_total_e,
                heat_pump=hp_res['summary'],
            )
        else:
            pct = s.get('ac_heating_additional_demand_pct', 0)
            ac_budget = _ac_heating_budget(s, additional_demand_pct=pct)
            summer = np.minimum(current_runtime().thermal_reference['GAS_MONTHLY'], s['summer_gas_base'])
            cooking = summer / summer.sum() * s['cooking_smc_annual']
            dhw = summer - cooking
            heating = ac_budget['heating_gas_smc']
            monthly_heat = ac_budget['requested_heating_electricity_kwh']
            dhw_efficiency = s['dhw_resistance_efficiency'] if s['dhw_heating_type'] == 'resistance' else s['dhw_scop']
            monthly_dhw = dhw * s['gas_pcs'] * s['boiler_efficiency'] / dhw_efficiency
            monthly_cook = cooking * s['gas_pcs'] * s['gas_cooking_efficiency'] / s['induction_efficiency']
            ac_heat_profile, ac_diag = _allocate_space_heating(
                s, d['temperature'], m, h, valid, _get_civil_hours(d), d['years'], return_diagnostics=True
            )
            extra += ac_heat_profile
            for mm in range(1, 13):
                use = valid & (m == mm)
                daytime = (h >= 11) & (h <= 15)
                evening = ((h >= 6) & (h <= 8)) | ((h >= 18) & (h <= 22))
                extra += _normalized_pattern(daytime.astype(float), monthly_dhw[mm - 1] * s['dhw_midday_fraction'], use)
                extra += _normalized_pattern(evening.astype(float), monthly_dhw[mm - 1] * (1 - s['dhw_midday_fraction']), use)
                extra += _normalized_pattern(((h >= 12) & (h <= 13) | (h >= 19) & (h <= 20)).astype(float), monthly_cook[mm - 1], use)
            baseline_useful = float(ac_budget['baseline_useful_heat_kwh'].sum())
            requested_useful = float(ac_budget['requested_useful_heat_kwh'].sum())
            baseline_ac_elec = float(ac_budget['baseline_heating_electricity_kwh'].sum())
            requested_ac_elec = float(ac_budget['requested_heating_electricity_kwh'].sum())
            extra_ac_elec = requested_ac_elec - baseline_ac_elec
            scop = float(s['scop'])
            pct_val = float(pct)
            enduses = dict(
                heating_smc=float(heating.sum()),
                dhw_smc=float(dhw.sum()),
                cooking_smc=float(cooking.sum()),
                heating_kwh=float(monthly_heat.sum()),
                dhw_kwh=float(monthly_dhw.sum()),
                cooking_kwh=float(monthly_cook.sum()),
                ac_heating_baseline_useful_kwh=baseline_useful,
                ac_heating_requested_useful_kwh=requested_useful,
                ac_heating_baseline_electricity_kwh=baseline_ac_elec,
                ac_heating_requested_electricity_kwh=requested_ac_elec,
                ac_heating_extra_electricity_kwh=extra_ac_elec,
                ac_heating_additional_demand_pct=pct_val,
                ac_heating_scop=scop,
                baseline_useful_heat_kwh=baseline_useful,
                requested_useful_heat_kwh=requested_useful,
                baseline_heating_electricity_kwh=baseline_ac_elec,
                requested_heating_electricity_kwh=requested_ac_elec,
                extra_heating_electricity_kwh=extra_ac_elec,
                ac_heating_baseline_kwh=baseline_ac_elec,
                ac_heating_requested_kwh=requested_ac_elec,
                ac_heating_extra_kwh=extra_ac_elec,
                ac_heating_allocation_diagnostics=ac_diag,
            )
    ev_kwh=s['ev_km_annual']*s['ev_kwh_100km']/100/s['ev_charge_efficiency']*s['ev_home_fraction'] if s['load_scenario']=='ev' else 0.
    ev=_normalized_pattern(((h>=11)&(h<=16)).astype(float),ev_kwh*s['ev_day_fraction'],valid)+_normalized_pattern(((h>=18)&(h<=23)).astype(float),ev_kwh*(1-s['ev_day_fraction']),valid)
    enduses['ev_kwh']=ev_kwh
    baseline_cooling=estimated_baseline_cooling(d)['summer_baseline_kwh']
    multiplier=float(s.get('summer_ac_multiplier',1.0))
    extra_ac_kwh=(multiplier-1.0)*baseline_cooling if multiplier>1.0 and baseline_cooling>0. else 0.
    if extra_ac_kwh>0:
        summer_use=valid&(m>=6)&(m<=9)
        cooling_degrees=np.where(summer_use,np.maximum(d['temperature']-current_runtime().thermal_reference['COOLING_BALANCE_TEMP'],0.),0.)
        for yi in range(len(d['years'])):
            if not np.sum(cooling_degrees[yi,summer_use[yi]]):
                cooling_degrees[yi,summer_use[yi]]=1.0
        extra+=_normalized_pattern(cooling_degrees,extra_ac_kwh,summer_use)
    enduses['summer_ac_baseline_kwh']=baseline_cooling
    enduses['summer_ac_extra_kwh']=extra_ac_kwh
    enduses['summer_ac_kwh']=baseline_cooling+extra_ac_kwh
    enduses['summer_ac_multiplier']=multiplier
    return base+extra,ev,enduses

def _load_cache(key):
    return _load_cache_cached(current_runtime().key, freeze(key))
_load_cache.cache_clear = _load_cache_cached.cache_clear
_load_cache.cache_info = _load_cache_cached.cache_info

LOAD_KEYS = [
    'load_scenario', 'load_growth', 'timezone_mode', 'heating_schedule_mode',
    'ac_heating_windows', 'ac_heating_additional_demand_pct',
    'summer_ac_multiplier', 'summer_gas_base', 'cooking_smc_annual', 'gas_pcs',
    'boiler_efficiency', 'gas_cooking_efficiency', 'induction_efficiency',
    'dhw_heating_type', 'dhw_resistance_efficiency', 'dhw_scop', 'scop',
    'dhw_midday_fraction', 'heating_balance_temp', 'ev_km_annual', 'ev_kwh_100km',
    'ev_charge_efficiency', 'ev_home_fraction', 'ev_day_fraction',
    'planning_heat_pump',
] + HP_PHYSICAL_KEYS

def load_profiles(s):
    d_defs = defaults()
    return _load_cache(tuple((k, freeze(s.get(k, d_defs.get(k)))) for k in LOAD_KEYS))

ENERGY_KEYS=['installation_year','south_panels','west_panels','east_panels','dormer_panels','flat_panels','secondary_south_panels','panel_watts','inverter_kw','pv_battery_coupling','battery_kwh','charge_kw','discharge_kw','roundtrip_efficiency','shade_strength','diffuse_suppression','pv_first_year_loss','pv_annual_fade','battery_calendar_fade','battery_cycle_fade','battery_replace_year','horizon_years','ev_start_year','dormer_south_proxy','export_price_profile','export_price_window']+LOAD_KEYS
ENERGY_KEYS += ['pv_dispatch_model', 'inverter_efficiency', 'ilr_ac_max', 'ilr_dc_max', 'clipping_ac_max', 'clipping_dc_max', 'battery_inverter_kw', 'battery_c_rate_max', 'battery_soc_min', 'battery_soc_max', 'battery_charge_efficiency', 'battery_discharge_efficiency']
# Routing priority is an energy-behaviour decision variable: every ENERGY_KEYS
# cache must be keyed on it (and the scheduled switch hour) so "auto"/fixed
# modes never share a stale dispatch.
ENERGY_KEYS += ['routing_priority', 'routing_switch_hour', 'scheduled_start_month', 'scheduled_end_month']
ALL_KEYS=tuple(SPEC.keys())

PV_PROFILE_KEYS=('south_panels','west_panels','east_panels','dormer_panels',
                 'flat_panels','secondary_south_panels','panel_watts',
                 'dormer_south_proxy','shade_strength','diffuse_suppression')

_PV_YIELD_SETTING = '_pv_yield_multiplier'

def _energy_key(s, keys=None):
    # Legacy scalar key shape stays compatible with private reference consumers.
    keys = ENERGY_KEYS if keys is None else keys
    if keys is ALL_KEYS and any(k in s for k in CONFIRMATIONS):
        keys = (*keys, *CONFIRMATIONS)
    key = tuple((k, freeze(s.get(k))) for k in keys)
    if s.get('roof_panels') is not None:
        key += (('roof_panels', freeze(s['roof_panels'])),)
    # Sensitivity changes production, while rated capacity and roof counts stay fixed.
    if _PV_YIELD_SETTING in s:
        key += ((_PV_YIELD_SETTING, s[_PV_YIELD_SETTING]),)
    return key


def pv_profile(s):
    key = tuple(s.get(k) for k in PV_PROFILE_KEYS)
    if s.get('roof_panels') is not None:
        key += (freeze(s['roof_panels']),)
    profile = _pv_profile_cached(current_runtime().key, key)
    factor = s.get(_PV_YIELD_SETTING, 1.0)
    return profile if factor == 1.0 else profile * factor

@lru_cache(maxsize=24)
def _pv_profile_cached(runtime_key, key):
    assert_runtime_key(runtime_key)
    s=dict(zip(PV_PROFILE_KEYS,key))
    d=data()
    if _dynamic_roofs() is not None:
        mapping = dict(key[len(PV_PROFILE_KEYS)])
        counts = np.array([mapping[r] for r in d['audit']['plane_ids']], float)
    else:
        # Preserve the original source-axis summation and explicit dormer proxy.
        counts=np.array([s['south_panels']+s['secondary_south_panels'],s['west_panels'],s['east_panels'],s['dormer_panels'],s['flat_panels']],float)
        if s['dormer_south_proxy']:counts[0]+=counts[3];counts[3]=0
    adjusted=d['power']-s['shade_strength']*(d['beam_loss']+s['diffuse_suppression']*d['diffuse_loss'])
    return np.maximum(np.einsum('o,oyt->yt',counts*s['panel_watts']/1000,adjusted),0)

def synthetic_prices():
    # Without market observations, apply the supplied solar capture benchmark
    # once, uniformly to exports. Inventing a depressed hourly shape as well
    # would add an unsupported second capture adjustment.
    return np.ones_like(data()['hour'],dtype=float)

def price_profile(settings=None):
    s=settings if isinstance(settings,dict) else {}
    pref=s.get('export_price_profile','empirical_csud')
    win=s.get('export_price_window','oct2025_sep2026')
    validate_price_selection(s, current_runtime().household_profile)
    fp=price_fingerprint(s)
    return _price_cache(current_runtime().key, fp,pref,win)

@lru_cache(maxsize=16)
def _price_cache(runtime_key, version,profile_pref='empirical_csud',window_pref='oct2025_sep2026'):
    assert_runtime_key(runtime_key)
    prices=current_runtime().price_data
    if current_runtime().household_profile is not None:
        binding = prices['profile_market']
        selected = binding['selection']
        mode = selected['valuation_mode']
        if mode == 'flat':
            from .runtime_context import freeze as freeze_runtime
            return freeze_runtime(synthetic_prices()*data()['valid']), 'flat_solar_capture_benchmark', None
        product = binding['product']
        d = data(); valid = d['valid']
        matrix = np.asarray(product['matrix_eur_kwh'])
        if mode == 'shape_scaled':
            from .prepare_market_prices import expand_pzo_proxy
            values = expand_pzo_proxy(product['normalized_shape'], d['month'], d['hour'], valid)
        else:
            values = np.zeros(valid.shape, dtype=float)
            values[valid] = matrix[d['month'][valid]-1, d['hour'][valid]]
        mean = np.sum(values*valid, axis=1)/valid.sum(axis=1)
        from .runtime_context import freeze as freeze_runtime
        return freeze_runtime(values), 'catalog_' + selected['series'] + '_' + mode, freeze_runtime(mean)
    actual=prices.get('hourly_prices.npz')
    empirical=prices.get('empirical_csud_prices.npz')
    if profile_pref=='flat_benchmark':
        return synthetic_prices(),'flat_solar_capture_benchmark',None
    if actual is not None:
        pzo=np.asarray(actual['pzo'],float)
        if pzo.shape!=data()['valid'].shape or not np.all(np.isfinite(pzo[data()['valid']])):
            raise ValueError('Hourly price file shape/values invalid')
        mean=np.sum(pzo*data()['valid'],axis=1)/data()['valid'].sum(axis=1)
        if np.any(mean<=0):raise ValueError('Hourly mean prices must be positive')
        return pzo/mean[:,None],'actual_hourly_gme_profile',mean
    if empirical is not None:
        a=empirical
        if window_pref=='calendar_2025':
            proxy=np.asarray(a['pzo_proxy_cal2025'],float)
            shape=np.asarray(a['normalized_shape_cal2025'],float)
            label='empirical_csud_calendar_2025_proxy'
            meta=json.loads(str(a['meta_json']))
            baseload=meta['windows']['calendar_2025']['mean_csud_eur_kwh']
        else:
            proxy=np.asarray(a['pzo_proxy_oct2025_sep2026'],float)
            shape=np.asarray(a['normalized_shape_oct2025_sep2026'],float)
            label='empirical_csud_oct2025_sep2026_proxy'
            meta=json.loads(str(a['meta_json']))
            baseload=meta['windows']['oct2025_sep2026']['mean_csud_eur_kwh']
        d=data();v=d['valid']
        if proxy.shape==v.shape:
            if not np.all(np.isfinite(proxy[v])):
                raise ValueError('Empirical price proxy values invalid')
            adapted_proxy=proxy
        else:
            if shape.shape!=(12,24) or not np.all(np.isfinite(shape)):
                raise ValueError('Empirical month/hour price shape/values invalid')
            m=d['month'];h=d['hour']
            adapted_proxy=np.zeros_like(h,float)
            for w in range(m.shape[0]):
                vw=v[w];m_idx=m[w,vw]-1;h_idx=h[w,vw]
                raw_vals=shape[m_idx,h_idx]
                year_mean=float(np.mean(raw_vals))
                if year_mean<=0 or not np.isfinite(year_mean):
                    raise ValueError('Empirical price proxy year mean must be positive and finite')
                adapted_proxy[w,vw]=raw_vals/year_mean
        if adapted_proxy.shape!=v.shape or not np.all(np.isfinite(adapted_proxy[v])):
            raise ValueError('Empirical price proxy shape/values invalid')
        mean=np.sum(adapted_proxy*v,axis=1)/v.sum(axis=1)
        if np.any(mean<=0):raise ValueError('Empirical price mean prices must be positive')
        return adapted_proxy/mean[:,None],label,mean
    if current_runtime().metadata.get('format') != 'local-legacy-profile-v1':
        raise ValueError('No bound price artifact; select an explicit flat benchmark')
    return synthetic_prices(),'flat_solar_capture_benchmark',None

def empirical_month_hour_data(window=None):
    ctx = current_runtime()
    if ctx.household_profile is not None:
        binding = ctx.price_data['profile_market']
        product = binding['product']
        if product is None:
            return None
        from .runtime_context import _identity_value
        selected = dict(binding['selection'])
        if window not in (None, 'profile_market', selected['window_start'] + '/' + selected['window_end']):
            raise ValueError('Market window is pinned to the profile revision')
        shape = product['normalized_shape']
        matrix = np.asarray(product['matrix_eur_kwh'])
        counts = np.asarray(product['counts_hours'])
        return dict(window=selected['window_start'] + '/' + selected['window_end'],
            window_meta=_identity_value(product), market_reference_period=[selected['window_start'],selected['window_end']],
            weather_years_label=weather_period()[3], weather_count=weather_period()[0],
            provenance={**selected, 'units':'EUR/kWh', 'source':'gme-catalog-v1',
                        'warning':'not_zonal_remuneration' if selected['series']=='PUN' else None},
            matrix_eur_kwh=matrix.tolist(), normalized_shape_12x24=None if shape is None else np.asarray(shape).tolist(),
            monthly_means_eur_kwh=np.average(matrix, weights=counts, axis=1).tolist(),
            hourly_means_eur_kwh=np.average(matrix, weights=counts, axis=0).tolist())
    if window is None:window='oct2025_sep2026'
    a=current_runtime().price_data.get('empirical_csud_prices.npz')
    if a is None:return None
    if window=='calendar_2025':
        matrix=np.asarray(a['month_hour_csud_cal2025'],float)
        shape=np.asarray(a['normalized_shape_cal2025'],float)
    else:
        matrix=np.asarray(a['month_hour_csud_oct2025_sep2026'],float)
        shape=np.asarray(a['normalized_shape_oct2025_sep2026'],float)
    meta=json.loads(str(a['meta_json']))
    w_count, _, _, w_label = weather_period()
    return {
        'window':window,
        'window_meta':meta['windows'].get(window,{}),
        'market_reference_period':meta['windows'].get(window,{}).get('dates_coverage',''),
        'weather_years_label':w_label,
        'weather_count':w_count,
        'provenance':{
            'source':meta['source'],
            'origin_url':meta['origin_url'],
            'retrieval':meta['retrieval'],
            'zone':meta['zone'],
            'units':'EUR/kWh',
            'spill_handling':meta['edge_month_spill_handling']
        },
        'matrix_eur_kwh':matrix.round(5).tolist(),
        'normalized_shape_12x24':shape.round(4).tolist(),
        'monthly_means_eur_kwh':[float(matrix[m].mean()) for m in range(12)],
        'hourly_means_eur_kwh':[float(v) for v in (matrix * np.array([31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31], dtype=float)[:, None]).sum(axis=0) / 365.0],
        'monthly_shapes':[float(shape[m].mean()) for m in range(12)],
        'hourly_shapes':[float(v) for v in (shape * np.array([31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31], dtype=float)[:, None]).sum(axis=0) / 365.0]
    }

@lru_cache(maxsize=128)
def _energy_cache_cached(runtime_key, key,price_version=0):
    assert_runtime_key(runtime_key)
    s=thaw_settings(dict(key))
    # Resolve an unresolved "auto" before the ENERGY_KEYS-only cache identity is
    # consumed by dispatch, so every low-level caller shares one concrete mode.
    if s.get('routing_priority',ROUTING_AUTO) not in ROUTING_MODES:
        full=defaults();full.update(s)
        s=dict(s);s['routing_priority']=resolve_routing_priority(full)
    inputs = _lifetime_mapped_inputs(s)
    pv = _map_pv_profile(pv_profile(s), inputs['mappings'], inputs['target_len'])
    load = inputs['load']
    ev = inputs['ev']
    valid = inputs['valid']
    pzo = inputs['pzo']
    month = inputs['month']
    hour = inputs['hour']
    out=dispatch_lifetime(pv,load,ev,valid,pzo,s['horizon_years'],s['ev_start_year'],s['inverter_kw'],s['battery_kwh'],s['charge_kw'],s['discharge_kw'],math.sqrt(s['roundtrip_efficiency']),s['pv_first_year_loss'],s['pv_annual_fade'],s['battery_calendar_fade'],s['battery_cycle_fade'],s['battery_replace_year'],s['pv_battery_coupling']=='dc_coupled',physical_parameters(s),month,hour)
    # Lifetime telescoping energy balance also includes shrinking-capacity/replacement loss.
    balance=out[:,:,1].sum(axis=0)-out[:,:,2].sum(axis=0)-out[:,:,3].sum(axis=0)-out[:,:,6].sum(axis=0)
    assert np.max(np.abs(balance))<1e-6
    lifetime=out[:,:,1].sum(axis=0)+out[:,:,5].sum(axis=0)-out[:,:,0].sum(axis=0)-out[:,:,6].sum(axis=0)-out[:,:,7].sum(axis=0)-out[-1,:,8]
    assert np.max(np.abs(lifetime))<1e-5
    _, _, enduses = load_profiles(s)
    return out,enduses

def _energy_cache(key,price_version=0):
    return _energy_cache_cached(current_runtime().key, freeze(key),price_version)
_energy_cache.cache_clear = _energy_cache_cached.cache_clear
_energy_cache.cache_info = _energy_cache_cached.cache_info

def envelope(values):
    a=np.asarray(values,float);a=a[np.isfinite(a)]
    if not len(a):return dict(min=None,median=None,mean=None,max=None,p10=None,p90=None)
    p10,p90=np.percentile(a,[10,90])
    return {k:float(v) for k,v in [('min',a.min()),('median',np.median(a)),('mean',a.mean()),('max',a.max()),('p10',p10),('p90',p90)]}

def payback_envelope(values):
    """Unrecovered weather outcomes remain censored above horizon, never dropped."""
    ordered=sorted(float(v) if v is not None else float('inf') for v in values)
    n=len(ordered);recovered=sum(math.isfinite(v) for v in ordered)
    def quantile(q):
        pos=(n-1)*q;lo=int(math.floor(pos));hi=int(math.ceil(pos))
        if not math.isfinite(ordered[lo]) or not math.isfinite(ordered[hi]):return None
        return ordered[lo]+(ordered[hi]-ordered[lo])*(pos-lo)
    return dict(min=ordered[0] if recovered else None,median=quantile(.5),mean=float(np.mean(ordered)) if recovered==n else None,max=ordered[-1] if recovered==n else None,p10=quantile(.1),p90=quantile(.9),recovered_weather_years=recovered,weather_count=n,unrecovered_weather_years=n-recovered,censoring='Unrecovered values exceed project horizon; null is not zero.')

def _pv_metric_basis(s, first):
    """AC-equivalent public PV production and self-consumed PV from a dispatch trace.

    The physical DC-coupled dispatch reports ``produced`` on an internal hybrid
    bus that includes the DC charge energy, so that ``produced = direct +
    charge + exported`` and the lifetime energy identity stay exact (the DC
    charge is needed to keep storage/conversion losses nonnegative when the
    battery efficiency exceeds the inverter efficiency). Public PV metrics are
    defined on the same AC-equivalent basis as the AC topology:

      * DC PV AC-equivalent   = produced - DC charge * (1 - inverter_efficiency)
      * self-consumed PV      = direct + DC charge * inverter_efficiency

    Non-DC-coupled dispatch already reports AC-equivalent quantities and is
    returned unchanged (``direct + charge``). Internal dispatch and physical
    SoC equations are not affected by this helper.
    """
    produced = first[:, 1]
    direct = first[:, 2]
    charge = first[:, 3]
    if s['pv_dispatch_model'] != 'physical' or s['pv_battery_coupling'] != 'dc_coupled':
        return produced, direct + charge
    eta = float(s['inverter_efficiency'])
    return produced - charge * (1.0 - eta), direct + charge * eta

def npv(flows,rate):return float(np.sum(np.asarray(flows,float)/(1+rate)**np.arange(len(flows))))

def irr_roots(flows):
    f=np.asarray(flows,float)
    if not np.any(f>0) or not np.any(f<0):return []
    # Solve polynomial in discount factor, retaining all economically possible roots.
    roots=np.polynomial.polynomial.polyroots(f)
    rates=sorted(1/z.real-1 for z in roots if abs(z.imag)<1e-7 and z.real>0 and 1/z.real-1>-.999999)
    return [float(r) for r in rates]

def persistent_payback(flows,rate=0):
    cumulative=np.cumsum(np.asarray(flows)/(1+rate)**np.arange(len(flows)))
    if cumulative[0]>=0 and np.all(cumulative>=-1e-9):return 0.
    for i in range(1,len(cumulative)):
        if cumulative[i]>=0 and np.all(cumulative[i:]>=-1e-9):
            previous=cumulative[i-1];return float(i-1+(-previous)/(cumulative[i]-previous))
    return None

def solve_implied_taeg(net_proceeds, monthly_payment, n_months):
    """Solve monthly IRR by robust bisection: cash received vs exact monthly annuity.
    Returns annual effective rate ((1 + r_monthly)^12 - 1). Returns None for zero borrowing.
    When TAN=0 and fees>0, returns positive TAEG.
    """
    if net_proceeds <= 0 or monthly_payment <= 0 or n_months <= 0:
        return None
    total_paid = n_months * monthly_payment
    if math.isclose(total_paid, net_proceeds, rel_tol=1e-12, abs_tol=1e-12):
        return 0.0

    def f(r_m):
        if abs(r_m) < 1e-14:
            return total_paid - net_proceeds
        return monthly_payment * (1.0 - (1.0 + r_m) ** (-n_months)) / r_m - net_proceeds

    if total_paid > net_proceeds:
        low = 0.0
        high = max(1.0, (total_paid / net_proceeds) / n_months * 2.0)
        while f(high) > 0 and high < 1e4:
            high *= 2.0
    else:
        low = -0.999
        high = 0.0
        while f(low) < 0 and low > -0.999999:
            low = (low - 1.0) / 2.0

    for _ in range(80):
        mid = 0.5 * (low + high)
        if f(mid) > 0:
            low = mid
        else:
            high = mid

    r_monthly = 0.5 * (low + high)
    return float((1.0 + r_monthly) ** 12 - 1.0)

def loan_schedule(principal,apr,years,horizon,rnom=None):
    payments=np.zeros(horizon);balances=np.zeros(horizon)
    equiv_payments=np.zeros(horizon) if rnom is not None else None
    if principal<=0 or years<=0 or horizon<=0:
        if rnom is not None:
            return payments,balances,equiv_payments
        return payments,balances
    n=int(years*12);r=apr/12.0
    monthly=principal/n if r==0 else principal*r/(1.0-(1.0+r)**(-n))
    balance=float(principal)
    df_factor=float(1.0+rnom) if rnom is not None else 1.0
    for y in range(horizon):
        for mm in range(1,13):
            if y*12+(mm-1)<n:
                payment=min(monthly,balance*(1.0+r))
                interest=balance*r
                balance=max(balance+interest-payment,0.0)
                payments[y]+=payment
                if rnom is not None:
                    factor=(df_factor**((12-mm)/12.0)) if df_factor>0 else 0.0
                    equiv_payments[y]+=payment*factor
        balances[y]=balance
    if horizon<years:
        payments[-1]+=balance
        if rnom is not None:
            equiv_payments[-1]+=balance
        balances[-1]=0.0
    if rnom is not None:
        return payments,balances,equiv_payments
    return payments,balances

def loan_payments_pv(principal,apr,years,horizon,rnom):
    """Discount month-end installments and any terminal balloon at their payment dates."""
    if principal<=0 or years<=0 or horizon<=0:
        return 0.0
    _,_,equiv=loan_schedule(principal,apr,years,horizon,rnom=rnom)
    discount=(1.0+rnom)**np.arange(1,horizon+1)
    return float(np.sum(equiv/discount))

def installed_capex(s, kwp):
    if kwp <= 0:
        return 0.0
    quote = s.get('quote_total')
    if quote is not None:
        total = float(quote)
    else:
        pv_base = float(s['pv_fixed_cost']) + kwp * float(s['pv_cost_kwp'])
        roof_extra = 0.0
        for r, panels in _panel_counts(s).items():
            extra_per_p = 0.0 if s.get('roof_panels') is not None else float(s.get(r + '_extra_cost_per_panel', 0.0) or 0.0)
            if panels > 0 and extra_per_p > 0:
                roof_extra += panels * extra_per_p

        bat_kwh = float(s.get('battery_kwh', 0.0) or 0.0)
        battery_cost = 0.0
        if bat_kwh > 0:
            if s.get('storage_cost_mode') == 'existing':
                battery_cost = 0.0
            else:
                total_mods = int(s.get('battery_modules', 0))
                mixed = bool(s.get('battery_mixed_sizes', False))
                sec_mods = int(s.get('battery_secondary_modules', 0)) if mixed else 0
                first_mods = max(0, total_mods - sec_mods)
                mod_kwh = float(s.get('battery_module_kwh', 0.0) or 0.0)
                sec_mod_kwh = float(s.get('battery_secondary_module_kwh', 0.0) or 0.0)
                bat_cost_kwh = float(s.get('battery_cost_kwh', 0.0) or 0.0)

                mod_override = s.get('battery_module_cost_override')
                if mod_override is not None:
                    primary_unit = float(mod_override)
                else:
                    primary_unit = mod_kwh * bat_cost_kwh
                primary_unit += float(s.get('battery_module_extra_cost', 0.0) or 0.0)

                sec_mod_override = s.get('battery_secondary_module_cost_override')
                if sec_mod_override is not None:
                    sec_unit = float(sec_mod_override)
                else:
                    sec_unit = sec_mod_kwh * bat_cost_kwh
                sec_unit += float(s.get('battery_secondary_module_extra_cost', 0.0) or 0.0)

                if total_mods > 0 and (mod_kwh > 0 or sec_mod_kwh > 0):
                    battery_cost = first_mods * primary_unit + sec_mods * sec_unit
                else:
                    battery_cost = bat_kwh * bat_cost_kwh

        total = pv_base + roof_extra + battery_cost
        if s.get('deduction_rate', 0.0) > 0:
            total *= (1.0 + float(s.get('deduction_price_premium', 0.0) or 0.0))
    return float(total if s.get('quote_vat_included', True) else total * (1.0 + float(s.get('installation_vat', 0.10) or 0.0)))


def battery_installed_capex(s):
    bat_kwh = float(s.get('battery_kwh', 0.0) or 0.0)
    if bat_kwh <= 0:
        return 0.0
    if s.get('storage_cost_mode') == 'existing':
        return 0.0
    total_mods = int(s.get('battery_modules', 0))
    mixed = bool(s.get('battery_mixed_sizes', False))
    sec_mods = int(s.get('battery_secondary_modules', 0)) if mixed else 0
    first_mods = max(0, total_mods - sec_mods)
    mod_kwh = float(s.get('battery_module_kwh', 0.0) or 0.0)
    sec_mod_kwh = float(s.get('battery_secondary_module_kwh', 0.0) or 0.0)
    bat_cost_kwh = float(s.get('battery_cost_kwh', 0.0) or 0.0)

    mod_override = s.get('battery_module_cost_override')
    if mod_override is not None:
        primary_unit = float(mod_override)
    else:
        primary_unit = mod_kwh * bat_cost_kwh
    primary_unit += float(s.get('battery_module_extra_cost', 0.0) or 0.0)

    sec_mod_override = s.get('battery_secondary_module_cost_override')
    if sec_mod_override is not None:
        sec_unit = float(sec_mod_override)
    else:
        sec_unit = sec_mod_kwh * bat_cost_kwh
    sec_unit += float(s.get('battery_secondary_module_extra_cost', 0.0) or 0.0)

    if total_mods > 0 and (mod_kwh > 0 or sec_mod_kwh > 0):
        battery_cost = first_mods * primary_unit + sec_mods * sec_unit
    else:
        battery_cost = bat_kwh * bat_cost_kwh

    if s.get('deduction_rate', 0.0) > 0:
        battery_cost *= (1.0 + float(s.get('deduction_price_premium', 0.0) or 0.0))
    return float(battery_cost if s.get('quote_vat_included', True) else battery_cost * (1.0 + float(s.get('installation_vat', 0.10) or 0.0)))


def detailed_installed_capex_breakdown(s, kwp):
    if kwp <= 0:
        return {
            'pv_fixed_cost_eur': 0.0,
            'pv_marginal_cost_eur': 0.0,
            'roof_placement_extra_eur': 0.0,
            'battery_marginal_cost_eur': 0.0,
            'unadjusted_capex_eur': 0.0,
            'deduction_price_premium_eur': 0.0,
            'vat_eur': 0.0,
            'installed_capex_eur': 0.0,
            'roof_breakdown': {},
            'battery_breakdown': {}
        }
    quote = s.get('quote_total')
    vat_rate = float(s.get('installation_vat', 0.10) or 0.0) if not s.get('quote_vat_included', True) else 0.0
    prem_rate = float(s.get('deduction_price_premium', 0.0) or 0.0) if s.get('deduction_rate', 0.0) > 0 else 0.0
    if quote is not None:
        total = float(quote)
        net_before_vat = total / (1.0 + vat_rate)
        return {
            'quote_total_applied': total,
            'pv_fixed_cost_eur': 0.0,
            'pv_marginal_cost_eur': 0.0,
            'roof_placement_extra_eur': 0.0,
            'battery_marginal_cost_eur': 0.0,
            'unadjusted_capex_eur': round(net_before_vat, 2),
            'deduction_price_premium_eur': 0.0,
            'vat_eur': round(total - net_before_vat, 2),
            'installed_capex_eur': round(total, 2),
            'roof_breakdown': {},
            'battery_breakdown': {}
        }

    pv_fixed = float(s.get('pv_fixed_cost', 0.0))
    pv_marginal = kwp * float(s.get('pv_cost_kwp', 0.0))
    roof_extra = 0.0
    roof_breakdown = {}
    for r, panels in _panel_counts(s).items():
        extra_per_p = 0.0 if s.get('roof_panels') is not None else float(s.get(r + '_extra_cost_per_panel', 0.0) or 0.0)
        tot_extra = panels * extra_per_p
        if panels > 0:
            roof_breakdown[r] = {
                'panels': panels,
                'extra_cost_per_panel': extra_per_p,
                'total_extra_eur': tot_extra
            }
        roof_extra += tot_extra

    bat_kwh = float(s.get('battery_kwh', 0.0) or 0.0)
    battery_cost = 0.0
    battery_breakdown = {}
    if bat_kwh > 0:
        total_mods = int(s.get('battery_modules', 0))
        mixed = bool(s.get('battery_mixed_sizes', False))
        sec_mods = int(s.get('battery_secondary_modules', 0)) if mixed else 0
        first_mods = max(0, total_mods - sec_mods)
        mod_kwh = float(s.get('battery_module_kwh', 0.0) or 0.0)
        sec_mod_kwh = float(s.get('battery_secondary_module_kwh', 0.0) or 0.0)
        bat_cost_kwh = float(s.get('battery_cost_kwh', 0.0) or 0.0)

        if s.get('storage_cost_mode') == 'existing':
            battery_cost = 0.0
            battery_breakdown = {
                'total_modules': total_mods,
                'first_modules': first_mods,
                'first_module_kwh': mod_kwh,
                'first_module_unit_cost_eur': 0.0,
                'secondary_modules': sec_mods,
                'secondary_module_kwh': sec_mod_kwh,
                'secondary_module_unit_cost_eur': 0.0,
                'total_battery_cost_eur': 0.0,
                'status': 'existing',
                'note': 'Existing storage (sunk cost) - 0 € initial capex'
            }
        else:
            mod_override = s.get('battery_module_cost_override')
            primary_base = float(mod_override) if mod_override is not None else (mod_kwh * bat_cost_kwh)
            primary_extra = float(s.get('battery_module_extra_cost', 0.0) or 0.0)
            primary_unit = primary_base + primary_extra

            sec_mod_override = s.get('battery_secondary_module_cost_override')
            sec_base = float(sec_mod_override) if sec_mod_override is not None else (sec_mod_kwh * bat_cost_kwh)
            sec_extra = float(s.get('battery_secondary_module_extra_cost', 0.0) or 0.0)
            sec_unit = sec_base + sec_extra

            if total_mods > 0 and (mod_kwh > 0 or sec_mod_kwh > 0):
                battery_cost = first_mods * primary_unit + sec_mods * sec_unit
            else:
                battery_cost = bat_kwh * bat_cost_kwh

            battery_breakdown = {
                'total_modules': total_mods,
                'first_modules': first_mods,
                'first_module_kwh': mod_kwh,
                'first_module_unit_cost_eur': round(primary_unit, 2),
                'secondary_modules': sec_mods,
                'secondary_module_kwh': sec_mod_kwh,
                'secondary_module_unit_cost_eur': round(sec_unit, 2),
                'total_battery_cost_eur': round(battery_cost, 2)
            }

    unadjusted = pv_fixed + pv_marginal + roof_extra + battery_cost
    prem_eur = unadjusted * prem_rate
    after_prem = unadjusted + prem_eur
    vat_eur = after_prem * vat_rate
    installed_total = after_prem + vat_eur

    return {
        'pv_fixed_cost_eur': round(pv_fixed, 2),
        'pv_marginal_cost_eur': round(pv_marginal, 2),
        'roof_placement_extra_eur': round(roof_extra, 2),
        'battery_marginal_cost_eur': round(battery_cost, 2),
        'unadjusted_capex_eur': round(unadjusted, 2),
        'deduction_price_premium_eur': round(prem_eur, 2),
        'vat_eur': round(vat_eur, 2),
        'installed_capex_eur': round(installed_total, 2),
        'roof_breakdown': roof_breakdown,
        'battery_breakdown': battery_breakdown
    }

def _financial(s,annual,kwp,calculate_irr=True,hp_summary=None,ranking_only=False,candidate_only=False):
    h=s['horizon_years'];weather=annual.shape[1];capex=installed_capex(s,kwp)
    active_hp = _uses_heat_pump(s)
    thermal_ref = _thermal_reference()
    if thermal_ref is not None and active_hp:
        # Financial credits follow the same target-calendar realization as dispatch.
        hp_forecast = _forecast_mapped_series(s)['mapped_hp_breakdown']
        hp_summary = {'by_weather': hp_forecast['by_weather']}

    if active_hp:
        from feasibility.heatpump_finance import (
            calculate_hp_conversion_capex,
            calculate_hp_maintenance,
            calculate_hp_replacement,
            calculate_hp_gas_finance,
        )
        conversion = calculate_hp_conversion_capex(s)
    elif s['load_scenario'] != 'baseline':
        conversion = float(s['electrification_capex'] + s['gas_disconnect_cost'])
    elif s.get('electrification_capex_include_baseline', False):
        conversion = float(s['electrification_capex'])
    else:
        conversion = 0.
    if thermal_ref is not None:
        changes = any(_thermal_action(s,k) != 'unchanged' for k in thermal_ref.end_uses)
        if not changes:
            conversion = float(s['electrification_capex']) if s.get('electrification_capex_include_baseline') else 0.
        elif not active_hp:
            conversion = float(s['electrification_capex'])
        # Disconnection costs require an explicit selection and actual gas removal.
        from .heatpump_finance import calculate_thermal_finance
        conversion_facts = calculate_thermal_finance(s, hp_summary, n_weather=weather)
        if changes and active_hp and s.get('hp_disconnect_gas') and not conversion_facts['gas_disconnected']:
            conversion -= float(s['gas_disconnect_cost'])
        if changes and not active_hp and conversion_facts['gas_disconnected']:
            conversion += float(s['gas_disconnect_cost'])
    project=np.zeros((h+1,weather));package=np.zeros_like(project);equity=np.zeros_like(project);financed_project=np.zeros_like(project)
    equity_disc=np.zeros_like(project)
    loan_scope=capex+(conversion if s['loan_include_electrification'] else 0.)
    requested_loan=s['loan_amount'] if s['loan_mode']=='amount' else loan_scope*s['loan_fraction']
    net_principal=min(loan_scope,max(0.,float(requested_loan)))
    upfront_fee=float(s.get('loan_upfront_fees_or_tax',16.0) or 0.0)
    amortized_principal=net_principal+upfront_fee if net_principal>0 else 0.0
    effective_fee=upfront_fee if net_principal>0 else 0.0
    principal=net_principal
    financed_capex=capex+(conversion if s['loan_include_electrification'] else 0.)
    project[0]=-capex;package[0]=-capex-conversion;equity[0]=-capex-conversion+net_principal;financed_project[0]=-financed_capex
    equity_disc[0]=equity[0]
    rnom=(1+s['real_discount_rate'])*(1+s['inflation'])-1
    debt,balances,debt_equiv=loan_schedule(amortized_principal,s['loan_apr'],s['loan_years'],h,rnom=rnom)
    loan_interest=np.zeros(h);loan_principal=np.zeros(h);previous_balance=amortized_principal
    fiscal_policy=current_policy()
    eligible,deduction=fiscal_policy.investment_terms(capex,s)
    discount=(1+rnom)**np.arange(1,h+1);gross_cost=capex;net_cost=capex
    pv_debt=loan_payments_pv(amortized_principal,s['loan_apr'],s['loan_years'],h,rnom)
    pv_discounted=np.zeros(weather);components=[]
    historic_load=data()['audit']['annual_kwh']
    valuation=export_valuation(s)
    for yy in range(h):
        year=yy+1;infl=(1+s['inflation'])**yy;row=annual[yy]
        power=s['baseline_power_kw'] if s['load_scenario']=='baseline' else s['electrified_power_kw']
        no_pv_bill=electric_bill(row[:,0],s,year,power)
        with_pv_bill=electric_bill(row[:,5],s,year,power)
        avoided=no_pv_bill-with_pv_bill
        ramp_progress=min(yy/s['export_price_future_years'],1.)
        future_factor=1+(s['actual_price_future_factor']-1)*ramp_progress
        export_unit_price=valuation.settlement_multiplier(s['export_market_price'], s['export_capture_factor'],
            future_factor, infl, (1+s['energy_real_escalation'])**yy)
        pzo=row[:,11]*export_unit_price
        settlement=fiscal_policy.export_settlement(row[:,6],pzo,s,year,kwp)
        gross,fees,export_tax,net_export=(settlement[k] for k in ('gross','fees','tax','net'))
        cer=row[:,6]*s['cer_eligible_export_share']*s['cer_tariff']*s['cer_user_allocation']*infl if s['cer_enabled'] else np.zeros(weather)
        maint=(s['om_annual']+s['insurance_annual']+s['cleaning_annual'])*infl if kwp>0 else 0.
        inverter_rep=s['inverter_replacement_cost']*infl if (kwp>0 and year==s['inverter_replace_year']) else 0.
        battery_rep=s['battery_replacement_cost_kwh']*s['battery_kwh']*infl if (kwp>0 and s['battery_kwh']>0 and year==s['battery_replace_year']) else 0.
        replacement=inverter_rep+battery_rep
        incentive=fiscal_policy.investment_relief(capex,s,year)
        residual=capex*s['residual_fraction']*infl if year==h else 0.
        project[year]=avoided+net_export+cer+incentive-maint-replacement+residual
        if ranking_only or candidate_only:
            continue
        loan_principal[yy]=max(previous_balance-balances[yy],0.)
        loan_interest[yy]=max(debt[yy]-loan_principal[yy],0.)
        previous_balance=balances[yy]
        base_elec=electric_bill(historic_load*s['load_growth'],s,year,s['baseline_power_kw'])
        base_gas=gas_bill(s,year)
        baseline=base_elec+base_gas
        if thermal_ref is not None:
            thermal_costs = calculate_thermal_finance(s, hp_summary, year, weather)
            baseline = base_elec + thermal_costs['baseline_cost_eur']
            remaining_gas = rem_gas_cost = thermal_costs['remaining_cost_eur']
            allowance_y = np.asarray(thermal_costs['allowance_by_weather'])
            gas_credit = thermal_costs['avoided_gas_credit_by_weather']
            avoided_gas_y = np.zeros(weather) if gas_credit is None else np.asarray(gas_credit)
            hp_maint = calculate_hp_maintenance(s,year) if active_hp and changes else 0.
            hp_rep = calculate_hp_replacement(s,year) if active_hp and changes else 0.
            package[year]=baseline-with_pv_bill-rem_gas_cost-allowance_y+net_export+cer+incentive-maint-replacement+residual-hp_maint-hp_rep
        elif not active_hp:
            remaining_gas=base_gas if s['load_scenario']=='baseline' else 0.
            package[year]=baseline-with_pv_bill-remaining_gas+net_export+cer+incentive-maint-replacement+residual
            rem_gas_cost=remaining_gas
            allowance_y=np.zeros(weather)
            avoided_gas_y=np.zeros(weather)
            hp_maint=0.0
            hp_rep=0.0
        else:
            hp_maint=calculate_hp_maintenance(s,year)
            hp_rep=calculate_hp_replacement(s,year)
            gas_fin_y=calculate_hp_gas_finance(s,hp_summary=hp_summary,year=year,n_weather=weather)
            rem_gas_cost=gas_fin_y['actual_remaining_gas_cost']
            allowance_y=np.asarray(gas_fin_y['allowance_by_weather'],dtype=float)
            avoided_gas_y=np.asarray(gas_fin_y['avoided_gas_credit_by_weather'],dtype=float)
            package[year]=baseline-with_pv_bill-rem_gas_cost-allowance_y+net_export+cer+incentive-maint-replacement+residual-hp_maint-hp_rep
        financed_project[year]=(package[year] if s['loan_include_electrification'] else project[year])-loan_interest[yy]
        equity[year]=(package[year] if s['load_scenario']!='baseline' else project[year])-debt[yy]
        equity_disc[year]=(package[year] if s['load_scenario']!='baseline' else project[year])-debt_equiv[yy]
        gross_cost+=(maint+replacement-residual)/discount[yy]
        net_cost+=(maint+replacement-residual-incentive)/discount[yy]
        pv_discounted+=row[:,1]/discount[yy]
        comp_dict=dict(year=year,avoided_import=float(avoided.mean()),gross_export=float(gross.mean()),export_tax=float(export_tax.mean()),gse_fees=float(fees),net_export=float(net_export.mean()),cer=float(cer.mean()),tax_deduction=float(incentive),maintenance=float(maint),replacement=float(replacement),inverter_replacement=float(inverter_rep),battery_replacement=float(battery_rep),residual=float(residual),debt_service=float(debt[yy]),debt_service_year_end_equivalent=float(debt_equiv[yy]),loan_interest=float(loan_interest[yy]),loan_principal=float(loan_principal[yy]),loan_balance=float(balances[yy]),baseline_package_bill=float(baseline),baseline_electric_bill=float(base_elec),baseline_gas_bill=float(base_gas),with_system_electric_bill=float(with_pv_bill.mean()),remaining_gas_bill=float(rem_gas_cost),pv_incremental=float(project[year].mean()),whole_package=float(package[year].mean()),equity=float(equity[year].mean()),financed_project=float(financed_project[year].mean()),component_medians=dict(avoided_import=float(np.median(avoided)),net_export_cer=float(np.median(net_export+cer)),avoided_package_bills=float(np.median(baseline-with_pv_bill-(remaining_gas if not active_hp else (rem_gas_cost+allowance_y))))))
        if active_hp:
            comp_dict.update(unserved_service_allowance=float(allowance_y.mean()),avoided_gas_credit=float(avoided_gas_y.mean()),actual_remaining_gas_bill=float(rem_gas_cost),hp_maintenance=float(hp_maint),hp_replacement=float(hp_rep))
        if thermal_ref is not None:
            comp_dict.update(baseline_thermal_bill=thermal_costs['baseline_cost_eur'],
                remaining_thermal_bill=thermal_costs['remaining_cost_eur'],
                baseline_gas_bill=thermal_costs['baseline_gas_cost_eur'],
                remaining_gas_bill=thermal_costs['remaining_gas_cost_eur'],
                actual_remaining_gas_bill=thermal_costs['remaining_gas_cost_eur'],
                avoided_gas_credit=None if gas_credit is None else float(np.mean(gas_credit)),
                unserved_service_allowance=float(allowance_y.mean()))
        components.append(comp_dict)
    solar_loan_principal=min(net_principal,capex)
    solar_loan_ratio=(solar_loan_principal/net_principal) if net_principal>0 else 1.0
    solar_equity=np.zeros_like(project);solar_financed=np.zeros_like(project)
    package_equity=np.zeros_like(package);package_financed=np.zeros_like(package)
    solar_equity_disc=np.zeros_like(project);package_equity_disc=np.zeros_like(package)
    solar_equity[0]=-capex+solar_loan_principal;solar_financed[0]=-capex
    package_equity[0]=-capex-conversion+net_principal;package_financed[0]=-capex-conversion
    solar_equity_disc[0]=solar_equity[0];package_equity_disc[0]=package_equity[0]
    for yy in range(h):
        solar_equity[yy+1]=project[yy+1]-debt[yy]*solar_loan_ratio
        solar_equity_disc[yy+1]=project[yy+1]-debt_equiv[yy]*solar_loan_ratio
        solar_financed[yy+1]=project[yy+1]-loan_interest[yy]*solar_loan_ratio
        package_equity[yy+1]=package[yy+1]-debt[yy]
        package_equity_disc[yy+1]=package[yy+1]-debt_equiv[yy]
        package_financed[yy+1]=package[yy+1]-loan_interest[yy]
    if ranking_only:
        # Route ranking needs only solar project and allocated-equity NPVs.
        proj_npvs = [npv(project[:,w],rnom) for w in range(weather)]
        proj_env = envelope(proj_npvs)
        solar_eq_npvs = [npv(solar_equity_disc[:,w],rnom) for w in range(weather)]
        solar_eq_env = envelope(solar_eq_npvs)
        return {
            'pv_incremental': {'npv': proj_env, 'npv_eur': proj_env, 'npv_by_weather': proj_npvs},
            'solar_equity': {'npv': solar_eq_env, 'npv_eur': solar_eq_env, 'npv_by_weather': solar_eq_npvs},
        }, []
    contractual_years=int(s['loan_years'])
    n_contractual_months=contractual_years*12
    r_month=s['loan_apr']/12.0
    if amortized_principal>0 and n_contractual_months>0:
        monthly_payment=amortized_principal/n_contractual_months if r_month==0 else amortized_principal*r_month/(1.0-(1.0+r_month)**(-n_contractual_months))
        total_repaid=n_contractual_months*monthly_payment
        total_interest=total_repaid-amortized_principal
        full_debt,full_balances=loan_schedule(amortized_principal,s['loan_apr'],contractual_years,contractual_years)
        prev_b=amortized_principal
        real_interest=0.0
        infl=1.0+float(s['inflation'])
        for yy in range(contractual_years):
            ann_p=max(prev_b-full_balances[yy],0.)
            ann_i=max(full_debt[yy]-ann_p,0.)
            real_interest+=ann_i/(infl**(yy+1))
            prev_b=full_balances[yy]
        implied_taeg=solve_implied_taeg(net_principal,monthly_payment,n_contractual_months)
    else:
        monthly_payment=0.0
        total_repaid=0.0
        total_interest=0.0
        real_interest=0.0
        implied_taeg=None
    def metrics(flows, discounting_flows=None):
        dflows = flows if discounting_flows is None else discounting_flows
        irrs=[irr_roots(flows[:,w]) for w in range(weather)] if calculate_irr else [[] for w in range(weather)]
        scalar=[r[0] if len(r)==1 else np.nan for r in irrs]
        simple=[persistent_payback(flows[:,w]) for w in range(weather)]
        discounted=[persistent_payback(dflows[:,w],rnom) for w in range(weather)]
        simple_mean=persistent_payback(flows.mean(axis=1))
        discounted_mean=persistent_payback(dflows.mean(axis=1),rnom)
        npv_by_weather=[npv(dflows[:,w],rnom) for w in range(weather)]
        npv_env=envelope(npv_by_weather)
        return dict(npv=npv_env,npv_eur=npv_env,npv_by_weather=npv_by_weather,irr=envelope(scalar),irr_multiple_roots=any(len(r)>1 for r in irrs),irr_roots_by_weather=irrs,simple_payback=payback_envelope(simple),discounted_payback=payback_envelope(discounted),simple_payback_of_mean_cash_flows=simple_mean,discounted_payback_of_mean_cash_flows=discounted_mean,unpaid_weather_years=sum(v is None for v in simple),unpaid_discounted_weather_years=sum(v is None for v in discounted))
    initial_solar_equity=float(capex-solar_loan_principal)
    solar_eq_metrics=metrics(solar_equity, discounting_flows=solar_equity_disc)
    solar_eq_metrics.update(
        loan_principal_eur=float(net_principal),
        loan_amortized_principal_eur=float(amortized_principal),
        loan_upfront_fees_or_tax_eur=float(effective_fee),
        loan_monthly_payment_eur=float(monthly_payment),
        loan_total_repaid_eur=float(total_repaid),
        loan_total_interest_eur=float(total_interest),
        loan_implied_taeg=float(implied_taeg) if implied_taeg is not None else None,
        loan_real_interest_eur=float(real_interest),
        loan_pv_debt_eur=float(pv_debt),
        solar_loan_principal_eur=float(solar_loan_principal),
        solar_loan_ratio=float(solar_loan_ratio),
        initial_solar_equity_eur=initial_solar_equity,
        total_loan_scope_eur=float(loan_scope)
    )
    if candidate_only:
        return dict(pv_incremental=metrics(project),solar_equity=solar_eq_metrics,
                    capex_eur=float(capex),solar_loan_principal_eur=float(solar_loan_principal),
                    solar_loan_ratio=float(solar_loan_ratio),loan_principal_eur=float(net_principal),
                    loan_amortized_principal_eur=float(amortized_principal),
                    loan_upfront_fees_or_tax_eur=float(effective_fee),
                    loan_monthly_payment_eur=float(monthly_payment),
                    loan_total_repaid_eur=float(total_repaid),
                    loan_total_interest_eur=float(total_interest),
                    loan_implied_taeg=float(implied_taeg) if implied_taeg is not None else None,
                    loan_real_interest_eur=float(real_interest),
                    loan_pv_debt_eur=float(pv_debt),
                    initial_solar_equity_eur=initial_solar_equity,total_loan_scope_eur=float(loan_scope)), []
    finance=dict(pv_incremental=metrics(project),whole_package=metrics(package),equity=metrics(equity, discounting_flows=equity_disc),financed_project=metrics(financed_project),solar_equity=solar_eq_metrics,solar_financed=metrics(solar_financed),package_equity=metrics(package_equity, discounting_flows=package_equity_disc),package_financed=metrics(package_financed),capex_eur=float(capex),conversion_capex_eur=float(conversion),loan_principal_eur=float(net_principal),loan_amortized_principal_eur=float(amortized_principal),loan_upfront_fees_or_tax_eur=float(effective_fee),loan_monthly_payment_eur=float(monthly_payment),loan_total_repaid_eur=float(total_repaid),loan_total_interest_eur=float(total_interest),loan_implied_taeg=float(implied_taeg) if implied_taeg is not None else None,loan_real_interest_eur=float(real_interest),loan_pv_debt_eur=float(pv_debt),solar_loan_principal_eur=float(solar_loan_principal),solar_loan_ratio=float(solar_loan_ratio),initial_solar_equity_eur=initial_solar_equity,total_loan_scope_eur=float(loan_scope),requested_loan_amount_eur=float(s['loan_amount'] if s['loan_mode']=='amount' else loan_scope*s['loan_fraction']),loan_scope='solar_and_conversion' if s['loan_include_electrification'] else 'solar',eligible_deduction_capex_eur=float(eligible),annual_deduction_eur=float(deduction),nominal_discount_rate=float(rnom),real_discount_rate=s['real_discount_rate'],historical_annual_electric_bill_eur=float(electric_bill(historic_load,s)),historical_annual_gas_bill_eur=float(gas_bill(s)),lcoe=envelope(np.divide(gross_cost,pv_discounted,out=np.full(weather,np.nan),where=pv_discounted>0)),lcoe_net_incentives=envelope(np.divide(net_cost,pv_discounted,out=np.full(weather,np.nan),where=pv_discounted>0)))
    if active_hp and thermal_ref is None:
        gas_fin_y1 = calculate_hp_gas_finance(s, hp_summary=hp_summary, year=1, n_weather=weather)
        finance['service_comparable'] = bool(gas_fin_y1['service_comparable'])
        finance['actual_remaining_gas_cost_eur'] = float(gas_fin_y1['actual_remaining_gas_cost'])
        finance['unserved_service_allowance'] = gas_fin_y1['allowance']
        finance['unserved_service_allowance_eur'] = float(gas_fin_y1['allowance']['mean'])
        finance['avoided_gas_credit'] = gas_fin_y1['avoided_gas_credit']
        finance['avoided_gas_credit_eur'] = float(gas_fin_y1['avoided_gas_credit']['mean'])
        finance['gas_volumes'] = gas_fin_y1['gas_volumes']
        finance['heat_pump_gas_finance'] = gas_fin_y1
    else:
        finance['service_comparable'] = True
    finance['financed_project']['definition']='Financed scope capex is paid at year zero; yearly cash flows deduct loan interest only. Principal repayments are excluded here because the full financed-scope capex is included at year zero. Equity cash flows separately deduct full debt service. Electrification is in this scope only when loan_include_electrification is enabled; conversion capex never enters the deduction basis.'
    finance['equity']['definition']='Personal equity cash flows cover all equipment costs (solar plus conversion for an active electrified household) net of total loan proceeds at year zero, and deduct full annual debt service from corresponding operational benefits in each subsequent operating year.'
    if thermal_ref is not None:
        facts = calculate_thermal_finance(s, hp_summary, n_weather=weather)
        finance.update(thermal_finance=facts, service_comparable=facts['service_comparable'],
            unserved_service_allowance_eur=float(np.mean(facts['allowance_by_weather'])),
            historical_annual_gas_bill_eur=facts['baseline_gas_cost_eur'],
            historical_annual_thermal_bill_eur=facts['baseline_cost_eur'],
            avoided_gas_credit_eur=None if facts['avoided_gas_credit_by_weather'] is None else
                float(np.mean(facts['avoided_gas_credit_by_weather'])))
    finance['solar_equity']['definition']='Solar-only equity cash flows allocate loan principal and debt service proportionally: solar_loan_principal = min(principal, solar_capex), with yearly debt service scaled by solar_loan_ratio = solar_loan_principal / total_principal. Year 0 equity outlay is -solar_capex + solar_loan_principal (initial solar equity = solar_capex - solar_loan_principal), and yearly flows deduct allocated debt service. When package loan is selected (loan_include_electrification=True), total principal covers combined solar and conversion capex up to borrowing limits, allocating debt service without double deducting capex or principal.'
    finance['solar_financed']['definition']='Solar-only financed cash flows include full solar capex at year 0 and deduct only the allocated loan interest (scaled by solar_loan_ratio = min(principal, solar_capex) / principal) from annual operating savings. Principal repayments are excluded from annual operating flows because capex is recognized at year zero.'
    finance['package_equity']['definition']='Package equity cash flows cover solar plus electrification conversion capex net of total loan principal at year 0, and deduct full annual debt service (principal and interest) in each operating year.'
    finance['package_financed']['definition']='Package financed cash flows include full package capex (solar + conversion) at year 0 and deduct only annual loan interest across operating years, excluding principal repayment.'
    finance['lcoe_by_weather']=[float(v) if math.isfinite(v) else None for v in np.divide(gross_cost,pv_discounted,out=np.full(weather,np.nan),where=pv_discounted>0)]
    finance['pv_incremental']['lcoe']=finance['lcoe'];finance['pv_incremental']['lcoe_net_incentives']=finance['lcoe_net_incentives']
    finance['confidence']='Scenario arithmetic is auditable; costs, tax capacity and physical layout remain unconfirmed.'
    finance['reasoning']='Each weather year repeats over life with annual aging. Cash flows separate PV against identical future load from conversion package against historic electric+gas baseline. Historical gas annualization uses a flat August-regulated-rate approximation.'
    if thermal_ref is not None:
        finance['whole_package']['definition'] = 'Solar and selected equipment versus the measured household electrical bill and explicit carrier bills; embedded electricity is counted once, and fixed-cost removal requires explicit disconnection.'
    finance['export_price_source']=price_profile(s)[1]
    if current_runtime().household_profile is not None:
        finance['export_valuation'] = price_identity(s)
        finance['fiscal_policy'] = current_policy().application_provenance(s,h)
        finance['fiscal_policy']['rule_revision'] = current_runtime().fiscal_revision
    finance['current_invoice_check']=current_invoice_check(s)
    finance['cashflows_by_weather']={'pv_incremental':project.T.tolist(),'whole_package':package.T.tolist(),'equity':equity.T.tolist(),'financed_project':financed_project.T.tolist(),'solar_equity':solar_equity.T.tolist(),'solar_financed':solar_financed.T.tolist(),'package_equity':package_equity.T.tolist(),'package_financed':package_financed.T.tolist()}
    finance['cashflows_for_discounting_by_weather']={'pv_incremental':project.T.tolist(),'whole_package':package.T.tolist(),'equity':equity_disc.T.tolist(),'financed_project':financed_project.T.tolist(),'solar_equity':solar_equity_disc.T.tolist(),'solar_financed':solar_financed.T.tolist(),'package_equity':package_equity_disc.T.tolist(),'package_financed':package_financed.T.tolist()}
    cash=[dict(year=0,pv_incremental=-float(capex),whole_package=-float(capex+conversion),equity=float(equity[0,0]),financed_project=float(financed_project[0,0]),solar_equity=float(solar_equity[0,0]),solar_financed=float(solar_financed[0,0]),package_equity=float(package_equity[0,0]),package_financed=float(package_financed[0,0]),debt_service_year_end_equivalent=0.,loan_interest=0.,loan_principal=0.,inverter_replacement=0.,battery_replacement=0.)] + components
    for yy,c in enumerate(cash):
        c['pv_incremental_range']=envelope(project[yy]);c['whole_package_range']=envelope(package[yy]);c['equity_range']=envelope(equity[yy]);c['financed_project_range']=envelope(financed_project[yy])
        c['solar_equity_range']=envelope(solar_equity[yy]);c['solar_financed_range']=envelope(solar_financed[yy]);c['package_equity_range']=envelope(package_equity[yy]);c['package_financed_range']=envelope(package_financed[yy])
        if yy>0:
            c['solar_equity']=float(solar_equity[yy].mean())
            c['solar_financed']=float(solar_financed[yy].mean())
            c['package_equity']=float(package_equity[yy].mean())
            c['package_financed']=float(package_financed[yy].mean())
        c['pv_incremental_median']=float(np.median(project[yy]))
        c['whole_package_median']=float(np.median(package[yy]))
        c['equity_median']=float(np.median(equity[yy]))
        c['financed_project_median']=float(np.median(financed_project[yy]))
        c['solar_equity_median']=float(np.median(solar_equity[yy]))
        c['solar_financed_median']=float(np.median(solar_financed[yy]))
        c['package_equity_median']=float(np.median(package_equity[yy]))
        c['package_financed_median']=float(np.median(package_financed[yy]))
        c['pv_incremental_mean']=float(project[yy].mean())
        c['whole_package_mean']=float(package[yy].mean())
        c['equity_mean']=float(equity[yy].mean())
        c['financed_project_mean']=float(financed_project[yy].mean())
        c['solar_equity_mean']=float(solar_equity[yy].mean())
        c['solar_financed_mean']=float(solar_financed[yy].mean())
        c['package_equity_mean']=float(package_equity[yy].mean())
        c['package_financed_mean']=float(package_financed[yy].mean())
        c['component_statistics']=f'Component scalar amounts are diagnostic weather means that reconcile to mean total cash flow; totals and medians represent the full {project.shape[1]}-weather distribution.'
    if s['cashflow_mode']=='real':
        for c in cash:
            factor=(1+s['inflation'])**c['year']
            c['display_pv_incremental']=c['pv_incremental']/factor;c['display_whole_package']=c['whole_package']/factor;c['display_equity']=c['equity']/factor
            for key in ['pv_incremental_range','whole_package_range','equity_range','financed_project_range','solar_equity_range','solar_financed_range','package_equity_range','package_financed_range']:
                c['display_'+key]={k:v/factor if isinstance(v,(int,float)) else v for k,v in c[key].items()}
            for key in ['tax_deduction','replacement','inverter_replacement','battery_replacement','residual','debt_service','debt_service_year_end_equivalent','loan_interest','loan_principal','financed_project','solar_equity','solar_financed','package_equity','package_financed','unserved_service_allowance','avoided_gas_credit','actual_remaining_gas_bill','hp_maintenance','hp_replacement']:
                value = c.get(key,0)
                c['display_'+key] = None if value is None else value/factor
            for key in ['pv_incremental_mean','whole_package_mean','equity_mean','financed_project_mean','solar_equity_mean','solar_financed_mean','package_equity_mean','package_financed_mean']:
                c['display_'+key]=c[key]/factor
    return finance,cash

ROUTING_BASIS_DEFAULT='solar_equity'

def _resolver_signature(s):
    # Include financial settings and storage-cost ownership, not only physics.
    full=defaults();full.update(s)
    return tuple(sorted((k,freeze(v)) for k,v in full.items()))

def _routing_clipping_feasibility(s,annual):
    mapping=_forecast_calendar_mapping(current_runtime().key, int(s.get('installation_year',2026))+1)
    pv=_map_pv_profile(pv_profile(s),mapping[0],mapping[-1])
    pv_energy=np.sum(pv,axis=1)
    degradation=np.maximum(0.,1-s['pv_first_year_loss']-s['pv_annual_fade']*np.arange(len(annual)))
    available=degradation[:,None]*pv_energy[None,:]
    fractions=np.divide(annual[:,:,9],available,out=np.zeros_like(available),where=available>0)
    cap=s['clipping_dc_max'] if s['battery_kwh']>0 and s['pv_battery_coupling']=='dc_coupled' else s['clipping_ac_max']
    return bool(np.all(fractions<=cap+1e-9)),float(fractions.max()),float(cap)

@lru_cache(maxsize=256)
def _resolve_routing_cached(runtime_key, signature,price_version,basis=ROUTING_BASIS_DEFAULT,enforce_clipping=False):
    assert_runtime_key(runtime_key)
    s=thaw_settings(dict(signature))
    counts=_panel_total(s)
    kwp=counts*s['panel_watts']/1000.
    use_hp=_uses_heat_pump(s)
    scores={};feasible={}
    for mode in ROUTING_MODES:
        ss={**s,'routing_priority':mode}
        annual,enduses=_energy_cache(_energy_key(ss),price_version)
        fin,_=_financial(ss,annual,kwp,calculate_irr=False,
                         hp_summary=enduses.get('heat_pump') if use_hp else None)
        scores[mode]=float(fin[basis]['npv']['mean'])
        feasible[mode]=_routing_clipping_feasibility(ss,annual)[0] if enforce_clipping else True
    # Stable ties prefer the standard inverter mode. Never hide failed physics
    # or failed financial calculations behind a successful-looking fallback.
    return max(ROUTING_MODES,key=lambda mode:(feasible[mode],scores[mode])),scores

def resolve_routing_priority(s,basis=ROUTING_BASIS_DEFAULT,enforce_clipping=False):
    if s.get('pv_dispatch_model','legacy')!='physical':
        return 'battery_first'
    if s['battery_kwh']<=0:
        return 'pv_only'
    raw=s.get('routing_priority','auto')
    if raw in ROUTING_MODES:
        return raw
    return _resolve_routing_cached(current_runtime().key, _resolver_signature(s),price_fingerprint(s),basis,enforce_clipping)[0]

def _routing_settings(s,basis=ROUTING_BASIS_DEFAULT,enforce_clipping=False):
    # Resolve while the complete financial scenario is still available, before
    # constructing an energy-only cache key. This also separates price-driven
    # Auto decisions from reusable fixed-mode physical traces.
    route=resolve_routing_priority(s,basis,enforce_clipping)
    return {**s,'routing_priority':'battery_first' if route=='pv_only' else route}

def evaluate(settings=None,detail=False):
    s=validate(settings);counts=_panel_total(s);kwp=counts*s['panel_watts']/1000
    resolved=resolve_routing_priority(s)
    s_energy={**s,'routing_priority':resolved}
    version=price_fingerprint(s)
    annual,enduses=_energy_cache(_energy_key(s_energy),version);first=annual[0]
    energy={name:envelope(first[:,i]) for i,name in enumerate(FIELDS)}
    eta_inv=float(s['inverter_efficiency'])
    charge_first=np.maximum(first[:,3],0.)
    recaptured=np.zeros_like(charge_first)
    if s['pv_dispatch_model']=='physical' and s['pv_battery_coupling']=='dc_coupled' and s['battery_kwh']>0:
        mapping=_forecast_calendar_mapping(current_runtime().key, int(s.get('installation_year',2026))+1)
        raw=_map_pv_profile(pv_profile(s_energy),mapping[0],mapping[-1])*(1-s['pv_first_year_loss'])
        possible_over=np.sum(np.maximum(raw-s['inverter_kw'],0.)/eta_inv,axis=1)
        recaptured=np.clip(possible_over-first[:,9]/eta_inv,0.,charge_first)
    energy['charged_from_recaptured_clipping_kwh']=envelope(recaptured)
    energy['charged_from_normal_solar_kwh']=envelope(charge_first-recaptured)
    produced_ac,self_consumed_pv=_pv_metric_basis(s,first)
    energy['pv_kwh']=envelope(produced_ac)
    autarky=1-np.divide(first[:,5],first[:,0],out=np.ones(len(first)),where=first[:,0]>0)
    self_consumption=np.divide(self_consumed_pv,produced_ac,out=np.zeros(len(first)),where=produced_ac>0)
    useful=np.divide(first[:,2]+first[:,4],produced_ac,out=np.zeros(len(first)),where=produced_ac>0)
    mean_load=float(first[:,0].mean());mean_imp=float(first[:,5].mean())
    ens_autarky=1.-mean_imp/mean_load if mean_load>0 else 0.
    energy.update(autarky=envelope(autarky),self_consumption=envelope(self_consumption),useful_self_consumption=envelope(useful),ensemble_autarky=ens_autarky)
    use_heat_pump = _uses_heat_pump(s)
    hp_summary = enduses.get('heat_pump') if use_heat_pump else None
    finance,cash=_financial(s_energy,annual,kwp,hp_summary=hp_summary)
    roof_limits={'south':22,'west':11,'east':6,'dormer':4,'flat':8,'secondary_south':5}
    dynamic = _dynamic_roofs() is not None
    beyond_roof_limits=not dynamic and any(s[f'{roof}_panels']>limit for roof,limit in roof_limits.items())
    beyond_model_scale=not dynamic and kwp>20
    theoretical=not dynamic and bool(s['secondary_south_panels'] or s['dormer_panels'] or s['flat_panels'] or s['south_panels']>15 or beyond_roof_limits or beyond_model_scale)

    prof_arr, profile_source, prof_mean = price_profile(s)
    valuation = export_valuation(s)
    absolute_prices = valuation.unit == 'EUR'
    report_anchor = 1.0 if absolute_prices else s['export_market_price']
    first_export = first[:, 6]
    first_weighted = first[:, 11]
    has_exports = bool(np.any(first_export > 0))
    capture_ratio_by_weather = np.divide(first_weighted, first_export, out=np.full(len(first), np.nan), where=first_export > 0)
    if has_exports and np.any(np.isfinite(capture_ratio_by_weather)):
        valid_ratios = capture_ratio_by_weather[np.isfinite(capture_ratio_by_weather)]
        cap_factor_med = float(np.median(valid_ratios))
        cap_factor_env = envelope(valid_ratios)
        if profile_source == 'flat_solar_capture_benchmark':
            cap_factor_med = float(s['export_capture_factor'])
            cap_factor_env = dict(min=cap_factor_med, median=cap_factor_med, mean=cap_factor_med, max=cap_factor_med, p10=cap_factor_med, p90=cap_factor_med)
            cap_price_med = float(round(s['export_market_price'] * s['export_capture_factor'], 5))
            cap_price_env = dict(min=cap_price_med, median=cap_price_med, mean=cap_price_med, max=cap_price_med, p10=cap_price_med, p90=cap_price_med)
        else:
            cap_price_med = float(round(report_anchor * cap_factor_med, 5))
            cap_price_by_weather = capture_ratio_by_weather * report_anchor
            cap_price_env = envelope(cap_price_by_weather[np.isfinite(cap_price_by_weather)])
    else:
        cap_factor_med = None
        cap_factor_env = dict(min=None, median=None, mean=None, max=None, p10=None, p90=None)
        cap_price_med = None
        cap_price_env = dict(min=None, median=None, mean=None, max=None, p10=None, p90=None)

    flat_bench_unit_price = float(round(s['export_market_price'] * s['export_capture_factor'], 5))
    flat_gross_by_weather = first_export * flat_bench_unit_price
    flat_gross_med = float(round(float(np.median(flat_gross_by_weather)), 2)) if has_exports else 0.0

    active_unit_multiplier = 1.0 if profile_source != 'flat_solar_capture_benchmark' else s['export_capture_factor']
    active_gross_by_weather = first_weighted * report_anchor * active_unit_multiplier
    active_gross_med = float(round(float(np.median(active_gross_by_weather)), 2)) if has_exports else 0.0

    if current_runtime().household_profile is not None and not profile_source == 'flat_solar_capture_benchmark':
        selected = current_runtime().household_profile['market']
        profile_label = selected['series'] + ' ' + selected['valuation_mode'] + ' export benchmark'
    elif profile_source == 'actual_hourly_gme_profile':
        profile_label = 'Actual chronological hourly GME'
    elif 'empirical_csud' in profile_source:
        profile_label = 'Empirical CSUD shape'
    else:
        profile_label = 'Flat solar capture benchmark'

    is_flat_active = bool(profile_source == 'flat_solar_capture_benchmark')
    if is_flat_active:
        curve_gross_med = None
        delta_gross_med = None
        comp_note = f"Flat benchmark active: a constant capture factor ({s['export_capture_factor']:.2f}) applies directly to the baseload forecast. Empirical curve comparison is not calculated while flat benchmark is active."
    else:
        curve_gross_med = active_gross_med
        delta_gross_med = float(round(active_gross_med - flat_gross_med, 2))
        comp_note = f"Comparison holds physical solar production and self-consumption dispatch identical between empirical shape and flat {s['export_capture_factor']:.2f} benchmark."

    window_key = s.get('export_price_window', 'oct2025_sep2026')
    export_analysis = dict(
        profile_source=profile_source,
        profile_label=profile_label,
        profile_type='flat' if is_flat_active else 'empirical',
        window=window_key,
        baseload_price_eur_kwh=float(s['export_market_price']),
        legacy_capture_factor=float(s['export_capture_factor']),
        legacy_capture_factor_active=is_flat_active,
        export_weighted_capture_factor=cap_factor_med,
        export_weighted_capture_factor_envelope=cap_factor_env,
        export_weighted_captured_price_eur_kwh=cap_price_med,
        export_weighted_captured_price_envelope=cap_price_env,
        annual_export_kwh=float(energy['export_kwh']['median']),
        flat_benchmark_comparison=dict(
            flat_captured_price_eur_kwh=flat_bench_unit_price,
            flat_year1_pzo_gross_eur=flat_gross_med,
            curve_year1_pzo_gross_eur=curve_gross_med,
            delta_year1_pzo_gross_eur=delta_gross_med,
            note=comp_note
        )
    )

    if current_runtime().household_profile is not None:
        export_analysis.update(price_identity(s))
        export_analysis['profile_type'] = valuation.mode
        export_analysis['window'] = current_runtime().household_profile['market']['window_start'] + '/' + current_runtime().household_profile['market']['window_end']
        if absolute_prices:
            energy['export_proceeds_base_eur'] = energy.pop('export_price_weighted_kwh')
            export_analysis['baseload_price_eur_kwh'] = current_runtime().price_data['profile_market']['product']['mean_eur_kwh']
            export_analysis['export_weighted_capture_factor'] = None
            export_analysis['export_weighted_capture_factor_envelope'] = None
    w_count, _, _, w_label = weather_period()
    warnings=list(data()['audit'].get('limitations', ()))+['All financial/eligibility settings are pending scenarios; cost inputs are placeholders until itemized quotes.',f'All {w_count} historic weather years are repeated separately over project life, not randomly sampled or probabilistic.','Battery aging is annual capacity stepping based on cumulative model cycles; string/bypass, standby losses and hourly tariff/load changes remain unmodeled.',f"PV-to-battery dispatch uses the selected {s['pv_battery_coupling']} approximation with AC-equivalent PVGIS inputs; product-specific joint-output constraints are unmodeled.",'EV scenario includes electrification; vehicle cost and mobility benefits excluded.','CER eligible share is assumed, not reconstructed community hourly sharing.']
    if current_runtime().household_profile is not None:
        selected = current_runtime().household_profile['market']
        if selected['series'] == 'PUN':
            warnings.append('not_zonal_remuneration: PUN is an export comparison benchmark, not zonal remuneration.')
    if profile_source == 'flat_solar_capture_benchmark':
        warnings.append(f"Flat solar capture benchmark active: constant export_capture_factor ({s['export_capture_factor']:.2f}) applied once to baseload forecast; no empirical month/hour shape applied.")
    elif current_runtime().household_profile is not None:
        warnings.append('Pinned ' + selected['series'] + ' ' + selected['valuation_mode'] + ' export benchmark; capture factor is inactive. Future ramp, inflation and energy escalation each apply once. Fixed retail billing and physical dispatch are unchanged.')
    elif profile_source in ('empirical_csud_oct2025_sep2026_proxy', 'empirical_csud_calendar_2025_proxy'):
        win_label = 'Oct 2025 – Sep 2026' if 'oct2025' in profile_source else 'Calendar 2025'
        warnings.append(f'Empirical CSUD month/hour price profile from official GME market data ({win_label}) is normalized to selected baseload forecast. The export price factor starts at 1.0 in the first modeled year, ramps linearly to actual_price_future_factor after export_price_future_years elapsed years, then stays constant; export_capture_factor is inactive on this empirical curve path. Import prices and physical dispatch are unchanged.')
    elif 'hourly_prices.npz' in current_runtime().price_data:
        warnings.append('Actual supplied hourly GME price profile is normalized to selected baseload forecast. The export price factor starts at 1.0 in the first modeled year, ramps linearly to actual_price_future_factor after export_price_future_years elapsed years, then stays constant; export_capture_factor is not applied again to this observed shape. Import prices and physical dispatch are unchanged.')
    else:
        warnings.append('The export price factor starts at 1.0 in the first modeled year, moves linearly to actual_price_future_factor after export_price_future_years elapsed years, then stays constant. Inflation and energy escalation follow their separate paths. The ramp affects export revenue only, not import prices or physical dispatch. No observed hourly or seasonal market shape is available.')
    if not dynamic and s['dormer_panels']:warnings.append('Dormer shading conflicts strongly with annual Google flux; this option is experimental and excluded from practical Pareto.')
    if not dynamic and s['dormer_south_proxy']:warnings.append('31south ceiling uses south profile for secondary segments and four dormer panels; this is a deliberately optimistic orientation proxy.')
    if not use_heat_pump and _thermal_reference() is None:
        if s['heating_schedule_mode'] == 'scheduled':
            windows = s.get('ac_heating_windows') or []
            if not windows:
                win_desc = "no operating windows"
            else:
                desc_parts = []
                for w in windows:
                    sh, sm = divmod(w['start_minute'], 60)
                    eh, em = divmod(w['end_minute'], 60)
                    w_id = w.get('id', 'window')
                    w_label = w_id if 'window' in w_id else f"{w_id} window"
                    if w['start_minute'] == w['end_minute']:
                        desc_parts.append(f"{w_label} disabled")
                    else:
                        desc_parts.append(f"{w_label} {sh:02d}:{sm:02d}–{eh:02d}:{em:02d}")
                win_desc = ", ".join(desc_parts)
            warnings.append(
                f"Scheduled space-heating mode active: {win_desc} "
                f"({s['timezone_mode']} clock convention). Monthly useful gas/heating electricity budgets are conserved for each weather year "
                f"and allocated only to permitted operating hours, temperature-weighted by degree difference below the balance temperature proxy "
                f"({s['heating_balance_temp']:.0f}°C). Hours outside permitted operating windows have zero modeled space heating. "
                f"Building thermal mass, radiator cooldown, morning/evening warmup recovery spikes, compressor power limits, and temperature-varying heat-pump COP remain unmodeled."
            )
        else:
            warnings.append(
                f"Continuous space-heating mode active (24h old model): monthly useful gas/heating electricity budgets are conserved for each weather year "
                f"and distributed across all hours temperature-weighted by degree difference below the balance temperature proxy ({s['heating_balance_temp']:.0f}°C)."
            )
        warnings.append(
            f"Outdoor heating balance temperature ({s['heating_balance_temp']:.0f}°C) is a degree-demand modeling proxy, conceptually distinct from the user's reported indoor thermostat comfort setpoint (18°C). "
            "User AC seasonal ratings are 4.0 for average climate and 5.1 for warmer climate (default 4.0; unverified). Modeled space-heating electricity replaces only the measured gas-heated portion via heat-pump SCOP; radiator water mass is not inherited by a split heat pump."
        )
    elif _thermal_reference() is None:
        warnings.append(
            "Air-to-water heat pump model active: useful space and domestic hot water heating demands are calibrated from "
            f"{legacy_text('hp_reference','the explicitly supplied 12-month thermal reference, ')}"
            "converted to hourly electrical power input using temperature-dependent compressor performance maps, weather compensation, "
            "and stratified DHW cylinder storage dynamics."
        )
        warnings.append(
            f"Provisional heat pump equipment ratings include nominal {s.get('hp_nominal_kw', 9.0):.1f} kW thermal capacity "
            f"and {s.get('hp_radiator_dt50_kw', 10.0):.1f} kW emitter capacity at Delta-T 50. Emitter sizing, distribution circuit losses, "
            "and dwelling thermal envelope characteristics remain unverified placeholders."
        )
        warnings.append(
            f"Multi-year weather records ({w_label}) are used as climatology analogues. Building thermal capacitance, "
            "indoor dynamic room temperature decay, and thermal comfort are not modeled (no RC / comfort validation); "
            "unmet space or water heating is explicitly tracked and remains unmet."
        )
        if not finance.get('service_comparable', True):
            allowance_eur = finance.get('unserved_service_allowance_eur', 0.0)
            warnings.insert(0,
                f"MAJOR WARNING: Heat pump service shortfall detected (unmet space/DHW thermal demand exceeds numerical tolerance). "
                f"The heat pump system is not service-comparable to the baseline heating service. An explicit unserved service allowance "
                f"({allowance_eur:.2f} EUR/year diagnostic average) is deducted from whole-package benefits to withhold variable avoided gas credits "
                "for unsupplied heat. This conservative fuel credit adjustment does NOT estimate required equipment upgrades or indoor comfort, "
                "nor does it validate system economics: unmet heat remains unmet and is not backed up by silently generated consumption."
            )
        else:
            warnings.append(
                "Heat pump service is fully comparable across all modeled weather years (unmet space and DHW thermal demand within numerical tolerance)."
            )
    else:
        warnings.append('Thermal demand uses the bound household useful-heat and carrier-input reference. Embedded electrical demand is retained unless an identified separated series is replaced.')
        warnings.append('Consumption quality and measurement periods are reported in enduses.thermal_baseline and enduses.consumption_quality; synthetic profiles remain approximations.')
        if use_heat_pump:
            warnings.append('Heat-pump COP, cylinder and radiator simulations use explicit useful heat with historical weather analogues; building thermal comfort remains unvalidated.')
            if not finance.get('service_comparable', True):
                warnings.insert(0, 'Heat-pump service shortfall: an explicit unserved-service allowance withholds avoided carrier costs. Unmet heat remains unmet.')
    warnings += ([] if _thermal_reference() is not None else [
        'User local research adopted without fresh web verification; raw mapping errors corrected (capture once,6kWp catalog capex calibrated).',
        'Gas run rate is an annual approximation from flat August regulated unit costs, not measured twelve-month billed expenditure.'
    ])
    if beyond_roof_limits or beyond_model_scale:
        warnings.append('This layout exceeds former roof-count or 20 kWp modeling thresholds; results are theoretical model extrapolations and are excluded from practical Pareto rankings.')
    result=dict(settings=s,status='SCENARIO_PENDING',resolved_routing_priority=resolved,effective_pv_battery_coupling=('ac_equivalent' if s['battery_kwh']<=0 else s['pv_battery_coupling']),layout=dict(kwp=kwp,panel_count=counts,theoretical=theoretical,counts=_panel_counts(s)),energy=energy,finance=finance,export_price_analysis=export_analysis,annual_cashflows=cash,enduses=enduses,weather_years=data()['years'].tolist(),assumptions=records(),limitations=warnings)
    if use_heat_pump and 'heat_pump' in enduses:
        result['heat_pump'] = deepcopy(enduses['heat_pump'])
        result['service_comparable'] = finance.get('service_comparable', True)
        if 'heat_pump_gas_finance' in finance and isinstance(result['heat_pump'], dict):
            result['heat_pump']['gas_finance'] = finance['heat_pump_gas_finance']
    if dynamic:
        result['layout']['roofs'] = [dict(id=r['id'], label=r['label'], max_kwp=r['max_kwp'],
                                        panels=s['roof_panels'][r['id']]) for r in _dynamic_roofs()]
        result['solar_audit'] = dict(loss_convention='compiled additional attenuation applied once',
                                    shading_fractions={r['id']: r['shading_fraction'] for r in _dynamic_roofs()},
                                    shading_interpretation='uniform additional attenuation, not measured beam geometry',
                                    formula='power - shade_strength * (beam_loss + diffuse_suppression * diffuse_loss)',
                                    roof_shading_fraction_applied_again=False,
                                    plane_ids=list(data()['audit']['plane_ids']))
        result['assumptions'] = [r for r in result['assumptions'] if r['name'] in s]
    for record in result['assumptions']:
        name=record['name'];record['value']=s[name]
        if s[name]!=defaults()[name]:
            record['base_source']=record['source'];record['base_status']=record['status']
            record['status']='proposed';record['source']='Edited scenario input; pending confirmation'
    result['metrics']=dict(npv_eur=finance['pv_incremental']['npv'],package_npv_eur=finance['whole_package']['npv'],equity_npv_eur=finance['equity']['npv'],irr=finance['pv_incremental']['irr'],payback_years=finance['pv_incremental']['simple_payback'],discounted_payback_years=finance['pv_incremental']['discounted_payback'],financed_discounted_payback_years=finance['financed_project']['discounted_payback'],simple_payback_of_mean_cash_flows=finance['pv_incremental']['simple_payback_of_mean_cash_flows'],discounted_payback_of_mean_cash_flows=finance['pv_incremental']['discounted_payback_of_mean_cash_flows'],autarky=energy['autarky'],self_consumption=energy['self_consumption'],pv_kwh=energy['pv_kwh'],grid_import_kwh=energy['grid_import_kwh'],export_kwh=energy['export_kwh'],lcoe_eur_kwh=finance['lcoe'])
    result['lifetime_energy']=[dict(year=y+1,**{name:envelope(annual[y,:,i]) for i,name in enumerate(FIELDS)}) for y in range(s['horizon_years'])]
    if detail:
        d=data();wi=0 if dynamic else min(10, len(d['years'])-1);load,ev,_=load_profiles(s);demand=load[wi]+ev[wi] if s['ev_start_year']<=1 else load[wi]
        _,trace=dispatch_year(pv_profile(s_energy)[wi]*(1-s['pv_first_year_loss']),demand,s['battery_kwh'],s['charge_kw'],s['discharge_kw'],math.sqrt(s['roundtrip_efficiency']),s['inverter_kw'],0.,synthetic_prices()[wi],d['valid'][wi],s['pv_battery_coupling']=='dc_coupled',physical_parameters(s_energy),d['month'][wi],d['hour'][wi])
        result['detail_weather_year']=int(d['years'][wi]);result['monthly']=[]
        for m in range(1,13):
            v=trace[(d['month'][wi]==m)&d['valid'][wi]]
            result['monthly'].append(dict(month=m,pv_kwh=float(v[:,0].sum()),load_kwh=float(v[:,1].sum()),grid_import_kwh=float(v[:,2].sum()),export_kwh=float(v[:,3].sum()),direct_kwh=float(v[:,5].sum()),charge_kwh=float(v[:,6].sum()),discharge_kwh=float(v[:,7].sum())))
        result['hourly']={'columns':['pv_kwh','load_kwh','grid_import_kwh','export_kwh','soc_kwh','direct_kwh','charge_kwh','discharge_kwh'],'values':trace[d['valid'][wi]].round(6).tolist(),'hour_standard':d['hour'][wi,d['valid'][wi]].tolist(),'month_standard':d['month'][wi,d['valid'][wi]].tolist()}
        pv_detail=pv_profile(s_energy)[wi]*(1-s['pv_first_year_loss'])
        ceiling,rec=_trace_routing_telemetry(s_energy,pv_detail,demand,trace,d['month'][wi],d['hour'][wi],valid=d['valid'][wi])
        result['hourly']['soc_ceiling_sub_kwh']=ceiling[d['valid'][wi]].round(6).tolist()
        result['hourly']['charged_from_recaptured_clipping_kwh']=rec[d['valid'][wi]].round(6).tolist()
        result['hourly']['charged_from_normal_solar_kwh']=(trace[:,6]-rec)[d['valid'][wi]].round(6).tolist()

        if use_heat_pump:
            from feasibility.heatpump_integration import get_source_heat_pump_hourly, calculate_cooking_electricity
            hp_res = get_source_heat_pump_hourly(s)
            gas_alloc = hp_res['gas_allocation']
            cook_elec, _, _ = calculate_cooking_electricity(s, gas_alloc, d['valid'], d['month'], d['hour'])
            ev_eff = ev if s['ev_start_year'] <= 1 and s['load_scenario'] == 'ev' else np.zeros_like(ev)
            hp_heat = hp_res['space_compressor_electricity_kwh'] + hp_res['space_backup_electricity_kwh']
            hp_dhw = hp_res['dhw_compressor_electricity_kwh'] + hp_res['dhw_backup_electricity_kwh']
            hp_aux = hp_res['auxiliary_electricity_kwh']
            v_mask = d['valid'][wi]
            tot_demand = demand[v_mask]
            ck_v = cook_elec[wi, v_mask]
            ev_v = ev_eff[wi, v_mask]
            hp_heat_v = hp_heat[wi, v_mask]
            hp_dhw_v = hp_dhw[wi, v_mask]
            hp_aux_v = hp_aux[wi, v_mask]
            hh_v = np.maximum(0.0, tot_demand - (ck_v + ev_v + hp_heat_v + hp_dhw_v + hp_aux_v))

            end_uses_matrix = np.column_stack([hh_v, ck_v, ev_v, hp_heat_v, hp_dhw_v, hp_aux_v])
            result['hourly_end_uses'] = {
                'columns': [
                    'household_kwh',
                    'cooking_kwh',
                    'ev_kwh',
                    'heat_pump_heating_kwh',
                    'heat_pump_hot_water_kwh',
                    'heat_pump_auxiliary_kwh',
                ],
                'values': end_uses_matrix.round(6).tolist(),
                'hour_standard': d['hour'][wi, v_mask].tolist(),
                'month_standard': d['month'][wi, v_mask].tolist(),
            }
    result['gas_costs'] = build_gas_costs_audit(s)
    return result

CHART_KEYS=[k for k in ENERGY_KEYS if k not in ['load_scenario','horizon_years','pv_annual_fade','battery_calendar_fade','battery_cycle_fade','battery_replace_year']]
CHART_FIELDS={'load_kwh':1,'pv_kwh':0,'grid_import_kwh':2,'export_kwh':3,'direct_kwh':5,'charge_kwh':6,'discharge_kwh':7}

def chart_data(settings=None):
    """Bounded first-year comparisons: three loads × all weather years, no study.

    Every scenario shares the selected physical configuration. EV timing obeys
    ev_start_year, as in evaluate; year-one plots never silently bring it forward.
    Monthly and annual bands summarize complete individual weather records.
    """
    s=validate(settings)
    return _chart_cache(_energy_key(s, CHART_KEYS))

@lru_cache(maxsize=32)
def _chart_cache_cached(runtime_key, key):
    assert_runtime_key(runtime_key)
    s=defaults();s.update(thaw_settings(dict(key)));d=data();years=d['years'].tolist()
    # Charts describe the first operating year on the same calendar as evaluate.
    s['horizon_years']=1
    scenarios=[]
    def summarize(monthly):
        annual=monthly.sum(axis=1)
        return dict(annual=envelope(annual),monthly=[envelope(monthly[:,m]) for m in range(12)],annual_by_weather=annual.tolist(),monthly_by_weather=monthly.tolist())
    measured_monthly=np.tile(np.asarray(d['audit']['monthly_kwh'],float),(len(years),1))
    comparisons = ([(s['load_scenario'], 'Projected electricity')] if _dynamic_roofs() is not None else
                   [('baseline','Historical electricity'),('electrified','Gas eliminated'),('ev','Gas eliminated + EV')])
    for name,label in comparisons:
        ss=_routing_settings({**s,'load_scenario':name})
        _,_,enduses=load_profiles(ss)
        mapped=_forecast_mapped_series(ss)
        pv=mapped['mapped_pv'];demand=mapped['mapped_load']
        months=mapped['target_months'];hours=mapped['target_std_hours']
        valid=np.ones(mapped['target_len'],dtype=bool)
        monthly={field:np.zeros((len(years),12)) for field in CHART_FIELDS};annual_rows=[]
        for w in range(len(years)):
            row,trace=dispatch_year(pv[w],demand[w],ss['battery_kwh'],ss['charge_kw'],ss['discharge_kw'],math.sqrt(ss['roundtrip_efficiency']),ss['inverter_kw'],0.,mapped['mapped_pzo'][w],valid,ss['pv_battery_coupling']=='dc_coupled',physical_parameters(ss),months,hours)
            annual_rows.append(row.tolist())
            for m in range(1,13):
                v=trace[months==m]
                for field,col in CHART_FIELDS.items():monthly[field][w,m-1]=float(v[:,col].sum())
        scenarios.append(dict(id=name,label=label,series={field:summarize(values) for field,values in monthly.items()},annual_dispatch_by_weather=annual_rows,enduses=enduses))
    dynamic = _dynamic_roofs() is not None
    roofs = ([dict(r) for r in _dynamic_roofs()] if dynamic else calibration()['roofs'])
    adjusted=np.maximum(d['power']-s['shade_strength']*(d['beam_loss']+s['diffuse_suppression']*d['diffuse_loss']),0)
    for i,roof in enumerate(roofs):
        if dynamic:
            planes = list(d['audit']['plane_ids'])
            roof['selected_pvgis_ac'] = envelope(adjusted[planes.index(roof['id'])].sum(axis=1)) if roof['id'] in planes else None
        else:
            roof['selected_pvgis_ac']=envelope(adjusted[i].sum(axis=1))
    selected_config = {k:s[k] for k in CHART_KEYS if k not in LOAD_KEYS and (not dynamic or k not in LEGACY_ROOF_KEYS)}
    if dynamic:
        selected_config['roof_panels'] = s['roof_panels']
    return dict(weather_years=years,weather_count=len(years),year=1,unit='kWh electricity',range_definition='Min–max across all historical weather years; markers are medians, not probability or confidence intervals.',selected_config=selected_config,load_growth=s['load_growth'],summer_ac_multiplier=s['summer_ac_multiplier'],ev_active=s['ev_start_year']<=1,ev_start_year=s['ev_start_year'],measured=summarize(measured_monthly),scenarios=scenarios,roofs=roofs,roof_unit='kWh/kWp/year',shade_strength=s['shade_strength'],diffuse_suppression=s['diffuse_suppression'])

def _chart_cache(key):
    return _chart_cache_cached(current_runtime().key, freeze(key))
_chart_cache.cache_clear = _chart_cache_cached.cache_clear
_chart_cache.cache_info = _chart_cache_cached.cache_info

def fair_price(settings=None,metric='npv',target=0):
    s=validate(settings);target=float(target)
    if not math.isfinite(target):raise ValueError('target must be finite')
    metric={'npv_eur':'npv','payback_years':'simple_payback','lcoe_eur_kwh':'lcoe'}.get(metric,metric)
    if metric not in ['npv','irr','simple_payback','discounted_payback','lcoe']:raise ValueError('Unsupported fair-price metric')
    if metric=='irr' and target<=-1:raise ValueError('IRR target must exceed -100%')
    if metric=='lcoe' and target<=0:raise ValueError('LCOE target must be positive')
    if metric.endswith('payback') and not 0<target<=s['horizon_years']:raise ValueError('Payback target must be within project horizon and above zero')
    if _panel_total(s)==0:return dict(status='NO_PV',price_eur=None,metric=metric,target=target)
    version=price_fingerprint(s)
    dispatch_settings=_routing_settings(s)
    annual,_=_energy_cache(_energy_key(dispatch_settings),version)
    kwp=_panel_total(s)*s['panel_watts']/1000
    @lru_cache(maxsize=512)
    def all_residuals(price):
        f,_=_financial({**s,'quote_total':float(price),'quote_vat_included':True},annual,kwp,calculate_irr=False)
        if metric=='npv':return np.array(f['pv_incremental']['npv_by_weather'])-target
        if metric=='irr':return np.array([npv(flows,target) for flows in f['cashflows_by_weather']['pv_incremental']])
        if metric.endswith('payback'):
            flows=np.array(f['cashflows_by_weather']['pv_incremental']);rate=f['nominal_discount_rate'] if metric=='discounted_payback' else 0.
            discounted=flows/(1+rate)**np.arange(flows.shape[1]);cumulative=np.cumsum(discounted,axis=1)
            i=int(target);fraction=target-i
            at_target=cumulative[:,i]+(fraction*discounted[:,i+1] if fraction and i+1<flows.shape[1] else 0.)
            # Requiring every later cumulative balance to stay positive preserves
            # replacement-cost reversals and includes never-recovered weather years.
            later=cumulative[:,i+1:]
            return np.minimum(at_target,later.min(axis=1)) if later.shape[1] else at_target
        return np.array(f['lcoe_by_weather'])-target
    def difference(price):return float(np.median(all_residuals(price)))
    lo,hi=0.,150000.;a,b=difference(lo),difference(hi)
    if not np.isfinite(a) or not np.isfinite(b) or a*b>0:return dict(status='NOT_BRACKETED',price_eur=None,metric=metric,target=target,bracket_eur=[lo,hi],endpoint_residuals=[None if not np.isfinite(a) else a,None if not np.isfinite(b) else b])
    answer=brentq(difference,lo,hi,xtol=.01)
    residual_at_root=float(difference(answer))
    if not math.isfinite(residual_at_root) or abs(residual_at_root)>.10:
        return dict(status='UNRESOLVED_ROOT',price_eur=None,metric=metric,target=target,residual=residual_at_root)
    roots=[]
    if metric in ['npv','irr','lcoe'] or metric.endswith('payback'):
        for wi in range(len(data()['years'])):
            def residual(price):return all_residuals(price)[wi]
            ra,rb=residual(lo),residual(hi)
            roots.append(float(brentq(residual,lo,hi,xtol=.01)) if ra*rb<=0 else None)
    return dict(status='SCENARIO_PENDING',price_eur=float(answer),price_range_eur=envelope([x if x is not None else np.nan for x in roots]) if roots else None,roots_by_weather=roots,metric=metric,target=target,bracket_eur=[lo,hi],residual=residual_at_root,residual_definition='Persistent cumulative discounted/undiscounted cash-flow constraint in EUR; payback target is a maximum, not a guaranteed exact crossing time' if metric.endswith('payback') else 'NPV at target IRR equals zero; multiple IRR roots can exist and the target is not necessarily the unique return' if metric=='irr' else 'Metric minus target',definition='VAT-inclusive solar/storage quote; other settings and conversion capex unchanged')

def _pareto(rows):
    for r in rows:
        r['pareto']=False
        if r['theoretical']:continue
        a=r['metrics']
        a_spb=a.get('simple_payback_of_mean_cash_flows') if 'simple_payback_of_mean_cash_flows' in a else (a.get('payback_years', {}).get('median') if isinstance(a.get('payback_years'), dict) else a.get('payback_years'))
        a_dpb=a.get('discounted_payback_of_mean_cash_flows') if 'discounted_payback_of_mean_cash_flows' in a else (a.get('discounted_payback_years', {}).get('median') if isinstance(a.get('discounted_payback_years'), dict) else a.get('discounted_payback_years'))
        av=[a['npv_eur']['mean'],a['autarky']['median'],a['self_consumption']['median'],-(a_spb if a_spb is not None else 1e6),-(a_dpb if a_dpb is not None else 1e6)]
        dominated=False
        for q in rows:
            if q['theoretical'] or q['load_scenario']!=r['load_scenario']:continue
            b=q['metrics']
            b_spb=b.get('simple_payback_of_mean_cash_flows') if 'simple_payback_of_mean_cash_flows' in b else (b.get('payback_years', {}).get('median') if isinstance(b.get('payback_years'), dict) else b.get('payback_years'))
            b_dpb=b.get('discounted_payback_of_mean_cash_flows') if 'discounted_payback_of_mean_cash_flows' in b else (b.get('discounted_payback_years', {}).get('median') if isinstance(b.get('discounted_payback_years'), dict) else b.get('discounted_payback_years'))
            bv=[b['npv_eur']['mean'],b['autarky']['median'],b['self_consumption']['median'],-(b_spb if b_spb is not None else 1e6),-(b_dpb if b_dpb is not None else 1e6)]
            if all(x>=y-1e-8 for x,y in zip(bv,av)) and any(x>y+1e-8 for x,y in zip(bv,av)):dominated=True;break
        r['pareto']=not dominated
    return rows

def _dynamic_sweep(s):
    """Profile-bounded CLI candidates; household presets belong to legacy mode only."""
    maxima = _search_maxima(s)
    layouts = [('no_pv', dict.fromkeys(maxima, 0)),
               ('selected', _panel_counts(s)), ('max', maxima)]
    optimized = optimize_configuration(s, {'objective': 'npv_eur',
                                          'battery_max_modules': s['battery_modules']})
    rows = []
    seen = set()
    candidates = [(name, {**s, **_roof_settings(counts),
                          'battery_kwh': s['battery_kwh'] if any(counts.values()) else 0,
                          'battery_modules': s['battery_modules'] if any(counts.values()) else 0,
                          'battery_secondary_modules': s['battery_secondary_modules'] if any(counts.values()) else 0})
                  for name, counts in layouts]
    candidates.append(('optimized', optimized['best_candidate']['settings']))
    for name, params in candidates:
        params = {**params, 'quote_total': None}
        sig = (tuple(_panel_counts(params).items()), params['battery_modules'],
               params['battery_secondary_modules'])
        if sig in seen:
            continue
        seen.add(sig)
        result = evaluate(params)
        row = {k: result[k] for k in ('layout', 'metrics', 'energy', 'finance', 'settings')}
        row.update(id=name, label=name, load_scenario=s['load_scenario'],
                   battery_kwh=params['battery_kwh'], theoretical=False)
        rows.append(row)
    _pareto(rows)
    return dict(status='SCENARIO_PENDING', candidates=rows, scenario_envelopes=[],
                pareto_ids=[r['id'] for r in rows if r['pareto']],
                search=optimized['search'],
                definition='Profile-bounded zero, selected, maximum and bounded optimizer layouts for the selected projected load.',
                limitations=optimized['limitations'])


def sweep(settings=None):
    s=validate(settings);rows=[]
    if _dynamic_roofs() is not None:
        return _dynamic_sweep(s)
    layouts=[(name, dict(values)) for name, values in _legacy_sizing_reference()['sweep_layouts']]
    custom_layout={n+'_panels':s[n+'_panels'] for n in ['south','west','east','dormer','flat','secondary_south']}
    custom_layout['dormer_south_proxy']=s['dormer_south_proxy']
    layouts.append(('custom',custom_layout))
    clear={n+'_panels':0 for n in ['south','west','east','dormer','flat','secondary_south']}
    is_mixed=bool(s.get('battery_mixed_sizes',False))
    if not is_mixed:
        battery_options=sorted(set([0,5,10,15,s['battery_kwh']]))
        for scenario in ['baseline','electrified','ev']:
            for label,layout in layouts:
                # Preserve the selected usable total in the custom row and expose
                # it among the established preset sizes for every preset layout.
                options=[0] if label=='no_pv' else battery_options
                if label=='custom':options=[s['battery_kwh']] if any(custom_layout[k+'_panels'] for k in ['south','west','east','dormer','flat','secondary_south']) else [0]
                for battery in options:
                    params={**s,**clear,**layout,'load_scenario':scenario,'battery_kwh':battery,'quote_total':None,'dormer_south_proxy':layout.get('dormer_south_proxy',False)}
                    _normalize_battery(params, {'battery_kwh'})
                    if label!='custom':
                        inverter_bounds=SPEC['inverter_kw'][2]
                        params['inverter_kw']=min(inverter_bounds[1],max(inverter_bounds[0],math.ceil(sum(params[n+'_panels'] for n in ['south','west','east','dormer','flat','secondary_south'])*params['panel_watts']/1000)))
                    r=evaluate(params);row={k:r[k] for k in ['layout','metrics','energy','finance']};row.update(id=f'{scenario}_{label}_{battery}',label=label,load_scenario=scenario,battery_kwh=battery,theoretical=r['layout']['theoretical'],settings=r.get('settings',params));rows.append(row)
    else:
        preset_comps=enumerate_battery_compositions_by_capacity(0.0,15.0,settings=s)
        sel_comp=calculate_battery_composition(total_modules=s['battery_modules'],secondary_modules=s['battery_secondary_modules'],module_kwh=s['battery_module_kwh'],secondary_module_kwh=s['battery_secondary_module_kwh'],mixed_sizes=True)
        zero_comp=calculate_battery_composition(total_modules=0,secondary_modules=0,module_kwh=s['battery_module_kwh'],secondary_module_kwh=s['battery_secondary_module_kwh'],mixed_sizes=True)
        seen_ids=set()
        mixed_compositions=[]
        for c in preset_comps:
            if c['id'] not in seen_ids:
                seen_ids.add(c['id'])
                mixed_compositions.append(c)
        if sel_comp['id'] not in seen_ids:
            seen_ids.add(sel_comp['id'])
            mixed_compositions.append(sel_comp)
        mixed_compositions.sort(key=lambda c:(round(c['battery_kwh'],9),c['battery_modules'],c['battery_secondary_modules']))

        for scenario in ['baseline','electrified','ev']:
            for label,layout in layouts:
                if label=='no_pv':
                    comps=[zero_comp]
                elif label=='custom':
                    has_panels=any(custom_layout[k+'_panels'] for k in ['south','west','east','dormer','flat','secondary_south'])
                    comps=[sel_comp] if has_panels else [zero_comp]
                else:
                    comps=mixed_compositions

                for comp in comps:
                    params={
                        **s,
                        **clear,
                        **layout,
                        'load_scenario':scenario,
                        'battery_mixed_sizes':True,
                        'battery_modules':comp['battery_modules'],
                        'battery_secondary_modules':comp['battery_secondary_modules'],
                        'battery_module_kwh':comp['battery_module_kwh'],
                        'battery_secondary_module_kwh':comp['battery_secondary_module_kwh'],
                        'battery_kwh':comp['battery_kwh'],
                        'quote_total':None,
                        'dormer_south_proxy':layout.get('dormer_south_proxy',False)
                    }
                    _normalize_battery(params,{'battery_modules','battery_secondary_modules'})
                    if label!='custom':
                        inverter_bounds=SPEC['inverter_kw'][2]
                        params['inverter_kw']=min(inverter_bounds[1],max(inverter_bounds[0],math.ceil(sum(params[n+'_panels'] for n in ['south','west','east','dormer','flat','secondary_south'])*params['panel_watts']/1000)))
                    r=evaluate(params)
                    row={k:r[k] for k in ['layout','metrics','energy','finance']}
                    row_id=f'{scenario}_{label}_{comp["battery_kwh"]:g}_{comp["battery_secondary_modules"]}sec'
                    row.update(
                        id=row_id,
                        label=label,
                        load_scenario=scenario,
                        battery_kwh=comp['battery_kwh'],
                        battery_modules=comp['battery_modules'],
                        battery_secondary_modules=comp['battery_secondary_modules'],
                        first_modules=comp.get('first_modules',comp['battery_modules']-comp['battery_secondary_modules']),
                        composition=comp,
                        theoretical=r['layout']['theoretical'],
                        settings=r.get('settings',params)
                    )
                    rows.append(row)
    _pareto(rows)
    scenarios=[]
    # Joint coherent paths, not independent cherry-picked best/worst parameters.
    for path,rate in [('lower',-.02),('flat',0),('higher',.03)]:
        for deduction in current_policy().deduction_rates():
            params={**s,'energy_real_escalation':rate,'gas_real_escalation':rate,'deduction_rate':deduction}
            r=evaluate(params);scenarios.append(dict(id=f'{path}_deduction_{deduction}',energy_path=path,deduction_rate=deduction,metrics=r['metrics'],finance=r['finance']))
    inverter_bounds=SPEC['inverter_kw'][2]
    return dict(status='SCENARIO_PENDING',candidates=rows,scenario_envelopes=scenarios,pareto_ids=[r['id'] for r in rows if r['pareto']],definition=f'Practical Pareto: maximize weather-median unlevered PV NPV, autarky, self-consumption; minimize simple and discounted persistent payback. Theoretical layouts excluded. Preset layouts size inverters to the next integer kW within {inverter_bounds[0]}–{inverter_bounds[1]} kW modeling bounds; the custom layout retains the selected inverter setting.',limitations=[f'Weather envelopes include all {len(data()["years"])} years; joint price paths are scenarios, not market distributions.','Scenario envelopes apply to selected layout; candidate grid uses current selected financial assumptions.'])


OPTIMIZE_ROOFS=LEGACY_ROOFS  # Frozen legacy compatibility order only.
OPTIMIZE_OBJECTIVES=('autarky','npv_eur','self_consumption','payback_years','discounted_payback_years')
OPTIMIZE_EXHAUSTIVE_LIMIT=1500
OPTIMIZE_EVALUATION_BUDGET=400

def _legacy_sizing_reference():
    ctx = current_runtime()
    if 'sizing_reference' not in ctx.metadata:
        raise ValueError('Explicit legacy sizing reference required')
    return ctx.metadata['sizing_reference']


def _search_roofs():
    roofs = _dynamic_roofs()
    return tuple(_legacy_sizing_reference()['roof_ids']) if roofs is None else tuple(r['id'] for r in roofs)


def _roof_settings(counts):
    """Adapt canonical plane counts only at the legacy settings boundary."""
    return ({r + '_panels': n for r, n in counts.items()} if _dynamic_roofs() is None
            else {'roof_panels': dict(counts)})


def _search_maxima(s, requested=None):
    roofs = _dynamic_roofs()
    if roofs is None:
        limits = _legacy_sizing_reference()['panel_maxima']
    else:
        from .roof_config import maximum_panel_count
        limits = {r['id']: maximum_panel_count(r['max_kwp'], s['panel_watts']) for r in roofs}
    if requested is None:
        requested = {}
    if not isinstance(requested, dict):
        raise ValueError('panel_maxima must be an object')
    unknown = set(requested) - set(limits)
    if unknown:
        raise ValueError('Unknown panel maxima: ' + ', '.join(sorted(map(str, unknown))))
    result = {r: _optimization_integer(requested.get(r, limits[r]), 'panel_maxima.' + r)
              for r in limits}
    if roofs is not None and any(result[r] > limits[r] for r in limits):
        raise ValueError('panel_maxima cannot exceed household roof capacity')
    return result


def _roof_signature(counts):
    """Plane IDs participate in v2 identity; legacy identifiers stay frozen."""
    return tuple(counts) if _dynamic_roofs() is None else tuple(zip(_search_roofs(), counts))


def _candidate_roof_order(candidate, priority=None):
    roofs = candidate.get('roof_counts') or {}
    order = tuple(priority) if priority is not None else _search_roofs()
    # Include every ID even for standalone candidate comparisons outside a bound worker.
    return order + tuple(sorted(set(roofs) - set(order)))


def _optimization_integer(value,name):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or int(value)!=value or value<0:
        raise ValueError(name+' must be a finite nonnegative integer')
    return int(value)

def _optimization_objective_value(result,objective):
    if objective=='npv_eur':
        value=result['metrics']['npv_eur']['mean']
    elif objective=='payback_years':
        value=result['metrics'].get('simple_payback_of_mean_cash_flows')
    elif objective=='discounted_payback_years':
        value=result['metrics'].get('discounted_payback_of_mean_cash_flows')
    else:
        value=result['metrics'][objective]['median']
    if value is None:
        return -1e12 if objective not in ('payback_years','discounted_payback_years') else 1e12
    return float(value)

def optimize_configuration(settings=None,search=None):
    """Find a discrete roof-by-roof panel and battery-module configuration.

    Small spaces are enumerated completely. Large spaces use deterministic
    sampled starts and coordinate refinement, and are explicitly labeled as
    best-found rather than certified global optima.
    """
    raw={} if settings is None else settings
    if not isinstance(raw,dict):raise ValueError('settings must be a JSON object')
    # Search controls determine the optimizer bounds. Existing installation
    # counts and a legacy total are not optimizer constraints and can even be
    # internally inconsistent while editing a new zero-PV configuration.
    base_raw=dict(raw)
    base_raw.update(_roof_settings(dict.fromkeys(_search_roofs(), 0)))
    base_raw.update(battery_modules=0,battery_secondary_modules=0,battery_kwh=0)
    s=validate(base_raw)
    if search is None:search={}
    if not isinstance(search,dict):raise ValueError('search must be a JSON object')
    allowed={'objective','battery_min_modules','battery_max_modules','panel_maxima','include_theoretical'}
    unknown=set(search)-allowed
    if unknown:raise ValueError('Unknown optimizer settings: '+', '.join(sorted(unknown)))
    objective=search.get('objective','autarky')
    if objective not in OPTIMIZE_OBJECTIVES:raise ValueError('objective must be one of '+', '.join(OPTIMIZE_OBJECTIVES))
    batt_min=_optimization_integer(search.get('battery_min_modules',0),'battery_min_modules')
    batt_max=_optimization_integer(search.get('battery_max_modules',4),'battery_max_modules')
    if batt_min>batt_max:raise ValueError('battery_min_modules must be <= battery_max_modules')
    if 'panel_maxima' in search and not isinstance(search['panel_maxima'], dict):
        raise ValueError('panel_maxima must be an object')
    roof_max=_search_maxima(s, search.get('panel_maxima'))
    include_theoretical=search.get('include_theoretical',True)
    if not isinstance(include_theoretical,bool):raise ValueError('include_theoretical must be boolean')
    # Optimizer layouts use the user's usable module capacity as entered. A
    # legacy explicit total may have normalized that linked value in validate.
    mixed=bool(raw.get('battery_mixed_sizes',s.get('battery_mixed_sizes',False)))
    module_capacity=float(raw.get('battery_module_kwh',s['battery_module_kwh']))
    if not math.isfinite(module_capacity) or module_capacity<0:raise ValueError('battery_module_kwh must be finite and nonnegative')
    secondary_capacity=float(raw.get('battery_secondary_module_kwh',s['battery_secondary_module_kwh']))
    if not math.isfinite(secondary_capacity) or secondary_capacity<0:raise ValueError('battery_secondary_module_kwh must be finite and nonnegative')
    if batt_max:
        if module_capacity<=0:raise ValueError('battery_module_kwh must be positive when searching battery modules')
        if mixed and secondary_capacity<=0:raise ValueError('battery_secondary_module_kwh must be positive when searching battery modules')
    # Keep requested ranges lazy: very large user limits must enter the bounded
    # best-found path without first allocating every integer in those ranges.
    module_values=range(batt_min,batt_max+1)
    roof_space=math.prod(roof_max[name]+1 for name in _search_roofs())
    if not mixed:
        batt_space=batt_max-batt_min+1
    else:
        batt_space=((batt_max-batt_min+1)*(batt_min+batt_max+2))//2
    space=roof_space*batt_space
    exact=space<=OPTIMIZE_EXHAUSTIVE_LIMIT
    budget=space if exact else OPTIMIZE_EVALUATION_BUDGET
    rows=[];seen=set();best_result=None;best_key=None
    roof_limits=_legacy_sizing_reference()['panel_maxima'] if _dynamic_roofs() is None else roof_max
    def consider(counts,modules,secondary=0):
        nonlocal best_result,best_key
        counts=tuple(int(x) for x in counts);modules=int(modules);secondary=int(secondary)
        if not mixed:secondary=0
        signature=_roof_signature(counts)+(modules,secondary)
        if signature in seen or len(rows)>=budget:return
        seen.add(signature)
        if s.get('pv_max_kwp') is not None and sum(counts)*s['panel_watts']/1000.0 > float(s['pv_max_kwp']) + 1e-6:return
        if _dynamic_roofs() is None and not include_theoretical and (any(counts[i]>roof_limits[name] for i,name in enumerate(_search_roofs())) or counts[3] or counts[4] or counts[5] or counts[0]>15 or sum(counts)*s['panel_watts']/1000>20):return
        if not any(counts) and modules>0:return
        comp=calculate_battery_composition(
            total_modules=modules,
            secondary_modules=secondary,
            module_kwh=module_capacity,
            secondary_module_kwh=secondary_capacity,
            mixed_sizes=mixed,
        )
        params={
            **s,
            **_roof_settings(dict(zip(_search_roofs(), counts))),
            'battery_mixed_sizes':mixed,
            'battery_modules':modules,
            'battery_secondary_modules':comp['battery_secondary_modules'],
            'battery_module_kwh':module_capacity,
            'battery_secondary_module_kwh':secondary_capacity,
            'battery_kwh':comp['battery_kwh'],
            'quote_total':None,
            **({'dormer_south_proxy': False} if _dynamic_roofs() is None else {}),
        }
        # Existing proxy mode disallows several physical roof classes. Search
        # counts are still model inputs, so let validate reject an invalid row.
        result=evaluate(params)
        layout=result['layout'];metrics=result['metrics'];finance=result['finance']
        label=' + '.join(f'{name} {count}' for name,count in zip(_search_roofs(),counts) if count) or 'No PV'
        if modules:
            if not mixed:
                label+=f' · {modules} battery module'+('s' if modules!=1 else '')
            else:
                first_mods=comp['first_modules']
                sec_mods=comp['battery_secondary_modules']
                parts=[]
                if first_mods>0:parts.append(f'{first_mods} × {module_capacity:g}')
                if sec_mods>0:parts.append(f'{sec_mods} × {secondary_capacity:g}')
                label+=f' · {" + ".join(parts)} kWh'
        ident='opt_'+'_'.join(str(x) for x in signature)
        compact_metrics={k:metrics[k] for k in ['npv_eur','autarky','self_consumption','payback_years','discounted_payback_years','simple_payback_of_mean_cash_flows','discounted_payback_of_mean_cash_flows']}
        compact_finance=dict(capex_eur=finance['capex_eur'],npv_eur=finance['pv_incremental']['npv'],package_npv_eur=finance['whole_package']['npv'],lcoe_eur_kwh=finance['lcoe'])
        row=dict(
            id=ident,
            label=label,
            load_scenario=result['settings']['load_scenario'],
            battery_kwh=result['settings']['battery_kwh'],
            battery_modules=modules,
            battery_secondary_modules=comp['battery_secondary_modules'],
            battery_module_kwh=module_capacity,
            battery_secondary_module_kwh=secondary_capacity,
            battery_mixed_sizes=mixed,
            theoretical=layout['theoretical'],
            layout=layout,
            metrics=compact_metrics,
            finance=compact_finance,
            settings=result['settings'],
        )
        rows.append(row)
        score=_optimization_objective_value(result,objective)
        # For payback metrics lower is better; all other objectives maximize.
        sort_score=score if objective not in ('payback_years','discounted_payback_years') else -score
        capex=float(finance.get('capex_eur',0))
        tie=(sort_score,-capex,-sum(counts),-modules,-secondary,tuple(-x for x in counts))
        if best_key is None or tie>best_key:best_key=tie;best_result=result

    if exact:
        import itertools
        if not mixed:
            for values in itertools.product(*(range(roof_max[name]+1) for name in _search_roofs()),module_values):
                consider(values[:-1],values[-1],0)
        else:
            for values in itertools.product(*(range(roof_max[name]+1) for name in _search_roofs()),module_values):
                roof_counts=values[:-1];m=values[-1]
                for s_val in range(0,m+1):
                    consider(roof_counts,m,s_val)
    else:
        # Seed boundary, single-roof, midpoint, and battery endpoint layouts.
        mins=[0]*len(_search_roofs());maxs=[roof_max[n] for n in _search_roofs()]
        seeds=[mins,maxs]
        for i,maximum in enumerate(maxs):
            for value in sorted(set([0,maximum,maximum//2])):
                seed=mins.copy();seed[i]=value;seeds.append(seed)
        module_seeds=sorted(set([batt_min,batt_max,(batt_min+batt_max)//2]))
        for seed in seeds:
            for modules in module_seeds:
                if not mixed:
                    consider(seed,modules,0)
                else:
                    for s_val in sorted(set([0,modules,modules//2])):
                        consider(seed,modules,s_val)
        # Deterministic coarse coordinate sweeps, followed by local integer
        # refinement around the best incumbent until the explicit work budget.
        coarse=[]
        for name in _search_roofs():
            maximum=roof_max[name]
            vals=sorted(set([0,maximum,maximum//4,maximum//2,(3*maximum)//4]))
            coarse.append(vals)
        for i,vals in enumerate(coarse):
            incumbent=_panel_counts(best_result['settings'])[_search_roofs()[i]] if best_result else 0
            for value in vals+[incumbent]:
                base=[_panel_counts((best_result or {}).get('settings',s)).get(n,0) for n in _search_roofs()]
                base[i]=value
                for modules in module_seeds:
                    if not mixed:
                        consider(base,modules,0)
                    else:
                        for s_val in sorted(set([0,modules,modules//2])):
                            consider(base,modules,s_val)
        while len(rows)<budget and best_result:
            before=len(rows);base=[_panel_counts(best_result['settings'])[n] for n in _search_roofs()]
            best_modules=best_result['settings']['battery_modules']
            best_sec=best_result['settings'].get('battery_secondary_modules',0) if mixed else 0
            for i,name in enumerate(_search_roofs()):
                lo=max(0,base[i]-3);hi=min(roof_max[name],base[i]+3)
                for value in range(lo,hi+1):
                    trial=base.copy();trial[i]=value;consider(trial,best_modules,best_sec)
            for modules in range(max(batt_min,best_modules-2),min(batt_max,best_modules+2)+1):
                if not mixed:
                    consider(base,modules,0)
                else:
                    for s_val in sorted(set([min(modules,best_sec),0,modules,modules//2])):
                        consider(base,modules,s_val)
            if mixed and best_modules>0:
                lo_s=max(0,best_sec-2);hi_s=min(best_modules,best_sec+2)
                for s_val in range(lo_s,hi_s+1):
                    consider(base,best_modules,s_val)
                consider(base,best_modules,0)
                consider(base,best_modules,best_modules)
            if len(rows)==before:break
    if not rows:raise ValueError('No feasible configurations within the requested search bounds')
    _pareto(rows)
    best_id=next(row['id'] for row in rows if row['settings']==best_result['settings'])
    summary=dict(objective=objective,exact=exact,search_method='exhaustive_integer_enumeration' if exact else 'sampled_discrete_coordinate_refinement',evaluated_count=len(rows),search_space_size=space,evaluation_budget=budget,best_found=not exact,certified_global_optimum=exact,requested_panel_maxima=roof_max,battery_min_modules=batt_min,battery_max_modules=batt_max,include_theoretical=include_theoretical,best_at_panel_maxima={name:_panel_counts(best_result['settings'])[name]==roof_max[name] for name in _search_roofs()},best_at_battery_maximum=best_result['settings']['battery_modules']==batt_max)
    return dict(status='SCENARIO_PENDING',objective=objective,best_candidate=best_result,recommended_id=best_id,candidates=rows,pareto_ids=[row['id'] for row in rows if row['pareto']],search=summary,definition='Candidate rows are compact; best_candidate contains the full evaluated result for applying or inspecting the selected configuration. Candidate layouts use the selected inverter, charge/discharge limits, panel wattage, usable battery module capacity, aging, finance, and household scenario. Alternate layouts use the benchmark cost model instead of any selected quote total.',limitations=['Large searches are best-found results from deterministic discrete coordinate refinement and are not certified global optima.'] if not exact else [])

def calibration(settings=None):
    """Every numerical report assertion is emitted here, never computed in prose."""
    if _thermal_reference() is not None:
        ref = _thermal_reference()
        s = defaults() if settings is None else settings
        from .heatpump_finance import calculate_thermal_finance
        return dict(annual_kwh=data()['audit']['annual_kwh'],
            annual_gas_smc=ref.annual_gas_smc if any(u.carrier == 'gas' for u in ref.end_uses.values()) else None,
            annual_electric_bill_eur=electric_bill(data()['audit']['annual_kwh'], s),
            thermal_baseline=calculate_thermal_finance({**s, 'load_scenario':'baseline'}),
            invoice=None, research=None, legacy_reference_applicable=False)
    s=defaults() if settings is None else settings;audit=data()['audit'];research=mutable_copy(current_runtime().metadata['calibration'])
    benchmark = current_runtime().metadata['presentation']['calibration_benchmarks']
    roofs=[]
    for i,(name,yields) in enumerate(audit['pvgis_yields'].items()):
        dc=audit['roofs'][i]['google_dc_kwh_kwp']
        # Explicit alternative Google AC bridge. Separate from PVGIS 14% losses.
        factors={'inverter_efficiency':.97,'wiring':.98,'soiling':.98,'thermal':.93,'other':.98,'clipping':1.0}
        ac_factor=math.prod(factors.values())
        roofs.append(dict(orientation=name,pvgis_unshaded=envelope(yields['unshaded_annual']),pvgis_beam_shaded=envelope(yields['beam_shaded_annual']),pvgis_all_light_shaded=envelope(yields['all_radiation_shaded_annual']),google_dc_kwh_kwp=dc,google_ac_factors=factors,google_ac_status='placeholder',google_ac_reason='Illustrative DC-to-AC bridge; equipment efficiencies, temperature and clipping not measured. Do not apply again to PVGIS AC production.',google_ac_factor=ac_factor,google_ac_kwh_kwp=dc*ac_factor if dc else None,raster_annual_flux=audit['roofs'][i]['annual_flux_kwh_kwp']))
    return dict(date=benchmark['date'],invoice=current_invoice_check(s),annual_kwh=audit['annual_kwh'],annual_gas_smc=float(current_runtime().thermal_reference['GAS_MONTHLY'].sum()),annual_electric_bill_eur=electric_bill(audit['annual_kwh'],s),annual_gas_runrate_eur=gas_bill(s),marginal_import_eur_kwh=(s['retail_commodity']+s['retail_regulated']+s['electric_excise'])*(1+s['electric_vat']),fixed_electric_eur_year=electric_bill(0,s),original_cost_formula_6kwp_eur=benchmark['original_fixed_cost']+benchmark['original_cost_kwp']*benchmark['benchmark_kwp'],calibrated_6kwp_eur=installed_capex({**s,'battery_kwh':0},benchmark['benchmark_kwp']),provided_capture_eur_kwh=benchmark['provided_capture_eur_kwh'],base_times_capture_eur_kwh=s['export_market_price']*s['export_capture_factor'],capture_difference_eur_kwh=s['export_market_price']*s['export_capture_factor']-benchmark['provided_capture_eur_kwh'],roofs=roofs,weather_count=len(data()['years']),hour_count=int(data()['valid'].sum()),research=research)

def quote_evaluate(settings=None,quote=None):
    """Normalize an itemized quote and compare claims using the same simulator."""
    s,q,total,populated=normalize_quote(settings,quote)
    warnings=[]
    result=evaluate(s);checks=[]
    s_route=_routing_settings(s)
    key=_energy_key(s_route)
    version=price_fingerprint(s_route)
    fc_res=_forecast_2027_runs(key,version)
    headline_flows=_ensemble_annual_flows(s_route,fc_res)
    if 'forecast_year' in fc_res:
        headline_flows['forecast_year']=fc_res['forecast_year']
    result['headline_energy_flows']=headline_flows
    for field,metric,mult in [('claimed_yield_kwh','pv_kwh',1),('claimed_self_consumption_pct','self_consumption',.01),('claimed_payback_years','payback_years',1)]:
        if q.get(field) in [None,'']:continue
        claim=float(q[field])*mult
        if not math.isfinite(claim):raise ValueError('Claim must be finite')
        band=result['metrics'][metric];lo,hi=band['min'],band['max']
        outside=(lo is not None and claim<lo) or (hi is not None and claim>hi)
        if metric=='payback_years' and lo is None and claim<=s['horizon_years']:outside=True
        checks.append(dict(field=field,claim=claim,simulated=band,outside_weather_range=outside,interpretation='Compare definitions and assumptions; weather range is not a warranty.'))
    missing=[k for k in ['module_model','inverter_model','battery_model','datasheet_urls','payment_schedule','layout_setbacks','warranties','monitoring_fees','inverter_replacement_terms','degradation_curves','power_curves','roundtrip_efficiency','continuous_charge_kw','continuous_discharge_kw'] if not q.get(k)]
    warnings+=['Spatial layout and strings are not yet validated: segment-average raster sampling is used.','Payment schedule is retained as quote evidence; financial model assumes upfront capital at t=0 and year-end operating cash flows.']
    if missing:warnings.append('Quote evidence still missing: '+', '.join(missing))
    return dict(result=result,headline_energy_flows=headline_flows,charts=chart_data(s),quote=q,gas_costs=result.get('gas_costs'),itemized_total_eur=total if populated else None,claims=checks,missing_fields=missing,warnings=warnings)

def normalize_quote(settings=None,quote=None):
    """Return validated settings and quote totals after applying quote overrides."""
    s=validate(settings)
    if quote is not None and not isinstance(quote,dict):raise ValueError('Quote must be an object')
    q=quote or {};items=q.get('line_items',[]);total=0.;populated=0
    if not isinstance(items,list) or len(items)>100:raise ValueError('At most 100 quote lines')
    for line in items:
        if not isinstance(line,dict):raise ValueError('Each quote line must be an object')
        if line.get('net_eur') in [None,'']:continue
        if line.get('scope','solar_storage')!='solar_storage':raise ValueError('Quote line scope must be solar_storage; conversion costs belong in separate controls')
        net=float(line['net_eur']);vat=float(line.get('vat',.1));quantity=float(line.get('quantity',1))
        if not all(math.isfinite(x) for x in [net,vat,quantity]) or min(net,vat,quantity)<0 or vat>1:raise ValueError('Invalid quote line amount or VAT')
        total+=quantity*net*(1+vat);populated+=1
    use_itemized = q.get('price_basis') not in ('modeled', 'summary_candidate') and q.get('use_itemized', True) is not False
    if populated and use_itemized:
        s.update(quote_total=total, quote_vat_included=True)
    for field,key in [('roundtrip_efficiency','roundtrip_efficiency'),('continuous_charge_kw','charge_kw'),('continuous_discharge_kw','discharge_kw')]:
        if q.get(field) not in [None,'']:s[key]=float(q[field])
    if q.get('battery_nameplate_kwh') not in [None,''] and q.get('battery_dod') not in [None,'']:
        nameplate=float(q['battery_nameplate_kwh']);dod=float(q['battery_dod'])
        if not math.isfinite(nameplate) or nameplate<0 or not 0<dod<=1:raise ValueError('Invalid battery nameplate or DoD')
        s['battery_kwh']=nameplate*dod
    if q.get('price_basis') == 'summary_candidate':
        reference = q.get('reference_settings')
        if not isinstance(reference, dict):
            raise ValueError('Summary candidate requires reference settings')
        reference = validate(reference)
        if reference.get('quote_total') is not None:
            base_kwp = _panel_total(reference) * reference['panel_watts'] / 1000
            candidate_kwp = _panel_total(s) * s['panel_watts'] / 1000
            modeled_base = installed_capex({**reference, 'quote_total': None}, base_kwp)
            modeled_candidate = installed_capex({**s, 'quote_total': None, 'quote_vat_included': reference['quote_vat_included']}, candidate_kwp)
            anchored_cost = installed_capex(reference, base_kwp) + modeled_candidate - modeled_base
            s.update(quote_total=max(0., anchored_cost) if candidate_kwp > 0 else 0., quote_vat_included=True)
    s=validate(s)
    return s,q,total,populated

def solve_loan_amount(settings=None,quote=None):
    """Scan feasible principals and minimize median persistent discounted equity recovery."""
    s,_,_,_=normalize_quote(settings,quote)
    horizon=s['horizon_years'];apr=s['loan_apr'];years=s['loan_years']
    counts=_panel_total(s);kwp=counts*s['panel_watts']/1000
    version=price_fingerprint(s)
    dispatch_settings=_routing_settings(s)
    annual,_=_energy_cache(_energy_key(dispatch_settings),version)
    base_finance,_=_financial({**s,'loan_mode':'amount','loan_amount':0.,'loan_fraction':0.},annual,kwp,calculate_irr=False)
    base=np.asarray(base_finance['cashflows_by_weather']['equity'],float)
    eligible_cap=base_finance['capex_eur']+(base_finance['conversion_capex_eur'] if s['loan_include_electrification'] else 0.)
    eligible_cap=min(float(eligible_cap),float(SPEC['loan_amount'][2][1]))
    payment_factor=(apr/12)/(1-(1+apr/12)**(-years*12)) if apr else 1/(years*12)
    max_payment=s['loan_max_monthly_payment']
    upfront_fee=float(s.get('loan_upfront_fees_or_tax',16.0) or 0.0)
    if max_payment is None:
        search_cap=eligible_cap
    else:
        affordable_cap=max(0.,float(max_payment)/payment_factor-upfront_fee)
        search_cap=min(eligible_cap,affordable_cap)
    integer_cap=int(math.floor(search_cap))
    candidates=np.arange(integer_cap+1,dtype=float)
    if search_cap-integer_cap>1e-9:candidates=np.concatenate((candidates,[search_cap]))
    # Preserve the exact eligible boundary when it is also affordable.
    eligible_amort=eligible_cap+upfront_fee if eligible_cap>0 else 0.
    eligible_cap_affordable=max_payment is None or eligible_amort*payment_factor<=max_payment+1e-9
    if eligible_cap_affordable and eligible_cap-integer_cap>1e-9 and not np.any(candidates==eligible_cap):
        candidates=np.concatenate((candidates,[eligible_cap]))
    debt,_=loan_schedule(1.,apr,years,horizon)
    nominal=(1+s['real_discount_rate'])*(1+s['inflation'])-1
    discount=(1+nominal)**np.arange(horizon+1)
    discounted_base=base/discount[None,:]
    best_score=None;best_amount=None
    # Keep each temporary candidate × weather × year block bounded.
    block=512;weather=base.shape[0]
    for start in range(0,len(candidates),block):
        amounts=candidates[start:start+block]
        p_amort=np.where(amounts>0,amounts+upfront_fee,0.)
        candidate_flows=np.column_stack((amounts,-p_amort[:,None]*debt[None,:]))
        discounted_candidate_flows=candidate_flows/discount[None,:]
        cumulative=np.cumsum(discounted_base[None,:,:]+discounted_candidate_flows[:,None,:],axis=2)
        suffix=np.minimum.accumulate(cumulative[:,:,::-1],axis=2)[:,:,::-1]
        eligible=(suffix[:,:,1:]>=-1e-9)&(cumulative[:,:,1:]>=0)
        has=eligible.any(axis=2);cross=eligible.argmax(axis=2)+1
        values=np.full(has.shape,np.inf)
        rows,cols=np.nonzero(has)
        if len(rows):
            idx=cross[rows,cols]
            prev=cumulative[rows,cols,idx-1];cur=cumulative[rows,cols,idx]
            values[rows,cols]=idx-1+np.divide(-prev,cur-prev,out=np.zeros_like(prev),where=np.abs(cur-prev)>1e-15)
        already=(cumulative[:,:,0]>=0)&(suffix[:,:,0]>=-1e-9)
        values[already]=0.
        medians=np.median(values,axis=1)
        finite=np.isfinite(medians)
        if finite.any():
            local=float(np.min(medians[finite]));positions=np.flatnonzero(medians==local)
            if best_score is None or local<best_score-1e-12:
                best_score=local;best_amount=float(amounts[int(positions[0])])
    initial_cash=None;year20=None
    if best_amount is not None:
        best_p_amort=best_amount+upfront_fee if best_amount>0 else 0.
        best_flow=np.concatenate(([best_amount],-best_p_amort*debt))
        best_cumulative=np.cumsum(discounted_base+(best_flow/discount)[None,:],axis=1)
        initial_cash=max(0.,-float(np.median(best_cumulative[:,0])))
        if horizon>=20:year20=float(np.median(best_cumulative[:,20]))
    best_p_amort=best_amount+upfront_fee if (best_amount is not None and best_amount>0) else 0.
    optimal_payment=None if best_amount is None else float(best_p_amort*payment_factor)
    return dict(status='OPTIMAL' if best_amount is not None else 'NO_RECOVERY',optimal_amount_eur=best_amount,optimal_monthly_payment_eur=optimal_payment,max_monthly_payment_eur=max_payment,median_discounted_recovery_years=best_score,median_initial_cash_eur=initial_cash,median_year20_discounted_impact_eur=year20,eligible_cap_eur=eligible_cap,affordable_search_cap_eur=search_cap,loan_apr=apr,loan_years=years,horizon_years=horizon,weather_count=weather,resolution_eur=1,search_candidates=len(candidates),objective='Minimum median persistent discounted recovery across all historical weather paths; unrecovered paths are censored above the simulation horizon.',definition='Scans every whole-euro principal through the affordable search cap, plus exact affordable and eligible-cap boundaries when fractional. Ties select the lower principal.')

def compact(result):
    keys=['settings','status','layout','metrics','energy','enduses']
    if 'heat_pump' in result:
        keys.append('heat_pump')
    return {k:result[k] for k in keys if k in result}

def sensitivity(settings=None):
    """One-at-a-time plausible ranges; not a claim to enumerate every combination."""
    s=validate(settings);rows=[]
    hp_on=bool(s.get('planning_heat_pump',False))
    hp_inactive_legacy={
        'scop',
        'dhw_heating_type','dhw_scop','dhw_midday_fraction','dhw_resistance_efficiency',
        'heating_schedule_mode','ac_heating_windows','heating_on_hour','heating_off_hour',
        'heating_evening_on_hour','heating_evening_off_hour','heating_balance_temp',
        'electrification_capex','electrification_capex_include_baseline',
        'gas_disconnect_cost',
        'ac_heating_additional_demand_pct',
    }
    for record in records():
        name=record['name'];span=record.get('plausible_range')
        if name not in s:continue
        value=s[name]
        if name in ['quote_total','installation_year','principal_residence_confirmed','cer_enabled','ac_heating_windows'] or name.endswith('_panels'):continue
        if not hp_on:
            if name.startswith('hp_') or name=='planning_heat_pump':continue
        else:
            if name=='planning_heat_pump' or name.startswith('hp_') or name in hp_inactive_legacy:continue
        choices=record.get('choices')
        if choices:
            values=[v for v in choices if v is not None and (not isinstance(v,(int,float)) or math.isfinite(v))]
        elif isinstance(value,bool):
            values=[False,True]
        elif span and len(span)==2:
            if any(v is None or (isinstance(v,(int,float)) and not math.isfinite(v)) for v in span):
                if name=='ac_heating_additional_demand_pct':
                    values=[0, 25, 50]
                else:
                    continue
            else:
                values=span
        elif name=='ac_heating_additional_demand_pct':
            values=[0, 25, 50]
        else:
            continue
        for v in values:
            if v==value:continue
            try:
                r=evaluate({**s,name:v});rows.append(dict(parameter=name,value=v,metrics=r['metrics'],capex_eur=r['finance']['capex_eur']))
            except ValueError as ex:rows.append(dict(parameter=name,value=v,error=str(ex)))

    # When HP is active, evaluate clearly labelled small provisional comparisons for key HP parameters:
    if hp_on:
        hp_cases=[]
        nom=float(s.get('hp_nominal_kw',9.0))
        for v in [round(nom*0.8,1),round(nom*1.2,1)]:
            if v!=nom:hp_cases.append(('hp_nominal_kw',v))
        flow_cold=float(s.get('hp_curve_flow_cold',55.0))
        for v in [flow_cold-5.0,flow_cold+5.0]:
            if v!=flow_cold:hp_cases.append(('hp_curve_flow_cold',v))
        capex=float(s.get('hp_capex',10000.0))
        for v in [round(capex*0.8,2),round(capex*1.2,2)]:
            if v!=capex:hp_cases.append(('hp_capex',v))

        for param_name,v in hp_cases:
            try:
                r=evaluate({**s,param_name:v})
                rows.append(dict(parameter=param_name,value=v,metrics=r['metrics'],capex_eur=r['finance']['capex_eur']))
            except ValueError as ex:
                rows.append(dict(parameter=param_name,value=v,error=str(ex)))

    # Explicit loan, export-price interpretation and installer-premium cases.
    for name,changes in [('loan_5pct',{'loan_fraction':1,'loan_apr':.05}),('loan_8pct',{'loan_fraction':1,'loan_apr':.08}),('supplied_capture_direct',{'export_market_price':.093,'export_capture_factor':1}),('original_cost_mapping',{'pv_fixed_cost':1500,'pv_cost_kwp':1350})]:
        r=evaluate({**s,**changes});rows.append(dict(parameter=name,value=changes,metrics=r['metrics'],capex_eur=r['finance']['capex_eur']))
    return dict(status='SCENARIO_PENDING',method=f'One parameter at a time across recorded plausible range; includes all {len(data()["years"])} weather years. Interactions additionally tested in joint stress cases. Not a probability distribution.',cases=rows)

def study(settings=None,progress=None):
    """Single-process deterministic candidate and financial-scenario computation."""
    s=validate(settings);base=sweep(s);allrows=[];rankings=[]
    for path,rate in [('lower',-.02),('flat',0),('higher',.03)]:
        for deduction in current_policy().deduction_rates():
            scenario=f'{path}_deduction_{deduction}';rows=[]
            for c in base['candidates']:
                r=evaluate({**c['settings'],'energy_real_escalation':rate,'gas_real_escalation':rate,'deduction_rate':deduction})
                rows.append(dict(id=c['id'],scenario=scenario,label=c['label'],load_scenario=c['load_scenario'],theoretical=c['theoretical'],battery_kwh=c['battery_kwh'],layout=r['layout'],metrics=r['metrics'],settings=r['settings'],capex_eur=r['finance']['capex_eur']))
            _pareto(rows);allrows.extend(rows)
            for load in ['baseline','electrified','ev']:
                eligible=[c for c in rows if c['load_scenario']==load and not c['theoretical']]
                for objective in ['npv_eur','payback_years','discounted_payback_years','autarky','self_consumption']:
                    reverse=objective not in ['payback_years','discounted_payback_years']
                    ordered=sorted(eligible,key=lambda c:(c['metrics'][objective]['median'] if c['metrics'][objective]['median'] is not None else (-math.inf if reverse else math.inf)),reverse=reverse)
                    rankings.append(dict(scenario=scenario,load_scenario=load,objective=objective,statistic='weather_median',ordered_ids=[c['id'] for c in ordered],pareto_ids=[c['id'] for c in eligible if c['pareto']]))
            if progress:progress(scenario)
    stress=[]
    for label,changes in [('lower_value',dict(real_discount_rate=.05,energy_real_escalation=-.02,gas_real_escalation=-.02,deduction_tax_capacity=0,pv_cost_kwp=1700,battery_cost_kwh=800,roundtrip_efficiency=.85,pv_annual_fade=.007,battery_calendar_fade=.03,om_annual=150,insurance_annual=80,cleaning_annual=100,diffuse_suppression=1)),('higher_value',dict(real_discount_rate=.01,energy_real_escalation=.03,gas_real_escalation=.03,deduction_tax_capacity=5000,pv_cost_kwp=1000,battery_cost_kwh=520,roundtrip_efficiency=.95,pv_annual_fade=.002,battery_calendar_fade=.005,om_annual=60,insurance_annual=0,cleaning_annual=0,diffuse_suppression=0))]:
        for c in base['candidates']:
            r=evaluate({**c['settings'],**changes});stress.append(dict(id=c['id'],stress=label,metrics=r['metrics'],capex_eur=r['finance']['capex_eur']))
        if progress:progress(label)
    joint=[]
    for c in base['candidates']:
        cases=[x for x in allrows+stress if x['id']==c['id']]
        bounds={}
        for key in c['metrics']:
            bands=[x['metrics'][key] for x in cases]
            pairs=[(b['min'],b['max'],b.get('nonrecovery_present',False) or b.get('unrecovered_weather_years',0)>0 or b['max'] is None) if isinstance(b,dict) else (b,b,b is None) for b in bands]
            lows=[lo for lo,_,_ in pairs if lo is not None];highs=[hi for _,hi,_ in pairs if hi is not None]
            bounds[key]=dict(min=min(lows) if lows else None,max=None if any(hi is None for _,hi,_ in pairs) else max(highs) if highs else None,nonrecovery_present=any(nr for _,_,nr in pairs))
        joint.append(dict(id=c['id'],label=c['label'],load_scenario=c['load_scenario'],battery_kwh=c['battery_kwh'],theoretical=c['theoretical'],metrics=bounds,cases=len(cases)))
    counterfactuals=[]
    for c in base['candidates']:
        if c['battery_kwh']==0:continue
        without=next((x for x in base['candidates'] if x['label']==c['label'] and x['load_scenario']==c['load_scenario'] and x['battery_kwh']==0), None)
        if without is None:continue
        flows=np.array(c['finance']['cashflows_by_weather']['pv_incremental'])-np.array(without['finance']['cashflows_by_weather']['pv_incremental'])
        counterfactuals.append(dict(id=c['id'],without_battery_id=without['id'],incremental_capex_eur=c['finance']['capex_eur']-without['finance']['capex_eur'],battery_incremental_npv_eur=envelope([npv(f,c['finance']['nominal_discount_rate']) for f in flows]),battery_incremental_payback_years=payback_envelope([persistent_payback(f) for f in flows])))
    hp_on=bool(s.get('planning_heat_pump',False))
    # Show both rated climates explicitly even when the selected load is baseline,
    # where changing SCOP alone cannot affect electricity consumption.
    # When heat pump planning is active, legacy air-to-air AC SCOP scenarios are inactive.
    scop_scenarios=[]
    if not hp_on:
        for rating in next(r for r in records() if r['name']=='scop').get('provided_ratings', ()):
            for load in ['electrified','ev']:
                result=evaluate({**s,'load_scenario':load,'scop':rating['value']})
                scop_scenarios.append(dict(**rating,load_scenario=load,result=compact(result)))
    notes=[
        'All numbers computed by feasibility/engine.py.',
        'Weather scenarios repeat each historic year over life; ranges are not probabilities.',
        'No financial assumption has been confirmed by selecting a default.',
        f"{legacy_text('study_cer','CER requires explicit profile assumptions and matching evidence.')}",
        f"{legacy_text('study_research','No live research is implied by model execution.')}"
    ]
    if hp_on:
        notes.append(legacy_text('hp_active_note','Heat pump planning active: separate AC SCOP scenarios are inactive and omitted.'))
    return dict(status='SCENARIO_PENDING',scop_scenarios=scop_scenarios,battery_counterfactuals=counterfactuals,candidate_joint_envelopes=joint,counts=dict(candidates=len(base['candidates']),financial_candidate_cases=len(allrows),joint_stress_cases=len(stress),rankings=len(rankings)),baseline_settings=s,calibration=calibration(s),base=base,scenario_candidates=allrows,rankings=rankings,joint_stress=stress,sensitivity=sensitivity(s),notes=notes)

def _candidate_battery_settings(s, cap_kwh, rep_comp=None):
    """Return a settings dict with coherent battery module composition for hypothetical capacity cap_kwh.
    Does not reuse current installed module counts from s.
    """
    is_mixed = bool(s.get('battery_mixed_sizes', False))
    mod_kwh = float(s.get('battery_module_kwh', 0.0) or 0.0)
    sec_mod_kwh = float(s.get('battery_secondary_module_kwh', 0.0) or 0.0)
    
    if cap_kwh <= 0.0:
        return {
            **s,
            'battery_mixed_sizes': is_mixed,
            'battery_modules': 0,
            'battery_secondary_modules': 0,
            'battery_module_kwh': mod_kwh,
            'battery_secondary_module_kwh': sec_mod_kwh,
            'battery_kwh': 0.0,
            'quote_total': None,
        }
    
    if is_mixed:
        if rep_comp is not None:
            c = rep_comp
        else:
            comps = enumerate_battery_compositions_by_capacity(0.0, float(cap_kwh), settings=s)
            matching = [comp for comp in comps if math.isclose(comp['battery_kwh'], cap_kwh, abs_tol=1e-6)]
            if matching:
                sorted_matching = sorted(matching, key=lambda x: (x['battery_modules'], x['battery_secondary_modules']))
                c = sorted_matching[0]
            else:
                c = calculate_battery_composition(
                    total_modules=int(round(cap_kwh / mod_kwh)) if mod_kwh > 0 else 1,
                    secondary_modules=0,
                    module_kwh=mod_kwh,
                    secondary_module_kwh=sec_mod_kwh,
                    mixed_sizes=True
                )
        return {
            **s,
            'battery_mixed_sizes': True,
            'battery_modules': c['battery_modules'],
            'battery_secondary_modules': c['battery_secondary_modules'],
            'battery_module_kwh': c['battery_module_kwh'],
            'battery_secondary_module_kwh': c['battery_secondary_module_kwh'],
            'battery_kwh': float(c['battery_kwh']),
            'quote_total': None,
        }
    
    # Single size mode
    if mod_kwh > 0 and math.isclose(cap_kwh % mod_kwh, 0.0, abs_tol=1e-6):
        m = int(round(cap_kwh / mod_kwh))
        return {
            **s,
            'battery_mixed_sizes': False,
            'battery_modules': m,
            'battery_secondary_modules': 0,
            'battery_module_kwh': mod_kwh,
            'battery_kwh': float(cap_kwh),
            'quote_total': None,
        }
    
    # Analytical capacity sampling (not an exact multiple of mod_kwh)
    bat_cost_kwh = float(s.get('battery_cost_kwh', 0.0) or 0.0)
    if s.get('storage_cost_mode') == 'existing':
        effective_unit_kwh = 0.0
    else:
        mod_override = s.get('battery_module_cost_override')
        if mod_override is not None:
            primary_unit = float(mod_override)
        else:
            primary_unit = mod_kwh * bat_cost_kwh if mod_kwh > 0 else bat_cost_kwh
        primary_unit += float(s.get('battery_module_extra_cost', 0.0) or 0.0)
        effective_unit_kwh = (primary_unit / mod_kwh) if mod_kwh > 0 else bat_cost_kwh
    
    return {
        **s,
        'battery_mixed_sizes': False,
        'battery_modules': 1,
        'battery_secondary_modules': 0,
        'battery_module_cost_override': None,
        'battery_secondary_module_cost_override': None,
        'battery_module_extra_cost': 0.0,
        'battery_secondary_module_extra_cost': 0.0,
        'battery_cost_kwh': effective_unit_kwh,
        'battery_kwh': float(cap_kwh),
        'battery_module_kwh': float(cap_kwh),
        'quote_total': None,
    }

@lru_cache(maxsize=16)
def _battery_sizing_cached(runtime_key, key_settings, key_options, quote_fingerprint, price_version):
    """Heavy battery sizing simulation cached on hashable settings, options and price version."""
    assert_runtime_key(runtime_key)
    s=thaw_settings(dict(key_settings));opt=thaw_settings(dict(key_options))
    counts=_panel_total(s)
    kwp=counts*s['panel_watts']/1000.0
    
    cost_delta_pct=float(opt.get('sensitivity_cost_delta_pct',0.0))
    cost_multiplier=1.0+cost_delta_pct

    user_step=int(opt.get('step',1))
    max_cap=int(opt.get('max_capacity',25))
    is_mixed=bool(s.get('battery_mixed_sizes',False))
    selected_cap=float(s['battery_kwh'])

    if not is_mixed:
        full_capacities=list(range(0,max_cap+1,1))
        rep_for_cap={}
        all_comps_by_cap={}
        selected_comp=None
    else:
        attainable_comps=enumerate_battery_compositions_by_capacity(0.0,float(max_cap),settings=s)
        selected_comp=calculate_battery_composition(
            total_modules=s['battery_modules'],
            secondary_modules=s['battery_secondary_modules'],
            module_kwh=s['battery_module_kwh'],
            secondary_module_kwh=s['battery_secondary_module_kwh'],
            mixed_sizes=True
        )
        all_mixed_comps=list(attainable_comps)
        if not any(c['id']==selected_comp['id'] for c in all_mixed_comps):
            all_mixed_comps.append(selected_comp)

        comps_by_cap={}
        for c in all_mixed_comps:
            c_kwh=float(c['battery_kwh'])
            comps_by_cap.setdefault(c_kwh,[]).append(c)

        # Deterministic documented representative for equal capacity:
        # minimum total module count, then minimum secondary module count.
        rep_for_cap={}
        all_comps_by_cap={}
        for c_kwh,clist in comps_by_cap.items():
            sorted_clist=sorted(clist,key=lambda c:(c['battery_modules'],c['battery_secondary_modules']))
            rep_for_cap[c_kwh]=sorted_clist[0]
            all_comps_by_cap[c_kwh]=sorted_clist

        full_capacities=sorted(comps_by_cap.keys())

    deduction_rate=float(opt.get('deduction_rate',s['deduction_rate']))
    s['deduction_rate']=deduction_rate
    base_capex=installed_capex(_candidate_battery_settings(s,0.0),kwp)
    lifetime=int(opt.get('battery_lifetime_years',s['battery_replace_year'] if s['battery_replace_year']>0 else 12))
    deg_annual=float(opt.get('battery_degradation_annual',s['battery_calendar_fade']))
    rnom=(1.0+s['real_discount_rate'])*(1.0+s['inflation'])-1.0
    h_years=s['horizon_years']
    vat_multiplier=1.0+(s['installation_vat'] if not s['quote_vat_included'] else 0.0)
    premium_multiplier=1.0+(s['deduction_price_premium'] if deduction_rate>0 else 0.0)

    # 1. Quote variants and marginal cost (applying cost_multiplier to all variants)
    q10_raw=opt.get('quote_10kwh_eur');q15_raw=opt.get('quote_15kwh_eur')
    if q10_raw is not None and q15_raw is not None:
        marginal_source='quote_variants'
        quote_variants_status='provided'
        missing_fields=[]
        q10=float(q10_raw)
        q15=float(q15_raw)
        ref_cand_desc='Quote variants (10 → 15 kWh increment)'
        c_ref_0=10.0
        c_ref_1=15.0
    else:
        marginal_source='benchmark'
        quote_variants_status='missing'
        missing_fields=[k for k,v in [('quote_10kwh_eur',q10_raw),('quote_15kwh_eur',q15_raw)] if v is None]
        q10=None;q15=None
        c_ref_0=0.0
        if is_mixed:
            first_cap=full_capacities[1] if len(full_capacities)>1 else (float(s.get('battery_module_kwh',5.0)) or 5.0)
            c_ref_1=first_cap
            ref_cand_desc=f'Attainable hardware increment (0 → {first_cap:.1f} kWh)'
        else:
            mod_kwh=float(s.get('battery_module_kwh',0.0) or 0.0)
            step_kwh=mod_kwh if mod_kwh>0 else 1.0
            c_ref_1=step_kwh
            ref_cand_desc=f'Benchmark single module ({step_kwh:.1f} kWh)' if mod_kwh>0 else 'Benchmark marginal storage unit cost'

    # Battery capex curve function B(C) preserving quote variants intercepts and benchmark economics
    def battery_capex_amount(cap_kwh, comp=None):
        if cap_kwh<=0:return 0.0
        if marginal_source=='quote_variants' and q10 is not None and q15 is not None:
            eff_q10=float(q10*cost_multiplier)
            eff_q15=float(q15*cost_multiplier)
            if cap_kwh==10.0:return eff_q10
            if cap_kwh==15.0:return eff_q15
            slope=(eff_q15-eff_q10)/5.0
            if cap_kwh>15.0:return float(eff_q15+(cap_kwh-15.0)*slope)
            if 10.0<cap_kwh<15.0:return float(eff_q10+(cap_kwh-10.0)*slope)
            return float(cap_kwh*(eff_q10/10.0))
        # Benchmark
        c=comp or (rep_for_cap.get(cap_kwh) if is_mixed else None)
        s_cand=_candidate_battery_settings(s,cap_kwh,c)
        raw_bat_capex=battery_installed_capex(s_cand)
        return float(raw_bat_capex*cost_multiplier)

    # Calculate scalar gross and net reference using actual reference endpoints
    delta_ref_c=float(c_ref_1-c_ref_0)
    b_ref_0=battery_capex_amount(c_ref_0,rep_for_cap.get(c_ref_0) if is_mixed else None)
    b_ref_1=battery_capex_amount(c_ref_1,rep_for_cap.get(c_ref_1) if is_mixed else None)
    delta_ref_capex=b_ref_1-b_ref_0
    capex_ref_0=base_capex+b_ref_0
    capex_ref_1=base_capex+b_ref_1

    effective_marginal_cost=float(delta_ref_capex/delta_ref_c) if delta_ref_c>0 else 0.0
    marginal_cost=float(effective_marginal_cost/cost_multiplier) if cost_multiplier>0 else effective_marginal_cost
    annualized_cost_gross=float(effective_marginal_cost/lifetime)

    # Net annualized cost for scalar summary incorporating endpoint capex and tax capacity nonlinearity
    fiscal_policy=current_policy()
    e0_ref,d0_ref=fiscal_policy.investment_terms(capex_ref_0,s)
    e1_ref,d1_ref=fiscal_policy.investment_terms(capex_ref_1,s)
    delta_deduction_ref=max(0.0,d1_ref-d0_ref)
    pv_deductions_ref=sum(
        delta_deduction_ref/((1.0+rnom)**(yy+1))
        for yy in range(h_years)
        if s['deduction_start_year']<=yy+1<s['deduction_start_year']+10
    )
    pv_ded_per_kwh=float(pv_deductions_ref/delta_ref_c) if delta_ref_c>0 else 0.0
    effective_marginal_net=max(0.0,effective_marginal_cost-pv_ded_per_kwh)
    annualized_cost_net=float(effective_marginal_net/lifetime)

    # Pre-calculate per-capacity actual incremental net deduction cost
    e0_base,d0_base=fiscal_policy.investment_terms(base_capex,s)

    annual_net_costs=[]
    annual_gross_costs=[]
    for cap in full_capacities:
        c_comp=rep_for_cap.get(cap) if is_mixed else None
        b_capex=battery_capex_amount(cap,c_comp)
        k_c=base_capex+b_capex
        e_c,d_c=fiscal_policy.investment_terms(k_c,s)
        pv_ded_c=sum(
            max(0.0,d_c-d0_base)/((1.0+rnom)**(yy+1))
            for yy in range(h_years)
            if s['deduction_start_year']<=yy+1<s['deduction_start_year']+10
        )
        annual_net_costs.append(max(0.0,b_capex-pv_ded_c)/lifetime)
        annual_gross_costs.append(b_capex/lifetime)

    # 2. Electricity prices, capture and margins (including sensitivity_spread_delta)
    p_import=float((s['retail_commodity']+s['retail_regulated']+s['electric_excise'])*(1.0+s['electric_vat']))
    prof_source=price_profile(s)[1]
    capture=s['export_capture_factor'] if prof_source=='flat_solar_capture_benchmark' else 1.0
    tax_rate=s['export_irpef']+s['regional_additional_tax']+s['municipal_additional_tax']
    spread_delta=float(opt.get('sensitivity_spread_delta',0.0))
    
    base_export_price=float(s['export_market_price']*capture)
    export_gross=float(base_export_price+spread_delta)
    p_export=float(export_gross*(1.0-tax_rate))
    rte=float(s['roundtrip_efficiency']);sqrt_rte=float(math.sqrt(rte))
    
    margin_charge=float(p_import*rte-p_export)
    margin_deliv=float(p_import-p_export/rte)
    margin_physical=float(p_import*sqrt_rte-p_export/sqrt_rte)
    breakeven_cycles_gross=float(annualized_cost_gross/margin_charge) if margin_charge>0 else None
    breakeven_cycles_net=float(annualized_cost_net/margin_charge) if margin_charge>0 else None
    breakeven_physical_gross=float(breakeven_cycles_gross*sqrt_rte) if breakeven_cycles_gross is not None else None
    breakeven_physical_net=float(breakeven_cycles_net*sqrt_rte) if breakeven_cycles_net is not None else None
    breakeven_deliv_gross=float(breakeven_cycles_gross*rte) if breakeven_cycles_gross is not None else None
    breakeven_deliv_net=float(breakeven_cycles_net*rte) if breakeven_cycles_net is not None else None

    # 3. Calendar mapped series and hourly prices for all historical weather years
    d=data();years=list(map(int,d['years']))
    lifetime_inputs=_lifetime_mapped_inputs(s)
    mappings=lifetime_inputs['mappings']
    load_mapped=lifetime_inputs['load']
    ev_mapped=lifetime_inputs['ev']
    valid_mapped=lifetime_inputs['valid']
    pzo_mapped=lifetime_inputs['pzo']
    month_mapped=lifetime_inputs['month']
    hour_mapped=lifetime_inputs['hour']
    mapped_data=_forecast_mapped_series(s)
    mapped_pv=mapped_data['mapped_pv']
    mapped_load=mapped_data['mapped_load']
    mapped_pzo=mapped_data['mapped_pzo']
    target_len=mapped_data['target_len']
    is_leap=mapped_data['is_leap']
    days_in_year=366 if is_leap else 365

    # 4. Dispatch over full capacity sweep
    valid=np.ones(target_len,dtype=bool)
    power=s['baseline_power_kw'] if s['load_scenario']=='baseline' else s['electrified_power_kw']
    
    full_cap_dispatch=[]
    baseline_net_bills=np.zeros(len(years))
    
    # Pre-allocate containers for sensitivity recalculation without re-running dispatch
    raw_imports=np.zeros((len(full_capacities),len(years)))
    raw_exports=np.zeros((len(full_capacities),len(years)))
    raw_pzo_sums=np.zeros((len(full_capacities),len(years)))
    cached_soc_traces={}
    
    for ci,cap in enumerate(full_capacities):
        cap_f=float(cap)
        s_route=_candidate_battery_settings(s,cap_f,rep_for_cap.get(cap_f) if is_mixed else None)
        s_route=_routing_settings(s_route,enforce_clipping=True)
        params_route=physical_parameters(s_route)
        _t_months=mapped_data['target_months'];_t_hours=mapped_data['target_std_hours']
        rows_import=np.zeros(len(years));rows_export=np.zeros(len(years))
        rows_pv=np.zeros(len(years));rows_self_cons=np.zeros(len(years))
        rows_autarky=np.zeros(len(years));rows_useful_sc=np.zeros(len(years))
        rows_discharge=np.zeros(len(years));rows_net_bill=np.zeros(len(years))
        rows_efc_phys=np.zeros(len(years));rows_efc_deliv=np.zeros(len(years))
        rows_efc_charge=np.zeros(len(years))
        
        for wi in range(len(years)):
            total,trace=dispatch_year(mapped_pv[wi],mapped_load[wi],cap_f,s['charge_kw'],s['discharge_kw'],sqrt_rte,s['inverter_kw'],0.,mapped_pzo[wi],valid,s['pv_battery_coupling']=='dc_coupled',params_route,_t_months,_t_hours)
            if selected_cap>0 and math.isclose(cap_f,selected_cap,abs_tol=1e-6):
                cached_soc_traces[wi]=trace[:,4].copy()
            rows_import[wi]=total[5];rows_export[wi]=total[6];rows_pv[wi]=total[1]
            rows_self_cons[wi]=(total[2]+total[3])/total[1] if total[1]>0 else 0.
            rows_autarky[wi]=1.0-total[5]/total[0] if total[0]>0 else 0.
            rows_useful_sc[wi]=(total[2]+total[4])/total[1] if total[1]>0 else 0.
            rows_discharge[wi]=total[4]
            rows_efc_phys[wi]=total[10]
            rows_efc_deliv[wi]=total[4]/cap_f if cap_f>0 else 0.
            rows_efc_charge[wi]=total[3]/cap_f if cap_f>0 else 0.
            
            raw_imports[ci,wi]=total[5]
            raw_exports[ci,wi]=total[6]
            raw_pzo_sums[ci,wi]=total[11]
            
            bill=electric_bill(total[5],s,year=1,power=power)
            pzo=total[11]*export_gross
            net_export=current_policy().export_settlement(total[6],pzo,s,1,kwp)['net']
            rows_net_bill[wi]=bill-net_export
            
        if math.isclose(cap_f,0.0,abs_tol=1e-9):
            baseline_net_bills=rows_net_bill.copy()
            
        savings=baseline_net_bills-rows_net_bill
        avg_annual_net = float(annual_net_costs[ci] / cap_f) if cap_f > 0 else 0.0
        avg_annual_gross = float(annual_gross_costs[ci] / cap_f) if cap_f > 0 else 0.0
        be_net = float(avg_annual_net / margin_charge) if (margin_charge > 0 and cap_f > 0) else None
        be_gross = float(avg_annual_gross / margin_charge) if (margin_charge > 0 and cap_f > 0) else None
        
        row_dict=dict(
            capacity_kwh=cap,
            routing_priority=s_route['routing_priority'],
            resolved_routing_priority=resolve_routing_priority(s_route),
            grid_import_kwh=envelope(rows_import),
            grid_export_kwh=envelope(rows_export),
            pv_kwh=envelope(rows_pv),
            self_consumption=envelope(rows_self_cons),
            autarky=envelope(rows_autarky),
            useful_self_consumption=envelope(rows_useful_sc),
            battery_discharge_kwh=envelope(rows_discharge),
            discharge_kwh=envelope(rows_discharge),
            annual_savings_eur=envelope(savings),
            annual_savings_by_weather=[float(v) for v in savings],
            efc_physical=envelope(rows_efc_phys),
            efc_delivered=envelope(rows_efc_deliv),
            efc_charge=envelope(rows_efc_charge),
            efc_physical_by_weather=[float(v) for v in rows_efc_phys],
            efc_delivered_by_weather=[float(v) for v in rows_efc_deliv],
            efc_charge_by_weather=[float(v) for v in rows_efc_charge],
            breakeven_charge_cycles_gross=be_gross,
            breakeven_charge_cycles_net=be_net,
            breakeven_physical_cycles_gross=float(be_gross * sqrt_rte) if be_gross is not None else None,
            breakeven_physical_cycles_net=float(be_net * sqrt_rte) if be_net is not None else None,
            breakeven_delivered_cycles_gross=float(be_gross * rte) if be_gross is not None else None,
            breakeven_delivered_cycles_net=float(be_net * rte) if be_net is not None else None,
        )
        if is_mixed:
            rep_comp=rep_for_cap[cap]
            avail_comps=all_comps_by_cap[cap]
            s_row={
                **s,
                'battery_mixed_sizes':True,
                'battery_modules':rep_comp['battery_modules'],
                'battery_secondary_modules':rep_comp['battery_secondary_modules'],
                'battery_module_kwh':rep_comp['battery_module_kwh'],
                'battery_secondary_module_kwh':rep_comp['battery_secondary_module_kwh'],
                'battery_kwh':rep_comp['battery_kwh'],
                'quote_total':None,
                'deduction_rate':deduction_rate,
                'battery_calendar_fade':deg_annual,
                'battery_replace_year':lifetime if s['battery_replace_year']>0 or opt.get('battery_lifetime_years') is not None else 0
            }
            if marginal_source=='quote_variants' and cap_f>0:
                b_capex_target=battery_capex_amount(cap_f)
                s_row['battery_module_cost_override']=None
                s_row['battery_secondary_module_cost_override']=None
                s_row['battery_module_extra_cost']=0.0
                s_row['battery_secondary_module_extra_cost']=0.0
                s_row['battery_cost_kwh']=float(b_capex_target/(cap_f*vat_multiplier*premium_multiplier))
            elif cost_multiplier!=1.0 and cap_f>0:
                if s_row.get('battery_module_cost_override') is not None:
                    s_row['battery_module_cost_override']=float(s_row['battery_module_cost_override'])*cost_multiplier
                if s_row.get('battery_secondary_module_cost_override') is not None:
                    s_row['battery_secondary_module_cost_override']=float(s_row['battery_secondary_module_cost_override'])*cost_multiplier
                s_row['battery_cost_kwh']=float(s_row.get('battery_cost_kwh',0.0))*cost_multiplier
                s_row['battery_module_extra_cost']=float(s_row.get('battery_module_extra_cost',0.0))*cost_multiplier
                s_row['battery_secondary_module_extra_cost']=float(s_row.get('battery_secondary_module_extra_cost',0.0))*cost_multiplier
            row_dict.update(
                composition=rep_comp,
                available_compositions=avail_comps,
                settings=s_row,
                outside_analysis_search_range=bool(cap>max_cap),
                search_range_status='outside_search_range' if cap>max_cap else 'within_search_range'
            )
        else:
            s_row=_candidate_battery_settings(s,cap_f)
            s_row.update({
                'quote_total':None,
                'deduction_rate':deduction_rate,
                'battery_calendar_fade':deg_annual,
                'battery_replace_year':lifetime if s['battery_replace_year']>0 or opt.get('battery_lifetime_years') is not None else 0
            })
            if marginal_source=='quote_variants' and cap_f>0:
                b_capex_target=battery_capex_amount(cap_f)
                s_row['battery_module_cost_override']=None
                s_row['battery_secondary_module_cost_override']=None
                s_row['battery_module_extra_cost']=0.0
                s_row['battery_secondary_module_extra_cost']=0.0
                s_row['battery_cost_kwh']=float(b_capex_target/(cap_f*vat_multiplier*premium_multiplier))
            elif cost_multiplier!=1.0 and cap_f>0:
                if s_row.get('battery_module_cost_override') is not None:
                    s_row['battery_module_cost_override']=float(s_row['battery_module_cost_override'])*cost_multiplier
                s_row['battery_cost_kwh']=float(s_row.get('battery_cost_kwh',0.0))*cost_multiplier
                s_row['battery_module_extra_cost']=float(s_row.get('battery_module_extra_cost',0.0))*cost_multiplier
            row_dict.update(settings=s_row)
        full_cap_dispatch.append(row_dict)

    # Subsample Chart 1 capacities if requested by user_step in single mode; preserve all attainable sizes in mixed mode
    if not is_mixed:
        chart1_capacities=[row for row in full_cap_dispatch if int(row['capacity_kwh'])%user_step==0 or int(row['capacity_kwh'])==max_cap]
    else:
        chart1_capacities=full_cap_dispatch

    # 5. Chart 2: True C to C+1 annual euro savings (1-kWh discrete steps)
    inc_steps=[]
    for i in range(len(full_capacities)-1):
        c0=full_capacities[i];c1=full_capacities[i+1]
        delta_c=float(c1-c0)
        s0=np.array(full_cap_dispatch[i]['annual_savings_by_weather'])
        s1=np.array(full_cap_dispatch[i+1]['annual_savings_by_weather'])
        diff=(s1-s0)/delta_c # EUR / kWh / year
        step_capex_0=battery_capex_amount(c0,rep_for_cap.get(c0) if is_mixed else None)
        step_capex_1=battery_capex_amount(c1,rep_for_cap.get(c1) if is_mixed else None)
        step_delta_capex=float(step_capex_1-step_capex_0)
        step_marginal_cost=float(step_delta_capex/delta_c) if delta_c>0 else 0.0
        step_annual_gross=float((annual_gross_costs[i+1]-annual_gross_costs[i])/delta_c) if delta_c>0 else 0.0
        step_annual_net=float((annual_net_costs[i+1]-annual_net_costs[i])/delta_c) if delta_c>0 else 0.0
        step_be_gross=float(step_annual_gross/margin_charge) if (margin_charge>0 and delta_c>0) else None
        step_be_net=float(step_annual_net/margin_charge) if (margin_charge>0 and delta_c>0) else None
        inc_steps.append(dict(
            capacity_from=c0,
            capacity_to=c1,
            delta_capacity_kwh=delta_c,
            incremental_savings_eur_kwh=envelope(diff),
            incremental_savings_by_weather=[float(v) for v in diff],
            marginal_cost_eur_kwh=step_marginal_cost,
            delta_capex_eur=step_delta_capex,
            annualized_cost_gross=step_annual_gross,
            annualized_cost_net=step_annual_net,
            breakeven_charge_cycles_gross=step_be_gross,
            breakeven_charge_cycles_net=step_be_net,
            breakeven_physical_cycles_gross=float(step_be_gross*sqrt_rte) if step_be_gross is not None else None,
            breakeven_physical_cycles_net=float(step_be_net*sqrt_rte) if step_be_net is not None else None,
            breakeven_delivered_cycles_gross=float(step_be_gross*rte) if step_be_gross is not None else None,
            breakeven_delivered_cycles_net=float(step_be_net*rte) if step_be_net is not None else None,
        ))

    marginal_cost_varies = any(
        not math.isclose(st['annualized_cost_net'], inc_steps[0]['annualized_cost_net'], rel_tol=1e-4, abs_tol=1e-4) or
        not math.isclose(st['annualized_cost_gross'], inc_steps[0]['annualized_cost_gross'], rel_tol=1e-4, abs_tol=1e-4)
        for st in inc_steps
    ) if inc_steps else False

    # Economic optimum capacity: argmax of lifetime net benefit across weather analogues
    deg_annual=float(opt.get('battery_degradation_annual',s['battery_calendar_fade']))
    denom_pv=sum(1.0/((1.0+rnom)**t) for t in range(1,int(lifetime)+1))
    deg_pv=sum(((1.0-deg_annual)**(t-1))/((1.0+rnom)**t) for t in range(1,int(lifetime)+1))
    deg_factor=deg_pv/denom_pv if denom_pv>0 else 1.0

    optimal_caps_net=[]
    optimal_caps_gross=[]
    for wi in range(len(years)):
        benefits_net=[
            float(full_cap_dispatch[ci]['annual_savings_by_weather'][wi]*deg_factor - annual_net_costs[ci])
            for ci,cap in enumerate(full_capacities)
        ]
        benefits_gross=[
            float(full_cap_dispatch[ci]['annual_savings_by_weather'][wi]*deg_factor - annual_gross_costs[ci])
            for ci,cap in enumerate(full_capacities)
        ]
        if not is_mixed:
            optimal_caps_net.append(int(full_capacities[int(np.argmax(benefits_net))]))
            optimal_caps_gross.append(int(full_capacities[int(np.argmax(benefits_gross))]))
        else:
            optimal_caps_net.append(float(full_capacities[int(np.argmax(benefits_net))]))
            optimal_caps_gross.append(float(full_capacities[int(np.argmax(benefits_gross))]))

    # Optimum of mean net/gross savings curve across all weather analogues
    mean_benefits_net=[
        float(full_cap_dispatch[ci]['annual_savings_eur']['mean']*deg_factor - annual_net_costs[ci])
        for ci in range(len(full_capacities))
    ]
    mean_benefits_gross=[
        float(full_cap_dispatch[ci]['annual_savings_eur']['mean']*deg_factor - annual_gross_costs[ci])
        for ci in range(len(full_capacities))
    ]
    if not is_mixed:
        ensemble_opt_net=int(full_capacities[int(np.argmax(mean_benefits_net))])
        ensemble_opt_gross=int(full_capacities[int(np.argmax(mean_benefits_gross))])
    else:
        ensemble_opt_net=float(full_capacities[int(np.argmax(mean_benefits_net))])
        ensemble_opt_gross=float(full_capacities[int(np.argmax(mean_benefits_gross))])

    # Sensitivity optimum band evaluating cost, lifetime, degradation and spread variants
    # Recalculates savings directly from stored raw_imports and raw_pzo_sums under spread shifts
    sens_cost_mults=[0.8,1.0,1.2]
    sens_lifetimes=[max(1,lifetime-3),lifetime,lifetime+3]
    sens_degs=[max(0.0,deg_annual-0.005),deg_annual,min(0.08,deg_annual+0.01)]
    sens_spread_deltas=[spread_delta-0.03,spread_delta,spread_delta+0.03]
    sens_optimal_caps=[]
    
    for sm in sens_cost_mults:
        for sl in sens_lifetimes:
            for sd in sens_degs:
                d_denom=sum(1.0/((1.0+rnom)**t) for t in range(1,int(sl)+1))
                d_num=sum(((1.0-sd)**(t-1))/((1.0+rnom)**t) for t in range(1,int(sl)+1))
                s_factor=d_num/d_denom if d_denom>0 else 1.0
                
                # Precompute net capex costs under this cost multiplier and lifetime
                sens_net_costs=[]
                for cap in full_capacities:
                    b_c=battery_capex_amount(cap,rep_for_cap.get(cap) if is_mixed else None)*sm
                    k_c=base_capex+b_c
                    e_c,d_c=fiscal_policy.investment_terms(k_c,s)
                    pv_ded_c=sum(
                        max(0.0,d_c-d0_base)/((1.0+rnom)**(yy+1))
                        for yy in range(h_years)
                        if s['deduction_start_year']<=yy+1<s['deduction_start_year']+10
                    )
                    sens_net_costs.append(max(0.0,b_c-pv_ded_c)/sl)
                    
                for sp_delta in sens_spread_deltas:
                    test_exp_g=float(base_export_price+sp_delta)
                    test_baseline_bills=np.zeros(len(years))
                    test_bills=np.zeros((len(full_capacities),len(years)))
                    for wi in range(len(years)):
                        for ci in range(len(full_capacities)):
                            imp_val=raw_imports[ci,wi]
                            exp_val=raw_exports[ci,wi]
                            pzo_val=raw_pzo_sums[ci,wi]*test_exp_g
                            n_exp=current_policy().export_settlement(exp_val,pzo_val,s,1,kwp)['net']
                            b_val=electric_bill(imp_val,s,year=1,power=power)-n_exp
                            test_bills[ci,wi]=b_val
                            if ci==0:test_baseline_bills[wi]=b_val
                            
                    for wi in range(len(years)):
                        b_net=[
                            float((test_baseline_bills[wi]-test_bills[ci,wi])*s_factor - sens_net_costs[ci])
                            for ci in range(len(full_capacities))
                        ]
                        if not is_mixed:
                            sens_optimal_caps.append(int(full_capacities[int(np.argmax(b_net))]))
                        else:
                            sens_optimal_caps.append(float(full_capacities[int(np.argmax(b_net))]))

    if not is_mixed:
        optimum_band=dict(
            weather_min_kwh=int(min(optimal_caps_net)),
            weather_max_kwh=int(max(optimal_caps_net)),
            weather_median_kwh=int(np.median(optimal_caps_net)),
            weather_mean_kwh=float(round(float(np.mean(optimal_caps_net)),2)),
            ensemble_optimum_kwh=ensemble_opt_net,
            gross_ensemble_optimum_kwh=ensemble_opt_gross,
            weather_basis='ensemble_mean',
            weather_count=len(years),
            gross_optimum_min_kwh=int(min(optimal_caps_gross)),
            gross_optimum_max_kwh=int(max(optimal_caps_gross)),
            gross_optimum_mean_kwh=float(round(float(np.mean(optimal_caps_gross)),2)),
            sensitivity_min_kwh=int(min(sens_optimal_caps)) if sens_optimal_caps else int(min(optimal_caps_net)),
            sensitivity_max_kwh=int(max(sens_optimal_caps)) if sens_optimal_caps else int(max(optimal_caps_net)),
            is_first_year_annualized_heuristic=True,
            future_export_ramp_included=False,
            definition='First-year annualized approximation: economic optimum capacity band based on year-1 dispatch savings levelized with annual degradation minus annualized capital cost. Future export price ramp is omitted in this heuristic; refer to the full discounted lifetime NPV heatmap (Chart 4) for authoritative multi-year sizing incorporating the future export price ramp.'
        )
    else:
        optimum_band=dict(
            weather_min_kwh=float(min(optimal_caps_net)),
            weather_max_kwh=float(max(optimal_caps_net)),
            weather_median_kwh=float(np.median(optimal_caps_net)),
            weather_mean_kwh=float(round(float(np.mean(optimal_caps_net)),2)),
            ensemble_optimum_kwh=ensemble_opt_net,
            gross_ensemble_optimum_kwh=ensemble_opt_gross,
            weather_basis='ensemble_mean',
            weather_count=len(years),
            gross_optimum_min_kwh=float(min(optimal_caps_gross)),
            gross_optimum_max_kwh=float(max(optimal_caps_gross)),
            gross_optimum_mean_kwh=float(round(float(np.mean(optimal_caps_gross)),2)),
            sensitivity_min_kwh=float(min(sens_optimal_caps)) if sens_optimal_caps else float(min(optimal_caps_net)),
            sensitivity_max_kwh=float(max(sens_optimal_caps)) if sens_optimal_caps else float(max(optimal_caps_net)),
            is_first_year_annualized_heuristic=True,
            future_export_ramp_included=False,
            definition='First-year annualized approximation: economic optimum capacity band based on year-1 dispatch savings levelized with annual degradation minus annualized capital cost. Future export price ramp is omitted in this heuristic; refer to the full discounted lifetime NPV heatmap (Chart 4) for authoritative multi-year sizing incorporating the future export price ramp.'
        )

    # 6. Chart 3 EFC & Break-even formula
    chart3_efc=[row for row in full_cap_dispatch if row['capacity_kwh']>0]
    
    # 7. Chart 4 Heatmap PV kWp x Battery kWh genuine discounted NPV
    chart4_npv_heatmap = None
    if _dynamic_roofs() is None:
        roof_names=['south','west','east','dormer','flat','secondary_south']
        base_counts={r:s[f'{r}_panels'] for r in roof_names}
        base_total=sum(base_counts.values())

        target_pv_grid=[3.0,4.8,6.0,7.2,8.4,9.6]
        current_exact_kwp=float(round(kwp,3))

        if not is_mixed:
            bat_grid=[0.0,2.5,5.0,7.5,10.0,12.0,15.0,20.0,25.0]
            if selected_cap not in bat_grid:
                bat_grid.append(selected_cap)
            bat_grid=sorted(set(bat_grid))
        else:
            bat_grid=[float(c) for c in full_capacities]

        # Generate unique layout configs
        unique_layouts={}
        for p_target in target_pv_grid:
            if base_total>0:
                ratio=p_target*1000.0/(base_total*s['panel_watts'])
                scaled_panels={r:int(round(base_counts[r]*ratio)) for r in roof_names}
                if sum(scaled_panels.values())==0 and p_target>0:
                    scaled_panels['south']=max(1,int(round(p_target*1000.0/s['panel_watts'])))
            else:
                scaled_panels={r:0 for r in roof_names}
                scaled_panels['south']=max(1,int(round(p_target*1000.0/s['panel_watts'])))
            exact_p=float(round(sum(scaled_panels.values())*s['panel_watts']/1000.0,3))
            if exact_p not in unique_layouts:
                unique_layouts[exact_p]=scaled_panels

        # Always include the exact current configuration layout
        if current_exact_kwp not in unique_layouts:
            unique_layouts[current_exact_kwp]=base_counts

        sorted_pv_axis=sorted(unique_layouts.keys())
        heatmap_cells=[]

        for exact_kwp in sorted_pv_axis:
            scaled_panels=unique_layouts[exact_kwp]
            s_pv={**s,**{f'{r}_panels':scaled_panels[r] for r in roof_names}}
            pv_prof=_map_pv_profile(pv_profile(s_pv),mappings,target_len)

            for b_kwh in bat_grid:
                comp_cell=rep_for_cap.get(b_kwh) if is_mixed else None
                s_cell=_candidate_battery_settings(s_pv,b_kwh,comp_cell)
                s_cell.update({
                    'quote_total':None,
                    'deduction_rate':deduction_rate,
                    'battery_calendar_fade':deg_annual,
                    'battery_replace_year':lifetime if s['battery_replace_year']>0 or opt.get('battery_lifetime_years') is not None else 0
                })

                # Exact parity with current configuration when layout and battery match
                if math.isclose(exact_kwp,current_exact_kwp,abs_tol=1e-4) and math.isclose(b_kwh,selected_cap,abs_tol=1e-4):
                    s_cell.update({
                        **s,
                        'quote_total':None,
                        'deduction_rate':deduction_rate,
                        'battery_calendar_fade':deg_annual,
                        'battery_replace_year':lifetime if s['battery_replace_year']>0 or opt.get('battery_lifetime_years') is not None else 0
                    })
                    if is_mixed:
                        comp_cell=selected_comp


                if spread_delta!=0.0:
                    eff_market=(base_export_price+spread_delta)/(capture if capture>0 else 1.0)
                    s_cell['export_market_price']=eff_market

                # Apply sensitivity cost multiplier if sensitivity_cost_delta_pct was specified
                if cost_multiplier!=1.0 and b_kwh>0:
                    if s_cell.get('battery_module_cost_override') is not None:
                        s_cell['battery_module_cost_override']=float(s_cell['battery_module_cost_override'])*cost_multiplier
                    if s_cell.get('battery_secondary_module_cost_override') is not None:
                        s_cell['battery_secondary_module_cost_override']=float(s_cell['battery_secondary_module_cost_override'])*cost_multiplier
                    s_cell['battery_cost_kwh']=float(s_cell.get('battery_cost_kwh',0.0))*cost_multiplier
                    s_cell['battery_module_extra_cost']=float(s_cell.get('battery_module_extra_cost',0.0))*cost_multiplier
                    s_cell['battery_secondary_module_extra_cost']=float(s_cell.get('battery_secondary_module_extra_cost',0.0))*cost_multiplier

                # If quote variants provided, the quote-variant capex overrides
                if marginal_source=='quote_variants' and b_kwh>0:
                    b_capex_target=battery_capex_amount(b_kwh)
                    s_cell['battery_module_cost_override']=None
                    s_cell['battery_secondary_module_cost_override']=None
                    s_cell['battery_module_extra_cost']=0.0
                    s_cell['battery_secondary_module_extra_cost']=0.0
                    s_cell['battery_cost_kwh']=float(b_capex_target/(b_kwh*vat_multiplier*premium_multiplier))

                s_cell=_routing_settings(s_cell,basis='pv_incremental',enforce_clipping=True)
                annual=dispatch_lifetime(
                    pv_prof,
                    load_mapped,
                    ev_mapped,
                    valid_mapped,
                    pzo_mapped,
                    s['horizon_years'],
                    s['ev_start_year'],
                    s['inverter_kw'],
                    b_kwh,
                    s['charge_kw'],
                    s['discharge_kw'],
                    sqrt_rte,
                    s['pv_first_year_loss'],
                    s['pv_annual_fade'],
                    deg_annual,
                    s['battery_cycle_fade'],
                    lifetime if s['battery_replace_year']>0 or opt.get('battery_lifetime_years') is not None else 0,
                    s['pv_battery_coupling']=='dc_coupled',physical_parameters(s_cell),month_mapped,hour_mapped
                )
                fin,_=_financial(s_cell,annual,exact_kwp,calculate_irr=False)
                npv_dict=fin['pv_incremental']['npv']
                feasible,clip_fraction,clip_cap=_routing_clipping_feasibility(s_cell,annual)
                cell_data=dict(
                    physical_feasible=feasible,
                    annual_clipping_fraction_max=clip_fraction,
                    clipping_fraction_limit=clip_cap,
                    pv_kwp=exact_kwp,
                    battery_kwh=float(b_kwh),
                    routing_priority=s_cell['routing_priority'],
                    resolved_routing_priority=resolve_routing_priority(s_cell),
                    npv_median=float(npv_dict['median']),
                    npv_mean=float(npv_dict['mean']),
                    npv_min=float(npv_dict['min']),
                    npv_max=float(npv_dict['max']),
                    capex_eur=float(fin['capex_eur']),
                    settings=s_cell
                )
                if is_mixed:
                    cell_data.update(
                        composition=comp_cell,
                        outside_analysis_search_range=bool(b_kwh>max_cap)
                    )
                else:
                    mod_kwh = float(s.get('battery_module_kwh', 0.0) or 0.0)
                    is_multiple = bool(mod_kwh > 0 and math.isclose(b_kwh % mod_kwh, 0.0, abs_tol=1e-6))
                    is_analytical = bool(b_kwh > 0 and not is_multiple)
                    cell_data.update(
                        is_analytical_sample=is_analytical,
                        capacity_nature='analytical_pro_rata' if is_analytical else 'hardware_multiple'
                    )
                heatmap_cells.append(cell_data)

        chart4_npv_heatmap = dict(
            pv_grid_kwp=sorted_pv_axis,
            battery_grid_kwh=bat_grid,
            cells=heatmap_cells,
            current_configuration=dict(
                pv_kwp=current_exact_kwp,
                battery_kwh=selected_cap,
                settings={**s,'quote_total':None},
                **({
                    'composition':selected_comp,
                    'outside_analysis_search_range':bool(selected_cap>max_cap)
                } if is_mixed else {})
            ),
            roof_mix_note='Roof sections scaled proportionally from current layout; roof fit not physically verified for larger arrays.'
        )

    # 8. Chart 5 Needed capacity CDF (365 days pooled across historical weather analogues)
    rep_choice=opt.get('weather_representative','median')
    if rep_choice not in ('low','median','high'):rep_choice='median'
    forecast_key=_energy_key(s)
    forecast_res=_forecast_runs(forecast_key,price_version)
    rep_idx=forecast_res['picks'][rep_choice]
    
    all_needed_days=[]
    daily_records=[]
    for wi in range(len(years)):
        w_year=years[wi]
        w_pv=mapped_pv[wi]
        w_load=mapped_load[wi]
        clipped_pv_w=np.minimum(w_pv,s['inverter_kw'])
        battery_charge_pv_w=w_pv if s['pv_battery_coupling']=='dc_coupled' else clipped_pv_w
        
        for day in range(days_in_year):
            h0=day*24;h1=h0+24
            pv_day=clipped_pv_w[h0:h1];charge_pv_day=battery_charge_pv_w[h0:h1];load_day=w_load[h0:h1]
            direct_possible=np.minimum(np.minimum(charge_pv_day,load_day),s['inverter_kw'])
            hourly_surplus=np.minimum(np.maximum(0.0,charge_pv_day-direct_possible),s['charge_kw'])
            surplus=float(hourly_surplus.sum())
            
            day_indices=np.where(pv_day>0.05)[0]
            if len(day_indices)>0:
                sunset=int(day_indices[-1])
                evening_net_load=float(np.maximum(0.0,load_day[sunset+1:]-pv_day[sunset+1:]).sum())
            else:
                evening_net_load=float(np.maximum(0.0,load_day-pv_day).sum())

            next_day=(day+1)%days_in_year
            next_pv=clipped_pv_w[next_day*24:(next_day+1)*24]
            next_load=w_load[next_day*24:(next_day+1)*24]
            next_indices=np.where(next_pv>0.05)[0]
            if len(next_indices)>0:
                sunrise=int(next_indices[0])
                morning_net_load=float(np.maximum(0.0,next_load[:sunrise]-next_pv[:sunrise]).sum())
            else:
                # Overcast / consecutive zero-solar day: full 24h demand counted
                morning_net_load=float(np.maximum(0.0,next_load-next_pv).sum())

            night_net_load=evening_net_load+morning_net_load
            day_needed=float(min(surplus*sqrt_rte,night_net_load/sqrt_rte))
            all_needed_days.append(day_needed)
            daily_records.append(dict(
                day=day,
                weather_year=w_year,
                needed_kwh=float(round(day_needed,2))
            ))
        
    sorted_needed=np.sort(np.array(all_needed_days))
    cdf_capacities=[float(x) for x in range(0,26)]
    cdf_points=[dict(
        capacity_kwh=c,
        fraction_days_met=float((sorted_needed<=c).mean()),
        days_met_count=int((sorted_needed<=c).sum()),
        fraction_days_full_utilization=float((sorted_needed>=c).mean())
    ) for c in cdf_capacities]
    
    cdf_percentiles=dict(
        min=float(round(float(sorted_needed[0]),2)),
        p10=float(round(float(np.percentile(sorted_needed,10)),2)),
        p25=float(round(float(np.percentile(sorted_needed,25)),2)),
        p50=float(round(float(np.median(sorted_needed)),2)),
        p75=float(round(float(np.percentile(sorted_needed,75)),2)),
        p80=float(round(float(np.percentile(sorted_needed,80)),2)),
        p90=float(round(float(np.percentile(sorted_needed,90)),2)),
        max=float(round(float(sorted_needed[-1]),2))
    )

    # 9. Chart 6 Full-year day x hour SOC heatmap
    if selected_cap<=0:
        active_cap=0.0
        empty_hours=0.0;full_hours=0.0;mid_hours=0.0
        empty_fraction=0.0;full_fraction=0.0;mid_fraction=0.0
        soc_matrix=[[0]*24 for _ in range(days_in_year)]
        monthly_summaries=[
            dict(
                month=m,
                mean_soc_pct=0.0,
                empty_fraction=0.0,
                full_fraction=0.0,
                mid_fraction=0.0
            ) for m in range(1,13)
        ]
        soc_status='NO_STORAGE'
        soc_note='No battery storage configured in current settings (0 kWh). Sizing analysis requires storage to display SOC dispatch.'
    else:
        active_cap=float(selected_cap)
        if len(cached_soc_traces)==len(years):
            soc_traces=[cached_soc_traces[wi] for wi in range(len(years))]
        else:
            rep_settings=_routing_settings({**s,'battery_kwh':active_cap},enforce_clipping=True)
            params_soc=physical_parameters(rep_settings)
            soc_traces=[]
            for wi in range(len(years)):
                _,rep_tr=dispatch_year(
                    mapped_pv[wi],mapped_load[wi],active_cap,
                    s['charge_kw'],s['discharge_kw'],sqrt_rte,
                    s['inverter_kw'],0.,mapped_pzo[wi],valid,
                    s['pv_battery_coupling']=='dc_coupled',
                    params_soc,
                    mapped_data['target_months'],mapped_data['target_std_hours']
                )
                soc_traces.append(rep_tr[:,4].copy())

        all_empty_fractions=[]
        all_full_fractions=[]
        all_empty_hours=[]
        all_full_hours=[]
        for tr in soc_traces:
            soc_s=tr[:target_len]
            emp_h=float((soc_s<=0.01*active_cap).sum())
            ful_h=float((soc_s>=0.99*active_cap).sum())
            all_empty_hours.append(emp_h)
            all_full_hours.append(ful_h)
            all_empty_fractions.append(float(emp_h/target_len))
            all_full_fractions.append(float(ful_h/target_len))

        empty_hours=float(np.mean(all_empty_hours))
        full_hours=float(np.mean(all_full_hours))
        mid_hours=float(target_len-empty_hours-full_hours)
        empty_fraction=float(np.mean(all_empty_fractions))
        full_fraction=float(np.mean(all_full_fractions))
        mid_fraction=float(max(0.0,1.0-empty_fraction-full_fraction))

        # Monthly summaries: per-month mean normalized SOC and mean of per-weather state indicators
        monthly_summaries=[]
        _t_months=mapped_data['target_months']
        for m in range(1,13):
            m_mask=(_t_months==m)
            m_empty=[]
            m_full=[]
            m_soc=[]
            for tr in soc_traces:
                soc_m=tr[:target_len][m_mask]
                m_empty.append((soc_m<=0.01*active_cap).mean())
                m_full.append((soc_m>=0.99*active_cap).mean())
                m_soc.append(soc_m.mean()/active_cap*100.0)
            m_emp_f=float(np.mean(m_empty))
            m_ful_f=float(np.mean(m_full))
            m_mid_f=float(max(0.0,1.0-m_emp_f-m_ful_f))
            m_soc_f=float(np.mean(m_soc))
            monthly_summaries.append(dict(
                month=m,
                mean_soc_pct=m_soc_f,
                empty_fraction=m_emp_f,
                full_fraction=m_ful_f,
                mid_fraction=m_mid_f
            ))

        mean_soc_series=np.mean([tr[:target_len] for tr in soc_traces],axis=0)
        soc_matrix=[]
        for day in range(days_in_year):
            row=[int(round(np.clip(mean_soc_series[day*24+hh]/active_cap*100,0,100))) for hh in range(24)]
            soc_matrix.append(row)
        soc_status='ACTIVE'
        soc_note=f'Continuous annual dispatch for {active_cap:.1f} kWh battery across full {target_len:,} hours: {len(years)}-year ensemble average across weather analogues ({years[0]}-{years[-1]}).'
    ded_pct=deduction_rate*100.0
    return dict(
        status='SCENARIO_PENDING',
        settings=s,
        options=opt,
        economics=dict(
            marginal_battery_cost_eur_kwh=marginal_cost,
            effective_marginal_battery_cost_eur_kwh=effective_marginal_cost,
            marginal_cost_source=marginal_source,
            marginal_cost_varies_by_step=bool(marginal_cost_varies),
            reference_candidate_description=ref_cand_desc,
            quote_variants_status=quote_variants_status,
            quote_10kwh_eur=q10,
            quote_15kwh_eur=q15,
            missing_fields=missing_fields,
            battery_lifetime_years=lifetime,
            deduction_rate=deduction_rate,
            annualized_marginal_cost_gross=annualized_cost_gross,
            annualized_marginal_cost_net=annualized_cost_net,
            incremental_deduction_pv_eur=float(pv_ded_per_kwh),
            import_price_eur_kwh=p_import,
            export_price_eur_kwh=p_export,
            base_spread_eur_kwh=p_import-p_export,
            roundtrip_efficiency=rte,
            margin_per_charge_cycle_eur_kwh=margin_charge,
            margin_per_delivered_cycle_eur_kwh=margin_deliv,
            margin_per_physical_cycle_eur_kwh=margin_physical,
            breakeven_charge_cycles_gross=breakeven_cycles_gross,
            breakeven_charge_cycles_net=breakeven_cycles_net,
            breakeven_physical_cycles_gross=breakeven_physical_gross,
            breakeven_physical_cycles_net=breakeven_physical_net,
            breakeven_delivered_cycles_gross=breakeven_deliv_gross,
            breakeven_delivered_cycles_net=breakeven_deliv_net,
            export_price_profile=s.get('export_price_profile','empirical_csud'),
            export_price_window=s.get('export_price_window','oct2025_sep2026'),
            export_price_source=prof_source,
            legacy_capture_factor_active=bool(prof_source=='flat_solar_capture_benchmark')
        ),
        source_vs_scenario=[
            dict(item='Benchmark battery cost',value=f"{s['battery_cost_kwh']:,.0f} €/usable kWh",source='Step 1 research benchmark (excl. VAT / premium)',status='provided'),
            dict(item='10 kWh quote variant',value=f'€{q10:,.0f}' if q10 is not None else 'Missing (null)',source='User quote entry' if q10 is not None else 'Quote pending',status='provided' if q10 is not None else 'missing'),
            dict(item='15 kWh quote variant',value=f'€{q15:,.0f}' if q15 is not None else 'Missing (null)',source='User quote entry' if q15 is not None else 'Quote pending',status='provided' if q15 is not None else 'missing'),
            dict(item='Effective marginal cost',value=f'{effective_marginal_cost:,.2f} €/kWh',source='Delta quote / delta capacity' if marginal_source=='quote_variants' else f'Benchmark capex: {ref_cand_desc}',status='calculated'),
            dict(item=f'Annualized marginal cost (net {ded_pct:.0f}% deduction)',value=f'{annualized_cost_net:,.2f} €/kWh/year',source=f'{lifetime:d}-year horizon with {ded_pct:.0f}% deduction (PV-discounted, tax-capacity capped)',status='scenario'),
            dict(item=f'Import commodity + network + tax ({s["electric_vat"]*100:.0f}% VAT)',value=f'{p_import:.4f} €/kWh',source=f'Scenario invoice-calibrated unit rates + {s["electric_vat"]*100:.0f}% VAT',status='scenario'),
            dict(item='Wholesale export price',value=f'{p_export:.4f} €/kWh',source='Flat benchmark (CSUD baseload × capture factor)' if prof_source=='flat_solar_capture_benchmark' else 'Empirical CSUD wholesale price less income taxes (with spread shift)',status='scenario')
        ],
        load_representativeness_note=f'Note on load representativeness: Measured household electricity load is derived from a single historical year of interval readings. While PV production is simulated across {len(years)} historical solar weather analogues, the annual load pattern represents one baseline household year scaled by heating degree hours and modeled appliances. Behavioral load variance across years is not measured.',
        chart1_capacity_dispatch=dict(
            capacities=chart1_capacities,
            weather_years=years,
            unit='kWh / year',
            grid_mode='mixed' if is_mixed else 'single_size',
            step_metadata=dict(
                mode='attainable_hardware_compositions' if is_mixed else 'integer_kwh_grid',
                subsampling_applied=False if is_mixed else (user_step>1),
                description='In mixed mode, grid points represent actual attainable module combinations rather than fixed 1-kWh steps.' if is_mixed else 'Grid points evaluated in 1-kWh steps.'
            )
        ),
        chart2_incremental_savings=dict(
            steps=inc_steps,
            annualized_marginal_cost_gross=annualized_cost_gross,
            annualized_marginal_cost_net=annualized_cost_net,
            marginal_cost_varies_by_step=bool(marginal_cost_varies),
            optimum_band=optimum_band
        ),
        chart3_efc_breakeven=dict(
            capacity_efc=chart3_efc,
            reference_candidate=marginal_source,
            reference_candidate_description=ref_cand_desc,
            marginal_cost_varies_by_step=bool(marginal_cost_varies),
            import_price_eur_kwh=p_import,
            export_price_eur_kwh=p_export,
            roundtrip_efficiency=rte,
            margin_per_charge_cycle_eur_kwh=margin_charge,
            margin_per_delivered_cycle_eur_kwh=margin_deliv,
            margin_per_physical_cycle_eur_kwh=margin_physical,
            breakeven_charge_cycles_gross=breakeven_cycles_gross,
            breakeven_charge_cycles_net=breakeven_cycles_net,
            breakeven_physical_cycles_gross=breakeven_physical_gross,
            breakeven_physical_cycles_net=breakeven_physical_net,
            breakeven_delivered_cycles_gross=breakeven_deliv_gross,
            breakeven_delivered_cycles_net=breakeven_deliv_net,
            physical_efc_definition='Delivered discharge / (sqrt(RTE) * capacity) — matches the engine cell aging model.',
            delivered_efc_definition='Delivered discharge to load / capacity = physical_efc * sqrt(RTE).',
            charge_input_efc_definition='AC charge input / capacity approx physical_efc / sqrt(RTE).',
            cycle_conversion_note='Both charge-input cycles * (P_import * RTE - P_export) and delivered cycles * (P_import - P_export / RTE) produce the identical annual euro margin. Chart 3 provides a single-year equivalent-cycle heuristic approximation that omits multi-year future export price ramp; direct authoritative sizing decisions to the full discounted NPV heatmap (Chart 4), which models complete lifetime cash flows and future export price ramp.'
        ),
        chart4_npv_heatmap=chart4_npv_heatmap,
        chart5_needed_capacity_cdf=dict(
            cdf_points=cdf_points,
            percentiles=cdf_percentiles,
            daily_needed_kwh=[float(round(v,2)) for v in all_needed_days],
            daily_records=daily_records,
            mean_needed_kwh=float(np.mean(sorted_needed)),
            weather_count=len(years),
            day_count=len(sorted_needed),
            weather_basis='ensemble_mean',
            representative_weather_year=years[rep_idx],
            weather_representative=rep_choice,
            diagnostic_nature='continuous_diagnostic',
            hardware_distinction_note='The daily needed-capacity CDF is a continuous energy-balance diagnostic representing storage requirements, distinct from discrete attainable physical hardware module choices (e.g. 5, 6.9, 10, 11.9, 13.8 kWh).',
            heuristic_description=f'Heuristic: Evaluates single-day cycle storage capacity across {days_in_year} calendar days pooled over {len(years)} historical weather analogues ({len(sorted_needed):,} total pooled daily records). For each day, needed capacity is min(charge-power-limited PV surplus * sqrt(RTE), following night load / sqrt(RTE)). Direct household PV and battery discharge remain inverter-AC limited; PV-to-battery charging follows the selected {s["pv_battery_coupling"]} approximation. Days with low surplus (winter) are solar-limited; days with low night load (summer) are load-limited. Evaluates single-day cycle storage capacity without multi-day carryover; consecutive zero-solar overcast days accumulate entire 24h demand. Calendar boundary wraps from Dec 31 to Jan 1.'
        ),
        chart6_soc_heatmap=dict(
            battery_capacity_kwh=active_cap,
            weather_year=None,
            weather_years=years,
            weather_count=len(years),
            weather_basis='ensemble_mean',
            representative_weather_year=years[rep_idx],
            weather_representative=rep_choice,
            status=soc_status,
            note=soc_note,
            empty_fraction=empty_fraction,
            full_fraction=full_fraction,
            mid_fraction=mid_fraction,
            empty_hours=empty_hours,
            full_hours=full_hours,
            mid_hours=mid_hours,
            hourly_soc_matrix=soc_matrix,
            monthly_summaries=monthly_summaries
        )
    )

def battery_sizing(settings=None, options=None, quote=None):
    """Perform comprehensive battery sizing analysis over all historical weather years."""
    s=validate(settings)
    
    # Normalize quote if provided: validates line items and applies equipment specs (DoD, RTE, power)
    if quote is not None:
        s_norm,q_norm,_,_=normalize_quote(s,quote)
        # Sizing evaluates multi-capacity alternative sweeps: decouple turnkey quote capex total
        s={**s_norm,'quote_total':None}
        quote=q_norm
        
    if options is not None and not isinstance(options,dict):
        raise ValueError('options must be a JSON object')
    opt=options or {}
    
    # Reject unknown options
    allowed_keys={
        'step','max_capacity','quote_10kwh_eur','quote_15kwh_eur',
        'battery_lifetime_years','weather_representative',
        'sensitivity_spread_delta','sensitivity_cost_delta_pct',
        'battery_degradation_annual','deduction_rate','load_scenario'
    }
    unknown=set(opt.keys())-allowed_keys
    if unknown:
        raise ValueError(f"Unknown options: {', '.join(sorted(unknown))}")
    
    # Extract and strictly validate options
    load_scenario=opt.get('load_scenario',s['load_scenario'])
    if load_scenario not in ('baseline','electrified','ev'):
        raise ValueError('load_scenario must be baseline, electrified, or ev')
    
    step=opt.get('step',1)
    if isinstance(step,bool) or not isinstance(step,int) or step not in (1,2):
        raise ValueError('step must be integer 1 or 2')
        
    max_cap=opt.get('max_capacity',25)
    if isinstance(max_cap,bool) or not isinstance(max_cap,int) or not 10<=max_cap<=50:
        raise ValueError('max_capacity must be integer between 10 and 50')
    if s.get('battery_mixed_sizes',False):
        enumerate_battery_compositions_by_capacity(0.0,float(max_cap),settings=s)
    
    q10=opt.get('quote_10kwh_eur');q15=opt.get('quote_15kwh_eur')
    if q10 is not None:
        if isinstance(q10,bool) or not isinstance(q10,(int,float)) or not math.isfinite(q10) or q10<0:
            raise ValueError('quote_10kwh_eur must be non-negative finite number')
        q10=float(q10)
    if q15 is not None:
        if isinstance(q15,bool) or not isinstance(q15,(int,float)) or not math.isfinite(q15) or q15<0:
            raise ValueError('quote_15kwh_eur must be non-negative finite number')
        q15=float(q15)
    if q10 is not None and q15 is not None and q15<=q10:
        raise ValueError('quote_15kwh_eur must exceed quote_10kwh_eur')
        
    lifetime=opt.get('battery_lifetime_years',s['battery_replace_year'] if s['battery_replace_year']>0 else 12)
    if isinstance(lifetime,bool) or not isinstance(lifetime,int) or not 1<=lifetime<=30:
        raise ValueError('battery_lifetime_years must be integer between 1 and 30')
    
    rep=opt.get('weather_representative','median')
    if rep not in ('low','median','high'):
        raise ValueError('weather_representative must be low, median, or high')
    
    spread_delta=opt.get('sensitivity_spread_delta',0.0)
    if isinstance(spread_delta,bool) or not isinstance(spread_delta,(int,float)) or not math.isfinite(spread_delta) or not -0.30<=spread_delta<=0.30:
        raise ValueError('sensitivity_spread_delta must be between -0.30 and +0.30')
    spread_delta=float(spread_delta)
    
    cost_delta=opt.get('sensitivity_cost_delta_pct',0.0)
    if isinstance(cost_delta,bool) or not isinstance(cost_delta,(int,float)) or not math.isfinite(cost_delta) or not -0.90<=cost_delta<=2.0:
        raise ValueError('sensitivity_cost_delta_pct must be between -0.90 and 2.0')
    cost_delta=float(cost_delta)
    
    deg=opt.get('battery_degradation_annual',s['battery_calendar_fade'])
    if isinstance(deg,bool) or not isinstance(deg,(int,float)) or not math.isfinite(deg) or not 0.0<=deg<=0.10:
        raise ValueError('battery_degradation_annual must be between 0.0 and 0.10')
    deg=float(deg)
    
    ded=opt.get('deduction_rate',s['deduction_rate'])
    if isinstance(ded,bool) or not isinstance(ded,(int,float)) or not math.isfinite(ded) or float(ded) not in (0.0,0.36,0.50):
        raise ValueError('deduction_rate must be exactly 0, 0.36, or 0.50')
    ded=float(ded)
    s=validate({**s,'load_scenario':load_scenario,'deduction_rate':ded})
    
    clean_options=dict(
        step=step,max_capacity=max_cap,
        quote_10kwh_eur=q10,quote_15kwh_eur=q15,
        battery_lifetime_years=lifetime,
        weather_representative=rep,
        sensitivity_spread_delta=spread_delta,
        sensitivity_cost_delta_pct=cost_delta,
        battery_degradation_annual=deg,
        deduction_rate=ded,
        load_scenario=load_scenario
    )
    
    # Hashable keys for LRU cache
    key_settings=tuple(sorted((k, freeze(v)) for k, v in s.items()))
    key_options=tuple(sorted((k, freeze(v)) for k, v in clean_options.items()))
    quote_fingerprint=hash(json.dumps(quote,sort_keys=True)) if quote else 0
    price_version=price_fingerprint(s)
    
    res=_battery_sizing_cached(current_runtime().key, key_settings,key_options,quote_fingerprint,price_version)
    return deepcopy(res)


SOLAR_OBJECTIVES = ('npv_eur', 'autarky', 'self_consumption', 'payback_years', 'discounted_payback_years')
SOLAR_EXHAUSTIVE_LIMIT = 250
SOLAR_EVALUATION_BUDGET = 350


def _cand_sig(c, yield_priority=None):
    yield_priority = _candidate_roof_order(c, yield_priority)
    roofs = c.get('roof_counts') or {}
    roof_tuple = tuple((r, roofs.get(r, 0)) for r in yield_priority)
    return (
        c.get('panel_count', 0),
        round(float(c.get('battery_kwh', 0.0)), 9),
        int(c.get('battery_modules', 0)),
        int(c.get('battery_secondary_modules', 0)),
        c.get('settings', {}).get('pv_battery_coupling', c.get('pv_battery_coupling')),
        c.get('settings', {}).get('routing_priority', c.get('routing_priority')),
        c.get('settings', {}).get('routing_switch_hour', c.get('routing_switch_hour')),
        c.get('settings', {}).get('scheduled_start_month', c.get('scheduled_start_month', 3)),
        c.get('settings', {}).get('scheduled_end_month', c.get('scheduled_end_month', 9)),
        roof_tuple
    )


def select_solar_recommendations(evaluated_candidates, yield_priority=None, options=None):
    """Deterministically select financial, balanced (main), budget, and smaller choices plus top alternatives."""
    options = options or {}
    financial_basis = options.get('financial_basis', 'project')
    basis_label = 'equity NPV' if financial_basis == 'after_financing' else 'project NPV'
    sac_pct = float(options.get('npv_sacrifice_pct', options.get('allowed_sacrifice_pct', 5.0)))
    cov_pct = float(options.get('coverage_target_pct', options.get('budget_coverage_target_pct', 70.0)))
    retained_profit_pct = float(options.get('retained_profit_pct', 95.0))
    exact = bool(options.get('exact', False))
    cov_target = cov_pct / 100.0

    candidates = list(evaluated_candidates)
    yield_priority = _candidate_roof_order(
        {'roof_counts': {r: 0 for c in candidates for r in c.get('roof_counts', {})}}, yield_priority)
    base_s = options.get('settings')
    if base_s is None and candidates:
        base_s = candidates[0].get('settings')
    if base_s is None:
        base_s = defaults()

    s_zero = {
        **base_s,
        **_roof_settings(dict.fromkeys(yield_priority, 0)),
        'battery_kwh': 0.0,
        'battery_modules': 0,
        'battery_secondary_modules': 0,
        'battery_mixed_sizes': False,
        'quote_total': None
    }

    w_count = options.get('weather_count')
    if w_count is None:
        if candidates and 'npv_by_weather' in candidates[0]:
            w_count = len(candidates[0]['npv_by_weather'])
        else:
            w_count = len(data()['years'])
    zero_env = envelope([0.0] * w_count)

    grid_import_env = options.get('annual_grid_import_kwh')
    if grid_import_env is None:
        if candidates and 'annual_grid_import_kwh' in candidates[0] and candidates[0].get('is_baseline'):
            grid_import_env = candidates[0]['annual_grid_import_kwh']
        else:
            try:
                base_eval = evaluate(s_zero, detail=False)
                grid_import_env = base_eval['metrics']['grid_import_kwh']
            except Exception:
                grid_import_env = zero_env
    grid_import_med_opt = options.get('annual_grid_import_kwh_median')
    if grid_import_med_opt is not None:
        try:
            grid_import_median = float(grid_import_med_opt)
        except (ValueError, TypeError):
            grid_import_median = 0.0
    else:
        grid_import_median = float(grid_import_env.get('median', 0.0) if isinstance(grid_import_env, dict) and grid_import_env.get('median') is not None else 0.0)
    grid_import_mean_opt = options.get('annual_grid_import_kwh_mean')
    if grid_import_mean_opt is not None:
        try:
            grid_import_mean = float(grid_import_mean_opt)
        except (ValueError, TypeError):
            grid_import_mean = grid_import_median
    else:
        grid_import_mean = float(grid_import_env.get('mean', grid_import_median) if isinstance(grid_import_env, dict) and grid_import_env.get('mean') is not None else grid_import_median)

    def _cand_sig_local(c):
        return _cand_sig(c, yield_priority)
    _cand_sig_fn = _cand_sig_local

    def _roof_priority_tuple(c):
        roofs = c.get('roof_counts') or {}
        return tuple(roofs.get(r, 0) for r in yield_priority)

    def _is_near_equivalent(c1, c2):
        if c1 is None or c2 is None:
            return False
        same_hardware = (
            c1.get('panel_count') == c2.get('panel_count') and
            abs(float(c1.get('battery_kwh', 0.0)) - float(c2.get('battery_kwh', 0.0))) < 1e-6 and
            c1.get('battery_modules') == c2.get('battery_modules') and
            c1.get('battery_secondary_modules') == c2.get('battery_secondary_modules')
        )
        cov1 = c1.get('autarky_mean', c1.get('autarky_median', 0.0))
        cov2 = c2.get('autarky_mean', c2.get('autarky_median', 0.0))
        if same_hardware:
            if abs(c1['npv_mean'] - c2['npv_mean']) < 50.0 and abs(cov1 - cov2) < 0.005:
                return True
        capex_diff = abs(c1['capex_eur'] - c2['capex_eur'])
        npv_diff = abs(c1['npv_mean'] - c2['npv_mean'])
        cov_diff = abs(cov1 - cov2)
        if capex_diff < 150.0 and npv_diff < 75.0 and cov_diff < 0.01:
            return True
        return False

    def _num_or_none(val, precision=None):
        if val is None:
            return None
        try:
            f = float(val)
            return round(f, precision) if precision is not None else f
        except (TypeError, ValueError):
            return None

    # Sorting keys for roles
    def _fin_key(c):
        return (
            c['npv_mean'],
            -c['capex_eur'],
            -c['panel_count'],
            -c['battery_kwh'],
            -c.get('battery_modules', 0),
            -c.get('battery_secondary_modules', 0),
            _roof_priority_tuple(c)
        )

    def _bal_key(c):
        return (
            c.get('autarky_mean', c.get('autarky_median', 0.0)),
            -c['capex_eur'],
            -c['panel_count'],
            -c['battery_kwh'],
            -c.get('battery_modules', 0),
            -c.get('battery_secondary_modules', 0),
            _roof_priority_tuple(c)
        )

    def _bud_key(c):
        return (
            -c['capex_eur'],
            c.get('autarky_mean', c.get('autarky_median', 0.0)),
            c['npv_mean'],
            -c['panel_count'],
            -c['battery_kwh'],
            -c.get('battery_modules', 0),
            -c.get('battery_secondary_modules', 0),
            _roof_priority_tuple(c)
        )

    def _smaller_key(c):
        return (
            -c['capex_eur'],
            -c['panel_count'],
            -c['battery_kwh'],
            -c.get('battery_modules', 0),
            -c.get('battery_secondary_modules', 0),
            _roof_priority_tuple(c)
        )

    # Separate install candidates and positive selected NPV candidates
    install_candidates = [c for c in candidates if c.get('panel_count', 0) > 0]
    positive_candidates = [c for c in install_candidates if c.get('npv_mean', 0) > 0]

    # Find or construct true no-install baseline candidate with coherent settings and baseline grid import
    zero_cand = next((c for c in candidates if c.get('panel_count', 0) == 0 and math.isclose(float(c.get('battery_kwh', 0.0)), 0.0, abs_tol=1e-6)), None)
    if zero_cand is None:
        zero_cand = {
            'panel_count': 0, 'kwp': 0.0,
            'roof_counts': {r: 0 for r in yield_priority},
            'battery_kwh': 0.0, 'battery_modules': 0, 'battery_secondary_modules': 0,
            'first_modules': 0, 'battery_module_kwh': None, 'battery_secondary_module_kwh': None,
            'battery_mixed_sizes': False,
            'capex_eur': 0.0, 'solar_capex_eur': 0.0,
            'solar_loan_principal_eur': 0.0, 'initial_solar_equity_eur': 0.0,
            'solar_loan_ratio': 1.0, 'loan_principal_eur': 0.0, 'total_loan_scope_eur': 0.0,
            'financial_basis': financial_basis,
            'npv': zero_env, 'npv_median': 0.0, 'npv_mean': 0.0, 'npv_min': 0.0, 'npv_max': 0.0, 'npv_by_weather': [0.0] * w_count,
            'project_npv': zero_env, 'project_npv_median': 0.0, 'project_npv_mean': 0.0, 'project_npv_min': 0.0, 'project_npv_max': 0.0, 'project_npv_by_weather': [0.0] * w_count,
            'equity_npv': zero_env, 'equity_npv_median': 0.0, 'equity_npv_mean': 0.0, 'equity_npv_min': 0.0, 'equity_npv_max': 0.0, 'equity_npv_by_weather': [0.0] * w_count,
            'discounted_payback_years': None, 'simple_payback_years': None,
            'autarky': zero_env, 'autarky_median': 0.0, 'autarky_mean': 0.0,
            'self_consumption': zero_env, 'self_consumption_median': 0.0, 'self_consumption_mean': 0.0,
            'annual_pv_kwh': zero_env, 'annual_pv_kwh_median': 0.0, 'annual_pv_kwh_mean': 0.0,
            'annual_pv_input_kwh_median': 0.0, 'annual_pv_input_kwh_mean': 0.0,
            'annual_clipped_kwh_median': 0.0, 'annual_clipped_kwh_mean': 0.0,
            'annual_clipped_pct_median': 0.0, 'annual_clipped_pct_mean': 0.0,
            'annual_grid_import_kwh': grid_import_env, 'annual_grid_import_kwh_median': grid_import_median,
            'annual_grid_import_kwh_mean': grid_import_mean,
            'annual_grid_export_kwh': zero_env, 'annual_grid_export_kwh_median': 0.0, 'annual_grid_export_kwh_mean': 0.0,
            'annual_battery_discharge_kwh': zero_env, 'annual_battery_discharge_kwh_median': 0.0, 'annual_battery_discharge_kwh_mean': 0.0,
            'settings': s_zero,
            'resolved_routing_priority': 'pv_only',
            'is_baseline': True
        }

    role_status = {}

    if not positive_candidates:
        # No positive installation configuration exists or installation domain is empty.
        # Financial no-install baseline is always available, not counted as a valid installation.
        if exact:
            fin_reason = 'No installation configuration in the evaluated domain achieves positive net present value under current economic assumptions; no-install baseline (€0 capex, €0 NPV) provides higher economic return.'
            fin_explanation = 'The separate no-install baseline (€0 capex, €0 NPV) provides higher financial return than any installation configuration in the domain.'
            bal_reason = 'No installation configuration in the evaluated domain achieves positive net present value. Balanced choice requires strictly positive net present value.'
            bud_reason = 'No configuration in the entire domain achieves positive net present value meeting coverage target.'
            small_reason = 'No installation configuration in the evaluated domain achieves positive net present value. Smaller choice requires strictly positive net present value.'
        else:
            fin_reason = 'No installation configuration among evaluated discrete samples achieves positive net present value under current economic assumptions; no-install baseline (€0 capex, €0 NPV) provides higher economic return.'
            fin_explanation = 'The separate no-install baseline (€0 capex, €0 NPV) provides higher financial return than any evaluated installation.'
            bal_reason = 'No installation configuration among evaluated discrete samples achieves positive net present value. Balanced choice requires strictly positive net present value.'
            bud_reason = 'Coverage target was not met with positive net present value among evaluated configurations; domain feasibility remains unknown.'
            small_reason = 'No installation configuration among evaluated discrete samples achieves positive net present value. Smaller choice requires strictly positive net present value.'

        financial_choice = dict(zero_cand)
        financial_choice['role'] = 'financial'
        financial_choice['role_label'] = 'No-install baseline'
        financial_choice['is_positive_npv'] = False
        financial_choice['is_no_install'] = True
        financial_choice['status'] = 'certified_no_install' if exact else 'best_found_no_install'
        financial_choice['certified_constrained_optimum'] = exact
        financial_choice['reason'] = fin_reason
        financial_choice['explanation'] = fin_explanation

        balanced_choice = None
        budget_choice = None
        smaller_choice = None

        role_status['financial'] = {
            'role': 'financial',
            'available': True,
            'is_no_install': True,
            'status': 'certified_no_install' if exact else 'best_found_no_install',
            'certified_constrained_optimum': exact,
            'reason': fin_reason
        }
        role_status['balanced'] = {
            'role': 'balanced',
            'available': False,
            'status': 'unavailable',
            'certified_constrained_optimum': False,
            'reason': bal_reason
        }
        role_status['budget'] = {
            'role': 'budget',
            'available': False,
            'status': 'proved_unavailable' if exact else 'target_not_met_among_evaluated',
            'certified_constrained_optimum': False,
            'infeasibility_proved': exact,
            'reason': bud_reason
        }
        role_status['smaller'] = {
            'role': 'smaller',
            'available': False,
            'status': 'unavailable',
            'certified_constrained_optimum': False,
            'reason': small_reason
        }
    else:
        # Positive installation candidates exist
        # 1. Financial: max mean selected NPV
        financial_cand = max(positive_candidates, key=_fin_key)
        best_npv = financial_cand['npv_mean']

        financial_choice = dict(financial_cand)
        financial_choice['role'] = 'financial'
        financial_choice['role_label'] = 'Best financial return'
        financial_choice['is_positive_npv'] = True
        financial_choice['is_no_install'] = False
        financial_choice['status'] = 'certified_optimum' if exact else 'best_found'
        financial_choice['certified_constrained_optimum'] = exact
        if exact:
            financial_choice['explanation'] = (
                f"Certified optimum under {financial_basis} basis: maximum mean {basis_label} "
                f"({financial_cand['npv_mean']:,.0f} €) with {financial_cand['panel_count']} panels "
                f"({financial_cand['kwp']:.2f} kWp) and {financial_cand.get('battery_kwh', 0.0):.1f} kWh storage."
            )
        else:
            financial_choice['explanation'] = (
                f"Best found under {financial_basis} basis among evaluated configurations: highest mean {basis_label} "
                f"({financial_cand['npv_mean']:,.0f} €) with {financial_cand['panel_count']} panels "
                f"({financial_cand['kwp']:.2f} kWp) and {financial_cand.get('battery_kwh', 0.0):.1f} kWh storage."
            )

        role_status['financial'] = {
            'role': 'financial',
            'available': True,
            'is_no_install': False,
            'status': 'certified_optimum' if exact else 'best_found',
            'certified_constrained_optimum': exact,
            'reason': 'Certified global maximum mean selected NPV.' if exact else 'Highest mean selected NPV among evaluated configurations within search budget.'
        }

        # 2. Balanced choice: max coverage retaining user percentage of best positive NPV, requiring strictly positive NPV
        threshold_npv = (1.0 - sac_pct / 100.0) * best_npv
        eligible_balanced = [
            c for c in install_candidates
            if c.get('npv_mean', 0) > 0 and c['npv_mean'] >= threshold_npv - 1e-9
        ]
        if eligible_balanced:
            balanced_cand = max(eligible_balanced, key=_bal_key)
            balanced_choice = dict(balanced_cand)
            balanced_choice['role'] = 'balanced'
            balanced_choice['role_label'] = 'Balanced choice (Main)'
            balanced_choice['allowed_sacrifice_pct'] = sac_pct
            balanced_choice['npv_threshold_eur'] = round(threshold_npv, 2)
            balanced_choice['retained_npv_pct'] = round((balanced_cand['npv_mean'] / best_npv) * 100.0, 2)
            sac_bal_eur = max(0.0, round(best_npv - balanced_cand['npv_mean'], 2))
            balanced_choice['sacrificed_npv_eur'] = sac_bal_eur
            balanced_choice['sacrificed_npv_pct'] = round((sac_bal_eur / best_npv) * 100.0, 2)
            cov_bal = balanced_cand.get('autarky_mean', balanced_cand['autarky_median'])
            cov_fin = financial_cand.get('autarky_mean', financial_cand['autarky_median'])
            balanced_choice['coverage_gain_pct'] = round((cov_bal - cov_fin) * 100.0, 2)
            balanced_choice['is_same_as_financial'] = (_cand_sig(balanced_cand) == _cand_sig(financial_cand))
            balanced_choice['status'] = 'certified_optimum' if exact else 'best_found'
            balanced_choice['certified_constrained_optimum'] = exact
            balanced_choice['explanation'] = (
                f'Preference-based selection (NPV sacrifice ≤ {sac_pct:g}% of financial optimum); '
                f'retains {balanced_choice["retained_npv_pct"]:.1f}% max NPV for '
                f'{cov_bal*100:.1f}% household demand coverage '
                f'(vs {cov_fin*100:.1f}% for financial winner). '
                f'Reflects user preference, not a universal optimum.'
            )
            role_status['balanced'] = {
                'role': 'balanced',
                'available': True,
                'status': 'certified_optimum' if exact else 'best_found',
                'certified_constrained_optimum': exact,
                'reason': f'Certified highest coverage retaining at least {100.0 - sac_pct:g}% of financial NPV.' if exact else f'Highest coverage among evaluated configurations retaining at least {100.0 - sac_pct:g}% of financial NPV.'
            }
        else:
            balanced_choice = None
            role_status['balanced'] = {
                'role': 'balanced',
                'available': False,
                'status': 'unavailable',
                'certified_constrained_optimum': False,
                'reason': f'No installation configuration in the evaluated domain achieves positive net present value retaining at least {100.0 - sac_pct:g}% of financial NPV.' if exact else f'No installation configuration among evaluated discrete samples achieves positive net present value retaining at least {100.0 - sac_pct:g}% of financial NPV.'
            }

        # 3. Budget choice: lowest capex with strictly positive NPV and coverage >= target
        eligible_budget = [
            c for c in install_candidates
            if c.get('npv_mean', 0) > 0 and c.get('autarky_mean', c['autarky_median']) >= cov_target - 1e-9
        ]
        if eligible_budget:
            budget_cand = max(eligible_budget, key=_bud_key)
            budget_choice = dict(budget_cand)
            budget_choice['role'] = 'budget'
            budget_choice['role_label'] = 'Budget choice'
            budget_choice['coverage_target_pct'] = cov_pct
            budget_choice['capex_savings_vs_financial'] = round(financial_cand['capex_eur'] - budget_cand['capex_eur'], 2)
            budget_choice['is_same_as_financial'] = (_cand_sig(budget_cand) == _cand_sig(financial_cand))
            budget_choice['is_same_as_balanced'] = (balanced_choice is not None and _cand_sig(budget_cand) == _cand_sig(balanced_choice))
            budget_choice['status'] = 'certified_optimum' if exact else 'best_found'
            budget_choice['certified_constrained_optimum'] = exact
            role_status['budget'] = {
                'role': 'budget',
                'available': True,
                'status': 'certified_optimum' if exact else 'best_found',
                'certified_constrained_optimum': exact,
                'reason': f'Certified lowest installed cost meeting coverage target ({cov_pct:g}%) with positive selected NPV.' if exact else f'Lowest installed cost meeting coverage target ({cov_pct:g}%) with positive selected NPV among evaluated configurations.'
            }
        else:
            budget_choice = None
            role_status['budget'] = {
                'role': 'budget',
                'available': False,
                'status': 'proved_unavailable' if exact else 'target_not_met_among_evaluated',
                'certified_constrained_optimum': False,
                'infeasibility_proved': exact,
                'reason': f'No configuration in the entire domain achieves coverage target ({cov_pct:g}%) with positive selected NPV.' if exact else f'Coverage target ({cov_pct:g}%) with positive selected NPV was not met among evaluated configurations; domain feasibility remains unproven.'
            }

        # 4. Smaller choice: lowest capex retaining retained_profit_pct of best positive NPV
        smaller_threshold_npv = (retained_profit_pct / 100.0) * best_npv
        eligible_smaller = [
            c for c in install_candidates
            if c.get('npv_mean', 0) > 0 and c['npv_mean'] >= smaller_threshold_npv - 1e-9
        ]
        if eligible_smaller:
            smaller_cand = max(eligible_smaller, key=_smaller_key)
            smaller_choice = dict(smaller_cand)
            smaller_choice['role'] = 'smaller'
            smaller_choice['role_label'] = 'Smaller efficient array'
            smaller_choice['retained_profit_pct'] = retained_profit_pct
            smaller_choice['npv_threshold_eur'] = round(smaller_threshold_npv, 2)
            smaller_choice['retained_npv_pct'] = round((smaller_cand['npv_mean'] / best_npv) * 100.0, 2)
            sac_smaller_eur = max(0.0, round(best_npv - smaller_cand['npv_mean'], 2))
            smaller_choice['sacrificed_npv_eur'] = sac_smaller_eur
            smaller_choice['sacrificed_npv_pct'] = round((sac_smaller_eur / best_npv) * 100.0, 2)
            smaller_choice['capex_savings_vs_financial'] = round(financial_cand['capex_eur'] - smaller_cand['capex_eur'], 2)
            smaller_choice['is_same_as_financial'] = (_cand_sig(smaller_cand) == _cand_sig(financial_cand))
            smaller_choice['is_same_as_balanced'] = (balanced_choice is not None and _cand_sig(smaller_cand) == _cand_sig(balanced_choice))
            smaller_choice['is_same_as_budget'] = (budget_choice is not None and _cand_sig(smaller_cand) == _cand_sig(budget_choice))
            smaller_choice['status'] = 'certified_optimum' if exact else 'best_found'
            smaller_choice['certified_constrained_optimum'] = exact
            if exact:
                smaller_choice['explanation'] = (
                    f'Certified minimum installed cost configuration retaining at least {retained_profit_pct:g}% of financial optimum NPV '
                    f'(retains {smaller_choice["retained_npv_pct"]:.1f}% max NPV for €{smaller_choice["capex_savings_vs_financial"]:,.0f} lower capex).'
                )
            else:
                smaller_choice['explanation'] = (
                    f'Lowest installed cost configuration among evaluated candidates retaining at least {retained_profit_pct:g}% of financial best-found NPV '
                    f'(retains {smaller_choice["retained_npv_pct"]:.1f}% max NPV for €{smaller_choice["capex_savings_vs_financial"]:,.0f} lower capex).'
                )
            role_status['smaller'] = {
                'role': 'smaller',
                'available': True,
                'status': 'certified_optimum' if exact else 'best_found',
                'certified_constrained_optimum': exact,
                'reason': f'Certified lowest installed cost retaining at least {retained_profit_pct:g}% of financial optimum NPV.' if exact else f'Lowest installed cost among evaluated configurations retaining at least {retained_profit_pct:g}% of financial optimum NPV.'
            }
        else:
            smaller_choice = None
            role_status['smaller'] = {
                'role': 'smaller',
                'available': False,
                'status': 'unavailable',
                'certified_constrained_optimum': False,
                'reason': f'No installation configuration in the evaluated domain achieves positive net present value retaining at least {retained_profit_pct:g}% of financial NPV.' if exact else f'No installation configuration among evaluated discrete samples achieves positive net present value retaining at least {retained_profit_pct:g}% of financial NPV.'
            }

    rec_choices = {
        'financial': financial_choice,
        'balanced': balanced_choice,
        'budget': budget_choice,
        'smaller': smaller_choice,
        'preferences': {
            'allowed_sacrifice_pct': sac_pct,
            'coverage_target_pct': cov_pct,
            'retained_profit_pct': retained_profit_pct,
            'financial_basis': financial_basis
        },
        'role_status': role_status,
        'main_recommendation': 'balanced' if balanced_choice is not None else ('financial' if (financial_choice is not None and not financial_choice.get('is_no_install')) else None)
    }

    # Top alternatives (up to 5 distinct physical layouts among install candidates)
    top_alternatives = []
    if install_candidates:
        primary_choices = []
        if financial_choice is not None and financial_choice.get('panel_count', 0) > 0:
            primary_choices.append((financial_choice, 'Best financial return'))
        if balanced_choice is not None and balanced_choice.get('panel_count', 0) > 0:
            primary_choices.append((balanced_choice, 'Balanced choice (Main)'))
        if budget_choice is not None and budget_choice.get('panel_count', 0) > 0:
            primary_choices.append((budget_choice, 'Budget choice'))
        if smaller_choice is not None and smaller_choice.get('panel_count', 0) > 0:
            primary_choices.append((smaller_choice, 'Smaller efficient array'))

        anchors_to_evaluate = []
        ref_fin = financial_choice if (financial_choice is not None and financial_choice.get('panel_count', 0) > 0) else None
        if ref_fin:
            # Smaller PV array than financial winner (meaningfully smaller if available)
            meaningful_smaller = [
                c for c in install_candidates
                if c['panel_count'] <= max(1, int(ref_fin['panel_count'] * 0.8)) and c['panel_count'] < ref_fin['panel_count']
            ]
            smaller_arrays = meaningful_smaller if meaningful_smaller else [
                c for c in install_candidates
                if c['panel_count'] < ref_fin['panel_count']
            ]
            if smaller_arrays:
                pos_smaller = [c for c in smaller_arrays if c['npv_mean'] > 0]
                if pos_smaller:
                    best_smaller = max(pos_smaller, key=_fin_key)
                else:
                    best_smaller = max(smaller_arrays, key=_fin_key)
                anchors_to_evaluate.append((best_smaller, 'Smaller array'))

        # Maximum household coverage
        max_cov = max(install_candidates, key=lambda c: (c.get('autarky_mean', c['autarky_median']), c['npv_mean'], -c['capex_eur']))
        anchors_to_evaluate.append((max_cov, 'Maximum coverage'))

        # Lowest capex with positive NPV if available
        pos_all = [c for c in install_candidates if c['npv_mean'] > 0]
        if pos_all:
            lowest_capex = min(pos_all, key=lambda c: (c['capex_eur'], -c.get('autarky_mean', c['autarky_median']), -c['npv_mean']))
        else:
            lowest_capex = min(install_candidates, key=lambda c: (c['capex_eur'], -c['npv_mean']))
        anchors_to_evaluate.append((lowest_capex, 'Lowest upfront investment'))

        # Pure solar (0 kWh battery)
        pure_solar = [c for c in install_candidates if float(c.get('battery_kwh', 0.0)) == 0.0]
        if pure_solar:
            best_pure = max(pure_solar, key=_fin_key)
            anchors_to_evaluate.append((best_pure, 'Pure solar (0 kWh battery)'))

        # Maximum PV capacity
        max_p = max(c['panel_count'] for c in install_candidates)
        max_p_cands = [c for c in install_candidates if c['panel_count'] == max_p]
        best_max_p = max(max_p_cands, key=lambda c: (c['npv_mean'], c.get('autarky_mean', c['autarky_median']), -c['capex_eur']))
        anchors_to_evaluate.append((best_max_p, 'Maximum PV capacity'))

        ordered_entries = []
        seen_sigs = {}
        for cand, role in primary_choices:
            if cand is None:
                continue
            sig = _cand_sig(cand)
            if sig in seen_sigs:
                entry = seen_sigs[sig]
                if role not in entry['roles']:
                    entry['roles'].append(role)
            else:
                entry = {'cand': cand, 'roles': [role], 'sig': sig}
                seen_sigs[sig] = entry
                ordered_entries.append(entry)

        for cand, role in anchors_to_evaluate:
            if cand is None:
                continue
            sig = _cand_sig(cand)
            if sig in seen_sigs:
                entry = seen_sigs[sig]
                if role not in entry['roles']:
                    entry['roles'].append(role)
            else:
                if len(ordered_entries) < 5:
                    if not any(_is_near_equivalent(cand, e['cand']) for e in ordered_entries):
                        entry = {'cand': cand, 'roles': [role], 'sig': sig}
                        seen_sigs[sig] = entry
                        ordered_entries.append(entry)

        # Sort ordered entries by capex ascending, then npv descending
        ordered_entries.sort(key=lambda e: (e['cand']['capex_eur'], -e['cand']['npv_mean'], e['cand']['panel_count']))

        fin_capex = float(financial_choice['capex_eur']) if financial_choice else 0.0
        fin_npv = float(financial_choice['npv_mean']) if financial_choice else 0.0
        fin_autarky = float(financial_choice.get('autarky_mean', financial_choice.get('autarky_median', 0.0))) if financial_choice else 0.0

        for e in ordered_entries:
            c = e['cand']
            sig = e['sig']
            is_fin = (financial_choice is not None and sig == _cand_sig(financial_choice))
            is_bal = (balanced_choice is not None and sig == _cand_sig(balanced_choice))
            is_bud = (budget_choice is not None and sig == _cand_sig(budget_choice))
            is_smaller = (smaller_choice is not None and sig == _cand_sig(smaller_choice))
            extra_capex = round(float(c['capex_eur']) - fin_capex, 2)
            npv_diff = round(float(c['npv_mean']) - fin_npv, 2)
            retained = round((float(c['npv_mean']) / fin_npv) * 100.0, 1) if fin_npv > 0 else None
            cov_mean_val = float(c.get('autarky_mean', c['autarky_median']))
            cov_diff_pct = round((cov_mean_val - fin_autarky) * 100.0, 1)

            alt_dict = {
                'panel_count': int(c['panel_count']),
                'kwp': float(c['kwp']),
                'roof_counts': dict(c.get('roof_counts') or {}),
                'battery_kwh': float(c.get('battery_kwh', 0.0)),
                'battery_modules': int(c.get('battery_modules', 0)),
                'battery_secondary_modules': int(c.get('battery_secondary_modules', 0)),
                'first_modules': int(c.get('first_modules', int(c.get('battery_modules', 0)) - int(c.get('battery_secondary_modules', 0)))),
                'battery_module_kwh': c.get('battery_module_kwh'),
                'battery_secondary_module_kwh': c.get('battery_secondary_module_kwh'),
                'battery_mixed_sizes': bool(c.get('battery_mixed_sizes', False)),
                'capex_eur': float(c['capex_eur']),
                'solar_capex_eur': float(c.get('solar_capex_eur', c['capex_eur'])),
                'solar_loan_principal_eur': float(c.get('solar_loan_principal_eur', 0.0)),
                'initial_solar_equity_eur': float(c.get('initial_solar_equity_eur', c['capex_eur'])),
                'solar_loan_ratio': float(c.get('solar_loan_ratio', 1.0)),
                'loan_principal_eur': float(c.get('loan_principal_eur', 0.0)),
                'total_loan_scope_eur': float(c.get('total_loan_scope_eur', c['capex_eur'])),
                'financial_basis': financial_basis,
                'extra_capex_vs_financial': extra_capex,
                'npv_median': float(c['npv_median']),
                'npv_mean': float(c.get('npv_mean', c['npv_median'])),
                'npv_min': float(c.get('npv_min', c['npv_median'])),
                'npv_max': float(c.get('npv_max', c['npv_median'])),
                'project_npv_median': float(c.get('project_npv_median', c['npv_median'])),
                'project_npv_mean': float(c.get('project_npv_mean', c.get('npv_mean', c['npv_median']))),
                'project_npv_min': float(c.get('project_npv_min', c.get('npv_min', c['npv_median']))),
                'project_npv_max': float(c.get('project_npv_max', c.get('npv_max', c['npv_median']))),
                'equity_npv_median': float(c.get('equity_npv_median', c['npv_median'])),
                'equity_npv_mean': float(c.get('equity_npv_mean', c.get('npv_mean', c['npv_median']))),
                'equity_npv_min': float(c.get('equity_npv_min', c.get('npv_min', c['npv_median']))),
                'equity_npv_max': float(c.get('equity_npv_max', c.get('npv_max', c['npv_median']))),
                'npv_diff_vs_financial': npv_diff,
                'retained_npv_pct': retained,
                'autarky_median': float(c['autarky_median']),
                'autarky_mean': cov_mean_val,
                'coverage_diff_vs_financial_pct': cov_diff_pct,
                'self_consumption_median': _num_or_none(c.get('self_consumption_median')),
                'self_consumption_mean': _num_or_none(c.get('self_consumption_mean', c.get('self_consumption_median'))),
                'annual_pv_kwh_median': _num_or_none(c.get('annual_pv_kwh_median'), 1),
                'annual_pv_kwh_mean': _num_or_none(c.get('annual_pv_kwh_mean', c.get('annual_pv_kwh_median')), 1),
                'annual_pv_input_kwh_median': _num_or_none(c.get('annual_pv_input_kwh_median'), 1),
                'annual_pv_input_kwh_mean': _num_or_none(c.get('annual_pv_input_kwh_mean', c.get('annual_pv_input_kwh_median')), 1),
                'annual_clipped_kwh_median': _num_or_none(c.get('annual_clipped_kwh_median'), 1),
                'annual_clipped_kwh_mean': _num_or_none(c.get('annual_clipped_kwh_mean', c.get('annual_clipped_kwh_median')), 1),
                'annual_clipped_pct_median': _num_or_none(c.get('annual_clipped_pct_median'), 2),
                'annual_clipped_pct_mean': _num_or_none(c.get('annual_clipped_pct_mean', c.get('annual_clipped_pct_median')), 2),
                'annual_grid_import_kwh_median': _num_or_none(c.get('annual_grid_import_kwh_median'), 1),
                'annual_grid_import_kwh_mean': _num_or_none(c.get('annual_grid_import_kwh_mean', c.get('annual_grid_import_kwh_median')), 1),
                'annual_grid_export_kwh_median': _num_or_none(c.get('annual_grid_export_kwh_median'), 1),
                'annual_grid_export_kwh_mean': _num_or_none(c.get('annual_grid_export_kwh_mean', c.get('annual_grid_export_kwh_median')), 1),
                'simple_payback_years': _num_or_none(c.get('simple_payback_years'), 2),
                'discounted_payback_years': _num_or_none(c.get('discounted_payback_years'), 2),
                'roles': list(e['roles']),
                'role_label': ' · '.join(e['roles']),
                'is_financial': is_fin,
                'is_balanced': is_bal,
                'is_budget': is_bud,
                'is_smaller': is_smaller,
                'reached_inverter_ceiling': bool(c.get('reached_inverter_ceiling', False)),
                'reached_roof_bound': bool(c.get('reached_roof_bound', False)),
                'settings': dict(c.get('settings') or {})
            }
            if is_smaller and smaller_choice is not None:
                alt_dict['retained_profit_pct'] = retained_profit_pct
                alt_dict['sacrificed_npv_eur'] = smaller_choice.get('sacrificed_npv_eur')
                alt_dict['sacrificed_npv_pct'] = smaller_choice.get('sacrificed_npv_pct')

            top_alternatives.append(alt_dict)

    return rec_choices, top_alternatives


_SIZING_DISPATCH_MAX_ENTRIES = 2048
_SIZING_DISPATCH_MAX_BYTES = 128 * 1024 * 1024  # 128 MiB
_SIZING_DISPATCH_MEMO = ContextVar('_SIZING_DISPATCH_MEMO', default=None)


def _sizing_dispatch_lifetime(s, *args):
    """Dispatch lifetime memoized within sensitivity evaluation.

    Canonical physical inputs (pv, load, ev, pzo) and candidate physical settings
    are uniquely and deterministically determined by ENERGY_KEYS and price_fingerprint
    within this request. Scenarios modifying only capital costs or financing APR share
    identical physical dispatch across weather years. Financial evaluation, ranking,
    and optimal routing selection are still dynamically re-executed for every candidate.
    The request-local ContextVar bounds memory (max 2048 entries and 128 MiB) and
    strictly releases all cached arrays upon exit via try/finally.
    """
    memo = _SIZING_DISPATCH_MEMO.get()
    if memo is None:
        return dispatch_lifetime(*args)

    key = (_energy_key(s), price_fingerprint(s))
    entries = memo['entries']

    if key in entries:
        annual = entries.pop(key)
        entries[key] = annual
        return annual

    annual = dispatch_lifetime(*args)
    arr_bytes = annual.nbytes
    if arr_bytes > _SIZING_DISPATCH_MAX_BYTES:
        return annual

    while entries and (len(entries) >= _SIZING_DISPATCH_MAX_ENTRIES or memo['nbytes'] + arr_bytes > _SIZING_DISPATCH_MAX_BYTES):
        _, evicted = entries.popitem(last=False)
        memo['nbytes'] -= evicted.nbytes

    annual.flags.writeable = False
    entries[key] = annual
    memo['nbytes'] += arr_bytes
    return annual


def _evaluate_solar_candidate(s, opt, counts, comp_or_kwh, precomputed=None, quote=None):
    """Evaluate an exact physical solar candidate configuration under given settings and options."""
    roofs = _search_roofs()
    if _dynamic_roofs() is not None:
        from .roof_config import validate_roof_counts
        if not isinstance(counts, dict):
            counts = tuple(counts)
            if len(counts) != len(roofs):
                raise ValueError('Candidate counts must match the ordered roof dimensions')
            counts = dict(zip(roofs, counts))
        counts = tuple(n for _, n in validate_roof_counts(
            {'roofs': _dynamic_roofs()}, counts, s['panel_watts']))
    elif isinstance(counts, dict):
        counts = tuple(int(counts.get(r, 0)) for r in roofs)
    else:
        counts = tuple(int(x) for x in counts)
    opt = opt or {}
    if precomputed is None:
        if opt.get('load_scenario') and opt['load_scenario'] != s.get('load_scenario'):
            s = {**s, 'load_scenario': opt['load_scenario']}
        s = validate(s)
    financial_basis = opt.get('financial_basis', 'project')
    objective = opt.get('objective', 'npv_eur')
    ded_rate = float(opt.get('deduction_rate', s.get('deduction_rate', 0.5)))
    battery_mode = opt.get('battery_mode', 'joint')
    if battery_mode not in ('joint', 'fixed', 'keep_current'):
        battery_mode = 'joint'
    if battery_mode == 'keep_current':
        battery_mode = 'fixed'

    storage_cost_mode = opt.get('storage_cost_mode')
    if storage_cost_mode not in ('purchased', 'existing'):
        storage_cost_mode = s.get('storage_cost_mode', 'purchased')
    if storage_cost_mode not in ('purchased', 'existing'):
        storage_cost_mode = 'purchased'
    if battery_mode != 'fixed' and storage_cost_mode == 'existing':
        storage_cost_mode = 'purchased'
    panel_watts = float(s.get('panel_watts', 440.0))
    total_panels = sum(counts)
    kwp = float(round(total_panels * panel_watts / 1000.0, 3))

    q10 = opt.get('quote_10kwh_eur')
    if q10 is None and isinstance(quote, dict):
        q10 = quote.get('quote_10kwh_eur')
    q15 = opt.get('quote_15kwh_eur')
    if q15 is None and isinstance(quote, dict):
        q15 = quote.get('quote_15kwh_eur')

    if isinstance(comp_or_kwh, dict):
        comp = comp_or_kwh
        b_kwh = float(comp.get('battery_kwh', 0.0))
        total_mods = int(comp.get('battery_modules', 0))
        sec_mods = int(comp.get('battery_secondary_modules', 0))
        first_mods = int(comp.get('first_modules', total_mods - sec_mods))
        cand_mod_kwh = comp.get('battery_module_kwh', float(s.get('battery_module_kwh', 5.0)))
        cand_sec_kwh = comp.get('battery_secondary_module_kwh') or float(s.get('battery_secondary_module_kwh', 10.0))
        cand_mixed = comp.get('battery_mixed_sizes', False)
    else:
        b_kwh = float(comp_or_kwh or 0.0)
        cand_mod_kwh = float(s.get('battery_module_kwh', 5.0))
        total_mods = int(round(b_kwh / cand_mod_kwh)) if b_kwh > 0 else 0
        sec_mods = 0
        first_mods = total_mods
        cand_sec_kwh = float(s.get('battery_secondary_module_kwh', 10.0))
        cand_mixed = False

    s_cand = {
        **s,
        **_roof_settings(dict(zip(_search_roofs(), counts))),
        'battery_kwh': b_kwh,
        'battery_modules': total_mods,
        'battery_secondary_modules': sec_mods,
        'battery_module_kwh': cand_mod_kwh,
        'battery_secondary_module_kwh': cand_sec_kwh,
        'battery_mixed_sizes': cand_mixed,
        'quote_total': None,
        'deduction_rate': ded_rate,
        'storage_cost_mode': storage_cost_mode,
        **({'dormer_south_proxy': False} if _dynamic_roofs() is None else {})
    }

    if precomputed is not None:
        load = precomputed['load']
        ev = precomputed['ev']
        valid = precomputed['valid']
        pzo = precomputed['pzo']
        years = precomputed['years']
        zero_import_env = precomputed['zero_import_env']
        zero_import_median = precomputed['zero_import_median']
        zero_import_mean = float(precomputed.get('zero_import_mean', zero_import_env.get('mean', zero_import_median) if isinstance(zero_import_env, dict) else zero_import_median))
        sqrt_rte = precomputed['sqrt_rte']
        vat_mult = precomputed['vat_mult']
        prem_mult = precomputed['prem_mult']
        pv_profile_cache = precomputed.get('pv_profile_cache')
        month_src = precomputed.get('month')
        hour_src = precomputed.get('hour')
        mappings = precomputed.get('mappings')
        target_len = precomputed.get('target_len')
    else:
        inputs = _lifetime_mapped_inputs(s)
        load = inputs['load']
        ev = inputs['ev']
        valid = inputs['valid']
        pzo = inputs['pzo']
        month_src = inputs['month']
        hour_src = inputs['hour']
        mappings = inputs['mappings']
        target_len = inputs['target_len']
        years = inputs['years']
        sqrt_rte = math.sqrt(s['roundtrip_efficiency'])
        vat_mult = 1.0 + (s['installation_vat'] if not s.get('quote_vat_included', False) else 0.0)
        prem_mult = 1.0 + (s['deduction_price_premium'] if ded_rate > 0 else 0.0)
        zero_pv = np.zeros_like(load, dtype=float)
        zero_annual = dispatch_lifetime(
            zero_pv, load, ev, valid, pzo,
            s['horizon_years'], s['ev_start_year'], s['inverter_kw'],
            0.0, 0.0, 0.0, sqrt_rte,
            s['pv_first_year_loss'], s['pv_annual_fade'],
            s['battery_calendar_fade'], s['battery_cycle_fade'],
            s['battery_replace_year'], s['pv_battery_coupling']=='dc_coupled',physical_parameters(s),
            month_src, hour_src
        )
        first_zero = zero_annual[0]
        zero_import_env = envelope(first_zero[:, 5])
        zero_import_median = float(zero_import_env['median'])
        zero_import_mean = float(zero_import_env.get('mean', zero_import_median))
        pv_profile_cache = None

    def battery_capex_amount(cap_kwh):
        if cap_kwh <= 0:
            return 0.0
        if battery_mode == 'fixed' and storage_cost_mode == 'existing' and math.isclose(cap_kwh, float(s['battery_kwh']), abs_tol=1e-6):
            return 0.0
        if q10 is not None and q15 is not None:
            if cap_kwh == 10.0: return float(q10)
            if cap_kwh == 15.0: return float(q15)
            slope = (q15 - q10) / 5.0
            if cap_kwh > 15.0: return float(q15 + (cap_kwh - 15.0) * slope)
            if 10.0 < cap_kwh < 15.0: return float(q10 + (cap_kwh - 10.0) * slope)
            return float(cap_kwh * (q10 / 10.0))
        bat_cost = float(s.get('battery_cost_kwh', 0.0) or 0.0)
        return float(cap_kwh * bat_cost * prem_mult * vat_mult)

    b_capex = battery_capex_amount(b_kwh)
    if storage_cost_mode == 'existing' and battery_mode == 'fixed':
        s_cand['battery_cost_kwh'] = 0.0
        s_cand['battery_module_cost_override'] = None
        s_cand['battery_secondary_module_cost_override'] = None
        s_cand['battery_module_extra_cost'] = 0.0
        s_cand['battery_secondary_module_extra_cost'] = 0.0
        s_cand['storage_cost_mode'] = 'existing'
    elif q10 is not None and q15 is not None and b_kwh > 0:
        eff_cost = float(b_capex / (b_kwh * vat_mult * prem_mult))
        spec_bounds = SPEC['battery_cost_kwh'][2]
        spec_max = float(spec_bounds[1]) if spec_bounds and spec_bounds[1] is not None else 2000.0
        if math.isclose(eff_cost, spec_max, abs_tol=1e-6, rel_tol=1e-9):
            eff_cost = spec_max
        elif math.isclose(eff_cost, 0.0, abs_tol=1e-6):
            eff_cost = 0.0
        s_cand['battery_cost_kwh'] = eff_cost
        s_cand['battery_module_cost_override'] = None
        s_cand['battery_secondary_module_cost_override'] = None
        s_cand['battery_module_extra_cost'] = float(s.get('battery_module_extra_cost', 0.0) or 0.0)
        s_cand['battery_secondary_module_extra_cost'] = float(s.get('battery_secondary_module_extra_cost', 0.0) or 0.0)
        s_cand['storage_cost_mode'] = 'purchased'
    elif b_kwh <= 0:
        s_cand['battery_cost_kwh'] = 0.0
        s_cand['battery_module_cost_override'] = None
        s_cand['battery_secondary_module_cost_override'] = None
        s_cand['battery_module_extra_cost'] = 0.0
        s_cand['battery_secondary_module_extra_cost'] = 0.0
        s_cand['storage_cost_mode'] = storage_cost_mode
    else:
        bat_cost = float(s.get('battery_cost_kwh', 0.0) or 0.0)
        s_cand['battery_cost_kwh'] = bat_cost
        s_cand['storage_cost_mode'] = 'purchased'

    if total_panels == 0 and b_kwh == 0:
        zero_env = envelope([0.0] * len(years))
        return {
            'panel_count': 0,
            'kwp': 0.0,
            'roof_counts': {r: 0 for r in _search_roofs()},
            'battery_kwh': 0.0,
            'battery_modules': 0,
            'battery_secondary_modules': 0,
            'first_modules': 0,
            'battery_module_kwh': cand_mod_kwh,
            'battery_secondary_module_kwh': cand_sec_kwh,
            'battery_mixed_sizes': cand_mixed,
            'storage_cost_mode': storage_cost_mode,
            'capex_eur': 0.0,
            'solar_capex_eur': 0.0,
            'solar_loan_principal_eur': 0.0,
            'initial_solar_equity_eur': 0.0,
            'solar_loan_ratio': 1.0,
            'loan_principal_eur': 0.0,
            'total_loan_scope_eur': 0.0,
            'financial_basis': financial_basis,
            'npv': zero_env,
            'npv_median': 0.0,
            'npv_mean': 0.0,
            'npv_min': 0.0,
            'npv_max': 0.0,
            'npv_by_weather': [0.0] * len(years),
            'project_npv': zero_env,
            'project_npv_median': 0.0,
            'project_npv_mean': 0.0,
            'project_npv_min': 0.0,
            'project_npv_max': 0.0,
            'project_npv_by_weather': [0.0] * len(years),
            'project_discounted_payback': payback_envelope([None] * len(years)),
            'project_discounted_payback_years': None,
            'project_discounted_payback_of_mean_cash_flows': None,
            'project_simple_payback': payback_envelope([None] * len(years)),
            'project_simple_payback_years': None,
            'project_simple_payback_of_mean_cash_flows': None,
            'equity_npv': zero_env,
            'equity_npv_median': 0.0,
            'equity_npv_mean': 0.0,
            'equity_npv_min': 0.0,
            'equity_npv_max': 0.0,
            'equity_npv_by_weather': [0.0] * len(years),
            'equity_discounted_payback': payback_envelope([None] * len(years)),
            'equity_discounted_payback_years': None,
            'equity_discounted_payback_of_mean_cash_flows': None,
            'equity_simple_payback': payback_envelope([None] * len(years)),
            'equity_simple_payback_years': None,
            'equity_simple_payback_of_mean_cash_flows': None,
            'discounted_payback': payback_envelope([None] * len(years)),
            'discounted_payback_years': None,
            'discounted_payback_of_mean_cash_flows': None,
            'simple_payback': payback_envelope([None] * len(years)),
            'simple_payback_years': None,
            'simple_payback_of_mean_cash_flows': None,
            'autarky': zero_env,
            'autarky_median': 0.0,
            'autarky_mean': 0.0,
            'self_consumption': zero_env,
            'self_consumption_median': 0.0,
            'self_consumption_mean': 0.0,
            'annual_pv_kwh': zero_env,
            'annual_pv_kwh_median': 0.0,
            'annual_pv_kwh_mean': 0.0,
            'annual_pv_input_kwh_median': 0.0,
            'annual_pv_input_kwh_mean': 0.0,
            'annual_clipped_kwh_median': 0.0,
            'annual_clipped_kwh_mean': 0.0,
            'annual_clipped_pct_median': 0.0,
            'annual_clipped_pct_mean': 0.0,
            'annual_grid_import_kwh': zero_import_env,
            'annual_grid_import_kwh_median': zero_import_median,
            'annual_grid_import_kwh_mean': zero_import_mean,
            'annual_grid_export_kwh': zero_env,
            'annual_grid_export_kwh_median': 0.0,
            'annual_grid_export_kwh_mean': 0.0,
            'annual_battery_discharge_kwh': zero_env,
            'annual_battery_discharge_kwh_median': 0.0,
            'annual_battery_discharge_kwh_mean': 0.0,
            'settings': s_cand,
            'resolved_routing_priority': 'pv_only',
            'is_baseline': True
        }

    if pv_profile_cache is not None and _roof_signature(counts) in pv_profile_cache:
        pv = pv_profile_cache[_roof_signature(counts)]
    else:
        pv_raw = pv_profile(s_cand)
        if mappings is not None:
            pv = _map_pv_profile(pv_raw, mappings, target_len)
        else:
            pv = pv_raw
        if pv_profile_cache is not None:
            pv_profile_cache[_roof_signature(counts)] = pv

    cap = s['clipping_dc_max'] if b_kwh>0 and s['pv_battery_coupling'] == 'dc_coupled' else s['clipping_ac_max']
    pv_energy = np.sum(np.where(valid, pv, 0.), axis=1)
    available = np.array([pv_energy * max(0.,1-s['pv_first_year_loss']-s['pv_annual_fade']*y)
                          for y in range(s['horizon_years'])])
    # Reuse the PV/shade arrays across modes; only the EMS routing changes.
    routing_schedule_search = None
    if b_kwh > 0 and total_panels > 0 and s.get('routing_priority') == 'scheduled_hybrid':
        from .schedule_optimizer import sizing_schedule_candidates
        schedule_candidates = sizing_schedule_candidates(
            s_cand, financial_basis=financial_basis, max_candidates=6,
            context_cache=precomputed.get('schedule_context_cache') if precomputed is not None else None,
            shared_context=precomputed.get('schedule_shared_context') if precomputed is not None else None)
        routing_schedule_search = {
            'method': 'monthly_screen_then_lifetime_verification',
            'exact': False,
            'screened_space_size': 931,
            'verified_count': len(schedule_candidates),
        }
        route_options = [
            {
                'routing_priority': 'scheduled_hybrid',
                'routing_switch_hour': cand['routing_switch_hour'],
                'scheduled_start_month': cand['scheduled_start_month'],
                'scheduled_end_month': cand['scheduled_end_month'],
            }
            for cand in schedule_candidates
        ]
    else:
        if b_kwh<=0:
            route_candidates = [s['routing_priority'] if s.get('routing_priority') in ROUTING_MODES else 'battery_first']
        elif s.get('routing_priority', ROUTING_AUTO) in ROUTING_MODES:
            route_candidates = [s['routing_priority']]
        else:
            route_candidates = list(ROUTING_MODES)
        route_options = []
        for route in route_candidates:
            switch_hours = [s_cand['routing_switch_hour']]
            if b_kwh > 0 and s.get('routing_priority', ROUTING_AUTO) not in ROUTING_MODES and route == 'scheduled_hybrid':
                # Optimize the charging window independently for every PV/storage pair.
                switch_hours = sorted(set([10, 11, 12, s_cand['routing_switch_hour']]))
            for hour in switch_hours:
                route_options.append({'routing_priority': route, 'routing_switch_hour': hour})
    best = None
    for overrides in route_options:
        s_try = {**s_cand, **overrides}
        annual_try = _sizing_dispatch_lifetime(
            s_try,
            pv, load, ev, valid, pzo,
            s['horizon_years'], s['ev_start_year'], s['inverter_kw'],
            b_kwh, s['charge_kw'], s['discharge_kw'], sqrt_rte,
            s['pv_first_year_loss'], s['pv_annual_fade'],
            s['battery_calendar_fade'], s['battery_cycle_fade'],
            s['battery_replace_year'], s['pv_battery_coupling']=='dc_coupled',physical_parameters(s_try),
            month_src, hour_src
        )
        fractions_try = np.divide(annual_try[:,:,9], available, out=np.zeros_like(available), where=available>0)
        feasible_try = bool(np.all(fractions_try <= cap + 1e-9))
        fin_try, _ = _financial(s_try, annual_try, kwp, calculate_irr=False, ranking_only=True)
        selected_try = fin_try['solar_equity']['npv'] if financial_basis == 'after_financing' else fin_try['pv_incremental']['npv']
        rank = (1 if feasible_try else 0, float(selected_try['mean']))
        if best is None or rank > best['rank']:
            best = dict(rank=rank, route=overrides['routing_priority'], settings=s_try, annual=annual_try, fin=fin_try,
                        feasible=feasible_try, fractions=fractions_try, selected=selected_try)
    s_cand = best['settings']
    annual = best['annual']
    fin, _ = _financial(s_cand, annual, kwp, calculate_irr=False, candidate_only=True)
    fractions = best['fractions']
    physical_feasible = best['feasible']
    proj_npv = fin['pv_incremental']['npv']
    eq_npv = fin['solar_equity']['npv']
    selected_npv = eq_npv if financial_basis == 'after_financing' else proj_npv

    proj_disc_payback = fin['pv_incremental']['discounted_payback']
    eq_disc_payback = fin['solar_equity']['discounted_payback']
    disc_payback = eq_disc_payback if financial_basis == 'after_financing' else proj_disc_payback

    proj_simp_payback = fin['pv_incremental']['simple_payback']
    eq_simp_payback = fin['solar_equity']['simple_payback']
    simp_payback = eq_simp_payback if financial_basis == 'after_financing' else proj_simp_payback

    first = annual[0]
    produced_ac, self_consumed_pv = _pv_metric_basis(s, first)
    autarky = envelope(1 - np.divide(first[:, 5], first[:, 0], out=np.ones(len(first)), where=first[:, 0] > 0))
    self_cons = envelope(np.divide(self_consumed_pv, produced_ac, out=np.zeros(len(first)), where=produced_ac > 0))
    pv_env = envelope(produced_ac)
    pv_input_by_weather = np.sum(pv * (1.0 - s['pv_first_year_loss']), axis=1)
    clipped_by_weather = first[:, 9]
    clipped_pct_by_weather = np.divide(100.0 * clipped_by_weather, pv_input_by_weather, out=np.zeros_like(clipped_by_weather), where=pv_input_by_weather > 0)
    pv_input_env = envelope(pv_input_by_weather)
    clipped_env = envelope(clipped_by_weather)
    clipped_pct_env = envelope(clipped_pct_by_weather)
    import_env = envelope(first[:, 5])
    export_env = envelope(first[:, 6])
    discharge_env = envelope(first[:, 4])

    mean_imp = float(np.mean(first[:, 5]))
    mean_load = float(np.mean(first[:, 0]))
    autarky_mean = 1.0 - mean_imp / mean_load if mean_load > 0 else 0.0

    mean_used_pv = float(np.mean(self_consumed_pv))
    mean_usable_pv = float(np.mean(produced_ac))
    self_consumption_mean = (mean_used_pv / mean_usable_pv) if mean_usable_pv > 0 else 0.0

    mean_pv_input = float(np.mean(pv_input_by_weather))
    mean_clipped = float(np.mean(clipped_by_weather))
    annual_clipped_kwh_mean = mean_clipped
    annual_clipped_pct_mean = (100.0 * mean_clipped / mean_pv_input) if mean_pv_input > 0 else 0.0

    annual_pv_kwh_mean = mean_usable_pv
    annual_pv_input_kwh_mean = mean_pv_input
    annual_grid_import_kwh_mean = mean_imp
    annual_grid_export_kwh_mean = float(np.mean(first[:, 6]))
    annual_battery_discharge_kwh_mean = float(np.mean(first[:, 4]))
    capex = float(fin['capex_eur'])
    solar_loan_principal = float(fin['solar_loan_principal_eur'])
    initial_solar_equity = float(fin.get('initial_solar_equity_eur', capex - solar_loan_principal))
    solar_loan_ratio = float(fin['solar_loan_ratio'])
    loan_principal = float(fin['loan_principal_eur'])
    total_loan_scope = float(fin.get('total_loan_scope_eur', capex))

    cand_res = {
        'resolved_routing_priority': s_cand['routing_priority'] if b_kwh>0 else 'pv_only',
        'routing_priority': s_cand['routing_priority'],
        'routing_switch_hour': s_cand['routing_switch_hour'],
        'scheduled_start_month': s_cand.get('scheduled_start_month', s.get('scheduled_start_month', 3)),
        'scheduled_end_month': s_cand.get('scheduled_end_month', s.get('scheduled_end_month', 9)),
        'physical_feasible': physical_feasible,
        'annual_clipping_fraction_max': float(fractions.max()),
        'clipping_fraction_limit': float(cap),
        'y_dc': int(b_kwh>0 and s['pv_battery_coupling'] == 'dc_coupled'),
        'effective_pv_battery_coupling': s['pv_battery_coupling'] if b_kwh>0 else 'ac_equivalent',
        'pv_inverter_kw': float(s['inverter_kw']),
        'battery_inverter_kw': float(s['battery_inverter_kw']),
        'battery_nominal_kwh': b_kwh / (s['battery_soc_max']-s['battery_soc_min']),
        'panel_count': total_panels,
        'kwp': kwp,
        'roof_counts': {r: counts[i] for i, r in enumerate(_search_roofs())},
        'battery_kwh': b_kwh,
        'battery_modules': total_mods,
        'battery_secondary_modules': sec_mods,
        'first_modules': first_mods,
        'battery_module_kwh': cand_mod_kwh,
        'battery_secondary_module_kwh': cand_sec_kwh,
        'battery_mixed_sizes': cand_mixed,
        'storage_cost_mode': storage_cost_mode,
        'capex_eur': capex,
        'solar_capex_eur': capex,
        'solar_loan_principal_eur': solar_loan_principal,
        'initial_solar_equity_eur': initial_solar_equity,
        'solar_loan_ratio': solar_loan_ratio,
        'loan_principal_eur': loan_principal,
        'loan_amortized_principal_eur': float(fin.get('loan_amortized_principal_eur', loan_principal)),
        'loan_upfront_fees_or_tax_eur': float(fin.get('loan_upfront_fees_or_tax_eur', 0.0)),
        'total_loan_scope_eur': total_loan_scope,
        'financial_basis': financial_basis,
        'npv': selected_npv,
        'npv_median': float(selected_npv['median']),
        'npv_mean': float(selected_npv['mean']),
        'npv_min': float(selected_npv['min']),
        'npv_max': float(selected_npv['max']),
        'npv_by_weather': [float(v) for v in (fin['solar_equity']['npv_by_weather'] if financial_basis == 'after_financing' else fin['pv_incremental']['npv_by_weather'])],
        'project_npv': proj_npv,
        'project_npv_median': float(proj_npv['median']),
        'project_npv_mean': float(proj_npv['mean']),
        'project_npv_min': float(proj_npv['min']),
        'project_npv_max': float(proj_npv['max']),
        'project_npv_by_weather': [float(v) for v in fin['pv_incremental']['npv_by_weather']],
        'project_discounted_payback': proj_disc_payback,
        'project_discounted_payback_years': fin['pv_incremental']['discounted_payback_of_mean_cash_flows'],
        'project_discounted_payback_of_mean_cash_flows': fin['pv_incremental']['discounted_payback_of_mean_cash_flows'],
        'project_simple_payback': proj_simp_payback,
        'project_simple_payback_years': fin['pv_incremental']['simple_payback_of_mean_cash_flows'],
        'project_simple_payback_of_mean_cash_flows': fin['pv_incremental']['simple_payback_of_mean_cash_flows'],
        'equity_npv': eq_npv,
        'equity_npv_median': float(eq_npv['median']),
        'equity_npv_mean': float(eq_npv['mean']),
        'equity_npv_min': float(eq_npv['min']),
        'equity_npv_max': float(eq_npv['max']),
        'equity_npv_by_weather': [float(v) for v in fin['solar_equity']['npv_by_weather']],
        'equity_discounted_payback': eq_disc_payback,
        'equity_discounted_payback_years': fin['solar_equity']['discounted_payback_of_mean_cash_flows'],
        'equity_discounted_payback_of_mean_cash_flows': fin['solar_equity']['discounted_payback_of_mean_cash_flows'],
        'equity_simple_payback': eq_simp_payback,
        'equity_simple_payback_years': fin['solar_equity']['simple_payback_of_mean_cash_flows'],
        'equity_simple_payback_of_mean_cash_flows': fin['solar_equity']['simple_payback_of_mean_cash_flows'],
        'discounted_payback': disc_payback,
        'discounted_payback_years': (fin['solar_equity']['discounted_payback_of_mean_cash_flows'] if financial_basis == 'after_financing' else fin['pv_incremental']['discounted_payback_of_mean_cash_flows']),
        'discounted_payback_of_mean_cash_flows': (fin['solar_equity']['discounted_payback_of_mean_cash_flows'] if financial_basis == 'after_financing' else fin['pv_incremental']['discounted_payback_of_mean_cash_flows']),
        'simple_payback': simp_payback,
        'simple_payback_years': (fin['solar_equity']['simple_payback_of_mean_cash_flows'] if financial_basis == 'after_financing' else fin['pv_incremental']['simple_payback_of_mean_cash_flows']),
        'simple_payback_of_mean_cash_flows': (fin['solar_equity']['simple_payback_of_mean_cash_flows'] if financial_basis == 'after_financing' else fin['pv_incremental']['simple_payback_of_mean_cash_flows']),
        'autarky': autarky,
        'autarky_median': float(autarky['median']),
        'autarky_mean': autarky_mean,
        'self_consumption': self_cons,
        'self_consumption_median': float(self_cons['median']),
        'self_consumption_mean': self_consumption_mean,
        'annual_pv_kwh': pv_env,
        'annual_pv_kwh_median': float(pv_env['median']),
        'annual_pv_kwh_mean': annual_pv_kwh_mean,
        'annual_pv_input_kwh_median': float(pv_input_env['median']),
        'annual_pv_input_kwh_mean': annual_pv_input_kwh_mean,
        'annual_clipped_kwh_median': float(clipped_env['median']),
        'annual_clipped_kwh_mean': annual_clipped_kwh_mean,
        'annual_clipped_pct_median': float(clipped_pct_env['median']),
        'annual_clipped_pct_mean': annual_clipped_pct_mean,
        'annual_grid_import_kwh': import_env,
        'annual_grid_import_kwh_median': float(import_env['median']),
        'annual_grid_import_kwh_mean': annual_grid_import_kwh_mean,
        'annual_grid_export_kwh': export_env,
        'annual_grid_export_kwh_median': float(export_env['median']),
        'annual_grid_export_kwh_mean': annual_grid_export_kwh_mean,
        'annual_battery_discharge_kwh': discharge_env,
        'annual_battery_discharge_kwh_median': float(discharge_env['median']),
        'annual_battery_discharge_kwh_mean': annual_battery_discharge_kwh_mean,
        'settings': s_cand,
        'is_baseline': False
    }
    if routing_schedule_search is not None:
        cand_res['routing_schedule_search'] = routing_schedule_search
    return cand_res


@lru_cache(maxsize=16)
def _solar_sizing_cached(runtime_key, key_settings, key_options, quote_fingerprint, price_version, key_quoted=()):
    """Heavy solar array and joint storage simulation cached on settings, options and price version."""
    assert_runtime_key(runtime_key)
    s = thaw_settings(dict(key_settings))
    clean_options = dict(key_options)
    roof_max = dict(clean_options['panel_maxima'])
    quoted_scenario = thaw_settings(dict(key_quoted)) if key_quoted else None

    panel_watts = float(s['panel_watts'])
    inverter_kw = float(s['inverter_kw'])
    pv_max_kwp = _effective_pv_max_kwp(s)
    include_theoretical = bool(clean_options['include_theoretical'])
    objective = clean_options['objective']
    battery_mode = clean_options['battery_mode']
    storage_cost_mode = clean_options['storage_cost_mode']
    budget_val = int(clean_options['evaluation_budget'])
    ded_rate = float(clean_options['deduction_rate'])
    q10 = clean_options.get('quote_10kwh_eur')
    q15 = clean_options.get('quote_15kwh_eur')
    rep = clean_options['weather_representative']
    sac_pct = float(clean_options.get('npv_sacrifice_pct', clean_options.get('allowed_sacrifice_pct', 5.0)))
    cov_pct = float(clean_options.get('coverage_target_pct', clean_options.get('budget_coverage_target_pct', 70.0)))
    financial_basis = clean_options.get('financial_basis', 'project')
    retained_profit_pct = float(clean_options.get('retained_profit_pct', 95.0))

    # DC array sizing has its own explicit limit; inverter_kw remains the AC
    # conversion/clipping ceiling used by the dispatch model.
    max_array_panels = max(0, int(math.floor((pv_max_kwp * 1000.0 + 1e-6) / panel_watts)))
    max_array_kwp = float(round(max_array_panels * panel_watts / 1000.0, 3))

    # Effective roof limits: layout panel_maxima are authoritative user bounds
    effective_limits = {}
    for r in _search_roofs():
        lim = roof_max[r]
        if _dynamic_roofs() is None and not include_theoretical:
            if r in ('dormer', 'flat', 'secondary_south'):
                lim = 0
        lim = min(lim, max_array_panels)
        effective_limits[r] = lim

    max_total_panels = min(sum(effective_limits.values()), max_array_panels)

    # Validate that positive storage cannot be requested when no PV is allowed
    if max_total_panels == 0 and battery_mode == 'joint' and float(clean_options['battery_min_kwh']) > 0:
        raise ValueError('Positive storage capacity cannot be installed without solar PV panels. Increase roof limits or maximum PV array capacity, or set storage minimum to 0.')

    # Requirement 2 (Intent B): Storage step and integer battery modules
    mixed = bool(s.get('battery_mixed_sizes', False))
    module_kwh = float(s.get('battery_module_kwh', 0.0) or 0.0)
    sec_module_kwh = float(s.get('battery_secondary_module_kwh', 0.0) or 0.0)

    if battery_mode == 'fixed':
        if not mixed:
            total_m = int(s.get('battery_modules', 0 if float(s['battery_kwh']) == 0 else 1))
            sec_m = 0
            comp = calculate_battery_composition(
                total_modules=total_m, secondary_modules=0, module_kwh=module_kwh, mixed_sizes=False
            )
        else:
            if float(s['battery_kwh']) == 0 or int(s.get('battery_modules', 0)) == 0:
                total_m = 0
                sec_m = 0
            else:
                total_m = int(s['battery_modules'])
                sec_m = int(s.get('battery_secondary_modules', 0))
            comp = calculate_battery_composition(
                total_modules=total_m,
                secondary_modules=sec_m,
                module_kwh=module_kwh,
                secondary_module_kwh=sec_module_kwh,
                mixed_sizes=True,
            )
        candidate_compositions = [comp]
    else:
        b_min = float(clean_options['battery_min_kwh'])
        b_max = float(clean_options['battery_max_kwh'])
        candidate_compositions = enumerate_battery_compositions_by_capacity(
            min_kwh=b_min,
            max_kwh=b_max,
            module_kwh=module_kwh,
            secondary_module_kwh=sec_module_kwh,
            mixed_sizes=mixed,
            max_combinations=50,
        )

    candidate_modules = sorted(set(c['battery_modules'] for c in candidate_compositions))
    candidate_batteries = [c['battery_kwh'] for c in candidate_compositions]

    # Enumerate/count VALID roof tuples efficiently with sum-limited DP,
    # excluding battery-without-PV and respecting min/max storage.
    dp = [0] * (max_total_panels + 1)
    dp[0] = 1
    for r in _search_roofs():
        lim = effective_limits[r]
        if lim == 0:
            continue
        new_dp = [0] * (max_total_panels + 1)
        for s_idx in range(max_total_panels + 1):
            if dp[s_idx] > 0:
                max_add = min(lim, max_total_panels - s_idx)
                for c in range(max_add + 1):
                    new_dp[s_idx + c] += dp[s_idx]
        dp = new_dp

    valid_nonzero_roof_layouts = sum(dp[1:])
    has_zero_storage = any(math.isclose(float(c['battery_kwh']), 0.0, abs_tol=1e-6) for c in candidate_compositions)
    # 0 panels only valid with 0 kWh storage; positive panel counts valid with all candidate compositions
    topology_mode = clean_options.get('topology_mode', 'fixed')
    topologies = ('ac_equivalent', 'dc_coupled') if topology_mode == 'auto' else (s['pv_battery_coupling'] if topology_mode == 'fixed' else topology_mode,)
    valid_candidates_count = valid_nonzero_roof_layouts * len(candidate_compositions) + (1 if has_zero_storage else 0)

    valid_candidates_count *= len(topologies)
    exact = (valid_candidates_count <= SOLAR_EXHAUSTIVE_LIMIT)
    budget = valid_candidates_count if exact else min(budget_val, valid_candidates_count)

    inputs = _lifetime_mapped_inputs(s)
    load = inputs['load']
    ev = inputs['ev']
    valid = inputs['valid']
    pzo = inputs['pzo']
    month_src = inputs['month']
    hour_src = inputs['hour']
    mappings = inputs['mappings']
    target_len = inputs['target_len']
    years = inputs['years']

    sqrt_rte = math.sqrt(s['roundtrip_efficiency'])
    vat_mult = 1.0 + (s['installation_vat'] if not s['quote_vat_included'] else 0.0)
    prem_mult = 1.0 + (s['deduction_price_premium'] if ded_rate > 0 else 0.0)

    # Requirement 3 (Intent C): Modeled production and descending roof priority
    # Derive per-roof yield using the exact single-roof pv_profile calculation as candidate dispatch,
    # with dormer_south_proxy=False matching actual candidate production assumptions.
    roof_production_kwh_kwp = {}
    kwp_single = panel_watts / 1000.0
    for r in _search_roofs():
        if _dynamic_roofs() is not None and _search_maxima(s)[r] == 0:
            roof_production_kwh_kwp[r] = 0.0
            continue
        s_single = {
            **s,
            **_roof_settings({k: int(k == r) for k in _search_roofs()}),
            **({'dormer_south_proxy': False} if _dynamic_roofs() is None else {})
        }
        pv_single = pv_profile(s_single)
        mapped_pv_single = _map_pv_profile(pv_single, mappings, target_len)
        annual_kwh_kwp = mapped_pv_single.sum(axis=1) / kwp_single
        roof_production_kwh_kwp[r] = float(round(float(np.median(annual_kwh_kwp)), 2))

    yield_priority = sorted(
        _search_roofs(),
        key=lambda r: (-roof_production_kwh_kwp[r], _search_roofs().index(r))
    )

    # Compute genuine zero-PV, zero-storage baseline dispatch across all historical weather analogues
    zero_pv = np.zeros_like(load, dtype=float)
    zero_annual = dispatch_lifetime(
        zero_pv, load, ev, valid, pzo,
        s['horizon_years'], s['ev_start_year'], s['inverter_kw'],
        0.0, 0.0, 0.0, sqrt_rte,
        s['pv_first_year_loss'], s['pv_annual_fade'],
        s['battery_calendar_fade'], s['battery_cycle_fade'],
        s['battery_replace_year'], s['pv_battery_coupling']=='dc_coupled',physical_parameters(s),
        month_src, hour_src
    )
    first_zero = zero_annual[0]
    zero_import_env = envelope(first_zero[:, 5])
    zero_import_median = float(zero_import_env['median'])
    zero_import_mean = float(zero_import_env.get('mean', zero_import_median))

    def battery_capex_amount(cap_kwh):
        if cap_kwh <= 0:
            return 0.0
        if battery_mode == 'fixed' and storage_cost_mode == 'existing' and math.isclose(cap_kwh, float(s['battery_kwh']), abs_tol=1e-6):
            return 0.0
        if q10 is not None and q15 is not None:
            if cap_kwh == 10.0: return float(q10)
            if cap_kwh == 15.0: return float(q15)
            slope = (q15 - q10) / 5.0
            if cap_kwh > 15.0: return float(q15 + (cap_kwh - 15.0) * slope)
            if 10.0 < cap_kwh < 15.0: return float(q10 + (cap_kwh - 10.0) * slope)
            return float(cap_kwh * (q10 / 10.0))
        bat_cost = float(s.get('battery_cost_kwh', 0.0) or 0.0)
        return float(cap_kwh * bat_cost * prem_mult * vat_mult)
    evaluated = {}
    pv_profile_cache = {}
    best_candidate = None
    best_key = None

    precomputed = {
        'load': load,
        'ev': ev,
        'valid': valid,
        'pzo': pzo,
        'month': month_src,
        'hour': hour_src,
        'mappings': mappings,
        'target_len': target_len,
        'years': years,
        'zero_import_env': zero_import_env,
        'zero_import_median': zero_import_median,
        'zero_import_mean': zero_import_mean,
        'sqrt_rte': sqrt_rte,
        'vat_mult': vat_mult,
        'prem_mult': prem_mult,
        'pv_profile_cache': pv_profile_cache,
        'schedule_context_cache': {},
        'schedule_shared_context': {},
    }

    attempted = set()
    schedule_search_performed = False
    def eval_candidate(counts, comp_or_kwh):
        results = [eval_topology(counts, comp_or_kwh, topology) for topology in topologies]
        return max((c for c in results if c is not None), key=lambda c: c['tie_key'], default=None)

    def eval_topology(counts, comp_or_kwh, topology):
        nonlocal best_candidate, best_key, schedule_search_performed
        counts = tuple(int(x) for x in counts)

        if isinstance(comp_or_kwh, dict):
            comp = comp_or_kwh
            b_kwh = float(comp['battery_kwh'])
        else:
            b_val = float(comp_or_kwh)
            matching = [c for c in candidate_compositions if math.isclose(c['battery_kwh'], b_val, abs_tol=1e-6)]
            if not matching:
                return None
            comp = matching[0]
            b_kwh = float(comp['battery_kwh'])

        if battery_mode == 'joint':
            b_min_val = float(clean_options['battery_min_kwh'])
            b_max_val = float(clean_options['battery_max_kwh'])
            if b_kwh < b_min_val - 1e-6 or b_kwh > b_max_val + 1e-6:
                return None

        total_mods = comp['battery_modules']
        sec_mods = comp.get('battery_secondary_modules', 0)
        cand_mixed = comp.get('battery_mixed_sizes', mixed)

        if not cand_mixed:
            sig = _roof_signature(counts) + (b_kwh,)
        else:
            sig = _roof_signature(counts) + (total_mods, sec_mods)

        sig = sig + (topology,)
        if sig in evaluated:
            return evaluated[sig]
        if sig in attempted or len(attempted) >= budget:
            return None

        for i, r in enumerate(_search_roofs()):
            if counts[i] < 0 or counts[i] > effective_limits[r]:
                return None
        total_panels = sum(counts)
        if total_panels > max_total_panels:
            return None
        kwp = float(round(total_panels * panel_watts / 1000.0, 3))
        if kwp > pv_max_kwp + 1e-6:
            return None
        if total_panels == 0 and b_kwh > 0:
            return None

        ilr = s['ilr_dc_max'] if topology == 'dc_coupled' and b_kwh>0 else s['ilr_ac_max']
        if kwp > inverter_kw * ilr + 1e-9:
            attempted.add(sig)
            return None
        attempted.add(sig)
        candidate_settings = {**s, 'pv_battery_coupling': topology,
                              'battery_inverter_kw': 0. if topology == 'dc_coupled' else s['battery_inverter_kw']}
        cand = _evaluate_solar_candidate(candidate_settings, clean_options, counts, comp, precomputed=precomputed)
        if cand.get('routing_schedule_search'):
            schedule_search_performed = True
        if not cand.get('physical_feasible', True):
            return None

        if total_panels == 0 and b_kwh == 0:
            cand['score'] = 0.0 if objective not in ('payback_years', 'discounted_payback_years') else 1e12
        else:
            if objective == 'npv_eur': score = float(cand['npv']['mean'])
            elif objective == 'autarky': score = float(cand.get('autarky_mean', cand['autarky']['median']))
            elif objective == 'self_consumption': score = float(cand.get('self_consumption_mean', cand['self_consumption']['median']))
            elif objective == 'payback_years':
                val = cand.get('simple_payback_of_mean_cash_flows', cand.get('simple_payback_years'))
                score = float(val) if val is not None else 1e12
            elif objective == 'discounted_payback_years':
                val = cand.get('discounted_payback_of_mean_cash_flows', cand.get('discounted_payback_years'))
                score = float(val) if val is not None else 1e12
            cand['score'] = score

        sort_score = cand['score'] if objective not in ('payback_years', 'discounted_payback_years') else -cand['score']
        capex_val = cand['capex_eur']
        yield_counts = tuple(counts[_search_roofs().index(r)] for r in yield_priority)
        tie = (
            sort_score,
            -capex_val,
            -total_panels,
            -b_kwh,
            -total_mods,
            -sec_mods,
            yield_counts
        )
        cand['tie_key'] = tie
        cand['_composition'] = comp
        evaluated[sig] = cand

        if best_key is None or tie > best_key:
            best_key = tie
            best_candidate = cand

        return cand

    # Run search
    seeding_truncated = False
    if exact:
        def generate_valid_roof_layouts():
            active_roofs = [r for r in _search_roofs() if effective_limits[r] > 0]
            if not active_roofs:
                yield tuple(0 for _ in _search_roofs())
                return

            def _rec(idx, rem):
                if idx == len(active_roofs):
                    yield ()
                    return
                r = active_roofs[idx]
                lim = min(effective_limits[r], rem)
                for c in range(lim + 1):
                    for rest in _rec(idx + 1, rem - c):
                        yield (c,) + rest

            for counts_active in _rec(0, max_total_panels):
                full_counts = [0] * len(_search_roofs())
                for i, r in enumerate(active_roofs):
                    full_counts[_search_roofs().index(r)] = counts_active[i]
                yield tuple(full_counts)

        zero_comp = next((c for c in candidate_compositions if math.isclose(c['battery_kwh'], 0.0, abs_tol=1e-6)), None)
        for counts in generate_valid_roof_layouts():
            total_p = sum(counts)
            if total_p == 0:
                if zero_comp:
                    eval_candidate(counts, zero_comp)
            else:
                for comp in candidate_compositions:
                    eval_candidate(counts, comp)
    else:
        zero_comp = next((c for c in candidate_compositions if math.isclose(c['battery_kwh'], 0.0, abs_tol=1e-6)), None)
        if zero_comp:
            eval_candidate(tuple(0 for _ in _search_roofs()), zero_comp)

        curr_counts = tuple(min(_panel_counts(s)[r], effective_limits[r]) for r in _search_roofs())
        if 0 < sum(curr_counts) <= max_total_panels:
            for comp in candidate_compositions:
                eval_candidate(curr_counts, comp)
                if len(attempted) >= budget: break

        max_counts = tuple(effective_limits[r] for r in _search_roofs())
        if 0 < sum(max_counts) <= max_total_panels:
            for comp in candidate_compositions:
                eval_candidate(max_counts, comp)
                if len(attempted) >= budget: break

        def greedy_alloc(n_panels):
            alloc = {r: 0 for r in _search_roofs()}
            rem = n_panels
            for r in yield_priority:
                lim = effective_limits[r]
                take = min(rem, lim)
                alloc[r] = take
                rem -= take
                if rem <= 0: break
            return tuple(alloc[r] for r in _search_roofs())

        def balanced_alloc(n_panels):
            alloc = {r: 0 for r in _search_roofs()}
            rem = n_panels
            top_r = yield_priority[0]
            take_top = min(rem, effective_limits[top_r])
            alloc[top_r] = take_top
            rem -= take_top
            for r in yield_priority[1:]:
                if rem <= 0: break
                avail = effective_limits[r] - alloc[r]
                if avail > 0:
                    take = min(rem, avail)
                    alloc[r] += take
                    rem -= take
            return tuple(alloc[r] for r in _search_roofs())

        curr_total = sum(curr_counts)
        def scaled_current_alloc(n_panels):
            if curr_total <= 0: return greedy_alloc(n_panels)
            alloc = {r: 0 for r in _search_roofs()}
            for r in _search_roofs():
                take = min(int(round(curr_counts[_search_roofs().index(r)] * n_panels / curr_total)), effective_limits[r])
                alloc[r] = take
            curr_sum = sum(alloc.values())
            diff = n_panels - curr_sum
            for r in yield_priority:
                if diff == 0: break
                if diff > 0:
                    avail = effective_limits[r] - alloc[r]
                    take = min(diff, avail)
                    alloc[r] += take
                    diff -= take
                elif diff < 0 and alloc[r] > 0:
                    drop = min(-diff, alloc[r])
                    alloc[r] -= drop
                    diff += drop
            return tuple(alloc[r] for r in _search_roofs())

        # Seeding phase: reserve a portion of budget (approx 65%) for diverse layout seeds,
        # evaluating ALL candidate battery compositions for each sampled layout to ensure
        # both boundary (max/min), intermediate, and smaller arrays with matched batteries are represented.
        seed_budget = int(budget * 0.65) if budget > 50 else budget

        seed_ns = []
        if max_total_panels > 0:
            for boundary in [max_total_panels, 1]:
                if boundary not in seed_ns and 1 <= boundary <= max_total_panels:
                    seed_ns.append(boundary)
            for frac in [0.5, 0.75, 0.25, 0.6, 0.4, 0.8, 0.3, 0.9, 0.1]:
                pt = int(round(max_total_panels * frac))
                if 1 <= pt <= max_total_panels and pt not in seed_ns:
                    seed_ns.append(pt)
            for n in range(1, max_total_panels + 1):
                if n not in seed_ns:
                    seed_ns.append(n)

        sampled_seed_ns = set()
        sampled_layouts_count = 0
        seeding_truncated = False

        for n in seed_ns:
            if len(attempted) >= seed_budget:
                seeding_truncated = True
                break
            layouts_n = []
            g_layout = greedy_alloc(n)
            layouts_n.append(g_layout)

            # Ensure pure south-only system is explicitly evaluated if south roof has capacity
            if _dynamic_roofs() is None and effective_limits.get('south', 0) >= n:
                s_layout = tuple(n if r == 'south' else 0 for r in _search_roofs())
                if s_layout not in layouts_n:
                    layouts_n.append(s_layout)

            b_layout = balanced_alloc(n)
            if b_layout not in layouts_n:
                layouts_n.append(b_layout)

            sc_layout = scaled_current_alloc(n)
            if sc_layout not in layouts_n:
                layouts_n.append(sc_layout)

            for layout in layouts_n:
                if len(attempted) >= seed_budget:
                    seeding_truncated = True
                    break
                rem_seed = seed_budget - len(attempted)
                if rem_seed < len(candidate_compositions) and len(attempted) >= 20:
                    seeding_truncated = True
                    break
                sampled_layouts_count += 1
                sampled_seed_ns.add(n)
                # Evaluate ALL candidate compositions for this layout
                for comp in candidate_compositions:
                    eval_candidate(layout, comp)
            if seeding_truncated:
                break

        # Multi-objective refinement loop:
        # Independent attention to:
        # 1. financial: max median selected NPV
        # 2. balanced: max autarky with selected NPV >= (1 - sac_pct/100) * best_npv
        # 3. budget: min capex with positive selected NPV and autarky >= coverage_target
        # 4. smaller: min capex retaining retained_profit_pct of best positive selected NPV
        refinement_iters = 0
        while len(attempted) < budget:
            refinement_iters += 1
            before_len = len(attempted)
            rec_incumbents, _ = select_solar_recommendations(
                evaluated.values(),
                yield_priority,
                {**clean_options, 'exact': False}
            )

            incumbent_targets = []
            seen_inc_sigs = set()

            for role_name in ('financial', 'balanced', 'budget', 'smaller'):
                c = rec_incumbents.get(role_name)
                if c is not None and c.get('panel_count', 0) > 0:
                    sig = (c['panel_count'], round(float(c.get('battery_kwh', 0.0)), 6), tuple((r, c['roof_counts'][r]) for r in _search_roofs()))
                    if sig not in seen_inc_sigs:
                        seen_inc_sigs.add(sig)
                        incumbent_targets.append((c, role_name))

            if not incumbent_targets:
                break

            rem = budget - len(attempted)
            budget_per_role = max(6, rem // len(incumbent_targets))

            for inc_c, role_name in incumbent_targets:
                if len(attempted) >= budget:
                    break
                role_start_len = len(attempted)
                inc_counts = tuple(inc_c['roof_counts'][r] for r in _search_roofs())
                inc_comp = inc_c.get('_composition')

                # Refine battery storage for this incumbent layout across all battery compositions
                for comp in candidate_compositions:
                    eval_candidate(inc_counts, comp)
                    if len(attempted) >= budget or (len(attempted) - role_start_len) >= budget_per_role:
                        break

                # Refine roof allocations (delta ±1, ±2 on active roofs)
                for i, r in enumerate(_search_roofs()):
                    if effective_limits[r] == 0:
                        continue
                    for delta in [-1, 1, -2, 2]:
                        nv = inc_counts[i] + delta
                        if 0 <= nv <= effective_limits[r]:
                            trial = list(inc_counts)
                            trial[i] = nv
                            if 0 < sum(trial) <= max_total_panels:
                                eval_candidate(tuple(trial), inc_comp)
                                if len(attempted) >= budget or (len(attempted) - role_start_len) >= budget_per_role:
                                    break
                    if len(attempted) >= budget or (len(attempted) - role_start_len) >= budget_per_role:
                        break

                # Refine roof transfers (move 1 panel between roofs)
                for i in range(len(_search_roofs())):
                    if inc_counts[i] <= 0:
                        continue
                    for j in range(len(_search_roofs())):
                        if i == j or effective_limits[_search_roofs()[j]] == 0:
                            continue
                        if inc_counts[j] + 1 <= effective_limits[_search_roofs()[j]]:
                            trial = list(inc_counts)
                            trial[i] -= 1
                            trial[j] += 1
                            if 0 < sum(trial) <= max_total_panels:
                                eval_candidate(tuple(trial), inc_comp)
                                if len(attempted) >= budget or (len(attempted) - role_start_len) >= budget_per_role:
                                    break
                    if len(attempted) >= budget or (len(attempted) - role_start_len) >= budget_per_role:
                        break

            if len(attempted) == before_len:
                break

    layout_exact = exact
    schedule_optimized = schedule_search_performed
    if schedule_optimized:
        exact = False

    physical_constraints = {'topology_mode': topology_mode, 'ilr_ac_max': s['ilr_ac_max'], 'ilr_dc_max': s['ilr_dc_max'], 'clipping_ac_max': s['clipping_ac_max'], 'clipping_dc_max': s['clipping_dc_max'], 'evaluated_including_rejected': len(attempted), 'rejected': len(attempted)-len(evaluated)}

    basis_label = 'equity NPV' if financial_basis == 'after_financing' else 'project NPV'
    objective_labels = {
        'npv_eur': f'Maximize mean discounted incremental {basis_label}',
        'autarky': 'Maximize median self-sufficiency / autarky',
        'self_consumption': 'Maximize median self-consumption',
        'payback_years': 'Minimize simple payback',
        'discounted_payback_years': 'Minimize discounted payback'
    }

    # Evaluate current baseline comparisons
    curr_modeled_settings = {**s, 'quote_total': None, 'deduction_rate': ded_rate,
                             **({'dormer_south_proxy': False} if _dynamic_roofs() is None else {})}
    curr_eval = evaluate(curr_modeled_settings, detail=False)
    curr_fin = curr_eval['finance']
    curr_proj_npv = curr_fin['pv_incremental']['npv']
    curr_eq_npv = curr_fin['solar_equity']['npv']
    curr_selected_npv = curr_eq_npv if financial_basis == 'after_financing' else curr_proj_npv
    curr_capex = float(curr_fin['capex_eur'])
    curr_loan_principal = float(curr_fin.get('loan_principal_eur', 0.0))
    curr_solar_loan_principal = float(curr_fin.get('solar_loan_principal_eur', 0.0))
    curr_initial_solar_equity = float(curr_fin.get('initial_solar_equity_eur', curr_capex))
    curr_solar_loan_ratio = float(curr_fin.get('solar_loan_ratio', 1.0))
    curr_total_loan_scope = float(curr_fin.get('total_loan_scope_eur', curr_capex))

    def _eval_ensemble_flows(eval_res, settings_dict):
        if eval_res.get('headline_energy_flows'):
            hl = eval_res['headline_energy_flows']
            pv_m = float(hl['pv_generation_kwh'])
            used_m = float(hl['direct_use_kwh'] + hl['battery_charge_kwh'])
            sc_m = float(used_m / pv_m) if pv_m > 0 else 0.0
            return {
                'autarky_mean': float(hl['autarky']),
                'self_consumption_mean': sc_m,
                'annual_pv_kwh_mean': pv_m,
                'annual_grid_import_kwh_mean': float(hl['grid_import_kwh']),
                'annual_clipped_kwh_mean': float(hl['clipped_kwh']),
                'annual_clipped_pct_mean': float(hl['clipped_pct'])
            }
        eng = eval_res['energy']
        pv_m = float(eng['pv_kwh']['mean'])
        imp_m = float(eng['grid_import_kwh']['mean'])
        load_m = float(eng['load_kwh']['mean'])
        aut_m = float(eng.get('ensemble_autarky', 1.0 - imp_m / load_m if load_m > 0 else 0.0))
        eta = float(settings_dict.get('inverter_efficiency', 1.0)) if (settings_dict.get('pv_dispatch_model') == 'physical' and settings_dict.get('pv_battery_coupling') == 'dc_coupled') else 1.0
        used_m = float(eng['direct_kwh']['mean']) + float(eng['charge_kwh']['mean']) * eta
        sc_m = float(used_m / pv_m) if pv_m > 0 else 0.0
        clip_m = float(eng['clipped_kwh']['mean'])
        pv_in_m = float(np.mean(np.sum(pv_profile(settings_dict) *
                                      (1.0 - settings_dict['pv_first_year_loss']), axis=1)))
        clip_pct_m = float(100.0 * clip_m / pv_in_m) if pv_in_m > 0 else 0.0
        return {
            'autarky_mean': aut_m,
            'self_consumption_mean': sc_m,
            'annual_pv_kwh_mean': pv_m,
            'annual_grid_import_kwh_mean': imp_m,
            'annual_clipped_kwh_mean': clip_m,
            'annual_clipped_pct_mean': clip_pct_m
        }

    curr_flows = _eval_ensemble_flows(curr_eval, s)

    curr_comp = {
        'id': 'current',
        **({'settings': curr_eval['settings']} if _dynamic_roofs() is not None else {}),
        'label': f"Current layout (modeled benchmark · {ded_rate*100:.0f}% deduction)",
        'panel_count': sum(_panel_counts(s)[r] for r in _search_roofs()),
        'kwp': float(round(sum(_panel_counts(s)[r] for r in _search_roofs()) * panel_watts / 1000.0, 3)),
        'roof_counts': {r: _panel_counts(s)[r] for r in _search_roofs()},
        'battery_kwh': float(s['battery_kwh']),
        'battery_modules': int(s.get('battery_modules', 0)),
        'battery_secondary_modules': int(s.get('battery_secondary_modules', 0)),
        'battery_module_kwh': float(s.get('battery_module_kwh', 0.0)),
        'battery_secondary_module_kwh': float(s.get('battery_secondary_module_kwh', 0.0)),
        'battery_mixed_sizes': bool(s.get('battery_mixed_sizes', False)),
        'capex_eur': curr_capex,
        'solar_capex_eur': curr_capex,
        'solar_loan_principal_eur': curr_solar_loan_principal,
        'initial_solar_equity_eur': curr_initial_solar_equity,
        'solar_loan_ratio': curr_solar_loan_ratio,
        'loan_principal_eur': curr_loan_principal,
        'total_loan_scope_eur': curr_total_loan_scope,
        'financial_basis': financial_basis,
        'npv': curr_selected_npv,
        'npv_median': float(curr_selected_npv['median']),
        'npv_mean': float(curr_selected_npv['mean']),
        'npv_min': float(curr_selected_npv['min']),
        'npv_max': float(curr_selected_npv['max']),
        'project_npv': curr_proj_npv,
        'project_npv_median': float(curr_proj_npv['median']),
        'project_npv_mean': float(curr_proj_npv['mean']),
        'project_npv_min': float(curr_proj_npv['min']),
        'project_npv_max': float(curr_proj_npv['max']),
        'equity_npv': curr_eq_npv,
        'equity_npv_median': float(curr_eq_npv['median']),
        'equity_npv_mean': float(curr_eq_npv['mean']),
        'equity_npv_min': float(curr_eq_npv['min']),
        'equity_npv_max': float(curr_eq_npv['max']),
        'discounted_payback_years': curr_fin['solar_equity']['discounted_payback_of_mean_cash_flows'] if financial_basis == 'after_financing' else curr_fin['pv_incremental']['discounted_payback_of_mean_cash_flows'],
        'simple_payback_years': curr_fin['solar_equity']['simple_payback_of_mean_cash_flows'] if financial_basis == 'after_financing' else curr_fin['pv_incremental']['simple_payback_of_mean_cash_flows'],
        'project_discounted_payback_years': curr_fin['pv_incremental']['discounted_payback_of_mean_cash_flows'],
        'project_simple_payback_years': curr_fin['pv_incremental']['simple_payback_of_mean_cash_flows'],
        'equity_discounted_payback_years': curr_fin['solar_equity']['discounted_payback_of_mean_cash_flows'],
        'equity_simple_payback_years': curr_fin['solar_equity']['simple_payback_of_mean_cash_flows'],
        'discounted_payback_of_mean_cash_flows': curr_fin['solar_equity']['discounted_payback_of_mean_cash_flows'] if financial_basis == 'after_financing' else curr_fin['pv_incremental']['discounted_payback_of_mean_cash_flows'],
        'simple_payback_of_mean_cash_flows': curr_fin['solar_equity']['simple_payback_of_mean_cash_flows'] if financial_basis == 'after_financing' else curr_fin['pv_incremental']['simple_payback_of_mean_cash_flows'],
        'project_discounted_payback_of_mean_cash_flows': curr_fin['pv_incremental']['discounted_payback_of_mean_cash_flows'],
        'project_simple_payback_of_mean_cash_flows': curr_fin['pv_incremental']['simple_payback_of_mean_cash_flows'],
        'equity_discounted_payback_of_mean_cash_flows': curr_fin['solar_equity']['discounted_payback_of_mean_cash_flows'],
        'equity_simple_payback_of_mean_cash_flows': curr_fin['solar_equity']['simple_payback_of_mean_cash_flows'],
        'autarky_median': float(curr_eval['metrics']['autarky']['median']),
        'autarky_mean': curr_flows['autarky_mean'],
        'self_consumption_median': float(curr_eval['metrics']['self_consumption']['median']),
        'self_consumption_mean': curr_flows['self_consumption_mean'],
        'annual_pv_kwh_median': float(curr_eval['metrics']['pv_kwh']['median']),
        'annual_pv_kwh_mean': curr_flows['annual_pv_kwh_mean'],
        'annual_grid_import_kwh_median': float(curr_eval['metrics']['grid_import_kwh']['median']),
        'annual_grid_import_kwh_mean': curr_flows['annual_grid_import_kwh_mean'],
        'annual_clipped_kwh_mean': curr_flows['annual_clipped_kwh_mean'],
        'annual_clipped_pct_mean': curr_flows['annual_clipped_pct_mean'],
        'cost_basis': 'modeled_benchmark',
        'deduction_rate': float(ded_rate)
    }

    curr_exceeds = curr_comp['kwp'] > pv_max_kwp + 1e-6
    curr_comp['exceeds_inverter_ceiling'] = curr_exceeds
    if curr_exceeds:
        curr_comp['inverter_ceiling_note'] = f"Exceeds selected PV array ceiling ({pv_max_kwp:.2f} kWp)"
    curr_comp['exceeds_pv_ceiling'] = curr_exceeds

    quoted_comp = None
    if quoted_scenario is not None:
        try:
            curr_quoted_eval = evaluate(quoted_scenario, detail=False)
            curr_quoted_fin = curr_quoted_eval['finance']
            curr_quoted_proj_npv = curr_quoted_fin['pv_incremental']['npv']
            curr_quoted_eq_npv = curr_quoted_fin['solar_equity']['npv']
            curr_quoted_selected_npv = curr_quoted_eq_npv if financial_basis == 'after_financing' else curr_quoted_proj_npv
            curr_quoted_capex = float(curr_quoted_fin['capex_eur'])
            curr_quoted_loan_principal = float(curr_quoted_fin.get('loan_principal_eur', 0.0))
            curr_quoted_solar_loan_principal = float(curr_quoted_fin.get('solar_loan_principal_eur', 0.0))
            curr_quoted_initial_solar_equity = float(curr_quoted_fin.get('initial_solar_equity_eur', curr_quoted_capex))
            curr_quoted_solar_loan_ratio = float(curr_quoted_fin.get('solar_loan_ratio', 1.0))
            curr_quoted_total_loan_scope = float(curr_quoted_fin.get('total_loan_scope_eur', curr_quoted_capex))

            quoted_flows = _eval_ensemble_flows(curr_quoted_eval, quoted_scenario)

            quoted_comp = {
                'id': 'current_quoted',
                **({'settings': curr_quoted_eval['settings']} if _dynamic_roofs() is not None else {}),
                'label': f"Current scenario (contractor quote · €{curr_quoted_fin['capex_eur']:,.0f})",
                'panel_count': sum(_panel_counts(quoted_scenario)[r] for r in _search_roofs()),
                'kwp': float(round(sum(_panel_counts(quoted_scenario)[r] for r in _search_roofs()) * panel_watts / 1000.0, 3)),
                'roof_counts': {r: _panel_counts(quoted_scenario)[r] for r in _search_roofs()},
                'battery_kwh': float(quoted_scenario['battery_kwh']),
                'battery_modules': int(quoted_scenario.get('battery_modules', 0)),
                'battery_secondary_modules': int(quoted_scenario.get('battery_secondary_modules', 0)),
                'battery_module_kwh': float(quoted_scenario.get('battery_module_kwh', 0.0)),
                'battery_secondary_module_kwh': float(quoted_scenario.get('battery_secondary_module_kwh', 0.0)),
                'battery_mixed_sizes': bool(quoted_scenario.get('battery_mixed_sizes', False)),
                'capex_eur': curr_quoted_capex,
                'solar_capex_eur': curr_quoted_capex,
                'solar_loan_principal_eur': curr_quoted_solar_loan_principal,
                'initial_solar_equity_eur': curr_quoted_initial_solar_equity,
                'solar_loan_ratio': curr_quoted_solar_loan_ratio,
                'loan_principal_eur': curr_quoted_loan_principal,
                'total_loan_scope_eur': curr_quoted_total_loan_scope,
                'financial_basis': financial_basis,
                'npv': curr_quoted_selected_npv,
                'npv_median': float(curr_quoted_selected_npv['median']),
                'npv_mean': float(curr_quoted_selected_npv['mean']),
                'npv_min': float(curr_quoted_selected_npv['min']),
                'npv_max': float(curr_quoted_selected_npv['max']),
                'project_npv': curr_quoted_proj_npv,
                'project_npv_median': float(curr_quoted_proj_npv['median']),
                'project_npv_mean': float(curr_quoted_proj_npv['mean']),
                'project_npv_min': float(curr_quoted_proj_npv['min']),
                'project_npv_max': float(curr_quoted_proj_npv['max']),
                'equity_npv': curr_quoted_eq_npv,
                'equity_npv_median': float(curr_quoted_eq_npv['median']),
                'equity_npv_mean': float(curr_quoted_eq_npv['mean']),
                'equity_npv_min': float(curr_quoted_eq_npv['min']),
                'equity_npv_max': float(curr_quoted_eq_npv['max']),
                'discounted_payback_years': curr_quoted_fin['solar_equity']['discounted_payback_of_mean_cash_flows'] if financial_basis == 'after_financing' else curr_quoted_fin['pv_incremental']['discounted_payback_of_mean_cash_flows'],
                'simple_payback_years': curr_quoted_fin['solar_equity']['simple_payback_of_mean_cash_flows'] if financial_basis == 'after_financing' else curr_quoted_fin['pv_incremental']['simple_payback_of_mean_cash_flows'],
                'project_discounted_payback_years': curr_quoted_fin['pv_incremental']['discounted_payback_of_mean_cash_flows'],
                'project_simple_payback_years': curr_quoted_fin['pv_incremental']['simple_payback_of_mean_cash_flows'],
                'equity_discounted_payback_years': curr_quoted_fin['solar_equity']['discounted_payback_of_mean_cash_flows'],
                'equity_simple_payback_years': curr_quoted_fin['solar_equity']['simple_payback_of_mean_cash_flows'],
                'discounted_payback_of_mean_cash_flows': curr_quoted_fin['solar_equity']['discounted_payback_of_mean_cash_flows'] if financial_basis == 'after_financing' else curr_quoted_fin['pv_incremental']['discounted_payback_of_mean_cash_flows'],
                'simple_payback_of_mean_cash_flows': curr_quoted_fin['solar_equity']['simple_payback_of_mean_cash_flows'] if financial_basis == 'after_financing' else curr_quoted_fin['pv_incremental']['simple_payback_of_mean_cash_flows'],
                'project_discounted_payback_of_mean_cash_flows': curr_quoted_fin['pv_incremental']['discounted_payback_of_mean_cash_flows'],
                'project_simple_payback_of_mean_cash_flows': curr_quoted_fin['pv_incremental']['simple_payback_of_mean_cash_flows'],
                'equity_discounted_payback_of_mean_cash_flows': curr_quoted_fin['solar_equity']['discounted_payback_of_mean_cash_flows'],
                'equity_simple_payback_of_mean_cash_flows': curr_quoted_fin['solar_equity']['simple_payback_of_mean_cash_flows'],
                'autarky_median': float(curr_quoted_eval['metrics']['autarky']['median']),
                'autarky_mean': quoted_flows['autarky_mean'],
                'self_consumption_median': float(curr_quoted_eval['metrics']['self_consumption']['median']),
                'self_consumption_mean': quoted_flows['self_consumption_mean'],
                'annual_pv_kwh_median': float(curr_quoted_eval['metrics']['pv_kwh']['median']),
                'annual_pv_kwh_mean': quoted_flows['annual_pv_kwh_mean'],
                'annual_grid_import_kwh_median': float(curr_quoted_eval['metrics']['grid_import_kwh']['median']),
                'annual_grid_import_kwh_mean': quoted_flows['annual_grid_import_kwh_mean'],
                'annual_clipped_kwh_mean': quoted_flows['annual_clipped_kwh_mean'],
                'annual_clipped_pct_mean': quoted_flows['annual_clipped_pct_mean'],
                'cost_basis': 'contractor_quote',
                'deduction_rate': float(quoted_scenario.get('deduction_rate', s['deduction_rate']))
            }
            quoted_exceeds = quoted_comp['kwp'] > pv_max_kwp + 1e-6
            quoted_comp['exceeds_inverter_ceiling'] = quoted_exceeds
            if quoted_exceeds:
                quoted_comp['inverter_ceiling_note'] = f"Exceeds selected PV array ceiling ({pv_max_kwp:.2f} kWp)"
            quoted_comp['exceeds_pv_ceiling'] = quoted_exceeds
        except Exception:
            quoted_comp = None

    routing_basis = 'solar_equity' if financial_basis == 'after_financing' else 'pv_incremental'
    forecast_key = _energy_key(_routing_settings(s, basis=routing_basis))
    forecast_res = _forecast_2027_runs(forecast_key, price_version)
    rep_idx = forecast_res['picks'][rep]
    rep_year = years[rep_idx]

    coupling_description = ('PV charging is independent of inverter AC output, subject to charge power and battery headroom.' if s['pv_battery_coupling'] == 'dc_coupled' else 'PV is inverter-AC-capped before battery charging.')
    limitations = [
        'Physical sizing enforces topology-specific ILR and annual clipping caps in every weather/project year, DC terminal C-rate, SoC window, separate AC battery inverter and shared DC hybrid output. Topology mode: ' + topology_mode + '.',
        'PVGIS inputs are AC-equivalent: available DC is reconstructed as profile / inverter efficiency. Battery capacity inputs remain usable kWh; nominal kWh = usable / SoC window. No grid charging. Inverter ratings are fixed inputs, not continuous sizing decisions.',
        f'Solar sizing enforces the selected PV array ceiling ({pv_max_kwp:.2f} kWp): the maximum whole-panel array is {max_array_panels} panels ({max_array_kwp:.3f} kWp). The separate inverter AC capacity is {inverter_kw:.2f} kW; {coupling_description}',
        f"Roof priority is established by modeled annual AC yield ({', '.join(f'{r}: {roof_production_kwh_kwp[r]:.0f} kWh/kWp' for r in yield_priority)}), allocating panels to higher-yielding sections first in greedy seeds and yield-based tie-breakers.",
        'Candidate layouts use transparent benchmark capital costs (fixed PV cost + marginal cost/kWp + battery cost/kWh) rather than an unchanged turnkey quote total.',
        f'The inverter AC limit is held fixed at {inverter_kw:.2f} kW as selected; {coupling_description} Candidate topology and physical limits are stored in each recommendation; product compatibility requires datasheet confirmation.',
        f'Economic evaluation covers all {len(years)} historical PVGIS weather analogues ({min(years)}–{max(years)}) across project life; bounds reflect historical weather variance.',
        'Null or unrecovered paybacks are handled strictly and never treated as zero.'
    ]
    if mixed:
        limitations.append('Mixed battery storage search evaluates combinations of both module sizes rather than a single uniform kWh step.')
    if schedule_optimized:
        limitations.append('Scheduled hybrid routing configurations use approximate monthly screening followed by exact lifetime dispatch verification.')
    if layout_exact and schedule_optimized:
        limitations.insert(0, f'Search space ({valid_candidates_count:,} combinations) was evaluated via exhaustive integer enumeration across candidate hardware layouts; battery routing schedules were screened using approximate monthly screening and verified with exact lifetime dispatch. Results are explicitly best-found rather than certified global optima.')
    elif not layout_exact:
        limitations.insert(0, f'Search space ({valid_candidates_count:,} combinations) was explored via bounded deterministic coordinate sampling and multi-objective refinement ({len(attempted)} checked, {len(attempted)/valid_candidates_count*100:.1f}% coverage). Results are explicitly best-found rather than certified global optima.')
    else:
        limitations.insert(0, f'Search space ({valid_candidates_count:,} combinations) was evaluated completely via exhaustive integer enumeration and is certified globally optimal within the specified bounds.')

    if not evaluated or best_candidate is None:
        zero_npv_env = {'min': 0.0, 'median': 0.0, 'mean': 0.0, 'max': 0.0}
        s_zero = {
            **s,
            **_roof_settings(dict.fromkeys(_search_roofs(), 0)),
            'battery_kwh': 0.0,
            'battery_modules': 0,
            'battery_secondary_modules': 0,
            'battery_mixed_sizes': False,
            'quote_total': None
        }
        detail_zero = evaluate(s_zero, detail=True)
        no_install_fallback = {
            'id': 'no_install',
            'role': 'financial',
            'role_label': 'No-install baseline',
            'panel_count': 0, 'kwp': 0.0,
            'roof_counts': {r: 0 for r in _search_roofs()},
            'battery_kwh': 0.0,
            'battery_modules': 0,
            'battery_secondary_modules': 0,
            'first_modules': 0,
            'battery_module_kwh': module_kwh,
            'battery_secondary_module_kwh': sec_module_kwh if mixed else None,
            'battery_mixed_sizes': mixed,
            'capex_eur': 0.0,
            'solar_capex_eur': 0.0,
            'solar_loan_principal_eur': 0.0,
            'initial_solar_equity_eur': 0.0,
            'solar_loan_ratio': 1.0,
            'loan_principal_eur': 0.0,
            'total_loan_scope_eur': 0.0,
            'financial_basis': financial_basis,
            'npv': zero_npv_env,
            'npv_median': 0.0, 'npv_mean': 0.0, 'npv_min': 0.0, 'npv_max': 0.0,
            'project_npv': zero_npv_env,
            'project_npv_median': 0.0, 'project_npv_mean': 0.0, 'project_npv_min': 0.0, 'project_npv_max': 0.0,
            'equity_npv': zero_npv_env,
            'equity_npv_median': 0.0, 'equity_npv_mean': 0.0, 'equity_npv_min': 0.0, 'equity_npv_max': 0.0,
            'discounted_payback_years': None,
            'simple_payback_years': None,
            'project_discounted_payback_years': None,
            'project_simple_payback_years': None,
            'equity_discounted_payback_years': None,
            'equity_simple_payback_years': None,
            'discounted_payback_of_mean_cash_flows': None,
            'simple_payback_of_mean_cash_flows': None,
            'project_discounted_payback_of_mean_cash_flows': None,
            'project_simple_payback_of_mean_cash_flows': None,
            'equity_discounted_payback_of_mean_cash_flows': None,
            'equity_simple_payback_of_mean_cash_flows': None,
            'autarky_median': 0.0, 'autarky_mean': 0.0,
            'self_consumption_median': 0.0, 'self_consumption_mean': 0.0,
            'annual_pv_kwh_median': 0.0, 'annual_pv_kwh_mean': 0.0,
            'annual_clipped_kwh_median': 0.0, 'annual_clipped_kwh_mean': 0.0,
            'annual_clipped_pct_median': 0.0, 'annual_clipped_pct_mean': 0.0,
            'annual_grid_import_kwh_median': zero_import_median,
            'annual_grid_import_kwh_mean': zero_import_mean,
            'annual_grid_export_kwh_median': 0.0, 'annual_grid_export_kwh_mean': 0.0,
            'annual_battery_discharge_kwh_median': 0.0, 'annual_battery_discharge_kwh_mean': 0.0,
            'annual_grid_import_kwh': zero_import_env,
            'is_no_install': True,
            'is_positive_npv': False,
            'status': 'certified_no_install' if exact else 'best_found_no_install',
            'certified_constrained_optimum': exact,
            'reason': 'No installation configuration in the evaluated domain achieves positive net present value under current economic assumptions; no-install baseline (€0 capex, €0 NPV) provides higher economic return.' if exact else 'No installation configuration among evaluated discrete samples achieves positive net present value under current economic assumptions; no-install baseline (€0 capex, €0 NPV) provides higher economic return.',
            'explanation': 'The separate no-install baseline (€0 capex, €0 NPV) provides higher financial return than any evaluated installation.',
            'settings': s_zero,
            'detail': detail_zero,
            'inverter_kw': inverter_kw,
            'physical_constraints': physical_constraints,
            'pv_max_kwp': pv_max_kwp,
            'inverter_held_fixed': True,
            'inverter_status': f'Fixed selected inverter ({inverter_kw:.2f} kW) with AC clipping',
            'reached_inverter_ceiling': False,
            'reached_roof_bound': False,
            'bounds_note': 'The separate no-install baseline (€0 capex, €0 NPV) provides higher lifetime economic return than any evaluated installation.',
            'weather_representative': rep,
            'representative_weather_year': rep_year,
            'npv_representative': 0.0
        }
        comps = [no_install_fallback]
        if quoted_comp: comps.append(quoted_comp)
        comps.append(curr_comp)
        search_summary = {
            'objective': objective,
            'objective_label': objective_labels[objective],
            'financial_basis': financial_basis,
            'retained_profit_pct': retained_profit_pct,
            'exact': exact,
            'layout_exact': layout_exact,
            'schedule_search_exact': not schedule_optimized,
            'best_found': not exact,
            'certified_global_optimum': exact,
            'search_method': 'exhaustive_integer_enumeration' if layout_exact else 'bounded_discrete_sampling_and_refinement',
            'evaluated_count': 0,
            'evaluated_fraction': 0.0,
            'search_space_size': valid_candidates_count,
            'valid_candidates_count': valid_candidates_count,
            'optimality_gap': None,
            'optimality_bound': None,
            'search_diagnostics': {
                'exact': exact,
                'layout_exact': layout_exact,
                'schedule_search_exact': not schedule_optimized,
                'evaluated_count': 0,
                'valid_candidates_count': valid_candidates_count,
                'evaluated_fraction': 0.0,
                'sampling_truncated': False,
                'optimality_bound': None,
                'optimality_gap': None,
                'pruning_certificate': None
            },
            'infeasibility_proved': False if not exact else True,
            'evaluation_budget': budget,
            'requested_roof_maxima': roof_max,
            'effective_roof_limits': effective_limits,
            'include_theoretical': include_theoretical,
            'battery_mode': battery_mode,
            'battery_mixed_sizes': mixed,
            'battery_step_kwh': None if mixed else module_kwh,
            'battery_step_mode': 'combinations' if mixed else 'uniform_step',
            'battery_module_kwh': module_kwh,
            'battery_secondary_module_kwh': sec_module_kwh if mixed else None,
            'battery_modules_evaluated': candidate_modules,
            'battery_capacities_evaluated': candidate_batteries,
            'battery_compositions_evaluated': [
                {
                    'id': c['id'],
                    'battery_modules': c['battery_modules'],
                    'battery_secondary_modules': c['battery_secondary_modules'],
                    'first_modules': c['first_modules'],
                    'battery_kwh': c['battery_kwh'],
                } for c in candidate_compositions
            ],
            'storage_cost_mode': storage_cost_mode,
            'inverter_kw': inverter_kw,
            'inverter_held_fixed': True,
            'inverter_panel_ceiling': max_array_panels,
            'inverter_kwp_ceiling': max_array_kwp,
            'physical_constraints': physical_constraints,
            'pv_max_kwp': pv_max_kwp,
            'pv_panel_ceiling': max_array_panels,
            'pv_array_kwp_ceiling': max_array_kwp,
            'hard_kwp_ceiling': None,
            'hard_panel_ceiling': None,
            'inverter_ceiling_enforced': True,
            'pv_ceiling_enforced': True,
            'roof_yield_priority': yield_priority,
            'roof_production_kwh_kwp': roof_production_kwh_kwp,
            'roof_priority_basis': 'modeled_median_annual_yield_pvgis_ac',
            'weather_count': len(years),
            'weather_representative': rep,
            'representative_weather_year': rep_year,
            'horizon_years': int(s['horizon_years']),
            'allowed_sacrifice_pct': sac_pct,
            'coverage_target_pct': cov_pct,
            'limitations': limitations
        }
        cost_assumptions = [
            {'item': 'PV fixed benchmark cost', 'value': f"{s['pv_fixed_cost']:,.0f} €", 'source': 'Benchmark catalog calibration', 'status': 'provided'},
            {'item': 'PV marginal benchmark cost', 'value': f"{s['pv_cost_kwp']:,.0f} €/kWp", 'source': 'Benchmark catalog calibration', 'status': 'provided'},
            {'item': 'Battery marginal cost', 'value': ('0 € (Existing storage)' if (storage_cost_mode == 'existing' and battery_mode == 'fixed') else f"{s['battery_cost_kwh']:,.0f} €/usable kWh"), 'source': ('Existing storage (sunk cost)' if (storage_cost_mode == 'existing' and battery_mode == 'fixed') else 'Step 1 research benchmark'), 'status': ('existing' if (storage_cost_mode == 'existing' and battery_mode == 'fixed') else 'benchmark')},
            {'item': 'Tax deduction rate', 'value': f"{ded_rate*100:.0f}%", 'source': 'Scenario tax deduction (10-yr schedule, IRPEF capacity-capped)', 'status': 'scenario'},
            {'item': 'Simulation horizon', 'value': f"{s['horizon_years']} years", 'source': 'Project simulation horizon', 'status': 'scenario'},
            {'item': 'Inverter AC capacity', 'value': f"{inverter_kw:.2f} kW", 'source': f"Held fixed; {coupling_description}", 'status': 'fixed'},
            {'item': 'Maximum PV array capacity', 'value': f"{pv_max_kwp:.2f} kWp", 'source': 'Selected connected array sizing limit', 'status': 'fixed'}
        ]
        no_feasible_rec_choices = {
            'financial': no_install_fallback,
            'balanced': None,
            'budget': None,
            'smaller': None,
            'preferences': {
                'allowed_sacrifice_pct': sac_pct,
                'coverage_target_pct': cov_pct,
                'retained_profit_pct': retained_profit_pct,
                'financial_basis': financial_basis
            },
            'role_status': {
                'financial': {
                    'role': 'financial',
                    'available': True,
                    'is_no_install': True,
                    'status': 'certified_no_install' if exact else 'best_found_no_install',
                    'certified_constrained_optimum': exact,
                    'reason': 'No installation configuration in the evaluated domain achieves positive net present value under current economic assumptions; no-install baseline (€0 capex, €0 NPV) provides higher economic return.' if exact else 'No installation configuration among evaluated discrete samples achieves positive net present value under current economic assumptions; no-install baseline (€0 capex, €0 NPV) provides higher economic return.'
                },
                'balanced': {'role': 'balanced', 'available': False, 'status': 'unavailable', 'certified_constrained_optimum': exact, 'reason': 'No installation configuration evaluated/available. Balanced choice requires strictly positive net present value.'},
                'budget': {'role': 'budget', 'available': False, 'status': 'proved_unavailable' if exact else 'target_not_met_among_evaluated', 'certified_constrained_optimum': exact, 'infeasibility_proved': exact, 'reason': 'No configuration in the domain achieves positive net present value meeting coverage target.' if exact else 'Target not met among evaluated configurations; domain feasibility remains unknown.'},
                'smaller': {'role': 'smaller', 'available': False, 'status': 'unavailable', 'certified_constrained_optimum': exact, 'reason': 'No installation configuration evaluated/available. Smaller choice requires strictly positive net present value.'}
            },
            'main_recommendation': None
        }
        return {
            'status': 'NO_FEASIBLE_CONFIGURATIONS',
            'recommended_candidate': None,
            'recommendation_choices': no_feasible_rec_choices,
            'top_alternatives': [],
            'size_curve': [],
            'tradeoff_matrix': {'pv_axis_kwp': [], 'battery_axis_kwh': [], 'cells': []},
            'comparisons': comps,
            'search_summary': search_summary,
            'cost_assumptions': cost_assumptions
        }

    full_recommended = evaluate(best_candidate['settings'], detail=True)

    counts_map = {}
    for cand in evaluated.values():
        n = cand['panel_count']
        if n not in counts_map or cand['tie_key'] > counts_map[n]['tie_key']:
            counts_map[n] = cand

    size_curve = []
    sorted_counts = sorted(counts_map.keys())
    for idx, n in enumerate(sorted_counts):
        pt = counts_map[n]
        is_rec = (pt['panel_count'] == best_candidate['panel_count'] and pt['tie_key'] == best_candidate['tie_key'])
        curve_entry = {
            'panel_count': pt['panel_count'],
            'kwp': pt['kwp'],
            'roof_counts': pt['roof_counts'],
            'battery_kwh': pt['battery_kwh'],
            'battery_modules': pt['battery_modules'],
            'battery_secondary_modules': pt.get('battery_secondary_modules', 0),
            'first_modules': pt.get('first_modules', pt['battery_modules'] - pt.get('battery_secondary_modules', 0)),
            'battery_module_kwh': pt.get('battery_module_kwh'),
            'battery_secondary_module_kwh': pt.get('battery_secondary_module_kwh'),
            'battery_mixed_sizes': pt.get('battery_mixed_sizes', False),
            'capex_eur': pt['capex_eur'],
            'solar_capex_eur': pt.get('solar_capex_eur', pt['capex_eur']),
            'solar_loan_principal_eur': pt.get('solar_loan_principal_eur', 0.0),
            'initial_solar_equity_eur': pt.get('initial_solar_equity_eur', pt['capex_eur']),
            'solar_loan_ratio': pt.get('solar_loan_ratio', 1.0),
            'loan_principal_eur': pt.get('loan_principal_eur', 0.0),
            'total_loan_scope_eur': pt.get('total_loan_scope_eur', pt['capex_eur']),
            'financial_basis': financial_basis,
            'npv': pt.get('npv'),
            'npv_median': pt['npv_median'],
            'npv_mean': pt.get('npv_mean', pt['npv_median']),
            'npv_min': pt['npv_min'],
            'npv_max': pt['npv_max'],
            'project_npv': pt.get('project_npv'),
            'project_npv_median': pt.get('project_npv_median', pt['npv_median']),
            'project_npv_mean': pt.get('project_npv_mean', pt.get('npv_mean', pt['npv_median'])),
            'project_npv_min': pt.get('project_npv_min', pt['npv_min']),
            'project_npv_max': pt.get('project_npv_max', pt['npv_max']),
            'equity_npv': pt.get('equity_npv'),
            'equity_npv_median': pt.get('equity_npv_median', pt['npv_median']),
            'equity_npv_mean': pt.get('equity_npv_mean', pt.get('npv_mean', pt['npv_median'])),
            'equity_npv_min': pt.get('equity_npv_min', pt['npv_min']),
            'equity_npv_max': pt.get('equity_npv_max', pt['npv_max']),
            'discounted_payback_years': pt['discounted_payback_years'],
            'simple_payback_years': pt['simple_payback_years'],
            'project_discounted_payback_years': pt.get('project_discounted_payback_years'),
            'project_simple_payback_years': pt.get('project_simple_payback_years'),
            'equity_discounted_payback_years': pt.get('equity_discounted_payback_years'),
            'equity_simple_payback_years': pt.get('equity_simple_payback_years'),
            'discounted_payback_of_mean_cash_flows': pt.get('discounted_payback_of_mean_cash_flows', pt.get('discounted_payback_years')),
            'simple_payback_of_mean_cash_flows': pt.get('simple_payback_of_mean_cash_flows', pt.get('simple_payback_years')),
            'project_discounted_payback_of_mean_cash_flows': pt.get('project_discounted_payback_of_mean_cash_flows', pt.get('project_discounted_payback_years')),
            'project_simple_payback_of_mean_cash_flows': pt.get('project_simple_payback_of_mean_cash_flows', pt.get('project_simple_payback_years')),
            'equity_discounted_payback_of_mean_cash_flows': pt.get('equity_discounted_payback_of_mean_cash_flows', pt.get('equity_discounted_payback_years')),
            'equity_simple_payback_of_mean_cash_flows': pt.get('equity_simple_payback_of_mean_cash_flows', pt.get('equity_simple_payback_years')),
            'autarky_median': pt['autarky_median'],
            'autarky_mean': pt.get('autarky_mean', pt['autarky_median']),
            'self_consumption_median': pt['self_consumption_median'],
            'self_consumption_mean': pt.get('self_consumption_mean', pt['self_consumption_median']),
            'annual_pv_kwh_median': pt['annual_pv_kwh_median'],
            'annual_pv_kwh_mean': pt.get('annual_pv_kwh_mean', pt['annual_pv_kwh_median']),
            'annual_pv_input_kwh_median': pt.get('annual_pv_input_kwh_median', 0.0),
            'annual_pv_input_kwh_mean': pt.get('annual_pv_input_kwh_mean', pt.get('annual_pv_input_kwh_median', 0.0)),
            'annual_clipped_kwh_median': pt.get('annual_clipped_kwh_median', 0.0),
            'annual_clipped_kwh_mean': pt.get('annual_clipped_kwh_mean', pt.get('annual_clipped_kwh_median', 0.0)),
            'annual_clipped_pct_median': pt.get('annual_clipped_pct_median', 0.0),
            'annual_clipped_pct_mean': pt.get('annual_clipped_pct_mean', pt.get('annual_clipped_pct_median', 0.0)),
            'annual_grid_import_kwh_median': pt['annual_grid_import_kwh_median'],
            'annual_grid_import_kwh_mean': pt.get('annual_grid_import_kwh_mean', pt['annual_grid_import_kwh_median']),
            'annual_grid_export_kwh_median': pt['annual_grid_export_kwh_median'],
            'annual_grid_export_kwh_mean': pt.get('annual_grid_export_kwh_mean', pt['annual_grid_export_kwh_median']),
            'is_recommended': is_rec,
            'settings': pt['settings']
        }
        if idx == 0:
            curve_entry['marginal_npv_per_panel'] = None
            curve_entry['marginal_npv_per_kwp'] = None
            curve_entry['delta_panels'] = 0
            curve_entry['delta_battery_kwh'] = 0.0
        else:
            prev = size_curve[idx - 1]
            dn = curve_entry['panel_count'] - prev['panel_count']
            dkwp = curve_entry['kwp'] - prev['kwp']
            dnpv = curve_entry['npv_mean'] - prev['npv_mean']
            curve_entry['delta_panels'] = dn
            curve_entry['delta_battery_kwh'] = round(curve_entry['battery_kwh'] - prev['battery_kwh'], 2)
            curve_entry['marginal_npv_per_panel'] = round(dnpv / dn, 2) if dn > 0 else 0.0
            curve_entry['marginal_npv_per_kwp'] = round(dnpv / dkwp, 2) if dkwp > 0 else 0.0
        size_curve.append(curve_entry)

    pv_axis = sorted(set(c['kwp'] for c in evaluated.values()))
    bat_axis = sorted(set(c['battery_kwh'] for c in evaluated.values()))
    heatmap_cells = []
    for p in pv_axis:
        for b in bat_axis:
            matching = [c for c in evaluated.values() if math.isclose(c['kwp'], p, abs_tol=1e-6) and math.isclose(c['battery_kwh'], b, abs_tol=1e-6)]
            if matching:
                best_cell = max(matching, key=lambda c: c['tie_key'])
                heatmap_cells.append({
                    'pv_kwp': p,
                    'panel_count': best_cell['panel_count'],
                    'battery_kwh': best_cell['battery_kwh'],
                    'battery_modules': best_cell['battery_modules'],
                    'battery_secondary_modules': best_cell.get('battery_secondary_modules', 0),
                    'first_modules': best_cell.get('first_modules', best_cell['battery_modules'] - best_cell.get('battery_secondary_modules', 0)),
                    'battery_module_kwh': best_cell['battery_module_kwh'],
                    'battery_secondary_module_kwh': best_cell.get('battery_secondary_module_kwh'),
                    'battery_mixed_sizes': best_cell.get('battery_mixed_sizes', False),
                    'financial_basis': financial_basis,
                    'npv': best_cell.get('npv'),
                    'npv_median': best_cell['npv_median'],
                    'npv_mean': best_cell.get('npv_mean', best_cell['npv_median']),
                    'npv_min': best_cell['npv_min'],
                    'npv_max': best_cell['npv_max'],
                    'project_npv': best_cell.get('project_npv'),
                    'project_npv_median': best_cell.get('project_npv_median', best_cell['npv_median']),
                    'project_npv_mean': best_cell.get('project_npv_mean', best_cell.get('npv_mean', best_cell['npv_median'])),
                    'project_npv_min': best_cell.get('project_npv_min', best_cell['npv_min']),
                    'project_npv_max': best_cell.get('project_npv_max', best_cell['npv_max']),
                    'equity_npv': best_cell.get('equity_npv'),
                    'equity_npv_median': best_cell.get('equity_npv_median', best_cell['npv_median']),
                    'equity_npv_mean': best_cell.get('equity_npv_mean', best_cell.get('npv_mean', best_cell['npv_median'])),
                    'equity_npv_min': best_cell.get('equity_npv_min', best_cell['npv_min']),
                    'equity_npv_max': best_cell.get('equity_npv_max', best_cell['npv_max']),
                    'capex_eur': best_cell['capex_eur'],
                    'solar_capex_eur': best_cell.get('solar_capex_eur', best_cell['capex_eur']),
                    'solar_loan_principal_eur': best_cell.get('solar_loan_principal_eur', 0.0),
                    'initial_solar_equity_eur': best_cell.get('initial_solar_equity_eur', best_cell['capex_eur']),
                    'solar_loan_ratio': best_cell.get('solar_loan_ratio', 1.0),
                    'loan_principal_eur': best_cell.get('loan_principal_eur', 0.0),
                    'total_loan_scope_eur': best_cell.get('total_loan_scope_eur', best_cell['capex_eur']),
                    'discounted_payback_years': best_cell.get('discounted_payback_years'),
                    'simple_payback_years': best_cell.get('simple_payback_years'),
                    'project_discounted_payback_years': best_cell.get('project_discounted_payback_years'),
                    'project_simple_payback_years': best_cell.get('project_simple_payback_years'),
                    'equity_discounted_payback_years': best_cell.get('equity_discounted_payback_years'),
                    'equity_simple_payback_years': best_cell.get('equity_simple_payback_years'),
                    'discounted_payback_of_mean_cash_flows': best_cell.get('discounted_payback_of_mean_cash_flows'),
                    'simple_payback_of_mean_cash_flows': best_cell.get('simple_payback_of_mean_cash_flows'),
                    'project_discounted_payback_of_mean_cash_flows': best_cell.get('project_discounted_payback_of_mean_cash_flows'),
                    'project_simple_payback_of_mean_cash_flows': best_cell.get('project_simple_payback_of_mean_cash_flows'),
                    'equity_discounted_payback_of_mean_cash_flows': best_cell.get('equity_discounted_payback_of_mean_cash_flows'),
                    'equity_simple_payback_of_mean_cash_flows': best_cell.get('equity_simple_payback_of_mean_cash_flows'),
                    'autarky_median': best_cell.get('autarky_median'),
                    'autarky_mean': best_cell.get('autarky_mean', best_cell.get('autarky_median')),
                    'self_consumption_median': best_cell.get('self_consumption_median'),
                    'self_consumption_mean': best_cell.get('self_consumption_mean', best_cell.get('self_consumption_median')),
                    'annual_pv_kwh_median': best_cell.get('annual_pv_kwh_median'),
                    'annual_pv_kwh_mean': best_cell.get('annual_pv_kwh_mean', best_cell.get('annual_pv_kwh_median')),
                    'annual_clipped_kwh_median': best_cell.get('annual_clipped_kwh_median', 0.0),
                    'annual_clipped_kwh_mean': best_cell.get('annual_clipped_kwh_mean', best_cell.get('annual_clipped_kwh_median', 0.0)),
                    'annual_clipped_pct_median': best_cell.get('annual_clipped_pct_median', 0.0),
                    'annual_clipped_pct_mean': best_cell.get('annual_clipped_pct_mean', best_cell.get('annual_clipped_pct_median', 0.0)),
                    'evaluated': True,
                    'settings': best_cell['settings'],
                    'resolved_routing_priority': best_cell.get('resolved_routing_priority'),
                    'routing_priority': best_cell['settings'].get('routing_priority'),
                    'routing_switch_hour': best_cell['settings'].get('routing_switch_hour'),
                    'scheduled_start_month': best_cell['settings'].get('scheduled_start_month', 3),
                    'scheduled_end_month': best_cell['settings'].get('scheduled_end_month', 9)
                })

    zero_bat_cands = [c for c in evaluated.values() if c['battery_kwh'] == 0.0]
    zero_bat_best = max(zero_bat_cands, key=lambda c: c['tie_key']) if zero_bat_cands else None

    zero_npv_env = {'min': 0.0, 'median': 0.0, 'mean': 0.0, 'max': 0.0}
    no_install_cand = counts_map.get(0, {
        'id': 'no_install',
        'label': 'No-install baseline',
        'panel_count': 0, 'kwp': 0.0,
        'roof_counts': {r: 0 for r in _search_roofs()},
        'battery_kwh': 0.0,
        'battery_modules': 0,
        'battery_secondary_modules': 0,
        'first_modules': 0,
        'battery_module_kwh': module_kwh,
        'battery_secondary_module_kwh': sec_module_kwh if mixed else None,
        'battery_mixed_sizes': mixed,
        'capex_eur': 0.0,
        'solar_capex_eur': 0.0,
        'solar_loan_principal_eur': 0.0,
        'initial_solar_equity_eur': 0.0,
        'solar_loan_ratio': 1.0,
        'loan_principal_eur': 0.0,
        'total_loan_scope_eur': 0.0,
        'financial_basis': financial_basis,
        'npv': zero_npv_env,
        'npv_median': 0.0, 'npv_mean': 0.0, 'npv_min': 0.0, 'npv_max': 0.0,
        'project_npv': zero_npv_env,
        'project_npv_median': 0.0, 'project_npv_mean': 0.0, 'project_npv_min': 0.0, 'project_npv_max': 0.0,
        'equity_npv': zero_npv_env,
        'equity_npv_median': 0.0, 'equity_npv_mean': 0.0, 'equity_npv_min': 0.0, 'equity_npv_max': 0.0,
        'discounted_payback_years': None,
        'simple_payback_years': None,
        'project_discounted_payback_years': None,
        'project_simple_payback_years': None,
        'equity_discounted_payback_years': None,
        'equity_simple_payback_years': None,
        'discounted_payback_of_mean_cash_flows': None,
        'simple_payback_of_mean_cash_flows': None,
        'project_discounted_payback_of_mean_cash_flows': None,
        'project_simple_payback_of_mean_cash_flows': None,
        'equity_discounted_payback_of_mean_cash_flows': None,
        'equity_simple_payback_of_mean_cash_flows': None,
        'autarky_median': 0.0, 'autarky_mean': 0.0,
        'self_consumption_median': 0.0, 'self_consumption_mean': 0.0,
        'annual_pv_kwh_median': 0.0, 'annual_pv_kwh_mean': 0.0,
        'annual_clipped_kwh_median': 0.0, 'annual_clipped_kwh_mean': 0.0,
        'annual_clipped_pct_median': 0.0, 'annual_clipped_pct_mean': 0.0,
        'annual_grid_import_kwh_median': zero_import_median,
        'annual_grid_import_kwh_mean': zero_import_mean
    })

    comp_keys = [
        'panel_count', 'kwp', 'roof_counts', 'battery_kwh', 'battery_modules', 'battery_secondary_modules', 'battery_mixed_sizes',
        'capex_eur', 'solar_capex_eur', 'solar_loan_principal_eur', 'initial_solar_equity_eur', 'solar_loan_ratio', 'loan_principal_eur', 'total_loan_scope_eur',
        'financial_basis', 'npv', 'npv_median', 'npv_mean', 'npv_min', 'npv_max',
        'project_npv', 'project_npv_median', 'project_npv_mean', 'project_npv_min', 'project_npv_max',
        'equity_npv', 'equity_npv_median', 'equity_npv_mean', 'equity_npv_min', 'equity_npv_max',
        'discounted_payback_years', 'project_discounted_payback_years', 'equity_discounted_payback_years',
        'simple_payback_years', 'project_simple_payback_years', 'equity_simple_payback_years',
        'discounted_payback_of_mean_cash_flows', 'project_discounted_payback_of_mean_cash_flows', 'equity_discounted_payback_of_mean_cash_flows',
        'simple_payback_of_mean_cash_flows', 'project_simple_payback_of_mean_cash_flows', 'equity_simple_payback_of_mean_cash_flows',
        'autarky_median', 'autarky_mean', 'self_consumption_median', 'self_consumption_mean',
        'annual_pv_kwh_median', 'annual_pv_kwh_mean', 'annual_clipped_kwh_median', 'annual_clipped_kwh_mean',
        'annual_clipped_pct_median', 'annual_clipped_pct_mean',
        'annual_grid_import_kwh_median', 'annual_grid_import_kwh_mean'
    ]

    comparisons = [
        {'id': 'no_install', 'label': 'No-install baseline', **{k: no_install_cand.get(k) for k in comp_keys if k in no_install_cand}}
    ]
    if quoted_comp:
        comparisons.append(quoted_comp)
    comparisons.append(curr_comp)
    comparisons.append({'id': 'recommended', 'label': 'Selected-objective winner', **{k: best_candidate.get(k) for k in comp_keys if k in best_candidate}})

    if zero_bat_best and zero_bat_best['tie_key'] != best_candidate['tie_key']:
        comparisons.append({'id': 'pure_solar', 'label': 'Best pure-solar (0 kWh battery)', **{k: zero_bat_best.get(k) for k in comp_keys if k in zero_bat_best}})

    max_n = max(sorted_counts)
    max_cand = counts_map[max_n]
    if max_cand['tie_key'] != best_candidate['tie_key']:
        is_true_max = (max_cand['panel_count'] == max_total_panels and max_total_panels > 0)
        if is_true_max and max_total_panels == max_array_panels and max_array_panels < sum(roof_max.values()):
            max_label = f'PV array ceiling ({max_cand["panel_count"]} panels · {max_cand["kwp"]:.2f} kWp)'
        elif is_true_max:
            max_label = f'Maximum roof capacity ({max_cand["panel_count"]} panels)'
        else:
            max_label = f'Largest evaluated system ({max_cand["panel_count"]} panels)'
        comparisons.append({'id': 'max_roof', 'label': max_label, **{k: max_cand.get(k) for k in comp_keys if k in max_cand}})

    if storage_cost_mode == 'existing' and battery_mode == 'fixed':
        bat_cost_item = {'item': 'Battery initial cost', 'value': '0 € (Existing storage)', 'source': 'Existing storage (sunk cost)', 'status': 'existing'}
    elif q10 is not None and q15 is not None:
        rec_b = best_candidate['battery_kwh'] if best_candidate else 0.0
        eff_rate = (battery_capex_amount(rec_b) / rec_b) if rec_b > 0 else ((q10 / 10.0) if q10 else (q15 - q10) / 5.0)
        has_conflict = (s.get('battery_module_cost_override') is not None or s.get('battery_secondary_module_cost_override') is not None)
        override_note = " (takes precedence over module override)" if has_conflict else ""
        bat_cost_item = {'item': 'Battery marginal cost', 'value': f"{eff_rate:,.0f} €/usable kWh", 'source': f'Quote variants (interpolated from 10 & 15 kWh quotes{override_note})', 'status': 'quote_variants'}
    elif s.get('battery_module_cost_override') is not None or s.get('battery_secondary_module_cost_override') is not None:
        bat_cost_item = {'item': 'Battery module cost', 'value': 'Module price overrides active', 'source': 'Explicit battery module cost overrides', 'status': 'override'}
    else:
        bat_cost_item = {'item': 'Battery marginal cost', 'value': f"{s['battery_cost_kwh']:,.0f} €/usable kWh", 'source': 'Step 1 research benchmark', 'status': 'benchmark'}

    rec_choices, top_alts = select_solar_recommendations(
        evaluated.values(), yield_priority, {
            **clean_options,
            'exact': exact,
            'settings': s,
            'annual_grid_import_kwh': zero_import_env,
            'annual_grid_import_kwh_median': zero_import_median,
            'annual_grid_import_kwh_mean': zero_import_mean,
            'weather_count': len(years)
        }
    )

    evaluated_frac = float(round(len(attempted) / valid_candidates_count, 6)) if valid_candidates_count > 0 else 0.0
    search_summary = {
        'objective': objective,
        'objective_label': objective_labels[objective],
        'financial_basis': financial_basis,
        'retained_profit_pct': retained_profit_pct,
        'exact': exact,
        'layout_exact': layout_exact,
        'schedule_search_exact': not schedule_optimized,
        'best_found': not exact,
        'certified_global_optimum': exact,
        'search_method': 'exhaustive_integer_enumeration' if layout_exact else 'bounded_discrete_sampling_and_refinement',
        'evaluated_count': len(attempted),
        'evaluated_fraction': evaluated_frac,
        'feasible_candidate_count': len(evaluated),
        'search_space_size': valid_candidates_count,
        'valid_candidates_count': valid_candidates_count,
        'optimality_gap': None,
        'optimality_bound': None,
        'search_diagnostics': {
            'exact': exact,
            'layout_exact': layout_exact,
            'schedule_search_exact': not schedule_optimized,
            'evaluated_count': len(attempted),
            'valid_candidates_count': valid_candidates_count,
            'evaluated_fraction': evaluated_frac,
            'sampling_truncated': seeding_truncated,
            'optimality_bound': None,
            'optimality_gap': None,
            'pruning_certificate': None
        },
        'infeasibility_proved': False if not exact else (rec_choices.get('budget') is None),
        'evaluation_budget': budget,
        'requested_roof_maxima': roof_max,
        'effective_roof_limits': effective_limits,
        'include_theoretical': include_theoretical,
        'battery_mode': battery_mode,
        'battery_mixed_sizes': mixed,
        'battery_step_kwh': None if mixed else module_kwh,
        'battery_step_mode': 'combinations' if mixed else 'uniform_step',
        'battery_module_kwh': module_kwh,
        'battery_secondary_module_kwh': sec_module_kwh if mixed else None,
        'battery_modules_evaluated': candidate_modules,
        'battery_capacities_evaluated': candidate_batteries,
        'battery_compositions_evaluated': [
            {
                'id': c['id'],
                'battery_modules': c['battery_modules'],
                'battery_secondary_modules': c['battery_secondary_modules'],
                'first_modules': c['first_modules'],
                'battery_kwh': c['battery_kwh'],
            } for c in candidate_compositions
        ],
        'storage_cost_mode': storage_cost_mode,
        'storage_cost_source': bat_cost_item['status'],
        'inverter_kw': inverter_kw,
        'inverter_held_fixed': True,
        'inverter_panel_ceiling': max_array_panels,
        'inverter_kwp_ceiling': max_array_kwp,
        'physical_constraints': physical_constraints,
        'pv_max_kwp': pv_max_kwp,
        'pv_panel_ceiling': max_array_panels,
        'pv_array_kwp_ceiling': max_array_kwp,
        'hard_kwp_ceiling': None,
        'hard_panel_ceiling': None,
        'inverter_ceiling_enforced': True,
        'pv_ceiling_enforced': True,
        'reached_inverter_ceiling': bool(best_candidate['panel_count'] == max_array_panels),
        'reached_roof_bound': bool(best_candidate['panel_count'] == sum(effective_limits.values())),
        'roof_yield_priority': yield_priority,
        'roof_production_kwh_kwp': roof_production_kwh_kwp,
        'roof_priority_basis': 'modeled_median_annual_yield_pvgis_ac',
        'weather_count': len(years),
        'weather_representative': rep,
        'representative_weather_year': rep_year,
        'horizon_years': int(s['horizon_years']),
        'allowed_sacrifice_pct': sac_pct,
        'coverage_target_pct': cov_pct,
        'limitations': limitations
    }

    cost_assumptions = [
        {'item': 'PV fixed benchmark cost', 'value': f"{s['pv_fixed_cost']:,.0f} €", 'source': 'Benchmark catalog calibration', 'status': 'provided'},
        {'item': 'PV marginal benchmark cost', 'value': f"{s['pv_cost_kwp']:,.0f} €/kWp", 'source': 'Benchmark catalog calibration', 'status': 'provided'},
        bat_cost_item,
        {'item': 'Tax deduction rate', 'value': f"{ded_rate*100:.0f}%", 'source': 'Scenario tax deduction (10-yr schedule, IRPEF capacity-capped)', 'status': 'scenario'},
        {'item': 'Simulation horizon', 'value': f"{s['horizon_years']} years", 'source': 'Project simulation horizon', 'status': 'scenario'},
        {'item': 'Inverter AC capacity', 'value': f"{inverter_kw:.2f} kW", 'source': f"Held fixed; {coupling_description}", 'status': 'fixed'},
        {'item': 'Maximum PV array capacity', 'value': f"{pv_max_kwp:.2f} kWp", 'source': 'Selected connected array sizing limit', 'status': 'fixed'}
    ]
    for r in _search_roofs():
        extra_r = float(s.get(r + '_extra_cost_per_panel', 0.0) or 0.0)
        if extra_r > 0:
            cost_assumptions.append({
                'item': f'{r.replace("_", " ").title()} roof extra cost',
                'value': f"{extra_r:,.0f} €/panel",
                'source': 'Roof placement mounting/hardware differential',
                'status': 'user_specified'
            })
    if s.get('battery_module_cost_override') is not None:
        is_superseded = (q10 is not None and q15 is not None) or (storage_cost_mode == 'existing' and battery_mode == 'fixed')
        cost_assumptions.append({
            'item': 'Primary battery module cost override',
            'value': f"{float(s['battery_module_cost_override']):,.0f} €/module",
            'source': 'Superseded by quote variants' if (q10 is not None and q15 is not None) else ('Sunk cost (existing storage)' if (storage_cost_mode == 'existing' and battery_mode == 'fixed') else 'Explicit primary battery module price override'),
            'status': 'superseded' if is_superseded else 'override'
        })
    if s.get('battery_secondary_module_cost_override') is not None:
        is_superseded = (q10 is not None and q15 is not None) or (storage_cost_mode == 'existing' and battery_mode == 'fixed')
        cost_assumptions.append({
            'item': 'Secondary battery module cost override',
            'value': f"{float(s['battery_secondary_module_cost_override']):,.0f} €/module",
            'source': 'Superseded by quote variants' if (q10 is not None and q15 is not None) else ('Sunk cost (existing storage)' if (storage_cost_mode == 'existing' and battery_mode == 'fixed') else 'Explicit secondary battery module price override'),
            'status': 'superseded' if is_superseded else 'override'
        })
    if float(s.get('battery_module_extra_cost', 0.0) or 0.0) > 0:
        cost_assumptions.append({
            'item': 'Primary battery module extra cost',
            'value': f"{float(s['battery_module_extra_cost']):,.0f} €/module",
            'source': 'Primary module installation/hardware extra',
            'status': 'user_specified'
        })
    if float(s.get('battery_secondary_module_extra_cost', 0.0) or 0.0) > 0:
        cost_assumptions.append({
            'item': 'Secondary battery module extra cost',
            'value': f"{float(s['battery_secondary_module_extra_cost']):,.0f} €/module",
            'source': 'Secondary module installation/hardware extra',
            'status': 'user_specified'
        })

    reached_inv = bool(best_candidate['panel_count'] == max_array_panels)
    reached_roof = bool(best_candidate['panel_count'] == sum(effective_limits.values()))
    profile_src = price_profile(s)[1]
    profile_desc = 'empirical CSUD' if 'empirical' in profile_src else ('flat capture benchmark' if 'flat' in profile_src else 'actual hourly GME')
    if current_runtime().household_profile is not None:
        market = current_runtime().household_profile['market']
        profile_desc = market['series'] + ' ' + market['valuation_mode'] + ' export benchmark'
    obj_name = {
        'npv_eur': f'maximum {basis_label}',
        'npv': f'maximum {basis_label}',
        'autarky': 'maximum autarky',
        'self_consumption': 'maximum self-consumption',
        'payback_years': 'fastest payback',
        'payback': 'fastest payback',
        'discounted_payback_years': 'fastest discounted payback',
        'discounted_payback': 'fastest discounted payback',
    }.get(objective, 'selected')
    if best_candidate['panel_count'] < max_array_panels:
        if exact:
            bounds_note = f"The recommended system ({best_candidate['panel_count']} panels, {best_candidate['kwp']:.2f} kWp) sits below the PV array sizing ceiling ({max_array_panels} panels, {pv_max_kwp:.2f} kWp). The selected inverter remains {inverter_kw:.2f} kW AC. Under the {obj_name} objective with the {profile_desc} price profile, this layout achieves certified optimum across all configurations within the specified bounds."
        else:
            bounds_note = f"The recommended system ({best_candidate['panel_count']} panels, {best_candidate['kwp']:.2f} kWp) sits below the PV array sizing ceiling ({max_array_panels} panels, {pv_max_kwp:.2f} kWp). The selected inverter remains {inverter_kw:.2f} kW AC. Under the {obj_name} objective with the {profile_desc} price profile, this layout was the highest-scoring candidate among evaluated discrete samples."
    elif reached_inv:
        bounds_note = f"The recommended system reaches the selected PV array ceiling ({max_array_panels} panels, {best_candidate['kwp']:.2f} kWp). AC inverter capacity remains {inverter_kw:.2f} kW."
    else:
        bounds_note = "The recommended system reaches the maximum allowable physical roof limits."

    if best_candidate['npv_mean'] < 0:
        rec_npv = best_candidate['npv_mean']
        bounds_note += f" Note: The recommended system has negative mean {basis_label} ({rec_npv:,.0f} €); the separate no-install baseline (€0 capex, €0 NPV) provides higher lifetime economic return."
        search_summary['advisory_note'] = f"The recommended configuration has a negative mean {basis_label} ({rec_npv:,.0f} €) under current cost assumptions. Refer to the separate no-install comparison baseline (€0 capex, €0 NPV)."
        limitations.append(f"The recommended configuration has negative mean {basis_label} ({rec_npv:,.0f} €). Under purely financial criteria, the no-install baseline provides higher economic return.")
    search_summary['bounds_note'] = bounds_note

    best_candidate_full = {
        **best_candidate,
        'inverter_kw': inverter_kw,
        'physical_constraints': physical_constraints,
        'pv_max_kwp': pv_max_kwp,
        'inverter_held_fixed': True,
        'inverter_status': f'Fixed selected inverter ({inverter_kw:.2f} kW) with AC clipping',
        'reached_inverter_ceiling': reached_inv,
        'reached_pv_ceiling': reached_inv,
        'reached_roof_bound': reached_roof,
        'bounds_note': bounds_note,
        'weather_representative': rep,
        'representative_weather_year': rep_year,
        'npv_representative': float(best_candidate['npv_by_weather'][rep_idx]),
        'cost_breakdown': detailed_installed_capex_breakdown(best_candidate['settings'], best_candidate['kwp']),
        'detail': full_recommended
    }

    def _make_candidate_bounds_note(cand, role_title, role_type='selected'):
        c_panels = cand['panel_count']
        c_kwp = cand['kwp']
        c_reached_inv = bool(c_panels == max_array_panels)
        c_reached_roof = bool(c_panels == sum(effective_limits.values()))
        if c_panels < max_array_panels:
            if role_type == 'financial':
                if exact:
                    note = f"{role_title} ({c_panels} panels, {c_kwp:.2f} kWp) sits below the PV array sizing ceiling ({max_array_panels} panels, {pv_max_kwp:.2f} kWp). Under the maximum {basis_label} objective with the {profile_desc} price profile, this configuration is certified optimal across the specified bounds."
                else:
                    note = f"{role_title} ({c_panels} panels, {c_kwp:.2f} kWp) sits below the PV array sizing ceiling ({max_array_panels} panels, {pv_max_kwp:.2f} kWp). Under the maximum {basis_label} objective with the {profile_desc} price profile, this configuration achieved highest mean {basis_label} among evaluated discrete samples."
            elif role_type == 'balanced':
                if exact:
                    note = f"{role_title} ({c_panels} panels, {c_kwp:.2f} kWp) sits below the PV array sizing ceiling ({max_array_panels} panels, {pv_max_kwp:.2f} kWp). Under preference-constrained selection with the {profile_desc} price profile, it maximizes coverage while retaining required {basis_label}."
                else:
                    note = f"{role_title} ({c_panels} panels, {c_kwp:.2f} kWp) sits below the PV array sizing ceiling ({max_array_panels} panels, {pv_max_kwp:.2f} kWp). Under preference-constrained selection with the {profile_desc} price profile, it achieved highest coverage among evaluated discrete samples while retaining required {basis_label}."
            elif role_type == 'budget':
                if exact:
                    note = f"{role_title} ({c_panels} panels, {c_kwp:.2f} kWp) sits below the PV array sizing ceiling ({max_array_panels} panels, {pv_max_kwp:.2f} kWp). Under budget selection with the {profile_desc} price profile, it minimizes installed capex while meeting the coverage target with positive {basis_label}."
                else:
                    note = f"{role_title} ({c_panels} panels, {c_kwp:.2f} kWp) sits below the PV array sizing ceiling ({max_array_panels} panels, {pv_max_kwp:.2f} kWp). Under budget selection with the {profile_desc} price profile, it achieved lowest installed capex among evaluated discrete samples meeting the coverage target with positive {basis_label}."
            elif role_type == 'smaller':
                if exact:
                    note = f"{role_title} ({c_panels} panels, {c_kwp:.2f} kWp) sits below the PV array sizing ceiling ({max_array_panels} panels, {pv_max_kwp:.2f} kWp). Under smaller efficient array selection with the {profile_desc} price profile, it minimizes installed capex while retaining at least {retained_profit_pct:g}% of financial {basis_label}."
                else:
                    note = f"{role_title} ({c_panels} panels, {c_kwp:.2f} kWp) sits below the PV array sizing ceiling ({max_array_panels} panels, {pv_max_kwp:.2f} kWp). Under smaller efficient array selection with the {profile_desc} price profile, it achieved lowest installed capex among evaluated discrete samples retaining at least {retained_profit_pct:g}% of financial {basis_label}."
            else:
                if exact:
                    note = f"{role_title} ({c_panels} panels, {c_kwp:.2f} kWp) sits below the PV array sizing ceiling ({max_array_panels} panels, {pv_max_kwp:.2f} kWp). Under the {obj_name} objective with the {profile_desc} price profile, this configuration is certified optimal across the specified bounds."
                else:
                    note = f"{role_title} ({c_panels} panels, {c_kwp:.2f} kWp) sits below the PV array sizing ceiling ({max_array_panels} panels, {pv_max_kwp:.2f} kWp)."
        elif c_reached_inv:
            note = f"{role_title} reaches the selected PV array ceiling ({max_array_panels} panels, {c_kwp:.2f} kWp; limit {pv_max_kwp:.2f} kWp). The AC inverter remains {inverter_kw:.2f} kW."
        else:
            note = f"{role_title} reaches the maximum allowable physical roof limits."
        if cand['npv_mean'] < 0:
            note += f" Note: System has negative mean {basis_label} ({cand['npv_mean']:,.0f} €); the separate no-install baseline (€0 capex, €0 NPV) provides higher lifetime economic return."
        return note

    def _enrich_choice_candidate(cand, role_title, role_type='selected'):
        if cand is None:
            return None
        c_reached_inv = bool(cand['panel_count'] == max_array_panels)
        c_reached_roof = bool(cand['panel_count'] == sum(effective_limits.values()))
        b_note = _make_candidate_bounds_note(cand, role_title, role_type)

        if cand.get('tie_key') == best_candidate.get('tie_key'):
            c_detail = full_recommended
        else:
            c_detail = evaluate(cand['settings'], detail=True)

        if 'npv_by_weather' in cand and cand['npv_by_weather']:
            npv_rep = float(cand['npv_by_weather'][rep_idx])
        else:
            npv_rep = float(cand.get('npv_mean', cand['npv_median']))

        cand.update({
            'inverter_kw': inverter_kw,
            'physical_constraints': physical_constraints,
            'pv_max_kwp': pv_max_kwp,
            'inverter_held_fixed': True,
            'inverter_status': f'Fixed selected inverter ({inverter_kw:.2f} kW) with AC clipping',
            'reached_inverter_ceiling': c_reached_inv,
            'reached_pv_ceiling': c_reached_inv,
            'reached_roof_bound': c_reached_roof,
            'bounds_note': b_note,
            'weather_representative': rep,
            'representative_weather_year': rep_year,
            'npv_representative': npv_rep,
            'cost_breakdown': detailed_installed_capex_breakdown(cand['settings'], cand['kwp']),
            'detail': c_detail
        })
        return cand

    _enrich_choice_candidate(rec_choices['financial'], "The financial reference system", 'financial')
    _enrich_choice_candidate(rec_choices['balanced'], "The balanced recommendation", 'balanced')
    _enrich_choice_candidate(rec_choices['budget'], "The budget choice", 'budget')
    _enrich_choice_candidate(rec_choices['smaller'], "The smaller efficient array", 'smaller')

    for alt in top_alts:
        alt_panels = alt.get('panel_count', 0)
        alt['reached_inverter_ceiling'] = bool(alt_panels == max_array_panels)
        alt['reached_roof_bound'] = bool(alt_panels == sum(effective_limits.values()))
        alt['inverter_kw'] = inverter_kw
        alt['pv_max_kwp'] = pv_max_kwp
        alt['reached_pv_ceiling'] = bool(alt_panels == max_array_panels)

    return {
        'status': 'SCENARIO_PENDING',
        'recommended_candidate': best_candidate_full,
        'recommendation_choices': rec_choices,
        'top_alternatives': top_alts,
        'size_curve': size_curve,
        'tradeoff_matrix': {
            'pv_axis_kwp': pv_axis,
            'battery_axis_kwh': bat_axis,
            'cells': heatmap_cells
        },
        'comparisons': comparisons,
        'search_summary': search_summary,
        'cost_assumptions': cost_assumptions,
        '_evaluated_candidates': list(evaluated.values()),
    }

def solar_sizing(settings=None, options=None, quote=None):
    """Find an optimal or best-found solar panel layout and matched storage capacity."""
    raw = {} if settings is None else settings
    if not isinstance(raw, dict): raise ValueError('settings must be a JSON object')
    s_user = validate(raw)
    quoted_scenario = None
    if quote is not None:
        s_norm, q_norm, itemized_total, populated = normalize_quote(s_user, quote)
        if populated > 0 or s_norm.get('quote_total') is not None or (isinstance(quote, dict) and quote.get('quote_total') is not None):
            quoted_scenario = deepcopy(s_norm)
            if quoted_scenario.get('quote_total') is None and isinstance(quote, dict) and quote.get('quote_total') is not None:
                quoted_scenario['quote_total'] = float(quote['quote_total'])
        quote = q_norm
    elif s_user.get('quote_total') is not None:
        quoted_scenario = deepcopy(s_user)
        s_norm = {**s_user, 'quote_total': None}
    else:
        s_norm = deepcopy(s_user)

    if options is not None and not isinstance(options, dict):
        raise ValueError('options must be a JSON object')
    opt = options or {}
    topology_mode = opt.get('topology_mode', 'fixed')
    if topology_mode not in ('fixed', 'auto', 'ac_equivalent', 'dc_coupled'):
        raise ValueError('topology_mode must be fixed, auto, ac_equivalent or dc_coupled')
    s_norm['pv_dispatch_model'] = 'physical'

    allowed_keys = {
        'topology_mode',
        'objective', 'load_scenario', 'battery_mode', 'storage_cost_mode',
        'battery_min_kwh', 'battery_max_kwh', 'battery_step_kwh',
        'panel_maxima', 'include_theoretical',
        'quote_10kwh_eur', 'quote_15kwh_eur',
        'weather_representative', 'deduction_rate', 'evaluation_budget',
        'npv_sacrifice_pct', 'allowed_sacrifice_pct',
        'coverage_target_pct', 'budget_coverage_target_pct',
        'financial_basis', 'retained_profit_pct'
    }
    unknown = set(opt.keys()) - allowed_keys
    if unknown:
        raise ValueError(f"Unknown options: {', '.join(sorted(unknown))}")

    financial_basis = opt.get('financial_basis') or 'project'
    if financial_basis not in ('project', 'after_financing'):
        raise ValueError("financial_basis must be 'project' or 'after_financing'")

    objective = opt.get('objective', 'npv_eur')
    if objective not in SOLAR_OBJECTIVES:
        raise ValueError(f"objective must be one of {', '.join(SOLAR_OBJECTIVES)}")

    battery_mode = opt.get('battery_mode', 'joint')
    if battery_mode not in ('joint', 'fixed', 'keep_current'):
        raise ValueError("battery_mode must be 'joint' or 'fixed'")
    if battery_mode == 'keep_current':
        battery_mode = 'fixed'

    if battery_mode == 'fixed':
        s = {**s_norm, 'quote_total': None}
    else:
        # In joint mode the module size must come from canonical validated user settings
        # BEFORE quote capacity normalization (current left-menu battery_module_kwh and
        # battery_secondary_module_kwh), and installed battery count remains current configuration
        # only, not a constraint on searched module counts.
        s = {
            **s_norm,
            'battery_modules': s_user['battery_modules'],
            'battery_secondary_modules': s_user.get('battery_secondary_modules', 0),
            'battery_module_kwh': s_user['battery_module_kwh'],
            'battery_secondary_module_kwh': s_user.get('battery_secondary_module_kwh', 6.9),
            'battery_mixed_sizes': s_user.get('battery_mixed_sizes', False),
            'battery_kwh': s_user['battery_kwh'],
            'quote_total': None,
        }

    load_scenario = opt.get('load_scenario', s['load_scenario'])
    if load_scenario not in ('baseline', 'electrified', 'ev'):
        raise ValueError('load_scenario must be baseline, electrified, or ev')
    s = validate({**s, 'load_scenario': load_scenario})

    storage_cost_mode = opt.get('storage_cost_mode', 'purchased')
    if storage_cost_mode not in ('purchased', 'existing'):
        raise ValueError("storage_cost_mode must be 'purchased' or 'existing'")
    if battery_mode != 'fixed' and storage_cost_mode == 'existing':
        storage_cost_mode = 'purchased'

    s = validate({**s, 'storage_cost_mode': storage_cost_mode})

    include_theoretical = opt.get('include_theoretical', True)
    if not isinstance(include_theoretical, bool):
        raise ValueError('include_theoretical must be boolean')

    if 'panel_maxima' in opt and not isinstance(opt['panel_maxima'], dict):
        raise ValueError('panel_maxima must be an object')
    roof_max = _search_maxima(s, opt.get('panel_maxima'))

    b_min = opt.get('battery_min_kwh', 0.0)
    if isinstance(b_min, bool) or not isinstance(b_min, (int, float)) or not math.isfinite(b_min) or b_min < 0 or b_min > 50:
        raise ValueError('battery_min_kwh must be a finite number between 0 and 50')
    b_min = float(b_min)

    b_max = opt.get('battery_max_kwh', 20.0)
    if isinstance(b_max, bool) or not isinstance(b_max, (int, float)) or not math.isfinite(b_max) or b_max < 0 or b_max > 50:
        raise ValueError('battery_max_kwh must be a finite number between 0 and 50')
    b_max = float(b_max)
    if b_min > b_max:
        raise ValueError('battery_min_kwh must be <= battery_max_kwh')

    mixed = bool(s.get('battery_mixed_sizes', False))
    module_kwh = float(s.get('battery_module_kwh', 0.0) or 0.0)
    sec_module_kwh = float(s.get('battery_secondary_module_kwh', 0.0) or 0.0)

    # Legacy step option: validate positive finite form only when relevant (positive joint search),
    # or finite non-negative form for fixed or zero-only searches.
    b_step_opt = opt.get('battery_step_kwh')
    if b_step_opt is not None:
        if battery_mode == 'joint' and b_max > 0:
            if isinstance(b_step_opt, bool) or not isinstance(b_step_opt, (int, float)) or not math.isfinite(b_step_opt) or b_step_opt <= 0:
                raise ValueError('battery_step_kwh must be a positive finite number')
        else:
            if isinstance(b_step_opt, bool) or not isinstance(b_step_opt, (int, float)) or not math.isfinite(b_step_opt) or b_step_opt < 0:
                raise ValueError('battery_step_kwh must be a non-negative finite number')
        b_step = float(b_step_opt)
    else:
        b_step = module_kwh

    if battery_mode == 'joint':
        if not mixed:
            if b_max > 0:
                if not math.isfinite(module_kwh) or module_kwh <= 0:
                    raise ValueError('Joint storage optimization requires a positive battery module capacity. Set battery module capacity (kWh) in the left menu under Battery & storage.')
                m_start = 1 if b_min <= 0 else math.ceil((b_min - 1e-9) / module_kwh)
                m_end = math.floor((b_max + 1e-9) / module_kwh)
                if b_min > 0 and m_start > m_end:
                    raise ValueError(f"No whole battery module capacities exist between {b_min:g} kWh and {b_max:g} kWh with module size {module_kwh:g} kWh.")
                pos_steps = (m_end - m_start + 1) if m_end >= m_start else 0
                if pos_steps > 50:
                    raise ValueError('battery_module_kwh generates too many candidate capacities (maximum 50 steps)')
        else:
            if b_max > 0:
                if not math.isfinite(module_kwh) or module_kwh <= 0:
                    raise ValueError('Joint storage optimization in mixed mode requires a positive first battery module capacity. Set battery module capacity (kWh) in the left menu under Battery & storage.')
                if not math.isfinite(sec_module_kwh) or sec_module_kwh <= 0:
                    raise ValueError('Joint storage optimization in mixed mode requires a positive secondary battery module capacity. Set secondary battery module capacity (kWh) in the left menu under Battery & storage.')
                enumerate_battery_compositions_by_capacity(
                    min_kwh=b_min,
                    max_kwh=b_max,
                    module_kwh=module_kwh,
                    secondary_module_kwh=sec_module_kwh,
                    mixed_sizes=True,
                    max_combinations=50,
                )

    panel_watts = float(s['panel_watts'])
    inverter_kw = float(s['inverter_kw'])
    pv_max_kwp = _effective_pv_max_kwp(s)
    max_arr_panels = max(0, int(math.floor((pv_max_kwp * 1000.0 + 1e-6) / panel_watts)))
    eff_panel_max = 0
    for r in _search_roofs():
        lim = roof_max[r]
        if _dynamic_roofs() is None and not include_theoretical:
            if r in ('dormer', 'flat', 'secondary_south'):
                lim = 0
        lim = min(lim, max_arr_panels)
        eff_panel_max += lim
    eff_panel_max = min(eff_panel_max, max_arr_panels)

    if eff_panel_max == 0 and battery_mode == 'joint' and b_min > 0:
        raise ValueError('Positive storage capacity cannot be installed without solar PV panels. Increase roof limits or maximum PV array capacity, or set storage minimum to 0.')

    q10 = opt.get('quote_10kwh_eur')
    if q10 is None and isinstance(quote, dict):
        q10 = quote.get('quote_10kwh_eur')
    if q10 is not None:
        if isinstance(q10, bool) or not isinstance(q10, (int, float)) or not math.isfinite(q10) or q10 < 0:
            raise ValueError('quote_10kwh_eur must be non-negative finite number')
        q10 = float(q10)

    q15 = opt.get('quote_15kwh_eur')
    if q15 is None and isinstance(quote, dict):
        q15 = quote.get('quote_15kwh_eur')
    if q15 is not None:
        if isinstance(q15, bool) or not isinstance(q15, (int, float)) or not math.isfinite(q15) or q15 < 0:
            raise ValueError('quote_15kwh_eur must be non-negative finite number')
        q15 = float(q15)
    if q10 is not None and q15 is not None and q15 <= q10:
        raise ValueError('quote_15kwh_eur must exceed quote_10kwh_eur')

    rep = opt.get('weather_representative', 'median')
    if rep not in ('low', 'median', 'high'):
        raise ValueError('weather_representative must be low, median, or high')

    ded = opt.get('deduction_rate', s['deduction_rate'])
    if isinstance(ded, bool) or not isinstance(ded, (int, float)) or not math.isfinite(ded) or float(ded) not in (0.0, 0.36, 0.50):
        raise ValueError('deduction_rate must be exactly 0, 0.36, or 0.50')
    ded = float(ded)

    budget_val = opt.get('evaluation_budget', SOLAR_EVALUATION_BUDGET)
    if isinstance(budget_val, bool) or not isinstance(budget_val, (int, float)) or not math.isfinite(budget_val) or int(budget_val) != budget_val or budget_val < 50 or budget_val > 1000:
        raise ValueError('evaluation_budget must be an integer between 50 and 1000')
    budget_val = int(budget_val)

    sac_opt = opt.get('npv_sacrifice_pct')
    if sac_opt is None:
        sac_opt = opt.get('allowed_sacrifice_pct', 5.0)
    if isinstance(sac_opt, bool) or not isinstance(sac_opt, (int, float)) or not math.isfinite(sac_opt) or sac_opt < 0.0 or sac_opt > 100.0:
        raise ValueError('npv_sacrifice_pct must be a finite number between 0 and 100')
    sac_pct = float(sac_opt)

    cov_opt = opt.get('coverage_target_pct')
    if cov_opt is None:
        cov_opt = opt.get('budget_coverage_target_pct', 70.0)
    if isinstance(cov_opt, bool) or not isinstance(cov_opt, (int, float)) or not math.isfinite(cov_opt) or cov_opt < 0.0 or cov_opt > 100.0:
        raise ValueError('coverage_target_pct must be a finite number between 0 and 100')
    cov_pct = float(cov_opt)

    ret_opt = opt.get('retained_profit_pct')
    if ret_opt is None:
        ret_opt = 95.0
    if isinstance(ret_opt, bool) or not isinstance(ret_opt, (int, float)) or not math.isfinite(ret_opt) or ret_opt < 0.0 or ret_opt > 100.0:
        raise ValueError('retained_profit_pct must be a finite number between 0 and 100')
    retained_profit_pct = float(ret_opt)

    clean_options = dict(
        topology_mode=topology_mode,
        objective=objective,
        load_scenario=load_scenario,
        battery_mode=battery_mode,
        storage_cost_mode=storage_cost_mode,
        include_theoretical=include_theoretical,
        panel_maxima=roof_max,
        battery_min_kwh=b_min,
        battery_max_kwh=b_max,
        battery_step_kwh=None if mixed else (module_kwh if (battery_mode == 'joint' and module_kwh > 0) else (module_kwh if battery_mode == 'fixed' else (float(b_step_opt) if b_step_opt is not None else 0.0))),
        battery_step_mode='combinations' if mixed else 'uniform_step',
        battery_mixed_sizes=mixed,
        quote_10kwh_eur=q10,
        quote_15kwh_eur=q15,
        weather_representative=rep,
        deduction_rate=ded,
        evaluation_budget=budget_val,
        npv_sacrifice_pct=sac_pct,
        coverage_target_pct=cov_pct,
        financial_basis=financial_basis,
        retained_profit_pct=retained_profit_pct
    )

    panel_maxima_tuple = tuple(sorted(roof_max.items()))
    key_options = tuple(sorted([(k, panel_maxima_tuple if k == 'panel_maxima' else v) for k, v in clean_options.items()]))
    key_settings = tuple(sorted((k, freeze(v)) for k, v in s.items()))
    key_quoted = tuple(sorted((k, freeze(v)) for k, v in quoted_scenario.items())) if quoted_scenario else ()
    quote_fingerprint = hash(json.dumps(quote, sort_keys=True)) if quote else 0
    price_version = price_fingerprint(s)

    res = _solar_sizing_cached(current_runtime().key, key_settings, key_options, quote_fingerprint, price_version, key_quoted)
    return deepcopy(res)


def _ensemble_annual_flows(settings, forecast_result):
    """Aggregate per-weather dispatch ledgers into physically balanced ensemble mean annual flows."""
    s = settings
    res = forecast_result
    _full = defaults()
    _full.update(s)
    resolved_route = s.get('routing_priority') or resolve_routing_priority(_full)
    eta_inv = float(s['inverter_efficiency'])
    inverter_kw = float(s['inverter_kw'])
    effective_dc = bool(
        s.get('pv_dispatch_model') == 'physical'
        and s.get('pv_battery_coupling') == 'dc_coupled'
        and float(s.get('battery_kwh', 0) or 0) > 0
    )
    factor = eta_inv if effective_dc else 1.0

    traces = res['traces']
    annual_list = res['annual']
    mapped_pv_list = res['mapped_data']['mapped_pv']
    weather_count = len(traces)
    if weather_count == 0:
        return {}

    ledgers = []
    for wi in range(weather_count):
        tr = traces[wi]
        ann = annual_list[wi]
        raw_pv = np.asarray(mapped_pv_list[wi], dtype=float)
        pv_input = float(ann['pv_input_kwh'])
        clipped = float(ann.get('clipped_kwh', ann['pv_input_kwh'] - ann['pv_kwh']))
        pv_gen = pv_input - clipped
        load = float(ann['load_kwh'])
        imp = float(ann['grid_import_kwh'])
        exp = float(ann['grid_export_kwh'])
        direct = float(ann['direct_kwh'])
        chg_tot = float(tr[:, 6].sum())
        dis_tot = float(tr[:, 7].sum())
        chg = factor * chg_tot
        dis = dis_tot
        true_losses = float(ann['battery_loss_kwh'])
        losses = factor * true_losses - (1.0 - factor) * dis_tot
        init_soc = 0.0
        init_soc_kwh = factor * init_soc
        end_soc = float(ann['end_soc_kwh'])
        end_soc_kwh = factor * end_soc
        storage_delta = end_soc - init_soc
        storage_delta_kwh = factor * storage_delta
        clipped_kwh = clipped
        clipped_pct = (100.0 * clipped / pv_input) if pv_input > 0 else 0.0

        pv_eq = tr[:, 0] - (1.0 - factor) * tr[:, 6]
        clip_series = np.maximum(raw_pv - pv_eq, 0.0)
        clipping_hours = float(np.count_nonzero(clip_series > 1e-9))
        peak_curtailed_kw = float(clip_series.max(initial=0.0))

        split = charge_source_split(raw_pv, tr[:, 6].reshape(-1), effective_dc, inverter_kw, eta_inv)
        norm_chg_kwh = factor * float(split['charged_from_normal_solar_kwh'])
        recap_chg_kwh = factor * float(split['charged_from_recaptured_clipping_kwh'])
        norm_chg_phys = float(split['charged_from_normal_solar_kwh'])
        recap_chg_phys = float(split['charged_from_recaptured_clipping_kwh'])
        autarky_yr = max(0.0, min(1.0, 1.0 - imp / load)) if load > 0 else 0.0

        ledgers.append({
            'pv_generation_kwh': pv_gen,
            'pv_input_kwh': pv_input,
            'load_demand_kwh': load,
            'grid_import_kwh': imp,
            'grid_export_kwh': exp,
            'direct_use_kwh': direct,
            'battery_charge_kwh': chg,
            'battery_discharge_kwh': dis,
            'storage_losses_kwh': losses,
            'initial_soc_kwh': init_soc_kwh,
            'end_soc_kwh': end_soc_kwh,
            'storage_delta_kwh': storage_delta_kwh,
            'clipped_kwh': clipped_kwh,
            'clipped_pct': clipped_pct,
            'clipping_hours': clipping_hours,
            'peak_curtailed_kw': peak_curtailed_kw,
            'charged_from_normal_solar_kwh': norm_chg_kwh,
            'charged_from_recaptured_clipping_kwh': recap_chg_kwh,
            'battery_charge_physical_kwh': chg_tot,
            'storage_losses_physical_kwh': true_losses,
            'initial_soc_stored_kwh': init_soc,
            'end_soc_stored_kwh': end_soc,
            'storage_delta_stored_kwh': storage_delta,
            'charged_from_normal_solar_physical_kwh': norm_chg_phys,
            'charged_from_recaptured_clipping_physical_kwh': recap_chg_phys,
            'autarky': autarky_yr,
        })

    energy_keys = (
        'pv_generation_kwh', 'pv_input_kwh', 'load_demand_kwh',
        'grid_import_kwh', 'grid_export_kwh', 'direct_use_kwh',
        'battery_charge_kwh', 'battery_discharge_kwh', 'storage_losses_kwh',
        'initial_soc_kwh', 'end_soc_kwh', 'storage_delta_kwh', 'clipped_kwh',
        'charged_from_normal_solar_kwh', 'charged_from_recaptured_clipping_kwh',
        'battery_charge_physical_kwh', 'storage_losses_physical_kwh',
        'initial_soc_stored_kwh', 'end_soc_stored_kwh', 'storage_delta_stored_kwh',
        'charged_from_normal_solar_physical_kwh', 'charged_from_recaptured_clipping_physical_kwh'
    )

    means = {}
    for k in energy_keys:
        means[k] = float(np.mean([ld[k] for ld in ledgers]))

    mean_pv_input = float(np.mean([ld['pv_input_kwh'] for ld in ledgers]))
    mean_clipped = float(np.mean([ld['clipped_kwh'] for ld in ledgers]))
    mean_imp = float(np.mean([ld['grid_import_kwh'] for ld in ledgers]))
    mean_load = float(np.mean([ld['load_demand_kwh'] for ld in ledgers]))

    clipped_pct = round(100.0 * mean_clipped / mean_pv_input, 2) if mean_pv_input > 0 else 0.0
    clipping_hours = round(float(np.mean([ld['clipping_hours'] for ld in ledgers])), 1)
    peak_curtailed_kw = round(float(np.mean([ld['peak_curtailed_kw'] for ld in ledgers])), 2)
    autarky = 1.0 - mean_imp / mean_load if mean_load > 0 else 0.0

    flow_ranges = {}
    for k in ledgers[0].keys():
        arr = [ld[k] for ld in ledgers]
        if k in energy_keys:
            digits = 6
        elif k in ('peak_curtailed_kw', 'clipped_pct'):
            digits = 2
        elif k == 'clipping_hours':
            digits = 1
        elif k == 'autarky':
            digits = 4
        else:
            digits = 4
        flow_ranges[k] = {
            'p10': round(float(np.percentile(arr, 10)), digits),
            'p90': round(float(np.percentile(arr, 90)), digits)
        }

    return dict(
        energy_reference='ac_equivalent',
        physical_charge_basis='DC' if effective_dc else 'AC',
        pv_generation_kwh=means['pv_generation_kwh'],
        pv_input_kwh=means['pv_input_kwh'],
        load_demand_kwh=means['load_demand_kwh'],
        grid_import_kwh=means['grid_import_kwh'],
        grid_export_kwh=means['grid_export_kwh'],
        direct_use_kwh=means['direct_use_kwh'],
        battery_charge_kwh=means['battery_charge_kwh'],
        battery_discharge_kwh=means['battery_discharge_kwh'],
        storage_losses_kwh=means['storage_losses_kwh'],
        weather_year=None,
        weather_count=weather_count,
        weather_year_count=weather_count,
        statistic='weather_mean',
        initial_soc_kwh=means['initial_soc_kwh'],
        end_soc_kwh=means['end_soc_kwh'],
        storage_delta_kwh=means['storage_delta_kwh'],
        clipped_kwh=means['clipped_kwh'],
        clipped_pct=clipped_pct,
        clipping_hours=clipping_hours,
        peak_curtailed_kw=peak_curtailed_kw,
        charged_from_normal_solar_kwh=means['charged_from_normal_solar_kwh'],
        charged_from_recaptured_clipping_kwh=means['charged_from_recaptured_clipping_kwh'],
        battery_charge_physical_kwh=means['battery_charge_physical_kwh'],
        storage_losses_physical_kwh=means['storage_losses_physical_kwh'],
        initial_soc_stored_kwh=means['initial_soc_stored_kwh'],
        end_soc_stored_kwh=means['end_soc_stored_kwh'],
        storage_delta_stored_kwh=means['storage_delta_stored_kwh'],
        charged_from_normal_solar_physical_kwh=means['charged_from_normal_solar_physical_kwh'],
        charged_from_recaptured_clipping_physical_kwh=means['charged_from_recaptured_clipping_physical_kwh'],
        autarky=autarky,
        flow_ranges=flow_ranges,
        routing_priority=resolved_route,
        routing_switch_hour=float(s.get('routing_switch_hour', 11) or 11),
        scheduled_start_month=int(s.get('scheduled_start_month', 3) or 3),
        scheduled_end_month=int(s.get('scheduled_end_month', 9) or 9),
        aggregation='Ensemble arithmetic mean across all historical weather years; AC-equivalent flows and stored delta satisfy PV=direct+charge+export, load=direct+delivered+grid, and charge-delivered=storage_losses+storage_delta',
        aggregation_note='Ensemble arithmetic mean across all historical weather years; AC-equivalent flows and stored delta satisfy PV=direct+charge+export, load=direct+delivered+grid, and charge-delivered=storage_losses+storage_delta; flow_ranges captures historical P10-P90 spread across weather years'
    )


def _ensemble_monthly_balance(forecast_result):
    """Sum calendar-month dispatch per weather year, then average like annual_flows."""
    traces = forecast_result['traces']
    if not traces:
        return []
    year = int(forecast_result['forecast_year'])
    fields = {'load_demand_kwh': 1, 'grid_import_kwh': 2,
              'grid_export_kwh': 3, 'direct_use_kwh': 5,
              'battery_discharge_kwh': 7}
    expected_hours = (366 if calendar.isleap(year) else 365) * 24
    if any(tr.shape != (expected_hours, 8) for tr in traces):
        raise ValueError('Monthly balance traces do not match the forecast calendar')
    rows = []
    start = 0
    for month in range(1, 13):
        stop = start + calendar.monthrange(year, month)[1] * 24
        row = {'month': month}
        for field, column in fields.items():
            row[field] = round(float(np.mean([
                tr[start:stop, column].sum() for tr in traces
            ])), 6)
        rows.append(row)
        start = stop
    return rows


@lru_cache(maxsize=64)
def _dispatch_matrix_cached(runtime_key, key, price_version, mode, scen, representative):
    assert_runtime_key(runtime_key)
    s = thaw_settings(dict(key))
    _full = defaults(); _full.update(s)
    resolved_route = resolve_routing_priority(_full)
    s['routing_priority'] = resolved_route
    res = _forecast_2027_runs(key, price_version)
    ri = res['picks'][representative]
    trace = res['traces'][ri]
    raw_pv = np.asarray(res['mapped_data']['mapped_pv'][ri], dtype=float)
    chosen_annual = res['annual'][ri]
    chosen_weather_year = int(chosen_annual['weather_year'])
    year = res['forecast_year']
    is_leap = calendar.isleap(year)
    days = 366 if is_leap else 365

    start_date = dt.date(year, 1, 1)
    months_per_day = [(start_date + dt.timedelta(days=d)).month for d in range(days)]

    grid_import_mat = np.zeros((12, 24))
    grid_export_mat = np.zeros((12, 24))
    clipping_mat = np.zeros((12, 24))
    pv_mat = np.zeros((12, 24))
    load_mat = np.zeros((12, 24))
    soc_mat = np.zeros((12, 24))
    direct_mat = np.zeros((12, 24))

    trace_reshaped = trace.reshape(days, 24, 8)
    raw_pv_reshaped = raw_pv.reshape(days, 24)
    # In AC-equivalent mode dispatch clips at the inverter ceiling before
    # charging; DC-coupled mode clips only PV that remains after battery charge
    # and AC export. Use the dispatch result so diagnostics match either mode.
    # Use the PVGIS AC-equivalent reference consistently in public PV flows.
    # The chronological trace remains on its original physical hybrid ledger.
    factor = s['inverter_efficiency'] if (s['pv_dispatch_model']=='physical'
              and s['pv_battery_coupling']=='dc_coupled' and s['battery_kwh']>0) else 1.
    pv_equivalent = trace_reshaped[:, :, 0] - (1-factor)*trace_reshaped[:, :, 6]
    clipping = np.maximum(raw_pv_reshaped - pv_equivalent, 0.0)
    pzo = res['mapped_data']['mapped_pzo'][ri].reshape(days, 24)
    from .soc_statistics import monthly_peak_soc_statistics
    peak_statistics = monthly_peak_soc_statistics(
        res['traces'], np.asarray(months_per_day)-1, s['battery_kwh'])
    all_traces = [t.reshape(days, 24, 8) for t in res['traces']]
    mapped_pv_list = res['mapped_data']['mapped_pv']
    weather_count = len(all_traces)
    raw_pv_all = [np.asarray(mapped_pv_list[wi], dtype=float).reshape(days, 24) for wi in range(weather_count)]
    pv_eq_all = [all_traces[wi][:, :, 0] - (1.0 - factor) * all_traces[wi][:, :, 6] for wi in range(weather_count)]
    clip_all = [np.maximum(raw_pv_all[wi] - pv_eq_all[wi], 0.0) for wi in range(weather_count)]
    monthly_clipping = []
    for m in range(1, 13):
        m_days = [d for d in range(days) if months_per_day[d] == m]
        if m_days:
            sub = trace_reshaped[m_days]
            raw_sub = raw_pv_reshaped[m_days]
            clip_sub = clipping[m_days]
            pv_mat[m - 1] = pv_equivalent[m_days].mean(axis=0)
            load_mat[m - 1] = sub[:, :, 1].mean(axis=0)
            grid_import_mat[m - 1] = sub[:, :, 2].mean(axis=0)
            grid_export_mat[m - 1] = sub[:, :, 3].mean(axis=0)
            clipping_mat[m - 1] = clipping[m_days].mean(axis=0)
            soc_mat[m - 1] = np.median(sub[:, :, 4], axis=0)
            direct_mat[m - 1] = sub[:, :, 5].mean(axis=0)
            if weather_count:
                raw_sums = [float(raw_pv_all[wi][m_days].sum()) for wi in range(weather_count)]
                clip_sums = [float(clip_all[wi][m_days].sum()) for wi in range(weather_count)]
                clip_hrs = [float(np.count_nonzero(clip_all[wi][m_days] > 1e-9)) for wi in range(weather_count)]
                mean_raw_input = float(np.mean(raw_sums))
                mean_clipped = float(np.mean(clip_sums))
                input_kwh = round(mean_raw_input, 6)
                clipped_kwh = round(mean_clipped, 6)
                clipping_hours = round(float(np.mean(clip_hrs)), 6)
                peak_curtailed_kw = round(float(max(clip_all[wi][m_days].max(initial=0.0) for wi in range(weather_count))), 2)
            else:
                mean_raw_input = mean_clipped = input_kwh = clipped_kwh = clipping_hours = peak_curtailed_kw = 0.0
            monthly_clipping.append(dict(
                **peak_statistics[m-1],
                month=m,
                pv_input_kwh=input_kwh,
                mean_peak_soc_kwh=round(float(sub[:, :, 4].max(axis=1).mean()), 2),
                mean_peak_soc_pct=(round(100.0 * float(sub[:, :, 4].max(axis=1).mean()) / s['battery_kwh'], 4)
                                   if s['battery_kwh'] > 0 else None),
                export_price_weighted_kwh=float((sub[:, :, 3] * pzo[m_days]).sum()),
                grid_import_kwh=round(float(sub[:, :, 2].sum()), 6),
                grid_export_kwh=round(float(sub[:, :, 3].sum()), 6),
                scheduled_active=bool(resolved_route == 'scheduled_hybrid' and scheduled_month_active(m, int(s.get('scheduled_start_month', 3) or 3), int(s.get('scheduled_end_month', 9) or 9))),
                pv_delivered_kwh=round(input_kwh - clipped_kwh, 6),
                clipped_kwh=clipped_kwh,
                clipped_pct=round(100.0 * mean_clipped / mean_raw_input, 2) if mean_raw_input > 0 else 0.0,
                clipping_hours=clipping_hours,
                peak_curtailed_kw=peak_curtailed_kw,
            ))

    months_struct = []
    for m in range(12):
        row = []
        for h in range(24):
            row.append({
                'month': m + 1,
                'hour': h,
                'pv': float(round(pv_mat[m, h], 4)),
                'load': float(round(load_mat[m, h], 4)),
                'import': float(round(grid_import_mat[m, h], 4)),
                'export': float(round(grid_export_mat[m, h], 4)),
                'clipping': float(round(clipping_mat[m, h], 4)),
                'soc': float(round(soc_mat[m, h], 4)),
                'direct': float(round(direct_mat[m, h], 4)),
                'weather_year': chosen_weather_year,
                'statistic': 'chosen_weather_analogue_mean',
                'timezone': 'UTC+01:00'
            })
        months_struct.append(row)

    all_traces = [t.reshape(days, 24, 8) for t in res['traces']]
    season_months = {
        'winter': [11, 0, 1],
        'spring': [2, 3, 4],
        'summer': [5, 6, 7],
        'autumn': [8, 9, 10],
        'annual': list(range(12))
    }
    seasons_struct = {}
    weather_count = len(all_traces)
    for s_name, s_m_indices in season_months.items():
        s_days = [d for d in range(days) if (months_per_day[d] - 1) in s_m_indices]
        s_hours = []
        for h in range(24):
            # Evaluate across all historical weather traces for cross-weather means and percentiles
            pv_weather = [float((tr[s_days, h, 0]-(1-factor)*tr[s_days, h, 6]).mean()) for tr in all_traces]
            load_weather = [float(tr[s_days, h, 1].mean()) for tr in all_traces]
            import_weather = [float(tr[s_days, h, 2].mean()) for tr in all_traces]
            export_weather = [float(tr[s_days, h, 3].mean()) for tr in all_traces]
            soc_weather = [float(tr[s_days, h, 4].mean()) for tr in all_traces]
            direct_weather = [float(tr[s_days, h, 5].mean()) for tr in all_traces]
            s_hours.append({
                'hour': h,
                'pv': round(float(np.mean(pv_weather)), 4),
                'load': round(float(np.mean(load_weather)), 4),
                'import': round(float(np.mean(import_weather)), 4),
                'export': round(float(np.mean(export_weather)), 4),
                'soc': round(float(np.mean(soc_weather)), 4),
                'direct': round(float(np.mean(direct_weather)), 4),
                'pv_envelope': [round(float(np.percentile(pv_weather, 10)), 4), round(float(np.percentile(pv_weather, 90)), 4)],
                'load_envelope': [round(float(np.percentile(load_weather, 10)), 4), round(float(np.percentile(load_weather, 90)), 4)],
                'import_envelope': [round(float(np.percentile(import_weather, 10)), 4), round(float(np.percentile(import_weather, 90)), 4)],
                'export_envelope': [round(float(np.percentile(export_weather, 10)), 4), round(float(np.percentile(export_weather, 90)), 4)],
                'soc_envelope': [round(float(np.percentile(soc_weather, 10)), 4), round(float(np.percentile(soc_weather, 90)), 4)],
                'direct_envelope': [round(float(np.percentile(direct_weather, 10)), 4), round(float(np.percentile(direct_weather, 90)), 4)],
                'statistic': f'flow_scalars: mean of {weather_count} weather day-weighted means; soc: mean of {weather_count} weather day-weighted means; envelopes: P10-P90 across {weather_count} weather years',
                'weather_count': weather_count
            })
        seasons_struct[s_name] = s_hours

    charge_tot = float(trace_reshaped[:, :, 6].sum())
    discharge_tot = float(trace_reshaped[:, :, 7].sum())
    eta_inv = float(s['inverter_efficiency'])
    effective_dc = bool(s['pv_dispatch_model']=='physical' and s['pv_battery_coupling'] == 'dc_coupled' and s['battery_kwh'] > 0)
    charge_split = charge_source_split(raw_pv, trace_reshaped[:, :, 6].reshape(-1), effective_dc,
                                       float(s['inverter_kw']), eta_inv)
    ceiling_series = soc_ceiling_series(raw_pv, np.asarray(res['mapped_data']['mapped_load'][ri], float),
                                        float(s['battery_kwh']), float(s['charge_kw']),
                                        float(s['inverter_kw']), eta_inv,
                                        float(s['battery_charge_efficiency'] or math.sqrt(s['roundtrip_efficiency'])),
                                        effective_dc, physical_parameters(s),
                                        res['mapped_data']['target_months'], res['mapped_data']['target_std_hours'])
    ceiling_reshaped = np.asarray(ceiling_series, float).reshape(days, 24)
    ceiling_mat = np.zeros((12, 24))
    for m in range(1, 13):
        m_days = [d for d in range(days) if months_per_day[d] == m]
        if m_days:
            ceiling_mat[m - 1] = ceiling_reshaped[m_days].mean(axis=0)
    charge_flat = trace_reshaped[:, :, 6].reshape(-1)
    if effective_dc:
        rec_flat = np.minimum(np.maximum(0.0, (raw_pv - float(s['inverter_kw'])) / eta_inv),
                              np.maximum(charge_flat, 0.0))
    else:
        rec_flat = np.zeros_like(charge_flat)
    rec_reshaped = rec_flat.reshape(days, 24)
    for entry in monthly_clipping:
        _m = entry['month']
        _idx = [d for d in range(days) if months_per_day[d] == _m]
        entry['charged_from_recaptured_clipping_kwh'] = round(float(rec_reshaped[_idx].sum()), 1)
        entry['charged_from_normal_solar_kwh'] = round(float(trace_reshaped[_idx, :, 6].sum() - rec_reshaped[_idx].sum()), 1)
    for _m in range(12):
        for _h in range(24):
            months_struct[_m][_h]['soc_ceiling_sub_kwh'] = float(round(ceiling_mat[_m, _h], 4))
    ceiling_weather=[]
    for wi,tr in enumerate(res['traces']):
        ceilings,_=_trace_routing_telemetry(s,res['mapped_data']['mapped_pv'][wi],
                                          res['mapped_data']['mapped_load'][wi],tr,
                                          res['mapped_data']['target_months'],res['mapped_data']['target_std_hours'])
        ceiling_weather.append(ceilings.reshape(days,24))
    for name,season_month_indices in season_months.items():
        selected_days=[di for di in range(days) if months_per_day[di]-1 in season_month_indices]
        for h in range(24):
            seasons_struct[name][h]['soc_ceiling_sub_kwh']=round(float(np.mean(
                [float(c[selected_days,h].mean()) for c in ceiling_weather])),4)
    true_losses = float(chosen_annual['battery_loss_kwh'])
    end_soc = float(chosen_annual['end_soc_kwh'])
    initial_soc = 0.0
    storage_delta = end_soc - initial_soc
    clipped = float(chosen_annual.get('clipped_kwh', chosen_annual['pv_input_kwh'] - chosen_annual['pv_kwh']))
    pv_input = float(chosen_annual['pv_input_kwh'])
    clipping_hours = int(np.count_nonzero(clipping > 1e-9))
    peak_curtailed_kw = float(clipping.max(initial=0.0))

    analogue_annual_flows = dict(
        energy_reference='ac_equivalent',
        physical_charge_basis='DC' if effective_dc else 'AC',
        pv_generation_kwh=round(round(pv_input, 1)-round(clipped, 1), 1),
        pv_input_kwh=round(pv_input, 1),
        load_demand_kwh=round(float(chosen_annual['load_kwh']), 1),
        grid_import_kwh=round(float(chosen_annual['grid_import_kwh']), 1),
        grid_export_kwh=round(float(chosen_annual['grid_export_kwh']), 1),
        direct_use_kwh=round(float(chosen_annual['direct_kwh']), 1),
        battery_charge_kwh=round(factor*charge_tot, 1),
        battery_discharge_kwh=round(discharge_tot, 1),
        storage_losses_kwh=round(factor*true_losses-(1-factor)*discharge_tot, 1),
        weather_year=chosen_weather_year,
        initial_soc_kwh=round(factor*initial_soc, 1),
        end_soc_kwh=round(factor*end_soc, 1),
        storage_delta_kwh=round(factor*storage_delta, 1),
        clipped_kwh=round(clipped, 1),
        clipped_pct=round(100.0 * clipped / pv_input, 2) if pv_input > 0 else 0.0,
        clipping_hours=clipping_hours,
        peak_curtailed_kw=round(peak_curtailed_kw, 2),
        charged_from_normal_solar_kwh=round(factor*charge_split['charged_from_normal_solar_kwh'], 1),
        charged_from_recaptured_clipping_kwh=round(factor*charge_split['charged_from_recaptured_clipping_kwh'], 1),
        battery_charge_physical_kwh=round(charge_tot, 1),
        storage_losses_physical_kwh=round(true_losses, 1),
        initial_soc_stored_kwh=round(initial_soc, 1),
        end_soc_stored_kwh=round(end_soc, 1),
        storage_delta_stored_kwh=round(storage_delta, 1),
        charged_from_normal_solar_physical_kwh=round(charge_split['charged_from_normal_solar_kwh'], 1),
        charged_from_recaptured_clipping_physical_kwh=round(charge_split['charged_from_recaptured_clipping_kwh'], 1),
        routing_priority=resolved_route,
        routing_switch_hour=float(s.get('routing_switch_hour', 11) or 11),
        scheduled_start_month=int(s.get('scheduled_start_month', 3) or 3),
        scheduled_end_month=int(s.get('scheduled_end_month', 9) or 9),
        aggregation='Coherent single-weather analogue (median PV yield); AC-equivalent flows and stored delta satisfy PV=direct+charge+export, load=direct+delivered+grid, and charge-delivered=storage_losses+storage_delta',
        aggregation_note='Coherent single-weather analogue (median PV yield); AC-equivalent flows and stored delta satisfy PV=direct+charge+export, load=direct+delivered+grid, and charge-delivered=storage_losses+storage_delta'
    )
    annual_flows = _ensemble_annual_flows(s, res)

    return dict(
        year=year,
        mode=mode,
        scenario=scen,
        representative=representative,
        weather_year=chosen_weather_year,
        weather_count=weather_count,
        selected_config={'inverter_kw': float(s['inverter_kw']), 'pv_battery_coupling': s['pv_battery_coupling'], 'charge_kw': float(s['charge_kw']), 'requested_pv_battery_coupling': s['pv_battery_coupling'], 'effective_dc_coupled': effective_dc, 'routing_priority': resolved_route, 'routing_switch_hour': float(s.get('routing_switch_hour', 11) or 11), 'scheduled_start_month': int(s.get('scheduled_start_month', 3) or 3), 'scheduled_end_month': int(s.get('scheduled_end_month', 9) or 9)},
        timezone='UTC+01:00 (fixed local standard time)',
        statistic=f'season_flows: mean across {weather_count} historical weather day-weighted means; season_soc: mean across {weather_count} historical weather day-weighted means; months: chosen weather analogue means',
        months=list(range(1, 13)),
        hours=list(range(24)),
        grid_import_kwh=grid_import_mat.round(4).tolist(),
        grid_export_kwh=grid_export_mat.round(4).tolist(),
        pv_kwh=pv_mat.round(4).tolist(),
        load_kwh=load_mat.round(4).tolist(),
        soc_kwh=soc_mat.round(4).tolist(),
        soc_ceiling_sub_kwh=ceiling_mat.round(4).tolist(),
        direct_kwh=direct_mat.round(4).tolist(),
        matrix=dict(months=months_struct, seasons=seasons_struct),
        monthly_clipping=monthly_clipping,
        monthly_balance=_ensemble_monthly_balance(res),
        annual_flows=annual_flows,
        analogue_annual_flows=analogue_annual_flows
    )


def dispatch_matrix(settings=None, mode='forecast_2027', scenario=None, representative='median'):
    """Compute 12-month by 24-hour dispatch matrix using cached forecast dispatches."""
    if mode not in ('forecast_2027', 'forecast'):
        raise ValueError(f"mode must be forecast or forecast_2027, got '{mode}'")
    requested = validate(settings)
    scen = scenario or requested['load_scenario']
    if scen not in ('baseline', 'electrified', 'ev'):
        raise ValueError('scenario must be baseline, electrified or ev')
    s = _routing_settings(validate({**requested, 'load_scenario': scen}))
    if representative not in ('low', 'median', 'high'):
        raise ValueError('representative must be low, median or high')
    key = _energy_key(s)
    version = price_fingerprint(s)
    res = _dispatch_matrix_cached(current_runtime().key, key, version, mode, scen, representative)
    res = deepcopy(res)
    from .schedule_optimizer import switch_hour_tradeoff
    res['schedule_optimizer'] = switch_hour_tradeoff(s)
    return res


def summary_sensitivity(settings=None, quote=None, scope='solar', cash_mode='cash'):
    """Evaluate fourteen NPV drivers and joint all-adverse scenario for the selected scope and financing basis."""
    if scope not in ('solar', 'package', 'project'):
        raise ValueError(f"scope must be 'solar', 'package', or 'project', got '{scope}'")
    if cash_mode not in ('cash', 'loan'):
        raise ValueError(f"cash_mode must be 'cash' or 'loan', got '{cash_mode}'")
    s, q, _, _ = normalize_quote(settings, quote)
    base_res = evaluate(s)

    def get_npv(r):
        f = r['finance']
        if scope == 'package':
            key = 'package_equity' if cash_mode == 'loan' else 'whole_package'
        else:
            key = 'solar_equity' if cash_mode == 'loan' else 'pv_incremental'
        m = f.get(key, f['pv_incremental'])
        return dict(
            mean=float(m['npv']['mean']),
            median=float(m['npv']['median']),
            min=float(m['npv']['min']),
            max=float(m['npv']['max']),
            by_weather=m.get('npv_by_weather', [])
        )

    base_npv = get_npv(base_res)

    comm_bounds = SPEC['retail_commodity'][2]
    comm_min = comm_bounds[0] if comm_bounds[0] is not None else 0.0
    comm_max = comm_bounds[1] if comm_bounds[1] is not None else float('inf')
    comm = s['retail_commodity']
    low_comm = round(max(comm_min, min(comm * 0.8, comm_max)), 4)
    high_comm = round(max(comm_min, min(comm * 1.2, comm_max)), 4)
    r_low_p = evaluate({**s, 'retail_commodity': low_comm})
    r_high_p = evaluate({**s, 'retail_commodity': high_comm})
    low_price_npv = get_npv(r_low_p)
    high_price_npv = get_npv(r_high_p)

    disc_bounds = SPEC['real_discount_rate'][2]
    disc_min = disc_bounds[0] if disc_bounds[0] is not None else 0.0
    disc_max = disc_bounds[1] if disc_bounds[1] is not None else float('inf')
    disc = s['real_discount_rate']
    low_disc = round(max(disc_min, min(disc - 0.02, disc_max)), 4)
    high_disc = round(max(disc_min, min(disc + 0.02, disc_max)), 4)
    r_low_d = evaluate({**s, 'real_discount_rate': low_disc})
    r_high_d = evaluate({**s, 'real_discount_rate': high_disc})
    low_disc_npv = get_npv(r_low_d)
    high_disc_npv = get_npv(r_high_d)

    weather_years = [int(y) for y in base_res.get('weather_years', data()['years'])]
    by_weather = base_npv['by_weather']
    worst_idx = int(np.argmin(by_weather))
    best_idx = int(np.argmax(by_weather))
    worst_weather_year = weather_years[worst_idx]
    best_weather_year = weather_years[best_idx]


    def bounded_evaluation(params):
        params = dict(params)
        for name, value in params.items():
            bounds = SPEC[name][2] if name in SPEC else None
            if isinstance(value, (int, float)) and not isinstance(value, bool) and bounds and len(bounds) == 2:
                low, high = bounds
                if isinstance(low, (int, float)): value = max(low, value)
                if isinstance(high, (int, float)): value = min(high, value)
                params[name] = value
        return evaluate(params)

    # 4. Export price ramp-down (actual_price_future_factor) clamped to SPEC bounds
    ramp_bounds = SPEC['actual_price_future_factor'][2]
    ramp_min = ramp_bounds[0] if ramp_bounds[0] is not None else 0.3
    ramp_max = ramp_bounds[1] if ramp_bounds[1] is not None else 2.0
    curr_factor = s.get('actual_price_future_factor', 1.0)
    low_ramp = round(max(ramp_min, min(curr_factor * 0.75, ramp_max)), 2)
    high_ramp = round(max(ramp_min, min(curr_factor * 1.25, ramp_max)), 2)
    r_low_ramp = bounded_evaluation({**s, 'actual_price_future_factor': low_ramp})
    r_high_ramp = bounded_evaluation({**s, 'actual_price_future_factor': high_ramp})
    low_ramp_npv = get_npv(r_low_ramp)
    high_ramp_npv = get_npv(r_high_ramp)

    # 5. Capex (+-10%)
    # Perturb actual full active installed cost incl quote/manual/modeled module overrides/extras
    # and conversion capex for whole-house; normalize_quote provides normalized capex override
    base_solar_capex = base_res['finance']['capex_eur']
    base_conv_capex = base_res['finance']['conversion_capex_eur']
    s_low_c = {**s, 'quote_total': round(base_solar_capex * 0.9, 2), 'quote_vat_included': True}
    s_high_c = {**s, 'quote_total': round(base_solar_capex * 1.1, 2), 'quote_vat_included': True}
    if scope == 'package' and base_conv_capex > 0:
        conversion_keys = (['hp_capex', 'hp_cooking_capex', 'gas_disconnect_cost']
                           if s.get('planning_heat_pump') else ['electrification_capex', 'gas_disconnect_cost'])
        for key in conversion_keys:
            s_low_c[key] = round(float(s[key]) * 0.9, 2)
            s_high_c[key] = round(float(s[key]) * 1.1, 2)

    r_low_capex = bounded_evaluation(s_low_c)
    r_high_capex = bounded_evaluation(s_high_c)
    low_capex_npv = get_npv(r_high_capex)
    high_capex_npv = get_npv(r_low_capex)

    # 6. Replacement cost (+-20%) - include HP replacement for package scope
    inv_rep = s.get('inverter_replacement_cost', 1200.0)
    bat_rep = s.get('battery_replacement_cost_kwh', 250.0)
    hp_rep = s.get('hp_replacement_cost', 6000.0)
    s_low_rep = {
        **s,
        'inverter_replacement_cost': round(inv_rep * 0.8, 2),
        'battery_replacement_cost_kwh': round(bat_rep * 0.8, 2),
        'hp_replacement_cost': round(hp_rep * 0.8, 2),
    }
    s_high_rep = {
        **s,
        'inverter_replacement_cost': round(inv_rep * 1.2, 2),
        'battery_replacement_cost_kwh': round(bat_rep * 1.2, 2),
        'hp_replacement_cost': round(hp_rep * 1.2, 2),
    }
    r_low_rep = bounded_evaluation(s_low_rep)
    r_high_rep = bounded_evaluation(s_high_rep)
    low_rep_npv = get_npv(r_high_rep)
    high_rep_npv = get_npv(r_low_rep)

    # 7. Gas tariff (+-20%) clamped to SPEC bounds
    gas_bounds = SPEC['gas_commodity'][2]
    gas_min = gas_bounds[0] if gas_bounds[0] is not None else 0.0
    gas_max = gas_bounds[1] if gas_bounds[1] is not None else 2.0
    gas_comm = s.get('gas_commodity', 0.47)
    low_gas = round(max(gas_min, min(gas_comm * 0.8, gas_max)), 4)
    high_gas = round(max(gas_min, min(gas_comm * 1.2, gas_max)), 4)
    r_low_g = bounded_evaluation({**s, 'gas_commodity': low_gas})
    r_high_g = bounded_evaluation({**s, 'gas_commodity': high_gas})
    low_gas_npv = get_npv(r_low_g)
    high_gas_npv = get_npv(r_high_g)

    # 8. Battery degradation (calendar/cycle fade +-50%) clamped to SPEC bounds
    bat_kwh = s.get('battery_kwh', 0.0)
    cal_bounds = SPEC['battery_calendar_fade'][2]
    cal_min = cal_bounds[0] if cal_bounds[0] is not None else 0.0
    cal_max = cal_bounds[1] if cal_bounds[1] is not None else 0.08
    cyc_bounds = SPEC['battery_cycle_fade'][2]
    cyc_min = cyc_bounds[0] if cyc_bounds[0] is not None else 0.0
    cyc_max = cyc_bounds[1] if cyc_bounds[1] is not None else 0.0003

    cal_fade = s.get('battery_calendar_fade', 0.015)
    cyc_fade = s.get('battery_cycle_fade', 0.00005)
    if bat_kwh > 0:
        low_cal = round(max(cal_min, min(cal_fade * 0.5, cal_max)), 5)
        high_cal = round(max(cal_min, min(cal_fade * 1.5, cal_max)), 5)
        low_cyc = round(max(cyc_min, min(cyc_fade * 0.5, cyc_max)), 7)
        high_cyc = round(max(cyc_min, min(cyc_fade * 1.5, cyc_max)), 7)
        r_low_deg = bounded_evaluation({**s, 'battery_calendar_fade': low_cal, 'battery_cycle_fade': low_cyc})
        r_high_deg = bounded_evaluation({**s, 'battery_calendar_fade': high_cal, 'battery_cycle_fade': high_cyc})
        low_deg_npv = get_npv(r_high_deg)
        high_deg_npv = get_npv(r_low_deg)
    else:
        low_deg_npv = base_npv
        high_deg_npv = base_npv

    # 9. Heat pump COP (+-20%) clamped to SPEC bounds
    scop_bounds = SPEC['scop'][2]
    scop_min = scop_bounds[0] if scop_bounds[0] is not None else 1.0
    scop_max = scop_bounds[1] if scop_bounds[1] is not None else 8.0
    dhw_bounds = SPEC['dhw_scop'][2]
    dhw_min = dhw_bounds[0] if dhw_bounds[0] is not None else 1.0
    dhw_max = dhw_bounds[1] if dhw_bounds[1] is not None else 8.0

    scop_val = s.get('scop', 4.0)
    dhw_scop_val = s.get('dhw_scop', 3.0)
    low_scop = round(max(scop_min, min(scop_val * 0.8, scop_max)), 2)
    high_scop = round(max(scop_min, min(scop_val * 1.2, scop_max)), 2)
    low_dhw = round(max(dhw_min, min(dhw_scop_val * 0.8, dhw_max)), 2)
    high_dhw = round(max(dhw_min, min(dhw_scop_val * 1.2, dhw_max)), 2)
    r_low_cop = bounded_evaluation({**s, 'scop': low_scop, 'dhw_scop': low_dhw})
    r_high_cop = bounded_evaluation({**s, 'scop': high_scop, 'dhw_scop': high_dhw})
    low_cop_npv = get_npv(r_low_cop)
    high_cop_npv = get_npv(r_high_cop)

    # 10. Real price escalation (+-0.01) clamped to SPEC bounds
    esc_bounds = SPEC['energy_real_escalation'][2]
    esc_min = esc_bounds[0] if esc_bounds[0] is not None else -0.1
    esc_max = esc_bounds[1] if esc_bounds[1] is not None else 0.1
    esc_val = s.get('energy_real_escalation', 0.0)
    low_esc = round(max(esc_min, min(esc_val - 0.01, esc_max)), 4)
    high_esc = round(max(esc_min, min(esc_val + 0.01, esc_max)), 4)
    r_low_esc = bounded_evaluation({**s, 'energy_real_escalation': low_esc})
    r_high_esc = bounded_evaluation({**s, 'energy_real_escalation': high_esc})
    low_esc_npv = get_npv(r_low_esc)
    high_esc_npv = get_npv(r_high_esc)

    # 11. Export price level (+-20%) clamped to SPEC bounds; PMG floor unchanged if elected
    exp_bounds = SPEC['export_market_price'][2]
    exp_min = exp_bounds[0] if exp_bounds[0] is not None else 0.0
    exp_max = exp_bounds[1] if exp_bounds[1] is not None else 0.3
    curr_export = s.get('export_market_price', 0.128)
    low_exp = round(max(exp_min, min(curr_export * 0.8, exp_max)), 4)
    high_exp = round(max(exp_min, min(curr_export * 1.2, exp_max)), 4)
    r_low_exp = bounded_evaluation({**s, 'export_market_price': low_exp})
    r_high_exp = bounded_evaluation({**s, 'export_market_price': high_exp})
    low_exp_npv = get_npv(r_low_exp)
    high_exp_npv = get_npv(r_high_exp)

    # 12. Loan APR (+-0.02) clamped to SPEC bounds
    apr_bounds = SPEC['loan_apr'][2]
    apr_min = apr_bounds[0] if apr_bounds[0] is not None else 0.0
    apr_max = apr_bounds[1] if apr_bounds[1] is not None else 0.3
    apr_val = s.get('loan_apr', 0.051)
    low_apr = round(max(apr_min, min(apr_val - 0.02, apr_max)), 4)
    high_apr = round(max(apr_min, min(apr_val + 0.02, apr_max)), 4)
    r_low_apr = bounded_evaluation({**s, 'loan_apr': low_apr})
    r_high_apr = bounded_evaluation({**s, 'loan_apr': high_apr})
    low_apr_npv = get_npv(r_low_apr)
    high_apr_npv = get_npv(r_high_apr)

    # 13. PV yield (+-10% production), preserving installed capacity and capex.
    yield_factor = s.get(_PV_YIELD_SETTING, 1.0)
    low_yield_factor = max(0.0, yield_factor * 0.9)
    high_yield_factor = min(2.0, yield_factor * 1.1)
    s_low_y = {**s, _PV_YIELD_SETTING: low_yield_factor, 'quote_total': base_solar_capex, 'quote_vat_included': True}
    s_high_y = {**s, _PV_YIELD_SETTING: high_yield_factor, 'quote_total': base_solar_capex, 'quote_vat_included': True}
    r_low_yield = bounded_evaluation(s_low_y)
    r_high_yield = bounded_evaluation(s_high_y)
    low_yield_npv = get_npv(r_low_yield)
    high_yield_npv = get_npv(r_high_yield)

    # 14. Deduction tax capacity (low=0, high=100000) within SPEC bounds
    tax_bounds = SPEC['deduction_tax_capacity'][2]
    tax_min = tax_bounds[0] if tax_bounds[0] is not None else 0.0
    tax_max = tax_bounds[1] if tax_bounds[1] is not None else 100000.0
    tax_cap_val = s.get('deduction_tax_capacity', 0.0)
    low_tax_cap = float(tax_min)
    high_tax_cap = float(tax_max)
    r_low_tax = bounded_evaluation({**s, 'deduction_tax_capacity': low_tax_cap})
    r_high_tax = bounded_evaluation({**s, 'deduction_tax_capacity': high_tax_cap})
    low_tax_npv = get_npv(r_low_tax)
    high_tax_npv = get_npv(r_high_tax)

    # 15. All-adverse together: true joint recomputation from standalone adverse endpoints
    adverse_settings = {}
    adverse_settings['retail_commodity'] = low_comm if low_price_npv['mean'] <= high_price_npv['mean'] else high_comm
    adverse_settings['actual_price_future_factor'] = low_ramp if low_ramp_npv['mean'] <= high_ramp_npv['mean'] else high_ramp

    adv_capex = s_high_c if get_npv(r_high_capex)['mean'] <= get_npv(r_low_capex)['mean'] else s_low_c
    adverse_settings['quote_total'] = adv_capex['quote_total']
    adverse_settings['quote_vat_included'] = adv_capex['quote_vat_included']
    if scope == 'package' and base_conv_capex > 0:
        conversion_keys = (['hp_capex', 'hp_cooking_capex', 'gas_disconnect_cost']
                           if s.get('planning_heat_pump') else ['electrification_capex', 'gas_disconnect_cost'])
        for key in conversion_keys:
            adverse_settings[key] = adv_capex[key]

    adv_rep = s_high_rep if get_npv(r_high_rep)['mean'] <= get_npv(r_low_rep)['mean'] else s_low_rep
    adverse_settings['inverter_replacement_cost'] = adv_rep['inverter_replacement_cost']
    adverse_settings['battery_replacement_cost_kwh'] = adv_rep['battery_replacement_cost_kwh']
    adverse_settings['hp_replacement_cost'] = adv_rep['hp_replacement_cost']

    adverse_settings['gas_commodity'] = low_gas if low_gas_npv['mean'] <= high_gas_npv['mean'] else high_gas

    if bat_kwh > 0:
        if get_npv(r_high_deg)['mean'] <= get_npv(r_low_deg)['mean']:
            adverse_settings['battery_calendar_fade'] = high_cal
            adverse_settings['battery_cycle_fade'] = high_cyc
        else:
            adverse_settings['battery_calendar_fade'] = low_cal
            adverse_settings['battery_cycle_fade'] = low_cyc

    adverse_settings['scop'] = low_scop if low_cop_npv['mean'] <= high_cop_npv['mean'] else high_scop
    adverse_settings['dhw_scop'] = low_dhw if low_cop_npv['mean'] <= high_cop_npv['mean'] else high_dhw

    adverse_settings['energy_real_escalation'] = low_esc if low_esc_npv['mean'] <= high_esc_npv['mean'] else high_esc
    adverse_settings['export_market_price'] = low_exp if low_exp_npv['mean'] <= high_exp_npv['mean'] else high_exp
    adverse_settings['loan_apr'] = low_apr if low_apr_npv['mean'] <= high_apr_npv['mean'] else high_apr
    adverse_settings[_PV_YIELD_SETTING] = low_yield_factor if low_yield_npv['mean'] <= high_yield_npv['mean'] else high_yield_factor
    adverse_settings['deduction_tax_capacity'] = low_tax_cap if low_tax_npv['mean'] <= high_tax_npv['mean'] else high_tax_cap

    # Joint evaluation with all adverse settings applied simultaneously (discount_rate excluded as valuation parameter)
    r_all_adverse = bounded_evaluation({**s, **adverse_settings})
    all_adverse_npv = get_npv(r_all_adverse)

    joint_by_weather = all_adverse_npv['by_weather']
    if joint_by_weather:
        worst_joint_idx = int(np.argmin(joint_by_weather))
        worst_joint_weather_year = weather_years[worst_joint_idx]
    else:
        worst_joint_weather_year = worst_weather_year

    return dict(
        scope=scope,
        cash_mode=cash_mode,
        base_npv=base_npv,
        base_npv_eur=base_npv['mean'],
        electricity_price=dict(
            parameter='retail_commodity',
            unit='EUR/kWh',
            base=dict(price=comm, npv_eur=base_npv['mean']),
            minus_20_pct=dict(
                price=low_comm,
                npv_eur=low_price_npv['mean'],
                delta_npv=round(low_price_npv['mean'] - base_npv['mean'], 2)
            ),
            plus_20_pct=dict(
                price=high_comm,
                npv_eur=high_price_npv['mean'],
                delta_npv=round(high_price_npv['mean'] - base_npv['mean'], 2)
            )
        ),
        energy_price=dict(
            parameter='retail_commodity',
            unit='EUR/kWh',
            base_value=comm,
            low_value=low_comm,
            high_value=high_comm,
            low_npv=low_price_npv['mean'],
            high_npv=high_price_npv['mean'],
            minus_20_pct=low_price_npv['mean'],
            plus_20_pct=high_price_npv['mean'],
            delta_low=round(low_price_npv['mean'] - base_npv['mean'], 2),
            delta_high=round(high_price_npv['mean'] - base_npv['mean'], 2)
        ),
        discount_rate=dict(
            parameter='real_discount_rate',
            unit='%',
            base=dict(rate=disc, npv_eur=base_npv['mean']),
            minus_2_pct=dict(
                rate=low_disc,
                npv_eur=low_disc_npv['mean'],
                delta_npv=round(low_disc_npv['mean'] - base_npv['mean'], 2)
            ),
            plus_2_pct=dict(
                rate=high_disc,
                npv_eur=high_disc_npv['mean'],
                delta_npv=round(high_disc_npv['mean'] - base_npv['mean'], 2)
            ),
            base_value=disc,
            low_value=low_disc,
            high_value=high_disc,
            low_npv=low_disc_npv['mean'],
            high_npv=high_disc_npv['mean'],
            delta_low=round(low_disc_npv['mean'] - base_npv['mean'], 2),
            delta_high=round(high_disc_npv['mean'] - base_npv['mean'], 2)
        ),
        weather=dict(
            worst_year=dict(
                year=str(worst_weather_year),
                weather_year=worst_weather_year,
                npv_eur=base_npv['min'],
                delta_npv=round(base_npv['min'] - base_npv['mean'], 2)
            ),
            mean_year=dict(
                year='Mean',
                npv_eur=base_npv['mean'],
                delta_npv=0.0
            ),
            best_year=dict(
                year=str(best_weather_year),
                weather_year=best_weather_year,
                npv_eur=base_npv['max'],
                delta_npv=round(base_npv['max'] - base_npv['mean'], 2)
            ),
            worst_weather_npv=base_npv['min'],
            median_weather_npv=base_npv['median'],
            mean_weather_npv=base_npv['mean'],
            best_weather_npv=base_npv['max'],
            delta_worst=round(base_npv['min'] - base_npv['mean'], 2),
            delta_best=round(base_npv['max'] - base_npv['mean'], 2),
            worst_weather_year=worst_weather_year,
            best_weather_year=best_weather_year,
            weather_count=len(weather_years),
            statistic='extrema (min/max), not P10/P90 quantiles'
        ),
        export_price_ramp=dict(
            parameter='actual_price_future_factor',
            unit='multiplier',
            base=dict(factor=curr_factor, npv_eur=base_npv['mean']),
            ramp_down=dict(factor=low_ramp, npv_eur=low_ramp_npv['mean'], delta_npv=round(low_ramp_npv['mean'] - base_npv['mean'], 2)),
            ramp_up=dict(factor=high_ramp, npv_eur=high_ramp_npv['mean'], delta_npv=round(high_ramp_npv['mean'] - base_npv['mean'], 2)),
            delta_low=round(low_ramp_npv['mean'] - base_npv['mean'], 2),
            delta_high=round(high_ramp_npv['mean'] - base_npv['mean'], 2)
        ),
        capex=dict(
            parameter='capex',
            unit='EUR',
            base=dict(npv_eur=base_npv['mean']),
            minus_10_pct=dict(npv_eur=high_capex_npv['mean'], delta_npv=round(high_capex_npv['mean'] - base_npv['mean'], 2)),
            plus_10_pct=dict(npv_eur=low_capex_npv['mean'], delta_npv=round(low_capex_npv['mean'] - base_npv['mean'], 2)),
            delta_low=round(low_capex_npv['mean'] - base_npv['mean'], 2),
            delta_high=round(high_capex_npv['mean'] - base_npv['mean'], 2)
        ),
        replacement_cost=dict(
            parameter='replacement_cost',
            unit='EUR',
            base=dict(npv_eur=base_npv['mean']),
            minus_20_pct=dict(npv_eur=high_rep_npv['mean'], delta_npv=round(high_rep_npv['mean'] - base_npv['mean'], 2)),
            plus_20_pct=dict(npv_eur=low_rep_npv['mean'], delta_npv=round(low_rep_npv['mean'] - base_npv['mean'], 2)),
            delta_low=round(low_rep_npv['mean'] - base_npv['mean'], 2),
            delta_high=round(high_rep_npv['mean'] - base_npv['mean'], 2)
        ),
        gas_price=dict(
            parameter='gas_commodity',
            unit='EUR/Smc',
            base=dict(price=gas_comm, npv_eur=base_npv['mean']),
            minus_20_pct=dict(price=low_gas, npv_eur=low_gas_npv['mean'], delta_npv=round(low_gas_npv['mean'] - base_npv['mean'], 2)),
            plus_20_pct=dict(price=high_gas, npv_eur=high_gas_npv['mean'], delta_npv=round(high_gas_npv['mean'] - base_npv['mean'], 2)),
            delta_low=round(low_gas_npv['mean'] - base_npv['mean'], 2),
            delta_high=round(high_gas_npv['mean'] - base_npv['mean'], 2)
        ),
        battery_degradation=dict(
            parameter='battery_calendar_fade',
            unit='fraction/year',
            base=dict(fade=cal_fade, npv_eur=base_npv['mean']),
            higher_fade=dict(npv_eur=low_deg_npv['mean'], delta_npv=round(low_deg_npv['mean'] - base_npv['mean'], 2)),
            lower_fade=dict(npv_eur=high_deg_npv['mean'], delta_npv=round(high_deg_npv['mean'] - base_npv['mean'], 2)),
            delta_low=round(low_deg_npv['mean'] - base_npv['mean'], 2),
            delta_high=round(high_deg_npv['mean'] - base_npv['mean'], 2)
        ),
        heat_pump_cop=dict(
            parameter='scop,dhw_scop',
            unit='kWh heat/kWh electricity',
            base=dict(scop=scop_val, dhw_scop=dhw_scop_val, npv_eur=base_npv['mean']),
            minus_20_pct=dict(scop=low_scop, dhw_scop=low_dhw, npv_eur=low_cop_npv['mean'], delta_npv=round(low_cop_npv['mean'] - base_npv['mean'], 2)),
            plus_20_pct=dict(scop=high_scop, dhw_scop=high_dhw, npv_eur=high_cop_npv['mean'], delta_npv=round(high_cop_npv['mean'] - base_npv['mean'], 2)),
            delta_low=round(low_cop_npv['mean'] - base_npv['mean'], 2),
            delta_high=round(high_cop_npv['mean'] - base_npv['mean'], 2)
        ),
        real_energy_price_escalation=dict(
            parameter='energy_real_escalation',
            unit='fraction/year',
            base=dict(rate=esc_val, npv_eur=base_npv['mean']),
            minus_1_pct=dict(rate=low_esc, npv_eur=low_esc_npv['mean'], delta_npv=round(low_esc_npv['mean'] - base_npv['mean'], 2)),
            plus_1_pct=dict(rate=high_esc, npv_eur=high_esc_npv['mean'], delta_npv=round(high_esc_npv['mean'] - base_npv['mean'], 2)),
            delta_low=round(low_esc_npv['mean'] - base_npv['mean'], 2),
            delta_high=round(high_esc_npv['mean'] - base_npv['mean'], 2)
        ),
        export_price_level=dict(
            parameter='export_market_price',
            unit='EUR/kWh',
            pmg_floor_active=bool(s.get('pmg_election')),
            pmg_price=float(s.get('pmg_price', 0.045)) if s.get('pmg_election') else None,
            base=dict(price=curr_export, npv_eur=base_npv['mean']),
            minus_20_pct=dict(price=low_exp, npv_eur=low_exp_npv['mean'], delta_npv=round(low_exp_npv['mean'] - base_npv['mean'], 2)),
            plus_20_pct=dict(price=high_exp, npv_eur=high_exp_npv['mean'], delta_npv=round(high_exp_npv['mean'] - base_npv['mean'], 2)),
            delta_low=round(low_exp_npv['mean'] - base_npv['mean'], 2),
            delta_high=round(high_exp_npv['mean'] - base_npv['mean'], 2)
        ),
        loan_apr=dict(
            parameter='loan_apr',
            unit='fraction/year',
            base=dict(rate=apr_val, npv_eur=base_npv['mean']),
            minus_2_pct=dict(rate=low_apr, npv_eur=low_apr_npv['mean'], delta_npv=round(low_apr_npv['mean'] - base_npv['mean'], 2)),
            plus_2_pct=dict(rate=high_apr, npv_eur=high_apr_npv['mean'], delta_npv=round(high_apr_npv['mean'] - base_npv['mean'], 2)),
            delta_low=round(low_apr_npv['mean'] - base_npv['mean'], 2),
            delta_high=round(high_apr_npv['mean'] - base_npv['mean'], 2)
        ),
        pv_yield=dict(
            parameter=_PV_YIELD_SETTING,
            unit='multiplier',
            base=dict(multiplier=yield_factor, npv_eur=base_npv['mean']),
            minus_10_pct=dict(multiplier=low_yield_factor, npv_eur=low_yield_npv['mean'], delta_npv=round(low_yield_npv['mean'] - base_npv['mean'], 2)),
            plus_10_pct=dict(multiplier=high_yield_factor, npv_eur=high_yield_npv['mean'], delta_npv=round(high_yield_npv['mean'] - base_npv['mean'], 2)),
            delta_low=round(low_yield_npv['mean'] - base_npv['mean'], 2),
            delta_high=round(high_yield_npv['mean'] - base_npv['mean'], 2)
        ),
        tax_deduction=dict(
            parameter='deduction_tax_capacity',
            unit='EUR/year',
            base=dict(capacity=tax_cap_val, npv_eur=base_npv['mean']),
            low_capacity=dict(capacity=low_tax_cap, npv_eur=low_tax_npv['mean'], delta_npv=round(low_tax_npv['mean'] - base_npv['mean'], 2)),
            high_capacity=dict(capacity=high_tax_cap, npv_eur=high_tax_npv['mean'], delta_npv=round(high_tax_npv['mean'] - base_npv['mean'], 2)),
            delta_low=round(low_tax_npv['mean'] - base_npv['mean'], 2),
            delta_high=round(high_tax_npv['mean'] - base_npv['mean'], 2)
        ),
        all_adverse_together=dict(
            group='combined',
            npv_eur=all_adverse_npv['min'],
            delta_npv=round(all_adverse_npv['min'] - base_npv['mean'], 2),
            delta_low=round(all_adverse_npv['min'] - base_npv['mean'], 2),
            delta_high=round(all_adverse_npv['min'] - base_npv['mean'], 2),
            worst_weather_npv=all_adverse_npv['min'],
            median_weather_npv=all_adverse_npv['median'],
            mean_weather_npv=all_adverse_npv['mean'],
            best_weather_npv=all_adverse_npv['max'],
            weather_year=worst_joint_weather_year,
            worst_weather_year=worst_joint_weather_year,
            applied_settings=dict(adverse_settings)
        )
    )


def validate_sensitivity_options(opts):
    """Validate solar sizing sensitivity scenario options."""
    if opts is None:
        opts = {}
    if not isinstance(opts, dict):
        raise ValueError("sensitivity_options must be a JSON object")
    allowed = {
        'export_revenue_delta_pct',
        'installed_cost_delta_pct',
        'household_consumption_delta_pct',
        'financing_apr_delta_pp'
    }
    unknown = set(opts.keys()) - allowed
    if unknown:
        raise ValueError(f"Unknown sensitivity options: {', '.join(sorted(unknown))}")

    exp_delta = opts.get('export_revenue_delta_pct', 20.0)
    if isinstance(exp_delta, bool) or not isinstance(exp_delta, (int, float)) or not math.isfinite(exp_delta) or exp_delta < 0.0 or exp_delta > 100.0:
        raise ValueError("export_revenue_delta_pct must be a finite number between 0 and 100")

    cost_delta = opts.get('installed_cost_delta_pct', 20.0)
    if isinstance(cost_delta, bool) or not isinstance(cost_delta, (int, float)) or not math.isfinite(cost_delta) or cost_delta < 0.0 or cost_delta > 200.0:
        raise ValueError("installed_cost_delta_pct must be a finite number between 0 and 200")

    cons_delta = opts.get('household_consumption_delta_pct', 10.0)
    if isinstance(cons_delta, bool) or not isinstance(cons_delta, (int, float)) or not math.isfinite(cons_delta) or cons_delta < 0.0 or cons_delta > 90.0:
        raise ValueError("household_consumption_delta_pct must be a finite number between 0 and 90")

    apr_delta = opts.get('financing_apr_delta_pp', 2.0)
    if isinstance(apr_delta, bool) or not isinstance(apr_delta, (int, float)) or not math.isfinite(apr_delta) or apr_delta < 0.0 or apr_delta > 30.0:
        raise ValueError("financing_apr_delta_pp must be a finite number between 0 and 30")

    return {
        'export_revenue_delta_pct': float(exp_delta),
        'installed_cost_delta_pct': float(cost_delta),
        'household_consumption_delta_pct': float(cons_delta),
        'financing_apr_delta_pp': float(apr_delta)
    }


def _freeze_options(opt):
    """Freeze options dictionary into hashable tuples for lru_cache."""
    frozen = []
    for k, v in sorted(opt.items()):
        if isinstance(v, dict):
            frozen.append((k, tuple(sorted(v.items()))))
        else:
            frozen.append((k, v))
    return tuple(frozen)


def _thaw_options(key_options):
    """Restore options tuple into standard dictionary with nested dicts."""
    opt = {}
    for k, v in key_options:
        if k == 'panel_maxima' and isinstance(v, (tuple, list)):
            opt[k] = dict(v)
        elif isinstance(v, tuple) and v and all(isinstance(item, tuple) and len(item) == 2 for item in v):
            opt[k] = dict(v)
        else:
            opt[k] = v
    return opt


def _layout_sig(cand):
    """Return layout signature including integer battery composition and roof counts."""
    if cand is None or cand.get('is_no_install'):
        return (True, 0, 0.0, 0, 0, tuple((r, 0) for r in _search_roofs()))
    roofs = cand.get('roof_counts') or {}
    roof_tuple = tuple((r, int(roofs.get(r, 0))) for r in _candidate_roof_order(cand))
    return (
        False,
        int(cand.get('panel_count', 0)),
        round(float(cand.get('battery_kwh', 0.0)), 4),
        int(cand.get('battery_modules', 0)),
        int(cand.get('battery_secondary_modules', 0)),
        cand.get('settings', {}).get('pv_battery_coupling', cand.get('pv_battery_coupling')),
        roof_tuple
    )


def _compact_candidate(cand, basis='project', is_no_install_baseline=False):
    """Return compact standardized candidate summary for sensitivity winners."""
    if cand is None:
        if not is_no_install_baseline:
            return None
        return {
            'role': 'financial',
            'role_label': 'No installation',
            'is_no_install': True,
            'is_positive_npv': False,
            'panel_count': 0,
            'kwp': 0.0,
            'battery_kwh': 0.0,
            'roof_counts': {r: 0 for r in _search_roofs()},
            'battery_modules': 0,
            'battery_secondary_modules': 0,
            'first_modules': 0,
            'battery_module_kwh': None,
            'battery_secondary_module_kwh': None,
            'battery_mixed_sizes': False,
            'storage_cost_mode': 'purchased',
            'capex_eur': 0.0,
            'solar_capex_eur': 0.0,
            'solar_loan_principal_eur': 0.0,
            'initial_solar_equity_eur': 0.0,
            'loan_principal_eur': 0.0,
            'financial_basis': basis,
            'project_npv_median': 0.0,
            'project_npv_mean': 0.0,
            'equity_npv_median': 0.0,
            'equity_npv_mean': 0.0,
            'npv_median': 0.0,
            'npv_mean': 0.0,
            'project_npv': {'median': 0.0, 'mean': 0.0, 'min': 0.0, 'max': 0.0},
            'equity_npv': {'median': 0.0, 'mean': 0.0, 'min': 0.0, 'max': 0.0},
            'npv': {'median': 0.0, 'mean': 0.0, 'min': 0.0, 'max': 0.0},
            'autarky_median': 0.0,
            'autarky_mean': 0.0,
            'self_consumption_median': 0.0,
            'self_consumption_mean': 0.0,
            'annual_clipped_kwh_mean': 0.0,
            'annual_clipped_pct_mean': 0.0,
            'project_discounted_payback_years': None,
            'equity_discounted_payback_years': None,
            'discounted_payback_years': None,
            'project_discounted_payback_of_mean_cash_flows': None,
            'equity_discounted_payback_of_mean_cash_flows': None,
            'discounted_payback_of_mean_cash_flows': None,
            'project_simple_payback_of_mean_cash_flows': None,
            'equity_simple_payback_of_mean_cash_flows': None,
            'simple_payback_of_mean_cash_flows': None,
            'coverage_fraction': 0.0,
            'coverage_pct': 0.0,
            'layout_str': '0 panels · 0.00 kWp · 0.0 kWh',
        }

    if cand.get('is_no_install') or cand.get('id') == 'no_install' or cand.get('panel_count', 0) == 0:
        return {
            'role': cand.get('role', 'financial'),
            'role_label': cand.get('role_label', 'No installation'),
            'is_no_install': True,
            'is_positive_npv': False,
            'panel_count': 0,
            'kwp': 0.0,
            'battery_kwh': 0.0,
            'roof_counts': {r: 0 for r in _search_roofs()},
            'battery_modules': 0,
            'battery_secondary_modules': 0,
            'first_modules': 0,
            'battery_module_kwh': cand.get('battery_module_kwh'),
            'battery_secondary_module_kwh': cand.get('battery_secondary_module_kwh'),
            'battery_mixed_sizes': cand.get('battery_mixed_sizes', False),
            'storage_cost_mode': cand.get('storage_cost_mode', 'purchased'),
            'capex_eur': 0.0,
            'solar_capex_eur': 0.0,
            'solar_loan_principal_eur': 0.0,
            'initial_solar_equity_eur': 0.0,
            'loan_principal_eur': 0.0,
            'financial_basis': basis,
            'project_npv_median': 0.0,
            'project_npv_mean': 0.0,
            'equity_npv_median': 0.0,
            'equity_npv_mean': 0.0,
            'npv_median': 0.0,
            'npv_mean': 0.0,
            'project_npv': {'median': 0.0, 'mean': 0.0, 'min': 0.0, 'max': 0.0},
            'equity_npv': {'median': 0.0, 'mean': 0.0, 'min': 0.0, 'max': 0.0},
            'npv': {'median': 0.0, 'mean': 0.0, 'min': 0.0, 'max': 0.0},
            'autarky_median': 0.0,
            'autarky_mean': 0.0,
            'self_consumption_median': 0.0,
            'self_consumption_mean': 0.0,
            'annual_clipped_kwh_mean': 0.0,
            'annual_clipped_pct_mean': 0.0,
            'project_discounted_payback_years': None,
            'equity_discounted_payback_years': None,
            'discounted_payback_years': None,
            'project_discounted_payback_of_mean_cash_flows': None,
            'equity_discounted_payback_of_mean_cash_flows': None,
            'discounted_payback_of_mean_cash_flows': None,
            'project_simple_payback_of_mean_cash_flows': None,
            'equity_simple_payback_of_mean_cash_flows': None,
            'simple_payback_of_mean_cash_flows': None,
            'coverage_fraction': 0.0,
            'coverage_pct': 0.0,
            'layout_str': '0 panels · 0.00 kWp · 0.0 kWh',
        }

    proj_npv = cand.get('project_npv', {})
    eq_npv = cand.get('equity_npv', {})
    proj_med = float(cand.get('project_npv_median', proj_npv.get('median', 0.0)))
    proj_mean = float(cand.get('project_npv_mean', proj_npv.get('mean', proj_med)))
    eq_med = float(cand.get('equity_npv_median', eq_npv.get('median', 0.0)))
    eq_mean = float(cand.get('equity_npv_mean', eq_npv.get('mean', eq_med)))
    npv_med = eq_med if basis == 'after_financing' else proj_med
    npv_mean = eq_mean if basis == 'after_financing' else proj_mean
    selected_npv = eq_npv if basis == 'after_financing' else proj_npv

    autarky_med = float(cand.get('autarky_median', cand.get('autarky', {}).get('median', 0.0)))
    if cand.get('autarky_mean') is not None:
        autarky_mean = float(cand['autarky_mean'])
        cov_frac = autarky_mean
    elif isinstance(cand.get('autarky'), dict) and cand['autarky'].get('mean') is not None:
        autarky_mean = float(cand['autarky']['mean'])
        cov_frac = autarky_mean
    else:
        autarky_mean = autarky_med
        cov_frac = float(cand.get('coverage_fraction', autarky_med))

    if cand.get('self_consumption_mean') is not None:
        self_cons_mean = float(cand['self_consumption_mean'])
    elif isinstance(cand.get('self_consumption'), dict) and cand['self_consumption'].get('mean') is not None:
        self_cons_mean = float(cand['self_consumption']['mean'])
    else:
        self_cons_mean = float(cand.get('self_consumption_median', cand.get('self_consumption', {}).get('median', 0.0)))
    self_cons_med = float(cand.get('self_consumption_median', cand.get('self_consumption', {}).get('median', 0.0)))
    clipped_mean = float(cand.get('annual_clipped_kwh_mean', cand.get('annual_clipped_kwh_median', 0.0)))
    clipped_pct_mean = float(cand.get('annual_clipped_pct_mean', cand.get('annual_clipped_pct_median', 0.0)))

    return {
        'role': cand.get('role'),
        'role_label': cand.get('role_label'),
        'is_no_install': False,
        'is_positive_npv': bool(cand.get('is_positive_npv', npv_mean > 0.0)),
        'panel_count': cand.get('panel_count', 0),
        'kwp': cand.get('kwp', 0.0),
        'battery_kwh': cand.get('battery_kwh', 0.0),
        'roof_counts': cand.get('roof_counts', {}),
        'battery_modules': cand.get('battery_modules', 0),
        'battery_secondary_modules': cand.get('battery_secondary_modules', 0),
        'first_modules': cand.get('first_modules', cand.get('battery_modules', 0)),
        'battery_module_kwh': cand.get('battery_module_kwh'),
        'battery_secondary_module_kwh': cand.get('battery_secondary_module_kwh'),
        'battery_mixed_sizes': cand.get('battery_mixed_sizes', False),
        'storage_cost_mode': cand.get('storage_cost_mode', 'purchased'),
        'capex_eur': float(cand.get('capex_eur', cand.get('solar_capex_eur', 0.0))),
        'solar_capex_eur': float(cand.get('solar_capex_eur', cand.get('capex_eur', 0.0))),
        'solar_loan_principal_eur': float(cand.get('solar_loan_principal_eur', 0.0)),
        'initial_solar_equity_eur': float(cand.get('initial_solar_equity_eur', 0.0)),
        'loan_principal_eur': float(cand.get('loan_principal_eur', 0.0)),
        'financial_basis': basis,
        'project_npv_median': proj_med,
        'project_npv_mean': proj_mean,
        'equity_npv_median': eq_med,
        'equity_npv_mean': eq_mean,
        'npv_median': npv_med,
        'npv_mean': npv_mean,
        'project_npv': {
            'median': proj_med,
            'mean': proj_mean,
            'min': float(proj_npv.get('min', proj_med)),
            'max': float(proj_npv.get('max', proj_med)),
        },
        'equity_npv': {
            'median': eq_med,
            'mean': eq_mean,
            'min': float(eq_npv.get('min', eq_med)),
            'max': float(eq_npv.get('max', eq_med)),
        },
        'npv': {
            'median': npv_med,
            'mean': npv_mean,
            'min': float(selected_npv.get('min', npv_med)),
            'max': float(selected_npv.get('max', npv_med)),
        },
        'autarky_median': autarky_med,
        'autarky_mean': autarky_mean,
        'self_consumption_median': self_cons_med,
        'self_consumption_mean': self_cons_mean,
        'annual_clipped_kwh_mean': clipped_mean,
        'annual_clipped_pct_mean': clipped_pct_mean,
        'project_discounted_payback_years': cand.get('project_discounted_payback_years'),
        'equity_discounted_payback_years': cand.get('equity_discounted_payback_years'),
        'discounted_payback_years': cand.get('discounted_payback_years'),
        'project_discounted_payback_of_mean_cash_flows': cand.get('project_discounted_payback_of_mean_cash_flows', cand.get('project_discounted_payback_years')),
        'equity_discounted_payback_of_mean_cash_flows': cand.get('equity_discounted_payback_of_mean_cash_flows', cand.get('equity_discounted_payback_years')),
        'discounted_payback_of_mean_cash_flows': cand.get('discounted_payback_of_mean_cash_flows', cand.get('discounted_payback_years')),
        'project_simple_payback_of_mean_cash_flows': cand.get('project_simple_payback_of_mean_cash_flows', cand.get('project_simple_payback_years')),
        'equity_simple_payback_of_mean_cash_flows': cand.get('equity_simple_payback_of_mean_cash_flows', cand.get('equity_simple_payback_years')),
        'simple_payback_of_mean_cash_flows': cand.get('simple_payback_of_mean_cash_flows', cand.get('simple_payback_years')),
        'coverage_fraction': cov_frac,
        'coverage_pct': round(cov_frac * 100.0, 1),
        'pv_battery_coupling': cand.get('settings', {}).get('pv_battery_coupling'),
        'routing_priority': cand.get('settings', {}).get('routing_priority'),
        'resolved_routing_priority': cand.get('resolved_routing_priority'),
        'routing_switch_hour': cand.get('settings', {}).get('routing_switch_hour'),
        'layout_str': f"{cand.get('panel_count', 0)} panels · {cand.get('kwp', 0.0):.2f} kWp · {cand.get('battery_kwh', 0.0):.1f} kWh",
    }


def _evaluate_repriced_base_candidate(base_preferred, s_scen, opt_scen, quote_scen=None, basis='project'):
    """Evaluate the exact physical base preferred candidate under scenario settings, canonical options, and quote overrides."""
    if not base_preferred or base_preferred.get('is_no_install') or base_preferred.get('panel_count', 0) == 0:
        return None

    raw_s = {} if s_scen is None else s_scen
    s_user = validate(raw_s)
    if quote_scen is not None:
        s_norm, q_norm, itemized_total, populated = normalize_quote(s_user, quote_scen)
        quote_active = q_norm
    else:
        s_norm = deepcopy(s_user)
        quote_active = None

    opt = dict(opt_scen or {})
    battery_mode = opt.get('battery_mode', 'joint')
    if battery_mode not in ('joint', 'fixed', 'keep_current'):
        battery_mode = 'joint'
    if battery_mode == 'keep_current':
        battery_mode = 'fixed'

    load_scenario = opt.get('load_scenario', s_norm['load_scenario'])
    if load_scenario not in ('baseline', 'electrified', 'ev'):
        load_scenario = s_norm['load_scenario']
    s = validate({**s_norm, 'load_scenario': load_scenario, 'quote_total': None})

    storage_cost_mode = opt.get('storage_cost_mode', 'purchased')
    if storage_cost_mode not in ('purchased', 'existing'):
        storage_cost_mode = 'purchased'
    if battery_mode != 'fixed' and storage_cost_mode == 'existing':
        storage_cost_mode = 'purchased'

    s = validate({**s, 'storage_cost_mode': storage_cost_mode})

    q10 = opt.get('quote_10kwh_eur')
    if q10 is None and isinstance(quote_active, dict):
        q10 = quote_active.get('quote_10kwh_eur')
    if q10 is not None:
        try: q10 = float(q10)
        except (ValueError, TypeError): q10 = None

    q15 = opt.get('quote_15kwh_eur')
    if q15 is None and isinstance(quote_active, dict):
        q15 = quote_active.get('quote_15kwh_eur')
    if q15 is not None:
        try: q15 = float(q15)
        except (ValueError, TypeError): q15 = None

    ded = opt.get('deduction_rate', s['deduction_rate'])
    try: ded = float(ded)
    except (ValueError, TypeError): ded = 0.5

    clean_opt = {
        **opt,
        'load_scenario': load_scenario,
        'deduction_rate': ded,
        'quote_10kwh_eur': q10,
        'quote_15kwh_eur': q15,
        'storage_cost_mode': storage_cost_mode,
        'battery_mode': battery_mode,
        'financial_basis': basis
    }

    counts = tuple(int(base_preferred.get('roof_counts', {}).get(r, 0)) for r in _search_roofs())
    comp = {
        'battery_kwh': float(base_preferred.get('battery_kwh', 0.0)),
        'battery_modules': int(base_preferred.get('battery_modules', 0)),
        'battery_secondary_modules': int(base_preferred.get('battery_secondary_modules', 0)),
        'first_modules': int(base_preferred.get('first_modules', base_preferred.get('battery_modules', 0) - base_preferred.get('battery_secondary_modules', 0))),
        'battery_module_kwh': base_preferred.get('battery_module_kwh', s.get('battery_module_kwh')),
        'battery_secondary_module_kwh': base_preferred.get('battery_secondary_module_kwh', s.get('battery_secondary_module_kwh')),
        'battery_mixed_sizes': base_preferred.get('battery_mixed_sizes', s.get('battery_mixed_sizes', False)),
    }
    candidate_topology = base_preferred.get('settings', {}).get('pv_battery_coupling', s['pv_battery_coupling'])
    s = {**s, 'pv_dispatch_model': 'physical', 'pv_battery_coupling': candidate_topology}
    if candidate_topology == 'dc_coupled':
        s['battery_inverter_kw'] = 0.
    ilr = s['ilr_dc_max'] if candidate_topology == 'dc_coupled' and comp['battery_kwh']>0 else s['ilr_ac_max']
    if sum(counts)*s['panel_watts']/1000 > ilr*s['inverter_kw'] + 1e-9:
        return None
    cand = _evaluate_solar_candidate(s, clean_opt, counts, comp, quote=quote_active)
    return cand if cand.get('physical_feasible', True) else None


def _find_repriced_base_candidate(base_preferred, s_scen, opt_scen, quote_scen=None, basis='project'):
    """Re-evaluate the exact base preferred candidate under scenario settings."""
    cand = _evaluate_repriced_base_candidate(base_preferred, s_scen, opt_scen, quote_scen=quote_scen, basis=basis)
    if cand is None:
        return _compact_candidate(None, basis=basis, is_no_install_baseline=True)
    return _compact_candidate(cand, basis=basis)


@lru_cache(maxsize=16)
def _solar_sizing_sensitivity_cached(runtime_key, key_settings, key_options, quote_json, price_version, key_sens):
    assert_runtime_key(runtime_key)
    s = thaw_settings(dict(key_settings))
    opt = _thaw_options(key_options)
    sens_opts = dict(key_sens)
    orig_quote = json.loads(quote_json) if quote_json else None
    financial_basis = opt.get('financial_basis', 'project')

    # 1. Base scenario
    base_res = solar_sizing(s, opt, orig_quote)
    base_choices = base_res.get('recommendation_choices', {})
    base_financial = base_choices.get('financial')
    base_preferred = base_financial or base_res.get('recommended_candidate')

    # Prepare scenario parameter checks and definitions
    # A. lower_export
    exp_delta = sens_opts['export_revenue_delta_pct']
    lower_export_price = max(0.0, float(s['export_market_price']) * (1.0 - exp_delta / 100.0))

    # B. higher_installed_cost
    cost_delta = sens_opts['installed_cost_delta_pct']
    c_factor = 1.0 + cost_delta / 100.0
    scaled_pv_fixed = float(s['pv_fixed_cost']) * c_factor
    scaled_pv_cost_kwp = float(s['pv_cost_kwp']) * c_factor
    scaled_bat_cost = float(s['battery_cost_kwh']) * c_factor
    spec_fixed_max = float(SPEC['pv_fixed_cost'][2][1])
    spec_kwp_max = float(SPEC['pv_cost_kwp'][2][1])
    spec_bat_max = float(SPEC['battery_cost_kwh'][2][1])

    exceeded_items = []
    if scaled_pv_fixed > spec_fixed_max + 1e-6:
        exceeded_items.append(f"pv_fixed_cost: {scaled_pv_fixed:.0f} > {spec_fixed_max:.0f} €")
    if scaled_pv_cost_kwp > spec_kwp_max + 1e-6:
        exceeded_items.append(f"pv_cost_kwp: {scaled_pv_cost_kwp:.0f} > {spec_kwp_max:.0f} €/kWp")

    for r in (() if _dynamic_roofs() is not None else LEGACY_ROOFS):
        k = f"{r}_extra_cost_per_panel"
        val = float(s.get(k, 0.0) or 0.0)
        if val > 0:
            scaled_val = val * c_factor
            lim = float(SPEC[k][2][1])
            if scaled_val > lim + 1e-6:
                exceeded_items.append(f"{k}: {scaled_val:.0f} > {lim:.0f} €/panel")

    # Check effective storage cost mode and admissible candidate capacities
    eff_battery_mode = opt.get('battery_mode', 'joint')
    if eff_battery_mode not in ('joint', 'fixed', 'keep_current'):
        eff_battery_mode = 'joint'
    if eff_battery_mode == 'keep_current':
        eff_battery_mode = 'fixed'

    eff_storage_cost_mode = opt.get('storage_cost_mode')
    if eff_storage_cost_mode not in ('purchased', 'existing'):
        eff_storage_cost_mode = s.get('storage_cost_mode', 'purchased')
    if eff_storage_cost_mode not in ('purchased', 'existing'):
        eff_storage_cost_mode = 'purchased'
    if eff_battery_mode != 'fixed' and eff_storage_cost_mode == 'existing':
        eff_storage_cost_mode = 'purchased'

    eval_caps = list(base_res.get('search_summary', {}).get('battery_capacities_evaluated') or [])
    if base_preferred and base_preferred.get('battery_kwh') is not None:
        eval_caps = list(set(eval_caps) | {float(base_preferred['battery_kwh'])})
    pos_caps = [float(c) for c in eval_caps if float(c) > 0.0]

    q10_raw = opt.get('quote_10kwh_eur')
    if q10_raw is None and isinstance(orig_quote, dict):
        q10_raw = orig_quote.get('quote_10kwh_eur')
    q15_raw = opt.get('quote_15kwh_eur')
    if q15_raw is None and isinstance(orig_quote, dict):
        q15_raw = orig_quote.get('quote_15kwh_eur')
    has_quotes = (q10_raw is not None and q15_raw is not None)

    if pos_caps and eff_storage_cost_mode != 'existing':
        if has_quotes:
            q10_pert = float(q10_raw) * c_factor
            q15_pert = float(q15_raw) * c_factor
            ded_rate = float(opt.get('deduction_rate', s.get('deduction_rate', 0.5)))
            vat_mult = 1.0 + (s['installation_vat'] if not s.get('quote_vat_included', False) else 0.0)
            prem_mult = 1.0 + (s['deduction_price_premium'] if ded_rate > 0 else 0.0)

            def _pert_battery_capex(cap):
                if cap <= 0.0: return 0.0
                if cap == 10.0: return q10_pert
                if cap == 15.0: return q15_pert
                slope = (q15_pert - q10_pert) / 5.0
                if cap > 15.0: return q15_pert + (cap - 15.0) * slope
                if 10.0 < cap < 15.0: return q10_pert + (cap - 10.0) * slope
                return cap * (q10_pert / 10.0)

            eff_rates = [_pert_battery_capex(cap) / (cap * vat_mult * prem_mult) for cap in pos_caps]
            max_quote_eff = max(eff_rates) if eff_rates else 0.0
            if max_quote_eff > spec_bat_max + 1e-6:
                exceeded_items.append(f"quote-derived battery_cost_kwh: {max_quote_eff:.0f} > {spec_bat_max:.0f} €/kWh")
        else:
            if scaled_bat_cost > spec_bat_max + 1e-6:
                exceeded_items.append(f"battery_cost_kwh: {scaled_bat_cost:.0f} > {spec_bat_max:.0f} €/kWh")

            for b in ('battery_module_extra_cost', 'battery_secondary_module_extra_cost'):
                val = float(s.get(b, 0.0) or 0.0)
                if val > 0:
                    scaled_val = val * c_factor
                    lim = float(SPEC[b][2][1])
                    if scaled_val > lim + 1e-6:
                        exceeded_items.append(f"{b}: {scaled_val:.0f} > {lim:.0f} €/module")

            for m in ('battery_module_cost_override', 'battery_secondary_module_cost_override'):
                val = s.get(m)
                if val is not None:
                    scaled_val = float(val) * c_factor
                    lim = float(SPEC[m][2][1])
                    if scaled_val > lim + 1e-6:
                        exceeded_items.append(f"{m}: {scaled_val:.0f} > {lim:.0f} €/module")

    cost_exceeds_limits = bool(exceeded_items)
    cost_unavail_reason = f"Perturbed capital costs exceed engine parameter limits ({', '.join(exceeded_items)})." if cost_exceeds_limits else None

    # C. lower_consumption
    cons_delta = sens_opts['household_consumption_delta_pct']
    lower_lg = float(s['load_growth']) * (1.0 - cons_delta / 100.0)
    spec_lg_min, spec_lg_max = SPEC['load_growth'][2]
    lg_exceeds_limits = (lower_lg < float(spec_lg_min) - 1e-6 or lower_lg > float(spec_lg_max) + 1e-6)

    # D. higher_apr
    apr_delta = sens_opts['financing_apr_delta_pp']
    higher_apr_val = float(s['loan_apr']) + apr_delta / 100.0
    spec_apr_min, spec_apr_max = SPEC['loan_apr'][2]
    apr_exceeds_limits = (higher_apr_val < float(spec_apr_min) - 1e-6 or higher_apr_val > float(spec_apr_max) + 1e-6)

    scenarios_meta = [
        ('base', 'Base Scenario',
         'Unperturbed baseline economic assumptions as configured in settings.',
         True, None,
         s, opt, orig_quote,
         {'export_market_price': float(s['export_market_price']), 'pv_fixed_cost': float(s['pv_fixed_cost']),
          'pv_cost_kwp': float(s['pv_cost_kwp']), 'battery_cost_kwh': float(s['battery_cost_kwh']),
          'load_growth': float(s['load_growth']), 'loan_apr': float(s['loan_apr'])}),

        ('lower_export', f"Lower Export Revenue (-{exp_delta:.0f}%)",
         f"Wholesale export market price reduced by {exp_delta:.1f}% "
         f"(from {s['export_market_price']:.4f} €/kWh to {lower_export_price:.4f} €/kWh). "
         f"Retail electricity tariffs are held constant. If PMG election is active (pmg_price = {s.get('pmg_price', 0.04):.4f} €/kWh), "
         f"the regulatory minimum price cushions downside revenue if wholesale prices drop below the floor.",
         True, None,
         {**s, 'export_market_price': lower_export_price}, opt, orig_quote,
         {'export_market_price': lower_export_price, 'pv_fixed_cost': float(s['pv_fixed_cost']),
          'pv_cost_kwp': float(s['pv_cost_kwp']), 'battery_cost_kwh': float(s['battery_cost_kwh']),
          'load_growth': float(s['load_growth']), 'loan_apr': float(s['loan_apr'])}),

        ('higher_installed_cost', f"Higher Installed Cost (+{cost_delta:.0f}%)",
         f"Initial solar installation capital costs increased by {cost_delta:.1f}% "
         f"(fixed cost: {s['pv_fixed_cost']:.0f} € -> {scaled_pv_fixed:.0f} €, "
         f"marginal PV: {s['pv_cost_kwp']:.0f} €/kWp -> {scaled_pv_cost_kwp:.0f} €/kWp, "
         f"battery: {s['battery_cost_kwh']:.0f} €/kWh -> {scaled_bat_cost:.0f} €/kWh, "
         f"including roof placement and module configuration extras). Equipment replacements in project years 10-15 are held fixed in current euros to focus on initial capital risk.",
         not cost_exceeds_limits,
         cost_unavail_reason,
         None, None, None,
         {'export_market_price': float(s['export_market_price']), 'pv_fixed_cost': scaled_pv_fixed,
          'pv_cost_kwp': scaled_pv_cost_kwp, 'battery_cost_kwh': scaled_bat_cost,
          'load_growth': float(s['load_growth']), 'loan_apr': float(s['loan_apr'])}),

        ('lower_consumption', f"Lower Household Consumption (-{cons_delta:.0f}%)",
         f"Baseline household electricity consumption reduced by {cons_delta:.1f}% via the load_growth parameter "
         f"({s['load_growth']:.3f} -> {lower_lg:.3f}). "
         f"This perturbation acts exclusively on measured baseline electricity load, keeping hourly load profiles and optional future heat pump/EV electrification schedules structurally intact.",
         not lg_exceeds_limits,
         f"Perturbed load growth ({lower_lg:.3f}) is outside engine limits [{spec_lg_min:.1f}, {spec_lg_max:.1f}]. Household consumption reduction of {cons_delta:.1f}% exceeds valid modeling range for baseline load_growth ({s['load_growth']:.2f})." if lg_exceeds_limits else None,
         {**s, 'load_growth': lower_lg} if not lg_exceeds_limits else None, opt, orig_quote,
         {'export_market_price': float(s['export_market_price']), 'pv_fixed_cost': float(s['pv_fixed_cost']),
          'pv_cost_kwp': float(s['pv_cost_kwp']), 'battery_cost_kwh': float(s['battery_cost_kwh']),
          'load_growth': lower_lg, 'loan_apr': float(s['loan_apr'])}),

        ('higher_apr', f"Higher Borrowing APR (+{apr_delta:.1f} pp)",
         f"Financing borrowing APR increased by {apr_delta:.1f} percentage points "
         f"({s['loan_apr']*100.0:.2f}% -> {higher_apr_val*100.0:.2f}%). "
         f"Borrowing mode, debt financing rule (debt fraction {s.get('loan_fraction', 0)*100.0:.0f}%), and maturity ({s.get('loan_years', 10)} years) remain fixed, isolating the pure borrowing interest rate sensitivity. In fraction mode, loan principal varies proportionally with candidate capital expenditure.",
         not apr_exceeds_limits,
         f"Perturbed financing APR ({higher_apr_val*100.0:.2f}%) exceeds the maximum allowable borrowing rate ({spec_apr_max*100.0:.1f}%)." if apr_exceeds_limits else None,
         {**s, 'loan_apr': higher_apr_val} if not apr_exceeds_limits else None, opt, orig_quote,
         {'export_market_price': float(s['export_market_price']), 'pv_fixed_cost': float(s['pv_fixed_cost']),
          'pv_cost_kwp': float(s['pv_cost_kwp']), 'battery_cost_kwh': float(s['battery_cost_kwh']),
          'load_growth': float(s['load_growth']), 'loan_apr': higher_apr_val}),
    ]

    out_scenarios = {}
    comparisons = []

    for scen_id, label, desc, is_available, unavail_reason, s_scen, opt_scen, q_scen, econ_params in scenarios_meta:
        economic_assumptions = {
            'export_market_price': econ_params['export_market_price'],
            'pv_fixed_cost': econ_params['pv_fixed_cost'],
            'pv_cost_kwp': econ_params['pv_cost_kwp'],
            'battery_cost_kwh': econ_params['battery_cost_kwh'],
            'load_growth': econ_params['load_growth'],
            'loan_apr': econ_params['loan_apr'],
            'loan_fraction': float(s.get('loan_fraction', 0)),
            'loan_years': int(s.get('loan_years', 10)),
            'pmg_price': float(s.get('pmg_price', 0.04)),
            'pmg_election': bool(s.get('pmg_election', True)),
            'replacements_held_fixed': True
        }

        if not is_available:
            role_status = {
                'financial': {'available': False, 'reason': unavail_reason},
                'balanced': {'available': False, 'reason': unavail_reason},
                'budget': {'available': False, 'reason': unavail_reason},
                'smaller': {'available': False, 'reason': unavail_reason},
            }
            out_scenarios[scen_id] = {
                'scenario_id': scen_id,
                'scenario_label': label,
                'status': 'UNAVAILABLE',
                'unavailable_reason': unavail_reason,
                'perturbation_description': desc,
                'economic_assumptions': economic_assumptions,
                'roles': {'financial': None, 'balanced': None, 'budget': None, 'smaller': None},
                'role_status': role_status,
                'base_preferred_repriced': None,
                'smaller_sacrifice': {'sacrifice_eur': None, 'sacrifice_pct': None},
                'search_summary': None
            }
            comparisons.append({
                'scenario_id': scen_id,
                'label': label,
                'status': 'UNAVAILABLE',
                'unavailable_reason': unavail_reason,
                'financial_panel_count': None,
                'financial_kwp': None,
                'financial_battery_kwh': None,
                'financial_capex_eur': None,
                'financial_npv_median': None,
                'financial_npv_mean': None,
                'financial_project_npv_median': None,
                'financial_project_npv_mean': None,
                'financial_equity_npv_median': None,
                'financial_equity_npv_mean': None,
                'financial_coverage_pct': None,
                'base_preferred_repriced_npv': None,
                'base_preferred_repriced_npv_mean': None,
                'regret_eur': None,
                'changed_layout': None,
                'delta_panels': None,
                'delta_kwp': None,
                'delta_battery_kwh': None,
                'delta_battery_modules': None,
                'delta_battery_secondary_modules': None,
                'delta_roof_counts': None,
                'delta_capex_eur': None
            })
            continue

        try:
            if scen_id == 'base':
                scen_res = base_res
            elif scen_id == 'higher_installed_cost':
                s_cost = deepcopy(s)
                s_cost['pv_fixed_cost'] = scaled_pv_fixed
                s_cost['pv_cost_kwp'] = scaled_pv_cost_kwp
                if not has_quotes and eff_storage_cost_mode != 'existing':
                    s_cost['battery_cost_kwh'] = scaled_bat_cost
                    for b in ('battery_module_extra_cost', 'battery_secondary_module_extra_cost'):
                        s_cost[b] = float(s.get(b, 0.0) or 0.0) * c_factor
                    if s.get('battery_module_cost_override') is not None:
                        s_cost['battery_module_cost_override'] = float(s['battery_module_cost_override']) * c_factor
                    if s.get('battery_secondary_module_cost_override') is not None:
                        s_cost['battery_secondary_module_cost_override'] = float(s['battery_secondary_module_cost_override']) * c_factor
                for r in ('south_extra_cost_per_panel', 'west_extra_cost_per_panel', 'east_extra_cost_per_panel',
                          'dormer_extra_cost_per_panel', 'flat_extra_cost_per_panel', 'secondary_south_extra_cost_per_panel'):
                    s_cost[r] = float(s.get(r, 0.0) or 0.0) * c_factor
                s_cost['storage_cost_mode'] = eff_storage_cost_mode

                opt_cost = deepcopy(opt)
                opt_cost['storage_cost_mode'] = eff_storage_cost_mode
                if opt_cost.get('quote_10kwh_eur') is not None:
                    opt_cost['quote_10kwh_eur'] = float(opt_cost['quote_10kwh_eur']) * c_factor
                if opt_cost.get('quote_15kwh_eur') is not None:
                    opt_cost['quote_15kwh_eur'] = float(opt_cost['quote_15kwh_eur']) * c_factor

                q_cost = deepcopy(orig_quote) if orig_quote else None
                if q_cost:
                    if q_cost.get('line_items'):
                        for item in q_cost['line_items']:
                            if isinstance(item, dict) and item.get('scope', 'solar_storage') == 'solar_storage':
                                if item.get('net_eur') not in (None, ''):
                                    item['net_eur'] = float(item['net_eur']) * c_factor
                    if q_cost.get('quote_total') is not None: q_cost['quote_total'] = float(q_cost['quote_total']) * c_factor
                    if q_cost.get('quote_solar_eur') is not None: q_cost['quote_solar_eur'] = float(q_cost['quote_solar_eur']) * c_factor
                    if q_cost.get('quote_storage_eur') is not None: q_cost['quote_storage_eur'] = float(q_cost['quote_storage_eur']) * c_factor
                    if q_cost.get('quote_10kwh_eur') is not None: q_cost['quote_10kwh_eur'] = float(q_cost['quote_10kwh_eur']) * c_factor
                    if q_cost.get('quote_15kwh_eur') is not None: q_cost['quote_15kwh_eur'] = float(q_cost['quote_15kwh_eur']) * c_factor

                s_scen = s_cost
                opt_scen = opt_cost
                q_scen = q_cost
                scen_res = solar_sizing(s_scen, opt_scen, q_scen)
            else:
                scen_res = solar_sizing(s_scen, opt_scen, q_scen)

            # Exact repricing of the base candidate under this scenario
            repriced_base_cand = _evaluate_repriced_base_candidate(base_preferred, s_scen, opt_scen, quote_scen=q_scen, basis=financial_basis)
        except ValueError as ex:
            if scen_id == 'higher_installed_cost' and ('outside modeling bounds' in str(ex) or 'exceed engine limits' in str(ex) or 'battery_cost_kwh' in str(ex) or 'pv_cost_kwp' in str(ex) or 'pv_fixed_cost' in str(ex)):
                err_reason = f"Perturbed capital costs exceed engine parameter limits ({ex})."
                role_status = {
                    'financial': {'available': False, 'reason': err_reason},
                    'balanced': {'available': False, 'reason': err_reason},
                    'budget': {'available': False, 'reason': err_reason},
                    'smaller': {'available': False, 'reason': err_reason},
                }
                out_scenarios[scen_id] = {
                    'scenario_id': scen_id,
                    'scenario_label': label,
                    'status': 'UNAVAILABLE',
                    'unavailable_reason': err_reason,
                    'perturbation_description': desc,
                    'economic_assumptions': economic_assumptions,
                    'roles': {'financial': None, 'balanced': None, 'budget': None, 'smaller': None},
                    'role_status': role_status,
                    'base_preferred_repriced': None,
                    'smaller_sacrifice': {'sacrifice_eur': None, 'sacrifice_pct': None},
                    'search_summary': None
                }
                comparisons.append({
                    'scenario_id': scen_id,
                    'label': label,
                    'status': 'UNAVAILABLE',
                    'unavailable_reason': err_reason,
                    'financial_panel_count': None,
                    'financial_kwp': None,
                    'financial_battery_kwh': None,
                    'financial_capex_eur': None,
                    'financial_npv_median': None,
                    'financial_npv_mean': None,
                    'financial_project_npv_median': None,
                    'financial_project_npv_mean': None,
                    'financial_equity_npv_median': None,
                    'financial_equity_npv_mean': None,
                    'financial_coverage_pct': None,
                    'base_preferred_repriced_npv': None,
                    'regret_eur': None,
                    'changed_layout': None,
                    'delta_panels': None,
                    'delta_kwp': None,
                    'delta_battery_kwh': None,
                    'delta_battery_modules': None,
                    'delta_battery_secondary_modules': None,
                    'delta_roof_counts': None,
                    'delta_capex_eur': None
                })
                continue
            raise
        repriced_base = _compact_candidate(repriced_base_cand, basis=financial_basis) if repriced_base_cand else _compact_candidate(None, basis=financial_basis, is_no_install_baseline=True)

        candidates_pool = list(scen_res.get('_evaluated_candidates', []))
        search_summary = dict(scen_res.get('search_summary', {}))

        # Incomplete sampling defensiveness:
        # Include exact repriced candidate in scenario pool before selecting all 4 roles
        pool_updated = False
        if repriced_base_cand and not repriced_base['is_no_install']:
            r_sig = _cand_sig(repriced_base_cand)
            if not any(_cand_sig(c) == r_sig for c in candidates_pool):
                candidates_pool.append(repriced_base_cand)
                pool_updated = True

        if pool_updated:
            eval_count = len(candidates_pool)
            valid_count = search_summary.get('valid_candidates_count', eval_count)
            eval_frac = float(round(eval_count / valid_count, 6)) if valid_count > 0 else 0.0
            is_exact = bool(search_summary.get('exact', False)) and (eval_count >= valid_count)
            search_summary['evaluated_count'] = eval_count
            search_summary['evaluated_fraction'] = eval_frac
            search_summary['exact'] = is_exact
            search_summary['certified_global_optimum'] = is_exact
            search_summary['best_found'] = not is_exact
            if 'search_diagnostics' in search_summary and isinstance(search_summary['search_diagnostics'], dict):
                search_summary['search_diagnostics'] = dict(search_summary['search_diagnostics'])
                search_summary['search_diagnostics']['evaluated_count'] = eval_count
                search_summary['search_diagnostics']['evaluated_fraction'] = eval_frac
                search_summary['search_diagnostics']['exact'] = is_exact
                search_summary['search_diagnostics']['certified_global_optimum'] = is_exact

            yield_priority = scen_res.get('search_summary', {}).get('roof_yield_priority') or _search_roofs()
            rec_choices, _ = select_solar_recommendations(
                candidates_pool, yield_priority, {
                    **opt_scen,
                    'exact': is_exact,
                    'settings': s_scen,
                    'annual_grid_import_kwh': scen_res.get('search_summary', {}).get('annual_grid_import_kwh'),
                    'annual_grid_import_kwh_median': scen_res.get('search_summary', {}).get('annual_grid_import_kwh_median'),
                    'weather_count': opt_scen.get('weather_count') or len(data()['years']),
                    'financial_basis': financial_basis,
                }
            )
        else:
            rec_choices = scen_res.get('recommendation_choices', {})

        roles = {
            'financial': _compact_candidate(rec_choices.get('financial'), basis=financial_basis),
            'balanced': _compact_candidate(rec_choices.get('balanced'), basis=financial_basis),
            'budget': _compact_candidate(rec_choices.get('budget'), basis=financial_basis),
            'smaller': _compact_candidate(rec_choices.get('smaller'), basis=financial_basis),
        }

        raw_role_status = rec_choices.get('role_status', {})
        role_status = {
            'financial': {'available': roles['financial'] is not None, 'reason': raw_role_status.get('financial', {}).get('reason') if roles['financial'] is None else None},
            'balanced': {'available': roles['balanced'] is not None, 'reason': raw_role_status.get('balanced', {}).get('reason') if roles['balanced'] is None else None},
            'budget': {'available': roles['budget'] is not None, 'reason': raw_role_status.get('budget', {}).get('reason') if roles['budget'] is None else None},
            'smaller': {'available': roles['smaller'] is not None, 'reason': raw_role_status.get('smaller', {}).get('reason') if roles['smaller'] is None else None},
        }

        fin_role = roles['financial']
        scen_best_npv = fin_role['npv_mean'] if fin_role else 0.0
        repriced_npv = repriced_base['npv_mean'] if repriced_base else 0.0
        regret_eur = max(0.0, round(scen_best_npv - repriced_npv, 2))

        changed_layout = (_layout_sig(fin_role) != _layout_sig(base_preferred))

        base_p_count = base_preferred.get('panel_count', 0) if base_preferred else 0
        base_p_kwp = base_preferred.get('kwp', 0.0) if base_preferred else 0.0
        base_p_bat = base_preferred.get('battery_kwh', 0.0) if base_preferred else 0.0
        base_p_mods = base_preferred.get('battery_modules', 0) if base_preferred else 0
        base_p_sec_mods = base_preferred.get('battery_secondary_modules', 0) if base_preferred else 0
        base_p_roofs = base_preferred.get('roof_counts', {}) if base_preferred else {}

        delta_panels = fin_role['panel_count'] - base_p_count if fin_role else 0
        delta_kwp = round(fin_role['kwp'] - base_p_kwp, 3) if fin_role else 0.0
        delta_battery_kwh = round(fin_role['battery_kwh'] - base_p_bat, 2) if fin_role else 0.0
        delta_battery_modules = fin_role['battery_modules'] - base_p_mods if fin_role else 0
        delta_battery_secondary_modules = fin_role['battery_secondary_modules'] - base_p_sec_mods if fin_role else 0
        delta_roof_counts = {r: (fin_role['roof_counts'].get(r, 0) if fin_role else 0) - base_p_roofs.get(r, 0) for r in _search_roofs()}
        delta_capex_eur = round(fin_role['capex_eur'] - repriced_base['capex_eur'], 2) if fin_role and repriced_base else 0.0

        sm_role = roles['smaller']
        if sm_role and not sm_role['is_no_install'] and fin_role and fin_role['is_positive_npv'] and not fin_role['is_no_install']:
            smaller_sac_eur = max(0.0, round(fin_role['npv_mean'] - sm_role['npv_mean'], 2))
            smaller_sac_pct = round(smaller_sac_eur / fin_role['npv_mean'] * 100.0, 2) if fin_role['npv_mean'] > 0 else 0.0
        else:
            smaller_sac_eur = None
            smaller_sac_pct = None

        is_cov_exact = bool(search_summary.get('exact', False)) and (search_summary.get('evaluated_count', 0) >= search_summary.get('valid_candidates_count', 0))
        search_cov = {
            'evaluated_count': search_summary.get('evaluated_count', 0),
            'valid_candidates_count': search_summary.get('valid_candidates_count', 0),
            'evaluated_fraction': search_summary.get('evaluated_fraction', 0.0),
            'exact': is_cov_exact,
            'best_found': not is_cov_exact if is_cov_exact else search_summary.get('best_found', True),
            'certified_global_optimum': is_cov_exact,
            'optimality_bound': None,
            'optimality_gap': None,
        }

        out_scenarios[scen_id] = {
            'scenario_id': scen_id,
            'scenario_label': label,
            'status': 'SUCCESS',
            'unavailable_reason': None,
            'perturbation_description': desc,
            'economic_assumptions': economic_assumptions,
            'roles': roles,
            'role_status': role_status,
            'base_preferred_repriced': {
                'role': 'financial',
                'layout_str': repriced_base['layout_str'],
                'panel_count': repriced_base['panel_count'],
                'kwp': repriced_base['kwp'],
                'battery_kwh': repriced_base['battery_kwh'],
                'roof_counts': repriced_base['roof_counts'],
                'battery_modules': repriced_base['battery_modules'],
                'battery_secondary_modules': repriced_base['battery_secondary_modules'],
                'capex_eur': repriced_base['capex_eur'],
                'financial_basis': financial_basis,
                'project_npv_median': repriced_base['project_npv_median'],
                'project_npv_mean': repriced_base['project_npv_mean'],
                'equity_npv_median': repriced_base['equity_npv_median'],
                'equity_npv_mean': repriced_base['equity_npv_mean'],
                'npv_median': repriced_base['npv_median'],
                'npv_mean': repriced_base['npv_mean'],
                'project_npv': repriced_base['project_npv'],
                'equity_npv': repriced_base['equity_npv'],
                'npv': repriced_base['npv'],
                'project_discounted_payback_years': repriced_base['project_discounted_payback_years'],
                'equity_discounted_payback_years': repriced_base['equity_discounted_payback_years'],
                'discounted_payback_years': repriced_base['discounted_payback_years'],
                'regret_vs_scenario_financial_best_eur': regret_eur,
                'changed_layout': changed_layout,
                'delta_panels': delta_panels,
                'delta_kwp': delta_kwp,
                'delta_battery_kwh': delta_battery_kwh,
                'delta_battery_modules': delta_battery_modules,
                'delta_battery_secondary_modules': delta_battery_secondary_modules,
                'delta_roof_counts': delta_roof_counts,
                'delta_capex_eur': delta_capex_eur,
            },
            'smaller_sacrifice': {
                'sacrifice_eur': smaller_sac_eur,
                'sacrifice_pct': smaller_sac_pct,
            },
            'search_summary': search_cov,
        }

        comparisons.append({
            'scenario_id': scen_id,
            'label': label,
            'status': 'SUCCESS',
            'unavailable_reason': None,
            'financial_panel_count': fin_role['panel_count'] if fin_role else None,
            'financial_kwp': fin_role['kwp'] if fin_role else None,
            'financial_battery_kwh': fin_role['battery_kwh'] if fin_role else None,
            'financial_capex_eur': fin_role['capex_eur'] if fin_role else None,
            'financial_npv_median': fin_role['npv_median'] if fin_role else None,
            'financial_npv_mean': fin_role['npv_mean'] if fin_role else None,
            'financial_project_npv_median': fin_role['project_npv_median'] if fin_role else None,
            'financial_project_npv_mean': fin_role['project_npv_mean'] if fin_role else None,
            'financial_equity_npv_median': fin_role['equity_npv_median'] if fin_role else None,
            'financial_equity_npv_mean': fin_role['equity_npv_mean'] if fin_role else None,
            'financial_coverage_pct': fin_role['coverage_pct'] if fin_role else None,
            'base_preferred_repriced_npv': repriced_base['npv_mean'] if repriced_base else None,
            'base_preferred_repriced_npv_mean': repriced_base['npv_mean'] if repriced_base else None,
            'regret_eur': regret_eur,
            'changed_layout': changed_layout,
            'delta_panels': delta_panels,
            'delta_kwp': delta_kwp,
            'delta_battery_kwh': delta_battery_kwh,
            'delta_battery_modules': delta_battery_modules,
            'delta_battery_secondary_modules': delta_battery_secondary_modules,
            'delta_roof_counts': delta_roof_counts,
            'delta_capex_eur': delta_capex_eur,
        })

    return {
        'status': 'SUCCESS',
        'financial_basis': financial_basis,
        'sensitivity_options': sens_opts,
        'scenarios': out_scenarios,
        'comparisons': comparisons,
        'economic_parameters': {
            'export_revenue_delta_pct': sens_opts['export_revenue_delta_pct'],
            'installed_cost_delta_pct': sens_opts['installed_cost_delta_pct'],
            'household_consumption_delta_pct': sens_opts['household_consumption_delta_pct'],
            'financing_apr_delta_pp': sens_opts['financing_apr_delta_pp'],
            'pmg_floor_active': bool(s.get('pmg_election', True)),
            'pmg_price': float(s.get('pmg_price', 0.04)),
            'consumption_multiplier_scope': 'baseline measured household electricity load via load_growth (heat pump and EV hourly profiles preserved)',
            'capital_cost_perturbation_scope': 'initial solar PV fixed/marginal costs, roof placement extras, battery module costs/extras, and quote variants (equipment replacements in years 10-15 held fixed)',
            'financing_perturbation_scope': 'loan APR modified; borrowing mode, debt fraction rule, and loan term held fixed (principal scales with candidate capex)'
        },
        'weather_envelope_note': f'All {weather_period()[0]} PVGIS historical weather years ({weather_period()[3]}) are evaluated separately for every economic scenario, preserving full weather uncertainty ranges alongside economic parameter shifts.'
    }


def solar_sizing_sensitivity(settings=None, options=None, quote=None, sensitivity_options=None):
    """Evaluate on-demand economic downside sensitivity scenarios for solar sizing."""
    raw = {} if settings is None else settings
    if not isinstance(raw, dict): raise ValueError('settings must be a JSON object')
    s_user = validate(raw)
    sens_opts = validate_sensitivity_options(sensitivity_options)

    if options is not None and not isinstance(options, dict):
        raise ValueError('options must be a JSON object')
    opt = deepcopy(options or {})

    if 'panel_maxima' in opt:
        if not isinstance(opt['panel_maxima'], dict):
            raise ValueError('panel_maxima must be an object')
        for r, val in opt['panel_maxima'].items():
            if r not in _search_roofs():
                raise ValueError(f"Unknown roof in panel_maxima: {r}")
            if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val) or val < 0 or int(val) != val:
                raise ValueError(f"panel_maxima[{r}] must be a non-negative integer")

    if 'panel_maxima' in opt:
        _search_maxima(s_user, opt['panel_maxima'])
    clean_options = {**opt}
    if 'financial_basis' in clean_options and clean_options['financial_basis'] not in ('project', 'after_financing'):
        raise ValueError("financial_basis must be 'project' or 'after_financing'")

    key_options = _freeze_options(clean_options)
    key_settings = tuple(sorted((k, freeze(v)) for k, v in s_user.items()))
    quote_json = json.dumps(quote, sort_keys=True) if quote is not None else ""
    key_sens = tuple(sorted(sens_opts.items()))
    price_version = price_fingerprint(s_user)

    token = _SIZING_DISPATCH_MEMO.set({'entries': OrderedDict(), 'nbytes': 0})
    try:
        res = _solar_sizing_sensitivity_cached(current_runtime().key, key_settings, key_options, quote_json, price_version, key_sens)
    finally:
        _SIZING_DISPATCH_MEMO.reset(token)
    return deepcopy(res)
