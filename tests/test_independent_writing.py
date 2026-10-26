"""Separate analysis/writing execution with frozen results and versioned methods."""
import asyncio
import copy
import json
from uuid import uuid4

import pytest

from tests.test_content_remix import isolated,client,report,wait
from api.services.remix import providers,store,workflow,task_contracts,registry,artifacts
from api.services.remix.schemas import JobInput,AgentDefinition,SkillDefinition


def submit(client,**data):
    payload=dict(consent=True,request_key=uuid4().hex,**data)
    response=client.post('/api/remix/jobs',json=payload)
    assert response.status_code==200,response.text
    return wait(client,response.json()['id'])


@pytest.fixture
def calls(monkeypatch):
    calls=[]
    async def analyze(evidence):
        calls.append(('analysis',copy.deepcopy(evidence)))
        return report(evidence),{'total_tokens':20},'fixture-analysis'
    async def write(evidence):
        calls.append(('writing',copy.deepcopy(evidence)))
        return report(evidence),{'total_tokens':30},'fixture-writer'
    def forbidden(*args,**kwargs):pytest.fail('Writing must not run media/ASR')
    monkeypatch.setattr(providers,'analyze',analyze);monkeypatch.setattr(providers,'write',write)
    monkeypatch.setattr(providers,'prepare_audio',forbidden);monkeypatch.setattr(providers,'transcribe',forbidden)
    return calls


def test_analysis_and_writing_are_distinct_jobs_contracts_and_retries(client,calls,monkeypatch):
    parent=submit(client,task_kind='analysis',source='xhs',content_id='fixture-note')
    before=store.get(parent['id'])
    assert parent['report']['rewrites']==[] and parent['artifacts']['creation']['state']=='not_requested'
    async def fail(evidence):
        calls.append(('writing-failed',evidence));raise providers.AnalysisError('仅写作失败')
    original=providers.write;monkeypatch.setattr(providers,'write',fail)
    response=client.post('/api/remix/jobs',json=dict(task_kind='writing',source='report',reference_id=parent['id'],consent=True,request_key=uuid4().hex))
    child=wait(client,response.json()['id'],'failed')
    assert child['id']!=parent['id'] and child['stage']=='write'
    assert child['parent_result_sha256']==task_contracts.result_hash(before)
    assert store.get(parent['id'])==before
    monkeypatch.setattr(providers,'write',original)
    assert client.post(f"/api/remix/jobs/{child['id']}/retry").status_code==200
    final=wait(client,child['id'])
    assert final['attempts']==2 and final['report']['sections']==[] and final['report']['rewrites']
    assert [c[0] for c in calls]==['analysis','writing-failed','writing']
    assert store.get(parent['id'])==before
    relations=client.get(f"/api/remix/jobs/{parent['id']}/relations").json()
    assert relations['children']['items'][0]['id']==child['id']
    assert client.get('/api/remix/jobs?task_kind=analysis').json()['total']==1
    assert client.get('/api/remix/jobs?task_kind=writing').json()['total']==1
    assert client.get(f"/api/remix/jobs/{child['id']}/relations").json()['snapshot_matches_current']


def test_same_analysis_reused_for_new_tone_and_exact_writing_cache(client,calls,monkeypatch):
    parent=submit(client,task_kind='analysis',source='xhs',content_id='fixture-note',brief={'tone':'直接'})
    original=store.get(parent['id'])
    # Writer-only defaults do not invalidate analysis inputs or its model cache.
    repeated=submit(client,task_kind='analysis',source='xhs',content_id='fixture-note',brief={'tone':'温和','framework':'信息筛选'})
    assert repeated['cache_hit'] and len(calls)==1
    first=submit(client,task_kind='writing',source='report',reference_id=parent['id'],brief={'tone':'直接'})
    frozen=copy.deepcopy(first['source']['reference_bundle'])
    changed=copy.deepcopy(original['report']);changed['summary']='后来更新的摘要'
    store.update(parent['id'],report=changed)
    def forbidden(*args,**kwargs):pytest.fail('Reusing a source must not query current collection data')
    monkeypatch.setattr(workflow.analytics,'read_db',forbidden)
    second=submit(client,task_kind='writing',source='report',reuse_job_id=first['id'],brief={'tone':'温和'})
    assert second['source']['reference_bundle']==frozen
    assert second['parent_result_sha256']==first['parent_result_sha256']
    assert second['reused_from_job_id']==first['id']
    assert not client.get(f"/api/remix/jobs/{second['id']}/relations").json()['snapshot_matches_current']
    same=submit(client,task_kind='writing',source='report',reuse_job_id=second['id'],brief={'tone':'温和'})
    assert same['cache_hit'] and [c[0] for c in calls]==['analysis','writing','writing']
    assert same['report']==second['report']


def test_writing_graph_reuses_transcript_and_original_media_without_preparation(client,calls):
    parent=submit(client,task_kind='analysis',source='xhs',content_id='fixture-note')
    transcript=[dict(start=0,end=1,text='已保存的转写原文')]
    store.update(parent['id'],transcript=transcript,duration=1,input={**parent['input'],'analyze_video':True})
    folder=workflow.folder_for(parent['id']);(folder/'video.mp4').write_bytes(b'fixture-video')
    child=submit(client,task_kind='writing',source='report',reference_id=parent['id'])
    assert child['transcript']==transcript and child['duration']==1
    assert child['source']['media_owner_id']==parent['id']
    reused=[e for e in child['evidence'] if e.get('origin_kind')=='transcript']
    assert len(reused)==1 and reused[0]['text']==transcript[0]['text'] and reused[0]['start']==0
    assert client.get(f"/api/remix/jobs/{child['id']}/video").content==b'fixture-video'
    assert not (store.root()/'jobs'/child['id']/'audio.wav').exists()
    assert [c[0] for c in calls]==['analysis','writing']


def test_independent_contracts_reject_cross_stage_outputs_and_prompts_are_separate(isolated,monkeypatch):
    import httpx
    evidence=workflow.build_evidence(workflow.source_for(JobInput(task_kind='analysis',source='xhs',content_id='fixture-note',request_key=uuid4().hex)),[])
    good=report(evidence);bad=copy.deepcopy(good)
    legacy=report([e for e in evidence if e['kind']!='task_contract'])
    bad['rewrites']=legacy['rewrites']
    with pytest.raises(providers.AnalysisError,match='rewrites'):providers.validate_report(bad,evidence)
    assert artifacts.manifest(bad,evidence)['creation']['ready_count']==0
    seen=[];original=httpx.AsyncClient
    def handler(request):
        payload=json.loads(request.content);seen.append(payload)
        return httpx.Response(200,json=dict(choices=[dict(finish_reason='stop',message=dict(content=json.dumps(good)))]))
    monkeypatch.setattr(providers.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    asyncio.run(providers.analyze(evidence))
    assert '本次是独立分析任务' in seen[0]['messages'][0]['content']
    assert '生成两篇不同角度的 rewrites' not in seen[0]['messages'][0]['content']
    writer=workflow.build_evidence(workflow.source_for(JobInput(task_kind='writing',source='manual',material='已有事实',request_key=uuid4().hex)),[])
    bad=report(writer);bad['sections']=legacy['sections']
    with pytest.raises(providers.AnalysisError,match='sections'):providers.validate_report(bad,writer,True)


def test_registry_versions_compatibility_freeze_disable_and_idempotent_submission(client,calls):
    agent=dict(id='local_editor',name='本机编辑',instruction='先解释适用条件，再给出核实办法。',content_type='图文',version=0)
    skill=dict(id='local_checklist',name='核实清单',instruction='只用现有证据，不补造缺失信息。',content_types=['图文'],version=0)
    assert client.post('/api/remix/creation/agents',json=agent).json()['version']==1
    assert client.post('/api/remix/creation/skills',json=skill).json()['version']==1
    payload=dict(task_kind='writing',source='manual',material='路线长两公里',agent_id='local_editor',skill_ids=['local_checklist'],consent=True,request_key=uuid4().hex)
    result=client.post('/api/remix/jobs',json=payload);assert result.status_code==200,result.text
    job=wait(client,result.json()['id']);frozen=copy.deepcopy(job['source']['execution'])
    assert frozen['agent']['version']==1 and frozen['skills'][0]['version']==1
    update={**agent,'version':1,'enabled':False,'instruction':'新版表达方式'}
    changed=client.put('/api/remix/creation/agents/local_editor',json=update)
    assert changed.status_code==200 and changed.json()['version']==2
    assert client.put('/api/remix/creation/agents/local_editor',json=update).status_code==409
    assert len(client.get('/api/remix/creation/definitions/agent/local_editor/versions').json())==2
    assert store.get(job['id'])['source']['execution']==frozen
    assert client.post('/api/remix/jobs',json=payload).json()['id']==job['id']
    assert client.post('/api/remix/jobs',json={**payload,'request_key':uuid4().hex}).status_code==422
    assert all(a['id']!='local_editor' for a in client.get('/api/remix/creation/catalog').json()['agents'])
    assert client.post('/api/remix/jobs',json={**payload,'agent_id':'script_writer','brief':{'content_type':'视频'},'request_key':uuid4().hex}).status_code==422
    assert client.put('/api/remix/creation/agents/content_writer',json={**agent,'id':'content_writer','version':1}).status_code==409
    # A custom video agent uses the output contract, not a hard-coded identifier.
    registry.save('agent',AgentDefinition(id='local_video',name='口播编辑',instruction='生成口播和分段',content_type='视频'))
    evidence=workflow.build_evidence(workflow.source_for(JobInput(task_kind='writing',source='manual',material='真实材料',agent_id='local_video',brief={'content_type':'视频'},request_key=uuid4().hex)),[])
    broken=report(evidence);broken['rewrites'][0]['script']=[]
    with pytest.raises(providers.AnalysisError,match='分段脚本'):providers.validate_report(broken,evidence,True)


def test_unknown_or_malformed_registry_and_invalid_task_sources_are_rejected(client,calls):
    base=dict(task_kind='writing',source='manual',material='真实材料',consent=True,request_key=uuid4().hex)
    assert client.post('/api/remix/jobs',json={**base,'agent_id':'missing'}).status_code==422
    assert client.post('/api/remix/jobs',json={**base,'skill_ids':['answer','answer']}).status_code==422
    assert client.post('/api/remix/creation/agents',json=dict(id='../escape',name='a',instruction='a')).status_code==422
    assert client.post('/api/remix/jobs',json={**base,'source':'xhs','content_id':'fixture-note'}).status_code==409
    assert client.post('/api/remix/jobs',json={**base,'task_kind':'analysis'}).status_code==409
    assert not calls


def test_submission_rejects_a_method_version_changed_since_selection(client,calls):
    definition=registry.save('agent',AgentDefinition(id='checked_editor',name='有版本的编辑',instruction='核对资料'))
    registry.save('agent',AgentDefinition(**{k:v for k,v in definition.items() if k not in ('builtin','updated_at')}),expected_id='checked_editor')
    skill=registry.save('skill',SkillDefinition(id='checked_skill',name='有版本的方法',instruction='保留范围'))
    base=dict(task_kind='writing',source='manual',material='有来源的材料',agent_id='checked_editor',skill_ids=['checked_skill'],consent=True,request_key=uuid4().hex)
    assert client.post('/api/remix/jobs',json={**base,'agent_version':1,'skill_versions':{'checked_skill':1}}).status_code==422
    assert client.post('/api/remix/jobs',json={**base,'agent_version':2,'skill_versions':{'checked_skill':2}}).status_code==422
    assert not calls
    valid=client.post('/api/remix/jobs',json={**base,'agent_version':2,'skill_versions':{'checked_skill':1}})
    assert valid.status_code==200,valid.text
    result=wait(client,valid.json()['id'])
    assert result['source']['execution']['agent']['version']==2
    assert len(calls)==1
