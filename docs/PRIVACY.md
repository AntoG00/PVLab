# Privacy and local data

Status: source-code distribution for users to run on their own machines, Italy
only. The author operates no hosted service. Hosted operator/contact and hosting
region requirements are not applicable to this release. The person running an
instance controls its local storage; separate hosting/processing by others needs
its own review and information. This is a technical description, not a legal
compliance certification or a hosted-service privacy notice.

## What is stored

Browser sessions submit household location, consumption files/totals, timestamps,
roof labels/orientation/capacity, equipment and thermal-use assumptions, selected
market/fiscal parameters, scenarios and defaults. The service stores immutable
input revisions, uploaded evidence, provenance, computed results/reports and job
state in SQLite and server-owned files outside the checkout. Coordinates and
consumption patterns can identify a home; generated reports may contain them.

There are no email addresses/accounts or password-based recovery. A random
browser capability identifies the session; the server stores its hash. The
`pv_session` cookie is HttpOnly, SameSite=Lax, without Domain. Startup binds to loopback by default. Explicitly enabled HTTP LAN
access is not encrypted. Anyone with a stolen capability may access its data;
use a trusted LAN and protect the browser and operating-system account.

Household data stays in tab memory in the UI; only display preferences persist in
browser storage. Server data persists independently. Losing/clearing the cookie
or browser session can leave stored data inaccessible from that browser: there is
no email recovery, account merging or supported restoration by guessing IDs.
Another browser receives a separate household namespace.

## Transfers and external providers

Manual roof/input entry is available independently of providers. After explicit
consent, solar/weather jobs send coordinates, roof geometry, equipment settings
and requested years to the European Commission PVGIS API and coordinates/years
to Open-Meteo's historical ERA5 API. Providers also see the requesting server's
network address. No consumption file is required by these provider requests.
Responses/provenance and coordinate-bearing caches remain tenant-private locally.
These services have their own policies, retention, availability and terms.

New candidates use PVGIS 6 SARAH-3 plus explicitly selected Open-Meteo ERA5;
there is no automatic switch to 5.3, another irradiance source or per-hour fallback.
The later approved Phase 9 contract governs over older fallback wording.
Provider timestamps/offsets and metadata are validated before compilation.

Read the [Commission privacy information](https://commission.europa.eu/privacy-policy_en)
and [Open-Meteo terms/privacy](https://open-meteo.com/en/terms) before use.
Open-Meteo's published free service is limited to non-commercial use and its
privacy notice describes coordinate-bearing troubleshooting logs retained up to
90 days. Those provider logs are outside this application's deletion control.
Recheck current terms for the intended use and required attribution.

Google Solar is a separate optional local helper using the user's own key; the
server does not accept that key or proxy Google requests. External Google caches
are user-managed, subject to the guidance in
[the solar data notes](../feasibility/SOLAR_DATASETS.md); copied/derived output is
not automatically exempt from provider restrictions.

## Retention, export and deletion

Local persistent storage is the default. There is no automatic 30/90-day expiry.
Use owned report exports for calculation results. They are not a complete,
portable backup of all original inputs; retain original files separately if
needed. Export only to a location you control and inspect before sharing.

The authenticated `DELETE /api/session` operation explicitly deletes the tenant,
revokes its sessions, cancels work and removes its owned files. Cleanup failures
stay tombstoned for administrator retry; startup recovery retries incomplete
cleanup. No account recovery or undo is promised. Deleting one profile does not
imply deleting every session-owned upload; use tenant deletion for the namespace.
An administrator can stop the service and remove a dedicated data root to delete
all local households; verify the path and that no other installation shares it.
Do not delete the private developer reference or original private sources.

No automatic private-input backup is made. Explicit legacy migration preserves a
private read-only imported-byte copy; it is a separate retention obligation.
Downloaded files, user backups, provider logs and storage-level snapshots require
separate deletion. Filesystem unlink/SQLite row deletion is not a guarantee of
forensic erasure from SSDs, journals or backups.

## Logs and access

HTTP access logging is disabled and operational audit events use bounded,
sanitized fields. Do not enable request-body, cookie, URL-query, file-content or
profile dumps. Keep diagnostic logs owner-only; share only reviewed/redacted
summaries. An administrator/OS user controlling storage can read it: browser
ownership is application isolation, not encryption against that administrator.
The service includes no analytics, advertising or automatic cloud backup.
