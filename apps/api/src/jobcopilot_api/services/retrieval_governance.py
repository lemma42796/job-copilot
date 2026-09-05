"""Lightweight candidate governance for retrieval.

This module deliberately avoids a schema migration. It infers coarse retrieval
signals from existing chunk metadata and applies conservative query-aware score
adjustments before final coarse ranking.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from jobcopilot_api.models.note_chunk import NoteChunk
from jobcopilot_api.schemas.agents.query_rewriter import (
    QueryIntent,
    QueryRewriteOutput,
)

ChunkSourceType = Literal[
    "canonical_project_fact",
    "project_doc",
    "interview_question_bank",
    "eval_case",
    "hard_negative",
    "generic_background",
]

PROTECTED_QUERY_INTENTS: set[QueryIntent] = {
    "project_fact",
    "boundary_question",
}

SOURCE_MULTIPLIERS_FOR_PROTECTED_INTENT: dict[ChunkSourceType, float] = {
    "canonical_project_fact": 1.12,
    "project_doc": 1.06,
    "generic_background": 1.0,
    "interview_question_bank": 0.86,
    "eval_case": 0.78,
    "hard_negative": 0.62,
}
POST_RERANK_COARSE_WEIGHT = 0.50
POST_RERANK_PROVIDER_WEIGHT = 0.32
POST_RERANK_GOVERNANCE_WEIGHT = 0.18
POST_RERANK_COARSE_KEEP_TOP_K = 50
POST_RERANK_DYNAMIC_MIN_K = 3
POST_RERANK_DYNAMIC_TARGET_K = 6
POST_RERANK_DIVERSITY_SOFT_K = 3
POST_RERANK_MAX_PER_NOTE_AFTER_SOFT_K = 2
POST_RERANK_MAX_PER_HEADING_AFTER_SOFT_K = 1
POST_RERANK_LATE_STRONG_MIN_FINAL_SCORE = 0.54
POST_RERANK_LATE_STRONG_MIN_GOVERNANCE = 0.88
POST_RERANK_LATE_PROVIDER_MIN_FINAL_SCORE = 0.60
POST_RERANK_LATE_PROVIDER_MIN_NORM_SCORE = 0.90
POST_RERANK_LATE_PROVIDER_MIN_GOVERNANCE = 0.76
POST_RERANK_FLOOR_MIN_GOVERNANCE = 0.62
POST_RERANK_CHALLENGER_MIN_GOVERNANCE = 0.70
POST_RERANK_EXTRA_MIN_GOVERNANCE = 0.78
POST_RERANK_HARD_NEGATIVE_CAP = 0.22
POST_RERANK_EVAL_CASE_CAP = 0.50
POST_RERANK_QUESTION_BANK_CAP = 0.58
PROTECTED_ANCHOR_ROUTE_WEIGHT = 5.0
PROTECTED_ANCHOR_TOP_K = 4
PROTECTED_ANCHOR_SQL_LIMIT = 200
STATE_RECOVERY_ANCHOR_MIN_SCORE = 8.0
PROVIDER_FAILURE_ANCHOR_MIN_SCORE = 10.0
ZERO_HIT_SUPPORT_TOP_K = 10
ZERO_HIT_MIN_REQUIRED_TERMS = 2
CONTRAST_EVIDENCE_MULTIPLIER = 1.12
CONTRAST_PRIMARY_TOPIC_MULTIPLIER = 1.05
CONTRAST_SECONDARY_TOPIC_MULTIPLIER = 0.82
CONTRAST_SINGLE_SIDE_MULTIPLIER = 0.94

_TECH_TERM_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+-]*|\d+[A-Za-z]*")
_SUPPORT_TERM_STOPWORDS = {
    "a",
    "an",
    "and",
    "api",
    "common",
    "comparison",
    "design",
    "detail",
    "details",
    "difference",
    "how",
    "implementation",
    "in",
    "interview",
    "is",
    "loop",
    "of",
    "or",
    "pattern",
    "question",
    "questions",
    "the",
    "to",
    "vs",
    "what",
}
_CONTRAST_MARKERS = (
    "区别",
    "不同",
    "对比",
    "compare",
    "comparison",
    "difference",
    " vs ",
    " versus ",
)
_CONTRAST_SEPARATORS = (" vs ", " versus ", " 和 ", " 与 ", " 跟 ", " 同 ")
_CONTRAST_SIGNAL_TERMS = (
    "区别",
    "不同",
    "对比",
    " vs ",
    " versus ",
    "不是",
    "不能",
    "不等于",
    "替代",
    "本身",
    "difference",
    "different",
    "instead",
    "replace",
)
_SHORT_SUPPORT_TERMS = {
    "api",
    "db",
    "gc",
    "io",
    "jd",
    "llm",
    "mq",
    "sdk",
    "sse",
    "ui",
}
_SUPPORT_TERM_ALIASES = {
    "mq": ("mq", "message queue", "message queues", "消息队列"),
    "k8s": ("k8s", "kubernetes"),
    "kubernetes": ("kubernetes", "k8s"),
}

STATE_RECOVERY_TRANSPORT_TERMS = (
    "sse",
    "eventsource",
    "text/event-stream",
    "client disconnected",
    "断线",
    "断开",
    "disconnect",
)
STATE_RECOVERY_STATE_TERMS = (
    "恢复",
    "重连",
    "reconnect",
    "session",
    "状态",
    "数据库",
    "postgres",
    "事实源",
    "落库",
    "/quiz?session",
)
STATE_RECOVERY_ANCHOR_PHRASES: tuple[tuple[str, float], ...] = (
    ("sse 断开不等于业务失败", 5.0),
    ("client disconnected", 5.0),
    ("submit_session_sse", 5.0),
    ("sse 只是通知通道", 4.0),
    ("不是唯一事实源", 4.0),
    ("前端重连后查 db 当前状态", 4.0),
    ("查 db 当前状态", 3.0),
    ("写数据库", 4.0),
    ("写回", 2.0),
    ("落库", 2.0),
    ("session 恢复", 4.0),
    ("状态恢复", 4.0),
    ("/quiz?session", 4.0),
    ("sse 未读缓冲", 2.5),
    ("session 状态", 2.0),
    ("postgres", 2.0),
    ("数据库状态", 2.0),
)
PROVIDER_FAILURE_ENTITY_TERMS = (
    "provider",
    "api provider",
    "llm provider",
    "模型 provider",
    "供应商",
)
PROVIDER_FAILURE_TIMEOUT_TERMS = (
    "timeout",
    "超时",
    "deadline",
    "read timeout",
    "connect timeout",
)
PROVIDER_FAILURE_RATE_LIMIT_TERMS = (
    "429",
    "rate limit",
    "rate limited",
    "retry-after",
    "too many requests",
    "限流",
)
PROVIDER_FAILURE_EVIDENCE_TERMS = (
    *PROVIDER_FAILURE_TIMEOUT_TERMS,
    *PROVIDER_FAILURE_RATE_LIMIT_TERMS,
    "retry",
    "重试",
    "backoff",
    "jitter",
)
PROVIDER_FAILURE_ANCHOR_PHRASES: tuple[tuple[str, float], ...] = (
    ("provider timeout", 5.0),
    ("provider api", 3.0),
    ("timeout 可重试", 4.0),
    ("可重试", 2.0),
    ("429 要解析 retry-after", 6.0),
    ("retry-after", 5.0),
    ("too many requests", 3.0),
    ("网络瞬态", 3.0),
    ("5xx", 2.0),
    ("4xx 不重试", 3.0),
    ("指数退避", 4.0),
    ("backoff", 3.0),
    ("jitter", 4.0),
    ("总 deadline", 4.0),
    ("deadline", 2.0),
    ("idempotency key", 5.0),
    ("幂等 key", 5.0),
    ("用户等待路径", 4.0),
    ("用户等待", 2.0),
    ("eval batch", 4.0),
    ("provider quota", 4.0),
    ("judge_retrying", 3.0),
    ("落库", 2.0),
)


@dataclass(frozen=True)
class RetrievalGovernanceContext:
    """Runtime context for query-aware candidate governance."""

    intent: QueryIntent
    core_entities: tuple[str, ...] = ()
    must_keep_terms: tuple[str, ...] = ()
    contrast_sides: tuple[tuple[str, ...], ...] = ()
    enabled: bool = False


@dataclass(frozen=True)
class QuerySupportAssessment:
    """Whether retrieved candidates sufficiently cover query anchors."""

    sufficient: bool
    reason: str
    required_terms: tuple[str, ...] = ()
    covered_terms: tuple[str, ...] = ()
    missing_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class PostRerankGovernanceDetail:
    """Explain how a candidate moved after provider rerank + governance blend."""

    chunk: NoteChunk
    coarse_rank: int | None
    rerank_rank: int | None
    coarse_score: float
    rerank_score: float | None
    normalized_rerank_score: float
    governance_score: float
    final_score: float
    source_type: ChunkSourceType
    source_multiplier: float
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class PostRerankGovernanceResult:
    """Selected chunks plus diagnostics for post-rerank governance."""

    selected: list[tuple[NoteChunk, float]]
    details: list[PostRerankGovernanceDetail]


def governance_context_from_rewrite(
    output: QueryRewriteOutput,
) -> RetrievalGovernanceContext:
    """Create governance context from query understanding output."""

    return RetrievalGovernanceContext(
        intent=output.intent,
        core_entities=tuple(output.core_entities),
        must_keep_terms=tuple(output.must_keep_terms),
        contrast_sides=_contrast_sides_from_rewrite(output),
        enabled=output.intent in PROTECTED_QUERY_INTENTS,
    )


def post_rerank_governance_blend(
    coarse_ranked: list[NoteChunk],
    rerank_scored: list[tuple[NoteChunk, float]],
    context: RetrievalGovernanceContext,
    expanded_queries: list[str],
    *,
    top_k: int,
    coarse_keep_top_k: int = POST_RERANK_COARSE_KEEP_TOP_K,
) -> PostRerankGovernanceResult:
    """Blend coarse rank, provider rerank, and deterministic governance.

    Provider rerank is challenger input, not full membership authority. The
    governed coarse top-K enters the competition first, then high-confidence
    provider-selected candidates from the wider pool can challenge weak slots.
    Final context is dynamic: keep enough clean evidence, avoid filling top-K
    with low-confidence related chunks.
    """

    if top_k <= 0 or not coarse_ranked:
        return PostRerankGovernanceResult(selected=[], details=[])

    coarse_rank_by_id = {
        int(chunk.id): rank
        for rank, chunk in enumerate(coarse_ranked, start=1)
    }
    rerank_rank_by_id = {
        int(chunk.id): rank
        for rank, (chunk, _) in enumerate(rerank_scored, start=1)
    }
    rerank_score_by_id = {
        int(chunk.id): float(score) for chunk, score in rerank_scored
    }
    rerank_norm_by_id = _normalize_rerank_scores(rerank_score_by_id)

    pool_by_id: dict[int, NoteChunk] = {}
    coarse_pool_size = min(
        len(coarse_ranked),
        max(top_k, coarse_keep_top_k, len(rerank_scored)),
    )
    for chunk in coarse_ranked[:coarse_pool_size]:
        pool_by_id[int(chunk.id)] = chunk
    for chunk, _ in rerank_scored:
        pool_by_id.setdefault(int(chunk.id), chunk)

    details: list[PostRerankGovernanceDetail] = []
    for chunk_id, chunk in pool_by_id.items():
        coarse_rank = coarse_rank_by_id.get(chunk_id)
        rerank_rank = rerank_rank_by_id.get(chunk_id)
        coarse_score = _rank_score(coarse_rank)
        normalized_rerank_score = rerank_norm_by_id.get(chunk_id, 0.0)
        governance = _post_rerank_governance_score(
            chunk,
            context,
            expanded_queries,
        )
        final_score = (
            POST_RERANK_COARSE_WEIGHT * coarse_score
            + POST_RERANK_PROVIDER_WEIGHT * normalized_rerank_score
            + POST_RERANK_GOVERNANCE_WEIGHT * governance.governance_score
        )
        if governance.score_cap is not None:
            final_score = min(final_score, governance.score_cap)
        details.append(
            PostRerankGovernanceDetail(
                chunk=chunk,
                coarse_rank=coarse_rank,
                rerank_rank=rerank_rank,
                coarse_score=coarse_score,
                rerank_score=rerank_score_by_id.get(chunk_id),
                normalized_rerank_score=normalized_rerank_score,
                governance_score=governance.governance_score,
                final_score=final_score,
                source_type=governance.source_type,
                source_multiplier=governance.source_multiplier,
                flags=governance.flags,
            )
        )

    selected_details = _select_dynamic_post_rerank_details(
        details,
        coarse_ranked,
        top_k=top_k,
    )
    selected_id_set = {int(item.chunk.id) for item in selected_details}
    rejected_details = [
        _with_selection_flag(item, "dynamic_rejected_low_confidence")
        for item in details
        if int(item.chunk.id) not in selected_id_set
    ]
    rejected_details.sort(
        key=lambda item: (
            -item.final_score,
            item.coarse_rank if item.coarse_rank is not None else 10**9,
            item.rerank_rank if item.rerank_rank is not None else 10**9,
            int(item.chunk.id),
        )
    )
    details = [*selected_details, *rejected_details]
    return PostRerankGovernanceResult(
        selected=[(item.chunk, item.final_score) for item in selected_details],
        details=details,
    )


def assess_query_support(
    context: RetrievalGovernanceContext,
    expanded_queries: list[str],
    chunks: list[NoteChunk],
    *,
    top_k: int = ZERO_HIT_SUPPORT_TOP_K,
) -> QuerySupportAssessment:
    """Check whether top candidates cover strong query anchors.

    The old zero-hit gate only asked "did hybrid search return enough rows?".
    For broad technical corpora that is too weak: Rust/Kubernetes-like
    out-of-corpus queries can still retrieve many nearby Java/Agent chunks.
    This gate is intentionally conservative and only activates when the
    original query exposes at least two strong technical anchors.
    """

    required_terms = _query_support_terms(context, expanded_queries)
    if len(required_terms) < ZERO_HIT_MIN_REQUIRED_TERMS:
        return QuerySupportAssessment(
            sufficient=True,
            reason="not_enough_strong_terms",
            required_terms=required_terms,
        )

    support_chunks = chunks[:top_k]
    covered_terms = tuple(
        term
        for term in required_terms
        if _support_term_covered(term, support_chunks)
    )
    missing_terms = tuple(
        term for term in required_terms if term not in covered_terms
    )
    min_covered = min(ZERO_HIT_MIN_REQUIRED_TERMS, len(required_terms))
    primary_term = required_terms[0]
    if primary_term not in covered_terms:
        return QuerySupportAssessment(
            sufficient=False,
            reason="primary_anchor_missing",
            required_terms=required_terms,
            covered_terms=covered_terms,
            missing_terms=missing_terms,
        )
    if len(covered_terms) < min_covered:
        return QuerySupportAssessment(
            sufficient=False,
            reason="too_few_anchors_covered",
            required_terms=required_terms,
            covered_terms=covered_terms,
            missing_terms=missing_terms,
        )
    return QuerySupportAssessment(
        sufficient=True,
        reason="anchors_covered",
        required_terms=required_terms,
        covered_terms=covered_terms,
        missing_terms=missing_terms,
    )


async def protected_anchor_search(
    session: AsyncSession,
    context: RetrievalGovernanceContext,
    expanded_queries: list[str],
    *,
    top_k: int = PROTECTED_ANCHOR_TOP_K,
    user_id: int | None = None,
) -> list[NoteChunk]:
    """Supplement protected queries with exact failure-recovery anchors.

    Vector/lexical top-K can miss short project-private fact chunks when many
    generic chunks share the same vocabulary. These routes are kept
    deliberately narrow: they only fire for known failure-recovery query shapes
    and only return chunks that match strong evidence phrases.
    """

    route = _protected_anchor_route(context, expanded_queries)
    if route is None:
        return []

    path_text = sa.func.lower(
        sa.func.concat_ws(
            " ",
            sa.func.array_to_string(NoteChunk.folder_path, " "),
            sa.func.array_to_string(NoteChunk.heading_path, " "),
            NoteChunk.content,
        )
    )
    stmt = sa.select(NoteChunk).where(_anchor_route_predicate(path_text, route))
    if user_id is not None:
        stmt = stmt.where(NoteChunk.user_id == user_id)
    stmt = stmt.limit(PROTECTED_ANCHOR_SQL_LIMIT)
    candidates = list((await session.execute(stmt)).scalars().all())
    scored: list[tuple[NoteChunk, float]] = []
    for chunk in candidates:
        score = _anchor_route_score(chunk, route)
        if score >= _anchor_route_min_score(route):
            scored.append((chunk, score))
    scored.sort(key=lambda item: (-item[1], item[0].id))
    return [chunk for chunk, _ in scored[:top_k]]


def classify_chunk_source(chunk: NoteChunk) -> ChunkSourceType:
    """Infer a coarse chunk source type from existing path/heading/content.

    The ordering is intentional: hard-negative/eval/question-bank hints should
    not be hidden by a folder that also contains "JobCopilot".
    """

    folder = _join(chunk.folder_path)
    heading = _join(chunk.heading_path)
    content_head = _norm(chunk.content[:1200])
    path_text = f"{folder} {heading}"

    if _has_any(
        f"{path_text} {content_head}",
        (
            "hard negative",
            "hard-negative",
            "hard negatives",
            "近邻干扰样本",
            "对抗样本",
            "hard negative 清单",
        ),
    ):
        return "hard_negative"

    if _has_any(path_text, ("评测", "eval", "fixture", "smoke", "baseline")):
        return "eval_case"

    if _has_any(
        path_text,
        (
            "题库",
            "追问题库",
            "追问样本",
            "模拟面试题",
            "综合模拟面试",
            "私有事实综合题库",
        ),
    ):
        return "interview_question_bank"

    if "jobcopilot" in path_text and "项目" in folder:
        return "canonical_project_fact"

    if "jobcopilot" in path_text:
        return "project_doc"

    return "generic_background"


def source_multiplier(
    chunk: NoteChunk,
    context: RetrievalGovernanceContext | None,
) -> float:
    """Return the score multiplier for a chunk under the query context."""

    if context is None:
        return 1.0
    multiplier = 1.0
    if context.enabled:
        source_type = classify_chunk_source(chunk)
        multiplier *= SOURCE_MULTIPLIERS_FOR_PROTECTED_INTENT[source_type]
        terms = (*context.core_entities, *context.must_keep_terms)
        if terms and multiplier >= 1.0 and _contains_any_term(chunk, terms):
            multiplier = min(multiplier + 0.03, 1.15)
    multiplier *= _contrast_multiplier(chunk, context)
    return multiplier


@dataclass(frozen=True)
class _PostRerankGovernanceScore:
    governance_score: float
    score_cap: float | None
    source_type: ChunkSourceType
    source_multiplier: float
    flags: tuple[str, ...]


def _post_rerank_governance_score(
    chunk: NoteChunk,
    context: RetrievalGovernanceContext,
    expanded_queries: list[str],
) -> _PostRerankGovernanceScore:
    source_type = classify_chunk_source(chunk)
    multiplier = source_multiplier(chunk, context)
    score = _clamp(0.70 + (multiplier - 1.0) * 1.20)
    score_cap: float | None = None
    flags: list[str] = []
    text = _support_norm(
        " ".join([*chunk.folder_path, *chunk.heading_path, chunk.content])
    )
    required_terms = _query_support_terms(context, expanded_queries)
    covered_terms = tuple(
        term
        for term in required_terms
        if _text_contains_support_term(text, term)
    )
    current_query_evidence_like = _looks_like_current_query_evidence(
        context,
        text,
        required_terms,
        covered_terms,
    )

    if source_type == "hard_negative":
        if current_query_evidence_like:
            flags.append("source_hard_negative_allowed_by_current_query")
        else:
            score -= 0.45
            score_cap = _min_cap(score_cap, POST_RERANK_HARD_NEGATIVE_CAP)
            flags.append("source_hard_negative_clamped")
    elif context.enabled and source_type == "eval_case":
        score -= 0.22
        score_cap = _min_cap(score_cap, POST_RERANK_EVAL_CASE_CAP)
        flags.append("source_eval_case_clamped")
    elif context.enabled and source_type == "interview_question_bank":
        score -= 0.18
        score_cap = _min_cap(score_cap, POST_RERANK_QUESTION_BANK_CAP)
        flags.append("source_question_bank_clamped")

    if required_terms:
        min_covered = min(ZERO_HIT_MIN_REQUIRED_TERMS, len(required_terms))
        if required_terms[0] not in covered_terms:
            score -= 0.26
            score_cap = _min_cap(score_cap, 0.56)
            flags.append("primary_anchor_missing")
        elif len(covered_terms) < min_covered:
            score -= 0.16
            score_cap = _min_cap(score_cap, 0.66)
            flags.append("too_few_anchors_covered")
        else:
            score += 0.04
            flags.append("anchors_covered")

    contrast_score, contrast_cap, contrast_flags = (
        _post_rerank_contrast_adjustment(chunk, context, text)
    )
    score += contrast_score
    score_cap = _min_cap(score_cap, contrast_cap)
    flags.extend(contrast_flags)

    route_score, route_cap, route_flags = _post_rerank_route_adjustment(
        context,
        expanded_queries,
        text,
    )
    score += route_score
    score_cap = _min_cap(score_cap, route_cap)
    flags.extend(route_flags)

    return _PostRerankGovernanceScore(
        governance_score=_clamp(score),
        score_cap=score_cap,
        source_type=source_type,
        source_multiplier=multiplier,
        flags=tuple(dict.fromkeys(flags)),
    )


def _select_dynamic_post_rerank_details(
    details: list[PostRerankGovernanceDetail],
    coarse_ranked: list[NoteChunk],
    *,
    top_k: int,
) -> list[PostRerankGovernanceDetail]:
    details_by_id = {int(item.chunk.id): item for item in details}
    coarse_floor_ids = [int(chunk.id) for chunk in coarse_ranked[:top_k]]
    coarse_floor_id_set = set(coarse_floor_ids)

    floor_candidates = [
        details_by_id[chunk_id]
        for chunk_id in coarse_floor_ids
        if chunk_id in details_by_id
        and _is_floor_candidate(details_by_id[chunk_id])
    ]
    challenger_candidates = [
        item
        for item in details
        if int(item.chunk.id) not in coarse_floor_id_set
        and _is_challenger_candidate(item)
    ]

    preferred_max = min(top_k, POST_RERANK_DYNAMIC_TARGET_K)
    selected: list[PostRerankGovernanceDetail] = []
    seen: set[int] = set()
    for item in sorted(
        [*floor_candidates, *challenger_candidates],
        key=_post_rerank_selection_key,
    ):
        chunk_id = int(item.chunk.id)
        if chunk_id in seen:
            continue
        strong_late_evidence = _is_late_strong_evidence_candidate(item)
        if (
            len(selected) >= preferred_max
            and not strong_late_evidence
        ):
            continue
        if (
            len(selected) >= min(POST_RERANK_DIVERSITY_SOFT_K, top_k)
            and _is_redundant_context_candidate(item, selected)
            and not strong_late_evidence
        ):
            continue
        if len(selected) >= top_k:
            break
        flag = (
            "challenger_selected"
            if chunk_id not in coarse_floor_id_set
            else "coarse_floor_selected"
        )
        selected.append(_with_selection_flag(item, flag))
        seen.add(chunk_id)

    if len(selected) >= min(POST_RERANK_DYNAMIC_MIN_K, top_k):
        return selected

    # Last-resort backfill keeps the pipeline usable for sparse queries without
    # letting clamped hard-negatives in if any cleaner coarse candidate exists.
    for chunk_id in coarse_floor_ids:
        if len(selected) >= min(POST_RERANK_DYNAMIC_MIN_K, top_k):
            break
        item = details_by_id.get(chunk_id)
        if item is None or int(item.chunk.id) in seen:
            continue
        if _is_blocked_by_governance(item):
            continue
        selected.append(_with_selection_flag(item, "min_context_backfill"))
        seen.add(int(item.chunk.id))

    return selected


def _is_floor_candidate(detail: PostRerankGovernanceDetail) -> bool:
    if _is_blocked_by_governance(detail):
        return False
    return detail.governance_score >= POST_RERANK_FLOOR_MIN_GOVERNANCE


def _is_challenger_candidate(detail: PostRerankGovernanceDetail) -> bool:
    if detail.rerank_rank is None:
        return False
    if _is_blocked_by_governance(detail):
        return False
    return (
        detail.governance_score >= POST_RERANK_CHALLENGER_MIN_GOVERNANCE
        or _is_extra_evidence_candidate(detail)
    )


def _is_extra_evidence_candidate(detail: PostRerankGovernanceDetail) -> bool:
    if _is_blocked_by_governance(detail):
        return False
    return (
        detail.governance_score >= POST_RERANK_EXTRA_MIN_GOVERNANCE
        or _has_any_flag(
            detail,
            (
                "contrast_direct_evidence",
                "state_recovery_route_supported",
                "provider_failure_route_supported",
            ),
        )
    )


def _is_late_strong_evidence_candidate(
    detail: PostRerankGovernanceDetail,
) -> bool:
    """Allow a small number of high-confidence chunks past the target size."""
    if _is_blocked_by_governance(detail):
        return False
    if _has_any_flag(
        detail,
        (
            "contrast_direct_evidence",
            "state_recovery_route_supported",
            "provider_failure_route_supported",
        ),
    ):
        return True
    if (
        detail.source_type in {"canonical_project_fact", "project_doc"}
        and detail.final_score >= POST_RERANK_LATE_STRONG_MIN_FINAL_SCORE
        and detail.governance_score >= POST_RERANK_LATE_STRONG_MIN_GOVERNANCE
    ):
        return True
    return (
        detail.final_score >= POST_RERANK_LATE_PROVIDER_MIN_FINAL_SCORE
        and
        detail.normalized_rerank_score
        >= POST_RERANK_LATE_PROVIDER_MIN_NORM_SCORE
        and detail.governance_score >= POST_RERANK_LATE_PROVIDER_MIN_GOVERNANCE
    )


def _is_redundant_context_candidate(
    detail: PostRerankGovernanceDetail,
    selected: list[PostRerankGovernanceDetail],
) -> bool:
    note_count = sum(
        1 for item in selected if item.chunk.note_id == detail.chunk.note_id
    )
    if note_count >= POST_RERANK_MAX_PER_NOTE_AFTER_SOFT_K:
        return True

    heading_key = _chunk_heading_key(detail.chunk)
    if heading_key is None:
        return False
    heading_count = sum(
        1
        for item in selected
        if _chunk_heading_key(item.chunk) == heading_key
    )
    return heading_count >= POST_RERANK_MAX_PER_HEADING_AFTER_SOFT_K


def _chunk_heading_key(chunk: NoteChunk) -> tuple[int, tuple[str, ...]] | None:
    if not chunk.heading_path:
        return None
    return (int(chunk.note_id), tuple(chunk.heading_path))


def _is_blocked_by_governance(detail: PostRerankGovernanceDetail) -> bool:
    blocking_prefixes = (
        "source_hard_negative_clamped",
        "source_eval_case_clamped",
        "source_question_bank_clamped",
        "primary_anchor_missing",
        "too_few_anchors_covered",
        "contrast_single_side_clamped",
        "contrast_weak_side_match",
        "project_anchor_missing",
        "state_recovery_transport_missing",
        "state_recovery_state_missing",
        "provider_anchor_missing",
    )
    return _has_any_flag(detail, blocking_prefixes)


def _has_any_flag(
    detail: PostRerankGovernanceDetail,
    flags: tuple[str, ...],
) -> bool:
    return any(flag in detail.flags for flag in flags)


def _post_rerank_selection_key(
    detail: PostRerankGovernanceDetail,
) -> tuple[float, float, int, int]:
    coarse_rank = (
        detail.coarse_rank if detail.coarse_rank is not None else 10**9
    )
    rerank_rank = (
        detail.rerank_rank if detail.rerank_rank is not None else 10**9
    )
    return (
        -detail.final_score,
        -detail.governance_score,
        coarse_rank,
        rerank_rank,
    )


def _with_selection_flag(
    detail: PostRerankGovernanceDetail,
    flag: str,
) -> PostRerankGovernanceDetail:
    return PostRerankGovernanceDetail(
        chunk=detail.chunk,
        coarse_rank=detail.coarse_rank,
        rerank_rank=detail.rerank_rank,
        coarse_score=detail.coarse_score,
        rerank_score=detail.rerank_score,
        normalized_rerank_score=detail.normalized_rerank_score,
        governance_score=detail.governance_score,
        final_score=detail.final_score,
        source_type=detail.source_type,
        source_multiplier=detail.source_multiplier,
        flags=tuple(dict.fromkeys((*detail.flags, flag))),
    )


def _post_rerank_contrast_adjustment(
    chunk: NoteChunk,
    context: RetrievalGovernanceContext,
    text: str,
) -> tuple[float, float | None, tuple[str, ...]]:
    if len(context.contrast_sides) < 2:
        return 0.0, None, ()

    left, right = context.contrast_sides[:2]
    left_hit = _side_covered(left, text)
    right_hit = _side_covered(right, text)
    if left_hit and right_hit:
        if _has_direct_contrast_signal(left, right, text):
            return 0.14, None, ("contrast_direct_evidence",)
        return 0.04, None, ("contrast_both_sides",)
    if left_hit or right_hit:
        return -0.26, 0.60, ("contrast_single_side_clamped",)
    if _contains_any_term(chunk, (*left, *right)):
        return -0.12, 0.70, ("contrast_weak_side_match",)
    return 0.0, None, ()


def _looks_like_current_query_evidence(
    context: RetrievalGovernanceContext,
    text: str,
    required_terms: tuple[str, ...],
    covered_terms: tuple[str, ...],
) -> bool:
    if len(context.contrast_sides) >= 2:
        left, right = context.contrast_sides[:2]
        return (
            _side_covered(left, text)
            and _side_covered(right, text)
            and _has_direct_contrast_signal(left, right, text)
        )
    if not required_terms:
        return False
    min_covered = min(ZERO_HIT_MIN_REQUIRED_TERMS, len(required_terms))
    return (
        required_terms[0] in covered_terms
        and len(covered_terms) >= min_covered
    )


def _post_rerank_route_adjustment(
    context: RetrievalGovernanceContext,
    expanded_queries: list[str],
    text: str,
) -> tuple[float, float | None, tuple[str, ...]]:
    route = _protected_anchor_route(context, expanded_queries)
    if route == "state_recovery":
        return _state_recovery_route_adjustment(text)
    if route == "provider_failure":
        return _provider_failure_route_adjustment(text)
    return 0.0, None, ()


def _state_recovery_route_adjustment(
    text: str,
) -> tuple[float, float | None, tuple[str, ...]]:
    score = 0.0
    cap: float | None = None
    flags: list[str] = []
    if "jobcopilot" not in text:
        score -= 0.28
        cap = _min_cap(cap, 0.52)
        flags.append("project_anchor_missing")
    if not _has_any(text, STATE_RECOVERY_TRANSPORT_TERMS):
        score -= 0.20
        cap = _min_cap(cap, 0.58)
        flags.append("state_recovery_transport_missing")
    if not _has_any(text, STATE_RECOVERY_STATE_TERMS):
        score -= 0.18
        cap = _min_cap(cap, 0.62)
        flags.append("state_recovery_state_missing")
    if not flags:
        score += 0.08
        flags.append("state_recovery_route_supported")
    return score, cap, tuple(flags)


def _provider_failure_route_adjustment(
    text: str,
) -> tuple[float, float | None, tuple[str, ...]]:
    score = 0.0
    cap: float | None = None
    flags: list[str] = []
    has_provider = _has_any(text, PROVIDER_FAILURE_ENTITY_TERMS)
    has_timeout = _has_any(text, PROVIDER_FAILURE_TIMEOUT_TERMS)
    has_rate_limit = _has_any(text, PROVIDER_FAILURE_RATE_LIMIT_TERMS)
    if not has_provider:
        score -= 0.22
        cap = _min_cap(cap, 0.58)
        flags.append("provider_anchor_missing")
    if not has_timeout:
        score -= 0.12
        flags.append("timeout_anchor_missing")
    if not has_rate_limit:
        score -= 0.12
        flags.append("rate_limit_anchor_missing")
    if not has_timeout and not has_rate_limit:
        cap = _min_cap(cap, 0.58)
    if not flags:
        score += 0.08
        flags.append("provider_failure_route_supported")
    return score, cap, tuple(flags)


def _rank_score(rank: int | None) -> float:
    if rank is None or rank <= 0:
        return 0.0
    return 1.0 / (rank**0.5)


def _normalize_rerank_scores(scores: dict[int, float]) -> dict[int, float]:
    if not scores:
        return {}
    values = list(scores.values())
    low = min(values)
    high = max(values)
    if high <= low:
        return {chunk_id: 0.0 for chunk_id in scores}
    return {
        chunk_id: _clamp((score - low) / (high - low))
        for chunk_id, score in scores.items()
    }


def _min_cap(current: float | None, candidate: float | None) -> float | None:
    if candidate is None:
        return current
    if current is None:
        return candidate
    return min(current, candidate)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return min(max(value, low), high)


def _contains_any_term(chunk: NoteChunk, terms: tuple[str, ...]) -> bool:
    text = _norm(
        " ".join([*chunk.folder_path, *chunk.heading_path, chunk.content])
    )
    return any(_norm(term) in text for term in terms if term.strip())


ProtectedAnchorRoute = Literal["state_recovery", "provider_failure"]


def _protected_anchor_route(
    context: RetrievalGovernanceContext,
    expanded_queries: list[str],
) -> ProtectedAnchorRoute | None:
    text = _query_context_text(context, expanded_queries)
    if _is_state_recovery_anchor_context(context, text):
        return "state_recovery"
    if _is_provider_failure_anchor_context(text):
        return "provider_failure"
    return None


def _query_context_text(
    context: RetrievalGovernanceContext,
    expanded_queries: list[str],
) -> str:
    return _norm(
        " ".join(
            [
                *context.core_entities,
                *context.must_keep_terms,
                *expanded_queries,
            ]
        )
    )


def _is_state_recovery_anchor_context(
    context: RetrievalGovernanceContext,
    text: str,
) -> bool:
    if not context.enabled:
        return False
    return (
        "jobcopilot" in text
        and _has_any(text, STATE_RECOVERY_TRANSPORT_TERMS)
        and _has_any(text, ("恢复", "重连", "reconnect", "recover"))
    )


def _is_provider_failure_anchor_context(text: str) -> bool:
    return (
        _has_any(text, PROVIDER_FAILURE_ENTITY_TERMS)
        and _has_any(text, PROVIDER_FAILURE_TIMEOUT_TERMS)
        and _has_any(text, PROVIDER_FAILURE_RATE_LIMIT_TERMS)
    )


def _anchor_route_predicate(path_text, route: ProtectedAnchorRoute):
    if route == "state_recovery":
        return sa.and_(
            path_text.like("%jobcopilot%", escape="\\"),
            _or_like(path_text, STATE_RECOVERY_TRANSPORT_TERMS),
            _or_like(path_text, STATE_RECOVERY_STATE_TERMS),
        )
    return sa.and_(
        _or_like(path_text, PROVIDER_FAILURE_ENTITY_TERMS),
        _or_like(path_text, PROVIDER_FAILURE_EVIDENCE_TERMS),
    )


def _anchor_route_score(chunk: NoteChunk, route: ProtectedAnchorRoute) -> float:
    if route == "state_recovery":
        return _state_recovery_anchor_score(chunk)
    return _provider_failure_anchor_score(chunk)


def _anchor_route_min_score(route: ProtectedAnchorRoute) -> float:
    if route == "state_recovery":
        return STATE_RECOVERY_ANCHOR_MIN_SCORE
    return PROVIDER_FAILURE_ANCHOR_MIN_SCORE


def _state_recovery_anchor_score(chunk: NoteChunk) -> float:
    source_type = classify_chunk_source(chunk)
    if source_type in {"hard_negative", "eval_case", "interview_question_bank"}:
        return 0.0
    elif source_type == "canonical_project_fact":
        source_prior = 3.0
    elif source_type == "project_doc":
        source_prior = 1.0
    else:
        source_prior = 0.0

    text = _norm(
        " ".join([*chunk.folder_path, *chunk.heading_path, chunk.content])
    )
    if "jobcopilot" not in text:
        return 0.0
    if not _has_any(text, STATE_RECOVERY_TRANSPORT_TERMS):
        return 0.0
    if not _has_any(text, STATE_RECOVERY_STATE_TERMS):
        return 0.0

    phrase_score = sum(
        weight
        for phrase, weight in STATE_RECOVERY_ANCHOR_PHRASES
        if phrase in text
    )
    if source_type != "canonical_project_fact" and phrase_score < 6.0:
        return 0.0
    return source_prior + phrase_score


def _provider_failure_anchor_score(chunk: NoteChunk) -> float:
    source_type = classify_chunk_source(chunk)
    if source_type in {"hard_negative", "eval_case", "interview_question_bank"}:
        return 0.0
    elif source_type == "canonical_project_fact":
        source_prior = 4.0
    elif source_type == "project_doc":
        source_prior = 1.5
    else:
        source_prior = 1.0

    text = _norm(
        " ".join([*chunk.folder_path, *chunk.heading_path, chunk.content])
    )
    if not _has_any(text, PROVIDER_FAILURE_ENTITY_TERMS):
        return 0.0
    if not _has_any(text, PROVIDER_FAILURE_EVIDENCE_TERMS):
        return 0.0

    phrase_score = sum(
        weight
        for phrase, weight in PROVIDER_FAILURE_ANCHOR_PHRASES
        if phrase in text
    )
    has_timeout = _has_any(text, PROVIDER_FAILURE_TIMEOUT_TERMS)
    has_rate_limit = _has_any(text, PROVIDER_FAILURE_RATE_LIMIT_TERMS)
    if has_timeout and has_rate_limit:
        phrase_score += 4.0
    elif source_type != "canonical_project_fact":
        phrase_score -= 2.0

    if source_type != "canonical_project_fact" and phrase_score < 9.0:
        return 0.0
    return source_prior + phrase_score


def _contrast_multiplier(
    chunk: NoteChunk,
    context: RetrievalGovernanceContext,
) -> float:
    if len(context.contrast_sides) < 2:
        return 1.0

    left, right = context.contrast_sides[:2]
    all_text = _support_norm(
        " ".join([*chunk.folder_path, *chunk.heading_path, chunk.content])
    )
    leaf_heading = _support_norm(
        chunk.heading_path[-1] if chunk.heading_path else ""
    )
    root_heading = _support_norm(
        chunk.heading_path[0] if chunk.heading_path else ""
    )

    left_hit = _side_covered(left, all_text)
    right_hit = _side_covered(right, all_text)
    if not left_hit and not right_hit:
        return 1.0

    leaf_heading_has_both = _side_covered(
        left, leaf_heading
    ) and _side_covered(right, leaf_heading)
    leaf_has_left = _side_covered(left, leaf_heading)
    leaf_has_right = _side_covered(right, leaf_heading)
    root_has_left = _side_covered(left, root_heading)
    root_has_right = _side_covered(right, root_heading)
    has_direct_contrast_signal = _has_direct_contrast_signal(
        left, right, all_text
    )

    if leaf_heading_has_both or has_direct_contrast_signal:
        return CONTRAST_EVIDENCE_MULTIPLIER
    if root_has_right and leaf_has_left and not leaf_has_right:
        return CONTRAST_SECONDARY_TOPIC_MULTIPLIER
    if root_has_left and not root_has_right:
        return CONTRAST_PRIMARY_TOPIC_MULTIPLIER
    if root_has_right and not root_has_left:
        return CONTRAST_SECONDARY_TOPIC_MULTIPLIER
    if left_hit != right_hit:
        return CONTRAST_SINGLE_SIDE_MULTIPLIER
    return 1.0


def _contrast_sides_from_rewrite(
    output: QueryRewriteOutput,
) -> tuple[tuple[str, ...], ...]:
    original_query = (
        output.expanded_queries[0] if output.expanded_queries else ""
    )
    if not _is_contrast_query(original_query):
        return ()

    raw_sides = _split_contrast_sides(original_query)
    sides = tuple(
        side for side in (_side_terms(raw) for raw in raw_sides) if side
    )
    if len(sides) >= 2:
        return sides[:2]

    fallback_terms = _query_support_terms(
        RetrievalGovernanceContext(
            intent=output.intent,
            core_entities=tuple(output.core_entities),
            must_keep_terms=tuple(output.must_keep_terms),
        ),
        output.expanded_queries,
    )
    if len(fallback_terms) >= 2:
        return ((fallback_terms[0],), (fallback_terms[1],))
    return ()


def _is_contrast_query(query: str) -> bool:
    text = f" {_support_norm(query)} "
    if " vs " in text or " versus " in text:
        return True
    has_marker = any(marker.strip() in text for marker in _CONTRAST_MARKERS)
    has_separator = any(separator in text for separator in _CONTRAST_SEPARATORS)
    return has_marker and has_separator


def _split_contrast_sides(query: str) -> tuple[str, ...]:
    text = f" {_support_norm(query)} "
    for separator in _CONTRAST_SEPARATORS:
        if separator not in text:
            continue
        left, right = text.split(separator, 1)
        return (_strip_contrast_side(left), _strip_contrast_side(right))
    return (query,)


def _strip_contrast_side(text: str) -> str:
    out = f" {text} "
    for marker in _CONTRAST_MARKERS:
        out = out.replace(marker, " ")
    for token in ("有什么", "有啥", "是什么", "吗", "呢"):
        out = out.replace(token, " ")
    return _support_norm(out)


def _side_terms(text: str) -> tuple[str, ...]:
    terms: list[str] = []
    seen: set[str] = set()
    for term in _technical_terms(text):
        if term in seen:
            continue
        terms.append(term)
        seen.add(term)
        if len(terms) >= 4:
            return tuple(terms)
    if terms:
        return tuple(terms)
    term = _support_norm(text)
    if _is_support_term(term):
        return (term,)
    return ()


def _query_support_terms(
    context: RetrievalGovernanceContext,
    expanded_queries: list[str],
) -> tuple[str, ...]:
    original_query = expanded_queries[0] if expanded_queries else ""
    raw_terms = [
        *context.core_entities,
        *context.must_keep_terms,
        *_technical_terms(original_query),
    ]
    terms: list[str] = []
    seen: set[str] = set()
    for raw in raw_terms:
        for term in _support_subterms(raw):
            if term in seen:
                continue
            terms.append(term)
            seen.add(term)
            if len(terms) >= 8:
                return tuple(terms)
    return tuple(terms)


def _support_subterms(raw: str) -> tuple[str, ...]:
    tokens = _technical_terms(raw)
    if tokens:
        return tuple(tokens)
    term = _support_norm(raw)
    if _is_support_term(term):
        return (term,)
    return ()


def _technical_terms(text: str) -> list[str]:
    terms: list[str] = []
    for token in _TECH_TERM_RE.findall(text):
        normalized = _support_norm(token)
        if _is_support_term(normalized, raw=token):
            terms.append(normalized)
    return terms


def _is_support_term(term: str, *, raw: str | None = None) -> bool:
    if term in _SHORT_SUPPORT_TERMS:
        return True
    if not term or term in _SUPPORT_TERM_STOPWORDS:
        return False
    if term.isdigit():
        return len(term) >= 2
    if any(ch.isdigit() for ch in term):
        return True
    if raw is not None and raw.isupper() and len(raw) >= 2:
        return True
    if len(term) >= 3:
        return True
    return False


def _support_term_covered(term: str, chunks: list[NoteChunk]) -> bool:
    for chunk in chunks:
        text = _support_norm(
            " ".join([*chunk.folder_path, *chunk.heading_path, chunk.content])
        )
        if _text_contains_support_term(text, term):
            return True
    return False


def _side_covered(side: tuple[str, ...], text: str) -> bool:
    return any(_text_contains_support_term(text, term) for term in side)


def _text_contains_support_term(text: str, term: str) -> bool:
    for alias in _SUPPORT_TERM_ALIASES.get(term, (term,)):
        normalized = _support_norm(alias)
        if not normalized:
            continue
        if _contains_cjk(normalized):
            if normalized in text:
                return True
        elif f" {normalized} " in f" {text} ":
            return True
    return False


def _has_direct_contrast_signal(
    left: tuple[str, ...],
    right: tuple[str, ...],
    text: str,
) -> bool:
    left_positions = _side_positions(left, text)
    right_positions = _side_positions(right, text)
    if not left_positions or not right_positions:
        return False
    padded = f" {text} "
    for left_pos in left_positions:
        for right_pos in right_positions:
            if abs(left_pos - right_pos) > 90:
                continue
            start = max(min(left_pos, right_pos) - 20, 0)
            end = min(max(left_pos, right_pos) + 40, len(padded))
            window = padded[start:end]
            if any(signal in window for signal in _CONTRAST_SIGNAL_TERMS):
                return True
    return False


def _side_positions(side: tuple[str, ...], text: str) -> list[int]:
    positions: list[int] = []
    padded = f" {text} "
    for term in side:
        for alias in _SUPPORT_TERM_ALIASES.get(term, (term,)):
            normalized = _support_norm(alias)
            if not normalized:
                continue
            needle = normalized if _contains_cjk(normalized) else f" {normalized} "
            start = 0
            while True:
                idx = padded.find(needle, start)
                if idx < 0:
                    break
                positions.append(idx)
                start = idx + max(len(needle), 1)
    return positions


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _support_norm(text: str) -> str:
    normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", text.casefold())
    return " ".join(normalized.split())


def _or_like(path_text, terms: tuple[str, ...]):
    return sa.or_(
        *[
            path_text.like(_like_pattern(_norm(term)), escape="\\")
            for term in terms
        ]
    )


def _like_pattern(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _join(parts: list[str]) -> str:
    return _norm(" ".join(parts))


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    normalized = _norm(text)
    return any(needle in normalized for needle in needles)
