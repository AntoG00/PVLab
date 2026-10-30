"""Tests for request-local bounded physical-dispatch memoization in solar sizing sensitivity."""
import test_support  # Must import test_support first
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, get_ident
from collections import OrderedDict
import numpy as np

from feasibility import engine
from feasibility.engine import (
    _SIZING_DISPATCH_MEMO,
    _sizing_dispatch_lifetime,
    solar_sizing_sensitivity,
    validate,
)


class TestRobustnessDispatchMemo(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.call_count = 0
        self.base_s = validate({})

        def stub_dispatch(*args, **kwargs):
            self.call_count += 1
            return np.ones((2, 3, 10), dtype=float) * self.call_count

        self.stub_patch = patch.object(engine, 'dispatch_lifetime', side_effect=stub_dispatch)
        self.stub_patch.start()
        self.addCleanup(self.stub_patch.stop)

    def test_no_active_memo_bypass_returns_writable_array(self):
        self.assertIsNone(_SIZING_DISPATCH_MEMO.get())
        res = _sizing_dispatch_lifetime(self.base_s, 'dummy_arg')
        self.assertEqual(self.call_count, 1)
        self.assertTrue(res.flags.writeable)

    def test_finance_reuse_and_physical_key_misses(self):
        memo = {'entries': OrderedDict(), 'nbytes': 0}
        tok = _SIZING_DISPATCH_MEMO.set(memo)
        try:
            with patch.object(engine, 'price_fingerprint', return_value='fp1'):
                s1 = dict(self.base_s)
                res1 = _sizing_dispatch_lifetime(s1)
                self.assertEqual(self.call_count, 1)
                self.assertFalse(res1.flags.writeable)

                # Finance-only changes (loan_apr, pv_fixed_cost, pv_cost_kwp) reuse cached array
                s_fin = {**s1, 'loan_apr': 0.08, 'pv_fixed_cost': 5000.0, 'pv_cost_kwp': 1200.0}
                res_fin = _sizing_dispatch_lifetime(s_fin)
                self.assertEqual(self.call_count, 1)
                self.assertIs(res_fin, res1)

                # Physical, load, and schedule changes must force misses
                for k, v in [
                    ('load_growth', float(s1['load_growth']) + 0.01),
                    ('routing_switch_hour', (int(s1['routing_switch_hour']) + 1) % 24),
                    ('scheduled_start_month', (int(s1['scheduled_start_month']) % 12) + 1),
                    ('battery_kwh', float(s1['battery_kwh']) + 1.0),
                ]:
                    prev = self.call_count
                    s_mod = {**s1, k: v}
                    res_mod = _sizing_dispatch_lifetime(s_mod)
                    self.assertEqual(self.call_count, prev + 1, f"Failed miss for {k}")
                    self.assertFalse(res_mod.flags.writeable)

            # Price fingerprint change forces miss
            with patch.object(engine, 'price_fingerprint', return_value='fp2'):
                prev = self.call_count
                res_fp = _sizing_dispatch_lifetime(s1)
                self.assertEqual(self.call_count, prev + 1)
                self.assertFalse(res_fp.flags.writeable)
        finally:
            _SIZING_DISPATCH_MEMO.reset(tok)

    def test_lru_and_byte_caps_enforcement(self):
        memo = {'entries': OrderedDict(), 'nbytes': 0}
        tok = _SIZING_DISPATCH_MEMO.set(memo)
        try:
            arr_bytes = np.ones((2, 3, 10), dtype=float).nbytes
            # Entry cap: max 2 entries, byte budget 3 arrays
            with patch.object(engine, '_SIZING_DISPATCH_MAX_ENTRIES', 2), \
                 patch.object(engine, '_SIZING_DISPATCH_MAX_BYTES', arr_bytes * 3):
                s1, s2, s3 = ({**self.base_s, 'battery_kwh': float(i)} for i in (1, 2, 3))
                _sizing_dispatch_lifetime(s1); _sizing_dispatch_lifetime(s2)
                _sizing_dispatch_lifetime(s1)  # s1 MRU
                _sizing_dispatch_lifetime(s3)  # evicts s2
                self.assertEqual(len(memo['entries']), 2)
                self.assertNotIn((tuple((k, engine.freeze(s2[k])) for k in engine.ENERGY_KEYS), engine.price_fingerprint(s2)), memo['entries'])

            # Byte cap: budget 1 array, max 3 entries
            memo['entries'].clear(); memo['nbytes'] = 0
            with patch.object(engine, '_SIZING_DISPATCH_MAX_ENTRIES', 3), \
                 patch.object(engine, '_SIZING_DISPATCH_MAX_BYTES', arr_bytes):
                s4, s5 = ({**self.base_s, 'battery_kwh': float(i)} for i in (4, 5))
                _sizing_dispatch_lifetime(s4); _sizing_dispatch_lifetime(s5)
                self.assertEqual(len(memo['entries']), 1)
                self.assertLessEqual(memo['nbytes'], arr_bytes)

            # Oversized array bypasses cache without eviction
            with patch.object(engine, '_SIZING_DISPATCH_MAX_BYTES', arr_bytes * 3), \
                 patch.object(engine, 'dispatch_lifetime', return_value=np.ones((10, 10, 10), dtype=float)):
                before_keys = tuple(memo['entries'])
                before_bytes = memo['nbytes']
                r_big = _sizing_dispatch_lifetime({**self.base_s, 'battery_kwh': 99.0})
                self.assertTrue(r_big.flags.writeable)
                self.assertEqual(tuple(memo['entries']), before_keys)
                self.assertEqual(memo['nbytes'], before_bytes)
        finally:
            _SIZING_DISPATCH_MEMO.reset(tok)

    def test_memo_lifecycle_success_and_exception(self):
        self.assertIsNone(_SIZING_DISPATCH_MEMO.get())
        def check_cached(*args, **kwargs):
            self.assertIsNotNone(_SIZING_DISPATCH_MEMO.get())
            return {'status': 'OK'}
        with patch.object(engine, '_solar_sizing_sensitivity_cached', side_effect=check_cached):
            self.assertEqual(solar_sizing_sensitivity(settings=self.base_s), {'status': 'OK'})
        self.assertIsNone(_SIZING_DISPATCH_MEMO.get())

        def check_fail(*args, **kwargs):
            self.assertIsNotNone(_SIZING_DISPATCH_MEMO.get())
            raise RuntimeError('sim_fail')
        with patch.object(engine, '_solar_sizing_sensitivity_cached', side_effect=check_fail):
            with self.assertRaises(RuntimeError):
                solar_sizing_sensitivity(settings=self.base_s)
        self.assertIsNone(_SIZING_DISPATCH_MEMO.get())

    def test_concurrent_calls_have_isolated_context(self):
        barrier = Barrier(2)
        def shared_cached(*args, **kwargs):
            memo = _SIZING_DISPATCH_MEMO.get()
            self.assertIsNotNone(memo)
            barrier.wait(timeout=10)
            r1 = _sizing_dispatch_lifetime(self.base_s)
            r2 = _sizing_dispatch_lifetime(self.base_s)
            self.assertIs(r1, r2)
            return {'memo_id': id(memo), 'thread_val': float(r1[0])}

        with patch.object(engine, 'dispatch_lifetime', side_effect=lambda *a, **k: np.array([get_ident()], dtype=float)), \
             patch.object(engine, '_solar_sizing_sensitivity_cached', side_effect=shared_cached):
            with ThreadPoolExecutor(max_workers=2) as ex:
                future1 = ex.submit(solar_sizing_sensitivity, settings=self.base_s)
                future2 = ex.submit(solar_sizing_sensitivity, settings=self.base_s)
                res1 = future1.result(timeout=20)
                res2 = future2.result(timeout=20)

        self.assertNotEqual(res1['memo_id'], res2['memo_id'])
        self.assertNotEqual(res1['thread_val'], res2['thread_val'])
        self.assertIsNone(_SIZING_DISPATCH_MEMO.get())

if __name__ == '__main__':
    unittest.main()
