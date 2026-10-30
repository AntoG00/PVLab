"""Unit tests for evidence upload store, HTTP receiver, inclusion, and priority coverage."""
from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from feasibility import evidence_formats
from feasibility import evidence_priority, evidence_store


class TestEvidenceUploadStore(unittest.TestCase):
    def test_exclusive_save_without_filesystem_hard_links(self):
        import errno
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'artifact.json'
            with patch.object(evidence_store.os, 'link', side_effect=OSError(errno.EPERM, 'No hard links')):
                evidence_store._atomic_new(target, b'original')
                self.assertEqual(target.read_bytes(), b'original')
                with self.assertRaises(FileExistsError):
                    evidence_store._atomic_new(target, b'replacement')
            self.assertEqual(target.read_bytes(), b'original')

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.sentinel = self.root / "active.npz"
        self.sentinel.write_bytes(b"SENTINEL_ACTIVE_CALCULATOR_DATA")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_valid_pvgis_csv_upload_and_inventory(self) -> None:
        raw_csv = (
            "# PVGIS 5.3 solar radiation database\n"
            "# Location: 40.0 N, 14.0 E\n"
            "time,P\n"
            "20240101:0010,0.0\n"
            "20240101:0110,120.0\n"
        ).encode("utf-8")
        res = evidence_store.save_upload("roof_east.csv", raw_csv, "pvgis53", "RoofEast", root=self.root)
        self.assertFalse(res["duplicate"])
        rec = res["dataset"]
        digest = hashlib.sha256(raw_csv).hexdigest()
        self.assertEqual(rec["sha256"], digest)
        self.assertEqual(rec["source"], "pvgis53")
        self.assertEqual(rec["filename"], "roof_east.csv")
        self.assertEqual(rec["stored_filename"], "source.csv")
        self.assertEqual(rec["series_label"], "RoofEast")
        self.assertFalse(rec["active_calculator_data_modified"])

        dest_dir = self.root / rec["id"]
        self.assertTrue((dest_dir / "source.csv").is_file())
        self.assertEqual((dest_dir / "source.csv").read_bytes(), raw_csv)
        self.assertTrue((dest_dir / "metadata.json").is_file())

        inv = evidence_store.inventory(root=self.root)
        self.assertEqual(len(inv["datasets"]), 1)
        self.assertEqual(inv["datasets"][0]["id"], rec["id"])
        self.assertTrue(inv["datasets"][0]["included"])
        self.assertEqual(inv["max_file_bytes"], evidence_store.MAX_FILE_BYTES)
        self.assertFalse(inv["active_calculator_data_modified"])
        self.assertEqual(len(inv["coverage"]), 1)
        self.assertEqual(inv["coverage"][0]["total_hours"], 2)

        # Persistence across subsequent inventory invocation
        inv2 = evidence_store.inventory(root=self.root)
        self.assertEqual(inv2["datasets"][0]["id"], rec["id"])
        self.assertEqual(self.sentinel.read_bytes(), b"SENTINEL_ACTIVE_CALCULATOR_DATA")

    def test_duplicate_upload_preserves_original(self) -> None:
        raw_csv = b"# PVGIS 5.3\ntime,P\n20240101:0010,0.0\n"
        res1 = evidence_store.save_upload("sample.csv", raw_csv, "pvgis53", "Main", root=self.root)
        res2 = evidence_store.save_upload("sample.csv", raw_csv, "pvgis53", "Main", root=self.root)
        self.assertFalse(res1["duplicate"])
        self.assertTrue(res2["duplicate"])
        self.assertEqual(res2["dataset"]["uploaded_at"], res1["dataset"]["uploaded_at"])
        self.assertEqual(res2["dataset"]["id"], res1["dataset"]["id"])

    def test_invalid_filenames_and_sources_rejected_before_artifact_stored(self) -> None:
        valid_bytes = b"# PVGIS 5.3\ntime,P\n20240101:0010,0.0\n"
        invalid_names = ["../traversal.csv", "sub/nested.csv", "win\\path.csv", "ctrl\x00.csv", "ctrl\n.csv"]
        for bad_name in invalid_names:
            with self.assertRaises(ValueError):
                evidence_store.save_upload(bad_name, valid_bytes, "pvgis53", "Main", root=self.root)

        for bad_src in ["nan", "", "unknown", "pvgis", "pvgis5"]:
            with self.assertRaises(ValueError):
                evidence_store.save_upload("file.csv", valid_bytes, bad_src, "Main", root=self.root)

        # Verify no dataset directories stored on rejection
        subdirs = [p for p in self.root.iterdir() if p.is_dir()]
        self.assertEqual(len(subdirs), 0)

    def test_empty_and_oversized_uploads_rejected(self) -> None:
        with self.assertRaises(ValueError):
            evidence_store.save_upload("empty.csv", b"", "pvgis53", "Main", root=self.root)

        with patch.object(evidence_store, "MAX_FILE_BYTES", 10):
            with self.assertRaises(ValueError):
                evidence_store.save_upload("big.csv", b"1234567890123", "pvgis53", "Main", root=self.root)

    def test_pvgis_source_json_schema_mismatch_rejected(self) -> None:
        v5_json = json.dumps({"outputs": {"hourly": [{"time": "20240101:0010", "P": 10.0}]}}).encode("utf-8")
        with self.assertRaises(ValueError):
            evidence_store.save_upload("mismatch.json", v5_json, "pvgis6", "Roof", root=self.root)

    @patch("urllib.request.urlopen", side_effect=AssertionError("Network fetch forbidden"))
    def test_google_json_no_url_fetch_and_sentinel_preserved(self, mock_urlopen: MagicMock) -> None:
        google_doc = json.dumps({
            "solarPotential": {
                "roofSegmentStats": [{"pitchDegrees": 20, "azimuthDegrees": 180}],
                "wholeRoofStats": {},
            }
        }).encode("utf-8")
        res = evidence_store.save_upload("google.json", google_doc, "google", "", root=self.root)
        mock_urlopen.assert_not_called()
        self.assertEqual(res["dataset"]["source"], "google")
        self.assertFalse(res["dataset"]["active_calculator_data_modified"])
        self.assertEqual(self.sentinel.read_bytes(), b"SENTINEL_ACTIVE_CALCULATOR_DATA")

    def test_receive_upload_raw_http_contract_and_guards(self) -> None:
        valid_csv = b"# PVGIS 5.3\ntime,P\n20240101:0010,0.0\n"

        class FakeHandler:
            def __init__(self, headers: dict[str, str], body: bytes) -> None:
                self.headers = headers
                self.rfile = io.BytesIO(body)

        headers = {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(valid_csv)),
            "X-Dataset-Filename": "%E2%98%80_panel.csv",
            "X-Dataset-Series": "Roof%20Plane%201",
            "X-Dataset-Source": "pvgis53",
        }
        res = evidence_store.receive_upload(FakeHandler(headers, valid_csv), root=self.root)
        self.assertEqual(res["dataset"]["filename"], "☀_panel.csv")
        self.assertEqual(res["dataset"]["series_label"], "Roof Plane 1")

        # Chunked guard
        h_chunked = dict(headers, **{"Transfer-Encoding": "chunked"})
        with self.assertRaises(ValueError):
            evidence_store.receive_upload(FakeHandler(h_chunked, valid_csv), root=self.root)

        # Content-Type guard
        h_ct = dict(headers, **{"Content-Type": "text/csv"})
        with self.assertRaises(ValueError):
            evidence_store.receive_upload(FakeHandler(h_ct, valid_csv), root=self.root)

        # Body short guard
        h_short = dict(headers, **{"Content-Length": "100"})
        with self.assertRaises(ValueError):
            evidence_store.receive_upload(FakeHandler(h_short, b"short"), root=self.root)

        # Oversized guard: rfile.read must NOT be called
        mock_rfile = MagicMock()
        h_over = dict(headers, **{"Content-Length": str(evidence_store.MAX_FILE_BYTES + 1)})
        over_handler = FakeHandler(h_over, b"")
        over_handler.rfile = mock_rfile
        with self.assertRaises(ValueError):
            evidence_store.receive_upload(over_handler, root=self.root)
        mock_rfile.read.assert_not_called()

    def test_set_inclusion_and_conflict_resolution(self) -> None:
        f1 = b"# PVGIS 5.3\ntime,P\n20240101:0000,100.0\n"
        f2 = b"# PVGIS 5.3\ntime,P\n20240101:0000,200.0\n"
        r1 = evidence_store.save_upload("f1.csv", f1, "pvgis53", "PlaneA", root=self.root)
        r2 = evidence_store.save_upload("f2.csv", f2, "pvgis53", "PlaneA", root=self.root)

        inv_conflict = evidence_store.inventory(root=self.root)
        self.assertEqual(inv_conflict["coverage"][0]["status"], "conflict")

        # Exclude r2: conflict bypassed, raw metadata preserved on disk
        evidence_store.set_inclusion({"id": r2["dataset"]["id"], "included": False}, root=self.root)
        self.assertTrue((self.root / r2["dataset"]["id"] / "metadata.json").is_file())

        inv_resolved = evidence_store.inventory(root=self.root)
        self.assertEqual(len(inv_resolved["datasets"]), 2)
        r2_rec = next(d for d in inv_resolved["datasets"] if d["id"] == r2["dataset"]["id"])
        self.assertFalse(r2_rec["included"])
        self.assertEqual(inv_resolved["coverage"][0]["status"], "ready")

        # Re-include restores conflict
        evidence_store.set_inclusion({"id": r2["dataset"]["id"], "included": True}, root=self.root)
        inv_restored = evidence_store.inventory(root=self.root)
        self.assertEqual(inv_restored["coverage"][0]["status"], "conflict")

        # Validation rejections
        with self.assertRaises(ValueError):
            evidence_store.set_inclusion({"id": "bad_hex_id", "included": True}, root=self.root)
        with self.assertRaises(ValueError):
            evidence_store.set_inclusion({"id": r1["dataset"]["id"], "included": "yes"}, root=self.root)


class TestEvidencePriorityCoverage(unittest.TestCase):
    def test_priority_v6_wins_and_v53_fills(self) -> None:
        cfg = {"latitude": 40.0, "longitude": 14.0, "tilt_deg": 30.0, "azimuth_deg": 0.0, "power_kwp": 3.0, "loss_percent": 14.0}
        rec_v6 = {"id": "v6_dataset", "source": "pvgis6", "series_label": "RoofSouth", "summary": {"kind": "hourly", "configuration": cfg}}
        rec_v53 = {"id": "v53_dataset", "source": "pvgis53", "series_label": "RoofSouth", "summary": {"kind": "hourly", "configuration": cfg}}

        # v6: 2024 hour 0 (0W) and hour 2 (200W)
        # v53: 2024 hour 0 (50W), hour 1 (100W), hour 2 (180W), hour 3 (300W), and 2005 hour (400W)
        # Hour 4 missing in both (unfilled gap)
        data = {
            "v6_dataset": [
                {"hour": "2024-01-01T00:00:00Z", "power_w": 0.0, "timestamp": "2024-01-01 00:00:00"},
                {"hour": "2024-01-01T02:00:00Z", "power_w": 200.0, "timestamp": "2024-01-01 02:00:00"},
            ],
            "v53_dataset": [
                {"hour": "2005-06-01T12:00:00Z", "power_w": 400.0, "timestamp": "20050601:1210"},
                {"hour": "2024-01-01T00:00:00Z", "power_w": 50.0, "timestamp": "20240101:0010"},
                {"hour": "2024-01-01T01:00:00Z", "power_w": 100.0, "timestamp": "20240101:0110"},
                {"hour": "2024-01-01T02:00:00Z", "power_w": 180.0, "timestamp": "20240101:0210"},
                {"hour": "2024-01-01T03:00:00Z", "power_w": 300.0, "timestamp": "20240101:0310"},
            ],
        }
        load_rows = lambda rec: data[rec["id"]]

        rows = evidence_priority.effective_rows([rec_v6, rec_v53], load_rows)
        self.assertEqual(len(rows), 5)
        # Provenance: v6 wins hour 0 & 2 (including zero power at hour 0); v53 fills 2005, hour 1, hour 3
        by_h = {r["hour"]: r for r in rows}
        self.assertEqual(by_h["2024-01-01T00:00:00Z"]["source"], "pvgis6")
        self.assertEqual(by_h["2024-01-01T00:00:00Z"]["power_w"], 0.0)
        self.assertEqual(by_h["2024-01-01T00:00:00Z"]["dataset_id"], "v6_dataset")
        self.assertEqual(by_h["2024-01-01T00:00:00Z"]["timestamp"], "2024-01-01 00:00:00")

        self.assertEqual(by_h["2024-01-01T01:00:00Z"]["source"], "pvgis53")
        self.assertEqual(by_h["2024-01-01T01:00:00Z"]["power_w"], 100.0)
        self.assertEqual(by_h["2024-01-01T02:00:00Z"]["source"], "pvgis6")
        self.assertEqual(by_h["2024-01-01T02:00:00Z"]["power_w"], 200.0)
        self.assertEqual(by_h["2024-01-01T03:00:00Z"]["source"], "pvgis53")
        self.assertEqual(by_h["2024-01-01T03:00:00Z"]["power_w"], 300.0)
        self.assertEqual(by_h["2005-06-01T12:00:00Z"]["source"], "pvgis53")
        self.assertEqual(by_h["2005-06-01T12:00:00Z"]["power_w"], 400.0)
        self.assertNotIn("2024-01-01T04:00:00Z", by_h)

        cov = evidence_priority.build_coverage([rec_v6, rec_v53], load_rows)
        self.assertEqual(len(cov), 1)
        c0 = cov[0]
        self.assertEqual(c0["status"], "ready")
        self.assertEqual(c0["v6_hours"], 2)
        self.assertEqual(c0["v53_hours"], 3)
        self.assertEqual(c0["total_hours"], 5)
        self.assertEqual(c0["overlap_hours"], 2)
        self.assertAlmostEqual(c0["energy_kwh"], 1.0)  # (0 + 100 + 200 + 300 + 400) / 1000

        # Input order independence
        rows_rev = evidence_priority.effective_rows([rec_v53, rec_v6], load_rows)
        self.assertEqual(rows, rows_rev)
        cov_rev = evidence_priority.build_coverage([rec_v53, rec_v6], load_rows)
        self.assertEqual(cov, cov_rev)

    def test_same_version_different_power_raises_conflict(self) -> None:
        rec1 = {"id": "v6_a", "source": "pvgis6", "series_label": "Roof", "summary": {"kind": "hourly"}}
        rec2 = {"id": "v6_b", "source": "pvgis6", "series_label": "Roof", "summary": {"kind": "hourly"}}
        data = {
            "v6_a": [{"hour": "2024-01-01T00:00:00Z", "power_w": 100.0, "timestamp": "t1"}],
            "v6_b": [{"hour": "2024-01-01T00:00:00Z", "power_w": 200.0, "timestamp": "t2"}],
        }
        with self.assertRaises(ValueError):
            evidence_priority.effective_rows([rec1, rec2], lambda r: data[r["id"]])

        cov = evidence_priority.build_coverage([rec1, rec2], lambda r: data[r["id"]])
        self.assertEqual(cov[0]["status"], "conflict")
        self.assertIn("Conflict: duplicate overlapping hour", cov[0]["error"])

    def test_mismatched_configuration_causes_conflict(self) -> None:
        c1 = {"latitude": 40.0, "longitude": 14.0, "tilt_deg": 30.0, "power_kwp": 3.0, "loss_percent": 14.0}
        c2 = {"latitude": 45.0, "longitude": 14.0, "tilt_deg": 30.0, "power_kwp": 3.0, "loss_percent": 14.0}
        rec1 = {"id": "r1", "source": "pvgis6", "series_label": "Roof", "summary": {"kind": "hourly", "configuration": c1}}
        rec2 = {"id": "r2", "source": "pvgis53", "series_label": "Roof", "summary": {"kind": "hourly", "configuration": c2}}
        loader = lambda r: [{"hour": "2024-01-01T00:00:00Z", "power_w": 10.0, "timestamp": "t"}]

        with self.assertRaises(ValueError):
            evidence_priority.effective_rows([rec1, rec2], loader)
        cov = evidence_priority.build_coverage([rec1, rec2], loader)
        self.assertEqual(cov[0]["status"], "conflict")
        self.assertIn("latitude", cov[0]["error"])

    def test_distinct_labels_never_merged(self) -> None:
        rec_s = {"id": "rs", "source": "pvgis6", "series_label": "Roof_South", "summary": {"kind": "hourly"}}
        rec_n = {"id": "rn", "source": "pvgis6", "series_label": "Roof_North", "summary": {"kind": "hourly"}}
        loader = lambda r: [{"hour": "2024-01-01T00:00:00Z", "power_w": 10.0, "timestamp": "t"}]

        with self.assertRaises(ValueError):
            evidence_priority.effective_rows([rec_s, rec_n], loader)
        cov = evidence_priority.build_coverage([rec_s, rec_n], loader)
        self.assertEqual(len(cov), 2)
        labels = {c["series_label"] for c in cov}
        self.assertEqual(labels, {"Roof_South", "Roof_North"})

    def test_missing_config_warning_and_monthly_summary_ignored(self) -> None:
        rec_hourly = {"id": "rh", "source": "pvgis6", "series_label": "Roof", "summary": {"kind": "hourly", "configuration": {}}}
        rec_monthly = {"id": "rm", "source": "pvgis53", "series_label": "Roof", "summary": {"kind": "monthly_summary", "configuration": {}}}
        loader = lambda r: [{"hour": "2024-01-01T00:00:00Z", "power_w": 50.0, "timestamp": "t"}] if r["id"] == "rh" else []

        cov = evidence_priority.build_coverage([rec_hourly, rec_monthly], loader)
        self.assertEqual(len(cov), 1)
        self.assertEqual(cov[0]["status"], "ready")
        self.assertTrue(any("complete configuration metadata" in w or "Matching relies on label" in w for w in cov[0]["warnings"]))

        # Monthly alone ignored
        self.assertEqual(evidence_priority.effective_rows([rec_monthly], loader), [])
        self.assertEqual(evidence_priority.build_coverage([rec_monthly], loader), [])

    @patch("urllib.request.urlopen", side_effect=AssertionError("Network fetch forbidden"))
    def test_integrated_storage_null_gap_and_legacy_zero_win(self, mock_urlopen: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as td:
            v6_doc = json.dumps({
                "timestamps": [
                    "2024-01-01 00:00:00",
                    "2024-01-01 01:00:00",
                    "2024-01-01 02:00:00",
                    "2024-01-01 03:00:00",
                ],
                "power": [0, None, 200, -0.002],
                "latitude": 40.0,
                "longitude": 14.0,
                "surface_tilt": 30.0,
                "surface_orientation": 180.0,
            }).encode("utf-8")
            v53_doc = (
                "# PVGIS 5.3\n"
                "# Latitude (decimal degrees):\t40.0\n"
                "# Longitude (decimal degrees):\t14.0\n"
                "# Slope:\t30 deg.\n"
                "# Azimuth:\t0 deg.\n"
                "time,P\n"
                "20240101:0000,100.0\n"
                "20240101:0100,150.0\n"
                "20240101:0200,100.0\n"
                "20240101:0300,50.0\n"
            ).encode("utf-8")

            r1 = evidence_store.save_upload("v6.json", v6_doc, "pvgis6", "Roof_A", root=td)
            r2 = evidence_store.save_upload("v53.csv", v53_doc, "pvgis53", "Roof_A", root=td)

            # Raw uploaded bytes must be preserved exactly on disk (containing negative and null)
            stored_raw = (Path(td) / r1["dataset"]["id"] / "source.json").read_bytes()
            self.assertEqual(stored_raw, v6_doc)
            self.assertIn(b"-0.002", stored_raw)
            self.assertIn(b"null", stored_raw)

            inv = evidence_store.inventory(root=td)
            mock_urlopen.assert_not_called()

            cov = inv["coverage"]
            self.assertEqual(len(cov), 1)
            c = cov[0]
            self.assertEqual(c["status"], "ready")
            self.assertEqual(c["total_hours"], 4)
            self.assertEqual(c["v6_hours"], 2)
            self.assertEqual(c["v53_hours"], 2)
            self.assertEqual(c["overlap_hours"], 2)
            self.assertAlmostEqual(c["energy_kwh"], 0.40)


if __name__ == "__main__":
    unittest.main()

