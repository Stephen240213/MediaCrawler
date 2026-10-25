"""Analysis DB is separate from both collection and workbench databases."""
import hashlib
import json
import sqlite3
import shutil
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from api.services import workbench_store

CHUNK_SIZE = 4*1024*1024
ACTIVE = ('queued', 'running')


def root():
    return workbench_store.DB_PATH.parent / 'content-remix'


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect():
    root().mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(root()/'analysis.sqlite3', timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,actor TEXT,request_key TEXT,fingerprint TEXT,status TEXT,stage TEXT,body TEXT,UNIQUE(actor,request_key))')
        db.execute('CREATE INDEX IF NOT EXISTS remix_status ON jobs(status)')
        db.execute('CREATE TABLE IF NOT EXISTS model_cache(key TEXT PRIMARY KEY,body TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY,actor TEXT,request_key TEXT,filename TEXT,size INTEGER,received INTEGER,sha256 TEXT,created_at TEXT,UNIQUE(actor,request_key))')
        with db:
            yield db
    finally:
        db.close()


def get(job_id):
    with connect() as db:
        row = db.execute('SELECT body FROM jobs WHERE id=?', (job_id,)).fetchone()
    return json.loads(row[0]) if row else None


def listing(page=1):
    with connect() as db:
        total = db.execute('SELECT count(*) FROM jobs').fetchone()[0]
        rows = db.execute('SELECT body FROM jobs ORDER BY rowid DESC LIMIT 20 OFFSET ?', ((page-1)*20,)).fetchall()
    items = []
    for row in rows:
        item = json.loads(row[0])
        items.append({k:v for k,v in item.items() if k not in ('report','evidence','transcript','source_body','source')})
    return {'items': items, 'total':total, 'page':page}


def update(job_id, **values):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT body FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise ValueError('分析任务不存在')
        job = json.loads(row[0]); job.update(values, updated_at=now())
        db.execute('UPDATE jobs SET status=?,stage=?,body=? WHERE id=?', (job['status'],job['stage'],json.dumps(job,ensure_ascii=False),job_id))
    return job


def submit(payload, actor, source):
    data = payload.model_dump(exclude={'request_key'})
    digest = hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT fingerprint,body FROM jobs WHERE actor=? AND request_key=?', (actor,payload.request_key)).fetchone()
        if row:
            if row['fingerprint'] != digest:
                raise ValueError('重复请求键对应的分析参数不同')
            return json.loads(row['body'])
        if db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] >= 5:
            raise ValueError('分析队列已满（最多 5 个），请等待或取消任务')
        job = dict(id=uuid4().hex, actor=actor, status='queued', stage='queued', created_at=now(),updated_at=now(),
                   input=data, title=source['title'], source=source, error=None, report=None, attempts=1)
        db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?)',(job['id'],actor,payload.request_key,digest,job['status'],job['stage'],json.dumps(job,ensure_ascii=False)))
    return job


def requeue(job_id):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT body FROM jobs WHERE id=?',(job_id,)).fetchone()
        if not row:
            raise ValueError('分析任务不存在')
        job=json.loads(row[0])
        if job['status'] not in ('failed','cancelled','interrupted'):
            raise ValueError('只能重试失败、取消或已中断的任务')
        if db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] >= 5:
            raise ValueError('分析队列已满')
        job.update(status='queued',error=None,updated_at=now(),attempts=job['attempts']+1)
        db.execute('UPDATE jobs SET status=?,body=? WHERE id=?',('queued',json.dumps(job,ensure_ascii=False),job_id))
    return job


def recover():
    with connect() as db:
        for row in db.execute("SELECT id,body FROM jobs WHERE status IN ('queued','running')").fetchall():
            job=json.loads(row['body']);job.update(status='interrupted',error='服务重启，分析已中断。可手动从检查点重试。',updated_at=now())
            db.execute('UPDATE jobs SET status=?,body=? WHERE id=?',('interrupted',json.dumps(job,ensure_ascii=False),row['id']))


def upload_path(item, partial=False):
    suffix = Path(item['filename']).suffix.lower()
    return root()/'uploads'/item['id']/('source'+suffix+('.part' if partial else ''))


def upload(upload_id):
    with connect() as db:
        row=db.execute('SELECT * FROM uploads WHERE id=?',(upload_id,)).fetchone()
    return dict(row) if row else None


def create_upload(payload, actor):
    if Path(payload.filename).suffix.lower() not in {'.mp4','.mov','.webm','.m4v'}:
        raise ValueError('支持 MP4、MOV、WebM、M4V 视频')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM uploads WHERE actor=? AND request_key=?',(actor,payload.request_key)).fetchone()
        if row:
            if row['filename'] != payload.filename or row['size'] != payload.size:
                raise ValueError('重复上传请求的文件信息不同')
            return dict(row)
        if db.execute('SELECT count(*) FROM uploads WHERE sha256 IS NULL').fetchone()[0]>=10:
            raise ValueError('最多保留 10 个未完成上传，请先完成上传或在本机清理无用分块')
        if shutil.disk_usage(root()).free < payload.size+100*1024*1024:
            raise ValueError('磁盘空间不足，请先释放本机空间')
        item=dict(id=uuid4().hex,actor=actor,request_key=payload.request_key,filename=payload.filename,size=payload.size,received=0,sha256=None,created_at=now())
        db.execute('INSERT INTO uploads VALUES(:id,:actor,:request_key,:filename,:size,:received,:sha256,:created_at)',item)
    return item


def write_chunk(upload_id, offset, body):
    if not body or len(body)>CHUNK_SIZE:
        raise ValueError('每个上传分块需为 1 字节至 4 MB')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM uploads WHERE id=?',(upload_id,)).fetchone()
        if not row:
            raise ValueError('上传不存在')
        item=dict(row)
        if offset+len(body)>item['size']:
            raise ValueError('上传内容超过声明大小')
        path=upload_path(item)
        if offset < item['received']:
            with path.open('rb') as file:
                file.seek(offset)
                if offset+len(body)<=item['received'] and file.read(len(body))==body:
                    return item
            raise ValueError('重复分块内容不一致')
        if offset != item['received'] or item['sha256']:
            raise ValueError('上传偏移不一致，请重新读取上传进度')
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('r+b' if path.exists() else 'w+b') as file:
            file.truncate(offset);file.seek(offset);file.write(body)
        item['received'] += len(body)
        if item['received']==item['size']:
            with path.open('rb') as file:
                item['sha256']=hashlib.file_digest(file,'sha256').hexdigest()
        db.execute('UPDATE uploads SET received=?,sha256=? WHERE id=?',(item['received'],item['sha256'],upload_id))
    return item


def cached_report(key):
    with connect() as db:
        row=db.execute('SELECT body FROM model_cache WHERE key=?',(key,)).fetchone()
    return json.loads(row[0]) if row else None


def cache_report(key, report, model):
    with connect() as db:
        db.execute('INSERT OR REPLACE INTO model_cache VALUES(?,?)',(key,json.dumps(dict(report=report,model=model),ensure_ascii=False)))
