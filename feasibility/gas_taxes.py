"""Pure, auditable gas pricing kernel and statutory tax accounting.

Provides progressive slab pricing for natural gas consumption in Italy
(Campania / Mezzogiorno non-industrial heating/domestic supply) according to
statutory national excise brackets, regional excise surcharges (ARISGAN), and
differentiated VAT rates (10% on first 480 Smc/year variable base including
excises, 22% beyond and on fixed components).

Stable API designed for Task 2 engine and financial integration.
"""
from __future__ import annotations

import copy
import math
from typing import Any, Mapping, Sequence

# -----------------------------------------------------------------------------
# Immutable statutory bracket thresholds and factory default rates
# -----------------------------------------------------------------------------
# Annual consumption boundaries (Smc/year)
# Four national excise slabs: [0, 120], [120, 480], [480, 1560], [1560, infinity)
# Regional surcharge (Campania) boundaries: [0, 120], [120, infinity)
# VAT threshold: 480 Smc/year
GAS_ANNUAL_BREAKS: tuple[float, float, float, float, None] = (0.0, 120.0, 480.0, 1560.0, None)
GAS_VAT_THRESHOLD: float = 480.0

DEFAULT_GAS_RATES: dict[str, float] = {
    'gas_commodity': 0.5,
    'gas_regulated': 0.4,
    'gas_fixed_day': 0.3,
    'inflation': 0.02,
    'gas_real_escalation': 0.0,
    'gas_excise_0_120': 0.038,
    'gas_excise_120_480': 0.135,
    'gas_excise_480_1560': 0.120,
    'gas_excise_over_1560': 0.150,
    'gas_regional_0_120': 0.019,
    'gas_regional_over_120': 0.031,
    'gas_vat_reduced': 0.10,
    'gas_vat_standard': 0.22,
}

RATE_BOUNDS: dict[str, tuple[float, float | None]] = {
    'gas_commodity': (0.0, 2.0),
    'gas_regulated': (0.0, 2.0),
    'gas_fixed_day': (0.0, 1.0),
    'inflation': (0.0, 0.15),
    'gas_real_escalation': (-0.10, 0.10),
    'gas_excise_0_120': (0.0, 1.0),
    'gas_excise_120_480': (0.0, 1.0),
    'gas_excise_480_1560': (0.0, 1.0),
    'gas_excise_over_1560': (0.0, 1.0),
    'gas_regional_0_120': (0.0, 1.0),
    'gas_regional_over_120': (0.0, 1.0),
    'gas_vat_reduced': (0.0, 1.0),
    'gas_vat_standard': (0.0, 1.0),
}

GAS_TAX_METADATA: dict[str, Any] = {
    'statutory_sources': {
        'national_excise': {
            'title': 'Gazzetta Ufficiale — tabella aliquote accise gas naturale, Allegato I al TUA, testo pubblicato 27 giugno 2026',
            'scope': 'Aliquote di accisa sul gas naturale per usi civili/riscaldamento - Territori ex Cassa Mezzogiorno (D.P.R. 218/1978)',
            'url': 'https://www.gazzettaufficiale.it/atto/serie_generale/caricaArticoloDefault/originario?atto.codiceRedazionale=26A03280&atto.dataPubblicazioneGazzetta=2026-06-27&atto.tipoProvvedimento=TESTO+COORDINATO+DEL+DECRETO-LEGGE',
            'verified_date': '2026-10-16',
        },
        'regional_surcharge': {
            'title': 'Regione Campania ARISGAN',
            'scope': 'Regione Campania (L.R. 3 aprile 1991 n. 7 e succ. mod.)',
            'url': 'https://archivioportale.regione.campania.it/it/printable/addizionale-regionale-all-accisa-sul-gas-naturale-arisgan-uccs',
            'verified_date': '2026-10-16',
        },
        'vat': {
            'title': 'ARERA guida bolletta gas; Senato risposta interrogazione 4-00121',
            'scope': 'Aliquota 10% fino a 480 Smc/anno su quota variabile (comprensiva di accise); aliquota 22% oltre 480 Smc e su tutte le quote fisse',
            'urls': [
                'https://www.arera.it/en/bolletta/guida-alla-lettura-delle-voci-di-spesa-gas',
                'https://www.senato.it/show-doc?id=1084673&leg=18&tipodoc=Sindispr',
            ],
            'verified_date': '2026-10-16',
        },
    },
    'brackets': [
        {
            'index': 0,
            'name': '0_120',
            'lower_smc': 0.0,
            'upper_smc': 120.0,
            'default_national_excise_eur_smc': 0.038,
            'default_regional_surcharge_eur_smc': 0.019,
            'default_vat_rate': 0.10,
            'vat_tier': 'reduced',
        },
        {
            'index': 1,
            'name': '120_480',
            'lower_smc': 120.0,
            'upper_smc': 480.0,
            'default_national_excise_eur_smc': 0.135,
            'default_regional_surcharge_eur_smc': 0.031,
            'default_vat_rate': 0.10,
            'vat_tier': 'reduced',
        },
        {
            'index': 2,
            'name': '480_1560',
            'lower_smc': 480.0,
            'upper_smc': 1560.0,
            'default_national_excise_eur_smc': 0.120,
            'default_regional_surcharge_eur_smc': 0.031,
            'default_vat_rate': 0.22,
            'vat_tier': 'standard',
        },
        {
            'index': 3,
            'name': 'over_1560',
            'lower_smc': 1560.0,
            'upper_smc': None,
            'default_national_excise_eur_smc': 0.150,
            'default_regional_surcharge_eur_smc': 0.031,
            'default_vat_rate': 0.22,
            'vat_tier': 'standard',
        },
    ],
    'pretax_price_definitions': {
        'gas_commodity': 'Pre-tax natural gas commodity retail price per Smc.',
        'gas_regulated': 'Pre-tax network and system charges flat approximation derived from bill-period average (real network tariff varies across slabs).',
        'gas_fixed_day': 'Pre-tax combined supplier and network fixed charge per day.',
    },
}


def get_gas_tax_metadata() -> dict[str, Any]:
    """Return a deep copy of gas tax statutory sources and bracket definitions."""
    return copy.deepcopy(GAS_TAX_METADATA)


# -----------------------------------------------------------------------------
# Input Validation (No circular engine import)
# -----------------------------------------------------------------------------
def _check_finite_number(val: Any, name: str, min_val: float | None = None, max_val: float | None = None) -> float:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise ValueError(f"{name} must be a numeric value, got {type(val).__name__} ({val!r})")
    f_val = float(val)
    if not math.isfinite(f_val):
        raise ValueError(f"{name} must be finite, got {f_val}")
    if min_val is not None and f_val < min_val:
        raise ValueError(f"{name} must be >= {min_val}, got {f_val}")
    if max_val is not None and f_val > max_val:
        raise ValueError(f"{name} must be <= {max_val}, got {f_val}")
    return f_val


def validate_gas_tax_settings(settings: Mapping[str, Any] | None) -> dict[str, float]:
    """Validate gas tax rates and assumptions, supplying defaults for missing keys.
    
    This function is pure and self-contained; it never reads files from disk.
    Unrelated keys in settings (e.g. solar or heat pump assumptions) are ignored.
    """
    if settings is not None and not isinstance(settings, Mapping):
        raise ValueError(f"settings must be a dict or mapping, got {type(settings).__name__}")

    rates: dict[str, float] = dict(DEFAULT_GAS_RATES)
    if settings is None:
        return rates

    for k, (low, high) in RATE_BOUNDS.items():
        if k in settings:
            val = settings[k]
            rates[k] = _check_finite_number(val, k, min_val=low, max_val=high)

    return rates


# -----------------------------------------------------------------------------
# Pure Pricing Kernel
# -----------------------------------------------------------------------------
def calculate_gas_cost(
    volume: float,
    days: int | float = 365,
    year: int | float = 1,
    prior_year_smc: float = 0.0,
    include_fixed: bool = True,
    settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Calculate itemized gas billing components with progressive statutory taxes.
    
    Args:
        volume: Consumption volume in Smc for the interval being billed. Must be >= 0.
        days: Number of billing days in the interval (default 365). Must be >= 0.
        year: Project year for escalation indexing (1-based, default 1). Must be >= 1.
        prior_year_smc: Cumulative annual consumption before the interval (default 0.0).
            Used to position the interval [prior_year_smc, prior_year_smc + volume]
            against progressive annual statutory tax brackets. Must be >= 0.
        include_fixed: Whether to include fixed supplier/network connection charges.
            When True and volume is 0, reflects a retained gas connection with
            applicable fixed charges and standard VAT.
        settings: Optional dictionary of scenario assumptions or overrides.
            Supplies backward-compatible missing-key defaults without reading disk.

    Returns:
        JSON-safe dictionary containing an exhaustive, auditable itemized breakdown:
        pre-tax commodity, network, and fixed charges, national excise, regional
        surcharge, reduced and standard VAT bases/rates/amounts, overall totals,
        and detailed per-bracket allocations.
    """
    # Strict validation of interval parameters
    vol = _check_finite_number(volume, 'volume', min_val=0.0)
    d = _check_finite_number(days, 'days', min_val=0.0)
    yr = _check_finite_number(year, 'year', min_val=1.0)
    prior = _check_finite_number(prior_year_smc, 'prior_year_smc', min_val=0.0)

    if not isinstance(include_fixed, bool):
        raise ValueError(f"include_fixed must be a boolean, got {type(include_fixed).__name__} ({include_fixed!r})")

    # Resolve rates purely from input settings with factory fallback
    rates = validate_gas_tax_settings(settings)

    # Year escalation factors matching baseline implementation
    infl = (1.0 + rates['inflation']) ** (yr - 1.0)
    escal = (1.0 + rates['gas_real_escalation']) ** (yr - 1.0)

    commodity_unit_rate = rates['gas_commodity'] * infl * escal
    network_unit_rate = rates['gas_regulated'] * infl * escal
    variable_unit_rate = commodity_unit_rate + network_unit_rate

    # Pre-tax variable components
    commodity_eur = vol * commodity_unit_rate
    network_eur = vol * network_unit_rate
    variable_pretax_eur = vol * variable_unit_rate

    # Pre-tax fixed components
    if include_fixed:
        fixed_eur = rates['gas_fixed_day'] * d * infl
    else:
        fixed_eur = 0.0

    # Annual brackets: [0, 120, 480, 1560, infinity)
    breaks: tuple[float, float, float, float, float] = (0.0, 120.0, 480.0, 1560.0, float('inf'))
    bracket_names = ('0_120', '120_480', '480_1560', 'over_1560')
    excise_rate_keys = ('gas_excise_0_120', 'gas_excise_120_480', 'gas_excise_480_1560', 'gas_excise_over_1560')
    regional_rate_keys = ('gas_regional_0_120', 'gas_regional_over_120', 'gas_regional_over_120', 'gas_regional_over_120')

    vat_reduced_rate = rates['gas_vat_reduced']
    vat_standard_rate = rates['gas_vat_standard']

    start = prior
    end = prior + vol

    brackets: list[dict[str, Any]] = []
    national_excise_eur = 0.0
    regional_surcharge_eur = 0.0
    reduced_vat_base_eur = 0.0
    standard_vat_variable_base_eur = 0.0

    for i in range(4):
        b_low = breaks[i]
        b_high = breaks[i + 1]

        # Portioned consumption in this bracket across [start, end]
        if end > b_low and start < b_high:
            q = max(0.0, min(end, b_high) - max(start, b_low))
        else:
            q = 0.0

        b_excise_rate = rates[excise_rate_keys[i]]
        b_regional_rate = rates[regional_rate_keys[i]]
        b_vat_rate = vat_reduced_rate if i < 2 else vat_standard_rate

        b_commodity_eur = q * commodity_unit_rate
        b_network_eur = q * network_unit_rate
        b_pretax_eur = q * variable_unit_rate
        b_excise_eur = q * b_excise_rate
        b_regional_eur = q * b_regional_rate

        # Taxable base for VAT on this bracket includes variable pretax + excise + surcharge
        b_taxable_base = b_pretax_eur + b_excise_eur + b_regional_eur
        b_vat_eur = b_taxable_base * b_vat_rate
        b_total_eur = b_taxable_base + b_vat_eur

        national_excise_eur += b_excise_eur
        regional_surcharge_eur += b_regional_eur

        if i < 2:
            reduced_vat_base_eur += b_taxable_base
        else:
            standard_vat_variable_base_eur += b_taxable_base

        brackets.append({
            'bracket_index': i,
            'name': bracket_names[i],
            'lower_smc': float(b_low),
            'upper_smc': float(b_high) if math.isfinite(b_high) else None,
            'quantity_smc': float(q),
            'national_excise_rate': float(b_excise_rate),
            'national_excise_eur': float(b_excise_eur),
            'regional_surcharge_rate': float(b_regional_rate),
            'regional_surcharge_eur': float(b_regional_eur),
            'vat_rate': float(b_vat_rate),
            'commodity_eur': float(b_commodity_eur),
            'network_eur': float(b_network_eur),
            'variable_pretax_eur': float(b_pretax_eur),
            'taxable_base_eur': float(b_taxable_base),
            'vat_eur': float(b_vat_eur),
            'total_eur': float(b_total_eur),
        })

    # Differentiated VAT calculation
    reduced_vat_eur = reduced_vat_base_eur * vat_reduced_rate

    if include_fixed:
        standard_vat_fixed_base_eur = fixed_eur
    else:
        standard_vat_fixed_base_eur = 0.0

    standard_vat_total_base_eur = standard_vat_variable_base_eur + standard_vat_fixed_base_eur
    standard_vat_eur = standard_vat_total_base_eur * vat_standard_rate
    standard_vat_variable_eur = standard_vat_variable_base_eur * vat_standard_rate
    standard_vat_fixed_eur = standard_vat_fixed_base_eur * vat_standard_rate

    vat_eur = reduced_vat_eur + standard_vat_eur

    # Aggregated totals
    variable_taxable_eur = variable_pretax_eur + national_excise_eur + regional_surcharge_eur
    variable_vat_eur = reduced_vat_eur + standard_vat_variable_eur
    variable_total_eur = variable_taxable_eur + variable_vat_eur

    fixed_total_eur = fixed_eur + standard_vat_fixed_eur
    total_eur = variable_total_eur + (fixed_total_eur if include_fixed else 0.0)

    # Days representation: int if integer, else float
    days_out = int(d) if int(d) == d else d

    return {
        'volume': float(vol),
        'days': days_out,
        'prior_year_smc': float(prior),
        'prior_cumulative_smc': float(prior),
        'year': int(yr) if int(yr) == yr else float(yr),
        'include_fixed': bool(include_fixed),

        # Pre-tax variable
        'commodity_eur': float(commodity_eur),
        'network_eur': float(network_eur),
        'regulated_eur': float(network_eur),
        'variable_pretax_eur': float(variable_pretax_eur),

        # Pre-tax fixed
        'fixed_eur': float(fixed_eur),

        # Statutory taxes
        'national_excise_eur': float(national_excise_eur),
        'regional_surcharge_eur': float(regional_surcharge_eur),

        # Reduced VAT (10% on variable consumption up to 480 Smc/year)
        'reduced_vat_base_eur': float(reduced_vat_base_eur),
        'reduced_vat_taxable_base': float(reduced_vat_base_eur),
        'reduced_vat_rate': float(vat_reduced_rate),
        'reduced_vat_eur': float(reduced_vat_eur),
        'reduced_vat_amount': float(reduced_vat_eur),

        # Standard VAT (22% on variable consumption over 480 Smc/year and fixed charges)
        'standard_vat_variable_base_eur': float(standard_vat_variable_base_eur),
        'standard_vat_fixed_base_eur': float(standard_vat_fixed_base_eur),
        'standard_vat_total_base_eur': float(standard_vat_total_base_eur),
        'standard_vat_base_eur': float(standard_vat_total_base_eur),
        'standard_vat_rate': float(vat_standard_rate),
        'standard_vat_eur': float(standard_vat_eur),
        'standard_vat_amount': float(standard_vat_eur),
        'standard_vat_variable_eur': float(standard_vat_variable_eur),
        'standard_vat_fixed_eur': float(standard_vat_fixed_eur),

        # Combined VAT and totals
        'vat_eur': float(vat_eur),
        'variable_taxable_eur': float(variable_taxable_eur),
        'variable_vat_eur': float(variable_vat_eur),
        'variable_total_eur': float(variable_total_eur),
        'fixed_total_eur': float(fixed_total_eur),
        'total_eur': float(total_eur),

        # Detailed per-bracket breakdown
        'brackets': brackets,
    }


# Aliases for API ergonomics and Task 2 integration
calculate_gas_bill = calculate_gas_cost
calculate_itemized_gas_bill = calculate_gas_cost
