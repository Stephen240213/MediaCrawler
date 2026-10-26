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
  # Media fixture tests need FFmpeg and a CJK font, not real platform access.
  setup_apt=(apt-get)
  if [[ "$(id -u)" != 0 ]]; then setup_apt=(sudo apt-get); fi
  "${setup_apt[@]}" update
  "${setup_apt[@]}" install -y --no-install-recommends ffmpeg fonts-noto-cjk
else
  uv run --frozen playwright install chromium
  command -v ffmpeg >/dev/null || { echo 'Install FFmpeg before running media fixture checks.' >&2; exit 1; }
fi
npm --prefix webui ci
echo 'Dependencies ready. Run bash scripts/cloud-check.sh.'
