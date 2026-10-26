"""Saved-source reuse and exact task membership. No platform/model requests."""
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime,timezone
from pydantic import BaseModel,ConfigDict,Field
from typing import Literal
from . import analytics,workbench_store as store


class ContentEvent(BaseModel):
    model_config=ConfigDict(extra='forbid')
    platform: Literal['xhs']='xhs'
    content_id: str=Field(min_length=1,max_length=200)
    keyword: str=Field(default='',max_length=1000)
    title: str=Field(default='',max_length=1000)
    action: Literal['new','refreshed','reused']='new'


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS collection_memberships(
        task_id TEXT,platform TEXT,content_id TEXT,keyword TEXT,action TEXT,title TEXT,
        PRIMARY KEY(task_id,platform,content_id,keyword))''')
    db.execute('CREATE INDEX IF NOT EXISTS collection_source ON collection_memberships(platform,content_id)')


def record(task_id,event):
    with store.connect() as db:
        schema(db)
        db.execute('''INSERT INTO collection_memberships VALUES(?,?,?,?,?,?)
            ON CONFLICT(task_id,platform,content_id,keyword) DO UPDATE SET title=excluded.title''',
            (task_id,event.platform,event.content_id,event.keyword,event.action,event.title))


def emit(content_id,title,keyword,action):
    event=ContentEvent(content_id=str(content_id),title=str(title or '')[:1000],keyword=str(keyword or '')[:1000],action=action)
    print('MC_CONTENT '+event.model_dump_json(),flush=True)


def existing_note(content_id):
    with analytics.read_db() as (db,_):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='xhs_note'").fetchone():return None
        row=db.execute('SELECT * FROM xhs_note WHERE note_id=? LIMIT 1',(content_id,)).fetchone()
    return dict(row) if row else None


def media_note(row):
    images=[dict(url_default=url) for url in str(row.get('image_list') or '').split(',') if url]
    videos=[dict(master_url=url) for url in str(row.get('video_url') or '').split(',') if url]
    return dict(note_id=row['note_id'],type=row.get('type'),image_list=images,video={'media':{'stream':{'h264':videos}}})


def observe_keyword(content_id,keyword):
    """Reuse still records a newly observed discovery relation, without a refetch."""
    if not keyword:return
    from database.analytics import migrate
    path=analytics.db_path()
    with analytics.read_db() as (db,_):ready=db.execute("SELECT 1 FROM sqlite_master WHERE name='source_keyword_memberships'").fetchone()
    if not ready:migrate(path)
    stamp=datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(path,timeout=10) as db:
        db.execute('''INSERT INTO source_keyword_memberships VALUES('xhs',?,?,?,?)
            ON CONFLICT(platform,content_id,keyword) DO UPDATE SET last_seen=excluded.last_seen''',(content_id,keyword,stamp,stamp))


def _rows(task_id):
    with store.connect() as db:
        schema(db)
        return [dict(zip(('platform','content_id','keyword','action','title'),r)) for r in db.execute('SELECT platform,content_id,keyword,action,title FROM collection_memberships WHERE task_id=? ORDER BY content_id,keyword',(task_id,))]


def legacy_rows(task):
    """Recover attempted source IDs from recognizable old store logs only.

    Old logs precede the actual store commit. These are explicitly not a frozen
    historical snapshot, even if current SQLite contains those IDs.
    """
    found={}
    with store.connect() as db:
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='task_logs'").fetchone()
        if not exists:return []
        for row in db.execute('SELECT message FROM task_logs WHERE task_id=? ORDER BY id',(task['id'],)):
            text=row[0]
            if '[store.xhs.update_xhs_note] xhs note:' not in text:continue
            match=re.search(r"['\"]note_id['\"]\s*:\s*['\"]([A-Za-z0-9_-]{1,200})['\"]",text)
            if not match:continue
            keyword=re.search(r"['\"]source_keyword['\"]\s*:\s*['\"]([^'\"]{0,1000})['\"]",text)
            found[match[1]]=dict(platform='xhs',content_id=match[1],keyword=keyword[1] if keyword else '',action='historical',title='')
    return list(found.values())


def members(task_id):
    task=store.get_document('task',task_id)
    rows=_rows(task_id)
    if not task:
        if not rows:raise ValueError('采集任务不存在')
        # History deletion must not invalidate a research scope using the batch.
        task=dict(id=task_id,target='已移除历史记录的采集批次',membership_version=1,outcome='completed',history_deleted=True)
    exact=bool(task.get('membership_version')==1)
    if not exact and not rows:rows=legacy_rows(task)
    by_id={}
    for row in rows:
        item=by_id.setdefault((row['platform'],row['content_id']),dict(row,keywords=[]))
        if row['keyword'] and row['keyword'] not in item['keywords']:item['keywords'].append(row['keyword'])
        if row['action']=='new' or row['action']=='refreshed' and item['action']=='reused':item['action']=row['action']
    with analytics.read_db() as (db,_):
        for item in by_id.values():
            current=db.execute('SELECT title,author,content_type,likes,comments,collected_at FROM analytics_content WHERE platform=? AND content_id=?',(item['platform'],item['content_id'])).fetchone()
            item.update(available=bool(current),**(dict(current) if current else {}))
    items=list(by_id.values());counts=dict(Counter(i['action'] for i in items))
    return dict(task=task,exact=exact,items=items,total=len(items),counts=counts,
                unavailable=sum(not i['available'] for i in items),
                scope='本次采集保存或复用的去重素材，正文与互动展示资料库当前版本。' if exact else '历史任务未保存精确成员表；仅列日志可追溯且当前可检查的内容，不等同完整历史快照。没有记录时不按关键词猜测归属。')


def selected_ids(task_id):
    value=members(task_id)
    if not value['exact']:raise ValueError('历史任务没有精确批次成员，请在资料列表核对后手动选择内容')
    return [i['content_id'] for i in value['items'] if i['platform']=='xhs' and i['available']]


def choices(search='',keyword='',task_id='',page=1):
    ids=None;scope=None
    if task_id:
        dataset=members(task_id);ids={i['content_id'] for i in dataset['items'] if i['available']};scope=dataset['scope']
    with analytics.read_db() as (db,_):
        clauses=["c.platform='xhs'"];args=[]
        if search:clauses.append("(instr(coalesce(c.title,''),?)>0 OR instr(coalesce(c.author,''),?)>0 OR c.content_id=?)");args.extend([search]*3)
        if keyword:
            exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='source_keyword_memberships'").fetchone()
            clause='c.keyword=?';args.append(keyword)
            if exists:clause+=' OR EXISTS(SELECT 1 FROM source_keyword_memberships k WHERE k.platform=c.platform AND k.content_id=c.content_id AND k.keyword=?)';args.append(keyword)
            clauses.append('('+clause+')')
        # Temp membership filter is expressed as JSON to avoid SQLite parameter limits.
        if ids is not None:clauses.append('c.content_id IN (SELECT value FROM json_each(?))');args.append(json.dumps(sorted(ids)))
        where=' AND '.join(clauses)
        total=db.execute('SELECT count(*) FROM analytics_content c WHERE '+where,args).fetchone()[0]
        rows=db.execute('SELECT content_id,title,author,keyword,content_type,likes,comments FROM analytics_content c WHERE '+where+' ORDER BY collected_at DESC,content_id LIMIT 20 OFFSET ?',args+[(page-1)*20]).fetchall()
    return dict(items=[dict(r) for r in rows],total=total,page=page,scope=scope)
