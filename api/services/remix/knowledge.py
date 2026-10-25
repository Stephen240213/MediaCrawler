"""User-confirmed writing preferences, with explicit revisions and bounded retrieval."""
import json
from uuid import uuid4

from . import store


def listing(page=1):
    with store.connect() as db:
        total = db.execute('SELECT count(*) FROM writing_knowledge').fetchone()[0]
        rows = db.execute('SELECT body FROM writing_knowledge ORDER BY updated_at DESC,id LIMIT 20 OFFSET ?', ((page-1)*20,)).fetchall()
    return dict(items=[json.loads(r[0]) for r in rows], total=total, page=page)


def save(payload, actor, entry_id=None):
    data = payload.model_dump()
    if not data['title'].strip() or not data['content'].strip():
        raise ValueError('经验标题和内容不能为空')
    if data['source_version_id'] and not data['source_job_id']:
        raise ValueError('修改经验需要关联来源任务')
    if data['source_job_id']:
        job = store.get(data['source_job_id'])
        if not job or not job.get('report'):
            raise ValueError('经验来源任务不存在或尚未完成')
        if data['source_version_id'] and not any(v['id']==data['source_version_id'] for v in store.versions(job['id'])):
            raise ValueError('经验来源版本不存在')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if entry_id:
            row = db.execute('SELECT body FROM writing_knowledge WHERE id=?', (entry_id,)).fetchone()
            if not row:
                raise ValueError('经验不存在')
            previous = json.loads(row[0])
            if data['version'] != previous['version']:
                raise ValueError('经验已被修改，请刷新后重试')
            data.update(version=previous['version']+1, created_at=previous['created_at'])
        else:
            if db.execute('SELECT count(*) FROM writing_knowledge').fetchone()[0] >= 1000:
                raise ValueError('经验库最多保存 1000 条，请整理后再添加')
            entry_id = uuid4().hex
            data.update(version=1, created_at=store.now())
        data.update(id=entry_id, updated_at=store.now(), confirmed_by=actor)
        raw = json.dumps(data, ensure_ascii=False)
        db.execute('INSERT OR REPLACE INTO writing_knowledge VALUES(?,?,?)', (entry_id, data['updated_at'], raw))
        db.execute('INSERT INTO writing_knowledge_history VALUES(?,?,?)', (entry_id, data['version'], raw))
    return data


def remove(entry_id):
    with store.connect() as db:
        if not db.execute('DELETE FROM writing_knowledge WHERE id=?', (entry_id,)).rowcount:
            raise ValueError('经验不存在')
        db.execute('DELETE FROM writing_knowledge_history WHERE id=?', (entry_id,))


def history(entry_id):
    with store.connect() as db:
        rows = db.execute('SELECT body FROM writing_knowledge_history WHERE id=? ORDER BY version DESC LIMIT 50', (entry_id,)).fetchall()
    return [json.loads(row[0]) for row in rows]


def relevant(brief):
    """Exact platform and topic scoping; general preferences are also eligible."""
    with store.connect() as db:
        rows = db.execute('SELECT body FROM writing_knowledge ORDER BY updated_at DESC,id LIMIT 1000').fetchall()
    eligible = []
    topic = brief.get('topic', '').strip().casefold()
    for row in rows:
        item = json.loads(row[0])
        if not item['enabled'] or item['platform'] not in ('all', brief.get('platform', 'xhs')):
            continue
        scope = item['topic'].strip().casefold()
        if scope and scope != topic:
            continue
        eligible.append((bool(scope), item['platform']!='all', item))
    eligible.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [{k:item[k] for k in ('id', 'version', 'kind', 'title', 'content', 'platform', 'topic')}
            for _, _, item in eligible[:6]]
