#!/usr/bin/env python3
# VERSION source: ../VERSION
"""Deterministic PLAN runtime entries for F1-02 through F1-07.

This is intentionally a thin adapter around the frozen F1-01 contracts, the
existing workflow template/binding helpers, project initialization templates,
and the public phase-checkpoint-loop state-root resolver. It creates only
external PLAN-instance projections; it does not dispatch work, create
checkpoints, call governance Skills, or ingest knowledge.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

import plan_contracts as contracts
import project_init
import workflow_contracts as workflow
import workflow_module_composer as composer
import workflow_template_matcher as matcher
import governance_profiles as governance


SKILL_ROOT = Path(__file__).resolve().parents[1]
CURRENT_VERSION = (SKILL_ROOT / "VERSION").read_text(encoding="utf-8").strip()
PLAN_VERSION = "1.0.0"
GENERIC_TEMPLATE_ID = "generic-project"
PACKETS_DIR = "packets"
RECEIPTS_DIR = "receipts"
GOVERNANCE_DIR = "governance"
GOVERNANCE_REQUESTS_DIR = f"{GOVERNANCE_DIR}/requests"
GOVERNANCE_RECEIPTS_DIR = f"{GOVERNANCE_DIR}/receipts"
OWNER_GATE_RECEIPTS_DIR = f"{GOVERNANCE_DIR}/owner-gate-receipts"
CHECKPOINTS_DIR = "checkpoints"
CHECKPOINT_REFS_DIR = f"{CHECKPOINTS_DIR}/refs"
CHECKPOINT_RESUMES_DIR = f"{CHECKPOINTS_DIR}/resumes"
PACKET_ID_PREFIX = "pkt-"
GOVERNANCE_REQUEST_ID_PREFIX = "gov-"
OUTCOMES_DIR = "outcomes"
ROUTING_DECISION_RELATIVE = f"{OUTCOMES_DIR}/routing-decision.json"
ROUTING_DECISIONS_BY_CHECKPOINT_DIR = f"{OUTCOMES_DIR}/routing-decisions/by-checkpoint"
EVOLUTION_DIR = f"{OUTCOMES_DIR}/evolution"
EVOLUTION_SIGNALS_BY_CHECKPOINT_DIR = f"{EVOLUTION_DIR}/by-checkpoint"
EVOLUTION_SIGNAL_RELATIVE = f"{EVOLUTION_DIR}/evolution-signal.json"
EVOLUTION_RECEIPTS_DIR = f"{EVOLUTION_DIR}/receipts"
CONTENT_DIR = f"{OUTCOMES_DIR}/content"
KNOWLEDGE_HANDOFF_RELATIVE = f"{CONTENT_DIR}/knowledge_handoff.json"
CONTENT_RECEIPTS_DIR = f"{CONTENT_DIR}/receipts"
KNOWLEDGE_HANDOFFS_BY_CHECKPOINT_DIR = f"{CONTENT_DIR}/by-checkpoint"
OUTCOME_DECISIONS = set(contracts.OUTCOME_DECISIONS)
EVOLUTION_RESULTS = set(contracts.EVOLUTION_RESULTS)
CONTENT_RESULTS = set(contracts.CONTENT_RESULTS)
PUBLICATION_DESTINATION_SYSTEM = "external-publishing-system"
FINALIZATION_MODES = {"SIMPLE", "ADVANCED"}
FINALIZATION_RESULTS = {
    "CLOSE_READY",
    "CLOSED",
    "CLOSE_BLOCKED",
    "CLOSE_WAITING_HUMAN",
    "ALREADY_CLOSED",
}
_MISSING_ROUTE = object()
SENSITIVE_MARKERS = re.compile(
    r"(?i)(api[_ -]?key|access[_ -]?token|password|passwd|secret|private key|begin (rsa|openssh|ec) private key)"
)

CHECKPOINT_READY_ACTIONS = {"ADVANCE_PHASE", "PUBLISHED_COMMIT"}
CHECKPOINT_PAUSED_ACTIONS = {"HOLD", "BLOCKED", "FAILED"}
CHECKPOINT_HUMAN_ACTIONS = {"INCONCLUSIVE", "UNKNOWN_STATUS"}
CHECKPOINT_COMPLETION_ACTIONS = {"COMPLETION_CANDIDATE"}
CHECKPOINT_LOCAL_FIELDS = {
    "task_id",
    "checkpoint_consumer_status",
    "resume_status",
    "effective_action",
    "source_ref_digest",
    "plan_package_digest",
    "verified_evidence",
    "verified_receipt",
    "recorded_at",
    "local_consumer_version",
    "checkpoint_ref_path",
    "pause_reason",
    "required_resolution",
    "paused_at_phase",
    "completion_candidate",
}

GOVERNANCE_STAGES = set(contracts.GOVERNANCE_STAGES)
GOVERNANCE_RESULTS = set(contracts.GOVERNANCE_RESULTS)
GOVERNANCE_CANDIDATE_CLASSES = {
    "KEEP",
    "MERGE_CANDIDATE",
    "DEPRECATE_CANDIDATE",
    "ARCHIVE_CANDIDATE",
    "DELETE_REQUIRES_OWNER_APPROVAL",
}
FORMAL_PROTECTED_ASSETS = (
    "VERSION",
    "schemas/plan-contracts.schema.json",
    "templates/workflow/template_registry.json",
    "templates/workflow/00_TEMPLATE_REGISTRY.md",
    "templates/workflow/task-types/",
    "templates/workflow/modules/",
    "templates/workflow/candidates/",
)


class PlanningError(RuntimeError):
    """Expected, structured failure for PLAN runtime entries."""

    def __init__(self, code: str, message: str, *, result: str = "FAILED") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.result = result


def _load_public_checkpoint_core() -> Any:
    """Load this Skill's minimal public checkpoint-reader boundary.

    The reader is intentionally local: installing an unrelated sibling Skill
    must not be a prerequisite for planning-with-files.  The function name is
    retained as a compatibility seam for callers that replace the public
    boundary in tests or in an external checkpoint engine.
    """
    try:
        import checkpoint_reader
    except ImportError as exc:  # pragma: no cover - only broken direct installs
        raise PlanningError("STATE_ROOT_RESOLVER_REUSE_GAP", "local checkpoint reader unavailable") from exc
    if not callable(getattr(checkpoint_reader, "runtime_state_root", None)) or not callable(
        getattr(checkpoint_reader, "read_head", None)
    ):
        raise PlanningError("STATE_ROOT_RESOLVER_REUSE_GAP", "checkpoint reader boundary is incomplete")
    return checkpoint_reader


def resolve_state_root(state_root: str | Path | None = None) -> Path:
    """Resolve and harden the shared runtime root using the public resolver."""
    core = _load_public_checkpoint_core()
    try:
        candidate = Path(core.runtime_state_root(SKILL_ROOT, state_root)).resolve(strict=False)
    except Exception as exc:  # the public resolver has its own stable error vocabulary
        raise PlanningError("UNSAFE_STATE_ROOT", str(exc)) from exc

    project_root = SKILL_ROOT.resolve()
    home = Path.home().resolve()
    if not candidate.is_absolute():
        raise PlanningError("UNSAFE_STATE_ROOT", "state-root must be absolute")
    if candidate == Path("/") or candidate == home:
        raise PlanningError("UNSAFE_STATE_ROOT", "state-root cannot be the system root or home directory")
    if ".git" in candidate.parts:
        raise PlanningError("UNSAFE_STATE_ROOT", "state-root cannot be inside a .git directory")
    if candidate == project_root or project_root in candidate.parents:
        raise PlanningError("UNSAFE_STATE_ROOT", "state-root cannot be inside planning-with-files")
    return candidate


def _safe_instance_path(state_root: Path, task_id: str) -> Path:
    if task_id in {".", ".."} or "/" in task_id or "\\" in task_id:
        raise PlanningError("UNSAFE_TASK_ID", "task_id cannot escape the state-root")
    instance = state_root / task_id
    if instance.parent != state_root:
        raise PlanningError("UNSAFE_TASK_ID", "task_id must be one path segment")
    if instance.is_symlink():
        raise PlanningError("UNSAFE_STATE_ROOT", "task instance cannot be a symlink")
    return instance


def _read_json(path: Path, *, code: str = "INVALID_SCHEMA") -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanningError(code, f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PlanningError(code, f"JSON root must be an object: {path}")
    return value


def _load_task_envelope(path: str | Path) -> dict[str, Any]:
    value = _read_json(Path(path).expanduser())
    try:
        contracts.validate_task_envelope(value)
    except workflow.ContractError as exc:
        message = str(exc)
        code = (
            "UNSUPPORTED_SCHEMA_VERSION"
            if value.get("schema_version") is not None
            and value.get("schema_version") != contracts.PLAN_CONTRACT_SCHEMA_VERSION
            else "INVALID_CONTRACT"
        )
        raise PlanningError(code, message) from exc
    return copy.deepcopy(value)


def _load_risk_route(value: Any) -> Any:
    """Load a structured external result without classifying task text locally."""
    if value is _MISSING_ROUTE:
        return value
    if value is None:
        return {}
    if isinstance(value, dict):
        return copy.deepcopy(value)
    path = Path(value).expanduser()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PlanningError("INVALID_RISK_ROUTE", f"cannot read structured risk route: {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise PlanningError("INVALID_RISK_ROUTE", "structured risk route must be a JSON object")
    return loaded


def _resolve_governance_policy(
    envelope: dict[str, Any],
    *,
    risk_route: Any = _MISSING_ROUTE,
    requested_profile: str | None = None,
    supported_profile: str | None = None,
    legacy: bool | None = None,
) -> dict[str, Any]:
    """Resolve the new policy boundary while preserving old un-routed callers."""
    route = _load_risk_route(risk_route)
    embedded = any(key in envelope for key in ("risk_route", "governance_route"))
    compatibility_mode = legacy is True or (
        legacy is None
        and route is _MISSING_ROUTE
        and not embedded
        and requested_profile is None
        and supported_profile is None
    )
    decision = governance.resolve_governance_profile(
        envelope,
        risk_route=None if route is _MISSING_ROUTE else route,
        requested_profile=requested_profile,
        supported_profile=supported_profile,
        legacy=compatibility_mode,
    )
    if decision.get("error_code"):
        raise PlanningError(
            str(decision["error_code"]),
            "requested or routed governance profile exceeds the supported planning capability",
            result="BLOCKED",
        )
    return decision


def _parse_timestamp(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _task_profile(envelope: dict[str, Any]) -> str:
    risk = envelope["risk_level"]
    priority = envelope["priority"]
    if risk in {"HIGH", "CRITICAL"}:
        return "HIGH_RISK"
    if priority == "P0":
        return "FULL"
    if risk == "MEDIUM" or priority == "P1":
        return "STANDARD"
    return "LIGHTWEIGHT"


def _governance_stages_for_profile(task_profile: str) -> list[str]:
    """Return legacy stages or the centralized stages for a new profile."""
    policies = {
        "LIGHTWEIGHT": ["POST_WRITE"],
        "STANDARD": ["PRE_WRITE", "POST_WRITE"],
        "FULL": ["PRE_WRITE", "POST_WRITE", "PRE_CLOSE"],
        "HIGH_RISK": ["PRE_WRITE", "POST_WRITE", "PRE_CLOSE"],
    }
    if task_profile in governance.PROFILE_ORDER:
        return copy.deepcopy(governance.PROFILE_CONFIG[task_profile]["required_stages"])
    try:
        return copy.deepcopy(policies[task_profile])
    except KeyError as exc:
        raise PlanningError("INVALID_GOVERNANCE_POLICY", f"unknown task profile: {task_profile}") from exc


def _condition(
    condition_id: str,
    condition_type: str,
    description: str,
    *,
    required: bool = True,
    evidence_required: bool = True,
    evaluation_method: str | dict[str, Any] = "evidence_and_contract_check",
    risk_level: str | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "condition_id": condition_id,
        "condition_type": condition_type,
        "description": description,
        "required": required,
        "evidence_required": evidence_required,
        "evaluation_method": evaluation_method,
        "status": "PENDING",
        "evidence_refs": [],
    }
    if risk_level is not None:
        value["risk_level"] = risk_level
    return value


def _split_dependencies(value: str) -> list[str]:
    if not value or value.strip(" -—") in {"", "无"}:
        return []
    normalized = value.replace("、", ",").replace("，", ",").replace(";", ",")
    return [item.strip() for item in normalized.split(",") if item.strip() and item.strip() not in {"无", "-", "—"}]


def _source_text(envelope: dict[str, Any]) -> str:
    return "\n".join(
        [
            str(envelope["title"]),
            str(envelope["objective"]),
            str(envelope["business_value"]),
            str(envelope["user_pain"]),
            " ".join(str(item) for item in envelope.get("success_criteria", [])),
            " ".join(str(item) for item in envelope.get("allowed_capabilities", [])),
        ]
    )


def _template_and_phases(envelope: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], Path, dict[str, Any]]:
    """Select the registered generic template and derive its phase skeleton."""
    preview_root = SKILL_ROOT / ".f1-02-preview-project"
    selection = matcher.identify_template(
        SKILL_ROOT,
        preview_root,
        project_name=str(envelope["project_id"]),
        source_text=_source_text(envelope),
        explicit_template_id=GENERIC_TEMPLATE_ID,
    )
    binding, _default_modules, template_path = workflow.template_binding(SKILL_ROOT, selection)
    composed = composer.compose_modules(
        SKILL_ROOT,
        preview_root,
        selection,
        source_text=_source_text(envelope),
    )
    rows = workflow.parse_markdown_table(
        template_path.read_text(encoding="utf-8"),
        ["ID", "阶段/任务", "是否必需", "默认主责", "前置条件", "默认优先级", "主线", "完成条件", "证据要求"],
    )
    if not rows:
        raise PlanningError("INVALID_CONTRACT", "selected template has no executable phase rows")

    phases: list[dict[str, Any]] = []
    for row in rows:
        phase_id = row["ID"]
        phases.append(
            {
                "phase_id": phase_id,
                "title": row.get("阶段/任务", phase_id),
                "objective": row.get("完成条件", "阶段产物完成并核验"),
                "dependencies": _split_dependencies(row.get("前置条件", "")),
                "completion_conditions": [
                    _condition(
                        f"phase-{phase_id}-completion",
                        "COMPLETION",
                        row.get("完成条件", "阶段产物完成并核验"),
                    )
                ],
                "failure_conditions": [
                    _condition(
                        f"phase-{phase_id}-failure",
                        "FAILURE",
                        f"{phase_id} 的必需产物或核验失败",
                    )
                ],
                "pause_conditions": [
                    _condition(
                        f"phase-{phase_id}-pause",
                        "PAUSE",
                        f"{phase_id} 缺少必要证据或遇到未裁决阻塞",
                    )
                ],
                "required_capabilities": [],
                "status": "未开始",
                "source_template_id": binding["template_id"],
                "source_template_version": binding["template_version"],
            }
        )
    return selection, phases, template_path, {"template": binding, "modules": composed["modules"]}


def build_plan_package(
    envelope: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
    risk_route: Any = _MISSING_ROUTE,
    requested_profile: str | None = None,
    supported_profile: str | None = None,
    legacy: bool | None = None,
) -> dict[str, Any]:
    """Build and validate a deterministic PlanPackage in memory."""
    contracts.validate_task_envelope(envelope)
    resolved_policy = copy.deepcopy(policy) if policy is not None else _resolve_governance_policy(
        envelope,
        risk_route=risk_route,
        requested_profile=requested_profile,
        supported_profile=supported_profile,
        legacy=legacy,
    )
    effective_profile = resolved_policy.get("effective_profile")
    if not isinstance(effective_profile, str) or not effective_profile:
        raise PlanningError("PROFILE_NOT_SUPPORTED", "cannot build a PlanPackage without an effective governance profile", result="BLOCKED")
    envelope_digest = contracts.contract_digest(envelope)
    plan_id = f"plan-{envelope['task_id']}-{envelope_digest[:12]}"
    selection, phases, _template_path, bindings = _template_and_phases(envelope)

    human_gates = copy.deepcopy(envelope["human_gates"])
    if (envelope["risk_level"] in {"HIGH", "CRITICAL"} or effective_profile == "STRICT") and not human_gates:
        gate_risk = envelope["risk_level"] if envelope["risk_level"] in {"HIGH", "CRITICAL"} else "CRITICAL"
        human_gates.append(
            _condition(
                f"gate-{envelope['task_id']}-owner",
                "USER_GATE",
                "高风险任务必须由负责人明确授权后才能推进",
                risk_level=gate_risk,
                evaluation_method="explicit_owner_authorization",
            )
        )

    completion_conditions = [
        _condition(f"completion-{envelope['task_id']}-{index:02d}", "COMPLETION", criterion)
        for index, criterion in enumerate(envelope.get("success_criteria", []), 1)
    ]
    if not completion_conditions:
        completion_conditions.append(
            _condition(
                f"completion-{envelope['task_id']}-01",
                "COMPLETION",
                "TaskEnvelope 未提供明确成功条件，需人工补充后才能完成",
            )
        )
    failure_conditions = [
        _condition(
            f"failure-{envelope['task_id']}-01",
            "FAILURE",
            "任一必需完成条件失败，PlanPackage 进入失败处理",
        )
    ]
    pause_conditions = [
        _condition(
            f"pause-{envelope['task_id']}-01",
            "PAUSE",
            "缺少必要证据、遇到未裁决阻塞或人工闸门未满足时暂停",
        )
    ]

    task_profile = effective_profile
    legacy_mode = bool(resolved_policy.get("legacy_mode"))
    if legacy_mode:
        governance_policy = {
            "required_stages": copy.deepcopy(resolved_policy.get("required_stages", [])),
            "blocking_results": ["BLOCKED", "INCONCLUSIVE"],
            "receipt_required": True,
            "integration_status": "RESERVED_ONLY",
        }
    else:
        governance_policy = governance.plan_governance_policy(resolved_policy)
    plan: dict[str, Any] = {
        "schema_version": contracts.PLAN_CONTRACT_SCHEMA_VERSION,
        "plan_id": plan_id,
        "plan_version": PLAN_VERSION,
        "task_id": envelope["task_id"],
        "project_id": envelope["project_id"],
        "task_profile": task_profile,
        "objective": envelope["objective"],
        "scope": copy.deepcopy(envelope["scope"]),
        "non_goals": copy.deepcopy(envelope["non_goals"]),
        "assumptions": copy.deepcopy(envelope.get("assumptions", [])),
        "dependencies": copy.deepcopy(envelope.get("dependencies", [])),
        "phases": phases,
        "capability_refs": [],
        "completion_conditions": completion_conditions,
        "failure_conditions": failure_conditions,
        "pause_conditions": pause_conditions,
        "human_gates": human_gates,
        "rollback_policy": {
            "mode": "delete_new_instance_on_failure",
            "legacy_compatibility": "preserve-v080",
            "automatic_migration": False,
        },
        "evidence_policy": {
            "required": True,
            "source_evidence": copy.deepcopy(envelope["source_evidence"]),
            "completion_requires_evidence": True,
        },
        "knowledge_policy": copy.deepcopy(envelope["knowledge_policy"]),
        "governance_policy": governance_policy,
        "cleanup_policy": {
            "temporary_files": "remove",
            "lock_files": "remove",
            "preserve_unknown_fields": True,
        },
        "status_summary": {
            "status": "未开始",
            "top_level_status": "READY",
            "current_phase": phases[0]["phase_id"],
            "recommended_next_task": phases[0]["phase_id"],
            "execution_started": False,
        },
        "created_at": envelope["created_at"],
        "producer": "planning-with-files",
        "producer_version": CURRENT_VERSION,
        "source_task_envelope_digest": envelope_digest,
        "source_evidence": copy.deepcopy(envelope["source_evidence"]),
        "success_criteria": copy.deepcopy(envelope["success_criteria"]),
        "business_value": envelope["business_value"],
        "user_pain": envelope["user_pain"],
        "priority": envelope["priority"],
        "human_gate_policy": "manual-only" if human_gates else "not-required",
        "allowed_capabilities": copy.deepcopy(envelope["allowed_capabilities"]),
        "forbidden_capabilities": copy.deepcopy(envelope["forbidden_capabilities"]),
        "capability_compatibility_status": "UNCONFIRMED",
        "capability_selection_mode": "MANUAL_SELECTION_REQUIRED",
        "template_binding": copy.deepcopy(bindings["template"]),
        "module_bindings": copy.deepcopy(bindings["modules"]),
        "template_match": copy.deepcopy(selection),
        "checkpoint_refs": [],
        "knowledge_handoff_ref": None,
    }
    if not legacy_mode:
        plan["governance_profile"] = copy.deepcopy(resolved_policy)
        plan["finalization_policy"] = {"mode": resolved_policy["finalization_mode"]}
    if any(field in envelope for field in contracts.MIDCOURSE_GATE_FIELDS):
        for field in contracts.MIDCOURSE_GATE_FIELDS:
            plan[field] = copy.deepcopy(envelope[field])
    # F1-07 finalization is a compatible PlanPackage extension.  Keep the
    # policy in the PLAN fact source when the producer supplied one, while
    # leaving v0.8.0 envelopes and plans unchanged when it is absent.
    if "finalization_policy" in envelope:
        plan["finalization_policy"] = copy.deepcopy(envelope["finalization_policy"])
    if "finalization_mode" in envelope:
        plan["finalization_mode"] = copy.deepcopy(envelope["finalization_mode"])
    try:
        contracts.validate_plan_package(plan)
    except workflow.ContractError as exc:
        raise PlanningError("INVALID_CONTRACT", f"generated PlanPackage invalid: {exc}") from exc
    return plan


def _project_template_values(envelope: dict[str, Any], state_root: Path, instance: Path) -> dict[str, str]:
    args = SimpleNamespace(
        relative_path=envelope["task_id"],
        primary_agent=envelope["requested_by"],
        primary_machine="runtime-state-root",
        project_id=envelope["project_id"],
        mac_root=str(state_root),
        win_root="",
        wsl_root="",
        project_name=envelope["title"],
        business_line="",
        input_dir="",
        output_dir="",
        audit_level="A1",
    )
    values = project_init.build_values(args, instance, state_root)
    values["TIMESTAMP"] = envelope["created_at"]
    values["PROJECT_NAME"] = envelope["title"]
    values["PROJECT_ID"] = envelope["project_id"]
    values["PRIMARY_AGENT"] = envelope["requested_by"]
    return values


def _render_instance_files(
    envelope: dict[str, Any],
    plan: dict[str, Any],
    checklist: str,
    state_root: Path,
    instance: Path,
    policy: dict[str, Any] | None = None,
) -> dict[str, str]:
    files: dict[str, str] = {
        "task-envelope.json": contracts.stable_json(envelope),
        "plan-package.json": contracts.stable_json(plan),
        workflow.CHECKLIST_NAME: checklist,
    }
    resolved_policy = policy or plan.get("governance_profile") or {}
    full_tables = bool(resolved_policy.get("generate_five_tables", True))
    if full_tables:
        values = _project_template_values(envelope, state_root, instance)
        for name in project_init.PROJECT_FILES:
            if name == workflow.CHECKLIST_NAME:
                continue
            files[name] = project_init.template_content(name, values)
    task_digest = contracts.contract_digest(envelope)
    plan_digest = contracts.contract_digest(plan)
    receipt = {
        "receipt_type": "PLAN_CREATION",
        "receipt_schema_version": 1,
        "result": "CREATED",
        "task_id": envelope["task_id"],
        "project_id": envelope["project_id"],
        "plan_id": plan["plan_id"],
        "plan_version": plan["plan_version"],
        "task_envelope_digest": task_digest,
        "plan_package_digest": plan_digest,
        "state_root": str(state_root),
        "instance_path": str(instance),
        "created_files": sorted(list(files) + ["receipts/create-plan.json"]),
        "warnings": ["capability_refs is empty; no unregistered capability was auto-selected"],
        "blocking_findings": [],
        "checkpoint_refs": [],
        "knowledge_handoff_ref": None,
        "producer": "planning-with-files",
        "producer_version": CURRENT_VERSION,
        "created_at": envelope["created_at"],
    }
    files["receipts/create-plan.json"] = contracts.stable_json(receipt)
    return files


def _file_names(instance: Path) -> list[str]:
    if not instance.is_dir():
        return []
    return sorted(path.relative_to(instance).as_posix() for path in instance.rglob("*") if path.is_file())


def _existing_instance(instance: Path, envelope_digest: str) -> dict[str, Any] | None:
    if not instance.exists():
        return None
    if not instance.is_dir():
        raise PlanningError("CONFLICT", f"task instance path is not a directory: {instance}", result="CONFLICT")
    envelope_path = instance / "task-envelope.json"
    plan_path = instance / "plan-package.json"
    if not envelope_path.is_file() or not plan_path.is_file():
        raise PlanningError("CONFLICT", "existing task instance is incomplete; refusing overwrite", result="CONFLICT")
    existing_envelope = _load_task_envelope(envelope_path)
    existing_digest = contracts.contract_digest(existing_envelope)
    existing_plan = _read_json(plan_path)
    try:
        contracts.validate_plan_package(existing_plan)
    except workflow.ContractError as exc:
        raise PlanningError("CONFLICT", f"existing PlanPackage is invalid: {exc}", result="CONFLICT") from exc
    if existing_digest != envelope_digest:
        raise PlanningError("TASK_ID_CONFLICT", "same task_id already has a different TaskEnvelope", result="CONFLICT")
    return {"envelope": existing_envelope, "plan": existing_plan, "files": _file_names(instance)}


def _result_base(
    *,
    result: str,
    envelope: dict[str, Any],
    plan: dict[str, Any],
    state_root: Path,
    instance: Path,
) -> dict[str, Any]:
    profile_decision = plan.get("governance_profile")
    if isinstance(profile_decision, dict):
        decision = copy.deepcopy(profile_decision)
    else:
        decision = governance.resolve_governance_profile(
            envelope,
            requested_profile=str(plan.get("task_profile", "")),
            legacy=True,
        )
        decision["requested_profile"] = plan.get("task_profile")
        decision["effective_profile"] = plan.get("task_profile")
    result = {
        "result": result,
        "task_id": envelope["task_id"],
        "project_id": envelope["project_id"],
        "plan_id": plan["plan_id"],
        "plan_version": plan["plan_version"],
        "task_profile": plan["task_profile"],
        "state_root": str(state_root),
        "instance_path": str(instance),
        "task_envelope_digest": contracts.contract_digest(envelope),
        "plan_package_digest": contracts.contract_digest(plan),
        "created_files": [],
        "existing_files": [],
        "warnings": ["capability_refs is empty; no unregistered capability was auto-selected"],
        "blocking_findings": [],
        "no_op": False,
    }
    result.update(
        {
            "requested_profile": decision.get("requested_profile"),
            "supported_profile": decision.get("supported_profile"),
            "effective_profile": decision.get("effective_profile"),
            "risk_level": decision.get("risk_level"),
            "enabled_gates": copy.deepcopy(decision.get("enabled_gates", [])),
            "disabled_gates": copy.deepcopy(decision.get("disabled_gates", [])),
            "decision_reason": copy.deepcopy(decision.get("decision_reason", [])),
            "top_level_status": governance.normalize_top_level_status(
                plan.get("status_summary", {}).get("status") if isinstance(plan.get("status_summary"), dict) else None
            ),
        }
    )
    return result


def _cleanup_lock_parent(lock_path: Path) -> None:
    parent = lock_path.parent
    try:
        parent.rmdir()
    except OSError:
        pass


def _write_transaction(instance: Path, state_root: Path, files: dict[str, str], agent: str) -> list[str]:
    if state_root.exists() and not state_root.is_dir():
        raise PlanningError("UNSAFE_STATE_ROOT", "state-root exists but is not a directory")
    state_root.mkdir(parents=True, exist_ok=True)
    lock_path = state_root / ".planning" / f"plan-create-{instance.name}.lock"
    conflicts_dir = state_root / ".planning" / "conflicts"
    target_file = f"{instance.name}/plan-package.json"
    target = instance / "plan-package.json"
    base_digest = workflow.file_digest(target) if target.is_file() else workflow.sha256_digest("")
    try:
        lock = workflow.acquire_workflow_lock(lock_path, target_file, base_digest, agent, conflicts_dir)
    except workflow.ContractError as exc:
        _cleanup_lock_parent(lock_path)
        raise PlanningError("LOCK_CONFLICT", str(exc), result="CONFLICT") from exc

    staging: Path | None = None
    try:
        current_digest = workflow.file_digest(target) if target.is_file() else workflow.sha256_digest("")
        if current_digest != lock["base_digest"]:
            raise PlanningError("CONFLICT", "base digest changed before plan creation", result="CONFLICT")
        if instance.exists():
            raise PlanningError("TASK_ID_CONFLICT", "task instance appeared while acquiring lock", result="CONFLICT")
        staging = Path(tempfile.mkdtemp(prefix=f".{instance.name}.tmp-", dir=state_root))
        for relative, content in sorted(files.items()):
            workflow.atomic_write_text(staging / relative, content)
        if instance.exists():
            raise PlanningError("TASK_ID_CONFLICT", "task instance appeared before atomic publish", result="CONFLICT")
        staging.replace(instance)
        staging = None
        return sorted(files)
    except OSError as exc:
        raise PlanningError("FAILED", f"atomic plan publication failed: {exc}") from exc
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        try:
            workflow.release_lock(lock_path, process_id=lock["process_id"], host_name=lock["host_name"])
        finally:
            _cleanup_lock_parent(lock_path)


def create_plan(
    task_envelope_path: str | Path,
    *,
    state_root: str | Path | None = None,
    preview: bool = False,
    apply: bool = False,
    agent: str | None = None,
    risk_route: Any = _MISSING_ROUTE,
    requested_profile: str | None = None,
    supported_profile: str | None = None,
    legacy: bool | None = None,
) -> dict[str, Any]:
    """Validate an envelope and route it to LIGHT, STANDARD, or STRICT."""
    try:
        if preview == apply:
            raise PlanningError("INVALID_MODE", "exactly one of preview or apply must be true")
        envelope = _load_task_envelope(task_envelope_path)
        envelope_digest = contracts.contract_digest(envelope)
        resolved_root = resolve_state_root(state_root)
        instance = _safe_instance_path(resolved_root, envelope["task_id"])
        # Existing instances are immutable compatibility boundaries.  Read the
        # stored profile before considering any new route or requested profile.
        existing = _existing_instance(instance, envelope_digest)
        if existing is not None:
            result = _result_base(
                result="PREVIEW" if preview else "EXISTING_PLAN",
                envelope=existing["envelope"],
                plan=existing["plan"],
                state_root=resolved_root,
                instance=instance,
            )
            result["existing_files"] = existing["files"]
            result["no_op"] = True
            return result

        policy = _resolve_governance_policy(
            envelope,
            risk_route=risk_route,
            requested_profile=requested_profile,
            supported_profile=supported_profile,
            legacy=legacy,
        )
        if policy["effective_profile"] in {"LIGHT_FAST", "LIGHT_CONTROLLED"}:
            return {
                "result": "LIGHTWEIGHT_ROUTED",
                "task_id": envelope["task_id"],
                "project_id": envelope["project_id"],
                "plan_id": None,
                "plan_version": None,
                "task_profile": policy["effective_profile"],
                "requested_profile": policy["requested_profile"],
                "supported_profile": policy["supported_profile"],
                "effective_profile": policy["effective_profile"],
                "risk_level": policy["risk_level"],
                "enabled_gates": copy.deepcopy(policy["enabled_gates"]),
                "disabled_gates": copy.deepcopy(policy["disabled_gates"]),
                "decision_reason": copy.deepcopy(policy["decision_reason"]),
                "governance_policy": copy.deepcopy(policy),
                "state_root": str(resolved_root),
                "instance_path": str(instance),
                "task_envelope_digest": envelope_digest,
                "plan_package_digest": None,
                "created_files": [],
                "existing_files": [],
                "warnings": [],
                "blocking_findings": [],
                "top_level_status": "READY",
                "no_op": False,
            }

        plan = build_plan_package(envelope, policy=policy)
        selection, _phases, template_path, bindings = _template_and_phases(envelope)
        checklist = workflow.checklist_from_template(
            envelope["project_id"],
            template_path,
            bindings["template"],
            bindings["modules"],
            selection,
            envelope["requested_by"],
            now=_parse_timestamp(envelope["created_at"]),
        )
        workflow.validate_checklist_text(checklist)
        result = _result_base(
            result="PREVIEW" if preview else "CREATED",
            envelope=envelope,
            plan=plan,
            state_root=resolved_root,
            instance=instance,
        )
        files = _render_instance_files(envelope, plan, checklist, resolved_root, instance, policy)
        result["created_files"] = sorted(files)
        if preview:
            return result
        created = _write_transaction(instance, resolved_root, files, agent or envelope["requested_by"])
        result["created_files"] = created
        # Re-read and validate after atomic publication.
        published_envelope = _load_task_envelope(instance / "task-envelope.json")
        published_plan = _read_json(instance / "plan-package.json")
        contracts.validate_plan_package(published_plan)
        workflow.validate_checklist_text((instance / workflow.CHECKLIST_NAME).read_text(encoding="utf-8"))
        if contracts.contract_digest(published_envelope) != envelope_digest:
            raise PlanningError("FAILED", "published TaskEnvelope digest changed unexpectedly")
        return result
    except PlanningError as exc:
        return _result_error(exc)
    except (OSError, ValueError, workflow.ContractError) as exc:
        return _result_error(PlanningError("INTERNAL_ERROR", str(exc)))


def _safe_component(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or ".." in value or "/" in value or "\\" in value:
        raise PlanningError("INVALID_IDENTIFIER", f"invalid {label}: {value!r}")
    normalized = "".join(char.lower() if char.isalnum() else "-" for char in value).strip("-.")
    while "--" in normalized:
        normalized = normalized.replace("--", "-")
    if not normalized or len(normalized) > 80:
        raise PlanningError("INVALID_IDENTIFIER", f"invalid {label}: {value!r}")
    return normalized


def _validate_instance_root(instance_root: str | Path) -> tuple[Path, Path]:
    candidate = Path(instance_root).expanduser()
    if not candidate.is_absolute():
        raise PlanningError("UNSAFE_INSTANCE_ROOT", "instance-root must be absolute")
    if candidate.is_symlink():
        raise PlanningError("UNSAFE_INSTANCE_ROOT", "instance-root cannot be a symlink")
    resolved = candidate.resolve(strict=False)
    if ".git" in resolved.parts:
        raise PlanningError("UNSAFE_INSTANCE_ROOT", "instance-root cannot be inside a .git directory")
    state_root = resolve_state_root(resolved.parent)
    expected = _safe_instance_path(state_root, resolved.name)
    if expected != resolved:
        raise PlanningError("UNSAFE_INSTANCE_ROOT", "instance-root must be directly under the resolved state-root")
    if not resolved.is_dir():
        raise PlanningError("INSTANCE_NOT_FOUND", f"task instance does not exist: {resolved}")
    return state_root, resolved


def _load_instance(instance_root: str | Path) -> tuple[Path, Path, dict[str, Any], dict[str, Any], str]:
    state_root, instance = _validate_instance_root(instance_root)
    envelope_path = instance / "task-envelope.json"
    plan_path = instance / "plan-package.json"
    checklist_path = instance / workflow.CHECKLIST_NAME
    if any(path.is_symlink() for path in (envelope_path, plan_path, checklist_path)):
        raise PlanningError("UNSAFE_INSTANCE_ROOT", "PLAN instance files cannot be symlinks")
    if not envelope_path.is_file() or not plan_path.is_file() or not checklist_path.is_file():
        raise PlanningError("INSTANCE_INCOMPLETE", f"task instance lacks required PLAN files: {instance}")
    envelope = _load_task_envelope(envelope_path)
    plan = _read_json(plan_path)
    try:
        contracts.validate_plan_package(plan)
    except workflow.ContractError as exc:
        raise PlanningError("INVALID_CONTRACT", f"invalid PlanPackage: {exc}") from exc
    if envelope["task_id"] != plan["task_id"] or envelope["project_id"] != plan["project_id"]:
        raise PlanningError("REFERENCE_MISMATCH", "TaskEnvelope and PlanPackage references differ")
    if envelope["task_id"] != instance.name:
        raise PlanningError("REFERENCE_MISMATCH", "instance directory does not match task_id")
    checklist = checklist_path.read_text(encoding="utf-8")
    try:
        workflow.validate_checklist_text(checklist)
    except workflow.ContractError as exc:
        raise PlanningError("INVALID_CHECKLIST", str(exc)) from exc
    return state_root, instance, envelope, plan, checklist


def _checkpoint_source_digest(value: dict[str, Any]) -> str:
    """Hash only the external checkpoint reference, excluding local projections."""
    payload = copy.deepcopy(value)
    for field in CHECKPOINT_LOCAL_FIELDS:
        payload.pop(field, None)
    return contracts.contract_digest(payload)


def _checkpoint_path(instance: Path, checkpoint_id: str, directory: str) -> Path:
    slug = _safe_component(checkpoint_id, "checkpoint_id")
    base = instance / directory
    if base.exists() and (base.is_symlink() or not base.is_dir()):
        raise PlanningError("UNSAFE_INSTANCE_ROOT", f"checkpoint directory must be a real directory: {directory}")
    return base / f"{slug}.json"


def _state_relative_path(state_root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(state_root.resolve(strict=False)).as_posix()
    except ValueError:
        return str(path.resolve(strict=False))


def _resolve_checkpoint_file(
    state_root: Path,
    instance: Path,
    raw_path: str,
    *,
    label: str,
) -> tuple[Path, str]:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise PlanningError("INVALID_CHECKPOINT_REF", f"{label} must be a non-empty path")
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = instance / candidate
    if candidate.is_symlink():
        raise PlanningError("CHECKPOINT_PATH_NOT_ALLOWED", f"{label} cannot be a symlink")
    resolved = candidate.resolve(strict=False)
    state = state_root.resolve(strict=False)
    project = SKILL_ROOT.resolve()
    if resolved == project or project in resolved.parents:
        raise PlanningError("CHECKPOINT_PATH_NOT_ALLOWED", f"{label} points into planning-with-files")
    if ".git" in resolved.parts or (resolved != state and state not in resolved.parents):
        raise PlanningError("CHECKPOINT_PATH_NOT_ALLOWED", f"{label} is outside the external state-root")
    if not resolved.is_file():
        raise PlanningError("CHECKPOINT_RECEIPT_NOT_FOUND" if label == "receipt_location" else "CHECKPOINT_EVIDENCE_NOT_FOUND", f"{label} does not exist: {resolved}")
    try:
        resolved.read_bytes()
    except OSError as exc:
        raise PlanningError("CHECKPOINT_RECEIPT_NOT_READABLE" if label == "receipt_location" else "CHECKPOINT_EVIDENCE_NOT_READABLE", f"cannot read {label}: {resolved}: {exc}") from exc
    return resolved, _state_relative_path(state_root, resolved)


def _checkpoint_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise PlanningError("INVALID_CHECKPOINT_REF", f"{label} must be a SHA-256 digest")
    return value.lower()


def _projection_path_matches(
    raw_path: Any,
    path: Path,
    state_root: Path,
    instance: Path,
) -> bool:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return False
    target = path.resolve(strict=False)
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve(strict=False) == target
    return any(
        candidate_value.resolve(strict=False) == target
        for candidate_value in (
            Path(raw_path),
            state_root / candidate,
            instance / candidate,
        )
    )


def _projection_digest_from_mapping(
    mapping: Any,
    path: Path,
    state_root: Path,
    instance: Path,
) -> str | None:
    if not isinstance(mapping, dict):
        return None
    for raw_path, digest in mapping.items():
        if _projection_path_matches(raw_path, path, state_root, instance):
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                raise PlanningError("CHECKPOINT_PROJECTION_DRIFT", f"invalid historical projection digest: {raw_path}")
            return digest.lower()
    return None


def _supplement_projection_hashes(
    evidence: Any,
    state_root: Path,
    instance: Path,
) -> dict[str, str]:
    """Extract only the formal projection hash shapes emitted by dabuding.

    Supplement evidence is intentionally an open evidence object, but the
    planning consumer must not treat arbitrary text as proof of a historical
    projection.  These are the stable checkpoint-core shapes: current table
    projections, scoped-baseline files, checkpoint-request audit evidence and
    the published read-head commit's table hashes.
    """
    if not isinstance(evidence, dict):
        raise PlanningError("CHECKPOINT_PROJECTION_DRIFT", "checkpoint supplement evidence is not an object")
    hashes: dict[str, str] = {}

    def add_mapping(mapping: Any) -> None:
        if not isinstance(mapping, dict):
            return
        for raw_path, digest in mapping.items():
            if not isinstance(raw_path, str) or not isinstance(digest, str):
                continue
            if re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                hashes[raw_path] = digest.lower()

    add_mapping(evidence.get("table_hashes"))
    current_projection = evidence.get("current_table_projection")
    if isinstance(current_projection, dict):
        add_mapping(current_projection.get("sha256"))
    scoped_baseline = evidence.get("scoped_baseline")
    if isinstance(scoped_baseline, dict):
        files = scoped_baseline.get("files")
        if isinstance(files, list):
            for item in files:
                if isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(item.get("sha256"), str):
                    add_mapping({item["path"]: item["sha256"]})
    checkpoint_request = evidence.get("checkpoint_request")
    if isinstance(checkpoint_request, dict):
        add_mapping({
            checkpoint_request.get("audit_path"): checkpoint_request.get("audit_sha256"),
        })
    read_head = evidence.get("read_head")
    if isinstance(read_head, dict):
        commit = read_head.get("commit")
        if isinstance(commit, dict):
            add_mapping(commit.get("table_hashes"))
    checkpoint = evidence.get("checkpoint")
    if isinstance(checkpoint, dict):
        add_mapping(checkpoint.get("table_hashes"))
    return hashes


def _load_checkpoint_supplement_projections(
    ref: dict[str, Any],
    state_root: Path,
    instance: Path,
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Validate formal supplements and return their historical hashes."""
    projections: dict[str, str] = {}
    root_binding_history: list[tuple[str, str]] = []
    supplement_items = [
        item
        for item in ref.get("evidence_refs", [])
        if isinstance(item, dict) and item.get("kind") == "checkpoint_supplement"
    ]
    if not supplement_items:
        return projections, root_binding_history
    commit_path = _checkpoint_external_file(state_root, instance, ref.get("commit_location"), "commit_location")
    commit = _read_json(commit_path, code="MIDCOURSE_CHECKPOINT_CHAIN_INVALID")
    accepted_commit_hashes = {
        _canonical_object_digest(commit),
        _raw_file_digest(commit_path),
    }
    for index, item in enumerate(supplement_items):
        raw_path = item.get("path") or item.get("location") or item.get("ref")
        expected = item.get("sha256") or item.get("digest")
        if not isinstance(raw_path, str) or not raw_path.strip() or expected is None:
            raise PlanningError("CHECKPOINT_PROJECTION_DRIFT", f"checkpoint supplement {index} is incomplete")
        expected_digest = _checkpoint_digest(expected, f"checkpoint_supplement[{index}].sha256")
        supplement_path, _ = _resolve_checkpoint_file(
            state_root,
            instance,
            raw_path,
            label=f"checkpoint_supplement[{index}]",
        )
        if _raw_file_digest(supplement_path) != expected_digest:
            raise PlanningError("CHECKPOINT_PROJECTION_DRIFT", f"checkpoint supplement digest mismatch: {raw_path}")
        supplement = _read_json(supplement_path, code="CHECKPOINT_PROJECTION_DRIFT")
        supplement_id = supplement.get("cp_id") or supplement.get("checkpoint_id")
        if supplement_id != ref.get("checkpoint_id"):
            raise PlanningError("CHECKPOINT_PROJECTION_DRIFT", "checkpoint supplement identity mismatch")
        publication = supplement.get("publication")
        declared_commit_hashes = {
            supplement.get("commit_hash"),
            publication.get("commit_hash") if isinstance(publication, dict) else None,
        }
        if not declared_commit_hashes.intersection(accepted_commit_hashes):
            raise PlanningError("CHECKPOINT_PROJECTION_DRIFT", "checkpoint supplement does not bind the immutable commit")
        if isinstance(supplement.get("task_id"), str) and supplement.get("task_id") != ref.get("task_id"):
            raise PlanningError("CHECKPOINT_PROJECTION_DRIFT", "checkpoint supplement task binding mismatch")
        if isinstance(supplement.get("plan_id"), str) and supplement.get("plan_id") != ref.get("plan_id"):
            raise PlanningError("CHECKPOINT_PROJECTION_DRIFT", "checkpoint supplement plan binding mismatch")
        if isinstance(supplement.get("phase_id"), str) and supplement.get("phase_id") != ref.get("phase_id"):
            raise PlanningError("CHECKPOINT_PROJECTION_DRIFT", "checkpoint supplement phase binding mismatch")
        if isinstance(publication, dict):
            if publication.get("source") != "PUBLISHED_COMMIT" or publication.get("verification_status") != "PASSED":
                raise PlanningError("CHECKPOINT_PROJECTION_DRIFT", "checkpoint supplement publication is not trusted")
            if publication.get("commit_id") not in {None, ref.get("checkpoint_id")}:
                raise PlanningError("CHECKPOINT_PROJECTION_DRIFT", "checkpoint supplement publication identity mismatch")
        immutable = supplement.get("immutable_engine_evidence")
        if isinstance(immutable, dict):
            commit_evidence = immutable.get("commit")
            if isinstance(commit_evidence, dict) and commit_evidence.get("sha256") not in {None, _raw_file_digest(commit_path)}:
                raise PlanningError("CHECKPOINT_PROJECTION_DRIFT", "checkpoint supplement immutable commit evidence mismatch")
            root_binding = immutable.get("root_binding")
            if isinstance(root_binding, dict) and isinstance(root_binding.get("path"), str):
                root_binding_history.append((root_binding["path"], str(ref.get("checkpoint_id"))))
        evidence = supplement.get("evidence")
        if evidence is None and isinstance(immutable, dict):
            evidence = supplement
        projections.update(
            _supplement_projection_hashes(evidence, state_root, instance)
        )
    return projections, root_binding_history


def _historical_root_binding_is_successor_projection(
    ref: dict[str, Any],
    root_binding: dict[str, Any],
    successor_ids: Iterable[str],
) -> bool:
    if any(root_binding.get(field) != ref.get(field) for field in ("task_id", "plan_id", "phase_id")):
        raise PlanningError(
            "CHECKPOINT_PROJECTION_DRIFT",
            "historical checkpoint root binding is not bound to the current task/plan/phase",
        )
    checkpoint_id = str(ref.get("checkpoint_id"))
    first_checkpoint_id = root_binding.get("first_checkpoint_id")
    latest_checkpoint_id = root_binding.get("latest_checkpoint_id")
    return first_checkpoint_id == checkpoint_id or latest_checkpoint_id in set(successor_ids)


def _checkpoint_evidence(
    ref: dict[str, Any],
    state_root: Path,
    instance: Path,
    *,
    historical: bool = False,
    successor_ids: Iterable[str] = (),
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    evidence_digests = ref.get("evidence_digests", {})
    if evidence_digests is None:
        evidence_digests = {}
    if not isinstance(evidence_digests, dict):
        raise PlanningError("INVALID_CHECKPOINT_REF", "evidence_digests must be an object")
    verified: list[dict[str, Any]] = []
    relative_refs: dict[str, str] = {}
    supplement_projections, supplement_root_bindings = _load_checkpoint_supplement_projections(ref, state_root, instance)
    commit_projection_hashes: dict[str, str] = {}
    if historical:
        commit_path = _checkpoint_external_file(state_root, instance, ref.get("commit_location"), "commit_location")
        commit = _read_json(commit_path, code="MIDCOURSE_CHECKPOINT_CHAIN_INVALID")
        commit_projection_hashes = commit.get("table_hashes", {}) if isinstance(commit.get("table_hashes", {}), dict) else {}
    for index, item in enumerate(ref.get("evidence_refs", [])):
        if isinstance(item, str):
            raw_path = item
            expected = evidence_digests.get(item)
            extra: dict[str, Any] = {}
        elif isinstance(item, dict):
            raw_path = item.get("path") or item.get("location") or item.get("ref")
            expected = item.get("sha256") or item.get("digest") or evidence_digests.get(raw_path)
            extra = copy.deepcopy(item)
        else:
            raise PlanningError("INVALID_CHECKPOINT_REF", f"evidence_refs[{index}] must be a path or object")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise PlanningError("INVALID_CHECKPOINT_REF", f"evidence_refs[{index}] has no path")
        if expected is None:
            raise PlanningError("CHECKPOINT_EVIDENCE_DIGEST_MISSING", f"missing SHA-256 for evidence_refs[{index}]")
        expected_digest = _checkpoint_digest(expected, f"evidence_refs[{index}].sha256")
        path, relative = _resolve_checkpoint_file(state_root, instance, raw_path, label=f"evidence_refs[{index}]")
        actual_digest = workflow.file_digest(path)
        root_binding: dict[str, Any] | None = None
        if extra.get("kind") == "checkpoint_root_binding":
            root_binding = _read_json(path, code="CHECKPOINT_PROJECTION_DRIFT")
            if any(root_binding.get(field) != ref.get(field) for field in ("task_id", "plan_id", "phase_id")):
                raise PlanningError(
                    "CHECKPOINT_PROJECTION_DRIFT",
                    "checkpoint root binding is not bound to the current task/plan/phase",
                )
        # A successor checkpoint legitimately replaces the shared phase head.
        # Preserve the historical evidence identity, but do not treat the old
        # head digest as the current authority. Immutable result/commit evidence
        # is still checked by the caller's history validator when available.
        if historical and extra.get("kind") == "checkpoint_head":
            extra["historical_projection"] = True
            extra["actual_sha256"] = actual_digest
            verified.append({"path": relative, "sha256": actual_digest, "historical_projection": True})
            relative_refs[relative] = actual_digest
            continue
        if historical and actual_digest != expected_digest and extra.get("kind") in {"checkpoint_root_binding", "audit"}:
            supported_digest = _projection_digest_from_mapping(
                commit_projection_hashes,
                path,
                state_root,
                instance,
            ) or _projection_digest_from_mapping(
                supplement_projections,
                path,
                state_root,
                instance,
            )
            if extra.get("kind") == "checkpoint_root_binding":
                if root_binding is None:
                    raise PlanningError("CHECKPOINT_PROJECTION_DRIFT", "checkpoint root binding is unreadable")
                successor_projection = _historical_root_binding_is_successor_projection(
                    ref,
                    root_binding,
                    successor_ids,
                )
                supplement_root_projection = any(
                    checkpoint_id == str(ref.get("checkpoint_id"))
                    and _projection_path_matches(raw_root_path, path, state_root, instance)
                    for raw_root_path, checkpoint_id in supplement_root_bindings
                )
            else:
                successor_projection = False
                supplement_root_projection = False
            if supported_digest == expected_digest or successor_projection or supplement_root_projection:
                extra["historical_projection"] = True
                extra["actual_sha256"] = actual_digest
                verified.append({"path": relative, "sha256": actual_digest, "historical_projection": True})
                relative_refs[relative] = actual_digest
                continue
        if actual_digest != expected_digest:
            raise PlanningError("CHECKPOINT_PROJECTION_DRIFT", f"evidence digest mismatch: {relative}")
        extra["path"] = raw_path
        extra["sha256"] = expected_digest
        verified.append({"path": relative, "sha256": actual_digest})
        relative_refs[relative] = actual_digest
    return relative_refs, verified


def _checkpoint_resume_entry(
    plan: dict[str, Any], checklist: str, resume_entry: str
) -> tuple[str, str | None]:
    raw = resume_entry.strip()
    if not raw:
        raise PlanningError("INVALID_RESUME_ENTRY", "resume_entry must not be empty")
    if "/" in raw:
        parts = raw.split("/")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise PlanningError("INVALID_RESUME_ENTRY", f"invalid resume_entry: {resume_entry}")
        phase_id, work_item_id = parts
    elif "#" in raw:
        parts = raw.split("#")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise PlanningError("INVALID_RESUME_ENTRY", f"invalid resume_entry: {resume_entry}")
        phase_id, work_item_id = parts
    else:
        phase_id, work_item_id = raw, None
    _find_plan_phase(plan, phase_id)
    if work_item_id is not None:
        _find_phase_and_work_item(plan, checklist, phase_id, work_item_id)
    return phase_id, work_item_id


def _checkpoint_action(ref: dict[str, Any]) -> str:
    action = ref.get("effective_action") or ref.get("checkpoint_action") or ref.get("decision")
    if not action:
        status = ref.get("checkpoint_status")
        if status in {"PASSED", "ACTIVE"}:
            action = "ADVANCE_PHASE"
        elif status in {"PAUSED", "BLOCKED", "FAILED"}:
            action = status
        elif status == "CLOSED":
            action = "COMPLETION_CANDIDATE"
        else:
            action = "UNKNOWN_STATUS"
    return action if action in CHECKPOINT_READY_ACTIONS | CHECKPOINT_PAUSED_ACTIONS | CHECKPOINT_HUMAN_ACTIONS | CHECKPOINT_COMPLETION_ACTIONS else "UNKNOWN_STATUS"


def _checkpoint_projection(ref: dict[str, Any]) -> dict[str, Any]:
    action = _checkpoint_action(ref)
    blocking = _string_values(ref.get("blocking_findings"))
    warnings = _string_values(ref.get("warnings"))
    if action in CHECKPOINT_READY_ACTIONS:
        return {
            "effective_action": action,
            "checkpoint_consumer_status": "VERIFIED",
            "resume_status": "READY",
            "task_status": "READY",
            "pause_status": "NOT_PAUSED",
            "overall_status": "进行中",
            "human_gate": "RESUME_READY",
            "blocking_findings": blocking,
            "warnings": warnings,
            "completion_candidate": False,
        }
    if action in CHECKPOINT_PAUSED_ACTIONS:
        return {
            "effective_action": action,
            "checkpoint_consumer_status": "VERIFIED",
            "resume_status": "PAUSED",
            "task_status": "PAUSED",
            "pause_status": "PAUSED",
            "overall_status": "阻塞",
            "human_gate": "WAITING_FOR_CHECKPOINT_RESOLUTION",
            "blocking_findings": blocking or [f"checkpoint action is {action}"],
            "warnings": warnings,
            "completion_candidate": False,
        }
    if action in CHECKPOINT_COMPLETION_ACTIONS:
        return {
            "effective_action": action,
            "checkpoint_consumer_status": "VERIFIED",
            "resume_status": "READY",
            "task_status": "COMPLETION_CANDIDATE",
            "pause_status": "NOT_PAUSED",
            "overall_status": "进行中",
            "human_gate": "PLANNING_COMPLETION_GATE_REQUIRED",
            "blocking_findings": blocking,
            "warnings": warnings,
            "completion_candidate": True,
        }
    return {
        "effective_action": "UNKNOWN_STATUS",
        "checkpoint_consumer_status": "VERIFIED",
        "resume_status": "WAITING_FOR_HUMAN",
        "task_status": "WAITING_FOR_HUMAN",
        "pause_status": "PAUSED",
        "overall_status": "暂停",
        "human_gate": "REQUIRED",
        "blocking_findings": blocking,
        "warnings": warnings,
        "completion_candidate": False,
    }


def _validate_checkpoint_reference(
    ref: dict[str, Any],
    envelope: dict[str, Any],
    plan: dict[str, Any],
    checklist: str,
    state_root: Path,
    instance: Path,
    *,
    historical: bool = False,
    successor_ids: Iterable[str] = (),
) -> dict[str, Any]:
    try:
        contracts.validate_checkpoint_ref(ref)
    except workflow.ContractError as exc:
        raise PlanningError("INVALID_CHECKPOINT_REF", str(exc)) from exc
    if ref.get("task_id") is not None and ref["task_id"] != envelope["task_id"]:
        raise PlanningError("REFERENCE_MISMATCH", "checkpoint task_id does not match TaskEnvelope")
    if ref["plan_id"] != plan["plan_id"] or ref["plan_version"] != plan["plan_version"]:
        raise PlanningError("REFERENCE_MISMATCH", "checkpoint plan_id or plan_version does not match PlanPackage")
    phase_id, work_item_id = _checkpoint_resume_entry(plan, checklist, ref["resume_entry"])
    if ref["phase_id"] != phase_id:
        raise PlanningError("REFERENCE_MISMATCH", "checkpoint phase_id does not match resume_entry")
    receipt_path, receipt_relative = _resolve_checkpoint_file(
        state_root, instance, ref["receipt_location"], label="receipt_location"
    )
    receipt_expected = ref.get("receipt_sha256") or ref.get("receipt_digest")
    receipt_actual = workflow.file_digest(receipt_path)
    if receipt_expected is not None and receipt_actual != _checkpoint_digest(receipt_expected, "receipt_sha256"):
        raise PlanningError("CHECKPOINT_PROJECTION_DRIFT", f"receipt digest mismatch: {receipt_relative}")
    evidence_map, verified_evidence = _checkpoint_evidence(
        ref,
        state_root,
        instance,
        historical=historical,
        successor_ids=successor_ids,
    )
    projection = _checkpoint_projection(ref)
    projection["historical"] = historical
    projection["current_authority"] = not historical
    return {
        "phase_id": phase_id,
        "work_item_id": work_item_id,
        "receipt": {"path": receipt_relative, "sha256": receipt_actual},
        "evidence_map": evidence_map,
        "verified_evidence": verified_evidence,
        "projection": projection,
    }


def _load_checkpoint_refs(instance: Path) -> list[dict[str, Any]]:
    directory = instance / CHECKPOINT_REFS_DIR
    if not directory.exists():
        return []
    if directory.is_symlink() or not directory.is_dir():
        raise PlanningError("UNSAFE_INSTANCE_ROOT", "checkpoint refs directory must be a real directory")
    refs: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        if path.is_symlink():
            raise PlanningError("UNSAFE_INSTANCE_ROOT", f"checkpoint ref cannot be a symlink: {path}")
        value = _read_json(path, code="INVALID_CHECKPOINT_REF")
        try:
            contracts.validate_checkpoint_ref(value)
        except workflow.ContractError as exc:
            raise PlanningError("INVALID_CHECKPOINT_REF", f"invalid stored checkpoint ref {path}: {exc}") from exc
        refs.append(value)
    return refs


def _checkpoint_decision_signature(ref: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(ref.get("plan_id", "")),
        str(ref.get("plan_version", "")),
        str(ref.get("phase_id", "")),
        f"{_checkpoint_action(ref)}:{ref.get('resume_entry', '')}",
    )


def _same_checkpoint_source_identity(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Recognize a projection refresh of one immutable checkpoint."""
    fields = (
        "checkpoint_id", "task_id", "plan_id", "phase_id", "previous_checkpoint_id", "resume_entry",
        "result_location", "commit_location", "head_location", "result_sha256",
        "head_sha256", "checkpoint_status", "publication_status", "verification_status",
        "scoped_baseline", "audit_sha256", "lineage_digest", "packet_digest",
    )
    return all(left.get(field) == right.get(field) for field in fields)


def _checkpoint_state_update(
    checklist: str,
    ref: dict[str, Any],
    context: dict[str, Any],
    ref_relative: str,
    source_digest: str,
) -> tuple[str, dict[str, Any]]:
    metadata = workflow.extract_machine_json(checklist, "workflow")
    projection = context["projection"]
    metadata["checklist_version"] = workflow.bump_semver(metadata["checklist_version"], "PATCH")
    metadata["checkpoint_consumer_status"] = projection["checkpoint_consumer_status"]
    metadata["checkpoint_action"] = projection["effective_action"]
    metadata["checkpoint_refs"] = _append_unique(_string_values(metadata.get("checkpoint_refs")), [ref_relative])
    metadata["last_trusted_checkpoint"] = ref["checkpoint_id"]
    metadata["checkpoint_ref_digest"] = source_digest
    metadata["resume_entry"] = ref["resume_entry"]
    metadata["resume_status"] = projection["resume_status"]
    metadata["task_status"] = projection["task_status"]
    metadata["pause_status"] = projection["pause_status"]
    metadata["completion_candidate"] = projection["completion_candidate"]
    metadata["current_phase"] = context["phase_id"]
    metadata["recommended_next_task"] = context["work_item_id"] or context["phase_id"]
    metadata["overall_status"] = projection["overall_status"]
    metadata["human_execution_gate"] = projection["human_gate"]
    metadata["blocking_findings"] = _append_unique(
        _string_values(metadata.get("blocking_findings")), projection["blocking_findings"]
    )
    metadata["non_blocking_findings"] = _append_unique(
        _string_values(metadata.get("non_blocking_findings")), projection["warnings"]
    )
    metadata["evidence_refs"] = _append_unique(
        _string_values(metadata.get("evidence_refs")), sorted(context["evidence_map"])
    )
    metadata["last_updated_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    history = metadata.get("change_history", [])
    if not isinstance(history, list):
        history = []
    history.append({
        "timestamp": metadata["last_updated_at"],
        "change_type": "CHECKPOINT_REF",
        "checkpoint_id": ref["checkpoint_id"],
        "effective_action": projection["effective_action"],
        "resume_status": projection["resume_status"],
        "version_classification": "PATCH",
    })
    metadata["change_history"] = history
    updated = workflow.replace_machine_json(checklist, "workflow", metadata)
    human_updates = {
        "checkpoint状态：": ref["checkpoint_status"],
        "checkpoint消费者状态：": projection["checkpoint_consumer_status"],
        "恢复状态：": projection["resume_status"],
        "可信checkpoint：": ref["checkpoint_id"],
        "恢复入口：": ref["resume_entry"],
    }
    if projection["blocking_findings"]:
        human_updates["checkpoint阻塞："] = "；".join(projection["blocking_findings"])
    if projection["warnings"]:
        human_updates["checkpoint警告："] = "；".join(projection["warnings"])
    updated = _upsert_human_summary_lines(updated, human_updates)
    try:
        workflow.validate_checklist_text(updated)
    except workflow.ContractError as exc:
        raise PlanningError("INVALID_CHECKLIST", f"updated checklist invalid: {exc}") from exc
    return updated, {
        "checkpoint_id": ref["checkpoint_id"],
        "effective_action": projection["effective_action"],
        "checkpoint_consumer_status": projection["checkpoint_consumer_status"],
        "resume_status": projection["resume_status"],
        "task_status": projection["task_status"],
        "pause_status": projection["pause_status"],
        "recommended_next_task": metadata["recommended_next_task"],
        "version_classification": "PATCH",
    }


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


def _is_final_manual_acceptance_gate(gate: dict[str, Any]) -> bool:
    condition_id = str(gate.get("condition_id") or "").casefold().replace("_", "-")
    description = str(gate.get("description") or "").casefold()
    return (
        "manual-acceptance" in condition_id
        or ("manual acceptance" in description and ("final" in description or "seal" in description))
        or ("人工验收" in description and ("最终" in description or "封板" in description))
    )


def _is_before_last_plan_phase(plan: dict[str, Any], current_phase: str | None) -> bool:
    phases = plan.get("phases", [])
    if not isinstance(phases, list):
        return False
    phase_ids = [
        str(phase.get("phase_id"))
        for phase in phases
        if isinstance(phase, dict) and phase.get("phase_id")
    ]
    if not phase_ids or current_phase not in phase_ids:
        return False
    return phase_ids.index(current_phase) < len(phase_ids) - 1


def _plan_human_gate_required(plan: dict[str, Any], *, current_phase: str | None = None) -> bool:
    defer_final_acceptance = _is_before_last_plan_phase(plan, current_phase)
    for gate in plan.get("human_gates", []):
        if isinstance(gate, dict) and gate.get("status") not in {"SATISFIED", "WAIVED", "NOT_APPLICABLE"}:
            if defer_final_acceptance and _is_final_manual_acceptance_gate(gate):
                continue
            return True
    return False


OWNER_GATE_RECEIPT_TYPE = "OWNER_GATE_REGISTRATION"
OWNER_GATE_RECEIPT_SCHEMA_VERSION = 1
OWNER_GATE_IDENTITY_ASSURANCE = "NO_CRYPTOGRAPHIC_OWNER_IDENTITY_CLAIM"


def _owner_gate_receipt_path(instance: Path, receipt_id: str) -> Path:
    return instance / OWNER_GATE_RECEIPTS_DIR / f"{receipt_id}.json"


def _owner_gate_receipt_digest(receipt: dict[str, Any]) -> str:
    return contracts.contract_digest(
        receipt,
        exclude_fields=("receipt_id", "registered_at", "receipt_digest"),
    )


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
        receipt = _read_json(path, code="INVALID_OWNER_GATE_RECEIPT")
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
) -> None:
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
    if receipt["result_commit_head"] != "VALID" or receipt["direct_read_head"] != "PASSED" or receipt.get("external_read_head", receipt.get("fuxi_read_head")) != "PASSED":
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
    if receipt["receipt_digest"] != _owner_gate_receipt_digest(receipt):
        raise PlanningError("OWNER_GATE_RECEIPT_MISMATCH", "owner-gate receipt digest does not match its content")
    if gate is not None:
        if gate.get("condition_type") != "USER_GATE" or gate.get("status") != "SATISFIED":
            raise PlanningError("OWNER_GATE_RECEIPT_MISMATCH", "PlanPackage gate is not a satisfied USER_GATE")
        ref = gate.get("owner_gate_receipt_ref")
        expected_ref = Path(OWNER_GATE_RECEIPTS_DIR, f"{receipt['receipt_id']}.json").as_posix()
        if ref != expected_ref or ref not in _string_values(gate.get("evidence_refs")):
            raise PlanningError("OWNER_GATE_RECEIPT_MISMATCH", "PlanPackage does not bind the owner-gate receipt")


def _resume_state_update(
    checklist: str,
    ref: dict[str, Any],
    context: dict[str, Any],
    resume_relative: str,
    warnings: list[str],
    evidence_refs: list[str],
) -> tuple[str, dict[str, Any]]:
    metadata = workflow.extract_machine_json(checklist, "workflow")
    metadata["checklist_version"] = workflow.bump_semver(metadata["checklist_version"], "PATCH")
    metadata["checkpoint_consumer_status"] = "VERIFIED"
    metadata["last_trusted_checkpoint"] = ref["checkpoint_id"]
    metadata["resume_status"] = "RESUMED"
    metadata["task_status"] = "READY"
    metadata["top_level_status"] = "READY"
    metadata["pause_status"] = "RESUMED"
    metadata["current_phase"] = context["phase_id"]
    metadata["recommended_next_task"] = context["work_item_id"] or context["phase_id"]
    metadata["resume_entry"] = ref["resume_entry"]
    metadata["overall_status"] = "进行中"
    metadata["human_execution_gate"] = "OPEN_FOR_RESUME_ONLY"
    metadata["resume_refs"] = _append_unique(_string_values(metadata.get("resume_refs")), [resume_relative])
    metadata["evidence_refs"] = _append_unique(_string_values(metadata.get("evidence_refs")), evidence_refs)
    metadata["non_blocking_findings"] = _append_unique(_string_values(metadata.get("non_blocking_findings")), warnings)
    metadata["last_updated_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    history = metadata.get("change_history", [])
    if not isinstance(history, list):
        history = []
    history.append({
        "timestamp": metadata["last_updated_at"],
        "change_type": "CHECKPOINT_RESUME",
        "checkpoint_id": ref["checkpoint_id"],
        "resume_entry": ref["resume_entry"],
        "version_classification": "PATCH",
    })
    metadata["change_history"] = history
    updated = workflow.replace_machine_json(checklist, "workflow", metadata)
    human_updates = {
        "恢复状态：": "RESUMED",
        "当前阶段：": context["phase_id"],
        "推荐下一步：": context["work_item_id"] or context["phase_id"],
        "恢复入口：": ref["resume_entry"],
    }
    if warnings:
        human_updates["恢复警告："] = "；".join(warnings)
    updated = _upsert_human_summary_lines(updated, human_updates)
    try:
        workflow.validate_checklist_text(updated)
    except workflow.ContractError as exc:
        raise PlanningError("INVALID_CHECKLIST", f"updated checklist invalid: {exc}") from exc
    return updated, {
        "checkpoint_id": ref["checkpoint_id"],
        "resume_entry": ref["resume_entry"],
        "task_status": "READY",
        "resume_status": "RESUMED",
        "proposed_phase": context["phase_id"],
        "proposed_work_item": context["work_item_id"],
        "version_classification": "PATCH",
    }


def record_checkpoint_ref(
    instance_root: str | Path,
    checkpoint_ref_path: str | Path,
    *,
    preview: bool = False,
    apply: bool = False,
    agent: str = "planning-with-files",
) -> dict[str, Any]:
    """Consume an external checkpoint reference without creating checkpoint authority."""
    try:
        if preview == apply:
            raise PlanningError("INVALID_MODE", "exactly one of preview or apply must be true")
        state_root, instance, envelope, plan, checklist = _load_instance(instance_root)
        incoming = _read_json(Path(checkpoint_ref_path).expanduser(), code="INVALID_CHECKPOINT_REF")
        context = _validate_checkpoint_reference(incoming, envelope, plan, checklist, state_root, instance)
        source_digest = _checkpoint_source_digest(incoming)
        target = _checkpoint_path(instance, incoming["checkpoint_id"], CHECKPOINT_REFS_DIR)
        existing = None
        if target.exists():
            if target.is_symlink():
                raise PlanningError("UNSAFE_INSTANCE_ROOT", "checkpoint ref cannot be a symlink")
            existing = _read_json(target, code="INVALID_CHECKPOINT_REF")
            existing_digest = existing.get("source_ref_digest") or _checkpoint_source_digest(existing)
            if existing_digest == source_digest:
                return {
                    "result": "EXISTING_CHECKPOINT_REF",
                    "checkpoint_id": incoming["checkpoint_id"],
                    "ref_path": str(target),
                    "no_op": True,
                    "idempotent": True,
                    "checkpoint_consumer_status": existing.get("checkpoint_consumer_status", "VERIFIED"),
                    "resume_status": existing.get("resume_status", "READY"),
                    "effective_action": existing.get("effective_action", _checkpoint_action(existing)),
                    "external_checkpoint_write": False,
                }
            if not _same_checkpoint_source_identity(existing, incoming):
                raise PlanningError("CHECKPOINT_ID_CONFLICT", "same checkpoint_id has different content", result="CONFLICT")
            existing = None
        stored_refs = _load_checkpoint_refs(instance)
        stored_by_id = {str(ref.get("checkpoint_id")): ref for ref in stored_refs}

        def is_ancestor(checkpoint_id: str, target_id: str | None) -> bool:
            seen: set[str] = set()
            current_id = target_id
            while isinstance(current_id, str) and current_id and current_id not in seen:
                if current_id == checkpoint_id:
                    return True
                seen.add(current_id)
                current_id = stored_by_id.get(current_id, {}).get("previous_checkpoint_id")
            return False

        for previous in stored_refs:
            if previous.get("checkpoint_id") == incoming.get("checkpoint_id"):
                continue
            is_declared_successor = is_ancestor(str(previous.get("checkpoint_id")), incoming.get("previous_checkpoint_id"))
            if (
                previous.get("phase_id") == incoming.get("phase_id")
                and _checkpoint_decision_signature(previous) != _checkpoint_decision_signature(incoming)
                and not is_declared_successor
            ):
                raise PlanningError("CHECKPOINT_DECISION_CONFLICT", "conflicting authoritative checkpoints exist for the same phase", result="CONFLICT")
        ref_relative = f"{CHECKPOINT_REFS_DIR}/{target.name}"
        projection = context["projection"]
        stored = copy.deepcopy(incoming)
        stored.setdefault("task_id", envelope["task_id"])
        stored["checkpoint_consumer_status"] = projection["checkpoint_consumer_status"]
        stored["resume_status"] = projection["resume_status"]
        stored["effective_action"] = projection["effective_action"]
        stored["source_ref_digest"] = source_digest
        stored["plan_package_digest"] = contracts.contract_digest(plan)
        stored["verified_evidence"] = context["verified_evidence"]
        stored["verified_receipt"] = context["receipt"]
        stored["recorded_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        stored["local_consumer_version"] = CURRENT_VERSION
        stored["checkpoint_ref_path"] = ref_relative
        stored["completion_candidate"] = projection["completion_candidate"]
        if projection["blocking_findings"]:
            stored["pause_reason"] = "; ".join(projection["blocking_findings"])
            stored["required_resolution"] = "resolve the external checkpoint decision before resuming"
            stored["paused_at_phase"] = context["phase_id"]
        contracts.validate_checkpoint_ref(stored)
        updated_checklist, state_update = _checkpoint_state_update(
            checklist, stored, context, ref_relative, source_digest
        )
        result: dict[str, Any] = {
            "result": "PREVIEW" if preview else "RECORDED_CHECKPOINT_REF",
            "checkpoint_id": stored["checkpoint_id"],
            "ref_path": str(target),
            "checkpoint_consumer_status": projection["checkpoint_consumer_status"],
            "resume_status": projection["resume_status"],
            "effective_action": projection["effective_action"],
            "state_update": state_update,
            "planned_files": [ref_relative, workflow.CHECKLIST_NAME],
            "created_files": [],
            "warnings": projection["warnings"],
            "blocking_findings": projection["blocking_findings"],
            "would_write": preview,
            "external_checkpoint_write": False,
            "state_root": str(state_root),
            "instance_path": str(instance),
        }
        if preview:
            return result
        expected = {
            ref_relative: workflow.file_digest(target) if target.exists() else workflow.sha256_digest(""),
            workflow.CHECKLIST_NAME: workflow.file_digest(instance / workflow.CHECKLIST_NAME),
        }
        _transaction_write(
            instance,
            state_root,
            {ref_relative: contracts.stable_json(stored), workflow.CHECKLIST_NAME: updated_checklist},
            expected_digests=expected,
            lock_target=workflow.CHECKLIST_NAME,
            lock_name="checkpoint-ref",
            agent=agent,
            transaction_tag="f1-05",
        )
        saved = _read_json(target, code="INVALID_CHECKPOINT_REF")
        contracts.validate_checkpoint_ref(saved)
        workflow.validate_checklist_text((instance / workflow.CHECKLIST_NAME).read_text(encoding="utf-8"))
        result["created_files"] = [ref_relative, workflow.CHECKLIST_NAME]
        result["would_write"] = False
        return result
    except PlanningError as exc:
        return _result_error(exc)
    except (OSError, ValueError, workflow.ContractError) as exc:
        return _result_error(PlanningError("INTERNAL_ERROR", str(exc)))


def resume_from_checkpoint(
    instance_root: str | Path,
    checkpoint_id: str,
    *,
    preview: bool = False,
    apply: bool = False,
    agent: str = "planning-with-files",
) -> dict[str, Any]:
    """Create a local resume projection from a verified external checkpoint ref."""
    try:
        if preview == apply:
            raise PlanningError("INVALID_MODE", "exactly one of preview or apply must be true")
        state_root, instance, envelope, plan, checklist = _load_instance(instance_root)
        ref_path = _checkpoint_path(instance, checkpoint_id, CHECKPOINT_REFS_DIR)
        if not ref_path.is_file() or ref_path.is_symlink():
            raise PlanningError("CHECKPOINT_REF_NOT_FOUND", f"checkpoint ref does not exist: {checkpoint_id}")
        ref = _read_json(ref_path, code="INVALID_CHECKPOINT_REF")
        context = _validate_checkpoint_reference(ref, envelope, plan, checklist, state_root, instance)
        if ref.get("checkpoint_consumer_status") != "VERIFIED":
            raise PlanningError("CHECKPOINT_NOT_VERIFIED", "checkpoint ref has not been verified by planning-with-files")
        current_plan_digest = contracts.contract_digest(plan)
        if ref.get("plan_package_digest") and ref["plan_package_digest"] != current_plan_digest:
            raise PlanningError("PLAN_PACKAGE_REPLACED", "current PlanPackage digest differs from checkpoint ref")
        governance = _current_governance_gate(instance, envelope, plan, checklist)
        if governance["status"] == "BLOCKED":
            raise PlanningError("CHECKPOINT_BLOCKED_BY_GOVERNANCE", "; ".join(governance["blocking_findings"]))
        if governance["status"] == "INCONCLUSIVE":
            raise PlanningError("HUMAN_GATE_REQUIRED", "current CleanlinessReceipt is INCONCLUSIVE")
        if ref.get("completion_candidate"):
            raise PlanningError("COMPLETION_GATE_REQUIRED", "completion candidate still requires a planning completion gate")
        _ensure_midcourse_gate_allows_phase(
            plan,
            context["phase_id"],
            state_root=state_root,
            instance=instance,
        )
        _ensure_midcourse_gate_evidence(state_root, instance, plan)
        if _plan_human_gate_required(plan, current_phase=context["phase_id"]):
            raise PlanningError("HUMAN_GATE_REQUIRED", "current PlanPackage contains an unresolved human gate")
        if ref.get("resume_status") == "WAITING_FOR_HUMAN":
            raise PlanningError("HUMAN_GATE_REQUIRED", "checkpoint ref requires human adjudication")
        if ref.get("resume_status") == "PAUSED":
            raise PlanningError("CHECKPOINT_PAUSED", "checkpoint ref is paused and cannot be resumed automatically")
        resume_path = _checkpoint_path(instance, checkpoint_id, CHECKPOINT_RESUMES_DIR)
        source_digest = ref.get("source_ref_digest") or _checkpoint_source_digest(ref)
        if resume_path.exists():
            if resume_path.is_symlink():
                raise PlanningError("UNSAFE_INSTANCE_ROOT", "resume record cannot be a symlink")
            existing_resume = _read_json(resume_path, code="INVALID_RESUME_RECORD")
            if existing_resume.get("checkpoint_ref_digest") == source_digest and existing_resume.get("plan_package_digest") == current_plan_digest:
                return {
                    "result": "ALREADY_RESUMED",
                    "checkpoint_id": checkpoint_id,
                    "resume_entry": ref["resume_entry"],
                    "current_status": "RESUMED",
                    "proposed_status": "RESUMED",
                    "proposed_phase": context["phase_id"],
                    "proposed_work_item": context["work_item_id"],
                    "blocking_findings": [],
                    "warnings": governance["warnings"],
                    "would_write": False,
                    "no_op": True,
                }
            raise PlanningError("RESUME_CONFLICT", "resume record exists for a different checkpoint snapshot", result="CONFLICT")
        resume_relative = f"{CHECKPOINT_RESUMES_DIR}/{resume_path.name}"
        updated_checklist, state_update = _resume_state_update(
            checklist,
            ref,
            context,
            resume_relative,
            governance["warnings"],
            list(context["evidence_map"]),
        )
        resume_record = {
            "checkpoint_id": checkpoint_id,
            "task_id": envelope["task_id"],
            "plan_id": plan["plan_id"],
            "plan_version": plan["plan_version"],
            "phase_id": context["phase_id"],
            "work_item_id": context["work_item_id"],
            "resume_entry": ref["resume_entry"],
            "task_status": "READY",
            "resume_status": "RESUMED",
            "checkpoint_consumer_status": "VERIFIED",
            "checkpoint_ref_digest": source_digest,
            "plan_package_digest": current_plan_digest,
            "evidence_refs": list(context["evidence_map"]),
            "warnings": governance["warnings"],
            "blocking_findings": [],
            "resumed_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "producer": "planning-with-files",
            "producer_version": CURRENT_VERSION,
        }
        result: dict[str, Any] = {
            "result": "PREVIEW" if preview else "RESUMED",
            "checkpoint_id": checkpoint_id,
            "resume_entry": ref["resume_entry"],
            "current_status": ref.get("resume_status"),
            "proposed_status": "RESUMED",
            "proposed_phase": context["phase_id"],
            "proposed_work_item": context["work_item_id"],
            "blocking_findings": [],
            "warnings": governance["warnings"],
            "would_write": preview,
            "planned_files": [resume_relative, workflow.CHECKLIST_NAME],
            "created_files": [],
            "state_update": state_update,
            "external_checkpoint_write": False,
        }
        if preview:
            return result
        expected = {
            resume_relative: workflow.sha256_digest(""),
            workflow.CHECKLIST_NAME: workflow.file_digest(instance / workflow.CHECKLIST_NAME),
        }
        _transaction_write(
            instance,
            state_root,
            {resume_relative: contracts.stable_json(resume_record), workflow.CHECKLIST_NAME: updated_checklist},
            expected_digests=expected,
            lock_target=workflow.CHECKLIST_NAME,
            lock_name="checkpoint-resume",
            agent=agent,
            transaction_tag="f1-05-resume",
        )
        saved = _read_json(resume_path, code="INVALID_RESUME_RECORD")
        if saved.get("checkpoint_id") != checkpoint_id or saved.get("resume_status") != "RESUMED":
            raise PlanningError("FAILED", "published resume record failed post-write validation")
        workflow.validate_checklist_text((instance / workflow.CHECKLIST_NAME).read_text(encoding="utf-8"))
        result["created_files"] = [resume_relative, workflow.CHECKLIST_NAME]
        result["would_write"] = False
        return result
    except PlanningError as exc:
        return _result_error(exc)
    except (OSError, ValueError, workflow.ContractError) as exc:
        return _result_error(PlanningError("INTERNAL_ERROR", str(exc)))


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


def _append_unique(values: list[Any], additions: Iterable[Any]) -> list[Any]:
    result = copy.deepcopy(values)
    for item in additions:
        if item not in result:
            result.append(copy.deepcopy(item))
    return result


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


def _transaction_write(
    instance: Path,
    state_root: Path,
    files: dict[str, str],
    *,
    expected_digests: dict[str, str],
    lock_target: str,
    lock_name: str,
    agent: str,
    transaction_tag: str = "f1-03",
) -> list[str]:
    """Stage and publish one or more instance files under the shared lock."""
    if state_root.exists() and not state_root.is_dir():
        raise PlanningError("UNSAFE_STATE_ROOT", "state-root exists but is not a directory")
    state_root.mkdir(parents=True, exist_ok=True)
    lock_path = state_root / ".planning" / f"{lock_name}-{instance.name}.lock"
    conflicts_dir = state_root / ".planning" / "conflicts"
    lock: dict[str, Any] | None = None
    target_path = instance / lock_target
    if target_path.is_symlink():
        raise PlanningError("UNSAFE_INSTANCE_ROOT", f"transaction target cannot be a symlink: {lock_target}")
    base_digest = expected_digests.get(lock_target, workflow.file_digest(target_path) if target_path.is_file() else workflow.sha256_digest(""))
    try:
        try:
            lock = workflow.acquire_workflow_lock(lock_path, f"{instance.name}/{lock_target}", base_digest, agent, conflicts_dir)
        except workflow.ContractError as exc:
            _cleanup_lock_parent(lock_path)
            raise PlanningError("LOCK_CONFLICT", str(exc), result="CONFLICT") from exc
        for relative, digest in expected_digests.items():
            current = instance / relative
            current_digest = workflow.file_digest(current) if current.is_file() else workflow.sha256_digest("")
            if current_digest != digest:
                raise PlanningError("CONFLICT", f"base digest changed before write: {relative}", result="CONFLICT")
        staging = Path(tempfile.mkdtemp(prefix=f".{instance.name}.{transaction_tag}-", dir=state_root))
        backup_dir = staging / ".backups"
        published: list[tuple[Path, Path | None, bool]] = []
        try:
            for relative, content in sorted(files.items()):
                workflow.atomic_write_text(staging / relative, content)
            for relative in sorted(files):
                target = instance / relative
                staged = staging / relative
                if target.is_symlink():
                    raise PlanningError("UNSAFE_INSTANCE_ROOT", f"transaction target cannot be a symlink: {relative}")
                backup: Path | None = None
                existed = target.exists()
                if existed:
                    backup = backup_dir / relative
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    backup.write_bytes(target.read_bytes())
                target.parent.mkdir(parents=True, exist_ok=True)
                staged.replace(target)
                published.append((target, backup, existed))
            return sorted(files)
        except OSError as exc:
            for target, backup, existed in reversed(published):
                try:
                    if existed and backup is not None and backup.exists():
                        backup.replace(target)
                    elif not existed and target.exists():
                        target.unlink()
                except OSError:
                    pass
            raise PlanningError("FAILED", f"atomic F1-03 transaction failed: {exc}") from exc
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
    finally:
        if lock is not None:
            try:
                workflow.release_lock(lock_path, process_id=lock["process_id"], host_name=lock["host_name"])
            finally:
                _cleanup_lock_parent(lock_path)


def _result_error(exc: PlanningError) -> dict[str, Any]:
    return {
        "result": exc.result,
        "error_code": exc.code,
        "error": exc.message,
        "warnings": [],
        "blocking_findings": [exc.message],
        "top_level_status": governance.normalize_top_level_status(exc.result, blocking_findings=[exc.code]),
        "no_op": False,
    }


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


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _scope_values(value: Any, *, include_exclude: bool = True) -> list[str]:
    if not isinstance(value, dict):
        return []
    fields = ("include", "exclude") if include_exclude else ("include",)
    result: list[str] = []
    for field in fields:
        result = _append_unique(result, _string_values(value.get(field)))
    return result


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


def _find_plan_phase(plan: dict[str, Any], phase_id: str) -> dict[str, Any]:
    _safe_component(phase_id, "phase_id")
    for phase in plan.get("phases", []):
        if isinstance(phase, dict) and phase.get("phase_id") == phase_id:
            return copy.deepcopy(phase)
    raise PlanningError("INVALID_PHASE_ID", f"phase_id does not exist in PlanPackage: {phase_id}")


def _raw_file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_object_digest(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _markdown_field(text: str, label: str) -> str | None:
    pattern = re.compile(
        rf"^\s*(?:[-*]\s*)?(?:\*\*)?{re.escape(label)}(?:\*\*)?\s*:\s*(.+?)\s*$",
        re.MULTILINE,
    )
    match = pattern.search(text)
    return match.group(1).strip().strip("`") if match else None


def _midcourse_runtime_failure(
    plan: dict[str, Any],
    *,
    status: str | None = None,
    code: str | None = None,
    evidence_refs: Iterable[str] = (),
) -> dict[str, Any]:
    gate_phase = str(plan.get("midcourse_gate_phase", "unknown"))
    frozen_result = str(plan.get("midcourse_gate_result", "NOT_REACHED"))
    return {
        "configured": True,
        "status": status or frozen_result,
        "effective_result": frozen_result,
        "passed": False,
        "source": "FROZEN_PLAN_PACKAGE",
        "gate_block": f"midcourse_gate:{gate_phase}:{status or frozen_result}",
        "evidence_block": f"midcourse_gate:{gate_phase}:{code}" if code else None,
        "evidence_refs": list(evidence_refs),
    }


def _checkpoint_external_file(state_root: Path, instance: Path, raw: Any, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise PlanningError("MIDCOURSE_CHECKPOINT_CHAIN_INVALID", f"{label} is missing")
    try:
        path, _relative = _resolve_checkpoint_file(state_root, instance, raw, label=label)
    except PlanningError as exc:
        raise PlanningError("MIDCOURSE_CHECKPOINT_CHAIN_INVALID", f"{label}: {exc.code}") from exc
    return path


def _validate_dynamic_checkpoint_identity(
    ref: dict[str, Any],
    state_root: Path,
    instance: Path,
    *,
    current: bool,
) -> list[str]:
    """Validate immutable checkpoint identity without trusting a projection head."""
    checkpoint_id = ref.get("checkpoint_id")
    result_path = _checkpoint_external_file(state_root, instance, ref.get("result_location"), "result_location")
    commit_path = _checkpoint_external_file(state_root, instance, ref.get("commit_location"), "commit_location")
    head_path = _checkpoint_external_file(state_root, instance, ref.get("head_location"), "head_location")
    result_digest = _raw_file_digest(result_path)
    result_evidence_digests = [
        item.get("sha256")
        for item in ref.get("evidence_refs", [])
        if isinstance(item, dict) and item.get("kind") == "checkpoint_result"
    ]
    expected_result_digest = ref.get("result_sha256") or (result_evidence_digests[0] if result_evidence_digests else None)
    if not isinstance(expected_result_digest, str) or result_digest != expected_result_digest.lower():
        raise PlanningError("MIDCOURSE_CHECKPOINT_CHAIN_INVALID", f"result digest mismatch: {checkpoint_id}")
    result = _read_json(result_path, code="MIDCOURSE_CHECKPOINT_CHAIN_INVALID")
    commit = _read_json(commit_path, code="MIDCOURSE_CHECKPOINT_CHAIN_INVALID")
    for value, label in ((result, "result"), (commit, "commit")):
        if value.get("cp_id") != checkpoint_id or value.get("task_id") != ref.get("task_id"):
            raise PlanningError("MIDCOURSE_CHECKPOINT_CHAIN_INVALID", f"{label} identity mismatch: {checkpoint_id}")
        if value.get("phase_id") != ref.get("phase_id"):
            raise PlanningError("MIDCOURSE_CHECKPOINT_CHAIN_INVALID", f"{label} phase mismatch: {checkpoint_id}")
    if commit.get("result_hash") != result_digest:
        raise PlanningError("MIDCOURSE_CHECKPOINT_CHAIN_INVALID", f"commit does not bind result: {checkpoint_id}")
    commit_file_digest = _raw_file_digest(commit_path)
    evidence_commit_digests = {
        item.get("sha256")
        for item in ref.get("evidence_refs", [])
        if isinstance(item, dict) and item.get("kind") == "checkpoint_commit"
    }
    declared_commit_hash = ref.get("commit_hash")
    accepted_commit_digests = {
        commit_file_digest,
        _canonical_object_digest(commit),
        *(item for item in evidence_commit_digests if isinstance(item, str)),
    }
    if isinstance(declared_commit_hash, str) and declared_commit_hash not in accepted_commit_digests:
        raise PlanningError("MIDCOURSE_CHECKPOINT_CHAIN_INVALID", f"commit digest mismatch: {checkpoint_id}")
    if current:
        head_digest = _raw_file_digest(head_path)
        head_evidence_digests = [
            item.get("sha256")
            for item in ref.get("evidence_refs", [])
            if isinstance(item, dict) and item.get("kind") == "checkpoint_head"
        ]
        expected_head_digest = ref.get("head_sha256") or (head_evidence_digests[0] if head_evidence_digests else None)
        if not isinstance(expected_head_digest, str) or head_digest != expected_head_digest.lower():
            raise PlanningError("CHECKPOINT_PROJECTION_DRIFT", f"current head digest mismatch: {checkpoint_id}")
        head = _read_json(head_path, code="MIDCOURSE_CHECKPOINT_CHAIN_INVALID")
        if head.get("commit_id") != checkpoint_id or head.get("commit_hash") != _canonical_object_digest(commit):
            raise PlanningError("MIDCOURSE_CHECKPOINT_CHAIN_INVALID", f"head does not bind commit: {checkpoint_id}")
        if head.get("commit_sequence") != ref.get("commit_sequence"):
            raise PlanningError("MIDCOURSE_CHECKPOINT_CHAIN_INVALID", f"head sequence mismatch: {checkpoint_id}")
    return [str(result_path), str(commit_path), str(head_path)]


def _validate_dynamic_checkpoint_chain(
    state_root: Path,
    instance: Path,
    plan: dict[str, Any],
    dynamic: dict[str, Any],
) -> tuple[str, list[str]]:
    refs = _load_checkpoint_refs(instance)
    by_id = {str(ref.get("checkpoint_id")): ref for ref in refs}
    nested_dynamic = dynamic.get("dynamic_governance_state")
    latest_id = dynamic.get("latest_checkpoint")
    if latest_id is None and isinstance(nested_dynamic, dict):
        latest_id = nested_dynamic.get("latest_checkpoint")
    if not isinstance(latest_id, str) or latest_id not in by_id:
        raise PlanningError("MIDCOURSE_CHECKPOINT_CHAIN_INVALID", "latest dynamic checkpoint is not a stored ref")
    latest = by_id[latest_id]
    if latest.get("task_id") != plan.get("task_id") or latest.get("plan_id") != plan.get("plan_id"):
        raise PlanningError("MIDCOURSE_CHECKPOINT_CHAIN_INVALID", "latest checkpoint is not bound to the frozen task/plan")
    if latest.get("phase_id") != plan.get("midcourse_gate_phase"):
        raise PlanningError("MIDCOURSE_CHECKPOINT_CHAIN_INVALID", "latest checkpoint is not bound to the midcourse phase")
    if latest.get("checkpoint_status") != "PASSED" or latest.get("effective_action") != "ADVANCE_PHASE":
        raise PlanningError("MIDCOURSE_CHECKPOINT_CHAIN_INVALID", "latest checkpoint is not PASSED/ADVANCE_PHASE")
    if latest.get("publication_status") != "PUBLISHED_COMMIT" or latest.get("verification_status") != "PASSED":
        raise PlanningError("MIDCOURSE_CHECKPOINT_CHAIN_INVALID", "latest checkpoint is not officially published and verified")
    successors = [
        ref for ref in refs
        if ref.get("previous_checkpoint_id") == latest_id
        and ref.get("effective_action") == "ADVANCE_PHASE"
    ]
    if successors:
        raise PlanningError("MIDCOURSE_CHECKPOINT_CHAIN_INVALID", "dynamic latest checkpoint has a successor")

    evidence_refs: list[str] = []
    visited: set[str] = set()
    current = latest
    while True:
        checkpoint_id = str(current.get("checkpoint_id"))
        if checkpoint_id in visited:
            raise PlanningError("MIDCOURSE_CHECKPOINT_CHAIN_INVALID", "checkpoint predecessor cycle detected")
        visited.add(checkpoint_id)
        evidence_refs.extend(
            _validate_dynamic_checkpoint_identity(
                current,
                state_root,
                instance,
                current=(current is latest) and not any(r.get("previous_checkpoint_id") == current.get("checkpoint_id") for r in refs),
            )
        )
        previous_id = current.get("previous_checkpoint_id")
        if not previous_id:
            break
        previous = by_id.get(str(previous_id))
        if previous is None:
            raise PlanningError("MIDCOURSE_CHECKPOINT_CHAIN_INVALID", f"missing predecessor: {previous_id}")
        if previous.get("task_id") != plan.get("task_id") or previous.get("plan_id") != plan.get("plan_id"):
            raise PlanningError("MIDCOURSE_CHECKPOINT_CHAIN_INVALID", f"predecessor binding mismatch: {previous_id}")
        if current.get("phase_id") == previous.get("phase_id"):
            try:
                if int(current.get("commit_sequence")) != int(previous.get("commit_sequence")) + 1:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise PlanningError("MIDCOURSE_CHECKPOINT_CHAIN_INVALID", f"predecessor sequence is discontinuous: {checkpoint_id}") from exc
        try:
            if _parse_timestamp(str(previous.get("created_at"))) >= _parse_timestamp(str(current.get("current_at") or current.get("created_at"))):
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise PlanningError("MIDCOURSE_CHECKPOINT_CHAIN_INVALID", f"predecessor timestamp is invalid: {checkpoint_id}") from exc
        current = previous

    canonical_root = latest.get("canonical_state_root")
    if not isinstance(canonical_root, str) or not Path(canonical_root).is_absolute():
        raise PlanningError("MIDCOURSE_READ_HEAD_UNTRUSTED", "canonical checkpoint state root is missing")
    checkpoint_state_root = Path(canonical_root).resolve(strict=False)
    if checkpoint_state_root != state_root.resolve(strict=False) / "checkpoint-engine":
        raise PlanningError("MIDCOURSE_READ_HEAD_UNTRUSTED", "checkpoint state root is not the task's canonical external root")
    core = _load_public_checkpoint_core()
    head = core.read_head(instance, str(plan["task_id"]), str(plan["midcourse_gate_phase"]), checkpoint_state_root)
    head_commit_id = head.get("commit_id")
    is_trusted = False
    if head_commit_id == latest_id:
        is_trusted = (
            head.get("source") == "PUBLISHED_COMMIT"
            and head.get("effective_action") == "ADVANCE_PHASE"
        )
    else:
        curr_id = head_commit_id
        visited_trace = set()
        while curr_id and curr_id not in visited_trace:
            visited_trace.add(curr_id)
            if curr_id == latest_id:
                is_trusted = True
                break
            curr_ref = by_id.get(curr_id)
            if not curr_ref:
                break
            curr_id = curr_ref.get("previous_checkpoint_id")
    if not is_trusted:
        raise PlanningError("MIDCOURSE_READ_HEAD_UNTRUSTED", "official read-head does not publish the dynamic latest checkpoint")
    evidence_refs.append(str(checkpoint_state_root))
    return latest_id, list(dict.fromkeys(evidence_refs))


def _midcourse_gate_runtime_state(
    state_root: Path,
    instance: Path,
    plan: dict[str, Any],
) -> dict[str, Any]:
    fields = {field for field in contracts.MIDCOURSE_GATE_FIELDS if field in plan}
    if not fields:
        return {"configured": False, "status": None, "effective_result": None, "passed": True, "source": "LEGACY_PLAN"}
    if fields != set(contracts.MIDCOURSE_GATE_FIELDS):
        raise PlanningError("INVALID_CONTRACT", "PlanPackage has incomplete midcourse gate fields")
    frozen_result = str(plan["midcourse_gate_result"])
    if frozen_result == "PASS":
        return {
            "configured": True,
            "status": "PASS",
            "effective_result": "PASS",
            "passed": True,
            "source": "FROZEN_PLAN_PACKAGE",
            "gate_block": None,
            "evidence_block": None,
            "evidence_refs": [],
        }
    state_path = state_root / "discovery" / "DISCOVERY_STATE.json"
    if not state_path.is_file() or state_path.is_symlink():
        return _midcourse_runtime_failure(plan)
    try:
        dynamic = _read_json(state_path, code="MIDCOURSE_DYNAMIC_STATE_INVALID")
        gate_phase = str(plan["midcourse_gate_phase"])
        if dynamic.get("task_id") != plan.get("task_id") or dynamic.get("midcourse_gate_phase") != gate_phase:
            raise PlanningError("MIDCOURSE_DYNAMIC_STATE_INVALID", "dynamic state task or phase does not match PlanPackage")
        dynamic_status = dynamic.get("midcourse_gate_result")
        nested = dynamic.get("dynamic_governance_state")
        if not isinstance(nested, dict) or nested.get("status") != dynamic_status:
            raise PlanningError("MIDCOURSE_DYNAMIC_STATE_INVALID", "dynamic governance projection is inconsistent")
        if dynamic_status != "MIDCOURSE_PASSED":
            return _midcourse_runtime_failure(plan, status=str(dynamic_status or frozen_result))
        if nested.get("authority") != "external_dynamic_governance_projection":
            raise PlanningError("MIDCOURSE_DYNAMIC_STATE_INVALID", "dynamic state is not an official external projection")
        if nested.get("official_contract_result_remains") != frozen_result:
            raise PlanningError("MIDCOURSE_DYNAMIC_STATE_INVALID", "dynamic state does not preserve the frozen gate result")
        review_ref = dynamic.get("midcourse_review_ref")
        owner_ref = dynamic.get("midcourse_owner_confirmation_ref")
        if not isinstance(review_ref, str) or not isinstance(owner_ref, str):
            raise PlanningError("MIDCOURSE_EVIDENCE_INVALID", "dynamic review and owner references are required")
        review_path = _final_evidence_file(state_root, instance, review_ref)
        owner_path = _final_evidence_file(state_root, instance, owner_ref)
        if review_path is None or owner_path is None:
            raise PlanningError("MIDCOURSE_EVIDENCE_INVALID", "dynamic review or owner reference is not readable")
        review_text = review_path.read_text(encoding="utf-8")
        owner = _read_json(owner_path, code="MIDCOURSE_EVIDENCE_INVALID")
        review_task = _markdown_field(review_text, "Task ID")
        review_plan = _markdown_field(review_text, "Plan ID")
        review_phase = _markdown_field(review_text, "Review phase")
        reviewed_at = _markdown_field(review_text, "Reviewed at")
        if (
            review_task != plan.get("task_id")
            or review_plan != plan.get("plan_id")
            or review_phase != gate_phase
            or not isinstance(reviewed_at, str)
            or not reviewed_at
        ):
            raise PlanningError("MIDCOURSE_EVIDENCE_INVALID", "midcourse review identity or timestamp is incomplete")
        owner_confirmation = owner.get("owner_confirmation")
        if (
            owner.get("task_id") != plan.get("task_id")
            or owner.get("plan_id") != plan.get("plan_id")
            or owner.get("phase") != gate_phase
            or not isinstance(owner_confirmation, dict)
            or owner_confirmation.get("status") != "RECORDED"
            or owner_confirmation.get("decision") != "APPROVED_TO_CONTINUE"
            or owner.get("dynamic_governance_state") != "MIDCOURSE_PASSED"
            or owner.get("no_drift_assessment", {}).get("overall") != "NO_DRIFT"
        ):
            raise PlanningError("MIDCOURSE_OWNER_CONFIRMATION_INVALID", "owner confirmation is not a valid no-drift approval")
        owner_recorded_at = owner.get("recorded_at")
        if not isinstance(owner_recorded_at, str):
            raise PlanningError("MIDCOURSE_OWNER_CONFIRMATION_INVALID", "owner confirmation timestamp is missing")
        try:
            if _parse_timestamp(owner_recorded_at) <= _parse_timestamp(reviewed_at):
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise PlanningError("MIDCOURSE_OWNER_CONFIRMATION_EARLY", "owner confirmation did not occur after the midcourse understanding proof") from exc
        latest_id, checkpoint_evidence = _validate_dynamic_checkpoint_chain(state_root, instance, plan, dynamic)
        if nested.get("latest_checkpoint") != latest_id:
            raise PlanningError("MIDCOURSE_DYNAMIC_STATE_INVALID", "dynamic latest checkpoint differs from validated chain")
        if dynamic.get("official_read_head") != "PASSED" or dynamic.get("official_read_head_source") != "PUBLISHED_COMMIT" or dynamic.get("official_read_head_action") != "ADVANCE_PHASE":
            raise PlanningError("MIDCOURSE_READ_HEAD_UNTRUSTED", "dynamic state does not record an official passed read-head")
        evidence = [
            str(state_path),
            str(review_path),
            str(owner_path),
            *checkpoint_evidence,
        ]
        return {
            "configured": True,
            "status": "MIDCOURSE_PASSED",
            "effective_result": "PASS",
            "passed": True,
            "source": "EXTERNAL_DYNAMIC_GOVERNANCE_PROJECTION",
            "latest_checkpoint": latest_id,
            "gate_block": None,
            "evidence_block": None,
            "evidence_refs": list(dict.fromkeys(evidence)),
        }
    except PlanningError as exc:
        return _midcourse_runtime_failure(plan, code=exc.code)
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        return _midcourse_runtime_failure(plan, code="MIDCOURSE_DYNAMIC_STATE_INVALID")


def _midcourse_gate_block_for_phase(
    plan: dict[str, Any],
    phase_id: str,
    *,
    state_root: Path | None = None,
    instance: Path | None = None,
) -> str | None:
    fields = {field for field in contracts.MIDCOURSE_GATE_FIELDS if field in plan}
    if not fields:
        return None
    if fields != set(contracts.MIDCOURSE_GATE_FIELDS):
        raise PlanningError("INVALID_CONTRACT", "PlanPackage has incomplete midcourse gate fields")
    runtime = (
        _midcourse_gate_runtime_state(state_root, instance, plan)
        if state_root is not None and instance is not None
        else {
            "effective_result": plan["midcourse_gate_result"],
            "status": plan["midcourse_gate_result"],
        }
    )
    if runtime["effective_result"] == "PASS":
        return None
    phase_ids = [
        str(item.get("phase_id"))
        for item in plan.get("phases", [])
        if isinstance(item, dict) and item.get("phase_id")
    ]
    try:
        gate_index = phase_ids.index(str(plan["midcourse_gate_phase"]))
        target_index = phase_ids.index(str(phase_id))
    except ValueError as exc:
        raise PlanningError("INVALID_CONTRACT", "midcourse gate phase is not declared by PlanPackage") from exc
    if target_index > gate_index:
        return f"midcourse gate {plan['midcourse_gate_phase']} is {runtime['status']}"
    return None


def _ensure_midcourse_gate_allows_phase(
    plan: dict[str, Any],
    phase_id: str,
    *,
    state_root: Path | None = None,
    instance: Path | None = None,
) -> None:
    blocking = _midcourse_gate_block_for_phase(
        plan,
        phase_id,
        state_root=state_root,
        instance=instance,
    )
    if blocking:
        raise PlanningError("MIDCOURSE_GATE_REQUIRED", blocking)


def _midcourse_gate_evidence_block(
    state_root: Path,
    instance: Path,
    plan: dict[str, Any],
) -> str | None:
    fields = {field for field in contracts.MIDCOURSE_GATE_FIELDS if field in plan}
    if not fields:
        return None
    if fields != set(contracts.MIDCOURSE_GATE_FIELDS):
        raise PlanningError("INVALID_CONTRACT", "PlanPackage has incomplete midcourse gate fields")
    runtime = _midcourse_gate_runtime_state(state_root, instance, plan)
    if runtime["evidence_block"]:
        return runtime["evidence_block"]
    if not runtime["passed"]:
        return None
    if runtime.get("source") == "EXTERNAL_DYNAMIC_GOVERNANCE_PROJECTION":
        return None
    missing: list[str] = []
    for field in (
        "midcourse_review_ref",
        "midcourse_owner_confirmation_ref",
        "owner_acceptance_checklist_ref",
    ):
        raw = plan.get(field)
        if not isinstance(raw, str) or _final_evidence_file(state_root, instance, raw) is None:
            missing.append(field)
    if missing:
        return "midcourse_gate:PASS_EVIDENCE_NOT_FOUND:" + ",".join(missing)
    return None


def _ensure_midcourse_gate_evidence(
    state_root: Path,
    instance: Path,
    plan: dict[str, Any],
) -> None:
    blocking = _midcourse_gate_evidence_block(state_root, instance, plan)
    if blocking:
        raise PlanningError("MIDCOURSE_GATE_EVIDENCE_REQUIRED", blocking)


def _midcourse_gate_finalization_block(
    state_root: Path,
    instance: Path,
    plan: dict[str, Any],
) -> str | None:
    fields = {field for field in contracts.MIDCOURSE_GATE_FIELDS if field in plan}
    if not fields:
        return None
    if fields != set(contracts.MIDCOURSE_GATE_FIELDS):
        raise PlanningError("INVALID_CONTRACT", "PlanPackage has incomplete midcourse gate fields")
    runtime = _midcourse_gate_runtime_state(state_root, instance, plan)
    if not runtime["passed"]:
        return runtime["gate_block"]
    return None


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


def _explicit_dirty_paths(envelope: dict[str, Any], plan: dict[str, Any], checklist_metadata: dict[str, Any]) -> list[str]:
    result: list[str] = []
    sources = (
        envelope.get("known_dirty_paths"),
        plan.get("known_dirty_paths"),
        plan.get("governance_policy", {}).get("known_dirty_paths") if isinstance(plan.get("governance_policy"), dict) else None,
        checklist_metadata.get("known_dirty_paths"),
    )
    for source in sources:
        result = _append_unique(result, _string_values(source))
    return result


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
            existing = _read_json(receipt_path, code="INVALID_OWNER_GATE_RECEIPT")
            updated_gate = copy.deepcopy(gate)
            _validate_owner_gate_receipt(
                existing,
                state_root=actual_state_root,
                instance=instance,
                envelope=envelope,
                plan=plan,
                gate=updated_gate,
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
        receipt_id = (
            f"ogr-{_safe_component(task_id, 'task_id')}-{_safe_component(gate_id, 'gate_id')}-"
            f"{contracts.contract_digest(identity)[:16]}"
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
        receipt["receipt_digest"] = _owner_gate_receipt_digest(receipt)
        contracts.validate_plan_package(updated_plan)
        _validate_owner_gate_receipt(
            receipt,
            state_root=actual_state_root,
            instance=instance,
            envelope=envelope,
            plan=updated_plan,
            gate=updated_gate,
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
                "plan-package.json": contracts.stable_json(updated_plan),
                receipt_ref: contracts.stable_json(receipt),
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
        stored_plan = _read_json(instance / "plan-package.json", code="INVALID_CONTRACT")
        stored_receipt = _read_json(_owner_gate_receipt_path(instance, receipt_id), code="INVALID_OWNER_GATE_RECEIPT")
        contracts.validate_plan_package(stored_plan)
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


def _merge_dicts(*values: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for value in values:
        if isinstance(value, dict):
            result.update(copy.deepcopy(value))
    return result


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


def _merge_count_maps(*values: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        for key, raw in value.items():
            if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
                result[str(key)] = result.get(str(key), 0) + raw
    return result


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
            files[handoff_relative] = contracts.stable_json(handoff)
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
        handoff = _read_json(handoff_path, code="INVALID_KNOWLEDGE_HANDOFF")
        contracts.validate_knowledge_handoff_package(handoff)
        if incoming["handoff_id"] != handoff["handoff_id"] or incoming["dedupe_key"] != handoff["dedupe_key"]:
            raise PlanningError("CONTENT_RECEIPT_MISMATCH", "receipt handoff_id or dedupe_key does not match handoff")
        if incoming["task_id"] != envelope["task_id"] or incoming["plan_id"] != plan["plan_id"]:
            raise PlanningError("REFERENCE_MISMATCH", "content receipt task/plan does not match instance")
        if incoming["destination_system"] != PUBLICATION_DESTINATION_SYSTEM:
            raise PlanningError("CONTENT_DESTINATION_NOT_ALLOWED", "destination_system must be external-publishing-system")
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


def _finalization_bool(policy: dict[str, Any], key: str, default: bool) -> bool:
    value = policy.get(key, default)
    if not isinstance(value, bool):
        raise PlanningError("INVALID_FINALIZATION_POLICY", f"{key} must be boolean")
    return value


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


def _final_evidence_file(state_root: Path, instance: Path, raw: str) -> Path | None:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = instance / candidate
    if candidate.is_symlink():
        return None
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        return None
    if ".git" in resolved.parts or resolved == SKILL_ROOT.resolve() or SKILL_ROOT.resolve() in resolved.parents:
        return None
    state = state_root.resolve(strict=False)
    if resolved != state and state not in resolved.parents:
        return None
    return resolved if resolved.is_file() else None


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


def _final_checkpoint_gate(
    state_root: Path,
    instance: Path,
    envelope: dict[str, Any],
    plan: dict[str, Any],
    checklist: str,
    policy: dict[str, Any],
    mode: str,
) -> tuple[list[str], list[str], list[str], list[str], str | None]:
    if not _finalization_bool(policy, "require_checkpoint_ref", mode == "ADVANCED"):
        return [], [], [], [], None
    refs = _load_checkpoint_refs(instance)
    if not refs:
        return ["checkpoint:MISSING_REF"], [], [], [], None
    blocking: list[str] = []
    waiting: list[str] = []
    warnings: list[str] = []
    evidence: list[str] = []
    trusted: str | None = None
    metadata = workflow.extract_machine_json(checklist, "workflow")
    runtime = _midcourse_gate_runtime_state(state_root, instance, plan)
    authoritative_id = runtime.get("latest_checkpoint") or metadata.get("last_trusted_checkpoint")
    if isinstance(authoritative_id, str) and authoritative_id in {ref.get("checkpoint_id") for ref in refs}:
        current_id = authoritative_id
        visited_trace = {current_id}
        while True:
            next_refs = [r for r in refs if r.get("previous_checkpoint_id") == current_id and r.get("checkpoint_id") not in visited_trace]
            if not next_refs:
                break
            next_ref = max(
                next_refs,
                key=lambda r: (int(r.get("commit_sequence") or 0), str(r.get("created_at") or ""))
            )
            next_id = next_ref.get("checkpoint_id")
            visited_trace.add(next_id)
            current_id = next_id
        authoritative_id = current_id
    if not isinstance(authoritative_id, str) or authoritative_id not in {ref.get("checkpoint_id") for ref in refs}:
        leaves = [
            ref for ref in refs
            if not any(other.get("previous_checkpoint_id") == ref.get("checkpoint_id") for other in refs)
        ]
        if leaves:
            authoritative_id = max(leaves, key=lambda ref: str(ref.get("created_at", ""))).get("checkpoint_id")
    def successor_ids_for(checkpoint_id: str) -> set[str]:
        successors: set[str] = set()
        frontier = [checkpoint_id]
        while frontier:
            predecessor = frontier.pop()
            for candidate in refs:
                candidate_id = str(candidate.get("checkpoint_id"))
                if candidate_id in successors or candidate_id == checkpoint_id:
                    continue
                if candidate.get("previous_checkpoint_id") == predecessor:
                    successors.add(candidate_id)
                    frontier.append(candidate_id)
        return successors

    authoritative_lineage: set[str] = set()
    lineage_cursor = authoritative_id
    while isinstance(lineage_cursor, str) and lineage_cursor:
        if lineage_cursor in authoritative_lineage:
            break
        authoritative_lineage.add(lineage_cursor)
        current_ref = next(
            (candidate for candidate in refs if candidate.get("checkpoint_id") == lineage_cursor),
            None,
        )
        if current_ref is None or not current_ref.get("previous_checkpoint_id"):
            break
        lineage_cursor = str(current_ref["previous_checkpoint_id"])

    for ref in refs:
        checkpoint_id = str(ref.get("checkpoint_id"))
        if (
            checkpoint_id not in authoritative_lineage
            and ref.get("task_id") == envelope["task_id"]
            and ref.get("plan_id") == plan["plan_id"]
        ):
            warnings.append(f"checkpoint:{checkpoint_id}:HISTORICAL_NON_AUTHORITATIVE")
            continue
        historical = checkpoint_id != authoritative_id
        try:
            context = _validate_checkpoint_reference(
                ref,
                envelope,
                plan,
                checklist,
                state_root,
                instance,
                historical=historical,
                successor_ids=successor_ids_for(str(ref.get("checkpoint_id"))),
            )
        except PlanningError as exc:
            if exc.code in {"CHECKPOINT_PROJECTION_DRIFT", "CHECKPOINT_PATH_NOT_ALLOWED", "CHECKPOINT_RECEIPT_NOT_FOUND", "CHECKPOINT_EVIDENCE_NOT_FOUND"}:
                blocking.append(f"checkpoint:{ref.get('checkpoint_id', 'unknown')}:{exc.code}")
            else:
                waiting.append(f"checkpoint:{ref.get('checkpoint_id', 'unknown')}:{exc.code}")
            continue
        projection = context["projection"]
        if not historical:
            trusted = ref.get("checkpoint_id")
        evidence.append(f"{CHECKPOINT_REFS_DIR}/{ref['checkpoint_id']}.json")
        evidence = _append_unique(evidence, _string_values(ref.get("evidence_refs")))
        evidence = _append_unique(evidence, [context["receipt"]["path"]])
        if historical:
            warnings.append(f"checkpoint:{ref['checkpoint_id']}:HISTORICAL_SUCCESSOR_REPLACED")
            continue
        action = projection["effective_action"]
        if action in CHECKPOINT_PAUSED_ACTIONS:
            blocking.append(f"checkpoint:{ref['checkpoint_id']}:{action}")
        elif action in CHECKPOINT_HUMAN_ACTIONS:
            waiting.append(f"checkpoint:{ref['checkpoint_id']}:HUMAN_REVIEW")
        elif action not in CHECKPOINT_READY_ACTIONS | CHECKPOINT_COMPLETION_ACTIONS:
            waiting.append(f"checkpoint:{ref['checkpoint_id']}:UNRESOLVED")
    return blocking, waiting, warnings, evidence, trusted


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


def verify_plan_summary(instance_root: str | Path) -> dict[str, Any]:
    """Return the compact, deterministic completion summary without writing."""
    try:
        state_root, instance, envelope, plan, checklist = _load_instance(instance_root)
        assessment = _finalization_assessment(state_root, instance, envelope, plan, checklist)
        metadata = workflow.extract_machine_json(checklist, "workflow")
        return {
            "result": "SUMMARY",
            "task_id": envelope["task_id"],
            "plan_id": plan["plan_id"],
            "plan_version": plan["plan_version"],
            "mode": assessment["mode"],
            "current_phase": metadata.get("current_phase"),
            "completion_gate": assessment["completion_gate"],
            "top_level_status": assessment["top_level_status"],
            "blocking_findings": assessment["blocking_findings"],
            "warnings": assessment["warnings"],
            "trusted_checkpoint": assessment["trusted_checkpoint"],
            "midcourse_gate_result": assessment["midcourse_gate_result"],
            "midcourse_gate_effective_result": assessment["midcourse_gate_effective_result"],
            "midcourse_gate_source": assessment["midcourse_gate_source"],
            "required_next_action": assessment["required_next_action"],
        }
    except PlanningError as exc:
        return {
            "result": "SUMMARY",
            "task_id": Path(instance_root).expanduser().name,
            "plan_id": None,
            "plan_version": None,
            "mode": "UNKNOWN",
            "current_phase": None,
            "completion_gate": "CLOSE_BLOCKED",
            "top_level_status": "BLOCKED",
            "blocking_findings": [f"{exc.code}: {exc.message}"],
            "warnings": [],
            "trusted_checkpoint": None,
            "required_next_action": "REPAIR_BLOCKING_EVIDENCE",
        }
    except (OSError, ValueError, workflow.ContractError) as exc:
        return {
            "result": "SUMMARY",
            "task_id": Path(instance_root).expanduser().name,
            "plan_id": None,
            "plan_version": None,
            "mode": "UNKNOWN",
            "current_phase": None,
            "completion_gate": "CLOSE_BLOCKED",
            "top_level_status": "BLOCKED",
            "blocking_findings": [f"INTERNAL_ERROR: {exc}"],
            "warnings": [],
            "trusted_checkpoint": None,
            "required_next_action": "REPAIR_BLOCKING_EVIDENCE",
        }


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create-plan", help="create a PlanPackage from a TaskEnvelope")
    create.add_argument("--task-envelope", required=True, help="path to a structured TaskEnvelope JSON file")
    create.add_argument("--state-root", help="optional external runtime state root")
    create.add_argument("--agent", help="writer identity; defaults to TaskEnvelope.requested_by")
    create.add_argument(
        "--risk-route",
        help="path to the structured governance L0-L3 route JSON; omit only for legacy compatibility",
    )
    create.add_argument("--requested-profile", choices=list(governance.PROFILE_ORDER))
    create.add_argument("--supported-profile", choices=list(governance.PROFILE_ORDER))
    create.add_argument("--legacy", action="store_true", help="retain the pre-v1 governance mapping explicitly")
    mode = create.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", action="store_true")
    mode.add_argument("--apply", action="store_true")
    packet = subparsers.add_parser("create-packet", help="create one ExecutionPacket without dispatching it")
    packet.add_argument("--instance-root", required=True, help="external task instance root")
    packet.add_argument("--phase-id", required=True)
    packet.add_argument("--work-item-id", required=True)
    packet.add_argument("--agent", default="planning-with-files")
    packet.add_argument("--revision", type=int, help="optional controlled packet revision")
    packet.add_argument("--revision-type")
    packet.add_argument("--predecessor-checkpoint-id")
    packet.add_argument("--revision-reason")
    packet.add_argument("--revision-scope")
    packet.add_argument("--revision-evidence-ref", action="append", default=[])
    packet_mode = packet.add_mutually_exclusive_group(required=True)
    packet_mode.add_argument("--preview", action="store_true")
    packet_mode.add_argument("--apply", action="store_true")
    receipt = subparsers.add_parser("record-receipt", help="validate and record one ExecutionReceipt")
    receipt.add_argument("--instance-root", required=True, help="external task instance root")
    receipt.add_argument("--receipt", required=True, help="path to an ExecutionReceipt JSON")
    receipt.add_argument("--agent", default="planning-with-files")
    receipt_mode = receipt.add_mutually_exclusive_group(required=True)
    receipt_mode.add_argument("--preview", action="store_true")
    receipt_mode.add_argument("--apply", action="store_true")
    governance_parser = subparsers.add_parser(
        "create-governance-request",
        help="create one local GovernanceRequest without invoking governance",
    )
    governance_parser.add_argument("--instance-root", required=True, help="external task instance root")
    governance_parser.add_argument("--stage", required=True, choices=sorted(GOVERNANCE_STAGES))
    governance_parser.add_argument("--phase-id", required=True)
    governance_parser.add_argument("--agent", default="planning-with-files")
    governance_mode = governance_parser.add_mutually_exclusive_group(required=True)
    governance_mode.add_argument("--preview", action="store_true")
    governance_mode.add_argument("--apply", action="store_true")
    cleanliness = subparsers.add_parser(
        "record-cleanliness-receipt",
        help="validate and consume one local CleanlinessReceipt without invoking governance",
    )
    cleanliness.add_argument("--instance-root", required=True, help="external task instance root")
    cleanliness.add_argument("--receipt", required=True, help="path to a CleanlinessReceipt JSON")
    cleanliness.add_argument("--agent", default="planning-with-files")
    cleanliness_mode = cleanliness.add_mutually_exclusive_group(required=True)
    cleanliness_mode.add_argument("--preview", action="store_true")
    cleanliness_mode.add_argument("--apply", action="store_true")
    postwrite_binding = subparsers.add_parser(
        "bind-postwrite-execution-receipt",
        help="bind one existing POST_WRITE receipt to its completed execution receipt",
    )
    postwrite_binding.add_argument("--instance-root", required=True, help="external task instance root")
    postwrite_binding.add_argument("--state-root", required=True, help="explicit external runtime state root")
    postwrite_binding.add_argument("--task-id", required=True)
    postwrite_binding.add_argument("--plan-id", required=True)
    postwrite_binding.add_argument("--phase-id", required=True)
    postwrite_binding.add_argument("--postwrite-receipt-id", required=True)
    postwrite_binding.add_argument("--execution-receipt-id", required=True)
    postwrite_binding.add_argument("--agent", default="planning-with-files")
    postwrite_binding_mode = postwrite_binding.add_mutually_exclusive_group(required=True)
    postwrite_binding_mode.add_argument("--preview", action="store_true")
    postwrite_binding_mode.add_argument("--apply", action="store_true")
    checkpoint = subparsers.add_parser(
        "record-checkpoint-ref",
        help="validate and record an external checkpoint reference without creating checkpoint authority",
    )
    checkpoint.add_argument("--instance-root", required=True, help="external task instance root")
    checkpoint.add_argument("--checkpoint-ref", required=True, help="path to an external checkpoint reference JSON")
    checkpoint.add_argument("--agent", default="planning-with-files")
    checkpoint_mode = checkpoint.add_mutually_exclusive_group(required=True)
    checkpoint_mode.add_argument("--preview", action="store_true")
    checkpoint_mode.add_argument("--apply", action="store_true")
    resume = subparsers.add_parser(
        "resume-from-checkpoint",
        help="create a local resume projection from a verified external checkpoint reference",
    )
    resume.add_argument("--instance-root", required=True, help="external task instance root")
    resume.add_argument("--checkpoint-id", required=True)
    resume.add_argument("--agent", default="planning-with-files")
    resume_mode = resume.add_mutually_exclusive_group(required=True)
    resume_mode.add_argument("--preview", action="store_true")
    resume_mode.add_argument("--apply", action="store_true")
    routing = subparsers.add_parser(
        "evaluate-outcome-routing",
        help="evaluate local evolution/content value without invoking external systems",
    )
    routing.add_argument("--instance-root", required=True, help="external task instance root")
    routing.add_argument("--agent", default="planning-with-files")
    routing_mode = routing.add_mutually_exclusive_group(required=True)
    routing_mode.add_argument("--preview", action="store_true")
    routing_mode.add_argument("--apply", action="store_true")
    evolution_receipt = subparsers.add_parser(
        "record-evolution-receipt",
        help="validate and record one external EvolutionReceipt without invoking bridge systems",
    )
    evolution_receipt.add_argument("--instance-root", required=True, help="external task instance root")
    evolution_receipt.add_argument("--receipt", required=True, help="path to an EvolutionReceipt JSON")
    evolution_receipt.add_argument("--agent", default="planning-with-files")
    evolution_mode = evolution_receipt.add_mutually_exclusive_group(required=True)
    evolution_mode.add_argument("--preview", action="store_true")
    evolution_mode.add_argument("--apply", action="store_true")
    content_receipt = subparsers.add_parser(
        "record-content-ingest-receipt",
        help="validate and record one external ContentIngestReceipt without invoking the publisher",
    )
    content_receipt.add_argument("--instance-root", required=True, help="external task instance root")
    content_receipt.add_argument("--receipt", required=True, help="path to a ContentIngestReceipt JSON")
    content_receipt.add_argument("--agent", default="planning-with-files")
    content_mode = content_receipt.add_mutually_exclusive_group(required=True)
    content_mode.add_argument("--preview", action="store_true")
    content_mode.add_argument("--apply", action="store_true")
    owner_gate = subparsers.add_parser(
        "register-owner-gate",
        help="register one explicit owner USER_GATE decision through the formal PLAN writer",
    )
    owner_gate.add_argument("--instance-root", required=True, help="external task instance root")
    owner_gate.add_argument("--state-root", required=True, help="explicit external runtime state root")
    owner_gate.add_argument("--task-id", required=True)
    owner_gate.add_argument("--plan-id", required=True)
    owner_gate.add_argument("--gate-id", required=True)
    owner_gate.add_argument("--expected-status", required=True, choices=["PENDING", "SATISFIED"])
    owner_gate.add_argument("--decision", required=True, choices=["SATISFIED"])
    owner_gate.add_argument("--confirmation-reference", required=True)
    owner_gate.add_argument("--confirmation-statement", required=True)
    owner_gate.add_argument("--accepted-commit", required=True)
    owner_gate.add_argument("--accepted-checkpoint", required=True)
    owner_gate.add_argument("--result-commit-head", required=True, choices=["VALID"])
    owner_gate.add_argument("--direct-read-head", required=True, choices=["PASSED"])
    owner_gate.add_argument("--external-read-head", required=True, choices=["PASSED"])
    owner_gate.add_argument("--authorize", required=True, choices=["PRE_CLOSE"])
    owner_gate.add_argument("--evidence-ref", action="append", required=True)
    owner_gate.add_argument("--agent", default="planning-with-files")
    owner_gate_mode = owner_gate.add_mutually_exclusive_group(required=True)
    owner_gate_mode.add_argument("--preview", action="store_true")
    owner_gate_mode.add_argument("--apply", action="store_true")
    finalize = subparsers.add_parser(
        "finalize-plan",
        help="evaluate and optionally publish the final PLAN completion gate",
    )
    finalize.add_argument("--instance-root", required=True, help="external task instance root")
    finalize.add_argument("--agent", default="planning-with-files")
    finalize_mode = finalize.add_mutually_exclusive_group(required=True)
    finalize_mode.add_argument("--preview", action="store_true")
    finalize_mode.add_argument("--apply", action="store_true")
    verify = subparsers.add_parser(
        "verify-plan",
        help="emit a compact deterministic PLAN completion summary",
    )
    verify.add_argument("--instance-root", required=True, help="external task instance root")
    verify.add_argument("--summary", action="store_true", required=True)
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "create-plan":
        output = create_plan(
            args.task_envelope,
            state_root=args.state_root,
            preview=args.preview,
            apply=args.apply,
            agent=args.agent,
            risk_route=args.risk_route if args.risk_route is not None else _MISSING_ROUTE,
            requested_profile=args.requested_profile,
            supported_profile=args.supported_profile,
            legacy=True if args.legacy else None,
        )
    elif args.command == "create-packet":
        output = create_packet(
            args.instance_root,
            args.phase_id,
            args.work_item_id,
            preview=args.preview,
            apply=args.apply,
            agent=args.agent,
            revision=args.revision,
            revision_type=args.revision_type,
            predecessor_checkpoint_id=args.predecessor_checkpoint_id,
            revision_reason=args.revision_reason,
            revision_scope=args.revision_scope,
            revision_evidence_refs=args.revision_evidence_ref,
        )
    elif args.command == "record-receipt":
        output = record_receipt(
            args.instance_root,
            args.receipt,
            preview=args.preview,
            apply=args.apply,
            agent=args.agent,
        )
    elif args.command == "create-governance-request":
        output = create_governance_request(
            args.instance_root,
            args.stage,
            args.phase_id,
            preview=args.preview,
            apply=args.apply,
            agent=args.agent,
        )
    elif args.command == "record-cleanliness-receipt":
        output = record_cleanliness_receipt(
            args.instance_root,
            args.receipt,
            preview=args.preview,
            apply=args.apply,
            agent=args.agent,
        )
    elif args.command == "bind-postwrite-execution-receipt":
        output = bind_postwrite_execution_receipt(
            args.instance_root,
            state_root=args.state_root,
            task_id=args.task_id,
            plan_id=args.plan_id,
            phase_id=args.phase_id,
            postwrite_receipt_id=args.postwrite_receipt_id,
            execution_receipt_id=args.execution_receipt_id,
            preview=args.preview,
            apply=args.apply,
            agent=args.agent,
        )
    elif args.command == "record-checkpoint-ref":
        output = record_checkpoint_ref(
            args.instance_root,
            args.checkpoint_ref,
            preview=args.preview,
            apply=args.apply,
            agent=args.agent,
        )
    elif args.command == "resume-from-checkpoint":
        output = resume_from_checkpoint(
            args.instance_root,
            args.checkpoint_id,
            preview=args.preview,
            apply=args.apply,
            agent=args.agent,
        )
    elif args.command == "evaluate-outcome-routing":
        output = evaluate_outcome_routing(
            args.instance_root,
            preview=args.preview,
            apply=args.apply,
            agent=args.agent,
        )
    elif args.command == "record-evolution-receipt":
        output = record_evolution_receipt(
            args.instance_root,
            args.receipt,
            preview=args.preview,
            apply=args.apply,
            agent=args.agent,
        )
    elif args.command == "record-content-ingest-receipt":
        output = record_content_ingest_receipt(
            args.instance_root,
            args.receipt,
            preview=args.preview,
            apply=args.apply,
            agent=args.agent,
        )
    elif args.command == "register-owner-gate":
        output = register_owner_gate(
            args.instance_root,
            task_id=args.task_id,
            plan_id=args.plan_id,
            state_root=args.state_root,
            gate_id=args.gate_id,
            expected_status=args.expected_status,
            decision=args.decision,
            confirmation_reference=args.confirmation_reference,
            confirmation_statement=args.confirmation_statement,
            accepted_commit=args.accepted_commit,
            accepted_checkpoint=args.accepted_checkpoint,
            result_commit_head=args.result_commit_head,
            direct_read_head=args.direct_read_head,
            external_read_head=args.external_read_head,
            authorize=args.authorize,
            evidence_refs=args.evidence_ref,
            preview=args.preview,
            apply=args.apply,
            agent=args.agent,
        )
    elif args.command == "finalize-plan":
        output = finalize_plan(
            args.instance_root,
            preview=args.preview,
            apply=args.apply,
            agent=args.agent,
        )
    elif args.command == "verify-plan":
        output = verify_plan_summary(args.instance_root) if args.summary else {"result": "FAILED"}
    else:
        return 2
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if output.get("result") in {
        "PREVIEW",
        "LIGHTWEIGHT_ROUTED",
        "CREATED",
        "EXISTING_PLAN",
        "EXISTING_PACKET",
        "RECORDED",
        "EXISTING_RECEIPT",
        "CREATED_GOVERNANCE_REQUEST",
        "EXISTING_GOVERNANCE_REQUEST",
        "RECORDED_CLEANLINESS_RECEIPT",
        "EXISTING_CLEANLINESS_RECEIPT",
        "BOUND_POSTWRITE_EXECUTION_RECEIPT",
        "EXISTING_POSTWRITE_EXECUTION_BINDING",
        "RECORDED_CHECKPOINT_REF",
        "EXISTING_CHECKPOINT_REF",
        "RESUMED",
        "ALREADY_RESUMED",
        "CREATED_OUTCOME_ROUTING",
        "EXISTING_ROUTING_DECISION",
        "RECORDED_EVOLUTION_RECEIPT",
        "EXISTING_EVOLUTION_RECEIPT",
        "RECORDED_CONTENT_INGEST_RECEIPT",
        "EXISTING_CONTENT_INGEST_RECEIPT",
        "REGISTERED_OWNER_GATE",
        "EXISTING_OWNER_GATE",
        "CLOSE_READY",
        "CLOSED",
        "CLOSE_BLOCKED",
        "CLOSE_WAITING_HUMAN",
        "ALREADY_CLOSED",
        "SUMMARY",
        "RECORDED_OUTCOME_ROUTING",
        "EXISTING_OUTCOME_ROUTING",
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
