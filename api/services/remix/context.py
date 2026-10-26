"""Frozen, bounded source and comment evidence; all reads stay on the collection Mac."""
import json

from . import knowledge, profiles, topics

MAX_COMMENTS = 80
MAX_COMMENT_CHARS = 8000


def saved_comments(db, content_id):
    total = db.execute("SELECT count(*) FROM analytics_comment WHERE platform='xhs' AND content_id=?", (content_id,)).fetchone()[0]
    rows = db.execute("SELECT comment_id,body,published_at FROM analytics_comment WHERE platform='xhs' AND content_id=? ORDER BY published_at DESC,comment_id LIMIT 160", (content_id,)).fetchall()
    return total, [dict(row) for row in rows]


def snapshot(source, payload, comments=(), saved_total=0):
    brief = payload.brief.model_dump()
    profile=profiles.snapshot(payload.profile_id)
    if profile:
        for key in ('audience','tone','topic','avoid'):
            if not brief[key]:brief[key]=profile[key]
        if 'platform' not in payload.brief.model_fields_set:brief['platform']=profile['platform']
    source.update(profile=profile,creative_brief=brief, knowledge=knowledge.relevant(brief,payload.profile_id,payload.include_global_knowledge), comment_samples=[],
                  comment_context=dict(enabled=payload.include_comments, saved_total=saved_total,
                                       policy='补充评论优先，已保存评论按时间倒序取前160条候选，去重后最多80条。每条最多500字，总计8000字。'))
    if payload.include_project_research:
        from . import research
        source['project_research']=research.project_context(payload.project_id)
    if not payload.include_comments:
        return source
    pasted = [dict(comment_id=f'manual-{i+1}', body=line.strip(), origin='user')
              for i, line in enumerate(payload.pasted_comments.splitlines()) if line.strip()]
    seen = set()
    budget = MAX_COMMENT_CHARS
    for row in pasted + [dict(item, origin='saved') for item in comments]:
        text = (row.get('body') or '').strip()
        if not text or text in seen:
            continue
        seen.add(text)
        if len(source['comment_samples']) >= MAX_COMMENTS or budget <= 0:
            break
        excerpt = text[:min(500, budget)]
        source['comment_samples'].append(dict(comment_id=row['comment_id'], text=excerpt,
            origin=row['origin'], truncated=len(excerpt)<len(text), published_at=row.get('published_at')))
        budget -= len(excerpt)
    source['comment_context'].update(sampled=len(source['comment_samples']),
        manual_submitted=len(pasted), deduplicated_candidates=len(seen))
    return source


def build_evidence(source, transcript):
    from .reference_evidence import prepare
    source, upstream = prepare(source)
    evidence = []
    limit = 30000
    def append(kind, text, identifier=None, **extra):
        nonlocal limit
        text = str(text)
        if not text or len(text) > limit:
            return False
        evidence.append(dict(id=identifier or f'E{len(evidence)+1:04d}',kind=kind,text=text,**extra))
        limit -= len(text)
        return True
    append('title', source['title'][:1000])
    metadata = {k:source.get(k) for k in ('kind','author','published_at','collected_at','metrics','video_available')}
    metadata['platform'] = {'xhs':'xhs','upload':'用户上传（原平台未知）','manual':'用户填写（原平台未知）','report':'分析报告（见来源快照）','research':'研究结果（见来源快照）','analytics':'数据分析（见筛选范围）'}.get(source['kind'],'未知')
    append('source_context', json.dumps(metadata,ensure_ascii=False))
    brief=dict(source.get('creative_brief',{}))
    supplemental=brief.pop('supplemental','')
    if supplemental:
        append('supplement',supplemental,origin='user_provided')
    append('brief', json.dumps(brief,ensure_ascii=False))
    if source.get('execution'):
        append('creation_execution', json.dumps(source['execution'],ensure_ascii=False))
    if source.get('provenance'):
        append('source_provenance', json.dumps(source['provenance'],ensure_ascii=False))
    if source.get('profile'):append('account_profile',json.dumps(source['profile'],ensure_ascii=False))
    for entry in upstream:
        append(entry['kind'],entry['text'],identifier=entry['id'],**{k:v for k,v in entry.items() if k not in ('id','kind','text')})
    used = []
    # Preferences guide expression; they are never source facts.
    for entry in source.get('knowledge',[]):
        snapshot = {**entry,'content':entry['content'][:800]}
        if append('writing_preference',json.dumps(snapshot,ensure_ascii=False)):
            used.append(dict(id=entry['id'],version=entry['version'],title=entry['title'],profile_id=entry.get('profile_id','')))
    used_research=[]
    for entry in source.get('project_research',[]):
        if append('project_research',json.dumps(entry,ensure_ascii=False)):
            used_research.append(dict(id=entry['revision_id'],name=entry['name'],assets=len(entry['assets'])))
    samples = source.get('comment_samples',[])
    comment_reserve = min(sum(len(c['text']) for c in samples), 8000, max(0,limit//3))
    body = source.get('body','')
    speech_chars = sum(len(s['text']) for s in transcript)
    source_budget = max(0,limit-comment_reserve-6000)
    body_budget = min(len(body),source_budget//2 if transcript else source_budget)
    body_excerpt = body[:body_budget]
    for start in range(0,len(body_excerpt),600):
        append('report_context' if upstream else 'body',body_excerpt[start:start+600])
    comment_used = 0
    for sample in samples:
        if len(sample['text']) > comment_reserve:
            break
        append('comment',sample['text'],comment_id=sample['comment_id'],origin=sample['origin'],truncated=sample['truncated'])
        comment_reserve -= len(sample['text'])
        comment_used += 1
    topic_table=topics.build(evidence)
    if topic_table['sample_size']:
        append('comment_topics',json.dumps(dict(rule_version=topic_table['rule_version'],sample_size=topic_table['sample_size'],multi_label=True,groups=[dict(topic=g['topic'],count=g['count'],evidence_ids=g['evidence_ids'][:4]) for g in topic_table['groups']]),ensure_ascii=False))
    speech_used = 0
    for segment in transcript:
        if len(segment['text'])>limit-6000:
            break
        append('transcript',segment['text'],start=segment['start'],end=segment['end'])
        speech_used += len(segment['text'])
    coverage = dict(body_chars=len(body_excerpt),body_original_chars=source.get('original_body_chars',len(body)),
        source_body_previously_truncated=source.get('truncated',False),
        transcript_chars=speech_used,transcript_original_chars=speech_chars,
        comments={**source.get('comment_context',{}),'included':comment_used},used_knowledge=used,used_research=used_research,
        profile=({k:source['profile'][k] for k in ('id','version','name')} if source.get('profile') else None),
        visual_analysis=False, upstream_evidence=source.get('upstream_coverage'))
    if not append('coverage',json.dumps(coverage,ensure_ascii=False)):
        raise ValueError('分析范围说明超过输入预算，请精简创作需求或写作经验')
    return evidence
