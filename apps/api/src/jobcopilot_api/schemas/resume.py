"""简历 + 诊断 REST IO schema(M3,OpenAPI / Pydantic schemas)。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


ResumeChunkType = Literal[
    "header", "summary", "skills", "experience", "project", "education", "other"
]


class ResumeChunk(BaseModel):
    position: str
    type: ResumeChunkType
    content: str


class ResumeOut(BaseModel):
    id: int
    title: str
    raw_text: str
    parsed_chunks: list[ResumeChunk]
    created_at: datetime


class ResumeAnalysisCreateIn(BaseModel):
    resume_id: int
    jd_analysis_id: int


class ResumeSuggestion(BaseModel):
    req_id: str | None
    req_text: str | None
    req_frequency: float | None
    resume_position: str | None
    coverage: Literal["strong", "weak", "missing"]
    diagnosis: str
    suggestion_topic: str | None
    tag: Literal["anchored", "unanchored"]


class ResumeAnalysisOut(BaseModel):
    id: int
    resume_id: int
    jd_analysis_id: int
    suggestions: list[ResumeSuggestion]
    anchored_ratio: float
    cost_usd: float
    created_at: datetime
