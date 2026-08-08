"""Deterministic PLAN runtime entries for F1-02 through F1-07. This is
intentionally a thin adapter around the frozen F1-01 contracts, the existing
workflow template/binding helpers, project initialization templates, and the
public phase-checkpoint-loop state-root resolver. It creates only external
PLAN-instance projections; it does not dispatch work, create checkpoints, call
governance Skills, or ingest knowledge.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from pwf_governed._legacy import plan_contracts as contracts
from pwf_governed._legacy import governance_profiles as governance
from pwf_governed._legacy import project_init
from pwf_governed._legacy import workflow_contracts as workflow
import pwf_governed.checkpoints as _checkpoints_module
import pwf_governed.finalization as _finalization_module
import pwf_governed.midcourse_gate as _midcourse_gate_module
import pwf_governed.owner_gate as _owner_gate_module
import pwf_governed.verify as _verify_module
from pwf_governed.checkpoints import record_checkpoint_ref
from pwf_governed.checkpoints import _final_checkpoint_gate as _final_checkpoint_gate_impl
from pwf_governed.checkpoints import _validate_checkpoint_reference as _validate_checkpoint_reference_impl
from pwf_governed.checkpoints import resume_from_checkpoint
from pwf_governed.core.constants import GOVERNANCE_STAGES
from pwf_governed.core.constants import OWNER_GATE_IDENTITY_ASSURANCE
from pwf_governed.core.constants import _MISSING_ROUTE
from pwf_governed.core.envelope import _canonical_object_digest
from pwf_governed.core.envelope import _load_public_checkpoint_core
from pwf_governed.core.envelope import _safe_component
from pwf_governed.core.envelope import resolve_state_root
from pwf_governed.core.errors import PlanningError
from pwf_governed.evolution import record_content_ingest_receipt
from pwf_governed.evolution import record_evolution_receipt
from pwf_governed.finalization import _final_governance_gate
from pwf_governed.finalization import _final_outcome_gate
from pwf_governed.finalization import finalize_plan as _finalize_plan_impl
from pwf_governed.governance import create_governance_request
from pwf_governed.governance import _load_governance_requests
from pwf_governed.outcomes import evaluate_outcome_routing
from pwf_governed.outcomes import _routing_decision_relative
from pwf_governed.owner_gate import _owner_gate_checkpoint_authority
from pwf_governed.owner_gate import register_owner_gate as _register_owner_gate_impl
from pwf_governed.packets import build_execution_packet
from pwf_governed.packets import create_packet
from pwf_governed.packets import _packet_digest
from pwf_governed.plan_builder import build_plan_package
from pwf_governed.plan_builder import create_plan
from pwf_governed.receipts import bind_postwrite_execution_receipt
from pwf_governed.receipts import record_cleanliness_receipt
from pwf_governed.receipts import record_receipt
from pwf_governed.midcourse_gate import _midcourse_gate_runtime_state as _midcourse_gate_runtime_state_impl
from pwf_governed.verify import verify_plan_summary as _verify_plan_summary_impl

_validate_checkpoint_reference = _validate_checkpoint_reference_impl

__all__ = [
    'bind_postwrite_execution_receipt',
    'build_execution_packet',
    'build_plan_package',
    'create_governance_request',
    'create_packet',
    'create_plan',
    'evaluate_outcome_routing',
    'finalize_plan',
    'main',
    'record_checkpoint_ref',
    'record_cleanliness_receipt',
    'record_content_ingest_receipt',
    'record_evolution_receipt',
    'record_receipt',
    'register_owner_gate',
    'resolve_state_root',
    'resume_from_checkpoint',
    'verify_plan_summary',
]


def _with_checkpoint_patch(callable_obj: Any, *args: Any, **kwargs: Any) -> Any:
    old_validate = _checkpoints_module._validate_checkpoint_reference
    old_checkpoint_runtime = _checkpoints_module._midcourse_gate_runtime_state
    old_runtime = _midcourse_gate_module._midcourse_gate_runtime_state
    old_core = _midcourse_gate_module._load_public_checkpoint_core
    old_final_checkpoint = _finalization_module._final_checkpoint_gate
    old_final_runtime = _finalization_module._midcourse_gate_runtime_state
    old_final_owner_authority = _finalization_module._owner_gate_checkpoint_authority
    try:
        _checkpoints_module._validate_checkpoint_reference = _validate_checkpoint_reference
        _checkpoints_module._midcourse_gate_runtime_state = _midcourse_gate_runtime_state
        _midcourse_gate_module._midcourse_gate_runtime_state = _midcourse_gate_runtime_state
        _midcourse_gate_module._load_public_checkpoint_core = _load_public_checkpoint_core
        _finalization_module._final_checkpoint_gate = _final_checkpoint_gate
        _finalization_module._midcourse_gate_runtime_state = _midcourse_gate_runtime_state
        _finalization_module._owner_gate_checkpoint_authority = _owner_gate_checkpoint_authority
        return callable_obj(*args, **kwargs)
    finally:
        _checkpoints_module._validate_checkpoint_reference = old_validate
        _checkpoints_module._midcourse_gate_runtime_state = old_checkpoint_runtime
        _midcourse_gate_module._midcourse_gate_runtime_state = old_runtime
        _midcourse_gate_module._load_public_checkpoint_core = old_core
        _finalization_module._final_checkpoint_gate = old_final_checkpoint
        _finalization_module._midcourse_gate_runtime_state = old_final_runtime
        _finalization_module._owner_gate_checkpoint_authority = old_final_owner_authority


def _midcourse_gate_runtime_state(
    state_root: Path,
    instance: Path,
    plan: dict[str, Any],
) -> dict[str, Any]:
    return _with_checkpoint_patch(_midcourse_gate_runtime_state_impl, state_root, instance, plan)


def _final_checkpoint_gate(
    state_root: Path,
    instance: Path,
    envelope: dict[str, Any],
    plan: dict[str, Any],
    checklist: str,
    policy: dict[str, Any],
    mode: str,
) -> tuple[list[str], list[str], list[str], list[str], str | None]:
    return _with_checkpoint_patch(
        _final_checkpoint_gate_impl,
        state_root,
        instance,
        envelope,
        plan,
        checklist,
        policy,
        mode,
    )


def _final_owner_gate_receipt_gate(
    state_root: Path,
    instance: Path,
    envelope: dict[str, Any],
    plan: dict[str, Any],
    checklist: str,
) -> tuple[list[str], list[str], list[str], list[str]]:
    old_authority = _finalization_module._owner_gate_checkpoint_authority
    _finalization_module._owner_gate_checkpoint_authority = _owner_gate_checkpoint_authority
    try:
        return _finalization_module._final_owner_gate_receipt_gate(
            state_root,
            instance,
            envelope,
            plan,
            checklist,
        )
    finally:
        _finalization_module._owner_gate_checkpoint_authority = old_authority


def register_owner_gate(
    instance_root: str | Path,
    *,
    task_id: str,
    plan_id: str,
    state_root: str | Path,
    gate_id: str,
    expected_status: str,
    decision: str,
    confirmation_reference: str,
    confirmation_statement: str,
    accepted_commit: str,
    accepted_checkpoint: str,
    result_commit_head: str,
    direct_read_head: str,
    external_read_head: str,
    authorize: str,
    evidence_refs: list[str],
    preview: bool = False,
    apply: bool = False,
    agent: str = "planning-with-files",
) -> dict[str, Any]:
    old_authority = _owner_gate_module._owner_gate_checkpoint_authority
    try:
        _owner_gate_module._owner_gate_checkpoint_authority = _owner_gate_checkpoint_authority
        return _register_owner_gate_impl(
            instance_root,
            task_id=task_id,
            plan_id=plan_id,
            state_root=state_root,
            gate_id=gate_id,
            expected_status=expected_status,
            decision=decision,
            confirmation_reference=confirmation_reference,
            confirmation_statement=confirmation_statement,
            accepted_commit=accepted_commit,
            accepted_checkpoint=accepted_checkpoint,
            result_commit_head=result_commit_head,
            direct_read_head=direct_read_head,
            external_read_head=external_read_head,
            authorize=authorize,
            evidence_refs=evidence_refs,
            preview=preview,
            apply=apply,
            agent=agent,
        )
    finally:
        _owner_gate_module._owner_gate_checkpoint_authority = old_authority


def verify_plan_summary(instance_root: str | Path) -> dict[str, Any]:
    return _with_checkpoint_patch(_verify_plan_summary_impl, instance_root)


def finalize_plan(
    instance_root: str | Path,
    *,
    preview: bool = False,
    apply: bool = False,
    agent: str = "planning-with-files",
) -> dict[str, Any]:
    return _with_checkpoint_patch(
        _finalize_plan_impl,
        instance_root,
        preview=preview,
        apply=apply,
        agent=agent,
    )

def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser._optionals.title = "optional arguments"
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create-plan", help="create a PlanPackage from a TaskEnvelope")
    create.add_argument("--task-envelope", required=True, help="path to a structured TaskEnvelope JSON file")
    create.add_argument("--state-root", help="optional external runtime state root")
    create.add_argument("--agent", help="writer identity; defaults to TaskEnvelope.requested_by")
    create.add_argument(
        "--risk-route",
        help="path to the structured governance L0-L3 route JSON; omit only for legacy compatibility",
    )
    create.add_argument("--requested-profile", choices=list(governance.PROFILE_ORDER))
    create.add_argument("--supported-profile", choices=list(governance.PROFILE_ORDER))
    create.add_argument("--legacy", action="store_true", help="retain the pre-v1 governance mapping explicitly")
    mode = create.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", action="store_true")
    mode.add_argument("--apply", action="store_true")
    packet = subparsers.add_parser("create-packet", help="create one ExecutionPacket without dispatching it")
    packet.add_argument("--instance-root", required=True, help="external task instance root")
    packet.add_argument("--phase-id", required=True)
    packet.add_argument("--work-item-id", required=True)
    packet.add_argument("--agent", default="planning-with-files")
    packet.add_argument("--revision", type=int, help="optional controlled packet revision")
    packet.add_argument("--revision-type")
    packet.add_argument("--predecessor-checkpoint-id")
    packet.add_argument("--revision-reason")
    packet.add_argument("--revision-scope")
    packet.add_argument("--revision-evidence-ref", action="append", default=[])
    packet_mode = packet.add_mutually_exclusive_group(required=True)
    packet_mode.add_argument("--preview", action="store_true")
    packet_mode.add_argument("--apply", action="store_true")
    receipt = subparsers.add_parser("record-receipt", help="validate and record one ExecutionReceipt")
    receipt.add_argument("--instance-root", required=True, help="external task instance root")
    receipt.add_argument("--receipt", required=True, help="path to an ExecutionReceipt JSON")
    receipt.add_argument("--agent", default="planning-with-files")
    receipt_mode = receipt.add_mutually_exclusive_group(required=True)
    receipt_mode.add_argument("--preview", action="store_true")
    receipt_mode.add_argument("--apply", action="store_true")
    governance_parser = subparsers.add_parser(
        "create-governance-request",
        help="create one local GovernanceRequest without invoking governance",
    )
    governance_parser.add_argument("--instance-root", required=True, help="external task instance root")
    governance_parser.add_argument("--stage", required=True, choices=sorted(GOVERNANCE_STAGES))
    governance_parser.add_argument("--phase-id", required=True)
    governance_parser.add_argument("--agent", default="planning-with-files")
    governance_mode = governance_parser.add_mutually_exclusive_group(required=True)
    governance_mode.add_argument("--preview", action="store_true")
    governance_mode.add_argument("--apply", action="store_true")
    cleanliness = subparsers.add_parser(
        "record-cleanliness-receipt",
        help="validate and consume one local CleanlinessReceipt without invoking governance",
    )
    cleanliness.add_argument("--instance-root", required=True, help="external task instance root")
    cleanliness.add_argument("--receipt", required=True, help="path to a CleanlinessReceipt JSON")
    cleanliness.add_argument("--agent", default="planning-with-files")
    cleanliness_mode = cleanliness.add_mutually_exclusive_group(required=True)
    cleanliness_mode.add_argument("--preview", action="store_true")
    cleanliness_mode.add_argument("--apply", action="store_true")
    postwrite_binding = subparsers.add_parser(
        "bind-postwrite-execution-receipt",
        help="bind one existing POST_WRITE receipt to its completed execution receipt",
    )
    postwrite_binding.add_argument("--instance-root", required=True, help="external task instance root")
    postwrite_binding.add_argument("--state-root", required=True, help="explicit external runtime state root")
    postwrite_binding.add_argument("--task-id", required=True)
    postwrite_binding.add_argument("--plan-id", required=True)
    postwrite_binding.add_argument("--phase-id", required=True)
    postwrite_binding.add_argument("--postwrite-receipt-id", required=True)
    postwrite_binding.add_argument("--execution-receipt-id", required=True)
    postwrite_binding.add_argument("--agent", default="planning-with-files")
    postwrite_binding_mode = postwrite_binding.add_mutually_exclusive_group(required=True)
    postwrite_binding_mode.add_argument("--preview", action="store_true")
    postwrite_binding_mode.add_argument("--apply", action="store_true")
    checkpoint = subparsers.add_parser(
        "record-checkpoint-ref",
        help="validate and record an external checkpoint reference without creating checkpoint authority",
    )
    checkpoint.add_argument("--instance-root", required=True, help="external task instance root")
    checkpoint.add_argument("--checkpoint-ref", required=True, help="path to an external checkpoint reference JSON")
    checkpoint.add_argument("--agent", default="planning-with-files")
    checkpoint_mode = checkpoint.add_mutually_exclusive_group(required=True)
    checkpoint_mode.add_argument("--preview", action="store_true")
    checkpoint_mode.add_argument("--apply", action="store_true")
    resume = subparsers.add_parser(
        "resume-from-checkpoint",
        help="create a local resume projection from a verified external checkpoint reference",
    )
    resume.add_argument("--instance-root", required=True, help="external task instance root")
    resume.add_argument("--checkpoint-id", required=True)
    resume.add_argument("--agent", default="planning-with-files")
    resume_mode = resume.add_mutually_exclusive_group(required=True)
    resume_mode.add_argument("--preview", action="store_true")
    resume_mode.add_argument("--apply", action="store_true")
    routing = subparsers.add_parser(
        "evaluate-outcome-routing",
        help="evaluate local evolution/content value without invoking external systems",
    )
    routing.add_argument("--instance-root", required=True, help="external task instance root")
    routing.add_argument("--agent", default="planning-with-files")
    routing_mode = routing.add_mutually_exclusive_group(required=True)
    routing_mode.add_argument("--preview", action="store_true")
    routing_mode.add_argument("--apply", action="store_true")
    evolution_receipt = subparsers.add_parser(
        "record-evolution-receipt",
        help="validate and record one external EvolutionReceipt without invoking bridge systems",
    )
    evolution_receipt.add_argument("--instance-root", required=True, help="external task instance root")
    evolution_receipt.add_argument("--receipt", required=True, help="path to an EvolutionReceipt JSON")
    evolution_receipt.add_argument("--agent", default="planning-with-files")
    evolution_mode = evolution_receipt.add_mutually_exclusive_group(required=True)
    evolution_mode.add_argument("--preview", action="store_true")
    evolution_mode.add_argument("--apply", action="store_true")
    content_receipt = subparsers.add_parser(
        "record-content-ingest-receipt",
        help="validate and record one external ContentIngestReceipt without invoking the publisher",
    )
    content_receipt.add_argument("--instance-root", required=True, help="external task instance root")
    content_receipt.add_argument("--receipt", required=True, help="path to a ContentIngestReceipt JSON")
    content_receipt.add_argument("--agent", default="planning-with-files")
    content_mode = content_receipt.add_mutually_exclusive_group(required=True)
    content_mode.add_argument("--preview", action="store_true")
    content_mode.add_argument("--apply", action="store_true")
    owner_gate = subparsers.add_parser(
        "register-owner-gate",
        help="register one explicit owner USER_GATE decision through the formal PLAN writer",
    )
    owner_gate.add_argument("--instance-root", required=True, help="external task instance root")
    owner_gate.add_argument("--state-root", required=True, help="explicit external runtime state root")
    owner_gate.add_argument("--task-id", required=True)
    owner_gate.add_argument("--plan-id", required=True)
    owner_gate.add_argument("--gate-id", required=True)
    owner_gate.add_argument("--expected-status", required=True, choices=["PENDING", "SATISFIED"])
    owner_gate.add_argument("--decision", required=True, choices=["SATISFIED"])
    owner_gate.add_argument("--confirmation-reference", required=True)
    owner_gate.add_argument("--confirmation-statement", required=True)
    owner_gate.add_argument("--accepted-commit", required=True)
    owner_gate.add_argument("--accepted-checkpoint", required=True)
    owner_gate.add_argument("--result-commit-head", required=True, choices=["VALID"])
    owner_gate.add_argument("--direct-read-head", required=True, choices=["PASSED"])
    owner_gate.add_argument("--external-read-head", required=True, choices=["PASSED"])
    owner_gate.add_argument("--authorize", required=True, choices=["PRE_CLOSE"])
    owner_gate.add_argument("--evidence-ref", action="append", required=True)
    owner_gate.add_argument("--agent", default="planning-with-files")
    owner_gate_mode = owner_gate.add_mutually_exclusive_group(required=True)
    owner_gate_mode.add_argument("--preview", action="store_true")
    owner_gate_mode.add_argument("--apply", action="store_true")
    finalize = subparsers.add_parser(
        "finalize-plan",
        help="evaluate and optionally publish the final PLAN completion gate",
    )
    finalize.add_argument("--instance-root", required=True, help="external task instance root")
    finalize.add_argument("--agent", default="planning-with-files")
    finalize_mode = finalize.add_mutually_exclusive_group(required=True)
    finalize_mode.add_argument("--preview", action="store_true")
    finalize_mode.add_argument("--apply", action="store_true")
    verify = subparsers.add_parser(
        "verify-plan",
        help="emit a compact deterministic PLAN completion summary",
    )
    verify.add_argument("--instance-root", required=True, help="external task instance root")
    verify.add_argument("--summary", action="store_true", required=True)
    return parser.parse_args(list(argv) if argv is not None else None)

def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "create-plan":
        output = create_plan(
            args.task_envelope,
            state_root=args.state_root,
            preview=args.preview,
            apply=args.apply,
            agent=args.agent,
            risk_route=args.risk_route if args.risk_route is not None else _MISSING_ROUTE,
            requested_profile=args.requested_profile,
            supported_profile=args.supported_profile,
            legacy=True if args.legacy else None,
        )
    elif args.command == "create-packet":
        output = create_packet(
            args.instance_root,
            args.phase_id,
            args.work_item_id,
            preview=args.preview,
            apply=args.apply,
            agent=args.agent,
            revision=args.revision,
            revision_type=args.revision_type,
            predecessor_checkpoint_id=args.predecessor_checkpoint_id,
            revision_reason=args.revision_reason,
            revision_scope=args.revision_scope,
            revision_evidence_refs=args.revision_evidence_ref,
        )
    elif args.command == "record-receipt":
        output = record_receipt(
            args.instance_root,
            args.receipt,
            preview=args.preview,
            apply=args.apply,
            agent=args.agent,
        )
    elif args.command == "create-governance-request":
        output = create_governance_request(
            args.instance_root,
            args.stage,
            args.phase_id,
            preview=args.preview,
            apply=args.apply,
            agent=args.agent,
        )
    elif args.command == "record-cleanliness-receipt":
        output = record_cleanliness_receipt(
            args.instance_root,
            args.receipt,
            preview=args.preview,
            apply=args.apply,
            agent=args.agent,
        )
    elif args.command == "bind-postwrite-execution-receipt":
        output = bind_postwrite_execution_receipt(
            args.instance_root,
            state_root=args.state_root,
            task_id=args.task_id,
            plan_id=args.plan_id,
            phase_id=args.phase_id,
            postwrite_receipt_id=args.postwrite_receipt_id,
            execution_receipt_id=args.execution_receipt_id,
            preview=args.preview,
            apply=args.apply,
            agent=args.agent,
        )
    elif args.command == "record-checkpoint-ref":
        output = record_checkpoint_ref(
            args.instance_root,
            args.checkpoint_ref,
            preview=args.preview,
            apply=args.apply,
            agent=args.agent,
        )
    elif args.command == "resume-from-checkpoint":
        output = resume_from_checkpoint(
            args.instance_root,
            args.checkpoint_id,
            preview=args.preview,
            apply=args.apply,
            agent=args.agent,
        )
    elif args.command == "evaluate-outcome-routing":
        output = evaluate_outcome_routing(
            args.instance_root,
            preview=args.preview,
            apply=args.apply,
            agent=args.agent,
        )
    elif args.command == "record-evolution-receipt":
        output = record_evolution_receipt(
            args.instance_root,
            args.receipt,
            preview=args.preview,
            apply=args.apply,
            agent=args.agent,
        )
    elif args.command == "record-content-ingest-receipt":
        output = record_content_ingest_receipt(
            args.instance_root,
            args.receipt,
            preview=args.preview,
            apply=args.apply,
            agent=args.agent,
        )
    elif args.command == "register-owner-gate":
        output = register_owner_gate(
            args.instance_root,
            task_id=args.task_id,
            plan_id=args.plan_id,
            state_root=args.state_root,
            gate_id=args.gate_id,
            expected_status=args.expected_status,
            decision=args.decision,
            confirmation_reference=args.confirmation_reference,
            confirmation_statement=args.confirmation_statement,
            accepted_commit=args.accepted_commit,
            accepted_checkpoint=args.accepted_checkpoint,
            result_commit_head=args.result_commit_head,
            direct_read_head=args.direct_read_head,
            external_read_head=args.external_read_head,
            authorize=args.authorize,
            evidence_refs=args.evidence_ref,
            preview=args.preview,
            apply=args.apply,
            agent=args.agent,
        )
    elif args.command == "finalize-plan":
        output = finalize_plan(
            args.instance_root,
            preview=args.preview,
            apply=args.apply,
            agent=args.agent,
        )
    elif args.command == "verify-plan":
        output = verify_plan_summary(args.instance_root) if args.summary else {"result": "FAILED"}
    else:
        return 2
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if output.get("result") in {
        "PREVIEW",
        "LIGHTWEIGHT_ROUTED",
        "CREATED",
        "EXISTING_PLAN",
        "EXISTING_PACKET",
        "RECORDED",
        "EXISTING_RECEIPT",
        "CREATED_GOVERNANCE_REQUEST",
        "EXISTING_GOVERNANCE_REQUEST",
        "RECORDED_CLEANLINESS_RECEIPT",
        "EXISTING_CLEANLINESS_RECEIPT",
        "BOUND_POSTWRITE_EXECUTION_RECEIPT",
        "EXISTING_POSTWRITE_EXECUTION_BINDING",
        "RECORDED_CHECKPOINT_REF",
        "EXISTING_CHECKPOINT_REF",
        "RESUMED",
        "ALREADY_RESUMED",
        "CREATED_OUTCOME_ROUTING",
        "EXISTING_ROUTING_DECISION",
        "RECORDED_EVOLUTION_RECEIPT",
        "EXISTING_EVOLUTION_RECEIPT",
        "RECORDED_CONTENT_INGEST_RECEIPT",
        "EXISTING_CONTENT_INGEST_RECEIPT",
        "REGISTERED_OWNER_GATE",
        "EXISTING_OWNER_GATE",
        "CLOSE_READY",
        "CLOSED",
        "CLOSE_BLOCKED",
        "CLOSE_WAITING_HUMAN",
        "ALREADY_CLOSED",
        "SUMMARY",
        "RECORDED_OUTCOME_ROUTING",
        "EXISTING_OUTCOME_ROUTING",
    } else 1

if __name__ == "__main__":
    sys.exit(main())
