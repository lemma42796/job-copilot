"""Integration tests for `services.chunk_service.rebuild_for_profile` (S8 C4).

Covers:
  1. Happy path — write 5 chunks (1 summary + 1 exp + 1 proj + 2 skills)
     with deterministic Dummy vectors + correct metadata/embed_model.
  2. Idempotent re-run — second call wipes the previous rows; count
     stays at 5, no UNIQUE explosion (no UNIQUE on profile_chunks; this
     guards against accidental dup INSERT growth).
  3. Empty profile — no summary/exp/proj/skill writes 0 chunks but does
     not raise; an existing pre-populated chunk is wiped.
  4. Cross-user — wrong `user_id` raises NotFoundError before any DB
     mutation.
  5. Embed failure isolation — embedder raising leaves prior chunks
     intact (DELETE is in the same tx as the INSERT, never executed
     until embed succeeds).
  6. Batching — > EMBED_BATCH_LIMIT chunks are split into multiple
     embed() calls; tokens accumulate.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
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
from jobcopilot_api.llm.embedders import (
    EMBED_BATCH_LIMIT,
    DummyEmbedder,
    EmbeddingResult,
)
from jobcopilot_api.models import (
    Profile,
    ProfileChunk,
    ProfileExperience,
    ProfileProject,
    ProfileSkill,
    User,
)
from jobcopilot_api.services.chunk_service import EMBED_VERSION, rebuild_for_profile

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
        await conn.execute(sa.text("TRUNCATE TABLE users RESTART IDENTITY CASCADE"))


async def _seed_profile(
    sm: async_sessionmaker[AsyncSession],
    *,
    email: str = "t@x",
    with_summary: bool = True,
    n_experiences: int = 1,
    n_projects: int = 1,
    n_skills: int = 2,
) -> tuple[int, int]:
    """Create a user + profile + child rows. Returns `(user_id, profile_id)`."""
    async with sm() as session, session.begin():
        user = User(email=email, name="t")
        session.add(user)
        await session.flush()

        profile = Profile(
            user_id=user.id,
            source="text_paste",
            status="parsed",
            summary="资深后端" if with_summary else None,
        )
        session.add(profile)
        await session.flush()

        for i in range(n_experiences):
            session.add(
                ProfileExperience(
                    profile_id=profile.id,
                    company=f"ACME-{i}",
                    title="高级后端工程师",
                    description="主导平台架构",
                    bullets=["重构订单系统"],
                    tech_stack=["python"],
                    achievements=["QPS 5x"],
                )
            )
        for i in range(n_projects):
            session.add(
                ProfileProject(
                    profile_id=profile.id,
                    name=f"调度系统-{i}",
                    role="主程",
                    description="celery 平台",
                    bullets=["100w/日"],
                    tech_stack=["celery"],
                    achievements=["100w 调度量"],
                )
            )
        for i in range(n_skills):
            session.add(
                ProfileSkill(
                    profile_id=profile.id,
                    name=f"skill_{i}",
                    category="language",
                    level="advanced",
                )
            )
        return user.id, profile.id


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


async def test_rebuild_writes_all_5_chunks_with_metadata_and_embed_columns(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    user_id, profile_id = await _seed_profile(sessionmaker_)
    embedder = DummyEmbedder()

    result = await rebuild_for_profile(
        sessionmaker_,
        user_id=user_id,
        profile_id=profile_id,
        embedder=embedder,
    )
    assert result.chunks_written == 5  # 1 summary + 1 exp + 1 proj + 2 skills
    assert result.embed_model == embedder.model
    assert result.tokens_in > 0  # Dummy returns sum(len(text))

    async with sessionmaker_() as session:
        rows = (
            (
                await session.execute(
                    sa.select(ProfileChunk)
                    .where(ProfileChunk.profile_id == profile_id)
                    .order_by(ProfileChunk.id)
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 5
    assert {r.granularity for r in rows} == {"summary", "experience", "project", "skill"}
    for r in rows:
        assert r.embed_model == embedder.model
        assert r.embed_version == EMBED_VERSION
        assert r.chunk_metadata == {"chunker_version": "v1"}
        assert r.embedding is not None
        assert len(list(r.embedding)) == 1024


# ---------------------------------------------------------------------------
# 2. Idempotent re-run (DELETE+INSERT)
# ---------------------------------------------------------------------------


async def test_rebuild_twice_does_not_grow_row_count(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    user_id, profile_id = await _seed_profile(sessionmaker_)
    embedder = DummyEmbedder()

    await rebuild_for_profile(
        sessionmaker_, user_id=user_id, profile_id=profile_id, embedder=embedder
    )
    await rebuild_for_profile(
        sessionmaker_, user_id=user_id, profile_id=profile_id, embedder=embedder
    )

    async with sessionmaker_() as session:
        count = (
            await session.execute(
                sa.select(sa.func.count())
                .select_from(ProfileChunk)
                .where(ProfileChunk.profile_id == profile_id)
            )
        ).scalar_one()
    assert count == 5


# ---------------------------------------------------------------------------
# 3. Empty profile (no summary, no children)
# ---------------------------------------------------------------------------


async def test_rebuild_empty_profile_writes_zero_chunks_and_wipes_old(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    user_id, profile_id = await _seed_profile(sessionmaker_)
    # First rebuild: 5 chunks land.
    await rebuild_for_profile(
        sessionmaker_, user_id=user_id, profile_id=profile_id, embedder=DummyEmbedder()
    )
    # Strip the profile down to nothing.
    async with sessionmaker_() as session, session.begin():
        await session.execute(
            sa.delete(ProfileExperience).where(ProfileExperience.profile_id == profile_id)
        )
        await session.execute(
            sa.delete(ProfileProject).where(ProfileProject.profile_id == profile_id)
        )
        await session.execute(sa.delete(ProfileSkill).where(ProfileSkill.profile_id == profile_id))
        await session.execute(
            sa.update(Profile).where(Profile.id == profile_id).values(summary=None)
        )

    result = await rebuild_for_profile(
        sessionmaker_, user_id=user_id, profile_id=profile_id, embedder=DummyEmbedder()
    )
    assert result.chunks_written == 0
    assert result.tokens_in == 0

    async with sessionmaker_() as session:
        count = (
            await session.execute(
                sa.select(sa.func.count())
                .select_from(ProfileChunk)
                .where(ProfileChunk.profile_id == profile_id)
            )
        ).scalar_one()
    assert count == 0


# ---------------------------------------------------------------------------
# 4. Cross-user 404 (no DB mutation)
# ---------------------------------------------------------------------------


async def test_rebuild_wrong_user_raises_not_found_and_no_writes(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    user_id, profile_id = await _seed_profile(sessionmaker_)
    # Pre-populate so we can prove the bad call did NOT touch chunks.
    await rebuild_for_profile(
        sessionmaker_, user_id=user_id, profile_id=profile_id, embedder=DummyEmbedder()
    )

    async with sessionmaker_() as session, session.begin():
        intruder = User(email="other@x", name="other")
        session.add(intruder)
        await session.flush()
        intruder_id = intruder.id

    with pytest.raises(NotFoundError):
        await rebuild_for_profile(
            sessionmaker_,
            user_id=intruder_id,
            profile_id=profile_id,
            embedder=DummyEmbedder(),
        )

    async with sessionmaker_() as session:
        count = (
            await session.execute(
                sa.select(sa.func.count())
                .select_from(ProfileChunk)
                .where(ProfileChunk.profile_id == profile_id)
            )
        ).scalar_one()
    assert count == 5  # untouched


# ---------------------------------------------------------------------------
# 5. Embed failure leaves prior chunks intact
# ---------------------------------------------------------------------------


class _BoomEmbedder:
    model = "boom-v0"

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        del texts
        raise RuntimeError("embed exploded")


async def test_rebuild_embed_failure_does_not_delete_existing_chunks(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    user_id, profile_id = await _seed_profile(sessionmaker_)
    await rebuild_for_profile(
        sessionmaker_, user_id=user_id, profile_id=profile_id, embedder=DummyEmbedder()
    )

    with pytest.raises(RuntimeError, match="embed exploded"):
        await rebuild_for_profile(
            sessionmaker_,
            user_id=user_id,
            profile_id=profile_id,
            embedder=_BoomEmbedder(),
        )

    async with sessionmaker_() as session:
        rows = (
            (
                await session.execute(
                    sa.select(ProfileChunk).where(ProfileChunk.profile_id == profile_id)
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 5
    # Old rows kept their original embed_model (DummyEmbedder), not "boom-v0".
    assert {r.embed_model for r in rows} == {DummyEmbedder().model}


# ---------------------------------------------------------------------------
# 6. Batching: > EMBED_BATCH_LIMIT chunks → multiple embed() calls
# ---------------------------------------------------------------------------


class _CountingEmbedder:
    """Wraps DummyEmbedder, counts the number of `embed()` invocations."""

    def __init__(self) -> None:
        self._inner = DummyEmbedder()
        self.calls = 0
        self.batch_sizes: list[int] = []

    @property
    def model(self) -> str:
        return self._inner.model

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        self.calls += 1
        self.batch_sizes.append(len(texts))
        return await self._inner.embed(texts)


async def test_rebuild_batches_chunks_at_embed_batch_limit(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    # 1 summary + 1 exp + 1 proj + 12 skills = 15 chunks → 2 batches (10 + 5).
    user_id, profile_id = await _seed_profile(sessionmaker_, n_skills=12)
    embedder = _CountingEmbedder()

    result = await rebuild_for_profile(
        sessionmaker_, user_id=user_id, profile_id=profile_id, embedder=embedder
    )
    assert result.chunks_written == 15
    assert embedder.calls == 2
    assert embedder.batch_sizes == [EMBED_BATCH_LIMIT, 5]

    async with sessionmaker_() as session:
        count = (
            await session.execute(
                sa.select(sa.func.count())
                .select_from(ProfileChunk)
                .where(ProfileChunk.profile_id == profile_id)
            )
        ).scalar_one()
    assert count == 15


pytestmark = pytest.mark.integration
