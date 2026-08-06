#!/usr/bin/env python3
"""Resolve the skill root directory without user-specific fallbacks."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Union


PathLike = Union[str, os.PathLike[str]]


class RootResolutionError(RuntimeError):
    """Raised when no explicit or trusted skill root is available."""


def _normalise(raw: PathLike) -> Path:
    return Path(raw).expanduser().resolve()


def _is_inside(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


def _trusted_root(candidate: Path, source: Path) -> bool:
    """Check if candidate contains a valid skill layout with scripts/ and VERSION."""
    skill_root = candidate
    return (
        (skill_root / "scripts").is_dir()
        and (skill_root / "VERSION").is_file()
        and _is_inside(source, skill_root / "scripts")
    )


def discover_skill_root(script_path: Optional[PathLike] = None) -> Path:
    """Find the skill root containing this script's stable layout."""

    source = _normalise(script_path or __file__)
    start = source if source.is_dir() else source.parent
    for candidate in (start,) + tuple(start.parents):
        if _trusted_root(candidate, source):
            return candidate
    raise RootResolutionError(
        "cannot resolve skill root; set PWF_ROOT or pass "
        "--skill-root, and ensure the script is inside a valid "
        "planning-with-files-governed checkout"
    )


def resolve_skill_root(
    explicit: Optional[PathLike] = None,
    *,
    script_path: Optional[PathLike] = None,
) -> Path:
    """Resolve explicit input, environment configuration, then trusted layout."""

    if explicit is not None and str(explicit).strip():
        return _normalise(explicit)
    configured = os.environ.get("PWF_ROOT")
    if configured and configured.strip():
        return _normalise(configured)
    return discover_skill_root(script_path)
