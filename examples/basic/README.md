# Basic Example

This example shows a minimal usage of planning-with-files-governed for a single-task project.

## Setup

```bash
# Initialize a session
python3 scripts/init-session.py \
  --project-root /path/to/project \
  --task-id example-task \
  --governance-level L1
```

## What Gets Created

```
/path/to/project/
  00.项目规划与治理/
    task-index.yaml
    example-task/
      task_plan.md          <- The file shown below
      1_master_plan.md
      3_status_update.md
      4_handoff.md
      WORKFLOW_CHECKLIST.md
```

## Files

- [`task_plan.md`](task_plan.md) - Example plan file for a simple feature task.
- [`task-index.yaml`](task-index.yaml) - Example task index.

## Workflow

1. `init-session.py` creates the structure.
2. Edit `task_plan.md` with your task details.
3. Work through phases, creating checkpoints as you go.
4. Run `plan-doctor.py` to validate.
5. Attest the plan with `attest-plan.py`.
