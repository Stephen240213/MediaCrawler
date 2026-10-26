"""Idempotent local MCP -> analytics/source/batch bridge. Never calls a provider."""
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from database import analytics as schema
from tools.user_hash import anonymize_user_id, mask_nickname
from . import analytics, collection_batches, workbench_store
from .redfox_payload import payloads, check_result

WRAPPERS = ('data', 'result', 'list', 'items', 'records', 'notes', 'videos', 'tweets', 'comments', 'aweme_list')
IDS = ('content_id', 'workId', 'photoId', 'note_id', 'noteId', 'aweme_id', 'video_id', 'videoId', 'tweet_id', 'tweetId', 'id_str', 'rest_id')


def scalar(row, *keys):
    return next((row[k] for k in keys if isinstance(row.get(k), (str, int, float)) and not isinstance(row[k], bool) and str(row[k]).strip()), None)


def content_id(value, platform):
    value = str(value or '').strip()
    if value.startswith(('https://', 'http://')):
        parsed = urlsplit(value)
        if platform == 'youtube':
            value = parse_qs(parsed.query).get('v', [''])[0] or parsed.path.strip('/').split('/')[-1]
        else:
            value = parsed.path.rstrip('/').split('/')[-1]
    return value if re.fullmatch(r'[A-Za-z0-9_-]{1,200}', value) else ''


def records(value, depth=0, kind='content'):
    if depth > 8:
        return []
    if isinstance(value, list):
        return [row for item in value for row in records(item, depth + 1, kind)]
    if not isinstance(value, dict):
        return []
    if kind == 'comment':
        for key in ('threadReplies', 'comments'):
            if isinstance(value.get(key), list):
                return records(value[key], depth + 1, kind)
    if any(value.get(k) for k in (*IDS, 'commentId', 'comment_id')) and scalar(value, 'title','workTitle','desc','workDesc','content','text','full_text','description'):
        return [value]
    # Known MCP/API containers only; never walk author/statistics objects as posts.
    for key in WRAPPERS:
        if isinstance(value.get(key), (list, dict)):
            return records(value[key], depth + 1, kind)
    return [value]


def mode(tool):
    if tool.endswith('_get_comments_result') or tool in ('twitter_get_comments', 'youtube_get_comments'):
        return 'comment'
    if 'transcript' in tool:
        return 'receipt'
    if any(part in tool for part in ('search_users', 'search_accounts', 'get_account', 'get_user', 'hot_accounts')) and 'user_works' not in tool:
        return 'receipt'
    if tool == 'xiaohongshu_get_comments':
        return 'receipt'
    return 'content'


def ingest_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS redfox_sources(
        platform TEXT,content_id TEXT,body TEXT,creator_hash TEXT,observed_at TEXT,task_id TEXT,
        PRIMARY KEY(platform,content_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS redfox_imports(
        task_id TEXT PRIMARY KEY,digest TEXT,report TEXT)''')
    db.execute('''CREATE TABLE IF NOT EXISTS redfox_remote_tasks(
        platform TEXT,remote_id TEXT,parent_id TEXT,PRIMARY KEY(platform,remote_id))''')


def transform(row, platform, kind, context, observed):
    row = dict(row)
    if isinstance(row.get('note_card'), dict):
        row = {**row, **row['note_card']}
    if isinstance(row.get('legacy'), dict):
        row = {**row, **row['legacy']}
    author = row.get('user') if isinstance(row.get('user'), dict) else row.get('author') if isinstance(row.get('author'), dict) else {}
    stats = row.get('interact_info') or row.get('statistics') or {}
    if isinstance(stats, dict):
        row = {**stats, **row}
    parent = content_id(scalar(row, *IDS), platform) or content_id(context.get('parent_id'), platform)
    if kind == 'comment':
        row_parent = content_id(scalar(row, 'content_id','workId','photoId','note_id','noteId','aweme_id','video_id','videoId'), platform)
        expected_parent = content_id(context.get('parent_id'), platform)
        if row_parent and expected_parent and row_parent != expected_parent:
            raise ValueError('评论所属内容与关联内容 ID 不一致，未写入')
        parent = expected_parent or row_parent
        cid = scalar(row, 'comment_id', 'commentId', 'id_str', 'rest_id', 'id') or (scalar(row, 'tweetId', 'tweet_id') if platform == 'twitter' else None)
        body = scalar(row, 'body', 'text', 'content', 'comment_content', 'commentText')
        if not parent or not cid or not body:
            raise ValueError('评论缺少所属内容 ID、评论 ID 或正文；可填写关联内容 ID 后重新同步')
        return schema.normalize(dict(platform=platform, content_id=parent, comment_id=cid, body=body,
            published_at=publication(row), collected_at=observed), kind='comment'), '', ''
    if not parent:
        # Generic id is accepted only alongside actual post fields, never a bare account/task ID.
        if any(row.get(k) for k in ('title', 'desc', 'description', 'text', 'full_text', 'workTitle', 'content')):
            parent = content_id(scalar(row, 'id'), platform)
    if not parent:
        raise ValueError('记录缺少可核验的作品 ID；未猜测归属')
    body = str(scalar(row, 'workDesc', 'desc', 'description', 'full_text', 'content', 'text', 'body') or '')
    title = scalar(row, 'workTitle', 'title', 'display_title') or body[:255]
    if not title and not body:
        raise ValueError('记录没有标题或正文，未当作有效内容')
    user_id = scalar(row, 'accountUserid', 'userId', 'authorId') or scalar(author, 'user_id', 'userId', 'id_str', 'id', 'channelId')
    nickname = scalar(row, 'accountNickname', 'accountName', 'userName', 'nickname', 'author') or scalar(author, 'nickname', 'name', 'displayName')
    creator = anonymize_user_id(user_id)
    link = scalar(row, 'workUrl', 'url', 'note_url', 'aweme_url', 'video_url')
    if not link:
        link = {'xhs':'https://www.xiaohongshu.com/explore/', 'dy':'https://www.douyin.com/video/',
                'twitter':'https://x.com/i/status/', 'youtube':'https://www.youtube.com/watch?v='}[platform] + parent
    normalized = schema.normalize(dict(platform=platform, content_id=parent, title=title,
        author=mask_nickname(nickname) if nickname else None, creator_hash=creator,
        keyword=context.get('keyword') or None, url=link,
        published_at=publication(row),
        collected_at=observed, content_type=scalar(row, 'workType', 'content_type', 'type', 'aweme_type'),
        likes=scalar(row, 'workLikedCount', 'useLikeCount', 'likeCount', 'likes', 'liked_count', 'digg_count', 'favorite_count'),
        favorites=scalar(row, 'workCollectedCount', 'collectedCount', 'collectCount', 'favorites', 'collected_count', 'collect_count', 'bookmarkCount'),
        comments=scalar(row, 'workCommentsCount', 'useCommentCount', 'commentCount', 'comments_count', 'comment_count', 'reply_count', 'replyCount'),
        shares=scalar(row, 'workSharedCount', 'useShareCount', 'shareCount', 'shares', 'share_count', 'retweet_count', 'retweetCount')))
    # Rounded values such as 2w+ remain unknown, rather than invented exact metrics.
    return normalized, body[:200000], creator


def publication(row):
    value = scalar(row, 'workPublishTime', 'publicTime', 'publishTime', 'published_at', 'publish_time', 'create_time', 'createTime', 'time', 'publishedAt', 'created_at', 'createdAt')
    if isinstance(value,str) and not schema.timestamp(value):
        try:
            return datetime.strptime(value, '%a %b %d %H:%M:%S %z %Y').isoformat()
        except ValueError:
            pass
    return value


def sync_export(envelope, platform, observed=None, parent_id=''):
    """Commit valid rows once, then reconcile workbench membership on every replay."""
    from .redfox import TOOLS
    tool = envelope.get('tool')
    if not any(t['name'] == tool and t['platform'] == platform for t in TOOLS):
        raise ValueError('文件工具与平台不匹配')
    result = check_result(envelope.get('result', {}))
    task_id = str(envelope.get('task_id') or '')
    if envelope.get('source') != 'redfox_mcp' or not re.fullmatch(r'[a-f0-9]{32}', task_id):
        raise ValueError('不是有效的 RedFox 任务导出文件')
    context = dict(envelope.get('context') or {})
    if parent_id:
        context['parent_id'] = content_id(parent_id, platform)
    observed = schema.timestamp(envelope.get('collected_at') or observed) or datetime.now(timezone.utc).isoformat()
    signature = hashlib.sha256(json.dumps([envelope, context], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    path = analytics.db_path()
    schema.migrate(path)
    with sqlite3.connect(path, timeout=30) as db:
        db.row_factory = sqlite3.Row
        schema.register_functions(db)
        db.execute('BEGIN IMMEDIATE')
        ingest_schema(db)
        previous = db.execute('SELECT digest,report FROM redfox_imports WHERE task_id=?', (task_id,)).fetchone()
        replay = bool(previous and previous['digest'] == signature)
        if replay:
            report = json.loads(previous['report'])
        else:
            kind = mode(tool)
            report = dict(contents=0, comments=0, rejected=0, warnings=[], members=[], status='synced')
            values = payloads(result)
            candidates = records(values[0], kind=kind) if values else []
            if kind == 'comment' and any(str(row.get('status','')).lower() in ('pending','processing','queued','running') for row in candidates):
                kind = 'receipt'
            # Some comment envelopes carry the parent once, outside the list.
            container = values[0] if values else {}
            for _ in range(6):
                if not isinstance(container, dict):
                    break
                inferred = content_id(scalar(container, 'content_id','note_id','noteId','opus_id','video_id','videoId','tweetId'), platform)
                if inferred and kind == 'comment' and not context.get('parent_id'):
                    context['parent_id'] = inferred
                container = container.get('data') or container.get('result')
            remote_id = context.get('remote_task_id')
            if not context.get('parent_id') and remote_id:
                known = db.execute('SELECT parent_id FROM redfox_remote_tasks WHERE platform=? AND remote_id=?', (platform, str(remote_id))).fetchone()
                if known:
                    context['parent_id'] = known[0]
            if kind == 'receipt':
                report['status'] = 'raw_only'
                report['warnings'].append('账号信息、任务回执或字幕结果保留原文件；仅内容和评论进入统计。')
                for row in candidates:
                    remote_id = scalar(row, 'taskId', 'task_id')
                    if remote_id and context.get('parent_id'):
                        db.execute('INSERT OR REPLACE INTO redfox_remote_tasks VALUES(?,?,?)', (platform, str(remote_id), context['parent_id']))
            else:
                seen = set()
                for index, row in enumerate(candidates[:50000]):
                    try:
                        item, body, creator = transform(row, platform, kind, context, observed)
                    except ValueError as exc:
                        report['rejected'] += 1
                        if len(report['warnings']) < 10:
                            report['warnings'].append(f'第 {index + 1} 条：{exc}')
                        continue
                    identity = item.get('comment_id') if kind == 'comment' else item['content_id']
                    if identity in seen:
                        continue
                    seen.add(identity)
                    table = 'analytics_comment' if kind == 'comment' else 'analytics_content'
                    key = 'comment_id' if kind == 'comment' else 'content_id'
                    observation = dict(item)
                    old = db.execute(f'SELECT * FROM {table} WHERE platform=? AND {key}=?', (platform, identity)).fetchone()
                    if old:
                        if kind == 'comment' and old['content_id'] != item['content_id']:
                            report['rejected'] += 1
                            continue
                        # Sparse MCP cards must not erase existing complete observations.
                        item = {k: item.get(k) if item.get(k) not in (None, '') else old[k] for k in item}
                    if kind == 'content':
                        schema.snapshot(db, observation)
                    columns = schema.CONTENT_COLUMNS if kind == 'content' else schema.COMMENT_COLUMNS
                    db.execute(schema.upsert_sql(table, columns), [item[c] for c in columns])
                    if kind == 'content':
                        db.execute('''INSERT INTO redfox_sources VALUES(?,?,?,?,?,?)
                            ON CONFLICT(platform,content_id) DO UPDATE SET
                            body=CASE WHEN excluded.body!='' AND (body='' OR excluded.observed_at>=observed_at) THEN excluded.body ELSE body END,
                            creator_hash=CASE WHEN excluded.creator_hash!='' AND (creator_hash='' OR excluded.observed_at>=observed_at) THEN excluded.creator_hash ELSE creator_hash END,
                            observed_at=CASE WHEN excluded.body!='' AND (body='' OR excluded.observed_at>=observed_at) THEN excluded.observed_at ELSE observed_at END,
                            task_id=CASE WHEN excluded.body!='' AND (body='' OR excluded.observed_at>=observed_at) THEN excluded.task_id ELSE task_id END''',
                            (platform, item['content_id'], body, creator, observed, task_id))
                    report['comments' if kind == 'comment' else 'contents'] += 1
                    if item['content_id'] not in [m['content_id'] for m in report['members']]:
                        action = 'reused' if kind == 'comment' or old and (old['collected_at'] or '') > observed else 'refreshed' if old else 'new'
                        report['members'].append(dict(content_id=item['content_id'], title=item.get('title') or '', keyword=context.get('keyword') or '', action=action))
                if not report['contents'] and not report['comments']:
                    report['status'] = 'unrecognized' if report['rejected'] or not values else 'empty'
                    report['warnings'].append('没有可同步的内容或评论；请核对结果结构、关联内容 ID 或异步任务是否完成。')
                if kind == 'comment':
                    report['warnings'].append('评论按所属内容 ID 关联；尚未采集的父内容需另行采集，才会进入内容统计与分析。')
            db.execute('INSERT OR REPLACE INTO redfox_imports VALUES(?,?,?)', (task_id, signature, json.dumps(report, ensure_ascii=False)))
        # Preserve every exact batch, including replays of exports written before this table existed.
        for member in report['members']:
            db.execute('INSERT OR IGNORE INTO analytics_collection_memberships VALUES(?,?,?,?)',
                       (task_id, platform, member['content_id'], 'redfox_mcp'))
    # Analytics commits first; replay repairs this second durable store if interrupted.
    if platform == 'xhs':
        for member in report['members']:
            collection_batches.record(task_id, collection_batches.ContentEvent(**member))
    summary = {k:v for k,v in report.items() if k != 'members'}
    summary['replayed'] = replay
    task = workbench_store.get_document('task', task_id)
    if task:
        task['ingestion'] = summary
        if platform == 'xhs' and report['members']:
            task['membership_version'] = 1
            task['dataset_counts'] = {'total':len(report['members'])}
        workbench_store.save_document('task', task)
    return summary


def sync_file(relative, parent_id=''):
    from api.routers.data import DATA_DIR
    parts = Path(relative).parts
    if len(parts) != 3 or parts[1] != 'redfox' or not re.fullmatch(r'[a-f0-9]{32}\.json', parts[2]):
        raise ValueError('请选择 MCP 原始 JSON 文件')
    root = DATA_DIR.resolve()
    path = root.joinpath(*parts)
    if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file() or path.stat().st_size > 8*1024*1024:
        raise ValueError('MCP 文件不存在或超过大小上限')
    data = json.loads(path.read_text())
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise ValueError('MCP 文件格式无效')
    if data[0].get('task_id') != path.stem:
        raise ValueError('文件名与导出任务 ID 不一致')
    task = workbench_store.get_document('task', parts[2][:-5])
    if task and task.get('outcome') == 'running':
        raise ValueError('请等待当前任务结束后再同步')
    try:
        return sync_export(data[0], parts[0], observed=path.stat().st_mtime, parent_id=parent_id)
    except ValueError as exc:
        # Correct historical false-success receipts, retaining the original file.
        if task and task.get('collection_method') == 'redfox_mcp' and '业务失败' in str(exc):
            task.update(outcome='failed',exit_code=1,ingestion={'status':'error','contents':0,'comments':0,'warnings':[str(exc)]})
            workbench_store.save_document('task', task)
        raise
