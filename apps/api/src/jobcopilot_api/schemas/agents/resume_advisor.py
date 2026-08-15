"""ResumeAdvisor IO schema(docs/TECH_DESIGN.md + SQLAlchemy models / Pydantic schemas)。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from jobcopilot_api.schemas.agents.jd_aggregator import Requirement


class ResumeChunk(BaseModel):
    """简历段落 — 来自 resumes.parsed_chunks(SQLAlchemy models / Pydantic schemas)。"""

    position: str  # "§3" / "§4 项目经历 / 项目 A"
    type: Literal[
        "header", "summary", "skills", "experience", "project", "education", "other"
    ]
    content: str


class ResumeSuggestion(BaseModel):
    req_id: str | None
    req_text: str | None
    req_frequency: float | None
    resume_position: str | None
    coverage: Literal["strong", "weak", "missing"]
    diagnosis: str
    suggestion_topic: str | None
    tag: Literal["anchored", "unanchored"]


class ResumeAdvisorInput(BaseModel):
    requirements: list[Requirement]
    resume_chunks: list[ResumeChunk]


class ResumeAdvisorOutput(BaseModel):
    suggestions: list[ResumeSuggestion]
