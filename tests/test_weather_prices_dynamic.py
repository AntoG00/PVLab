"""Unit tests for dynamic weather row adaptation in empirical price caching."""
from feasibility.runtime_context import current_runtime
import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

import feasibility.engine as engine
from feasibility import runtime_context


class TestWeatherPricesDynamic(unittest.TestCase):
    def setUp(self):
        engine._price_cache.cache_clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / 'data'
        self.data_dir.mkdir(parents=True)
        shape = np.ones((12, 24), dtype=float)
        shape[1, :] = 1.25
        meta = {'windows': {
            'oct2025_sep2026': {'mean_csud_eur_kwh': 0.11},
            'calendar_2025': {'mean_csud_eur_kwh': 0.10}
        }}
        self.meta_json = json.dumps(meta)
        self.npz_path = self.data_dir / 'empirical_csud_prices.npz'
        self.pzo19 = np.full((19, 8784), 0.95, dtype=float)
        self._write_npz(self.pzo19, shape)

    def tearDown(self):
        self.tmp.cleanup()
        engine._price_cache.cache_clear()

    def _write_npz(self, proxy, shape):
        np.savez_compressed(
            self.npz_path,
            pzo_proxy_oct2025_sep2026=proxy,
            pzo_proxy_cal2025=proxy * 1.05,
            normalized_shape_oct2025_sep2026=shape,
            normalized_shape_cal2025=shape * 1.05,
            meta_json=self.meta_json,
        )
        # Production bootstrap verifies and retains artifacts once. These unit
        # fixtures replace only that input boundary, preserving price math.
        with np.load(self.npz_path, allow_pickle=False) as archive:
            prices = {key: archive[key] for key in archive.files}
        ctx = replace(current_runtime(), price_data={'empirical_csud_prices.npz': prices})
        patcher = patch.object(runtime_context, '_bound', ctx)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _mock_data(self, n_years):
        month = np.ones((n_years, 8784), dtype=np.int16)
        hour = np.tile(np.arange(24, dtype=np.int16), 366)[:8784]
        hour = np.tile(hour, (n_years, 1))
        valid = np.zeros((n_years, 8784), dtype=bool)
        valid[:, :8760] = True
        if n_years >= 20:
            valid[19, :8784] = True
            month[19, 24 * 59:24 * 60] = 2
        return {'month': month, 'hour': hour, 'valid': valid, 'years': list(range(2005, 2005 + n_years))}

    def test_baseline_19_exact_rows(self):
        with patch.object(engine, 'HERE', Path(self.tmp.name)), patch.object(engine, 'data', return_value=self._mock_data(19)):
            pzo, label, mean = engine._price_cache(current_runtime().key, 1, 'empirical_csud', 'oct2025_sep2026')
            self.assertEqual(label, 'empirical_csud_oct2025_sep2026_proxy')
            np.testing.assert_array_equal(pzo, self.pzo19 / np.mean(self.pzo19[:, :8760], axis=1, keepdims=True))

    def test_dynamic_21_rows_both_windows_and_feb29(self):
        with patch.object(engine, 'HERE', Path(self.tmp.name)), patch.object(engine, 'data', return_value=self._mock_data(21)):
            for win in ('oct2025_sep2026', 'calendar_2025'):
                pzo, label, mean = engine._price_cache(current_runtime().key, f'v_{win}', 'empirical_csud', win)
                self.assertEqual(pzo.shape, (21, 8784))
                self.assertIn(win[:7], label)
                self.assertEqual(len(mean), 21)
                self.assertAlmostEqual(float(pzo[19, 24 * 59]), float(pzo[19, 0] * 1.25), places=6)

    def test_flat_benchmark_dynamic_shape(self):
        with patch.object(engine, 'data', return_value=self._mock_data(21)):
            pzo, label, mean = engine._price_cache(current_runtime().key, 3, 'flat_benchmark')
            self.assertEqual(label, 'flat_solar_capture_benchmark')
            self.assertEqual(pzo.shape, (21, 8784))
            self.assertTrue(np.all(pzo == 1.0))

    def test_corrupt_shape_and_nan_rejections(self):
        with patch.object(engine, 'HERE', Path(self.tmp.name)), patch.object(engine, 'data', return_value=self._mock_data(21)):
            self._write_npz(self.pzo19, np.full((10, 24), 1.0))
            with self.assertRaises(ValueError):
                engine._price_cache(current_runtime().key, 4, 'empirical_csud')
            self._write_npz(self.pzo19, np.full((12, 24), np.nan))
            with self.assertRaises(ValueError):
                engine._price_cache(current_runtime().key, 5, 'empirical_csud')

    def test_signed_shape_normalizes_each_valid_year(self):
        shape = np.ones((12,24))
        shape[:,0] = -.1
        self._write_npz(self.pzo19, shape)
        with patch.object(engine, 'data', return_value=self._mock_data(21)):
            values, _, _ = engine._price_cache(current_runtime().key, 'signed', 'empirical_csud')
            valid = self._mock_data(21)['valid']
            self.assertTrue(np.any(values[valid] < 0))
            np.testing.assert_allclose((values*valid).sum(axis=1)/valid.sum(axis=1), 1)
            self.assertTrue(np.all(values[~valid] == 0))

    def test_same_sized_proxy_nan_fails_closed(self):
        nan_proxy = self.pzo19.copy()
        nan_proxy[0, 10] = np.nan
        self._write_npz(nan_proxy, np.ones((12, 24)))
        with patch.object(engine, 'HERE', Path(self.tmp.name)), patch.object(engine, 'data', return_value=self._mock_data(19)):
            with self.assertRaises(ValueError):
                engine._price_cache(current_runtime().key, 6, 'empirical_csud')


if __name__ == '__main__':
    unittest.main()
