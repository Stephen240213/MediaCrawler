#!/usr/bin/env bash
# Codex Cloud install script. Run from any directory inside the checkout.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
command -v uv >/dev/null || { echo 'Install uv in the cloud environment first.' >&2; exit 1; }
command -v npm >/dev/null || { echo 'Node.js/npm is required (Node 22 recommended).' >&2; exit 1; }
uv sync --frozen
# Install browser libraries on Linux; macOS requires only the browser download.
if [[ "$(uname -s)" == Linux ]]; then
  uv run --frozen playwright install --with-deps chromium
else
  uv run --frozen playwright install chromium
fi
npm --prefix webui ci
echo 'Dependencies ready. Run bash scripts/cloud-check.sh.'
