#!/usr/bin/env python3
"""D-001 runtime-consumer tests for dynamic midcourse governance evidence."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import plan_contracts as contracts  # noqa: E402
import planning  # noqa: E402


FIXTURE = json.loads(
    (ROOT / "tests" / "fixtures" / "f1-01" / "valid_contracts.json").read_text(encoding="utf-8")
)["task_envelope"]
TASK_ID = "task-d001-runtime"
PHASE_ID = "P02"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((contracts.stable_json(value)).encode("utf-8"))


def _condition(condition_id: str, description: str) -> dict[str, object]:
    return {
        "condition_id": condition_id,
        "condition_type": "COMPLETION",
        "description": description,
        "required": True,
        "evidence_required": True,
        "evaluation_method": "evidence_and_contract_check",
        "status": "PENDING",
        "evidence_refs": [],
    }


def _midcourse_fields() -> dict[str, object]:
    return {
        "midcourse_gate_phase": PHASE_ID,
        "midcourse_gate_entry_criteria": [_condition("midcourse-entry", "范围稳定")],
        "midcourse_gate_exit_criteria": [_condition("midcourse-exit", "复核与确认完成")],
        "midcourse_grill_policy": {
            "mode": "INCREMENTAL",
            "one_question_at_a_time": True,
            "requires_fact_check": True,
            "requires_recommendation": True,
            "scope_change_is_blocking": True,
        },
        "midcourse_review_ref": None,
        "midcourse_owner_confirmation_ref": None,
        "midcourse_gate_result": "NOT_REACHED",
        "owner_acceptance_checklist_ref": "acceptance/owner-checklist.md",
    }


class _FakeCheckpointCore:
    """Only the public read-head boundary is mocked; files remain real."""

    def __init__(self, head: dict[str, object]) -> None:
        self.head = copy.deepcopy(head)

    def runtime_state_root(self, skill_root: Path, candidate: Path) -> Path:
        return Path(candidate).resolve(strict=False)

    def read_head(self, instance: Path, task_id: str, phase_id: str, state_root: Path) -> dict[str, object]:
        return copy.deepcopy(self.head)


class D001RuntimeConsumerTests(unittest.TestCase):
    def create_instance(self, root: Path, task_id: str = TASK_ID) -> tuple[Path, Path, dict[str, object]]:
        envelope = copy.deepcopy(FIXTURE)
        envelope.update({"task_id": task_id, **_midcourse_fields()})
        source = root / f"{task_id}.json"
        _write_json(source, envelope)
        state_root = root / "state-root"
        created = planning.create_plan(source, state_root=state_root, apply=True)
        self.assertEqual(created["result"], "CREATED", created)
        instance = state_root / task_id
        plan = json.loads((instance / "plan-package.json").read_text(encoding="utf-8"))
        return state_root, instance, plan

    def install_dynamic_evidence(
        self,
        state_root: Path,
        instance: Path,
        plan: dict[str, object],
        *,
        owner: bool = True,
        owner_recorded_at: str = "2026-07-24T12:00:00Z",
        review_task_id: str | None = None,
        broken_predecessor: bool = False,
        discontinuous_sequence: bool = False,
    ) -> dict[str, object]:
        review_task_id = review_task_id or str(plan["task_id"])
        review_ref = "evidence/midcourse-review.md"
        owner_ref = "evidence/midcourse-owner-confirmation.json"
        review_time = "2026-07-24T11:00:00Z"
        review = (
            f"# MIDCOURSE_REVIEW\n\n"
            f"- Task ID: `{review_task_id}`\n"
            f"- Plan ID: `{plan['plan_id']}`\n"
            f"- Review phase: `{PHASE_ID}`\n"
            f"- Reviewed at: `{review_time}`\n"
        )
        (instance / review_ref).parent.mkdir(parents=True, exist_ok=True)
        (instance / review_ref).write_bytes((review).encode("utf-8"))
        owner_value = {
            "schema_version": "1.0",
            "receipt_type": "MIDCOURSE_OWNER_CONFIRMATION",
            "task_id": plan["task_id"],
            "plan_id": plan["plan_id"],
            "phase": PHASE_ID,
            "recorded_at": owner_recorded_at,
            "owner_confirmation": {"status": "RECORDED", "decision": "APPROVED_TO_CONTINUE"},
            "dynamic_governance_state": "MIDCOURSE_PASSED",
            "no_drift_assessment": {"overall": "NO_DRIFT"},
        }
        if owner:
            _write_json(instance / owner_ref, owner_value)

        checkpoint_root = state_root / "checkpoint-engine"
        store = checkpoint_root / "phase-checkpoints" / "runtime-test" / "runtime-test"
        checkpoint_ids = (
            "CP-task-d001-runtime-P02-1-1",
            "CP-task-d001-runtime-P02-1-2",
        )
        heads: dict[str, dict[str, object]] = {}
        refs: list[dict[str, object]] = []
        for index, checkpoint_id in enumerate(checkpoint_ids, start=1):
            result = {
                "schema_version": "1.0",
                "cp_id": checkpoint_id,
                "task_id": plan["task_id"],
                "phase_id": PHASE_ID,
                "status": "PASSED",
                "effective_action": "ADVANCE_PHASE",
            }
            result_path = store / "artifacts" / checkpoint_id / "result.json"
            _write_json(result_path, result)
            result_digest = _sha256_bytes(result_path.read_bytes())
            commit = {
                "schema_version": "1.0",
                "cp_id": checkpoint_id,
                "task_id": plan["task_id"],
                "phase_id": PHASE_ID,
                "result_hash": result_digest,
                "validation_status": "PASSED",
            }
            commit_path = store / "commits" / f"{checkpoint_id}.commit.json"
            _write_json(commit_path, commit)
            commit_digest = planning._canonical_object_digest(commit)
            head = {
                "schema_version": "1.0",
                "commit_id": checkpoint_id,
                "commit_hash": commit_digest,
                "commit_sequence": index,
                "source": "PUBLISHED_COMMIT",
                "effective_action": "ADVANCE_PHASE",
                "commit": {
                    "task_id": plan["task_id"],
                    "phase_id": PHASE_ID,
                    "validation_status": "PASSED",
                },
            }
            head_path = store / "heads" / f"{plan['task_id']}-{PHASE_ID}-{index}.json"
            _write_json(head_path, head)
            heads[checkpoint_id] = head
            receipt_path = checkpoint_root / "checkpoint-evidence" / checkpoint_id / "receipt.json"
            _write_json(receipt_path, {"checkpoint_id": checkpoint_id, "result": "PASSED"})
            evidence = [
                {"kind": "checkpoint_result", "path": str(result_path), "sha256": result_digest},
                {"kind": "checkpoint_commit", "path": str(commit_path), "sha256": _sha256_bytes(commit_path.read_bytes())},
                {"kind": "checkpoint_head", "path": str(head_path), "sha256": _sha256_bytes(head_path.read_bytes())},
            ]
            previous = None if index == 1 else checkpoint_ids[0]
            if broken_predecessor and index == 2:
                previous = "CP-task-d001-runtime-P02-9-9"
            sequence = index
            if discontinuous_sequence and index == 2:
                sequence = 4
            ref = {
                "schema_version": 1,
                "checkpoint_id": checkpoint_id,
                "checkpoint_status": "PASSED",
                "task_id": plan["task_id"],
                "plan_id": plan["plan_id"],
                "plan_version": plan["plan_version"],
                "phase_id": PHASE_ID,
                "evidence_refs": evidence,
                "resume_entry": PHASE_ID,
                "receipt_location": str(receipt_path),
                "producer": "phase-checkpoint-loop",
                "producer_version": "1.0.6",
                "created_at": f"2026-07-24T{10 + index:02d}:00:00Z",
                "effective_action": "ADVANCE_PHASE",
                "publication_status": "PUBLISHED_COMMIT",
                "verification_status": "PASSED",
                "canonical_state_root": str(checkpoint_root),
                "previous_checkpoint_id": previous,
                "commit_sequence": sequence,
                "result_location": str(result_path),
                "result_sha256": result_digest,
                "commit_location": str(commit_path),
                "commit_hash": commit_digest,
                "head_location": str(head_path),
                "head_sha256": _sha256_bytes(head_path.read_bytes()),
                "lineage_digest": "a" * 64,
            }
            refs.append(ref)
            _write_json(instance / "checkpoints" / "refs" / f"{checkpoint_id}.json", ref)

        discovery = {
            "schema_version": "1.0",
            "task_id": plan["task_id"],
            "midcourse_gate_phase": PHASE_ID,
            "midcourse_gate_result": "MIDCOURSE_PASSED",
            "midcourse_review_ref": review_ref,
            "midcourse_owner_confirmation_ref": owner_ref,
            "official_read_head": "PASSED",
            "official_read_head_source": "PUBLISHED_COMMIT",
            "official_read_head_action": "ADVANCE_PHASE",
            "latest_checkpoint": checkpoint_ids[-1],
            "dynamic_governance_state": {
                "status": "MIDCOURSE_PASSED",
                "authority": "external_dynamic_governance_projection",
                "official_contract_result_remains": "NOT_REACHED",
                "latest_checkpoint": checkpoint_ids[-1],
            },
        }
        _write_json(state_root / "discovery" / "DISCOVERY_STATE.json", discovery)
        return {"head": heads[checkpoint_ids[-1]], "refs": refs, "review_time": review_time}

    def runtime(self, state_root: Path, instance: Path, plan: dict[str, object]) -> dict[str, object]:
        with mock.patch.object(
            planning,
            "_load_public_checkpoint_core",
            return_value=_FakeCheckpointCore(self._head),
        ):
            return planning._midcourse_gate_runtime_state(state_root, instance, plan)

    def setUp(self) -> None:
        self._head: dict[str, object] = {}

    def test_complete_dynamic_evidence_projects_pass_without_mutating_frozen_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root, instance, plan = self.create_instance(Path(directory))
            evidence = self.install_dynamic_evidence(state_root, instance, plan)
            self._head = evidence["head"]  # type: ignore[assignment]
            before = (instance / "plan-package.json").read_bytes()
            with mock.patch.object(planning, "_load_public_checkpoint_core", return_value=_FakeCheckpointCore(self._head)):
                summary = planning.verify_plan_summary(instance)
                final = planning.finalize_plan(instance, preview=True)
            self.assertEqual(summary["midcourse_gate_result"], "MIDCOURSE_PASSED")
            self.assertEqual(summary["midcourse_gate_effective_result"], "PASS")
            self.assertEqual(summary["midcourse_gate_source"], "EXTERNAL_DYNAMIC_GOVERNANCE_PROJECTION")
            self.assertEqual(final["midcourse_gate_result"], summary["midcourse_gate_result"])
            self.assertEqual(final["midcourse_gate_effective_result"], summary["midcourse_gate_effective_result"])
            self.assertEqual((instance / "plan-package.json").read_bytes(), before)

    def test_missing_owner_confirmation_remains_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root, instance, plan = self.create_instance(Path(directory))
            evidence = self.install_dynamic_evidence(state_root, instance, plan, owner=False)
            self._head = evidence["head"]  # type: ignore[assignment]
            runtime = self.runtime(state_root, instance, plan)
            self.assertFalse(runtime["passed"])
            self.assertEqual(runtime["effective_result"], "NOT_REACHED")
            self.assertIn("MIDCOURSE_EVIDENCE_INVALID", str(runtime["evidence_block"]))

    def test_owner_confirmation_before_understanding_proof_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root, instance, plan = self.create_instance(Path(directory))
            evidence = self.install_dynamic_evidence(
                state_root, instance, plan, owner_recorded_at="2026-07-24T10:00:00Z"
            )
            self._head = evidence["head"]  # type: ignore[assignment]
            runtime = self.runtime(state_root, instance, plan)
            self.assertFalse(runtime["passed"])
            self.assertIn("MIDCOURSE_OWNER_CONFIRMATION_EARLY", str(runtime["evidence_block"]))

    def test_cross_task_review_reference_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root, instance, plan = self.create_instance(Path(directory))
            evidence = self.install_dynamic_evidence(
                state_root, instance, plan, review_task_id="another-task"
            )
            self._head = evidence["head"]  # type: ignore[assignment]
            runtime = self.runtime(state_root, instance, plan)
            self.assertFalse(runtime["passed"])
            self.assertIn("MIDCOURSE_EVIDENCE_INVALID", str(runtime["evidence_block"]))

    def test_missing_or_discontinuous_checkpoint_predecessor_is_rejected(self) -> None:
        for kwargs in ({"broken_predecessor": True}, {"discontinuous_sequence": True}):
            with self.subTest(**kwargs), tempfile.TemporaryDirectory() as directory:
                state_root, instance, plan = self.create_instance(Path(directory))
                evidence = self.install_dynamic_evidence(state_root, instance, plan, **kwargs)
                self._head = evidence["head"]  # type: ignore[assignment]
                runtime = self.runtime(state_root, instance, plan)
                self.assertFalse(runtime["passed"])
                self.assertIn("MIDCOURSE_CHECKPOINT_CHAIN_INVALID", str(runtime["evidence_block"]))

    def test_historical_p02_head_is_validated_as_history_not_current_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root, instance, plan = self.create_instance(Path(directory))
            evidence = self.install_dynamic_evidence(state_root, instance, plan)
            self._head = evidence["head"]  # type: ignore[assignment]
            with mock.patch.object(planning, "_load_public_checkpoint_core", return_value=_FakeCheckpointCore(self._head)):
                runtime = planning._midcourse_gate_runtime_state(state_root, instance, plan)
                checkpoint_gate = planning._final_checkpoint_gate(
                    state_root,
                    instance,
                    json.loads((instance / "task-envelope.json").read_text(encoding="utf-8")),
                    plan,
                    (instance / "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8"),
                    {"require_checkpoint_ref": True},
                    "ADVANCED",
                )
            self.assertTrue(runtime["passed"])
            self.assertNotIn("checkpoint:CP-task-d001-runtime-P02-1-1:CHECKPOINT_PROJECTION_DRIFT", checkpoint_gate[0])
            self.assertEqual(checkpoint_gate[4], "CP-task-d001-runtime-P02-1-2")

    def test_historical_successor_projection_uses_formal_supplement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root, instance, plan = self.create_instance(Path(directory))
            evidence = self.install_dynamic_evidence(state_root, instance, plan)
            self._head = evidence["head"]  # type: ignore[assignment]
            old_ref = evidence["refs"][0]  # type: ignore[index]
            old_checkpoint_id = str(old_ref["checkpoint_id"])

            audit_path = instance / "5_audit.md"
            audit_path.write_bytes(("historical audit\n").encode("utf-8"))
            historical_audit_digest = _sha256_bytes(audit_path.read_bytes())
            binding_path = state_root / "checkpoint-engine" / "checkpoint-root-bindings" / "P02.json"
            _write_json(
                binding_path,
                {
                    "first_checkpoint_id": old_checkpoint_id,
                    "task_id": plan["task_id"],
                    "plan_id": plan["plan_id"],
                    "phase_id": PHASE_ID,
                    "latest_checkpoint_id": old_checkpoint_id,
                    "lineage_digest": "a" * 64,
                },
            )
            historical_binding_digest = _sha256_bytes(binding_path.read_bytes())

            commit_path = Path(str(old_ref["commit_location"]))
            commit = json.loads(commit_path.read_text(encoding="utf-8"))
            commit["table_hashes"] = {str(audit_path): historical_audit_digest}
            _write_json(commit_path, commit)
            old_ref["commit_hash"] = planning._canonical_object_digest(commit)
            for item in old_ref["evidence_refs"]:  # type: ignore[union-attr]
                if isinstance(item, dict) and item.get("kind") == "checkpoint_commit":
                    item["sha256"] = _sha256_bytes(commit_path.read_bytes())

            supplement_path = (
                state_root
                / "checkpoint-engine"
                / "phase-checkpoints"
                / "runtime-test"
                / "runtime-test"
                / "supplements"
                / old_checkpoint_id
                / "evidence-001.json"
            )
            _write_json(
                supplement_path,
                {
                    "commit_hash": planning._canonical_object_digest(commit),
                    "config_hash": "b" * 64,
                    "created_at": "2026-07-24T12:00:00Z",
                    "engine_version": "1.0.6",
                    "evidence": {
                        "current_table_projection": {
                            "action": "preserved_without_commit_or_rebinding",
                            "sha256": {
                                str(binding_path): historical_binding_digest,
                                str(audit_path): historical_audit_digest,
                            },
                            "status": "UNCOMMITTED_POST_CHECKPOINT_DELTA",
                        }
                    },
                    "cp_id": old_checkpoint_id,
                    "schema_version": "1.0",
                    "updated_at": "2026-07-24T12:00:00Z",
                    "revision": 1,
                },
            )
            old_ref["evidence_refs"].extend(  # type: ignore[union-attr]
                [
                    {
                        "kind": "checkpoint_supplement",
                        "path": str(supplement_path),
                        "sha256": _sha256_bytes(supplement_path.read_bytes()),
                    },
                    {
                        "kind": "checkpoint_root_binding",
                        "path": str(binding_path),
                        "sha256": historical_binding_digest,
                    },
                    {
                        "kind": "audit",
                        "path": str(audit_path),
                        "sha256": historical_audit_digest,
                    },
                ]
            )

            _write_json(
                binding_path,
                {
                    "first_checkpoint_id": old_checkpoint_id,
                    "task_id": plan["task_id"],
                    "plan_id": plan["plan_id"],
                    "phase_id": PHASE_ID,
                    "latest_checkpoint_id": "CP-task-d001-runtime-P02-1-2",
                    "lineage_digest": "a" * 64,
                },
            )
            audit_path.write_bytes(("successor audit\n").encode("utf-8"))
            _write_json(
                instance / "checkpoints" / "refs" / f"{old_checkpoint_id}.json",
                old_ref,
            )

            with mock.patch.object(planning, "_load_public_checkpoint_core", return_value=_FakeCheckpointCore(self._head)):
                checkpoint_gate = planning._final_checkpoint_gate(
                    state_root,
                    instance,
                    json.loads((instance / "task-envelope.json").read_text(encoding="utf-8")),
                    plan,
                    (instance / "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8"),
                    {"require_checkpoint_ref": True},
                    "ADVANCED",
                )
            self.assertNotIn(
                f"checkpoint:{old_checkpoint_id}:CHECKPOINT_PROJECTION_DRIFT",
                checkpoint_gate[0],
            )
            self.assertEqual(checkpoint_gate[4], "CP-task-d001-runtime-P02-1-2")

            old_ref["evidence_refs"] = [
                item
                for item in old_ref["evidence_refs"]  # type: ignore[union-attr]
                if not isinstance(item, dict) or item.get("kind") != "checkpoint_supplement"
            ]
            commit.pop("table_hashes", None)
            _write_json(commit_path, commit)
            old_ref["commit_hash"] = planning._canonical_object_digest(commit)
            for item in old_ref["evidence_refs"]:  # type: ignore[union-attr]
                if isinstance(item, dict) and item.get("kind") == "checkpoint_commit":
                    item["sha256"] = _sha256_bytes(commit_path.read_bytes())
            _write_json(
                binding_path,
                {
                    "first_checkpoint_id": "CP-task-d001-runtime-P02-9-9",
                    "task_id": plan["task_id"],
                    "plan_id": plan["plan_id"],
                    "phase_id": PHASE_ID,
                    "latest_checkpoint_id": "CP-task-d001-runtime-P02-9-9",
                    "lineage_digest": "a" * 64,
                },
            )
            _write_json(instance / "checkpoints" / "refs" / f"{old_checkpoint_id}.json", old_ref)
            with mock.patch.object(planning, "_load_public_checkpoint_core", return_value=_FakeCheckpointCore(self._head)):
                blocked_gate = planning._final_checkpoint_gate(
                    state_root,
                    instance,
                    json.loads((instance / "task-envelope.json").read_text(encoding="utf-8")),
                    plan,
                    (instance / "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8"),
                    {"require_checkpoint_ref": True},
                    "ADVANCED",
                )
            self.assertIn(
                f"checkpoint:{old_checkpoint_id}:CHECKPOINT_PROJECTION_DRIFT",
                blocked_gate[0],
            )

    def test_dynamic_state_cannot_override_frozen_gate_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root, instance, plan = self.create_instance(Path(directory))
            evidence = self.install_dynamic_evidence(state_root, instance, plan)
            self._head = evidence["head"]  # type: ignore[assignment]
            path = state_root / "discovery" / "DISCOVERY_STATE.json"
            dynamic = json.loads(path.read_text(encoding="utf-8"))
            dynamic["dynamic_governance_state"]["official_contract_result_remains"] = "PASS"
            _write_json(path, dynamic)
            runtime = self.runtime(state_root, instance, plan)
            self.assertFalse(runtime["passed"])
            self.assertIn("MIDCOURSE_DYNAMIC_STATE_INVALID", str(runtime["evidence_block"]))

    def test_legacy_envelope_without_eight_fields_remains_backward_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            envelope = copy.deepcopy(FIXTURE)
            envelope["task_id"] = "task-d001-legacy"
            source = Path(directory) / "legacy.json"
            _write_json(source, envelope)
            state_root = Path(directory) / "state-root"
            created = planning.create_plan(source, state_root=state_root, apply=True)
            self.assertEqual(created["result"], "CREATED", created)
            summary = planning.verify_plan_summary(state_root / envelope["task_id"])
            self.assertIsNone(summary.get("midcourse_gate_result"))
            self.assertNotIn("INVALID_CONTRACT", " ".join(summary["blocking_findings"]))

    def test_illegal_pass_combination_is_rejected_before_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            envelope = copy.deepcopy(FIXTURE)
            envelope.update({"task_id": "task-d001-illegal", **_midcourse_fields()})
            envelope["midcourse_gate_result"] = "PASS"
            source = Path(directory) / "illegal.json"
            _write_json(source, envelope)
            result = planning.create_plan(source, state_root=Path(directory) / "state-root", apply=True)
            self.assertEqual(result["result"], "FAILED")
            self.assertEqual(result["error_code"], "INVALID_CONTRACT")

    def test_verify_and_finalize_use_identical_dynamic_midcourse_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root, instance, plan = self.create_instance(Path(directory), "task-d001-consistency")
            evidence = self.install_dynamic_evidence(state_root, instance, plan)
            self._head = evidence["head"]  # type: ignore[assignment]
            with mock.patch.object(planning, "_load_public_checkpoint_core", return_value=_FakeCheckpointCore(self._head)):
                summary = planning.verify_plan_summary(instance)
                final = planning.finalize_plan(instance, preview=True)
            self.assertEqual(
                (summary["midcourse_gate_result"], summary["midcourse_gate_effective_result"], summary["midcourse_gate_source"]),
                (final["midcourse_gate_result"], final["midcourse_gate_effective_result"], final["midcourse_gate_source"]),
            )


if __name__ == "__main__":
    unittest.main()
