#!/usr/bin/env python3
"""Verify no private identifiers exist in the community edition tree."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Personal identifiers that must never appear (except in compat read code)
PRIVATE_IDENTIFIERS = [
    "huxuelei",
    "LobsterData",
    "伏羲",
    "北极星",
    "lobster-work",
    "lobster_work",
    "02.skill仓库",
    "01.正式Skill",
    "/Users/huxuelei",
    "E:\\LobsterData",
    "C:\\Users\\Administrator",
    "fuxi-orchestrator",
    "公众号小红书",
]

# fuxi_read_head is allowed ONLY in compatibility read code and MIGRATION.md
ALLOWED_FUXI_FILES = {
    "scripts/planning.py",  # compatibility read: data.get("external_read_head", data.get("fuxi_read_head"))
    "MIGRATION.md",         # migration documentation (old field names documented for migration)
    "CHANGELOG.md",         # changelog documents what was renamed/removed
}

# Files where migration-related identifiers (old field names, old terms) are allowed
# for documenting what changed, not for active use
MIGRATION_DOC_FILES = {"MIGRATION.md", "CHANGELOG.md"}


class TestCommunityTreeContainsNoPrivateIdentifiers(unittest.TestCase):
    """Scan all files for private identifiers."""

    def test_no_private_identifiers(self) -> None:
        violations: list[str] = []
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            if any(part in {".git", "__pycache__", ".pytest_cache", ".f1-02-unsafe-state"} for part in path.parts):
                continue
            if path.name.startswith("test_") and path.suffix == ".py":
                continue  # test files contain identifiers for scanning, not leaks
                continue
            if path.suffix in {".pyc", ".pyo", ".pyd"}:
                continue
            rel = path.relative_to(ROOT).as_posix()
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                continue

            for identifier in PRIVATE_IDENTIFIERS:
                if identifier.lower() in text.lower():
                    # Allow migration documentation files to reference old identifiers
                    if rel in MIGRATION_DOC_FILES:
                        continue
                    violations.append(f"{rel}: contains '{identifier}'")

            # Special check for fuxi_read_head: only allowed in specific files
            if "fuxi_read_head" in text and rel not in ALLOWED_FUXI_FILES:
                violations.append(f"{rel}: contains 'fuxi_read_head' (only allowed in compat read code and MIGRATION.md)")

            # Check for fuxi (brand) - only allowed in compat read code and migration docs
            if rel not in ALLOWED_FUXI_FILES:
                for line_num, line in enumerate(text.splitlines(), 1):
                    if "fuxi" in line.lower():
                        violations.append(f"{rel}:{line_num}: fuxi brand reference: {line.strip()[:80]}")

        self.assertEqual(violations, [], "Private identifiers found:\n" + "\n".join(violations))


if __name__ == "__main__":
    unittest.main()
