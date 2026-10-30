"""Versioned, inert household input contracts. Importing this module reads no data.

Drafts are valid storage objects, never implicit runnable household defaults.
The compiled-manifest readiness contract is metadata-only; future compilers must
validate their arrays before emitting it. This module does not activate data.
"""
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import date
import hashlib
import json
import math
import re
from types import MappingProxyType

SCHEMA_VERSION = 1
PARSER_VERSION = 'household-json-v1'
COMPILER_VERSION = 'profile-manifest-v1'
MODEL_VERSION = 'legacy-unbound-v1'
ZONES = ('NORD', 'CNOR', 'CSUD', 'SUD', 'CALA', 'SICI', 'SARD')


def object_fields(value, required, optional=()):
    if type(value) is not dict:
        raise ValueError('Expected JSON object type')
    if any(type(key) is not str for key in value):
        raise ValueError('JSON object keys must be strings')
    unknown = value.keys() - set(required) - set(optional)
    missing = set(required) - value.keys()
    if unknown:
        raise ValueError('Unknown fields: ' + ', '.join(sorted(unknown)))
    if missing:
        raise ValueError('Missing fields: ' + ', '.join(sorted(missing)))
    return value


def number(value, name, low=0, high=None, *, positive=False, integer=False):
    if type(value) not in (int, float) or (integer and type(value) is not int):
        raise ValueError(name + ': expected numeric type, not bool')
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(name + ': must be finite')
    if value < low or (positive and value == low) or (high is not None and value > high):
        raise ValueError(name + ': outside allowed range')
    return value


def boolean(value, name):
    if type(value) is not bool:
        raise ValueError(name + ': expected bool')
    return value


def choice(value, choices, name):
    if type(value) is not str or value not in choices:
        raise ValueError(name + ': unsupported value')
    return value


def identifier(value, name='id', *, digest=False, opaque=False, nullable=False):
    if value is None and nullable:
        return None
    pattern = r'[0-9a-f]{64}' if digest else r'[0-9a-f]{32}' if opaque else r'[A-Za-z][A-Za-z0-9_-]{0,63}'
    if type(value) is not str or re.fullmatch(pattern, value) is None:
        raise ValueError(name + ': invalid identifier type or format')
    return value


def iso_date(value, name, nullable=False):
    if value is None and nullable:
        return None
    if type(value) is not str:
        raise ValueError(name + ': invalid date type')
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError()
    except ValueError:
        raise ValueError(name + ': invalid ISO date') from None
    return value


def sequence(value, name, maximum, minimum=0):
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise ValueError(f'{name}: at least {minimum}, max {maximum} entries required')
    return value


def _plain(value):
    if is_dataclass(value) and not isinstance(value, type):
        result = {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
        # Omit absent additive thermal fields so existing canonical profiles and
        # immutable revision digests retain their original representation.
        if isinstance(value, EndUse):
            for key in ('monthly_input', 'conversion_kwh_per_unit', 'coverage',
                        'period_relation', 'annual_bill_eur'):
                if result[key] is None:
                    del result[key]
        elif isinstance(value, Thermal):
            if result['gas_allocation'] is None:
                del result['gas_allocation']
            if not result['electric_loads']:
                del result['electric_loads']
        return result
    if isinstance(value, Mapping):
        if any(type(k) is not str for k in value):
            raise ValueError('JSON keys must be strings')
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    if value is None or type(value) in (str, bool, int, float):
        return value
    raise ValueError('Not a JSON value')


def canonical_json(value):
    """Canonical UTF-8, sorted object keys, ordered arrays, finite JSON only."""
    try:
        return json.dumps(_plain(value), sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (TypeError, UnicodeError, OverflowError) as exc:
        raise ValueError('Invalid canonical JSON') from exc


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('Non-finite JSON constant: ' + value)
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, AttributeError) as exc:
        raise ValueError('Invalid UTF-8 JSON') from exc


@dataclass(frozen=True)
class Location:
    latitude: float
    longitude: float


@dataclass(frozen=True)
class Coverage:
    start: str | None
    end: str | None
    complete: bool


@dataclass(frozen=True)
class Consumption:
    artifact_id: str | None
    kind: str
    coverage: Coverage
    projection_policy: str
    acknowledged: bool


@dataclass(frozen=True)
class Roof:
    id: str
    label: str
    tilt_deg: float
    azimuth_deg: float
    max_kwp: float
    shading_fraction: float


@dataclass(frozen=True)
class EndUse:
    id: str
    carrier: str
    measured_input: float | None
    unit: str | None
    monthly_useful_kwh: tuple | None
    efficiency: float | None
    cop: float | None
    annual_fixed_cost: float
    annual_variable_cost: float
    included_in_electric_load: bool
    action: str
    artifact_id: str | None
    monthly_input: tuple | None = None
    conversion_kwh_per_unit: float | None = None
    coverage: Coverage | None = None
    period_relation: str | None = None
    annual_bill_eur: float | None = None


@dataclass(frozen=True)
class ElectricLoad:
    id: str
    included_in_electric_load: bool
    action: str
    artifact_id: str | None
    incremental_annual_kwh: float | None


@dataclass(frozen=True)
class Thermal:
    end_uses: tuple[EndUse, ...]
    gas_allocation: Mapping | None = None
    electric_loads: tuple[ElectricLoad, ...] = ()


@dataclass(frozen=True)
class Market:
    zone: str
    series: str
    window_start: str
    window_end: str
    valuation_mode: str
    price_revision: str | None


@dataclass(frozen=True)
class Fiscal:
    country: str
    rule_revision: str | None
    gas_region: str | None
    parameters: Mapping


@dataclass(frozen=True)
class HouseholdProfileV1:
    schema_version: int
    id: str
    country: str
    timezone: str
    location: Location
    consumption: Consumption
    roofs: tuple[Roof, ...]
    thermal: Thermal
    market: Market
    fiscal: Fiscal
    default_settings: Mapping


def profile_to_dict(profile):
    return _plain(profile)


def _end_use(value):
    optional = ('monthly_input', 'conversion_kwh_per_unit', 'coverage',
                'period_relation', 'annual_bill_eur')
    object_fields(value, [f.name for f in fields(EndUse) if f.name not in optional], optional)
    v = dict(value)
    choice(v['id'], ('space_heat', 'dhw', 'cooking'), 'thermal.id')
    choice(v['carrier'], ('gas', 'electricity', 'biomass', 'district', 'none'), 'carrier')
    choice(v['action'], ('unchanged', 'add', 'replace'), 'action')
    boolean(v['included_in_electric_load'], 'included_in_electric_load')
    identifier(v['artifact_id'], 'thermal.artifact_id', opaque=True, nullable=True)
    if v['unit'] is not None:
        choice(v['unit'], ('kwh', 'smc', 'kg'), 'unit')
    if (v['measured_input'] is None) != (v['unit'] is None):
        raise ValueError('measured_input and unit must be supplied together')
    for key in ('measured_input', 'efficiency', 'cop', 'annual_fixed_cost', 'annual_variable_cost'):
        if v[key] is not None or key.startswith('annual_'):
            number(v[key], key, positive=key in ('efficiency', 'cop'))
    if v['efficiency'] is not None:
        number(v['efficiency'], 'efficiency', high=1)
    if v['monthly_useful_kwh'] is not None:
        months = sequence(v['monthly_useful_kwh'], 'monthly_useful_kwh', 12, 12)
        v['monthly_useful_kwh'] = tuple(number(x, 'monthly_useful_kwh') for x in months)
        if v['measured_input'] is not None:
            raise ValueError('Choose measured_input OR monthly_useful_kwh')
    if v.get('monthly_input') is not None:
        v['monthly_input'] = tuple(number(x, 'monthly_input') for x in
                                   sequence(v['monthly_input'], 'monthly_input', 12, 12))
        if v['measured_input'] is None or v['monthly_useful_kwh'] is not None:
            raise ValueError('monthly_input requires measured_input only')
        if not math.isclose(math.fsum(v['monthly_input']), v['measured_input'], rel_tol=1e-12, abs_tol=1e-9):
            raise ValueError('monthly_input must conserve measured_input')
    if v.get('conversion_kwh_per_unit') is not None:
        number(v['conversion_kwh_per_unit'], 'conversion_kwh_per_unit', positive=True)
    if v.get('annual_bill_eur') is not None:
        number(v['annual_bill_eur'], 'annual_bill_eur')
    if v.get('period_relation') is not None:
        choice(v['period_relation'], ('same', 'different'), 'period_relation')
    if v.get('coverage') is not None:
        c = object_fields(v['coverage'], ('start', 'end', 'complete'))
        start, end = (iso_date(c[k], 'thermal.coverage.' + k) for k in ('start', 'end'))
        if start >= end:
            raise ValueError('Invalid thermal coverage window')
        v['coverage'] = Coverage(start, end, boolean(c['complete'], 'complete'))
    if v['carrier'] != 'electricity' and v['included_in_electric_load']:
        raise ValueError('Only electricity can be included_in_electric_load')
    if v['carrier'] == 'none':
        if any(v.get(k) is not None for k in ('measured_input', 'monthly_useful_kwh', 'monthly_input',
                                             'efficiency', 'cop', 'conversion_kwh_per_unit')):
            raise ValueError('Carrier none cannot have thermal input')
    elif v['unit'] is not None:
        expected = {'gas': 'smc', 'biomass': 'kg', 'district': 'kwh', 'electricity': 'kwh'}
        if v['unit'] != expected[v['carrier']]:
            raise ValueError('Carrier/unit mismatch')
    if v['carrier'] != 'electricity' and v['cop'] is not None:
        raise ValueError('COP requires electricity carrier')
    if v['carrier'] == 'electricity' and v['efficiency'] is not None:
        raise ValueError('Electricity requires COP, not efficiency')
    return EndUse(**v)


def _parameters(value, *, fiscal=False):
    # Explicit allowlists; absent settings are never replaced by personal defaults.
    from .fiscal import FISCAL_RATES
    numeric = ({'pcs_kwh_smc', 'gas_vat', 'annual_bill_eur', *FISCAL_RATES}
               if fiscal else {'panel_watts', 'inverter_kw', 'battery_kwh', 'retail_commodity',
                               'retail_regulated', 'export_market_price', 'discount_rate',
                               'horizon_years', 'baseline_cooling_kwh', 'summer_ac_multiplier'})
    flags = {'confirmed'} if fiscal else set()
    enums = {'mode': ('parameters', 'annual_bill')} if fiscal else {'retail_contract': ('fixed',)}
    text = {'policy_version', 'effective_from', 'effective_to', 'as_of'} if fiscal else set()
    special = {'sources'} if fiscal else set()
    object_fields(value, (), numeric | flags | enums.keys() | text | special)
    for key, val in value.items():
        if key in numeric:
            number(val, key, integer=key == 'horizon_years',
                   positive=key in ('pcs_kwh_smc', 'panel_watts', 'horizon_years'))
        elif key in text:
            if type(val) is not str or not val.strip():
                raise ValueError(key + ' must be a nonempty string')
        elif key in special:
            if type(val) is not list or not val or any(type(x) is not str or not x.strip() for x in val):
                raise ValueError('sources must contain nonempty source strings')
        elif key in flags:
            boolean(val, key)
        else:
            choice(val, enums[key], key)
    return MappingProxyType(dict(value))


def parse_profile(payload: dict) -> HouseholdProfileV1:
    object_fields(payload, [f.name for f in fields(HouseholdProfileV1)])
    v = dict(payload)
    if type(v['schema_version']) is not int or v['schema_version'] != 1:
        raise ValueError('schema_version: unsupported version or bool type')
    identifier(v['id'], opaque=True)
    choice(v['country'], ('IT',), 'country')
    choice(v['timezone'], ('Europe/Rome',), 'timezone')
    loc = object_fields(v['location'], ('latitude', 'longitude'))
    v['location'] = Location(number(loc['latitude'], 'latitude', -90, 90),
                             number(loc['longitude'], 'longitude', -180, 180))
    c = dict(object_fields(v['consumption'], [f.name for f in fields(Consumption)]))
    identifier(c['artifact_id'], 'consumption.artifact_id', opaque=True, nullable=True)
    choice(c['kind'], ('interval', 'monthly', 'annual'), 'consumption.kind')
    choice(c['projection_policy'], ('none', 'civil_month_hour', 'standard_month_hour',
                                   'synthetic_v1'), 'projection_policy')
    boolean(c['acknowledged'], 'acknowledged')
    cov = object_fields(c['coverage'], ('start', 'end', 'complete'))
    start, end = (iso_date(cov[k], 'coverage.' + k, True) for k in ('start', 'end'))
    complete = boolean(cov['complete'], 'coverage.complete')
    if (start is None) != (end is None) or (start is not None and start >= end):
        raise ValueError('Invalid coverage window')
    if complete and start is None:
        raise ValueError('Complete coverage requires dates')
    c['coverage'] = Coverage(start, end, complete)
    v['consumption'] = Consumption(**c)
    roofs = []
    for r in sequence(v['roofs'], 'roofs', 12):
        object_fields(r, [f.name for f in fields(Roof)])
        identifier(r['id'], 'roof.id')
        if type(r['label']) is not str or not 1 <= len(r['label']) <= 120 or any(ord(x) < 32 for x in r['label']):
            raise ValueError('roof.label: invalid text')
        number(r['tilt_deg'], 'tilt_deg', high=90)
        number(r['azimuth_deg'], 'azimuth_deg', high=360)
        if r['azimuth_deg'] == 360:
            raise ValueError('azimuth_deg must be less than 360')
        number(r['max_kwp'], 'max_kwp', positive=True)
        number(r['shading_fraction'], 'shading_fraction', high=1)
        roofs.append(Roof(**r))
    if len({r.id for r in roofs}) != len(roofs):
        raise ValueError('Duplicate roof identifiers')
    v['roofs'] = tuple(roofs)
    t = object_fields(v['thermal'], ('end_uses',), ('gas_allocation', 'electric_loads'))
    uses = tuple(_end_use(e) for e in sequence(t['end_uses'], 'end_uses', 3))
    if len({e.id for e in uses}) != len(uses):
        raise ValueError('Duplicate thermal end uses')
    allocation = t.get('gas_allocation')
    if allocation is not None:
        object_fields(allocation, ('monthly_gas_smc', 'summer_gas_base', 'cooking_smc_annual'))
        allocation = dict(allocation)
        allocation['monthly_gas_smc'] = tuple(number(x, 'monthly_gas_smc') for x in
            sequence(allocation['monthly_gas_smc'], 'monthly_gas_smc', 12, 12))
        for key in ('summer_gas_base', 'cooking_smc_annual'):
            number(allocation[key], key)
        allocation = MappingProxyType(allocation)
    electric = []
    for item in sequence(t.get('electric_loads', []), 'electric_loads', 2):
        object_fields(item, [f.name for f in fields(ElectricLoad)])
        choice(item['id'], ('ev', 'cooling'), 'electric load id')
        boolean(item['included_in_electric_load'], 'included_in_electric_load')
        choice(item['action'], ('unchanged', 'add', 'replace'), 'action')
        identifier(item['artifact_id'], 'electric artifact_id', opaque=True, nullable=True)
        if item['incremental_annual_kwh'] is not None:
            number(item['incremental_annual_kwh'], 'incremental_annual_kwh')
        electric.append(ElectricLoad(**item))
    if len({e.id for e in electric}) != len(electric):
        raise ValueError('Duplicate electric end uses')
    v['thermal'] = Thermal(uses, allocation, tuple(electric))
    m = object_fields(v['market'], [f.name for f in fields(Market)])
    choice(m['zone'], ZONES, 'market.zone')
    choice(m['series'], (*ZONES, 'PUN'), 'market.series')
    if m['series'] not in (m['zone'], 'PUN'):
        raise ValueError('market.series must match zone or explicitly select PUN comparison')
    choice(m['valuation_mode'], ('shape_scaled', 'absolute_proxy', 'flat'), 'valuation_mode')
    start = iso_date(m['window_start'], 'window_start')
    end = iso_date(m['window_end'], 'window_end')
    if start >= end:
        raise ValueError('Invalid market window')
    identifier(m['price_revision'], 'price_revision', digest=True, nullable=True)
    v['market'] = Market(**m)
    f = dict(object_fields(v['fiscal'], [x.name for x in fields(Fiscal)]))
    choice(f['country'], ('IT',), 'fiscal.country')
    identifier(f['rule_revision'], 'rule_revision', digest=True, nullable=True)
    identifier(f['gas_region'], 'gas_region', nullable=True)
    f['parameters'] = _parameters(f['parameters'], fiscal=True)
    v['fiscal'] = Fiscal(**f)
    v['default_settings'] = _parameters(v['default_settings'])
    profile = HouseholdProfileV1(**v)
    canonical_json(profile)  # Also reject invalid Unicode before accepting a draft.
    return profile


def validate_ready(profile, artifacts):
    """Validate a compiler's pinned manifest against verified artifact bytes.

    artifacts maps opaque artifact IDs to immutable bytes. A dataset_manifest
    references each component by ID, kind and SHA-256; it also attests validated
    full-year coverage and shared roof/year/calendar alignment. No file I/O and
    no compiler or numerical activation occurs here.
    """
    profile = parse_profile(profile_to_dict(profile))
    c = profile.consumption
    if c.artifact_id is None or not c.coverage.complete:
        raise ValueError('Draft not ready: missing artifact or complete coverage')
    if c.kind != 'interval' or c.projection_policy == 'synthetic_v1':
        if not c.acknowledged:
            raise ValueError('Draft not ready: synthetic consumption acknowledgement required')
    if profile.fiscal.rule_revision is None or profile.fiscal.parameters.get('confirmed') is not True:
        raise ValueError('Draft not ready: confirmed fiscal revision required')
    from .fiscal import resolve_fiscal
    resolve_fiscal(profile_to_dict(profile)['fiscal'], has_gas=any(e.carrier == 'gas' for e in profile.thermal.end_uses))
    parameters = profile.fiscal.parameters
    if parameters.get('mode') not in ('parameters', 'annual_bill'):
        raise ValueError('Draft not ready: explicit fiscal parameter mode required')
    if parameters['mode'] == 'annual_bill' and 'annual_bill_eur' not in parameters:
        raise ValueError('Draft not ready: annual bill approximation missing')
    if profile.market.price_revision is None and profile.market.valuation_mode != 'flat':
        raise ValueError('Draft not ready: pinned price revision required')
    for key, raw in artifacts.items():
        identifier(key, 'artifact id', opaque=True)
        if type(raw) is not bytes:
            raise ValueError('Artifact content must be bytes')
    manifests = []
    for raw in artifacts.values():
        try:
            obj = strict_json(raw)
        except ValueError:
            continue
        if type(obj) is dict and obj.get('kind') == 'dataset_manifest':
            manifests.append(obj)
    if len(manifests) != 1:
        raise ValueError('Draft not ready: one dataset_manifest required')
    m = object_fields(manifests[0], ('kind', 'schema_version', 'parser_version', 'compiler_version',
                     'calendar_version', 'years', 'roof_ids', 'location', 'coverage', 'components'))
    if type(m['schema_version']) is not int or m['schema_version'] != 1:
        raise ValueError('Invalid dataset manifest version')
    if m['parser_version'] != PARSER_VERSION or m['compiler_version'] != COMPILER_VERSION:
        raise ValueError('Dataset manifest parser/compiler mismatch')
    choice(m['calendar_version'], ('legacy_pvgis_v1', 'utc_hourly_v2'), 'calendar_version')
    years = sequence(m['years'], 'weather years', 30, 1)
    for year in years:
        number(year, 'weather year', 1900, 2100, integer=True)
    if years != sorted(set(years)):
        raise ValueError('Weather years must be ordered and unique')
    if (m['roof_ids'] != [r.id for r in profile.roofs] or
            canonical_json(m['location']) != canonical_json(profile.location)):
        raise ValueError('DATASET_MISMATCH: roof/location alignment')
    if canonical_json(m['coverage']) != canonical_json(c.coverage):
        raise ValueError('DATASET_MISMATCH: coverage alignment')
    components = sequence(m['components'], 'components', 64, 3)
    seen, kinds = set(), set()
    for item in components:
        object_fields(item, ('artifact_id', 'kind', 'sha256'))
        aid = identifier(item['artifact_id'], 'component artifact_id', opaque=True)
        identifier(item['sha256'], 'component sha256', digest=True)
        choice(item['kind'], ('consumption', 'solar', 'weather', 'prices', 'thermal'), 'component kind')
        if aid in seen or aid not in artifacts:
            raise ValueError('Missing or duplicate component artifact')
        seen.add(aid)
        kinds.add(item['kind'])
        if hashlib.sha256(artifacts[aid]).hexdigest() != item['sha256']:
            raise ValueError('Component digest mismatch')
    if not {'consumption', 'solar', 'weather'} <= kinds:
        raise ValueError('Missing consumption/solar/weather component')
    by_id = {x['artifact_id']: x for x in components}
    if c.artifact_id not in by_id or by_id[c.artifact_id]['kind'] != 'consumption':
        raise ValueError('Consumption artifact mismatch')
    if profile.market.price_revision is not None and not any(
            x['kind'] == 'prices' and x['sha256'] == profile.market.price_revision for x in components):
        raise ValueError('Price revision mismatch')
    for use in profile.thermal.end_uses:
        if use.carrier != 'none' and use.measured_input is None and use.monthly_useful_kwh is None:
            raise ValueError('Thermal baseline missing')
        if use.included_in_electric_load and use.action == 'replace' and use.artifact_id is None:
            raise ValueError('Replacement requires separated electrical artifact')
        if use.artifact_id is not None and (use.artifact_id not in by_id or by_id[use.artifact_id]['kind'] != 'thermal'):
            raise ValueError('Thermal artifact mismatch')
    return None
