"""Resume service — pending row creation + streaming graph runner + CRUD。

Two-phase pipeline 套 match_service 模板,M3 W7 升级:

- **create_pending_resume** (Phase 1): validate inputs + INSERT
  `status='generating'` row。永久约束 #4: SSE `started` event 在 phase 1
  之后 emit,带这里返回的 resume_id 做 resource_id。
- **run_generate_stream** (Phase 2, M3 W7 改造): 用 5 节点 LangGraph
  (retrieve → plan → draft → review → revise) 替代 S16 的"3 函数串调"。
  返回 `AsyncIterator[dict]` —— router 把每条事件转成 SSE。事件 shape:
  - `{"event": "node_completed", "node": "retrieve|plan|draft|review|revise",
     "revision_count": int}`
  - `{"event": "final", "resume_id": int, "status": "ready|review_failed",
     "review_passed": bool|None}`

Failure path 与 S16 一致:LLM upstream / timeout / schema invalid / 业务级
失败 → service 在 `except` 里调 `_mark_failed`(side-channel commit)+ raise。
review_failed(任意 high severity)**不**当失败,markdown 仍存供前端展示。

list / get / soft_delete 走 caller-managed session(同 match_service 模式)。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobcopilot_api.agents.resume_graph import (
    DEFAULT_MAX_REVISIONS,
    DEFAULT_RESUME_K,
    PreLoadedResumeContext,
    ResumeGraphDeps,
    ResumeGraphState,
    build_resume_graph,
    stream_resume_graph,
)
from jobcopilot_api.errors import JobCopilotError, NotFoundError
from jobcopilot_api.infra.prompts import LoadedPrompt
from jobcopilot_api.llm.client import LLMClient, LLMResult
from jobcopilot_api.llm.embedders import Embedder
from jobcopilot_api.llm.errors import (
    LLMSchemaInvalidError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from jobcopilot_api.models import (
    Jd,
    Match,
    Profile,
    ProfileChunk,
    ProfileEducation,
    Resume,
    ResumeVersion,
    User,
)
from jobcopilot_api.schemas.resumes import RESUME_VERSION_NOTE_MAX, ResumeReview

log = structlog.get_logger(__name__)

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

    `run_generate_stream(resume_id, ...)` 接力 phase 2。"""
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
# Phase 2: run generate (M3 W7 — graph streaming)
# ---------------------------------------------------------------------------


async def run_generate_stream(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    resume_id: int,
    user_id: int,
    embedder: Embedder,
    llm: LLMClient,
    drafter_prompt: LoadedPrompt,
    reviewer_prompt: LoadedPrompt,
    planner_prompt: LoadedPrompt,
    trace_id: str | None = None,
    k: int = DEFAULT_RESUME_K,
    max_revisions: int = DEFAULT_MAX_REVISIONS,
) -> AsyncIterator[dict[str, Any]]:
    """Phase 2: build graph → stream node events → 写 resume + resume_versions v1。

    分层(永久约束 #7 + M3 W7 + W8):
    1. 短 tx: 读 resume 行 + jd + (optional) match → 拿 hint + candidate
    2. build graph(无 checkpointer,5 节点)+ pre_loaded context +
       drafter token 回调
    3. 跑 graph 在后台 task,通过 asyncio.Queue 把 `drafter_token` /
       `node_completed` 两类事件交错发给 caller(W8 起)
    4. 单事务: UPDATE resume + INSERT resume_versions v1

    SSE 事件交错说明(W8 新):drafter / revise 节点跑 LLM 时,token delta
    从 `on_drafter_token` 流入 queue;**同一节点结束**才会有 `node_completed`
    事件入 queue。所以前端见到的是
    `drafter_token*(phase=draft) → node_completed(node=draft)
     → drafter_token*(phase=revise)? → node_completed(node=revise)?`,phase
    切换是前端重置预览缓冲的信号。

    Failure path:
    - retrieve 召回空 → mark_failed + ResumeGenerationFailedError(422)
    - 任意节点 LLM upstream / timeout → mark_failed + LLMUpstreamError(502)
    - planner / reviewer schema invalid → mark_failed + ResumeGenerationFailedError(422)
    - reviewer.passed=False **且** revision 用尽 → **不 mark_failed**,
      status='review_failed',markdown / findings 仍存供前端展示
    """
    # Phase 2.1 — short tx 加载 pre_loaded 上下文
    async with sessionmaker() as session:
        resume, jd, hint, candidate = await _load_resume_for_generate(
            session, resume_id=resume_id, user_id=user_id
        )

    # Phase 2.2 — queue + drafter token callback,交错 SSE 事件
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    SENTINEL_DONE: dict[str, Any] = {"__sentinel__": "done"}

    async def _on_drafter_token(phase: str, delta: str) -> None:
        await queue.put({"event": "drafter_token", "phase": phase, "delta": delta})

    deps = ResumeGraphDeps(
        sessionmaker=sessionmaker,
        llm=llm,
        embedder=embedder,
        drafter_prompt=drafter_prompt,
        reviewer_prompt=reviewer_prompt,
        planner_prompt=planner_prompt,
        k=k,
        on_drafter_token=_on_drafter_token,
    )
    pre_loaded = PreLoadedResumeContext(
        profile_id=resume.profile_id,
        jd=jd,
        hint=hint,
        candidate=candidate,
    )
    graph = build_resume_graph(deps, pre_loaded=pre_loaded)

    initial_state: ResumeGraphState = {
        "resume_id": resume_id,
        "user_id": user_id,
        "trace_id": trace_id,
        "max_revisions": max_revisions,
        "revision_count": 0,
        "drafter_results": [],
        "reviewer_results": [],
    }

    final_state: dict[str, Any] = {}
    runner_error: BaseException | None = None

    async def _runner() -> None:
        """Drive the graph; push events into queue. Captures business
        exceptions to be re-raised by the outer coroutine after queue drain
        — this preserves the original exc-chain mapping(LLMUpstream → 502
        etc.)without losing in-flight token / node events."""
        nonlocal runner_error, final_state
        try:
            async for kind, payload in stream_resume_graph(
                graph, initial_state, thread_id=str(resume_id)
            ):
                if kind == "node_completed":
                    await queue.put(
                        {
                            "event": "node_completed",
                            "node": payload.node,
                            "revision_count": payload.revision_count,
                        }
                    )
                elif kind == "final":
                    final_state = payload
        except Exception as exc:  # noqa: BLE001 — restored via re-raise below
            runner_error = exc
        finally:
            queue.put_nowait(SENTINEL_DONE)

    runner_task = asyncio.create_task(_runner())
    try:
        while True:
            ev = await queue.get()
            if ev is SENTINEL_DONE:
                break
            yield ev
    except BaseException:
        # SSE client disconnect / outer cancel → tear down graph runner
        runner_task.cancel()
        with contextlib.suppress(BaseException):
            await runner_task
        raise
    else:
        await runner_task

    if runner_error is not None:
        if isinstance(runner_error, (LLMUpstreamError, LLMTimeoutError)):
            await _mark_failed(sessionmaker, resume_id=resume_id)
            raise LLMUpstreamError(
                str(runner_error) or "LLM 上游异常", status_code=502
            ) from runner_error
        if isinstance(runner_error, LLMSchemaInvalidError):
            await _mark_failed(sessionmaker, resume_id=resume_id)
            snippet = str(runner_error)[:500] if str(runner_error) else ""
            raise ResumeGenerationFailedError(
                f"LLM 返回的 JSON 不符合 schema:{snippet}"
                if snippet
                else "LLM 返回的 JSON 不符合 schema"
            ) from runner_error
        await _mark_failed(sessionmaker, resume_id=resume_id)
        raise runner_error

    if not final_state or "draft_markdown" not in final_state:
        await _mark_failed(sessionmaker, resume_id=resume_id)
        raise ResumeGenerationFailedError("Graph 未返回 final state(draft_markdown 缺失)")

    review = final_state.get("review")
    if not isinstance(review, ResumeReview):
        await _mark_failed(sessionmaker, resume_id=resume_id)
        raise ResumeGenerationFailedError("Graph 未产出有效 ResumeReview")

    final_status = "ready" if review.passed else "review_failed"
    title = _make_title(jd)

    drafter_results: list[LLMResult] = final_state.get("drafter_results") or []
    reviewer_results: list[LLMResult] = final_state.get("reviewer_results") or []
    planner_result: LLMResult | None = final_state.get("planner_result")
    revision_count: int = final_state.get("revision_count", 0)

    # Phase 2.3 — 单事务写 resume + version v1
    async with sessionmaker() as session, session.begin():
        resume_row = await _get_resume_locked(session, resume_id=resume_id, user_id=user_id)
        _apply_generate_result(
            resume_row,
            draft_markdown=final_state["draft_markdown"],
            title=title,
            review=review,
            drafter_results=drafter_results,
            reviewer_results=reviewer_results,
            planner_result=planner_result,
            revision_count=revision_count,
        )
        resume_row.status = final_status

        version = ResumeVersion(
            resume_id=resume_id,
            version_number=1,
            markdown=final_state["draft_markdown"],
            edit_type="generated",
        )
        session.add(version)

    log.info(
        "resume_service.run_generate_stream",
        resume_id=resume_id,
        user_id=user_id,
        status=final_status,
        review_passed=review.passed,
        review_findings=len(review.findings),
        revisions=revision_count,
        chunks_retrieved=len(final_state.get("chunks") or []),
        drafter_calls=len(drafter_results),
        reviewer_calls=len(reviewer_results),
        planner_called=planner_result is not None,
        tokens_in=resume_row.tokens_in,
        tokens_out=resume_row.tokens_out,
        cost_cny=str(resume_row.cost_cny) if resume_row.cost_cny is not None else None,
        latency_ms=resume_row.latency_ms,
        trace_id=trace_id,
    )

    yield {
        "event": "final",
        "resume_id": resume_id,
        "status": final_status,
        "review_passed": review.passed,
        "revisions": revision_count,
    }


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


async def list_resume_versions(
    session: AsyncSession, *, user_id: int, resume_id: int
) -> list[ResumeVersion]:
    """All versions of a resume, oldest → newest by `version_number`.

    Resume 所有权校验先行(`get_resume` 抛 NotFoundError);只读路径,caller
    管 session。"""
    await get_resume(session, user_id=user_id, resume_id=resume_id)
    rows = (
        await session.scalars(
            sa.select(ResumeVersion)
            .where(ResumeVersion.resume_id == resume_id)
            .order_by(ResumeVersion.version_number.asc())
        )
    ).all()
    return list(rows)


async def create_resume_version(
    session: AsyncSession,
    *,
    user_id: int,
    resume_id: int,
    markdown: str,
    note: str | None,
) -> ResumeVersion:
    """User-driven 编辑保存。逻辑:

    1. 取所有权 + 校验 resume.status ∈ {ready, review_failed}(failed /
       generating 不允许编辑 — 没意义)
    2. SELECT max(version_number) → next_version
    3. INSERT resume_versions row(`edit_type='edited'`)
    4. UPDATE resumes.markdown = 新内容(便于 GET /resumes/{id} 默认拿活
       动版本,同时 review_findings 不动 — 那是 reviewer 跑的快照,跟用户
       手改无关;若希望"编辑后清空 findings",留 W8 后续讨论)

    Caller 必须把 session 包在 begin() 事务里。"""
    if note is not None and len(note) > RESUME_VERSION_NOTE_MAX:
        raise ResumePreconditionError(
            f"编辑备注超过 {RESUME_VERSION_NOTE_MAX} 字符上限"
        )
    resume = await get_resume(session, user_id=user_id, resume_id=resume_id)
    if resume.status not in ("ready", "review_failed"):
        raise ResumePreconditionError(
            f"Resume {resume_id} 当前状态 {resume.status},不能编辑(只允许 ready / review_failed)"
        )

    next_version = (
        await session.scalar(
            sa.select(sa.func.coalesce(sa.func.max(ResumeVersion.version_number), 0)).where(
                ResumeVersion.resume_id == resume_id
            )
        )
    ) or 0
    new_version = ResumeVersion(
        resume_id=resume_id,
        version_number=next_version + 1,
        markdown=markdown,
        edit_type="edited",
        edit_note=note,
    )
    session.add(new_version)
    resume.markdown = markdown
    await session.flush()
    return new_version


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
) -> tuple[Resume, Jd, str | None, dict[str, Any]]:
    """读取 pending resume 行 + JD + (可选)match 拼出的 hint + candidate
    deterministic 字段(profile 顶层字段 + educations,**不在 chunks 里**的)。

    返回 (Resume, Jd, hint_text_or_None, candidate);均 detached(后续 IO
    在 session 外)。

    `candidate` 是 profile 表上**不在 chunks 里**的 deterministic 字段透传
    (drafter v1.0.3+):
    - `full_name` / `phone` / `email` / `location`:profile 顶层字段
    - `target_titles`(list[str]):profile 顶层字段
    - `educations`(list[dict]):ProfileEducation 关联表全量
    """
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

    profile = await session.scalar(
        sa.select(Profile).where(
            Profile.id == resume.profile_id,
            Profile.user_id == user_id,
            Profile.deleted_at.is_(None),
        )
    )
    candidate: dict[str, Any] = {}
    if profile is not None:
        candidate = {
            "full_name": profile.full_name,
            "phone": profile.phone,
            "email": profile.email,
            "location": profile.location,
            "target_titles": [
                str(t) for t in (profile.target_titles or []) if str(t).strip()
            ],
        }
        edu_rows = (
            await session.scalars(
                sa.select(ProfileEducation)
                .where(ProfileEducation.profile_id == profile.id)
                .order_by(ProfileEducation.sort_order, ProfileEducation.id)
            )
        ).all()
        candidate["educations"] = [
            {
                "school": e.school,
                "degree": e.degree,
                "major": e.major,
                "start_date": e.start_date.isoformat() if e.start_date else None,
                "end_date": e.end_date.isoformat() if e.end_date else None,
                "gpa": float(e.gpa) if e.gpa is not None else None,
                "honors": list(e.honors) if e.honors else [],
            }
            for e in edu_rows
        ]

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

    return resume, jd, hint, candidate


def _compose_hint(match: Match) -> str | None:
    """把 match.gap_summary + missing_skills 拼成 drafter prompt 的 hint 段。

    ⚠️ **文案设计的关键约束**(S21 W8 dogfood resume #19 真 bug 后修订):
    hint 注入到 drafter USER 段,LLM 视为权威指令位。原版文案"缺失关键技能
    (可在简历中补强相关项目/课程)"等于明确命令 drafter 把候选人不会的技能
    写进简历(JD 镜像 + 编造)。新文案改成**反向警告语义** — gap_summary 加
    "只读,不要写入简历"前缀;missing_skills 改成"以下技能候选人 chunks 没
    有,**严禁列入简历**(gap 留给用户决策,不是简历该粉饰的)"。这与
    drafter prompt v1.0.5 D.3 + v1.0.6 USER 段 hint 防注入语相互兜底。
    """
    parts: list[str] = []
    if match.gap_summary:
        parts.append(
            "**只读差距分析**(供 drafter 理解候选人弱点,**不要写入简历**;"
            "gap 信息归 match 模块负责告知用户,不是简历该粉饰的):\n"
            + match.gap_summary.strip()
        )
    if match.missing_skills:
        names: list[str] = []
        for ms in match.missing_skills:
            if isinstance(ms, dict):
                name = ms.get("name")
                if isinstance(name, str) and name.strip():
                    names.append(name.strip())
        if names:
            parts.append(
                "**严禁列入简历的技能**(候选人 chunks 没有,JD 要 — 列了就是编造):"
                + ", ".join(names)
            )
    return "\n\n".join(parts) if parts else None


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
    drafter_results: list[LLMResult],
    reviewer_results: list[LLMResult],
    planner_result: LLMResult | None,
    revision_count: int,
) -> None:
    """Aggregate planner + drafter(可能多次)+ reviewer(可能多次)的 LLM 结果
    → resumes 表列。Status 由 caller 决定。

    `tokens_in / tokens_out / cached_tokens / cost_cny / latency_ms` 是所有
    LLM 调用之和(planner + 全部 draft + 全部 review)。`generation_model` 取
    最新一次 drafter 的 model,`review_model` 取最新一次 reviewer 的 model。"""
    all_calls: list[LLMResult] = []
    if planner_result is not None:
        all_calls.append(planner_result)
    all_calls.extend(drafter_results)
    all_calls.extend(reviewer_results)

    resume.title = title
    resume.markdown = draft_markdown
    resume.review_passed = review.passed
    resume.review_findings = [f.model_dump(mode="json") for f in review.findings]
    resume.generation_model = drafter_results[-1].model if drafter_results else None
    resume.review_model = reviewer_results[-1].model if reviewer_results else None
    resume.tokens_in = sum(r.tokens_in for r in all_calls)
    resume.tokens_out = sum(r.tokens_out for r in all_calls)
    resume.cached_tokens = sum(r.cached_tokens for r in all_calls)
    resume.cost_cny = (
        sum((r.cost_cny for r in all_calls), Decimal("0")) if all_calls else None
    )
    resume.latency_ms = sum(r.latency_ms for r in all_calls)
    resume.revisions = revision_count


async def _mark_failed(sessionmaker: async_sessionmaker[AsyncSession], *, resume_id: int) -> None:
    """Side-channel commit: status='failed' regardless of in-flight tx
    state(套 jd_service / match_service._mark_failed 模板)。"""
    async with sessionmaker() as session, session.begin():
        await session.execute(
            sa.update(Resume).where(Resume.id == resume_id).values(status="failed")
        )


__all__ = [
    "DEFAULT_MAX_REVISIONS",
    "DEFAULT_RESUME_K",
    "ResumeGenerationFailedError",
    "ResumePreconditionError",
    "create_pending_resume",
    "create_resume_version",
    "get_resume",
    "list_resume_versions",
    "list_resumes",
    "run_generate_stream",
    "soft_delete_resume",
]
