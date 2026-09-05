"""Integration test: DBCallLogger writes through to `llm_calls`.

Spins a real `pgvector/pgvector:pg16` container, runs alembic to head,
then drives `BaseLLMClient + DBCallLogger + DummyProvider` end-to-end
and asserts the row count + critical columns. The fourth test exercises
the load-bearing isolation guarantee (ADR-0004 D4): a business
transaction that rolls back must NOT take the cost log down with it.

Tagged `integration`; CI excludes it from the unit-coverage path the
same way `test_migrations.py` is.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from tenacity.wait import wait_none
from testcontainers.postgres import PostgresContainer

from jobcopilot_api.llm.client import BaseLLMClient
from jobcopilot_api.llm.db_logger import DBCallLogger
from jobcopilot_api.llm.errors import LLMSchemaInvalidError, LLMTimeoutError
from jobcopilot_api.llm.providers.dummy import DummyProvider
from jobcopilot_api.llm.tiers import Tier

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


class _Schema(BaseModel):
    title: str


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
    """Function-scope on purpose: pytest-asyncio gives each test its own
    event loop, and asyncpg connections cannot cross loops. Container is
    still module-scope so we don't restart Postgres per test.
    """
    eng = create_async_engine(_container, future=True)
    yield eng
    await eng.dispose()


@pytest.fixture(autouse=True)
async def _truncate_llm_calls(engine: AsyncEngine) -> AsyncIterator[None]:
    yield
    async with engine.begin() as conn:
        await conn.execute(sa.text("TRUNCATE TABLE llm_calls RESTART IDENTITY CASCADE"))


@pytest.fixture
def sessionmaker_(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def client_pair(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> tuple[BaseLLMClient, DummyProvider]:
    dummy = DummyProvider()
    db_logger = DBCallLogger(sessionmaker=sessionmaker_)
    client = BaseLLMClient(provider=dummy, logger=db_logger, retry_wait=wait_none())
    return client, dummy


async def _llm_call_count(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        cur = await conn.execute(sa.text("SELECT count(*) FROM llm_calls"))
        return int(cur.scalar_one())


async def _user_count_by_email(engine: AsyncEngine, email: str) -> int:
    async with engine.connect() as conn:
        cur = await conn.execute(
            sa.text("SELECT count(*) FROM users WHERE email = :email"), {"email": email}
        )
        return int(cur.scalar_one())


@pytest.mark.integration
async def test_success_call_writes_one_success_row(
    engine: AsyncEngine, client_pair: tuple[BaseLLMClient, DummyProvider]
) -> None:
    client, dummy = client_pair
    dummy.queue(content="hello", tokens_in=120, tokens_out=18, cached_tokens=10)

    result = await client.complete(
        feature="jd_parse",
        tier=Tier.CHEAP,
        system="sys",
        user="usr",
        user_id=None,
        trace_id="trace-success",
        related_entity="jd",
        related_id=42,
    )
    assert result.success is True

    assert await _llm_call_count(engine) == 1
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                sa.text(
                    "SELECT feature, tier, model, success, tokens_in, "
                    "       cached_tokens, tokens_out, error_code, "
                    "       trace_id, related_entity, related_id "
                    "FROM llm_calls"
                )
            )
        ).one()
    assert row.feature == "jd_parse"
    assert row.tier == "cheap"
    assert row.model == "qwen3.8-flash"
    assert row.success is True
    assert row.tokens_in == 120
    assert row.cached_tokens == 10
    assert row.tokens_out == 18
    assert row.error_code is None
    assert row.trace_id == "trace-success"
    assert row.related_entity == "jd"
    assert row.related_id == 42


@pytest.mark.integration
async def test_timeout_writes_failed_row_with_zero_cost(
    engine: AsyncEngine, client_pair: tuple[BaseLLMClient, DummyProvider]
) -> None:
    client, dummy = client_pair
    for _ in range(3):  # tenacity max_attempts=3
        dummy.queue_error(LLMTimeoutError("simulated"))

    with pytest.raises(LLMTimeoutError):
        await client.complete(
            feature="jd_parse",
            tier=Tier.CHEAP,
            system="sys",
            user="usr",
        )

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                sa.text(
                    "SELECT success, error_code, cost_cny, tokens_in, tokens_out FROM llm_calls"
                )
            )
        ).one()
    assert row.success is False
    assert row.error_code == "timeout"
    assert row.cost_cny == 0
    assert row.tokens_in == 0
    assert row.tokens_out == 0


@pytest.mark.integration
async def test_schema_invalid_writes_failed_row(
    engine: AsyncEngine, client_pair: tuple[BaseLLMClient, DummyProvider]
) -> None:
    client, dummy = client_pair
    dummy.queue(content="not-json", tokens_in=20, tokens_out=4)
    dummy.queue(content="also-not-json", tokens_in=30, tokens_out=10)

    with pytest.raises(LLMSchemaInvalidError):
        await client.complete(
            feature="jd_parse",
            tier=Tier.CHEAP,
            system="sys",
            user="usr",
            response_schema=_Schema,
        )

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                sa.text("SELECT success, error_code, tokens_in, tokens_out FROM llm_calls")
            )
        ).one()
    assert row.success is False
    assert row.error_code == "schema_invalid"
    # Both attempts billed (Schema repair retry tokens accumulated):
    assert row.tokens_in == 50
    assert row.tokens_out == 14


async def _do_business_work_then_rollback(
    *,
    sessionmaker_: async_sessionmaker[AsyncSession],
    client: BaseLLMClient,
    sentinel_email: str,
) -> None:
    async with sessionmaker_() as biz, biz.begin():
        await biz.execute(
            sa.text("INSERT INTO users (email, locale) VALUES (:email, 'zh-CN')"),
            {"email": sentinel_email},
        )
        await client.complete(
            feature="jd_parse",
            tier=Tier.CHEAP,
            system="sys",
            user="usr",
        )
        # Trigger rollback of the business transaction.
        raise RuntimeError("forced rollback")


@pytest.mark.integration
async def test_business_rollback_does_not_drop_cost_log(
    engine: AsyncEngine,
    sessionmaker_: async_sessionmaker[AsyncSession],
    client_pair: tuple[BaseLLMClient, DummyProvider],
) -> None:
    """The load-bearing assertion of ADR-0004 D4.

    Sequence:
      1. Open a business transaction.
      2. INSERT into users.
      3. Call LLMClient (which writes llm_calls on its own session).
      4. raise -> business rollback.
      5. Assert: users row gone, llm_calls row present.
    """
    client, dummy = client_pair
    dummy.queue(content="ok", tokens_in=5, tokens_out=2)

    sentinel_email = "rollback-sentinel@test"

    with pytest.raises(RuntimeError, match="forced rollback"):
        await _do_business_work_then_rollback(
            sessionmaker_=sessionmaker_,
            client=client,
            sentinel_email=sentinel_email,
        )

    # users insert was rolled back:
    assert await _user_count_by_email(engine, sentinel_email) == 0
    # but llm_calls survived because DBCallLogger committed on its own session:
    assert await _llm_call_count(engine) == 1
