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
from media_downloader.downloader import DEFAULT_USER_AGENT
from . import analytics

MAX_TOTAL = 100 * 1024 * 1024
MAX_FILES = 80
CDNS = ('xhscdn.com', 'xiaohongshu.com', 'douyinvod.com', 'douyinpic.com', 'byteimg.com',
        'hdslb.com', 'bilivideo.com', 'sinaimg.cn', 'weibocdn.com', 'kwimgs.com', 'kwaicdn.com', 'zhimg.com', 'bdimg.com', 'baidu.com')


def public_addresses(host, port, deadline):
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    ips = list(dict.fromkeys(address[4][0] for address in addresses))
    if ips and all(ipaddress.ip_address(ip).is_global for ip in ips):
        return ips
    # Some local VPN clients synthesize RFC 2544 addresses for public names.
    # Never connect to those addresses. Resolve only allowlisted CDN names via
    # a fixed HTTPS resolver and still require globally routable answers.
    fake_network = ipaddress.ip_network('198.18.0.0/15')
    if not ips or not all(ipaddress.ip_address(ip) in fake_network for ip in ips):
        raise ValueError('媒体 DNS 返回非公网地址，已阻止访问')
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError('达到下载时间上限')
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=min(8, remaining)) as resolver:
        response = resolver.get('https://1.1.1.1/dns-query', params={'name':host, 'type':'A'},
                                headers={'Host':'cloudflare-dns.com','Accept':'application/dns-json'},
                                extensions={'sni_hostname':'cloudflare-dns.com'})
        response.raise_for_status()
        if len(response.content) > 65536:
            raise ValueError('DNS 响应过大')
        data = response.json()
        answers = data.get('Answer') if isinstance(data, dict) else None
        if not isinstance(answers, list):
            raise ValueError('代理 DNS 虚拟 IP 无法解析为有效公网地址')
        ips = [entry['data'] for entry in answers[:64] if isinstance(entry, dict) and entry.get('type') == 1 and isinstance(entry.get('data'), str)]
        if data.get('Status') != 0 or not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise ValueError('代理 DNS 虚拟 IP 无法解析为有效公网地址')
        return list(dict.fromkeys(ips))


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
            port = parts.port or (443 if parts.scheme == 'https' else 80)
            ips = public_addresses(host, port, deadline)
            pinned = httpx.URL(url).copy_with(host=ips[0])
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError('达到下载时间上限')
            referer = ('https://www.xiaohongshu.com/' if host.endswith(('.xhscdn.com','.xiaohongshu.com'))
                       else 'https://www.bilibili.com/' if host.endswith(('.hdslb.com','.bilivideo.com'))
                       else 'https://www.douyin.com/' if host.endswith(('.douyinvod.com','.douyinpic.com','.byteimg.com'))
                       else None)
            headers = {'Host':parts.netloc, 'User-Agent':DEFAULT_USER_AGENT, 'Accept':'*/*', 'Accept-Encoding':'identity'}
            if referer:
                headers['Referer'] = referer
            client.cookies.clear()
            with client.stream('GET', pinned, headers=headers, extensions={'sni_hostname':host}, timeout=min(15, remaining)) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get('location', ''))
                    continue
                response.raise_for_status()
                mime = response.headers.get('content-type', '').split(';')[0]
                mime = mime.lower()
                neutral = mime in ('', 'application/octet-stream', 'binary/octet-stream', 'application/mp4')
                if not mime.startswith(('image/', 'video/')) and not neutral:
                    raise ValueError('返回的文件不是图片或视频（可能是错误页或未支持的播放清单）')
                length = response.headers.get('content-length','')
                if length.isdigit() and int(length) > budget:
                    raise ValueError('媒体超过剩余 100 MB 打包上限')
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    if len(chunks) + len(chunk) > budget or time.monotonic() > deadline:
                        raise ValueError('达到文件大小或下载时间上限')
                    chunks.extend(chunk)
                if not chunks:
                    raise ValueError('媒体响应为空')
                # CDN video responses often use octet-stream and extensionless URLs.
                # Require a recognizable signature for ambiguous MIME, never save HTML as MP4.
                signature = ('.avif' if len(chunks)>=12 and chunks[4:8] == b'ftyp' and chunks[8:12] in (b'avif', b'avis') else
                             '.mp4' if len(chunks)>=12 and chunks[4:8] == b'ftyp' else
                             '.webm' if chunks[:4] == b'\x1aE\xdf\xa3' else
                             '.jpg' if chunks[:3] == b'\xff\xd8\xff' else
                             '.png' if chunks[:8] == b'\x89PNG\r\n\x1a\n' else
                             '.gif' if chunks[:6] in (b'GIF87a',b'GIF89a') else
                             '.webp' if chunks[:4] == b'RIFF' and chunks[8:12] == b'WEBP' else None)
                if chunks.lstrip()[:20].lower().startswith((b'<html', b'<!doctype', b'{"')) or (neutral and not signature):
                    raise ValueError('无法识别有效媒体，可能是 CDN 错误响应')
                encoding = response.headers.get('content-encoding','identity').lower()
                if length.isdigit() and encoding in ('','identity') and len(chunks) != int(length):
                    raise ValueError('媒体下载不完整')
                return bytes(chunks), signature or guess_extension(url=url, content_type=mime, default='.bin')
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
    candidates = [(f'{i:03d}', [url]) for i, url in enumerate(images, 1)]
    if platform == 'xhs' and (videos or native.get('type') == 'video'):
        candidates = [('cover', [images[0]])] if images else []
        candidates += [('video', videos[:8])] if videos else []
    else:
        candidates += [(f'video-{i:03d}', [url]) for i,url in enumerate(videos,1)]
    expected_video = bool(videos or native.get('type') == 'video' or note.get('content_type') == 'video')
    media_urls = candidates
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
            video_count = 0
            local_stems = set()
            for p in files[:MAX_FILES]:
                if p.stat().st_size == 0:
                    warnings.append('本机有未完成的空媒体文件，将尝试保存的备用地址。')
                    continue
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
                local_stems.add(p.stem)
                video_count += int(p.suffix.lower() in {'.mp4','.mov','.webm'})
            deadline = time.monotonic() + 75
            for i, (stem, alternatives) in enumerate(candidates[:MAX_FILES]):
                if stem in local_stems:
                    continue
                if count >= MAX_FILES or total >= MAX_TOTAL:
                    warnings.append('达到媒体数量或大小上限，其余媒体已省略。')
                    break
                failures = []
                for url in alternatives:
                    if time.monotonic() >= deadline:
                        failures.append('达到下载时间上限')
                        break
                    try:
                        body, suffix = fetch_media(url, MAX_TOTAL-total, deadline)
                        is_video = suffix in {'.mp4','.webm','.mov'}
                        if stem.startswith('video') and not is_video:
                            raise ValueError('视频地址未返回可识别的视频文件')
                        archive.writestr(f'{title}/media/{stem}{suffix}', body)
                        total += len(body)
                        count += 1
                        video_count += int(is_video)
                        break
                    except httpx.HTTPStatusError as exc:
                        failures.append(f'HTTP {exc.response.status_code}')
                    except httpx.TimeoutException:
                        failures.append('网络超时')
                    except httpx.HTTPError:
                        failures.append('网络连接或 TLS 失败')
                    except socket.gaierror:
                        failures.append('DNS 解析失败')
                    except OSError:
                        failures.append('网络或文件读取失败')
                    except ValueError as exc:
                        failures.append(str(exc))
                if failures and (not archive.namelist() or not any(n.startswith(f'{title}/media/{stem}.') for n in archive.namelist())):
                    warnings.append(f'{"视频" if stem.startswith("video") else "图片"} {i+1} 下载失败：' + '；'.join(dict.fromkeys(failures)))
            if expected_video and not video_count:
                warnings.append('此笔记的视频未打包。请查看失败原因；媒体地址缺失或全部失效时，需要在采集电脑重新采集此笔记以更新地址。')
            if len(files) > MAX_FILES or len(media_urls) > MAX_FILES:
                warnings.append('最多打包 80 个媒体文件，其余已省略。')
            if not count:
                warnings.append('没有可用媒体。采集时可开启“下载媒体素材”；仅导入分析字段的记录可能没有媒体地址。')
            markdown = f"# {note.get('title') or content_id}\n\n{text}\n\n作者：{note.get('author') or '未提供'}\n\n原始内容：{note.get('url') or '未提供'}\n\n"
            for name in archive.namelist():
                relative = name[len(title)+1:]
                markdown += f'[{Path(relative).name}](<{relative}>)\n\n'
            archive.writestr(f'{title}/{title}.md', markdown)
            archive.writestr(f'{title}/Download Report.txt', f'已打包媒体：{count}；视频：{video_count}\n' + '\n'.join(warnings or ['全部可用本机素材已打包。']))
        return path, title, warnings
    except BaseException:
        path.unlink(missing_ok=True)
        raise
