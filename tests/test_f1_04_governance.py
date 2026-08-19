#!/usr/bin/env python3
"""F1-04 local GovernanceRequest and CleanlinessReceipt runtime tests."""

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
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


class F104GovernanceTests(unittest.TestCase):
    def envelope(self, **updates: object) -> dict[str, object]:
        value = copy.deepcopy(FIXTURE)
        value.update(updates)
        return value

    def write_json(self, root: Path, name: str, value: dict[str, object]) -> Path:
        path = root / name
        path.write_bytes((contracts.stable_json(value)).encode("utf-8"))
        return path

    def create_instance(self, root: Path, task_id: str = "task-f104", **updates: object) -> tuple[Path, Path]:
        value = self.envelope(task_id=task_id, **updates)
        envelope_path = self.write_json(root, f"{task_id}.json", value)
        state = root / "state-root"
        result = planning.create_plan(envelope_path, state_root=state, apply=True)
        self.assertEqual(result["result"], "CREATED", result)
        return state, state / task_id

    def create_packet(self, instance: Path) -> dict[str, object]:
        result = planning.create_packet(instance, "P01", "P01", apply=True, agent="test-f1-04")
        self.assertEqual(result["result"], "CREATED", result)
        return result["packet"]

    def create_request(self, instance: Path, stage: str = "PRE_WRITE", apply: bool = True) -> dict[str, object]:
        result = planning.create_governance_request(instance, stage, "P01", apply=apply, preview=not apply)
        self.assertIn(result["result"], {"CREATED_GOVERNANCE_REQUEST", "EXISTING_GOVERNANCE_REQUEST", "PREVIEW"}, result)
        return result

    def make_receipt(
        self,
        request: dict[str, object],
        *,
        result: str = "PASS",
        receipt_id: str = "cleanliness-receipt-01",
    ) -> dict[str, object]:
        blocking = ["protected asset changed"] if result == "BLOCKED" else []
        warnings = ["temporary artifact reported"] if result == "PASS_WITH_WARNINGS" else []
        return {
            "receipt_id": receipt_id,
            "request_id": request["request_id"],
            "task_id": request["task_id"],
            "plan_id": request["plan_id"],
            "phase_id": request["phase_id"],
            "governance_stage": request["governance_stage"],
            "result": result,
            "cleanliness_status": "CLEAN" if result == "PASS" else result,
            "scope_match": True,
            "blocking_findings": blocking,
            "non_blocking_findings": warnings,
            "duplicate_candidates": ["KEEP"],
            "unused_asset_candidates": ["KEEP"],
            "cleanup_actions": [],
            "protected_assets_status": {"status": "PRESERVED", "future_nested": {"keep": True}},
            "evidence_refs": ["fixture/f1-04-evidence.json"],
            "checked_at": "2026-07-17T00:00:00Z",
            "producer": "fixture-cleanliness",
            "producer_version": "0.0.1",
        }

    def write_receipt(self, root: Path, receipt: dict[str, object], name: str = "cleanliness.json") -> Path:
        return self.write_json(root, name, receipt)

    def make_execution_receipt(
        self,
        packet: dict[str, object],
        *,
        receipt_id: str = "receipt-execution-f104-postwrite",
        result: str = "PASS_WITH_WARNINGS",
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
            "summary": "Fixture execution completed for POST_WRITE binding.",
            "changed_paths": ["target.txt"],
            "created_assets": [],
            "deleted_assets": [],
            "test_results": {"focused": "PASS"},
            "evidence_refs": ["fixture/execution-evidence.json"],
            "warnings": ["fixture warning"] if result == "PASS_WITH_WARNINGS" else [],
            "blocking_findings": [],
            "rollback_status": "NOT_REQUIRED",
            "started_at": "2026-07-17T00:00:00Z",
            "completed_at": "2026-07-17T00:01:00Z",
            "producer": "fixture-execution",
            "producer_version": "0.0.1",
        }

    def prepare_postwrite_binding(self, root: Path) -> tuple[Path, Path, str, str]:
        state, instance = self.create_instance(root, "task-f104-postwrite-binding")
        packet = self.create_packet(instance)
        execution_id = "receipt-execution-f104-postwrite"
        execution = self.make_execution_receipt(packet, receipt_id=execution_id)
        self.assertEqual(
            planning.record_receipt(instance, self.write_receipt(root, execution, "execution-source.json"), apply=True)["result"],
            "RECORDED",
        )
        request = self.create_request(instance, "POST_WRITE")["request"]
        postwrite_id = "cln-f104-postwrite-binding"
        postwrite = self.make_receipt(request, result="PASS_WITH_WARNINGS", receipt_id=postwrite_id)
        self.assertEqual(
            planning.record_cleanliness_receipt(instance, self.write_receipt(root, postwrite, "postwrite-source.json"), apply=True)["result"],
            "RECORDED_CLEANLINESS_RECEIPT",
        )
        return state, instance, postwrite_id, execution_id

    def checklist_metadata(self, instance: Path) -> dict[str, object]:
        return workflow.extract_machine_json((instance / "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8"), "workflow")

    def set_required_stages(self, instance: Path, stages: list[str]) -> None:
        path = instance / "plan-package.json"
        plan = json.loads(path.read_text(encoding="utf-8"))
        plan["governance_policy"]["required_stages"] = stages
        path.write_bytes((contracts.stable_json(plan)).encode("utf-8"))

    def owner_gate_kwargs(self, state: Path, instance: Path, **updates: object) -> dict[str, object]:
        plan = json.loads((instance / "plan-package.json").read_text(encoding="utf-8"))
        commit = "a" * 40
        (instance / "evidence").mkdir(exist_ok=True)
        (instance / "evidence" / "owner-chain.json").write_text(
            contracts.stable_json({"commit": commit, "head": commit, "result_commit_head": "VALID"}),
            encoding="utf-8",
        )
        value: dict[str, object] = {
            "task_id": plan["task_id"],
            "plan_id": plan["plan_id"],
            "state_root": state,
            "gate_id": "gate-f101",
            "expected_status": "PENDING",
            "decision": "SATISFIED",
            "confirmation_reference": "test-owner-confirmation",
            "confirmation_statement": "Owner explicitly accepted the test handoff.",
            "accepted_commit": commit,
            "accepted_checkpoint": "cp-f104-authoritative",
            "result_commit_head": "VALID",
            "direct_read_head": "PASSED",
            "external_read_head": "PASSED",
            "authorize": "PRE_CLOSE",
            "evidence_refs": ["evidence/owner-chain.json"],
            "apply": True,
            "preview": False,
            "agent": "test-f1-04",
        }
        value.update(updates)
        return value

    def prepare_owner_gate_instance(self, root: Path, task_id: str = "task-f104-owner") -> tuple[Path, Path]:
        state, instance = self.create_instance(root, task_id)
        plan_path = instance / "plan-package.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["human_gates"][0]["formal_route"] = "FINAL_MANUAL_ACCEPTANCE"
        plan_path.write_bytes((contracts.stable_json(plan)).encode("utf-8"))
        return state, instance

    def test_request_contract_layout_and_protected_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            result = self.create_request(instance, "PRE_WRITE")
            request = result["request"]
            contracts.validate_governance_request(request)
            self.assertEqual(contracts.contract_field_count("governance_request"), 17)
            self.assertTrue((instance / "governance" / "requests" / f"{request['request_id']}.json").is_file())
            self.assertIn("VERSION", request["protected_assets"])
            self.assertFalse((instance / "governance" / "receipts").exists())

    def test_postwrite_execution_binding_is_formal_idempotent_and_preserves_receipt_facts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, instance, postwrite_id, execution_id = self.prepare_postwrite_binding(root)
            postwrite_path = instance / "governance" / "receipts" / f"{postwrite_id}.json"
            before = json.loads(postwrite_path.read_text(encoding="utf-8"))
            preview = planning.bind_postwrite_execution_receipt(
                instance,
                state_root=state,
                task_id="task-f104-postwrite-binding",
                plan_id=before["plan_id"],
                phase_id="P01",
                postwrite_receipt_id=postwrite_id,
                execution_receipt_id=execution_id,
                preview=True,
            )
            self.assertEqual(preview["result"], "PREVIEW")
            self.assertEqual(before, json.loads(postwrite_path.read_text(encoding="utf-8")))

            bound = planning.bind_postwrite_execution_receipt(
                instance,
                state_root=state,
                task_id="task-f104-postwrite-binding",
                plan_id=before["plan_id"],
                phase_id="P01",
                postwrite_receipt_id=postwrite_id,
                execution_receipt_id=execution_id,
                apply=True,
            )
            self.assertEqual(bound["result"], "BOUND_POSTWRITE_EXECUTION_RECEIPT", bound)
            stored = json.loads(postwrite_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["execution_receipt_id"], execution_id)
            self.assertEqual(stored["execution_receipt_digest"], contracts.contract_digest(json.loads((instance / "receipts" / f"{execution_id}.json").read_text(encoding="utf-8"))))
            self.assertEqual(stored["checked_at"], before["checked_at"])
            self.assertEqual(stored["result"], before["result"])
            self.assertEqual(stored["evidence_refs"], before["evidence_refs"])

            snapshot = tree_snapshot(instance)
            repeated = planning.bind_postwrite_execution_receipt(
                instance,
                state_root=state,
                task_id="task-f104-postwrite-binding",
                plan_id=before["plan_id"],
                phase_id="P01",
                postwrite_receipt_id=postwrite_id,
                execution_receipt_id=execution_id,
                apply=True,
            )
            self.assertEqual(repeated["result"], "EXISTING_POSTWRITE_EXECUTION_BINDING", repeated)
            self.assertTrue(repeated["idempotent"])
            self.assertEqual(snapshot, tree_snapshot(instance))

    def test_postwrite_execution_binding_rejects_missing_and_mismatched_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, instance, postwrite_id, execution_id = self.prepare_postwrite_binding(root)
            postwrite = json.loads((instance / "governance" / "receipts" / f"{postwrite_id}.json").read_text(encoding="utf-8"))
            kwargs = {
                "state_root": state,
                "task_id": "task-f104-postwrite-binding",
                "plan_id": postwrite["plan_id"],
                "phase_id": "P01",
                "postwrite_receipt_id": postwrite_id,
                "execution_receipt_id": execution_id,
                "apply": True,
            }
            wrong_state = planning.bind_postwrite_execution_receipt(instance, **{**kwargs, "state_root": root / "wrong-state"})
            self.assertEqual(wrong_state["error_code"], "STATE_ROOT_MISMATCH")
            wrong_task = planning.bind_postwrite_execution_receipt(instance, **{**kwargs, "task_id": "other-task"})
            self.assertEqual(wrong_task["error_code"], "TASK_ID_MISMATCH")
            wrong_plan = planning.bind_postwrite_execution_receipt(instance, **{**kwargs, "plan_id": "plan-other"})
            self.assertEqual(wrong_plan["error_code"], "PLAN_ID_MISMATCH")
            missing = planning.bind_postwrite_execution_receipt(instance, **{**kwargs, "execution_receipt_id": "receipt-missing"})
            self.assertEqual(missing["error_code"], "EXECUTION_RECEIPT_NOT_FOUND")
            self.assertEqual(postwrite, json.loads((instance / "governance" / "receipts" / f"{postwrite_id}.json").read_text(encoding="utf-8")))

    def test_postwrite_execution_binding_conflict_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, instance, postwrite_id, execution_id = self.prepare_postwrite_binding(root)
            postwrite_path = instance / "governance" / "receipts" / f"{postwrite_id}.json"
            postwrite = json.loads(postwrite_path.read_text(encoding="utf-8"))
            postwrite["execution_receipt_id"] = "receipt-other"
            postwrite["execution_receipt_digest"] = "b" * 64
            postwrite_path.write_bytes((contracts.stable_json(postwrite)).encode("utf-8"))
            before = postwrite_path.read_bytes()
            result = planning.bind_postwrite_execution_receipt(
                instance,
                state_root=state,
                task_id="task-f104-postwrite-binding",
                plan_id=postwrite["plan_id"],
                phase_id="P01",
                postwrite_receipt_id=postwrite_id,
                execution_receipt_id=execution_id,
                apply=True,
            )
            self.assertEqual(result["result"], "CONFLICT")
            self.assertEqual(result["error_code"], "POSTWRITE_EXECUTION_BINDING_CONFLICT")
            self.assertEqual(before, postwrite_path.read_bytes())

    def test_postwrite_execution_binding_cli_route_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, instance, postwrite_id, execution_id = self.prepare_postwrite_binding(root)
            plan = json.loads((instance / "plan-package.json").read_text(encoding="utf-8"))
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "planning.py"),
                    "bind-postwrite-execution-receipt",
                    "--instance-root", str(instance),
                    "--state-root", str(state),
                    "--task-id", "task-f104-postwrite-binding",
                    "--plan-id", plan["plan_id"],
                    "--phase-id", "P01",
                    "--postwrite-receipt-id", postwrite_id,
                    "--execution-receipt-id", execution_id,
                    "--preview",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["result"], "PREVIEW")

    def test_owner_gate_registers_satisfied_with_formal_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, instance = self.prepare_owner_gate_instance(root)
            kwargs = self.owner_gate_kwargs(state, instance)
            with mock.patch.object(planning, "_owner_gate_checkpoint_authority", return_value=({}, {})):
                result = planning.register_owner_gate(instance, **kwargs)
            self.assertEqual(result["result"], "REGISTERED_OWNER_GATE", result)
            plan = json.loads((instance / "plan-package.json").read_text(encoding="utf-8"))
            gate = plan["human_gates"][0]
            self.assertEqual(gate["status"], "SATISFIED")
            receipt_path = instance / gate["owner_gate_receipt_ref"]
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["decision"], "SATISFIED")
            self.assertEqual(receipt["identity_assurance"], planning.OWNER_GATE_IDENTITY_ASSURANCE)
            self.assertEqual(receipt["plan_package_digest_after"], contracts.contract_digest(plan))

    def test_owner_gate_repeat_is_idempotent_and_conflict_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, instance = self.prepare_owner_gate_instance(root)
            kwargs = self.owner_gate_kwargs(state, instance)
            with mock.patch.object(planning, "_owner_gate_checkpoint_authority", return_value=({}, {})):
                first = planning.register_owner_gate(instance, **kwargs)
                before = tree_snapshot(instance)
                repeated = planning.register_owner_gate(instance, **kwargs)
                conflict_kwargs = dict(kwargs)
                conflict_kwargs["confirmation_statement"] = "A different confirmation must conflict."
                conflict = planning.register_owner_gate(instance, **conflict_kwargs)
            self.assertEqual(first["result"], "REGISTERED_OWNER_GATE")
            self.assertEqual(repeated["result"], "EXISTING_OWNER_GATE", repeated)
            self.assertTrue(repeated["idempotent"])
            self.assertEqual(before, tree_snapshot(instance))
            self.assertEqual(conflict["result"], "CONFLICT", conflict)
            self.assertEqual(conflict["error_code"], "OWNER_GATE_CONFLICT")

    def test_owner_gate_rejects_missing_evidence_and_context_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, instance = self.prepare_owner_gate_instance(root, "task-f104-owner-negative")
            kwargs = self.owner_gate_kwargs(state, instance, evidence_refs=[])
            before = tree_snapshot(instance)
            with mock.patch.object(planning, "_owner_gate_checkpoint_authority", return_value=({}, {})):
                missing = planning.register_owner_gate(instance, **kwargs)
                wrong_task = planning.register_owner_gate(
                    instance,
                    **self.owner_gate_kwargs(state, instance, task_id="other-task"),
                )
                wrong_state = planning.register_owner_gate(
                    instance,
                    **self.owner_gate_kwargs(root / "other-state", instance),
                )
                wrong_gate = planning.register_owner_gate(
                    instance,
                    **self.owner_gate_kwargs(state, instance, gate_id="missing-gate"),
                )
            self.assertEqual(missing["error_code"], "OWNER_GATE_EVIDENCE_REQUIRED")
            self.assertEqual(wrong_task["error_code"], "TASK_ID_MISMATCH")
            self.assertEqual(wrong_state["error_code"], "STATE_ROOT_MISMATCH")
            self.assertEqual(wrong_gate["error_code"], "OWNER_GATE_NOT_FOUND")
            self.assertEqual(before, tree_snapshot(instance))

    def test_owner_gate_rejects_non_user_gate_and_other_pending_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, instance = self.prepare_owner_gate_instance(root, "task-f104-owner-type")
            plan_path = instance / "plan-package.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["human_gates"][0]["condition_type"] = "COMPLETION"
            plan_path.write_bytes((contracts.stable_json(plan)).encode("utf-8"))
            non_user = planning.register_owner_gate(
                instance,
                **self.owner_gate_kwargs(state, instance),
            )
            self.assertEqual(non_user["result"], "FAILED")

            plan["human_gates"][0]["condition_type"] = "USER_GATE"
            plan["human_gates"].append(
                {
                    "condition_id": "gate-other",
                    "condition_type": "USER_GATE",
                    "description": "Another required confirmation",
                    "required": True,
                    "evidence_required": True,
                    "evaluation_method": "owner_review",
                    "status": "PENDING",
                    "evidence_refs": [],
                }
            )
            plan_path.write_bytes((contracts.stable_json(plan)).encode("utf-8"))
            with mock.patch.object(planning, "_owner_gate_checkpoint_authority", return_value=({}, {})):
                other_pending = planning.register_owner_gate(
                    instance,
                    **self.owner_gate_kwargs(state, instance),
                )
            self.assertEqual(other_pending["error_code"], "OWNER_GATE_OTHER_GATE_PENDING")

    def test_preclose_requires_the_formal_owner_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, instance = self.prepare_owner_gate_instance(root, "task-f104-owner-preclose")
            kwargs = self.owner_gate_kwargs(state, instance)
            with mock.patch.object(planning, "_owner_gate_checkpoint_authority", return_value=({}, {})):
                registered = planning.register_owner_gate(instance, **kwargs)
                plan = json.loads((instance / "plan-package.json").read_text(encoding="utf-8"))
                envelope = json.loads((instance / "task-envelope.json").read_text(encoding="utf-8"))
                checklist = (instance / workflow.CHECKLIST_NAME).read_text(encoding="utf-8")
                blocking, waiting, _warnings, evidence = planning._final_owner_gate_receipt_gate(
                    state.resolve(), instance.resolve(), envelope, plan, checklist
                )
            self.assertEqual(blocking, [])
            self.assertEqual(waiting, [])
            self.assertIn(registered["receipt_path"].split(f"{instance.resolve().as_posix()}/", 1)[1], evidence)

            plan["human_gates"][0]["owner_gate_receipt_ref"] = "governance/owner-gate-receipts/missing.json"
            (instance / "plan-package.json").write_bytes((contracts.stable_json(plan)).encode("utf-8"))
            checklist = (instance / workflow.CHECKLIST_NAME).read_text(encoding="utf-8")
            blocking, _waiting, _warnings, _evidence = planning._final_owner_gate_receipt_gate(
                state.resolve(),
                instance.resolve(),
                envelope,
                plan,
                checklist,
            )
            self.assertIn("owner_gate:gate-f101:RECEIPT_NOT_FOUND", blocking)

    def test_request_preview_is_zero_write_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            before = tree_snapshot(instance)
            first = self.create_request(instance, "PRE_WRITE", apply=False)
            second = self.create_request(instance, "PRE_WRITE", apply=False)
            self.assertEqual(first["request_id"], second["request_id"])
            self.assertEqual(first["request_digest"], second["request_digest"])
            self.assertEqual(before, tree_snapshot(instance))
            self.assertFalse((instance / "governance").exists())

    def test_request_apply_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            first = self.create_request(instance, "PRE_WRITE")
            before = tree_snapshot(instance)
            second = self.create_request(instance, "PRE_WRITE")
            self.assertEqual(first["result"], "CREATED_GOVERNANCE_REQUEST")
            self.assertEqual(second["result"], "EXISTING_GOVERNANCE_REQUEST")
            self.assertTrue(second["no_op"])
            self.assertEqual(before, tree_snapshot(instance))

    def test_blocked_or_inconclusive_receipt_creates_one_immutable_retry_request(self) -> None:
        for result in ("BLOCKED", "INCONCLUSIVE"):
            with self.subTest(result=result), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                _state, instance = self.create_instance(root, task_id=f"task-f104-retry-{result.lower()}")
                first = self.create_request(instance, "PRE_WRITE")["request"]
                receipt_id = f"cleanliness-{result.lower()}"
                receipt_path = self.write_receipt(
                    root,
                    self.make_receipt(first, result=result, receipt_id=receipt_id),
                    f"{result.lower()}-receipt.json",
                )
                recorded = planning.record_cleanliness_receipt(instance, receipt_path, apply=True)
                self.assertEqual(recorded["result"], "RECORDED_CLEANLINESS_RECEIPT", recorded)

                retry_preview = planning.create_governance_request(instance, "PRE_WRITE", "P01", preview=True)
                self.assertEqual(retry_preview["result"], "PREVIEW", retry_preview)
                retry = retry_preview["request"]
                self.assertNotEqual(retry["request_id"], first["request_id"])
                self.assertEqual(retry["retry_sequence"], 1)
                self.assertEqual(retry["retry_of_request_id"], first["request_id"])
                self.assertEqual(retry["retry_trigger_receipt_id"], receipt_id)

                applied = planning.create_governance_request(instance, "PRE_WRITE", "P01", apply=True)
                self.assertEqual(applied["result"], "CREATED_GOVERNANCE_REQUEST", applied)
                self.assertEqual(applied["request_id"], retry["request_id"])
                repeated = planning.create_governance_request(instance, "PRE_WRITE", "P01", apply=True)
                self.assertEqual(repeated["result"], "EXISTING_GOVERNANCE_REQUEST", repeated)
                self.assertEqual(repeated["request_id"], retry["request_id"])
                self.assertTrue((instance / "governance" / "requests" / f"{first['request_id']}.json").is_file())
                self.assertTrue((instance / "governance" / "receipts" / f"{receipt_id}.json").is_file())

    def test_same_request_id_with_different_content_is_a_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            result = self.create_request(instance, "PRE_WRITE")
            path = Path(result["request_path"])
            request = json.loads(path.read_text(encoding="utf-8"))
            request["expected_changes"].append("tampered")
            path.write_bytes((contracts.stable_json(request)).encode("utf-8"))
            conflict = planning.create_governance_request(instance, "PRE_WRITE", "P01", apply=True)
            self.assertEqual(conflict["result"], "CONFLICT")
            self.assertEqual(conflict["error_code"], "GOVERNANCE_REQUEST_CONFLICT")

    def test_final_governance_gate_pairs_receipt_by_request_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            self.create_request(instance, "PRE_WRITE")

            checklist_path = instance / "WORKFLOW_CHECKLIST.md"
            checklist = self.checklist_metadata(instance)
            checklist["known_dirty_paths"] = ["retry-marker.txt"]
            checklist_path.write_text(
                workflow.replace_machine_json(
                    checklist_path.read_text(encoding="utf-8"),
                    "workflow",
                    checklist,
                ),
                encoding="utf-8",
            )
            self.create_request(instance, "PRE_WRITE")

            requests = planning._load_governance_requests(instance)
            ordered = sorted(requests, key=lambda item: str(item["request_id"]))
            self.assertGreaterEqual(len(ordered), 2)
            current_request = ordered[-1]
            historical_request = ordered[0]
            # Pin event order so this test does not depend on a one-second
            # timestamp rollover between request creation calls.
            historical_request["requested_at"] = "2026-07-17T00:00:01Z"
            current_request["requested_at"] = "2026-07-17T00:00:02Z"
            requests_dir = instance / "governance" / "requests"
            for request in (current_request, historical_request):
                (requests_dir / f"{request['request_id']}.json").write_text(
                    contracts.stable_json(request), encoding="utf-8"
                )
            current_receipt = self.make_receipt(current_request, receipt_id="aa-current")
            historical_receipt = self.make_receipt(
                historical_request,
                result="BLOCKED",
                receipt_id="zz-historical-blocked",
            )
            (instance / "governance" / "receipts").mkdir(parents=True)
            (instance / "governance" / "receipts" / "aa-current.json").write_text(
                contracts.stable_json(current_receipt), encoding="utf-8"
            )
            (instance / "governance" / "receipts" / "zz-historical-blocked.json").write_text(
                contracts.stable_json(historical_receipt), encoding="utf-8"
            )

            envelope = json.loads((instance / "task-envelope.json").read_text(encoding="utf-8"))
            plan = json.loads((instance / "plan-package.json").read_text(encoding="utf-8"))
            blocking, waiting, warnings, evidence = planning._final_governance_gate(
                instance,
                envelope,
                plan,
                checklist_path.read_text(encoding="utf-8"),
                {"require_cleanliness_receipts": True, "required_governance_stages": ["PRE_WRITE"]},
                "ADVANCED",
            )
            self.assertEqual(blocking, [])
            self.assertEqual(waiting, [])
            self.assertEqual(warnings, [])
            self.assertIn("governance/receipts/aa-current.json", evidence)

    def test_final_governance_gate_uses_request_event_time_not_filename_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            self.create_request(instance, "PRE_WRITE")

            checklist_path = instance / "WORKFLOW_CHECKLIST.md"
            checklist = self.checklist_metadata(instance)
            checklist["known_dirty_paths"] = ["retry-marker.txt"]
            checklist_path.write_text(
                workflow.replace_machine_json(
                    checklist_path.read_text(encoding="utf-8"),
                    "workflow",
                    checklist,
                ),
                encoding="utf-8",
            )
            self.create_request(instance, "PRE_WRITE")

            requests = planning._load_governance_requests(instance)
            self.assertEqual(len(requests), 2)
            request_by_id = {str(item["request_id"]): item for item in requests}
            filename_last = max(requests, key=lambda item: str(item["request_id"]))
            current = next(item for item in requests if item is not filename_last)
            historical = filename_last
            current["requested_at"] = "2026-07-17T00:00:02Z"
            historical["requested_at"] = "2026-07-17T00:00:01Z"
            requests_dir = instance / "governance" / "requests"
            for request in (current, historical):
                (requests_dir / f"{request['request_id']}.json").write_text(
                    contracts.stable_json(request), encoding="utf-8"
                )

            receipts_dir = instance / "governance" / "receipts"
            receipts_dir.mkdir(parents=True)
            current_receipt = self.make_receipt(current, receipt_id="current-event-pass")
            historical_receipt = self.make_receipt(
                historical,
                result="BLOCKED",
                receipt_id="historical-filename-last-blocked",
            )
            (receipts_dir / "current-event-pass.json").write_text(
                contracts.stable_json(current_receipt), encoding="utf-8"
            )
            (receipts_dir / "historical-filename-last-blocked.json").write_text(
                contracts.stable_json(historical_receipt), encoding="utf-8"
            )

            envelope = json.loads((instance / "task-envelope.json").read_text(encoding="utf-8"))
            plan = json.loads((instance / "plan-package.json").read_text(encoding="utf-8"))
            blocking, waiting, warnings, evidence = planning._final_governance_gate(
                instance,
                envelope,
                plan,
                checklist_path.read_text(encoding="utf-8"),
                {"require_cleanliness_receipts": True, "required_governance_stages": ["PRE_WRITE"]},
                "ADVANCED",
            )
            self.assertEqual(blocking, [])
            self.assertEqual(waiting, [])
            self.assertEqual(warnings, [])
            self.assertIn("governance/receipts/current-event-pass.json", evidence)
            self.assertNotIn("governance/receipts/historical-filename-last-blocked.json", evidence)

    def test_final_governance_gate_missing_matching_receipt_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            request = self.create_request(instance, "PRE_WRITE")["request"]
            other_request = copy.deepcopy(request)
            other_request["request_id"] = "request-for-another-attempt"
            other_receipt = self.make_receipt(other_request, receipt_id="only-other-request")
            (instance / "governance" / "receipts").mkdir(parents=True)
            (instance / "governance" / "receipts" / "only-other-request.json").write_text(
                contracts.stable_json(other_receipt), encoding="utf-8"
            )
            envelope = json.loads((instance / "task-envelope.json").read_text(encoding="utf-8"))
            plan = json.loads((instance / "plan-package.json").read_text(encoding="utf-8"))
            blocking, _waiting, _warnings, _evidence = planning._final_governance_gate(
                instance,
                envelope,
                plan,
                (instance / "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8"),
                {"require_cleanliness_receipts": True, "required_governance_stages": ["PRE_WRITE"]},
                "ADVANCED",
            )
            self.assertIn("governance:PRE_WRITE:CLEANLINESS_RECEIPT_MISMATCH", blocking)

    def test_invalid_stage_is_rejected_without_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            result = planning.create_governance_request(instance, "NOT_A_STAGE", "P01", apply=True)
            self.assertEqual(result["result"], "FAILED")
            self.assertEqual(result["error_code"], "INVALID_GOVERNANCE_STAGE")
            self.assertFalse((instance / "governance").exists())

    def test_task_profile_stage_defaults_are_respected(self) -> None:
        cases = (
            ("task-f104-light", "LOW", "P2", ["POST_WRITE"]),
            ("task-f104-standard", "MEDIUM", "P1", ["PRE_WRITE", "POST_WRITE"]),
            ("task-f104-full", "LOW", "P0", ["PRE_WRITE", "POST_WRITE", "PRE_CLOSE"]),
            ("task-f104-risk", "CRITICAL", "P2", ["PRE_WRITE", "POST_WRITE", "PRE_CLOSE"]),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for task_id, risk, priority, stages in cases:
                _state, instance = self.create_instance(root, task_id, risk_level=risk, priority=priority)
                plan = json.loads((instance / "plan-package.json").read_text(encoding="utf-8"))
                self.assertEqual(plan["governance_policy"]["required_stages"], stages)
                for stage in stages:
                    result = planning.create_governance_request(instance, stage, "P01", preview=True)
                    self.assertEqual(result["result"], "PREVIEW")
                rejected = planning.create_governance_request(instance, "PRE_WRITE", "P01", preview=True)
                if "PRE_WRITE" not in stages:
                    self.assertEqual(rejected["error_code"], "GOVERNANCE_STAGE_NOT_REQUIRED")

    def test_explicit_plan_policy_overrides_profile_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root, risk_level="LOW", priority="P2")
            self.set_required_stages(instance, ["PRE_CLOSE"])
            self.assertEqual(planning.create_governance_request(instance, "PRE_CLOSE", "P01", preview=True)["result"], "PREVIEW")
            result = planning.create_governance_request(instance, "POST_WRITE", "P01", preview=True)
            self.assertEqual(result["error_code"], "GOVERNANCE_STAGE_NOT_REQUIRED")

    def test_explicit_finalization_preclose_is_available_for_standard_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(
                root,
                risk_level="MEDIUM",
                priority="P1",
                finalization_policy={
                    "mode": "ADVANCED",
                    "required_governance_stages": ["PRE_CLOSE"],
                },
            )
            plan = json.loads((instance / "plan-package.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["task_profile"], "STANDARD")
            self.assertEqual(
                planning.create_governance_request(instance, "PRE_CLOSE", "P01", preview=True)["result"],
                "PREVIEW",
            )

    def test_request_uses_packet_references_and_does_not_scan_unknown_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            packet = self.create_packet(instance)
            (instance / "unrecorded-dirty-file.txt").write_bytes(("not a declared dirty path").encode("utf-8"))
            result = self.create_request(instance, "PRE_WRITE")
            request = result["request"]
            self.assertIn(f"packets/{packet['packet_id']}.json", request["packet_refs"])
            self.assertIn(f"packet:{packet['packet_id']}", request["expected_changes"])
            self.assertNotIn("unrecorded-dirty-file.txt", request["known_dirty_paths"])

    def test_request_cli_is_structured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "planning.py"),
                    "create-governance-request",
                    "--instance-root",
                    str(instance),
                    "--stage",
                    "PRE_WRITE",
                    "--phase-id",
                    "P01",
                    "--preview",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["result"], "PREVIEW")

    def test_receipt_requires_existing_request_and_matching_references(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            missing = self.write_receipt(root, {
                **self.make_receipt({"request_id": "missing", "task_id": "task-f104", "plan_id": "plan-missing", "phase_id": "P01", "governance_stage": "PRE_WRITE"}),
            })
            result = planning.record_cleanliness_receipt(instance, missing, apply=True)
            self.assertEqual(result["error_code"], "GOVERNANCE_REQUEST_NOT_FOUND")

            request = self.create_request(instance, "PRE_WRITE")["request"]
            mismatch = self.make_receipt(request, receipt_id="cleanliness-mismatch")
            mismatch["task_id"] = "task-other"
            path = self.write_receipt(root, mismatch, "mismatch.json")
            before = tree_snapshot(instance)
            result = planning.record_cleanliness_receipt(instance, path, apply=True)
            self.assertEqual(result["error_code"], "CLEANLINESS_RECEIPT_MISMATCH")
            self.assertEqual(before, tree_snapshot(instance))

    def test_receipt_preview_is_zero_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            request = self.create_request(instance, "PRE_WRITE")["request"]
            receipt = self.make_receipt(request)
            receipt_path = self.write_receipt(root, receipt)
            before = tree_snapshot(instance)
            result = planning.record_cleanliness_receipt(instance, receipt_path, preview=True)
            self.assertEqual(result["result"], "PREVIEW")
            self.assertEqual(before, tree_snapshot(instance))
            self.assertFalse((instance / "governance" / "receipts").exists())

    def test_pass_receipt_updates_checklist_and_keeps_plan_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            request = self.create_request(instance, "PRE_WRITE")["request"]
            receipt = self.make_receipt(request)
            receipt_path = self.write_receipt(root, receipt)
            plan_before = (instance / "plan-package.json").read_bytes()
            result = planning.record_cleanliness_receipt(instance, receipt_path, apply=True)
            self.assertEqual(result["result"], "RECORDED_CLEANLINESS_RECEIPT")
            stored_path = instance / "governance" / "receipts" / "cleanliness-receipt-01.json"
            self.assertTrue(stored_path.is_file())
            self.assertEqual(plan_before, (instance / "plan-package.json").read_bytes())
            metadata = self.checklist_metadata(instance)
            self.assertEqual(metadata["governance_status"], "PASS")
            self.assertTrue(metadata["governance_can_progress"])
            self.assertIn("governance/receipts/cleanliness-receipt-01.json", metadata["governance_receipt_refs"])
            self.assertIn("fixture/f1-04-evidence.json", metadata["evidence_refs"])
            self.assertFalse((instance / "receipts" / "cleanliness-receipt-01.json").exists())

    def test_pass_with_warnings_retains_warning_and_allows_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            request = self.create_request(instance, "PRE_WRITE")["request"]
            receipt = self.make_receipt(request, result="PASS_WITH_WARNINGS")
            result = planning.record_cleanliness_receipt(instance, self.write_receipt(root, receipt), apply=True)
            self.assertTrue(result["decision"]["can_progress"])
            metadata = self.checklist_metadata(instance)
            self.assertEqual(metadata["governance_status"], "PASS_WITH_WARNINGS")
            self.assertIn("temporary artifact reported", metadata["non_blocking_findings"])
            self.assertIn("temporary artifact reported", (instance / "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8"))

    def test_blocked_receipt_stops_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            request = self.create_request(instance, "PRE_WRITE")["request"]
            receipt = self.make_receipt(request, result="BLOCKED")
            result = planning.record_cleanliness_receipt(instance, self.write_receipt(root, receipt), apply=True)
            self.assertFalse(result["decision"]["can_progress"])
            metadata = self.checklist_metadata(instance)
            self.assertEqual(metadata["overall_status"], "阻塞")
            self.assertEqual(metadata["human_execution_gate"], "BLOCKED_BY_GOVERNANCE")
            self.assertIn("protected asset changed", metadata["blocking_findings"])

    def test_successful_same_stage_retry_clears_stale_governance_block(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            first_request = self.create_request(instance, "PRE_WRITE")["request"]
            blocked = self.make_receipt(first_request, result="BLOCKED", receipt_id="cleanliness-blocked")
            self.assertEqual(
                planning.record_cleanliness_receipt(instance, self.write_receipt(root, blocked), apply=True)["result"],
                "RECORDED_CLEANLINESS_RECEIPT",
            )

            checklist_path = instance / "WORKFLOW_CHECKLIST.md"
            checklist = self.checklist_metadata(instance)
            checklist["known_dirty_paths"] = ["retry-marker.txt"]
            checklist_path.write_text(
                workflow.replace_machine_json(
                    checklist_path.read_text(encoding="utf-8"),
                    "workflow",
                    checklist,
                ),
                encoding="utf-8",
            )
            second_request = self.create_request(instance, "PRE_WRITE")["request"]
            second_blocked = self.make_receipt(second_request, result="BLOCKED", receipt_id="cleanliness-blocked-second")
            self.assertEqual(
                planning.record_cleanliness_receipt(instance, self.write_receipt(root, second_blocked, "second-blocked.json"), apply=True)["result"],
                "RECORDED_CLEANLINESS_RECEIPT",
            )

            checklist = self.checklist_metadata(instance)
            checklist["known_dirty_paths"] = ["pass-marker.txt"]
            checklist_path.write_text(
                workflow.replace_machine_json(
                    checklist_path.read_text(encoding="utf-8"),
                    "workflow",
                    checklist,
                ),
                encoding="utf-8",
            )
            retry_request = self.create_request(instance, "PRE_WRITE")["request"]
            passed = self.make_receipt(retry_request, receipt_id="cleanliness-retry-pass")
            result = planning.record_cleanliness_receipt(instance, self.write_receipt(root, passed, "retry.json"), apply=True)
            self.assertEqual(result["result"], "RECORDED_CLEANLINESS_RECEIPT")
            metadata = self.checklist_metadata(instance)
            self.assertEqual(metadata["governance_status"], "PASS")
            self.assertEqual(metadata["blocking_findings"], [])
            self.assertEqual(metadata["overall_status"], "进行中")
            self.assertEqual(metadata["human_execution_gate"], "OPEN")
            self.assertEqual(metadata["governance_blocking_findings"], [])

    def test_inconclusive_receipt_transfers_to_human_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            request = self.create_request(instance, "PRE_WRITE")["request"]
            receipt = self.make_receipt(request, result="INCONCLUSIVE")
            result = planning.record_cleanliness_receipt(instance, self.write_receipt(root, receipt), apply=True)
            self.assertTrue(result["decision"]["requires_human_gate"])
            metadata = self.checklist_metadata(instance)
            self.assertEqual(metadata["verification_status"], "待人工裁决")
            self.assertEqual(metadata["human_execution_gate"], "WAITING_FOR_OWNER_F1-04")

    def test_duplicate_cleanliness_receipt_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            request = self.create_request(instance, "PRE_WRITE")["request"]
            path = self.write_receipt(root, self.make_receipt(request))
            first = planning.record_cleanliness_receipt(instance, path, apply=True)
            before = tree_snapshot(instance)
            second = planning.record_cleanliness_receipt(instance, path, apply=True)
            self.assertEqual(first["result"], "RECORDED_CLEANLINESS_RECEIPT")
            self.assertEqual(second["result"], "EXISTING_CLEANLINESS_RECEIPT")
            self.assertEqual(before, tree_snapshot(instance))

    def test_same_receipt_id_with_different_content_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            request = self.create_request(instance, "PRE_WRITE")["request"]
            first = self.make_receipt(request)
            self.assertEqual(planning.record_cleanliness_receipt(instance, self.write_receipt(root, first), apply=True)["result"], "RECORDED_CLEANLINESS_RECEIPT")
            changed = copy.deepcopy(first)
            changed["cleanliness_status"] = "TAMPERED"
            before = tree_snapshot(instance)
            result = planning.record_cleanliness_receipt(instance, self.write_receipt(root, changed, "changed.json"), apply=True)
            self.assertEqual(result["error_code"], "CLEANLINESS_RECEIPT_ID_CONFLICT")
            self.assertEqual(before, tree_snapshot(instance))

    def test_different_receipt_id_with_same_request_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            request = self.create_request(instance, "PRE_WRITE")["request"]
            first = self.make_receipt(request, receipt_id="cleanliness-first")
            planning.record_cleanliness_receipt(instance, self.write_receipt(root, first), apply=True)
            second = self.make_receipt(request, result="PASS_WITH_WARNINGS", receipt_id="cleanliness-second")
            result = planning.record_cleanliness_receipt(instance, self.write_receipt(root, second, "second.json"), apply=True)
            self.assertEqual(result["error_code"], "CLEANLINESS_RECEIPT_CONFLICT")

    def test_atomic_receipt_failure_rolls_back_receipt_and_checklist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            request = self.create_request(instance, "PRE_WRITE")["request"]
            receipt_path = self.write_receipt(root, self.make_receipt(request, receipt_id="cleanliness-rollback"))
            before = tree_snapshot(instance)
            original_write = planning.workflow.atomic_write_text
            calls = 0

            def fail_on_second_write(target: Path, content: str) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected F1-04 transaction failure")
                original_write(target, content)

            with mock.patch.object(planning.workflow, "atomic_write_text", side_effect=fail_on_second_write):
                result = planning.record_cleanliness_receipt(instance, receipt_path, apply=True)
            self.assertEqual(result["result"], "FAILED")
            self.assertEqual(before, tree_snapshot(instance))
            self.assertEqual(list(instance.parent.glob(f".{instance.name}.f1-04-*")), [])

    def test_unknown_receipt_and_checklist_fields_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            checklist_path = instance / "WORKFLOW_CHECKLIST.md"
            text = checklist_path.read_text(encoding="utf-8")
            metadata = workflow.extract_machine_json(text, "workflow")
            metadata["future_machine_field"] = {"keep": True}
            checklist_path.write_bytes((workflow.replace_machine_json(text, "workflow", metadata)).encode("utf-8"))
            request = self.create_request(instance, "PRE_WRITE")["request"]
            receipt = self.make_receipt(request)
            receipt["future_root"] = {"keep": True}
            receipt["protected_assets_status"]["future_nested"] = {"keep": "nested"}
            path = self.write_receipt(root, receipt)
            planning.record_cleanliness_receipt(instance, path, apply=True)
            stored = json.loads((instance / "governance" / "receipts" / "cleanliness-receipt-01.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["future_root"], {"keep": True})
            self.assertEqual(stored["protected_assets_status"]["future_nested"], {"keep": "nested"})
            self.assertEqual(self.checklist_metadata(instance)["future_machine_field"], {"keep": True})

    def test_candidate_categories_and_automatic_skill_delete_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            request = self.create_request(instance, "PRE_WRITE")["request"]
            invalid_category = self.make_receipt(request, receipt_id="invalid-category")
            invalid_category["duplicate_candidates"] = ["AUTO_DELETE"]
            result = planning.record_cleanliness_receipt(instance, self.write_receipt(root, invalid_category), apply=True)
            self.assertEqual(result["error_code"], "INVALID_CANDIDATE_CATEGORY")
            invalid_delete = self.make_receipt(request, receipt_id="invalid-delete")
            invalid_delete["cleanup_actions"] = ["自动删除 Skill planning-with-files"]
            result = planning.record_cleanliness_receipt(instance, self.write_receipt(root, invalid_delete, "delete.json"), apply=True)
            self.assertEqual(result["error_code"], "FORBIDDEN_AUTOMATIC_DELETE")

    def test_execution_and_cleanliness_receipts_are_directory_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            self.create_packet(instance)
            request = self.create_request(instance, "PRE_WRITE")["request"]
            receipt = self.make_receipt(request)
            planning.record_cleanliness_receipt(instance, self.write_receipt(root, receipt), apply=True)
            self.assertTrue((instance / "receipts" / "create-plan.json").is_file())
            self.assertTrue((instance / "governance" / "receipts" / "cleanliness-receipt-01.json").is_file())
            self.assertFalse((instance / "receipts" / "cleanliness-receipt-01.json").exists())

    def test_cleanliness_receipt_cli_is_structured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root)
            request = self.create_request(instance, "PRE_WRITE")["request"]
            path = self.write_receipt(root, self.make_receipt(request))
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "planning.py"),
                    "record-cleanliness-receipt",
                    "--instance-root",
                    str(instance),
                    "--receipt",
                    str(path),
                    "--preview",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["result"], "PREVIEW")

    def test_existing_f1_entrypoints_remain_available(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            envelope_path = self.write_json(root, "compat.json", self.envelope(task_id="task-f104-compat"))
            preview = planning.create_plan(envelope_path, state_root=root / "compat-state", preview=True)
            self.assertEqual(preview["result"], "PREVIEW")
            _state, instance = self.create_instance(root, "task-f104-entrypoint")
            packet = planning.create_packet(instance, "P01", "P01", preview=True)
            self.assertEqual(packet["result"], "PREVIEW")


if __name__ == "__main__":
    unittest.main()
