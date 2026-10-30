import json
import pytest
from pathlib import Path
from unittest.mock import Mock, patch
from feasibility import server

class DummyServer:
    instance_id = "test_instance"
    server_port = 8000

@pytest.fixture
def handler_factory(tmp_path, monkeypatch):
    def make_handler(report_bytes=None):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        study_file = reports_dir / "study.json"
        if report_bytes is not None:
            study_file.write_bytes(report_bytes)
        monkeypatch.setattr(server, "HERE", tmp_path)

        handler = server.LegacyLocalHandler.__new__(server.LegacyLocalHandler)
        handler.server = DummyServer()
        handler.path = "/api/study"
        sent = []
        def fake_send(obj, status=200, kind="application/json"):
            sent.append((status, kind, obj))
            return obj
        handler.send = fake_send
        return handler, sent
    return make_handler

def test_study_not_found(handler_factory):
    handler, sent = handler_factory(report_bytes=None)
    handler.do_GET()
    assert len(sent) == 1
    status, kind, body = sent[0]
    assert status == 404
    assert "not yet computed" in body.get("error", "")

def test_study_malformed_json_returns_409(handler_factory):
    handler, sent = handler_factory(report_bytes=b"invalid json {{{")
    handler.do_GET()
    assert len(sent) == 1
    status, kind, body = sent[0]
    assert status == 409
    assert body.get("stale") is True
    assert "Malformed" in body.get("error", "")

def test_study_legacy_missing_identity_returns_409(handler_factory, monkeypatch):
    mock_ident = ["baseline_19y", "sha_npz", "sha_audit"]
    monkeypatch.setattr(server.engine.weather_dataset, "active_weather_dataset_identity", lambda: mock_ident)
    legacy_payload = json.dumps({"summary": {}, "base": []}).encode()
    handler, sent = handler_factory(report_bytes=legacy_payload)
    handler.do_GET()
    assert len(sent) == 1
    status, kind, body = sent[0]
    assert status == 409
    assert body.get("stale") is True
    assert body.get("saved_identity") is None

def test_study_stale_mismatched_identity_returns_409(handler_factory, monkeypatch):
    mock_ident = ["candidate_pvgis6", "sha_candidate_npz", "sha_candidate_audit"]
    monkeypatch.setattr(server.engine.weather_dataset, "active_weather_dataset_identity", lambda: mock_ident)
    old_payload = json.dumps({
        "summary": {},
        "weather_dataset": {"identity": ["baseline_19y", "old_sha", "old_sha"], "years": [2005, 2023]}
    }).encode()
    handler, sent = handler_factory(report_bytes=old_payload)
    handler.do_GET()
    assert len(sent) == 1
    status, kind, body = sent[0]
    assert status == 409
    assert body.get("stale") is True
    assert body.get("saved_identity") == ["baseline_19y", "old_sha", "old_sha"]

def test_study_current_matching_identity_preserves_bytes(handler_factory, monkeypatch):
    mock_ident = ["candidate_pvgis6", "sha_candidate_npz", "sha_candidate_audit"]
    monkeypatch.setattr(server.engine.weather_dataset, "active_weather_dataset_identity", lambda: mock_ident)
    raw_payload = json.dumps({
        "summary": {"kpi": 42},
        "weather_dataset": {"identity": mock_ident, "years": [2014, 2025]}
    }).encode()
    handler, sent = handler_factory(report_bytes=raw_payload)
    handler.do_GET()
    assert len(sent) == 1
    status, kind, body = sent[0]
    assert status == 200
    assert kind == "application/octet-stream"
    assert body == raw_payload


def test_study_uses_explicit_bound_identity(handler_factory):
    from feasibility.runtime_context import current_runtime
    identity = list(current_runtime().weather_identity)
    raw = json.dumps({'weather_dataset': {'identity': identity}}).encode()
    handler, sent = handler_factory(report_bytes=raw)
    handler.do_GET()
    assert sent == [(200, 'application/octet-stream', raw)]
