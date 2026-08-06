# Architecture Overview

This document provides a high-level architecture overview of planning-with-files-governed.

---

## System Layers

```
┌─────────────────────────────────────────────────────┐
│                  User / Agent                        │
│              (CLI commands, hooks)                   │
├─────────────────────────────────────────────────────┤
│                Scripts Layer                         │
│  init-session · plan-doctor · attest-plan ·          │
│  inject-plan · checkpoint · resolve-plan-dir         │
├─────────────────────────────────────────────────────┤
│              Templates Layer                         │
│  task_plan.md · 1_master_plan.md ·                   │
│  3_status_update.md · 4_handoff.md ·                 │
│  WORKFLOW_CHECKLIST.md                               │
├─────────────────────────────────────────────────────┤
│               Schemas Layer                          │
│  8 JSON schemas for workflow contracts               │
│  (checkpoint, handoff, task-index, plan, etc.)       │
├─────────────────────────────────────────────────────┤
│              Config Layer                            │
│  PWF_ROOT · PWF_GOVERNANCE_LEVEL · PWF_LAYOUT        │
│  governance profiles · layout versions               │
├─────────────────────────────────────────────────────┤
│               Tests Layer                            │
│  unit tests · integration tests ·                    │
│  schema validation · privacy scan · path safety      │
├─────────────────────────────────────────────────────┤
│            File System (Disk)                        │
│  00.项目规划与治理/<task-id>/*                        │
│  Plan files · checkpoints · evidence · locks         │
└─────────────────────────────────────────────────────┘
```

---

## Scripts Layer

The scripts layer provides the runtime entry points. All scripts are pure Python (standard library only, no external dependencies).

### Entry Points

| Script | Purpose |
|--------|---------|
| `init-session.py` | Initialize or resume a planning session |
| `resolve-plan-dir.py` | Resolve the plan directory for a project/task |
| `plan-doctor.py` | Validate plan structure, checkpoint integrity, and handoff consistency |
| `attest-plan.py` | Compute and verify SHA-256 attestations for plan files |
| `inject-plan.py` | Inject plan state into hook contexts (userprompt, pretool, precompact) |
| `checkpoint.py` | Create immutable checkpoints with predecessor chain |
| `validate-schemas.py` | Validate all JSON schemas in the schemas directory |
| `migrate-fields.py` | Migrate field names from production edition to Community Edition |

### Design Principles

- **Stateless scripts**: each script reads from disk, performs its operation, and writes to disk. No in-memory state persists between invocations.
- **Single responsibility**: each script handles one concern.
- **Disk is authority**: no script trusts in-memory or chat-based state; all state is read from and written to disk files.
- **Path safety**: all file operations are confined to the project root via path resolution and validation.

---

## Templates Layer

Templates provide the initial structure for plan files. They are Markdown files with placeholder sections that the agent fills in during planning.

### Template Files

| Template | Purpose |
|----------|---------|
| `task_plan.md` | The authoritative plan file (single source of truth) |
| `1_master_plan.md` | Master plan with phases, dependencies, and done criteria |
| `3_status_update.md` | Current status and progress tracking |
| `4_handoff.md` | Cross-device handoff record |
| `WORKFLOW_CHECKLIST.md` | Phase-by-phase checklist |

### Template Philosophy

- Templates are **starting points**, not rigid forms. Agents adapt them to the task.
- The `task_plan.md` template establishes the single-source-of-truth contract.
- All templates use plain Markdown for maximum portability across devices and editors.

---

## Schemas Layer

JSON schemas define the structural contracts for machine-readable workflow data.

### Schemas

| Schema | Purpose |
|--------|---------|
| `checkpoint.schema.json` | Checkpoint file structure |
| `handoff.schema.json` | Handoff record structure |
| `task-index.schema.json` | Task discovery index structure |
| `plan-attestation.schema.json` | SHA-256 attestation structure |
| `session-state.schema.json` | Session state structure |
| `governance-profile.schema.json` | Governance profile configuration |
| `write-lock.schema.json` | Write lock state structure |
| `recovery-point.schema.json` | Recovery point structure |

### Schema Validation

- Schemas are validated at runtime by `plan-doctor.py` and `validate-schemas.py`.
- Schema validation ensures **structural correctness** but not content trustworthiness (see [SECURITY.md](../SECURITY.md)).
- All schemas use JSON Schema Draft 2020-12.

---

## Config Layer

Configuration is primarily via environment variables and CLI flags. No configuration file is required.

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PWF_ROOT` | Project root | Root directory for planning files |
| `PWF_GOVERNANCE_LEVEL` | `L1` | Default governance level (L0–L3) |
| `PWF_LAYOUT` | `v3` | Layout version |

### Governance Profiles

Governance profiles control checkpoint frequency, attestation requirements, and validation strictness. See [governance-levels.md](governance-levels.md) for details.

---

## Tests Layer

The test suite ensures correctness and prevents regressions.

### Test Categories

| Category | Description |
|----------|-------------|
| Unit tests | Test individual script functions in isolation |
| Integration tests | Test script interactions with file system |
| Schema validation tests | Ensure schemas are valid and enforce constraints |
| Path safety tests | Verify path traversal prevention |
| Privacy scan tests | Check for secret patterns in files |
| Checkpoint integrity tests | Verify SHA-256 chain validation |
| Handoff tests | Test cross-platform handoff protocol |
| Recovery tests | Test session catchup and checkpoint recovery |

### Running Tests

```bash
python3 -m pytest tests/ -q
python3 scripts/validate-schemas.py
```

---

## File System (Disk)

The file system is the persistent state store. All state lives in the project's planning directory.

### Directory Structure (Layout v3)

```
/path/to/project/
  00.项目规划与治理/
    task-index.yaml              # Task discovery index
    <task-id>/
      task_plan.md               # Authoritative plan (single source of truth)
      1_master_plan.md           # Master plan
      3_status_update.md         # Status tracking
      4_handoff.md               # Handoff record
      WORKFLOW_CHECKLIST.md      # Phase checklist
      checkpoints/               # Immutable checkpoint files
        cp-001.json
        cp-002.json
        ...
      evidence/                  # Validation evidence
      .write-lock                # Write lock (device-local, not committed)
```

### State Trust Hierarchy

```
Trusted checkpoint > Actual test/verification > Git state > Disk plan file > Report/receipt > Chat description
```

The disk plan file is the authority for task state. Checkpoints are the authority for verified state transitions. Chat descriptions are never trusted as state.

---

## Data Flow

```
1. init-session.py
   └──> Creates Layout v3 structure from templates
   └──> Writes task-index.yaml entry
   └──> Acquires write lock

2. Agent works on task
   └──> Updates task_plan.md, 3_status_update.md
   └──> Creates checkpoints via checkpoint.py
       └──> Each checkpoint references predecessor by SHA-256
   └──> inject-plan.py feeds state to hooks

3. plan-doctor.py
   └──> Validates plan structure
   └──> Verifies checkpoint chain integrity
   └──> Checks task-index consistency
   └──> Reports diagnostics

4. attest-plan.py
   └──> Computes SHA-256 of plan file
   └──> Records attestation

5. Handoff (cross-device)
   └──> checkpoint.py --handoff creates 4_handoff.md
   └──> Write lock released
   └──> Receiving device verifies and resumes
```

