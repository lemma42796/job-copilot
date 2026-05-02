"""JD service. ADR-0006 D7 / D8 / D10.

Transaction model
-----------------

- **create_and_parse** uses an injected `async_sessionmaker` and runs in
  TWO transactions:
    1. INSERT `(status='parsing', raw_text)` and commit (D7: raw_text
       must persist before the slow LLM call so a crash leaves a
       debuggable row).
    2. After the LLM returns, a fresh session writes the structured
       fields + `status='parsed'` OR `status='parse_failed'`. The
       failure-path commit means raw_text survives the exception
       (D8: "raw_text 保留供前端手填").

  This mirrors the `DBCallLogger` side-channel pattern (ADR-0004): a
  failure in the business path must not lose the diagnostic write.

- **list / get / patch / soft_delete** take a caller-managed session,
  matching `file_service`'s convention. Routers wrap them in
  `async with session.begin()`.

Error taxonomy (ADR-0006 D8)
----------------------------

| Failure                                         | HTTP | code               |
|-------------------------------------------------|------|--------------------|
| LLM upstream 5xx / timeout                      | 502  | LLM_UPSTREAM_ERROR |
| LLM schema invalid (after 1 retry) / title空 / PDF抽空 / 文本过短 | 422  | JD_PARSE_FAILED   |

`LLMTimeoutError` (default code 504 in `llm/errors.py`) is normalized
to 502 here because — from the user's point of view at /v1/jds/parse —
"upstream is slow" and "upstream 5xx" are the same failure category.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import undefer

from jobcopilot_api.agents.jd_parser.agent import parse_jd
from jobcopilot_api.errors import JobCopilotError, NotFoundError
from jobcopilot_api.infra.pdf import extract_pdf_text
from jobcopilot_api.infra.prompts import LoadedPrompt
from jobcopilot_api.llm.client import LLMClient
from jobcopilot_api.llm.errors import (
    LLMSchemaInvalidError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from jobcopilot_api.models import File, Jd
from jobcopilot_api.schemas.jds import JDPatchInput, JDStructured

MIN_TEXT_LENGTH = 50


class JDParseFailedError(JobCopilotError):
    """422 — `JD_PARSE_FAILED`. Covers schema-invalid, empty title, and
    too-short input. PDF-loading failures use `PdfExtractionError`
    (same wire code, different class for diagnostic clarity).
    """

    status_code = 422
    code = "JD_PARSE_FAILED"
    title = "JD 解析失败"


# ---------------------------------------------------------------------------
# create + parse
# ---------------------------------------------------------------------------


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
) -> Jd:
    """Create a JD row and run JDParserAgent against it.

    Two-transaction shape (see module docstring): INSERT pending → LLM
    call → UPDATE parsed/failed. Returns the final detached `Jd` row.

    Raises `LLMUpstreamError` (502) or `JDParseFailedError` (422) per
    ADR-0006 D8; in both cases the row exists in DB at `status='parse_failed'`.
    """
    raw_text = await _resolve_raw_text(
        sessionmaker, source=source, text=text, file_id=file_id, user_id=user_id
    )
    raw_file_id = file_id if source == "pdf_upload" else None

    # Phase 1: persist parsing row.
    async with sessionmaker() as session, session.begin():
        jd = Jd(
            user_id=user_id,
            source=source,
            status="parsing",
            raw_text=raw_text,
            raw_file_id=raw_file_id,
        )
        session.add(jd)
        await session.flush()
        jd_id = jd.id

    # Phase 2: LLM call (no DB transaction held).
    try:
        result = await parse_jd(
            text=raw_text,
            prompt=prompt,
            llm=llm,
            user_id=user_id,
            trace_id=trace_id,
            related_id=jd_id,
        )
    except (LLMUpstreamError, LLMTimeoutError) as exc:
        await _mark_failed(sessionmaker, jd_id=jd_id)
        # ADR-0006 D8: timeout collapses into 502 LLM_UPSTREAM_ERROR.
        raise LLMUpstreamError(str(exc) or "LLM 上游服务异常", status_code=502) from exc
    except LLMSchemaInvalidError as exc:
        await _mark_failed(sessionmaker, jd_id=jd_id)
        raise JDParseFailedError("LLM 返回的 JSON 不符合 schema") from exc

    parsed = result.parsed
    if not isinstance(parsed, JDStructured) or not (parsed.title and parsed.title.strip()):
        await _mark_failed(sessionmaker, jd_id=jd_id)
        raise JDParseFailedError("LLM 未识别出有效 title,JD 内容可能不完整")

    # Phase 3: write structured fields.
    async with sessionmaker() as session, session.begin():
        jd_row = await get_jd(session, user_id=user_id, jd_id=jd_id)
        _apply_structured(
            jd_row,
            parsed,
            model=result.model,
            tokens=result.tokens_in + result.tokens_out,
            cost=result.cost_cny,
        )
        jd_row.status = "parsed"
    return jd_row


async def _resolve_raw_text(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    source: str,
    text: str | None,
    file_id: int | None,
    user_id: int,
) -> str:
    """Resolve the source-specific raw text. Runs before any jds row is
    inserted — failures here mean nothing got persisted.
    """
    if source == "text_paste":
        if text is None or len(text.strip()) < MIN_TEXT_LENGTH:
            raise JDParseFailedError(f"JD 文本过短(< {MIN_TEXT_LENGTH} 字符),无法解析")
        return text
    if source == "pdf_upload":
        if file_id is None:
            raise JDParseFailedError("source=pdf_upload 必须提供 file_id")
        async with sessionmaker() as session:
            file = await _get_jd_pdf_file(session, file_id=file_id, user_id=user_id)
            content = file.content
        extracted = extract_pdf_text(content)
        if len(extracted) < MIN_TEXT_LENGTH:
            raise JDParseFailedError(
                f"PDF 抽取文本过短(< {MIN_TEXT_LENGTH} 字符),可能是扫描件或加密"
            )
        return extracted
    raise JDParseFailedError(f"不支持的 source:{source}")


async def _get_jd_pdf_file(session: AsyncSession, *, file_id: int, user_id: int) -> File:
    """Fetch a non-soft-deleted file owned by `user_id`, with `content`
    undeferred. NotFound → ADR-0005 D9 (no existence-leakage difference
    between "wrong user" and "absent").
    """
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


def _apply_structured(
    jd: Jd,
    parsed: JDStructured,
    *,
    model: str,
    tokens: int,
    cost: Decimal,
) -> None:
    """Map `JDStructured` → DB columns. Does NOT touch `status`; caller
    decides between `parsed` (create_and_parse success) and the patch
    case where status may be revertable.

    `model`/`tokens`/`cost` are the LLM-call metadata for this write —
    `create_and_parse` passes the fresh values, `patch_jd` passes the
    stored values so user edits don't reset the cost ledger.
    """
    jd.company = parsed.company
    jd.title = parsed.title
    jd.location = parsed.location
    jd.salary_min = parsed.salary_min
    jd.salary_max = parsed.salary_max
    jd.salary_currency = "CNY"  # ADR-0006 D5
    jd.salary_period = parsed.salary_period
    jd.job_level = parsed.job_level
    jd.years_required = parsed.years_required
    jd.education = parsed.education
    jd.hard_skills = [s.model_dump(mode="json") for s in parsed.hard_skills]
    jd.soft_skills = [s.model_dump(mode="json") for s in parsed.soft_skills]
    jd.bonus_skills = [s.model_dump(mode="json") for s in parsed.bonus_skills]
    jd.responsibilities = list(parsed.responsibilities)
    jd.description = parsed.description
    jd.parse_confidence = Decimal(str(parsed.confidence))
    jd.parse_model = model
    jd.parse_tokens = tokens
    jd.parse_cost_cny = cost


async def _mark_failed(sessionmaker: async_sessionmaker[AsyncSession], *, jd_id: int) -> None:
    """Side-channel commit: set `status='parse_failed'` regardless of
    whatever business transaction is in flight. Mirrors DBCallLogger.
    """
    async with sessionmaker() as session, session.begin():
        await session.execute(
            sa.update(Jd)
            .where(Jd.id == jd_id)
            .values(status="parse_failed", parse_confidence=Decimal("0"))
        )


# ---------------------------------------------------------------------------
# list / get / patch / soft_delete (caller-managed session)
# ---------------------------------------------------------------------------


async def list_jds(
    session: AsyncSession,
    *,
    user_id: int,
    status: str | None = None,
    created_after: datetime | None = None,
    cursor: int | None = None,
    limit: int = 20,
) -> tuple[list[Jd], bool]:
    """Return `(rows, has_more)`. Cursor is `id` (descending), monotonic
    enough for M1 — proper opaque base64 cursor lands when pagination is
    revisited at scale.
    """
    stmt = (
        sa.select(Jd)
        .where(Jd.user_id == user_id, Jd.deleted_at.is_(None))
        .order_by(Jd.created_at.desc(), Jd.id.desc())
        .limit(limit + 1)
    )
    if status is not None:
        stmt = stmt.where(Jd.status == status)
    if created_after is not None:
        stmt = stmt.where(Jd.created_at > created_after)
    if cursor is not None:
        stmt = stmt.where(Jd.id < cursor)

    rows = list((await session.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    return rows[:limit], has_more


async def get_jd(session: AsyncSession, *, user_id: int, jd_id: int) -> Jd:
    """Return the active jd row or raise `NotFoundError` (D9: same
    response for "soft-deleted", "wrong user", "absent")."""
    cur = await session.execute(
        sa.select(Jd).where(
            Jd.id == jd_id,
            Jd.user_id == user_id,
            Jd.deleted_at.is_(None),
        )
    )
    jd = cur.scalar_one_or_none()
    if jd is None:
        raise NotFoundError(f"jd {jd_id} not found")
    return jd


async def patch_jd(
    session: AsyncSession,
    *,
    user_id: int,
    jd_id: int,
    patch: JDPatchInput,
) -> Jd:
    """Apply user edits. `structured` rewrites the parsed columns;
    `status` overrides last so the client can also flip
    parsing→parsed without supplying structured. ADR-0006 D2.
    """
    jd = await get_jd(session, user_id=user_id, jd_id=jd_id)
    if patch.structured is not None:
        _apply_structured(
            jd,
            patch.structured,
            model=jd.parse_model or "",
            tokens=jd.parse_tokens or 0,
            cost=jd.parse_cost_cny or Decimal("0"),
        )
    if patch.status is not None:
        jd.status = patch.status
    await session.flush()
    return jd


async def soft_delete_jd(session: AsyncSession, *, user_id: int, jd_id: int) -> None:
    """`UPDATE deleted_at = NOW()` with `RETURNING id` for an existence
    probe. NotFoundError if the row was already gone (idempotent caller
    behavior is the router's job).
    """
    cur = await session.execute(
        sa.update(Jd)
        .where(
            Jd.id == jd_id,
            Jd.user_id == user_id,
            Jd.deleted_at.is_(None),
        )
        .values(deleted_at=sa.func.now())
        .returning(Jd.id)
    )
    if cur.scalar_one_or_none() is None:
        raise NotFoundError(f"jd {jd_id} not found")
