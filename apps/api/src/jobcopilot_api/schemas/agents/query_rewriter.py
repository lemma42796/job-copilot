"""QueryRewriter IO schema(5-AGENT_DESIGN §2.7.1)。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class QueryRewriteInput(BaseModel):
    user_query: str


QueryIntent = Literal[
    "topic_interview",
    "project_fact",
    "boundary_question",
    "zero_hit_candidate",
]

QueryRole = Literal[
    "original",
    "synonym",
    "entity_preserving",
    "adjacent",
    "broad",
]


class WeightedQuery(BaseModel):
    """一条可参与跨 query RRF 的 query 及其投票权重。"""

    query: str
    role: QueryRole
    weight: float = Field(ge=0.0, le=2.0)


class QueryRewriteOutput(BaseModel):
    """Pydantic schema 跟 query_rewriter SYSTEM prompt 锁定的 JSON 对齐。

    - intent/core_entities/must_keep_terms:query understanding 诊断与排序保护
    - weighted_queries:跨 query RRF 使用的 query + weight
    - expanded_queries:审计兼容字段,首项必为原 query,≤ 5 项
    - rationale:中文一句,trace 可见;不参与下游计算
    """

    intent: QueryIntent
    core_entities: list[str] = Field(default_factory=list, max_length=8)
    must_keep_terms: list[str] = Field(default_factory=list, max_length=8)
    weighted_queries: list[WeightedQuery] = Field(min_length=1, max_length=5)
    expanded_queries: list[str] = Field(min_length=1, max_length=5)
    rationale: str
