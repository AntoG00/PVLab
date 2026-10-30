"""Phase 14 arithmetic parity tests for ItalyFiscalPolicy.legacy()."""
import pytest
from feasibility.fiscal_it import ItalyFiscalPolicy


def test_electric_bill_hand_math():
    p = ItalyFiscalPolicy.legacy()
    s = {
        'inflation': 0.02, 'energy_real_escalation': 0.03, 'regulated_real_escalation': 0.01,
        'retail_commodity': 0.20, 'retail_regulated': 0.05, 'electric_excise': 0.02,
        'electric_vat': 0.10, 'supplier_fixed_month': 5.0, 'network_fixed_month': 3.0,
        'power_fixed_kw_month': 1.5, 'baseline_power_kw': 3.0,
    }
    assert p.electric_bill(1000.0, s, year=1) == pytest.approx(462.0)
    assert p.electric_bill(1000.0, s, year=1, power=4.5) == pytest.approx(491.7)
    assert p.electric_bill(1000.0, s, year=2) == pytest.approx(480.4404)


def test_export_settlement_pmg_fee_tax():
    p = ItalyFiscalPolicy.legacy()
    s = {
        'inflation': 0.0, 'pmg_price': 0.10, 'pmg_nominal_escalation': 0.0,
        'pmg_election': True, 'gse_fee_eur_kw_year': 5.0, 'gse_fee_vat': 0.22,
        'export_irpef': 0.20, 'regional_additional_tax': 0.02,
        'municipal_additional_tax': 0.01, 'export_fee_tax_deductible': True,
    }
    r = p.export_settlement(1000.0, 60.0, s, year=1, kwp=4)
    assert r['gross'] == pytest.approx(100.0)
    assert r['fees'] == pytest.approx(24.4)
    assert r['tax'] == pytest.approx(17.388)
    assert r['net'] == pytest.approx(58.212)

    s_off = dict(s, pmg_election=False)
    r_neg = p.export_settlement(1000.0, -50.0, s_off, year=1, kwp=3)
    assert r_neg['gross'] == pytest.approx(-50.0)
    assert r_neg['fees'] == 0.0
    assert r_neg['tax'] == 0.0
    assert r_neg['net'] == pytest.approx(-50.0)


def test_investment_relief_timing_and_capacity():
    p = ItalyFiscalPolicy.legacy()
    s = {
        'deduction_eligible_fraction': 1.0, 'deduction_rate': 0.50,
        'deduction_tax_capacity': 4500.0, 'deduction_start_year': 2,
    }
    capex = 100000.0
    assert p.investment_relief(capex, s, year=1) == 0.0
    for y in (2, 5, 11):
        assert p.investment_relief(capex, s, year=y) == pytest.approx(4500.0)
    assert p.investment_relief(capex, s, year=12) == 0.0
