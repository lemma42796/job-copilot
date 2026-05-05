"""Resume service — pending row creation + run_generate + CRUD (S16).

Two-phase pipeline 套 match_service 模板:

- **create_pending_resume** (Phase 1): validate inputs + INSERT
  `status='generating'` row。永久约束 #4: SSE `started` event 在 phase 1
  之后 emit,带这里返回的 resume_id 做 resource_id。
- **run_generate** (Phase 2): retrieve top-K=20 chunks + LLM draft + LLM
  review + 写 resumes 行 + INSERT resume_versions v1。

D8 = A:函数串调,不上 LangGraph。MVP 不做 plan / 不做 revise:reviewer
不通过(任意 high severity)→ status='review_failed' + 仍写 markdown 让前
端展示警告条;LLM upstream / schema invalid 走 _mark_failed 旁路 commit
(永久约束 ADR-0004 side-channel pattern)。

list / get / soft_delete 走 caller-managed session(同 match_service 模式)。
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobcopilot_api.agents.resume_drafter import draft_resume
from jobcopilot_api.agents.resume_reviewer import review_resume
from jobcopilot_api.errors import JobCopilotError, NotFoundError
from jobcopilot_api.infra.prompts import LoadedPrompt
from jobcopilot_api.llm.client import LLMClient, LLMResult
from jobcopilot_api.llm.embedders import Embedder
from jobcopilot_api.llm.errors import (
    LLMSchemaInvalidError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from jobcopilot_api.models import Jd, Match, Profile, ProfileChunk, Resume, ResumeVersion, User
from jobcopilot_api.schemas.resumes import ResumeReview
from jobcopilot_api.services.retrieval_service import (
    RetrieveResult,
    build_match_query,
    retrieve_for_match,
)

log = structlog.get_logger(__name__)

DEFAULT_RESUME_K = 20
RESUME_TITLE_MAX = 200


class ResumePreconditionError(JobCopilotError):
    """422 — `RESUME_PRECONDITION`. 入参合法但状态不满足生成前提
    (JD 未 parsed / profile 无 chunk / match 不存在或不属于 user 等)。"""

    status_code = 422
    code = "RESUME_PRECONDITION"
    title = "无法生成简历"


class ResumeGenerationFailedError(JobCopilotError):
    """422 — `RESUME_GENERATION_FAILED`. retrieve 召回空 / reviewer LLM
    schema invalid 等业务级失败。LLM upstream 5xx / timeout 仍映射到 502
    LLM_UPSTREAM_ERROR(套 match_service / jd_service 同款)。"""

    status_code = 422
    code = "RESUME_GENERATION_FAILED"
    title = "简历生成失败"


# ---------------------------------------------------------------------------
# Phase 1: create pending row
# ---------------------------------------------------------------------------


async def create_pending_resume(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    user_id: int,
    jd_id: int,
    profile_id: int,
    match_id: int | None = None,
) -> int:
    """Phase 1: 校验入参 + INSERT 一条 status='generating' 行,返回 resume_id。

    `run_generate(resume_id, ...)` 接力 phase 2。"""
    async with sessionmaker() as session, session.begin():
        await _validate_user(session, user_id=user_id)
        await _validate_jd_parsed(session, user_id=user_id, jd_id=jd_id)
        await _validate_profile_owned(session, user_id=user_id, profile_id=profile_id)
        await _validate_profile_has_chunks(session, profile_id=profile_id)
        if match_id is not None:
            await _validate_match_for_hint(
                session, user_id=user_id, match_id=match_id, jd_id=jd_id, profile_id=profile_id
            )

        resume = Resume(
            user_id=user_id,
            jd_id=jd_id,
            profile_id=profile_id,
            match_id=match_id,
            status="generating",
        )
        session.add(resume)
        await session.flush()
        return resume.id


# ---------------------------------------------------------------------------
# Phase 2: run generate
# ---------------------------------------------------------------------------


async def run_generate(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    resume_id: int,
    user_id: int,
    embedder: Embedder,
    llm: LLMClient,
    drafter_prompt: LoadedPrompt,
    reviewer_prompt: LoadedPrompt,
    trace_id: str | None = None,
    k: int = DEFAULT_RESUME_K,
) -> tuple[Resume, LLMResult, LLMResult, RetrieveResult]:
    """Phase 2: retrieve → draft → review → 写 resume + resume_versions v1。

    分层(永久约束 #7):
    1. 短 tx: 读 resume 行 + 关联 jd + (optional) match → 拿查询 + hint
    2. 纯函数: build_match_query(jd) (复用 retrieval_service)
    3. 慢 IO(无事务): retrieve_for_match (embed + select own session, K=20)
    4. 慢 IO(无事务): draft_resume (LLM, 无 DB)
    5. 慢 IO(无事务): review_resume (LLM, 无 DB)
    6. 单事务: UPDATE resume + INSERT resume_versions v1

    Failure path:
    - retrieve 召回空 → mark_failed + raise ResumeGenerationFailedError(422)
    - drafter / reviewer LLM upstream / timeout → mark_failed + raise
      LLMUpstreamError(502)
    - reviewer LLM schema invalid → mark_failed + raise
      ResumeGenerationFailedError(422)
    - reviewer.passed=False(任意 high severity)→ **不 mark_failed**,
      status='review_failed',markdown / findings 仍存供前端展示
    """
    async with sessionmaker() as session:
        resume, jd, hint = await _load_resume_for_generate(
            session, resume_id=resume_id, user_id=user_id
        )

    query_text = build_match_query(jd)

    try:
        retrieve = await retrieve_for_match(
            sessionmaker,
            profile_id=resume.profile_id,
            query_text=query_text,
            embedder=embedder,
            k=k,
        )
    except (LLMUpstreamError, LLMTimeoutError) as exc:
        await _mark_failed(sessionmaker, resume_id=resume_id)
        raise LLMUpstreamError(str(exc) or "embedding 上游异常", status_code=502) from exc

    if not retrieve.chunks:
        await _mark_failed(sessionmaker, resume_id=resume_id)
        raise ResumeGenerationFailedError(
            "Profile chunks 召回为空,无法生成简历(profile 是否已 chunk?embedding 是否完整?)"
        )

    try:
        drafter_result = await draft_resume(
            jd=jd,
            chunks=retrieve.chunks,
            hint=hint,
            prompt=drafter_prompt,
            llm=llm,
            user_id=user_id,
            trace_id=trace_id,
            related_id=resume_id,
        )
    except (LLMUpstreamError, LLMTimeoutError) as exc:
        await _mark_failed(sessionmaker, resume_id=resume_id)
        raise LLMUpstreamError(str(exc) or "drafter 上游异常", status_code=502) from exc

    draft_markdown = (drafter_result.content or "").strip()
    if not draft_markdown:
        await _mark_failed(sessionmaker, resume_id=resume_id)
        raise ResumeGenerationFailedError("Drafter 返回空 markdown")

    try:
        reviewer_result = await review_resume(
            draft_markdown=draft_markdown,
            chunks=retrieve.chunks,
            prompt=reviewer_prompt,
            llm=llm,
            user_id=user_id,
            trace_id=trace_id,
            related_id=resume_id,
        )
    except (LLMUpstreamError, LLMTimeoutError) as exc:
        await _mark_failed(sessionmaker, resume_id=resume_id)
        raise LLMUpstreamError(str(exc) or "reviewer 上游异常", status_code=502) from exc
    except LLMSchemaInvalidError as exc:
        await _mark_failed(sessionmaker, resume_id=resume_id)
        snippet = str(exc)[:500] if str(exc) else ""
        raise ResumeGenerationFailedError(
            f"Reviewer 返回的 JSON 不符合 schema:{snippet}"
            if snippet
            else "Reviewer 返回的 JSON 不符合 schema"
        ) from exc

    review = reviewer_result.parsed
    if not isinstance(review, ResumeReview):
        await _mark_failed(sessionmaker, resume_id=resume_id)
        raise ResumeGenerationFailedError("Reviewer 未返回有效的 ResumeReview")

    final_status = "ready" if review.passed else "review_failed"
    title = _make_title(jd)

    async with sessionmaker() as session, session.begin():
        resume_row = await _get_resume_locked(session, resume_id=resume_id, user_id=user_id)
        _apply_generate_result(
            resume_row,
            draft_markdown=draft_markdown,
            title=title,
            review=review,
            drafter_result=drafter_result,
            reviewer_result=reviewer_result,
        )
        resume_row.status = final_status

        version = ResumeVersion(
            resume_id=resume_id,
            version_number=1,
            markdown=draft_markdown,
            edit_type="generated",
        )
        session.add(version)

    log.info(
        "resume_service.run_generate",
        resume_id=resume_id,
        user_id=user_id,
        status=final_status,
        review_passed=review.passed,
        review_findings=len(review.findings),
        chunks_retrieved=len(retrieve.chunks),
        drafter_tokens_in=drafter_result.tokens_in,
        drafter_tokens_out=drafter_result.tokens_out,
        drafter_cost=str(drafter_result.cost_cny),
        reviewer_tokens_in=reviewer_result.tokens_in,
        reviewer_tokens_out=reviewer_result.tokens_out,
        reviewer_cost=str(reviewer_result.cost_cny),
        latency_ms=resume_row.latency_ms,
        trace_id=trace_id,
    )
    return resume_row, drafter_result, reviewer_result, retrieve


# ---------------------------------------------------------------------------
# list / get / soft_delete (caller-managed session)
# ---------------------------------------------------------------------------


async def list_resumes(
    session: AsyncSession,
    *,
    user_id: int,
    jd_id: int | None = None,
    profile_id: int | None = None,
    match_id: int | None = None,
    status: str | None = None,
    created_after: datetime | None = None,
    cursor: int | None = None,
    limit: int = 20,
) -> tuple[list[Resume], bool]:
    """Return `(rows, has_more)`. cursor=id desc(同 match list 模板)。"""
    stmt = (
        sa.select(Resume)
        .where(Resume.user_id == user_id, Resume.deleted_at.is_(None))
        .order_by(Resume.created_at.desc(), Resume.id.desc())
        .limit(limit + 1)
    )
    if jd_id is not None:
        stmt = stmt.where(Resume.jd_id == jd_id)
    if profile_id is not None:
        stmt = stmt.where(Resume.profile_id == profile_id)
    if match_id is not None:
        stmt = stmt.where(Resume.match_id == match_id)
    if status is not None:
        stmt = stmt.where(Resume.status == status)
    if created_after is not None:
        stmt = stmt.where(Resume.created_at > created_after)
    if cursor is not None:
        stmt = stmt.where(Resume.id < cursor)

    rows = list((await session.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    return rows[:limit], has_more


async def get_resume(session: AsyncSession, *, user_id: int, resume_id: int) -> Resume:
    """Return active resume row or raise `NotFoundError` (ADR-0005 D9)。"""
    cur = await session.execute(
        sa.select(Resume).where(
            Resume.id == resume_id,
            Resume.user_id == user_id,
            Resume.deleted_at.is_(None),
        )
    )
    resume = cur.scalar_one_or_none()
    if resume is None:
        raise NotFoundError(f"resume {resume_id} not found")
    return resume


async def soft_delete_resume(session: AsyncSession, *, user_id: int, resume_id: int) -> None:
    cur = await session.execute(
        sa.update(Resume)
        .where(
            Resume.id == resume_id,
            Resume.user_id == user_id,
            Resume.deleted_at.is_(None),
        )
        .values(deleted_at=sa.func.now())
        .returning(Resume.id)
    )
    if cur.scalar_one_or_none() is None:
        raise NotFoundError(f"resume {resume_id} not found")


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
        raise ResumePreconditionError(f"JD 当前状态为 {status},需先解析为 parsed 状态才能生成简历")


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
    """RAG 前提:profile 至少有一条带 embedding 的 chunk(同 match_service)。"""
    count = await session.scalar(
        sa.select(sa.func.count())
        .select_from(ProfileChunk)
        .where(
            ProfileChunk.profile_id == profile_id,
            ProfileChunk.embedding.is_not(None),
        )
    )
    if not count:
        raise ResumePreconditionError(
            "Profile 暂无 chunk 或 embedding 未生成,无法检索;请先重建 profile chunks"
        )


async def _validate_match_for_hint(
    session: AsyncSession,
    *,
    user_id: int,
    match_id: int,
    jd_id: int,
    profile_id: int,
) -> None:
    """match_id 提供时校验:存在 + 属于 user + 与 (jd_id, profile_id) 一致 +
    status='scored'(只有 scored 才有 gap_summary 可用)。"""
    match = await session.scalar(
        sa.select(Match).where(
            Match.id == match_id,
            Match.user_id == user_id,
            Match.deleted_at.is_(None),
        )
    )
    if match is None:
        raise NotFoundError(f"match {match_id} not found")
    if match.jd_id != jd_id or match.profile_id != profile_id:
        raise ResumePreconditionError(
            f"match {match_id} 与 jd_id={jd_id}/profile_id={profile_id} 不一致"
        )
    if match.status != "scored":
        raise ResumePreconditionError(
            f"match {match_id} 当前状态 {match.status},未完成评分,无法用作 hint"
        )


async def _load_resume_for_generate(
    session: AsyncSession, *, resume_id: int, user_id: int
) -> tuple[Resume, Jd, str | None]:
    """读取 pending resume 行 + JD + (可选)match 拼出的 hint。

    返回 (Resume, Jd, hint_text_or_None);均 detached(后续 IO 在 session
    外)。"""
    resume = await get_resume(session, user_id=user_id, resume_id=resume_id)
    if resume.status != "generating":
        raise ResumePreconditionError(
            f"Resume {resume_id} 当前状态 {resume.status},已完成或失败,不能重复 generate"
        )

    jd = await session.scalar(
        sa.select(Jd).where(
            Jd.id == resume.jd_id,
            Jd.user_id == user_id,
            Jd.deleted_at.is_(None),
        )
    )
    if jd is None:
        raise NotFoundError(f"jd {resume.jd_id} not found")

    hint: str | None = None
    if resume.match_id is not None:
        match = await session.scalar(
            sa.select(Match).where(
                Match.id == resume.match_id,
                Match.user_id == user_id,
                Match.deleted_at.is_(None),
            )
        )
        # match 中途被软删时降级为无 hint,而不是失败 generate。
        if match is not None and match.status == "scored":
            hint = _compose_hint(match)

    return resume, jd, hint


def _compose_hint(match: Match) -> str | None:
    """把 match.gap_summary + missing_skills 拼成 drafter prompt 的 hint 段。"""
    parts: list[str] = []
    if match.gap_summary:
        parts.append(f"差距分析:{match.gap_summary.strip()}")
    if match.missing_skills:
        names: list[str] = []
        for ms in match.missing_skills:
            if isinstance(ms, dict):
                name = ms.get("name")
                if isinstance(name, str) and name.strip():
                    names.append(name.strip())
        if names:
            parts.append("缺失关键技能(可在简历中补强相关项目/课程):" + ", ".join(names))
    return "\n".join(parts) if parts else None


async def _get_resume_locked(session: AsyncSession, *, resume_id: int, user_id: int) -> Resume:
    """Re-read resume row inside the write transaction(同 match_service 模式)。"""
    resume = await session.scalar(
        sa.select(Resume).where(
            Resume.id == resume_id,
            Resume.user_id == user_id,
            Resume.deleted_at.is_(None),
        )
    )
    if resume is None:
        raise NotFoundError(f"resume {resume_id} not found")
    return resume


def _make_title(jd: Jd) -> str:
    """简历标题模板:`{title} - {company}`。任一缺失时降级为另一个;两者都
    缺时用占位。截断到 200 字符以适配 §3.10 column。"""
    pieces = [p for p in (jd.title, jd.company) if p and p.strip()]
    title = " - ".join(pieces) if pieces else "未命名简历"
    return title[:RESUME_TITLE_MAX]


def _apply_generate_result(
    resume: Resume,
    *,
    draft_markdown: str,
    title: str,
    review: ResumeReview,
    drafter_result: LLMResult,
    reviewer_result: LLMResult,
) -> None:
    """Map drafter + reviewer 输出 → resumes 表列。Status 由 caller 决定。

    `tokens_in / tokens_out / cached_tokens / cost_cny / latency_ms` 是
    drafter+reviewer 之和;`generation_model` / `review_model` 分两列。"""
    resume.title = title
    resume.markdown = draft_markdown
    resume.review_passed = review.passed
    resume.review_findings = [f.model_dump(mode="json") for f in review.findings]
    resume.generation_model = drafter_result.model
    resume.review_model = reviewer_result.model
    resume.tokens_in = drafter_result.tokens_in + reviewer_result.tokens_in
    resume.tokens_out = drafter_result.tokens_out + reviewer_result.tokens_out
    resume.cached_tokens = drafter_result.cached_tokens + reviewer_result.cached_tokens
    resume.cost_cny = drafter_result.cost_cny + reviewer_result.cost_cny
    resume.latency_ms = drafter_result.latency_ms + reviewer_result.latency_ms


async def _mark_failed(sessionmaker: async_sessionmaker[AsyncSession], *, resume_id: int) -> None:
    """Side-channel commit: status='failed' regardless of in-flight tx
    state(套 jd_service / match_service._mark_failed 模板)。"""
    async with sessionmaker() as session, session.begin():
        await session.execute(
            sa.update(Resume).where(Resume.id == resume_id).values(status="failed")
        )


__all__ = [
    "DEFAULT_RESUME_K",
    "ResumeGenerationFailedError",
    "ResumePreconditionError",
    "create_pending_resume",
    "get_resume",
    "list_resumes",
    "run_generate",
    "soft_delete_resume",
]
