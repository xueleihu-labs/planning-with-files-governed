#!/usr/bin/env python3
"""Verify that project initialization works without --rule-profile (implicit base mode)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


class TestDefaultBaseProfileInitializes(unittest.TestCase):
    """Verify initialization works in implicit base mode."""

    def test_new_project_initializes_without_rule_profile(self) -> None:
        """A new project should initialize successfully without any rule profile."""
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "test-project"
            project.mkdir(parents=True)
            env = os.environ.copy()
            env["PWF_ROOT"] = directory
            result = subprocess_run(
                [sys.executable, str(SCRIPTS_DIR / "project_init.py"),
                 "--new", "--project-root", str(project),
                 "--skill-root", directory,
                 "--task-id", "test-task-001"],
                env=env, capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            # Verify governance files were created
            self.assertTrue((project / "AGENTS.md").exists())
            self.assertTrue((project / "00_PROJECT_INDEX.md").exists())

    def test_adopt_project_works_without_rule_profile(self) -> None:
        """Adopting an existing project should work without rule profile."""
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "adopt-project"
            project.mkdir(parents=True)
            # Create a minimal existing project
            (project / "AGENTS.md").write_text("# Existing rules\n", encoding="utf-8")
            env = os.environ.copy()
            env["PWF_ROOT"] = directory
            result = subprocess_run(
                [sys.executable, str(SCRIPTS_DIR / "project_init.py"),
                 "--adopt", "--project-root", str(project),
                 "--skill-root", directory],
                env=env, capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)


def subprocess_run(*args, **kwargs):
    import subprocess
    return subprocess.run(*args, **kwargs)


if __name__ == "__main__":
    unittest.main()
