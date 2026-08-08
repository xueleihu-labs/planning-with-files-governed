"""Gate 2 extracted module: shared/evidence.py.

Generated from the Gate 1 planning.py baseline.
"""
from __future__ import annotations

from pathlib import Path

from pwf_governed.core.constants import (
    SKILL_ROOT,
)

def _final_evidence_file(state_root: Path, instance: Path, raw: str) -> Path | None:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = instance / candidate
    if candidate.is_symlink():
        return None
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        return None
    if ".git" in resolved.parts or resolved == SKILL_ROOT.resolve() or SKILL_ROOT.resolve() in resolved.parents:
        return None
    state = state_root.resolve(strict=False)
    if resolved != state and state not in resolved.parents:
        return None
    return resolved if resolved.is_file() else None
