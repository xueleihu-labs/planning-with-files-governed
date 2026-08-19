"""Gate 2 extracted module: verify.py.

Generated from the Gate 1 planning.py baseline.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pwf_governed._legacy import (
    workflow_contracts,
)
from pwf_governed._legacy import workflow_contracts as workflow

from pwf_governed._legacy import (
    workflow_contracts,
)
from pwf_governed.core.envelope import (
    _load_instance,
)
from pwf_governed.core.errors import (
    PlanningError,
)
from pwf_governed.edition import edition_operation
from pwf_governed.finalization import (
    _finalization_assessment,
)

@edition_operation
def verify_plan_summary(instance_root: str | Path) -> dict[str, Any]:
    """Return the compact, deterministic completion summary without writing."""
    try:
        state_root, instance, envelope, plan, checklist = _load_instance(instance_root)
        assessment = _finalization_assessment(state_root, instance, envelope, plan, checklist)
        metadata = workflow.extract_machine_json(checklist, "workflow")
        excel_check = {}
        try:
            from pwf_governed.progress_excel import validate_required_plan_artifacts
            excel_check = validate_required_plan_artifacts(instance)
        except Exception:
            pass
        return {
            "result": "SUMMARY",
            "task_id": envelope["task_id"],
            "plan_id": plan["plan_id"],
            "plan_version": plan["plan_version"],
            "mode": assessment["mode"],
            "current_phase": metadata.get("current_phase"),
            "completion_gate": assessment["completion_gate"],
            "top_level_status": assessment["top_level_status"],
            "blocking_findings": assessment["blocking_findings"],
            "warnings": assessment["warnings"],
            "trusted_checkpoint": assessment["trusted_checkpoint"],
            "midcourse_gate_result": assessment["midcourse_gate_result"],
            "midcourse_gate_effective_result": assessment["midcourse_gate_effective_result"],
            "midcourse_gate_source": assessment["midcourse_gate_source"],
            "required_next_action": assessment["required_next_action"],
            "required_excel_exists": excel_check.get("REQUIRED_EXCEL_EXISTS", False),
            "required_excel_valid": excel_check.get("REQUIRED_EXCEL_VALID", False),
        }
    except PlanningError as exc:
        excel_check = {}
        try:
            from pwf_governed.progress_excel import validate_required_plan_artifacts
            excel_check = validate_required_plan_artifacts(instance_root)
        except Exception:
            pass
        return {
            "result": "SUMMARY",
            "task_id": Path(instance_root).expanduser().name,
            "plan_id": None,
            "plan_version": None,
            "mode": "UNKNOWN",
            "current_phase": None,
            "completion_gate": "CLOSE_BLOCKED",
            "top_level_status": "BLOCKED",
            "blocking_findings": [f"{exc.code}: {exc.message}"],
            "warnings": [],
            "trusted_checkpoint": None,
            "required_next_action": "REPAIR_BLOCKING_EVIDENCE",
            "required_excel_exists": excel_check.get("REQUIRED_EXCEL_EXISTS", False),
            "required_excel_valid": excel_check.get("REQUIRED_EXCEL_VALID", False),
        }
    except (OSError, ValueError, workflow.ContractError) as exc:
        excel_check = {}
        try:
            from pwf_governed.progress_excel import validate_required_plan_artifacts
            excel_check = validate_required_plan_artifacts(instance_root)
        except Exception:
            pass
        return {
            "result": "SUMMARY",
            "task_id": Path(instance_root).expanduser().name,
            "plan_id": None,
            "plan_version": None,
            "mode": "UNKNOWN",
            "current_phase": None,
            "completion_gate": "CLOSE_BLOCKED",
            "top_level_status": "BLOCKED",
            "blocking_findings": [f"INTERNAL_ERROR: {exc}"],
            "warnings": [],
            "trusted_checkpoint": None,
            "required_next_action": "REPAIR_BLOCKING_EVIDENCE",
            "required_excel_exists": excel_check.get("REQUIRED_EXCEL_EXISTS", False),
            "required_excel_valid": excel_check.get("REQUIRED_EXCEL_VALID", False),
        }
