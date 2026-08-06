from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "scripts" / "project_init.py"
CATCHUP = ROOT / "scripts" / "session-catchup.py"
CHECK = ROOT / "scripts" / "check_complete.py"
PLANNING = ROOT / "00.项目规划与治理"


class ProjectInitLayoutTests(unittest.TestCase):
    def run_init(self, project: Path, *args: str) -> subprocess.CompletedProcess[str]:
        supplied = list(args)
        if "--new" in supplied and "--task-id" not in supplied:
            supplied.extend(["--task-id", "task-one"])
        return subprocess.run(
            [sys.executable, str(INIT), "--project-root", str(project), "--relative-path", "中文项目/with spaces", "--index-mode", "skip", *supplied],
            text=True,
            capture_output=True,
        )

    def test_new_project_keeps_root_entries_and_concentrates_planning_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pwf-init-") as directory:
            project = Path(directory) / "项目 中文 with spaces"
            result = self.run_init(project, "--new")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((project / "AGENTS.md").is_file())
            self.assertTrue((project / "CLAUDE.md").is_file())
            self.assertTrue((project / "00_PROJECT_INDEX.md").is_file())
            self.assertTrue((project / "README.md").is_file())
            planning = project / "00.项目规划与治理" / "task-one"
            self.assertTrue(planning.is_dir())
            for name in ("WORKFLOW_CHECKLIST.md", "1_master_plan.md", "2_execution_log.md", "3_status_update.md", "4_handoff.md", "5_audit.md", "task_plan.md", "findings.md", "progress.md", "CONTEXT.md"):
                self.assertTrue((planning / name).is_file(), name)
                self.assertFalse((project / name).exists(), name)
            for name in ("ADR", "evidence", "reports"):
                self.assertTrue((planning / name).is_dir(), name)
            index = (project / "00_PROJECT_INDEX.md").read_text(encoding="utf-8")
            self.assertIn("00.项目规划与治理/task-one/WORKFLOW_CHECKLIST.md", index)
            self.assertIn("00.项目规划与治理/task-one/task_plan.md", index)
            self.assertTrue((project / "00.项目规划与治理" / "task-index.yaml").is_file())

    def test_adopt_legacy_does_not_automatically_migrate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pwf-adopt-") as directory:
            project = Path(directory) / "legacy"
            project.mkdir()
            (project / "1_master_plan.md").write_text("legacy plan\n", encoding="utf-8")
            result = self.run_init(project, "--adopt")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((project / "1_master_plan.md").read_text(encoding="utf-8"), "legacy plan\n")
            self.assertFalse((project / "00.项目规划与治理").exists())
            self.assertTrue((project / "WORKFLOW_CHECKLIST.md").is_file())

    def test_conflict_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pwf-conflict-") as directory:
            project = Path(directory) / "conflict"
            project.mkdir()
            (project / "1_master_plan.md").write_text("legacy\n", encoding="utf-8")
            (project / "00.项目规划与治理").mkdir()
            (project / "00.项目规划与治理" / "1_master_plan.md").write_text("new\n", encoding="utf-8")
            result = self.run_init(project, "--adopt")
            self.assertEqual(result.returncode, 3)
            self.assertIn("LAYOUT_CONFLICT", result.stderr)

    def test_migrate_layout_cli_defaults_to_dry_run_and_apply_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pwf-cli-migrate-") as directory:
            project = Path(directory) / "legacy"
            project.mkdir()
            (project / "1_master_plan.md").write_text("legacy\n", encoding="utf-8")
            preview = self.run_init(project, "migrate-layout")
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertIn('"mode": "DRY_RUN"', preview.stdout)
            self.assertTrue((project / "1_master_plan.md").exists())
            apply_result = self.run_init(project, "migrate-layout", "--apply", "--confirm")
            self.assertEqual(apply_result.returncode, 0, apply_result.stderr)
            self.assertTrue((project / "00.项目规划与治理" / "1_master_plan.md").is_file())
            self.assertFalse((project / "1_master_plan.md").exists())

    def test_subdirectory_catchup_and_completion_resolve_parent_project(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pwf-child-") as directory:
            project = Path(directory) / "project"
            result = self.run_init(project, "--new")
            self.assertEqual(result.returncode, 0, result.stderr)
            child = project / "00.项目规划与治理" / "task-one" / "evidence"
            catchup = subprocess.run([sys.executable, str(CATCHUP), str(child)], text=True, capture_output=True)
            self.assertEqual(catchup.returncode, 0, catchup.stderr)
            self.assertIn(f"Root: {project.resolve()}", catchup.stdout)
            self.assertIn("Core governance files: 13/13", catchup.stdout)
            check = subprocess.run([sys.executable, str(CHECK), "--root", str(child), "--audit-level", "A0"], text=True, capture_output=True)
            self.assertEqual(check.returncode, 4)
            self.assertNotIn("missing 1_master_plan.md", check.stdout)


if __name__ == "__main__":
    unittest.main()
