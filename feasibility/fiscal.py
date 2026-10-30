"""Versioned fiscal interface; clients select data, never Python implementations."""
from typing import Protocol
from functools import lru_cache

POLICY_VERSION = 'italy-fiscal-v1'
CONFIRMATIONS = ('pmg_eligible_confirmed', 'deduction_eligible_confirmed',
                 'unsupported_fiscal_year_acknowledged')
ELECTRIC_RATES = ('electric_excise', 'electric_vat')
EXPORT_RATES = ('pmg_price', 'pmg_nominal_escalation', 'gse_fee_eur_kw_year',
                'gse_fee_vat', 'export_irpef', 'regional_additional_tax',
                'municipal_additional_tax', 'deduction_rate')
GAS_RATES = ('gas_excise_0_120', 'gas_excise_120_480', 'gas_excise_480_1560',
             'gas_excise_over_1560', 'gas_regional_0_120', 'gas_regional_over_120',
             'gas_vat_reduced', 'gas_vat_standard')
FISCAL_RATES = ELECTRIC_RATES + EXPORT_RATES + GAS_RATES


class FiscalPolicy(Protocol):
    def electric_bill(self, consumption, settings, year, power): ...
    def thermal_bill(self, thermal_baseline, settings, year): ...
    def export_settlement(self, exports, proceeds, settings, year, kwp): ...
    def investment_relief(self, capex, settings, year): ...
    def provenance(self): ...


def rule_digest(fiscal):
    """Content identity includes region and every explicit parameter/source/date."""
    import hashlib
    from .profiles import canonical_json
    return hashlib.sha256(canonical_json({k: fiscal[k] for k in
        ('country', 'gas_region', 'parameters')})).hexdigest()


def resolve_fiscal(fiscal, revision=None, *, has_gas=False):
    from .fiscal_it import ItalyFiscalPolicy
    if fiscal['country'] != 'IT':
        raise ValueError('Unsupported fiscal country')
    if fiscal['rule_revision'] != rule_digest(fiscal) or (
            revision is not None and revision != fiscal['rule_revision']):
        raise ValueError('Fiscal revision must match explicit rule content and runtime')
    return ItalyFiscalPolicy(fiscal['parameters'], fiscal['gas_region'], has_gas=has_gas)


def current_policy():
    from .runtime_context import runtime_if_initialized
    from .fiscal_it import ItalyFiscalPolicy
    ctx = runtime_if_initialized()
    if ctx is not None and ctx.household_profile is not None:
        return _bound_policy(ctx.key)
    if ctx is not None and ctx.calendar_version != 'legacy_pvgis_v1':
        raise ValueError('Fiscal policy requires a bound household profile for v2')
    # Compatibility is confined to the explicitly bootstrapped legacy path and
    # pure old adapter callers. New household profiles always take the branch above.
    return ItalyFiscalPolicy.legacy()


def gas_cost(volume, days=365, year=1, prior_year_smc=0., include_fixed=True, settings=None):
    """Compatibility adapter with runtime-selected regional policy."""
    return current_policy().gas_breakdown(volume, settings, year, days=days,
        prior_year_smc=prior_year_smc, include_fixed=include_fixed)


@lru_cache(maxsize=8)
def _bound_policy(runtime_key):
    from .runtime_context import assert_runtime_key
    ctx = assert_runtime_key(runtime_key)
    return resolve_fiscal(ctx.household_profile['fiscal'], ctx.fiscal_revision,
        has_gas=any(u.carrier == 'gas' for u in ctx.thermal_reference.end_uses.values()))
