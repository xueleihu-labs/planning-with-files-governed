#!/usr/bin/env python3
# VERSION source: ../VERSION
"""Machine contracts for planning-with-files v0.8.0 workflow artifacts."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import re
import socket
import subprocess
from pathlib import Path
from typing import Any, Callable

import planning_layout


WORKFLOW_SCHEMA_VERSION = 1
REGISTRY_SCHEMA_VERSION = 1
CANDIDATE_SCHEMA_VERSION = 1
LOCK_SCHEMA_VERSION = 1
CONFLICT_SCHEMA_VERSION = 1

ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TASK_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
CANDIDATE_ID_RE = re.compile(r"^cand-[0-9]{8}-[0-9a-f]{12}$")
SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
RFC3339_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")

BLOCKS = {
    "workflow": ("<!-- BEGIN WORKFLOW METADATA -->", "<!-- END WORKFLOW METADATA -->"),
    "template": ("<!-- BEGIN TEMPLATE METADATA -->", "<!-- END TEMPLATE METADATA -->"),
    "conflict": ("<!-- BEGIN CONFLICT METADATA -->", "<!-- END CONFLICT METADATA -->"),
}

TEMPLATE_LIFECYCLES = {"FORMAL", "EXPERIMENTAL", "DEPRECATED"}
CANDIDATE_STATUSES = {"PROPOSED", "VALIDATING", "APPROVED", "APPLIED", "REJECTED", "SUPERSEDED"}
CONFLICT_STATUSES = {"OPEN", "RESOLVED", "DISCARDED", "SUPERSEDED"}
ARTIFACT_TYPES = {"task-template", "workflow-module"}
CHECKLIST_NAME = "WORKFLOW_CHECKLIST.md"


class ContractError(ValueError):
    """Raised when a workflow contract is malformed or unsupported."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, separators=(",", ": ")) + "\n"


def normalize_text(text: str) -> str:
    """Normalize text exactly as the digest contract specifies."""
    if text.startswith("\ufeff"):
        text = text[1:]
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.rstrip("\n") + "\n"


def normalized_bytes(value: str | bytes) -> bytes:
    if isinstance(value, bytes):
        text = value.decode("utf-8-sig")
    else:
        text = value
    return normalize_text(text).encode("utf-8")


def sha256_digest(value: str | bytes) -> str:
    return hashlib.sha256(normalized_bytes(value)).hexdigest()


def file_digest(path: Path) -> str:
    return sha256_digest(path.read_bytes())


def validate_id(value: str, label: str = "id") -> None:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ContractError(f"invalid {label}: {value!r}")


def validate_task_id(value: str) -> None:
    if not isinstance(value, str) or not TASK_ID_RE.fullmatch(value):
        raise ContractError(f"invalid task_id: {value!r}")


def validate_candidate_id(value: str) -> None:
    if not isinstance(value, str) or not CANDIDATE_ID_RE.fullmatch(value):
        raise ContractError(f"invalid candidate_id: {value!r}")


def validate_semver(value: str, label: str = "version") -> None:
    if not isinstance(value, str) or not SEMVER_RE.fullmatch(value):
        raise ContractError(f"invalid {label}: {value!r}")


def validate_rfc3339(value: str, label: str = "timestamp") -> None:
    if not isinstance(value, str) or not RFC3339_RE.fullmatch(value):
        raise ContractError(f"invalid {label}: {value!r}")
    try:
        dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"invalid {label}: {value!r}") from exc


def utc_filename_timestamp(now: dt.datetime | None = None) -> str:
    current = now or dt.datetime.now(dt.timezone.utc)
    return current.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def extract_machine_json(text: str, block_type: str) -> dict[str, Any]:
    if block_type not in BLOCKS:
        raise ContractError(f"unknown machine block: {block_type}")
    begin, end = BLOCKS[block_type]
    start = text.find(begin)
    finish = text.find(end)
    if start < 0 or finish < start:
        raise ContractError(f"missing or malformed {block_type} machine block")
    body = text[start + len(begin):finish].strip()
    match = re.fullmatch(r"```json\s*\n(.*?)\n```", body, re.DOTALL)
    if not match:
        raise ContractError(f"{block_type} machine block must contain a JSON fence")
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ContractError(f"invalid JSON in {block_type} machine block: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{block_type} machine block must contain a JSON object")
    return value


def extract_machine_json_lenient(text: str, block_type: str) -> dict[str, Any]:
    try:
        return extract_machine_json(text, block_type)
    except ContractError:
        if block_type not in BLOCKS:
            raise
        begin, _end = BLOCKS[block_type]
        start = text.find(begin)
        if start < 0:
            raise
        match = re.search(r"```json\s*\n(.*?)\n```", text[start + len(begin):], re.DOTALL)
        if not match:
            raise
        try:
            value = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise ContractError(f"invalid JSON in damaged {block_type} machine block: {exc}") from exc
        if not isinstance(value, dict):
            raise ContractError(f"damaged {block_type} machine block must contain a JSON object")
        return value


def render_machine_block(block_type: str, value: dict[str, Any]) -> str:
    if block_type not in BLOCKS:
        raise ContractError(f"unknown machine block: {block_type}")
    begin, end = BLOCKS[block_type]
    return f"{begin}\n```json\n{canonical_json(value)}```\n{end}"


def replace_machine_json(text: str, block_type: str, value: dict[str, Any]) -> str:
    if block_type not in BLOCKS:
        raise ContractError(f"unknown machine block: {block_type}")
    begin, end = BLOCKS[block_type]
    start = text.find(begin)
    finish = text.find(end)
    rendered = render_machine_block(block_type, value)
    if start >= 0 and finish < start:
        fence_end = text.find("\n```", start + len(begin))
        if fence_end >= 0:
            suffix_start = fence_end + len("\n```")
            next_line = text.find("\n", suffix_start)
            if next_line >= 0:
                suffix_start = next_line + 1
            return text[:start] + rendered + "\n" + text[suffix_start:]
    if start < 0 or finish < start:
        separator = "\n\n" if text and not text.endswith("\n\n") else ""
        return normalize_text(text) + separator + rendered + "\n"
    return text[:start] + rendered + text[finish + len(end):]


def merge_json(original: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(original)
    merged.update(copy.deepcopy(updates))
    return merged


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(normalize_text(text))
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def merge_json_file(path: Path, updates: dict[str, Any], validator: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    original: dict[str, Any] = {}
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise ContractError(f"JSON root must be an object: {path}")
        original = value
    merged = merge_json(original, updates)
    if validator:
        validator(merged)
    if path.exists() and canonical_json(original) == canonical_json(merged):
        return merged
    atomic_write_text(path, canonical_json(merged))
    return merged


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    return value


def validate_workflow_metadata(value: dict[str, Any]) -> None:
    _require_object(value, "workflow metadata")
    if value.get("workflow_schema_version") != WORKFLOW_SCHEMA_VERSION:
        raise ContractError("unsupported workflow_schema_version")
    for key in ("project_id", "checklist_version", "current_phase", "overall_status", "owner_agent", "last_updated_at"):
        if key not in value:
            raise ContractError(f"workflow metadata missing {key}")
    validate_semver(value["checklist_version"], "checklist_version")
    validate_rfc3339(value["last_updated_at"], "last_updated_at")
    template = _require_object(value.get("template"), "template binding")
    validate_id(template.get("template_id", ""), "template_id")
    validate_semver(template.get("template_version", ""), "template_version")
    if not re.fullmatch(r"[0-9a-f]{64}", template.get("template_digest", "")):
        raise ContractError("invalid template_digest")
    modules = value.get("modules", [])
    if not isinstance(modules, list):
        raise ContractError("modules must be a list")
    for module in modules:
        item = _require_object(module, "module binding")
        validate_id(item.get("module_id", ""), "module_id")
        validate_semver(item.get("module_version", ""), "module_version")
        if not re.fullmatch(r"[0-9a-f]{64}", item.get("module_digest", "")):
            raise ContractError("invalid module_digest")


def parse_markdown_table(text: str, required_columns: list[str]) -> list[dict[str, str]]:
    lines = [line.strip() for line in text.splitlines() if line.strip().startswith("|")]
    rows: list[dict[str, str]] = []
    for index in range(len(lines) - 1):
        header = [cell.strip() for cell in lines[index].strip("|").split("|")]
        divider = [cell.strip() for cell in lines[index + 1].strip("|").split("|")]
        if not all(column in header for column in required_columns):
            continue
        if not all(set(cell) <= {"-", ":", " "} for cell in divider):
            continue
        for raw in lines[index + 2:]:
            cells = [cell.strip() for cell in raw.strip("|").split("|")]
            if len(cells) != len(header) or set(cells) <= {"-", ":", " "}:
                break
            rows.append(dict(zip(header, cells)))
        break
    return rows


def workflow_root(skill_root: Path) -> Path:
    return skill_root / "templates" / "workflow"


def load_registry(skill_root: Path) -> dict[str, Any]:
    path = workflow_root(skill_root) / "template_registry.json"
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    validate_registry(value)
    return value


def artifact_path(skill_root: Path, artifact_id: str, version: str, artifact_type: str) -> Path:
    if artifact_type == "task-template":
        candidates = [workflow_root(skill_root) / "base" / artifact_id / f"{version}.md", workflow_root(skill_root) / "task-types" / artifact_id / f"{version}.md"]
    else:
        candidates = [workflow_root(skill_root) / "modules" / artifact_id / f"{version}.md"]
    for path in candidates:
        if path.is_file():
            return path
    raise ContractError(f"workflow artifact not found: {artifact_type} {artifact_id}@{version}")


def select_template(skill_root: Path, task_text: str, explicit_id: str | None = None) -> dict[str, Any]:
    from workflow_template_matcher import select_template as deterministic_select

    return deterministic_select(skill_root, Path.cwd(), task_text, explicit_id)


def template_binding(skill_root: Path, selection: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], Path]:
    template_path = artifact_path(skill_root, selection["template_id"], selection["template_version"], "task-template")
    template_metadata = extract_machine_json(template_path.read_text(encoding="utf-8"), "template")
    validate_template_metadata(template_metadata)
    template_digest = file_digest(template_path)
    registry = load_registry(skill_root)
    registry_item = next(item for item in registry["templates"] if item["id"] == selection["template_id"])
    modules: list[dict[str, Any]] = []
    for module_id in template_metadata.get("module_ids", []):
        module_item = next(item for item in registry["modules"] if item["id"] == module_id)
        module_path = artifact_path(skill_root, module_id, module_item["current_version"], "workflow-module")
        module_metadata = extract_machine_json(module_path.read_text(encoding="utf-8"), "template")
        validate_template_metadata(module_metadata)
        modules.append({"module_id": module_id, "module_version": module_item["current_version"], "module_digest": file_digest(module_path)})
    binding = {"template_id": registry_item["id"], "template_version": selection["template_version"], "template_digest": template_digest}
    return binding, modules, template_path


def initial_workflow_metadata(project_id: str, template: dict[str, Any], modules: list[dict[str, Any]], selection: dict[str, Any], owner_agent: str = "", now: dt.datetime | None = None) -> dict[str, Any]:
    current = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()
    template_match = {key: selection[key] for key in ("template_id", "template_version", "template_digest", "match_method", "confidence", "matched_signals", "excluded_templates", "fallback_used", "reason") if key in selection}
    if "reason" not in template_match and "selection_reason" in selection:
        template_match["reason"] = selection["selection_reason"]
    return {
        "workflow_schema_version": WORKFLOW_SCHEMA_VERSION,
        "project_id": project_id,
        "checklist_version": "1.0.0",
        "template": template,
        "modules": modules,
        "template_match": template_match,
        "current_phase": "P01",
        "overall_status": "未开始",
        "owner_agent": owner_agent,
        "last_updated_at": current,
        "recommended_next_task": "P01",
    }


def checklist_from_template(project_id: str, template_path: Path, template: dict[str, Any], modules: list[dict[str, Any]], selection: dict[str, Any], owner_agent: str = "", now: dt.datetime | None = None) -> str:
    metadata = initial_workflow_metadata(project_id, template, modules, selection, owner_agent, now)
    template_text = template_path.read_text(encoding="utf-8")
    source_rows = parse_markdown_table(template_text, ["ID", "阶段/任务", "是否必需", "默认主责", "前置条件", "默认优先级", "主线", "完成条件", "证据要求"])
    lines = [
        f"# Workflow Checklist: {project_id}",
        "",
        render_machine_block("workflow", metadata),
        "",
        "## 工作流任务清单",
        "",
        "| ID | 阶段/任务 | 主责智能体 | 前置条件 | 优先级 | 主线 | 状态 | 核验状态 | 完成证据 | 阻塞/备注 | 下一步 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for index, row in enumerate(source_rows):
        task_id = row.get("ID", f"P{index + 1:02d}")
        next_task = source_rows[index + 1].get("ID", "验收/封板") if index + 1 < len(source_rows) else "验收/封板"
        lines.append(f"| {task_id} | {row.get('阶段/任务', '')} | {row.get('默认主责', '')} | {row.get('前置条件', '无')} | {row.get('默认优先级', 'P1')} | {row.get('主线', '是')} | 未开始 | 未核验 | — | — | {next_task} |")
    lines.extend(["", "## 变更历史", "", "| 时间 | 变更类型 | 涉及ID | 变更内容 | 原因 | 影响范围 | 执行者 |", "|---|---|---|---|---|---|---|", "| — | 初始化 | — | 从模板实例化 | 初始基线 | 全部任务 | planning-with-files |", ""])
    return "\n".join(lines)


def adopt_with_explicit_evidence(checklist_text: str, project_root: Path, source_names: tuple[str, ...] = ("00_PROJECT_INDEX.md", "1_master_plan.md", "2_execution_log.md", "3_status_update.md", "4_handoff.md", "5_audit.md", "README.md")) -> str:
    corpus_parts = []
    resolved = planning_layout.resolve_layout(project_root, require=False)
    for name in source_names:
        path = resolved.path(name) if resolved is not None and name in planning_layout.PLANNING_DOCUMENTS else project_root / name
        if path.exists():
            corpus_parts.append(path.read_text(encoding="utf-8", errors="replace"))
    corpus = "\n".join(corpus_parts)
    lines = checklist_text.splitlines()
    header_index = next((index for index, line in enumerate(lines) if "| ID | 阶段/任务 | 主责智能体 |" in line), -1)
    if header_index < 0:
        return checklist_text
    for index in range(header_index + 2, len(lines)):
        line = lines[index]
        if not line.startswith("|") or line.startswith("|---"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 11:
            continue
        task_name = cells[1]
        if not task_name or task_name not in corpus:
            continue
        position = corpus.find(task_name)
        window = corpus[max(0, position - 300):position + 300]
        if not re.search(r"已完成|完成|PASS", window):
            continue
        paths = re.findall(r"(?:[A-Za-z0-9_.\-/]+\.(?:md|py|json|txt|yaml|yml|sh))", window)
        evidence = next((candidate for candidate in paths if (project_root / candidate).is_file()), None)
        if evidence is None and resolved is not None:
            evidence = next((candidate for candidate in paths if (resolved.planning_dir / candidate).is_file()), None)
        if not evidence:
            continue
        cells[6] = "已完成"
        cells[7] = "已核验"
        cells[8] = evidence
        lines[index] = "| " + " | ".join(cells) + " |"
    return "\n".join(lines) + ("\n" if checklist_text.endswith("\n") else "")


def checklist_tasks(text: str) -> list[dict[str, str]]:
    return parse_markdown_table(text, ["ID", "阶段/任务", "状态", "核验状态", "下一步"])


def validate_checklist_text(text: str) -> dict[str, Any]:
    metadata = extract_machine_json(text, "workflow")
    validate_workflow_metadata(metadata)
    tasks = checklist_tasks(text)
    ids: set[str] = set()
    for task in tasks:
        validate_task_id(task["ID"])
        if task["ID"] in ids:
            raise ContractError(f"duplicate task_id: {task['ID']}")
        ids.add(task["ID"])
    return metadata


def checklist_summary(text: str) -> dict[str, Any]:
    metadata = validate_checklist_text(text)
    tasks = checklist_tasks(text)
    completed = [task for task in tasks if task.get("状态") == "已完成"]
    active = [task for task in tasks if task.get("状态") == "进行中" and task.get("主线", "是") == "是"]
    blocked = [task for task in tasks if task.get("状态") == "阻塞"]
    recommended = metadata.get("recommended_next_task") or (active[0]["ID"] if active else (tasks[0]["ID"] if tasks else "验收/封板"))
    return {"metadata": metadata, "tasks": tasks, "completed": completed, "active": active, "blocked": blocked, "recommended_next_task": recommended}


def workflow_integrity_errors(text: str) -> list[str]:
    errors: list[str] = []
    try:
        metadata = validate_checklist_text(text)
    except ContractError as exc:
        return [str(exc)]
    tasks = checklist_tasks(text)
    ids = {task["ID"] for task in tasks}
    valid_statuses = {"未开始", "进行中", "阻塞", "已完成", "已跳过", "已废弃"}
    valid_verification = {"未核验", "已核验", "待补证据", "不适用"}
    for task in tasks:
        if task.get("状态") not in valid_statuses:
            errors.append(f"{task['ID']}: invalid status")
        if task.get("核验状态") not in valid_verification:
            errors.append(f"{task['ID']}: invalid verification status")
        if task.get("状态") == "已完成" and task.get("核验状态") not in {"已核验", "不适用"}:
            errors.append(f"{task['ID']}: completed task lacks verified evidence")
        dependency = task.get("前置条件", "")
        for dependency_id in re.findall(r"\b[A-Za-z][A-Za-z0-9_-]*\b", dependency):
            if dependency_id in ids and next(item for item in tasks if item["ID"] == dependency_id).get("状态") not in {"已完成", "已跳过", "已废弃"} and task.get("状态") == "已完成":
                errors.append(f"{task['ID']}: dependency {dependency_id} incomplete")
        if task.get("状态") == "阻塞" and not task.get("阻塞/备注", "").strip("—- "):
            errors.append(f"{task['ID']}: blocked task lacks reason")
    if metadata.get("recommended_next_task") not in ids and metadata.get("recommended_next_task") != "验收/封板":
        errors.append("recommended_next_task does not exist")
    return errors


def acquire_workflow_lock(lock_path: Path, target_file: str, base_digest: str, agent_id: str, conflicts_dir: Path) -> dict[str, Any]:
    if lock_path.exists():
        current = json.loads(lock_path.read_text(encoding="utf-8-sig"))
        stale, reason = lock_is_stale(current)
        if not stale:
            target_path = lock_path.parent.parent / current["target_file"]
            current_digest = file_digest(target_path) if target_path.is_file() else current["base_digest"]
            write_conflict_report(conflicts_dir, {
                "conflict_id": f"conflict-{utc_filename_timestamp()}",
                "target_file": current["target_file"],
                "base_digest": current["base_digest"],
                "current_digest": current_digest,
                "conflict_reason": reason,
                "affected_task_ids": [],
                "recommended_handling": "等待锁持有者完成，或人工核对后合并",
                "lock_owner": current["lock_owner"],
                "agent_id": current["agent_id"],
                "process_id": current["process_id"],
                "host_name": current["host_name"],
            }, dt.datetime.now(dt.timezone.utc))
            if "cross-host" in reason:
                write_stale_lock_recovery_report(conflicts_dir, current, reason)
            raise ContractError(f"workflow lock unavailable: {reason}")
        write_stale_lock_recovery_report(conflicts_dir, current, reason)
        lock_path.unlink()
    lock = new_lock(target_file, base_digest, agent_id)
    write_lock(lock_path, lock)
    return lock


def validate_template_metadata(value: dict[str, Any]) -> None:
    _require_object(value, "template metadata")
    if value.get("artifact_type") not in ARTIFACT_TYPES:
        raise ContractError("artifact_type must be task-template or workflow-module")
    if value.get("workflow_schema_version") != WORKFLOW_SCHEMA_VERSION:
        raise ContractError("unsupported workflow_schema_version")
    validate_id(value.get("template_id") or value.get("module_id", ""), "template/module id")
    validate_semver(value.get("version", ""), "version")
    if value.get("status") not in TEMPLATE_LIFECYCLES:
        raise ContractError("invalid template lifecycle")


def validate_candidate(value: dict[str, Any]) -> None:
    _require_object(value, "candidate")
    if value.get("candidate_schema_version") != CANDIDATE_SCHEMA_VERSION:
        raise ContractError("unsupported candidate_schema_version")
    validate_candidate_id(value.get("candidate_id", ""))
    if value.get("status") not in CANDIDATE_STATUSES:
        raise ContractError("invalid candidate status")
    validate_id(value.get("source_template_id", ""), "source_template_id")
    validate_semver(value.get("source_template_version", ""), "source_template_version")
    for key in ("created_at", "updated_at"):
        validate_rfc3339(value.get(key, ""), key)


def validate_registry(value: dict[str, Any]) -> None:
    _require_object(value, "registry")
    if value.get("registry_schema_version") != REGISTRY_SCHEMA_VERSION:
        raise ContractError("unsupported registry_schema_version")
    for item in value.get("templates", []) + value.get("modules", []):
        _require_object(item, "registry entry")
        validate_id(item.get("id", ""), "registry id")
        validate_semver(item.get("current_version", ""), "current_version")
        if item.get("lifecycle") not in TEMPLATE_LIFECYCLES:
            raise ContractError("invalid registry lifecycle")


def validate_lock(value: dict[str, Any]) -> None:
    _require_object(value, "lock")
    if value.get("lock_schema_version") != LOCK_SCHEMA_VERSION:
        raise ContractError("unsupported lock_schema_version")
    for key in ("lock_owner", "agent_id", "host_name", "target_file", "created_at", "heartbeat_at"):
        if not value.get(key):
            raise ContractError(f"lock missing {key}")
    validate_rfc3339(value["created_at"], "created_at")
    validate_rfc3339(value["heartbeat_at"], "heartbeat_at")
    if not re.fullmatch(r"[0-9a-f]{64}", value.get("base_digest", "")):
        raise ContractError("invalid lock base_digest")


def validate_conflict(value: dict[str, Any]) -> None:
    _require_object(value, "conflict")
    if value.get("conflict_schema_version") != CONFLICT_SCHEMA_VERSION:
        raise ContractError("unsupported conflict_schema_version")
    if value.get("status") not in CONFLICT_STATUSES:
        raise ContractError("invalid conflict status")
    for key in ("conflict_id", "discovered_at", "target_file", "base_digest", "current_digest"):
        if not value.get(key):
            raise ContractError(f"conflict missing {key}")
    validate_rfc3339(value["discovered_at"], "discovered_at")


def bump_semver(version: str, level: str) -> str:
    validate_semver(version)
    if level not in {"PATCH", "MINOR", "MAJOR"}:
        raise ContractError(f"invalid bump level: {level}")
    major, minor, patch = (int(part) for part in version.split(".", 2))
    if level == "PATCH":
        patch += 1
    elif level == "MINOR":
        minor += 1
        patch = 0
    else:
        major += 1
        minor = patch = 0
    return f"{major}.{minor}.{patch}"


def candidate_id(payload: dict[str, Any], now: dt.datetime | None = None) -> str:
    content = copy.deepcopy(payload)
    content.pop("candidate_id", None)
    digest = sha256_digest(canonical_json(content))[:12]
    date = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).strftime("%Y%m%d")
    return f"cand-{date}-{digest}"


def conflict_filename(now: dt.datetime | None = None, digest: str = "00000000") -> str:
    return f"workflow-conflict-{utc_filename_timestamp(now)}-{digest[:8]}.md"


def stale_lock_recovery_filename(now: dt.datetime | None = None) -> str:
    return f"stale-lock-recovery-{utc_filename_timestamp(now)}.md"


def process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def lock_is_stale(lock: dict[str, Any], now: dt.datetime | None = None, timeout_seconds: int = 300, host_name: str | None = None) -> tuple[bool, str]:
    validate_lock(lock)
    current = now or dt.datetime.now(dt.timezone.utc)
    heartbeat = dt.datetime.fromisoformat(lock["heartbeat_at"].replace("Z", "+00:00"))
    age = (current - heartbeat.astimezone(dt.timezone.utc)).total_seconds()
    if age <= timeout_seconds:
        return False, "heartbeat within timeout"
    local_host = host_name or socket.gethostname()
    if lock["host_name"] != local_host:
        return False, "cross-host stale lock requires manual recovery"
    pid = int(lock.get("process_id", 0))
    if process_exists(pid):
        return False, "same-host process still exists"
    return True, "same-host heartbeat expired and process is absent"


def new_lock(target_file: str, base_digest: str, agent_id: str, lock_owner: str | None = None, process_id: int | None = None, host_name: str | None = None, now: dt.datetime | None = None) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{64}", base_digest):
        raise ContractError("invalid lock base_digest")
    current = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    lock = {
        "lock_schema_version": LOCK_SCHEMA_VERSION,
        "lock_owner": lock_owner or agent_id,
        "agent_id": agent_id,
        "process_id": process_id if process_id is not None else os.getpid(),
        "host_name": host_name or socket.gethostname(),
        "created_at": current,
        "heartbeat_at": current,
        "target_file": target_file,
        "base_digest": base_digest,
    }
    validate_lock(lock)
    return lock


def write_lock(path: Path, lock: dict[str, Any]) -> None:
    validate_lock(lock)
    if path.exists():
        raise ContractError(f"active workflow lock exists: {path}")
    atomic_write_text(path, canonical_json(lock))


def heartbeat_lock(path: Path, now: dt.datetime | None = None) -> dict[str, Any]:
    if not path.exists():
        raise ContractError(f"workflow lock missing: {path}")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    validate_lock(value)
    current = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return merge_json_file(path, {"heartbeat_at": current}, validate_lock)


def release_lock(path: Path, process_id: int | None = None, host_name: str | None = None) -> bool:
    if not path.exists():
        return False
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    validate_lock(value)
    if process_id is not None and value["process_id"] != process_id:
        raise ContractError("workflow lock owner mismatch")
    if host_name is not None and value["host_name"] != host_name:
        raise ContractError("workflow lock host mismatch")
    path.unlink()
    return True


def write_conflict_report(conflicts_dir: Path, payload: dict[str, Any], now: dt.datetime | None = None) -> Path:
    report = copy.deepcopy(payload)
    report.setdefault("conflict_schema_version", CONFLICT_SCHEMA_VERSION)
    report.setdefault("discovered_at", (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
    report.setdefault("status", "OPEN")
    validate_conflict(report)
    digest = sha256_digest(canonical_json(report))
    filename = conflict_filename(now, digest)
    metadata = render_machine_block("conflict", report)
    body = "\n".join([
        metadata,
        "",
        "# Workflow Conflict Report",
        "",
        f"- Conflict ID: `{report['conflict_id']}`",
        f"- Target file: `{report['target_file']}`",
        f"- Status: `{report['status']}`",
        f"- Reason: {report.get('conflict_reason', '未说明')}",
        f"- Affected tasks: {', '.join(report.get('affected_task_ids', [])) or '无'}",
        "",
        "## Recommended Handling",
        "",
        str(report.get("recommended_handling", "人工核对后合并")),
        "",
    ])
    path = conflicts_dir / filename
    atomic_write_text(path, body)
    return path


def write_stale_lock_recovery_report(conflicts_dir: Path, lock: dict[str, Any], reason: str, now: dt.datetime | None = None) -> Path:
    current = now or dt.datetime.now(dt.timezone.utc)
    payload = {
        "conflict_schema_version": CONFLICT_SCHEMA_VERSION,
        "conflict_id": f"stale-lock-{utc_filename_timestamp(current)}",
        "discovered_at": current.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "target_file": lock["target_file"],
        "base_digest": lock["base_digest"],
        "current_digest": lock["base_digest"],
        "status": "OPEN",
        "conflict_reason": reason,
        "lock_owner": lock["lock_owner"],
        "agent_id": lock["agent_id"],
        "process_id": lock["process_id"],
        "host_name": lock["host_name"],
    }
    validate_conflict(payload)
    metadata = render_machine_block("conflict", payload)
    body = "\n".join([metadata, "", "# Stale Lock Recovery Report", "", f"- Reason: {reason}", "- Automatic takeover: `NO`", ""])
    path = conflicts_dir / stale_lock_recovery_filename(current)
    atomic_write_text(path, body)
    return path


def registry_markdown(registry: dict[str, Any]) -> str:
    validate_registry(registry)
    lines = [
        "<!-- Generated from template_registry.json; do not edit directly. -->",
        "# Workflow Template Registry",
        "",
        f"Registry schema version: {registry['registry_schema_version']}",
        "",
        "| ID | 类型 | 当前版本 | 生命周期 | 适用任务 | 关键词 | 使用次数 | 成功次数 |",
        "|---|---|---|---|---|---|---:|---:|",
    ]
    entries = [("template", item) for item in registry.get("templates", [])]
    entries += [("module", item) for item in registry.get("modules", [])]
    for kind, item in sorted(entries, key=lambda pair: (pair[0], pair[1]["id"])):
        keywords = ", ".join(item.get("keywords", []))
        lines.append(f"| `{item['id']}` | {kind} | `{item['current_version']}` | {item['lifecycle']} | {item.get('name', '')} | {keywords} | {item.get('usage_count', 0)} | {item.get('success_count', 0)} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    digest = sub.add_parser("digest")
    digest.add_argument("path")
    render = sub.add_parser("render-registry")
    render.add_argument("registry")
    render.add_argument("output")
    validate = sub.add_parser("validate-json")
    validate.add_argument("kind", choices=("workflow", "template", "candidate", "registry", "lock", "conflict"))
    validate.add_argument("path")
    args = parser.parse_args(argv)
    try:
        if args.command == "digest":
            print(file_digest(Path(args.path)))
        elif args.command == "render-registry":
            registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
            atomic_write_text(Path(args.output), registry_markdown(registry))
        else:
            value = json.loads(Path(args.path).read_text(encoding="utf-8"))
            validators = {
                "workflow": validate_workflow_metadata,
                "template": validate_template_metadata,
                "candidate": validate_candidate,
                "registry": validate_registry,
                "lock": validate_lock,
                "conflict": validate_conflict,
            }
            validators[args.kind](value)
            print("PASS")
    except (OSError, json.JSONDecodeError, ContractError) as exc:
        print(f"FAIL: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
