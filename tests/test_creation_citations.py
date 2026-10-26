"""Upstream citations stay attached to their frozen excerpts, including old retries."""
import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from tests.test_content_remix import isolated, report
from api.services.remix import providers, store, workflow
from api.services.remix.schemas import JobInput


def frozen_source(count=56, large=False):
    originals=[dict(id=f'E{i:04d}',kind='body',text=f'原文证据{i}：'+('停车条件须核实。'*300 if large else '停车条件须核实。')) for i in range(1,count+1)]
    upstream=report(originals)
    upstream['summary_evidence_ids']=[originals[-1]['id']]
    upstream['sections'][0]['findings'][0]['evidence_ids']=[originals[-1]['id']]
    upstream['rewrites'][0]['evidence_ids']=[originals[-1]['id']]
    return dict(kind='report',title='来源报告 · 创作',body=json.dumps(dict(report=upstream,evidence=originals),ensure_ascii=False),
        video_available=False,creative_brief={'framework':'auto'},comment_context={'enabled':False},
        provenance={'kind':'report','job_id':'a'*32})


def test_old_report_ids_are_remapped_to_real_current_excerpts():
    source=frozen_source()
    frozen=source['body']
    evidence=workflow.build_evidence(source,[])
    imported={e['id']:e for e in evidence if e['kind']=='source_excerpt'}
    assert len(imported)==56 and imported['R0056']['upstream_id']=='E0056'
    assert '原文证据56' in imported['R0056']['text']
    narrative=''.join(e['text'] for e in evidence if e['kind']=='report_context')
    assert 'R0056' in narrative and 'E0056' not in narrative
    assert source['body']==frozen # Existing source/report is never rewritten.
    valid=report(evidence);valid['rewrites'][0]['evidence_ids']=['R0056']
    assert providers.validate_report(valid,evidence,require_creation=True)['rewrites'][0]['evidence_ids']==['R0056']
    valid['rewrites'][0]['evidence_ids']=['E9999']
    with pytest.raises(providers.AnalysisError,match='未知编号 1'):
        providers.validate_report(valid,evidence,require_creation=True)


def test_new_large_report_keeps_structured_snapshot_and_bounded_imports(isolated):
    source=frozen_source(170,large=True)
    original=store.submit(JobInput(source='manual',request_key=uuid4().hex),'fixture',dict(kind='manual',title='父报告',body='原文',video_available=False))
    bundle=json.loads(source['body'])
    store.update(original['id'],status='completed',report=bundle['report'],evidence=bundle['evidence'])
    frozen=workflow.source_for(JobInput(source='report',reference_id=original['id'],request_key=uuid4().hex))
    assert frozen['truncated'] and len(frozen['reference_bundle']['evidence'])==170
    evidence=workflow.build_evidence(frozen,[])
    imported=[e for e in evidence if e['kind']=='source_excerpt']
    assert len(imported)==160 and sum(len(e['text']) for e in imported)<=12000
    assert sum(len(e['text']) for e in evidence)<=30000
    assert all(e['truncated'] for e in imported)
    # Omitted upstream references never masquerade as current citations.
    narrative=''.join(e['text'] for e in evidence if e['kind']=='report_context')
    assert 'E0170' not in narrative and 'R0170' not in narrative
    coverage=json.loads(next(e['text'] for e in evidence if e['kind']=='coverage'))
    assert coverage['upstream_evidence']['omitted']==10


def test_incomplete_legacy_snapshot_fails_before_model():
    source=frozen_source();source['body']=source['body'][:100]
    with pytest.raises(providers.AnalysisError,match='快照不完整.*尚未调用模型'):
        workflow.build_evidence(source,[])


def test_model_receives_exact_id_allowlist_without_legacy_metadata(isolated,monkeypatch):
    evidence=workflow.build_evidence(frozen_source(),[])
    requests=[]
    real_client=httpx.AsyncClient
    def handler(request):
        payload=json.loads(request.content);requests.append(payload)
        user=json.loads(payload['messages'][1]['content'])
        assert set(user['citation_ids'])=={e['id'] for e in evidence}
        assert 'E0056' not in payload['messages'][1]['content']
        assert all('upstream_id' not in e for e in user['evidence'])
        raw=report(evidence);raw['rewrites'][0]['evidence_ids']=['R0056']
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(raw)}}]})
    monkeypatch.setattr(providers.httpx,'AsyncClient',lambda **kw:real_client(transport=httpx.MockTransport(handler),**kw))
    result,_,_=asyncio.run(providers.analyze(evidence))
    assert result['rewrites'][0]['evidence_ids']==['R0056'] and len(requests)==1
    schema=providers.citation_schema(evidence)
    assert schema['$defs']['Rewrite']['properties']['evidence_ids']['items']['enum']==[e['id'] for e in evidence]


def test_checkpoint_retry_rebuilds_evidence_from_original_frozen_snapshot(isolated,monkeypatch):
    source=frozen_source()
    payload=JobInput(source='report',reference_id='a'*32,consent=True,request_key=uuid4().hex)
    job=store.submit(payload,'fixture',source)
    legacy=[dict(id=f'E{i:04d}',kind='body',text='旧版重分段') for i in range(1,36)]
    store.update(job['id'],status='running',evidence=legacy)
    calls=[]
    async def model(evidence):
        calls.append(evidence)
        assert any(e['id']=='R0056' and '原文证据56' in e['text'] for e in evidence)
        raw=report(evidence);raw['rewrites'][0]['evidence_ids']=['R0056']
        return raw,{'total_tokens':1},'fixture-model'
    monkeypatch.setattr(providers,'analyze',model)
    async def resume():
        async with AsyncSqliteSaver.from_conn_string(str(store.root()/'checkpoints.sqlite3')) as saver:
            graph=workflow.graph(saver)
            await graph.aupdate_state({'configurable':{'thread_id':job['id']}},{'job_id':job['id'],'evidence':legacy},as_node='evidence')
        await workflow.run(job['id'])
    asyncio.run(resume())
    final=store.get(job['id'])
    assert final['status']=='completed' and len(calls)==1
    assert final['source']==source and final['report']['rewrites'][0]['evidence_ids']==['R0056']
    assert final['evidence']==calls[0]


def test_chained_report_does_not_turn_upstream_comments_into_facts():
    source=frozen_source(1)
    source['body']=json.dumps(dict(report=report([{'id':'R0001','kind':'body','text':'来源'}]),evidence=[dict(id='R0001',kind='source_excerpt',origin_kind='comment',text='评论者的说法',factual_source=False)]))
    imported=next(e for e in workflow.build_evidence(source,[]) if e['kind']=='source_excerpt')
    assert imported['origin_kind']=='comment' and not imported['factual_source']


def test_citation_like_product_codes_inside_original_quotes_are_not_rewritten():
    source=frozen_source(1)
    bundle=json.loads(source['body']);bundle['evidence'][0]['text']='原文中的商品型号是 E0001，不是引用。'
    source['body']=json.dumps(bundle,ensure_ascii=False)
    quote=next(e for e in workflow.build_evidence(source,[]) if e['kind']=='source_excerpt')
    assert quote['text']==bundle['evidence'][0]['text']
