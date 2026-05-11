"""`DBCallLogger` — persists `LLMResult` to the `llm_calls` table.

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
"""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobcopilot_api.llm.client import LLMResult
from jobcopilot_api.models.llm_call import LlmCall

log = structlog.get_logger(__name__)


class DBCallLogger:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def log(self, result: LLMResult) -> None:
        try:
            async with self._sessionmaker() as session:
                session.add(_to_record(result))
                await session.commit()
        except Exception as exc:
            log.warning(
                "llm_call_log_failed",
                feature=result.feature,
                tier=result.tier.value,
                model=result.model,
                error=str(exc),
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
