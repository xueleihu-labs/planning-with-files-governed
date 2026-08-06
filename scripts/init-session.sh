#!/usr/bin/env bash
# Version source: ../VERSION
# Cross-platform project foundation wrapper. Legacy positional project name is supported.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON:-}"
if [ -z "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(command -v python3 || command -v python)"
fi
exec "$PYTHON_BIN" "$SCRIPT_DIR/project_init.py" "$@"
