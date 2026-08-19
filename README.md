# planning-with-files-governed v2.0.0-rc.4

A governance-oriented, file-based planning and checkpoint system for AI coding agents — with immutable checkpoints, cross-platform handoff, and L0–L3 governance profiles.

> **Non-official derivative disclaimer:** planning-with-files-governed is an independent, community-maintained derivative of planning-with-files. It is not affiliated with, endorsed by, or maintained by the upstream author.

The Python package version is `2.0.0rc4`; the GitHub release/tag is `v2.0.0-rc.4`. This is a pre-release candidate, not the final `v2.0.0` release.

---

## Table of Contents

- [Overview](#overview)
- [Upstream Attribution](#upstream-attribution)
- [Key Differences from Upstream](#key-differences-from-upstream)
- [Compatibility Policy](#compatibility-policy)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Governance Levels (L0–L3)](#governance-levels-l0l3)
- [Cross-Platform Handoff](#cross-platform-handoff)
- [Threat Model](#threat-model)
- [Security](#security)
- [Current Limitations](#current-limitations)
- [中文摘要](#中文摘要)

---

## Overview

planning-with-files-governed (PWF-Governed) provides a file-based, single-source-of-truth planning framework for AI coding agents. It replaces in-memory or chat-based task tracking with durable, verifiable plan files, immutable checkpoints, and a deterministic handoff protocol that works across macOS, Windows, and WSL.

The core principle is simple: **the plan file on disk is the only authority.** Every phase transition, checkpoint, and handoff is recorded as a file with a SHA-256 attestation, so any agent or device can resume from a known-good state without trusting chat history.

---

## Upstream Attribution

This project is derived from [OthmanAdi/planning-with-files](https://github.com/OthmanAdi/planning-with-files), tag `v3.8.1`, commit `117dfae83eefb0d4f0f5824252fd833dcde13459`.

### Dual Copyright

```
MIT License

Copyright (c) 2026 Ahmad Adi
Copyright (c) 2026 xueleihu52-arch
```

The upstream project's original work is gratefully acknowledged. This derivative adds governance profiles, checkpoint integrity, cross-platform handoff, and a selective-backport compatibility model while preserving the file-based planning philosophy of the original.

See [UPSTREAM.md](UPSTREAM.md) for full derivation details.

---

## Key Differences from Upstream

- **Single authoritative source of truth** — no second state system; the plan file on disk is the only authority for task state.
- **Immutable state chain** — checkpoint → result → commit → head state forms a tamper-evident chain; each link references its predecessor by SHA-256.
- **Cross-device handoff** — deterministic protocol for handing off a task between macOS, Windows, and WSL with hash verification.
- **L0–L3 governance profiles** — four governance levels (`LIGHT_FAST`, `LIGHT_CONTROLLED`, `STANDARD`, `STRICT`) that control checkpoint frequency, attestation requirements, and validation strictness.
- **Layout v3** — project-local task planning at `00.项目规划与治理/<task-id>/` with deterministic single/multi-task discovery.
- **Task index** — `task-index.yaml` provides deterministic task discovery; single-task auto-discovery, multi-task requires explicit `task_id`.
- **SHA-256 plan attestation** — every plan file can be attested with a SHA-256 hash, enabling integrity verification at any time.
- **Smart hook injection** — selective injection into `userprompt`, `pretool`, and `precompact` hooks without blanket overrides.
- **Session catchup** — recovery after `/clear` or session restart by reading the latest checkpoint and plan state from disk.
- **Plan doctor diagnostics** — `plan-doctor` validates plan structure, checkpoint integrity, and handoff consistency.
- **Selective backport strategy** — upstream changes are evaluated individually; this is not a drop-in mirror.

---

## Compatibility Policy

planning-with-files-governed is an independent governance-oriented
derivative of OthmanAdi/planning-with-files. It is not an upstream mirror
and does not promise version parity with the latest upstream release.

Upstream changes are evaluated selectively. A change may be backported only
when it preserves:

1. the single-source-of-truth model;
2. deterministic plan and checkpoint contracts;
3. cross-platform path safety;
4. recoverable and verifiable handoffs;
5. backward-compatible data migration where practical; and
6. the Community Edition security and test gates.

Compatibility is maintained at the workflow and data-contract level where
documented. Drop-in compatibility with every upstream command, hook, plugin,
or release is not guaranteed.

Every accepted upstream backport must record its source commit, affected
files, compatibility impact, and validation evidence.

---

## Installation

### Prerequisites

- Python ≥ 3.10
- No external dependencies (standard library only)
- Git (recommended for version control of plan files)

### Steps

```bash
# Clone the repository
git clone https://github.com/xueleihu-labs/planning-with-files-governed.git
cd planning-with-files-governed

# Optionally add to PATH for global access
export PATH="$PWD/scripts:$PATH"
```

### Verification

```bash
python3 scripts/plan-doctor.py --version
python3 -m pytest tests/ -q
```

No `pip install` is required. All scripts use the Python standard library only.

On native Windows, use the `.ps1` wrappers in `scripts/` with PowerShell. They validate the selected Python executable and prefer a working `python` or `py` launcher over a broken `python3` alias. The `.sh` wrappers are for macOS, Linux, WSL, or Git Bash environments that provide `sh`/`bash`.

---

## Quick Start

### 1. Initialize a session

```bash
python3 scripts/init-session.py \
  --project-root /path/to/project \
  --task-id my-first-task \
  --governance-level L2
```

This creates the Layout v3 structure:

```
/path/to/project/
  00.项目规划与治理/
    task-index.yaml
    my-first-task/
      task_plan.md
      1_master_plan.md
      3_status_update.md
      4_handoff.md
      WORKFLOW_CHECKLIST.md
```

### 2. Run plan doctor

```bash
python3 scripts/plan-doctor.py \
  --project-root /path/to/project \
  --task-id my-first-task
```

The plan doctor validates plan structure, checkpoint integrity, and handoff consistency.

### 3. Attest a plan

```bash
python3 scripts/attest-plan.py \
  --project-root /path/to/project \
  --task-id my-first-task \
  --plan-file task_plan.md
```

This computes a SHA-256 hash of the plan file and records the attestation.

### 4. Inject plan context

```bash
python3 scripts/inject-plan.py \
  --project-root /path/to/project \
  --task-id my-first-task \
  --hook userprompt
```

This injects the current plan state into the specified hook context.

---

## Governance Levels (L0–L3)

| Level | Name | Checkpoint Freq | Attestation | Use Case |
|-------|------|----------------|-------------|----------|
| L0 | `LIGHT_FAST` | Per-phase | Optional | Quick prototyping, low-risk tasks |
| L1 | `LIGHT_CONTROLLED` | Per-phase | Required | Standard development tasks |
| L2 | `STANDARD` | Per-phase + on-demand | Required + verified | Production-bound features |
| L3 | `STRICT` | Every significant action | Required + verified + signed | Critical/irreversible operations |

See [docs/governance-levels.md](docs/governance-levels.md) for detailed specifications.

---

## Cross-Platform Handoff

planning-with-files-governed supports deterministic handoff between macOS, Windows, and WSL:

1. **Stable checkpoint** — the current device writes a checkpoint with SHA-256 attestation.
2. **Handoff record** — a `4_handoff.md` file records the handoff with predecessor hash, device info, and outstanding work.
3. **Write lock release** — the current device releases the task write lock.
4. **Verification** — the receiving device verifies the checkpoint hash, predecessor chain, and write lock state before resuming.

### Example: macOS → Windows

```bash
# On macOS: create checkpoint and handoff
python3 scripts/init-session.py --project-root /path/to/project --task-id shared-task
# ... work ...
python3 scripts/checkpoint.py --project-root /path/to/project --task-id shared-task --handoff

# On Windows: verify and resume
python3 scripts/init-session.py --project-root C:\Users\<username>\projects\example --task-id shared-task --resume
```

See [examples/cross-platform-handoff/](examples/cross-platform-handoff/) for a complete example.

---

## Threat Model

The primary threats addressed by this system:

- **Untrusted external checkpoints** — checkpoint data from external sources is treated as untrusted input; SHA-256 verification and schema validation are required.
- **Path traversal** — all file operations are confined to expected root directories.
- **Stale plans** — plan staleness is detected via checkpoint comparison and plan-doctor diagnostics.
- **Concurrent writers** — write lock enforcement prevents simultaneous modifications.
- **Credential leakage** — plan files must never contain credentials; runtime state is excluded from Git by default.

See [docs/threat-model.md](docs/threat-model.md) for the full threat model.

---

## Security

- Plan files may contain sensitive task information; do not commit runtime state to Git by default.
- External checkpoint and read-head data is treated as untrusted input.
- Schema validation does not guarantee content trustworthiness.
- File paths must be confined to expected root directories to prevent path traversal.
- No credentials should be stored in plan files.

See [SECURITY.md](SECURITY.md) for vulnerability reporting and security guidelines.

---

## Current Limitations

- **No remote state sync** — plan files are synchronized via user-managed file sync (e.g., Syncthing, Git); no built-in remote state server.
- **No automatic conflict resolution** — concurrent writes from two devices without proper handoff will produce a conflict that must be resolved manually.
- **Single-language scripts** — all runtime scripts are Python; no shell-only fallback is provided.
- **No GUI** — all interaction is via CLI and file editing.
- **Schema validation is structural** — JSON schema validation ensures structure but cannot verify semantic correctness of plan content.
- **Windows path handling** — backslash paths are normalized internally, but users should prefer forward slashes in configuration files.

---

## 中文摘要

**planning-with-files-governed** 是一个面向 AI 编码智能体的文件级规划与检查点治理系统，衍生自 OthmanAdi/planning-with-files（v3.8.1）。

**核心特性：**

- 单一事实源：磁盘上的计划文件是唯一权威状态
- 不可变状态链：检查点 → 结果 → 提交 → 头状态，通过 SHA-256 链接
- 跨设备交接：支持 macOS / Windows / WSL 之间的确定性交接
- L0–L3 治理分级：从轻量快速到严格签名，四级治理配置
- Layout v3：项目本地任务规划，确定性单/多任务发现
- SHA-256 计划认证、智能 Hook 注入、会话恢复、计划诊断

**重要声明：** 本项目是 planning-with-files 的独立社区衍生版本，与上游作者无隶属关系，未经上游作者背书或维护。

**许可证：** MIT（双重版权：Copyright (c) 2026 Ahmad Adi + Copyright (c) 2026 xueleihu52-arch）

**安装：** Python ≥ 3.10，无需外部依赖，克隆后即可使用。
