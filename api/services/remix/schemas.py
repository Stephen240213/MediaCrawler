from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


Framework = Literal['auto', '需求解答', '决策对比', '真实经历', '信息筛选']
Platform = Literal['xhs', 'douyin', 'bilibili', 'other']


class CreativeBrief(StrictModel):
    platform: Platform = 'xhs'
    content_type: Literal['图文', '视频'] = '图文'
    audience: str = Field(default='', max_length=300)
    objective: Literal['分享', '收藏', '咨询', '转化'] = '分享'
    topic: str = Field(default='', max_length=100)
    framework: Framework = 'auto'
    tone: str = Field(default='', max_length=300)
    must_keep: str = Field(default='', max_length=1000)
    avoid: str = Field(default='', max_length=500)
    supplemental: str = Field(default='', max_length=3000)


class JobInput(StrictModel):
    source: Literal['xhs', 'upload']
    content_id: str = Field(default='', max_length=200)
    upload_id: str = Field(default='', pattern=r'^([a-f0-9]{32})?$', max_length=32)
    title: str = Field(default='', max_length=200)
    analyze_video: bool = False
    include_comments: bool = True
    pasted_comments: str = Field(default='', max_length=8000)
    brief: CreativeBrief = Field(default_factory=CreativeBrief)
    consent: bool = False
    request_key: str = Field(pattern=r'^[a-zA-Z0-9_-]{16,128}$')


class UploadInput(StrictModel):
    filename: str = Field(min_length=1, max_length=200)
    size: int = Field(ge=1, le=200*1024*1024)
    request_key: str = Field(pattern=r'^[a-zA-Z0-9_-]{16,128}$')


class Finding(StrictModel):
    observation: str = Field(min_length=1, max_length=1200)
    interpretation: str = Field(min_length=1, max_length=1200)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)


class Section(StrictModel):
    dimension: Literal['开头与钩子', '内容结构', '选题与受众', '表达与情绪']
    findings: list[Finding] = Field(min_length=1, max_length=5)


class Idea(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    outline: list[str] = Field(min_length=1, max_length=8)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)


class TitleOption(StrictModel):
    title: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=300)


class Rewrite(StrictModel):
    angle: str = Field(min_length=1, max_length=200)
    framework: Literal['需求解答', '决策对比', '真实经历', '信息筛选']
    title_options: list[TitleOption] = Field(min_length=3, max_length=3)
    body: str = Field(min_length=1, max_length=4000)
    hook: str = Field(default='', max_length=300)
    script: list[str] = Field(default_factory=list, max_length=8)
    cover_text: str = Field(min_length=1, max_length=100)
    visuals: list[str] = Field(min_length=1, max_length=6)
    tags: list[str] = Field(default_factory=list, max_length=8)
    fact_checks: list[str] = Field(default_factory=list, max_length=8)
    change_notes: list[str] = Field(min_length=1, max_length=6)
    evidence_ids: list[str] = Field(min_length=1, max_length=12)


class VersionInput(StrictModel):
    title_option: int = Field(default=0, ge=0, le=2)
    rewrite_index: int = Field(ge=0, le=2)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=8000)
    feedback: str = Field(default='', max_length=1500)
    request_key: str = Field(pattern=r'^[a-zA-Z0-9_-]{16,128}$')


class KnowledgeInput(StrictModel):
    kind: Literal['preference', 'edit', 'rule'] = 'preference'
    title: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=2000)
    platform: Literal['all', 'xhs', 'douyin', 'bilibili', 'other'] = 'all'
    topic: str = Field(default='', max_length=100)
    reason: str = Field(default='', max_length=500)
    enabled: bool = True
    source_job_id: str = Field(default='', pattern=r'^([a-f0-9]{32})?$')
    source_version_id: str = Field(default='', pattern=r'^([a-f0-9]{32})?$')
    version: int = Field(default=1, ge=1)


class AnalysisReport(StrictModel):
    summary: str = Field(min_length=1, max_length=1500)
    summary_evidence_ids: list[str] = Field(min_length=1, max_length=12)
    sections: list[Section] = Field(min_length=4, max_length=4)
    ideas: list[Idea] = Field(min_length=1, max_length=5)
    limitations: list[str] = Field(min_length=1, max_length=12)

    # Defaults keep historical phase-one reports readable without another model call.
    recommended_framework: Framework = 'auto'
    framework_reason: str = Field(default='', max_length=1000)
    comment_analysis: list[Finding] = Field(default_factory=list, max_length=5)
    rewrites: list[Rewrite] = Field(default_factory=list, max_length=3)
