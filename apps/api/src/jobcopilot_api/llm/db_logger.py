"""`DBCallLogger` — persists `LLMResult` to `llm_calls` and charges balance.

ADR-0004 D4 in code form:

- Uses an **independent** `AsyncSession` from a sessionmaker that is *not*
  the one a request handler is currently using. That way a business-level
  `session.rollback()` cannot take the cost log down with it.
- Synchronous w.r.t. `LLMClient.complete` — the `await` here completes
  before the response returns to the agent. (We considered fire-and-forget
  via `asyncio.create_task`, but FastAPI cancels pending tasks when the
  request ends, which would silently drop logs — see ADR-0004 §修正 Q4.)
- Total fault tolerance: any exception from the DB layer is swallowed and
  logged at WARNING. The cost log is best-effort; we never want a logging
  failure to break a successful LLM call.

P1:落账后按 `cost_cny` 实扣用户余额,流水关联到刚写入的 `llm_calls.id`。
缓存命中的调用 `cost_cny` 为 0,不产生扣费 — 用户天然受益于缓存。
本类是**文本生成**这条链路的记账点;rerank 与 embedding 各自在
`services/reranker.py` / `llm/embedders.py` 里走同一套 `record_usage`。
"""

from __future__ import annotations

from decimal import Decimal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobcopilot_api.llm.client import LLMResult
from jobcopilot_api.models.llm_call import LlmCall

log = structlog.get_logger(__name__)


class DBCallLogger:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._sessionmaker = sessionmaker

    def _get_sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        if self._sessionmaker is not None:
            return self._sessionmaker
        from jobcopilot_api.infra.db import get_sessionmaker

        return get_sessionmaker()

    async def log(self, result: LLMResult) -> None:
        from jobcopilot_api.services import billing_service

        llm_call_id: int | None = None
        try:
            async with self._get_sessionmaker()() as session:
                record = _to_record(result)
                session.add(record)
                await session.commit()
                llm_call_id = record.id
        except Exception as exc:
            log.warning(
                "llm_call_log_failed",
                feature=result.feature,
                tier=result.tier.value,
                model=result.model,
                error=str(exc),
            )

        await billing_service.charge(
            user_id=result.user_id,
            cost_cny=Decimal(result.cost_cny),
            channel=billing_service.CHANNEL_GENERATION,
            feature=result.feature,
            llm_call_id=llm_call_id,
        )


def _to_record(result: LLMResult) -> LlmCall:
    return LlmCall(
        user_id=result.user_id,
        feature=result.feature,
        tier=result.tier.value,
        model=result.model,
        thinking_mode=result.thinking_mode,
        tokens_in=result.tokens_in,
        cached_tokens=result.cached_tokens,
        cache_creation_input_tokens=result.cache_creation_input_tokens,
        tokens_out=result.tokens_out,
        cost_cny=result.cost_cny,
        latency_ms=result.latency_ms,
        success=result.success,
        error_code=result.error_code,
        cached=result.cached,
        metadata_json=result.metadata,
        trace_id=result.trace_id,
        related_entity=result.related_entity,
        related_id=result.related_id,
        prompt_version_id=result.prompt_version_id,
    )
