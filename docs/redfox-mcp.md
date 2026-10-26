# RedFox MCP 采集

在采集工作台的「采集方式」选择 **RedFox MCP**，选择小红书、抖音、X / Twitter 或 YouTube，再选择工具并填写参数。工具说明包含分页、日期、排序和异步任务规则。当前目录根据已配置服务公开的 39 个工具定义建立；服务工具变更时需更新 `api/services/redfox_tools.json`。

凭据依次读取本机服务进程的 `REDFOX_API_KEY` 和 `$CODEX_HOME/config.toml`（默认 `~/.codex/config.toml`）中相应 `redfox-<平台>-mcp` 的 `http_headers.REDFOX_API_KEY` 或 `env_http_headers.REDFOX_API_KEY`。只读取对应凭据，连接固定的 `https://mcp.redfox.hk/<平台>/mcp`，不执行配置中的命令。凭据不会发送给前端或写入仓库。远程工作台同样读取采集电脑的配置。

每次执行一个 MCP 工具调用；不会自动翻页、重试或轮询，以免重复扣费。HTTP 初始化后支持 JSON 和 SSE 响应、服务会话 ID。工具调用可能消耗 RedFox 积分。

结果保存为 `data/<平台>/redfox/<本机任务ID>.json`，可通过「数据文件 → 导出数据文件」预览和下载。JSON 保留远程 taskId 和翻页游标，供用户填写到结果查询或下一页调用中。异步评论、转写的“已完成”表示本次工具调用及保存完成，远程任务可能仍在处理。停止本机进程不会撤销已经提交的远程任务。

当前保存原始 MCP 结果，暂不导入内容资料库、内容研究、SQLite 或媒体下载。浏览器采集选项不用于 MCP 模式。任务记录会显示 RedFox 来源及具体工具。

仅针对 MCP 的本机验证命令：

```sh
uv run --frozen pytest tests/test_redfox.py -q
npm --prefix webui run build
uv run --frozen pytest tests/test_redfox_browser.py -q
```

上述测试使用本地模拟响应，不执行真实平台采集。页面测试只检查 MCP 表单与请求，不执行浏览器采集。
