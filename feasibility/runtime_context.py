"""Explicit, immutable household binding for one computation process.

Construction freezes owned inputs and the process model-build identity.
Local bootstrapping performs artifact I/O before binding;
there is deliberately no reset or rebind API.
"""
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from .thermal_baseline import ThermalReference
from functools import lru_cache
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import sys
from threading import Lock
from types import MappingProxyType

import numpy as np


@lru_cache(maxsize=1)
def model_build_digest():
    """Freeze source and numerical/calendar runtime identity once per process."""
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob('*.py')):
        digest.update(path.name.encode() + b'\0' + path.read_bytes())
    runtime = [sys.version, sys.implementation.cache_tag]
    runtime.extend(version(name) for name in ('numpy', 'scipy', 'numba', 'llvmlite'))
    from zoneinfo import TZPATH
    for base in TZPATH:
        path = Path(base) / 'Europe/Rome'
        if path.is_file():
            digest.update(path.read_bytes())
            break
    else:
        from importlib.resources import files
        digest.update(files('tzdata.zoneinfo').joinpath('Europe/Rome').read_bytes())
    digest.update(json.dumps(runtime).encode())
    return digest.hexdigest()


def legacy_thermal_inputs():
    """Read explicit external legacy inputs only at local bootstrap time.

    Migration writes this file from the already sealed private reference. There
    is no personal vector or implicit fallback in production source.
    """
    from .profile_store import ProfileStore
    from .profiles import strict_json
    store = ProfileStore()
    with store._directory((), create=False) as fd:
        raw = store._read(fd, 'legacy-thermal.json', 4096)
    value = strict_json(raw)
    if type(value) is not dict or set(value) != {'GAS_MONTHLY', 'COOLING_BALANCE_TEMP'}:
        raise ValueError('Explicit legacy thermal input required')
    gas = value['GAS_MONTHLY']
    if (not isinstance(gas,list) or len(gas) != 12 or
            any(type(x) not in (int,float) or not np.isfinite(x) or x < 0 for x in gas)):
        raise ValueError('Legacy gas requires twelve finite nonnegative quantities')
    balance = value['COOLING_BALANCE_TEMP']
    if type(balance) not in (int,float) or not np.isfinite(balance):
        raise ValueError('Legacy cooling balance temperature must be finite')
    return freeze({'GAS_MONTHLY': np.asarray(gas, dtype=float), 'COOLING_BALANCE_TEMP': balance})


def _thermal_types():
    # Explicit allowlist, not general dataclass serialization or object coercion.
    from .thermal_baseline import ThermalReference, EndUseReference
    from .profiles import Coverage
    return ThermalReference, EndUseReference, Coverage


def _identity_value(value):
    if type(value) in _thermal_types():
        return {f.name: _identity_value(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {k: _identity_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_identity_value(v) for v in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value


def freeze(value):
    if type(value) in _thermal_types():
        return replace(value, **{f.name: freeze(getattr(value, f.name)) for f in fields(value)})
    if isinstance(value, np.ndarray):
        if value.dtype.hasobject:
            raise TypeError('Runtime arrays cannot contain Python objects')
        order = 'F' if value.flags.f_contiguous and not value.flags.c_contiguous else 'C'
        # An immutable bytes owner also prevents setflags(write=True) and alias writes.
        return np.frombuffer(value.tobytes(order=order), dtype=value.dtype).reshape(value.shape, order=order)
    if isinstance(value, Mapping):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    if value is None or isinstance(value, (str, bool, int, float, np.generic)):
        return value
    raise TypeError(f'Unsupported runtime value: {type(value).__name__}')


def mutable_copy(value):
    """Copy JSON containers for callers; array storage remains immutable."""
    if isinstance(value, Mapping):
        return {k: mutable_copy(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [mutable_copy(v) for v in value]
    return value


def available_price_catalog(catalog_dir, revision=None):
    """Verified administrator metadata; selecting it never refreshes a runtime."""
    from .market_catalog import read_revision
    catalog = read_revision(catalog_dir, revision)
    manifest = catalog['manifest']
    return {'revision': catalog['revision'], 'series': list(manifest['series']),
            'coverage': list(manifest['coverage']), 'window': list(manifest['window']),
            'windows': [list(manifest['window'])]}


def resolve_profile_prices(profile, catalog_dir=None):
    """Resolve a pinned catalog before constructing/initializing a worker.

    Only an explicit flat selection avoids catalog I/O. Caller supplies the
    administrator catalog directory; profile JSON cannot supply arbitrary paths.
    A refresh requires a new profile revision, compiled manifest and worker.
    """
    from .profiles import parse_profile, profile_to_dict
    profile = profile_to_dict(parse_profile(profile_to_dict(profile)))
    selected = dict(profile['market'])
    product = None
    if selected['valuation_mode'] != 'flat':
        if catalog_dir is None or selected['price_revision'] is None:
            raise ValueError('Selected market requires a pinned catalog revision')
        from .market_catalog import read_revision
        from .prepare_market_prices import compute_empirical_window
        catalog = read_revision(catalog_dir, selected['price_revision'])
        product = compute_empirical_window(catalog['days'], selected['window_start'],
            selected['window_end'], series=selected['series'],
            require_climatology=selected['valuation_mode'] == 'shape_scaled')
        if selected['valuation_mode'] == 'shape_scaled' and product['normalized_shape'] is None:
            raise ValueError('Shape-scaled prices require a positive annual mean')
    return {'profile_market': {'selection': selected, 'product': product}}


def _profile_price_binding(profile, price_data, price_revision, dataset):
    """Validate the resolver's retained product and bind all content to identity."""
    from .profiles import canonical_json
    market = profile['market']
    if market['valuation_mode'] == 'flat':
        expected = {'profile_market': {'selection': market, 'product': None}}
        if price_data and canonical_json(_identity_value(price_data)) != canonical_json(expected):
            raise ValueError('Flat profile must not retain a different price selection')
        return expected, hashlib.sha256(canonical_json(market)).hexdigest()
    if (price_revision != market['price_revision'] or set(price_data) != {'profile_market'}):
        raise ValueError('Profile prices must match the pinned market revision')
    binding = price_data['profile_market']
    if (set(binding) != {'selection', 'product'} or
            canonical_json(_identity_value(binding['selection'])) != canonical_json(market)):
        raise ValueError('Profile prices must match the pinned market selection')
    product = binding['product']
    if (not isinstance(product, Mapping) or product.get('series') != market['series'] or
            list(product.get('window', ())) != [market['window_start'], market['window_end']]):
        raise ValueError('Price product differs from selected series/window')
    matrix = np.asarray(product['matrix_eur_kwh'], dtype=float)
    counts = np.asarray(product['counts_hours'], dtype=float)
    if (matrix.shape != (12,24) or counts.shape != matrix.shape or
            not np.all(np.isfinite(matrix)) or not np.all(np.isfinite(counts)) or
            np.any(counts < 0) or not np.isfinite(product['mean_eur_kwh'])):
        raise ValueError('Invalid bound month/hour prices')
    mask = dataset['valid']
    month, hour = dataset['month'][mask]-1, dataset['hour'][mask]
    if np.any(counts[month, hour] <= 0):
        raise ValueError('Price window lacks required month/hour coverage')
    if market['valuation_mode'] == 'shape_scaled':
        shape = np.asarray(product['normalized_shape'], dtype=float)
        if (product['mean_eur_kwh'] <= 0 or shape.shape != matrix.shape or
                not np.all(np.isfinite(shape)) or
                not np.array_equal(shape, matrix / product['mean_eur_kwh'])):
            raise ValueError('Shape-scaled prices require a positive annual mean and normalized shape')
        from .prepare_market_prices import expand_pzo_proxy
        expand_pzo_proxy(shape, dataset['month'], dataset['hour'], mask)
    return price_data, price_revision


@dataclass(frozen=True, eq=False)
class RuntimeContext:
    owner_id: str
    input_revision: str
    dataset_digest: str
    price_revision: str
    defaults_snapshot: Mapping
    thermal_reference: Mapping | ThermalReference
    fiscal_revision: str
    calendar_version: str
    dataset: Mapping
    weather_identity: tuple
    records_snapshot: tuple
    metadata: Mapping
    price_data: Mapping = field(default_factory=dict)
    household_profile: Mapping | None = None
    model_build_id: str = field(default_factory=model_build_digest)
    key: str = field(init=False)

    def __post_init__(self):
        if self.household_profile is not None:
            from .profiles import parse_profile, profile_to_dict, canonical_json
            profile = profile_to_dict(parse_profile(profile_to_dict(self.household_profile)))
            manifest = _identity_value(self.metadata.get('dataset_manifest', {}))
            if (self.calendar_version != 'utc_hourly_v2' or
                    hashlib.sha256(canonical_json(manifest)).hexdigest() != self.dataset_digest or
                    manifest.get('profile_digest') != hashlib.sha256(canonical_json(profile)).hexdigest()):
                raise ValueError('Household profile must match the pinned v2 dataset manifest')
            planes = manifest.get('plane_ids')
            if (planes != list(self.dataset['audit']['plane_ids']) or
                    planes != [r['id'] for r in profile['roofs'] if r['id'] in planes] or
                    len(planes) != self.dataset['power'].shape[0]):
                raise ValueError('Household roof axes do not match the compiled dataset')
            from .fiscal import resolve_fiscal
            policy = resolve_fiscal(profile['fiscal'], self.fiscal_revision,
                has_gas=any(u['carrier'] == 'gas' for u in profile['thermal']['end_uses']))
            object.__setattr__(self, 'household_profile', profile)
            from .thermal_baseline import resolve_thermal_baseline, ThermalReference
            resolved = resolve_thermal_baseline(profile)
            if type(self.thermal_reference) is not ThermalReference or (
                    canonical_json(_identity_value(self.thermal_reference)) !=
                    canonical_json(_identity_value(resolved))):
                raise ValueError('Thermal reference must match the pinned household profile')
            # Bind the validated, independently resolved value, never caller aliases.
            object.__setattr__(self, 'thermal_reference', resolved)
            gas_uses = [u for u in resolved.end_uses.values() if u.carrier == 'gas']
            if gas_uses and policy.parameters['mode'] == 'parameters' and any(u.monthly_input is None for u in gas_uses):
                raise ValueError('Detailed gas billing requires explicit monthly gas quantities')
            if gas_uses and policy.parameters['mode'] == 'annual_bill':
                total = sum(u.annual_bill_eur if u.annual_bill_eur is not None else
                    u.annual_fixed_cost + u.annual_variable_cost for u in gas_uses)
                if not np.isclose(total, policy.parameters['annual_bill_eur'], rtol=0, atol=1e-8):
                    raise ValueError('Annual gas approximation must match explicit thermal bills')
            prices, revision = _profile_price_binding(profile, self.price_data, self.price_revision, self.dataset)
            object.__setattr__(self, 'price_data', prices)
            object.__setattr__(self, 'price_revision', revision)
            defaults = dict(self.defaults_snapshot)
            from .fiscal import FISCAL_RATES
            defaults.update({k:v for k,v in policy.parameters.items() if k in FISCAL_RATES})
            defaults.setdefault('pmg_eligible_confirmed', False)
            defaults.setdefault('deduction_eligible_confirmed', False)
            defaults.setdefault('unsupported_fiscal_year_acknowledged', False)
            defaults['export_price_profile'] = ('flat_benchmark' if profile['market']['valuation_mode'] == 'flat'
                                                else 'profile_market')
            defaults['export_price_window'] = 'profile_market'
            if 'export_market_price' in profile['default_settings']:
                defaults['export_market_price'] = profile['default_settings']['export_market_price']
            object.__setattr__(self, 'defaults_snapshot', defaults)
        elif type(self.thermal_reference) in _thermal_types():
            raise ValueError('ThermalReference requires a pinned household profile')
        elif not isinstance(self.thermal_reference, Mapping):
            raise TypeError('Legacy thermal reference must be a mapping')
        for field in fields(self):
            if field.init:
                object.__setattr__(self, field.name, freeze(getattr(self, field.name)))
        self._validate_thermal_inputs()
        identity = {name: getattr(self, name) for name in (
            'owner_id', 'input_revision', 'dataset_digest', 'price_revision',
            'defaults_snapshot', 'thermal_reference', 'fiscal_revision',
            'calendar_version', 'model_build_id')}
        identity['thermal_inputs'] = self.metadata.get('thermal_inputs', {})
        identity['compiler_version'] = self.metadata.get('compiler_version', 'uncompiled')
        if self.household_profile is not None:
            identity['household_profile'] = self.household_profile
            identity['profile_prices'] = self.price_data
        raw = json.dumps(_identity_value(identity), sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False, allow_nan=False).encode('utf-8')
        object.__setattr__(self, 'key', hashlib.sha256(raw).hexdigest())


    def _validate_thermal_inputs(self):
        """Bind caller-verified separated hourly artifacts, never arbitrary paths.

        Service ownership/readiness checks remain with the caller. Both clock
        projections must already be aligned to the compiled weather axes.
        """
        inputs = self.metadata.get('thermal_inputs', {})
        if not isinstance(inputs, Mapping) or set(inputs) - {'separated_electricity', 'disconnect_end_uses'}:
            raise ValueError('Unknown thermal runtime inputs')
        if not inputs:
            return
        from .thermal_baseline import ThermalReference
        if type(self.thermal_reference) is not ThermalReference:
            raise ValueError('Thermal runtime inputs require a household ThermalReference')
        uses = self.thermal_reference.end_uses
        disconnected = inputs.get('disconnect_end_uses', ())
        if (not isinstance(disconnected, (list, tuple)) or
                any(type(k) is not str or k not in uses for k in disconnected) or
                len(set(disconnected)) != len(disconnected)):
            raise ValueError('Disconnection must explicitly identify distinct end uses')
        separated = inputs.get('separated_electricity', {})
        if not isinstance(separated, Mapping) or set(separated) - uses.keys():
            raise ValueError('Unknown separated electrical end use')
        for key, item in separated.items():
            use = uses[key]
            if (not isinstance(item, Mapping) or set(item) != {'artifact_id', 'civil', 'standard'} or
                    use.artifact_id is None or item['artifact_id'] != use.artifact_id or
                    not use.included_in_electric_load):
                raise ValueError('Separated electricity must match the claimed embedded artifact')
            if key in ('space_heat','dhw','cooking') and use.provenance.get('period_relation') != 'same':
                raise ValueError('Separated replacement must share the household measurement period')
            for clock in ('civil', 'standard'):
                a = np.asarray(item[clock])
                if (a.dtype.kind not in 'iuf' or a.shape != self.dataset['valid'].shape or
                        not np.all(np.isfinite(a)) or np.any(a < 0) or
                        np.any(a[~self.dataset['valid']] != 0)):
                    raise ValueError('Separated electricity must align with the compiled hourly axis')
                if use.monthly_input is not None:
                    for yi in range(len(a)):
                        for month in range(1,13):
                            selected = self.dataset['valid'][yi] & (self.dataset['month'][yi] == month)
                            if not np.isclose(a[yi, selected].sum(), use.monthly_input[month-1], rtol=1e-10, atol=1e-8):
                                raise ValueError('Separated electricity disagrees with measured monthly input')


_bound = None
_lock = Lock()


def initialize_runtime(ctx):
    """Bind once; only repeating the identical context object is idempotent."""
    if not isinstance(ctx, RuntimeContext):
        raise TypeError('initialize_runtime requires a RuntimeContext')
    global _bound
    with _lock:
        if _bound is not None and _bound is not ctx:
            raise RuntimeError('Runtime already initialized; a different revision requires a new process')
        _bound = ctx
    return ctx


def current_runtime():
    if _bound is None:
        raise RuntimeError('Runtime is not initialized; explicitly initialize a local profile before computation')
    return _bound


def assert_runtime_key(runtime_key):
    ctx = current_runtime()
    if runtime_key != ctx.key:
        raise ValueError('Cache runtime key does not match the bound immutable revision')
    return ctx


def runtime_if_initialized():
    """For pure schema/storage readers which are also usable in the parent."""
    return _bound
