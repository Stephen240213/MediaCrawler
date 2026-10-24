#!/bin/bash
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$PROJECT_ROOT/.local-tools/bin:/Users/yiwen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH"
export UV_CACHE_DIR="$PROJECT_ROOT/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$PROJECT_ROOT/.local-tools/python"
export UV_FROZEN=true
cd "$PROJECT_ROOT" || exit 1
export UV_NO_SYNC=true
export MPLCONFIGDIR="$PROJECT_ROOT/.cache/matplotlib"
mkdir -p "$MPLCONFIGDIR"
