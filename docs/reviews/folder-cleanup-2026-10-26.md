# 2026-10-26 项目目录整理记录

本次在本机 macOS 执行文件整理，经用户确认清理方向，并明确同意“备份只留验证过的最新一份”。没有改变源码、依赖声明、锁文件、配置或启动脚本。

## 文档归档

| 原位置（项目根目录起算） | 新位置 |
| --- | --- |
| `ANALYTICS.md` | `docs/workbench/ANALYTICS.md` |
| `CONTENT_REMIX_AGENT.md` | `docs/workbench/CONTENT_REMIX_AGENT.md` |
| `WORKBENCH.md` | `docs/workbench/WORKBENCH.md` |
| `WORKBENCH_IMPROVEMENTS.md` | `docs/reviews/WORKBENCH_IMPROVEMENTS.md` |
| `CLOUD_DEVELOPMENT.md` | `docs/development/CLOUD_DEVELOPMENT.md` |
| `REMOTE_WORKBENCH.md` | `docs/deployment/REMOTE_WORKBENCH.md` |
| `本机使用说明.md` | `docs/usage/本机使用说明.md` |
| `deploy/remote/MACOS_LAUNCHER.md` | `docs/deployment/MACOS_LAUNCHER.md` |
| `deploy/remote/MACOS_WORKERS_DEV.md` | `docs/deployment/MACOS_WORKERS_DEV.md` |
| `deploy/remote/MOSS_TRANSCRIPTION.md` | `docs/workbench/MOSS_TRANSCRIPTION.md` |
| `deploy/remote/CONTENT_REMIX_CREATION.md` | `docs/workbench/CONTENT_REMIX_CREATION.md` |
| `deploy/remote/CONTENT_PROJECTS_ACCEPTANCE.md` | `docs/reviews/CONTENT_PROJECTS_ACCEPTANCE.md` |
| `deploy/remote/REMIX_LEARNING_ACCEPTANCE.md` | `docs/reviews/REMIX_LEARNING_ACCEPTANCE.md` |
| `deploy/remote/REMIX_MEDIA_ACCEPTANCE.md` | `docs/reviews/REMIX_MEDIA_ACCEPTANCE.md` |

新增根目录 `项目目录说明.md` 与 `docs/README.md`，在根 README 添加导航。归档文档注明命令及行内文件路径仍以项目根目录为基准；修正远程工作台说明中的相对文档链接。

## 本机清理

- 删除 `.cache/uv/` 的下载及构建缓存，保留锁文件；清理时取得 uv 缓存独占锁。
- 删除 `.cache/cached-wheels/`、`.cache/download-wheels/` 和缓存目录中的旧安装辅助脚本、依赖导出、诊断输出。保留 `.cache/matplotlib/`。
- 删除未使用的本机 `.pnpm-store/`。删除前确认当前安装环境的符号链接没有指向待清理缓存。
- 清理源码及文档范围中的 `__pycache__/`、`.pytest_cache/`、`.DS_Store`；跳过数据、浏览器配置、运行状态、依赖目录和 `.git/`。
- 将根目录 `error.log` 归档为本机 `.runtime/logs/archived/error-20261026.log`，不提交日志内容。
- 删除完整备份 `workspace-20261026T022058429200Z` 与 `workspace-20261026T035027642439Z`。
- 保留 `.runtime/backups/workspace-20261026T051456099632Z/`。`.runtime/repair-backups/` 是修复前快照，另行保留。

清理前磁盘占用 4,816,117,760 字节，清理后 2,367,500,288 字节，减少 2,448,617,472 字节，约 2.28 GiB。这是本机 `du -sk` 的目录占用差值，不等同于 APFS 实际可用空间的变化；文档编辑和之后使用会使数字略有变化。

## 验证与边界

- 使用已安装的本机 Python 执行项目自带 `api.backup --verify`。最新备份在删除前与清理后均验证通过：574 个文件，创建时间 2026-10-26 13:14:56（北京时间）。检查包括文件大小、SHA-256、SQLite 完整性和素材引用。
- 文档归档后，14 份移动文档的原文保留检查、33 个本地 Markdown 相对链接、移动目标和 Git 差异格式检查通过。确认仅剩一份完整备份，启动入口、Python 解释器与前端成品仍在原位置。
- 源码、启动脚本、配置和锁文件没有改动；数据、登录态、在用运行环境和前端成品保留。提交范围仅限 Markdown 文档，不含本机缓存清理和私有文件。
- 当前会话没有可用的云端代码执行入口。按 `AGENTS.md` 的环境约束，没有在本机执行依赖安装、构建或项目回归测试；`scripts/cloud-check.sh` 未执行。本次备份校验和文件检查不代表云端环境或真实采集验收。
- 没有启动真实采集、交互登录或发布；保留的备份是历史快照，并非本轮新生成的全量备份。
