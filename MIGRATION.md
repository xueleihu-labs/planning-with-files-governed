# Migration Guide

This guide covers migrating to planning-with-files-governed Community Edition from the upstream original or from the production edition.

---

## Table of Contents

- [Before You Begin](#before-you-begin)
- [Migrating from Upstream Original](#migrating-from-upstream-original)
- [Migrating from Production Edition](#migrating-from-production-edition)
- [Field Rename Mapping](#field-rename-mapping)
- [Unsupported Internal Profiles](#unsupported-internal-profiles)
- [Important Warnings](#important-warnings)

---

## Before You Begin

> **Always backup your project before initializing or migrating.**

```bash
# Create a backup of your project planning directory
cp -r /path/to/project/00.项目规划与治理 /path/to/project/00.项目规划与治理.backup.$(date +%Y%m%d)
```

Migration is a one-way operation. Once you adopt the Community Edition field names and layout, reverting requires manual restoration from backup.

---

## Migrating from Upstream Original

If you are using the upstream [OthmanAdi/planning-with-files](https://github.com/OthmanAdi/planning-with-files) (v3.8.1 or earlier):

### 1. Install the Community Edition

```bash
git clone https://github.com/xueleihu-labs/planning-with-files-governed.git
cd planning-with-files-governed
```

### 2. Initialize the Community Edition in your project

```bash
python3 scripts/init-session.py \
  --project-root /path/to/project \
  --task-id <your-task-id> \
  --governance-level L1
```

### 3. Migrate existing plan files

Copy your existing plan content into the new Layout v3 structure. The key difference is that the Community Edition uses a project-local planning directory (`00.项目规划与治理/<task-id>/`) rather than a global or external planning directory.

### 4. Update references

- Replace any global planning root references with `PWF_ROOT`.
- Update hook configurations to use the Community Edition's smart injection hooks.
- Run `plan-doctor.py` to validate the migrated structure.

### Notes

- The upstream original does not have governance levels, checkpoints, or attestation. These are new features you adopt by migrating.
- Existing plan content is compatible at the Markdown level; structural validation is new.

---

## Migrating from Production Edition

If you are using the production edition of planning-with-files (internal version v1.2.0):

### 1. Review field renames

See the [Field Rename Mapping](#field-rename-mapping) table below. The Community Edition uses genericized names.

### 2. Update environment variables

```bash
# Old
export LOBSTER_ROOT=/path/to/planning

# New
export PWF_ROOT=/path/to/planning
```

### 3. Update script invocations

```bash
# Old
python3 scripts/init-session.py --lobster-root /path/to/project --task-id <id>

# New
python3 scripts/init-session.py --project-root /path/to/project --task-id <id>
# or
python3 scripts/init-session.py --skill-root /path/to/project --task-id <id>
```

### 4. Run the migration helper (if available)

```bash
python3 scripts/migrate-fields.py --project-root /path/to/project --dry-run
# Review the output, then:
python3 scripts/migrate-fields.py --project-root /path/to/project
```

### 5. Validate

```bash
python3 scripts/plan-doctor.py --project-root /path/to/project --task-id <id> --verbose
```

---

## Field Rename Mapping

The following table maps production edition field names to Community Edition names:

| Production Edition | Community Edition | Notes |
|-------------------|-------------------|-------|
| `LOBSTER_ROOT` | `PWF_ROOT` | Environment variable for planning root |
| `--lobster-root` | `--skill-root` (or `--project-root`) | CLI flag for root directory |
| `fuxi_read_head` | `external_read_head` | Read compatibility: old field is accepted on read, new field is emitted on write |
| `fuxi_risk_route` | `governance_route` | Governance routing field |
| `fuxi-orchestrator` | `orchestrator` | Orchestrator reference |
| `伏羲总指挥` | `Project Owner` | Role title |

### Backward-Compatible Read for `fuxi_read_head`

The Community Edition maintains **read compatibility** for `fuxi_read_head`:

- **Reading**: if a file contains `fuxi_read_head`, the system reads it as `external_read_head`.
- **Writing**: the system always writes `external_read_head` (never `fuxi_read_head`).

This means existing files with `fuxi_read_head` will be read correctly, but any new writes or updates will use the new field name. Over time, files will naturally migrate to the new name as they are updated.

---

## Unsupported Internal Profiles

The following internal profiles from the production edition are **not supported** in the Community Edition:

| Profile | Status | Replacement |
|---------|--------|-------------|
| `lobster-work` | Removed | Use governance levels (L0–L3) instead |
| Personal rule profiles | Removed | Not applicable in Community Edition |
| Internal acceptance reports | Removed | Use `plan-doctor.py` and evidence directory |
| Internal references | Removed | Genericized or removed |

If your production edition configuration references any of these, you must remove or replace them before the Community Edition will function correctly.

---

## Important Warnings

### Community Edition and Production Edition Cannot Be Directly Interchanged

The Community Edition and production edition are **not directly interchangeable**:

- **Different field names**: the Community Edition uses genericized names (see mapping above).
- **Different profiles**: internal profiles are removed from the Community Edition.
- **Different paths**: personal and internal paths are removed from the Community Edition.
- **Different scope**: the Community Edition excludes personal rule profiles, internal reports, and internal terminology.

Running the production edition on Community Edition files (or vice versa) without migration will produce errors or unexpected behavior.

### Always Backup Before Initializing

```bash
# Always create a backup before running init-session.py on an existing project
cp -r /path/to/project/00.项目规划与治理 /path/to/project/00.项目规划与治理.backup.$(date +%Y%m%d)
```

### Migration Is One-Way

Once you adopt the Community Edition field names and layout, reverting to the production edition requires manual restoration from backup. The backward-compatible read for `fuxi_read_head` helps with the transition, but new files will always use `external_read_head`.

### Validate After Migration

Always run `plan-doctor.py` after migration to ensure structural integrity:

```bash
python3 scripts/plan-doctor.py \
  --project-root /path/to/project \
  --task-id <task-id> \
  --verbose
```

