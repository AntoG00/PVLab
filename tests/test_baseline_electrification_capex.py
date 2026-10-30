"""Focused regressions for Task 3: baseline electrification CAPEX switch."""
import unittest
import numpy as np

from feasibility import assumptions, engine
try:
    from test_support import IsolatedDefaultsTestCase
except ImportError:
    from tests.test_support import IsolatedDefaultsTestCase


class BaselineElectrificationCapexTests(IsolatedDefaultsTestCase):
    def test_default_off_backward_compatibility(self):
        """Default is False and baseline defaults to 0 conversion cost."""
        spec = assumptions.SPEC['electrification_capex_include_baseline']
        self.assertEqual(spec[0], False)
        self.assertEqual(spec[1], 'boolean')
        self.assertIsNone(spec[2])
        self.assertEqual(spec[3], 'loads')

        defaults = assumptions.defaults()
        self.assertIn('electrification_capex_include_baseline', defaults)
        self.assertFalse(defaults['electrification_capex_include_baseline'])

        validated = engine.validate({'load_scenario': 'baseline'})
        self.assertFalse(validated['electrification_capex_include_baseline'])

        res_default = engine.evaluate({'load_scenario': 'baseline', 'south_panels': 10, 'battery_kwh': 0.0})
        self.assertEqual(res_default['finance']['conversion_capex_eur'], 0.0)

        res_explicit_false = engine.evaluate({
            'load_scenario': 'baseline',
            'south_panels': 10,
            'battery_kwh': 0.0,
            'electrification_capex_include_baseline': False
        })
        self.assertEqual(res_explicit_false['finance']['conversion_capex_eur'], 0.0)
        np.testing.assert_array_equal(
            res_default['finance']['cashflows_by_weather']['whole_package'],
            res_explicit_false['finance']['cashflows_by_weather']['whole_package']
        )
        np.testing.assert_array_equal(
            res_default['finance']['cashflows_by_weather']['equity'],
            res_explicit_false['finance']['cashflows_by_weather']['equity']
        )

    def test_baseline_enabled_costs_increase_personal_outlay_no_loan(self):
        """Baseline switch enabled increases upfront personal outlay by capex delta when unlevered."""
        base_settings = {
            'south_panels': 10,
            'battery_kwh': 0.0,
            'load_scenario': 'baseline',
            'loan_amount': 0.0,
            'loan_fraction': 0.0,
            'electrification_capex': 6000.0
        }
        res_off = engine.evaluate({**base_settings, 'electrification_capex_include_baseline': False})
        res_on = engine.evaluate({**base_settings, 'electrification_capex_include_baseline': True})

        capex = res_on['finance']['capex_eur']
        conv_on = res_on['finance']['conversion_capex_eur']
        self.assertEqual(conv_on, 6000.0)
        self.assertEqual(res_off['finance']['conversion_capex_eur'], 0.0)

        # Whole package year 0 outlay includes electrification_capex
        wp_off_y0 = res_off['finance']['cashflows_by_weather']['whole_package'][0][0]
        wp_on_y0 = res_on['finance']['cashflows_by_weather']['whole_package'][0][0]
        self.assertEqual(wp_off_y0, -capex)
        self.assertEqual(wp_on_y0, -capex - 6000.0)
        self.assertAlmostEqual(wp_on_y0 - wp_off_y0, -6000.0)

        # Generic personal equity upfront cash includes electrification_capex
        eq_off_y0 = res_off['finance']['cashflows_by_weather']['equity'][0][0]
        eq_on_y0 = res_on['finance']['cashflows_by_weather']['equity'][0][0]
        self.assertEqual(eq_off_y0, -capex)
        self.assertEqual(eq_on_y0, -capex - 6000.0)
        self.assertAlmostEqual(eq_on_y0 - eq_off_y0, -6000.0)

        # Package equity upfront cash includes electrification_capex
        peq_off_y0 = res_off['finance']['cashflows_by_weather']['package_equity'][0][0]
        peq_on_y0 = res_on['finance']['cashflows_by_weather']['package_equity'][0][0]
        self.assertEqual(peq_off_y0, -capex)
        self.assertEqual(peq_on_y0, -capex - 6000.0)
        self.assertAlmostEqual(peq_on_y0 - peq_off_y0, -6000.0)

        # Solar-only incremental paths remain unaffected
        solar_off_y0 = res_off['finance']['cashflows_by_weather']['solar_equity'][0][0]
        solar_on_y0 = res_on['finance']['cashflows_by_weather']['solar_equity'][0][0]
        self.assertEqual(solar_off_y0, -capex)
        self.assertEqual(solar_on_y0, -capex)

    def test_baseline_enabled_costs_increase_personal_outlay_solar_only_loan(self):
        """Solar-only loan covers solar capex, leaving electrification cost in personal cash outlay."""
        settings = {
            'south_panels': 10,
            'battery_kwh': 0.0,
            'load_scenario': 'baseline',
            'electrification_capex_include_baseline': True,
            'loan_include_electrification': False,
            'loan_mode': 'fraction',
            'loan_fraction': 1.0,
            'electrification_capex': 6000.0
        }
        res = engine.evaluate(settings)
        f = res['finance']
        capex = f['capex_eur']

        self.assertEqual(f['loan_scope'], 'solar')
        self.assertEqual(f['loan_principal_eur'], capex)
        self.assertEqual(f['conversion_capex_eur'], 6000.0)

        # Solar equity year 0 is fully covered by solar loan
        self.assertAlmostEqual(f['cashflows_by_weather']['solar_equity'][0][0], 0.0, places=6)

        # Overall personal equity outlay year 0 is -capex - 6000 + principal = -6000 (user pays conversion)
        self.assertAlmostEqual(f['cashflows_by_weather']['equity'][0][0], -6000.0, places=6)
        self.assertAlmostEqual(f['cashflows_by_weather']['package_equity'][0][0], -6000.0, places=6)

    def test_baseline_loan_include_switch_finances_optional_cost_fraction_mode(self):
        """loan_include_electrification enables financing the optional baseline cost with fraction mode."""
        settings = {
            'south_panels': 10,
            'battery_kwh': 0.0,
            'load_scenario': 'baseline',
            'electrification_capex_include_baseline': True,
            'loan_include_electrification': True,
            'loan_mode': 'fraction',
            'loan_fraction': 1.0,
            'electrification_capex': 6000.0
        }
        res = engine.evaluate(settings)
        f = res['finance']
        capex = f['capex_eur']

        self.assertEqual(f['loan_scope'], 'solar_and_conversion')
        self.assertEqual(f['requested_loan_amount_eur'], capex + 6000.0)
        self.assertEqual(f['loan_principal_eur'], capex + 6000.0)

        # Full package is financed, so year 0 equity cash outlay is 0
        self.assertAlmostEqual(f['cashflows_by_weather']['equity'][0][0], 0.0, places=6)
        self.assertAlmostEqual(f['cashflows_by_weather']['package_equity'][0][0], 0.0, places=6)

        # Financed project capex recognized at t=0
        self.assertAlmostEqual(f['cashflows_by_weather']['financed_project'][0][0], -(capex + 6000.0), places=6)

    def test_baseline_identical_physical_operating_flows_deduction_and_gas(self):
        """Enabling/disabling baseline toggle yields identical physical flows, bills, deduction and gas service."""
        base_settings = {
            'south_panels': 10,
            'battery_kwh': 5.0,
            'load_scenario': 'baseline',
            'electrification_capex': 6000.0,
            'gas_disconnect_cost': 200.0
        }
        res_off = engine.evaluate({**base_settings, 'electrification_capex_include_baseline': False})
        res_on = engine.evaluate({**base_settings, 'electrification_capex_include_baseline': True})

        # Physical energy dispatch identical
        for k in ('load_kwh', 'pv_kwh', 'direct_kwh', 'grid_import_kwh', 'export_kwh'):
            self.assertEqual(res_off['energy'][k], res_on['energy'][k])

        # Enduses identical
        self.assertEqual(res_off['enduses'], res_on['enduses'])

        # Gas service is retained: no gas disconnect cost in baseline
        self.assertEqual(res_on['finance']['conversion_capex_eur'], 6000.0)
        self.assertEqual(res_off['finance']['historical_annual_gas_bill_eur'], res_on['finance']['historical_annual_gas_bill_eur'])

        # Tax deduction basis is unchanged (conversion excluded)
        self.assertEqual(res_off['finance']['eligible_deduction_capex_eur'], res_on['finance']['eligible_deduction_capex_eur'])
        self.assertEqual(res_off['finance']['annual_deduction_eur'], res_on['finance']['annual_deduction_eur'])

        # Operating annual flows (years 1..h) are identical
        h = res_off['settings']['horizon_years']
        for y in range(1, h + 1):
            y_off = res_off['annual_cashflows'][y]
            y_on = res_on['annual_cashflows'][y]
            self.assertEqual(y_off['avoided_import'], y_on['avoided_import'])
            self.assertEqual(y_off['net_export'], y_on['net_export'])
            self.assertEqual(y_off['tax_deduction'], y_on['tax_deduction'])
            self.assertEqual(y_off['baseline_electric_bill'], y_on['baseline_electric_bill'])
            self.assertEqual(y_off['baseline_gas_bill'], y_on['baseline_gas_bill'])
            self.assertEqual(y_off['remaining_gas_bill'], y_on['remaining_gas_bill'])

    def test_non_baseline_unchanged(self):
        """Non-baseline scenarios retain electrification_capex + gas_disconnect_cost independent of toggle."""
        for scenario in ('electrified', 'ev'):
            res_off = engine.evaluate({
                'load_scenario': scenario,
                'south_panels': 10,
                'battery_kwh': 5.0,
                'electrification_capex': 6000.0,
                'gas_disconnect_cost': 150.0,
                'electrification_capex_include_baseline': False
            })
            res_on = engine.evaluate({
                'load_scenario': scenario,
                'south_panels': 10,
                'battery_kwh': 5.0,
                'electrification_capex': 6000.0,
                'gas_disconnect_cost': 150.0,
                'electrification_capex_include_baseline': True
            })

            self.assertEqual(res_off['finance']['conversion_capex_eur'], 6150.0)
            self.assertEqual(res_on['finance']['conversion_capex_eur'], 6150.0)
            np.testing.assert_array_equal(
                res_off['finance']['cashflows_by_weather']['whole_package'],
                res_on['finance']['cashflows_by_weather']['whole_package']
            )
            np.testing.assert_array_equal(
                res_off['finance']['cashflows_by_weather']['equity'],
                res_on['finance']['cashflows_by_weather']['equity']
            )

    def test_validation(self):
        """electrification_capex_include_baseline must be boolean."""
        self.assertTrue(engine.validate({'electrification_capex_include_baseline': True})['electrification_capex_include_baseline'])
        self.assertFalse(engine.validate({'electrification_capex_include_baseline': False})['electrification_capex_include_baseline'])
        with self.assertRaises(ValueError):
            engine.validate({'electrification_capex_include_baseline': 'true'})
        with self.assertRaises(ValueError):
            engine.validate({'electrification_capex_include_baseline': 1})
        with self.assertRaises(ValueError):
            engine.validate({'electrification_capex_include_baseline': None})


if __name__ == '__main__':
    unittest.main()
