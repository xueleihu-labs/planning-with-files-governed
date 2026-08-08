#!/usr/bin/env python3
"""Explicit Gate 4B compatibility facade."""
# version source: VERSION
from __future__ import annotations

import sys
from pathlib import Path

_candidate_src = Path(__file__).resolve().parents[1] / "src"
if _candidate_src.is_dir() and str(_candidate_src) not in sys.path:
    sys.path.insert(0, str(_candidate_src))

from pwf_governed._legacy.planning import (
    resolve_state_root,
    build_plan_package,
    create_plan,
    record_checkpoint_ref,
    resume_from_checkpoint,
    build_execution_packet,
    create_packet,
    create_governance_request,
    record_cleanliness_receipt,
    bind_postwrite_execution_receipt,
    record_receipt,
    register_owner_gate,
    evaluate_outcome_routing,
    record_evolution_receipt,
    record_content_ingest_receipt,
    finalize_plan,
    verify_plan_summary,
    main,
    GOVERNANCE_STAGES,
    OWNER_GATE_IDENTITY_ASSURANCE,
    PlanningError,
    _MISSING_ROUTE,
    _canonical_object_digest,
    _checkpoints_module,
    _final_checkpoint_gate,
    _final_checkpoint_gate_impl,
    _final_governance_gate,
    _final_outcome_gate,
    _final_owner_gate_receipt_gate,
    _finalization_module,
    _finalize_plan_impl,
    _load_governance_requests,
    _load_public_checkpoint_core,
    _midcourse_gate_module,
    _midcourse_gate_runtime_state,
    _midcourse_gate_runtime_state_impl,
    _owner_gate_checkpoint_authority,
    _owner_gate_module,
    _packet_digest,
    _parse_args,
    _register_owner_gate_impl,
    _routing_decision_relative,
    _safe_component,
    _validate_checkpoint_reference,
    _validate_checkpoint_reference_impl,
    _verify_module,
    _verify_plan_summary_impl,
    _with_checkpoint_patch,
    contracts,
    governance,
    project_init,
    workflow,
)

__all__ = [
    "resolve_state_root",
    "build_plan_package",
    "create_plan",
    "record_checkpoint_ref",
    "resume_from_checkpoint",
    "build_execution_packet",
    "create_packet",
    "create_governance_request",
    "record_cleanliness_receipt",
    "bind_postwrite_execution_receipt",
    "record_receipt",
    "register_owner_gate",
    "evaluate_outcome_routing",
    "record_evolution_receipt",
    "record_content_ingest_receipt",
    "finalize_plan",
    "verify_plan_summary",
    "main",
]

# Facade delegation class for test mock compatibility
import types

class _FacadeModule(types.ModuleType):
    def __getattribute__(self, name):
        if name.startswith("_FacadeModule__") or name in {"__class__", "__dict__", "__weakref__", "__module__", "__name__", "__doc__", "__file__", "__path__", "__package__", "__all__"}:
            return super().__getattribute__(name)
        try:
            target = sys.modules["pwf_governed._legacy.planning"]
            val = getattr(target, name)
            return val
        except (KeyError, AttributeError):
            return super().__getattribute__(name)

    def __setattr__(self, name, value):
        if name.startswith("_FacadeModule__") or name in {"__class__", "__dict__", "__weakref__", "__module__", "__name__", "__doc__", "__file__", "__path__", "__package__", "__loader__", "__spec__", "__all__"}:
            super().__setattr__(name, value)
        else:
            try:
                target = sys.modules["pwf_governed._legacy.planning"]
                setattr(target, name, value)
            except (KeyError, AttributeError):
                super().__setattr__(name, value)

    def __delattr__(self, name):
        try:
            target = sys.modules["pwf_governed._legacy.planning"]
            delattr(target, name)
        except (KeyError, AttributeError):
            super().__delattr__(name)

sys.modules[__name__].__class__ = _FacadeModule

if __name__ == "__main__":
    raise SystemExit(main())
