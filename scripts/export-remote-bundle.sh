#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
target=${1:?Usage: bash scripts/export-remote-bundle.sh /absolute/path/remote-workbench.bundle}
[[ "$target" = /* ]] || { echo 'Use an absolute destination path.' >&2; exit 1; }
[[ ! -e "$target" ]] || { echo 'Destination exists; refusing to overwrite.' >&2; exit 1; }
git diff --quiet && git diff --cached --quiet || { echo 'Commit tracked changes before exporting.' >&2; exit 1; }
# Includes complete reachable Git history, never untracked data or credentials.
git bundle create "$target" feat/remote-workbench
git bundle verify "$target"
