"""Calendar/transport fixtures only; never queries a real RedFox account."""
from datetime import datetime
from zoneinfo import ZoneInfo
import json

import httpx
import pytest

from api.services.redfox import RedFoxClient, TOOLS
from api.services.redfox_dates import DARK_HORSE, darkhorse_window, validate_dates, catalog_tools

NOW=datetime(2026,10,26,16,30,tzinfo=ZoneInfo('Asia/Shanghai'))


def test_shanghai_window_and_exact_30_day_boundary():
    assert darkhorse_window(NOW)=={'min':'2026-09-27','max':'2026-10-25','recommended':'2026-10-19'}
    with pytest.raises(ValueError,match='日期格式正确.*尚未调用'):
        validate_dates(DARK_HORSE,{'start_date':'2026-09-26'},NOW)
    validate_dates(DARK_HORSE,{'start_date':'2026-09-27'},NOW)
    validate_dates(DARK_HORSE,{'start_date':'2026-10-25'},NOW)
    assert darkhorse_window(datetime(2026,10,25,16,30,tzinfo=ZoneInfo('UTC')))==darkhorse_window(NOW)


@pytest.mark.parametrize('value',['2026-9-26','2026/10/19','2026-02-30','2026-10-26','2026-10-27'])
def test_invalid_or_unavailable_dates_rejected_before_collection(value):
    with pytest.raises(ValueError,match='尚未调用'):
        validate_dates(DARK_HORSE,{'start_date':value},NOW)


def test_only_date_fields_change_other_tools_keep_their_time_formats():
    validate_dates('xiaohongshu_search_ai_articles',{'start_time':'2026-10-19 00:00:00'},NOW)
    validate_dates('xiaohongshu_search_hot_notes',{'start_date':'2025-01-01','end_date':'2026-10-25'},NOW)
    with pytest.raises(ValueError,match='不能晚于'):
        validate_dates('xiaohongshu_search_hot_notes',{'start_date':'2026-10-25','end_date':'2026-10-19'},NOW)
    catalog=catalog_tools(TOOLS)
    darkhorse=next(t for t in catalog if t['name']==DARK_HORSE)
    assert darkhorse['inputSchema']['properties']['start_date']['format']=='date'
    assert 'format' not in next(t for t in TOOLS if t['name']==DARK_HORSE)['inputSchema']['properties']['start_date']


@pytest.mark.asyncio
async def test_provider_date_error_adds_context_without_retry():
    calls=[]
    def handle(req):
        body=json.loads(req.content);calls.append(body)
        if body['method']=='notifications/initialized':return httpx.Response(202)
        result={'protocolVersion':'2025-03-26'} if body['method']=='initialize' else {
            'isError':False,'structuredContent':{'error':'api_error','code':3203,'message':'接口执行异常，积分未扣除: 日期格式错误'}}
        return httpx.Response(200,json={'jsonrpc':'2.0','id':body['id'],'result':result})
    args={'category':'旅行度假','start_date':darkhorse_window()['recommended']}
    with pytest.raises(ValueError,match='已通过本机格式与窗口校验.*数据缺失') as error:
        await RedFoxClient('xhs','fixture-key',httpx.MockTransport(handle)).call(DARK_HORSE,args)
    assert args['start_date'] in str(error.value) and '不会自动重试' in str(error.value)
    assert len([call for call in calls if call['method']=='tools/call'])==1
