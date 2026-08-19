# Changelog

All notable changes to planning-with-files-governed are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0-rc.4] - 2026-08-20

### Added

- Canonical Project Progress Excel contract: `<PROJECT_ROOT>/项目进度表_人话版.xlsx` established as standard REQUIRED artifact across all formal PLAN lifecycles.
- Transactional migration with 5-gate equivalence checks (`CANONICAL_EXCEL_VALID`, `BOSS_LOG_DATA_EQUIVALENT`, `BOSS_DECISION_DATA_EQUIVALENT`, `FOUR_SHEETS_COMPLETE`, `RELATIONSHIPS_VALID`) from legacy `00.项目规划与治理/` paths.
- Deep `validate_required_plan_artifacts()` integrity checks validating OpenXML ZIP structure, relationships, all 4 canonical sheets, and `00_老板记录` parseability.
- Native OpenXML Freeze Panes, `A1:F1000` AutoFilter, 3 Data Validation dropdowns on `00_老板记录`, and `A1:H{max}` AutoFilter on `02_阶段与步骤明细`.
- Executive Dashboard on `01_项目总览` with consistency gates preventing false 100% completion when active steps remain.
- Latest-effective-state wins deduplication on `03_决策与待办`, separating Active from Historical decisions by stable `decision_key`.
- Read-only diagnostics in `doctor` and `verify_plan_summary` without file mutation.
- Automatic creation and refresh during formal lifecycle mutation entrypoints (`init`, `create_plan`, `record_checkpoint_ref`, `resume_from_checkpoint`, `finalize_plan`).

## [2.0.0-rc.3] - 2026-08-19

### Fixed

- Enhanced Markdown planning parser in `progress_excel`: extracted global status from `00_PROJECT_INDEX.md`, automatic task title resolution from Markdown H1 headings, task step extraction from Done Criteria checkboxes (`-[x]`), and automatic decision extraction from `3_status_update.md` into `03_决策与待办`.
- Updated security policy documentation to reflect supported pre-release line `2.0.0-rc.3`.
- Ensured consistent version metadata across packaging, documentation, schemas, and tests.

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
