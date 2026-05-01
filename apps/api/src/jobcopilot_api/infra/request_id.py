"""Request-ID ASGI middleware + UUIDv7 generator + contextvar.

Outermost middleware in the stack: every downstream log line and exception
handler can read the id from `request_id_ctxvar`.
"""

from __future__ import annotations

import os
import time
from contextvars import ContextVar

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-Id"

request_id_ctxvar: ContextVar[str | None] = ContextVar("request_id", default=None)


def generate_uuid7() -> str:
    """RFC 9562 UUIDv7: 48-bit ms unix-ts + version/variant + random."""
    ts_ms = int(time.time() * 1000)
    ts_bytes = ts_ms.to_bytes(6, "big")

    rand = bytearray(os.urandom(10))
    rand[0] = (rand[0] & 0x0F) | 0x70  # version 7
    rand[2] = (rand[2] & 0x3F) | 0x80  # variant 10

    raw = ts_bytes + bytes(rand)
    return (
        f"{raw[0:4].hex()}-{raw[4:6].hex()}-{raw[6:8].hex()}-{raw[8:10].hex()}-{raw[10:16].hex()}"
    )


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Reads `X-Request-Id`, generates one if absent, binds to logging
    context, and echoes it in the response.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        rid = request.headers.get(REQUEST_ID_HEADER) or generate_uuid7()
        token = request_id_ctxvar.set(rid)
        structlog.contextvars.bind_contextvars(request_id=rid)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
            request_id_ctxvar.reset(token)
        response.headers[REQUEST_ID_HEADER] = rid
        return response
