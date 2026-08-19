#!/usr/bin/env python3
"""Verify no production absolute paths exist in the community edition."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PRODUCTION_PATH_PATTERNS = [
    "/Users/huxuelei",
    "/Users/huxuelei/",
    "E:\\LobsterData",
    "C:\\Users\\Administrator",
    "02.skill仓库",
    "01.正式Skill",
]

GENERIC_PLACEHOLDERS = [
    "/Users/<username>",
    "C:\\Users\\<username>",
    "/path/to/project",
    "<username>",
    "<security-contact>",
    "<your-token>",
]


class TestNoProductionAbsolutePaths(unittest.TestCase):
    """Scan all files for hardcoded production absolute paths."""

    def test_no_production_absolute_paths(self) -> None:
        violations: list[str] = []
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            if any(part in {".git", "__pycache__", ".pytest_cache", ".f1-02-unsafe-state"} for part in path.parts):
                continue
            if path.name.startswith("test_") and path.suffix == ".py":
                continue  # test files contain patterns for scanning, not leaks
                continue
            if path.suffix in {".pyc", ".pyo", ".pyd"}:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                continue
            for pattern in PRODUCTION_PATH_PATTERNS:
                if pattern in text:
                    # Allow generic placeholders
                    if any(ph in text for ph in GENERIC_PLACEHOLDERS):
                        continue
                    violations.append(f"{path.relative_to(ROOT)}: contains '{pattern}'")

        self.assertEqual(violations, [], "Production absolute paths found:\n" + "\n".join(violations))


if __name__ == "__main__":
    unittest.main()
