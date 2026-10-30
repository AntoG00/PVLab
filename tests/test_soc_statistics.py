import unittest
import numpy as np
from feasibility.soc_statistics import monthly_peak_soc_statistics


class TestMonthlyPeakSocStatistics(unittest.TestCase):
    def setUp(self):
        self.capacity = 10.0
        self.month_days_12 = np.arange(12)  # 12 days = 288 hours

    def test_rare_lowcharge_days_reduce_mean_but_median_100(self):
        # 8 weather traces, each (288, 8) with SoC in col 4
        traces = [np.zeros((288, 8)) for _ in range(8)]
        for i in range(7):
            traces[i][:, 4] = 10.0  # 100% full
        traces[7][:, 4] = 10.0
        traces[7][0:24, 4] = 2.0  # Jan rare low-charge day: 2kWh (20%)
        stats = monthly_peak_soc_statistics(traces, self.month_days_12, self.capacity)
        self.assertEqual(len(stats), 12)
        jan = stats[0]
        self.assertEqual(jan['median_peak_soc_pct'], 100.0)
        self.assertAlmostEqual(np.mean([t[:24, 4].max() for t in traces])*10, 90.0)
        self.assertAlmostEqual(jan['peak_soc_p10_pct'], 76.0)
        self.assertEqual(jan['p25_peak_soc_pct'], 100.0)
        self.assertEqual(jan['peak_soc_p90_pct'], 100.0)
        self.assertEqual(jan['peak_soc_days_ge95_pct'], 87.5)
        self.assertEqual(jan['peak_soc_weather_count'], 8)

    def test_all_weather_participates_instead_of_chosen_analogue(self):
        # 3 weather traces: one low (0%), two high (100%)
        traces = [np.zeros((288, 8)) for _ in range(3)]
        traces[0][:, 4] = 0.0
        traces[1][:, 4] = 10.0
        traces[2][:, 4] = 10.0
        stats = monthly_peak_soc_statistics(traces, self.month_days_12, self.capacity)
        self.assertEqual(stats[0]['median_peak_soc_pct'], 100.0)
        self.assertEqual(stats[0]['p25_peak_soc_pct'], 50.0)
        self.assertEqual(stats[0]['peak_soc_weather_count'], 3)
        zero_stats = monthly_peak_soc_statistics(traces, self.month_days_12, 0)
        self.assertIsNone(zero_stats[0]['p25_peak_soc_pct'])
        self.assertIsNone(zero_stats[0]['median_peak_soc_pct'])

    def test_daily_max_computed_before_median_alternating_peaks(self):
        # 1 weather trace, 24 days = 576 hours (2 days per month)
        month_days = np.repeat(np.arange(12), 2)
        trace = np.zeros((576, 8))
        for m in range(12):
            d1_start = (2 * m) * 24
            d2_start = (2 * m + 1) * 24
            trace[d1_start + 8, 4] = 10.0   # Day 1 peak at hr 8 (100%)
            trace[d2_start + 16, 4] = 10.0  # Day 2 peak at hr 16 (100%)
        stats = monthly_peak_soc_statistics([trace], month_days, self.capacity)
        self.assertEqual(np.median(trace[:48, 4].reshape(2,24), axis=0).max()*10, 50.0)
        # Median of daily peaks is 100.0; median hourly profile max is 50.0
        self.assertEqual(stats[0]['median_peak_soc_pct'], 100.0)
        for key in ('peak_soc_p10_pct', 'p25_peak_soc_pct', 'peak_soc_p90_pct', 'peak_soc_days_ge95_pct', 'peak_soc_weather_count'):
            self.assertIn(key, stats[0])


