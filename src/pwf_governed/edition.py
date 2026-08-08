"""Version-neutral edition boundary for the shared PWF core."""

from __future__ import annotations

import copy
import functools
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, TypeVar


class EditionBoundaryError(ValueError):
    """Raised when an edition boundary contract is violated."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


NOT_HANDLED = object()


@dataclass(frozen=True)
class ProjectInitHooks:
    """The eight evidence-backed project-init extension points."""

    def extend_parser(self, parser: Any) -> None:
        return None

    def resolve_root(self, args: Any) -> Any:
        return NOT_HANDLED

    def resource_roots(self, shared_root: Any) -> tuple[Any, ...]:
        return (shared_root,)

    def prepare(
        self,
        args: Any,
        root: Path,
        edition_root: Path,
        values: dict[str, str],
        active_layout: Any,
    ) -> dict[str, str]:
        return values

    def handle_readonly(
        self,
        args: Any,
        root: Path,
        edition_root: Path,
        values: dict[str, str],
        active_layout: Any,
    ) -> Any:
        return NOT_HANDLED

    def after_create(
        self,
        args: Any,
        root: Path,
        edition_root: Path,
        values: dict[str, str],
        active_layout: Any,
    ) -> Any:
        return NOT_HANDLED

    def index_preflight(self, args: Any, root: Path, edition_root: Path) -> Any:
        return NOT_HANDLED

    def index_update(
        self,
        args: Any,
        root: Path,
        edition_root: Path,
        agent: str,
        active_layout: Any,
        preflight: Any,
    ) -> Any:
        return NOT_HANDLED


@dataclass(frozen=True)
class EditionBoundary:
    """Pure boundary behavior injected around the shared canonical core."""

    name: str = "COMMUNITY"
    project_init_hooks: ProjectInitHooks = field(default_factory=ProjectInitHooks)
    publication_destination: str = "external-publishing-system"
    state_namespace: str = "pwf"

    def adapt_input(self, payload_kind: str, value: Mapping[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(dict(value))

    def adapt_output(self, payload_kind: str, value: Mapping[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(dict(value))

    def format_path(self, value: str | Path) -> str:
        return Path(value).as_posix()

    def defaults(self) -> dict[str, str]:
        return {
            "win_root": "",
            "wsl_root": "",
            "business_line": "",
            "input_dir": "",
            "output_dir": "",
        }


COMMUNITY_IDENTITY = EditionBoundary()

_BOUNDARY: ContextVar[EditionBoundary] = ContextVar(
    "pwf_edition_boundary",
    default=COMMUNITY_IDENTITY,
)
_ADAPTATION_ACTIVE: ContextVar[bool] = ContextVar(
    "pwf_adaptation_active",
    default=False,
)
_ADAPTATION_TRACE: ContextVar[frozenset[tuple[str, str, int]]] = ContextVar(
    "pwf_adaptation_trace",
    default=frozenset(),
)
_ADAPTATION_KEEP_ALIVE: ContextVar[list[Any]] = ContextVar(
    "pwf_adaptation_keep_alive",
    default=[],
)

_ALLOWED_CALLSITES: dict[str, tuple[str, frozenset[str]]] = {
    "task-envelope-read": ("INPUT", frozenset({"TaskEnvelope"})),
    "plan-package-read": ("INPUT", frozenset({"PlanPackage"})),
    "plan-package-write": ("OUTPUT", frozenset({"PlanPackage"})),
    "checkpoint-reference-read": ("INPUT", frozenset({"CheckpointRef"})),
    "checkpoint-reference-write": ("OUTPUT", frozenset({"CheckpointRef"})),
    "resume-reference-read": ("INPUT", frozenset({"CheckpointRef"})),
    "resume-record-write": ("OUTPUT", frozenset({"ResumeRecord"})),
    "handoff-write": ("OUTPUT", frozenset({"KnowledgeHandoff"})),
    "handoff-read": ("INPUT", frozenset({"KnowledgeHandoff"})),
    "owner-gate-receipt-read": ("INPUT", frozenset({"OwnerGateReceipt"})),
    "owner-gate-receipt-write": ("OUTPUT", frozenset({"OwnerGateReceipt"})),
    "summary-read-set": (
        "INPUT",
        frozenset({"PlanPackage", "CheckpointRef", "ResumeRecord", "KnowledgeHandoff"}),
    ),
    "community-cli-output": ("OUTPUT", frozenset({"CLIResult"})),
    "internal-cli-output": ("OUTPUT", frozenset({"CLIResult"})),
}


def current_edition() -> EditionBoundary:
    return _BOUNDARY.get()


@contextmanager
def use_edition(boundary: EditionBoundary) -> Iterator[EditionBoundary]:
    if not isinstance(boundary, EditionBoundary):
        raise TypeError("boundary must be an EditionBoundary")
    token = _BOUNDARY.set(boundary)
    try:
        yield boundary
    finally:
        _BOUNDARY.reset(token)


@contextmanager
def adaptation_session() -> Iterator[None]:
    if _ADAPTATION_ACTIVE.get():
        yield
        return
    active_token = _ADAPTATION_ACTIVE.set(True)
    trace_token = _ADAPTATION_TRACE.set(frozenset())
    keep_alive_token = _ADAPTATION_KEEP_ALIVE.set([])
    try:
        yield
    finally:
        _ADAPTATION_KEEP_ALIVE.reset(keep_alive_token)
        _ADAPTATION_TRACE.reset(trace_token)
        _ADAPTATION_ACTIVE.reset(active_token)


F = TypeVar("F", bound=Callable[..., Any])


def edition_operation(function: F) -> F:
    """Run one public operation with an isolated adaptation trace."""

    @functools.wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        with adaptation_session():
            return function(*args, **kwargs)

    return wrapped  # type: ignore[return-value]


def _adapt_once(
    direction: str,
    value: Mapping[str, Any],
    *,
    payload_kind: str,
    callsite_id: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EditionBoundaryError("PWF_ADAPTER_INPUT_INVALID", "adapter payload must be a JSON object")
    contract = _ALLOWED_CALLSITES.get(callsite_id)
    if contract is None or contract[0] != direction or payload_kind not in contract[1]:
        raise EditionBoundaryError(
            "PWF_ADAPTER_CALLSITE_NOT_ALLOWED",
            f"{direction.lower()} adaptation is not registered for {callsite_id}:{payload_kind}",
        )
    if not _ADAPTATION_ACTIVE.get():
        with adaptation_session():
            return _adapt_once(
                direction,
                value,
                payload_kind=payload_kind,
                callsite_id=callsite_id,
            )
    key = (direction, payload_kind, id(value))
    trace = _ADAPTATION_TRACE.get()
    if key in trace:
        raise EditionBoundaryError(
            "PWF_DOUBLE_ADAPT",
            f"payload already adapted at {callsite_id}:{payload_kind}:{direction.lower()}",
        )
    _ADAPTATION_KEEP_ALIVE.set(_ADAPTATION_KEEP_ALIVE.get() + [value])
    _ADAPTATION_TRACE.set(trace | {key})
    boundary = current_edition()
    if direction == "INPUT":
        return boundary.adapt_input(payload_kind, value)
    return boundary.adapt_output(payload_kind, value)


def adapt_input_once(
    value: Mapping[str, Any],
    *,
    payload_kind: str,
    callsite_id: str,
) -> dict[str, Any]:
    return _adapt_once(
        "INPUT",
        value,
        payload_kind=payload_kind,
        callsite_id=callsite_id,
    )


def adapt_output_once(
    value: Mapping[str, Any],
    *,
    payload_kind: str,
    callsite_id: str,
) -> dict[str, Any]:
    return _adapt_once(
        "OUTPUT",
        value,
        payload_kind=payload_kind,
        callsite_id=callsite_id,
    )


from pwf_governed.core.envelope import bind_input_adapter
from pwf_governed._legacy import plan_contracts as _checkpoint_contracts
from pwf_governed._legacy import workflow_contracts as _checkpoint_workflow
from pwf_governed.shared import checkpoint_support as _checkpoint_support


bind_input_adapter(adapt_input_once)
_checkpoint_support.Path = Path
_checkpoint_support.Any = Any
_checkpoint_support.contracts = _checkpoint_contracts
_checkpoint_support.workflow = _checkpoint_workflow


__all__ = [
    "COMMUNITY_IDENTITY",
    "EditionBoundary",
    "EditionBoundaryError",
    "NOT_HANDLED",
    "ProjectInitHooks",
    "adapt_input_once",
    "adapt_output_once",
    "adaptation_session",
    "current_edition",
    "edition_operation",
    "use_edition",
]
