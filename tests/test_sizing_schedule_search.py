"""Behavior tests for sizing schedule screening."""
import copy
import unittest
from unittest.mock import patch
import numpy as np

import feasibility.engine as eng
from feasibility.schedule_optimizer import sizing_schedule_candidates
from test_support import IsolatedDefaultsTestCase


def _make_weather(imp=100.0, exp=50.0, pzo=5.0, clip=10.0, pv=200.0, overrides=None):
    years = []
    for _ in range(2):
        months = []
        for m in range(12):
            row = {
                'grid_import_kwh': imp,
                'grid_export_kwh': exp,
                'export_price_weighted_kwh': pzo,
                'clipped_kwh': clip,
                'pv_input_kwh': pv,
            }
            if overrides and m in overrides:
                row.update(overrides[m])
            months.append(row)
        years.append(months)
    return years


class TestSizingScheduleSearch(IsolatedDefaultsTestCase):

    def test_exhaustive_hardware_with_screened_schedules_is_best_found(self):
        settings = eng.validate({'pv_dispatch_model':'physical','routing_priority':'scheduled_hybrid',
                                 'battery_modules':1,'battery_kwh':5.12,'horizon_years':2})
        options = {'battery_mode':'fixed','panel_maxima':dict.fromkeys(eng.OPTIMIZE_ROOFS, 0)}
        options['panel_maxima']['south'] = 1
        shortlist = [{'routing_switch_hour':10,'scheduled_start_month':11,'scheduled_end_month':2}]
        with patch('feasibility.schedule_optimizer.sizing_schedule_candidates', return_value=shortlist):
            summary = eng.solar_sizing(settings, options)['search_summary']
        self.assertTrue(summary['layout_exact'])
        self.assertFalse(summary['schedule_search_exact'])
        self.assertFalse(summary['exact'])
        self.assertFalse(summary['certified_global_optimum'])

    def setUp(self):
        super().setUp()
        self.settings = eng.validate({
            'pv_dispatch_model': 'physical',
            'battery_kwh': 10.0,
            'routing_switch_hour': 11,
            'scheduled_start_month': 5,
            'scheduled_end_month': 9,
            'export_market_price': 0.12,
            'export_capture_factor': 1.0,
        })
        self.trace_calls = []

    def _mock_trace(self, _eng, cfg, prepared_context=None, energy_only=False):
        self.assertTrue(energy_only)
        self.trace_calls.append((cfg.get('routing_priority'), cfg.get('routing_switch_hour')))
        if cfg.get('routing_priority') == 'battery_first':
            return 0, {'weather_monthly_clipping': _make_weather()}
        h = cfg.get('routing_switch_hour', 11)
        bat = float(cfg.get('battery_kwh', 10.0) or 0.0)
        boost = 70.0 if (h == 14 if bat > 10.0 else h == 10) else 10.0
        # Wraparound Nov-Feb [10, 11, 0, 1] gain vs heavy penalty outside ideal window [2..9]
        overrides = {m: {'export_price_weighted_kwh': boost, 'grid_export_kwh': 120.0} for m in (10, 11, 0, 1)}
        for m in range(2, 10):
            overrides[m] = {'grid_import_kwh': 800.0, 'export_price_weighted_kwh': 0.0}
        return 0, {'weather_monthly_clipping': _make_weather(overrides=overrides)}

    @patch('feasibility.schedule_optimizer.prepare_dispatch_context', return_value={'ctx': True})
    @patch('feasibility.engine.price_profile', return_value=(np.zeros(24), 'profile'))
    def test_sizing_schedule_candidates_behavior(self, mock_price, mock_prep):
        with patch('feasibility.schedule_optimizer._trace_monthly_summary', side_effect=self._mock_trace):
            s = copy.deepcopy(self.settings)
            cands = sizing_schedule_candidates(s)

            # Assert all hours 9..15 traced once + baseline
            expected_calls = [('battery_first', 11)] + [('scheduled_hybrid', h) for h in range(9, 16)]
            self.assertEqual(self.trace_calls, expected_calls)

            # <=7 default shortlist and no mutation
            self.assertLessEqual(len(cands), 7)
            self.assertGreater(len(cands), 0)
            self.assertEqual(s, self.settings)

            # Wraparound Nov-Feb financial winner shortlisted, winter penalty excludes full-year
            self.assertTrue(any(c['scheduled_start_month'] == 11 and c['scheduled_end_month'] == 2 for c in cands))
            self.assertNotEqual((cands[0]['scheduled_start_month'], cands[0]['scheduled_end_month']), (1, 12))

            # Changed hardware changes month/hour winner
            s_small = dict(self.settings, battery_kwh=5.0)
            s_large = dict(self.settings, battery_kwh=15.0)
            c_small = sizing_schedule_candidates(s_small)
            c_large = sizing_schedule_candidates(s_large)
            self.assertEqual(c_small[0]['routing_switch_hour'], 10)
            self.assertEqual(c_large[0]['routing_switch_hour'], 14)

            # Supplied schedule always retained even max_candidates=1
            s_custom = dict(self.settings, routing_switch_hour=15, scheduled_start_month=4, scheduled_end_month=8)
            c_one = sizing_schedule_candidates(s_custom, max_candidates=1)
            self.assertTrue(any(c['routing_switch_hour'] == 15 and c['scheduled_start_month'] == 4 and
                                c['scheduled_end_month'] == 8 for c in c_one))

        # Incomplete month data falls back coarsely
        with patch('feasibility.schedule_optimizer._trace_monthly_summary',
                   return_value=(0, {'weather_monthly_clipping': [[{'grid_import_kwh': 1.0}] * 5] * 2})):
            c_fb = sizing_schedule_candidates(self.settings)
            self.assertGreaterEqual(len(c_fb), 7)
            fb_defaults = [c for c in c_fb if c['scheduled_start_month'] == 1 and c['scheduled_end_month'] == 12]
            self.assertEqual(set(c['routing_switch_hour'] for c in fb_defaults), set(range(9, 16)))


if __name__ == '__main__':
    unittest.main()
