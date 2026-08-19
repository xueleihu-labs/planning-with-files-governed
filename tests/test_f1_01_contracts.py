#!/usr/bin/env python3
"""F1-01 deterministic PLAN contract and compatibility tests."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import plan_contracts as plan  # noqa: E402
import workflow_contracts as workflow  # noqa: E402


FIXTURE = ROOT / "tests" / "fixtures" / "f1-01" / "valid_contracts.json"
LEGACY_CHECKLIST = ROOT / "tests" / "fixtures" / "workflow-v080" / "projects" / "bound-old-template-version" / "WORKFLOW_CHECKLIST.md"


class F101ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixtures = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_schema_is_valid_and_declares_all_contract_kinds(self) -> None:
        schema = json.loads((ROOT / "schemas" / "plan-contracts.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(schema["$id"], "planning-with-files/plan-contracts.schema.json")
        self.assertEqual(set(schema["$defs"]), set(plan.CONTRACT_KINDS) | {"id", "version", "timestamp", "scope", "knowledge_policy", "plan_extension"})
        self.assertEqual(len(schema["oneOf"]), len(plan.CONTRACT_KINDS) + 1)
        routing = schema["$defs"]["routing_decision"]
        self.assertIn("content_judgment", routing["properties"])
        self.assertEqual(
            routing["properties"]["content_judgment"]["required"],
            ["status", "reason_code", "reason", "evidence_ref", "decided_by"],
        )

    def test_all_nine_contract_categories_have_valid_samples(self) -> None:
        for kind in plan.CONTRACT_KINDS:
            with self.subTest(kind=kind):
                plan.validate_contract(kind, self.fixtures[kind])

    def test_contract_field_counts_are_frozen(self) -> None:
        self.assertEqual(plan.contract_field_count("task_envelope"), 22)
        self.assertEqual(plan.contract_field_count("plan_package"), 26)
        self.assertEqual(plan.contract_field_count("execution_packet"), 24)
        self.assertEqual(plan.contract_field_count("execution_receipt"), 23)
        self.assertEqual(plan.contract_field_count("governance_request"), 17)
        self.assertEqual(plan.contract_field_count("governance_receipt"), 19)
        self.assertEqual(plan.contract_field_count("capability_ref"), 12)
        self.assertEqual(plan.contract_field_count("checkpoint_ref"), 11)
        self.assertEqual(plan.contract_field_count("knowledge_handoff"), 5)

    def test_required_fields_and_enums_are_rejected(self) -> None:
        missing = copy.deepcopy(self.fixtures["task_envelope"])
        del missing["objective"]
        with self.assertRaises(workflow.ContractError):
            plan.validate_contract("task_envelope", missing)

        invalid = copy.deepcopy(self.fixtures["capability_ref"])
        invalid["compatibility_status"] = "AUTO"
        with self.assertRaises(workflow.ContractError):
            plan.validate_contract("capability_ref", invalid)

        invalid_gate = copy.deepcopy(self.fixtures["condition"])
        invalid_gate["condition_type"] = "UNKNOWN"
        with self.assertRaises(workflow.ContractError):
            plan.validate_contract("condition", invalid_gate)

    def test_unknown_root_and_nested_fields_are_preserved(self) -> None:
        original = copy.deepcopy(self.fixtures["plan_package"])
        original["future_root"] = {"enabled": True}
        original["status_summary"]["future_nested"] = {"owner": "future"}
        original["phases"][0]["future_phase"] = {"keep": True}
        updates = {"status_summary": {"status": "UPDATED"}}
        updates["phases"] = [{"phase_id": "F1-01", "objective": "updated"}]
        merged = plan.validate_and_merge("plan_package", original, updates)
        self.assertEqual(merged["future_root"], {"enabled": True})
        self.assertEqual(merged["status_summary"]["future_nested"], {"owner": "future"})
        self.assertEqual(merged["status_summary"]["status"], "UPDATED")
        self.assertEqual(merged["phases"][0]["future_phase"], {"keep": True})

    def test_stable_json_and_digest_are_deterministic(self) -> None:
        value = {"中文": "值", "b": 2, "a": [1, 2]}
        reordered = {"a": [1, 2], "中文": "值", "b": 2}
        self.assertEqual(plan.stable_json(value), plan.stable_json(reordered))
        self.assertEqual(plan.contract_digest(value), plan.contract_digest(reordered))

    def test_legacy_v080_checklist_is_readable_without_writeback(self) -> None:
        text = LEGACY_CHECKLIST.read_text(encoding="utf-8")
        before = text
        result = plan.read_workflow_compatibility(text)
        self.assertEqual(result["compatibility_status"], plan.LEGACY_COMPATIBLE)
        self.assertFalse(result["writeback_required"])
        self.assertEqual(result["effective_extension"]["task_envelope"], None)
        self.assertEqual(result["effective_extension"]["execution_packets"], [])
        self.assertEqual(text, before)
        self.assertNotIn(plan.PLAN_EXTENSION_KEY, workflow.extract_machine_json(text, "workflow"))

    def test_current_plan_extension_is_optional_but_validated(self) -> None:
        extension = {
            "schema_version": 1,
            "task_envelope": self.fixtures["task_envelope"],
            "plan_package": self.fixtures["plan_package"],
            "execution_packets": [self.fixtures["execution_packet"]],
            "execution_receipts": [],
            "future_extension": {"keep": True},
        }
        plan.validate_plan_extension(extension)
        self.assertEqual(plan.read_workflow_compatibility({
            "workflow_schema_version": 1,
            "project_id": "demo-project",
            "checklist_version": "1.0.0",
            "template": {"template_id": "generic-project", "template_version": "1.0.0", "template_digest": "a" * 64},
            "modules": [],
            "current_phase": "P01",
            "overall_status": "未开始",
            "owner_agent": "Codex",
            "last_updated_at": "2026-07-16T13:00:00+08:00",
            "plan_contracts": extension,
        })["compatibility_status"], plan.CURRENT_CONTRACT)

    def test_new_task_bundle_requires_matching_references(self) -> None:
        bundle = {
            "plan_contracts": {
                "schema_version": 1,
                "task_envelope": self.fixtures["task_envelope"],
                "plan_package": self.fixtures["plan_package"],
                "execution_packets": [self.fixtures["execution_packet"]],
                "execution_receipts": [self.fixtures["execution_receipt"]],
            }
        }
        plan.validate_new_task_bundle(bundle)
        broken = copy.deepcopy(bundle)
        broken["plan_contracts"]["execution_packets"][0]["plan_version"] = "2.0.0"
        with self.assertRaises(workflow.ContractError):
            plan.validate_new_task_bundle(broken)

    def test_unregistered_or_incompatible_capability_requires_manual_selection(self) -> None:
        compatible = copy.deepcopy(self.fixtures["capability_ref"])
        self.assertEqual(plan.capability_invocation_mode(compatible), "AUTO_ALLOWED")
        for status in ("COMPATIBLE_WITH_WARNINGS", "INCOMPATIBLE", "UNREGISTERED", "UNCONFIRMED"):
            candidate = copy.deepcopy(compatible)
            candidate["compatibility_status"] = status
            self.assertEqual(plan.capability_invocation_mode(candidate), "MANUAL_SELECTION_REQUIRED")

    def test_checkpoint_and_handoff_are_references_not_embedded_content(self) -> None:
        checkpoint = copy.deepcopy(self.fixtures["checkpoint_ref"])
        checkpoint["full_history"] = "large history"
        with self.assertRaises(workflow.ContractError):
            plan.validate_checkpoint_ref(checkpoint)

        handoff = copy.deepcopy(self.fixtures["knowledge_handoff"])
        handoff["body"] = "传播正文"
        with self.assertRaises(workflow.ContractError):
            plan.validate_knowledge_handoff(handoff)

    def test_governance_results_control_progress(self) -> None:
        receipt = copy.deepcopy(self.fixtures["governance_receipt"])
        self.assertEqual(plan.governance_decision(receipt)["can_progress"], True)
        receipt["result"] = "BLOCKED"
        self.assertEqual(plan.governance_decision(receipt)["can_progress"], False)
        receipt["result"] = "INCONCLUSIVE"
        self.assertTrue(plan.governance_decision(receipt)["requires_human_gate"])
        receipt["result"] = "PASS_WITH_WARNINGS"
        receipt["evidence_refs"] = []
        self.assertFalse(plan.governance_decision(receipt)["can_progress"])

    def test_high_risk_user_gate_cannot_be_waived(self) -> None:
        condition = copy.deepcopy(self.fixtures["condition"])
        condition.update({"condition_type": "USER_GATE", "risk_level": "CRITICAL", "status": "WAIVED"})
        with self.assertRaises(workflow.ContractError):
            plan.validate_condition(condition)

    def test_execution_receipt_idempotency_ignores_identity_and_timestamps(self) -> None:
        first = copy.deepcopy(self.fixtures["execution_receipt"])
        second = copy.deepcopy(first)
        second["receipt_id"] = "receipt-execution-f101-retry"
        second["started_at"] = "2026-07-16T14:00:00+08:00"
        second["completed_at"] = "2026-07-16T14:01:00+08:00"
        self.assertTrue(plan.receipts_are_idempotent(first, second))
        second["summary"] = "不同结果"
        self.assertFalse(plan.receipts_are_idempotent(first, second))

    def test_version_classification_and_single_bump(self) -> None:
        base = {"status": "PENDING", "evidence_refs": []}
        self.assertEqual(plan.classify_version_change(base, {"status": "SATISFIED", "evidence_refs": []}), "PATCH")
        self.assertEqual(plan.classify_version_change(base, {"status": "PENDING", "evidence_refs": [], "phases": ["F1"]}), "MINOR")
        self.assertEqual(plan.classify_version_change({"task_id": "P01"}, {"task_id": "P02"}), "MAJOR")
        self.assertEqual(plan.classify_version_change(base, base, no_op=True), "NONE")
        self.assertEqual(plan.classify_version_change(base, base, preview=True), "NONE")
        self.assertEqual(plan.classify_version_change(base, base, baseline_rebuilt=True), "MAJOR")
        self.assertEqual(plan.next_version("1.2.3", "PATCH"), "1.2.4")
        self.assertEqual(plan.next_version("1.2.3", "MINOR"), "1.3.0")
        self.assertEqual(plan.next_version("1.2.3", "MAJOR"), "2.0.0")
        self.assertEqual(plan.next_version("1.2.3", "NONE"), "1.2.3")

    def test_schema_and_workflow_versions_remain_independent(self) -> None:
        self.assertEqual(plan.PLAN_CONTRACT_SCHEMA_VERSION, 1)
        self.assertEqual(plan.WORKFLOW_SCHEMA_VERSION, 1)
        self.assertEqual((ROOT / "VERSION").read_text(encoding="utf-8").strip(), "2.0.0rc3")

    def test_state_ownership_is_explicit_and_not_a_second_registry(self) -> None:
        self.assertEqual(plan.state_owner("plan"), "planning-with-files")
        self.assertEqual(plan.state_owner("checkpoint"), "phase-checkpoint-loop")
        self.assertEqual(plan.state_owner("cleanliness_result"), "全域洁癖")
        with self.assertRaises(workflow.ContractError):
            plan.state_owner("capability_registry_owned_by_planning")

    def test_temporary_contract_fixture_round_trip_does_not_touch_real_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            path.write_text(plan.stable_json(self.fixtures["task_envelope"]), encoding="utf-8")
            loaded = json.loads(path.read_text(encoding="utf-8"))
            plan.validate_contract("task_envelope", loaded)
        self.assertFalse(Path(directory).exists())


if __name__ == "__main__":
    unittest.main()
