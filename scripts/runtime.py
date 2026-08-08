#!/usr/bin/env python3
"""Explicit Gate 4B compatibility facade."""
# version source: VERSION
from __future__ import annotations

import sys
from pathlib import Path

_candidate_src = Path(__file__).resolve().parents[1] / "src"
if _candidate_src.is_dir() and str(_candidate_src) not in sys.path:
    sys.path.insert(0, str(_candidate_src))

from pwf_governed._legacy.runtime import (
    ATTESTATION_FILE,
    DISABLED_ENV,
    Iterable,
    LEGACY_ATTESTATION_FILE,
    MODE_FILE,
    NONCE_FILE,
    Path,
    ResolvedPlan,
    RuntimeError_,
    argparse,
    attest,
    dataclasses,
    disabled,
    doctor,
    hashlib,
    inject,
    json,
    layout,
    main,
    os,
    re,
    resolve_plan,
    sha256_file,
    smart_plan_extract,
    sys,
    tempfile,
    _active_scoped_plan,
    _atomic_text,
    _attestation_value,
    _head,
    _mode,
    _nonce,
    _parser,
    _regular_plan_dir,
    _runtime_root,
    _safe_plan_id,
    _scoped_plan,
    _section,
    _within,
)

__all__ = [
    "ATTESTATION_FILE",
    "DISABLED_ENV",
    "Iterable",
    "LEGACY_ATTESTATION_FILE",
    "MODE_FILE",
    "NONCE_FILE",
    "Path",
    "ResolvedPlan",
    "RuntimeError_",
    "argparse",
    "attest",
    "dataclasses",
    "disabled",
    "doctor",
    "hashlib",
    "inject",
    "json",
    "layout",
    "main",
    "os",
    "re",
    "resolve_plan",
    "sha256_file",
    "smart_plan_extract",
    "sys",
    "tempfile",
]

# Facade delegation class for test mock compatibility
import types

class _FacadeModule(types.ModuleType):
    def __getattribute__(self, name):
        if name.startswith("_FacadeModule__") or name in {"__class__", "__dict__", "__weakref__", "__module__", "__name__", "__doc__", "__file__", "__path__", "__package__", "__all__"}:
            return super().__getattribute__(name)
        try:
            target = sys.modules["pwf_governed._legacy.runtime"]
            val = getattr(target, name)
            return val
        except (KeyError, AttributeError):
            return super().__getattribute__(name)

    def __setattr__(self, name, value):
        if name.startswith("_FacadeModule__") or name in {"__class__", "__dict__", "__weakref__", "__module__", "__name__", "__doc__", "__file__", "__path__", "__package__", "__loader__", "__spec__", "__all__"}:
            super().__setattr__(name, value)
        else:
            try:
                target = sys.modules["pwf_governed._legacy.runtime"]
                setattr(target, name, value)
            except (KeyError, AttributeError):
                super().__setattr__(name, value)

    def __delattr__(self, name):
        try:
            target = sys.modules["pwf_governed._legacy.runtime"]
            delattr(target, name)
        except (KeyError, AttributeError):
            super().__delattr__(name)

sys.modules[__name__].__class__ = _FacadeModule

if __name__ == "__main__":
    raise SystemExit(main())
