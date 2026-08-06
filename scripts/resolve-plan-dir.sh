#!/bin/sh
# Version source: ../VERSION
# Resolve the active planning directory through the local fail-closed runtime.
# The Python adapter is intentionally shared by macOS, Git Bash and WSL.
set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd) || exit 0
PYTHON_BIN=${PYTHON:-}
if [ -z "$PYTHON_BIN" ]; then
    PYTHON_BIN=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)
fi
[ -n "$PYTHON_BIN" ] || exit 0

if [ -n "${PLANNING_DIR:-}" ]; then
    "$PYTHON_BIN" "$SCRIPT_DIR/runtime.py" resolve --cwd "$PWD" --planning-dir "$PLANNING_DIR" --quiet "$@" || exit 0
    exit 0
fi
"$PYTHON_BIN" "$SCRIPT_DIR/runtime.py" resolve --cwd "$PWD" --quiet "$@" || exit 0
exit 0
