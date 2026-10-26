"""Built-in creation agents and versioned skills, with frozen source provenance."""
import json
from . import store, context, providers, research, registry, task_contracts
from api.services import analytics

AGENTS = registry.AGENTS
SKILLS = registry.SKILLS


def catalog():
    return dict(agents=[a for a in registry.listing('agent') if a['enabled']], skills=[s for s in registry.listing('skill') if s['enabled']], model=providers.status())


def source_for(payload):
    if payload.reuse_job_id:
        previous=store.get(payload.reuse_job_id)
        if payload.task_kind!='writing' or not previous or not previous.get('source',{}).get('execution'):
            raise ValueError('请选择已有写作任务复用来源')
        if payload.source!=previous['source']['kind']:
            raise ValueError('复用任务时不能同时更换来源类型')
        # Deep-copy the exact frozen package. Do not reopen live analytics or a parent.
        source=json.loads(json.dumps(previous['source']))
        source['execution']=registry.resolve(payload.agent_id,payload.skill_ids,payload.brief.content_type,payload.agent_version,payload.skill_versions)
        if payload.title.strip():source['title']=payload.title.strip()
        return context.snapshot(source,payload)
    provenance = dict(kind=payload.source)
    title = payload.title.strip()
    reference_bundle = None
    dataset_snapshot = None
    dependency = None
    media_owner = None
    inherited_transcript = []
    inherited_duration = None
    original_url = None
    if payload.source == 'manual':
        body = payload.material.strip()
        title = title or '手填材料创作'
        provenance['note'] = '用户提供材料，未经独立核实'
    elif payload.source == 'report':
        job = store.get(payload.reference_id)
        if not job or job['status'] != 'completed' or not job.get('report'):
            raise ValueError('请选择已完成的分析报告')
        dependency=dict(job_id=job['id'],result_sha256=task_contracts.result_hash(job),kind=task_contracts.kind(job),title=job['title'])
        original_url=job.get('source',{}).get('url')
        inherited_transcript=job.get('transcript') or []
        inherited_duration=job.get('duration')
        media_owner=job['source'].get('media_owner_id') or (job['id'] if job['input'].get('analyze_video') else None)
        reference_bundle = dict(report=job['report'], evidence=job.get('evidence', []), source_kind=job.get('source',{}).get('kind','unknown'), source_data=job.get('source',{}),transcript=job.get('transcript',[]))
        body = json.dumps(reference_bundle, ensure_ascii=False)
        title = title or job['title']+' · 创作'
        provenance.update(job_id=job['id'], updated_at=job['updated_at'], note='模型分析包含推断，引用原文保留在报告中')
    elif payload.source == 'research':
        rev = research.revision(payload.reference_id)
        if not rev or rev['status'] not in ('completed', 'partial'):
            raise ValueError('请选择已生成的研究版本')
        reference_bundle = dict(materials=rev.get('materials', []), assets=rev.get('assets', []), evidence=rev.get('evidence', []))
        body = json.dumps(reference_bundle, ensure_ascii=False)
        title = title or rev['space']['name']+' · 创作'
        provenance.update(revision_id=rev['id'], note='研究候选解释与原文摘录，不是已核实事实')
    elif payload.source == 'analytics':
        allowed = {'platform', 'keyword', 'start', 'end', 'basis', 'source', 'task_id', 'content_id', 'author_id', 'content_type'}
        if set(payload.analytics_filters)-allowed or any(len(v)>500 for v in payload.analytics_filters.values()):
            raise ValueError('数据分析筛选参数无效')
        options = payload.analytics_filters
        from .source_archive import analytics_snapshot
        dataset_snapshot=analytics_snapshot(options)
        result=dataset_snapshot['analysis']
        body=json.dumps(dict(filters=options,generated_at=result['generated_at'],summary=result['summary'],charts=result['charts'],comment_analysis=result.get('comment_analysis'),note='完整原始记录与统计保存在任务来源包'),ensure_ascii=False)
        title = title or '数据洞察创作'
        provenance.update(filters=options, generated_at=result['generated_at'])
    else:
        raise ValueError('不支持的创作来源')
    if not body:
        raise ValueError('请填写创作材料')
    if payload.analyze_video:
        raise ValueError('此创作来源不支持视频转写，请在内容分析中上传视频')
    execution=registry.resolve(payload.agent_id,payload.skill_ids,payload.brief.content_type,payload.agent_version,payload.skill_versions)
    source = context.snapshot(dict(kind=payload.source, title=title[:200], body=body[:18000], original_body_chars=len(body), truncated=len(body)>18000,
        url=original_url, video_available=False, provenance=provenance, execution=execution), payload)
    if dependency is not None:
        source.update(analysis_dependency=dependency,reused_transcript=inherited_transcript,reused_duration=inherited_duration,media_owner_id=media_owner)
    if dataset_snapshot is not None:
        source['dataset_snapshot']=dataset_snapshot
    if reference_bundle is not None:
        source['reference_bundle'] = reference_bundle
    return source
