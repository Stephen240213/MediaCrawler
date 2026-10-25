"""Frozen, bounded source and comment evidence; all reads stay on the collection Mac."""
import json

from . import knowledge

MAX_COMMENTS = 80
MAX_COMMENT_CHARS = 8000


def saved_comments(db, content_id):
    total = db.execute("SELECT count(*) FROM analytics_comment WHERE platform='xhs' AND content_id=?", (content_id,)).fetchone()[0]
    rows = db.execute("SELECT comment_id,body,published_at FROM analytics_comment WHERE platform='xhs' AND content_id=? ORDER BY published_at DESC,comment_id LIMIT 160", (content_id,)).fetchall()
    return total, [dict(row) for row in rows]


def snapshot(source, payload, comments=(), saved_total=0):
    brief = payload.brief.model_dump()
    source.update(creative_brief=brief, knowledge=knowledge.relevant(brief), comment_samples=[],
                  comment_context=dict(enabled=payload.include_comments, saved_total=saved_total,
                                       policy='补充评论优先，已保存评论按时间倒序取前160条候选，去重后最多80条。每条最多500字，总计8000字。'))
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
    evidence = []
    limit = 30000
    def append(kind, text, **extra):
        nonlocal limit
        text = str(text)
        if not text or len(text) > limit:
            return False
        evidence.append(dict(id=f'E{len(evidence)+1:04d}',kind=kind,text=text,**extra))
        limit -= len(text)
        return True
    append('title', source['title'][:1000])
    metadata = {k:source.get(k) for k in ('kind','author','published_at','collected_at','metrics','video_available')}
    metadata['platform'] = 'xhs' if source['kind']=='xhs' else '用户上传'
    append('source_context', json.dumps(metadata,ensure_ascii=False))
    brief=dict(source.get('creative_brief',{}))
    supplemental=brief.pop('supplemental','')
    if supplemental:
        append('supplement',supplemental,origin='user_provided')
    append('brief', json.dumps(brief,ensure_ascii=False))
    used = []
    # Preferences guide expression; they are never source facts.
    for entry in source.get('knowledge',[]):
        snapshot = {**entry,'content':entry['content'][:800]}
        if append('writing_preference',json.dumps(snapshot,ensure_ascii=False)):
            used.append(dict(id=entry['id'],version=entry['version'],title=entry['title']))
    samples = source.get('comment_samples',[])
    comment_reserve = min(sum(len(c['text']) for c in samples), 8000, max(0,limit//3))
    body = source.get('body','')
    speech_chars = sum(len(s['text']) for s in transcript)
    source_budget = max(0,limit-comment_reserve-3000)
    body_budget = min(len(body),source_budget//2 if transcript else source_budget)
    body_excerpt = body[:body_budget]
    for start in range(0,len(body_excerpt),600):
        append('body',body_excerpt[start:start+600])
    comment_used = 0
    for sample in samples:
        if len(sample['text']) > comment_reserve:
            break
        append('comment',sample['text'],comment_id=sample['comment_id'],origin=sample['origin'],truncated=sample['truncated'])
        comment_reserve -= len(sample['text'])
        comment_used += 1
    speech_used = 0
    for segment in transcript:
        if len(segment['text'])>limit-3000:
            break
        append('transcript',segment['text'],start=segment['start'],end=segment['end'])
        speech_used += len(segment['text'])
    coverage = dict(body_chars=len(body_excerpt),body_original_chars=source.get('original_body_chars',len(body)),
        source_body_previously_truncated=source.get('truncated',False),
        transcript_chars=speech_used,transcript_original_chars=speech_chars,
        comments={**source.get('comment_context',{}),'included':comment_used},used_knowledge=used,
        visual_analysis=False)
    if not append('coverage',json.dumps(coverage,ensure_ascii=False)):
        raise ValueError('分析范围说明超过输入预算，请精简创作需求或写作经验')
    return evidence
