"""Shared scenario API state is atomic, validated, and independent of defaults."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from feasibility import assumptions, engine, shared_state
from feasibility.server import state_get_response, state_post_response
from test_support import IsolatedDefaultsTestCase


class SharedStateTests(IsolatedDefaultsTestCase):
    def test_future_export_factor_controls_have_user_facing_ramp_help(self):
        records = {record['name']: record for record in assumptions.persisted_records()}
        target = records['actual_price_future_factor']
        duration = records['export_price_future_years']
        self.assertEqual(duration['value'], 10)
        self.assertEqual(duration['unit'], 'years')
        self.assertEqual(target['group'], duration['group'])
        self.assertLess(list(records).index('actual_price_future_factor'), list(records).index('export_price_future_years'))
        self.assertIn('moves linearly to this target over the selected ramp duration', target['description'])
        self.assertIn('0.75 means export prices are 25% lower once the ramp finishes', target['description'])
        self.assertIn('Number of years for the future export-price factor to reach its target', duration['description'])
        self.assertIn('holds there after the ramp', duration['description'])
        self.assertIn('not import prices or retail self-consumption', duration['description'])

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state_path = Path(self.temp.name) / 'current_scenario.json'
        self.defaults_path = Path(self.temp.name) / 'user_defaults.json'
        self.state_patch = patch.object(shared_state, 'STATE_PATH', self.state_path)
        self.defaults_patch = patch.object(assumptions, 'USER_DEFAULTS_PATH', self.defaults_path)
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)
        self.defaults_patch.start()
        self.addCleanup(self.defaults_patch.stop)

    def tearDown(self):
        super().tearDown()

    def test_initial_state_persists_and_revision_supports_unchanged_poll(self):
        initial = shared_state.get_state()
        self.assertFalse(self.state_path.exists(), 'initial reads do not persist defaults')
        self.assertEqual(initial['settings'], assumptions.persisted_defaults())
        self.assertEqual(initial['quote'], {'line_items': []})
        unchanged = shared_state.get_state(initial['revision'])
        self.assertEqual(unchanged, {'revision': initial['revision'], 'unchanged': True})

        saved = shared_state.update_state({'settings': {'export_irpef': .27}})
        restarted_read = shared_state.get_state()
        self.assertNotEqual(saved['revision'], initial['revision'])
        self.assertEqual(restarted_read['revision'], saved['revision'])
        self.assertEqual(restarted_read['settings']['export_irpef'], .27)
        self.assertEqual(json.loads(self.state_path.read_text())['settings']['export_irpef'], .27)

    def test_old_saved_scenarios_gain_new_schema_defaults_on_read(self):
        old_settings={'loan_fraction':.4,'load_scenario':'electrified','scop':4.0}
        self.state_path.write_text(json.dumps({'settings':old_settings,'quote':{'line_items':[]},'state_revision':'old-schema'}))
        state=shared_state.get_state()
        self.assertEqual(state['settings']['loan_fraction'],.4)
        self.assertEqual(state['settings']['loan_mode'],assumptions.persisted_defaults()['loan_mode'])
        self.assertEqual(state['settings']['dhw_heating_type'],'resistance')
        self.assertEqual(state['settings']['export_price_future_years'],10)
        self.assertEqual(state['settings']['summer_ac_multiplier'],1.0)
        self.assertEqual(json.loads(self.state_path.read_text())['settings'],old_settings,'read migration does not rewrite user scenarios')

    def test_disjoint_patches_from_stale_clients_merge_against_latest(self):
        stale_a = shared_state.get_state()
        stale_b = shared_state.get_state()
        self.assertEqual(stale_a['revision'], stale_b['revision'])
        first = shared_state.update_state({'settings': {'export_irpef': .22}})
        second = shared_state.update_state({'settings': {'inflation': .03}})
        self.assertNotEqual(first['revision'], second['revision'])
        self.assertEqual(second['settings']['export_irpef'], .22)
        self.assertEqual(second['settings']['inflation'], .03)

    def test_blank_pv_array_limit_stays_dynamic_across_persisted_inverter_edits(self):
        state = shared_state.update_state({'settings': {'inverter_kw': 6.0, 'pv_max_kwp': None}})
        self.assertIsNone(state['settings']['pv_max_kwp'])
        self.assertIsNone(json.loads(self.state_path.read_text())['settings']['pv_max_kwp'])
        updated = shared_state.update_state({'settings': {'inverter_kw': 8.0}})
        self.assertEqual(updated['settings']['inverter_kw'], 8.0)
        self.assertIsNone(updated['settings']['pv_max_kwp'])

    def test_battery_module_patches_derive_total_and_explicit_total_wins(self):
        first=shared_state.update_state({'settings': {'battery_modules': 3, 'battery_module_kwh': 8}})
        self.assertEqual(first['settings']['battery_kwh'],24)
        second=shared_state.update_state({'settings': {'battery_modules': 4}})
        self.assertEqual(second['settings']['battery_kwh'],32)
        explicit=shared_state.update_state({'settings': {'battery_modules': 2, 'battery_module_kwh': 9, 'battery_kwh': 15}})
        self.assertEqual(explicit['settings']['battery_kwh'],15)
        self.assertEqual(explicit['settings']['battery_module_kwh'],7.5)
        self.assertEqual(json.loads(self.state_path.read_text())['settings']['battery_kwh'],15)

    def test_module_patch_migrates_legacy_saved_settings_before_merging(self):
        legacy={'south_panels':12,'battery_kwh':18}
        self.state_path.write_text(json.dumps({'settings':legacy,'quote':{'line_items':[]},'state_revision':'legacy'}))
        state=shared_state.update_state({'settings': {'battery_modules': 2}})
        self.assertEqual(state['settings']['battery_kwh'],10)
        self.assertEqual(state['settings']['battery_module_kwh'],5)

    def test_legacy_scenario_migrates_module_metadata_without_rewriting_file(self):
        legacy={'south_panels':35,'panel_watts':520,'battery_kwh':18}
        self.state_path.write_text(json.dumps({'settings':legacy,'quote':{'line_items':[]},'state_revision':'legacy'}))
        state=shared_state.get_state()
        self.assertEqual(state['settings']['battery_kwh'],18)
        self.assertEqual(state['settings']['battery_modules']*state['settings']['battery_module_kwh'],18)
        self.assertEqual(json.loads(self.state_path.read_text())['settings'],legacy)

    def test_saved_module_default_changes_derived_default_total(self):
        total_entry=assumptions.save_user_default('battery_kwh',12,engine.validate,path=self.defaults_path)
        self.assertEqual(total_entry['value'],12)
        self.assertEqual(assumptions.persisted_defaults()['battery_module_kwh'],12)
        module_entry=assumptions.save_user_default('battery_modules',4,engine.validate,path=self.defaults_path)
        self.assertEqual(module_entry['value'],4,'the returned default must be the requested module-count record')
        self.assertEqual(assumptions.persisted_defaults()['battery_kwh'],48)
        capacity_entry=assumptions.save_user_default('battery_module_kwh',7,engine.validate,path=self.defaults_path)
        self.assertEqual(capacity_entry['value'],7,'the returned default must be the requested per-module capacity record')
        defaults=assumptions.persisted_defaults()
        self.assertEqual(defaults['battery_kwh'],28)
        self.assertEqual(defaults['battery_modules']*defaults['battery_module_kwh'],28)
        stored=json.loads(self.defaults_path.read_text())['overrides']
        self.assertEqual({stored[k]['value'] for k in ['battery_kwh','battery_modules','battery_module_kwh']},{28,4,7})

    def test_overflowing_module_product_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Battery counts and capacities'):
            engine.validate({'battery_modules': 2, 'battery_module_kwh': 1e308})

    def test_invalid_updates_leave_last_valid_file_untouched(self):
        valid = shared_state.update_state({'settings': {'export_irpef': .27}})
        before = self.state_path.read_bytes()
        for bad in [
            {'settings': {'unrecognized': 1}},
            {'settings': {'south_panels': 0, 'battery_kwh': 5}},
            {'quote': {'line_items': [None]}},
            {'replace': True, 'settings': {'export_irpef': .2}, 'quote': {}},
            {'settings': []},
        ]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                shared_state.update_state(bad)
            self.assertEqual(self.state_path.read_bytes(), before)
            self.assertEqual(shared_state.get_state()['revision'], valid['revision'])

    def test_quote_updates_merge_fields_but_replace_line_items_as_an_array(self):
        first = shared_state.update_state({'quote': {
            'line_items': [{'description': 'PV', 'net_eur': 1000}],
            'battery_nameplate_kwh': 10,
            'battery_dod': .8,
        }})
        self.assertEqual(first['settings']['battery_kwh'], assumptions.persisted_defaults()['battery_kwh'])
        second = shared_state.update_state({'quote': {'line_items': []}})
        self.assertEqual(second['quote']['line_items'], [])
        self.assertEqual(second['quote']['battery_nameplate_kwh'], 10)
        normalized = engine.quote_evaluate(second['settings'], second['quote'])
        self.assertEqual(normalized['result']['settings']['battery_kwh'], 8)

    def test_replace_requires_full_settings_and_updates_whole_scenario(self):
        partial = {'replace': True, 'settings': {'export_irpef': .2}, 'quote': {'line_items': []}}
        with self.assertRaisesRegex(ValueError, 'all inputs'):
            shared_state.update_state(partial)
        full = assumptions.persisted_defaults()
        full['export_irpef'] = .2
        result = shared_state.update_state({'replace': True, 'settings': full, 'quote': {'line_items': []}})
        self.assertEqual(result['settings'], full)
        self.assertEqual(result['quote'], {'line_items': []})

    def test_saved_default_updates_revision_without_overwriting_current_scenario(self):
        current = shared_state.update_state({'settings': {'export_irpef': .31}})
        assumptions.save_user_default('export_irpef', .22, engine.validate, path=self.defaults_path)
        after_default = shared_state.get_state()
        self.assertNotEqual(current['revision'], after_default['revision'])
        self.assertEqual(after_default['settings']['export_irpef'], .31)
        self.assertEqual(after_default['defaults']['export_irpef'], .22)
        record = next(row for row in after_default['records'] if row['name'] == 'export_irpef')
        self.assertEqual(record['value'], .22)
        self.assertEqual(record['source'], 'User-saved shared app default')

    def test_server_state_route_helpers_return_snapshot_and_unchanged_shape(self):
        initial = state_get_response('')
        self.assertTrue({'revision', 'settings', 'quote', 'defaults', 'records'} <= set(initial))
        unchanged = state_get_response('revision=' + initial['revision'])
        self.assertEqual(unchanged, {'revision': initial['revision'], 'unchanged': True})
        updated = state_post_response({'settings': {'export_irpef': .29}})
        self.assertEqual(updated['settings']['export_irpef'], .29)
        self.assertNotEqual(updated['revision'], initial['revision'])


if __name__ == '__main__':
    unittest.main()
