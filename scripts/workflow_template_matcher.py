#!/usr/bin/env python3
# VERSION source: ../VERSION
"""Deterministic, local-only workflow template matching for P2-01."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import workflow_contracts as contracts
import planning_layout as planning_layout


MATCH_METHODS = {"explicit", "binding", "rule", "fallback"}
SELECTABLE_LIFECYCLES = {"FORMAL", "EXPERIMENTAL"}
MIN_RULE_SCORE = 2
SOURCE_FILES = (
    "00_PROJECT_INDEX.md",
    "1_master_plan.md",
    "2_execution_log.md",
    "3_status_update.md",
    "4_handoff.md",
    "5_audit.md",
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
)


class TemplateMatchError(contracts.ContractError):
    """Raised when deterministic template identification cannot be trusted."""


def _lifecycle(entry: dict[str, Any]) -> str:
    return str(entry.get("lifecycle_status", entry.get("lifecycle", "")))


def _registry_entries(skill_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    registry = contracts.load_registry(skill_root)
    templates = registry.get("templates", [])
    if not isinstance(templates, list):
        raise TemplateMatchError("template registry templates must be a list")
    for entry in templates:
        if not isinstance(entry, dict):
            raise TemplateMatchError("template registry entry must be an object")
        if not entry.get("id") or not entry.get("current_version"):
            raise TemplateMatchError("template registry entry missing id or current_version")
        if _lifecycle(entry) not in {"FORMAL", "EXPERIMENTAL", "DEPRECATED"}:
            raise TemplateMatchError(f"invalid template lifecycle: {entry.get('id')}")
        if not entry.get("digest"):
            raise TemplateMatchError(f"template registry entry missing digest: {entry.get('id')}")
        path = contracts.artifact_path(skill_root, entry["id"], entry["current_version"], "task-template")
        if contracts.file_digest(path) != entry["digest"]:
            raise TemplateMatchError(f"template digest mismatch: {entry['id']}@{entry['current_version']}")
    return registry, templates


def _read_project_sources(project_root: Path) -> tuple[str, list[str]]:
    chunks: list[str] = []
    resolved = planning_layout.resolve_layout(project_root, require=False)
    for name in SOURCE_FILES:
        path = (resolved.path(name) if resolved is not None and name in planning_layout.PLANNING_DOCUMENTS else project_root / name)
        if path.is_file():
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    root_entries = sorted(path.name for path in project_root.iterdir()) if project_root.is_dir() else []
    if resolved is not None and resolved.planning_dir.is_dir():
        root_entries.extend(f"{resolved.planning_dir.name}/{name}" for name in SOURCE_FILES if (resolved.planning_dir / name).is_file())
    return "\n".join(chunks), root_entries


def _binding_result(skill_root: Path, project_root: Path, allow_malformed: bool = False) -> dict[str, Any] | None:
    resolved = planning_layout.resolve_layout(project_root, require=False)
    checklist = resolved.path(contracts.CHECKLIST_NAME) if resolved is not None else project_root / contracts.CHECKLIST_NAME
    if not checklist.is_file():
        return None
    try:
        metadata = contracts.extract_machine_json(checklist.read_text(encoding="utf-8"), "workflow")
        contracts.validate_workflow_metadata(metadata)
    except (OSError, contracts.ContractError) as exc:
        if allow_malformed and "missing or malformed workflow machine block" in str(exc):
            return None
        raise TemplateMatchError(f"existing workflow binding is invalid: {exc}") from exc
    binding = metadata["template"]
    try:
        path = contracts.artifact_path(skill_root, binding["template_id"], binding["template_version"], "task-template")
    except contracts.ContractError as exc:
        raise TemplateMatchError(f"bound template version is missing: {exc}") from exc
    actual_digest = contracts.file_digest(path)
    if actual_digest != binding["template_digest"]:
        raise TemplateMatchError(f"bound template digest mismatch: {binding['template_id']}@{binding['template_version']}")
    return {
        "template_id": binding["template_id"],
        "template_version": binding["template_version"],
        "template_digest": actual_digest,
        "match_method": "binding",
        "confidence": 1.0,
        "matched_signals": ["WORKFLOW_CHECKLIST.md.template"],
        "excluded_templates": [],
        "fallback_used": False,
        "reason": "项目已有合法模板绑定",
    }


def _validate_explicit(entry: dict[str, Any], skill_root: Path) -> dict[str, Any]:
    lifecycle = _lifecycle(entry)
    if lifecycle not in SELECTABLE_LIFECYCLES:
        raise TemplateMatchError(f"explicit template is not selectable: {entry['id']} ({lifecycle})")
    path = contracts.artifact_path(skill_root, entry["id"], entry["current_version"], "task-template")
    digest = contracts.file_digest(path)
    if digest != entry["digest"]:
        raise TemplateMatchError(f"template digest mismatch: {entry['id']}@{entry['current_version']}")
    return {
        "template_id": entry["id"],
        "template_version": entry["current_version"],
        "template_digest": digest,
        "match_method": "explicit",
        "confidence": 1.0,
        "matched_signals": [],
        "excluded_templates": [],
        "fallback_used": False,
        "reason": "用户明确指定模板",
    }


def _score_entry(entry: dict[str, Any], project_name: str, source_text: str, root_entries: list[str]) -> tuple[int, list[str], bool]:
    name_text = project_name.lower()
    target_text = source_text.lower()
    all_text = f"{name_text}\n{target_text}\n{' '.join(root_entries).lower()}"
    signals: list[str] = []
    excluded = [keyword for keyword in entry.get("exclude_keywords", []) if str(keyword).lower() in all_text]
    if excluded:
        return -1, [f"exclude:{keyword}" for keyword in sorted(excluded)], True
    score = 0
    for keyword in entry.get("keywords", []):
        keyword_text = str(keyword).lower()
        if keyword_text in name_text:
            score += 4
            signals.append(f"project_name:{keyword}")
        if keyword_text in target_text:
            score += 3
            signals.append(f"target:{keyword}")
        if keyword_text in all_text and f"project_name:{keyword}" not in signals and f"target:{keyword}" not in signals:
            score += 2
            signals.append(f"keyword:{keyword}")
    for artifact_signal in entry.get("artifact_signals", []):
        matches = [name for name in root_entries if str(artifact_signal).lower() in name.lower()]
        if matches:
            score += 4
            signals.append(f"artifact:{artifact_signal}")
    return score, sorted(set(signals)), False


def identify_template(
    skill_root: Path,
    project_root: Path,
    project_name: str | None = None,
    source_text: str | None = None,
    explicit_template_id: str | None = None,
    allow_malformed_binding: bool = False,
) -> dict[str, Any]:
    """Return a deterministic primary-template match using only local files."""
    registry, templates = _registry_entries(skill_root)
    project_name = project_name or project_root.name
    collected_text, root_entries = _read_project_sources(project_root)
    source_text = source_text or collected_text

    by_id = {entry["id"]: entry for entry in templates}
    if explicit_template_id:
        entry = by_id.get(explicit_template_id)
        if entry is None:
            raise TemplateMatchError(f"unknown template_id: {explicit_template_id}")
        return _validate_explicit(entry, skill_root)

    binding = _binding_result(skill_root, project_root, allow_malformed_binding)
    if binding is not None:
        return binding

    scored: list[tuple[int, str, dict[str, Any], list[str]]] = []
    excluded_templates: list[str] = []
    for entry in templates:
        if _lifecycle(entry) not in SELECTABLE_LIFECYCLES:
            continue
        score, signals, excluded = _score_entry(entry, project_name, source_text, root_entries)
        if excluded:
            excluded_templates.append(entry["id"])
            continue
        if score >= MIN_RULE_SCORE:
            scored.append((score, entry["id"], entry, signals))
    scored.sort(key=lambda item: (-item[0], item[1]))
    if scored:
        score, _template_id, entry, signals = scored[0]
        path = contracts.artifact_path(skill_root, entry["id"], entry["current_version"], "task-template")
        confidence = round(min(0.99, score / 12.0), 2)
        return {
            "template_id": entry["id"],
            "template_version": entry["current_version"],
            "template_digest": contracts.file_digest(path),
            "match_method": "rule",
            "confidence": confidence,
            "matched_signals": signals,
            "excluded_templates": sorted(excluded_templates),
            "fallback_used": False,
            "reason": f"本地确定性规则匹配，整数评分={score}",
        }

    generic = by_id.get("generic-project")
    if generic is None:
        raise TemplateMatchError("generic-project fallback is missing from registry")
    if _lifecycle(generic) not in SELECTABLE_LIFECYCLES:
        raise TemplateMatchError("generic-project fallback is not selectable")
    path = contracts.artifact_path(skill_root, generic["id"], generic["current_version"], "task-template")
    if contracts.file_digest(path) != generic["digest"]:
        raise TemplateMatchError("generic-project fallback digest mismatch")
    return {
        "template_id": generic["id"],
        "template_version": generic["current_version"],
        "template_digest": generic["digest"],
        "match_method": "fallback",
        "confidence": 0.25,
        "matched_signals": [],
        "excluded_templates": sorted(excluded_templates),
        "fallback_used": True,
        "reason": "没有达到最低规则评分，回退 generic-project",
    }


def select_template(skill_root: Path, project_root: Path, task_text: str, explicit_template_id: str | None = None) -> dict[str, Any]:
    return identify_template(skill_root, project_root, project_root.name, task_text, explicit_template_id)
