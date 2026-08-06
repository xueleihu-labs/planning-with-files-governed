# Threat Model

This document describes the threat model for planning-with-files-governed, including identified threats, their impact, and the mitigations implemented.

---

## Scope

This threat model covers the planning-with-files-governed runtime: scripts, plan files, checkpoints, handoff records, and the file-based state system. It does not cover threats in the broader development environment (OS security, network security, etc.) beyond where the system interacts with them.

---

## Trust Boundaries

```
┌──────────────────────────────────────────────────┐
│                 Trusted Zone                      │
│  - Local scripts (verified from repository)       │
│  - Locally created plan files and checkpoints     │
│  - Local write locks                              │
├──────────────────────────────────────────────────┤
│              Validation Boundary                  │
│  - SHA-256 verification                           │
│  - Schema validation                              │
│  - Path traversal prevention                      │
│  - Content review                                 │
├──────────────────────────────────────────────────┤
│                Untrusted Zone                     │
│  - External checkpoints (from other devices)      │
│  - External read-head data                        │
│  - File-sync-delivered files (Syncthing, etc.)    │
│  - Handoff records from other agents              │
│  - Any data not created on this device            │
└──────────────────────────────────────────────────┘
```

---

## Identified Threats

### T1: Untrusted External Checkpoints

<!-- 中文注释：威胁 T1 - 不可信的外部检查点。 -->

**Description**: Checkpoint files received from external sources (other devices, file sync, other agents) may contain tampered, corrupted, or malicious data.

**Impact**: 
- False state recovery (resuming from an incorrect checkpoint).
- Execution of malicious file paths embedded in checkpoint data.
- Chain corruption (predecessor hash manipulation to break audit trail).

**Mitigations**:
- **SHA-256 chain verification**: every checkpoint's predecessor hash is verified before consumption.
- **Schema validation**: all external checkpoints must pass JSON schema validation.
- **Content review**: users are advised to review external checkpoint content before resuming.
- **Quarantine**: external checkpoints that fail verification are rejected, not consumed.
- **No auto-execution**: checkpoint data is never executed; file paths are validated against the project root before any operation.

**Residual Risk**: A sophisticated attacker who can break SHA-256 could forge a checkpoint. This is considered a low-probability threat.

---

### T2: Path Traversal

<!-- 中文注释：威胁 T2 - 路径穿越攻击。 -->

**Description**: An attacker (or corrupted checkpoint) provides file paths that escape the project root directory, potentially accessing or modifying files outside the planning directory.

**Impact**:
- Reading sensitive files outside the project (e.g., `/etc/passwd`, `~/.ssh/id_rsa`).
- Writing files outside the project (e.g., overwriting system files).
- Information leakage through plan file references.

**Attack Vectors**:
- `--project-root` flag with a relative path containing `..`.
- `--plan-file` flag with an absolute path outside the project.
- Checkpoint data containing file paths with traversal sequences.
- Symlinks within the project directory pointing outside.

**Mitigations**:
- **Path resolution**: all paths are resolved to absolute form before any file operation.
- **Root confinement**: resolved paths must start with the expected project root.
- **Traversal rejection**: any path containing `..` that resolves outside the root is rejected.
- **Symlink check**: symlinks within the planning directory are resolved and checked against the root.
- **Path normalization**: backslash paths (Windows) are normalized to forward slashes before validation.
- **CI tests**: path safety tests verify that traversal attempts are rejected.

**Residual Risk**: Race conditions (TOCTOU) between path validation and file operation could theoretically allow traversal. This is mitigated by using resolved paths for both validation and operation.

---

### T3: Stale Plans

<!-- 中文注释：威胁 T3 - 过期计划。 -->

**Description**: A plan file on disk is outdated because work was done without updating the plan, or a handoff was incomplete.

**Impact**:
- Resuming work from an incorrect state.
- Duplicating already-completed work.
- Missing critical steps.
- Inconsistent state across devices.

**Mitigations**:
- **Checkpoint comparison**: the latest checkpoint is compared against the plan file to detect staleness.
- **Plan doctor diagnostics**: `plan-doctor.py` detects stale plans by comparing checkpoint timestamps with plan file modification times.
- **Handoff verification**: handoff records include the last checkpoint hash; mismatches indicate staleness.
- **Write lock**: prevents concurrent modifications that could lead to stale state.
- **Session catchup**: `init-session.py --resume` reads the latest checkpoint and alerts if the plan file is stale.

**Residual Risk**: If work is done entirely outside the system (no checkpoints, no plan updates), staleness cannot be detected. Users must use the system consistently.

---

### T4: Concurrent Writers

<!-- 中文注释：威胁 T4 - 并发写入冲突。 -->

**Description**: Two devices or agents attempt to modify the same task's plan files simultaneously.

**Impact**:
- Lost updates (one writer overwrites another's changes).
- Corrupted plan files (interleaved writes).
- Checkpoint chain corruption.
- Inconsistent state across devices.

**Mitigations**:
- **Write lock**: a `.write-lock` file prevents concurrent writes. The lock includes device identity and timestamp.
- **Lock verification**: every write operation checks the lock before proceeding.
- **Stale lock detection**: locks older than a configurable TTL are detected and reported (but not automatically broken — manual intervention required).
- **Handoff protocol**: cross-device handoff requires explicit lock release before the receiving device can acquire the lock.
- **File-sync awareness**: the system is designed to work with file sync tools (Syncthing) by using lock files that are not synced.

**Residual Risk**: If the write lock file is not synced (by design) and two devices start independently, the lock cannot prevent concurrent writes. The handoff protocol is the primary mitigation for cross-device scenarios.

---

### T5: Credential Leakage

<!-- 中文注释：威胁 T5 - 凭证泄露。 -->

**Description**: Sensitive information (passwords, API keys, tokens, private keys) is accidentally included in plan files, checkpoints, or evidence, and then committed to Git or synced to other devices.

**Impact**:
- Credential exposure in version control.
- Credential exposure on synced devices.
- Credential exposure in shared or public repositories.

**Mitigations**:
- **Guidance**: SECURITY.md and SKILL.md explicitly prohibit credentials in plan files.
- **CI privacy scan**: the CI pipeline includes a privacy scan that checks for common secret patterns (API keys, tokens, private keys).
- **.gitignore**: runtime state (checkpoints, evidence, locks) is excluded from Git by default.
- **Schema constraints**: schemas do not include fields for credentials.
- **No inline values**: guidance recommends environment variable names or secret manager references instead of inline values.

**Residual Risk**: The privacy scan uses pattern matching and may miss non-standard credential formats. Users must exercise judgment when writing plan files.

---

### T6: Checkpoint Chain Tampering

<!-- 中文注释：威胁 T6 - 检查点链篡改。 -->

**Description**: An attacker modifies a checkpoint file to alter the recorded state, then updates the predecessor hash to maintain apparent chain integrity.

**Impact**:
- False state recovery.
- Audit trail corruption.
- Loss of trust in historical state.

**Mitigations**:
- **SHA-256 chain**: each checkpoint records its predecessor's SHA-256 hash. Modifying a checkpoint requires modifying all subsequent checkpoints to maintain the chain.
- **Attestation verification**: `attest-plan.py --verify` checks the entire chain from the genesis checkpoint to the latest.
- **Plan doctor**: `plan-doctor.py` verifies the full chain integrity.
- **Immutable checkpoints**: checkpoint files are write-once; the system refuses to modify existing checkpoints.

**Residual Risk**: An attacker with write access to the entire checkpoint directory could re-create the entire chain. This is mitigated by Git versioning of plan files (if used) and the write lock.

---

### T7: Hook Injection Abuse

<!-- 中文注释：威胁 T7 - Hook 注入滥用。 -->

**Description**: Malicious or corrupted plan data is injected into agent hooks (userprompt, pretool, precompact), causing the agent to execute unintended actions.

**Impact**:
- Agent executes malicious instructions from injected plan data.
- Agent modifies files outside the intended scope.
- Agent skips security checks due to injected overrides.

**Mitigations**:
- **Smart injection**: `inject-plan.py` only injects structured, validated data — not raw plan content.
- **Schema validation**: injected data must pass schema validation.
- **Path safety**: any file paths in injected data are validated against the project root.
- **No command injection**: injected data never includes shell commands or code — only structured state information (phase name, next steps, constraints).

**Residual Risk**: If the agent interprets injected state as instructions rather than context, it could be misled. This is a limitation of agent behavior, not the injection system.

---

## Threat Summary

| ID | Threat | Severity | Likelihood | Primary Mitigation |
|----|--------|----------|------------|-------------------|
| T1 | Untrusted external checkpoints | High | Medium | SHA-256 chain + schema validation |
| T2 | Path traversal | High | Low | Path resolution + root confinement |
| T3 | Stale plans | Medium | Medium | Checkpoint comparison + plan-doctor |
| T4 | Concurrent writers | Medium | Medium | Write lock + handoff protocol |
| T5 | Credential leakage | High | Low | CI privacy scan + .gitignore |
| T6 | Checkpoint chain tampering | High | Low | SHA-256 chain + immutable checkpoints |
| T7 | Hook injection abuse | Medium | Low | Smart injection + schema validation |

---

## Assumptions

- The local file system is trusted (not compromised by malware).
- The local Python runtime is trusted (scripts are run from a verified repository).
- File sync tools (Syncthing) are correctly configured but may deliver files from untrusted devices.
- Git repositories are used for version control but may be public.
- The agent executing the plan is cooperative (not adversarial).

## Out of Scope

- Operating system security.
- Network-level attacks (MITM, etc.).
- Agent adversarial behavior (the agent is assumed to be cooperative).
- Physical security of devices.
- Cryptographic attacks on SHA-256 (considered computationally infeasible).

