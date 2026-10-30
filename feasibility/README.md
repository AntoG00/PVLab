# Calculator notes

Enter a household profile before evaluating results. Consumption can be complete
interval data or an explicitly acknowledged monthly/annual synthetic projection.
Partial interval data supports review; missing coverage needs complete monthly
totals. Gross electrical demand and thermal end-use actions must be separated to
avoid counting an already-electric use twice.

Manual roof planes are always available. Capacity is entered by the user; panel
fit, roof structure, setbacks and electrical design require independent checks.
The application can prepare PVGIS/temperature candidates only after explicit
provider consent and selection. Provider availability and current usage terms
need separate verification before release claims. Ordinary tests mock providers.
Google Solar is only an optional standalone local helper, described in
[SOLAR_DATASETS.md](SOLAR_DATASETS.md); the application never accepts Google keys
or proxies that service.

Wholesale catalog revisions are pinned to the profile. Flat valuation is an
explicit choice. PUN export valuation is a comparison, not a default export
remuneration guarantee. PUN-indexed retail billing is not supported. Fixed retail,
export, tax eligibility and thermal costs require explicit scenario inputs.
See [GAS_TAXES.md](GAS_TAXES.md) and [HEAT_PUMP_MODEL.md](HEAT_PUMP_MODEL.md).

The local server uses browser-session capabilities and an owned job queue.
Changing inputs creates a new revision; stale results cannot become current.
Exports belong to the selected profile/revision/job. Shared household data is
never selected merely by a client-supplied file path.

Run from the project root with `bash feasibility/run.sh --host 127.0.0.1 --port 8001`.
`PV_PYTHON` optionally selects an interpreter. `PV_DATA_ROOT` must be outside Git
checkouts. Ordinary imports do not load household inputs. Legacy local adapters
require explicitly supplied external inputs and are not new-profile defaults.

Use the root `pyproject.toml` and hash locks in the [installation guide](../docs/DEPLOYMENT.md).
Production excludes rasterio; the legacy extra is opt-in. The local/LAN stdlib
server keeps two spawn workers, one native thread each, eight queued jobs,
900-second deadlines and a 2 GiB RSS ceiling per worker. See
[operations](../docs/OPERATIONS.md) for recovery, market updates and audit, and
[privacy](../docs/PRIVACY.md) for retention/deletion and provider transfers.
Country-policy interfaces remain available for future contributions; only Italy
is implemented. Fiscal inputs remain user-supplied scenario assumptions. Multiple LLMs reviewed
the model; there is no independent fiscal-specialist validation or current legal
correctness claim. See [fiscal status](../docs/FISCAL_STATUS.md). This source-available
release runs on user machines with loopback startup; there is no author-hosted service.
