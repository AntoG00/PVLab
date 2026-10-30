"""Direct v2 simulations using synthetic compiled roof and load artifacts."""
from copy import deepcopy
from dataclasses import replace
import json
import numpy as np
import pytest
from feasibility import engine as e, runtime_context as runtime
from feasibility.assumptions import SPEC
from feasibility.dataset_compile import compile_dataset
from feasibility.profiles import canonical_json
from feasibility.thermal_baseline import resolve_thermal_baseline
from tests.test_dataset_compile import inputs


def context(n=2, order=None, diffuse_fraction=0):
    profile, solar, load = inputs(n, years=(2023,))
    if n == 0:
        profile['roofs'] = []
    if order is not None:
        profile['roofs'] = [profile['roofs'][i] for i in order]
        solar = replace(solar, plane_ids=tuple(solar.plane_ids[i] for i in order),
                        power=solar.power[list(order)], beam_loss=solar.beam_loss[list(order)],
                        diffuse_loss=solar.diffuse_loss[list(order)])
    solar = replace(solar, diffuse_loss=solar.power * diffuse_fraction)
    from tests.test_fiscal_policy import configure_fiscal
    configure_fiscal(profile)
    c = compile_dataset(profile, solar, load)
    dataset = {**c.arrays, 'audit': json.loads(c.audit_bytes)}
    defaults = {k: deepcopy(v[0]) for k, v in SPEC.items()}
    defaults.update(pmg_eligible_confirmed=True, battery_kwh=0, battery_modules=0, horizon_years=1,
                    export_price_profile='flat_benchmark', routing_priority='battery_first')
    return runtime.RuntimeContext(owner_id='synthetic', input_revision=c.digest,
        dataset_digest=c.digest, price_revision='synthetic-flat', defaults_snapshot=defaults,
        thermal_reference=resolve_thermal_baseline(profile),
        fiscal_revision=profile['fiscal']['rule_revision'], calendar_version='utc_hourly_v2',
        dataset=dataset, weather_identity=('synthetic', c.digest, c.digest),
        records_snapshot=(), metadata={'dataset_manifest': json.loads(c.manifest_bytes),
                                      'compiler_version': 'dataset-compiler-v2'},
        household_profile=profile)


def bind(monkeypatch, n=2, order=None):
    ctx = context(n, order)
    monkeypatch.setattr(runtime, '_bound', ctx)
    return ctx


@pytest.mark.parametrize('n', [0, 1, 2, 7, 12])
def test_direct_evaluation_and_labels(monkeypatch, n):
    ctx = bind(monkeypatch, n)
    counts = {f'plane{i}': 2 for i in range(n)}
    result = e.evaluate({'roof_panels': counts})
    assert result['layout']['counts'] == counts
    assert result['layout']['panel_count'] == n * 2
    assert result['layout']['kwp'] == pytest.approx(n * .8)
    assert result['layout']['roofs'] == [dict(id=f'plane{i}', label=f'Plane {i}',
                                             max_kwp=5, panels=2) for i in range(n)]
    assert result['layout']['theoretical'] is False
    assert result['solar_audit']['roof_shading_fraction_applied_again'] is False
    assert result['energy']['pv_kwh']['median'] >= 0
    if not n:
        assert result['energy']['pv_kwh']['median'] == 0
        assert result['finance']['capex_eur'] == 0
    assert ctx.dataset['power'].shape[0] == n


def test_scaling_loss_and_cache_keys(monkeypatch):
    bind(monkeypatch)
    s = e.validate({'roof_panels': {'plane0': 2}})
    p = e.pv_profile(s)
    expected = .8 * (e.data()['power'][0] - e.data()['beam_loss'][0])
    np.testing.assert_array_equal(p, expected)
    bigger = e.validate({'roof_panels': {'plane0': 4}})
    watts = e.validate({'roof_panels': {'plane0': 2}, 'panel_watts': 800})
    np.testing.assert_array_equal(e.pv_profile(bigger), 2*p)
    np.testing.assert_array_equal(e.pv_profile(watts), 2*p)
    assert e.evaluate(bigger)['energy']['pv_kwh']['median'] == 2*e.evaluate(s)['energy']['pv_kwh']['median']
    assert e._energy_key(s) != e._energy_key(bigger)
    assert e._energy_key(s, e.CHART_KEYS) != e._energy_key(bigger, e.CHART_KEYS)
    restored = e.thaw_settings(dict(e._energy_key(s)))
    assert restored['roof_panels'] == s['roof_panels']
    no_loss = e.validate({'roof_panels': {'plane0': 2}, 'shade_strength': 0})
    np.testing.assert_array_equal(e.pv_profile(no_loss), .8*e.data()['power'][0])


def test_manifest_permutation(monkeypatch):
    bind(monkeypatch, 7)
    settings = {'roof_panels': {f'plane{i}': i+1 for i in range(7)}}
    first = e.pv_profile(e.validate(settings)).copy()
    bind(monkeypatch, 7, list(reversed(range(7))))
    second = e.pv_profile(e.validate(settings))
    np.testing.assert_allclose(second, first, rtol=1e-15, atol=1e-15)


def test_zero_counts_and_canonical_empty_mapping(monkeypatch):
    bind(monkeypatch)
    s = e.validate({'roof_panels': {}})
    assert s['roof_panels'] == {'plane0': 0, 'plane1': 0}
    assert e.thaw_settings({'roof_panels': ()})['roof_panels'] == {}
    assert not e.pv_profile(s).any()
    assert e.validate(s) == s
    with pytest.raises(ValueError, match='Battery without PV'):
        e.validate({'roof_panels': {}, 'battery_kwh': 5})


@pytest.mark.parametrize('bad', [{'unknown': 0}, {'plane0': True}, {'plane0': 1.0},
                                 {'plane0': -1}, {'plane0': 13}, None, []])
def test_invalid_counts(monkeypatch, bad):
    bind(monkeypatch)
    with pytest.raises(ValueError):
        e.validate({'roof_panels': bad})


@pytest.mark.parametrize('key', sorted(e.LEGACY_ROOF_KEYS))
def test_mixed_legacy_controls_rejected(monkeypatch, key):
    bind(monkeypatch)
    with pytest.raises(ValueError, match='Legacy roof controls'):
        e.validate({'roof_panels': {'plane0': 1}, key: False if key.endswith('proxy') else 0})


def test_quote_and_capex_totals(monkeypatch):
    bind(monkeypatch)
    settings = {'roof_panels': {'plane0': 2, 'plane1': 3}}
    s = e.validate(settings)
    cost = e.installed_capex(s, 2.)
    assert e.detailed_installed_capex_breakdown(s, 2.)['installed_capex_eur'] == pytest.approx(cost, abs=.01)
    quote = e.quote_evaluate(settings, {'line_items': [{'net_eur': 1000, 'vat': .1}]})
    assert quote['result']['layout']['panel_count'] == 5
    assert quote['result']['finance']['capex_eur'] == 1100
    assert quote['itemized_total_eur'] == 1100
    assert set(quote['result']['layout']['counts']) == {'plane0', 'plane1'}


def test_standalone_battery_sizing_guard(monkeypatch):
    bind(monkeypatch)
    with pytest.raises(ValueError, match='Standalone battery sizing is unavailable'):
        e.battery_sizing({'roof_panels': {'plane0': 1}})


def test_runtime_profile_binding(monkeypatch):
    ctx = bind(monkeypatch)
    bad = runtime.mutable_copy(ctx.household_profile)
    bad['roofs'][0]['max_kwp'] = 4
    with pytest.raises(ValueError, match='pinned v2 dataset manifest'):
        replace(ctx, household_profile=bad)
    with pytest.raises(TypeError):
        ctx.household_profile['roofs'][0]['label'] = 'Changed'


def test_unbound_scalar_validation_remains_pure(monkeypatch):
    monkeypatch.setattr(runtime, '_bound', None)
    base = {k: deepcopy(v[0]) for k, v in SPEC.items()}
    assert e.validate({'battery_kwh': 0}, base=base)['south_panels'] == base['south_panels']


def test_diffuse_sensitivity_applied_once(monkeypatch):
    monkeypatch.setattr(runtime, '_bound', context(diffuse_fraction=.05))
    s = e.validate({'roof_panels': {'plane0': 2}, 'shade_strength': .5, 'diffuse_suppression': .4})
    d = e.data()
    expected = .8 * (d['power'][0] - .5 * (d['beam_loss'][0] + .4*d['diffuse_loss'][0]))
    np.testing.assert_array_equal(e.pv_profile(s), expected)


def test_runtime_axes_and_unbound_v2_rejected(monkeypatch):
    ctx = context()
    bad = {**runtime.mutable_copy(ctx.dataset), 'power': ctx.dataset['power'][:1]}
    with pytest.raises(ValueError, match='roof axes'):
        replace(ctx, dataset=bad)
    with pytest.raises(ValueError, match='pinned household profile'):
        replace(ctx, household_profile=None)
    # A legacy mapping cannot enable v2 evaluation without the pinned profile.
    monkeypatch.setattr(runtime, '_bound', replace(ctx, household_profile=None, thermal_reference={}))
    with pytest.raises(ValueError, match='bound household profile'):
        e.evaluate({'roof_panels': {}})


def test_dispatch_matrix_schedule_uses_dynamic_panel_counts(monkeypatch):
    ctx = bind(monkeypatch, 2)
    settings={'roof_panels':{'plane0':2,'plane1':3},'pv_dispatch_model':'physical',
              'pv_battery_coupling':'dc_coupled','battery_kwh':2.,'battery_modules':1}
    result=e.dispatch_matrix(settings)
    assert result['schedule_optimizer']['rows']
    assert result['annual_flows']['pv_generation_kwh'] > 0
    assert result['weather_count'] == len(ctx.dataset['years'])
    assert e._panel_total(e.validate(settings)) == 5
