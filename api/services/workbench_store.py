"""Local workbench documents and task history. No platform credentials are stored."""
import json
import sqlite3
import os
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.getenv('MC_RUNTIME_DIR', str(ROOT / '.runtime'))) / 'workbench.sqlite3'


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS documents (kind TEXT, id TEXT, body TEXT NOT NULL, PRIMARY KEY(kind,id))')
        with db:
            yield db
    finally:
        db.close()


def list_documents(kind):
    with connect() as db:
        rows = db.execute('SELECT body FROM documents WHERE kind=? ORDER BY rowid DESC', (kind,)).fetchall()
    return [json.loads(row[0]) for row in rows]


def save_document(kind, document):
    with connect() as db:
        db.execute('INSERT OR REPLACE INTO documents VALUES (?,?,?)', (kind, document['id'], json.dumps(document, ensure_ascii=False)))


def get_document(kind, document_id):
    with connect() as db:
        row = db.execute('SELECT body FROM documents WHERE kind=? AND id=?', (kind, document_id)).fetchone()
    return json.loads(row[0]) if row else None


def remote_schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS task_requests(actor TEXT, request_key TEXT, fingerprint TEXT NOT NULL, task_id TEXT NOT NULL, PRIMARY KEY(actor,request_key))')
    db.execute('CREATE TABLE IF NOT EXISTS task_logs(id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, timestamp TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS task_logs_task ON task_logs(task_id,id)')
    db.execute('CREATE TABLE IF NOT EXISTS audit_events(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT NOT NULL, identity TEXT NOT NULL, method TEXT NOT NULL, path TEXT NOT NULL, status INTEGER NOT NULL)')


def reserve_task(document, actor, request_key, fingerprint):
    """Atomic duplicate detection and single active task admission."""
    with connect() as db:
        remote_schema(db)
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute('SELECT fingerprint,task_id FROM task_requests WHERE actor=? AND request_key=?', (actor,request_key)).fetchone()
        if previous:
            if previous[0] != fingerprint:
                return 'conflict', None
            row=db.execute("SELECT body FROM documents WHERE kind='task' AND id=?",(previous[1],)).fetchone()
            return 'replay', json.loads(row[0]) if row else {'id':previous[1]}
        active=db.execute("SELECT body FROM documents WHERE kind='task' AND json_extract(body,'$.outcome')='running' LIMIT 1").fetchone()
        if active:
            return 'busy', json.loads(active[0])
        db.execute('INSERT INTO task_requests VALUES (?,?,?,?)',(actor,request_key,fingerprint,document['id']))
        db.execute('INSERT INTO documents VALUES (?,?,?)',('task',document['id'],json.dumps(document,ensure_ascii=False)))
        return 'new', document


def append_log(task_id, timestamp, level, message):
    with connect() as db:
        remote_schema(db)
        return db.execute('INSERT INTO task_logs(task_id,timestamp,level,message) VALUES (?,?,?,?)',(task_id,timestamp,level,message)).lastrowid


def task_logs(task_id, after=0, limit=500, before=None):
    with connect() as db:
        remote_schema(db)
        if before is not None:
            result=db.execute('SELECT id,timestamp,level,message,task_id FROM task_logs WHERE task_id=? AND id<? ORDER BY id DESC LIMIT ?', (task_id,before,limit)).fetchall()
            result.reverse()
        elif after:
            result=db.execute('SELECT id,timestamp,level,message,task_id FROM task_logs WHERE task_id=? AND id>? ORDER BY id LIMIT ?', (task_id,after,limit)).fetchall()
        else:
            result=db.execute('SELECT id,timestamp,level,message,task_id FROM task_logs WHERE task_id=? ORDER BY id DESC LIMIT ?', (task_id,limit)).fetchall()
            result.reverse()
    return [dict(zip(('id','timestamp','level','message','task_id'),r)) for r in result]


def recover_tasks(active_id=None):
    from datetime import datetime, timezone
    with connect() as db:
        for key,body in db.execute("SELECT id,body FROM documents WHERE kind='task'").fetchall():
            item=json.loads(body)
            if item.get('outcome')=='running' and key!=active_id:
                item.update(outcome='interrupted',finished_at=datetime.now(timezone.utc).isoformat(),exit_code=None)
                db.execute("UPDATE documents SET body=? WHERE kind='task' AND id=?",(json.dumps(item,ensure_ascii=False),key))


def audit(actor, identity, method, path, status):
    from datetime import datetime, timezone
    from .redaction import redact
    with connect() as db:
        remote_schema(db)
        return db.execute('INSERT INTO audit_events(at,actor,identity,method,path,status) VALUES (?,?,?,?,?,?)', (datetime.now(timezone.utc).isoformat(),actor,identity,method,redact(path)[:300],status)).lastrowid


def finish_audit(event_id, status):
    with connect() as db:
        db.execute('UPDATE audit_events SET status=? WHERE id=?',(status,event_id))


def delete_task(task_id):
    """Remove terminal history/logs atomically; retain replay tombstones and data."""
    with connect() as db:
        remote_schema(db)
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT body FROM documents WHERE kind='task' AND id=?", (task_id,)).fetchone()
        if not row:
            return False
        if json.loads(row[0]).get('outcome') == 'running':
            raise ValueError('运行中的任务不能删除，请先停止任务')
        db.execute("DELETE FROM documents WHERE kind='task' AND id=?", (task_id,))
        db.execute('DELETE FROM task_logs WHERE task_id=?', (task_id,))
        return True
