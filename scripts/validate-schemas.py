#!/usr/bin/env python3
# Version source: ../VERSION

"""Validate all JSON Schema files in the schemas/ directory.

Checks that every .schema.json file:
  1. Is valid JSON
  2. Has a $schema field pointing at a JSON Schema draft
  3. Has a $id or title field identifying the schema
  4. Has a type, oneOf, or allOf top-level keyword

Exits 0 on success, 1 on any validation failure.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = ROOT / "schemas"

REQUIRED_KEYS = {"$schema"}
IDENTIFIER_KEYS = {"$id", "title"}
TYPE_KEYS = {"type", "oneOf", "allOf", "anyOf"}


def validate_schema(path: Path) -> list[str]:
    """Return a list of error messages for a schema file (empty = valid)."""
    errors: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{path.name}: invalid JSON: {exc}"]
    except OSError as exc:
        return [f"{path.name}: cannot read: {exc}"]

    if not isinstance(data, dict):
        return [f"{path.name}: schema root must be an object"]

    for key in REQUIRED_KEYS:
        if key not in data:
            errors.append(f"{path.name}: missing required key '{key}'")

    if not any(key in data for key in IDENTIFIER_KEYS):
        errors.append(f"{path.name}: missing $id or title")

    if not any(key in data for key in TYPE_KEYS):
        errors.append(f"{path.name}: missing type, oneOf, allOf, or anyOf")

    return errors


def main() -> int:
    if not SCHEMAS_DIR.is_dir():
        print(f"ERROR: schemas directory not found: {SCHEMAS_DIR}", file=sys.stderr)
        return 1

    schema_files = sorted(SCHEMAS_DIR.glob("*.schema.json"))
    if not schema_files:
        print("ERROR: no .schema.json files found", file=sys.stderr)
        return 1

    all_errors: list[str] = []
    for path in schema_files:
        errors = validate_schema(path)
        if errors:
            all_errors.extend(errors)
        else:
            print(f"  OK  {path.name}")

    if all_errors:
        print(f"\nFAILED: {len(all_errors)} error(s)", file=sys.stderr)
        for err in all_errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(f"\nPASS: {len(schema_files)} schema(s) validated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
