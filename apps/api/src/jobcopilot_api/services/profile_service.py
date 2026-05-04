"""Profile service. Mirrors `jd_service` for the parse pipeline, plus
DELETE-then-INSERT child rebuilds for the 5-table profile structure.

Transaction model
-----------------

Same two-phase split as JD so the SSE router can emit `started` between
Phase 1 and Phase 2 with the freshly-minted `profile_id`:

- **create_pending_profile** — Phase 1: enforce per-user uniqueness
  (Q2: 409 on duplicate, no auto-overwrite), resolve raw_text, INSERT
  `(status='parsing', raw_text, source)` and commit.
- **run_parse** — Phase 2 (LLM) + Phase 3 (UPDATE parent + DELETE/INSERT
  children). Failure marks `status='parse_failed'` via side-channel
  commit so raw_text + diagnostic state survive.

`create_and_parse` exists for symmetry with jd_service but is unused
today — `POST /v1/profiles/parse` is SSE-only (Q-mark in S7 plan).

Error taxonomy
--------------

| Failure                             | HTTP | code                  |
|-------------------------------------|------|-----------------------|
| LLM upstream 5xx / timeout          | 502  | LLM_UPSTREAM_ERROR    |
| LLM schema invalid / 文本过短 / PDF抽空 | 422  | PROFILE_PARSE_FAILED  |
| 该 user 已有 profile                | 409  | PROFILE_EXISTS        |

Skill dedupe (S7 plan Q3 / 永久约束 jds-style):

`profile_skills` has `UNIQUE (profile_id, name)`. `_apply_structured`
collapses duplicates in the LLM output by normalized `name`, last-write
wins. The prompt also instructs the LLM to dedupe — this is belt + braces.
"""

from __future__ import annotations

from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import undefer

from jobcopilot_api.agents.profile_parser.agent import parse_profile
from jobcopilot_api.errors import ConflictError, JobCopilotError, NotFoundError
from jobcopilot_api.infra.pdf import extract_pdf_text
from jobcopilot_api.infra.prompts import LoadedPrompt
from jobcopilot_api.llm.client import LLMClient, LLMResult
from jobcopilot_api.llm.errors import (
    LLMSchemaInvalidError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from jobcopilot_api.models import (
    File,
    Profile,
    ProfileEducation,
    ProfileExperience,
    ProfileProject,
    ProfileSkill,
    User,
)
from jobcopilot_api.schemas.profiles import (
    ProfilePatchInput,
    ProfileSkillItem,
    ProfileStructured,
)

MIN_TEXT_LENGTH = 100  # resumes are longer than the JD floor (50)


class ProfileParseFailedError(JobCopilotError):
    """422 — `PROFILE_PARSE_FAILED`. Schema-invalid, too-short input, or
    PDF that yielded no text. Mirrors `JDParseFailedError`."""

    status_code = 422
    code = "PROFILE_PARSE_FAILED"
    title = "简历解析失败"


class ProfileExistsError(ConflictError):
    """409 — user already has a profile (S7 plan Q2). Client should
    explicitly DELETE the existing one before re-parsing."""

    code = "PROFILE_EXISTS"
    title = "用户已有 profile"


# ---------------------------------------------------------------------------
# create + parse
# ---------------------------------------------------------------------------


async def create_pending_profile(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    user_id: int,
    source: str,
    text: str | None,
    file_id: int | None,
) -> tuple[int, str]:
    """Phase 1: enforce per-user uniqueness, resolve raw_text, INSERT
    `status='parsing'` row.

    Returns `(profile_id, raw_text)`. Pre-insert failures (text too
    short / PDF garbage / duplicate user) raise before any row is
    written.
    """
    raw_text = await _resolve_raw_text(
        sessionmaker, source=source, text=text, file_id=file_id, user_id=user_id
    )
    raw_file_id = file_id if source == "pdf_upload" else None

    async with sessionmaker() as session, session.begin():
        # 校验 user 存在,不然 INSERT 会撞 fk_profiles_user_id 抛 IntegrityError,
        # SSE generator 起手未发 started 就死,前端拿到 0 字节静默(S11 dogfood
        # bad case #2)。映射成 NotFoundError → SSE error/done 显式回报。
        user_exists = await session.scalar(sa.select(sa.literal(1)).where(User.id == user_id))
        if user_exists is None:
            raise NotFoundError(f"用户 {user_id} 不存在")

        existing_id = (
            await session.execute(
                sa.select(Profile.id).where(
                    Profile.user_id == user_id, Profile.deleted_at.is_(None)
                )
            )
        ).scalar_one_or_none()
        if existing_id is not None:
            raise ProfileExistsError(
                f"user {user_id} already has profile {existing_id}; DELETE it first to re-parse"
            )

        profile = Profile(
            user_id=user_id,
            source=source,
            status="parsing",
            raw_text=raw_text,
            raw_file_id=raw_file_id,
        )
        session.add(profile)
        await session.flush()
        return profile.id, raw_text


async def run_parse(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    profile_id: int,
    user_id: int,
    raw_text: str,
    llm: LLMClient,
    prompt: LoadedPrompt,
    trace_id: str | None = None,
) -> tuple[Profile, LLMResult]:
    """Phase 2 (LLM) + Phase 3 (UPDATE parent + rebuild children).

    On success returns `(Profile, LLMResult)`; the router needs
    `LLMResult` for token breakdown reporting. On any failure marks
    `status='parse_failed'` via side-channel commit and raises.
    """
    try:
        result = await parse_profile(
            text=raw_text,
            prompt=prompt,
            llm=llm,
            user_id=user_id,
            trace_id=trace_id,
            related_id=profile_id,
        )
    except (LLMUpstreamError, LLMTimeoutError) as exc:
        await _mark_failed(sessionmaker, profile_id=profile_id)
        raise LLMUpstreamError(str(exc) or "LLM 上游服务异常", status_code=502) from exc
    except LLMSchemaInvalidError as exc:
        await _mark_failed(sessionmaker, profile_id=profile_id)
        # 把 Pydantic 校验路径透到 SSE error.detail 上限 500 字符,dogfood 时
        # 直接能看到哪个字段不符合 schema(S11 bad case #1 调试代价)。
        snippet = str(exc)[:500] if str(exc) else ""
        msg = (
            f"LLM 返回的 JSON 不符合 schema: {snippet}"
            if snippet
            else "LLM 返回的 JSON 不符合 schema"
        )
        raise ProfileParseFailedError(msg) from exc

    parsed = result.parsed
    if not isinstance(parsed, ProfileStructured):
        await _mark_failed(sessionmaker, profile_id=profile_id)
        raise ProfileParseFailedError("LLM 未返回有效 ProfileStructured")

    async with sessionmaker() as session, session.begin():
        profile = await get_profile(session, user_id=user_id, profile_id=profile_id)
        _apply_parent(
            profile,
            parsed,
            model=result.model,
            tokens=result.tokens_in + result.tokens_out,
            cost=result.cost_cny,
        )
        await _replace_children(session, profile_id=profile_id, parsed=parsed)
        profile.status = "parsed"
    return profile, result


async def create_and_parse(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    user_id: int,
    source: str,
    text: str | None,
    file_id: int | None,
    llm: LLMClient,
    prompt: LoadedPrompt,
    trace_id: str | None = None,
) -> tuple[Profile, LLMResult]:
    """Sync-path wrapper: Phase 1 → Phase 2/3 in one call. Kept for
    symmetry with `jd_service.create_and_parse`; SSE path uses the
    two-phase split directly."""
    profile_id, raw_text = await create_pending_profile(
        sessionmaker, user_id=user_id, source=source, text=text, file_id=file_id
    )
    return await run_parse(
        sessionmaker,
        profile_id=profile_id,
        user_id=user_id,
        raw_text=raw_text,
        llm=llm,
        prompt=prompt,
        trace_id=trace_id,
    )


async def _resolve_raw_text(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    source: str,
    text: str | None,
    file_id: int | None,
    user_id: int,
) -> str:
    if source == "text_paste":
        if text is None or len(text.strip()) < MIN_TEXT_LENGTH:
            raise ProfileParseFailedError(f"简历文本过短(< {MIN_TEXT_LENGTH} 字符),无法解析")
        return text
    if source == "pdf_upload":
        if file_id is None:
            raise ProfileParseFailedError("source=pdf_upload 必须提供 file_id")
        async with sessionmaker() as session:
            file = await _get_profile_pdf_file(session, file_id=file_id, user_id=user_id)
            content = file.content
        extracted = extract_pdf_text(content)
        if len(extracted) < MIN_TEXT_LENGTH:
            raise ProfileParseFailedError(
                f"PDF 抽取文本过短(< {MIN_TEXT_LENGTH} 字符),可能是扫描件或加密"
            )
        return extracted
    raise ProfileParseFailedError(f"不支持的 source:{source}")


async def _get_profile_pdf_file(session: AsyncSession, *, file_id: int, user_id: int) -> File:
    cur = await session.execute(
        sa.select(File)
        .where(
            File.id == file_id,
            File.user_id == user_id,
            File.deleted_at.is_(None),
        )
        .options(undefer(File.content))
    )
    file = cur.scalar_one_or_none()
    if file is None:
        raise NotFoundError(f"file {file_id} not found")
    return file


def _apply_parent(
    profile: Profile,
    parsed: ProfileStructured,
    *,
    model: str,
    tokens: int,
    cost: Decimal,
) -> None:
    """Map the parent fields from `ProfileStructured` → DB columns. Does
    NOT touch `status` / children."""
    profile.full_name = parsed.full_name
    profile.phone = parsed.phone
    profile.email = parsed.email
    profile.location = parsed.location
    profile.summary = parsed.summary
    profile.target_titles = list(parsed.target_titles)
    profile.parse_model = model
    profile.parse_tokens = tokens
    profile.parse_cost_cny = cost


async def _replace_children(
    session: AsyncSession, *, profile_id: int, parsed: ProfileStructured
) -> None:
    """DELETE-then-INSERT all 4 child tables. Skill dedupe by normalized
    name (last write wins) so `uq_ps_profile_name` won't trip."""
    await session.execute(
        sa.delete(ProfileExperience).where(ProfileExperience.profile_id == profile_id)
    )
    await session.execute(sa.delete(ProfileProject).where(ProfileProject.profile_id == profile_id))
    await session.execute(sa.delete(ProfileSkill).where(ProfileSkill.profile_id == profile_id))
    await session.execute(
        sa.delete(ProfileEducation).where(ProfileEducation.profile_id == profile_id)
    )
    await session.flush()

    for idx, exp in enumerate(parsed.experiences):
        session.add(
            ProfileExperience(
                profile_id=profile_id,
                company=exp.company,
                title=exp.title,
                location=exp.location,
                start_date=exp.start_date,
                end_date=exp.end_date,
                is_current=exp.is_current,
                description=exp.description,
                bullets=list(exp.bullets),
                tech_stack=list(exp.tech_stack),
                achievements=list(exp.achievements),
                sort_order=idx,
            )
        )

    for idx, proj in enumerate(parsed.projects):
        session.add(
            ProfileProject(
                profile_id=profile_id,
                name=proj.name,
                role=proj.role,
                start_date=proj.start_date,
                end_date=proj.end_date,
                description=proj.description,
                bullets=list(proj.bullets),
                tech_stack=list(proj.tech_stack),
                achievements=list(proj.achievements),
                repo_url=proj.repo_url,
                demo_url=proj.demo_url,
                sort_order=idx,
            )
        )

    deduped: dict[str, ProfileSkillItem] = {}
    for sk in parsed.skills:
        deduped[sk.name] = sk
    for sk in deduped.values():
        session.add(
            ProfileSkill(
                profile_id=profile_id,
                name=sk.name,
                name_raw=sk.name_raw,
                category=sk.category,
                level=sk.level,
                years=Decimal(str(sk.years)) if sk.years is not None else None,
            )
        )

    for idx, edu in enumerate(parsed.educations):
        session.add(
            ProfileEducation(
                profile_id=profile_id,
                school=edu.school,
                degree=edu.degree,
                major=edu.major,
                start_date=edu.start_date,
                end_date=edu.end_date,
                gpa=Decimal(str(edu.gpa)) if edu.gpa is not None else None,
                honors=list(edu.honors),
                sort_order=idx,
            )
        )


async def _mark_failed(sessionmaker: async_sessionmaker[AsyncSession], *, profile_id: int) -> None:
    """Side-channel commit: set `status='parse_failed'` regardless of
    whatever business transaction is in flight."""
    async with sessionmaker() as session, session.begin():
        await session.execute(
            sa.update(Profile).where(Profile.id == profile_id).values(status="parse_failed")
        )


# ---------------------------------------------------------------------------
# list / get / patch / soft_delete (caller-managed session)
# ---------------------------------------------------------------------------


async def list_profiles(
    session: AsyncSession,
    *,
    user_id: int,
    cursor: int | None = None,
    limit: int = 20,
) -> tuple[list[Profile], bool]:
    """Return `(rows, has_more)`. Cursor is `id` (descending). M1 single-
    user means the list typically has 0 or 1 rows; pagination is here
    for M3 multi-version-resume support."""
    stmt = (
        sa.select(Profile)
        .where(Profile.user_id == user_id, Profile.deleted_at.is_(None))
        .order_by(Profile.created_at.desc(), Profile.id.desc())
        .limit(limit + 1)
    )
    if cursor is not None:
        stmt = stmt.where(Profile.id < cursor)

    rows = list((await session.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    return rows[:limit], has_more


async def get_profile(session: AsyncSession, *, user_id: int, profile_id: int) -> Profile:
    cur = await session.execute(
        sa.select(Profile).where(
            Profile.id == profile_id,
            Profile.user_id == user_id,
            Profile.deleted_at.is_(None),
        )
    )
    profile = cur.scalar_one_or_none()
    if profile is None:
        raise NotFoundError(f"profile {profile_id} not found")
    return profile


async def get_children(
    session: AsyncSession, *, profile_id: int
) -> tuple[
    list[ProfileExperience],
    list[ProfileProject],
    list[ProfileSkill],
    list[ProfileEducation],
]:
    """Read all 4 child collections for the detail view, ordered by
    `sort_order` then `id` for deterministic output."""
    exps = list(
        (
            await session.execute(
                sa.select(ProfileExperience)
                .where(ProfileExperience.profile_id == profile_id)
                .order_by(ProfileExperience.sort_order, ProfileExperience.id)
            )
        )
        .scalars()
        .all()
    )
    projs = list(
        (
            await session.execute(
                sa.select(ProfileProject)
                .where(ProfileProject.profile_id == profile_id)
                .order_by(ProfileProject.sort_order, ProfileProject.id)
            )
        )
        .scalars()
        .all()
    )
    skills = list(
        (
            await session.execute(
                sa.select(ProfileSkill)
                .where(ProfileSkill.profile_id == profile_id)
                .order_by(ProfileSkill.sort_order, ProfileSkill.id)
            )
        )
        .scalars()
        .all()
    )
    edus = list(
        (
            await session.execute(
                sa.select(ProfileEducation)
                .where(ProfileEducation.profile_id == profile_id)
                .order_by(ProfileEducation.sort_order, ProfileEducation.id)
            )
        )
        .scalars()
        .all()
    )
    return exps, projs, skills, edus


async def patch_profile(
    session: AsyncSession,
    *,
    user_id: int,
    profile_id: int,
    patch: ProfilePatchInput,
) -> Profile:
    """Apply user edits. `structured` rebuilds children; `status` /
    `target_salary_*` override last so a single PATCH can do both.

    Reusing `_apply_parent` would reset `parse_model`/`tokens`/`cost`
    on every PATCH, so we inline the parent field copy with the existing
    cost ledger instead.
    """
    profile = await get_profile(session, user_id=user_id, profile_id=profile_id)

    if patch.structured is not None:
        profile.full_name = patch.structured.full_name
        profile.phone = patch.structured.phone
        profile.email = patch.structured.email
        profile.location = patch.structured.location
        profile.summary = patch.structured.summary
        profile.target_titles = list(patch.structured.target_titles)
        await _replace_children(session, profile_id=profile_id, parsed=patch.structured)

    if patch.status is not None:
        profile.status = patch.status
    if patch.target_salary_min is not None:
        profile.target_salary_min = patch.target_salary_min
    if patch.target_salary_max is not None:
        profile.target_salary_max = patch.target_salary_max

    await session.flush()
    return profile


async def soft_delete_profile(session: AsyncSession, *, user_id: int, profile_id: int) -> None:
    cur = await session.execute(
        sa.update(Profile)
        .where(
            Profile.id == profile_id,
            Profile.user_id == user_id,
            Profile.deleted_at.is_(None),
        )
        .values(deleted_at=sa.func.now())
        .returning(Profile.id)
    )
    if cur.scalar_one_or_none() is None:
        raise NotFoundError(f"profile {profile_id} not found")
