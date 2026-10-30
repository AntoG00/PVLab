"""Regression tests for planned ensemble mean headlines, NPV linearity, and ratio-of-means autarky."""
import test_support  # Live defaults isolation
from unittest.mock import patch
import numpy as np
import pytest
from feasibility import assumptions, engine as e

SCOPES = (
    "pv_incremental", "whole_package", "equity", "financed_project",
    "solar_equity", "solar_financed", "package_equity", "package_financed",
)


@pytest.fixture
def asymmetric_setup():
    h, w = 12, 5
    raw_s = assumptions.defaults()
    raw_s.update({
        "horizon_years": h,
        "south_panels": 15,
        "panel_watts": 400,
        "pv_cost_kwp": 1200.0,
        "pv_fixed_cost": 0.0,
        "quote_total": 3000.0,
        "quote_vat_included": True,
        "battery_kwh": 10.0,
        "battery_replacement_cost_kwh": 250.0,
        "battery_replace_year": 8,
        "inverter_replacement_cost": 1200.0,
        "inverter_replace_year": 6,
        "om_annual": 120.0,
        "load_scenario": "electrified",
        "planning_heat_pump": False,
        "electrification_capex": 2000.0,
        "gas_disconnect_cost": 250.0,
        "loan_mode": "amount",
        "loan_amount": 7500.0,
        "loan_apr": 0.048,
        "loan_years": 8,
        "loan_upfront_fees_or_tax": 16.0,
        "loan_include_electrification": True,
        "real_discount_rate": 0.035,
        "inflation": 0.025,
        "deduction_rate": 0.50,
        "deduction_tax_capacity": 2500.0,
        "export_irpef": 0.23,
        "regional_additional_tax": 0.0173,
        "municipal_additional_tax": 0.008,
        "cashflow_mode": "real",
    })
    settings = e.validate(raw_s)

    # Weather 0: nonpaying outcome (0 solar). Weathers 1..4: asymmetric positive solar.
    w_solar = np.array([0.0, 0.70, 0.90, 1.40, 2.50])
    w_load = np.array([0.80, 0.90, 1.00, 1.15, 1.65])
    annual = np.zeros((h, w, len(e.FIELDS)), dtype=float)

    for yy in range(h):
        loads = 3200.0 * (1.0 + 0.02) ** yy * w_load
        pvs = 4500.0 * (1.0 - 0.005) ** yy * w_solar
        directs = np.minimum(loads * 0.4, pvs * 0.45)
        imports = loads - directs
        exports = np.maximum(0.0, pvs - directs)
        annual[yy, :, 0] = loads
        annual[yy, :, 1] = pvs
        annual[yy, :, 2] = directs
        annual[yy, :, 5] = imports
        annual[yy, :, 6] = exports
        annual[yy, :, 11] = exports * 0.95

    return settings, annual, h, w


def test_npv_linearity_and_mean_cashflow_payback_invariants(asymmetric_setup):
    settings, annual, h, w = asymmetric_setup
    rnom = (1.0 + settings["real_discount_rate"]) * (1.0 + settings["inflation"]) - 1.0

    with patch.object(e, "data", return_value={"audit": {"annual_kwh": 3200.0}}), \
         patch.object(e, "price_profile", return_value=(None, "flat_solar_capture_benchmark", 0.10)), \
         patch.object(e, "electric_bill", side_effect=lambda kwh, *args: np.asarray(kwh, dtype=float) * 0.25), \
         patch.object(e, "gas_bill", return_value=600.0):
        fin, cash = e._financial(settings, annual, kwp=6.0, calculate_irr=True)

    for scope in SCOPES:
        disc_arr = np.asarray(fin["cashflows_for_discounting_by_weather"][scope], dtype=float)
        nom_arr = np.asarray(fin["cashflows_by_weather"][scope], dtype=float)
        mean_disc_cf = np.mean(disc_arr, axis=0)
        mean_nom_cf = np.mean(nom_arr, axis=0)

        # 1. t0 inclusion and exact NPV linearity across weathers
        assert mean_disc_cf[0] == disc_arr[0, 0]
        expected_npv_of_mean = e.npv(mean_disc_cf, rnom)
        actual_weather_npvs = np.asarray(fin[scope]["npv_by_weather"], dtype=float)
        np.testing.assert_allclose(fin[scope]["npv"]["mean"], np.mean(actual_weather_npvs), rtol=1e-10, atol=1e-8)
        np.testing.assert_allclose(fin[scope]["npv"]["mean"], expected_npv_of_mean, rtol=1e-10, atol=1e-8)

        # 2. Cumulative mean equals cumsum of mean cashflows
        np.testing.assert_allclose(
            np.cumsum(mean_nom_cf), np.mean(np.cumsum(nom_arr, axis=1), axis=0), rtol=1e-10, atol=1e-8
        )

        # 3. Unconditional assertion of planned payback of mean cash flows
        expected_spb_mean = e.persistent_payback(mean_nom_cf, 0.0)
        expected_dpb_mean = e.persistent_payback(mean_disc_cf, rnom)
        assert fin[scope]["simple_payback_of_mean_cash_flows"] == pytest.approx(expected_spb_mean, rel=1e-10, abs=1e-8)
        assert fin[scope]["discounted_payback_of_mean_cash_flows"] == pytest.approx(expected_dpb_mean, rel=1e-10, abs=1e-8)

        # 4. Weather 0 is unrecovered: payback_envelope mean is None (never dropped)
        unpaid = sum(e.persistent_payback(row) is None for row in nom_arr)
        assert fin[scope]["unpaid_weather_years"] == unpaid
        assert fin[scope]["simple_payback"]["unrecovered_weather_years"] == unpaid
        if unpaid:
            assert fin[scope]["simple_payback"]["mean"] is None
        # Payback of mean cash flow differs from median
        if scope == "whole_package":
            assert expected_spb_mean is not None and unpaid > 0
            assert expected_spb_mean != fin[scope]["simple_payback"]["median"]
        if scope == "pv_incremental":
            assert unpaid > 0

    # 5. Cash row mean and display mean fields asserted unconditionally
    for yy, c in enumerate(cash):
        factor = (1.0 + settings["inflation"]) ** yy
        for scope in SCOPES:
            expected_mean = float(np.mean(np.asarray(fin["cashflows_by_weather"][scope], dtype=float)[:, yy]))
            np.testing.assert_allclose(c[f"{scope}_mean"], expected_mean, rtol=1e-10, atol=1e-8)
            np.testing.assert_allclose(c[f"display_{scope}_mean"], expected_mean / factor, rtol=1e-10, atol=1e-8)
            assert f"{scope}_median" in c


def test_persistent_payback_reversal_handling():
    """Verify persistent payback requires cumulative sum to remain positive indefinitely."""
    # Cumulatives: -1000, -600, -200, +200 (crosses at 3), -300 (dip at 4), +100 (crosses at 5), +500
    flows = [-1000.0, 400.0, 400.0, 400.0, -500.0, 400.0, 400.0]
    pb = e.persistent_payback(flows, 0.0)
    assert pb == pytest.approx(4.75, abs=1e-8)


def test_evaluate_energy_means_and_ensemble_autarky(asymmetric_setup):
    settings, annual, h, w = asymmetric_setup
    mock_data = {
        "audit": {"annual_kwh": 3200.0, "limitations": []},
        "years": np.array([2001, 2002, 2003, 2004, 2005]),
        "valid": np.ones((w, 8760), dtype=bool),
    }

    with patch.object(e, "_energy_cache", return_value=(annual, {})), \
         patch.object(e, "data", return_value=mock_data), \
         patch.object(e, "price_profile", return_value=(None, "flat_solar_capture_benchmark", 0.10)), \
         patch.object(e, "electric_bill", side_effect=lambda kwh, *args: np.asarray(kwh, dtype=float) * 0.25), \
         patch.object(e, "gas_bill", return_value=600.0), \
         patch.object(e, "weather_period", return_value=(w, 2001, 2005, "2001–2005")):
        res = e.evaluate(settings)

    energy = res["energy"]
    first = annual[0]
    mean_load = float(np.mean(first[:, 0]))
    mean_imp = float(np.mean(first[:, 5]))
    np.testing.assert_allclose(energy["load_kwh"]["mean"], mean_load, rtol=1e-10, atol=1e-8)
    np.testing.assert_allclose(energy["grid_import_kwh"]["mean"], mean_imp, rtol=1e-10, atol=1e-8)

    # Ratio of means autarky vs mean of autarky ratios
    mean_of_ratios = float(np.mean(1.0 - first[:, 5] / first[:, 0]))
    ratio_of_means = 1.0 - mean_imp / mean_load
    assert ratio_of_means != pytest.approx(mean_of_ratios, abs=1e-4)
    np.testing.assert_allclose(energy["ensemble_autarky"], ratio_of_means, rtol=1e-10, atol=1e-8)


def test_ensemble_annual_flows_sankey_conservation():
    settings = e.validate({
        "pv_dispatch_model": "physical", "pv_battery_coupling": "ac_equivalent",
        "battery_kwh": 10.0, "charge_kw": 5.0, "discharge_kw": 5.0, "inverter_kw": 5.0,
    })
    traces, annuals, mapped = [], [], []
    for pv, load, imp, exp, chg, dis, direct in [
        (100.0, 80.0, 25.0, 40.0, 20.0, 15.0, 40.0),
        (200.0, 120.0, 30.0, 100.0, 30.0, 20.0, 70.0),
        (350.0, 190.0, 50.0, 200.0, 40.0, 30.0, 110.0),
    ]:
        traces.append(np.array([[pv, load, imp, exp, 2.0, direct, chg, dis]]))
        mapped.append(np.array([pv]))
        annuals.append({
            "weather_year": 2000, "pv_input_kwh": pv, "pv_kwh": pv, "load_kwh": load,
            "grid_import_kwh": imp, "grid_export_kwh": exp, "direct_kwh": direct,
            "battery_loss_kwh": chg - dis, "end_soc_kwh": 0.0, "clipped_kwh": 0.0,
        })
    flows = e._ensemble_annual_flows(settings, {"traces": traces, "annual": annuals, "mapped_data": {"mapped_pv": mapped}})
    np.testing.assert_allclose(flows["autarky"], 1 - flows["grid_import_kwh"] / flows["load_demand_kwh"], rtol=1e-10, atol=1e-8)
    np.testing.assert_allclose(flows["pv_generation_kwh"], flows["direct_use_kwh"] + flows["battery_charge_kwh"] + flows["grid_export_kwh"], rtol=1e-10, atol=1e-8)
    np.testing.assert_allclose(flows["load_demand_kwh"], flows["direct_use_kwh"] + flows["battery_discharge_kwh"] + flows["grid_import_kwh"], rtol=1e-10, atol=1e-8)


def test_quote_evaluate_headline_energy_flows_matches_dispatch_matrix():
    settings = assumptions.defaults()
    with patch.object(e, "chart_data", return_value={}):
        qe = e.quote_evaluate(settings)
    dm = e.dispatch_matrix(settings)

    qe_flows = qe["result"]["headline_energy_flows"]
    dm_flows = dm["annual_flows"]

    # Full precision matching between quote_evaluate headline_energy_flows and dispatch_matrix annual_flows
    assert qe_flows["pv_generation_kwh"] == dm_flows["pv_generation_kwh"]
    assert qe_flows["load_demand_kwh"] == dm_flows["load_demand_kwh"]
    assert qe_flows["grid_import_kwh"] == dm_flows["grid_import_kwh"]
    assert qe_flows["autarky"] == dm_flows["autarky"]
    assert qe_flows["forecast_year"] == dm.get("forecast_year", 2027)
    assert qe["headline_energy_flows"] is qe_flows

    # Retained native lifecycle diagnostics unchanged
    metrics = qe["result"]["metrics"]
    energy = qe["result"]["energy"]
    assert "autarky" in metrics
    assert "pv_kwh" in metrics
    assert "grid_import_kwh" in metrics
    assert "ensemble_autarky" in energy
    # Verify native lifecycle PV mean reflects unmapped native weather including leap years
    assert energy["pv_kwh"]["mean"] != qe_flows["pv_generation_kwh"]
