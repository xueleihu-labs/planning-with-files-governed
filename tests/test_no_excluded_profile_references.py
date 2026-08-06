#!/usr/bin/env python3
"""Verify no references to excluded profile modules exist in the codebase."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXCLUDED_REFERENCES = [
    "lobster-work",
    "lobster_work",
    "rule_profiles",
    "rule_evolution",
    "LOBSTER_RULE_PROFILE",
    "LOBSTER_INDEX_MANAGER",
    "import rule_profiles",
    "import rule_evolution",
    "resolve_index_manager",
]


class TestNoExcludedProfileReferences(unittest.TestCase):
    """Verify no dangling references to excluded modules exist."""

    def test_no_excluded_profile_references_in_scripts(self) -> None:
        """Scripts should not reference excluded modules."""
        violations: list[str] = []
        scripts_dir = ROOT / "scripts"
        for path in scripts_dir.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for ref in EXCLUDED_REFERENCES:
                if ref in text:
                    violations.append(f"scripts/{path.name}: contains '{ref}'")
        self.assertEqual(violations, [], "Excluded profile references found:\n" + "\n".join(violations))

    def test_no_excluded_profile_references_in_tests(self) -> None:
        """Test files (except this one) should not reference excluded modules."""
        violations: list[str] = []
        tests_dir = ROOT / "tests"
        for path in tests_dir.glob("*.py"):
            if path.name.startswith("test_no_excluded") or path.name.startswith("test_implicit") or path.name.startswith("test_default_base") or path.name.startswith("test_community"):
                continue  # these test files reference excluded modules in their assertions
            text = path.read_text(encoding="utf-8")
            for ref in EXCLUDED_REFERENCES:
                if ref in text:
                    violations.append(f"tests/{path.name}: contains '{ref}'")
        self.assertEqual(violations, [], "Excluded profile references found:\n" + "\n".join(violations))

    def test_no_excluded_profile_references_in_templates(self) -> None:
        """Templates should not reference excluded modules."""
        violations: list[str] = []
        templates_dir = ROOT / "templates"
        for path in templates_dir.rglob("*"):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for ref in EXCLUDED_REFERENCES:
                if ref in text:
                    violations.append(f"templates/{path.relative_to(ROOT)}: contains '{ref}'")
        self.assertEqual(violations, [], "Excluded profile references found:\n" + "\n".join(violations))


if __name__ == "__main__":
    unittest.main()
