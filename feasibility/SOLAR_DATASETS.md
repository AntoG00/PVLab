# Solar data and privacy

Manual roof entry is always independent of provider services. Enter plane IDs,
labels, tilt, compass azimuth and capacity explicitly. Geometry, panel fit,
structure and electrical design need independent verification.

New profile candidates use explicitly selected PVGIS 6 SARAH-3 solar years and
historical Open-Meteo ERA5 temperature. Requests, responses, timestamps, offsets,
units and provider metadata are pinned to an immutable candidate before compile.
No automatic provider-version or per-hour fallback is used for new profiles.
Live provider availability and current terms remain separate release checks.

PVGIS power is an AC-equivalent proxy, not newly calculated DC physics. Coarse
plane shading applies a stated uniform extra attenuation, not measured roof
geometry. Weather envelopes are sensitivity scenarios, not forecasts or
confidence intervals. Public tests generate analytic weather and mock provider
responses, without internet access or real household inputs.

Uploaded evidence is a preview and cannot silently become active simulation
input. Household solar data and coordinates remain private to the owned profile.
Historical local raster/merged-weather tools are compatibility utilities; their
geometry and inputs must be supplied externally. They are not onboarding defaults.

## Optional standalone Google Solar helper

`tools/google_solar_helper.py` runs separately with the user's own local
`GOOGLE_API_KEY` and an external output directory. The simulator does not collect
Google keys or call, host or proxy Google Solar. Suggested roof sections are
unverified measurements, not automatically accepted profile geometry.

The helper records attribution and retrieval/expiry metadata. Its retention
policy uses at most 30 consecutive days for cached Building Insights/Data Layers
under the applicable [EEA service terms](https://cloud.google.com/terms/maps-platform/eea/maps-service-terms),
with deletion guidance and [Solar attribution policies](https://developers.google.com/maps/documentation/solar/policies).
Check current terms before use. Copying or deriving configuration does not by
itself establish an exemption. The design does not rely on the fixed-media
Downstream Transaction exception. Users manage deletion of their external Google
caches; those responses and helper outputs never belong in Git or a public demo.
Existing private raster evidence and backups must be preserved separately.
