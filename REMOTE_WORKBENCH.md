# 远程访问采集电脑上的 MediaCrawler

本版本让手机、平板和其他电脑通过 HTTPS 浏览器访问指定电脑的工作台。采集进程、平台登录浏览器、SQLite、草稿与素材均留在采集电脑。Cloudflare 转发网页访问内容，不建立云端采集数据库副本。浏览器关闭或掉线不会主动停止已提交任务。

## 入口与执行器

```text
远程浏览器 → Cloudflare Access → 固定 Tunnel → 127.0.0.1:8081（强制 JWT）
本机浏览器 ─────────────────────────────────→ 127.0.0.1:8080（本机入口）
                                                     ↓
                                    同一 API / 同一任务执行器 / 同一本机存储
```

两个端口由 `python -m api.serve` 的同一个进程启动，不要分别运行两个 Uvicorn 服务，不要配置多个 worker。后端只绑定回环地址，不需要开放路由器端口。Tunnel **只能**转发到受保护的 8081，不能转到 8080、Chrome 调试端口、Vite 开发服务或其他服务。

- 远程入口验证 Access JWT 的 RS256 签名、固定团队签发方、受众、有效期和账号名单；只信任签名后的身份，不信任邮箱请求头、客户端地址或转发头。静态网页、下载、API、WebSocket 都经过验证。
- 本机入口也校验 Host、写入 Origin 和 CSRF，避免 DNS 重绑定与跨站写入；本机访问统一使用 `http://127.0.0.1:8080`，不要换成其他 Host。
- 写入需要同源 Origin、当前操作者对应的 CSRF；服务重启导致 CSRF 变化时，前端刷新会话并且最多重试一次。CSRF 校验在业务操作前进行。
- 操作员可以启动/停止、导入、编辑；只读账号可以查看全工作台数据、任务、日志、草稿和素材，以及下载导出，不提供行级/任务所有权隔离。UI 禁用写入，后端仍独立拒绝越权。
- JWT 密钥仅从配置团队的 `/cdn-cgi/access/certs` 经 TLS 获取，缓存 5 分钟，限制未知 key 刷新频率。身份服务不可用且没有有效缓存时拒绝访问，不降级为匿名。
- WebSocket 连接验证 Origin，并在 JWT 到期后关闭；会话过期界面提示重新登录。所有受保护响应禁止缓存。写入审计不能记录时先拒绝操作。

## 在采集电脑安装

先备份现有数据库、`data/`、浏览器配置和 `.runtime/`。保持原有登录浏览器方式；云端没有你的登录态，不能通过复制 Git 仓库获得。

### 1. 导入 Git bundle（无需 GitHub）

云端开发分支为 `feat/remote-workbench`。交付 bundle 包含完整可达 Git 提交历史，不包含忽略文件、运行数据、依赖目录或凭据。先验证：

```bash
git bundle verify /path/to/mediacrawler-remote-workbench.bundle
```

已有本机仓库：确认工作区干净后导入，不执行 reset/force：

```bash
cd /path/to/MediaCrawler
git status --short --branch
git fetch /path/to/mediacrawler-remote-workbench.bundle feat/remote-workbench:feat/remote-workbench
git switch feat/remote-workbench
```

若本机已有同名分支，先检查其提交与本地改动，使用不同分支名导入后审阅、正常合并，不覆盖原分支。全新安装可使用 `git clone -b feat/remote-workbench /path/to/bundle MediaCrawler`。后续升级重复备份、导入新分支并正常合并，不盲目覆盖本机数据。

安装 Python 3.11、uv、Node 22，然后在该目录运行：

```bash
uv sync --frozen
npm --prefix webui ci
npm --prefix webui run build
```

遵循原项目的本机浏览器配置。不要把云端测试的 `/usr/bin/chromium` 路径复制到 Windows/macOS。现有系统浏览器的登录、CDP 确认和二维码扫描都在采集电脑完成。不要将浏览器调试端口绑定公网。

### 2. 配置固定 Tunnel 和 Access

准备 Cloudflare 管理的域名/子域名，例如 `crawler.example.com`，并按 Cloudflare 官方安装流程安装 cloudflared、创建 named tunnel 和 DNS 路由。在 Cloudflare Zero Trust 创建 **Self-hosted Access application**，保护整个 `crawler.example.com`，包括 `/`、`/api/*`、`/assets/*` 和 WebSocket；不要创建绕过策略或公开健康检查例外。Allow 策略只包含指定账号，其他访问拒绝。复制该 application 的 AUD 与团队名。

按 `deploy/remote/cloudflared.yml.example` 在本机私有目录创建配置：

- 固定隧道 UUID 和凭据文件由本机 cloudflared 管理，不提交 Git。
- 域名改为真实子域名，服务固定为 `http://127.0.0.1:8081`，`httpHostHeader` 与真实域名完全一致。
- 保留最终 `http_status:404`，不添加任意端口或目录的转发。
- cloudflared 和配置文件均放在采集电脑，按官方流程登录；不在云端开发环境中建立实际隧道。

Access 与 Tunnel 费用、免费额度以开通时的服务条款为准。本项目没有自动创建 Cloudflare 账号、DNS、Access 应用或隧道的能力。

### 3. 配置并启动应用

把 `deploy/remote/remote.env.example` 复制到本机私有配置目录，填写团队名、AUD、真实 HTTPS 源地址，以及操作员和只读邮箱。角色名单不能重叠，至少一个操作员；邮箱需与 Access JWT 声明一致。无须填写或粘贴 JWT、Cookie 或 Tunnel token。

macOS/Linux 建议配置文件权限 `chmod 600`；Windows 通过文件 ACL 只允许当前用户读取。必要时设置 `MC_TOOL_PATH`，让登录后启动的服务能找到 uv 与 Node；macOS/Linux 用冒号分隔目录，Windows 用分号。使用绝对路径。

停止旧的 `uvicorn api.main:app` 服务，再在项目根目录启动：

```bash
uv run --frozen python -m api.serve --env-file /absolute/private/path/remote.env
```

另一个终端按你的固定配置启动 Tunnel：

```bash
cloudflared --config /absolute/private/path/config.yml tunnel run
```

本机：`http://127.0.0.1:8080`；其他设备：你配置的 `https://crawler.example.com`。公开地址由实际 Cloudflare 配置决定，云端没有生成或发布该地址。采集电脑必须开机、联网并保持所需的桌面会话；睡眠、关机或退出登录会影响任务。

仅本机验证时可以显式运行 `python -m api.serve --local-only --local-port 8090`；这不会启动远程入口。缺少远程配置时，默认启动会报错退出，不会暗中关闭鉴权继续运行。

## 任务、日志与平台登录

- 提交接口支持 `Idempotency-Key`；相同操作者、相同 key 与参数返回原 task ID，不再次启动。相同 key 搭配不同参数返回 409。前端在会话存储中只保留参数摘要与随机请求标识，不保存 Cookie 或原始参数；超时重试复用该标识。
- 全局一次只接纳一个任务。SQLite 事务确保并发接纳互斥，服务进程锁阻止多个受管服务，执行进程锁防止遗留采集进程仍存在时重复启动。
- 停止请求必须携带用户所见任务的 ID，旧页面的任务 ID 不会停止新任务。只终止本服务创建的任务进程树，不终止既有外部 CDP 浏览器，也不根据历史 PID 猜测并杀进程。
- 服务启动把遗留 running 记录持久化为 interrupted，不自动恢复。若旧执行进程仍持锁，会明确报错，禁止新采集；到采集电脑检查实际进程后处理，不直接删除锁文件绕过操作系统锁。
- 正常关闭受管服务会停止其任务并标记中断。异常退出时，POSIX 任务监督进程仍持执行锁；Windows 使用受控进程树与 Job Object 清理子进程。macOS/Windows 的实际浏览器与开机启动必须在对应平台验收。
- 日志包含 task ID 和持久化单调序号。WebSocket 重连配合每 5 秒 HTTP 补取；日志接口支持 `task_id`、`after`、`before`、`limit`（最多 500）。恢复时最近 500 条用于界面，较早日志可用 `before=当前最小序号` 向前翻页，使用 `after` 则按序补取后续日志。
- 平台登录状态接口展示所选平台在任务中实际执行的已有登录检查及其时间；尚未检查时返回**未知**，登录流程结束但未复查时也返回未知。它不额外发起平台检查请求，不会把“能打开工作台”或历史检查通过当作“当前仍然登录”。需要二维码、验证码、风控或 CDP 确认时，界面提示到采集电脑完成。没有新增二维码图像中转、远程桌面、自动验证码处理或任意浏览器控制接口。
- Cookie 通过受控子进程环境传递，解析后从采集程序环境移除，不进入命令行参数。日志额外遮蔽 Cookie、Bearer/JWT、常见 token 字段与本机调试 WebSocket 地址。不要把账号凭据复制到草稿、文件名或任意文案字段。

## 开机启动与运维

模板不自动安装，先调整绝对路径和运行账号，再在对应电脑安装：

- **Linux**：`deploy/remote/mediacrawler.service.example` 是桌面用户的 systemd user unit，随图形会话启动，不使用 root，不启用后台 linger 来假装具备桌面浏览器。Tunnel 可使用独立 `cloudflared.service.example`。不同发行版的 graphical-session 集成需要本机核对。
- **macOS**：使用项目 `.venv/bin/python deploy/remote/install-macos.py --env-file <私有文件> --output ~/Library/LaunchAgents/com.mediacrawler.workbench.plist` 生成 LaunchAgent。检查后加 `--install` 加载；已有文件不覆盖。它在用户登录后运行，不是系统 LaunchDaemon。Tunnel 按官方方式单独配置用户启动。
- **Windows**：在 PowerShell 运行 `deploy/remote/install-windows.ps1 -Python <项目虚拟环境python.exe> -EnvironmentFile <私有文件>` 预览任务，检查后加 `-Install`。使用当前交互用户、登录时触发，禁止 SYSTEM 或无人登录的 Session 0 运行浏览器。已有同名任务不覆盖。Tunnel 按官方方式部署；不要把隧道 token 写进任务参数或 Git。

本机健康检查：`GET http://127.0.0.1:8080/api/health`。远程健康检查仍必须通过 Access；未授权返回 401/403 是预期保护，不应为监控开放旁路。应用和隧道由服务管理器异常重启，但采集任务不自动重跑。

审计在工作台 SQLite 的 `audit_events`，记录 UTC 时间、签名身份主体、邮箱、方法、无查询参数的路径和结果。写入尝试先记录，再执行业务；审计不可用则停止接纳写入。审计结果为 0 表示尝试已记录但结果尚未写回，例如服务中途崩溃；不能据此认定操作成功。没有额外公开审计管理接口。日志与审计需按本机保留策略备份和归档，当前未提供自动轮转 UI。

## 在线备份与恢复

采集库继续使用 `SQLITE_DB_PATH` / 默认 `database/sqlite_tables.db`；工作台库继续使用 `.runtime/workbench.sqlite3`，可用 `MC_RUNTIME_DIR` 指定其目录。新增任务请求、日志与审计表只在工作台库，采集表和分析快照没有混入任务记录。

对两个数据库分别使用 SQLite backup API，并检查备份完整性，不复制活动数据库/WAL 文件：

```bash
uv run --frozen python -m api.backup --env-file /absolute/private/path/remote.env --destination /another-disk/mediacrawler-backups
```

不存在的源会跳过，不会创建空源；已有目标不会覆盖。两个数据库是分别一致的在线备份，不保证跨库同一瞬间；如需同一业务切面，先停止采集。工具只备份 SQLite，`data/`、本机浏览器配置、私有环境与隧道凭据需要另行安全备份。可通过当前操作系统计划任务定期运行备份命令，建议另一磁盘。

恢复前停止应用、Tunnel 和采集进程，保留故障库副本；检查备份 `PRAGMA integrity_check` 后复制回对应路径，避免旧 WAL 混入。恢复会回退备份后的任务、数据和幂等记录；恢复后的旧请求不应由客户端自动再次提交。结构新增采用 `CREATE TABLE/INDEX IF NOT EXISTS`，原有草稿和任务文档保留；SQLite 分析迁移说明继续见 `ANALYTICS.md`。

## 自动化验证与本机最终验收

云端可运行：

```bash
uv run --frozen pytest tests --ignore=tests/test_media_e2e.py -q
(cd webui && npm exec -- tsc --noEmit)
npm --prefix webui run build
```

新增测试使用临时 SQLite、本地测试进程、生成的 RSA 密钥和模拟 JWKS；没有向真实采集平台发起登录或采集。浏览器验证通过测试专用反向代理携带真实签名测试 JWT，生产代码不存在测试账号或跳过签名的开关。截图在 `/tmp/mediacrawler-remote-proof/`。

必须到真实采集电脑验收：实际域名与证书、Access 未授权拒绝、手机移动网络访问、读写角色、二维码与人工登录、实际采集、关闭页面后继续运行、电脑重启/退出登录行为、备份恢复，以及对应 Windows/macOS/Linux 服务管理器。云端的测试通过不能替代这些实际验收。

本版不实现多执行节点、浏览器扩展、自动发布、GitHub 自动同步、无人值守验证码处理、桌面远控或云端数据库副本。本轮开发不推送远程仓库。

### 本次云端交付验证记录

- Linux / Python 3.11 / Node 22 / 系统 Chromium：全套 `411 passed`，仅原有 SQLAlchemy 弃用警告；未运行真实平台端到端采集。
- 独立 `tsc --noEmit`、生产构建和 `git diff --check` 通过。
- 实际双端口服务启动、远程匿名拒绝、单执行器占用、临时数据库在线备份均通过；桌面 1440px、平板 820px、手机 390px 页面已验证。
- 远程角色、会话失效、关闭页面后任务继续运行、无 WebSocket 日志交付时的轮询恢复已验证；原工作台、分析、草稿和素材回归通过。
- macOS LaunchAgent 生成与 plist 内容校验通过，但没有在 macOS 安装或运行；Windows 任务计划与 Job Object 需要 Windows 本机验收。未建立真实 Cloudflare Tunnel。
- 新工作目录 `/workspace/MediaCrawler-remote`；原 `/workspace/MediaCrawler` 未修改。当前独立预览为环境内 `http://127.0.0.1:8090`，使用临时数据库且没有样例采集数据；真实远程域名尚未配置。

## 没有自有域名的 macOS 临时部署

使用 Cloudflare 提供的 `workers.dev` 入口、Access 与 Mac Quick Tunnel 的步骤见 [macOS 临时部署说明](deploy/remote/MACOS_WORKERS_DEV.md)。不要将 Quick Tunnel 直接接到无鉴权的本机端口。临时 Tunnel 重启可能换地址，必须更新固定 Worker 上游；此路径尚待真实 Cloudflare 账号和 Mac 验收。

## macOS 独立启动程序

工作台和 Tunnel 分别使用独立的英文命名 Start、Stop、Status 程序。先启动工作台，需要公网访问时再启动 Tunnel。Tunnel 失败或停止不会关闭工作台；只有 Tunnel 程序负责更新 Worker 上游地址。安装与使用步骤见 [macOS 独立启动说明](deploy/remote/MACOS_LAUNCHER.md)。仍保留 Access 保护和本机 Wrangler 授权，不自动开始采集。
