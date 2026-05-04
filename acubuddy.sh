#!/usr/bin/env bash
# Launcher: activates .venv, loads .env, runs opencode from the repo root.
# Forwards any extra arguments to opencode.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if [ -f .venv/bin/activate ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
else
    echo "ERROR: no venv at .venv/bin/activate. Create one with: python -m venv .venv" >&2
    exit 1
fi

if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
else
    echo "Warning: no .env file. Copy .env.example to .env and add DEEPSEEK_API_KEY." >&2
fi

if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
    echo "Warning: DEEPSEEK_API_KEY not set. OpenCode will fail to call DeepSeek." >&2
fi

exec opencode "$@"
