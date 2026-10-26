"""Versioned local generation instructions. Definitions never execute code or tools."""
import json

from . import store

AGENTS = [
    dict(id='content_writer',name='内容创作 Agent',description='根据来源和创作需求生成图文草稿',instruction='以图文阅读场景组织完整、可编辑的正文。',content_type='图文'),
    dict(id='script_writer',name='视频脚本 Agent',description='生成口播开头、分段脚本与画面建议',instruction='以短视频口播场景创作，提供 hook 和分段 script；视觉仅作为待拍摄建议。',content_type='视频'),
    dict(id='insight_writer',name='数据洞察 Agent',description='复用已有分析，写清数据范围、结论和行动建议',instruction='根据已保存分析与原始证据写作；逐项保留样本范围和时间，区分统计、推断和待核实信息。',content_type='图文'),
]
SKILLS = [
    dict(id='answer',name='需求解答',instruction='先给直接答案，再用来源细节解释使用条件和下一步。'),
    dict(id='compare',name='决策对比',instruction='按相同维度比较选项，区分已知、未知与适合人群，不混用不同案例的事实。'),
    dict(id='story',name='经历叙事',instruction='按场景、问题、过程、结果组织来源记录的经历；不得虚构第一人称亲历。'),
    dict(id='curate',name='信息整理',instruction='提炼关键条件、亮点、限制和待核实事项，数据需保留样本范围和时间。'),
    dict(id='evidence_outline',name='证据提纲',instruction='先按已保存的分析结论组织提纲，每一处价格、数量和条件保留对应原文或统计依据；省略无依据的细节。'),
    dict(id='plain_language',name='清晰表达',instruction='用熟悉词语和短句解释已有分析，先给读者需要的答案，再说明条件与核实办法，不扩大原结论。'),
]


class SelectionError(ValueError):
    pass


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS creation_definitions(kind TEXT,id TEXT,version INTEGER,body TEXT NOT NULL,PRIMARY KEY(kind,id,version))')


def listing(kind):
    builtins=AGENTS if kind=='agent' else SKILLS
    defaults=dict(version=1,enabled=True,builtin=True)
    if kind=='skill':defaults['content_types']=['图文','视频']
    result=[dict(defaults,**entry) for entry in builtins]
    with store.connect() as db:
        schema(db)
        rows=db.execute('SELECT d.body FROM creation_definitions d WHERE kind=? AND version=(SELECT max(n.version) FROM creation_definitions n WHERE n.kind=d.kind AND n.id=d.id) ORDER BY id',(kind,)).fetchall()
    return result+[json.loads(row[0]) for row in rows]


def save(kind,payload,expected_id=None):
    data=payload.model_dump()
    if not data['name'].strip() or not data['instruction'].strip():raise ValueError('配置名称和执行方法不能为空')
    if expected_id and data['id']!=expected_id:raise ValueError('配置编号不能更改')
    if data['id'] in {e['id'] for e in (AGENTS if kind=='agent' else SKILLS)}:
        raise ValueError('内置配置只读，请使用新编号另存')
    with store.connect() as db:
        schema(db);db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT version FROM creation_definitions WHERE kind=? AND id=? ORDER BY version DESC LIMIT 1',(kind,data['id'])).fetchone()
        version=row[0] if row else 0
        if bool(row)!=bool(expected_id):raise ValueError('配置已存在或不存在，请刷新列表')
        if data['version']!=version:raise ValueError('配置已被修改，请刷新后编辑')
        data.update(version=version+1,builtin=False,updated_at=store.now())
        db.execute('INSERT INTO creation_definitions VALUES(?,?,?,?)',(kind,data['id'],data['version'],json.dumps(data,ensure_ascii=False)))
    return data


def history(kind,identifier):
    with store.connect() as db:
        schema(db)
        return [json.loads(r[0]) for r in db.execute('SELECT body FROM creation_definitions WHERE kind=? AND id=? ORDER BY version DESC',(kind,identifier))]


def resolve(agent_id,skill_ids,content_type,agent_version=None,skill_versions=None):
    agents={a['id']:a for a in listing('agent')};skills={s['id']:s for s in listing('skill')}
    agent=agents.get(agent_id)
    if not agent or not agent['enabled']:raise SelectionError('所选 Agent 不存在或已停用，请刷新配置')
    if agent_version is not None and agent['version']!=agent_version:raise SelectionError('所选 Agent 版本已更新，请刷新配置并核对后提交')
    if agent['content_type']!=content_type:raise SelectionError(f"{agent['name']} 需要选择{agent['content_type']}内容类型")
    if len(set(skill_ids))!=len(skill_ids):raise SelectionError('Skill 不能重复选择')
    skill_versions=skill_versions or {}
    if set(skill_versions)-set(skill_ids):raise SelectionError('Skill 版本必须对应本次所选方法')
    selected=[]
    for identifier in skill_ids:
        item=skills.get(identifier)
        if not item or not item['enabled']:raise SelectionError('所选 Skill 不存在或已停用，请刷新配置')
        if identifier in skill_versions and skill_versions[identifier]!=item['version']:raise SelectionError('所选 Skill 版本已更新，请刷新配置并核对后提交')
        if content_type not in item['content_types']:raise SelectionError(f"{item['name']} 不支持{content_type}内容类型")
        selected.append(item)
    return dict(agent=agent,skills=selected,registry_version=1)
