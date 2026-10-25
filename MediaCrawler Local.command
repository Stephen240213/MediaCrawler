#!/bin/bash
source "$(dirname "$0")/scripts/local-env.sh"
if curl --noproxy "*" -fsS --max-time 2 http://127.0.0.1:8080/api/health >/dev/null 2>&1; then
  echo 'Port 8080 is in use. Verify MediaCrawler in the opened page.'
  open http://127.0.0.1:8080
  exit 0
fi
if [ ! -x .venv/bin/python ] || [ ! -f api/webui/index.html ]; then
  echo 'Setup is incomplete. Install dependencies and build the WebUI first.'
  read -r -p 'Press Return to close...'
  exit 1
fi
(
  for attempt in {1..30}; do
    if curl --noproxy "*" -fsS --max-time 1 http://127.0.0.1:8080/api/health >/dev/null 2>&1; then
      open http://127.0.0.1:8080
      exit
    fi
    sleep 1
  done
) &
echo 'MediaCrawler: http://127.0.0.1:8080'
echo 'Press Control+C in this window to stop the service.'
exec .venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8080
