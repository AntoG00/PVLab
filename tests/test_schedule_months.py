"""Unit tests for scheduled AM/PM active months configuration and dispatch gates."""
import math
import unittest
import numpy as np

from test_support import IsolatedDefaultsTestCase
import feasibility.engine as e
from feasibility.assumptions import SPEC
from feasibility.physical_dispatch import (
    ROUTING_MODES,
    dispatch_physical_year,
    physical_parameters,
    scheduled_month_active,
    soc_ceiling_series,
)


class ScheduleMonthsTests(IsolatedDefaultsTestCase):
    def settings(self, routing='scheduled_hybrid', **over):
        base = {
            'pv_dispatch_model': 'physical',
            'pv_battery_coupling': 'dc_coupled',
            'routing_priority': routing,
            'routing_switch_hour': 11,
            'scheduled_start_month': 3,
            'scheduled_end_month': 9,
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

    def dispatch(self, pv, load, routing='scheduled_hybrid', dc=True, month=None, hour=None, **over):
        s = self.settings(routing, **over)
        return e.dispatch_year(
            np.array(pv, float), np.array(load, float),
            s['battery_kwh'], s['charge_kw'], s['discharge_kw'],
            math.sqrt(s['roundtrip_efficiency']), s['inverter_kw'], 0.0,
            np.ones(len(pv)), np.ones(len(pv), bool), dc, physical_parameters(s),
            None if month is None else np.array(month),
            None if hour is None else np.array(hour),
        )

    def test_scheduled_months_defaults(self):
        self.assertEqual(SPEC['scheduled_start_month'][0], 3)
        self.assertEqual(SPEC['scheduled_end_month'][0], 9)
        s = e.validate({})
        self.assertEqual(s['scheduled_start_month'], 3)
        self.assertEqual(s['scheduled_end_month'], 9)

    def test_validation_rejects_fractional_and_out_of_bounds(self):
        for bad in (0, 13, -1, 3.5, 9.2, 'march'):
            with self.assertRaises((ValueError, TypeError)):
                e.validate({'scheduled_start_month': bad})
            with self.assertRaises((ValueError, TypeError)):
                e.validate({'scheduled_end_month': bad})

    def test_synthetic_dispatch_march_vs_february_before_switch(self):
        # Hour 10 < switch 11. Sub-inverter solar 2.0 kW, load 0.0.
        # March (month 3) is active -> grid-first -> sub-inverter battery charging paused.
        # February (month 2) is inactive -> self-use -> battery charges.
        # Trace columns: [pv, load, grid_import, export, soc, direct, charge_bus, discharge]
        pv = [2.0]
        load = [0.0]
        hour = [10]

        row_mar, trace_mar = self.dispatch(pv, load, month=[3], hour=hour)
        row_feb, trace_feb = self.dispatch(pv, load, month=[2], hour=hour)

        self.assertEqual(float(trace_mar[0, 6]), 0.0)
        self.assertGreater(float(trace_feb[0, 6]), 0.0)

    def test_change_start_to_april_disables_march(self):
        pv = [2.0]
        load = [0.0]
        hour = [10]
        month = [3]

        # With start=3, March is active -> sub-inverter charging paused
        _, trace_def = self.dispatch(pv, load, month=month, hour=hour, scheduled_start_month=3)
        self.assertEqual(float(trace_def[0, 6]), 0.0)

        # With start=4, March is outside active window -> self-use charges battery
        _, trace_apr = self.dispatch(pv, load, month=month, hour=hour, scheduled_start_month=4)
        self.assertGreater(float(trace_apr[0, 6]), 0.0)

    def test_end_month_inclusive_and_after_end_self_use(self):
        pv = [2.0]
        load = [0.0]
        hour = [10]

        # Month 9 (September) is inclusive -> active -> sub-inverter charging paused
        _, trace_sep = self.dispatch(pv, load, month=[9], hour=hour)
        self.assertEqual(float(trace_sep[0, 6]), 0.0)

        # Month 10 (October) is after end -> inactive -> self-use
        _, trace_oct = self.dispatch(pv, load, month=[10], hour=hour)
        self.assertGreater(float(trace_oct[0, 6]), 0.0)

    def test_wrap_nov_to_feb_active_jan_not_march(self):
        pv = [2.0]
        load = [0.0]
        hour = [10]
        over = {'scheduled_start_month': 11, 'scheduled_end_month': 2}

        self.assertTrue(scheduled_month_active(1, 11, 2))
        self.assertFalse(scheduled_month_active(3, 11, 2))

        # January (1) is active in Nov-Feb wrap -> grid-first before switch
        _, trace_jan = self.dispatch(pv, load, month=[1], hour=hour, **over)
        self.assertEqual(float(trace_jan[0, 6]), 0.0)

        # March (3) is outside wrapped window -> self-use
        _, trace_mar = self.dispatch(pv, load, month=[3], hour=hour, **over)
        self.assertGreater(float(trace_mar[0, 6]), 0.0)

    def test_at_switch_hour_self_use(self):
        pv = [2.0]
        load = [0.0]
        month = [3]

        # Hour 10 < switch 11 -> grid-first (0 sub-inverter charge)
        _, trace_10 = self.dispatch(pv, load, month=month, hour=[10])
        self.assertEqual(float(trace_10[0, 6]), 0.0)

        # Hour 11 == switch 11 -> self-use (charges battery)
        _, trace_11 = self.dispatch(pv, load, month=month, hour=[11])
        self.assertGreater(float(trace_11[0, 6]), 0.0)

    def test_dc_over_inverter_recapture_active_while_sub_inverter_charging_paused(self):
        # Inverter AC limit = 3.0 kW, eta = 0.9 => DC threshold = 3.0 / 0.9 = 3.333 kW.
        # PV = 5.0 kW generates over-inverter clipping PV that can be recaptured.
        # Sub-inverter solar (3.0 kW AC) is exported, while clipping surplus (5 - 3)/0.9 charges battery.
        pv = [5.0]
        load = [0.0]
        month = [3]
        hour = [10]

        row, trace = self.dispatch(pv, load, month=month, hour=hour, dc=True)
        # trace[0, 6] is charge_bus, trace[0, 3] is export, trace[0, 7] is discharge
        expected_recapture = (5.0 - 3.0) / 0.9
        self.assertAlmostEqual(float(trace[0, 6]), expected_recapture, places=6)
        self.assertAlmostEqual(float(trace[0, 3]), 3.0, places=6)
        self.assertEqual(float(trace[0, 7]), 0.0)

    def test_telemetry_soc_ceiling_series_agrees_gate(self):
        pv = np.array([2.0], float)
        load = np.array([0.0], float)
        capacity = 10.0
        charge_limit = 5.0
        inverter = 3.0
        eta = 0.9
        eta_ch = 1.0
        dc = True

        s = self.settings('scheduled_hybrid', scheduled_start_month=3, scheduled_end_month=9)
        params = physical_parameters(s)

        # In March (active) hour 10: sub-inverter charging paused -> ceiling is 0
        ceil_mar = soc_ceiling_series(
            pv, load, capacity, charge_limit, inverter, eta, eta_ch, dc,
            params, month=np.array([3]), hour=np.array([10]),
        )
        self.assertEqual(float(ceil_mar[0]), 0.0)

        # In February (inactive) hour 10: self-use -> ceiling is capacity
        ceil_feb = soc_ceiling_series(
            pv, load, capacity, charge_limit, inverter, eta, eta_ch, dc,
            params, month=np.array([2]), hour=np.array([10]),
        )
        self.assertEqual(float(ceil_feb[0]), capacity)

    def test_old_parameter_tuples_still_run(self):
        pv = np.array([2.0], float)
        load = np.array([0.0], float)
        s = self.settings('scheduled_hybrid')
        full_params = physical_parameters(s)

        # 7-element and 9-element legacy parameter slices
        for legacy_params in (full_params[:7], full_params[:9]):
            row, trace = dispatch_physical_year(
                pv, load, 10.0, 5.0, 5.0,
                3.0, 0.0, np.ones(1), np.ones(1, bool), True, legacy_params,
                month=np.array([3]), hour=np.array([10]), record_trace=True,
            )
            # Seven parameters default to battery-first; nine carry scheduled mode.
            if len(legacy_params) == 7:
                self.assertGreater(float(trace[0, 6]), 0.0)
            else:
                self.assertEqual(float(trace[0, 6]), 0.0)
            self.assertEqual(float(trace[0, 7]), 0.0)
            self.assertGreaterEqual(float(trace[0, 3]), 0.0)
            # Energy balance: PV generation matches export + direct
            self.assertAlmostEqual(float(trace[0, 0]), float(trace[0, 3]) + float(trace[0, 5]) + float(trace[0, 6]), places=6)

    def test_month_only_change_alters_energy_keys_cache_identity(self):
        self.assertIn('scheduled_start_month', e.ENERGY_KEYS)
        self.assertIn('scheduled_end_month', e.ENERGY_KEYS)

        s_base = e.validate({})
        s_start = e.validate({'scheduled_start_month': 4})
        s_end = e.validate({'scheduled_end_month': 10})

        k_base = tuple((k, e.freeze(s_base[k])) for k in e.ENERGY_KEYS)
        k_start = tuple((k, e.freeze(s_start[k])) for k in e.ENERGY_KEYS)
        k_end = tuple((k, e.freeze(s_end[k])) for k in e.ENERGY_KEYS)

        self.assertNotEqual(k_base, k_start)
        self.assertNotEqual(k_base, k_end)
