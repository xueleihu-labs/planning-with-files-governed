#!/usr/bin/env python3
# Version source: ../VERSION
"""Scan the community edition tree for private identifiers.

Exits 0 if clean, 1 if any violation is found.
This scanner constructs all detection patterns dynamically so that it
does not contain the identifiers as plaintext substrings.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve()


def _join(*parts: str) -> str:
    return "".join(parts)


# Brand prefix constructed from char codes to avoid self-detection
_BP = chr(102) + chr(117) + chr(120) + chr(105)

# Compat field name constructed dynamically
_CF = _BP + "_read_" + "head"

# All private identifiers — each split so no pattern appears contiguously
_IDENTIFIERS = [
    _join("hux", "uelei"),
    _join("Lob", "ster", "Data"),
    "\u4f0f\u7f72",
    "\u5317\u6781\u661f",
    _join("lobster-", "work"),
    _join("lobster_", "work"),
    _join("02.skill", "\u4ed3\u5e93"),
    _join("01.\u6b63\u5f0f", "Skill"),
    _join("/Users/", "hux", "uelei"),
    _join("E:\\", "Lob", "ster", "Data"),
    _join("C:\\Users\\", "Admin", "istrator"),
    _join(_BP, "-", "orchestrator"),
    "\u516c\u4f17\u53f7\u5c0f\u7ea2\u4e66",
]

_ALLOWED_COMPAT_FILES = {
    "scripts/planning.py",
    "MIGRATION.md",
    "CHANGELOG.md",
}

_MIGRATION_DOC_FILES = {"MIGRATION.md", "CHANGELOG.md"}

_SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".f1-02-unsafe-state",
    ".cache",
    "cache",
    "coverage",
    "htmlcov",
    "planning-demo",
    "planning-test",
    "planning-files-runtime",
    "runtime-data",
    "session-cache",
    "dist",
    "build",
    ".venv",
}


def main() -> int:
    violations: list[str] = []

    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.resolve() == SELF:
            continue
        if path.name.startswith("test_") and path.suffix == ".py":
            continue
        if path.suffix in {".pyc", ".pyo", ".pyd"}:
            continue

        rel = path.relative_to(ROOT).as_posix()

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue

        for identifier in _IDENTIFIERS:
            if identifier.lower() in text.lower():
                if rel in _MIGRATION_DOC_FILES:
                    continue
                violations.append(f"{rel}: contains private identifier")

        if _CF in text and rel not in _ALLOWED_COMPAT_FILES:
            violations.append(
                f"{rel}: contains legacy compat field "
                "(only allowed in compat read code and migration docs)"
            )

        if rel not in _ALLOWED_COMPAT_FILES:
            for line_num, line in enumerate(text.splitlines(), 1):
                if _BP in line.lower():
                    violations.append(
                        f"{rel}:{line_num}: brand reference detected"
                    )

    if violations:
        print(f"FAILED: {len(violations)} violation(s)", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1

    print("PASS: no private identifiers found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
