"""Regression tests for solar sizing ensemble means, ratio-of-means metrics, and coverage gates."""
import test_support  # Isolate live defaults.
import numpy as np
import pytest
from unittest.mock import patch
from feasibility import assumptions, engine as e


@pytest.fixture
def asymmetric_21_dispatch():
    w, h = 21, 20
    s_factors = np.array([0.90, 0.92, 0.95, 0.98, 1.00, 1.02, 1.05, 1.08, 1.10, 0.94,
                          0.96, 1.01, 1.03, 1.06, 0.99, 0.35, 0.45, 0.55, 1.70, 1.95, 2.30])
    l_factors = np.array([0.95, 0.98, 1.00, 1.02, 1.05, 0.97, 1.03, 1.01, 0.99, 1.04,
                          0.96, 1.00, 1.02, 0.98, 1.01, 0.70, 0.75, 0.80, 1.80, 2.10, 2.50])
    pv_in = 5000.0 * s_factors
    clipped = np.maximum(0.0, pv_in - 6500.0)
    pv_prod = pv_in - clipped
    loads = 4000.0 * l_factors
    directs = np.minimum(loads * 0.35, pv_prod * 0.40)
    charges = np.minimum(pv_prod * 0.25, (loads - directs) * 0.50)
    discharges = charges * 0.90
    imports = loads - directs - discharges
    exports = pv_prod - directs - charges

    annual = np.zeros((h, w, len(e.FIELDS)), dtype=float)
    for yy in range(h):
        g, f = (1.0 + 0.015) ** yy, (1.0 - 0.005) ** yy
        annual[yy, :, 0] = loads * g
        annual[yy, :, 1], annual[yy, :, 2], annual[yy, :, 3] = pv_prod * f, directs * f, charges * f
        annual[yy, :, 4], annual[yy, :, 5], annual[yy, :, 6] = discharges * f, imports * g, exports * f
        annual[yy, :, 7], annual[yy, :, 9], annual[yy, :, 11] = (charges - discharges) * f, clipped * f, exports * f
        annual[yy, :, 12] = 5.0

    return {
        'w': w, 'h': h, 'annual': annual, 'loads': loads, 'pv_in': pv_in, 'pv_prod': pv_prod,
        'directs': directs, 'charges': charges, 'imports': imports, 'clipped': clipped,
        'onsite': directs + charges,
    }


def _cand(panels=10, kwp=4.4, bat=5.0, capex=5000.0, npv_mean=3000.0, npv_med=2800.0,
          aut_mean=0.70, aut_med=0.65, sc_mean=0.60, sc_med=0.55,
          cl_mean=100.0, cl_med=0.0, cl_pct_mean=2.0, cl_pct_med=0.0,
          roofs=None, cov_frac=None, pos_npv=True):
    env = {'median': npv_med, 'mean': npv_mean, 'min': min(npv_mean, npv_med), 'max': max(npv_mean, npv_med)}
    c = {
        'panel_count': panels, 'kwp': kwp, 'battery_kwh': bat, 'battery_modules': int(bat // 5),
        'capex_eur': capex, 'solar_capex_eur': capex, 'financial_basis': 'project',
        'npv': env, 'npv_mean': npv_mean, 'npv_median': npv_med,
        'project_npv': env, 'project_npv_mean': npv_mean, 'project_npv_median': npv_med,
        'equity_npv': env, 'equity_npv_mean': npv_mean, 'equity_npv_median': npv_med,
        'autarky': {'median': aut_med, 'mean': aut_mean}, 'autarky_mean': aut_mean, 'autarky_median': aut_med,
        'self_consumption': {'median': sc_med, 'mean': sc_mean}, 'self_consumption_mean': sc_mean, 'self_consumption_median': sc_med,
        'annual_clipped_kwh_mean': cl_mean, 'annual_clipped_kwh_median': cl_med,
        'annual_clipped_pct_mean': cl_pct_mean, 'annual_clipped_pct_median': cl_pct_med,
        'roof_counts': roofs or {'south': panels, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
        'settings': {'south_panels': panels, 'battery_kwh': bat, 'pv_battery_coupling': 'ac_equivalent', 'routing_priority': 'battery_first'},
        'is_positive_npv': pos_npv, 'is_baseline': False,
    }
    if cov_frac is not None:
        c['coverage_fraction'] = cov_frac
    return c


def test_evaluate_solar_candidate_flow_metrics(asymmetric_21_dispatch):
    disp = asymmetric_21_dispatch
    w, h, annual = disp['w'], disp['h'], disp['annual']
    loads, imports, pv_in, onsite, pv_prod, clipped = (
        disp['loads'], disp['imports'], disp['pv_in'], disp['onsite'], disp['pv_prod'], disp['clipped']
    )
    counts = (10, 0, 0, 0, 0, 0)
    pv_arr = np.ones((w, 8760)) * (pv_in[:, None] / 8760.0)

    precomputed = {
        'load': np.zeros((w, 8760)), 'ev': np.zeros((w, 8760)), 'valid': np.ones((w, 8760), bool),
        'pzo': np.zeros((w, 8760)), 'years': list(range(2005, 2005 + w)),
        'zero_import_env': e.envelope(imports), 'zero_import_median': float(np.median(imports)),
        'sqrt_rte': 0.95, 'vat_mult': 1.0, 'prem_mult': 1.0,
        'month': np.ones(8760, int), 'hour': np.ones(8760, int),
        'pv_profile_cache': {counts: pv_arr},
    }
    s = assumptions.defaults()
    s.update({
        'pv_dispatch_model': 'physical', 'pv_battery_coupling': 'ac_equivalent',
        'battery_kwh': 5.0, 'inverter_kw': 6.0, 'horizon_years': h,
        'pv_first_year_loss': 0.0, 'routing_priority': 'battery_first',
    })
    fin_env = {'median': 3000.0, 'mean': 3200.0, 'min': 1000.0, 'max': 5000.0}
    mock_fin = {
        'capex_eur': 6000.0, 'solar_loan_principal_eur': 0.0, 'solar_loan_ratio': 1.0, 'loan_principal_eur': 0.0,
        'pv_incremental': {'npv': fin_env, 'npv_by_weather': [3200.0] * w, 'discounted_payback': {'median': 6.5},
                           'discounted_payback_of_mean_cash_flows': 6.5, 'simple_payback': {'median': 5.5},
                           'simple_payback_of_mean_cash_flows': 5.5},
        'solar_equity': {'npv': fin_env, 'npv_by_weather': [3200.0] * w, 'discounted_payback': {'median': 6.5},
                         'discounted_payback_of_mean_cash_flows': 6.5, 'simple_payback': {'median': 5.5},
                         'simple_payback_of_mean_cash_flows': 5.5},
    }

    with patch.object(e, '_sizing_dispatch_lifetime', return_value=annual),          patch.object(e, '_financial', return_value=(mock_fin, [])):
        cand = e._evaluate_solar_candidate(s, {'financial_basis': 'project', 'battery_mode': 'fixed'},
                                           counts, 5.0, precomputed=precomputed)

    exp_aut_mean = float(1.0 - np.mean(imports) / np.mean(loads))
    exp_sc_mean = float(np.mean(onsite) / np.mean(pv_prod))
    exp_cl_mean = float(np.mean(clipped))
    exp_cl_pct_mean = float(100.0 * np.mean(clipped) / np.mean(pv_in))

    np.testing.assert_allclose(cand['autarky_mean'], exp_aut_mean, rtol=1e-10)
    np.testing.assert_allclose(cand['self_consumption_mean'], exp_sc_mean, rtol=1e-10)
    np.testing.assert_allclose(cand['annual_clipped_kwh_mean'], exp_cl_mean, rtol=1e-10)
    np.testing.assert_allclose(cand['annual_clipped_pct_mean'], exp_cl_pct_mean, rtol=1e-10)

    # Legacy medians preserved
    np.testing.assert_allclose(cand['autarky_median'], float(np.median(1.0 - imports / loads)), rtol=1e-10)
    np.testing.assert_allclose(cand['self_consumption_median'], float(np.median(onsite / pv_prod)), rtol=1e-10)
    np.testing.assert_allclose(cand['annual_clipped_kwh_median'], float(np.median(clipped)), rtol=1e-10)
    np.testing.assert_allclose(cand['annual_clipped_pct_median'], float(np.median(100.0 * clipped / pv_in)), rtol=1e-10)

    # Invariants: mean != median != mean of ratios
    assert cand['autarky_mean'] != cand['autarky_median']
    assert cand['autarky_mean'] != pytest.approx(float(np.mean(1.0 - imports / loads)), abs=1e-3)
    assert cand['self_consumption_mean'] != cand['self_consumption_median']
    assert cand['annual_clipped_kwh_mean'] != cand['annual_clipped_kwh_median']
    assert cand['annual_clipped_pct_mean'] != cand['annual_clipped_pct_median']


def test_no_install_baseline_zeros():
    zero = e._compact_candidate(None, is_no_install_baseline=True)
    assert zero['is_no_install'] and zero['capex_eur'] == 0.0 and zero['npv_mean'] == 0.0
    assert zero['autarky_mean'] == 0.0 and zero['autarky_median'] == 0.0
    assert zero['self_consumption_mean'] == 0.0 and zero['coverage_pct'] == 0.0

    neg = _cand(panels=6, capex=4000.0, npv_mean=-200.0, npv_med=-100.0, pos_npv=False)
    recs, _ = e.select_solar_recommendations([neg])
    fin = recs['financial']
    assert fin['is_no_install'] and fin['panel_count'] == 0 and fin['capex_eur'] == 0.0
    assert fin['autarky_mean'] == 0.0 and fin['autarky_median'] == 0.0
    assert fin['self_consumption_mean'] == 0.0 and fin['annual_clipped_kwh_mean'] == 0.0
    assert recs['balanced'] is None and recs['budget'] is None and recs['smaller'] is None


def test_budget_coverage_gate_disagreement():
    # Pass on mean (72% >= 70%), fail on median (65% < 70%)
    c_cheap = _cand(panels=6, capex=4200.0, aut_mean=0.72, aut_med=0.65)
    c_exp = _cand(panels=12, capex=7500.0, aut_mean=0.75, aut_med=0.76)
    recs, _ = e.select_solar_recommendations([c_cheap, c_exp], options={'coverage_target_pct': 70.0})
    assert recs['budget']['panel_count'] == 6 and recs['budget']['autarky_mean'] == 0.72

    # Fail on mean (67% < 70%), pass on median (74% >= 70%) -> must reject
    c_fail = _cand(panels=5, capex=3800.0, aut_mean=0.67, aut_med=0.74)
    c_valid = _cand(panels=10, capex=6200.0, aut_mean=0.73, aut_med=0.71)
    recs2, _ = e.select_solar_recommendations([c_fail, c_valid], options={'coverage_target_pct': 70.0})
    assert recs2['budget']['panel_count'] == 10 and recs2['budget']['autarky_mean'] == 0.73


def test_balanced_choice_coverage_ranking_and_gain():
    c_fin = _cand(panels=16, capex=11000.0, npv_mean=10000.0, aut_mean=0.60)
    # Both retain >= 95% NPV; B1 has higher autarky_mean (0.83 > 0.77) but lower median (0.73 < 0.87)
    c_b1 = _cand(panels=12, capex=9000.0, npv_mean=9800.0, aut_mean=0.83, aut_med=0.73, roofs={'south': 12})
    c_b2 = _cand(panels=14, capex=9000.0, npv_mean=9700.0, aut_mean=0.77, aut_med=0.87, roofs={'south': 14})
    recs, _ = e.select_solar_recommendations([c_fin, c_b1, c_b2], options={'npv_sacrifice_pct': 5.0})

    bal = recs['balanced']
    assert bal['panel_count'] == 12 and bal['autarky_mean'] == 0.83
    assert bal['retained_npv_pct'] == round((9800.0 / 10000.0) * 100.0, 2)
    assert bal['coverage_gain_pct'] == round((0.83 - 0.60) * 100.0, 2)


def test_financial_and_smaller_npv_mean_ranking():
    # Financial winner selects higher npv_mean (5600 > 5200) despite lower median (4900 < 5800)
    c_f1 = _cand(panels=10, npv_mean=5600.0, npv_med=4900.0)
    c_f2 = _cand(panels=12, npv_mean=5200.0, npv_med=5800.0)
    recs, _ = e.select_solar_recommendations([c_f1, c_f2])
    assert recs['financial']['panel_count'] == 10 and recs['financial']['npv_mean'] == 5600.0

    # Smaller array evaluates retained profit against npv_mean (95% of 10000 = 9500)
    c_fin = _cand(panels=16, capex=12000.0, npv_mean=10000.0)
    c_s1 = _cand(panels=10, capex=7000.0, npv_mean=9600.0, npv_med=9200.0)
    c_s2 = _cand(panels=12, capex=9000.0, npv_mean=9800.0, npv_med=9600.0)
    recs_s, _ = e.select_solar_recommendations([c_fin, c_s1, c_s2], options={'retained_profit_pct': 95.0})
    assert recs_s['smaller']['panel_count'] == 10 and recs_s['smaller']['capex_eur'] == 7000.0


def test_compact_candidate_forwards_means_over_existing_coverage_fraction():
    # cand has autarky_mean=0.72, autarky_median=0.65, and legacy cov_frac=0.65
    cand = _cand(panels=10, npv_mean=4500.0, npv_med=4100.0, aut_mean=0.72, aut_med=0.65,
                 sc_mean=0.74, sc_med=0.79, cov_frac=0.65)
    compact = e._compact_candidate(cand)

    assert compact['npv_mean'] == 4500.0 and compact['npv_median'] == 4100.0
    assert compact['autarky_mean'] == 0.72 and compact['autarky_median'] == 0.65
    assert compact['self_consumption_mean'] == 0.74 and compact['self_consumption_median'] == 0.79
    # Forwarding mean wins over legacy existing coverage_fraction (0.65)
    assert compact['coverage_fraction'] == 0.72
    assert compact['coverage_fraction'] != 0.65
    assert compact['coverage_pct'] == round(0.72 * 100.0, 1)


def test_top_alternatives_forward_means():
    c1 = _cand(panels=10, capex=5000.0, npv_mean=3000.0, aut_mean=0.72, aut_med=0.65)
    c2 = _cand(panels=14, capex=7000.0, npv_mean=3200.0, aut_mean=0.81, aut_med=0.75, roofs={'south': 14})
    _, alts = e.select_solar_recommendations([c1, c2])
    assert len(alts) >= 1
    for alt in alts:
        assert 'autarky_mean' in alt and 'autarky_median' in alt
        assert 'self_consumption_mean' in alt and 'annual_clipped_kwh_mean' in alt
