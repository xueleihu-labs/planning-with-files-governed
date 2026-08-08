#!/usr/bin/env python3
# VERSION source: ../VERSION
"""Deterministic local workflow-module composition for P2-02."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pwf_governed._legacy import workflow_contracts as contracts
from pwf_governed._legacy import planning_layout as planning_layout


SELECTABLE_LIFECYCLES = {"FORMAL", "EXPERIMENTAL"}
SOURCE_RANK = {"explicit": 1, "binding": 2, "template-default": 3, "rule": 4}


class ModuleCompositionError(contracts.ContractError):
    """Raised when module composition cannot be made deterministic and safe."""


def _lifecycle(entry: dict[str, Any]) -> str:
    return str(entry.get("lifecycle_status", entry.get("lifecycle", "")))


def _module_entries(skill_root: Path) -> dict[str, dict[str, Any]]:
    registry = contracts.load_registry(skill_root)
    result: dict[str, dict[str, Any]] = {}
    for entry in registry.get("modules", []):
        if not isinstance(entry, dict) or not entry.get("id") or not entry.get("current_version"):
            raise ModuleCompositionError("invalid module registry entry")
        module_id = entry["id"]
        if module_id in result:
            raise ModuleCompositionError(f"duplicate module registry entry: {module_id}")
        if _lifecycle(entry) not in {"FORMAL", "EXPERIMENTAL", "DEPRECATED"}:
            raise ModuleCompositionError(f"invalid module lifecycle: {module_id}")
        if not entry.get("digest"):
            raise ModuleCompositionError(f"module registry entry missing digest: {module_id}")
        try:
            path = contracts.artifact_path(skill_root, module_id, entry["current_version"], "workflow-module")
        except contracts.ContractError as exc:
            raise ModuleCompositionError(f"module version missing: {module_id}@{entry['current_version']}") from exc
        if contracts.file_digest(path) != entry["digest"]:
            raise ModuleCompositionError(f"module digest mismatch: {module_id}@{entry['current_version']}")
        result[module_id] = entry
    return result


def _module_binding(skill_root: Path, module_id: str, version: str, digest: str, method: str, required: bool, signals: list[str], reason: str) -> dict[str, Any]:
    if not module_id:
        raise ModuleCompositionError("module_id is empty")
    try:
        path = contracts.artifact_path(skill_root, module_id, version, "workflow-module")
    except contracts.ContractError as exc:
        raise ModuleCompositionError(f"module version missing: {module_id}@{version}") from exc
    actual_digest = contracts.file_digest(path)
    if actual_digest != digest:
        raise ModuleCompositionError(f"module digest mismatch: {module_id}@{version}")
    metadata = contracts.extract_machine_json(path.read_text(encoding="utf-8"), "template")
    contracts.validate_template_metadata(metadata)
    if metadata.get("artifact_type") != "workflow-module" or metadata.get("module_id") != module_id:
        raise ModuleCompositionError(f"module metadata mismatch: {module_id}@{version}")
    return {
        "module_id": module_id,
        "module_version": version,
        "module_digest": actual_digest,
        "selection_method": method,
        "required": bool(required),
        "matched_signals": sorted(set(signals)),
        "reason": reason,
    }


def _current_binding(skill_root: Path, entry: dict[str, Any], method: str, required: bool, signals: list[str], reason: str) -> dict[str, Any]:
    lifecycle = _lifecycle(entry)
    if method != "binding" and lifecycle not in SELECTABLE_LIFECYCLES:
        raise ModuleCompositionError(f"module is not selectable for new composition: {entry['id']} ({lifecycle})")
    return _module_binding(skill_root, entry["id"], entry["current_version"], entry["digest"], method, required, signals, reason)


def _source_text(project_root: Path, supplied_text: str | None) -> str:
    if supplied_text is not None:
        return supplied_text
    chunks: list[str] = []
    resolved = planning_layout.resolve_layout(project_root, require=False)
    for name in ("00_PROJECT_INDEX.md", "1_master_plan.md", "2_execution_log.md", "3_status_update.md", "4_handoff.md", "5_audit.md", "README.md", "AGENTS.md", "CLAUDE.md"):
        path = resolved.path(name) if resolved is not None and name in planning_layout.PLANNING_DOCUMENTS else project_root / name
        if path.is_file():
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def _default_module_ids(skill_root: Path, template_selection: dict[str, Any]) -> tuple[list[str], list[str]]:
    path = contracts.artifact_path(skill_root, template_selection["template_id"], template_selection["template_version"], "task-template")
    metadata = contracts.extract_machine_json(path.read_text(encoding="utf-8"), "template")
    contracts.validate_template_metadata(metadata)
    required = list(metadata.get("module_ids", []))
    optional = list(metadata.get("recommended_module_ids", metadata.get("optional_module_ids", [])))
    return required, optional


def _add_candidate(selected: dict[str, dict[str, Any]], candidate: dict[str, Any], warnings: list[str]) -> None:
    module_id = candidate["module_id"]
    current = selected.get(module_id)
    if current is None:
        selected[module_id] = candidate
        return
    current_rank = SOURCE_RANK[current["selection_method"]]
    candidate_rank = SOURCE_RANK[candidate["selection_method"]]
    if candidate["module_digest"] != current["module_digest"] or candidate["module_version"] != current["module_version"]:
        if candidate_rank == current_rank:
            raise ModuleCompositionError(f"same-level module version conflict: {module_id}")
        if candidate_rank < current_rank:
            selected[module_id] = candidate
        else:
            warnings.append(f"重复模块已去重：保留 {module_id} 的 {current['selection_method']} 来源")
        return
    if candidate_rank < current_rank:
        selected[module_id] = candidate
    elif candidate_rank == current_rank:
        current["required"] = bool(current["required"] or candidate["required"])
        current["matched_signals"] = sorted(set(current.get("matched_signals", []) + candidate.get("matched_signals", [])))
    else:
        current["required"] = bool(current["required"] or candidate["required"])
        current["matched_signals"] = sorted(set(current.get("matched_signals", []) + candidate.get("matched_signals", [])))


def _resolve_dependencies(skill_root: Path, entries: dict[str, dict[str, Any]], selected: dict[str, dict[str, Any]], excluded: set[str], warnings: list[str]) -> None:
    visited: set[str] = set()

    def visit(module_id: str, stack: tuple[str, ...]) -> None:
        if module_id in stack:
            raise ModuleCompositionError(f"module dependency cycle: {' -> '.join(stack + (module_id,))}")
        if module_id in visited:
            return
        visited.add(module_id)
        entry = entries[module_id]
        for dependency_id in entry.get("requires_modules", []):
            if dependency_id in excluded:
                raise ModuleCompositionError(f"required dependency explicitly excluded: {module_id} -> {dependency_id}")
            if dependency_id not in entries:
                raise ModuleCompositionError(f"required dependency module missing: {module_id} -> {dependency_id}")
            dependency_entry = entries[dependency_id]
            candidate = _current_binding(skill_root, dependency_entry, "rule", True, [f"依赖 {module_id} 要求 {dependency_id}"], f"依赖模块自动补入：{module_id}")
            _add_candidate(selected, candidate, warnings)
            visit(dependency_id, stack + (module_id,))

    for module_id in list(selected):
        visit(module_id, ())


def _conflicts(entry: dict[str, Any], other_id: str) -> bool:
    return other_id in set(entry.get("conflicts_with", []))


def _resolve_conflicts(entries: dict[str, dict[str, Any]], selected: dict[str, dict[str, Any]], warnings: list[str]) -> None:
    changed = True
    while changed:
        changed = False
        ids = sorted(selected)
        for index, left_id in enumerate(ids):
            for right_id in ids[index + 1:]:
                if not (_conflicts(entries[left_id], right_id) or _conflicts(entries[right_id], left_id)):
                    continue
                left_rank = SOURCE_RANK[selected[left_id]["selection_method"]]
                right_rank = SOURCE_RANK[selected[right_id]["selection_method"]]
                if left_rank == right_rank:
                    raise ModuleCompositionError(f"same-level module conflict: {left_id} vs {right_id}")
                drop_id = left_id if left_rank > right_rank else right_id
                keep_id = right_id if drop_id == left_id else left_id
                dropped = selected[drop_id]
                if dropped["required"] and dropped["selection_method"] == "template-default":
                    raise ModuleCompositionError(
                        f"required template module conflicts with higher-priority module: {drop_id} vs {keep_id}"
                    )
                warnings.append(f"模块冲突已按优先级处理：保留 {keep_id}，跳过 {drop_id}")
                del selected[drop_id]
                changed = True
                break
            if changed:
                break


def _verify_dependencies(entries: dict[str, dict[str, Any]], selected: dict[str, dict[str, Any]]) -> None:
    for module_id in selected:
        for dependency_id in entries[module_id].get("requires_modules", []):
            if dependency_id not in selected:
                raise ModuleCompositionError(f"required dependency removed by conflict resolution: {module_id} -> {dependency_id}")


def compose_modules(
    skill_root: Path,
    project_root: Path,
    template_selection: dict[str, Any],
    source_text: str | None = None,
    explicit_module_ids: list[str] | None = None,
    excluded_module_ids: list[str] | None = None,
    existing_bindings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compose zero or more modules using only local deterministic rules."""
    entries = _module_entries(skill_root)
    explicit = list(explicit_module_ids or [])
    excluded = set(excluded_module_ids or [])
    if len(set(explicit)) != len(explicit):
        raise ModuleCompositionError("duplicate explicit module_id")
    for module_id in excluded:
        if module_id not in entries:
            raise ModuleCompositionError(f"unknown excluded module: {module_id}")
    selected: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []

    for module_id in explicit:
        if module_id in excluded:
            warnings.append(f"显式排除优先：跳过显式模块 {module_id}")
            continue
        entry = entries.get(module_id)
        if entry is None:
            raise ModuleCompositionError(f"unknown explicit module: {module_id}")
        selected[module_id] = _current_binding(skill_root, entry, "explicit", False, [f"用户明确指定 {module_id}"], "用户明确指定模块")

    for binding in existing_bindings or []:
        module_id = binding.get("module_id", "")
        if module_id in excluded:
            warnings.append(f"显式排除优先：跳过已有模块 {module_id}")
            continue
        if module_id not in entries:
            raise ModuleCompositionError(f"bound module is not registered: {module_id}")
        selected_candidate = _module_binding(
            skill_root,
            module_id,
            binding.get("module_version", ""),
            binding.get("module_digest", ""),
            "binding",
            bool(binding.get("required", False)),
            binding.get("matched_signals", ["WORKFLOW_CHECKLIST.md.modules"]),
            binding.get("reason", "项目已有合法模块绑定"),
        )
        _add_candidate(selected, selected_candidate, warnings)

    required_ids, optional_ids = _default_module_ids(skill_root, template_selection)
    for module_id in required_ids:
        if module_id in excluded:
            raise ModuleCompositionError(f"required template module explicitly excluded: {module_id}")
        entry = entries.get(module_id)
        if entry is None:
            raise ModuleCompositionError(f"required template module missing: {module_id}")
        _add_candidate(selected, _current_binding(skill_root, entry, "template-default", True, [f"主模板 {template_selection['template_id']} 推荐 {module_id}"], "主模板默认必需模块"), warnings)
    source = _source_text(project_root, source_text).lower()
    for module_id in optional_ids:
        if module_id in excluded or module_id in selected:
            continue
        entry = entries.get(module_id)
        if entry is None:
            raise ModuleCompositionError(f"optional template module missing: {module_id}")
        if any(str(keyword).lower() in source for keyword in entry.get("keywords", [])):
            _add_candidate(selected, _current_binding(skill_root, entry, "template-default", False, [f"主模板可选模块命中 {module_id}"], "主模板可选模块"), warnings)

    for module_id in sorted(entries):
        if module_id in selected or module_id in excluded:
            continue
        entry = entries[module_id]
        if _lifecycle(entry) not in SELECTABLE_LIFECYCLES:
            continue
        signals = [str(keyword) for keyword in entry.get("keywords", []) if str(keyword).lower() in source]
        if signals:
            _add_candidate(selected, _current_binding(skill_root, entry, "rule", False, [f"规则:{signal}" for signal in signals], "项目目标/文件内容触发模块规则"), warnings)

    _resolve_dependencies(skill_root, entries, selected, excluded, warnings)
    _resolve_conflicts(entries, selected, warnings)
    _verify_dependencies(entries, selected)
    ordered = list(selected.values())
    return {"modules": ordered, "excluded_modules": sorted(excluded), "warnings": sorted(set(warnings))}
