"""Migration round-trip + schema-shape integration test.

Spins a `pgvector/pgvector:pg16` container, runs `alembic upgrade head`,
then `downgrade base`, then `upgrade head` again, and asserts the
critical extensions / tables / indexes are present.

Tagged `integration` so it stays out of the unit-test path.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

EXPECTED_TABLES = {
    "users",
    "files",
    "jds",
    "profiles",
    "profile_experiences",
    "profile_projects",
    "profile_skills",
    "profile_educations",
    "profile_chunks",
    "llm_calls",
    "prompt_versions",
}


@pytest.fixture(scope="module")
def pg_async_url() -> Iterator[str]:
    container = (
        PostgresContainer(image="pgvector/pgvector:pg16")
        .with_env("POSTGRES_USER", "jobcopilot")
        .with_env("POSTGRES_PASSWORD", "jobcopilot")
        .with_env("POSTGRES_DB", "jobcopilot")
    )
    container.start()
    try:
        sync_url = container.get_connection_url()
        # testcontainers returns psycopg2-style; convert to asyncpg for alembic
        async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
            "postgresql://", "postgresql+asyncpg://"
        )
        yield async_url
    finally:
        container.stop()


def _make_alembic_config(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    # script_location in alembic.ini is relative; resolve to an absolute path
    # so `pytest` can run from any cwd.
    cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.mark.integration
def test_migration_round_trip(pg_async_url: str) -> None:
    cfg = _make_alembic_config(pg_async_url)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    sync_url = pg_async_url.replace("postgresql+asyncpg://", "postgresql+psycopg://")
    engine = sa.create_engine(sync_url)
    try:
        with engine.connect() as conn:
            extensions = {
                r[0]
                for r in conn.execute(
                    sa.text(
                        "SELECT extname FROM pg_extension WHERE extname IN ('vector','pg_trgm')"
                    )
                )
            }
            assert extensions == {"vector", "pg_trgm"}

            tables = {
                r[0]
                for r in conn.execute(
                    sa.text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                )
            }
            assert EXPECTED_TABLES.issubset(tables), f"missing: {EXPECTED_TABLES - tables}"

            hnsw = conn.execute(
                sa.text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE indexname='idx_pc_embedding_hnsw'"
                )
            ).first()
            assert hnsw is not None
            assert "hnsw" in hnsw.indexdef.lower()
            assert "vector_cosine_ops" in hnsw.indexdef

            trigger_count = conn.execute(
                sa.text(
                    "SELECT count(*) FROM pg_trigger "
                    "WHERE tgname LIKE 'tg_%_set_updated_at' AND NOT tgisinternal"
                )
            ).scalar_one()
            assert trigger_count >= 7  # users, jds, profiles + 4 children

            # 0006: llm_calls FKs both fall back to NULL on delete (cost log
            # must survive user / prompt_version removal).
            llm_fks = {
                (r.constraint_name, r.delete_rule): r.foreign_table_name
                for r in conn.execute(
                    sa.text(
                        "SELECT tc.constraint_name, rc.delete_rule, "
                        "       ccu.table_name AS foreign_table_name "
                        "FROM information_schema.table_constraints tc "
                        "JOIN information_schema.referential_constraints rc "
                        "  ON tc.constraint_name = rc.constraint_name "
                        "JOIN information_schema.constraint_column_usage ccu "
                        "  ON ccu.constraint_name = tc.constraint_name "
                        "WHERE tc.table_name='llm_calls' "
                        "  AND tc.constraint_type='FOREIGN KEY'"
                    )
                )
            }
            assert llm_fks.get(("fk_lc_user_id", "SET NULL")) == "users"
            assert llm_fks.get(("fk_lc_prompt_version_id", "SET NULL")) == "prompt_versions"

            # 0006: prompt_versions (agent_name, version) must be unique.
            uq_exists = conn.execute(
                sa.text("SELECT 1 FROM pg_constraint WHERE conname='uq_pv_agent_version'")
            ).scalar_one_or_none()
            assert uq_exists == 1
    finally:
        engine.dispose()
