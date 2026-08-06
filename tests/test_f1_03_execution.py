#!/usr/bin/env python3
"""F1-03 ExecutionPacket and ExecutionReceipt runtime tests."""

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


FIXTURES = json.loads(
    (ROOT / "tests" / "fixtures" / "f1-01" / "valid_contracts.json").read_text(encoding="utf-8")
)
ENVELOPE = FIXTURES["task_envelope"]


def tree_snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


class F103ExecutionTests(unittest.TestCase):
    def write_envelope(self, root: Path, task_id: str = "task-f103", **updates: object) -> Path:
        value = copy.deepcopy(ENVELOPE)
        value.update({"task_id": task_id, **updates})
        path = root / f"{task_id}.json"
        path.write_text(contracts.stable_json(value), encoding="utf-8")
        return path

    def create_instance(self, root: Path, task_id: str = "task-f103") -> tuple[Path, Path]:
        state = root / "state-root"
        result = planning.create_plan(
            self.write_envelope(root, task_id),
            state_root=state,
            apply=True,
            agent="test-f1-03",
        )
        self.assertEqual(result["result"], "CREATED")
        return state, state / task_id

    def create_packet(self, instance: Path) -> dict[str, object]:
        result = planning.create_packet(instance, "P01", "P01", apply=True, agent="test-f1-03")
        self.assertEqual(result["result"], "CREATED")
        return json.loads((instance / "packets" / f"{result['packet_id']}.json").read_text(encoding="utf-8"))

    def make_receipt(
        self,
        packet: dict[str, object],
        *,
        receipt_id: str = "receipt-f103",
        result: str = "PASS",
        evidence_refs: list[str] | None = None,
        warnings: list[str] | None = None,
        blocking_findings: list[str] | None = None,
        started_at: str = "2026-07-17T10:00:00+08:00",
        completed_at: str = "2026-07-17T10:01:00+08:00",
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "receipt_id": receipt_id,
            "packet_id": packet["packet_id"],
            "plan_id": packet["plan_id"],
            "plan_version": packet["plan_version"],
            "task_id": packet["task_id"],
            "phase_id": packet["phase_id"],
            "work_item_id": packet["work_item_id"],
            "skill_ref": copy.deepcopy(packet["skill_ref"]),
            "result": result,
            "summary": f"{result} execution result",
            "changed_paths": [],
            "created_assets": [],
            "deleted_assets": [],
            "test_results": {"unit": "PASS"},
            "evidence_refs": evidence_refs if evidence_refs is not None else ["evidence/test.txt"],
            "warnings": warnings if warnings is not None else [],
            "blocking_findings": blocking_findings if blocking_findings is not None else [],
            "rollback_status": "NOT_REQUIRED",
            "started_at": started_at,
            "completed_at": completed_at,
            "producer": "test-executor",
            "producer_version": "0.8.0",
        }

    def write_receipt(self, root: Path, receipt: dict[str, object], name: str = "receipt.json") -> Path:
        path = root / name
        path.write_text(contracts.stable_json(receipt), encoding="utf-8")
        return path

    def checklist(self, instance: Path) -> tuple[dict[str, object], list[dict[str, str]]]:
        text = (instance / "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8")
        return workflow.extract_machine_json(text, "workflow"), workflow.checklist_tasks(text)

    def test_packet_preview_is_zero_write_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            before = tree_snapshot(instance)
            first = planning.create_packet(instance, "P01", "P01", preview=True)
            second = planning.create_packet(instance, "P01", "P01", preview=True)
            self.assertEqual(first["result"], "PREVIEW")
            self.assertEqual(first["packet_id"], second["packet_id"])
            self.assertEqual(first["packet_digest"], second["packet_digest"])
            self.assertEqual(before, tree_snapshot(instance))
            self.assertFalse((instance / "packets").exists())

    def test_packet_apply_writes_one_valid_packet_without_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _state, instance = self.create_instance(Path(directory))
            packet = self.create_packet(instance)
            contracts.validate_execution_packet(packet)
            self.assertEqual(packet["dispatch_status"], "MANUAL_SELECTION_REQUIRED")
            self.assertEqual(packet["checkpoint_refs"], [])
            self.assertIsNone(packet["knowledge_handoff_ref"])
            self.assertEqual(list((instance / "packets").glob("*.json")).__len__(), 1)

    def test_packet_id_and_digest_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            first = planning.build_execution_packet(
                json.loads((instance / "plan-package.json").read_text(encoding="utf-8")),
                (instance / "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8"),
                "P01",
                "P01",
            )
            second = planning.build_execution_packet(
                json.loads((instance / "plan-package.json").read_text(encoding="utf-8")),
                (instance / "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8"),
                "P01",
                "P01",
            )
            self.assertEqual(first, second)
            self.assertEqual(planning._packet_digest(first), planning._packet_digest(second))

    def test_repeated_packet_apply_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _state, instance = self.create_instance(Path(directory))
            first = planning.create_packet(instance, "P01", "P01", apply=True)
            before = tree_snapshot(instance)
            second = planning.create_packet(instance, "P01", "P01", apply=True)
            self.assertEqual(first["packet_id"], second["packet_id"])
            self.assertEqual(second["result"], "EXISTING_PACKET")
            self.assertTrue(second["no_op"])
            self.assertEqual(before, tree_snapshot(instance))

    def test_revision_packet_reuses_immutable_source_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            source_packet = self.create_packet(instance)
            source_receipt = self.make_receipt(source_packet, receipt_id="receipt-revision-source")
            self.assertEqual(
                planning.record_receipt(instance, self.write_receipt(root, source_receipt), apply=True)["result"],
                "RECORDED",
            )
            source_packet_path = instance / "packets" / f"{source_packet['packet_id']}.json"
            source_receipt_path = instance / "receipts" / f"{source_receipt['receipt_id']}.json"
            source_packet_before = source_packet_path.read_bytes()
            source_receipt_before = source_receipt_path.read_bytes()

            result = planning.create_packet(
                instance,
                "P01",
                "P01",
                apply=True,
                revision=2,
                revision_type="GOVERNANCE_SEQUENCE_REPAIR",
                predecessor_checkpoint_id="CP-task-f103-P02-7-7",
                revision_reason="PLAN_DEFINITION_CONFLICT_RESOLUTION",
                revision_scope="P03 audit handoff minimum completion definition",
                technical_reexecution=False,
                audit_reexecution=False,
                revision_evidence_refs=["evidence/audit-report.md", "evidence/final-handoff.md"],
            )
            self.assertEqual(result["result"], "CREATED")
            self.assertNotEqual(result["packet_id"], source_packet["packet_id"])
            revision_packet = json.loads(
                (instance / "packets" / f"{result['packet_id']}.json").read_text(encoding="utf-8")
            )
            contracts.validate_execution_packet(revision_packet)
            self.assertEqual(revision_packet["revision"], 2)
            self.assertEqual(revision_packet["predecessor_checkpoint_id"], "CP-task-f103-P02-7-7")
            self.assertFalse(revision_packet["technical_reexecution"])
            self.assertFalse(revision_packet["audit_reexecution"])
            self.assertFalse(revision_packet["receipt_requirements"]["required"])
            self.assertEqual(
                revision_packet["execution_receipt_reuse"]["source_receipt_id"],
                source_receipt["receipt_id"],
            )
            self.assertEqual(source_packet_before, source_packet_path.read_bytes())
            self.assertEqual(source_receipt_before, source_receipt_path.read_bytes())

            repeated = planning.create_packet(
                instance,
                "P01",
                "P01",
                apply=True,
                revision=2,
                revision_type="GOVERNANCE_SEQUENCE_REPAIR",
                predecessor_checkpoint_id="CP-task-f103-P02-7-7",
                revision_reason="PLAN_DEFINITION_CONFLICT_RESOLUTION",
                revision_scope="P03 audit handoff minimum completion definition",
                technical_reexecution=False,
                audit_reexecution=False,
                revision_evidence_refs=["evidence/audit-report.md", "evidence/final-handoff.md"],
            )
            self.assertEqual(repeated["result"], "EXISTING_PACKET")
            self.assertTrue(repeated["no_op"])

    def test_conflicting_revision_is_rejected_without_rewriting_old_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            source_packet = self.create_packet(instance)
            source_receipt = self.make_receipt(source_packet, receipt_id="receipt-revision-conflict")
            planning.record_receipt(instance, self.write_receipt(root, source_receipt), apply=True)
            arguments = {
                "revision": 2,
                "revision_type": "GOVERNANCE_SEQUENCE_REPAIR",
                "predecessor_checkpoint_id": "CP-task-f103-P02-7-7",
                "revision_reason": "PLAN_DEFINITION_CONFLICT_RESOLUTION",
                "revision_scope": "P03 audit handoff minimum completion definition",
                "technical_reexecution": False,
                "audit_reexecution": False,
                "revision_evidence_refs": ["evidence/audit-report.md"],
            }
            first = planning.create_packet(instance, "P01", "P01", apply=True, **arguments)
            self.assertEqual(first["result"], "CREATED")
            before = tree_snapshot(instance)
            conflicting = dict(arguments)
            conflicting["revision_scope"] = "different governance scope"
            second = planning.create_packet(instance, "P01", "P01", apply=True, **conflicting)
            self.assertEqual(second["result"], "CONFLICT")
            self.assertEqual(second["error_code"], "REVISION_CONFLICT")
            self.assertEqual(before, tree_snapshot(instance))

    def test_invalid_phase_and_work_item_are_rejected_without_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _state, instance = self.create_instance(Path(directory))
            before = tree_snapshot(instance)
            bad_phase = planning.create_packet(instance, "P99", "P01", apply=True)
            bad_item = planning.create_packet(instance, "P01", "P99", apply=True)
            self.assertEqual(bad_phase["error_code"], "INVALID_PHASE_ID")
            self.assertEqual(bad_item["error_code"], "INVALID_WORK_ITEM_ID")
            self.assertEqual(before, tree_snapshot(instance))

    def test_confirmed_compatible_capability_is_selected_but_not_called(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            plan_path = instance / "plan-package.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["capability_refs"] = [copy.deepcopy(FIXTURES["capability_ref"])]
            plan_path.write_text(contracts.stable_json(plan), encoding="utf-8")
            result = planning.create_packet(instance, "P01", "P01", preview=True)
            self.assertEqual(result["dispatch_status"], "AUTO_ALLOWED")
            self.assertEqual(result["packet"]["skill_ref"]["skill_id"], "planning-with-files")
            self.assertFalse((instance / "receipts" / "execution.json").exists())

    def test_create_packet_cli_is_structured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root, "task-cli-f103")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "planning.py"),
                    "create-packet",
                    "--instance-root",
                    str(instance),
                    "--phase-id",
                    "P01",
                    "--work-item-id",
                    "P01",
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
            self.assertEqual(output["dispatch_status"], "MANUAL_SELECTION_REQUIRED")

    def test_receipt_preview_is_zero_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            packet = self.create_packet(instance)
            receipt = self.make_receipt(packet)
            receipt_path = self.write_receipt(root, receipt)
            before = tree_snapshot(instance)
            result = planning.record_receipt(instance, receipt_path, preview=True)
            self.assertEqual(result["result"], "PREVIEW")
            self.assertEqual(result["state_update"]["version_classification"], "PATCH")
            self.assertEqual(before, tree_snapshot(instance))
            self.assertFalse((instance / "receipts" / f"{receipt['receipt_id']}.json").exists())

    def test_pass_receipt_updates_work_item_and_keeps_plan_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            packet = self.create_packet(instance)
            plan_before = (instance / "plan-package.json").read_bytes()
            receipt = self.make_receipt(packet)
            result = planning.record_receipt(instance, self.write_receipt(root, receipt), apply=True)
            self.assertEqual(result["result"], "RECORDED")
            stored = json.loads((instance / "receipts" / f"{receipt['receipt_id']}.json").read_text(encoding="utf-8"))
            contracts.validate_execution_receipt(stored)
            metadata, tasks = self.checklist(instance)
            task = next(item for item in tasks if item["ID"] == "P01")
            self.assertEqual(task["状态"], "已完成")
            self.assertEqual(task["核验状态"], "已核验")
            self.assertEqual(metadata["last_execution_receipt_ref"], "receipts/receipt-f103.json")
            self.assertEqual(plan_before, (instance / "plan-package.json").read_bytes())

    def test_pass_without_evidence_does_not_complete_work_item(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            packet = self.create_packet(instance)
            receipt = self.make_receipt(packet, receipt_id="receipt-no-evidence", evidence_refs=[])
            planning.record_receipt(instance, self.write_receipt(root, receipt), apply=True)
            _metadata, tasks = self.checklist(instance)
            task = next(item for item in tasks if item["ID"] == "P01")
            self.assertEqual(task["状态"], "进行中")
            self.assertEqual(task["核验状态"], "待补证据")

    def test_pass_with_warnings_retains_warnings_and_can_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            packet = self.create_packet(instance)
            receipt = self.make_receipt(packet, receipt_id="receipt-warnings", result="PASS_WITH_WARNINGS", warnings=["slow"])
            result = planning.record_receipt(instance, self.write_receipt(root, receipt), apply=True)
            self.assertEqual(result["warnings"], ["slow"])
            text = (instance / "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8")
            self.assertIn("warnings=slow", text)
            task = next(item for item in workflow.checklist_tasks(text) if item["ID"] == "P01")
            self.assertEqual(task["状态"], "已完成")

    def test_failed_receipt_blocks_and_does_not_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            packet = self.create_packet(instance)
            receipt = self.make_receipt(packet, receipt_id="receipt-failed", result="FAILED", evidence_refs=[], blocking_findings=["test failed"])
            planning.record_receipt(instance, self.write_receipt(root, receipt), apply=True)
            metadata, tasks = self.checklist(instance)
            task = next(item for item in tasks if item["ID"] == "P01")
            self.assertEqual(task["状态"], "阻塞")
            self.assertEqual(metadata["overall_status"], "阻塞")
            self.assertIn("blocking=test failed", (instance / "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8"))

    def test_blocked_receipt_sets_blocking_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            packet = self.create_packet(instance)
            receipt = self.make_receipt(packet, receipt_id="receipt-blocked", result="BLOCKED", evidence_refs=[], blocking_findings=["owner gate"])
            result = planning.record_receipt(instance, self.write_receipt(root, receipt), apply=True)
            self.assertEqual(result["result"], "RECORDED")
            metadata, _tasks = self.checklist(instance)
            self.assertEqual(metadata["overall_status"], "阻塞")

    def test_inconclusive_receipt_transfers_to_human_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            packet = self.create_packet(instance)
            receipt = self.make_receipt(packet, receipt_id="receipt-inconclusive", result="INCONCLUSIVE", evidence_refs=[])
            planning.record_receipt(instance, self.write_receipt(root, receipt), apply=True)
            text = (instance / "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8")
            self.assertIn("HUMAN_GATE_REQUIRED", text)
            task = next(item for item in workflow.checklist_tasks(text) if item["ID"] == "P01")
            self.assertEqual(task["状态"], "阻塞")

    def test_receipt_mismatch_is_zero_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            packet = self.create_packet(instance)
            receipt = self.make_receipt(packet, receipt_id="receipt-mismatch")
            receipt["plan_id"] = "plan-other"
            before = tree_snapshot(instance)
            result = planning.record_receipt(instance, self.write_receipt(root, receipt), apply=True)
            self.assertEqual(result["error_code"], "RECEIPT_MISMATCH")
            self.assertEqual(before, tree_snapshot(instance))

    def test_receipt_time_order_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            packet = self.create_packet(instance)
            receipt = self.make_receipt(packet, receipt_id="receipt-time", started_at="2026-07-17T10:02:00+08:00")
            result = planning.record_receipt(instance, self.write_receipt(root, receipt), preview=True)
            self.assertEqual(result["error_code"], "INVALID_RECEIPT_TIME")

    def test_duplicate_receipt_apply_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            packet = self.create_packet(instance)
            receipt = self.make_receipt(packet, receipt_id="receipt-duplicate")
            path = self.write_receipt(root, receipt)
            first = planning.record_receipt(instance, path, apply=True)
            before = tree_snapshot(instance)
            second = planning.record_receipt(instance, path, apply=True)
            self.assertEqual(first["result"], "RECORDED")
            self.assertEqual(second["result"], "EXISTING_RECEIPT")
            self.assertTrue(second["no_op"])
            self.assertEqual(before, tree_snapshot(instance))

    def test_same_receipt_id_with_different_content_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            packet = self.create_packet(instance)
            first = self.make_receipt(packet, receipt_id="receipt-id-conflict")
            first_path = self.write_receipt(root, first, "first.json")
            planning.record_receipt(instance, first_path, apply=True)
            changed = self.make_receipt(packet, receipt_id="receipt-id-conflict", result="FAILED", evidence_refs=[])
            result = planning.record_receipt(instance, self.write_receipt(root, changed, "changed.json"), apply=True)
            self.assertEqual(result["error_code"], "RECEIPT_ID_CONFLICT")

    def test_equivalent_receipt_with_new_identity_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            packet = self.create_packet(instance)
            first = self.make_receipt(packet, receipt_id="receipt-equivalent")
            planning.record_receipt(instance, self.write_receipt(root, first, "first.json"), apply=True)
            second = self.make_receipt(
                packet,
                receipt_id="receipt-equivalent-retry",
                started_at="2026-07-17T10:02:00+08:00",
                completed_at="2026-07-17T10:03:00+08:00",
            )
            result = planning.record_receipt(instance, self.write_receipt(root, second, "retry.json"), apply=True)
            self.assertEqual(result["result"], "EXISTING_RECEIPT")
            self.assertFalse((instance / "receipts" / "receipt-equivalent-retry.json").exists())

    def test_conflicting_terminal_receipt_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            packet = self.create_packet(instance)
            first = self.make_receipt(packet, receipt_id="receipt-terminal-one")
            planning.record_receipt(instance, self.write_receipt(root, first), apply=True)
            second = self.make_receipt(packet, receipt_id="receipt-terminal-two", result="FAILED", evidence_refs=[])
            result = planning.record_receipt(instance, self.write_receipt(root, second, "second.json"), apply=True)
            self.assertEqual(result["error_code"], "RECEIPT_RESULT_CONFLICT")

    def test_unknown_receipt_fields_and_nested_fields_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            packet = self.create_packet(instance)
            receipt = self.make_receipt(packet, receipt_id="receipt-unknown")
            receipt["future_root"] = {"keep": True}
            receipt["test_results"]["future_nested"] = {"keep": "nested"}  # type: ignore[index]
            planning.record_receipt(instance, self.write_receipt(root, receipt), apply=True)
            stored = json.loads((instance / "receipts" / "receipt-unknown.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["future_root"], {"keep": True})
            self.assertEqual(stored["test_results"]["future_nested"], {"keep": "nested"})

    def test_checklist_unknown_machine_fields_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            checklist_path = instance / "WORKFLOW_CHECKLIST.md"
            original = checklist_path.read_text(encoding="utf-8")
            metadata = workflow.extract_machine_json(original, "workflow")
            metadata["future_machine_field"] = {"keep": True}
            checklist_path.write_text(workflow.replace_machine_json(original, "workflow", metadata), encoding="utf-8")
            packet = self.create_packet(instance)
            receipt = self.make_receipt(packet, receipt_id="receipt-checklist-unknown")
            planning.record_receipt(instance, self.write_receipt(root, receipt), apply=True)
            updated = workflow.extract_machine_json(checklist_path.read_text(encoding="utf-8"), "workflow")
            self.assertEqual(updated["future_machine_field"], {"keep": True})

    def test_receipt_apply_does_not_rewrite_plan_package_or_migrate_old_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            packet = self.create_packet(instance)
            plan_before = (instance / "plan-package.json").read_bytes()
            envelope_before = (instance / "task-envelope.json").read_bytes()
            receipt = self.make_receipt(packet, receipt_id="receipt-frozen-plan")
            planning.record_receipt(instance, self.write_receipt(root, receipt), apply=True)
            self.assertEqual(plan_before, (instance / "plan-package.json").read_bytes())
            self.assertEqual(envelope_before, (instance / "task-envelope.json").read_bytes())
            self.assertEqual(list(root.glob("old-project/*")), [])

    def test_packet_lock_conflict_blocks_apply(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, instance = self.create_instance(root, "task-packet-lock")
            lock_path = state / ".planning" / f"packet-{instance.name}.lock"
            lock = workflow.new_lock(
                f"{instance.name}/packets/pending.json",
                workflow.sha256_digest(""),
                "other-agent",
                process_id=os.getpid(),
            )
            workflow.write_lock(lock_path, lock)
            result = planning.create_packet(instance, "P01", "P01", apply=True)
            self.assertEqual(result["error_code"], "LOCK_CONFLICT")
            self.assertFalse((instance / "packets").exists())

    def test_receipt_lock_conflict_blocks_apply(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, instance = self.create_instance(root, "task-receipt-lock")
            packet = self.create_packet(instance)
            lock_path = state / ".planning" / f"receipt-{instance.name}.lock"
            lock = workflow.new_lock(
                f"{instance.name}/WORKFLOW_CHECKLIST.md",
                workflow.file_digest(instance / "WORKFLOW_CHECKLIST.md"),
                "other-agent",
                process_id=os.getpid(),
            )
            workflow.write_lock(lock_path, lock)
            receipt = self.make_receipt(packet, receipt_id="receipt-lock")
            result = planning.record_receipt(instance, self.write_receipt(root, receipt), apply=True)
            self.assertEqual(result["error_code"], "LOCK_CONFLICT")
            self.assertFalse((instance / "receipts" / "receipt-lock.json").exists())

    def test_receipt_atomic_failure_rolls_back_both_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root, "task-receipt-rollback")
            packet = self.create_packet(instance)
            receipt = self.make_receipt(packet, receipt_id="receipt-rollback")
            path = self.write_receipt(root, receipt)
            before = tree_snapshot(instance)
            original_write = planning.workflow.atomic_write_text
            calls = 0

            def fail_on_receipt_stage(target: Path, content: str) -> None:
                nonlocal calls
                calls += 1
                if calls == 3:
                    raise OSError("injected receipt transaction failure")
                original_write(target, content)

            with mock.patch.object(planning.workflow, "atomic_write_text", side_effect=fail_on_receipt_stage):
                result = planning.record_receipt(instance, path, apply=True)
            self.assertEqual(result["result"], "FAILED")
            self.assertEqual(before, tree_snapshot(instance))
            self.assertFalse(list(instance.parent.glob(f".{instance.name}.f1-03-*")))

    def test_record_receipt_cli_is_structured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root, "task-receipt-cli")
            packet = self.create_packet(instance)
            receipt_path = self.write_receipt(root, self.make_receipt(packet, receipt_id="receipt-cli"))
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "planning.py"),
                    "record-receipt",
                    "--instance-root",
                    str(instance),
                    "--receipt",
                    str(receipt_path),
                    "--preview",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["result"], "PREVIEW")

    def test_existing_f1_02_create_plan_behavior_remains_available(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            result = planning.create_plan(self.write_envelope(root, "task-f102-compat"), state_root=state, preview=True)
            self.assertEqual(result["result"], "PREVIEW")
            self.assertFalse(state.exists())


if __name__ == "__main__":
    unittest.main()
