#!/usr/bin/env python3
"""Human approval gate and controlled workflow-template promotion for P4-02."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import workflow_contracts as contracts
import workflow_candidate_validator as validator


PROMOTION_TYPES = {
    "REUSABLE_IMPROVEMENT",
    "TEMPLATE_MISSING",
    "TEMPLATE_REDUNDANT",
    "TEMPLATE_ERROR",
    "NEW_MODULE_CANDIDATE",
    "NEW_TEMPLATE_CANDIDATE",
}
NON_PROMOTABLE = {"PROJECT_EXCEPTION", "INCIDENTAL_ISSUE", "NO_ACTION"}
RECEIPT_DECISIONS = {"APPROVE", "REJECT"}
OPERATIONS = {
    "ADD_TASK",
    "REMOVE_TASK",
    "CHANGE_ORDER",
    "CHANGE_DEPENDENCY",
    "CHANGE_OWNER",
    "CHANGE_COMPLETION_REQUIREMENT",
    "CHANGE_EVIDENCE_REQUIREMENT",
    "UPDATE_KEYWORDS",
    "UPDATE_EXCLUDE_KEYWORDS",
    "UPDATE_DOCUMENTATION",
}
TASK_COLUMNS = ["ID", "阶段/任务", "是否必需", "默认主责", "前置条件", "默认优先级", "主线", "完成条件", "证据要求"]
RISK_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


class PromotionError(ValueError):
    """Expected approval or promotion failure."""


def _now(value: str | None = None) -> dt.datetime:
    if value:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc).replace(microsecond=0)
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _rfc3339(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _candidate(candidate_dir: Path) -> dict[str, Any]:
    path = candidate_dir / "candidate.json"
    if not candidate_dir.is_dir() or not path.is_file():
        raise PromotionError("candidate directory or candidate.json is missing")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PromotionError(f"invalid candidate.json: {exc}") from exc
    contracts.validate_candidate(value)
    if candidate_dir.name != value.get("candidate_id"):
        raise PromotionError("candidate directory does not match candidate_id")
    return value


def _candidate_digest(candidate_dir: Path) -> str:
    return contracts.file_digest(candidate_dir / "candidate.json")


def _validation_digest(candidate_dir: Path) -> str:
    path = candidate_dir / "validation.md"
    if not path.is_file():
        raise PromotionError("validation.md is missing")
    return contracts.file_digest(path)


def approval_challenge(candidate_dir: Path, candidate: dict[str, Any] | None = None) -> str:
    """Return a deterministic 16-hex challenge for the current candidate."""
    candidate = candidate or _candidate(candidate_dir)
    validation_digest = _validation_digest(candidate_dir)
    payload = {
        "candidate_id": candidate["candidate_id"],
        "candidate_digest": _candidate_digest(candidate_dir),
        "validation_digest": validation_digest,
        "structure_signature": candidate.get("structure_signature", ""),
        "current_validation_count": candidate.get("current_validation_count", 0),
    }
    return contracts.sha256_digest(contracts.canonical_json(payload))[:16]


def _validation_threshold(candidate: dict[str, Any]) -> bool:
    if candidate.get("status") != "VALIDATING":
        return False
    if int(candidate.get("current_validation_count", 0)) < int(candidate.get("required_validation_count", 1)):
        return False
    if candidate.get("approval_ready") is not True:
        return False
    if candidate.get("validation_conflicts"):
        return False
    return True


def approval_preview(candidate_dir: Path) -> dict[str, Any]:
    candidate = _candidate(candidate_dir)
    challenge = approval_challenge(candidate_dir, candidate)
    eligible = _validation_threshold(candidate) and candidate.get("status") == "VALIDATING"
    return {
        "mode": "approval-preview",
        "candidate_id": candidate["candidate_id"],
        "candidate_status": candidate.get("status"),
        "validation_threshold_met": int(candidate.get("current_validation_count", 0)) >= int(candidate.get("required_validation_count", 1)),
        "approval_ready": candidate.get("approval_ready", False),
        "approval_challenge": challenge,
        "candidate_digest": _candidate_digest(candidate_dir),
        "validation_digest": _validation_digest(candidate_dir),
        "eligible": eligible,
        "writes": False,
    }


def _receipt(receipt_path: Path) -> dict[str, Any]:
    if not receipt_path.is_file():
        raise PromotionError("approval receipt must already exist")
    try:
        value = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PromotionError(f"invalid approval receipt: {exc}") from exc
    if not isinstance(value, dict):
        raise PromotionError("approval receipt must be a JSON object")
    required = ("approval_schema_version", "candidate_id", "decision", "explicit_user_approval", "approver", "approved_at", "approval_challenge", "candidate_digest", "validation_digest", "approved_scope", "reason")
    missing = [key for key in required if key not in value]
    if missing:
        raise PromotionError("approval receipt missing: " + ", ".join(missing))
    if value["approval_schema_version"] != 1:
        raise PromotionError("unsupported approval_schema_version")
    if value["decision"] not in RECEIPT_DECISIONS:
        raise PromotionError("invalid approval decision")
    if value["explicit_user_approval"] is not True:
        raise PromotionError("explicit_user_approval must be true")
    if not isinstance(value["approved_scope"], list) or not value["approved_scope"]:
        raise PromotionError("approved_scope must be a non-empty list")
    try:
        contracts.validate_rfc3339(value["approved_at"], "approved_at")
    except contracts.ContractError as exc:
        raise PromotionError(str(exc)) from exc
    contracts.validate_candidate_id(value["candidate_id"])
    if not re.fullmatch(r"[0-9a-f]{64}", value["candidate_digest"]):
        raise PromotionError("invalid candidate_digest")
    if not re.fullmatch(r"[0-9a-f]{64}", value["validation_digest"]):
        raise PromotionError("invalid validation_digest")
    return value


def _approval_lock(candidate_dir: Path, skill_root: Path, base_digest: str) -> dict[str, Any]:
    lock_path = candidate_dir / ".approval.lock"
    conflicts = skill_root / ".planning" / "conflicts"
    return contracts.acquire_workflow_lock(lock_path, f"candidates/{candidate_dir.name}/candidate.json", base_digest, "Codex", conflicts)


def apply_approval(candidate_dir: Path, skill_root: Path, receipt_path: Path, *, now: dt.datetime | None = None) -> dict[str, Any]:
    candidate = _candidate(candidate_dir)
    preview = approval_preview(candidate_dir)
    receipt = _receipt(receipt_path)
    if receipt["candidate_id"] != candidate["candidate_id"]:
        raise PromotionError("candidate_id mismatch")
    if receipt["approval_challenge"] != preview["approval_challenge"]:
        raise PromotionError("approval challenge mismatch")
    if receipt["candidate_digest"] != preview["candidate_digest"]:
        raise PromotionError("candidate digest mismatch")
    if receipt["validation_digest"] != preview["validation_digest"]:
        raise PromotionError("validation digest mismatch")
    if "proposed_change" not in receipt["approved_scope"]:
        raise PromotionError("approval scope does not include proposed_change")
    if candidate.get("risk_level") == "CRITICAL" and not any(str(item).lower() in {"security", "audit", "permission", "安全", "审计", "权限"} for item in receipt["approved_scope"]):
        raise PromotionError("CRITICAL approval scope must include security, audit, or permission")
    if receipt["decision"] == "APPROVE" and not _validation_threshold(candidate):
        raise PromotionError("candidate validation threshold or approval_ready is not satisfied")
    before = _candidate_digest(candidate_dir)
    lock = _approval_lock(candidate_dir, skill_root, before)
    try:
        if _candidate_digest(candidate_dir) != before:
            raise PromotionError("candidate digest changed before approval")
        current = _candidate(candidate_dir)
        updated = copy.deepcopy(current)
        updated["approval"] = {
            "decision": receipt["decision"],
            "approver": receipt["approver"],
            "approved_at": receipt["approved_at"],
            "approval_challenge": receipt["approval_challenge"],
            "receipt_digest": contracts.file_digest(receipt_path),
            "candidate_digest": receipt["candidate_digest"],
            "validation_digest": receipt["validation_digest"],
            "approved_scope": copy.deepcopy(receipt["approved_scope"]),
        }
        updated["status"] = "APPROVED" if receipt["decision"] == "APPROVE" else "REJECTED"
        updated["updated_at"] = _rfc3339(now or dt.datetime.now(dt.timezone.utc).replace(microsecond=0))
        # Bind the post-approval candidate content without introducing a
        # self-referential digest field.
        updated["approval"]["approved_candidate_digest"] = contracts.sha256_digest(contracts.canonical_json(updated))
        contracts.validate_candidate(updated)
        contracts.atomic_write_text(candidate_dir / "candidate.json", contracts.canonical_json(updated))
        contracts.atomic_write_text(candidate_dir / "CANDIDATE.md", _candidate_summary(updated))
        contracts.atomic_write_text(candidate_dir / "validation.md", _validation_summary(updated))
    finally:
        contracts.release_lock(candidate_dir / ".approval.lock", process_id=lock["process_id"], host_name=lock["host_name"])
    return {"mode": "approve", "candidate_id": updated["candidate_id"], "decision": receipt["decision"], "status": updated["status"], "writes": True}


def _candidate_summary(candidate: dict[str, Any]) -> str:
    approval = candidate.get("approval", {})
    return "\n".join([
        f"# Workflow Candidate {candidate['candidate_id']}", "",
        f"- Candidate type: {candidate.get('candidate_type', '—')}",
        f"- Risk: {candidate.get('risk_level', '—')}",
        f"- Status: {candidate.get('status', '—')}",
        f"- Validation count: {candidate.get('current_validation_count', 0)}/{candidate.get('required_validation_count', 1)}",
        f"- Approval decision: {approval.get('decision', 'PENDING')}", "",
        "Promotion remains explicit and transaction-gated.", "",
    ])


def _validation_summary(candidate: dict[str, Any]) -> str:
    return "\n".join([
        "# Validation", "",
        f"- Current validation count: {candidate.get('current_validation_count', 0)}",
        f"- Required validation count: {candidate.get('required_validation_count', 1)}",
        f"- Status: {candidate.get('status', '—')}",
        f"- Approval ready: {'YES' if candidate.get('approval_ready') else 'NO'}",
        f"- Approval decision: {candidate.get('approval', {}).get('decision', 'PENDING')}", "",
    ])


def _artifact_type(candidate: dict[str, Any]) -> str:
    if candidate.get("candidate_type") == "NEW_MODULE_CANDIDATE":
        return "workflow-module"
    return "task-template"


def _operation(candidate: dict[str, Any]) -> str:
    operation = candidate.get("proposed_change", {}).get("operation", "")
    return operation


def _semver_level(candidate: dict[str, Any]) -> tuple[str, str]:
    operation = _operation(candidate)
    if operation in {"ADD_TASK", "REMOVE_TASK", "CHANGE_DEPENDENCY", "CHANGE_COMPLETION_REQUIREMENT", "CHANGE_EVIDENCE_REQUIREMENT"}:
        return "MAJOR", "流程结构、依赖、完成条件或证据要求发生不兼容变化"
    if operation in {"CHANGE_ORDER", "CHANGE_OWNER", "UPDATE_KEYWORDS", "UPDATE_EXCLUDE_KEYWORDS"}:
        return "MINOR", "增加兼容性流程信息或调整非契约性顺序/负责人"
    if operation in {"UPDATE_DOCUMENTATION"}:
        return "PATCH", "仅文档兼容修复"
    raise PromotionError("INCOMPLETE_PROMOTION_PLAN")


def _effective_risk(candidate: dict[str, Any], operation: str) -> tuple[str, str]:
    actual = "HIGH" if operation in {"ADD_TASK", "REMOVE_TASK", "CHANGE_DEPENDENCY", "CHANGE_COMPLETION_REQUIREMENT", "CHANGE_EVIDENCE_REQUIREMENT"} else ("MEDIUM" if operation in {"CHANGE_ORDER", "CHANGE_OWNER"} else "LOW")
    if candidate.get("candidate_type") in {"NEW_TEMPLATE_CANDIDATE", "NEW_MODULE_CANDIDATE"}:
        actual = "HIGH"
    declared = str(candidate.get("risk_level", "LOW"))
    effective = max((declared, actual), key=lambda item: RISK_ORDER.get(item, -1))
    reason = "" if effective == declared else f"结构化操作要求风险至少为 {actual}，已从 {declared} 升级"
    return effective, reason


def _rows_from_artifact(text: str) -> list[dict[str, str]]:
    return contracts.parse_markdown_table(text, TASK_COLUMNS)


def _render_table(rows: list[dict[str, str]]) -> str:
    lines = ["| " + " | ".join(TASK_COLUMNS) + " |", "|" + "|".join("---" for _ in TASK_COLUMNS) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(row.get(column, "") for column in TASK_COLUMNS) + " |")
    return "\n".join(lines)


def _replace_task_table(text: str, rows: list[dict[str, str]]) -> str:
    lines = text.splitlines()
    header_index = next((i for i, line in enumerate(lines) if line.strip().startswith("| ID | 阶段/任务 | 是否必需")), -1)
    if header_index < 0:
        raise PromotionError("template/module task table is missing")
    end = header_index + 2
    while end < len(lines) and lines[end].strip().startswith("|"):
        end += 1
    replacement = _render_table(rows).splitlines()
    result = lines[:header_index] + replacement + lines[end:]
    return "\n".join(result).rstrip("\n") + "\n"


def _task_from_change(change: dict[str, Any]) -> dict[str, str]:
    task = change.get("task")
    if not isinstance(task, dict) or not task.get("ID") or not task.get("阶段/任务"):
        raise PromotionError("INCOMPLETE_PROMOTION_PLAN: structured task is missing")
    return {
        "ID": str(task.get("ID", "")),
        "阶段/任务": str(task.get("阶段/任务", "")),
        "是否必需": str(task.get("是否必需", "是")),
        "默认主责": str(task.get("默认主责", task.get("主责智能体", ""))),
        "前置条件": str(task.get("前置条件", "无")),
        "默认优先级": str(task.get("默认优先级", task.get("优先级", "P1"))),
        "主线": str(task.get("主线", "是")),
        "完成条件": str(task.get("完成条件", "完成且验证通过")),
        "证据要求": str(task.get("证据要求", task.get("完成证据", "执行记录/验证结果"))),
    }


def _apply_change(text: str, candidate: dict[str, Any]) -> str:
    operation = _operation(candidate)
    change = candidate.get("proposed_change", {})
    if operation == "UPDATE_DOCUMENTATION":
        documentation = change.get("documentation")
        if not isinstance(documentation, str) or not documentation.strip():
            raise PromotionError("INCOMPLETE_PROMOTION_PLAN")
        return text.rstrip() + "\n\n" + documentation.strip() + "\n"
    rows = _rows_from_artifact(text)
    task_id = str(change.get("task_id", ""))
    index = next((i for i, row in enumerate(rows) if row.get("ID") == task_id), -1)
    if operation == "ADD_TASK":
        new_task = _task_from_change(change)
        if any(row.get("ID") == new_task["ID"] for row in rows):
            raise PromotionError("target task ID already exists")
        rows.append(new_task)
    elif operation == "REMOVE_TASK":
        if index < 0:
            raise PromotionError("target task ID does not exist")
        rows.pop(index)
    elif operation in {"CHANGE_DEPENDENCY", "CHANGE_OWNER", "CHANGE_COMPLETION_REQUIREMENT", "CHANGE_EVIDENCE_REQUIREMENT"}:
        if index < 0:
            raise PromotionError("target task ID does not exist")
        field = {
            "CHANGE_DEPENDENCY": "前置条件",
            "CHANGE_OWNER": "默认主责",
            "CHANGE_COMPLETION_REQUIREMENT": "完成条件",
            "CHANGE_EVIDENCE_REQUIREMENT": "证据要求",
        }[operation]
        actual = change.get("change", {}).get("actual")
        if actual is None:
            actual = change.get(field) or change.get("new_value")
        if actual is None:
            raise PromotionError("INCOMPLETE_PROMOTION_PLAN")
        rows[index][field] = str(actual)
    elif operation == "CHANGE_ORDER":
        order = change.get("order") or change.get("change", {}).get("actual")
        if not isinstance(order, list) or set(order) != {row.get("ID") for row in rows}:
            raise PromotionError("INCOMPLETE_PROMOTION_PLAN: complete order list is required")
        by_id = {row["ID"]: row for row in rows}
        rows = [by_id[item] for item in order]
    elif operation in {"UPDATE_KEYWORDS", "UPDATE_EXCLUDE_KEYWORDS"}:
        raise PromotionError("registry-only keyword promotion requires explicit registry plan")
    else:
        raise PromotionError("INCOMPLETE_PROMOTION_PLAN")
    return _replace_task_table(text, rows)


def _update_metadata(text: str, artifact_id: str, version: str, artifact_type: str, lifecycle: str = "FORMAL") -> str:
    metadata = contracts.extract_machine_json(text, "template")
    metadata = copy.deepcopy(metadata)
    metadata["artifact_type"] = artifact_type
    metadata["workflow_schema_version"] = contracts.WORKFLOW_SCHEMA_VERSION
    metadata["version"] = version
    metadata["status"] = lifecycle
    metadata["template_id" if artifact_type == "task-template" else "module_id"] = artifact_id
    return contracts.replace_machine_json(text, "template", metadata)


def _artifact_info(skill_root: Path, candidate: dict[str, Any], target_id: str | None = None, target_type: str | None = None) -> dict[str, Any]:
    artifact_type = target_type or candidate.get("target_artifact_type") or _artifact_type(candidate)
    if artifact_type not in {"task-template", "workflow-module"}:
        raise PromotionError("invalid target artifact type")
    artifact_id = target_id or candidate.get("target_artifact_id") or (candidate.get("source_template_id") if artifact_type == "task-template" else "")
    if not artifact_id:
        raise PromotionError("INCOMPLETE_PROMOTION_PLAN: target artifact ID is required")
    contracts.validate_id(artifact_id, "target artifact id")
    registry = contracts.load_registry(skill_root)
    collection = registry["templates"] if artifact_type == "task-template" else registry["modules"]
    entry = next((item for item in collection if item.get("id") == artifact_id), None)
    if entry:
        current_version = entry["current_version"]
        path = contracts.artifact_path(skill_root, artifact_id, current_version, artifact_type)
        expected = entry.get("digest")
        actual = contracts.file_digest(path)
        if expected and expected != actual:
            raise PromotionError("target artifact digest mismatch")
        return {"artifact_type": artifact_type, "artifact_id": artifact_id, "current_version": current_version, "current_path": path, "entry": entry, "registry": registry}
    if candidate.get("candidate_type") not in {"NEW_TEMPLATE_CANDIDATE", "NEW_MODULE_CANDIDATE"}:
        raise PromotionError("target artifact does not exist")
    return {"artifact_type": artifact_type, "artifact_id": artifact_id, "current_version": None, "current_path": None, "entry": None, "registry": registry}


def _new_artifact_text(candidate: dict[str, Any], info: dict[str, Any], version: str) -> str:
    change = candidate.get("proposed_change", {})
    artifact_id = info["artifact_id"]
    artifact_type = info["artifact_type"]
    new_artifact = change.get("new_artifact")
    if not isinstance(new_artifact, dict):
        raise PromotionError("INCOMPLETE_PROMOTION_PLAN: new_artifact is required")
    description = str(new_artifact.get("description", artifact_id))
    rows = new_artifact.get("tasks")
    if not isinstance(rows, list) or not rows:
        raise PromotionError("INCOMPLETE_PROMOTION_PLAN: new_artifact.tasks is required")
    normalized = []
    for item in rows:
        normalized.append(_task_from_change({"task": item}))
    metadata = {
        "artifact_type": artifact_type,
        "description": description,
        "status": str(new_artifact.get("lifecycle", "FORMAL")),
        "version": version,
        "workflow_schema_version": contracts.WORKFLOW_SCHEMA_VERSION,
        "template_id" if artifact_type == "task-template" else "module_id": artifact_id,
    }
    return "\n".join([
        contracts.render_machine_block("template", metadata), "",
        f"# {description}", "", _render_table(normalized), "",
    ])


def _promotion_plan(candidate: dict[str, Any], skill_root: Path, target_id: str | None = None, target_type: str | None = None) -> dict[str, Any]:
    if candidate.get("status") != "APPROVED":
        raise PromotionError("candidate must be APPROVED before promotion")
    if candidate.get("candidate_type") not in PROMOTION_TYPES:
        raise PromotionError("candidate classification is not promotable")
    structure_errors = validator._structure_valid(candidate)
    if structure_errors:
        raise PromotionError("candidate structure changed: " + "; ".join(structure_errors))
    approval = candidate.get("approval", {})
    bound = approval.get("approved_candidate_digest")
    if bound:
        check = copy.deepcopy(candidate)
        check.setdefault("approval", {}).pop("approved_candidate_digest", None)
        if contracts.sha256_digest(contracts.canonical_json(check)) != bound:
            raise PromotionError("candidate changed after approval")
    operation = _operation(candidate)
    if operation not in OPERATIONS:
        raise PromotionError("INCOMPLETE_PROMOTION_PLAN")
    info = _artifact_info(skill_root, candidate, target_id, target_type)
    level, level_reason = _semver_level(candidate)
    effective_risk, risk_reason = _effective_risk(candidate, operation)
    current = info["current_version"]
    new_version = "1.0.0" if current is None else contracts.bump_semver(current, level)
    old_text = ""
    if info["current_path"]:
        old_text = info["current_path"].read_text(encoding="utf-8")
        new_text = _update_metadata(_apply_change(old_text, candidate), info["artifact_id"], new_version, info["artifact_type"])
    else:
        new_text = _new_artifact_text(candidate, info, new_version)
    new_digest = contracts.sha256_digest(new_text)
    if info["current_path"] and "templates/workflow/base/" in info["current_path"].as_posix():
        relative_dir = "base"
    else:
        relative_dir = "task-types" if info["artifact_type"] == "task-template" else "modules"
    relative_artifact = f"templates/workflow/{relative_dir}/{info['artifact_id']}/{new_version}.md"
    history_root = f"templates/workflow/history/{info['artifact_id']}/{new_version}"
    return {
        "candidate_id": candidate["candidate_id"],
        "candidate_type": candidate["candidate_type"],
        "artifact_type": info["artifact_type"],
        "artifact_id": info["artifact_id"],
        "previous_version": current,
        "previous_digest": contracts.file_digest(info["current_path"]) if info["current_path"] else None,
        "new_version": new_version,
        "new_digest": new_digest,
        "semver_level": level,
        "semver_reason": level_reason,
        "risk_level": effective_risk,
        "risk_escalation_reason": risk_reason,
        "operation": operation,
        "artifact_path": relative_artifact,
        "history_root": history_root,
        "registry": info["registry"],
        "new_text": new_text,
        "old_text": old_text,
    }


def promotion_preview(candidate_dir: Path, skill_root: Path, *, target_id: str | None = None, target_type: str | None = None) -> dict[str, Any]:
    candidate = _candidate(candidate_dir)
    plan = _promotion_plan(candidate, skill_root, target_id, target_type)
    return {
        "mode": "promotion-preview",
        "candidate_id": candidate["candidate_id"],
        "candidate_status": candidate["status"],
        "promotion_eligible": True,
        "artifact_type": plan["artifact_type"],
        "artifact_id": plan["artifact_id"],
        "previous_version": plan["previous_version"],
        "previous_digest": plan["previous_digest"],
        "new_version": plan["new_version"],
        "new_digest": plan["new_digest"],
        "semver_level": plan["semver_level"],
        "semver_reason": plan["semver_reason"],
        "risk_level": plan["risk_level"],
        "risk_escalation_reason": plan["risk_escalation_reason"],
        "operation": plan["operation"],
        "artifact_path": plan["artifact_path"],
        "history_root": plan["history_root"],
        "rollback_target": plan["previous_version"] or "none",
        "writes": False,
    }


def _registry_update(registry: dict[str, Any], plan: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    updated = copy.deepcopy(registry)
    collection = updated["templates"] if plan["artifact_type"] == "task-template" else updated["modules"]
    entry = next((item for item in collection if item.get("id") == plan["artifact_id"]), None)
    if entry is None:
        entry = {
            "id": plan["artifact_id"], "name": plan["artifact_id"], "keywords": [], "exclude_keywords": [],
            "lifecycle": "FORMAL", "lifecycle_status": "FORMAL", "usage_count": 0, "success_count": 0,
        }
        collection.append(entry)
    else:
        if entry.get("current_version") != plan["previous_version"] or entry.get("digest") != plan["previous_digest"]:
            raise PromotionError("registry target changed since promotion preview")
    entry["current_version"] = plan["new_version"]
    entry["digest"] = plan["new_digest"]
    entry["lifecycle"] = "FORMAL"
    entry["lifecycle_status"] = "FORMAL"
    versions = list(entry.get("versions", []))
    if plan["previous_version"] and not any(item.get("version") == plan["previous_version"] for item in versions):
        versions.append({"version": plan["previous_version"], "digest": plan["previous_digest"]})
    versions.append({"version": plan["new_version"], "digest": plan["new_digest"], "candidate_id": candidate["candidate_id"], "lifecycle": "FORMAL"})
    entry["versions"] = versions
    return updated


def _history_files(skill_root: Path, plan: dict[str, Any], candidate: dict[str, Any]) -> dict[str, str]:
    root = plan["history_root"]
    approval = candidate.get("approval", {})
    promotion = {
        "promotion_schema_version": 1,
        "candidate_id": candidate["candidate_id"],
        "artifact_type": plan["artifact_type"],
        "artifact_id": plan["artifact_id"],
        "previous_version": plan["previous_version"],
        "previous_digest": plan["previous_digest"],
        "new_version": plan["new_version"],
        "new_digest": plan["new_digest"],
        "operation": plan["operation"],
        "risk_level": plan["risk_level"],
        "approval": {"approver": approval.get("approver"), "approved_at": approval.get("approved_at"), "receipt_digest": approval.get("receipt_digest")},
    }
    return {
        f"{root}/PROMOTION.md": f"# Promotion\n\n- Candidate: {candidate['candidate_id']}\n- Artifact: {plan['artifact_type']} `{plan['artifact_id']}`\n- Version: {plan['previous_version'] or 'none'} → {plan['new_version']}\n- Operation: {plan['operation']}\n",
        f"{root}/promotion.json": contracts.canonical_json(promotion),
        f"{root}/MIGRATION.md": "# Migration\n\n旧项目不自动迁移；新项目默认使用新 current_version。\n",
        f"{root}/ROLLBACK.md": f"# Rollback\n\n回滚目标版本：{plan['previous_version'] or '删除新资产并恢复注册表旧状态'}。\n",
    }


def _promotion_lock(skill_root: Path, candidate: dict[str, Any], plan: dict[str, Any], registry_digest: str) -> tuple[Path, dict[str, Any]]:
    workflow_root = skill_root / "templates" / "workflow"
    lock_path = workflow_root / ".promotion.lock"
    conflicts = skill_root / ".planning" / "conflicts"
    lock = contracts.acquire_workflow_lock(lock_path, "workflow/template_registry.json", registry_digest, "Codex", conflicts)
    lock["candidate_id"] = candidate["candidate_id"]
    lock["candidate_base_digest"] = _candidate_digest(skill_root / "templates" / "workflow" / "candidates" / candidate["candidate_id"])
    lock["registry_base_digest"] = registry_digest
    lock["target_artifact_digest"] = plan["previous_digest"] or ""
    contracts.atomic_write_text(lock_path, contracts.canonical_json(lock))
    return lock_path, lock


def _apply_transaction(candidate_dir: Path, skill_root: Path, candidate: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    workflow_root = skill_root / "templates" / "workflow"
    registry_path = workflow_root / "template_registry.json"
    projection_path = workflow_root / "00_TEMPLATE_REGISTRY.md"
    registry_before = contracts.load_registry(skill_root)
    registry_digest = contracts.file_digest(registry_path)
    plan_registry = _registry_update(registry_before, plan, candidate)
    contracts.validate_registry(plan_registry)
    projection = contracts.registry_markdown(plan_registry)
    artifact_path = skill_root / plan["artifact_path"]
    history = _history_files(skill_root, plan, candidate)
    target_files: dict[Path, bytes | None] = {
        artifact_path: artifact_path.read_bytes() if artifact_path.exists() else None,
        registry_path: registry_path.read_bytes(),
        projection_path: projection_path.read_bytes() if projection_path.exists() else None,
        candidate_dir / "candidate.json": (candidate_dir / "candidate.json").read_bytes(),
        candidate_dir / "CANDIDATE.md": (candidate_dir / "CANDIDATE.md").read_bytes() if (candidate_dir / "CANDIDATE.md").exists() else None,
        candidate_dir / "validation.md": (candidate_dir / "validation.md").read_bytes() if (candidate_dir / "validation.md").exists() else None,
    }
    for relative in history:
        history_path = skill_root / relative
        target_files[history_path] = history_path.read_bytes() if history_path.exists() else None
    temp_root = workflow_root / ".promotion-tmp" / candidate["candidate_id"]
    if temp_root.exists():
        shutil.rmtree(temp_root)
    lock_path, lock = _promotion_lock(skill_root, candidate, plan, registry_digest)
    try:
        if contracts.file_digest(registry_path) != registry_digest:
            raise PromotionError("registry digest changed before promotion")
        temp_root.mkdir(parents=True, exist_ok=True)
        contracts.atomic_write_text(temp_root / "artifact.md", plan["new_text"])
        contracts.validate_template_metadata(contracts.extract_machine_json(plan["new_text"], "template"))
        contracts.atomic_write_text(temp_root / "registry.json", contracts.canonical_json(plan_registry))
        contracts.atomic_write_text(temp_root / "registry.md", projection)
        for relative, text in history.items():
            target = temp_root / Path(relative).as_posix().removeprefix("templates/workflow/")
            contracts.atomic_write_text(target, text)
        # All generated files are valid before any formal file is touched.
        if contracts.file_digest(temp_root / "artifact.md") != plan["new_digest"]:
            raise PromotionError("new artifact digest mismatch")
        contracts.atomic_write_text(artifact_path, plan["new_text"])
        contracts.atomic_write_text(registry_path, contracts.canonical_json(plan_registry))
        contracts.atomic_write_text(projection_path, projection)
        for relative, text in history.items():
            contracts.atomic_write_text(skill_root / relative, text)
        updated_candidate = copy.deepcopy(candidate)
        updated_candidate["status"] = "APPLIED"
        updated_candidate["application"] = {
            "applied_at": _rfc3339(dt.datetime.now(dt.timezone.utc).replace(microsecond=0)),
            "artifact_type": plan["artifact_type"],
            "artifact_id": plan["artifact_id"],
            "previous_version": plan["previous_version"],
            "new_version": plan["new_version"],
            "new_digest": plan["new_digest"],
            "registry_digest": contracts.file_digest(registry_path),
            "promotion_record": f"{plan['history_root']}/PROMOTION.md",
            "rollback_record": f"{plan['history_root']}/ROLLBACK.md",
        }
        updated_candidate["updated_at"] = updated_candidate["application"]["applied_at"]
        contracts.validate_candidate(updated_candidate)
        contracts.atomic_write_text(candidate_dir / "candidate.json", contracts.canonical_json(updated_candidate))
        contracts.atomic_write_text(candidate_dir / "CANDIDATE.md", _candidate_summary(updated_candidate))
        contracts.atomic_write_text(candidate_dir / "validation.md", _validation_summary(updated_candidate))
        return {"mode": "promotion-apply", "candidate_id": candidate["candidate_id"], "status": "APPLIED", "artifact_type": plan["artifact_type"], "artifact_id": plan["artifact_id"], "new_version": plan["new_version"], "new_digest": plan["new_digest"], "writes": True}
    except Exception:
        for path, content in target_files.items():
            if content is None:
                if path.exists():
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
        raise
    finally:
        if temp_root.exists():
            shutil.rmtree(temp_root)
        contracts.release_lock(lock_path, process_id=lock["process_id"], host_name=lock["host_name"])


def promotion_apply(candidate_dir: Path, skill_root: Path, *, target_id: str | None = None, target_type: str | None = None) -> dict[str, Any]:
    candidate = _candidate(candidate_dir)
    plan = _promotion_plan(candidate, skill_root, target_id, target_type)
    return _apply_transaction(candidate_dir, skill_root, candidate, plan)


def _summary(result: dict[str, Any]) -> str:
    return "\n".join([
        f"Mode: {result.get('mode')}",
        f"Candidate: {result.get('candidate_id', 'unknown')}",
        f"Status: {result.get('status', result.get('candidate_status', 'unknown'))}",
        f"Artifact: {result.get('artifact_type', '—')} {result.get('artifact_id', '—')}",
        f"Version: {result.get('new_version', '—')}",
        f"Eligible: {'YES' if result.get('eligible', result.get('promotion_eligible', False)) else 'NO'}",
        f"Writes: {'YES' if result.get('writes') else 'NO'}",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--approval-preview", action="store_true")
    modes.add_argument("--approve", action="store_true")
    modes.add_argument("--promotion-preview", action="store_true")
    modes.add_argument("--promotion-apply", action="store_true")
    parser.add_argument("--candidate-dir", required=True)
    parser.add_argument("--skill-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--approval-receipt")
    parser.add_argument("--target-id")
    parser.add_argument("--target-type", choices=("task-template", "workflow-module"))
    parser.add_argument("--format", choices=("json", "summary"), default="json")
    args = parser.parse_args(argv)
    try:
        candidate_dir = Path(args.candidate_dir).expanduser()
        skill_root = Path(args.skill_root).expanduser()
        if args.approval_preview:
            result = approval_preview(candidate_dir)
        elif args.approve:
            if not args.approval_receipt:
                raise PromotionError("--approval-receipt is required with --approve")
            result = apply_approval(candidate_dir, skill_root, Path(args.approval_receipt).expanduser())
        elif args.promotion_preview:
            result = promotion_preview(candidate_dir, skill_root, target_id=args.target_id, target_type=args.target_type)
        else:
            result = promotion_apply(candidate_dir, skill_root, target_id=args.target_id, target_type=args.target_type)
    except (PromotionError, contracts.ContractError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(_summary(result) if args.format == "summary" else json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
