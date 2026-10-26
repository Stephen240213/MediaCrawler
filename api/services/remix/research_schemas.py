"""Neutral research contracts, independent of the target creator's writing brief."""
from typing import Literal
from pydantic import Field, model_validator
from .schemas import StrictModel

CATEGORIES = ('费用条件', '位置配套', '使用体验', '风险核查', '表达方法', '其他')


class ResearchSpace(StrictModel):
    name: str = Field(min_length=1, max_length=150)
    kind: Literal['single', 'account', 'topic']
    content_id: str = Field(default='', max_length=200)
    account_id: str = Field(default='', pattern=r'^([a-f0-9]{64})?$')
    keywords: list[str] = Field(default_factory=list, max_length=20)
    contains: list[str] = Field(default_factory=list, max_length=20)
    excludes: list[str] = Field(default_factory=list, max_length=20)
    include_ids: list[str] = Field(default_factory=list, max_length=200)
    exclude_ids: list[str] = Field(default_factory=list, max_length=200)
    selection_mode: Literal['rules','selected','task'] = 'rules'
    task_id: str = Field(default='',pattern=r'^([a-f0-9]{32})?$')
    start: str = Field(default='', pattern=r'^(\d{4}-\d{2}-\d{2})?$')
    end: str = Field(default='', pattern=r'^(\d{4}-\d{2}-\d{2})?$')
    archived: bool = False
    version: int = Field(default=0, ge=0)

    @model_validator(mode='after')
    def valid_scope(self):
        from datetime import date
        if not self.name.strip(): raise ValueError('研究名称不能为空')
        for field in ('keywords', 'contains', 'excludes', 'include_ids', 'exclude_ids'):
            values=getattr(self,field)
            if any(not s.strip() or len(s)>200 for s in values): raise ValueError('规则词需为1–200字')
            setattr(self,field,sorted(set(s.strip() for s in values)))
        for value in (self.start,self.end):
            if value: date.fromisoformat(value)
        if self.start and self.end and self.start>self.end: raise ValueError('开始日期不能晚于结束日期')
        if self.kind=='single' and not self.content_id: raise ValueError('请选择单条素材')
        if self.kind=='account' and not self.account_id: raise ValueError('请选择具有可靠ID的发布者')
        if self.selection_mode=='selected' and not self.include_ids: raise ValueError('请直接选择至少一条资料')
        if self.selection_mode=='task' and not self.task_id: raise ValueError('请选择采集批次')
        if self.kind=='topic' and self.selection_mode=='rules' and not (self.keywords or self.contains or self.include_ids): raise ValueError('专题至少需要一个来源关键词、匹配词或指定素材')
        return self


class ResearchRun(StrictModel):
    plan_token: str = Field(pattern=r'^[a-f0-9]{64}$')
    consent: bool = False
    max_calls: int = Field(default=20, ge=1, le=40)
    request_key: str = Field(pattern=r'^[a-zA-Z0-9_-]{16,128}$')


class Asset(StrictModel):
    kind: Literal['来源声明', '读者问题', '案例', '反例', '方法候选', '选题']
    category: Literal['费用条件', '位置配套', '使用体验', '风险核查', '表达方法', '其他']
    text: str = Field(min_length=1,max_length=800)
    evidence_ids: list[str] = Field(min_length=1,max_length=10)


class Material(StrictModel):
    summary: str = Field(min_length=1,max_length=1200)
    opening: str = Field(max_length=500)
    structure: str = Field(max_length=700)
    assets: list[Asset] = Field(max_length=12)
    limitations: list[str] = Field(max_length=3)


class CommentContribution(StrictModel):
    evidence_id: str = Field(max_length=200)
    category: Literal['费用条件', '位置配套', '使用体验', '风险核查', '表达方法', '其他']
    need: str = Field(max_length=400)


class Comments(StrictModel):
    contributions: list[CommentContribution] = Field(max_length=100)


class Synthesis(StrictModel):
    summary: str = Field(min_length=1,max_length=1800)
    findings: list[Asset] = Field(max_length=10)
    gaps: list[str] = Field(max_length=5)


class AssetTags(StrictModel):
    tags: list[str] = Field(max_length=12)
    version: int = Field(ge=0)

    @model_validator(mode='after')
    def valid_tags(self):
        if any(not t.strip() or len(t)>60 for t in self.tags): raise ValueError('类目标签需为1–60字，可用 / 表达层级')
        self.tags=sorted(set(t.strip() for t in self.tags))
        return self
