"""Regression tests for schedule optimizer multi-objective selection and weather SoC statistics."""
import datetime as dt
import unittest
from unittest.mock import patch
import numpy as np

from test_support import IsolatedDefaultsTestCase
from feasibility import assumptions, engine, schedule_optimizer


class TestScheduleOptimizerObjectives(IsolatedDefaultsTestCase):
    """Regression suite covering NPV/clipping trade-offs and per-weather peak SoC evaluation."""

    def setUp(self):
        super().setUp()
        schedule_optimizer._cached_switch_hour_tradeoff.cache_clear()

    def tearDown(self):
        schedule_optimizer._cached_switch_hour_tradeoff.cache_clear()
        super().tearDown()

    def test_objective_selection_flat_vs_declining_export_factor(self):
        """Part A: MaxNPV selects h10 when flat and h13 when declining; Year 1 values match."""
        s = assumptions.defaults()
        s.update({
            'pv_dispatch_model': 'physical',
            'pv_battery_coupling': 'dc_coupled',
            'battery_kwh': 10.0,
            'horizon_years': 20,
            'export_market_price': 0.1,
            'export_irpef': 0.37,
            'regional_additional_tax': 0.0,
            'municipal_additional_tax': 0.0,
            'pmg_election': False,
            'cer_enabled': False,
            'inflation': 0.0,
            'energy_real_escalation': 0.0,
            'regulated_real_escalation': 0.0,
            'real_discount_rate': 0.04,
            'export_price_future_years': 10,
            'actual_price_future_factor': 1.0,
            'load_scenario': 'baseline',
        })

        H, W = 20, 2
        sc = 25.0

        def mock_energy_cache(key, price_ver):
            d = dict(key)
            p = d.get('routing_priority')
            h = d.get('routing_switch_hour')
            ann = np.zeros((H, W, 14))
            ann[:, :, 0] = 1200.0
            ann[:, :, 1] = 2000.0
            if p in ('battery_first', 'dynamic_reserve') or p != 'scheduled_hybrid':
                ann[:, :, 5] = 300.0
                ann[:, :, 6] = 1000.0
                ann[:, :, 11] = 900.0
            elif h == 10:
                ann[:, :, 5] = 300.0
                ann[:, :, 6] = 1000.0
                ann[:, :, 11] = 1000.0
            elif h == 13:
                ann[:, :, 5] = 300.0 - sc
                ann[:, :, 6] = 1000.0 - sc
                ann[:, :, 11] = 900.0 - sc
            else:
                ann[:, :, 5] = 300.0
                ann[:, :, 6] = 1000.0
                ann[:, :, 11] = 900.0
            return ann, {}

        def mock_trace(eng, cfg, ekey=None, price_ver=0, base_ri=None, prepared_context=None):
            p = cfg.get('routing_priority')
            h = cfg.get('routing_switch_hour')
            if p in ('battery_first', 'dynamic_reserve') or p != 'scheduled_hybrid':
                h = None
            res = []
            for _ in range(W):
                months = []
                for m in range(1, 13):
                    if h == 10:
                        imp, exp, wexp, clip = 25.0, 83.33, 1000.0 / 12, 0.5
                    elif h == 13:
                        imp, exp, wexp, clip = (300.0 - sc) / 12, (1000.0 - sc) / 12, (900.0 - sc) / 12, 0.5
                    else:
                        imp, exp, wexp, clip = 25.0, 83.33, 75.0, 1.0
                    months.append({
                        'month': m, 'pv_input_kwh': 100.0, 'load_kwh': 100.0,
                        'grid_import_kwh': imp, 'grid_export_kwh': exp,
                        'export_price_weighted_kwh': wexp, 'clipped_kwh': clip,
                        'p25_peak_soc_pct': 100.0, 'median_peak_soc_pct': 100.0,
                    })
                res.append(months)
            return 0, {'monthly_clipping': res[0], 'weather_monthly_clipping': res}

        with patch('feasibility.schedule_optimizer.prepare_dispatch_context', return_value={}),              patch.object(engine, '_energy_cache', side_effect=mock_energy_cache),              patch.object(schedule_optimizer, '_trace_monthly_summary', side_effect=mock_trace):
            schedule_optimizer._cached_switch_hour_tradeoff.cache_clear()
            out_flat = schedule_optimizer.switch_hour_tradeoff(s)
            self.assertEqual(out_flat['recommendation_npv']['routing_switch_hour'], 10)

            schedule_optimizer._cached_switch_hour_tradeoff.cache_clear()
            s_decl = dict(s, actual_price_future_factor=0.3)
            out_decl = schedule_optimizer.switch_hour_tradeoff(s_decl)
            self.assertEqual(out_decl['recommendation_npv']['routing_switch_hour'], 13)

            for rf, rd in zip(out_flat['rows'], out_decl['rows']):
                self.assertEqual(rf['net_energy_value_delta_eur'], rd['net_energy_value_delta_eur'])
                self.assertEqual(rf['grid_import_kwh'], rd['grid_import_kwh'])
                self.assertEqual(rf['grid_export_kwh'], rd['grid_export_kwh'])
                self.assertEqual(rf['clipped_kwh'], rd['clipped_kwh'])

    def test_trace_monthly_summary_per_weather_and_make_row_p25(self):
        """Part B: Real per-weather P25 in _trace_monthly_summary and public switch tradeoff pooling."""
        days = 365
        start_date = dt.date(2027, 1, 1)
        months_per_day = np.array([(start_date + dt.timedelta(days=d)).month for d in range(days)], dtype=int)
        month_days = months_per_day - 1
        day_counts = np.bincount(month_days, minlength=12)
        target_months = np.repeat(months_per_day, 24)
        month_hours = target_months - 1
        zeros_24 = np.zeros(365 * 24)

        ctx = {
            'mapped_data': {'mapped_pv': [zeros_24, zeros_24], 'mapped_load': [zeros_24, zeros_24], 'mapped_pzo': [zeros_24, zeros_24]},
            'ones_price': np.ones(365 * 24), 'valid': np.ones(365 * 24, dtype=bool),
            'target_months': target_months, 'target_std_hours': np.tile(np.arange(24), 365),
            'ri': 0, 'days': days, 'month_hours': month_hours, 'month_days': month_days, 'day_counts': day_counts,
        }

        cap = 10.0
        tr0, tr1 = np.zeros((365 * 24, 8)), np.zeros((365 * 24, 8))
        tr0.reshape(365, 24, 8)[:, 12, 4] = 0.5 * cap
        tr1.reshape(365, 24, 8)[:, 12, 4] = 0.9 * cap

        s_b1 = assumptions.defaults()
        s_b1.update({'battery_kwh': cap, 'pv_dispatch_model': 'physical', 'pv_battery_coupling': 'dc_coupled'})

        with patch.object(engine, 'dispatch_weather_years',
                          return_value=(np.zeros((2, 14)), np.stack((tr0, tr1)))):
            _, rep = schedule_optimizer._trace_monthly_summary(engine, s_b1, prepared_context=ctx)
            wm = rep['weather_monthly_clipping']
            self.assertEqual(wm[0][0]['p25_peak_soc_pct'], 50.0)
            self.assertEqual(wm[1][0]['p25_peak_soc_pct'], 90.0)

        # B2: public switch_hour_tradeoff with June 80/90, outside active 100, span Apr-Jun
        s_b2 = assumptions.defaults()
        s_b2.update({
            'pv_dispatch_model': 'physical', 'pv_battery_coupling': 'dc_coupled',
            'battery_kwh': 10.0, 'horizon_years': 20, 'export_market_price': 0.1,
            'export_irpef': 0.0, 'regional_additional_tax': 0.0, 'municipal_additional_tax': 0.0,
            'pmg_election': False, 'cer_enabled': False, 'inflation': 0.0,
            'energy_real_escalation': 0.0, 'regulated_real_escalation': 0.0,
            'real_discount_rate': 0.04, 'export_price_future_years': 10,
            'actual_price_future_factor': 1.0, 'load_scenario': 'baseline',
        })

        H, W = 20, 2

        def mock_energy_cache_b2(key, price_ver):
            d = dict(key)
            p = d.get('routing_priority')
            ann = np.zeros((H, W, 14))
            ann[:, :, 0] = 1200.0
            ann[:, :, 1] = 2000.0
            if p in ('battery_first', 'dynamic_reserve') or p != 'scheduled_hybrid':
                ann[:, :, 5] = 300.0
                ann[:, :, 6] = 1000.0
                ann[:, :, 11] = 900.0
            else:
                ann[:, :, 5] = 300.0
                ann[:, :, 6] = 1000.0
                ann[:, :, 11] = (950.0 if d.get('scheduled_start_month')==4 and d.get('scheduled_end_month')==6 else 949.0)
            return ann, {}

        def mock_trace_b2(eng, cfg, ekey=None, price_ver=0, base_ri=None, prepared_context=None):
            p = cfg.get('routing_priority')
            sm, em = cfg.get('scheduled_start_month'), cfg.get('scheduled_end_month')
            res = []
            for w in range(W):
                months = []
                for m in range(1, 13):
                    is_base = (p in ('battery_first', 'dynamic_reserve') or p != 'scheduled_hybrid')
                    active_m = (4 <= m <= 6) and (sm is None or (sm <= m <= em))
                    if is_base or not active_m:
                        wexp, clip, p25 = 75.0, 1.0, 100.0
                    else:
                        wexp, clip = 80.0, 0.5
                        p25 = (80.0 if w == 0 else 90.0) if m == 6 else 100.0
                    months.append({
                        'month': m, 'pv_input_kwh': 100.0, 'load_kwh': 100.0,
                        'grid_import_kwh': 25.0, 'grid_export_kwh': 83.33,
                        'export_price_weighted_kwh': wexp, 'clipped_kwh': clip,
                        'p25_peak_soc_pct': p25, 'median_peak_soc_pct': p25,
                    })
                res.append(months)
            return 0, {'monthly_clipping': res[0], 'weather_monthly_clipping': res}

        with patch('feasibility.schedule_optimizer.prepare_dispatch_context', return_value={}),              patch.object(engine, '_energy_cache', side_effect=mock_energy_cache_b2),              patch.object(schedule_optimizer, '_trace_monthly_summary', side_effect=mock_trace_b2):
            schedule_optimizer._cached_switch_hour_tradeoff.cache_clear()
            out = schedule_optimizer.switch_hour_tradeoff(s_b2)
            rec_npv = out['recommendation_npv']
            rec_clip = out['recommendation_clipping']
            self.assertEqual(rec_npv['active_months'], [4, 5, 6])
            self.assertEqual(rec_npv['min_p25_peak_soc_pct'], 85.0)
            self.assertFalse(rec_npv['preserves_autarky_soc'])
            self.assertFalse(rec_clip['preserves_autarky_soc'])
            self.assertEqual(rec_clip['min_p25_peak_soc_pct'], 85.0)
            self.assertEqual(rec_clip['active_months'], [4, 5, 6])


if __name__ == '__main__':
    unittest.main()
