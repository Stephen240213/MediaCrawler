"""Apply the website's visible filters and consume only its own search responses.

No private search API is invoked by this module. Every response is produced by
normal input, filter clicks or scrolling in the existing browser page.
"""
import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError, expect
from model.xhs_search_filters import FILTER_GROUPS, XhsSearchFilters


class SearchFilterError(RuntimeError):
    pass


def request_matches(request, keyword):
    try:
        parsed = urlparse(request.url)
        return ((parsed.hostname, parsed.path) in {
                    ('edith.xiaohongshu.com', '/api/sns/web/v1/search/notes'),
                    ('www.xiaohongshu.com', '/api/sns/web/v1/search/notes'),
                    ('so.xiaohongshu.com', '/api/sns/web/v2/search/notes'),
                }
                and request.method == 'POST'
                and request.post_data_json.get('keyword') == keyword)
    except (ValueError, AttributeError, TypeError):
        return False


def filter_fingerprint(body):
    # Hash only filter-related data, excluding volatile page/search IDs. Never
    # persist the raw request (which may contain location data or credentials).
    keys = ('sort', 'note_type', 'filters', 'ext_flags', 'geo', 'search_scope', 'publish_time', 'location')
    selected = {key: body[key] for key in keys if key in body}
    return hashlib.sha256(json.dumps(selected, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass
class SearchBatch:
    body: dict
    items: list
    has_more: bool

    @classmethod
    async def read(cls, response):
        if response.status != 200:
            raise SearchFilterError(f'网页搜索请求失败，HTTP {response.status}，请检查登录或平台验证')
        payload = await response.json()
        if payload.get('success') is False or payload.get('code', 0) not in (0, None):
            raise SearchFilterError('平台未返回有效搜索结果，请在浏览器检查登录或验证提示')
        data = payload.get('data')
        if not isinstance(data, dict) or not isinstance(data.get('items'), list):
            raise SearchFilterError('网页搜索结果格式已变化，停止采集以避免使用错误结果')
        return cls(response.request.post_data_json, data['items'], bool(data.get('has_more', False)))


class BrowserFilteredSearch:
    def __init__(self, page: Page, filters: XhsSearchFilters, report):
        self.page, self.filters, self.report = page, filters, report
        self.keyword = ''
        self.batch = None
        self.verified = {}
        self.scrolls = 0
        self.stop_reason = ''

    def group(self, label):
        return self.page.locator('.filters-wrapper > .filters').filter(has=self.page.get_by_text(label, exact=True))

    def option(self, label, option):
        # aria-hidden nodes are not actionable options, even if they duplicate text.
        return self.group(label).locator('.tags:not([aria-hidden="true"])').filter(has=self.page.get_by_text(option, exact=True))

    async def open_panel(self):
        if await self.group('排序依据').is_visible():
            return
        trigger = self.page.locator('.filter').filter(has=self.page.get_by_text(re.compile(r'^(筛选|已筛选)$')))
        await trigger.hover(timeout=10000)
        await self.group('排序依据').wait_for(state='visible', timeout=5000)

    def matches(self, response):
        return request_matches(response.request, self.keyword)

    async def apply(self, keyword):
        self.keyword = keyword
        self.report('searching', keyword=keyword)
        # Older and newer website layouts use different inputs. All selectors
        # are stable IDs observed on the real website, not page-internal state.
        field = self.page.locator('#search-input:visible, #search-input-ai:visible, #search-input-in-feeds:visible')
        await field.wait_for(state='visible', timeout=15000)
        await field.fill(keyword)
        async with self.page.expect_response(self.matches, timeout=30000) as response:
            await field.press('Enter')
        self.batch = await SearchBatch.read(await response.value)
        for key, (label, choices) in FILTER_GROUPS.items():
            desired = choices[getattr(self.filters, key)]
            self.report('filtering', stop_reason=f'正在选择：{label} · {desired}')
            await self.open_panel()
            option = self.option(label, desired)
            if await option.count() != 1:
                raise SearchFilterError(f'筛选项不可用：{label} / {desired}，未改用默认条件')
            selected = (await option.get_attribute('class') or '').split()
            if 'active' not in selected:
                async with self.page.expect_response(self.matches, timeout=20000) as response:
                    await option.click(timeout=10000)
                self.batch = await SearchBatch.read(await response.value)
            await self.open_panel()
            await expect(self.option(label, desired)).to_have_class(re.compile(r'\bactive\b'), timeout=5000)
        self.report('verifying', stop_reason='')
        self.verified = await self.verify()
        self.report('collecting', verified_filters=self.verified)
        # Move out of the hover panel without selecting any different setting.
        await self.page.mouse.move(400, 250)
        return self.batch

    async def verify(self):
        await self.open_panel()
        verified = {}
        for key, (label, choices) in FILTER_GROUPS.items():
            expected = choices[getattr(self.filters, key)]
            active = self.group(label).locator('.tags.active:not([aria-hidden="true"])')
            if await active.count() != 1 or (await active.inner_text()).strip() != expected:
                raise SearchFilterError(f'筛选校验失败：{label} 应为 {expected}，停止采集')
            verified[label] = expected
        return verified

    async def next_batch(self):
        if not self.batch.has_more:
            self.stop_reason = 'platform_end'
            return None
        fingerprint = filter_fingerprint(self.batch.body)
        while self.scrolls < self.filters.max_scrolls:
            self.scrolls += 1
            self.report('collecting', scrolls=self.scrolls)
            try:
                async with self.page.expect_response(self.matches, timeout=5000) as response:
                    # Keep the pointer over the result column in both site layouts.
                    await self.page.mouse.move(400, 450)
                    await self.page.mouse.wheel(0, 1100)
                batch = await SearchBatch.read(await response.value)
            except PlaywrightTimeoutError:
                if await self.page.get_by_text(re.compile('没有更多|到底了|暂无搜索结果')).count():
                    self.stop_reason = 'page_end'
                    return None
                continue
            if filter_fingerprint(batch.body) != fingerprint:
                raise SearchFilterError('继续加载时搜索条件发生变化，停止采集')
            self.batch = batch
            return batch
        self.stop_reason = 'scroll_limit'
        return None
