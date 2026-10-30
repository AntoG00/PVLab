"""CLI-only immutable solar candidates, never activation.

Profile manifest fields (all explicit): profile (HouseholdProfileV1), years,
solar {loss_percent, mounting, technology, source_policy}, load {monthly_totals,
policy (ProjectionPolicy fields), optional interval_csv and ingest_options},
cache_root (external), tenant_id (opaque 32 hex chars). Relative input paths are
resolved beside the manifest. No household bootstrap, load or geometry defaults.

Source policy is pvgis6_sarah3 + openmeteo_era5 with
hour_mapping=pvgis6-sarah3-utc-observation-v1. Only this audited combination is
supported. No fixed weather window, 5.3 or per-hour fallback. Empty roofs fetch
only historical temperature: there is no generation/reference-plane request.

PVGIS 6 accepts north-clockwise compass orientation directly (v5 south aspect
would be (azimuth-180)%360). Explicit 1 kWp, module and system efficiency replace
v5 peakpower/pvcalculation/components options; verbose requests components.
Validate returned tilt/orientation to 0.01 degrees, coordinates to 1e-4 degrees,
kWp to 1e-4, efficiency to 1e-6; do not copy legacy roof-specific rounding waivers.
Missing response coordinates use validated request provenance, never a claim of
provider-confirmed location. Open-Meteo nearest ERA5 grid may differ by <=0.26
geographic degrees on each axis; retain both grid and request coordinates.

Timestamp contract: JRC's PVGIS manual section 9.3 describes instantaneous
satellite observations with a location-dependent offset, used as an hourly
energy proxy. PVGIS 6's generate_timestamps converts to UTC and adds source time
offset. For a known UTC/Hourly/SARAH-3 request, require exactly one observation
per UTC hour, a constant nonnegative within-hour nanosecond offset per complete
year, and identical solar timestamps across planes. Only after those checks,
associate each observation with its corresponding UTC hour. Reject gaps,
duplicates, changes of offset, unsupported sources and partial years. No
interpolation, sorting, or generic timestamp flooring. Preserve original strings
and exact offsets in the digest-bound solar-source.json. Validate the observed
response frequency alias `h` for requested `Hourly`; the old minute field
records only the first offset's minute component for compatibility.
Historical temperature uses explicit Open-Meteo ERA5, hourly temperature_2m,
UTC/Celsius, nearest grid with elevation adjustment disabled. Require exact UTC
hourly valid times and full requested years; temperature is model data, not
private measurements and not a reconstruction at solar observation instants.

References checked 2026-10-28 (live availability/terms remain unverified):
https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/using-pvgis-5/pvgis-5-user-manual_en#93-note-on-pvgis-data-timestamps
https://code.europa.eu/pvgis/pvgis/-/blob/main/pvgis/api/datetime/datetimeindex.py
https://photovoltaic-geographic-information-system.ec.europa.eu/api/v6/openapi.json
https://open-meteo.com/en/docs/historical-weather-api
"""
import argparse
from datetime import datetime
from pathlib import Path
import sys
from .consumption_ingest import IngestOptions, parse_consumption
from .dataset_compile import compile_dataset
from .load_projection import ProjectionPolicy, project_load
from .profiles import strict_json, canonical_json, parse_profile
from .solar_provider import SolarRequest, TenantCache, fetch_pvgis, _atomic, _private_dir


def build_candidate(profile_manifest, output, *, transport=None):
    manifest_path = Path(profile_manifest).resolve()
    output = Path(output).resolve()
    repo = Path(__file__).resolve().parent.parent
    if output == repo or repo in output.parents or output.exists():
        raise ValueError('candidate must be a new external directory')
    _private_dir(output.parent, allow_shared=True)
    status = output.with_name(output.name + '.status.json')
    def progress(stage, completed, total):
        _atomic(status, canonical_json(dict(state=stage, completed=completed, total=total, activated=False)))
    progress('validating', 0, 1)
    try:
        if manifest_path.stat().st_size > 2*1024*1024:
            raise ValueError('manifest size limit')
        obj = strict_json(manifest_path.read_bytes())
        if set(obj) != {'profile', 'years', 'solar', 'load', 'cache_root', 'tenant_id'}:
            raise ValueError('invalid profile manifest fields')
        profile = parse_profile(obj['profile'])
        cache = TenantCache((manifest_path.parent / obj['cache_root']).resolve(), obj['tenant_id'])
        load_spec = obj['load']
        if set(load_spec) - {'monthly_totals', 'policy', 'interval_csv', 'ingest_options'}:
            raise ValueError('invalid load fields')
        intervals = None
        if 'interval_csv' in load_spec:
            opts = dict(load_spec['ingest_options'])
            for k in ('coverage_start', 'coverage_end'):
                if opts.get(k) is not None:
                    opts[k] = datetime.fromisoformat(opts[k].replace('Z', '+00:00'))
            with (manifest_path.parent / load_spec['interval_csv']).open('rb') as f:
                intervals = parse_consumption(f, IngestOptions(**opts))
        load = project_load(intervals, load_spec.get('monthly_totals'), obj['years'], ProjectionPolicy(**load_spec['policy']))
        solar = obj['solar']
        if set(solar) != {'loss_percent', 'mounting', 'technology', 'source_policy'}:
            raise ValueError('invalid solar fields')
        source = fetch_pvgis(SolarRequest(profile, tuple(obj['years']), cache=cache,
                                        progress=progress, **solar), transport)
        progress('compiling', 0, 1)
        dataset = compile_dataset(profile, source.artifact, load)
        dataset.write_candidate(output)
        _atomic(status, canonical_json(dict(state='complete', completed=1, total=1, activated=False,
            candidate_digest=dataset.digest, solar_source_digest=source.artifact.source_digest,
            source_contract='pvgis6-sarah3-openmeteo-era5-v1', source_policy=solar['source_policy'])))
        return dataset
    except Exception as exc:
        # No raw provider body, coordinates, input paths or arbitrary exception text.
        _atomic(status, canonical_json(dict(state='failed', error=type(exc).__name__, activated=False)))
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--profile-manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = build_candidate(args.profile_manifest, args.output)
    except Exception:
        print('Solar candidate failed; see its private status file. No activation performed.', file=sys.stderr)
        return 1
    print('Validated candidate SHA256: ' + result.digest)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
