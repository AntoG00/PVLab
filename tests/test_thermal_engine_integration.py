"""Synthetic carrier baselines, immutable bindings and replacement conservation."""
from dataclasses import FrozenInstanceError, dataclass, replace
import json
import numpy as np
import pytest
from feasibility import engine as e, runtime_context as runtime
from feasibility.dataset_compile import compile_dataset
from feasibility.thermal_baseline import resolve_thermal_baseline
from tests.test_dataset_compile import inputs
from tests.test_dynamic_roofs import context
from tests.test_thermal_baseline import end_use


def thermal_context(*uses, electric_loads=(), separated=False, disconnect=(), years=(2023,)):
    profile, solar, load = inputs(0, years=years)
    profile['roofs'] = []
    profile['consumption']['coverage'] = dict(start='2024-01-01', end='2025-01-01', complete=True)
    profile['thermal'] = dict(end_uses=list(uses), electric_loads=list(electric_loads))
    from tests.test_fiscal_policy import configure_fiscal
    configure_fiscal(profile)
    compiled = compile_dataset(profile, solar, load)
    dataset = {**compiled.arrays, 'audit': json.loads(compiled.audit_bytes)}
    metadata = dict(dataset_manifest=json.loads(compiled.manifest_bytes), compiler_version='dataset-compiler-v2')
    thermal_inputs = {}
    if separated:
        thermal_inputs['separated_electricity'] = {u['id']: dict(artifact_id=u['artifact_id'],
            civil=dataset['load_civil'] * .1, standard=dataset['load_standard'] * .1)
            for u in uses if u['included_in_electric_load']}
    if disconnect:
        thermal_inputs['disconnect_end_uses'] = list(disconnect)
    if thermal_inputs:
        metadata['thermal_inputs'] = thermal_inputs
    return replace(context(0), fiscal_revision=profile['fiscal']['rule_revision'], input_revision=compiled.digest, dataset_digest=compiled.digest,
        dataset=dataset, metadata=metadata, household_profile=profile,
        thermal_reference=resolve_thermal_baseline(profile))


def bind(monkeypatch, *uses, **kwargs):
    ctx = thermal_context(*uses, **kwargs)
    monkeypatch.setattr(runtime, '_bound', ctx)
    return ctx


def settings(**changes):
    return e.validate(dict(roof_panels={}, load_scenario='electrified', scop=4,
                          heating_schedule_mode='continuous', hp_indoor_temp=18, **changes))


def test_runtime_owns_thermal_reference_and_rejects_tampering():
    ctx = thermal_context(end_use('gas'))
    ref = ctx.thermal_reference
    with pytest.raises(FrozenInstanceError):
        ref.summer_ac_multiplier = 2
    with pytest.raises(TypeError):
        ref.end_uses['new'] = ref.end_uses['space_heat']
    with pytest.raises(TypeError):
        ref.end_uses['space_heat'].provenance['method'] = 'changed'
    with pytest.raises(FrozenInstanceError):
        ref.end_uses['space_heat'].provenance['coverage'].complete = False
    changed = replace(ref, end_uses={'space_heat': replace(ref.end_uses['space_heat'], annual_variable_cost=999.)})
    with pytest.raises(ValueError, match='match the pinned'):
        replace(ctx, thermal_reference=changed)
    with pytest.raises(ValueError, match='match the pinned'):
        replace(ctx, thermal_reference={'GAS_MONTHLY': np.zeros(12)})
    with pytest.raises(ValueError, match='pinned household'):
        replace(ctx, household_profile=None)


@pytest.mark.parametrize('change', [dict(monthly_input=[20.]*12, measured_input=240.),
    dict(annual_variable_cost=101.), dict(efficiency=.9), dict(action='unchanged'),
    dict(period_relation='different', coverage=dict(start='2023-01-01', end='2024-01-01', complete=True))])
def test_thermal_changes_invalidate_runtime_and_caches(monkeypatch, change):
    first = thermal_context(end_use('gas'))
    second = thermal_context(end_use('gas', **change))
    assert first.key != second.key
    monkeypatch.setattr(runtime, '_bound', second)
    with pytest.raises(ValueError, match='runtime key'):
        e._load_cache_cached(first.key, ())
    from feasibility.heatpump_integration import get_source_reference_degree_hours
    with pytest.raises(ValueError, match='runtime key'):
        get_source_reference_degree_hours(first.key, 18.)


def test_reference_mapping_order_and_unknown_objects():
    ctx = thermal_context(end_use('gas'))
    use = ctx.thermal_reference.end_uses['space_heat']
    same = replace(ctx.thermal_reference, end_uses={'space_heat': replace(use,
        provenance=dict(reversed(list(use.provenance.items()))))})
    assert replace(ctx, thermal_reference=same).key == ctx.key
    @dataclass(frozen=True)
    class Unknown:
        value: int
    with pytest.raises(TypeError, match='Unsupported runtime value'):
        runtime.freeze(Unknown(1))


def test_separated_binding_is_copied_validated_and_identified():
    ctx = thermal_context(end_use('electricity', artifact_id='a'*32), separated=True)
    metadata = runtime.mutable_copy(ctx.metadata)
    series = np.array(metadata['thermal_inputs']['separated_electricity']['space_heat']['civil'])
    metadata['thermal_inputs']['separated_electricity']['space_heat']['civil'] = series
    owned = replace(ctx, metadata=metadata)
    before = owned.key
    series[:] = 0
    assert owned.key == before
    a = owned.metadata['thermal_inputs']['separated_electricity']['space_heat']['civil']
    assert a.sum() == pytest.approx(120)
    with pytest.raises(ValueError):
        a.setflags(write=True)
    with pytest.raises(ValueError, match='monthly input'):
        replace(ctx, metadata=metadata)
    metadata = runtime.mutable_copy(ctx.metadata)
    # Move energy within one month: valid separated artifact, distinct identity.
    series = np.array(metadata['thermal_inputs']['separated_electricity']['space_heat']['civil'])
    series[0,1] += series[0,0]
    series[0,0] = 0
    metadata['thermal_inputs']['separated_electricity']['space_heat']['civil'] = series
    assert replace(ctx, metadata=metadata).key != ctx.key
    metadata['thermal_inputs']['separated_electricity']['space_heat']['artifact_id'] = 'b'*32
    with pytest.raises(ValueError, match='claimed embedded artifact'):
        replace(ctx, metadata=metadata)


@pytest.mark.parametrize('carrier,useful_month', [('gas',80.),('biomass',40.),('district',8.)])
def test_source_forecast_dispatch_and_costs(monkeypatch, carrier, useful_month):
    ctx = bind(monkeypatch, end_use(carrier))
    s = settings()
    source,ev,diagnostics = e.load_profiles(s)
    assert ev.sum() == 0
    assert source.sum() == pytest.approx(1200 + 12*useful_month/4)
    assert diagnostics['thermal_baseline']['space_heat']['carrier'] == carrier
    assert diagnostics['heating_smc'] == (120 if carrier == 'gas' else None)
    mapped = e._forecast_mapped_series(s)
    assert mapped['mapped_load'].sum() == pytest.approx(source.sum())
    result = e.evaluate(s)
    costs = result['finance']['thermal_finance']
    assert costs['baseline_cost_eur'] == 120
    assert costs['remaining_cost_eur'] == 20
    assert result['energy']['load_kwh']['median'] == pytest.approx(source.sum())
    cash = result['annual_cashflows'][1]
    assert cash['baseline_package_bill'] == pytest.approx(cash['baseline_electric_bill'] + 120)
    assert cash['whole_package'] == pytest.approx(cash['baseline_package_bill'] - cash['with_system_electric_bill'] - 20)
    if carrier != 'gas':
        assert result['gas_costs']['applicable'] is False
        assert result['finance']['historical_annual_gas_bill_eur'] is None
        assert cash['baseline_gas_bill'] is None


def test_unchanged_electricity_is_not_added_or_credited_twice(monkeypatch):
    ctx = bind(monkeypatch, end_use('electricity', action='unchanged'))
    s = settings()
    source,ev,diagnostics = e.load_profiles(s)
    np.testing.assert_array_equal(source, ctx.dataset['load_civil'])
    assert diagnostics['additional_electricity_kwh'] == 0
    assert e.evaluate(s)['finance']['thermal_finance']['baseline_cost_eur'] == 0
    assert e._forecast_mapped_series(s)['mapped_load'].sum() == pytest.approx(1200)


def test_electric_replacement_conserves_source_and_forecast(monkeypatch):
    bind(monkeypatch, end_use('electricity', artifact_id='a'*32), separated=True)
    s = settings()
    source,_,diagnostics = e.load_profiles(s)
    assert source.sum() == pytest.approx(1200-120+90)
    assert diagnostics['embedded_electricity_removed_kwh'] == pytest.approx(120)
    assert diagnostics['additional_electricity_kwh'] == pytest.approx(-30)
    assert e._forecast_mapped_series(s)['mapped_load'].sum() == pytest.approx(1170)
    assert e.evaluate(s)['energy']['load_kwh']['median'] == pytest.approx(1170)


def test_replacement_without_separated_input_rejected(monkeypatch):
    bind(monkeypatch, end_use('electricity', artifact_id='a'*32))
    with pytest.raises(ValueError, match='separated existing series'):
        e.load_profiles(settings())


def test_replacement_batch_rejects_negative_gross_residual(monkeypatch):
    # Each series has measured monthly input, but the batch exceeds gross load.
    uses = [end_use('electricity', id=k, monthly_input=[60.]*12, measured_input=720., artifact_id=c*32)
            for k,c in [('space_heat','a'),('dhw','b')]]
    ctx = thermal_context(*uses)
    metadata = runtime.mutable_copy(ctx.metadata)
    metadata['thermal_inputs'] = {'separated_electricity': {u['id']: dict(artifact_id=u['artifact_id'],
        civil=ctx.dataset['load_civil']*.6, standard=ctx.dataset['load_standard']*.6) for u in uses}}
    monkeypatch.setattr(runtime, '_bound', replace(ctx, metadata=metadata))
    with pytest.raises(ValueError, match='Negative residual household demand'):
        e.load_profiles(settings())


def test_no_heat_and_baseline_scenario_leave_measured_load(monkeypatch):
    ctx = bind(monkeypatch)
    source,ev,diagnostics = e.load_profiles(settings())
    np.testing.assert_array_equal(source,ctx.dataset['load_civil'])
    assert e.evaluate(settings())['finance']['conversion_capex_eur'] == 0
    ctx = bind(monkeypatch, end_use('biomass'))
    s = settings(); s['load_scenario'] = 'baseline'
    np.testing.assert_array_equal(e.load_profiles(s)[0],ctx.dataset['load_civil'])
    assert e.evaluate(s)['finance']['thermal_finance']['remaining_cost_eur'] == 120


def test_explicit_incremental_ev_and_cooling_only(monkeypatch):
    loads = [dict(id=k, included_in_electric_load=False, action='add', artifact_id=None,
                  incremental_annual_kwh=energy) for k,energy in [('ev',100.),('cooling',50.)]]
    bind(monkeypatch, electric_loads=loads)
    s = settings(); s['load_scenario'] = 'ev'
    source,ev,diagnostics = e.load_profiles(s)
    assert source.sum() == pytest.approx(1250)
    assert ev.sum() == pytest.approx(100)
    assert diagnostics['additional_electricity_kwh'] == pytest.approx(150)
    assert e._forecast_mapped_series(s)['mapped_load'].sum() == pytest.approx(1350)
    s['summer_ac_multiplier'] = 2
    with pytest.raises(ValueError, match='explicit incremental energy'):
        e.load_profiles(s)


def test_fixed_carrier_costs_need_explicit_disconnection(monkeypatch):
    from feasibility.heatpump_finance import calculate_thermal_finance
    bind(monkeypatch, end_use('district'))
    assert calculate_thermal_finance(settings())['remaining_cost_eur'] == 20
    first = runtime.current_runtime()
    second = thermal_context(end_use('district'), disconnect=('space_heat',))
    assert first.key != second.key
    monkeypatch.setattr(runtime,'_bound',second)
    assert calculate_thermal_finance(settings())['remaining_cost_eur'] == 0
    bind(monkeypatch, end_use('gas'))
    s = settings(); s['hp_disconnect_gas'] = True
    assert calculate_thermal_finance(s)['remaining_cost_eur'] == 0
    bind(monkeypatch, end_use('gas'), end_use('gas',id='dhw',action='unchanged'))
    with pytest.raises(ValueError,match='every gas end use'):
        calculate_thermal_finance(s)


def test_direct_bill_never_infers_avoidable_variable_share(monkeypatch):
    from feasibility.heatpump_finance import calculate_thermal_finance
    use = end_use('district',annual_bill_eur=500.,annual_fixed_cost=0.,annual_variable_cost=0.)
    bind(monkeypatch,use)
    assert calculate_thermal_finance(settings())['remaining_cost_eur'] == 500
    bind(monkeypatch,use,disconnect=('space_heat',))
    assert calculate_thermal_finance(settings())['remaining_cost_eur'] == 0


@pytest.mark.parametrize('carrier',['gas','biomass','district'])
def test_heat_pump_source_forecast_use_same_carrier_heat(monkeypatch,carrier):
    from feasibility.heatpump_integration import get_source_heat_pump_hourly
    # Identical useful heat, independently of source carrier units.
    use = end_use(carrier, measured_input=None, unit=None, monthly_input=None, monthly_useful_kwh=[80.]*12)
    bind(monkeypatch,use)
    s = settings(planning_heat_pump=True)
    hp = get_source_heat_pump_hourly(s)
    assert hp['gas_allocation'] is None
    assert hp['summary']['metadata']['reference_gas_allocation'] is None
    assert hp['summary']['by_weather'][0]['annual_requested_space_heat_kwh'] == pytest.approx(960)
    source,_,diagnostics = e.load_profiles(s)
    assert source.sum() == pytest.approx(1200+hp['total_electricity_kwh'].sum())
    forecast=e._forecast_mapped_series(s)
    fc=forecast['mapped_hp_breakdown']
    assert fc['by_weather'][0]['annual_requested_space_heat_kwh'] == pytest.approx(960)
    assert forecast['mapped_load'].sum() == pytest.approx(1200+fc['total_electricity_kwh'].sum())
    assert e.evaluate(s)['finance']['thermal_finance']['baseline_cost_eur'] == 120


def test_unserved_carrier_credit_is_withheld(monkeypatch):
    from feasibility.heatpump_finance import calculate_thermal_finance
    bind(monkeypatch,end_use('biomass'))
    s=settings(planning_heat_pump=True)
    facts=calculate_thermal_finance(s, {'by_weather':[dict(annual_requested_space_heat_kwh=480.,
        annual_unmet_space_heat_kwh=480.)]})
    assert facts['allowance_by_weather'] == [100.]
    assert facts['avoided_fuel_credit_by_weather'] == [0.]
    assert not facts['service_comparable']
    assert facts['baseline_gas_cost_eur'] is None


def test_real_cashflows_keep_non_gas_fields_not_applicable(monkeypatch):
    bind(monkeypatch, end_use('district'))
    s=settings(cashflow_mode='real')
    result=e.evaluate(s)
    assert result['annual_cashflows'][1]['display_avoided_gas_credit'] is None
    assert result['annual_cashflows'][1]['display_actual_remaining_gas_bill'] is None


def test_hp_flag_without_changed_thermal_use_preserves_embedded_load(monkeypatch):
    ctx=bind(monkeypatch,end_use('electricity',action='unchanged'))
    s=settings(planning_heat_pump=True)
    np.testing.assert_array_equal(e.load_profiles(s)[0],ctx.dataset['load_civil'])
    assert not e._uses_heat_pump(s)
    assert e.evaluate(s)['finance']['conversion_capex_eur'] == 0


def test_multiple_years_and_leap_replacement_are_conserved(monkeypatch):
    bind(monkeypatch,end_use('electricity',artifact_id='a'*32),separated=True,years=(2023,2024))
    s=settings()
    source,_,_=e.load_profiles(s)
    # Existing AC calibration conserves the ensemble mean. February's
    # 28/29 degree-day split is retained, with exact separated subtraction.
    expected = [1080 + 11*7.5 + 7.5*2*days/57 for days in (28,29)]
    np.testing.assert_allclose(source.sum(axis=1),expected,rtol=1e-12,atol=1e-9)
    assert source.sum(axis=1).mean() == pytest.approx(1170,rel=1e-12,abs=1e-9)
    forecast=e._forecast_mapped_series(s)
    np.testing.assert_allclose(forecast['mapped_load'].sum(axis=1),[1170,1170],rtol=1e-12,atol=1e-9)


def test_nonembedded_electrical_bill_is_distinct_from_metered_household(monkeypatch):
    from feasibility.heatpump_finance import calculate_thermal_finance
    bind(monkeypatch,end_use('electricity',included_in_electric_load=False))
    facts=calculate_thermal_finance(settings())
    assert facts['baseline_cost_eur'] == 120
    assert facts['remaining_cost_eur'] == 20
    assert facts['baseline_gas_cost_eur'] is None


def test_explicit_no_heat_reference_leaves_load_unchanged(monkeypatch):
    use=end_use('none',measured_input=None,monthly_input=None,efficiency=None,
        conversion_kwh_per_unit=None,annual_fixed_cost=0.,annual_variable_cost=0.,action='unchanged')
    ctx=bind(monkeypatch,use)
    s=settings(planning_heat_pump=True)
    np.testing.assert_array_equal(e.load_profiles(s)[0],ctx.dataset['load_civil'])
    assert e.evaluate(s)['finance']['thermal_finance']['baseline_cost_eur']==0


def test_unserved_embedded_electricity_cannot_create_package_savings(monkeypatch):
    from feasibility.heatpump_finance import calculate_thermal_finance
    bind(monkeypatch,end_use('electricity',artifact_id='a'*32),separated=True)
    s=settings(planning_heat_pump=True)
    facts=calculate_thermal_finance(s,{'by_weather':[dict(annual_requested_space_heat_kwh=360.,
        annual_unmet_space_heat_kwh=360.)]})
    expected=e.electric_bill(1200,s)-e.electric_bill(1080,s)
    assert facts['baseline_cost_eur']==0
    assert facts['allowance_by_weather']==pytest.approx([expected])
    assert facts['embedded_electric_service_allowance_by_weather']==pytest.approx([expected])
    assert facts['avoided_fuel_credit_by_weather']==pytest.approx([0])
    assert not facts['service_comparable']


@pytest.mark.parametrize('fault',['shape','negative','infinite','padding','unknown','disconnect'])
def test_invalid_separated_runtime_inputs_rejected(fault):
    ctx=thermal_context(end_use('electricity',artifact_id='a'*32),separated=True)
    metadata=runtime.mutable_copy(ctx.metadata)
    entry=metadata['thermal_inputs']['separated_electricity']['space_heat']
    a=np.array(entry['civil']);entry['civil']=a
    if fault=='shape':entry['civil']=a[:,:-1]
    elif fault=='negative':a[0,0]=-1
    elif fault=='infinite':a[0,0]=float('inf')
    elif fault=='padding':a[0,-1]=1
    elif fault=='unknown':metadata['thermal_inputs']['unrecognized']=True
    else:metadata['thermal_inputs']['disconnect_end_uses']=['unknown']
    with pytest.raises(ValueError):replace(ctx,metadata=metadata)


def test_different_period_separated_replacement_rejected():
    use=end_use('electricity',artifact_id='a'*32,period_relation='different',
        coverage=dict(start='2023-01-01',end='2024-01-01',complete=True))
    with pytest.raises(ValueError,match='measurement period'):
        thermal_context(use,separated=True)


def test_financial_allowance_follows_dispatched_forecast_calendar(monkeypatch):
    from feasibility.heatpump_finance import calculate_thermal_finance
    bind(monkeypatch,end_use('biomass',measured_input=None,unit=None,monthly_input=None,
        monthly_useful_kwh=[2000.]*12),years=(2024,))
    s=settings(planning_heat_pump=True,hp_nominal_kw=.5,hp_backup_kw=0.,hp_radiator_dt50_kw=.5)
    result=e.evaluate(s)
    fc=e._forecast_mapped_series(s)['mapped_hp_breakdown']
    expected=calculate_thermal_finance(s,{'by_weather':fc['by_weather']})
    assert expected['allowance_by_weather'][0] > 0
    assert result['finance']['thermal_finance']['allowance_by_weather']==pytest.approx(expected['allowance_by_weather'],rel=1e-12,abs=1e-9)
    assert not result['finance']['service_comparable']


def test_ev_replacement_is_not_an_incremental_planned_ev(monkeypatch):
    load=dict(id='ev',included_in_electric_load=True,action='replace',artifact_id='a'*32,
              incremental_annual_kwh=100.)
    bind(monkeypatch,electric_loads=[load])
    s=settings();s['load_scenario']='ev'
    with pytest.raises(ValueError,match='incremental planned additions only'):
        e.load_profiles(s)
    s['load_scenario']='baseline'
    assert e.load_profiles(s)[0].sum()==pytest.approx(1200)


def test_runtime_reference_comparison_preserves_strict_numeric_types():
    ctx=thermal_context(end_use('gas',annual_variable_cost=1.))
    ref=ctx.thermal_reference
    altered=replace(ref,end_uses={'space_heat':replace(ref.end_uses['space_heat'],annual_variable_cost=True)})
    with pytest.raises(ValueError,match='match the pinned'):
        replace(ctx,thermal_reference=altered)
