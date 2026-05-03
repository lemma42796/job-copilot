"""Integration tests: `ProfileChunk` ORM round-trip + FK CASCADE + metadata alias.

Verifies:
  1. Insert + read-back of all 4 columns the chunk pipeline writes
     (`granularity` enum / `content` / `embedding` Vector(1024) /
     `chunk_metadata` JSONB → DB column `metadata` / `embed_model` /
     `embed_version`).
  2. FK CASCADE: deleting a parent `Profile` purges its chunks (the
     migration declared `ondelete="CASCADE"`).
  3. `chunk_granularity` enum rejects values outside the 4 allowed.
  4. `metadata` JSONB defaults to `{}` when not specified.

Tagged `integration`; one Postgres container per module (mirrors
test_files_orm.py).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer

from jobcopilot_api.models import Profile, ProfileChunk, User

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


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
        # CASCADE: users → profiles → profile_chunks all clear via FK ondelete cascade.
        await conn.execute(sa.text("TRUNCATE TABLE users RESTART IDENTITY CASCADE"))


async def _make_profile(session: AsyncSession, *, email: str = "t@x") -> Profile:
    user = User(email=email, name="t", locale="zh-CN")
    session.add(user)
    await session.flush()
    profile = Profile(user_id=user.id, source="text_paste", status="parsed")
    session.add(profile)
    await session.flush()
    return profile


def _vec(seed: float) -> list[float]:
    """Build a 1024-dim vector with a deterministic per-element value."""
    return [seed + i * 1e-6 for i in range(1024)]


@pytest.mark.integration
async def test_chunk_round_trip_all_columns(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker_() as session, session.begin():
        profile = await _make_profile(session)
        session.add(
            ProfileChunk(
                profile_id=profile.id,
                granularity="experience",
                source_table="profile_experiences",
                source_id=42,
                content="公司:ACME\n职位:SWE",
                embedding=_vec(0.1),
                chunk_metadata={"chunker_version": "v1", "extra": "x"},
                embed_model="text-embedding-v4",
                embed_version="v1",
            )
        )

    async with sessionmaker_() as session:
        loaded = (await session.execute(sa.select(ProfileChunk))).scalar_one()
        assert loaded.granularity == "experience"
        assert loaded.source_table == "profile_experiences"
        assert loaded.source_id == 42
        assert loaded.content == "公司:ACME\n职位:SWE"
        assert loaded.chunk_metadata == {"chunker_version": "v1", "extra": "x"}
        assert loaded.embed_model == "text-embedding-v4"
        assert loaded.embed_version == "v1"
        # pgvector returns numpy.ndarray; just verify the dimension + first element.
        assert loaded.embedding is not None
        assert len(list(loaded.embedding)) == 1024
        assert float(loaded.embedding[0]) == pytest.approx(0.1)


@pytest.mark.integration
async def test_chunk_metadata_jsonb_defaults_to_empty_object(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker_() as session, session.begin():
        profile = await _make_profile(session)
        session.add(
            ProfileChunk(
                profile_id=profile.id,
                granularity="summary",
                source_table="profiles",
                source_id=profile.id,
                content="summary text",
            )
        )

    async with sessionmaker_() as session:
        chunk = (await session.execute(sa.select(ProfileChunk))).scalar_one()
        assert chunk.chunk_metadata == {}
        assert chunk.embedding is None  # nullable; populated later by the embedder


@pytest.mark.integration
async def test_chunk_fk_cascade_removes_chunks_when_profile_deleted(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker_() as session, session.begin():
        profile = await _make_profile(session)
        for i in range(3):
            session.add(
                ProfileChunk(
                    profile_id=profile.id,
                    granularity="skill",
                    source_table="profile_skills",
                    source_id=i,
                    content=f"skill {i}",
                )
            )

    async with sessionmaker_() as session, session.begin():
        await session.execute(sa.delete(Profile).where(Profile.id == profile.id))

    async with sessionmaker_() as session:
        count = (
            await session.execute(sa.select(sa.func.count()).select_from(ProfileChunk))
        ).scalar_one()
        assert count == 0


@pytest.mark.integration
async def test_chunk_granularity_enum_rejects_unknown_values(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    """`chunk_granularity` PG ENUM only accepts the 4 declared values."""
    async with sessionmaker_() as session:
        async with session.begin():
            profile = await _make_profile(session)
        with pytest.raises((IntegrityError, DBAPIError)):
            async with session.begin():
                session.add(
                    ProfileChunk(
                        profile_id=profile.id,
                        granularity="education",  # not in the 4-value enum
                        source_table="profile_educations",
                        source_id=1,
                        content="x",
                    )
                )


@pytest.mark.integration
async def test_chunk_fk_orphan_blocked_when_profile_missing(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker_() as session:
        with pytest.raises(IntegrityError):
            async with session.begin():
                session.add(
                    ProfileChunk(
                        profile_id=999_999,
                        granularity="summary",
                        source_table="profiles",
                        source_id=999_999,
                        content="orphan",
                    )
                )


@pytest.mark.integration
async def test_chunk_metadata_python_attr_aliases_db_metadata_column(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    """Verify Python `chunk_metadata` ↔ DB `metadata` mapping by raw SQL probe."""
    async with sessionmaker_() as session, session.begin():
        profile = await _make_profile(session)
        session.add(
            ProfileChunk(
                profile_id=profile.id,
                granularity="project",
                source_table="profile_projects",
                source_id=1,
                content="x",
                chunk_metadata={"flag": True},
            )
        )

    async with sessionmaker_() as session:
        row = (
            await session.execute(
                sa.text("SELECT metadata FROM profile_chunks WHERE profile_id = :pid"),
                {"pid": profile.id},
            )
        ).one()
        assert row[0] == {"flag": True}
