#!/usr/bin/env python3
"""F1-07 final completion gate and compact verification tests."""

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


FIXTURES = json.loads(
    (ROOT / "tests" / "fixtures" / "f1-01" / "valid_contracts.json").read_text(encoding="utf-8")
)
BASE_ENVELOPE = FIXTURES["task_envelope"]


def tree_snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


class F107FinalizationTests(unittest.TestCase):
    def write_json(self, path: Path, value: dict[str, object]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contracts.stable_json(value), encoding="utf-8")
        return path

    def create_instance(
        self,
        root: Path,
        task_id: str,
        *,
        mode: str = "SIMPLE",
        policy: dict[str, object] | None = None,
        knowledge_policy: dict[str, object] | None = None,
    ) -> tuple[Path, Path]:
        envelope = copy.deepcopy(BASE_ENVELOPE)
        envelope.update(
            {
                "task_id": task_id,
                "human_gates": [],
                "knowledge_policy": knowledge_policy
                or {
                    "level": "NONE",
                    "required_evidence": [],
                    "required_images": [],
                    "prohibited_content": [],
                    "redaction_requirements": [],
                    "ingest_required": False,
                },
                "finalization_policy": {"mode": mode, **(policy or {})},
            }
        )
        source = self.write_json(root / f"{task_id}.json", envelope)
        state = root / "state-root"
        created = planning.create_plan(source, state_root=state, apply=True, agent="test-f1-07")
        self.assertEqual(created["result"], "CREATED", created)
        instance = state / task_id
        (instance / "evidence").mkdir()
        (instance / "evidence" / "final.txt").write_text("final evidence\n", encoding="utf-8")
        self.satisfy_plan_and_checklist(instance)
        return state, instance

    def satisfy_plan_and_checklist(self, instance: Path, *, evidence: str = "evidence/final.txt") -> None:
        plan_path = instance / "plan-package.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        for condition in plan["completion_conditions"]:
            condition["status"] = "SATISFIED"
            condition["evidence_refs"] = [evidence]
        plan_path.write_text(contracts.stable_json(plan), encoding="utf-8")

        lines = (instance / workflow.CHECKLIST_NAME).read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if not line.startswith("| P") or line.startswith("|---"):
                continue
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            cells[6] = "已完成"
            cells[7] = "已核验"
            cells[8] = evidence
            lines[index] = "| " + " | ".join(cells) + " |"
        (instance / workflow.CHECKLIST_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")

    def make_execution_receipt(self, packet: dict[str, object], *, result: str = "PASS") -> dict[str, object]:
        return {
            "schema_version": 1,
            "receipt_id": f"receipt-f107-{result.lower()}",
            "packet_id": packet["packet_id"],
            "plan_id": packet["plan_id"],
            "plan_version": packet["plan_version"],
            "task_id": packet["task_id"],
            "phase_id": packet["phase_id"],
            "work_item_id": packet["work_item_id"],
            "skill_ref": copy.deepcopy(packet["skill_ref"]),
            "result": result,
            "summary": "F1-07 execution fixture",
            "changed_paths": [],
            "created_assets": [],
            "deleted_assets": [],
            "test_results": {"fixture": "PASS"},
            "evidence_refs": ["evidence/final.txt"],
            "warnings": [],
            "blocking_findings": ["fixture failure"] if result in {"FAILED", "BLOCKED"} else [],
            "rollback_status": "NOT_REQUIRED",
            "started_at": "2026-07-17T10:00:00+08:00",
            "completed_at": "2026-07-17T10:01:00+08:00",
            "producer": "f1-07-fixture",
            "producer_version": "0.9.0",
        }

    def set_outcome_policies(
        self,
        instance: Path,
        *,
        evolution_policy: dict[str, object] | None = None,
        knowledge_policy: dict[str, object] | None = None,
        content_policy: dict[str, object] | None = None,
    ) -> None:
        for name in ("task-envelope.json", "plan-package.json"):
            path = instance / name
            value = json.loads(path.read_text(encoding="utf-8"))
            if evolution_policy is not None:
                value["evolution_policy"] = copy.deepcopy(evolution_policy)
            if knowledge_policy is not None:
                value["knowledge_policy"] = copy.deepcopy(knowledge_policy)
            if content_policy is not None:
                value["content_policy"] = copy.deepcopy(content_policy)
            self.write_json(path, value)

    def add_checkpoint(self, state: Path, instance: Path, *, action: str = "ADVANCE_PHASE") -> dict[str, object]:
        receipt = instance / "checkpoint-receipt.json"
        receipt.write_text("checkpoint receipt\n", encoding="utf-8")
        evidence = instance / "evidence" / "checkpoint.txt"
        evidence.write_text("checkpoint evidence\n", encoding="utf-8")
        plan = json.loads((instance / "plan-package.json").read_text(encoding="utf-8"))
        canonical_state_root = state / "checkpoint-state"
        canonical_state_root.mkdir(parents=True, exist_ok=True)
        lineage_digest = "a" * 64
        root_binding = canonical_state_root / "root-binding.json"
        root_binding.write_text(
            contracts.stable_json(
                {
                    "schema_version": "1.0",
                    "task_id": plan["task_id"],
                    "plan_id": plan["plan_id"],
                    "phase_id": "P01",
                    "lineage_digest": lineage_digest,
                }
            ),
            encoding="utf-8",
        )
        head = canonical_state_root / "head.json"
        head.write_text(
            contracts.stable_json(
                {
                    "checkpoint_status": "PASSED" if action == "ADVANCE_PHASE" else "BLOCKED",
                    "effective_action": action,
                }
            ),
            encoding="utf-8",
        )
        audit = canonical_state_root / "audit.md"
        audit.write_text("checkpoint audit\n", encoding="utf-8")
        ref = {
            "checkpoint_id": "cp-f107-final",
            "checkpoint_status": "PASSED",
            "task_id": plan["task_id"],
            "plan_id": plan["plan_id"],
            "plan_version": plan["plan_version"],
            "phase_id": "P01",
            "effective_action": action,
            "decision": action,
            "publication_status": "PUBLISHED_COMMIT" if action == "ADVANCE_PHASE" else "NOT_PUBLISHED",
            "verification_status": "PASSED" if action == "ADVANCE_PHASE" else "FAILED",
            "checkpoint_consumer_status": "VERIFIED",
            "evidence_refs": [
                {"path": "evidence/checkpoint.txt", "sha256": workflow.file_digest(evidence)}
            ],
            "receipt_location": "checkpoint-receipt.json",
            "receipt_sha256": workflow.file_digest(receipt),
            "audit_path": str(audit),
            "audit_sha256": workflow.file_digest(audit),
            "canonical_state_root": str(canonical_state_root),
            "lineage_digest": lineage_digest,
            "root_binding_location": str(root_binding),
            "root_binding_sha256": workflow.file_digest(root_binding),
            "head_location": str(head),
            "scoped_baseline": {"sha256": "b" * 64, "paths": ["evidence/checkpoint.txt"]},
            "resume_entry": "P01/P01",
            "producer": "f1-07-checkpoint-fixture",
            "producer_version": "0.0.1",
            "created_at": "2026-07-17T10:02:00+08:00",
        }
        ref_path = self.write_json(state / "checkpoint-ref.json", ref)
        result = planning.record_checkpoint_ref(instance, ref_path, apply=True, agent="test-f1-07")
        self.assertEqual(result["result"], "RECORDED_CHECKPOINT_REF", result)
        return ref

    def add_advanced_evidence(
        self,
        state: Path,
        instance: Path,
        *,
        cleanliness_result: str = "PASS_WITH_WARNINGS",
        add_checkpoint: bool = True,
    ) -> None:
        plan_path = instance / "plan-package.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["governance_policy"]["required_stages"] = ["PRE_CLOSE"]
        plan_path.write_text(contracts.stable_json(plan), encoding="utf-8")

        packet_result = planning.create_packet(instance, "P01", "P01", apply=True, agent="test-f1-07")
        self.assertEqual(packet_result["result"], "CREATED", packet_result)
        packet = json.loads(
            (instance / "packets" / f"{packet_result['packet_id']}.json").read_text(encoding="utf-8")
        )
        execution_path = self.write_json(state / "execution-receipt.json", self.make_execution_receipt(packet))
        recorded = planning.record_receipt(instance, execution_path, apply=True, agent="test-f1-07")
        self.assertEqual(recorded["result"], "RECORDED", recorded)

        request_result = planning.create_governance_request(
            instance, "PRE_CLOSE", "P01", apply=True, agent="test-f1-07"
        )
        self.assertEqual(request_result["result"], "CREATED_GOVERNANCE_REQUEST", request_result)
        request = request_result["request"]
        receipt = {
            "receipt_id": f"cleanliness-f107-{cleanliness_result.lower()}",
            "request_id": request["request_id"],
            "task_id": request["task_id"],
            "plan_id": request["plan_id"],
            "phase_id": request["phase_id"],
            "governance_stage": request["governance_stage"],
            "result": cleanliness_result,
            "cleanliness_status": cleanliness_result,
            "scope_match": True,
            "blocking_findings": ["fixture protected asset changed"] if cleanliness_result == "BLOCKED" else [],
            "non_blocking_findings": ["fixture warning"] if cleanliness_result == "PASS_WITH_WARNINGS" else [],
            "duplicate_candidates": ["KEEP"],
            "unused_asset_candidates": ["KEEP"],
            "cleanup_actions": [],
            "protected_assets_status": {"status": "PRESERVED"},
            "evidence_refs": ["evidence/final.txt"],
            "checked_at": "2026-07-17T10:03:00+08:00",
            "producer": "f1-07-governance-fixture",
            "producer_version": "0.0.1",
        }
        receipt_path = self.write_json(state / "cleanliness-receipt.json", receipt)
        recorded = planning.record_cleanliness_receipt(instance, receipt_path, apply=True, agent="test-f1-07")
        self.assertEqual(recorded["result"], "RECORDED_CLEANLINESS_RECEIPT", recorded)
        if add_checkpoint:
            self.add_checkpoint(state, instance)

    def test_simple_ready_preview_apply_and_idempotence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state, instance = self.create_instance(Path(directory), "task-f107-simple")
            preview = planning.finalize_plan(instance, preview=True)
            self.assertEqual(preview["result"], "CLOSE_READY")
            applied = planning.finalize_plan(instance, apply=True)
            self.assertEqual(applied["result"], "CLOSED")
            repeated = planning.finalize_plan(instance, apply=True)
            self.assertEqual(repeated["result"], "ALREADY_CLOSED")
            metadata = workflow.extract_machine_json(
                (instance / workflow.CHECKLIST_NAME).read_text(encoding="utf-8"), "workflow"
            )
            self.assertEqual(metadata["final_status"], "CLOSED")
            self.assertEqual(metadata["checklist_version"], "1.0.1")
            self.assertEqual(list((state / ".planning").glob("**/*")), [])

    def test_simple_does_not_force_advanced_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _state, instance = self.create_instance(Path(directory), "task-f107-simple-light")
            result = planning.finalize_plan(instance, preview=True)
            self.assertEqual(result["completion_gate"], "CLOSE_READY")
            self.assertEqual(result["final_receipt_refs"], [])

    def test_legacy_v080_instance_reads_with_simple_compatibility_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            envelope = copy.deepcopy(BASE_ENVELOPE)
            envelope.update({"task_id": "task-f107-legacy-v080", "human_gates": []})
            envelope.pop("finalization_policy", None)
            source = self.write_json(root / "legacy.json", envelope)
            created = planning.create_plan(source, state_root=root / "state-root", apply=True, agent="test-v080")
            self.assertEqual(created["result"], "CREATED", created)
            instance = root / "state-root" / "task-f107-legacy-v080"
            (instance / "evidence").mkdir()
            (instance / "evidence" / "final.txt").write_text("legacy evidence\n", encoding="utf-8")
            self.satisfy_plan_and_checklist(instance)
            plan = json.loads((instance / "plan-package.json").read_text(encoding="utf-8"))
            self.assertNotIn("finalization_policy", plan)
            result = planning.finalize_plan(instance, preview=True)
            self.assertEqual(result["mode"], "SIMPLE")
            self.assertEqual(result["result"], "CLOSE_READY", result)
            metadata = workflow.extract_machine_json(
                (instance / workflow.CHECKLIST_NAME).read_text(encoding="utf-8"), "workflow"
            )
            self.assertNotIn("final_status", metadata)

    def test_advanced_complete_accepts_pass_with_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, instance = self.create_instance(
                root,
                "task-f107-advanced",
                mode="ADVANCED",
                policy={"required_governance_stages": ["PRE_CLOSE"], "require_outcome_routing": False},
            )
            self.add_advanced_evidence(state, instance)
            result = planning.finalize_plan(instance, preview=True)
            self.assertEqual(result["result"], "CLOSE_READY", result)
            self.assertTrue(any("governance/receipts" in ref for ref in result["final_receipt_refs"]))
            self.assertTrue(result["trusted_checkpoint"])
            self.assertTrue(result["warnings"])

    def test_advanced_cleanliness_blocked_blocks_close(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, instance = self.create_instance(
                root,
                "task-f107-cleanliness-blocked",
                mode="ADVANCED",
                policy={
                    "required_governance_stages": ["PRE_CLOSE"],
                    "require_checkpoint_ref": False,
                    "require_outcome_routing": False,
                },
            )
            self.add_advanced_evidence(state, instance, cleanliness_result="BLOCKED", add_checkpoint=False)
            result = planning.finalize_plan(instance, preview=True)
            self.assertEqual(result["result"], "CLOSE_BLOCKED", result)
            self.assertIn("governance:PRE_CLOSE:BLOCKED", result["blocking_findings"])

    def test_advanced_resume_then_no_evolution_and_content_not_required_can_close(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, instance = self.create_instance(
                root,
                "task-f107-no-outcome-value",
                mode="ADVANCED",
                policy={"required_governance_stages": ["PRE_CLOSE"], "require_outcome_routing": True},
            )
            self.add_advanced_evidence(state, instance)
            resumed = planning.resume_from_checkpoint(instance, "cp-f107-final", apply=True, agent="test-f1-07")
            self.assertEqual(resumed["result"], "RESUMED", resumed)
            route = planning.evaluate_outcome_routing(instance, apply=True, agent="test-f1-07")
            self.assertEqual(route["decision"], "NO_VALUE", route)
            metadata = workflow.extract_machine_json(
                (instance / workflow.CHECKLIST_NAME).read_text(encoding="utf-8"), "workflow"
            )
            self.assertEqual(metadata["evolution_status"], "NO_EVOLUTION")
            self.assertEqual(metadata["content_status"], "NOT_REQUIRED")
            result = planning.finalize_plan(instance, preview=True)
            self.assertEqual(result["result"], "CLOSE_READY", result)

    def test_advanced_missing_evolution_receipt_blocks_close(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, instance = self.create_instance(
                root,
                "task-f107-missing-evolution-receipt",
                mode="ADVANCED",
                policy={"required_governance_stages": ["PRE_CLOSE"], "require_outcome_routing": True},
            )
            self.set_outcome_policies(
                instance,
                evolution_policy={"reusable_rule_candidates": ["stable-rule"], "cross_task_value": True},
            )
            self.add_advanced_evidence(state, instance)
            route = planning.evaluate_outcome_routing(instance, apply=True, agent="test-f1-07")
            self.assertEqual(route["decision"], "EVOLUTION_ONLY", route)
            result = planning.finalize_plan(instance, preview=True)
            self.assertEqual(result["result"], "CLOSE_BLOCKED", result)
            self.assertIn("outcomes:evolution:MISSING_RECEIPT", result["blocking_findings"])

    def test_advanced_missing_content_ingest_receipt_blocks_close(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, instance = self.create_instance(
                root,
                "task-f107-missing-content-receipt",
                mode="ADVANCED",
                policy={"required_governance_stages": ["PRE_CLOSE"], "require_outcome_routing": True},
            )
            self.set_outcome_policies(
                instance,
                knowledge_policy={
                    "level": "BRIEF",
                    "potential_value": "reusable content",
                    "required_evidence": [],
                    "required_images": [],
                    "prohibited_content": [],
                    "redaction_requirements": [],
                    "ingest_required": True,
                },
                content_policy={
                    "content_title": "F1-07 content fixture",
                    "content_summary": "content receipt is intentionally absent",
                    "core_value": "prove the close gate",
                },
            )
            self.add_advanced_evidence(state, instance)
            route = planning.evaluate_outcome_routing(instance, apply=True, agent="test-f1-07")
            self.assertEqual(route["decision"], "CONTENT_ONLY", route)
            result = planning.finalize_plan(instance, preview=True)
            self.assertEqual(result["result"], "CLOSE_BLOCKED", result)
            self.assertIn("outcomes:content:MISSING_RECEIPT", result["blocking_findings"])

    def test_advanced_missing_execution_receipt_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, instance = self.create_instance(
                root,
                "task-f107-missing-receipt",
                mode="ADVANCED",
                policy={"required_governance_stages": [], "require_cleanliness_receipts": False, "require_checkpoint_ref": False, "require_outcome_routing": False},
            )
            packet_result = planning.create_packet(instance, "P01", "P01", apply=True)
            self.assertEqual(packet_result["result"], "CREATED")
            result = planning.finalize_plan(instance, preview=True)
            self.assertEqual(result["result"], "CLOSE_BLOCKED")
            self.assertIn("MISSING_RECEIPT", " ".join(result["blocking_findings"]))

    def test_failed_execution_receipt_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, instance = self.create_instance(
                root,
                "task-f107-failed-receipt",
                mode="ADVANCED",
                policy={"required_governance_stages": [], "require_cleanliness_receipts": False, "require_checkpoint_ref": False, "require_outcome_routing": False},
            )
            packet_result = planning.create_packet(instance, "P01", "P01", apply=True)
            packet = json.loads(
                (instance / "packets" / f"{packet_result['packet_id']}.json").read_text(encoding="utf-8")
            )
            receipt = self.make_execution_receipt(packet, result="FAILED")
            path = self.write_json(state / "failed-receipt.json", receipt)
            self.assertEqual(planning.record_receipt(instance, path, apply=True)["result"], "RECORDED")
            result = planning.finalize_plan(instance, preview=True)
            self.assertEqual(result["result"], "CLOSE_BLOCKED")
            self.assertIn("FAILED", " ".join(result["blocking_findings"]))

    def test_checkpoint_projection_drift_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, instance = self.create_instance(
                root,
                "task-f107-checkpoint-drift",
                mode="ADVANCED",
                policy={"required_governance_stages": [], "require_execution_receipts": False, "require_cleanliness_receipts": False, "require_outcome_routing": False},
            )
            self.add_checkpoint(state, instance)
            ref_path = instance / "checkpoints" / "refs" / "cp-f107-final.json"
            ref = json.loads(ref_path.read_text(encoding="utf-8"))
            evidence = instance / "evidence" / "checkpoint.txt"
            evidence.write_text("drifted\n", encoding="utf-8")
            ref_path.write_text(contracts.stable_json(ref), encoding="utf-8")
            result = planning.finalize_plan(instance, preview=True)
            self.assertEqual(result["result"], "CLOSE_BLOCKED")
            self.assertIn("CHECKPOINT_PROJECTION_DRIFT", " ".join(result["blocking_findings"]))

    def test_orphan_historical_checkpoint_is_non_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, instance = self.create_instance(
                root,
                "task-f107-orphan-checkpoint",
                mode="ADVANCED",
                policy={
                    "required_governance_stages": [],
                    "require_execution_receipts": False,
                    "require_cleanliness_receipts": False,
                    "require_checkpoint_ref": True,
                    "require_outcome_routing": False,
                },
            )
            current = self.add_checkpoint(state, instance)
            orphan = copy.deepcopy(current)
            orphan["checkpoint_id"] = "cp-f107-orphan"
            orphan["previous_checkpoint_id"] = None
            self.write_json(instance / "checkpoints" / "refs" / "cp-f107-orphan.json", orphan)

            calls: list[tuple[str, bool]] = []

            def validate_checkpoint(ref: dict[str, object], *args: object, **kwargs: object) -> dict[str, object]:
                calls.append((str(ref["checkpoint_id"]), bool(kwargs.get("historical"))))
                return {
                    "projection": {"effective_action": "ADVANCE_PHASE"},
                    "evidence_map": {},
                    "receipt": {"path": "checkpoint-receipt.json"},
                }

            envelope = json.loads((instance / "task-envelope.json").read_text(encoding="utf-8"))
            plan = json.loads((instance / "plan-package.json").read_text(encoding="utf-8"))
            checklist = (instance / workflow.CHECKLIST_NAME).read_text(encoding="utf-8")
            with mock.patch.object(planning, "_validate_checkpoint_reference", side_effect=validate_checkpoint), mock.patch.object(
                planning,
                "_midcourse_gate_runtime_state",
                return_value={"latest_checkpoint": current["checkpoint_id"]},
            ):
                blocking, waiting, warnings, _evidence, trusted = planning._final_checkpoint_gate(
                    state,
                    instance,
                    envelope,
                    plan,
                    checklist,
                    {"require_checkpoint_ref": True},
                    "ADVANCED",
                )

            self.assertEqual(blocking, [])
            self.assertEqual(waiting, [])
            self.assertEqual(trusted, current["checkpoint_id"])
            self.assertEqual(calls, [(str(current["checkpoint_id"]), False)])
            self.assertIn("checkpoint:cp-f107-orphan:HISTORICAL_NON_AUTHORITATIVE", warnings)

    def test_human_gate_returns_waiting_without_closing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            envelope = copy.deepcopy(BASE_ENVELOPE)
            envelope.update(
                {
                    "task_id": "task-f107-human",
                    "knowledge_policy": {"level": "NONE"},
                    "finalization_policy": {"mode": "SIMPLE"},
                }
            )
            source = self.write_json(root / "human.json", envelope)
            created = planning.create_plan(source, state_root=root / "state-root", apply=True)
            self.assertEqual(created["result"], "CREATED")
            instance = root / "state-root" / "task-f107-human"
            self.satisfy_plan_and_checklist(instance)
            result = planning.finalize_plan(instance, preview=True)
            self.assertEqual(result["result"], "CLOSE_WAITING_HUMAN")
            self.assertEqual(result["required_next_action"], "OWNER_REVIEW")

    def test_outcome_human_review_required_keeps_advanced_plan_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, instance = self.create_instance(
                root,
                "task-f107-outcome-human",
                mode="ADVANCED",
                policy={
                    "require_execution_receipts": False,
                    "require_cleanliness_receipts": False,
                    "require_checkpoint_ref": False,
                },
                knowledge_policy={
                    "level": "FULL",
                    "potential_value": "sensitive result",
                    "required_evidence": [],
                    "required_images": [],
                    "prohibited_content": [],
                    "redaction_requirements": [],
                    "ingest_required": True,
                    "sensitive_content": True,
                },
            )
            route = planning.evaluate_outcome_routing(instance, apply=True, agent="test-f1-07")
            self.assertEqual(route["decision"], "HUMAN_REVIEW_REQUIRED", route)
            result = planning.finalize_plan(instance, preview=True)
            self.assertEqual(result["result"], "CLOSE_WAITING_HUMAN")

    def test_preview_is_zero_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root, "task-f107-preview")
            before = tree_snapshot(instance)
            result = planning.finalize_plan(instance, preview=True)
            self.assertEqual(result["result"], "CLOSE_READY")
            self.assertEqual(before, tree_snapshot(instance))

    def test_apply_failure_rolls_back_checklist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root, "task-f107-rollback")
            before = tree_snapshot(instance)
            with mock.patch.object(planning.workflow, "atomic_write_text", side_effect=OSError("fixture failure")):
                result = planning.finalize_plan(instance, apply=True)
            self.assertEqual(result["result"], "FAILED")
            self.assertEqual(before, tree_snapshot(instance))

    def test_unknown_fields_in_plan_and_checklist_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root, "task-f107-unknown")
            plan_path = instance / "plan-package.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["future_finalization"] = {"keep": True}
            plan_path.write_text(contracts.stable_json(plan), encoding="utf-8")
            checklist_path = instance / workflow.CHECKLIST_NAME
            text = checklist_path.read_text(encoding="utf-8")
            metadata = workflow.extract_machine_json(text, "workflow")
            metadata["future_finalization"] = {"keep": "nested"}
            checklist_path.write_text(workflow.replace_machine_json(text, "workflow", metadata), encoding="utf-8")
            result = planning.finalize_plan(instance, apply=True)
            self.assertEqual(result["result"], "CLOSED")
            self.assertEqual(json.loads(plan_path.read_text(encoding="utf-8"))["future_finalization"], {"keep": True})
            final_metadata = workflow.extract_machine_json(checklist_path.read_text(encoding="utf-8"), "workflow")
            self.assertEqual(final_metadata["future_finalization"], {"keep": "nested"})

    def test_verify_summary_cli_is_compact_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root, "task-f107-summary")
            before = tree_snapshot(instance)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "planning.py"),
                    "verify-plan",
                    "--instance-root",
                    str(instance),
                    "--summary",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            output = json.loads(completed.stdout)
            self.assertEqual(output["result"], "SUMMARY")
            self.assertEqual(output["mode"], "SIMPLE")
            self.assertEqual(before, tree_snapshot(instance))

    def test_invalid_finalization_mode_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root, "task-f107-invalid", mode="SIMPLE")
            plan_path = instance / "plan-package.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["finalization_policy"] = {"mode": "UNKNOWN"}
            plan_path.write_text(contracts.stable_json(plan), encoding="utf-8")
            result = planning.finalize_plan(instance, preview=True)
            self.assertEqual(result["error_code"], "INVALID_FINALIZATION_MODE")


if __name__ == "__main__":
    unittest.main()
