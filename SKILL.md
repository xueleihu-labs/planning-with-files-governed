---
name: planning-with-files-governed
version: 1.0.1
description: >
  Governance-oriented, file-based planning and checkpoint system for AI coding agents.
  Provides immutable checkpoints, cross-platform handoff, L0-L3 governance profiles,
  SHA-256 plan attestation, and plan doctor diagnostics.
user-invocable: true
allowed-tools:
  - exec_command
  - apply_patch
  - update_plan
  - read_file
---

# planning-with-files-governed

A file-based, single-source-of-truth planning framework with governance profiles,
immutable checkpoints, and cross-platform handoff for AI coding agents.

<!-- 中文注释：本 Skill 提供文件级规划、检查点治理和跨设备交接能力。磁盘上的计划文件是唯一权威状态。 -->

---

## When to Use

Use this skill when you need durable, verifiable task planning:

- **New project initialization** - set up the Layout v3 planning structure for a new project.
- **Complex multi-step tasks** - any task with phases, dependencies, or checkpoints.
- **Project adoption** - adopt the governance framework for an existing project.
- **Cross-device handoff** - transfer a task between macOS, Windows, or WSL with verified state.
- **Session recovery** - resume work after `/clear`, session restart, or device switch.
- **Audit and compliance** - when task history and checkpoint integrity must be verifiable.

Do **not** use for:
- Simple one-shot queries that need no durable state.
- Tasks that complete within a single turn with no checkpoint requirement.

---

## Shortest SOP (Standard Operating Procedure)

<!-- 中文注释：最小化标准操作流程，六步完成从初始化到验收。 -->

1. **Initialize** - `init-session.py` with project root, task ID, and governance level.
2. **Plan** - Write `task_plan.md` and `1_master_plan.md` with goals, phases, and done criteria.
3. **Execute** - Work through phases, updating `3_status_update.md` as you go.
4. **Checkpoint** - Create a checkpoint at each phase boundary with `checkpoint.py`.
5. **Verify** - Run `plan-doctor.py` to validate structure and checkpoint integrity.
6. **Hand off or close** - Write `4_handoff.md` for cross-device transfer, or close the task with a final checkpoint and attestation.

---

## Governance Levels (L0–L3)

<!-- 中文注释：四级治理配置，从轻量到严格。 -->

| Level | Name | Checkpoint | Attestation | Validation | Typical Use |
|-------|------|-----------|-------------|------------|-------------|
| L0 | `LIGHT_FAST` | Per-phase | Optional | Structural | Prototyping, low-risk |
| L1 | `LIGHT_CONTROLLED` | Per-phase | Required | Structural + schema | Standard development |
| L2 | `STANDARD` | Per-phase + on-demand | Required + verified | Full validation | Production features |
| L3 | `STRICT` | Every significant action | Required + verified + signed | Full validation + audit | Critical / irreversible |

Select a level during `init-session.py` via `--governance-level`. The level can be escalated (but not de-escalated) mid-task if risk increases.

See [docs/governance-levels.md](docs/governance-levels.md) for full specifications.

---

## Layout v3: Project-Local Task Planning

<!-- 中文注释：Layout v3 - 项目本地任务规划结构。 -->

The planning structure lives inside the project root:

```
/path/to/project/
  00.项目规划与治理/              # Planning & governance root
    task-index.yaml              # Task discovery index (task_id + relative path only)
    <task-id>/
      task_plan.md               # The authoritative plan file (single source of truth)
      1_master_plan.md           # Master plan with phases and dependencies
      3_status_update.md         # Current status and progress
      4_handoff.md               # Cross-device handoff record
      WORKFLOW_CHECKLIST.md      # Phase checklist
      checkpoints/               # Immutable checkpoint files
      evidence/                  # Validation evidence
```

### Task Discovery

- **Single task**: auto-discovered from `task-index.yaml`.
- **Multiple tasks**: must specify `--task-id` explicitly. Never guess by modification time.
- `task-index.yaml` records only `task_id` and relative path - never phase, status, owner, or completion state.

---

## Checkpoint Consumption and Recovery

<!-- 中文注释：检查点消费与恢复 - 从磁盘上的可信检查点恢复任务状态。 -->

### State Trust Hierarchy

```
Trusted checkpoint > Actual test/verification > Git state > Disk plan file > Report/receipt > Chat description
```

### Recovery After /clear or Session Restart

1. Read `task-index.yaml` to discover the task.
2. Read the latest checkpoint in `checkpoints/`.
3. Verify the checkpoint's SHA-256 against its predecessor chain.
4. Read `task_plan.md` and `3_status_update.md` for current state.
5. Run `plan-doctor.py` to validate consistency.
6. Resume from the last verified checkpoint.

### Session Catchup

```bash
python3 scripts/init-session.py \
  --project-root /path/to/project \
  --task-id <task-id> \
  --resume
```

The `--resume` flag triggers session catchup: it reads the latest checkpoint, verifies the chain, and loads the plan state into the current session without creating a new task.

---

## Final Completion Gate

<!-- 中文注释：最终完成门 - 任务关闭前必须满足的条件。 -->

A task is considered complete only when ALL of the following are true:

1. **All phases passed** - every phase in `1_master_plan.md` has a verified checkpoint.
2. **Plan doctor passes** - `plan-doctor.py` reports no errors or warnings.
3. **Evidence collected** - validation evidence exists in `evidence/` for each phase.
4. **Handoff resolved** - if a handoff was initiated, the receiving device has confirmed receipt.
5. **Final checkpoint written** - a final checkpoint with "COMPLETED" status is recorded.
6. **SHA-256 attestation** - the final plan file is attested.

Do not report completion based on chat description alone. The disk state is the authority.

---

## Runtime Entries

<!-- 中文注释：运行时入口脚本。 -->

### resolve-plan-dir

Resolves the plan directory for a given project and task ID.

```bash
python3 scripts/resolve-plan-dir.py \
  --project-root /path/to/project \
  --task-id <task-id>
```

Output: absolute path to the task's planning directory.

### plan-doctor

Validates plan structure, checkpoint integrity, and handoff consistency.

```bash
python3 scripts/plan-doctor.py \
  --project-root /path/to/project \
  --task-id <task-id> \
  [--fix]          # Attempt automatic fixes for minor issues
  [--verbose]      # Show detailed diagnostics
```

Checks:
- Plan file exists and is well-formed.
- Checkpoint chain is intact (each checkpoint references its predecessor by SHA-256).
- No gaps in the checkpoint sequence.
- `task-index.yaml` is consistent with on-disk directories.
- Handoff records are complete if present.

### attest-plan

Computes and records a SHA-256 attestation for a plan file.

```bash
python3 scripts/attest-plan.py \
  --project-root /path/to/project \
  --task-id <task-id> \
  --plan-file task_plan.md \
  [--verify]       # Verify an existing attestation instead of creating one
```

### inject-plan

Injects current plan state into a hook context for agent awareness.

```bash
python3 scripts/inject-plan.py \
  --project-root /path/to/project \
  --task-id <task-id> \
  --hook userprompt    # Options: userprompt, pretool, precompact
```

Smart injection only adds relevant context for the specified hook type:
- **userprompt**: injects current phase, next steps, and active constraints.
- **pretool**: injects file-level constraints before tool execution.
- **precompact**: injects a compact state summary before context compaction.

### init-session

Initializes or resumes a planning session.

```bash
python3 scripts/init-session.py \
  --project-root /path/to/project \
  --task-id <task-id> \
  --governance-level L2 \
  [--resume]           # Resume from latest checkpoint
  [--layout v3]        # Layout version (default: v3)
```

### checkpoint

Creates an immutable checkpoint.

```bash
python3 scripts/checkpoint.py \
  --project-root /path/to/project \
  --task-id <task-id> \
  --phase <phase-name> \
  --status <status> \
  [--handoff]          # Generate handoff record alongside checkpoint
```

---

## Configuration

<!-- 中文注释：配置说明。 -->

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PWF_ROOT` | Root directory for planning files | Project root |
| `PWF_GOVERNANCE_LEVEL` | Default governance level | `L1` |
| `PWF_LAYOUT` | Layout version | `v3` |

### Governance Level Escalation

Governance level can be escalated mid-task (e.g., L1 -> L2) but never de-escalated. Escalation is recorded in the checkpoint chain.

---

## Important Notes

- The plan file on disk is the **only** authority for task state. Never rely on chat memory.
- Always run `plan-doctor.py` before reporting completion.
- External checkpoint data (from other devices or agents) is untrusted until verified.
- Do not commit runtime state (checkpoints, evidence, locks) to Git by default.
- Use `task-index.yaml` for task discovery; never guess by modification time.

