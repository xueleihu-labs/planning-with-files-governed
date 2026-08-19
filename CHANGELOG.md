# Changelog

All notable changes to planning-with-files-governed are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0-rc.2] - 2026-08-19

### Added

- Human-readable project progress Excel sheet generator (`pwf progress --excel` and `pwf export-excel`).
- 4-Sheet architecture:
  - `00_老板记录` (USER-MANAGED protected sheet for boss notes/reminders, never overwritten).
  - `01_项目总览` (SYSTEM-MANAGED dashboard with metadata banner, status colors, and human summaries).
  - `02_阶段与步骤明细` (SYSTEM-MANAGED phase and step breakdown with Done criteria and evidence).
  - `03_决策与待办` (HYBRID with system decisions and preserved user-managed decision columns).
- Zero-dependency OpenXML (.xlsx) builder and parser using Python standard library.
- Fail-closed user data preservation: existing boss records and user columns are 100% preserved across refreshes; corrupted files are preserved without overwrite.
- Decoupled lifecycle: formal checkpoints succeed independently of auxiliary Excel view refresh.

### Fixed

- Native Windows PowerShell wrappers now probe Python executables and avoid broken `python3` App Execution Aliases.
- Windows project-init locks now use a native process-existence probe and normalized lock-root comparison, preserving active locks fail-closed.
- Runtime adapter tests now execute the native PowerShell wrappers on Windows and isolate local runtime artifacts from public-tree scans.

## [2.0.0-rc.1] - 2026-08-08

### Added

- First v2 release candidate for the Community Edition.
- Modular v2 architecture.
- Installable `pwf` CLI.

### Changed

- Preserved legacy Plan, checkpoint, and handoff compatibility.
- Adopted a canonical shared-core architecture.
- Supports Python `>=3.10`.

### Notes

- Python package version: `2.0.0rc1`.
- GitHub release/tag: `v2.0.0-rc.1`.
- This is a pre-release candidate for `v2.0.0`; the final release is not included.
- Use one PWF distribution per Python environment.

## 1.0.1 - 2026-08-06

### Changed
- Raised minimum Python version to 3.10 (Python 3.9 reached end-of-life on 2025-10-31; CI already covers 3.10–3.13)
- CI now runs `plan-doctor doctor --planning-dir examples/basic --task-id example-task` without `|| true`, so the example validation is a real gate instead of a silently skipped step

## 1.0.0 - 2026-08-06

### Added
- Community Edition release based on production v1.2.0
- L0-L3 governance profiles (LIGHT_FAST, LIGHT_CONTROLLED, STANDARD, STRICT)
- Layout v3 with project-local task planning
- Checkpoint consumption and recovery
- SHA-256 plan attestation
- Smart hook injection (userprompt, pretool, precompact)
- Session catchup for recovery
- Plan doctor diagnostics
- Cross-platform support (macOS, Windows, WSL)
- 8 JSON schemas for workflow contracts
- Comprehensive test suite

### Changed
- Renamed LOBSTER_ROOT to PWF_ROOT
- Renamed fuxi_read_head to external_read_head (with backward-compatible read)
- Renamed fuxi_risk_route to governance_route
- Genericized all paths and terminology
- Removed personal rule profile system

### Removed
- Personal rule profiles (lobster-work)
- Internal acceptance reports
- Internal references and terminology
