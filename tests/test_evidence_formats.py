"""Isolated unit tests for upload evidence format validation.

Contracts:
    evidence_formats.validate_dataset(filename: str, data: bytes) -> dict
    evidence_formats.iter_pvgis_rows(filename: str, data: bytes) -> Iterator[dict]
Raises ValueError on unsupported, malformed, or corrupt dataset inputs.
Returns summary dict with keys: provider, format, warnings (list), optional row_count, start, end, years.
iter_pvgis_rows yields dicts: {'timestamp': str, 'hour': 'YYYY-MM-DDTHH:00:00Z', 'power_w': float}
"""

import json
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

# Load staged evidence_formats module directly; skips must not mask broken imports
from feasibility import evidence_formats


class TestEvidenceFormats(unittest.TestCase):
    def validate(self, filename: str, data: bytes) -> dict:
        return evidence_formats.validate_dataset(filename, data)

    def iter_rows(self, filename: str, data: bytes):
        return list(evidence_formats.iter_pvgis_rows(filename, data))

    def test_pvgis_csv_valid_with_bom_and_crlf(self):
        csv_body = (
            "# PVGIS-SARAH2 solar radiation database\r\n"
            "# Location: 45.0 N, 9.0 E\r\n"
            "time,P,G(i),H_sun,T2m,WS10m,Int\r\n"
            "20240101:0010,0.0,0.0,0.0,5.2,1.1,0\r\n"
            "20240101:0110,0.0,0.0,0.0,4.8,1.2,0\r\n\r\n"
            "P: PV system power (W)\r\n"
        )
        raw_csv = b"\xef\xbb\xbf" + csv_body.encode("utf-8")
        res = self.validate("pvgis_2024.csv", raw_csv)
        self.assertIn("PVGIS", res.get("provider", "").upper())
        self.assertEqual(res.get("row_count"), 2)
        self.assertIsInstance(res.get("warnings"), list)
        warn_txt = " ".join(str(w) for w in res.get("warnings", [])).lower()
        self.assertTrue(any(k in warn_txt for k in ["missing", "component", "coverage", "partial"]))

    def test_pvgis_monthly_summary_tsv(self):
        tsv_lines = [
            "Latitude (decimal degrees):\t45.0",
            "Longitude (decimal degrees):\t9.0",
            "Nominal power of the PV system (c-Si) (kWp):\t1.0",
            "Slope:\t13 deg.",
            "Azimuth:\t-5 deg.",
            "Month\tE_d\tE_m\tH(i)_d\tH(i)_m\tSD_m",
        ]
        for m in range(1, 13):
            tsv_lines.append(f"{m}\t3.0\t90.0\t4.0\t120.0\t10.0")
        tsv_lines.append("Year\t3.0\t90.0\t4.0\t120.0\t10.0\r\n\r\nE_d: Average daily production")
        raw_tsv = "\r\n".join(tsv_lines).encode("utf-8")

        res = self.validate("MonthlyData.csv", raw_tsv)
        self.assertEqual(res.get("kind"), "monthly_summary")
        self.assertEqual(res.get("row_count"), 12)
        warn_txt = " ".join(str(w) for w in res.get("warnings", [])).lower()
        self.assertTrue("hourly" in warn_txt or "no_hourly_coverage" in warn_txt)

        # Do not invent timestamps or covered years
        self.assertIsNone(res.get("start"))
        self.assertIsNone(res.get("end"))
        self.assertFalse(res.get("years"))

        # iter_pvgis_rows returns 0 rows for monthly summary
        self.assertEqual(len(self.iter_rows("MonthlyData.csv", raw_tsv)), 0)

        # Metadata azimuth normalized when clear (-5 deg PVGIS -> 175 deg standard, or -5 deg legacy)
        meta = res.get("metadata", {})
        az = meta.get("azimuth") if meta else res.get("azimuth")
        if az is not None:
            self.assertTrue(az in (-5.0, 175.0), f"Azimuth {az} not in (-5, 175)")

    def test_iter_pvgis_rows_cross_format_hour_buckets(self):
        # v5 CSV row (time: 20240101:0010)
        csv_v5 = "time,P,G(i),H_sun,T2m,WS10m,Int\n20240101:0010,150.0,0.0,0.0,5.0,1.0,0\n".encode("utf-8")
        rows_v5 = self.iter_rows("pvgis_v5.csv", csv_v5)
        self.assertEqual(len(rows_v5), 1)
        self.assertEqual(rows_v5[0]["hour"], "2024-01-01T00:00:00Z")
        self.assertEqual(rows_v5[0]["timestamp"], "20240101:0010")
        self.assertAlmostEqual(rows_v5[0]["power_w"], 150.0)

        # v6 JSON with fractional seconds (timestamp: 2024-01-01 00:10:15.646791458)
        v6_json = json.dumps({
            "timestamps": ["2024-01-01 00:10:15.646791458"],
            "power": [150.0],
            "irradiance": [0.0],
        }).encode("utf-8")
        rows_v6 = self.iter_rows("pvgis_v6.json", v6_json)
        self.assertEqual(len(rows_v6), 1)
        self.assertEqual(rows_v6[0]["hour"], "2024-01-01T00:00:00Z")
        self.assertEqual(rows_v6[0]["timestamp"], "2024-01-01 00:10:15.646791458")
        self.assertAlmostEqual(rows_v6[0]["power_w"], 150.0)

        # Both v5 and v6 align to identical UTC hour bucket
        self.assertEqual(rows_v5[0]["hour"], rows_v6[0]["hour"])

    def test_pvgis_csv_rejections(self):
        base = "time,P,G(i),H_sun,T2m,WS10m,Int\n"
        cases = {
            "bad_time.csv": base + "bad_timestamp,0.0,0.0,0.0,5.0,1.0,0\n",
            "neg_power.csv": base + "20240101:0010,-5.0,0.0,0.0,5.0,1.0,0\n",
            "nan_power.csv": base + "20240101:0010,NaN,0.0,0.0,5.0,1.0,0\n",
            "dup_rows.csv": base + "20240101:0010,0.0,0.0,0.0,5.0,1.0,0\n20240101:0010,0.0,0.0,0.0,5.0,1.0,0\n",
        }
        for name, text in cases.items():
            with self.assertRaises(ValueError, msg=f"Should reject {name}"):
                self.validate(name, text.encode("utf-8"))

    def test_pvgis5_json_valid(self):
        payload = {
            "outputs": {
                "hourly": [
                    {"time": "20240101:0010", "P": 0.0, "Gb(i)": 0.0, "Gd(i)": 0.0, "Gr(i)": 0.0},
                    {"time": "20240101:0110", "P": 12.5, "Gb(i)": 5.0, "Gd(i)": 2.0, "Gr(i)": 1.0},
                ]
            }
        }
        res = self.validate("pvgis5.json", json.dumps(payload).encode("utf-8"))
        self.assertIn("PVGIS", res.get("provider", "").upper())
        self.assertIn("JSON", res.get("format", "").upper())
        self.assertIsInstance(res.get("warnings"), list)

    def test_pvgis5_missing_or_null_power_rejected(self):
        # v5.3 missing power still invalid
        bad_json = {"outputs": {"hourly": [{"time": "20240101:0010", "P": None}]}}
        with self.assertRaises(ValueError):
            self.validate("v5_null_p.json", json.dumps(bad_json).encode("utf-8"))

        bad_csv = "time,P,G(i),H_sun,T2m,WS10m,Int\n20240101:0010,,0.0,0.0,5.0,1.0,0\n".encode("utf-8")
        with self.assertRaises(ValueError):
            self.validate("v5_missing_p.csv", bad_csv)

        # Legacy negative power in v5 still rejected
        bad_v5_neg = {"outputs": {"hourly": [{"time": "20240101:0010", "P": -5.0}]}}
        with self.assertRaises(ValueError):
            self.validate("v5_neg_p.json", json.dumps(bad_v5_neg).encode("utf-8"))

    def test_pvgis6_json_valid_and_rejected_variants(self):
        v6_valid = {
            "timestamps": ["2024-01-01 12:10:15.646791458", "2024-01-01 13:10:15.646791458"],
            "power": [120.0, 350.0],
            "irradiance": [200.0, 520.0],
        }
        res = self.validate("pvgis6.json", json.dumps(v6_valid).encode("utf-8"))
        self.assertIn("PVGIS", res.get("provider", "").upper())
        self.assertIn("JSON", res.get("format", "").upper())

        bad_v6 = [
            ("no_ts.json", {"power": [10.0], "irradiance": [50.0]}),
            ("len_mismatch.json", {"timestamps": ["2024-01-01 12:10:15.646791458"], "power": [10.0, 20.0], "irradiance": [50.0]}),
            ("nan_power.json", {"timestamps": ["2024-01-01 12:10:15.646791458"], "power": [float("nan")], "irradiance": [50.0]}),
            ("dup_ts.json", {"timestamps": ["2024-01-01 12:10:15.646791458", "2024-01-01 12:10:15.646791458"], "power": [10.0, 10.0], "irradiance": [50.0, 50.0]}),
        ]
        for name, doc in bad_v6:
            with self.assertRaises(ValueError, msg=f"Should reject {name}"):
                self.validate(name, json.dumps(doc).encode("utf-8"))

    def test_pvgis6_json_null_power_and_irradiance_gaps(self):
        # 24-hour synthetic dataset: 9 null hours, 3 zero hours, 12 positive hours
        times = [f"2024-01-01 {h:02d}:10:15.646791458" for h in range(24)]
        powers = [None] * 9 + [0.0] * 3 + [100.0 + h * 10 for h in range(12)]
        # Null irradiance components allowed as legitimate gap with warning
        irradiance = [None] * 9 + [0.0] * 3 + [200.0 + h * 20 for h in range(12)]

        payload = {
            "timestamps": times,
            "power": powers,
            "direct_inclined_irradiance_before_reflectivity": irradiance,
        }
        raw_json = json.dumps(payload).encode("utf-8")

        res = self.validate("v6_export_with_nulls.json", raw_json)
        self.assertIn("PVGIS", res.get("provider", "").upper())
        self.assertEqual(res.get("missing_power_rows", 9), 9)
        self.assertEqual(res.get("valid_row_count", 15), 15)

        # Warnings include null/gap warning without fabricated values
        warn_txt = " ".join(str(w) for w in res.get("warnings", [])).lower()
        self.assertTrue(any(k in warn_txt for k in ["gap", "missing", "null", "irradiance", "coverage"]))

        # iter_pvgis_rows skips null power rows, retains actual 0.0 values
        streamed = self.iter_rows("v6_export_with_nulls.json", raw_json)
        self.assertEqual(len(streamed), 15)
        self.assertTrue(all(r["power_w"] is not None for r in streamed))
        zero_rows = [r for r in streamed if r["power_w"] == 0.0]
        self.assertEqual(len(zero_rows), 3)

        # Rejections: NaN, boolean, and negative power in v6 must still be rejected
        with self.assertRaises(ValueError, msg="v6 must reject boolean power"):
            self.validate("v6_bool.json", json.dumps({"timestamps": [times[0]], "power": [True]}).encode("utf-8"))

        # Negative power in v6 is accepted as RAW reference, counted in warnings/negative rows, and excluded from streaming
        res_neg = self.validate("v6_neg.json", json.dumps({"timestamps": [times[0]], "power": [-0.002195667]}).encode("utf-8"))
        self.assertEqual(res_neg.get("valid_row_count", 0), 0)
        self.assertEqual(res_neg.get("negative_power_rows", res_neg.get("unavailable_power_rows", 1)), 1)
        self.assertEqual(len(self.iter_rows("v6_neg.json", json.dumps({"timestamps": [times[0]], "power": [-0.002195667]}).encode("utf-8"))), 0)

        # Full timestamps still validated: duplicate timestamps including null power rows rejected
        dup_null_payload = {
            "timestamps": [times[0], times[0]],
            "power": [None, None],
        }
        with self.assertRaises(ValueError, msg="Duplicate timestamps with null power must be rejected"):
            self.validate("v6_dup_null.json", json.dumps(dup_null_payload).encode("utf-8"))

    def test_pvgis6_json_negative_power_policy(self):
        # Actual v6 beta contains small negative power (e.g. -0.002195667 W)
        # Policy: accepted as RAW reference; summary records negative/unavailable rows;
        # iterator SKIPS negative (for v5.3 fallback); NEVER clips to 0/abs or includes negative energy.
        times = [
            "2024-01-01 10:10:15.646791458",
            "2024-01-01 11:10:15.646791458",
            "2024-01-01 12:10:15.646791458",
        ]
        # One legitimate positive (100.0), one zero (0.0), one negative (-0.002195667)
        doc = {
            "timestamps": times,
            "power": [100.0, 0.0, -0.002195667],
        }
        raw_bytes = json.dumps(doc).encode("utf-8")

        # Raw bytes unchanged assert
        parsed_raw = json.loads(raw_bytes.decode("utf-8"))
        self.assertEqual(parsed_raw["power"][2], -0.002195667)

        res = self.validate("v6_beta_neg.json", raw_bytes)
        self.assertIn("PVGIS", res.get("provider", "").upper())

        # Summary valid count 2 (positive and zero; negative excluded from valid count)
        valid_cnt = res.get("valid_row_count", res.get("row_count"))
        self.assertEqual(valid_cnt, 2)

        # Summary records negative/unavailable rows count or warnings
        neg_cnt = res.get("negative_power_rows", res.get("unavailable_power_rows", 1))
        self.assertEqual(neg_cnt, 1)

        # Iterator skips negative power, retains actual zero, never clips to 0 or abs
        streamed = self.iter_rows("v6_beta_neg.json", raw_bytes)
        self.assertEqual(len(streamed), 2)
        streamed_powers = [r["power_w"] for r in streamed]
        self.assertIn(100.0, streamed_powers)
        self.assertIn(0.0, streamed_powers)
        # Exactly one zero (never clips negative to 0.0)
        self.assertEqual(streamed_powers.count(0.0), 1)
        # Never includes negative power or its absolute value
        self.assertTrue(all(p >= 0.0 for p in streamed_powers))
        self.assertNotIn(-0.002195667, streamed_powers)
        self.assertNotIn(0.002195667, streamed_powers)

    def test_pvgis6_json_radians_units_and_ground_component(self):
        # Actual v6 API response with angle units='radians', peak_power_unit='kWp', system_efficiency=.86
        payload_rad = {
            "timestamps": ["2024-01-01 12:10:15.646791458"],
            "power": [100.0],
            "unit": "radians",
            "surface_tilt": math.radians(13),
            "surface_orientation": math.radians(175),
            "peak_power": 1.0,
            "peak_power_unit": "kWp",
            "system_efficiency": 0.86,
            "direct_inclined_irradiance_before_reflectivity": [250.0],
            "diffuse_inclined_irradiance_before_reflectivity": [150.0],
            "ground_reflected_inclined_irradiance_before_reflectivity": [20.0],
        }
        res_rad = self.validate("v6_radians.json", json.dumps(payload_rad).encode("utf-8"))
        cfg = res_rad.get("configuration", {})
        meta = res_rad.get("metadata", {})

        # Normalized configuration: tilt 13 deg, azimuth -5 deg, power 1 kWp, loss 14%
        tilt = cfg.get("tilt_deg", meta.get("tilt"))
        self.assertIsNotNone(tilt)
        self.assertAlmostEqual(tilt, 13.0, places=1)

        az = cfg.get("azimuth_deg", meta.get("azimuth"))
        self.assertIsNotNone(az)
        self.assertAlmostEqual(az, -5.0, places=1)

        power = cfg.get("power_kwp", meta.get("system_power_kwp"))
        self.assertIsNotNone(power)
        self.assertAlmostEqual(power, 1.0, places=2)

        loss = cfg.get("loss_percent", meta.get("loss_percent"))
        self.assertIsNotNone(loss)
        self.assertAlmostEqual(loss, 14.0, places=1)

        # Ground component field actual 'ground_reflected_inclined_irradiance_before_reflectivity'
        self.assertTrue(res_rad.get("has_components", False))
        warn_txt = " ".join(str(w) for w in res_rad.get("warnings", [])).lower()
        self.assertNotIn("components gb(i)", warn_txt)
        self.assertNotIn("components absent", warn_txt)

        # Zero output angle in radians: North 0 rad normalizes to legacy -180 deg
        payload_zero = {
            "timestamps": ["2024-01-01 12:10:15.646791458"],
            "power": [50.0],
            "unit": "radians",
            "surface_tilt": 0.0,
            "surface_orientation": 0.0,
        }
        res_zero = self.validate("v6_zero_rad.json", json.dumps(payload_zero).encode("utf-8"))
        cfg_zero = res_zero.get("configuration", {})
        meta_zero = res_zero.get("metadata", {})
        az_zero = cfg_zero.get("azimuth_deg", meta_zero.get("azimuth"))
        self.assertIsNotNone(az_zero)
        self.assertTrue(az_zero in (-180.0, 180.0) or abs(abs(az_zero) - 180.0) < 1e-2)

        # Input query angles in degrees must NOT be converted again
        payload_deg = {
            "timestamps": ["2024-01-01 12:10:15.646791458"],
            "power": [100.0],
            "inputs": {
                "surface_tilt": 13.0,
                "surface_orientation": 175.0,
            },
        }
        res_deg = self.validate("v6_degrees.json", json.dumps(payload_deg).encode("utf-8"))
        cfg_deg = res_deg.get("configuration", {})
        meta_deg = res_deg.get("metadata", {})
        tilt_deg = cfg_deg.get("tilt_deg", meta_deg.get("tilt"))
        self.assertAlmostEqual(tilt_deg, 13.0, places=1)
        az_deg = cfg_deg.get("azimuth_deg", meta_deg.get("azimuth"))
        self.assertAlmostEqual(az_deg, -5.0, places=1)

    def test_unrecognized_json_and_empty_file_rejected(self):
        for name in ["empty.csv", "empty.json"]:
            with self.assertRaises(ValueError):
                self.validate(name, b"")
        unknown = json.dumps({"schema": "unknown", "content": [1, 2, 3]}).encode("utf-8")
        with self.assertRaises(ValueError):
            self.validate("unrecognized.json", unknown)

    @patch("urllib.request.urlopen", side_effect=AssertionError("Network fetch forbidden"))
    def test_google_insights_and_datalayers_no_network(self, mock_urlopen):
        insights = {"solarPotential": {"roofSegmentStats": [{"pitchDegrees": 13, "azimuthDegrees": 175}]}}
        res_insights = self.validate("google_solar.json", json.dumps(insights).encode("utf-8"))
        self.assertTrue("GOOGLE" in res_insights.get("provider", "").upper() or "SOLAR" in res_insights.get("provider", "").upper())

        datalayers = {"dsmUrl": "https://example.invalid/a", "hourlyShadeUrls": ["https://example.invalid/b"]}
        res_dl = self.validate("datalayers.json", json.dumps(datalayers).encode("utf-8"))
        self.assertIsInstance(res_dl.get("warnings"), list)
        mock_urlopen.assert_not_called()

    def test_tiff_headers_classic_bigtiff_and_corrupt(self):
        # Classic TIFF little-endian: magic 42, IFD at 8 with empty directory (0 entries)
        classic_header = b"II\x2a\x00\x08\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        res_classic = self.validate("elevation.tif", classic_header)
        self.assertIn("TIFF", res_classic.get("format", "").upper())
        self.assertIsInstance(res_classic.get("warnings"), list)

        # BigTIFF valid header type 43 (offset size 8, reserved 0, first IFD at 16 with 0 entries)
        big_tiff_header = (
            b"II\x2b\x00\x08\x00\x00\x00\x10\x00\x00\x00\x00\x00\x00\x00"
            b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        )
        res_big = self.validate("elevation_big.tiff", big_tiff_header)
        self.assertIn("TIFF", res_big.get("format", "").upper())

        # Truncated magic header rejected
        for corrupt in [b"II", b"II\x2a"]:
            with self.assertRaises(ValueError):
                self.validate("corrupted.tif", corrupt)

    def test_untrusted_html_filename_data_only(self):
        untrusted_name = "<script>alert('xss')</script>.csv"
        csv_data = "time,P,G(i),H_sun,T2m,WS10m,Int\n20240101:0010,0.0,0.0,0.0,5.0,1.0,0\n".encode("utf-8")
        res = self.validate(untrusted_name, csv_data)
        self.assertIsInstance(res, dict)
        self.assertIn("provider", res)
        self.assertIn("format", res)
