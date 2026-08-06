#!/usr/bin/env python3
"""P4-01 deterministic candidate validation tests."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import workflow_candidate_generator as generator
import workflow_candidate_validator as validator
import workflow_contracts as contracts


class WorkflowCandidateValidatorTests(unittest.TestCase):
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

    def _create_candidate(self, project_id="demo-project"):
        helper = __import__("test_workflow_candidate_generator", fromlist=["WorkflowCandidateGeneratorTests"]).WorkflowCandidateGeneratorTests
        source = helper()
        source.fixture = self.fixture
        source.successful_added_checklist(project_id=project_id)
        result = generator.generate_candidates(self.skill, self.project, apply=True, now=self.now)
        return self.skill / "templates/workflow/candidates" / result["candidates"][0]["candidate_id"]

    def test_first_pass_enters_validating_without_second_count_for_source_project(self):
        candidate = self._create_candidate()
        preview = validator.validate_submission(candidate, self.skill, self.project, result="PASS")
        self.assertEqual(preview["result"], "PASS")
        self.assertFalse(preview["writes"])
        applied = validator.apply_validation(candidate, self.skill, self.project, result="PASS", now=self.now)
        self.assertEqual(applied["candidate_status"], "VALIDATING")
        self.assertEqual(applied["current_validation_count"], 1)
        self.assertFalse(applied["approval_ready"])

    def test_second_project_pass_increments_once(self):
        candidate = self._create_candidate()
        validator.apply_validation(candidate, self.skill, self.project, result="PASS", now=self.now)
        second = self.fixture.root / "second-project"
        shutil.copytree(self.project, second)
        text = (second / contracts.CHECKLIST_NAME).read_text(encoding="utf-8").replace("demo-project", "second-project")
        (second / contracts.CHECKLIST_NAME).write_text(text, encoding="utf-8")
        result = validator.apply_validation(candidate, self.skill, second, result="PASS", now=self.now)
        self.assertEqual(result["current_validation_count"], 2)
        payload = json.loads((candidate / "candidate.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["successful_source_project_ids"], ["demo-project", "second-project"])

    def test_fail_does_not_increment_and_two_projects_reject(self):
        candidate = self._create_candidate()
        first = validator.apply_validation(candidate, self.skill, self.project, result="FAIL", reason="counterexample", now=self.now)
        self.assertEqual(first["current_validation_count"], 1)
        second = self.fixture.root / "second-project"
        shutil.copytree(self.project, second)
        text = (second / contracts.CHECKLIST_NAME).read_text(encoding="utf-8").replace("demo-project", "second-project")
        (second / contracts.CHECKLIST_NAME).write_text(text, encoding="utf-8")
        result = validator.apply_validation(candidate, self.skill, second, result="FAIL", reason="second counterexample", now=self.now)
        self.assertEqual(result["candidate_status"], "REJECTED")
        self.assertEqual(result["current_validation_count"], 1)

    def test_inconclusive_does_not_count_then_pass_counts_once(self):
        candidate = self._create_candidate()
        validator.apply_validation(candidate, self.skill, self.project, result="INCONCLUSIVE", now=self.now)
        payload = json.loads((candidate / "candidate.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["current_validation_count"], 1)
        result = validator.apply_validation(candidate, self.skill, self.project, result="PASS", now=self.now)
        self.assertEqual(result["current_validation_count"], 1)
        self.assertTrue(any(item["result"] == "PASS" for item in json.loads((candidate / "candidate.json").read_text(encoding="utf-8"))["validation_history"]))

    def test_duplicate_pass_does_not_duplicate_history_or_count(self):
        candidate = self._create_candidate()
        validator.apply_validation(candidate, self.skill, self.project, result="PASS", now=self.now)
        before = json.loads((candidate / "candidate.json").read_text(encoding="utf-8"))
        validator.apply_validation(candidate, self.skill, self.project, result="PASS", now=self.now)
        after = json.loads((candidate / "candidate.json").read_text(encoding="utf-8"))
        self.assertEqual(len(after["validation_history"]), len(before["validation_history"]))
        self.assertEqual(after["current_validation_count"], before["current_validation_count"])

    def test_pass_then_fail_is_conflict_not_downgrade(self):
        candidate = self._create_candidate()
        validator.apply_validation(candidate, self.skill, self.project, result="PASS", now=self.now)
        result = validator.apply_validation(candidate, self.skill, self.project, result="FAIL", now=self.now)
        self.assertEqual(result["candidate_status"], "VALIDATING")
        payload = json.loads((candidate / "candidate.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "VALIDATING")
        self.assertTrue(payload["validation_conflicts"])

    def test_missing_evidence_cannot_pass(self):
        candidate = self._create_candidate()
        text = (self.project / contracts.CHECKLIST_NAME).read_text(encoding="utf-8").replace("| 已核验 | P02A.md |", "| 已核验 | — |")
        (self.project / contracts.CHECKLIST_NAME).write_text(text, encoding="utf-8")
        preview = validator.validate_submission(candidate, self.skill, self.project, result="PASS")
        self.assertEqual(preview["result"], "INCONCLUSIVE")
        self.assertFalse(preview["writes"])

    def test_checklist_digest_mismatch_is_invalid(self):
        candidate = self._create_candidate()
        path = self.project / contracts.CHECKLIST_NAME
        path.write_text(path.read_text(encoding="utf-8") + "tamper\n", encoding="utf-8")
        result = validator.validate_submission(candidate, self.skill, self.project, result="PASS")
        self.assertFalse(result["valid"])
        self.assertTrue(any("digest" in error for error in result["errors"]))

    def test_template_digest_mismatch_is_invalid(self):
        candidate = self._create_candidate()
        template = self.skill / "templates/workflow/base/generic-project/1.0.0.md"
        template.write_text(template.read_text(encoding="utf-8") + "tamper\n", encoding="utf-8")
        result = validator.validate_submission(candidate, self.skill, self.project, result="PASS")
        self.assertFalse(result["valid"])
        self.assertTrue(any("replay" in error for error in result["errors"]))

    def test_structure_tampering_is_invalid(self):
        candidate = self._create_candidate()
        path = candidate / "candidate.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["structure_payload"]["operation"] = "REMOVE_TASK"
        path.write_text(contracts.canonical_json(payload), encoding="utf-8")
        result = validator.validate_submission(candidate, self.skill, self.project, result="PASS")
        self.assertFalse(result["valid"])
        self.assertTrue(any("structure_signature" in error for error in result["errors"]))

    def test_low_risk_reaches_approval_ready(self):
        candidate = self._create_candidate()
        old_directory = candidate
        path = candidate / "candidate.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["risk_level"] = "LOW"
        payload["required_validation_count"] = 1
        payload["candidate_id"] = contracts.candidate_id({
            "candidate_type": payload["candidate_type"], "risk_level": "LOW",
            "source_template_id": payload["source_template_id"], "source_template_version": payload["source_template_version"],
            "source_template_digest": payload["source_template_digest"], "structure_signature": payload["structure_signature"],
            "proposed_change": payload["proposed_change"],
        }, now=self.now)
        path.unlink()
        path.write_text(contracts.canonical_json(payload), encoding="utf-8")
        new_directory = candidate.parent / payload["candidate_id"]
        old_directory.rename(new_directory)
        result = validator.apply_validation(new_directory, self.skill, self.project, result="PASS", now=self.now)
        self.assertTrue(result["approval_ready"])

    def test_high_risk_never_auto_approves(self):
        candidate = self._create_candidate()
        result = validator.apply_validation(candidate, self.skill, self.project, result="PASS", now=self.now)
        self.assertFalse(result["approval_ready"])

    def test_critical_requires_existing_approval_record(self):
        candidate = self._create_candidate()
        path = candidate / "candidate.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["risk_level"] = "CRITICAL"
        path.write_text(contracts.canonical_json(payload), encoding="utf-8")
        self.assertFalse(validator.validate_submission(candidate, self.skill, self.project, result="PASS")["approval_ready"])

    def test_critical_with_existing_approval_record_can_be_ready(self):
        candidate = self._create_candidate()
        path = candidate / "candidate.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["risk_level"] = "CRITICAL"
        payload["required_validation_count"] = 1
        structural = {
            "candidate_type": payload["candidate_type"], "risk_level": "CRITICAL",
            "source_template_id": payload["source_template_id"], "source_template_version": payload["source_template_version"],
            "source_template_digest": payload["source_template_digest"], "structure_signature": payload["structure_signature"],
            "proposed_change": payload["proposed_change"],
        }
        new_id = contracts.candidate_id(structural, now=self.now)
        payload["candidate_id"] = new_id
        old_directory = candidate
        path.write_text(contracts.canonical_json(payload), encoding="utf-8")
        new_directory = candidate.parent / new_id
        old_directory.rename(new_directory)
        approval = self.fixture.root / "approval.json"
        approval.write_text(json.dumps({"candidate_id": new_id, "approved": True}), encoding="utf-8")
        result = validator.apply_validation(new_directory, self.skill, self.project, result="PASS", approval_record=approval, now=self.now)
        self.assertTrue(result["approval_ready"])

    def test_schema_and_package_integrity_failure_is_invalid(self):
        candidate = self._create_candidate()
        (candidate / "validation.md").unlink()
        result = validator.validate_submission(candidate, self.skill, self.project, result="PASS")
        self.assertEqual(result["result"], "INCONCLUSIVE")
        self.assertTrue(result["errors"])

    def test_unknown_fields_are_preserved(self):
        candidate = self._create_candidate()
        path = candidate / "candidate.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["future_unknown"] = {"keep": True}
        path.write_text(contracts.canonical_json(payload), encoding="utf-8")
        validator.apply_validation(candidate, self.skill, self.project, result="PASS", now=self.now)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["future_unknown"], {"keep": True})

    def test_apply_failure_leaves_candidate_json_valid(self):
        candidate = self._create_candidate()
        original = (candidate / "candidate.json").read_bytes()
        with mock.patch.object(validator.contracts, "atomic_write_text", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                validator.apply_validation(candidate, self.skill, self.project, result="PASS", now=self.now)
        self.assertEqual((candidate / "candidate.json").read_bytes(), original)
        contracts.validate_candidate(json.loads((candidate / "candidate.json").read_text(encoding="utf-8")))

    def test_apply_failure_after_candidate_write_rolls_back_package(self):
        candidate = self._create_candidate()
        original = {path.name: path.read_bytes() for path in candidate.iterdir() if path.is_file()}
        calls = {"count": 0}
        real_write = validator.contracts.atomic_write_text

        def fail_on_second(path, text):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("injected after first package write")
            return real_write(path, text)

        with mock.patch.object(validator.contracts, "atomic_write_text", side_effect=fail_on_second):
            with self.assertRaises(OSError):
                validator.apply_validation(candidate, self.skill, self.project, result="PASS", now=self.now)
        for name, content in original.items():
            self.assertEqual((candidate / name).read_bytes(), content)

    def test_invalid_result_does_not_write(self):
        candidate = self._create_candidate()
        with self.assertRaises(validator.CandidateValidationError):
            validator.apply_validation(candidate, self.skill, self.project, result="UNKNOWN")

    def test_invalid_submission_is_not_counted(self):
        candidate = self._create_candidate()
        result = validator.apply_validation(candidate, self.skill, self.project, result="INVALID", reason="malformed evidence", now=self.now)
        self.assertFalse(result["counted"])
        self.assertEqual(result["current_validation_count"], 1)

    def test_one_fail_does_not_reject_candidate(self):
        candidate = self._create_candidate()
        result = validator.apply_validation(candidate, self.skill, self.project, result="FAIL", now=self.now)
        self.assertEqual(result["candidate_status"], "VALIDATING")

    def test_pass_without_acceptance_is_rejected_in_preview(self):
        candidate = self._create_candidate()
        (self.project / "5_audit.md").write_text("未完成", encoding="utf-8")
        result = validator.validate_submission(candidate, self.skill, self.project, result="PASS")
        self.assertEqual(result["result"], "INCONCLUSIVE")
        self.assertTrue(result["errors"])

    def test_replay_difference_mismatch_is_rejected(self):
        candidate = self._create_candidate()
        path = candidate / "candidate.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["proposed_change"]["task_id"] = "P99"
        path.write_text(contracts.canonical_json(payload), encoding="utf-8")
        result = validator.validate_submission(candidate, self.skill, self.project, result="PASS")
        self.assertFalse(result["valid"])
        self.assertTrue(result["errors"])

    def test_source_project_is_added_on_new_successful_validation(self):
        candidate = self._create_candidate()
        second = self.fixture.root / "second-project"
        shutil.copytree(self.project, second)
        text = (second / contracts.CHECKLIST_NAME).read_text(encoding="utf-8").replace("demo-project", "second-project")
        (second / contracts.CHECKLIST_NAME).write_text(text, encoding="utf-8")
        validator.apply_validation(candidate, self.skill, second, result="PASS", now=self.now)
        payload = json.loads((candidate / "candidate.json").read_text(encoding="utf-8"))
        self.assertIn("second-project", payload["source_project_ids"])

    def test_candidate_status_never_becomes_approved(self):
        candidate = self._create_candidate()
        validator.apply_validation(candidate, self.skill, self.project, result="PASS", now=self.now)
        payload = json.loads((candidate / "candidate.json").read_text(encoding="utf-8"))
        self.assertNotIn(payload["status"], {"APPROVED", "APPLIED"})

    def test_candidate_level_lock_is_released(self):
        candidate = self._create_candidate()
        validator.apply_validation(candidate, self.skill, self.project, result="PASS", now=self.now)
        self.assertFalse((candidate / ".validation.lock").exists())

    def test_validation_history_contains_source_digest(self):
        candidate = self._create_candidate()
        validator.apply_validation(candidate, self.skill, self.project, result="INCONCLUSIVE", now=self.now)
        payload = json.loads((candidate / "candidate.json").read_text(encoding="utf-8"))
        entry = payload["validation_history"][0]
        self.assertEqual(entry["source_checklist_digest"], contracts.file_digest(self.project / contracts.CHECKLIST_NAME))

    def test_repeated_preview_is_deterministic(self):
        candidate = self._create_candidate()
        first = validator.validate_submission(candidate, self.skill, self.project, result="PASS")
        second = validator.validate_submission(candidate, self.skill, self.project, result="PASS")
        self.assertEqual(first, second)

    def test_preview_cli_is_zero_write(self):
        candidate = self._create_candidate()
        before = {path: path.read_bytes() for path in candidate.rglob("*") if path.is_file()}
        command = [sys.executable, str(ROOT / "scripts/workflow_candidate_validator.py"), "--candidate-dir", str(candidate), "--source-project-root", str(self.project), "--skill-root", str(self.skill), "--preview", "--result", "PASS", "--format", "json"]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0)
        after = {path: path.read_bytes() for path in candidate.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(json.loads(completed.stdout)["writes"])

    def test_forbidden_status_cannot_be_validated(self):
        candidate = self._create_candidate()
        path = candidate / "candidate.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["status"] = "APPROVED"
        path.write_text(contracts.canonical_json(payload), encoding="utf-8")
        result = validator.validate_submission(candidate, self.skill, self.project, result="PASS")
        self.assertFalse(result["valid"])
        self.assertIn("candidate status cannot be validated: APPROVED", result["errors"])

    def test_no_external_model_or_network_references(self):
        source = (ROOT / "scripts/workflow_candidate_validator.py").read_text(encoding="utf-8").lower()
        for token in ("requests.", "urllib", "agnes", "http://", "https://", "llm", "model"):
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
