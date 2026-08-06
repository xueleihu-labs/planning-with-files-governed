#!/usr/bin/env python3
# VERSION source: ../VERSION
"""Machine-checkable A0/A1/A2 completion gate."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workflow_contracts as workflow
import planning_layout as layout


def field(content: str, label: str) -> str:
    match = re.search(rf"^[-*]\s*\*\*{re.escape(label)}\*\*:\s*(.+?)\s*$", content, re.MULTILINE)
    return match.group(1).strip() if match else ""


def main() -> int:
    if os.environ.get("PLANNING_DISABLED") == "1":
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--planning-dir")
    parser.add_argument("--task-id")
    parser.add_argument("--audit-level", choices=("A0", "A1", "A2"), default="A1")
    args = parser.parse_args()
    supplied_root = Path(args.root).expanduser().resolve()
    try:
        resolved = layout.resolve_layout(
            start=supplied_root,
            planning_dir=args.planning_dir,
            task_id=args.task_id,
            require=False,
        )
    except layout.LayoutError as exc:
        print(f"FAIL: {exc}")
        return 4
    root = resolved.project_root if resolved is not None else supplied_root
    plan = resolved.path("1_master_plan.md") if resolved is not None else root / "1_master_plan.md"
    audit = resolved.path("5_audit.md") if resolved is not None else root / "5_audit.md"
    missing = [str(path.name) for path in (plan, audit) if not path.exists()]
    if missing:
        print("FAIL: missing " + ", ".join(missing))
        return 4
    plan_text = plan.read_text(encoding="utf-8")
    failures = []
    if field(plan_text, "Done Criteria Status") != "PASS":
        failures.append("Done Criteria Status must be PASS")
    if field(plan_text, "Validation Status") != "PASS":
        failures.append("Validation Status must be PASS")
    if field(plan_text, "Unresolved Blockers") != "NONE":
        failures.append("Unresolved Blockers must be NONE")
    if args.audit_level in ("A1", "A2"):
        audit_text = audit.read_text(encoding="utf-8")
        if field(audit_text, "Audit Result") != "PASS":
            failures.append("Audit Result must be PASS")
        if args.audit_level == "A2":
            executor = field(audit_text, "Execution Agent-ID")
            auditor = field(audit_text, "Audit Agent-ID")
            if not executor or not auditor or executor == auditor or auditor == "PENDING":
                failures.append("A2 requires an independent Audit Agent-ID")
            if field(audit_text, "Boss Gate") != "APPROVED":
                failures.append("A2 requires Boss Gate APPROVED")
    checklist = resolved.path(workflow.CHECKLIST_NAME) if resolved is not None else root / workflow.CHECKLIST_NAME
    if checklist.exists():
        try:
            checklist_text = checklist.read_text(encoding="utf-8")
            failures.extend("workflow: " + error for error in workflow.workflow_integrity_errors(checklist_text))
        except (OSError, workflow.ContractError) as exc:
            failures.append(f"workflow checklist invalid: {exc}")
    else:
        print("WARN: WORKFLOW_CHECKLIST.md is absent; legacy project compatibility mode")
    if failures:
        print("FAIL:")
        for item in failures:
            print("- " + item)
        return 4
    print(f"PASS: {args.audit_level} completion gate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
