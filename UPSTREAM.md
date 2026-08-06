# Upstream Attribution and Derivation Record

## Upstream Repository

- **Repository**: [OthmanAdi/planning-with-files](https://github.com/OthmanAdi/planning-with-files)
- **Tag**: `v3.8.1`
- **Commit**: `117dfae83eefb0d4f0f5824252fd833dcde13459`
- **Tag type**: Lightweight tag, verified via `git ls-remote --tags`
- **Derivation date**: 2026-07-30

## Community Edition Source

This Community Edition is derived from the production edition of planning-with-files (internal production version `v1.2.0`). The production edition was itself derived from upstream `v3.8.1` with significant governance extensions.

## Selective Backport Strategy

This repository is an independent derivative. It is not an official distribution of the upstream project.

Upstream changes are evaluated selectively under the [Compatibility Policy](README.md#compatibility-policy). A change is backported only when it preserves:

1. The single-source-of-truth model.
2. Deterministic plan and checkpoint contracts.
3. Cross-platform path safety.
4. Recoverable and verifiable handoffs.
5. Backward-compatible data migration where practical.
6. The Community Edition security and test gates.

### Backport Record Format

Every accepted backport must record:

```
- Source commit: <sha>
- Upstream tag/branch: <ref>
- Affected files: <list>
- Compatibility impact: <assessment>
- Validation evidence: <test results / plan-doctor output>
- Backport date: <YYYY-MM-DD>
- Backported by: <contributor>
```

## Relationship to Upstream

| Aspect | Upstream (planning-with-files) | This Derivative (planning-with-files-governed) |
|--------|-------------------------------|------------------------------------------------|
| State model | File-based planning | File-based planning + immutable checkpoint chain |
| Governance | Not specified | L0–L3 governance profiles |
| Handoff | Not specified | Cross-platform deterministic handoff |
| Layout | Project-level | Layout v3 with project-local task planning |
| Task discovery | Not specified | Deterministic via task-index.yaml |
| Integrity | Not specified | SHA-256 plan attestation |
| Hooks | Not specified | Smart injection (userprompt, pretool, precompact) |
| Recovery | Not specified | Session catchup + checkpoint recovery |
| Diagnostics | Not specified | Plan doctor |
| Compatibility | N/A | Selective backport, not a mirror |

## Dual Copyright

```
MIT License

Copyright (c) 2026 Ahmad Adi
Copyright (c) 2026 xueleihu52-arch
```

The upstream author's original work is gratefully acknowledged. This derivative extends the file-based planning philosophy with governance, integrity, and cross-platform capabilities while maintaining the MIT license.

## Disclaimer

planning-with-files-governed is an independent, community-maintained derivative of planning-with-files. It is not affiliated with, endorsed by, or maintained by the upstream author.
