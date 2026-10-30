"""Pure synthetic validator tests for Phase 7 roof panel configuration."""
import pytest
from feasibility.profiles import HouseholdProfileV1, Roof, parse_profile
from feasibility.roof_config import validate_roof_counts


def _make_profile(roof_specs=None):
    if roof_specs is None:
        roof_specs = [
            dict(id='plane0', label='Plane 0', tilt_deg=30.0, azimuth_deg=180.0, max_kwp=5.0, shading_fraction=0.1),
            dict(id='plane1', label='Plane 1', tilt_deg=25.0, azimuth_deg=90.0, max_kwp=3.0, shading_fraction=0.0),
        ]
    raw = dict(
        schema_version=1,
        id='1' * 32,
        country='IT',
        timezone='Europe/Rome',
        location=dict(latitude=45.0, longitude=9.0),
        consumption=dict(
            artifact_id='2' * 32,
            kind='monthly',
            projection_policy='synthetic_v1',
            acknowledged=True,
            coverage=dict(start=None, end=None, complete=False),
        ),
        roofs=roof_specs,
        thermal=dict(end_uses=[]),
        market=dict(
            zone='NORD',
            series='NORD',
            window_start='2023-01-01',
            window_end='2024-01-01',
            valuation_mode='flat',
            price_revision=None,
        ),
        fiscal=dict(country='IT', rule_revision=None, gas_region=None, parameters={}),
        default_settings=dict(baseline_cooling_kwh=0),
    )
    return parse_profile(raw)


def test_manifest_order_preserved():
    profile = _make_profile()
    roof_panels = {'plane1': 4, 'plane0': 8}
    result = validate_roof_counts(profile, roof_panels, panel_watts=400)
    assert result == (('plane0', 8), ('plane1', 4))


def test_missing_ids_count_zero():
    profile = _make_profile()
    roof_panels = {'plane0': 6}
    result = validate_roof_counts(profile, roof_panels, panel_watts=400)
    assert result == (('plane0', 6), ('plane1', 0))
    assert validate_roof_counts(profile, {}, panel_watts=400) == (('plane0', 0), ('plane1', 0))


def test_empty_roofs_valid():
    empty_profile = dict(roofs=())
    assert validate_roof_counts(empty_profile, {}, panel_watts=400) == ()


def test_empty_roofs_rejects_nonempty_panels():
    empty_profile = dict(roofs=())
    with pytest.raises(ValueError, match='Unknown roof ID'):
        validate_roof_counts(empty_profile, {'plane0': 1}, panel_watts=400)


@pytest.mark.parametrize('bad_panels', [None, [], [('plane0', 1)], [('plane0', 1), ('plane0', 2)], ('plane0', 1)])
def test_reject_none_and_sequences_and_duplicate_pairs(bad_panels):
    profile = _make_profile()
    with pytest.raises(ValueError, match='roof_panels must be a Mapping JSON object'):
        validate_roof_counts(profile, bad_panels, panel_watts=400)


def test_reject_nonstring_keys_in_roof_panels():
    profile = _make_profile()
    with pytest.raises(ValueError, match='roof_panels keys must be strings'):
        validate_roof_counts(profile, {1: 5}, panel_watts=400)


def test_reject_unknown_roof_id():
    profile = _make_profile()
    with pytest.raises(ValueError, match='Unknown roof ID'):
        validate_roof_counts(profile, {'unknown_roof': 2}, panel_watts=400)


@pytest.mark.parametrize('bad_count', [True, False, 2.5, '5', None, [1]])
def test_reject_bool_and_noninteger_counts(bad_count):
    profile = _make_profile()
    with pytest.raises(ValueError):
        validate_roof_counts(profile, {'plane0': bad_count}, panel_watts=400)


def test_reject_negative_count():
    profile = _make_profile()
    with pytest.raises(ValueError):
        validate_roof_counts(profile, {'plane0': -1}, panel_watts=400)


@pytest.mark.parametrize('huge_count', [10**1000, 10**50])
def test_reject_huge_int_count(huge_count):
    profile = _make_profile()
    with pytest.raises(ValueError):
        validate_roof_counts(profile, {'plane0': huge_count}, panel_watts=400)


@pytest.mark.parametrize('bad_watts', [0, -100, float('inf'), float('-inf'), float('nan'), True, False, '400'])
def test_reject_invalid_panel_watts(bad_watts):
    profile = _make_profile()
    with pytest.raises(ValueError):
        validate_roof_counts(profile, {'plane0': 2}, panel_watts=bad_watts)


def test_capacity_zero_roof():
    profile_zero = dict(roofs=[Roof(id='rz', label='RZ', tilt_deg=30.0, azimuth_deg=180.0, max_kwp=0.0, shading_fraction=0.0)])
    assert validate_roof_counts(profile_zero, {}, panel_watts=400) == (('rz', 0),)
    assert validate_roof_counts(profile_zero, {'rz': 0}, panel_watts=400) == (('rz', 0),)
    with pytest.raises(ValueError, match='exceeds max_kwp'):
        validate_roof_counts(profile_zero, {'rz': 1}, panel_watts=400)


def test_overcapacity_and_just_overcapacity_rejection():
    profile = _make_profile()
    with pytest.raises(ValueError, match='exceeds max_kwp'):
        validate_roof_counts(profile, {'plane0': 13}, panel_watts=400)
    with pytest.raises(ValueError, match='exceeds max_kwp'):
        validate_roof_counts(profile, {'plane1': 8}, panel_watts=400)

    with pytest.raises(ValueError, match='exceeds max_kwp'):
        validate_roof_counts(profile, {'plane0': 10}, panel_watts=500.01)


def test_exact_capacity_allowed():
    profile = _make_profile()
    result = validate_roof_counts(profile, {'plane0': 10}, panel_watts=500)
    assert result == (('plane0', 10), ('plane1', 0))


def test_frozen_dataclass_support():
    roof0 = Roof(id='r0', label='R0', tilt_deg=30.0, azimuth_deg=180.0, max_kwp=4.0, shading_fraction=0.0)
    roof1 = Roof(id='r1', label='R1', tilt_deg=20.0, azimuth_deg=90.0, max_kwp=2.0, shading_fraction=0.0)
    profile = _make_profile([
        dict(id='r0', label='R0', tilt_deg=30.0, azimuth_deg=180.0, max_kwp=4.0, shading_fraction=0.0),
        dict(id='r1', label='R1', tilt_deg=20.0, azimuth_deg=90.0, max_kwp=2.0, shading_fraction=0.0),
    ])
    assert isinstance(profile, HouseholdProfileV1)
    assert isinstance(profile.roofs[0], Roof)
    result = validate_roof_counts(profile, {'r1': 5, 'r0': 8}, panel_watts=400)
    assert result == (('r0', 8), ('r1', 5))


def test_max_roofs_boundary():
    roofs_12 = [Roof(id=f'r{i}', label=f'R{i}', tilt_deg=30.0, azimuth_deg=180.0, max_kwp=1.0, shading_fraction=0.0) for i in range(12)]
    assert len(validate_roof_counts(dict(roofs=roofs_12), {}, panel_watts=400)) == 12

    roofs_13 = [Roof(id=f'r{i}', label=f'R{i}', tilt_deg=30.0, azimuth_deg=180.0, max_kwp=1.0, shading_fraction=0.0) for i in range(13)]
    with pytest.raises(ValueError, match='max 12 roofs allowed'):
        validate_roof_counts(dict(roofs=roofs_13), {}, panel_watts=400)


def test_duplicate_roof_ids_in_profile_rejected():
    duplicate_roofs = [
        Roof(id='r0', label='R0', tilt_deg=30.0, azimuth_deg=180.0, max_kwp=1.0, shading_fraction=0.0),
        Roof(id='r0', label='R0 dup', tilt_deg=20.0, azimuth_deg=90.0, max_kwp=2.0, shading_fraction=0.0),
    ]
    with pytest.raises(ValueError, match='Duplicate roof ID'):
        validate_roof_counts(dict(roofs=duplicate_roofs), {}, panel_watts=400)


def test_invalid_profile_rejected():
    with pytest.raises(ValueError, match='profile must provide a roofs attribute'):
        validate_roof_counts(None, {}, panel_watts=400)
    with pytest.raises(ValueError, match='profile must provide a roofs attribute'):
        validate_roof_counts({'not_roofs': []}, {}, panel_watts=400)
