"""CacheStore — LLM response 客户端缓存(S21 子任务 4-B)。

DashScope 没有 Anthropic 那种 server-side prompt caching;评测和 dogfood
阶段重复跑同 prompt 成本线性放大,客户端 response cache 直接降一个数量级。

设计与 `CallLogger` 同形:Protocol + Postgres 实现 + Noop 默认。`PostgresCacheStore`
用**独立** `async_sessionmaker`(与请求链路的 session 隔离),业务事务回滚不
牵连缓存。任何 DB 异常被吞掉(WARNING 一行) — cache 故障必须降级为
"miss",不能让缓存层把 LLMClient 砸了。

Streaming 路径(`on_token` 非 None)在 `BaseLLMClient` 入口就 skip cache,
不会走到这里。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class CachedResponse:
    """命中后从 cache 还原的 ProviderResponse 等价物 + 原始 model。

    `model` 单独带回是因为命中场景下 LLMClient 不会再走 `tier_to_model`
    做下发(tier 仍然是请求侧的,model 是写入时的快照),logger 行用写入
    时的 model 对账。
    """

    content: str
    tokens_in: int
    tokens_out: int
    cached_tokens: int
    model: str


class CacheStore(Protocol):
    async def get(self, cache_key: str) -> CachedResponse | None: ...

    async def put(
        self,
        *,
        cache_key: str,
        model: str,
        feature: str,
        prompt_version_id: int | None,
        request: dict[str, Any],
        response: dict[str, Any],
    ) -> None: ...


class NoopCacheStore:
    """禁用缓存 — 任何 get 返回 None,put 是空操作。`infra.llm` 在
    `JOBCOPILOT_LLM_CACHE_ENABLED=false` 时注入此实现。"""

    async def get(self, cache_key: str) -> CachedResponse | None:
        return None

    async def put(
        self,
        *,
        cache_key: str,
        model: str,
        feature: str,
        prompt_version_id: int | None,
        request: dict[str, Any],
        response: dict[str, Any],
    ) -> None:
        return None


_GET_SQL = text(
    """
    UPDATE llm_response_cache
       SET hit_count = hit_count + 1,
           last_hit_at = now()
     WHERE cache_key = :cache_key
     RETURNING response, model
    """
)

_PUT_SQL = text(
    """
    INSERT INTO llm_response_cache
        (cache_key, model, feature, prompt_version_id, request, response)
    VALUES
        (:cache_key, :model, :feature, :prompt_version_id,
         CAST(:request AS jsonb), CAST(:response AS jsonb))
    ON CONFLICT (cache_key) DO NOTHING
    """
)


class PostgresCacheStore:
    """Postgres 实现。

    `get` 用单条 `UPDATE ... RETURNING` 同时读 response 和 推 hit_count /
    last_hit_at — 比"先 SELECT 再 UPDATE"省一往返,且并发命中下计数无丢
    失风险(同行原子 UPDATE)。

    `put` 走 `INSERT ... ON CONFLICT DO NOTHING`:并发 miss(两个调用同
    时算出同 key)各自 PUT 一次,后到的不覆盖先到的;两条 response 在同
    key 下应该等价(同 prompt → 同 LLM 决策),保留首条 OK。
    """

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def get(self, cache_key: str) -> CachedResponse | None:
        try:
            async with self._sessionmaker() as session:
                row = (
                    await session.execute(_GET_SQL, {"cache_key": cache_key})
                ).first()
                await session.commit()
        except Exception as exc:
            log.warning("llm_cache_get_failed", cache_key=cache_key, error=str(exc))
            return None

        if row is None:
            return None
        response = row[0]
        model = row[1]
        return CachedResponse(
            content=str(response.get("content", "")),
            tokens_in=int(response.get("tokens_in", 0)),
            tokens_out=int(response.get("tokens_out", 0)),
            cached_tokens=int(response.get("cached_tokens", 0)),
            model=str(model),
        )

    async def put(
        self,
        *,
        cache_key: str,
        model: str,
        feature: str,
        prompt_version_id: int | None,
        request: dict[str, Any],
        response: dict[str, Any],
    ) -> None:
        try:
            async with self._sessionmaker() as session:
                await session.execute(
                    _PUT_SQL,
                    {
                        "cache_key": cache_key,
                        "model": model,
                        "feature": feature,
                        "prompt_version_id": prompt_version_id,
                        "request": json.dumps(request, ensure_ascii=False),
                        "response": json.dumps(response, ensure_ascii=False),
                    },
                )
                await session.commit()
        except Exception as exc:
            log.warning(
                "llm_cache_put_failed",
                cache_key=cache_key,
                feature=feature,
                error=str(exc),
            )
