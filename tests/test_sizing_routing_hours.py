"""Regression tests for candidate routing and switch-hour evaluation."""
import unittest
from unittest.mock import patch
import numpy as np
from test_support import IsolatedDefaultsTestCase
import feasibility.engine as e


class TestSizingRoutingHours(IsolatedDefaultsTestCase):
    def setUp(self):
        super().setUp()
        self.years = [2020, 2021]
        self.valid = np.ones((len(self.years), 24), dtype=bool)
        self.load = np.full((len(self.years), 24), 1.0)
        self.ev = np.zeros((len(self.years), 24))
        self.pzo = np.full((len(self.years), 24), 0.20)
        self.zero_env = e.envelope([0.0] * len(self.years))

    def _base_precomputed(self, counts, pv_per_hour=2.0):
        pv = np.full((len(self.years), 24), pv_per_hour)
        return {
            'load': self.load,
            'ev': self.ev,
            'valid': self.valid,
            'pzo': self.pzo,
            'years': self.years,
            'zero_import_env': self.zero_env,
            'zero_import_median': 0.0,
            'sqrt_rte': 0.95,
            'vat_mult': 1.0,
            'prem_mult': 1.0,
            'pv_profile_cache': {counts: pv},
            'month': np.ones(24, dtype=int),
            'hour': np.arange(24, dtype=int),
        }

    def _base_settings(self, **kwargs):
        base = e.validate({})
        base.update({
            'horizon_years': 2,
            'routing_priority': e.ROUTING_AUTO,
            'routing_switch_hour': 11,
            'deduction_rate': 0.0,
            'pv_battery_coupling': 'dc_coupled',
            'pv_first_year_loss': 0.0,
            'pv_annual_fade': 0.0,
        })
        base.update(kwargs)
        return base

    def _run_candidate(self, s, counts, battery_kwh, outcome_fn=None, opt=None, schedule_outcome_fn=None):
        opt = opt or {}
        pre = self._base_precomputed(counts)
        dispatch_calls = []
        last_stry = {}

        def fake_physical(s_try):
            last_stry["s"] = s_try
            return (0., 1., 1., 1., 1., 0., 1.)

        def fake_dispatch(*args, **kwargs):
            st = last_stry["s"]
            route = st["routing_priority"]
            hour = st.get("routing_switch_hour")
            dispatch_calls.append((route, hour))
            if schedule_outcome_fn is not None:
                clip_val, _ = schedule_outcome_fn(st)
            else:
                clip_val, _ = outcome_fn(route, hour)
            annual = np.zeros((s["horizon_years"], len(self.years), 14), dtype=float)
            if np.isscalar(clip_val):
                annual[:, :, 9] = float(clip_val) * 48.0
            else:
                annual[:, :, 9] = np.array(clip_val, dtype=float) * 48.0
            return annual

        def fake_financial(s_try, annual_try, kwp, calculate_irr=False, ranking_only=False, candidate_only=False):
            route = s_try["routing_priority"]
            hour = s_try.get("routing_switch_hour")
            if schedule_outcome_fn is not None:
                _, npv = schedule_outcome_fn(s_try)
            else:
                _, npv = outcome_fn(route, hour)
            if isinstance(npv, dict):
                proj_val = float(npv.get("project", npv.get("pv_incremental", 0.0)))
                eq_val = float(npv.get("equity", npv.get("solar_equity", 0.0)))
            else:
                proj_val = float(npv)
                eq_val = float(npv)
            proj_env = {"median": proj_val, "min": proj_val, "max": proj_val, "mean": proj_val}
            eq_env = {"median": eq_val, "min": eq_val, "max": eq_val, "mean": eq_val}
            pay = {"median": 5.0, "min": 5.0, "max": 5.0, "mean": 5.0}
            fin = {
                "pv_incremental": {
                    "npv": proj_env,
                    "npv_eur": proj_env,
                    "discounted_payback": pay,
                    "simple_payback": pay,
                    "discounted_payback_of_mean_cash_flows": 5.0,
                    "simple_payback_of_mean_cash_flows": 5.0,
                    "npv_by_weather": [proj_val] * len(self.years),
                },
                "solar_equity": {
                    "npv": eq_env,
                    "npv_eur": eq_env,
                    "discounted_payback": pay,
                    "simple_payback": pay,
                    "discounted_payback_of_mean_cash_flows": 5.0,
                    "simple_payback_of_mean_cash_flows": 5.0,
                    "npv_by_weather": [eq_val] * len(self.years),
                },
                "capex_eur": 5000.0,
                "solar_loan_principal_eur": 0.0,
                "solar_loan_ratio": 1.0,
                "loan_principal_eur": 0.0,
                "initial_solar_equity_eur": 5000.0,
                "total_loan_scope_eur": 5000.0,
            }
            return fin, None

        fake_data = {"month": np.ones(24, dtype=int), "hour": np.arange(24, dtype=int)}

        with patch.object(e, "data", return_value=fake_data), \
             patch.object(e, "physical_parameters", side_effect=fake_physical), \
             patch.object(e, "dispatch_lifetime", side_effect=fake_dispatch), \
             patch.object(e, "_financial", side_effect=fake_financial), \
             patch.object(e, "_pv_metric_basis", return_value=(np.ones(len(self.years)), np.ones(len(self.years)))):
            res = e._evaluate_solar_candidate(s, opt, counts, battery_kwh, precomputed=pre)
        return res, dispatch_calls

    def test_default_clipping_caps_from_validate(self):
        default_settings = e.validate({})
        self.assertEqual(default_settings["clipping_dc_max"], 0.04)
        self.assertEqual(default_settings["clipping_ac_max"], 0.03)

        outcome = lambda r, h: (0.035, 3000.)
        s_dc = self._base_settings(pv_battery_coupling="dc_coupled")
        res_dc, _ = self._run_candidate(s_dc, (10, 0, 0, 0, 0, 0), 5.0, outcome)
        self.assertEqual(res_dc["clipping_fraction_limit"], 0.04)
        self.assertTrue(res_dc["physical_feasible"])

        s_ac = self._base_settings(pv_battery_coupling="ac_coupled")
        res_ac, _ = self._run_candidate(s_ac, (10, 0, 0, 0, 0, 0), 5.0, outcome)
        self.assertEqual(res_ac["clipping_fraction_limit"], 0.03)
        self.assertFalse(res_ac["physical_feasible"])

    def test_two_distinct_candidates_choose_different_winning_hours(self):
        s = self._base_settings()
        fn_a = lambda r, h: (0.01, 3500. if (r, h) == ("scheduled_hybrid", 10) else 2000.)
        res_a, calls_a = self._run_candidate(s, (10, 0, 0, 0, 0, 0), 5.0, fn_a)
        self.assertEqual(res_a["routing_priority"], "scheduled_hybrid")
        self.assertEqual(res_a["routing_switch_hour"], 10)
        self.assertEqual(res_a["settings"]["routing_switch_hour"], 10)
        for h in (10, 11, 12):
            self.assertIn(("scheduled_hybrid", h), calls_a)

        fn_b = lambda r, h: (0.01, 5000. if (r, h) == ("scheduled_hybrid", 12) else 2000.)
        res_b, calls_b = self._run_candidate(s, (20, 0, 0, 0, 0, 0), 10.0, fn_b)
        self.assertEqual(res_b["routing_priority"], "scheduled_hybrid")
        self.assertEqual(res_b["routing_switch_hour"], 12)
        self.assertEqual(res_b["settings"]["routing_switch_hour"], 12)
        for h in (10, 11, 12):
            self.assertIn(("scheduled_hybrid", h), calls_b)

    def test_infeasible_highest_npv_loses_to_feasible(self):
        s = self._base_settings(clipping_dc_max=0.04)
        def outcome(r, h):
            if (r, h) == ("scheduled_hybrid", 11):
                return (0.06, 9000.)  # Infeasible despite higher NPV
            if (r, h) == ("scheduled_hybrid", 10):
                return (0.02, 4000.)  # Feasible winner
            return (0.02, 2000.)
        res, _ = self._run_candidate(s, (15, 0, 0, 0, 0, 0), 5.0, outcome)
        self.assertEqual(res["routing_priority"], "scheduled_hybrid")
        self.assertEqual(res["routing_switch_hour"], 10)
        self.assertTrue(res["physical_feasible"])
        self.assertEqual(res["npv_median"], 4000.)

    def test_all_weather_and_project_year_violation_coverage(self):
        s = self._base_settings(clipping_dc_max=0.04)
        clipping_by_year_weather = np.array([
            [0.02, 0.02],
            [0.02, 0.05],
        ])
        outcome = lambda r, h: (clipping_by_year_weather, 4000.)
        res, _ = self._run_candidate(s, (10, 0, 0, 0, 0, 0), 5.0, outcome)
        self.assertFalse(res["physical_feasible"])
        self.assertAlmostEqual(res["annual_clipping_fraction_max"], 0.05)

    def test_dynamic_reserve_can_win(self):
        s = self._base_settings()
        outcome = lambda r, h: (0.01, 7500. if r == "dynamic_reserve" else 4000.)
        res, _ = self._run_candidate(s, (15, 0, 0, 0, 0, 0), 5.0, outcome)
        self.assertEqual(res["routing_priority"], "dynamic_reserve")
        self.assertEqual(res["resolved_routing_priority"], "dynamic_reserve")
        self.assertEqual(res["settings"]["routing_priority"], "dynamic_reserve")

    def test_manual_chosen_hour_and_configured_hour(self):
        s_manual = self._base_settings(routing_priority="scheduled_hybrid", routing_switch_hour=14)
        shortlist = [{
            "routing_switch_hour": 14,
            "scheduled_start_month": 1,
            "scheduled_end_month": 12,
        }]
        with patch("feasibility.schedule_optimizer.sizing_schedule_candidates", return_value=shortlist):
            res, calls = self._run_candidate(s_manual, (10, 0, 0, 0, 0, 0), 5.0, lambda r, h: (0.01, 3000.))
        self.assertEqual(calls, [("scheduled_hybrid", 14)])
        self.assertEqual(res["routing_priority"], "scheduled_hybrid")
        self.assertEqual(res["routing_switch_hour"], 14)

        s_auto = self._base_settings(routing_priority=e.ROUTING_AUTO, routing_switch_hour=14)
        fn_cfg = lambda r, h: (0.01, 6000. if (r, h) == ("scheduled_hybrid", 14) else 3000.)
        res_cfg, calls_cfg = self._run_candidate(s_auto, (10, 0, 0, 0, 0, 0), 5.0, fn_cfg)
        for h in (10, 11, 12, 14):
            self.assertIn(("scheduled_hybrid", h), calls_cfg)
        self.assertEqual(res_cfg["routing_switch_hour"], 14)

    def test_zero_battery_evaluates_single_route_as_pv_only(self):
        res, calls = self._run_candidate(self._base_settings(), (10, 0, 0, 0, 0, 0), 0.0, lambda r, h: (0.01, 1000.))
        self.assertEqual(len(calls), 1)
        self.assertEqual(res["resolved_routing_priority"], "pv_only")
        self.assertEqual(res["effective_pv_battery_coupling"], "ac_equivalent")


    def test_scheduled_hybrid_shortlist_evaluates_different_schedules_and_copies_settings(self):
        s = self._base_settings(
            routing_priority="scheduled_hybrid",
            routing_switch_hour=11,
            scheduled_start_month=1,
            scheduled_end_month=12,
        )
        shortlist = [
            {"routing_switch_hour": 10, "scheduled_start_month": 5, "scheduled_end_month": 9},
            {"routing_switch_hour": 13, "scheduled_start_month": 4, "scheduled_end_month": 10},
        ]

        def schedule_outcome(st):
            hour = st.get("routing_switch_hour")
            if hour == 10:
                return (0.01, 3500.0)
            if hour == 13:
                return (0.01, 4200.0)
            return (0.01, 1000.0)

        with patch("feasibility.schedule_optimizer.sizing_schedule_candidates", return_value=shortlist) as mock_helper:
            res, calls = self._run_candidate(
                s, (10, 0, 0, 0, 0, 0), 5.0, schedule_outcome_fn=schedule_outcome
            )

        mock_helper.assert_called_once()
        call_kwargs = mock_helper.call_args[1] if len(mock_helper.call_args) > 1 and mock_helper.call_args[1] else getattr(mock_helper.call_args, "kwargs", {})
        self.assertEqual(call_kwargs.get("financial_basis", "project"), "project")
        self.assertEqual(call_kwargs.get("max_candidates", 6), 6)

        self.assertIn(("scheduled_hybrid", 10), calls)
        self.assertIn(("scheduled_hybrid", 13), calls)
        self.assertNotEqual(calls, [("scheduled_hybrid", 11)])

        self.assertEqual(res["routing_switch_hour"], 13)
        self.assertEqual(res["scheduled_start_month"], 4)
        self.assertEqual(res["scheduled_end_month"], 10)
        self.assertEqual(res["settings"]["routing_switch_hour"], 13)
        self.assertEqual(res["settings"]["scheduled_start_month"], 4)
        self.assertEqual(res["settings"]["scheduled_end_month"], 10)
        self.assertEqual(res["npv_median"], 4200.0)
        self.assertTrue(res["physical_feasible"])

    def test_infeasible_higher_npv_schedule_loses_to_feasible(self):
        s = self._base_settings(
            routing_priority="scheduled_hybrid",
            routing_switch_hour=11,
            clipping_dc_max=0.04,
        )
        shortlist = [
            {"routing_switch_hour": 10, "scheduled_start_month": 4, "scheduled_end_month": 10},
            {"routing_switch_hour": 12, "scheduled_start_month": 5, "scheduled_end_month": 9},
        ]

        def schedule_outcome(st):
            hour = st.get("routing_switch_hour")
            if hour == 10:
                return (0.06, 9000.0)  # Infeasible: clipping exceeds cap
            if hour == 12:
                return (0.02, 4500.0)  # Feasible winner
            return (0.02, 1000.0)

        with patch("feasibility.schedule_optimizer.sizing_schedule_candidates", return_value=shortlist):
            res, _ = self._run_candidate(
                s, (15, 0, 0, 0, 0, 0), 5.0, schedule_outcome_fn=schedule_outcome
            )

        self.assertEqual(res["routing_switch_hour"], 12)
        self.assertEqual(res["scheduled_start_month"], 5)
        self.assertEqual(res["scheduled_end_month"], 9)
        self.assertEqual(res["settings"]["routing_switch_hour"], 12)
        self.assertEqual(res["settings"]["scheduled_start_month"], 5)
        self.assertEqual(res["settings"]["scheduled_end_month"], 9)
        self.assertTrue(res["physical_feasible"])
        self.assertEqual(res["npv_median"], 4500.0)

    def test_financial_basis_chooses_correct_project_vs_equity_schedule(self):
        s = self._base_settings(routing_priority="scheduled_hybrid", routing_switch_hour=11)
        shortlist = [
            {"routing_switch_hour": 10, "scheduled_start_month": 5, "scheduled_end_month": 9},
            {"routing_switch_hour": 13, "scheduled_start_month": 4, "scheduled_end_month": 10},
        ]

        def schedule_outcome(st):
            hour = st.get("routing_switch_hour")
            if hour == 10:
                return (0.01, {"project": 5000.0, "equity": 2000.0})
            if hour == 13:
                return (0.01, {"project": 3000.0, "equity": 6000.0})
            return (0.01, {"project": 1000.0, "equity": 1000.0})

        with patch("feasibility.schedule_optimizer.sizing_schedule_candidates", return_value=shortlist) as mock_helper:
            res_proj, _ = self._run_candidate(
                s, (10, 0, 0, 0, 0, 0), 5.0, opt={"financial_basis": "project"},
                schedule_outcome_fn=schedule_outcome,
            )
            call_kwargs = mock_helper.call_args[1] if len(mock_helper.call_args) > 1 and mock_helper.call_args[1] else getattr(mock_helper.call_args, "kwargs", {})
            self.assertEqual(call_kwargs.get("financial_basis", "project"), "project")

        self.assertEqual(res_proj["routing_switch_hour"], 10)
        self.assertEqual(res_proj["scheduled_start_month"], 5)
        self.assertEqual(res_proj["scheduled_end_month"], 9)
        self.assertEqual(res_proj["npv_median"], 5000.0)

        with patch("feasibility.schedule_optimizer.sizing_schedule_candidates", return_value=shortlist) as mock_helper:
            res_eq, _ = self._run_candidate(
                s, (10, 0, 0, 0, 0, 0), 5.0, opt={"financial_basis": "after_financing"},
                schedule_outcome_fn=schedule_outcome,
            )
            call_kwargs = mock_helper.call_args[1] if len(mock_helper.call_args) > 1 and mock_helper.call_args[1] else getattr(mock_helper.call_args, "kwargs", {})
            self.assertEqual(call_kwargs.get("financial_basis"), "after_financing")

        self.assertEqual(res_eq["routing_switch_hour"], 13)
        self.assertEqual(res_eq["scheduled_start_month"], 4)
        self.assertEqual(res_eq["scheduled_end_month"], 10)
        self.assertEqual(res_eq["npv_median"], 6000.0)

    def test_different_hardware_configurations_choose_different_schedules_changing_ranking(self):
        s = self._base_settings(
            routing_priority="scheduled_hybrid",
            routing_switch_hour=11,
            scheduled_start_month=1,
            scheduled_end_month=12,
        )

        def fake_helper(s_cand, **kwargs):
            b_kwh = s_cand.get("battery_kwh", 0.0)
            if b_kwh <= 5.0:
                return [{"routing_switch_hour": 10, "scheduled_start_month": 5, "scheduled_end_month": 9}]
            else:
                return [{"routing_switch_hour": 13, "scheduled_start_month": 3, "scheduled_end_month": 10}]

        def schedule_outcome(st):
            b_kwh = st.get("battery_kwh", 0.0)
            h = st.get("routing_switch_hour")
            if b_kwh <= 5.0:
                npv = 4000.0 if h == 10 else 2500.0
            else:
                npv = 3500.0 if h == 13 else 3000.0
            return (0.01, npv)

        with patch("feasibility.schedule_optimizer.sizing_schedule_candidates", side_effect=fake_helper):
            res_small, _ = self._run_candidate(
                s, (10, 0, 0, 0, 0, 0), 5.0, schedule_outcome_fn=schedule_outcome
            )
            res_large, _ = self._run_candidate(
                s, (20, 0, 0, 0, 0, 0), 10.0, schedule_outcome_fn=schedule_outcome
            )

        self.assertEqual(res_small["routing_switch_hour"], 10)
        self.assertEqual(res_small["scheduled_start_month"], 5)
        self.assertEqual(res_small["scheduled_end_month"], 9)

        self.assertEqual(res_large["routing_switch_hour"], 13)
        self.assertEqual(res_large["scheduled_start_month"], 3)
        self.assertEqual(res_large["scheduled_end_month"], 10)

        # At original input schedule (hour 11), large hardware scored higher
        _, fixed_small_npv = schedule_outcome({**s, "battery_kwh": 5.0, "routing_switch_hour": 11})
        _, fixed_large_npv = schedule_outcome({**s, "battery_kwh": 10.0, "routing_switch_hour": 11})
        self.assertGreater(fixed_large_npv, fixed_small_npv)

        # Under optimized candidate schedules, small hardware wins (ranking reversed)
        self.assertGreater(res_small["npv_median"], res_large["npv_median"])

    def test_no_pv_and_zero_battery_skip_helper(self):
        s = self._base_settings(routing_priority="scheduled_hybrid", routing_switch_hour=11)

        with patch("feasibility.schedule_optimizer.sizing_schedule_candidates") as mock_helper:
            # Zero battery with PV
            res_zero_bat, _ = self._run_candidate(
                s, (10, 0, 0, 0, 0, 0), 0.0, lambda r, h: (0.01, 1000.0)
            )
            mock_helper.assert_not_called()
            self.assertEqual(res_zero_bat["resolved_routing_priority"], "pv_only")

            # Zero PV and zero battery
            res_zero_both, _ = self._run_candidate(
                s, (0, 0, 0, 0, 0, 0), 0.0, lambda r, h: (0.01, 0.0)
            )
            mock_helper.assert_not_called()
            self.assertEqual(res_zero_both["panel_count"], 0)
            self.assertEqual(res_zero_both["battery_kwh"], 0.0)
if __name__ == "__main__":
    unittest.main()
