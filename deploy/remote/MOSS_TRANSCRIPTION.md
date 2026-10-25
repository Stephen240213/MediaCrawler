# 本机 MOSS 转写

在 Apple Silicon Mac 上，ContentRemixAgent 可以复用 BaoCut 的原生 `model-worker`、MOSS 模型和 Qwen3 时间戳对齐器，使用 MLX／Metal 完成离线语音转写。无需安装 Python 推理框架或 whisper.cpp；适配器不会自动下载权重。BaoCut 更新后如发生协议不兼容，需要重新验证。

## 配置

先安装 FFmpeg（包含 ffprobe），确保桌面启动器的 PATH 可找到两个命令：

```sh
brew install ffmpeg
```

在本机私有 `remote.env` 中设置：

```dotenv
MC_REMIX_ASR=moss
```

默认 worker 为 `/Applications/BaoCut.app/Contents/Resources/bin/model-worker`，模型根目录为运行后端的用户的 `~/.baocut/models`。如位置不同，可显式配置绝对路径：

```dotenv
MC_REMIX_MOSS_WORKER=/Applications/BaoCut.app/Contents/Resources/bin/model-worker
MC_REMIX_MOSS_MODELS=/absolute/path/to/models
```

需要已由 BaoCut 下载的两组模型及其 `.bcut-manifest.json` 清单：

- `OpenMOSS-Team/MOSS-Transcribe-Diarize`
- `aufklarer/Qwen3-ForcedAligner-0.6B-4bit`

配置完成后重启工作台后端，点击页面“刷新配置与素材”。“转写已配置”表示 worker 可执行且模型文件与清单的大小匹配；实际推理时还会检查 worker 协议版本、MLX／Metal 后端以及输出摘要。沙箱可能屏蔽 Metal，真实本机运行应通过桌面启动器进行。

语音留在本机。生成完整分析报告仍需单独配置 `MC_REMIX_API_KEY`、`MC_REMIX_BASE_URL`、`MC_REMIX_MODEL`；只有用户在页面同意并提交分析后，文字证据才会发送到配置的分析服务。不要将密钥、权重、音频、运行目录或真实用户数据提交到 Git。

## 行为与限制

- 保留 `MC_REMIX_ASR=local`（whisper.cpp）和 `MC_REMIX_ASR=cloud` 的已有行为。
- 每次转写运行独立 worker，结束、失败、取消或超时后回收进程。任务输出只从此次作业的临时目录读取，并核对大小及 SHA-256。
- 使用对齐后的字词时间戳分句，转换成秒数，供报告引用和跳转视频。当前不执行说话人区分。
- 视频仍限制为 15 分钟、200 MB；长音频速度和识别质量应另行验收，短片结果不能代表长视频表现。

## 验证

协议、超时、取消、路径限制与输出校验使用本地假 worker 测试：

```sh
uv run --frozen pytest tests/test_remix_moss.py tests/test_content_remix.py -q
```

浏览器测试使用 fixture 服务，不触发真实账号登录、采集或模型调用。运行仓库的 `scripts/cloud-check.sh`，并按需运行 `tests/test_remix_browser.py`。实际使用的环境和浏览器需在验证记录中注明；本机结果不代表 Cloud provisioning 已通过。
