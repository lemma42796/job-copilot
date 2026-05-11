"""QueryRewriter IO schema(5-AGENT_DESIGN §2.7.1)。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class QueryRewriteInput(BaseModel):
    user_query: str


class QueryRewriteOutput(BaseModel):
    """Pydantic schema 跟 5-AGENT §2.7.2 SYSTEM 锁定的 JSON 严格对齐。

    - expanded_queries:首项必为原 query,≤ 5 项(由 service 层后处理强约束)
    - rationale:中文一句,trace 可见;不参与下游计算
    """

    expanded_queries: list[str] = Field(min_length=1, max_length=5)
    rationale: str
