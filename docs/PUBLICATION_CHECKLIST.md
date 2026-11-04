# Audited local source update — publication checklist

This checklist describes preparation of a subsequent reviewed update to an existing
local public repository. No remote, push, release publication, or deployment is authorized.
Existing public history contains three commits; this update appends a fourth commit and
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
- [ ] Windows x64: native CPython 3.12.16 verification ran on this update.
  Python: 2,308 passed plus 268 subtests; Node: 466 passed; acceptance passed.
  Exact public synthetic-reference comparison FAILED; expected values were preserved.
  Windows exact numerical parity is an unresolved release limitation.
  Full Python passed with four test workers; higher-concurrency runs had intermittent
  startup/cleanup failures. No application limits or assertions were widened.
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

- [x] Full clean public suite: 2,299 Python tests passed, 466 Node tests passed;
  Linux acceptance and sealed/synthetic references match without oracle refresh or skips.
  The separate Windows exact-reference failure above is not waived.
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
`48e943697c448147fca454d0a22d923c36571ce7f0e95838ca6f964104ed1471`.

Manifests (`PUBLIC_EXPORT_MANIFEST.json`, `SOFTWARE_MANIFEST.json`) record sanitized file
metadata, runtime requirements, and dependency lock hashes without private paths or IDs.

## Public Git history gate

The existing public repository has three commits. This update appends exactly one new
commit (the fourth commit) on `main`. Never amend, reset, rebase, or reinitialize.
Clean worktree and fsck required; approved author/committer identity; every object scanned;
no inherited private refs, objects, hooks, or alternates; no remote and no tags.

## Roof-control verification

Profile roof controls become visible when populated and clear/hide without an active
profile. Applying a matching sizing recommendation refreshes the displayed plane
counts; stale revisions remain rejected. Focused UI checks and the full Linux
public gate passed. The unchanged Python backend also passed the exact private
cold/warm comparison; no numerical expectation or security protection changed.

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
