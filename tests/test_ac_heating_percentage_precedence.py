"""Tests for ac_heating_additional_demand_pct precedence in shared state.

Validates the precedence hierarchy:
incoming explicit > saved in scenario > user defaults overrides > factory default.

Also preserves:
- Deliberate old complete full-replacement export missing pct -> 0.0
- Rejection of full replacement when unrelated required fields are missing
- Non-rewriting of legacy saved scenario files on read
"""
import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
sys.path.insert(0, str(PROJECT_ROOT / 'feasibility' / 'vendor'))
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(HERE))

import test_support  # Installs live path write guards
from test_support import IsolatedDefaultsTestCase

from feasibility import assumptions, engine, shared_state


class AcHeatingPercentagePrecedenceTests(IsolatedDefaultsTestCase):
    """Test percentage precedence: incoming > saved > user overrides > factory."""

    def test_no_saved_state_default25_unrelated_first_edit_retains25(self):
        """No saved state, user default 25, unrelated first edit retains 25."""
        self.assertFalse(self._state_path.exists())

        # Save user default via real save_user_default and verify metadata
        entry = assumptions.save_user_default('ac_heating_additional_demand_pct', 25, engine.validate)
        self.assertEqual(entry['value'], 25)
        self.assertEqual(entry['status'], 'provided')
        self.assertEqual(entry['owner'], 'user')
        self.assertIn('date', entry)
        self.assertEqual(entry['factory_default'], 0)

        # Raw file check for metadata completeness
        stored = json.loads(self._defaults_path.read_text())['overrides']
        self.assertIn('ac_heating_additional_demand_pct', stored)
        self.assertEqual(stored['ac_heating_additional_demand_pct']['value'], 25)

        # Confirm defaults and get_state show 25 before any saved scenario exists
        self.assertEqual(assumptions.persisted_defaults()['ac_heating_additional_demand_pct'], 25.0)
        initial = shared_state.get_state()
        self.assertEqual(initial['settings']['ac_heating_additional_demand_pct'], 25.0)
        self.assertFalse(self._state_path.exists(), 'get_state does not create scenario file')

        # First unrelated edit must retain user default 25
        updated = shared_state.update_state({'settings': {'scop': 4.0}})
        self.assertEqual(updated['settings']['scop'], 4.0)
        self.assertEqual(updated['settings']['ac_heating_additional_demand_pct'], 25.0)

        # Subsequent reads back from disk retain 25
        reloaded = shared_state.get_state()
        self.assertEqual(reloaded['settings']['ac_heating_additional_demand_pct'], 25.0)

    def test_legacy_saved_scenario_missing_pct_default25_read_inherits25_bytes_unchanged_unrelated_patch_retains25(self):
        """Legacy saved scenario missing pct, default 25, read inherits 25 and file bytes unchanged, unrelated patch retains 25."""
        # Save user default 25
        assumptions.save_user_default('ac_heating_additional_demand_pct', 25, engine.validate)

        # Create legacy saved scenario without ac_heating_additional_demand_pct
        legacy_settings = {'scop': 4.0, 'load_scenario': 'electrified', 'loan_fraction': 0.4}
        legacy_state = {
            'state_revision': 'legacy-rev-001',
            'settings': legacy_settings,
            'quote': {'line_items': []},
        }
        self._state_path.write_text(json.dumps(legacy_state, indent=2) + '\n')
        raw_bytes_before = self._state_path.read_bytes()

        # Read must inherit user default 25 without modifying the file on disk
        read_snapshot = shared_state.get_state()
        self.assertEqual(read_snapshot['settings']['ac_heating_additional_demand_pct'], 25.0)
        self.assertEqual(self._state_path.read_bytes(), raw_bytes_before, 'read migration must not rewrite legacy saved file')
        self.assertEqual(json.loads(self._state_path.read_text())['settings'], legacy_settings)

        # Unrelated partial patch must retain effective user default 25
        updated = shared_state.update_state({'settings': {'export_irpef': 0.22}})
        self.assertEqual(updated['settings']['export_irpef'], 0.22)
        self.assertEqual(updated['settings']['ac_heating_additional_demand_pct'], 25.0)
        self.assertEqual(shared_state.get_state()['settings']['ac_heating_additional_demand_pct'], 25.0)

    def test_explicit_saved0_beats_default25(self):
        """Explicit saved percentage 0 in scenario overrides user default 25."""
        assumptions.save_user_default('ac_heating_additional_demand_pct', 25, engine.validate)
        self.assertEqual(assumptions.persisted_defaults()['ac_heating_additional_demand_pct'], 25.0)

        # Save scenario with explicit 0
        saved_settings = {'scop': 4.0, 'load_scenario': 'electrified', 'ac_heating_additional_demand_pct': 0}
        saved_state = {
            'state_revision': 'rev-explicit-0',
            'settings': saved_settings,
            'quote': {'line_items': []},
        }
        self._state_path.write_text(json.dumps(saved_state, indent=2) + '\n')

        # Read must reflect saved 0, not default 25
        snapshot = shared_state.get_state()
        self.assertEqual(snapshot['settings']['ac_heating_additional_demand_pct'], 0.0)

        # Unrelated patch must keep explicit saved 0
        updated = shared_state.update_state({'settings': {'export_irpef': 0.22}})
        self.assertEqual(updated['settings']['ac_heating_additional_demand_pct'], 0.0)
        self.assertEqual(shared_state.get_state()['settings']['ac_heating_additional_demand_pct'], 0.0)

    def test_incoming_explicit50_beats_saved0_and_default25(self):
        """Incoming explicit percentage 50 overrides both saved 0 and user default 25."""
        assumptions.save_user_default('ac_heating_additional_demand_pct', 25, engine.validate)

        # Save scenario with explicit 0
        saved_settings = {'scop': 4.0, 'load_scenario': 'electrified', 'ac_heating_additional_demand_pct': 0}
        self._state_path.write_text(json.dumps({
            'state_revision': 'rev-explicit-0',
            'settings': saved_settings,
            'quote': {'line_items': []},
        }, indent=2) + '\n')

        # Incoming patch with explicit 50
        updated = shared_state.update_state({'settings': {'ac_heating_additional_demand_pct': 50}})
        self.assertEqual(updated['settings']['ac_heating_additional_demand_pct'], 50.0)

        # Snapshot reloaded from disk retains 50
        snapshot = shared_state.get_state()
        self.assertEqual(snapshot['settings']['ac_heating_additional_demand_pct'], 50.0)

    def test_no_override_defaults0(self):
        """When no user default override exists, factory default 0 is inherited."""
        self.assertEqual(assumptions.persisted_defaults()['ac_heating_additional_demand_pct'], 0.0)

        # No saved state inherits factory 0
        snapshot = shared_state.get_state()
        self.assertEqual(snapshot['settings']['ac_heating_additional_demand_pct'], 0.0)

        # First unrelated edit retains factory 0
        updated = shared_state.update_state({'settings': {'scop': 4.0}})
        self.assertEqual(updated['settings']['ac_heating_additional_demand_pct'], 0.0)

        # Legacy saved scenario missing pct also inherits factory 0
        legacy_settings = {'scop': 4.0, 'load_scenario': 'electrified'}
        self._state_path.write_text(json.dumps({
            'state_revision': 'rev-factory-0',
            'settings': legacy_settings,
            'quote': {'line_items': []},
        }, indent=2) + '\n')
        self.assertEqual(shared_state.get_state()['settings']['ac_heating_additional_demand_pct'], 0.0)

        # Unrelated patch retains factory 0
        updated2 = shared_state.update_state({'settings': {'export_irpef': 0.22}})
        self.assertEqual(updated2['settings']['ac_heating_additional_demand_pct'], 0.0)

    def test_old_complete_full_replacement_missing_pct_still0_and_unrelated_missing_field_rejected(self):
        """Old complete full replacement missing pct still migrates to 0, unrelated missing fields rejected."""
        assumptions.save_user_default('ac_heating_additional_demand_pct', 25, engine.validate)
        self.assertEqual(assumptions.persisted_defaults()['ac_heating_additional_demand_pct'], 25.0)

        all_defs = dict(assumptions.persisted_defaults())
        self.assertIn('ac_heating_additional_demand_pct', all_defs)
        del all_defs['ac_heating_additional_demand_pct']

        # Historical full replacement without pct migrates to 0.0 (not user default 25)
        res = shared_state.update_state(
            {'settings': all_defs, 'quote': {'line_items': []}, 'replace': True}
        )
        self.assertEqual(res['settings']['ac_heating_additional_demand_pct'], 0.0)

        # Full replacement missing an unrelated required field (e.g. scop) is rejected
        broken_export = dict(all_defs)
        del broken_export['scop']
        with self.assertRaisesRegex(ValueError, 'replace settings must include all inputs'):
            shared_state.update_state(
                {'settings': broken_export, 'quote': {'line_items': []}, 'replace': True}
            )


if __name__ == '__main__':
    unittest.main()
