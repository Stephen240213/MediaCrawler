"""Local workbench documents and task history. No platform credentials are stored."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / '.runtime' / 'workbench.sqlite3'


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
