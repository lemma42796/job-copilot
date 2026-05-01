"""JobCopilotError + RFC 7807 exception handler."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient

from jobcopilot_api.errors import NotFoundError
from jobcopilot_api.main import create_app


@pytest.fixture
def app() -> FastAPI:
    """Override the conftest `app` fixture to add throwaway routes."""
    app = create_app()
    router = APIRouter()

    @router.get("/test/notfound")
    async def _raise_not_found() -> None:
        raise NotFoundError("widget 42 not found")

    @router.get("/test/coerce")
    async def _coerce(x: int) -> dict[str, int]:
        return {"x": x}

    app.include_router(router)
    return app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_jobcopilot_error__renders_problem_json(
    client: AsyncClient,
) -> None:
    response = await client.get("/test/notfound")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")

    body = response.json()
    assert body["code"] == "NOT_FOUND"
    assert body["status"] == 404
    assert body["title"] == "未找到该资源"
    assert body["detail"] == "widget 42 not found"
    assert body["instance"] == "/test/notfound"
    assert body["request_id"] is not None
    assert body["type"].endswith("/errors/not-found")


@pytest.mark.asyncio
async def test_validation_error__renders_problem_json_with_field_errors(
    client: AsyncClient,
) -> None:
    response = await client.get("/test/coerce", params={"x": "not-an-int"})
    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")

    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["errors"]
    assert any("x" in err["field"] for err in body["errors"])


@pytest.mark.asyncio
async def test_unmatched_route__renders_problem_json_404(
    client: AsyncClient,
) -> None:
    response = await client.get("/v1/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "NOT_FOUND"
    assert body["status"] == 404
