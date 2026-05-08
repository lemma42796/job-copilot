"""SSE 事件统一 schema(4-API_SPEC §2.3)。

通用事件序列(每个 SSE 端点都遵守):
- started:资源 INSERT 完拿到 id 后立即推
- progress:中间进度(各端点 data schema 不同)
- result:终态结果(可选,有的端点用 done 直接带结果)
- error:任意阶段失败(后接 done)
- done:收尾(必发,失败也要发)

永久约束 #4:不在 SSE response header 写非 ASCII 字符到 resource_id。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

EventName = Literal["started", "progress", "result", "error", "done"]


class StartedEvent(BaseModel):
    job_id: str
    resource_id: int


class ErrorEvent(BaseModel):
    code: str
    detail: str | None = None


class DoneEvent(BaseModel):
    ok: bool


class ProgressEvent(BaseModel):
    """各端点 progress 的 data 形态不同,公共 base 留扩展点。"""

    stage: str
    data: dict[str, Any] = Field(default_factory=dict)
