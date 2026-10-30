"""Tests for dispatch context reuse and cache isolation in schedule sizing."""
import test_support  # Import test_support before engine
from unittest.mock import patch
import numpy as np
import feasibility.engine as e
import feasibility.schedule_optimizer as so

ml = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
M = np.repeat(np.repeat(np.arange(1, 13), ml), 24)
H = np.tile(np.arange(24), 365)
LOAD = [np.full(8760, 0.5) for _ in range(3)]
PZO = [np.full(8760, 0.1) for _ in range(3)]
for _a in [M, H] + LOAD + PZO:
    _a.flags.writeable = False

def _settings(dc=True, **kw):
    cfg = {'pv_dispatch_model': 'physical', 'pv_battery_coupling': 'dc_coupled' if dc else 'ac_equivalent',
           'battery_kwh': 5.0, 'routing_priority': 'scheduled_hybrid', 'south_panels': 10,
           'inverter_kw': 3.0, 'pv_first_year_loss': 0.02}
    cfg.update(kw)
    return e.validate(cfg)

def _raw_pv(s):
    sp = s.get('south_panels', 10)
    return [np.maximum(np.sin((H - 6) * np.pi / 12.0), 0.0) * (sp + w) for w in range(3)]

def _full_forecast(s):
    fade = 1.0 - float(s.get('pv_first_year_loss', 0.0) or 0.0)
    return {'target_len': 8760, 'forecast_year': 2027, 'is_leap': False,
            'target_months': M, 'target_std_hours': H,
            'mapped_pv': [r * fade for r in _raw_pv(s)],
            'mapped_load': LOAD, 'mapped_pzo': PZO,
            'mappings': [(np.arange(8760), np.arange(8760), np.ones(8760), np.zeros(8760)) for _ in range(3)]}

def _mock_trace(eng, s, prepared_context=None, energy_only=True, **kw):
    ri = 0 if prepared_context.get('ri') is None else prepared_context['ri']
    m_dicts = [dict(month=m + 1, pv_input_kwh=100.0, grid_import_kwh=50.0, grid_export_kwh=40.0,
                    export_price_weighted_kwh=35.0, clipped_kwh=5.0) for m in range(12)]
    weathers = [list(m_dicts) for _ in range(3)]
    return ri, {'weather_monthly_clipping': weathers, 'monthly_clipping': weathers[ri]}

def test_prepare_dispatch_context_reuse_lifecycle():
    calls = {'full': 0}
    def _tracked_forecast(s):
        calls['full'] += 1
        return _full_forecast(s)

    shared = {}
    layouts = [_settings(south_panels=10 + 2 * i, dc=(i % 2 == 0), battery_kwh=5.0 + 2.5 * (i % 2)) for i in range(6)]

    with patch.object(e, '_forecast_mapped_series', side_effect=_tracked_forecast),          patch.object(e, 'pv_profile', side_effect=_raw_pv),          patch.object(e, '_apply_mapping', side_effect=lambda src, m: src):

        contexts = [so.prepare_dispatch_context(e, s, base_ri=i % 3, shared_context=shared) for i, s in enumerate(layouts)]
        assert calls['full'] == 1
        assert 'template' in shared and 'mapped_pv' not in shared['template']['mapped_data']

        for i, s in enumerate(layouts):
            expected = [r * (1.0 - s['pv_first_year_loss']) for r in _raw_pv(s)]
            for w in range(3):
                np.testing.assert_array_equal(contexts[i]['mapped_data']['mapped_pv'][w], expected[w])

        tmpl_md = shared['template']['mapped_data']
        for ctx in contexts:
            assert ctx['mapped_data']['mapped_load'] is tmpl_md['mapped_load']
            assert ctx['target_months'] is shared['template']['target_months']

        assert contexts[0]['mapped_data']['mapped_pv'] is not contexts[1]['mapped_data']['mapped_pv']
        assert contexts[1]['ri'] == 1 and shared['template']['ri'] is None

        shared2 = {}
        so.prepare_dispatch_context(e, layouts[0], shared_context=shared2)
        assert calls['full'] == 2

        ctx_reused = contexts[0]
        ctx_direct = so.prepare_dispatch_context(e, layouts[0], base_ri=0)
        for w in range(3):
            np.testing.assert_array_equal(ctx_reused['mapped_data']['mapped_pv'][w], ctx_direct['mapped_data']['mapped_pv'][w])
            np.testing.assert_array_equal(ctx_reused['mapped_data']['mapped_load'][w], ctx_direct['mapped_data']['mapped_load'][w])
        np.testing.assert_array_equal(ctx_reused['target_months'], ctx_direct['target_months'])
        np.testing.assert_array_equal(ctx_reused['month_days'], ctx_direct['month_days'])

def test_sizing_candidates_cache_limits_and_fallback():
    calls = {'full': 0}
    def _tracked_forecast(s):
        calls['full'] += 1
        return _full_forecast(s)

    cache, shared = {}, {}
    with patch.object(e, '_forecast_mapped_series', side_effect=_tracked_forecast),          patch.object(e, 'pv_profile', side_effect=_raw_pv),          patch.object(e, '_apply_mapping', side_effect=lambda src, m: src),          patch.object(so, '_trace_monthly_summary', side_effect=_mock_trace):

        for p in range(6):
            res = so.sizing_schedule_candidates(_settings(south_panels=10 + p), context_cache=cache, shared_context=shared)
            assert len(res) > 0
        assert calls['full'] == 1 and len(cache) == 4

        shared2 = {}
        so.sizing_schedule_candidates(_settings(south_panels=10), context_cache={}, shared_context=shared2)
        assert calls['full'] == 2

        cache_fail, shared_fail = {}, {}
        with patch.object(e, '_forecast_mapped_series', side_effect=RuntimeError('init fail')):
            fb = so.sizing_schedule_candidates(_settings(), context_cache=cache_fail, shared_context=shared_fail)
            assert len(fb) > 0 and len(cache_fail) == 0 and len(shared_fail) == 0

        prior_keys = set(cache.keys())
        orig_load = shared['template']['mapped_data']['mapped_load'][0].copy()
        new_layout = _settings(south_panels=99)
        with patch.object(e, '_apply_mapping', side_effect=RuntimeError('remap fail')):
            fb2 = so.sizing_schedule_candidates(new_layout, context_cache=cache, shared_context=shared)
            assert len(fb2) > 0
            assert set(cache.keys()) == prior_keys
            np.testing.assert_array_equal(shared['template']['mapped_data']['mapped_load'][0], orig_load)

        res_after = so.sizing_schedule_candidates(new_layout, context_cache=cache, shared_context=shared)
        assert len(res_after) > 0
