"""Health endpoint tests."""

from __future__ import annotations

from httpx import AsyncClient


async def test_health__get__returns_ok(client: AsyncClient) -> None:
    resp = await client.get("/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert "timestamp" in body
