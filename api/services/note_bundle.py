"""Per-note ZIP export; bounded local files and public CDN-only media retrieval."""
import ipaddress
import json
import socket
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.parse import urlsplit, urljoin

import httpx
from fastapi import HTTPException

from database.analytics import SOURCES
from media_downloader.paths import build_media_dir, sanitize_component, guess_extension
from . import analytics

MAX_TOTAL = 100 * 1024 * 1024
MAX_FILES = 80
CDNS = ('xhscdn.com', 'xiaohongshu.com', 'douyinvod.com', 'douyinpic.com', 'byteimg.com',
        'hdslb.com', 'bilivideo.com', 'sinaimg.cn', 'weibocdn.com', 'kwimgs.com', 'kwaicdn.com', 'zhimg.com', 'bdimg.com', 'baidu.com')


def fetch_media(url, budget, deadline):
    """Pin each validated public DNS address, including redirects; no proxy/cookies."""
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        for _ in range(4):
            parts = urlsplit(url)
            host = (parts.hostname or '').lower()
            if parts.scheme not in ('http', 'https') or parts.username or parts.password or parts.port not in (None, 80, 443):
                raise ValueError('不支持的媒体地址')
            if not any(host == domain or host.endswith('.' + domain) for domain in CDNS):
                raise ValueError('媒体地址不在支持的 CDN 范围')
            addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            ips = [address[4][0] for address in addresses]
            if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
                raise ValueError('媒体地址必须为公网地址')
            pinned = httpx.URL(url).copy_with(scheme='https', host=ips[0], port=None)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError('达到下载时间上限')
            with client.stream('GET', pinned, headers={'Host':host}, extensions={'sni_hostname':host}, timeout=min(15, remaining)) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get('location', ''))
                    continue
                response.raise_for_status()
                mime = response.headers.get('content-type', '').split(';')[0]
                if not mime.startswith(('image/', 'video/')):
                    raise ValueError('返回的文件不是图片或视频')
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    if len(chunks) + len(chunk) > budget or time.monotonic() > deadline:
                        raise ValueError('达到文件大小或下载时间上限')
                    chunks.extend(chunk)
                return bytes(chunks), guess_extension(url=url, content_type=mime, default='.bin')
        raise ValueError('媒体重定向次数过多')


def urls(value):
    if not value:
        return []
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            decoded = value
        if isinstance(decoded, str):
            return [v.strip() for v in decoded.split(',') if v.strip()]
        value = decoded
    if isinstance(value, list):
        return [v if isinstance(v,str) else v.get('url_default') or v.get('url','') for v in value if isinstance(v,(str,dict))]
    return []


def build(platform, content_id, data_root):
    with analytics.read_db() as (db, _):
        row = db.execute('SELECT * FROM analytics_content WHERE platform=? AND content_id=?', (platform, content_id)).fetchone()
        if row is None:
            raise HTTPException(404, '内容不存在')
        note = dict(row)
        native = {}
        for table, (source_platform, key, _) in SOURCES.items():
            if source_platform != platform:
                continue
            if db.execute('SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?', (table,)).fetchone():
                raw = db.execute(f'SELECT * FROM "{table}" WHERE "{key}"=? LIMIT 1', (content_id,)).fetchone()
                if raw:
                    native = dict(raw)
                    break
    title = sanitize_component(note.get('title') or content_id, fallback='Note')[:70]
    title = title.strip('. ') or 'Note'
    warnings = []
    text = native.get('desc') or native.get('content') or native.get('content_text') or native.get('text')
    if not text:
        text = note.get('title') or ''
        warnings.append('源数据没有独立正文，仅导出已保存的标题。')
    images = list(dict.fromkeys(urls(native.get('image_list'))))
    videos = list(dict.fromkeys(urls(native.get('video_url'))))
    # XHS video_url stores alternative encodings of one video, not multiple videos.
    candidates = [(f'{i:03d}', url) for i, url in enumerate(images, 1)]
    if platform == 'xhs' and (videos or native.get('type') == 'video'):
        candidates = [('cover', images[0])] if images else []
        candidates += [('video', videos[0])] if videos else []
    else:
        candidates += [(f'video-{i:03d}', url) for i,url in enumerate(videos,1)]
    media_urls = [url for _,url in candidates]
    root = Path(data_root).resolve()
    directory = build_media_dir(root, platform, content_id)
    files = []
    if directory.is_dir() and directory.resolve().is_relative_to(root):
        for p in sorted(directory.iterdir()):
            if p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(root) and p.suffix.lower() in {'.jpg','.jpeg','.png','.webp','.gif','.mp4','.mov','.webm','.avif'}:
                files.append(p)
    temporary = tempfile.NamedTemporaryFile(prefix='mediacrawler-note-', suffix='.zip', delete=False)
    path = Path(temporary.name)
    temporary.close()
    try:
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            total = 0
            count = 0
            for p in files[:MAX_FILES]:
                if total + p.stat().st_size > MAX_TOTAL:
                    warnings.append('本机素材超过 100 MB 上限，部分文件未打包。')
                    continue
                # Bound the actual read too if a collector is still writing.
                with p.open('rb') as source:
                    body = source.read(MAX_TOTAL - total + 1)
                if len(body) > MAX_TOTAL-total:
                    warnings.append('文件仍在写入或超过上限，已跳过。')
                    continue
                archive.writestr(f'{title}/media/{p.name}', body)
                total += len(body)
                count += 1
            deadline = time.monotonic() + 75
            local_stems = {p.stem for p in files}
            for i, (stem, url) in enumerate(candidates[:MAX_FILES]):
                if stem in local_stems:
                    continue
                if count >= MAX_FILES or total >= MAX_TOTAL:
                    warnings.append('达到媒体数量或大小上限，其余媒体已省略。')
                    break
                try:
                    body, suffix = fetch_media(url, MAX_TOTAL-total, deadline)
                    archive.writestr(f'{title}/media/{stem}{suffix}', body)
                    total += len(body)
                    count += 1
                except (ValueError, OSError, httpx.HTTPError):
                    warnings.append(f'第 {i+1} 个媒体无法下载（地址过期、网络限制或文件超过上限）。')
            if len(files) > MAX_FILES or len(media_urls) > MAX_FILES:
                warnings.append('最多打包 80 个媒体文件，其余已省略。')
            if not count:
                warnings.append('没有可用媒体。采集时可开启“下载媒体素材”；仅导入分析字段的记录可能没有媒体地址。')
            markdown = f"# {note.get('title') or content_id}\n\n{text}\n\n作者：{note.get('author') or '未提供'}\n\n原始内容：{note.get('url') or '未提供'}\n\n"
            for name in archive.namelist():
                relative = name[len(title)+1:]
                markdown += f'[{Path(relative).name}](<{relative}>)\n\n'
            archive.writestr(f'{title}/{title}.md', markdown)
            archive.writestr(f'{title}/Download Report.txt', f'已打包媒体：{count}\n' + '\n'.join(warnings or ['全部可用本机素材已打包。']))
        return path, title, warnings
    except BaseException:
        path.unlink(missing_ok=True)
        raise
