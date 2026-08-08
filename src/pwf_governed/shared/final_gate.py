"""Gate 2 extracted module: shared/final_gate.py.

Generated from the Gate 1 planning.py baseline.
"""
from __future__ import annotations

from typing import Any

from pwf_governed.core.errors import (
    PlanningError,
)

def _is_final_manual_acceptance_gate(gate: dict[str, Any]) -> bool:
    condition_id = str(gate.get("condition_id") or "").casefold().replace("_", "-")
    description = str(gate.get("description") or "").casefold()
    return (
        "manual-acceptance" in condition_id
        or ("manual acceptance" in description and ("final" in description or "seal" in description))
        or ("人工验收" in description and ("最终" in description or "封板" in description))
    )

def _is_before_last_plan_phase(plan: dict[str, Any], current_phase: str | None) -> bool:
    phases = plan.get("phases", [])
    if not isinstance(phases, list):
        return False
    phase_ids = [
        str(phase.get("phase_id"))
        for phase in phases
        if isinstance(phase, dict) and phase.get("phase_id")
    ]
    if not phase_ids or current_phase not in phase_ids:
        return False
    return phase_ids.index(current_phase) < len(phase_ids) - 1

def _plan_human_gate_required(plan: dict[str, Any], *, current_phase: str | None = None) -> bool:
    defer_final_acceptance = _is_before_last_plan_phase(plan, current_phase)
    for gate in plan.get("human_gates", []):
        if isinstance(gate, dict) and gate.get("status") not in {"SATISFIED", "WAIVED", "NOT_APPLICABLE"}:
            if defer_final_acceptance and _is_final_manual_acceptance_gate(gate):
                continue
            return True
    return False

def _finalization_bool(policy: dict[str, Any], key: str, default: bool) -> bool:
    value = policy.get(key, default)
    if not isinstance(value, bool):
        raise PlanningError("INVALID_FINALIZATION_POLICY", f"{key} must be boolean")
    return value
