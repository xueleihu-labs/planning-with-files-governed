#!/usr/bin/env python3
"""Deterministic candidate validation and evidence accumulation for P4-01."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import workflow_candidate_generator as generator
import workflow_contracts as contracts
import workflow_replay as replay


RESULTS = {"PASS", "FAIL", "INCONCLUSIVE", "INVALID"}
VALIDATION_STATUSES = {"PROPOSED", "VALIDATING"}
FORBIDDEN_STATUSES = {"APPROVED", "APPLIED", "REJECTED", "SUPERSEDED"}


class CandidateValidationError(ValueError):
    """Expected validation failure."""


def _now(value: str | None = None) -> dt.datetime:
    if value:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc).replace(microsecond=0)
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _rfc3339(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _candidate_path(candidate_dir: Path) -> Path:
    return candidate_dir / "candidate.json"


def _load_candidate(candidate_dir: Path) -> dict[str, Any]:
    path = _candidate_path(candidate_dir)
    if not candidate_dir.is_dir() or not path.is_file():
        raise CandidateValidationError("candidate directory or candidate.json is missing")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateValidationError(f"invalid candidate.json: {exc}") from exc
    contracts.validate_candidate(value)
    if candidate_dir.name != value["candidate_id"]:
        raise CandidateValidationError("candidate directory name does not match candidate_id")
    if value.get("status") not in VALIDATION_STATUSES:
        raise CandidateValidationError(f"candidate status cannot be validated: {value.get('status')}")
    required_files = {"CANDIDATE.md", "source-projects.md", "template-diff.md", "evidence.md", "validation.md"}
    missing = sorted(name for name in required_files if not (candidate_dir / name).is_file())
    if missing:
        raise CandidateValidationError("candidate package missing files: " + ", ".join(missing))
    return value


def _expected_candidate_id(value: dict[str, Any]) -> str:
    structural = {
        "candidate_type": value.get("candidate_type", ""),
        "risk_level": value.get("risk_level", ""),
        "source_template_id": value.get("source_template_id", ""),
        "source_template_version": value.get("source_template_version", ""),
        "source_template_digest": value.get("source_template_digest", ""),
        "structure_signature": value.get("structure_signature", ""),
        "proposed_change": value.get("proposed_change", {}),
    }
    return contracts.candidate_id(structural, now=dt.datetime.fromisoformat(value["created_at"].replace("Z", "+00:00")))


def _structure_valid(value: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    payload = value.get("structure_payload")
    signature = value.get("structure_signature")
    if not isinstance(payload, dict) or not isinstance(signature, str):
        return ["structure_payload and structure_signature are required"]
    if contracts.sha256_digest(contracts.canonical_json(payload)) != signature:
        errors.append("structure_signature does not match structure_payload")
    try:
        if _expected_candidate_id(value) != value.get("candidate_id"):
            errors.append("candidate_id does not match stable candidate content")
    except (KeyError, ValueError, contracts.ContractError):
        errors.append("candidate_id content cannot be recomputed")
    return errors


def _project_acceptance(project_root: Path, replay_result: dict[str, Any]) -> bool:
    if replay_result.get("replay_status") != "COMPLETE":
        return False
    return generator._has_acceptance(project_root, replay_result)


def _task_for(candidate: dict[str, Any], project_root: Path) -> tuple[dict[str, str] | None, list[dict[str, Any]]]:
    checklist = project_root / contracts.CHECKLIST_NAME
    text = checklist.read_text(encoding="utf-8")
    tasks = {item["ID"]: item for item in contracts.checklist_tasks(text)}
    change = candidate.get("proposed_change", {})
    task_id = change.get("task_id", "")
    if not task_id:
        return None, []
    task = tasks.get(task_id)
    return task, [] if task else [{"task_id": task_id, "type": "task_missing_from_source_checklist"}]


def _source_integrity(candidate: dict[str, Any], skill_root: Path, project_root: Path) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    checklist_path = project_root / contracts.CHECKLIST_NAME
    if not checklist_path.is_file():
        return {}, ["source checklist is missing"]
    try:
        metadata = contracts.validate_checklist_text(checklist_path.read_text(encoding="utf-8"))
    except (OSError, contracts.ContractError) as exc:
        return {}, [f"source checklist is invalid: {exc}"]
    source_project_id = str(metadata.get("project_id", ""))
    known_source = source_project_id in candidate.get("source_project_ids", [])
    checklist_digest = contracts.file_digest(checklist_path)
    if known_source and checklist_digest not in candidate.get("source_checklist_digests", []):
        errors.append("source checklist digest does not match candidate")
    replay_result = replay.replay_project(skill_root, project_root)
    if known_source and candidate.get("replay_digest") and generator._replay_digest(replay_result) != candidate.get("replay_digest"):
        errors.append("replay_digest does not match current source replay")
    if replay_result.get("replay_status") in {"DIGEST_MISMATCH", "INVALID_BINDING", "INVALID_CHECKLIST"}:
        errors.append("source replay is not valid: " + str(replay_result.get("replay_status")))
    template = metadata.get("template", {})
    for key in ("template_id", "template_version", "template_digest"):
        candidate_key = "source_template_" + key.removeprefix("template_")
        if candidate.get(candidate_key) != template.get(key):
            errors.append(f"candidate/source {key} mismatch")
    return {"metadata": metadata, "replay": replay_result, "checklist_digest": checklist_digest}, errors


def _evidence_errors(candidate: dict[str, Any], source: dict[str, Any], project_root: Path) -> list[str]:
    errors: list[str] = []
    replay_result = source.get("replay", {})
    task, task_errors = _task_for(candidate, project_root)
    errors.extend(str(item.get("type", item)) for item in task_errors)
    if task is not None:
        if task.get("状态") != "已完成":
            errors.append("source task is not completed")
        if task.get("核验状态") != "已核验":
            errors.append("source task is not verified")
        evidence = task.get("完成证据", "").strip()
        if not evidence or evidence in {"—", "-", "无"}:
            errors.append("source task has no completion evidence")
        elif any(gap.get("task_id") == task.get("ID") for gap in replay_result.get("differences", {}).get("evidence_gaps", [])):
            errors.append("source task evidence is incomplete or invalid")
    change = candidate.get("proposed_change", {})
    operation = change.get("operation")
    diff = replay_result.get("differences", {})
    if operation == "ADD_TASK" and change.get("task_id") not in diff.get("added_tasks", []):
        errors.append("candidate ADD_TASK is not present in replay added_tasks")
    mapping = {
        "CHANGE_DEPENDENCY": "changed_dependencies",
        "CHANGE_OWNER": "changed_owners",
        "CHANGE_COMPLETION_REQUIREMENT": "changed_completion_requirements",
    }
    if operation in mapping and not any(item.get("task_id") == change.get("task_id") for item in diff.get(mapping[operation], [])):
        errors.append("candidate change is not present in replay differences")
    if not _project_acceptance(project_root, replay_result):
        errors.append("source project or phase acceptance is not valid")
    return sorted(set(errors))


def validate_candidate_dir(candidate_dir: Path, skill_root: Path, source_project_root: Path) -> dict[str, Any]:
    """Read-only integrity and evidence validation for one candidate."""
    try:
        candidate = _load_candidate(candidate_dir)
    except CandidateValidationError as exc:
        return {"valid": False, "result": "INVALID", "errors": [str(exc)], "candidate_status": None}
    errors = _structure_valid(candidate)
    source, source_errors = _source_integrity(candidate, skill_root, source_project_root)
    errors.extend(source_errors)
    if not errors:
        errors.extend(_evidence_errors(candidate, source, source_project_root))
    return {
        "valid": not errors,
        "result": "PASS" if not errors else "INCONCLUSIVE",
        "errors": sorted(set(errors)),
        "candidate": candidate,
        "candidate_status": candidate.get("status"),
        "source_project_id": source.get("metadata", {}).get("project_id"),
        "source_checklist_digest": source.get("checklist_digest"),
        "replay": source.get("replay", {}),
    }


def _existing_entry(candidate: dict[str, Any], project_id: str, checklist_digest: str) -> dict[str, Any] | None:
    for entry in candidate.get("validation_history", []):
        if entry.get("source_project_id") == project_id and entry.get("source_checklist_digest") == checklist_digest:
            return entry
    return None


def _approval_exists(candidate: dict[str, Any], approval_record: Path | None) -> bool:
    if approval_record and approval_record.is_file():
        try:
            value = json.loads(approval_record.read_text(encoding="utf-8-sig"))
            return value.get("candidate_id") == candidate.get("candidate_id") and value.get("approved") is True
        except (OSError, json.JSONDecodeError):
            return False
    return any(item.get("candidate_id") == candidate.get("candidate_id") and item.get("approved") is True for item in candidate.get("approval_records", []))


def _approval_ready(candidate: dict[str, Any], approval_record: Path | None = None) -> bool:
    enough = int(candidate.get("current_validation_count", 0)) >= int(candidate.get("required_validation_count", 1))
    if candidate.get("risk_level") == "CRITICAL":
        return enough and _approval_exists(candidate, approval_record)
    return enough


def _apply_payload(candidate: dict[str, Any], validation: dict[str, Any], result: str, reason: str, evidence_ref: str | None, now: dt.datetime, approval_record: Path | None) -> tuple[dict[str, Any], dict[str, Any]]:
    project_id = validation.get("source_project_id") or ""
    checklist_digest = validation.get("source_checklist_digest") or ""
    entry = {
        "result": result,
        "source_project_id": project_id,
        "source_checklist_digest": checklist_digest,
        "reason": reason,
        "evidence_ref": evidence_ref or "",
        "recorded_at": _rfc3339(now),
        "counted": False,
    }
    updated = copy.deepcopy(candidate)
    if project_id and project_id not in updated.setdefault("source_project_ids", []):
        updated["source_project_ids"].append(project_id)
    if checklist_digest and checklist_digest not in updated.setdefault("source_checklist_digests", []):
        updated["source_checklist_digests"].append(checklist_digest)
    history = list(updated.get("validation_history", []))
    previous = _existing_entry(updated, project_id, checklist_digest)
    conflict = False
    if previous and previous.get("result") == "PASS" and result == "FAIL":
        conflict = True
        updated.setdefault("validation_conflicts", []).append(copy.deepcopy(entry))
    elif not previous:
        if result == "PASS":
            successful_projects = set(updated.get("successful_source_project_ids", []))
            # P3-02 records one successful source project in the initial count;
            # materialize that provenance before accepting a P4 submission.
            if not successful_projects and int(updated.get("current_validation_count", 0)) > 0:
                successful_projects.update(updated.get("source_project_ids", []))
                updated["successful_source_project_ids"] = sorted(successful_projects)
            if project_id not in successful_projects:
                updated["current_validation_count"] = int(updated.get("current_validation_count", 0)) + 1
                updated.setdefault("successful_source_project_ids", []).append(project_id)
                entry["counted"] = True
        history.append(entry)
    elif previous.get("result") == "INCONCLUSIVE" and result == "PASS":
        history.append(entry)
        successful_projects = set(updated.get("successful_source_project_ids", []))
        if not successful_projects and int(updated.get("current_validation_count", 0)) > 0:
            successful_projects.update(updated.get("source_project_ids", []))
            updated["successful_source_project_ids"] = sorted(successful_projects)
        if project_id not in successful_projects:
            updated["current_validation_count"] = int(updated.get("current_validation_count", 0)) + 1
            updated.setdefault("successful_source_project_ids", []).append(project_id)
            entry["counted"] = True
    updated["validation_history"] = history
    failures = {item.get("source_project_id") for item in history if item.get("result") == "FAIL"}
    if result == "FAIL" and not conflict:
        failures.add(project_id)
    if len(failures) >= 2:
        updated["status"] = "REJECTED"
    elif updated.get("status") == "PROPOSED":
        updated["status"] = "VALIDATING"
    updated["approval_ready"] = _approval_ready(updated, approval_record)
    updated["updated_at"] = _rfc3339(now)
    return updated, entry


def _append_markdown(candidate_dir: Path, candidate: dict[str, Any], entry: dict[str, Any]) -> None:
    source_path = candidate_dir / "source-projects.md"
    source = source_path.read_text(encoding="utf-8") if source_path.is_file() else "# Source Projects\n"
    line = f"- Validation source: {entry['source_project_id']} ({entry['result']}); checklist: {entry['source_checklist_digest']}"
    if line not in source:
        contracts.atomic_write_text(source_path, source.rstrip() + "\n" + line + "\n")
    evidence_path = candidate_dir / "evidence.md"
    evidence = evidence_path.read_text(encoding="utf-8") if evidence_path.is_file() else "# Evidence\n"
    evidence_line = f"- Validation evidence: {entry['result']} | {entry['source_project_id']} | {entry['evidence_ref'] or 'recorded source evidence'}"
    if evidence_line not in evidence:
        contracts.atomic_write_text(evidence_path, evidence.rstrip() + "\n" + evidence_line + "\n")
    validation_path = candidate_dir / "validation.md"
    body = "# Validation\n\n" + "\n".join([
        f"- Current validation count: {candidate.get('current_validation_count', 0)}",
        f"- Required validation count: {candidate.get('required_validation_count', 1)}",
        f"- Status: {candidate.get('status')}",
        f"- Approval ready: {'YES' if candidate.get('approval_ready') else 'NO'}",
        f"- Last result: {entry['result']} ({entry['source_project_id']})",
        "",
    ])
    contracts.atomic_write_text(validation_path, body)


def apply_validation(candidate_dir: Path, skill_root: Path, source_project_root: Path, *, result: str, reason: str = "", evidence_ref: str | None = None, approval_record: Path | None = None, now: dt.datetime | None = None, agent_id: str = "Codex") -> dict[str, Any]:
    if result not in RESULTS:
        raise CandidateValidationError(f"unsupported validation result: {result}")
    candidate = _load_candidate(candidate_dir)
    before_digest = contracts.file_digest(_candidate_path(candidate_dir))
    validation = validate_candidate_dir(candidate_dir, skill_root, source_project_root)
    if result == "PASS" and not validation.get("valid"):
        raise CandidateValidationError("PASS rejected: " + "; ".join(validation.get("errors", [])))
    now = now or dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    conflicts = skill_root / ".planning" / "conflicts"
    lock_path = candidate_dir / ".validation.lock"
    lock = contracts.acquire_workflow_lock(lock_path, f"candidates/{candidate_dir.name}/candidate.json", before_digest, agent_id, conflicts)
    original_files: dict[Path, bytes] = {}
    for name in ("candidate.json", "source-projects.md", "evidence.md", "validation.md"):
        path = candidate_dir / name
        if path.is_file():
            original_files[path] = path.read_bytes()
    try:
        current_digest = contracts.file_digest(_candidate_path(candidate_dir))
        if current_digest != before_digest:
            raise CandidateValidationError("candidate digest changed before validation write")
        current = _load_candidate(candidate_dir)
        validation = validate_candidate_dir(candidate_dir, skill_root, source_project_root)
        updated, entry = _apply_payload(current, validation, result, reason, evidence_ref, now, approval_record)
        contracts.validate_candidate(updated)
        contracts.atomic_write_text(_candidate_path(candidate_dir), contracts.canonical_json(updated))
        _append_markdown(candidate_dir, updated, entry)
    except Exception:
        # Restore every touched file if a later package member fails.  The
        # contract layer still performs the normal atomic replacement for each
        # successful write; this rollback prevents a half-updated package.
        for path, content in original_files.items():
            path.write_bytes(content)
        raise
    finally:
        contracts.release_lock(lock_path, process_id=lock["process_id"], host_name=lock["host_name"])
    return {
        "mode": "apply",
        "result": result,
        "candidate_id": updated["candidate_id"],
        "candidate_status": updated["status"],
        "current_validation_count": updated.get("current_validation_count", 0),
        "required_validation_count": updated.get("required_validation_count", 1),
        "approval_ready": updated.get("approval_ready", False),
        "counted": entry.get("counted", False),
        "validation_entry": entry,
        "writes": True,
    }


def validate_submission(candidate_dir: Path, skill_root: Path, source_project_root: Path, *, result: str, reason: str = "", evidence_ref: str | None = None, approval_record: Path | None = None) -> dict[str, Any]:
    if result not in RESULTS:
        raise CandidateValidationError(f"unsupported validation result: {result}")
    validation = validate_candidate_dir(candidate_dir, skill_root, source_project_root)
    candidate = validation.get("candidate", {})
    if result == "PASS" and not validation.get("valid"):
        effective = "INCONCLUSIVE"
    else:
        effective = result
    entry = {
        "result": effective,
        "source_project_id": validation.get("source_project_id", ""),
        "source_checklist_digest": validation.get("source_checklist_digest", ""),
        "reason": reason,
        "evidence_ref": evidence_ref or "",
        "counted": False,
    }
    existing = _existing_entry(candidate, entry["source_project_id"], entry["source_checklist_digest"]) if candidate else None
    return {
        "mode": "preview",
        "result": effective,
        "requested_result": result,
        "candidate_id": candidate.get("candidate_id"),
        "candidate_status": candidate.get("status"),
        "valid": validation.get("valid", False),
        "errors": validation.get("errors", []),
        "source_project_id": entry["source_project_id"],
        "current_validation_count": candidate.get("current_validation_count", 0),
        "required_validation_count": candidate.get("required_validation_count", 1),
        "approval_ready": _approval_ready(candidate, approval_record) if candidate else False,
        "duplicate_submission": bool(existing),
        "validation_entry": entry,
        "writes": False,
    }


def _summary(result: dict[str, Any]) -> str:
    return "\n".join([
        f"Mode: {result.get('mode')}",
        f"Candidate: {result.get('candidate_id', 'unknown')}",
        f"Result: {result.get('result')}",
        f"Status: {result.get('candidate_status', 'unknown')}",
        f"Validation count: {result.get('current_validation_count', 0)}/{result.get('required_validation_count', 1)}",
        f"Approval ready: {'YES' if result.get('approval_ready') else 'NO'}",
        f"Writes: {'YES' if result.get('writes') else 'NO'}",
        f"Errors: {len(result.get('errors', []))}",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preview", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--candidate-dir", required=True)
    parser.add_argument("--source-project-root", required=True)
    parser.add_argument("--skill-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--result", choices=sorted(RESULTS), required=True)
    parser.add_argument("--reason", default="")
    parser.add_argument("--evidence-ref")
    parser.add_argument("--approval-record")
    parser.add_argument("--format", choices=("json", "summary"), default="json")
    args = parser.parse_args(argv)
    try:
        candidate_dir = Path(args.candidate_dir).expanduser()
        skill_root = Path(args.skill_root).expanduser()
        source_project_root = Path(args.source_project_root).expanduser()
        approval = Path(args.approval_record).expanduser() if args.approval_record else None
        if args.apply:
            result = apply_validation(candidate_dir, skill_root, source_project_root, result=args.result, reason=args.reason, evidence_ref=args.evidence_ref, approval_record=approval)
        else:
            result = validate_submission(candidate_dir, skill_root, source_project_root, result=args.result, reason=args.reason, evidence_ref=args.evidence_ref, approval_record=approval)
    except (CandidateValidationError, contracts.ContractError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(_summary(result) if args.format == "summary" else json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
