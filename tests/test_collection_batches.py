"""No live collection: exact batch membership, legacy provenance and saved-note reuse."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
import config
from api.services import collection_batches as batches,workbench_store,analytics
from api.services.remix import research
from api.services.remix.research_schemas import ResearchSpace
from api.services.crawler_manager import CrawlerManager
from model.xhs_search_filters import XhsSearchFilters
from media_platform.xhs.core import XiaoHongShuCrawler
from media_platform.xhs.browser_search import SearchBatch
from tests.test_content_remix import isolated,client
from tests.test_remix_research import add_note


def task(exact=True):
    item=dict(id=uuid4().hex,platform='xhs',target='测试关键词',outcome='completed',started_at='2026-10-26T01:00:00Z',crawler_type='search',finished_at=None,exit_code=0)
    if exact:item['membership_version']=1
    workbench_store.save_document('task',item)
    return item


def test_exact_membership_dedup_current_versions_and_keyword_relations(isolated):
    add_note();t=task()
    batches.record(t['id'],batches.ContentEvent(content_id='second',keyword='a',title='第一版',action='new'))
    batches.record(t['id'],batches.ContentEvent(content_id='second',keyword='b',title='第一版',action='reused'))
    batches.record(t['id'],batches.ContentEvent(content_id='second',keyword='b',title='第二版',action='reused'))
    result=batches.members(t['id'])
    assert result['total']==1 and result['exact'] and result['counts']=={'new':1}
    assert result['items'][0]['keywords']==['a','b'] and result['items'][0]['available']
    batches.observe_keyword('second','新关联')
    assert '新关联' in research.discovery()['keywords']
    assert batches.choices(task_id=t['id'])['total']==1
    assert batches.choices(task_id=t['id'],search='不存在')['total']==0


def test_legacy_logs_only_trace_attempted_ids_no_keyword_guess(isolated):
    add_note();add_note(cid='not-this-task');t=task(False)
    workbench_store.append_log(t['id'],'now','info',"[store.xhs.update_xhs_note] xhs note: {'note_id': 'second', 'source_keyword': '旅行'}")
    result=batches.members(t['id'])
    assert not result['exact'] and result['total']==1
    assert result['items'][0]['content_id']=='second' and result['counts']=={'historical':1}
    with pytest.raises(ValueError,match='精确'):batches.selected_ids(t['id'])
    empty=task(False);assert batches.members(empty['id'])['total']==0


def test_selected_and_task_research_scopes_are_exact_not_keyword_unions(isolated):
    add_note();add_note(cid='third');t=task()
    batches.record(t['id'],batches.ContentEvent(content_id='second'))
    scoped=research.save(ResearchSpace(name='本次批次',kind='topic',selection_mode='task',task_id=t['id']),'local')
    assert research.plan(scoped['id'])['total']==1
    batches.record(t['id'],batches.ContentEvent(content_id='third'))
    assert research.plan(scoped['id'])['total']==2
    workbench_store.delete_task(t['id'])
    assert research.plan(scoped['id'])['total']==2
    assert batches.members(t['id'])['task']['history_deleted']
    selected=research.save(ResearchSpace(name='勾选资料',kind='topic',selection_mode='selected',include_ids=['second'],keywords=['旅行']),'local')
    assert research.plan(selected['id'])['total']==1
    with pytest.raises(ValueError):ResearchSpace(name='未选择',kind='topic',selection_mode='selected')


def test_content_event_persists_counts_and_sanitizes_titles(isolated):
    add_note();t=task();manager=CrawlerManager();manager.task=t;manager.task_id=t['id']
    event=batches.ContentEvent(content_id='second',action='reused',title='password=secret')
    manager._create_log_entry('MC_CONTENT '+event.model_dump_json())
    saved=workbench_store.get_document('task',t['id'])
    assert saved['dataset_counts']==dict(reused=1,total=1)
    with workbench_store.connect() as db:
        assert 'secret' not in db.execute('SELECT title FROM collection_memberships').fetchone()[0]


def test_dataset_api_source_choices_and_live_ui_routes(client):
    add_note();t=task();batches.record(t['id'],batches.ContentEvent(content_id='second',action='reused'))
    response=client.get(f"/api/workbench/tasks/{t['id']}/dataset")
    assert response.status_code==200 and response.json()['total']==1
    assert client.get('/api/remix/research/source-choices',params={'task_id':t['id']}).json()['total']==1
    assert client.get('/api/workbench/tasks/missing/dataset').status_code==404
    options=client.get('/api/config/xhs-search-filters').json()
    assert options['limits']['max_notes_count']==config.CRAWLER_MAX_NOTES_COUNT


@pytest.mark.asyncio
@pytest.mark.parametrize('refresh',[False,True])
async def test_browser_reuses_existing_without_refetch_and_preserves_batch_metadata(tmp_path,monkeypatch,refresh):
    from api.routers import workbench
    import media_platform.xhs.browser_search as browser_module
    import store.xhs as xhs_store
    monkeypatch.setattr(config,'XHS_SEARCH_FILTERS',XhsSearchFilters(refresh_existing=refresh,max_scrolls=30).model_dump())
    monkeypatch.setattr(config,'KEYWORDS','测试');monkeypatch.setattr(config,'START_PAGE',2)
    monkeypatch.setattr(config,'CRAWLER_MAX_NOTES_COUNT',1);monkeypatch.setattr(config,'SAVE_DATA_PATH',str(tmp_path));monkeypatch.setattr(config,'SAVE_DATA_OPTION','sqlite')
    monkeypatch.setattr(config,'ENABLE_GET_MEDIA',True);monkeypatch.setattr(config,'XHS_INTERNATIONAL',False)
    class FakeSearch:
        stop_reason='platform_end'
        def __init__(self,page,filters,report):
            self.report=report;assert filters.max_scrolls==30
        async def apply(self,keyword):return SearchBatch({},[dict(id='skipped',xsec_token='secret')],True)
        async def next_batch(self):return SearchBatch({},[dict(id='existing',xsec_token='secret')],False)
    monkeypatch.setattr(browser_module,'BrowserFilteredSearch',FakeSearch)
    monkeypatch.setattr(batches,'existing_note',lambda cid:dict(note_id=cid,title='已保存',image_list='https://cdn.test/a.jpg'))
    observed=[];monkeypatch.setattr(batches,'observe_keyword',lambda cid,kw:observed.append((cid,kw)))
    monkeypatch.setattr(batches,'emit',lambda *args:None)
    saved=AsyncMock();monkeypatch.setattr(xhs_store,'update_xhs_note',saved)
    crawler=XiaoHongShuCrawler();crawler.context_page=SimpleNamespace(goto=AsyncMock())
    crawler.get_note_detail_async_task=AsyncMock(return_value=dict(note_id='existing'))
    crawler.download_media=AsyncMock();crawler.batch_get_note_comments=AsyncMock()
    await crawler.search()
    assert crawler.get_note_detail_async_task.await_count==int(refresh)
    assert crawler.batch_get_note_comments.await_count==int(refresh)
    assert saved.await_count==int(refresh) and crawler.download_media.await_count==1
    meta=json.loads(next((tmp_path/'xhs/search_metadata').glob('*.json')).read_text())
    assert meta['start_page']==2 and meta['requested_options']['max_scrolls']==30
    assert meta['skipped_batches']==1 and meta['reused']==int(not refresh) and meta['refreshed']==int(refresh)
    assert [m['content_id'] for m in meta['members']]==['existing']
    assert 'secret' not in json.dumps(meta)


@pytest.mark.asyncio
@pytest.mark.parametrize('fail',[False,True])
async def test_api_search_honors_non_page_multiple_limit_and_last_page(tmp_path,monkeypatch,fail):
    import store.xhs as xhs_store
    monkeypatch.setattr(config,'CRAWLER_MAX_NOTES_COUNT',30);monkeypatch.setattr(config,'START_PAGE',2)
    monkeypatch.setattr(config,'KEYWORDS','测试');monkeypatch.setattr(config,'SAVE_DATA_OPTION','sqlite')
    monkeypatch.setattr(config,'ENABLE_GET_MEDIA',False);monkeypatch.setattr(config,'CRAWLER_MAX_SLEEP_SEC',0)
    monkeypatch.setattr(batches,'existing_note',lambda cid:dict(note_id=cid,title='缓存') if cid=='note0' else None)
    monkeypatch.setattr(batches,'observe_keyword',lambda *args:None);events=[];monkeypatch.setattr(batches,'emit',lambda *args:events.append(args))
    saved=AsyncMock();monkeypatch.setattr(xhs_store,'update_xhs_note',saved)
    crawler=XiaoHongShuCrawler();crawler.xhs_client=SimpleNamespace(get_note_by_keyword=AsyncMock(side_effect=[
        {'items':[dict(id='note'+str(i),xsec_token='fixture') for i in range(20)],'has_more':True},
        {'items':[dict(id='note'+str(i),xsec_token='fixture') for i in range(20,40)],'has_more':False}]))
    async def detail(**kwargs):return None if fail else dict(note_id=kwargs['note_id'])
    crawler.get_note_detail_async_task=AsyncMock(side_effect=detail);crawler.download_media=AsyncMock();crawler.batch_get_note_comments=AsyncMock()
    await crawler.search_with_api()
    assert saved.await_count==(0 if fail else 29) and len(events)==1 and config.CRAWLER_MAX_NOTES_COUNT==30
    assert [c.kwargs['page'] for c in crawler.xhs_client.get_note_by_keyword.call_args_list]==[2,3]
