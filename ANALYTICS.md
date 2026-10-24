# SQLite 数据分析

## 启动与使用

在项目根目录安装依赖、构建并启动本机服务：

```bash
uv sync --frozen
npm --prefix webui ci
npm --prefix webui run build
uv run --frozen uvicorn api.main:app --host 127.0.0.1 --port 8080
```

访问 `http://127.0.0.1:8080`，从左侧导航进入「数据分析」。首次打开点击「备份并初始化」，也可以在使用新版采集器首次保存到 SQLite 时自动初始化。不会自动开始采集。API 没有用户认证，仅供本机使用，不要开放到公网。

本次 Codex Cloud 环境已提供 `/workspace/.mediacrawler-env.sh`。在此环境先执行 `source /workspace/.mediacrawler-env.sh`，使用已安装的 Node 22、Python 3.11 和系统 Chromium。npm 镜像被限制时可用：

```bash
npm --prefix webui ci --registry=https://registry.npmjs.org --replace-registry-host=always
```

工作台和后端默认保存格式均为 SQLite；其他格式仍可选择。浏览器登录、执行、草稿预览与素材管理沿用现有流程。SQLite 采集结果直接进入分析，不需要再导入。

### 数据位置与范围

- 复用 `config.db_config.sqlite_db_config['db_path']` 指定的采集库，默认 `database/sqlite_tables.db`；可通过 `SQLITE_DB_PATH` 环境变量选择另一采集库（启动前设置绝对路径）。
- `.runtime/workbench.sqlite3` 仍只保存草稿、任务和素材元数据，不参与分析。
- 内容按平台 + 内容 ID 去重，评论按平台 + 评论 ID 去重。保留原始平台表，不删除其重复行，分析表取采集时间较新的一行；同时间按原库 rowid 顺序确定。
- 支持当前 ORM 的小红书、抖音、快手、B 站视频与动态、微博、贴吧、知乎内容和评论。分析保留匿名创作者哈希，不读取原始用户 ID。
- 当前云端没有真实采集数据。产品初始展示空状态；测试数据只存在于临时测试库中，不随产品初始化写入。

### 看板与口径

平台、精确关键词、起止日期可组合筛选；日期按北京时间，包含结束日期全天。可以选择以「内容发布时间」或「最近采集时间」筛选内容集合，所有面板使用同一内容集合。点击分布、作者排行、趋势点或日期条可查看对应明细；评论趋势点击会增加独立的评论日期筛选，高频词点击会筛选包含该词的评论。

- 内容数为规范化后的内容条数；作者数优先按匿名哈希去重，哈希缺失时以平台 + 昵称分组，同名作者可能合并；作者字段缺失不计数。
- 评论数为已采集且能关联当前内容的去重原始评论数。它与平台返回的内容评论指标不同，孤立评论会保留，但在导入对应内容前不进入看板。
- 发布时间趋势统计内容原始发布日。最近采集趋势统计每条内容的最近采集日，**不是每次运行的任务数或历次采集事件总量**。
- 互动总量是已知的点赞、收藏、评论、分享之和，同时展示有效项数。缺失指标显示 `—`；全缺失不生成总量。部分指标缺失的内容不适合直接横向比较。
- 日期空值不会被补成当前时间。字符串数值支持逗号、千/万/亿和 k/w/m，无法解析、负数与非整数计数为空。时间支持秒、毫秒、微秒时间戳、ISO 时间、YYYY-MM-DD 及 YYYYMMDD；没有时区的日期按北京时间解释。原始表已写入的默认 0 无法反推为缺失。
- 分布展示前 30 项，作者排行按内容数展示前 20 名；明细每页 20 条，后端每页上限 100。原始链接仅允许 http(s)，缺失时明确显示。
- 高频词使用最近最多 5000 个不同评论文本，每文本最多 2000 字，jieba 分词、基础停用词过滤，按包含该词的评论数计数。这是有界样本，页面明确标注；不提供情感分析、全量词频或语义聚类。
- 页面提供各指标有效记录数、源数据采集更新时间与查询时间；源数据没有时间时显示未提供，不用查询时间冒充数据更新时间。

### 导入与导出

在看板底部选择文件、平台和记录类型，先「校验并预览结果」，再「确认导入有效记录」。校验不会写入；确认时再次校验，错误行跳过，有效行在一个事务中写入。结果显示新增、已有内容的新观察、重复、拒绝、警告数量及前 20 条行级提示。

- 支持 `.xlsx` 首个工作表（首行为字段名）、UTF-8 CSV、JSON 数组或单对象、JSONL。JSON 也接受 `data`、`contents`、`comments` 包装数组。
- 单次最多 10 MB / 50000 行；旧 `.xls`、非 UTF-8 CSV、多工作表合并与自定义字段映射暂不支持，请先另存为支持的格式。Excel 长数字 ID 如已被电子表格舍入，无法恢复，建议以文本保存。
- 必须有平台和内容 ID；可以在界面统一指定平台。评论还必须有评论 ID，正文为 body/content/comment_content。
- 支持规范字段 `platform, content_id, author, keyword, published_at, collected_at, likes, favorites, comments, shares, content_type, title, url`，以及原项目的 note_id/aweme_id/video_id、nickname、source_keyword、time/create_time、last_modify_ts/add_ts、liked_count 等别名和常用中文字段。完整映射见 `database/analytics.py` 的 ALIASES。
- 平台值为 `xhs, dy, ks, bili, wb, tieba, zhihu`。不从任意 id 字段猜测内容 ID，以免导入错表。
- 缺失字段保留为空，无法解析的可选字段给出警告。同平台、内容 ID、采集时间已有不同指标的记录拒绝导入，避免改写历史；完全重复记录跳过。缺少采集时间的记录可以用于当前分析，但不生成快照，也不能覆盖已有带采集时间的内容。
- 导入数据只写采集库的分析表，不会回写平台原始表，也不会加入草稿或任务记录。
- 顶部导出按钮导出当前平台、关键词、内容日期和作者/类型定位筛选下的内容，不包含评论文本/评论日期筛选。支持 CSV 和 Excel，单次最多 50000 条，超限需缩小范围。空值留空，防止电子表格把外部文本当公式执行。

## 兼容迁移与备份

新增 `analytics_meta`、`analytics_content`、`analytics_comment`、`analytics_snapshot` 表及索引；原平台表结构和数据不删除、不重建。GET 查询使用 SQLite `mode=ro` 与 `PRAGMA query_only=ON`，不会隐式初始化。初始化使用明确的 POST `/api/analytics/initialize`，内容导入使用独立 POST `/api/analytics/import`。

首次迁移现存非空库时，使用 SQLite 在线 backup API 在库旁生成 `sqlite_tables.db.analytics-<UTC时间>.bak`；备份在任何分析结构写入之前完成。备份失败则不迁移；结构、回填和触发器安装位于同一事务中，失败回滚。再次初始化幂等，已有版本不会重复回填。新增平台表后再次初始化会安装其触发器；版本 1 尚不支持外部绕过触发器写入后的自动全量重建。

正式本机使用前建议再独立备份一次。先停止采集与本机 API，然后在项目根目录运行（可选自定义路径）：

```bash
uv run --frozen python - <<'PY'
import sqlite3
from datetime import datetime
from config.db_config import sqlite_db_config
path = sqlite_db_config['db_path']
backup = path + '.manual-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.bak'
with sqlite3.connect(path) as source, sqlite3.connect(backup) as target:
    source.backup(target)
print(backup)
PY
```

恢复时停止所有连接，保留故障库及迁移后新增数据的副本，再将经验证的 `.bak` 复制到配置的库路径。不要在活动 WAL 事务期间直接替换数据库文件。恢复完整旧库会丢失备份时间之后的写入，应先导出需要保留的新数据。仅回退 Git 代码不会撤销数据库触发器；要使用完全旧版写入程序，应恢复对应旧备份，或先备份后由熟悉 SQLite 的维护者移除 `analytics_*` 触发器（保留表）。

### 快照如何保存

原平台表的 INSERT/UPDATE 触发器在采集事务内同步规范化数据和快照，采集回滚时快照一起回滚。新版 SQLAlchemy SQLite 连接注册 `mc_number`、`mc_time`、`mc_author` 清洗函数；外部 Python 写入同一原始库时也必须先调用 `database.analytics.register_functions(connection)`，否则触发器会阻止未注册函数的写入。外部只读 SQLite 工具不受影响。

快照主键为平台 + 内容 ID + 采集时间；冲突不会覆盖历史。每个指标分别至少有两个不同时间的非空观测才计算绝对增长，增长率基数必须大于 0；否则显示「暂无可比较历史」或「基数为 0，无增长率」。增长可以为负数，不人为归零。原采集器时间通常为毫秒（也兼容秒时间戳），同一时间戳的重复更新不构成两次历史观测，保留首个快照。旧记录回填只代表已经保存下来的观测，无法恢复过去已被覆盖的数值。

## 验证

```bash
# 不访问真实采集平台，全部使用临时数据库/本地 fixture
uv run --frozen pytest tests/test_analytics.py tests/test_analytics_browser.py -q
uv run --frozen pytest tests --ignore=tests/test_media_e2e.py -q
(cd webui && npm exec -- tsc --noEmit)
npm --prefix webui run build
```

TypeScript 单独检查应从 `webui` 目录执行 `npx tsc --noEmit`（或使用 `npm --prefix webui run build` 中的 `tsc -b`）。浏览器测试需要已安装 Playwright Chromium；可通过 `MEDIACRAWLER_TEST_BROWSER` 指定可执行文件。本次云端使用 `/usr/bin/chromium`。浏览器测试启动真实本机 API，验证初始化/空状态、文件校验导入、图表筛选、互动历史、CSV 下载、原始评论、窄屏布局、错误重试和旧导航，并将截图写到 `/tmp/mediacrawler-analytics-proof/`。不运行真实平台采集或登录测试。

版本在独立分支 `feat/sqlite-analytics` 分步提交，不强制覆盖历史，不推送远程。不包含浏览器扩展、自动发布、GitHub 自动同步、跨设备同步或远程数据库访问。

### 本次验证记录

- Python 3.11、Node 22、Linux 系统 Chromium；TypeScript `tsc --noEmit` 与前端生产构建通过。
- `pytest tests --ignore=tests/test_media_e2e.py -q`：390 passed（含 47 项分析后端测试、1 项实际 Chromium 浏览器流程），仅现有 SQLAlchemy 弃用警告。
- 浏览器覆盖桌面 1440px 与窄屏 390px，确认无页面级横向溢出，无 JavaScript 页面异常。
- 数据库测试全部使用临时文件；没有修改已有真实采集数据，没有运行真实平台采集。默认采集源当前为空，正式页面不填充测试数据。
