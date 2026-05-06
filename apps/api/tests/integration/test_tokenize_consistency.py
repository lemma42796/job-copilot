"""Integration test — Python `tokenize_ngram` 与 SQL `public.char_ngrams` 输出严格一致。

S21 子任务 4-A:hybrid 检索的命脉是 query 切法(Python)与文档索引切法(SQL)
完全相同;任一端漂移就会让 lexical 路径召回率暴跌。本测试在真 PG 上跑,
对各类 input 跑双端,assert token 集合相等。

如果某天有人改了 SQL `char_ngrams`(如词形扩展、stopwords)而忘了改 Python
端,本测试立即报错。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from testcontainers.postgres import PostgresContainer

from jobcopilot_api.services.tokenize import tokenize_ngram

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
async def engine(_container: str) -> AsyncEngine:
    return create_async_engine(_container, future=True, pool_pre_ping=True)


CASES = [
    "我精通Python后端开发",
    "Hello World",
    "",
    "   ",
    "a",
    "我",
    "ab",
    "我精",
    "熟悉LangGraph和PostgreSQL,使用Redis做缓存",
    "AI Agent 开发,RAG 检索,LangChain / LlamaIndex",
    "12W QPS 高并发系统",
    "Python 3.9 / Go 1.21",
    ",,..!!??",
    "字符 ngram + ASCII unigram",
    # 跨边界 bigram + 多种标点
    "我熟悉Python(熟练)、Go(基础)和PostgreSQL",
]


@pytest.mark.integration
@pytest.mark.parametrize("text", CASES)
async def test_python_sql_token_consistency(engine: AsyncEngine, text: str) -> None:
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text("SELECT public.char_ngrams(:s)"),
            {"s": text},
        )
        sql_output = result.scalar_one()

    sql_tokens = sql_output.split() if sql_output else []
    py_tokens = tokenize_ngram(text)

    assert py_tokens == sql_tokens, (
        f"Token mismatch for {text!r}:\n  py:  {py_tokens}\n  sql: {sql_tokens}"
    )
