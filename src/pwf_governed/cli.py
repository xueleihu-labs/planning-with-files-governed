"""Unified pwf CLI entry point.

Bridges to legacy implementations. Gate 2 will refactor these into proper modules.
"""
from __future__ import annotations

from pathlib import Path
import argparse
import sys

from pwf_governed import __version__
from pwf_governed.distribution import (
    EditionConflictError,
    assert_runtime_edition_compatible,
)
from pwf_governed.edition import (
    COMMUNITY_IDENTITY,
    adaptation_session,
    use_edition,
)
from pwf_governed._legacy.runtime import (
    doctor,
)

def _bridge_planning(argv: list[str]) -> int:
    """Bridge to legacy planning.py main()."""
    from pwf_governed._legacy.planning import main as planning_main
    return planning_main(argv)

def _bridge_runtime_doctor(argv: list[str]) -> int:
    """Bridge to legacy runtime.py doctor command."""
    from pwf_governed._legacy.runtime import doctor
    import os
    cwd = Path(os.getcwd())
    try:
        lines = doctor(cwd)
        for line in lines:
            print(line)
        return 0
    except Exception as e:
        print(f"doctor: {e}", file=sys.stderr)
        return 1

def _bridge_project_init(argv: list[str]) -> int:
    """Bridge to legacy project_init.py."""
    from pwf_governed._legacy.project_init import main as init_main
    return init_main(argv)

def _bridge_verify(argv: list[str]) -> int:
    """Bridge to legacy planning.py verify-plan."""
    return _bridge_planning(["verify-plan"] + argv)

def _bridge_create(argv: list[str]) -> int:
    """Bridge to legacy planning.py create-plan."""
    return _bridge_planning(["create-plan"] + argv)

def _bridge_checkpoint(argv: list[str]) -> int:
    """Bridge to legacy planning.py record-checkpoint-ref."""
    return _bridge_planning(["record-checkpoint-ref"] + argv)

def _bridge_resume(argv: list[str]) -> int:
    """Bridge to legacy planning.py resume-from-checkpoint."""
    return _bridge_planning(["resume-from-checkpoint"] + argv)

def _dispatch(raw_argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pwf",
        description="planning-with-files-governed CLI (v{})".format(__version__),
    )
    parser.add_argument("--version", action="version", version="pwf {}".format(__version__))

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("doctor", help="Run plan doctor diagnostics")
    sub.add_parser("init", help="Initialize a new planning project")
    sub.add_parser("create", help="Create a plan from a task envelope")
    sub.add_parser("checkpoint", help="Record a checkpoint reference")
    sub.add_parser("resume", help="Resume from a checkpoint")
    sub.add_parser("verify", help="Verify a plan summary")
    sub.add_parser("attest", help="Attest a plan with SHA-256")
    sub.add_parser("inject", help="Inject plan context into a hook")

    handlers = {
        "doctor": _bridge_runtime_doctor,
        "init": _bridge_project_init,
        "create": _bridge_create,
        "checkpoint": _bridge_checkpoint,
        "resume": _bridge_resume,
        "verify": _bridge_verify,
        "attest": lambda a: _bridge_planning(["attest-plan"] + a),
        "inject": lambda a: _bridge_planning(["inject-plan"] + a),
    }

    if raw_argv and raw_argv[0] in handlers:
        return handlers[raw_argv[0]](raw_argv[1:])

    args = parser.parse_args(raw_argv)

    if args.command is None:
        parser.print_help()
        return 0

    handler = handlers.get(args.command)
    if handler:
        return handler([])

    parser.print_help()
    return 1


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        assert_runtime_edition_compatible()
    except EditionConflictError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    with use_edition(COMMUNITY_IDENTITY), adaptation_session():
        return _dispatch(raw_argv)

if __name__ == "__main__":
    sys.exit(main())
