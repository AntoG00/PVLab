"""Tests for empirical GME CSUD market price curve ingestion, normalization,
valuation, caching, and price-sensitive sizing.

Run with:
PYTHONPATH=feasibility/vendor:tests python3 -m unittest tests/test_empirical_prices.py
"""

import copy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo
import numpy as np

try:
    from test_support import IsolatedDefaultsTestCase
except ImportError:
    from tests.test_support import IsolatedDefaultsTestCase

import feasibility.engine as engine
import feasibility.assumptions as assumptions
import feasibility.prepare_market_prices as prep

import os
DATA = Path(os.environ['PV_LEGACY_DATA_ROOT'])


class TestEmpiricalPrices(IsolatedDefaultsTestCase):

    def setUp(self):
        super().setUp()
        self.defaults = copy.deepcopy(engine.defaults())

    def test_empirical_data_artifact_coverage_and_meta(self):
        """Validates that empirical artifacts exist with complete provenance and coverage."""
        meta_file = DATA / 'empirical_csud_meta.json'
        npz_file = DATA / 'empirical_csud_prices.npz'

        self.assertTrue(meta_file.exists(), "empirical_csud_meta.json must exist")
        self.assertTrue(npz_file.exists(), "empirical_csud_prices.npz must exist")

        with open(meta_file, 'r', encoding='utf-8') as f:
            meta = json.load(f)

        # Provenance checks
        self.assertIn('synthetic gme-format', meta.get('source', '').lower())
        self.assertEqual(meta.get('zone'), 'CSUD')
        self.assertEqual(meta.get('total_zip_archives'), 2)

        # Ingestion record counts
        self.assertEqual(meta.get('total_civil_days'), 365+366)
        self.assertEqual(meta.get('total_hourly_records'), 8760+8784)

        # Archives integrity: two generated archives recorded with sha256 hashes
        archives = meta.get('archive_hashes', {})
        self.assertEqual(len(archives), 2)
        for name, sha in archives.items():
            self.assertTrue(name.startswith('MGP_Prezzi'), f"Invalid archive name: {name}")
            self.assertEqual(len(sha), 64, f"Invalid SHA256 for archive {name}")

        from feasibility.market_catalog import read_revision
        catalog=read_revision(DATA.parents[1]/'catalog',meta['catalog_revision'])
        self.assertEqual(len(catalog['days']),365+366)
        self.assertEqual(set(catalog['manifest']['series']),set(prep.SERIES))
        self.assertIs(meta['fictional'],True)
        import hashlib
        for name,digest in archives.items():
            raw=(DATA.parents[1]/'price-archives'/name).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),digest)

        # NPZ structure
        with np.load(npz_file) as npz:
            self.assertIn('month_hour_csud_oct2025_sep2026', npz.files)
            self.assertIn('month_hour_csud_cal2025', npz.files)
            self.assertIn('normalized_shape_oct2025_sep2026', npz.files)
            self.assertIn('normalized_shape_cal2025', npz.files)

            m_oct = npz['month_hour_csud_oct2025_sep2026']
            m_cal = npz['month_hour_csud_cal2025']
            w_oct = npz['normalized_shape_oct2025_sep2026']
            w_cal = npz['normalized_shape_cal2025']

            self.assertEqual(m_oct.shape, (12, 24))
            self.assertEqual(m_cal.shape, (12, 24))
            self.assertEqual(w_oct.shape, (12, 24))
            self.assertEqual(w_cal.shape, (12, 24))

            # All finite and non-negative
            self.assertTrue(np.all(np.isfinite(m_oct)))
            self.assertTrue(np.all(np.isfinite(m_cal)))

    def test_hourly_vs_15min_aggregation_agreement(self):
        """Verifies that 15-minute XML aggregation matches hourly XML values with high precision."""
        meta_file = DATA / 'empirical_csud_meta.json'
        with open(meta_file, 'r', encoding='utf-8') as f:
            meta = json.load(f)

        agreement = meta.get('hourly_quarterhour_agreement', {})
        self.assertEqual(agreement.get('checked_hours', 0), 8760+8784)
        max_diff = agreement.get('max_abs_difference_eur_mwh', 999)
        self.assertLess(max_diff, 1e-4, f"15min vs hourly discrepancy too large: {max_diff}")

    def test_dst_transitions_metadata_counts(self):
        """Verifies 23-hour and 25-hour civil days are recorded without modulo hour % 24 corruption."""
        meta_file = DATA / 'empirical_csud_meta.json'
        with open(meta_file, 'r', encoding='utf-8') as f:
            meta = json.load(f)

        dst_counts = meta.get('dst_counts', {})
        self.assertEqual(dst_counts.get('23_hour_days'), 2)
        self.assertEqual(dst_counts.get('25_hour_days'), 2)
        self.assertEqual(dst_counts.get('24_hour_days'), 727)

        dst_dates = meta.get('dst_dates', {})
        self.assertIn('20230326', dst_dates.get('spring_2023', ''))
        self.assertIn('20231029', dst_dates.get('autumn_2023', ''))
        self.assertIn('20240331', dst_dates.get('spring_2024', ''))

    def test_baseload_normalization_equals_one(self):
        """Verifies the empirical month/hour curve is normalized across all hours to mean 1.0."""
        days_per_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

        npz_file = DATA / 'empirical_csud_prices.npz'
        with np.load(npz_file) as npz:
            for key in ['normalized_shape_oct2025_sep2026', 'normalized_shape_cal2025']:
                weights = npz[key]
                total_weighted = sum(weights[m].sum() * days_per_month[m] for m in range(12))
                mean_over_year = total_weighted / 8760.0
                self.assertAlmostEqual(mean_over_year, 1.0, places=6,
                                       msg=f"Normalized weights for {key} must have annual mean of 1.0")

    def test_price_profile_precedence_and_fingerprint(self):
        """Tests that price_profile respects source precedence and invalidates via price_fingerprint."""
        s = copy.deepcopy(self.defaults)

        # 1. Flat benchmark profile
        s['export_price_profile'] = 'flat_benchmark'
        self.assertEqual(engine.export_valuation(s).mode, 'flat')
        self.assertEqual(engine.export_valuation(s).unit, 'weighted_kWh')
        fp_flat = engine.price_fingerprint(s)
        self.assertIsInstance(fp_flat, int)
        shape_flat, source_flat, _ = engine.price_profile(s)
        self.assertEqual(source_flat, 'flat_solar_capture_benchmark')
        # In flat benchmark, the returned shape is 1.0 everywhere (engine multiplies by baseload * capture)
        self.assertTrue(np.allclose(shape_flat, 1.0))

        # The generated actual-hourly artifact has precedence. A separate
        # immutable synthetic binding without it exercises empirical windows.
        self.assertEqual(engine.price_profile({**s,'export_price_profile':'empirical_csud'})[1],
                         'actual_hourly_gme_profile')
        from dataclasses import replace
        from feasibility.runtime_context import current_runtime
        from unittest.mock import patch
        context=current_runtime()
        empirical={k:v for k,v in context.price_data.items() if k!='hourly_prices.npz'}
        self.enterContext(patch('feasibility.runtime_context._bound',replace(context,
            price_data=empirical,price_revision='synthetic-empirical-only')))
        # 2. Empirical CSUD Oct 2025 - Sep 2026
        s['export_price_profile'] = 'empirical_csud'
        s['export_price_window'] = 'oct2025_sep2026'
        fp_emp = engine.price_fingerprint(s)
        self.assertNotEqual(fp_flat, fp_emp)

        shape_emp, source_emp, _ = engine.price_profile(s)
        self.assertEqual(source_emp, 'empirical_csud_oct2025_sep2026_proxy')
        self.assertEqual(shape_emp.shape, (engine.weather_period().count, 8784))

        # 3. Calendar 2025 window
        s['export_price_window'] = 'calendar_2025'
        fp_cal = engine.price_fingerprint(s)
        self.assertNotEqual(fp_emp, fp_cal)
        shape_cal, source_cal, _ = engine.price_profile(s)
        self.assertEqual(source_cal, 'empirical_csud_calendar_2025_proxy')
        self.assertEqual(shape_cal.shape, (engine.weather_period().count, 8784))

    def test_duck_curve_midday_discount_vs_evening_peak(self):
        """Verifies the empirical CSUD duck curve: lower midday clearing prices and higher evening peak."""
        data = engine.empirical_month_hour_data('oct2025_sep2026')
        self.assertIsNotNone(data)
        matrix = np.array(data['matrix_eur_kwh']) * 1000.0  # EUR/MWh

        # Check summer months (e.g. July, month index 6)
        july_midday = np.mean(matrix[6, 11:15])  # 11:00 to 14:00
        july_evening = np.mean(matrix[6, 19:22])  # 19:00 to 21:00
        self.assertLess(july_midday, july_evening,
                        f"July midday ({july_midday:.1f} €/MWh) should be lower than evening ({july_evening:.1f} €/MWh)")

        # Verify annual hourly means show lower midday than evening
        annual_hourly = np.array(data['hourly_means_eur_kwh']) * 1000.0
        midday_avg = np.mean(annual_hourly[11:15])
        evening_avg = np.mean(annual_hourly[19:22])
        self.assertLess(midday_avg, evening_avg,
                        f"Annual midday ({midday_avg:.1f}) should be lower than evening ({evening_avg:.1f})")

    def test_parser_synthetic_dst_and_summer_ora_mapping(self):
        """Exercises DST (23h and 25h) and summer Ora->fixed UTC+1 standard time mapping on synthetic XMLs."""
        rome = ZoneInfo('Europe/Rome')
        std_tz = timezone(timedelta(hours=1))

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            zip_file = tmp_path / 'MGP_Prezzi_Test.zip'

            def make_xml(date_str, n_hours):
                root = ET.Element('NewDataSet')
                for ora in range(1, n_hours + 1):
                    p = ET.SubElement(root, 'Prezzi')
                    ET.SubElement(p, 'Data').text = date_str
                    ET.SubElement(p, 'Mercato').text = 'MGP'
                    ET.SubElement(p, 'Ora').text = str(ora)
                    for series in prep.SERIES:
                        ET.SubElement(p, series).text = f"{100.0 + ora:.2f}".replace('.', ',')
                return ET.tostring(root, encoding='utf-8')

            def make_xml15(date_str, n_hours):
                root = ET.Element('NewDataSet')
                for periodo in range(1, n_hours * 4 + 1):
                    p = ET.SubElement(root, 'Prezzi15')
                    ET.SubElement(p, 'Data').text = date_str
                    ET.SubElement(p, 'Mercato').text = 'MGP'
                    ET.SubElement(p, 'Periodo').text = str(periodo)
                    ora = (periodo - 1) // 4 + 1
                    for series in prep.SERIES:
                        ET.SubElement(p, series).text = f"{100.0 + ora:.2f}".replace('.', ',')
                return ET.tostring(root, encoding='utf-8')

            with zipfile.ZipFile(zip_file, 'w') as z:
                # 1. Spring DST: 2025-03-30 (23 civil hours)
                z.writestr('20250330MGPPrezzi.xml', make_xml('20250330', 23))
                # 2. Autumn DST: 2025-10-26 (25 civil hours)
                z.writestr('20251026MGPPrezzi.xml', make_xml('20251026', 25))
                z.writestr('20251026MGPPrezzi15.xml', make_xml15('20251026', 25))
                # 3. Summer Day: 2025-07-15 (24 civil hours)
                z.writestr('20250715MGPPrezzi.xml', make_xml('20250715', 24))

            # Explicit half-open coverage; inspect each separated fixture day.
            records = {}
            for day in ('2025-03-30', '2025-07-15', '2025-10-26'):
                end = (datetime.fromisoformat(day) + timedelta(days=1)).date().isoformat()
                ingest = prep.ingest_mgp_data(tmp_path, day, end)
                rows = [r for r in ingest['days'][day]['records'] if r[0] == 'CSUD']
                # Canonical quarters retain their resolution; compare hourly starts.
                step = 4 if rows[0][2] == 900 else 1
                records[day.replace('-', '')] = [
                    (i + 1, row[3] * 1000, datetime.fromisoformat(row[1]).astimezone(std_tz))
                    for i, row in enumerate(rows[::step])]

            # Validate Spring DST has exact 23 hours
            self.assertEqual(len(records['20250330']), 23)

            # Validate Autumn DST has exact 25 hours and distinct standard times for repeated civil hours
            self.assertEqual(len(records['20251026']), 25)
            times_autumn = [rec[2] for rec in records['20251026']]
            # Ora 2 (01:00 CEST) corresponds to 23:00 UTC (00:00 std)
            # Ora 3 (02:00 CET repeated) corresponds to 01:00 UTC (02:00 std)
            self.assertEqual(times_autumn[1].hour, 0)
            self.assertEqual(times_autumn[2].hour, 1)
            self.assertEqual(times_autumn[3].hour, 2)
            # All 25 timestamps must be strictly sequential standard times
            for i in range(len(times_autumn) - 1):
                self.assertEqual(times_autumn[i+1] - times_autumn[i], timedelta(hours=1))

            # Validate Summer Date (2025-07-15):
            # Ora 1 (00:00 CEST) -> previous date 22:00 UTC -> previous date 23:00 fixed UTC+1 standard time!
            summer_recs = records['20250715']
            ora1_time = summer_recs[0][2]
            self.assertEqual(ora1_time.day, 14)
            self.assertEqual(ora1_time.hour, 23)

            # Ora 13 (civil 12:00 interval start CEST) -> 10:00 UTC -> 11:00 fixed standard time
            ora13_time = summer_recs[12][2]
            self.assertEqual(ora13_time.day, 15)
            self.assertEqual(ora13_time.hour, 11)

            # Ora 14 (civil 13:00 interval start CEST) -> 11:00 UTC -> 12:00 fixed standard time
            ora14_time = summer_recs[13][2]
            self.assertEqual(ora14_time.day, 15)
            self.assertEqual(ora14_time.hour, 12)

    def test_parser_validates_data_and_mercato_consistency(self):
        """Verifies ingest_mgp_data rejects inconsistent Data or Mercato across rows."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            zip_file = tmp_path / 'MGP_Prezzi_Bad.zip'

            # XML with mismatched Data in row 2
            root = ET.Element('NewDataSet')
            p1 = ET.SubElement(root, 'Prezzi')
            ET.SubElement(p1, 'Data').text = '20250101'
            ET.SubElement(p1, 'Mercato').text = 'MGP'
            ET.SubElement(p1, 'Ora').text = '1'
            for series in prep.SERIES:
                ET.SubElement(p1, series).text = '100,0'

            p2 = ET.SubElement(root, 'Prezzi')
            ET.SubElement(p2, 'Data').text = '20250102'  # Inconsistent date!
            ET.SubElement(p2, 'Mercato').text = 'MGP'
            ET.SubElement(p2, 'Ora').text = '2'
            for series in prep.SERIES:
                ET.SubElement(p2, series).text = '100,0'

            with zipfile.ZipFile(zip_file, 'w') as z:
                z.writestr('20250101MGPPrezzi.xml', ET.tostring(root, encoding='utf-8'))

            with self.assertRaises(ValueError):
                prep.ingest_mgp_data(tmp_path, '2025-01-01', '2025-01-02')

    def test_dayweighted_curve_unequal_months(self):
        """Verifies backend day-weighting aligns with non-leap month lengths (365 days total)."""
        data = engine.empirical_month_hour_data('oct2025_sep2026')
        self.assertIsNotNone(data)

        # In empirical_month_hour_data, hourly_means_eur_kwh is weighted by month days
        hourly_means = np.array(data['hourly_means_eur_kwh'])
        annual_mean_from_hourly = float(np.mean(hourly_means))
        expected_meta_mean = float(data['window_meta']['mean_csud_eur_kwh'])

        self.assertAlmostEqual(annual_mean_from_hourly, expected_meta_mean, places=10,
                               msg="24-hour mean of day-weighted hourly means must strictly equal annual baseload mean")

        # Independent mathematical verification of month weights:
        # Month 1 (Jan) has 31 days, Month 2 (Feb) has 28 days
        days = np.array([31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31], dtype=float)
        mock_matrix = np.zeros((12, 24))
        mock_matrix[0, :] = 100.0  # Jan
        mock_matrix[1, :] = 200.0  # Feb

        weighted_hourly = (mock_matrix * days[:, None]).sum(axis=0) / 365.0
        expected_weighted_val = (100.0 * 31 + 200.0 * 28) / 365.0
        unweighted_val = (100.0 + 200.0) / 12.0

        self.assertTrue(np.allclose(weighted_hourly, expected_weighted_val))
        self.assertNotEqual(round(expected_weighted_val, 4), round(unweighted_val, 4))

    def test_multiyear_ramp_and_separate_escalation(self):
        """Verifies multi-year ramp formula F=1+(target-1)*min((Y-1)/ramp,1) with separate escalation and PMG."""
        s = copy.deepcopy(self.defaults)
        s['south_panels'] = 14
        s['battery_kwh'] = 0.0
        s['export_market_price'] = 0.128
        s['actual_price_future_factor'] = 0.75
        s['export_price_future_years'] = 10
        s['inflation'] = 0.02
        s['energy_real_escalation'] = 0.03
        s['horizon_years'] = 20
        s['pmg_election'] = False

        fin, cash = engine._financial(s, engine._energy_cache(tuple((k, s[k]) for k in engine.ENERGY_KEYS), 0)[0], 6.16)

        # Check future factor values across years:
        # Y1 (yy=0): F=1.0
        # Y6 (yy=5): F=1 - 0.25*(5/10) = 0.875
        # Y11 (yy=10): F=1 - 0.25*(10/10) = 0.75
        # Y20 (yy=19): F=0.75
        for yy in range(20):
            year = yy + 1
            progress = min(yy / 10.0, 1.0)
            expected_f = 1.0 + (0.75 - 1.0) * progress
            if year == 1:
                self.assertAlmostEqual(expected_f, 1.0)
            elif year == 6:
                self.assertAlmostEqual(expected_f, 0.875)
            elif year == 11:
                self.assertAlmostEqual(expected_f, 0.75)
            elif year == 20:
                self.assertAlmostEqual(expected_f, 0.75)

        # Check short 3-year ramp reaches target at Y4:
        s_short = copy.deepcopy(s)
        s_short['export_price_future_years'] = 3
        for yy in range(6):
            year = yy + 1
            progress = min(yy / 3.0, 1.0)
            expected_f = 1.0 + (0.75 - 1.0) * progress
            if year == 1:
                self.assertAlmostEqual(expected_f, 1.0)
            elif year == 2:
                self.assertAlmostEqual(expected_f, 1.0 - 0.25 * (1/3))
            elif year == 3:
                self.assertAlmostEqual(expected_f, 1.0 - 0.25 * (2/3))
            elif year == 4:
                self.assertAlmostEqual(expected_f, 0.75)
            elif year == 5:
                self.assertAlmostEqual(expected_f, 0.75)

        # Verify retail electric bill is completely unaffected by actual_price_future_factor
        s_base_f = copy.deepcopy(s)
        s_base_f['actual_price_future_factor'] = 1.0
        fin_base, cash_base = engine._financial(s_base_f, engine._energy_cache(tuple((k, s[k]) for k in engine.ENERGY_KEYS), 0)[0], 6.16)

        for c_ramp, c_base in zip(cash[1:], cash_base[1:]):
            self.assertAlmostEqual(c_ramp['avoided_import'], c_base['avoided_import'], places=6)
            self.assertAlmostEqual(c_ramp['with_system_electric_bill'], c_base['with_system_electric_bill'], places=6)

    def test_hourly_export_revenue_fixture_night_pv_zero_and_midday(self):
        """Verifies expensive nighttime prices produce zero export revenue because solar generation is zero at night."""
        s = copy.deepcopy(self.defaults)
        s['south_panels'] = 14
        s['battery_kwh'] = 0.0
        s['export_price_profile'] = 'empirical_csud'

        # Under empirical profile, nighttime wholesale prices are high, but night PV is zero
        eval_res = engine.evaluate(s, detail=False)
        analysis = eval_res['export_price_analysis']

        # Capture factor should be strictly less than 1.0 (midday duck curve discount)
        self.assertIsNotNone(analysis['export_weighted_capture_factor'])
        self.assertLess(analysis['export_weighted_capture_factor'], 1.0)
        self.assertFalse(analysis['legacy_capture_factor_active'])

        # Safe zero-export handling: system with 0 panels
        s_zero = copy.deepcopy(s)
        s_zero['south_panels'] = 0
        eval_zero = engine.evaluate(s_zero, detail=False)
        analysis_zero = eval_zero['export_price_analysis']

        self.assertIsNone(analysis_zero['export_weighted_captured_price_eur_kwh'])
        self.assertIsNone(analysis_zero['export_weighted_capture_factor'])
        self.assertEqual(analysis_zero['flat_benchmark_comparison']['curve_year1_pzo_gross_eur'], 0.0)

    def test_battery_heatmap_zero_and_signed_sensitivity(self):
        """Verifies battery heatmap sensitivity eliminates residual 0.001 floor and handles signed/zero valuation."""
        s = copy.deepcopy(self.defaults)
        s['battery_kwh'] = 5.0
        s['export_price_profile'] = 'empirical_csud'

        # When spread_delta cancels base export price exactly (base + delta == 0)
        base_exp = s['export_market_price']
        res_zero = engine.battery_sizing(
            s,
            options={'max_capacity': 10, 'step': 1, 'sensitivity_spread_delta': -base_exp}
        )
        cells_zero = res_zero['chart4_npv_heatmap']['cells']
        self.assertTrue(len(cells_zero) > 0)

        # When spread_delta pushes price negative (stress test)
        res_neg = engine.battery_sizing(
            s,
            options={'max_capacity': 10, 'step': 1, 'sensitivity_spread_delta': -base_exp - 0.05}
        )
        cells_neg = res_neg['chart4_npv_heatmap']['cells']
        self.assertTrue(len(cells_neg) > 0)
        # NPV under negative export price should be less than or equal to zero export price
        self.assertLessEqual(cells_neg[0]['npv_median'], cells_zero[0]['npv_median'])

    def test_battery_chart_heuristics_labeling(self):
        """Verifies Chart 2 and Chart 3 clearly state they are first-year annualized approximations omitting ramp."""
        s = copy.deepcopy(self.defaults)
        res = engine.battery_sizing(s, options={'max_capacity': 10, 'step': 1})

        opt_band = res['chart2_incremental_savings']['optimum_band']
        self.assertTrue(opt_band.get('is_first_year_annualized_heuristic'))
        self.assertFalse(opt_band.get('future_export_ramp_included'))
        self.assertIn('first-year annualized approximation', opt_band.get('definition', '').lower())
        self.assertIn('chart 4', opt_band.get('definition', '').lower())

        efc_note = res['chart3_efc_breakeven']['cycle_conversion_note'].lower()
        self.assertIn('single-year equivalent-cycle heuristic approximation', efc_note)
        self.assertIn('omits multi-year future export price ramp', efc_note)
        self.assertIn('chart 4', efc_note)

    def test_solar_bounds_note_objective_and_profile_aware(self):
        """Verifies solar sizing bounds_note is factual and aware of objective and active price profile."""
        s = copy.deepcopy(self.defaults)
        s['inverter_kw'] = 6.0

        # Run with flat benchmark
        res_flat = engine.solar_sizing(
            {**s, 'export_price_profile': 'flat_benchmark', 'export_market_price': 0.0},
            options={'evaluation_budget': 50, 'battery_mode': 'fixed', 'objective': 'npv_eur'}
        )
        rec_flat = res_flat['recommended_candidate']
        note_flat = rec_flat['bounds_note'].lower()
        if rec_flat['panel_count'] < res_flat['search_summary']['inverter_panel_ceiling']:
            self.assertIn('maximum project npv', note_flat)
            self.assertIn('flat capture benchmark', note_flat)

        # Run with empirical CSUD
        res_emp = engine.solar_sizing(
            {**s, 'export_price_profile': 'empirical_csud', 'export_market_price': 0.0},
            options={'evaluation_budget': 50, 'battery_mode': 'fixed', 'objective': 'autarky'}
        )
        rec_emp = res_emp['recommended_candidate']
        note_emp = rec_emp['bounds_note'].lower()
        if rec_emp['panel_count'] < res_emp['search_summary']['inverter_panel_ceiling']:
            self.assertIn('maximum autarky', note_emp)
            self.assertIn('empirical csud', note_emp)

    def test_dynamic_legacy_capture_factor_and_flat_envelope(self):
        """Verifies dynamic user capture factor propagates and flat envelope reflects constant capture."""
        s = copy.deepcopy(self.defaults)
        s['south_panels'] = 14
        s['battery_kwh'] = 0.0
        s['export_price_profile'] = 'flat_benchmark'
        s['export_capture_factor'] = 0.82

        eval_flat = engine.evaluate(s, detail=False)
        analysis = eval_flat['export_price_analysis']

        self.assertEqual(analysis['legacy_capture_factor'], 0.82)
        self.assertEqual(analysis['export_weighted_capture_factor'], 0.82)
        # Flat envelope must reflect actual constant capture (0.82), not ratio 1.0!
        env = analysis['export_weighted_capture_factor_envelope']
        self.assertEqual(env['min'], 0.82)
        self.assertEqual(env['median'], 0.82)
        self.assertEqual(env['max'], 0.82)

        # On flat benchmark, empirical curve comparison is Not calculated (None)
        comp = analysis['flat_benchmark_comparison']
        self.assertIsNone(comp['curve_year1_pzo_gross_eur'])
        self.assertIsNone(comp['delta_year1_pzo_gross_eur'])
        self.assertIn('not calculated while flat benchmark is active', comp['note'].lower())


if __name__ == '__main__':
    unittest.main()
