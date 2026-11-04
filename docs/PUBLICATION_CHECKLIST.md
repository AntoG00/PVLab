# Audited local source update — publication checklist

This checklist describes preparation of a subsequent reviewed update to an existing
local public repository. No remote, push, release publication, or deployment is authorized.
Existing public history contains seven commits; this update appends an eighth commit and
must never amend, reset, rebase, or reinitialize public history.

## Release policy

- [x] Source-code-only distribution; users run their own local Python server.
- [x] Source-available original code: unmodified PolyForm Noncommercial 1.0.0; LICENSE
  and NOTICE preserved. Commercial use requires separate explicit written permission.
- [x] Third-party code/data terms separate; THIRD_PARTY_NOTICES.md retained. No real
  provider archives, manufacturer tables, private inputs, dependency binaries, or native bundles.
- [x] Loopback default (127.0.0.1); optional trusted-LAN binding. Session isolation,
  CSRF/Host/Origin checks, owned deletion, and bounded workers retained.
- [x] Hosted operator/contact and hosting region: not applicable to this release.
- [x] Fiscal status: multiple-LLM review without independent specialist validation
  (FISCAL_STATUS.md). No current legal correctness claim; non-blocking for code-only.

## Platform validation and environment compatibility

- [x] Linux (x86_64) verification current for the application and tests.
- [x] Windows x64: native CPython 3.12.16 verification passed with four test workers.
  Python: 2,310 passed plus 268 subtests; Node: 466 passed; acceptance, lifecycle smoke,
  and exact two/seven-plane synthetic-reference comparisons passed. The earlier
  64-difference failure is retained as a historical receipt, now resolved.
  Higher-concurrency runs had intermittent startup/cleanup failures; no application
  limits or assertions were widened.
- [ ] macOS (Apple Silicon arm64): native platform validation remains pending.
- [x] Environment metadata: owner approved a separate current kernel/tzdata compatibility
  manifest with unchanged sealed inputs/oracle and exact comparator, after original
  metadata gate rejected host environment mismatch.

## Verification evidence

Superseded initial export baseline (Gate 20/Phase 21): Python 1,990/2,004 passed, Node
352 passed, wheel 61 entries, sdist 74 files; acceptance passed.

Pre-update Python/Node/acceptance checks: Python 2,286 passed, Node 464 passed, acceptance passed.
The initial metadata-only private gate rejection is retained; the separately approved
compatibility gate pins the current environment and compares the unchanged sealed oracle.

- [x] Full clean public suite: 2,301 Python tests passed, 466 Node tests passed;
  Linux acceptance and sealed/synthetic references match without oracle refresh or skips.
  Exact Windows synthetic-reference parity is resolved without an oracle refresh or waiver.
- [x] Payload bytes/hashes, relative paths, and POSIX modes audited against committed
  reviewed source allowlist; no private Git objects, private filesystem paths, or raw inputs.
- [x] Strict export privacy and secret scans: zero findings; no legacy exemptions.
- [x] Package build verification: wheel contains 66 entries, source archive
  contains 79 files; archive members match reviewed source without bundled binaries.

- [x] Four hash-locked dependency inventories audited: no known advisories. Separate
  third-party notices preserved, including the newly required timezone package.
- [x] Installed production wheel: actual default loopback socket, three-session
  isolation, explicit pairing, owned deletion, assets, no capability logs and clean SIGINT.
- [x] Seven exact approved synthetic media assets inspected: no EXIF, PNG chunks
  limited to IHDR/IDAT/IEND; JPEG retains only standard sRGB/JFIF metadata.

Reviewed source inventory SHA256 (`reviewed_source_sha256`):
`4bcee07194be53dd9f7d003ff2218bd8fe901cde61c9f190181c02b6310c0d89`.

Manifests (`PUBLIC_EXPORT_MANIFEST.json`, `SOFTWARE_MANIFEST.json`) record sanitized file
metadata, runtime requirements, and dependency lock hashes without private paths or IDs.

## Public Git history gate

The existing public repository has seven commits. This update appends exactly one new
commit (the eighth commit) on `main`. Never amend, reset, rebase, or reinitialize.
Clean worktree and fsck required; approved author/committer identity; every object scanned;
no inherited private refs, objects, hooks, or alternates; no remote and no tags.

## Roof-control verification

Profile roof controls become visible when populated and clear/hide without an active
profile. Applying a matching sizing recommendation refreshes the displayed plane
counts; stale revisions remain rejected. Focused UI checks and the full Linux
public gate passed. The unchanged Python backend also passed the exact private
cold/warm comparison; no numerical expectation or security protection changed.

## 2026-11-04 Windows parity verification and synchronized candidate

The verified parity fix is committed and synchronized into the public export candidate,
appending a fifth public commit while preserving all four prior commits. No remote,
push, publication, or deployment is authorized. Confirmed diagnosis: 21 of 731 original synthetic
analytic cosine daily samples differed in the last bit under Windows UCRT vs Linux glibc.
Across 64 snapshot differences, 24 were floats (largest absolute delta 2.2737367544323206e-13 kWh)
and 40 were hash/fingerprint fields; no recorded financial numeric value changed. The fixed
generator freezes the original 731 float.hex values, preserving every Linux input array bit,
with reference.json unchanged. The compiler now writes canonical NPZ ZipInfo metadata
(create_system=3, permissions 0o600<<16), preserving exact original Linux archive bytes where
Windows previously set create_system=0 on 10 entries. No production numerical equation,
tolerance, expected output, hash validation, or worker/security limit changed. The unchanged
full exact two/seven-plane comparison now passes on Linux and native Windows. The full
Linux gate passed (2,301 Python and 466 Node tests, acceptance, exact public reference),
and the sealed private cold/warm comparison passed exactly. The full native Windows gate
passed with four test workers: 2,310 Python tests plus 268 subtests, 466 Node tests,
acceptance and lifecycle smoke checks; no skips. This clears the exact-parity blocker
for the synchronized candidate; exact owner approval remains required before any future push.

## Future update procedure and owner approval

Subsequent updates must follow this reviewed sequence:
1. Committed allowlist files: Stage and review changes in source repository allowlist.
2. New empty temp snapshot: Export committed allowlist files into an isolated directory.
3. Audit and test: Run privacy/secret scans, clean package builds, and full test suites.
4. Reviewed synchronization: Synchronize reviewed files into the public repository
   worktree while strictly preserving existing public `.git` directory and commit history.
5. Append public commit: Record changes as a single new commit appended to history.
6. Stop for owner approval: Stop before any remote creation, push, or deployment.

Explicit owner review and approval of the exact commit ID and manifest SHA256 are
required before any remote push or distribution. Users maintain their local runtime/OS.
