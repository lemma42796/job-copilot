"""JD 库 + 一键分析 REST IO schema(M2.5,4-API_SPEC §6)。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class JdCreateIn(BaseModel):
    """JD 上传 — 文本 OR 截图(截图前端先调多模态 OCR 转文本再 POST)。"""

    raw_text: str
    extras: dict[str, Any] = Field(default_factory=dict)


class JdPatchIn(BaseModel):
    title: str | None = None
    extras: dict[str, Any] | None = None


class JdOut(BaseModel):
    id: int
    title: str
    raw_text: str
    parsed_payload: dict[str, Any]
    parse_model: str
    cost_usd: float
    created_at: datetime


class JdAnalysisCreateIn(BaseModel):
    jd_ids: list[int]
    note: str | None = None


class AggregatedRequirement(BaseModel):
    id: str
    canonical_text: str
    category: str
    frequency: float
    raw_phrases: list[str]
    supporting_jd_ids: list[int]


class JdAnalysisOut(BaseModel):
    id: int
    jd_count: int
    aggregated_requirements: list[AggregatedRequirement]
    learning_path_md: str
    cost_usd: float
    created_at: datetime
