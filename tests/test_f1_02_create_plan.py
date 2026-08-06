#!/usr/bin/env python3
"""F1-02 tests for the deterministic TaskEnvelope -> PlanPackage entry."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import plan_contracts as contracts  # noqa: E402
import planning  # noqa: E402
import workflow_contracts as workflow  # noqa: E402


FIXTURE = json.loads(
    (ROOT / "tests" / "fixtures" / "f1-01" / "valid_contracts.json").read_text(encoding="utf-8")
)["task_envelope"]


def tree_snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


class F102CreatePlanTests(unittest.TestCase):
    def envelope(self, **updates: object) -> dict[str, object]:
        value = copy.deepcopy(FIXTURE)
        value.update(updates)
        return value

    def write_envelope(self, directory: Path, value: dict[str, object]) -> Path:
        path = directory / f"{value['task_id']}.json"
        path.write_text(contracts.stable_json(value), encoding="utf-8")
        return path

    def test_preview_is_zero_write_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state-root"
            input_path = self.write_envelope(root, self.envelope(task_id="task-preview"))
            first = planning.create_plan(input_path, state_root=state, preview=True)
            second = planning.create_plan(input_path, state_root=state, preview=True)
            self.assertEqual(first["result"], "PREVIEW")
            self.assertEqual(first["plan_id"], second["plan_id"])
            self.assertEqual(first["plan_package_digest"], second["plan_package_digest"])
            self.assertFalse(state.exists())
            self.assertEqual(first["created_files"], second["created_files"])

    def test_apply_reuses_contracts_templates_and_writes_external_instance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state-root"
            envelope = self.envelope(task_id="task-apply")
            input_path = self.write_envelope(root, envelope)
            result = planning.create_plan(input_path, state_root=state, apply=True)
            instance = state / envelope["task_id"]
            self.assertEqual(result["result"], "CREATED")
            self.assertTrue(instance.is_dir())
            self.assertTrue((instance / "task-envelope.json").is_file())
            self.assertTrue((instance / "plan-package.json").is_file())
            self.assertTrue((instance / "receipts" / "create-plan.json").is_file())
            self.assertTrue((instance / "WORKFLOW_CHECKLIST.md").is_file())
            for name in ("00_PROJECT_INDEX.md", "1_master_plan.md", "2_execution_log.md", "3_status_update.md", "4_handoff.md", "5_audit.md", "AGENTS.md", "CLAUDE.md", "README.md"):
                self.assertTrue((instance / name).is_file(), name)
            stored_envelope = json.loads((instance / "task-envelope.json").read_text(encoding="utf-8"))
            stored_plan = json.loads((instance / "plan-package.json").read_text(encoding="utf-8"))
            contracts.validate_task_envelope(stored_envelope)
            contracts.validate_plan_package(stored_plan)
            checklist = (instance / "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8")
            metadata = workflow.validate_checklist_text(checklist)
            self.assertEqual(metadata["template"]["template_id"], "generic-project")
            self.assertEqual(metadata["template"]["template_digest"], workflow.file_digest(ROOT / "templates/workflow/base/generic-project/1.0.0.md"))
            self.assertFalse((instance / "knowledge_handoff.json").exists())
            self.assertFalse((state / "phase-checkpoints").exists())

    def test_cli_create_plan_preview_is_structured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            input_path = self.write_envelope(root, self.envelope(task_id="task-cli"))
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "planning.py"),
                    "create-plan",
                    "--task-envelope",
                    str(input_path),
                    "--state-root",
                    str(state),
                    "--preview",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            output = json.loads(completed.stdout)
            self.assertEqual(output["result"], "PREVIEW")
            self.assertEqual(output["task_id"], "task-cli")
            self.assertFalse(state.exists())

    def test_task_profiles_are_deterministic(self) -> None:
        cases = (
            ("LOW", "P2", "LIGHTWEIGHT"),
            ("MEDIUM", "P2", "STANDARD"),
            ("LOW", "P0", "FULL"),
            ("HIGH", "P2", "HIGH_RISK"),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, (risk, priority, expected) in enumerate(cases):
                value = self.envelope(task_id=f"task-profile-{index}", risk_level=risk, priority=priority)
                result = planning.create_plan(self.write_envelope(root, value), state_root=root / f"state-{index}", preview=True)
                self.assertEqual(result["result"], "PREVIEW")
                self.assertEqual(result["task_profile"], expected)

    def test_plan_package_mapping_and_reserved_integrations(self) -> None:
        value = self.envelope(task_id="task-mapping")
        package = planning.build_plan_package(value)
        contracts.validate_plan_package(package)
        self.assertEqual(package["objective"], value["objective"])
        self.assertEqual(package["scope"], value["scope"])
        self.assertEqual(package["non_goals"], value["non_goals"])
        self.assertEqual(package["knowledge_policy"], value["knowledge_policy"])
        self.assertEqual(package["capability_refs"], [])
        self.assertEqual(package["capability_compatibility_status"], "UNCONFIRMED")
        self.assertEqual(package["checkpoint_refs"], [])
        self.assertIsNone(package["knowledge_handoff_ref"])
        self.assertEqual(package["governance_policy"]["integration_status"], "RESERVED_ONLY")
        self.assertEqual(package["template_binding"]["template_id"], "generic-project")
        self.assertEqual(len(package["phases"]), 3)

    def test_high_risk_generates_pending_manual_gate(self) -> None:
        value = self.envelope(task_id="task-high-risk", risk_level="CRITICAL", human_gates=[])
        package = planning.build_plan_package(value)
        contracts.validate_plan_package(package)
        self.assertTrue(package["human_gates"])
        self.assertEqual(package["human_gates"][0]["condition_type"], "USER_GATE")
        self.assertEqual(package["human_gates"][0]["status"], "PENDING")
        self.assertEqual(package["human_gates"][0]["risk_level"], "CRITICAL")

    def test_unknown_root_and_nested_fields_are_preserved(self) -> None:
        value = self.envelope(task_id="task-unknown", future_root={"keep": True})
        value["scope"]["future_scope_field"] = {"keep": "nested"}  # type: ignore[index]
        value["human_gates"][0]["future_gate_field"] = ["keep"]  # type: ignore[index]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            result = planning.create_plan(self.write_envelope(root, value), state_root=state, apply=True)
            self.assertEqual(result["result"], "CREATED")
            stored = json.loads((state / value["task_id"] / "task-envelope.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["future_root"], {"keep": True})
            self.assertEqual(stored["scope"]["future_scope_field"], {"keep": "nested"})
            self.assertEqual(stored["human_gates"][0]["future_gate_field"], ["keep"])

    def test_repeated_apply_is_idempotent_and_does_not_rewrite_instance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            value = self.envelope(task_id="task-idempotent")
            input_path = self.write_envelope(root, value)
            first = planning.create_plan(input_path, state_root=state, apply=True)
            snapshot = tree_snapshot(state / value["task_id"])
            second = planning.create_plan(input_path, state_root=state, apply=True)
            self.assertEqual(first["result"], "CREATED")
            self.assertEqual(second["result"], "EXISTING_PLAN")
            self.assertTrue(second["no_op"])
            self.assertEqual(snapshot, tree_snapshot(state / value["task_id"]))

    def test_same_task_id_with_different_content_is_a_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            original = self.envelope(task_id="task-conflict")
            input_path = self.write_envelope(root, original)
            self.assertEqual(planning.create_plan(input_path, state_root=state, apply=True)["result"], "CREATED")
            before = tree_snapshot(state / original["task_id"])
            changed = self.envelope(task_id="task-conflict", objective="different objective")
            changed_path = self.write_envelope(root, changed)
            result = planning.create_plan(changed_path, state_root=state, apply=True)
            self.assertEqual(result["result"], "CONFLICT")
            self.assertEqual(result["error_code"], "TASK_ID_CONFLICT")
            self.assertEqual(before, tree_snapshot(state / original["task_id"]))

    def test_invalid_input_and_unsafe_roots_are_zero_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid_state = root / "invalid-state"
            invalid = self.envelope(task_id="task-invalid")
            invalid.pop("objective")
            invalid_path = self.write_envelope(root, invalid)
            invalid_result = planning.create_plan(invalid_path, state_root=invalid_state, apply=True)
            self.assertEqual(invalid_result["result"], "FAILED")
            self.assertEqual(invalid_result["error_code"], "INVALID_CONTRACT")
            self.assertFalse(invalid_state.exists())

            unsafe_state = ROOT / ".f1-02-unsafe-state"
            unsafe_result = planning.create_plan(
                self.write_envelope(root, self.envelope(task_id="task-unsafe")),
                state_root=unsafe_state,
                apply=True,
            )
            self.assertEqual(unsafe_result["result"], "FAILED")
            self.assertEqual(unsafe_result["error_code"], "UNSAFE_STATE_ROOT")
            self.assertFalse(unsafe_state.exists())

    def test_atomic_failure_rolls_back_new_instance_and_staging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            value = self.envelope(task_id="task-rollback")
            input_path = self.write_envelope(root, value)
            original_write = planning.workflow.atomic_write_text
            calls = 0

            def fail_after_some_writes(path: Path, content: str) -> None:
                nonlocal calls
                calls += 1
                if calls == 3:
                    raise OSError("injected write failure")
                original_write(path, content)

            with mock.patch.object(planning.workflow, "atomic_write_text", side_effect=fail_after_some_writes):
                result = planning.create_plan(input_path, state_root=state, apply=True)
            self.assertEqual(result["result"], "FAILED")
            self.assertFalse((state / value["task_id"]).exists())
            self.assertEqual(list(state.glob(f".{value['task_id']}.tmp-*")), [])

    def test_lock_conflict_blocks_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            state.mkdir()
            lock_path = state / ".planning" / "plan-create-task-lock.lock"
            lock = workflow.new_lock(
                "task-lock/plan-package.json",
                workflow.sha256_digest(""),
                "other-agent",
                process_id=os.getpid(),
            )
            workflow.write_lock(lock_path, lock)
            value = self.envelope(task_id="task-lock")
            result = planning.create_plan(self.write_envelope(root, value), state_root=state, apply=True)
            self.assertEqual(result["result"], "CONFLICT")
            self.assertEqual(result["error_code"], "LOCK_CONFLICT")
            self.assertFalse((state / value["task_id"]).exists())

    def test_public_state_root_environment_override_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "env-state"
            input_path = self.write_envelope(root, self.envelope(task_id="task-env-root"))
            with mock.patch.dict(os.environ, {"PHASE_CHECKPOINT_STATE_ROOT": str(state)}, clear=False):
                result = planning.create_plan(input_path, preview=True)
            self.assertEqual(result["result"], "PREVIEW")
            self.assertEqual(Path(result["state_root"]), state.resolve())
            self.assertFalse(state.exists())

    def test_old_v080_project_is_not_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_project = root / "old-project"
            old_project.mkdir()
            old_project.joinpath("WORKFLOW_CHECKLIST.md").write_text("legacy checklist", encoding="utf-8")
            state = root / "new-state"
            value = self.envelope(task_id="task-new-only")
            result = planning.create_plan(self.write_envelope(root, value), state_root=state, apply=True)
            self.assertEqual(result["result"], "CREATED")
            self.assertFalse((old_project / "task-envelope.json").exists())
            self.assertFalse((old_project / "plan-package.json").exists())


if __name__ == "__main__":
    unittest.main()
