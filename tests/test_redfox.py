"""MCP fixtures only: no real collection or browser collector execution."""
import json
import sys
import subprocess
import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from api.schemas.crawler import CrawlerStartRequest
from api.services import redfox, workbench_store
from api.services.crawler_manager import CrawlerManager


def request(**overrides):
    return CrawlerStartRequest(platform='xhs', collection_method='redfox_mcp',
        mcp_tool='xiaohongshu_search_articles', mcp_arguments={'keyword':'fixture'}, save_option='json', **overrides)


def test_catalog_and_schema_validation():
    assert len(redfox.TOOLS) == 39
    for tool in redfox.TOOLS:
        assert tool['platform'] in redfox.PLATFORMS
        assert tool['name'].startswith(redfox.PLATFORMS[tool['platform']] + '_')
    for changes in ({'mcp_tool':'publish'}, {'platform':'wb'}, {'mcp_arguments':{}},
                    {'mcp_arguments':{'keyword':123}}, {'mcp_arguments':{'keyword':'x','token':'secret'}},
                    {'save_option':'sqlite'}, {'cookies':'private'}, {'enable_media':True}):
        payload=request().model_dump();payload.update(changes)
        with pytest.raises(ValidationError):
            CrawlerStartRequest(**payload)
    with pytest.raises(ValueError, match='至少填写一个'):
        redfox.validate_tool('xhs','xiaohongshu_get_work',{})


def test_persistent_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv('REDFOX_API_KEY',raising=False)
    monkeypatch.setenv('CODEX_HOME',str(tmp_path))
    (tmp_path/'config.toml').write_text('[mcp_servers.redfox-xiaohongshu-mcp.http_headers]\nREDFOX_API_KEY="fixture-key"\n')
    assert redfox.credential('xhs') == 'fixture-key'
    monkeypatch.setenv('REDFOX_API_KEY','env-key')
    assert redfox.credential('xhs') == 'env-key'
    monkeypatch.delenv('REDFOX_API_KEY')
    (tmp_path/'config.toml').write_text('invalid[')
    assert redfox.credential('xhs') == ''


@pytest.mark.asyncio
@pytest.mark.parametrize('sse',[False,True])
async def test_streamable_http_session_and_call_once(sse):
    calls=[]
    def handle(req):
        body=json.loads(req.content);calls.append(body)
        assert req.headers['REDFOX_API_KEY']=='fixture-key'
        if body['method']=='initialize':
            return httpx.Response(200,json={'jsonrpc':'2.0','id':body['id'],'result':{'protocolVersion':'2025-03-26'}},headers={'Mcp-Session-Id':'fixture-session'})
        assert req.headers['mcp-session-id']=='fixture-session'
        if body['method']=='notifications/initialized':
            return httpx.Response(202)
        result={'jsonrpc':'2.0','id':body['id'],'result':{'content':[{'type':'text','text':'{"taskId":"remote-fixture"}'}]}}
        if sse:
            return httpx.Response(200,headers={'content-type':'text/event-stream'},text='event: message\ndata: '+json.dumps({'jsonrpc':'2.0','method':'notifications/progress'})+'\n\ndata: '+json.dumps(result)+'\n\n')
        return httpx.Response(200,json=result)
    client=redfox.RedFoxClient('xhs','fixture-key',httpx.MockTransport(handle))
    result=await client.call('xiaohongshu_search_articles',{'keyword':'fixture'})
    assert 'remote-fixture' in result['content'][0]['text']
    assert [c['method'] for c in calls]==['initialize','notifications/initialized','tools/call']


@pytest.mark.asyncio
async def test_errors_are_not_retried_or_leaked():
    calls=[]
    def handle(req):
        calls.append(req)
        return httpx.Response(401,text='secret-key, upstream diagnostic')
    with pytest.raises(ValueError,match='HTTP 401') as error:
        await redfox.RedFoxClient('xhs','secret-key',httpx.MockTransport(handle)).call('any',{})
    assert len(calls)==1 and 'secret-key' not in str(error.value)
    with pytest.raises(ValueError,match='执行失败'):
        redfox.RedFoxClient.result({'result':{'isError':True,'content':[{'text':'secret-key'}]}})


@pytest.mark.asyncio
async def test_collect_preserves_async_ids_and_saves_only_result(tmp_path, monkeypatch):
    monkeypatch.setenv('REDFOX_API_KEY','fixture-secret')
    monkeypatch.setenv('MEDIACRAWLER_TASK_ID','a'*32)
    call=AsyncMock(return_value={'content':[{'type':'text','text':'{"taskId":"remote-id","cursor":"next"}'}]})
    monkeypatch.setattr(redfox.RedFoxClient,'call',call)
    await redfox.collect(request(),tmp_path)
    payload=json.loads((tmp_path/'xhs'/'redfox'/('a'*32+'.json')).read_text())
    assert payload[0]['source']=='redfox_mcp'
    assert 'remote-id' in payload[0]['result']['content'][0]['text']
    assert 'mcp_arguments' not in payload[0]
    call.assert_awaited_once()


@pytest.mark.asyncio
async def test_manager_routes_mcp_through_supervisor(tmp_path,monkeypatch):
    monkeypatch.setattr(workbench_store,'DB_PATH',tmp_path/'workbench.sqlite3')
    monkeypatch.setenv('REDFOX_API_KEY','fixture-key')
    manager=CrawlerManager()
    assert manager._build_command(request())==[sys.executable,'-m','api.services.redfox']
    captured={}
    def spawn(command,**kwargs):
        captured.update(command=command,**kwargs)
        raise OSError('fixture: do not execute real collection')
    monkeypatch.setattr(subprocess,'Popen',spawn)
    assert not await manager.start(request())
    assert captured['command'][1:3]==['-m','api.task_runner']
    assert json.loads(captured['env']['MEDIACRAWLER_MCP_REQUEST'])['mcp_tool']=='xiaohongshu_search_articles'
    assert 'fixture-key' not in ' '.join(captured['command'])
    assert manager.task['collection_method']=='redfox_mcp'
    assert manager.task['outcome']=='failed'
    assert 'membership_version' not in manager.task


def test_catalog_endpoint_does_not_contact_redfox(monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app
    monkeypatch.setattr(redfox,'credential',lambda platform:'fixture-secret')
    monkeypatch.setattr(redfox.RedFoxClient,'call',AsyncMock(side_effect=AssertionError('no remote call')))
    response=TestClient(app).get('/api/crawler/redfox/tools')
    assert response.status_code==200
    assert response.json()['configured']['youtube'] is True
    assert len(response.json()['tools'])==39
    assert 'fixture-secret' not in response.text


@pytest.mark.asyncio
async def test_mcp_supervised_success_with_local_response(tmp_path, monkeypatch):
    monkeypatch.setattr(workbench_store,'DB_PATH',tmp_path/'workbench.sqlite3')
    monkeypatch.setenv('REDFOX_API_KEY','fixture-key')
    manager=CrawlerManager()
    # Run the real supervisor/collector lifecycle with a fixture tool response.
    code = f'''
import asyncio, os
from unittest.mock import AsyncMock
from api.schemas.crawler import CrawlerStartRequest
from api.services import redfox
redfox.RedFoxClient.call = AsyncMock(return_value={{'content':[{{'type':'text','text':'fixture result'}}]}})
asyncio.run(redfox.collect(CrawlerStartRequest.model_validate_json(os.environ['MEDIACRAWLER_MCP_REQUEST']), {str(tmp_path)!r}))
'''
    monkeypatch.setattr(manager,'_build_command',lambda config:[sys.executable,'-c',code])
    assert await manager.start(request())
    await asyncio.wait_for(manager._read_task,10)
    assert manager.task['outcome']=='completed'
    assert manager.task['exit_code']==0 and manager.status=='idle'
    assert (tmp_path/'xhs'/'redfox'/f'{manager.task_id}.json').is_file()
    saved=workbench_store.list_documents('task')[0]
    assert saved['collection_method']=='redfox_mcp' and saved['mcp_tool']=='xiaohongshu_search_articles'
    assert any('已保存 MCP 原始结果' in entry.message for entry in manager.logs)
