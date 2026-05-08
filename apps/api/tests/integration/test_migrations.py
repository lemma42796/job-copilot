"""Migration upgrade + schema-shape integration test(v2 形态)。

Spins a `pgvector/pgvector:pg16` container, runs `alembic upgrade head`,
asserts 关键 v2 schema 形状(沿用层 + v2 新表 + 关键索引 + 触发器 +
char_ngrams 函数)。

**downgrade 不测**:0016 v1→v2 切换不实做 downgrade(语义不兼容,
DATA_MODEL §10);要回 v1 走 git checkout v0.1-jobcopilot-v1。

Tagged `integration` 走 testcontainers,不进 unit-test 路径。
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

# v2 业务表 + 沿用层(LLM cost / cache / prompt 版本)。M0 alembic 0016 落地。
EXPECTED_TABLES = {
    # v2 笔记 SR 闭环
    "notes",
    "note_chunks",
    "questions",
    "quiz_sessions",
    "session_answers",
    "knowledge_gaps",
    # v2 JD 累积分析
    "jds",
    "jd_analyses",
    # v2 简历诊断
    "resumes",
    "resume_analyses",
    # 沿用层(0006 + 0015)
    "llm_calls",
    "prompt_versions",
    "llm_response_cache",
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
        async_url = sync_url.replace(
            "postgresql+psycopg2://", "postgresql+asyncpg://"
        ).replace("postgresql://", "postgresql+asyncpg://")
        yield async_url
    finally:
        container.stop()


def _make_alembic_config(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.mark.integration
def test_migration_upgrade_v2_schema(pg_async_url: str) -> None:
    cfg = _make_alembic_config(pg_async_url)

    command.upgrade(cfg, "head")

    sync_url = pg_async_url.replace("postgresql+asyncpg://", "postgresql+psycopg://")
    engine = sa.create_engine(sync_url)
    try:
        with engine.connect() as conn:
            # pgvector + pg_trgm 扩展(沿用 v1 0001)
            extensions = {
                r[0]
                for r in conn.execute(
                    sa.text(
                        "SELECT extname FROM pg_extension "
                        "WHERE extname IN ('vector','pg_trgm')"
                    )
                )
            }
            assert extensions == {"vector", "pg_trgm"}

            # v2 表 + 沿用层都建出来
            tables = {
                r[0]
                for r in conn.execute(
                    sa.text(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                    )
                )
            }
            assert EXPECTED_TABLES.issubset(tables), (
                f"missing: {EXPECTED_TABLES - tables}"
            )

            # v1 业务表已 DROP(0016 一波清)
            v1_tables = {
                "users",
                "files",
                "profiles",
                "profile_chunks",
                "matches",
                "resume_versions",
            }
            assert v1_tables.isdisjoint(tables), (
                f"v1 tables 残留:{v1_tables & tables}"
            )

            # v2 ENUM 都建出来
            enums = {
                r[0]
                for r in conn.execute(
                    sa.text("SELECT typname FROM pg_type WHERE typtype='e'")
                )
            }
            assert {"note_source", "question_type", "quiz_session_status"}.issubset(
                enums
            )

            # note_chunks HNSW 索引(笔记语义召回的命脉)
            hnsw = conn.execute(
                sa.text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE indexname='ix_note_chunks_embedding_hnsw'"
                )
            ).first()
            assert hnsw is not None
            assert "hnsw" in hnsw.indexdef.lower()
            assert "vector_cosine_ops" in hnsw.indexdef

            # updated_at 触发器:v2 8 张业务表(notes / note_chunks / questions /
            # quiz_sessions / session_answers / knowledge_gaps / jds / resumes)
            # + 沿用层 prompt_versions / llm_response_cache 也有,所以 ≥ 8
            trigger_count = conn.execute(
                sa.text(
                    "SELECT count(*) FROM pg_trigger "
                    "WHERE tgname LIKE 'tg_%_set_updated_at' AND NOT tgisinternal"
                )
            ).scalar_one()
            assert trigger_count >= 8

            # 0006 沿用:llm_calls.prompt_version_id FK ON DELETE SET NULL
            # (cost log 必须撑过 prompt_version 删除;v2 不再有 users FK,
            # M4+ SaaS 时再 add column user_id + 重 build FK)
            prompt_fk = conn.execute(
                sa.text(
                    "SELECT rc.delete_rule, ccu.table_name "
                    "FROM information_schema.table_constraints tc "
                    "JOIN information_schema.referential_constraints rc "
                    "  ON tc.constraint_name = rc.constraint_name "
                    "JOIN information_schema.constraint_column_usage ccu "
                    "  ON ccu.constraint_name = tc.constraint_name "
                    "WHERE tc.table_name='llm_calls' "
                    "  AND tc.constraint_name='fk_lc_prompt_version_id'"
                )
            ).first()
            assert prompt_fk is not None
            assert prompt_fk.delete_rule == "SET NULL"
            assert prompt_fk.table_name == "prompt_versions"

            # 0006 沿用:prompt_versions (agent_name, version) UNIQUE
            uq_exists = conn.execute(
                sa.text(
                    "SELECT 1 FROM pg_constraint WHERE conname='uq_pv_agent_version'"
                )
            ).scalar_one_or_none()
            assert uq_exists == 1

            # 0014 沿用:char_ngrams SQL 函数仍在,note_chunks.content_tsv 走它
            char_ngrams_exists = conn.execute(
                sa.text(
                    "SELECT 1 FROM pg_proc WHERE proname='char_ngrams' "
                    "AND pronamespace = 'public'::regnamespace"
                )
            ).scalar_one_or_none()
            assert char_ngrams_exists == 1
            content_tsv_def = conn.execute(
                sa.text(
                    "SELECT pg_get_expr(adbin, adrelid) AS expr "
                    "FROM pg_attrdef ad "
                    "JOIN pg_attribute a ON a.attrelid=ad.adrelid AND a.attnum=ad.adnum "
                    "WHERE a.attrelid='note_chunks'::regclass "
                    "  AND a.attname='content_tsv'"
                )
            ).first()
            assert content_tsv_def is not None
            assert "char_ngrams" in content_tsv_def.expr.lower()

            # notes 软删 partial unique index(同 folder + title 不重)
            notes_uq = conn.execute(
                sa.text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE indexname='uq_notes_folder_title'"
                )
            ).first()
            assert notes_uq is not None
            idxdef = notes_uq.indexdef.lower()
            assert "unique index" in idxdef
            assert "(folder_path, title)" in idxdef
            assert "where (deleted_at is null)" in idxdef
    finally:
        engine.dispose()
