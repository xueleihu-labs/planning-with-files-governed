#!/usr/bin/env python3
"""Small, read-only checkpoint compatibility boundary.

``planning-with-files`` consumes published checkpoint heads but does not own
the checkpoint engine.  The old implementation imported that reader from a
neighbouring Skill, which made this otherwise portable Skill fail when the
neighbour was not installed.  This module keeps only the public reader
contract needed here: isolated runtime-root resolution and fail-closed head
verification.  Writers remain outside this Skill.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional


class CheckpointError(RuntimeError):
    """Expected checkpoint reader failure."""


def _default_state_root() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "planning-with-files-governed"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "planning-with-files-governed"
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))) / "planning-with-files-governed"


def runtime_state_root(root: Path, state_root: Optional[str | Path] = None) -> Path:
    """Resolve an explicit external runtime root and reject worktree paths."""
    selected = state_root if state_root is not None else os.environ.get("PHASE_CHECKPOINT_STATE_ROOT") or _default_state_root()
    candidate = Path(selected).expanduser()
    if not candidate.is_absolute():
        raise CheckpointError("RUNTIME_DIR_NOT_ISOLATED")
    candidate = candidate.resolve(strict=False)
    project_root = Path(root).resolve(strict=False)
    if candidate == project_root or project_root in candidate.parents:
        raise CheckpointError("RUNTIME_DIR_NOT_ISOLATED")
    return candidate


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: Any) -> str:
    data = value if isinstance(value, bytes) else _canonical(value)
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CheckpointError(f"unreadable JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CheckpointError(f"JSON object required: {path}")
    return value


def _content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _store(root: Path, state_root: Optional[str | Path] = None) -> Path:
    resolved_root = Path(root).resolve(strict=False)
    repository = _sha256(str(resolved_root))[:24]
    worktree = _sha256(str(resolved_root))[:24]
    return runtime_state_root(resolved_root, state_root) / "phase-checkpoints" / repository / worktree


def _ensure_store(root: Path, state_root: Optional[str | Path] = None) -> dict[str, Path]:
    base = _store(root, state_root)
    paths = {name: base / name for name in ("commits", "heads", "artifacts")}
    # The authoritative engine creates these directories before writing.  A
    # read must preserve that behavior so missing heads remain deterministic.
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def _fail_closed(reason: str) -> dict[str, Any]:
    return {"effective_action": "BLOCKED", "reason": reason, "source": "FAIL_CLOSED_READER"}


def _valid_identifier(value: str) -> bool:
    if not isinstance(value, str) or not value or len(value) > 80 or ".." in value:
        return False
    return all(char.isalnum() or char in "._-" for char in value)


def read_head(root: Path, task_id: str, phase_id: str, state_root: Optional[str | Path] = None) -> dict[str, Any]:
    """Read a published head and verify its commit, result and seal hashes."""
    if not _valid_identifier(task_id) or not _valid_identifier(phase_id):
        raise CheckpointError("invalid task_id or phase_id")
    paths = _ensure_store(Path(root), state_root)
    head_path = paths["heads"] / f"{task_id}-{phase_id}.json"
    if not head_path.exists():
        return _fail_closed("UNTRUSTED_CHECKPOINT_HEAD")
    try:
        head = _read_json(head_path)
        commit_id = head["commit_id"]
        if not isinstance(commit_id, str) or not _valid_identifier(commit_id):
            return _fail_closed("UNTRUSTED_CHECKPOINT_HEAD")
        commit_path = paths["commits"] / f"{commit_id}.commit.json"
        commit = _read_json(commit_path)
        if _sha256(commit) != head.get("commit_hash"):
            return _fail_closed("UNTRUSTED_CHECKPOINT_HEAD")
        result_path = paths["artifacts"] / commit_id / "result.json"
        if not result_path.exists() or _content_hash(result_path) != commit.get("result_hash"):
            return _fail_closed("UNTRUSTED_CHECKPOINT_HEAD")
        manifest_hash = commit.get("phase_seal_manifest_hash")
        if manifest_hash:
            manifest_path = paths["artifacts"] / commit_id / "phase-seal-manifest.json"
            if not manifest_path.exists() or _content_hash(manifest_path) != manifest_hash:
                return _fail_closed("UNTRUSTED_CHECKPOINT_HEAD")
        return {
            "effective_action": commit["effective_action"],
            "source": "PUBLISHED_COMMIT",
            "commit_id": commit_id,
            "head_version": head.get("head_version"),
            "commit_sequence": head.get("commit_sequence"),
            "commit": commit,
        }
    except (CheckpointError, KeyError, TypeError):
        return _fail_closed("UNTRUSTED_CHECKPOINT_HEAD")


__all__ = ["CheckpointError", "read_head", "runtime_state_root"]
