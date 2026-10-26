"""Operator-only mutations inherit the existing boundary's JWT/CSRF/audit checks."""
import asyncio
import json
import re
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse, RedirectResponse

from api.services import analytics, workbench_store
from api.services.remix import providers, store, workflow, knowledge, source_links, profiles, publications, production, projects, research
from api.services.remix.research_schemas import ResearchSpace, ResearchRun, AssetTags
from api.services.remix.schemas import JobInput, UploadInput, KnowledgeInput, VersionInput, ProfileInput, PublicationInput, ObservationInput, ProductionInput, ProjectInput, ProjectLink, EditorSnapshot, AgentDefinition, SkillDefinition

router=APIRouter(prefix='/remix',tags=['ContentRemixAgent'])


@router.get('/creation/catalog')
def creation_catalog():
    from api.services.remix.creation import catalog
    return catalog()


@router.get('/creation/registry')
def creation_registry():
    from api.services.remix import registry
    return dict(agents=registry.listing('agent'),skills=registry.listing('skill'))


def save_definition(kind,payload,expected_id=None):
    from api.services.remix import registry
    try:return registry.save(kind,payload,expected_id)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.post('/creation/agents')
def create_agent(payload: AgentDefinition):return save_definition('agent',payload)


@router.put('/creation/agents/{definition_id}')
def update_agent(definition_id: str,payload: AgentDefinition):return save_definition('agent',payload,definition_id)


@router.post('/creation/skills')
def create_skill(payload: SkillDefinition):return save_definition('skill',payload)


@router.put('/creation/skills/{definition_id}')
def update_skill(definition_id: str,payload: SkillDefinition):return save_definition('skill',payload,definition_id)


@router.get('/creation/definitions/{kind}/{definition_id}/versions')
def definition_versions(kind: Literal['agent','skill'],definition_id: str):
    from api.services.remix import registry
    return registry.history(kind,definition_id)


@router.get('/research/discovery')
def research_discovery(search: str=Query('',max_length=200)):
    try:return research.discovery(search)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.get('/research/source-choices')
def research_source_choices(search: str=Query('',max_length=200),keyword: str=Query('',max_length=200),task_id: str='',page: int=Query(1,ge=1)):
    from api.services import collection_batches
    try:return collection_batches.choices(search,keyword,task_id,page)
    except ValueError as exc:raise HTTPException(404,str(exc)) from exc


@router.get('/research/spaces')
def research_spaces():return research.listing()


@router.post('/research/preview-statistics')
def research_statistics(payload: ResearchSpace):
    """Local calculations only; no saved scope or model consent is required."""
    try:return research.preview_scope(payload)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc


@router.post('/research/spaces')
def research_create(payload: ResearchSpace,request: Request):
    try:return research.save(payload,actor(request))
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.get('/research/spaces/{space_id}')
def research_detail(space_id: str):
    try:return research.detail(identifier(space_id))
    except ValueError as exc:raise HTTPException(404,str(exc)) from exc


@router.put('/research/spaces/{space_id}')
def research_update(space_id: str,payload: ResearchSpace,request: Request):
    try:return research.save(payload,actor(request),identifier(space_id))
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.get('/research/spaces/{space_id}/plan')
def research_plan(space_id: str):
    try:return research.plan(identifier(space_id))
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.post('/research/spaces/{space_id}/runs')
def research_submit(space_id: str,payload: ResearchRun,request: Request):
    try:return research.submit(identifier(space_id),payload,actor(request))
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.get('/research/runs/{run_id}')
def research_run(run_id: str):
    value=research.run_get(identifier(run_id))
    if not value:raise HTTPException(404,'批次不存在')
    return research.public_run(value)


@router.post('/research/runs/{run_id}/cancel')
async def research_cancel(run_id: str):
    try:
        await workflow.manager.cancel_research(identifier(run_id))
        return research.public_run(research.run_get(run_id))
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.get('/research/revisions/{revision_id}')
def research_revision(revision_id: str):
    value=research.revision(identifier(revision_id))
    if not value:raise HTTPException(404,'研究版本不存在')
    return value


@router.get('/research/spaces/{space_id}/assets')
def research_assets(space_id: str,search: str=Query('',max_length=200),category: str=Query('',max_length=60),page: int=Query(1,ge=1)):
    items=research.assets(identifier(space_id),search,category)
    return dict(items=items[(page-1)*20:page*20],total=len(items),page=page)


@router.put('/research/spaces/{space_id}/assets/{asset_id}/tags')
def research_asset_tags(space_id: str,asset_id: str,payload: AssetTags):
    if not re.fullmatch(r'[a-f0-9]{64}',asset_id):raise HTTPException(404,'资产不存在')
    try:return research.tag_asset(identifier(space_id),asset_id,payload)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.get('/projects')
def list_projects(archived: bool=False):
    return projects.listing(archived)


@router.post('/projects')
def create_project(payload: ProjectInput,request: Request):
    try:return projects.save(payload,actor(request))
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.get('/projects/{project_id}')
def project_detail(project_id: str):
    value=projects.detail(identifier(project_id))
    if not value:raise HTTPException(404,'项目不存在')
    return value


@router.put('/projects/{project_id}')
def update_project(project_id: str,payload: ProjectInput,request: Request):
    try:return projects.save(payload,actor(request),identifier(project_id))
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.post('/projects/{project_id}/links')
def link_project(project_id: str,payload: ProjectLink):
    try:return projects.link(identifier(project_id),payload)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.post('/projects/{project_id}/unlink')
def unlink_project(project_id: str,payload: ProjectLink):
    try:return projects.unlink(identifier(project_id),payload)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.get('/editors/{kind}/{resource_id}')
def editor_snapshot(kind: str,resource_id: str,request: Request):
    if kind not in ('project','rewrite','production','draft','research'):raise HTTPException(404,'编辑器不存在')
    try:return projects.snapshot(actor(request),kind,resource_id)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.put('/editors/{kind}/{resource_id}')
def save_editor_snapshot(kind: str,resource_id: str,payload: EditorSnapshot,request: Request):
    if kind not in ('project','rewrite','production','draft','research'):raise HTTPException(404,'编辑器不存在')
    try:return projects.save_snapshot(actor(request),kind,resource_id,payload)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.delete('/editors/{kind}/{resource_id}')
def clear_editor_snapshot(kind: str,resource_id: str,request: Request,revision: int=Query(ge=0)):
    if kind not in ('project','rewrite','production','draft','research'):raise HTTPException(404,'编辑器不存在')
    try:return projects.clear_snapshot(actor(request),kind,resource_id,revision)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


def identifier(value):
    if not re.fullmatch(r'[a-f0-9]{32}',value):
        raise HTTPException(404,'记录不存在')
    return value


def job_or_404(job_id):
    job=store.get(identifier(job_id))
    if not job:
        raise HTTPException(404,'分析任务不存在')
    path=store.root()/'jobs'/job_id/'rejected-report.json'
    job['stored_response_available']=path.is_file()
    if job['status']=='failed' and path.is_file() and not job.get('artifacts'):
        from api.services.remix.artifacts import manifest
        from api.services.remix.revalidation import evidence_hash
        try:
            saved=json.loads(path.read_text())
            if saved.get('evidence_sha256')==evidence_hash(job.get('evidence',[])):
                job['artifacts']=manifest(saved['candidate'],job.get('evidence',[]))
        except (ValueError,OSError):pass
    return job


def actor(request):
    return getattr(request.state,'identity',{}).get('sub','local')


@router.get('/config')
def configuration():
    return providers.status()


@router.get('/sources')
def sources(page: int=Query(1,ge=1), search: str=Query('',max_length=200)):
    with analytics.read_db() as (db,_):
        where="platform='xhs' AND (instr(coalesce(title,''),?)>0 OR content_id=?)"
        total=db.execute('SELECT count(*) FROM analytics_content WHERE '+where,(search,search)).fetchone()[0]
        rows=db.execute("SELECT content_id,title,author,content_type,(SELECT count(*) FROM analytics_comment m WHERE m.platform='xhs' AND m.content_id=analytics_content.content_id) AS saved_comments FROM analytics_content WHERE "+where+' ORDER BY collected_at DESC,content_id LIMIT 20 OFFSET ?',(search,search,(page-1)*20)).fetchall()
    return dict(total=total,items=[{**dict(row),'video_available':bool(workflow.local_video(row['content_id']))} for row in rows],page=page)


@router.post('/uploads')
def create_upload(payload: UploadInput, request: Request):
    try:
        item=store.create_upload(payload,actor(request))
        return {k:item[k] for k in ('id','filename','size','received','sha256')}
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc


@router.get('/uploads/{upload_id}')
def upload_progress(upload_id: str):
    item=store.upload(identifier(upload_id))
    if not item:
        raise HTTPException(404,'上传不存在')
    return {k:item[k] for k in ('id','filename','size','received','sha256')}


@router.put('/uploads/{upload_id}')
async def upload_chunk(upload_id: str, request: Request, offset: int=Query(ge=0)):
    identifier(upload_id)
    body=bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body)>store.CHUNK_SIZE:
            raise HTTPException(413,'单个上传分块不能超过 4 MB')
    try:
        item=await asyncio.to_thread(store.write_chunk,upload_id,offset,bytes(body))
        return {k:item[k] for k in ('id','filename','size','received','sha256')}
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc


@router.get('/jobs')
def jobs(page: int=Query(1,ge=1),search: str=Query('',max_length=200),status: str=Query('',pattern=r'^(|queued|running|completed|failed|interrupted|cancelled)$'),task_kind: str=Query('',pattern=r'^(|analysis|writing|legacy)$'),parent_job_id: str=Query('',pattern=r'^([a-f0-9]{32})?$')):
    return store.listing(page,search,status,task_kind,parent_job_id)


@router.post('/jobs')
def submit(payload: JobInput, request: Request):
    if not payload.consent:
        raise HTTPException(422,'请确认正文、转写、所选评论、创作需求和适用写作经验会发送给配置的模型服务；云端转写还会发送音频')
    if payload.source=='upload' and not payload.analyze_video:
        raise HTTPException(422,'上传视频需选择视频转写分析')
    try:
        existing=store.replay(payload,actor(request))
        if existing:return existing
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc
    config=providers.status()
    if not config['model_ready']:
        raise HTTPException(422,'分析模型未配置，请查看配置说明')
    if payload.analyze_video and not (config['ffmpeg_ready'] and config['asr_ready']):
        raise HTTPException(422,'FFmpeg 或转写服务未就绪，请查看配置说明')
    try:
        source=workflow.source_for(payload)
        if payload.analyze_video and not source['video_available']:
            raise ValueError('所选内容没有已下载的本机视频，请上传视频或选择仅正文分析')
        return store.submit(payload,actor(request),source)
    except ValueError as exc:
        from api.services.remix.registry import SelectionError
        if isinstance(exc,SelectionError):raise HTTPException(422,str(exc)) from exc
        raise HTTPException(409,str(exc)) from exc


@router.get('/jobs/{job_id}')
def get_job(job_id: str):
    return job_or_404(job_id)


@router.get('/jobs/{job_id}/relations')
def job_relations(job_id: str,page: int=Query(1,ge=1)):
    from api.services.remix import task_contracts
    job=job_or_404(job_id);parent=store.get(job.get('parent_job_id')) if job.get('parent_job_id') else None
    dependency=job.get('source',{}).get('analysis_dependency')
    return dict(parent=({k:parent.get(k) for k in ('id','title','status','task_kind')} if parent else None),
        dependency=dependency,snapshot_matches_current=(task_contracts.result_hash(parent)==dependency['result_sha256'] if parent and dependency else None),
        children=store.listing(page=page,parent_job_id=job_id))


@router.post('/jobs/{job_id}/revalidate')
def revalidate_saved_response(job_id: str):
    from api.services.remix.revalidation import restore
    job_or_404(job_id)
    try:return restore(identifier(job_id))
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.get('/jobs/{job_id}/draft-candidates')
def draft_candidates(job_id: str):
    from api.services.remix.artifacts import draft_candidates
    job=job_or_404(job_id)
    path=store.root()/'jobs'/identifier(job_id)/'rejected-report.json'
    if not path.is_file():return {'items':[]}
    from api.services.remix.revalidation import evidence_hash
    saved=json.loads(path.read_text())
    if saved.get('evidence_sha256')!=evidence_hash(job.get('evidence',[])):raise HTTPException(409,'已保存产物与当前来源版本不一致')
    candidate=saved['candidate']
    return {'items':draft_candidates(candidate,job.get('evidence',[])), 'review_required':True}


@router.get('/jobs/{job_id}/source-records')
def source_records(job_id: str,kind: str=Query('',pattern=r'^(|analysis|content|comment|evidence)$'),page: int=Query(1,ge=1),record_id: str=Query('',max_length=200)):
    from api.services.remix.source_archive import records
    return records(job_or_404(job_id),kind,page,record_id)


@router.get('/jobs/{job_id}/source-archive.json')
def source_archive(job_id: str):
    from api.services.remix.source_archive import archive
    data=archive(job_or_404(job_id))
    return Response(json.dumps(data,ensure_ascii=False),media_type='application/json',headers={
        'Cache-Control':'no-store','Content-Disposition':f'attachment; filename="sources-{identifier(job_id)}.json"'})


@router.get('/jobs/{job_id}/rejected-report.json')
def rejected_report(job_id: str):
    job_or_404(job_id)
    path=store.root()/'jobs'/identifier(job_id)/'rejected-report.json'
    if not path.is_file():raise HTTPException(404,'此历史任务没有保存模型失败响应，无法复原具体字段错误')
    return Response(path.read_bytes(),media_type='application/json',headers={
        'Cache-Control':'no-store','Content-Disposition':f'attachment; filename="rejected-{identifier(job_id)}.json"'})


@router.get('/jobs/{job_id}/source-link')
def original_source_link(job_id: str):
    job=job_or_404(job_id)
    target=source_links.original_url(job)
    if not target:
        raise HTTPException(404,'本任务没有原始小红书笔记链接')
    return RedirectResponse(target,status_code=302,headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})


@router.post('/jobs/{job_id}/cancel')
async def cancel(job_id: str):
    job_or_404(job_id)
    try:
        await workflow.manager.cancel(job_id)
        return store.get(job_id)
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc


@router.post('/jobs/{job_id}/retry')
def retry(job_id: str):
    job=job_or_404(job_id)
    if job.get('source',{}).get('kind')=='analytics' and not job['source'].get('dataset_snapshot'):
        raise HTTPException(409,'此历史任务只冻结了统计摘要，缺少原始正文与评论。请按原筛选重新创作并保存完整来源；未调用模型。')
    try:
        if not providers.status()['model_ready']:
            raise ValueError('分析模型未配置，请先更新本机配置')
        return store.requeue(job_id)
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc


@router.get('/jobs/{job_id}/report.md')
def export_report(job_id: str):
    job=job_or_404(job_id)
    try:
        body=workflow.markdown(job)
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc
    return Response(body,media_type='text/markdown; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="ContentRemix-{job_id}.md"'})


@router.get('/jobs/{job_id}/video')
def video(job_id: str, request: Request):
    job=job_or_404(job_id)
    owner=job.get('source',{}).get('media_owner_id') or job_id
    if not job['input']['analyze_video'] and owner==job_id:
        raise HTTPException(404,'本任务没有视频')
    identifier(owner)
    folder=store.root()/'jobs'/owner
    path=next((p for p in folder.glob('video.*') if p.suffix in {'.mp4','.mov','.webm','.m4v'} and p.is_file() and not p.is_symlink()),None)
    if not path or not path.resolve().is_relative_to(folder.resolve()):
        raise HTTPException(404,'视频尚未准备完成')
    size=path.stat().st_size;start=0;end=size-1;code=200
    headers={'Accept-Ranges':'bytes','Cache-Control':'no-store'}
    requested=request.headers.get('range')
    if requested:
        match=re.fullmatch(r'bytes=(\d*)-(\d*)',requested)
        if not match or not any(match.groups()):
            raise HTTPException(416,'无效的视频范围',headers={'Content-Range':f'bytes */{size}'})
        if not match[1]:
            length=int(match[2]);start=max(0,size-length)
            if not length:
                raise HTTPException(416,'无效的视频范围')
        else:
            start=int(match[1]);end=min(int(match[2]),size-1) if match[2] else size-1
        if start>=size or start>end:
            raise HTTPException(416,'视频范围越界',headers={'Content-Range':f'bytes */{size}'})
        code=206;headers['Content-Range']=f'bytes {start}-{end}/{size}'
    headers['Content-Length']=str(end-start+1)
    def chunks():
        with path.open('rb') as file:
            file.seek(start);remaining=end-start+1
            while remaining>0:
                data=file.read(min(1024*1024,remaining))
                if not data:
                    break
                remaining-=len(data);yield data
    mime='video/webm' if path.suffix=='.webm' else 'video/quicktime' if path.suffix=='.mov' else 'video/mp4'
    return StreamingResponse(chunks(),status_code=code,media_type=mime,headers=headers)


@router.post('/jobs/{job_id}/draft')
def save_draft(job_id: str, idea: int=Query(0,ge=0,le=4)):
    job=job_or_404(job_id)
    if not job.get('report') or idea>=len(job['report']['ideas']):
        raise HTTPException(409,'创作提纲尚未生成')
    item=job['report']['ideas'][idea]
    # Stable ID makes repeated clicks idempotent; never overwrite edited drafts.
    draft_id=f"remix-{job_id}-{idea}"
    with workbench_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute("SELECT body FROM documents WHERE kind='draft' AND id=?",(draft_id,)).fetchone()
        if row:
            return json.loads(row[0])
        draft=dict(id=draft_id,title=item['title'],content='创作提纲（AI 建议，需人工核实与原创表达）\n\n'+'\n'.join(item['outline']),
                   tags=[],images=[],platform='xhs',version=1,updated_at=store.now())
        db.execute('INSERT INTO documents VALUES (?,?,?)',('draft',draft_id,json.dumps(draft,ensure_ascii=False)))
    return draft


@router.get('/knowledge')
def list_knowledge(page: int=Query(1,ge=1)):
    return knowledge.listing(page)


@router.post('/knowledge')
def create_knowledge(payload: KnowledgeInput, request: Request):
    try:
        return knowledge.save(payload,actor(request))
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc


@router.put('/knowledge/{entry_id}')
def update_knowledge(entry_id: str, payload: KnowledgeInput, request: Request):
    try:
        return knowledge.save(payload,actor(request),identifier(entry_id))
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc


@router.get('/knowledge/{entry_id}/history')
def knowledge_history(entry_id: str):
    return knowledge.history(identifier(entry_id))


@router.delete('/knowledge/{entry_id}')
def delete_knowledge(entry_id: str):
    try:
        knowledge.remove(identifier(entry_id))
        return {'deleted':True}
    except ValueError as exc:
        raise HTTPException(404,str(exc)) from exc


@router.get('/jobs/{job_id}/versions')
def list_versions(job_id: str):
    job_or_404(job_id)
    return store.versions(job_id)


@router.post('/jobs/{job_id}/versions')
def save_version(job_id: str, payload: VersionInput, request: Request):
    job_or_404(job_id)
    try:
        return store.save_version(job_id,payload,actor(request))
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc


@router.post('/jobs/{job_id}/versions/{version_id}/adopt')
def adopt_version(job_id: str, version_id: str):
    job=job_or_404(job_id)
    if job['status']!='completed':
        raise HTTPException(409,'任务尚未完成')
    try:
        version=store.adopt_version(job_id,identifier(version_id))
    except ValueError as exc:
        raise HTTPException(404,str(exc)) from exc
    draft_id='remix-version-'+version['id']
    with workbench_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute("SELECT body FROM documents WHERE kind='draft' AND id=?",(draft_id,)).fetchone()
        if row:
            return dict(version=version,draft=json.loads(row[0]))
        platform=(job.get('source',{}).get('creative_brief') or job['input'].get('brief',{})).get('platform','xhs')
        draft=dict(id=draft_id,title=version['title'],content=version['content'],tags=version['tags'],
                   images=[],platform=platform,version=1,updated_at=store.now())
        db.execute('INSERT INTO documents VALUES(?,?,?)',('draft',draft_id,json.dumps(draft,ensure_ascii=False)))
    return dict(version=version,draft=draft)


@router.get('/profiles')
def list_profiles():
    return profiles.listing()


@router.post('/profiles')
def create_profile(payload: ProfileInput, request: Request):
    try:return profiles.save(payload,actor(request))
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.put('/profiles/{profile_id}')
def update_profile(profile_id: str, payload: ProfileInput, request: Request):
    try:return profiles.save(payload,actor(request),identifier(profile_id))
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.get('/profiles/{profile_id}/history')
def profile_history(profile_id: str):
    return profiles.history(identifier(profile_id))


@router.get('/jobs/{job_id}/comment-topics')
def comment_topics(job_id: str):
    job=job_or_404(job_id)
    from api.services.remix import topics
    if job.get('comment_topics',{}).get('rule_version')==topics.RULE_VERSION:return job['comment_topics']
    return {**topics.build(job.get('evidence',[])),'historical_recalculation':True}


@router.get('/jobs/{job_id}/publications')
def job_publications(job_id: str):
    job_or_404(job_id)
    return publications.listing(job_id)


@router.post('/jobs/{job_id}/publications')
def create_publication(job_id: str, payload: PublicationInput, request: Request):
    job_or_404(job_id)
    try:return publications.save(job_id,payload,actor(request))
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.put('/publications/{publication_id}')
def update_publication(publication_id: str, payload: PublicationInput, request: Request):
    record=publications.get(identifier(publication_id))
    if not record:raise HTTPException(404,'发布记录不存在')
    try:return publications.save(record['job_id'],payload,actor(request),publication_id)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.get('/publications/{publication_id}/observations')
def publication_observations(publication_id: str):
    if not publications.get(identifier(publication_id)):raise HTTPException(404,'发布记录不存在')
    return publications.observations(publication_id)


@router.post('/publications/{publication_id}/observations')
def add_observation(publication_id: str, payload: ObservationInput, request: Request):
    try:return publications.observe(identifier(publication_id),payload,actor(request))
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.get('/publications/{publication_id}/review')
def publication_review(publication_id: str, metric: str=Query('favorites',max_length=30), window_hours: int=Query(72,ge=1,le=2160), tolerance_hours: int=Query(24,ge=0,le=168)):
    try:return publications.review(identifier(publication_id),metric,window_hours,tolerance_hours)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.get('/production/config')
def production_configuration():
    return production.status()


@router.get('/jobs/{job_id}/production-seed')
def production_seed(job_id: str, version_id: str=Query(pattern=r'^[a-f0-9]{32}$')):
    job_or_404(job_id)
    try:return production.seed(job_id,version_id)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.get('/jobs/{job_id}/original-subtitles')
def original_subtitles(job_id: str):
    job_or_404(job_id)
    try:return production.original_subtitles(job_id)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.get('/jobs/{job_id}/productions')
def production_versions(job_id: str):
    job_or_404(job_id)
    return production.listing(job_id)


@router.post('/jobs/{job_id}/productions')
def create_production(job_id: str, payload: ProductionInput, request: Request):
    job_or_404(job_id)
    try:return production.save(job_id,payload,actor(request))
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


def production_or_404(revision_id):
    revision=production.get(identifier(revision_id))
    if not revision:raise HTTPException(404,'制作版本不存在')
    return revision


@router.get('/productions/{revision_id}')
def production_detail(revision_id: str):
    return production_or_404(revision_id)


@router.get('/productions/{revision_id}/assets/{name}')
def production_asset(revision_id: str, name: str):
    revision=production_or_404(revision_id)
    try:body=production.asset(revision,name)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc
    mime='image/png' if name.endswith('.png') else 'text/vtt; charset=utf-8' if name.endswith('.vtt') else 'application/json' if name.endswith('.json') else 'text/plain; charset=utf-8'
    return Response(body,media_type=mime,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
        'Content-Disposition':f'inline; filename="{name}"'})


@router.get('/productions/{revision_id}/manual-package.zip')
def manual_package(revision_id: str):
    revision=production_or_404(revision_id)
    try:body=production.bundle(revision)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc
    return Response(body,media_type='application/zip',headers={'Cache-Control':'no-store',
        'Content-Disposition':f'attachment; filename="ContentRemix-manual-{revision_id}.zip"'})
