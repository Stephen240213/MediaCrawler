"""Local, versioned template artwork and subtitle handoff. Never publishes or calls a model."""
import hashlib
import html
import io
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4
import zipfile

from PIL import Image, ImageDraw, ImageFont

from . import store
from .schemas import DesignPage, ProductionInput

RENDERER = 'remix-templates-v1'
SIZES = {'3:4': (1080, 1440), '1:1': (1080, 1080), '9:16': (1080, 1920)}
THEMES = {
    'paper': ('#F6F1E7', '#222922', '#557259', '#DFE5D8'),
    'ocean': ('#EAF3F5', '#133542', '#187B86', '#CFE4E8'),
    'night': ('#192333', '#F5F1E8', '#F0BB70', '#29394E'),
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def font_path():
    candidates = [os.environ.get('MC_REMIX_DESIGN_FONT', ''),
        '/System/Library/Fonts/STHeiti Medium.ttc',
        '/System/Library/Fonts/PingFang.ttc',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc']
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            try:
                ImageFont.truetype(candidate, 24)
                return Path(candidate)
            except OSError:
                pass
    raise ValueError('未找到可用的中文字体，请在本机配置 MC_REMIX_DESIGN_FONT 后重试；不会自动下载字体')


def status():
    try:
        path = font_path()
        return dict(ready=True, font=path.name, renderer=RENDERER)
    except ValueError as exc:
        return dict(ready=False, issue=str(exc), renderer=RENDERER)


def listing(job_id):
    with store.connect() as db:
        rows = db.execute('SELECT body FROM production_revisions WHERE job_id=? ORDER BY rowid DESC LIMIT 100', (job_id,)).fetchall()
    values = [json.loads(row[0]) for row in rows]
    return [{k:v[k] for k in ('id', 'job_id', 'version_id', 'created_at', 'title', 'status')} for v in values]


def get(revision_id):
    with store.connect() as db:
        row = db.execute('SELECT body FROM production_revisions WHERE id=?', (revision_id,)).fetchone()
    return json.loads(row[0]) if row else None


def source_version(job_id, version_id):
    job = store.get(job_id)
    if not job or job['status'] != 'completed':
        raise ValueError('请先完成分析并保存成稿版本')
    version = next((v for v in store.versions(job_id) if v['id'] == version_id), None)
    if not version:
        raise ValueError('请选择本任务已保存的成稿版本')
    return job, version


def seed(job_id, version_id):
    job, version = source_version(job_id, version_id)
    rewrite = job['report']['rewrites'][version['rewrite_index']]
    paragraphs = [version['content'][i:i+650] for i in range(0, len(version['content']), 650)]
    warnings = []
    if len(paragraphs) > 8:
        warnings.append('正文超过8页默认卡片容量，余下内容仍保留在发布包正文，请人工选择卡片重点。')
    if len(version['title']) > 80:
        warnings.append('封面标题已取前80字，请人工精简。')
    # Timecodes come from the full saved transcript, never from generated copy.
    cues = job.get('transcript') or []
    return dict(version_id=version_id, theme='paper', ratio='3:4',
        cover=dict(title=version['title'][:80], body='', eyebrow='内容笔记', footer=''),
        cards=[dict(title=f'内容要点 {i+1:02d}', body=p, eyebrow='阅读笔记', footer='') for i, p in enumerate(paragraphs[:8])],
        subtitle_basis='none', subtitle_duration=None, cues=[],
        checks=dict(copy_checked=False, facts=False, rights=False, subtitles=False),
        suggestions=dict(cover_text=rewrite.get('cover_text', ''), visuals=rewrite.get('visuals', []),
            fact_checks=rewrite.get('fact_checks', []), original_subtitles_available=bool(cues),
            original_cue_count=len(cues), warnings=warnings))


def original_subtitles(job_id):
    job = store.get(job_id)
    if not job or not job.get('transcript') or not job.get('duration'):
        raise ValueError('本任务没有完整的原视频转写时间轴，请手动输入已录制视频的真实字幕时间')
    if len(job['transcript']) > 1500:
        raise ValueError('原转写超过1500段，请先人工合并字幕段落')
    return dict(subtitle_basis='original_video', subtitle_duration=job['duration'], cues=job['transcript'])


def clean(value):
    if any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise ValueError('文字包含不支持的控制字符')
    return value.replace('\t', '    ').strip()


def wrap(text, font, width):
    lines = []
    for paragraph in text.split('\n'):
        line = ''
        for char in paragraph:
            if line and font.getlength(line+char) > width:
                lines.append(line); line = ''
            line += char
        lines.append(line)
    return lines


def fitted(text, path, width, height, largest, smallest, balance=False):
    for size in range(largest, smallest-1, -2):
        font = ImageFont.truetype(str(path), size)
        lines = wrap(text, font, width)
        if balance and len(lines) > 1 and '\n' not in text:
            target = min(width, math.ceil(font.getlength(text)/len(lines)+size*.5))
            while target < width:
                balanced = wrap(text, font, target)
                if len(balanced) <= len(lines):
                    lines = balanced
                    break
                target += max(1, size//4)
        leading = math.ceil(size*1.55)
        if len(lines)*leading <= height:
            return font, lines, leading
    raise ValueError('页面文字超出安全区域，请精简标题/正文、拆分卡片或选择更高的画幅；不会静默截断文字')


def render(page, theme, ratio, number, total, path):
    width, height = SIZES[ratio]
    background, ink, accent, muted = THEMES[theme]
    image = Image.new('RGB', (width, height), background)
    draw = ImageDraw.Draw(image)
    margin = 82; area = width-margin*2
    draw.rounded_rectangle((margin, 70, margin+66, 80), radius=5, fill=accent)
    label_font = ImageFont.truetype(str(path), 27)
    draw.ellipse((width-250, -110, width+150, 290), fill=muted)
    draw.text((margin, 112), clean(page['eyebrow']), font=label_font, fill=accent, anchor='lt')
    title_height = int(height*.29)
    font, lines, leading = fitted(clean(page['title']), path, area, title_height, 86 if number == 0 else 68, 38, balance=True)
    y = 230
    for line in lines:
        draw.text((margin, y), line, font=font, fill=ink, anchor='lt'); y += leading
    y += 45
    if page['body'].strip():
        draw.rectangle((margin, y-18, margin+area, y-16), fill=accent)
        font, lines, leading = fitted(clean(page['body']), path, area, height-y-170, 43, 24)
        for line in lines:
            draw.text((margin, y), line, font=font, fill=ink, anchor='lt'); y += leading
    draw.line((margin, height-112, width-margin, height-112), fill=accent, width=2)
    footer = clean(page['footer'])
    footer_font, footer_lines, _ = fitted(footer, path, area-150, 45, 24, 18)
    draw.text((margin, height-80), footer_lines[0], font=footer_font, fill=ink, anchor='lt')
    draw.text((width-margin, height-80), 'COVER' if number == 0 else f'{number:02d} / {total:02d}', font=label_font, fill=accent, anchor='rt')
    output = io.BytesIO(); image.save(output, format='PNG')
    return output.getvalue()


def validate_cues(spec, job):
    cues = spec['cues']; basis = spec['subtitle_basis']
    if basis == 'none':
        if cues or spec['subtitle_duration'] is not None:
            raise ValueError('未启用字幕时请清空时间轴和时长')
        return
    if not cues:
        raise ValueError('启用字幕后至少需要一段真实时间轴')
    duration = spec['subtitle_duration']
    if basis == 'original_video':
        if not job.get('transcript') or not job.get('duration'):
            raise ValueError('本任务没有可对应的原视频字幕')
        if duration != job['duration']:
            raise ValueError('原视频字幕必须使用原视频真实时长')
    if duration is None:
        raise ValueError('请填写字幕对应视频的真实时长')
    last = 0
    for cue in cues:
        start, end = round(cue['start']*1000), round(cue['end']*1000)
        text = clean(cue['text'])
        if start < last or end <= start or end > round(duration*1000):
            raise ValueError('字幕须按时间排序且不重叠，结束必须晚于开始并位于视频时长内（毫秒精度）')
        if not text or len(text.splitlines()) > 2 or '-->' in text or '\n\n' in text:
            raise ValueError('字幕文字不能为空，每段最多两行且不能包含时间分隔符或空行')
        cue['text'] = text
        last = end
    if sum(len(c['text']) for c in cues) > 100000:
        raise ValueError('字幕文字总量不能超过100000字')


def clock(seconds, separator):
    ms = round(seconds*1000); hours, ms = divmod(ms, 3600000); minutes, ms = divmod(ms, 60000); secs, ms = divmod(ms, 1000)
    return f'{hours:02d}:{minutes:02d}:{secs:02d}{separator}{ms:03d}'


def subtitle_files(spec):
    if not spec['cues']:
        return {}
    stem = 'original-video' if spec['subtitle_basis'] == 'original_video' else 'manual-video'
    srt = []; vtt = ['WEBVTT\n']
    for index, cue in enumerate(spec['cues'], 1):
        text = html.escape(cue['text'], quote=False)
        srt.append(f"{index}\n{clock(cue['start'], ',')} --> {clock(cue['end'], ',')}\n{text}\n")
        vtt.append(f"{clock(cue['start'], '.')} --> {clock(cue['end'], '.')}\n{text}\n")
    return {f'{stem}.srt': '\n'.join(srt).encode(), f'{stem}.vtt': '\n'.join(vtt).encode()}


def folder(revision_id):
    return store.root()/'production'/revision_id


def save(job_id, payload: ProductionInput, actor):
    job, version = source_version(job_id, payload.version_id)
    spec = payload.model_dump(exclude={'request_key'})
    fingerprint = digest(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode())
    with store.connect() as db:
        row = db.execute('SELECT fingerprint,body FROM production_revisions WHERE job_id=? AND request_key=?', (job_id, payload.request_key)).fetchone()
    if row:
        if row[0] != fingerprint: raise ValueError('重复请求键对应的制作内容不同')
        return json.loads(row[1])
    validate_cues(spec, job)
    for page in [spec['cover'], *spec['cards']]:
        if not clean(page['title']): raise ValueError('页面标题不能为空')
        for key in ('title', 'body', 'eyebrow', 'footer'): page[key] = clean(page[key])
    path = font_path()
    files = {'cover.png': render(spec['cover'], spec['theme'], spec['ratio'], 0, len(spec['cards']), path)}
    for index, page in enumerate(spec['cards'], 1):
        files[f'card-{index:02d}.png'] = render(page, spec['theme'], spec['ratio'], index, len(spec['cards']), path)
    files.update(subtitle_files(spec))
    files.update({'title.txt': version['title'].encode(), 'body.txt': version['content'].encode(),
                  'tags.txt': '\n'.join('#'+t.lstrip('#') for t in version['tags']).encode(),
                  'design.json': json.dumps(spec, ensure_ascii=False, indent=2).encode()})
    revision_id = uuid4().hex
    data = dict(id=revision_id, job_id=job_id, version_id=version['id'], created_at=store.now(), actor=actor,
        title=version['title'], spec=spec, renderer=RENDERER,
        font=dict(name=path.name, sha256=digest(path.read_bytes())),
        source_version_sha256=digest(json.dumps({k:version[k] for k in ('id', 'title', 'content', 'tags', 'framework')}, sort_keys=True, ensure_ascii=False).encode()),
        profile=job.get('source', {}).get('profile'),
        fact_checks=job['report']['rewrites'][version['rewrite_index']].get('fact_checks', []),
        artifacts=[dict(name=name, size=len(body), sha256=digest(body)) for name, body in files.items()],
        status='prepared', published=False)
    directory = store.root()/'production'; directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.building-', dir=directory))
    committed = False
    try:
        for name, body in files.items():
            file = temporary/name; file.write_bytes(body); file.chmod(0o600)
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT fingerprint,body FROM production_revisions WHERE job_id=? AND request_key=?', (job_id, payload.request_key)).fetchone()
            if row:
                if row[0] != fingerprint: raise ValueError('重复请求键对应的制作内容不同')
                return json.loads(row[1])
            if db.execute('SELECT count(*) FROM production_revisions WHERE job_id=?', (job_id,)).fetchone()[0] >= 100:
                raise ValueError('每个任务最多保留100份制作版本')
            temporary.rename(folder(revision_id))
            db.execute('INSERT INTO production_revisions VALUES(?,?,?,?,?)', (revision_id, job_id, payload.request_key, fingerprint, json.dumps(data, ensure_ascii=False)))
        committed = True
        return data
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
        if not committed: shutil.rmtree(folder(revision_id), ignore_errors=True)


def asset(revision, name):
    entry = next((a for a in revision['artifacts'] if a['name'] == name), None)
    if not entry or Path(name).name != name:
        raise ValueError('产物不存在')
    directory = folder(revision['id'])
    path = directory/name
    if directory.is_symlink() or path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError('产物文件缺失或路径无效，请另存新的制作版本')
    data = path.read_bytes()
    if len(data) != entry['size'] or digest(data) != entry['sha256']:
        raise ValueError('产物校验失败，请另存新的制作版本')
    return data


def bundle(revision):
    checks = revision['spec']['checks']
    required = ['copy_checked', 'facts', 'rights'] + (['subtitles'] if revision['spec']['cues'] else [])
    if not all(checks[k] for k in required):
        raise ValueError('导出发布包前请核对文稿、事实、素材权利及所用字幕，再保存新的制作版本')
    manifest = {k:revision[k] for k in ('id', 'job_id', 'version_id', 'created_at', 'renderer', 'font', 'source_version_sha256', 'artifacts')}
    manifest.update(status='manual_handoff_only', published=False, subtitle_basis=revision['spec']['subtitle_basis'],
                    checks=checks, contains_original_video=False)
    text = ('人工发布包（导出不等于已发布）\n\n'
        '1. 核对 title.txt、body.txt、tags.txt，按目标平台要求粘贴或调整。\n'
        '2. cover.png 为封面，card-XX.png 按编号排序；design.json 保留可编辑内容。\n'
        '3. 原视频字幕只对应原始素材，不能用于改写稿或重新录制视频；手工时间轴需在对应成片中核对。包内不含原视频，也不自动合成带字幕的视频。\n'
        '4. 在平台官方客户端/创作者页面选择正确账号，人工检查预览并提交。本工具没有调用发布接口。\n'
        '5. 发布成功后，回工作台“发布结果与同类复盘”登记实际链接、成稿版本和发布时间。\n'
        '6. manifest.json 记录版本、输入指纹和产物 SHA-256；不可将导出状态填作发布成功。\n\n待核实信息（人工确认不等于平台独立验证）：\n'
        + '\n'.join('- '+line for line in revision['fact_checks']))
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for entry in revision['artifacts']:
            archive.writestr(entry['name'], asset(revision, entry['name']))
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr('README.txt', text)
    return output.getvalue()
