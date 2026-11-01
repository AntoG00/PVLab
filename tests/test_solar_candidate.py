"""Synthetic complete-year provider/temperature/compiler tests; no live network."""
import copy
from dataclasses import replace
from datetime import timedelta
import json
import stat
import numpy as np
import pytest
from feasibility.solar_provider import (SolarRequest, ProviderClient, Response, RateLimiter,
    PVGIS_URL, TEMPERATURE_URL, SOURCE_POLICY, TenantCache, fetch_pvgis, timestamp_ns)
from feasibility.solar_candidate import build_candidate, main
from feasibility.load_projection import utc_slots
from feasibility.dataset_compile import compile_dataset
from feasibility.weather_dataset import verify_and_load_dataset
from tests.test_dataset_compile import inputs


class Clock:
    def __init__(self): self.now = 0
    def clock(self): return self.now
    def sleep(self, t): self.now += t


class Weather:
    def __init__(self, modify=None): self.calls = []; self.modify = modify
    def __call__(self, endpoint, params, **kwargs):
        self.calls.append((endpoint, dict(params)))
        year = int(params.get('start_time', params.get('start_date'))[:4])
        slots = utc_slots(year); n = len(slots)
        if endpoint == PVGIS_URL:
            obj = dict(timestamps=[t.strftime('%Y-%m-%dT%H:%M:')+'00.123456789Z' for t in slots],
                power=[500]*n, surface_tilt=params['surface_tilt'], surface_orientation=params['surface_orientation'],
                unit='degrees', peak_power=1, peak_power_unit='kWp', technology=params['photovoltaic_module'],
                system_efficiency=params['system_efficiency'], shading='PVGIS', azimuth_origin='North', frequency='h')
            for k in ('direct_inclined_irradiance_before_reflectivity',
                      'diffuse_inclined_irradiance_before_reflectivity',
                      'ground_reflected_inclined_irradiance_before_reflectivity'):
                obj[k] = [1]*n
        else:
            obj = dict(latitude=params['latitude'], longitude=params['longitude'], utc_offset_seconds=0,
                hourly_units=dict(temperature_2m='°C'), hourly=dict(time=[t.strftime('%Y-%m-%dT%H:%M') for t in slots],
                temperature_2m=[-5]*n))
        if self.modify: self.modify(endpoint, obj, params)
        return Response(200, endpoint, json.dumps(obj, allow_nan=True).encode())


def client(weather):
    c = Clock()
    return ProviderClient(weather, clock=c.clock, sleep=c.sleep, limiter=RateLimiter(c.clock, c.sleep))


def request(nr=1, years=(2023, 2024)):
    p, _, load = inputs(nr=nr, years=years)
    if not nr: p['roofs'] = []
    return SolarRequest(p, years, 14, 'Free standing 2025', 'cSi', SOURCE_POLICY), load


def test_hand_conversion_full_leap_and_provenance_roundtrip(tmp_path):
    r, load = request(); weather = Weather()
    source = fetch_pvgis(r, client(weather)); a = source.artifact
    assert a.power.shape == (1, 2, 8784)
    assert a.power[0, 0, 0] == .5 and a.beam_loss[0, 0, 0] == .05
    assert np.all(a.diffuse_loss == 0)
    assert a.temperature[0, 0] == -5
    assert np.all(a.power[0, 0, 8760:] == 0)
    assert a.power[0, 1].sum() == 4392
    with pytest.raises(ValueError): a.power.setflags(write=True)
    provenance = json.loads(source.provenance_bytes)
    assert provenance['offset_nanoseconds_by_year'] == {'2023':123456789,'2024':123456789}
    assert provenance['timestamps_by_year']['2023'][0].endswith('.123456789Z')
    assert provenance['solar'][0]['coordinate_authority'] == 'validated_request'
    assert provenance['solar'][0]['provider_confirmed_coordinates'] == {}
    params = provenance['solar'][0]['request_parameters']
    assert params['surface_orientation'] == 180 and params['peak-power'] == 1
    assert params['system_efficiency'] == .86 and params['irradiance_source'] == 'SARAH-3'
    compiled = compile_dataset(r.profile, a, load)
    dest = compiled.write_candidate(tmp_path/'candidate')
    data = verify_and_load_dataset(dest/'hourly.npz',dest/'audit.json',dest/'manifest.json',compiled.digest)
    np.testing.assert_array_equal(data['temperature'], a.temperature)
    assert (dest/'solar-source.json').read_bytes() == source.provenance_bytes
    (dest/'solar-source.json').unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        verify_and_load_dataset(dest/'hourly.npz',dest/'audit.json',dest/'manifest.json',compiled.digest)


@pytest.mark.parametrize('bad', ['azimuth','tilt','coordinates','equipment','power_unit','efficiency','negative',
    'nonfinite','component','short','duplicate','gap','irregular','temperature_missing','temperature_gap',
    'temperature_unit','temperature_offset','temperature_grid','temperature_fraction','temperature_bool',
    'nested_coordinates'])
def test_fail_closed_on_source_contract(bad):
    def modify(endpoint, obj, params):
        if endpoint == PVGIS_URL:
            if bad == 'azimuth': obj['surface_orientation'] += 1
            if bad == 'tilt': obj['surface_tilt'] += 1
            if bad == 'coordinates': obj['latitude'] = 0
            if bad == 'nested_coordinates': obj['inputs'] = {'location': {'latitude': 0, 'longitude': 0}}
            if bad == 'equipment': obj['technology'] = 'other'
            if bad == 'power_unit': obj['peak_power_unit'] = 'W'
            if bad == 'efficiency': obj['system_efficiency'] = 1
            if bad == 'negative': obj['power'][0] = -1
            if bad == 'nonfinite': obj['power'][0] = float('nan')
            if bad == 'component': obj['direct_inclined_irradiance_before_reflectivity'][0] = -1
            if bad == 'short': obj['timestamps'].pop()
            if bad == 'duplicate': obj['timestamps'][1] = obj['timestamps'][0]
            if bad == 'gap': obj['timestamps'][10] = '2023-01-02T00:00:00Z'
            if bad == 'irregular': obj['timestamps'][1] = '2023-01-01T01:00:01Z'
        else:
            if bad == 'temperature_missing': obj['hourly']['temperature_2m'][0] = None
            if bad == 'temperature_gap': obj['hourly']['time'].pop()
            if bad == 'temperature_unit': obj['hourly_units']['temperature_2m'] = 'K'
            if bad == 'temperature_offset': obj['utc_offset_seconds'] = 3600
            if bad == 'temperature_grid': obj['longitude'] = 0
            if bad == 'temperature_fraction': obj['hourly']['time'][0] = '2023-01-01T00:00:00.000000001Z'
            if bad == 'temperature_bool': obj['hourly']['temperature_2m'][0] = False
    r, _ = request(years=(2023,))
    with pytest.raises(ValueError): fetch_pvgis(r, client(Weather(modify)))


def test_cross_plane_time_alignment_and_no_fallback():
    def modify(endpoint, obj, params):
        if endpoint == PVGIS_URL and params['surface_orientation'] == 90:
            obj['timestamps'] = [x.replace('.123456789', '.123456788') for x in obj['timestamps']]
    r, _ = request(nr=2, years=(2023,)); r.profile['roofs'][1]['azimuth_deg'] = 90
    w=Weather(modify)
    with pytest.raises(ValueError, match='cross-plane'): fetch_pvgis(r, client(w))
    assert len(w.calls) == 2 and all(x[0] == PVGIS_URL for x in w.calls)


def test_empty_roof_temperature_only_and_full_dynamic_count():
    for nr in (0, 7):
        r, load = request(nr=nr, years=(2024,)); w=Weather()
        a = fetch_pvgis(r, client(w)).artifact
        assert a.power.shape == (nr,1,8784)
        assert len(w.calls) == nr+1
        assert all(p['models'] == 'era5' for u,p in w.calls if u == TEMPERATURE_URL)
        assert compile_dataset(r.profile,a,load).arrays['power'].shape[0] == nr


def test_policy_must_be_explicit_no_implicit_fallback():
    r,_=request(); w=Weather()
    with pytest.raises(ValueError,match='policy'): fetch_pvgis(replace(r,source_policy={}),client(w))
    assert w.calls == []


def test_exact_utc_nanosecond_conversion():
    assert timestamp_ns('2023-01-01T01:10:37.563604116+01:00') == timestamp_ns('2023-01-01T00:10:37.563604116Z')
    assert timestamp_ns('2023-01-01 00:10:37.563604116') % 10**9 == 563604116
    with pytest.raises(ValueError): timestamp_ns('2023-01-01T00:00:00.1234567891Z')


def test_cache_is_tenant_request_and_equipment_scoped(tmp_path):
    r,_=request(years=(2023,)); w=Weather(); c=client(w)
    r=replace(r,cache=TenantCache(tmp_path/'cache','1'*32))
    first=fetch_pvgis(r,c); assert len(w.calls)==2
    assert fetch_pvgis(r,c).artifact.source_digest == first.artifact.source_digest and len(w.calls)==2
    fetch_pvgis(replace(r,loss_percent=12),c); assert len(w.calls)==3
    fetch_pvgis(replace(r,cache=TenantCache(tmp_path/'cache','2'*32)),c); assert len(w.calls)==5
    for p in (tmp_path/'cache').rglob('*.json'): assert stat.S_IMODE(p.stat().st_mode)==0o600


def test_existing_shared_parent_permissions_are_preserved(tmp_path):
    from feasibility.solar_provider import _private_dir
    shared=tmp_path/'shared'; shared.mkdir(mode=0o755); shared.chmod(0o755)
    _private_dir(shared,allow_shared=True)
    with pytest.raises(ValueError,match='owner-only'): TenantCache(shared,'1'*32)
    assert stat.S_IMODE(shared.stat().st_mode)==0o755


def test_cli_candidate_atomic_publication_and_failure_status(tmp_path):
    r,_=request(years=(2023,))
    spec=dict(profile=r.profile,years=[2023],solar=dict(loss_percent=14,mounting=r.mounting,
        technology=r.technology,source_policy=SOURCE_POLICY),cache_root=str(tmp_path/'cache'),tenant_id='1'*32,
        load=dict(monthly_totals=[100]*12,policy=dict(mode='synthetic',leap_policy='february_month_hour',
        reference_year=2023,acknowledged=True)))
    manifest=tmp_path/'profile.json'; manifest.write_text(json.dumps(spec))
    dest=tmp_path/'complete'; result=build_candidate(manifest,dest,transport=client(Weather()))
    assert json.loads((tmp_path/'complete.status.json').read_text())['state']=='complete'
    assert not (tmp_path/'weather-active.json').exists()
    assert json.loads((dest/'manifest.json').read_text())['solar_digest'] == json.loads((dest/'audit.json').read_text())['solar_source']['sha256']
    spec['load']['policy']['acknowledged']=False; manifest.write_text(json.dumps(spec))
    with pytest.raises(ValueError): build_candidate(manifest,tmp_path/'failed',transport=client(Weather()))
    assert not (tmp_path/'failed').exists()
    assert json.loads((tmp_path/'failed.status.json').read_text())['state']=='failed'
    assert main(['--profile-manifest',str(manifest),'--output',str(tmp_path/'failed-again')])==1


def test_source_digest_mismatch_rejected():
    r, load=request(years=(2023,)); a=fetch_pvgis(r,client(Weather())).artifact
    with pytest.raises(ValueError): compile_dataset(r.profile,replace(a,source_provenance=b'{}'),load)


def test_radians_and_provider_confirmed_request_coordinates():
    import math
    def modify(endpoint, obj, params):
        if endpoint == PVGIS_URL:
            obj['unit'] = 'radians'
            obj['surface_tilt'] = math.radians(params['surface_tilt'])
            obj['surface_orientation'] = math.radians(params['surface_orientation'])
            obj['latitude'],obj['longitude'] = params['latitude'],params['longitude']
    r,_=request(years=(2023,))
    source=fetch_pvgis(r,client(Weather(modify)))
    p=json.loads(source.provenance_bytes)['solar'][0]
    assert p['coordinate_authority']=='provider'
    assert p['provider_confirmed_coordinates']=={'latitude':45.0,'longitude':9.0}
    def nested(endpoint, obj, params):
        if endpoint == PVGIS_URL:
            obj['inputs'] = {'location': {'latitude': params['latitude'], 'longitude': params['longitude']}}
    p=json.loads(fetch_pvgis(r,client(Weather(nested))).provenance_bytes)['solar'][0]
    assert p['coordinate_authority']=='provider'
    assert p['provider_confirmed_coordinates']=={'latitude':45.0,'longitude':9.0}


def test_source_file_tampering_rejected_before_load(tmp_path):
    r,load=request(years=(2023,)); a=fetch_pvgis(r,client(Weather())).artifact
    c=compile_dataset(r.profile,a,load); d=c.write_candidate(tmp_path/'candidate')
    f=d/'solar-source.json'; f.write_bytes(f.read_bytes()+b' ')
    with pytest.raises(ValueError,match='solar source SHA256'):
        verify_and_load_dataset(d/'hourly.npz',d/'audit.json',d/'manifest.json',c.digest)
