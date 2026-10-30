"""Standalone helper tests with fake Google responses and synthetic values only."""
from datetime import datetime, timezone, timedelta
import json
import os
import stat
import pytest
from tools.google_solar_helper import build_suggestions, main


def response(params, key):
    assert key == 'test-only'
    assert params['requiredQuality'] == 'HIGH'
    return dict(solarPotential=dict(roofSegmentStats=[dict(pitchDegrees=30,azimuthDegrees=180,
        stats=dict(areaMeters2=50))],solarPanels=[dict(segmentIndex=0),dict(segmentIndex=0)]))


def test_suggestions_attribution_expiry_permissions_no_key(tmp_path,monkeypatch):
    monkeypatch.setenv('GOOGLE_API_KEY','test-only')
    now=datetime(2026,1,1,tzinfo=timezone.utc)
    f=build_suggestions(45,9,tmp_path/'helper',transport=response,now=now)
    raw=f.read_text(); obj=json.loads(raw)
    assert 'test-only' not in raw
    assert obj['roof_sections'][0]['candidate_panel_count']==2
    assert obj['attribution']=='Google Maps'
    assert datetime.fromisoformat(obj['delete_by'])==now+timedelta(days=30)
    assert stat.S_IMODE(f.stat().st_mode)==0o600
    assert stat.S_IMODE(f.parent.stat().st_mode)==0o700
    assert list(f.parent.iterdir())==[f]


def test_missing_key_and_no_cli_key_args(tmp_path,monkeypatch):
    monkeypatch.delenv('GOOGLE_API_KEY',raising=False)
    with pytest.raises(ValueError,match='environment'): build_suggestions(45,9,tmp_path/'new',transport=response)
    with pytest.raises(SystemExit): main(['--key','test-only'])
    assert not (tmp_path/'new').exists()


def test_provider_errors_redacted(tmp_path,monkeypatch,capsys):
    monkeypatch.setenv('GOOGLE_API_KEY','test-only')
    def fail(*a): raise RuntimeError('test-only')
    with pytest.raises(ValueError,match='details withheld') as e:
        build_suggestions(45,9,tmp_path/'new',transport=fail)
    assert 'test-only' not in str(e.value)
    assert capsys.readouterr().out == ''
    with pytest.raises(SystemExit):
        main(['--latitude','45','--longitude','9','--output',str(tmp_path/'new'),'--key','test-only'])
    assert 'test-only' not in capsys.readouterr().err


def test_reject_repo_output_and_invalid_coordinates(tmp_path,monkeypatch):
    monkeypatch.setenv('GOOGLE_API_KEY','test-only')
    with pytest.raises(ValueError): build_suggestions(float('nan'),9,tmp_path/'new',transport=response)
    with pytest.raises(ValueError,match='external'): build_suggestions(45,9,'tools/not-a-cache',transport=response)
    assert not (tmp_path/'new').exists()
