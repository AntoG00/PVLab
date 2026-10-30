#!/usr/bin/env python3
"""OPTIONAL standalone Google Solar roof suggestions; never imported by the app.

Run locally with your own Google account/key in GOOGLE_API_KEY (no CLI key).
Output is Google Maps content, not simulator input or independently verified
geometry. Manual roof measurement is independent. EEA service terms section
21.2 permit BuildingInsights/DataLayers caching up to 30 consecutive days;
delete afterwards. A fixed-media Downstream Transaction exception exists; this
helper does not rely on it. Copying/confirming/deriving config is no exemption.
Raw caches and all suggestions remain user-managed; no app retention service.
https://cloud.google.com/terms/maps-platform/eea/maps-service-terms
https://developers.google.com/maps/documentation/solar/policies
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import sys
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler

ENDPOINT = 'https://solar.googleapis.com/v1/buildingInsights:findClosest'
MAX_BYTES = 8 * 1024 * 1024
POLICY_URL = 'https://developers.google.com/maps/documentation/solar/policies'
TERMS_URL = 'https://cloud.google.com/terms/maps-platform/eea/maps-service-terms'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('redirect rejected')


class PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # Do not echo a key mistakenly supplied as an unsupported CLI argument.
        super().error('invalid helper arguments; use --help')


def _fetch(parameters, key):
    # Header authentication avoids keys in URLs, exceptions, shell history or logs.
    request = Request(ENDPOINT + '?' + urlencode(parameters), headers={'X-Goog-Api-Key': key})
    with build_opener(NoRedirect).open(request, timeout=30) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('response size limit')
    return json.loads(raw)


def _finite(value, lower, upper):
    if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError('invalid numeric field')
    return value


def build_suggestions(latitude, longitude, output, *, transport=None, now=None):
    latitude = _finite(latitude, -90, 90)
    longitude = _finite(longitude, -180, 180)
    output = Path(output).resolve()
    repo = Path(__file__).resolve().parent.parent
    if output == repo or repo in output.parents:
        raise ValueError('output directory must be external to repository')
    if output.exists():
        raise ValueError('output directory must be new')
    key = os.environ.get('GOOGLE_API_KEY')
    if not key:
        raise ValueError('GOOGLE_API_KEY environment variable required')
    params = {'location.latitude': latitude, 'location.longitude': longitude, 'requiredQuality': 'HIGH'}
    # Never include exception text: providers and injected transports may include a key.
    try:
        obj = (transport or _fetch)(params, key)
    except Exception:
        raise ValueError('Google Solar request failed; details withheld') from None
    if type(obj) is not dict or type(obj.get('solarPotential')) is not dict:
        raise ValueError('missing solar potential')
    potential = obj['solarPotential']
    segments, panels = potential.get('roofSegmentStats'), potential.get('solarPanels', [])
    if type(segments) is not list or not 1 <= len(segments) <= 1000 or type(panels) is not list or len(panels) > 100000:
        raise ValueError('invalid roof sections')
    counts = [0] * len(segments)
    for p in panels:
        i = p.get('segmentIndex')
        if type(i) is not int or not 0 <= i < len(segments):
            raise ValueError('invalid panel segment')
        counts[i] += 1
    sections = []
    for i, segment in enumerate(segments):
        sections.append(dict(id=f'suggestion-{i}', tilt_deg=_finite(segment.get('pitchDegrees'), 0, 90),
            azimuth_deg=_finite(segment.get('azimuthDegrees'), 0, 360),
            area_m2=_finite(segment.get('stats', {}).get('areaMeters2'), 0, 1e9),
            candidate_panel_count=counts[i], status='unverified_suggestion'))
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('retrieval time must be timezone-aware')
    now = now.astimezone(timezone.utc)
    result = dict(schema_version=1, provider='Google Solar API', attribution='Google Maps',
        attribution_policy=POLICY_URL, terms=TERMS_URL, request_coordinates=dict(latitude=latitude, longitude=longitude),
        retrieved_at=now.isoformat(), delete_by=(now + timedelta(days=30)).isoformat(),
        retention='Delete these Google-derived suggestions and any separately retained raw caches within 30 consecutive days. User-managed; no exception assumed.',
        limitations='Suggestions only; verify roof geometry and structural suitability independently. Display with required Google Maps attribution; do not display on a non-Google map.',
        roof_sections=sections)
    # New external directory, private permissions; no key or raw response retained.
    output.mkdir(parents=True, mode=0o700)
    path = output / 'roof-suggestions.json'
    with path.open('x', encoding='utf-8') as f:
        os.chmod(path, 0o600)
        json.dump(result, f, indent=2, allow_nan=False)
        f.write('\n')
    return path


def main(argv=None):
    parser = PrivateArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--latitude', type=float, required=True)
    parser.add_argument('--longitude', type=float, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        build_suggestions(args.latitude, args.longitude, args.output)
    except Exception:
        print('Google Solar helper failed; check configuration and provider access. Details withheld.', file=sys.stderr)
        return 1
    print('Roof suggestions saved externally. Google Maps attribution and 30-day deletion apply.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
