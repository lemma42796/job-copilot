"""Integration tests for `/v1/profiles` (router layer).

Mirrors `test_jds_router.py` shape; key differences are SSE-only parse
(no sync mode) and the 409 ProfileExistsError path.
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
from jobcopilot_api.routers.profiles import _llm_dep, _sessionmaker_dep

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

MakeApp = Callable[[LLMClient], FastAPI]


GOLDEN_LLM_OUTPUT = json.dumps(
    {
        "full_name": "张三",
        "phone": "13800001234",
        "email": "zhangsan@example.com",
        "location": "上海",
        "summary": "5 年后端工程师",
        "target_titles": ["高级后端"],
        "experiences": [
            {
                "company": "ACME",
                "title": "高级后端",
                "location": "上海",
                "start_date": "2022-03-01",
                "end_date": None,
                "is_current": True,
                "description": "主导平台架构",
                "bullets": ["重构订单系统"],
                "tech_stack": ["python"],
                "achievements": ["QPS 5x"],
            }
        ],
        "projects": [],
        "skills": [
            {
                "name": "python",
                "name_raw": "Python",
                "category": "language",
                "level": "expert",
                "years": 5.0,
            }
        ],
        "educations": [
            {
                "school": "上海交通大学",
                "degree": "硕士",
                "major": "CS",
                "start_date": "2018-09-01",
                "end_date": "2021-06-30",
                "gpa": 3.7,
                "honors": [],
            }
        ],
    }
)


def _long_resume() -> str:
    return (
        "张三 高级后端工程师 5 年经验\n"
        "邮箱: zhangsan@example.com 电话: 13800001234\n"
        "ACME 公司 高级后端工程师 2022.03 - 至今\n"
        "  - 重构订单系统,QPS 从 2k 提升到 10k\n"
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


def _client_with_response(content: str) -> LLMClient:
    dummy = DummyProvider()
    dummy.queue(content=content, tokens_in=1200, tokens_out=420)
    return BaseLLMClient(provider=dummy, logger=MemoryCallLogger(), retry_wait=wait_none())


def _client_with_error(*errs: BaseException) -> LLMClient:
    dummy = DummyProvider()
    for e in errs:
        dummy.queue_error(e)
    return BaseLLMClient(provider=dummy, logger=MemoryCallLogger(), retry_wait=wait_none())


@pytest.fixture
def prompt() -> LoadedPrompt:
    return LoadedPrompt(
        id=1,
        agent_name="profile_parser",
        version="v1.0.0",
        system="you are a resume parser",
        user_template="parse: {{ resume_text }}",
        template_hash="x" * 64,
    )


@pytest.fixture
def make_app(
    sessionmaker_: async_sessionmaker[AsyncSession],
    prompt: LoadedPrompt,
) -> MakeApp:
    def _make(llm: LLMClient) -> FastAPI:
        app = create_app()
        # Seed both prompt caches so jds + profiles routers both resolve.
        app.state.prompt_versions = {
            ("profile_parser", "v1.0.0"): prompt,
            ("jd_parser", "v1.0.1"): prompt,
        }

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
# POST /v1/profiles/parse — SSE
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_parse_sse_success_emits_started_result_done(make_app: MakeApp, user_id: int) -> None:
    app = make_app(_client_with_response(GOLDEN_LLM_OUTPUT))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/profiles/parse",
            json={"text": _long_resume(), "source": "text_paste"},
            headers=_hdr(user_id),
        )
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    names = [e[0] for e in events]
    assert names == ["started", "result", "done"]

    started = events[0][1]
    assert isinstance(started["resource_id"], int)

    result = events[1][1]
    assert result["resource_id"] == started["resource_id"]
    assert result["url"] == f"/v1/profiles/{started['resource_id']}"
    assert events[2][1] == {"ok": True}


@pytest.mark.integration
async def test_parse_sse_pre_insert_failure_skips_started(make_app: MakeApp, user_id: int) -> None:
    """Text too short fails BEFORE any profile row exists."""
    app = make_app(_client_with_response(GOLDEN_LLM_OUTPUT))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/profiles/parse",
            json={"text": "too short", "source": "text_paste"},
            headers=_hdr(user_id),
        )
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    names = [e[0] for e in events]
    assert names == ["error", "done"]
    assert events[0][1]["code"] == "PROFILE_PARSE_FAILED"


@pytest.mark.integration
async def test_parse_sse_duplicate_user_emits_409_code(make_app: MakeApp, user_id: int) -> None:
    """Q2 decision: re-parsing without DELETE first returns PROFILE_EXISTS."""
    app = make_app(_client_with_response(GOLDEN_LLM_OUTPUT))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # First parse: success.
        await client.post(
            "/v1/profiles/parse",
            json={"text": _long_resume(), "source": "text_paste"},
            headers=_hdr(user_id),
        )

        # Second parse: should fail at create_pending (no started).
        app.dependency_overrides[_llm_dep] = lambda: _client_with_response(GOLDEN_LLM_OUTPUT)
        resp = await client.post(
            "/v1/profiles/parse",
            json={"text": _long_resume(), "source": "text_paste"},
            headers=_hdr(user_id),
        )
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    names = [e[0] for e in events]
    assert names == ["error", "done"]
    assert events[0][1]["code"] == "PROFILE_EXISTS"


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
            "/v1/profiles/parse",
            json={"text": _long_resume(), "source": "text_paste"},
            headers=_hdr(user_id),
        )
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    names = [e[0] for e in events]
    assert names == ["started", "error", "done"]
    assert events[1][1]["code"] == "LLM_UPSTREAM"


@pytest.mark.integration
async def test_parse_sse_unauthorized_when_no_user_header(make_app: MakeApp) -> None:
    app = make_app(_client_with_response(GOLDEN_LLM_OUTPUT))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/profiles/parse",
            json={"text": _long_resume(), "source": "text_paste"},
        )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /v1/profiles + GET /v1/profiles/{id}
# ---------------------------------------------------------------------------


async def _seed_profile(client: AsyncClient, app: FastAPI, uid: int) -> int:
    """Drive POST /parse via SSE and return the created resource_id."""
    app.dependency_overrides[_llm_dep] = lambda: _client_with_response(GOLDEN_LLM_OUTPUT)
    resp = await client.post(
        "/v1/profiles/parse",
        json={"text": _long_resume(), "source": "text_paste"},
        headers=_hdr(uid),
    )
    events = _parse_sse(resp.text)
    started = next(d for name, d in events if name == "started")
    return int(started["resource_id"])  # type: ignore[arg-type]


@pytest.mark.integration
async def test_list_profiles_returns_user_rows_only(
    make_app: MakeApp, user_id: int, other_user_id: int
) -> None:
    app = make_app(_client_with_response(GOLDEN_LLM_OUTPUT))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _seed_profile(client, app, user_id)
        await _seed_profile(client, app, other_user_id)

        resp = await client.get("/v1/profiles", headers=_hdr(user_id))

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1
    assert body["data"][0]["full_name"] == "张三"


@pytest.mark.integration
async def test_get_profile_detail_returns_full_structured(make_app: MakeApp, user_id: int) -> None:
    app = make_app(_client_with_response(GOLDEN_LLM_OUTPUT))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid = await _seed_profile(client, app, user_id)

        resp = await client.get(f"/v1/profiles/{pid}", headers=_hdr(user_id))

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "parsed"
    assert body["structured"]["full_name"] == "张三"
    assert len(body["structured"]["experiences"]) == 1
    assert body["structured"]["experiences"][0]["company"] == "ACME"
    assert body["stats"]["experiences"] == 1
    assert body["stats"]["skills"] == 1
    assert body["raw_text"] is not None


@pytest.mark.integration
async def test_get_profile_detail_404_for_other_user(
    make_app: MakeApp, user_id: int, other_user_id: int
) -> None:
    app = make_app(_client_with_response(GOLDEN_LLM_OUTPUT))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid = await _seed_profile(client, app, user_id)

        resp = await client.get(f"/v1/profiles/{pid}", headers=_hdr(other_user_id))

    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# PATCH + DELETE
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_patch_profile_structured_rebuilds_children(make_app: MakeApp, user_id: int) -> None:
    app = make_app(_client_with_response(GOLDEN_LLM_OUTPUT))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid = await _seed_profile(client, app, user_id)

        resp = await client.patch(
            f"/v1/profiles/{pid}",
            json={
                "structured": {
                    "full_name": "李四",
                    "skills": [{"name": "go", "name_raw": "Go", "category": "language"}],
                }
            },
            headers=_hdr(user_id),
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["structured"]["full_name"] == "李四"
    assert len(body["structured"]["skills"]) == 1
    assert body["structured"]["skills"][0]["name"] == "go"
    assert body["stats"]["experiences"] == 0


@pytest.mark.integration
async def test_delete_profile_then_reparse_succeeds(make_app: MakeApp, user_id: int) -> None:
    """Q2: DELETE clears the partial-unique block so the next POST /parse
    creates a fresh row."""
    app = make_app(_client_with_response(GOLDEN_LLM_OUTPUT))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid1 = await _seed_profile(client, app, user_id)

        resp = await client.delete(f"/v1/profiles/{pid1}", headers=_hdr(user_id))
        assert resp.status_code == 204

        get_resp = await client.get(f"/v1/profiles/{pid1}", headers=_hdr(user_id))
        assert get_resp.status_code == 404

        pid2 = await _seed_profile(client, app, user_id)
        assert pid2 != pid1
