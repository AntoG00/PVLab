# Audited local source export — publication checklist

This checklist describes preparation of an unpublished local export. No GitHub
remote, push, release publication or deployment is authorized by these checks.

## Release policy

- [x] Source-code-only distribution; users run their own Python server.
- [x] Source-available original code: unmodified PolyForm Noncommercial 1.0.0,
  LICENSE and required NOTICE preserved. Commercial use, including customer
  assessments and paid services, requires separate written permission.
- [x] Third-party code/data terms are separate; THIRD_PARTY_NOTICES.md retained.
  No real provider archives, manufacturer tables, private inputs, dependency
  binaries or native-library bundle is included.
- [x] Loopback startup; explicit optional trusted-LAN binding. Session isolation,
  CSRF/Host/Origin controls, owned deletion and bounded workers retained.
- [x] Hosted operator/contact and hosting region: not applicable to this release.
- [x] Fiscal status: multiple-LLM review without independent specialist validation;
  sources/dates and limitations in FISCAL_STATUS.md. No professional-review or
  current-legal-correctness claim; specialist review is not a code-only blocker.

## Verification evidence

Gate 20 cleared with private/public Python 1,990 passed, Node 352 passed, acceptance
and exact sealed/synthetic references; no skipped tests or oracle refresh. Four
dependency locks audited without known advisories. Original license/notices and
production-wheel loopback/isolation/deletion/log checks passed.

- [x] Phase 21 full private and clean exported public suites: Python 2,004 passed,
  Node 352 passed, acceptance and exact sealed/synthetic references passed. No
  skipped tests or oracle refresh; all 14 exporter checks passed independently
  in a fresh Gemini verification and locally.
- [x] All payload bytes/hashes, relative paths and modes audited against committed
  reviewed source. No private Git objects, raw inputs or source paths are copied.
- [x] Strict export privacy and directory secret scans: zero findings; no legacy
  exemptions. The synthetic PNG has only IHDR/IDAT/IEND chunks.
- [x] Build from the exported directory: wheel 61 entries, source archive 74 files;
  every application/source member matches reviewed bytes. License/notices retained,
  no bundled native dependencies and no archive-content/metadata privacy findings.

Reviewed source inventory SHA256:
`d9610d1d10d7cc7a0caffc0aeef96d2b1623ca8f24e27fc902915a18f021da73`.
Detailed reports remain outside the export. The final delivery supplies the
manifest SHA256 and the reviewed fresh Git commit; neither is embedded here,
because embedding a self-dependent whole-tree/commit digest would be circular.

The final Git gate requires exactly one initial commit on main, the approved
public author/committer identity, clean fsck/worktree, every object scanned, no
inherited refs/objects/config/hooks/alternates, no remote and no tags. Git and
directory secret scans and the source/manifest/metadata checks run again on the
final payload before handoff. A failed check blocks delivery, not just a checkbox.

`PUBLIC_EXPORT_MANIFEST.json` lists sanitized file hashes/sizes/modes; its own SHA256
is supplied with the reviewed export. The `reviewed_source_sha256` field excludes
this checklist and generated manifests to avoid a circular digest. The manifest's
file entries still include the final checklist and software manifest.
`SOFTWARE_MANIFEST.json` records source license, runtime and dependency-lock hashes.
Neither includes private source commit IDs, local paths or private gate evidence.

## Remaining owner action and update policy

Select the final GitHub repository name/remote and separately approve the exact
export commit and manifest hash before creating a remote, pushing or publishing.
There is no hosted deployment in this release model. Future releases use reviewed
allowlist exports/patches with the same privacy, secret, synthetic, dependency/data
licensing and exact-regression checks; never copy or mirror private Git history.
Users maintain their own local runtime/OS and explicit provider credentials/data.
