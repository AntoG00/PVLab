"""Heat pump financial integration, conversion capex, and fuel credit accounting.

Stage 5 financial helpers for optional air-to-water heat pump modeling.
Handles:
  1. Conversion capex combinations (hp_capex, hp_cooking_capex, gas_disconnect_cost)
  2. Annual heat pump maintenance and replacement cash flows
  3. Gas bill accounting for retained vs disconnected connections
  4. Fuel credit withholding for unserved space heating and DHW (unserved service allowance)
  5. Service comparability reporting and audit envelopes
"""
from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np


def is_active_hp(settings: dict[str, Any]) -> bool:
    """Return True if heat pump planning is active on an applicable load scenario."""
    return bool(settings.get('planning_heat_pump', False)) and (settings.get('load_scenario') != 'baseline')


def calculate_hp_conversion_capex(settings: dict[str, Any]) -> float:
    """Calculate conversion capex for active heat pump scenario.
    
    Formula:
      hp_capex (shared outdoor unit + DHW cylinder + installation ONCE)
      + hp_cooking_capex (ONLY if hp_electric_cooking is True)
      + gas_disconnect_cost (ONLY if hp_disconnect_gas is True)
      
    Nominal 9 kW capacity is NOT a separate equipment cost (included in hp_capex).
    Legacy electrification_capex is strictly excluded / ignored.
    """
    hp_capex = float(settings.get('hp_capex', 10000.0))
    cooking_capex = float(settings.get('hp_cooking_capex', 500.0)) if settings.get('hp_electric_cooking', False) else 0.0
    disconnect_cost = float(settings.get('gas_disconnect_cost', 150.0)) if settings.get('hp_disconnect_gas', False) else 0.0
    return float(hp_capex + cooking_capex + disconnect_cost)


def calculate_conversion_capex(settings: dict[str, Any]) -> float:
    """Determine conversion capex for any scenario, preserving legacy behavior when inactive."""
    if is_active_hp(settings):
        return calculate_hp_conversion_capex(settings)
    if settings.get('load_scenario') != 'baseline':
        return float(settings.get('electrification_capex', 6000.0) + settings.get('gas_disconnect_cost', 150.0))
    elif settings.get('electrification_capex_include_baseline', False):
        return float(settings.get('electrification_capex', 6000.0))
    return 0.0


def calculate_hp_maintenance(settings: dict[str, Any], year: int = 1) -> float:
    """Annual heat pump maintenance and servicing allowance inflated to operating year."""
    infl = (1.0 + float(settings.get('inflation', 0.02))) ** (year - 1)
    return float(settings.get('hp_annual_maintenance', 100.0)) * infl


def calculate_hp_replacement(settings: dict[str, Any], year: int = 1) -> float:
    """Heat pump equipment replacement cost inflated to replacement year (0 if not replacement year)."""
    rep_year = int(settings.get('hp_replacement_year', 0))
    if rep_year > 0 and year == rep_year:
        infl = (1.0 + float(settings.get('inflation', 0.02))) ** (year - 1)
        return float(settings.get('hp_replacement_cost', 6000.0)) * infl
    return 0.0


def gas_variable_cost(volume: float, settings: dict[str, Any], year: int = 1) -> float:
    """Price gas volume (Smc) using statutory progressive variable tiers, matching baseline gas_bill.
    
    Fixed charges are excluded.
    Uses pure pricing kernel calculate_gas_cost() with all editable tax rates and settings.
    """
    vol = float(volume)
    if vol <= 0.0:
        return 0.0
    from .fiscal import gas_cost as calculate_gas_cost
    res = calculate_gas_cost(
        volume=vol,
        days=365,
        year=year,
        prior_year_smc=0.0,
        include_fixed=False,
        settings=settings,
    )
    return float(res['variable_total_eur'])


def calculate_hp_remaining_gas_bill(settings: dict[str, Any], year: int = 1) -> float:
    """Calculate remaining gas bill for heat pump household without modifying global gas_bill(0).
    
    Rules:
      1. If hp_disconnect_gas is True:
         Service is permanently disconnected. Actual gas bill is 0.0 EUR.
      2. If hp_disconnect_gas is False (retained connection):
         - If hp_electric_cooking is False:
           Household burns allocated cooking gas (e.g. 25 Smc). Bill includes variable tiers + fixed charges.
         - If hp_electric_cooking is True:
           Household volume is 0 Smc, but connection is retained. Retains annual fixed gas charges
           with editable gas_vat_standard.
    """
    from .engine import _thermal_reference
    if _thermal_reference() is not None:
        return calculate_thermal_finance(settings, year=year)['remaining_gas_cost_eur']
    if settings.get('hp_disconnect_gas', False):
        return 0.0

    cooking_smc = 0.0 if settings.get('hp_electric_cooking', False) else float(settings.get('cooking_smc_annual', 25.0))
    from .fiscal import gas_cost as calculate_gas_cost
    res = calculate_gas_cost(
        volume=cooking_smc,
        days=365,
        year=year,
        prior_year_smc=0.0,
        include_fixed=True,
        settings=settings,
    )
    return float(res['total_eur'])


def calculate_thermal_finance(settings, hp_summary=None, year=1, n_weather=1):
    """Actual carrier bills, without pricing embedded electricity a second time.

    Explicit fixed/variable costs and unreconstructed direct annual bills come
    from the thermal reference. A direct bill has no inferred variable share:
    savings require explicit disconnection. Gas fiscal adapters remain unchanged
    for callers choosing tariff-based Smc diagnostics.
    """
    from . import engine
    from .runtime_context import current_runtime
    ref = engine._thermal_reference()
    if ref is None:
        raise ValueError('Carrier-neutral finance requires a bound ThermalReference')
    infl = (1 + settings['inflation']) ** (year-1)
    disconnected = current_runtime().metadata.get('thermal_inputs', {}).get('disconnect_end_uses', ())
    gas_uses = [u for u in ref.end_uses.values() if u.carrier == 'gas']
    from .fiscal import current_policy
    fiscal = current_policy()
    fiscal.settings(settings,year)
    detailed_gas = bool(gas_uses) and not fiscal.legacy_mode and fiscal.parameters['mode'] == 'parameters'
    gas_fractions = {}
    removed_gas = bool(gas_uses) and all(engine._thermal_action(settings,u.id) == 'replace' for u in gas_uses)
    gas_disconnect = removed_gas and bool(settings.get('hp_disconnect_gas', False))
    if settings.get('hp_disconnect_gas') and gas_uses and not removed_gas and settings['load_scenario'] != 'baseline':
        raise ValueError('Gas disconnection requires removal of every gas end use')
    if hp_summary is None and engine._uses_heat_pump(settings):
        from .heatpump_integration import get_source_heat_pump_hourly
        hp_summary = get_source_heat_pump_hourly(settings)['summary']
    rows = (hp_summary or {}).get('by_weather', [])
    baseline = remaining = 0.
    baseline_gas = remaining_gas = 0.
    allowance = np.zeros(n_weather)
    gas_allowance = np.zeros(n_weather)
    embedded_allowance = np.zeros(n_weather)
    comparable = np.ones(n_weather, dtype=bool)
    by_carrier = {}
    for key,use in ref.end_uses.items():
        mode = engine._thermal_action(settings,key)
        fractions = np.zeros(n_weather)
        prefix = 'space' if key == 'space_heat' else 'draw' if key == 'dhw' else None
        if mode != 'unchanged' and prefix is not None and engine._uses_heat_pump(settings):
            for wi in range(n_weather):
                row = rows[wi] if wi < len(rows) else (rows[0] if rows else {})
                requested_key = 'annual_requested_space_heat_kwh' if prefix == 'space' else 'annual_requested_draw_heat_kwh'
                unmet_key = 'annual_unmet_space_heat_kwh' if prefix == 'space' else 'annual_unmet_draw_heat_kwh'
                requested = float(row.get(requested_key, 0.))
                unmet = float(row.get(unmet_key, 0.))
                fractions[wi] = min(1., max(0., unmet/requested)) if requested > 0 else 0.
                comparable[wi] &= unmet <= 1e-3
        if use.carrier == 'none':
            continue
        if use.carrier == 'electricity' and use.included_in_electric_load:
            # Removal already lowers the metered bill. Withhold that implicit
            # saving for unserved heat, without adding a second baseline bill.
            if mode == 'replace' and prefix is not None:
                separated = engine._thermal_existing(settings)
                if key not in separated:
                    raise ValueError('Replacement requires separated existing series')
                old = separated[key].sum(axis=1)
                baseline_kwh = engine.data()['audit']['annual_kwh'] * settings['load_growth']
                for wi,fraction in enumerate(fractions):
                    removed = float(old[wi % len(old)]) * fraction
                    if removed > baseline_kwh:
                        raise ValueError('Separated electrical input exceeds gross baseline')
                    value = float(engine.electric_bill(baseline_kwh,settings,year,settings['baseline_power_kw']) -
                        engine.electric_bill(baseline_kwh-removed,settings,year,settings['baseline_power_kw']))
                    allowance[wi] += value
                    embedded_allowance[wi] += value
            continue
        if detailed_gas and use.carrier == 'gas':
            gas_fractions[key] = fractions
            continue
        remove = mode == 'replace'
        disconnect = gas_disconnect if use.carrier == 'gas' else (remove and key in disconnected)
        fixed = use.annual_fixed_cost * infl
        variable = use.annual_variable_cost * infl
        direct = use.annual_bill_eur
        if direct is not None:
            cost = direct * infl
            retained = 0. if disconnect else cost
            avoidable = cost if disconnect else 0.
        else:
            cost = fixed + variable
            retained = (0. if disconnect else fixed) + (0. if remove else variable)
            avoidable = variable if remove else 0.
        baseline += cost
        remaining += retained
        if use.carrier == 'gas':
            baseline_gas += cost
            remaining_gas += retained
        carrier = by_carrier.setdefault(use.carrier, {'baseline_eur': 0., 'remaining_eur': 0.})
        carrier['baseline_eur'] += cost
        carrier['remaining_eur'] += retained
        for wi,fraction in enumerate(fractions):
            amount = avoidable * fraction
            allowance[wi] += amount
            if use.carrier == 'gas':
                gas_allowance[wi] += amount
    if detailed_gas:
        volume = ref.annual_gas_smc
        remaining_volume = sum(sum(u.monthly_input) for u in gas_uses
                               if engine._thermal_action(settings,u.id) != 'replace')
        before = fiscal.thermal_bill({'volume_smc':volume},settings,year)
        after = fiscal.thermal_bill({'volume_smc':remaining_volume,'include_fixed':not gas_disconnect},settings,year)
        baseline_gas, remaining_gas = before['total_eur'], after['total_eur']
        baseline += baseline_gas
        remaining += remaining_gas
        by_carrier['gas'] = dict(baseline_eur=baseline_gas,remaining_eur=remaining_gas)
        for wi in range(n_weather):
            unserved = sum(sum(u.monthly_input)*gas_fractions[u.id][wi] for u in gas_uses
                           if engine._thermal_action(settings,u.id) == 'replace')
            served = fiscal.thermal_bill({'volume_smc':max(remaining_volume,volume-unserved)},settings,year)
            amount = max(0.,before['variable_total_eur']-served['variable_total_eur'])
            allowance[wi] += amount
            gas_allowance[wi] += amount
    return dict(baseline_cost_eur=float(baseline), remaining_cost_eur=float(remaining),
        baseline_gas_cost_eur=float(baseline_gas) if gas_uses else None,
        remaining_gas_cost_eur=float(remaining_gas) if gas_uses else None,
        allowance_by_weather=allowance.tolist(),
        avoided_fuel_credit_by_weather=(baseline-remaining-allowance+embedded_allowance).tolist(),
        embedded_electric_service_allowance_by_weather=embedded_allowance.tolist(),
        avoided_gas_credit_by_weather=(baseline_gas-remaining_gas-gas_allowance).tolist() if gas_uses else None,
        service_comparable=bool(np.all(comparable)), by_carrier=by_carrier,
        gas_disconnected=gas_disconnect,
        cost_basis='Explicit thermal bills; embedded electrical energy is already in the household electricity bill',
        direct_bill_policy='No variable share inferred; direct-bill savings require explicit disconnection')


def calculate_hp_gas_finance(
    settings: dict[str, Any],
    hp_summary: dict[str, Any] | None = None,
    year: int = 1,
    tolerance: float = 1e-3,
    n_weather: int | None = None,
) -> dict[str, Any]:
    """Calculate structured gas financial metrics and unserved service withholding.
    
    Allocates explicitly bound natural gas into heating, DHW, and cooking end-uses.
    For fuel credits:
      Withholds variable avoided gas value corresponding to the unmet fraction of each
      historical gas allocation:
        withheld_heating_smc = heating_ref_smc * (unmet_space / requested_space)
        withheld_dhw_smc = dhw_ref_smc * (unmet_dhw / requested_draw)
        fractions bounded in [0.0, 1.0]; zero-request case explicitly yields 0.0.
      Withheld service is priced at BASELINE avoided-marginal tiers:
        allowance = variable_cost(baseline_volume) - variable_cost(baseline_volume - withheld_volume)
        clipped to valid range [0, baseline_volume], rather than pricing withheld volume from 0.
      This ensures:
        - Exact 0.0 EUR gas credit when all heating/DHW is unmet and gas cooking is retained,
          eliminating phantom savings from progressive tier differences.
        - In fully converted or disconnected cases, only cooking and/or fixed charge savings survive.
        - Partial withholding is strictly monotonic with respect to unserved volume,
          with no negative heat credit, preserving actual remaining gas bill and fixed charges.
        - Variable tiers and tax assumptions match baseline gas_bill identically.
      Deducted as 'unserved_service_allowance_eur' from whole package benefits ONLY.
      
    Reports:
      actual_remaining_gas_cost, avoided_gas_credit, unserved_service_allowance,
      gas_volumes, service_comparable, by-weather bounds.
    """
    from feasibility.engine import gas_bill, envelope, _thermal_reference
    from feasibility.runtime_context import current_runtime
    if _thermal_reference() is not None:
        from .engine import _uses_heat_pump
        if hp_summary is None and _uses_heat_pump(settings):
            from .heatpump_integration import get_source_heat_pump_hourly
            hp_summary = get_source_heat_pump_hourly(settings)['summary']
        count = n_weather if n_weather is not None else len((hp_summary or {}).get('by_weather', [])) or 1
        facts = calculate_thermal_finance(settings, hp_summary, year, count)
        gas = facts['avoided_gas_credit_by_weather']
        return dict(actual_remaining_gas_cost=facts['remaining_gas_cost_eur'],
                    actual_remaining_gas_cost_eur=facts['remaining_gas_cost_eur'],
                    avoided_gas_credit=None if gas is None else envelope(gas),
                    avoided_gas_credit_eur=None if gas is None else envelope(gas),
                    avoided_gas_credit_by_weather=gas, gas_volumes=None,
                    allowance_by_weather=facts['allowance_by_weather'],
                    unserved_service_allowance_eur=envelope(facts['allowance_by_weather']),
                    service_comparable=facts['service_comparable'], thermal_finance=facts)
    monthly_gas = current_runtime().thermal_reference['GAS_MONTHLY']

    summer_base = float(settings.get('summer_gas_base', 25.0))
    cooking_annual = float(settings.get('cooking_smc_annual', 25.0))
    summer = np.minimum(monthly_gas, summer_base)
    summer_sum = float(summer.sum())
    cooking = (summer / summer_sum * cooking_annual) if summer_sum > 0 else np.zeros_like(summer)
    dhw = summer - cooking
    heating = monthly_gas - summer

    heating_ref_smc = float(heating.sum())
    dhw_ref_smc = float(dhw.sum())
    cooking_ref_smc = float(cooking.sum())
    baseline_smc = float(monthly_gas.sum())

    if hp_summary is None:
        from feasibility.heatpump_integration import get_source_heat_pump_hourly
        hp_summary = get_source_heat_pump_hourly(settings)['summary']

    by_weather = hp_summary.get('by_weather', []) if isinstance(hp_summary, dict) else []
    if n_weather is not None:
        w_count = n_weather
    elif by_weather:
        w_count = len(by_weather)
    else:
        w_count = 1

    withheld_smc_list: list[float] = []
    allowance_list: list[float] = []
    comparable_list: list[bool] = []
    heat_frac_list: list[float] = []
    dhw_frac_list: list[float] = []

    baseline_var_cost = gas_variable_cost(baseline_smc, settings, year=year)

    for w in range(w_count):
        bw = by_weather[w] if (by_weather and w < len(by_weather)) else (by_weather[0] if by_weather else {})
        space_req = float(bw.get('annual_requested_space_heat_kwh', 0.0))
        space_unmet = max(0.0, float(bw.get('annual_unmet_space_heat_kwh', 0.0)))
        dhw_req = float(bw.get('annual_requested_draw_heat_kwh', 0.0))
        dhw_unmet = max(0.0, float(bw.get('annual_unmet_draw_heat_kwh', 0.0)))

        h_frac = min(1.0, max(0.0, space_unmet / space_req)) if space_req > 0.0 else 0.0
        d_frac = min(1.0, max(0.0, dhw_unmet / dhw_req)) if dhw_req > 0.0 else 0.0

        withheld_h = heating_ref_smc * h_frac
        withheld_d = dhw_ref_smc * d_frac
        tot_withheld = withheld_h + withheld_d
        tot_withheld_clipped = min(baseline_smc, max(0.0, tot_withheld))

        # Price withheld service at baseline avoided-marginal tiers:
        # allowance = variable_cost(baseline_volume) - variable_cost(baseline_volume - withheld_volume)
        rem_volume = max(0.0, baseline_smc - tot_withheld_clipped)
        allowance = max(0.0, baseline_var_cost - gas_variable_cost(rem_volume, settings, year=year))

        withheld_smc_list.append(tot_withheld_clipped)
        allowance_list.append(allowance)
        comparable_list.append(bool(space_unmet <= tolerance and dhw_unmet <= tolerance))
        heat_frac_list.append(h_frac)
        dhw_frac_list.append(d_frac)

    actual_rem_gas = calculate_hp_remaining_gas_bill(settings, year=year)
    baseline_gas = float(gas_bill(settings, year=year))
    remaining_smc = 0.0 if settings.get('hp_electric_cooking', False) else cooking_ref_smc

    gross_avoided_gas = baseline_gas - actual_rem_gas
    avoided_credit_arr = np.array([max(0.0, gross_avoided_gas - allw) for allw in allowance_list], dtype=float)
    withheld_arr = np.array(withheld_smc_list, dtype=float)
    allowance_arr = np.array(allowance_list, dtype=float)
    avoided_smc_arr = np.array([max(0.0, (baseline_smc - remaining_smc) - w_smc) for w_smc in withheld_smc_list], dtype=float)

    is_overall_comparable = bool(all(comparable_list)) if comparable_list else True

    return {
        'actual_remaining_gas_cost': float(actual_rem_gas),
        'actual_remaining_gas_cost_eur': float(actual_rem_gas),
        'avoided_gas_credit': envelope(avoided_credit_arr),
        'avoided_gas_credit_eur': envelope(avoided_credit_arr),
        'avoided_gas_credit_by_weather': avoided_credit_arr.tolist(),
        'allowance': envelope(allowance_arr),
        'unserved_service_allowance': envelope(allowance_arr),
        'unserved_service_allowance_eur': envelope(allowance_arr),
        'allowance_by_weather': allowance_arr.tolist(),
        'gas_volumes': {
            'baseline_smc': float(baseline_smc),
            'remaining_smc': float(remaining_smc),
            'withheld_smc': envelope(withheld_arr),
            'withheld_smc_by_weather': withheld_arr.tolist(),
            'avoided_smc': envelope(avoided_smc_arr),
            'avoided_smc_by_weather': avoided_smc_arr.tolist(),
            'heating_reference_smc': float(heating_ref_smc),
            'dhw_reference_smc': float(dhw_ref_smc),
            'cooking_reference_smc': float(cooking_ref_smc),
        },
        'service_comparable': is_overall_comparable,
        'service_comparable_by_weather': comparable_list,
        'heating_unmet_fraction_by_weather': heat_frac_list,
        'dhw_unmet_fraction_by_weather': dhw_frac_list,
    }
