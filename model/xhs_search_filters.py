"""Shared API/CLI contract for browser-driven Xiaohongshu search."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

FILTER_GROUPS = {
    'sort_by': ('排序依据', {'general': '综合', 'latest': '最新', 'most_liked': '最多点赞', 'most_commented': '最多评论', 'most_collected': '最多收藏'}),
    'note_type': ('笔记类型', {'all': '不限', 'image': '图文', 'video': '视频'}),
    'publish_time': ('发布时间', {'all': '不限', 'day': '一天内', 'week': '一周内', 'half_year': '半年内'}),
    'search_scope': ('搜索范围', {'all': '不限', 'viewed': '已看过', 'unviewed': '未看过', 'following': '已关注'}),
    'location': ('位置距离', {'all': '不限', 'same_city': '同城', 'nearby': '附近'}),
}


class XhsSearchFilters(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sort_by: Literal['general', 'latest', 'most_liked', 'most_commented', 'most_collected'] = 'general'
    note_type: Literal['all', 'image', 'video'] = 'all'
    publish_time: Literal['all', 'day', 'week', 'half_year'] = 'all'
    search_scope: Literal['all', 'viewed', 'unviewed', 'following'] = 'all'
    location: Literal['all', 'same_city', 'nearby'] = 'all'
    max_scrolls: int = Field(default=20, ge=1, le=100)
    timeout_seconds: int = Field(default=300, ge=30, le=1800)

    def labels(self):
        return {group: choices[getattr(self, key)] for key, (group, choices) in FILTER_GROUPS.items()}


class XhsSearchProgress(BaseModel):
    model_config = ConfigDict(extra='forbid')
    phase: Literal['opening', 'searching', 'filtering', 'verifying', 'collecting', 'details', 'saving', 'finished', 'failed']
    keyword: str = Field(max_length=1000)
    requested_filters: dict[str, str] = Field(default_factory=dict)
    verified_filters: dict[str, str] = Field(default_factory=dict)
    discovered: int = Field(default=0, ge=0)
    collected: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    scrolls: int = Field(default=0, ge=0)
    stop_reason: str = Field(default='', max_length=500)
