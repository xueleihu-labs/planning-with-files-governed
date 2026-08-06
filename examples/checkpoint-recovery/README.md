# Checkpoint Recovery Example

This example demonstrates recovering a task after a session reset (`/clear`), session restart, or unexpected interruption using the checkpoint-based recovery mechanism.

## Scenario

An agent was working on a task and had completed Phase 1 and Phase 2. The session was cleared (`/clear`). The agent needs to resume from the last verified checkpoint without losing progress.

## Recovery Flow

```
1. Session cleared / restarted
         |
2. init-session.py --resume
         |
3. Read task-index.yaml  ──>  Discover task
         |
4. Read latest checkpoint (cp-002)
         |
5. Verify SHA-256 chain:
   - cp-002 predecessor hash == cp-001 hash?  YES
   - cp-001 is genesis?  YES
         |
6. Read task_plan.md and 3_status_update.md
         |
7. Run plan-doctor.py  ──>  Validate consistency
         |
8. Resume from last verified checkpoint
```

## Files

- [`recovery-walkthrough.md`](recovery-walkthrough.md) - Step-by-step recovery walkthrough.
- [`checkpoint-chain-example.json`](checkpoint-chain-example.json) - Example checkpoint chain showing two checkpoints.

## Quick Recovery Command

```bash
# After /clear or session restart, resume from the last checkpoint
python3 scripts/init-session.py \
  --project-root /path/to/project \
  --task-id database-migration-task \
  --resume

# Verify state integrity
python3 scripts/plan-doctor.py \
  --project-root /path/to/project \
  --task-id database-migration-task \
  --verbose

# Verify the checkpoint chain
python3 scripts/attest-plan.py \
  --project-root /path/to/project \
  --task-id database-migration-task \
  --plan-file task_plan.md \
  --verify
```

## What Happens During Recovery

1. **Task discovery**: `task-index.yaml` is read to find the task.
2. **Checkpoint discovery**: the latest checkpoint in `checkpoints/` is located.
3. **Chain verification**: each checkpoint's predecessor hash is verified back to the genesis checkpoint.
4. **Plan state loading**: `task_plan.md` and `3_status_update.md` are read to reconstruct the current plan state.
5. **Consistency check**: `plan-doctor.py` validates that the checkpoint, plan file, and status update are consistent.
6. **Session restoration**: the verified state is loaded into the current session.
7. **Write lock acquisition**: a new write lock is acquired for the current device.

## What If Recovery Fails?

If any verification step fails:

- **Checkpoint hash mismatch**: the checkpoint chain may be corrupted. Do not proceed. Investigate which checkpoint was modified.
- **Missing checkpoint**: a checkpoint referenced as a predecessor does not exist. The chain is broken.
- **Stale plan**: the plan file was modified after the last checkpoint without creating a new checkpoint. Manual review is needed.
- **Write lock held**: another device or session holds the write lock. Wait for it to be released or use the handoff protocol.

In all failure cases, the system refuses to resume and reports the specific failure. Never force recovery past a verification failure.
