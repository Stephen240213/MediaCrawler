"""Built-in creation agents and versioned skills, with frozen source provenance."""
import json
from . import store, context, providers, research
from api.services import analytics

AGENTS = [
    dict(id='content_writer', name='内容创作 Agent', description='根据来源和创作需求生成图文草稿', instruction='以图文阅读场景组织完整、可编辑的正文。'),
    dict(id='script_writer', name='视频脚本 Agent', description='生成口播开头、分段脚本与画面建议', instruction='以短视频口播场景创作，提供 hook 和分段 script；视觉仅作为待拍摄建议。'),
]
SKILLS = [
    dict(id='answer', name='需求解答', version=1, instruction='先给直接答案，再用来源细节解释使用条件和下一步。'),
    dict(id='compare', name='决策对比', version=1, instruction='按相同维度比较选项，区分已知、未知与适合人群，不混用不同案例的事实。'),
    dict(id='story', name='经历叙事', version=1, instruction='按场景、问题、过程、结果组织来源记录的经历；不得虚构第一人称亲历。'),
    dict(id='curate', name='信息整理', version=1, instruction='提炼关键条件、亮点、限制和待核实事项，数据需保留样本范围和时间。'),
]


def catalog():
    return dict(agents=AGENTS, skills=SKILLS, model=providers.status())


def source_for(payload):
    provenance = dict(kind=payload.source)
    title = payload.title.strip()
    reference_bundle = None
    dataset_snapshot = None
    if payload.source == 'manual':
        body = payload.material.strip()
        title = title or '手填材料创作'
        provenance['note'] = '用户提供材料，未经独立核实'
    elif payload.source == 'report':
        job = store.get(payload.reference_id)
        if not job or job['status'] != 'completed' or not job.get('report'):
            raise ValueError('请选择已完成的分析报告')
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
    agent = next(a for a in AGENTS if a['id'] == payload.agent_id)
    skills = [s.copy() for s in SKILLS if s['id'] in payload.skill_ids]
    if agent['id'] == 'script_writer' and payload.brief.content_type != '视频':
        raise ValueError('视频脚本 Agent 需要选择视频内容类型')
    source = context.snapshot(dict(kind=payload.source, title=title[:200], body=body[:18000], original_body_chars=len(body), truncated=len(body)>18000,
        url=None, video_available=False, provenance=provenance, execution=dict(agent=agent.copy(), skills=skills)), payload)
    if dataset_snapshot is not None:
        source['dataset_snapshot']=dataset_snapshot
    if reference_bundle is not None:
        source['reference_bundle'] = reference_bundle
    return source
