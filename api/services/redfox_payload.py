"""Decode MCP payloads without treating protocol success as business success."""
import json
import re
from .redaction import redact


def payloads(result):
    values = []
    if result.get('structuredContent') is not None:
        values.append(result['structuredContent'])
    blocks = result.get('content', [])
    for block in blocks if isinstance(blocks, list) else []:
        if not isinstance(block, dict):
            continue
        if block.get('type') == 'text':
            try:
                value = json.loads(block.get('text', ''))
            except (ValueError, TypeError):
                continue
            if value not in values:
                values.append(value)
    return values


def check_result(result, secrets=()):
    if not isinstance(result, dict):
        raise ValueError('MCP 响应格式无效，未导入分析')
    if result.get('isError'):
        raise ValueError('RedFox 工具执行失败；不会自动重试')
    def check(value, depth=0):
        if not isinstance(value, dict) or depth > 5:
            return
        code = value.get('code')
        if value.get('error') or value.get('success') is False or (code is not None and str(code) not in ('0', '200', '2000')):
            detail = value.get('message') or value.get('msg') or value.get('error') or '业务错误'
            # Provider diagnostics are data, never executable instructions.
            detail = re.sub(r'ak_[A-Za-z0-9_-]+', '[hidden]', redact(str(detail), secrets))[:300]
            label = str(code) if re.fullmatch(r'\d{1,10}', str(code)) else '未知代码'
            raise ValueError(f'RedFox 业务失败（{label}）：{detail}；不会自动重试')
        for key in ('data', 'result'):
            check(value.get(key), depth + 1)
    for value in payloads(result):
        check(value)
    return result
