"""RedFox Streamable HTTP client. Credentials stay on the collection computer."""
import asyncio
import json
import os
from pathlib import Path
from datetime import datetime, timezone
import tomllib

import httpx
from .redfox_payload import check_result

PLATFORMS = {'xhs': 'xiaohongshu', 'dy': 'douyin', 'twitter': 'twitter', 'youtube': 'youtube'}
TOOLS = json.loads(Path(__file__).with_name('redfox_tools.json').read_text())
MAX_RESPONSE = 8 * 1024 * 1024


def credential(platform):
    if platform not in PLATFORMS:
        raise ValueError('RedFox 不支持此平台')
    key = os.environ.get('REDFOX_API_KEY', '').strip()
    if key:
        return key
    try:
        path = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'config.toml'
        server = tomllib.loads(path.read_text()).get('mcp_servers', {}).get(f'redfox-{PLATFORMS[platform]}-mcp', {})
        key = server.get('http_headers', {}).get('REDFOX_API_KEY', '')
        env_name = server.get('env_http_headers', {}).get('REDFOX_API_KEY', '')
        return os.environ.get(env_name, key).strip()
    except (OSError, ValueError, TypeError, AttributeError):
        return ''


def validate_tool(platform, name, arguments):
    tool = next((t for t in TOOLS if t['platform'] == platform and t['name'] == name), None)
    if not tool:
        raise ValueError('请选择此平台支持的 RedFox 工具')
    schema = tool['inputSchema']
    if len(json.dumps(arguments).encode()) > 32768:
        raise ValueError('MCP 参数过长')
    for field in arguments:
        if field not in schema['properties']:
            raise ValueError(f'不支持的参数：{field}')
        expected = schema['properties'][field]['type']
        value = arguments[field]
        valid = isinstance(value, str) if expected == 'string' else type(value) in (int, float)
        if not valid:
            raise ValueError(f'参数 {field} 类型不正确')
        choices = schema['properties'][field].get('enum')
        if choices and value not in choices:
            raise ValueError(f'参数 {field} 请从分类列表选择，例如：旅行度假')
    for field in schema['required']:
        if field not in arguments or arguments[field] == '':
            raise ValueError(f'请填写参数：{field}')
    alternatives = {
        'xiaohongshu_get_work': ('work_id', 'work_link'),
        'xiaohongshu_get_user_works': ('red_id', 'userid'),
        'douyin_get_work': ('work_id', 'work_url'),
        'douyin_get_user_works': ('account_id', 'author_url', 'sec_user_id'),
        'douyin_get_user_works_wide': ('user_id', 'unique_name', 'short_id'),
        'twitter_get_user': ('screen_name', 'rest_id'),
    }
    if name in alternatives and not any(arguments.get(k) for k in alternatives[name]):
        raise ValueError('至少填写一个：' + ' / '.join(alternatives[name]))
    return tool


class RedFoxClient:
    def __init__(self, platform, key, transport=None):
        self.url = f'https://mcp.redfox.hk/{PLATFORMS[platform]}/mcp'
        self.headers = {'REDFOX_API_KEY': key, 'Accept': 'application/json, text/event-stream'}
        self.client = httpx.AsyncClient(timeout=90, transport=transport, follow_redirects=False)
        self.counter = 0

    async def request(self, method, params, notification=False):
        self.counter += 1
        request_id = self.counter
        body = dict(jsonrpc='2.0', method=method, params=params)
        if not notification:
            body['id'] = request_id
        async with self.client.stream('POST', self.url, headers=self.headers, json=body) as response:
            response.raise_for_status()
            if method == 'initialize' and response.headers.get('mcp-session-id'):
                self.headers['Mcp-Session-Id'] = response.headers['mcp-session-id']
            if notification:
                return None
            size = 0
            if 'text/event-stream' in response.headers.get('content-type', ''):
                data = []
                async for line in response.aiter_lines():
                    size += len(line.encode())
                    if size > MAX_RESPONSE:
                        raise ValueError('MCP 响应超出大小上限')
                    if line.startswith('data:'):
                        data.append(line[5:].lstrip())
                    elif not line and data:
                        message = json.loads('\n'.join(data)); data = []
                        if message.get('id') == request_id:
                            return self.result(message, (self.headers['REDFOX_API_KEY'],))
                raise ValueError('MCP 响应未完整结束；不会自动重试')
            chunks = []
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE:
                    raise ValueError('MCP 响应超出大小上限')
                chunks.append(chunk)
            message = json.loads(b''.join(chunks))
            if message.get('id') != request_id:
                raise ValueError('MCP 响应标识不匹配')
            return self.result(message, (self.headers['REDFOX_API_KEY'],))

    @staticmethod
    def result(message, secrets=()):
        if 'error' in message:
            raise ValueError('MCP 服务返回协议错误；请检查账户或参数，不会自动重试')
        result = message.get('result')
        if not isinstance(result, dict):
            raise ValueError('MCP 返回格式无效')
        if result.get('isError'):
            raise ValueError('RedFox 工具执行失败；请检查参数、权限或余额，不会自动重试')
        return check_result(result, secrets)

    async def call(self, name, arguments):
        try:
            async with asyncio.timeout(120):
                initialized = await self.request('initialize', dict(protocolVersion='2025-03-26', capabilities={}, clientInfo={'name': 'MediaCrawler', 'version': '1.0'}))
                self.headers['MCP-Protocol-Version'] = initialized.get('protocolVersion', '2025-03-26')
                await self.request('notifications/initialized', {}, notification=True)
                return await self.request('tools/call', {'name': name, 'arguments': arguments})
        except httpx.HTTPStatusError as exc:
            raise ValueError(f'RedFox 返回 HTTP {exc.response.status_code}；请检查凭据或余额，不会自动重试') from None
        except (httpx.HTTPError, TimeoutError):
            raise ValueError('RedFox 通信未完成，远程任务可能已提交；不会自动重试，请先核对任务') from None
        finally:
            await self.client.aclose()


async def collect(request, output_root=None):
    validate_tool(request.platform.value, request.mcp_tool, request.mcp_arguments)
    key = credential(request.platform.value)
    if not key:
        raise ValueError('未找到 RedFox API Key，请配置采集电脑的 REDFOX_API_KEY 或 Codex MCP 持久配置')
    print(f'RedFox MCP · {request.mcp_tool} · 单次调用，不自动翻页或重试', flush=True)
    result = await RedFoxClient(request.platform.value, key).call(request.mcp_tool, request.mcp_arguments)
    check_result(result, (key,))
    from api.routers.data import DATA_DIR
    root = Path(output_root) if output_root is not None else DATA_DIR
    folder = root / request.platform.value / 'redfox'
    folder.mkdir(parents=True, exist_ok=True)
    task_id = os.environ['MEDIACRAWLER_TASK_ID']
    if not task_id.isalnum():
        raise ValueError('任务 ID 无效')
    path = folder / f'{task_id}.json'
    # Preserve remote task IDs and cursors for explicit follow-up calls.
    from .redfox_ingest import content_id, sync_export
    args = request.mcp_arguments
    parent = request.mcp_parent_content_id or next((args[k] for k in ('opus_id','tweet_id','video_id','work_id','work_link','work_url','video_url','url') if args.get(k)), '')
    context = dict(keyword=args.get('keyword') or args.get('search_query') or '',
                   parent_id=content_id(parent, request.platform.value), remote_task_id=args.get('task_id') or '')
    payload = [dict(task_id=task_id, source='redfox_mcp', tool=request.mcp_tool, result=result,
                    collected_at=datetime.now(timezone.utc).isoformat(), context=context)]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2).replace(key, '[hidden]'), encoding='utf-8')
    report = sync_export(json.loads(path.read_text())[0], request.platform.value)
    print('MC_MCP_SYNC ' + json.dumps(report, ensure_ascii=False), flush=True)
    print(f'完成：已保存 MCP 原始结果到 {path.relative_to(root)}；同步内容 {report["contents"]} 条、评论 {report["comments"]} 条。异步任务请另行查询。', flush=True)


if __name__ == '__main__':
    from api.schemas.crawler import CrawlerStartRequest
    try:
        asyncio.run(collect(CrawlerStartRequest.model_validate_json(os.environ['MEDIACRAWLER_MCP_REQUEST'])))
    except Exception as exc:
        # Never print raw transport bodies, URLs, arguments or credentials.
        message = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else 'RedFox 采集未完成，请核对服务状态和参数；不会自动重试'
        print('MC_MCP_ERROR ' + json.dumps({'message':message}, ensure_ascii=False), flush=True)
        raise SystemExit(1)
