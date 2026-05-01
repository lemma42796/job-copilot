"""Health check router."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from jobcopilot_api import __version__

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str = Field(description="API package version")
    env: str = Field(description="Runtime environment")
    timestamp: datetime = Field(description="Server time (UTC)")


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def get_health() -> HealthResponse:
    from jobcopilot_api.settings import settings

    return HealthResponse(
        status="ok",
        version=__version__,
        env=settings.env,
        timestamp=datetime.now(UTC),
    )
