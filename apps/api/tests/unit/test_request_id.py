"""RequestIDMiddleware + UUIDv7 generator."""

from __future__ import annotations

import re

import pytest
from httpx import AsyncClient

from jobcopilot_api.infra.request_id import REQUEST_ID_HEADER, generate_uuid7

UUIDV7_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def test_generate_uuid7__has_version_and_variant_bits() -> None:
    rid = generate_uuid7()
    assert UUIDV7_RE.match(rid), rid


@pytest.mark.asyncio
async def test_request_id__missing_header__server_generates_uuid7(
    client: AsyncClient,
) -> None:
    response = await client.get("/v1/health")
    assert response.status_code == 200
    rid = response.headers.get(REQUEST_ID_HEADER)
    assert rid is not None
    assert UUIDV7_RE.match(rid), rid


@pytest.mark.asyncio
async def test_request_id__inbound_header__echoed_verbatim(
    client: AsyncClient,
) -> None:
    response = await client.get("/v1/health", headers={REQUEST_ID_HEADER: "trace-from-client"})
    assert response.headers.get(REQUEST_ID_HEADER) == "trace-from-client"
