import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from pydantic import ValidationError
from playwright.async_api import async_playwright

import config
from api.main import app
from api.schemas import CrawlerStartRequest
from api.services import workbench_store
from api.services.crawler_manager import CrawlerManager
from cmd_arg import parse_cmd
from media_platform.xhs.browser_search import BrowserFilteredSearch, SearchBatch, SearchFilterError, filter_fingerprint, request_matches
from media_platform.xhs.core import XiaoHongShuCrawler
from model.xhs_search_filters import XhsSearchFilters


@pytest.mark.parametrize('change', [{'location':'somewhere'}, {'max_scrolls':0}, {'timeout_seconds':29}, {'typo':True}])
def test_invalid_filters_rejected(change):
    with pytest.raises(ValidationError):
        XhsSearchFilters(**change)


@pytest.mark.parametrize('change', [{'platform':'dy'}, {'crawler_type':'detail'}, {'start_page':2}, {'keywords':''}])
def test_unsupported_request_rejected(change):
    with pytest.raises(ValidationError):
        CrawlerStartRequest.model_validate({'platform':'xhs', 'keywords':'露营', 'xhs_filters':{}, **change})


def test_contract_options_and_command():
    with TestClient(app) as client:
        groups=client.get('/api/config/xhs-search-filters').json()['groups']
    assert len(groups)==5
    request=CrawlerStartRequest(platform='xhs', keywords='露营', xhs_filters=XhsSearchFilters(publish_time='week'))
    command=CrawlerManager()._build_command(request)
    filters=json.loads(command[command.index('--xhs_filters')+1])
    assert filters['publish_time']=='week'
    assert '--xhs_filters' not in CrawlerManager()._build_command(CrawlerStartRequest(platform='bili'))


@pytest.mark.asyncio
async def test_cli_contract_and_reset():
    original={k:v for k,v in vars(config).items() if k.isupper()}
    try:
        await parse_cmd(['--platform','xhs','--type','search','--keywords','露营','--xhs_filters','{"note_type":"image"}'])
        assert config.XHS_SEARCH_FILTERS['note_type']=='image'
        await parse_cmd(['--platform','bili'])
        assert config.XHS_SEARCH_FILTERS is None
    finally:
        for k,v in original.items():setattr(config,k,v)


def test_progress_is_persisted_without_raw_cookie(tmp_path,monkeypatch):
    monkeypatch.setattr(workbench_store,'DB_PATH',tmp_path/'state.db')
    manager=CrawlerManager();manager.task={'id':'test','outcome':'running'}
    manager._create_log_entry('MC_PROGRESS '+json.dumps({'phase':'verifying','keyword':'露营','verified_filters':{'发布时间':'一周内'}}))
    assert workbench_store.get_document('task','test')['progress']['verified_filters']=={'发布时间':'一周内'}
    manager._create_log_entry('MC_PROGRESS {"phase":"fake","keyword":"test"}')
    assert manager.task['progress']['phase']=='verifying'


def test_fingerprint_ignores_page_but_detects_filter_change():
    assert filter_fingerprint({'page':1,'sort':'latest'})==filter_fingerprint({'page':2,'sort':'latest'})
    assert filter_fingerprint({'sort':'latest'})!=filter_fingerprint({'sort':'general'})
    assert not request_matches(SimpleNamespace(url='https://evil.test/api/sns/web/v1/search/notes',method='POST',post_data_json={'keyword':'a'}),'a')


@pytest.mark.asyncio
@pytest.mark.parametrize('items,limit,expected', [([{'id':'one','xsec_token':'t'},{'id':'one','xsec_token':'t'},{'id':'two','xsec_token':'u'}],1,['one']),([{'id':'last','xsec_token':'t'}],5,['last'])])
async def test_core_keeps_last_page_deduplicates_and_honors_small_limit(tmp_path,monkeypatch,items,limit,expected):
    monkeypatch.setattr(config,'XHS_SEARCH_FILTERS',XhsSearchFilters().model_dump())
    monkeypatch.setattr(config,'KEYWORDS','露营')
    monkeypatch.setattr(config,'CRAWLER_MAX_NOTES_COUNT',limit)
    monkeypatch.setattr(config,'SAVE_DATA_PATH',str(tmp_path))
    monkeypatch.setattr(config,'XHS_INTERNATIONAL',False)
    class FakeSearch:
        stop_reason='platform_end'
        def __init__(self,page,filters,report):self.report=report
        async def apply(self,keyword):
            self.report('collecting',verified_filters=XhsSearchFilters().labels())
            return SearchBatch({},items,False)
        async def next_batch(self):return None
    import media_platform.xhs.browser_search as browser_module
    import store.xhs as xhs_store
    monkeypatch.setattr(browser_module,'BrowserFilteredSearch',FakeSearch)
    saved=AsyncMock();monkeypatch.setattr(xhs_store,'update_xhs_note',saved)
    crawler=XiaoHongShuCrawler();crawler.context_page=SimpleNamespace(goto=AsyncMock())
    async def detail(**kwargs):return {'note_id':kwargs['note_id']}
    crawler.get_note_detail_async_task=AsyncMock(side_effect=detail)
    crawler.download_media=AsyncMock();crawler.batch_get_note_comments=AsyncMock()
    await crawler.search()
    assert [call.args[0]['note_id'] for call in saved.call_args_list]==expected
    assert config.CRAWLER_MAX_NOTES_COUNT==limit
    metadata=json.loads(next((tmp_path/'xhs'/'search_metadata').glob('*.json')).read_text())
    assert metadata['collected']==len(expected)
    assert metadata['verified_filters']['发布时间']=='不限'
    assert 'xsec_token' not in json.dumps(metadata)


@pytest_asyncio.fixture
async def browser_page():
    """Real Chrome, but all network requests are fulfilled locally by fixtures."""
    async with async_playwright() as playwright:
        browser=await playwright.chromium.launch(executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless=True)
        page=await browser.new_page(viewport={'width':1200,'height':900})
        html=(Path(__file__).parent/'fixtures'/'xhs_search.html').read_text()
        async def route_handler(route):
            if route.request.is_navigation_request():
                await route.fulfill(content_type='text/html',body=html)
            elif '/api/sns/web/v1/search/notes' in route.request.url:
                body=route.request.post_data_json
                await route.fulfill(json={'success':True,'code':0,'data':{'items':[{'id':'page'+str(body['page']),'xsec_token':'test'}],'has_more':body['page']<2}})
            else:await route.abort()
        await page.route('**/*',route_handler)
        await page.goto('https://www.xiaohongshu.com')
        yield page
        await browser.close()


@pytest.mark.asyncio
async def test_real_browser_controls_verify_and_paginate(browser_page):
    events=[]
    search=BrowserFilteredSearch(browser_page,XhsSearchFilters(sort_by='latest',note_type='image',publish_time='week',search_scope='following',location='same_city'),lambda phase,**fields:events.append((phase,fields)))
    batch=await search.apply('露营')
    assert batch.body['filters']['发布时间']=='一周内'
    assert search.verified['排序依据']=='最新'
    assert search.verified['位置距离']=='同城'
    next_batch=await search.next_batch()
    assert next_batch.items[0]['id']=='page2'
    assert await search.next_batch() is None
    assert any(phase=='verifying' for phase,_ in events)


@pytest.mark.asyncio
async def test_missing_browser_option_fails_closed(browser_page):
    # Simulate a site revision that removed a requested option.
    await browser_page.locator('.filters').filter(has=browser_page.get_by_text('位置距离',exact=True)).locator('.tags').filter(has_text='同城').evaluate_all('(nodes)=>nodes.forEach(n=>n.remove())')
    search=BrowserFilteredSearch(browser_page,XhsSearchFilters(location='same_city'),lambda *args,**kwargs:None)
    with pytest.raises(SearchFilterError,match='筛选项不可用'):
        await search.apply('露营')


@pytest.mark.asyncio
async def test_filter_tampering_is_detected(browser_page):
    search=BrowserFilteredSearch(browser_page,XhsSearchFilters(sort_by='latest'),lambda *args,**kwargs:None)
    await search.apply('露营')
    await browser_page.locator('.filters').filter(has=browser_page.get_by_text('排序依据',exact=True)).locator('.tags.active:not([aria-hidden])').evaluate_all('(nodes)=>nodes.forEach(n=>n.classList.remove("active"))')
    with pytest.raises(SearchFilterError,match='筛选校验失败'):
        await search.verify()


@pytest.mark.asyncio
async def test_local_cdp_discovery_ignores_system_proxy(monkeypatch):
    from unittest.mock import MagicMock
    import tools.cdp_browser as cdp
    response=MagicMock(status_code=200)
    response.json.return_value={'webSocketDebuggerUrl':'ws://127.0.0.1:9222/devtools/browser/test'}
    client=MagicMock();client.get=AsyncMock(return_value=response)
    client.__aenter__=AsyncMock(return_value=client);client.__aexit__=AsyncMock(return_value=False)
    factory=MagicMock(return_value=client)
    monkeypatch.setattr(cdp.httpx,'AsyncClient',factory)
    result=await cdp.CDPBrowserManager()._get_browser_websocket_url(9222)
    factory.assert_called_once_with(trust_env=False)
    client.get.assert_awaited_once_with('http://127.0.0.1:9222/json/version',timeout=10)
    assert result.startswith('ws://127.0.0.1:9222')


@pytest.mark.parametrize('host,path', [
    ('edith.xiaohongshu.com','/api/sns/web/v1/search/notes'),
    ('www.xiaohongshu.com','/api/sns/web/v1/search/notes'),
    ('so.xiaohongshu.com','/api/sns/web/v2/search/notes'),
])
def test_observed_search_endpoints(host,path):
    request=SimpleNamespace(url='https://'+host+path,method='POST',post_data_json={'keyword':'露营'})
    assert request_matches(request,'露营')
    assert not request_matches(request,'另一个关键词')


def test_v2_filter_flags_are_part_of_fingerprint():
    assert filter_fingerprint({'ext_flags':['week']})!=filter_fingerprint({'ext_flags':['all']})
