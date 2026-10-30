# PV Lab

A source-available photovoltaic, storage and household electrification scenario calculator.
Distributed as GitHub source code for users to run locally; the author operates no hosted service.
Italy and Europe/Rome are the currently supported profile country and timezone.
Results depend on explicit household inputs, equipment assumptions and selected
fiscal rules. They are scenarios, not installation designs or financial promises.

## Local setup

Use Linux and CPython 3.12. Install the complete hash-locked development environment
and the package without unpinned build dependencies:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes --only-binary=:all: -r requirements-dev.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation .
bash feasibility/run.sh --host 127.0.0.1 --port 8001
```

The default host is loopback (`127.0.0.1`). Use `--host 0.0.0.0` only for explicit trusted-LAN access. Each browser session owns
its profiles, uploads and results. Enter an explicit household through setup;
`--demo` first generates fictional arrays externally without activating them.
Data persists until explicit deletion. `PV_DATA_ROOT` selects an external,
owner-only data directory. Node.js 24.22.1 is required only for tests.

See [installation and launch blockers](docs/DEPLOYMENT.md),
[operations](docs/OPERATIONS.md) and [privacy](docs/PRIVACY.md).
Italy is the only implemented country. The fiscal model was reviewed by multiple
LLMs without independent fiscal-specialist validation; it makes no claim of
professional review or current legal correctness. See [fiscal status and dated
sources](docs/FISCAL_STATUS.md). Specialist validation is not a release blocker
for this code-only distribution. Hosted operator/contact and hosting-region
requirements are not applicable to this release.

## License

Original code uses the included, unmodified [PolyForm Noncommercial License 1.0.0](LICENSE).

Noncommercial use is permitted under the included license. Commercial use, including customer assessments and paid services, requires separate written permission from the copyright holder.

This project is source-available. See [NOTICE](NOTICE) for the copyright holder
and [third-party notices](THIRD_PARTY_NOTICES.md) for independent code/data terms.
No hosted service, GitHub publication or deployment is performed by installation.

## Reproducible public tests

```sh
.venv/bin/python tools/check_public_plan.py --mode public
```

Public tests use analytic fictional inputs, mocked provider responses and a
separate deterministic reference. No household files, API keys, live provider
requests or private reference are required. The generator and its formulas are
in `tests/fixtures/public_demo/`; large arrays are generated outside the checkout.
The fictional example is invented, not an anonymized home or a real site survey.

Private development also runs `--mode private` with an external
`PV_PRIVATE_ROOT`. That additional gate checks the sealed private reference
exactly; the public reference never replaces it. Private history and artifacts
are not publication inputs. `tools/export_allowlist.json` identifies reviewed
files; creating or publishing a fresh repository is a separate operation.

See [model and data notes](feasibility/README.md).
