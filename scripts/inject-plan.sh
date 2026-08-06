#!/bin/sh
# Version source: ../VERSION
# Emit plan context for hook adapters. Always exits cleanly for hook safety.
set -u
[ "${PLANNING_DISABLED:-}" = "1" ] && exit 0
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd) || exit 0
PYTHON_BIN=${PYTHON:-}
if [ -z "$PYTHON_BIN" ]; then
    PYTHON_BIN=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)
fi
[ -n "$PYTHON_BIN" ] || exit 0
CONTEXT=userprompt
TASK_ID=${PWF_TASK_ID:-}
while [ "$#" -gt 0 ]; do
    case "$1" in
        --context)
            [ "$#" -ge 2 ] || exit 0
            CONTEXT=$2
            shift 2
            ;;
        --context=*)
            CONTEXT=${1#--context=}
            shift
            ;;
        --task-id)
            [ "$#" -ge 2 ] || exit 0
            TASK_ID=$2
            shift 2
            ;;
        --task-id=*)
            TASK_ID=${1#--task-id=}
            shift
            ;;
        *) shift ;;
    esac
done
set -- "$PYTHON_BIN" "$SCRIPT_DIR/runtime.py" inject --cwd "$PWD" --context "$CONTEXT"
[ -z "$TASK_ID" ] || set -- "$@" --task-id "$TASK_ID"
"$@" || true
exit 0
