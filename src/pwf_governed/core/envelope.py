"""Gate 2 extracted module: core/envelope.py.

Generated from the Gate 1 planning.py baseline.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from typing import Iterable
import copy
import datetime as dt
import hashlib
import json
import re
import shutil
import tempfile

from pwf_governed._legacy import (
    governance_profiles,
    plan_contracts,
    workflow_contracts,
)
from pwf_governed._legacy import governance_profiles as governance
from pwf_governed._legacy import plan_contracts as contracts
from pwf_governed._legacy import workflow_contracts as workflow

from pwf_governed._legacy import (
    governance_profiles,
    plan_contracts,
    workflow_contracts,
)
from pwf_governed.core.constants import (
    SKILL_ROOT,
    _MISSING_ROUTE,
)
from pwf_governed.core.errors import (
    PlanningError,
)


def _identity_input_adapter(
    value: dict[str, Any],
    *,
    payload_kind: str,
    callsite_id: str,
) -> dict[str, Any]:
    return value


_INPUT_ADAPTER = _identity_input_adapter


def bind_input_adapter(adapter: Any) -> None:
    global _INPUT_ADAPTER
    _INPUT_ADAPTER = adapter

def _load_public_checkpoint_core() -> Any:
    """Load this Skill's minimal public checkpoint-reader boundary.

    The reader is intentionally local: installing an unrelated sibling Skill
    must not be a prerequisite for planning-with-files.  The function name is
    retained as a compatibility seam for callers that replace the public
    boundary in tests or in an external checkpoint engine.
    """
    try:
        from pwf_governed._legacy import checkpoint_reader as checkpoint_reader
    except ImportError as exc:  # pragma: no cover - only broken direct installs
        raise PlanningError("STATE_ROOT_RESOLVER_REUSE_GAP", "local checkpoint reader unavailable") from exc
    if not callable(getattr(checkpoint_reader, "runtime_state_root", None)) or not callable(
        getattr(checkpoint_reader, "read_head", None)
    ):
        raise PlanningError("STATE_ROOT_RESOLVER_REUSE_GAP", "checkpoint reader boundary is incomplete")
    return checkpoint_reader

def resolve_state_root(state_root: str | Path | None = None) -> Path:
    """Resolve and harden the shared runtime root using the public resolver."""
    core = _load_public_checkpoint_core()
    try:
        candidate = Path(core.runtime_state_root(SKILL_ROOT, state_root)).resolve(strict=False)
    except Exception as exc:  # the public resolver has its own stable error vocabulary
        raise PlanningError("UNSAFE_STATE_ROOT", str(exc)) from exc

    project_root = SKILL_ROOT.resolve()
    package_root = Path(__file__).resolve().parents[1]
    source_root = package_root.parent if package_root.name == "pwf_governed" else None
    repository_root = (
        source_root.parent
        if source_root is not None and (source_root.parent / ".git").exists()
        else None
    )
    home = Path.home().resolve()
    if not candidate.is_absolute():
        raise PlanningError("UNSAFE_STATE_ROOT", "state-root must be absolute")
    if candidate == Path("/") or candidate == home:
        raise PlanningError("UNSAFE_STATE_ROOT", "state-root cannot be the system root or home directory")
    if ".git" in candidate.parts:
        raise PlanningError("UNSAFE_STATE_ROOT", "state-root cannot be inside a .git directory")
    if candidate == project_root or project_root in candidate.parents:
        raise PlanningError("UNSAFE_STATE_ROOT", "state-root cannot be inside planning-with-files")
    if repository_root is not None and (candidate == repository_root or repository_root in candidate.parents):
        raise PlanningError("UNSAFE_STATE_ROOT", "state-root cannot be inside the source repository")
    return candidate

def _safe_instance_path(state_root: Path, task_id: str) -> Path:
    if task_id in {".", ".."} or "/" in task_id or "\\" in task_id:
        raise PlanningError("UNSAFE_TASK_ID", "task_id cannot escape the state-root")
    instance = state_root / task_id
    if instance.parent != state_root:
        raise PlanningError("UNSAFE_TASK_ID", "task_id must be one path segment")
    if instance.is_symlink():
        raise PlanningError("UNSAFE_STATE_ROOT", "task instance cannot be a symlink")
    return instance

def _read_json(path: Path, *, code: str = "INVALID_SCHEMA") -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanningError(code, f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PlanningError(code, f"JSON root must be an object: {path}")
    return value

def _load_task_envelope(path: str | Path) -> dict[str, Any]:
    value = _INPUT_ADAPTER(
        _read_json(Path(path).expanduser()),
        payload_kind="TaskEnvelope",
        callsite_id="task-envelope-read",
    )
    try:
        contracts.validate_task_envelope(value)
    except workflow.ContractError as exc:
        message = str(exc)
        code = (
            "UNSUPPORTED_SCHEMA_VERSION"
            if value.get("schema_version") is not None
            and value.get("schema_version") != contracts.PLAN_CONTRACT_SCHEMA_VERSION
            else "INVALID_CONTRACT"
        )
        raise PlanningError(code, message) from exc
    return copy.deepcopy(value)

def _load_risk_route(value: Any) -> Any:
    """Load a structured external result without classifying task text locally."""
    if value is _MISSING_ROUTE:
        return value
    if value is None:
        return {}
    if isinstance(value, dict):
        return copy.deepcopy(value)
    path = Path(value).expanduser()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PlanningError("INVALID_RISK_ROUTE", f"cannot read structured risk route: {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise PlanningError("INVALID_RISK_ROUTE", "structured risk route must be a JSON object")
    return loaded

def _parse_timestamp(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))

def _result_base(
    *,
    result: str,
    envelope: dict[str, Any],
    plan: dict[str, Any],
    state_root: Path,
    instance: Path,
) -> dict[str, Any]:
    profile_decision = plan.get("governance_profile")
    if isinstance(profile_decision, dict):
        decision = copy.deepcopy(profile_decision)
    else:
        decision = governance.resolve_governance_profile(
            envelope,
            requested_profile=str(plan.get("task_profile", "")),
            legacy=True,
        )
        decision["requested_profile"] = plan.get("task_profile")
        decision["effective_profile"] = plan.get("task_profile")
    result = {
        "result": result,
        "task_id": envelope["task_id"],
        "project_id": envelope["project_id"],
        "plan_id": plan["plan_id"],
        "plan_version": plan["plan_version"],
        "task_profile": plan["task_profile"],
        "state_root": str(state_root),
        "instance_path": str(instance),
        "task_envelope_digest": contracts.contract_digest(envelope),
        "plan_package_digest": contracts.contract_digest(plan),
        "created_files": [],
        "existing_files": [],
        "warnings": ["capability_refs is empty; no unregistered capability was auto-selected"],
        "blocking_findings": [],
        "no_op": False,
    }
    result.update(
        {
            "requested_profile": decision.get("requested_profile"),
            "supported_profile": decision.get("supported_profile"),
            "effective_profile": decision.get("effective_profile"),
            "risk_level": decision.get("risk_level"),
            "enabled_gates": copy.deepcopy(decision.get("enabled_gates", [])),
            "disabled_gates": copy.deepcopy(decision.get("disabled_gates", [])),
            "decision_reason": copy.deepcopy(decision.get("decision_reason", [])),
            "top_level_status": governance.normalize_top_level_status(
                plan.get("status_summary", {}).get("status") if isinstance(plan.get("status_summary"), dict) else None
            ),
        }
    )
    return result

def _cleanup_lock_parent(lock_path: Path) -> None:
    parent = lock_path.parent
    try:
        parent.rmdir()
    except OSError:
        pass

def _write_transaction(instance: Path, state_root: Path, files: dict[str, str], agent: str) -> list[str]:
    if state_root.exists() and not state_root.is_dir():
        raise PlanningError("UNSAFE_STATE_ROOT", "state-root exists but is not a directory")
    state_root.mkdir(parents=True, exist_ok=True)
    lock_path = state_root / ".planning" / f"plan-create-{instance.name}.lock"
    conflicts_dir = state_root / ".planning" / "conflicts"
    target_file = f"{instance.name}/plan-package.json"
    target = instance / "plan-package.json"
    base_digest = workflow.file_digest(target) if target.is_file() else workflow.sha256_digest("")
    try:
        lock = workflow.acquire_workflow_lock(lock_path, target_file, base_digest, agent, conflicts_dir)
    except workflow.ContractError as exc:
        _cleanup_lock_parent(lock_path)
        raise PlanningError("LOCK_CONFLICT", str(exc), result="CONFLICT") from exc

    staging: Path | None = None
    try:
        current_digest = workflow.file_digest(target) if target.is_file() else workflow.sha256_digest("")
        if current_digest != lock["base_digest"]:
            raise PlanningError("CONFLICT", "base digest changed before plan creation", result="CONFLICT")
        if instance.exists():
            raise PlanningError("TASK_ID_CONFLICT", "task instance appeared while acquiring lock", result="CONFLICT")
        staging = Path(tempfile.mkdtemp(prefix=f".{instance.name}.tmp-", dir=state_root))
        for relative, content in sorted(files.items()):
            workflow.atomic_write_text(staging / relative, content)
        if instance.exists():
            raise PlanningError("TASK_ID_CONFLICT", "task instance appeared before atomic publish", result="CONFLICT")
        staging.replace(instance)
        staging = None
        return sorted(files)
    except OSError as exc:
        raise PlanningError("FAILED", f"atomic plan publication failed: {exc}") from exc
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        try:
            workflow.release_lock(lock_path, process_id=lock["process_id"], host_name=lock["host_name"])
        finally:
            _cleanup_lock_parent(lock_path)

def _safe_component(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or ".." in value or "/" in value or "\\" in value:
        raise PlanningError("INVALID_IDENTIFIER", f"invalid {label}: {value!r}")
    normalized = "".join(char.lower() if char.isalnum() else "-" for char in value).strip("-.")
    while "--" in normalized:
        normalized = normalized.replace("--", "-")
    if not normalized or len(normalized) > 80:
        raise PlanningError("INVALID_IDENTIFIER", f"invalid {label}: {value!r}")
    return normalized

def _validate_instance_root(instance_root: str | Path) -> tuple[Path, Path]:
    candidate = Path(instance_root).expanduser()
    if not candidate.is_absolute():
        raise PlanningError("UNSAFE_INSTANCE_ROOT", "instance-root must be absolute")
    if candidate.is_symlink():
        raise PlanningError("UNSAFE_INSTANCE_ROOT", "instance-root cannot be a symlink")
    resolved = candidate.resolve(strict=False)
    if ".git" in resolved.parts:
        raise PlanningError("UNSAFE_INSTANCE_ROOT", "instance-root cannot be inside a .git directory")
    state_root = resolve_state_root(resolved.parent)
    expected = _safe_instance_path(state_root, resolved.name)
    if expected != resolved:
        raise PlanningError("UNSAFE_INSTANCE_ROOT", "instance-root must be directly under the resolved state-root")
    if not resolved.is_dir():
        raise PlanningError("INSTANCE_NOT_FOUND", f"task instance does not exist: {resolved}")
    return state_root, resolved

def _load_instance(instance_root: str | Path) -> tuple[Path, Path, dict[str, Any], dict[str, Any], str]:
    state_root, instance = _validate_instance_root(instance_root)
    envelope_path = instance / "task-envelope.json"
    plan_path = instance / "plan-package.json"
    checklist_path = instance / workflow.CHECKLIST_NAME
    if any(path.is_symlink() for path in (envelope_path, plan_path, checklist_path)):
        raise PlanningError("UNSAFE_INSTANCE_ROOT", "PLAN instance files cannot be symlinks")
    if not envelope_path.is_file() or not plan_path.is_file() or not checklist_path.is_file():
        raise PlanningError("INSTANCE_INCOMPLETE", f"task instance lacks required PLAN files: {instance}")
    envelope = _load_task_envelope(envelope_path)
    plan = _INPUT_ADAPTER(
        _read_json(plan_path),
        payload_kind="PlanPackage",
        callsite_id="plan-package-read",
    )
    try:
        contracts.validate_plan_package(plan)
    except workflow.ContractError as exc:
        raise PlanningError("INVALID_CONTRACT", f"invalid PlanPackage: {exc}") from exc
    if envelope["task_id"] != plan["task_id"] or envelope["project_id"] != plan["project_id"]:
        raise PlanningError("REFERENCE_MISMATCH", "TaskEnvelope and PlanPackage references differ")
    if envelope["task_id"] != instance.name:
        raise PlanningError("REFERENCE_MISMATCH", "instance directory does not match task_id")
    checklist = checklist_path.read_text(encoding="utf-8")
    try:
        workflow.validate_checklist_text(checklist)
    except workflow.ContractError as exc:
        raise PlanningError("INVALID_CHECKLIST", str(exc)) from exc
    return state_root, instance, envelope, plan, checklist

def _append_unique(values: list[Any], additions: Iterable[Any]) -> list[Any]:
    result = copy.deepcopy(values)
    for item in additions:
        if item not in result:
            result.append(copy.deepcopy(item))
    return result

def _transaction_write(
    instance: Path,
    state_root: Path,
    files: dict[str, str],
    *,
    expected_digests: dict[str, str],
    lock_target: str,
    lock_name: str,
    agent: str,
    transaction_tag: str = "f1-03",
) -> list[str]:
    """Stage and publish one or more instance files under the shared lock."""
    if state_root.exists() and not state_root.is_dir():
        raise PlanningError("UNSAFE_STATE_ROOT", "state-root exists but is not a directory")
    state_root.mkdir(parents=True, exist_ok=True)
    lock_path = state_root / ".planning" / f"{lock_name}-{instance.name}.lock"
    conflicts_dir = state_root / ".planning" / "conflicts"
    lock: dict[str, Any] | None = None
    target_path = instance / lock_target
    if target_path.is_symlink():
        raise PlanningError("UNSAFE_INSTANCE_ROOT", f"transaction target cannot be a symlink: {lock_target}")
    base_digest = expected_digests.get(lock_target, workflow.file_digest(target_path) if target_path.is_file() else workflow.sha256_digest(""))
    try:
        try:
            lock = workflow.acquire_workflow_lock(lock_path, f"{instance.name}/{lock_target}", base_digest, agent, conflicts_dir)
        except workflow.ContractError as exc:
            _cleanup_lock_parent(lock_path)
            raise PlanningError("LOCK_CONFLICT", str(exc), result="CONFLICT") from exc
        for relative, digest in expected_digests.items():
            current = instance / relative
            current_digest = workflow.file_digest(current) if current.is_file() else workflow.sha256_digest("")
            if current_digest != digest:
                raise PlanningError("CONFLICT", f"base digest changed before write: {relative}", result="CONFLICT")
        staging = Path(tempfile.mkdtemp(prefix=f".{instance.name}.{transaction_tag}-", dir=state_root))
        backup_dir = staging / ".backups"
        published: list[tuple[Path, Path | None, bool]] = []
        try:
            for relative, content in sorted(files.items()):
                workflow.atomic_write_text(staging / relative, content)
            for relative in sorted(files):
                target = instance / relative
                staged = staging / relative
                if target.is_symlink():
                    raise PlanningError("UNSAFE_INSTANCE_ROOT", f"transaction target cannot be a symlink: {relative}")
                backup: Path | None = None
                existed = target.exists()
                if existed:
                    backup = backup_dir / relative
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    backup.write_bytes(target.read_bytes())
                target.parent.mkdir(parents=True, exist_ok=True)
                staged.replace(target)
                published.append((target, backup, existed))
            return sorted(files)
        except OSError as exc:
            for target, backup, existed in reversed(published):
                try:
                    if existed and backup is not None and backup.exists():
                        backup.replace(target)
                    elif not existed and target.exists():
                        target.unlink()
                except OSError:
                    pass
            raise PlanningError("FAILED", f"atomic F1-03 transaction failed: {exc}") from exc
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
    finally:
        if lock is not None:
            try:
                workflow.release_lock(lock_path, process_id=lock["process_id"], host_name=lock["host_name"])
            finally:
                _cleanup_lock_parent(lock_path)

def _result_error(exc: PlanningError) -> dict[str, Any]:
    return {
        "result": exc.result,
        "error_code": exc.code,
        "error": exc.message,
        "warnings": [],
        "blocking_findings": [exc.message],
        "top_level_status": governance.normalize_top_level_status(exc.result, blocking_findings=[exc.code]),
        "no_op": False,
    }

def _string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]

def _scope_values(value: Any, *, include_exclude: bool = True) -> list[str]:
    if not isinstance(value, dict):
        return []
    fields = ("include", "exclude") if include_exclude else ("include",)
    result: list[str] = []
    for field in fields:
        result = _append_unique(result, _string_values(value.get(field)))
    return result

def _raw_file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def _canonical_object_digest(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()

def _markdown_field(text: str, label: str) -> str | None:
    pattern = re.compile(
        rf"^\s*(?:[-*]\s*)?(?:\*\*)?{re.escape(label)}(?:\*\*)?\s*:\s*(.+?)\s*$",
        re.MULTILINE,
    )
    match = pattern.search(text)
    return match.group(1).strip().strip("`") if match else None

def _explicit_dirty_paths(envelope: dict[str, Any], plan: dict[str, Any], checklist_metadata: dict[str, Any]) -> list[str]:
    result: list[str] = []
    sources = (
        envelope.get("known_dirty_paths"),
        plan.get("known_dirty_paths"),
        plan.get("governance_policy", {}).get("known_dirty_paths") if isinstance(plan.get("governance_policy"), dict) else None,
        checklist_metadata.get("known_dirty_paths"),
    )
    for source in sources:
        result = _append_unique(result, _string_values(source))
    return result

def _merge_dicts(*values: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for value in values:
        if isinstance(value, dict):
            result.update(copy.deepcopy(value))
    return result

def _merge_count_maps(*values: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        for key, raw in value.items():
            if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
                result[str(key)] = result.get(str(key), 0) + raw
    return result
