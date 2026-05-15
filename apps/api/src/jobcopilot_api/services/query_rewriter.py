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
from jobcopilot_api.schemas.agents.query_rewriter import (
    QueryIntent,
    QueryRewriteOutput,
    WeightedQuery,
)

logger = logging.getLogger(__name__)

PROMPT_NAME = "query_rewriter"
PROMPT_VERSION = "v2.0"  # v2.0:Query Understanding + weighted queries

# 5-AGENT §2.7.2 — 改一次 bump version,不直接改字面量(沿用 v1 LESSONS §8.2)
SYSTEM_PROMPT = """你是 RAG 检索 Query Understanding Agent。任务:理解用户 query,抽取核心约束,并生成少量可加权的检索 query,用于笔记库 hybrid search。

【硬约束】
1. weighted_queries ≤ 5 个,第一项必须是原 query 不变,role="original",weight=2.0
2. expanded_queries 必须与 weighted_queries 的 query 字段同序同内容,用于审计兼容
3. intent 只能是:
   - "topic_interview":普通技术主题 / 学习主题
   - "project_fact":项目私有事实,例如 JobCopilot / M2 / AnswerJudge / Context Cache 等
   - "boundary_question":是否支持 / 为什么不 / 有无 / 当前边界 / 里程碑边界
   - "zero_hit_candidate":乱码 / 无明确主题 / 笔记可能没有覆盖的过泛主题
4. core_entities 放 query 中必须保真的实体 / 产品名 / 版本名 / 专有名词
5. must_keep_terms 放改写时必须保留的词,例如 "M2"、"JobCopilot"、"AnswerJudge"、"岗位类 query"
6. 对 project_fact 或 boundary_question:非 original query 必须保留至少一个 must_keep_terms/core_entities,禁止把私有事实泛化成行业常识
7. 只扩"同义词 / 强相邻概念 / 常见同领域术语",不扩"行业常识 / 上位概念 / 不相关概念"
8. 中英混合保留:笔记里可能有"synchronized"也可能有"同步锁",两者都列
9. 如果 query 是乱码 / keyboard mash / 不是任何明确的技术或学习主题:只返回原 query 一项,intent="zero_hit_candidate",不扩任何 meta 描述
10. 严格 JSON,无前后散文

【weight 规则】
- original 固定 2.0
- entity_preserving / synonym:1.0
- adjacent:0.75
- broad:0.5,且仅当没有稀释核心实体时才允许

【输出格式】
{
  "intent": "topic_interview",
  "core_entities": ["<核心实体>"],
  "must_keep_terms": ["<必须保留词>"],
  "weighted_queries": [
    {"query": "<原 query>", "role": "original", "weight": 2.0},
    {"query": "<同义/强相邻 1>", "role": "entity_preserving", "weight": 1.0}
  ],
  "expanded_queries": ["<原 query>", "<同义/强相邻 1>"],
  "rationale": "<中文一句话>"
}"""

USER_TEMPLATE = "用户 query:{user_query}"

# §2.7.4 失败回退:trace warning 但不阻塞流程
FALLBACK_RATIONALE = "query_rewrite 失败,回退原 query"

ORIGINAL_QUERY_WEIGHT = 2.0
DEFAULT_REWRITE_WEIGHT = 1.0
PROTECTED_INTENTS: set[QueryIntent] = {"project_fact", "boundary_question"}
ROLE_DEFAULT_WEIGHTS = {
    "original": ORIGINAL_QUERY_WEIGHT,
    "synonym": 1.0,
    "entity_preserving": 1.0,
    "adjacent": 0.75,
    "broad": 0.5,
}


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
            intent="zero_hit_candidate",
            core_entities=[],
            must_keep_terms=[],
            weighted_queries=[
                WeightedQuery(
                    query=user_query,
                    role="original",
                    weight=ORIGINAL_QUERY_WEIGHT,
                )
            ],
            expanded_queries=[user_query],
            rationale=FALLBACK_RATIONALE,
        )

    parsed = result.parsed
    assert isinstance(parsed, QueryRewriteOutput)  # response_schema 已校验

    weighted = _normalize_weighted_queries(parsed, user_query)
    return QueryRewriteOutput(
        intent=parsed.intent,
        core_entities=_normalize_terms(parsed.core_entities),
        must_keep_terms=_normalize_terms(parsed.must_keep_terms),
        weighted_queries=weighted,
        expanded_queries=[item.query for item in weighted],
        rationale=parsed.rationale,
    )


def query_weights(output: QueryRewriteOutput) -> list[float]:
    """返回与 expanded_queries 同序的跨 query RRF 权重。"""
    by_query = {item.query: item.weight for item in output.weighted_queries}
    return [
        by_query.get(query, DEFAULT_REWRITE_WEIGHT)
        for query in output.expanded_queries
    ]


def _normalize_terms(terms: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for term in terms:
        normalized = " ".join(term.strip().split())
        if not normalized or normalized in seen:
            continue
        out.append(normalized)
        seen.add(normalized)
        if len(out) >= 8:
            break
    return out


def _normalize_weighted_queries(
    parsed: QueryRewriteOutput,
    user_query: str,
) -> list[WeightedQuery]:
    """首项必为原 query;后续去重、保护核心实体、截到 5 项。"""
    weighted: list[WeightedQuery] = [
        WeightedQuery(
            query=user_query,
            role="original",
            weight=ORIGINAL_QUERY_WEIGHT,
        )
    ]
    seen = {user_query}
    protected_terms = _normalize_terms(
        [*parsed.must_keep_terms, *parsed.core_entities]
    )
    if parsed.intent == "zero_hit_candidate":
        return weighted
    if parsed.intent in PROTECTED_INTENTS and not protected_terms:
        return weighted

    for item in parsed.weighted_queries:
        q = " ".join(item.query.strip().split())
        if not q or q in seen or q == user_query:
            continue
        if (
            parsed.intent in PROTECTED_INTENTS
            and protected_terms
            and not _contains_any_term(q, protected_terms)
        ):
            continue
        role = "entity_preserving" if item.role == "original" else item.role
        max_weight = ROLE_DEFAULT_WEIGHTS.get(role, DEFAULT_REWRITE_WEIGHT)
        weight = min(max(float(item.weight), 0.0), max_weight)
        if weight <= 0:
            continue
        weighted.append(WeightedQuery(query=q, role=role, weight=weight))
        seen.add(q)
        if len(weighted) >= 5:
            break

    # LLM 有时只填 expanded_queries,这里作为兼容兜底,仍受同样保护规则约束。
    for q in parsed.expanded_queries:
        q = " ".join(q.strip().split())
        if not q or q in seen or q == user_query:
            continue
        if (
            parsed.intent in PROTECTED_INTENTS
            and protected_terms
            and not _contains_any_term(q, protected_terms)
        ):
            continue
        weighted.append(
            WeightedQuery(
                query=q,
                role="entity_preserving",
                weight=DEFAULT_REWRITE_WEIGHT,
            )
        )
        seen.add(q)
        if len(weighted) >= 5:
            break
    return weighted


def _contains_any_term(query: str, terms: list[str]) -> bool:
    query_norm = query.casefold()
    return any(term.casefold() in query_norm for term in terms)
