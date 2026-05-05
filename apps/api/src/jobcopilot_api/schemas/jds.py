"""JD parse / list / detail / patch wire contract. ADR-0006 D5.

Pydantic models here are the API boundary; ORM in `models/jd.py` is the
DB boundary; mapping between them is the service layer's job (D5).

`JDStructured` mirrors AGENT_DESIGN §3.3 and is what the LLM produces;
service layer fills in `salary_currency='CNY'` (D5: LLM does not extract
currency) when persisting.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class JdSource(StrEnum):
    """`jd_source` PG ENUM (migration 0003).

    M1 API only accepts `text_paste` / `pdf_upload` (ADR-0006 D1);
    `image_upload` ships with M1 末 (qwen3.6-flash 原生多模态); `extension_paste`
    is reserved for M5+.
    """

    TEXT_PASTE = "text_paste"
    PDF_UPLOAD = "pdf_upload"
    IMAGE_UPLOAD = "image_upload"
    EXTENSION_PASTE = "extension_paste"


class JdStatus(StrEnum):
    """`jd_status` PG ENUM (migration 0003)."""

    PARSING = "parsing"
    PARSED = "parsed"
    PARSE_FAILED = "parse_failed"


# ---------------------------------------------------------------------------
# JDStructured: LLM extraction result. AGENT_DESIGN §3.3.
# ---------------------------------------------------------------------------


class JDSkill(BaseModel):
    name: str = Field(description="归一化技能名,小写,如 'python','langchain'")
    name_raw: str
    required: bool
    weight: float = Field(ge=0.0, le=1.0)
    or_group_id: int | None = Field(
        default=None,
        description=(
            "B26:同 OR 组的多个 skill 共享同一个 group id(从 1 开始)。"
            "纯并列(无量词)→ null;'X / Y / Z 中至少一门' → 三个 skill 共享 or_group_id=1。"
            "多个独立 OR 组(如同时存在语言 OR + 数据库 OR)用不同的 id。"
        ),
    )


class JDStructured(BaseModel):
    company: str | None = None
    title: str
    location: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_period: Literal["monthly", "yearly"] = "monthly"
    salary_months: int | None = Field(
        default=None,
        description="JD 写明的 'X 薪' 数(14/16 等),代表年终奖月数;JD 未写则 None",
    )
    job_level: Literal["intern", "junior", "middle", "senior", "lead"] | None = None
    years_required: int | None = None
    education: (
        Literal[
            "专科",
            "本科",
            "硕士",
            "博士",
            "不限",  # B23:JD 写"学历不限"/未提
            "本科及以上",  # B23:保留 OR-higher 语义,不再降维成"本科"
            "任一档",  # B23:JD 写"本硕博均可"/"本科/硕士均可" 等并列档位
        ]
        | None
    ) = None
    hard_skills: list[JDSkill] = Field(default_factory=list)
    soft_skills: list[JDSkill] = Field(default_factory=list)
    bonus_skills: list[JDSkill] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    description: str = ""
    confidence: float = Field(ge=0.0, le=1.0, description="抽取置信度自评")


# ---------------------------------------------------------------------------
# POST /v1/jds/parse
# ---------------------------------------------------------------------------


class JDParseInput(BaseModel):
    """Body for POST /v1/jds/parse. ADR-0006 D1.

    Exactly one of `text` or `file_id` must be provided, and `source`
    must agree with the chosen branch.
    """

    model_config = ConfigDict(extra="forbid")

    text: str | None = None
    file_id: int | None = None
    source: Literal["text_paste", "pdf_upload"]
    # B25:LLM 不抽取的源链接 + 平台标识。前端粘贴时填(可选);留空则后端 NULL。
    source_url: str | None = None
    source_publisher: str | None = None

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


class JDTokens(BaseModel):
    input: int
    output: int


class JDParseResponse(BaseModel):
    """Synchronous response. ADR-0006 D4.

    On `parse_failed`, `structured` / `confidence` / `tokens` / `cost_cny`
    will be null (the HTTP layer raises 422/502 before this is returned;
    this shape exists for consistency with the SSE result envelope).
    """

    id: int
    status: JdStatus
    structured: JDStructured | None = None
    confidence: float | None = None
    tokens: JDTokens | None = None
    cost_cny: Decimal | None = None


# ---------------------------------------------------------------------------
# GET /v1/jds (list) and /v1/jds/{id} (detail)
# ---------------------------------------------------------------------------


class JDListItem(BaseModel):
    """Light row for listing — strips raw_text / skills / responsibilities."""

    id: int
    source: JdSource
    status: JdStatus
    company: str | None
    title: str | None
    location: str | None
    salary_min: int | None
    salary_max: int | None
    salary_currency: str | None
    parse_confidence: Decimal | None
    created_at: datetime


class JDListResponse(BaseModel):
    data: list[JDListItem]
    next_cursor: str | None = None
    has_more: bool = False


class JDDetail(BaseModel):
    """Full row for GET /v1/jds/{id}."""

    id: int
    source: JdSource
    status: JdStatus
    raw_text: str | None
    raw_file_id: int | None
    source_url: str | None = None
    source_publisher: str | None = None
    company: str | None
    title: str | None
    location: str | None
    salary_min: int | None
    salary_max: int | None
    salary_currency: str | None
    salary_period: str | None
    salary_months: int | None
    job_level: str | None
    years_required: int | None
    education: str | None
    hard_skills: list[JDSkill]
    soft_skills: list[JDSkill]
    bonus_skills: list[JDSkill]
    responsibilities: list[str]
    description: str | None
    parse_confidence: Decimal | None
    parse_model: str | None
    parse_tokens: int | None
    parse_cost_cny: Decimal | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# PATCH /v1/jds/{id}
# ---------------------------------------------------------------------------


class JDPatchInput(BaseModel):
    """Allowed fields per ADR-0006 D2.

    `raw_text` is the source of truth for re-parsing and is not patchable
    (D7); to re-parse with edits, the client should DELETE + POST /parse.
    `status` allows `parsing` / `parsed` only — `archived` extension waits
    for M4.

    `extra="forbid"`: unknown fields are 422 — drift between client and
    server should be loud, not silently ignored.
    """

    model_config = ConfigDict(extra="forbid")

    structured: JDStructured | None = None
    status: Literal["parsing", "parsed"] | None = None
