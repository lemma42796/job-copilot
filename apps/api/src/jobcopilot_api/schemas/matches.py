"""Match wire contract. API_SPEC §6.5 + AGENT_DESIGN §6.3.

Pydantic 是 API 边界,ORM 在 `models/match.py` 是 DB 边界,mapping 在
service / router 层。`MatchResult` 是 LLM 直接产出(`MatchAnalystAgent`),
落库时拆到 matches 表对应列。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MatchStatus(StrEnum):
    """`match_status` PG ENUM (migration 0011)."""

    PENDING = "pending"
    SCORED = "scored"
    FAILED = "failed"


class MatchDepth(StrEnum):
    """`POST /v1/matches` 的 depth 枚举(API_SPEC §6.5)。

    M2 MVP(S14)统一走 STANDARD tier,depth 仅作为 SSE 输入字段保留并
    校验,实际不区分;S14 归档卡注明,深度差分留 evals 阶段。"""

    QUICK = "quick"
    DEEP = "deep"


# ---------------------------------------------------------------------------
# MatchResult: LLM 输出。AGENT_DESIGN §6.3。
# ---------------------------------------------------------------------------


class MatchedSkill(BaseModel):
    name: str = Field(description="归一化技能名(小写,与 JDSkill.name 同口径)")
    strength: float = Field(ge=0.0, le=1.0, description="候选人在该技能的强度自评")
    evidence_chunk_ids: list[int] = Field(
        default_factory=list,
        description="支撑该技能的 profile_chunks.id 列表;必须来自 LLM 收到的 chunk 列表",
    )


class MissingSkill(BaseModel):
    name: str
    severity: Literal["critical", "major", "minor"]
    suggestion: str = Field(description="一句改进建议(可执行,不要空话)")


class MatchResult(BaseModel):
    """LLM 在 analyze 节点产出的对象。落库时拆到 matches 表对应列。"""

    score: int = Field(ge=0, le=100, description="0-100 总分,见评分规则")
    matched_skills: list[MatchedSkill] = Field(default_factory=list)
    missing_skills: list[MissingSkill] = Field(default_factory=list)
    advantage_summary: str = Field(max_length=400, description="优势分析(不超 400 字)")
    gap_summary: str = Field(max_length=400, description="差距分析(不超 400 字)")
    suggestions: list[str] = Field(default_factory=list, description="3-5 条可执行建议")


# ---------------------------------------------------------------------------
# POST /v1/matches input
# ---------------------------------------------------------------------------


class MatchCreateInput(BaseModel):
    """Body for POST /v1/matches。API_SPEC §6.5。"""

    model_config = ConfigDict(extra="forbid")

    jd_id: int = Field(gt=0)
    profile_id: int = Field(gt=0)
    depth: MatchDepth = MatchDepth.QUICK


# ---------------------------------------------------------------------------
# GET /v1/matches list / detail
# ---------------------------------------------------------------------------


class MatchListItem(BaseModel):
    """List 行 — 不带 chunk-level evidence,前端 list 页用。"""

    id: int
    status: MatchStatus
    jd_id: int
    profile_id: int
    score: int | None
    matched_skills_count: int
    missing_skills_count: int
    created_at: datetime


class MatchListResponse(BaseModel):
    data: list[MatchListItem]
    next_cursor: str | None = None
    has_more: bool = False


class MatchTokens(BaseModel):
    input: int
    output: int


class MatchDetail(BaseModel):
    """Full row for GET /v1/matches/{id}。`structured` 子段保持 LLM 原型,
    前端拿来渲染 chunk-evidence hover。"""

    id: int
    status: MatchStatus
    jd_id: int
    profile_id: int
    structured: MatchResult | None = None
    model: str | None = None
    tokens: MatchTokens | None = None
    cost_cny: Decimal | None = None
    latency_ms: int | None = None
    created_at: datetime
    updated_at: datetime
