#!/usr/bin/env python3
"""Single, fail-closed planning-layout resolver and migration engine.

The local governance contract remains authoritative.  This module only answers
where the planning artifacts live and performs an explicit, reversible layout
migration; it does not decide task ownership or completion.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, Mapping


CANONICAL_DIR_NAME = "00.项目规划与治理"
TASK_INDEX_NAME = "task-index.yaml"
LEGACY_LAYOUT_VERSION = 2
LAYOUT_VERSION = 3
LAYOUT_CONFIG_FILE = ".planning-layout.json"
LAYOUT_MARKER_BEGIN = "<!-- PLANNING-WITH-FILES-LAYOUT:BEGIN -->"
LAYOUT_MARKER_END = "<!-- PLANNING-WITH-FILES-LAYOUT:END -->"

CHECKLIST_NAME = "WORKFLOW_CHECKLIST.md"
PLANNING_DOCUMENTS = (
    CHECKLIST_NAME,
    "1_master_plan.md",
    "2_execution_log.md",
    "3_status_update.md",
    "4_handoff.md",
    "5_audit.md",
    "task_plan.md",
    "findings.md",
    "progress.md",
    "CONTEXT.md",
)
ENTRY_DOCUMENTS = ("AGENTS.md", "CLAUDE.md", "00_PROJECT_INDEX.md", "README.md")
PLANNING_DIRS = ("ADR", "evidence", "handoffs", "reports", "scripts")
PLAN_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")


class LayoutError(ValueError):
    """Base error with a stable, user-facing fail-closed code."""

    def __init__(self, code: str, message: str, *, details: object | None = None) -> None:
        self.code = code
        self.details = details
        super().__init__(f"{code}: {message}")


class LayoutConflict(LayoutError):
    def __init__(self, message: str, *, details: object | None = None) -> None:
        super().__init__("LAYOUT_CONFLICT", message, details=details)


class UnsafeLayoutPath(LayoutError):
    def __init__(self, message: str, *, details: object | None = None) -> None:
        super().__init__("UNSAFE_LAYOUT_PATH", message, details=details)


class MigrationConflict(LayoutError):
    def __init__(self, message: str, *, details: object | None = None) -> None:
        super().__init__("MIGRATION_CONFLICT", message, details=details)


@dataclasses.dataclass(frozen=True)
class Layout:
    project_root: Path
    planning_dir: Path
    source: str
    root_index: Path
    explicit: bool = False
    task_id: str | None = None
    planning_root: Path | None = None

    @property
    def is_legacy(self) -> bool:
        return self.planning_dir == self.project_root

    @property
    def is_canonical(self) -> bool:
        return (
            not self.is_legacy
            and self.planning_root is not None
            and self.planning_root.name == CANONICAL_DIR_NAME
        )

    @property
    def is_task_scoped(self) -> bool:
        return self.task_id is not None

    def path(self, name: str) -> Path:
        if name in PLANNING_DOCUMENTS:
            return self.planning_dir / name
        if name in ENTRY_DOCUMENTS:
            return self.project_root / name
        raise ValueError(f"unknown planning artifact: {name}")

    def managed_paths(self) -> dict[str, Path]:
        return {name: self.path(name) for name in PLANNING_DOCUMENTS}

    def to_dict(self) -> dict[str, object]:
        return {
            "layout_version": LAYOUT_VERSION,
            "project_root": str(self.project_root),
            "planning_dir": str(self.planning_dir),
            "planning_directory": self.planning_dir.name if not self.is_legacy else ".",
            "source": self.source,
            "root_index": str(self.root_index),
            "explicit": self.explicit,
            "legacy": self.is_legacy,
            "task_id": self.task_id,
            "planning_root": str(self.planning_root) if self.planning_root else None,
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _within(root: Path, candidate: Path) -> bool:
    try:
        _canonical(candidate).relative_to(_canonical(root))
    except ValueError:
        return False
    return True


def _component_links(root: Path, candidate: Path) -> list[str]:
    """Return symlinked components, including a link used as the directory."""
    root_real = _canonical(root)
    lexical = candidate.expanduser()
    if not lexical.is_absolute():
        lexical = root_real / lexical
    try:
        relative = lexical.relative_to(root_real)
    except ValueError:
        return [str(lexical)]
    current = root_real
    found: list[str] = []
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            found.append(str(current))
    return found


def validate_contained_path(project_root: Path, candidate: Path, *, allow_missing: bool = True) -> Path:
    root = _canonical(project_root)
    resolved = _canonical(candidate if candidate.is_absolute() else root / candidate)
    if not _within(root, resolved):
        raise UnsafeLayoutPath(
            f"path escapes project root: {candidate}",
            details={"project_root": str(root), "candidate": str(candidate), "resolved": str(resolved)},
        )
    links = _component_links(root, candidate)
    if links:
        raise UnsafeLayoutPath("symlink/junction component is not accepted as a planning boundary", details=links)
    if not allow_missing and not resolved.exists():
        raise UnsafeLayoutPath(f"path does not exist: {resolved}")
    return resolved


def validate_plan_id(value: str) -> str:
    if not isinstance(value, str) or not value or not PLAN_ID_RE.fullmatch(value) or ".." in value:
        raise LayoutError("UNSAFE_PLAN_ID", "PLAN_ID must be one safe path segment without traversal")
    return value


def validate_task_id(value: str) -> str:
    """Validate the visible task directory name used by layout v3."""
    try:
        return validate_plan_id(value)
    except LayoutError as exc:
        raise LayoutError("UNSAFE_TASK_ID", "task_id must be one safe path segment without traversal") from exc


def task_index_path(project_root: Path) -> Path:
    return _canonical(project_root) / CANONICAL_DIR_NAME / TASK_INDEX_NAME


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def read_task_index(project_root: Path, *, require: bool = False) -> dict[str, str]:
    """Read the dependency-free JSON-compatible YAML task index.

    The file intentionally uses JSON syntax while retaining the `.yaml` name:
    it is valid YAML 1.2, human-readable, and needs no third-party parser on
    Python 3.10/macOS/Windows/WSL.
    """
    path = task_index_path(project_root)
    if not path.exists():
        if require:
            raise LayoutError("TASK_INDEX_MISSING", f"task index not found: {path}")
        return {}
    if path.is_symlink() or not path.is_file():
        raise UnsafeLayoutPath(f"task index is not a regular file: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LayoutError("INVALID_TASK_INDEX", f"cannot read task index: {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") not in {1, "1.0.0"}:
        raise LayoutError("INVALID_TASK_INDEX", f"unsupported task index schema: {path}")
    rows = payload.get("tasks")
    if not isinstance(rows, list):
        raise LayoutError("INVALID_TASK_INDEX", f"tasks must be a list: {path}")
    result: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"task_id", "path"}:
            raise LayoutError("INVALID_TASK_INDEX", f"each task entry must contain only task_id and path: {path}")
        task_id = validate_task_id(row.get("task_id"))
        relative = row.get("path")
        if not isinstance(relative, str) or relative != task_id:
            raise LayoutError("INVALID_TASK_INDEX", f"task path must equal task_id: {path}")
        if task_id in result:
            raise LayoutError("TASK_INDEX_CONFLICT", f"duplicate task_id: {task_id}")
        validate_contained_path(project_root, _canonical(project_root) / CANONICAL_DIR_NAME / relative)
        result[task_id] = relative
    return result


def write_task_index(project_root: Path, tasks: Mapping[str, str]) -> Path:
    """Atomically write the discovery-only task index."""
    normalized: list[dict[str, str]] = []
    for task_id, relative in sorted(tasks.items()):
        safe = validate_task_id(task_id)
        if relative != safe:
            raise LayoutError("INVALID_TASK_INDEX", f"task path must equal task_id: {safe}")
        normalized.append({"task_id": safe, "path": safe})
    path = task_index_path(project_root)
    payload = {"schema_version": "1.0.0", "tasks": normalized}
    _atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return path


def register_task(project_root: Path, task_id: str) -> Path:
    """Register one task after its first managed document exists."""
    safe = validate_task_id(task_id)
    root = _canonical(project_root)
    container = root / CANONICAL_DIR_NAME
    candidate = validate_contained_path(root, container / safe, allow_missing=False)
    if not candidate.is_dir() or not any((candidate / name).is_file() for name in PLANNING_DOCUMENTS):
        raise LayoutError("TASK_NOT_READY", f"task has no managed planning document: {safe}")
    tasks = read_task_index(root, require=False)
    tasks[safe] = safe
    return write_task_index(root, tasks)


def discover_task_directories(project_root: Path) -> dict[str, Path]:
    """Return task directories and fail closed on index/disk drift."""
    root = _canonical(project_root)
    container = root / CANONICAL_DIR_NAME
    if not container.exists():
        return {}
    if container.is_symlink() or not container.is_dir():
        raise UnsafeLayoutPath(f"planning container is not a regular directory: {container}")
    index_file = task_index_path(root)
    indexed = read_task_index(root, require=False)
    discovered: dict[str, Path] = {}
    for child in sorted(container.iterdir(), key=lambda item: item.name):
        if child.name.startswith(".") or child.name == TASK_INDEX_NAME:
            continue
        if not child.is_dir():
            continue
        task_id = validate_task_id(child.name)
        if any((child / name).exists() for name in PLANNING_DOCUMENTS):
            discovered[task_id] = validate_contained_path(root, child, allow_missing=False)
    if index_file.exists():
        if set(indexed) != set(discovered):
            raise LayoutError(
                "TASK_INDEX_CONFLICT",
                "task index and task directories differ",
                details={"indexed": sorted(indexed), "discovered": sorted(discovered)},
            )
    elif discovered:
        raise LayoutError("TASK_INDEX_MISSING", f"task directories exist without {index_file}")
    return discovered


def git_boundary(start: Path) -> Path:
    """Find the nearest Git boundary without consulting parents above it."""
    current = _canonical(start)
    while True:
        marker = current / ".git"
        if marker.exists():
            return current
        if current.parent == current:
            return current
        current = current.parent


def discover_project_root(start: Path | None = None) -> Path:
    """Find a project root from a child directory, stopping at Git boundary."""
    current = _canonical(start or Path.cwd())
    if not current.is_dir():
        current = current.parent
    for ancestor in (current, *current.parents):
        if ancestor.name == CANONICAL_DIR_NAME:
            return ancestor.parent
    boundary = git_boundary(current)
    while True:
        if current.name == CANONICAL_DIR_NAME:
            return current.parent
        if _has_layout_signal(current):
            return current
        if current == boundary or current.parent == current:
            return current
        current = current.parent


def _has_layout_signal(root: Path) -> bool:
    if (root / CANONICAL_DIR_NAME).is_dir() or (root / LAYOUT_CONFIG_FILE).is_file():
        return True
    if any((root / name).exists() for name in PLANNING_DOCUMENTS):
        return True
    return (root / "00_PROJECT_INDEX.md").is_file() and (root / "AGENTS.md").is_file()


def _config_from_text(text: str, source: Path) -> str | None:
    marker_start = text.find(LAYOUT_MARKER_BEGIN)
    marker_end = text.find(LAYOUT_MARKER_END)
    if marker_start >= 0 and marker_end > marker_start:
        payload = text[marker_start + len(LAYOUT_MARKER_BEGIN):marker_end].strip()
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise LayoutError("INVALID_LAYOUT_CONFIG", f"invalid layout JSON in {source}: {exc}") from exc
        if not isinstance(value, dict):
            raise LayoutError("INVALID_LAYOUT_CONFIG", f"layout configuration must be an object: {source}")
        selected = value.get("planning_directory") or value.get("planning_dir")
        if selected is not None and not isinstance(selected, str):
            raise LayoutError("INVALID_LAYOUT_CONFIG", f"planning directory must be a string: {source}")
        return selected
    patterns = (
        r"(?im)^\s*(?:planning_directory|planning_dir|规划目录)\s*:\s*`?([^`\r\n]+?)`?\s*$",
        r"(?im)^\s*[-*]\s*\*\*(?:planning_directory|planning_dir|规划目录)\*\*\s*:\s*`?([^`\r\n]+?)`?\s*$",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return None


def configured_planning_dir(project_root: Path) -> str | None:
    index = project_root / "00_PROJECT_INDEX.md"
    if index.is_file():
        selected = _config_from_text(index.read_text(encoding="utf-8", errors="replace"), index)
        if selected:
            return selected
    config = project_root / LAYOUT_CONFIG_FILE
    if config.is_file():
        try:
            value = json.loads(config.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LayoutError("INVALID_LAYOUT_CONFIG", f"cannot read {config}: {exc}") from exc
        if not isinstance(value, dict) or not isinstance(value.get("planning_directory"), str):
            raise LayoutError("INVALID_LAYOUT_CONFIG", f"planning_directory missing in {config}")
        return value["planning_directory"]
    return None


def _candidate(root: Path, value: str, *, explicit: bool = False) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    return validate_contained_path(root, candidate, allow_missing=True)


def _has_managed_files(directory: Path) -> bool:
    return any((directory / name).exists() for name in PLANNING_DOCUMENTS)


def _snapshot(directory: Path) -> dict[str, dict[str, object]]:
    snapshot: dict[str, dict[str, object]] = {}
    for name in PLANNING_DOCUMENTS:
        path = directory / name
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_file():
            raise UnsafeLayoutPath(f"managed planning artifact is not a regular file: {path}")
        snapshot[name] = {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256_file(path)}
    return snapshot


def _compare_layouts(canonical_dir: Path, legacy_dir: Path) -> list[dict[str, object]]:
    left = _snapshot(canonical_dir)
    right = _snapshot(legacy_dir)
    conflicts: list[dict[str, object]] = []
    for name in sorted(set(left) | set(right)):
        if name not in left or name not in right:
            conflicts.append({"name": name, "canonical": left.get(name), "legacy": right.get(name), "reason": "presence differs"})
        elif left[name]["sha256"] != right[name]["sha256"]:
            conflicts.append({"name": name, "canonical": left[name], "legacy": right[name], "reason": "content differs"})
    return conflicts


def _root_index(root: Path) -> Path:
    preferred = root / "00_PROJECT_INDEX.md"
    return preferred if preferred.exists() else root / "INDEX.md"


def _layout_from_candidate(
    root: Path,
    candidate: Path,
    *,
    source: str,
    explicit: bool = False,
) -> Layout:
    """Build a Layout from a contained directory, including task scope."""
    canonical = root / CANONICAL_DIR_NAME
    if candidate == canonical:
        return Layout(root, candidate, source, _root_index(root), explicit, planning_root=canonical)
    try:
        relative = candidate.relative_to(canonical)
    except ValueError:
        return Layout(root, candidate, source, _root_index(root), explicit)
    if len(relative.parts) == 1:
        task_id = validate_task_id(relative.parts[0])
        tasks = discover_task_directories(root)
        if task_id not in tasks:
            raise LayoutError("TASK_NOT_FOUND", f"task_id is not registered: {task_id}")
        return Layout(root, candidate, source, _root_index(root), explicit, task_id=task_id, planning_root=canonical)
    return Layout(root, candidate, source, _root_index(root), explicit)


def _task_layout(root: Path, task_id: str, *, source: str, explicit: bool = False, allow_missing: bool = False) -> Layout:
    safe = validate_task_id(task_id)
    canonical = root / CANONICAL_DIR_NAME
    candidate = validate_contained_path(root, canonical / safe, allow_missing=allow_missing)
    if not allow_missing:
        tasks = discover_task_directories(root)
        if safe not in tasks:
            raise LayoutError("TASK_NOT_FOUND", f"task_id is not registered: {safe}")
    return Layout(root, candidate, source, _root_index(root), explicit, task_id=safe, planning_root=canonical)


def _task_id_from_start(root: Path, start: Path | None) -> str | None:
    if start is None:
        return None
    anchor = _canonical(start)
    if not anchor.is_dir():
        anchor = anchor.parent
    canonical = root / CANONICAL_DIR_NAME
    try:
        relative = anchor.relative_to(canonical)
    except ValueError:
        return None
    if not relative.parts or relative.parts[0].startswith(".") or relative.parts[0] == TASK_INDEX_NAME:
        return None
    candidate = canonical / relative.parts[0]
    if not candidate.is_dir():
        return None
    if not any((candidate / name).is_file() for name in PLANNING_DOCUMENTS):
        return None
    return validate_task_id(relative.parts[0])


def resolve_layout(
    project_root: Path | None = None,
    *,
    start: Path | None = None,
    planning_dir: str | Path | None = None,
    task_id: str | None = None,
    require: bool = False,
) -> Layout | None:
    """Resolve one layout, failing closed on task ambiguity and escapes."""
    root = _canonical(project_root or discover_project_root(start))
    if not root.exists():
        if require:
            raise LayoutError("PROJECT_ROOT_MISSING", f"project root does not exist: {root}")
        return None
    explicit_path = _candidate(root, str(planning_dir)) if planning_dir is not None else None
    configured = configured_planning_dir(root)
    configured_path = _candidate(root, configured) if configured else None
    canonical_path = _candidate(root, CANONICAL_DIR_NAME)
    legacy_path = root

    if explicit_path is not None:
        return _layout_from_candidate(root, explicit_path, source="explicit", explicit=True)
    if configured_path is not None and configured_path not in {canonical_path, legacy_path}:
        return _layout_from_candidate(root, configured_path, source="configured")

    requested_task = task_id or os.environ.get("PWF_TASK_ID")
    inferred_task = _task_id_from_start(root, start)
    selected_task = requested_task or inferred_task
    if selected_task:
        return _task_layout(root, selected_task, source="task-id" if requested_task else "task-directory")

    if canonical_path.is_dir():
        discovered = discover_task_directories(root)
        flat_active = _has_managed_files(canonical_path)
        if discovered and flat_active:
            raise LayoutConflict(
                "task-scoped and flat canonical layouts are both populated; resolve explicitly",
                details={"tasks": sorted(discovered)},
            )
        if discovered:
            if len(discovered) > 1:
                raise LayoutError(
                    "TASK_SELECTION_REQUIRED",
                    "multiple task directories exist; pass --task-id or set PWF_TASK_ID",
                    details={"tasks": sorted(discovered)},
                )
            only = next(iter(discovered))
            return _task_layout(root, only, source="single-task")

    active: list[tuple[str, Path, bool]] = []
    if explicit_path is not None:
        active.append(("explicit", explicit_path, True))
    if configured_path is not None and all(configured_path != item[1] for item in active):
        active.append(("configured", configured_path, False))
    if canonical_path.exists() and _has_managed_files(canonical_path) and all(canonical_path != item[1] for item in active):
        active.append(("canonical", canonical_path, False))
    if _has_managed_files(legacy_path) and all(legacy_path != item[1] for item in active):
        active.append(("legacy", legacy_path, False))

    canonical_active = next((item[1] for item in active if item[1] == canonical_path), None)
    legacy_active = next((item[1] for item in active if item[1] == legacy_path), None)
    if canonical_active is not None and legacy_active is not None:
        conflicts = _compare_layouts(canonical_active, legacy_active)
        if conflicts:
            names = ", ".join(str(item.get("name")) for item in conflicts)
            raise LayoutConflict(
                f"canonical and legacy planning layouts are both populated and differ ({names}); resolve explicitly",
                details=conflicts,
            )

    if not active:
        if require:
            raise LayoutError("PLANNING_LAYOUT_NOT_FOUND", f"no planning artifacts found under {root}")
        selected = explicit_path or configured_path or canonical_path
        return Layout(root, selected, "default" if explicit_path is None else "explicit", _root_index(root), explicit_path is not None, planning_root=canonical_path if selected == canonical_path else None)

    selected_source, selected_path, selected_explicit = active[0]
    if selected_path == root:
        source = "legacy" if selected_source != "explicit" else "explicit"
    else:
        source = selected_source
    return _layout_from_candidate(root, selected_path, source=source, explicit=selected_explicit)


def layout_for_init(project_root: Path, *, mode: str, planning_dir: str | Path | None = None, task_id: str | None = None) -> Layout:
    root = _canonical(project_root)
    if mode not in {"new", "adopt", "repair"}:
        raise LayoutError("INVALID_MODE", f"unsupported initialization mode: {mode}")
    if mode == "new":
        if planning_dir is not None:
            raise LayoutError(
                "TASK_ID_REQUIRED",
                "new tasks must use --task-id under 00.项目规划与治理; planning_dir is read-only compatibility for adopt/repair",
            )
        if not task_id:
            raise LayoutError("TASK_ID_REQUIRED", "--task-id is required when creating a new project task")
        return _task_layout(root, task_id, source="new-task", explicit=True, allow_missing=True)
    resolved = resolve_layout(root, planning_dir=planning_dir, task_id=task_id, require=False)
    if resolved is not None:
        return resolved
    if not task_id:
        raise LayoutError("TASK_ID_REQUIRED", "task_id is required when no existing task can be resolved")
    return _task_layout(root, task_id, source="init-task", explicit=True, allow_missing=True)


def layout_marker(layout: Layout) -> str:
    payload = {
        "layout_version": LAYOUT_VERSION,
        "planning_directory": "." if layout.is_legacy else str(layout.planning_dir.relative_to(layout.project_root).as_posix()),
        "managed_documents": list(PLANNING_DOCUMENTS),
        "task_id": layout.task_id,
        "task_index": str(task_index_path(layout.project_root).relative_to(layout.project_root).as_posix()) if layout.is_task_scoped else None,
    }
    return f"{LAYOUT_MARKER_BEGIN}\n{json.dumps(payload, ensure_ascii=False, sort_keys=True)}\n{LAYOUT_MARKER_END}"


def render_index_links(layout: Layout, *, existing: str = "") -> str:
    """Return a stable root index block without replacing user-owned content."""
    marker = layout_marker(layout)
    location = layout.planning_dir.relative_to(layout.project_root).as_posix() if not layout.is_legacy else "."
    rows = ["## planning-with-files 规划入口", "", f"- 布局版本：`{LAYOUT_VERSION}`", f"- 当前规划目录：`{location}`", "", "| 文件 | 用途 |", "|---|---|"]
    if layout.is_task_scoped:
        rows.append(f"| `{task_index_path(layout.project_root).relative_to(layout.project_root).as_posix()}` | 任务发现索引（不保存阶段状态） |")
    for name in ENTRY_DOCUMENTS:
        if name == "00_PROJECT_INDEX.md":
            continue
        rows.append(f"| `{name}` | 根目录入口 |")
    for name in PLANNING_DOCUMENTS:
        relative = layout.path(name).relative_to(layout.project_root).as_posix()
        rows.append(f"| `{relative}` | 规划与治理工件 |")
    rows.extend(["", marker])
    block = "\n".join(rows) + "\n"
    begin = "<!-- planning-with-files:index:begin -->"
    end = "<!-- planning-with-files:index:end -->"
    section = f"{begin}\n{block}{end}"
    if begin in existing and end in existing:
        start = existing.index(begin)
        stop = existing.index(end, start) + len(end)
        return existing[:start] + section + existing[stop:]
    return existing.rstrip() + ("\n\n" if existing.strip() else "") + section


FORBIDDEN_IMPORT_PARTS = {
    ".git",
    ".gitmodules",
    ".gitnexus",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    ".env",
    "cache",
    "logs",
    "runtime",
}


def _import_inventory(source: Path) -> tuple[list[str], list[dict[str, object]]]:
    if source.is_symlink() or not source.is_dir():
        raise UnsafeLayoutPath(f"task package source is not a regular directory: {source}")
    directories: list[str] = []
    files: list[dict[str, object]] = []
    for current, dirnames, filenames in os.walk(source, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in sorted(dirnames):
            path = current_path / name
            relative = path.relative_to(source).as_posix()
            if name in FORBIDDEN_IMPORT_PARTS or name.startswith(".sync-conflict-"):
                raise LayoutError("UNSAFE_IMPORT_CONTENT", f"forbidden task package directory: {relative}")
            if path.is_symlink():
                raise UnsafeLayoutPath(f"symlink/junction is not accepted in task package: {relative}")
            directories.append(relative)
        for name in sorted(filenames):
            path = current_path / name
            relative = path.relative_to(source).as_posix()
            if (
                name in FORBIDDEN_IMPORT_PARTS
                or name.startswith(".sync-conflict-")
                or path.suffix.lower() in {".key", ".pem", ".p12", ".pfx"}
            ):
                raise LayoutError("UNSAFE_IMPORT_CONTENT", f"forbidden task package file: {relative}")
            if path.is_symlink() or not path.is_file():
                raise UnsafeLayoutPath(f"non-regular file is not accepted in task package: {relative}")
            files.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return sorted(set(directories)), sorted(files, key=lambda item: str(item["path"]))


def task_import_plan(project_root: Path, source_dir: Path, task_id: str) -> dict[str, object]:
    root = _canonical(project_root)
    source_input = Path(source_dir).expanduser()
    if not source_input.is_absolute():
        source_input = Path.cwd() / source_input
    if source_input.is_symlink():
        raise UnsafeLayoutPath(f"task package source cannot be a symlink: {source_input}")
    source = source_input.resolve(strict=False)
    safe = validate_task_id(task_id)
    destination = validate_contained_path(root, root / CANONICAL_DIR_NAME / safe)
    if destination.exists() or destination.is_symlink():
        raise MigrationConflict(f"task destination already exists: {destination}")
    indexed = read_task_index(root, require=False)
    if safe in indexed:
        raise MigrationConflict(f"task_id is already indexed: {safe}")
    directories, files = _import_inventory(source)
    return {
        "status": "READY",
        "source": str(source),
        "destination": str(destination),
        "task_id": safe,
        "directories": directories,
        "files": files,
        "source_file_count": len(files),
        "generated_at": dt.datetime.now().astimezone().replace(microsecond=0).isoformat(),
    }


def import_task_package(
    project_root: Path,
    source_dir: Path,
    task_id: str,
    *,
    apply: bool = False,
    confirm: bool = False,
    failure_after: int | None = None,
) -> dict[str, object]:
    """Copy an external task package into layout v3 without mutating source."""
    plan = task_import_plan(project_root, source_dir, task_id)
    if not apply:
        return {**plan, "mode": "DRY_RUN", "writes": 0}
    if not confirm:
        raise LayoutError("CONFIRM_REQUIRED", "task import apply requires explicit --confirm")
    root = _canonical(project_root)
    source = Path(str(plan["source"]))
    destination = Path(str(plan["destination"]))
    container = destination.parent
    container.mkdir(parents=True, exist_ok=True)
    index_file = task_index_path(root)
    old_index = index_file.read_bytes() if index_file.is_file() else None
    staging = Path(tempfile.mkdtemp(prefix=".task-import-", dir=container))
    package = staging / str(plan["task_id"])
    activated = False
    copied = 0
    try:
        package.mkdir()
        for relative in plan["directories"]:
            (package / str(relative)).mkdir(parents=True, exist_ok=True)
        for item in plan["files"]:
            relative = str(item["path"])
            src = source / relative
            dst = package / relative
            if sha256_file(src) != item["sha256"]:
                raise MigrationConflict(f"source changed before import: {src}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            if sha256_file(dst) != item["sha256"]:
                raise MigrationConflict(f"staging hash mismatch: {src}")
            copied += 1
            if failure_after is not None and copied >= failure_after:
                raise RuntimeError("injected task import failure")
        evidence_dir = package / "evidence" / "migration"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        receipt = {
            "schema_version": "1.0.0",
            "task_id": plan["task_id"],
            "source": plan["source"],
            "source_files": plan["files"],
            "source_file_count": plan["source_file_count"],
            "imported_at": dt.datetime.now().astimezone().replace(microsecond=0).isoformat(),
        }
        _atomic_text(evidence_dir / "source-manifest.json", json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        for item in plan["files"]:
            relative = str(item["path"])
            if sha256_file(package / relative) != item["sha256"]:
                raise MigrationConflict(f"package changed before activation: {relative}")
        os.replace(package, destination)
        activated = True
        register_task(root, str(plan["task_id"]))
        discover_task_directories(root)
        return {**plan, "mode": "APPLY", "writes": copied + 2, "source_unchanged": True, "activated": True}
    except Exception:
        if activated and destination.exists():
            shutil.rmtree(destination)
        if old_index is None:
            if index_file.exists():
                index_file.unlink()
        else:
            _atomic_text(index_file, old_index.decode("utf-8"))
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def migration_plan(
    project_root: Path,
    *,
    target_dir: str | Path | None = None,
    planning_dir: str | Path | None = None,
) -> dict[str, object]:
    root = _canonical(project_root)
    source_layout = resolve_layout(root, planning_dir=planning_dir, require=False)
    source_dir = root if source_layout is None or not source_layout.is_legacy else source_layout.planning_dir
    if source_layout is not None and not source_layout.is_legacy:
        return {"status": "NOOP", "reason": "source layout is already canonical", "layout": source_layout.to_dict(), "items": []}
    destination = _candidate(root, str(target_dir) if target_dir is not None else CANONICAL_DIR_NAME)
    if destination == root:
        raise UnsafeLayoutPath("migration destination must not be the project root")
    if source_dir == destination:
        raise MigrationConflict("migration source and destination are identical")
    items: list[dict[str, object]] = []
    for name in PLANNING_DOCUMENTS:
        source = source_dir / name
        if not source.exists():
            continue
        if source.is_symlink() or not source.is_file():
            raise UnsafeLayoutPath(f"cannot migrate non-regular managed file: {source}")
        destination_file = destination / name
        source_hash = sha256_file(source)
        if destination_file.exists():
            if destination_file.is_symlink() or not destination_file.is_file():
                raise UnsafeLayoutPath(f"destination is not a regular file: {destination_file}")
            destination_hash = sha256_file(destination_file)
            if destination_hash != source_hash:
                raise MigrationConflict(
                    f"destination content differs for {name}",
                    details={"source": str(source), "destination": str(destination_file), "source_sha256": source_hash, "destination_sha256": destination_hash},
                )
            action = "remove-duplicate"
        else:
            destination_hash = None
            action = "move"
        items.append({"name": name, "source": str(source), "destination": str(destination_file), "bytes": source.stat().st_size, "sha256": source_hash, "destination_sha256": destination_hash, "action": action})
    return {
        "status": "READY" if items else "NOOP",
        "source": str(source_dir),
        "destination": str(destination),
        "items": items,
        "unknown_files_untouched": sorted(path.name for path in source_dir.iterdir() if path.is_file() and path.name not in PLANNING_DOCUMENTS),
        "generated_at": dt.datetime.now().astimezone().replace(microsecond=0).isoformat(),
    }


def migrate_layout(
    project_root: Path,
    *,
    target_dir: str | Path | None = None,
    planning_dir: str | Path | None = None,
    apply: bool = False,
    confirm: bool = False,
    failure_after: int | None = None,
) -> dict[str, object]:
    plan = migration_plan(project_root, target_dir=target_dir, planning_dir=planning_dir)
    if not apply:
        return {**plan, "mode": "DRY_RUN", "writes": 0}
    if not confirm:
        raise LayoutError("CONFIRM_REQUIRED", "migration apply requires explicit --confirm")
    if plan["status"] == "NOOP":
        return {**plan, "mode": "APPLY", "writes": 0, "idempotent": True}
    destination = Path(str(plan["destination"]))
    source = Path(str(plan["source"]))
    destination.mkdir(parents=True, exist_ok=True)
    for directory in PLANNING_DIRS:
        (destination / directory).mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".planning-layout-migrate-", dir=destination.parent))
    moved: list[tuple[Path, Path]] = []
    duplicate_sources: list[Path] = []
    try:
        manifest = {"plan": plan, "staging": str(staging), "created_at": dt.datetime.now().astimezone().replace(microsecond=0).isoformat()}
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for index, item in enumerate(plan["items"]):
            item = dict(item)
            src = Path(str(item["source"]))
            dst = Path(str(item["destination"]))
            if sha256_file(src) != item["sha256"]:
                raise MigrationConflict(f"source changed before migration: {src}")
            if item["action"] == "remove-duplicate":
                duplicate_sources.append(src)
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            staged = staging / str(item["name"])
            shutil.copyfile(src, staged)
            if sha256_file(staged) != item["sha256"]:
                raise MigrationConflict(f"staging hash mismatch: {src}")
            os.replace(src, dst)
            moved.append((src, dst))
            if failure_after is not None and len(moved) >= failure_after:
                raise RuntimeError("injected migration failure")
        for src in duplicate_sources:
            if sha256_file(src) != sha256_file(Path(str(next(item["destination"] for item in plan["items"] if item["source"] == str(src))))):
                raise MigrationConflict(f"duplicate source changed before removal: {src}")
            src.unlink()
        return {**plan, "mode": "APPLY", "writes": len(moved) + len(duplicate_sources), "idempotent": False, "rollback": "not-needed"}
    except Exception:
        for src, dst in reversed(moved):
            if dst.exists() and not src.exists():
                os.replace(dst, src)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def describe_conflict(exc: LayoutError) -> dict[str, object]:
    return {"result": "BLOCKED", "code": exc.code, "message": str(exc), "details": exc.details}


__all__ = [
    "CANONICAL_DIR_NAME",
    "CHECKLIST_NAME",
    "ENTRY_DOCUMENTS",
    "LAYOUT_VERSION",
    "LEGACY_LAYOUT_VERSION",
    "Layout",
    "LayoutConflict",
    "LayoutError",
    "PLANNING_DOCUMENTS",
    "TASK_INDEX_NAME",
    "UnsafeLayoutPath",
    "configured_planning_dir",
    "discover_project_root",
    "layout_for_init",
    "layout_marker",
    "migrate_layout",
    "migration_plan",
    "task_import_plan",
    "import_task_package",
    "render_index_links",
    "resolve_layout",
    "read_task_index",
    "write_task_index",
    "register_task",
    "discover_task_directories",
    "task_index_path",
    "sha256_file",
    "validate_contained_path",
    "validate_plan_id",
    "validate_task_id",
]
