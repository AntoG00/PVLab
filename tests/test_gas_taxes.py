"""Tests for pure gas pricing kernel, statutory gas tax accounting, and assumptions schema."""
from __future__ import annotations

import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from feasibility import assumptions, engine, shared_state
from feasibility.gas_taxes import (
    DEFAULT_GAS_RATES,
    GAS_ANNUAL_BREAKS,
    GAS_TAX_METADATA,
    calculate_gas_bill,
    calculate_gas_cost,
    get_gas_tax_metadata,
    validate_gas_tax_settings,
)
from test_support import IsolatedDefaultsTestCase


def _independent_old_annual_gas_bill(
    volume: float,
    year: int = 1,
    commodity: float = 0.5,
    regulated: float = 0.4,
    fixed_day: float = 0.3,
    inflation: float = 0.02,
    gas_real_escalation: float = 0.0,
) -> float:
    """Independent implementation of the legacy annual progressive gas bill formula."""
    if volume <= 0:
        return 0.0
    variable = (commodity + regulated) * (1.0 + inflation) ** (year - 1) * (1.0 + gas_real_escalation) ** (year - 1)
    fixed = fixed_day * 365.0 * (1.0 + inflation) ** (year - 1)
    breaks = [0.0, 120.0, 480.0, 1560.0, float('inf')]
    excise = [0.038, 0.135, 0.120, 0.150]
    net = []
    for i in range(4):
        quantity = max(0.0, min(volume, breaks[i + 1]) - breaks[i])
        regional = 0.019 if i == 0 else 0.031
        subtotal = quantity * (variable + excise[i] + regional)
        net.append(subtotal * (1.10 if i < 2 else 1.22))
    return float(sum(net) + fixed * 1.22)


class GasTaxKernelTests(unittest.TestCase):
    """Core mathematical correctness, boundary behaviors, and reference invoice tests."""

    def test_independent_old_formula_equivalence_at_boundaries_and_years(self):
        """Kernel must match the independent legacy annual formula at boundaries and escalated years."""
        test_volumes = [
            0.01, 10.0, 50.0,
            119.9, 120.0, 120.1,
            479.9, 480.0, 480.1,
            600.0,  # Canonical annual sum from GAS_MONTHLY
            1559.9, 1560.0, 1560.1,
            2500.0,
        ]
        test_years = [1, 2, 20]

        for yr in test_years:
            for vol in test_volumes:
                with self.subTest(year=yr, volume=vol):
                    expected = _independent_old_annual_gas_bill(vol, year=yr)
                    result = calculate_gas_cost(vol, days=365, year=yr, prior_year_smc=0.0, include_fixed=True)
                    self.assertAlmostEqual(result['total_eur'], expected, places=9)

                    # Also verify exact parity with engine.gas_bill(s, year=yr, smc=vol)
                    engine_bill = engine.gas_bill(engine.defaults(), year=yr, smc=vol)
                    self.assertAlmostEqual(result['total_eur'], engine_bill, places=9)

    def test_reference_bill_calculation_with_prior_cumulative(self):
        """Independent invented bill: 50 Smc, 30 days, 200 Smc prior cumulative."""
        rates={**DEFAULT_GAS_RATES,'gas_commodity':.5,'gas_regulated':.4,'gas_fixed_day':.3}
        result=calculate_gas_cost(volume=50.,days=30,year=1,prior_year_smc=200.,include_fixed=True,settings=rates)
        # Entire interval is in the 120..480 bracket. These independent exact
        # amounts cover every subtotal, tax base, VAT split and rounded total.
        expected=dict(commodity_eur=25.,network_eur=20.,variable_pretax_eur=45.,fixed_eur=9.,
            national_excise_eur=6.75,regional_surcharge_eur=1.55,reduced_vat_base_eur=53.3,
            reduced_vat_eur=5.33,standard_vat_fixed_base_eur=9.,standard_vat_variable_base_eur=0.,
            standard_vat_eur=1.98,vat_eur=7.31,total_eur=69.61)
        for key,value in expected.items():
            with self.subTest(field=key):self.assertAlmostEqual(result[key],value,places=6)
        self.assertEqual(round(result['total_eur'],2),69.61)

    def test_fresh_year_gives_different_excise_than_prior_cumulative(self):
        """Fresh-year consumption must apply first bracket rates, not hardcode the prior=146 total."""
        rates = {
            'gas_commodity': 0.470,
            'gas_regulated': 0.395,
            'gas_fixed_day': 0.28,
            'gas_excise_0_120': 0.038,
            'gas_regional_0_120': 0.019,
            'gas_vat_reduced': 0.10,
            'gas_vat_standard': 0.22,
        }

        fresh = calculate_gas_cost(50.0, days=30, year=1, prior_year_smc=0.0, include_fixed=True, settings=rates)
        with_prior = calculate_gas_cost(50.0, days=30, year=1, prior_year_smc=200.0, include_fixed=True, settings=rates)

        # Fresh year applies 0-120 slab rates (0.038 and 0.019)
        expected_fresh_national = 50.0 * 0.038  # 0.836
        expected_fresh_regional = 50.0 * 0.019  # 0.418
        self.assertAlmostEqual(fresh['national_excise_eur'], expected_fresh_national, places=6)
        self.assertAlmostEqual(fresh['regional_surcharge_eur'], expected_fresh_regional, places=6)

        # Must differ distinctly from with_prior (32.902 vs 35.5398)
        self.assertNotEqual(fresh['total_eur'], with_prior['total_eur'])
        self.assertAlmostEqual(fresh['total_eur'], (50*(.470+.395+.038+.019))*1.10 + 30*.28*1.22, places=6)

    def test_component_sum_identity_and_vat_on_tax_inclusive_subtotal(self):
        """Total must equal exact sum of components; VAT must be charged on tax-inclusive base."""
        test_cases = [
            (50.0, 30, 0.0),
            (200.0, 60, 50.0),
            (600.0, 90, 400.0),
            (1000.0, 180, 1200.0),
        ]
        for vol, days, prior in test_cases:
            with self.subTest(vol=vol, days=days, prior=prior):
                res = calculate_gas_cost(vol, days=days, year=2, prior_year_smc=prior, include_fixed=True)

                # Component sum identity
                comp_sum = (
                    res['commodity_eur']
                    + res['network_eur']
                    + res['fixed_eur']
                    + res['national_excise_eur']
                    + res['regional_surcharge_eur']
                    + res['vat_eur']
                )
                self.assertAlmostEqual(res['total_eur'], comp_sum, places=9)

                # Variable subtotal identity
                var_sum = (
                    res['commodity_eur']
                    + res['network_eur']
                    + res['national_excise_eur']
                    + res['regional_surcharge_eur']
                    + res['reduced_vat_eur']
                    + res['standard_vat_variable_eur']
                )
                self.assertAlmostEqual(res['variable_total_eur'], var_sum, places=9)

                # Fixed subtotal identity
                fixed_sum = res['fixed_eur'] + res['standard_vat_fixed_eur']
                self.assertAlmostEqual(res['fixed_total_eur'], fixed_sum, places=9)
                self.assertAlmostEqual(res['total_eur'], res['variable_total_eur'] + res['fixed_total_eur'], places=9)

                # VAT base includes excise + surcharge: taxable bases sum to pretax + excise + surcharge
                taxable_base_sum = res['reduced_vat_base_eur'] + res['standard_vat_variable_base_eur']
                pretax_plus_taxes = res['variable_pretax_eur'] + res['national_excise_eur'] + res['regional_surcharge_eur']
                self.assertAlmostEqual(taxable_base_sum, pretax_plus_taxes, places=9)

    def test_cumulative_split_equivalence_across_thresholds(self):
        """Splitting a volume across intervals must yield identical variable taxes and costs."""
        # Test crossings of 120, 480, and 1560 thresholds
        scenarios = [
            (80.0, 70.0),     # crosses 120 (total 150)
            (400.0, 150.0),   # crosses 480 (total 550)
            (1400.0, 300.0),  # crosses 1560 (total 1700)
            (100.0, 1500.0),  # crosses both 120 and 480 (total 1600)
        ]

        for v1, v2 in scenarios:
            with self.subTest(v1=v1, v2=v2):
                v_total = v1 + v2
                # Single interval [0, v_total] (variable only, include_fixed=False)
                full = calculate_gas_cost(v_total, days=365, year=1, prior_year_smc=0.0, include_fixed=False)

                # Split intervals: [0, v1] and [v1, v1 + v2]
                part1 = calculate_gas_cost(v1, days=180, year=1, prior_year_smc=0.0, include_fixed=False)
                part2 = calculate_gas_cost(v2, days=185, year=1, prior_year_smc=v1, include_fixed=False)

                self.assertAlmostEqual(part1['commodity_eur'] + part2['commodity_eur'], full['commodity_eur'], places=9)
                self.assertAlmostEqual(part1['network_eur'] + part2['network_eur'], full['network_eur'], places=9)
                self.assertAlmostEqual(part1['national_excise_eur'] + part2['national_excise_eur'], full['national_excise_eur'], places=9)
                self.assertAlmostEqual(part1['regional_surcharge_eur'] + part2['regional_surcharge_eur'], full['regional_surcharge_eur'], places=9)
                self.assertAlmostEqual(part1['reduced_vat_eur'] + part2['reduced_vat_eur'], full['reduced_vat_eur'], places=9)
                self.assertAlmostEqual(part1['standard_vat_eur'] + part2['standard_vat_eur'], full['standard_vat_eur'], places=9)
                self.assertAlmostEqual(part1['variable_total_eur'] + part2['variable_total_eur'], full['variable_total_eur'], places=9)
                self.assertAlmostEqual(part1['total_eur'] + part2['total_eur'], full['total_eur'], places=9)

    def test_zero_volume_retained_connection_vs_excluded(self):
        """Zero volume with retained connection bills fixed charges + 22% VAT; excluded bills 0."""
        # Retained connection (include_fixed=True)
        retained = calculate_gas_cost(0.0, days=365, year=1, prior_year_smc=0.0, include_fixed=True)
        expected_fixed = 0.3 * 365.0  # 102.20
        expected_vat = expected_fixed * 0.22  # 22.484
        expected_total = expected_fixed * 1.22  # 124.684

        self.assertEqual(retained['volume'], 0.0)
        self.assertEqual(retained['commodity_eur'], 0.0)
        self.assertEqual(retained['network_eur'], 0.0)
        self.assertEqual(retained['national_excise_eur'], 0.0)
        self.assertEqual(retained['regional_surcharge_eur'], 0.0)
        self.assertEqual(retained['reduced_vat_eur'], 0.0)
        self.assertAlmostEqual(retained['fixed_eur'], expected_fixed, places=6)
        self.assertAlmostEqual(retained['standard_vat_fixed_base_eur'], expected_fixed, places=6)
        self.assertAlmostEqual(retained['standard_vat_eur'], expected_vat, places=6)
        self.assertAlmostEqual(retained['vat_eur'], expected_vat, places=6)
        self.assertAlmostEqual(retained['total_eur'], expected_total, places=6)

        # Excluded connection / no service (include_fixed=False)
        excluded = calculate_gas_cost(0.0, days=365, year=1, prior_year_smc=0.0, include_fixed=False)
        self.assertEqual(excluded['fixed_eur'], 0.0)
        self.assertEqual(excluded['vat_eur'], 0.0)
        self.assertEqual(excluded['total_eur'], 0.0)
        self.assertEqual(excluded['variable_total_eur'], 0.0)

    def test_edited_individual_and_all_zero_rates(self):
        """Testing user-edited custom rates and zero-tax edge case."""
        # 1. Custom excise rate
        custom = calculate_gas_cost(50.0, settings={'gas_excise_0_120': 0.050})
        self.assertAlmostEqual(custom['national_excise_eur'], 50.0 * 0.050, places=6)

        # 2. All taxes set to zero
        zero_taxes = {
            'gas_excise_0_120': 0.0,
            'gas_excise_120_480': 0.0,
            'gas_excise_480_1560': 0.0,
            'gas_excise_over_1560': 0.0,
            'gas_regional_0_120': 0.0,
            'gas_regional_over_120': 0.0,
            'gas_vat_reduced': 0.0,
            'gas_vat_standard': 0.0,
        }
        res_zero = calculate_gas_cost(600.0, days=365, year=1, include_fixed=True, settings=zero_taxes)
        self.assertEqual(res_zero['national_excise_eur'], 0.0)
        self.assertEqual(res_zero['regional_surcharge_eur'], 0.0)
        self.assertEqual(res_zero['vat_eur'], 0.0)
        expected_pretax = 600.0 * (0.5 + 0.4) + 0.3 * 365.0
        self.assertAlmostEqual(res_zero['total_eur'], expected_pretax, places=6)

    def test_json_safety_and_no_infinity(self):
        """Output must serialize cleanly to JSON and contain null rather than infinity."""
        res = calculate_gas_cost(2000.0, days=365, year=1, prior_year_smc=0.0, include_fixed=True)
        dumped = json.dumps(res, allow_nan=False)
        self.assertIsInstance(dumped, str)
        self.assertIn('"upper_smc": null', dumped)
        self.assertNotIn('Infinity', dumped)
        self.assertNotIn('NaN', dumped)
        self.assertIsNone(res['brackets'][-1]['upper_smc'])


class GasTaxValidationTests(unittest.TestCase):
    """Input rejection, boundary limits, and non-boolean numeric enforcement."""

    def test_reject_negative_and_nonfinite_inputs(self):
        invalid_cases = [
            ({'volume': -1.0}, 'volume must be >= 0.0'),
            ({'volume': float('nan')}, 'volume must be finite'),
            ({'volume': float('inf')}, 'volume must be finite'),
            ({'days': -5}, 'days must be >= 0.0'),
            ({'days': float('nan')}, 'days must be finite'),
            ({'year': 0}, 'year must be >= 1.0'),
            ({'year': -1}, 'year must be >= 1.0'),
            ({'prior_year_smc': -0.1}, 'prior_year_smc must be >= 0.0'),
            ({'prior_year_smc': float('inf')}, 'prior_year_smc must be finite'),
        ]
        for kwargs, match in invalid_cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError) as ctx:
                    calculate_gas_cost(
                        volume=kwargs.get('volume', 10.0),
                        days=kwargs.get('days', 365),
                        year=kwargs.get('year', 1),
                        prior_year_smc=kwargs.get('prior_year_smc', 0.0),
                    )
                self.assertIn(match, str(ctx.exception))

    def test_reject_boolean_numeric_parameters(self):
        """Booleans must not silently masquerade as 0 or 1 numbers."""
        bool_cases = [
            ({'volume': True}, 'volume must be a numeric value'),
            ({'volume': False}, 'volume must be a numeric value'),
            ({'days': True}, 'days must be a numeric value'),
            ({'year': True}, 'year must be a numeric value'),
            ({'prior_year_smc': False}, 'prior_year_smc must be a numeric value'),
        ]
        for kwargs, match in bool_cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError) as ctx:
                    calculate_gas_cost(
                        volume=kwargs.get('volume', 10.0),
                        days=kwargs.get('days', 365),
                        year=kwargs.get('year', 1),
                        prior_year_smc=kwargs.get('prior_year_smc', 0.0),
                    )
                self.assertIn(match, str(ctx.exception))

    def test_reject_non_boolean_include_fixed(self):
        for invalid in ["True", 1, 0, None, [True]]:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError) as ctx:
                    calculate_gas_cost(10.0, include_fixed=invalid)  # type: ignore[arg-type]
                self.assertIn('include_fixed must be a boolean', str(ctx.exception))

    def test_reject_invalid_rate_settings(self):
        """Rates in settings must respect bounds, non-boolean, and finite numbers."""
        invalid_settings = [
            ({'gas_excise_0_120': 1.01}, 'gas_excise_0_120 must be <= 1.0'),
            ({'gas_excise_0_120': -0.01}, 'gas_excise_0_120 must be >= 0.0'),
            ({'gas_excise_0_120': True}, 'gas_excise_0_120 must be a numeric value'),
            ({'gas_vat_reduced': 1.5}, 'gas_vat_reduced must be <= 1.0'),
            ({'gas_vat_standard': -0.1}, 'gas_vat_standard must be >= 0.0'),
            ({'gas_commodity': 2.5}, 'gas_commodity must be <= 2.0'),
            ({'gas_regulated': -0.05}, 'gas_regulated must be >= 0.0'),
            ({'gas_fixed_day': 1.5}, 'gas_fixed_day must be <= 1.0'),
        ]
        for s, match in invalid_settings:
            with self.subTest(setting=s):
                with self.assertRaises(ValueError) as ctx:
                    calculate_gas_cost(10.0, settings=s)
                self.assertIn(match, str(ctx.exception))


class AssumptionsSchemaAndPersistenceTests(IsolatedDefaultsTestCase):
    """Assumptions SPEC schema, engine.validate generic enforcement, and persistence isolation."""

    def test_spec_contains_all_gas_tax_assumptions(self):
        """SPEC must define all 8 scalar rate assumptions in group 'gas' with bounds [0, 1]."""
        expected_keys = {
            'gas_excise_0_120': (0.038, 'EUR/Smc', [0, 1], 'gas'),
            'gas_excise_120_480': (0.135, 'EUR/Smc', [0, 1], 'gas'),
            'gas_excise_480_1560': (0.120, 'EUR/Smc', [0, 1], 'gas'),
            'gas_excise_over_1560': (0.150, 'EUR/Smc', [0, 1], 'gas'),
            'gas_regional_0_120': (0.019, 'EUR/Smc', [0, 1], 'gas'),
            'gas_regional_over_120': (0.031, 'EUR/Smc', [0, 1], 'gas'),
            'gas_vat_reduced': (0.10, 'fraction', [0, 1], 'gas'),
            'gas_vat_standard': (0.22, 'fraction', [0, 1], 'gas'),
        }

        for key, (val, unit, bounds, group) in expected_keys.items():
            self.assertIn(key, assumptions.SPEC)
            spec_row = assumptions.SPEC[key]
            self.assertEqual(spec_row[0], val)
            self.assertEqual(spec_row[1], unit)
            self.assertEqual(spec_row[2], bounds)
            self.assertEqual(spec_row[3], group)

    def test_clarified_descriptions_for_pretax_prices(self):
        """Three pre-tax prices must have clarified descriptions in SPEC."""
        desc_comm = assumptions.SPEC['gas_commodity'][4]
        desc_reg = assumptions.SPEC['gas_regulated'][4]
        desc_fix = assumptions.SPEC['gas_fixed_day'][4]

        self.assertIn('Pre-tax', desc_comm)
        self.assertIn('commodity', desc_comm)
        self.assertIn('Pre-tax', desc_reg)
        self.assertIn('flat approximation', desc_reg)
        self.assertIn('Pre-tax', desc_fix)
        self.assertIn('supplier and network fixed', desc_fix)

    def test_engine_validate_enforces_bounds_on_new_keys(self):
        """engine.validate generically validates bounds and numeric types for new keys."""
        # Valid override
        valid_res = engine.validate({'gas_excise_0_120': 0.045})
        self.assertEqual(valid_res['gas_excise_0_120'], 0.045)

        # Rejects bounds violation
        with self.assertRaises(ValueError):
            engine.validate({'gas_excise_0_120': 1.05})
        with self.assertRaises(ValueError):
            engine.validate({'gas_regional_0_120': -0.01})
        with self.assertRaises(ValueError):
            engine.validate({'gas_vat_standard': 1.1})

        # Rejects boolean numeric
        with self.assertRaises(ValueError):
            engine.validate({'gas_vat_reduced': True})

    def test_records_provenance_metadata(self):
        """records() emits official statutory sources, dates, and avoids conflating export taxes."""
        recs = {r['name']: r for r in assumptions.records()}

        for key in ('inflation','gas_commodity','gas_excise_0_120','gas_excise_120_480',
                    'gas_excise_480_1560','gas_excise_over_1560','gas_regional_0_120',
                    'gas_regional_over_120','gas_vat_reduced','gas_vat_standard'):
            self.assertIsNone(recs[key]['date_accessed'])
            self.assertEqual(recs[key]['source'],'Neutral modeling example; profile-specific evidence required')
            self.assertNotEqual(recs[key]['status'],'measured')
        policy=get_gas_tax_metadata()
        self.assertIn('statutory_sources',policy)
        self.assertIn('Gazzetta',str(policy['statutory_sources']['national_excise']))
        self.assertIn('ARISGAN',str(policy['statutory_sources']))

        # Solar export income tax assumptions remain untouched in group 'tax'
        self.assertEqual(recs['regional_additional_tax']['group'], 'tax')
        self.assertEqual(recs['municipal_additional_tax']['group'], 'tax')
        self.assertEqual(recs['export_irpef']['group'], 'tax')

    def test_save_and_reset_user_default_in_temp_isolation(self):
        """Testing user default persistence and reset against guarded temporary files."""
        # 1. Save user default override
        saved = assumptions.save_user_default('gas_excise_0_120', 0.042, engine.validate)
        self.assertEqual(saved['value'], 0.042)
        self.assertEqual(saved['status'], 'provided')
        self.assertEqual(saved['owner'], 'user')
        self.assertEqual(saved['factory_default'], 0.038)

        # 2. Reload defaults and check record provenance
        defs = assumptions.persisted_defaults()
        self.assertEqual(defs['gas_excise_0_120'], 0.042)

        rec = next(r for r in assumptions.persisted_records() if r['name'] == 'gas_excise_0_120')
        self.assertEqual(rec['value'], 0.042)
        self.assertEqual(rec['status'], 'provided')
        self.assertEqual(rec['owner'], 'user')

        # 3. Reset user default back to factory default
        assumptions.reset_user_default('gas_excise_0_120')
        defs_reset = assumptions.persisted_defaults()
        self.assertEqual(defs_reset['gas_excise_0_120'], 0.038)

    def test_old_scenario_additive_defaults_preservation(self):
        """shared_state._snapshot() seamlessly populates new gas tax defaults into older scenarios while preserving nondefault unrelated settings, custom quotes, and explicit gas overrides."""
        # Synthesize an older scenario file lacking 7 gas tax keys, but with:
        # 1. Nondefault unrelated settings
        # 2. An explicit saved gas tax override (gas_excise_120_480: 0.180)
        # 3. Nondefault custom quote with installer and line items
        old_settings = dict(assumptions.SPEC)
        old_settings_values = {k: v[0] for k, v in old_settings.items() if not k.startswith('gas_excise_') and not k.startswith('gas_regional_') and not k.startswith('gas_vat_')}

        # Nondefault settings
        old_settings_values['south_panels'] = 16
        old_settings_values['battery_kwh'] = 12.0
        old_settings_values['retail_commodity'] = 0.285
        old_settings_values['gas_commodity'] = 0.520
        old_settings_values['gas_regulated'] = 0.410
        old_settings_values['gas_fixed_day'] = 0.310
        # Explicit saved gas tax override
        old_settings_values['gas_excise_120_480'] = 0.180

        custom_quote = {
            'installer': 'Campania Solar Tech',
            'turnkey_price_eur': 12500.0,
            'line_items': [
                {'description': 'High-efficiency PV modules', 'total_eur': 7000.0},
                {'description': 'Hybrid Inverter', 'total_eur': 2500.0},
                {'description': 'Installation & Electrical certification', 'total_eur': 3000.0},
            ],
        }

        old_scenario = {
            'state_revision': 'rev-old-custom-99',
            'settings': old_settings_values,
            'quote': custom_quote,
        }
        original_json_text = json.dumps(old_scenario, indent=2)

        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as tf:
            tf.write(original_json_text)
            temp_path = Path(tf.name)

        try:
            snapshot = shared_state._snapshot(temp_path)
            settings = snapshot['settings']
            quote = snapshot['quote']

            # 1. Unrelated nondefault settings are preserved exactly
            self.assertEqual(settings['south_panels'], 16)
            self.assertEqual(settings['battery_kwh'], 12.0)
            self.assertEqual(settings['retail_commodity'], 0.285)
            self.assertEqual(settings['gas_commodity'], 0.520)
            self.assertEqual(settings['gas_regulated'], 0.410)
            self.assertEqual(settings['gas_fixed_day'], 0.310)

            # 2. Saved gas override is preserved, not clobbered by factory default
            self.assertEqual(settings['gas_excise_120_480'], 0.180)

            # 3. Missing new gas tax keys are populated with factory defaults
            self.assertEqual(settings['gas_excise_0_120'], 0.038)
            self.assertEqual(settings['gas_excise_480_1560'], 0.120)
            self.assertEqual(settings['gas_excise_over_1560'], 0.150)
            self.assertEqual(settings['gas_regional_0_120'], 0.019)
            self.assertEqual(settings['gas_regional_over_120'], 0.031)
            self.assertEqual(settings['gas_vat_reduced'], 0.10)
            self.assertEqual(settings['gas_vat_standard'], 0.22)

            # 4. Custom quote is preserved exactly
            self.assertEqual(quote['installer'], 'Campania Solar Tech')
            self.assertEqual(quote['turnkey_price_eur'], 12500.0)
            self.assertEqual(len(quote['line_items']), 3)
            self.assertEqual(quote['line_items'][0]['total_eur'], 7000.0)

            # 5. Underlying disk file was not modified
            raw_on_disk = temp_path.read_text()
            self.assertEqual(raw_on_disk, original_json_text)
            on_disk = json.loads(raw_on_disk)
            self.assertNotIn('gas_excise_0_120', on_disk['settings'])
        finally:
            if temp_path.exists():
                temp_path.unlink()


class GasTaxMetadataTests(unittest.TestCase):
    """Exported metadata and UI integration structure tests."""

    def test_metadata_structure(self):
        meta = get_gas_tax_metadata()
        self.assertIn('statutory_sources', meta)
        self.assertIn('brackets', meta)
        self.assertIn('pretax_price_definitions', meta)

        self.assertEqual(len(meta['brackets']), 4)
        self.assertEqual(meta['brackets'][0]['name'], '0_120')
        self.assertEqual(meta['brackets'][0]['default_national_excise_eur_smc'], 0.038)
        self.assertEqual(meta['brackets'][0]['default_regional_surcharge_eur_smc'], 0.019)
        self.assertEqual(meta['brackets'][0]['default_vat_rate'], 0.10)
        self.assertIsNone(meta['brackets'][3]['upper_smc'])


if __name__ == '__main__':
    unittest.main()
