# Audited local source update — publication checklist

This checklist describes preparation of a subsequent reviewed update to an existing
local public repository. No remote, push, release publication, or deployment is authorized.
Existing public history contains two commits; this update appends a third commit and
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
- [ ] Windows x64: support docs retain prior native verification; not rerun here.
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

- [x] Full private compatibility and clean exported public suites: 2,299 Python tests passed, 465 Node tests passed;
  all acceptance scenarios and sealed references match without oracle refresh or skips.
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
`f08ca10ced2304bc63cbc22103fcf7379f53045540fa2cd91a244d88bab2bdc0`.

Manifests (`PUBLIC_EXPORT_MANIFEST.json`, `SOFTWARE_MANIFEST.json`) record sanitized file
metadata, runtime requirements, and dependency lock hashes without private paths or IDs.

## Public Git history gate

The existing public repository has two commits. This update appends exactly one new
commit (the third commit) on `main`. Never amend, reset, rebase, or reinitialize.
Clean worktree and fsck required; approved author/committer identity; every object scanned;
no inherited private refs, objects, hooks, or alternates; no remote and no tags.

## Known pre-existing UI limitations

The result-view roof controls remain hidden, and applying a sizing recommendation
does not immediately refresh those controls. Roof edits remain available through
onboarding. These issues predate this update; they are follow-up usability work.

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
