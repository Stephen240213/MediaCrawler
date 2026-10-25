# macOS 工作台与 Tunnel 独立启动说明

工作台和公网隧道分开管理，各有独立的启动、停止、状态程序。所有程序名称使用英文。

| 程序 | 用途 |
| --- | --- |
| MediaCrawler Start | 启动本机工作台并打开本机页面，不连接 Tunnel、不部署 Worker |
| MediaCrawler Stop | 仅停止工作台，保留数据，不停止 Tunnel |
| MediaCrawler Status | 查看当前工作台状态 |
| MediaCrawler Tunnel Start | 连接 Tunnel、检查受保护入口并更新 Worker 上游地址 |
| MediaCrawler Tunnel Stop | 仅停止 Tunnel，不影响工作台和正在运行的本机任务 |
| MediaCrawler Tunnel Status | 查看当前 Tunnel 状态 |

## 更新与安装

先完成现有 Python、Node、cloudflared 和私有 remote.env 配置。更新时需要停止旧版管理的服务，以便替换 LaunchAgent。下面命令不删除数据库、草稿、素材或密钥：

```bash
cd /Users/yiwen/Documents/ChatGPT/MediaCrawler
git pull --ff-only
uv sync --frozen
uv run --frozen python -m api.desktop stop
uv run --frozen python -m api.desktop tunnel-stop
uv run --frozen python -m api.desktop install
```

首次升级到拆分版时，旧的 MediaCrawler Stop 会停止旧版共同管理的工作台和 Tunnel。新版安装完成后，两者才独立管理。

安装器会打开 `~/Applications/MediaCrawler`。请从这个新打开的目录运行程序；旧 Dock 图标可能仍指向备份中的旧版程序，需要移除后重新添加。

安装器会先编译、签名并验证全部程序，再替换已确认由本安装器创建的文件。旧版程序、配置和 LaunchAgent 会备份到应用目录中的 `.backups/`。同名的第三方文件或服务不会覆盖。

如果使用自定义端口或私有配置路径，请继续在安装时显式传入原来的值，例如：

```bash
uv run --frozen python -m api.desktop install \
  --env-file /absolute/private/path/remote.env \
  --local-port 8090 --remote-port 8081
```

## 日常使用

只在 Mac 本机使用时，打开 **MediaCrawler Start** 即可。本机默认地址：

```text
http://127.0.0.1:8090/
```

需要让手机、平板或其他电脑访问时，再打开 **MediaCrawler Tunnel Start**。启动 Tunnel 前必须先有工作台的受保护本机接口；Tunnel 程序不会代替你启动或停止工作台。

公网地址继续使用原来的 workers.dev 地址，访问仍需通过 Cloudflare Access 登录。Tunnel 启动时会等待临时地址和已注册连接，再验证匿名请求返回 HTTP 401，最后更新 Worker 的上游地址。未通过保护检查时不会更新 Worker，也不会关闭工作台。

重复点击各自的 Start 会复用该组件已有的服务，不会强制重启。关闭网页不会停止已运行的后台服务。Mac 仍需开机、保持联网和登录桌面用户；本方案没有设置登录后自动启动或自动采集。

不再需要远程访问时，打开 **MediaCrawler Tunnel Stop**，本机工作台和采集任务继续运行。停止工作台后，即使 Tunnel 进程仍在运行，公网也无法使用工作台；重新启动工作台后隧道可重新转发。若要全部关闭，分别运行两个 Stop。

## 独立进程、状态与日志

两个组件分别使用以下用户级 LaunchAgent：

```text
com.mediacrawler.desktop    工作台
com.mediacrawler.tunnel     Tunnel
```

它们具有独立的进程锁、状态文件和日志：

```text
<MC_RUNTIME_DIR 或项目/.runtime>/desktop/
  settings.json             本机路径、端口等启动配置，不含密钥值
  status.json               工作台状态
  tunnel-status.json        Tunnel 状态
  desktop.log               工作台监督日志
  tunnel.log                Tunnel 监督日志
  agent.stdout.log          工作台 LaunchAgent 标准输出
  agent.stderr.log          工作台 LaunchAgent 错误输出
  tunnel-agent.stdout.log   Tunnel LaunchAgent 标准输出
  tunnel-agent.stderr.log   Tunnel LaunchAgent 错误输出
```

监督日志达到 2 MB 时轮转，保留两份备份；输出经过凭据脱敏。每个组件只停止自己创建的子进程，不按进程名称批量终止 Python、Chrome 或 cloudflared，也不会因另一个组件出错而停止自己的服务。

工作台停止时，会通过已有清理机制停止它管理的采集和分析任务。已经存在的外部 Chrome 浏览器不会被批量关闭。不要通过删除执行锁来强行启动第二个采集进程。

## Tunnel 启动失败

类似 `Tunnel origin is not reachable or is not protected` 的旧错误表示公网隧道检测失败，不代表 Python 无法运行。新版将错误保存在独立的 `tunnel.log` 中，并记录检测到的 HTTP 状态或网络不可用情况。

工作台已经与这一检测分离：Tunnel 出错时仍可通过本机地址继续使用工作台。只需排查或重试 **MediaCrawler Tunnel Start**。

如果 Wrangler 授权失效，在 Mac 终端重新登录：

```bash
cd /Users/yiwen/Documents/ChatGPT/MediaCrawler/deploy/remote/worker
npx wrangler@4.152.0 login
```

然后重新运行 **MediaCrawler Tunnel Start**。隧道仍使用固定版本 `wrangler@4.152.0` 更新当前检出的 Worker，带有 `--keep-vars`，不会删除 Access 策略或其他 Worker 变量。临时 Tunnel 地址可能变化，不具有固定隧道的稳定性保证。

## 弹窗与诊断

错误弹窗可通过 Close 或 Escape 关闭，45 秒后自动关闭。Open Log 打开该程序的错误日志；Diagnostics 仅在手动点击后打开终端执行诊断，不会自动启动采集。

各程序的完整控制输出保存在 `~/Library/Logs/MediaCrawler/`。状态窗口只展示有限长度的文本，避免 Python 错误内容过长导致按钮无法操作。

```bash
cd /Users/yiwen/Documents/ChatGPT/MediaCrawler
uv run --frozen python -m api.desktop diagnose
```

诊断分别显示当前工作台和 Tunnel 状态，并给历史日志标注文件时间。某次 Status 日志中的 `ready` 不能证明当前仍在运行。

终端能读取 Python 不代表 Finder 或 LaunchAgent 已有 Documents 目录权限。如果 macOS 提示访问项目所在文件夹，请按实际使用需求授权；不要仅凭弹窗就删除虚拟环境或移动已有数据。

## 验证与限制

云端测试使用临时文件、模拟应用编译、受控测试进程及真实本机 API，验证两组程序和 LaunchAgent、停止范围、重复启动、状态隔离、Tunnel 失败不停止工作台，以及本机工作台无需外网检测即可就绪。

云端不会注册 Mac LaunchAgent、连接真实 Tunnel、变更 Cloudflare 账号或启动真实采集。原生应用编译、Finder 权限、Mac 网络和真实公网访问仍需在采集 Mac 上验收。
