# macOS：先用 Cloudflare 提供的地址，后续再绑定自己的域名

> 本文命令及行内文件路径均以项目根目录为基准。

此路径不购买域名：使用 **Access 保护的 workers.dev 地址**作为浏览器入口，Worker 转发到采集 Mac 的 Quick Tunnel。不要直接分享 trycloudflare.com 地址作为无保护工作台入口。

```text
手机/平板浏览器
  → https://mediacrawler-workbench.<你的子域>.workers.dev
  → Cloudflare Access 登录
  → 固定目标 Worker（不存储采集数据）
  → https://<临时名字>.trycloudflare.com
  → Mac 的受保护端口 127.0.0.1:8081
  → 同一工作台 / 本机浏览器 / 本机数据库
```

Quick Tunnel 是临时调试通道，不承诺固定地址或持续可用；重启 Tunnel 后地址可能变化，需要更新 Worker 的 UPSTREAM_ORIGIN。只有 Worker 浏览器入口保持固定。本阶段先手动启动并验收，不把固定隧道的服务模板直接套到 Quick Tunnel。以后配置自有域名和固定 Tunnel 后，再完成长期自动启动。

## 1. 在采集 Mac 准备项目

本文件的命令需要在你的 Mac 终端执行，不能在云端代为安装。不要复制云端 Cookie、浏览器目录或临时测试数据库。

- 先备份本机项目的采集 SQLite、`.runtime/`、`data/` 和浏览器相关配置。
- 下载交付的 Git bundle；已有仓库按 [远程工作台说明](REMOTE_WORKBENCH.md) 导入新分支，不覆盖本机改动。新电脑可使用 `git clone -b feat/remote-workbench <bundle路径> MediaCrawler`。
- 进入本机项目目录。先检查 `git status --short --branch`，保留现有本地修改。
- 需要 Python 3.11、uv、Node 22 和 cloudflared。先用 `command -v uv`、`node --version`、`command -v cloudflared` 检查已有工具；没有工具时按官方说明安装。已有 Homebrew 的 Mac 可按需使用 `brew install uv node@22 cloudflared`。不要重复替换已有可用环境。

在本机项目根目录安装和构建：

```bash
uv sync --frozen
npm --prefix webui ci
npm --prefix webui run build
```

## 2. 创建 Worker 地址（先不连接采集数据）

你需要自己登录 Cloudflare 账号。以下 OAuth 登录应只在 Mac 完成，不发送 Token 到聊天，也不把 CLI 配置目录加入 Git。

```bash
cd deploy/remote/worker
npx wrangler@4 login
npx wrangler@4 deploy
```

部署前检查 `wrangler.toml` 的 Worker 名称：如果账号里已有同名 Worker，不要覆盖，先改为未占用名称。Cloudflare 会返回实际 `https://<worker>.<账号子域>.workers.dev` 地址；后续都使用这个实际地址，不能照抄示例。

代码未配置上游时拒绝访问，不会公开本机数据。此步骤只有 Worker 代码部署，不会启动采集，也不会传输数据库。

## 3. 为整个 Worker 开启 Access

在 Cloudflare 控制台找到这个 Worker 的 Settings → Domains & Routes，为 workers.dev 入口启用 Cloudflare Access。按控制台引导配置团队及 Self-hosted Access application：

- 保护整个 Worker 主机，包括 `/`、`/api/*`、`/assets/*` 和 WebSocket。
- Allow 策略只允许指定邮箱/身份账号，不使用 Everyone、Bypass 或公开路径例外。
- 核对实际允许账号，不能仅依赖创建应用时的默认策略。
- 记录团队名和该 application 的 AUD，用于 Mac 后端验证 JWT。
- 禁用未使用的预览地址；Worker 也会独立校验请求源地址，拒绝其他主机入口。

若当前账号控制台没有 workers.dev 的 Access 保护选项，停止此处，核对 Cloudflare 当前产品支持与账号设置；**不能省略 Access 或改成公开代理**。本方案没有在真实 Cloudflare 账号上开通验收。官方文档：

- https://developers.cloudflare.com/workers/configuration/routing/workers-dev/
- https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/

## 4. 配置 Mac 后端

回到本机项目根目录，将 `deploy/remote/remote.env.example` 复制到仅当前用户可读的私有文件，例如 `~/.config/mediacrawler/remote.env`。创建目录并设置文件权限为 600，在编辑器中填写：

```dotenv
MC_ACCESS_TEAM=你的团队名
MC_ACCESS_AUD=这个Worker对应的Access应用AUD
MC_PUBLIC_ORIGIN=https://实际Worker地址.workers.dev
MC_OPERATORS=你的允许登录邮箱
MC_VIEWERS=
UV_FROZEN=1
```

实际 Worker 地址形如 `mediacrawler-workbench.account-subdomain.workers.dev`，上面是字段说明，不是可直接使用的域名。团队名不含 `.cloudflareaccess.com` 后缀。AUD 是应用标识，不是 JWT 或 Tunnel Token。其他环境路径沿用原有采集电脑配置；如果需要补充 uv/Node 搜索目录，设置 MC_TOOL_PATH。

停止占用 8080 的旧工作台服务，然后从项目根目录启动一个受管进程：

```bash
uv run --frozen python -m api.serve --env-file "$HOME/.config/mediacrawler/remote.env"
```

本机仍访问 `http://127.0.0.1:8080`。远程端口 8081 必须使用 Access JWT，不对外监听，不需要路由器端口映射。

## 5. 启动 Mac 的临时 Tunnel

新开一个终端窗口，Host 参数替换为实际 Worker 主机名（没有 https://）：

```bash
cloudflared tunnel --url http://127.0.0.1:8081 --http-host-header mediacrawler-workbench.account-subdomain.workers.dev
```

保留这个窗口运行，并记录输出中的实际 `https://<随机名字>.trycloudflare.com`。如果 Mac 已有 cloudflared 固定隧道配置，Quick Tunnel 可能受其影响；先保留原配置，不要删除或覆盖既有隧道文件。

## 6. 填写 Worker 上游变量

在 Worker 的 Settings → Variables and Secrets 配置两个普通变量，并部署配置：

- `PUBLIC_ORIGIN`：实际完整 Worker HTTPS 地址，不带额外路径。
- `UPSTREAM_ORIGIN`：上一步 Mac 返回的完整 trycloudflare.com HTTPS 地址，不带额外路径。

这两个变量是地址，不填 JWT、Cookie、私钥或 Tunnel token。Worker 只允许固定的 trycloudflare.com 上游，不接收用户提供的任意代理目标；它转发 Access JWT 和 CSRF，后端仍独立验证签名、角色与来源。Access 浏览器 Cookie 不向 Mac 转发。上传下载使用流转发，不配置 KV、R2、D1 或任何云端数据库。

## 7. 必须完成的跨网络验收

1. 未登录/不在白名单中的浏览器访问 Worker，必须被 Access 拦截；API、下载和 WebSocket 同样受保护。
2. 授权账号通过手机移动网络打开 Worker 地址，确认工作台身份和操作员/只读角色。
3. 直接访问 trycloudflare.com 临时地址时，工作台仍须拒绝没有有效 JWT 的请求。
4. 验证数据分析和导出；平台登录、二维码、验证码仍到 Mac 上操作。
5. 在 Mac 许可的实际任务中验证关闭访问页面后任务继续运行；不要在云端做真实采集。
6. 断开 Mac Tunnel，远程应显示离线错误；重启获得新地址后更新 UPSTREAM_ORIGIN，验证恢复。

只有这些验收完成后，才能称为已完成远程部署。Mac 需开机、联网并保持所需用户会话；睡眠或退出登录会影响工作台。Worker、Access 与 Tunnel 的免费额度和条款以开通时实际账号为准，轮询请求也会占用 Worker 配额。

后续绑定自有域名时，按照 [远程工作台说明](REMOTE_WORKBENCH.md) 改用固定 Tunnel，更新 Access 应用及 MC_PUBLIC_ORIGIN，并重新验证角色、Origin 与 CSRF。没有自动公开工作台、关闭鉴权或绕过平台登录的步骤。

## 本次验证范围

Worker 代理的 4 项 Node 测试通过：无身份/未配置拒绝、固定目标与敏感 Cookie 移除、双斜杠路径不改变目标、WebSocket 响应保留、离线错误和上传大小限制。这是本地代理逻辑测试，不是 Cloudflare 边缘运行或 Mac 实际部署证明。

```bash
node --test deploy/remote/worker/index.test.mjs
```

## 独立启动工作台与 Tunnel

完成本节远程验证后，可安装两组英文命名的程序：**MediaCrawler Start / Stop / Status** 只控制工作台；**MediaCrawler Tunnel Start / Stop / Status** 单独控制 Tunnel 并更新临时上游。工作台不等待 Tunnel 连通，Tunnel 出错不会关闭工作台。安装步骤见 [macOS 独立启动说明](MACOS_LAUNCHER.md)。临时 Tunnel 的地址与可用性限制仍然存在；Access 保护保持启用，不自动开始采集。
