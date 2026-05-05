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
# Planner LLM 输出 schema(M3 W7 — 5 节点 graph 的 plan 节点)
# ---------------------------------------------------------------------------


class ResumeSectionPlan(BaseModel):
    """Planner 对单个简历章节的取舍计划。"""

    section: Literal[
        "## 基本信息",
        "## 求职意向",
        "## 专业概要",
        "## 工作经历",
        "## 项目经历",
        "## 技能",
        "## 教育背景",
    ]
    rationale: str = Field(description="为什么这样写这个章节(简短一句话,指向 JD 命中点)")
    must_include_chunk_ids: list[int] = Field(
        default_factory=list,
        description="必须出现在该章节的 chunk_id 列表(planner 从 retrieve top-K 中挑出最对口的)",
    )
    skip: bool = Field(
        default=False, description="是否整章节跳过(基本信息 / 教育在 candidate 字段全空时设 true)"
    )


class ResumePlan(BaseModel):
    """Planner 输出的章节计划。drafter 据此组织行文。"""

    overall_strategy: str = Field(
        description="3-5 句话:候选人最契合 JD 的 2-3 条核心优势 + 整体行文策略"
    )
    emphasis_skills: list[str] = Field(
        default_factory=list,
        description="必须在简历中显式出现的关键技能(JD hard_skills ∩ chunks 实际命中)",
    )
    de_emphasize: list[str] = Field(
        default_factory=list,
        description="淡化但不删除的内容(与 JD 关联弱的经历 / 早期项目 / 副项目可标记此处)",
    )
    sections: list[ResumeSectionPlan]


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
    match_id: int | None = Field(
        default=None,
        description="可选:从 match 详情页触发时带上,用于把 gap_summary 作为 drafter hint",
    )


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


# ---------------------------------------------------------------------------
# GET /v1/resumes/{id}/versions + POST /v1/resumes/{id}/versions(W8)
# ---------------------------------------------------------------------------


RESUME_VERSION_NOTE_MAX = 200


class ResumeVersionItem(BaseModel):
    """单条版本快照。`edit_type` 区分来源:
    - `generated`:graph 跑出的初版(W7 起;revise 不另起 version,直接覆盖 v1 的 markdown)
    - `edited`:用户在 monaco 编辑器手改后保存
    - `regenerated`:M3 后续 `/regenerate` 整篇重跑(留 placeholder)"""

    id: int
    version_number: int
    markdown: str
    edit_type: Literal["generated", "edited", "regenerated"] | None
    edit_note: str | None
    created_at: datetime


class ResumeVersionListResponse(BaseModel):
    data: list[ResumeVersionItem]


class ResumeVersionCreateInput(BaseModel):
    """POST /v1/resumes/{id}/versions body。

    `markdown` 必填非空(空版本无意义);`note` 可选(用户描述这次编辑做了
    什么),200 字符上限避免被滥用为长篇 changelog。"""

    model_config = ConfigDict(extra="forbid")

    markdown: str = Field(min_length=1)
    note: str | None = Field(default=None, max_length=RESUME_VERSION_NOTE_MAX)
