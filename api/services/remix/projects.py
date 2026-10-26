"""Content project references and actor-scoped recovery drafts; no model or publishing calls."""
import json
from uuid import uuid4
from fastapi import HTTPException
from . import store, profiles
from api.services import workbench_store, analytics


def listing(archived=False):
    with store.connect() as db:
        rows=db.execute('SELECT body FROM content_projects ORDER BY updated_at DESC LIMIT 500').fetchall()
    return [p for row in rows if (p:=json.loads(row[0]))['archived']==archived]


def get(project_id):
    with store.connect() as db:row=db.execute('SELECT body FROM content_projects WHERE id=?',(project_id,)).fetchone()
    return json.loads(row[0]) if row else None


def save(payload,actor,project_id=None):
    data=payload.model_dump()
    if not data['title'].strip():raise ValueError('项目名称不能为空')
    if data['profile_id'] and not profiles.get(data['profile_id']):raise ValueError('账号画像不存在')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if project_id:
            row=db.execute('SELECT body FROM content_projects WHERE id=?',(project_id,)).fetchone()
            if not row:raise ValueError('项目不存在')
            old=json.loads(row[0])
            if data['version']!=old['version']:raise ValueError('项目已在其他页面更新，当前编辑保留，请重新读取后核对')
            data['created_at']=old['created_at']
        else:
            if data['version']!=0:raise ValueError('新项目版本应为0')
            if db.execute('SELECT count(*) FROM content_projects').fetchone()[0]>=500:raise ValueError('项目最多500个，请先整理已有项目')
            project_id=uuid4().hex;data['created_at']=store.now()
        data.update(id=project_id,title=data['title'].strip(),version=data['version']+1,updated_at=store.now(),actor=actor)
        db.execute('INSERT OR REPLACE INTO content_projects VALUES(?,?,?)',(project_id,data['updated_at'],json.dumps(data,ensure_ascii=False)))
    return data


def resolve(kind,resource_id):
    if kind=='job':
        item=store.get(resource_id)
        return dict(id=resource_id,title=item['title'],status=item['status']) if item else None
    if kind=='draft':
        item=workbench_store.get_document('draft',resource_id)
        return dict(id=resource_id,title=item['title'] or '未命名草稿',version=item['version']) if item else None
    if kind=='asset':
        from api.routers.workbench import media_path,asset_info
        try:return asset_info(media_path(resource_id))
        except Exception:return None
    if kind=='source':
        platform,_,content_id=resource_id.partition(':')
        if not platform or not content_id:return None
        try:
            with analytics.read_db() as (db,_):
                row=db.execute('SELECT title FROM analytics_content WHERE platform=? AND content_id=?',(platform,content_id)).fetchone()
        except HTTPException:return None
        return dict(id=resource_id,title=row['title'] or content_id,platform=platform) if row else None
    return None


def link(project_id,payload):
    if not resolve(payload.kind,payload.resource_id):raise ValueError('关联记录不存在或不可访问')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT body FROM content_projects WHERE id=?',(project_id,)).fetchone()
        if not row or json.loads(row[0])['archived']:raise ValueError('项目不存在或已归档')
        if not db.execute('SELECT 1 FROM project_links WHERE project_id=? AND kind=? AND resource_id=?',(project_id,payload.kind,payload.resource_id)).fetchone():
            if db.execute('SELECT count(*) FROM project_links WHERE project_id=?',(project_id,)).fetchone()[0]>=200:raise ValueError('单项目最多关联200条记录')
            db.execute('INSERT INTO project_links VALUES(?,?,?,?)',(project_id,payload.kind,payload.resource_id,store.now()))
    return detail(project_id)


def unlink(project_id,payload):
    with store.connect() as db:
        if not db.execute('SELECT 1 FROM content_projects WHERE id=?',(project_id,)).fetchone():raise ValueError('项目不存在')
        db.execute('DELETE FROM project_links WHERE project_id=? AND kind=? AND resource_id=?',(project_id,payload.kind,payload.resource_id))
    return detail(project_id)


def detail(project_id):
    project=get(project_id)
    if not project:return None
    with store.connect() as db:links=[dict(r) for r in db.execute('SELECT kind,resource_id,created_at FROM project_links WHERE project_id=? ORDER BY created_at',(project_id,))]
    result={**project,'links':[],'versions':[],'productions':[],'publications':[],'drafts':[]}
    from . import production,publications
    for item in links:
        resource=resolve(item['kind'],item['resource_id'])
        result['links'].append({**item,'resource':resource,'missing':resource is None})
        if item['kind']=='job' and resource:
            job_id=item['resource_id']
            result['versions'] += [{k:v[k] for k in ('id','job_id','title','adopted','created_at')} for v in store.versions(job_id)]
            result['productions'] += production.listing(job_id)
            result['publications'] += [{k:p.get(k,'') for k in ('id','job_id','version_id','production_id','title','url','published_at')} for p in publications.listing(job_id)]
            ids=['remix-'+job_id+'-'+str(i) for i in range(5)]+['remix-version-'+v['id'] for v in result['versions'] if v['job_id']==job_id]
            with workbench_store.connect() as db:
                rows=db.execute("SELECT body FROM documents WHERE kind='draft' AND id IN ("+','.join('?' for _ in ids)+')',ids).fetchall()
            for row in rows:
                d=json.loads(row[0]);result['drafts'].append(dict(id=d['id'],title=d['title'],version=d['version']))
    result['stage']='已发布' if result['publications'] else '已有制作版本' if result['productions'] else '已有成稿' if result['versions'] else '已有草稿' if result['drafts'] or any(l['kind']=='draft' and not l['missing'] for l in result['links']) else '资料准备'
    return result


def editor_resource(kind,resource_id):
    if kind in ('rewrite','production') and not store.get(resource_id):raise ValueError('分析任务不存在')
    if kind=='project' and resource_id!='new' and not get(resource_id):raise ValueError('项目不存在')
    if kind=='draft' and resource_id!='current':raise ValueError('恢复编辑范围无效')


def snapshot(actor,kind,resource_id):
    editor_resource(kind,resource_id)
    with store.connect() as db:row=db.execute('SELECT revision,body,updated_at FROM editor_snapshots WHERE actor=? AND kind=? AND resource_id=?',(actor,kind,resource_id)).fetchone()
    return dict(revision=row[0],data=json.loads(row[1]),updated_at=row[2]) if row else dict(revision=0,data=None,updated_at=None)


def save_snapshot(actor,kind,resource_id,payload):
    editor_resource(kind,resource_id)
    raw=json.dumps(payload.data,ensure_ascii=False,allow_nan=False)
    if len(raw.encode())>300000:raise ValueError('恢复草稿超过300KB，请精简编辑内容')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT revision FROM editor_snapshots WHERE actor=? AND kind=? AND resource_id=?',(actor,kind,resource_id)).fetchone()
        previous=row[0] if row else 0
        if previous!=payload.revision:raise ValueError('恢复草稿已在其他页面更新，当前编辑仍保留本机，请重新读取并核对')
        if not row and db.execute('SELECT count(*) FROM editor_snapshots WHERE actor=?',(actor,)).fetchone()[0]>=2000:raise ValueError('恢复草稿数量已达上限')
        stamp=store.now();revision=previous+1
        db.execute('INSERT OR REPLACE INTO editor_snapshots VALUES(?,?,?,?,?,?)',(actor,kind,resource_id,revision,raw,stamp))
    return dict(revision=revision,data=payload.data,updated_at=stamp)


def clear_snapshot(actor,kind,resource_id,revision):
    editor_resource(kind,resource_id)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT revision FROM editor_snapshots WHERE actor=? AND kind=? AND resource_id=?',(actor,kind,resource_id)).fetchone()
        previous=row[0] if row else 0
        if previous!=revision:raise ValueError('恢复草稿已更新，请重新读取后核对')
        if row:db.execute('UPDATE editor_snapshots SET revision=?,body=?,updated_at=? WHERE actor=? AND kind=? AND resource_id=?',(previous+1,'null',store.now(),actor,kind,resource_id))
    return dict(revision=previous+1 if row else 0,data=None)
