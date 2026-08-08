"""Gate 2 extracted module: midcourse_gate.py.

Generated from the Gate 1 planning.py baseline.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from typing import Iterable

from pwf_governed._legacy import (
    plan_contracts,
)
from pwf_governed._legacy import plan_contracts as contracts

from pwf_governed._legacy import (
    plan_contracts,
)
from pwf_governed.core.envelope import (
    _canonical_object_digest,
    _load_public_checkpoint_core,
    _markdown_field,
    _parse_timestamp,
    _raw_file_digest,
    _read_json,
)
from pwf_governed.core.errors import (
    PlanningError,
)
from pwf_governed.shared.checkpoint_support import (
    _checkpoint_external_file,
    _load_checkpoint_refs,
)
from pwf_governed.shared.evidence import (
    _final_evidence_file,
)

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
