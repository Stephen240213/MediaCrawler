"""Versioned, deterministic topic counts over the included comment sample only."""
from .evidence_policy import capabilities

RULE_VERSION='comment-topics-v2-typed-comments'
RULES=(
 ('费用与支付',('多少钱','价格','费用','租金','月租','年付','押金','水电','管理费','预算','收费'), '明确价格口径、费用范围和付款方式'),
 ('门槛与条件',('条件','要求','限制','短租','长租','月租','宠物','能不能','可以吗','适合','资格'), '说明适用条件、限制和不适用情况'),
 ('位置与配套',('位置','哪里','地址','交通','地铁','停车','外卖','快递','买菜','超市','医院','配套'), '补充有来源的位置和生活配套信息'),
 ('体验与质量',('潮湿','发霉','噪音','隔音','卫生','舒服','好用','难用','信号','网络','体验','质量','效果'), '用可核实的细节回应体验顾虑'),
 ('方法与操作',('怎么','如何','步骤','教程','方法','操作','攻略','流程'), '给出明确步骤并标注所需条件'),
 ('购买与获取',('链接','购买','怎么买','求购','求同款','联系','预约','还有吗','房源','求地址'), '明确下一步获取或咨询方式，不编造联系方式'),
 ('风险与售后',('避雷','骗人','骗局','退货','退款','售后','合同','安全','风险','踩坑','投诉'), '区分评论指控与已核实事实，补充核验办法'),
)


def build(evidence):
    comments=[e for e in evidence if 'comment_text' in capabilities(e)]
    groups=[];matched=set()
    for name,words,action in RULES:
        hits=[e for e in comments if any(word in e['text'] for word in words)]
        if not hits:continue
        matched.update(e['id'] for e in hits)
        groups.append(dict(topic=name,count=len(hits),evidence_ids=[e['id'] for e in hits],
                           information_gap='需结合原文核对，判断哪些问题尚未回答',rewrite_action=action))
    other=[e['id'] for e in comments if e['id'] not in matched]
    if other:groups.append(dict(topic='其他反馈',count=len(other),evidence_ids=other,
        information_gap='规则未归入具体议题，需结合原评论判断',rewrite_action='人工检查代表评论，避免根据关键词推断情绪'))
    return dict(rule_version=RULE_VERSION,sample_size=len(comments),multi_label=True,groups=groups,
                scope='仅统计本次纳入的评论记录；一条可归入多个议题，类别计数不能直接相加。关键词规则不是情绪、真实满意度或全体观众判断。')
