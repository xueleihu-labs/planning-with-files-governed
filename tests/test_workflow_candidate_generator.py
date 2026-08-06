#!/usr/bin/env python3
"""P3-02 deterministic candidate generation tests."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import workflow_candidate_generator as generator
import workflow_contracts as contracts
class WorkflowCandidateGeneratorTests(unittest.TestCase):
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

    def successful_added_checklist(self, project_id="demo-project", specific=False):
        statuses = {"P01": "已完成", "P02": "已完成", "P03": "已完成", "P02A": "已完成"}
        evidence = {task_id: f"{task_id}.md" for task_id in statuses}
        for path in evidence.values():
            (self.project / path).write_text("evidence", encoding="utf-8")
        (self.project / "5_audit.md").write_text("结论：PASS", encoding="utf-8")
        (self.project / "4_handoff.md").write_text("交接完成", encoding="utf-8")
        rows = self.fixture.task_rows(statuses={"P01": "已完成", "P02": "已完成", "P03": "已完成"}, evidence={"P01": "P01.md", "P02": "P02.md", "P03": "P03.md"})
        rows.append({
            "id": "P02A", "name": "一次性人工步骤" if specific else "新增流程步骤",
            "owner": "Executor", "dependency": "P02", "priority": "P1", "mainline": "是",
            "status": "已完成", "verification": "已核验", "evidence": "P02A.md",
            "note": "project-specific" if specific else "—", "next": "验收/封板",
        })
        text = self.fixture.checklist(rows, history=self.fixture.history(["P01", "P02", "P03", "P02A"]), extra={"project_id": project_id})
        self.fixture.write(text)

    def test_preview_classifies_successful_added_task(self):
        self.successful_added_checklist()
        result = generator.generate_candidates(self.skill, self.project, now=self.now)
        self.assertEqual(result["mode"], "preview")
        self.assertTrue(result["candidate_eligible"])
        self.assertEqual(result["candidates"][0]["candidate_type"], "TEMPLATE_MISSING")
        self.assertFalse((self.skill / "templates/workflow/candidates").exists())

    def test_unverified_task_is_not_eligible(self):
        self.successful_added_checklist()
        text = (self.project / contracts.CHECKLIST_NAME).read_text(encoding="utf-8").replace("| 已核验 | P02A.md |", "| 未核验 | P02A.md |")
        (self.project / contracts.CHECKLIST_NAME).write_text(text, encoding="utf-8")
        result = generator.generate_candidates(self.skill, self.project, now=self.now)
        self.assertFalse(result["candidate_eligible"])

    def test_completed_task_without_evidence_is_not_eligible(self):
        self.successful_added_checklist()
        text = (self.project / contracts.CHECKLIST_NAME).read_text(encoding="utf-8").replace("| 已核验 | P02A.md |", "| 已核验 | — |")
        (self.project / contracts.CHECKLIST_NAME).write_text(text, encoding="utf-8")
        result = generator.generate_candidates(self.skill, self.project, now=self.now)
        self.assertFalse(result["candidate_eligible"])

    def test_project_specific_is_not_candidate(self):
        self.successful_added_checklist(specific=True)
        result = generator.generate_candidates(self.skill, self.project, now=self.now)
        self.assertFalse(result["candidate_eligible"])
        self.assertTrue(any(item["classification"] == "PROJECT_EXCEPTION" for item in result["classifications"]))

    def test_incidental_rework_is_not_candidate(self):
        self.fixture.write(self.fixture.checklist(self.fixture.task_rows(), history=[{
            "时间": "—", "变更类型": "返工", "涉及ID": "P02", "变更内容": "重新进入进行中",
            "原因": "验证失败", "影响范围": "P02", "执行者": "Codex",
        }]))
        result = generator.generate_candidates(self.skill, self.project, now=self.now)
        self.assertFalse(result["candidate_eligible"])
        self.assertTrue(any(item["classification"] == "INCIDENTAL_ISSUE" for item in result["classifications"]))

    def test_apply_creates_proposed_six_file_package(self):
        self.successful_added_checklist()
        result = generator.generate_candidates(self.skill, self.project, apply=True, now=self.now)
        candidate_id = result["candidates"][0]["candidate_id"]
        directory = self.skill / "templates/workflow/candidates" / candidate_id
        self.assertEqual(sorted(path.name for path in directory.iterdir()), ["CANDIDATE.md", "candidate.json", "evidence.md", "source-projects.md", "template-diff.md", "validation.md"])
        payload = json.loads((directory / "candidate.json").read_text(encoding="utf-8"))
        contracts.validate_candidate(payload)
        self.assertEqual(payload["status"], "PROPOSED")

    def test_high_risk_candidate_is_not_approved(self):
        self.successful_added_checklist()
        result = generator.generate_candidates(self.skill, self.project, apply=True, now=self.now)
        payload = json.loads(next((self.skill / "templates/workflow/candidates").glob("cand-*/candidate.json")).read_text(encoding="utf-8"))
        self.assertEqual(payload["risk_level"], "HIGH")
        self.assertEqual(payload["status"], "PROPOSED")
        self.assertEqual(result["candidates"][0]["applied"], True)

    def test_candidate_id_format_and_time_exclusion(self):
        self.successful_added_checklist()
        first = generator.generate_candidates(self.skill, self.project, now=self.now)
        later = generator.generate_candidates(self.skill, self.project, now=self.now.replace(hour=23))
        self.assertRegex(first["candidates"][0]["candidate_id"], r"^cand-20260715-[0-9a-f]{12}$")
        self.assertEqual(first["candidates"][0]["candidate_id"], later["candidates"][0]["candidate_id"])

    def test_same_structure_deduplicates_without_second_directory(self):
        self.successful_added_checklist()
        first = generator.generate_candidates(self.skill, self.project, apply=True, now=self.now)
        second = generator.generate_candidates(self.skill, self.project, apply=True, now=self.now)
        directories = list((self.skill / "templates/workflow/candidates").glob("cand-*"))
        self.assertEqual(len(directories), 1)
        self.assertEqual(second["candidates"][0]["duplicate_candidate_id"], first["candidates"][0]["candidate_id"])

    def test_duplicate_merge_preserves_unknown_fields_and_validation_count(self):
        self.successful_added_checklist()
        first = generator.generate_candidates(self.skill, self.project, apply=True, now=self.now)
        path = self.skill / "templates/workflow/candidates" / first["candidates"][0]["candidate_id"] / "candidate.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["future_unknown"] = {"keep": True}
        path.write_text(contracts.canonical_json(payload), encoding="utf-8")
        self.successful_added_checklist(project_id="second-project")
        result = generator.generate_candidates(self.skill, self.project, apply=True, now=self.now)
        merged = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(merged["future_unknown"], {"keep": True})
        self.assertEqual(merged["source_project_ids"], ["demo-project", "second-project"])
        self.assertEqual(merged["current_validation_count"], 2)
        self.assertTrue(result["candidates"][0]["duplicate_candidate_id"])

    def test_one_project_does_not_count_twice(self):
        self.successful_added_checklist()
        generator.generate_candidates(self.skill, self.project, apply=True, now=self.now)
        generator.generate_candidates(self.skill, self.project, apply=True, now=self.now)
        path = next((self.skill / "templates/workflow/candidates").glob("cand-*/candidate.json"))
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["current_validation_count"], 1)

    def test_preview_is_zero_write(self):
        self.successful_added_checklist()
        before = {path: path.read_bytes() for path in self.project.rglob("*") if path.is_file()}
        candidate_root = self.skill / "templates/workflow/candidates"
        result = generator.generate_candidates(self.skill, self.project, now=self.now)
        after = {path: path.read_bytes() for path in self.project.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(candidate_root.exists())
        self.assertTrue(result["candidates"])

    def test_write_failure_leaves_no_half_package(self):
        self.successful_added_checklist()
        with mock.patch.object(generator.contracts, "atomic_write_text", side_effect=OSError("injected failure")):
            with self.assertRaises(OSError):
                generator.generate_candidates(self.skill, self.project, apply=True, now=self.now)
        candidate_root = self.skill / "templates/workflow/candidates"
        self.assertFalse(candidate_root.exists())

    def test_schema_validation_passes(self):
        self.successful_added_checklist()
        generator.generate_candidates(self.skill, self.project, apply=True, now=self.now)
        path = next((self.skill / "templates/workflow/candidates").glob("cand-*/candidate.json"))
        contracts.validate_candidate(json.loads(path.read_text(encoding="utf-8")))

    def test_digest_mismatch_rejects_candidate(self):
        self.successful_added_checklist()
        path = self.skill / "templates/workflow/base/generic-project/1.0.0.md"
        path.write_text(path.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
        result = generator.generate_candidates(self.skill, self.project, now=self.now)
        self.assertFalse(result["candidate_eligible"])
        self.assertEqual(result["replay_status"], "DIGEST_MISMATCH")
        self.assertFalse((self.skill / "templates/workflow/candidates").exists())

    def test_no_acceptance_does_not_count_validation(self):
        self.successful_added_checklist()
        (self.project / "5_audit.md").write_text("未完成", encoding="utf-8")
        (self.project / "4_handoff.md").write_text("", encoding="utf-8")
        result = generator.generate_candidates(self.skill, self.project, apply=True, now=self.now)
        self.assertFalse(result["candidates"])
        self.assertFalse((self.skill / "templates/workflow/candidates").exists())

    def test_generic_project_does_not_auto_create_template_candidate(self):
        self.successful_added_checklist()
        result = generator.generate_candidates(self.skill, self.project, now=self.now)
        self.assertFalse(any(item["candidate_type"] == "NEW_TEMPLATE_CANDIDATE" for item in result["candidates"]))

    def test_explicit_new_template_candidate_requires_acceptance(self):
        self.successful_added_checklist()
        result = generator.generate_candidates(self.skill, self.project, request_new_template=True, now=self.now)
        self.assertTrue(any(item["candidate_type"] == "NEW_TEMPLATE_CANDIDATE" for item in result["candidates"]))

    def test_explicit_module_candidate_type(self):
        self.successful_added_checklist()
        result = generator.generate_candidates(self.skill, self.project, candidate_type="NEW_MODULE_CANDIDATE", now=self.now)
        self.assertTrue(any(item["candidate_type"] == "NEW_MODULE_CANDIDATE" for item in result["candidates"]))

    def test_formal_templates_and_registry_remain_unchanged(self):
        self.successful_added_checklist()
        registry = (self.skill / "templates/workflow/template_registry.json").read_bytes()
        template = (self.skill / "templates/workflow/base/generic-project/1.0.0.md").read_bytes()
        generator.generate_candidates(self.skill, self.project, apply=True, now=self.now)
        self.assertEqual(registry, (self.skill / "templates/workflow/template_registry.json").read_bytes())
        self.assertEqual(template, (self.skill / "templates/workflow/base/generic-project/1.0.0.md").read_bytes())

    def test_cli_preview_json_is_read_only(self):
        self.successful_added_checklist()
        command = [
            sys.executable, str(ROOT / "scripts/workflow_candidate_generator.py"),
            "--project-root", str(self.project), "--skill-root", str(self.skill),
            "--preview", "--format", "json",
        ]
        completed = __import__("subprocess").run(command, capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0)
        result = json.loads(completed.stdout)
        self.assertEqual(result["mode"], "preview")
        self.assertFalse(result["writes"])


if __name__ == "__main__":
    unittest.main()
