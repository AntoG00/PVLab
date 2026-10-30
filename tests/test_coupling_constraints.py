"""Physical routing and admissibility regression tests for the system designer."""
import unittest
import numpy as np
from test_support import IsolatedDefaultsTestCase
import feasibility.engine as e
from feasibility.physical_dispatch import physical_parameters


class CouplingConstraintsTests(IsolatedDefaultsTestCase):
    def dispatch(self, pv, load, dc=False, **settings):
        s = e.validate({'pv_dispatch_model': 'physical', 'inverter_kw': 3.,
                        'inverter_efficiency': .9, 'battery_inverter_kw': 2.,
                        'battery_c_rate_max': 1., 'battery_charge_efficiency': 1.,
                        'battery_discharge_efficiency': 1., **settings})
        return e.dispatch_year(np.array(pv, float), np.array(load, float),
                               s['battery_kwh'], s['charge_kw'], s['discharge_kw'],
                               1., s['inverter_kw'], 0., np.ones(len(pv)),
                               np.ones(len(pv), bool), dc, physical_parameters(s))

    def test_dc_recovers_upstream_clipping_ac_cannot(self):
        ac, _ = self.dispatch([5.4, 0], [0, 3])
        dc, trace = self.dispatch([5.4, 0], [0, 3], dc=True)
        self.assertAlmostEqual(ac[9], 2.4)
        self.assertLess(dc[9], ac[9])
        self.assertGreater(dc[4], ac[4])
        self.assertLess(dc[13], 1e-9)
        self.assertTrue(np.all(trace[:,5]+trace[:,3]+trace[:,7] <= 3+1e-9))

    def test_ac_battery_inverter_and_efficiency_direction(self):
        _, trace = self.dispatch([3., 0], [0, 5.])
        self.assertAlmostEqual(trace[0,6], 2.)  # AC charge rating
        self.assertAlmostEqual(trace[0,4], 1.8)  # DC stored
        self.assertAlmostEqual(trace[1,7], 1.62)  # DC -> AC conversion

    def test_c_rate_and_soc_window(self):
        _, trace = self.dispatch([10., 10., 0], [0, 0, 10], dc=True,
                                 battery_kwh=2., battery_c_rate_max=.25,
                                 battery_soc_min=.2, battery_soc_max=.8)
        # 2 usable kWh / .6 = 3.333 nominal; .25C = .8333 kW DC.
        self.assertAlmostEqual(trace[0,6], 2/.6*.25)
        self.assertTrue(np.all(trace[:,4] <= 2+1e-9))
        self.assertLessEqual(trace[2,7], 2/.6*.25*.9+1e-9)
        with self.assertRaises(ValueError):
            e.validate({'battery_soc_min': .8, 'battery_soc_max': .5})

    def test_ac_output_uses_separate_inverter_dc_output_is_shared(self):
        _, ac = self.dispatch([3., 2.], [0, 6.], battery_inverter_kw=3.)
        _, dc = self.dispatch([3., 2.], [0, 6.], dc=True, battery_inverter_kw=0.)
        self.assertGreater(ac[1,5]+ac[1,7], 3.)
        self.assertLessEqual(dc[1,5]+dc[1,7], 3.+1e-9)

    def test_physical_dc_monthly_clipping_matches_annual_dispatch(self):
        matrix = e.dispatch_matrix({'pv_dispatch_model': 'physical',
                                    'pv_battery_coupling': 'dc_coupled',
                                    'south_panels': 30, 'inverter_kw': 3.,
                                    'battery_kwh': 5., 'load_scenario': 'baseline'})
        self.assertAlmostEqual(sum(m['clipped_kwh'] for m in matrix['monthly_clipping']),
                               matrix['annual_flows']['clipped_kwh'], delta=1.)
        self.assertGreater(matrix['annual_flows']['clipped_kwh'], 0.)

    def test_hourly_terminal_equations_and_conservation(self):
        rng = np.random.default_rng(71)
        pv, load = rng.uniform(0, 8, 168), rng.uniform(0, 6, 168)
        for dc in (False, True):
            eta, ch_eff, dis_eff = .91, .94, .89
            params = (1., eta, ch_eff, dis_eff, .5, 4., .7)
            row, trace = e.dispatch_year(pv, load, 7., 5., 5., 1., 3.,
                                         1., np.ones(168), np.ones(168, bool), dc, params)
            previous = 1.
            for t, values in enumerate(trace):
                produced, demand, imported, exported, soc, direct, charge, discharge = values
                charge_dc = charge if dc else charge*eta
                discharge_dc = discharge/eta
                self.assertAlmostEqual(soc-previous, ch_eff*charge_dc-discharge_dc/dis_eff, places=9)
                self.assertLessEqual(charge_dc, .5*7/.7+1e-9)
                self.assertLessEqual(discharge_dc, .5*7/.7+1e-9)
                self.assertAlmostEqual(charge*discharge, 0., places=9)
                if dc:
                    self.assertLessEqual(direct+exported+discharge, 3.+1e-9)
                    clipping_dc = max(pv[t]/eta-direct/eta-exported/eta-charge_dc, 0.)
                    self.assertAlmostEqual(eta*(pv[t]/eta-clipping_dc-charge_dc+discharge_dc),
                                           direct+exported+discharge, places=9)
                else:
                    self.assertLessEqual(charge, 4.+1e-9)
                    self.assertLessEqual(discharge, 4.+1e-9)
                    self.assertLessEqual(direct+charge+exported, 3.+1e-9)
                previous = soc
            self.assertLess(row[13], 1e-9)

    def test_zero_battery(self):
        row, trace = self.dispatch([8., 0], [0, 4], dc=True, battery_kwh=0)
        self.assertEqual(trace[:,4].sum(), 0)
        self.assertAlmostEqual(row[9], 5.)
        self.assertLess(row[13], 1e-9)

    def test_designer_checks_both_topologies_ilr_and_every_year_clipping(self):
        result = e.solar_sizing({'horizon_years': 2, 'inverter_kw': 3.,
                                'pv_max_kwp': 7., 'panel_watts': 1000,
                                'clipping_ac_max': 0., 'clipping_dc_max': .01},
                               {'topology_mode': 'auto', 'battery_max_kwh': 5,
                                'panel_maxima': {'south': 7, 'west': 0, 'east': 0,
                                                 'dormer': 0, 'flat': 0, 'secondary_south': 0}})
        candidates = result['_evaluated_candidates']
        ac = next(c for c in candidates if c['panel_count'] == 1 and c['battery_kwh'] == 0 and c['settings']['pv_battery_coupling'] == 'ac_equivalent')
        dc = next(c for c in candidates if c['panel_count'] == 1 and c['battery_kwh'] == 0 and c['settings']['pv_battery_coupling'] == 'dc_coupled')
        self.assertNotEqual(e._cand_sig(ac), e._cand_sig(dc))
        self.assertEqual({c['settings']['pv_battery_coupling'] for c in candidates},
                         {'ac_equivalent', 'dc_coupled'})
        for candidate in candidates:
            if not candidate['panel_count']:
                continue
            s = candidate['settings']
            dc = s['pv_battery_coupling'] == 'dc_coupled'
            self.assertLessEqual(candidate['kwp'], s['inverter_kw']*s['ilr_dc_max' if dc else 'ilr_ac_max']+1e-9)
            self.assertLessEqual(candidate['annual_clipping_fraction_max'], s['clipping_dc_max' if dc else 'clipping_ac_max']+1e-9)
            self.assertEqual(s['pv_dispatch_model'], 'physical')
            if dc:
                self.assertEqual(s['battery_inverter_kw'], 0.)
        policy = result['search_summary']['physical_constraints']
        self.assertGreater(policy['rejected'], 0)
        self.assertEqual(policy['evaluated_including_rejected'], result['search_summary']['search_space_size'])

    def test_public_pv_metrics_use_ac_equivalent_basis_and_match_candidate(self):
        # DC-coupled physical dispatch reports `produced` on an internal hybrid bus
        # that includes DC charge energy. Public annual_pv_kwh and self-consumption
        # must be reported on the AC-equivalent basis, and the sizing candidate
        # metrics must agree with evaluate() for the same settings.
        s = e.validate({'pv_dispatch_model': 'physical',
                        'pv_battery_coupling': 'dc_coupled',
                        'south_panels': 12, 'panel_watts': 1000.,
                        'inverter_kw': 3., 'inverter_efficiency': .9,
                        'battery_kwh': 5., 'battery_inverter_kw': 0.,
                        'load_scenario': 'baseline'})
        counts = {r: s[r + '_panels'] for r in e.OPTIMIZE_ROOFS}
        composition = {'battery_kwh': s['battery_kwh'], 'battery_modules': s['battery_modules'],
                       'battery_module_kwh': s['battery_module_kwh'],
                       'battery_secondary_module_kwh': s['battery_secondary_module_kwh'],
                       'battery_mixed_sizes': False}
        cand = e._evaluate_solar_candidate(s, {}, counts, composition)
        metrics = e.evaluate(cand['settings'], detail=False)['metrics']

        key = tuple((k, e.freeze(s[k])) for k in e.ENERGY_KEYS)
        annual, _ = e._energy_cache(key, e.price_fingerprint(s))
        first = annual[0]
        eta = float(s['inverter_efficiency'])
        produced_ac = first[:, 1] - first[:, 3] * (1. - eta)
        self_pv = first[:, 2] + first[:, 3] * eta
        sc = np.divide(self_pv, produced_ac, out=np.zeros(len(first)), where=produced_ac > 0)

        # The corrected AC-equivalent basis genuinely differs from the internal bus.
        self.assertTrue(np.any(first[:, 3] > 1e-9))
        self.assertTrue(np.any(np.abs(first[:, 1] - produced_ac) > 1e-9))

        self.assertAlmostEqual(metrics['pv_kwh']['median'], float(np.median(produced_ac)), places=6)
        self.assertAlmostEqual(metrics['self_consumption']['median'], float(np.median(sc)), places=9)
        self.assertAlmostEqual(cand['annual_pv_kwh_median'], metrics['pv_kwh']['median'], places=6)
        self.assertAlmostEqual(cand['self_consumption_median'], metrics['self_consumption']['median'], places=9)
