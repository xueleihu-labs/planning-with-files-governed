# Cross-Platform Handoff Example

This example demonstrates handing off a task from macOS to Windows using the planning-with-files-governed handoff protocol.

## Scenario

A developer starts a task on macOS, creates a checkpoint, and hands off to a Windows machine (via WSL) to continue testing in a Windows-specific environment.

## Handoff Protocol

```
macOS (Device A)                    Windows/WSL (Device B)
─────────────────                   ──────────────────────
1. Work on task
2. Create checkpoint
   checkpoint.py --handoff
3. Write 4_handoff.md
4. Release write lock          ──>  5. Verify checkpoint hash
                              sync   6. Verify predecessor chain
                                     7. Acquire write lock
                                     8. Resume work
```

## Files

- [`4_handoff.md`](4_handoff.md) - Example handoff record from macOS to Windows.
- [`checkpoint-example.json`](checkpoint-example.json) - Example checkpoint with SHA-256 attestation.

## Step-by-Step

### On macOS (Device A)

```bash
# Initialize and work on the task
python3 scripts/init-session.py \
  --project-root /path/to/project \
  --task-id shared-api-task \
  --governance-level L2

# ... work through phases ...

# Create a handoff checkpoint
python3 scripts/checkpoint.py \
  --project-root /path/to/project \
  --task-id shared-api-task \
  --phase "implementation" \
  --status "in-progress" \
  --handoff

# Verify before releasing
python3 scripts/plan-doctor.py \
  --project-root /path/to/project \
  --task-id shared-api-task
```

### File sync occurs (Syncthing, Git, etc.)

### On Windows/WSL (Device B)

```bash
# Resume from the handoff checkpoint
python3 scripts/init-session.py \
  --project-root C:\Users\<username>\projects\example \
  --task-id shared-api-task \
  --resume

# The system will:
# 1. Read the handoff record (4_handoff.md)
# 2. Verify the checkpoint SHA-256 chain
# 3. Verify the predecessor hash
# 4. Acquire the write lock
# 5. Load the plan state into the session

# Continue work
python3 scripts/checkpoint.py \
  --project-root C:\Users\<username>\projects\example \
  --task-id shared-api-task \
  --phase "testing" \
  --status "in-progress"
```

## Important Notes

- The write lock file (`.write-lock`) is device-local and should NOT be synced.
- The handoff record (`4_handoff.md`) IS synced and contains the checkpoint hash for verification.
- Always run `plan-doctor.py` after resuming to validate state integrity.
- If the checkpoint hash does not match, do NOT proceed - investigate the discrepancy.
