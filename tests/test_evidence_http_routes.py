"""The upload route accepts bounded raw files without changing calculator state."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from feasibility import evidence_store, server


class Request:
    def __init__(self, path, raw=b'', headers=None):
        self.path = path
        self.rfile = io.BytesIO(raw)
        self.headers = headers or {}
        self.response = None
        self.status = None

    def send(self, response, status=200):
        self.response, self.status = response, status


class EvidenceHTTPRoutes(unittest.TestCase):
    def test_large_raw_upload_catalog_and_reversible_selection(self):
        raw = json.dumps({'solarPotential': {'roofSegmentStats': []}, 'extra': 'x' * 2_100_000}).encode()
        request = Request('/api/evidence/datasets', raw, {
            'Content-Length': str(len(raw)), 'Content-Type': 'application/octet-stream',
            'X-Dataset-Filename': 'solar.json', 'X-Dataset-Source': 'google',
        })
        with tempfile.TemporaryDirectory() as folder, patch.object(evidence_store, 'UPLOAD_ROOT', Path(folder)), \
                patch('urllib.request.urlopen', side_effect=AssertionError('No remote API calls')):
            server.LegacyLocalHandler.do_POST(request)
            self.assertEqual(request.status, 200, request.response)
            record = request.response['dataset']
            self.assertEqual((Path(folder) / record['id'] / 'source.json').read_bytes(), raw)
            catalog = Request('/api/evidence/datasets')
            server.LegacyLocalHandler.do_GET(catalog)
            self.assertEqual(catalog.response['datasets'][0]['id'], record['id'])
            for included in (False, True):
                body = json.dumps({'id': record['id'], 'included': included}).encode()
                selection = Request('/api/evidence/datasets/selection', body, {'Content-Length': str(len(body))})
                server.LegacyLocalHandler.do_POST(selection)
                self.assertEqual(selection.status, 200, selection.response)
                self.assertIs(selection.response['active_calculator_data_modified'], False)
            self.assertEqual((Path(folder) / record['id'] / 'source.json').read_bytes(), raw)

    def test_oversized_route_does_not_read_request_body(self):
        request = Request('/api/evidence/datasets', headers={
            'Content-Length': str(evidence_store.MAX_FILE_BYTES + 1),
            'Content-Type': 'application/octet-stream',
        })
        request.rfile = Mock()
        server.LegacyLocalHandler.do_POST(request)
        self.assertEqual(request.status, 400)
        request.rfile.read.assert_not_called()
