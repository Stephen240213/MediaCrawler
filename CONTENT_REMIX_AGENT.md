# ContentRemixAgent — Phase 1

## What is implemented

The workbench navigation now includes **AI 内容分析 / ContentRemixAgent**. The first release supports:

- Selecting an existing Xiaohongshu record from the collection SQLite database, with search and pagination. Native note text is read when available; normalized imports may contain only a title. No new platform collection is automatically started.
- Optional video analysis when the selected note already has downloaded local media. Otherwise, use text-only analysis or upload the video. The existing one-click ZIP download is a browser export and does not automatically import its contents back into this module.
- MP4, MOV, WebM and M4V uploads, limited to 200 MiB and 15 minutes. Uploads use 4 MiB sequential chunks, so they fit the existing 16 MiB Access/Worker request boundary. Duplicate chunks and requests are checked; interrupted uploads can resume while the same file remains selected. A page refresh requires selecting and uploading again. Whole-file SHA256 is recorded on completion.
- Audio extraction using FFmpeg; timestamped transcription through local whisper.cpp or an OpenAI-compatible cloud transcription endpoint.
- A real LangGraph workflow: media → transcription → evidence → analysis → validation. AsyncSqliteSaver persists node checkpoints. Only one analysis runs at a time; at most five jobs may be queued/running.
- Structured Chinese reports with an evidence-linked summary, opening/hook, structure, topic/audience, expression/emotion, differentiated original ideas, limitations and model/prompt provenance.
- Clickable original evidence, timestamped transcript inspection, authenticated video playback and byte-range seeking, Markdown export, and idempotent saving of an idea outline to existing drafts. Saving again never overwrites a draft edited by the user.
- Persistent job status, cancellation, manual retry and restart recovery. Closing/reloading the browser does not cancel an accepted job. On service restart, queued/running jobs are marked interrupted; they do not restart without a user action.

No sample reports or fake transcriptions ship in production. Unconfigured environments show setup instructions and keep submission disabled. API endpoints also enforce readiness and explicit consent.

## Mac configuration

Keep using the existing Access-protected deployment and **MediaCrawler Start / MediaCrawler Stop**. After pulling this release, install the new locked dependencies and rebuild:

```bash
cd /Users/yiwen/Documents/ChatGPT/MediaCrawler
uv sync --frozen
npm --prefix webui ci
npm --prefix webui run build
```

The lockfile was regenerated with uv; it adds LangGraph/checkpoint dependencies while retaining all previous dependency versions. The package index is now the official `https://pypi.org/simple`, matching the cloud installation override used previously.

Add private configuration to the existing `~/.config/mediacrawler/remote.env`. Preserve all existing Access, audience and operator settings. Never put real keys in Git, a screenshot or chat.

```dotenv
MC_REMIX_API_KEY=YOUR_PROVIDER_KEY
MC_REMIX_BASE_URL=https://api.openai.com/v1
MC_REMIX_MODEL=gpt-4.1-mini
MC_REMIX_ASR=local
MC_REMIX_WHISPER_MODEL=/absolute/path/to/ggml-model.bin
# Optional if whisper-cli is not on the service PATH:
# MC_REMIX_WHISPER_BIN=/absolute/path/to/whisper-cli
```

The analysis adapter calls `/chat/completions` using JSON object output and a Pydantic-generated schema in the prompt. A compatible provider must support this API and the configured model. Keys are only read by the backend. The browser sees the receiving hostname, model and readiness, never the key or full provider URL. HTTPS is required except for explicitly configured loopback services.

For local transcription on macOS:

```bash
brew install ffmpeg whisper-cpp
```

Install a multilingual GGML model compatible with the installed whisper.cpp release (for Chinese, do not choose an English-only `.en` model). Official model instructions: https://github.com/ggml-org/whisper.cpp/tree/master/models . Set its absolute file path in `MC_REMIX_WHISPER_MODEL`. Model downloads are not performed automatically.

For cloud transcription instead:

```dotenv
MC_REMIX_ASR=cloud
MC_REMIX_ASR_MODEL=whisper-1
```

Cloud transcription uses the same configured API base/key, `/audio/transcriptions`, `verbose_json`, and segment timestamps. A provider/model returning only untimed text is rejected. FFmpeg is still needed to extract/compress the audio. This release does not upload full video to the model service.

Restart with **MediaCrawler Stop**, then **MediaCrawler Start**. Refresh **AI 内容分析 → 刷新配置与素材**. Text analysis needs only the analysis service; video analysis additionally needs FFmpeg and the selected transcription backend.

## Evidence and model behavior

Explicit consent is required for every submitted request. The selected title, saved body and chosen transcript are sent to the configured analysis service. In cloud-ASR mode, extracted audio is also sent to the transcription service; in local-ASR mode it stays on the computer. LangSmith tracing is explicitly disabled around graph execution.

Input evidence is bounded to 30,000 characters. Long note bodies are truncated with a report limitation; overlong combined body/transcript input is rejected. Videos are limited to 15 minutes, transcripts to 600 timed segments, and model output to 5,000 tokens. These are processing limits, not a currency budget; prices vary by provider and model.

The model receives source materials as untrusted JSON and has no tools or filesystem access. Reports are parsed and validated against a strict schema. Each finding, summary and creative idea must refer to existing evidence IDs; unsupported IDs, missing references, duplicate/incomplete dimensions and malformed outputs are rejected. The backend adds missing-body, truncation, voice-recognition and missing-visual/comment limitations.

This validates the existence of citations and output structure; it is not a proof that every interpretation follows from its citation. Users should inspect original evidence. Single-item analysis cannot establish viral causality or viral probability. Suggested outlines are AI proposals, not verified personal experiences or finished publishable text.

For identical evidence, endpoint, model and prompt version, a previously validated analysis result is reused; the new job records a cache hit and zero new analysis-call tokens. Completed graph stages/artifacts are reused on retry. A provider request interrupted before its result was saved may still be billed; retry may repeat that call. Transcriptions are cached within each job, not globally across independent uploads. Reports record the selected ASR provider/model and transcript hash.

## Storage and execution

All analysis storage is under the configured runtime directory:

```text
<MC_RUNTIME_DIR>/content-remix/
  analysis.sqlite3       jobs, upload progress, validated report cache
  checkpoints.sqlite3    LangGraph node checkpoints
  uploads/<id>/          uploaded source videos
  jobs/<id>/             video snapshot, audio, transcript and metadata
```

Collection data/snapshots remain in the collection database. Drafts and collection task history remain in the existing workbench database. Analysis reads do not migrate or update collection records. No browser extension, publishing endpoint, arbitrary command runner or GitHub synchronization is added.

Analysis tasks run on the existing backend's event loop, with separate concurrency from browser collection. FFmpeg and whisper-cli are owned child processes with fixed argument lists, timeouts, and cancellation cleanup. Child stderr and raw model error bodies are not exposed. Container demuxers and input protocols are restricted so uploaded playlists cannot cause FFmpeg to fetch arbitrary network content. Uploaded filenames do not control filesystem paths.

Cancellation stops the active analysis/model await and owned media subprocess, preserving uploaded/generated files for retry. A short local video copy already running in a background thread may finish after cancellation; it cannot start another model call. Retries may change the configured provider only for a stage that has not already completed. Existing report/provenance remains unchanged.

## API and access

All routes live under `/api/remix` on the existing same-origin API:

- `GET /config`, `/sources`, `/jobs`, `/jobs/{id}`
- `POST /uploads`; `GET /uploads/{id}`; `PUT /uploads/{id}?offset=...`
- `POST /jobs`, `/jobs/{id}/cancel`, `/jobs/{id}/retry`, `/jobs/{id}/draft`
- `GET /jobs/{id}/video`, `/jobs/{id}/report.md`

Remote writes inherit operator roles, Access JWT, Origin/CSRF protection and operation audit logging. Viewers can inspect/download results but cannot upload, submit, retry, cancel or save drafts. This follows the existing shared-workbench model; it is not multi-tenant per-user data isolation.

Lists are paginated. Video GET supports a single HTTP byte range; malformed/multiple/out-of-bounds ranges are rejected. Job/video IDs are validated; endpoints do not accept arbitrary server paths. Uploads enforce declared size, type suffix, chunk offsets, duplicate content checks, available disk space and at most ten incomplete uploads. Uploaded bytes are subsequently validated by the restricted media parser. Large files and incomplete uploads remain local until manually cleaned; automatic deletion is not implemented.

## Backup and upgrade

No collection/workbench schema migration is required. New analysis tables are created additively in the new directory. Existing Mac runtime files are never fetched through Git.

For a consistent backup, stop the workbench first. Use SQLite's online backup mechanism for **both** analysis and checkpoint databases, rather than copying a live database/WAL. For example, after setting an absolute backup destination:

```bash
sqlite3 /absolute/runtime/content-remix/analysis.sqlite3 ".backup '/absolute/backup/analysis.sqlite3'"
sqlite3 /absolute/runtime/content-remix/checkpoints.sqlite3 ".backup '/absolute/backup/checkpoints.sqlite3'"
```

Copy `uploads/` and `jobs/` into that same backup while the service is stopped. Keep them together with both database backups; checkpoints may reference artifact paths. Restore to the same configured runtime directory with the service stopped, then restart and manually retry interrupted jobs. The backup contains creator content and should be private. Back up the existing collection/workbench stores using their existing procedures separately.

## Validation and remaining local acceptance

Automated tests use temporary collection, workbench and analysis databases, generated media, and test-only model/ASR adapters. They cover actual LangGraph persistence, retries without repeating ASR, cancellation, restart recovery, report caching, invalid citations/schema/timestamps, upload bounds/replay, byte ranges, drafts, remote roles/CSRF/audit, actual FFmpeg processing and playlist rejection. Chromium tests cover submission, page reload, persistent history, reports/citations, Markdown download, draft saving, video upload/transcription controls, missing configuration, desktop/tablet/mobile layout and original navigation.

Cloud verification does not prove real-model output quality, Mac whisper.cpp performance, provider billing behavior or cross-network large uploads. Those require the actual configured Mac/provider. The first release excludes link-triggered collection, other platforms, cover/keyframe analysis, comments, batch comparison, trained virality prediction, transcript editing and automatic publishing. The phase-one architecture keeps these as future extensions rather than claiming unsupported results.
