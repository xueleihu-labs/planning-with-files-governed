#!/usr/bin/env python3
# VERSION source: ../VERSION
"""Read-only deterministic workflow replay for planning-with-files P3-01."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import workflow_contracts as contracts


REPLAY_SCHEMA_VERSION = 1
REPLAY_STATUSES = {
    "COMPLETE",
    "PARTIAL",
    "PARTIAL_KNOWN_NON_BLOCKING",
    "INSUFFICIENT_BASELINE",
    "INVALID_BINDING",
    "DIGEST_MISMATCH",
    "INVALID_CHECKLIST",
}
KNOWN_NON_BLOCKING_DEBT_STATUSES = {"KNOWN_NON_BLOCKING", "PARTIAL_KNOWN_NON_BLOCKING"}
DIFFERENCE_KEYS = (
    "added_tasks",
    "missing_tasks",
    "skipped_tasks",
    "deprecated_tasks",
    "reordered_tasks",
    "changed_dependencies",
    "changed_owners",
    "changed_completion_requirements",
    "rework_events",
    "blocking_events",
    "evidence_gaps",
)
HISTORY_COLUMNS = ["时间", "变更类型", "涉及ID", "变更内容", "原因", "影响范围", "执行者"]
TASK_ID_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9_-]*\b")
PATH_RE = re.compile(r"(?<![A-Za-z0-9_.-])(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:md|py|json|txt|yaml|yml|sh|toml|csv)$")


class ReplayFailure(Exception):
    """Expected replay failure with a stable status and message."""

    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _empty_differences() -> dict[str, list[Any]]:
    return {key: [] for key in DIFFERENCE_KEYS}


def _base_result(project_id: str) -> dict[str, Any]:
    return {
        "replay_schema_version": REPLAY_SCHEMA_VERSION,
        "project_id": project_id,
        "replay_status": "INVALID_CHECKLIST",
        "baseline_source": "",
        "template": {},
        "modules": [],
        "expected_task_ids": [],
        "actual_task_ids": [],
        "execution_sequence": [],
        "differences": _empty_differences(),
        "execution_summary": {
            "completed": 0,
            "in_progress": 0,
            "blocked": 0,
            "skipped": 0,
            "deprecated": 0,
        },
        "warnings": [],
        "known_non_blocking_debts": [],
    }


def _failure(project_root: Path, status: str, message: str) -> dict[str, Any]:
    result = _base_result(project_root.name)
    result["replay_status"] = status
    result["warnings"] = [message]
    return result


def _registry_entry(registry: dict[str, Any], artifact_id: str, artifact_type: str) -> dict[str, Any]:
    key = "templates" if artifact_type == "task-template" else "modules"
    for entry in registry.get(key, []):
        if entry.get("id") == artifact_id:
            return entry
    raise ReplayFailure("INVALID_BINDING", f"bound {artifact_type} is not registered: {artifact_id}")


def _validate_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ReplayFailure("INVALID_BINDING", f"invalid {label}")
    return value


def _load_bound_artifact(
    skill_root: Path,
    registry: dict[str, Any],
    binding: dict[str, Any],
    artifact_type: str,
) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    if not isinstance(binding, dict):
        raise ReplayFailure("INVALID_BINDING", f"{artifact_type} binding must be an object")
    id_key = "template_id" if artifact_type == "task-template" else "module_id"
    artifact_id = binding.get(id_key)
    version = binding.get("template_version" if artifact_type == "task-template" else "module_version")
    digest = binding.get("template_digest" if artifact_type == "task-template" else "module_digest")
    try:
        contracts.validate_id(artifact_id, id_key)
        contracts.validate_semver(version, f"{id_key.replace('_id', '_version')}")
    except contracts.ContractError as exc:
        raise ReplayFailure("INVALID_BINDING", str(exc)) from exc
    expected_digest = _validate_digest(digest, f"{id_key.replace('_id', '_digest')}")
    try:
        entry = _registry_entry(registry, artifact_id, artifact_type)
        path = contracts.artifact_path(skill_root, artifact_id, version, artifact_type)
    except contracts.ContractError as exc:
        raise ReplayFailure("INVALID_BINDING", str(exc)) from exc
    actual_digest = contracts.file_digest(path)
    if actual_digest != expected_digest:
        raise ReplayFailure("DIGEST_MISMATCH", f"{artifact_id}@{version} digest mismatch")
    try:
        metadata = contracts.extract_machine_json(path.read_text(encoding="utf-8"), "template")
        contracts.validate_template_metadata(metadata)
    except (OSError, contracts.ContractError) as exc:
        raise ReplayFailure("INVALID_BINDING", f"invalid metadata for {artifact_id}@{version}: {exc}") from exc
    if metadata.get("artifact_type") != artifact_type or metadata.get(id_key) != artifact_id:
        raise ReplayFailure("INVALID_BINDING", f"metadata binding mismatch for {artifact_id}@{version}")
    lifecycle = entry.get("lifecycle", entry.get("lifecycle_status", ""))
    if lifecycle not in {"FORMAL", "EXPERIMENTAL", "DEPRECATED"}:
        raise ReplayFailure("INVALID_BINDING", f"invalid lifecycle for {artifact_id}")
    return (
        {
            id_key: artifact_id,
            "template_version" if artifact_type == "task-template" else "module_version": version,
            "template_digest" if artifact_type == "task-template" else "module_digest": expected_digest,
        },
        path,
        metadata,
    )


def _task_row(row: dict[str, str], module: bool = False) -> dict[str, str]:
    return {
        "ID": row.get("ID", "").strip(),
        "阶段/任务": row.get("阶段/任务", "").strip(),
        "主责智能体": row.get("主责智能体", row.get("默认主责", "")).strip(),
        "前置条件": row.get("前置条件", "").strip(),
        "完成条件": row.get("完成条件", "").strip(),
        "证据要求": row.get("证据要求", "").strip(),
        "_source": "module" if module else "template",
    }


def _artifact_tasks(path: Path, module: bool) -> list[dict[str, str]]:
    try:
        rows = contracts.parse_markdown_table(
            path.read_text(encoding="utf-8"),
            ["ID", "阶段/任务", "前置条件", "完成条件"],
        )
    except OSError as exc:
        raise ReplayFailure("INVALID_BINDING", f"cannot read workflow artifact: {path}") from exc
    if not rows:
        return []
    tasks = [_task_row(row, module) for row in rows]
    for task in tasks:
        try:
            contracts.validate_task_id(task["ID"])
        except contracts.ContractError as exc:
            raise ReplayFailure("INVALID_BINDING", str(exc)) from exc
    return tasks


def _history_rows(checklist_text: str) -> list[dict[str, str]]:
    return contracts.parse_markdown_table(checklist_text, HISTORY_COLUMNS)


def _history_text(row: dict[str, str]) -> str:
    return " ".join(row.get(column, "") for column in HISTORY_COLUMNS).lower()


def _history_task_ids(row: dict[str, str], known_ids: set[str]) -> list[str]:
    raw = row.get("涉及ID", "")
    return [token for token in TASK_ID_RE.findall(raw) if token in known_ids]


def _baseline_source(metadata: dict[str, Any], history: list[dict[str, str]]) -> tuple[str, list[str] | None]:
    for key in ("workflow_baseline", "initial_baseline", "baseline"):
        value = metadata.get(key)
        if isinstance(value, dict) and isinstance(value.get("expected_task_ids"), list):
            ids = [str(item) for item in value["expected_task_ids"]]
            if ids:
                return "checklist-baseline", ids
    for row in history:
        change_type = row.get("变更类型", "")
        if "初始化" in change_type:
            return "checklist-initialization", None
        if "模板同步" in change_type or "同步模板" in change_type:
            return "template-sync", None
    return "bound-template", None


def _ordered_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _compare_tasks(expected: list[dict[str, str]], actual: list[dict[str, str]]) -> dict[str, list[Any]]:
    differences = _empty_differences()
    expected_ids = [task["ID"] for task in expected]
    actual_ids = [task["ID"] for task in actual]
    expected_set = set(expected_ids)
    actual_set = set(actual_ids)
    differences["added_tasks"] = sorted(actual_set - expected_set)
    differences["missing_tasks"] = [task_id for task_id in expected_ids if task_id not in actual_set]
    differences["skipped_tasks"] = [task["ID"] for task in actual if task.get("状态") == "已跳过"]
    differences["deprecated_tasks"] = [task["ID"] for task in actual if task.get("状态") == "已废弃"]

    expected_index = {task_id: index for index, task_id in enumerate(expected_ids)}
    actual_index = {task_id: index for index, task_id in enumerate(actual_ids)}
    differences["reordered_tasks"] = [
        {
            "task_id": task_id,
            "expected_index": expected_index[task_id],
            "actual_index": actual_index[task_id],
        }
        for task_id in sorted(expected_set & actual_set)
        if expected_index[task_id] != actual_index[task_id]
    ]

    expected_by_id = {task["ID"]: task for task in expected}
    actual_by_id = {task["ID"]: task for task in actual}
    for task_id in sorted(expected_set & actual_set):
        old = expected_by_id[task_id]
        new = actual_by_id[task_id]
        if old.get("前置条件", "") != new.get("前置条件", ""):
            differences["changed_dependencies"].append({
                "task_id": task_id,
                "expected": old.get("前置条件", ""),
                "actual": new.get("前置条件", ""),
            })
        if old.get("主责智能体", "") != new.get("主责智能体", ""):
            differences["changed_owners"].append({
                "task_id": task_id,
                "expected": old.get("主责智能体", ""),
                "actual": new.get("主责智能体", ""),
            })
        if new.get("完成条件") and old.get("完成条件", "") != new.get("完成条件", ""):
            differences["changed_completion_requirements"].append({
                "task_id": task_id,
                "expected": old.get("完成条件", ""),
                "actual": new.get("完成条件", ""),
            })
    return differences


def _execution_sequence(history: list[dict[str, str]], actual_ids: list[str], known_ids: set[str]) -> tuple[list[str], list[str]]:
    sequence: list[str] = []
    for row in history:
        sequence.extend(_history_task_ids(row, known_ids))
    sequence = _ordered_unique(sequence)
    warnings: list[str] = []
    if not sequence:
        if actual_ids:
            warnings.append("无法从清单变更历史确认精确执行顺序")
            return actual_ids[:], warnings
        return [], warnings
    missing = [task_id for task_id in actual_ids if task_id not in sequence]
    if missing:
        warnings.append("清单中存在未出现在变更历史的任务，执行顺序为部分可恢复")
    return sequence, warnings


def _known_non_blocking_replay_debts(
    metadata: dict[str, Any], differences: dict[str, list[Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Classify only explicitly declared, fully covered replay differences.

    The checklist machine block may carry optional structured debt entries.  A
    debt is eligible only when it names valid replay difference keys and all
    currently observed non-empty keys are covered by declared debts.  This
    keeps historical compatibility debt truthful without turning an
    undeclared replay mismatch into a non-blocking result.
    """
    raw = metadata.get("known_non_blocking_debts", [])
    if not isinstance(raw, list):
        return [], []
    observed = {key for key, value in differences.items() if value}
    if not observed:
        return [], []
    debts: list[dict[str, Any]] = []
    covered: set[str] = set()
    for item in raw:
        if not isinstance(item, dict) or item.get("status") not in KNOWN_NON_BLOCKING_DEBT_STATUSES:
            continue
        keys = item.get("difference_keys")
        if not isinstance(keys, list) or not keys:
            continue
        normalized = {str(key) for key in keys}
        if not normalized <= set(DIFFERENCE_KEYS):
            continue
        debts.append(item)
        covered.update(normalized)
    if not debts or not observed <= covered:
        return [], []
    labels = [str(item.get("debt_id") or item.get("description") or "unnamed") for item in debts]
    return debts, ["回放差异已由清单显式登记为已知非阻断债务：" + "、".join(labels)]


def _rework_events(history: list[dict[str, str]], known_ids: set[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for index, row in enumerate(history):
        text = _history_text(row)
        if not re.search(r"返工|重做|rework|重新进入|完成后.*进行中", text):
            continue
        ids = _history_task_ids(row, known_ids)
        events.append({"history_index": index, "task_ids": ids, "event": "rework"})
    return events


def _blocking_events(history: list[dict[str, str]], actual: list[dict[str, str]], known_ids: set[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for task in actual:
        if task.get("状态") == "阻塞":
            events.append({"task_id": task["ID"], "event": "blocked", "source": "current-checklist"})
    for index, row in enumerate(history):
        text = _history_text(row)
        if not re.search(r"阻塞|解除阻塞|解除|恢复", text):
            continue
        ids = _history_task_ids(row, known_ids)
        events.append({"history_index": index, "task_ids": ids, "event": "blocking-change"})
    return events


def _path_references(value: str) -> list[str]:
    return PATH_RE.findall(value.replace("、", " ").replace("，", " "))


def _evidence_gaps(project_root: Path, actual: list[dict[str, str]], history: list[dict[str, str]]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    history_text = "\n".join(_history_text(row) for row in history)
    for task in actual:
        task_id = task["ID"]
        evidence = task.get("完成证据", "").strip()
        if task.get("状态") == "已完成":
            if not evidence or evidence in {"—", "-", "无"}:
                gaps.append({"task_id": task_id, "type": "missing_completion_evidence"})
            for reference in _path_references(evidence):
                if not (project_root / reference).is_file():
                    gaps.append({"task_id": task_id, "type": "evidence_path_missing", "reference": reference})
        if task.get("核验状态") == "已核验" and not re.search(rf"{re.escape(task_id.lower())}.*核验|核验.*{re.escape(task_id.lower())}|验证.*{re.escape(task_id.lower())}", history_text):
            gaps.append({"task_id": task_id, "type": "missing_verification_record"})
        name = task.get("阶段/任务", "")
        if task.get("状态") == "已完成" and "审计" in name:
            audit_path = project_root / "5_audit.md"
            audit_text = audit_path.read_text(encoding="utf-8", errors="replace") if audit_path.is_file() else ""
            if not re.search(r"PASS|通过|结论", audit_text, re.IGNORECASE):
                gaps.append({"task_id": task_id, "type": "missing_audit_conclusion"})
        if task.get("状态") == "已完成" and "交接" in name:
            handoff_path = project_root / "4_handoff.md"
            handoff_text = handoff_path.read_text(encoding="utf-8", errors="replace") if handoff_path.is_file() else ""
            if not handoff_text.strip() or not re.search(r"交接|handoff|完成|PASS", handoff_text, re.IGNORECASE):
                gaps.append({"task_id": task_id, "type": "missing_handoff_record"})
    return gaps


def _summary(actual: list[dict[str, str]]) -> dict[str, int]:
    mapping = {
        "已完成": "completed",
        "进行中": "in_progress",
        "阻塞": "blocked",
        "已跳过": "skipped",
        "已废弃": "deprecated",
    }
    result = {key: 0 for key in ("completed", "in_progress", "blocked", "skipped", "deprecated")}
    for task in actual:
        key = mapping.get(task.get("状态"))
        if key:
            result[key] += 1
    return result


def replay_project(skill_root: Path, project_root: Path) -> dict[str, Any]:
    """Replay a project without writing files or acquiring locks."""
    checklist_path = project_root / contracts.CHECKLIST_NAME
    if not checklist_path.is_file():
        return _failure(project_root, "INVALID_CHECKLIST", f"missing {contracts.CHECKLIST_NAME}")
    try:
        checklist_text = checklist_path.read_text(encoding="utf-8")
        metadata = contracts.validate_checklist_text(checklist_text)
        tasks = contracts.checklist_tasks(checklist_text)
        if not tasks:
            raise ReplayFailure("INVALID_CHECKLIST", "workflow checklist has no tasks")
        if len({task["ID"] for task in tasks}) != len(tasks):
            raise ReplayFailure("INVALID_CHECKLIST", "duplicate task_id in workflow checklist")
        registry = contracts.load_registry(skill_root)
        template_binding = metadata.get("template")
        template_info, template_path, template_metadata = _load_bound_artifact(skill_root, registry, template_binding, "task-template")
        module_infos: list[dict[str, Any]] = []
        expected_tasks = _artifact_tasks(template_path, module=False)
        for module_binding in metadata.get("modules", []):
            module_info, module_path, _module_metadata = _load_bound_artifact(skill_root, registry, module_binding, "workflow-module")
            module_infos.append(module_info)
            expected_tasks.extend(_artifact_tasks(module_path, module=True))
        if not expected_tasks:
            raise ReplayFailure("INSUFFICIENT_BASELINE", "bound template and modules contain no recoverable task table")
        expected_ids = [task["ID"] for task in expected_tasks]
        if len(set(expected_ids)) != len(expected_ids):
            raise ReplayFailure("INVALID_BINDING", "duplicate task_id in bound template/module baseline")
        history = _history_rows(checklist_text)
        baseline_source, baseline_ids = _baseline_source(metadata, history)
        if baseline_ids is not None:
            expected_ids = baseline_ids
        actual = [dict(task) for task in tasks]
        differences = _compare_tasks(expected_tasks, actual)
        known_ids = set(expected_ids) | {task["ID"] for task in actual}
        execution_sequence, sequence_warnings = _execution_sequence(history, [task["ID"] for task in actual], known_ids)
        differences["rework_events"] = _rework_events(history, known_ids)
        differences["blocking_events"] = _blocking_events(history, actual, known_ids)
        differences["evidence_gaps"] = _evidence_gaps(project_root, actual, history)
        known_debts, debt_warnings = _known_non_blocking_replay_debts(metadata, differences)
        warnings = sequence_warnings[:] + debt_warnings
        if differences["evidence_gaps"]:
            warnings.append("存在证据缺口，回放标记为 PARTIAL")
        if known_debts:
            replay_status = "PARTIAL_KNOWN_NON_BLOCKING"
        else:
            replay_status = "PARTIAL" if warnings else "COMPLETE"
        result = _base_result(metadata["project_id"])
        result.update({
            "replay_status": replay_status,
            "baseline_source": baseline_source,
            "template": template_info,
            "modules": module_infos,
            "expected_task_ids": expected_ids,
            "actual_task_ids": [task["ID"] for task in actual],
            "execution_sequence": execution_sequence,
            "differences": differences,
            "execution_summary": _summary(actual),
            "warnings": sorted(set(warnings)),
            "known_non_blocking_debts": known_debts,
        })
        return result
    except ReplayFailure as exc:
        return _failure(project_root, exc.status, exc.message)
    except contracts.ContractError as exc:
        return _failure(project_root, "INVALID_CHECKLIST", str(exc))
    except (OSError, ValueError, TypeError) as exc:
        return _failure(project_root, "INVALID_CHECKLIST", str(exc))


def _summary_text(result: dict[str, Any]) -> str:
    differences = result["differences"]
    counts = ", ".join(f"{key}={len(value)}" for key, value in differences.items() if value)
    counts = counts or "no differences"
    return "\n".join([
        f"Replay status: {result['replay_status']}",
        f"Project: {result['project_id']}",
        f"Baseline: {result['baseline_source'] or 'unknown'}",
        f"Template: {result['template'].get('template_id', 'unknown')}@{result['template'].get('template_version', 'unknown')}",
        f"Expected tasks: {len(result['expected_task_ids'])}",
        f"Actual tasks: {len(result['actual_task_ids'])}",
        f"Differences: {counts}",
        f"Warnings: {len(result['warnings'])}",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--format", choices=("json", "summary"), default="json")
    parser.add_argument("--skill-root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args(argv)
    result = replay_project(Path(args.skill_root).expanduser(), Path(args.project_root).expanduser())
    if args.format == "summary":
        print(_summary_text(result))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["replay_status"] in {"COMPLETE", "PARTIAL", "PARTIAL_KNOWN_NON_BLOCKING"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
