#!/usr/bin/env python3
"""Explicit Gate 4B compatibility facade."""
# version source: VERSION
from __future__ import annotations

import sys
from pathlib import Path

_candidate_src = Path(__file__).resolve().parents[1] / "src"
if _candidate_src.is_dir() and str(_candidate_src) not in sys.path:
    sys.path.insert(0, str(_candidate_src))

from pwf_governed._legacy.workflow_contracts import (
    ARTIFACT_TYPES,
    Any,
    BLOCKS,
    CANDIDATE_ID_RE,
    CANDIDATE_SCHEMA_VERSION,
    CANDIDATE_STATUSES,
    CHECKLIST_NAME,
    CONFLICT_SCHEMA_VERSION,
    CONFLICT_STATUSES,
    Callable,
    ContractError,
    ID_RE,
    LOCK_SCHEMA_VERSION,
    Path,
    REGISTRY_SCHEMA_VERSION,
    RFC3339_RE,
    SEMVER_RE,
    TASK_ID_RE,
    TEMPLATE_LIFECYCLES,
    WORKFLOW_SCHEMA_VERSION,
    acquire_workflow_lock,
    adopt_with_explicit_evidence,
    argparse,
    artifact_path,
    atomic_write_text,
    bump_semver,
    candidate_id,
    canonical_json,
    checklist_from_template,
    checklist_summary,
    checklist_tasks,
    conflict_filename,
    copy,
    dt,
    extract_machine_json,
    extract_machine_json_lenient,
    file_digest,
    hashlib,
    heartbeat_lock,
    initial_workflow_metadata,
    json,
    load_registry,
    lock_is_stale,
    main,
    merge_json,
    merge_json_file,
    new_lock,
    normalize_text,
    normalized_bytes,
    os,
    parse_markdown_table,
    planning_layout,
    process_exists,
    re,
    registry_markdown,
    release_lock,
    render_machine_block,
    replace_machine_json,
    select_template,
    sha256_digest,
    socket,
    stale_lock_recovery_filename,
    subprocess,
    template_binding,
    utc_filename_timestamp,
    validate_candidate,
    validate_candidate_id,
    validate_checklist_text,
    validate_conflict,
    validate_id,
    validate_lock,
    validate_registry,
    validate_rfc3339,
    validate_semver,
    validate_task_id,
    validate_template_metadata,
    validate_workflow_metadata,
    workflow_integrity_errors,
    workflow_root,
    write_conflict_report,
    write_lock,
    write_stale_lock_recovery_report,
    _require_object,
)

__all__ = [
    "ARTIFACT_TYPES",
    "Any",
    "BLOCKS",
    "CANDIDATE_ID_RE",
    "CANDIDATE_SCHEMA_VERSION",
    "CANDIDATE_STATUSES",
    "CHECKLIST_NAME",
    "CONFLICT_SCHEMA_VERSION",
    "CONFLICT_STATUSES",
    "Callable",
    "ContractError",
    "ID_RE",
    "LOCK_SCHEMA_VERSION",
    "Path",
    "REGISTRY_SCHEMA_VERSION",
    "RFC3339_RE",
    "SEMVER_RE",
    "TASK_ID_RE",
    "TEMPLATE_LIFECYCLES",
    "WORKFLOW_SCHEMA_VERSION",
    "acquire_workflow_lock",
    "adopt_with_explicit_evidence",
    "argparse",
    "artifact_path",
    "atomic_write_text",
    "bump_semver",
    "candidate_id",
    "canonical_json",
    "checklist_from_template",
    "checklist_summary",
    "checklist_tasks",
    "conflict_filename",
    "copy",
    "dt",
    "extract_machine_json",
    "extract_machine_json_lenient",
    "file_digest",
    "hashlib",
    "heartbeat_lock",
    "initial_workflow_metadata",
    "json",
    "load_registry",
    "lock_is_stale",
    "main",
    "merge_json",
    "merge_json_file",
    "new_lock",
    "normalize_text",
    "normalized_bytes",
    "os",
    "parse_markdown_table",
    "planning_layout",
    "process_exists",
    "re",
    "registry_markdown",
    "release_lock",
    "render_machine_block",
    "replace_machine_json",
    "select_template",
    "sha256_digest",
    "socket",
    "stale_lock_recovery_filename",
    "subprocess",
    "template_binding",
    "utc_filename_timestamp",
    "validate_candidate",
    "validate_candidate_id",
    "validate_checklist_text",
    "validate_conflict",
    "validate_id",
    "validate_lock",
    "validate_registry",
    "validate_rfc3339",
    "validate_semver",
    "validate_task_id",
    "validate_template_metadata",
    "validate_workflow_metadata",
    "workflow_integrity_errors",
    "workflow_root",
    "write_conflict_report",
    "write_lock",
    "write_stale_lock_recovery_report",
]

# Facade delegation class for test mock compatibility
import types

class _FacadeModule(types.ModuleType):
    def __getattribute__(self, name):
        if name.startswith("_FacadeModule__") or name in {"__class__", "__dict__", "__weakref__", "__module__", "__name__", "__doc__", "__file__", "__path__", "__package__", "__all__"}:
            return super().__getattribute__(name)
        try:
            target = sys.modules["pwf_governed._legacy.workflow_contracts"]
            val = getattr(target, name)
            return val
        except (KeyError, AttributeError):
            return super().__getattribute__(name)

    def __setattr__(self, name, value):
        if name.startswith("_FacadeModule__") or name in {"__class__", "__dict__", "__weakref__", "__module__", "__name__", "__doc__", "__file__", "__path__", "__package__", "__loader__", "__spec__", "__all__"}:
            super().__setattr__(name, value)
        else:
            try:
                target = sys.modules["pwf_governed._legacy.workflow_contracts"]
                setattr(target, name, value)
            except (KeyError, AttributeError):
                super().__setattr__(name, value)

    def __delattr__(self, name):
        try:
            target = sys.modules["pwf_governed._legacy.workflow_contracts"]
            delattr(target, name)
        except (KeyError, AttributeError):
            super().__delattr__(name)

sys.modules[__name__].__class__ = _FacadeModule

if __name__ == "__main__":
    raise SystemExit(main())
