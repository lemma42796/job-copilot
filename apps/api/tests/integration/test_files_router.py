"""Integration tests for `/v1/files` (router layer). ADR-0005 / API_SPEC §6.9.

Stack: httpx + ASGITransport drives FastAPI in-process; testcontainers
spins a real `pgvector/pgvector:pg16` so multi-statement transactions
behave as in production. Each test gets a fresh User row to keep
quotas + dedup behaviors isolated.

Coverage target: golden + 6 error branches + ETag/304 + 401/403/404.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
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
from testcontainers.postgres import PostgresContainer

from jobcopilot_api.infra.db import get_session
from jobcopilot_api.infra.upload import MAX_UPLOAD_BYTES
from jobcopilot_api.main import create_app
from jobcopilot_api.models import User

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

# ~1KB minimal-but-not-tiny PDF; comfortable for ETag / dedup checks.
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3 minimal pdf body\n" + b"x" * 1024
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


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


@pytest.fixture
async def app_with_db(sessionmaker_: async_sessionmaker[AsyncSession]) -> AsyncIterator[FastAPI]:
    app = create_app()

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker_() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = _override_session
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
async def client(app_with_db: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def user_id(sessionmaker_: async_sessionmaker[AsyncSession]) -> int:
    async with sessionmaker_() as session, session.begin():
        user = User(email="t@test", name="t", locale="zh-CN")
        session.add(user)
        await session.flush()
        return user.id


def _hdr(uid: int) -> dict[str, str]:
    return {"X-User-Id": str(uid)}


# ---------- POST /v1/files/upload ----------


@pytest.mark.integration
async def test_upload_happy_returns_201(client: AsyncClient, user_id: int) -> None:
    resp = await client.post(
        "/v1/files/upload",
        files={"file": ("jd.pdf", PDF, "application/pdf")},
        data={"purpose": "jd_pdf"},
        headers=_hdr(user_id),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["replayed"] is False
    assert body["filename"] == "jd.pdf"
    assert body["mime"] == "application/pdf"
    assert body["size_bytes"] == len(PDF)
    assert len(body["sha256"]) == 64
    assert body["url"] == f"/v1/files/{body['id']}"


@pytest.mark.integration
async def test_upload_dedup_replay_returns_200(client: AsyncClient, user_id: int) -> None:
    first = await client.post(
        "/v1/files/upload",
        files={"file": ("a.pdf", PDF, "application/pdf")},
        data={"purpose": "jd_pdf"},
        headers=_hdr(user_id),
    )
    assert first.status_code == 201

    second = await client.post(
        "/v1/files/upload",
        files={"file": ("renamed.pdf", PDF, "application/pdf")},
        data={"purpose": "jd_pdf"},
        headers=_hdr(user_id),
    )
    assert second.status_code == 200
    body = second.json()
    assert body["replayed"] is True
    assert body["id"] == first.json()["id"]
    # Original filename preserved.
    assert body["filename"] == "a.pdf"


@pytest.mark.integration
async def test_upload_mime_not_allowed_415(client: AsyncClient, user_id: int) -> None:
    resp = await client.post(
        "/v1/files/upload",
        files={"file": ("evil.exe", b"MZ\x90\x00", "application/x-msdownload")},
        data={"purpose": "other"},
        headers=_hdr(user_id),
    )
    assert resp.status_code == 415
    body = resp.json()
    assert body["code"] == "UNSUPPORTED_MEDIA"


@pytest.mark.integration
async def test_upload_magic_mismatch_415(client: AsyncClient, user_id: int) -> None:
    """Mime in whitelist but content does not match its signature."""
    resp = await client.post(
        "/v1/files/upload",
        files={"file": ("fake.pdf", PNG, "application/pdf")},
        data={"purpose": "jd_pdf"},
        headers=_hdr(user_id),
    )
    assert resp.status_code == 415
    body = resp.json()
    assert body["code"] == "UNSUPPORTED_MEDIA"


@pytest.mark.integration
async def test_upload_size_over_limit_returns_413(client: AsyncClient, user_id: int) -> None:
    """20MB cap: 1 byte over should fire 413 from the chunked reader."""
    payload = b"%PDF-1.7\n" + b"x" * (MAX_UPLOAD_BYTES + 1)
    resp = await client.post(
        "/v1/files/upload",
        files={"file": ("huge.pdf", payload, "application/pdf")},
        data={"purpose": "jd_pdf"},
        headers=_hdr(user_id),
    )
    assert resp.status_code == 413
    body = resp.json()
    assert body["code"] == "PAYLOAD_TOO_LARGE"


@pytest.mark.integration
async def test_upload_missing_user_id_header_returns_401(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/files/upload",
        files={"file": ("jd.pdf", PDF, "application/pdf")},
        data={"purpose": "jd_pdf"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


@pytest.mark.integration
async def test_upload_invalid_purpose_returns_validation_error(
    client: AsyncClient, user_id: int
) -> None:
    resp = await client.post(
        "/v1/files/upload",
        files={"file": ("jd.pdf", PDF, "application/pdf")},
        data={"purpose": "not_a_real_purpose"},
        headers=_hdr(user_id),
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "VALIDATION_ERROR"


# ---------- GET /v1/files/{id} ----------


@pytest.mark.integration
async def test_download_returns_content_with_etag_and_disposition(
    client: AsyncClient, user_id: int
) -> None:
    upload_resp = await client.post(
        "/v1/files/upload",
        files={"file": ("中文-jd.pdf", PDF, "application/pdf")},
        data={"purpose": "jd_pdf"},
        headers=_hdr(user_id),
    )
    file_id = upload_resp.json()["id"]
    sha256 = upload_resp.json()["sha256"]

    resp = await client.get(f"/v1/files/{file_id}", headers=_hdr(user_id))
    assert resp.status_code == 200
    assert resp.content == PDF
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["etag"] == f'"{sha256}"'
    assert resp.headers["cache-control"] == "private, max-age=86400"
    # RFC 6266 percent-encoded UTF-8 disposition.
    disp = resp.headers["content-disposition"]
    assert disp.startswith("attachment; filename*=UTF-8''")
    assert "%E4%B8%AD%E6%96%87" in disp  # "中文" percent-encoded


@pytest.mark.integration
async def test_download_etag_match_returns_304_no_body(client: AsyncClient, user_id: int) -> None:
    upload_resp = await client.post(
        "/v1/files/upload",
        files={"file": ("a.pdf", PDF, "application/pdf")},
        data={"purpose": "jd_pdf"},
        headers=_hdr(user_id),
    )
    file_id = upload_resp.json()["id"]
    sha256 = upload_resp.json()["sha256"]

    resp = await client.get(
        f"/v1/files/{file_id}",
        headers={**_hdr(user_id), "If-None-Match": f'"{sha256}"'},
    )
    assert resp.status_code == 304
    assert resp.content == b""
    assert resp.headers["etag"] == f'"{sha256}"'


@pytest.mark.integration
async def test_download_404_when_other_user(
    client: AsyncClient,
    user_id: int,
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    upload_resp = await client.post(
        "/v1/files/upload",
        files={"file": ("a.pdf", PDF, "application/pdf")},
        data={"purpose": "jd_pdf"},
        headers=_hdr(user_id),
    )
    file_id = upload_resp.json()["id"]

    async with sessionmaker_() as session, session.begin():
        bob = User(email="bob@test", name="bob", locale="zh-CN")
        session.add(bob)
        await session.flush()
        bob_id = bob.id

    resp = await client.get(f"/v1/files/{file_id}", headers=_hdr(bob_id))
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


@pytest.mark.integration
async def test_download_404_after_soft_delete(client: AsyncClient, user_id: int) -> None:
    upload_resp = await client.post(
        "/v1/files/upload",
        files={"file": ("a.pdf", PDF, "application/pdf")},
        data={"purpose": "jd_pdf"},
        headers=_hdr(user_id),
    )
    file_id = upload_resp.json()["id"]

    delete_resp = await client.delete(f"/v1/files/{file_id}", headers=_hdr(user_id))
    assert delete_resp.status_code == 204

    resp = await client.get(f"/v1/files/{file_id}", headers=_hdr(user_id))
    assert resp.status_code == 404


# ---------- DELETE /v1/files/{id} ----------


@pytest.mark.integration
async def test_delete_returns_204(client: AsyncClient, user_id: int) -> None:
    upload_resp = await client.post(
        "/v1/files/upload",
        files={"file": ("a.pdf", PDF, "application/pdf")},
        data={"purpose": "jd_pdf"},
        headers=_hdr(user_id),
    )
    file_id = upload_resp.json()["id"]

    resp = await client.delete(f"/v1/files/{file_id}", headers=_hdr(user_id))
    assert resp.status_code == 204
    assert resp.content == b""


@pytest.mark.integration
async def test_delete_idempotent_call_returns_404(client: AsyncClient, user_id: int) -> None:
    """Second delete on the same row → 404. Routers do not silently
    swallow this; clients can map to no-op if they want."""
    upload_resp = await client.post(
        "/v1/files/upload",
        files={"file": ("a.pdf", PDF, "application/pdf")},
        data={"purpose": "jd_pdf"},
        headers=_hdr(user_id),
    )
    file_id = upload_resp.json()["id"]

    first = await client.delete(f"/v1/files/{file_id}", headers=_hdr(user_id))
    assert first.status_code == 204

    second = await client.delete(f"/v1/files/{file_id}", headers=_hdr(user_id))
    assert second.status_code == 404
    assert second.json()["code"] == "NOT_FOUND"


@pytest.mark.integration
async def test_delete_404_when_other_user(
    client: AsyncClient,
    user_id: int,
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    upload_resp = await client.post(
        "/v1/files/upload",
        files={"file": ("a.pdf", PDF, "application/pdf")},
        data={"purpose": "jd_pdf"},
        headers=_hdr(user_id),
    )
    file_id = upload_resp.json()["id"]

    async with sessionmaker_() as session, session.begin():
        bob = User(email="bob2@test", name="bob", locale="zh-CN")
        session.add(bob)
        await session.flush()
        bob_id = bob.id

    resp = await client.delete(f"/v1/files/{file_id}", headers=_hdr(bob_id))
    assert resp.status_code == 404
