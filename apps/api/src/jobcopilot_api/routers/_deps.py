"""Cross-cutting FastAPI dependencies for routers.

Lives at `_deps` (underscore-prefixed) so it stays clearly internal:
routers import from here, but tests / external callers should not.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Header

from jobcopilot_api.errors import JobCopilotError


class UnauthorizedError(JobCopilotError):
    """Caller did not present a valid `X-User-Id` (v1 local-single-user)."""

    status_code = 401
    code = "UNAUTHORIZED"
    title = "未认证"


async def current_user_id(
    x_user_id: Annotated[int | None, Header(alias="X-User-Id")] = None,
) -> int:
    """v1 single-user local pragma: caller passes `X-User-Id`.

    The dep signature is what M5+ JWT extraction will replace; routes
    keep using `Depends(current_user_id)` and don't change.
    """
    if x_user_id is None or x_user_id <= 0:
        raise UnauthorizedError("missing or invalid X-User-Id header")
    return x_user_id
