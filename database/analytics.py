"""Additive SQLite analytics schema. Reads never migrate or modify the source.

Native table triggers participate in the collector's transaction. All timestamps
are UTC ISO strings; naive source dates are interpreted as Asia/Shanghai.
"""
import hashlib
import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

VERSION = 1
PLATFORMS = ('xhs', 'dy', 'ks', 'bili', 'wb', 'tieba', 'zhihu')
# table, platform, content ID, publication column; comments share their parent's ID.
SOURCES = {
    'xhs_note': ('xhs', 'note_id', 'time'),
    'douyin_aweme': ('dy', 'aweme_id', 'create_time'),
    'kuaishou_video': ('ks', 'video_id', 'create_time'),
    'bilibili_video': ('bili', 'video_id', 'create_time'),
    'bilibili_up_dynamic': ('bili', 'dynamic_id', 'pub_ts'),
    'weibo_note': ('wb', 'note_id', 'create_time'),
    'tieba_note': ('tieba', 'note_id', 'publish_time'),
    'zhihu_content': ('zhihu', 'content_id', 'created_time'),
}
COMMENTS = {
    'xhs_note_comment': ('xhs', 'note_id', 'create_time'),
    'douyin_aweme_comment': ('dy', 'aweme_id', 'create_time'),
    'kuaishou_video_comment': ('ks', 'video_id', 'create_time'),
    'bilibili_video_comment': ('bili', 'video_id', 'create_time'),
    'weibo_note_comment': ('wb', 'note_id', 'create_time'),
    'tieba_comment': ('tieba', 'note_id', 'publish_time'),
    'zhihu_comment': ('zhihu', 'content_id', 'publish_time'),
}
ALIASES = {
    'content_id': ('content_id', 'note_id', 'aweme_id', 'video_id', 'dynamic_id', '内容ID', '内容 ID'),
    'author': ('author', 'nickname', 'user_nickname', 'user_name', '作者'),
    'keyword': ('keyword', 'source_keyword', '关键词'),
    'published_at': ('published_at', 'publish_time', 'created_time', 'create_time', 'time', 'pub_ts', '发布时间'),
    'collected_at': ('collected_at', 'last_modify_ts', 'add_ts', '采集时间'),
    'likes': ('likes', 'liked_count', 'voteup_count', 'total_liked', '点赞数'),
    'favorites': ('favorites', 'collected_count', 'video_favorite_count', '收藏数'),
    'comments': ('comments', 'comment_count', 'comments_count', 'video_comment', 'total_comments', '评论数'),
    'shares': ('shares', 'share_count', 'shared_count', 'video_share_count', 'total_forwards', '分享数'),
    'content_type': ('content_type', 'type', 'video_type', 'aweme_type', '内容类型'),
    'url': ('url', 'note_url', 'aweme_url', 'video_url', 'content_url', '原始链接'),
    'title': ('title', 'desc', 'content_text', 'text', 'content', '标题'),
}
CONTENT_COLUMNS = ('platform', 'content_id', 'author_id', 'author', 'keyword', 'published_at', 'collected_at',
                   'likes', 'favorites', 'comments', 'shares', 'content_type', 'url', 'title')
COMMENT_COLUMNS = ('platform', 'comment_id', 'content_id', 'published_at', 'collected_at', 'body')


def number(value):
    if value is None or isinstance(value, bool):
        return None
    s = str(value).strip().lower().replace(',', '').replace('，', '')
    match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(万|亿|千|k|w|m)?', s)
    if not match:
        return None
    n = float(match[1]) * {None: 1, '万': 10000, '亿': 100000000, '千': 1000, 'k': 1000, 'w': 10000, 'm': 1000000}[match[2]]
    return int(n) if math.isfinite(n) and n == int(n) and n <= 2**53 - 1 else None


def timestamp(value):
    if value is None or str(value).strip().lower() in ('', 'none', 'null', 'nan', '0'):
        return None
    try:
        s = str(value).strip()
        if re.fullmatch(r'\d{8}', s):
            dt = datetime.strptime(s, '%Y%m%d').replace(tzinfo=ZoneInfo('Asia/Shanghai'))
        elif re.fullmatch(r'\d+(?:\.\d+)?', s):
            n = float(s)
            if n <= 0:
                return None
            if n > 1e14:
                n /= 1e6
            elif n > 1e11:
                n /= 1000
            dt = datetime.fromtimestamp(n, timezone.utc)
        else:
            dt = datetime.fromisoformat(s.replace('Z', '+00:00').replace('/', '-'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo('Asia/Shanghai'))
        if not 1970 <= dt.year <= 2100:
            return None
        return dt.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def pick(row, names):
    missing = ('', 'none', 'null', 'nan', 'n/a', 'undefined', '--', '-')
    return next((row[k] for k in names if k in row and row[k] is not None and str(row[k]).strip().lower() not in missing), None)


def normalize(row, platform=None, kind='content'):
    platform = platform or pick(row, ('platform', '平台'))
    if platform not in PLATFORMS:
        raise ValueError('平台须为 ' + ', '.join(PLATFORMS))
    result = {key: pick(row, names) for key, names in ALIASES.items()}
    result['platform'] = platform
    for key in ('published_at', 'collected_at'):
        result[key] = timestamp(result[key])
    if isinstance(result['content_id'], (dict, list, bool)):
        raise ValueError('内容 ID 需要字符串或数字')
    if kind == 'comment':
        comment_id = pick(row, ('comment_id', '评论ID'))
        if isinstance(comment_id, (dict, list, bool)):
            raise ValueError('评论 ID 需要字符串或数字')
        if not comment_id or not result['content_id']:
            raise ValueError('评论需要 comment_id 与所属内容 ID')
        return dict(platform=platform, comment_id=str(comment_id), content_id=str(result['content_id']),
                    published_at=result['published_at'], collected_at=result['collected_at'],
                    body=str(pick(row, ('body', 'content', 'comment_content', '评论内容')) or ''))
    if result['content_id'] is None:
        raise ValueError('缺少内容 ID')
    for key in ('likes', 'favorites', 'comments', 'shares'):
        result[key] = number(result[key])
    for key in ('content_id', 'author', 'keyword', 'content_type', 'url', 'title'):
        result[key] = str(result[key]) if result[key] is not None else None
    # Existing privacy-preserving creator hash is preferred; nickname fallback is
    # explicitly labelled in the UI, and never reads raw platform user IDs.
    result['author_id'] = pick(row, ('creator_hash',)) or (hashlib.sha256((platform + ':' + result['author']).encode()).hexdigest() if result['author'] else None)
    return result


SCHEMA = [
    'CREATE TABLE IF NOT EXISTS analytics_meta(version INTEGER NOT NULL)',
    '''CREATE TABLE IF NOT EXISTS analytics_content (
    platform TEXT NOT NULL, content_id TEXT NOT NULL, author_id TEXT, author TEXT, keyword TEXT,
    published_at TEXT, collected_at TEXT, likes INTEGER, favorites INTEGER, comments INTEGER,
    shares INTEGER, content_type TEXT, url TEXT, title TEXT, PRIMARY KEY(platform,content_id))''',
    '''CREATE TABLE IF NOT EXISTS analytics_comment (
    platform TEXT NOT NULL, comment_id TEXT NOT NULL, content_id TEXT NOT NULL,
    published_at TEXT, collected_at TEXT, body TEXT, PRIMARY KEY(platform,comment_id))''',
    '''CREATE TABLE IF NOT EXISTS analytics_snapshot (
    platform TEXT NOT NULL, content_id TEXT NOT NULL, collected_at TEXT NOT NULL,
    likes INTEGER, favorites INTEGER, comments INTEGER, shares INTEGER,
    PRIMARY KEY(platform,content_id,collected_at))''',
    'CREATE INDEX IF NOT EXISTS analytics_publish ON analytics_content(platform,published_at)',
    'CREATE INDEX IF NOT EXISTS analytics_collect ON analytics_content(platform,collected_at)',
    'CREATE INDEX IF NOT EXISTS analytics_keyword ON analytics_content(keyword)',
    'CREATE INDEX IF NOT EXISTS analytics_author ON analytics_content(platform,author_id)',
    'CREATE INDEX IF NOT EXISTS analytics_comment_parent ON analytics_comment(platform,content_id,published_at)',
]


def upsert_sql(table, columns, prefix='?'):
    keys = ('platform', 'content_id') if table.endswith('content') else ('platform', 'comment_id')
    values = ','.join('?' for _ in columns) if prefix == '?' else ','.join(prefix + c for c in columns)
    updates = ','.join(f'{c}=excluded.{c}' for c in columns if c not in keys)
    return f'''INSERT INTO {table}({','.join(columns)}) VALUES({values})
    ON CONFLICT({','.join(keys)}) DO UPDATE SET {updates}
    WHERE coalesce(excluded.collected_at,'') >= coalesce({table}.collected_at,'')'''


def save(db, item, kind='content'):
    columns = CONTENT_COLUMNS if kind == 'content' else COMMENT_COLUMNS
    table = 'analytics_content' if kind == 'content' else 'analytics_comment'
    if kind == 'content':
        snapshot(db, item)
    db.execute(upsert_sql(table, columns), [item[c] for c in columns])


def snapshot(db, item):
    if item['collected_at'] and any(item[k] is not None for k in ('likes', 'favorites', 'comments', 'shares')):
        cols = ('platform', 'content_id', 'collected_at', 'likes', 'favorites', 'comments', 'shares')
        db.execute(f"INSERT OR IGNORE INTO analytics_snapshot VALUES ({','.join('?' for _ in cols)})", [item[c] for c in cols])


def register_functions(db):
    db.create_function('mc_number', 1, number, deterministic=True)
    db.create_function('mc_time', 1, timestamp, deterministic=True)
    db.create_function('mc_author', 2, lambda p, a: hashlib.sha256((p + ':' + a).encode()).hexdigest() if a else None, deterministic=True)


def install_triggers(db, table, spec, kind):
    platform, content_id, published = spec
    available = {r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}
    def col(names):
        cols = [f'''CASE WHEN lower(trim(CAST(NEW."{n}" AS TEXT))) IN ('','none','null','nan','n/a','undefined','--','-') THEN NULL ELSE NEW."{n}" END''' for n in names if n in available]
        return 'coalesce(' + ','.join(cols) + ')' if len(cols) > 1 else cols[0] if cols else 'NULL'
    expressions = {k: col(v) for k, v in ALIASES.items()}
    expressions.update(platform=f"'{platform}'", content_id=f'CAST(NEW."{content_id}" AS TEXT)',
                       published_at=f'mc_time(NEW."{published}")', collected_at=f"mc_time({col(('last_modify_ts', 'add_ts'))})")
    author = expressions['author']
    expressions['author_id'] = f"coalesce({col(('creator_hash',))},mc_author('{platform}',{author}))"
    for k in ('likes', 'favorites', 'comments', 'shares'):
        expressions[k] = f'mc_number({expressions[k]})'
    if kind == 'comment':
        expressions.update(comment_id='CAST(NEW.comment_id AS TEXT)', body=col(('content', 'comment_content')))
    columns = CONTENT_COLUMNS if kind == 'content' else COMMENT_COLUMNS
    target = 'analytics_content' if kind == 'content' else 'analytics_comment'
    statement = upsert_sql(target, columns)
    for k in columns:
        statement = statement.replace('?', expressions[k], 1)
    snap = ''
    if kind == 'content':
        cols = ('platform', 'content_id', 'collected_at', 'likes', 'favorites', 'comments', 'shares')
        snap = f"INSERT OR IGNORE INTO analytics_snapshot SELECT {','.join(expressions[c] for c in cols)} WHERE {expressions['collected_at']} IS NOT NULL AND ({' OR '.join(expressions[c] + ' IS NOT NULL' for c in cols[3:])});"
    for action in ('INSERT', 'UPDATE'):
        name = f'analytics_{table}_{action.lower()}'
        db.execute(f'''CREATE TRIGGER IF NOT EXISTS {name} AFTER {action} ON "{table}"
            WHEN NEW."{content_id}" IS NOT NULL BEGIN {snap} {statement}; END''')


def migrate(path):
    """Explicit, transactional, idempotent initialization; backup before changes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    db = sqlite3.connect(path, timeout=30)
    register_functions(db)
    backup = None
    try:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'analytics_meta' in tables:
            row = db.execute('SELECT version FROM analytics_meta').fetchone()
            if row and row[0] != VERSION:
                raise ValueError('不支持此分析数据库版本，请升级程序')
        if existed and tables and 'analytics_meta' not in tables:
            backup = str(path) + '.analytics-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.bak'
            with sqlite3.connect(backup) as target:
                db.backup(target)
        db.execute('BEGIN IMMEDIATE')
        for sql in SCHEMA:
            db.execute(sql)
        first = db.execute('SELECT version FROM analytics_meta').fetchone() is None
        for kind, sources in (('content', SOURCES), ('comment', COMMENTS)):
            for table, spec in sources.items():
                if table not in tables:
                    continue
                install_triggers(db, table, spec, kind)
                if first:
                    cursor = db.execute(f'SELECT * FROM "{table}" ORDER BY coalesce(last_modify_ts,add_ts,0),rowid')
                    columns = [d[0] for d in cursor.description]
                    for values in cursor:
                        row = dict(zip(columns, values))
                        row['published_at'] = row.get(spec[2])
                        try:
                            save(db, normalize(row, spec[0], kind), kind)
                        except ValueError:
                            continue
        if first:
            db.execute('INSERT INTO analytics_meta VALUES (?)', (VERSION,))
        db.commit()
        return {'version': VERSION, 'backup': backup}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
