#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
export PATH="/opt/homebrew/opt/node@22/bin:/usr/local/opt/node@22/bin:/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
if [[ ! -x .venv/bin/python ]]; then
  echo 'Python environment is missing. Complete uv sync --frozen first.'
  read -r -p 'Press Return to close...'
  exit 1
fi
install_result=0
.venv/bin/python -m api.desktop install || install_result=$?
if [[ "$install_result" -eq 0 ]]; then
  echo 'Install complete. Open MediaCrawler Start in Applications/MediaCrawler.'
else
  echo 'Install failed. Keep the error output for troubleshooting.'
fi
read -r -p 'Press Return to close...'
exit "$install_result"
