"""Account isolation, comment counts and manual result cohorts; no network/model calls."""
import json
from datetime import datetime,timedelta,timezone

import pytest

from api.services.remix import profiles,knowledge,context,topics,store,publications,providers,workflow
from api.services.remix.schemas import ProfileInput,KnowledgeInput,JobInput,PublicationInput,ObservationInput,VersionInput
from tests.test_content_remix import isolated,client,report,submit,wait

NOW=datetime.now(timezone.utc)-timedelta(hours=1)
PUBLISHED=NOW-timedelta(hours=72)


def profile(name='账号A'):
    return profiles.save(ProfileInput(name=name,audience='长期旅居用户',tone='客观',topic='旅居'),'fixture')


def finished(p,topic='旅居',framework='信息筛选'):
    payload=JobInput(source='xhs',content_id='fixture-note',profile_id=p['id'] if p else '',brief={'topic':topic,'framework':framework},consent=True,request_key='fixture-'+str(len(store.listing()['items'])).zfill(16))
    source=workflow.source_for(payload);job=store.submit(payload,'fixture',source)
    evidence=workflow.build_evidence(source,[])
    r=report(evidence);store.update(job['id'],status='completed',report=r,evidence=evidence)
    version=store.save_version(job['id'],VersionInput(rewrite_index=0,title='实际标题',content='实际正文',request_key='version-fixture-1234'),'fixture')
    return job,version


def publish(p,score=10,*,topic='旅居',age=72,promoted=False,date=PUBLISHED,views=100,framework='信息筛选'):
    job,version=finished(p,topic,framework)
    item=publications.save(job['id'],PublicationInput(version_id=version['id'],url='https://www.xiaohongshu.com/explore/'+version['id'],published_at=date.isoformat(),topic=topic),'fixture')
    o=publications.observe(item['id'],ObservationInput(observed_at=(date+timedelta(hours=age)).isoformat(),favorites=score,views=views,promoted=promoted,request_key='observation-key-1234'),'fixture')
    return item,o


def test_profile_versions_and_frozen_job_context(isolated):
    p=profile();payload=JobInput(source='xhs',content_id='fixture-note',profile_id=p['id'],request_key='snapshot-profile-1234')
    snapshot=workflow.source_for(payload)
    assert snapshot['creative_brief']['tone']=='客观' and snapshot['profile']['version']==1
    new=profiles.save(ProfileInput(name='账号新名',tone='直接',version=1),'fixture',p['id'])
    assert new['version']==2 and snapshot['profile']['name']=='账号A'
    assert workflow.source_for(payload)['creative_brief']['tone']=='直接'
    assert len(profiles.history(p['id']))==2
    with pytest.raises(ValueError,match='刷新'):profiles.save(ProfileInput(name='过期编辑',version=1),'fixture',p['id'])
    profiles.save(ProfileInput(name='停用账号',enabled=False,version=2),'fixture',p['id'])
    with pytest.raises(ValueError,match='停用'):workflow.source_for(payload)


def test_knowledge_profile_isolation_and_global_opt_in(isolated):
    a=profile();b=profile('账号B')
    for owner,title in [(a['id'],'A经验'),(b['id'],'B经验'),('','通用经验')]:
        knowledge.save(KnowledgeInput(profile_id=owner,title=title,content='仅适用此作用域'),'fixture')
    brief={'platform':'xhs','topic':'旅居'}
    assert [k['title'] for k in knowledge.relevant(brief,a['id'])]==['A经验']
    assert {k['title'] for k in knowledge.relevant(brief,a['id'],True)}=={'A经验','通用经验'}
    assert [k['title'] for k in knowledge.relevant(brief)]==['通用经验']
    source=workflow.source_for(JobInput(source='xhs',content_id='fixture-note',profile_id=a['id'],request_key='account-scope-123456'))
    e=workflow.build_evidence(source,[])
    assert any(x['kind']=='account_profile' for x in e)
    assert 'B经验' not in json.dumps(e,ensure_ascii=False)


def test_comment_topic_counts_are_multilabel_and_only_included_evidence():
    e=[dict(id='E1',kind='comment',text='月租多少钱，可以养宠物吗？'),dict(id='E2',kind='comment',text='交通如何，停车方便吗？'),dict(id='E3',kind='comment',text='谢谢分享'),dict(id='E4',kind='body',text='价格多少钱')]
    result=topics.build(e);groups={g['topic']:g for g in result['groups']}
    assert result['sample_size']==3 and groups['费用与支付']['count']==1
    assert groups['门槛与条件']['count']==1 and groups['其他反馈']['evidence_ids']==['E3']
    assert all('E4' not in g['evidence_ids'] for g in groups.values())
    assert sum(g['count'] for g in groups.values())>result['sample_size']


def test_cohort_scope_age_promotion_null_zero_and_median(isolated):
    a=profile();b=profile('其他账号')
    target,_=publish(a,20)
    first,_=publish(a,0,framework='需求解答');second,_=publish(a,10)
    publish(b,999);publish(a,999,topic='其他题材');publish(a,999,age=1);publish(a,999,promoted=True)
    publish(a,None);publish(a,999,date=PUBLISHED-timedelta(days=31))
    result=publications.review(target['id'])
    assert result['peer_count']==2 and result['baseline_median']==5
    assert result['relative_change_pct']==300
    assert result['rates']['favorites']==.2
    assert {p['publication_id'] for p in result['peers']}=={first['id'],second['id']}
    assert result['excluded']['scope']==2 and result['excluded']['promotion']==1 and result['excluded']['missing']==1
    assert len(result['framework_groups'])==2
    assert all(g['count']==1 for g in result['framework_groups'])


def test_missing_account_or_topic_does_not_mix_unassigned_records(isolated):
    target,_=publish(None,10)
    assert not publications.review(target['id'])['available']
    target,_=publish(profile(),10,topic='')
    assert not publications.review(target['id'])['available']


def test_zero_baseline_and_zero_denominator_are_not_fake_growth(isolated):
    a=profile();target,_=publish(a,0,views=0);publish(a,0)
    r=publications.review(target['id'])
    assert r['baseline_median']==0 and r['relative_change_pct'] is None and r['rates']['favorites'] is None


def test_multiple_observations_closest_age_and_duplicate_retry(isolated):
    a=profile();p,first=publish(a,10,age=71)
    payload=ObservationInput(observed_at=(PUBLISHED+timedelta(hours=72)).isoformat(),favorites=30,promoted=False,request_key='second-observation-123')
    second=publications.observe(p['id'],payload,'fixture')
    assert publications.observe(p['id'],payload,'fixture')['id']==second['id']
    assert len(publications.observations(p['id']))==2
    assert publications.review(p['id'])['value']==30
    with pytest.raises(ValueError,match='重复'):publications.observe(p['id'],payload.model_copy(update={'favorites':40}),'fixture')
    with pytest.raises(ValueError,match='早于'):publications.observe(p['id'],ObservationInput(observed_at=(PUBLISHED-timedelta(hours=1)).isoformat(),likes=1,request_key='invalid-observation-123'),'fixture')


@pytest.mark.parametrize('url',['http://www.xiaohongshu.com/explore/test','https://evil.test/post/test','https://user:password@www.xiaohongshu.com/explore/test','https://127.0.0.1/post','https://[fd00::1]/post'])
def test_publication_links_are_scoped_and_not_local(url):
    with pytest.raises(ValueError):publications.public_url(url,'xhs')


def test_publication_identity_cannot_change_after_observation(isolated):
    p,_=publish(profile(),10)
    data=PublicationInput(**{k:p[k] for k in ('version_id','url','published_at','content_type','topic','notes','version')})
    updated=publications.save(p['job_id'],data.model_copy(update={'notes':'核对后的备注'}),'fixture',p['id'])
    assert updated['version']==2
    with pytest.raises(ValueError,match='口径'):
        publications.save(p['job_id'],data.model_copy(update={'version':2,'topic':'新题材'}),'fixture',p['id'])
    with pytest.raises(ValueError,match='刷新'):
        publications.save(p['job_id'],data,'fixture',p['id'])


def test_api_roundtrip_profiles_topics_publication_and_observation(client,monkeypatch):
    async def model(evidence):return report(evidence),{},'fixture'
    monkeypatch.setattr(providers,'analyze',model)
    p=client.post('/api/remix/profiles',json={'name':'测试账号','topic':'旅行','tone':'简洁'}).json()
    j=submit(client,profile_id=p['id'],pasted_comments='多少钱？\n交通怎么安排？')
    final=wait(client,j['id'])
    assert final['source']['profile']['id']==p['id']
    topic=client.get(f"/api/remix/jobs/{j['id']}/comment-topics").json()
    assert topic['sample_size']==2 and topic['groups']
    v=client.post(f"/api/remix/jobs/{j['id']}/versions",json=dict(rewrite_index=0,title='测试发布',content='测试正文',request_key='api-version-key-123')).json()
    pub=client.post(f"/api/remix/jobs/{j['id']}/publications",json=dict(version_id=v['id'],url='https://www.xiaohongshu.com/explore/fixture-post?tracking=removed',published_at=PUBLISHED.isoformat(),topic='旅行')).json()
    assert pub['profile_id']==p['id'] and '?' not in pub['url']
    obs=client.post(f"/api/remix/publications/{pub['id']}/observations",json=dict(observed_at=NOW.isoformat(),favorites=0,promoted=False,request_key='api-observation-key-123'))
    assert obs.status_code==200 and obs.json()['favorites']==0 and obs.json()['views'] is None
    r=client.get(f"/api/remix/publications/{pub['id']}/review").json()
    assert r['available'] and r['peer_count']==0
    assert client.post('/api/remix/knowledge',json=dict(profile_id=p['id'],title='待验证的观察',content='这是描述性比较，继续记录反例',kind='rule',source_job_id=j['id'],source_version_id=v['id'])).status_code==200
    assert client.get(f"/api/remix/profiles/{p['id']}/history").status_code==200


def test_same_publication_cannot_be_counted_twice_across_jobs(isolated):
    p,_=publish(profile(),10)
    other,version=finished(profile('另一个账号'))
    with pytest.raises(ValueError,match='重复计数'):
        publications.save(other['id'],PublicationInput(version_id=version['id'],url=p['url'],published_at=PUBLISHED.isoformat(),topic='旅居'),'fixture')


@pytest.mark.parametrize('url',['https://[fd00::1]/post','https://[2001:4860:4860::8888]/post','https://127.0.0.1/post','https://localhost/post'])
def test_other_platform_requires_public_domain(url):
    with pytest.raises(ValueError):publications.public_url(url,'other')


def test_legacy_report_can_bind_actual_publication_account_without_model_call(isolated):
    job,version=finished(None)
    account=profile()
    record=publications.save(job['id'],PublicationInput(profile_id=account['id'],version_id=version['id'],url='https://www.xiaohongshu.com/explore/legacy-published',published_at=PUBLISHED.isoformat(),topic='旅居'),'fixture')
    assert record['profile_id']==account['id'] and record['profile_snapshot']['version']==1
    assert store.get(job['id'])['source']['profile'] is None


def test_unknown_promotion_is_not_assumed_organic(isolated):
    p,_=publish(profile(),10)
    publications.observe(p['id'],ObservationInput(observed_at=(PUBLISHED+timedelta(hours=72)).isoformat(),favorites=20,promoted=None,request_key='unknown-promotion-123'),'fixture')
    result=publications.review(p['id'])
    assert not result['available'] and '推广状态未知' in result['reason']
