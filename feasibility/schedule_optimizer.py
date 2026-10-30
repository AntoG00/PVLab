"""Schedule optimizer for physical dispatch switch hour tradeoff.

Evaluates baseline battery_first against scheduled_hybrid configurations
across switch hours 9..14, ranking paired lifetime equity NPV and physical
clipping savings across all historical weather years.
"""
from feasibility.runtime_context import current_runtime, assert_runtime_key

import time
import math
import calendar
import datetime as dt
from functools import lru_cache
from copy import deepcopy
import numpy as np
from .soc_statistics import monthly_peak_soc_statistics
from .ensemble_statistics import paired_improvement_statistics


def _freeze(v):
    import feasibility.engine as eng
    return eng.freeze(v)


def prepare_dispatch_context(eng, s, base_ri=None, shared_context=None):
    """Precompute mapped solar/load data and date groupings once for sweep runs.

    All candidates and Self-Use reuse the same weather-year order and calendar.
    A representative index is retained only for legacy diagnostic consumers.

    shared_context: optional dict persisting for the lifetime of one sizing request
    (with fixed load/calendar/prices) to reuse mapped non-PV arrays across PV layouts.
    """
    if shared_context is not None and shared_context.get('template') is not None:
        template = shared_context['template']
        ctx = dict(template)
        mapped_data = dict(template['mapped_data'])
        mappings = mapped_data['mappings']
        target_len = template['target_len']
        pv_source = eng.pv_profile(s)
        mapped_pv = np.empty((len(mappings), target_len), dtype=float)
        for wi, mapping in enumerate(mappings):
            mapped_pv[wi] = eng._apply_mapping(pv_source[wi], mapping) * (1 - s['pv_first_year_loss'])
        mapped_data['mapped_pv'] = mapped_pv
        ctx['mapped_data'] = mapped_data
        ctx['ri'] = int(base_ri) if base_ri is not None else None
        return ctx

    mapped_data = eng._forecast_mapped_series(s)
    target_len = mapped_data['target_len']
    forecast_year = mapped_data['forecast_year']
    is_leap = mapped_data['is_leap']
    days = 366 if is_leap else 365

    # The baseline dispatch chooses the representative from actual delivered PV.
    ri = int(base_ri) if base_ri is not None else None

    target_months = np.asarray(mapped_data['target_months'], dtype=int)
    target_std_hours = np.asarray(mapped_data['target_std_hours'], dtype=int)
    month_hours = target_months - 1

    start_date = dt.date(forecast_year, 1, 1)
    months_per_day = np.array([(start_date + dt.timedelta(days=d)).month for d in range(days)], dtype=int)
    month_days = months_per_day - 1
    day_counts = np.bincount(month_days, minlength=12)

    res = dict(
        ri=ri,
        mapped_data=mapped_data,
        target_len=target_len,
        target_months=target_months,
        target_std_hours=target_std_hours,
        valid=np.ones(target_len, dtype=bool),
        ones_price=np.ones(target_len, dtype=float),
        forecast_year=forecast_year,
        days=days,
        month_hours=month_hours,
        month_days=month_days,
        day_counts=day_counts,
    )

    if shared_context is not None:
        tpl_mapped_data = {k: v for k, v in mapped_data.items() if k != 'mapped_pv'}
        shared_context['template'] = dict(res, ri=None, mapped_data=tpl_mapped_data)

    return res


def _trace_monthly_summary(eng, s, ekey=None, price_ver=0, base_ri=None, prepared_context=None, energy_only=False):
    """Generate monthly dispatch summary, using precomputed context when provided."""
    factor = (
        s['inverter_efficiency']
        if (s['pv_dispatch_model'] == 'physical'
            and s['pv_battery_coupling'] == 'dc_coupled'
            and s['battery_kwh'] > 0)
        else 1.0
    )
    if prepared_context is not None:
        ctx = prepared_context
        md = ctx['mapped_data']
        params = eng.physical_parameters(s)
        if energy_only:
            totals, monthly_matrix = eng.dispatch_weather_monthly(
                np.asarray(md['mapped_pv']),
                np.asarray(md['mapped_load']),
                np.asarray(md['mapped_pzo']),
                ctx['month_hours'],
                1.0 - factor,
                s['battery_kwh'],
                s['charge_kw'],
                s['discharge_kw'],
                math.sqrt(s['roundtrip_efficiency']),
                s['inverter_kw'],
                0.,
                ctx['ones_price'],
                ctx['valid'],
                s['pv_battery_coupling'] == 'dc_coupled',
                params,
                ctx['target_months'],
                ctx['target_std_hours'],
            )
        else:
            totals, traces = eng.dispatch_weather_years(
                np.asarray(md['mapped_pv']),
                np.asarray(md['mapped_load']),
                s['battery_kwh'],
                s['charge_kw'],
                s['discharge_kw'],
                math.sqrt(s['roundtrip_efficiency']),
                s['inverter_kw'],
                0.,
                ctx['ones_price'],
                ctx['valid'],
                s['pv_battery_coupling'] == 'dc_coupled',
                params,
                ctx['target_months'],
                ctx['target_std_hours'],
            )
        if ctx['ri'] is None:
            yields = np.array([total[1] for total in totals])
            order = np.argsort(yields)
            target = float(np.percentile(yields, 50))
            ctx['ri'] = int(order[np.argmin(abs(yields[order] - target))])
        ri = ctx['ri']
        days = ctx['days']
        month_hours, month_days, day_counts = ctx['month_hours'], ctx['month_days'], ctx['day_counts']
        if energy_only:
            weather_monthly = [
                [
                    dict(
                        month=m + 1,
                        pv_input_kwh=round(float(monthly_matrix[w, m, 0]), 1),
                        grid_import_kwh=float(monthly_matrix[w, m, 1]),
                        grid_export_kwh=float(monthly_matrix[w, m, 2]),
                        export_price_weighted_kwh=float(monthly_matrix[w, m, 3]),
                        clipped_kwh=float(monthly_matrix[w, m, 4]),
                    )
                    for m in range(12) if day_counts[m] > 0
                ]
                for w in range(monthly_matrix.shape[0])
            ]
            return ri, {
                'monthly_clipping': weather_monthly[ri],
                'weather_monthly_clipping': weather_monthly,
            }
    else:
        res = eng._forecast_2027_runs(ekey, price_ver)
        ri = base_ri if base_ri is not None else res['picks'].get('median', 0)
        traces = res['traces']
        md = res.get('mapped_data', {})
        year = res['forecast_year']
        is_leap = calendar.isleap(year)
        days = 366 if is_leap else 365
        start_date = dt.date(year, 1, 1)
        months_per_day = np.array([(start_date + dt.timedelta(days=d)).month for d in range(days)], dtype=int)
        month_days = months_per_day - 1
        day_counts = np.bincount(month_days, minlength=12)
        target_months = np.repeat(months_per_day, 24)
        month_hours = target_months - 1

    def summarize(trace, raw_pv, raw_load, pzo):
        if energy_only:
            # Screening consumes energy totals, without the SOC/report statistics.
            pv_equivalent = trace[:, 0] - (1.0 - factor) * trace[:, 6]
            clipping = np.maximum(raw_pv - pv_equivalent, 0.0)
            pv_input_m = np.bincount(month_hours, weights=raw_pv, minlength=12)
            grid_imp_m = np.bincount(month_hours, weights=trace[:, 2], minlength=12)
            grid_exp_m = np.bincount(month_hours, weights=trace[:, 3], minlength=12)
            pzo_exp_m = np.bincount(month_hours, weights=trace[:, 3] * pzo, minlength=12)
            clipped_kwh_m = np.bincount(month_hours, weights=clipping, minlength=12)
            return [
                dict(month=m + 1, pv_input_kwh=round(float(pv_input_m[m]), 1),
                     grid_import_kwh=float(grid_imp_m[m]), grid_export_kwh=float(grid_exp_m[m]),
                     export_price_weighted_kwh=float(pzo_exp_m[m]), clipped_kwh=float(clipped_kwh_m[m]))
                for m in range(12) if day_counts[m] > 0
            ]
        peak_statistics = monthly_peak_soc_statistics([trace], month_days, s['battery_kwh'])
        pv_equivalent = trace[:, 0] - (1.0 - factor) * trace[:, 6]
        clipping = np.maximum(raw_pv - pv_equivalent, 0.0)

        # Vectorized daily peak SoC for monthly averaging
        trace_reshaped = trace.reshape(days, 24, 8)
        daily_peak_soc = trace_reshaped[:, :, 4].max(axis=1)

        # Vectorized monthly sums via bincount
        pv_input_m = np.bincount(month_hours, weights=raw_pv, minlength=12)
        load_kwh_m = np.bincount(month_hours, weights=raw_load, minlength=12)
        grid_imp_m = np.bincount(month_hours, weights=trace[:, 2], minlength=12)
        grid_exp_m = np.bincount(month_hours, weights=trace[:, 3], minlength=12)
        pzo_exp_m = np.bincount(month_hours, weights=trace[:, 3] * pzo, minlength=12)
        clipped_kwh_m = np.bincount(month_hours, weights=clipping, minlength=12)
        clip_hrs_m = np.bincount(month_hours, weights=(clipping > 1e-9).astype(float), minlength=12)
        peak_soc_sum = np.bincount(month_days, weights=daily_peak_soc, minlength=12)

        monthly_rows = []
        for m_idx in range(12):
            cnt = day_counts[m_idx]
            if cnt == 0:
                continue
            m = m_idx + 1
            input_kwh = float(pv_input_m[m_idx])
            load_kwh = float(load_kwh_m[m_idx])
            clipped_kwh = float(clipped_kwh_m[m_idx])
            peak_soc_kwh = float(peak_soc_sum[m_idx] / cnt)
            m_mask = (month_hours == m_idx)
            peak_curt = float(clipping[m_mask].max(initial=0.0))

            monthly_rows.append(dict(
                **peak_statistics[m_idx],
                month=m,
                pv_input_kwh=round(input_kwh, 1),
                load_kwh=load_kwh,
                mean_peak_soc_kwh=round(peak_soc_kwh, 2),
                mean_peak_soc_pct=(
                    100.0 * peak_soc_kwh / s['battery_kwh']
                    if s['battery_kwh'] > 0 else None
                ),
                export_price_weighted_kwh=float(pzo_exp_m[m_idx]),
                grid_import_kwh=float(grid_imp_m[m_idx]),
                grid_export_kwh=float(grid_exp_m[m_idx]),
                clipped_kwh=clipped_kwh,
                clipped_pct=round(100.0 * clipped_kwh / input_kwh, 2) if input_kwh > 0 else 0.0,
                clipping_hours=int(clip_hrs_m[m_idx]),
                peak_curtailed_kw=round(peak_curt, 2),
            ))
        return monthly_rows

    weather_monthly = []
    mapped_pv = md.get('mapped_pv', [])
    mapped_load = md.get('mapped_load', None)
    mapped_pzo = md.get('mapped_pzo', [])
    for idx, tr in enumerate(traces):
        pv_arr = np.asarray(mapped_pv[idx], dtype=float)
        pzo_arr = np.asarray(mapped_pzo[idx], dtype=float)
        if mapped_load is not None and idx < len(mapped_load):
            load_arr = np.asarray(mapped_load[idx], dtype=float)
        else:
            load_arr = np.asarray(tr[:, 1], dtype=float)
        weather_monthly.append(summarize(tr, pv_arr, load_arr, pzo_arr))

    report = {
        'monthly_clipping': weather_monthly[ri],
        'weather_monthly_clipping': weather_monthly,
    }
    return ri, report



def sizing_schedule_candidates(s, financial_basis='project', max_candidates=6, context_cache=None, shared_context=None):
    """Efficient screening search for scheduled AM/PM candidates during system sizing.

    Screening is explicitly approximate; full physics/NPV ranking occurs later
    in the engine. Spliced monthly energy summaries across baseline and full-year
    scheduled traces approximate contiguous cyclic month spans without re-dispatching
    every candidate configuration across all weather years.

    Optional context caches belong to one sizing request with fixed load/calendar/prices.
    Optional shared_context reuses mapped non-PV arrays across candidates in one sizing request.
    """
    try:
        raw_h = s.get('routing_switch_hour')
        inp_h = 11 if raw_h is None else int(round(float(raw_h)))
    except (TypeError, ValueError):
        inp_h = 11

    try:
        raw_start = s.get('scheduled_start_month')
        inp_start = 1 if raw_start is None else int(raw_start)
    except (TypeError, ValueError):
        inp_start = 1

    try:
        raw_end = s.get('scheduled_end_month')
        inp_end = 12 if raw_end is None else int(raw_end)
    except (TypeError, ValueError):
        inp_end = 12

    def _coarse_fallback():
        res = []
        seen_fb = set()
        for h_fb in range(9, 16):
            tup = (h_fb, 1, 12)
            if tup not in seen_fb:
                seen_fb.add(tup)
                res.append({
                    'routing_switch_hour': h_fb,
                    'scheduled_start_month': 1,
                    'scheduled_end_month': 12,
                })
        inp_tup = (inp_h, inp_start, inp_end)
        if inp_tup not in seen_fb:
            seen_fb.add(inp_tup)
            res.append({
                'routing_switch_hour': inp_h,
                'scheduled_start_month': inp_start,
                'scheduled_end_month': inp_end,
            })
        return res

    is_physical = s.get('pv_dispatch_model', 'legacy') == 'physical'
    try:
        battery_kwh = float(s.get('battery_kwh', 0.0) or 0.0)
    except (TypeError, ValueError):
        battery_kwh = 0.0

    if not is_physical or battery_kwh <= 0.0:
        return _coarse_fallback()

    import feasibility.engine as eng

    context = None
    if context_cache is not None:
        key = tuple(s[k] for k in eng.PV_PROFILE_KEYS) + (s['pv_battery_coupling'],)
        if key in context_cache:
            cached = context_cache.pop(key)
            context_cache[key] = cached
            context = dict(cached, ri=None)
    if context is None:
        try:
            if shared_context is not None:
                context = prepare_dispatch_context(eng, s, shared_context=shared_context)
            else:
                context = prepare_dispatch_context(eng, s)
        except Exception:
            return _coarse_fallback()
        if context_cache is not None:
            while len(context_cache) >= 4:
                context_cache.pop(next(iter(context_cache)))
            context_cache[key] = dict(context, ri=None)
            context = dict(context, ri=None)

    base_s = dict(s, routing_priority='battery_first')
    try:
        _, base_disp = _trace_monthly_summary(eng, base_s, prepared_context=context, energy_only=True)
        base_weathers = base_disp.get('weather_monthly_clipping', [])
        if not base_weathers or any(not w or len(w) != 12 for w in base_weathers):
            return _coarse_fallback()
    except Exception:
        return _coarse_fallback()

    W = len(base_weathers)
    switch_hours = list(range(9, 16))
    cand_weathers_by_h = {}

    for h in switch_hours:
        cfg = dict(
            s,
            routing_priority='scheduled_hybrid',
            routing_switch_hour=h,
            scheduled_start_month=1,
            scheduled_end_month=12,
        )
        try:
            _, cand_disp = _trace_monthly_summary(eng, cfg, prepared_context=context, energy_only=True)
            w_list = cand_disp.get('weather_monthly_clipping', [])
            if not w_list or len(w_list) != W or any(not w or len(w) != 12 for w in w_list):
                return _coarse_fallback()
            cand_weathers_by_h[h] = w_list
        except Exception:
            return _coarse_fallback()

    base_imp = np.array([[float(w[m].get('grid_import_kwh', 0.0) or 0.0) for m in range(12)] for w in base_weathers], dtype=float)
    base_exp = np.array([[float(w[m].get('grid_export_kwh', 0.0) or 0.0) for m in range(12)] for w in base_weathers], dtype=float)
    base_pzo = np.array([[float(w[m].get('export_price_weighted_kwh', 0.0) or 0.0) for m in range(12)] for w in base_weathers], dtype=float)
    base_clip = np.array([[float(w[m].get('clipped_kwh', 0.0) or 0.0) for m in range(12)] for w in base_weathers], dtype=float)
    base_pv = np.array([[float(w[m].get('pv_input_kwh', 0.0) or 0.0) for m in range(12)] for w in base_weathers], dtype=float)

    base_annual_imp = np.sum(base_imp, axis=1)
    base_annual_exp = np.sum(base_exp, axis=1)
    base_annual_pzo = np.sum(base_pzo, axis=1)
    base_annual_clip = np.sum(base_clip, axis=1)
    base_annual_pv = np.sum(base_pv, axis=1)

    delta_imp_by_h = {}
    delta_exp_by_h = {}
    delta_pzo_by_h = {}
    delta_clip_by_h = {}

    for h in switch_hours:
        w_list = cand_weathers_by_h[h]
        c_imp = np.array([[float(w[m].get('grid_import_kwh', 0.0) or 0.0) for m in range(12)] for w in w_list], dtype=float)
        c_exp = np.array([[float(w[m].get('grid_export_kwh', 0.0) or 0.0) for m in range(12)] for w in w_list], dtype=float)
        c_pzo = np.array([[float(w[m].get('export_price_weighted_kwh', 0.0) or 0.0) for m in range(12)] for w in w_list], dtype=float)
        c_clip = np.array([[float(w[m].get('clipped_kwh', 0.0) or 0.0) for m in range(12)] for w in w_list], dtype=float)

        delta_imp_by_h[h] = c_imp - base_imp
        delta_exp_by_h[h] = c_exp - base_exp
        delta_pzo_by_h[h] = c_pzo - base_pzo
        delta_clip_by_h[h] = c_clip - base_clip

    counts = eng._panel_total(s)
    panel_watts = float(s.get('panel_watts', 440.0) or 440.0)
    kwp = counts * panel_watts / 1000.0

    capture = s.get('export_capture_factor', 1.0) if eng.price_profile(s)[1] == 'flat_solar_capture_benchmark' else 1.0
    tax = float(s.get('export_irpef', 0.0) + s.get('regional_additional_tax', 0.0) + s.get('municipal_additional_tax', 0.0))
    rnom = (1.0 + float(s.get('real_discount_rate', 0.0))) * (1.0 + float(s.get('inflation', 0.0))) - 1.0
    h_years = int(s.get('horizon_years', 25) or 25)
    pmg_election = bool(s.get('pmg_election', False))
    cer_enabled = bool(s.get('cer_enabled', False))
    cer_tariff = float(s.get('cer_tariff', 0.0) or 0.0)
    cer_share = float(s.get('cer_eligible_export_share', 0.0) or 0.0)
    cer_alloc = float(s.get('cer_user_allocation', 0.0) or 0.0)
    exp_fee_ded = bool(s.get('export_fee_tax_deductible', False))
    gse_fee_kw = float(s.get('gse_fee_eur_kw_year', 0.0) or 0.0)
    gse_fee_vat = float(s.get('gse_fee_vat', 0.0) or 0.0)
    exp_market_price = float(s.get('export_market_price', 0.0) or 0.0)
    pmg_price = float(s.get('pmg_price', 0.0) or 0.0)
    pmg_nom_esc = float(s.get('pmg_nominal_escalation', 0.0) or 0.0)
    energy_real_esc = float(s.get('energy_real_escalation', 0.0) or 0.0)
    future_factor = float(s.get('actual_price_future_factor', 1.0) or 1.0)
    future_years = max(float(s.get('export_price_future_years', 1.0) or 1.0), 1e-6)
    inflation = float(s.get('inflation', 0.0) or 0.0)

    disc_arr = np.zeros(h_years, dtype=float)
    retail_arr = np.zeros(h_years, dtype=float)
    exp_unit_arr = np.zeros(h_years, dtype=float)
    pmg_unit_arr = np.zeros(h_years, dtype=float)
    fees_ded_arr = np.zeros(h_years, dtype=float)
    cer_arr = np.zeros(h_years, dtype=float)

    for yy in range(h_years):
        year = yy + 1
        infl = (1.0 + inflation) ** yy
        disc_arr[yy] = (1.0 + rnom) ** year
        retail_arr[yy] = float(eng.electric_bill(1, s, year) - eng.electric_bill(0, s, year))
        ramp = min(yy / future_years, 1.0)
        future = 1.0 + (future_factor - 1.0) * ramp
        exp_unit_arr[yy] = exp_market_price * capture * future * infl * ((1.0 + energy_real_esc) ** yy)
        pmg_unit_arr[yy] = pmg_price * ((1.0 + pmg_nom_esc) ** yy)
        fees_yy = (0.0 if kwp <= 3.0 else min(gse_fee_kw * kwp, 10000.0)) * (1.0 + gse_fee_vat) * infl
        fees_ded_arr[yy] = fees_yy if exp_fee_ded else 0.0
        cer_arr[yy] = cer_share * cer_tariff * cer_alloc * infl if cer_enabled else 0.0

    def compute_lifetime_op_values(imp_vec, exp_vec, pzo_vec):
        imp_2d = imp_vec[None, :]
        exp_2d = exp_vec[None, :]
        pzo_2d = pzo_vec[None, :]

        pzo_yy = pzo_2d * exp_unit_arr[:, None]
        min_yy = exp_2d * pmg_unit_arr[:, None]
        if pmg_election:
            tot_yy = np.where(min_yy > pzo_yy, min_yy, pzo_yy)
        else:
            tot_yy = pzo_yy

        tot_safe = np.maximum(tot_yy, 1e-12)
        excess = np.maximum(tot_yy - fees_ded_arr[:, None], 0.0)
        tax_frac_yy = np.where(tot_yy > 0.0, excess * tax / tot_safe, 0.0)

        net_val_yy = (
            -imp_2d * retail_arr[:, None]
            + tot_yy * (1.0 - tax_frac_yy)
            + exp_2d * cer_arr[:, None]
        )
        return np.sum(net_val_yy / disc_arr[:, None], axis=0)

    base_lifetime_vals = compute_lifetime_op_values(base_annual_imp, base_annual_exp, base_annual_pzo)

    spans = []
    for length in range(1, 12):
        for start_m in range(1, 13):
            months = tuple(((start_m - 1 + i) % 12) + 1 for i in range(length))
            spans.append((start_m, months[-1], [m - 1 for m in months]))
    spans.append((1, 12, list(range(12))))

    pv_safe = np.where(base_annual_pv > 0.0, base_annual_pv, 1.0)
    meta = []
    all_imp, all_exp, all_pzo, all_clip = [], [], [], []

    for h in switch_hours:
        d_imp = delta_imp_by_h[h]
        d_exp = delta_exp_by_h[h]
        d_pzo = delta_pzo_by_h[h]
        d_clip = delta_clip_by_h[h]

        for start_m, end_m, m_indices in spans:
            all_imp.append(base_annual_imp + np.sum(d_imp[:, m_indices], axis=1))
            all_exp.append(base_annual_exp + np.sum(d_exp[:, m_indices], axis=1))
            all_pzo.append(base_annual_pzo + np.sum(d_pzo[:, m_indices], axis=1))
            all_clip.append(np.maximum(base_annual_clip + np.sum(d_clip[:, m_indices], axis=1), 0.0))
            meta.append((h, start_m, end_m))

    # Batch independent schedules while retaining each monthly sum and traversal order.
    cand_imp, cand_exp, cand_pzo, cand_clip = map(np.array, (all_imp, all_exp, all_pzo, all_clip))
    all_lt_vals = compute_lifetime_op_values(cand_imp.ravel(), cand_exp.ravel(), cand_pzo.ravel())
    mean_deltas = np.mean(all_lt_vals.reshape(cand_imp.shape) - base_lifetime_vals, axis=1)
    clip_fractions = np.where(base_annual_pv > 0.0, cand_clip / pv_safe, 0.0)
    worst_clips = np.max(clip_fractions, axis=1)
    mean_clips = np.mean(cand_clip, axis=1)
    candidates = [
        dict(routing_switch_hour=h, scheduled_start_month=start_m, scheduled_end_month=end_m,
             fin_val=float(mean_deltas[i]), worst_clip=float(worst_clips[i]), mean_clip=float(mean_clips[i]))
        for i, (h, start_m, end_m) in enumerate(meta)
    ]

    fin_sorted = sorted(
        candidates,
        key=lambda c: (-c['fin_val'], c['worst_clip'], c['mean_clip'], c['routing_switch_hour'], c['scheduled_start_month'], c['scheduled_end_month'])
    )
    clip_sorted = sorted(
        candidates,
        key=lambda c: (c['worst_clip'], c['mean_clip'], -c['fin_val'], c['routing_switch_hour'], c['scheduled_start_month'], c['scheduled_end_month'])
    )

    selected = []
    seen = set()

    for c in fin_sorted:
        tup = (c['routing_switch_hour'], c['scheduled_start_month'], c['scheduled_end_month'])
        if tup not in seen:
            seen.add(tup)
            selected.append({
                'routing_switch_hour': c['routing_switch_hour'],
                'scheduled_start_month': c['scheduled_start_month'],
                'scheduled_end_month': c['scheduled_end_month'],
            })
            if len(selected) >= 4:
                break

    clip_count = 0
    for c in clip_sorted:
        tup = (c['routing_switch_hour'], c['scheduled_start_month'], c['scheduled_end_month'])
        if tup not in seen:
            seen.add(tup)
            selected.append({
                'routing_switch_hour': c['routing_switch_hour'],
                'scheduled_start_month': c['scheduled_start_month'],
                'scheduled_end_month': c['scheduled_end_month'],
            })
            clip_count += 1
            if clip_count >= 2:
                break

    if max_candidates is not None and max_candidates > 0:
        selected = selected[:max_candidates]

    seen = {(c['routing_switch_hour'], c['scheduled_start_month'], c['scheduled_end_month']) for c in selected}
    inp_tup = (inp_h, inp_start, inp_end)
    if inp_tup not in seen:
        seen.add(inp_tup)
        selected.append({
            'routing_switch_hour': inp_h,
            'scheduled_start_month': inp_start,
            'scheduled_end_month': inp_end,
        })

    return selected

@lru_cache(maxsize=32)
def _cached_switch_hour_tradeoff(runtime_key, frozen_s, price_ver):
    """Bounded lru_cache evaluation for switch hour tradeoff."""
    assert_runtime_key(runtime_key)
    import feasibility.engine as eng

    s = eng.thaw_settings(dict(frozen_s))

    is_physical = s.get('pv_dispatch_model', 'legacy') == 'physical'
    battery_kwh = float(s.get('battery_kwh', 0.0) or 0.0)
    switch_hours = list(range(9, 15))

    if not is_physical or battery_kwh <= 0.0:
        reason = 'disabled' if not is_physical else 'zero_battery'
        base_row = {
            'routing_priority': 'battery_first',
            'routing_switch_hour': None,
            'scheduled_start_month': None,
            'scheduled_end_month': None,
            'active_months': [],
            'selection_objectives': [],
            'clipped_kwh': None,
            'summer_max_soc_pct': None,
            'summer_p25_peak_soc_pct': None,
            'min_p25_peak_soc_pct': None,
            'preserves_autarky_soc': None,
            'grid_import_kwh': None,
            'grid_export_kwh': None,
            'autarky': None,
            'equity_npv_delta': None,
            'equity_npv_delta_median': None,
            'equity_npv_delta_p10': None,
            'equity_npv_delta_p90': None,
            'equity_npv_improving_years': None,
            'status': reason,
        }
        rows = [base_row]
        for h in switch_hours:
            rows.append({
                'routing_priority': 'scheduled_hybrid',
                'routing_switch_hour': h,
                'scheduled_start_month': None,
                'scheduled_end_month': None,
                'active_months': [],
                'selection_objectives': [],
                'clipped_kwh': None,
                'summer_max_soc_pct': None,
                'summer_p25_peak_soc_pct': None,
                'min_p25_peak_soc_pct': None,
                'grid_import_kwh': None,
                'grid_export_kwh': None,
                'autarky': None,
                'equity_npv_delta': None,
                'equity_npv_delta_median': None,
                'equity_npv_delta_p10': None,
                'equity_npv_delta_p90': None,
                'equity_npv_improving_years': None,
                'status': reason,
            })
        rows.append({
            'routing_priority': 'dynamic_reserve',
            'routing_switch_hour': None,
            'scheduled_start_month': None,
            'scheduled_end_month': None,
            'active_months': [],
            'selection_objectives': [],
            'clipped_kwh': None,
            'summer_max_soc_pct': None,
            'summer_p25_peak_soc_pct': None,
            'min_p25_peak_soc_pct': None,
            'preserves_autarky_soc': None,
            'grid_import_kwh': None,
            'grid_export_kwh': None,
            'autarky': None,
            'equity_npv_delta': None,
            'equity_npv_delta_median': None,
            'equity_npv_delta_p10': None,
            'equity_npv_delta_p90': None,
            'equity_npv_improving_years': None,
            'status': reason,
        })
        return rows, None, None

    counts = eng._panel_total(s)
    kwp = counts * s['panel_watts'] / 1000.0

    def make_energy_key(cfg):
        return eng._energy_key({**s, **cfg}, eng.ENERGY_KEYS)

    capture = s['export_capture_factor'] if eng.price_profile(s)[1] == 'flat_solar_capture_benchmark' else 1.0
    tax = s['export_irpef'] + s['regional_additional_tax'] + s['municipal_additional_tax']
    rnom = (1 + s['real_discount_rate']) * (1 + s['inflation']) - 1
    h_years = s.get('horizon_years', 25)
    fees_y1 = (0 if kwp <= 3 else min(s['gse_fee_eur_kw_year'] * kwp, 10000)) * (1 + s['gse_fee_vat'])
    retail_y1 = eng.electric_bill(1, s) - eng.electric_bill(0, s)
    cer_y1 = s['cer_eligible_export_share'] * s['cer_tariff'] * s['cer_user_allocation'] if s['cer_enabled'] else 0

    year_terms = []
    for yy in range(h_years):
        year = yy + 1
        infl = (1 + s['inflation']) ** yy
        disc = (1 + rnom) ** year
        retail_yy = eng.electric_bill(1, s, year) - eng.electric_bill(0, s, year)
        ramp = min(yy / s['export_price_future_years'], 1.0)
        future = 1.0 + (s['actual_price_future_factor'] - 1.0) * ramp
        exp_unit_price = s['export_market_price'] * capture * future * infl * (1 + s['energy_real_escalation']) ** yy
        pmg_escalation_factor = (1 + s['pmg_nominal_escalation']) ** yy
        fees_yy = (0 if kwp <= 3 else min(s['gse_fee_eur_kw_year'] * kwp, 10000)) * (1 + s['gse_fee_vat']) * infl
        cer_yy = s['cer_eligible_export_share'] * s['cer_tariff'] * s['cer_user_allocation'] * infl if s['cer_enabled'] else 0
        year_terms.append((retail_yy, disc, exp_unit_price, pmg_escalation_factor, fees_yy, cer_yy))

    def monthly_values(disp):
        # Energy caches stay independent of finance; value their weighted exports here.
        monthly = [dict(m) for m in disp.get('monthly_clipping', [])]

        # Year 1 (yy = 0)
        pzo_y1 = [m['export_price_weighted_kwh'] * s['export_market_price'] * capture for m in monthly]
        min_y1 = [m['grid_export_kwh'] * s['pmg_price'] for m in monthly]
        gross_y1 = min_y1 if s['pmg_election'] and sum(min_y1) > sum(pzo_y1) else pzo_y1
        total_y1 = sum(gross_y1)
        tax_fraction_y1 = max(total_y1 - (fees_y1 if s['export_fee_tax_deductible'] else 0), 0) * tax / total_y1 if total_y1 > 0 else 0

        for m, value in zip(monthly, gross_y1):
            m['operating_value_eur'] = -m['grid_import_kwh'] * retail_y1 + value * (1 - tax_fraction_y1) + m['grid_export_kwh'] * cer_y1

        # Lifetime operating value across horizon_years
        lifetime_op_vals = [0.0] * len(monthly)
        for retail_yy, disc, exp_unit_price, pmg_factor, fees_yy, cer_yy in year_terms:
            pzo_yy = [m['export_price_weighted_kwh'] * exp_unit_price for m in monthly]
            min_yy = [m['grid_export_kwh'] * s['pmg_price'] * pmg_factor for m in monthly]
            gross_yy = min_yy if s['pmg_election'] and sum(min_yy) > sum(pzo_yy) else pzo_yy
            tot_yy = sum(gross_yy)
            tax_frac_yy = max(tot_yy - (fees_yy if s['export_fee_tax_deductible'] else 0), 0) * tax / tot_yy if tot_yy > 0 else 0
            for idx, (m, val_yy) in enumerate(zip(monthly, gross_yy)):
                net_val_yy = -m['grid_import_kwh'] * retail_yy + val_yy * (1 - tax_frac_yy) + m['grid_export_kwh'] * cer_yy
                lifetime_op_vals[idx] += net_val_yy / disc

        for idx, m in enumerate(monthly):
            m['lifetime_operating_value_eur'] = lifetime_op_vals[idx]

        return monthly

    context = prepare_dispatch_context(eng, s)

    def get_weather_monthlies(disp):
        if not isinstance(disp, dict):
            return []
        w_list = disp.get('weather_monthly_clipping')
        if not w_list:
            legacy = disp.get('monthly_clipping')
            w_list = [legacy] if legacy is not None else []
        res = []
        for w in w_list:
            if not w or len(w) != 12:
                return []
            res.append(monthly_values({'monthly_clipping': w}))
        return res

    def dispatch_for_span(hour, start_m, end_m):
        cfg = dict(s, routing_priority='scheduled_hybrid', routing_switch_hour=hour,
                   scheduled_start_month=start_m, scheduled_end_month=end_m)
        key = make_energy_key(cfg)
        _, disp = _trace_monthly_summary(eng, cfg, key, price_ver, prepared_context=context)
        return cfg, key, disp

    base_s = dict(s, routing_priority='battery_first')
    _, base_disp = _trace_monthly_summary(
        eng, base_s, make_energy_key(base_s), price_ver, prepared_context=context)
    base_weathers = get_weather_monthlies(base_disp)
    if not base_weathers or any(len(w) != 12 for w in base_weathers):
        return [], None, None
    W = len(base_weathers)
    base_annual_clips = [sum(m['clipped_kwh'] for m in w) for w in base_weathers]
    base_annual_vals = [sum(m['operating_value_eur'] for m in w) for w in base_weathers]
    base_annual_lt_vals = [sum(m['lifetime_operating_value_eur'] for m in w) for w in base_weathers]

    def qualifies_month_value(m_idx, cand_w):
        b_vals = [w[m_idx].get('operating_value_eur') for w in base_weathers]
        c_vals = [w[m_idx].get('operating_value_eur') for w in cand_w]
        if any(v is None or not np.isfinite(v) for v in b_vals + c_vals):
            return False
        mean_delta = float(np.mean([c - b for b, c in zip(b_vals, c_vals)]))
        return np.isfinite(mean_delta) and mean_delta > 0.0

    def qualifies_month_clipping(m_idx, cand_w):
        b_clips = [w[m_idx].get('clipped_kwh') for w in base_weathers]
        c_clips = [w[m_idx].get('clipped_kwh') for w in cand_w]
        b_vals = [w[m_idx].get('operating_value_eur') for w in base_weathers]
        c_vals = [w[m_idx].get('operating_value_eur') for w in cand_w]
        b_p25 = [w[m_idx].get('p25_peak_soc_pct') for w in base_weathers]
        c_p25 = [w[m_idx].get('p25_peak_soc_pct') for w in cand_w]

        if any(v is None or not np.isfinite(v) for v in b_clips + c_clips + b_vals + c_vals + b_p25 + c_p25):
            return False

        med_base_clip = float(np.median(b_clips))
        if med_base_clip < 0.5 - 1e-9:
            return False

        med_clip_saved = float(np.median([b - c for b, c in zip(b_clips, c_clips)]))
        if med_clip_saved <= 0.2:
            return False

        med_val_delta = float(np.median([c - b for b, c in zip(b_vals, c_vals)]))
        if med_val_delta < -0.25 - 1e-9:
            return False

        med_p25_drop = float(np.median([b - c for b, c in zip(b_p25, c_p25)]))
        if not np.isfinite(med_p25_drop) or med_p25_drop > 5.0 + 1e-9:
            return False

        return True

    def compute_exact_equity_npv_by_weather(cfg):
        cfg_key = make_energy_key(cfg)
        annual, enduses = eng._energy_cache(cfg_key, price_ver)
        use_hp = bool(cfg.get('planning_heat_pump', False)) and (cfg.get('load_scenario') != 'baseline')
        fin, _ = eng._financial(
            cfg, annual, kwp, calculate_irr=False,
            hp_summary=enduses.get('heat_pump') if use_hp else None
        )
        solar_eq = fin['solar_equity']
        arr = np.asarray(solar_eq['npv_by_weather'], dtype=float)
        if len(arr) != W or not np.all(np.isfinite(arr)):
            raise ValueError(f"Expected finite solar_equity.npv_by_weather of length {W}")
        return arr

    base_npv_arr = compute_exact_equity_npv_by_weather(base_s)

    def make_row(h, active, weathers, status, basis='monthly_slice_estimate', routing_priority=None, selection_objectives=None):
        if selection_objectives is None:
            selection_objectives = []
        if routing_priority is None:
            if status == 'baseline':
                routing_priority = 'battery_first'
            elif status == 'benchmark':
                routing_priority = 'dynamic_reserve'
            else:
                routing_priority = 'scheduled_hybrid'

        row = dict(routing_priority=routing_priority,
                   routing_switch_hour=h,
                   scheduled_start_month=active[0] if active else None,
                   scheduled_end_month=active[-1] if active else None,
                   active_months=active,
                   selection_objectives=list(selection_objectives),
                   clipped_kwh=None, summer_max_soc_pct=None,
                   summer_p25_peak_soc_pct=None, min_p25_peak_soc_pct=None,
                   preserves_autarky_soc=True if status == 'baseline' else None,
                   grid_import_kwh=None, grid_import_p10_kwh=None,
                   grid_import_p90_kwh=None, grid_export_kwh=None, autarky=None,
                   autarky_p10=None, autarky_p90=None,
                   equity_npv_delta=0.0 if status == 'baseline' else None,
                   equity_npv_delta_median=0.0 if status == 'baseline' else None,
                   equity_npv_delta_p10=0.0 if status == 'baseline' else None,
                   equity_npv_delta_p90=0.0 if status == 'baseline' else None,
                   equity_npv_improving_years=0 if status == 'baseline' else None,
                   net_energy_value_delta_eur=None, metrics_basis=basis,
                   soc_statistic='p25_daily_peak_all_weather',
                   energy_statistic='weather_mean',
                   autarky_basis='one_minus_mean_import_over_mean_load',
                   status=status,
                   clipping_saved_kwh=None, clipping_saved_p10_kwh=None,
                   clipping_saved_p90_kwh=None, clipping_improving_years=None,
                   net_energy_value_delta_p10_eur=None, net_energy_value_delta_p90_eur=None,
                   value_improving_years=None, weather_year_count=None,
                   statistic_basis='paired_annual_differences_all_weather')
        if weathers:
            annual_clips = [sum(m['clipped_kwh'] for m in w) for w in weathers]
            annual_imps = [sum(m['grid_import_kwh'] for m in w) for w in weathers]
            annual_exps = [sum(m['grid_export_kwh'] for m in w) for w in weathers]
            annual_loads = [sum(m['load_kwh'] for m in w) for w in weathers]
            annual_vals = [sum(m['operating_value_eur'] for m in w) for w in weathers]

            autarkies = [
                (max(0., min(1., 1.0 - imp / load)) if load > 0 else 0.)
                for imp, load in zip(annual_imps, annual_loads)
            ]
            mean_imp = float(np.mean(annual_imps))
            mean_exp = float(np.mean(annual_exps))
            mean_load = float(np.mean(annual_loads))
            mean_autarky = 1.0 - mean_imp / mean_load if mean_load > 0 else 0.
            imp_p10, imp_p90 = np.percentile(annual_imps, [10, 90])
            aut_p10, aut_p90 = np.percentile(autarkies, [10, 90])

            # Report the worst active month, pooling each monthly statistic
            # across weather years. Missing median data must not hide valid P25s.
            soc_months = active if active else list(range(3, 10))

            m_p25_list = []
            has_valid_p25 = True
            for m in soc_months:
                p25_vals = [w[m - 1].get('p25_peak_soc_pct') for w in weathers]
                if not p25_vals or any(v is None or not np.isfinite(v) for v in p25_vals):
                    has_valid_p25 = False
                    break
                m_p25_list.append(float(np.median(p25_vals)))
            min_p25_soc = round(float(min(m_p25_list)), 1) if has_valid_p25 and m_p25_list else None

            m_med_list = []
            has_valid_med = True
            for m in soc_months:
                med_vals = [w[m - 1].get('median_peak_soc_pct') for w in weathers]
                if not med_vals or any(v is None or not np.isfinite(v) for v in med_vals):
                    has_valid_med = False
                    break
                m_med_list.append(float(np.median(med_vals)))
            pooled_med_soc = round(float(min(m_med_list)), 1) if has_valid_med and m_med_list else None

            if status == 'baseline':
                c_saved = 0.0
                c_p10 = 0.0
                c_p90 = 0.0
                c_imp_yrs = 0
                v_delta = 0.0
                v_p10 = 0.0
                v_p90 = 0.0
                v_imp_yrs = 0
            else:
                c_stats = paired_improvement_statistics(base_annual_clips, annual_clips, lower_is_better=True)
                v_stats = paired_improvement_statistics(base_annual_vals, annual_vals, lower_is_better=False)
                c_saved = c_stats['mean']
                c_p10 = round(float(c_stats['p10']), 2)
                c_p90 = round(float(c_stats['p90']), 2)
                c_imp_yrs = int(c_stats['improving_years'])
                v_delta = v_stats['mean']
                v_p10 = round(float(v_stats['p10']), 6)
                v_p90 = round(float(v_stats['p90']), 6)
                v_imp_yrs = int(v_stats['improving_years'])

            row.update(
                clipped_kwh=float(np.mean(annual_clips)),
                grid_import_kwh=mean_imp,
                grid_import_p10_kwh=round(float(imp_p10), 2),
                grid_import_p90_kwh=round(float(imp_p90), 2),
                grid_export_kwh=mean_exp,
                summer_max_soc_pct=pooled_med_soc,
                summer_p25_peak_soc_pct=min_p25_soc,
                min_p25_peak_soc_pct=min_p25_soc,
                autarky=mean_autarky,
                autarky_p10=round(float(aut_p10), 4),
                autarky_p90=round(float(aut_p90), 4),
                net_energy_value_delta_eur=v_delta,
                clipping_saved_kwh=c_saved,
                clipping_saved_p10_kwh=c_p10,
                clipping_saved_p90_kwh=c_p90,
                clipping_improving_years=c_imp_yrs,
                net_energy_value_delta_p10_eur=v_p10,
                net_energy_value_delta_p90_eur=v_p90,
                value_improving_years=v_imp_yrs,
                weather_year_count=len(weathers),
            )
        return row

    base_row = make_row(None, [], base_weathers, 'baseline', 'verified_trace')
    rows = [base_row]

    candidate_spans_to_verify = []
    for h in switch_hours:
        _, _, disp = dispatch_for_span(h, 1, 12)
        full_cand_weathers = get_weather_monthlies(disp)
        if len(full_cand_weathers) != W or any(len(w) != 12 for w in full_cand_weathers):
            rows.append(make_row(h, [], None, 'infeasible_format'))
            continue

        qualifying_val = [m for m in range(1, 13) if qualifies_month_value(m - 1, full_cand_weathers)]
        qualifying_clip = [m for m in range(1, 13) if qualifies_month_clipping(m - 1, full_cand_weathers)]

        def build_spans(qual_m):
            runs = []
            for m in qual_m:
                if not runs or m != runs[-1][-1] + 1:
                    runs.append([])
                runs[-1].append(m)
            spans = []
            for run in runs:
                for i in range(len(run)):
                    for j in range(i, len(run)):
                        spans.append(run[i:j+1])
            return spans

        val_spans = build_spans(qualifying_val)
        clip_spans = build_spans(qualifying_clip)

        def eval_span(span):
            spliced_weathers = [
                monthly_values({'monthly_clipping': [
                    full_cand_weathers[w][i] if (i + 1) in span else base_weathers[w][i]
                    for i in range(12)
                ]})
                for w in range(W)
            ]
            c_clips = [sum(m['clipped_kwh'] for m in w_m) for w_m in spliced_weathers]
            c_vals = [sum(m['operating_value_eur'] for m in w_m) for w_m in spliced_weathers]
            c_lt_vals = [sum(m['lifetime_operating_value_eur'] for m in w_m) for w_m in spliced_weathers]
            clip_stats = paired_improvement_statistics(base_annual_clips, c_clips, lower_is_better=True)
            val_stats = paired_improvement_statistics(base_annual_vals, c_vals, lower_is_better=False)
            lt_val_stats = paired_improvement_statistics(base_annual_lt_vals, c_lt_vals, lower_is_better=False)
            return dict(
                span=span,
                spliced=spliced_weathers,
                clip_saved=clip_stats['mean'],
                val_delta=val_stats['mean'],
                lt_val_delta=lt_val_stats['mean']
            )

        val_evaluated = []
        for span in val_spans:
            ev = eval_span(span)
            if ev['val_delta'] > 0.0:
                val_evaluated.append(ev)

        clip_evaluated = []
        for span in clip_spans:
            ev = eval_span(span)
            if ev['clip_saved'] > 0.0:
                clip_evaluated.append(ev)

        best_val = max(val_evaluated, key=lambda x: (x['lt_val_delta'], x['val_delta'], x['clip_saved'])) if val_evaluated else None
        best_clip = max(clip_evaluated, key=lambda x: (x['clip_saved'], x['lt_val_delta'], x['val_delta'])) if clip_evaluated else None

        if not best_val and not best_clip:
            rows.append(make_row(h, [], None, 'no_eligible_months'))
            continue

        if best_val and best_clip and best_val['span'] == best_clip['span']:
            candidate_spans_to_verify.append((h, best_val['span'], best_val['spliced'], ['value', 'clipping']))
        else:
            if best_val:
                candidate_spans_to_verify.append((h, best_val['span'], best_val['spliced'], ['value']))
            if best_clip:
                candidate_spans_to_verify.append((h, best_clip['span'], best_clip['spliced'], ['clipping']))

    verified_candidates = []
    for h, active, spliced, objectives in candidate_spans_to_verify:
        cfg, key, disp = dispatch_for_span(h, active[0], active[-1])
        verified_weathers = get_weather_monthlies(disp)
        complete = len(verified_weathers) == W and all(len(w) == 12 for w in verified_weathers)

        row = make_row(h, active, verified_weathers if complete else None,
                       'feasible' if complete else 'infeasible_verification',
                       'verified_trace', selection_objectives=objectives)

        cand_npv_arr = compute_exact_equity_npv_by_weather(cfg) if complete else None
        if cand_npv_arr is not None and base_npv_arr is not None:
            paired_npv = cand_npv_arr - base_npv_arr
            exact_npv_delta = float(np.mean(paired_npv))
            npv_p10, npv_med, npv_p90 = np.percentile(paired_npv, [10, 50, 90])
            row['equity_npv_delta'] = exact_npv_delta
            row['equity_npv_delta_median'] = float(npv_med)
            row['equity_npv_delta_p10'] = float(npv_p10)
            row['equity_npv_delta_p90'] = float(npv_p90)
            row['equity_npv_improving_years'] = int(np.count_nonzero(paired_npv > 1e-9))
        else:
            exact_npv_delta = None
            row['equity_npv_delta'] = None
            row['equity_npv_delta_median'] = None
            row['equity_npv_delta_p10'] = None
            row['equity_npv_delta_p90'] = None
            row['equity_npv_improving_years'] = None

        valid = False
        verified_objs = []
        preserves_autarky_soc = False
        if complete and exact_npv_delta is not None and np.isfinite(exact_npv_delta):
            # Guard: finite metrics
            metrics_finite = all(
                row[k] is not None and np.isfinite(row[k])
                for k in ('clipped_kwh', 'grid_import_kwh', 'net_energy_value_delta_eur')
            )
            # Guard: imports no more than baseline + 0.1 kWh (with nonfinite guard)
            base_import = base_row.get('grid_import_kwh')
            cand_import = row.get('grid_import_kwh')
            import_ok = (
                base_import is not None and np.isfinite(base_import)
                and cand_import is not None and np.isfinite(cand_import)
                and cand_import <= base_import + 0.1
            )

            # Guard: active-month pooled P25 drop <= 5pp vs corresponding baseline months (raw nonfinite fails guard)
            p25_drop_ok = True
            for m in active:
                b_vals = [w[m - 1].get('p25_peak_soc_pct') for w in base_weathers]
                c_vals = [w[m - 1].get('p25_peak_soc_pct') for w in verified_weathers]
                if (not b_vals or not c_vals
                        or any(v is None or not np.isfinite(v) for v in b_vals)
                        or any(v is None or not np.isfinite(v) for v in c_vals)):
                    p25_drop_ok = False
                    break
                b_p25_m = float(np.median(b_vals))
                c_p25_m = float(np.median(c_vals))
                drop = b_p25_m - c_p25_m
                if not np.isfinite(drop) or drop > 5.0 + 1e-9:
                    p25_drop_ok = False
                    break

            preserves_autarky_soc = bool(metrics_finite and import_ok and p25_drop_ok)

            if metrics_finite:
                val_ok = all(qualifies_month_value(m - 1, verified_weathers) for m in active) and (row['net_energy_value_delta_eur'] > 0.0)
                clip_ok = preserves_autarky_soc and all(qualifies_month_clipping(m - 1, verified_weathers) for m in active) and (row['clipping_saved_kwh'] > 0.0)

                if val_ok:
                    verified_objs.append('value')
                if clip_ok:
                    verified_objs.append('clipping')

                if verified_objs:
                    valid = True

        row['preserves_autarky_soc'] = preserves_autarky_soc

        if valid:
            row['status'] = 'feasible'
            row['selection_objectives'] = verified_objs
            verified_candidates.append(row)
        else:
            row['status'] = 'infeasible_verification'
            row['selection_objectives'] = []

        rows.append(row)

    dyn_s = dict(s, routing_priority='dynamic_reserve')
    _, dyn_disp = _trace_monthly_summary(
        eng, dyn_s, make_energy_key(dyn_s), price_ver, prepared_context=context)
    dyn_weathers = get_weather_monthlies(dyn_disp)
    dyn_complete = len(dyn_weathers) == W and all(len(w) == 12 for w in dyn_weathers)
    dyn_row = make_row(None, [], dyn_weathers if dyn_complete else None,
                       'benchmark' if dyn_complete else 'infeasible_format',
                       'verified_trace', routing_priority='dynamic_reserve',
                       selection_objectives=[])

    dyn_npv_arr = compute_exact_equity_npv_by_weather(dyn_s) if dyn_complete else None
    if dyn_npv_arr is not None and base_npv_arr is not None:
        paired_dyn_npv = dyn_npv_arr - base_npv_arr
        dyn_exact_npv = float(np.mean(paired_dyn_npv))
        dyn_p10, dyn_med, dyn_p90 = np.percentile(paired_dyn_npv, [10, 50, 90])
        dyn_row['equity_npv_delta'] = dyn_exact_npv
        dyn_row['equity_npv_delta_median'] = float(dyn_med)
        dyn_row['equity_npv_delta_p10'] = float(dyn_p10)
        dyn_row['equity_npv_delta_p90'] = float(dyn_p90)
        dyn_row['equity_npv_improving_years'] = int(np.count_nonzero(paired_dyn_npv > 1e-9))
    else:
        dyn_exact_npv = None
        dyn_row['equity_npv_delta'] = None
        dyn_row['equity_npv_delta_median'] = None
        dyn_row['equity_npv_delta_p10'] = None
        dyn_row['equity_npv_delta_p90'] = None
        dyn_row['equity_npv_improving_years'] = None

    # Evaluate dynamic preservation guard
    dyn_preserves = False
    base_import = base_row.get('grid_import_kwh')
    dyn_import = dyn_row.get('grid_import_kwh')
    if (dyn_complete and base_import is not None and np.isfinite(base_import)
            and dyn_import is not None and np.isfinite(dyn_import)
            and dyn_import <= base_import + 0.1):
        dyn_p25_ok = True
        for m in range(3, 10):
            b_vals = [w[m - 1].get('p25_peak_soc_pct') for w in base_weathers]
            d_vals = [w[m - 1].get('p25_peak_soc_pct') for w in dyn_weathers]
            if (not b_vals or not d_vals
                    or any(v is None or not np.isfinite(v) for v in b_vals)
                    or any(v is None or not np.isfinite(v) for v in d_vals)):
                dyn_p25_ok = False
                break
            b_p25_m = float(np.median(b_vals))
            d_p25_m = float(np.median(d_vals))
            drop = b_p25_m - d_p25_m
            if not np.isfinite(drop) or drop > 5.0 + 1e-9:
                dyn_p25_ok = False
                break
        dyn_preserves = dyn_p25_ok
    dyn_row['preserves_autarky_soc'] = dyn_preserves
    rows.append(dyn_row)

    # Dynamic NPV eligible when complete finite exact NPV >= 0 regardless of preservation
    dyn_metrics_ok = all(
        dyn_row.get(k) is not None and np.isfinite(dyn_row[k])
        for k in ('clipped_kwh', 'grid_import_kwh', 'net_energy_value_delta_eur')
    )
    dyn_eligible_npv = bool(dyn_complete and dyn_exact_npv is not None and np.isfinite(dyn_exact_npv) and dyn_exact_npv >= 0.0 and dyn_metrics_ok)

    # NPV winner: max exact NPV then clipping; require >= 0 exact NPV
    npv_pool = [r for r in verified_candidates if r.get('equity_npv_delta') is not None and r['equity_npv_delta'] >= 0.0]
    if dyn_eligible_npv:
        npv_pool.append(dyn_row)

    rec_npv = max(npv_pool, key=lambda r: (r['equity_npv_delta'], r.get('clipping_saved_kwh', 0.0) or 0.0)) if npv_pool else None

    # Rank verified profitable rows by clipping saved; autarky is diagnostic.
    clip_pool = [
        r for r in rows
        if r.get('status') in ('feasible', 'verified', 'benchmark')
        and r.get('metrics_basis') == 'verified_trace'
        and all(r.get(k) is not None and np.isfinite(r[k]) for k in
                ('clipped_kwh', 'grid_import_kwh', 'net_energy_value_delta_eur',
                 'clipping_saved_kwh', 'equity_npv_delta'))
        and r['clipping_saved_kwh'] > 0.0
        and r['equity_npv_delta'] > 0.0
    ]

    rec_clipping = max(clip_pool, key=lambda r: (r.get('clipping_saved_kwh', 0.0) or 0.0, r['equity_npv_delta'])) if clip_pool else None

    # Sort rows: baseline first, then hour ascending (two spans allowed), then dynamic last
    baseline_rows = [r for r in rows if r.get('routing_priority') == 'battery_first']
    candidate_rows = [r for r in rows if r.get('routing_priority') == 'scheduled_hybrid']
    dynamic_rows = [r for r in rows if r.get('routing_priority') == 'dynamic_reserve']

    candidate_rows.sort(key=lambda r: (
        r['routing_switch_hour'] if r.get('routing_switch_hour') is not None else 999,
        r['scheduled_start_month'] if r.get('scheduled_start_month') is not None else 0,
        r['scheduled_end_month'] if r.get('scheduled_end_month') is not None else 0
    ))
    sorted_rows = baseline_rows + candidate_rows + dynamic_rows

    return sorted_rows, rec_npv, rec_clipping


def switch_hour_tradeoff(settings):
    """Cached switch hour tradeoff evaluation for scheduled hybrid dispatch.

    Args:
        settings (dict): Feasibility calculation settings.

    Returns:
        dict: {
            'rows': list of dicts (baseline row followed by candidates and dynamic_reserve benchmark),
            'recommendation_npv': dict of best NPV feasible row or None,
            'recommendation_clipping': dict of best clipping feasible row or None,
            'recommendation': dict of best NPV feasible row or None (alias),
            'horizon_years': int,
            'elapsed_seconds': float,
            'time_basis': str,
            'representative_basis': str,
        }
    """
    t_start = time.perf_counter()
    import feasibility.engine as eng

    s = eng.validate(settings)
    frozen_s = tuple(sorted((k, eng.freeze(v)) for k, v in s.items()))
    price_ver = eng.price_fingerprint(s)

    rows, rec_npv, rec_clipping = _cached_switch_hour_tradeoff(current_runtime().key, frozen_s, price_ver)

    return {
        'rows': deepcopy(rows),
        'recommendation_npv': deepcopy(rec_npv),
        'recommendation_clipping': deepcopy(rec_clipping),
        'recommendation': deepcopy(rec_npv),
        'horizon_years': int(s.get('horizon_years', 25)),
        'elapsed_seconds': round(time.perf_counter() - t_start, 6),
        'time_basis': 'UTC+01:00 fixed standard time',
        'representative_basis': 'All historical weather years; candidate and Self-Use paired by year',
    }
