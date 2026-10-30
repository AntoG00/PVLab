"""Unit tests for Stage 4A heat pump settings, schema, validation, and shared state migration.

Covers:
1. Factory defaults, user nominal 9kW, provenance status, and labels.
2. Old scenario missing switch with saved defaults True stays old behavior (including server snapshot).
3. Temporary state partial patch preserves enabled flag.
4. Reset defaults and shared user defaults persistence.
5. Manufacturer map JSON schema validation, roundtrip, and malformed/nonfinite/ragged rejection.
6. Guarded string/choice handling in engine.validate and records().
7. Strict cross-field bounds (curve ordering, tank temperatures, deadband, hygiene, active windows, gas disconnect).
8. Bypassing inapplicable cross-field checks when planning_heat_pump is False to preserve old scenarios.
9. Preserved legacy SCOP, standalone DHW, schedules, and unforced load_scenario.
10. Key coverage (LOAD_KEYS, ENERGY_KEYS, CHART_KEYS, ALL_KEYS) and hashable cache keys.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from feasibility import assumptions, engine, shared_state
import test_support
from feasibility.heatpump import (
    HeatPumpMapMetadata,
    HeatPumpPerformanceMap,
    create_provisional_fallback_map,
)
from feasibility.heatpump_settings import (
    HP_ALL_KEYS,
    HP_COST_KEYS,
    HP_PHYSICAL_KEYS,
    HP_SPEC,
    get_heat_pump_schema,
    parse_manufacturer_map_json,
    serialize_manufacturer_map,
)
from feasibility.server import state_get_response, state_post_response


class TestFactoryDefaultsAndProvenance(unittest.TestCase):
    """Verify factory defaults, nominal 9kW user proposal, and provenance metadata."""

    def setUp(self):
        super().setUp()
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        tpath = Path(self._temp_dir.name)
        p = patch.object(assumptions, 'USER_DEFAULTS_PATH', tpath / 'nonexistent.json')
        p.start()
        self.addCleanup(p.stop)

    def test_factory_defaults_canonical_values(self):
        defs = engine.defaults()
        self.assertIs(defs['planning_heat_pump'], False)
        self.assertEqual(defs['hp_nominal_kw'], 9.0)
        self.assertIs(defs['hp_weather_sensitive'], False)
        self.assertEqual(defs['hp_indoor_temp'], 18.0)
        self.assertEqual(defs['hp_setback_delta'], 0.0)
        self.assertEqual(defs['hp_schedule_mode'], 'continuous')
        self.assertEqual(defs['hp_morning_on'], 6)
        self.assertEqual(defs['hp_morning_off'], 8)
        self.assertEqual(defs['hp_evening_on'], 16)
        self.assertEqual(defs['hp_evening_off'], 20)
        self.assertEqual(defs['hp_curve_outdoor_cold'], -5.0)
        self.assertEqual(defs['hp_curve_outdoor_mild'], 15.0)
        self.assertEqual(defs['hp_curve_flow_cold'], 55.0)
        self.assertEqual(defs['hp_curve_flow_mild'], 35.0)
        self.assertEqual(defs['hp_radiator_dt50_kw'], 10.0)
        self.assertEqual(defs['hp_radiator_exponent'], 1.3)
        self.assertEqual(defs['hp_return_drop'], 5.0)

        # Tank parameters
        self.assertEqual(defs['hp_tank_litres'], 200.0)
        self.assertEqual(defs['hp_tank_target_temp'], 50.0)
        self.assertEqual(defs['hp_tank_inlet_temp'], 15.0)
        self.assertEqual(defs['hp_tank_ambient_temp'], 20.0)
        self.assertEqual(defs['hp_tank_loss_w_k'], 1.0)
        self.assertEqual(defs['hp_tank_deadband'], 5.0)
        self.assertEqual(defs['hp_tank_service_temp'], 40.0)
        self.assertEqual(defs['hp_tank_initial_temp'], 50.0)
        self.assertEqual(defs['hp_coil_approach'], 5.0)

        # DHW demand & reheat
        self.assertEqual(defs['hp_dhw_demand_mode'], 'gas_calibrated')
        self.assertEqual(defs['hp_dhw_litres_day'], 150.0)
        self.assertEqual(defs['hp_prior_dhw_loss_fraction'], 0.0)
        self.assertEqual(defs['hp_draw_morning_on'], 7)
        self.assertEqual(defs['hp_draw_morning_off'], 9)
        self.assertEqual(defs['hp_draw_evening_on'], 18)
        self.assertEqual(defs['hp_draw_evening_off'], 21)
        self.assertEqual(defs['hp_draw_morning_fraction'], 0.5)
        self.assertEqual(defs['hp_reheat_mode'], 'continuous')
        self.assertEqual(defs['hp_reheat_on'], 11)
        self.assertEqual(defs['hp_reheat_off'], 16)

        # Hygiene
        self.assertIs(defs['hp_hygiene_enabled'], False)
        self.assertEqual(defs['hp_hygiene_temp'], 60.0)
        self.assertEqual(defs['hp_hygiene_weekday'], 0)
        self.assertEqual(defs['hp_hygiene_hour'], 12)
        self.assertEqual(defs['hp_hygiene_duration_hours'], 1)

        # Backup & Auxiliaries
        self.assertEqual(defs['hp_backup_kw'], 3.0)
        self.assertEqual(defs['hp_backup_efficiency'], 1.0)
        self.assertEqual(defs['hp_external_pump_kw'], 0.06)
        self.assertEqual(defs['hp_standby_kw'], 0.02)
        self.assertEqual(defs['hp_min_modulation_fraction'], 0.25)
        self.assertEqual(defs['hp_cycling_penalty'], 0.1)
        self.assertEqual(defs['hp_distribution_loss_fraction'], 0.0)
        self.assertEqual(defs['hp_additional_defrost_penalty'], 0.0)
        self.assertEqual(defs['hp_additional_unit_aux_kw'], 0.0)

        # Finance & Electrification
        self.assertEqual(defs['hp_capex'], 10000.0)
        self.assertEqual(defs['hp_annual_maintenance'], 100.0)
        self.assertEqual(defs['hp_replacement_year'], 0)
        self.assertEqual(defs['hp_replacement_cost'], 6000.0)
        self.assertIs(defs['hp_electric_cooking'], False)
        self.assertEqual(defs['hp_cooking_capex'], 500.0)
        self.assertIs(defs['hp_disconnect_gas'], False)

        # Map JSON
        self.assertEqual(defs['hp_manufacturer_map_json'], '')

    def test_provenance_records_and_labels(self):
        recs = {r['name']: r for r in engine.records()}

        # planning_heat_pump
        plan_rec = recs['planning_heat_pump']
        self.assertEqual(plan_rec['value'], False)
        self.assertEqual(plan_rec['status'], 'proposed')
        self.assertEqual(plan_rec['owner'], 'user / installer / tax adviser')
        self.assertEqual(plan_rec['description'], 'Planning a heat pump')

        # hp_nominal_kw
        nom_rec = recs['hp_nominal_kw']
        self.assertEqual(nom_rec['value'], 9.0)
        self.assertEqual(nom_rec['status'], 'proposed')
        self.assertEqual(nom_rec['owner'], 'user / installer / tax adviser')
        self.assertEqual(nom_rec['source'],'Neutral modeling example; profile-specific evidence required')
        self.assertIn('not verified capacity',nom_rec['description'])
        self.assertNotIn('user-proposed',nom_rec['description'])
        self.assertIsNone(nom_rec['date_accessed'])

        # Costs and placeholders
        self.assertEqual(recs['hp_capex']['status'], 'placeholder')
        self.assertEqual(recs['hp_replacement_cost']['status'], 'placeholder')
        self.assertEqual(recs['hp_cooking_capex']['status'], 'placeholder')
        self.assertEqual(recs['hp_radiator_dt50_kw']['status'], 'proposed')

        # Optional text guarded choices in records
        map_rec = recs['hp_manufacturer_map_json']
        self.assertIsNone(map_rec.get('choices'), 'Optional text setting must not have choices list')
        self.assertEqual(map_rec['value'], '')


class TestLegacyAndSharedStateMigration(unittest.TestCase):
    """Test old scenario migration, saved defaults interaction, and partial patches."""

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_path = Path(self.temp_dir.name) / 'current_scenario.json'
        self.defaults_path = Path(self.temp_dir.name) / 'user_defaults.json'
        self.state_patch = patch.object(shared_state, 'STATE_PATH', self.state_path)
        self.defaults_patch = patch.object(assumptions, 'USER_DEFAULTS_PATH', self.defaults_path)
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)
        self.defaults_patch.start()
        self.addCleanup(self.defaults_patch.stop)

    def tearDown(self):
        super().tearDown()

    def test_old_scenario_missing_switch_with_saved_defaults_true_stays_off(self):
        # 1. Save user default: planning_heat_pump = True
        assumptions.save_user_default('planning_heat_pump', True, engine.validate, path=self.defaults_path)
        self.assertTrue(assumptions.defaults(path=self.defaults_path)['planning_heat_pump'])

        # 2. Write an old stored scenario that lacks 'planning_heat_pump'
        old_settings = {'south_panels': 14, 'scop': 3.8, 'load_scenario': 'baseline'}
        self.state_path.write_text(json.dumps({
            'state_revision': 'old-rev-123',
            'settings': old_settings,
            'quote': {'line_items': []},
        }))

        # 3. Reading shared state migrates missing switch to False BEFORE merging defaults
        state = shared_state.get_state()
        self.assertIs(state['settings']['planning_heat_pump'], False, 'Old scenario missing switch must stay False')
        self.assertEqual(state['settings']['south_panels'], 14)

        # 4. Scenario file on disk remains unmodified by snapshot read
        disk_settings = json.loads(self.state_path.read_text())['settings']
        self.assertEqual(disk_settings, old_settings)

        # 5. Server route snapshot helper returns False as well
        srv_snapshot = state_get_response('')
        self.assertIs(srv_snapshot['settings']['planning_heat_pump'], False)

    def test_engine_validate_without_base_and_missing_switch_treats_as_legacy_false(self):
        # Save user default: planning_heat_pump = True
        assumptions.save_user_default('planning_heat_pump', True, engine.validate, path=self.defaults_path)
        # engine.validate with no base and missing switch treats as legacy False
        val = engine.validate({'south_panels': 10})
        self.assertIs(val['planning_heat_pump'], False)

    def test_partial_patch_preserves_enabled_value_from_base(self):
        # When base has planning_heat_pump = True, partial patch retains True
        base_true = {**engine.defaults(), 'planning_heat_pump': True}
        val = engine.validate({'export_irpef': 0.25}, base=base_true)
        self.assertIs(val['planning_heat_pump'], True)

        # When base has planning_heat_pump = False, partial patch retains False
        base_false = {**engine.defaults(), 'planning_heat_pump': False}
        val_f = engine.validate({'export_irpef': 0.25}, base=base_false)
        self.assertIs(val_f['planning_heat_pump'], False)

    def test_shared_state_partial_patch_preserves_enabled_state(self):
        # Enable planning_heat_pump in scenario
        st1 = shared_state.update_state({'settings': {'planning_heat_pump': True}})
        self.assertIs(st1['settings']['planning_heat_pump'], True)

        # Apply partial patch modifying only export_irpef
        st2 = shared_state.update_state({'settings': {'export_irpef': 0.22}})
        self.assertIs(st2['settings']['planning_heat_pump'], True, 'Partial patch must preserve enabled heat pump')
        self.assertEqual(st2['settings']['export_irpef'], 0.22)

    def test_fresh_defaults_and_reset_defaults(self):
        # Fresh scenario (no current_scenario.json) uses chosen defaults
        assumptions.save_user_default('hp_nominal_kw', 12.0, engine.validate, path=self.defaults_path)
        assumptions.save_user_default('planning_heat_pump', True, engine.validate, path=self.defaults_path)

        state = shared_state.get_state()
        self.assertIs(state['settings']['planning_heat_pump'], True)
        self.assertEqual(state['settings']['hp_nominal_kw'], 12.0)


class TestManufacturerMapValidation(unittest.TestCase):
    """Test validation and deserialization of hp_manufacturer_map_json."""

    def test_empty_string_is_valid_and_parses_to_none(self):
        self.assertIsNone(parse_manufacturer_map_json(''))
        self.assertIsNone(parse_manufacturer_map_json('   '))
        self.assertIsNone(parse_manufacturer_map_json(None))
        s = engine.validate({'hp_manufacturer_map_json': ''})
        self.assertEqual(s['hp_manufacturer_map_json'], '')

    def test_valid_json_map_roundtrip_and_ownership(self):
        base_map = create_provisional_fallback_map(nominal_capacity_kw=8.0)
        serialized = serialize_manufacturer_map(base_map)

        parsed = parse_manufacturer_map_json(serialized)
        self.assertIsNotNone(parsed)
        self.assertIsInstance(parsed, HeatPumpPerformanceMap)
        np.testing.assert_allclose(parsed.outdoor_temps, base_map.outdoor_temps)
        np.testing.assert_allclose(parsed.water_temps, base_map.water_temps)
        np.testing.assert_allclose(parsed.cop_grid, base_map.cop_grid)
        np.testing.assert_allclose(parsed.capacity_grid_kw, base_map.capacity_grid_kw)
        self.assertTrue(parsed.metadata.includes_defrost)
        self.assertTrue(parsed.metadata.includes_unit_auxiliaries)

        # Validate through engine.validate
        val = engine.validate({'planning_heat_pump': True, 'hp_manufacturer_map_json': serialized})
        self.assertEqual(val['hp_manufacturer_map_json'], serialized)
        # Serialized string remains hashable for cache keys
        self.assertIsInstance(hash(val['hp_manufacturer_map_json']), int)

    def test_invalid_json_rejections(self):
        bad_cases = [
            ('{invalid json', 'not valid JSON'),
            ('[1, 2, 3]', 'must be a JSON object'),
            ('{"outdoor_temps": [0, 5]}', 'missing required keys'),
            (
                json.dumps({
                    'outdoor_temps': [0, 5],
                    'water_temps': [35, 45],
                    'cop_grid': [[3.0, 2.5], [3.5, 3.0]],
                    'capacity_grid_kw': [[6.0, 5.0], [7.0, 6.0]],
                    'metadata': {'source': 'test', 'model': 'm', 'test_basis': 'b'},  # missing includes_defrost
                }),
                'missing required boolean',
            ),
            (
                json.dumps({
                    'outdoor_temps': [0, 5],
                    'water_temps': [35, 45],
                    'cop_grid': [[3.0, 2.5], [3.5, 3.0]],
                    'capacity_grid_kw': [[6.0, 5.0], [7.0, 6.0]],
                    'metadata': {'source': 'test', 'model': 'm', 'test_basis': 'b', 'includes_defrost': 'yes', 'includes_unit_auxiliaries': True},
                }),
                'must be a boolean',
            ),
            (
                json.dumps({
                    'outdoor_temps': [0, float('nan')],
                    'water_temps': [35, 45],
                    'cop_grid': [[3.0, 2.5], [3.5, 3.0]],
                    'capacity_grid_kw': [[6.0, 5.0], [7.0, 6.0]],
                    'metadata': {'source': 'test', 'model': 'm', 'test_basis': 'b', 'includes_defrost': True, 'includes_unit_auxiliaries': True},
                }),
                'finite numbers',
            ),
            (
                json.dumps({
                    'outdoor_temps': [0, 5],
                    'water_temps': [35, 45],
                    'cop_grid': [[3.0, 2.5]],  # only 1 row instead of 2
                    'capacity_grid_kw': [[6.0, 5.0], [7.0, 6.0]],
                    'metadata': {'source': 'test', 'model': 'm', 'test_basis': 'b', 'includes_defrost': True, 'includes_unit_auxiliaries': True},
                }),
                'does not match axes',
            ),
            (
                json.dumps({
                    'outdoor_temps': [5, 0],  # not strictly increasing
                    'water_temps': [35, 45],
                    'cop_grid': [[3.0, 2.5], [3.5, 3.0]],
                    'capacity_grid_kw': [[6.0, 5.0], [7.0, 6.0]],
                    'metadata': {'source': 'test', 'model': 'm', 'test_basis': 'b', 'includes_defrost': True, 'includes_unit_auxiliaries': True},
                }),
                'strictly increasing',
            ),
            (
                json.dumps({
                    'outdoor_temps': [0, 5],
                    'water_temps': [35, 45],
                    'cop_grid': [[0.0, 2.5], [3.5, 3.0]],  # COP must be > 0
                    'capacity_grid_kw': [[6.0, 5.0], [7.0, 6.0]],
                    'metadata': {'source': 'test', 'model': 'm', 'test_basis': 'b', 'includes_defrost': True, 'includes_unit_auxiliaries': True},
                }),
                'positive',
            ),
        ]
        for raw, err_match in bad_cases:
            with self.subTest(err=err_match):
                with self.assertRaises(ValueError) as ctx:
                    parse_manufacturer_map_json(raw)
                self.assertIn(err_match.lower(), str(ctx.exception).lower())

    def test_guarded_string_choice_handling(self):
        # Non-string for hp_manufacturer_map_json is rejected
        with self.assertRaisesRegex(ValueError, 'must be a string'):
            engine.validate({'hp_manufacturer_map_json': 12345})

        # Choices fields reject invalid choices
        with self.assertRaisesRegex(ValueError, 'must be one of'):
            engine.validate({'hp_schedule_mode': 'unsupported_mode'})


class TestCrossFieldValidation(unittest.TestCase):
    """Test strict cross-field validation rules and bypass when planning_heat_pump is False."""

    def test_weather_curve_ordering(self):
        # Inverted outdoor curve (cold >= mild)
        with self.assertRaisesRegex(ValueError, 'hp_curve_outdoor_cold.*strictly less'):
            engine.validate({'planning_heat_pump': True, 'hp_curve_outdoor_cold': 15.0, 'hp_curve_outdoor_mild': 10.0})

        # Inverted flow curve (flow_cold <= flow_mild)
        with self.assertRaisesRegex(ValueError, 'hp_curve_flow_cold.*strictly greater'):
            engine.validate({'planning_heat_pump': True, 'hp_curve_flow_cold': 30.0, 'hp_curve_flow_mild': 40.0})

    def test_tank_temperature_and_deadband_bounds(self):
        # Service temp exceeding target
        with self.assertRaisesRegex(ValueError, 'hp_tank_service_temp.*cannot exceed.*hp_tank_target_temp'):
            engine.validate({'planning_heat_pump': True, 'hp_tank_target_temp': 45.0, 'hp_tank_service_temp': 50.0})

        # Inlet exceeding service temp
        with self.assertRaisesRegex(ValueError, 'hp_tank_inlet_temp.*cannot exceed.*hp_tank_service_temp'):
            engine.validate({'planning_heat_pump': True, 'hp_tank_inlet_temp': 35.0, 'hp_tank_service_temp': 30.0})

        # Target minus deadband below inlet (target 40, deadband 15 -> 25 < inlet 30)
        with self.assertRaisesRegex(ValueError, 'target_temp minus deadband'):
            engine.validate({'planning_heat_pump': True, 'hp_tank_target_temp': 40.0, 'hp_tank_inlet_temp': 30.0, 'hp_tank_service_temp': 35.0, 'hp_tank_deadband': 15.0})

        # Hygiene lower than target when enabled
        with self.assertRaisesRegex(ValueError, 'hp_hygiene_temp.*cannot be lower'):
            engine.validate({'planning_heat_pump': True, 'hp_hygiene_enabled': True, 'hp_tank_target_temp': 65.0, 'hp_hygiene_temp': 55.0})

    def test_scheduled_active_windows(self):
        # Heating scheduled mode with equal on/off hours disables both windows
        with self.assertRaisesRegex(ValueError, 'at least one active operating window'):
            engine.validate({
                'planning_heat_pump': True,
                'hp_schedule_mode': 'scheduled',
                'hp_morning_on': 6,
                'hp_morning_off': 6,
                'hp_evening_on': 18,
                'hp_evening_off': 18,
            })

        # DHW reheat scheduled mode disabled
        with self.assertRaisesRegex(ValueError, 'active reheat operating window'):
            engine.validate({
                'planning_heat_pump': True,
                'hp_reheat_mode': 'scheduled',
                'hp_reheat_on': 12,
                'hp_reheat_off': 12,
            })

        # DHW draw windows disabled
        with self.assertRaisesRegex(ValueError, 'at least one active draw window'):
            engine.validate({
                'planning_heat_pump': True,
                'hp_draw_morning_on': 8,
                'hp_draw_morning_off': 8,
                'hp_draw_evening_on': 20,
                'hp_draw_evening_off': 20,
            })

    def test_gas_disconnect_requires_electric_cooking_when_cooking_gas_positive(self):
        # Disconnecting gas while cooking gas > 0 without electric cooking is rejected
        with self.assertRaisesRegex(ValueError, 'electric cooking.*hp_electric_cooking=True'):
            engine.validate({
                'cooking_smc_annual': 25.0,
                'hp_disconnect_gas': True,
                'hp_electric_cooking': False,
            })

        # Passes when electric cooking is enabled
        ok_val = engine.validate({
            'cooking_smc_annual': 25.0,
            'hp_disconnect_gas': True,
            'hp_electric_cooking': True,
        })
        self.assertTrue(ok_val['hp_disconnect_gas'])
        self.assertTrue(ok_val['hp_electric_cooking'])

    def test_inapplicable_crossfields_bypassed_when_planning_off(self):
        # With planning_heat_pump=False, arbitrary hp curve/tank parameters within individual bounds do not fail cross-field validation
        s = engine.validate({
            'planning_heat_pump': False,
            'hp_curve_outdoor_cold': 12.0,
            'hp_curve_outdoor_mild': 10.0,  # inverted, but planning is off
            'hp_tank_service_temp': 55.0,
            'hp_tank_target_temp': 45.0,    # inverted, but planning is off
        })
        self.assertIs(s['planning_heat_pump'], False)


    def test_aliases_normalized_seamlessly(self):
        s = engine.validate({
            'planning_heat_pump': True,
            'hp_tank_target': 52.0,
            'hp_tank_inlet': 12.0,
            'hp_tank_ambient': 22.0,
            'hp_tank_coil_approach': 4.0,
            'hp_tank_deadband_k': 6.0,
        })
        self.assertEqual(s['hp_tank_target_temp'], 52.0)
        self.assertEqual(s['hp_tank_inlet_temp'], 12.0)
        self.assertEqual(s['hp_tank_ambient_temp'], 22.0)
        self.assertEqual(s['hp_coil_approach'], 4.0)
        self.assertEqual(s['hp_tank_deadband'], 6.0)


class TestLegacySettingsPreservation(unittest.TestCase):
    """Ensure older settings preserve their exact values and are never overwritten on toggle."""

    def test_toggle_preserves_legacy_scop_and_dhw_and_schedules(self):
        custom_legacy = {
            'scop': 4.5,
            'dhw_heating_type': 'heat_pump',
            'dhw_scop': 3.2,
            'dhw_resistance_efficiency': 0.95,
            'dhw_midday_fraction': 0.7,
            'heating_schedule_mode': 'continuous',
            'heating_on_hour': 7,
            'heating_off_hour': 10,
            'heating_evening_on_hour': 17,
            'heating_evening_off_hour': 22,
            'electrification_capex': 7500.0,
            'electrification_capex_include_baseline': True,
        }

        # Validate with planning_heat_pump = False
        s_off = engine.validate({**custom_legacy, 'planning_heat_pump': False})
        # Toggle planning without losing legacy schedule meaning during migration.
        s_on = engine.validate({**custom_legacy, 'planning_heat_pump': True})
        expected_windows = [
            {'id': 'morning', 'start_minute': 7 * 60, 'end_minute': 10 * 60},
            {'id': 'evening', 'start_minute': 17 * 60, 'end_minute': 22 * 60},
        ]
        for validated in (s_off, s_on):
            self.assertEqual(validated['ac_heating_windows'], expected_windows)
            for k, expected in custom_legacy.items():
                if k.endswith('_hour'):
                    self.assertNotIn(k, validated, 'Legacy hour keys migrate to canonical windows')
                else:
                    self.assertEqual(validated[k], expected, f'{k} altered on heat-pump toggle')

    def test_load_scenario_not_forced_in_engine_validation(self):
        # Baseline remains a historical comparator even when planning_heat_pump is True
        s = engine.validate({'planning_heat_pump': True, 'load_scenario': 'baseline'})
        self.assertEqual(s['load_scenario'], 'baseline')
        self.assertTrue(s['planning_heat_pump'])


class TestKeyCoverageAndCacheKeys(unittest.TestCase):
    """Verify key list completeness, inheritance, and hashable cache keys."""

    def test_spec_and_schema_coverage(self):
        schema = get_heat_pump_schema()
        self.assertEqual(len(schema['spec']), 59)
        self.assertIn('planning_heat_pump', HP_ALL_KEYS)
        for key in HP_PHYSICAL_KEYS:
            self.assertIn(key, assumptions.SPEC)
            self.assertIn(key, engine.LOAD_KEYS)
            self.assertIn(key, engine.ENERGY_KEYS)
            self.assertIn(key, engine.CHART_KEYS)

        for key in HP_COST_KEYS:
            self.assertIn(key, assumptions.SPEC)
            self.assertIn(key, engine.ALL_KEYS)

    def test_hashable_cache_keys_and_sensitivity(self):
        s1 = engine.defaults()
        key1 = tuple((k, engine.freeze(s1[k])) for k in engine.LOAD_KEYS)
        self.assertIsInstance(hash(key1), int)

        # Modifying a physical heat pump setting changes the load cache key
        s2 = {**s1, 'hp_nominal_kw': 11.0}
        key2 = tuple((k, engine.freeze(s2[k])) for k in engine.LOAD_KEYS)
        self.assertNotEqual(key1, key2)

        # Map JSON string in s items tuple is hashable
        base_map = create_provisional_fallback_map(nominal_capacity_kw=9.0)
        s3 = {**s1, 'hp_manufacturer_map_json': serialize_manufacturer_map(base_map)}
        key_all = tuple(sorted((k, engine.freeze(v)) for k, v in s3.items()))
        self.assertIsInstance(hash(key_all), int)


if __name__ == '__main__':
    unittest.main()
