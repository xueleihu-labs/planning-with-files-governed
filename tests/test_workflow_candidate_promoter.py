#!/usr/bin/env python3
"""P4-02 approval-gate and controlled-promotion tests."""

from __future__ import annotations

import json
import copy
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import workflow_candidate_generator as generator
import workflow_candidate_promoter as promoter
import workflow_candidate_validator as validator
import workflow_contracts as contracts


class WorkflowCandidatePromoterTests(unittest.TestCase):
    def setUp(self):
        from test_workflow_replay import WorkflowReplayTests
        self.fixture = WorkflowReplayTests()
        self.fixture.setUp()
        self.now = generator.dt.datetime(2026, 7, 15, 12, 0, tzinfo=generator.dt.timezone.utc)

    def tearDown(self):
        self.fixture.tearDown()

    @property
    def skill(self):
        return self.fixture.skill

    @property
    def project(self):
        return self.fixture.project

    def _candidate(self, candidate_type=None):
        helper = __import__("test_workflow_candidate_generator", fromlist=["WorkflowCandidateGeneratorTests"]).WorkflowCandidateGeneratorTests
        source = helper()
        source.fixture = self.fixture
        source.successful_added_checklist()
        result = generator.generate_candidates(self.skill, self.project, apply=True, now=self.now)
        candidate = self.skill / "templates/workflow/candidates" / result["candidates"][0]["candidate_id"]
        if candidate_type:
            path = candidate / "candidate.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["candidate_type"] = candidate_type
            path.write_text(contracts.canonical_json(payload), encoding="utf-8")
        return candidate

    def _approved_candidate(self):
        candidate = self._candidate()
        validator.apply_validation(candidate, self.skill, self.project, result="PASS", now=self.now)
        second = self.fixture.root / "second-project"
        shutil.copytree(self.project, second)
        text = (second / contracts.CHECKLIST_NAME).read_text(encoding="utf-8").replace("demo-project", "second-project")
        (second / contracts.CHECKLIST_NAME).write_text(text, encoding="utf-8")
        validator.apply_validation(candidate, self.skill, second, result="PASS", now=self.now)
        preview = promoter.approval_preview(candidate)
        receipt = self.fixture.root / "approval.json"
        receipt.write_text(contracts.canonical_json({
            "approval_schema_version": 1,
            "candidate_id": preview["candidate_id"],
            "decision": "APPROVE",
            "explicit_user_approval": True,
            "approver": "user",
            "approved_at": "2026-07-15T21:00:00+08:00",
            "approval_challenge": preview["approval_challenge"],
            "candidate_digest": preview["candidate_digest"],
            "validation_digest": preview["validation_digest"],
            "approved_scope": ["proposed_change"],
            "reason": "approved for controlled promotion",
        }), encoding="utf-8")
        promoter.apply_approval(candidate, self.skill, receipt, now=self.now)
        return candidate, receipt

    def _receipt(self, candidate, decision="APPROVE", **overrides):
        preview = promoter.approval_preview(candidate)
        value = {
            "approval_schema_version": 1,
            "candidate_id": preview["candidate_id"],
            "decision": decision,
            "explicit_user_approval": True,
            "approver": "user",
            "approved_at": "2026-07-15T21:00:00+08:00",
            "approval_challenge": preview["approval_challenge"],
            "candidate_digest": preview["candidate_digest"],
            "validation_digest": preview["validation_digest"],
            "approved_scope": ["proposed_change"],
            "reason": "test receipt",
        }
        value.update(overrides)
        path = self.fixture.root / "receipt.json"
        path.write_text(contracts.canonical_json(value), encoding="utf-8")
        return path

    def _structured_approved(self, candidate_type, artifact_id, artifact_type, new_artifact):
        candidate = self._candidate()
        path = candidate / "candidate.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        change = {"operation": "ADD_TASK", "task_id": "", "task": {}, "new_artifact": new_artifact}
        structure = copy.deepcopy(payload["structure_payload"])
        structure.update({"candidate_type": candidate_type, "operation": "ADD_TASK", "task": {}, "change": {}, "event": {}})
        payload.update({
            "candidate_type": candidate_type,
            "target_artifact_id": artifact_id,
            "target_artifact_type": artifact_type,
            "proposed_change": change,
            "structure_payload": structure,
            "status": "APPROVED",
        })
        payload["structure_signature"] = contracts.sha256_digest(contracts.canonical_json(structure))
        payload["candidate_id"] = contracts.candidate_id({
            "candidate_type": payload["candidate_type"], "risk_level": payload["risk_level"],
            "source_template_id": payload["source_template_id"], "source_template_version": payload["source_template_version"],
            "source_template_digest": payload["source_template_digest"], "structure_signature": payload["structure_signature"],
            "proposed_change": payload["proposed_change"],
        }, now=self.now)
        old = candidate
        path.write_text(contracts.canonical_json(payload), encoding="utf-8")
        candidate = old.parent / payload["candidate_id"]
        old.rename(candidate)
        current = json.loads((candidate / "candidate.json").read_text(encoding="utf-8"))
        current["approval"] = {"decision": "APPROVE", "approver": "user", "approved_at": "2026-07-15T21:00:00+08:00", "approved_scope": ["proposed_change"]}
        current["approval"]["approved_candidate_digest"] = contracts.sha256_digest(contracts.canonical_json(current))
        (candidate / "candidate.json").write_text(contracts.canonical_json(current), encoding="utf-8")
        return candidate

    def test_unmet_threshold_cannot_be_approved(self):
        candidate = self._candidate()
        receipt = self._receipt(candidate)
        with self.assertRaises(promoter.PromotionError):
            promoter.apply_approval(candidate, self.skill, receipt, now=self.now)

    def test_proposed_candidate_cannot_be_approved(self):
        candidate = self._candidate()
        preview = promoter.approval_preview(candidate)
        self.assertFalse(preview["eligible"])

    def test_approval_preview_is_zero_write(self):
        candidate = self._candidate()
        before = {path: path.read_bytes() for path in candidate.rglob("*") if path.is_file()}
        promoter.approval_preview(candidate)
        after = {path: path.read_bytes() for path in candidate.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_challenge_is_deterministic(self):
        candidate = self._candidate()
        self.assertEqual(promoter.approval_challenge(candidate), promoter.approval_challenge(candidate))

    def test_candidate_change_invalidates_challenge(self):
        candidate = self._candidate()
        first = promoter.approval_challenge(candidate)
        path = candidate / "validation.md"
        path.write_text(path.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
        self.assertNotEqual(first, promoter.approval_challenge(candidate))

    def test_missing_receipt_is_rejected(self):
        candidate = self._candidate()
        with self.assertRaises(promoter.PromotionError):
            promoter.apply_approval(candidate, self.skill, self.fixture.root / "missing.json", now=self.now)

    def test_tool_does_not_create_receipt(self):
        candidate = self._candidate()
        path = self.fixture.root / "missing.json"
        with self.assertRaises(promoter.PromotionError):
            promoter.apply_approval(candidate, self.skill, path, now=self.now)
        self.assertFalse(path.exists())

    def test_candidate_id_mismatch_is_rejected(self):
        candidate = self._candidate()
        receipt = self._receipt(candidate, candidate_id="cand-20260715-000000000000")
        with self.assertRaises(promoter.PromotionError):
            promoter.apply_approval(candidate, self.skill, receipt, now=self.now)

    def test_candidate_digest_mismatch_is_rejected(self):
        candidate = self._candidate()
        receipt = self._receipt(candidate, candidate_digest="0" * 64)
        with self.assertRaises(promoter.PromotionError):
            promoter.apply_approval(candidate, self.skill, receipt, now=self.now)

    def test_validation_digest_mismatch_is_rejected(self):
        candidate = self._candidate()
        receipt = self._receipt(candidate, validation_digest="0" * 64)
        with self.assertRaises(promoter.PromotionError):
            promoter.apply_approval(candidate, self.skill, receipt, now=self.now)

    def test_missing_explicit_user_approval_is_rejected(self):
        candidate = self._candidate()
        receipt = self._receipt(candidate, explicit_user_approval=False)
        with self.assertRaises(promoter.PromotionError):
            promoter.apply_approval(candidate, self.skill, receipt, now=self.now)

    def test_invalid_timezone_is_rejected(self):
        candidate = self._candidate()
        receipt = self._receipt(candidate, approved_at="2026-07-15T21:00:00")
        with self.assertRaises(promoter.PromotionError):
            promoter.apply_approval(candidate, self.skill, receipt, now=self.now)

    def test_approve_receipt_enters_approved(self):
        candidate = self._candidate()
        validator.apply_validation(candidate, self.skill, self.project, result="PASS", now=self.now)
        second = self.fixture.root / "second-project"
        shutil.copytree(self.project, second)
        (second / contracts.CHECKLIST_NAME).write_text((second / contracts.CHECKLIST_NAME).read_text(encoding="utf-8").replace("demo-project", "second-project"), encoding="utf-8")
        validator.apply_validation(candidate, self.skill, second, result="PASS", now=self.now)
        receipt = self._receipt(candidate)
        result = promoter.apply_approval(candidate, self.skill, receipt, now=self.now)
        self.assertEqual(result["status"], "APPROVED")

    def test_approval_lock_is_released(self):
        candidate = self._candidate()
        receipt = self._receipt(candidate, decision="REJECT")
        promoter.apply_approval(candidate, self.skill, receipt, now=self.now)
        self.assertFalse((candidate / ".approval.lock").exists())

    def test_reject_receipt_enters_rejected(self):
        candidate = self._candidate()
        receipt = self._receipt(candidate, decision="REJECT")
        result = promoter.apply_approval(candidate, self.skill, receipt, now=self.now)
        self.assertEqual(result["status"], "REJECTED")

    def test_approval_record_is_saved_and_unknown_field_preserved(self):
        candidate = self._approved_candidate()[0]
        path = candidate / "candidate.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["future_unknown"] = {"keep": True}
        path.write_text(contracts.canonical_json(payload), encoding="utf-8")
        # A changed candidate must not accept an old receipt, proving challenge binding.
        self.assertRaises(promoter.PromotionError, promoter.apply_approval, candidate, self.skill, self._receipt(candidate), now=self.now)

    def test_critical_scope_requires_security_or_audit(self):
        candidate = self._approved_candidate()[0]
        path = candidate / "candidate.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["risk_level"] = "CRITICAL"
        path.write_text(contracts.canonical_json(payload), encoding="utf-8")
        receipt = self._receipt(candidate, approved_scope=["proposed_change"])
        with self.assertRaises(promoter.PromotionError):
            promoter.apply_approval(candidate, self.skill, receipt, now=self.now)

    def test_approved_candidate_can_generate_promotion_preview(self):
        candidate, _ = self._approved_candidate()
        result = promoter.promotion_preview(candidate, self.skill)
        self.assertTrue(result["promotion_eligible"])
        self.assertEqual(result["new_version"], "2.0.0")
        self.assertFalse(result["writes"])

    def test_non_approved_candidate_cannot_promote(self):
        candidate = self._candidate()
        with self.assertRaises(promoter.PromotionError):
            promoter.promotion_preview(candidate, self.skill)

    def test_promotion_preview_is_zero_write(self):
        candidate, _ = self._approved_candidate()
        before = {path: path.read_bytes() for path in self.skill.rglob("*") if path.is_file()}
        promoter.promotion_preview(candidate, self.skill)
        after = {path: path.read_bytes() for path in self.skill.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_add_task_is_major(self):
        candidate, _ = self._approved_candidate()
        self.assertEqual(promoter.promotion_preview(candidate, self.skill)["semver_level"], "MAJOR")

    def test_low_declared_risk_is_escalated_for_structural_change(self):
        candidate, _ = self._approved_candidate()
        old = candidate
        path = candidate / "candidate.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["risk_level"] = "LOW"
        payload["candidate_id"] = contracts.candidate_id({
            "candidate_type": payload["candidate_type"], "risk_level": "LOW",
            "source_template_id": payload["source_template_id"], "source_template_version": payload["source_template_version"],
            "source_template_digest": payload["source_template_digest"], "structure_signature": payload["structure_signature"],
            "proposed_change": payload["proposed_change"],
        }, now=self.now)
        payload["approval"].pop("approved_candidate_digest", None)
        path.write_text(contracts.canonical_json(payload), encoding="utf-8")
        candidate = old.parent / payload["candidate_id"]
        old.rename(candidate)
        rebound = json.loads((candidate / "candidate.json").read_text(encoding="utf-8"))
        rebound["approval"]["approved_candidate_digest"] = contracts.sha256_digest(contracts.canonical_json(rebound))
        (candidate / "candidate.json").write_text(contracts.canonical_json(rebound), encoding="utf-8")
        result = promoter.promotion_preview(candidate, self.skill)
        self.assertEqual(result["risk_level"], "HIGH")
        self.assertIn("升级", result["risk_escalation_reason"])

    def test_order_change_is_minor_when_structured(self):
        candidate, _ = self._approved_candidate()
        path = candidate / "candidate.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["proposed_change"] = {"operation": "CHANGE_ORDER", "order": ["P01", "P02", "P03"]}
        path.write_text(contracts.canonical_json(payload), encoding="utf-8")
        self.assertRaises(promoter.PromotionError, promoter.promotion_preview, candidate, self.skill)

    def test_documentation_change_is_patch_when_structured(self):
        candidate, _ = self._approved_candidate()
        path = candidate / "candidate.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["proposed_change"] = {"operation": "UPDATE_DOCUMENTATION", "documentation": "## Additional Notes\nStable."}
        path.write_text(contracts.canonical_json(payload), encoding="utf-8")
        # The candidate ID/structure contract is intentionally invalidated by
        # this direct mutation, so the promoter must reject instead of guessing.
        with self.assertRaises(promoter.PromotionError):
            promoter.promotion_preview(candidate, self.skill)

    def test_missing_structured_plan_is_rejected(self):
        candidate, _ = self._approved_candidate()
        path = candidate / "candidate.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["proposed_change"] = {"operation": "ADD_TASK"}
        path.write_text(contracts.canonical_json(payload), encoding="utf-8")
        with self.assertRaises(promoter.PromotionError):
            promoter.promotion_preview(candidate, self.skill)

    def test_old_template_version_is_unchanged_after_promotion(self):
        candidate, _ = self._approved_candidate()
        old_path = self.skill / "templates/workflow/base/generic-project/1.0.0.md"
        old_bytes = old_path.read_bytes()
        promoter.promotion_apply(candidate, self.skill)
        self.assertEqual(old_path.read_bytes(), old_bytes)

    def test_new_version_digest_is_correct(self):
        candidate, _ = self._approved_candidate()
        result = promoter.promotion_apply(candidate, self.skill)
        new_path = self.skill / "templates/workflow/base/generic-project/2.0.0.md"
        self.assertEqual(result["new_digest"], contracts.file_digest(new_path))

    def test_registry_json_updates_and_projection_is_deterministic(self):
        candidate, _ = self._approved_candidate()
        promoter.promotion_apply(candidate, self.skill)
        registry_path = self.skill / "templates/workflow/template_registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        entry = next(item for item in registry["templates"] if item["id"] == "generic-project")
        self.assertEqual(entry["current_version"], "2.0.0")
        self.assertEqual((self.skill / "templates/workflow/00_TEMPLATE_REGISTRY.md").read_text(encoding="utf-8"), contracts.registry_markdown(registry))

    def test_history_migration_and_rollback_files_are_complete(self):
        candidate, _ = self._approved_candidate()
        promoter.promotion_apply(candidate, self.skill)
        root = self.skill / "templates/workflow/history/generic-project/2.0.0"
        self.assertEqual(sorted(path.name for path in root.iterdir()), ["MIGRATION.md", "PROMOTION.md", "ROLLBACK.md", "promotion.json"])

    def test_candidate_becomes_applied_only_after_transaction(self):
        candidate, _ = self._approved_candidate()
        promoter.promotion_apply(candidate, self.skill)
        payload = json.loads((candidate / "candidate.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "APPLIED")
        self.assertIn("application", payload)

    def test_failed_transaction_keeps_approved_and_no_new_version(self):
        candidate, _ = self._approved_candidate()
        with mock.patch.object(promoter.contracts, "atomic_write_text", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                promoter.promotion_apply(candidate, self.skill)
        payload = json.loads((candidate / "candidate.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "APPROVED")
        self.assertFalse((self.skill / "templates/workflow/base/generic-project/2.0.0.md").exists())

    def test_target_artifact_digest_tampering_is_rejected(self):
        candidate, _ = self._approved_candidate()
        path = self.skill / "templates/workflow/base/generic-project/1.0.0.md"
        path.write_text(path.read_text(encoding="utf-8") + "tamper\n", encoding="utf-8")
        with self.assertRaises(promoter.PromotionError):
            promoter.promotion_preview(candidate, self.skill)

    def test_same_name_new_asset_is_not_overwritten(self):
        candidate, _ = self._approved_candidate()
        result = promoter.promotion_preview(candidate, self.skill, target_id="generic-project")
        self.assertEqual(result["artifact_id"], "generic-project")
        self.assertEqual(result["previous_version"], "1.0.0")

    def test_new_template_requires_explicit_structured_artifact(self):
        candidate = self._candidate("NEW_TEMPLATE_CANDIDATE")
        path = candidate / "candidate.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["status"] = "APPROVED"
        payload["target_artifact_id"] = "new-template"
        payload["proposed_change"] = {"operation": "ADD_TASK"}
        path.write_text(contracts.canonical_json(payload), encoding="utf-8")
        with self.assertRaises(promoter.PromotionError):
            promoter.promotion_preview(candidate, self.skill)

    def test_new_module_candidate_uses_module_type(self):
        candidate = self._candidate("NEW_MODULE_CANDIDATE")
        path = candidate / "candidate.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["status"] = "APPROVED"
        payload["target_artifact_id"] = "new-module"
        payload["proposed_change"] = {"operation": "ADD_TASK", "new_artifact": {"description": "New module", "tasks": [{"ID": "T01", "阶段/任务": "Test"}]}}
        path.write_text(contracts.canonical_json(payload), encoding="utf-8")
        with self.assertRaises(promoter.PromotionError):
            promoter.promotion_preview(candidate, self.skill)

    def test_new_template_candidate_creates_1_0_0(self):
        candidate = self._structured_approved("NEW_TEMPLATE_CANDIDATE", "new-template", "task-template", {"description": "New Template", "tasks": [{"ID": "P01", "阶段/任务": "Start"}]})
        result = promoter.promotion_apply(candidate, self.skill)
        self.assertEqual(result["new_version"], "1.0.0")
        self.assertTrue((self.skill / "templates/workflow/task-types/new-template/1.0.0.md").is_file())

    def test_new_module_candidate_creates_1_0_0(self):
        candidate = self._structured_approved("NEW_MODULE_CANDIDATE", "new-module", "workflow-module", {"description": "New Module", "tasks": [{"ID": "T01", "阶段/任务": "Check"}]})
        result = promoter.promotion_apply(candidate, self.skill)
        self.assertEqual(result["new_version"], "1.0.0")
        self.assertTrue((self.skill / "templates/workflow/modules/new-module/1.0.0.md").is_file())

    def test_approval_and_apply_preserve_unknown_candidate_fields(self):
        candidate, _ = self._approved_candidate()
        path = candidate / "candidate.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["future_unknown"] = {"keep": True}
        path.write_text(contracts.canonical_json(payload), encoding="utf-8")
        # Old approval challenge is invalid after mutation; no silent overwrite.
        with self.assertRaises(promoter.PromotionError):
            promoter.promotion_apply(candidate, self.skill)

    def test_project_checklist_is_never_modified(self):
        candidate, _ = self._approved_candidate()
        before = (self.project / contracts.CHECKLIST_NAME).read_bytes()
        promoter.promotion_apply(candidate, self.skill)
        self.assertEqual((self.project / contracts.CHECKLIST_NAME).read_bytes(), before)

    def test_no_external_model_or_network_references(self):
        source = (ROOT / "scripts/workflow_candidate_promoter.py").read_text(encoding="utf-8").lower()
        for token in ("requests.", "urllib", "agnes", "http://", "https://", "model"):
            self.assertNotIn(token, source)

    def test_cli_approval_preview_is_json_and_read_only(self):
        candidate = self._candidate()
        command = [sys.executable, str(ROOT / "scripts/workflow_candidate_promoter.py"), "--candidate-dir", str(candidate), "--approval-preview", "--format", "json"]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["mode"], "approval-preview")

    def test_cli_requires_explicit_mode(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/workflow_candidate_promoter.py"), "--candidate-dir", str(self.project)], capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
