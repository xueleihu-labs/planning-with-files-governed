#!/usr/bin/env python3
"""F1-06 deterministic outcome routing and local handoff receipt tests."""

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


class F106OutcomeTests(unittest.TestCase):
    def envelope(self, task_id: str, **updates: object) -> dict[str, object]:
        value = copy.deepcopy(BASE_ENVELOPE)
        value.update({
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
        })
        value.update(updates)
        return value

    def _write_json(self, path: Path, value: dict[str, object]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contracts.stable_json(value), encoding="utf-8")
        return path

    def create_instance(
        self,
        root: Path,
        task_id: str,
        *,
        evolution_policy: dict[str, object] | None = None,
        knowledge_policy: dict[str, object] | None = None,
        content_policy: dict[str, object] | None = None,
        envelope_extra: dict[str, object] | None = None,
    ) -> tuple[Path, Path]:
        envelope = self.envelope(task_id, content_policy=content_policy or {})
        if evolution_policy is not None:
            envelope["evolution_policy"] = copy.deepcopy(evolution_policy)
        if knowledge_policy is not None:
            envelope["knowledge_policy"] = copy.deepcopy(knowledge_policy)
        if envelope_extra:
            envelope.update(copy.deepcopy(envelope_extra))
        source = self._write_json(root / f"{task_id}.json", envelope)
        state = root / "state"
        created = planning.create_plan(source, state_root=state, apply=True)
        self.assertEqual(created["result"], "CREATED", created)
        instance = state / task_id

        stored_envelope = json.loads((instance / "task-envelope.json").read_text(encoding="utf-8"))
        stored_plan = json.loads((instance / "plan-package.json").read_text(encoding="utf-8"))
        stored_envelope["evolution_policy"] = copy.deepcopy(evolution_policy or {})
        stored_envelope["content_policy"] = copy.deepcopy(content_policy or {})
        if knowledge_policy is not None:
            stored_envelope["knowledge_policy"] = copy.deepcopy(knowledge_policy)
        if envelope_extra:
            stored_envelope.update(copy.deepcopy(envelope_extra))
        stored_plan["evolution_policy"] = copy.deepcopy(evolution_policy or {})
        stored_plan["content_policy"] = copy.deepcopy(content_policy or {})
        if knowledge_policy is not None:
            stored_plan["knowledge_policy"] = copy.deepcopy(knowledge_policy)
        self._write_json(instance / "task-envelope.json", stored_envelope)
        self._write_json(instance / "plan-package.json", stored_plan)
        return state, instance

    def route(self, instance: Path, *, apply: bool = True) -> dict[str, object]:
        return planning.evaluate_outcome_routing(
            instance,
            apply=apply,
            preview=not apply,
        )

    def test_contract_samples_and_field_counts(self) -> None:
        for kind in (
            "routing_decision",
            "evolution_signal",
            "evolution_receipt",
            "knowledge_handoff_package",
            "content_ingest_receipt",
        ):
            with self.subTest(kind=kind):
                contracts.validate_contract(kind, FIXTURES[kind])
                self.assertGreater(contracts.contract_field_count(kind), 0)

    def test_five_decisions_are_supported(self) -> None:
        for decision in contracts.OUTCOME_DECISIONS:
            self.assertIn(decision, {"NO_VALUE", "EVOLUTION_ONLY", "CONTENT_ONLY", "EVOLUTION_AND_CONTENT", "HUMAN_REVIEW_REQUIRED"})

    def test_no_value_routing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _state, instance = self.create_instance(Path(directory), "task-f106-no-value")
            result = self.route(instance)
            self.assertEqual(result["result"], "CREATED_OUTCOME_ROUTING")
            self.assertEqual(result["decision"], "NO_VALUE")
            self.assertFalse((instance / "outcomes" / "evolution" / "evolution-signal.json").exists())
            self.assertFalse((instance / "outcomes" / "content" / "knowledge_handoff.json").exists())

    def test_engineering_governance_emits_structured_not_applicable_judgment(self) -> None:
        knowledge = {
            "level": "BRIEF",
            "potential_value": "可复用的中期门契约兼容模式",
            "required_evidence": [],
            "required_images": [],
            "prohibited_content": [],
            "redaction_requirements": [],
            "ingest_required": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            _state, instance = self.create_instance(
                Path(directory),
                "task-f106-engineering-na",
                knowledge_policy=knowledge,
            )
            result = self.route(instance)
            judgment = result["routing_decision"]["content_judgment"]
            self.assertEqual(result["decision"], "NO_VALUE")
            self.assertFalse(result["routing_decision"]["content_required"])
            self.assertEqual(judgment["status"], "NOT_APPLICABLE")
            self.assertEqual(judgment["reason_code"], "ENGINEERING_GOVERNANCE_ONLY")
            self.assertIn(judgment["evidence_ref"], result["routing_decision"]["evidence_refs"])
            self.assertFalse((instance / "outcomes/content/knowledge_handoff.json").exists())

    def test_engineering_scope_scripts_path_does_not_trigger_content_marker(self) -> None:
        knowledge = {
            "level": "BRIEF",
            "potential_value": "可复用的运行时门禁契约模式",
            "required_evidence": ["专项测试结果", "全量回归结果"],
            "required_images": [],
            "prohibited_content": ["secret", "token", "password"],
            "redaction_requirements": [],
            "ingest_required": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            _state, instance = self.create_instance(
                Path(directory),
                "task-f106-engineering-scripts-path",
                knowledge_policy=knowledge,
                envelope_extra={
                    "title": "planning-with-files 运行时契约升级",
                    "objective": "修复正式验证门的动态状态消费并保持 fail-closed",
                    "scope": {
                        "include": [
                            "planning-with-files/schemas/plan-contracts.schema.json",
                            "planning-with-files/scripts/planning.py",
                            "planning-with-files/tests",
                        ],
                        "exclude": ["Git push", "LOCAL_PHASE_SEAL"],
                    },
                },
            )
            result = self.route(instance)
            self.assertEqual(result["decision"], "NO_VALUE")
            judgment = result["routing_decision"]["content_judgment"]
            self.assertEqual(judgment["status"], "NOT_APPLICABLE")
            self.assertEqual(judgment["reason_code"], "ENGINEERING_GOVERNANCE_ONLY")

    def test_missing_content_judgment_blocks_advanced_outcome_gate(self) -> None:
        knowledge = {
            "level": "BRIEF",
            "potential_value": "可复用的中期门契约兼容模式",
            "required_evidence": [],
            "required_images": [],
            "prohibited_content": [],
            "redaction_requirements": [],
            "ingest_required": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            state, instance = self.create_instance(
                Path(directory),
                "task-f106-missing-content-judgment",
                knowledge_policy=knowledge,
            )
            self.route(instance)
            route_path = instance / "outcomes" / "routing-decision.json"
            decision = json.loads(route_path.read_text(encoding="utf-8"))
            decision.pop("content_judgment")
            self._write_json(route_path, decision)
            envelope = json.loads((instance / "task-envelope.json").read_text(encoding="utf-8"))
            plan = json.loads((instance / "plan-package.json").read_text(encoding="utf-8"))
            blocking, _waiting, _warnings, _evidence = planning._final_outcome_gate(
                state,
                instance,
                envelope,
                plan,
                {"require_outcome_routing": True},
                "ADVANCED",
            )
            self.assertIn("outcomes:routing-decision:content_judgment_missing", blocking)

    def test_not_applicable_content_judgment_cannot_override_content_task(self) -> None:
        decision = copy.deepcopy(FIXTURES["routing_decision"])
        decision["content_required"] = True
        decision["content_judgment"] = {
            "status": "NOT_APPLICABLE",
            "reason_code": "ENGINEERING_GOVERNANCE_ONLY",
            "reason": "非法地将内容任务标记为不适用",
            "evidence_ref": "F1-01_ACCEPTANCE_REPORT.md",
            "decided_by": "planning-with-files",
        }
        with self.assertRaises(workflow.ContractError):
            contracts.validate_routing_decision(decision)

    def test_evolution_only_and_e0_failure_threshold(self) -> None:
        policy = {"failure_counts": {"same-failure": 2}, "cross_task_value": True}
        with tempfile.TemporaryDirectory() as directory:
            _state, instance = self.create_instance(Path(directory), "task-f106-evolution", evolution_policy=policy)
            result = self.route(instance)
            self.assertEqual(result["decision"], "EVOLUTION_ONLY")
            signal = json.loads((instance / "outcomes/evolution/evolution-signal.json").read_text(encoding="utf-8"))
            self.assertEqual(signal["e0_result"], "EVOLUTION_PROPOSAL")
            self.assertEqual(signal["failure_counts"]["same-failure"], 2)

    def test_evolution_correction_and_manual_action_thresholds(self) -> None:
        cases = (
            ({"correction_counts": {"same-rule": 2}, "cross_task_value": True}, "REPEAT_CORRECTION"),
            ({"manual_action_counts": {"same-operation": 3}, "cross_task_value": True}, "REPEAT_MANUAL_ACTION"),
        )
        for index, (policy, signal_type) in enumerate(cases):
            with self.subTest(signal_type=signal_type), tempfile.TemporaryDirectory() as directory:
                _state, instance = self.create_instance(Path(directory), f"task-f106-threshold-{index}", evolution_policy=policy)
                result = self.route(instance)
                self.assertEqual(result["decision"], "EVOLUTION_ONLY")
                signal = json.loads((instance / "outcomes/evolution/evolution-signal.json").read_text(encoding="utf-8"))
                self.assertIn(signal_type, signal["signal_types"])

    def test_skill_contract_and_automation_gaps_are_signals(self) -> None:
        policy = {
            "skill_gap_candidates": ["missing-capability"],
            "contract_gap_candidates": ["missing-receipt-field"],
            "automation_candidates": ["stable-local-check"],
            "cross_task_value": True,
        }
        with tempfile.TemporaryDirectory() as directory:
            _state, instance = self.create_instance(Path(directory), "task-f106-gaps", evolution_policy=policy)
            result = self.route(instance)
            self.assertEqual(result["decision"], "EVOLUTION_ONLY")
            signal = json.loads((instance / "outcomes/evolution/evolution-signal.json").read_text(encoding="utf-8"))
            self.assertIn("SKILL_GAP", signal["signal_types"])
            self.assertIn("CONTRACT_GAP", signal["signal_types"])
            self.assertIn("AUTOMATION_OPPORTUNITY", signal["signal_types"])

    def test_no_cross_task_value_returns_no_evolution(self) -> None:
        policy = {"skill_gap_candidates": ["private-only-gap"], "cross_task_value": False}
        with tempfile.TemporaryDirectory() as directory:
            _state, instance = self.create_instance(Path(directory), "task-f106-private-evolution", evolution_policy=policy)
            result = self.route(instance)
            self.assertEqual(result["decision"], "NO_VALUE")
            self.assertFalse((instance / "outcomes/evolution/evolution-signal.json").exists())

    def test_private_facts_are_excluded_from_signal(self) -> None:
        policy = {
            "reusable_rule_candidates": ["stable-rule"],
            "excluded_private_facts": ["customer name", "private path"],
            "cross_task_value": True,
        }
        with tempfile.TemporaryDirectory() as directory:
            _state, instance = self.create_instance(Path(directory), "task-f106-private-facts", evolution_policy=policy)
            self.route(instance)
            signal = json.loads((instance / "outcomes/evolution/evolution-signal.json").read_text(encoding="utf-8"))
            self.assertEqual(signal["excluded_private_facts"], ["customer name", "private path"])
            self.assertNotIn("customer name", signal["reusable_rule_candidates"])
            self.assertNotIn("private path", signal["evidence_refs"])

    def test_content_only_creates_handoff_without_platform_draft(self) -> None:
        knowledge = {
            "level": "BRIEF",
            "potential_value": "可复用的本地工作流经验",
            "required_evidence": [],
            "required_images": [],
            "prohibited_content": [],
            "redaction_requirements": [],
            "ingest_required": True,
        }
        content = {
            "content_title": "一次可复用的工作流经验",
            "content_summary": "展示如何用证据驱动阶段交接。",
            "core_value": "减少状态误判。",
            "reusable_knowledge": ["只消费结构化引用"],
            "project_specific_facts": ["本任务使用 v0.8.0"],
            "target_audience": ["AI workflow practitioners"],
            "recommended_platforms": ["XIAOHONGSHU", "WECHAT_OFFICIAL_ACCOUNT"],
            "content_angles": ["避坑指南"],
        }
        with tempfile.TemporaryDirectory() as directory:
            _state, instance = self.create_instance(Path(directory), "task-f106-content", knowledge_policy=knowledge, content_policy=content)
            result = self.route(instance)
            self.assertEqual(result["decision"], "CONTENT_ONLY")
            handoff = json.loads((instance / "outcomes/content/knowledge_handoff.json").read_text(encoding="utf-8"))
            contracts.validate_knowledge_handoff_package(handoff)
            self.assertEqual(result["routing_decision"]["content_judgment"]["status"], "REQUIRED")
            self.assertNotIn("draft", handoff)
            self.assertFalse((instance / "xhs").exists())
            self.assertFalse((instance / "wechat").exists())
            self.assertEqual(result["external_calls"], [])

    def test_sensitive_content_requires_human_review_and_blocks_handoff(self) -> None:
        knowledge = {
            "level": "FULL",
            "potential_value": "sensitive secret",
            "required_evidence": [],
            "required_images": [],
            "prohibited_content": [],
            "redaction_requirements": [],
            "ingest_required": True,
            "sensitive_content": True,
        }
        content = {"content_title": "敏感内容", "content_summary": "需人工复核", "core_value": "不可自动传播"}
        with tempfile.TemporaryDirectory() as directory:
            _state, instance = self.create_instance(Path(directory), "task-f106-sensitive", knowledge_policy=knowledge, content_policy=content)
            result = self.route(instance)
            self.assertEqual(result["decision"], "HUMAN_REVIEW_REQUIRED")
            self.assertTrue(result["routing_decision"]["human_review_required"])
            self.assertFalse((instance / "outcomes/content/knowledge_handoff.json").exists())
            metadata = workflow.extract_machine_json((instance / "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8"), "workflow")
            self.assertEqual(metadata["human_execution_gate"], "REQUIRED")

    def test_both_chains_are_independent(self) -> None:
        policy = {"reusable_rule_candidates": ["rule"], "cross_task_value": True}
        knowledge = {"level": "BRIEF", "potential_value": "内容价值", "required_evidence": [], "required_images": [], "prohibited_content": [], "redaction_requirements": [], "ingest_required": True}
        content = {"content_title": "双分流", "content_summary": "双链独立", "core_value": "分别跟踪"}
        with tempfile.TemporaryDirectory() as directory:
            _state, instance = self.create_instance(Path(directory), "task-f106-both", evolution_policy=policy, knowledge_policy=knowledge, content_policy=content)
            result = self.route(instance)
            self.assertEqual(result["decision"], "EVOLUTION_AND_CONTENT")
            self.assertTrue((instance / "outcomes/evolution/evolution-signal.json").exists())
            self.assertTrue((instance / "outcomes/content/knowledge_handoff.json").exists())

    def test_routing_id_is_deterministic(self) -> None:
        policy = {"reusable_rule_candidates": ["rule"], "cross_task_value": True}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state_a, instance_a = self.create_instance(root, "task-f106-deterministic-a", evolution_policy=policy)
            first = self.route(instance_a, apply=False)
            _state_b, instance_b = self.create_instance(root, "task-f106-deterministic-b", evolution_policy=policy)
            second = self.route(instance_b, apply=False)
            self.assertNotEqual(first["decision_id"], second["decision_id"])
            self.assertEqual(first["routing_decision"]["created_at"], second["routing_decision"]["created_at"])
            self.assertEqual(first["routing_decision"]["decision"], second["routing_decision"]["decision"])
            self.assertEqual(first["routing_decision"]["dedupe_key"] != second["routing_decision"]["dedupe_key"], True)

    def test_preview_is_zero_write_and_apply_is_idempotent(self) -> None:
        policy = {"reusable_rule_candidates": ["rule"], "cross_task_value": True}
        with tempfile.TemporaryDirectory() as directory:
            _state, instance = self.create_instance(Path(directory), "task-f106-idempotent", evolution_policy=policy)
            before = tree_snapshot(instance)
            preview = self.route(instance, apply=False)
            self.assertEqual(preview["result"], "PREVIEW")
            self.assertEqual(before, tree_snapshot(instance))
            first = self.route(instance)
            snapshot = tree_snapshot(instance)
            second = self.route(instance)
            self.assertEqual(first["result"], "CREATED_OUTCOME_ROUTING")
            self.assertEqual(second["result"], "EXISTING_ROUTING_DECISION")
            self.assertEqual(snapshot, tree_snapshot(instance))

    def test_same_decision_id_with_changed_content_is_conflict(self) -> None:
        policy = {"reusable_rule_candidates": ["rule"], "cross_task_value": True}
        with tempfile.TemporaryDirectory() as directory:
            _state, instance = self.create_instance(Path(directory), "task-f106-decision-conflict", evolution_policy=policy)
            self.route(instance)
            path = instance / "outcomes/routing-decision.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["decision"] = "NO_VALUE"
            self._write_json(path, value)
            result = self.route(instance)
            self.assertEqual(result["result"], "CONFLICT")
            self.assertEqual(result["error_code"], "OUTCOME_ROUTING_CONFLICT")

    def _evolution_receipt(self, instance: Path, *, receipt_id: str = "receipt-f106-evolution") -> dict[str, object]:
        signal = json.loads((instance / "outcomes/evolution/evolution-signal.json").read_text(encoding="utf-8"))
        receipt = copy.deepcopy(FIXTURES["evolution_receipt"])
        receipt.update({
            "receipt_id": receipt_id,
            "signal_id": signal["signal_id"],
            "dedupe_key": signal["dedupe_key"],
            "task_id": signal["task_id"],
            "plan_id": signal["plan_id"],
            "checkpoint_ref": "checkpoints/refs/cp-f106.json",
            "processed_at": "2026-07-17T00:00:00Z",
        })
        return receipt

    def _content_receipt(self, instance: Path, *, receipt_id: str = "receipt-f106-content") -> dict[str, object]:
        handoff = json.loads((instance / "outcomes/content/knowledge_handoff.json").read_text(encoding="utf-8"))
        receipt = copy.deepcopy(FIXTURES["content_ingest_receipt"])
        receipt.update({
            "receipt_id": receipt_id,
            "handoff_id": handoff["handoff_id"],
            "dedupe_key": handoff["dedupe_key"],
            "task_id": handoff["task_id"],
            "plan_id": handoff["plan_id"],
            "destination_path": str(Path(tempfile.gettempdir()) / "f106-publication" / "knowledge.json"),
            "ingested_at": "2026-07-17T00:00:00Z",
        })
        return receipt

    def test_evolution_receipt_is_validated_recorded_and_idempotent(self) -> None:
        policy = {"reusable_rule_candidates": ["rule"], "cross_task_value": True}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root, "task-f106-evolution-receipt", evolution_policy=policy)
            self.route(instance)
            receipt = self._evolution_receipt(instance)
            path = self._write_json(root / "evolution-receipt.json", receipt)
            preview = planning.record_evolution_receipt(instance, path, preview=True)
            self.assertEqual(preview["result"], "PREVIEW")
            recorded = planning.record_evolution_receipt(instance, path, apply=True)
            self.assertEqual(recorded["result"], "RECORDED_EVOLUTION_RECEIPT")
            repeated = planning.record_evolution_receipt(instance, path, apply=True)
            self.assertEqual(repeated["result"], "EXISTING_EVOLUTION_RECEIPT")
            stored = json.loads((instance / "outcomes/evolution/receipts/receipt-f106-evolution.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["receipt_id"], receipt["receipt_id"])
            metadata = workflow.extract_machine_json((instance / "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8"), "workflow")
            self.assertEqual(metadata["evolution_status"], "CANDIDATE_CREATED")

    def test_evolution_receipt_mismatch_is_rejected_without_write(self) -> None:
        policy = {"reusable_rule_candidates": ["rule"], "cross_task_value": True}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root, "task-f106-evolution-mismatch", evolution_policy=policy)
            self.route(instance)
            receipt = self._evolution_receipt(instance)
            receipt["dedupe_key"] = "wrong-dedupe"
            result = planning.record_evolution_receipt(instance, self._write_json(root / "bad-evolution.json", receipt), apply=True)
            self.assertEqual(result["error_code"], "EVOLUTION_RECEIPT_MISMATCH")
            self.assertFalse((instance / "outcomes/evolution/receipts").exists())

    def test_content_receipt_is_validated_recorded_and_independent(self) -> None:
        policy = {"reusable_rule_candidates": ["rule"], "cross_task_value": True}
        knowledge = {"level": "BRIEF", "potential_value": "content", "required_evidence": [], "required_images": [], "prohibited_content": [], "redaction_requirements": [], "ingest_required": True}
        content = {"content_title": "内容", "content_summary": "内容摘要", "core_value": "内容价值"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root, "task-f106-content-receipt", evolution_policy=policy, knowledge_policy=knowledge, content_policy=content)
            self.route(instance)
            receipt = self._content_receipt(instance)
            path = self._write_json(root / "content-receipt.json", receipt)
            self.assertEqual(planning.record_content_ingest_receipt(instance, path, preview=True)["result"], "PREVIEW")
            recorded = planning.record_content_ingest_receipt(instance, path, apply=True)
            self.assertEqual(recorded["result"], "RECORDED_CONTENT_INGEST_RECEIPT")
            repeated = planning.record_content_ingest_receipt(instance, path, apply=True)
            self.assertEqual(repeated["result"], "EXISTING_CONTENT_INGEST_RECEIPT")
            metadata = workflow.extract_machine_json((instance / "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8"), "workflow")
            self.assertEqual(metadata["content_status"], "INGESTED")
            self.assertEqual(metadata["evolution_status"], "READY_FOR_BRIDGE")

    def test_content_receipt_mismatch_and_destination_guard(self) -> None:
        knowledge = {"level": "BRIEF", "potential_value": "content", "required_evidence": [], "required_images": [], "prohibited_content": [], "redaction_requirements": [], "ingest_required": True}
        content = {"content_title": "内容", "content_summary": "内容摘要", "core_value": "内容价值"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root, "task-f106-content-guard", knowledge_policy=knowledge, content_policy=content)
            self.route(instance)
            receipt = self._content_receipt(instance)
            receipt["destination_path"] = str(instance / "second-knowledge-base.json")
            result = planning.record_content_ingest_receipt(instance, self._write_json(root / "bad-content.json", receipt), apply=True)
            self.assertEqual(result["error_code"], "CONTENT_DESTINATION_NOT_ALLOWED")
            receipt = self._content_receipt(instance, receipt_id="receipt-f106-content-wrong")
            receipt["handoff_id"] = "wrong-handoff"
            result = planning.record_content_ingest_receipt(instance, self._write_json(root / "mismatch-content.json", receipt), apply=True)
            self.assertEqual(result["error_code"], "CONTENT_RECEIPT_MISMATCH")

    def test_one_chain_failure_does_not_fake_other_success(self) -> None:
        policy = {"reusable_rule_candidates": ["rule"], "cross_task_value": True}
        knowledge = {"level": "BRIEF", "potential_value": "content", "required_evidence": [], "required_images": [], "prohibited_content": [], "redaction_requirements": [], "ingest_required": True}
        content = {"content_title": "内容", "content_summary": "内容摘要", "core_value": "内容价值"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root, "task-f106-independent-failure", evolution_policy=policy, knowledge_policy=knowledge, content_policy=content)
            self.route(instance)
            evolution = self._evolution_receipt(instance, receipt_id="receipt-f106-failed-evolution")
            evolution["result"] = "FAILED_RETRYABLE"
            self.assertEqual(planning.record_evolution_receipt(instance, self._write_json(root / "failed-evolution.json", evolution), apply=True)["result"], "RECORDED_EVOLUTION_RECEIPT")
            metadata = workflow.extract_machine_json((instance / "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8"), "workflow")
            self.assertEqual(metadata["evolution_status"], "FAILED_RETRYABLE")
            self.assertEqual(metadata["content_status"], "READY_FOR_INGEST")

    def test_unknown_root_and_nested_receipt_fields_are_preserved(self) -> None:
        policy = {"reusable_rule_candidates": ["rule"], "cross_task_value": True}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root, "task-f106-unknown", evolution_policy=policy)
            self.route(instance)
            receipt = self._evolution_receipt(instance, receipt_id="receipt-f106-unknown")
            receipt["future_root"] = {"keep": True}
            receipt["evidence_refs"] = [{"path": "fixture", "future_nested": {"keep": "yes"}}]
            result = planning.record_evolution_receipt(instance, self._write_json(root / "unknown.json", receipt), apply=True)
            self.assertEqual(result["result"], "RECORDED_EVOLUTION_RECEIPT")
            stored = json.loads((instance / "outcomes/evolution/receipts/receipt-f106-unknown.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["future_root"], {"keep": True})
            self.assertEqual(stored["evidence_refs"][0]["future_nested"], {"keep": "yes"})

    def test_atomic_write_failure_rolls_back_all_outcome_files(self) -> None:
        policy = {"reusable_rule_candidates": ["rule"], "cross_task_value": True}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root, "task-f106-rollback", evolution_policy=policy)
            before = tree_snapshot(instance)
            with mock.patch.object(planning.workflow, "atomic_write_text", side_effect=OSError("fixture write failure")):
                result = self.route(instance)
            self.assertEqual(result["result"], "FAILED")
            self.assertEqual(before, tree_snapshot(instance))
            self.assertFalse((instance / "outcomes").exists())

    def test_cli_entries_are_available_and_external_calls_are_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root, "task-f106-cli")
            completed = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "planning.py"), "evaluate-outcome-routing", "--instance-root", str(instance), "--preview"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            output = json.loads(completed.stdout)
            self.assertEqual(output["result"], "PREVIEW")
            self.assertEqual(output["external_calls"], [])

    def test_cli_receipt_entries_are_available(self) -> None:
        policy = {"reusable_rule_candidates": ["rule"], "cross_task_value": True}
        knowledge = {"level": "BRIEF", "potential_value": "content", "required_evidence": [], "required_images": [], "prohibited_content": [], "redaction_requirements": [], "ingest_required": True}
        content = {"content_title": "内容", "content_summary": "内容摘要", "core_value": "内容价值"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _state, instance = self.create_instance(root, "task-f106-cli-receipts", evolution_policy=policy, knowledge_policy=knowledge, content_policy=content)
            self.route(instance)
            evolution_path = self._write_json(root / "cli-evolution.json", self._evolution_receipt(instance, receipt_id="receipt-f106-cli-evolution"))
            evolution_run = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "planning.py"), "record-evolution-receipt", "--instance-root", str(instance), "--receipt", str(evolution_path), "--preview"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(evolution_run.returncode, 0, evolution_run.stderr)
            self.assertEqual(json.loads(evolution_run.stdout)["result"], "PREVIEW")

            content_path = self._write_json(root / "cli-content.json", self._content_receipt(instance, receipt_id="receipt-f106-cli-content"))
            content_run = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "planning.py"), "record-content-ingest-receipt", "--instance-root", str(instance), "--receipt", str(content_path), "--preview"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(content_run.returncode, 0, content_run.stderr)
            self.assertEqual(json.loads(content_run.stdout)["result"], "PREVIEW")

    def test_no_t021_or_platform_draft_is_generated(self) -> None:
        policy = {"reusable_rule_candidates": ["rule"], "cross_task_value": True}
        with tempfile.TemporaryDirectory() as directory:
            _state, instance = self.create_instance(Path(directory), "task-f106-boundary", evolution_policy=policy)
            self.route(instance)
            files = {path.name for path in instance.rglob("*") if path.is_file()}
            self.assertNotIn("T021", files)
            self.assertNotIn("knowledge_handoff.md", files)
            self.assertFalse(any("小红书" in str(path) or "公众号" in str(path) for path in instance.rglob("*")))


if __name__ == "__main__":
    unittest.main()
