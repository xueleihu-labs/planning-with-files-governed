"""Gate 2 extracted module: outcomes.py.

Generated from the Gate 1 planning.py baseline.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from typing import Iterable
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
    _validate_checkpoint_reference,
)
from pwf_governed.core.constants import (
    CHECKPOINT_REFS_DIR,
    CURRENT_VERSION,
    EVOLUTION_SIGNALS_BY_CHECKPOINT_DIR,
    EVOLUTION_SIGNAL_RELATIVE,
    GOVERNANCE_RECEIPTS_DIR,
    KNOWLEDGE_HANDOFFS_BY_CHECKPOINT_DIR,
    KNOWLEDGE_HANDOFF_RELATIVE,
    RECEIPTS_DIR,
    ROUTING_DECISIONS_BY_CHECKPOINT_DIR,
    ROUTING_DECISION_RELATIVE,
    SENSITIVE_MARKERS,
)
from pwf_governed.core.envelope import (
    _append_unique,
    _load_instance,
    _merge_count_maps,
    _merge_dicts,
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
from pwf_governed.edition import adapt_output_once, edition_operation
from pwf_governed.governance import (
    _load_cleanliness_receipts,
    _upsert_human_summary_lines,
)
from pwf_governed.receipts import (
    _load_receipts,
)
from pwf_governed.shared.checkpoint_support import (
    _load_checkpoint_refs,
    _resolve_checkpoint_file,
)

def _outcome_target(instance: Path, relative: str) -> Path:
    target = instance / relative
    current = instance
    for part in Path(relative).parts[:-1]:
        current = current / part
        if current.exists() and (current.is_symlink() or not current.is_dir()):
            raise PlanningError("UNSAFE_INSTANCE_ROOT", f"outcome directory is not a real directory: {current}")
    if target.exists() and target.is_symlink():
        raise PlanningError("UNSAFE_INSTANCE_ROOT", f"outcome file cannot be a symlink: {relative}")
    return target

def _routing_decision_relative(checkpoint_id: str | None) -> str:
    """Return the immutable routing record path for one checkpoint.

    A missing checkpoint keeps the v0.9.0 singleton behavior for legacy
    instances.  Once a trusted checkpoint is present, the checkpoint becomes
    part of the storage identity and the legacy singleton is never reused for
    a different checkpoint.
    """
    if checkpoint_id is None:
        return ROUTING_DECISION_RELATIVE
    return f"{ROUTING_DECISIONS_BY_CHECKPOINT_DIR}/{_safe_component(checkpoint_id, 'checkpoint_id')}.json"

def _evolution_signal_relative(checkpoint_id: str | None) -> str:
    if checkpoint_id is None:
        return EVOLUTION_SIGNAL_RELATIVE
    return f"{EVOLUTION_SIGNALS_BY_CHECKPOINT_DIR}/{_safe_component(checkpoint_id, 'checkpoint_id')}.json"

def _knowledge_handoff_relative(checkpoint_id: str | None) -> str:
    if checkpoint_id is None:
        return KNOWLEDGE_HANDOFF_RELATIVE
    return f"{KNOWLEDGE_HANDOFFS_BY_CHECKPOINT_DIR}/{_safe_component(checkpoint_id, 'checkpoint_id')}.json"

def _routing_decision_location(instance: Path, checkpoint_id: str | None) -> Path:
    """Locate a routing decision without treating the legacy singleton as latest."""
    specific = _outcome_target(instance, _routing_decision_relative(checkpoint_id))
    if specific.is_file():
        return specific
    if checkpoint_id is None:
        return _outcome_target(instance, ROUTING_DECISION_RELATIVE)
    legacy = _outcome_target(instance, ROUTING_DECISION_RELATIVE)
    if legacy.is_file():
        value = _read_json(legacy, code="INVALID_ROUTING_DECISION")
        try:
            contracts.validate_routing_decision(value)
        except workflow.ContractError as exc:
            raise PlanningError("INVALID_ROUTING_DECISION", str(exc)) from exc
        if value.get("checkpoint_id") == checkpoint_id:
            return legacy
    return specific

def _outcome_timestamp(envelope: dict[str, Any], plan: dict[str, Any]) -> str:
    return str(plan.get("created_at") or envelope.get("created_at"))

def _outcome_policy(envelope: dict[str, Any], plan: dict[str, Any], key: str) -> dict[str, Any]:
    return _merge_dicts(envelope.get(key), plan.get(key))

def _outcome_source_refs(
    envelope: dict[str, Any],
    plan: dict[str, Any],
    checklist: str,
    receipts: list[dict[str, Any]],
    cleanliness_receipts: list[dict[str, Any]],
    checkpoint_refs: list[dict[str, Any]],
) -> list[str]:
    refs = ["task-envelope.json", "plan-package.json", workflow.CHECKLIST_NAME]
    for index, item in enumerate(envelope.get("source_evidence", [])):
        refs.append(item if isinstance(item, str) else f"task-envelope.json#source_evidence[{index}]")
    for item in plan.get("evidence_policy", {}).get("source_evidence", []) if isinstance(plan.get("evidence_policy"), dict) else []:
        if isinstance(item, str):
            refs.append(item)
    for receipt in receipts:
        refs.append(f"{RECEIPTS_DIR}/{receipt['receipt_id']}.json")
        refs.extend(_string_values(receipt.get("evidence_refs")))
    for receipt in cleanliness_receipts:
        refs.append(f"{GOVERNANCE_RECEIPTS_DIR}/{receipt['receipt_id']}.json")
        refs.extend(_string_values(receipt.get("evidence_refs")))
    for ref in checkpoint_refs:
        refs.append(f"{CHECKPOINT_REFS_DIR}/{ref['checkpoint_id']}.json")
        refs.extend(_string_values(ref.get("evidence_refs")))
    return _append_unique([], [item for item in refs if isinstance(item, str) and item])

def _outcome_counts(
    metadata: dict[str, Any],
    receipts: list[dict[str, Any]],
    cleanliness_receipts: list[dict[str, Any]],
    evolution_policy: dict[str, Any],
) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    failures: dict[str, int] = {}
    corrections: dict[str, int] = {}
    manual_actions: dict[str, int] = {}

    for receipt in receipts:
        if receipt.get("result") in {"FAILED", "BLOCKED", "INCONCLUSIVE"}:
            key = f"execution:{receipt.get('phase_id', 'unknown')}"
            failures[key] = failures.get(key, 0) + 1
    for receipt in cleanliness_receipts:
        if receipt.get("result") in {"BLOCKED", "INCONCLUSIVE"}:
            key = f"governance:{receipt.get('governance_stage', 'unknown')}"
            failures[key] = failures.get(key, 0) + 1

    history = metadata.get("change_history", [])
    if isinstance(history, list):
        for item in history:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("change_type", "")).upper()
            fingerprint = str(item.get("fingerprint") or item.get("category") or kind or "unknown")
            if any(token in kind for token in ("MANUAL_CORRECTION", "RULE_CORRECTION", "MANUAL_FIX")):
                corrections[fingerprint] = corrections.get(fingerprint, 0) + 1
            if any(token in kind for token in ("MANUAL_ACTION", "MANUAL_EXECUTION", "MANUAL_OPERATION")):
                manual_actions[fingerprint] = manual_actions.get(fingerprint, 0) + 1

    failures = _merge_count_maps(failures, evolution_policy.get("failure_counts"))
    corrections = _merge_count_maps(corrections, evolution_policy.get("correction_counts"))
    manual_actions = _merge_count_maps(manual_actions, evolution_policy.get("manual_action_counts"))
    return failures, corrections, manual_actions

def _candidate_values(policy: dict[str, Any], key: str) -> list[str]:
    value = policy.get(key, [])
    return _append_unique([], [item for item in value if isinstance(item, str) and item.strip()]) if isinstance(value, list) else []

def _outcome_state_update(
    checklist: str,
    decision: dict[str, Any],
    *,
    outcome_ref: str,
    receipt_kind: str | None = None,
    receipt_ref: str | None = None,
    receipt_result: str | None = None,
) -> tuple[str, dict[str, Any]]:
    metadata = workflow.extract_machine_json(checklist, "workflow")
    metadata["checklist_version"] = workflow.bump_semver(metadata["checklist_version"], "PATCH")
    metadata["outcome_routing_ref"] = outcome_ref
    metadata["outcome_decision"] = decision["decision"]
    metadata["outcome_human_review_required"] = decision["human_review_required"]
    metadata["evolution_status"] = "READY_FOR_BRIDGE" if decision["evolution_required"] else "NO_EVOLUTION"
    metadata["content_status"] = "READY_FOR_INGEST" if decision["content_required"] else "NOT_REQUIRED"
    if decision["human_review_required"]:
        metadata["evolution_status"] = "HUMAN_REVIEW_REQUIRED" if decision["evolution_required"] else metadata["evolution_status"]
        metadata["content_status"] = "HUMAN_REVIEW_REQUIRED" if decision["content_required"] else metadata["content_status"]
        metadata["human_execution_gate"] = "REQUIRED"
        metadata["overall_status"] = "暂停"
    else:
        metadata["overall_status"] = "进行中"
    for field, value in (
        ("evolution_signal_ref", decision.get("evolution_signal_ref")),
        ("knowledge_handoff_ref", decision.get("knowledge_handoff_ref")),
        ("evolution_receipt_ref", decision.get("evolution_receipt_ref")),
        ("content_ingest_receipt_ref", decision.get("content_ingest_receipt_ref")),
    ):
        if value is not None:
            metadata[field] = value
    if receipt_kind == "evolution" and receipt_ref:
        metadata["evolution_receipt_ref"] = receipt_ref
        metadata["evolution_status"] = receipt_result or metadata["evolution_status"]
    if receipt_kind == "content" and receipt_ref:
        metadata["content_ingest_receipt_ref"] = receipt_ref
        metadata["content_status"] = receipt_result or metadata["content_status"]
    metadata["outcome_evidence_refs"] = _append_unique(
        _string_values(metadata.get("outcome_evidence_refs")), decision["evidence_refs"]
    )
    metadata["last_updated_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    history = metadata.get("change_history", [])
    if not isinstance(history, list):
        history = []
    history.append({
        "timestamp": metadata["last_updated_at"],
        "change_type": "EVOLUTION_RECEIPT" if receipt_kind == "evolution" else "CONTENT_INGEST_RECEIPT" if receipt_kind == "content" else "OUTCOME_ROUTING",
        "decision_id": decision["decision_id"],
        "result": receipt_result or decision["decision"],
        "version_classification": "PATCH",
    })
    metadata["change_history"] = history
    updated = workflow.replace_machine_json(checklist, "workflow", metadata)
    human_updates = {
        "成果分流：": decision["decision"],
        "进化状态：": metadata["evolution_status"],
        "内容状态：": metadata["content_status"],
    }
    if decision["human_review_required"]:
        human_updates["成果分流人工闸门："] = "REQUIRED"
    updated = _upsert_human_summary_lines(updated, human_updates)
    workflow.validate_checklist_text(updated)
    return updated, {
        "decision": decision["decision"],
        "evolution_status": metadata["evolution_status"],
        "content_status": metadata["content_status"],
        "human_review_required": decision["human_review_required"],
        "version_classification": "PATCH",
    }

def _outcome_digest(value: dict[str, Any], *, exclude: Iterable[str] = ()) -> str:
    return contracts.contract_digest(value, exclude_fields=tuple(exclude))

def _load_routing_decision(instance: Path, checkpoint_id: str | None = None) -> dict[str, Any]:
    path = _routing_decision_location(instance, checkpoint_id)
    if not path.is_file():
        suffix = f" for checkpoint {checkpoint_id}" if checkpoint_id else ""
        raise PlanningError("OUTCOME_ROUTING_NOT_FOUND", f"routing decision does not exist{suffix}")
    value = _read_json(path, code="INVALID_ROUTING_DECISION")
    try:
        contracts.validate_routing_decision(value)
    except workflow.ContractError as exc:
        raise PlanningError("INVALID_ROUTING_DECISION", str(exc)) from exc
    return value

def _outcome_dedupe_conflict(
    instance: Path,
    dedupe_key: str,
    decision_id: str,
    checkpoint_id: str | None = None,
) -> None:
    try:
        existing = _load_routing_decision(instance, checkpoint_id)
    except PlanningError as exc:
        if exc.code == "OUTCOME_ROUTING_NOT_FOUND":
            return
        raise
    if existing.get("decision_id") != decision_id and existing.get("dedupe_key") == dedupe_key:
        raise PlanningError("OUTCOME_ROUTING_CONFLICT", "same outcome dedupe_key has different routing decision", result="CONFLICT")

def _routing_checkpoint_provenance(
    envelope: dict[str, Any],
    plan: dict[str, Any],
    checklist: str,
    state_root: Path,
    instance: Path,
    checkpoint_refs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Validate the current trusted checkpoint before creating a route.

    Legacy instances without a checkpoint keep the v0.9.0 singleton route.
    Once local CheckpointRefs exist, routing is fail-closed and must bind to
    the checklist's current trusted ref, its canonical state root, lineage,
    audit projection and published read-head.
    """
    if not checkpoint_refs:
        return None
    metadata = workflow.extract_machine_json(checklist, "workflow")
    checkpoint_id = metadata.get("last_trusted_checkpoint")
    if not isinstance(checkpoint_id, str) or not checkpoint_id:
        raise PlanningError("OUTCOME_ROUTING_NOT_ELIGIBLE", "checkpoint refs exist but no trusted checkpoint is projected")
    matching = [item for item in checkpoint_refs if item.get("checkpoint_id") == checkpoint_id]
    if len(matching) != 1:
        raise PlanningError("OUTCOME_ROUTING_NOT_ELIGIBLE", f"trusted checkpoint ref is missing or ambiguous: {checkpoint_id}")
    ref = matching[0]
    try:
        current_created = _parse_timestamp(str(ref["created_at"]))
    except (KeyError, ValueError) as exc:
        raise PlanningError("OUTCOME_ROUTING_NOT_ELIGIBLE", f"invalid checkpoint timestamp: {checkpoint_id}") from exc
    for candidate in checkpoint_refs:
        if candidate.get("checkpoint_id") == checkpoint_id:
            continue
        try:
            candidate_created = _parse_timestamp(str(candidate["created_at"]))
        except (KeyError, ValueError) as exc:
            raise PlanningError("OUTCOME_ROUTING_NOT_ELIGIBLE", f"invalid checkpoint timestamp: {candidate.get('checkpoint_id')}") from exc
        if candidate_created > current_created:
            raise PlanningError("STALE_CHECKPOINT", f"checkpoint {checkpoint_id} is older than {candidate.get('checkpoint_id')}", result="CONFLICT")

    try:
        context = _validate_checkpoint_reference(ref, envelope, plan, checklist, state_root, instance)
    except PlanningError as exc:
        raise PlanningError("OUTCOME_ROUTING_NOT_ELIGIBLE", exc.message, result=exc.result) from exc
    projection = context["projection"]
    if ref.get("checkpoint_consumer_status") != "VERIFIED":
        raise PlanningError("OUTCOME_ROUTING_NOT_ELIGIBLE", "checkpoint consumer is not VERIFIED")
    if ref.get("checkpoint_status") != "PASSED" or ref.get("verification_status") != "PASSED":
        raise PlanningError("OUTCOME_ROUTING_NOT_ELIGIBLE", "checkpoint is not PASSED")
    if ref.get("effective_action") != "ADVANCE_PHASE" or ref.get("publication_status") != "PUBLISHED_COMMIT":
        raise PlanningError("OUTCOME_ROUTING_NOT_ELIGIBLE", "checkpoint read-head is not ADVANCE_PHASE/PUBLISHED_COMMIT")
    if projection.get("effective_action") != "ADVANCE_PHASE":
        raise PlanningError("OUTCOME_ROUTING_NOT_ELIGIBLE", "checkpoint projection is not ADVANCE_PHASE")
    if metadata.get("checkpoint_consumer_status") != "VERIFIED" or metadata.get("checkpoint_action") != "ADVANCE_PHASE":
        raise PlanningError("OUTCOME_ROUTING_NOT_ELIGIBLE", "checklist checkpoint projection is not trusted")
    if metadata.get("resume_status") not in {"RESUMED", "READY"} or metadata.get("task_status") != "READY":
        raise PlanningError("OUTCOME_ROUTING_NOT_ELIGIBLE", "resume-from-checkpoint is not READY/RESUMED")
    if metadata.get("current_phase") != ref.get("phase_id"):
        raise PlanningError("OUTCOME_ROUTING_NOT_ELIGIBLE", "current phase differs from trusted checkpoint")

    canonical_root_value = ref.get("canonical_state_root")
    lineage_digest = ref.get("lineage_digest")
    if not isinstance(canonical_root_value, str) or not Path(canonical_root_value).is_absolute():
        raise PlanningError("OUTCOME_ROUTING_NOT_ELIGIBLE", "canonical checkpoint state root is missing or not absolute")
    canonical_root = Path(canonical_root_value).resolve(strict=False)
    state_resolved = state_root.resolve(strict=False)
    if canonical_root == Path("/") or not (canonical_root == state_resolved or state_resolved in canonical_root.parents):
        raise PlanningError("OUTCOME_ROUTING_NOT_ELIGIBLE", "canonical checkpoint state root is outside the task state root")
    if not isinstance(lineage_digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", lineage_digest):
        raise PlanningError("OUTCOME_ROUTING_NOT_ELIGIBLE", "checkpoint lineage digest is missing or invalid")

    root_binding_location = ref.get("root_binding_location")
    if not isinstance(root_binding_location, str):
        raise PlanningError("OUTCOME_ROUTING_NOT_ELIGIBLE", "checkpoint root binding is missing")
    try:
        root_binding_path, _ = _resolve_checkpoint_file(state_root, instance, root_binding_location, label="root_binding_location")
    except PlanningError as exc:
        raise PlanningError("OUTCOME_ROUTING_NOT_ELIGIBLE", exc.message, result=exc.result) from exc
    root_binding_digest = workflow.file_digest(root_binding_path)
    if ref.get("root_binding_sha256") and ref["root_binding_sha256"].lower() != root_binding_digest:
        raise PlanningError("CHECKPOINT_PROJECTION_DRIFT", "checkpoint root binding digest changed", result="CONFLICT")
    root_binding = _read_json(root_binding_path, code="INVALID_CHECKPOINT_ROOT_BINDING")
    if root_binding.get("lineage_digest") != lineage_digest:
        raise PlanningError("CHECKPOINT_PROJECTION_DRIFT", "checkpoint lineage digest differs from root binding", result="CONFLICT")
    for field in ("task_id", "plan_id", "phase_id"):
        if root_binding.get(field) not in {None, ref.get(field), envelope.get(field), plan.get(field)}:
            raise PlanningError("CHECKPOINT_PROJECTION_DRIFT", f"root binding {field} does not match checkpoint", result="CONFLICT")

    audit_path_value = ref.get("audit_path")
    audit_expected = ref.get("audit_sha256")
    if not isinstance(audit_path_value, str) or not isinstance(audit_expected, str):
        raise PlanningError("OUTCOME_ROUTING_NOT_ELIGIBLE", "audit path or digest is missing")
    try:
        audit_path, _ = _resolve_checkpoint_file(state_root, instance, audit_path_value, label="audit_path")
    except PlanningError as exc:
        raise PlanningError("OUTCOME_ROUTING_NOT_ELIGIBLE", exc.message, result=exc.result) from exc
    if workflow.file_digest(audit_path) != audit_expected.lower():
        raise PlanningError("CHECKPOINT_PROJECTION_DRIFT", "audit digest changed", result="CONFLICT")

    head_location = ref.get("head_location")
    if not isinstance(head_location, str):
        raise PlanningError("OUTCOME_ROUTING_NOT_ELIGIBLE", "published read-head location is missing")
    try:
        head_path, _ = _resolve_checkpoint_file(state_root, instance, head_location, label="head_location")
    except PlanningError as exc:
        raise PlanningError("OUTCOME_ROUTING_NOT_ELIGIBLE", exc.message, result=exc.result) from exc
    head_digest = workflow.file_digest(head_path)
    ref_path = _checkpoint_path(instance, checkpoint_id, CHECKPOINT_REFS_DIR)
    if not ref_path.is_file() or ref_path.is_symlink():
        raise PlanningError("OUTCOME_ROUTING_NOT_ELIGIBLE", "local CheckpointRef file is missing or unsafe")
    checkpoint_ref_digest = workflow.file_digest(ref_path)
    phase_id = str(ref["phase_id"])
    resume_entry = str(ref.get("resume_entry", ""))
    work_item_id = resume_entry.rsplit("/", 1)[-1] if "/" in resume_entry else resume_entry
    if not work_item_id:
        raise PlanningError("OUTCOME_ROUTING_NOT_ELIGIBLE", "checkpoint resume entry has no work item")
    return {
        "routing_provenance_version": "1.0.0",
        "phase_id": phase_id,
        "work_item_id": work_item_id,
        "checkpoint_id": checkpoint_id,
        "canonical_checkpoint_state_root": str(canonical_root),
        "checkpoint_lineage_digest": lineage_digest.lower(),
        "checkpoint_ref_digest": checkpoint_ref_digest,
        "checkpoint_ref_path": ref_path.relative_to(instance).as_posix(),
        "read_head": {
            "decision": str(ref.get("decision") or ref.get("effective_action")),
            "publication_status": str(ref.get("publication_status")),
            "verification_status": str(ref.get("verification_status")),
            "head_digest": head_digest,
            "head_location": head_location,
        },
        "resume_status": metadata.get("resume_status"),
        "scoped_baseline": copy.deepcopy(ref.get("scoped_baseline")),
    }

def _build_outcome_routing(
    envelope: dict[str, Any],
    plan: dict[str, Any],
    checklist: str,
    state_root: Path,
    instance: Path,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    metadata = workflow.extract_machine_json(checklist, "workflow")
    receipts = _load_receipts(instance)
    cleanliness = _load_cleanliness_receipts(instance)
    checkpoint_refs = _load_checkpoint_refs(instance)
    provenance = _routing_checkpoint_provenance(envelope, plan, checklist, state_root, instance, checkpoint_refs)
    evidence_refs = _outcome_source_refs(envelope, plan, checklist, receipts, cleanliness, checkpoint_refs)
    evolution_policy = _outcome_policy(envelope, plan, "evolution_policy")
    evolution = _evolution_analysis(metadata, evolution_policy, receipts, cleanliness)
    content = _content_analysis(envelope, plan, evidence_refs)
    human_required = evolution["human_review_required"] or content["human_review_required"]
    evolution_required = evolution["has_value"]
    content_required = content["has_value"]
    if human_required:
        decision_name = "HUMAN_REVIEW_REQUIRED"
    elif evolution_required and content_required:
        decision_name = "EVOLUTION_AND_CONTENT"
    elif evolution_required:
        decision_name = "EVOLUTION_ONLY"
    elif content_required:
        decision_name = "CONTENT_ONLY"
    else:
        decision_name = "NO_VALUE"
    identity = {
        "task_id": envelope["task_id"],
        "plan_id": plan["plan_id"],
        "plan_version": plan["plan_version"],
        "phase_id": metadata.get("current_phase"),
        "evolution": evolution,
        "content": content,
        "evidence_refs": evidence_refs,
    }
    if provenance is not None:
        identity["checkpoint_provenance"] = provenance
    dedupe_key = contracts.contract_digest(identity)
    decision_id = f"route-{_safe_component(envelope['task_id'], 'task_id')}-{dedupe_key[:12]}"
    checkpoint_id = provenance["checkpoint_id"] if provenance is not None else metadata.get("last_trusted_checkpoint") or (checkpoint_refs[-1]["checkpoint_id"] if checkpoint_refs else None)
    signal_relative = _evolution_signal_relative(checkpoint_id)
    handoff_relative = _knowledge_handoff_relative(checkpoint_id)
    decision = {
        "decision_id": decision_id,
        "task_id": envelope["task_id"],
        "plan_id": plan["plan_id"],
        "checkpoint_id": checkpoint_id,
        "decision": decision_name,
        "evolution_required": evolution_required,
        "content_required": content_required,
        "content_judgment": copy.deepcopy(content["content_judgment"]),
        "evolution_signal_ref": signal_relative if evolution_required else None,
        "knowledge_handoff_ref": handoff_relative if content_required and not content["human_review_required"] else None,
        "evolution_receipt_ref": None,
        "content_ingest_receipt_ref": None,
        "evidence_refs": evidence_refs,
        "warnings": _append_unique([], content["missing_required_evidence"]),
        "blocking_findings": ["content evidence or redaction requires owner review"] if content["human_review_required"] else [],
        "human_review_required": human_required,
        "created_at": _outcome_timestamp(envelope, plan),
        "producer": "planning-with-files",
        "producer_version": CURRENT_VERSION,
        "dedupe_key": dedupe_key,
        "evolution_status": "READY_FOR_BRIDGE" if evolution_required and not evolution["human_review_required"] else "HUMAN_REVIEW_REQUIRED" if evolution["human_review_required"] else "NO_EVOLUTION",
        "content_status": "READY_FOR_INGEST" if content_required and not content["human_review_required"] else "HUMAN_REVIEW_REQUIRED" if content["human_review_required"] else "NOT_REQUIRED",
    }
    if provenance is not None:
        decision.update(provenance)
        decision["routing_input_digest"] = dedupe_key
        decision["decision_digest"] = contracts.contract_digest(
            decision,
            exclude_fields=("created_at", "decision_digest", "evolution_receipt_ref", "content_ingest_receipt_ref"),
        )
    contracts.validate_routing_decision(decision)
    signal = None
    if evolution_required:
        signal = {
            "signal_id": f"signal-{_safe_component(envelope['task_id'], 'task_id')}-{dedupe_key[:12]}",
            "dedupe_key": dedupe_key,
            "task_id": envelope["task_id"],
            "plan_id": plan["plan_id"],
            "phase_id": metadata.get("current_phase") or "UNKNOWN",
            "checkpoint_id": checkpoint_id,
            "signal_types": evolution["signal_types"],
            "failure_counts": evolution["failure_counts"],
            "correction_counts": evolution["correction_counts"],
            "manual_action_counts": evolution["manual_action_counts"],
            "reusable_rule_candidates": evolution["reusable_rule_candidates"],
            "skill_gap_candidates": evolution["skill_gap_candidates"],
            "contract_gap_candidates": evolution["contract_gap_candidates"],
            "automation_candidates": evolution["automation_candidates"],
            "excluded_private_facts": evolution["excluded_private_facts"],
            "evidence_refs": evidence_refs,
            "e0_result": evolution["e0_result"],
            "handoff_status": "BLOCKED" if evolution["human_review_required"] else "READY_FOR_BRIDGE",
            "created_at": _outcome_timestamp(envelope, plan),
            "producer": "planning-with-files",
            "producer_version": CURRENT_VERSION,
        }
        contracts.validate_evolution_signal(signal)
    handoff = None
    if content_required and not content["human_review_required"]:
        handoff = {
            "handoff_id": f"handoff-{_safe_component(envelope['task_id'], 'task_id')}-{dedupe_key[:12]}",
            "dedupe_key": dedupe_key,
            "task_id": envelope["task_id"],
            "plan_id": plan["plan_id"],
            "project_name": content["project_name"],
            "source_type": content["source_type"],
            "content_title": content["content_title"],
            "content_summary": content["content_summary"],
            "core_value": content["core_value"],
            "reusable_knowledge": content["reusable_knowledge"],
            "project_specific_facts": content["project_specific_facts"],
            "target_audience": content["target_audience"],
            "recommended_platforms": content["recommended_platforms"],
            "content_angles": content["content_angles"],
            "evidence_refs": evidence_refs,
            "image_refs": content["image_refs"],
            "source_paths": content["source_paths"],
            "sensitive_content": content["sensitive_content"],
            "redaction_requirements": content["redaction_requirements"],
            "handoff_status": "PENDING_INGEST",
            "created_at": _outcome_timestamp(envelope, plan),
            "producer": "planning-with-files",
            "producer_version": CURRENT_VERSION,
        }
        contracts.validate_knowledge_handoff_package(handoff)
    return decision, signal, handoff

def _existing_outcome_or_conflict(path: Path, value: dict[str, Any], kind: str, *, exclude: Iterable[str] = ()) -> dict[str, Any] | None:
    if not path.exists():
        return None
    existing = _read_json(path, code=f"INVALID_{kind.upper()}")
    try:
        contracts.validate_contract(kind, existing)
    except workflow.ContractError as exc:
        raise PlanningError(f"INVALID_{kind.upper()}", str(exc), result="CONFLICT") from exc
    if _outcome_digest(existing, exclude=exclude) == _outcome_digest(value, exclude=exclude):
        return existing
    raise PlanningError(f"{kind.upper()}_CONFLICT", f"existing {kind} has different content", result="CONFLICT")

@edition_operation
def evaluate_outcome_routing(
    instance_root: str | Path,
    *,
    preview: bool = False,
    apply: bool = False,
    agent: str = "planning-with-files",
) -> dict[str, Any]:
    """Evaluate local outcome value and persist only local routing projections."""
    try:
        if preview == apply:
            raise PlanningError("INVALID_MODE", "exactly one of preview or apply must be true")
        state_root, instance, envelope, plan, checklist = _load_instance(instance_root)
        decision, signal, handoff = _build_outcome_routing(envelope, plan, checklist, state_root, instance)
        checkpoint_id = decision.get("checkpoint_id")
        decision_path = _routing_decision_location(instance, checkpoint_id)
        existing_decision = None
        existing_path = None
        candidates = [_outcome_target(instance, _routing_decision_relative(checkpoint_id))]
        if checkpoint_id is None:
            candidates.append(_outcome_target(instance, ROUTING_DECISION_RELATIVE))
        for candidate in candidates:
            if not candidate.is_file():
                continue
            existing = _read_json(candidate, code="INVALID_ROUTING_DECISION")
            try:
                contracts.validate_routing_decision(existing)
            except workflow.ContractError as exc:
                raise PlanningError("INVALID_ROUTING_DECISION", str(exc)) from exc
            if checkpoint_id is not None and candidate == _outcome_target(instance, ROUTING_DECISION_RELATIVE) and existing.get("checkpoint_id") != checkpoint_id:
                continue
            try:
                existing_decision = _existing_outcome_or_conflict(
                    candidate, decision, "routing_decision", exclude=("created_at", "evolution_receipt_ref", "content_ingest_receipt_ref")
                )
            except PlanningError as exc:
                if exc.code == "ROUTING_DECISION_CONFLICT":
                    raise PlanningError("OUTCOME_ROUTING_CONFLICT", exc.message, result="CONFLICT") from exc
                raise
            existing_path = candidate
            break
        if existing_decision is not None:
            return {
                "result": "EXISTING_OUTCOME_ROUTING" if checkpoint_id is not None else "EXISTING_ROUTING_DECISION",
                "decision_id": existing_decision["decision_id"],
                "decision": existing_decision["decision"],
                "decision_path": (existing_path or decision_path).as_posix(),
                "routing_decision": existing_decision,
                "no_op": True,
                "idempotent": True,
            }
        decision_relative = decision_path.relative_to(instance).as_posix()
        files: dict[str, str] = {decision_relative: contracts.stable_json(decision)}
        planned = [decision_relative]
        if signal is not None:
            signal_relative = str(decision.get("evolution_signal_ref") or EVOLUTION_SIGNAL_RELATIVE)
            signal_path = _outcome_target(instance, signal_relative)
            _existing_outcome_or_conflict(signal_path, signal, "evolution_signal", exclude=("created_at",))
            files[signal_relative] = contracts.stable_json(signal)
            planned.append(signal_relative)
        if handoff is not None:
            handoff_relative = str(decision.get("knowledge_handoff_ref") or KNOWLEDGE_HANDOFF_RELATIVE)
            handoff_path = _outcome_target(instance, handoff_relative)
            _existing_outcome_or_conflict(handoff_path, handoff, "knowledge_handoff_package", exclude=("created_at",))
            persisted_handoff = adapt_output_once(
                handoff,
                payload_kind="KnowledgeHandoff",
                callsite_id="handoff-write",
            )
            files[handoff_relative] = contracts.stable_json(persisted_handoff)
            planned.append(handoff_relative)
        updated_checklist, state_update = _outcome_state_update(
            checklist, decision, outcome_ref=decision_relative
        )
        files[workflow.CHECKLIST_NAME] = updated_checklist
        planned.append(workflow.CHECKLIST_NAME)
        result: dict[str, Any] = {
            "result": "PREVIEW" if preview else "RECORDED_OUTCOME_ROUTING" if checkpoint_id is not None else "CREATED_OUTCOME_ROUTING",
            "decision_id": decision["decision_id"],
            "decision": decision["decision"],
            "routing_decision": decision,
            "evolution_signal": signal,
            "knowledge_handoff": handoff,
            "decision_path": decision_path.as_posix(),
            "planned_files": sorted(planned),
            "created_files": [],
            "state_update": state_update,
            "state_root": str(state_root),
            "instance_path": str(instance),
            "external_calls": [],
            "no_op": False,
        }
        if preview:
            return result
        expected = {relative: workflow.sha256_digest("") for relative in files if relative != workflow.CHECKLIST_NAME}
        expected[workflow.CHECKLIST_NAME] = workflow.file_digest(instance / workflow.CHECKLIST_NAME)
        _transaction_write(
            instance,
            state_root,
            files,
            expected_digests=expected,
            lock_target=workflow.CHECKLIST_NAME,
            lock_name="outcome-routing",
            agent=agent,
            transaction_tag="f1-06-routing",
        )
        stored_decision = _load_routing_decision(instance, checkpoint_id)
        if _outcome_digest(stored_decision, exclude=("created_at", "evolution_receipt_ref", "content_ingest_receipt_ref")) != _outcome_digest(decision, exclude=("created_at", "evolution_receipt_ref", "content_ingest_receipt_ref")):
            raise PlanningError("FAILED", "published routing decision changed unexpectedly")
        workflow.validate_checklist_text((instance / workflow.CHECKLIST_NAME).read_text(encoding="utf-8"))
        result["created_files"] = sorted(files)
        return result
    except PlanningError as exc:
        return _result_error(exc)
    except (OSError, ValueError, workflow.ContractError) as exc:
        return _result_error(PlanningError("INTERNAL_ERROR", str(exc)))

def _evolution_analysis(
    metadata: dict[str, Any],
    evolution_policy: dict[str, Any],
    receipts: list[dict[str, Any]],
    cleanliness_receipts: list[dict[str, Any]],
) -> dict[str, Any]:
    failures, corrections, manual_actions = _outcome_counts(
        metadata, receipts, cleanliness_receipts, evolution_policy
    )
    signal_types: list[str] = []
    if any(count >= 2 for count in failures.values()):
        signal_types.append("REPEAT_FAILURE")
    if any(count >= 2 for count in corrections.values()):
        signal_types.append("REPEAT_CORRECTION")
    if any(count >= 3 for count in manual_actions.values()):
        signal_types.append("REPEAT_MANUAL_ACTION")
    reusable = _candidate_values(evolution_policy, "reusable_rule_candidates")
    skill_gaps = _candidate_values(evolution_policy, "skill_gap_candidates")
    contract_gaps = _candidate_values(evolution_policy, "contract_gap_candidates")
    automation = _candidate_values(evolution_policy, "automation_candidates")
    if reusable:
        signal_types.append("REUSABLE_RULE")
    if skill_gaps:
        signal_types.append("SKILL_GAP")
    if contract_gaps:
        signal_types.append("CONTRACT_GAP")
    if automation:
        signal_types.append("AUTOMATION_OPPORTUNITY")
    private_facts = _candidate_values(evolution_policy, "excluded_private_facts")
    if evolution_policy.get("cross_task_value") is False:
        signal_types = []
        reusable = []
        skill_gaps = []
        contract_gaps = []
        automation = []
    human_required = bool(
        evolution_policy.get("human_review_required")
        or evolution_policy.get("private_facts_not_separable")
        or evolution_policy.get("sensitive_content")
    )
    e0_result = "HUMAN_REVIEW_REQUIRED" if human_required else ("EVOLUTION_PROPOSAL" if signal_types else "NO_EVOLUTION")
    return {
        "failure_counts": failures,
        "correction_counts": corrections,
        "manual_action_counts": manual_actions,
        "signal_types": sorted(set(signal_types)),
        "reusable_rule_candidates": reusable,
        "skill_gap_candidates": skill_gaps,
        "contract_gap_candidates": contract_gaps,
        "automation_candidates": automation,
        "excluded_private_facts": private_facts,
        "human_review_required": human_required,
        "e0_result": e0_result,
        "has_value": bool(signal_types),
    }

def _content_judgment_required(policy: dict[str, Any]) -> bool:
    content_fields = (
        "content_title",
        "content_summary",
        "core_value",
        "reusable_knowledge",
        "project_specific_facts",
        "target_audience",
        "recommended_platforms",
        "content_angles",
        "image_refs",
    )
    return bool(
        policy.get("level", "NONE") != "NONE"
        or policy.get("potential_value")
        or policy.get("ingest_required")
        or any(policy.get(field) for field in content_fields)
    )

def _content_task_markers(
    envelope: dict[str, Any],
    plan: dict[str, Any],
    policy: dict[str, Any],
) -> tuple[bool, bool]:
    content_fields = (
        "content_title",
        "content_summary",
        "core_value",
        "reusable_knowledge",
        "project_specific_facts",
        "target_audience",
        "recommended_platforms",
        "content_angles",
        "image_refs",
    )
    explicit = bool(policy.get("ingest_required")) or str(policy.get("level", "NONE")).upper() == "FULL" or any(
        policy.get(field) for field in content_fields
    )
    task_text = contracts.stable_json(
        {
            "title": envelope.get("title"),
            "objective": envelope.get("objective"),
            "scope": plan.get("scope"),
            "non_goals": plan.get("non_goals"),
        }
    )
    marked = bool(
        re.search(
            r"内容|文章|视频|素材|发布|脚本|知识提炼|\b(?:content|article|video|publish|script)\b",
            task_text,
            re.IGNORECASE,
        )
    )
    return explicit, marked

def _content_analysis(
    envelope: dict[str, Any],
    plan: dict[str, Any],
    evidence_refs: list[str],
) -> dict[str, Any]:
    policy = _merge_dicts(envelope.get("knowledge_policy"), plan.get("knowledge_policy"), envelope.get("content_policy"), plan.get("content_policy"))
    level = policy.get("level", "NONE")
    potential_value = str(policy.get("potential_value", "")).strip()
    required_evidence = _string_values(policy.get("required_evidence"))
    evidence_text = " ".join(evidence_refs)
    missing_required = [item for item in required_evidence if item not in evidence_text]
    sensitive = policy.get("sensitive_content", False)
    if isinstance(sensitive, str):
        sensitive = bool(sensitive.strip())
    sensitive_payload = {
        field: policy.get(field)
        for field in (
            "potential_value",
            "content_title",
            "content_summary",
            "core_value",
            "reusable_knowledge",
            "project_specific_facts",
            "target_audience",
            "content_angles",
        )
    }
    sensitive = bool(sensitive or SENSITIVE_MARKERS.search(contracts.stable_json(sensitive_payload)))
    target_audience = _string_values(policy.get("target_audience")) or ["AI workflow practitioners"]
    title = str(policy.get("content_title") or envelope.get("title", "")).strip()
    summary = str(policy.get("content_summary") or envelope.get("objective", "")).strip()
    core_value = str(policy.get("core_value") or potential_value or envelope.get("business_value", "")).strip()
    reusable = _string_values(policy.get("reusable_knowledge"))
    project_facts = _string_values(policy.get("project_specific_facts"))
    angles = _string_values(policy.get("content_angles"))
    platforms = _string_values(policy.get("recommended_platforms"))
    platforms = [item for item in platforms if item in contracts.PLATFORM_CANDIDATES]
    if not platforms:
        platforms = ["INTERNAL_KNOWLEDGE"]
    redaction = _string_values(policy.get("redaction_requirements"))
    explicit_content_request, content_markers = _content_task_markers(envelope, plan, policy)
    requested_judgment = policy.get("content_judgment")
    if requested_judgment is not None:
        try:
            contracts.validate_content_judgment(requested_judgment)
        except workflow.ContractError as exc:
            raise PlanningError("CONTENT_JUDGMENT_INVALID", str(exc)) from exc
        judgment = copy.deepcopy(requested_judgment)
    elif content_markers and not explicit_content_request:
        raise PlanningError(
            "CONTENT_JUDGMENT_REQUIRED",
            "content-like task must provide an explicit content judgment before routing",
        )
    elif explicit_content_request:
        judgment = {
            "status": "REQUIRED",
            "reason_code": "CONTENT_OUTPUT_REQUIRED",
            "reason": "任务包含内容产出、知识摄取或发布相关契约，必须进入内容结果链。",
            "evidence_ref": evidence_refs[0] if evidence_refs else "task-envelope.json",
            "decided_by": "planning-with-files",
        }
    else:
        judgment = {
            "status": "NOT_APPLICABLE",
            "reason_code": "ENGINEERING_GOVERNANCE_ONLY",
            "reason": "任务范围仅限工程契约、运行时门禁、测试和治理证据，不产生、筛选、发布或提炼内容资产。",
            "evidence_ref": evidence_refs[0] if evidence_refs else "task-envelope.json",
            "decided_by": "planning-with-files",
        }
    if judgment["status"] == "NOT_APPLICABLE" and (explicit_content_request or content_markers):
        raise PlanningError(
            "CONTENT_JUDGMENT_INVALID",
            "content production or content-like task cannot be marked NOT_APPLICABLE",
        )
    content_missing_required = missing_required if judgment["status"] == "REQUIRED" else []
    human_required = bool(
        sensitive
        or policy.get("human_review_required")
        or policy.get("private_facts_not_separable")
        or content_missing_required
    )
    value = judgment["status"] == "REQUIRED" and level != "NONE" and bool(
        potential_value and title and summary and core_value and evidence_refs
    )
    if judgment["status"] == "REQUIRED" and not value:
        raise PlanningError(
            "CONTENT_JUDGMENT_INVALID",
            "content judgment is REQUIRED but the content policy is incomplete",
        )
    return {
        "level": level,
        "potential_value": potential_value,
        "required_evidence": required_evidence,
        "missing_required_evidence": content_missing_required,
        "human_review_required": human_required,
        "has_value": value,
        "sensitive_content": sensitive,
        "project_name": str(envelope.get("project_id", envelope.get("title", ""))),
        "source_type": str(policy.get("source_type", "WORKFLOW_EXPERIENCE")),
        "content_title": title,
        "content_summary": summary,
        "core_value": core_value,
        "reusable_knowledge": reusable,
        "project_specific_facts": project_facts,
        "target_audience": target_audience,
        "recommended_platforms": platforms,
        "content_angles": angles,
        "image_refs": _string_values(policy.get("image_refs")),
        "source_paths": _append_unique([], evidence_refs),
        "redaction_requirements": redaction,
        "content_judgment": judgment,
    }
