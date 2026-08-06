# Governance Levels (L0–L3)

planning-with-files-governed provides four governance levels that control checkpoint frequency, attestation requirements, and validation strictness. The level is selected at session initialization and can be **escalated** (but not de-escalated) mid-task.

<!-- 中文注释：四级治理配置，可在任务中升级但不可降级。 -->

---

## Overview Table

| Level | Name | Checkpoint Frequency | Attestation | Validation | Write Lock | Typical Use Case |
|-------|------|---------------------|-------------|------------|------------|------------------|
| L0 | `LIGHT_FAST` | Per-phase boundary | Optional | Structural only | Optional | Prototyping, low-risk experiments |
| L1 | `LIGHT_CONTROLLED` | Per-phase boundary | Required | Structural + schema | Required | Standard development tasks |
| L2 | `STANDARD` | Per-phase + on-demand | Required + verified | Full validation | Required + verified | Production-bound features |
| L3 | `STRICT` | Every significant action | Required + verified + signed | Full validation + audit trail | Required + verified + signed | Critical / irreversible operations |

---

## L0: LIGHT_FAST

<!-- 中文注释：L0 - 轻量快速，适合原型和低风险任务。 -->

**Purpose**: Maximum speed with minimal overhead. Suitable for prototyping, experimentation, and low-risk tasks where the cost of a mistake is low.

### Checkpoint Policy
- Checkpoints are created at phase boundaries only.
- Intermediate checkpoints are optional.

### Attestation Policy
- SHA-256 attestation is optional.
- If attestation is performed, it is recorded but not verified against predecessors.

### Validation Policy
- Structural validation only (file exists, required sections present).
- Schema validation is optional.

### Write Lock Policy
- Write lock is optional.
- Concurrent writes are detected but not blocked.

### When to Use
- Prototyping and proof-of-concept work.
- Low-risk refactoring.
- Solo development with no cross-device handoff.
- Quick tasks that complete in a single session.

### When NOT to Use
- Any task that may require cross-device handoff.
- Tasks touching production code or data.
- Tasks where state loss would be costly.

---

## L1: LIGHT_CONTROLLED

<!-- 中文注释：L1 - 轻量受控，标准开发任务。 -->

**Purpose**: Standard governance for typical development tasks. Balances speed with accountability.

### Checkpoint Policy
- Checkpoints are required at every phase boundary.
- Additional checkpoints can be created on-demand.

### Attestation Policy
- SHA-256 attestation is required for every checkpoint.
- Attestation is recorded in the checkpoint file.

### Validation Policy
- Structural validation (file exists, required sections present).
- JSON schema validation for all machine-readable files.
- `plan-doctor.py` must pass before phase completion.

### Write Lock Policy
- Write lock is required.
- Concurrent writes are blocked with an error.

### When to Use
- Standard feature development.
- Bug fixes in non-critical systems.
- Tasks that may involve cross-device handoff.
- Most day-to-day development work.

### When NOT to Use
- Rapid prototyping (use L0).
- Production deployments or irreversible operations (use L2 or L3).

---

## L2: STANDARD

<!-- 中文注释：L2 - 标准，生产级功能开发。 -->

**Purpose**: Full governance for production-bound features and tasks with moderate risk.

### Checkpoint Policy
- Checkpoints are required at every phase boundary.
- Additional checkpoints are created on-demand.
- Checkpoints are created before any potentially destructive operation.

### Attestation Policy
- SHA-256 attestation is required for every checkpoint.
- Attestation is verified against the predecessor chain.
- Verification failure blocks phase progression.

### Validation Policy
- Full structural and schema validation.
- `plan-doctor.py` must pass with no warnings.
- Evidence must be collected for each phase.
- Cross-platform path safety is enforced.

### Write Lock Policy
- Write lock is required and verified.
- Lock state is checked before every write operation.
- Stale locks are detected and reported.

### When to Use
- Production feature development.
- Database schema changes.
- API changes affecting external consumers.
- Tasks requiring audit trail.
- Cross-device handoff scenarios.

### When NOT to Use
- Quick prototyping (use L0 or L1).
- Life-critical or irreversible operations (use L3).

---

## L3: STRICT

<!-- 中文注释：L3 - 严格，关键和不可逆操作。 -->

**Purpose**: Maximum governance for critical, irreversible, or high-risk operations.

### Checkpoint Policy
- Checkpoints are required for **every significant action**, not just phase boundaries.
- A checkpoint is required before and after any irreversible operation.
- Checkpoints must be explicitly confirmed (not auto-created).

### Attestation Policy
- SHA-256 attestation is required for every checkpoint.
- Attestation is verified against the predecessor chain.
- Attestation is **signed** (requires explicit signer identity).
- Signature verification is required for phase progression.

### Validation Policy
- Full structural and schema validation.
- `plan-doctor.py` must pass with zero warnings and zero informational notes.
- Evidence must be collected and verified for every action.
- Full audit trail is maintained.
- Cross-platform path safety is strictly enforced.
- Privacy scan must pass before checkpoint creation.

### Write Lock Policy
- Write lock is required, verified, and signed.
- Lock state is checked before every write operation.
- Lock includes signer identity and timestamp.
- Stale locks require explicit override with documented justification.

### When to Use
- Production deployments.
- Database migrations or data backfills.
- Security-sensitive changes.
- Irreversible operations (deletions, force pushes, etc.).
- Regulatory or compliance-bound tasks.
- Tasks involving real financial transactions.

### When NOT to Use
- Standard development (use L1 or L2).
- Anything where the overhead would slow progress without adding meaningful safety.

---

## Escalation Rules

<!-- 中文注释：治理级别可升级不可降级。 -->

### Escalation (Allowed)

Governance level can be **escalated** mid-task:

```
L0 -> L1  ✓  (allowed)
L1 -> L2  ✓  (allowed)
L2 -> L3  ✓  (allowed)
L0 -> L3  ✓  (allowed, multi-level escalation)
```

Escalation is recorded in the checkpoint chain with:
- Previous level
- New level
- Reason for escalation
- Timestamp
- Escalation checkpoint hash

### De-escalation (Not Allowed)

Governance level **cannot be de-escalated** mid-task:

```
L3 -> L2  ✗  (not allowed)
L2 -> L1  ✗  (not allowed)
L1 -> L0  ✗  (not allowed)
```

If a lower level is needed, the task must be completed (or abandoned with a final checkpoint) and a new task initiated at the desired level.

### Rationale

- **Escalation is safe**: increasing governance never weakens existing guarantees.
- **De-escalation is unsafe**: decreasing governance mid-task could bypass integrity guarantees established at the higher level.
- This policy ensures that the governance level at any point in a task is at least as strict as the level at which the task was initiated.

---

## Selecting a Level

### Decision Guide

```
Is the task irreversible or life-critical?
  YES -> L3 (STRICT)
  NO  -> Does it touch production code, data, or external consumers?
           YES -> L2 (STANDARD)
           NO  -> Is it standard development with potential handoff?
                    YES -> L1 (LIGHT_CONTROLLED)
                    NO  -> L0 (LIGHT_FAST)
```

### Default Recommendation

When in doubt, use **L1 (LIGHT_CONTROLLED)**. It provides a good balance of speed and accountability for most development tasks.

