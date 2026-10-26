"""Complete local source packages and creation-specific output validation."""
import asyncio
import json
import sqlite3
from uuid import uuid4

import pytest
from tests.test_content_remix import isolated, client, report
from api.services.remix import store, workflow, providers
from api.services.remix.schemas import JobInput
from api.services.remix.source_archive import records,archive
from database import analytics as schema
from api.services import analytics


def creation_input(**changes):
    return JobInput(source='analytics',analytics_filters={'platform':'xhs'},consent=True,request_key=uuid4().hex,**changes)


def test_analytics_freezes_all_original_records_and_keeps_tail_after_db_change(isolated):
    with sqlite3.connect(analytics.db_path()) as db:
        for i in range(170):
            schema.save(db,schema.normalize(dict(platform='xhs',content_id='fixture-note',comment_id=f'comment-{i:03d}',body='完整评论'+str(i)+'尾部'),kind='comment'),kind='comment')
    payload=creation_input();source=workflow.source_for(payload)
    assert source['dataset_snapshot']['complete']
    assert source['dataset_snapshot']['counts']==dict(contents=1,comments=170)
    assert '先说明问题' in source['dataset_snapshot']['contents'][0]['body']
    evidence=workflow.build_evidence(source,[])
    comments=[e for e in evidence if e.get('origin_kind')=='comment']
    assert len(comments)==170 and any('169尾部' in e['text'] for e in comments)
    assert any(e['id']=='A0001' and 'authors' in e['text'] for e in evidence)
    job=store.submit(payload,'fixture',source)
    store.update(job['id'],evidence=evidence)
    with sqlite3.connect(analytics.db_path()) as db:db.execute("DELETE FROM analytics_comment")
    saved=store.get(job['id'])
    assert archive(saved)['source']['dataset_snapshot']['counts']['comments']==170
    assert records(saved,'comment',9)['items'][-1]['data']['body']=='完整评论169尾部'
    assert records(saved,record_id='C0170')['items'][0]['data']['body']=='完整评论169尾部'


def test_creation_contract_does_not_require_fake_post_analysis(isolated):
    evidence=workflow.build_evidence(workflow.source_for(creation_input()),[])
    raw=report(evidence);raw.pop('sections');raw.pop('ideas')
    parsed=providers.validate_report(raw,evidence,require_creation=True)
    assert parsed['sections']==[] and parsed['ideas']==[] and parsed['rewrites']
    assert 'sections' not in providers.citation_schema(evidence)['required']
    with pytest.raises(providers.AnalysisError,match='sections'):
        providers.validate_report(raw,[dict(id='E0001',kind='title',text='单条内容')])
    raw['rewrites'][0]['title_options']=[]
    with pytest.raises(providers.AnalysisError,match=r'rewrites.0.title_options') as failure:
        providers.validate_report(raw,evidence,require_creation=True)
    assert failure.value.diagnostics['issues'][0]['field']=='rewrites.0.title_options'
    assert 'input' not in json.dumps(failure.value.diagnostics)


def test_invalid_response_is_preserved_privately_and_recoverable_without_another_call(isolated,monkeypatch):
    payload=creation_input();source=workflow.source_for(payload)
    job=store.submit(payload,'fixture',source);store.update(job['id'],status='running')
    calls=[]
    async def old_contract(evidence):
        calls.append(evidence)
        candidate=report(evidence);candidate.pop('sections');candidate.pop('ideas')
        raise providers.AnalysisError('旧输出要求拒绝了创作稿',diagnostics={'kind':'schema','issues':[{'field':'sections','code':'missing','message':'缺少必填字段'}]},candidate=candidate)
    monkeypatch.setattr(providers,'analyze',old_contract)
    with pytest.raises(providers.AnalysisError):asyncio.run(workflow.run(job['id']))
    rejected=store.root()/'jobs'/job['id']/'rejected-report.json'
    assert rejected.is_file() and rejected.stat().st_mode&0o777==0o600
    assert store.get(job['id'])['model_diagnostics']['issues'][0]['field']=='sections'
    asyncio.run(workflow.run(job['id']))
    final=store.get(job['id'])
    assert final['status']=='completed' and final['recovered_response'] and len(calls)==1
    assert final['report']['sections']==[] and final['model_diagnostics'] is None


def test_source_archive_endpoints_keep_frozen_rows_and_raw_schema_diagnostics(client,monkeypatch):
    async def model(evidence):return report(evidence),{},'fixture-model'
    monkeypatch.setattr(providers,'analyze',model)
    source=workflow.source_for(creation_input());job=store.submit(creation_input(),'fixture',source)
    route=f"/api/remix/jobs/{job['id']}"
    response=client.get(route+'/source-records',params={'kind':'content'})
    assert response.status_code==200 and response.json()['total']==1
    package=client.get(route+'/source-archive.json')
    assert package.status_code==200 and package.json()['source']['dataset_snapshot']['complete']
    assert package.headers['cache-control']=='no-store'
    assert client.get(route+'/rejected-report.json').status_code==404


def test_oversized_sources_fail_before_a_model_call_instead_of_being_sliced(isolated):
    from tests.test_creation_citations import frozen_source
    with pytest.raises(providers.AnalysisError,match='完整创作素材超过120000'):
        workflow.build_evidence(frozen_source(170,large=True),[])
