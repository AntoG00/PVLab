"""Summary financing must use the configured APR before currency deflation."""
import unittest
from feasibility import engine


class SummaryLoanInterestTests(unittest.TestCase):
    def test_first_year_interest_tracks_configured_apr(self):
        principal = 14999.0
        for apr in (0.06, 0.0525):
            with self.subTest(apr=apr):
                payments, balances = engine.loan_schedule(principal, apr, 10, 20)
                interest = payments[0] - (principal - balances[0])
                monthly_rate = apr / 12
                instalment = principal * monthly_rate / (1 - (1 + monthly_rate) ** -120)
                expected_balance = principal * (1 + monthly_rate) ** 12 - instalment * ((1 + monthly_rate) ** 12 - 1) / monthly_rate
                self.assertAlmostEqual(interest, 12 * instalment - principal + expected_balance, places=7)
                self.assertAlmostEqual(interest, principal * apr, delta=principal * apr * 0.06)
