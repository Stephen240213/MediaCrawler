# MediaCrawler 文档导航

这里汇总本机工作台和上游采集器的说明。文档中的命令默认在项目根目录执行；移动文档没有改变程序、数据或启动脚本的位置。

## 使用与功能

- [本机使用说明](usage/本机使用说明.md)：双击启动、登录、结果位置。
- [本机内容工作台](workbench/WORKBENCH.md)：素材、草稿、任务和存储。
- [分析功能](workbench/ANALYTICS.md)：分析流程、数据库和迁移。
- [内容改编 Agent](workbench/CONTENT_REMIX_AGENT.md)：研究、创作与制作流程。
- [内容创作说明](workbench/CONTENT_REMIX_CREATION.md)。
- [MOSS 转写说明](workbench/MOSS_TRANSCRIPTION.md)。
- [RedFox MCP](redfox-mcp.md)：连接与使用说明。

## 开发与部署

- [Codex Cloud 开发](development/CLOUD_DEVELOPMENT.md)：环境配置与验证流程。
- [远程工作台](deployment/REMOTE_WORKBENCH.md)：远程访问、备份和恢复。
- [macOS 启动程序](deployment/MACOS_LAUNCHER.md)。
- [macOS Workers 临时部署](deployment/MACOS_WORKERS_DEV.md)。

开发约束以项目根目录的 `AGENTS.md` 为准。安装脚本、服务模板和 Worker 源码继续位于 `deploy/remote/`。

## 评审与验收记录

`reviews/` 保存设计研究、阶段验收和历史记录；这些记录描述当时的交付范围，当前操作以对应使用文档为准。

- [工作台改进记录](reviews/WORKBENCH_IMPROVEMENTS.md)。
- [内容项目验收](reviews/CONTENT_PROJECTS_ACCEPTANCE.md)。
- [学习流程验收](reviews/REMIX_LEARNING_ACCEPTANCE.md)。
- [媒体制作验收](reviews/REMIX_MEDIA_ACCEPTANCE.md)。
- [2026-10-26 目录整理记录](reviews/folder-cleanup-2026-10-26.md)。

## 上游文档与站点资源

原有上游文档保留现有地址，以保持 VitePress 页面与图片引用：

- [上游使用首页](index.md)。
- [采集器代码结构](项目代码结构.md)。
- [项目架构](项目架构文档.md)。
- [常见问题](常见问题.md)。
- [数据存储](data_storage_guide.md)。
- [Excel 导出](excel_export_guide.md)。

`.vitepress/` 是文档站点配置，`static/` 是图片资源，`proxylane/` 是原有代理服务文档。`STZHONGS.TTF` 和 `hit_stopwords.txt` 是功能资源，保留原位置。
