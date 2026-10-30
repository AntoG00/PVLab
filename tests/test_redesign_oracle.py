"""Oracle verification tests for dispatch trace indices, battery efficiency, and hourly conservation."""
import math
import unittest
import numpy as np

from feasibility import engine
try:
    from test_support import IsolatedDefaultsTestCase
except ImportError:
    from tests.test_support import IsolatedDefaultsTestCase


class RedesignOracleTests(IsolatedDefaultsTestCase):
    def test_hourly_trace_oracle_and_charge_discharge_indices(self):
        settings = {
            'south_panels': 14,
            'battery_kwh': 10.0,
            'charge_kw': 5.0,
            'discharge_kw': 5.0,
            'roundtrip_efficiency': 0.90,
            'load_scenario': 'baseline'
        }
        res = engine.dispatch_matrix(settings, mode='forecast_2027', scenario='baseline', representative='median')
        af = res['analogue_annual_flows']

        # Access internal forecast run to inspect full raw hourly trace
        key = tuple((k, engine.validate(settings)[k]) for k in engine.ENERGY_KEYS)
        runs = engine._forecast_2027_runs(key, engine.price_fingerprint(settings))
        ri = runs['picks']['median']
        trace = runs['traces'][ri]  # shape (8760, 8)
        annual_item = runs['annual'][ri]

        hours = trace.shape[0]
        self.assertIn(hours, (8760, 8784))

        eff = math.sqrt(0.90)

        # Inspect trace columns:
        # col 0: produced, col 1: demand, col 2: import, col 3: export,
        # col 4: soc, col 5: direct, col 6: charge, col 7: discharge
        prev_soc = 0.0
        tot_losses = 0.0

        for t in range(hours):
            produced = trace[t, 0]
            demand = trace[t, 1]
            imp = trace[t, 2]
            exp = trace[t, 3]
            soc = trace[t, 4]
            direct = trace[t, 5]
            charge = trace[t, 6]
            discharge = trace[t, 7]

            # 1. Non-negativity (with float precision tolerance)
            self.assertGreaterEqual(produced, -1e-9)
            self.assertGreaterEqual(demand, -1e-9)
            self.assertGreaterEqual(imp, -1e-9)
            self.assertGreaterEqual(exp, -1e-9)
            self.assertGreaterEqual(soc, -1e-6)
            self.assertLessEqual(soc, 10.0 + 1e-6)
            self.assertGreaterEqual(direct, -1e-9)
            self.assertGreaterEqual(charge, -1e-9)
            self.assertGreaterEqual(discharge, -1e-9)

            # 2. Charge and discharge mutual exclusivity: cannot charge and discharge simultaneously
            self.assertAlmostEqual(charge * discharge, 0.0, places=6)

            # 3. Direct solar and surplus logic:
            # direct = min(produced, demand)
            self.assertAlmostEqual(direct, min(produced, demand), places=6)

            # 4. Inverter AC bus power balance:
            # produced = direct + charge + export
            self.assertAlmostEqual(produced, direct + charge + exp, places=6)
            # demand = direct + discharge + import
            self.assertAlmostEqual(demand, direct + discharge + imp, places=6)

            # 5. Battery SOC update physics:
            # soc = prev_soc + charge * eff - discharge / eff
            expected_soc = prev_soc + charge * eff - discharge / eff
            self.assertAlmostEqual(soc, expected_soc, places=5)

            # 6. Battery losses:
            # loss = charge * (1 - eff) + discharge * (1/eff - 1)
            # charge - discharge = loss + (soc - prev_soc)
            hourly_loss = charge * (1.0 - eff) + discharge * (1.0 / eff - 1.0)
            self.assertAlmostEqual(charge - discharge, hourly_loss + (soc - prev_soc), places=5)
            tot_losses += hourly_loss

            prev_soc = soc

        # Verify annual totals reconcile to the hourly oracle accumulation
        tot_charge = float(trace[:, 6].sum())
        tot_discharge = float(trace[:, 7].sum())
        end_soc = float(trace[-1, 4])

        self.assertAlmostEqual(tot_losses, annual_item['battery_loss_kwh'], delta=0.01)
        self.assertAlmostEqual(tot_charge - tot_discharge, tot_losses + end_soc, delta=0.01)

        # Verify coherent analogue totals at their documented one-decimal reporting precision
        self.assertEqual(af['battery_charge_kwh'], round(tot_charge, 1))
        self.assertEqual(af['battery_discharge_kwh'], round(tot_discharge, 1))
        self.assertEqual(af['storage_losses_kwh'], round(tot_losses, 1))
        self.assertEqual(af['end_soc_kwh'], round(end_soc, 1))
        self.assertEqual(af['storage_delta_kwh'], round(end_soc, 1))


if __name__ == '__main__':
    unittest.main()
