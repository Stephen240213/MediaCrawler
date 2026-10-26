"""Incremental research on isolated local fixtures; never call a real model."""
import asyncio
import copy
import sqlite3
import time
from uuid import uuid4
import httpx
import pytest
from api.services import analytics
from api.services.remix import research as r, research_provider as provider, projects
from api.services.remix.research_schemas import ResearchSpace, ResearchRun, AssetTags
from api.services.remix.schemas import ProjectInput, ProjectLink
from database import analytics as schema
from tests.test_content_remix import isolated, client


async def fake_model(kind,evidence):
    return fixture_result(kind,evidence),{'total_tokens':17},'fixture-research'


def fixture_result(kind,evidence):
    ids=[e['id'] for e in evidence]
    if kind=='material':
        return dict(summary='原文介绍旅行安排',opening='用具体问题开头',structure='问题→方法→限制',
                    assets=[dict(kind='来源声明',category='费用条件',text='来源提到费用，尚需核对',evidence_ids=ids[-1:])],limitations=['未分析画面'])
    if kind=='comments': return dict(contributions=[dict(evidence_id=e['id'],category='费用条件',need=e['text'][:200]) for e in evidence])
    return dict(summary=('同一账号的选题及表达观察' if kind=='account' else '跨来源问题与说法对照'),
                findings=[dict(kind='方法候选',category='费用条件',text='明确已知条件，补齐缺口后验证',evidence_ids=ids[:1])],gaps=['这些样本不能证明传播因果'])


@pytest.fixture
def model(monkeypatch):
    calls=[]
    async def invoke(kind,evidence):
        calls.append((kind,copy.deepcopy(evidence)))
        return await fake_model(kind,evidence)
    monkeypatch.setattr(provider,'analyze',invoke)
    return calls


def add_note(cid='second',keyword='旅行',user='stable-user',author='同名作者',body='费用是700，具体条件待确认',**values):
    with sqlite3.connect(analytics.db_path()) as db:
        cols={row[1] for row in db.execute('PRAGMA table_info(xhs_note)')}
        if 'user_id' not in cols: db.execute('ALTER TABLE xhs_note ADD COLUMN user_id TEXT')
        row=db.execute('SELECT 1 FROM xhs_note WHERE note_id=?',(cid,)).fetchone()
        if row: db.execute('UPDATE xhs_note SET desc=?,user_id=? WHERE note_id=?',(body,user,cid))
        else: db.execute('INSERT INTO xhs_note(note_id,desc,user_id) VALUES(?,?,?)',(cid,body,user))
        schema.save(db,schema.normalize(dict(platform='xhs',content_id=cid,keyword=keyword,title='费用与旅行 '+cid,author=author,**values)))


def comment(cid,text,note='second'):
    with sqlite3.connect(analytics.db_path()) as db:
        schema.save(db,schema.normalize(dict(platform='xhs',content_id=note,comment_id=cid,body=text),kind='comment'),kind='comment')


def space(**values):
    return r.save(ResearchSpace(name='旅居资料',kind='topic',keywords=['旅行'],**values),'local')


def execute(s,max_calls=40):
    p=r.plan(s['id']);run=r.submit(s['id'],ResearchRun(plan_token=p['plan_token'],consent=True,max_calls=max_calls,request_key=uuid4().hex),'local')
    asyncio.run(r.execute(run['id']))
    done=r.run_get(run['id'])
    return done,r.revision(done['revision_id'])


def test_shared_cache_across_single_account_topic_and_unchanged(isolated,model):
    add_note();comment('c1','费用包含什么？');comment('c2','费用包含什么？')
    topic=space();done,rev=execute(topic)
    assert done['status']=='completed' and rev['stats']['comments_covered']==2
    assert rev['stats']['body_covered']==1
    assert [k for k,_ in model].count('material')==1
    assert [k for k,_ in model].count('comments')==1
    before=len(model);same,same_rev=execute(topic)
    assert len(model)==before and same['calls']==[] and same_rev['id']==rev['id']
    assert r.plan(topic['id'])['max_calls']==0
    discovery=r.discovery();assert discovery['unknown_accounts']==1
    account=r.save(ResearchSpace(name='实际发布者',kind='account',account_id=discovery['accounts'][0]['id']),'local')
    account_done,account_rev=execute(account)
    assert not any(k in ('material','comments') for k,_ in model[before:])
    assert all(c['kind']=='account' for c in account_done['calls'])
    assert account_rev['space']['kind']=='account'
    single=r.save(ResearchSpace(name='单篇',kind='single',content_id='second'),'local')
    before=len(model);execute(single)
    assert all(k=='single' for k,_ in model[before:])
    assert 'profile' not in r.encode(model) and 'brief' not in r.encode(model)


def test_metrics_comment_edit_delete_and_body_update_are_independent(isolated,model):
    add_note();comment('c1','费用包含什么？');s=space();_,rev=execute(s)
    before=len(model)
    with sqlite3.connect(analytics.db_path()) as db: db.execute("UPDATE analytics_content SET likes=13,collected_at='2026-10-25' WHERE content_id='second'")
    p=r.plan(s['id']);assert p['counts']['metrics_changed']==1 and p['counts']['material_pending']==0
    _,metric_rev=execute(s)
    assert len(model)==before and metric_rev['stats']['metrics']['likes']==dict(known=1,total=13)
    comment('c2','费用包含什么？');before=len(model)
    assert r.plan(s['id'])['counts']['comment_pending']==1
    _,new=execute(s)
    delta=model[before:]
    assert not any(k=='material' for k,_ in delta)
    assert len(next(e for k,e in delta if k=='comments'))==1
    assert new['stats']['comments_covered']==2
    comment('c1','押金是多少？');before=len(model);execute(s)
    assert len(next(e for k,e in model[before:] if k=='comments'))==1
    with sqlite3.connect(analytics.db_path()) as db: db.execute("DELETE FROM analytics_comment WHERE comment_id='c2'")
    before=len(model);_,removed=execute(s)
    assert not any(k in ('material','comments') for k,_ in model[before:])
    assert removed['stats']['comments_covered']==1
    assert not any(e['text']=='费用包含什么？' for e in removed['evidence'])
    add_note(body='改过的正文：明确费用及租期');before=len(model);execute(s)
    assert [k for k,_ in model[before:]].count('material')==1
    assert not any(k=='comments' for k,_ in model[before:])
    assert r.revision(rev['id'])['stats']['comments_covered']==1


def test_keyword_memberships_dedup_and_account_identity_correction(isolated,model):
    add_note(keyword='旅行');add_note(keyword='旅居');add_note(cid='other',user='another-id')
    with sqlite3.connect(analytics.db_path()) as db:
        assert db.execute("SELECT count(*) FROM source_keyword_memberships WHERE content_id='second'").fetchone()[0]==2
    a=r.discovery()['accounts'];assert len(a)==2 and a[0]['id']!=a[1]['id']
    s=r.save(ResearchSpace(name='多词',kind='topic',keywords=['旅行','旅居']),'local')
    assert r.plan(s['id'])['total']==2
    _,rev=execute(s);assert rev['stats']['body_covered']==2
    acct=r.save(ResearchSpace(name='账号',kind='account',account_id=r.digest(['xhs','stable-user'])),'local')
    execute(acct);before=len(model)
    with sqlite3.connect(analytics.db_path()) as db: db.execute("UPDATE xhs_note SET user_id='another-id' WHERE note_id='second'")
    assert r.plan(acct['id'])['counts']['removed']==1
    _,rev=execute(acct);assert rev['stats']['members']==0
    assert not any(k=='material' for k,_ in model[before:])
    with pytest.raises(ValueError): r.save(ResearchSpace(name='昵称不能归属',kind='account',account_id='a'*64),'local')


def test_frozen_scope_stale_preview_budget_partial_and_failure_retry(isolated,model,monkeypatch):
    add_note();s=space();p=r.plan(s['id'])
    comment('c1','租金？')
    with pytest.raises(ValueError,match='变化'): r.submit(s['id'],ResearchRun(plan_token=p['plan_token'],consent=True,request_key=uuid4().hex),'local')
    p=r.plan(s['id']);payload=ResearchRun(plan_token=p['plan_token'],consent=True,max_calls=1,request_key=uuid4().hex)
    run=r.submit(s['id'],payload,'local');assert r.submit(s['id'],payload,'local')['id']==run['id']
    add_note(cid='arrives-later')
    asyncio.run(r.execute(run['id']));done=r.run_get(run['id']);rev=r.revision(done['revision_id'])
    assert done['status']=='partial' and len(done['calls'])==1 and rev['stats']['members']==1
    assert r.plan(s['id'])['total']==2
    assert rev['stats']['body_covered']==1 and rev['stats']['comments_covered']==0
    async def fail(kind,evidence):
        if kind=='material' and any('arrives-later' in e['text'] for e in evidence): raise provider.providers.AnalysisError('模拟服务失败')
        return await fake_model(kind,evidence)
    monkeypatch.setattr(provider,'analyze',fail)
    done,rev=execute(s)
    assert done['status']=='partial' and any(i['status']=='failed' for i in done['items'])
    assert rev['stats']['body_covered']==1
    monkeypatch.setattr(provider,'analyze',fake_model)
    done,rev=execute(s)
    assert done['status']=='completed' and rev['stats']['body_covered']==2
    assert [c['kind'] for c in done['calls']].count('material')==1


def test_exclusion_project_pin_asset_tags_and_schema_version(isolated,model,monkeypatch):
    add_note();s=space();_,old=execute(s)
    project=projects.save(ProjectInput(title='创作交付'),'local')
    projects.link(project['id'],ProjectLink(kind='research',resource_id=old['id']))
    asset=r.assets(s['id'])[0]
    assert r.tag_asset(s['id'],asset['id'],AssetTags(tags=['旅居 / 费用','决策'],version=0))['version']==1
    with pytest.raises(ValueError):r.tag_asset(s['id'],asset['id'],AssetTags(tags=[],version=0))
    assert r.assets(s['id'],category='旅居 / 费用')
    data={k:v for k,v in s.items() if k in ResearchSpace.model_fields};data.update(exclude_ids=['second'])
    s=r.save(ResearchSpace(**data),'local',s['id']);_,new=execute(s)
    assert new['stats']['members']==0 and r.assets(s['id'])==[]
    detail=projects.detail(project['id']);ref=detail['links'][0]
    assert ref['resource_id']==old['id'] and ref['resource']['newer_available']
    assert r.revision(old['id'])['stats']['members']==1
    data.update(version=s['version'],exclude_ids=[]);r.save(ResearchSpace(**data),'local',s['id'])
    monkeypatch.setattr(provider,'VERSION','neutral-research-new-policy')
    assert r.plan(s['id'])['counts']['material_pending']==1


def test_research_queue_api_consent_archive_and_recovery(client,model):
    add_note()
    for i in range(25):comment('api-comment-'+str(i),'费用怎么计算？')
    created=client.post('/api/remix/research/spaces',json=dict(name='API专题',kind='topic',keywords=['旅行']));assert created.status_code==200
    s=created.json();p=client.get(f"/api/remix/research/spaces/{s['id']}/plan").json()
    payload=dict(plan_token=p['plan_token'],consent=False,max_calls=20,request_key=uuid4().hex)
    assert client.post(f"/api/remix/research/spaces/{s['id']}/runs",json=payload).status_code==409
    payload['consent']=True;response=client.post(f"/api/remix/research/spaces/{s['id']}/runs",json=payload);assert response.status_code==200
    run=response.json();assert 'sources' not in run['plan'] and 'provider' not in run['plan']
    for _ in range(100):
        current=client.get('/api/remix/research/runs/'+run['id']).json()
        if current['status']=='completed': break
        time.sleep(.05)
    assert current['status']=='completed',current
    first=client.get(f"/api/remix/research/spaces/{s['id']}/assets").json()
    second=client.get(f"/api/remix/research/spaces/{s['id']}/assets?page=2").json()
    assert first['total']==27 and len(first['items'])==20 and len(second['items'])==7
    assert not {a['id'] for a in first['items']}&{a['id'] for a in second['items']}
    assert client.get('/api/remix/research/revisions/'+current['revision_id']).status_code==200
    value={k:v for k,v in s.items() if k in ResearchSpace.model_fields};value['archived']=True
    assert client.put('/api/remix/research/spaces/'+s['id'],json=value).status_code==200
    payload.update(plan_token=client.get(f"/api/remix/research/spaces/{s['id']}/plan").json()['plan_token'],request_key=uuid4().hex)
    assert client.post(f"/api/remix/research/spaces/{s['id']}/runs",json=payload).status_code==409
    assert client.put('/api/remix/editors/research/new-topic',json=dict(revision=0,data={'name':'未完成'})).status_code==200


def test_interrupted_calls_never_auto_retry(isolated,model):
    add_note();s=space();p=r.plan(s['id']);run=r.submit(s['id'],ResearchRun(plan_token=p['plan_token'],consent=True,request_key=uuid4().hex),'local')
    r.update_run(run['id'],status='running',calls=[dict(kind='material',status='started')])
    r.recover();assert r.run_get(run['id'])['calls'][0]['status']=='unknown'
    assert r.run_get(run['id'])['status']=='interrupted' and not model


def test_provider_rejects_missing_comment_and_fabricated_refs():
    evidence=[dict(id='stable-id',text='费用？')]
    with pytest.raises(ValueError):provider.validate('comments',{'contributions':[]},evidence)
    raw=fixture_result('material',evidence);raw['assets'][0]['evidence_ids']=['fabricated']
    with pytest.raises(ValueError):provider.validate('material',raw,evidence)


@pytest.mark.asyncio
async def test_research_transport_uses_bounded_json_no_retries(isolated,monkeypatch):
    calls=[]
    def handle(request):
        import json
        payload=json.loads(request.content);calls.append(payload)
        assert 'brief' not in request.content.decode() and payload['max_tokens']==6000
        evidence=json.loads(payload['messages'][1]['content'])
        return httpx.Response(200,json={'choices':[dict(finish_reason='stop',message={'content':json.dumps(fixture_result('comments',evidence))})],'usage':{'total_tokens':2}})
    real=httpx.AsyncClient
    monkeypatch.setattr(provider.httpx,'AsyncClient',lambda **kwargs:real(transport=httpx.MockTransport(handle),**kwargs))
    result,usage,_=await provider.analyze('comments',[dict(id='test-id',text='费用？')])
    assert len(calls)==1 and usage['total_tokens']==2 and len(result['contributions'])==1


def test_real_native_anonymous_creator_hash_is_reliable_not_nickname_fallback(isolated):
    add_note(user='',cid='one');add_note(user='',cid='two');add_note(user='',cid='three')
    with sqlite3.connect(analytics.db_path()) as db:
        db.execute('ALTER TABLE xhs_note ADD COLUMN creator_hash TEXT')
        db.execute("UPDATE xhs_note SET creator_hash=? WHERE note_id IN ('one','two')",('0123456789abcdef',))
        # Same nickname and a normalized author_id hash do not establish identity.
    d=r.discovery();assert len(d['accounts'])==1 and d['accounts'][0]['count']==2
    assert d['accounts'][0]['basis']=='匿名发布者稳定标识'
    s=r.save(ResearchSpace(name='匿名发布者',kind='account',account_id=d['accounts'][0]['id']),'local')
    assert r.plan(s['id'])['total']==2 and d['unknown_accounts']==2


def test_native_keyword_triggers_preserve_observed_many_to_many(tmp_path):
    path=tmp_path/'native.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE xhs_note(note_id TEXT,time INTEGER,add_ts INTEGER,last_modify_ts INTEGER,source_keyword TEXT)')
        db.execute("INSERT INTO xhs_note VALUES('note',1700000000,1700000000,1700000001,'旅居')")
    schema.migrate(path)
    with sqlite3.connect(path) as db:
        schema.register_functions(db)
        db.execute("UPDATE xhs_note SET source_keyword='旅行',last_modify_ts=1700000002")
        db.execute("UPDATE xhs_note SET source_keyword='旅行',last_modify_ts=1700000003")
        assert db.execute("SELECT count(*) FROM source_keyword_memberships WHERE content_id='note'").fetchone()[0]==2
        assert db.execute('SELECT count(*) FROM analytics_content').fetchone()[0]==1
        db.execute('SAVEPOINT capture')
        db.execute("UPDATE xhs_note SET source_keyword='回滚',last_modify_ts=1700000004")
        db.execute('ROLLBACK TO capture')
        assert db.execute("SELECT count(*) FROM source_keyword_memberships WHERE keyword='回滚'").fetchone()[0]==0


def test_returning_to_old_content_creates_a_new_current_revision(isolated,model):
    add_note(body='原始正文A');s=space();_,first=execute(s)
    add_note(body='修改正文B');_,second=execute(s)
    add_note(body='原始正文A');done,current=execute(s)
    assert not any(c['kind']=='material' for c in done['calls'])
    assert current['id'] not in (first['id'],second['id'])
    assert r.detail(s['id'])['latest']['id']==current['id']
    assert len(r.detail(s['id'])['revisions'])==3


def test_narrow_scope_and_account_work_with_more_than_5000_saved_sources(isolated):
    add_note(cid='zz-target',keyword='特定专题')
    with sqlite3.connect(analytics.db_path()) as db:
        db.executemany("INSERT INTO analytics_content(platform,content_id,title,keyword) VALUES('xhs',?,?,'其他')",[(f'a{i:05d}','无关资料') for i in range(5100)])
    assert r.discovery()['selector_limited']
    found=r.discovery('zz-target');assert found['accounts'] and found['scanned']==1
    account=r.save(ResearchSpace(name='大资料库中的单账号',kind='account',account_id=found['accounts'][0]['id']),'local')
    assert r.plan(account['id'])['total']==1
    single=r.save(ResearchSpace(name='大资料库中的单篇',kind='single',content_id='zz-target'),'local')
    assert r.plan(single['id'])['total']==1
    topic=r.save(ResearchSpace(name='窄专题',kind='topic',keywords=['特定专题']),'local')
    assert r.plan(topic['id'])['total']==1


def test_project_research_is_opt_in_bounded_pinned_and_not_creator_preference(isolated,model):
    from api.services.remix import workflow,context
    from api.services.remix.schemas import JobInput
    add_note();s=space();_,old=execute(s)
    project=projects.save(ProjectInput(title='待创作项目'),'local')
    projects.link(project['id'],ProjectLink(kind='research',resource_id=old['id']))
    kwargs=dict(source='xhs',content_id='fixture-note',project_id=project['id'],request_key=uuid4().hex)
    default=workflow.source_for(JobInput(**kwargs));assert not default.get('project_research')
    frozen=workflow.source_for(JobInput(**kwargs,include_project_research=True))
    entries=frozen['project_research'];assert entries and entries[0]['revision_id']==old['id']
    assert entries[0]['assets'][0]['evidence'][0]['quote']
    assert '待核实' in entries[0]['policy'] and len(r.encode(entries))<8000
    add_note(body='正文已更新');execute(s)
    again=workflow.source_for(JobInput(**kwargs,include_project_research=True))
    assert again['project_research']==entries
    evidence=context.build_evidence(frozen,[])
    assert any(e['kind']=='project_research' for e in evidence)
    assert not any(e['kind']=='writing_preference' for e in evidence)
    assert 'used_research' in next(e['text'] for e in evidence if e['kind']=='coverage')
    with pytest.raises(ValueError):workflow.source_for(JobInput(source='xhs',content_id='fixture-note',include_project_research=True,request_key=uuid4().hex))


def test_research_and_legacy_jobs_share_the_same_queue_limit(isolated):
    from api.services.remix import store
    from api.services.remix.schemas import JobInput
    add_note()
    for i in range(5):
        s=r.save(ResearchSpace(name='排队'+str(i),kind='topic',keywords=['旅行']),'local')
        r.submit(s['id'],ResearchRun(plan_token=r.plan(s['id'])['plan_token'],consent=True,request_key=uuid4().hex),'local')
    with pytest.raises(ValueError,match='队列已满'):
        store.submit(JobInput(source='xhs',content_id='fixture-note',request_key=uuid4().hex),'local',{'title':'单条'})
