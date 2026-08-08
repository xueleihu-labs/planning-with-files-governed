#!/usr/bin/env python3
"""Explicit Gate 4B compatibility facade."""
# version source: VERSION
from __future__ import annotations

import sys
from pathlib import Path

_candidate_src = Path(__file__).resolve().parents[1] / "src"
if _candidate_src.is_dir() and str(_candidate_src) not in sys.path:
    sys.path.insert(0, str(_candidate_src))

from pwf_governed._legacy.planning_layout import (
    CANONICAL_DIR_NAME,
    CHECKLIST_NAME,
    ENTRY_DOCUMENTS,
    FORBIDDEN_IMPORT_PARTS,
    Iterable,
    LAYOUT_CONFIG_FILE,
    LAYOUT_MARKER_BEGIN,
    LAYOUT_MARKER_END,
    LAYOUT_VERSION,
    LEGACY_LAYOUT_VERSION,
    Layout,
    LayoutConflict,
    LayoutError,
    Mapping,
    MigrationConflict,
    PLANNING_DIRS,
    PLANNING_DOCUMENTS,
    PLAN_ID_RE,
    Path,
    TASK_INDEX_NAME,
    UnsafeLayoutPath,
    configured_planning_dir,
    dataclasses,
    describe_conflict,
    discover_project_root,
    discover_task_directories,
    dt,
    git_boundary,
    hashlib,
    import_task_package,
    json,
    layout_for_init,
    layout_marker,
    migrate_layout,
    migration_plan,
    os,
    re,
    read_task_index,
    register_task,
    render_index_links,
    resolve_layout,
    sha256_file,
    shutil,
    subprocess,
    task_import_plan,
    task_index_path,
    tempfile,
    validate_contained_path,
    validate_plan_id,
    validate_task_id,
    write_task_index,
    _atomic_text,
    _candidate,
    _canonical,
    _compare_layouts,
    _component_links,
    _config_from_text,
    _has_layout_signal,
    _has_managed_files,
    _import_inventory,
    _layout_from_candidate,
    _root_index,
    _snapshot,
    _task_id_from_start,
    _task_layout,
    _within,
)

__all__ = [
    "CANONICAL_DIR_NAME",
    "CHECKLIST_NAME",
    "ENTRY_DOCUMENTS",
    "FORBIDDEN_IMPORT_PARTS",
    "Iterable",
    "LAYOUT_CONFIG_FILE",
    "LAYOUT_MARKER_BEGIN",
    "LAYOUT_MARKER_END",
    "LAYOUT_VERSION",
    "LEGACY_LAYOUT_VERSION",
    "Layout",
    "LayoutConflict",
    "LayoutError",
    "Mapping",
    "MigrationConflict",
    "PLANNING_DIRS",
    "PLANNING_DOCUMENTS",
    "PLAN_ID_RE",
    "Path",
    "TASK_INDEX_NAME",
    "UnsafeLayoutPath",
    "configured_planning_dir",
    "dataclasses",
    "describe_conflict",
    "discover_project_root",
    "discover_task_directories",
    "dt",
    "git_boundary",
    "hashlib",
    "import_task_package",
    "json",
    "layout_for_init",
    "layout_marker",
    "migrate_layout",
    "migration_plan",
    "os",
    "re",
    "read_task_index",
    "register_task",
    "render_index_links",
    "resolve_layout",
    "sha256_file",
    "shutil",
    "subprocess",
    "task_import_plan",
    "task_index_path",
    "tempfile",
    "validate_contained_path",
    "validate_plan_id",
    "validate_task_id",
    "write_task_index",
]

# Facade delegation class for test mock compatibility
import types

class _FacadeModule(types.ModuleType):
    def __getattribute__(self, name):
        if name.startswith("_FacadeModule__") or name in {"__class__", "__dict__", "__weakref__", "__module__", "__name__", "__doc__", "__file__", "__path__", "__package__", "__all__"}:
            return super().__getattribute__(name)
        try:
            target = sys.modules["pwf_governed._legacy.planning_layout"]
            val = getattr(target, name)
            return val
        except (KeyError, AttributeError):
            return super().__getattribute__(name)

    def __setattr__(self, name, value):
        if name.startswith("_FacadeModule__") or name in {"__class__", "__dict__", "__weakref__", "__module__", "__name__", "__doc__", "__file__", "__path__", "__package__", "__loader__", "__spec__", "__all__"}:
            super().__setattr__(name, value)
        else:
            try:
                target = sys.modules["pwf_governed._legacy.planning_layout"]
                setattr(target, name, value)
            except (KeyError, AttributeError):
                super().__setattr__(name, value)

    def __delattr__(self, name):
        try:
            target = sys.modules["pwf_governed._legacy.planning_layout"]
            delattr(target, name)
        except (KeyError, AttributeError):
            super().__delattr__(name)

sys.modules[__name__].__class__ = _FacadeModule
