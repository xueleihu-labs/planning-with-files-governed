"""Gate 2 extracted module: governance.py.

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
    FORMAL_PROTECTED_ASSETS,
    GOVERNANCE_CANDIDATE_CLASSES,
    GOVERNANCE_RECEIPTS_DIR,
    GOVERNANCE_REQUESTS_DIR,
    GOVERNANCE_REQUEST_ID_PREFIX,
    GOVERNANCE_STAGES,
    PACKETS_DIR,
)
from pwf_governed.core.envelope import (
    _append_unique,
    _explicit_dirty_paths,
    _load_instance,
    _parse_timestamp,
    _read_json,
    _result_error,
    _safe_component,
    _scope_values,
    _string_values,
    _transaction_write,
)
from pwf_governed.core.errors import (
    PlanningError,
)
from pwf_governed.midcourse_gate import (
    _ensure_midcourse_gate_allows_phase,
    _ensure_midcourse_gate_evidence,
)
from pwf_governed.plan_builder import (
    _governance_stages_for_profile,
)

def _current_governance_gate(instance: Path, envelope: dict[str, Any], plan: dict[str, Any], checklist: str) -> dict[str, Any]:
    warnings: list[str] = []
    blocking: list[str] = []
    evidence: list[str] = []
    inconclusive = False
    latest_by_stage: dict[str, dict[str, Any]] = {}
    for receipt in _load_cleanliness_receipts(instance):
        if receipt.get("task_id") != envelope["task_id"] or receipt.get("plan_id") != plan["plan_id"]:
            continue
        stage = str(receipt["governance_stage"])
        previous = latest_by_stage.get(stage)
        if previous is None or (
            _parse_timestamp(str(receipt["checked_at"])),
            str(receipt["receipt_id"]),
        ) > (
            _parse_timestamp(str(previous["checked_at"])),
            str(previous["receipt_id"]),
        ):
            latest_by_stage[stage] = receipt
    # Governance receipts are an append-only retry ledger. A later receipt for
    # the same stage supersedes an earlier BLOCKED/INCONCLUSIVE projection;
    # historical receipts remain immutable evidence but cannot block forever.
    for receipt in latest_by_stage.values():
        evidence = _append_unique(evidence, _string_values(receipt.get("evidence_refs")))
        if receipt.get("result") == "BLOCKED":
            blocking = _append_unique(blocking, _string_values(receipt.get("blocking_findings")))
            blocking = blocking or ["current CleanlinessReceipt is BLOCKED"]
        elif receipt.get("result") == "INCONCLUSIVE":
            inconclusive = True
        elif receipt.get("result") == "PASS_WITH_WARNINGS":
            warnings = _append_unique(warnings, _string_values(receipt.get("non_blocking_findings")))
    metadata = workflow.extract_machine_json(checklist, "workflow")
    if metadata.get("governance_status") == "BLOCKED":
        blocking = _append_unique(blocking, _string_values(metadata.get("blocking_findings")) or ["governance projection is BLOCKED"])
    if metadata.get("governance_status") == "INCONCLUSIVE":
        inconclusive = True
    if blocking:
        return {"status": "BLOCKED", "blocking_findings": blocking, "warnings": warnings, "evidence_refs": evidence}
    if inconclusive:
        return {"status": "INCONCLUSIVE", "blocking_findings": [], "warnings": warnings, "evidence_refs": evidence}
    return {"status": "PASS_WITH_WARNINGS" if warnings else "PASS", "blocking_findings": [], "warnings": warnings, "evidence_refs": evidence}

def _governance_required_stages(plan: dict[str, Any]) -> list[str]:
    policy = plan.get("governance_policy", {})
    if not isinstance(policy, dict):
        raise PlanningError("INVALID_GOVERNANCE_POLICY", "PlanPackage governance_policy must be an object")
    if "required_stages" in policy:
        stages = policy["required_stages"]
        if not isinstance(stages, list) or any(stage not in GOVERNANCE_STAGES for stage in stages):
            raise PlanningError("INVALID_GOVERNANCE_POLICY", "required_stages contains an invalid governance stage")
        resolved = list(dict.fromkeys(str(stage) for stage in stages))
    else:
        resolved = _governance_stages_for_profile(str(plan.get("task_profile", "")))

    # An explicit finalization policy can require PRE_CLOSE even when the
    # profile default is STANDARD. Keep the ordinary phase policy intact and
    # add only the explicitly requested finalization stages so request
    # creation and the finalization gate cannot disagree.
    finalization = plan.get("finalization_policy", {})
    if finalization is not None and not isinstance(finalization, dict):
        raise PlanningError("INVALID_FINALIZATION_POLICY", "PlanPackage finalization_policy must be an object")
    if isinstance(finalization, dict) and "required_governance_stages" in finalization:
        final_stages = finalization["required_governance_stages"]
        if not isinstance(final_stages, list) or any(stage not in GOVERNANCE_STAGES for stage in final_stages):
            raise PlanningError("INVALID_FINALIZATION_POLICY", "required_governance_stages contains an invalid governance stage")
        resolved = list(dict.fromkeys([*resolved, *(str(stage) for stage in final_stages)]))
    return resolved

def _governance_checks(stage: str) -> list[str]:
    checks = {
        "PRE_WRITE": [
            "scope_check",
            "protected_asset_check",
            "dirty_path_isolation",
            "pollution_risk_check",
        ],
        "POST_WRITE": [
            "changed_scope_check",
            "test_evidence_check",
            "documentation_and_index_check",
            "temporary_artifact_check",
            "formal_asset_digest_check",
        ],
        "PRE_CLOSE": [
            "code_check",
            "project_documentation_check",
            "agent_memory_check",
            "rules_and_index_check",
            "knowledge_reference_check",
            "final_cleanliness_acceptance",
        ],
    }
    try:
        return copy.deepcopy(checks[stage])
    except KeyError as exc:
        raise PlanningError("INVALID_GOVERNANCE_STAGE", f"unsupported governance stage: {stage}") from exc

def _governance_request_digest(request: dict[str, Any]) -> str:
    return contracts.contract_digest(request, exclude_fields=("request_id", "requested_at"))

def _governance_request_path(instance: Path, request_id: str) -> Path:
    _safe_component(request_id, "request_id")
    return instance / GOVERNANCE_REQUESTS_DIR / f"{request_id}.json"

def _governance_receipt_path(instance: Path, receipt_id: str) -> Path:
    _safe_component(receipt_id, "receipt_id")
    return instance / GOVERNANCE_RECEIPTS_DIR / f"{receipt_id}.json"

def _build_governance_request(
    envelope: dict[str, Any],
    plan: dict[str, Any],
    checklist: str,
    state_root: Path,
    instance: Path,
    stage: str,
    phase_id: str,
) -> dict[str, Any]:
    if stage not in GOVERNANCE_STAGES:
        raise PlanningError("INVALID_GOVERNANCE_STAGE", f"unsupported governance stage: {stage}")
    required_stages = _governance_required_stages(plan)
    if stage not in required_stages:
        raise PlanningError("GOVERNANCE_STAGE_NOT_REQUIRED", f"{stage} is not required by the PlanPackage")
    _ensure_midcourse_gate_allows_phase(
        plan,
        phase_id,
        state_root=state_root,
        instance=instance,
    )
    phase = _find_plan_phase(plan, phase_id)
    metadata = workflow.extract_machine_json(checklist, "workflow")
    packets = _load_phase_packets(instance, plan, phase_id)

    allowed_paths = _scope_values(plan.get("scope"), include_exclude=False)
    forbidden_paths = _scope_values(plan.get("forbidden_scope"))
    forbidden_paths = _append_unique(forbidden_paths, _string_values(plan.get("forbidden_paths")))
    expected_changes = [f"phase:{phase_id}"]
    objective = phase.get("objective")
    if isinstance(objective, str) and objective:
        expected_changes.append(f"objective:{objective}")
    packet_refs: list[str] = []
    for packet in packets:
        packet_ref = f"{PACKETS_DIR}/{packet['packet_id']}.json"
        packet_refs.append(packet_ref)
        allowed_paths = _append_unique(allowed_paths, _scope_values(packet.get("allowed_scope"), include_exclude=False))
        forbidden_paths = _append_unique(forbidden_paths, _scope_values(packet.get("forbidden_scope")))
        expected_changes.append(f"packet:{packet['packet_id']}")
        outputs = packet.get("expected_outputs", {})
        if isinstance(outputs, dict):
            expected_changes.extend(f"expected_output:{packet['packet_id']}:{key}" for key in sorted(outputs))

    policy = plan.get("governance_policy", {})
    if not isinstance(policy, dict):
        policy = {}
    protected_assets = list(FORMAL_PROTECTED_ASSETS)
    protected_assets = _append_unique(protected_assets, _string_values(policy.get("protected_assets")))
    protected_assets = _append_unique(protected_assets, _string_values(plan.get("protected_assets")))
    for packet in packets:
        requirements = packet.get("governance_requirements", {})
        if isinstance(requirements, dict):
            protected_assets = _append_unique(protected_assets, _string_values(requirements.get("protected_assets")))

    source_evidence = envelope.get("source_evidence", [])
    evidence_refs = ["task-envelope.json", "plan-package.json", workflow.CHECKLIST_NAME]
    for index, item in enumerate(source_evidence if isinstance(source_evidence, list) else []):
        if isinstance(item, str) and item:
            evidence_refs.append(item)
        elif isinstance(item, dict):
            evidence_refs.append(f"task-envelope.json#source_evidence[{index}]")
    evidence_refs = _append_unique(evidence_refs, packet_refs)

    cleanup_policy = plan.get("cleanup_policy", {})
    if not isinstance(cleanup_policy, dict):
        cleanup_policy = {}
    temporary_policy = plan.get("temporary_artifact_policy", cleanup_policy)
    if not isinstance(temporary_policy, dict):
        temporary_policy = copy.deepcopy(cleanup_policy)
    knowledge_policy = plan.get("knowledge_policy", {})
    if not isinstance(knowledge_policy, dict):
        knowledge_policy = {}

    request: dict[str, Any] = {
        "request_id": "pending",
        "task_id": envelope["task_id"],
        "plan_id": plan["plan_id"],
        "plan_version": plan["plan_version"],
        "phase_id": phase_id,
        "governance_stage": stage,
        "allowed_paths": allowed_paths,
        "forbidden_paths": forbidden_paths,
        "expected_changes": _append_unique([], expected_changes),
        "protected_assets": protected_assets,
        "known_dirty_paths": _explicit_dirty_paths(envelope, plan, metadata),
        "temporary_artifact_policy": copy.deepcopy(temporary_policy),
        "cleanup_policy": copy.deepcopy(cleanup_policy),
        "knowledge_policy": copy.deepcopy(knowledge_policy),
        "evidence_refs": _append_unique([], evidence_refs),
        "requested_checks": _governance_checks(stage),
        "requested_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "task_profile": plan.get("task_profile", "UNSPECIFIED"),
        "required_stages": required_stages,
        "packet_refs": packet_refs,
        "source_refs": ["task-envelope.json", "plan-package.json", workflow.CHECKLIST_NAME],
    }
    digest = _governance_request_digest(request)
    request["request_id"] = (
        f"{GOVERNANCE_REQUEST_ID_PREFIX}{_safe_component(envelope['task_id'], 'task_id')}"
        f"-{stage.lower()}-{_safe_component(phase_id, 'phase_id')}-{digest[:12]}"
    )
    try:
        contracts.validate_governance_request(request)
    except workflow.ContractError as exc:
        raise PlanningError("INVALID_CONTRACT", f"generated GovernanceRequest invalid: {exc}") from exc
    return request

def _load_governance_requests(instance: Path) -> list[dict[str, Any]]:
    directory = instance / GOVERNANCE_REQUESTS_DIR
    if not directory.exists():
        return []
    if directory.is_symlink() or not directory.is_dir():
        raise PlanningError("UNSAFE_INSTANCE_ROOT", "governance requests directory must be a real directory")
    result: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        if path.is_symlink():
            raise PlanningError("UNSAFE_INSTANCE_ROOT", f"governance request cannot be a symlink: {path}")
        request = _read_json(path, code="INVALID_GOVERNANCE_REQUEST")
        try:
            contracts.validate_governance_request(request)
        except workflow.ContractError as exc:
            raise PlanningError("INVALID_GOVERNANCE_REQUEST", f"invalid existing governance request {path}: {exc}") from exc
        result.append(request)
    return result

def create_governance_request(
    instance_root: str | Path,
    stage: str,
    phase_id: str,
    *,
    preview: bool = False,
    apply: bool = False,
    agent: str = "planning-with-files",
) -> dict[str, Any]:
    """Build and optionally persist one local GovernanceRequest; never run governance."""
    try:
        if preview == apply:
            raise PlanningError("INVALID_MODE", "exactly one of preview or apply must be true")
        state_root, instance, envelope, plan, checklist = _load_instance(instance_root)
        if (
            stage in {"PRE_WRITE", "PRE_CLOSE"}
            and isinstance(plan.get("governance_profile"), dict)
            and stage not in _governance_required_stages(plan)
        ):
            raise PlanningError("GOVERNANCE_STAGE_DISABLED", f"{stage} is disabled for the active governance profile")
        _ensure_midcourse_gate_evidence(state_root, instance, plan)
        request = _build_governance_request(
            envelope,
            plan,
            checklist,
            state_root,
            instance,
            stage,
            phase_id,
        )
        existing_requests = _load_governance_requests(instance)
        trigger_receipt = _latest_retryable_governance_receipt(
            instance,
            task_id=request["task_id"],
            plan_id=request["plan_id"],
            phase_id=request["phase_id"],
            stage=request["governance_stage"],
        )
        request, _is_retry = _select_governance_retry_request(
            request,
            existing_requests,
            trigger_receipt,
        )
        request_path = _governance_request_path(instance, request["request_id"])
        if request_path.exists():
            if request_path.is_symlink():
                raise PlanningError("UNSAFE_INSTANCE_ROOT", "governance request cannot be a symlink")
            existing = _read_json(request_path, code="INVALID_GOVERNANCE_REQUEST")
            try:
                contracts.validate_governance_request(existing)
            except workflow.ContractError as exc:
                raise PlanningError("GOVERNANCE_REQUEST_CONFLICT", f"existing request is invalid: {exc}", result="CONFLICT") from exc
            if _governance_request_digest(existing) == _governance_request_digest(request):
                return {
                    "result": "EXISTING_GOVERNANCE_REQUEST",
                    "request_id": request["request_id"],
                    "request_path": str(request_path),
                    "request": existing,
                    "no_op": True,
                    "idempotent": True,
                }
            raise PlanningError("GOVERNANCE_REQUEST_CONFLICT", "same request_id has different content", result="CONFLICT")
        result: dict[str, Any] = {
            "result": "PREVIEW" if preview else "CREATED_GOVERNANCE_REQUEST",
            "request_id": request["request_id"],
            "request_digest": _governance_request_digest(request),
            "request_path": str(request_path),
            "request": request,
            "planned_files": [f"{GOVERNANCE_REQUESTS_DIR}/{request['request_id']}.json"],
            "created_files": [],
            "no_op": False,
            "state_root": str(state_root),
            "instance_path": str(instance),
        }
        if preview:
            return result
        relative = f"{GOVERNANCE_REQUESTS_DIR}/{request['request_id']}.json"
        _transaction_write(
            instance,
            state_root,
            {relative: contracts.stable_json(request)},
            expected_digests={relative: workflow.sha256_digest("")},
            lock_target=relative,
            lock_name="governance-request",
            agent=agent,
            transaction_tag="f1-04",
        )
        stored = _read_json(request_path, code="INVALID_GOVERNANCE_REQUEST")
        contracts.validate_governance_request(stored)
        if contracts.stable_json(stored) != contracts.stable_json(request):
            raise PlanningError("FAILED", "published GovernanceRequest changed unexpectedly")
        result["created_files"] = [relative]
        return result
    except PlanningError as exc:
        return _result_error(exc)
    except (OSError, ValueError, workflow.ContractError) as exc:
        return _result_error(PlanningError("INTERNAL_ERROR", str(exc)))

def _validate_governance_candidate_actions(receipt: dict[str, Any]) -> None:
    for field in ("duplicate_candidates", "unused_asset_candidates"):
        for item in receipt.get(field, []):
            if item not in GOVERNANCE_CANDIDATE_CLASSES:
                raise PlanningError("INVALID_CANDIDATE_CATEGORY", f"unsupported {field} category: {item}")
    for action in receipt.get("cleanup_actions", []):
        lowered = action.lower()
        if ("delete" in lowered or "删除" in action) and (
            "skill" in lowered or "自动" in action or "auto" in lowered or "不可逆" in action
        ):
            raise PlanningError("FORBIDDEN_AUTOMATIC_DELETE", "cleanup receipt contains an automatic Skill deletion action")

def _governance_receipt_digest(receipt: dict[str, Any]) -> str:
    return contracts.contract_digest(receipt, exclude_fields=("receipt_id", "checked_at"))

def _governance_state_update(
    checklist: str,
    request: dict[str, Any],
    receipt: dict[str, Any],
    receipt_ref: str,
) -> tuple[str, dict[str, Any]]:
    metadata = workflow.extract_machine_json(checklist, "workflow")
    decision = contracts.governance_decision(receipt)
    result = receipt["result"]
    previous_governance_status = metadata.get("governance_status")
    previous_governance_stage = metadata.get("governance_stage")
    receipt_ref_list = _string_values(metadata.get("governance_receipt_refs"))
    receipt_ref_list = _append_unique(receipt_ref_list, [receipt_ref])
    metadata["governance_status"] = result
    metadata["governance_stage"] = receipt["governance_stage"]
    metadata["governance_request_ref"] = f"{GOVERNANCE_REQUESTS_DIR}/{request['request_id']}.json"
    metadata["last_governance_receipt_ref"] = receipt_ref
    metadata["governance_receipt_refs"] = receipt_ref_list
    metadata["governance_decision"] = copy.deepcopy(decision)
    metadata["governance_can_progress"] = decision["can_progress"]
    metadata["governance_requires_human_gate"] = decision["requires_human_gate"]
    current_blocking = _string_values(metadata.get("blocking_findings"))
    if result in {"BLOCKED", "INCONCLUSIVE"}:
        metadata["governance_blocking_findings"] = _append_unique([], receipt["blocking_findings"])
        metadata["blocking_findings"] = _append_unique(current_blocking, receipt["blocking_findings"])
    elif previous_governance_stage == receipt["governance_stage"] and previous_governance_status in {"BLOCKED", "INCONCLUSIVE"}:
        # ``blocking_findings`` is the checklist's governance projection, not
        # an independent business-failure ledger. A successful retry for the
        # same stage therefore supersedes the entire stale projection. This
        # also repairs v0.8/v0.9 instances and instances with multiple failed
        # attempts, where only the latest receipt's findings were retained.
        metadata["blocking_findings"] = []
        metadata["governance_blocking_findings"] = []
    else:
        metadata["governance_blocking_findings"] = []
        metadata["blocking_findings"] = _append_unique(current_blocking, receipt["blocking_findings"])
    metadata["non_blocking_findings"] = _append_unique(_string_values(metadata.get("non_blocking_findings")), receipt["non_blocking_findings"])
    metadata["evidence_refs"] = _append_unique(_string_values(metadata.get("evidence_refs")), receipt["evidence_refs"])
    metadata["verification_status"] = "已核验" if decision["can_progress"] else ("待人工裁决" if result == "INCONCLUSIVE" else "阻塞")
    if result in {"BLOCKED", "INCONCLUSIVE"}:
        metadata["overall_status"] = "阻塞"
        metadata["recommended_next_task"] = request["phase_id"]
        metadata["human_execution_gate"] = "WAITING_FOR_OWNER_F1-04" if result == "INCONCLUSIVE" else "BLOCKED_BY_GOVERNANCE"
    elif metadata.get("overall_status") not in {"阻塞", "失败"}:
        metadata["overall_status"] = "进行中"
    elif (
        previous_governance_stage == receipt["governance_stage"]
        and previous_governance_status in {"BLOCKED", "INCONCLUSIVE"}
        and not metadata["blocking_findings"]
    ):
        metadata["overall_status"] = "进行中"
        metadata["human_execution_gate"] = "OPEN_FOR_FINALIZATION" if receipt["governance_stage"] == "PRE_CLOSE" else "OPEN"
    metadata["current_phase"] = request["phase_id"]
    metadata["last_updated_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    metadata["checklist_version"] = workflow.bump_semver(metadata["checklist_version"], "PATCH")
    history = metadata.get("change_history", [])
    if not isinstance(history, list):
        history = []
    history.append({
        "timestamp": metadata["last_updated_at"],
        "change_type": "GOVERNANCE_RECEIPT",
        "request_id": request["request_id"],
        "receipt_id": receipt["receipt_id"],
        "result": result,
        "version_classification": "PATCH",
    })
    metadata["change_history"] = history
    updated = workflow.replace_machine_json(checklist, "workflow", metadata)
    human_updates = {
        "治理阶段：": receipt["governance_stage"],
        "治理状态：": result,
        "治理回执：": receipt_ref,
    }
    if receipt["non_blocking_findings"]:
        human_updates["治理警告："] = "；".join(receipt["non_blocking_findings"])
    if receipt["blocking_findings"]:
        human_updates["治理阻塞："] = "；".join(receipt["blocking_findings"])
    updated = _upsert_human_summary_lines(updated, human_updates)
    try:
        workflow.validate_checklist_text(updated)
    except workflow.ContractError as exc:
        raise PlanningError("INVALID_CHECKLIST", f"updated checklist invalid: {exc}") from exc
    return updated, {
        "governance_stage": receipt["governance_stage"],
        "governance_status": result,
        "can_progress": decision["can_progress"],
        "requires_human_gate": decision["requires_human_gate"],
        "receipt_ref": receipt_ref,
        "version_classification": "PATCH",
    }

def _find_plan_phase(plan: dict[str, Any], phase_id: str) -> dict[str, Any]:
    _safe_component(phase_id, "phase_id")
    for phase in plan.get("phases", []):
        if isinstance(phase, dict) and phase.get("phase_id") == phase_id:
            return copy.deepcopy(phase)
    raise PlanningError("INVALID_PHASE_ID", f"phase_id does not exist in PlanPackage: {phase_id}")

def _load_phase_packets(instance: Path, plan: dict[str, Any], phase_id: str) -> list[dict[str, Any]]:
    packets_dir = instance / PACKETS_DIR
    if not packets_dir.exists():
        return []
    if packets_dir.is_symlink() or not packets_dir.is_dir():
        raise PlanningError("UNSAFE_INSTANCE_ROOT", "packets directory must be a real directory")
    result: list[dict[str, Any]] = []
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
        if packet["phase_id"] == phase_id:
            result.append(packet)
    return result

def _load_cleanliness_receipts(instance: Path) -> list[dict[str, Any]]:
    directory = instance / GOVERNANCE_RECEIPTS_DIR
    if not directory.exists():
        return []
    if directory.is_symlink() or not directory.is_dir():
        raise PlanningError("UNSAFE_INSTANCE_ROOT", "governance receipts directory must be a real directory")
    result: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        if path.is_symlink():
            raise PlanningError("UNSAFE_INSTANCE_ROOT", f"cleanliness receipt cannot be a symlink: {path}")
        receipt = _read_json(path, code="INVALID_CLEANLINESS_RECEIPT")
        try:
            contracts.validate_governance_receipt(receipt)
        except workflow.ContractError as exc:
            raise PlanningError("INVALID_CLEANLINESS_RECEIPT", f"invalid existing cleanliness receipt {path}: {exc}") from exc
        result.append(receipt)
    return result

def _latest_retryable_governance_receipt(
    instance: Path,
    *,
    task_id: str,
    plan_id: str,
    phase_id: str,
    stage: str,
) -> dict[str, Any] | None:
    candidates = [
        receipt
        for receipt in _load_cleanliness_receipts(instance)
        if receipt.get("task_id") == task_id
        and receipt.get("plan_id") == plan_id
        and receipt.get("phase_id") == phase_id
        and receipt.get("governance_stage") == stage
    ]
    if not candidates:
        return None
    latest = max(
        candidates,
        key=lambda item: (
            _parse_timestamp(str(item["checked_at"])),
            str(item["receipt_id"]),
        ),
    )
    return latest if latest.get("result") in {"BLOCKED", "INCONCLUSIVE"} else None

def _select_governance_retry_request(
    request: dict[str, Any],
    existing_requests: list[dict[str, Any]],
    trigger_receipt: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    """Return the current request or one immutable child retry request."""
    if trigger_receipt is None:
        return request, False

    children = [
        item
        for item in existing_requests
        if item.get("task_id") == request.get("task_id")
        and item.get("plan_id") == request.get("plan_id")
        and item.get("phase_id") == request.get("phase_id")
        and item.get("governance_stage") == request.get("governance_stage")
        and item.get("retry_of_request_id") == trigger_receipt.get("request_id")
        and item.get("retry_trigger_receipt_id") == trigger_receipt.get("receipt_id")
    ]
    if len(children) > 1:
        raise PlanningError(
            "GOVERNANCE_RETRY_CONFLICT",
            "multiple retry requests reference the same blocked governance receipt",
            result="CONFLICT",
        )
    if children:
        return children[0], True

    sequences = [
        item.get("retry_sequence")
        for item in existing_requests
        if item.get("task_id") == request.get("task_id")
        and item.get("plan_id") == request.get("plan_id")
        and item.get("phase_id") == request.get("phase_id")
        and item.get("governance_stage") == request.get("governance_stage")
        and isinstance(item.get("retry_sequence"), int)
        and not isinstance(item.get("retry_sequence"), bool)
        and item.get("retry_sequence", 0) > 0
    ]
    retry = copy.deepcopy(request)
    retry["retry_sequence"] = (max(sequences) if sequences else 0) + 1
    retry["retry_of_request_id"] = str(trigger_receipt["request_id"])
    retry["retry_trigger_receipt_id"] = str(trigger_receipt["receipt_id"])
    retry["retry_reason"] = "PREVIOUS_GOVERNANCE_RECEIPT_BLOCKED_OR_INCONCLUSIVE"
    digest = _governance_request_digest(retry)
    retry["request_id"] = (
        f"{GOVERNANCE_REQUEST_ID_PREFIX}{_safe_component(retry['task_id'], 'task_id')}"
        f"-{retry['governance_stage'].lower()}-{_safe_component(retry['phase_id'], 'phase_id')}-{digest[:12]}"
    )
    try:
        contracts.validate_governance_request(retry)
    except workflow.ContractError as exc:
        raise PlanningError("INVALID_CONTRACT", f"generated GovernanceRequest retry invalid: {exc}") from exc
    return retry, False

def _upsert_human_summary_lines(text: str, updates: dict[str, str]) -> str:
    lines = text.splitlines()
    found: set[str] = set()
    for index, line in enumerate(lines):
        for label, value in updates.items():
            if line.startswith(label):
                lines[index] = f"{label}{value}"
                found.add(label)
    missing = [f"{label}{value}" for label, value in updates.items() if label not in found]
    if missing:
        insert_at = next((i for i, line in enumerate(lines) if line.strip() == "## 工作流任务清单"), len(lines))
        lines[insert_at:insert_at] = ["", *missing]
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
