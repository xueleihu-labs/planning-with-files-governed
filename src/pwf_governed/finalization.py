"""Gate 2 extracted module: finalization.py.

Generated from the Gate 1 planning.py baseline.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import copy
import datetime as dt

from pwf_governed._legacy import (
    governance_profiles,
    plan_contracts,
    workflow_contracts,
)
from pwf_governed._legacy import governance_profiles as governance
from pwf_governed._legacy import plan_contracts as contracts
from pwf_governed._legacy import workflow_contracts as workflow

from pwf_governed._legacy import (
    governance_profiles,
    plan_contracts,
    workflow_contracts,
)
from pwf_governed.checkpoints import (
    _final_checkpoint_gate,
)
from pwf_governed.core.constants import (
    CONTENT_RECEIPTS_DIR,
    CONTENT_RESULTS,
    EVOLUTION_RECEIPTS_DIR,
    EVOLUTION_RESULTS,
    FINALIZATION_MODES,
    GOVERNANCE_RECEIPTS_DIR,
    PACKETS_DIR,
    RECEIPTS_DIR,
    ROUTING_DECISION_RELATIVE,
)
from pwf_governed.core.envelope import (
    _append_unique,
    _load_instance,
    _merge_dicts,
    _parse_timestamp,
    _read_json,
    _result_error,
    _string_values,
    _transaction_write,
)
from pwf_governed.core.errors import (
    PlanningError,
)
from pwf_governed.governance import (
    _governance_receipt_digest,
    _governance_required_stages,
    _load_cleanliness_receipts,
    _load_governance_requests,
    _upsert_human_summary_lines,
)
from pwf_governed.midcourse_gate import (
    _midcourse_gate_evidence_block,
    _midcourse_gate_finalization_block,
    _midcourse_gate_runtime_state,
)
from pwf_governed.outcomes import (
    _content_judgment_required,
    _load_routing_decision,
    _outcome_policy,
    _outcome_target,
    _routing_decision_location,
)
from pwf_governed.owner_gate import (
    _owner_gate_checkpoint_authority,
    _validate_owner_gate_receipt,
)
from pwf_governed.receipts import (
    _load_receipts,
    _validate_cleanliness_receipt_against_request,
    _validate_receipt_against_packet,
)
from pwf_governed.shared.checkpoint_support import (
    _load_checkpoint_refs,
)
from pwf_governed.shared.evidence import (
    _final_evidence_file,
)
from pwf_governed.shared.final_gate import (
    _finalization_bool,
    _plan_human_gate_required,
)

def _finalization_policy(envelope: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for source, label in ((envelope, "TaskEnvelope"), (plan, "PlanPackage")):
        if "finalization_policy" not in source:
            continue
        policy = source["finalization_policy"]
        if not isinstance(policy, dict):
            raise PlanningError("INVALID_FINALIZATION_POLICY", f"{label}.finalization_policy must be an object")
        values.update(copy.deepcopy(policy))
    return values

def _finalization_mode(envelope: dict[str, Any], plan: dict[str, Any]) -> str:
    policy = _finalization_policy(envelope, plan)
    mode = policy.get("mode") or plan.get("finalization_mode")
    if mode is None and policy.get("advanced") is True:
        mode = "ADVANCED"
    if mode is None:
        mode = "ADVANCED" if plan.get("task_profile") in {"FULL", "HIGH_RISK", "STRICT"} else "SIMPLE"
    if mode not in FINALIZATION_MODES:
        raise PlanningError("INVALID_FINALIZATION_MODE", f"unsupported finalization mode: {mode}")
    return str(mode)

def _final_ref_values(value: Any) -> list[str]:
    placeholders = {"", "-", "—", "n/a", "N/A", "none", "None"}

    def keep(raw: Any) -> str | None:
        if not isinstance(raw, str):
            return None
        item = raw.strip()
        return item if item and item not in placeholders else None

    if isinstance(value, str) and value.strip():
        item = keep(value)
        return [item] if item else []
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            value_item = keep(item)
            if value_item:
                result.append(value_item)
        elif isinstance(item, dict):
            raw = item.get("path") or item.get("location") or item.get("ref")
            value_item = keep(raw)
            if value_item:
                result.append(value_item)
    return _append_unique([], result)

def _final_evidence_check(state_root: Path, instance: Path, refs: Any) -> tuple[list[str], list[str]]:
    values = _final_ref_values(refs)
    missing: list[str] = []
    verified: list[str] = []
    for raw in values:
        if _final_evidence_file(state_root, instance, raw) is None:
            missing.append(raw)
        else:
            verified.append(raw)
    return _append_unique([], verified), _append_unique([], missing)

def _final_conditions_gate(
    state_root: Path,
    instance: Path,
    conditions: Any,
    label: str,
) -> tuple[list[str], list[str], list[str], list[str]]:
    blocking: list[str] = []
    waiting: list[str] = []
    warnings: list[str] = []
    evidence: list[str] = []
    if conditions is None:
        return blocking, waiting, warnings, evidence
    if not isinstance(conditions, list):
        raise PlanningError("INVALID_CONDITIONS", f"{label} must be an array")
    for condition in conditions:
        if not isinstance(condition, dict) or not condition.get("required", True):
            continue
        condition_id = str(condition.get("condition_id", "unknown"))
        status = str(condition.get("status", "PENDING"))
        refs = _final_ref_values(condition.get("evidence_refs"))
        evidence = _append_unique(evidence, refs)
        if status == "FAILED":
            blocking.append(f"{label}:{condition_id}:FAILED")
            continue
        if status in {"PENDING", "WAIVED"}:
            waiting.append(f"{label}:{condition_id}:{status}")
            continue
        if status == "SATISFIED":
            if condition.get("evidence_required", True) and not refs:
                waiting.append(f"{label}:{condition_id}:MISSING_EVIDENCE")
                continue
            _, missing = _final_evidence_check(state_root, instance, refs)
            if missing:
                waiting.append(f"{label}:{condition_id}:EVIDENCE_NOT_FOUND")
            continue
        if status == "NOT_APPLICABLE":
            warnings.append(f"{label}:{condition_id}:NOT_APPLICABLE")
            continue
        waiting.append(f"{label}:{condition_id}:UNKNOWN_STATUS")
    return blocking, waiting, warnings, evidence

def _final_checklist_gate(checklist: str) -> tuple[list[str], list[str], list[str], list[str]]:
    blocking: list[str] = []
    waiting: list[str] = []
    warnings: list[str] = []
    evidence: list[str] = []
    tasks = workflow.checklist_tasks(checklist)
    if not tasks:
        waiting.append("checklist:no_tasks")
        return blocking, waiting, warnings, evidence
    yes_values = {"是", "YES", "TRUE", "true", "必需"}
    for task in tasks:
        required_field = task.get("是否必需")
        required = (
            str(required_field).strip() in yes_values
            if required_field is not None
            else task.get("主线", "是") != "否"
        )
        if not required:
            continue
        task_id = str(task.get("ID", "unknown"))
        status = str(task.get("状态", "未开始"))
        verification = str(task.get("核验状态", "未核验"))
        raw_evidence = task.get("完成证据") or task.get("验收证据")
        evidence = _append_unique(evidence, _final_ref_values(raw_evidence))
        if status == "阻塞":
            blocking.append(f"task:{task_id}:BLOCKED")
        elif status not in {"已完成", "已跳过", "已废弃"}:
            waiting.append(f"task:{task_id}:{status}")
        elif status == "已完成" and verification not in {"已核验", "不适用"}:
            waiting.append(f"task:{task_id}:UNVERIFIED")
        elif status == "已完成" and not evidence:
            waiting.append(f"task:{task_id}:MISSING_EVIDENCE")
    return blocking, waiting, warnings, evidence

def _final_checklist_gate_with_context(
    state_root: Path,
    instance: Path,
    checklist: str,
) -> tuple[list[str], list[str], list[str], list[str]]:
    blocking, waiting, warnings, evidence = _final_checklist_gate(checklist)
    if not evidence:
        return blocking, waiting, warnings, evidence
    _, missing = _final_evidence_check(state_root, instance, evidence)
    if missing:
        waiting.extend(f"checklist:evidence:{item}:NOT_FOUND" for item in missing)
    return blocking, _append_unique([], waiting), warnings, evidence

def _final_owner_gate_receipt_gate(
    state_root: Path,
    instance: Path,
    envelope: dict[str, Any],
    plan: dict[str, Any],
    checklist: str,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Require formal receipts for owner gates that claim final acceptance."""
    blocking: list[str] = []
    waiting: list[str] = []
    warnings: list[str] = []
    evidence: list[str] = []
    for gate in plan.get("human_gates", []):
        if not isinstance(gate, dict):
            continue
        formal_route = gate.get("formal_route")
        receipt_ref = gate.get("owner_gate_receipt_ref")
        if formal_route != "FINAL_MANUAL_ACCEPTANCE" and not receipt_ref:
            continue
        condition_id = str(gate.get("condition_id", "unknown"))
        if gate.get("status") != "SATISFIED":
            continue
        if not isinstance(receipt_ref, str) or not receipt_ref:
            blocking.append(f"owner_gate:{condition_id}:MISSING_RECEIPT")
            continue
        receipt_path = _final_evidence_file(state_root, instance, receipt_ref)
        if receipt_path is None:
            blocking.append(f"owner_gate:{condition_id}:RECEIPT_NOT_FOUND")
            continue
        try:
            receipt = _read_json(receipt_path, code="INVALID_OWNER_GATE_RECEIPT")
            _validate_owner_gate_receipt(
                receipt,
                state_root=state_root,
                instance=instance,
                envelope=envelope,
                plan=plan,
                gate=gate,
            )
            _owner_gate_checkpoint_authority(
                state_root,
                instance,
                envelope,
                plan,
                checklist,
                str(receipt["accepted_checkpoint"]),
                str(receipt["accepted_commit"]),
            )
        except PlanningError as exc:
            blocking.append(f"owner_gate:{condition_id}:{exc.code}")
            continue
        evidence = _append_unique(evidence, [receipt_ref])
        evidence = _append_unique(evidence, _string_values(receipt.get("evidence_refs")))
    return blocking, waiting, warnings, evidence

def _final_failure_pause_gate(
    state_root: Path,
    instance: Path,
    plan: dict[str, Any],
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Treat satisfied failure/pause conditions as terminal blockers."""
    blocking: list[str] = []
    waiting: list[str] = []
    warnings: list[str] = []
    evidence: list[str] = []

    for label, conditions in (("failure", plan.get("failure_conditions", [])), ("pause", plan.get("pause_conditions", []))):
        if conditions is None:
            continue
        if not isinstance(conditions, list):
            raise PlanningError("INVALID_CONDITIONS", f"{label}_conditions must be an array")
        for condition in conditions:
            if not isinstance(condition, dict) or not condition.get("required", True):
                continue
            condition_id = str(condition.get("condition_id", "unknown"))
            status = str(condition.get("status", "PENDING"))
            refs = _final_ref_values(condition.get("evidence_refs"))
            evidence = _append_unique(evidence, refs)
            if status in {"FAILED", "SATISFIED"}:
                target = blocking if label == "failure" else waiting
                target.append(f"{label}:{condition_id}:{status}")
            elif status == "WAIVED":
                waiting.append(f"{label}:{condition_id}:WAIVED")
            elif status not in {"PENDING", "NOT_APPLICABLE"}:
                waiting.append(f"{label}:{condition_id}:{status}")
    if evidence:
        _, missing = _final_evidence_check(state_root, instance, evidence)
        waiting.extend(f"conditions:evidence:{item}:NOT_FOUND" for item in missing)
    return blocking, _append_unique([], waiting), warnings, evidence

def _load_all_packets(instance: Path, plan: dict[str, Any]) -> list[dict[str, Any]]:
    packets_dir = instance / PACKETS_DIR
    if not packets_dir.exists():
        return []
    if packets_dir.is_symlink() or not packets_dir.is_dir():
        raise PlanningError("UNSAFE_INSTANCE_ROOT", "packets directory must be a real directory")
    packets: list[dict[str, Any]] = []
    for path in sorted(packets_dir.glob("*.json")):
        if path.is_symlink():
            raise PlanningError("UNSAFE_INSTANCE_ROOT", f"packet cannot be a symlink: {path}")
        packet = _read_json(path, code="INVALID_PACKET")
        try:
            contracts.validate_execution_packet(packet)
        except workflow.ContractError as exc:
            raise PlanningError("INVALID_PACKET", f"invalid existing packet {path}: {exc}") from exc
        if packet["task_id"] != plan["task_id"] or packet["plan_id"] != plan["plan_id"] or packet["plan_version"] != plan["plan_version"]:
            raise PlanningError("REFERENCE_MISMATCH", f"packet references do not match PlanPackage: {path}")
        packets.append(packet)
    return packets

def _execution_receipt_for_packet(
    packet: dict[str, Any],
    receipts: list[dict[str, Any]],
    packets_by_id: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, str | None]:
    direct = [receipt for receipt in receipts if receipt.get("packet_id") == packet.get("packet_id")]
    if direct:
        return direct[-1], None
    reuse = packet.get("execution_receipt_reuse")
    if not isinstance(reuse, dict) or reuse.get("enabled") is not True:
        return None, "MISSING_RECEIPT"
    source_packet_id = reuse.get("source_packet_id")
    source_packet = packets_by_id.get(str(source_packet_id))
    if source_packet is None:
        return None, "REUSED_SOURCE_PACKET_NOT_FOUND"
    source_receipts = [
        receipt for receipt in receipts
        if receipt.get("receipt_id") == reuse.get("source_receipt_id")
        and receipt.get("packet_id") == source_packet_id
    ]
    if len(source_receipts) != 1:
        return None, "REUSED_SOURCE_RECEIPT_NOT_FOUND"
    receipt = source_receipts[0]
    try:
        _validate_receipt_against_packet(receipt, source_packet)
    except PlanningError as exc:
        return None, f"REUSED_RECEIPT_INVALID:{exc.code}"
    if contracts.contract_digest(source_packet) != reuse.get("source_packet_digest"):
        return None, "REUSED_SOURCE_PACKET_DRIFT"
    if contracts.contract_digest(receipt) != reuse.get("source_receipt_digest"):
        return None, "REUSED_SOURCE_RECEIPT_DRIFT"
    return receipt, None

def _final_execution_gate(
    instance: Path,
    plan: dict[str, Any],
    policy: dict[str, Any],
    mode: str,
) -> tuple[list[str], list[str], list[str], list[str]]:
    if not _finalization_bool(policy, "require_execution_receipts", mode == "ADVANCED"):
        return [], [], [], []
    packets = _load_all_packets(instance, plan)
    receipts = _load_receipts(instance)
    blocking: list[str] = []
    waiting: list[str] = []
    warnings: list[str] = []
    evidence: list[str] = []
    if not packets:
        blocking.append("execution:no_packets")
        return blocking, waiting, warnings, evidence
    packets_by_id = {str(packet.get("packet_id")): packet for packet in packets}
    for packet in packets:
        packet_id = packet["packet_id"]
        receipt, receipt_error = _execution_receipt_for_packet(packet, receipts, packets_by_id)
        if receipt is None:
            blocking.append(f"execution:{packet_id}:{receipt_error or 'MISSING_RECEIPT'}")
            continue
        reuse = packet.get("execution_receipt_reuse")
        ref = (
            str(reuse.get("source_receipt_ref"))
            if isinstance(reuse, dict) and reuse.get("source_receipt_ref")
            else f"{RECEIPTS_DIR}/{receipt['receipt_id']}.json"
        )
        evidence.append(ref)
        if isinstance(reuse, dict) and reuse.get("source_packet_ref"):
            evidence = _append_unique(evidence, [str(reuse["source_packet_ref"])])
        evidence = _append_unique(evidence, _string_values(receipt.get("evidence_refs")))
        result = receipt.get("result")
        if result in {"FAILED", "BLOCKED"}:
            blocking.append(f"execution:{packet_id}:{result}")
        elif result == "INCONCLUSIVE":
            waiting.append(f"execution:{packet_id}:INCONCLUSIVE")
        elif result not in {"PASS", "PASS_WITH_WARNINGS"}:
            waiting.append(f"execution:{packet_id}:UNRESOLVED")
        if not receipt.get("evidence_refs"):
            waiting.append(f"execution:{packet_id}:MISSING_EVIDENCE")
        if result == "PASS_WITH_WARNINGS":
            warnings = _append_unique(warnings, _string_values(receipt.get("warnings")))
    return blocking, waiting, warnings, evidence

def _final_governance_gate(
    instance: Path,
    envelope: dict[str, Any],
    plan: dict[str, Any],
    checklist: str,
    policy: dict[str, Any],
    mode: str,
) -> tuple[list[str], list[str], list[str], list[str]]:
    if not _finalization_bool(policy, "require_cleanliness_receipts", mode == "ADVANCED"):
        return [], [], [], []
    required = policy.get("required_governance_stages")
    if required is None:
        required = _governance_required_stages(plan)
    if not isinstance(required, list):
        raise PlanningError("INVALID_FINALIZATION_POLICY", "required_governance_stages must be an array")
    requests = _load_governance_requests(instance)
    receipts = _load_cleanliness_receipts(instance)
    blocking: list[str] = []
    waiting: list[str] = []
    warnings: list[str] = []
    evidence: list[str] = []
    for stage in list(dict.fromkeys(str(item) for item in required)):
        stage_requests = [item for item in requests if item.get("task_id") == envelope["task_id"] and item.get("plan_id") == plan["plan_id"] and item.get("governance_stage") == stage]
        stage_receipts = [item for item in receipts if item.get("task_id") == envelope["task_id"] and item.get("plan_id") == plan["plan_id"] and item.get("governance_stage") == stage]
        if not stage_requests:
            blocking.append(f"governance:{stage}:MISSING_REQUEST")
            continue
        if not stage_receipts:
            blocking.append(f"governance:{stage}:MISSING_RECEIPT")
            continue
        # Governance requests are an append-only event stream; filesystem
        # ordering is not the event order when a retry has a different digest.
        request = max(
            stage_requests,
            key=lambda item: (
                _parse_timestamp(str(item["requested_at"])),
                str(item.get("request_id", "")),
            ),
        )
        matching_receipts = [
            item for item in stage_receipts if item.get("request_id") == request.get("request_id")
        ]
        if not matching_receipts:
            blocking.append(f"governance:{stage}:CLEANLINESS_RECEIPT_MISMATCH")
            continue
        receipt_digests = {_governance_receipt_digest(item) for item in matching_receipts}
        if len(receipt_digests) > 1:
            blocking.append(f"governance:{stage}:CLEANLINESS_RECEIPT_MISMATCH")
            continue
        receipt = sorted(matching_receipts, key=lambda item: str(item.get("receipt_id", "")))[-1]
        try:
            decision = _validate_cleanliness_receipt_against_request(receipt, request)
        except PlanningError as exc:
            blocking.append(f"governance:{stage}:{exc.code}")
            continue
        ref = f"{GOVERNANCE_RECEIPTS_DIR}/{receipt['receipt_id']}.json"
        evidence.append(ref)
        evidence = _append_unique(evidence, _string_values(receipt.get("evidence_refs")))
        result = receipt.get("result")
        if result == "BLOCKED" or decision.get("status") == "BLOCKED":
            blocking.append(f"governance:{stage}:BLOCKED")
        elif result == "INCONCLUSIVE" or decision.get("status") == "INCONCLUSIVE":
            waiting.append(f"governance:{stage}:INCONCLUSIVE")
        elif result not in {"PASS", "PASS_WITH_WARNINGS"}:
            waiting.append(f"governance:{stage}:UNRESOLVED")
        if result == "PASS_WITH_WARNINGS":
            warnings = _append_unique(warnings, _string_values(receipt.get("non_blocking_findings")))
    return blocking, waiting, warnings, evidence

def _final_outcome_gate(
    state_root: Path,
    instance: Path,
    envelope: dict[str, Any],
    plan: dict[str, Any],
    policy: dict[str, Any],
    mode: str,
    checkpoint_id: str | None = None,
) -> tuple[list[str], list[str], list[str], list[str]]:
    route_path = _routing_decision_location(instance, checkpoint_id)
    if checkpoint_id is not None and not route_path.is_file():
        refs = _load_checkpoint_refs(instance)
        by_id = {ref.get("checkpoint_id"): ref for ref in refs if ref.get("checkpoint_id")}
        curr_id = checkpoint_id
        visited = set()
        while curr_id and curr_id not in visited:
            visited.add(curr_id)
            curr_ref = by_id.get(curr_id)
            if not curr_ref:
                break
            prev_id = curr_ref.get("previous_checkpoint_id")
            if not prev_id:
                break
            candidate_path = _routing_decision_location(instance, prev_id)
            if candidate_path.is_file():
                checkpoint_id = prev_id
                route_path = candidate_path
                break
            curr_id = prev_id
    evolution_policy = _outcome_policy(envelope, plan, "evolution_policy")
    content_policy = _merge_dicts(
        envelope.get("knowledge_policy"),
        plan.get("knowledge_policy"),
        envelope.get("content_policy"),
        plan.get("content_policy"),
    )
    evolution_required = bool(evolution_policy.get("required"))
    content_required = bool(
        content_policy.get("level", "NONE") != "NONE"
        and (content_policy.get("ingest_required") or content_policy.get("potential_value"))
    )
    require_routing = _finalization_bool(
        policy,
        "require_outcome_routing",
        mode == "ADVANCED" and (evolution_required or content_required),
    )
    if not require_routing:
        return ([], [], [], [ROUTING_DECISION_RELATIVE]) if route_path.is_file() else ([], [], [], [])
    if not route_path.is_file():
        missing = []
        if not evolution_required and not content_required:
            missing.append("outcomes:routing-decision:judgment_missing")
        if evolution_required:
            missing.append("outcomes:routing-decision:evolution_judgment_missing")
        if content_required:
            missing.append("outcomes:routing-decision:content_judgment_missing")
        return (missing, [], [], []) if missing else ([], [], [], [])
    decision = _load_routing_decision(instance, checkpoint_id)
    blocking: list[str] = []
    waiting: list[str] = []
    warnings: list[str] = []
    evidence: list[str] = [route_path.relative_to(instance).as_posix()]
    if _content_judgment_required(content_policy):
        judgment = decision.get("content_judgment")
        if judgment is None:
            blocking.append("outcomes:routing-decision:content_judgment_missing")
        else:
            try:
                contracts.validate_content_judgment(judgment)
            except workflow.ContractError as exc:
                blocking.append(f"outcomes:routing-decision:content_judgment_invalid:{exc}")
            else:
                evidence = _append_unique(evidence, [str(judgment["evidence_ref"])])
    if decision.get("human_review_required") or decision.get("decision") == "HUMAN_REVIEW_REQUIRED":
        waiting.append("outcomes:HUMAN_REVIEW_REQUIRED")
    if decision.get("blocking_findings") and not decision.get("human_review_required"):
        blocking.extend(f"outcomes:{item}" for item in _string_values(decision.get("blocking_findings")))

    def consume_receipt(kind: str, required: bool, ref_field: str, relative_dir: str, validator: Any, results: set[str]) -> None:
        if decision.get(f"{kind}_status") == "HUMAN_REVIEW_REQUIRED":
            waiting.append(f"outcomes:{kind}:HUMAN_REVIEW_REQUIRED")
            return
        ref = decision.get(ref_field)
        if not required:
            return
        if not ref:
            blocking.append(f"outcomes:{kind}:MISSING_RECEIPT")
            return
        path = _outcome_target(instance, str(ref))
        if not path.is_file():
            blocking.append(f"outcomes:{kind}:RECEIPT_NOT_FOUND")
            return
        value = _read_json(path, code=f"INVALID_{kind.upper()}_RECEIPT")
        try:
            validator(value)
        except workflow.ContractError as exc:
            blocking.append(f"outcomes:{kind}:INVALID_RECEIPT:{exc}")
            return
        evidence.append(str(ref))
        result = value.get("result")
        if result == "BLOCKED":
            blocking.append(f"outcomes:{kind}:BLOCKED")
        elif result == "FAILED_RETRYABLE":
            waiting.append(f"outcomes:{kind}:FAILED_RETRYABLE")
        elif result not in results:
            waiting.append(f"outcomes:{kind}:UNRESOLVED")

    evolution_required = bool(decision.get("evolution_required"))
    content_required = bool(decision.get("content_required"))
    evolution_receipt_policy = evolution_policy.get("required", True)
    if not isinstance(evolution_receipt_policy, bool):
        raise PlanningError("INVALID_OUTCOME_POLICY", "evolution_policy.required must be boolean")
    require_evolution = _finalization_bool(
        policy,
        "require_evolution_receipt",
        mode == "ADVANCED" and evolution_required and evolution_receipt_policy,
    )
    require_content = _finalization_bool(
        policy,
        "require_content_ingest_receipt",
        mode == "ADVANCED" and content_required and bool(content_policy.get("ingest_required")),
    )
    consume_receipt("evolution", evolution_required and require_evolution, "evolution_receipt_ref", EVOLUTION_RECEIPTS_DIR, contracts.validate_evolution_receipt, EVOLUTION_RESULTS)
    consume_receipt("content", content_required and require_content, "content_ingest_receipt_ref", CONTENT_RECEIPTS_DIR, contracts.validate_content_ingest_receipt, CONTENT_RESULTS)
    if decision.get("evolution_status") == "HUMAN_REVIEW_REQUIRED":
        waiting.append("outcomes:evolution:HUMAN_REVIEW_REQUIRED")
    if decision.get("content_status") == "HUMAN_REVIEW_REQUIRED":
        waiting.append("outcomes:content:HUMAN_REVIEW_REQUIRED")
    if decision.get("evolution_status") == "BLOCKED":
        blocking.append("outcomes:evolution:BLOCKED")
    if decision.get("content_status") == "BLOCKED":
        blocking.append("outcomes:content:BLOCKED")
    return blocking, waiting, warnings, evidence

def _finalization_assessment(
    state_root: Path,
    instance: Path,
    envelope: dict[str, Any],
    plan: dict[str, Any],
    checklist: str,
) -> dict[str, Any]:
    policy = _finalization_policy(envelope, plan)
    mode = _finalization_mode(envelope, plan)
    metadata = workflow.extract_machine_json(checklist, "workflow")
    blocking: list[str] = []
    waiting: list[str] = []
    warnings: list[str] = []
    evidence: list[str] = ["task-envelope.json", "plan-package.json", workflow.CHECKLIST_NAME]
    receipts: list[str] = []

    if metadata.get("overall_status") in {"阻塞", "BLOCKED"} or metadata.get("task_status") in {"BLOCKED", "FAILED"}:
        blocking.append("checklist:BLOCKED")
    if metadata.get("human_execution_gate") in {"REQUIRED", "WAITING_FOR_HUMAN", "WAITING_FOR_OWNER"}:
        waiting.append("checklist:HUMAN_GATE_REQUIRED")
    if _plan_human_gate_required(plan, current_phase=metadata.get("current_phase")):
        waiting.append("plan:UNRESOLVED_HUMAN_GATE")
    b, w, warn, refs = _final_owner_gate_receipt_gate(
        state_root,
        instance,
        envelope,
        plan,
        checklist,
    )
    blocking.extend(b)
    waiting.extend(w)
    warnings.extend(warn)
    evidence = _append_unique(evidence, refs)
    midcourse_runtime = _midcourse_gate_runtime_state(state_root, instance, plan)
    midcourse_block = _midcourse_gate_finalization_block(state_root, instance, plan)
    if midcourse_block:
        blocking.append(midcourse_block)
    midcourse_evidence_block = _midcourse_gate_evidence_block(state_root, instance, plan)
    if midcourse_evidence_block:
        blocking.append(midcourse_evidence_block)
    evidence = _append_unique(evidence, midcourse_runtime.get("evidence_refs", []))

    for fn, args in (
        (_final_checklist_gate_with_context, (state_root, instance, checklist)),
        (_final_conditions_gate, (state_root, instance, plan.get("completion_conditions", []), "completion")),
    ):
        b, w, warn, refs = fn(*args)
        blocking.extend(b)
        waiting.extend(w)
        warnings.extend(warn)
        evidence = _append_unique(evidence, refs)

    b, w, warn, refs = _final_failure_pause_gate(state_root, instance, plan)
    blocking.extend(b)
    waiting.extend(w)
    warnings.extend(warn)
    evidence = _append_unique(evidence, refs)

    b, w, warn, refs = _final_execution_gate(instance, plan, policy, mode)
    blocking.extend(b)
    waiting.extend(w)
    warnings.extend(warn)
    receipts = _append_unique(receipts, refs)
    evidence = _append_unique(evidence, refs)

    b, w, warn, refs = _final_governance_gate(instance, envelope, plan, checklist, policy, mode)
    blocking.extend(b)
    waiting.extend(w)
    warnings.extend(warn)
    receipts = _append_unique(receipts, refs)
    evidence = _append_unique(evidence, refs)

    b, w, warn, refs, trusted_checkpoint = _final_checkpoint_gate(state_root, instance, envelope, plan, checklist, policy, mode)
    blocking.extend(b)
    waiting.extend(w)
    warnings.extend(warn)
    receipts = _append_unique(receipts, refs)
    evidence = _append_unique(evidence, refs)

    b, w, warn, refs = _final_outcome_gate(
        state_root, instance, envelope, plan, policy, mode, metadata.get("last_trusted_checkpoint")
    )
    blocking.extend(b)
    waiting.extend(w)
    warnings.extend(warn)
    receipts = _append_unique(receipts, refs)
    evidence = _append_unique(evidence, refs)

    blocking = _append_unique([], blocking)
    waiting = _append_unique([], waiting)
    warnings = _append_unique([], warnings)
    evidence = _append_unique([], evidence)
    receipts = _append_unique([], receipts)
    gate = "CLOSE_BLOCKED" if blocking else "CLOSE_WAITING_HUMAN" if waiting else "CLOSE_READY"
    next_action = "NONE" if gate == "CLOSE_READY" else "REPAIR_BLOCKING_EVIDENCE" if gate == "CLOSE_BLOCKED" else "OWNER_REVIEW"
    return {
        "mode": mode,
        "completion_gate": gate,
        "top_level_status": governance.normalize_top_level_status(
            gate,
            blocking_findings=blocking,
            waiting_owner=bool(waiting),
        ),
        "blocking_findings": blocking,
        "warnings": warnings,
        "final_evidence_refs": evidence,
        "final_receipt_refs": receipts,
        "trusted_checkpoint": trusted_checkpoint or metadata.get("last_trusted_checkpoint"),
        "midcourse_gate_result": midcourse_runtime.get("status"),
        "midcourse_gate_effective_result": midcourse_runtime.get("effective_result"),
        "midcourse_gate_source": midcourse_runtime.get("source"),
        "required_next_action": next_action,
        "known_non_blocking_debts": _append_unique(
            _string_values(metadata.get("known_non_blocking_debts")),
            _string_values(plan.get("known_non_blocking_debts")) + _string_values(policy.get("known_non_blocking_debts")),
        ),
    }

def _finalization_state_update(checklist: str, assessment: dict[str, Any], result: str) -> str:
    metadata = workflow.extract_machine_json(checklist, "workflow")
    metadata["checklist_version"] = workflow.bump_semver(metadata["checklist_version"], "PATCH")
    metadata["task_status"] = "CLOSED" if result == "CLOSED" else "BLOCKED" if result == "CLOSE_BLOCKED" else "WAITING_FOR_HUMAN"
    metadata["final_status"] = result
    metadata["finalization_mode"] = assessment["mode"]
    metadata["completion_gate"] = assessment["completion_gate"]
    metadata["blocking_findings"] = _append_unique([], assessment["blocking_findings"])
    metadata["required_next_action"] = assessment["required_next_action"]
    metadata["verification_status"] = "已核验" if result == "CLOSED" else "待补证据" if result == "CLOSE_BLOCKED" else "待人工"
    metadata["top_level_status"] = governance.normalize_top_level_status(
        result,
        waiting_owner=result == "CLOSE_WAITING_HUMAN",
        completed=result == "CLOSED",
    )
    if result == "CLOSED":
        metadata["closed_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        metadata["overall_status"] = "已完成"
        metadata["human_execution_gate"] = "CLOSED"
        metadata["recommended_next_task"] = "验收/封板"
    elif result == "CLOSE_BLOCKED":
        metadata["overall_status"] = "阻塞"
        metadata["human_execution_gate"] = "REPAIR_REQUIRED"
    else:
        metadata["overall_status"] = "暂停"
        metadata["human_execution_gate"] = "REQUIRED"
    metadata["final_evidence_refs"] = _append_unique([], assessment["final_evidence_refs"])
    metadata["final_receipt_refs"] = _append_unique([], assessment["final_receipt_refs"])
    metadata["warnings"] = _append_unique(_string_values(metadata.get("warnings")), assessment["warnings"])
    metadata["known_non_blocking_debts"] = _append_unique(
        _string_values(metadata.get("known_non_blocking_debts")), assessment["known_non_blocking_debts"]
    )
    metadata["last_updated_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    history = metadata.get("change_history", [])
    if not isinstance(history, list):
        history = []
    history.append({
        "timestamp": metadata["last_updated_at"],
        "change_type": "FINALIZE_PLAN",
        "result": result,
        "mode": assessment["mode"],
        "completion_gate": assessment["completion_gate"],
        "version_classification": "PATCH",
    })
    metadata["change_history"] = history
    updated = workflow.replace_machine_json(checklist, "workflow", metadata)
    updated = _upsert_human_summary_lines(
        updated,
        {
            "最终完成门：": result,
            "最终验证状态：": metadata["verification_status"],
            "最终模式：": assessment["mode"],
        },
    )
    workflow.validate_checklist_text(updated)
    return updated

def finalize_plan(
    instance_root: str | Path,
    *,
    preview: bool = False,
    apply: bool = False,
    agent: str = "planning-with-files",
) -> dict[str, Any]:
    """Evaluate and optionally atomically publish the final PLAN completion gate."""
    try:
        if preview == apply:
            raise PlanningError("INVALID_MODE", "exactly one of preview or apply must be true")
        state_root, instance, envelope, plan, checklist = _load_instance(instance_root)
        metadata = workflow.extract_machine_json(checklist, "workflow")
        if metadata.get("final_status") == "CLOSED":
            return {
                "result": "ALREADY_CLOSED",
                "task_id": envelope["task_id"],
                "plan_id": plan["plan_id"],
                "mode": _finalization_mode(envelope, plan),
                "completion_gate": "CLOSED",
                "top_level_status": "COMPLETED",
                "no_op": True,
                "version_incremented": False,
            }
        assessment = _finalization_assessment(state_root, instance, envelope, plan, checklist)
        gate = assessment["completion_gate"]
        result = "CLOSED" if apply and gate == "CLOSE_READY" else gate
        output: dict[str, Any] = {
            "result": result,
            "task_id": envelope["task_id"],
            "plan_id": plan["plan_id"],
            "mode": assessment["mode"],
            "completion_gate": gate,
            "top_level_status": "COMPLETED" if result == "CLOSED" else assessment["top_level_status"],
            "blocking_findings": assessment["blocking_findings"],
            "warnings": assessment["warnings"],
            "final_evidence_refs": assessment["final_evidence_refs"],
            "final_receipt_refs": assessment["final_receipt_refs"],
            "trusted_checkpoint": assessment["trusted_checkpoint"],
            "midcourse_gate_result": assessment["midcourse_gate_result"],
            "midcourse_gate_effective_result": assessment["midcourse_gate_effective_result"],
            "midcourse_gate_source": assessment["midcourse_gate_source"],
            "required_next_action": assessment["required_next_action"],
            "planned_files": [workflow.CHECKLIST_NAME],
            "created_files": [],
            "no_op": False,
            "version_incremented": False,
            "state_root": str(state_root),
            "instance_path": str(instance),
            "external_calls": [],
        }
        if preview:
            return output
        updated = _finalization_state_update(checklist, assessment, result)
        _transaction_write(
            instance,
            state_root,
            {workflow.CHECKLIST_NAME: updated},
            expected_digests={workflow.CHECKLIST_NAME: workflow.file_digest(instance / workflow.CHECKLIST_NAME)},
            lock_target=workflow.CHECKLIST_NAME,
            lock_name="finalize-plan",
            agent=agent,
            transaction_tag="f1-07-finalize",
        )
        final_checklist = (instance / workflow.CHECKLIST_NAME).read_text(encoding="utf-8")
        workflow.validate_checklist_text(final_checklist)
        output["created_files"] = [workflow.CHECKLIST_NAME]
        output["version_incremented"] = True
        return output
    except PlanningError as exc:
        return _result_error(exc)
    except (OSError, ValueError, workflow.ContractError) as exc:
        return _result_error(PlanningError("INTERNAL_ERROR", str(exc)))
