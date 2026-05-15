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
    stmt = (
        sa.select(NoteChunk)
        .where(_anchor_route_predicate(path_text, route))
        .limit(PROTECTED_ANCHOR_SQL_LIMIT)
    )
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
