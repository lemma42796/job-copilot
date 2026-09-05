"""异步任务(P3)对外 schema。

长任务接口的返回从"一条 SSE 流"变成"202 + job_id",前端拿 job_id 去
`GET /api/jobs/{id}/events` 订阅进度。`after_seq` 支持断线续读。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class JobAcceptedOut(BaseModel):
    """202 响应体。`resource_id` 让前端立刻跳转到已预占的资源页。"""

    job_id: int
    status: str
    kind: str
    resource_kind: str | None = None
    resource_id: int | None = None


class JobOut(BaseModel):
    id: int
    kind: str
    status: Literal[
        "queued",
        "running",
        "succeeded",
        "failed",
        "insufficient_balance",
        "deadline_exceeded",
    ]
    resource_kind: str | None = None
    resource_id: int | None = None
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_detail: str | None = None
    last_seq: int = 0
    created_at: datetime | None = None
    finished_at: datetime | None = None


class JobEventOut(BaseModel):
    seq: int
    event: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class JobEventListOut(BaseModel):
    job_id: int
    status: str
    events: list[JobEventOut]
