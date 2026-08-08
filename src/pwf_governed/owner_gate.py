"""Gate 2 extracted module: owner_gate.py.

Generated from the Gate 1 planning.py baseline.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import copy
import datetime as dt
import re

from pwf_governed._legacy import (
    plan_contracts,
    workflow_contracts,
)
from pwf_governed._legacy import plan_contracts as contracts
from pwf_governed._legacy import workflow_contracts as workflow

from pwf_governed._legacy import (
    plan_contracts,
    workflow_contracts,
)
from pwf_governed.checkpoints import (
    _checkpoint_path,
    _final_checkpoint_gate,
    _validate_checkpoint_reference,
)
from pwf_governed.core.constants import (
    CHECKPOINT_REFS_DIR,
    OWNER_GATE_IDENTITY_ASSURANCE,
    OWNER_GATE_RECEIPTS_DIR,
    OWNER_GATE_RECEIPT_SCHEMA_VERSION,
    OWNER_GATE_RECEIPT_TYPE,
)
from pwf_governed.core.envelope import (
    _append_unique,
    _load_instance,
    _parse_timestamp,
    _read_json,
    _result_error,
    _safe_component,
    _string_values,
    _transaction_write,
)
from pwf_governed.core.errors import (
    PlanningError,
)
from pwf_governed.edition import (
    adapt_input_once,
    adapt_output_once,
    edition_operation,
)
from pwf_governed.shared.checkpoint_support import (
    _resolve_checkpoint_file,
)
from pwf_governed.shared.evidence import (
    _final_evidence_file,
)

def _owner_gate_receipt_path(instance: Path, receipt_id: str) -> Path:
    return instance / OWNER_GATE_RECEIPTS_DIR / f"{receipt_id}.json"

def _owner_gate_receipt_digest(receipt: dict[str, Any]) -> str:
    return contracts.contract_digest(
        receipt,
        exclude_fields=("receipt_id", "registered_at", "receipt_digest"),
    )


def _canonical_owner_gate_receipt(
    receipt: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    persisted = copy.deepcopy(receipt)
    canonical = adapt_input_once(
        receipt,
        payload_kind="OwnerGateReceipt",
        callsite_id="owner-gate-receipt-read",
    )
    return canonical, persisted

def _owner_gate_receipt_identity(
    *,
    task_id: str,
    plan_id: str,
    plan_version: str,
    state_root: Path,
    instance: Path,
    gate_id: str,
    confirmation_reference: str,
    confirmation_statement: str,
    accepted_commit: str,
    accepted_checkpoint: str,
    result_commit_head: str,
    direct_read_head: str,
    external_read_head: str,
    authorize: str,
    evidence_refs: list[str],
) -> dict[str, Any]:
    """Build the stable owner-confirmation identity used for idempotency."""
    return {
        "receipt_type": OWNER_GATE_RECEIPT_TYPE,
        "receipt_schema_version": OWNER_GATE_RECEIPT_SCHEMA_VERSION,
        "task_id": task_id,
        "plan_id": plan_id,
        "plan_version": plan_version,
        "state_root": str(state_root),
        "instance_path": str(instance),
        "gate_id": gate_id,
        "gate_type": "USER_GATE",
        "previous_status": "PENDING",
        "decision": "SATISFIED",
        "confirmation_reference": confirmation_reference,
        "confirmation_statement": confirmation_statement,
        "accepted_commit": accepted_commit,
        "accepted_checkpoint": accepted_checkpoint,
        "result_commit_head": result_commit_head,
        "direct_read_head": direct_read_head,
        "external_read_head": external_read_head,
        "authorize": authorize,
        "evidence_refs": list(evidence_refs),
        "authorization_source": "EXPLICIT_USER_AUTHORIZATION",
        "identity_assurance": OWNER_GATE_IDENTITY_ASSURANCE,
    }

def _load_owner_gate_receipts(instance: Path) -> list[dict[str, Any]]:
    directory = instance / OWNER_GATE_RECEIPTS_DIR
    if not directory.exists():
        return []
    if directory.is_symlink() or not directory.is_dir():
        raise PlanningError("UNSAFE_INSTANCE_ROOT", "owner-gate receipts directory must be a real directory")
    receipts: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        if path.is_symlink():
            raise PlanningError("UNSAFE_INSTANCE_ROOT", f"owner-gate receipt cannot be a symlink: {path}")
        receipt, persisted = _canonical_owner_gate_receipt(
            _read_json(path, code="INVALID_OWNER_GATE_RECEIPT")
        )
        if persisted.get("receipt_digest") != _owner_gate_receipt_digest(persisted):
            raise PlanningError(
                "OWNER_GATE_RECEIPT_MISMATCH",
                "owner-gate receipt digest does not match its persisted content",
            )
        receipts.append(receipt)
    return receipts

def _validate_owner_gate_receipt(
    receipt: dict[str, Any],
    *,
    state_root: Path,
    instance: Path,
    envelope: dict[str, Any],
    plan: dict[str, Any],
    gate: dict[str, Any] | None = None,
    plan_digest_field: str = "plan_package_digest_after",
    persisted_receipt: dict[str, Any] | None = None,
) -> None:
    receipt, detected_persistence = _canonical_owner_gate_receipt(receipt)
    digest_source = persisted_receipt or detected_persistence
    required = (
        "receipt_id",
        "receipt_type",
        "receipt_schema_version",
        "task_id",
        "plan_id",
        "plan_version",
        "state_root",
        "instance_path",
        "gate_id",
        "gate_type",
        "previous_status",
        "decision",
        "confirmation_reference",
        "confirmation_statement",
        "accepted_commit",
        "accepted_checkpoint",
        "result_commit_head",
        "direct_read_head",
        "external_read_head",
        "authorize",
        "evidence_refs",
        "authorization_source",
        "identity_assurance",
        "plan_package_digest_before",
        plan_digest_field,
        "registered_at",
        "receipt_digest",
    )
    missing = [field for field in required if field not in receipt]
    if missing:
        raise PlanningError("INVALID_OWNER_GATE_RECEIPT", "receipt missing fields: " + ", ".join(missing))
    if receipt["receipt_type"] != OWNER_GATE_RECEIPT_TYPE or receipt["receipt_schema_version"] != OWNER_GATE_RECEIPT_SCHEMA_VERSION:
        raise PlanningError("INVALID_OWNER_GATE_RECEIPT", "unsupported owner-gate receipt type or schema")
    for field, expected in (
        ("task_id", envelope["task_id"]),
        ("plan_id", plan["plan_id"]),
        ("plan_version", plan["plan_version"]),
        ("state_root", str(state_root)),
        ("instance_path", str(instance)),
    ):
        if receipt[field] != expected:
            raise PlanningError("OWNER_GATE_RECEIPT_MISMATCH", f"receipt {field} does not match the current Plan instance")
    if receipt["gate_type"] != "USER_GATE" or receipt["previous_status"] != "PENDING" or receipt["decision"] != "SATISFIED":
        raise PlanningError("INVALID_OWNER_GATE_RECEIPT", "receipt does not describe PENDING to SATISFIED USER_GATE registration")
    if receipt["authorization_source"] != "EXPLICIT_USER_AUTHORIZATION" or receipt["identity_assurance"] != OWNER_GATE_IDENTITY_ASSURANCE:
        raise PlanningError("INVALID_OWNER_GATE_RECEIPT", "receipt has an invalid authorization assurance declaration")
    for field in ("confirmation_reference", "confirmation_statement", "accepted_checkpoint", "authorize"):
        if not isinstance(receipt[field], str) or not receipt[field].strip():
            raise PlanningError("INVALID_OWNER_GATE_RECEIPT", f"receipt {field} must be non-empty")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", str(receipt["accepted_commit"])):
        raise PlanningError("INVALID_OWNER_GATE_RECEIPT", "receipt accepted_commit is not a full Git commit hash")
    if receipt["result_commit_head"] != "VALID" or receipt["direct_read_head"] != "PASSED" or receipt["external_read_head"] != "PASSED":
        raise PlanningError("INVALID_OWNER_GATE_RECEIPT", "receipt accepted chain/read-head facts are not passed")
    if receipt["authorize"] != "PRE_CLOSE":
        raise PlanningError("INVALID_OWNER_GATE_RECEIPT", "receipt does not authorize PRE_CLOSE")
    refs = receipt["evidence_refs"]
    if not isinstance(refs, list) or not refs or any(not isinstance(item, str) or not item.strip() for item in refs):
        raise PlanningError("OWNER_GATE_EVIDENCE_REQUIRED", "owner-gate receipt requires non-empty evidence_refs")
    for raw in refs:
        if _final_evidence_file(state_root, instance, raw) is None:
            raise PlanningError("OWNER_GATE_EVIDENCE_NOT_FOUND", f"owner-gate evidence does not exist: {raw}")
    if receipt["plan_package_digest_before"] == receipt[plan_digest_field]:
        raise PlanningError("INVALID_OWNER_GATE_RECEIPT", "owner-gate receipt did not record a PlanPackage transition")
    if receipt[plan_digest_field] != contracts.contract_digest(plan):
        raise PlanningError("OWNER_GATE_RECEIPT_MISMATCH", f"receipt {plan_digest_field} does not match the current PlanPackage")
    try:
        _parse_timestamp(str(receipt["registered_at"]))
    except (TypeError, ValueError) as exc:
        raise PlanningError("INVALID_OWNER_GATE_RECEIPT", "receipt registered_at is not RFC3339") from exc
    if receipt["receipt_digest"] != _owner_gate_receipt_digest(digest_source):
        raise PlanningError("OWNER_GATE_RECEIPT_MISMATCH", "owner-gate receipt digest does not match its content")
    if gate is not None:
        if gate.get("condition_type") != "USER_GATE" or gate.get("status") != "SATISFIED":
            raise PlanningError("OWNER_GATE_RECEIPT_MISMATCH", "PlanPackage gate is not a satisfied USER_GATE")
        ref = gate.get("owner_gate_receipt_ref")
        expected_ref = Path(OWNER_GATE_RECEIPTS_DIR, f"{receipt['receipt_id']}.json").as_posix()
        if ref != expected_ref or ref not in _string_values(gate.get("evidence_refs")):
            raise PlanningError("OWNER_GATE_RECEIPT_MISMATCH", "PlanPackage does not bind the owner-gate receipt")

def _owner_gate_chain_evidence(
    state_root: Path,
    instance: Path,
    evidence_refs: list[str],
    accepted_commit: str,
) -> None:
    """Require one real structured evidence file to bind result, commit and head."""
    for raw in evidence_refs:
        path = _final_evidence_file(state_root, instance, raw)
        if path is None or path.suffix.lower() != ".json":
            continue
        value = _read_json(path, code="OWNER_GATE_EVIDENCE_INVALID")
        if (
            value.get("result_commit_head") == "VALID"
            and value.get("commit") == accepted_commit
            and value.get("head") == accepted_commit
        ):
            return
    raise PlanningError(
        "OWNER_GATE_CHAIN_EVIDENCE_MISSING",
        "evidence_refs must include a real result_commit_head=VALID record bound to accepted_commit",
    )

@edition_operation
def register_owner_gate(
    instance_root: str | Path,
    *,
    task_id: str,
    plan_id: str,
    state_root: str | Path,
    gate_id: str,
    expected_status: str,
    decision: str,
    confirmation_reference: str,
    confirmation_statement: str,
    accepted_commit: str,
    accepted_checkpoint: str,
    result_commit_head: str,
    direct_read_head: str,
    external_read_head: str,
    authorize: str,
    evidence_refs: list[str],
    preview: bool = False,
    apply: bool = False,
    agent: str = "planning-with-files",
) -> dict[str, Any]:
    """Register one explicit owner USER_GATE decision through the PLAN writer."""
    try:
        if preview == apply:
            raise PlanningError("INVALID_MODE", "exactly one of preview or apply must be true")
        actual_state_root, instance, envelope, plan, checklist = _load_instance(instance_root)
        explicit_state_root = Path(state_root).expanduser().resolve(strict=False)
        if explicit_state_root != actual_state_root.resolve(strict=False):
            raise PlanningError("STATE_ROOT_MISMATCH", "explicit state-root does not match the PLAN instance")
        if task_id != envelope["task_id"] or task_id != plan["task_id"]:
            raise PlanningError("TASK_ID_MISMATCH", "task_id does not match the loaded TaskEnvelope and PlanPackage")
        if plan_id != plan["plan_id"]:
            raise PlanningError("PLAN_ID_MISMATCH", "plan_id does not match the loaded PlanPackage")
        if expected_status not in {"PENDING", "SATISFIED"}:
            raise PlanningError("INVALID_OWNER_GATE_STATUS", f"unsupported expected gate status: {expected_status}")
        if decision != "SATISFIED":
            raise PlanningError("INVALID_OWNER_GATE_DECISION", "owner-gate registration only accepts decision=SATISFIED")
        if result_commit_head != "VALID" or direct_read_head != "PASSED" or external_read_head != "PASSED":
            raise PlanningError("OWNER_GATE_EVIDENCE_NOT_PASSED", "result/commit/head and Direct/external read-head must be passed")
        if authorize != "PRE_CLOSE":
            raise PlanningError("INVALID_OWNER_GATE_AUTHORIZATION", "owner-gate registration only authorizes PRE_CLOSE")
        for label, value in (
            ("confirmation_reference", confirmation_reference),
            ("confirmation_statement", confirmation_statement),
            ("accepted_checkpoint", accepted_checkpoint),
        ):
            if not isinstance(value, str) or not value.strip():
                raise PlanningError("OWNER_CONFIRMATION_REQUIRED", f"{label} is required")
        if not re.fullmatch(r"[0-9a-fA-F]{40}", accepted_commit):
            raise PlanningError("INVALID_COMMIT", "accepted_commit must be a full Git commit hash")
        normalized_commit = accepted_commit.lower()
        normalized_refs = sorted(_append_unique([], [str(item).strip() for item in evidence_refs if str(item).strip()]))
        if not normalized_refs:
            raise PlanningError("OWNER_GATE_EVIDENCE_REQUIRED", "at least one evidence_ref is required")
        for raw in normalized_refs:
            if _final_evidence_file(actual_state_root, instance, raw) is None:
                raise PlanningError("OWNER_GATE_EVIDENCE_NOT_FOUND", f"owner-gate evidence does not exist: {raw}")
        _owner_gate_chain_evidence(actual_state_root, instance, normalized_refs, normalized_commit)

        matching_gates = [
            gate for gate in plan.get("human_gates", [])
            if isinstance(gate, dict) and gate.get("condition_id") == gate_id
        ]
        if len(matching_gates) != 1:
            raise PlanningError("OWNER_GATE_NOT_FOUND", f"expected exactly one USER_GATE: {gate_id}")
        gate = matching_gates[0]
        if gate.get("condition_type") != "USER_GATE":
            raise PlanningError("OWNER_GATE_TYPE_NOT_SUPPORTED", f"gate is not a USER_GATE: {gate_id}")
        if gate.get("required", True) is not True:
            raise PlanningError("OWNER_GATE_NOT_REQUIRED", f"gate is not required: {gate_id}")
        actual_status = str(gate.get("status"))

        identity = _owner_gate_receipt_identity(
            task_id=task_id,
            plan_id=plan_id,
            plan_version=str(plan["plan_version"]),
            state_root=actual_state_root,
            instance=instance,
            gate_id=gate_id,
            confirmation_reference=confirmation_reference.strip(),
            confirmation_statement=confirmation_statement.strip(),
            accepted_commit=normalized_commit,
            accepted_checkpoint=accepted_checkpoint.strip(),
            result_commit_head=result_commit_head,
            direct_read_head=direct_read_head,
            external_read_head=external_read_head,
            authorize=authorize,
            evidence_refs=normalized_refs,
        )

        if actual_status == "SATISFIED":
            receipt_ref = gate.get("owner_gate_receipt_ref")
            if not isinstance(receipt_ref, str) or not receipt_ref:
                raise PlanningError("OWNER_GATE_RECEIPT_MISSING", f"satisfied gate has no formal receipt: {gate_id}")
            receipt_path = _final_evidence_file(actual_state_root, instance, receipt_ref)
            if receipt_path is None:
                raise PlanningError("OWNER_GATE_RECEIPT_NOT_FOUND", f"formal owner-gate receipt is missing: {receipt_ref}")
            existing, persisted_existing = _canonical_owner_gate_receipt(
                _read_json(receipt_path, code="INVALID_OWNER_GATE_RECEIPT")
            )
            updated_gate = copy.deepcopy(gate)
            _validate_owner_gate_receipt(
                existing,
                state_root=actual_state_root,
                instance=instance,
                envelope=envelope,
                plan=plan,
                gate=updated_gate,
                persisted_receipt=persisted_existing,
            )
            if not all(existing.get(field) == value for field, value in identity.items()):
                raise PlanningError(
                    "OWNER_GATE_CONFLICT",
                    "same gate is already satisfied by a different confirmation",
                    result="CONFLICT",
                )
            _owner_gate_checkpoint_authority(
                actual_state_root,
                instance,
                envelope,
                plan,
                checklist,
                accepted_checkpoint.strip(),
                normalized_commit,
            )
            return {
                "result": "EXISTING_OWNER_GATE",
                "task_id": task_id,
                "plan_id": plan_id,
                "gate_id": gate_id,
                "gate_status": "SATISFIED",
                "receipt_id": existing["receipt_id"],
                "receipt_path": receipt_path.as_posix(),
                "no_op": True,
                "idempotent": True,
                "state_root": str(actual_state_root),
                "instance_path": str(instance),
            }
        if actual_status != expected_status:
            raise PlanningError(
                "OWNER_GATE_STATE_MISMATCH",
                f"gate {gate_id} is {actual_status}, expected {expected_status}",
            )
        if actual_status != "PENDING":
            raise PlanningError("OWNER_GATE_STATE_CONFLICT", f"only PENDING USER_GATE can be satisfied: {gate_id}")

        for other in plan.get("human_gates", []):
            if not isinstance(other, dict) or other.get("condition_id") == gate_id or not other.get("required", True):
                continue
            if other.get("status") not in {"SATISFIED", "WAIVED", "NOT_APPLICABLE"}:
                raise PlanningError(
                    "OWNER_GATE_OTHER_GATE_PENDING",
                    f"cannot satisfy {gate_id} while another required gate is unresolved: {other.get('condition_id')}",
                )

        for existing in _load_owner_gate_receipts(instance):
            if existing.get("task_id") == task_id and existing.get("plan_id") == plan_id and existing.get("gate_id") == gate_id:
                raise PlanningError("OWNER_GATE_ORPHAN_RECEIPT", f"existing receipt is not bound to a satisfied Plan gate: {gate_id}")

        accepted_checkpoint_id = accepted_checkpoint.strip()
        _owner_gate_checkpoint_authority(
            actual_state_root,
            instance,
            envelope,
            plan,
            checklist,
            accepted_checkpoint_id,
            normalized_commit,
        )
        before_digest = contracts.contract_digest(plan)
        persisted_identity = adapt_output_once(
            identity,
            payload_kind="OwnerGateReceipt",
            callsite_id="owner-gate-receipt-write",
        )
        receipt_id = (
            f"ogr-{_safe_component(task_id, 'task_id')}-{_safe_component(gate_id, 'gate_id')}-"
            f"{contracts.contract_digest(persisted_identity)[:16]}"
        )
        receipt_ref = f"{OWNER_GATE_RECEIPTS_DIR}/{receipt_id}.json"
        registered_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        updated_plan = copy.deepcopy(plan)
        updated_gate = next(
            item for item in updated_plan["human_gates"]
            if isinstance(item, dict) and item.get("condition_id") == gate_id
        )
        updated_gate["status"] = "SATISFIED"
        updated_gate["evidence_refs"] = _append_unique(
            _string_values(updated_gate.get("evidence_refs")),
            normalized_refs + [receipt_ref],
        )
        updated_gate["owner_gate_receipt_ref"] = receipt_ref
        updated_gate["owner_gate_receipt_id"] = receipt_id
        updated_gate["owner_gate_registered_at"] = registered_at
        after_digest = contracts.contract_digest(updated_plan)
        receipt = {
            **identity,
            "receipt_id": receipt_id,
            "accepted_checkpoint": accepted_checkpoint_id,
            "registered_at": registered_at,
            "plan_package_digest_before": before_digest,
            "plan_package_digest_after": after_digest,
        }
        persisted_receipt = adapt_output_once(
            receipt,
            payload_kind="OwnerGateReceipt",
            callsite_id="owner-gate-receipt-write",
        )
        receipt_digest = _owner_gate_receipt_digest(persisted_receipt)
        receipt["receipt_digest"] = receipt_digest
        persisted_receipt["receipt_digest"] = receipt_digest
        persisted_updated_plan = adapt_output_once(
            updated_plan,
            payload_kind="PlanPackage",
            callsite_id="plan-package-write",
        )
        contracts.validate_plan_package(updated_plan)
        _validate_owner_gate_receipt(
            receipt,
            state_root=actual_state_root,
            instance=instance,
            envelope=envelope,
            plan=updated_plan,
            gate=updated_gate,
            persisted_receipt=persisted_receipt,
        )
        result: dict[str, Any] = {
            "result": "PREVIEW" if preview else "REGISTERED_OWNER_GATE",
            "task_id": task_id,
            "plan_id": plan_id,
            "gate_id": gate_id,
            "gate_status": "SATISFIED",
            "receipt_id": receipt_id,
            "receipt_path": _owner_gate_receipt_path(instance, receipt_id).as_posix(),
            "plan_package_digest_before": before_digest,
            "plan_package_digest_after": after_digest,
            "planned_files": ["plan-package.json", receipt_ref],
            "created_files": [],
            "no_op": False,
            "state_root": str(actual_state_root),
            "instance_path": str(instance),
            "identity_assurance": OWNER_GATE_IDENTITY_ASSURANCE,
        }
        if preview:
            return result
        _transaction_write(
            instance,
            actual_state_root,
            {
                "plan-package.json": contracts.stable_json(persisted_updated_plan),
                receipt_ref: contracts.stable_json(persisted_receipt),
            },
            expected_digests={
                "plan-package.json": workflow.file_digest(instance / "plan-package.json"),
                receipt_ref: workflow.sha256_digest(""),
            },
            lock_target="plan-package.json",
            lock_name="owner-gate",
            agent=agent,
            transaction_tag="f1-07-owner-gate",
        )
        _stored_root, _stored_instance, _stored_envelope, stored_plan, _stored_checklist = _load_instance(instance)
        stored_receipt = _read_json(_owner_gate_receipt_path(instance, receipt_id), code="INVALID_OWNER_GATE_RECEIPT")
        _validate_owner_gate_receipt(
            stored_receipt,
            state_root=actual_state_root,
            instance=instance,
            envelope=envelope,
            plan=stored_plan,
            gate=next(item for item in stored_plan["human_gates"] if item.get("condition_id") == gate_id),
        )
        result["created_files"] = ["plan-package.json", receipt_ref]
        return result
    except PlanningError as exc:
        return _result_error(exc)
    except (OSError, ValueError, workflow.ContractError) as exc:
        return _result_error(PlanningError("INTERNAL_ERROR", str(exc)))

def _owner_gate_checkpoint_authority(
    state_root: Path,
    instance: Path,
    envelope: dict[str, Any],
    plan: dict[str, Any],
    checklist: str,
    checkpoint_id: str,
    accepted_commit: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    ref_path = _checkpoint_path(instance, checkpoint_id, CHECKPOINT_REFS_DIR)
    if not ref_path.is_file() or ref_path.is_symlink():
        raise PlanningError("OWNER_GATE_CHECKPOINT_NOT_FOUND", f"accepted checkpoint ref does not exist: {checkpoint_id}")
    ref = _read_json(ref_path, code="INVALID_CHECKPOINT_REF")
    if ref.get("task_id") != envelope["task_id"] or ref.get("plan_id") != plan["plan_id"]:
        raise PlanningError("OWNER_GATE_CHECKPOINT_MISMATCH", "accepted checkpoint is not bound to the current task and Plan")
    if ref.get("checkpoint_status") != "PASSED" or ref.get("verification_status") != "PASSED":
        raise PlanningError("OWNER_GATE_CHECKPOINT_NOT_PASSED", "accepted checkpoint is not PASSED/verified")
    if ref.get("effective_action") != "ADVANCE_PHASE" or ref.get("publication_status") != "PUBLISHED_COMMIT":
        raise PlanningError("OWNER_GATE_READ_HEAD_NOT_PASSED", "accepted checkpoint is not an officially published read-head")
    if ref.get("repository_head") != accepted_commit:
        raise PlanningError("OWNER_GATE_COMMIT_MISMATCH", "accepted commit does not match checkpoint repository_head")
    manifest_raw = ref.get("manifest_location")
    if isinstance(manifest_raw, str) and manifest_raw:
        manifest_path, _ = _resolve_checkpoint_file(
            state_root,
            instance,
            manifest_raw,
            label="manifest_location",
        )
        manifest = _read_json(manifest_path, code="OWNER_GATE_CHECKPOINT_MISMATCH")
        if manifest.get("audited_git_head") != accepted_commit:
            raise PlanningError("OWNER_GATE_COMMIT_MISMATCH", "checkpoint manifest does not audit the accepted commit")
    context = _validate_checkpoint_reference(
        ref,
        envelope,
        plan,
        checklist,
        state_root,
        instance,
    )
    blocking, waiting, _warnings, _evidence, trusted = _final_checkpoint_gate(
        state_root,
        instance,
        envelope,
        plan,
        checklist,
        {"require_checkpoint_ref": True},
        "ADVANCED",
    )
    if blocking or waiting or trusted != checkpoint_id:
        findings = blocking + waiting
        raise PlanningError(
            "OWNER_GATE_CHECKPOINT_NOT_AUTHORITATIVE",
            "; ".join(findings) or f"checkpoint is not the single authoritative head: {checkpoint_id}",
        )
    return ref, context
