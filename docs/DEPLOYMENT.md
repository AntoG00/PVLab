# Run the source code locally

This release supports Italy / Europe/Rome only. The country-policy interface is
retained for future contributors; other countries are not implemented. Public
distribution is source code only; users run their own Python server. The author
operates no hosted service. Hosted operator/contact and hosting-region requirements
are not applicable to this release. Remote creation, pushing and publication still
require a separate owner action.

## Runtime and install

Use Linux, Apple silicon macOS, or Windows x64 with CPython 3.12 on a local
filesystem supporting SQLite WAL and private POSIX permissions or Windows ACLs.
Storage rejects symlinks and Windows reparse points. See the README for verified
platform versions; native macOS verification remains pending.
The sealed private reference stays on its recorded CPython 3.12.14 build; a fresh
interpreter does not replace that reference. New Python builds, architectures,
BLAS or scientific versions require exact comparisons, not wider tolerances.
Use a current security-patched CPython 3.12 build before release; the Python
security review is separate from Python dependency auditing.

From a reviewed source checkout, with `python3.12` installed:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes --only-binary=:all: -r requirements-dev.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation .
```

For a production-only environment, install `requirements.lock` and
`requirements-build.lock` with the same hash/binary options, then install `.`
with `--no-deps --no-build-isolation`. The build backend is pinned too. Rasterio
is absent from production; only explicit legacy raster tools need the separately
hash-locked `requirements-legacy.lock`. No application vendor directory, native
library bundle, developer-home path or agent-runtime fallback is required.

Locks were resolved with uv 0.12.1. Regenerate production and legacy locks using
`uv pip compile pyproject.toml --universal --python-version 3.12 --generate-hashes --no-header`
with respectively no extra or `--extra legacy`, and an explicit `--output-file`.
Use `--extra dev` for the development lock. Resolve the pinned build-system
requirement separately for the build lock. Review the diff, audit all locks and
run both exact gates before accepting a dependency update. Never automatically
upgrade the numerical stack to resolve an advisory.

On Windows, use the native PowerShell installation and launch commands in the
[README](../README.md#local-setup). No WSL, Bash, or environment activation is required.

## Choose onboarding or fictional data

```sh
bash feasibility/run.sh --port 8001
```

This creates owner-only external platform user-data storage. The Linux default is
`$XDG_DATA_HOME/pv-lab` or `$HOME/.local/share/pv-lab`. The macOS default is
`$HOME/Library/Application Support/pv-lab`. Windows defaults to
`%LOCALAPPDATA%\pv-lab`, created with a private owner-only ACL. `PV_DATA_ROOT` can select
another external, owner-only directory outside repositories. Existing directories
with unsafe permissions, symlinks or Windows reparse points are rejected. Use a dedicated OS user and
local disk, not shared writable storage. Open household setup in the browser and
supply an explicit profile; there is no inherited household or automatic activation.

```sh
bash feasibility/run.sh --demo --host 127.0.0.1 --port 8001
```

The optional demo generates fictional hourly arrays and provenance in a new
`fictional-demo` subdirectory of the data root, then starts the same onboarding
service. It does not create a ready profile or pretend the arrays are measured
household data. Existing demo outputs are not overwritten. The offline public
gate exercises complete fictional profiles and their exact reference:

```sh
.venv/bin/python tools/check_public_plan.py --mode public --workers 1
```

Install Node.js 24.22.1 for that gate; `PV_NODE` can name its executable. No Node
runtime is needed to serve the application. The gate verifies runtime/dependency
versions, generates fictional inputs outside Git, copies only the explicit export
inventory and blocks remote connections throughout its test children. Network is
needed for initial dependency installation and audit, not the synthetic suite.

## Network and process lifecycle

Default binding is loopback (`127.0.0.1`), accessible on this computer. To enable
trusted LAN access explicitly, use `--host 0.0.0.0` and restrict the firewall. Do not expose
this HTTP service to the internet. Session cookies remain random capabilities,
HttpOnly and SameSite=Lax without Domain; HTTP LAN cookies cannot require HTTPS.
Host/Origin, CSRF and ownership checks remain enabled. `--allowed-host` restricts
accepted authorities; it is not user authentication. `--local-admin-restart` is
optional, loopback-only and refuses active work; ordinary launches omit it.

Use Ctrl-C in the foreground for normal stop; the server closes its worker pool
and sockets in its cleanup path. Under a supervisor send SIGINT and allow cleanup
before force-kill. Do not assume SIGTERM drains jobs: it uses process termination.
After a crash, restart with the same data root; interrupted jobs fail rather than
publish partial results. Check recovery/deletion state as described in OPERATIONS.

An installed wheel can run from outside the source tree:
`python -m feasibility.server --host 127.0.0.1 --port 8001`.
It contains application Python and curated browser assets, not tests, household
inputs, private reports or native dependencies. Demo generation and full gates
use the reviewed source checkout.

## Source-only release policy and optional future hosting

Original code is source-available under the unmodified PolyForm Noncommercial
License 1.0.0; commercial use, including installers' customer assessments,
requires separate explicit written permission. See LICENSE, NOTICE and
[third-party notices](../THIRD_PARTY_NOTICES.md). The source release includes no
real provider archives, manufacturer tables, private inputs or dependency binaries.
Those separate inputs/dependencies retain their own terms and required notices.

The fiscal model was reviewed by multiple LLMs without independent specialist
validation. No professional review or current legal correctness is claimed.
[Fiscal sources, dates and limitations](FISCAL_STATUS.md) must remain visible;
specialist review is not an owner-imposed blocker for this code-only release.

Applicable release gates retain privacy/secret scans, reviewed synthetic fixtures,
dependency/data-license compatibility, exact private/public regression and clean
installation. Hosted operator/contact and hosting-region requirements are not
applicable. Any unresolved rights for material actually included in an export
remain blockers. Rights to redistribute unbundled real provider data are not
claimed and must be checked by its users separately. Local runtime/OS maintenance
belongs to the user; no interpreter or Node binary is distributed with this source.
Phase 21 prepares a fresh local repository and stops before remote creation,
pushing, publishing or deploying anything.

Optional future work: TLS reverse proxy and HTTPS `__Host-` cookies, ASGI adapter,
Dockerfile/.dockerignore/image scanning, cgroup CPU/memory quotas, remote access
policy and automatic 30-day idle/90-day absolute retention. None is a prerequisite
for the current local/LAN stdlib service; none is implemented by this phase.
