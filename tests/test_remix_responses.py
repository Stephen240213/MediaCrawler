"""Model response compatibility and failures; HTTP is always intercepted locally."""
import json

import httpx
import pytest

from api.services.remix import providers
from tests.test_content_remix import report

EVIDENCE = [dict(id='E0001',kind='title',text='测试素材')]


def completion(content=None, reason='stop'):
    return dict(choices=[dict(finish_reason=reason,message=dict(content=content))])


@pytest.mark.parametrize('data,message',[
    (completion('{"summary":', 'length'),'截断'),
    (completion(json.dumps(report(EVIDENCE)), 'length'),'截断'),
    (completion('', 'stop'),'空正文'),
    (completion(None, 'stop'),'空正文'),
    (completion('  ', 'stop'),'空正文'),
    (completion('{broken json}', 'stop'),'JSON'),
    (completion('blocked', 'content_filter'),'未接受'),
    ({},'可用'),
    ({'choices':[]},'可用'),
    ({'choices':[None]},'消息结构'),
    ({'choices':[{'message':None}]},'消息结构'),
    ({'error':{'message':'private-api-key'}},'可用'),
])
def test_completion_failure_categories_never_expose_response(data,message):
    with pytest.raises(providers.AnalysisError,match=message) as exc:
        providers.completion_report(data,EVIDENCE)
    assert 'private-api-key' not in str(exc.value)


@pytest.mark.parametrize('fence', ['```json\n{}\n```','```JSON\n{}\n```','```\n{}\n```','{}'])
def test_complete_json_fences_are_supported_without_weakening_validation(fence):
    raw=json.dumps(report(EVIDENCE),ensure_ascii=False)
    parsed=providers.completion_report(completion(fence.format(raw)),EVIDENCE)
    assert parsed['summary']=='测试摘要' and parsed['rewrites']
    bad=report(EVIDENCE);bad['rewrites'][0]['evidence_ids']=['E9999']
    with pytest.raises(providers.AnalysisError,match='引用'):
        providers.completion_report(completion(fence.format(json.dumps(bad))),EVIDENCE)


@pytest.mark.parametrize('base,model,thinking',[
    ('https://api.deepseek.com/v1','deepseek-flash',True),
    ('https://api.deepseek.com','deepseek-v4-pro',True),
    ('https://model.test/v1','deepseek-flash',False),
    ('https://api.deepseek.com.evil.test/v1','deepseek-flash',False),
    ('https://model.test/v1','other-model',False),
])
@pytest.mark.asyncio
async def test_vendor_mode_is_scoped_and_no_automatic_paid_retry(monkeypatch,base,model,thinking):
    monkeypatch.setenv('MC_REMIX_API_KEY','private-test-key')
    monkeypatch.setenv('MC_REMIX_BASE_URL',base)
    monkeypatch.setenv('MC_REMIX_MODEL',model)
    original=httpx.AsyncClient
    calls=[]
    def handler(request):
        calls.append(1)
        payload=json.loads(request.content)
        assert payload['max_tokens']==10000
        if thinking:assert payload['thinking']=={'type':'disabled'}
        else:assert 'thinking' not in payload
        return httpx.Response(200,json=completion('', 'length'))
    monkeypatch.setattr(providers.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    with pytest.raises(providers.AnalysisError,match='截断'):
        await providers.analyze(EVIDENCE)
    assert len(calls)==1


@pytest.mark.parametrize('failure,message',[
    ('body','HTTP 响应不是有效 JSON'),
    ('timeout','超时'),
    ('connect','无法连接'),
    ('schema','报告格式'),
])
@pytest.mark.asyncio
async def test_http_and_content_errors_are_distinct(monkeypatch,failure,message):
    monkeypatch.setenv('MC_REMIX_API_KEY','private-test-key')
    monkeypatch.setenv('MC_REMIX_BASE_URL','https://model.test/v1')
    original=httpx.AsyncClient
    def handler(request):
        if failure=='body':return httpx.Response(200,text='<html>private-test-key</html>')
        if failure=='timeout':raise httpx.ReadTimeout('private-test-key',request=request)
        if failure=='connect':raise httpx.ConnectError('private-test-key',request=request)
        return httpx.Response(200,json=completion('{"summary":"wrong-schema"}'))
    monkeypatch.setattr(providers.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    with pytest.raises(providers.AnalysisError,match=message) as exc:
        await providers.analyze(EVIDENCE)
    assert 'private-test-key' not in str(exc.value)


def test_nine_valid_citations_do_not_discard_complete_comment_rich_report():
    evidence=[dict(id=f'E{i:04d}',kind='title',text='独立证据') for i in range(1,10)]
    raw=report(evidence)
    raw['sections'][2]['findings'][0]['evidence_ids']=[e['id'] for e in evidence]
    parsed=providers.completion_report(completion(json.dumps(raw)),evidence)
    assert len(parsed['sections'][2]['findings'][0]['evidence_ids'])==9
    raw['sections'][2]['findings'][0]['evidence_ids'][-1]='E9999'
    with pytest.raises(providers.AnalysisError,match='引用'):
        providers.completion_report(completion(json.dumps(raw)),evidence)
