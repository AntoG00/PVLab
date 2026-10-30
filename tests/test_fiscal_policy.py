"""Explicit synthetic rule inputs and applicability boundaries; no legal assertions."""
from copy import deepcopy
from dataclasses import replace
import pytest
from feasibility.fiscal import rule_digest, resolve_fiscal, GAS_RATES
from feasibility.fiscal_it import ItalyFiscalPolicy


def configure_fiscal(profile):
    """Invented complete rules for tests, deliberately not a regional tax table."""
    from feasibility.thermal_baseline import resolve_thermal_baseline
    ref=resolve_thermal_baseline(profile)
    bill=sum(u.annual_bill_eur if u.annual_bill_eur is not None else
             u.annual_fixed_cost+u.annual_variable_cost for u in ref.end_uses.values() if u.carrier=='gas')
    profile['fiscal'] = dict(country='IT',rule_revision=None,gas_region=None,parameters=dict(
        policy_version='italy-fiscal-v1',confirmed=True,mode='annual_bill',annual_bill_eur=bill,
        effective_from='2020-01-01',effective_to='2100-01-01',as_of='2026-01-01',
        sources=['Synthetic arithmetic fixture, not a legal source'],electric_excise=.02,electric_vat=.1,
        pmg_price=.04,pmg_nominal_escalation=0.,gse_fee_eur_kw_year=0.,gse_fee_vat=.22,
        export_irpef=.2,regional_additional_tax=.01,municipal_additional_tax=.005,deduction_rate=0.))
    profile['fiscal']['rule_revision']=rule_digest(profile['fiscal'])
    return profile


def rule():
    from tests.test_profiles import valid_draft_payload
    p=configure_fiscal(valid_draft_payload.__wrapped__())
    return p['fiscal']


def settings(policy):
    from feasibility.assumptions import SPEC
    s={k:deepcopy(v[0]) for k,v in SPEC.items()}
    s.update({k:v for k,v in policy.parameters.items() if k in s})
    s.update(installation_year=2026,pmg_election=False,deduction_eligible_confirmed=False)
    return s


def test_rule_rejections_and_frozen_ownership():
    r=rule(); p=resolve_fiscal(r); r['parameters']['sources'].append('changed')
    assert len(p.parameters['sources'])==1
    with pytest.raises(TypeError): p.parameters['electric_vat']=.5
    with pytest.raises(ValueError,match='revision'):resolve_fiscal(r)
    r=rule();r['country']='XX'
    with pytest.raises(ValueError,match='country'):resolve_fiscal(r)
    for field,value in [('confirmed',False),('policy_version','v99'),('effective_to','2019-01-01'),('sources',[])]:
        r=rule();r['parameters'][field]=value;r['rule_revision']=rule_digest(r)
        with pytest.raises(ValueError):resolve_fiscal(r)


def test_confirmations_years_and_pinned_rate_rejection():
    p=resolve_fiscal(rule());s=settings(p)
    p.electric_bill(100,s)
    with pytest.raises(ValueError,match='PMG'):p.export_settlement(100,1,{**s,'pmg_election':True},1,4)
    p.export_settlement(100,1,{**s,'pmg_election':True,'pmg_eligible_confirmed':True},1,4)
    with pytest.raises(ValueError,match='pinned'):p.electric_bill(100,{**s,'electric_vat':.9})
    with pytest.raises(ValueError,match='Unsupported fiscal year'):p.electric_bill(100,s,100)
    p.electric_bill(100,{**s,'unsupported_fiscal_year_acknowledged':True},100)
    with pytest.raises(ValueError,match='boolean'):p.settings({**s,'pmg_eligible_confirmed':1})
    r=rule();r['parameters']['deduction_rate']=.5;r['rule_revision']=rule_digest(r);p=resolve_fiscal(r);s=settings(p)
    with pytest.raises(ValueError,match='deduction'):p.investment_relief(1000,s)
    assert p.investment_relief(1000,{**s,'deduction_eligible_confirmed':True,
        'principal_residence_confirmed':True,'deduction_tax_capacity':1000})==50


def test_gas_region_complete_rates_and_named_approximation():
    r=rule();p=resolve_fiscal(r,has_gas=True);s=settings(p)
    assert p.thermal_bill({},s)['tax_breakdown_applicable'] is False
    with pytest.raises(ValueError,match='approximation'):p.gas_breakdown(100,s)
    r['parameters']['mode']='parameters';r['rule_revision']=rule_digest(r)
    with pytest.raises(ValueError,match='region'):resolve_fiscal(r,has_gas=True)
    r['gas_region']='synthetic_region';r['parameters'].update({k:0. for k in GAS_RATES});r['rule_revision']=rule_digest(r)
    p=resolve_fiscal(r,has_gas=True);s=settings(p)
    a=p.gas_breakdown(100,s)
    assert a['national_excise_eur']==a['regional_surcharge_eur']==a['vat_eur']==0
    r['parameters']['gas_regional_0_120']=.123;r['rule_revision']=rule_digest(r)
    q=resolve_fiscal(r,has_gas=True)
    assert q.gas_breakdown(100,settings(q))['regional_surcharge_eur']==pytest.approx(12.3)
    assert q.provenance()['current_legal_review_verified'] is False


def test_runtime_rejects_unconfirmed_or_mismatched_and_identity_changes():
    from tests.test_dynamic_roofs import context
    from feasibility.runtime_context import _identity_value
    c=context(0)
    with pytest.raises(ValueError,match='Fiscal revision'):replace(c,fiscal_revision='f'*64)
    bad=_identity_value(c.household_profile);bad['fiscal']['parameters']['confirmed']=False
    with pytest.raises(ValueError):replace(c,household_profile=bad)
    # Fiscal revision is itself part of identity for compatibility contexts too.
    legacy=replace(c,household_profile=None,thermal_reference={})
    assert replace(legacy,fiscal_revision='changed').key != legacy.key


def bind_revised_fiscal(monkeypatch, ctx, fiscal):
    """Recompile synthetic metadata for a changed immutable rule revision."""
    import json, hashlib
    from feasibility import runtime_context as rt
    from feasibility.profiles import canonical_json
    profile=rt._identity_value(ctx.household_profile)
    profile['fiscal']=fiscal
    manifest=rt._identity_value(ctx.metadata['dataset_manifest'])
    manifest['profile_digest']=hashlib.sha256(canonical_json(profile)).hexdigest()
    metadata={**rt._identity_value(ctx.metadata),'dataset_manifest':manifest}
    revised=replace(ctx,household_profile=profile,fiscal_revision=fiscal['rule_revision'],
        dataset_digest=hashlib.sha256(canonical_json(manifest)).hexdigest(),metadata=metadata)
    monkeypatch.setattr(rt,'_bound',revised)
    return revised


def test_real_runtime_revision_cache_confirmations_and_non_gas(monkeypatch):
    from tests.test_dynamic_roofs import context
    from feasibility import engine as e, runtime_context as rt
    from feasibility.fiscal import current_policy
    c=context(0);monkeypatch.setattr(rt,'_bound',c)
    original=current_policy()
    r=rt._identity_value(c.household_profile)['fiscal']
    r['parameters']['electric_vat']=.2;r['rule_revision']=rule_digest(r)
    other=bind_revised_fiscal(monkeypatch,c,r)
    assert c.key != other.key
    assert current_policy() is not original
    with pytest.raises(ValueError,match='PMG'):e.validate({'pmg_eligible_confirmed':False})
    with pytest.raises(ValueError,match='boolean'):e.validate({'pmg_eligible_confirmed':1})
    s=e.validate({'pmg_election':False})
    assert e._energy_key(s,e.ALL_KEYS) != e._energy_key({**s,'pmg_eligible_confirmed':False},e.ALL_KEYS)
    assert current_policy().thermal_bill(other.thermal_reference,s)['gas_total_eur'] is None
    monkeypatch.setattr(rt,'_bound',c)
    assert current_policy() is original


def test_detailed_gas_finance_and_explicit_region(monkeypatch):
    from tests.test_thermal_engine_integration import thermal_context
    from tests.test_thermal_baseline import end_use
    from feasibility import engine as e, runtime_context as rt
    from feasibility.heatpump_finance import calculate_thermal_finance
    from feasibility.fiscal import current_policy
    c=thermal_context(end_use(),end_use(id='cooking',measured_input=120.,monthly_input=[10.]*12))
    r=rt._identity_value(c.household_profile)['fiscal']
    r['gas_region']='synthetic_region';r['parameters']['mode']='parameters'
    r['parameters'].update({k:0. for k in GAS_RATES})
    r['parameters']['gas_excise_120_480']=.1
    r['rule_revision']=rule_digest(r)
    bind_revised_fiscal(monkeypatch,c,r)
    s=e.validate({'pmg_election':False,'load_scenario':'baseline'})
    costs=calculate_thermal_finance(s)
    expected=current_policy().thermal_bill({'volume_smc':240},s)
    assert costs['baseline_gas_cost_eur']==expected['total_eur']
    assert costs['remaining_gas_cost_eur']==expected['total_eur']
    assert e.gas_bill_breakdown(s)['national_excise_eur']==12.
    assert e.build_gas_costs_audit(s)['tax_rules']['gas_region']=='synthetic_region'
    assert costs['baseline_gas_cost_eur'] != 240. # explicit old thermal bill is bypassed by selected detailed mode
    s=e.validate({'pmg_election':False,'load_scenario':'electrified','planning_heat_pump':False})
    retained=calculate_thermal_finance(s)
    zero=current_policy().thermal_bill({'volume_smc':0},s)
    assert retained['remaining_gas_cost_eur']==zero['total_eur']
    assert calculate_thermal_finance({**s,'hp_disconnect_gas':True})['remaining_gas_cost_eur']==0
    hp={**s,'planning_heat_pump':True,'hp_electric_cooking':True}
    incomplete=calculate_thermal_finance(hp,{'by_weather':[dict(
        annual_requested_space_heat_kwh=100.,annual_unmet_space_heat_kwh=50.)]})
    served=current_policy().thermal_bill({'volume_smc':180},hp)
    assert incomplete['allowance_by_weather'][0]==pytest.approx(
        expected['variable_total_eur']-served['variable_total_eur'])
    r['parameters']['gas_regional_0_120']=.2;r['rule_revision']=rule_digest(r)
    next_ctx=bind_revised_fiscal(monkeypatch,c,r)
    s=e.validate({'pmg_election':False,'load_scenario':'baseline'})
    assert calculate_thermal_finance(s)['baseline_gas_cost_eur']==pytest.approx(expected['total_eur']+24.)


def test_coverage_uses_dispatched_operating_year():
    r=rule();r['parameters'].update(effective_from='2027-01-01',effective_to='2028-01-01')
    r['rule_revision']=rule_digest(r);p=resolve_fiscal(r);s=settings(p)
    p.settings(s,1) # installation 2026, first dispatched year 2027
    with pytest.raises(ValueError,match='Unsupported fiscal year'):p.settings(s,2)
