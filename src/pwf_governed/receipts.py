"""Gate 2 extracted module: receipts.py.

Generated from the Gate 1 planning.py baseline.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import copy
import datetime as dt

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
    GOVERNANCE_RECEIPTS_DIR,
    PACKETS_DIR,
    RECEIPTS_DIR,
)
from pwf_governed.core.envelope import (
    _append_unique,
    _load_instance,
    _parse_timestamp,
    _read_json,
    _result_error,
    _safe_component,
    _transaction_write,
)
from pwf_governed.core.errors import (
    PlanningError,
)
from pwf_governed.governance import (
    _governance_receipt_digest,
    _governance_receipt_path,
    _governance_request_path,
    _governance_state_update,
    _load_cleanliness_receipts,
    _validate_governance_candidate_actions,
)

def _load_receipts(instance: Path) -> list[dict[str, Any]]:
    receipts_dir = instance / RECEIPTS_DIR
    if not receipts_dir.exists():
        return []
    result: list[dict[str, Any]] = []
    for path in sorted(receipts_dir.glob("*.json")):
        if path.name == "create-plan.json":
            continue
        if path.is_symlink():
            raise PlanningError("UNSAFE_INSTANCE_ROOT", f"receipt cannot be a symlink: {path}")
        value = _read_json(path)
        try:
            contracts.validate_execution_receipt(value)
        except workflow.ContractError as exc:
            raise PlanningError("INVALID_RECEIPT", f"invalid existing receipt {path}: {exc}") from exc
        result.append(value)
    return result

def _validate_receipt_against_packet(receipt: dict[str, Any], packet: dict[str, Any]) -> None:
    try:
        contracts.validate_execution_receipt(receipt)
    except workflow.ContractError as exc:
        raise PlanningError("INVALID_RECEIPT", str(exc)) from exc
    for field in ("packet_id", "plan_id", "plan_version", "task_id", "phase_id", "work_item_id"):
        if receipt[field] != packet[field]:
            raise PlanningError("RECEIPT_MISMATCH", f"receipt {field} does not match packet")
    if contracts.stable_json(receipt["skill_ref"]) != contracts.stable_json(packet["skill_ref"]):
        raise PlanningError("RECEIPT_MISMATCH", "receipt skill_ref does not match packet")
    try:
        started = _parse_timestamp(receipt["started_at"])
        completed = _parse_timestamp(receipt["completed_at"])
    except (TypeError, ValueError) as exc:
        raise PlanningError("INVALID_RECEIPT", "receipt timestamps are invalid") from exc
    if completed < started:
        raise PlanningError("INVALID_RECEIPT_TIME", "completed_at must not precede started_at")

def _check_receipt_conflicts(instance: Path, receipt: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    receipt_path = instance / RECEIPTS_DIR / f"{receipt['receipt_id']}.json"
    if receipt_path.exists():
        existing = _read_json(receipt_path)
        try:
            contracts.validate_execution_receipt(existing)
        except workflow.ContractError as exc:
            raise PlanningError("RECEIPT_ID_CONFLICT", f"existing receipt is invalid: {exc}", result="CONFLICT") from exc
        if contracts.stable_json(existing) == contracts.stable_json(receipt):
            return "EXISTING_RECEIPT", existing
        raise PlanningError("RECEIPT_ID_CONFLICT", "same receipt_id has different content", result="CONFLICT")
    for existing in _load_receipts(instance):
        if existing["packet_id"] != receipt["packet_id"]:
            continue
        if contracts.receipts_are_idempotent(existing, receipt):
            return "EXISTING_RECEIPT", existing
        raise PlanningError("RECEIPT_RESULT_CONFLICT", "packet already has a conflicting terminal receipt", result="CONFLICT")
    return "NEW_RECEIPT", None

def _checklist_row_location(text: str, work_item_id: str) -> tuple[list[str], int, list[str], dict[str, int]]:
    lines = text.splitlines()
    for header_index, line in enumerate(lines[:-1]):
        if not line.strip().startswith("|"):
            continue
        headers = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not {"ID", "状态", "核验状态", "下一步"}.issubset(headers):
            continue
        divider = [cell.strip() for cell in lines[header_index + 1].strip().strip("|").split("|")]
        if not all(set(cell) <= {"-", ":", " "} for cell in divider):
            continue
        for row_index in range(header_index + 2, len(lines)):
            raw = lines[row_index]
            if not raw.strip().startswith("|"):
                break
            cells = [cell.strip() for cell in raw.strip().strip("|").split("|")]
            if len(cells) != len(headers):
                break
            if cells[headers.index("ID")] == work_item_id:
                return lines, row_index, cells, {name: headers.index(name) for name in headers}
        break
    raise PlanningError("INVALID_WORK_ITEM_ID", f"work_item_id row not found: {work_item_id}")

def _validate_cleanliness_receipt_against_request(receipt: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    try:
        contracts.validate_governance_receipt(receipt)
    except workflow.ContractError as exc:
        raise PlanningError("INVALID_CLEANLINESS_RECEIPT", str(exc)) from exc
    _validate_governance_candidate_actions(receipt)
    for field in ("request_id", "task_id", "plan_id", "phase_id", "governance_stage"):
        if receipt[field] != request[field]:
            raise PlanningError("CLEANLINESS_RECEIPT_MISMATCH", f"receipt {field} does not match GovernanceRequest")
    return contracts.governance_decision(receipt)

def _check_cleanliness_receipt_conflicts(instance: Path, receipt: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    receipt_path = _governance_receipt_path(instance, receipt["receipt_id"])
    if receipt_path.exists():
        if receipt_path.is_symlink():
            raise PlanningError("UNSAFE_INSTANCE_ROOT", "cleanliness receipt cannot be a symlink")
        existing = _read_json(receipt_path, code="INVALID_CLEANLINESS_RECEIPT")
        try:
            contracts.validate_governance_receipt(existing)
        except workflow.ContractError as exc:
            raise PlanningError("CLEANLINESS_RECEIPT_ID_CONFLICT", f"existing receipt is invalid: {exc}", result="CONFLICT") from exc
        if _governance_receipt_digest(existing) == _governance_receipt_digest(receipt):
            return "EXISTING_CLEANLINESS_RECEIPT", existing
        raise PlanningError("CLEANLINESS_RECEIPT_ID_CONFLICT", "same receipt_id has different content", result="CONFLICT")
    for existing in _load_cleanliness_receipts(instance):
        if existing["request_id"] != receipt["request_id"]:
            continue
        if _governance_receipt_digest(existing) == _governance_receipt_digest(receipt):
            return "EXISTING_CLEANLINESS_RECEIPT", existing
        raise PlanningError("CLEANLINESS_RECEIPT_CONFLICT", "request already has a different cleanliness receipt", result="CONFLICT")
    return "NEW_CLEANLINESS_RECEIPT", None

def record_cleanliness_receipt(
    instance_root: str | Path,
    receipt_path: str | Path,
    *,
    preview: bool = False,
    apply: bool = False,
    agent: str = "planning-with-files",
) -> dict[str, Any]:
    """Validate and consume one local CleanlinessReceipt; never call a governance Skill."""
    try:
        if preview == apply:
            raise PlanningError("INVALID_MODE", "exactly one of preview or apply must be true")
        state_root, instance, _envelope, _plan, checklist = _load_instance(instance_root)
        receipt = _read_json(Path(receipt_path).expanduser(), code="INVALID_CLEANLINESS_RECEIPT")
        if "request_id" not in receipt:
            raise PlanningError("INVALID_CLEANLINESS_RECEIPT", "receipt missing request_id")
        request_path = _governance_request_path(instance, receipt["request_id"])
        if not request_path.is_file() or request_path.is_symlink():
            raise PlanningError("GOVERNANCE_REQUEST_NOT_FOUND", f"governance request does not exist: {receipt['request_id']}")
        request = _read_json(request_path, code="INVALID_GOVERNANCE_REQUEST")
        try:
            contracts.validate_governance_request(request)
        except workflow.ContractError as exc:
            raise PlanningError("INVALID_GOVERNANCE_REQUEST", str(exc)) from exc
        decision = _validate_cleanliness_receipt_against_request(receipt, request)
        conflict_status, existing = _check_cleanliness_receipt_conflicts(instance, receipt)
        receipt_target = _governance_receipt_path(instance, receipt["receipt_id"])
        receipt_relative = f"{GOVERNANCE_RECEIPTS_DIR}/{receipt['receipt_id']}.json"
        if conflict_status == "EXISTING_CLEANLINESS_RECEIPT":
            return {
                "result": "EXISTING_CLEANLINESS_RECEIPT",
                "receipt_id": receipt["receipt_id"],
                "receipt_path": str(receipt_target),
                "request_id": receipt["request_id"],
                "no_op": True,
                "idempotent": True,
                "decision": decision,
                "state_update": {},
            }
        updated_checklist, state_update = _governance_state_update(
            checklist,
            request,
            receipt,
            receipt_relative,
        )
        result: dict[str, Any] = {
            "result": "PREVIEW" if preview else "RECORDED_CLEANLINESS_RECEIPT",
            "receipt_id": receipt["receipt_id"],
            "receipt_path": str(receipt_target),
            "request_id": receipt["request_id"],
            "request_path": str(request_path),
            "decision": decision,
            "state_update": state_update,
            "planned_files": [receipt_relative, workflow.CHECKLIST_NAME],
            "created_files": [],
            "no_op": False,
            "warnings": copy.deepcopy(receipt["non_blocking_findings"]),
            "blocking_findings": copy.deepcopy(receipt["blocking_findings"]),
            "state_root": str(state_root),
            "instance_path": str(instance),
        }
        if preview:
            return result
        expected = {
            receipt_relative: workflow.sha256_digest(""),
            workflow.CHECKLIST_NAME: workflow.file_digest(instance / workflow.CHECKLIST_NAME),
        }
        _transaction_write(
            instance,
            state_root,
            {
                receipt_relative: contracts.stable_json(receipt),
                workflow.CHECKLIST_NAME: updated_checklist,
            },
            expected_digests=expected,
            lock_target=workflow.CHECKLIST_NAME,
            lock_name="cleanliness-receipt",
            agent=agent,
            transaction_tag="f1-04",
        )
        stored = _read_json(receipt_target, code="INVALID_CLEANLINESS_RECEIPT")
        contracts.validate_governance_receipt(stored)
        workflow.validate_checklist_text((instance / workflow.CHECKLIST_NAME).read_text(encoding="utf-8"))
        result["created_files"] = [receipt_relative, workflow.CHECKLIST_NAME]
        return result
    except PlanningError as exc:
        return _result_error(exc)
    except (OSError, ValueError, workflow.ContractError) as exc:
        return _result_error(PlanningError("INTERNAL_ERROR", str(exc)))

def bind_postwrite_execution_receipt(
    instance_root: str | Path,
    *,
    state_root: str | Path,
    task_id: str,
    plan_id: str,
    phase_id: str,
    postwrite_receipt_id: str,
    execution_receipt_id: str,
    preview: bool = False,
    apply: bool = False,
    agent: str = "planning-with-files",
) -> dict[str, Any]:
    """Bind one existing POST_WRITE receipt to its completed execution receipt."""
    try:
        if preview == apply:
            raise PlanningError("INVALID_MODE", "exactly one of preview or apply must be true")
        actual_state_root, instance, envelope, plan, _checklist = _load_instance(instance_root)
        explicit_state_root = Path(state_root).expanduser().resolve(strict=False)
        if explicit_state_root != actual_state_root.resolve(strict=False):
            raise PlanningError("STATE_ROOT_MISMATCH", "explicit state-root does not match the PLAN instance")
        if task_id != envelope["task_id"] or task_id != plan["task_id"]:
            raise PlanningError("TASK_ID_MISMATCH", "task_id does not match the loaded TaskEnvelope and PlanPackage")
        if plan_id != plan["plan_id"]:
            raise PlanningError("PLAN_ID_MISMATCH", "plan_id does not match the loaded PlanPackage")
        if phase_id not in {str(item.get("phase_id")) for item in plan.get("phases", []) if isinstance(item, dict)}:
            raise PlanningError("PHASE_ID_MISMATCH", f"phase_id is not defined by the PlanPackage: {phase_id}")
        for label, value in (
            ("postwrite_receipt_id", postwrite_receipt_id),
            ("execution_receipt_id", execution_receipt_id),
        ):
            _safe_component(value, label)

        postwrite_path = _governance_receipt_path(instance, postwrite_receipt_id)
        if not postwrite_path.is_file() or postwrite_path.is_symlink():
            raise PlanningError("POSTWRITE_RECEIPT_NOT_FOUND", f"POST_WRITE receipt does not exist: {postwrite_receipt_id}")
        postwrite = _read_json(postwrite_path, code="INVALID_CLEANLINESS_RECEIPT")
        try:
            contracts.validate_governance_receipt(postwrite)
        except workflow.ContractError as exc:
            raise PlanningError("INVALID_CLEANLINESS_RECEIPT", str(exc)) from exc
        if postwrite.get("receipt_id") != postwrite_receipt_id:
            raise PlanningError("POSTWRITE_RECEIPT_MISMATCH", "POST_WRITE receipt identity does not match its path")
        if postwrite.get("governance_stage") != "POST_WRITE":
            raise PlanningError("POSTWRITE_STAGE_REQUIRED", "only a POST_WRITE receipt can receive an execution binding")
        request_path = _governance_request_path(instance, str(postwrite.get("request_id", "")))
        if not request_path.is_file() or request_path.is_symlink():
            raise PlanningError("GOVERNANCE_REQUEST_NOT_FOUND", f"governance request does not exist: {postwrite.get('request_id')}")
        request = _read_json(request_path, code="INVALID_GOVERNANCE_REQUEST")
        try:
            contracts.validate_governance_request(request)
        except workflow.ContractError as exc:
            raise PlanningError("INVALID_GOVERNANCE_REQUEST", str(exc)) from exc
        for field, expected in (
            ("task_id", task_id),
            ("plan_id", plan_id),
            ("phase_id", phase_id),
            ("governance_stage", "POST_WRITE"),
        ):
            if postwrite.get(field) != expected or request.get(field) != expected:
                raise PlanningError("POSTWRITE_CONTEXT_MISMATCH", f"POST_WRITE receipt/request {field} does not match the requested context")
        if postwrite.get("request_id") != request.get("request_id"):
            raise PlanningError("POSTWRITE_CONTEXT_MISMATCH", "POST_WRITE receipt does not match its GovernanceRequest")

        execution_dir = instance / RECEIPTS_DIR
        if execution_dir.is_symlink() or not execution_dir.is_dir():
            raise PlanningError("EXECUTION_RECEIPT_NOT_FOUND", "execution receipts directory is not a real directory")
        execution_path = execution_dir / f"{execution_receipt_id}.json"
        if not execution_path.is_file() or execution_path.is_symlink():
            raise PlanningError("EXECUTION_RECEIPT_NOT_FOUND", f"execution receipt does not exist: {execution_receipt_id}")
        execution = _read_json(execution_path, code="INVALID_RECEIPT")
        try:
            contracts.validate_execution_receipt(execution)
        except workflow.ContractError as exc:
            raise PlanningError("INVALID_RECEIPT", str(exc)) from exc
        if execution.get("receipt_id") != execution_receipt_id:
            raise PlanningError("EXECUTION_RECEIPT_MISMATCH", "execution receipt identity does not match its path")
        packet_id = execution.get("packet_id")
        if not isinstance(packet_id, str) or not packet_id:
            raise PlanningError("EXECUTION_RECEIPT_MISMATCH", "execution receipt packet_id is missing")
        packet_path = instance / PACKETS_DIR / f"{packet_id}.json"
        if not packet_path.is_file() or packet_path.is_symlink():
            raise PlanningError("PACKET_NOT_FOUND", f"execution receipt packet does not exist: {packet_id}")
        packet = _read_json(packet_path, code="INVALID_PACKET")
        try:
            contracts.validate_execution_packet(packet)
        except workflow.ContractError as exc:
            raise PlanningError("INVALID_PACKET", str(exc)) from exc
        _validate_receipt_against_packet(execution, packet)
        for field, expected in (("task_id", task_id), ("plan_id", plan_id), ("phase_id", phase_id)):
            if execution.get(field) != expected or packet.get(field) != expected:
                raise PlanningError("EXECUTION_RECEIPT_MISMATCH", f"execution receipt/packet {field} does not match the requested context")
        if execution.get("result") not in {"PASS", "PASS_WITH_WARNINGS"} or not execution.get("completed_at"):
            raise PlanningError("EXECUTION_RECEIPT_NOT_COMPLETE", "execution receipt must be a completed PASS or PASS_WITH_WARNINGS result")

        expected_execution_digest = contracts.contract_digest(execution)
        current_execution_id = postwrite.get("execution_receipt_id")
        current_execution_digest = postwrite.get("execution_receipt_digest")
        if current_execution_id == execution_receipt_id:
            if current_execution_digest != expected_execution_digest:
                raise PlanningError(
                    "POSTWRITE_EXECUTION_BINDING_CONFLICT",
                    "POST_WRITE receipt already names the execution receipt with a different digest",
                    result="CONFLICT",
                )
            return {
                "result": "EXISTING_POSTWRITE_EXECUTION_BINDING",
                "postwrite_receipt_id": postwrite_receipt_id,
                "postwrite_receipt_path": str(postwrite_path),
                "execution_receipt_id": execution_receipt_id,
                "execution_receipt_path": str(execution_path),
                "execution_receipt_digest": expected_execution_digest,
                "no_op": True,
                "idempotent": True,
                "state_root": str(actual_state_root),
                "instance_path": str(instance),
            }
        if current_execution_id not in {None, ""} or current_execution_digest not in {None, ""}:
            raise PlanningError(
                "POSTWRITE_EXECUTION_BINDING_CONFLICT",
                "POST_WRITE receipt already contains a different or incomplete execution binding",
                result="CONFLICT",
            )

        updated = copy.deepcopy(postwrite)
        updated["execution_receipt_id"] = execution_receipt_id
        updated["execution_receipt_digest"] = expected_execution_digest
        contracts.validate_governance_receipt(updated)
        receipt_relative = f"{GOVERNANCE_RECEIPTS_DIR}/{postwrite_receipt_id}.json"
        result: dict[str, Any] = {
            "result": "PREVIEW" if preview else "BOUND_POSTWRITE_EXECUTION_RECEIPT",
            "postwrite_receipt_id": postwrite_receipt_id,
            "postwrite_receipt_path": str(postwrite_path),
            "execution_receipt_id": execution_receipt_id,
            "execution_receipt_path": str(execution_path),
            "execution_receipt_digest": expected_execution_digest,
            "planned_files": [receipt_relative],
            "created_files": [],
            "no_op": False,
            "state_root": str(actual_state_root),
            "instance_path": str(instance),
        }
        if preview:
            return result
        _transaction_write(
            instance,
            actual_state_root,
            {receipt_relative: contracts.stable_json(updated)},
            expected_digests={receipt_relative: workflow.file_digest(postwrite_path)},
            lock_target=receipt_relative,
            lock_name="postwrite-binding",
            agent=agent,
            transaction_tag="f1-04-postwrite-binding",
        )
        stored = _read_json(postwrite_path, code="INVALID_CLEANLINESS_RECEIPT")
        contracts.validate_governance_receipt(stored)
        if stored.get("execution_receipt_id") != execution_receipt_id or stored.get("execution_receipt_digest") != expected_execution_digest:
            raise PlanningError("POSTWRITE_BINDING_VERIFY_FAILED", "published POST_WRITE execution binding did not verify")
        result["created_files"] = [receipt_relative]
        return result
    except PlanningError as exc:
        return _result_error(exc)
    except (OSError, ValueError, workflow.ContractError) as exc:
        return _result_error(PlanningError("INTERNAL_ERROR", str(exc)))

def _receipt_state_update(
    checklist: str,
    packet: dict[str, Any],
    receipt: dict[str, Any],
    receipt_ref: str,
) -> tuple[str, dict[str, Any]]:
    lines, row_index, cells, columns = _checklist_row_location(checklist, packet["work_item_id"])
    result = receipt["result"]
    evidence_sufficient = bool(receipt["evidence_refs"])
    completed_candidate = result in {"PASS", "PASS_WITH_WARNINGS"} and evidence_sufficient
    if completed_candidate:
        status = "已完成"
        verification = "已核验"
        next_task = cells[columns["下一步"]] or packet["work_item_id"]
    elif result in {"FAILED", "BLOCKED", "INCONCLUSIVE"}:
        status = "阻塞"
        verification = "待补证据"
        next_task = packet["work_item_id"]
    else:
        status = "进行中"
        verification = "待补证据"
        next_task = packet["work_item_id"]
    cells[columns["状态"]] = status
    cells[columns["核验状态"]] = verification
    if "完成证据" in columns:
        cells[columns["完成证据"]] = receipt_ref
    elif "验收证据" in columns:
        cells[columns["验收证据"]] = receipt_ref
    note_parts = [f"{result}: {receipt['summary']}"]
    if receipt["warnings"]:
        note_parts.append("warnings=" + ", ".join(receipt["warnings"]))
    if receipt["blocking_findings"]:
        note_parts.append("blocking=" + ", ".join(receipt["blocking_findings"]))
    if result == "INCONCLUSIVE":
        note_parts.append("HUMAN_GATE_REQUIRED")
    if "阻塞/备注" in columns:
        cells[columns["阻塞/备注"]] = "；".join(note_parts)
    lines[row_index] = "| " + " | ".join(str(cell).replace("|", "/").replace("\n", " ") for cell in cells) + " |"
    metadata = workflow.extract_machine_json(checklist, "workflow")
    metadata["checklist_version"] = workflow.bump_semver(metadata["checklist_version"], "PATCH")
    metadata["current_phase"] = packet["phase_id"]
    metadata["overall_status"] = "阻塞" if result in {"FAILED", "BLOCKED", "INCONCLUSIVE"} else "进行中"
    metadata["recommended_next_task"] = next_task
    metadata["last_execution_receipt_ref"] = receipt_ref
    refs = metadata.get("execution_receipt_refs", [])
    if not isinstance(refs, list):
        refs = []
    metadata["execution_receipt_refs"] = _append_unique(refs, [receipt_ref])
    metadata["last_updated_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    updated = "\n".join(lines) + ("\n" if checklist.endswith("\n") else "")
    updated = workflow.replace_machine_json(updated, "workflow", metadata)
    try:
        workflow.validate_checklist_text(updated)
    except workflow.ContractError as exc:
        raise PlanningError("INVALID_CHECKLIST", f"updated checklist invalid: {exc}") from exc
    state_update = {
        "task_id": packet["work_item_id"],
        "status": status,
        "verification_status": verification,
        "recommended_next_task": next_task,
        "receipt_ref": receipt_ref,
        "completed_candidate": completed_candidate,
        "version_classification": "PATCH",
    }
    return updated, state_update

def record_receipt(
    instance_root: str | Path,
    receipt_path: str | Path,
    *,
    preview: bool = False,
    apply: bool = False,
    agent: str = "planning-with-files",
) -> dict[str, Any]:
    """Validate one ExecutionReceipt and update only the instance projection."""
    try:
        if preview == apply:
            raise PlanningError("INVALID_MODE", "exactly one of preview or apply must be true")
        state_root, instance, _envelope, plan, checklist = _load_instance(instance_root)
        incoming = _read_json(Path(receipt_path).expanduser(), code="INVALID_RECEIPT")
        if "packet_id" not in incoming:
            raise PlanningError("INVALID_RECEIPT", "receipt missing packet_id")
        packet_path = instance / PACKETS_DIR / f"{incoming['packet_id']}.json"
        if not packet_path.is_file():
            raise PlanningError("PACKET_NOT_FOUND", f"packet does not exist: {incoming['packet_id']}")
        packet = _read_json(packet_path)
        try:
            contracts.validate_execution_packet(packet)
        except workflow.ContractError as exc:
            raise PlanningError("INVALID_PACKET", str(exc)) from exc
        _validate_receipt_against_packet(incoming, packet)
        conflict_status, existing = _check_receipt_conflicts(instance, incoming)
        receipt_relative = f"{RECEIPTS_DIR}/{incoming['receipt_id']}.json"
        receipt_target = instance / receipt_relative
        if conflict_status == "EXISTING_RECEIPT":
            return {
                "result": "EXISTING_RECEIPT",
                "receipt_id": incoming["receipt_id"],
                "receipt_path": str(receipt_target),
                "packet_id": incoming["packet_id"],
                "no_op": True,
                "idempotent": True,
                "state_update": {},
            }
        updated_checklist, state_update = _receipt_state_update(
            checklist, packet, incoming, receipt_relative
        )
        result: dict[str, Any] = {
            "result": "PREVIEW" if preview else "RECORDED",
            "receipt_id": incoming["receipt_id"],
            "receipt_path": str(receipt_target),
            "packet_id": incoming["packet_id"],
            "packet_path": str(packet_path),
            "state_update": state_update,
            "planned_files": [receipt_relative, workflow.CHECKLIST_NAME],
            "no_op": False,
            "warnings": copy.deepcopy(incoming["warnings"]),
            "blocking_findings": copy.deepcopy(incoming["blocking_findings"]),
            "state_root": str(state_root),
            "instance_path": str(instance),
        }
        if preview:
            return result
        expected = {
            receipt_relative: workflow.sha256_digest(""),
            workflow.CHECKLIST_NAME: workflow.file_digest(instance / workflow.CHECKLIST_NAME),
        }
        _transaction_write(
            instance,
            state_root,
            {
                receipt_relative: contracts.stable_json(incoming),
                workflow.CHECKLIST_NAME: updated_checklist,
            },
            expected_digests=expected,
            lock_target=workflow.CHECKLIST_NAME,
            lock_name="receipt",
            agent=agent,
        )
        stored = _read_json(receipt_target)
        contracts.validate_execution_receipt(stored)
        final_checklist = (instance / workflow.CHECKLIST_NAME).read_text(encoding="utf-8")
        workflow.validate_checklist_text(final_checklist)
        result["created_files"] = [receipt_relative, workflow.CHECKLIST_NAME]
        return result
    except PlanningError as exc:
        return _result_error(exc)
    except (OSError, ValueError, workflow.ContractError) as exc:
        return _result_error(PlanningError("INTERNAL_ERROR", str(exc)))
    except Exception as exc:
        return {
            "result": "FAILED",
            "error_code": "INTERNAL_ERROR",
            "error": str(exc),
            "created_files": [],
            "existing_files": [],
            "warnings": [],
            "blocking_findings": [str(exc)],
            "no_op": False,
        }
