import math
from dataclasses import FrozenInstanceError
import pytest
from feasibility.engine import ValuationDescriptor


def test_rejection_inconsistent_units():
    with pytest.raises(ValueError, match="Inconsistent"):
        ValuationDescriptor(mode='flat', unit='EUR')

    with pytest.raises(ValueError, match="Inconsistent"):
        ValuationDescriptor(mode='shape_scaled', unit='EUR')

    with pytest.raises(ValueError, match="Inconsistent"):
        ValuationDescriptor(mode='absolute_proxy', unit='weighted_kWh')

    with pytest.raises(ValueError, match="Invalid valuation mode"):
        ValuationDescriptor(mode='invalid_mode', unit='EUR')

    with pytest.raises(ValueError, match="Invalid valuation unit"):
        ValuationDescriptor(mode='flat', unit='MWh')


def test_frozen_immutability():
    desc = ValuationDescriptor(mode='flat', unit='weighted_kWh')
    with pytest.raises(FrozenInstanceError):
        desc.mode = 'shape_scaled'
    with pytest.raises(FrozenInstanceError):
        desc.unit = 'EUR'


def test_absolute_proxy_exports_and_prices_proceeds():
    # Exports [1, 2] and prices [-0.1, 0.2] yielding expected proceeds 0.3
    desc = ValuationDescriptor(mode='absolute_proxy', unit='EUR')
    exports = [1.0, 2.0]
    prices = [-0.1, 0.2]
    raw_proceeds = sum(e * p for e, p in zip(exports, prices))
    assert math.isclose(raw_proceeds, 0.3, rel_tol=1e-9)

    # EUR ignores anchor and capture
    mult = desc.settlement_multiplier(anchor=0.15, capture=0.85)
    assert mult == 1.0
    settled = desc.settle(raw_proceeds, anchor=0.15, capture=0.85)
    assert math.isclose(settled, 0.3, rel_tol=1e-9)


def test_negative_currency_sums_allowed():
    desc = ValuationDescriptor(mode='absolute_proxy', unit='EUR')
    exports = [2.0, 1.0]
    prices = [-0.2, 0.1]
    raw_proceeds = sum(e * p for e, p in zip(exports, prices))  # -0.4 + 0.1 = -0.3
    assert math.isclose(raw_proceeds, -0.3, rel_tol=1e-9)
    settled = desc.settle(raw_proceeds)
    assert math.isclose(settled, -0.3, rel_tol=1e-9)


def test_settlement_multipliers_and_order():
    # weighted_kWh flat applies anchor * capture * future * inf * esc
    d_flat = ValuationDescriptor(mode='flat', unit='weighted_kWh')
    m_flat = d_flat.settlement_multiplier(anchor=0.10, capture=0.80, future_factor=1.05, inflation_factor=1.02, escalation_factor=1.01)
    assert math.isclose(m_flat, 0.10 * 0.80 * 1.05 * 1.02 * 1.01, rel_tol=1e-9)

    # weighted_kWh shape applies anchor alone: anchor * future * inf * esc
    d_shape = ValuationDescriptor(mode='shape_scaled', unit='weighted_kWh')
    m_shape = d_shape.settlement_multiplier(anchor=0.10, capture=0.80, future_factor=1.05, inflation_factor=1.02, escalation_factor=1.01)
    assert math.isclose(m_shape, 0.10 * 1.05 * 1.02 * 1.01, rel_tol=1e-9)

    # EUR ignores anchor and capture: future * inf * esc
    d_abs = ValuationDescriptor(mode='absolute_proxy', unit='EUR')
    m_abs = d_abs.settlement_multiplier(anchor=0.10, capture=0.80, future_factor=1.05, inflation_factor=1.02, escalation_factor=1.01)
    assert math.isclose(m_abs, 1.05 * 1.02 * 1.01, rel_tol=1e-9)


def test_finite_validation():
    d_flat = ValuationDescriptor(mode='flat', unit='weighted_kWh')
    with pytest.raises(ValueError, match="finite"):
        d_flat.settlement_multiplier(anchor=float('nan'), capture=1.0)
    with pytest.raises(ValueError, match="finite"):
        d_flat.settlement_multiplier(anchor=1.0, capture=float('inf'))
    with pytest.raises(ValueError, match="finite"):
        d_flat.settlement_multiplier(anchor=1.0, capture=1.0, future_factor=float('nan'))


from copy import deepcopy
from dataclasses import replace
import json
import os
import numpy as np
from feasibility import engine as e, runtime_context as runtime
from feasibility.market_catalog import update_catalog
from feasibility.dataset_compile import compile_dataset
from feasibility.assumptions import SPEC
from feasibility.thermal_baseline import resolve_thermal_baseline
from tests.test_dataset_compile import inputs
from tests.test_market_catalog import synthetic_ingest


@pytest.fixture(scope='module')
def catalog_data(tmp_path_factory):
    root = tmp_path_factory.mktemp('profile-market') / 'catalog'
    def prices(stamp, series):
        if series == 'PUN':
            return -50
        if series == 'NORD':
            return -100 if stamp.hour == 0 else 200
        return 300 if stamp.hour < 12 else 50
    ingest = synthetic_ingest('2023-01-01', '2024-01-01', prices)
    result = update_catalog(root, ingest, publish=True)
    return root, result['revision'], ingest


def price_context(catalog_data, *, zone='NORD', series=None, mode='absolute_proxy',
                  start='2023-01-01', end='2024-01-01', revision=None, years=(2023,2024)):
    root, pinned, _ = catalog_data
    profile, solar, load = inputs(1, years=years)
    profile['market'].update(zone=zone, series=series or zone, valuation_mode=mode,
        window_start=start, window_end=end, price_revision=None if mode=='flat' else revision or pinned)
    profile['default_settings']['export_market_price'] = .15
    from tests.test_fiscal_policy import configure_fiscal
    configure_fiscal(profile)
    compiled = compile_dataset(profile, solar, load)
    dataset = {**compiled.arrays, 'audit': json.loads(compiled.audit_bytes)}
    prices = runtime.resolve_profile_prices(profile, root)
    defaults = {k: deepcopy(v[0]) for k,v in SPEC.items()}
    defaults.update(pmg_eligible_confirmed=True, battery_kwh=0, battery_modules=0, horizon_years=1,
                    routing_priority='battery_first', roof_panels={'plane0':2})
    return runtime.RuntimeContext(owner_id='synthetic', input_revision=compiled.digest,
        dataset_digest=compiled.digest, price_revision=profile['market']['price_revision'],
        defaults_snapshot=defaults, thermal_reference=resolve_thermal_baseline(profile),
        fiscal_revision=profile['fiscal']['rule_revision'], calendar_version='utc_hourly_v2',
        dataset=dataset, weather_identity=('synthetic',compiled.digest,compiled.digest),
        records_snapshot=(), metadata={'dataset_manifest':json.loads(compiled.manifest_bytes),
                                      'compiler_version':'dataset-compiler-v2'},
        household_profile=profile, price_data=prices)


def test_catalog_metadata_and_explicit_resolution(catalog_data):
    root, revision, _ = catalog_data
    info = runtime.available_price_catalog(root, revision)
    assert info['revision'] == revision
    assert info['series'] == ['NORD','CNOR','CSUD','SUD','CALA','SICI','SARD','PUN']
    assert info['window'] == ['2023-01-01','2024-01-01']
    profile = inputs()[0]
    profile['market'].update(valuation_mode='shape_scaled', price_revision=revision)
    with pytest.raises(ValueError, match='pinned catalog'):
        runtime.resolve_profile_prices(profile)
    profile['market']['price_revision'] = 'f'*64
    with pytest.raises((ValueError, OSError)):
        runtime.resolve_profile_prices(profile, root)
    profile['market'].update(valuation_mode='flat', price_revision=None)
    assert runtime.resolve_profile_prices(profile)['profile_market']['product'] is None


@pytest.mark.parametrize('mode', ['flat','shape_scaled','absolute_proxy'])
def test_profile_arrays_hand_prices_and_padding(monkeypatch, catalog_data, mode):
    ctx = price_context(catalog_data, mode=mode)
    monkeypatch.setattr(runtime, '_bound', ctx)
    values, source, mean = e.price_profile()
    valid = ctx.dataset['valid']
    if mode == 'flat':
        np.testing.assert_array_equal(values[valid], 1)
    else:
        product = ctx.price_data['profile_market']['product']
        raw = np.asarray(product['matrix_eur_kwh'])[ctx.dataset['month'][valid]-1, ctx.dataset['hour'][valid]]
        if mode == 'absolute_proxy':
            np.testing.assert_array_equal(values[valid], raw)
            assert values[valid].min() == pytest.approx(-.1)
        else:
            for yi in range(2):
                mask = valid[yi]
                expected = np.asarray(product['matrix_eur_kwh'])[ctx.dataset['month'][yi,mask]-1,ctx.dataset['hour'][yi,mask]]
                np.testing.assert_allclose(values[yi,mask], expected/expected.mean(), rtol=1e-14)
                assert values[yi,mask].mean() == pytest.approx(1)
        assert np.all(values[~valid] == 0)
        with pytest.raises(ValueError):
            values[0,0] = 123
        with pytest.raises(ValueError):
            values.setflags(write=True)
    assert ctx.defaults_snapshot['export_market_price'] == .15
    assert e.export_valuation().unit == ('EUR' if mode=='absolute_proxy' else 'weighted_kWh')


@pytest.mark.parametrize('mode,weighted,expected', [('absolute_proxy',.3,.3),('absolute_proxy',-.3,-.3),
    ('flat',3,.15*1.*3),('shape_scaled',2,.15*2)])
def test_finance_settles_units_once(monkeypatch, catalog_data, mode, weighted, expected):
    ctx = price_context(catalog_data, mode=mode)
    monkeypatch.setattr(runtime, '_bound', ctx)
    s = e.validate(dict(horizon_years=3, pmg_election=False, actual_price_future_factor=.5,
        export_price_future_years=1, inflation=.02, energy_real_escalation=.01))
    annual = np.zeros((3,2,len(e.FIELDS)))
    annual[:,:,6] = 3
    annual[:,:,11] = weighted
    finance, cash = e._financial(s,annual,0,calculate_irr=False)
    assert cash[1]['gross_export'] == pytest.approx(expected)
    assert cash[2]['gross_export'] == pytest.approx(expected*.5*1.02*1.01)
    assert cash[3]['gross_export'] == pytest.approx(expected*.5*1.02**2*1.01**2)
    assert cash[1]['export_tax'] == (0 if expected < 0 else pytest.approx(expected*(s['export_irpef']+s['regional_additional_tax']+s['municipal_additional_tax'])))
    if mode == 'absolute_proxy':
        other = {**s,'export_market_price':.30,'export_capture_factor':1.2}
        _, other_cash = e._financial(other,annual,0,calculate_irr=False)
        assert other_cash[1]['gross_export'] == cash[1]['gross_export']
    s['pmg_election'] = True
    _, minimum_cash = e._financial(s,annual,0,calculate_irr=False)
    assert minimum_cash[1]['gross_export'] == pytest.approx(max(expected,3*s['pmg_price']))
    assert finance['export_valuation']['unit'] == e.export_valuation().unit


def test_fixed_flows_and_zone_finance(monkeypatch, catalog_data):
    results=[]
    identities=[]
    for zone in ('NORD','SUD'):
        ctx = price_context(catalog_data, zone=zone)
        monkeypatch.setattr(runtime, '_bound', ctx)
        results.append(e.evaluate({'pmg_election':False}))
        identities.append(e.price_identity())
    for name in e.FIELDS:
        if name != 'export_price_weighted_kwh':
            assert results[0]['energy'][name] == results[1]['energy'][name]
    assert results[0]['annual_cashflows'][1]['gross_export'] != results[1]['annual_cashflows'][1]['gross_export']
    assert identities[0]['fingerprint'] != identities[1]['fingerprint']
    assert e.price_result_stale(identities[0])
    assert not e.price_result_stale(identities[1])
    assert results[1]['finance']['export_valuation']['series'] == 'SUD'
    assert results[1]['export_price_analysis']['unit'] == 'EUR'


def test_nonpositive_shape_rejected_absolute_kept(monkeypatch, catalog_data):
    with pytest.raises(ValueError, match='positive annual mean'):
        price_context(catalog_data, series='PUN',mode='shape_scaled')
    ctx = price_context(catalog_data,series='PUN')
    monkeypatch.setattr(runtime, '_bound', ctx)
    result = e.evaluate({'pmg_election':False})
    assert result['annual_cashflows'][1]['gross_export'] < 0
    assert any('not_zonal_remuneration' in w for w in result['limitations'])
    report = e.empirical_month_hour_data()
    assert report['provenance']['series'] == 'PUN'
    assert report['normalized_shape_12x24'] is None
    assert report['provenance']['price_revision'] == catalog_data[1]


def test_unsupported_retail_and_pinned_controls(monkeypatch,catalog_data):
    ctx = price_context(catalog_data)
    monkeypatch.setattr(runtime,'_bound',ctx)
    for changes in ({'retail_contract':'pun_indexed'},{'export_price_profile':'flat_benchmark'},
                    {'export_price_window':'calendar_2025'}):
        with pytest.raises(ValueError,match='Unsupported|pinned'):
            e.validate(changes)
    assert 'retail_contract' not in e.validate({'retail_contract':'fixed'})
    profile=deepcopy(inputs()[0])
    profile['default_settings']['retail_contract']='pun_indexed'
    from feasibility.profiles import parse_profile
    with pytest.raises(ValueError):
        parse_profile(profile)


def test_window_binding_and_incomplete_coverage(monkeypatch,catalog_data):
    with pytest.raises(ValueError,match='coverage'):
        price_context(catalog_data,start='2023-02-01',end='2023-03-01')
    first = price_context(catalog_data)
    second = price_context(catalog_data,start='2023-01-02',end='2024-01-01')
    monkeypatch.setattr(runtime,'_bound',first)
    identity=e.price_identity()
    monkeypatch.setattr(runtime,'_bound',second)
    assert e.price_result_stale(identity)
    assert e.price_identity()['window_start']=='2023-01-02'
    with pytest.raises(ValueError,match='pinned'):
        e.empirical_month_hour_data('calendar_2025')


def test_revision_refresh_same_mtime_and_corruption(monkeypatch,catalog_data,tmp_path):
    import shutil
    from pathlib import Path
    root,pinned,ingest=catalog_data
    copied=tmp_path/'catalog'
    shutil.copytree(root,copied)
    original=price_context((copied,pinned,ingest))
    monkeypatch.setattr(runtime,'_bound',original)
    identity=e.price_identity()
    old_values=e.price_profile()[0].copy()
    pointer=copied/'current.json'
    before=pointer.stat()
    update=synthetic_ingest('2023-01-01','2023-01-02',400)
    fresh=update_catalog(copied,update,publish=True,replace=True)['revision']
    os.utime(pointer,ns=(before.st_atime_ns,before.st_mtime_ns))
    assert fresh != pinned
    # Operator refresh does not affect an existing worker or a pinned resolver.
    assert e.price_identity()==identity
    np.testing.assert_array_equal(e.price_profile()[0],old_values)
    assert runtime.available_price_catalog(copied)['revision']==fresh
    reloaded=price_context((copied,pinned,ingest))
    assert reloaded.key==original.key
    refreshed=price_context((copied,pinned,ingest),revision=fresh)
    assert refreshed.input_revision != original.input_revision
    monkeypatch.setattr(runtime,'_bound',refreshed)
    assert e.price_result_stale(identity)
    assert not np.array_equal(e.price_profile()[0],old_values)
    manifest=copied/'revisions'/fresh/'manifest.json'
    before=manifest.stat()
    raw=manifest.read_text()
    manifest.write_text(raw.replace('gme-catalog-v1','gme-catalog-v0'))
    os.utime(manifest,ns=(before.st_atime_ns,before.st_mtime_ns))
    with pytest.raises(ValueError,match='hash/schema'):
        price_context((copied,pinned,ingest),revision=fresh)
    # Already resolved immutable inputs remain usable after external corruption.
    assert e.price_identity()['price_revision']==fresh


def test_bound_selection_and_content_identity(monkeypatch,catalog_data):
    from feasibility.runtime_context import _identity_value
    ctx=price_context(catalog_data)
    with pytest.raises(ValueError,match='revision'):
        replace(ctx,price_revision='f'*64)
    with pytest.raises(ValueError,match='revision'):
        replace(ctx,price_data={})
    changed=_identity_value(ctx.price_data)
    changed['profile_market']['selection']['series']='SUD'
    with pytest.raises(ValueError,match='selection'):
        replace(ctx,price_data=changed)
    changed=_identity_value(ctx.price_data)
    changed['profile_market']['product']['matrix_eur_kwh'][0][0]+=1
    altered=replace(ctx,price_data=changed)
    assert altered.key!=ctx.key
    with pytest.raises(ValueError):
        ctx.price_data['profile_market']['product']['matrix_eur_kwh'][0,0]=99
    monkeypatch.setattr(runtime,'_bound',altered)
    with pytest.raises(ValueError,match='runtime key'):
        e._price_cache(ctx.key,1)


def test_auto_routing_scores_use_selected_market(monkeypatch,catalog_data):
    all_scores=[]
    for zone in ('NORD','SUD'):
        ctx=price_context(catalog_data,zone=zone,years=(2023,))
        monkeypatch.setattr(runtime,'_bound',ctx)
        s=e.validate(dict(routing_priority='auto',battery_modules=1,battery_kwh=5,
                          roof_panels={'plane0':8},pmg_election=False))
        resolved,scores=e._resolve_routing_cached(ctx.key,e._resolver_signature(s),e.price_fingerprint(s))
        expected={mode:e.evaluate({**s,'routing_priority':mode})['finance']['solar_equity']['npv']['mean']
                  for mode in e.ROUTING_MODES}
        assert scores==expected
        assert resolved==max(e.ROUTING_MODES,key=expected.get)
        all_scores.append(scores)
    assert all_scores[0]!=all_scores[1]


def test_sizing_recommendation_changes_with_signed_market(monkeypatch,catalog_data):
    winners=[]
    settings=dict(pmg_election=False,pv_fixed_cost=0,pv_cost_kwp=0,om_annual=0,
        insurance_annual=0,cleaning_annual=0,deduction_rate=0,gse_fee_eur_kw_year=0)
    for series in ('NORD','PUN'):
        ctx=price_context(catalog_data,series=series,years=(2023,))
        monkeypatch.setattr(runtime,'_bound',ctx)
        manual=[e.evaluate({**settings,'roof_panels':{'plane0':count}})['metrics']['npv_eur']['mean']
                for count in range(3)]
        result=e.optimize_configuration(settings,dict(objective='npv_eur',panel_maxima={'plane0':2},battery_max_modules=0))
        winner=result['best_candidate']['settings']['roof_panels']['plane0']
        assert winner==max(range(3),key=manual.__getitem__)
        winners.append(winner)
    assert winners==[2,1]


def test_report_scenario_identity_and_signed_units(monkeypatch,catalog_data):
    ctx=price_context(catalog_data,mode='shape_scaled')
    monkeypatch.setattr(runtime,'_bound',ctx)
    identity=e.price_identity()
    assert e.price_result_stale(identity,{'export_market_price':.2})
    ctx=price_context(catalog_data)
    monkeypatch.setattr(runtime,'_bound',ctx)
    result=e.evaluate({'pmg_election':False})
    assert 'export_proceeds_base_eur' in result['energy']
    assert 'export_price_weighted_kwh' not in result['energy']
    assert result['export_price_analysis']['profile_type']=='absolute_proxy'
    assert not any('No observed hourly or seasonal' in w for w in result['limitations'])
