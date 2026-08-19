"""Gate 2 extracted module: checkpoints.py.

Generated from the Gate 1 planning.py baseline.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from typing import Iterable
import copy
import datetime as dt
import re

from pwf_governed._legacy import (
    governance_profiles,
    plan_contracts,
    workflow_contracts,
)
from pwf_governed._legacy import governance_profiles as governance
from pwf_governed._legacy import plan_contracts as contracts
from pwf_governed._legacy import workflow_contracts as workflow
from pwf_governed._legacy.checkpoint_reader import (
    read_head,
)
from pwf_governed._legacy.workflow_contracts import (
    candidate_id,
)

from pwf_governed._legacy import (
    governance_profiles,
    plan_contracts,
    workflow_contracts,
)
from pwf_governed._legacy.checkpoint_reader import (
    read_head,
)
from pwf_governed._legacy.workflow_contracts import (
    candidate_id,
)
from pwf_governed.core.constants import (
    CHECKPOINT_COMPLETION_ACTIONS,
    CHECKPOINT_HUMAN_ACTIONS,
    CHECKPOINT_LOCAL_FIELDS,
    CHECKPOINT_PAUSED_ACTIONS,
    CHECKPOINT_READY_ACTIONS,
    CHECKPOINT_REFS_DIR,
    CHECKPOINT_RESUMES_DIR,
    CURRENT_VERSION,
)
from pwf_governed.core.envelope import (
    _append_unique,
    _canonical_object_digest,
    _load_instance,
    _raw_file_digest,
    _read_json,
    _result_error,
    _safe_component,
    _string_values,
    _transaction_write,
)
from pwf_governed.core.errors import (
    PlanningError,
)
from pwf_governed.edition import adapt_input_once, adapt_output_once, edition_operation
from pwf_governed.governance import (
    _current_governance_gate,
    _find_plan_phase,
    _upsert_human_summary_lines,
)
from pwf_governed.midcourse_gate import (
    _ensure_midcourse_gate_allows_phase,
    _ensure_midcourse_gate_evidence,
    _midcourse_gate_runtime_state,
)
from pwf_governed.packets import (
    _find_phase_and_work_item,
)
from pwf_governed.shared.checkpoint_support import (
    _checkpoint_external_file,
    _load_checkpoint_refs,
    _resolve_checkpoint_file,
)
from pwf_governed.shared.final_gate import (
    _finalization_bool,
    _plan_human_gate_required,
)

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

@edition_operation
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
        incoming = adapt_input_once(
            _read_json(Path(checkpoint_ref_path).expanduser(), code="INVALID_CHECKPOINT_REF"),
            payload_kind="CheckpointRef",
            callsite_id="checkpoint-reference-read",
        )
        context = _validate_checkpoint_reference(incoming, envelope, plan, checklist, state_root, instance)
        source_digest = _checkpoint_source_digest(incoming)
        target = _checkpoint_path(instance, incoming["checkpoint_id"], CHECKPOINT_REFS_DIR)
        existing = None
        if target.exists():
            if target.is_symlink():
                raise PlanningError("UNSAFE_INSTANCE_ROOT", "checkpoint ref cannot be a symlink")
            existing = adapt_input_once(
                _read_json(target, code="INVALID_CHECKPOINT_REF"),
                payload_kind="CheckpointRef",
                callsite_id="checkpoint-reference-read",
            )
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
        persisted_stored = adapt_output_once(
            stored,
            payload_kind="CheckpointRef",
            callsite_id="checkpoint-reference-write",
        )
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
            {ref_relative: contracts.stable_json(persisted_stored), workflow.CHECKLIST_NAME: updated_checklist},
            expected_digests=expected,
            lock_target=workflow.CHECKLIST_NAME,
            lock_name="checkpoint-ref",
            agent=agent,
            transaction_tag="f1-05",
        )
        saved = adapt_input_once(
            _read_json(target, code="INVALID_CHECKPOINT_REF"),
            payload_kind="CheckpointRef",
            callsite_id="checkpoint-reference-read",
        )
        contracts.validate_checkpoint_ref(saved)
        workflow.validate_checklist_text((instance / workflow.CHECKLIST_NAME).read_text(encoding="utf-8"))
        result["created_files"] = [ref_relative, workflow.CHECKLIST_NAME]
        result["would_write"] = False
        try:
            from pwf_governed.progress_excel import ensure_required_plan_artifacts
            ensure_required_plan_artifacts(instance)
        except Exception:
            pass
        return result
    except PlanningError as exc:
        return _result_error(exc)
    except (OSError, ValueError, workflow.ContractError) as exc:
        return _result_error(PlanningError("INTERNAL_ERROR", str(exc)))

@edition_operation
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
        ref = adapt_input_once(
            _read_json(ref_path, code="INVALID_CHECKPOINT_REF"),
            payload_kind="CheckpointRef",
            callsite_id="resume-reference-read",
        )
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
        persisted_resume_record = adapt_output_once(
            resume_record,
            payload_kind="ResumeRecord",
            callsite_id="resume-record-write",
        )
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
            {resume_relative: contracts.stable_json(persisted_resume_record), workflow.CHECKLIST_NAME: updated_checklist},
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
        try:
            from pwf_governed.progress_excel import ensure_required_plan_artifacts
            ensure_required_plan_artifacts(instance)
        except Exception:
            pass
        return result
    except PlanningError as exc:
        return _result_error(exc)
    except (OSError, ValueError, workflow.ContractError) as exc:
        return _result_error(PlanningError("INTERNAL_ERROR", str(exc)))

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
