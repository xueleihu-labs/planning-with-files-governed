"""Gate 2 extracted module: packets.py.

Generated from the Gate 1 planning.py baseline.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from typing import Iterable
import copy

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
from pwf_governed.core.constants import (
    CURRENT_VERSION,
    PACKETS_DIR,
    PACKET_ID_PREFIX,
    RECEIPTS_DIR,
)
from pwf_governed.core.envelope import (
    _append_unique,
    _load_instance,
    _read_json,
    _result_error,
    _safe_component,
    _transaction_write,
)
from pwf_governed.core.errors import (
    PlanningError,
)
from pwf_governed.governance import (
    _load_phase_packets,
)
from pwf_governed.midcourse_gate import (
    _ensure_midcourse_gate_allows_phase,
    _ensure_midcourse_gate_evidence,
)
from pwf_governed.receipts import (
    _load_receipts,
    _validate_receipt_against_packet,
)

def _find_phase_and_work_item(
    plan: dict[str, Any], checklist: str, phase_id: str, work_item_id: str
) -> tuple[dict[str, Any], dict[str, str]]:
    _safe_component(phase_id, "phase_id")
    _safe_component(work_item_id, "work_item_id")
    phases = [item for item in plan.get("phases", []) if item.get("phase_id") == phase_id]
    if not phases:
        raise PlanningError("INVALID_PHASE_ID", f"phase_id does not exist in PlanPackage: {phase_id}")
    tasks = workflow.checklist_tasks(checklist)
    task = next((item for item in tasks if item.get("ID") == work_item_id), None)
    if task is None:
        raise PlanningError("INVALID_WORK_ITEM_ID", f"work_item_id does not exist in WORKFLOW_CHECKLIST.md: {work_item_id}")
    phase = copy.deepcopy(phases[0])
    declared_items = phase.get("work_items", phase.get("work_item_ids"))
    if declared_items is not None and work_item_id not in {str(item) for item in declared_items}:
        raise PlanningError("INVALID_WORK_ITEM_ID", f"work_item_id is not declared by phase {phase_id}: {work_item_id}")
    return phase, task

def _selected_skill_ref(plan: dict[str, Any]) -> tuple[dict[str, Any], str]:
    compatible = [
        item for item in plan.get("capability_refs", [])
        if isinstance(item, dict) and item.get("compatibility_status") == "COMPATIBLE"
    ]
    if compatible:
        selected = sorted(
            compatible,
            key=lambda item: (str(item.get("skill_id", "")), str(item.get("capability_id", ""))),
        )[0]
        skill_ref = copy.deepcopy(selected)
        skill_ref["dispatch_status"] = "AUTO_ALLOWED"
        return skill_ref, "AUTO_ALLOWED"
    return {
        "compatibility_status": "UNCONFIRMED",
        "dispatch_status": "MANUAL_SELECTION_REQUIRED",
    }, "MANUAL_SELECTION_REQUIRED"

def _packet_identity_payload(
    plan: dict[str, Any],
    phase: dict[str, Any],
    work_item: dict[str, str],
    skill_ref: dict[str, Any],
    allowed_scope: dict[str, Any],
    forbidden_scope: dict[str, Any],
    objective: str,
    completion_conditions: list[dict[str, Any]],
    evidence_requirements: dict[str, Any],
    revision_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "plan_id": plan["plan_id"],
        "plan_version": plan["plan_version"],
        "task_id": plan["task_id"],
        "phase_id": phase["phase_id"],
        "work_item_id": work_item["ID"],
        "skill_ref": skill_ref,
        "objective": objective,
        "allowed_scope": allowed_scope,
        "forbidden_scope": forbidden_scope,
        "completion_conditions": completion_conditions,
        "evidence_requirements": evidence_requirements,
    }
    if revision_metadata:
        payload["revision"] = copy.deepcopy(revision_metadata)
    return payload

def _build_revision_metadata(
    instance: Path,
    plan: dict[str, Any],
    phase_id: str,
    work_item_id: str,
    *,
    revision: int | None,
    revision_type: str | None,
    predecessor_checkpoint_id: str | None,
    revision_reason: str | None,
    revision_scope: str | None,
    technical_reexecution: bool | None,
    audit_reexecution: bool | None,
    revision_evidence_refs: Iterable[str],
) -> dict[str, Any]:
    if revision is None or revision == 1:
        return {}
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 2:
        raise PlanningError("INVALID_REVISION", "revision must be an integer greater than 1")
    if not all(isinstance(value, str) and value.strip() for value in (revision_type, predecessor_checkpoint_id, revision_reason, revision_scope)):
        raise PlanningError("INVALID_REVISION", "revision metadata is incomplete")
    technical = False if technical_reexecution is None else technical_reexecution
    audit = False if audit_reexecution is None else audit_reexecution
    if not isinstance(technical, bool) or not isinstance(audit, bool) or technical or audit:
        raise PlanningError("INVALID_REVISION", "governance revision cannot re-execute technical work or audit")

    phase_packets = _load_phase_packets(instance, plan, phase_id)
    base_packets = [
        packet for packet in phase_packets
        if int(packet.get("revision", 1)) == 1 and packet.get("work_item_id") == work_item_id
    ]
    if len(base_packets) != 1:
        raise PlanningError(
            "REVISION_SOURCE_PACKET_AMBIGUOUS" if len(base_packets) > 1 else "REVISION_SOURCE_PACKET_NOT_FOUND",
            "revision must reference exactly one immutable base packet",
        )
    source_packet = base_packets[0]
    receipts = _load_receipts(instance)
    source_receipts = [
        receipt for receipt in receipts
        if receipt.get("packet_id") == source_packet.get("packet_id")
    ]
    if len(source_receipts) != 1:
        raise PlanningError(
            "REVISION_SOURCE_RECEIPT_AMBIGUOUS" if len(source_receipts) > 1 else "REVISION_SOURCE_RECEIPT_NOT_FOUND",
            "revision must reference exactly one immutable source ExecutionReceipt",
        )
    source_receipt = source_receipts[0]
    _validate_receipt_against_packet(source_receipt, source_packet)
    if source_receipt.get("result") not in {"PASS", "PASS_WITH_WARNINGS"}:
        raise PlanningError("REVISION_SOURCE_RECEIPT_NOT_COMPLETE", "source ExecutionReceipt is not complete")
    evidence = _append_unique(
        [
            f"{PACKETS_DIR}/{source_packet['packet_id']}.json",
            f"{RECEIPTS_DIR}/{source_receipt['receipt_id']}.json",
        ],
        list(revision_evidence_refs),
    )
    if not evidence:
        raise PlanningError("INVALID_REVISION", "revision_evidence_refs must not be empty")
    return {
        "revision": revision,
        "revision_type": revision_type,
        "predecessor_checkpoint_id": predecessor_checkpoint_id,
        "revision_reason": revision_reason,
        "revision_scope": revision_scope,
        "technical_reexecution": technical,
        "audit_reexecution": audit,
        "revision_evidence_refs": evidence,
        "execution_receipt_reuse": {
            "enabled": True,
            "source_packet_id": source_packet["packet_id"],
            "source_packet_ref": f"{PACKETS_DIR}/{source_packet['packet_id']}.json",
            "source_packet_digest": contracts.contract_digest(source_packet),
            "source_receipt_id": source_receipt["receipt_id"],
            "source_receipt_ref": f"{RECEIPTS_DIR}/{source_receipt['receipt_id']}.json",
            "source_receipt_digest": contracts.contract_digest(source_receipt),
        },
    }

def build_execution_packet(
    plan: dict[str, Any],
    checklist: str,
    phase_id: str,
    work_item_id: str,
    *,
    state_root: Path | None = None,
    instance: Path | None = None,
    revision_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one deterministic ExecutionPacket without dispatching it."""
    try:
        contracts.validate_plan_package(plan)
    except workflow.ContractError as exc:
        raise PlanningError("INVALID_CONTRACT", f"invalid PlanPackage: {exc}") from exc
    phase, work_item = _find_phase_and_work_item(plan, checklist, phase_id, work_item_id)
    _ensure_midcourse_gate_allows_phase(
        plan,
        phase_id,
        state_root=state_root,
        instance=instance,
    )
    skill_ref, dispatch_status = _selected_skill_ref(plan)
    objective = str(phase.get("objective") or work_item.get("阶段/任务") or work_item_id)
    allowed_scope = copy.deepcopy(plan.get("scope", {}))
    forbidden_scope = copy.deepcopy(plan.get("forbidden_scope", {}))
    if not isinstance(forbidden_scope, dict):
        forbidden_scope = {}
    forbidden_scope.setdefault("include", [])
    forbidden_scope.setdefault("exclude", [])
    forbidden_scope["include"] = _append_unique(
        forbidden_scope["include"], plan.get("forbidden_capabilities", [])
    )
    forbidden_scope["exclude"] = _append_unique(
        forbidden_scope["exclude"], plan.get("non_goals", [])
    )
    completion_conditions = copy.deepcopy(phase.get("completion_conditions", []))
    failure_conditions = copy.deepcopy(phase.get("failure_conditions", []))
    pause_conditions = copy.deepcopy(phase.get("pause_conditions", []))
    evidence_requirements = copy.deepcopy(plan.get("evidence_policy", {}))
    evidence_requirements["work_item_evidence"] = work_item.get("证据要求", "")
    identity = _packet_identity_payload(
        plan,
        phase,
        work_item,
        skill_ref,
        allowed_scope,
        forbidden_scope,
        objective,
        completion_conditions,
        evidence_requirements,
        revision_metadata,
    )
    digest = contracts.contract_digest(identity)
    revision_suffix = f"-r{revision_metadata['revision']}" if revision_metadata else ""
    packet_id = f"{PACKET_ID_PREFIX}{_safe_component(plan['task_id'], 'task_id')}-{_safe_component(phase_id, 'phase_id')}{revision_suffix}-{digest[:12]}"
    packet = {
        "schema_version": contracts.PLAN_CONTRACT_SCHEMA_VERSION,
        "packet_id": packet_id,
        "plan_id": plan["plan_id"],
        "plan_version": plan["plan_version"],
        "task_id": plan["task_id"],
        "phase_id": phase_id,
        "work_item_id": work_item_id,
        "skill_ref": skill_ref,
        "objective": objective,
        "allowed_scope": allowed_scope,
        "forbidden_scope": forbidden_scope,
        "inputs": {
            "task_envelope": "task-envelope.json",
            "plan_package": "plan-package.json",
            "work_item_id": work_item_id,
        },
        "expected_outputs": {
            "execution_receipt": f"{RECEIPTS_DIR}/<receipt-id>.json",
            "work_item_id": work_item_id,
        },
        "completion_conditions": completion_conditions,
        "failure_conditions": failure_conditions,
        "pause_conditions": pause_conditions,
        "evidence_requirements": evidence_requirements,
        "permissions": {
            "allowed_scope": copy.deepcopy(allowed_scope),
            "forbidden_scope": copy.deepcopy(forbidden_scope),
            "dispatch": "NOT_PERFORMED",
        },
        "governance_requirements": {
            "required_stages": copy.deepcopy(plan.get("governance_policy", {}).get("required_stages", [])),
            "receipt_required": True,
            "integration_status": "RESERVED_ONLY",
        },
        "timeout_policy": copy.deepcopy(plan.get("timeout_policy", {"mode": "manual"})),
        "receipt_requirements": {
            "schema_version": contracts.PLAN_CONTRACT_SCHEMA_VERSION,
            "required": True,
            "path": f"{RECEIPTS_DIR}/<receipt-id>.json",
            "idempotent": True,
        },
        "created_at": plan["created_at"],
        "producer": "planning-with-files",
        "producer_version": CURRENT_VERSION,
        "dispatch_status": dispatch_status,
        "checkpoint_refs": [],
        "knowledge_handoff_ref": None,
    }
    if revision_metadata:
        packet.update(copy.deepcopy(revision_metadata))
        packet["inputs"]["revision_sources"] = copy.deepcopy(revision_metadata["revision_evidence_refs"])
        packet["receipt_requirements"]["required"] = False
        packet["receipt_requirements"]["reused_execution_receipt"] = copy.deepcopy(
            revision_metadata["execution_receipt_reuse"]
        )
    try:
        contracts.validate_execution_packet(packet)
    except workflow.ContractError as exc:
        raise PlanningError("INVALID_CONTRACT", f"generated ExecutionPacket invalid: {exc}") from exc
    return packet

def _packet_digest(packet: dict[str, Any]) -> str:
    return contracts.contract_digest(packet, exclude_fields=("packet_id", "created_at"))

def create_packet(
    instance_root: str | Path,
    phase_id: str,
    work_item_id: str,
    *,
    preview: bool = False,
    apply: bool = False,
    agent: str = "planning-with-files",
    revision: int | None = None,
    revision_type: str | None = None,
    predecessor_checkpoint_id: str | None = None,
    revision_reason: str | None = None,
    revision_scope: str | None = None,
    technical_reexecution: bool | None = None,
    audit_reexecution: bool | None = None,
    revision_evidence_refs: Iterable[str] = (),
) -> dict[str, Any]:
    """Create one deterministic ExecutionPacket; never dispatch a Skill."""
    try:
        if preview == apply:
            raise PlanningError("INVALID_MODE", "exactly one of preview or apply must be true")
        state_root, instance, _envelope, plan, checklist = _load_instance(instance_root)
        _ensure_midcourse_gate_evidence(state_root, instance, plan)
        revision_metadata = _build_revision_metadata(
            instance,
            plan,
            phase_id,
            work_item_id,
            revision=revision,
            revision_type=revision_type,
            predecessor_checkpoint_id=predecessor_checkpoint_id,
            revision_reason=revision_reason,
            revision_scope=revision_scope,
            technical_reexecution=technical_reexecution,
            audit_reexecution=audit_reexecution,
            revision_evidence_refs=revision_evidence_refs,
        )
        packet = build_execution_packet(
            plan,
            checklist,
            phase_id,
            work_item_id,
            state_root=state_root,
            instance=instance,
            revision_metadata=revision_metadata,
        )
        if revision_metadata:
            existing_revisions = [
                item for item in _load_phase_packets(instance, plan, phase_id)
                if item.get("work_item_id") == work_item_id
                and item.get("revision") == revision_metadata["revision"]
            ]
            if existing_revisions and all(contracts.stable_json(item) != contracts.stable_json(packet) for item in existing_revisions):
                raise PlanningError("REVISION_CONFLICT", "same task/phase/revision already exists with different content", result="CONFLICT")
        packet_path = instance / PACKETS_DIR / f"{packet['packet_id']}.json"
        packet_digest = _packet_digest(packet)
        result: dict[str, Any] = {
            "result": "PREVIEW" if preview else "CREATED",
            "packet_id": packet["packet_id"],
            "packet_digest": packet_digest,
            "packet_path": str(packet_path),
            "packet": packet,
            "dispatch_status": packet["dispatch_status"],
            "created_files": [],
            "no_op": False,
            "state_root": str(state_root),
            "instance_path": str(instance),
        }
        if packet_path.exists():
            existing = _read_json(packet_path)
            try:
                contracts.validate_execution_packet(existing)
            except workflow.ContractError as exc:
                raise PlanningError("PACKET_ID_CONFLICT", f"existing packet is invalid: {exc}", result="CONFLICT") from exc
            if contracts.stable_json(existing) != contracts.stable_json(packet):
                raise PlanningError("PACKET_ID_CONFLICT", "same packet_id has different content", result="CONFLICT")
            result.update({"result": "EXISTING_PACKET", "packet": existing, "no_op": True})
            return result
        if preview:
            return result
        relative = f"{PACKETS_DIR}/{packet['packet_id']}.json"
        _transaction_write(
            instance,
            state_root,
            {relative: contracts.stable_json(packet)},
            expected_digests={relative: workflow.sha256_digest("")},
            lock_target=relative,
            lock_name="packet",
            agent=agent,
        )
        stored = _read_json(packet_path)
        contracts.validate_execution_packet(stored)
        if contracts.stable_json(stored) != contracts.stable_json(packet):
            raise PlanningError("FAILED", "published ExecutionPacket changed unexpectedly")
        result["created_files"] = [relative]
        return result
    except PlanningError as exc:
        return _result_error(exc)
    except (OSError, ValueError, workflow.ContractError) as exc:
        return _result_error(PlanningError("INTERNAL_ERROR", str(exc)))
