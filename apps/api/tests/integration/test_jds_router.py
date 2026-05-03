"""Integration tests for `/v1/jds` (router layer). ADR-0006 D4 / D8 / D12.

Stack mirrors `test_files_router.py`: testcontainers PG + httpx
ASGITransport. Two extras specific to JDs:

  - `DummyProvider` is injected via `app.dependency_overrides[_llm_dep]`
    so we don't talk to DashScope; each test queues its own response or
    error.
  - `app.state.prompt_versions` is seeded by hand because ASGITransport
    does not run lifespan, so `load_prompt_versions` never fires.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from tenacity.wait import wait_none
from testcontainers.postgres import PostgresContainer

from jobcopilot_api.infra.db import get_session
from jobcopilot_api.infra.prompts import LoadedPrompt
from jobcopilot_api.llm.client import BaseLLMClient, LLMClient, MemoryCallLogger
from jobcopilot_api.llm.errors import LLMUpstreamError
from jobcopilot_api.llm.providers.dummy import DummyProvider
from jobcopilot_api.main import create_app
from jobcopilot_api.models import User
from jobcopilot_api.routers.jds import _llm_dep, _sessionmaker_dep

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

MakeApp = Callable[[LLMClient], FastAPI]


GOLDEN_LLM_OUTPUT = json.dumps(
    {
        "company": "ACME",
        "title": "Senior Backend Engineer",
        "location": "Shanghai",
        "salary_min": 30000,
        "salary_max": 50000,
        "salary_period": "monthly",
        "job_level": "senior",
        "years_required": 5,
        "education": "本科",
        "hard_skills": [{"name": "python", "name_raw": "Python", "required": True, "weight": 1.0}],
        "soft_skills": [],
        "bonus_skills": [],
        "responsibilities": ["Lead the backend platform"],
        "description": "JD body",
        "confidence": 0.92,
    }
)


def _long_text() -> str:
    return (
        "Senior Backend Engineer @ ACME\n"
        "We are hiring an experienced engineer to lead our platform team. "
        "Required: Python, PostgreSQL, distributed systems, AWS or GCP.\n"
    )


def _hdr(uid: int) -> dict[str, str]:
    return {"X-User-Id": str(uid)}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
        await conn.execute(sa.text("TRUNCATE TABLE jds RESTART IDENTITY CASCADE"))


def _client_with_response(content: str) -> LLMClient:
    """LLMClient backed by a DummyProvider that yields a single canned
    JSON response. `wait_none()` skips tenacity backoff so tests stay
    fast."""
    dummy = DummyProvider()
    dummy.queue(content=content, tokens_in=120, tokens_out=40)
    return BaseLLMClient(provider=dummy, logger=MemoryCallLogger(), retry_wait=wait_none())


def _client_with_error(*errs: BaseException) -> LLMClient:
    dummy = DummyProvider()
    for e in errs:
        dummy.queue_error(e)
    return BaseLLMClient(provider=dummy, logger=MemoryCallLogger(), retry_wait=wait_none())


@pytest.fixture
def prompt() -> LoadedPrompt:
    """Stand-in for the lifespan-loaded prompt. `id=1` is enough because
    DummyProvider doesn't actually use the template content."""
    return LoadedPrompt(
        id=1,
        agent_name="jd_parser",
        version="v1.0.1",
        system="you are a JD parser",
        user_template="parse: {{ jd_text }}",
        template_hash="x" * 64,
    )


@pytest.fixture
def make_app(
    sessionmaker_: async_sessionmaker[AsyncSession],
    prompt: LoadedPrompt,
) -> MakeApp:
    """Factory that wires a fresh app with overridable LLM client.

    Tests call `make_app(llm)` to get a (FastAPI, AsyncClient-ready) app
    with the given client injected. Sessionmaker, session-dep, and the
    prompt cache are seeded the same way every time.
    """

    def _make(llm: LLMClient) -> FastAPI:
        app = create_app()
        app.state.prompt_versions = {("jd_parser", "v1.0.1"): prompt}

        async def _override_session() -> AsyncIterator[AsyncSession]:
            async with sessionmaker_() as session:
                try:
                    yield session
                except Exception:
                    await session.rollback()
                    raise

        app.dependency_overrides[get_session] = _override_session
        app.dependency_overrides[_llm_dep] = lambda: llm
        app.dependency_overrides[_sessionmaker_dep] = lambda: sessionmaker_
        return app

    return _make


@pytest.fixture
async def user_id(sessionmaker_: async_sessionmaker[AsyncSession]) -> int:
    async with sessionmaker_() as session, session.begin():
        u = User(email="t@example.com", name="t", locale="zh-CN")
        session.add(u)
        await session.flush()
        return u.id


@pytest.fixture
async def other_user_id(sessionmaker_: async_sessionmaker[AsyncSession]) -> int:
    async with sessionmaker_() as session, session.begin():
        u = User(email="other@example.com", name="other", locale="zh-CN")
        session.add(u)
        await session.flush()
        return u.id


def _parse_sse(body: str) -> list[tuple[str, dict[str, object]]]:
    """Split an SSE response body into `[(event_name, parsed_data), ...]`.

    sse-starlette emits frames separated by blank lines; each frame is
    `event: <name>\\ndata: <json>\\n`. Comment heartbeats (`:` prefix)
    are filtered out.
    """
    out: list[tuple[str, dict[str, object]]] = []
    for chunk in body.split("\r\n\r\n"):
        chunk = chunk.strip()
        if not chunk or chunk.startswith(":"):
            continue
        event = ""
        data = ""
        for line in chunk.splitlines():
            if line.startswith("event:"):
                event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data = line[len("data:") :].strip()
        if event:
            out.append((event, json.loads(data) if data else {}))
    return out


# ---------------------------------------------------------------------------
# POST /v1/jds/parse — sync
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_parse_sync_golden_returns_201_with_structured(
    make_app: MakeApp, user_id: int
) -> None:
    app = make_app(_client_with_response(GOLDEN_LLM_OUTPUT))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/jds/parse",
            json={"text": _long_text(), "source": "text_paste"},
            headers=_hdr(user_id),
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "parsed"
    assert body["structured"]["title"] == "Senior Backend Engineer"
    assert body["structured"]["company"] == "ACME"
    assert body["tokens"] == {"input": 120, "output": 40}
    assert body["confidence"] == 0.92
    assert body["id"] >= 1


@pytest.mark.integration
async def test_parse_sync_text_too_short_returns_422(make_app: MakeApp, user_id: int) -> None:
    """Pre-insert failure: no jd row should exist after the call."""
    app = make_app(_client_with_response(GOLDEN_LLM_OUTPUT))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/jds/parse",
            json={"text": "way too short", "source": "text_paste"},
            headers=_hdr(user_id),
        )

    assert resp.status_code == 422
    assert resp.json()["code"] == "JD_PARSE_FAILED"


@pytest.mark.integration
async def test_parse_sync_llm_upstream_returns_502(make_app: MakeApp, user_id: int) -> None:
    # 3 retries inside BaseLLMClient.
    app = make_app(
        _client_with_error(
            LLMUpstreamError("503", status_code=503),
            LLMUpstreamError("503", status_code=503),
            LLMUpstreamError("503", status_code=503),
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/jds/parse",
            json={"text": _long_text(), "source": "text_paste"},
            headers=_hdr(user_id),
        )

    assert resp.status_code == 502
    assert resp.json()["code"] == "LLM_UPSTREAM"


@pytest.mark.integration
async def test_parse_sync_unauthorized_when_no_user_header(make_app: MakeApp, user_id: int) -> None:
    app = make_app(_client_with_response(GOLDEN_LLM_OUTPUT))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/jds/parse",
            json={"text": _long_text(), "source": "text_paste"},
        )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /v1/jds/parse?stream=1 — SSE
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_parse_sse_success_emits_started_result_done(make_app: MakeApp, user_id: int) -> None:
    app = make_app(_client_with_response(GOLDEN_LLM_OUTPUT))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/jds/parse?stream=1",
            json={"text": _long_text(), "source": "text_paste"},
            headers=_hdr(user_id),
        )
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    names = [e[0] for e in events]
    assert names == ["started", "result", "done"]

    started = events[0][1]
    assert "job_id" in started
    assert isinstance(started["resource_id"], int)

    result = events[1][1]
    assert result["resource_id"] == started["resource_id"]
    assert result["url"] == f"/v1/jds/{started['resource_id']}"

    assert events[2][1] == {"ok": True}


@pytest.mark.integration
async def test_parse_sse_llm_failure_emits_started_error_done(
    make_app: MakeApp, user_id: int
) -> None:
    app = make_app(
        _client_with_error(
            LLMUpstreamError("503", status_code=503),
            LLMUpstreamError("503", status_code=503),
            LLMUpstreamError("503", status_code=503),
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/jds/parse?stream=1",
            json={"text": _long_text(), "source": "text_paste"},
            headers=_hdr(user_id),
        )
    # SSE always returns HTTP 200 once connected (API_SPEC §6.3 footnote).
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    names = [e[0] for e in events]
    assert names == ["started", "error", "done"]
    assert events[1][1]["code"] == "LLM_UPSTREAM"
    assert events[2][1] == {"ok": False}


@pytest.mark.integration
async def test_parse_sse_pre_insert_failure_skips_started(make_app: MakeApp, user_id: int) -> None:
    """Text too short fails BEFORE any jds row exists, so no `started`."""
    app = make_app(_client_with_response(GOLDEN_LLM_OUTPUT))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/jds/parse?stream=1",
            json={"text": "too short", "source": "text_paste"},
            headers=_hdr(user_id),
        )
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    names = [e[0] for e in events]
    assert names == ["error", "done"]
    assert events[0][1]["code"] == "JD_PARSE_FAILED"


# ---------------------------------------------------------------------------
# GET /v1/jds + GET /v1/jds/{id}
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_list_jds_returns_user_rows_only(
    make_app: MakeApp, user_id: int, other_user_id: int
) -> None:
    app = make_app(_client_with_response(GOLDEN_LLM_OUTPUT))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Seed: 2 jds for user_id, 1 for other.
        for _ in range(2):
            await client.post(
                "/v1/jds/parse",
                json={"text": _long_text(), "source": "text_paste"},
                headers=_hdr(user_id),
            )
            # Re-arm the dummy for the next call (each app call drains 1).
            app.dependency_overrides[_llm_dep] = lambda: _client_with_response(GOLDEN_LLM_OUTPUT)
        await client.post(
            "/v1/jds/parse",
            json={"text": _long_text(), "source": "text_paste"},
            headers=_hdr(other_user_id),
        )

        resp = await client.get("/v1/jds", headers=_hdr(user_id))

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 2
    assert all(isinstance(r["id"], int) for r in body["data"])
    assert body["has_more"] is False


@pytest.mark.integration
async def test_list_jds_pagination_cursor(make_app: MakeApp, user_id: int) -> None:
    """Seed 3, fetch with limit=2, expect cursor + has_more True."""
    app = make_app(_client_with_response(GOLDEN_LLM_OUTPUT))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(3):
            app.dependency_overrides[_llm_dep] = lambda: _client_with_response(GOLDEN_LLM_OUTPUT)
            r = await client.post(
                "/v1/jds/parse",
                json={"text": _long_text(), "source": "text_paste"},
                headers=_hdr(user_id),
            )
            assert r.status_code == 201

        resp = await client.get("/v1/jds?limit=2", headers=_hdr(user_id))

    body = resp.json()
    assert len(body["data"]) == 2
    assert body["has_more"] is True
    assert body["next_cursor"] is not None


@pytest.mark.integration
async def test_get_jd_detail_returns_full_row(make_app: MakeApp, user_id: int) -> None:
    app = make_app(_client_with_response(GOLDEN_LLM_OUTPUT))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (
            await client.post(
                "/v1/jds/parse",
                json={"text": _long_text(), "source": "text_paste"},
                headers=_hdr(user_id),
            )
        ).json()

        resp = await client.get(f"/v1/jds/{created['id']}", headers=_hdr(user_id))

    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Senior Backend Engineer"
    assert body["raw_text"] is not None  # JDDetail includes raw_text
    assert body["status"] == "parsed"
    assert body["hard_skills"][0]["name"] == "python"


@pytest.mark.integration
async def test_get_jd_detail_404_for_other_user(
    make_app: MakeApp, user_id: int, other_user_id: int
) -> None:
    app = make_app(_client_with_response(GOLDEN_LLM_OUTPUT))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (
            await client.post(
                "/v1/jds/parse",
                json={"text": _long_text(), "source": "text_paste"},
                headers=_hdr(user_id),
            )
        ).json()

        resp = await client.get(f"/v1/jds/{created['id']}", headers=_hdr(other_user_id))

    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# PATCH + DELETE
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_patch_jd_structured_overrides_title(make_app: MakeApp, user_id: int) -> None:
    app = make_app(_client_with_response(GOLDEN_LLM_OUTPUT))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (
            await client.post(
                "/v1/jds/parse",
                json={"text": _long_text(), "source": "text_paste"},
                headers=_hdr(user_id),
            )
        ).json()

        resp = await client.patch(
            f"/v1/jds/{created['id']}",
            json={
                "structured": {
                    "title": "Staff Engineer",
                    "company": "ACME",
                    "confidence": 0.95,
                },
            },
            headers=_hdr(user_id),
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Staff Engineer"


@pytest.mark.integration
async def test_patch_jd_status_only(make_app: MakeApp, user_id: int) -> None:
    app = make_app(_client_with_response(GOLDEN_LLM_OUTPUT))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (
            await client.post(
                "/v1/jds/parse",
                json={"text": _long_text(), "source": "text_paste"},
                headers=_hdr(user_id),
            )
        ).json()

        resp = await client.patch(
            f"/v1/jds/{created['id']}",
            json={"status": "parsing"},
            headers=_hdr(user_id),
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "parsing"


@pytest.mark.integration
async def test_delete_jd_returns_204_then_404(make_app: MakeApp, user_id: int) -> None:
    app = make_app(_client_with_response(GOLDEN_LLM_OUTPUT))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (
            await client.post(
                "/v1/jds/parse",
                json={"text": _long_text(), "source": "text_paste"},
                headers=_hdr(user_id),
            )
        ).json()

        resp = await client.delete(f"/v1/jds/{created['id']}", headers=_hdr(user_id))
        assert resp.status_code == 204

        # Soft-deleted → 404 on next GET (D9: no existence-leak distinction).
        get_resp = await client.get(f"/v1/jds/{created['id']}", headers=_hdr(user_id))
        assert get_resp.status_code == 404
