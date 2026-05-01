"""Unit-level smoke tests for infra/db.py.

Engine creation is lazy (no socket opened), so we can exercise the singleton
contract without a live Postgres. `get_session` is exercised by integration
and router tests.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from jobcopilot_api.infra.db import (
    get_engine,
    get_sessionmaker,
    reset_engine,
)


@pytest.mark.asyncio
async def test_get_engine__called_twice__returns_same_instance() -> None:
    try:
        engine = get_engine()
        assert isinstance(engine, AsyncEngine)
        assert get_engine() is engine
    finally:
        await reset_engine()


@pytest.mark.asyncio
async def test_reset_engine__after_get__yields_fresh_engine() -> None:
    first = get_engine()
    await reset_engine()
    second = get_engine()
    try:
        assert first is not second
    finally:
        await reset_engine()


@pytest.mark.asyncio
async def test_get_sessionmaker__is_singleton_async_sessionmaker() -> None:
    try:
        sm = get_sessionmaker()
        assert isinstance(sm, async_sessionmaker)
        assert get_sessionmaker() is sm
        # Bind survives even if engine is recreated only once
        assert sm.kw.get("class_", AsyncSession) is AsyncSession
    finally:
        await reset_engine()
