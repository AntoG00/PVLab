import json
import unittest

from feasibility import assumptions, engine
from tests import test_support


class UserDefaultsTests(test_support.IsolatedDefaultsTestCase):
    def setUp(self):
        super().setUp()
        self.path = self._defaults_path

    def test_save_is_atomic_preserves_factory_and_other_keys(self):
        self.assertEqual(assumptions.persisted_defaults()['deduction_tax_capacity'],assumptions.SPEC['deduction_tax_capacity'][0])
        saved=assumptions.save_user_default('deduction_tax_capacity',12000,engine.validate,path=self.path)
        self.assertEqual(saved,{'value':12000,'status':'provided','date':saved['date'],'owner':'user','factory_default':assumptions.SPEC['deduction_tax_capacity'][0]})
        self.assertEqual(assumptions.persisted_defaults()['deduction_tax_capacity'],12000)
        record=next(r for r in assumptions.persisted_records() if r['name']=='deduction_tax_capacity')
        self.assertEqual((record['value'],record['status'],record['owner'],record['factory_default']),(12000,'provided','user',assumptions.SPEC['deduction_tax_capacity'][0]))

        assumptions.save_user_default('load_scenario','ev',engine.validate,path=self.path)
        saved_file=json.loads(self.path.read_text())
        self.assertEqual(set(saved_file['overrides']),{'deduction_tax_capacity','load_scenario'})
        self.assertEqual(saved_file['overrides']['deduction_tax_capacity']['value'],12000)
        self.assertEqual(assumptions.persisted_defaults()['load_scenario'],'ev')

    def test_cross_constraint_rejection_does_not_write_or_overwrite(self):
        assumptions.save_user_default('deduction_tax_capacity',12000,engine.validate,path=self.path)
        before=self.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'Battery without PV'):
            assumptions.save_user_default('south_panels',0,engine.validate,path=self.path)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(assumptions.persisted_defaults()['south_panels'],assumptions.SPEC['south_panels'][0])

    def test_battery_context_validates_against_stored_defaults(self):
        assumptions.save_user_default('battery_modules', 0, engine.validate, path=self.path)
        assumptions.save_user_default('south_panels', 0, engine.validate, path=self.path)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'Battery without PV'):
            assumptions.save_user_default('battery_modules', 1, engine.validate, path=self.path,
                                          battery_settings={'battery_modules': 1, 'battery_kwh': 5,
                                                            'battery_module_kwh': 5})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertGreater(engine.defaults()['south_panels'], 0)


if __name__=='__main__':
    unittest.main()
