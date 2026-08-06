#!/usr/bin/env python3
"""F1-05 checkpoint reference, pause, and resume consumer tests."""

from __future__ import annotations

import copy
import json
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
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


class F105CheckpointTests(unittest.TestCase):
    def envelope(self, **updates: object) -> dict[str, object]:
        value = copy.deepcopy(FIXTURE)
        value.update(updates)
        return value

    def write_json(self, root: Path, name: str, value: dict[str, object]) -> Path:
        path = root / name
        path.write_text(contracts.stable_json(value), encoding="utf-8")
        return path

    def create_instance(self, root: Path, task_id: str = "task-f105", **updates: object) -> tuple[Path, Path]:
        envelope = self.envelope(task_id=task_id, **updates)
        envelope_path = self.write_json(root, f"{task_id}.json", envelope)
        state = root / "state-root"
        result = planning.create_plan(envelope_path, state_root=state, apply=True)
        self.assertEqual(result["result"], "CREATED", result)
        return state, state / task_id

    def checkpoint_ref(
        self,
        instance: Path,
        *,
        checkpoint_id: str = "cp-f105-01",
        action: str = "ADVANCE_PHASE",
        status: str = "PASSED",
        phase_id: str = "P01",
        resume_entry: str = "P01/P01",
        receipt_name: str = "checkpoint-receipt.json",
        create_receipt: bool = True,
    ) -> tuple[dict[str, object], Path]:
        plan = json.loads((instance / "plan-package.json").read_text(encoding="utf-8"))
        receipt_path = instance / receipt_name
        if create_receipt:
            receipt_path.write_text(
                contracts.stable_json({"fixture": True, "checkpoint_id": checkpoint_id, "producer": "fixture-test-producer"}),
                encoding="utf-8",
            )
        audit_digest = workflow.file_digest(instance / "5_audit.md")
        ref = {
            "checkpoint_id": checkpoint_id,
            "checkpoint_status": status,
            "task_id": plan["task_id"],
            "plan_id": plan["plan_id"],
            "plan_version": plan["plan_version"],
            "phase_id": phase_id,
            "effective_action": action,
            "evidence_refs": [{"path": "5_audit.md", "sha256": audit_digest, "fixture": True}],
            "resume_entry": resume_entry,
            "receipt_location": receipt_name,
            "producer": "fixture-test-producer",
            "producer_version": "0.0.1",
            "created_at": "2026-07-17T00:00:00Z",
            "fixture": True,
        }
        return ref, receipt_path

    def write_ref(self, root: Path, ref: dict[str, object], name: str = "checkpoint-ref.json") -> Path:
        return self.write_json(root, name, ref)

    def record(self, instance: Path, root: Path, ref: dict[str, object], *, apply: bool = True) -> dict[str, object]:
        return planning.record_checkpoint_ref(
            instance,
            self.write_ref(root, ref),
            apply=apply,
            preview=not apply,
        )

    def test_checkpoint_ref_contract_and_fixture_producer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            ref, _receipt = self.checkpoint_ref(instance)
            contracts.validate_checkpoint_ref(ref)
            self.assertEqual(contracts.contract_field_count("checkpoint_ref"), 11)
            self.assertEqual(ref["producer"], "fixture-test-producer")

    def test_record_advance_preview_is_zero_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, instance = self.create_instance(root)
            ref, _receipt = self.checkpoint_ref(instance)
            before = tree_snapshot(instance)
            result = self.record(instance, root, ref, apply=False)
            self.assertEqual(result["result"], "PREVIEW")
            self.assertTrue(result["would_write"])
            self.assertFalse((instance / "checkpoints").exists())
            self.assertFalse((state / "phase-checkpoints").exists())
            self.assertEqual(before, tree_snapshot(instance))

    def test_record_advance_apply_maps_verified_ready_and_preserves_unknowns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, instance = self.create_instance(root)
            ref, _receipt = self.checkpoint_ref(instance)
            result = self.record(instance, root, ref)
            self.assertEqual(result["result"], "RECORDED_CHECKPOINT_REF")
            self.assertEqual(result["checkpoint_consumer_status"], "VERIFIED")
            self.assertEqual(result["resume_status"], "READY")
            stored = json.loads((instance / "checkpoints" / "refs" / "cp-f105-01.json").read_text(encoding="utf-8"))
            self.assertTrue(stored["fixture"])
            self.assertTrue(stored["verified_evidence"][0]["sha256"])
            metadata = workflow.extract_machine_json((instance / "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8"), "workflow")
            self.assertEqual(metadata["checkpoint_consumer_status"], "VERIFIED")
            self.assertEqual(metadata["resume_status"], "READY")
            self.assertEqual(metadata["last_trusted_checkpoint"], "cp-f105-01")
            self.assertFalse((state / "phase-checkpoints").exists())

    def test_published_commit_is_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            ref, _receipt = self.checkpoint_ref(instance, checkpoint_id="cp-f105-published", action="PUBLISHED_COMMIT")
            result = self.record(instance, root, ref)
            self.assertEqual(result["effective_action"], "PUBLISHED_COMMIT")
            self.assertEqual(result["resume_status"], "READY")

    def test_hold_blocked_and_failed_map_to_paused(self) -> None:
        for action in ("HOLD", "BLOCKED", "FAILED"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                _state, instance = self.create_instance(root, f"task-{action.lower()}")
                ref, _receipt = self.checkpoint_ref(instance, checkpoint_id=f"cp-{action.lower()}", action=action, status="UNKNOWN")
                ref["blocking_findings"] = [f"fixture {action.lower()} reason"]
                result = self.record(instance, root, ref)
                self.assertEqual(result["checkpoint_consumer_status"], "VERIFIED")
                self.assertEqual(result["resume_status"], "PAUSED")
                self.assertEqual(result["state_update"]["pause_status"], "PAUSED")

    def test_inconclusive_and_unknown_map_to_human_gate(self) -> None:
        for action in ("INCONCLUSIVE", "UNKNOWN_ACTION"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                _state, instance = self.create_instance(root, f"task-{action.lower()}")
                ref, _receipt = self.checkpoint_ref(instance, checkpoint_id=f"cp-{action.lower()}", action=action, status="UNKNOWN")
                result = self.record(instance, root, ref)
                self.assertEqual(result["resume_status"], "WAITING_FOR_HUMAN")
                metadata = workflow.extract_machine_json((instance / "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8"), "workflow")
                self.assertEqual(metadata["human_execution_gate"], "REQUIRED")

    def test_closed_checkpoint_is_completion_candidate_not_auto_close(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root, human_gates=[])
            ref, _receipt = self.checkpoint_ref(instance, checkpoint_id="cp-f105-closed", action="COMPLETION_CANDIDATE", status="CLOSED")
            result = self.record(instance, root, ref)
            self.assertEqual(result["resume_status"], "READY")
            self.assertEqual(result["state_update"]["task_status"], "COMPLETION_CANDIDATE")
            resume = planning.resume_from_checkpoint(instance, "cp-f105-closed", preview=True)
            self.assertEqual(resume["error_code"], "COMPLETION_GATE_REQUIRED")

    def test_task_plan_and_phase_mismatches_are_rejected(self) -> None:
        cases = (("task_id", "wrong-task"), ("plan_id", "plan-wrong"), ("phase_id", "P99"))
        for field, value in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                _state, instance = self.create_instance(root, f"task-mismatch-{field}")
                ref, _receipt = self.checkpoint_ref(instance, checkpoint_id=f"cp-{field}")
                ref[field] = value
                result = self.record(instance, root, ref)
                self.assertEqual(result["result"], "FAILED")
                self.assertIn(result["error_code"], {"REFERENCE_MISMATCH", "INVALID_PHASE_ID"})
                self.assertFalse((instance / "checkpoints").exists())

    def test_receipt_location_and_binding_paths_are_checked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            ref, receipt = self.checkpoint_ref(
                instance, checkpoint_id="cp-missing-receipt", receipt_name="missing.json", create_receipt=False
            )
            self.assertFalse(receipt.exists())
            result = self.record(instance, root, ref)
            self.assertEqual(result["error_code"], "CHECKPOINT_RECEIPT_NOT_FOUND")
            ref, _receipt = self.checkpoint_ref(instance, checkpoint_id="cp-outside")
            ref["receipt_location"] = str(ROOT / "VERSION")
            result = self.record(instance, root, ref)
            self.assertEqual(result["error_code"], "CHECKPOINT_PATH_NOT_ALLOWED")

    def test_missing_evidence_digest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            ref, _receipt = self.checkpoint_ref(instance, checkpoint_id="cp-no-digest")
            ref["evidence_refs"] = ["5_audit.md"]
            result = self.record(instance, root, ref)
            self.assertEqual(result["error_code"], "CHECKPOINT_EVIDENCE_DIGEST_MISSING")

    def test_projection_drift_blocks_record_and_resume_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            ref, _receipt = self.checkpoint_ref(instance)
            (instance / "5_audit.md").write_text("drifted fixture audit\n", encoding="utf-8")
            before = tree_snapshot(instance)
            result = self.record(instance, root, ref)
            self.assertEqual(result["error_code"], "CHECKPOINT_PROJECTION_DRIFT")
            self.assertEqual(before, tree_snapshot(instance))

    def test_record_apply_is_idempotent_and_same_id_content_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            ref, _receipt = self.checkpoint_ref(instance)
            first = self.record(instance, root, ref)
            before = tree_snapshot(instance)
            second = self.record(instance, root, ref)
            self.assertEqual(first["result"], "RECORDED_CHECKPOINT_REF")
            self.assertEqual(second["result"], "EXISTING_CHECKPOINT_REF")
            self.assertTrue(second["idempotent"])
            self.assertEqual(before, tree_snapshot(instance))
            changed = copy.deepcopy(ref)
            changed["resume_entry"] = "P01"
            conflict = self.record(instance, root, changed)
            self.assertEqual(conflict["error_code"], "CHECKPOINT_ID_CONFLICT")

    def test_conflicting_checkpoint_decisions_for_same_phase_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            first, _receipt = self.checkpoint_ref(instance, checkpoint_id="cp-f105-hold", action="HOLD", status="UNKNOWN")
            self.record(instance, root, first)
            second, _receipt = self.checkpoint_ref(instance, checkpoint_id="cp-f105-advance", action="ADVANCE_PHASE")
            result = self.record(instance, root, second)
            self.assertEqual(result["error_code"], "CHECKPOINT_DECISION_CONFLICT")

    def test_resume_preview_is_zero_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root, human_gates=[])
            ref, _receipt = self.checkpoint_ref(instance)
            self.record(instance, root, ref)
            before = tree_snapshot(instance)
            result = planning.resume_from_checkpoint(instance, "cp-f105-01", preview=True)
            self.assertEqual(result["result"], "PREVIEW")
            self.assertTrue(result["would_write"])
            self.assertEqual(result["proposed_status"], "RESUMED")
            self.assertFalse((instance / "checkpoints" / "resumes").exists())
            self.assertEqual(before, tree_snapshot(instance))

    def test_resume_apply_and_repeat_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root, human_gates=[])
            ref, _receipt = self.checkpoint_ref(instance)
            self.record(instance, root, ref)
            first = planning.resume_from_checkpoint(instance, "cp-f105-01", apply=True)
            before = tree_snapshot(instance)
            second = planning.resume_from_checkpoint(instance, "cp-f105-01", apply=True)
            self.assertEqual(first["result"], "RESUMED")
            self.assertEqual(second["result"], "ALREADY_RESUMED")
            self.assertTrue(second["no_op"])
            self.assertEqual(before, tree_snapshot(instance))
            record = json.loads((instance / "checkpoints" / "resumes" / "cp-f105-01.json").read_text(encoding="utf-8"))
            self.assertEqual(record["resume_status"], "RESUMED")

    def test_plan_package_replacement_rejects_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            ref, _receipt = self.checkpoint_ref(instance)
            self.record(instance, root, ref)
            plan_path = instance / "plan-package.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["objective"] = "replaced plan"
            plan_path.write_text(contracts.stable_json(plan), encoding="utf-8")
            result = planning.resume_from_checkpoint(instance, "cp-f105-01", preview=True)
            self.assertEqual(result["error_code"], "PLAN_PACKAGE_REPLACED")

    def test_blocked_cleanliness_receipt_prevents_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            request_result = planning.create_governance_request(instance, "PRE_WRITE", "P01", apply=True)
            request = request_result["request"]
            receipt = {
                "receipt_id": "cleanliness-f105-blocked",
                "request_id": request["request_id"],
                "task_id": request["task_id"],
                "plan_id": request["plan_id"],
                "phase_id": request["phase_id"],
                "governance_stage": request["governance_stage"],
                "result": "BLOCKED",
                "cleanliness_status": "BLOCKED",
                "scope_match": True,
                "blocking_findings": ["fixture protected asset changed"],
                "non_blocking_findings": [],
                "duplicate_candidates": ["KEEP"],
                "unused_asset_candidates": ["KEEP"],
                "cleanup_actions": [],
                "protected_assets_status": {"status": "PRESERVED"},
                "evidence_refs": ["fixture/f1-05-blocked.json"],
                "checked_at": "2026-07-17T00:00:00Z",
                "producer": "fixture-test-producer",
                "producer_version": "0.0.1",
            }
            receipt_path = self.write_json(root, "blocked-cleanliness.json", receipt)
            self.assertEqual(planning.record_cleanliness_receipt(instance, receipt_path, apply=True)["result"], "RECORDED_CLEANLINESS_RECEIPT")
            ref, _receipt = self.checkpoint_ref(instance)
            self.record(instance, root, ref)
            before = tree_snapshot(instance)
            result = planning.resume_from_checkpoint(instance, "cp-f105-01", apply=True)
            self.assertEqual(result["error_code"], "CHECKPOINT_BLOCKED_BY_GOVERNANCE")
            self.assertEqual(before, tree_snapshot(instance))

    def test_later_same_stage_pass_supersedes_historical_blocked_receipt_for_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root, human_gates=[])

            first_request = planning.create_governance_request(instance, "PRE_WRITE", "P01", apply=True)["request"]
            blocked = {
                "receipt_id": "cleanliness-f105-historical-block",
                "request_id": first_request["request_id"],
                "task_id": first_request["task_id"],
                "plan_id": first_request["plan_id"],
                "phase_id": first_request["phase_id"],
                "governance_stage": first_request["governance_stage"],
                "result": "BLOCKED",
                "cleanliness_status": "BLOCKED",
                "scope_match": False,
                "blocking_findings": ["historical retry was out of scope"],
                "non_blocking_findings": [],
                "duplicate_candidates": ["KEEP"],
                "unused_asset_candidates": ["KEEP"],
                "cleanup_actions": [],
                "protected_assets_status": {"status": "PRESERVED"},
                "evidence_refs": ["5_audit.md"],
                "checked_at": "2026-07-17T00:00:00Z",
                "producer": "fixture-test-producer",
                "producer_version": "0.0.1",
            }
            blocked_path = self.write_json(root, "historical-blocked.json", blocked)
            self.assertEqual(
                planning.record_cleanliness_receipt(instance, blocked_path, apply=True)["result"],
                "RECORDED_CLEANLINESS_RECEIPT",
            )

            checklist_path = instance / "WORKFLOW_CHECKLIST.md"
            checklist = workflow.extract_machine_json(checklist_path.read_text(encoding="utf-8"), "workflow")
            checklist["known_dirty_paths"] = ["current-retry-marker.txt"]
            checklist_path.write_text(
                workflow.replace_machine_json(
                    checklist_path.read_text(encoding="utf-8"),
                    "workflow",
                    checklist,
                ),
                encoding="utf-8",
            )
            second_request = planning.create_governance_request(instance, "PRE_WRITE", "P01", apply=True)["request"]
            passed = {
                "receipt_id": "cleanliness-f105-current-pass",
                "request_id": second_request["request_id"],
                "task_id": second_request["task_id"],
                "plan_id": second_request["plan_id"],
                "phase_id": second_request["phase_id"],
                "governance_stage": second_request["governance_stage"],
                "result": "PASS",
                "cleanliness_status": "COMPLETED",
                "scope_match": True,
                "blocking_findings": [],
                "non_blocking_findings": [],
                "duplicate_candidates": ["KEEP"],
                "unused_asset_candidates": ["KEEP"],
                "cleanup_actions": [],
                "protected_assets_status": {"status": "PRESERVED"},
                "evidence_refs": ["5_audit.md"],
                "checked_at": "2026-07-17T00:01:00Z",
                "producer": "fixture-test-producer",
                "producer_version": "0.0.1",
            }
            passed_path = self.write_json(root, "current-pass.json", passed)
            self.assertEqual(
                planning.record_cleanliness_receipt(instance, passed_path, apply=True)["result"],
                "RECORDED_CLEANLINESS_RECEIPT",
            )

            ref, _receipt = self.checkpoint_ref(instance)
            self.record(instance, root, ref)
            result = planning.resume_from_checkpoint(instance, "cp-f105-01", apply=True)
            self.assertEqual(result["result"], "RESUMED", result)
            self.assertTrue((instance / "governance" / "receipts" / "cleanliness-f105-historical-block.json").is_file())

    def test_inconclusive_cleanliness_requires_human_and_warnings_are_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root, human_gates=[])
            request_result = planning.create_governance_request(instance, "PRE_WRITE", "P01", apply=True)
            request = request_result["request"]
            receipt = {
                "receipt_id": "cleanliness-f105-warning",
                "request_id": request["request_id"],
                "task_id": request["task_id"],
                "plan_id": request["plan_id"],
                "phase_id": request["phase_id"],
                "governance_stage": request["governance_stage"],
                "result": "PASS_WITH_WARNINGS",
                "cleanliness_status": "WARNINGS",
                "scope_match": True,
                "blocking_findings": [],
                "non_blocking_findings": ["fixture warning"],
                "duplicate_candidates": ["KEEP"],
                "unused_asset_candidates": ["KEEP"],
                "cleanup_actions": [],
                "protected_assets_status": {"status": "PRESERVED"},
                "evidence_refs": ["fixture/f1-05-warning.json"],
                "checked_at": "2026-07-17T00:00:00Z",
                "producer": "fixture-test-producer",
                "producer_version": "0.0.1",
            }
            receipt_path = self.write_json(root, "warning-cleanliness.json", receipt)
            self.assertEqual(planning.record_cleanliness_receipt(instance, receipt_path, apply=True)["result"], "RECORDED_CLEANLINESS_RECEIPT")
            ref, _receipt = self.checkpoint_ref(instance)
            self.record(instance, root, ref)
            result = planning.resume_from_checkpoint(instance, "cp-f105-01", apply=True)
            self.assertEqual(result["result"], "RESUMED")
            self.assertIn("fixture warning", result["warnings"])

    def test_inconclusive_cleanliness_requires_human_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root, human_gates=[])
            request_result = planning.create_governance_request(instance, "PRE_WRITE", "P01", apply=True)
            request = request_result["request"]
            receipt = {
                "receipt_id": "cleanliness-f105-inconclusive",
                "request_id": request["request_id"],
                "task_id": request["task_id"],
                "plan_id": request["plan_id"],
                "phase_id": request["phase_id"],
                "governance_stage": request["governance_stage"],
                "result": "INCONCLUSIVE",
                "cleanliness_status": "INCONCLUSIVE",
                "scope_match": True,
                "blocking_findings": [],
                "non_blocking_findings": ["fixture needs owner decision"],
                "duplicate_candidates": ["KEEP"],
                "unused_asset_candidates": ["KEEP"],
                "cleanup_actions": [],
                "protected_assets_status": {"status": "PRESERVED"},
                "evidence_refs": ["fixture/f1-05-inconclusive.json"],
                "checked_at": "2026-07-17T00:00:00Z",
                "producer": "fixture-test-producer",
                "producer_version": "0.0.1",
            }
            receipt_path = self.write_json(root, "inconclusive-cleanliness.json", receipt)
            self.assertEqual(
                planning.record_cleanliness_receipt(instance, receipt_path, apply=True)["result"],
                "RECORDED_CLEANLINESS_RECEIPT",
            )
            ref, _receipt = self.checkpoint_ref(instance)
            self.record(instance, root, ref)
            result = planning.resume_from_checkpoint(instance, "cp-f105-01", preview=True)
            self.assertEqual(result["error_code"], "HUMAN_GATE_REQUIRED")

    def test_checkpoint_consumer_does_not_create_external_authority_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root, human_gates=[])
            ref, _receipt = self.checkpoint_ref(instance)
            self.record(instance, root, ref)
            self.assertEqual(planning.resume_from_checkpoint(instance, "cp-f105-01", apply=True)["result"], "RESUMED")
            authority_names = {"result.json", "commit.json", "head.json"}
            self.assertEqual(
                [path for path in root.rglob("*") if path.is_file() and path.name in authority_names],
                [],
            )
            self.assertFalse((root / "state-root" / "phase-checkpoints").exists())

    def test_unresolved_plan_human_gate_blocks_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            ref, _receipt = self.checkpoint_ref(instance)
            self.record(instance, root, ref)
            result = planning.resume_from_checkpoint(instance, "cp-f105-01", preview=True)
            self.assertEqual(result["error_code"], "HUMAN_GATE_REQUIRED")

    def test_final_manual_acceptance_gate_is_deferred_before_last_phase_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root, human_gates=[])
            plan_path = instance / "plan-package.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["human_gates"] = [
                {
                    "condition_id": "gate-deep-grill-manual-acceptance",
                    "condition_type": "USER_GATE",
                    "description": "最终人工验收与独立只读审计完成后才允许正式封板",
                    "required": True,
                    "evidence_required": True,
                    "evaluation_method": "manual",
                    "status": "PENDING",
                    "evidence_refs": [],
                    "risk_level": "MEDIUM",
                }
            ]
            plan_path.write_text(contracts.stable_json(plan), encoding="utf-8")
            ref, _receipt = self.checkpoint_ref(instance)
            self.record(instance, root, ref)
            result = planning.resume_from_checkpoint(instance, "cp-f105-01", preview=True)
            self.assertEqual(result["result"], "PREVIEW", result)
            self.assertEqual(result["proposed_status"], "RESUMED")

    def test_atomic_checkpoint_ref_write_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            ref, _receipt = self.checkpoint_ref(instance)
            before = tree_snapshot(instance)
            original_write = planning.workflow.atomic_write_text
            calls = 0

            def fail_on_second_write(target: Path, content: str) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected F1-05 ref transaction failure")
                original_write(target, content)

            with mock.patch.object(planning.workflow, "atomic_write_text", side_effect=fail_on_second_write):
                result = self.record(instance, root, ref)
            self.assertEqual(result["result"], "FAILED")
            self.assertEqual(before, tree_snapshot(instance))
            self.assertEqual(list(instance.parent.glob(f".{instance.name}.f1-05-*")), [])

    def test_atomic_resume_write_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            ref, _receipt = self.checkpoint_ref(instance)
            self.record(instance, root, ref)
            before = tree_snapshot(instance)
            original_write = planning.workflow.atomic_write_text
            calls = 0

            def fail_on_second_write(target: Path, content: str) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected F1-05 resume transaction failure")
                original_write(target, content)

            with mock.patch.object(planning.workflow, "atomic_write_text", side_effect=fail_on_second_write):
                result = planning.resume_from_checkpoint(instance, "cp-f105-01", apply=True)
            self.assertEqual(result["result"], "FAILED")
            self.assertEqual(before, tree_snapshot(instance))
            self.assertEqual(list(instance.parent.glob(f".{instance.name}.f1-05-resume-*")), [])

    def test_unknown_root_and_nested_fields_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            ref, _receipt = self.checkpoint_ref(instance)
            ref["future_root"] = {"keep": True}
            ref["evidence_refs"][0]["future_nested"] = {"keep": "nested"}
            self.record(instance, root, ref)
            stored = json.loads((instance / "checkpoints" / "refs" / "cp-f105-01.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["future_root"], {"keep": True})
            self.assertEqual(stored["evidence_refs"][0]["future_nested"], {"keep": "nested"})

    def test_cli_entries_are_structured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            ref, _receipt = self.checkpoint_ref(instance)
            ref_path = self.write_ref(root, ref)
            for command in (
                ["record-checkpoint-ref", "--instance-root", str(instance), "--checkpoint-ref", str(ref_path), "--preview"],
                ["resume-from-checkpoint", "--instance-root", str(instance), "--checkpoint-id", "cp-f105-01", "--preview"],
            ):
                completed = subprocess.run(
                    [sys.executable, str(ROOT / "scripts" / "planning.py"), *command],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                payload = json.loads(completed.stdout)
                self.assertEqual(payload["result"], "PREVIEW" if command[0] == "record-checkpoint-ref" else "FAILED")

    def test_existing_runtime_commands_remain_available(self) -> None:
        completed = subprocess.run([sys.executable, str(ROOT / "scripts" / "planning.py"), "--help"], capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0)
        for command in ("create-plan", "create-packet", "record-receipt", "create-governance-request", "record-cleanliness-receipt"):
            self.assertIn(command, completed.stdout)


if __name__ == "__main__":
    unittest.main()
