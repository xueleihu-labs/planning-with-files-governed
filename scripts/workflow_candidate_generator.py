#!/usr/bin/env python3
# VERSION source: ../VERSION
"""Deterministic replay classification and candidate generation for P3-02."""

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
import workflow_replay as replay


CLASSIFICATIONS = {
    "PROJECT_EXCEPTION",
    "INCIDENTAL_ISSUE",
    "REUSABLE_IMPROVEMENT",
    "NEW_MODULE_CANDIDATE",
    "NEW_TEMPLATE_CANDIDATE",
    "TEMPLATE_MISSING",
    "TEMPLATE_REDUNDANT",
    "TEMPLATE_ERROR",
    "NO_ACTION",
}
RISKS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
STATUSES = {"PROPOSED", "VALIDATING", "APPROVED", "APPLIED", "REJECTED", "SUPERSEDED"}
OPERATIONS = {
    "ADD_TASK",
    "REMOVE_TASK",
    "CHANGE_ORDER",
    "CHANGE_DEPENDENCY",
    "CHANGE_OWNER",
    "CHANGE_COMPLETION_REQUIREMENT",
    "CHANGE_EVIDENCE_REQUIREMENT",
}
TASK_SIGNAL_RE = re.compile(r"project-specific|项目特例|一次性|仅本项目|绝对路径|机器路径", re.IGNORECASE)
PATH_RE = re.compile(r"(?:^|\\s)(/[^\s|]+)")
SUCCESS_RE = re.compile(r"PASS|通过|验收|封板|完成", re.IGNORECASE)


class CandidateGenerationError(ValueError):
    """Expected candidate-generation failure."""


def _now(value: str | None = None) -> dt.datetime:
    if value:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc).replace(microsecond=0)
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _rfc3339(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _candidate_root(skill_root: Path) -> Path:
    return skill_root / "templates" / "workflow" / "candidates"


def _source_checklist(skill_root: Path, project_root: Path) -> tuple[dict[str, Any], str]:
    path = project_root / contracts.CHECKLIST_NAME
    if not path.is_file():
        raise CandidateGenerationError(f"missing {contracts.CHECKLIST_NAME}")
    try:
        text = path.read_text(encoding="utf-8")
        metadata = contracts.validate_checklist_text(text)
    except (OSError, contracts.ContractError) as exc:
        raise CandidateGenerationError(f"invalid checklist: {exc}") from exc
    return metadata, contracts.file_digest(path)


def _acceptance_text(project_root: Path) -> str:
    chunks = []
    for name in ("5_audit.md", "3_status_update.md", "2_execution_log.md"):
        path = project_root / name
        if path.is_file():
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def _has_acceptance(project_root: Path, replay_result: dict[str, Any]) -> bool:
    if replay_result.get("replay_status") != "COMPLETE":
        return False
    return bool(SUCCESS_RE.search(_acceptance_text(project_root)))


def _task_map(project_root: Path) -> dict[str, dict[str, str]]:
    text = (project_root / contracts.CHECKLIST_NAME).read_text(encoding="utf-8")
    return {task["ID"]: task for task in contracts.checklist_tasks(text)}


def _project_specific(task: dict[str, str], project_id: str, explicit: bool) -> bool:
    if explicit:
        return True
    corpus = " ".join(task.get(key, "") for key in ("阶段/任务", "前置条件", "完成证据", "阻塞/备注", "下一步"))
    return bool(TASK_SIGNAL_RE.search(corpus) or project_id in corpus or PATH_RE.search(corpus))


def _task_evidence_valid(project_root: Path, task: dict[str, str], evidence_gaps: list[dict[str, Any]]) -> bool:
    if task.get("状态") != "已完成" or task.get("核验状态") != "已核验":
        return False
    evidence = task.get("完成证据", "").strip()
    if not evidence or evidence in {"—", "-", "无"}:
        return False
    task_id = task.get("ID")
    return not any(item.get("task_id") == task_id for item in evidence_gaps)


def _risk_for(operation: str, classification: str) -> str:
    if classification == "NEW_TEMPLATE_CANDIDATE":
        return "HIGH"
    if classification == "NEW_MODULE_CANDIDATE":
        return "HIGH"
    if operation in {"ADD_TASK", "REMOVE_TASK", "CHANGE_DEPENDENCY", "CHANGE_COMPLETION_REQUIREMENT", "CHANGE_EVIDENCE_REQUIREMENT"}:
        return "HIGH"
    if operation in {"CHANGE_ORDER", "CHANGE_OWNER"}:
        return "MEDIUM"
    return "LOW"


def _required_validations(risk: str) -> int:
    return {"LOW": 1, "MEDIUM": 2, "HIGH": 2, "CRITICAL": 2}[risk]


def _normalize_task(task: dict[str, str]) -> dict[str, str]:
    keys = ("ID", "阶段/任务", "主责智能体", "前置条件", "优先级", "主线", "状态", "核验状态", "完成证据", "阻塞/备注", "下一步")
    return {key: task.get(key, "").strip() for key in keys if key in task}


def _classification_records(
    project_root: Path,
    replay_result: dict[str, Any],
    metadata: dict[str, Any],
    explicit_project_specific: bool,
    requested_type: str | None,
    request_new_template: bool,
) -> list[dict[str, Any]]:
    tasks = _task_map(project_root)
    evidence_gaps = replay_result.get("differences", {}).get("evidence_gaps", [])
    acceptance = _has_acceptance(project_root, replay_result)
    project_id = str(metadata.get("project_id", project_root.name))
    records: list[dict[str, Any]] = []

    for task_id in replay_result.get("differences", {}).get("added_tasks", []):
        task = tasks.get(task_id, {"ID": task_id, "阶段/任务": task_id})
        specific = _project_specific(task, project_id, explicit_project_specific)
        valid = _task_evidence_valid(project_root, task, evidence_gaps)
        classification = "PROJECT_EXCEPTION" if specific else "TEMPLATE_MISSING"
        candidate_type = classification
        reason = "项目明确标记为特例" if specific else "实际清单出现模板基线之外的任务"
        if requested_type == "NEW_MODULE_CANDIDATE" and not specific:
            classification = candidate_type = "NEW_MODULE_CANDIDATE"
            reason = "用户明确请求将该结构作为模块候选"
        records.append({
            "classification": classification,
            "candidate_type": candidate_type,
            "operation": "ADD_TASK",
            "task_id": task_id,
            "task": _normalize_task(task),
            "eligible": bool(valid and acceptance and not specific),
            "evidence_sufficient": valid,
            "risk_level": _risk_for("ADD_TASK", classification),
            "reason": reason if acceptance else f"{reason}；缺少项目或阶段验收结果",
            "evidence_gaps": [item for item in evidence_gaps if item.get("task_id") == task_id],
        })

    for change_key, operation in (
        ("changed_dependencies", "CHANGE_DEPENDENCY"),
        ("changed_owners", "CHANGE_OWNER"),
        ("changed_completion_requirements", "CHANGE_COMPLETION_REQUIREMENT"),
    ):
        for change in replay_result.get("differences", {}).get(change_key, []):
            task = tasks.get(change.get("task_id", ""), {"ID": change.get("task_id", "")})
            specific = _project_specific(task, project_id, explicit_project_specific)
            valid = _task_evidence_valid(project_root, task, evidence_gaps)
            classification = "PROJECT_EXCEPTION" if specific else "REUSABLE_IMPROVEMENT"
            candidate_type = classification
            if requested_type == "NEW_MODULE_CANDIDATE" and not specific:
                classification = candidate_type = "NEW_MODULE_CANDIDATE"
            records.append({
                "classification": classification,
                "candidate_type": candidate_type,
                "operation": operation,
                "task_id": change.get("task_id", ""),
                "task": _normalize_task(task),
                "change": copy.deepcopy(change),
                "eligible": bool(valid and acceptance and not specific),
                "evidence_sufficient": valid,
                "risk_level": _risk_for(operation, classification),
                "reason": "事实差异已完成、核验且有证据" if valid and acceptance else "缺少完成、核验、证据或验收条件",
                "evidence_gaps": [item for item in evidence_gaps if item.get("task_id") == change.get("task_id")],
            })

    for task_id in replay_result.get("differences", {}).get("skipped_tasks", []) + replay_result.get("differences", {}).get("deprecated_tasks", []):
        task = tasks.get(task_id, {"ID": task_id})
        records.append({
            "classification": "TEMPLATE_REDUNDANT",
            "candidate_type": "TEMPLATE_REDUNDANT",
            "operation": "REMOVE_TASK",
            "task_id": task_id,
            "task": _normalize_task(task),
            "eligible": False,
            "evidence_sufficient": False,
            "risk_level": "HIGH",
            "reason": "单个项目跳过或废弃不足以证明模板冗余",
            "evidence_gaps": [],
        })

    for event in replay_result.get("differences", {}).get("rework_events", []):
        records.append({
            "classification": "INCIDENTAL_ISSUE",
            "candidate_type": "INCIDENTAL_ISSUE",
            "operation": "NO_ACTION",
            "task_id": (event.get("task_ids") or [""])[0],
            "event": copy.deepcopy(event),
            "eligible": False,
            "evidence_sufficient": False,
            "risk_level": "MEDIUM",
            "reason": "返工本身不能证明模板存在问题",
            "evidence_gaps": [],
        })

    for event in replay_result.get("differences", {}).get("blocking_events", []):
        records.append({
            "classification": "INCIDENTAL_ISSUE",
            "candidate_type": "INCIDENTAL_ISSUE",
            "operation": "NO_ACTION",
            "task_id": event.get("task_id", (event.get("task_ids") or [""])[0]),
            "event": copy.deepcopy(event),
            "eligible": False,
            "evidence_sufficient": False,
            "risk_level": "MEDIUM",
            "reason": "阻塞记录属于事实事件，尚不足以证明模板问题",
            "evidence_gaps": [],
        })

    if request_new_template:
        generic = metadata.get("template", {}).get("template_id") == "generic-project"
        if generic and _has_acceptance(project_root, replay_result):
            records.append({
                "classification": "NEW_TEMPLATE_CANDIDATE",
                "candidate_type": "NEW_TEMPLATE_CANDIDATE",
                "operation": "ADD_TASK",
                "task_id": "",
                "task": {},
                "eligible": True,
                "evidence_sufficient": True,
                "risk_level": "HIGH",
                "reason": "用户明确请求，当前项目使用 generic-project 且已验收",
                "evidence_gaps": [],
            })
        else:
            records.append({
                "classification": "NEW_TEMPLATE_CANDIDATE",
                "candidate_type": "NEW_TEMPLATE_CANDIDATE",
                "operation": "NO_ACTION",
                "task_id": "",
                "task": {},
                "eligible": False,
                "evidence_sufficient": False,
                "risk_level": "HIGH",
                "reason": "新模板候选需要 generic-project、完整验收和用户明确请求",
                "evidence_gaps": ["generic-project/验收条件不足"],
            })

    if not records:
        records.append({
            "classification": "NO_ACTION",
            "candidate_type": "NO_ACTION",
            "operation": "NO_ACTION",
            "task_id": "",
            "eligible": False,
            "evidence_sufficient": False,
            "risk_level": "LOW",
            "reason": "回放没有产生可分类差异",
            "evidence_gaps": [],
        })

    return sorted(records, key=lambda item: (
        item.get("classification", ""),
        item.get("operation", ""),
        item.get("task_id", ""),
        contracts.canonical_json(item),
    ))


def _replay_digest(result: dict[str, Any]) -> str:
    return contracts.sha256_digest(contracts.canonical_json(result))


def _structure_payload(
    record: dict[str, Any],
    metadata: dict[str, Any],
    replay_result: dict[str, Any],
) -> dict[str, Any]:
    template = metadata.get("template", {})
    return {
        "candidate_type": record["candidate_type"],
        "source_template_id": template.get("template_id", ""),
        "source_template_version": template.get("template_version", ""),
        "source_modules": [
            {
                "module_id": item.get("module_id", ""),
                "module_version": item.get("module_version", ""),
                "module_digest": item.get("module_digest", ""),
            }
            for item in metadata.get("modules", [])
        ],
        "involved_task_ids": [record.get("task_id", "")] if record.get("task_id") else replay_result.get("expected_task_ids", []),
        "operation": record["operation"],
        "task": record.get("task", {}),
        "change": record.get("change", {}),
        "event": record.get("event", {}),
    }


def _structure_signature(payload: dict[str, Any]) -> str:
    return contracts.sha256_digest(contracts.canonical_json(payload))


def _candidate_payload(
    record: dict[str, Any],
    metadata: dict[str, Any],
    checklist_digest: str,
    replay_result: dict[str, Any],
    project_root: Path,
    now: dt.datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    template = metadata["template"]
    structure = _structure_payload(record, metadata, replay_result)
    signature = _structure_signature(structure)
    risk = record["risk_level"]
    accepted = _has_acceptance(project_root, replay_result)
    current_validations = 1 if accepted else 0
    structural = {
        "candidate_type": record["candidate_type"],
        "risk_level": risk,
        "source_template_id": template["template_id"],
        "source_template_version": template["template_version"],
        "source_template_digest": template["template_digest"],
        "structure_signature": signature,
        "proposed_change": {
            "operation": record["operation"],
            "task_id": record.get("task_id", ""),
            "task": record.get("task", {}),
            "change": record.get("change", {}),
        },
    }
    candidate_id = contracts.candidate_id(structural, now=now)
    timestamp = _rfc3339(now)
    payload = {
        "candidate_schema_version": contracts.CANDIDATE_SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "candidate_type": record["candidate_type"],
        "status": "PROPOSED",
        "risk_level": risk,
        "source_template_id": template["template_id"],
        "source_template_version": template["template_version"],
        "source_template_digest": template["template_digest"],
        "source_project_ids": [metadata["project_id"]],
        "source_checklist_digests": [checklist_digest],
        "replay_digest": _replay_digest(replay_result),
        "structure_signature": signature,
        "structure_payload": structure,
        "proposed_change": structural["proposed_change"],
        "required_validation_count": _required_validations(risk),
        "current_validation_count": current_validations,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    return payload, structure


def _candidate_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(path for path in root.iterdir() if path.is_dir() and path.name.startswith("cand-"))


def _find_duplicate(root: Path, signature: str) -> tuple[Path, dict[str, Any]] | None:
    for directory in _candidate_dirs(root):
        path = directory / "candidate.json"
        if not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            contracts.validate_candidate(value)
        except (OSError, json.JSONDecodeError, contracts.ContractError):
            continue
        if value.get("structure_signature") == signature:
            return directory, value
    return None


def _candidate_markdown(payload: dict[str, Any], record: dict[str, Any]) -> dict[str, str]:
    candidate_id = payload["candidate_id"]
    return {
        "CANDIDATE.md": "\n".join([
            f"# Workflow Candidate {candidate_id}",
            "",
            f"- Classification: {record['classification']}",
            f"- Risk: {payload['risk_level']}",
            f"- Status: {payload['status']}",
            f"- Reason: {record['reason']}",
            f"- Required validations: {payload['required_validation_count']}",
            f"- Current validations: {payload['current_validation_count']}",
            "",
            "This candidate is proposal-only. It must not be automatically applied or promoted.",
            "",
        ]),
        "source-projects.md": "\n".join([
            "# Source Projects",
            "",
            f"- Project ID: {payload['source_project_ids'][0]}",
            f"- Checklist digest: {payload['source_checklist_digests'][0]}",
            f"- Replay digest: {payload['replay_digest']}",
            "",
        ]),
        "template-diff.md": "\n".join([
            "# Template Diff",
            "",
            f"- Operation: {payload['proposed_change']['operation']}",
            f"- Task ID: {payload['proposed_change'].get('task_id') or '—'}",
            f"- Classification: {record['classification']}",
            "",
            "No formal template or module file is modified by P3-02.",
            "",
        ]),
        "evidence.md": "\n".join([
            "# Evidence",
            "",
            f"- Source checklist digest: {payload['source_checklist_digests'][0]}",
            f"- Replay digest: {payload['replay_digest']}",
            f"- Acceptance detected: {'YES' if payload['current_validation_count'] else 'NO'}",
            f"- Evidence gaps: {', '.join(str(item) for item in record.get('evidence_gaps', [])) or 'none'}",
            "",
        ]),
        "validation.md": "\n".join([
            "# Validation",
            "",
            f"- Current validation count: {payload['current_validation_count']}",
            f"- Required validation count: {payload['required_validation_count']}",
            f"- Status: {payload['status']}",
            f"- Next validation: run a separate successful project or phase validation.",
            "",
        ]),
    }


def _merge_source(existing: dict[str, Any], incoming: dict[str, Any], now: dt.datetime) -> dict[str, Any]:
    merged = copy.deepcopy(existing)
    for key in ("source_project_ids", "source_checklist_digests"):
        values = list(dict.fromkeys(list(existing.get(key, [])) + list(incoming.get(key, []))))
        merged[key] = values
    existing_projects = set(existing.get("source_project_ids", []))
    new_project = incoming.get("source_project_ids", [None])[0]
    if new_project and new_project not in existing_projects and incoming.get("current_validation_count", 0):
        merged["current_validation_count"] = int(existing.get("current_validation_count", 0)) + 1
    merged["updated_at"] = _rfc3339(now)
    merged["status"] = existing.get("status", "PROPOSED")
    return merged


def _write_package(directory: Path, payload: dict[str, Any], record: dict[str, Any]) -> None:
    temp = directory.parent / f".tmp-{directory.name}-{os.getpid()}"
    parent_preexisting = directory.parent.exists()
    if temp.exists():
        shutil.rmtree(temp)
    try:
        temp.mkdir(parents=True)
        contracts.validate_candidate(payload)
        contracts.atomic_write_text(temp / "candidate.json", contracts.canonical_json(payload))
        for name, text in _candidate_markdown(payload, record).items():
            contracts.atomic_write_text(temp / name, text)
        if directory.exists():
            raise FileExistsError(directory)
        temp.rename(directory)
    except Exception:
        if temp.exists():
            shutil.rmtree(temp)
        if not parent_preexisting and directory.parent.exists() and not any(directory.parent.iterdir()):
            directory.parent.rmdir()
        raise


def generate_candidates(
    skill_root: Path,
    project_root: Path,
    *,
    apply: bool = False,
    candidate_type: str | None = None,
    mark_project_specific: bool = False,
    request_new_template: bool = False,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    metadata, checklist_digest = _source_checklist(skill_root, project_root)
    replay_result = replay.replay_project(skill_root, project_root)
    if replay_result.get("replay_status") in {"DIGEST_MISMATCH", "INVALID_BINDING", "INVALID_CHECKLIST"}:
        return {
            "mode": "apply" if apply else "preview",
            "replay_status": replay_result.get("replay_status"),
            "candidate_eligible": False,
            "classifications": [],
            "candidates": [],
            "warnings": replay_result.get("warnings", []),
            "writes": False,
        }
    if candidate_type and candidate_type not in CLASSIFICATIONS:
        raise CandidateGenerationError(f"unsupported candidate type: {candidate_type}")
    records = _classification_records(
        project_root,
        replay_result,
        metadata,
        mark_project_specific,
        candidate_type,
        request_new_template,
    )
    if candidate_type:
        for record in records:
            if record["classification"] not in {"NO_ACTION", "INCIDENTAL_ISSUE", "PROJECT_EXCEPTION"}:
                record["candidate_type"] = candidate_type
    now = now or dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    candidate_root = _candidate_root(skill_root)
    outputs: list[dict[str, Any]] = []
    for record in records:
        if not record.get("eligible"):
            continue
        payload, structure = _candidate_payload(record, metadata, checklist_digest, replay_result, project_root, now)
        duplicate = _find_duplicate(candidate_root, payload["structure_signature"])
        item = {
            "classification": record["classification"],
            "candidate_type": payload["candidate_type"],
            "candidate_id": payload["candidate_id"],
            "structure_signature": payload["structure_signature"],
            "risk_level": payload["risk_level"],
            "eligible": True,
            "duplicate_candidate_id": duplicate[1]["candidate_id"] if duplicate else None,
            "evidence_gaps": record.get("evidence_gaps", []),
            "suggested_action": "merge-existing" if duplicate else ("write-candidate" if apply else "apply-to-create"),
        }
        if apply:
            if duplicate:
                directory, existing = duplicate
                merged = _merge_source(existing, payload, now)
                contracts.validate_candidate(merged)
                contracts.atomic_write_text(directory / "candidate.json", contracts.canonical_json(merged))
                source_text = (directory / "source-projects.md").read_text(encoding="utf-8") if (directory / "source-projects.md").is_file() else "# Source Projects\n\n"
                addition = f"- Project ID: {metadata['project_id']}\\n- Checklist digest: {checklist_digest}\\n- Replay digest: {_replay_digest(replay_result)}\\n"
                if metadata["project_id"] not in source_text:
                    contracts.atomic_write_text(directory / "source-projects.md", source_text.rstrip() + "\\n" + addition)
                item["applied"] = True
            else:
                try:
                    _write_package(candidate_root / payload["candidate_id"], payload, record)
                    item["applied"] = True
                except FileExistsError:
                    duplicate_after_race = _find_duplicate(candidate_root, payload["structure_signature"])
                    if not duplicate_after_race:
                        raise
                    item["duplicate_candidate_id"] = duplicate_after_race[1]["candidate_id"]
                    item["applied"] = False
        outputs.append(item)
    return {
        "mode": "apply" if apply else "preview",
        "replay_status": replay_result.get("replay_status"),
        "candidate_eligible": bool(outputs),
        "classifications": records,
        "candidates": outputs,
        "warnings": replay_result.get("warnings", []),
        "writes": bool(apply and outputs),
    }


def _summary(result: dict[str, Any]) -> str:
    return "\n".join([
        f"Mode: {result['mode']}",
        f"Replay status: {result.get('replay_status', 'UNKNOWN')}",
        f"Candidate eligible: {'YES' if result.get('candidate_eligible') else 'NO'}",
        f"Candidates: {len(result.get('candidates', []))}",
        f"Writes: {'YES' if result.get('writes') else 'NO'}",
        f"Warnings: {len(result.get('warnings', []))}",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preview", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--skill-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--format", choices=("json", "summary"), default="json")
    parser.add_argument("--candidate-type")
    parser.add_argument("--mark-project-specific", action="store_true")
    parser.add_argument("--request-new-template", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = generate_candidates(
            Path(args.skill_root).expanduser(),
            Path(args.project_root).expanduser(),
            apply=args.apply,
            candidate_type=args.candidate_type,
            mark_project_specific=args.mark_project_specific,
            request_new_template=args.request_new_template,
        )
    except (CandidateGenerationError, contracts.ContractError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.format == "summary":
        print(_summary(result))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
