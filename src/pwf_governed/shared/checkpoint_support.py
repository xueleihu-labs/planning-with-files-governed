"""Shared low-level checkpoint path and reference helpers."""
from __future__ import annotations

from pwf_governed.core.constants import (
    CHECKPOINT_REFS_DIR,
    SKILL_ROOT,
)
from pwf_governed.core.envelope import (
    _read_json,
)
from pwf_governed.core.errors import (
    PlanningError,
)

def _state_relative_path(state_root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(state_root.resolve(strict=False)).as_posix()
    except ValueError:
        return str(path.resolve(strict=False))

def _resolve_checkpoint_file(
    state_root: Path,
    instance: Path,
    raw_path: str,
    *,
    label: str,
) -> tuple[Path, str]:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise PlanningError("INVALID_CHECKPOINT_REF", f"{label} must be a non-empty path")
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = instance / candidate
    if candidate.is_symlink():
        raise PlanningError("CHECKPOINT_PATH_NOT_ALLOWED", f"{label} cannot be a symlink")
    resolved = candidate.resolve(strict=False)
    state = state_root.resolve(strict=False)
    project = SKILL_ROOT.resolve()
    if resolved == project or project in resolved.parents:
        raise PlanningError("CHECKPOINT_PATH_NOT_ALLOWED", f"{label} points into planning-with-files")
    if ".git" in resolved.parts or (resolved != state and state not in resolved.parents):
        raise PlanningError("CHECKPOINT_PATH_NOT_ALLOWED", f"{label} is outside the external state-root")
    if not resolved.is_file():
        raise PlanningError("CHECKPOINT_RECEIPT_NOT_FOUND" if label == "receipt_location" else "CHECKPOINT_EVIDENCE_NOT_FOUND", f"{label} does not exist: {resolved}")
    try:
        resolved.read_bytes()
    except OSError as exc:
        raise PlanningError("CHECKPOINT_RECEIPT_NOT_READABLE" if label == "receipt_location" else "CHECKPOINT_EVIDENCE_NOT_READABLE", f"cannot read {label}: {resolved}: {exc}") from exc
    return resolved, _state_relative_path(state_root, resolved)

def _load_checkpoint_refs(instance: Path) -> list[dict[str, Any]]:
    directory = instance / CHECKPOINT_REFS_DIR
    if not directory.exists():
        return []
    if directory.is_symlink() or not directory.is_dir():
        raise PlanningError("UNSAFE_INSTANCE_ROOT", "checkpoint refs directory must be a real directory")
    refs: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        if path.is_symlink():
            raise PlanningError("UNSAFE_INSTANCE_ROOT", f"checkpoint ref cannot be a symlink: {path}")
        value = _read_json(path, code="INVALID_CHECKPOINT_REF")
        try:
            contracts.validate_checkpoint_ref(value)
        except workflow.ContractError as exc:
            raise PlanningError("INVALID_CHECKPOINT_REF", f"invalid stored checkpoint ref {path}: {exc}") from exc
        refs.append(value)
    return refs

def _checkpoint_external_file(state_root: Path, instance: Path, raw: Any, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise PlanningError("MIDCOURSE_CHECKPOINT_CHAIN_INVALID", f"{label} is missing")
    try:
        path, _relative = _resolve_checkpoint_file(state_root, instance, raw, label=label)
    except PlanningError as exc:
        raise PlanningError("MIDCOURSE_CHECKPOINT_CHAIN_INVALID", f"{label}: {exc.code}") from exc
    return path
