"""Async SQLAlchemy engine + session plumbing.

Engine is built lazily so tests can reset settings between cases.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from jobcopilot_api.settings import settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        # P2:显式配置连接池。默认值(pool_size=5 + max_overflow=10)在
        # 十几个并发会话下就会撞 QueuePool limit。总连接数上限 =
        # 进程数 × (pool_size + max_overflow),必须小于 PostgreSQL
        # max_connections;超出则引入 PgBouncer。
        _engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout_s,
            pool_recycle=settings.db_pool_recycle_s,
            future=True,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(),
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:  # pragma: no cover
    """FastAPI dependency: yields a session, rolls back on error.

    Exercised end-to-end by router integration tests; excluded from unit
    coverage so that the gate doesn't require a live DB at unit-test time.
    """
    async with get_sessionmaker()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def reset_engine() -> None:
    """Test helper: dispose & forget the cached engine/sessionmaker."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
