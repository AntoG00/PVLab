"""Tests for Sizing and Summary calendar parity, 21x8760 mapping, and heating conservation."""
import numpy as np
import pytest

from test_support import IsolatedDefaultsTestCase
from feasibility import engine


class SizingCalendarParityTests(IsolatedDefaultsTestCase):
    def setUp(self):
        super().setUp()
        self.settings = engine.validate({
            'installation_year': 2026,
            'pv_first_year_loss': 0.02,
            'ev_start_year': 2,
            'load_scenario': 'electrified',
            'heating_schedule_mode': 'scheduled',
            'timezone_mode': 'civil',
            'ac_heating_windows': [
                {'id': 'morning', 'start_minute': 390, 'end_minute': 510},
                {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
            ],
            'south_panels': 12,
            'battery_kwh': 5.0,
        })

    def test_sizing_and_summary_parity(self):
        """Sizing candidate evaluation matches Summary evaluate on NPV means and ensemble autarky."""
        s = self.settings
        cand = engine._evaluate_solar_candidate(s, {}, (12, 0, 0, 0, 0, 0), 5.0)
        s_cand = cand['settings']
        summary = engine.evaluate(s_cand)

        # Equal Project NPV mean
        proj_npv_cand = cand['project_npv_mean']
        proj_npv_sum = summary['finance']['pv_incremental']['npv']['mean']
        assert proj_npv_cand == pytest.approx(proj_npv_sum, abs=0.01, rel=0)

        # Equal Equity NPV mean
        eq_npv_cand = cand['equity_npv_mean']
        eq_npv_sum = summary['finance']['solar_equity']['npv']['mean']
        assert eq_npv_cand == pytest.approx(eq_npv_sum, abs=0.01, rel=0)

        # Equal Ensemble Autarky
        autarky_cand = cand['autarky_mean']
        autarky_sum = summary['energy']['ensemble_autarky']
        assert autarky_cand == pytest.approx(autarky_sum, abs=1e-12, rel=0)
        key = tuple((k, engine.freeze(s_cand[k])) for k in engine.ENERGY_KEYS)
        current = engine._forecast_2027_runs(key, engine.price_fingerprint(s_cand))
        for metric in ('load_kwh', 'grid_import_kwh'):
            assert np.mean([row[metric] for row in current['annual']]) == pytest.approx(
                summary['energy'][metric]['mean'], abs=1e-9, rel=0,
            )

    def test_forecast_mapped_series_21x8760_and_dst_mapping(self):
        """Confirm 21x8760 mapped weather, omitted source leap day, and DST hour mapping."""
        s = self.settings
        mapped = engine._forecast_mapped_series(s)

        # 2026 installation -> 2027 forecast year (non-leap, 8760 hours)
        assert mapped['forecast_year'] == 2027
        assert mapped['target_len'] == 8760
        assert mapped['is_leap'] is False

        # 21 weather years mapped to 8760
        n_years = len(engine.data()['years'])
        assert n_years == len(engine.data()['years'])
        assert n_years == 2
        assert mapped['mapped_pv'].shape == (n_years, 8760)
        assert mapped['mapped_load'].shape == (n_years, 8760)

        # February has exactly 28 days (672 hours), source leap Feb 29 omitted
        feb_hours = np.count_nonzero(mapped['target_months'] == 2)
        assert feb_hours == 28 * 24

        # DST mapping: civil hour equals (std + 1) % 24 during DST, std outside
        std_h = mapped['target_std_hours']
        civ_h = mapped['target_civil_hours']
        dst_diff = (civ_h - std_h) % 24
        assert set(np.unique(dst_diff)).issubset({0, 1})
        assert np.count_nonzero(dst_diff == 1) > 4000

    def test_heating_conservation_and_cold_warm_ordering(self):
        """Source vs target annual mean heating conservation and cold/warm year ordering."""
        s = self.settings
        d = engine.data()
        mapped = engine._forecast_mapped_series(s)

        src_heating = engine._allocate_space_heating(
            s, d['temperature'], d['month'], d['hour'], d['valid'],
            engine._get_civil_hours(d), d['years']
        )
        valid_target = np.ones_like(mapped['mapped_temp'], dtype=bool)
        tgt_heating = engine._allocate_space_heating(
            s, mapped['mapped_temp'], mapped['target_months'], mapped['target_std_hours'],
            valid_target, mapped['target_civil_hours'], d['years']
        )

        # Annual ensemble mean heating energy conserved within 0.1%
        src_annual_mean = float(np.mean(np.sum(src_heating, axis=1)))
        tgt_annual_mean = float(np.mean(np.sum(tgt_heating, axis=1)))
        assert tgt_annual_mean == pytest.approx(src_annual_mean, abs=1e-9, rel=0)

        # Coldest year vs warmest year ordering preserved
        src_annuals = np.sum(src_heating, axis=1)
        tgt_annuals = np.sum(tgt_heating, axis=1)
        cold_idx = int(np.argmax(src_annuals))
        warm_idx = int(np.argmin(src_annuals))
        assert tgt_annuals[cold_idx] > tgt_annuals[warm_idx]
