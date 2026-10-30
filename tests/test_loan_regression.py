"""Regression tests for loan financing, upfront fees, TAEG, and levered NPV parity."""
import numpy as np
import pytest
import test_support
from feasibility import assumptions, engine as e


@pytest.fixture
def make_annual():
    def _make(h=10, w=5):
        annual = np.zeros((h, w, 14))
        weather = np.linspace(0.8, 1.2, w).reshape(1, w)
        for field, val in [
            (0, 3000.0), (1, 4000.0), (2, 1500.0),
            (5, 1500.0), (6, 2500.0), (11, 200.0),
        ]:
            annual[..., field] = val * weather
        return annual
    return _make


@pytest.fixture
def loan_settings():
    def _builder(**overrides):
        s = assumptions.defaults()
        s.update({
            "horizon_years": 10,
            "pv_cost_kwp": 1000.0,
            "pv_fixed_cost": 0.0,
            "battery_kwh": 0.0,
            "load_scenario": "baseline",
            "loan_mode": "amount",
            "loan_amount": 14999.72,
            "loan_fraction": 0.0,
            "loan_upfront_fees_or_tax": 16.0,
            "loan_apr": 0.051,
            "loan_years": 10,
            "loan_include_electrification": False,
            "real_discount_rate": 0.03,
            "inflation": 0.02,
            "planning_heat_pump": False,
        })
        s.update(overrides)
        return e.validate(s)
    return _builder


def test_baseline_loan_metadata_and_reconciliation(loan_settings, make_annual):
    """Baseline loan matches amortized principal 15015.72, monthly 160, total 19200, TAEG 5.2452094%."""
    s = loan_settings()
    annual = make_annual(10)
    fin, cash = e._financial(s, annual, kwp=15.0, calculate_irr=False)

    assert fin["loan_amortized_principal_eur"] == pytest.approx(15015.72, abs=0.01)
    assert fin["loan_upfront_fees_or_tax_eur"] == pytest.approx(16.0, abs=0.01)
    assert fin["loan_monthly_payment_eur"] == pytest.approx(160.0, abs=0.01)
    assert fin["loan_total_repaid_eur"] == pytest.approx(19200.0, abs=0.03)
    assert fin["loan_total_interest_eur"] == pytest.approx(4184.28, abs=0.03)
    assert fin["loan_implied_taeg"] == pytest.approx(0.052452094, rel=1e-5)

    debt, balances = e.loan_schedule(15015.72, 0.051, 10, 10)
    assert debt[0] == pytest.approx(1919.9997, abs=1e-3)
    assert balances[-1] == pytest.approx(0.0, abs=1e-5)
    prev = 15015.72
    for y in range(10):
        p = prev - balances[y]
        i = debt[y] - p
        assert p + i == pytest.approx(debt[y], abs=1e-4)
        prev = balances[y]

    # Annual rows contract: debt_service_year_end_equivalent present; year 0 is 0
    rnom = (1.0 + s["real_discount_rate"]) * (1.0 + s["inflation"]) - 1.0
    expected_eq_debt = _independent_debt_year_end_equivalent(15015.72, 0.051, 10, 10, rnom)
    assert cash[0].get("debt_service_year_end_equivalent", 0.0) == 0.0
    actual_eq_debt = [row["debt_service_year_end_equivalent"] for row in cash[1:]]
    np.testing.assert_allclose(actual_eq_debt, expected_eq_debt, rtol=1e-5, atol=1e-4)

    # Nominal rows and loan_monthly_payment still actual: no double correction
    actual_nominal_debt = [row["debt_service"] for row in cash[1:]]
    np.testing.assert_allclose(actual_nominal_debt, debt, rtol=1e-5, atol=1e-4)
    assert fin["loan_monthly_payment_eur"] == pytest.approx(160.0, abs=0.01)
    assert actual_eq_debt[0] > actual_nominal_debt[0]

    # Cashflows for discounting have same scope keys as genuine nominal cashflows
    assert "cashflows_for_discounting_by_weather" in fin
    assert set(fin["cashflows_for_discounting_by_weather"].keys()) == set(fin["cashflows_by_weather"].keys())


def test_year0_net_proceeds_and_scope_fee_allocation(loan_settings, make_annual):
    """Net credit capped near capex; fee financed into debt balance with ratio allocation."""
    kwp = 15.0  # Capex = 15000.0, borrowing 14999.72 is capped near capex
    s = loan_settings(loan_amount=14999.72, loan_upfront_fees_or_tax=16.0)
    fin, _ = e._financial(s, make_annual(10), kwp=kwp, calculate_irr=False)

    levered_t0 = fin["cashflows_by_weather"]["solar_equity"][0][0]
    unlevered_t0 = fin["cashflows_by_weather"]["pv_incremental"][0][0]
    net_proceeds = levered_t0 - unlevered_t0
    assert net_proceeds == pytest.approx(14999.72, abs=0.01)
    assert levered_t0 == pytest.approx(-15000.0 + 14999.72, abs=0.01)
    assert fin.get("solar_loan_ratio", 1.0) == pytest.approx(1.0, abs=1e-6)


def test_real_interest_deflation(loan_settings, make_annual):
    """Deflated interest uses inflation exponent yy + 1 for each operating year."""
    infl = 0.02
    fin, _ = e._financial(loan_settings(inflation=infl), make_annual(10), kwp=15.0, calculate_irr=False)
    debt, balances = e.loan_schedule(15015.72, 0.051, 10, 10)

    prev = 15015.72
    expected_real = 0.0
    for yy in range(10):
        p = prev - balances[yy]
        i = debt[yy] - p
        expected_real += i / ((1.0 + infl) ** (yy + 1))
        prev = balances[yy]

    assert fin["loan_real_interest_eur"] == pytest.approx(expected_real, abs=0.05)
    assert fin["loan_real_interest_eur"] < fin["loan_total_interest_eur"]


@pytest.mark.parametrize("amt,fee,apr,exp_amort,exp_fee,exp_taeg_pos,exp_null_taeg", [
    (0.0, 16.0, 0.051, 0.0, 0.0, False, True),
    (10000.0, 16.0, 0.0, 10016.0, 16.0, True, False),
    (50000.0, 16.0, 0.051, 10016.0, 16.0, True, False),  # Capped at capex 10000 + 16 fee
])
def test_loan_edge_cases_parameterized(loan_settings, make_annual, amt, fee, apr, exp_amort, exp_fee, exp_taeg_pos, exp_null_taeg):
    """Parametric validation: zero loan fee exemption, zero TAN fee TAEG, and capex capping."""
    s = loan_settings(loan_amount=amt, loan_upfront_fees_or_tax=fee, loan_apr=apr)
    fin, _ = e._financial(s, make_annual(10), kwp=10.0, calculate_irr=False)

    assert fin["loan_amortized_principal_eur"] == pytest.approx(exp_amort, abs=0.01)
    assert fin["loan_upfront_fees_or_tax_eur"] == pytest.approx(exp_fee, abs=0.01)
    if exp_null_taeg:
        assert fin["loan_implied_taeg"] is None
        assert fin["loan_monthly_payment_eur"] == 0.0
    elif exp_taeg_pos:
        assert fin["loan_implied_taeg"] is not None and fin["loan_implied_taeg"] > 0.0


def test_negative_discount_setting_validation(loan_settings):
    """Negative real discount rate is rejected by engine validation bounds [0, 0.20]."""
    with pytest.raises(ValueError, match="outside modeling bounds"):
        loan_settings(real_discount_rate=-0.01)


def test_horizon_balloon_settlement():
    """Shorter horizon than loan maturity triggers final balloon settlement balance."""
    debt, balances = e.loan_schedule(15015.72, 0.051, years=10, horizon=5)
    assert len(debt) == 5 and len(balances) == 5
    assert balances[-1] == pytest.approx(0.0, abs=1e-5)
    reg_debt, _ = e.loan_schedule(15015.72, 0.051, years=10, horizon=10)
    assert debt[-1] > reg_debt[-1]


def _independent_debt_year_end_equivalent(amortized_principal, apr, years, horizon, rnom):
    """Independently sum monthly amortization by year with factor (1+rnom)**((12-mm)/12);
    balloon at horizon year-end unadjusted."""
    if amortized_principal <= 0.0 or years <= 0 or horizon <= 0:
        return np.zeros(horizon)
    n = int(years * 12)
    r_loan = apr / 12.0
    pmt = (
        amortized_principal / n
        if r_loan == 0.0
        else amortized_principal * r_loan / (1.0 - (1.0 + r_loan) ** (-n))
    )
    balance = float(amortized_principal)
    eq_debt = np.zeros(horizon)
    for y in range(horizon):
        for mm in range(1, 13):
            m = y * 12 + mm
            if m <= n:
                interest = balance * r_loan
                p = min(pmt, balance + interest)
                balance = max(0.0, balance + interest - p)
                factor = 1.0 if rnom == 0.0 else (1.0 + rnom) ** ((12 - mm) / 12.0)
                eq_debt[y] += p * factor
    if horizon < years and balance > 0.0:
        eq_debt[-1] += balance
    return eq_debt


def _monthly_discounted_debt_oracle(amortized_principal, net_principal, apr, years, horizon, rnom):
    """Independent explicit month-by-month discounted debt service oracle.

    Discounts month 1..N debt service at effective nominal monthly rate (1 + rnom)**(1/12) - 1.
    If horizon < years, the unamortized principal balance at the end of the horizon is treated
    as a terminal balloon payment discounted at the terminal year (1 + rnom)**(-horizon).
    """
    if amortized_principal <= 0.0 or years <= 0 or horizon <= 0:
        return 0.0, 0.0
    n = int(years * 12)
    h_months = int(horizon * 12)
    r_loan = apr / 12.0
    pmt = amortized_principal / n if r_loan == 0.0 else amortized_principal * r_loan / (1.0 - (1.0 + r_loan) ** (-n))
    r_disc = 0.0 if rnom == 0.0 else (1.0 + rnom) ** (1.0 / 12.0) - 1.0

    balance = amortized_principal
    pv = 0.0
    for m in range(1, h_months + 1):
        if m <= n:
            interest = balance * r_loan
            p = min(pmt, balance + interest)
            balance = max(0.0, balance + interest - p)
            disc = 1.0 if r_disc == 0.0 else (1.0 + r_disc) ** (-m)
            pv += p * disc
    if horizon < years and balance > 0.0:
        disc_term = 1.0 if rnom == 0.0 else (1.0 + rnom) ** (-horizon)
        pv += balance * disc_term
        balance = 0.0

    delta = net_principal - pv
    return pv, delta


@pytest.mark.parametrize("case,amt,fee,apr,l_years,h_years,r_disc,infl,kwp,exp_oracle_range", [
    ("baseline", 14999.72, 16.0, 0.051, 10, 10, 0.03, 0.02, 15.0, (-135.0, -122.0)),
    ("zero_discount", 14999.72, 16.0, 0.051, 10, 10, 0.0, 0.0, 15.0, (-4205.0, -4195.0)),
    ("no_loan", 0.0, 16.0, 0.051, 10, 10, 0.03, 0.02, 15.0, (0.0, 0.0)),
    ("horizon_shorter_balloon", 14999.72, 16.0, 0.051, 10, 5, 0.03, 0.02, 15.0, (-105.0, -90.0)),
    ("horizon_longer_loan", 14999.72, 16.0, 0.051, 5, 10, 0.03, 0.02, 15.0, None),
    ("zero_apr", 14999.72, 16.0, 0.0, 10, 10, 0.03, 0.02, 15.0, None),
])
def test_levered_npv_delta_monthly_oracle_and_parity(
    loan_settings, make_annual, case, amt, fee, apr, l_years, h_years, r_disc, infl, kwp, exp_oracle_range
):
    """Independent explicit month 1..N discounted debt oracle, zero discount parity, and new discounting paths."""
    rnom = (1.0 + r_disc) * (1.0 + infl) - 1.0
    s = loan_settings(
        loan_amount=amt,
        loan_upfront_fees_or_tax=fee,
        loan_apr=apr,
        loan_years=l_years,
        real_discount_rate=r_disc,
        inflation=infl,
        horizon_years=h_years,
    )
    fin, cash = e._financial(s, make_annual(h_years), kwp=kwp, calculate_irr=True)
    rank, _ = e._financial(s, make_annual(h_years), kwp=kwp, calculate_irr=False, ranking_only=True)
    cand, _ = e._financial(s, make_annual(h_years), kwp=kwp, calculate_irr=False, candidate_only=True)

    amortized = fin["loan_amortized_principal_eur"]
    net_principal = fin["loan_principal_eur"]
    pv_oracle, oracle_delta = _monthly_discounted_debt_oracle(
        amortized, net_principal, s["loan_apr"], l_years, h_years, rnom
    )

    # Validate exact calculated oracle delta and bounded range if specified
    if exp_oracle_range is not None:
        assert exp_oracle_range[0] <= oracle_delta <= exp_oracle_range[1]
    assert oracle_delta == pytest.approx(net_principal - pv_oracle, abs=1e-6)

    # Parity with actual financial engine delta across all weather scenarios
    actual_median_delta = fin["solar_equity"]["npv"]["median"] - fin["pv_incremental"]["npv"]["median"]
    assert actual_median_delta == pytest.approx(oracle_delta, abs=0.05)
    weather_deltas = np.array(fin["solar_equity"]["npv_by_weather"]) - np.array(fin["pv_incremental"]["npv_by_weather"])
    np.testing.assert_allclose(weather_deltas, oracle_delta, atol=0.05)

    if case == "baseline":
        # Explicit month 1..120 discounted debt oracle at nominal rate (1.0506)**(1/12) - 1
        assert oracle_delta == pytest.approx(-122.6834, abs=1e-3)
        assert actual_median_delta == pytest.approx(-122.68, abs=0.05)
    elif case == "zero_discount":
        # At zero discount rate, timing vanishes: delta equals exact undiscounted total repayment wedge
        assert actual_median_delta == pytest.approx(net_principal - fin["loan_total_repaid_eur"], abs=0.05)
        assert actual_median_delta == pytest.approx(-4200.277, abs=0.05)
    elif case == "no_loan":
        assert oracle_delta == 0.0
        assert actual_median_delta == pytest.approx(0.0, abs=1e-6)
    elif case == "horizon_shorter_balloon":
        # Shorter horizon than term: remaining balance discounted only at terminal year
        assert oracle_delta == pytest.approx(-97.9863, abs=1e-3)

    # Scope keys parity between discounting paths and genuine nominal flows
    cf_disc = fin["cashflows_for_discounting_by_weather"]
    cf_nom = fin["cashflows_by_weather"]
    assert set(cf_disc.keys()) == set(cf_nom.keys())

    # Annual rows: debt_service_year_end_equivalent matches independent formula; year 0 is 0
    expected_eq_debt = _independent_debt_year_end_equivalent(amortized, apr, l_years, h_years, rnom)
    assert cash[0].get("debt_service_year_end_equivalent", 0.0) == 0.0
    actual_eq_debt = [row["debt_service_year_end_equivalent"] for row in cash[1:]]
    np.testing.assert_allclose(actual_eq_debt, expected_eq_debt, rtol=1e-5, atol=1e-4)

    # Nominal rows and loan_monthly_payment still actual: no double correction
    actual_nominal_debt = [row["debt_service"] for row in cash[1:]]
    nom_debt_sched, _ = e.loan_schedule(amortized, apr, l_years, h_years)
    np.testing.assert_allclose(actual_nominal_debt, nom_debt_sched, rtol=1e-5, atol=1e-4)
    if amt > 0:
        n_contract_months = l_years * 12
        r_m = apr / 12.0
        exp_pmt = (
            amortized / n_contract_months
            if r_m == 0
            else amortized * r_m / (1.0 - (1.0 + r_m) ** (-n_contract_months))
        )
        assert fin["loan_monthly_payment_eur"] == pytest.approx(exp_pmt, abs=0.01)

    # Assert npv(new paths) == all NPV aliases / by-weather / full / ranking / candidate
    w_count = len(cf_disc["solar_equity"])
    for scope in ("pv_incremental", "solar_equity"):
        disc_paths = cf_disc[scope]
        path_npvs = [e.npv(disc_paths[w], rnom) for w in range(w_count)]
        np.testing.assert_allclose(fin[scope]["npv_by_weather"], path_npvs, rtol=1e-5, atol=1e-4)
        assert fin[scope]["npv"]["median"] == pytest.approx(float(np.median(path_npvs)), abs=1e-4)
        assert fin[scope]["npv_eur"]["median"] == pytest.approx(fin[scope]["npv"]["median"], abs=1e-4)

        # Fast path parity
        assert rank[scope]["npv"] == pytest.approx(fin[scope]["npv"])
        assert cand[scope]["npv"] == pytest.approx(fin[scope]["npv"])
        np.testing.assert_allclose(rank[scope]["npv_by_weather"], fin[scope]["npv_by_weather"], rtol=1e-5, atol=1e-4)

    # Discounted payback computes from new discounting paths; compare full envelope (including censored quantiles)
    for scope in ("pv_incremental", "solar_equity"):
        expected_disc_pb = [e.persistent_payback(cf_disc[scope][w], rnom) for w in range(w_count)]
        expected_disc_env = e.payback_envelope(expected_disc_pb)
        for k in ("min", "median", "mean", "max", "p10", "p90", "recovered_weather_years", "unrecovered_weather_years"):
            exp_val = expected_disc_env[k]
            act_val = fin[scope]["discounted_payback"][k]
            if exp_val is None:
                assert act_val is None
            else:
                assert act_val == pytest.approx(exp_val, abs=1e-4)

    # Original nominal flows unchanged for simple payback and IRR; compare full envelope
    for scope in ("pv_incremental", "solar_equity"):
        expected_simple_pb = [e.persistent_payback(cf_nom[scope][w], 0.0) for w in range(w_count)]
        expected_simple_env = e.payback_envelope(expected_simple_pb)
        for k in ("min", "median", "mean", "max", "p10", "p90", "recovered_weather_years", "unrecovered_weather_years"):
            exp_val = expected_simple_env[k]
            act_val = fin[scope]["simple_payback"][k]
            if exp_val is None:
                assert act_val is None
            else:
                assert act_val == pytest.approx(exp_val, abs=1e-4)
        if "irr_roots_by_weather" in fin[scope]:
            for w in range(w_count):
                exp_roots = e.irr_roots(cf_nom[scope][w])
                np.testing.assert_allclose(fin[scope]["irr_roots_by_weather"][w], exp_roots, rtol=1e-5, atol=1e-4)


def test_levered_npv_scope_solar_vs_whole_package(loan_settings, make_annual):
    """Scope allocation: whole package equity delta scales by solar_loan_ratio for solar equity delta."""
    rnom = (1.0 + 0.03) * (1.0 + 0.02) - 1.0
    s = loan_settings(
        load_scenario="electrified",
        planning_heat_pump=True,
        hp_capex=5000.0,
        loan_amount=15000.0,
        loan_include_electrification=True,
        real_discount_rate=0.03,
        inflation=0.02,
    )
    annual = make_annual(10)
    kwp = 10.0  # Capex = 10000; conversion = 5000; total = 15000; solar_loan_ratio = 10000 / 15000 = 2/3
    fin, cash = e._financial(s, annual, kwp=kwp, calculate_irr=False)
    cand, _ = e._financial(s, annual, kwp=kwp, calculate_irr=False, candidate_only=True)
    rank, _ = e._financial(s, annual, kwp=kwp, calculate_irr=False, ranking_only=True)

    ratio = fin["solar_loan_ratio"]
    assert ratio == pytest.approx(2.0 / 3.0, abs=1e-5)

    pkg_delta = fin["package_equity"]["npv"]["median"] - fin["whole_package"]["npv"]["median"]
    solar_delta = fin["solar_equity"]["npv"]["median"] - fin["pv_incremental"]["npv"]["median"]
    assert solar_delta == pytest.approx(pkg_delta * ratio, abs=0.01)

    # Monthly debt oracle evaluation on package scope vs solar scope
    pv_m_oracle, pkg_oracle_delta = _monthly_discounted_debt_oracle(
        fin["loan_amortized_principal_eur"], fin["loan_principal_eur"], s["loan_apr"], s["loan_years"], 10, rnom
    )
    assert pkg_delta == pytest.approx(pkg_oracle_delta, abs=0.05)
    solar_oracle_delta = pkg_oracle_delta * ratio
    assert solar_delta == pytest.approx(solar_oracle_delta, abs=0.05)

    # Cashflows for discounting contract: scope keys, equity flows year-end equivalents, and npv parity
    cf_disc = fin["cashflows_for_discounting_by_weather"]
    cf_nom = fin["cashflows_by_weather"]
    assert set(cf_disc.keys()) == set(cf_nom.keys())

    eq_debt = _independent_debt_year_end_equivalent(
        fin["loan_amortized_principal_eur"], s["loan_apr"], s["loan_years"], 10, rnom
    )
    w_count = len(cf_disc["package_equity"])
    for w in range(w_count):
        # Package equity discounting path uses full debt_service_year_end_equivalent
        np.testing.assert_allclose(
            np.array(cf_disc["package_equity"][w][1:]) - np.array(cf_nom["whole_package"][w][1:]),
            -eq_debt,
            rtol=1e-5, atol=1e-4,
        )
        # Solar equity discounting path scales by solar_loan_ratio
        np.testing.assert_allclose(
            np.array(cf_disc["solar_equity"][w][1:]) - np.array(cf_nom["pv_incremental"][w][1:]),
            -eq_debt * ratio,
            rtol=1e-5, atol=1e-4,
        )
        assert e.npv(cf_disc["package_equity"][w], rnom) == pytest.approx(fin["package_equity"]["npv_by_weather"][w], abs=1e-4)
        assert e.npv(cf_disc["solar_equity"][w], rnom) == pytest.approx(fin["solar_equity"]["npv_by_weather"][w], abs=1e-4)

    # Fast path parity preserves levered solar equity delta
    for fast in (cand, rank):
        assert fast["solar_equity"]["npv"] == pytest.approx(fin["solar_equity"]["npv"])
        fast_solar_delta = fast["solar_equity"]["npv"]["median"] - fast["pv_incremental"]["npv"]["median"]
        assert fast_solar_delta == pytest.approx(solar_delta, abs=1e-5)


def test_fast_path_parity(loan_settings, make_annual):
    """Ranking and candidate fast paths preserve NPV envelopes and loan metadata parity."""
    s = loan_settings()
    annual = make_annual(10)
    full, _ = e._financial(s, annual, kwp=15.0, calculate_irr=False)
    ranking, _ = e._financial(s, annual, kwp=15.0, calculate_irr=False, ranking_only=True)
    candidate, _ = e._financial(s, annual, kwp=15.0, calculate_irr=False, candidate_only=True)

    for scope in ("pv_incremental", "solar_equity"):
        assert ranking[scope]["npv"] == pytest.approx(full[scope]["npv"])
        assert candidate[scope]["npv"] == pytest.approx(full[scope]["npv"])
        np.testing.assert_allclose(ranking[scope]["npv_by_weather"], full[scope]["npv_by_weather"], rtol=1e-5, atol=1e-4)
        assert ranking[scope]["npv_eur"] == pytest.approx(full[scope]["npv_eur"])
        assert candidate[scope]["npv_eur"] == pytest.approx(full[scope]["npv_eur"])
    for k in ("loan_amortized_principal_eur", "loan_upfront_fees_or_tax_eur", "loan_monthly_payment_eur", "loan_implied_taeg"):
        if k in candidate:
            assert candidate[k] == full[k]


def test_solver_monthly_affordability_ceiling_reduced_by_fee(loan_settings, make_annual, monkeypatch):
    """Upfront fee of 16 reduces affordable net credit ceiling by 16 while monthly budget remains satisfied."""
    annual = make_annual(10, 1)
    capex = 10000.0
    equity_flow = np.zeros((1, 11))
    equity_flow[0, 0] = -capex
    equity_flow[0, 1:] = 1500.0
    base_fin = {"capex_eur": capex, "conversion_capex_eur": 0.0, "cashflows_by_weather": {"equity": equity_flow}}

    monkeypatch.setattr(e, "_energy_cache", lambda *args, **kwargs: (annual, None))
    monkeypatch.setattr(e, "_financial", lambda *args, **kwargs: (base_fin, []))

    max_payment = 100.0
    s_no_fee = loan_settings(loan_max_monthly_payment=max_payment, loan_upfront_fees_or_tax=0.0)
    s_fee = loan_settings(loan_max_monthly_payment=max_payment, loan_upfront_fees_or_tax=16.0)

    res_no_fee = e.solve_loan_amount(s_no_fee)
    res_fee = e.solve_loan_amount(s_fee)

    assert res_fee["affordable_search_cap_eur"] == pytest.approx(res_no_fee["affordable_search_cap_eur"] - 16.0, abs=0.01)
    assert res_fee["optimal_monthly_payment_eur"] <= max_payment + 1e-6
    assert res_no_fee["optimal_monthly_payment_eur"] <= max_payment + 1e-6


def test_solver_budget_below_fee_only_payment_yields_zero_loan(loan_settings, make_annual, monkeypatch):
    """Monthly payment budget smaller than fee-only instalment yields zero loan and no fee."""
    annual = make_annual(10, 1)
    capex = 10000.0
    equity_flow = np.zeros((1, 11))
    equity_flow[0, 0] = -capex
    equity_flow[0, 1:] = 1500.0
    base_fin = {"capex_eur": capex, "conversion_capex_eur": 0.0, "cashflows_by_weather": {"equity": equity_flow}}

    monkeypatch.setattr(e, "_energy_cache", lambda *args, **kwargs: (annual, None))
    monkeypatch.setattr(e, "_financial", lambda *args, **kwargs: (base_fin, []))

    s_sub_fee = loan_settings(loan_max_monthly_payment=0.10, loan_upfront_fees_or_tax=16.0)
    res = e.solve_loan_amount(s_sub_fee)

    assert res["affordable_search_cap_eur"] == 0.0
    assert res["optimal_amount_eur"] == 0.0


def test_loan_enabled_disabled_unlevered_invariance(loan_settings, make_annual):
    """Engine loan enabled vs disabled with identical annual inputs:
    all project/package nominal/discounting paths, unlevered NPV aliases and
    simple/discounted paybacks BIT-FOR-BIT unchanged; debt equivalence only,
    fees and balloon boundary preserved."""
    annual = make_annual(10)
    kwp = 15.0

    s_enabled = loan_settings(
        load_scenario="electrified",
        planning_heat_pump=True,
        hp_capex=5000.0,
        loan_mode="amount",
        loan_amount=14999.72,
        loan_upfront_fees_or_tax=16.0,
        loan_apr=0.051,
        loan_years=10,
        loan_include_electrification=True,
        real_discount_rate=0.03,
        inflation=0.02,
        horizon_years=10,
    )
    s_disabled = loan_settings(
        load_scenario="electrified",
        planning_heat_pump=True,
        hp_capex=5000.0,
        loan_mode="amount",
        loan_amount=0.0,
        loan_upfront_fees_or_tax=0.0,
        loan_apr=0.051,
        loan_years=10,
        loan_include_electrification=False,
        real_discount_rate=0.03,
        inflation=0.02,
        horizon_years=10,
    )

    fin_en, cash_en = e._financial(s_enabled, annual, kwp=kwp, calculate_irr=True)
    fin_dis, cash_dis = e._financial(s_disabled, annual, kwp=kwp, calculate_irr=True)
    rank_en, _ = e._financial(s_enabled, annual, kwp=kwp, calculate_irr=False, ranking_only=True)
    rank_dis, _ = e._financial(s_disabled, annual, kwp=kwp, calculate_irr=False, ranking_only=True)
    cand_en, _ = e._financial(s_enabled, annual, kwp=kwp, calculate_irr=False, candidate_only=True)
    cand_dis, _ = e._financial(s_disabled, annual, kwp=kwp, calculate_irr=False, candidate_only=True)

    # 1. Project/package nominal and discounting paths bit-for-bit unchanged
    for scope in ("pv_incremental", "whole_package"):
        np.testing.assert_array_equal(
            fin_en["cashflows_by_weather"][scope],
            fin_dis["cashflows_by_weather"][scope],
        )
        np.testing.assert_array_equal(
            fin_en["cashflows_for_discounting_by_weather"][scope],
            fin_dis["cashflows_for_discounting_by_weather"][scope],
        )

    # 2. Unlevered NPV aliases bit-for-bit unchanged
    for scope in ("pv_incremental", "whole_package"):
        assert fin_en[scope]["npv"] == fin_dis[scope]["npv"]
        assert fin_en[scope]["npv_eur"] == fin_dis[scope]["npv_eur"]
        np.testing.assert_array_equal(
            fin_en[scope]["npv_by_weather"],
            fin_dis[scope]["npv_by_weather"],
        )
        if scope == "pv_incremental":
            assert rank_en[scope]["npv"] == rank_dis[scope]["npv"]
            assert cand_en[scope]["npv"] == cand_dis[scope]["npv"]
            np.testing.assert_array_equal(
                rank_en[scope]["npv_by_weather"],
                rank_dis[scope]["npv_by_weather"],
            )

    # 3. Simple and discounted paybacks bit-for-bit unchanged
    for scope in ("pv_incremental", "whole_package"):
        assert fin_en[scope]["simple_payback"] == fin_dis[scope]["simple_payback"]
        assert fin_en[scope]["discounted_payback"] == fin_dis[scope]["discounted_payback"]

    # 4. Debt equivalence only, fees and balloon boundary preserved
    assert fin_en["loan_amortized_principal_eur"] == pytest.approx(15015.72, abs=0.01)
    assert fin_en["loan_upfront_fees_or_tax_eur"] == pytest.approx(16.0, abs=0.01)
    rnom = (1.0 + s_enabled["real_discount_rate"]) * (1.0 + s_enabled["inflation"]) - 1.0
    expected_eq_debt = _independent_debt_year_end_equivalent(15015.72, 0.051, 10, 10, rnom)
    actual_eq_debt = [row["debt_service_year_end_equivalent"] for row in cash_en[1:]]
    np.testing.assert_allclose(actual_eq_debt, expected_eq_debt, rtol=1e-5, atol=1e-4)

    for row in cash_dis:
        assert row.get("debt_service", 0.0) == 0.0
        assert row.get("debt_service_year_end_equivalent", 0.0) == 0.0
    assert fin_dis["loan_amortized_principal_eur"] == 0.0
    assert fin_dis["loan_upfront_fees_or_tax_eur"] == 0.0

    # Shorter horizon balloon settlement boundary
    debt_b, balances_b = e.loan_schedule(15015.72, 0.051, years=10, horizon=5)
    assert len(debt_b) == 5 and len(balances_b) == 5
    assert balances_b[-1] == pytest.approx(0.0, abs=1e-5)
    assert debt_b[-1] > debt_b[0]
