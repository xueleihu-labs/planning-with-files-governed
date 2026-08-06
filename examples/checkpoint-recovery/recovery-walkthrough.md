# Recovery Walkthrough: database-migration-task

> Step-by-step example of recovering a task after `/clear`.
> <!-- 中文注释：/clear 后的逐步恢复示例。 -->

## Before Recovery: Task State

The task `database-migration-task` was at Phase 2 (Implementation) when the session was cleared:

- **Phase 1 (Design)**: Completed and verified. Checkpoint `cp-001` created.
- **Phase 2 (Implementation)**: In progress. Checkpoint `cp-002` created at 60% completion.
- **Phase 3 (Testing)**: Not started.
- **Phase 4 (Deployment)**: Not started.

## Step 1: Session Was Cleared

The agent's session was cleared via `/clear`. All in-memory state is lost. The disk state is intact.

## Step 2: Resume

```bash
python3 scripts/init-session.py \
  --project-root /path/to/project \
  --task-id database-migration-task \
  --resume
```

## Step 3: System Reads task-index.yaml

```yaml
# /path/to/project/00.项目规划与治理/task-index.yaml
tasks:
  - task_id: database-migration-task
    path: database-migration-task/
```

Task discovered: `database-migration-task`.

## Step 4: System Finds Latest Checkpoint

```
/path/to/project/00.项目规划与治理/database-migration-task/checkpoints/
  cp-001.json   <- Genesis checkpoint (Phase 1 complete)
  cp-002.json   <- Latest checkpoint (Phase 2, 60% complete)
```

Latest checkpoint: `cp-002`.

## Step 5: Chain Verification

The system verifies the checkpoint chain:

```
cp-002:
  predecessor.checkpoint_id = "cp-001"
  predecessor.hash = "1a2b3c..."
  
  Verify: SHA-256(cp-001) == "1a2b3c..."  ✓ MATCH

cp-001:
  predecessor = null (genesis checkpoint)
  
  Verify: genesis checkpoint has no predecessor  ✓ OK
```

Chain verification: **PASSED**.

## Step 6: Plan State Loading

The system reads:

- `task_plan.md` - the authoritative plan with all phases and done criteria.
- `3_status_update.md` - current status showing Phase 2 at 60%.

## Step 7: Plan Doctor Validation

```bash
python3 scripts/plan-doctor.py \
  --project-root /path/to/project \
  --task-id database-migration-task \
  --verbose
```

Output (example):

```
Plan Doctor Report
==================
Task: database-migration-task
Governance Level: L2 (STANDARD)

Checks:
  [PASS] Plan file exists and is well-formed
  [PASS] Checkpoint chain intact (2 checkpoints verified)
  [PASS] No gaps in checkpoint sequence
  [PASS] task-index.yaml consistent with on-disk directories
  [PASS] No handoff in progress
  [PASS] Plan file hash matches latest checkpoint attestation
  [PASS] Status update consistent with latest checkpoint

Summary: 7 checks passed, 0 warnings, 0 errors
```

## Step 8: Resume Work

The agent now has full context:

- Knows it is at Phase 2 (Implementation) at 60%.
- Knows what was completed in Phase 1.
- Knows what remains in Phase 2.
- Knows the next steps.
- Has a verified checkpoint chain for audit trail.

The agent continues working from the last verified state, creating `cp-003` when Phase 2 is complete.

## Key Principle

> **The disk state is the authority. Chat memory is never trusted for state recovery.**
> <!-- 中文注释：磁盘状态是权威，聊天记忆不可信。 -->

The agent does not need to "remember" what it was doing. It reads the verified state from disk and resumes. This ensures consistency across session clears, device switches, and agent restarts.
