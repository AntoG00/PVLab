"""Hand-calculated carrier physics and electrical replacement contracts."""
import numpy as np
import pytest

from feasibility.profiles import parse_profile, profile_to_dict
from feasibility.thermal_baseline import (
    allocate_gas_baseline, input_to_useful, replacement_delta, resolve_thermal_baseline,
)


def payload(*uses):
    return dict(schema_version=1, id='1'*32, country='IT', timezone='Europe/Rome',
        location=dict(latitude=45., longitude=9.),
        consumption=dict(artifact_id=None, kind='monthly',
            coverage=dict(start='2024-01-01', end='2025-01-01', complete=True),
            projection_policy='synthetic_v1', acknowledged=True), roofs=[],
        thermal=dict(end_uses=list(uses)),
        market=dict(zone='NORD', series='NORD', window_start='2024-01-01',
                    window_end='2025-01-01', valuation_mode='flat', price_revision=None),
        fiscal=dict(country='IT', rule_revision=None, gas_region=None, parameters={}),
        default_settings={})


def end_use(carrier='gas', id='space_heat', **overrides):
    v = dict(id=id, carrier=carrier, measured_input=120., monthly_input=[10.]*12,
        unit={'gas':'smc', 'biomass':'kg', 'district':'kwh', 'electricity':'kwh', 'none':None}[carrier],
        monthly_useful_kwh=None, efficiency=None if carrier=='electricity' else .8,
        cop=3. if carrier=='electricity' else None,
        conversion_kwh_per_unit=10. if carrier=='gas' else 5. if carrier=='biomass' else 1.,
        annual_fixed_cost=20., annual_variable_cost=100., annual_bill_eur=None,
        included_in_electric_load=carrier=='electricity', action='replace', artifact_id=None,
        coverage=dict(start='2024-01-01', end='2025-01-01', complete=True), period_relation='same')
    v.update(overrides)
    return v


def test_conversions_hand_calculated():
    assert input_to_useful(120., 'smc', 10.5, .9) == 1134.
    assert input_to_useful(500., 'kg', 4.8, .85) == 2040.
    assert input_to_useful(2500., 'kwh', 1., .95) == 2375.
    assert input_to_useful(1500., 'kwh', 1., 3.2) == 4800.
    assert input_to_useful(0, 'smc', 10.5, .9) == 0.


@pytest.mark.parametrize('args', [
    (100,'m3',10.5,.9), (100,'kwh',1.05,.9), (-1,'smc',10.5,.9),
    (100,'smc',0,.9), (100,'smc',-1,.9), (100,'smc',10.5,0),
    (100,'smc',10.5,-.9), (float('nan'),'smc',10.5,.9),
    (100,'smc',float('inf'),.9), (100,'smc',10.5,float('-inf')),
])
def test_invalid_conversion_values(args):
    with pytest.raises(ValueError):
        input_to_useful(*args)


@pytest.mark.parametrize('args', [(True,'smc',10.5,.9), (100,'smc',False,.9),
    ('100','smc',10.5,.9), (100,'smc','10.5',.9), (100,'smc',10.5,'0.9')])
def test_invalid_conversion_types(args):
    with pytest.raises(TypeError):
        input_to_useful(*args)


def test_conversion_overflow():
    with pytest.raises(OverflowError):
        input_to_useful(1e308, 'kwh', 1, 10.)


def test_gas_conservation_and_end_use_efficiencies():
    # Per month: 20 Smc total, 10 nonheat -> 10 heat, 2 cooking, 8 DHW.
    shares = allocate_gas_baseline([20.]*12, 10., 24.)
    assert shares == dict(space_heat=(10.,)*12, dhw=(8.,)*12, cooking=(2.,)*12)
    uses = [end_use(id=k, measured_input=sum(v), monthly_input=None,
                    efficiency=.5 if k=='cooking' else .8) for k,v in shares.items()]
    p = payload(*uses)
    p['thermal']['gas_allocation'] = dict(monthly_gas_smc=[20.]*12,
                                          summer_gas_base=10., cooking_smc_annual=24.)
    r = resolve_thermal_baseline(p)
    assert r.annual_gas_smc == 240.
    assert r.monthly_useful_kwh == dict(space_heat=(80.,)*12, dhw=(64.,)*12, cooking=(10.,)*12)
    assert r.end_uses['dhw'].provenance['method'] == 'allocated_gas'
    assert r.end_uses['space_heat'].provenance['coverage'].start == '2024-01-01'
    with pytest.raises(TypeError):
        r.end_uses['dhw'] = None
    p['thermal']['end_uses'].pop()
    with pytest.raises(ValueError, match='claimed'):
        resolve_thermal_baseline(p)


@pytest.mark.parametrize('gas,base,cooking', [([1.]*12,1.,13.), ([0.]*12,0.,1.),
    ([-1.]*12,1.,0.), ([1.]*11,1.,0.), ([True]*12,1.,0.)])
def test_invalid_gas_allocation(gas,base,cooking):
    with pytest.raises((TypeError,ValueError)):
        allocate_gas_baseline(gas,base,cooking)


def test_zero_gas():
    assert all(not any(x) for x in allocate_gas_baseline([0.]*12,0.,0.).values())


def test_mixed_carriers_and_direct_bill_independent_of_heat():
    p = payload(end_use('biomass', id='space_heat'), end_use('district', id='dhw'),
                end_use('electricity', id='cooking'))
    r = resolve_thermal_baseline(p)
    assert r.monthly_useful_kwh == dict(space_heat=(40.,)*12, dhw=(8.,)*12, cooking=(30.,)*12)
    assert r.annual_gas_smc == 0.
    p['thermal']['end_uses'][0].update(annual_fixed_cost=0., annual_variable_cost=0., annual_bill_eur=300.)
    direct = resolve_thermal_baseline(p)
    assert direct.monthly_useful_kwh == r.monthly_useful_kwh
    assert direct.end_uses['space_heat'].annual_bill_eur == 300.
    p['thermal']['end_uses'][0]['annual_fixed_cost'] = 1.
    with pytest.raises(ValueError, match='annual bill'):
        resolve_thermal_baseline(p)


@pytest.mark.parametrize('carrier', ['gas','biomass','district','electricity'])
def test_direct_useful_heat(carrier):
    p = payload(end_use(carrier, measured_input=None, monthly_input=None, unit=None,
                        monthly_useful_kwh=[24.]*12))
    r = resolve_thermal_baseline(p)
    assert r.monthly_useful_kwh['space_heat'] == (24.,)*12
    assert r.end_uses['space_heat'].monthly_input == ((8.,)*12 if carrier=='electricity' else None)
    assert r.annual_gas_smc == (None if carrier=='gas' else 0.)
    if carrier == 'electricity':
        assert r.end_uses['space_heat'].unit == 'kwh'


def test_same_useful_heat_compares_carriers_without_financial_inference():
    for carrier, amount in [('gas', 10.), ('biomass', 20.), ('district', 100.), ('electricity', 80./3.)]:
        ref = resolve_thermal_baseline(payload(end_use(carrier,
            measured_input=amount*12, monthly_input=[amount]*12)))
        assert ref.monthly_useful_kwh['space_heat'] == (80.,)*12
        assert ref.end_uses['space_heat'].annual_variable_cost == 100.


def test_missing_baseline_cop_and_unknown_consumption_period_rejected():
    p = payload(end_use(measured_input=None, monthly_input=None, unit=None))
    with pytest.raises(ValueError, match='Choose'):
        resolve_thermal_baseline(p)
    p = payload(end_use('electricity', measured_input=None, monthly_input=None,
                        unit=None, monthly_useful_kwh=[1.]*12, cop=None))
    with pytest.raises(ValueError, match='COP'):
        resolve_thermal_baseline(p)
    p = payload(end_use(period_relation='different'))
    p['consumption']['coverage'] = dict(start=None,end=None,complete=False)
    with pytest.raises(ValueError, match='Consumption coverage'):
        resolve_thermal_baseline(p)


def test_old_thermal_canonical_representation_preserved():
    use = end_use()
    for key in ('monthly_input','conversion_kwh_per_unit','coverage','period_relation','annual_bill_eur'):
        use.pop(key)
    p = payload(use)
    assert profile_to_dict(parse_profile(p)) == p


def test_empty_and_no_heating():
    assert resolve_thermal_baseline(payload()).monthly_useful_kwh == {}
    p = payload(end_use('none', measured_input=None, monthly_input=None, unit=None,
        conversion_kwh_per_unit=None, efficiency=None, annual_fixed_cost=0, annual_variable_cost=0))
    assert resolve_thermal_baseline(p).monthly_useful_kwh == dict(space_heat=(0.,)*12)


@pytest.mark.parametrize('change,pattern', [
    ({'monthly_input':[10.]*11}, '12'), ({'monthly_input':[11.]*12}, 'conserve'),
    ({'unit':'kg'}, 'unit'), ({'efficiency':1.1}, 'range'),
    ({'conversion_kwh_per_unit':0}, 'range'), ({'included_in_electric_load':True}, 'electricity'),
    ({'cop':3}, 'COP'), ({'monthly_useful_kwh':[1.]*12}, 'OR'),
])
def test_nested_validation(change,pattern):
    with pytest.raises(ValueError, match=pattern):
        parse_profile(payload(end_use(**change)))


@pytest.mark.parametrize('change,pattern', [
    ({'coverage':None}, 'coverage'), ({'period_relation':None}, 'period_relation'),
    ({'period_relation':'different'}, 'disagrees'), ({'monthly_input':None}, 'monthly'),
    ({'conversion_kwh_per_unit':None}, 'conversion'), ({'efficiency':None}, 'efficiency'),
    ({'coverage':dict(start='2024-01-01',end='2025-01-01',complete=False)}, 'Complete'),
])
def test_resolution_requires_explicit_inputs(change,pattern):
    with pytest.raises(ValueError, match=pattern):
        resolve_thermal_baseline(payload(end_use(**change)))


def test_different_consumption_period_audited_and_round_trip():
    p = payload(end_use(coverage=dict(start='2023-01-01',end='2024-01-01',complete=True),
                         period_relation='different'))
    profile = parse_profile(p)
    assert parse_profile(profile_to_dict(profile)) == profile
    assert resolve_thermal_baseline(profile).end_uses['space_heat'].provenance['period_relation'] == 'different'


def electric_reference(*ids, artifact='a'*32):
    return resolve_thermal_baseline(payload(*(end_use('electricity', id=k, artifact_id=artifact)
                                              for k in ids)))


def test_unchanged_included_zero_and_no_gas_saving():
    ref = electric_reference('space_heat')
    np.testing.assert_array_equal(replacement_delta(ref, {'space_heat':'unchanged'},
        {'household':[3.,5.]}, {}), [0.,0.])
    assert ref.annual_gas_smc == 0.
    np.testing.assert_array_equal(replacement_delta(ref, {'space_heat':'unchanged'},
        {'household':[3.,5.]}, {'space_heat':[1.,3.]}), [0.,0.])


def test_electric_replace_subtracts_exactly_before_adding():
    ref = electric_reference('space_heat')
    result = replacement_delta(ref, {'space_heat':'replace'},
        {'household':[3.,5.], 'space_heat':[2.,4.]}, {'space_heat':[1.,3.]})
    np.testing.assert_array_equal(result, [-1.,-1.])
    assert not result.flags.writeable


@pytest.mark.parametrize('old', [{'household':[3.,5.]}, {'space_heat':[2.,4.]}])
def test_missing_separated_load_rejected(old):
    with pytest.raises(ValueError, match='separated'):
        replacement_delta(electric_reference('space_heat'), {'space_heat':'replace'}, old, {'space_heat':[1.,3.]})


def test_missing_artifact_and_overlapping_claims_rejected():
    with pytest.raises(ValueError, match='artifact'):
        replacement_delta(electric_reference('space_heat',artifact=None), {'space_heat':'replace'},
                          {'household':[5.], 'space_heat':[2.]}, {'space_heat':[1.]})
    ref = electric_reference('space_heat','dhw')
    with pytest.raises(ValueError, match='Overlapping'):
        replacement_delta(ref, {'space_heat':'replace','dhw':'replace'},
            {'household':[5.], 'space_heat':[2.], 'dhw':[2.]}, {'space_heat':[1.], 'dhw':[1.]})


def test_aggregate_negative_residual_even_when_new_demand_would_hide_it():
    p = payload(end_use('electricity', artifact_id='a'*32),
                end_use('electricity', id='dhw', artifact_id='b'*32))
    with pytest.raises(ValueError, match='Negative residual'):
        replacement_delta(resolve_thermal_baseline(p), {'space_heat':'replace','dhw':'replace'},
            {'household':[3.], 'space_heat':[2.], 'dhw':[2.]}, {'space_heat':[10.], 'dhw':[10.]})


def test_add_and_nonembedded_replacement():
    ref = resolve_thermal_baseline(payload(end_use('gas')))
    for mode in ('add','replace'):
        np.testing.assert_array_equal(replacement_delta(ref, {'space_heat':mode}, {},
                                                        {'space_heat':[2.,4.]}), [2.,4.])
    with pytest.raises(ValueError, match='added again'):
        replacement_delta(electric_reference('space_heat'), {'space_heat':'add'}, {}, {'space_heat':[1.]})


@pytest.mark.parametrize('new', [[True], ['1'], [-1.], [float('nan')], [[1.]], [], [1.,2.]])
def test_bad_hourly_series(new):
    with pytest.raises(ValueError):
        replacement_delta(electric_reference('space_heat'), {'space_heat':'replace'},
            {'household':[3.], 'space_heat':[2.]}, {'space_heat':new})


@pytest.mark.parametrize('key', ['ev','cooling'])
def test_ev_cooling_incremental_and_replacement(key):
    p = payload()
    p['thermal']['electric_loads'] = [dict(id=key, included_in_electric_load=False, action='add',
                                          artifact_id=None, incremental_annual_kwh=5.)]
    ref = resolve_thermal_baseline(p)
    assert ref.summer_ac_multiplier == 1.
    np.testing.assert_array_equal(replacement_delta(ref, {key:'add'}, {}, {key:[2.,3.]}), [2.,3.])
    with pytest.raises(ValueError, match='conserve'):
        replacement_delta(ref, {key:'add'}, {}, {key:[3.,3.]})
    p['thermal']['electric_loads'][0].update(incremental_annual_kwh=None)
    with pytest.raises(ValueError, match='incremental'):
        resolve_thermal_baseline(p)
    p['thermal']['electric_loads'][0].update(action='replace', included_in_electric_load=True,
                                            artifact_id='a'*32)
    ref = resolve_thermal_baseline(p)
    np.testing.assert_array_equal(replacement_delta(ref, {key:'replace'},
        {'household':[3.,4.], key:[2.,3.]}, {key:[1.,2.]}), [-1.,-1.])
    np.testing.assert_array_equal(replacement_delta(ref, {key:'unchanged'}, {'household':[3.,4.]}, {}), [0.,0.])
