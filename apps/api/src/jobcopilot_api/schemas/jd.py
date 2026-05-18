"""JD 库 + 一键分析 REST IO schema(M2.5,4-API_SPEC §6)。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

JdSource = Literal["text_paste", "image_upload"]


class JdCreateIn(BaseModel):
    """JD 上传 — M2.5 第一刀先支持文本粘贴。

    截图 OCR 后续会走 multipart + Qwen 多模态 OCR,再复用同一条 parsed
    payload 入库路径。
    """

    source: JdSource = "text_paste"
    raw_text: str = Field(min_length=1, max_length=10_000)


class JdPatchIn(BaseModel):
    """JD 库里用户可改 LLM 自动抽出的 title。"""

    title: str | None = Field(default=None, max_length=255)


class JdOut(BaseModel):
    id: int
    source: JdSource
    title: str
    raw_text: str
    parsed_payload: dict[str, Any]
    parse_model: str | None = None
    parse_prompt_version: str | None = None
    parse_tokens_in: int | None = None
    parse_tokens_out: int | None = None
    parse_cost_cny: Decimal | None = None
    created_at: datetime
    updated_at: datetime


class JdListItemOut(BaseModel):
    id: int
    title: str
    source: JdSource
    raw_text_preview: str
    hard_skills_count: int
    created_at: datetime


class JdListOut(BaseModel):
    items: list[JdListItemOut]
    next_cursor: int | None = None
    has_more: bool = False


class JdAnalysisFilter(BaseModel):
    type: Literal["all", "title", "ids", "recent"]
    value: str | None = None
    ids: list[int] | None = None
    n: int | None = Field(default=None, ge=1, le=200)


class JdAnalysisCreateIn(BaseModel):
    filter: JdAnalysisFilter
    filter_description: str | None = Field(default=None, max_length=255)


class AggregatedRequirement(BaseModel):
    id: str
    canonical_text: str
    category: str
    frequency: float
    raw_phrases: list[str]
    supporting_jd_ids: list[int]


class JdAnalysisListItemOut(BaseModel):
    id: int
    jd_count: int
    filter_description: str | None = None
    status: str
    requirement_count: int
    quiz_topic_count: int
    started_at: datetime
    completed_at: datetime | None = None
    failed_at: datetime | None = None


class JdAnalysisListOut(BaseModel):
    items: list[JdAnalysisListItemOut]
    next_cursor: int | None = None
    has_more: bool = False


class JdAnalysisOut(BaseModel):
    id: int
    jd_ids: list[int]
    jd_count: int
    filter_description: str | None = None
    status: str
    aggregated_requirements: list[AggregatedRequirement] = Field(default_factory=list)
    learning_path_md: str | None = None
    quiz_topic_candidates: list[dict[str, Any]] = Field(default_factory=list)
    note_match_summary: list[dict[str, Any]] = Field(default_factory=list)
    total_tokens_in: int | None = None
    total_tokens_out: int | None = None
    total_cost_cny: Decimal | None = None
    cache_hit_rate: Decimal | None = None
    started_at: datetime
    completed_at: datetime | None = None
    failed_at: datetime | None = None
    failure_reason: str | None = None
