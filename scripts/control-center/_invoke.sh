#!/usr/bin/env sh
set -eu

ACTION=${1:?"usage: _invoke.sh <start|stop|restart|status|dashboard> [arguments...]"}
shift

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
CONFIG=${RAG_CONTROL_CENTER_CONFIG:-ops/control-center/settings.yaml}

if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: the 'uv' command is required. Install uv and retry." >&2
    exit 127
fi

cd "$REPO_ROOT"

if [ "$ACTION" = "dashboard" ]; then
    exec uv run rag dashboard --config "$CONFIG" "$@"
fi

exec uv run rag services "$ACTION" --all --config "$CONFIG" "$@"

