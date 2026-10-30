from feasibility import engine


def test_dynamic_roof_sizing_acceptance(monkeypatch):
    monkeypatch.setattr(
        engine,
        "_dynamic_roofs",
        lambda *args, **kwargs: ({"id": "r1", "max_kwp": 0.4}, {"id": "r2", "max_kwp": 0.8}),
        raising=False,
    )
    monkeypatch.setattr(
        engine,
        "validate",
        lambda s, *args, **kwargs: dict(s) if isinstance(s, dict) else s,
        raising=False,
    )

    def fake_pareto(rows):
        for r in rows:
            r.setdefault("pareto", True)

    monkeypatch.setattr(engine, "_pareto", fake_pareto, raising=False)

    six_layouts = [
        {"r1": 0, "r2": 0},
        {"r1": 0, "r2": 1},
        {"r1": 0, "r2": 2},
        {"r1": 1, "r2": 0},
        {"r1": 1, "r2": 1},
        {"r1": 1, "r2": 2},
    ]

    nonmonotonic_scores = {
        (0, 0): 10.0,
        (0, 1): 20.0,
        (0, 2): 15.0,
        (1, 0): 30.0,
        (1, 1): 100.0,
        (1, 2): 40.0,
    }

    def objective_fn(layout):
        return nonmonotonic_scores[(layout["r1"], layout["r2"])]

    independent_winner = max(six_layouts, key=objective_fn)
    assert independent_winner == {"r1": 1, "r2": 1}

    visited_layouts = []

    def fake_evaluate(params):
        assert 'r1_panels' not in params and 'r2_panels' not in params
        layout = dict(params['roof_panels'])
        visited_layouts.append(dict(layout))
        score = objective_fn(layout)
        return {
            "layout": {
                "theoretical": False,
                "roof_panels": dict(layout),
            },
            "metrics": {
                "autarky": {"median": score, "mean": score},
                "npv_eur": {"mean": score, "median": score},
                "self_consumption": {"median": score, "mean": score},
                "payback_years": None,
                "discounted_payback_years": None,
                "simple_payback_of_mean_cash_flows": 10.0,
                "discounted_payback_of_mean_cash_flows": 10.0,
            },
            "finance": {
                "capex_eur": 1000.0,
                "pv_incremental": {"npv": score},
                "whole_package": {"npv": score},
                "lcoe": 0.15,
            },
            "settings": dict(params),
        }

    monkeypatch.setattr(engine, "evaluate", fake_evaluate, raising=False)

    settings = {
        "panel_watts": 400,
        "roof_panels": {"r1": 0, "r2": 0},
        "battery_module_kwh": 5.0,
        "battery_secondary_module_kwh": 5.0,
        "battery_mixed_sizes": False,
        "battery_modules": 0,
        "battery_secondary_modules": 0,
        "battery_kwh": 0.0,
        "load_scenario": "baseline",
    }
    search = {
        "panel_maxima": {"r1": 1, "r2": 2},
        "battery_min_modules": 0,
        "battery_max_modules": 0,
    }

    result = engine.optimize_configuration(settings=settings, search=search)

    # Assert all SIX exact layouts visited
    assert len(visited_layouts) == 6
    for layout in six_layouts:
        assert layout in visited_layouts

    # Assert best layout equals independently enumerated winner from a nonmonotonic objective (winner r1=1,r2=1)
    best_candidate = result["best_candidate"]
    best_layout = (
        best_candidate.get("settings", {}).get("roof_panels")
        or best_candidate.get("layout", {}).get("roof_panels")
    )
    assert best_layout == independent_winner
    assert best_layout == {"r1": 1, "r2": 1}

import math
import pytest
from feasibility import engine as e
from feasibility.roof_config import maximum_panel_count, validate_roof_counts
from tests.test_dynamic_roofs import bind


@pytest.mark.parametrize('kwp,watts,expected', [(0,400,0), (.3,100,3),
    (.7999,400,1), (math.nextafter(.8, 0),400,2), (.8-1e-10,400,1)])
def test_whole_panel_boundary(kwp, watts, expected):
    assert maximum_panel_count(kwp, watts) == expected
    assert validate_roof_counts({'roofs':[{'id':'r','max_kwp':kwp}]},
                               {'r':expected}, watts) == (('r', expected),)
    with pytest.raises(ValueError):
        validate_roof_counts({'roofs':[{'id':'r','max_kwp':kwp}]}, {'r':expected+1}, watts)


@pytest.mark.parametrize('entry', ['optimizer','solar','sensitivity'])
@pytest.mark.parametrize('maxima', [{'unknown':0}, {'plane0':13}, {'plane0':True}, {'plane0':-1}])
def test_profile_maxima_rejected(monkeypatch, entry, maxima):
    bind(monkeypatch)
    settings = {'roof_panels': {'plane0':1}}
    with pytest.raises(ValueError):
        if entry == 'optimizer':
            e.optimize_configuration(settings, {'panel_maxima':maxima})
        elif entry == 'solar':
            e.solar_sizing(settings, {'panel_maxima':maxima})
        else:
            e.solar_sizing_sensitivity(settings, {'panel_maxima':maxima})


@pytest.mark.parametrize('n', [0,1,7,12])
def test_solar_canonical_roofs_and_current_quote(monkeypatch, n):
    bind(monkeypatch, n)
    ids = [f'plane{i}' for i in range(n)]
    counts = dict.fromkeys(ids, 0)
    if n: counts[ids[-1]] = 2
    out = e.solar_sizing({'roof_panels':counts, 'quote_total':1000 if n else None},
                         {'panel_maxima':dict.fromkeys(ids,1), 'battery_max_kwh':0,
                          'evaluation_budget':50, 'include_theoretical':False})
    summary = out['search_summary']
    assert list(summary['effective_roof_limits']) == ids
    assert summary['evaluated_count'] <= summary['evaluation_budget']
    comparisons = out['comparisons']
    current = next(c for c in comparisons if c['id']=='current')
    assert current['roof_counts'] == counts
    assert current['settings']['roof_panels'] == counts
    if n:
        quoted = next(c for c in comparisons if c['id']=='current_quoted')
        assert quoted['roof_counts'] == counts
        assert quoted['settings']['roof_panels'] == counts
    if n==12:
        assert summary['best_found'] is True
        assert summary['certified_global_optimum'] is False
    for candidate in comparisons:
        assert set(candidate['roof_counts']) == set(ids)
        assert 'south_panels' not in candidate.get('settings', {})


def test_seven_roof_candidate_identity(monkeypatch):
    bind(monkeypatch, 7)
    roofs = dict.fromkeys([f'plane{i}' for i in range(7)],0)
    a={'panel_count':1,'roof_counts':{**roofs,'plane5':1}}
    b={'panel_count':1,'roof_counts':{**roofs,'plane6':1}}
    assert e._cand_sig(a) != e._cand_sig(b)
    assert e._layout_sig(a) != e._layout_sig(b)
    assert e._compact_candidate(a)['roof_counts'] == a['roof_counts']


def test_seven_roof_sensitivity_retains_ids(monkeypatch):
    bind(monkeypatch, 7)
    ids=[f'plane{i}' for i in range(7)]
    result=e.solar_sizing_sensitivity({'roof_panels':{'plane6':1},
        'pv_fixed_cost':0,'pv_cost_kwp':0,'om_annual':0,
        'insurance_annual':0,'cleaning_annual':0,'deduction_rate':0},
        {'panel_maxima':{r:int(r=='plane6') for r in ids},'battery_max_kwh':0},
        sensitivity_options=dict(export_revenue_delta_pct=20,installed_cost_delta_pct=10,
                                 household_consumption_delta_pct=10,financing_apr_delta_pp=0))
    assert result['status']=='SUCCESS'
    base=result['scenarios']['base']
    assert base['roles']['financial']['roof_counts']['plane6'] == 1
    for role in base['roles'].values():
        if role is not None:
            assert list(role['roof_counts'])==ids


def test_dynamic_sweep_generic_candidates(monkeypatch):
    bind(monkeypatch,1)
    out=e.sweep({'roof_panels':{'plane0':1}})
    assert {c['id'] for c in out['candidates']} <= {'no_pv','selected','max','optimized'}
    assert any(c['id']=='no_pv' for c in out['candidates'])
    for row in out['candidates']:
        assert set(row['layout']['counts']) == {'plane0'}
        assert row['layout']['panel_count'] <= 12


def test_optimizer_twelve_roofs_bounded_and_deterministic(monkeypatch):
    bind(monkeypatch, 12)
    settings={'roof_panels': {'plane11':2}}
    search={'battery_max_modules':0}
    first=e.optimize_configuration(settings, search)
    second=e.optimize_configuration(settings, search)
    assert first==second
    assert first['search']['best_found'] is True
    assert first['search']['evaluated_count'] <= e.OPTIMIZE_EVALUATION_BUDGET
    assert len(first['search']['requested_panel_maxima'])==12
    assert set(first['best_candidate']['settings']['roof_panels'])=={f'plane{i}' for i in range(12)}


def test_dynamic_candidate_rejects_unknown_and_wrong_dimensions(monkeypatch):
    bind(monkeypatch)
    s=e.validate({'roof_panels':{}})
    for counts in ({'unknown':0}, (1,), (1,2,3)):
        with pytest.raises(ValueError):
            e._evaluate_solar_candidate(s, {}, counts, 0)


def test_legacy_search_reference_is_pinned():
    from feasibility.runtime_context import current_runtime
    ref=current_runtime().metadata['sizing_reference']
    assert ref['version']=='synthetic-legacy-sizing-v1'
    assert tuple(ref['roof_ids'])==e.OPTIMIZE_ROOFS
    assert dict(ref['panel_maxima'])==dict(e._legacy_sizing_reference()['panel_maxima'])
    with pytest.raises(TypeError):
        ref['panel_maxima']['south']=0
