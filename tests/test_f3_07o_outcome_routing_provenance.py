#!/usr/bin/env python3
"""F3-07O checkpoint-scoped outcome-routing contract tests."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "scripts"))

import plan_contracts as contracts  # noqa: E402
import planning  # noqa: E402
import workflow_contracts as workflow  # noqa: E402


FIXTURES = json.loads(
    (ROOT / "tests" / "fixtures" / "f1-01" / "valid_contracts.json").read_text(encoding="utf-8")
)


class OutcomeRoutingProvenanceTests(unittest.TestCase):
    def create_instance(self, root: Path, task_id: str) -> Path:
        envelope = copy.deepcopy(FIXTURES["task_envelope"])
        envelope.update(
            {
                "task_id": task_id,
                "evolution_policy": {},
                "content_policy": {},
                "knowledge_policy": {
                    "level": "NONE",
                    "required_evidence": [],
                    "required_images": [],
                    "prohibited_content": [],
                    "redaction_requirements": [],
                    "ingest_required": False,
                },
            }
        )
        source = root / f"{task_id}.json"
        source.write_text(contracts.stable_json(envelope), encoding="utf-8")
        result = planning.create_plan(source, state_root=root / "state", apply=True)
        self.assertEqual(result["result"], "CREATED", result)
        return root / "state" / task_id

    def install_checkpoint(
        self,
        instance: Path,
        checkpoint_id: str,
        *,
        created_at: str,
        lineage: str = "a" * 64,
        effective_action: str = "ADVANCE_PHASE",
    ) -> Path:
        state_root = instance.parent
        canonical = state_root / "checkpoint-state"
        canonical.mkdir(parents=True, exist_ok=True)
        receipt = canonical / f"{checkpoint_id}-receipt.json"
        head = canonical / f"{checkpoint_id}-head.json"
        audit = canonical / f"{checkpoint_id}-audit.md"
        root_binding = canonical / f"{checkpoint_id}-root-binding.json"
        receipt.write_text("{}\n", encoding="utf-8")
        head.write_text("{}\n", encoding="utf-8")
        audit.write_text(f"audit {checkpoint_id}\n", encoding="utf-8")
        plan = json.loads((instance / "plan-package.json").read_text(encoding="utf-8"))
        binding = {
            "schema_version": "1.0",
            "task_id": plan["task_id"],
            "plan_id": plan["plan_id"],
            "phase_id": "P01",
            "lineage_digest": lineage,
        }
        root_binding.write_text(contracts.stable_json(binding), encoding="utf-8")
        ref = {
            "schema_version": 1,
            "task_id": plan["task_id"],
            "checkpoint_id": checkpoint_id,
            "checkpoint_status": "PASSED" if effective_action == "ADVANCE_PHASE" else "BLOCKED",
            "plan_id": plan["plan_id"],
            "plan_version": plan["plan_version"],
            "phase_id": "P01",
            "evidence_refs": [],
            "resume_entry": "P01/P01",
            "receipt_location": str(receipt),
            "receipt_sha256": workflow.file_digest(receipt),
            "producer": "orchestrator.checkpoint-adapter",
            "producer_version": "0.1.2",
            "created_at": created_at,
            "effective_action": effective_action,
            "decision": effective_action,
            "publication_status": "PUBLISHED_COMMIT" if effective_action == "ADVANCE_PHASE" else "NOT_PUBLISHED",
            "verification_status": "PASSED" if effective_action == "ADVANCE_PHASE" else "FAILED",
            "checkpoint_consumer_status": "VERIFIED",
            "canonical_state_root": str(canonical),
            "lineage_digest": lineage,
            "root_binding_location": str(root_binding),
            "root_binding_sha256": workflow.file_digest(root_binding),
            "audit_path": str(audit),
            "audit_sha256": workflow.file_digest(audit),
            "head_location": str(head),
            "scoped_baseline": {"sha256": "b" * 64, "paths": ["fixture.json"]},
            "checkpoint_attempt": 1,
        }
        ref_path = instance / "checkpoints" / "refs" / f"{planning._safe_component(checkpoint_id, 'checkpoint_id')}.json"
        ref_path.parent.mkdir(parents=True, exist_ok=True)
        ref_path.write_text(contracts.stable_json(ref), encoding="utf-8")

        checklist_path = instance / "WORKFLOW_CHECKLIST.md"
        checklist = checklist_path.read_text(encoding="utf-8")
        metadata = workflow.extract_machine_json(checklist, "workflow")
        metadata.update(
            {
                "checkpoint_refs": [str(ref_path.relative_to(instance))],
                "last_trusted_checkpoint": checkpoint_id,
                "checkpoint_consumer_status": "VERIFIED",
                "checkpoint_action": effective_action,
                "resume_status": "RESUMED",
                "task_status": "READY",
                "pause_status": "RESUMED",
                "current_phase": "P01",
                "overall_status": "进行中",
                "human_execution_gate": "OPEN_FOR_RESUME_ONLY",
            }
        )
        checklist_path.write_text(workflow.replace_machine_json(checklist, "workflow", metadata), encoding="utf-8")
        return ref_path

    def route(self, instance: Path, *, apply: bool = True) -> dict[str, object]:
        return planning.evaluate_outcome_routing(instance, apply=apply, preview=not apply)

    def test_legacy_decision_is_preserved_and_new_checkpoint_gets_own_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            instance = self.create_instance(Path(directory), "task-f307o-legacy")
            legacy = self.route(instance)
            self.assertEqual(legacy["result"], "CREATED_OUTCOME_ROUTING")
            legacy_path = instance / "outcomes" / "routing-decision.json"
            legacy_value = json.loads(legacy_path.read_text(encoding="utf-8"))
            legacy_value["checkpoint_id"] = "CP-old"
            legacy_path.write_text(contracts.stable_json(legacy_value), encoding="utf-8")
            legacy_digest = workflow.file_digest(legacy_path)
            self.install_checkpoint(instance, "CP-new", created_at="2026-07-21T00:00:00Z")

            preview = self.route(instance, apply=False)
            self.assertEqual(preview["result"], "PREVIEW")
            self.assertEqual(preview["routing_decision"]["checkpoint_id"], "CP-new")
            self.assertIn("outcomes/routing-decisions/by-checkpoint/cp-new.json", preview["decision_path"])
            applied = self.route(instance)
            self.assertEqual(applied["result"], "RECORDED_OUTCOME_ROUTING")
            self.assertEqual(workflow.file_digest(legacy_path), legacy_digest)
            self.assertTrue((instance / "outcomes/routing-decisions/by-checkpoint/cp-new.json").is_file())

    def test_same_checkpoint_is_idempotent_and_changed_content_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            instance = self.create_instance(Path(directory), "task-f307o-idempotent")
            self.install_checkpoint(instance, "CP-same", created_at="2026-07-21T00:00:00Z")
            first = self.route(instance)
            self.assertEqual(first["result"], "RECORDED_OUTCOME_ROUTING")
            second = self.route(instance)
            self.assertEqual(second["result"], "EXISTING_OUTCOME_ROUTING")
            path = instance / "outcomes/routing-decisions/by-checkpoint/cp-same.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["decision"] = "CONTENT_ONLY"
            path.write_text(contracts.stable_json(value), encoding="utf-8")
            conflict = self.route(instance)
            self.assertEqual(conflict["result"], "CONFLICT")
            self.assertEqual(conflict["error_code"], "OUTCOME_ROUTING_CONFLICT")

    def test_stale_checkpoint_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            instance = self.create_instance(Path(directory), "task-f307o-stale")
            self.install_checkpoint(instance, "CP-old", created_at="2026-07-21T00:00:00Z")
            self.install_checkpoint(instance, "CP-new", created_at="2026-07-21T00:01:00Z")
            checklist_path = instance / "WORKFLOW_CHECKLIST.md"
            checklist = checklist_path.read_text(encoding="utf-8")
            metadata = workflow.extract_machine_json(checklist, "workflow")
            metadata["last_trusted_checkpoint"] = "CP-old"
            checklist_path.write_text(workflow.replace_machine_json(checklist, "workflow", metadata), encoding="utf-8")
            result = self.route(instance)
            self.assertEqual(result["error_code"], "STALE_CHECKPOINT")

    def test_root_lineage_and_read_head_fail_closed(self) -> None:
        cases = ("root", "lineage", "head")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                instance = self.create_instance(Path(directory), f"task-f307o-{case}")
                ref_path = self.install_checkpoint(instance, f"CP-{case}", created_at="2026-07-21T00:00:00Z")
                ref = json.loads(ref_path.read_text(encoding="utf-8"))
                if case == "root":
                    ref["canonical_state_root"] = str(Path(directory) / "outside")
                elif case == "lineage":
                    ref["lineage_digest"] = "c" * 64
                else:
                    ref["verification_status"] = "HOLD"
                ref_path.write_text(contracts.stable_json(ref), encoding="utf-8")
                result = self.route(instance)
                self.assertIn(result["error_code"], {"OUTCOME_ROUTING_NOT_ELIGIBLE", "CHECKPOINT_PROJECTION_DRIFT"})

    def test_checkpoint_route_path_is_sanitized(self) -> None:
        with self.assertRaises(planning.PlanningError):
            planning._routing_decision_relative("../escape")


if __name__ == "__main__":
    unittest.main()
