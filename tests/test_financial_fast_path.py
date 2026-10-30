"""Sizing-only financial paths must match the full report calculation exactly."""
import test_support
import numpy as np
import pytest
from feasibility import engine as e


def _make_annual(h):
    annual = np.zeros((h, 5, 14))
    weather = np.linspace(.8, 1.2, 5).reshape(1, 5)
    for field, value in [(0, 3000.), (1, 4000.), (2, 1500.), (5, 1500.), (6, 2500.), (11, 200.)]:
        annual[..., field] = value * weather
    return annual


@pytest.mark.parametrize('heat_pump', [False, True])
@pytest.mark.parametrize('h,loan_frac,loan_amt,inc_elec,scenario', [
    (20, .7, 0., False, 'baseline'),
    (20, 0., 5000., True, 'baseline'),
    (20, .8, 0., True, 'electrified'),
    (20, 0., 6000., False, 'electrified'),
    (3, .5, 0., True, 'baseline'),
    (3, 0., 4000., False, 'baseline'),
    (3, .6, 0., False, 'electrified'),
    (3, 0., 4500., True, 'electrified'),
])
def test_financial_fast_paths(h, loan_frac, loan_amt, inc_elec, scenario, heat_pump):
    settings = e.validate({
        'horizon_years': h, 'loan_mode': 'amount' if loan_amt > 0 else 'fraction',
        'loan_fraction': loan_frac, 'loan_amount': loan_amt,
        'loan_include_electrification': inc_elec, 'load_scenario': scenario,
        'planning_heat_pump': heat_pump,
    })
    annual = _make_annual(h)
    original = annual.copy()
    annual.flags.writeable = False
    full, _ = e._financial(settings, annual, 5.28, calculate_irr=False)
    ranking, _ = e._financial(settings, annual, 5.28, ranking_only=True, calculate_irr=False)
    candidate, _ = e._financial(settings, annual, 5.28, candidate_only=True, calculate_irr=False)
    np.testing.assert_array_equal(annual, original)
    for scope in ('pv_incremental', 'solar_equity'):
        assert ranking[scope]['npv'] == full[scope]['npv']
        for metric in ('npv', 'simple_payback', 'discounted_payback', 'npv_by_weather'):
            assert candidate[scope][metric] == full[scope][metric]
    for key, value in candidate.items():
        if key not in ('pv_incremental', 'solar_equity'):
            assert value == full[key]
