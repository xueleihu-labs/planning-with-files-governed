#!/usr/bin/env python3
"""Focused compatibility and runtime tests for the D-001 midcourse gate fields."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import plan_contracts as contracts  # noqa: E402
import planning  # noqa: E402
import workflow_contracts as workflow  # noqa: E402


FIXTURES = json.loads(
    (ROOT / "tests" / "fixtures" / "f1-01" / "valid_contracts.json").read_text(encoding="utf-8")
)
LEGACY_ENVELOPE = FIXTURES["task_envelope"]


def criterion(condition_id: str, description: str) -> dict[str, object]:
    value = copy.deepcopy(FIXTURES["condition"])
    value.update(
        {
            "condition_id": condition_id,
            "condition_type": "COMPLETION",
            "description": description,
            "status": "PENDING",
            "evidence_refs": [],
        }
    )
    return value


def midcourse_fields(
    *,
    result: str = "NOT_REACHED",
    review_ref: str | None = None,
    owner_ref: str | None = None,
    gate_phase: str = "P02",
) -> dict[str, object]:
    return {
        "midcourse_gate_phase": gate_phase,
        "midcourse_gate_entry_criteria": [criterion("midcourse-entry", "进入中期门前范围保持稳定")],
        "midcourse_gate_exit_criteria": [criterion("midcourse-exit", "中期复核和老板确认完成")],
        "midcourse_grill_policy": {
            "mode": "INCREMENTAL",
            "one_question_at_a_time": True,
            "requires_recommendation": True,
        },
        "midcourse_review_ref": review_ref,
        "midcourse_owner_confirmation_ref": owner_ref,
        "midcourse_gate_result": result,
        "owner_acceptance_checklist_ref": "acceptance/owner-checklist.md",
    }


class MidcourseGateContractTests(unittest.TestCase):
    def envelope(self, **updates: object) -> dict[str, object]:
        value = copy.deepcopy(LEGACY_ENVELOPE)
        value.update(updates)
        value["task_id"] = updates.get("task_id", "task-midcourse")
        return value

    def test_legacy_task_envelope_remains_readable_and_plan_has_no_new_fields(self) -> None:
        value = self.envelope()
        contracts.validate_task_envelope(value)
        package = planning.build_plan_package(value)
        contracts.validate_plan_package(package)
        self.assertFalse(set(contracts.MIDCOURSE_GATE_FIELDS).intersection(value))
        self.assertFalse(set(contracts.MIDCOURSE_GATE_FIELDS).intersection(package))

    def test_complete_new_format_validates_and_maps_through_single_fact_chain(self) -> None:
        fields = midcourse_fields()
        value = self.envelope(**fields)
        contracts.validate_task_envelope(value)
        package = planning.build_plan_package(value)
        contracts.validate_plan_package(package)
        self.assertEqual(
            {field: package[field] for field in contracts.MIDCOURSE_GATE_FIELDS},
            fields,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "task.json"
            source.write_bytes((contracts.stable_json(value)).encode("utf-8"))
            state = root / "state"
            self.assertEqual(planning.create_plan(source, state_root=state, apply=True)["result"], "CREATED")
            instance = state / value["task_id"]
            stored_envelope = json.loads((instance / "task-envelope.json").read_text(encoding="utf-8"))
            stored_plan = json.loads((instance / "plan-package.json").read_text(encoding="utf-8"))
            checklist = (instance / "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8")
            packet = planning.build_execution_packet(stored_plan, checklist, "P01", "P01")
            contracts.validate_new_task_bundle(
                {
                    "plan_contracts": {
                        "schema_version": 1,
                        "task_envelope": stored_envelope,
                        "plan_package": stored_plan,
                        "execution_packets": [packet],
                        "execution_receipts": [],
                    }
                }
            )

            drifted_plan = copy.deepcopy(stored_plan)
            drifted_plan["midcourse_review_ref"] = "evidence/drifted-review.md"
            with self.assertRaises(workflow.ContractError):
                contracts.validate_new_task_bundle(
                    {
                        "plan_contracts": {
                            "schema_version": 1,
                            "task_envelope": stored_envelope,
                            "plan_package": drifted_plan,
                            "execution_packets": [packet],
                            "execution_receipts": [],
                        }
                    }
                )

            illegal_pass = copy.deepcopy(stored_envelope)
            illegal_pass["midcourse_gate_result"] = "PASS"
            illegal_pass["midcourse_review_ref"] = None
            illegal_pass["midcourse_owner_confirmation_ref"] = None
            with self.assertRaises(workflow.ContractError):
                contracts.validate_task_envelope(illegal_pass)

    def test_initial_states_are_explicit_and_do_not_claim_gate_evidence(self) -> None:
        for result in ("PENDING", "NOT_REACHED"):
            with self.subTest(result=result):
                value = self.envelope(**midcourse_fields(result=result))
                contracts.validate_task_envelope(value)

    def test_partial_fields_invalid_state_and_missing_pass_refs_are_rejected(self) -> None:
        partial = self.envelope(midcourse_gate_phase="P02")
        with self.assertRaises(workflow.ContractError):
            contracts.validate_task_envelope(partial)

        invalid_state = self.envelope(**midcourse_fields(result="UNKNOWN"))
        with self.assertRaises(workflow.ContractError):
            contracts.validate_task_envelope(invalid_state)

        false_initial_evidence = self.envelope(
            **midcourse_fields(result="NOT_REACHED", review_ref="evidence/review.md")
        )
        with self.assertRaises(workflow.ContractError):
            contracts.validate_task_envelope(false_initial_evidence)

        incomplete_pass = self.envelope(**midcourse_fields(result="PASS"))
        with self.assertRaises(workflow.ContractError):
            contracts.validate_task_envelope(incomplete_pass)

        missing_acceptance_checklist = self.envelope(**midcourse_fields())
        missing_acceptance_checklist["owner_acceptance_checklist_ref"] = None
        with self.assertRaises(workflow.ContractError):
            contracts.validate_task_envelope(missing_acceptance_checklist)

    def test_plan_gate_phase_must_reference_declared_phase(self) -> None:
        value = self.envelope(**midcourse_fields(gate_phase="P99"))
        contracts.validate_task_envelope(value)
        with self.assertRaises(planning.PlanningError):
            contracts.validate_plan_package(planning.build_plan_package(value))

    def test_checkpoint_resume_preserves_midcourse_fields_and_evidence_refs(self) -> None:
        value = self.envelope(
            task_id="task-midcourse-resume",
            **midcourse_fields(
                result="PASS",
                review_ref="evidence/midcourse-review.md",
                owner_ref="evidence/owner-confirmation.md",
            ),
        )
        value["human_gates"][0]["status"] = "SATISFIED"
        value["human_gates"][0]["evidence_refs"] = ["evidence/owner-confirmation.md"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "task.json"
            source.write_bytes((contracts.stable_json(value)).encode("utf-8"))
            state = root / "state"
            self.assertEqual(planning.create_plan(source, state_root=state, apply=True)["result"], "CREATED")
            instance = state / value["task_id"]

            evidence_contents = {
                "evidence/midcourse-review.md": "midcourse review\n",
                "evidence/owner-confirmation.md": "owner confirmation\n",
                "acceptance/owner-checklist.md": "owner acceptance checklist\n",
            }
            for relative, content in evidence_contents.items():
                path = instance / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((content).encode("utf-8"))
            receipt_path = instance / "receipts" / "checkpoint-receipt.json"
            receipt_path.write_bytes(("checkpoint receipt\n").encode("utf-8"))

            plan_before = json.loads((instance / "plan-package.json").read_text(encoding="utf-8"))
            fields_before = {
                field: copy.deepcopy(plan_before[field]) for field in contracts.MIDCOURSE_GATE_FIELDS
            }
            evidence_refs = [
                plan_before["midcourse_review_ref"],
                plan_before["midcourse_owner_confirmation_ref"],
                plan_before["owner_acceptance_checklist_ref"],
            ]
            evidence_digests = {
                relative: hashlib.sha256((instance / relative).read_bytes()).hexdigest()
                for relative in evidence_refs
            }
            checkpoint_ref = {
                "checkpoint_id": "cp-midcourse-resume",
                "checkpoint_status": "PASSED",
                "task_id": value["task_id"],
                "plan_id": plan_before["plan_id"],
                "plan_version": plan_before["plan_version"],
                "phase_id": "P01",
                "evidence_refs": evidence_refs,
                "evidence_digests": evidence_digests,
                "resume_entry": "P01/P01",
                "receipt_location": "receipts/checkpoint-receipt.json",
                "receipt_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
                "effective_action": "ADVANCE_PHASE",
                "producer": "phase-checkpoint-loop",
                "producer_version": "1.0.6",
                "created_at": "2026-07-24T12:00:00Z",
            }
            checkpoint_path = root / "checkpoint-reference.json"
            checkpoint_path.write_bytes((contracts.stable_json(checkpoint_ref)).encode("utf-8"))

            recorded = planning.record_checkpoint_ref(instance, checkpoint_path, apply=True)
            self.assertEqual(recorded["result"], "RECORDED_CHECKPOINT_REF")
            resumed = planning.resume_from_checkpoint(instance, "cp-midcourse-resume", apply=True)
            self.assertEqual(resumed["result"], "RESUMED")

            plan_after = json.loads((instance / "plan-package.json").read_text(encoding="utf-8"))
            self.assertEqual(
                {field: plan_after[field] for field in contracts.MIDCOURSE_GATE_FIELDS},
                fields_before,
            )
            resume_record = json.loads(
                (instance / "checkpoints" / "resumes" / "cp-midcourse-resume.json").read_text(
                    encoding="utf-8"
                )
            )
            expected_resume_evidence = {
                f"{value['task_id']}/{relative}": digest
                for relative, digest in evidence_digests.items()
            }
            expected_resume_evidence_refs = list(expected_resume_evidence)
            self.assertEqual(resume_record["evidence_refs"], expected_resume_evidence_refs)
            stored_ref = json.loads(
                (instance / "checkpoints" / "refs" / "cp-midcourse-resume.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                {
                    item["path"]: item["sha256"]
                    for item in stored_ref["verified_evidence"]
                },
                expected_resume_evidence,
            )
            self.assertEqual(
                set(resume_record["evidence_refs"]),
                {
                    f"{value['task_id']}/{plan_after['midcourse_review_ref']}",
                    f"{value['task_id']}/{plan_after['midcourse_owner_confirmation_ref']}",
                    f"{value['task_id']}/{plan_after['owner_acceptance_checklist_ref']}",
                },
            )

    def test_unpassed_gate_blocks_later_packet_governance_and_finalization(self) -> None:
        value = self.envelope(
            task_id="task-midcourse-block",
            risk_level="HIGH",
            **midcourse_fields(),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "task.json"
            source.write_bytes((contracts.stable_json(value)).encode("utf-8"))
            state = root / "state"
            created = planning.create_plan(source, state_root=state, apply=True)
            self.assertEqual(created["result"], "CREATED")
            instance = state / value["task_id"]

            packet = planning.create_packet(instance, "P03", "P03", preview=True)
            self.assertEqual(packet["error_code"], "MIDCOURSE_GATE_REQUIRED")

            request = planning.create_governance_request(instance, "PRE_CLOSE", "P03", preview=True)
            self.assertEqual(request["error_code"], "MIDCOURSE_GATE_REQUIRED")

            finalization = planning.finalize_plan(instance, preview=True)
            self.assertEqual(finalization["completion_gate"], "CLOSE_BLOCKED")
            self.assertIn("midcourse_gate:P02:NOT_REACHED", finalization["blocking_findings"])

    def test_passed_gate_allows_later_packet(self) -> None:
        value = self.envelope(
            task_id="task-midcourse-pass",
            **midcourse_fields(
                result="PASS",
                review_ref="evidence/midcourse-review.md",
                owner_ref="evidence/owner-confirmation.md",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "task.json"
            source.write_bytes((contracts.stable_json(value)).encode("utf-8"))
            state = root / "state"
            self.assertEqual(planning.create_plan(source, state_root=state, apply=True)["result"], "CREATED")
            instance = state / value["task_id"]
            (instance / "evidence").mkdir()
            (instance / "evidence" / "midcourse-review.md").write_bytes(("review\n").encode("utf-8"))
            (instance / "evidence" / "owner-confirmation.md").write_bytes(("owner\n").encode("utf-8"))
            (instance / "acceptance").mkdir()
            (instance / "acceptance" / "owner-checklist.md").write_bytes(("checklist\n").encode("utf-8"))
            packet = planning.create_packet(instance, "P03", "P03", preview=True)
            self.assertEqual(packet["result"], "PREVIEW")

    def test_passed_gate_with_fake_refs_is_fail_closed_at_runtime(self) -> None:
        value = self.envelope(
            task_id="task-midcourse-fake-evidence",
            **midcourse_fields(
                result="PASS",
                review_ref="evidence/missing-review.md",
                owner_ref="evidence/missing-owner.md",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "task.json"
            source.write_bytes((contracts.stable_json(value)).encode("utf-8"))
            state = root / "state"
            self.assertEqual(planning.create_plan(source, state_root=state, apply=True)["result"], "CREATED")
            instance = state / value["task_id"]
            packet = planning.create_packet(instance, "P03", "P03", preview=True)
            self.assertEqual(packet["error_code"], "MIDCOURSE_GATE_EVIDENCE_REQUIRED")


if __name__ == "__main__":
    unittest.main()
