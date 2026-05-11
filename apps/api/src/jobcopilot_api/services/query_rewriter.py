"""QueryRewriter service — retrieval pipeline 第一段(M2)。

5-AGENT_DESIGN §2.7:把用户聊天框短 query 扩成同义/相邻概念集,提高全库
hybrid search 召回。失败回退原 query 不阻塞(rerank / hybrid 也会贡献结果,
不让一段 LLM 失败把整个 retrieval 链断掉)。

调用方:services/retrieval_pipeline.py(M2)。
"""

from __future__ import annotations

import logging

from jobcopilot_api.infra.llm import get_llm_client
from jobcopilot_api.llm.client import LLMClient
from jobcopilot_api.llm.errors import LLMError
from jobcopilot_api.llm.tiers import Tier
from jobcopilot_api.schemas.agents.query_rewriter import QueryRewriteOutput

logger = logging.getLogger(__name__)

PROMPT_NAME = "query_rewriter"
PROMPT_VERSION = "v1.1"  # v1.1 加 #6 防呆约束(乱码 / 无主题 query 不扩)

# 5-AGENT §2.7.2 — 改一次 bump version,不直接改字面量(沿用 v1 LESSONS §8.2)
SYSTEM_PROMPT = """你是 RAG 检索 query 改写 Agent。任务:把用户的短 query 扩成同义 / 相邻概念集,以提高笔记库 hybrid search 的召回。

【硬约束】
1. 扩展项 ≤ 5 个,首项必须是原 query 不变
2. 只扩"同义词 / 强相邻概念 / 常见同领域术语",不扩"行业常识 / 上位概念 / 不相关概念"
3. 中英混合保留:笔记里可能有"synchronized"也可能有"同步锁",两者都列
4. 不解释 / 不评论 / 不输出原 query 的复述
5. 严格 JSON,无前后散文
6. 如果 query 是乱码 / keyboard mash / 不是任何明确的技术或学习主题:**只返回原 query 一项**,不扩任何 meta 描述(禁止扩出 "random string"、"garbage input"、"invalid query"、"unknown" 等元词);rationale 写"查询无明确主题,跳过扩展"

【输出格式】
{
  "expanded_queries": ["<原 query>", "<同义/相邻 1>", ...],
  "rationale": "<中文一句话>"
}"""

USER_TEMPLATE = "用户 query:{user_query}"

# §2.7.4 失败回退:trace warning 但不阻塞流程
FALLBACK_RATIONALE = "query_rewrite 失败,回退原 query"


async def rewrite_query(
    user_query: str,
    *,
    llm: LLMClient | None = None,
) -> QueryRewriteOutput:
    """LLM 改写 query,失败回退 [user_query]。

    总是返回 QueryRewriteOutput(失败时 expanded_queries=[user_query]),
    上层不需要 try/except;trace 里 langfuse generation 已记 ERROR 痕迹。
    """
    client = llm or get_llm_client()
    try:
        result = await client.complete(
            feature=PROMPT_NAME,
            tier=Tier.CHEAP,  # 5-AGENT §2.1 thinking off
            system=SYSTEM_PROMPT,
            user=USER_TEMPLATE.format(user_query=user_query),
            response_schema=QueryRewriteOutput,
        )
    except LLMError as exc:
        logger.warning(
            "query_rewrite failed, falling back to user_query: %s", exc
        )
        return QueryRewriteOutput(
            expanded_queries=[user_query],
            rationale=FALLBACK_RATIONALE,
        )

    parsed = result.parsed
    assert isinstance(parsed, QueryRewriteOutput)  # response_schema 已校验

    # 后处理强约束(§2.7.2 硬约束 #1):首项必为原 query,去重,截 5
    expanded = _normalize(parsed.expanded_queries, user_query)
    return QueryRewriteOutput(
        expanded_queries=expanded,
        rationale=parsed.rationale,
    )


def _normalize(expanded: list[str], user_query: str) -> list[str]:
    """首项必为原 query;后续去重(保序)+ 截到 5 项。"""
    out = [user_query]
    seen = {user_query}
    for q in expanded:
        q = q.strip()
        if not q or q in seen:
            continue
        out.append(q)
        seen.add(q)
        if len(out) >= 5:
            break
    return out
