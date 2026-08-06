#!/usr/bin/env python3
"""Check that active planning-with-files assets use the VERSION source."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def require(path: Path, pattern: str) -> str | None:
    text = path.read_text(encoding="utf-8", errors="replace")
    if not re.search(pattern, text, re.MULTILINE):
        return f"{path.relative_to(ROOT)} missing {pattern}"
    return None


def main() -> int:
    errors = []
    errors.append(require(ROOT / "SKILL.md", rf"^version:\s*{re.escape(VERSION)}\s*$"))
    errors.append(require(ROOT / "README.md", rf"v{re.escape(VERSION)}"))
    errors.append(require(ROOT / "CHANGELOG.md", rf"^##\s+{re.escape(VERSION)}"))
    for path in (ROOT / "templates").glob("*.md"):
        if path.name == "INDEX.md":
            continue
        errors.append(require(path, r"Template version source: VERSION"))
    for path in (ROOT / "scripts").glob("*.sh"):
        errors.append(require(path, r"Version source: ../VERSION"))
    for path in (ROOT / "scripts").glob("*.ps1"):
        errors.append(require(path, r"Version source: ../VERSION"))
    for path in (ROOT / "scripts").glob("*.py"):
        if path.name != "check-version.py":
            errors.append(require(path, r"VERSION|version"))
    errors = [error for error in errors if error]
    if errors:
        print("VERSION CHECK FAIL")
        print("\n".join("- " + error for error in errors))
        return 1
    print(f"VERSION CHECK PASS: {VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
