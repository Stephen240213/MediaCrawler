"""Local export -> analytics -> research evidence. No collection or model calls."""
import json
import sqlite3

import pytest
from api.services import analytics, workbench_store, collection_batches
from api.services.redfox_ingest import sync_export, sync_file
from api.services.redfox_payload import check_result
from api.services.remix import workflow, research, context
from api.services.remix.schemas import JobInput
from api.services.remix.research_schemas import ResearchSpace
from tests.test_content_remix import isolated, client


def exported(data, tool='xiaohongshu_search_articles', task_id='d'*32, **context_values):
    result={'code':2000,'data':data}
    return dict(task_id=task_id,source='redfox_mcp',tool=tool,collected_at='2026-10-26T08:00:00Z',
                context={'keyword':'MCP旅行',**context_values}, result={'isError':False,'structuredContent':result,
                'content':[{'type':'text','text':json.dumps(result)}]})


def note(**changes):
    return dict(workId='mcp-note',workTitle='MCP旅行路线',workDesc='先核对路线，再安排住宿。',
                accountNickname='测试发布者',accountUserid='raw-user-fixture',workLikedCount=123,
                workCollectedCount=12,workCommentsCount=2,workSharedCount=0,
                workPublishTime='2026-10-25 12:00:00',workType='normal',**changes)


def task(tid='d'*32, platform='xhs', **changes):
    value=dict(id=tid,collection_method='redfox_mcp',platform=platform,crawler_type='search',
               started_at='2026-10-26T08:00:00Z',outcome='completed',exit_code=0,**changes)
    workbench_store.save_document('task',value)
    return value


@pytest.mark.parametrize('structured',[False,True])
def test_false_protocol_success_is_business_failure(isolated,structured):
    value={'error':'api_error','code':3203,'message':'接口执行异常，积分未扣除: category参数值的无效'}
    result={'isError':False,'content':[{'type':'text','text':json.dumps(value)}]}
    if structured:result['structuredContent']=value
    with pytest.raises(ValueError,match='3203.*category'):
        check_result(result)
    with pytest.raises(ValueError,match='3203'):
        sync_export({**exported([]),'result':result},'xhs')
    assert analytics.details({'platform':'xhs','content_id':'mcp-note'})['total']==0


def test_content_body_metrics_membership_and_research_chain(isolated):
    task()
    report=sync_export(exported({'list':[note()]}),'xhs')
    assert report['contents']==1 and not report['replayed']
    rows=analytics.details({'platform':'xhs','content_id':'mcp-note'})['items']
    assert rows[0]['likes']==123 and rows[0]['shares']==0 and rows[0]['keyword']=='MCP旅行'
    assert rows[0]['published_at']=='2026-10-25T04:00:00.000000Z'
    assert rows[0]['author']!='测试发布者'
    members=collection_batches.members('d'*32)
    assert members['exact'] and [r['content_id'] for r in members['items']]==['mcp-note']
    payload=JobInput(source='xhs',content_id='mcp-note',consent=True,request_key='mcp-evidence-fixture')
    source=workflow.source_for(payload)
    assert source['body']=='先核对路线，再安排住宿。'
    saved=research.save(ResearchSpace(name='MCP批次',kind='topic',selection_mode='task',task_id='d'*32),'local')
    assert research.plan(saved['id'])['total']==1
    with analytics.read_db() as (db,_):
        catalog=research._catalog(db,search='MCP旅行')
    assert catalog[0]['body_available'] and catalog[0]['account_id']
    account=research.save(ResearchSpace(name='MCP账号',kind='account',account_id=catalog[0]['account_id']),'local')
    assert research.plan(account['id'])['total']==1
    replay=sync_export(exported({'list':[note()]}),'xhs')
    assert replay['replayed'] and analytics.history('xhs','mcp-note')['snapshot_count']==1
    assert collection_batches.members('d'*32)['total']==1


def test_comments_async_parent_link_reaches_evidence(isolated):
    task();sync_export(exported([note()]),'xhs')
    receipt=exported({'taskId':'remote-comments'},tool='xiaohongshu_get_comments',task_id='e'*32,parent_id='mcp-note')
    assert sync_export(receipt,'xhs')['status']=='raw_only'
    response=exported({'list':[{'commentId':'comment-fixture','content':'住宿怎么安排？','createTime':'2026-10-25 13:00:00'}]},
                      tool='xiaohongshu_get_comments_result',task_id='f'*32,remote_task_id='remote-comments')
    assert sync_export(response,'xhs')['comments']==1
    assert analytics.overview({'platform':'xhs','content_id':'mcp-note'})['summary']['comment_count']==1
    source=workflow.source_for(JobInput(source='xhs',content_id='mcp-note',include_comments=True,consent=True,request_key='mcp-comments-fixture'))
    evidence=context.build_evidence(source,[])
    assert any(e['kind']=='body' and '住宿' in e['text'] for e in evidence)
    assert any(e['kind']=='comment' and e['text']=='住宿怎么安排？' for e in evidence)
    assert sync_export(response,'xhs')['replayed']
    with analytics.read_db() as (db,_):
        assert db.execute("SELECT count(*) FROM analytics_comment WHERE comment_id='comment-fixture'").fetchone()[0]==1


def test_unknown_rows_accounts_sparse_updates_and_uncertain_metrics(isolated):
    first=note();first['useLikeCount']='2w+';first.pop('workLikedCount')
    report=sync_export(exported([first,{'accountUserid':'account-only','accountNickname':'not content'}]),'xhs')
    assert report['contents']==1 and report['rejected']==1
    row=analytics.details({'platform':'xhs','content_id':'mcp-note'})['items'][0]
    assert row['likes'] is None and row['favorites']==12
    second=exported([{'workId':'mcp-note','workTitle':'更完整的路线','workLikedCount':200}],task_id='e'*32)
    second['collected_at']='2026-10-26T09:00:00Z'
    sync_export(second,'xhs')
    row=analytics.details({'platform':'xhs','content_id':'mcp-note'})['items'][0]
    assert row['likes']==200 and row['favorites']==12
    assert workflow.source_for(JobInput(source='xhs',content_id='mcp-note',consent=True,request_key='mcp-sparse-fixture'))['body']=='先核对路线，再安排住宿。'
    snapshots=analytics.history('xhs','mcp-note')['items']
    assert snapshots[0]['favorites'] is None # Not falsely observed again on a sparse card.
    report=sync_export(exported({'id':'a-user','name':'account'},tool='xiaohongshu_get_account',task_id='f'*32),'xhs')
    assert report['status']=='raw_only' and report['contents']==0


@pytest.mark.parametrize('platform,tool,row',[
    ('xhs','xiaohongshu_get_dark_horse_notes',{'photoId':'darkhorse','title':'黑马笔记','desc':'正文','useLikeCount':'2w+','collectedCount':'123','publicTime':'2026-10-25 12:00:00'}),
    ('dy','douyin_search_articles',{'workId':'douyin-fixture','title':'抖音作品','content':'作品正文','likeCount':42}),
    ('twitter','twitter_get_tweet',{'rest_id':'tweet-fixture','legacy':{'full_text':'推文正文','favorite_count':17}}),
    ('youtube','youtube_get_video',{'videoId':'video-fixture','title':'视频标题','description':'视频简介','likeCount':12}),
])
def test_platform_shapes(isolated,platform,tool,row):
    report=sync_export(exported([row],tool=tool),'%s'%platform)
    assert report['contents']==1
    assert analytics.overview({'platform':platform})['summary']['content_count']>=1


def test_historical_file_sync_and_failure_repair(client,isolated,monkeypatch):
    from api.routers import data
    root=isolated/'exports';monkeypatch.setattr(data,'DATA_DIR',root)
    folder=root/'xhs'/'redfox';folder.mkdir(parents=True)
    path=folder/('d'*32+'.json');path.write_text(json.dumps([exported([note()])]))
    task()
    response=client.post('/api/crawler/redfox/import',json={'path':'xhs/redfox/'+path.name})
    assert response.status_code==200 and response.json()['contents']==1
    assert client.post('/api/crawler/redfox/import',json={'path':'xhs/redfox/'+path.name}).json()['replayed']
    assert client.post('/api/crawler/redfox/import',json={'path':'../../.env'}).status_code==422
    failed=exported([],task_id='e'*32)
    failed['result']={'isError':False,'structuredContent':{'error':'api_error','code':3203,'message':'category参数值的无效'}}
    error_path=folder/('e'*32+'.json');error_path.write_text(json.dumps([failed]));task('e'*32)
    response=client.post('/api/crawler/redfox/import',json={'path':'xhs/redfox/'+error_path.name})
    assert response.status_code==422 and '3203' in response.text
    assert workbench_store.get_document('task','e'*32)['outcome']=='failed'
    info=data.get_file_info(error_path)
    assert info['record_count']==0 and '失败响应' in info['display_name']


def test_twitter_reply_ids_and_parent_envelope_are_distinct(isolated):
    main=exported({'tweetId':'parent-tweet','content':'父推文','likeCount':12,'threadReplies':[{'tweetId':'reply-tweet','text':'回复正文'}]},
                  tool='twitter_get_tweet')
    assert sync_export(main,'twitter')['contents']==1
    replies=exported({'tweetId':'parent-tweet','content':'父推文','threadReplies':[
        {'tweetId':'reply-tweet','text':'回复正文','createdAt':'Mon Oct 26 08:00:00 +0000 2026'}]},
        tool='twitter_get_comments',task_id='e'*32)
    assert sync_export(replies,'twitter')['comments']==1
    details=analytics.details({'platform':'twitter'},kind='comment')['items']
    assert details[0]['comment_id']=='reply-tweet' and details[0]['content_id']=='parent-tweet'
    assert details[0]['published_at']=='2026-10-26T08:00:00.000000Z'


def test_body_freshness_prefers_newer_saved_native_content(isolated):
    with sqlite3.connect(analytics.db_path()) as db:
        db.execute('ALTER TABLE xhs_note ADD COLUMN last_modify_ts INTEGER')
        db.execute('ALTER TABLE xhs_note ADD COLUMN time INTEGER')
    sync_export(exported([note()]),'xhs')
    with sqlite3.connect(analytics.db_path()) as db:
        from database.analytics import register_functions
        register_functions(db)
        db.execute('INSERT INTO xhs_note(note_id,desc,last_modify_ts) VALUES(?,?,?)',('mcp-note','较新的本地正文',1793005200000))
    source=workflow.source_for(JobInput(source='xhs',content_id='mcp-note',consent=True,request_key='mcp-native-freshness'))
    assert source['body']=='较新的本地正文'
