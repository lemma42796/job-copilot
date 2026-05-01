"""Integration tests: services/file_service.py end-to-end against pgvector/pg16.

Covers the orchestration layer of ADR-0005:
  - happy-path upload returns a row + replayed=False
  - duplicate (user_id, sha256) → existing row + replayed=True (D5)
  - quota exceeded → 413 before INSERT (D8)
  - get_for_download eager-loads `content` (D7) and 404s on cross-user
    or soft-deleted access (D9 / D10)
  - soft_delete is idempotent-ish: second attempt 404s

Tagged `integration`; one Postgres container per module.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Iterator
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
from testcontainers.postgres import PostgresContainer

from jobcopilot_api.errors import NotFoundError
from jobcopilot_api.infra.upload import (
    USER_QUOTA_BYTES,
    QuotaExceededError,
)
from jobcopilot_api.models import File, User
from jobcopilot_api.schemas.files import FilePurpose
from jobcopilot_api.services.file_service import (
    get_file_for_download,
    soft_delete_file,
    upload_file,
)

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

# Padded so quota math has comfortable headroom; size_bytes will be ~1KB.
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3 minimal pdf body\n" + b"x" * 1024
PDF_ALT = b"%PDF-1.7\nALT minimal pdf body\n" + b"y" * 1024


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


async def _chunks(data: bytes, size: int = 4096) -> AsyncIterable[bytes]:
    for i in range(0, len(data), size):
        yield data[i : i + size]


async def _make_user(session: AsyncSession, *, email: str) -> User:
    user = User(email=email, name="t", locale="zh-CN")
    session.add(user)
    await session.flush()
    return user


# ---------- upload_file ----------


@pytest.mark.integration
async def test_upload_happy_returns_new_row(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker_() as session, session.begin():
        user = await _make_user(session, email="happy@test")
        row, replayed = await upload_file(
            session=session,
            user_id=user.id,
            purpose=FilePurpose.JD_PDF,
            filename="jd.pdf",
            mime_type="application/pdf",
            reader=_chunks(PDF),
        )
        assert replayed is False
        assert row.size_bytes == len(PDF)
        assert row.purpose == "jd_pdf"
        assert len(row.sha256) == 64


@pytest.mark.integration
async def test_upload_dedup_replay_returns_existing_row(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    """ADR-0005 D5: same user uploading the same bytes returns the
    existing file id with replayed=True instead of inserting a new row."""
    async with sessionmaker_() as session, session.begin():
        user = await _make_user(session, email="dup@test")
        first, _ = await upload_file(
            session=session,
            user_id=user.id,
            purpose=FilePurpose.JD_PDF,
            filename="jd.pdf",
            mime_type="application/pdf",
            reader=_chunks(PDF),
        )
        first_id = first.id

    async with sessionmaker_() as session, session.begin():
        user = (await session.execute(sa.select(User).where(User.email == "dup@test"))).scalar_one()
        second, replayed = await upload_file(
            session=session,
            user_id=user.id,
            purpose=FilePurpose.JD_PDF,
            filename="jd-renamed.pdf",  # different filename, same bytes
            mime_type="application/pdf",
            reader=_chunks(PDF),
        )
        assert replayed is True
        assert second.id == first_id
        # Original filename preserved — dedup doesn't update existing metadata.
        assert second.name == "jd.pdf"


@pytest.mark.integration
async def test_upload_quota_exceeded_raises_413(
    sessionmaker_: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
) -> None:
    """Pre-fill the user with two near-100MB rows summing to (quota - 100
    bytes), then a tiny real upload should still trip the quota.

    Two rows because the DB has `ck_files_size` at 100MB per row; we
    bypass `upload_file` for setup so we don't need a real 200MB blob
    in memory. Different sha256 per row so the partial unique index is
    not what trips the failure.
    """
    async with sessionmaker_() as session, session.begin():
        user = await _make_user(session, email="quota@test")
        await session.flush()
        user_id = user.id

    # 2 * (~100MB - 50) sums to (USER_QUOTA - 100); each row stays under
    # the 100MB row-level CHECK.
    per_row = (USER_QUOTA_BYTES - 100) // 2
    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO files "
                "(user_id, name, mime_type, size_bytes, sha256, content, purpose) VALUES "
                "(:uid, 'a.pdf', 'application/pdf', :sz, :sha_a, :body, 'jd_pdf'), "
                "(:uid, 'b.pdf', 'application/pdf', :sz, :sha_b, :body, 'jd_pdf')"
            ),
            {
                "uid": user_id,
                "sz": per_row,
                "sha_a": "a" * 64,
                "sha_b": "b" * 64,
                "body": b"%PDF-stub",
            },
        )

    # PDF body is well over 100 bytes, so the new upload pushes past quota.
    with pytest.raises(QuotaExceededError) as ei:
        async with sessionmaker_() as session, session.begin():
            await upload_file(
                session=session,
                user_id=user_id,
                purpose=FilePurpose.JD_PDF,
                filename="next.pdf",
                mime_type="application/pdf",
                reader=_chunks(PDF),
            )
    assert ei.value.status_code == 413
    assert ei.value.code == "QUOTA_EXCEEDED"


# ---------- get_file_for_download ----------


@pytest.mark.integration
async def test_get_file_for_download_returns_content(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker_() as session, session.begin():
        user = await _make_user(session, email="get@test")
        uploaded, _ = await upload_file(
            session=session,
            user_id=user.id,
            purpose=FilePurpose.JD_PDF,
            filename="g.pdf",
            mime_type="application/pdf",
            reader=_chunks(PDF),
        )
        await session.flush()
        file_id = uploaded.id
        user_id = user.id

    async with sessionmaker_() as session:
        loaded = await get_file_for_download(session=session, user_id=user_id, file_id=file_id)
        assert loaded.content == PDF
        assert loaded.name == "g.pdf"


@pytest.mark.integration
async def test_get_file_for_download_404_when_other_user(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    """Cross-user access must look indistinguishable from 'no such file'
    (no existence leak — ADR-0005 D10)."""
    async with sessionmaker_() as session, session.begin():
        alice = await _make_user(session, email="alice3@test")
        bob = await _make_user(session, email="bob3@test")
        uploaded, _ = await upload_file(
            session=session,
            user_id=alice.id,
            purpose=FilePurpose.JD_PDF,
            filename="alice.pdf",
            mime_type="application/pdf",
            reader=_chunks(PDF),
        )
        await session.flush()
        file_id = uploaded.id
        bob_id = bob.id

    async with sessionmaker_() as session:
        with pytest.raises(NotFoundError):
            await get_file_for_download(session=session, user_id=bob_id, file_id=file_id)


@pytest.mark.integration
async def test_get_file_for_download_404_when_soft_deleted(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker_() as session, session.begin():
        user = await _make_user(session, email="del-get@test")
        uploaded, _ = await upload_file(
            session=session,
            user_id=user.id,
            purpose=FilePurpose.JD_PDF,
            filename="d.pdf",
            mime_type="application/pdf",
            reader=_chunks(PDF),
        )
        await session.flush()
        file_id = uploaded.id
        user_id = user.id

    async with sessionmaker_() as session, session.begin():
        await soft_delete_file(session=session, user_id=user_id, file_id=file_id)

    async with sessionmaker_() as session:
        with pytest.raises(NotFoundError):
            await get_file_for_download(session=session, user_id=user_id, file_id=file_id)


# ---------- soft_delete_file ----------


@pytest.mark.integration
async def test_soft_delete_marks_row_then_get_404(
    sessionmaker_: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
) -> None:
    async with sessionmaker_() as session, session.begin():
        user = await _make_user(session, email="del@test")
        uploaded, _ = await upload_file(
            session=session,
            user_id=user.id,
            purpose=FilePurpose.JD_PDF,
            filename="d.pdf",
            mime_type="application/pdf",
            reader=_chunks(PDF),
        )
        await session.flush()
        file_id = uploaded.id
        user_id = user.id

    async with sessionmaker_() as session, session.begin():
        await soft_delete_file(session=session, user_id=user_id, file_id=file_id)

    async with engine.connect() as conn:
        cur = await conn.execute(
            sa.text("SELECT deleted_at FROM files WHERE id = :id"),
            {"id": file_id},
        )
        deleted_at = cur.scalar_one()
        assert deleted_at is not None


@pytest.mark.integration
async def test_soft_delete_404_when_already_deleted(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    """Idempotency we explicitly chose not to give: a second delete on
    the same row 404s. Routers can map this to a no-op if needed."""
    async with sessionmaker_() as session, session.begin():
        user = await _make_user(session, email="del2@test")
        uploaded, _ = await upload_file(
            session=session,
            user_id=user.id,
            purpose=FilePurpose.JD_PDF,
            filename="x.pdf",
            mime_type="application/pdf",
            reader=_chunks(PDF),
        )
        await session.flush()
        file_id = uploaded.id
        user_id = user.id

    async with sessionmaker_() as session, session.begin():
        await soft_delete_file(session=session, user_id=user_id, file_id=file_id)

    async with sessionmaker_() as session, session.begin():
        with pytest.raises(NotFoundError):
            await soft_delete_file(session=session, user_id=user_id, file_id=file_id)


@pytest.mark.integration
async def test_soft_delete_then_re_upload_same_bytes_works(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    """Bridges D5 + D9: after soft-delete, the partial unique index
    no longer blocks re-uploading the same bytes, and the re-upload is
    a fresh row (replayed=False)."""
    async with sessionmaker_() as session, session.begin():
        user = await _make_user(session, email="reup@test")
        first, _ = await upload_file(
            session=session,
            user_id=user.id,
            purpose=FilePurpose.JD_PDF,
            filename="r.pdf",
            mime_type="application/pdf",
            reader=_chunks(PDF),
        )
        await session.flush()
        first_id = first.id
        user_id = user.id

    async with sessionmaker_() as session, session.begin():
        await soft_delete_file(session=session, user_id=user_id, file_id=first_id)

    async with sessionmaker_() as session, session.begin():
        second, replayed = await upload_file(
            session=session,
            user_id=user_id,
            purpose=FilePurpose.JD_PDF,
            filename="r2.pdf",
            mime_type="application/pdf",
            reader=_chunks(PDF),
        )
        assert replayed is False
        assert second.id != first_id


@pytest.mark.integration
async def test_upload_propagates_alt_content_as_distinct_row(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    """Different bytes from the same user → two rows, no dedup."""
    async with sessionmaker_() as session, session.begin():
        user = await _make_user(session, email="distinct@test")
        a, _ = await upload_file(
            session=session,
            user_id=user.id,
            purpose=FilePurpose.JD_PDF,
            filename="a.pdf",
            mime_type="application/pdf",
            reader=_chunks(PDF),
        )
        b, replayed_b = await upload_file(
            session=session,
            user_id=user.id,
            purpose=FilePurpose.JD_PDF,
            filename="b.pdf",
            mime_type="application/pdf",
            reader=_chunks(PDF_ALT),
        )
        assert replayed_b is False
        assert a.id != b.id
        assert a.sha256 != b.sha256


@pytest.mark.integration
async def test_upload_rejects_oversize_before_db_insert(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    """size 413 must fire before any DB row is created."""
    from jobcopilot_api.infra.upload import MAX_UPLOAD_BYTES, FileTooLargeError

    big = b"%PDF-1.7\n" + b"x" * (MAX_UPLOAD_BYTES + 1)
    async with sessionmaker_() as session, session.begin():
        user = await _make_user(session, email="big@test")
        with pytest.raises(FileTooLargeError):
            await upload_file(
                session=session,
                user_id=user.id,
                purpose=FilePurpose.JD_PDF,
                filename="huge.pdf",
                mime_type="application/pdf",
                reader=_chunks(big),
            )

    async with sessionmaker_() as session:
        cur = await session.execute(sa.select(sa.func.count()).select_from(File))
        assert cur.scalar_one() == 0
