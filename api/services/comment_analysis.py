"""Bounded, explainable document-frequency and rule coding; no model inference."""
import re
from collections import Counter

THEMES = {
    '价格与预算': ('价格', '多少钱', '多少米', '预算', '费用', '贵', '便宜', '收费', '性价比'),
    '位置与交通': ('地址', '位置', '哪里', '在哪', '交通', '停车', '地铁', '导航', '怎么去'),
    '预订与购买': ('预订', '预约', '怎么买', '购买', '链接', '怎么买', '门票', '订房', '下单'),
    '体验与服务': ('体验', '服务', '卫生', '环境', '隔音', '态度', '设施', '排队'),
    '攻略与适用性': ('攻略', '推荐', '适合', '带娃', '宠物', '避雷', '注意', '路线'),
}
POSITIVE = ('喜欢', '满意', '好看', '不错', '值得', '很棒', '推荐', '漂亮')
NEGATIVE = ('失望', '不推荐', '不值得', '不好', '太贵', '踩雷', '避雷', '差劲', '不满意', '不喜欢', '不好看', '不漂亮')
STOP = set('一个 这个 那个 我们 你们 他们 可以 就是 真的 怎么 多少 哪里 还是 还有 偷笑 哈哈 哈哈哈 表情 图片 视频 笔记 分享 评论 博主 请问 谢谢 感觉 什么 这样 那么 已经 自己 但是 因为 所以 没有 不是 这些 那些 时候 一下 一些 这么 这种 这里 那里 the and that with this'.split())


def codes(text):
    text = text.lower()
    topics = [label for label, terms in THEMES.items() if any(t in text for t in terms)]
    negative = any(t in text for t in NEGATIVE)
    # Remove explicit negative phrases before positive matching (e.g. 不推荐).
    positive_text = text
    for phrase in NEGATIVE:
        positive_text = positive_text.replace(phrase, '')
    positive = any(t in positive_text for t in POSITIVE)
    sentiment = '褒贬并存' if positive and negative else '正向线索' if positive else '负向线索' if negative else '未识别情绪'
    question = bool(re.search(r'[?？]|请问|怎么|哪里|在哪|多少|能否|能不能|有没有|是否|吗', text))
    return topics, sentiment, question


def analyze(bodies, total):
    import jieba
    words, themes, sentiments = Counter(), Counter(), Counter()
    question_count = 0
    sample_count = sum(row['n'] for row in bodies)
    truncated = 0
    for row in bodies:
        body = row['body'] or ''
        truncated += row['n'] if len(body) > 2000 else 0
        text = body[:2000]
        tokens = {w.lower().strip() for w in jieba.cut(re.sub(r'\[[^\]]{1,30}\]', ' ', text))
                  if len(w.strip()) > 1 and re.search(r'[\u4e00-\u9fffA-Za-z]', w)} - STOP
        words.update({w: row['n'] for w in tokens})
        topics, sentiment, question = codes(text)
        themes.update({t: row['n'] for t in topics or ['其他或未识别']})
        sentiments[sentiment] += row['n']
        question_count += row['n'] if question else 0
    points = lambda counter: [{'label': label, 'value': value} for label, value in counter.most_common(40)]
    return dict(words=points(words), themes=points(themes), sentiments=points(sentiments),
                sample_comments=sample_count, unique_texts=len(bodies), total_comments=total,
                question_count=question_count, truncated_comments=truncated,
                rules={**{k: list(v) for k,v in THEMES.items()}, '正向词':list(POSITIVE), '负向词':list(NEGATIVE)})


def matches(text, code):
    topics, sentiment, question = codes((text or '')[:2000])
    if code == '提问意图':
        return int(question)
    return int(code in topics or code == sentiment or (code == '其他或未识别' and not topics))
