"""Integration tests for `services.jd_service`. ADR-0006 D7 / D8 / D10.

Covers:

  - create_and_parse: text_paste happy / PDF happy / text too short /
    PDF garbage / LLM upstream / LLM schema invalid / title empty
    (the four-row failure matrix from D8 plus the resolve-raw-text
    branch that fails BEFORE any jds row is inserted).
  - get / list / patch / soft_delete: ownership boundaries, soft-delete
    invisibility, status filters, cursor.

Uses testcontainers for the full migration set (jds + ENUMs + files
needed by the PDF branch) and DummyProvider to drive parse_jd without
touching the network.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from tenacity.wait import wait_none
from testcontainers.postgres import PostgresContainer

from jobcopilot_api.infra.prompts import LoadedPrompt
from jobcopilot_api.llm.client import BaseLLMClient, MemoryCallLogger
from jobcopilot_api.llm.errors import LLMUpstreamError
from jobcopilot_api.llm.providers.dummy import DummyProvider
from jobcopilot_api.models import File, Jd, User
from jobcopilot_api.schemas.jds import JDPatchInput, JDStructured
from jobcopilot_api.services.jd_service import (
    JDParseFailedError,
    create_and_parse,
    get_jd,
    list_jds,
    patch_jd,
    soft_delete_jd,
)

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


# Reuses the test_pdf_extract _make_pdf logic; duplicated here to keep
# integration tests self-contained.
def _make_pdf(text: str) -> bytes:
    content_stream = f"BT\n/F1 12 Tf\n50 750 Td\n({text}) Tj\nET\n".encode("latin-1")
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(content_stream)).encode()
        + b" >>\nstream\n"
        + content_stream
        + b"endstream",
    ]
    body = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    offsets: list[int] = []
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{idx} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_offset = len(body)
    xref = b"xref\n0 6\n0000000000 65535 f \n"
    for off in offsets:
        xref += f"{off:010d} 00000 n \n".encode()
    xref += b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
    xref += f"{xref_offset}\n%%EOF".encode()
    return body + xref


GOLDEN_LLM_OUTPUT = json.dumps(
    {
        "company": "ACME",
        "title": "Senior Backend Engineer",
        "location": "Shanghai",
        "salary_min": 30000,
        "salary_max": 50000,
        "salary_period": "monthly",
        "job_level": "senior",
        "years_required": 5,
        "education": "本科",
        "hard_skills": [{"name": "python", "name_raw": "Python", "required": True, "weight": 1.0}],
        "soft_skills": [],
        "bonus_skills": [],
        "responsibilities": ["Lead the backend platform"],
        "description": "JD body",
        "confidence": 0.92,
    }
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _container() -> Iterator[str]:
    container = (
        PostgresContainer(image="pgvector/pgvector:pg16")
        .with_env("POSTGRES_USER", "jobcopilot")
        .with_env("POSTGRES_PASSWORD", "jobcopilot")
        .with_env("POSTGRES_DB", "jobcopilot")
    )
    container.start()
    try:
        sync_url = container.get_connection_url()
        async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
            "postgresql://", "postgresql+asyncpg://"
        )
        cfg = Config(str(ALEMBIC_INI))
        cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
        cfg.set_main_option("sqlalchemy.url", async_url)
        command.upgrade(cfg, "head")
        yield async_url
    finally:
        container.stop()


@pytest.fixture
async def engine(_container: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(_container, future=True)
    yield eng
    await eng.dispose()


@pytest.fixture
def sessionmaker_(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _truncate(engine: AsyncEngine) -> AsyncIterator[None]:
    yield
    async with engine.begin() as conn:
        await conn.execute(sa.text("TRUNCATE TABLE users RESTART IDENTITY CASCADE"))
        await conn.execute(sa.text("TRUNCATE TABLE jds RESTART IDENTITY CASCADE"))


@pytest.fixture
async def user_id(sessionmaker_: async_sessionmaker[AsyncSession]) -> int:
    async with sessionmaker_() as session, session.begin():
        u = User(email="t@example.com", name="t")
        session.add(u)
        await session.flush()
        return u.id


@pytest.fixture
async def other_user_id(sessionmaker_: async_sessionmaker[AsyncSession]) -> int:
    async with sessionmaker_() as session, session.begin():
        u = User(email="other@example.com", name="other")
        session.add(u)
        await session.flush()
        return u.id


def _prompt(prompt_id: int = 1) -> LoadedPrompt:
    return LoadedPrompt(
        id=prompt_id,
        agent_name="jd_parser",
        version="v1.0.0",
        system="you are a JD parser",
        user_template="parse: {{ jd_text }}",
        template_hash="x" * 64,
    )


def _client_with_response(content: str) -> BaseLLMClient:
    dummy = DummyProvider()
    dummy.queue(content=content, tokens_in=120, tokens_out=40)
    return BaseLLMClient(
        provider=dummy,
        logger=MemoryCallLogger(),
        retry_wait=wait_none(),
    )


def _client_with_error(*errs: BaseException) -> BaseLLMClient:
    dummy = DummyProvider()
    for e in errs:
        dummy.queue_error(e)
    return BaseLLMClient(
        provider=dummy,
        logger=MemoryCallLogger(),
        retry_wait=wait_none(),
    )


def _long_text() -> str:
    """JD-like text comfortably above the 50-char floor."""
    return (
        "Senior Backend Engineer @ ACME\n"
        "We are hiring an experienced engineer to lead our platform team. "
        "Required: Python, PostgreSQL, distributed systems, AWS or GCP.\n"
    )


# ---------------------------------------------------------------------------
# create_and_parse: text_paste happy path
# ---------------------------------------------------------------------------


async def test_create_and_parse_text_paste_writes_structured_and_parsed_status(
    sessionmaker_: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    llm = _client_with_response(GOLDEN_LLM_OUTPUT)

    jd, _ = await create_and_parse(
        sessionmaker_,
        user_id=user_id,
        source="text_paste",
        text=_long_text(),
        file_id=None,
        llm=llm,
        prompt=_prompt(prompt_id=42),
    )

    assert jd.status == "parsed"
    assert jd.title == "Senior Backend Engineer"
    assert jd.company == "ACME"
    assert jd.salary_min == 30000
    assert jd.salary_currency == "CNY"
    assert jd.parse_confidence == Decimal("0.920")
    assert jd.parse_model == "qwen3.6-flash"
    assert jd.parse_tokens == 160  # 120 in + 40 out
    assert jd.raw_text is not None
    assert "Senior Backend" in jd.raw_text
    assert jd.hard_skills == [
        {"name": "python", "name_raw": "Python", "required": True, "weight": 1.0}
    ]


# ---------------------------------------------------------------------------
# create_and_parse: pdf_upload happy path
# ---------------------------------------------------------------------------


async def test_create_and_parse_pdf_upload_extracts_and_persists_raw_file_id(
    sessionmaker_: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    pdf_bytes = _make_pdf(
        "Senior Backend Engineer at ACME hiring Python PostgreSQL Docker Kubernetes engineers"
    )

    async with sessionmaker_() as session, session.begin():
        f = File(
            user_id=user_id,
            name="jd.pdf",
            mime_type="application/pdf",
            size_bytes=len(pdf_bytes),
            sha256="a" * 64,
            content=pdf_bytes,
            purpose="jd_pdf",
        )
        session.add(f)
        await session.flush()
        file_id = f.id

    llm = _client_with_response(GOLDEN_LLM_OUTPUT)
    jd, _ = await create_and_parse(
        sessionmaker_,
        user_id=user_id,
        source="pdf_upload",
        text=None,
        file_id=file_id,
        llm=llm,
        prompt=_prompt(),
    )

    assert jd.status == "parsed"
    assert jd.raw_file_id == file_id
    assert jd.raw_text is not None
    assert "ACME" in jd.raw_text


# ---------------------------------------------------------------------------
# resolve_raw_text failures: NO row inserted
# ---------------------------------------------------------------------------


async def test_create_and_parse_text_too_short_raises_and_inserts_nothing(
    sessionmaker_: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    llm = _client_with_response(GOLDEN_LLM_OUTPUT)

    with pytest.raises(JDParseFailedError, match="过短"):
        await create_and_parse(
            sessionmaker_,
            user_id=user_id,
            source="text_paste",
            text="too short",  # < 50 chars
            file_id=None,
            llm=llm,
            prompt=_prompt(),
        )

    async with sessionmaker_() as session:
        count = (await session.execute(sa.select(sa.func.count()).select_from(Jd))).scalar_one()
        assert count == 0


async def test_create_and_parse_pdf_garbage_raises_and_inserts_nothing(
    sessionmaker_: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    async with sessionmaker_() as session, session.begin():
        f = File(
            user_id=user_id,
            name="bad.pdf",
            mime_type="application/pdf",
            size_bytes=10,
            sha256="b" * 64,
            content=b"not a real pdf",
            purpose="jd_pdf",
        )
        session.add(f)
        await session.flush()
        file_id = f.id

    llm = _client_with_response(GOLDEN_LLM_OUTPUT)

    # PdfExtractionError inherits JDParseFailedError's wire shape
    # (status 422 / code JD_PARSE_FAILED). Test catches the broader
    # JobCopilotError-derived class as a sanity check.
    from jobcopilot_api.infra.pdf import PdfExtractionError

    with pytest.raises(PdfExtractionError):
        await create_and_parse(
            sessionmaker_,
            user_id=user_id,
            source="pdf_upload",
            text=None,
            file_id=file_id,
            llm=llm,
            prompt=_prompt(),
        )

    async with sessionmaker_() as session:
        count = (await session.execute(sa.select(sa.func.count()).select_from(Jd))).scalar_one()
        assert count == 0


# ---------------------------------------------------------------------------
# Failure path: parse_failed status persists, raw_text survives
# ---------------------------------------------------------------------------


async def test_create_and_parse_llm_upstream_persists_failed_row(
    sessionmaker_: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    # 3 retries inside BaseLLMClient — queue 3 errors so we exhaust them.
    llm = _client_with_error(
        LLMUpstreamError("503", status_code=503),
        LLMUpstreamError("503", status_code=503),
        LLMUpstreamError("503", status_code=503),
    )

    with pytest.raises(LLMUpstreamError) as excinfo:
        await create_and_parse(
            sessionmaker_,
            user_id=user_id,
            source="text_paste",
            text=_long_text(),
            file_id=None,
            llm=llm,
            prompt=_prompt(),
        )
    # ADR-0006 D8 normalization: timeout / 5xx all surface as 502.
    assert excinfo.value.status_code == 502

    async with sessionmaker_() as session:
        rows = (await session.execute(sa.select(Jd))).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "parse_failed"
        assert rows[0].parse_confidence == Decimal("0")
        assert rows[0].raw_text is not None
        assert "Senior Backend" in rows[0].raw_text


async def test_create_and_parse_schema_invalid_persists_failed_row(
    sessionmaker_: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    # Two non-JSON responses: first triggers schema-retry, second exhausts.
    dummy = DummyProvider()
    dummy.queue(content="not json", tokens_in=10, tokens_out=2)
    dummy.queue(content="still not json", tokens_in=10, tokens_out=2)
    llm = BaseLLMClient(provider=dummy, logger=MemoryCallLogger(), retry_wait=wait_none())

    with pytest.raises(JDParseFailedError, match="schema"):
        await create_and_parse(
            sessionmaker_,
            user_id=user_id,
            source="text_paste",
            text=_long_text(),
            file_id=None,
            llm=llm,
            prompt=_prompt(),
        )

    async with sessionmaker_() as session:
        rows = (await session.execute(sa.select(Jd))).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "parse_failed"


async def test_create_and_parse_empty_title_persists_failed_row(
    sessionmaker_: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    # Schema-valid JSON but title is whitespace-only: ADR-0006 D8 row 3.
    bad = json.dumps(
        {
            "company": "ACME",
            "title": "   ",
            "confidence": 0.5,
            "hard_skills": [],
            "soft_skills": [],
            "bonus_skills": [],
            "responsibilities": [],
            "description": "x",
        }
    )
    llm = _client_with_response(bad)

    with pytest.raises(JDParseFailedError, match="title"):
        await create_and_parse(
            sessionmaker_,
            user_id=user_id,
            source="text_paste",
            text=_long_text(),
            file_id=None,
            llm=llm,
            prompt=_prompt(),
        )

    async with sessionmaker_() as session:
        rows = (await session.execute(sa.select(Jd))).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "parse_failed"


# ---------------------------------------------------------------------------
# get_jd
# ---------------------------------------------------------------------------


async def test_get_jd_happy(sessionmaker_: async_sessionmaker[AsyncSession], user_id: int) -> None:
    llm = _client_with_response(GOLDEN_LLM_OUTPUT)
    jd, _ = await create_and_parse(
        sessionmaker_,
        user_id=user_id,
        source="text_paste",
        text=_long_text(),
        file_id=None,
        llm=llm,
        prompt=_prompt(),
    )

    async with sessionmaker_() as session:
        out = await get_jd(session, user_id=user_id, jd_id=jd.id)
        assert out.title == "Senior Backend Engineer"


async def test_get_jd_404_for_other_user(
    sessionmaker_: async_sessionmaker[AsyncSession],
    user_id: int,
    other_user_id: int,
) -> None:
    from jobcopilot_api.errors import NotFoundError

    llm = _client_with_response(GOLDEN_LLM_OUTPUT)
    jd, _ = await create_and_parse(
        sessionmaker_,
        user_id=user_id,
        source="text_paste",
        text=_long_text(),
        file_id=None,
        llm=llm,
        prompt=_prompt(),
    )

    async with sessionmaker_() as session:
        with pytest.raises(NotFoundError):
            await get_jd(session, user_id=other_user_id, jd_id=jd.id)


async def test_get_jd_404_for_soft_deleted(
    sessionmaker_: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    from jobcopilot_api.errors import NotFoundError

    llm = _client_with_response(GOLDEN_LLM_OUTPUT)
    jd, _ = await create_and_parse(
        sessionmaker_,
        user_id=user_id,
        source="text_paste",
        text=_long_text(),
        file_id=None,
        llm=llm,
        prompt=_prompt(),
    )

    async with sessionmaker_() as session, session.begin():
        await soft_delete_jd(session, user_id=user_id, jd_id=jd.id)

    async with sessionmaker_() as session:
        with pytest.raises(NotFoundError):
            await get_jd(session, user_id=user_id, jd_id=jd.id)


# ---------------------------------------------------------------------------
# list_jds
# ---------------------------------------------------------------------------


async def test_list_jds_filters_by_status_and_user(
    sessionmaker_: async_sessionmaker[AsyncSession],
    user_id: int,
    other_user_id: int,
) -> None:
    llm = _client_with_response(GOLDEN_LLM_OUTPUT)

    # 2 jds for user_id, 1 for other_user.
    for _ in range(2):
        await create_and_parse(
            sessionmaker_,
            user_id=user_id,
            source="text_paste",
            text=_long_text(),
            file_id=None,
            llm=_client_with_response(GOLDEN_LLM_OUTPUT),
            prompt=_prompt(),
        )
    await create_and_parse(
        sessionmaker_,
        user_id=other_user_id,
        source="text_paste",
        text=_long_text(),
        file_id=None,
        llm=llm,
        prompt=_prompt(),
    )

    async with sessionmaker_() as session:
        rows, has_more = await list_jds(session, user_id=user_id)
        assert len(rows) == 2
        assert has_more is False
        assert all(r.user_id == user_id for r in rows)


async def test_list_jds_pagination_has_more(
    sessionmaker_: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    for _ in range(3):
        await create_and_parse(
            sessionmaker_,
            user_id=user_id,
            source="text_paste",
            text=_long_text(),
            file_id=None,
            llm=_client_with_response(GOLDEN_LLM_OUTPUT),
            prompt=_prompt(),
        )

    async with sessionmaker_() as session:
        rows, has_more = await list_jds(session, user_id=user_id, limit=2)
        assert len(rows) == 2
        assert has_more is True


async def test_list_jds_excludes_soft_deleted(
    sessionmaker_: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    jd_a, _ = await create_and_parse(
        sessionmaker_,
        user_id=user_id,
        source="text_paste",
        text=_long_text(),
        file_id=None,
        llm=_client_with_response(GOLDEN_LLM_OUTPUT),
        prompt=_prompt(),
    )
    await create_and_parse(
        sessionmaker_,
        user_id=user_id,
        source="text_paste",
        text=_long_text(),
        file_id=None,
        llm=_client_with_response(GOLDEN_LLM_OUTPUT),
        prompt=_prompt(),
    )
    async with sessionmaker_() as session, session.begin():
        await soft_delete_jd(session, user_id=user_id, jd_id=jd_a.id)

    async with sessionmaker_() as session:
        rows, _ = await list_jds(session, user_id=user_id)
        assert len(rows) == 1
        assert rows[0].id != jd_a.id


# ---------------------------------------------------------------------------
# patch_jd
# ---------------------------------------------------------------------------


async def test_patch_jd_structured_overrides_fields(
    sessionmaker_: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    jd, _ = await create_and_parse(
        sessionmaker_,
        user_id=user_id,
        source="text_paste",
        text=_long_text(),
        file_id=None,
        llm=_client_with_response(GOLDEN_LLM_OUTPUT),
        prompt=_prompt(),
    )

    edited = JDStructured(
        title="Staff Engineer",
        company="ACME",
        confidence=0.95,
    )
    async with sessionmaker_() as session, session.begin():
        out = await patch_jd(
            session,
            user_id=user_id,
            jd_id=jd.id,
            patch=JDPatchInput(structured=edited),
        )
        assert out.title == "Staff Engineer"
        # Cost ledger frozen across patch (ADR-0006 D6 chain).
        assert out.parse_tokens == 160


async def test_patch_jd_status_only(
    sessionmaker_: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    jd, _ = await create_and_parse(
        sessionmaker_,
        user_id=user_id,
        source="text_paste",
        text=_long_text(),
        file_id=None,
        llm=_client_with_response(GOLDEN_LLM_OUTPUT),
        prompt=_prompt(),
    )

    async with sessionmaker_() as session, session.begin():
        out = await patch_jd(
            session,
            user_id=user_id,
            jd_id=jd.id,
            patch=JDPatchInput(status="parsing"),
        )
        assert out.status == "parsing"
        # Title preserved when only status patched.
        assert out.title == "Senior Backend Engineer"


# ---------------------------------------------------------------------------
# soft_delete_jd
# ---------------------------------------------------------------------------


async def test_soft_delete_jd_marks_deleted_at(
    sessionmaker_: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    jd, _ = await create_and_parse(
        sessionmaker_,
        user_id=user_id,
        source="text_paste",
        text=_long_text(),
        file_id=None,
        llm=_client_with_response(GOLDEN_LLM_OUTPUT),
        prompt=_prompt(),
    )

    async with sessionmaker_() as session, session.begin():
        await soft_delete_jd(session, user_id=user_id, jd_id=jd.id)

    async with sessionmaker_() as session:
        row = (await session.execute(sa.select(Jd).where(Jd.id == jd.id))).scalar_one()
        assert row.deleted_at is not None


async def test_soft_delete_jd_404_when_already_deleted(
    sessionmaker_: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    from jobcopilot_api.errors import NotFoundError

    jd, _ = await create_and_parse(
        sessionmaker_,
        user_id=user_id,
        source="text_paste",
        text=_long_text(),
        file_id=None,
        llm=_client_with_response(GOLDEN_LLM_OUTPUT),
        prompt=_prompt(),
    )

    async with sessionmaker_() as session, session.begin():
        await soft_delete_jd(session, user_id=user_id, jd_id=jd.id)
    async with sessionmaker_() as session, session.begin():
        with pytest.raises(NotFoundError):
            await soft_delete_jd(session, user_id=user_id, jd_id=jd.id)
