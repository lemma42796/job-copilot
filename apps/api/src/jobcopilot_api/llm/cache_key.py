"""Cache key 计算(S21 子任务 4-B)。

`compute_cache_key` 把决定 LLM 输出的所有显式输入折成一个 sha256 hex —
任一字段变了就是新 key,旧 cache 自然失效,无 TTL / 无版本号迁移负担。

入参刻意选 *经 `_augment_with_schema` 之后* 的 user 文本,保证 schema 变
更(Pydantic 模型加字段 / 改约束)直接换 key,不需要外部 cache busting。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def compute_cache_key(
    *,
    model: str,
    system: str,
    user: str,
    response_format: dict[str, Any] | None,
    thinking_mode: bool,
    prompt_version_id: int | None,
    reasoning_effort: str | None = None,
    temperature: float | None = None,
) -> str:
    payload = {
        "model": model,
        "system": system,
        "user": user,
        "response_format": response_format,
        "thinking_mode": thinking_mode,
        "reasoning_effort": reasoning_effort,
        "prompt_version_id": prompt_version_id,
        "temperature": temperature,
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
