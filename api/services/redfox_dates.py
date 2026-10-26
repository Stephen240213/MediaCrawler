"""Shanghai calendar validation; stay inside the provider's uncertain 30-day edge."""
from copy import deepcopy
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
import re

DARK_HORSE = 'xiaohongshu_get_dark_horse_notes'


def darkhorse_window(now=None):
    today = (now or datetime.now(ZoneInfo('Asia/Shanghai'))).astimezone(ZoneInfo('Asia/Shanghai')).date()
    return dict(min=(today-timedelta(days=29)).isoformat(), max=(today-timedelta(days=1)).isoformat(),
                recommended=(today-timedelta(days=7)).isoformat())


def is_date_field(tool, field):
    return field in ('rank_date','start_date','end_date','publish_time_start','publish_time_end') or (
        tool in ('douyin_get_daily_hot_rank','douyin_get_daily_surge_rank','douyin_get_weekly_surge_rank') and field in ('start_time','end_time'))


def catalog_tools(tools):
    result = deepcopy(tools)
    for tool in result:
        for field, spec in tool['inputSchema']['properties'].items():
            if is_date_field(tool['name'],field):
                spec['format'] = 'date'
        if tool['name'] == DARK_HORSE:
            tool['datePolicies'] = {'start_date':darkhorse_window()}
    return result


def validate_dates(tool, arguments, now=None):
    for field,value in arguments.items():
        if not is_date_field(tool,field):
            continue
        try:
            if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
                raise ValueError
            date.fromisoformat(value)
        except ValueError:
            raise ValueError(f'参数 {field} 需要真实日期，格式 YYYY-MM-DD，例如 2026-10-19；尚未调用 RedFox') from None
    for start,end in (('start_date','end_date'),('publish_time_start','publish_time_end'),('start_time','end_time')):
        if is_date_field(tool,start) and arguments.get(start) and arguments.get(end) and arguments[start] > arguments[end]:
            raise ValueError('开始日期不能晚于结束日期；尚未调用 RedFox')
    if tool == DARK_HORSE and arguments.get('start_date'):
        window = darkhorse_window(now)
        if not window['min'] <= arguments['start_date'] <= window['max']:
            raise ValueError(f'日期格式正确，但黑马榜为避开服务的30天边界，仅提交上海日期 {window["min"]} 至 {window["max"]}；建议手动选择 {window["recommended"]}。尚未调用 RedFox')


def date_error_hint(tool, arguments, error):
    if tool == DARK_HORSE and '日期格式错误' in str(error):
        window = darkhorse_window()
        return (f'{error}。提交日期 {arguments.get("start_date", "")} 已通过本机格式与窗口校验；'
                f'RedFox 对部分日期的数据缺失或服务窗口问题也会返回此提示。可手动改用建议日期 {window["recommended"]}，或稍后再查；每次执行仍可能消耗积分。')
    return str(error)
