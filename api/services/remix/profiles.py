"""Versioned creator context; existing task snapshots never change."""
import json
from uuid import uuid4
from . import store


def listing():
    with store.connect() as db:
        rows=db.execute('SELECT body FROM creator_profiles ORDER BY rowid DESC LIMIT 200').fetchall()
    return [json.loads(r[0]) for r in rows]


def get(profile_id):
    with store.connect() as db:
        row=db.execute('SELECT body FROM creator_profiles WHERE id=?',(profile_id,)).fetchone()
    return json.loads(row[0]) if row else None


def save(payload,actor,profile_id=None):
    data=payload.model_dump()
    if not data['name'].strip():raise ValueError('画像名称不能为空')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if profile_id:
            row=db.execute('SELECT body FROM creator_profiles WHERE id=?',(profile_id,)).fetchone()
            if not row:raise ValueError('账号画像不存在')
            old=json.loads(row[0])
            if old['version']!=data['version']:raise ValueError('画像已更新，请刷新后重试')
            data.update(version=old['version']+1,created_at=old['created_at'])
        else:
            if db.execute('SELECT count(*) FROM creator_profiles').fetchone()[0]>=200:raise ValueError('画像数量已达上限')
            profile_id=uuid4().hex;data.update(version=1,created_at=store.now())
        data.update(id=profile_id,updated_at=store.now(),confirmed_by=actor)
        raw=json.dumps(data,ensure_ascii=False)
        db.execute('INSERT OR REPLACE INTO creator_profiles VALUES(?,?)',(profile_id,raw))
        db.execute('INSERT INTO creator_profile_history VALUES(?,?,?)',(profile_id,data['version'],raw))
    return data


def history(profile_id):
    with store.connect() as db:
        rows=db.execute('SELECT body FROM creator_profile_history WHERE id=? ORDER BY version DESC LIMIT 50',(profile_id,)).fetchall()
    return [json.loads(r[0]) for r in rows]


def snapshot(profile_id):
    if not profile_id:return None
    p=get(profile_id)
    if not p or not p['enabled']:raise ValueError('账号画像不存在或已停用')
    return {k:p[k] for k in ('id','version','name','platform','positioning','audience','tone','topic','avoid','principles')}
