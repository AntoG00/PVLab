"""Synthetic compiler and fail-closed v2 loader integration tests."""
from dataclasses import replace
import hashlib
import io
import json
import zipfile
import numpy as np
import pytest
from feasibility.dataset_compile import (SolarArtifact, compile_dataset, sha256,
                                         check_npz_bounds, validate_arrays)
from feasibility.load_projection import project_load
from feasibility.profiles import parse_profile, canonical_json
from feasibility.weather_dataset import verify_and_load_dataset
from tests.test_load_projection import policy


def inputs(nr=1, years=(2023, 2024, 2028)):
    profile = dict(schema_version=1, id='1'*32, country='IT', timezone='Europe/Rome',
                   location=dict(latitude=45.0, longitude=9.0),
                   consumption=dict(artifact_id='2'*32, kind='monthly', projection_policy='synthetic_v1',
                                    acknowledged=True, coverage=dict(start=None, end=None, complete=False)),
                   roofs=[dict(id=f'plane{i}', label=f'Plane {i}', tilt_deg=30, azimuth_deg=180,
                               max_kwp=5, shading_fraction=0.1) for i in range(max(1, nr))],
                   thermal=dict(end_uses=[]),
                   market=dict(zone='NORD', series='NORD', window_start='2023-01-01',
                               window_end='2024-01-01', valuation_mode='flat', price_revision=None),
                   fiscal=dict(country='IT', rule_revision=None, gas_region=None, parameters={}),
                   default_settings=dict(baseline_cooling_kwh=0))
    load = project_load(None, [100]*12, years, policy())
    shape = (nr, len(years), 8784)
    power = np.zeros(shape)
    temperature = np.zeros((len(years), 8784))
    for yi, year in enumerate(years):
        n = 8784 if year % 4 == 0 else 8760
        power[:, yi, :n] = 1
        temperature[yi, :n] = 15
    solar = SolarArtifact(years, tuple(f'plane{i}' for i in range(nr)), 45.0, 9.0,
                          power, power*0.1, power*0, temperature, 'b'*64, 10)
    return profile, solar, load


@pytest.mark.parametrize('nr', [0, 1, 7])
def test_roundtrip_dynamic_planes_and_readonly(tmp_path, nr):
    compiled = compile_dataset(*inputs(nr))
    dest = compiled.write_candidate(tmp_path / 'candidate')
    data = verify_and_load_dataset(dest / 'hourly.npz', dest / 'audit.json',
                                   dest / 'manifest.json', compiled.digest)
    assert data['power'].shape == (nr, 3, 8784)
    assert data['audit']['annual_kwh'] == 1200
    assert data['audit']['baseline_cooling_kwh'] == 0
    assert data['audit']['projection']['mode'] == 'synthetic'
    for name, a in compiled.arrays.items():
        np.testing.assert_array_equal(a, data[name])
        assert data[name].dtype == a.dtype
        with pytest.raises(ValueError):
            data[name].setflags(write=True)
    with pytest.raises(ValueError, match='exists'):
        compiled.write_candidate(dest)
    assert not (tmp_path / 'weather-active.json').exists()


@pytest.mark.parametrize('bad', ['padding', 'load_padding', 'loss', 'nan', 'object', 'shape', 'dtype', 'negative'])
def test_compiler_rejects_invalid_arrays(bad):
    p, s, load = inputs()
    if bad == 'padding':
        s.power[0, 0, -1] = 1
    elif bad == 'load_padding':
        a = load.load_civil.copy(); a[0, -1] = 1
        load = replace(load, load_civil=a)
    elif bad == 'loss':
        s.beam_loss[0, 0, 0] = 2
    elif bad == 'nan':
        s.temperature[0, 0] = np.nan
    elif bad == 'object':
        s = replace(s, power=s.power.astype(object))
    elif bad == 'shape':
        s = replace(s, power=s.power[:, :, :-1])
    elif bad == 'dtype':
        s = replace(s, temperature=s.temperature.astype('float32'))
    else:
        s.power[0, 0, 0] = -1
    with pytest.raises(ValueError):
        compile_dataset(p, s, load)


@pytest.mark.parametrize('component', ['hourly.npz', 'audit.json', 'manifest.json'])
def test_hash_mismatch(tmp_path, component):
    compiled = compile_dataset(*inputs())
    dest = compiled.write_candidate(tmp_path / 'candidate')
    path = dest / component
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError, match='SHA256'):
        verify_and_load_dataset(dest/'hourly.npz', dest/'audit.json', dest/'manifest.json', compiled.digest)


def test_v2_cannot_use_unpinned_legacy_path(tmp_path):
    c = compile_dataset(*inputs())
    d = c.write_candidate(tmp_path/'candidate')
    with pytest.raises(ValueError, match='manifest'):
        verify_and_load_dataset(d/'hourly.npz', d/'audit.json')
    with pytest.raises(ValueError, match='pinned'):
        verify_and_load_dataset(d/'hourly.npz', d/'audit.json', d/'manifest.json')


def test_atomic_failure_leaves_no_candidate_or_pointer(tmp_path, monkeypatch):
    import feasibility.weather_dataset as wd
    c = compile_dataset(*inputs())
    def fail(*args, **kwargs):
        raise ValueError('injected integrity failure')
    monkeypatch.setattr(wd, 'verify_and_load_dataset', fail)
    pointer = tmp_path/'weather-active.json'
    pointer.write_text('unchanged')
    with pytest.raises(ValueError, match='injected'):
        c.write_candidate(tmp_path/'candidate')
    assert not (tmp_path/'candidate').exists()
    assert pointer.read_text() == 'unchanged'
    assert list(tmp_path.glob('.compile-*')) == []


def test_acknowledgement_and_explicit_cooling_gate():
    p, s, load = inputs()
    with pytest.raises(ValueError, match='acknowledgement'):
        compile_dataset(p, s, replace(load, acknowledged=False))
    p['consumption']['acknowledged'] = False
    with pytest.raises(ValueError, match='acknowledgement'):
        compile_dataset(p, s, load)
    p['consumption']['acknowledged'] = True
    p['default_settings'].clear()
    with pytest.raises(ValueError, match='cooling'):
        compile_dataset(p, s, load)
    for bad in (-1, True, float('nan')):
        p['default_settings']['baseline_cooling_kwh'] = bad
        with pytest.raises(ValueError):
            parse_profile(p)


@pytest.mark.parametrize('field,value', [('latitude', 44), ('plane_ids', ('unknown',)),
    ('years', (2024,)), ('provider_minute_offset', 60), ('source_digest', 'bad')])
def test_alignment_and_metadata(field, value):
    p, s, load = inputs()
    with pytest.raises(ValueError):
        compile_dataset(p, replace(s, **{field:value}), load)


def test_npz_entries_and_decompression_bounded(monkeypatch):
    import feasibility.dataset_compile as dc
    c = compile_dataset(*inputs())
    monkeypatch.setattr(dc, 'MAX_DECOMPRESSED_BYTES', 10)
    with pytest.raises(ValueError, match='decompressed'):
        check_npz_bounds(c.archive_bytes)
    monkeypatch.setattr(dc, 'MAX_ARCHIVE_BYTES', 10)
    with pytest.raises(ValueError, match='archive size'):
        check_npz_bounds(c.archive_bytes)


@pytest.mark.parametrize('kind', ['extra', 'object', 'calendar', 'load', 'schema', 'version'])
def test_rehashed_invalid_candidate_rejected(tmp_path, kind):
    c = compile_dataset(*inputs())
    d = c.write_candidate(tmp_path/'candidate')
    manifest = json.loads(c.manifest_bytes)
    arrays = {k:v.copy() for k,v in c.arrays.items()}
    if kind == 'extra': arrays['unexpected'] = np.array([1])
    if kind == 'object': arrays['power'] = arrays['power'].astype(object)
    if kind == 'calendar': arrays['month'][0, 0] = 12
    if kind == 'load': arrays['load_civil'][0, 0] += 1
    if kind == 'schema': manifest['arrays']['power']['dtype'] = 'float32'
    if kind == 'version': manifest['calendar_version'] = 'unknown'
    raw = io.BytesIO(); np.savez_compressed(raw, **arrays)
    (d/'hourly.npz').write_bytes(raw.getvalue())
    manifest['sha256_npz'] = sha256(raw.getvalue())
    m = canonical_json(manifest); (d/'manifest.json').write_bytes(m)
    with pytest.raises(ValueError):
        verify_and_load_dataset(d/'hourly.npz', d/'audit.json', d/'manifest.json', sha256(m))


def test_claimed_npy_allocation_rejected_before_loading():
    c = compile_dataset(*inputs(0))
    raw = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(c.archive_bytes)) as original, zipfile.ZipFile(raw, 'w') as out:
        for entry in original.infolist():
            blob = original.read(entry)
            if entry.filename == 'temperature.npy':
                header = io.BytesIO()
                np.lib.format.write_array_header_1_0(header, dict(descr='<f8', fortran_order=False,
                                                                shape=(1, 10**12)))
                blob = header.getvalue()
            out.writestr(entry.filename, blob)
    with pytest.raises(ValueError, match='allocation'):
        check_npz_bounds(raw.getvalue())


@pytest.mark.parametrize('field', ['valid', 'month', 'load_civil'])
def test_zero_roofs_still_validate_calendar_and_load(field):
    c = compile_dataset(*inputs(0))
    data = {k:v.copy() for k,v in c.arrays.items()}
    data[field][0, 0] = False if field == 'valid' else 99 if field == 'month' else np.nan
    with pytest.raises(ValueError):
        validate_arrays(data, (2023, 2024, 2028), ())


def test_candidate_rename_failure_cleans_reservation(tmp_path, monkeypatch):
    import feasibility.dataset_compile as dc
    c = compile_dataset(*inputs())
    def fail(*args):
        raise OSError('injected rename failure')
    monkeypatch.setattr(dc.os, 'rename', fail)
    with pytest.raises(OSError, match='injected'):
        c.write_candidate(tmp_path/'candidate')
    assert not (tmp_path/'candidate').exists()
    assert list(tmp_path.iterdir()) == []


def test_legacy_nonzero_padding_is_reported_not_repaired(tmp_path):
    from tests.test_weather_activation import _make_dummy_dataset
    archive, audit = tmp_path/'hourly.npz', tmp_path/'audit.json'
    _make_dummy_dataset(archive, audit, [2023])
    with np.load(archive, allow_pickle=False) as original:
        data = {k: original[k] for k in original.files}
    data['temperature'][0, -1] = 2
    np.savez_compressed(archive, **data)
    before = archive.read_bytes()
    with pytest.raises(ValueError, match='1 nonzero trailing values'):
        verify_and_load_dataset(archive, audit)
    assert archive.read_bytes() == before


def test_projection_digest_and_policy_cannot_be_relabelled():
    p, s, load = inputs()
    with pytest.raises(ValueError, match='digest'):
        compile_dataset(p, s, replace(load, monthly_totals=(101,)*12))
    p['consumption']['projection_policy'] = 'none'
    with pytest.raises(ValueError, match='policy'):
        compile_dataset(p, s, load)


@pytest.mark.parametrize('years', [(2024, 2023), (2024, 2024), (), (True,), tuple(range(1990, 2021))])
def test_year_boundaries(years):
    p, s, load = inputs()
    with pytest.raises(ValueError):
        compile_dataset(p, replace(s, years=years), load)


def test_metadata_size_limit_before_read(tmp_path, monkeypatch):
    import feasibility.dataset_compile as dc
    c = compile_dataset(*inputs())
    d = c.write_candidate(tmp_path/'candidate')
    monkeypatch.setattr(dc, 'MAX_METADATA_BYTES', 20)
    with pytest.raises(ValueError, match='metadata size'):
        verify_and_load_dataset(d/'hourly.npz', d/'audit.json', d/'manifest.json', c.digest)


def test_compilation_is_deterministic_and_copies_solar_inputs():
    p, s, load = inputs()
    a = compile_dataset(p, s, load)
    b = compile_dataset(p, s, load)
    assert a.manifest_bytes == b.manifest_bytes
    s.power[0, 0, 0] = 50
    assert a.arrays['power'][0, 0, 0] == 1
