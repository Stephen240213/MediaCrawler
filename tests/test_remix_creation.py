"""Creation and learning use fixture data only; no live model or platform calls."""
import json
import sqlite3

import pytest

from api.services import analytics
from api.services.remix import context, knowledge, providers, store, workflow
from api.services.remix.schemas import CreativeBrief, JobInput, KnowledgeInput
from database import analytics as schema
from tests.test_content_remix import client, isolated, report, submit, wait


def add_comments():
    with sqlite3.connect(analytics.db_path()) as db:
        for comment_id, text in [('a','路线怎么安排？'),('b','路线怎么安排？'),('c','租期和付款方式是什么？'),('d','')]:
            schema.save(db,schema.normalize(dict(platform='xhs',content_id='fixture-note',comment_id=comment_id,body=text),kind='comment'),kind='comment')
        schema.save(db,schema.normalize(dict(platform='xhs',content_id='other-note',comment_id='other',body='其他内容的评论'),kind='comment'),kind='comment')
        db.execute("UPDATE analytics_content SET author='测试作者',likes=0,favorites=5,published_at='2026-10-20T00:00:00Z' WHERE content_id='fixture-note'")


def entry(**changes):
    value=dict(kind='preference',title='客观表达',content='避免全网最低，只保留已知条件',platform='xhs',topic='租房')
    value.update(changes)
    return value


def test_source_context_and_comments_are_frozen_scoped_and_cited(isolated):
    add_comments()
    payload=JobInput(source='xhs',content_id='fixture-note',consent=True,pasted_comments='手动补充的评论\n路线怎么安排？',request_key='comments-source-12345')
    source=workflow.source_for(payload)
    assert source['comment_context']['saved_total']==4
    assert len(source['comment_samples'])==3
    assert '其他内容的评论' not in json.dumps(source,ensure_ascii=False)
    evidence=workflow.build_evidence(source,[])
    meta=json.loads(next(e['text'] for e in evidence if e['kind']=='source_context'))
    assert meta['author']=='测试作者' and meta['platform']=='xhs' and meta['metrics']['likes']==0
    assert meta['published_at']
    assert len([e for e in evidence if e['kind']=='comment'])==3
    result=providers.validate_report(report(evidence),evidence,require_creation=True)
    assert len(result['comment_analysis'])==1
    raw=report(evidence);raw['comment_analysis'][0]['evidence_ids']=['E0001']
    with pytest.raises(providers.AnalysisError,match='评论'):
        providers.validate_report(raw,evidence)
    with sqlite3.connect(analytics.db_path()) as db:
        db.execute("DELETE FROM analytics_comment WHERE content_id='fixture-note'")
    assert len(source['comment_samples'])==3


def test_comments_opt_out_and_manual_upload_context(isolated):
    add_comments()
    payload=JobInput(source='xhs',content_id='fixture-note',include_comments=False,pasted_comments='不应发送',request_key='comments-disabled-123')
    source=workflow.source_for(payload)
    assert not source['comment_samples']
    assert not any(e['kind']=='comment' for e in workflow.build_evidence(source,[]))
    payload=JobInput(source='upload',pasted_comments='咨询租期\n询问水电',request_key='manual-comments-1234')
    source=context.snapshot(dict(kind='upload',title='上传素材'),payload)
    assert len(source['comment_samples'])==2
    assert all(c['origin']=='user' for c in source['comment_samples'])


def test_evidence_budget_reports_actual_coverage(isolated):
    payload=JobInput(source='xhs',request_key='evidence-budget-1234',pasted_comments='\n'.join('评'+str(i)+'字'*400 for i in range(18)),
        brief=CreativeBrief(supplemental='补'*3000,must_keep='实'*1000))
    source=context.snapshot(dict(kind='xhs',title='标题',body='文'*29000),payload)
    speech=[dict(start=i,end=i+1,text='语'*1000) for i in range(29)]
    evidence=workflow.build_evidence(source,speech)
    assert sum(len(e['text']) for e in evidence)<=providers.MAX_SOURCE_CHARS
    coverage=json.loads(next(e['text'] for e in evidence if e['kind']=='coverage'))
    assert coverage['body_chars']<coverage['body_original_chars']
    assert coverage['comments']['included']>0
    assert coverage['comments']['included']==len([e for e in evidence if e['kind']=='comment'])
    assert len({e['id'] for e in evidence})==len(evidence)


def test_knowledge_versions_scope_disable_and_snapshot(isolated):
    saved=knowledge.save(KnowledgeInput(**entry()),'operator')
    assert not knowledge.relevant(dict(platform='douyin',topic='租房'))
    assert not knowledge.relevant(dict(platform='xhs',topic='旅行'))
    selected=knowledge.relevant(dict(platform='xhs',topic='租房'))
    assert selected[0]['version']==1
    changed=knowledge.save(KnowledgeInput(**entry(content='简明说明付款方式',version=1)), 'operator',saved['id'])
    assert changed['version']==2 and selected[0]['content']!=changed['content']
    with pytest.raises(ValueError,match='刷新'):
        knowledge.save(KnowledgeInput(**entry()),'operator',saved['id'])
    assert len(knowledge.history(saved['id']))==2
    disabled=knowledge.save(KnowledgeInput(**entry(version=2,enabled=False)),'operator',saved['id'])
    assert disabled['version']==3 and not knowledge.relevant(dict(platform='xhs',topic='租房'))
    knowledge.remove(saved['id'])
    assert not knowledge.listing()['items'] and not knowledge.history(saved['id'])


def test_knowledge_api_rejects_stale_updates_and_invalid_sources(client):
    saved=client.post('/api/remix/knowledge',json=entry()).json()
    assert saved['version']==1
    body=entry(content='更新表达',version=1)
    assert client.put('/api/remix/knowledge/'+saved['id'],json=body).json()['version']==2
    assert client.put('/api/remix/knowledge/'+saved['id'],json=body).status_code==409
    assert len(client.get('/api/remix/knowledge/'+saved['id']+'/history').json())==2
    assert client.post('/api/remix/knowledge',json=entry(source_job_id='a'*32)).status_code==409
    assert client.delete('/api/remix/knowledge/'+saved['id']).status_code==200


def test_brief_and_knowledge_changes_invalidate_cache(client,monkeypatch):
    calls=[]
    async def model(evidence):
        calls.append(evidence)
        return report(evidence),{},'fixture'
    monkeypatch.setattr(providers,'analyze',model)
    first=submit(client,brief={'topic':'租房'})
    wait(client,first['id'])
    second=submit(client,brief={'topic':'租房'},request_key='same-brief-second-123')
    assert wait(client,second['id'])['cache_hit']
    client.post('/api/remix/knowledge',json=entry())
    third=submit(client,brief={'topic':'租房'},request_key='new-knowledge-12345')
    assert not wait(client,third['id'])['cache_hit']
    fourth=submit(client,brief={'topic':'租房','objective':'咨询'},request_key='new-objective-12345')
    assert not wait(client,fourth['id'])['cache_hit']
    assert len(calls)==3
    assert any(e['kind']=='writing_preference' for e in calls[1])
    assert not any(e['kind']=='writing_preference' for e in calls[0])


def test_full_creation_versions_adoption_and_learning(client,monkeypatch):
    add_comments()
    async def model(evidence):return report(evidence),{},'fixture'
    monkeypatch.setattr(providers,'analyze',model)
    job=submit(client,brief=dict(topic='租房',framework='信息筛选'))
    final=wait(client,job['id'])
    assert final['report']['rewrites'][0]['framework']=='信息筛选'
    assert final['report']['comment_analysis']
    content=dict(rewrite_index=0,title='编辑后的标题',content='人工修订后的完整正文',feedback='以后将付款条件放在前面',request_key='version-save-key-123')
    version=client.post(f"/api/remix/jobs/{job['id']}/versions",json=content).json()
    assert version['content']==content['content'] and version['original_content']!=content['content']
    assert client.post(f"/api/remix/jobs/{job['id']}/versions",json=content).json()['id']==version['id']
    assert client.post(f"/api/remix/jobs/{job['id']}/versions",json={**content,'content':'不同正文'}).status_code==409
    other=client.post(f"/api/remix/jobs/{job['id']}/versions",json={**content,'content':'另一版本','request_key':'version-save-key-456'}).json()
    path=f"/api/remix/jobs/{job['id']}/versions/{version['id']}/adopt"
    adopted=client.post(path).json()
    draft=adopted['draft'];draft['content']='在草稿中再次修改'
    assert client.put('/api/workbench/drafts/'+draft['id'],json={k:draft[k] for k in ('title','content','tags','images','platform','version')}).status_code==200
    assert client.post(path).json()['draft']['content']=='在草稿中再次修改'
    client.post(f"/api/remix/jobs/{job['id']}/versions/{other['id']}/adopt")
    versions=client.get(f"/api/remix/jobs/{job['id']}/versions").json()
    assert sum(v['adopted'] for v in versions)==1
    learned=client.post('/api/remix/knowledge',json=entry(kind='edit',source_job_id=job['id'],source_version_id=version['id'],content=version['feedback']))
    assert learned.status_code==200 and learned.json()['kind']=='edit'
    exported=client.get(f"/api/remix/jobs/{job['id']}/report.md").text
    assert '改写方案' in exported and '评论样本分析' in exported and '当前采用版本' in exported and '另一版本' in exported


def test_new_model_contract_and_legacy_readability(isolated):
    evidence=[dict(id='E0001',kind='title',text='1250元/月，年租年付')]
    raw=report(evidence)
    legacy={k:v for k,v in raw.items() if k not in ('recommended_framework','framework_reason','comment_analysis','rewrites')}
    assert providers.validate_report(legacy,evidence)['rewrites']==[]
    with pytest.raises(providers.AnalysisError,match='完整改写'):
        providers.validate_report(legacy,evidence,require_creation=True)
    evidence.append(dict(id='E0002',kind='brief',text=json.dumps(dict(framework='信息筛选'))))
    with pytest.raises(providers.AnalysisError,match='框架'):
        providers.validate_report(raw,evidence,require_creation=True)
    raw['rewrites'][0]['evidence_ids']=['E9999']
    with pytest.raises(providers.AnalysisError,match='引用'):
        providers.validate_report(raw,evidence)
