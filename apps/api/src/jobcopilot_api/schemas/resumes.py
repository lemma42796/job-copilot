"""Resume wire contract. API_SPEC §6.6 + AGENT_DESIGN §7.

Pydantic 是 API 边界,ORM 在 `models/resume.py` 是 DB 边界,mapping 在
service / router 层。

Drafter 不走 `response_schema`(MVP 决策):简历正文是长 markdown,JSON
包装会让 LLM 把整段 markdown 转义到一个字符串字段里,大概率把 \n / 代码块
里的引号搞乱;直接 plain text 把 LLM 输出当 markdown 收最稳。Reviewer
仍走 schema(`ResumeReview`),`passed: bool + findings: list` 是结构化拿
来落库的。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ResumeStatus(StrEnum):
    """`resume_status` PG ENUM (migration 0012)。

    `generating` 替代 match 的 `pending` —— 永久约束 #4 phase-1 落库即可发
    `started` 带 resource_id;`review_failed` 是业务级失败的特殊态,
    markdown 仍保留供前端展示。"""

    GENERATING = "generating"
    REVIEW_FAILED = "review_failed"
    READY = "ready"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Reviewer LLM 输出 schema(AGENT_DESIGN §7.3.4)
# ---------------------------------------------------------------------------


class ReviewFinding(BaseModel):
    """单条 reviewer findings。同时复用为 `resumes.review_findings` JSONB
    的元素结构 + GET /resumes/{id} 的 wire 形态。"""

    section: str = Field(description="草稿中出现问题的章节(如 '## 项目经历')")
    quoted_text: str = Field(description="草稿中有问题的原文片段(用于前端高亮)")
    issue_type: Literal["fabrication", "exaggeration", "unsupported_number", "other"]
    severity: Literal["high", "medium", "low"]
    explanation: str = Field(description="为什么标记此处问题(一句话,具体到差异)")


class ResumeReview(BaseModel):
    """LLM reviewer 输出对象(AGENT_DESIGN §7.3.4)。

    `passed=true` 仅当无 high severity finding;medium/low 不阻断,只在前端
    展示警告条。"""

    passed: bool = Field(description="若存在任意 severity='high' 的 finding 则 false")
    findings: list[ReviewFinding] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# POST /v1/resumes/generate input
# ---------------------------------------------------------------------------


class ResumeCreateInput(BaseModel):
    """Body for POST /v1/resumes/generate。MVP 收窄入口 → 仅 match 详情页
    触发,所以 `match_id` 实际上必传以拿 gap_summary hint;但 schema 保留
    Optional 留给未来 JD 详情 / 直入接口。"""

    model_config = ConfigDict(extra="forbid")

    jd_id: int = Field(gt=0)
    profile_id: int = Field(gt=0)
    match_id: int | None = Field(default=None, description="可选:从 match 详情页触发时带上,用于把 gap_summary 作为 drafter hint")


# ---------------------------------------------------------------------------
# GET /v1/resumes list / detail
# ---------------------------------------------------------------------------


class ResumeListItem(BaseModel):
    """List 行 — 不带 markdown 全文,只带前端列表卡需要的字段。"""

    id: int
    status: ResumeStatus
    jd_id: int
    profile_id: int
    match_id: int | None
    title: str | None
    review_passed: bool | None
    review_findings_count: int
    cost_cny: Decimal | None
    created_at: datetime


class ResumeListResponse(BaseModel):
    data: list[ResumeListItem]
    next_cursor: str | None = None
    has_more: bool = False


class ResumeTokens(BaseModel):
    input: int
    output: int


class ResumeDetail(BaseModel):
    """Full row for GET /v1/resumes/{id}。"""

    id: int
    status: ResumeStatus
    jd_id: int
    profile_id: int
    match_id: int | None
    title: str | None
    markdown: str | None
    review_passed: bool | None
    review_findings: list[ReviewFinding]
    generation_model: str | None
    review_model: str | None
    tokens: ResumeTokens | None
    cost_cny: Decimal | None
    latency_ms: int | None
    created_at: datetime
    updated_at: datetime
