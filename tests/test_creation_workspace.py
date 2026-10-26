"""Creation sources and queue use temporary stores and a fixture model only."""
import asyncio
import json
import time
from uuid import uuid4

import pytest
from tests.test_content_remix import isolated, client, report
from api.services.remix import creation, providers, store, workflow
from api.services.remix.schemas import JobInput


def payload(**changes):
    return JobInput(source='manual', material='路线为两公里，需提前核实停车条件。', consent=True,
                    request_key=uuid4().hex, **changes)


def test_manual_source_freezes_agent_skills_and_is_not_a_fact_preference(isolated):
    source=workflow.source_for(payload(agent_id='script_writer',skill_ids=['compare','story'],brief={'content_type':'视频'}))
    assert source['kind']=='manual' and source['provenance']['kind']=='manual'
    assert source['execution']['agent']['id']=='script_writer'
    assert [s['version'] for s in source['execution']['skills']]==[1,1]
    evidence=workflow.build_evidence(source,[])
    assert any(e['kind']=='body' and '两公里' in e['text'] for e in evidence)
    policy=next(json.loads(e['text']) for e in evidence if e['kind']=='creation_execution')
    assert policy['skills'][0]['id']=='compare'
    with pytest.raises(ValueError,match='需要选择视频'):
        workflow.source_for(payload(agent_id='script_writer'))
    with pytest.raises(ValueError,match='填写创作材料'):
        creation.source_for(JobInput(source='manual',request_key=uuid4().hex))


def test_report_and_analytics_sources_keep_provenance(isolated):
    original=store.submit(payload(),'fixture',workflow.source_for(payload()))
    evidence=workflow.build_evidence(original['source'],[])
    store.update(original['id'],status='completed',report=report(evidence),evidence=evidence)
    request=JobInput(source='report',reference_id=original['id'],request_key=uuid4().hex)
    source=workflow.source_for(request)
    assert source['provenance']['job_id']==original['id']
    assert '测试摘要' in source['body'] and '两公里' in source['body']
    frozen=source['body']
    store.update(original['id'],report={'summary':'后来改变'})
    assert source['body']==frozen
    data=workflow.source_for(JobInput(source='analytics',analytics_filters={'platform':'xhs'},request_key=uuid4().hex))
    assert data['provenance']['filters']=={'platform':'xhs'}
    snapshot=json.loads(data['body'])
    assert snapshot['summary']['content_count']==1 and snapshot['generated_at']
    with pytest.raises(ValueError,match='没有可用于创作'):
        workflow.source_for(JobInput(source='analytics',analytics_filters={'platform':'youtube'},request_key=uuid4().hex))
    with pytest.raises(ValueError,match='筛选参数无效'):
        workflow.source_for(JobInput(source='analytics',analytics_filters={'sql':'private'},request_key=uuid4().hex))


def test_creation_queue_and_draft_adoption(client,monkeypatch):
    calls=[]
    async def model(evidence):
        calls.append(evidence)
        return report(evidence),{'total_tokens':8},'fixture-creation'
    monkeypatch.setattr(providers,'analyze',model)
    catalog=client.get('/api/remix/creation/catalog').json()
    assert len(catalog['agents'])==2 and len(catalog['skills'])==4
    data=payload(skill_ids=['answer']).model_dump()
    denied=client.post('/api/remix/jobs',json={**data,'consent':False})
    assert denied.status_code==422 and not calls
    response=client.post('/api/remix/jobs',json=data)
    assert response.status_code==200,response.text
    job=response.json()
    assert client.post('/api/remix/jobs',json=data).json()['id']==job['id']
    for _ in range(100):
        job=client.get('/api/remix/jobs/'+job['id']).json()
        if job['status'] in ('completed','failed'):break
        time.sleep(.05)
    assert job['status']=='completed',job
    assert len(calls)==1 and any(e['kind']=='creation_execution' for e in calls[0])
    rewrite=job['report']['rewrites'][0]
    version=client.post(f"/api/remix/jobs/{job['id']}/versions",json=dict(rewrite_index=0,title=rewrite['title_options'][0]['title'],content=rewrite['body'],request_key=uuid4().hex)).json()
    route=f"/api/remix/jobs/{job['id']}/versions/{version['id']}/adopt"
    draft=client.post(route).json()['draft']
    assert draft['content']==rewrite['body'] and client.post(route).json()['draft']['id']==draft['id']
    assert client.post('/api/remix/jobs',json={**data,'skill_ids':['untrusted-skill']}).status_code==422


def test_research_source_freezes_revision_and_job_search(isolated):
    revision_id=uuid4().hex
    revision=dict(id=revision_id,status='partial',space={'name':'旅行研究'},materials=[{'summary':'已有材料归纳'}],assets=[{'text':'可能存在停车需求'}],evidence=[{'id':'original-E1','text':'需要提前确认停车'}])
    with store.connect() as db:
        db.execute('INSERT INTO research_revisions VALUES(?,?,?)',(revision_id,uuid4().hex,json.dumps(revision)))
    source=workflow.source_for(JobInput(source='research',reference_id=revision_id,request_key=uuid4().hex))
    assert source['provenance']['revision_id']==revision_id and '需要提前确认停车' in source['body']
    request=payload(title='可查找的旅行任务')
    job=store.submit(request,'fixture',workflow.source_for(request))
    assert store.listing(search='旅行',status='queued')['items'][0]['id']==job['id']
    assert store.listing(search='不存在')['total']==0
    assert store.listing(search="' OR 1=1 --")['total']==0
    assert store.listing(search='旅行',status='completed')['total']==0


def test_selected_agent_and_skill_reach_model_and_script_output_is_checked(isolated,monkeypatch):
    import httpx
    source=workflow.source_for(payload(agent_id='script_writer',skill_ids=['compare'],brief={'content_type':'视频'}))
    evidence=workflow.build_evidence(source,[])
    calls=[]
    original=httpx.AsyncClient
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(report(evidence))}}]})
    monkeypatch.setattr(providers.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    asyncio.run(providers.analyze(evidence))
    system=calls[0]['messages'][0]['content']
    assert '视频脚本 Agent' in system and '决策对比' in system and '按相同维度比较选项' in system
    broken=report(evidence);broken['rewrites'][0]['script']=[]
    with pytest.raises(providers.AnalysisError,match='未返回口播开头与分段脚本'):
        providers.validate_report(broken,evidence,require_creation=True)
