"""Regression for common-reference PV and storage reporting."""
from test_support import IsolatedDefaultsTestCase
import feasibility.engine as e


class TestPVReportingReference(IsolatedDefaultsTestCase):
    def test_physical_dc_grid_first_reporting(self):
        m=e.dispatch_matrix({
            'pv_dispatch_model':'physical','pv_battery_coupling':'dc_coupled',
            'routing_priority':'grid_first','south_panels':30,'panel_watts':480,
            'inverter_kw':6,'inverter_efficiency':.972,'battery_kwh':9.73,
            'horizon_years':2,
        })
        af=m['annual_flows']
        self.assertEqual(af['energy_reference'],'ac_equivalent')
        self.assertGreater(af['battery_charge_physical_kwh'],0.)
        for row in m['monthly_clipping']:
            self.assertAlmostEqual(row['pv_input_kwh'],row['pv_delivered_kwh']+row['clipped_kwh'],delta=1e-7)
            self.assertLessEqual(row['pv_delivered_kwh'],row['pv_input_kwh'])
        self.assertAlmostEqual(af['pv_input_kwh'],af['pv_generation_kwh']+af['clipped_kwh'],delta=1e-7)
        self.assertAlmostEqual(af['pv_generation_kwh'],af['direct_use_kwh']+af['battery_charge_kwh']+af['grid_export_kwh'],delta=.2)
        self.assertAlmostEqual(af['load_demand_kwh'],af['direct_use_kwh']+af['battery_discharge_kwh']+af['grid_import_kwh'],delta=.2)
        self.assertAlmostEqual(af['battery_charge_kwh']-af['battery_discharge_kwh'],af['storage_losses_kwh']+af['storage_delta_kwh'],delta=.2)
        self.assertAlmostEqual(af['battery_charge_kwh'],.972*af['battery_charge_physical_kwh'],delta=.1)
