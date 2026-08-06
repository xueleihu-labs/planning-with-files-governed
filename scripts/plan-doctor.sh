#!/bin/sh
# Version source: ../VERSION
# Diagnostic-only runtime self-check. It does not alter plan state.
set -u
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd) || exit 0
PYTHON_BIN=${PYTHON:-}
if [ -z "$PYTHON_BIN" ]; then
    PYTHON_BIN=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)
fi
[ -n "$PYTHON_BIN" ] || { echo "WARN  Python unavailable"; exit 0; }
exec "$PYTHON_BIN" "$SCRIPT_DIR/runtime.py" doctor --cwd "$PWD" "$@"
