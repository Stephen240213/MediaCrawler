# Workbench updates

## Collection and task records

New collection forms default to Xiaohongshu (`xhs`); selecting another platform still works. Existing browser execution and SQLite defaults remain available.

Task history offers Delete with confirmation. Only terminal records can be deleted. The operation atomically deletes the task document and its logs in the workbench database. It preserves collection data, snapshots, drafts, assets, audit events and request-key tombstones, so retrying a previously submitted request cannot silently restart collection. Running tasks must be stopped first. There is no bulk delete.

## Note download

The content detail row offers one-click download to an operator. The backend creates a ZIP with a folder named after the note title, containing Markdown, `media/` images/videos, and `Download Report.txt`. Unsafe filename characters are replaced; titles are capped at 70 characters. The endpoint is POST and inherits operator authorization, origin/CSRF checks and audit logging.

It reads the existing collection database without migrations, recovers the full body from a known native platform table when present, and includes media already saved under `data/<platform>/media/<content_id>`. Missing media with stored supported CDN URLs are fetched on the collection computer. Redirects and DNS answers are validated; connections pin a public IP with the original TLS server name. No platform cookies or proxy credentials are forwarded. If a local VPN maps CDN DNS to RFC 2544 fake IPs (198.18.0.0/15), the downloader resolves the allowlisted hostname through a fixed Cloudflare HTTPS DNS endpoint, validates that answers are public and connects to a pinned public address. Other private DNS answers remain blocked. This DNS fallback sends only the CDN hostname, never a signed media URL or credentials. Small or absent source fields are reported rather than synthesized. XHS video URLs are alternatives for one video; up to eight saved URLs are tried in order for an absent local video, and exactly one successful video is included. Requests retain their saved HTTP/HTTPS scheme and include the standard browser User-Agent and platform Referer. Ambiguous binary Content-Type responses require a recognized media signature. HTML/error bodies, empty responses and incomplete transfers are rejected.

Limits: 80 media files, 100 MiB uncompressed media, a 75-second remote transfer window (individual network operations time out within 15 seconds). Temporary ZIPs are removed after the response. A failed, expired, unsupported or unavailable media URL produces a report entry with the HTTP status, DNS/network issue, size limit or type error, not a fake media file. The page reports included media/video counts and warning summaries, so a ZIP without a video is clearly identified. Records imported with only normalized analysis fields may only contain a title and metrics, without full body/media; downloading cannot recover data never collected. Non-XHS native media schemas and DASH-only streams may need existing local downloads. Very large media are omitted. Remote CDN requests are tested with mocks; real platform downloads require local acceptance testing.

## Comment analysis

Content details no longer show collection time or aggregate interaction-total columns. Author ranking is removed. Collection-time filtering/trends and individual interaction counts remain available.

The analysis follows the current content filters. It uses the most recently observed 5,000 distinct nonblank comment texts, weighted by the number of distinct comment records sharing that text, and caps each text at 2,000 characters. The UI discloses total comments, sampled comments, distinct texts and truncated comments.

- Chinese segmentation and stopword/emoji removal produce document-frequency terms. Each comment contributes at most once to a word. The clickable word cloud and frequency chart share this source.
- Explicit multi-label rules identify price/budget, location/transport, booking/purchase, experience/service and guidance/suitability. Unmatched texts are reported separately. Themes overlap, so their counts are not additive.
- Question markers yield a question-intent count and sample percentage.
- Lexical emotional cues are grouped as positive, negative, mixed or unrecognized. Explicit negative phrases are removed before looking for positive phrases. This is not a trained sentiment model, cannot reliably interpret irony/complex negation, and does not estimate satisfaction.
- Click a term or category to page through all matching original comments under the current content filters. Drill-down covers the full filtered comment population; its count may exceed the bounded analysis sample. Clear controls restore the records view.

The UI exposes the coding lexicons. No comments are sent to third-party AI services. No topic or sentiment inference is performed on fabricated data.

References consulted on 2026-10-25:

- Jieba Chinese segmentation and keyword extraction: https://github.com/fxsjy/jieba (README fetched from raw.githubusercontent.com).
- scikit-learn topic extraction reference: https://scikit-learn.org/stable/auto_examples/applications/plot_topics_extraction_with_nmf_lda.html (source example fetched from the scikit-learn GitHub repository).

These references inform segmentation, document-level counts and the separation of term exploration from semantic topics. This version uses transparent rule coding; it does not claim to run NMF/LDA. For a future learned classifier, first label a representative corpus, evaluate per-class precision/recall and confusion, and preserve reviewable original comments.

## Validation and deployment

All database/media tests use temporary directories. Browser tests cover desktop (1440 px), tablet (768 px), mobile (390 px), filters, word/category drill-down, real ZIP download, deletion confirmation/cancellation, absent columns and retained navigation. CDN tests mock requests and verify URL/DNS/redirect boundaries. No real platform collection is started in cloud development.

No database schema change or data migration is needed. Existing data and deployment settings stay on the Mac. Pull `feat/remote-workbench`, rebuild `webui` with `npm ci && npm run build`, then restart via MediaCrawler Stop / MediaCrawler Start. No launcher reinstall or new Tunnel/Access setup is required for these changes.
