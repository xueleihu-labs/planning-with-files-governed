"""Gate 2 extracted module: evolution.py.

Generated from the Gate 1 planning.py baseline.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import copy
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
from pwf_governed.core.constants import (
    CONTENT_RECEIPTS_DIR,
    EVOLUTION_RECEIPTS_DIR,
    EVOLUTION_SIGNAL_RELATIVE,
    KNOWLEDGE_HANDOFF_RELATIVE,
    PUBLICATION_DESTINATION_SYSTEM,
    SKILL_ROOT,
)
from pwf_governed.core.envelope import (
    _load_instance,
    _read_json,
    _result_error,
    _transaction_write,
)
from pwf_governed.core.errors import (
    PlanningError,
)
from pwf_governed.edition import adapt_input_once, current_edition, edition_operation
from pwf_governed.outcomes import (
    _load_routing_decision,
    _outcome_digest,
    _outcome_state_update,
    _outcome_target,
    _routing_decision_location,
)

def _receipt_conflict_path(instance: Path, relative: str, incoming: dict[str, Any], kind: str, identity_field: str) -> tuple[str, dict[str, Any] | None]:
    target = _outcome_target(instance, relative)
    if target.exists():
        existing = _read_json(target, code=f"INVALID_{kind.upper()}")
        try:
            contracts.validate_contract(kind, existing)
        except workflow.ContractError as exc:
            raise PlanningError(f"INVALID_{kind.upper()}", str(exc), result="CONFLICT") from exc
        if _outcome_digest(existing, exclude=("processed_at", "ingested_at")) == _outcome_digest(incoming, exclude=("processed_at", "ingested_at")):
            return "EXISTING", existing
        raise PlanningError(f"{kind.upper()}_ID_CONFLICT", f"same {identity_field} has different content", result="CONFLICT")
    return "NEW", None

def _update_routing_receipt_reference(decision: dict[str, Any], *, field: str, ref: str) -> dict[str, Any]:
    updated = copy.deepcopy(decision)
    updated[field] = ref
    contracts.validate_routing_decision(updated)
    return updated

def record_evolution_receipt(
    instance_root: str | Path,
    receipt_path: str | Path,
    *,
    preview: bool = False,
    apply: bool = False,
    agent: str = "planning-with-files",
) -> dict[str, Any]:
    """Validate and store an external EvolutionReceipt without invoking bridge systems."""
    try:
        if preview == apply:
            raise PlanningError("INVALID_MODE", "exactly one of preview or apply must be true")
        state_root, instance, envelope, plan, checklist = _load_instance(instance_root)
        incoming = _read_json(Path(receipt_path).expanduser(), code="INVALID_EVOLUTION_RECEIPT")
        try:
            contracts.validate_evolution_receipt(incoming)
        except workflow.ContractError as exc:
            raise PlanningError("INVALID_EVOLUTION_RECEIPT", str(exc)) from exc
        metadata = workflow.extract_machine_json(checklist, "workflow")
        checkpoint_id = metadata.get("last_trusted_checkpoint")
        decision_path = _routing_decision_location(instance, checkpoint_id)
        decision = _load_routing_decision(instance, checkpoint_id)
        signal_relative = str(decision.get("evolution_signal_ref") or EVOLUTION_SIGNAL_RELATIVE)
        signal_path = _outcome_target(instance, signal_relative)
        if not signal_path.is_file():
            raise PlanningError("EVOLUTION_SIGNAL_NOT_FOUND", "evolution signal does not exist")
        signal = _read_json(signal_path, code="INVALID_EVOLUTION_SIGNAL")
        contracts.validate_evolution_signal(signal)
        if signal.get("checkpoint_id") != checkpoint_id:
            raise PlanningError("STALE_CHECKPOINT", "evolution signal does not belong to the current trusted checkpoint", result="CONFLICT")
        if incoming["signal_id"] != signal["signal_id"] or incoming["dedupe_key"] != signal["dedupe_key"]:
            raise PlanningError("EVOLUTION_RECEIPT_MISMATCH", "receipt signal_id or dedupe_key does not match signal")
        if incoming["task_id"] != envelope["task_id"] or incoming["plan_id"] != plan["plan_id"]:
            raise PlanningError("REFERENCE_MISMATCH", "evolution receipt task/plan does not match instance")
        receipt_relative = f"{EVOLUTION_RECEIPTS_DIR}/{incoming['receipt_id']}.json"
        status, existing = _receipt_conflict_path(instance, receipt_relative, incoming, "evolution_receipt", "receipt_id")
        if status == "EXISTING":
            return {"result": "EXISTING_EVOLUTION_RECEIPT", "receipt_id": incoming["receipt_id"], "receipt_path": str(_outcome_target(instance, receipt_relative)), "receipt": existing, "no_op": True, "idempotent": True}
        if decision.get("evolution_signal_ref") != signal_relative:
            raise PlanningError("EVOLUTION_RECEIPT_MISMATCH", "routing decision does not reference evolution signal")
        updated_decision = _update_routing_receipt_reference(decision, field="evolution_receipt_ref", ref=receipt_relative)
        decision_relative = decision_path.relative_to(instance).as_posix()
        updated_checklist, state_update = _outcome_state_update(
            checklist,
            updated_decision,
            outcome_ref=decision_relative,
            receipt_kind="evolution",
            receipt_ref=receipt_relative,
            receipt_result=incoming["result"],
        )
        files = {
            receipt_relative: contracts.stable_json(incoming),
            decision_relative: contracts.stable_json(updated_decision),
            workflow.CHECKLIST_NAME: updated_checklist,
        }
        result: dict[str, Any] = {
            "result": "PREVIEW" if preview else "RECORDED_EVOLUTION_RECEIPT",
            "receipt_id": incoming["receipt_id"],
            "receipt_path": str(_outcome_target(instance, receipt_relative)),
            "decision_path": _outcome_target(instance, decision_relative).as_posix(),
            "state_update": state_update,
            "planned_files": sorted(files),
            "created_files": [],
            "external_calls": [],
            "no_op": False,
        }
        if preview:
            return result
        expected = {
            receipt_relative: workflow.sha256_digest(""),
            decision_relative: workflow.file_digest(_outcome_target(instance, decision_relative)),
            workflow.CHECKLIST_NAME: workflow.file_digest(instance / workflow.CHECKLIST_NAME),
        }
        _transaction_write(instance, state_root, files, expected_digests=expected, lock_target=workflow.CHECKLIST_NAME, lock_name="outcome-evolution-receipt", agent=agent, transaction_tag="f1-06-evolution")
        stored = _read_json(_outcome_target(instance, receipt_relative), code="INVALID_EVOLUTION_RECEIPT")
        contracts.validate_evolution_receipt(stored)
        result["created_files"] = sorted(files)
        return result
    except PlanningError as exc:
        return _result_error(exc)
    except (OSError, ValueError, workflow.ContractError) as exc:
        return _result_error(PlanningError("INTERNAL_ERROR", str(exc)))

def _validate_content_destination(destination_path: str, state_root: Path, instance: Path) -> None:
    candidate = Path(destination_path).expanduser()
    if not candidate.is_absolute():
        raise PlanningError("CONTENT_DESTINATION_NOT_ALLOWED", "destination_path must be absolute")
    resolved = candidate.resolve(strict=False)
    protected = (state_root.resolve(), instance.resolve(), SKILL_ROOT.resolve())
    if any(resolved == root or root in resolved.parents for root in protected):
        raise PlanningError("CONTENT_DESTINATION_NOT_ALLOWED", "destination_path points into the source state or Skill root")

@edition_operation
def record_content_ingest_receipt(
    instance_root: str | Path,
    receipt_path: str | Path,
    *,
    preview: bool = False,
    apply: bool = False,
    agent: str = "planning-with-files",
) -> dict[str, Any]:
    """Validate and store an external ContentIngestReceipt without calling the publisher."""
    try:
        if preview == apply:
            raise PlanningError("INVALID_MODE", "exactly one of preview or apply must be true")
        state_root, instance, envelope, plan, checklist = _load_instance(instance_root)
        incoming = _read_json(Path(receipt_path).expanduser(), code="INVALID_CONTENT_INGEST_RECEIPT")
        try:
            contracts.validate_content_ingest_receipt(incoming)
        except workflow.ContractError as exc:
            raise PlanningError("INVALID_CONTENT_INGEST_RECEIPT", str(exc)) from exc
        _validate_content_destination(incoming["destination_path"], state_root, instance)
        metadata = workflow.extract_machine_json(checklist, "workflow")
        checkpoint_id = metadata.get("last_trusted_checkpoint")
        decision_path = _routing_decision_location(instance, checkpoint_id)
        decision = _load_routing_decision(instance, checkpoint_id)
        handoff_relative = str(decision.get("knowledge_handoff_ref") or KNOWLEDGE_HANDOFF_RELATIVE)
        handoff_path = _outcome_target(instance, handoff_relative)
        if not handoff_path.is_file():
            raise PlanningError("KNOWLEDGE_HANDOFF_NOT_FOUND", "knowledge_handoff does not exist")
        handoff = adapt_input_once(
            _read_json(handoff_path, code="INVALID_KNOWLEDGE_HANDOFF"),
            payload_kind="KnowledgeHandoff",
            callsite_id="handoff-read",
        )
        contracts.validate_knowledge_handoff_package(handoff)
        if incoming["handoff_id"] != handoff["handoff_id"] or incoming["dedupe_key"] != handoff["dedupe_key"]:
            raise PlanningError("CONTENT_RECEIPT_MISMATCH", "receipt handoff_id or dedupe_key does not match handoff")
        if incoming["task_id"] != envelope["task_id"] or incoming["plan_id"] != plan["plan_id"]:
            raise PlanningError("REFERENCE_MISMATCH", "content receipt task/plan does not match instance")
        destination_system = current_edition().publication_destination
        if incoming["destination_system"] != destination_system:
            raise PlanningError(
                "CONTENT_DESTINATION_NOT_ALLOWED",
                f"destination_system must be {destination_system}",
            )
        receipt_relative = f"{CONTENT_RECEIPTS_DIR}/{incoming['receipt_id']}.json"
        status, existing = _receipt_conflict_path(instance, receipt_relative, incoming, "content_ingest_receipt", "receipt_id")
        if status == "EXISTING":
            return {"result": "EXISTING_CONTENT_INGEST_RECEIPT", "receipt_id": incoming["receipt_id"], "receipt_path": str(_outcome_target(instance, receipt_relative)), "receipt": existing, "no_op": True, "idempotent": True}
        if decision.get("knowledge_handoff_ref") != handoff_relative:
            raise PlanningError("CONTENT_RECEIPT_MISMATCH", "routing decision does not reference knowledge_handoff")
        updated_decision = _update_routing_receipt_reference(decision, field="content_ingest_receipt_ref", ref=receipt_relative)
        decision_relative = decision_path.relative_to(instance).as_posix()
        updated_checklist, state_update = _outcome_state_update(
            checklist,
            updated_decision,
            outcome_ref=decision_relative,
            receipt_kind="content",
            receipt_ref=receipt_relative,
            receipt_result=incoming["result"],
        )
        files = {
            receipt_relative: contracts.stable_json(incoming),
            decision_relative: contracts.stable_json(updated_decision),
            workflow.CHECKLIST_NAME: updated_checklist,
        }
        result: dict[str, Any] = {
            "result": "PREVIEW" if preview else "RECORDED_CONTENT_INGEST_RECEIPT",
            "receipt_id": incoming["receipt_id"],
            "receipt_path": str(_outcome_target(instance, receipt_relative)),
            "decision_path": _outcome_target(instance, decision_relative).as_posix(),
            "state_update": state_update,
            "planned_files": sorted(files),
            "created_files": [],
            "external_calls": [],
            "no_op": False,
        }
        if preview:
            return result
        expected = {
            receipt_relative: workflow.sha256_digest(""),
            decision_relative: workflow.file_digest(_outcome_target(instance, decision_relative)),
            workflow.CHECKLIST_NAME: workflow.file_digest(instance / workflow.CHECKLIST_NAME),
        }
        _transaction_write(instance, state_root, files, expected_digests=expected, lock_target=workflow.CHECKLIST_NAME, lock_name="outcome-content-receipt", agent=agent, transaction_tag="f1-06-content")
        stored = _read_json(_outcome_target(instance, receipt_relative), code="INVALID_CONTENT_INGEST_RECEIPT")
        contracts.validate_content_ingest_receipt(stored)
        result["created_files"] = sorted(files)
        return result
    except PlanningError as exc:
        return _result_error(exc)
    except (OSError, ValueError, workflow.ContractError) as exc:
        return _result_error(PlanningError("INTERNAL_ERROR", str(exc)))
