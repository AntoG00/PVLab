"""Routing-priority (EMS work mode) tests for the physical dispatch kernel.

Low-level tests exercise the numba kernel directly and need no bundled data.
Integrated resolver/optimizer tests require the explicitly generated synthetic dataset.
"""
import math
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from test_support import IsolatedDefaultsTestCase
import feasibility.engine as e
from feasibility.physical_dispatch import (
    ROUTING_MODES,
    charge_source_split,
    dispatch_physical_year,
    physical_parameters,
    soc_ceiling_series,
)

DATA = e.data()
assert DATA['valid'].any(), 'Generated weather required for integrated routing tests'

def requires_data(test):
    return test


class _Base(IsolatedDefaultsTestCase):
    def settings(self, routing, **over):
        base = {
            'pv_dispatch_model': 'physical',
            'routing_priority': routing,
            'routing_switch_hour': 11,
            'inverter_kw': 3.0,
            'inverter_efficiency': 0.9,
            'battery_inverter_kw': 2.0,
            'battery_c_rate_max': 1.0,
            'battery_charge_efficiency': 1.0,
            'battery_discharge_efficiency': 1.0,
            'battery_kwh': 10.0,
            'charge_kw': 5.0,
            'discharge_kw': 5.0,
        }
        base.update(over)
        return e.validate(base)

    def dispatch(self, pv, load, routing, dc=False, month=None, hour=None, **over):
        s = self.settings(routing, **over)
        return e.dispatch_year(
            np.array(pv, float), np.array(load, float),
            s['battery_kwh'], s['charge_kw'], s['discharge_kw'],
            math.sqrt(s['roundtrip_efficiency']), s['inverter_kw'], 0.0,
            np.ones(len(pv)), np.ones(len(pv), bool), dc, physical_parameters(s),
            None if month is None else np.array(month),
            None if hour is None else np.array(hour),
        )

    @staticmethod
    def solar_day(month=6):
        pv = np.zeros(24)
        pv[7:16] = 6.0
        load = np.full(24, 0.5)
        return pv, load, np.full(24, month), np.arange(24)


class RoutingModesSyntheticTests(_Base):
    def test_signed_prices_change_currency_only_for_fixed_routing(self):
        for routing in ROUTING_MODES:
            s = self.settings(routing, inverter_efficiency=1.)
            rows, traces = [], []
            for prices in ([-.1,.2], [-1.,-2.]):
                row, trace = e.dispatch_year(np.array([1.,2.]), np.zeros(2),
                    0., 0., 0., 1., 3., 0., np.array(prices), np.ones(2,dtype=bool),
                    False, physical_parameters(s), np.array([6,6]), np.array([12,13]))
                rows.append(row); traces.append(trace)
            np.testing.assert_array_equal(traces[0], traces[1])
            np.testing.assert_array_equal(rows[0][:11], rows[1][:11])
            self.assertAlmostEqual(rows[0][11], .3)
            self.assertAlmostEqual(rows[1][11], -5.)

    def test_all_four_modes_both_topologies_conserve_and_bound(self):
        pv, load, month, hour = self.solar_day()
        for routing in ROUTING_MODES:
            for dc in (False, True):
                row, trace = self.dispatch(pv, load, routing, dc=dc, month=month, hour=hour)
                self.assertLess(row[13], 1e-9, f'{routing} dc={dc} balance error')
                self.assertTrue(np.all(trace[:, 4] >= -1e-9))
                self.assertTrue(np.all(trace[:, 4] <= 10.0 + 1e-9))
                # Never simultaneous charge and discharge.
                self.assertTrue(np.all(trace[:, 6] * trace[:, 7] <= 1e-9))

    def test_soc_conservation_independent_oracle(self):
        rng = np.random.default_rng(7)
        pv, load = rng.uniform(0, 8, 72), rng.uniform(0, 6, 72)
        for routing in ROUTING_MODES:
            for dc in (False, True):
                _, trace = self.dispatch(pv, load, routing, dc=dc)
                eta, eta_ch, eta_dis = 0.9, 0.94, 0.89
                s = self.settings(routing, battery_charge_efficiency=eta_ch,
                                  battery_discharge_efficiency=eta_dis)
                # Rebuild with explicit efficiencies so the oracle is independent.
                _, trace = e.dispatch_year(
                    np.array(pv, float), np.array(load, float), s['battery_kwh'],
                    s['charge_kw'], s['discharge_kw'], 1.0, s['inverter_kw'], 1.0,
                    np.ones(len(pv)), np.ones(len(pv), bool), dc, physical_parameters(s))
                previous = 1.0
                for t in range(len(pv)):
                    produced, demand, imported, exported, soc, direct, charge, discharge = trace[t]
                    charge_dc = charge if dc else charge * eta
                    discharge_dc = discharge / eta
                    self.assertAlmostEqual(
                        soc - previous,
                        eta_ch * charge_dc - discharge_dc / eta_dis, places=9)
                    previous = soc

    def test_combined_streams_share_terminal_charge_limit(self):
        for mode in ROUTING_MODES:
            _,trace=self.dispatch([4.5],[0.],mode,dc=True,month=[6],hour=[12],
                                  inverter_efficiency=1.,battery_c_rate_max=.1)
            self.assertLessEqual(trace[0,6],1.+1e-9)

    def test_reserve_does_not_include_tomorrows_clipping(self):
        s=self.settings('dynamic_reserve')
        pv=np.array([2.,2.,0.,0.,8.,8.])
        hh=np.array([9,10,23,0,10,11]);mm=np.full(6,6)
        ceiling=soc_ceiling_series(pv,np.zeros(6),10.,5.,3.,.9,1.,True,
                                   physical_parameters(s),mm,hh)
        self.assertAlmostEqual(ceiling[0],10.)
        self.assertLess(ceiling[3],10.)
        self.assertAlmostEqual(ceiling[-1],5.)

    def test_battery_first_matches_legacy_7tuple_numbers(self):
        pv, load, month, hour = self.solar_day()
        s = self.settings('battery_first')
        params9 = physical_parameters(s)
        params7 = params9[:7]
        _, t9 = e.dispatch_year(np.array(pv, float), np.array(load, float), s['battery_kwh'],
                                s['charge_kw'], s['discharge_kw'], math.sqrt(s['roundtrip_efficiency']),
                                s['inverter_kw'], 0.0, np.ones(24), np.ones(24, bool), True, params9)
        _, t7 = e.dispatch_year(np.array(pv, float), np.array(load, float), s['battery_kwh'],
                                s['charge_kw'], s['discharge_kw'], math.sqrt(s['roundtrip_efficiency']),
                                s['inverter_kw'], 0.0, np.ones(24), np.ones(24, bool), True, params7)
        np.testing.assert_allclose(t9, t7, atol=1e-12)


class RoutingCalendarTests(_Base):
    def test_dynamic_reserve_uses_dc_energy_and_charge_efficiency(self):
        pv = np.zeros(24)
        pv[8:12], pv[12:14] = 2., 4.
        s = self.settings('dynamic_reserve', inverter_efficiency=.8,
                          battery_charge_efficiency=.8)
        ceiling = soc_ceiling_series(
            pv, np.zeros(24), 10., 5., 3., .8, .8, True,
            physical_parameters(s), np.full(24, 5), np.arange(24))
        # Two hours of 1/.8 DC kWh store 2 kWh, including the current hour.
        np.testing.assert_allclose(ceiling[11:14], [8., 8., 9.])
        row, trace = self.dispatch(pv, np.zeros(24), 'dynamic_reserve', dc=True,
                                   month=np.full(24, 5), hour=np.arange(24),
                                   inverter_efficiency=.8, battery_charge_efficiency=.8)
        np.testing.assert_allclose(trace[11:14, 4], [8., 9., 10.])
        self.assertAlmostEqual(row[9], 0., places=9)

    def test_grid_first_exports_sub_surplus_battery_first_charges_it(self):
        pv, load, month, hour = self.solar_day()
        pv=np.minimum(pv,2.)  # Only sub-inverter surplus: isolates the mode gate.
        for dc in (True,):
            bf, _ = self.dispatch(pv, load, 'battery_first', dc=dc, month=month, hour=hour)
            gf, _ = self.dispatch(pv, load, 'grid_first', dc=dc, month=month, hour=hour)
            self.assertLess(gf[3], bf[3])
            self.assertGreater(gf[6], bf[6])

    def test_scheduled_switch_only_during_high_irradiance_months(self):
        pv, load, month, hour = self.solar_day(month=6)
        pv=np.minimum(pv,2.)
        bf, _ = self.dispatch(pv, load, 'battery_first', dc=True, month=month, hour=hour)
        gf, _ = self.dispatch(pv, load, 'grid_first', dc=True, month=month, hour=hour)
        sch, _ = self.dispatch(pv, load, 'scheduled_hybrid', dc=True, month=month, hour=hour)
        # Before the switch hour June behaves grid-first; after it behaves battery-first.
        morning_bf, morning_gf, morning_sch = bf[3], gf[3], sch[3]
        self.assertLess(morning_sch, morning_bf)
        self.assertGreater(morning_sch, morning_gf - 1e-9)
        # Outside Apr-Sep scheduled is plain battery-first at every hour.
        pv2, load2, month2, hour2 = self.solar_day(month=1)
        bf2, _ = self.dispatch(pv2, load2, 'battery_first', dc=True, month=month2, hour=hour2)
        sch2, _ = self.dispatch(pv2, load2, 'scheduled_hybrid', dc=True, month=month2, hour=hour2)
        self.assertAlmostEqual(bf2[3], sch2[3], places=9)

    def test_dynamic_reserve_ceiling_before_switch(self):
        pv, load, month, hour = self.solar_day(month=6)
        s = self.settings('dynamic_reserve')
        params = physical_parameters(s)
        ceiling = soc_ceiling_series(pv, load, s['battery_kwh'], s['charge_kw'],
                                     s['inverter_kw'], s['inverter_efficiency'],
                                     s['battery_charge_efficiency'], True, params, month, hour)
        self.assertLess(ceiling[10], s['battery_kwh'])
        self.assertLess(ceiling[14], s['battery_kwh'])
        self.assertAlmostEqual(ceiling[16], s['battery_kwh'], places=9)

    def test_dynamic_reserve_all_day_ceiling_and_dispatch_regression(self):
        pv = np.zeros(24)
        load = np.zeros(24)
        pv[8:11] = 2.0
        pv[11] = 2.0
        pv[12:15] = 6.0
        load[18:22] = 1.5
        month = np.full(24, 6)
        hour = np.arange(24)

        s = self.settings('dynamic_reserve')
        params = physical_parameters(s)
        ceiling = soc_ceiling_series(
            pv, load, s['battery_kwh'], s['charge_kw'],
            s['inverter_kw'], s['inverter_efficiency'],
            s['battery_charge_efficiency'], True, params, month, hour,
        )

        # Telemetry remains reserved after nominal switch (11) until clipping ends and unlocks afterward
        self.assertLess(ceiling[11], s['battery_kwh'])
        self.assertLess(ceiling[12], s['battery_kwh'])
        self.assertLess(ceiling[14], s['battery_kwh'])
        self.assertAlmostEqual(ceiling[15], s['battery_kwh'], places=9)

        # Actual dispatch saves clipping vs battery_first while conserving energy and no-grid charging
        dr_row, dr_trace = self.dispatch(pv, load, 'dynamic_reserve', dc=True, month=month, hour=hour)
        bf_row, _ = self.dispatch(pv, load, 'battery_first', dc=True, month=month, hour=hour)
        self.assertLess(dr_row[9], bf_row[9])
        self.assertAlmostEqual(dr_row[9], 0., places=9)
        # The public compiled wrapper must execute the current physical kernel.
        kernel_row, kernel_trace = dispatch_physical_year(
            pv, load, 10., 5., 5., 3., 0., np.ones(24), np.ones(24, bool),
            True, params, month, hour)
        np.testing.assert_allclose(dr_row, kernel_row, atol=1e-12)
        np.testing.assert_allclose(dr_trace, kernel_trace, atol=1e-12)
        self.assertLess(dr_row[13], 1e-9)
        for t in range(24):
            if dr_trace[t, 6] > 1e-9:
                self.assertAlmostEqual(dr_trace[t, 2], 0.0, places=9)

        # Dynamic output independent of routing_switch_hour=9 vs 14
        row_sw9, trace_sw9 = self.dispatch(pv, load, 'dynamic_reserve', dc=True, month=month, hour=hour, routing_switch_hour=9)
        row_sw14, trace_sw14 = self.dispatch(pv, load, 'dynamic_reserve', dc=True, month=month, hour=hour, routing_switch_hour=14)
        np.testing.assert_allclose(row_sw9, row_sw14, atol=1e-12)
        np.testing.assert_allclose(trace_sw9, trace_sw14, atol=1e-12)

        # Second day reset across midnight
        pv2 = np.concatenate([pv, np.zeros(24)])
        pv2[24 + 8:24 + 11] = 2.0
        load2 = np.tile(load, 2)
        ceiling2 = soc_ceiling_series(
            pv2, load2, s['battery_kwh'], s['charge_kw'],
            s['inverter_kw'], s['inverter_efficiency'],
            s['battery_charge_efficiency'], True, params, np.full(48, 6), np.tile(np.arange(24), 2),
        )
        self.assertLess(ceiling2[11], s['battery_kwh'])
        self.assertAlmostEqual(ceiling2[32], s['battery_kwh'], places=9)

    def test_dynamic_reserve_ceiling_honors_valid_mask(self):
        pv=np.zeros(24);pv[8]=2.;pv[12]=10.
        valid=np.ones(24,dtype=bool);valid[12]=False
        params=(1.,1.,1.,1.,1.,5.,1.,3.,11.)
        ceiling=soc_ceiling_series(pv,np.zeros(24),2.,5.,3.,1.,1.,True,params,valid=valid)
        unmasked=soc_ceiling_series(pv,np.zeros(24),2.,5.,3.,1.,1.,True,params)
        self.assertEqual(ceiling[8],2.)
        self.assertEqual(unmasked[8],0.)
        out,trace=dispatch_physical_year(pv,np.zeros(24),2.,5.,5.,3.,0.,np.ones(24),valid,True,params)
        self.assertEqual(trace[8,4],ceiling[8])
        self.assertEqual(trace.shape,(24,8))
        self.assertEqual(len(out),14)

    def test_charge_source_split_recaptures_over_inverter_first(self):
        pv = np.array([6.0, 0.0, 2.0])
        charge = np.array([4.0, 0.0, 1.0])
        split = charge_source_split(pv, charge, True, 3.0, 0.9)
        over = (6.0 - 3.0) / 0.9
        self.assertAlmostEqual(split['charged_from_recaptured_clipping_kwh'], over, places=9)
        self.assertAlmostEqual(split['charged_from_normal_solar_kwh'], 5.0 - over, places=9)
        ac_only = charge_source_split(pv, charge, False, 3.0, 0.9)
        self.assertEqual(ac_only['charged_from_recaptured_clipping_kwh'], 0.0)


class RoutingZeroStorageTests(_Base):
    def test_zero_battery_disables_dc_relief_and_uses_ac_cap(self):
        row, trace = self.dispatch([8.0, 0.0], [0.0, 4.0], 'grid_first', dc=True, battery_kwh=0.0)
        self.assertEqual(float(trace[:, 4].sum()), 0.0)
        self.assertAlmostEqual(row[9], 5.0)
        self.assertLess(row[13], 1e-9)

    def test_zero_storage_effective_mode_keeps_scenario_settings_valid(self):
        s=self.settings('auto',battery_kwh=0.)
        reusable=e._routing_settings(s)
        self.assertEqual(e.resolve_routing_priority(reusable),'pv_only')
        self.assertEqual(e.validate(reusable)['battery_kwh'],0.)

    def test_zero_battery_auto_resolves_pv_only(self):
        s = self.settings('auto', battery_kwh=0.0)
        self.assertEqual(e.resolve_routing_priority(s), 'pv_only')


@requires_data
class RoutingResolverIntegratedTests(_Base):
    def _physical(self, routing, **over):
        base = {
            'pv_dispatch_model': 'physical',
            'pv_battery_coupling': 'dc_coupled',
            'routing_priority': routing,
            'routing_switch_hour': 11,
            'south_panels': 18,
            'panel_watts': 480.0,
            'inverter_kw': 5.0,
            'battery_kwh': 5.0,
            'battery_inverter_kw': 0.0,
            'horizon_years': 2,
            'load_scenario': 'baseline',
        }
        base.update(over)
        return base

    def test_auto_result_keeps_auto_setting_and_resolves(self):
        res = e.evaluate(self._physical('auto'))
        self.assertEqual(res['settings']['routing_priority'], 'auto')
        self.assertIn(res['resolved_routing_priority'], ROUTING_MODES)

    def test_auto_dominates_fixed_modes_on_solar_equity_npv(self):
        auto = e.evaluate(self._physical('auto'))
        auto_npv = auto['finance']['solar_equity']['npv']['median']
        for mode in ROUTING_MODES:
            fixed = e.evaluate(self._physical(mode))
            self.assertEqual(fixed['resolved_routing_priority'], mode)
            self.assertGreaterEqual(auto_npv + 1e-6,
                                    fixed['finance']['solar_equity']['npv']['median'])

    def test_fixed_mode_is_locked(self):
        for mode in ROUTING_MODES:
            res = e.evaluate(self._physical(mode))
            self.assertEqual(res['settings']['routing_priority'], mode)
            self.assertEqual(res['resolved_routing_priority'], mode)

    def test_constrained_auto_prefers_feasible_route_over_higher_npv(self):
        s=e.validate(self._physical('auto',horizon_years=1,pv_first_year_loss=0.))
        means=dict(zip(ROUTING_MODES,[1000.,900.,800.,700.]))
        medians=dict(zip(ROUTING_MODES,[850.,950.,750.,650.]))
        def energy(key,version):
            mode=dict(key)['routing_priority'];a=np.zeros((1,1,14))
            a[0,0,9]=10. if mode=='battery_first' else 1.
            return a,{}
        def finance(settings,*args,**kwargs):
            mode=settings['routing_priority']
            env={'mean':means[mode],'median':medians[mode],'min':min(means[mode],medians[mode]),'max':max(means[mode],medians[mode])}
            return {'solar_equity':{'npv':env},'pv_incremental':{'npv':env}},[]
        e._resolve_routing_cached.cache_clear()
        with patch.object(e,'_energy_cache',side_effect=energy),patch.object(e,'_financial',side_effect=finance):
            with patch.object(e,'pv_profile',return_value=np.array([[100.]])),patch.object(e,'data',return_value={'valid':np.ones((1,1),bool)}), patch.object(e, '_forecast_calendar_mapping', return_value=([(np.array([0]), np.array([0]), np.array([1.]), np.array([0.]))], 1)):
                self.assertEqual(e.resolve_routing_priority(s),'battery_first')
                self.assertEqual(e.resolve_routing_priority(s,enforce_clipping=True),'grid_first')
                self.assertEqual(e.resolve_routing_priority({**s,'routing_priority':'battery_first'},enforce_clipping=True),'battery_first')
        e._resolve_routing_cached.cache_clear()

    def test_price_and_loan_solvers_resolve_before_energy_only_cache(self):
        class CapturedCacheCall(Exception):
            pass
        for solver in (e.fair_price,e.solve_loan_amount):
            seen=[]
            def capture(key,version):
                seen.append(dict(key))
                raise CapturedCacheCall()
            settings=self._physical('auto',real_discount_rate=.01)
            with patch.object(e,'resolve_routing_priority',return_value='grid_first') as resolve:
                with patch.object(e,'_energy_cache',side_effect=capture):
                    with self.assertRaises(CapturedCacheCall):
                        solver(settings)
                self.assertEqual(resolve.call_args.args[0]['real_discount_rate'],.01)
            self.assertEqual(seen[0]['routing_priority'],'grid_first')
            self.assertEqual(settings['routing_priority'],'auto')

    def test_resolver_identity_tracks_financial_settings(self):
        s1 = e.validate(self._physical('auto'))
        s2 = e.validate(self._physical('auto', real_discount_rate=0.01))
        self.assertNotEqual(e._resolver_signature(s1), e._resolver_signature(s2))

    def test_dispatch_matrix_exposes_routing_contract(self):
        res = e.dispatch_matrix(self._physical('auto'), mode='forecast_2027')
        self.assertIn('routing_priority', res['selected_config'])
        self.assertIn(res['selected_config']['routing_priority'], ROUTING_MODES)
        self.assertEqual(len(res['soc_ceiling_sub_kwh']), 12)
        self.assertEqual(len(res['soc_ceiling_sub_kwh'][0]), 24)
        flows = res['annual_flows']
        self.assertIn('charged_from_normal_solar_kwh', flows)
        self.assertIn('charged_from_recaptured_clipping_kwh', flows)

    def test_solar_candidate_route_matches_evaluate(self):
        s = e.validate(self._physical('battery_first'))
        counts = {r: s[r + '_panels'] for r in e.OPTIMIZE_ROOFS}
        composition = {
            'battery_kwh': s['battery_kwh'],
            'battery_modules': s['battery_modules'],
            'battery_module_kwh': s['battery_module_kwh'],
            'battery_secondary_module_kwh': s['battery_secondary_module_kwh'],
            'battery_mixed_sizes': False,
        }
        cand = e._evaluate_solar_candidate(s, {}, counts, composition)
        self.assertEqual(cand['settings']['routing_priority'], 'battery_first')
        metrics = e.evaluate(cand['settings'], detail=False)['metrics']
        self.assertAlmostEqual(cand['annual_pv_kwh_median'],
                               metrics['pv_kwh']['median'], places=6)

    def test_auto_candidate_returns_concrete_mode(self):
        s = e.validate(self._physical('auto'))
        counts = {r: s[r + '_panels'] for r in e.OPTIMIZE_ROOFS}
        composition = {
            'battery_kwh': s['battery_kwh'],
            'battery_modules': s['battery_modules'],
            'battery_module_kwh': s['battery_module_kwh'],
            'battery_secondary_module_kwh': s['battery_secondary_module_kwh'],
            'battery_mixed_sizes': False,
        }
        cand = e._evaluate_solar_candidate(s, {}, counts, composition)
        self.assertIn(cand['settings']['routing_priority'], ROUTING_MODES)


if __name__ == '__main__':
    unittest.main()
