"""Integration tests for `infra.prompts.load_prompt_versions`. ADR-0006 D6.

Three load-bearing behaviors:

  1. Empty DB → all .j2 files INSERT a row; cache reflects ids.
  2. Re-running with unchanged files is idempotent (same ids,
     no duplicate rows).
  3. Editing a template in place (without bumping version) raises
     `PromptVersionMismatchError` and rolls back partial state.

We use a tmp prompts dir per test so we don't pollute the real
`prompts/` shipped with the package.
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

from jobcopilot_api.infra.prompts import (
    PromptVersionMismatchError,
    load_prompt_versions,
)
from jobcopilot_api.models import PromptVersion

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

TEMPLATE_V1 = "## SYSTEM\nyou are a tester.\n\n## USER\nhi {{ name }}\n"
TEMPLATE_V1_EDITED = "## SYSTEM\nyou are a tester (edited).\n\n## USER\nhi {{ name }}\n"
TEMPLATE_V2 = "## SYSTEM\nv2 system.\n\n## USER\nhi {{ name }}\n"


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
        await conn.execute(sa.text("TRUNCATE TABLE prompt_versions RESTART IDENTITY CASCADE"))


def _write_prompts(root: Path, agent: str, version: str, content: str) -> Path:
    agent_dir = root / agent
    agent_dir.mkdir(parents=True, exist_ok=True)
    path = agent_dir / f"{version}.j2"
    path.write_text(content, encoding="utf-8")
    return path


# ---- 1. INSERT path ----


async def test_first_load_inserts_rows(
    sessionmaker_: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    _write_prompts(tmp_path, "jd_parser", "v1.0.0", TEMPLATE_V1)
    _write_prompts(tmp_path, "profile_parser", "v1.0.0", TEMPLATE_V2)

    cache = await load_prompt_versions(sessionmaker_, prompts_dir=tmp_path)

    assert set(cache.keys()) == {("jd_parser", "v1.0.0"), ("profile_parser", "v1.0.0")}
    jd = cache[("jd_parser", "v1.0.0")]
    assert jd.system == "you are a tester."
    assert jd.user_template == "hi {{ name }}"
    assert jd.id > 0

    async with sessionmaker_() as session:
        rows = (await session.execute(sa.select(PromptVersion))).scalars().all()
        assert len(rows) == 2


# ---- 2. Idempotent re-run ----


async def test_second_load_reuses_existing_id(
    sessionmaker_: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    _write_prompts(tmp_path, "jd_parser", "v1.0.0", TEMPLATE_V1)

    first = await load_prompt_versions(sessionmaker_, prompts_dir=tmp_path)
    second = await load_prompt_versions(sessionmaker_, prompts_dir=tmp_path)

    assert first[("jd_parser", "v1.0.0")].id == second[("jd_parser", "v1.0.0")].id

    async with sessionmaker_() as session:
        count = (
            await session.execute(sa.select(sa.func.count()).select_from(PromptVersion))
        ).scalar_one()
        assert count == 1


# ---- 3. Mismatch detection ----


async def test_edit_without_bump_raises(
    sessionmaker_: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    _write_prompts(tmp_path, "jd_parser", "v1.0.0", TEMPLATE_V1)
    await load_prompt_versions(sessionmaker_, prompts_dir=tmp_path)

    # Silently edit the template content under the same filename.
    _write_prompts(tmp_path, "jd_parser", "v1.0.0", TEMPLATE_V1_EDITED)

    with pytest.raises(PromptVersionMismatchError, match="differs from"):
        await load_prompt_versions(sessionmaker_, prompts_dir=tmp_path)


async def test_mismatch_error_suggests_next_version(
    sessionmaker_: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    _write_prompts(tmp_path, "jd_parser", "v1.0.0", TEMPLATE_V1)
    await load_prompt_versions(sessionmaker_, prompts_dir=tmp_path)

    _write_prompts(tmp_path, "jd_parser", "v1.0.0", TEMPLATE_V1_EDITED)

    with pytest.raises(PromptVersionMismatchError, match=r"v1\.0\.0 → v1\.0\.1"):
        await load_prompt_versions(sessionmaker_, prompts_dir=tmp_path)


async def test_mismatch_rollback_does_not_corrupt_other_rows(
    sessionmaker_: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """Mid-batch failure must roll back any newly-inserted rows from the
    same load_prompt_versions call (single-transaction guarantee).
    """
    _write_prompts(tmp_path, "alpha", "v1.0.0", TEMPLATE_V1)
    await load_prompt_versions(sessionmaker_, prompts_dir=tmp_path)

    # alpha row exists. Now a second agent has new content (good) AND
    # alpha is silently edited (bad). We expect: alpha row unchanged,
    # beta row NOT inserted (mid-batch rollback).
    _write_prompts(tmp_path, "alpha", "v1.0.0", TEMPLATE_V1_EDITED)
    _write_prompts(tmp_path, "beta", "v1.0.0", TEMPLATE_V2)

    with pytest.raises(PromptVersionMismatchError):
        await load_prompt_versions(sessionmaker_, prompts_dir=tmp_path)

    async with sessionmaker_() as session:
        rows = (
            (await session.execute(sa.select(PromptVersion).order_by(PromptVersion.agent_name)))
            .scalars()
            .all()
        )
        # Only alpha should exist; beta got rolled back.
        assert len(rows) == 1
        assert rows[0].agent_name == "alpha"


# ---- 4. New version alongside old (no mismatch) ----


async def test_new_version_alongside_old_inserts(
    sessionmaker_: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    _write_prompts(tmp_path, "jd_parser", "v1.0.0", TEMPLATE_V1)
    first = await load_prompt_versions(sessionmaker_, prompts_dir=tmp_path)

    # Add v1.0.1 with new content; v1.0.0 still untouched.
    _write_prompts(tmp_path, "jd_parser", "v1.0.1", TEMPLATE_V2)
    second = await load_prompt_versions(sessionmaker_, prompts_dir=tmp_path)

    assert first[("jd_parser", "v1.0.0")].id == second[("jd_parser", "v1.0.0")].id
    assert ("jd_parser", "v1.0.1") in second
    assert second[("jd_parser", "v1.0.1")].id != second[("jd_parser", "v1.0.0")].id
