"""Chart aggregates must preserve full hourly dispatch and measured reference."""
import unittest, json
import numpy as np
from feasibility import engine as e
from test_support import IsolatedDefaultsTestCase

class ChartTests(IsolatedDefaultsTestCase):
    def test_all_weather_monthly_and_dispatch_conservation(self):
        settings={'south_panels':9,'west_panels':3,'battery_kwh':4,'inverter_kw':4,'load_growth':1.12,'ev_start_year':1}
        charts=e.chart_data(settings)
        self.assertEqual(charts['weather_count'],e.weather_period().count)
        np.testing.assert_allclose([m['median'] for m in charts['measured']['monthly']],e.data()['audit']['monthly_kwh'])
        self.assertEqual(charts['selected_config']['south_panels'],9)
        self.assertEqual(charts['selected_config']['inverter_kw'],4)
        for case in charts['scenarios']:
            result=e.evaluate({**settings,'load_scenario':case['id'],'horizon_years':1})
            raw=np.asarray(case['annual_dispatch_by_weather'])
            np.testing.assert_allclose(raw[:,1]+raw[:,5],raw[:,0]+raw[:,6]+raw[:,7]+raw[:,8],atol=1e-7)
            for field in e.CHART_FIELDS:
                series=case['series'][field]
                np.testing.assert_allclose(np.asarray(series['monthly_by_weather']).sum(axis=1),series['annual_by_weather'],atol=1e-7)
                np.testing.assert_allclose(series['annual_by_weather'],raw[:,e.FIELDS.index(field)],atol=1e-7)
                for key in ['min','median','mean','max']:
                    self.assertAlmostEqual(series['annual'][key],result['energy'][field][key],places=6)
        json.dumps(charts,allow_nan=False)
    def test_delayed_ev_and_roof_controls(self):
        charts=e.chart_data({'ev_start_year':4,'shade_strength':.6,'diffuse_suppression':.25})
        self.assertFalse(charts['ev_active'])
        self.assertEqual(charts['scenarios'][1]['series'],charts['scenarios'][2]['series'])
        d=e.data();expected=np.maximum(d['power']-.6*(d['beam_loss']+.25*d['diffuse_loss']),0).sum(axis=2)
        for i,roof in enumerate(charts['roofs']):
            self.assertAlmostEqual(roof['selected_pvgis_ac']['median'],np.median(expected[i]))
        default=e.chart_data()
        for roof in default['roofs']:
            self.assertAlmostEqual(roof['selected_pvgis_ac']['median'],roof['pvgis_beam_shaded']['median'],places=6)
    def test_missing_google_comparisons_remain_missing(self):
        roofs=e.chart_data()['roofs']
        flat=next(r for r in roofs if r['orientation']=='flat')
        self.assertIsNone(flat['google_dc_kwh_kwp'])
        self.assertIsNone(flat['google_ac_kwh_kwp'])
        self.assertGreater(flat['selected_pvgis_ac']['max'],0)
        # Missing independent evidence must never become a zero-yield datum.
        json.dumps(roofs,allow_nan=False)
    def test_no_system_baseline_and_quote_selected_equipment(self):
        charts=e.chart_data({'south_panels':0,'battery_kwh':0,'load_growth':1})
        base=charts['scenarios'][0]['series']
        self.assertEqual(base['load_kwh'],base['grid_import_kwh'])
        self.assertAlmostEqual(base['load_kwh']['annual']['median'],charts['measured']['annual']['median'])
        q=e.quote_evaluate({'horizon_years':1},{'battery_nameplate_kwh':10,'battery_dod':.8,'continuous_charge_kw':2})
        self.assertEqual(q['charts']['selected_config']['battery_kwh'],q['result']['settings']['battery_kwh'])
        self.assertEqual(q['charts']['selected_config']['charge_kw'],2)

if __name__=='__main__':unittest.main()
