#!/usr/bin/env python3
"""Explicit Gate 4B compatibility facade."""
# version source: VERSION
from __future__ import annotations

import sys
from pathlib import Path

_candidate_src = Path(__file__).resolve().parents[1] / "src"
if _candidate_src.is_dir() and str(_candidate_src) not in sys.path:
    sys.path.insert(0, str(_candidate_src))

from pwf_governed._legacy.workflow_module_composer import (
    Any,
    ModuleCompositionError,
    Path,
    SELECTABLE_LIFECYCLES,
    SOURCE_RANK,
    compose_modules,
    contracts,
    planning_layout,
    _add_candidate,
    _conflicts,
    _current_binding,
    _default_module_ids,
    _lifecycle,
    _module_binding,
    _module_entries,
    _resolve_conflicts,
    _resolve_dependencies,
    _source_text,
    _verify_dependencies,
)

__all__ = [
    "Any",
    "ModuleCompositionError",
    "Path",
    "SELECTABLE_LIFECYCLES",
    "SOURCE_RANK",
    "compose_modules",
    "contracts",
    "planning_layout",
]

# Facade delegation class for test mock compatibility
import types

class _FacadeModule(types.ModuleType):
    def __getattribute__(self, name):
        if name.startswith("_FacadeModule__") or name in {"__class__", "__dict__", "__weakref__", "__module__", "__name__", "__doc__", "__file__", "__path__", "__package__", "__all__"}:
            return super().__getattribute__(name)
        try:
            target = sys.modules["pwf_governed._legacy.workflow_module_composer"]
            val = getattr(target, name)
            return val
        except (KeyError, AttributeError):
            return super().__getattribute__(name)

    def __setattr__(self, name, value):
        if name.startswith("_FacadeModule__") or name in {"__class__", "__dict__", "__weakref__", "__module__", "__name__", "__doc__", "__file__", "__path__", "__package__", "__loader__", "__spec__", "__all__"}:
            super().__setattr__(name, value)
        else:
            try:
                target = sys.modules["pwf_governed._legacy.workflow_module_composer"]
                setattr(target, name, value)
            except (KeyError, AttributeError):
                super().__setattr__(name, value)

    def __delattr__(self, name):
        try:
            target = sys.modules["pwf_governed._legacy.workflow_module_composer"]
            delattr(target, name)
        except (KeyError, AttributeError):
            super().__delattr__(name)

sys.modules[__name__].__class__ = _FacadeModule
