"""UI choices from RedFox documentation; suggestions never narrow the wire schema.

XHS search sort values: https://redfox.hk/apis/xiaohongshu/384C6W6B
Other partial lists are explicitly extensible, not claimed as exhaustive enums.
"""


def decorate(tool):
    name = tool['name']
    for field, spec in tool['inputSchema']['properties'].items():
        choices = None
        if field == 'search_type' and name == 'twitter_search_tweets':
            choices = [('Top', '热门'), ('Latest', '最新'), ('Media', '媒体'), ('People', '用户'), ('Lists', '列表')]
        elif field == 'date_type':
            choices = [(1, '日榜'), (2, '周榜'), (3, '月榜')] if tool['platform'] == 'xhs' else [('days', '日榜'), ('weeks', '周榜'), ('months', '月榜')]
        elif field == 'sort_type':
            choices = [('_0', '综合 / 相关性'), ('_2', '最新发布'), ('_4', '最多互动')] if name in ('xiaohongshu_search_articles','douyin_search_users') else [('default', '默认排序')] if name == 'douyin_search_articles' else []
        elif field in ('type', 'category') and not spec.get('enum'):
            choices = [(v, v) for v in (['综合全部'] if tool['platform'] == 'xhs' else ['全部', '美食'])]
        elif field == 'language_code':
            choices = [('zh', '中文'), ('en', '英语'), ('ja', '日语')]
        elif field == 'country_code':
            choices = [('CN', '中国'), ('US', '美国'), ('JP', '日本')]
        elif field == 'sort_by':
            choices = [('top', '热门评论')]
        if choices is not None:
            spec['choices'] = [dict(value=v, label=label) for v, label in choices]
            spec['allowCustom'] = True
        if field in ('start_time', 'end_time') and 'search_ai_articles' in name:
            spec['format'] = 'date-time'
        if spec['type'] == 'number':
            spec['step'] = 1
            if field == 'offset':
                spec['minimum'] = 0
                if name in ('xiaohongshu_search_articles','douyin_search_articles','douyin_search_users'): spec['step'] = 20
            if field in ('page_num', 'page_size', 'timeout_seconds'): spec['minimum'] = 1
            if field == 'page_size': spec['maximum'] = 50
            if field == 'data_num': spec['minimum'] = -1
