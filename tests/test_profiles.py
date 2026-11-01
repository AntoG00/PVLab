"""Synthetic schema boundary tests for feasibility.profiles."""

import copy
import dataclasses
import pytest

from feasibility.profiles import (
    canonical_json,
    parse_profile,
    profile_to_dict,
    validate_ready,
)


@pytest.fixture
def valid_draft_payload():
    return {
        "schema_version": 1,
        "id": "1" * 32,
        "country": "IT",
        "timezone": "Europe/Rome",
        "location": {"latitude": 45.0, "longitude": 9.0},
        "consumption": {
            "artifact_id": None,
            "kind": "interval",
            "coverage": {"start": None, "end": None, "complete": False},
            "projection_policy": "none",
            "acknowledged": False,
        },
        "roofs": [
            {
                "id": "main",
                "label": "Main",
                "tilt_deg": 25.0,
                "azimuth_deg": 180.0,
                "max_kwp": 5.0,
                "shading_fraction": 0.1,
            }
        ],
        "thermal": {"end_uses": []},
        "market": {
            "zone": "NORD",
            "series": "NORD",
            "window_start": "2025-01-01",
            "window_end": "2026-01-01",
            "valuation_mode": "shape_scaled",
            "price_revision": None,
        },
        "fiscal": {
            "country": "IT",
            "rule_revision": None,
            "gas_region": None,
            "parameters": {},
        },
        "default_settings": {},
    }


@pytest.fixture
def valid_thermal_end_use():
    return {
        "id": "space_heat",
        "carrier": "gas",
        "measured_input": 1200.0,
        "unit": "smc",
        "monthly_useful_kwh": None,
        "efficiency": 0.9,
        "cop": None,
        "annual_fixed_cost": 50.0,
        "annual_variable_cost": 1000.0,
        "included_in_electric_load": False,
        "action": "replace",
        "artifact_id": None,
    }


def test_parse_valid_draft_profile(valid_draft_payload):
    profile = parse_profile(valid_draft_payload)
    assert profile.schema_version == 1
    assert profile.id == "1" * 32
    assert profile.country == "IT"
    assert profile.timezone == "Europe/Rome"
    assert profile.location.latitude == 45.0
    assert profile.location.longitude == 9.0
    assert len(profile.roofs) == 1
    assert profile.roofs[0].id == "main"
    assert profile.default_settings == {}


def test_profile_to_dict_roundtrip(valid_draft_payload):
    profile = parse_profile(valid_draft_payload)
    exported = profile_to_dict(profile)
    assert exported == valid_draft_payload
    profile_reparsed = parse_profile(exported)
    assert profile == profile_reparsed

    cjson1 = canonical_json(valid_draft_payload)
    cjson2 = canonical_json(exported)
    assert cjson1 == cjson2
    assert isinstance(cjson1, (str, bytes))


def test_input_mutation_isolation(valid_draft_payload):
    payload = copy.deepcopy(valid_draft_payload)
    profile = parse_profile(payload)

    payload["schema_version"] = 999
    payload["country"] = "US"
    payload["location"]["latitude"] = 0.0
    payload["roofs"][0]["id"] = "mutated"
    payload["roofs"].append(copy.deepcopy(valid_draft_payload["roofs"][0]))
    payload["market"]["zone"] = "SUD"

    assert profile.schema_version == 1
    assert profile.country == "IT"
    assert profile.location.latitude == 45.0
    assert profile.roofs[0].id == "main"
    assert len(profile.roofs) == 1
    assert profile.market.zone == "NORD"


def test_immutable_nested_structures(valid_draft_payload):
    profile = parse_profile(valid_draft_payload)

    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError, TypeError)):
        profile.country = "FR"

    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError, TypeError)):
        profile.location.latitude = 50.0

    assert isinstance(profile.roofs, tuple)
    with pytest.raises(AttributeError):
        profile.roofs.append(valid_draft_payload["roofs"][0])

    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError, TypeError)):
        profile.roofs[0].tilt_deg = 30.0

    assert isinstance(profile.thermal.end_uses, tuple)


@pytest.mark.parametrize(
    "path,bad_key",
    [
        ([], "unexpected_root"),
        (["location"], "unknown_loc"),
        (["consumption"], "unknown_consumption"),
        (["consumption", "coverage"], "extra_coverage"),
        (["roofs", 0], "extra_roof_field"),
        (["market"], "unknown_market_key"),
        (["fiscal"], "unknown_fiscal_key"),
        (["fiscal", "parameters"], "unexpected_tax_param"),
        (["default_settings"], "personal_override"),
        (["thermal"], "unknown_thermal_key"),
    ],
)
def test_parse_profile_rejects_unknown_fields_recursive(
    valid_draft_payload, path, bad_key
):
    payload = copy.deepcopy(valid_draft_payload)
    target = payload
    for step in path:
        target = target[step]
    target[bad_key] = "invalid"

    with pytest.raises(ValueError, match="(?i)unknown|unexpected"):
        parse_profile(payload)


@pytest.mark.parametrize("bad_version", [0, 2, "1", None, -1])
def test_parse_profile_rejects_unsupported_version(valid_draft_payload, bad_version):
    payload = copy.deepcopy(valid_draft_payload)
    if bad_version is None:
        payload.pop("schema_version", None)
    else:
        payload["schema_version"] = bad_version

    with pytest.raises(ValueError, match="(?i)version"):
        parse_profile(payload)


@pytest.mark.parametrize(
    "mutate_fn",
    [
        lambda p: p.update(schema_version=True),
        lambda p: p.update(schema_version=False),
        lambda p: p["location"].update(latitude=True),
        lambda p: p["location"].update(longitude=False),
        lambda p: p["roofs"][0].update(tilt_deg=True),
        lambda p: p["roofs"][0].update(azimuth_deg=False),
        lambda p: p["roofs"][0].update(max_kwp=True),
        lambda p: p["roofs"][0].update(shading_fraction=False),
        lambda p: p["market"].update(price_revision=True),
    ],
)
def test_parse_profile_rejects_bool_numbers_recursively(
    valid_draft_payload, mutate_fn
):
    payload = copy.deepcopy(valid_draft_payload)
    mutate_fn(payload)

    with pytest.raises(ValueError, match="(?i)bool|type|numeric"):
        parse_profile(payload)


@pytest.mark.parametrize("bad_float", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize(
    "mutate_fn",
    [
        lambda p, val: p["location"].update(latitude=val),
        lambda p, val: p["location"].update(longitude=val),
        lambda p, val: p["roofs"][0].update(tilt_deg=val),
        lambda p, val: p["roofs"][0].update(azimuth_deg=val),
        lambda p, val: p["roofs"][0].update(max_kwp=val),
        lambda p, val: p["roofs"][0].update(shading_fraction=val),
    ],
)
def test_parse_profile_rejects_nan_and_inf_recursively(
    valid_draft_payload, bad_float, mutate_fn
):
    payload = copy.deepcopy(valid_draft_payload)
    mutate_fn(payload, bad_float)

    with pytest.raises(ValueError, match="(?i)finite|nan|inf"):
        parse_profile(payload)


@pytest.mark.parametrize("bad_country", ["FR", "DE", "US", "", "it", None, 123])
def test_parse_profile_rejects_unsupported_country(valid_draft_payload, bad_country):
    payload = copy.deepcopy(valid_draft_payload)
    payload["country"] = bad_country

    with pytest.raises(ValueError, match="(?i)country|unsupported"):
        parse_profile(payload)


@pytest.mark.parametrize(
    "bad_timezone",
    ["UTC", "Europe/London", "America/New_York", "Europe/rome", "", None],
)
def test_parse_profile_rejects_unsupported_timezone(valid_draft_payload, bad_timezone):
    payload = copy.deepcopy(valid_draft_payload)
    payload["timezone"] = bad_timezone

    with pytest.raises(ValueError, match="(?i)timezone|unsupported"):
        parse_profile(payload)


@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        "   ",
        "1" * 31,
        "1" * 33,
        "not-a-valid-hex-uuid-string-len32",
        "../traversal_id_attempt_32_chars",
        "1" * 31 + "G",
        "1" * 31 + "A",
        None,
    ],
)
def test_parse_profile_rejects_malformed_profile_id(valid_draft_payload, bad_id):
    payload = copy.deepcopy(valid_draft_payload)
    payload["id"] = bad_id

    with pytest.raises(ValueError, match="(?i)id|uuid|hex"):
        parse_profile(payload)


@pytest.mark.parametrize(
    "bad_roof_id",
    [
        "",
        "   ",
        "../escape",
        "path/traversal",
        "roof id with spaces",
        "roof$special!char",
        None,
    ],
)
def test_parse_profile_rejects_malformed_roof_id(valid_draft_payload, bad_roof_id):
    payload = copy.deepcopy(valid_draft_payload)
    payload["roofs"][0]["id"] = bad_roof_id

    with pytest.raises(ValueError, match="(?i)roof.*id|identifier"):
        parse_profile(payload)


def test_parse_profile_rejects_duplicate_roof_ids(valid_draft_payload):
    payload = copy.deepcopy(valid_draft_payload)
    r1 = copy.deepcopy(payload["roofs"][0])
    r2 = copy.deepcopy(payload["roofs"][0])
    r1["id"] = "plane_a"
    r2["id"] = "plane_a"
    payload["roofs"] = [r1, r2]

    with pytest.raises(ValueError, match="(?i)duplicate|roof"):
        parse_profile(payload)


def test_parse_profile_accepts_zero_roofs(valid_draft_payload):
    payload = copy.deepcopy(valid_draft_payload)
    payload["roofs"] = []

    assert parse_profile(payload).roofs == ()


def test_parse_profile_roof_limit_12_planes(valid_draft_payload):
    payload = copy.deepcopy(valid_draft_payload)
    template = payload["roofs"][0]

    payload["roofs"] = [
        {**template, "id": f"roof_{i:02d}", "label": f"Roof {i}"}
        for i in range(12)
    ]
    profile = parse_profile(payload)
    assert len(profile.roofs) == 12

    payload["roofs"].append(
        {**template, "id": "roof_13", "label": "Roof 13"}
    )
    with pytest.raises(ValueError, match="(?i)max.*12|roof.*limit"):
        parse_profile(payload)


@pytest.mark.parametrize("bad_tilt", [-1.0, -0.01, 90.1, 180.0])
def test_parse_profile_rejects_out_of_bounds_tilt(valid_draft_payload, bad_tilt):
    payload = copy.deepcopy(valid_draft_payload)
    payload["roofs"][0]["tilt_deg"] = bad_tilt

    with pytest.raises(ValueError, match="(?i)tilt"):
        parse_profile(payload)


@pytest.mark.parametrize("bad_azimuth", [-0.1, -180.0, 360.0, 360.1, 400.0])
def test_parse_profile_rejects_out_of_bounds_azimuth(valid_draft_payload, bad_azimuth):
    payload = copy.deepcopy(valid_draft_payload)
    payload["roofs"][0]["azimuth_deg"] = bad_azimuth

    with pytest.raises(ValueError, match="(?i)azimuth"):
        parse_profile(payload)


@pytest.mark.parametrize("bad_kwp", [0.0, -1.0, -0.001])
def test_parse_profile_rejects_nonpositive_max_kwp(valid_draft_payload, bad_kwp):
    payload = copy.deepcopy(valid_draft_payload)
    payload["roofs"][0]["max_kwp"] = bad_kwp

    with pytest.raises(ValueError, match="(?i)max_kwp|capacity"):
        parse_profile(payload)


@pytest.mark.parametrize("bad_shading", [-0.01, 1.01, 2.0])
def test_parse_profile_rejects_out_of_bounds_shading(valid_draft_payload, bad_shading):
    payload = copy.deepcopy(valid_draft_payload)
    payload["roofs"][0]["shading_fraction"] = bad_shading

    with pytest.raises(ValueError, match="(?i)shading"):
        parse_profile(payload)


@pytest.mark.parametrize("kind", ["interval", "monthly", "annual"])
def test_parse_profile_valid_consumption_kinds(valid_draft_payload, kind):
    payload = copy.deepcopy(valid_draft_payload)
    payload["consumption"]["kind"] = kind
    profile = parse_profile(payload)
    assert profile.consumption.kind == kind


@pytest.mark.parametrize("bad_kind", ["hourly", "bimonthly", "synthetic", "", None])
def test_parse_profile_rejects_invalid_consumption_kind(valid_draft_payload, bad_kind):
    payload = copy.deepcopy(valid_draft_payload)
    payload["consumption"]["kind"] = bad_kind

    with pytest.raises(ValueError, match="(?i)consumption.*kind"):
        parse_profile(payload)


@pytest.mark.parametrize(
    "zone", ["NORD", "CNOR", "CSUD", "SUD", "CALA", "SICI", "SARD"]
)
def test_parse_profile_valid_market_zones(valid_draft_payload, zone):
    payload = copy.deepcopy(valid_draft_payload)
    payload["market"]["zone"] = zone
    payload["market"]["series"] = zone
    profile = parse_profile(payload)
    assert profile.market.zone == zone


@pytest.mark.parametrize("bad_zone", ["NORTH", "FRANCE", "ROMA", "", None])
def test_parse_profile_rejects_invalid_market_zone(valid_draft_payload, bad_zone):
    payload = copy.deepcopy(valid_draft_payload)
    payload["market"]["zone"] = bad_zone

    with pytest.raises(ValueError, match="(?i)zone"):
        parse_profile(payload)


@pytest.mark.parametrize("mode", ["shape_scaled", "absolute_proxy", "flat"])
def test_parse_profile_valid_valuation_modes(valid_draft_payload, mode):
    payload = copy.deepcopy(valid_draft_payload)
    payload["market"]["valuation_mode"] = mode
    profile = parse_profile(payload)
    assert profile.market.valuation_mode == mode


@pytest.mark.parametrize(
    "forbidden_mode", ["indexed", "pun_indexed", "dynamic_retail", "hourly_spot"]
)
def test_parse_profile_rejects_forbidden_indexed_retail(
    valid_draft_payload, forbidden_mode
):
    payload = copy.deepcopy(valid_draft_payload)
    payload["market"]["valuation_mode"] = forbidden_mode

    with pytest.raises(ValueError, match="(?i)indexed|valuation_mode|unsupported"):
        parse_profile(payload)


@pytest.mark.parametrize(
    "start,end",
    [
        ("2025-01-01", "2024-01-01"),
        ("2025-01-01", "2025-01-01"),
        ("2025/01/01", "2026/01/01"),
        ("invalid-date", "2026-01-01"),
    ],
)
def test_parse_profile_rejects_invalid_market_window(valid_draft_payload, start, end):
    payload = copy.deepcopy(valid_draft_payload)
    payload["market"]["window_start"] = start
    payload["market"]["window_end"] = end

    with pytest.raises(ValueError, match="(?i)window|date"):
        parse_profile(payload)


def test_parse_profile_valid_thermal_end_use(
    valid_draft_payload, valid_thermal_end_use
):
    payload = copy.deepcopy(valid_draft_payload)
    payload["thermal"]["end_uses"] = [valid_thermal_end_use]
    profile = parse_profile(payload)
    assert len(profile.thermal.end_uses) == 1
    eu = profile.thermal.end_uses[0]
    assert eu.id == "space_heat"
    assert eu.carrier == "gas"
    assert eu.action == "replace"


@pytest.mark.parametrize("bad_id", ["cooling", "ev_charging", "solar", "", None])
def test_parse_profile_rejects_invalid_thermal_end_use_id(
    valid_draft_payload, valid_thermal_end_use, bad_id
):
    payload = copy.deepcopy(valid_draft_payload)
    eu = copy.deepcopy(valid_thermal_end_use)
    eu["id"] = bad_id
    payload["thermal"]["end_uses"] = [eu]

    with pytest.raises(ValueError, match="(?i)thermal.*id|space_heat|dhw|cooking"):
        parse_profile(payload)


@pytest.mark.parametrize("bad_carrier", ["coal", "hydrogen", "oil", "", None])
def test_parse_profile_rejects_invalid_thermal_carrier(
    valid_draft_payload, valid_thermal_end_use, bad_carrier
):
    payload = copy.deepcopy(valid_draft_payload)
    eu = copy.deepcopy(valid_thermal_end_use)
    eu["carrier"] = bad_carrier
    payload["thermal"]["end_uses"] = [eu]

    with pytest.raises(ValueError, match="(?i)carrier"):
        parse_profile(payload)


@pytest.mark.parametrize("bad_action", ["remove", "retire", "modify", "", None])
def test_parse_profile_rejects_invalid_thermal_action(
    valid_draft_payload, valid_thermal_end_use, bad_action
):
    payload = copy.deepcopy(valid_draft_payload)
    eu = copy.deepcopy(valid_thermal_end_use)
    eu["action"] = bad_action
    payload["thermal"]["end_uses"] = [eu]

    with pytest.raises(ValueError, match="(?i)action"):
        parse_profile(payload)


@pytest.mark.parametrize("bad_unit", ["gallons", "liters", "m3", "barrels"])
def test_parse_profile_rejects_invalid_thermal_unit(
    valid_draft_payload, valid_thermal_end_use, bad_unit
):
    payload = copy.deepcopy(valid_draft_payload)
    eu = copy.deepcopy(valid_thermal_end_use)
    eu["unit"] = bad_unit
    payload["thermal"]["end_uses"] = [eu]

    with pytest.raises(ValueError, match="(?i)unit|kwh|smc|kg"):
        parse_profile(payload)


@pytest.mark.parametrize("bad_months", [[100.0] * 11, [100.0] * 13, []])
def test_parse_profile_rejects_invalid_monthly_useful_kwh_length(
    valid_draft_payload, valid_thermal_end_use, bad_months
):
    payload = copy.deepcopy(valid_draft_payload)
    eu = copy.deepcopy(valid_thermal_end_use)
    eu["monthly_useful_kwh"] = bad_months
    payload["thermal"]["end_uses"] = [eu]

    with pytest.raises(ValueError, match="(?i)monthly_useful_kwh|12"):
        parse_profile(payload)


@pytest.mark.parametrize("bad_included", ["yes", 1, 0, None, "true"])
def test_parse_profile_rejects_non_bool_included_in_electric_load(
    valid_draft_payload, valid_thermal_end_use, bad_included
):
    payload = copy.deepcopy(valid_draft_payload)
    eu = copy.deepcopy(valid_thermal_end_use)
    eu["included_in_electric_load"] = bad_included
    payload["thermal"]["end_uses"] = [eu]

    with pytest.raises(ValueError, match="(?i)included_in_electric_load|bool"):
        parse_profile(payload)


def test_parse_profile_rejects_unknown_key_in_thermal_end_use(
    valid_draft_payload, valid_thermal_end_use
):
    payload = copy.deepcopy(valid_draft_payload)
    eu = copy.deepcopy(valid_thermal_end_use)
    eu["unknown_thermal_field"] = "not_allowed"
    payload["thermal"]["end_uses"] = [eu]

    with pytest.raises(ValueError, match="(?i)unknown|unexpected"):
        parse_profile(payload)


def test_draft_accepted_by_parser_but_rejected_by_validate_ready(valid_draft_payload):
    profile = parse_profile(valid_draft_payload)
    assert profile is not None

    with pytest.raises(ValueError, match="(?i)not ready|draft|missing artifact|unrunnable"):
        validate_ready(profile, artifacts={})



@pytest.mark.parametrize("lat,lon", [(-90.1, 0.0), (90.1, 0.0), (0.0, -180.1), (0.0, 180.1)])
def test_parse_profile_rejects_out_of_bounds_location(valid_draft_payload, lat, lon):
    p = copy.deepcopy(valid_draft_payload)
    p["location"]["latitude"] = lat
    p["location"]["longitude"] = lon
    with pytest.raises(ValueError, match="(?i)latitude|longitude|outside allowed range"):
        parse_profile(p)


@pytest.mark.parametrize("start,end,complete", [
    ("2025-01-01", None, False),
    (None, "2025-01-01", False),
    ("2025-06-01", "2025-01-01", False),
    ("2025-01-01", "2025-01-01", False),
    (None, None, True),
])
def test_parse_profile_rejects_invalid_coverage(valid_draft_payload, start, end, complete):
    p = copy.deepcopy(valid_draft_payload)
    p["consumption"]["coverage"] = {"start": start, "end": end, "complete": complete}
    with pytest.raises(ValueError, match="(?i)coverage"):
        parse_profile(p)


def test_parse_profile_rejects_thermal_cardinality_violations(valid_draft_payload, valid_thermal_end_use):
    p = copy.deepcopy(valid_draft_payload)
    u1 = copy.deepcopy(valid_thermal_end_use)
    u2 = copy.deepcopy(valid_thermal_end_use)
    p["thermal"]["end_uses"] = [u1, u2]
    with pytest.raises(ValueError, match="(?i)duplicate.*thermal"):
        parse_profile(p)
    p["thermal"]["end_uses"] = [
        {**u1, "id": "space_heat"},
        {**u1, "id": "dhw"},
        {**u1, "id": "cooking"},
        {**u1, "id": "extra"},
    ]
    with pytest.raises(ValueError, match="(?i)max.*3|end_uses"):
        parse_profile(p)


@pytest.mark.parametrize("bad_label", ["", "A" * 121, "Roof\n1", "Roof\t2", "Roof\x003", 123, None])
def test_parse_profile_rejects_invalid_roof_label(valid_draft_payload, bad_label):
    p = copy.deepcopy(valid_draft_payload)
    p["roofs"][0]["label"] = bad_label
    with pytest.raises(ValueError, match=r"(?i)roof\.label"):
        parse_profile(p)


@pytest.mark.parametrize("field", [
    "measured_input", "efficiency", "cop", "annual_fixed_cost", "annual_variable_cost"
])
@pytest.mark.parametrize("bad_val", [True, False, float("nan"), float("inf"), float("-inf")])
def test_parse_profile_rejects_thermal_scalar_bad_numbers(valid_draft_payload, valid_thermal_end_use, field, bad_val):
    p = copy.deepcopy(valid_draft_payload)
    eu = copy.deepcopy(valid_thermal_end_use)
    eu[field] = bad_val
    p["thermal"]["end_uses"] = [eu]
    with pytest.raises(ValueError, match="(?i)bool|finite|type|outside allowed range"):
        parse_profile(p)


@pytest.mark.parametrize("bad_val", [True, False, float("nan"), float("inf"), float("-inf")])
def test_parse_profile_rejects_thermal_monthly_useful_kwh_bad_numbers(valid_draft_payload, valid_thermal_end_use, bad_val):
    p = copy.deepcopy(valid_draft_payload)
    eu = copy.deepcopy(valid_thermal_end_use)
    eu["measured_input"] = None
    eu["unit"] = None
    eu["monthly_useful_kwh"] = [100.0] * 11 + [bad_val]
    p["thermal"]["end_uses"] = [eu]
    with pytest.raises(ValueError, match="(?i)bool|finite|type|outside allowed range"):
        parse_profile(p)



def test_consumption_load_fields_roundtrip_and_backward_compatibility(valid_draft_payload):
    profile_old = parse_profile(valid_draft_payload)
    assert profile_old.consumption.annual_kwh is None
    assert profile_old.consumption.monthly_totals is None
    exported_old = profile_to_dict(profile_old)
    assert exported_old == valid_draft_payload
    assert 'annual_kwh' not in exported_old['consumption']
    assert 'monthly_totals' not in exported_old['consumption']

    payload_annual = copy.deepcopy(valid_draft_payload)
    payload_annual['consumption']['annual_kwh'] = 3200.0
    profile_annual = parse_profile(payload_annual)
    assert profile_annual.consumption.annual_kwh == 3200.0
    assert profile_annual.consumption.monthly_totals is None
    exported_annual = profile_to_dict(profile_annual)
    assert exported_annual['consumption']['annual_kwh'] == 3200.0
    assert 'monthly_totals' not in exported_annual['consumption']
    assert parse_profile(exported_annual) == profile_annual

    payload_monthly = copy.deepcopy(valid_draft_payload)
    months = [200.0, 220.0, 250.0, 300.0, 350.0, 400.0, 420.0, 380.0, 330.0, 290.0, 260.0, 240.0]
    payload_monthly['consumption']['monthly_totals'] = months
    profile_monthly = parse_profile(payload_monthly)
    assert profile_monthly.consumption.annual_kwh is None
    assert profile_monthly.consumption.monthly_totals == tuple(months)
    exported_monthly = profile_to_dict(profile_monthly)
    assert exported_monthly['consumption']['monthly_totals'] == months
    assert 'annual_kwh' not in exported_monthly['consumption']
    assert parse_profile(exported_monthly) == profile_monthly


@pytest.mark.parametrize(
    'bad_consumption',
    [
        {'annual_kwh': -1.0},
        {'annual_kwh': True},
        {'annual_kwh': float('nan')},
        {'annual_kwh': float('inf')},
        {'annual_kwh': float('-inf')},
        {'monthly_totals': [100.0] * 11},
        {'monthly_totals': [100.0] * 13},
        {'monthly_totals': [100.0] * 11 + [-1.0]},
        {'monthly_totals': [100.0] * 11 + [float('nan')]},
        {'monthly_totals': [100.0] * 11 + [float('inf')]},
        {'monthly_totals': [100.0] * 11 + [True]},
        {'monthly_totals': 'not-a-list'},
        {'extra_field': 'not_allowed'},
    ],
)
def test_consumption_load_fields_reject_invalid_inputs(valid_draft_payload, bad_consumption):
    payload = copy.deepcopy(valid_draft_payload)
    payload['consumption'].update(bad_consumption)
    with pytest.raises(ValueError):
        parse_profile(payload)
