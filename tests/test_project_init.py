#!/usr/bin/env python3
"""Regression tests for planning-with-files v0.7.0 initialization."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import workflow_contracts as workflow  # noqa: E402
import planning_layout as planning_layout  # noqa: E402
INIT = ROOT / "scripts" / "project_init.py"
CHECK = ROOT / "scripts" / "check_complete.py"
VERSION = ROOT / "scripts" / "check-version.py"
CATCHUP = ROOT / "scripts" / "session-catchup.py"


class ProjectInitTests(unittest.TestCase):
    def file(self, project: Path, name: str) -> Path:
        if name in planning_layout.PLANNING_DOCUMENTS:
            return project / planning_layout.CANONICAL_DIR_NAME / "test-task" / name
        return project / name

    def run_init(self, root: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        supplied = list(args)
        if "--new" in supplied and "--task-id" not in supplied:
            supplied.extend(["--task-id", "test-task"])
        command = [sys.executable, str(INIT), "--project-root", str(root), "--relative-path", "99项目/test", "--index-mode", "skip", *supplied]
        return subprocess.run(command, text=True, capture_output=True, env=env)

    def test_new_creates_nine_files_and_claude_import(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            result = self.run_init(project, "--new", "--project-name", "test")
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = {"00_PROJECT_INDEX.md", "AGENTS.md", "CLAUDE.md", "README.md", planning_layout.CANONICAL_DIR_NAME}
            self.assertTrue(expected.issubset({path.name for path in project.iterdir()}))
            self.assertFalse(any((project / name).exists() for name in planning_layout.PLANNING_DOCUMENTS))
            self.assertIn("@AGENTS.md", (project / "CLAUDE.md").read_text(encoding="utf-8"))
            agents = (project / "AGENTS.md").read_text(encoding="utf-8")
            checklist = self.file(project, "WORKFLOW_CHECKLIST.md")
            self.assertTrue(checklist.exists())
            metadata = workflow.extract_machine_json(checklist.read_text(encoding="utf-8"), "workflow")
            self.assertEqual(metadata["template"]["template_id"], "generic-project")
            self.assertEqual(metadata["owner_agent"], "Codex")
            workflow.validate_checklist_text(checklist.read_text(encoding="utf-8"))

    def test_new_explicit_workflow_template_binds_modules_and_digests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            result = self.run_init(project, "--new", "--workflow-template", "skill-create")
            self.assertEqual(result.returncode, 0, result.stderr)
            metadata = workflow.extract_machine_json(self.file(project, "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8"), "workflow")
            self.assertEqual(metadata["template"]["template_id"], "skill-create")
            self.assertEqual(metadata["modules"][0]["module_id"], "testing")
            self.assertRegex(metadata["template"]["template_digest"], r"^[0-9a-f]{64}$")
            self.assertRegex(metadata["modules"][0]["module_digest"], r"^[0-9a-f]{64}$")
            self.assertEqual(metadata["template_match"]["match_method"], "explicit")
            self.assertEqual(metadata["template_match"]["template_digest"], metadata["template"]["template_digest"])

    def test_new_rule_matching_writes_deterministic_match_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            result = self.run_init(project, "--new", "--project-name", "创建 Skill 项目")
            self.assertEqual(result.returncode, 0, result.stderr)
            metadata = workflow.extract_machine_json(self.file(project, "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8"), "workflow")
            self.assertEqual(metadata["template_match"]["template_id"], "skill-create")
            self.assertEqual(metadata["template_match"]["match_method"], "rule")
            self.assertIsInstance(metadata["template_match"]["matched_signals"], list)

    def test_new_explicit_module_writes_selection_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            result = self.run_init(project, "--new", "--workflow-template", "generic-project", "--module", "testing")
            self.assertEqual(result.returncode, 0, result.stderr)
            metadata = workflow.extract_machine_json(self.file(project, "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8"), "workflow")
            self.assertEqual(metadata["modules"][0]["module_id"], "testing")
            self.assertEqual(metadata["modules"][0]["selection_method"], "explicit")
            self.assertTrue(metadata["modules"][0]["required"] is False)

    def test_required_module_exclusion_is_a_clean_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            result = self.run_init(project, "--new", "--workflow-template", "skill-create", "--exclude-module", "testing")
            self.assertEqual(result.returncode, 3)
            self.assertIn("required template module explicitly excluded", result.stderr)
            self.assertFalse(self.file(project, "WORKFLOW_CHECKLIST.md").exists())

    def test_adopt_preserves_existing_template_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            self.assertEqual(self.run_init(project, "--new", "--workflow-template", "skill-create").returncode, 0)
            result = self.run_init(project, "--adopt")
            self.assertEqual(result.returncode, 0, result.stderr)
            metadata = workflow.extract_machine_json(self.file(project, "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8"), "workflow")
            self.assertEqual(metadata["template"]["template_id"], "skill-create")
            self.assertEqual(metadata["template_match"]["match_method"], "explicit")
            self.assertEqual(metadata["checklist_version"], "1.0.0")

    def test_adopt_preserves_existing_module_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            self.assertEqual(self.run_init(project, "--new", "--workflow-template", "generic-project", "--module", "testing").returncode, 0)
            before = workflow.extract_machine_json(self.file(project, "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8"), "workflow")
            result = self.run_init(project, "--adopt")
            self.assertEqual(result.returncode, 0, result.stderr)
            after = workflow.extract_machine_json(self.file(project, "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8"), "workflow")
            self.assertEqual(after["modules"], before["modules"])
            self.assertEqual(after["checklist_version"], "1.0.0")

    def test_adopt_module_change_bumps_minor_once_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            self.assertEqual(self.run_init(project, "--new", "--workflow-template", "generic-project").returncode, 0)
            changed = self.run_init(project, "--adopt", "--module", "testing")
            self.assertEqual(changed.returncode, 0, changed.stderr)
            metadata = workflow.extract_machine_json(self.file(project, "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8"), "workflow")
            self.assertEqual(metadata["checklist_version"], "1.1.0")
            self.assertEqual(metadata["modules"][0]["module_id"], "testing")
            repeated = self.run_init(project, "--adopt", "--module", "testing")
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            repeated_metadata = workflow.extract_machine_json(self.file(project, "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8"), "workflow")
            self.assertEqual(repeated_metadata["checklist_version"], "1.1.0")

    def test_adopt_explicit_template_overrides_existing_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            self.assertEqual(self.run_init(project, "--new", "--workflow-template", "skill-create").returncode, 0)
            result = self.run_init(project, "--adopt", "--workflow-template", "generic-project")
            self.assertEqual(result.returncode, 0, result.stderr)
            metadata = workflow.extract_machine_json(self.file(project, "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8"), "workflow")
            self.assertEqual(metadata["template"]["template_id"], "generic-project")
            self.assertEqual(metadata["template_match"]["match_method"], "explicit")
            self.assertEqual(metadata["checklist_version"], "1.1.0")

    def test_adopt_creates_unverified_checklist_without_fabricating_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            self.assertEqual(self.run_init(project, "--new").returncode, 0)
            self.file(project, "WORKFLOW_CHECKLIST.md").unlink()
            result = self.run_init(project, "--adopt")
            self.assertEqual(result.returncode, 0, result.stderr)
            text = self.file(project, "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8")
            self.assertNotIn("| 已完成 |", text)
            self.assertIn("| 未核验 |", text)

    def test_adopt_accepts_only_local_explicit_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            self.assertEqual(self.run_init(project, "--new", "--workflow-template", "skill-create").returncode, 0)
            self.file(project, "WORKFLOW_CHECKLIST.md").unlink()
            (project / "evidence.md").write_text("需求与边界已完成，证据见 evidence.md。", encoding="utf-8")
            log = self.file(project, "2_execution_log.md")
            log.write_text(log.read_text(encoding="utf-8") + "\n需求与边界已完成，证据见 evidence.md。\n", encoding="utf-8")
            result = self.run_init(project, "--adopt", "--workflow-template", "skill-create")
            self.assertEqual(result.returncode, 0, result.stderr)
            text = self.file(project, "WORKFLOW_CHECKLIST.md").read_text(encoding="utf-8")
            self.assertIn("| P01 | 需求与边界 |", text)
            self.assertIn("| 已完成 | 已核验 | evidence.md |", text)

    def test_new_refuses_existing_governance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            self.assertEqual(self.run_init(project, "--new").returncode, 0)
            self.assertEqual(self.run_init(project, "--new").returncode, 3)

    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            result = self.run_init(project, "--new", "--dry-run")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(project.exists())

    def test_adopt_appends_profile_without_overwriting_existing_rules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            self.assertEqual(self.run_init(project, "--new").returncode, 0)
            agents = project / "AGENTS.md"
            agents.write_text("# User-owned rules\n", encoding="utf-8")
            (project / "README.md").unlink()
            result = self.run_init(project, "--adopt")
            self.assertEqual(result.returncode, 0, result.stderr)
            content = agents.read_text(encoding="utf-8")
            self.assertTrue(content.startswith("# User-owned rules\n"))
            self.assertTrue((project / "README.md").exists())

    def test_repair_preview_and_apply_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            self.assertEqual(self.run_init(project, "--new").returncode, 0)
            status = self.file(project, "3_status_update.md")
            original = status.read_text(encoding="utf-8").replace("## 执行与审计状态\n", "")
            status.write_text(original, encoding="utf-8")
            preview = self.run_init(project, "--repair")
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertNotIn("## 执行与审计状态", status.read_text(encoding="utf-8"))
            applied = self.run_init(project, "--repair", "--apply-repair")
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertIn("## 执行与审计状态", status.read_text(encoding="utf-8"))

    def test_workflow_repair_preview_is_zero_write_and_apply_is_versioned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            self.assertEqual(self.run_init(project, "--new").returncode, 0)
            checklist = self.file(project, "WORKFLOW_CHECKLIST.md")
            original = checklist.read_text(encoding="utf-8")
            checklist.write_text(original.replace("<!-- END WORKFLOW METADATA -->", "<!-- BROKEN -->"), encoding="utf-8")
            broken = checklist.read_text(encoding="utf-8")
            preview = self.run_init(project, "--repair")
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertEqual(checklist.read_text(encoding="utf-8"), broken)
            applied = self.run_init(project, "--repair", "--apply-repair")
            self.assertEqual(applied.returncode, 0, applied.stderr)
            repaired = checklist.read_text(encoding="utf-8")
            metadata = workflow.extract_machine_json(repaired, "workflow")
            self.assertEqual(metadata["checklist_version"], "1.0.1")

    def test_check_complete_detects_invalid_workflow_completion_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            self.assertEqual(self.run_init(project, "--new").returncode, 0)
            plan = self.file(project, "1_master_plan.md")
            plan.write_text(plan.read_text(encoding="utf-8").replace("PENDING", "PASS", 3), encoding="utf-8")
            checklist = self.file(project, "WORKFLOW_CHECKLIST.md")
            checklist.write_text(checklist.read_text(encoding="utf-8").replace("| 未开始 | 未核验 |", "| 已完成 | 未核验 |", 1), encoding="utf-8")
            result = subprocess.run([sys.executable, str(CHECK), "--root", str(project), "--audit-level", "A0"], text=True, capture_output=True)
            self.assertEqual(result.returncode, 4)
            self.assertIn("completed task lacks verified evidence", result.stdout)

    def test_valid_lock_blocks_concurrent_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            (project / ".planning-init.lock").write_text(json.dumps({"pid": os.getpid(), "root": str(project.resolve())}), encoding="utf-8")
            self.assertEqual(self.run_init(project, "--new").returncode, 6)

    def test_stale_lock_is_retained_as_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            (project / ".planning-init.lock").write_text(json.dumps({"pid": 99999999, "root": str(project.resolve())}), encoding="utf-8")
            result = self.run_init(project, "--new")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(list(project.glob(".planning-init.lock.*.diagnostic")))

    def test_gitignore_is_extended_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            (project / ".gitignore").write_text("user-rule\n", encoding="utf-8")
            self.assertEqual(self.run_init(project, "--new").returncode, 0)
            ignore = (project / ".gitignore").read_text(encoding="utf-8")
            self.assertIn("user-rule", ignore)
            self.assertIn(".planning-init.lock", ignore)

    def test_apply_repair_requires_repair_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_init(Path(directory) / "project", "--apply-repair")
            self.assertEqual(result.returncode, 2)

    def test_audit_levels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            self.assertEqual(self.run_init(project, "--new").returncode, 0)
            plan = self.file(project, "1_master_plan.md")
            audit = self.file(project, "5_audit.md")
            plan.write_text(plan.read_text(encoding="utf-8").replace("PENDING", "PASS", 2), encoding="utf-8")
            self.assertEqual(subprocess.run([sys.executable, str(CHECK), "--root", str(project), "--audit-level", "A0"]).returncode, 0)
            self.assertEqual(subprocess.run([sys.executable, str(CHECK), "--root", str(project), "--audit-level", "A1"]).returncode, 4)
            audit_text = audit.read_text(encoding="utf-8").replace("- **Audit Result**: PENDING", "- **Audit Result**: PASS")
            audit_text = audit_text.replace("- **Audit Agent-ID**: PENDING", "- **Audit Agent-ID**: Auditor")
            audit_text = audit_text.replace("- **Boss Gate**: PENDING", "- **Boss Gate**: APPROVED")
            audit.write_text(audit_text, encoding="utf-8")
            self.assertEqual(subprocess.run([sys.executable, str(CHECK), "--root", str(project), "--audit-level", "A2"]).returncode, 0)

    def test_version_source(self) -> None:
        self.assertEqual(subprocess.run([sys.executable, str(VERSION)]).returncode, 0)

    def test_auto_index_falls_back_and_catchup_reads_root_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            project = temp_root / "project"
            env = os.environ.copy()
            env["PWF_ROOT"] = str(temp_root / "no-external-index")
            result = self.run_init(project, "--new", "--index-mode", "auto", env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((project / "INDEX.md").exists())
            self.assertTrue((project / "LIBRARY_LOG.md").exists())
            index_text = (project / "00_PROJECT_INDEX.md").read_text(encoding="utf-8")
            self.assertIn("索引方式：等价流程", index_text)
            self.assertIn("`AGENTS.md`", index_text)
            catchup = subprocess.run([sys.executable, str(CATCHUP), str(project)], text=True, capture_output=True)
            self.assertEqual(catchup.returncode, 0)
            self.assertIn("Core governance files: 13/13", catchup.stdout)


if __name__ == "__main__":
    unittest.main()
