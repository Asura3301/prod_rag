#!/usr/bin/env sh
set -eu

if command -v pgrep >/dev/null 2>&1 && pgrep -f "LM Studio|lm-studio" >/dev/null 2>&1; then
    echo "LM Studio Desktop is already running."
    exit 0
fi

if [ "$(uname -s)" = "Darwin" ]; then
    exec open -a "LM Studio"
fi

if [ -n "${LM_STUDIO_EXE:-}" ] && [ -x "$LM_STUDIO_EXE" ]; then
    "$LM_STUDIO_EXE" >/dev/null 2>&1 &
    echo "LM Studio Desktop launched."
    exit 0
fi

for application in lm-studio lmstudio; do
    if command -v "$application" >/dev/null 2>&1; then
        "$application" >/dev/null 2>&1 &
        echo "LM Studio Desktop launched."
        exit 0
    fi
done

echo "LM Studio Desktop was not found. Install it or set LM_STUDIO_EXE." >&2
exit 1
