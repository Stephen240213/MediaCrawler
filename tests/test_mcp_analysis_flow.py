"""MCP preview/provenance/research integration; temporary data and mock models only."""
import asyncio
import json
from uuid import uuid4

from api.services import analytics,workbench_store
from api.services.redfox_ingest import sync_export
from api.services.remix import research, research_provider
from api.services.remix.research_schemas import ResearchSpace,ResearchRun
from tests.test_content_remix import isolated,client
from tests.test_redfox_ingest import exported,note,task
from tests.test_remix_research import fake_model


def test_flat_preview_real_count_and_raw_mode(client,isolated,monkeypatch):
    from api.routers import data
    root=isolated/'exports';monkeypatch.setattr(data,'DATA_DIR',root)
    folder=root/'xhs'/'redfox';folder.mkdir(parents=True)
    values=[{**note(),'workId':f'preview-{i}','workTitle':f'内容 {i}'} for i in range(23)]
    path=folder/('d'*32+'.json');path.write_text(json.dumps([exported({'list':values})]))
    info=data.get_file_info(path)
    assert info['record_count']==23 and info['mcp']['task_id']=='d'*32
    response=client.get('/api/data/files/xhs/redfox/'+path.name,params={'limit':20,'offset':20})
    assert response.status_code==200 and response.json()['total']==23
    assert len(response.json()['data'])==3
    assert response.json()['data'][0]['content_id']=='preview-20'
    assert response.json()['data'][0]['body']=='先核对路线，再安排住宿。'
    raw=client.get('/api/data/files/xhs/redfox/'+path.name,params={'raw':True}).json()
    assert raw['data'][0]['source']=='redfox_mcp' and raw['total']==1
    # Previewing never imports rows.
    assert analytics.details({'source':'redfox_mcp'})['total']==0


def test_exact_mcp_source_and_batch_filters_cover_exports_and_comments(client,isolated):
    task();sync_export(exported([note()]),'xhs')
    task('e'*32);sync_export(exported([{**note(),'workId':'batch-two'}],task_id='e'*32),'xhs')
    # The first post also appears in a second batch; source totals still deduplicate.
    task('f'*32);sync_export(exported([note()],task_id='f'*32),'xhs')
    sync_export(exported([{'commentId':'saved-comment','content':'怎么安排住宿？'}],tool='xiaohongshu_get_comments_result',task_id='a'*32,parent_id='mcp-note'),'xhs')
    assert client.get('/api/analytics/overview',params={'source':'redfox_mcp'}).json()['summary']['content_count']==2
    selection={'source':'redfox_mcp','task_id':'d'*32}
    assert client.get('/api/analytics/details',params=selection).json()['total']==1
    assert client.get('/api/analytics/details',params={**selection,'kind':'comment'}).json()['total']==1
    assert client.get('/api/analytics/overview',params=selection).json()['summary']['comment_count']==1
    csv=client.get('/api/analytics/export',params={**selection,'format':'csv'}).text
    assert 'mcp-note' in csv and 'batch-two' not in csv and 'fixture-note' not in csv
    choices=client.get('/api/analytics/sources').json()['batches']
    assert next(b for b in choices if b['task_id']=='d'*32)['content_count']==1
    assert client.get('/api/analytics/details',params={'task_id':'b'*32}).json()['total']==0
    assert client.get('/api/analytics/details',params={'source':'unrecognized'}).status_code==422


def test_local_stats_require_no_saved_scope_or_model_call(client,isolated,monkeypatch):
    task();sync_export(exported([note()]),'xhs')
    async def forbidden(*args,**kwargs):raise AssertionError('local statistics must not invoke a model')
    monkeypatch.setattr(research_provider,'analyze',forbidden)
    payload=ResearchSpace(name='本地统计',kind='topic',selection_mode='task',task_id='d'*32,
                          question='比较这批内容的点赞与收藏').model_dump()
    response=client.post('/api/remix/research/preview-statistics',json=payload)
    assert response.status_code==200,response.text
    stats=response.json()
    assert stats['contents']==1 and stats['body_available']==1 and stats['saved_comments']==0
    assert stats['metrics']['likes']=={'known':1,'missing':0,'total':123,'median':123}
    assert stats['metrics']['comments']['total']==2 # Provider count is not fabricated saved text.
    assert research.listing()==[]


def test_question_guides_api_synthesis_and_invalidates_only_its_cache(isolated,monkeypatch):
    task();sync_export(exported([note()]),'xhs')
    calls=[]
    async def model(kind,evidence,question=''):
        calls.append((kind,question,evidence))
        return await fake_model(kind,evidence)
    monkeypatch.setattr(research_provider,'analyze',model)
    payload=ResearchSpace(name='自然语言研究',kind='topic',selection_mode='task',task_id='d'*32,question='比较表达结构')
    space=research.save(payload,'local')
    def run():
        p=research.plan(space['id'])
        batch=research.submit(space['id'],ResearchRun(plan_token=p['plan_token'],consent=True,max_calls=40,request_key=uuid4().hex),'local')
        asyncio.run(research.execute(batch['id']))
        return research.run_get(batch['id'])
    done=run()
    assert done['status']=='completed'
    assert any(kind=='topic' and question=='比较表达结构' and any(e['id'].startswith('stats:') for e in evidence) for kind,question,evidence in calls)
    assert research.plan(space['id'])['max_calls']==0
    material_calls=sum(kind=='material' for kind,_,_ in calls)
    values={k:space[k] for k in ResearchSpace.model_fields if k in space};values['question']='寻找有证据的选题机会'
    space=research.save(ResearchSpace(**values),'local',space['id'])
    assert research.plan(space['id'])['max_calls']>0
    assert run()['status']=='completed'
    assert sum(kind=='material' for kind,_,_ in calls)==material_calls
    assert any(question=='寻找有证据的选题机会' for _,question,_ in calls)
