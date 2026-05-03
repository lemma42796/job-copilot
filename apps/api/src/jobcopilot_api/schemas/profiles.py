"""Profile parse / list / detail / patch wire contract.

Pydantic models here are the API boundary; ORM in `models/profile.py`
is the DB boundary; mapping between them is the service layer's job.

`ProfileStructured` mirrors AGENT_DESIGN §4.3 and is what the LLM
produces. Unlike `JDStructured` (single flat row), the parsed result
spans 5 child tables, so the service layer rebuilds children with a
DELETE-then-INSERT (Q3 decision in S7 plan).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

_YEAR_RE = re.compile(r"^\d{4}$")
_YEAR_MONTH_RE = re.compile(r"^(\d{4})-(\d{1,2})$")


def _pad_partial_date(v: object) -> object:
    """Pad resume-style partial dates to `YYYY-MM-DD` so pydantic's `date` accepts them.

    LLMs return dates exactly as written in the resume — `"2020-01"`,
    `"2016"` — which `datetime.date.fromisoformat` rejects. We pad missing
    components to `01`; the UI drops the day on render. `""` is treated as
    `None` (LLMs sometimes emit empty strings instead of nulls).
    """
    if not isinstance(v, str):
        return v
    s = v.strip()
    if not s:
        return None
    m = _YEAR_MONTH_RE.fullmatch(s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-01"
    if _YEAR_RE.fullmatch(s):
        return f"{s}-01-01"
    return v


PartialDate = Annotated[date | None, BeforeValidator(_pad_partial_date)]


class ProfileSource(StrEnum):
    """`profile_source` PG ENUM (migration 0009)."""

    PDF_UPLOAD = "pdf_upload"
    TEXT_PASTE = "text_paste"
    MANUAL = "manual"


class ProfileStatus(StrEnum):
    """`profile_status` PG ENUM (migration 0009)."""

    PARSING = "parsing"
    PARSED = "parsed"
    PARSE_FAILED = "parse_failed"


# ---------------------------------------------------------------------------
# ProfileStructured: LLM extraction result. AGENT_DESIGN §4.3.
# ---------------------------------------------------------------------------


SkillCategory = Literal["language", "framework", "tool", "database", "cloud", "other"]
SkillLevel = Literal["beginner", "intermediate", "advanced", "expert"]


class ProfileExperienceItem(BaseModel):
    company: str
    title: str
    location: str | None = None
    start_date: PartialDate = None
    end_date: PartialDate = None
    is_current: bool = False
    description: str = ""
    bullets: list[str] = Field(default_factory=list)
    tech_stack: list[str] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)


class ProfileProjectItem(BaseModel):
    name: str
    role: str | None = None
    start_date: PartialDate = None
    end_date: PartialDate = None
    description: str = ""
    bullets: list[str] = Field(default_factory=list)
    tech_stack: list[str] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    repo_url: str | None = None
    demo_url: str | None = None


class ProfileSkillItem(BaseModel):
    name: str = Field(description="归一化技能名,小写,如 'python','langchain'")
    name_raw: str
    category: SkillCategory = "other"
    level: SkillLevel | None = None
    years: float | None = None


class ProfileEducationItem(BaseModel):
    school: str
    degree: str | None = None
    major: str | None = None
    start_date: PartialDate = None
    end_date: PartialDate = None
    gpa: float | None = None
    honors: list[str] = Field(default_factory=list)


class ProfileStructured(BaseModel):
    full_name: str | None = None
    phone: str | None = None
    email: str | None = None
    location: str | None = None
    summary: str | None = None
    target_titles: list[str] = Field(default_factory=list)
    experiences: list[ProfileExperienceItem] = Field(default_factory=list)
    projects: list[ProfileProjectItem] = Field(default_factory=list)
    skills: list[ProfileSkillItem] = Field(default_factory=list)
    educations: list[ProfileEducationItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# POST /v1/profiles/parse (SSE only — long resumes)
# ---------------------------------------------------------------------------


class ProfileParseInput(BaseModel):
    """Body for POST /v1/profiles/parse.

    Exactly one of `text` or `file_id`; `source` must agree.
    """

    model_config = ConfigDict(extra="forbid")

    text: str | None = None
    file_id: int | None = None
    source: Literal["text_paste", "pdf_upload"]

    @model_validator(mode="after")
    def _check_exactly_one(self) -> Self:
        has_text = self.text is not None and self.text.strip() != ""
        has_file = self.file_id is not None
        if has_text == has_file:
            raise ValueError("exactly one of `text` or `file_id` must be provided")
        if self.source == "text_paste" and not has_text:
            raise ValueError("source=text_paste requires non-empty `text`")
        if self.source == "pdf_upload" and not has_file:
            raise ValueError("source=pdf_upload requires `file_id`")
        return self


# ---------------------------------------------------------------------------
# GET /v1/profiles + /v1/profiles/{id}
# ---------------------------------------------------------------------------


class ProfileListItem(BaseModel):
    """Light row for listing — strips structured children."""

    id: int
    source: ProfileSource
    status: ProfileStatus
    full_name: str | None
    location: str | None
    parse_model: str | None
    created_at: datetime
    updated_at: datetime


class ProfileListResponse(BaseModel):
    data: list[ProfileListItem]
    next_cursor: str | None = None
    has_more: bool = False


class ProfileStats(BaseModel):
    """Aggregate counts surfaced on the detail view (AGENT_DESIGN §4.5).

    `chunks` stays 0 until S8 wires the rechunk pipeline.
    """

    experiences: int
    projects: int
    skills: int
    educations: int
    chunks: int = 0


class ProfileDetail(BaseModel):
    """Full profile + children, returned by GET /v1/profiles/{id}."""

    id: int
    source: ProfileSource
    status: ProfileStatus
    raw_text: str | None
    raw_file_id: int | None
    structured: ProfileStructured
    target_salary_min: int | None
    target_salary_max: int | None
    parse_model: str | None
    parse_tokens: int | None
    parse_cost_cny: Decimal | None
    stats: ProfileStats
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# PATCH /v1/profiles/{id}
# ---------------------------------------------------------------------------


class ProfilePatchInput(BaseModel):
    """Allowed fields. `raw_text` is the source of truth for re-parsing
    and is NOT patchable; to re-parse with edits, DELETE + POST /parse.

    Setting `structured` rewrites all children (DELETE-then-INSERT). Skill
    `name` collisions inside the new payload are deduped (last write wins)
    so the unique constraint `uq_ps_profile_name` won't trip the request.
    """

    model_config = ConfigDict(extra="forbid")

    structured: ProfileStructured | None = None
    status: Literal["parsing", "parsed"] | None = None
    target_salary_min: int | None = None
    target_salary_max: int | None = None


# ---------------------------------------------------------------------------
# GET /v1/profiles/{id}/chunks (S8 C5)
# ---------------------------------------------------------------------------


ChunkGranularity = Literal["experience", "project", "skill", "summary"]


class ProfileChunkItem(BaseModel):
    """One `profile_chunks` row. The 1024-dim `embedding` is intentionally
    not exposed — the route is for debugging/visualization, not retrieval,
    and shipping float[1024] per row blows up the payload."""

    id: int
    granularity: ChunkGranularity
    source_table: str
    source_id: int
    content: str
    embed_model: str | None
    embed_version: str | None
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ProfileChunksResponse(BaseModel):
    data: list[ProfileChunkItem]
    total: int
