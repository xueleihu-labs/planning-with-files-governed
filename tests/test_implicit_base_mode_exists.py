#!/usr/bin/env python3
"""Verify that base is an implicit default mode with no profile file dependency."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestImplicitBaseModeExists(unittest.TestCase):
    """Verify base mode is implicit (no file-based profile)."""

    def test_no_profiles_directory(self) -> None:
        """The templates/profiles/ directory should not exist (personal profiles excluded)."""
        profiles_dir = ROOT / "templates" / "profiles"
        self.assertFalse(profiles_dir.exists(), "templates/profiles/ should not exist in community edition")

    def test_no_rule_profiles_module(self) -> None:
        """The rule_profiles.py module should not exist (personal rule system excluded)."""
        rule_profiles = ROOT / "scripts" / "rule_profiles.py"
        self.assertFalse(rule_profiles.exists(), "scripts/rule_profiles.py should not exist")

    def test_no_rule_evolution_module(self) -> None:
        """The rule_evolution.py module should not exist (personal rule system excluded)."""
        rule_evolution = ROOT / "scripts" / "rule_evolution.py"
        self.assertFalse(rule_evolution.exists(), "scripts/rule_evolution.py should not exist")

    def test_project_init_has_no_rule_profile_arg(self) -> None:
        """project_init.py should not have --rule-profile argument."""
        init_script = ROOT / "scripts" / "project_init.py"
        text = init_script.read_text(encoding="utf-8")
        self.assertNotIn("--rule-profile", text, "project_init.py should not have --rule-profile argument")
        self.assertNotIn("--rule-sync", text, "project_init.py should not have --rule-sync argument")

    def test_project_init_has_no_rule_profiles_import(self) -> None:
        """project_init.py should not import rule_profiles."""
        init_script = ROOT / "scripts" / "project_init.py"
        text = init_script.read_text(encoding="utf-8")
        self.assertNotIn("import rule_profiles", text, "project_init.py should not import rule_profiles")


if __name__ == "__main__":
    unittest.main()
