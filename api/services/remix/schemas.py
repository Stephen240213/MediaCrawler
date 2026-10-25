from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class JobInput(StrictModel):
    source: Literal['xhs', 'upload']
    content_id: str = Field(default='', max_length=200)
    upload_id: str = Field(default='', pattern=r'^([a-f0-9]{32})?$', max_length=32)
    title: str = Field(default='', max_length=200)
    analyze_video: bool = False
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


class AnalysisReport(StrictModel):
    summary: str = Field(min_length=1, max_length=1500)
    summary_evidence_ids: list[str] = Field(min_length=1, max_length=12)
    sections: list[Section] = Field(min_length=4, max_length=4)
    ideas: list[Idea] = Field(min_length=1, max_length=5)
    limitations: list[str] = Field(min_length=1, max_length=12)
