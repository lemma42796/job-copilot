"""Match service — pending row creation (S13) + run_analyze + CRUD (S14).

Two-phase pipeline (mirrors jd_service / profile_service):

- **create_pending_match** (Phase 1): validate inputs + INSERT
  `status='pending'` row。永久约束 #4: SSE `started` event 在 phase 1
  之后 emit,带这里返回的 match_id 做 resource_id。
- **run_analyze** (Phase 2): retrieve top-K chunks (embedder + DB SELECT)
  + LLM analyze + 业务一致性校验(剔除非法 evidence_chunk_ids)+ UPDATE
  匹配行 status='scored'。LLM upstream / schema-invalid 走 `_mark_failed`
  旁路 commit(永久约束 ADR-0004 side-channel pattern),raise 出去由 SSE
  router emit `error → done(ok=False)`。

list / get / soft_delete 走 caller-managed session(同 jd_service 模式)。
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobcopilot_api.agents.match_analyst import analyze_match
from jobcopilot_api.errors import JobCopilotError, NotFoundError
from jobcopilot_api.infra.prompts import LoadedPrompt
from jobcopilot_api.llm.client import LLMClient, LLMResult
from jobcopilot_api.llm.embedders import Embedder
from jobcopilot_api.llm.errors import (
    LLMSchemaInvalidError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from jobcopilot_api.models import Jd, Match, Profile, ProfileChunk, User
from jobcopilot_api.schemas.matches import MatchedSkill, MatchResult
from jobcopilot_api.services.retrieval_service import (
    DEFAULT_TOP_K,
    RetrieveResult,
    build_match_query,
    retrieve_for_match,
)

log = structlog.get_logger(__name__)


class MatchPreconditionError(JobCopilotError):
    """422 — `MATCH_PRECONDITION`. 入参合法但状态不满足匹配前提
    (JD 未 parsed / profile 无 chunk 等)。和 NotFound 区分:not found
    映射到 ADR-0005 D9(404 不区分软删/越权/不存在),precondition
    给用户的提示是"对你可见但还不能用"。"""

    status_code = 422
    code = "MATCH_PRECONDITION"
    title = "无法发起匹配"


class MatchAnalyzeFailedError(JobCopilotError):
    """422 — `MATCH_ANALYZE_FAILED`. LLM schema invalid / chunks 全被
    剔除等 analyze 阶段的"业务级失败"。LLM upstream 5xx / timeout 仍
    映射到 502 LLM_UPSTREAM_ERROR(jd_service 同款)。"""

    status_code = 422
    code = "MATCH_ANALYZE_FAILED"
    title = "匹配分析失败"


# ---------------------------------------------------------------------------
# Phase 1: create pending row
# ---------------------------------------------------------------------------


async def create_pending_match(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    user_id: int,
    jd_id: int,
    profile_id: int,
) -> int:
    """Phase 1: 校验入参 + INSERT 一条 status='pending' 行,返回 match_id。

    `run_analyze(match_id, ...)` 接力 phase 2。"""
    async with sessionmaker() as session, session.begin():
        await _validate_user(session, user_id=user_id)
        await _validate_jd_parsed(session, user_id=user_id, jd_id=jd_id)
        await _validate_profile_owned(session, user_id=user_id, profile_id=profile_id)
        await _validate_profile_has_chunks(session, profile_id=profile_id)

        match = Match(
            user_id=user_id,
            jd_id=jd_id,
            profile_id=profile_id,
            status="pending",
        )
        session.add(match)
        await session.flush()
        return match.id


# ---------------------------------------------------------------------------
# Phase 2: run analyze
# ---------------------------------------------------------------------------


async def run_analyze(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    match_id: int,
    user_id: int,
    embedder: Embedder,
    llm: LLMClient,
    prompt: LoadedPrompt,
    trace_id: str | None = None,
    k: int = DEFAULT_TOP_K,
) -> tuple[Match, LLMResult, RetrieveResult]:
    """Phase 2: retrieve + analyze + UPDATE match 行。

    分层(永久约束 #7):
    1. 短 tx: 读 match 行 + 关联 jd(校验所有权,得到查询输入)
    2. 纯函数: build_match_query(jd)
    3. 慢 IO(无事务): retrieve_for_match (embed + select own session)
    4. 慢 IO(无事务): analyze_match (LLM, 无 DB)
    5. 业务校验: 剔除 LLM 编造的 evidence_chunk_ids (AGENT_DESIGN §6.6 简化版,
       MVP 不重试)
    6. 单事务: UPDATE matches 行至 status='scored'

    Failure path:
    - LLM upstream / timeout → mark_failed + raise LLMUpstreamError(502)
    - LLM schema invalid → mark_failed + raise MatchAnalyzeFailedError(422)
    - 其他业务校验失败(如 chunks 召回空)→ mark_failed + raise MatchAnalyzeFailedError(422)
    """
    async with sessionmaker() as session:
        match, jd = await _load_match_for_analyze(session, match_id=match_id, user_id=user_id)

    query_text = build_match_query(jd)

    try:
        retrieve = await retrieve_for_match(
            sessionmaker,
            profile_id=match.profile_id,
            query_text=query_text,
            embedder=embedder,
            k=k,
        )
    except (LLMUpstreamError, LLMTimeoutError) as exc:
        await _mark_failed(sessionmaker, match_id=match_id)
        raise LLMUpstreamError(str(exc) or "embedding 上游异常", status_code=502) from exc

    if not retrieve.chunks:
        await _mark_failed(sessionmaker, match_id=match_id)
        raise MatchAnalyzeFailedError(
            "Profile chunks 召回为空,无法分析(profile 是否已 chunk?embedding 是否完整?)"
        )

    try:
        result = await analyze_match(
            jd=jd,
            chunks=retrieve.chunks,
            prompt=prompt,
            llm=llm,
            user_id=user_id,
            trace_id=trace_id,
            related_id=match_id,
        )
    except (LLMUpstreamError, LLMTimeoutError) as exc:
        await _mark_failed(sessionmaker, match_id=match_id)
        raise LLMUpstreamError(str(exc) or "LLM 上游异常", status_code=502) from exc
    except LLMSchemaInvalidError as exc:
        await _mark_failed(sessionmaker, match_id=match_id)
        snippet = str(exc)[:500] if str(exc) else ""
        raise MatchAnalyzeFailedError(
            f"LLM 返回的 JSON 不符合 schema:{snippet}"
            if snippet
            else "LLM 返回的 JSON 不符合 schema"
        ) from exc

    parsed = result.parsed
    if not isinstance(parsed, MatchResult):
        await _mark_failed(sessionmaker, match_id=match_id)
        raise MatchAnalyzeFailedError("LLM 未返回有效的 MatchResult")

    valid_chunk_ids = {c.id for c in retrieve.chunks}
    sanitized = _sanitize_evidence(parsed, valid_chunk_ids)

    async with sessionmaker() as session, session.begin():
        match_row = await _get_match_locked(session, match_id=match_id, user_id=user_id)
        _apply_match_result(
            match_row,
            sanitized,
            llm_result=result,
        )
        match_row.status = "scored"

    log.info(
        "match_service.run_analyze",
        match_id=match_id,
        user_id=user_id,
        score=sanitized.score,
        matched_skills=len(sanitized.matched_skills),
        missing_skills=len(sanitized.missing_skills),
        chunks_retrieved=len(retrieve.chunks),
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost_cny=str(result.cost_cny),
        latency_ms=result.latency_ms,
        trace_id=trace_id,
    )
    return match_row, result, retrieve


# ---------------------------------------------------------------------------
# list / get / soft_delete (caller-managed session)
# ---------------------------------------------------------------------------


async def list_matches(
    session: AsyncSession,
    *,
    user_id: int,
    jd_id: int | None = None,
    profile_id: int | None = None,
    status: str | None = None,
    created_after: datetime | None = None,
    cursor: int | None = None,
    limit: int = 20,
) -> tuple[list[Match], bool]:
    """Return `(rows, has_more)`. cursor=id desc(同 jd list 模板)。"""
    stmt = (
        sa.select(Match)
        .where(Match.user_id == user_id, Match.deleted_at.is_(None))
        .order_by(Match.created_at.desc(), Match.id.desc())
        .limit(limit + 1)
    )
    if jd_id is not None:
        stmt = stmt.where(Match.jd_id == jd_id)
    if profile_id is not None:
        stmt = stmt.where(Match.profile_id == profile_id)
    if status is not None:
        stmt = stmt.where(Match.status == status)
    if created_after is not None:
        stmt = stmt.where(Match.created_at > created_after)
    if cursor is not None:
        stmt = stmt.where(Match.id < cursor)

    rows = list((await session.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    return rows[:limit], has_more


async def get_match(session: AsyncSession, *, user_id: int, match_id: int) -> Match:
    """Return active match row or raise `NotFoundError` (ADR-0005 D9)。"""
    cur = await session.execute(
        sa.select(Match).where(
            Match.id == match_id,
            Match.user_id == user_id,
            Match.deleted_at.is_(None),
        )
    )
    match = cur.scalar_one_or_none()
    if match is None:
        raise NotFoundError(f"match {match_id} not found")
    return match


async def soft_delete_match(session: AsyncSession, *, user_id: int, match_id: int) -> None:
    cur = await session.execute(
        sa.update(Match)
        .where(
            Match.id == match_id,
            Match.user_id == user_id,
            Match.deleted_at.is_(None),
        )
        .values(deleted_at=sa.func.now())
        .returning(Match.id)
    )
    if cur.scalar_one_or_none() is None:
        raise NotFoundError(f"match {match_id} not found")


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _validate_user(session: AsyncSession, *, user_id: int) -> None:
    exists = await session.scalar(sa.select(sa.literal(1)).where(User.id == user_id))
    if exists is None:
        raise NotFoundError(f"用户 {user_id} 不存在")


async def _validate_jd_parsed(session: AsyncSession, *, user_id: int, jd_id: int) -> None:
    """JD 必须存在 + 属于 user + 已 parsed。"""
    status = await session.scalar(
        sa.select(Jd.status).where(
            Jd.id == jd_id,
            Jd.user_id == user_id,
            Jd.deleted_at.is_(None),
        )
    )
    if status is None:
        raise NotFoundError(f"jd {jd_id} not found")
    if status != "parsed":
        raise MatchPreconditionError(f"JD 当前状态为 {status},需先解析为 parsed 状态才能发起匹配")


async def _validate_profile_owned(session: AsyncSession, *, user_id: int, profile_id: int) -> None:
    exists = await session.scalar(
        sa.select(sa.literal(1)).where(
            Profile.id == profile_id,
            Profile.user_id == user_id,
            Profile.deleted_at.is_(None),
        )
    )
    if exists is None:
        raise NotFoundError(f"profile {profile_id} not found")


async def _validate_profile_has_chunks(session: AsyncSession, *, profile_id: int) -> None:
    """RAG 前提:profile 至少有一条带 embedding 的 chunk。

    chunker_version 任意(M2 后续 chunker 升级时不在这里卡;只兜底"完全
    没切"或"chunker rebuild 失败导致 embedding 全 NULL"两种空召回场景)。
    """
    count = await session.scalar(
        sa.select(sa.func.count())
        .select_from(ProfileChunk)
        .where(
            ProfileChunk.profile_id == profile_id,
            ProfileChunk.embedding.is_not(None),
        )
    )
    if not count:
        raise MatchPreconditionError(
            "Profile 暂无 chunk 或 embedding 未生成,无法检索;请先重建 profile chunks"
        )


async def _load_match_for_analyze(
    session: AsyncSession, *, match_id: int, user_id: int
) -> tuple[Match, Jd]:
    """Read the pending match row + its JD,校验 user 拥有 + match 仍 pending。

    返回 (Match, Jd) 都已 detached(后续 IO 在 session 外)。"""
    match = await get_match(session, user_id=user_id, match_id=match_id)
    if match.status != "pending":
        raise MatchPreconditionError(
            f"Match {match_id} 当前状态 {match.status},已完成或失败,不能重复 analyze"
        )

    jd = await session.scalar(
        sa.select(Jd).where(
            Jd.id == match.jd_id,
            Jd.user_id == user_id,
            Jd.deleted_at.is_(None),
        )
    )
    if jd is None:
        raise NotFoundError(f"jd {match.jd_id} not found")
    return match, jd


async def _get_match_locked(session: AsyncSession, *, match_id: int, user_id: int) -> Match:
    """Re-read match row inside the write transaction so we update an
    attached, fresh row (avoid `Object instance is not bound to a Session`)。"""
    match = await session.scalar(
        sa.select(Match).where(
            Match.id == match_id,
            Match.user_id == user_id,
            Match.deleted_at.is_(None),
        )
    )
    if match is None:
        raise NotFoundError(f"match {match_id} not found")
    return match


def _sanitize_evidence(parsed: MatchResult, valid_chunk_ids: set[int]) -> MatchResult:
    """剔除 matched_skills 里 LLM 编造的 evidence_chunk_ids(AGENT_DESIGN §6.6)。

    MVP 不重试(只剔除一次);若全部 evidence 为非法,留 matched_skill 但
    `evidence_chunk_ids` 变空,前端 hover 时无可点回的来源。"""
    cleaned: list[MatchedSkill] = []
    for ms in parsed.matched_skills:
        ok = [cid for cid in ms.evidence_chunk_ids if cid in valid_chunk_ids]
        cleaned.append(ms.model_copy(update={"evidence_chunk_ids": ok}))
    return parsed.model_copy(update={"matched_skills": cleaned})


def _apply_match_result(
    match: Match,
    parsed: MatchResult,
    *,
    llm_result: LLMResult,
) -> None:
    """Map MatchResult → DB columns + LLM 元数据。Status 由 caller 决定。"""
    match.score = parsed.score
    match.matched_skills = [s.model_dump(mode="json") for s in parsed.matched_skills]
    match.missing_skills = [s.model_dump(mode="json") for s in parsed.missing_skills]
    match.advantage_summary = parsed.advantage_summary
    match.gap_summary = parsed.gap_summary
    match.suggestions = list(parsed.suggestions)
    match.model = llm_result.model
    match.tokens_in = llm_result.tokens_in
    match.tokens_out = llm_result.tokens_out
    match.cost_cny = llm_result.cost_cny
    match.latency_ms = llm_result.latency_ms


async def _mark_failed(sessionmaker: async_sessionmaker[AsyncSession], *, match_id: int) -> None:
    """Side-channel commit: status='failed' regardless of in-flight tx
    state(套 jd_service._mark_failed / DBCallLogger 模板)。"""
    async with sessionmaker() as session, session.begin():
        await session.execute(sa.update(Match).where(Match.id == match_id).values(status="failed"))


# Re-exports kept for other modules / tests that imported from this file.
__all__ = [
    "MatchAnalyzeFailedError",
    "MatchPreconditionError",
    "create_pending_match",
    "get_match",
    "list_matches",
    "run_analyze",
    "soft_delete_match",
]
