#!/usr/bin/env bash
# No real platform login, collection, or publishing is performed.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export MPLBACKEND=Agg
export MPLCONFIGDIR="${TMPDIR:-/tmp}/mediacrawler-matplotlib"
uv run --frozen pytest \
  tests/test_xhs_browser_filters.py tests/test_workbench.py \
  tests/test_api_limits.py tests/test_cdp_browser.py \
  tests/test_xhs_core_access_error.py tests/test_xhs_raw_response_errors.py -q
npm --prefix webui run build
