import numpy as np
import pytest

from feasibility.ensemble_statistics import paired_improvement_statistics


def test_median_of_paired_differences_is_not_difference_of_medians():
    result = paired_improvement_statistics([0, 10, 100], [5, 100, 101])
    assert result['median'] == 5
    assert result['median'] != np.median([5, 100, 101])-np.median([0, 10, 100])
    assert result['p10'] == pytest.approx(1.8)
    assert result['p90'] == pytest.approx(73)


def test_downside_and_improving_year_count_are_preserved():
    result = paired_improvement_statistics([10, 20, 30], [11, 18, 25], lower_is_better=True)
    assert result == dict(mean=2., median=2., p10=pytest.approx(-.4), p90=pytest.approx(4.4),
                          improving_years=2, weather_count=3)


@pytest.mark.parametrize('baseline,candidate', [([], []), ([1], [1, 2]),
                                              ([np.nan], [1]), ([[1]], [[2]])])
def test_invalid_pairs_are_rejected(baseline, candidate):
    with pytest.raises(ValueError):
        paired_improvement_statistics(baseline, candidate)
