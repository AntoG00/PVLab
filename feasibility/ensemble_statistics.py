"""Descriptive comparisons of schedules under matched historical weather years."""

import numpy as np


def paired_improvement_statistics(baseline, candidate, lower_is_better=False):
    """Summarize paired annual improvements; percentiles are historical spread.

    Inputs must already be annual totals, with matching weather-year order.
    These are descriptive statistics, not calibrated future probabilities or CIs.
    """
    baseline, candidate = np.asarray(baseline, dtype=float), np.asarray(candidate, dtype=float)
    if (baseline.ndim != 1 or not baseline.size or baseline.shape != candidate.shape
            or not np.all(np.isfinite(baseline)) or not np.all(np.isfinite(candidate))):
        raise ValueError('Expected finite annual totals paired by weather year')
    improvement = baseline-candidate if lower_is_better else candidate-baseline
    p10, median, p90 = np.percentile(improvement, [10, 50, 90])
    return dict(mean=float(np.mean(improvement)),
                median=float(median), p10=float(p10), p90=float(p90),
                improving_years=int(np.count_nonzero(improvement > 1e-9)),
                weather_count=int(improvement.size))
