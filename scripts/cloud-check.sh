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
# Content production regressions: isolated model/media fixtures, no real accounts.
uv run --frozen pytest \
  tests/test_content_remix.py tests/test_remix_creation.py tests/test_remix_learning.py \
  tests/test_remix_moss.py tests/test_remix_production.py tests/test_remix_responses.py \
  tests/test_remix_source_links.py tests/test_remote_access.py \
  tests/test_content_projects.py tests/test_asset_catalog.py tests/test_workspace_backup.py -q
# Requires the production bundle built above. Browser requests use local fixtures.
uv run --frozen pytest tests/test_remix_browser.py tests/test_projects_browser.py -q
