#!/usr/bin/env python3
# VERSION source: ../VERSION
"""Deterministic F1-01 PLAN machine-contract validators.

This module is deliberately separate from the v0.8 workflow writer.  It freezes
the contract vocabulary and compatibility rules without adding a runtime CLI or
changing old checklist files.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Iterable, Mapping

import workflow_contracts as workflow


PLAN_CONTRACT_SCHEMA_VERSION = 1
WORKFLOW_SCHEMA_VERSION = workflow.WORKFLOW_SCHEMA_VERSION
PLAN_EXTENSION_KEY = "plan_contracts"

LEGACY_COMPATIBLE = "LEGACY_COMPATIBLE"
CURRENT_CONTRACT = "CURRENT_CONTRACT"

RISK_LEVELS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
TASK_PRIORITIES = {"P0", "P1", "P2", "P3"}
CONDITION_TYPES = {"COMPLETION", "FAILURE", "PAUSE", "USER_GATE"}
CONDITION_STATUSES = {"PENDING", "SATISFIED", "FAILED", "WAIVED", "NOT_APPLICABLE"}
CAPABILITY_COMPATIBILITY = {
    "COMPATIBLE",
    "COMPATIBLE_WITH_WARNINGS",
    "INCOMPATIBLE",
    "UNREGISTERED",
    "UNCONFIRMED",
}
SIDE_EFFECT_LEVELS = {"NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL"}
CHECKPOINT_STATUSES = {"PENDING", "ACTIVE", "PASSED", "FAILED", "PAUSED", "BLOCKED", "CLOSED", "UNKNOWN"}
KNOWLEDGE_LEVELS = {"NONE", "BRIEF", "FULL"}
HANDOFF_STATUSES = {
    "NOT_REQUIRED",
    "PENDING_GENERATION",
    "PENDING_INGEST",
    "INGESTED",
    "FAILED_RETRYABLE",
    "BLOCKED_SECURITY",
}
OUTCOME_DECISIONS = {
    "NO_VALUE",
    "EVOLUTION_ONLY",
    "CONTENT_ONLY",
    "EVOLUTION_AND_CONTENT",
    "HUMAN_REVIEW_REQUIRED",
}
E0_RESULTS = {"NO_EVOLUTION", "EVOLUTION_PROPOSAL", "HUMAN_REVIEW_REQUIRED"}
EVOLUTION_HANDOFF_STATUSES = {
    "NOT_REQUIRED",
    "READY_FOR_BRIDGE",
    "PENDING_PROCESSING",
    "CANDIDATE_CREATED",
    "APPLIED",
    "REJECTED_NO_VALUE",
    "BLOCKED",
    "FAILED_RETRYABLE",
}
EVOLUTION_RESULTS = {
    "NO_EVOLUTION",
    "CANDIDATE_CREATED",
    "DUPLICATE_EXISTING",
    "APPLIED",
    "REJECTED",
    "BLOCKED",
    "FAILED_RETRYABLE",
}
CONTENT_RESULTS = {
    "INGESTED",
    "REJECTED_NO_VALUE",
    "DUPLICATE_EXISTING",
    "BLOCKED_SECURITY",
    "FAILED_RETRYABLE",
    "INCONCLUSIVE",
}
CONTENT_JUDGMENT_STATUSES = {"REQUIRED", "NOT_APPLICABLE"}
CONTENT_JUDGMENT_REASON_CODES = {
    "CONTENT_OUTPUT_REQUIRED",
    "ENGINEERING_GOVERNANCE_ONLY",
}
PLATFORM_CANDIDATES = {
    "XIAOHONGSHU",
    "WECHAT_OFFICIAL_ACCOUNT",
    "BAIJIAHAO",
    "INTERNAL_KNOWLEDGE",
}
GOVERNANCE_STAGES = {"PRE_WRITE", "POST_WRITE", "PRE_CLOSE"}
GOVERNANCE_RESULTS = {"PASS", "PASS_WITH_WARNINGS", "BLOCKED", "INCONCLUSIVE"}
EXECUTION_RESULTS = {"PASS", "PASS_WITH_WARNINGS", "FAILED", "BLOCKED", "INCONCLUSIVE"}
REVISION_TYPES = {"GOVERNANCE_SEQUENCE_REPAIR"}
ROLLBACK_STATUSES = {"NOT_REQUIRED", "PENDING", "IN_PROGRESS", "COMPLETED", "FAILED"}
CONTRACT_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]*$")

CONTRACT_KINDS = (
    "task_envelope",
    "plan_package",
    "execution_packet",
    "condition",
    "capability_ref",
    "checkpoint_ref",
    "knowledge_handoff",
    "governance_request",
    "governance_receipt",
    "execution_receipt",
    "routing_decision",
    "evolution_signal",
    "evolution_receipt",
    "knowledge_handoff_package",
    "content_ingest_receipt",
)

TASK_ENVELOPE_FIELDS = (
    "schema_version", "task_id", "project_id", "title", "objective",
    "business_value", "user_pain", "scope", "non_goals", "success_criteria",
    "risk_level", "priority", "human_gates", "time_constraints",
    "allowed_capabilities", "forbidden_capabilities", "knowledge_policy",
    "source_evidence", "requested_by", "created_at", "producer", "producer_version",
)
PLAN_PACKAGE_FIELDS = (
    "schema_version", "plan_id", "plan_version", "task_id", "project_id",
    "task_profile", "objective", "scope", "non_goals", "assumptions",
    "dependencies", "phases", "capability_refs", "completion_conditions",
    "failure_conditions", "pause_conditions", "human_gates", "rollback_policy",
    "evidence_policy", "knowledge_policy", "governance_policy", "cleanup_policy",
    "status_summary", "created_at", "producer", "producer_version",
)
MIDCOURSE_GATE_FIELDS = (
    "midcourse_gate_phase",
    "midcourse_gate_entry_criteria",
    "midcourse_gate_exit_criteria",
    "midcourse_grill_policy",
    "midcourse_review_ref",
    "midcourse_owner_confirmation_ref",
    "midcourse_gate_result",
    "owner_acceptance_checklist_ref",
)
MIDCOURSE_GATE_RESULTS = {
    "PENDING",
    "NOT_REACHED",
    "PASS",
    "BLOCKED",
    "FAILED",
    "INCONCLUSIVE",
}
MIDCOURSE_INITIAL_RESULTS = {"PENDING", "NOT_REACHED"}
EXECUTION_PACKET_FIELDS = (
    "schema_version", "packet_id", "plan_id", "plan_version", "task_id",
    "phase_id", "work_item_id", "skill_ref", "objective", "allowed_scope",
    "forbidden_scope", "inputs", "expected_outputs", "completion_conditions",
    "failure_conditions", "pause_conditions", "evidence_requirements", "permissions",
    "governance_requirements", "timeout_policy", "receipt_requirements", "created_at",
    "producer", "producer_version",
)
CONDITION_FIELDS = (
    "condition_id", "condition_type", "description", "required", "evidence_required",
    "evaluation_method", "status", "evidence_refs",
)
CAPABILITY_REF_FIELDS = (
    "skill_id", "skill_version", "capability_id", "capability_version", "registry_ref",
    "input_contract_ref", "output_contract_ref", "risk_level", "side_effect_level",
    "supports_preview", "is_idempotent", "compatibility_status",
)
CHECKPOINT_REF_FIELDS = (
    "checkpoint_id", "checkpoint_status", "plan_id", "plan_version", "phase_id",
    "evidence_refs", "resume_entry", "receipt_location", "producer", "producer_version",
    "created_at",
)
KNOWLEDGE_HANDOFF_FIELDS = (
    "handoff_id", "dedupe_key", "handoff_status", "handoff_location", "ingest_receipt_ref",
)
GOVERNANCE_REQUEST_FIELDS = (
    "request_id", "task_id", "plan_id", "plan_version", "phase_id", "governance_stage",
    "allowed_paths", "forbidden_paths", "expected_changes", "protected_assets",
    "known_dirty_paths", "temporary_artifact_policy", "cleanup_policy", "knowledge_policy",
    "evidence_refs", "requested_checks", "requested_at",
)
GOVERNANCE_RECEIPT_FIELDS = (
    "receipt_id", "request_id", "task_id", "plan_id", "phase_id", "governance_stage",
    "result", "cleanliness_status", "scope_match", "blocking_findings",
    "non_blocking_findings", "duplicate_candidates", "unused_asset_candidates",
    "cleanup_actions", "protected_assets_status", "evidence_refs", "checked_at",
    "producer", "producer_version",
)
EXECUTION_RECEIPT_FIELDS = (
    "schema_version", "receipt_id", "packet_id", "plan_id", "plan_version", "task_id",
    "phase_id", "work_item_id", "skill_ref", "result", "summary", "changed_paths",
    "created_assets", "deleted_assets", "test_results", "evidence_refs", "warnings",
    "blocking_findings", "rollback_status", "started_at", "completed_at", "producer",
    "producer_version",
)
ROUTING_DECISION_FIELDS = (
    "decision_id", "task_id", "plan_id", "checkpoint_id", "decision",
    "evolution_required", "content_required", "evolution_signal_ref",
    "knowledge_handoff_ref", "evolution_receipt_ref", "content_ingest_receipt_ref",
    "content_judgment", "evidence_refs", "warnings", "blocking_findings", "human_review_required",
    "created_at", "producer", "producer_version",
)
ROUTING_PROVENANCE_FIELDS = (
    "routing_provenance_version", "phase_id", "work_item_id",
    "canonical_checkpoint_state_root", "checkpoint_lineage_digest",
    "checkpoint_ref_digest", "read_head", "routing_input_digest", "decision_digest",
)
EVOLUTION_SIGNAL_FIELDS = (
    "signal_id", "dedupe_key", "task_id", "plan_id", "phase_id", "checkpoint_id",
    "signal_types", "failure_counts", "correction_counts", "manual_action_counts",
    "reusable_rule_candidates", "skill_gap_candidates", "contract_gap_candidates",
    "automation_candidates", "excluded_private_facts", "evidence_refs", "e0_result",
    "handoff_status", "created_at", "producer", "producer_version",
)
EVOLUTION_RECEIPT_FIELDS = (
    "receipt_id", "signal_id", "dedupe_key", "task_id", "plan_id", "result",
    "e0_result", "proposal_id", "candidate_id", "application_status", "checkpoint_ref",
    "changed_assets", "evidence_refs", "warnings", "blocking_findings", "processed_at",
    "producer", "producer_version",
)
KNOWLEDGE_HANDOFF_PACKAGE_FIELDS = (
    "handoff_id", "dedupe_key", "task_id", "plan_id", "project_name", "source_type",
    "content_title", "content_summary", "core_value", "reusable_knowledge",
    "project_specific_facts", "target_audience", "recommended_platforms", "content_angles",
    "evidence_refs", "image_refs", "source_paths", "sensitive_content",
    "redaction_requirements", "handoff_status", "created_at", "producer", "producer_version",
)
CONTENT_INGEST_RECEIPT_FIELDS = (
    "receipt_id", "handoff_id", "dedupe_key", "task_id", "plan_id", "result",
    "destination_system", "destination_path", "created_assets", "platform_candidates",
    "warnings", "blocking_findings", "evidence_refs", "ingested_at", "producer",
    "producer_version",
)

CONTRACT_FIELD_SETS = {
    "task_envelope": TASK_ENVELOPE_FIELDS,
    "plan_package": PLAN_PACKAGE_FIELDS,
    "execution_packet": EXECUTION_PACKET_FIELDS,
    "condition": CONDITION_FIELDS,
    "capability_ref": CAPABILITY_REF_FIELDS,
    "checkpoint_ref": CHECKPOINT_REF_FIELDS,
    "knowledge_handoff": KNOWLEDGE_HANDOFF_FIELDS,
    "governance_request": GOVERNANCE_REQUEST_FIELDS,
    "governance_receipt": GOVERNANCE_RECEIPT_FIELDS,
    "execution_receipt": EXECUTION_RECEIPT_FIELDS,
    "routing_decision": ROUTING_DECISION_FIELDS,
    "evolution_signal": EVOLUTION_SIGNAL_FIELDS,
    "evolution_receipt": EVOLUTION_RECEIPT_FIELDS,
    "knowledge_handoff_package": KNOWLEDGE_HANDOFF_PACKAGE_FIELDS,
    "content_ingest_receipt": CONTENT_INGEST_RECEIPT_FIELDS,
}

STATE_OWNERSHIP = {
    "objective": "Project Owner",
    "business_value": "Project Owner",
    "risk_level": "Project Owner",
    "human_gates": "Project Owner",
    "plan": "planning-with-files",
    "execution_contract": "planning-with-files",
    "schedule_and_routing": "orchestrator",
    "checkpoint": "phase-checkpoint-loop",
    "business_result": "下游执行 Skill",
    "cleanliness_result": "全域洁癖",
    "capability_registry": "Skill 治理和索引体系",
    "knowledge_body": "external-publishing-system",
    "evolution_candidate": "phase-evolution-bridge",
}

_EMBEDDED_CONTENT_KEYS = {
    "body", "content", "draft", "full_text", "full_history", "full_log",
    "complete_conversation", "complete_project_history", "repository_snapshot",
    "commit_patch", "source_code", "raw_dialogue",
}


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise workflow.ContractError(f"{label} must be an object")
    return value


def _nonempty(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise workflow.ContractError(f"{label} must be a non-empty string")


def _id(value: Any, label: str) -> None:
    if not isinstance(value, str) or not CONTRACT_ID_RE.fullmatch(value):
        raise workflow.ContractError(f"invalid {label}: {value!r}")


def _enum(value: Any, allowed: set[str], label: str) -> None:
    if value not in allowed:
        raise workflow.ContractError(f"invalid {label}: {value!r}")


def _bool(value: Any, label: str) -> None:
    if not isinstance(value, bool):
        raise workflow.ContractError(f"{label} must be boolean")


def _list(value: Any, label: str) -> None:
    if not isinstance(value, list):
        raise workflow.ContractError(f"{label} must be a list")


def _nonempty_list(value: Any, label: str) -> None:
    _list(value, label)
    if not value:
        raise workflow.ContractError(f"{label} must not be empty")


def _string_list(value: Any, label: str) -> None:
    _list(value, label)
    for index, item in enumerate(value):
        _nonempty(item, f"{label}[{index}]")


def _object_or_string(value: Any, label: str) -> None:
    if isinstance(value, str):
        _nonempty(value, label)
    elif isinstance(value, dict):
        if not value:
            raise workflow.ContractError(f"{label} must not be empty")
    else:
        raise workflow.ContractError(f"{label} must be an object or string")


def _list_of_objects(value: Any, label: str, validator: Any | None = None) -> None:
    _list(value, label)
    for index, item in enumerate(value):
        _object(item, f"{label}[{index}]")
        if validator:
            validator(item)


def _timestamp(value: Any, label: str) -> None:
    workflow.validate_rfc3339(value, label)


def _semver(value: Any, label: str) -> None:
    workflow.validate_semver(value, label)


def _schema_version(value: dict[str, Any]) -> None:
    if value.get("schema_version") != PLAN_CONTRACT_SCHEMA_VERSION:
        raise workflow.ContractError("unsupported plan contract schema_version")


def _required(value: dict[str, Any], fields: Iterable[str]) -> None:
    for field in fields:
        if field not in value:
            raise workflow.ContractError(f"contract missing {field}")


def _scope(value: Any, label: str = "scope") -> None:
    item = _object(value, label)
    for key in ("include", "exclude"):
        if key in item:
            _string_list(item[key], f"{label}.{key}")


def _policy(value: Any, label: str) -> None:
    _object(value, label)


def _nonempty_policy(value: Any, label: str) -> None:
    item = _object(value, label)
    if not item:
        raise workflow.ContractError(f"{label} must not be empty")


def _nullable_ref(value: Any, label: str) -> None:
    if value is not None:
        _nonempty(value, label)


def _evidence_refs(value: Any, label: str = "evidence_refs") -> None:
    _list(value, label)
    for index, item in enumerate(value):
        if isinstance(item, str):
            _nonempty(item, f"{label}[{index}]")
        elif isinstance(item, dict):
            if not item:
                raise workflow.ContractError(f"{label}[{index}] must not be empty")
        else:
            raise workflow.ContractError(f"{label}[{index}] must be a string or object")


def _reject_embedded_content(value: Mapping[str, Any], label: str) -> None:
    forbidden = sorted(_EMBEDDED_CONTENT_KEYS.intersection(value.keys()))
    if forbidden:
        raise workflow.ContractError(f"{label} contains embedded content: {', '.join(forbidden)}")


def validate_condition(value: dict[str, Any]) -> None:
    _object(value, "condition")
    _required(value, CONDITION_FIELDS)
    _id(value["condition_id"], "condition_id")
    _enum(value["condition_type"], CONDITION_TYPES, "condition_type")
    _nonempty(value["description"], "description")
    _bool(value["required"], "required")
    _bool(value["evidence_required"], "evidence_required")
    _object_or_string(value["evaluation_method"], "evaluation_method")
    _enum(value["status"], CONDITION_STATUSES, "status")
    _evidence_refs(value["evidence_refs"])
    if "risk_level" in value:
        _enum(value["risk_level"], RISK_LEVELS, "risk_level")
    if value["condition_type"] == "USER_GATE" and value.get("risk_level") in {"HIGH", "CRITICAL"} and value["status"] == "WAIVED":
        raise workflow.ContractError("high-risk USER_GATE cannot be automatically WAIVED")


def _validate_conditions(value: Any, label: str) -> None:
    _list_of_objects(value, label, validate_condition)


def _validate_execution_packet_revision(value: dict[str, Any]) -> None:
    """Validate the optional controlled revision envelope.

    Legacy packets intentionally have no revision fields.  A revision is a
    separate governance packet and must declare why it exists, which trusted
    checkpoint it continues, and which immutable execution evidence it reuses.
    """
    if "revision" not in value:
        return
    revision = value["revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise workflow.ContractError("revision must be a positive integer")
    if revision == 1:
        return
    required = (
        "revision_type",
        "predecessor_checkpoint_id",
        "revision_reason",
        "revision_scope",
        "technical_reexecution",
        "audit_reexecution",
        "revision_evidence_refs",
        "execution_receipt_reuse",
    )
    _required(value, required)
    _enum(value["revision_type"], REVISION_TYPES, "revision_type")
    _id(value["predecessor_checkpoint_id"], "predecessor_checkpoint_id")
    _nonempty(value["revision_reason"], "revision_reason")
    _nonempty(value["revision_scope"], "revision_scope")
    _bool(value["technical_reexecution"], "technical_reexecution")
    _bool(value["audit_reexecution"], "audit_reexecution")
    if value["technical_reexecution"] or value["audit_reexecution"]:
        raise workflow.ContractError("governance revision cannot re-execute technical work or audit")
    _string_list(value["revision_evidence_refs"], "revision_evidence_refs")
    reuse = _object(value["execution_receipt_reuse"], "execution_receipt_reuse")
    _required(
        reuse,
        (
            "enabled",
            "source_packet_id",
            "source_packet_ref",
            "source_packet_digest",
            "source_receipt_id",
            "source_receipt_ref",
            "source_receipt_digest",
        ),
    )
    _bool(reuse["enabled"], "execution_receipt_reuse.enabled")
    if not reuse["enabled"]:
        raise workflow.ContractError("governance revision must enable execution receipt reuse")
    _id(reuse["source_packet_id"], "execution_receipt_reuse.source_packet_id")
    _nonempty(reuse["source_packet_ref"], "execution_receipt_reuse.source_packet_ref")
    _sha256(reuse["source_packet_digest"], "execution_receipt_reuse.source_packet_digest")
    _id(reuse["source_receipt_id"], "execution_receipt_reuse.source_receipt_id")
    _nonempty(reuse["source_receipt_ref"], "execution_receipt_reuse.source_receipt_ref")
    _sha256(reuse["source_receipt_digest"], "execution_receipt_reuse.source_receipt_digest")


def validate_knowledge_policy(value: dict[str, Any]) -> None:
    _object(value, "knowledge_policy")
    if "level" not in value:
        raise workflow.ContractError("knowledge_policy missing level")
    _enum(value["level"], KNOWLEDGE_LEVELS, "knowledge_policy.level")
    for key in ("required_evidence", "required_images", "prohibited_content", "redaction_requirements"):
        if key in value:
            _string_list(value[key], f"knowledge_policy.{key}")
    if "ingest_required" in value:
        _bool(value["ingest_required"], "knowledge_policy.ingest_required")


def validate_capability_ref(value: dict[str, Any]) -> None:
    _object(value, "capability_ref")
    _required(value, CAPABILITY_REF_FIELDS)
    for field in ("skill_id", "capability_id", "registry_ref", "input_contract_ref", "output_contract_ref"):
        _nonempty(value[field], field)
    for field in ("skill_version", "capability_version"):
        _semver(value[field], field)
    _enum(value["risk_level"], RISK_LEVELS, "risk_level")
    _enum(value["side_effect_level"], SIDE_EFFECT_LEVELS, "side_effect_level")
    _bool(value["supports_preview"], "supports_preview")
    _bool(value["is_idempotent"], "is_idempotent")
    _enum(value["compatibility_status"], CAPABILITY_COMPATIBILITY, "compatibility_status")


def validate_checkpoint_ref(value: dict[str, Any]) -> None:
    _object(value, "checkpoint_ref")
    _required(value, CHECKPOINT_REF_FIELDS)
    _reject_embedded_content(value, "checkpoint_ref")
    _id(value["checkpoint_id"], "checkpoint_id")
    _enum(value["checkpoint_status"], CHECKPOINT_STATUSES, "checkpoint_status")
    _id(value["plan_id"], "plan_id")
    _semver(value["plan_version"], "plan_version")
    _id(value["phase_id"], "phase_id")
    _evidence_refs(value["evidence_refs"])
    _nonempty(value["resume_entry"], "resume_entry")
    _nonempty(value["receipt_location"], "receipt_location")
    _nonempty(value["producer"], "producer")
    _semver(value["producer_version"], "producer_version")
    _timestamp(value["created_at"], "created_at")


def validate_knowledge_handoff(value: dict[str, Any]) -> None:
    if "project_name" in value or "content_title" in value:
        validate_knowledge_handoff_package(value)
        return
    _object(value, "knowledge_handoff")
    _required(value, KNOWLEDGE_HANDOFF_FIELDS)
    _reject_embedded_content(value, "knowledge_handoff")
    _id(value["handoff_id"], "handoff_id")
    _nonempty(value["dedupe_key"], "dedupe_key")
    _enum(value["handoff_status"], HANDOFF_STATUSES, "handoff_status")
    _nonempty(value["handoff_location"], "handoff_location")
    if value["ingest_receipt_ref"] is not None:
        _nonempty(value["ingest_receipt_ref"], "ingest_receipt_ref")


def _optional_ref(value: Any, label: str) -> None:
    if value is not None:
        _nonempty(value, label)


def _optional_id(value: Any, label: str) -> None:
    if value is not None:
        _id(value, label)


def _sha256(value: Any, label: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise workflow.ContractError(f"{label} must be a SHA-256 digest")


def _validate_task_plan_refs(value: dict[str, Any], fields: tuple[str, ...]) -> None:
    for field in fields:
        if field not in value:
            continue
        if field in {"task_id", "plan_id", "phase_id", "checkpoint_id"}:
            _optional_id(value[field], field)
        elif field == "producer":
            _nonempty(value[field], field)
        elif field == "producer_version":
            _semver(value[field], field)
        elif field in {"created_at", "processed_at", "ingested_at"}:
            _timestamp(value[field], field)


def validate_content_judgment(value: dict[str, Any]) -> None:
    _object(value, "content_judgment")
    required = ("status", "reason_code", "reason", "evidence_ref", "decided_by")
    _required(value, required)
    _enum(value["status"], CONTENT_JUDGMENT_STATUSES, "content_judgment.status")
    _enum(value["reason_code"], CONTENT_JUDGMENT_REASON_CODES, "content_judgment.reason_code")
    for field in ("reason", "evidence_ref", "decided_by"):
        _nonempty(value[field], f"content_judgment.{field}")
    if value["status"] == "NOT_APPLICABLE" and value["reason_code"] != "ENGINEERING_GOVERNANCE_ONLY":
        raise workflow.ContractError(
            "content_judgment NOT_APPLICABLE requires ENGINEERING_GOVERNANCE_ONLY"
        )
    if value["status"] == "REQUIRED" and value["reason_code"] != "CONTENT_OUTPUT_REQUIRED":
        raise workflow.ContractError(
            "content_judgment REQUIRED requires CONTENT_OUTPUT_REQUIRED"
        )


def validate_routing_decision(value: dict[str, Any]) -> None:
    _object(value, "routing_decision")
    # ``content_judgment`` was added as an optional structured extension.  It
    # is required by the advanced outcome gate whenever content policy makes
    # the judgment relevant, while old v1.0.0 routing records remain readable.
    _required(value, tuple(field for field in ROUTING_DECISION_FIELDS if field != "content_judgment"))
    _reject_embedded_content(value, "routing_decision")
    _id(value["decision_id"], "decision_id")
    _validate_task_plan_refs(value, ROUTING_DECISION_FIELDS)
    _enum(value["decision"], OUTCOME_DECISIONS, "decision")
    _bool(value["evolution_required"], "evolution_required")
    _bool(value["content_required"], "content_required")
    content_judgment = value.get("content_judgment")
    if content_judgment is not None:
        validate_content_judgment(content_judgment)
        if content_judgment["status"] == "NOT_APPLICABLE" and value["content_required"]:
            raise workflow.ContractError(
                "content_judgment NOT_APPLICABLE cannot be used when content_required is true"
            )
        if content_judgment["status"] == "REQUIRED" and not value["content_required"]:
            raise workflow.ContractError(
                "content_judgment REQUIRED requires content_required to be true"
            )
        if content_judgment["evidence_ref"] not in value["evidence_refs"]:
            raise workflow.ContractError(
                "content_judgment.evidence_ref must be listed in routing_decision.evidence_refs"
            )
    for field in ("evolution_signal_ref", "knowledge_handoff_ref", "evolution_receipt_ref", "content_ingest_receipt_ref"):
        _optional_ref(value[field], field)
    _evidence_refs(value["evidence_refs"])
    _string_list(value["warnings"], "warnings")
    _string_list(value["blocking_findings"], "blocking_findings")
    _bool(value["human_review_required"], "human_review_required")
    if "routing_provenance_version" in value:
        _required(value, ROUTING_PROVENANCE_FIELDS)
        _semver(value["routing_provenance_version"], "routing_provenance_version")
        _id(value["phase_id"], "phase_id")
        _id(value["work_item_id"], "work_item_id")
        _nonempty(value["canonical_checkpoint_state_root"], "canonical_checkpoint_state_root")
        _sha256(value["checkpoint_lineage_digest"], "checkpoint_lineage_digest")
        _sha256(value["checkpoint_ref_digest"], "checkpoint_ref_digest")
        _object(value["read_head"], "read_head")
        for field in ("decision", "publication_status", "verification_status"):
            _nonempty(value["read_head"].get(field), f"read_head.{field}")
        _sha256(value["read_head"].get("head_digest"), "read_head.head_digest")
        _sha256(value["routing_input_digest"], "routing_input_digest")
        _sha256(value["decision_digest"], "decision_digest")


def _validate_count_map(value: Any, label: str) -> None:
    item = _object(value, label)
    for key, count in item.items():
        _nonempty(str(key), f"{label} key")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise workflow.ContractError(f"{label}.{key} must be a non-negative integer")


def validate_evolution_signal(value: dict[str, Any]) -> None:
    _object(value, "evolution_signal")
    _required(value, EVOLUTION_SIGNAL_FIELDS)
    _reject_embedded_content(value, "evolution_signal")
    _id(value["signal_id"], "signal_id")
    _nonempty(value["dedupe_key"], "dedupe_key")
    _validate_task_plan_refs(value, EVOLUTION_SIGNAL_FIELDS)
    _string_list(value["signal_types"], "signal_types")
    for field in ("failure_counts", "correction_counts", "manual_action_counts"):
        _validate_count_map(value[field], field)
    for field in ("reusable_rule_candidates", "skill_gap_candidates", "contract_gap_candidates", "automation_candidates", "excluded_private_facts"):
        _string_list(value[field], field)
    _evidence_refs(value["evidence_refs"])
    _enum(value["e0_result"], E0_RESULTS, "e0_result")
    _enum(value["handoff_status"], EVOLUTION_HANDOFF_STATUSES, "handoff_status")


def validate_evolution_receipt(value: dict[str, Any]) -> None:
    _object(value, "evolution_receipt")
    _required(value, EVOLUTION_RECEIPT_FIELDS)
    _reject_embedded_content(value, "evolution_receipt")
    _id(value["receipt_id"], "receipt_id")
    _id(value["signal_id"], "signal_id")
    _nonempty(value["dedupe_key"], "dedupe_key")
    _validate_task_plan_refs(value, EVOLUTION_RECEIPT_FIELDS)
    _enum(value["result"], EVOLUTION_RESULTS, "result")
    _enum(value["e0_result"], E0_RESULTS, "e0_result")
    _optional_id(value["proposal_id"], "proposal_id")
    _optional_id(value["candidate_id"], "candidate_id")
    _nonempty(value["application_status"], "application_status")
    checkpoint = value["checkpoint_ref"]
    if checkpoint is not None:
        if isinstance(checkpoint, dict):
            validate_checkpoint_ref(checkpoint)
        else:
            _nonempty(checkpoint, "checkpoint_ref")
    for field in ("changed_assets", "evidence_refs", "warnings", "blocking_findings"):
        _evidence_refs(value[field]) if field == "evidence_refs" else _string_list(value[field], field)


def validate_knowledge_handoff_package(value: dict[str, Any]) -> None:
    _object(value, "knowledge_handoff_package")
    _required(value, KNOWLEDGE_HANDOFF_PACKAGE_FIELDS)
    _reject_embedded_content(value, "knowledge_handoff_package")
    _id(value["handoff_id"], "handoff_id")
    _nonempty(value["dedupe_key"], "dedupe_key")
    _validate_task_plan_refs(value, KNOWLEDGE_HANDOFF_PACKAGE_FIELDS)
    _nonempty(value["project_name"], "project_name")
    _nonempty(value["source_type"], "source_type")
    for field in ("content_title", "content_summary", "core_value"):
        _nonempty(value[field], field)
    for field in ("reusable_knowledge", "project_specific_facts", "target_audience", "content_angles", "image_refs", "source_paths", "redaction_requirements"):
        _string_list(value[field], field)
    _string_list(value["recommended_platforms"], "recommended_platforms")
    for item in value["recommended_platforms"]:
        _enum(item, PLATFORM_CANDIDATES, "recommended_platforms")
    _evidence_refs(value["evidence_refs"])
    if not isinstance(value["sensitive_content"], (bool, list, dict)):
        raise workflow.ContractError("sensitive_content must be boolean, list, or object")
    _enum(value["handoff_status"], HANDOFF_STATUSES, "handoff_status")


def validate_content_ingest_receipt(value: dict[str, Any]) -> None:
    _object(value, "content_ingest_receipt")
    _required(value, CONTENT_INGEST_RECEIPT_FIELDS)
    _reject_embedded_content(value, "content_ingest_receipt")
    _id(value["receipt_id"], "receipt_id")
    _id(value["handoff_id"], "handoff_id")
    _nonempty(value["dedupe_key"], "dedupe_key")
    _validate_task_plan_refs(value, CONTENT_INGEST_RECEIPT_FIELDS)
    _enum(value["result"], CONTENT_RESULTS, "result")
    _nonempty(value["destination_system"], "destination_system")
    _nonempty(value["destination_path"], "destination_path")
    for field in ("created_assets", "platform_candidates", "warnings", "blocking_findings"):
        _string_list(value[field], field)
    for item in value["platform_candidates"]:
        _enum(item, PLATFORM_CANDIDATES, "platform_candidates")
    _evidence_refs(value["evidence_refs"])


def _validate_task_reference_fields(value: dict[str, Any]) -> None:
    _id(value["task_id"], "task_id")
    _id(value["project_id"], "project_id")
    _nonempty(value["producer"], "producer")
    _semver(value["producer_version"], "producer_version")
    _timestamp(value["created_at"], "created_at")


def _validate_midcourse_fields(
    value: dict[str, Any],
    label: str,
    *,
    phase_ids: set[str] | None = None,
) -> None:
    present = {field for field in MIDCOURSE_GATE_FIELDS if field in value}
    if not present:
        return
    if present != set(MIDCOURSE_GATE_FIELDS):
        missing = sorted(set(MIDCOURSE_GATE_FIELDS) - present)
        raise workflow.ContractError(f"{label} midcourse gate fields must be all present; missing: {', '.join(missing)}")

    _id(value["midcourse_gate_phase"], f"{label}.midcourse_gate_phase")
    if phase_ids is not None and value["midcourse_gate_phase"] not in phase_ids:
        raise workflow.ContractError(
            f"{label}.midcourse_gate_phase does not reference a declared phase: {value['midcourse_gate_phase']}"
        )
    _nonempty_list(value["midcourse_gate_entry_criteria"], f"{label}.midcourse_gate_entry_criteria")
    _validate_conditions(value["midcourse_gate_entry_criteria"], f"{label}.midcourse_gate_entry_criteria")
    _nonempty_list(value["midcourse_gate_exit_criteria"], f"{label}.midcourse_gate_exit_criteria")
    _validate_conditions(value["midcourse_gate_exit_criteria"], f"{label}.midcourse_gate_exit_criteria")
    _nonempty_policy(value["midcourse_grill_policy"], f"{label}.midcourse_grill_policy")
    _nullable_ref(value["midcourse_review_ref"], f"{label}.midcourse_review_ref")
    _nullable_ref(value["midcourse_owner_confirmation_ref"], f"{label}.midcourse_owner_confirmation_ref")
    _enum(value["midcourse_gate_result"], MIDCOURSE_GATE_RESULTS, f"{label}.midcourse_gate_result")
    _nonempty(value["owner_acceptance_checklist_ref"], f"{label}.owner_acceptance_checklist_ref")

    result = value["midcourse_gate_result"]
    if result in MIDCOURSE_INITIAL_RESULTS and (
        value["midcourse_review_ref"] is not None
        or value["midcourse_owner_confirmation_ref"] is not None
    ):
        raise workflow.ContractError(
            f"{label} initial midcourse result {result} cannot claim review or owner confirmation"
        )
    if result == "PASS" and (
        value["midcourse_review_ref"] is None
        or value["midcourse_owner_confirmation_ref"] is None
    ):
        raise workflow.ContractError(f"{label} PASS requires review and owner confirmation references")


def validate_task_envelope(value: dict[str, Any]) -> None:
    _object(value, "TaskEnvelope")
    _required(value, TASK_ENVELOPE_FIELDS)
    _schema_version(value)
    _validate_task_reference_fields(value)
    _nonempty(value["title"], "title")
    _nonempty(value["objective"], "objective")
    _nonempty(value["business_value"], "business_value")
    _nonempty(value["user_pain"], "user_pain")
    _scope(value["scope"])
    _string_list(value["non_goals"], "non_goals")
    _string_list(value["success_criteria"], "success_criteria")
    _enum(value["risk_level"], RISK_LEVELS, "risk_level")
    _enum(value["priority"], TASK_PRIORITIES, "priority")
    _validate_conditions(value["human_gates"], "human_gates")
    for gate in value["human_gates"]:
        if gate["condition_type"] != "USER_GATE":
            raise workflow.ContractError("TaskEnvelope human_gates must contain USER_GATE conditions")
    _policy(value["time_constraints"], "time_constraints")
    _string_list(value["allowed_capabilities"], "allowed_capabilities")
    _string_list(value["forbidden_capabilities"], "forbidden_capabilities")
    validate_knowledge_policy(value["knowledge_policy"])
    _evidence_refs(value["source_evidence"], "source_evidence")
    _nonempty(value["requested_by"], "requested_by")
    _validate_midcourse_fields(value, "TaskEnvelope")


def _phase(value: Any, label: str) -> None:
    item = _object(value, label)
    if "phase_id" not in item:
        raise workflow.ContractError(f"{label} missing phase_id")
    _id(item["phase_id"], f"{label}.phase_id")


def validate_plan_package(value: dict[str, Any]) -> None:
    _object(value, "PlanPackage")
    _required(value, PLAN_PACKAGE_FIELDS)
    _schema_version(value)
    _id(value["plan_id"], "plan_id")
    _semver(value["plan_version"], "plan_version")
    _validate_task_reference_fields(value)
    _nonempty(value["task_profile"], "task_profile")
    _nonempty(value["objective"], "objective")
    _scope(value["scope"])
    _string_list(value["non_goals"], "non_goals")
    _string_list(value["assumptions"], "assumptions")
    _string_list(value["dependencies"], "dependencies")
    _list(value["phases"], "phases")
    for index, item in enumerate(value["phases"]):
        _phase(item, f"phases[{index}]")
    phase_ids = {str(item["phase_id"]) for item in value["phases"]}
    _list_of_objects(value["capability_refs"], "capability_refs", validate_capability_ref)
    _validate_conditions(value["completion_conditions"], "completion_conditions")
    _validate_conditions(value["failure_conditions"], "failure_conditions")
    _validate_conditions(value["pause_conditions"], "pause_conditions")
    _validate_conditions(value["human_gates"], "human_gates")
    for gate in value["human_gates"]:
        if gate["condition_type"] != "USER_GATE":
            raise workflow.ContractError("PlanPackage human_gates must contain USER_GATE conditions")
    for field in ("rollback_policy", "evidence_policy", "governance_policy", "cleanup_policy", "status_summary"):
        _policy(value[field], field)
    validate_knowledge_policy(value["knowledge_policy"])
    _validate_midcourse_fields(value, "PlanPackage", phase_ids=phase_ids)


def _ref_or_object(value: Any, label: str) -> None:
    if isinstance(value, str):
        _nonempty(value, label)
    else:
        _object(value, label)


def validate_execution_packet(value: dict[str, Any]) -> None:
    _object(value, "ExecutionPacket")
    _required(value, EXECUTION_PACKET_FIELDS)
    _schema_version(value)
    for field in ("packet_id", "plan_id", "task_id", "phase_id", "work_item_id"):
        _id(value[field], field)
    _semver(value["plan_version"], "plan_version")
    _ref_or_object(value["skill_ref"], "skill_ref")
    _nonempty(value["objective"], "objective")
    _scope(value["allowed_scope"], "allowed_scope")
    _scope(value["forbidden_scope"], "forbidden_scope")
    for field in ("inputs", "expected_outputs", "evidence_requirements", "permissions", "governance_requirements", "timeout_policy", "receipt_requirements"):
        _policy(value[field], field)
    _validate_conditions(value["completion_conditions"], "completion_conditions")
    _validate_conditions(value["failure_conditions"], "failure_conditions")
    _validate_conditions(value["pause_conditions"], "pause_conditions")
    _validate_execution_packet_revision(value)
    _nonempty(value["producer"], "producer")
    _semver(value["producer_version"], "producer_version")
    _timestamp(value["created_at"], "created_at")


def capability_invocation_mode(value: dict[str, Any]) -> str:
    validate_capability_ref(value)
    return "AUTO_ALLOWED" if value["compatibility_status"] == "COMPATIBLE" else "MANUAL_SELECTION_REQUIRED"


def validate_governance_request(value: dict[str, Any]) -> None:
    _object(value, "governance_request")
    _required(value, GOVERNANCE_REQUEST_FIELDS)
    _reject_embedded_content(value, "governance_request")
    for field in ("request_id", "task_id", "plan_id", "phase_id"):
        _id(value[field], field)
    _semver(value["plan_version"], "plan_version")
    _enum(value["governance_stage"], GOVERNANCE_STAGES, "governance_stage")
    for field in ("allowed_paths", "forbidden_paths", "expected_changes", "protected_assets", "known_dirty_paths", "evidence_refs", "requested_checks"):
        _string_list(value[field], field)
    for field in ("temporary_artifact_policy", "cleanup_policy", "knowledge_policy"):
        _policy(value[field], field)
    _timestamp(value["requested_at"], "requested_at")


def validate_governance_receipt(value: dict[str, Any]) -> None:
    _object(value, "governance_receipt")
    _required(value, GOVERNANCE_RECEIPT_FIELDS)
    _reject_embedded_content(value, "governance_receipt")
    for field in ("receipt_id", "request_id", "task_id", "plan_id", "phase_id"):
        _id(value[field], field)
    _enum(value["governance_stage"], GOVERNANCE_STAGES, "governance_stage")
    _enum(value["result"], GOVERNANCE_RESULTS, "result")
    _nonempty(value["cleanliness_status"], "cleanliness_status")
    if not isinstance(value["scope_match"], (bool, str)):
        raise workflow.ContractError("scope_match must be boolean or string")
    for field in ("blocking_findings", "non_blocking_findings", "duplicate_candidates", "unused_asset_candidates", "cleanup_actions", "evidence_refs"):
        _string_list(value[field], field)
    _policy(value["protected_assets_status"], "protected_assets_status")
    _timestamp(value["checked_at"], "checked_at")
    _nonempty(value["producer"], "producer")
    _semver(value["producer_version"], "producer_version")


def governance_decision(value: dict[str, Any]) -> dict[str, Any]:
    validate_governance_receipt(value)
    result = value["result"]
    if result == "PASS":
        return {"can_progress": True, "requires_human_gate": False, "reason": "governance passed"}
    if result == "PASS_WITH_WARNINGS":
        allowed = bool(value["evidence_refs"]) and not value["blocking_findings"]
        return {"can_progress": allowed, "requires_human_gate": not allowed, "reason": "warnings retained with evidence" if allowed else "warnings lack evidence or contain blocking findings"}
    if result == "INCONCLUSIVE":
        return {"can_progress": False, "requires_human_gate": True, "reason": "inconclusive governance result"}
    return {"can_progress": False, "requires_human_gate": True, "reason": "governance is blocked"}


def validate_execution_receipt(value: dict[str, Any]) -> None:
    _object(value, "execution_receipt")
    _required(value, EXECUTION_RECEIPT_FIELDS)
    _schema_version(value)
    for field in ("receipt_id", "packet_id", "plan_id", "task_id", "phase_id", "work_item_id"):
        _id(value[field], field)
    _semver(value["plan_version"], "plan_version")
    _ref_or_object(value["skill_ref"], "skill_ref")
    _enum(value["result"], EXECUTION_RESULTS, "result")
    _nonempty(value["summary"], "summary")
    for field in ("changed_paths", "created_assets", "deleted_assets", "evidence_refs", "warnings", "blocking_findings"):
        _string_list(value[field], field)
    _policy(value["test_results"], "test_results")
    _enum(value["rollback_status"], ROLLBACK_STATUSES, "rollback_status")
    _timestamp(value["started_at"], "started_at")
    _timestamp(value["completed_at"], "completed_at")
    _nonempty(value["producer"], "producer")
    _semver(value["producer_version"], "producer_version")


_VALIDATORS = {
    "task_envelope": validate_task_envelope,
    "plan_package": validate_plan_package,
    "execution_packet": validate_execution_packet,
    "condition": validate_condition,
    "capability_ref": validate_capability_ref,
    "checkpoint_ref": validate_checkpoint_ref,
    "knowledge_handoff": validate_knowledge_handoff,
    "governance_request": validate_governance_request,
    "governance_receipt": validate_governance_receipt,
    "execution_receipt": validate_execution_receipt,
    "routing_decision": validate_routing_decision,
    "evolution_signal": validate_evolution_signal,
    "evolution_receipt": validate_evolution_receipt,
    "knowledge_handoff_package": validate_knowledge_handoff_package,
    "content_ingest_receipt": validate_content_ingest_receipt,
}


def validate_contract(kind: str, value: dict[str, Any]) -> None:
    if kind not in _VALIDATORS:
        raise workflow.ContractError(f"unknown plan contract kind: {kind}")
    _VALIDATORS[kind](value)


def contract_fields(kind: str) -> tuple[str, ...]:
    try:
        return CONTRACT_FIELD_SETS[kind]
    except KeyError as exc:
        raise workflow.ContractError(f"unknown plan contract kind: {kind}") from exc


def contract_field_count(kind: str) -> int:
    return len(contract_fields(kind))


def stable_json(value: Any) -> str:
    return workflow.canonical_json(value)


def contract_digest(value: dict[str, Any], exclude_fields: Iterable[str] = ()) -> str:
    payload = copy.deepcopy(value)
    for field in exclude_fields:
        payload.pop(field, None)
    return workflow.sha256_digest(stable_json(payload))


def merge_preserving_unknown(original: Any, updates: Any) -> Any:
    """Recursively merge known updates while retaining unknown nested fields."""
    if isinstance(original, dict) and isinstance(updates, dict):
        result = copy.deepcopy(original)
        for key, value in updates.items():
            result[key] = merge_preserving_unknown(result[key], value) if key in result else copy.deepcopy(value)
        return result
    if isinstance(original, list) and isinstance(updates, list):
        identity_keys = (
            "id", "task_id", "condition_id", "phase_id", "packet_id", "receipt_id",
            "request_id", "handoff_id", "decision_id", "signal_id", "capability_id",
            "skill_id", "work_item_id",
        )
        original_by_identity: dict[tuple[str, Any], dict[str, Any]] = {}
        for item in original:
            if not isinstance(item, dict):
                continue
            identity = next(((key, item[key]) for key in identity_keys if key in item), None)
            if identity is not None:
                original_by_identity[identity] = item
        if original_by_identity and all(isinstance(item, dict) for item in updates):
            merged_items = []
            for item in updates:
                identity = next(((key, item[key]) for key in identity_keys if key in item), None)
                previous = original_by_identity.get(identity) if identity is not None else None
                merged_items.append(merge_preserving_unknown(previous, item) if previous is not None else copy.deepcopy(item))
            return merged_items
    return copy.deepcopy(updates)


def validate_and_merge(kind: str, original: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    merged = merge_preserving_unknown(original, updates)
    validate_contract(kind, merged)
    return merged


def _legacy_defaults() -> dict[str, Any]:
    return {
        "schema_version": PLAN_CONTRACT_SCHEMA_VERSION,
        "task_envelope": None,
        "plan_package": None,
        "execution_packets": [],
        "execution_receipts": [],
    }


def validate_plan_extension(value: dict[str, Any], require_complete: bool = False) -> None:
    _object(value, PLAN_EXTENSION_KEY)
    _required(value, ("schema_version", "task_envelope", "plan_package", "execution_packets", "execution_receipts"))
    _schema_version(value)
    for field in ("task_envelope", "plan_package"):
        if value.get(field) is not None:
            validate_contract(field, value[field])
    for field in ("execution_packets", "execution_receipts"):
        items = value.get(field, [])
        _list(items, field)
        kind = field[:-1]
        for item in items:
            validate_contract(kind, item)
    if require_complete:
        if value.get("task_envelope") is None or value.get("plan_package") is None:
            raise workflow.ContractError("complete plan extension requires task_envelope and plan_package")
        if not value.get("execution_packets"):
            raise workflow.ContractError("complete plan extension requires execution_packets")


def read_workflow_compatibility(value: str | dict[str, Any]) -> dict[str, Any]:
    """Read v0.8 metadata without writing legacy defaults back to disk."""
    metadata = workflow.extract_machine_json(value, "workflow") if isinstance(value, str) else copy.deepcopy(value)
    workflow.validate_workflow_metadata(metadata)
    stored = metadata.get(PLAN_EXTENSION_KEY)
    if stored is None:
        return {
            "compatibility_status": LEGACY_COMPATIBLE,
            "workflow_schema_version": WORKFLOW_SCHEMA_VERSION,
            "stored_extension": None,
            "effective_extension": _legacy_defaults(),
            "writeback_required": False,
            "writeback_policy": "NO_AUTOMATIC_MIGRATION",
        }
    validate_plan_extension(stored)
    return {
        "compatibility_status": CURRENT_CONTRACT,
        "workflow_schema_version": WORKFLOW_SCHEMA_VERSION,
        "stored_extension": copy.deepcopy(stored),
        "effective_extension": copy.deepcopy(stored),
        "writeback_required": False,
        "writeback_policy": "EXPLICIT_UPGRADE_ONLY",
    }


def validate_new_task_bundle(bundle: dict[str, Any]) -> None:
    extension = _object(bundle.get(PLAN_EXTENSION_KEY), PLAN_EXTENSION_KEY)
    validate_plan_extension(extension, require_complete=True)
    envelope = extension["task_envelope"]
    plan = extension["plan_package"]
    if envelope["task_id"] != plan["task_id"] or envelope["project_id"] != plan["project_id"]:
        raise workflow.ContractError("TaskEnvelope and PlanPackage task/project references differ")
    envelope_midcourse = {field: envelope.get(field) for field in MIDCOURSE_GATE_FIELDS if field in envelope}
    plan_midcourse = {field: plan.get(field) for field in MIDCOURSE_GATE_FIELDS if field in plan}
    if envelope_midcourse or plan_midcourse:
        if set(envelope_midcourse) != set(MIDCOURSE_GATE_FIELDS) or set(plan_midcourse) != set(MIDCOURSE_GATE_FIELDS):
            raise workflow.ContractError("TaskEnvelope and PlanPackage must agree on complete midcourse gate field support")
        if envelope_midcourse != plan_midcourse:
            raise workflow.ContractError("TaskEnvelope and PlanPackage midcourse gate fields differ")
    for packet in extension["execution_packets"]:
        if packet["plan_id"] != plan["plan_id"] or packet["plan_version"] != plan["plan_version"] or packet["task_id"] != plan["task_id"]:
            raise workflow.ContractError("ExecutionPacket does not match PlanPackage")
    for receipt in extension["execution_receipts"]:
        if receipt["plan_id"] != plan["plan_id"] or receipt["plan_version"] != plan["plan_version"] or receipt["task_id"] != plan["task_id"]:
            raise workflow.ContractError("ExecutionReceipt does not match PlanPackage")


def receipt_idempotency_key(value: dict[str, Any]) -> str:
    validate_execution_receipt(value)
    excluded = {"receipt_id", "started_at", "completed_at"}
    return contract_digest(value, excluded)


def receipts_are_idempotent(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return receipt_idempotency_key(first) == receipt_idempotency_key(second)


STRUCTURAL_CHANGE_FIELDS = {
    "task_id", "phase_id", "phases", "dependencies", "capability_refs",
    "execution_packets", "task_structure", "plan_structure",
}


def classify_version_change(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    baseline_rebuilt: bool = False,
    task_id_semantics_changed: bool = False,
    incompatible_migration: bool = False,
    preview: bool = False,
    no_op: bool = False,
    idempotent: bool = False,
) -> str:
    if preview or no_op or idempotent:
        return "NONE"
    if baseline_rebuilt or task_id_semantics_changed or incompatible_migration:
        return "MAJOR"
    if dict(before) == dict(after):
        return "NONE"
    changed = {key for key in set(before) | set(after) if before.get(key) != after.get(key)}
    if "task_id" in changed:
        return "MAJOR"
    if changed.intersection(STRUCTURAL_CHANGE_FIELDS):
        return "MINOR"
    return "PATCH"


def next_version(version: str, classification: str) -> str:
    if classification == "NONE":
        _semver(version, "version")
        return version
    return workflow.bump_semver(version, classification)


def state_owner(field: str) -> str:
    try:
        return STATE_OWNERSHIP[field]
    except KeyError as exc:
        raise workflow.ContractError(f"unknown owned state field: {field}") from exc
