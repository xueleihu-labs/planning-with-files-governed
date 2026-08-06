#!/bin/sh
# Version source: ../VERSION
# Lock, show or clear the active task_plan.md SHA-256 attestation.
set -u
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd) || exit 2
PYTHON_BIN=${PYTHON:-}
if [ -z "$PYTHON_BIN" ]; then
    PYTHON_BIN=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)
fi
[ -n "$PYTHON_BIN" ] || { echo "[plan-attest] Python is required" >&2; exit 2; }
MODE_ARG=""
TASK_ID=${PWF_TASK_ID:-}
while [ "$#" -gt 0 ]; do
    case "$1" in
        --show|--clear)
            [ -z "$MODE_ARG" ] || { echo "Usage: $0 [--show|--clear] [--task-id ID]" >&2; exit 2; }
            MODE_ARG=$1
            shift
            ;;
        --task-id)
            [ "$#" -ge 2 ] || { echo "Usage: $0 [--show|--clear] [--task-id ID]" >&2; exit 2; }
            TASK_ID=$2
            shift 2
            ;;
        --task-id=*)
            TASK_ID=${1#--task-id=}
            shift
            ;;
        *) echo "Usage: $0 [--show|--clear] [--task-id ID]" >&2; exit 2 ;;
    esac
done
set -- "$PYTHON_BIN" "$SCRIPT_DIR/runtime.py" attest --cwd "$PWD"
[ -z "$MODE_ARG" ] || set -- "$@" "$MODE_ARG"
[ -z "$TASK_ID" ] || set -- "$@" --task-id "$TASK_ID"
exec "$@"
