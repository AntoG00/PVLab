"""Physical AC/DC routing for the discrete system designer (hourly time step).

PVGIS is AC-equivalent; dividing by eta_inv reconstructs a DC proxy.
SoC/trace use usable energy above the lower SoC bound. Absolute nominal
SoC is trace[:,4] + soc_min * nominal_kwh. Clipping is reported in
AC-equivalent kWh for compatibility; its fraction equals the DC fraction.

Routing priority is a first-class EMS work mode. Legacy callers that build a
7-tuple ``physical_parameters`` keep working; new callers get an 11-tuple whose
trailing entries carry routing, switch hour, and inclusive month bounds.
Unresolved low-level calls default to ``battery_first``; public scenario
entry points resolve Auto against the financial objective before dispatch.
"""
import calendar
import math
import numpy as np
from numba import njit, prange


ROUTING_AUTO = 'auto'
ROUTING_MODES = ('battery_first', 'grid_first', 'scheduled_hybrid', 'dynamic_reserve')
SCHEDULED_MONTHS = (3, 4, 5, 6, 7, 8, 9)
_DEFAULT_SWITCH_HOUR = 11.0
_MODE_CODE = {name: float(i) for i, name in enumerate(ROUTING_MODES)}


def normalize_routing_priority(value, default='battery_first'):
    """Map an unresolved/unknown routing value onto a concrete fixed mode."""
    if value in ROUTING_MODES:
        return value
    return default


def routing_switch_hour(settings):
    try:
        value = float(settings.get('routing_switch_hour', _DEFAULT_SWITCH_HOUR) or _DEFAULT_SWITCH_HOUR)
    except (TypeError, ValueError):
        value = _DEFAULT_SWITCH_HOUR
    return value


def physical_parameters(s):
    """Return the physical dispatch parameter tuple.

    The historical 7-tuple layout is preserved for the first seven entries and
    four numeric entries are appended (mode, switch hour, start/end month).
    Consumers that only understand seven entries use ``len(params)`` guarding.
    """
    efficiency = math.sqrt(s['roundtrip_efficiency'])
    mode = normalize_routing_priority(s.get('routing_priority', ROUTING_AUTO) if isinstance(s, dict) else ROUTING_AUTO)
    switch = routing_switch_hour(s) if isinstance(s, dict) else _DEFAULT_SWITCH_HOUR
    return (float(s.get('pv_dispatch_model') == 'physical'),
            float(s['inverter_efficiency']),
            float(s['battery_charge_efficiency'] or efficiency),
            float(s['battery_discharge_efficiency'] or efficiency),
            float(s['battery_c_rate_max']), float(s['battery_inverter_kw']),
            float(s['battery_soc_max'] - s['battery_soc_min']),
            float(_MODE_CODE[mode]), float(switch),
            float(scheduled_month(s, 'scheduled_start_month', 3)),
            float(scheduled_month(s, 'scheduled_end_month', 9)))


def scheduled_month(settings, key, default):
    try:
        return max(1, min(12, int(settings.get(key, default))))
    except (TypeError, ValueError, OverflowError):
        return default


@njit(cache=True)
def scheduled_month_active(month, start=3, end=9):
    return start <= month <= end if start <= end else month >= start or month <= end


@njit(cache=True)
def _params_parts(params):
    mode_code = int(params[7]) if len(params) > 7 else 0
    switch = float(params[8]) if len(params) > 8 else _DEFAULT_SWITCH_HOUR
    return mode_code, switch


@njit(cache=True)
def _month_from_doy(doy):
    """Non-leap month (1..12) for a 0-based day-of-year, synthetic calendar fallback."""
    cumulative = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334, 365)
    for m in range(12):
        if doy < cumulative[m + 1]:
            return m + 1
    return 12


@njit(cache=True)
def _over_remaining(over, valid, eta_ch, ch_cap, month, hour, available):
    """Reverse daily sum; invalid/padded hours contribute no capturable energy."""
    out=np.zeros(len(over))
    remaining=0.
    next_hour=-1
    next_month=-1
    next_index=-1
    for t in range(len(over)-1,-1,-1):
        if not valid[t]:
            continue
        hh=hour[t] if hour is not None else t%24
        mm=month[t] if month is not None else _month_from_doy(t//24)
        if next_index>=0:
            boundary=(hh>=next_hour or mm!=next_month or next_index-t>=24)
            if hour is None:
                boundary=boundary or t//24!=next_index//24
            if boundary:
                remaining=0.
        remaining+=min(over[t],ch_cap)*eta_ch
        out[t]=remaining
        next_hour=hh
        next_month=mm
        next_index=t
    return out


@njit(cache=True)
def dispatch_physical_year(pv, load, capacity, charge_limit, discharge_limit,
                           inverter, initial_soc, prices, valid, dc, params,
                           month=None, hour=None, record_trace=True):
    eta, eta_ch, eta_dis = params[1], params[2], params[3]
    c_rate, battery_inverter, window = params[4], params[5], params[6]
    mode_code, switch = _params_parts(params)
    start = int(params[9]) if len(params) > 9 else 3
    end = int(params[10]) if len(params) > 10 else 9
    dc = dc and capacity > 0.0
    out = np.zeros(14)
    trace = np.zeros((len(pv) if record_trace else 0, 8))
    soc = min(max(initial_soc, 0.), capacity)
    out[7] = max(initial_soc-capacity, 0.)
    nominal = capacity/window
    charge_max = min(charge_limit, nominal*c_rate)
    discharge_max = min(discharge_limit, nominal*c_rate)
    # These arrays are independent of SoC. In particular the reverse daily
    # look-ahead never enters the chronological state update below.
    raw = np.maximum(pv, 0.)
    sub = np.minimum(raw, inverter)/eta
    over = np.maximum(raw-inverter, 0.)/eta
    demand = np.maximum(load, 0.)
    surplus = np.maximum(sub-demand/eta, 0.)
    deficit = np.maximum(demand/eta-sub, 0.)
    remaining = np.zeros(len(pv))
    if mode_code == 3 and dc and len(pv):
        remaining = _over_remaining(over, valid, eta_ch, charge_max,
                                    month, hour, valid)
    for t in range(len(pv)):
        if not valid[t]:
            continue
        before = soc
        hh = hour[t] if hour is not None else t % 24
        mm = month[t] if month is not None else _month_from_doy(t//24)
        gate = 1.
        ceiling = capacity
        if mode_code == 1 or (mode_code == 2 and scheduled_month_active(mm, start, end) and hh < switch):
            gate = 0.
            ceiling = 0.
        elif mode_code == 3:
            ceiling = max(0., capacity-remaining[t])
        direct = min(sub[t]*eta, demand[t])
        if dc:
            # Both streams share the same terminal power and energy headroom.
            over_ch = min(over[t], charge_max, max((capacity-before)/eta_ch, 0.))
            mid = before+eta_ch*over_ch
            sub_ch = gate*min(surplus[t], max(charge_max-over_ch, 0.),
                              max((ceiling-mid)/eta_ch, 0.))
            ch_dc = over_ch+sub_ch
            charge_bus = ch_dc
            exported = max((sub[t]-sub_ch)*eta-demand[t], 0.)
            discharge_ceiling_dc = max(inverter/eta-sub[t], 0.)
            clip_dc = over[t]-over_ch
            produced = direct+exported+charge_bus
        else:
            ch_dc = gate*min(surplus[t]*eta*eta, charge_max,
                            battery_inverter*eta, max((ceiling-before)/eta_ch, 0.))
            charge_bus = ch_dc/eta
            exported = surplus[t]*eta-charge_bus
            discharge_ceiling_dc = battery_inverter/eta
            clip_dc = over[t]
            produced = sub[t]*eta
        # Use starting SoC. Surplus and deficit are mutually exclusive; a
        # saturated hybrid inverter also prevents discharge while recapturing.
        dis_dc = min(deficit[t], discharge_max, discharge_ceiling_dc, before*eta_dis)
        discharge_ac = dis_dc*eta
        soc = before+eta_ch*ch_dc-dis_dc/eta_dis
        imported = max(demand[t]-direct-discharge_ac, 0.)
        charge_storage_loss = ch_dc*(1.-eta_ch)
        charge_inverter_loss = 0. if dc else ch_dc*(1./eta-1.)
        discharge_storage_loss = dis_dc*(1./eta_dis-1.)
        discharge_inverter_loss = dis_dc*(1.-eta)
        loss = charge_storage_loss+charge_inverter_loss+discharge_storage_loss+discharge_inverter_loss
        error = abs(produced+imported-demand[t]-exported-loss-(soc-before))
        if soc < -1e-8 or soc > capacity+1e-8:
            raise ValueError('Physical battery SoC constraint failed')
        out[0] += demand[t]
        out[1] += produced
        out[2] += direct
        out[3] += charge_bus
        out[4] += discharge_ac
        out[5] += imported
        out[6] += exported
        out[7] += loss
        out[9] += clip_dc*eta
        out[10] += dis_dc/eta_dis/nominal if nominal > 0 else 0.
        out[11] += exported*prices[t]
        out[13] = max(out[13], error)
        if record_trace:
            trace[t,0] = produced
            trace[t,1] = demand[t]
            trace[t,2] = imported
            trace[t,3] = exported
            trace[t,4] = soc
            trace[t,5] = direct
            trace[t,6] = charge_bus
            trace[t,7] = discharge_ac
    out[8] = soc
    out[12] = capacity
    return out, trace


def charge_source_split(pv, charge_bus, dc_coupled, inverter, eta):
    """Split battery charging into normal solar and recaptured DC clipping.

    Over-inverter DC power is captured before sub-inverter surplus in every
    routing mode, so recaptured clipping equals ``min(over_available, charge)``.
    Units follow the charge bus: DC input for DC coupling, AC input for AC.
    """
    charge = np.asarray(charge_bus, float)
    if not dc_coupled:
        return dict(charged_from_recaptured_clipping_kwh=0.0,
                    charged_from_normal_solar_kwh=float(np.sum(np.maximum(charge, 0.0))))
    pv = np.asarray(pv, float)
    over = np.maximum(0.0, (pv - inverter) / eta)
    recaptured = np.minimum(over, np.maximum(charge, 0.0))
    return dict(charged_from_recaptured_clipping_kwh=float(np.sum(recaptured)),
                charged_from_normal_solar_kwh=float(np.sum(np.maximum(charge, 0.0) - recaptured)))


def soc_ceiling_series(pv, load, capacity, charge_limit, inverter, eta, eta_ch, dc,
                       params, month=None, hour=None, valid=None):
    """Per-hour sub-inverter SoC ceiling ``SoC_ceil,sub(t)`` for observability."""
    n = len(pv)
    mode_code, switch = _params_parts(params)
    ceiling = np.full(n, float(capacity))
    if capacity <= 0.0:
        return np.zeros(n)
    if mode_code == 1:
        return np.zeros(n)
    if mode_code == 3 and dc:
        over = np.maximum(0.0, (np.asarray(pv, float) - inverter) / eta)
        window = params[6] if params[6] > 0 else 1.0
        ch_cap = min(charge_limit, capacity / window * params[4])
        valid_mask = np.ones(n, dtype=np.bool_) if valid is None else np.asarray(valid, dtype=np.bool_)
        over_rem = _over_remaining(over, valid_mask, eta_ch, ch_cap,
                                   None if month is None else np.asarray(month),
                                   None if hour is None else np.asarray(hour),
                                   valid_mask)
        ceiling = np.maximum(0.0, float(capacity) - over_rem)
    if mode_code == 2:
        if month is None or hour is None:
            derived_hour = np.arange(n) % 24
            derived_month = np.array([_month_from_doy(i // 24) for i in range(n)])
        else:
            derived_hour = np.asarray(hour)
            derived_month = np.asarray(month)
        start = int(params[9]) if len(params) > 9 else 3
        end = int(params[10]) if len(params) > 10 else 9
        active = ((derived_month >= start) & (derived_month <= end)) if start <= end else ((derived_month >= start) | (derived_month <= end))
        am = active & (derived_hour < switch)
        ceiling = np.where(am, 0.0, ceiling)
    return ceiling


# Co-locating dispatch kernels with dispatch_physical_year ensures that Numba's
# on-disk cache invalidates whenever physical dispatch dependencies change.
@njit(cache=True)
def dispatch_year(pv,load,capacity,charge_limit,discharge_limit,efficiency,inverter,initial_soc,price_shape,valid,dc_coupled=False,physical=(0.,1.,1.,1.,1.,0.,1.),month=None,hour=None,record_trace=True):
    """Hourly dispatch; PV profiles are AC-equivalent model inputs, not measured DC output."""
    if physical[0] > 0:
        return dispatch_physical_year(pv,load,capacity,charge_limit,discharge_limit,inverter,initial_soc,price_shape,valid,dc_coupled,physical,month,hour,record_trace)
    out=np.zeros(14);trace=np.zeros((len(pv) if record_trace else 0,8));soc=min(initial_soc,capacity)
    out[7]=max(initial_soc-capacity,0.) # energy lost when usable capacity fades
    maxerror=0.
    for t in range(len(pv)):
        if not valid[t]:continue
        before=soc;raw=max(pv[t],0.);demand=max(load[t],0.)
        if dc_coupled:
            direct=min(raw,demand,inverter)
            surplus=max(raw-direct,0.)
            charge=min(surplus,charge_limit,max((capacity-soc)/efficiency,0.))
            soc+=charge*efficiency
            ac_surplus=max(surplus-charge,0.)
            exported=min(ac_surplus,max(inverter-direct,0.))
            clipped=max(ac_surplus-exported,0.)
            produced=raw-clipped
        else:
            # Legacy AC-equivalent topology: all PV, including the battery's
            # charging input, must pass through the inverter AC ceiling.
            produced=min(raw,inverter)
            direct=min(produced,demand)
            surplus=produced-direct
            charge=min(surplus,charge_limit,max((capacity-soc)/efficiency,0.))
            soc+=charge*efficiency
            exported=surplus-charge
            clipped=raw-produced
        discharge=min(demand-direct,discharge_limit,max(inverter-direct-exported,0.),soc*efficiency)
        soc-=discharge/efficiency
        imported=demand-direct-discharge
        loss=charge*(1-efficiency)+discharge*(1/efficiency-1)
        error=abs(produced+imported-demand-exported-loss-(soc-before));maxerror=max(maxerror,error)
        if soc < -1e-8 or soc>capacity+1e-8 or discharge+direct>inverter+1e-8 or direct+exported+discharge>inverter+1e-8:raise ValueError('Physical dispatch constraint failed')
        out[0]+=demand;out[1]+=produced;out[2]+=direct;out[3]+=charge;out[4]+=discharge;out[5]+=imported;out[6]+=exported;out[7]+=loss;out[9]+=clipped;out[11]+=exported*price_shape[t]
        if record_trace:
            trace[t,0]=produced;trace[t,1]=demand;trace[t,2]=imported;trace[t,3]=exported;trace[t,4]=soc;trace[t,5]=direct;trace[t,6]=charge;trace[t,7]=discharge
    out[8]=soc;out[10]=out[4]/efficiency/capacity if capacity>0 else 0.;out[12]=capacity;out[13]=maxerror
    return out,trace


@njit(cache=True, parallel=True, nogil=True)
def dispatch_weather_years(pv,load,capacity,charge_limit,discharge_limit,efficiency,inverter,initial_soc,price_shape,valid,dc_coupled=False,physical=(0.,1.,1.,1.,1.,0.,1.),month=None,hour=None):
    if pv.ndim != 2 or load.ndim != 2:
        raise ValueError('pv and load must be 2D arrays')
    weather = pv.shape[0]
    target_len = pv.shape[1]
    if load.shape[0] != weather or load.shape[1] != target_len:
        raise ValueError('pv and load shapes must match')
    if len(price_shape) != target_len or len(valid) != target_len:
        raise ValueError('price_shape and valid lengths must match target_len')
    if month is not None and len(month) != target_len:
        raise ValueError('month length must match target_len')
    if hour is not None and len(hour) != target_len:
        raise ValueError('hour length must match target_len')
    totals = np.zeros((weather, 14))
    traces = np.zeros((weather, target_len, 8))
    for w in prange(weather):
        tot, tr = dispatch_year(pv[w], load[w], capacity, charge_limit, discharge_limit, efficiency, inverter, initial_soc, price_shape, valid, dc_coupled, physical, month, hour, True)
        totals[w] = tot
        traces[w] = tr
    return totals, traces


@njit(cache=True, parallel=True, nogil=True)
def dispatch_weather_monthly(pv,load,pzo,month_bins,pv_charge_loss,capacity,charge_limit,discharge_limit,efficiency,inverter,initial_soc,price_shape,valid,dc_coupled=False,physical=(0.,1.,1.,1.,1.,0.,1.),month=None,hour=None):
    if pv.ndim != 2 or load.ndim != 2 or pzo.ndim != 2:
        raise ValueError('pv, load, and pzo must be 2D arrays')
    weather = pv.shape[0]
    target_len = pv.shape[1]
    if load.shape[0] != weather or load.shape[1] != target_len:
        raise ValueError('load shape must match pv shape')
    if pzo.shape[0] != weather or pzo.shape[1] != target_len:
        raise ValueError('pzo shape must match pv shape')
    if len(month_bins) != target_len:
        raise ValueError('month_bins length must match target_len')
    if len(price_shape) != target_len or len(valid) != target_len:
        raise ValueError('price_shape and valid lengths must match target_len')
    if month is not None and len(month) != target_len:
        raise ValueError('month length must match target_len')
    if hour is not None and len(hour) != target_len:
        raise ValueError('hour length must match target_len')
    for t in range(target_len):
        if month_bins[t] < 0 or month_bins[t] > 11:
            raise ValueError('month_bins values must be in 0..11')
    totals = np.zeros((weather, 14))
    monthly = np.zeros((weather, 12, 5))
    for w in prange(weather):
        tot, tr = dispatch_year(pv[w], load[w], capacity, charge_limit, discharge_limit, efficiency, inverter, initial_soc, price_shape, valid, dc_coupled, physical, month, hour, True)
        totals[w] = tot
        for t in range(target_len):
            m = month_bins[t]
            raw_pv = pv[w, t]
            imp = tr[t, 2]
            exp = tr[t, 3]
            w_exp = exp * pzo[w, t]
            pv_equiv = tr[t, 0] - pv_charge_loss * tr[t, 6]
            clip = np.maximum(raw_pv - pv_equiv, 0.0)
            monthly[w, m, 0] += raw_pv
            monthly[w, m, 1] += imp
            monthly[w, m, 2] += exp
            monthly[w, m, 3] += w_exp
            monthly[w, m, 4] += clip
    return totals, monthly


# Co-locating dispatch_lifetime with dispatch_year and physical kernels ensures
# that Numba's on-disk cache invalidates whenever physical dispatch dependencies change.
@njit(cache=True, parallel=True, nogil=True)
def dispatch_lifetime(pv,load,ev,valid,price_shape,horizon,ev_start,inverter,capacity,charge,discharge,efficiency,firstfade,pvfade,calfade,cyclefade,replaceyear,dc_coupled=False,physical=(0.,1.,1.,1.,1.,0.,1.),month=None,hour=None):
    weather=len(pv);annual=np.zeros((horizon,weather,14))
    # Weather paths are independent; lifetime years retain sequential battery state.
    for w in prange(weather):
        soc=0.;age=0;cycles=0.
        m_w = month[w] if month is not None else None
        h_w = hour[w] if hour is not None else None
        for y in range(horizon):
            replacement_loss=0.
            if replaceyear>0 and y==replaceyear and capacity>0:
                replacement_loss=soc;soc=0.;age=0;cycles=0.
            usable=capacity*(1-calfade)**age*(1-cyclefade)**cycles
            degradation=max(0.,1-firstfade-pvfade*y)
            demand=load[w]+ev[w] if y+1>=ev_start else load[w]
            row,unused=dispatch_year(pv[w]*degradation,demand,usable,charge,discharge,efficiency,inverter,soc,price_shape[w],valid[w],dc_coupled,physical,m_w,h_w,False)
            row[7]+=replacement_loss;annual[y,w]=row;soc=row[8];cycles+=row[10];age+=1
    return annual
