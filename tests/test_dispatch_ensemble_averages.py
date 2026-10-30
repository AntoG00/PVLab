from feasibility.runtime_context import current_runtime
import test_support  # Must be imported first for live defaults isolation

from unittest.mock import patch
import numpy as np
import pytest

import feasibility.engine as e


@pytest.fixture(autouse=True)
def clear_dispatch_matrix_cache():
    e._dispatch_matrix_cached.cache_clear()
    yield
    e._dispatch_matrix_cached.cache_clear()


def _make_forecast_year(pv, load, imp, exp, soc, direct, charge, discharge,
                        mapped_pv=None, raw_pv_total=None, clipped=0.0,
                        true_losses=0.0, end_soc=None, weather_year=2000):
    pv_arr = np.asarray(pv, dtype=float)
    load_arr = np.asarray(load, dtype=float)
    imp_arr = np.asarray(imp, dtype=float)
    exp_arr = np.asarray(exp, dtype=float)
    soc_arr = np.asarray(soc, dtype=float)
    direct_arr = np.asarray(direct, dtype=float)
    charge_arr = np.asarray(charge, dtype=float)
    discharge_arr = np.asarray(discharge, dtype=float)

    trace = np.column_stack([
        pv_arr, load_arr, imp_arr, exp_arr,
        soc_arr, direct_arr, charge_arr, discharge_arr
    ])

    if mapped_pv is None:
        mapped_pv = pv_arr.copy()
    else:
        mapped_pv = np.asarray(mapped_pv, dtype=float)

    pv_kwh = float(pv_arr.sum())
    if raw_pv_total is None:
        raw_pv_total = float(mapped_pv.sum())

    if end_soc is None:
        end_soc = float(soc_arr[-1]) if len(soc_arr) > 0 else 0.0

    annual = {
        'weather_year': weather_year,
        'pv_input_kwh': float(raw_pv_total),
        'pv_kwh': float(pv_kwh),
        'load_kwh': float(load_arr.sum()),
        'grid_import_kwh': float(imp_arr.sum()),
        'grid_export_kwh': float(exp_arr.sum()),
        'direct_kwh': float(direct_arr.sum()),
        'battery_loss_kwh': float(true_losses),
        'end_soc_kwh': float(end_soc),
        'clipped_kwh': float(clipped),
    }

    return trace, mapped_pv, annual


class TestEnsembleAnnualFlows:
    """Numerical regression tests for _ensemble_annual_flows helper."""

    def test_arithmetic_mean_skewed_distribution_differs_from_median(self):
        settings = e.validate({
            'pv_dispatch_model': 'physical',
            'pv_battery_coupling': 'ac_equivalent',
            'battery_kwh': 10.0,
            'charge_kw': 5.0,
            'discharge_kw': 5.0,
            'inverter_kw': 5.0,
            'inverter_efficiency': 0.95,
        })

        # Year 0: flow = 100
        # Year 1: flow = 100
        # Year 2: flow = 400
        # Arithmetic mean = 200, median = 100
        t0, m0, a0 = _make_forecast_year(
            pv=[100.0], load=[100.0], imp=[50.0], exp=[50.0],
            soc=[2.0], direct=[50.0], charge=[0.0], discharge=[0.0],
            weather_year=2001
        )
        t1, m1, a1 = _make_forecast_year(
            pv=[100.0], load=[100.0], imp=[50.0], exp=[50.0],
            soc=[2.0], direct=[50.0], charge=[0.0], discharge=[0.0],
            weather_year=2002
        )
        t2, m2, a2 = _make_forecast_year(
            pv=[400.0], load=[400.0], imp=[200.0], exp=[200.0],
            soc=[8.0], direct=[200.0], charge=[0.0], discharge=[0.0],
            weather_year=2003
        )

        res_fc = {
            'traces': [t0, t1, t2],
            'annual': [a0, a1, a2],
            'mapped_data': {'mapped_pv': [m0, m1, m2]},
        }

        flows = e._ensemble_annual_flows(settings, res_fc)

        # Assert arithmetic mean, NOT median
        assert flows['pv_generation_kwh'] == pytest.approx(200.0)
        assert flows['pv_generation_kwh'] != 100.0
        assert flows['load_demand_kwh'] == pytest.approx(200.0)
        assert flows['grid_import_kwh'] == pytest.approx(100.0)
        assert flows['grid_export_kwh'] == pytest.approx(100.0)
        assert flows['direct_use_kwh'] == pytest.approx(100.0)
        assert flows['end_soc_kwh'] == pytest.approx(4.0)
        assert flows['weather_count'] == 3

    @pytest.mark.parametrize('coupling,factor', [('ac_equivalent', 1.0), ('dc_coupled', 0.9)])
    def test_exact_conservation_laws_including_end_soc(self, coupling, factor):
        settings = e.validate({
            'pv_dispatch_model': 'physical',
            'pv_battery_coupling': coupling,
            'battery_kwh': 10.0,
            'charge_kw': 5.0,
            'discharge_kw': 5.0,
            'inverter_kw': 5.0,
            'inverter_efficiency': 0.9,
        })

        # Year 0:
        # direct=30, charge=20 (phys), export=10 -> pv_gen = direct + factor*charge + export
        # load = direct + discharge + imp = 30 + 15 + 25 = 70
        # storage phys: chg(20) - dis(15) = true_losses(2) + end_soc(3)
        pv0_gen = 30.0 + factor * 20.0 + 10.0
        t0, m0, a0 = _make_forecast_year(
            pv=[pv0_gen], load=[70.0], imp=[25.0], exp=[10.0],
            soc=[3.0], direct=[30.0], charge=[20.0], discharge=[15.0],
            raw_pv_total=pv0_gen + 5.0, clipped=5.0, true_losses=2.0, end_soc=3.0,
            weather_year=2001
        )

        # Year 1:
        # direct=40, charge=30 (phys), export=15
        # load = direct(40) + dis(20) + imp(30) = 90
        # storage phys: chg(30) - dis(20) = true_losses(4) + end_soc(6)
        pv1_gen = 40.0 + factor * 30.0 + 15.0
        t1, m1, a1 = _make_forecast_year(
            pv=[pv1_gen], load=[90.0], imp=[30.0], exp=[15.0],
            soc=[6.0], direct=[40.0], charge=[30.0], discharge=[20.0],
            raw_pv_total=pv1_gen + 10.0, clipped=10.0, true_losses=4.0, end_soc=6.0,
            weather_year=2002
        )

        res_fc = {
            'traces': [t0, t1],
            'annual': [a0, a1],
            'mapped_data': {'mapped_pv': [m0, m1]},
        }

        flows = e._ensemble_annual_flows(settings, res_fc)

        # Exact PV conservation
        assert flows['pv_generation_kwh'] == pytest.approx(
            flows['direct_use_kwh'] + flows['battery_charge_kwh'] + flows['grid_export_kwh'], abs=1e-6
        )
        assert flows['pv_input_kwh'] == pytest.approx(
            flows['pv_generation_kwh'] + flows['clipped_kwh'], abs=1e-6
        )

        # Exact Load conservation
        assert flows['load_demand_kwh'] == pytest.approx(
            flows['direct_use_kwh'] + flows['battery_discharge_kwh'] + flows['grid_import_kwh'], abs=1e-6
        )

        # Exact Storage conservation (AC-equivalent flows)
        assert flows['battery_charge_kwh'] - flows['battery_discharge_kwh'] == pytest.approx(
            flows['storage_losses_kwh'] + flows['storage_delta_kwh'], abs=1e-6
        )
        assert flows['storage_delta_kwh'] == pytest.approx(
            flows['end_soc_kwh'] - flows['initial_soc_kwh'], abs=1e-6
        )

        # Exact Physical Storage conservation
        assert flows['battery_charge_physical_kwh'] - flows['battery_discharge_kwh'] == pytest.approx(
            flows['storage_losses_physical_kwh'] + flows['storage_delta_stored_kwh'], abs=1e-6
        )
        assert flows['storage_delta_stored_kwh'] == pytest.approx(
            flows['end_soc_stored_kwh'] - flows['initial_soc_stored_kwh'], abs=1e-6
        )

    def test_percentiles_p10_p90_differ_from_min_max(self):
        settings = e.validate({
            'pv_dispatch_model': 'physical',
            'pv_battery_coupling': 'ac_equivalent',
            'battery_kwh': 10.0,
            'charge_kw': 5.0,
            'discharge_kw': 5.0,
        })

        # 5 distinct years so P10 and P90 are distinct interpolated points, not min/max
        values = [100.0, 200.0, 300.0, 400.0, 1000.0]
        traces, mapped, annuals = [], [], []
        for i, val in enumerate(values):
            t, m, a = _make_forecast_year(
                pv=[val], load=[val], imp=[val * 0.5], exp=[val * 0.5],
                soc=[5.0], direct=[val * 0.5], charge=[0.0], discharge=[0.0],
                weather_year=2000 + i
            )
            traces.append(t)
            mapped.append(m)
            annuals.append(a)

        res_fc = {
            'traces': traces,
            'annual': annuals,
            'mapped_data': {'mapped_pv': mapped},
        }

        flows = e._ensemble_annual_flows(settings, res_fc)
        pv_ranges = flows['flow_ranges']['pv_generation_kwh']

        expected_p10 = float(np.percentile(values, 10))
        expected_p90 = float(np.percentile(values, 90))

        assert pv_ranges['p10'] == pytest.approx(expected_p10, abs=1e-4)
        assert pv_ranges['p90'] == pytest.approx(expected_p90, abs=1e-4)
        assert pv_ranges['p10'] != min(values)
        assert pv_ranges['p90'] != max(values)

    def test_autarky_ratio_of_means_differs_from_mean_of_ratios(self):
        settings = e.validate({
            'pv_dispatch_model': 'physical',
            'pv_battery_coupling': 'ac_equivalent',
            'battery_kwh': 10.0,
        })

        # Year 0: load=1000, imp=200 -> autarky_0 = 1 - 200/1000 = 0.8
        # Year 1: load=4000, imp=2400 -> autarky_1 = 1 - 2400/4000 = 0.4
        # Year 2: load=2000, imp=1000 -> autarky_2 = 1 - 1000/2000 = 0.5
        t0, m0, a0 = _make_forecast_year(
            pv=[800.0], load=[1000.0], imp=[200.0], exp=[0.0],
            soc=[0.0], direct=[800.0], charge=[0.0], discharge=[0.0]
        )
        t1, m1, a1 = _make_forecast_year(
            pv=[1600.0], load=[4000.0], imp=[2400.0], exp=[0.0],
            soc=[0.0], direct=[1600.0], charge=[0.0], discharge=[0.0]
        )
        t2, m2, a2 = _make_forecast_year(
            pv=[1000.0], load=[2000.0], imp=[1000.0], exp=[0.0],
            soc=[0.0], direct=[1000.0], charge=[0.0], discharge=[0.0]
        )

        res_fc = {
            'traces': [t0, t1, t2],
            'annual': [a0, a1, a2],
            'mapped_data': {'mapped_pv': [m0, m1, m2]},
        }

        flows = e._ensemble_annual_flows(settings, res_fc)

        mean_yearly_autarky = (0.8 + 0.4 + 0.5) / 3.0  # 0.5667
        mean_imp = (200.0 + 2400.0 + 1000.0) / 3.0
        mean_load = (1000.0 + 4000.0 + 2000.0) / 3.0
        expected_autarky = round(1.0 - mean_imp / mean_load, 4)  # 0.4857

        assert flows['autarky'] == pytest.approx(expected_autarky, abs=1e-4)
        assert flows['autarky'] != pytest.approx(mean_yearly_autarky, abs=1e-2)

        # And flow_ranges['autarky'] captures the per-year spread
        autarky_ranges = flows['flow_ranges']['autarky']
        assert autarky_ranges['p10'] == pytest.approx(round(float(np.percentile([0.8, 0.4, 0.5], 10)), 4))
        assert autarky_ranges['p90'] == pytest.approx(round(float(np.percentile([0.8, 0.4, 0.5], 90)), 4))

    def test_zero_cases_load_pv_storage(self):
        # 1. Zero load
        s_zero_load = e.validate({'battery_kwh': 10.0, 'load_scenario': 'baseline'})
        t_zl, m_zl, a_zl = _make_forecast_year(
            pv=[100.0], load=[0.0], imp=[0.0], exp=[100.0],
            soc=[0.0], direct=[0.0], charge=[0.0], discharge=[0.0]
        )
        flows_zl = e._ensemble_annual_flows(s_zero_load, {
            'traces': [t_zl], 'annual': [a_zl], 'mapped_data': {'mapped_pv': [m_zl]}
        })
        assert flows_zl['load_demand_kwh'] == 0.0
        assert flows_zl['grid_import_kwh'] == 0.0
        assert flows_zl['autarky'] == 0.0

        # 2. Zero PV
        t_zp, m_zp, a_zp = _make_forecast_year(
            pv=[0.0], load=[50.0], imp=[50.0], exp=[0.0],
            soc=[0.0], direct=[0.0], charge=[0.0], discharge=[0.0],
            raw_pv_total=0.0, clipped=0.0
        )
        flows_zp = e._ensemble_annual_flows(s_zero_load, {
            'traces': [t_zp], 'annual': [a_zp], 'mapped_data': {'mapped_pv': [m_zp]}
        })
        assert flows_zp['pv_generation_kwh'] == 0.0
        assert flows_zp['pv_input_kwh'] == 0.0
        assert flows_zp['clipped_kwh'] == 0.0
        assert flows_zp['clipped_pct'] == 0.0

        # 3. Zero storage
        s_zero_bat = e.validate({'battery_kwh': 0.0})
        t_zb, m_zb, a_zb = _make_forecast_year(
            pv=[50.0], load=[50.0], imp=[25.0], exp=[25.0],
            soc=[0.0], direct=[25.0], charge=[0.0], discharge=[0.0]
        )
        flows_zb = e._ensemble_annual_flows(s_zero_bat, {
            'traces': [t_zb], 'annual': [a_zb], 'mapped_data': {'mapped_pv': [m_zb]}
        })
        assert flows_zb['battery_charge_kwh'] == 0.0
        assert flows_zb['battery_discharge_kwh'] == 0.0
        assert flows_zb['storage_losses_kwh'] == 0.0
        assert flows_zb['storage_delta_kwh'] == 0.0

    def test_effective_dc_conversion_and_charge_source_splits(self):
        s_dc = e.validate({
            'pv_dispatch_model': 'physical',
            'pv_battery_coupling': 'dc_coupled',
            'battery_kwh': 10.0,
            'inverter_kw': 5.0,
            'inverter_efficiency': 0.9,
            'charge_kw': 5.0,
            'discharge_kw': 5.0,
        })

        # Hour 0: raw_pv=8.0 (above 5kW inverter), charge=3.0 DC
        # Solar above inverter: (8.0 - 5.0) / 0.9 = 3.333 kWh DC -> recaptured clipping = 3.0, normal solar = 0.0
        # Hour 1: raw_pv=3.0 (below 5kW inverter), charge=2.0 DC
        # Normal solar = 2.0, recaptured clipping = 0.0
        raw_pv = np.array([8.0, 3.0])
        # AC equivalent PV trace: pv_trace - (1 - factor)*charge
        charge_dc = np.array([3.0, 2.0])
        pv_eq = raw_pv.copy()  # mapped raw PV
        pv_col = np.array([5.0, 1.0])  # export/direct

        t, m, a = _make_forecast_year(
            pv=pv_col, load=[1.0, 1.0], imp=[0.0, 0.0], exp=[4.0, 0.0],
            soc=[3.0, 5.0], direct=[1.0, 1.0], charge=charge_dc, discharge=[0.0, 0.0],
            mapped_pv=raw_pv, raw_pv_total=11.0, clipped=1.0, true_losses=0.5, end_soc=5.0
        )

        res_fc = {
            'traces': [t],
            'annual': [a],
            'mapped_data': {'mapped_pv': [m]},
        }

        flows = e._ensemble_annual_flows(s_dc, res_fc)

        assert flows['physical_charge_basis'] == 'DC'
        assert flows['battery_charge_kwh'] == pytest.approx(0.9 * flows['battery_charge_physical_kwh'])
        assert flows['charged_from_normal_solar_kwh'] == pytest.approx(0.9 * flows['charged_from_normal_solar_physical_kwh'])
        assert flows['charged_from_recaptured_clipping_kwh'] == pytest.approx(0.9 * flows['charged_from_recaptured_clipping_physical_kwh'])
        assert flows['charged_from_recaptured_clipping_physical_kwh'] > 0.0
        assert flows['charged_from_normal_solar_physical_kwh'] > 0.0
        assert flows['battery_charge_physical_kwh'] == pytest.approx(
            flows['charged_from_normal_solar_physical_kwh'] + flows['charged_from_recaptured_clipping_physical_kwh']
        )


class TestDispatchMatrixEnsembleAverages:
    """Numerical regression tests for seasonal/annual hourly averages in dispatch matrix."""

    def test_seasonal_annual_hourly_means_and_profile_conservation(self):
        days = 365
        hours_per_day = 24
        t_total = days * hours_per_day

        settings = e.validate({
            'pv_dispatch_model': 'physical',
            'pv_battery_coupling': 'ac_equivalent',
            'battery_kwh': 10.0,
            'charge_kw': 5.0,
            'discharge_kw': 5.0,
            'inverter_kw': 5.0,
            'inverter_efficiency': 1.0,
            'load_scenario': 'baseline',
        })
        s = e._routing_settings(settings)
        key = tuple((k, e.freeze(s[k])) for k in e.ENERGY_KEYS)
        version = e.price_fingerprint(s)

        # 3 weather years with skewed hourly values so median != mean
        # Year 0: pv=1.0, load=1.0, imp=0.5, exp=0.5, direct=0.5, soc=2.0
        # Year 1: pv=1.0, load=1.0, imp=0.5, exp=0.5, direct=0.5, soc=2.0
        # Year 2: pv=7.0, load=4.0, imp=2.0, exp=5.0, direct=2.0, soc=8.0
        # Cross-weather means: pv=3.0, load=2.0, imp=1.0, exp=2.0, direct=1.0, soc=4.0
        # Cross-weather medians: pv=1.0, load=1.0, imp=0.5, exp=0.5, direct=0.5, soc=2.0
        def _build_full_year(pv_val, load_val, imp_val, exp_val, soc_val, direct_val, w_yr):
            pv_series = np.full(t_total, pv_val)
            load_series = np.full(t_total, load_val)
            imp_series = np.full(t_total, imp_val)
            exp_series = np.full(t_total, exp_val)
            soc_series = np.full(t_total, soc_val)
            # A low-reserve day every third day makes the within-year mean
            # differ from its median as well as differing between weather years.
            soc_series.reshape(days, 24)[::3] *= 0.5
            direct_series = np.full(t_total, direct_val)
            charge_series = np.zeros(t_total)
            discharge_series = np.zeros(t_total)

            t, m, a = _make_forecast_year(
                pv=pv_series, load=load_series, imp=imp_series, exp=exp_series,
                soc=soc_series, direct=direct_series, charge=charge_series, discharge=discharge_series,
                raw_pv_total=pv_val * t_total, clipped=0.0, true_losses=0.0, end_soc=soc_val,
                weather_year=w_yr
            )
            return t, m, a

        t0, m0, a0 = _build_full_year(1.0, 1.0, 0.5, 0.5, 2.0, 0.5, 2005)
        t1, m1, a1 = _build_full_year(1.0, 1.0, 0.5, 0.5, 2.0, 0.5, 2006)
        t2, m2, a2 = _build_full_year(7.0, 4.0, 2.0, 5.0, 8.0, 2.0, 2007)

        mock_forecast = {
            'traces': [t0, t1, t2],
            'annual': [a0, a1, a2],
            'mapped_data': {
                'mapped_pv': [m0, m1, m2],
                'mapped_load': [np.full(t_total, 1.0), np.full(t_total, 1.0), np.full(t_total, 4.0)],
                'mapped_pzo': [np.zeros(t_total), np.zeros(t_total), np.zeros(t_total)],
                'target_months': np.zeros(12),
                'target_std_hours': np.zeros(12),
            },
            'picks': {'low': 0, 'median': 1, 'high': 2},
            'forecast_year': 2027,
        }

        e._dispatch_matrix_cached.cache_clear()
        with patch.object(e, '_forecast_2027_runs', return_value=mock_forecast), \
             patch.object(e, 'soc_ceiling_series', return_value=np.zeros(t_total)), \
             patch.object(e, '_trace_routing_telemetry', return_value=(np.zeros(t_total), None)):

            mat_res = e._dispatch_matrix_cached(current_runtime().key, key, version, 'forecast_2027', 'baseline', 'median')

        annual_season = mat_res['matrix']['seasons']['annual']
        assert len(annual_season) == 24

        # Check cross-weather hourly means vs medians
        for h in range(24):
            hour_data = annual_season[h]
            # Mean is 3.0, median is 1.0
            assert hour_data['pv'] == pytest.approx(3.0)
            assert hour_data['pv'] != 1.0
            # Mean is 2.0, median is 1.0
            assert hour_data['load'] == pytest.approx(2.0)
            # Mean is 1.0, median is 0.5
            assert hour_data['import'] == pytest.approx(1.0)
            # Mean is 2.0, median is 0.5
            assert hour_data['export'] == pytest.approx(2.0)
            daily_soc_factor = 1.0 - 0.5 * len(range(0, days, 3)) / days
            assert hour_data['soc'] == pytest.approx(round(4.0 * daily_soc_factor, 4))
            assert hour_data['soc'] != 2.0
            # SoC envelope reflects P10-P90 across weather years
            expected_soc_p10 = round(float(np.percentile([2.0, 2.0, 8.0], 10)) * daily_soc_factor, 4)
            expected_soc_p90 = round(float(np.percentile([2.0, 2.0, 8.0], 90)) * daily_soc_factor, 4)
            assert hour_data['soc_envelope'] == [expected_soc_p10, expected_soc_p90]

        # Verify annual average hourly profile * 365 equals mean annual flow exactly
        annual_flows = mat_res['annual_flows']
        pv_annual_profile_sum = sum(h['pv'] for h in annual_season) * 365.0
        load_annual_profile_sum = sum(h['load'] for h in annual_season) * 365.0
        import_annual_profile_sum = sum(h['import'] for h in annual_season) * 365.0
        export_annual_profile_sum = sum(h['export'] for h in annual_season) * 365.0
        direct_annual_profile_sum = sum(h['direct'] for h in annual_season) * 365.0

        assert pv_annual_profile_sum == pytest.approx(annual_flows['pv_generation_kwh'], abs=0.5)
        assert load_annual_profile_sum == pytest.approx(annual_flows['load_demand_kwh'], abs=0.5)
        assert import_annual_profile_sum == pytest.approx(annual_flows['grid_import_kwh'], abs=0.5)
        assert export_annual_profile_sum == pytest.approx(annual_flows['grid_export_kwh'], abs=0.5)
        assert direct_annual_profile_sum == pytest.approx(annual_flows['direct_use_kwh'], abs=0.5)

        # Verify selected monthly / analogue ledger is unchanged single-weather analogue (Year 1, 2006)
        assert mat_res['weather_year'] == 2006
        analogue = mat_res['analogue_annual_flows']
        assert analogue['weather_year'] == 2006
        assert analogue['pv_generation_kwh'] == pytest.approx(1.0 * t_total, abs=0.5)
        assert analogue['load_demand_kwh'] == pytest.approx(1.0 * t_total, abs=0.5)
        assert analogue['grid_import_kwh'] == pytest.approx(0.5 * t_total, abs=0.5)
        assert analogue['grid_export_kwh'] == pytest.approx(0.5 * t_total, abs=0.5)

        # Monthly matrix has chosen weather year (2006) analogue stats
        jan_h0 = mat_res['matrix']['months'][0][0]
        assert jan_h0['weather_year'] == 2006
        assert jan_h0['statistic'] == 'chosen_weather_analogue_mean'
        assert jan_h0['soc'] == 2.0  # Median of single year (not ensemble cross-weather mean 4.0)

    def test_monthly_clipping_ensemble_averages_and_conservation(self):
        days = 365
        hours_per_day = 24
        t_total = days * hours_per_day

        settings = e.validate({
            'pv_dispatch_model': 'physical',
            'pv_battery_coupling': 'ac_equivalent',
            'battery_kwh': 10.0,
            'charge_kw': 5.0,
            'discharge_kw': 5.0,
            'inverter_kw': 5.0,
            'inverter_efficiency': 1.0,
            'load_scenario': 'baseline',
        })
        s = e._routing_settings(settings)
        key = tuple((k, e.freeze(s[k])) for k in e.ENERGY_KEYS)
        version = e.price_fingerprint(s)

        # 3 weather years:
        # Years 0 and 1: low PV, no clipping (raw_pv = 1.0, pv = 1.0)
        # Year 2: high PV with clipping (raw_pv = 8.0, delivered pv = 5.0 capped at inverter)
        # Year 1 is the chosen analogue (median).
        def _build_year(raw_pv_val, delivered_pv_val, w_yr):
            pv_series = np.full(t_total, delivered_pv_val)
            raw_pv_series = np.full(t_total, raw_pv_val)
            load_series = np.full(t_total, 1.0)
            imp_series = np.full(t_total, 0.5)
            exp_series = np.full(t_total, max(0.0, delivered_pv_val - 1.0))
            soc_series = np.full(t_total, 2.0)
            direct_series = np.full(t_total, 1.0)
            charge_series = np.zeros(t_total)
            discharge_series = np.zeros(t_total)
            clipped_val = (raw_pv_val - delivered_pv_val) * t_total

            t, m, a = _make_forecast_year(
                pv=pv_series, load=load_series, imp=imp_series, exp=exp_series,
                soc=soc_series, direct=direct_series, charge=charge_series, discharge=discharge_series,
                mapped_pv=raw_pv_series, raw_pv_total=raw_pv_val * t_total,
                clipped=clipped_val, true_losses=0.0, end_soc=2.0,
                weather_year=w_yr
            )
            return t, m, a

        t0, m0, a0 = _build_year(1.0, 1.0, 2005)
        t1, m1, a1 = _build_year(1.0, 1.0, 2006)
        t2, m2, a2 = _build_year(8.0, 5.0, 2007)

        mock_forecast = {
            'traces': [t0, t1, t2],
            'annual': [a0, a1, a2],
            'mapped_data': {
                'mapped_pv': [m0, m1, m2],
                'mapped_load': [np.full(t_total, 1.0), np.full(t_total, 1.0), np.full(t_total, 1.0)],
                'mapped_pzo': [np.zeros(t_total), np.zeros(t_total), np.zeros(t_total)],
                'target_months': np.zeros(12),
                'target_std_hours': np.zeros(12),
            },
            'picks': {'low': 0, 'median': 1, 'high': 2},
            'forecast_year': 2027,
        }

        e._dispatch_matrix_cached.cache_clear()
        with patch.object(e, '_forecast_2027_runs', return_value=mock_forecast), \
             patch.object(e, 'soc_ceiling_series', return_value=np.zeros(t_total)), \
             patch.object(e, '_trace_routing_telemetry', return_value=(np.zeros(t_total), None)):

            mat_res = e._dispatch_matrix_cached(current_runtime().key, key, version, 'forecast_2027', 'baseline', 'median')

        monthly = mat_res['monthly_clipping']
        annual_flows = mat_res['annual_flows']
        analogue = mat_res['analogue_annual_flows']

        assert len(monthly) == 12

        # 1. Chosen analogue (Year 1) had 0 clipping and peak 0 kW
        assert analogue['clipped_kwh'] == 0.0
        assert analogue['clipping_hours'] == 0
        assert analogue['peak_curtailed_kw'] == 0.0

        # 2. Monthly clipping reflects arithmetic ensemble means, distinct from analogue
        for m in monthly:
            import calendar
            month_hours = calendar.monthrange(2027, m['month'])[1] * 24
            assert m['pv_input_kwh'] == pytest.approx(month_hours * 10 / 3, abs=1e-6)
            assert m['clipped_kwh'] == pytest.approx(month_hours, abs=1e-6)
            assert m['pv_delivered_kwh'] == pytest.approx(month_hours * 7 / 3, abs=1e-6)
            assert m['clipping_hours'] == pytest.approx(month_hours / 3, abs=1e-6)
            # Ensemble mean raw input: (1.0 + 1.0 + 8.0) / 3 = 3.333 kW * hours_in_month
            assert m['pv_input_kwh'] > 1.0 * 28 * 24
            # Ensemble mean clipped: (0 + 0 + 3.0) / 3 = 1.0 kW * hours_in_month > 0
            assert m['clipped_kwh'] > 0.0
            # Ensemble mean hours: (0 + 0 + hours_in_month) / 3 = hours_in_month / 3 > 0
            assert m['clipping_hours'] > 0.0
            # Peak curtailed is ensemble maximum (3.0 kW), not analogue (0.0) nor mean (1.0)
            assert m['peak_curtailed_kw'] == pytest.approx(3.0, abs=0.01)
            # Energy balance: delivered + clipped == input
            assert m['pv_input_kwh'] == pytest.approx(m['pv_delivered_kwh'] + m['clipped_kwh'], abs=1e-5)
            # Curtailment percentage is ratio of mean curtailed to mean input
            expected_pct = 100.0 * m['clipped_kwh'] / m['pv_input_kwh']
            assert m['clipped_pct'] == pytest.approx(expected_pct, abs=0.02)

        # 3. Monthly sums match ensemble annual means
        assert sum(m['pv_input_kwh'] for m in monthly) == pytest.approx(annual_flows['pv_input_kwh'], abs=0.5)
        assert sum(m['clipped_kwh'] for m in monthly) == pytest.approx(annual_flows['clipped_kwh'], abs=0.5)
        assert sum(m['pv_delivered_kwh'] for m in monthly) == pytest.approx(annual_flows['pv_generation_kwh'], abs=0.5)
        assert sum(m['clipping_hours'] for m in monthly) == pytest.approx(annual_flows['clipping_hours'], abs=0.2)
        assert max(m['peak_curtailed_kw'] for m in monthly) >= annual_flows['peak_curtailed_kw'] - 1e-9

        # 4. Percentiles in flow_ranges reflect actual per-year percentage distribution vs ratio-of-means
        assert 'flow_ranges' in annual_flows
        assert 'clipped_pct' in annual_flows['flow_ranges']
        pct_range = annual_flows['flow_ranges']['clipped_pct']
        assert pct_range == {'p10': 0.0, 'p90': 30.0}
        assert annual_flows['flow_ranges']['clipped_kwh'] == {'p10': 0.0, 'p90': 21024.0}
        assert pct_range['p10'] <= annual_flows['clipped_pct'] <= pct_range['p90']
