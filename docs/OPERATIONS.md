# Operating a user-run local instance

Use the installation and trusted-LAN boundaries in DEPLOYMENT.md. Run under a
dedicated OS user with external owner-only storage (directories 0700, files 0600).
Keep the default SQLite WAL database, immutable artifacts and catalog revisions
together; do not edit SQLite or active immutable datasets while jobs run.

## Compute budget and recovery

The spawn-only pool allows two immutable revision-bound workers, one operation
per worker, queue capacity eight, and one queued/running heavy job per tenant.
Scientific/native thread counts are one per process. Each job has a 900-second
hard deadline and each worker a 2 GiB RSS ceiling. The parent samples RSS and
terminates violations; this is a watchdog, not a cgroup allocation guarantee.
Allow headroom for parent, transient allocations, database, OS and two workers.
Limits can be reduced through the existing bounded configuration; do not remove
caps or enlarge them without owner approval and measured evidence.

Profiles allow at most 12 roofs and 30 weather years. Sizing uses bounded candidate
budgets; do not replace it with an eager Cartesian search. Monitor worker peak
RSS, cold JIT/warm latency, queue rejection, timeout and cancellation with synthetic
profiles before selecting a machine. A small fixture is not proof of worst-case
capacity. Keep maximum-envelope stress evidence separate from ordinary test timing.

A failed/killed/over-limit worker makes its job fail; partial output never becomes
current. Retry creates new work against the current revision. Restart recovery
marks interrupted queued/running work failed. Normal Ctrl-C closes the pool;
SIGTERM/forced power loss require restart recovery. No supervisor configuration
that silently restarts an unsafe/incomplete job is supplied.

Deletion cancels owned work first. Incomplete removal is tombstoned; startup
janitor retries it. An administrator can explicitly run the same recovery after
stopping the server:

```sh
.venv/bin/python -c 'from feasibility.tenant_store import TenantStore; TenantStore().janitor()'
```

Use exactly the intended `PV_DATA_ROOT`. Do not run a second administrator process
against active computation. Browser users use the owned session deletion API;
loss of a capability is not recoverable by email.

## Providers

New solar candidates use PVGIS 6 SARAH-3 and explicitly selected historical
Open-Meteo ERA5. No automatic PVGIS 5.3 fallback is implemented or authorized by
the approved Phase 9 amendment. Legacy manifest-bound arrays remain unchanged.
Requests retain original timestamps, exact offsets and response digests. Validated
request coordinates are distinguished from provider-confirmed coordinates; ERA5
coordinates identify the selected model grid, not a measured home sensor.

Provider concurrency is two with at least one second between starts within the
process. Connection/read/total request limits are 10/30/120 seconds; responses are
bounded at 128 MiB. HTTP 429/529 and 5xx get at most three attempts with 1/2-second
backoff; redirects, invalid bodies and other failures stop. Aggregate API use
across installations is not globally rate-limited; obey each provider's limits.
Do not turn deterministic CI into a live API load test or log coordinate queries.

Separate 2026-10-30 smoke: one fictional Italian test point, one 2023 south-facing
plane at 1 kWp, 14% loss; live PVGIS GET returned 8,760 observations and passed
current equipment/time/offset validation. It supplied no confirmed coordinates,
so authority remained validated_request. Open-Meteo ERA5 returned 8,760 UTC/Celsius
values. This verifies that sample contract, not every location/year or provider
license. The [official API service](https://photovoltaic-geographic-information-system.ec.europa.eu/api/)
and [release notes](https://photovoltaic-geographic-information-system.ec.europa.eu/api/release-notes)
were checked: the October POST-body change leaves GET query requests unchanged.
Review SARAH-3 coverage for each requested site/year. No invented v6 URLs or
claims of confirmed provider availability/terms for all cases are permitted.

The [Open-Meteo terms](https://open-meteo.com/en/terms) distinguish free
non-commercial API access from paid commercial access and require attribution.
PVGIS dataset-specific licensing/attribution, including third-party irradiance,
needs owner review; a successful response does not grant redistribution rights.

## Monthly market catalog maintenance

Obtain authorized GME monthly archives outside Git; retain source hashes privately.
Choose the actual interval/schema rather than assuming hourly records: current
sources may use quarter-hours. Stage and validate before publishing:

```sh
.venv/bin/python -m feasibility.prepare_market_prices --input "$PV_MARKET_INPUT" --catalog "$PV_CATALOG_ROOT" --start 2025-01-01 --end 2025-02-01
.venv/bin/python -m feasibility.prepare_market_prices --input "$PV_MARKET_INPUT" --catalog "$PV_CATALOG_ROOT" --start 2025-01-01 --end 2025-02-01 --publish
```

Set those variables to external directories. Dates are a half-open example, not
an instruction to replace a production window. Use `--source-schema` for the
actual archive format and explicit `--window-start/--window-end` for the intended
valuation window. Inspect complete DST/series coverage and signed EUR/MWh to
EUR/kWh conversion before publish. Identical imports are idempotent; corrected
historical days require `--replace` and a new immutable revision/correction record.

Record the prior revision from `current.json` before publish. To roll back, stop
admissions/service, validate the old revision with `market_catalog.read_revision`,
write a same-directory temporary pointer `{"schema_version":"gme-catalog-v1","revision":"<reviewed-digest>"}`
with owner-only permissions, fsync it and atomically replace `current.json`, then
restart. Never modify or delete immutable revision files. There is no dedicated
rollback CLI. Existing profiles stay pinned regardless of the operator pointer;
users must explicitly refresh/recompile to select a different catalog revision.

GME's [PUN Index definition](https://gme.mercatoelettrico.org/it-it/Home/Pubblicazioni/Indici-GME/PUNIndexGme)
and [2025 market notice](https://gme.mercatoelettrico.org/it-it/Home/AvvisieComunicati/AvvisieComunicatiME?id=6208)
distinguish the purchase-weighted national reference from zonal prices. Neither
is a guaranteed export payment or fixed-retail contract. Rights to redistribute
raw archives remain unconfirmed; none is included. Archive ingestion/coverage
checks are distinct from approval of redistribution rights.

## Updates, audit and release decisions

CI installs hash-locked dependencies, builds the curated package, validates export
paths, scans secrets/privacy and runs the entire offline synthetic Python, Node,
acceptance and exact public reference suite. It receives no private denylist,
private reference, household inputs or deployment credentials. Private development
also runs its separate exact sealed oracle. Do not upload private evidence as CI
artifacts or secrets. Do not refresh expected numbers to clear a failure.

Run `python -m pip_audit --require-hashes --no-deps --disable-pip -r requirements-dev.lock`
and repeat for production/build/legacy locks. Audit failures and service errors
block the audit step. For each advisory record affected lock/version, reachable
code, severity, fix and an owner-reviewed decision/expiry in private evidence;
never blanket-ignore advisories. An upgrade requires both exact gates and review.
Also review Python, Node, OS and optional raster/GDAL advisories separately.

No automatic private-data backups are made. Stop the service before any deliberate
operator backup and protect/decommission it explicitly. Inspect operational logs
for capability, coordinate, filename and consumption leaks without publishing
matched values. No request-body or cookie logging should be enabled.

This is a source-code-only release, with no author-hosted service. Hosted
operator/contact and hosting-region requirements are not applicable. Startup
binds to loopback; optional LAN access is explicit. Original code is source-available
under PolyForm Noncommercial 1.0.0 with separate written permission required for
commercial use. See THIRD_PARTY_NOTICES.md for independent dependency/data terms.

The fiscal model has multiple-LLM review only, without independent specialist
validation or a current-legal-correctness claim. Sources, applicability dates and
limitations are in FISCAL_STATUS.md. Specialist review is not a blocker for this
code-only distribution. Privacy, secret scans, synthetic-fixture provenance,
dependency/data licensing and exact regression gates remain applicable. Publication
requires the separate owner release action after reviewing the fresh export.

## Recorded synthetic capacity smoke (2026-10-30)

On the development machine with no competing test suite, two tenants each used
12 synthetic planes and 30 weather years (1995–2024), one forecast year, no storage
and a two-layout sizing search. Cold evaluation including JIT took 6.94 seconds,
warm evaluation 0.21 seconds and bounded sizing 0.36 seconds (two-job batches).
Sampled worker RSS peaks were 488,357,888 / 499,810,304 bytes; parent peak RSS was
389,607,424 bytes. Queued cancellation published no result; owned deletion took
0.016 seconds. The 2 GiB caps were unchanged. These are hardware/scenario-specific
observations, not a worst-case guarantee for larger battery/thermal/horizon
searches. Deadline/crash/recovery and redacted-audit cases remain in the full
synthetic suite, independent of these timings.
