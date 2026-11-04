"""Italian compatibility arithmetic, not a claim of current legal coverage.

New policies require explicit, confirmed, content-addressed rule data. Existing
rates are neither updated nor inferred from a location or wholesale zone.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
import math
import numpy as np
from .fiscal import POLICY_VERSION, FISCAL_RATES, ELECTRIC_RATES, EXPORT_RATES, GAS_RATES, CONFIRMATIONS


@dataclass(frozen=True, init=False)
class ItalyFiscalPolicy:
    parameters: Mapping
    gas_region: str | None
    legacy_mode: bool

    def __init__(self, parameters, gas_region=None, *, has_gas=False, _legacy=False):
        p = dict(parameters)
        if not isinstance(p.get('sources', ()), (list,tuple)):
            raise ValueError('Fiscal sources must be a sequence')
        if not _legacy:
            allowed = set(FISCAL_RATES) | {'policy_version','confirmed','mode','annual_bill_eur',
                'effective_from','effective_to','as_of','sources'}
            if set(p) - allowed:
                raise ValueError('Unsupported fiscal fields: ' + ', '.join(sorted(set(p)-allowed)))
            if p.get('policy_version') != POLICY_VERSION:
                raise ValueError('Unsupported fiscal policy version')
            if p.get('confirmed') is not True or p.get('mode') not in ('parameters','annual_bill'):
                raise ValueError('Confirmed explicit fiscal mode required')
            for key in ELECTRIC_RATES + EXPORT_RATES:
                if key not in p:
                    raise ValueError('Explicit fiscal rate required: ' + key)
            for key in FISCAL_RATES:
                if key in p and (type(p[key]) not in (int,float) or not math.isfinite(p[key]) or p[key] < 0):
                    raise ValueError('Invalid fiscal rate: ' + key)
            if sum(p[k] for k in ('export_irpef','regional_additional_tax','municipal_additional_tax')) >= 1:
                raise ValueError('Combined export tax must be below 100%')
            for key in FISCAL_RATES:
                if key in p and key not in ('pmg_price','gse_fee_eur_kw_year') and p[key] > 1:
                    raise ValueError('Invalid fiscal fraction: ' + key)
            try:
                start, end, asof = (date.fromisoformat(p[k]) for k in ('effective_from','effective_to','as_of'))
            except (KeyError, TypeError, ValueError):
                raise ValueError('Explicit fiscal effective dates and as_of required') from None
            if start >= end or not p.get('sources') or not all(type(x) is str and x.strip() for x in p['sources']):
                raise ValueError('Fiscal coverage and sources required')
            p['sources'] = tuple(p['sources'])
            if has_gas and p['mode'] == 'parameters' and (not gas_region or any(k not in p for k in GAS_RATES)):
                raise ValueError('Explicit gas region and all slab/VAT rates required')
            if p['mode'] == 'annual_bill' and (type(p.get('annual_bill_eur')) not in (int,float) or
                    not math.isfinite(p['annual_bill_eur']) or p['annual_bill_eur'] < 0):
                raise ValueError('Explicit annual bill required')
        object.__setattr__(self,'parameters',MappingProxyType(p))
        object.__setattr__(self,'gas_region',gas_region)
        object.__setattr__(self,'legacy_mode',_legacy)

    @classmethod
    def legacy(cls):
        return cls({}, 'legacy-private', _legacy=True)

    def settings(self, settings, year=1):
        if self.legacy_mode:
            return settings
        s = dict(settings)
        for key in (*CONFIRMATIONS, 'pmg_election', 'export_fee_tax_deductible'):
            if key in s and type(s[key]) is not bool:
                raise ValueError(key + ' must be boolean')
        calendar_year = s.get('installation_year')
        if type(year) not in (int,float) or not math.isfinite(year) or year < 1 or int(year) != year:
            raise ValueError('Fiscal project year must be a positive integer')
        if type(calendar_year) not in (int,float) or int(calendar_year) != calendar_year:
            raise ValueError('Explicit installation_year required')
        actual = int(calendar_year + year)
        start, end = (date.fromisoformat(self.parameters[k]) for k in ('effective_from','effective_to'))
        if (date(actual,1,1) < start or date(actual,12,31) >= end) and s.get('unsupported_fiscal_year_acknowledged') is not True:
            raise ValueError('Unsupported fiscal year requires acknowledgement or updated rules')
        if s.get('pmg_election') and s.get('pmg_eligible_confirmed') is not True:
            raise ValueError('PMG eligibility confirmation required')
        if self.parameters['deduction_rate'] > 0 and s.get('deduction_eligible_confirmed') is not True:
            raise ValueError('Investment deduction eligibility confirmation required')
        for key in FISCAL_RATES:
            if key in self.parameters:
                if key in s and s[key] != self.parameters[key]:
                    raise ValueError('Scenario fiscal rate differs from pinned rule: ' + key)
                s[key] = self.parameters[key]
        return s

    def electric_bill(self, consumption, settings, year=1, power=None):
        s=self.settings(settings,year)
        if power is None:power=s['baseline_power_kw']
        commodity=(1+s['inflation'])**(year-1)*(1+s['energy_real_escalation'])**(year-1)
        regulated=(1+s['inflation'])**(year-1)*(1+s['regulated_real_escalation'])**(year-1)
        variable=(s['retail_commodity']*commodity+(s['retail_regulated']+s['electric_excise'])*regulated)*(1+s['electric_vat'])
        fixed=12*(s['supplier_fixed_month']+s['network_fixed_month']+power*s['power_fixed_kw_month'])*regulated*(1+s['electric_vat'])
        return consumption*variable+fixed

    def gas_breakdown(self, volume, settings, year=1, **kwargs):
        s=self.settings(settings,year)
        if not self.legacy_mode:
            if self.parameters['mode'] != 'parameters':
                raise ValueError('Annual bill approximation has no gas tax breakdown')
            if not self.gas_region or any(k not in self.parameters for k in GAS_RATES):
                raise ValueError('Explicit gas region and all slab/VAT rates required')
        from .gas_taxes import calculate_gas_cost
        return calculate_gas_cost(volume=volume, settings=s, year=year, **kwargs)

    def thermal_bill(self, thermal_baseline, settings, year=1):
        s=self.settings(settings,year)
        if hasattr(thermal_baseline, 'end_uses'):
            gas = [u for u in thermal_baseline.end_uses.values() if u.carrier == 'gas']
            non_gas = sum(u.annual_bill_eur if u.annual_bill_eur is not None else
                u.annual_fixed_cost + u.annual_variable_cost
                for u in thermal_baseline.end_uses.values() if u.carrier not in ('gas','none')
                and not u.included_in_electric_load)
            gas_cost = 0.
            if gas:
                if not self.legacy_mode and self.parameters['mode'] == 'annual_bill':
                    gas_cost = self.parameters['annual_bill_eur']*(1+s['inflation'])**(year-1)
                else:
                    gas_cost = self.gas_breakdown(thermal_baseline.annual_gas_smc,s,year)['total_eur']
            return dict(total_eur=non_gas*(1+s['inflation'])**(year-1)+gas_cost,
                        gas_total_eur=gas_cost if gas else None)
        if not self.legacy_mode and self.parameters['mode'] == 'annual_bill':
            return dict(total_eur=self.parameters['annual_bill_eur']*(1+s['inflation'])**(year-1),
                        tax_breakdown_applicable=False, cost_basis='annual_bill_approximation')
        return self.gas_breakdown(thermal_baseline['volume_smc'], s, year,
            **{k:v for k,v in thermal_baseline.items() if k != 'volume_smc'})

    def export_settlement(self, exports, proceeds, settings, year=1, kwp=0):
        s=self.settings(settings,year)
        yy=year-1;infl=(1+s['inflation'])**yy
        minimum=exports*s['pmg_price']*(1+s['pmg_nominal_escalation'])**yy
        gross=np.maximum(proceeds,minimum) if s['pmg_election'] else proceeds
        fees=(0 if kwp<=3 else min(s['gse_fee_eur_kw_year']*kwp,10000))*(1+s['gse_fee_vat'])*infl
        tax=s['export_irpef']+s['regional_additional_tax']+s['municipal_additional_tax']
        export_tax=np.maximum(gross-(fees if s['export_fee_tax_deductible'] else 0),0)*tax
        return dict(gross=gross,fees=fees,tax=export_tax,net=gross-export_tax-fees)

    def deduction_rates(self):
        if self.legacy_mode or 'deduction_rate' not in self.parameters:
            return (0, 0.36, 0.5)
        return (self.parameters['deduction_rate'],)

    def investment_terms(self, capex, settings):
        s=self.settings(settings)
        eligible=min(capex*s['deduction_eligible_fraction'],96000)
        deduction=min(eligible*s['deduction_rate']/10,s['deduction_tax_capacity'])
        return eligible,deduction

    def investment_relief(self, capex, settings, year=1):
        s=self.settings(settings,year)
        _,deduction=self.investment_terms(capex,s)
        return deduction if s['deduction_start_year']<=year<s['deduction_start_year']+10 else 0.

    def application_provenance(self, settings, horizon):
        result=self.provenance()
        result['scenario_confirmations']={k:settings.get(k,False) for k in CONFIRMATIONS}
        start,end=(date.fromisoformat(self.parameters[k]) for k in ('effective_from','effective_to'))
        years=[int(settings['installation_year'])+year for year in range(1,horizon+1)]
        unsupported=[y for y in years if date(y,1,1)<start or date(y,12,31)>=end]
        result.update(operating_years=years,unsupported_years=unsupported,
                      unsupported_rule_projection=bool(unsupported))
        return result

    def provenance(self):
        if self.legacy_mode:
            return dict(version='legacy-fiscal-v1', legacy=True, current_legal_review_verified=False)
        return dict(version=POLICY_VERSION, legacy=False, gas_region=self.gas_region,
            parameters=dict(self.parameters), approximate=self.parameters['mode']=='annual_bill',
            current_legal_review_verified=False,
            constants=dict(gse_exempt_kwp=3,gse_fee_cap_eur=10000,investment_cap_eur=96000,installments=10),
            limitations=['Explicit user-confirmed rules; current source/legal review pending',
                         'Out-of-coverage projection requires scenario acknowledgement'])
