"""Daily battery charge statistics over equally weighted historical weather years."""

import numpy as np


def monthly_peak_soc_statistics(traces, month_days, capacity):
    """Pool daily maxima by month; percentiles describe historical spread, not CIs.

    Each complete weather year supplies the same number of days per month.
    The median therefore describes a typical day across equally weighted years.
    Daily maxima precede pooling, so differing charge times do not lower peaks.
    """
    traces, md = list(traces), np.asarray(month_days)
    if (not traces or md.ndim != 1 or not np.issubdtype(md.dtype, np.integer)
            or set(md.tolist()) != set(range(12))
            or any(getattr(t, 'shape', None) != (len(md)*24, 8)
                   or not np.all(np.isfinite(t[:, 4])) for t in traces)):
        raise ValueError('Expected finite SoC traces on a common complete calendar')
    peaks = np.array([t[:, 4].reshape(-1, 24).max(axis=1) for t in traces])
    valid_capacity = np.isfinite(capacity) and capacity > 0
    result = []
    for month in range(12):
        samples = peaks[:, md == month].ravel()
        p10, p25, median, p90 = np.percentile(samples, [10, 25, 50, 90])
        def pct(value):
            return float(100*value/capacity) if valid_capacity else None
        result.append(dict(
            median_peak_soc_kwh=float(median),
            median_peak_soc_pct=pct(median),
            p25_peak_soc_pct=pct(p25),
            peak_soc_p10_pct=pct(p10),
            peak_soc_p90_pct=pct(p90),
            peak_soc_days_ge95_pct=(float(100*np.mean(samples >= .95*capacity))
                                    if valid_capacity else None),
            peak_soc_weather_count=len(traces),
        ))
    return result
