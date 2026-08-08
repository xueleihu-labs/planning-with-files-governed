#!/usr/bin/env python3
"""Explicit Gate 4B compatibility facade."""
# version source: VERSION
from __future__ import annotations

import sys
from pathlib import Path

_candidate_src = Path(__file__).resolve().parents[1] / "src"
if _candidate_src.is_dir() and str(_candidate_src) not in sys.path:
    sys.path.insert(0, str(_candidate_src))

from pwf_governed._legacy.project_init import (
    CORE_FILES,
    EditionBoundaryError,
    INIT_PLANNING_FILES,
    INIT_PROJECT_FILES,
    Iterable,
    LOCK_NAME,
    NOT_HANDLED,
    PROJECT_FILES,
    Path,
    ProjectLock,
    REQUIRED_SECTIONS,
    VERSION_FILE,
    argparse,
    atomic_write,
    build_values,
    composer,
    create_missing,
    create_workflow_checklist,
    current_agent,
    current_edition,
    default_skill_root,
    difflib,
    dt,
    edition_operation,
    ensure_gitignore,
    infer_machine,
    json,
    layout,
    main,
    matcher,
    os,
    parse_args,
    pid_exists,
    planning_layout,
    planning_path,
    project_file_target,
    project_has_governance,
    record_index_result,
    repair_preview,
    repair_workflow_checklist,
    root_resolver,
    run_index_preflight,
    run_index_update,
    shutil,
    signal,
    subprocess,
    sys,
    template_content,
    timestamp,
    workflow,
    workflow_task_text,
    write_equivalent_index,
    _resource_roots,
    _workflow_resource_root,
)

__all__ = [
    "CORE_FILES",
    "EditionBoundaryError",
    "INIT_PLANNING_FILES",
    "INIT_PROJECT_FILES",
    "Iterable",
    "LOCK_NAME",
    "NOT_HANDLED",
    "PROJECT_FILES",
    "Path",
    "ProjectLock",
    "REQUIRED_SECTIONS",
    "VERSION_FILE",
    "argparse",
    "atomic_write",
    "build_values",
    "composer",
    "create_missing",
    "create_workflow_checklist",
    "current_agent",
    "current_edition",
    "default_skill_root",
    "difflib",
    "dt",
    "edition_operation",
    "ensure_gitignore",
    "infer_machine",
    "json",
    "layout",
    "main",
    "matcher",
    "os",
    "parse_args",
    "pid_exists",
    "planning_layout",
    "planning_path",
    "project_file_target",
    "project_has_governance",
    "record_index_result",
    "repair_preview",
    "repair_workflow_checklist",
    "root_resolver",
    "run_index_preflight",
    "run_index_update",
    "shutil",
    "signal",
    "subprocess",
    "sys",
    "template_content",
    "timestamp",
    "workflow",
    "workflow_task_text",
    "write_equivalent_index",
]

# Facade delegation class for test mock compatibility
import types

class _FacadeModule(types.ModuleType):
    def __getattribute__(self, name):
        if name.startswith("_FacadeModule__") or name in {"__class__", "__dict__", "__weakref__", "__module__", "__name__", "__doc__", "__file__", "__path__", "__package__", "__all__"}:
            return super().__getattribute__(name)
        try:
            target = sys.modules["pwf_governed._legacy.project_init"]
            val = getattr(target, name)
            return val
        except (KeyError, AttributeError):
            return super().__getattribute__(name)

    def __setattr__(self, name, value):
        if name.startswith("_FacadeModule__") or name in {"__class__", "__dict__", "__weakref__", "__module__", "__name__", "__doc__", "__file__", "__path__", "__package__", "__loader__", "__spec__", "__all__"}:
            super().__setattr__(name, value)
        else:
            try:
                target = sys.modules["pwf_governed._legacy.project_init"]
                setattr(target, name, value)
            except (KeyError, AttributeError):
                super().__setattr__(name, value)

    def __delattr__(self, name):
        try:
            target = sys.modules["pwf_governed._legacy.project_init"]
            delattr(target, name)
        except (KeyError, AttributeError):
            super().__delattr__(name)

sys.modules[__name__].__class__ = _FacadeModule

if __name__ == "__main__":
    raise SystemExit(main())
