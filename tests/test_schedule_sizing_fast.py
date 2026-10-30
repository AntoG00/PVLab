"""Fast regression suite for sizing schedule candidates search and context cache."""
import test_support
import builtins
from unittest.mock import patch
import numpy as np

from test_support import IsolatedDefaultsTestCase
import feasibility.engine as e
import feasibility.schedule_optimizer as so


def _settings(dc=True, **overrides):
    base = {
        'pv_dispatch_model': 'physical',
        'pv_battery_coupling': 'dc_coupled' if dc else 'ac_equivalent',
        'battery_kwh': 5.0,
        'battery_modules': 1,
        'routing_priority': 'scheduled_hybrid',
        'inverter_kw': 3.0,
        'south_panels': 12,
        'load_scenario': 'baseline',
        'horizon_years': 20,
        'export_price_profile': 'flat_benchmark',
        'export_capture_factor': 0.95,
        'actual_price_future_factor': 1.15,
        'export_price_future_years': 5.0,
        'energy_real_escalation': 0.015,
        'pmg_nominal_escalation': 0.025,
        'inflation': 0.02,
        'real_discount_rate': 0.03,
        'export_market_price': 0.11,
        'pmg_price': 0.08,
        'pmg_election': True,
        'cer_enabled': True,
        'cer_tariff': 0.11,
        'cer_eligible_export_share': 0.5,
        'cer_user_allocation': 0.8,
        'export_fee_tax_deductible': True,
        'gse_fee_eur_kw_year': 5.0,
        'gse_fee_vat': 0.22,
        'export_irpef': 0.23,
        'regional_additional_tax': 0.012,
        'municipal_additional_tax': 0.008,
    }
    base.update(overrides)
    return e.validate(base)


def _context(*args, **kwargs):
    ml = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    days, t_len = 365, 8760
    month_days = np.repeat(np.arange(12), ml)
    month_hours = np.repeat(month_days, 24)
    target_std_hours = np.tile(np.arange(24), days)
    target_months = month_hours + 1
    base_pv = np.maximum(np.sin((target_std_hours - 6) * np.pi / 12.0), 0.0)
    load = 0.5 + 0.8 * (target_std_hours >= 18)
    pzo = 0.8 + 0.4 * (target_std_hours >= 18)
    mapped_data = {
        'target_len': t_len,
        'forecast_year': 2027,
        'is_leap': False,
        'target_months': target_months,
        'target_std_hours': target_std_hours,
        'mapped_pv': [base_pv * s for s in (3, 5, 7)],
        'mapped_load': [load.copy() for _ in range(3)],
        'mapped_pzo': [pzo.copy() for _ in range(3)],
    }
    return {
        'ri': None,
        'mapped_data': mapped_data,
        'target_len': t_len,
        'target_months': target_months,
        'target_std_hours': target_std_hours,
        'valid': np.ones(t_len, dtype=bool),
        'ones_price': np.ones(t_len, dtype=float),
        'forecast_year': 2027,
        'days': days,
        'month_hours': month_hours,
        'month_days': month_days,
        'day_counts': np.array(ml),
        'mapped_pzo': [pzo.copy() for _ in range(3)],
    }


def _capture(module, settings):
    rows, orig_sorted = [], builtins.sorted

    def _rec(seq, *a, **k):
        res = orig_sorted(seq, *a, **k)
        if seq and isinstance(seq[0], dict) and 'routing_switch_hour' in seq[0] and 'fin_val' in seq[0]:
            cols = ('routing_switch_hour', 'scheduled_start_month', 'scheduled_end_month', 'fin_val', 'worst_clip', 'mean_clip')
            if len(rows) == 0:
                rows.extend([[float(d.get(c, 0.0)) for c in cols] for d in seq])
        return res

    with patch.object(module, 'prepare_dispatch_context', side_effect=_context), \
         patch.object(module, 'sorted', side_effect=_rec, create=True):
        sel = module.sizing_schedule_candidates(settings)

    triples = [[d['routing_switch_hour'], d['scheduled_start_month'], d['scheduled_end_month']] for d in sel]
    return np.asarray(rows, dtype=float), np.asarray(triples, dtype=int)


class TestScheduleSizingFast(IsolatedDefaultsTestCase):

    def test_energy_only_monthly_totals_vs_full_reports(self):
        for dc in (True, False):
            for bat in (0.0, 5.0):
                s = _settings(dc=dc, battery_kwh=bat)
                ctx_energy = _context()
                ri_e, disp_e = so._trace_monthly_summary(e, s, prepared_context=ctx_energy, energy_only=True)
                ctx_full = _context()
                ri_f, disp_f = so._trace_monthly_summary(e, s, prepared_context=ctx_full, energy_only=False)

                self.assertEqual(ri_e, ri_f)
                weathers_e = disp_e['weather_monthly_clipping']
                weathers_f = disp_f['weather_monthly_clipping']
                self.assertEqual(len(weathers_e), len(weathers_f))

                for w_idx in range(len(weathers_e)):
                    months_e = weathers_e[w_idx]
                    months_f = weathers_f[w_idx]
                    self.assertEqual(len(months_e), 12)
                    self.assertEqual(len(months_f), 12)
                    for m_idx in range(12):
                        row_e = months_e[m_idx]
                        row_f = months_f[m_idx]
                        self.assertEqual(row_e['month'], row_f['month'])
                        self.assertEqual(row_e['pv_input_kwh'], row_f['pv_input_kwh'])
                        self.assertEqual(row_e['grid_import_kwh'], row_f['grid_import_kwh'])
                        self.assertEqual(row_e['grid_export_kwh'], row_f['grid_export_kwh'])
                        self.assertEqual(row_e['export_price_weighted_kwh'], row_f['export_price_weighted_kwh'])
                        self.assertEqual(row_e['clipped_kwh'], row_f['clipped_kwh'])
                        self.assertNotIn('mean_peak_soc_pct', row_e)
                        self.assertNotIn('clipping_hours', row_e)
                        self.assertIn('mean_peak_soc_pct', row_f)
                        self.assertIn('clipping_hours', row_f)

    def test_independent_per_candidate_reference_all_931_candidates_and_shortlist(self):
        s = _settings(dc=True)
        rows, batched_selected = _capture(so, s)
        self.assertEqual(rows.shape, (931, 6))

        ctx = _context()
        base_s = dict(s, routing_priority='battery_first')
        _, base_disp = so._trace_monthly_summary(e, base_s, prepared_context=ctx, energy_only=True)
        base_weathers = base_disp['weather_monthly_clipping']

        cand_weathers_by_h = {}
        switch_hours = list(range(9, 16))
        for h in switch_hours:
            cfg = dict(s, routing_priority='scheduled_hybrid', routing_switch_hour=h, scheduled_start_month=1, scheduled_end_month=12)
            _, cand_disp = so._trace_monthly_summary(e, cfg, prepared_context=ctx, energy_only=True)
            cand_weathers_by_h[h] = cand_disp['weather_monthly_clipping']

        base_imp = np.array([[float(w[m]['grid_import_kwh']) for m in range(12)] for w in base_weathers], dtype=float)
        base_exp = np.array([[float(w[m]['grid_export_kwh']) for m in range(12)] for w in base_weathers], dtype=float)
        base_pzo = np.array([[float(w[m]['export_price_weighted_kwh']) for m in range(12)] for w in base_weathers], dtype=float)
        base_clip = np.array([[float(w[m]['clipped_kwh']) for m in range(12)] for w in base_weathers], dtype=float)
        base_pv = np.array([[float(w[m]['pv_input_kwh']) for m in range(12)] for w in base_weathers], dtype=float)

        base_ann_imp = np.sum(base_imp, axis=1)
        base_ann_exp = np.sum(base_exp, axis=1)
        base_ann_pzo = np.sum(base_pzo, axis=1)
        base_ann_clip = np.sum(base_clip, axis=1)
        base_ann_pv = np.sum(base_pv, axis=1)
        pv_safe = np.where(base_ann_pv > 0.0, base_ann_pv, 1.0)

        delta_imp_by_h = {h: np.array([[float(w[m]['grid_import_kwh']) for m in range(12)] for w in cand_weathers_by_h[h]], dtype=float) - base_imp for h in switch_hours}
        delta_exp_by_h = {h: np.array([[float(w[m]['grid_export_kwh']) for m in range(12)] for w in cand_weathers_by_h[h]], dtype=float) - base_exp for h in switch_hours}
        delta_pzo_by_h = {h: np.array([[float(w[m]['export_price_weighted_kwh']) for m in range(12)] for w in cand_weathers_by_h[h]], dtype=float) - base_pzo for h in switch_hours}
        delta_clip_by_h = {h: np.array([[float(w[m]['clipped_kwh']) for m in range(12)] for w in cand_weathers_by_h[h]], dtype=float) - base_clip for h in switch_hours}

        counts = sum(s.get(n + '_panels', 0) for n in e.OPTIMIZE_ROOFS)
        panel_watts = float(s.get('panel_watts', 440.0) or 440.0)
        kwp = counts * panel_watts / 1000.0

        capture = s.get('export_capture_factor', 1.0) if e.price_profile(s)[1] == 'flat_solar_capture_benchmark' else 1.0
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
            retail_arr[yy] = float(e.electric_bill(1, s, year) - e.electric_bill(0, s, year))
            ramp = min(yy / future_years, 1.0)
            future = 1.0 + (future_factor - 1.0) * ramp
            exp_unit_arr[yy] = exp_market_price * capture * future * infl * ((1.0 + energy_real_esc) ** yy)
            pmg_unit_arr[yy] = pmg_price * ((1.0 + pmg_nom_esc) ** yy)
            fees_yy = (0.0 if kwp <= 3.0 else min(gse_fee_kw * kwp, 10000.0)) * (1.0 + gse_fee_vat) * infl
            fees_ded_arr[yy] = fees_yy if exp_fee_ded else 0.0
            cer_arr[yy] = cer_share * cer_tariff * cer_alloc * infl if cer_enabled else 0.0

        def compute_vector_of_weather_lifetime(imp_vec, exp_vec, pzo_vec):
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

        base_lifetime_vals = compute_vector_of_weather_lifetime(base_ann_imp, base_ann_exp, base_ann_pzo)

        spans = []
        for length in range(1, 12):
            for start_m in range(1, 13):
                months = tuple(((start_m - 1 + i) % 12) + 1 for i in range(length))
                spans.append((start_m, months[-1], [m - 1 for m in months]))
        spans.append((1, 12, list(range(12))))

        ref_candidates = []
        idx = 0
        for h in switch_hours:
            d_imp = delta_imp_by_h[h]
            d_exp = delta_exp_by_h[h]
            d_pzo = delta_pzo_by_h[h]
            d_clip = delta_clip_by_h[h]

            for start_m, end_m, m_indices in spans:
                c_imp = base_ann_imp + np.sum(d_imp[:, m_indices], axis=1)
                c_exp = base_ann_exp + np.sum(d_exp[:, m_indices], axis=1)
                c_pzo = base_ann_pzo + np.sum(d_pzo[:, m_indices], axis=1)
                c_clip = np.maximum(base_ann_clip + np.sum(d_clip[:, m_indices], axis=1), 0.0)

                lt_vals = compute_vector_of_weather_lifetime(c_imp, c_exp, c_pzo)
                fin_val = float(np.mean(lt_vals - base_lifetime_vals))
                worst_clip = float(np.max(np.where(base_ann_pv > 0.0, c_clip / pv_safe, 0.0)))
                mean_clip = float(np.mean(c_clip))

                ref_candidates.append({
                    'routing_switch_hour': h,
                    'scheduled_start_month': start_m,
                    'scheduled_end_month': end_m,
                    'fin_val': fin_val,
                    'worst_clip': worst_clip,
                    'mean_clip': mean_clip,
                })

                self.assertEqual(rows[idx, 0], h)
                self.assertEqual(rows[idx, 1], start_m)
                self.assertEqual(rows[idx, 2], end_m)
                self.assertEqual(rows[idx, 3], fin_val)
                self.assertEqual(rows[idx, 4], worst_clip)
                self.assertEqual(rows[idx, 5], mean_clip)
                idx += 1

        fin_sorted = sorted(ref_candidates, key=lambda c: (-c['fin_val'], c['worst_clip'], c['mean_clip'], c['routing_switch_hour'], c['scheduled_start_month'], c['scheduled_end_month']))
        clip_sorted = sorted(ref_candidates, key=lambda c: (c['worst_clip'], c['mean_clip'], -c['fin_val'], c['routing_switch_hour'], c['scheduled_start_month'], c['scheduled_end_month']))

        selected, seen = [], set()
        for c in fin_sorted:
            tup = (c['routing_switch_hour'], c['scheduled_start_month'], c['scheduled_end_month'])
            if tup not in seen:
                seen.add(tup)
                selected.append(list(tup))
                if len(selected) >= 4:
                    break
        clip_cnt = 0
        for c in clip_sorted:
            tup = (c['routing_switch_hour'], c['scheduled_start_month'], c['scheduled_end_month'])
            if tup not in seen:
                seen.add(tup)
                selected.append(list(tup))
                clip_cnt += 1
                if clip_cnt >= 2:
                    break
        selected = selected[:6]
        inp_tup = [s.get('routing_switch_hour', 11), s.get('scheduled_start_month', 1), s.get('scheduled_end_month', 12)]
        if tuple(inp_tup) not in seen:
            selected.append(inp_tup)

        np.testing.assert_array_equal(batched_selected, np.asarray(selected, dtype=int))

    def test_context_cache_lru_and_ri_management(self):
        cache = {}
        prep_calls = []
        trace_call_ris = []

        def _synthetic_prep(eng, settings, base_ri=None):
            prep_calls.append(settings.get('south_panels'))
            return _context()

        def _mock_trace(eng, cfg, prepared_context=None, energy_only=False):
            if prepared_context is not None:
                trace_call_ris.append(prepared_context.get('ri'))
                if prepared_context.get('ri') is None:
                    prepared_context['ri'] = 1
            w_list = [[{
                'grid_import_kwh': 100.0, 'grid_export_kwh': 50.0,
                'export_price_weighted_kwh': 10.0, 'clipped_kwh': 5.0,
                'pv_input_kwh': 200.0,
            } for _ in range(12)] for _ in range(3)]
            return 1, {'weather_monthly_clipping': w_list}

        with patch.object(so, 'prepare_dispatch_context', side_effect=_synthetic_prep), \
             patch.object(so, '_trace_monthly_summary', side_effect=_mock_trace):

            for panels in (10, 11, 12, 13):
                s = _settings(south_panels=panels)
                so.sizing_schedule_candidates(s, context_cache=cache)

            self.assertEqual(len(cache), 4)
            self.assertEqual(len(prep_calls), 4)
            # 4 configs * 8 calls per config = 32 calls total
            self.assertEqual(len(trace_call_ris), 32)

            # Check South=10 reuse: hits cache without prep call, updates LRU order
            trace_call_ris.clear()
            s10 = _settings(south_panels=10)
            k10 = tuple(s10[k] for k in e.PV_PROFILE_KEYS) + (s10['pv_battery_coupling'],)
            so.sizing_schedule_candidates(s10, context_cache=cache)
            self.assertEqual(len(prep_calls), 4)
            self.assertEqual(len(trace_call_ris), 8)
            # Within the 8 calls: Call 1 sees None, calls 2..8 share ri=1
            self.assertIsNone(trace_call_ris[0])
            self.assertEqual(trace_call_ris[1:], [1] * 7)
            self.assertEqual(list(cache.keys())[-1], k10)

            # 5th entry evicts LRU (which was south_panels=11)
            s11 = _settings(south_panels=11)
            k11 = tuple(s11[k] for k in e.PV_PROFILE_KEYS) + (s11['pv_battery_coupling'],)
            s14 = _settings(south_panels=14)
            so.sizing_schedule_candidates(s14, context_cache=cache)
            self.assertEqual(len(cache), 4)
            self.assertEqual(len(prep_calls), 5)
            self.assertNotIn(k11, cache)

            # Changing topology/coupling key to ac_equivalent causes cache miss
            s14_ac = _settings(south_panels=14, dc=False)
            k14_ac = tuple(s14_ac[k] for k in e.PV_PROFILE_KEYS) + (s14_ac['pv_battery_coupling'],)
            so.sizing_schedule_candidates(s14_ac, context_cache=cache)
            self.assertEqual(len(prep_calls), 6)
            self.assertIn(k14_ac, cache)

            # Failed preparation is not cached
            def _failing_prep(eng, settings, base_ri=None):
                raise RuntimeError("PV GIS download failed")

            with patch.object(so, 'prepare_dispatch_context', side_effect=_failing_prep):
                s_fail = _settings(south_panels=99)
                k_fail = tuple(s_fail[k] for k in e.PV_PROFILE_KEYS) + (s_fail['pv_battery_coupling'],)
                fb = so.sizing_schedule_candidates(s_fail, context_cache=cache)
                self.assertTrue(len(fb) > 0)
                self.assertNotIn(k_fail, cache)

            # Cached entries maintain ri=None
            k14 = tuple(s14[k] for k in e.PV_PROFILE_KEYS) + (s14['pv_battery_coupling'],)
            self.assertIsNone(cache[k14]['ri'])

            # Changing battery capacity reuses cached PV without new prep call,
            # receives ri=None anew in call 1, shares it across 8 calls, and leaves cache with ri=None
            trace_call_ris.clear()
            s14_bat10 = _settings(south_panels=14, battery_kwh=10.0)
            so.sizing_schedule_candidates(s14_bat10, context_cache=cache)
            self.assertEqual(len(prep_calls), 6)
            self.assertEqual(len(trace_call_ris), 8)
            self.assertIsNone(trace_call_ris[0])
            self.assertEqual(trace_call_ris[1:], [1] * 7)
            self.assertIsNone(cache[k14]['ri'])

    def test_tied_score_shortlist_traversal_and_order(self):
        s = _settings(dc=True, routing_switch_hour=11, scheduled_start_month=1, scheduled_end_month=12)

        def _tied_mock_trace(eng, cfg, prepared_context=None, energy_only=False):
            w_list = [[{
                'grid_import_kwh': 100.0, 'grid_export_kwh': 50.0,
                'export_price_weighted_kwh': 10.0, 'clipped_kwh': 5.0,
                'pv_input_kwh': 200.0,
            } for _ in range(12)] for _ in range(3)]
            return 0, {'weather_monthly_clipping': w_list}

        with patch.object(so, 'prepare_dispatch_context', side_effect=_context), \
             patch.object(so, '_trace_monthly_summary', side_effect=_tied_mock_trace):
            selected = so.sizing_schedule_candidates(s, max_candidates=6)

        # In tie-break condition, all candidates have delta=0 and equal clipping.
        # fin_sorted takes top 4 in ascending (switch_hour, start_month, end_month):
        # (9, 1, 1), (9, 1, 2), (9, 1, 3), (9, 1, 4).
        # clip_sorted skips seen and takes next 2:
        # (9, 1, 5), (9, 1, 6).
        # Input schedule (11, 1, 12) was not in seen, so appended as 7th candidate.
        expected = [
            {'routing_switch_hour': 9, 'scheduled_start_month': 1, 'scheduled_end_month': 1},
            {'routing_switch_hour': 9, 'scheduled_start_month': 1, 'scheduled_end_month': 2},
            {'routing_switch_hour': 9, 'scheduled_start_month': 1, 'scheduled_end_month': 3},
            {'routing_switch_hour': 9, 'scheduled_start_month': 1, 'scheduled_end_month': 4},
            {'routing_switch_hour': 9, 'scheduled_start_month': 1, 'scheduled_end_month': 5},
            {'routing_switch_hour': 9, 'scheduled_start_month': 1, 'scheduled_end_month': 6},
            {'routing_switch_hour': 11, 'scheduled_start_month': 1, 'scheduled_end_month': 12},
        ]
        self.assertEqual(selected, expected)
