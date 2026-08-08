"""Gate 2 extracted module: plan_builder.py.

Generated from the Gate 1 planning.py baseline.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
import copy

from pwf_governed._legacy import (
    governance_profiles,
    plan_contracts,
    project_init,
    workflow_contracts,
    workflow_module_composer,
    workflow_template_matcher,
)
from pwf_governed._legacy import governance_profiles as governance
from pwf_governed._legacy import plan_contracts as contracts
from pwf_governed._legacy import project_init
from pwf_governed._legacy import workflow_contracts as workflow
from pwf_governed._legacy import workflow_module_composer as composer
from pwf_governed._legacy import workflow_template_matcher as matcher

from pwf_governed._legacy import (
    governance_profiles,
    plan_contracts,
    project_init,
    workflow_contracts,
    workflow_module_composer,
    workflow_template_matcher,
)
from pwf_governed.core.constants import (
    CURRENT_VERSION,
    GENERIC_TEMPLATE_ID,
    PLAN_VERSION,
    SKILL_ROOT,
    _MISSING_ROUTE,
)
from pwf_governed.core.envelope import (
    _load_instance,
    _load_risk_route,
    _load_task_envelope,
    _parse_timestamp,
    _read_json,
    _result_base,
    _result_error,
    _safe_instance_path,
    _write_transaction,
    resolve_state_root,
)
from pwf_governed.core.errors import (
    PlanningError,
)
from pwf_governed.edition import adapt_output_once, edition_operation

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
    task_digest = contracts.contract_digest(envelope)
    plan_digest = contracts.contract_digest(plan)
    persisted_plan = adapt_output_once(
        plan,
        payload_kind="PlanPackage",
        callsite_id="plan-package-write",
    )
    files: dict[str, str] = {
        "task-envelope.json": contracts.stable_json(envelope),
        "plan-package.json": contracts.stable_json(persisted_plan),
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
    _state_root, _instance, existing_envelope, existing_plan, _checklist = _load_instance(instance)
    existing_digest = contracts.contract_digest(existing_envelope)
    if existing_digest != envelope_digest:
        raise PlanningError("TASK_ID_CONFLICT", "same task_id already has a different TaskEnvelope", result="CONFLICT")
    return {"envelope": existing_envelope, "plan": existing_plan, "files": _file_names(instance)}

@edition_operation
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
        _published_root, _published_instance, published_envelope, published_plan, _published_checklist = _load_instance(instance)
        if contracts.contract_digest(published_envelope) != envelope_digest:
            raise PlanningError("FAILED", "published TaskEnvelope digest changed unexpectedly")
        return result
    except PlanningError as exc:
        return _result_error(exc)
    except (OSError, ValueError, workflow.ContractError) as exc:
        return _result_error(PlanningError("INTERNAL_ERROR", str(exc)))
