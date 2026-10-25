#!/bin/bash
source "$(dirname "$0")/scripts/local-env.sh"
if curl --noproxy "*" -fsS --max-time 2 http://127.0.0.1:8080/api/health >/dev/null 2>&1; then
  echo '8080 端口已有服务，请在打开的页面确认 MediaCrawler。'
  open http://127.0.0.1:8080
  exit 0
fi
if [ ! -x .venv/bin/python ] || [ ! -f api/webui/index.html ]; then
  echo '部署不完整，请先完成依赖安装和前端构建。'
  read -r -p '按回车关闭…'
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
echo 'MediaCrawler：http://127.0.0.1:8080'
echo '在本窗口按 Control+C 停止服务。'
exec .venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8080
