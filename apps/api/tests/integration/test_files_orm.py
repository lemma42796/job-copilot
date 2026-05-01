"""Integration tests: User/File ORM round-trip + 0007 unique index behavior.

Covers the four load-bearing assertions of ADR-0005 D5:

  1. User → File round-trip via the `User.files` relationship.
  2. Same (user_id, sha256) inserted twice raises IntegrityError
     (this is what the upload service catches to return `replayed=true`).
  3. Soft-deleting a file releases the partial unique index, so the
     same user can re-upload the same bytes.
  4. Cross-user same-sha256 is allowed (each user owns their row +
     their own quota; ADR-0005 D5 explicitly chose against cross-user
     bytes sharing).

Tagged `integration`; spins one Postgres container per module.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import selectinload
from testcontainers.postgres import PostgresContainer

from jobcopilot_api.models import File, User

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

SHA_A = "a" * 64
SHA_B = "b" * 64


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
    """Function-scope: pytest-asyncio gives each test a fresh event loop
    and asyncpg connections cannot cross loops (S2-E lesson).
    """
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
        # CASCADE drops files (user FK cascade) and any future child rows.
        await conn.execute(sa.text("TRUNCATE TABLE users RESTART IDENTITY CASCADE"))


async def _make_user(session: AsyncSession, *, email: str) -> User:
    user = User(email=email, name="t", locale="zh-CN")
    session.add(user)
    await session.flush()
    return user


def _make_file(*, user_id: int, sha256: str, name: str = "x.pdf", purpose: str = "jd_pdf") -> File:
    return File(
        user_id=user_id,
        name=name,
        mime_type="application/pdf",
        size_bytes=4,
        sha256=sha256,
        content=b"%PDF",
        purpose=purpose,
    )


@pytest.mark.integration
async def test_user_file_round_trip(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker_() as session, session.begin():
        user = await _make_user(session, email="alice@test")
        session.add(_make_file(user_id=user.id, sha256=SHA_A, name="r.pdf"))

    async with sessionmaker_() as session:
        loaded = (
            await session.execute(
                sa.select(User).where(User.email == "alice@test").options(selectinload(User.files))
            )
        ).scalar_one()
        assert loaded.locale == "zh-CN"
        assert loaded.settings == {}
        assert len(loaded.files) == 1
        assert loaded.files[0].name == "r.pdf"
        assert loaded.files[0].sha256 == SHA_A
        # `content` is deferred — not eagerly loaded with the metadata.
        assert "content" not in loaded.files[0].__dict__


async def _insert_dup_file(
    sessionmaker_: async_sessionmaker[AsyncSession],
    *,
    email: str,
    sha256: str,
) -> None:
    async with sessionmaker_() as session, session.begin():
        user = (await session.execute(sa.select(User).where(User.email == email))).scalar_one()
        session.add(_make_file(user_id=user.id, sha256=sha256, name="other.pdf"))


@pytest.mark.integration
async def test_same_user_same_sha256_blocked(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker_() as session, session.begin():
        user = await _make_user(session, email="dup@test")
        session.add(_make_file(user_id=user.id, sha256=SHA_A))

    with pytest.raises(IntegrityError):
        await _insert_dup_file(sessionmaker_, email="dup@test", sha256=SHA_A)


@pytest.mark.integration
async def test_soft_delete_releases_unique_index(
    sessionmaker_: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
) -> None:
    """ADR-0005 D9: partial index excludes deleted rows, so the same
    user can re-upload the same bytes after soft-deleting the previous
    upload."""
    async with sessionmaker_() as session, session.begin():
        user = await _make_user(session, email="soft@test")
        session.add(_make_file(user_id=user.id, sha256=SHA_A))

    # Soft-delete via raw UPDATE (the service-layer helper lands in S3-B).
    async with engine.begin() as conn:
        await conn.execute(
            sa.text("UPDATE files SET deleted_at = NOW() WHERE sha256 = :sha"),
            {"sha": SHA_A},
        )

    # Re-uploading the same bytes is now allowed.
    async with sessionmaker_() as session, session.begin():
        user = (
            await session.execute(sa.select(User).where(User.email == "soft@test"))
        ).scalar_one()
        session.add(_make_file(user_id=user.id, sha256=SHA_A, name="resurrected.pdf"))

    async with engine.connect() as conn:
        cur = await conn.execute(
            sa.text("SELECT count(*) FROM files WHERE sha256 = :sha"), {"sha": SHA_A}
        )
        assert cur.scalar_one() == 2


@pytest.mark.integration
async def test_cross_user_same_sha256_allowed(
    sessionmaker_: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
) -> None:
    async with sessionmaker_() as session, session.begin():
        alice = await _make_user(session, email="alice2@test")
        bob = await _make_user(session, email="bob@test")
        session.add(_make_file(user_id=alice.id, sha256=SHA_B))
        session.add(_make_file(user_id=bob.id, sha256=SHA_B))

    async with engine.connect() as conn:
        cur = await conn.execute(
            sa.text("SELECT count(*) FROM files WHERE sha256 = :sha"), {"sha": SHA_B}
        )
        assert cur.scalar_one() == 2
