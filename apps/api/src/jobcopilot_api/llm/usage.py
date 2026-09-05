"""非文本生成链路的统一记账入口(P1)。

`llm/db_logger.py` 只覆盖走 `LLMClient` 的文本生成。rerank 与 embedding
不产生 `LLMResult`,但同样花钱,所以它们通过本模块落同一张 `llm_calls`
表并扣同一份余额 —— 三条链路的成本归因保持在一个地方。

`tier` 列在这两条链路上没有语义,统一写 `"none"`;`feature` 分别是
`rerank` / `embedding`,`channel` 由调用方传,决定流水里的链路维度。
"""

from __future__ import annotations

from decimal import Decimal

import structlog

from jobcopilot_api.infra.db import get_sessionmaker
from jobcopilot_api.models.llm_call import LlmCall

log = structlog.get_logger(__name__)

FEATURE_RERANK = "rerank"
FEATURE_EMBEDDING = "embedding"


async def record_usage(
    *,
    user_id: int | None,
    feature: str,
    channel: str,
    model: str,
    tokens_in: int,
    tokens_out: int = 0,
    cost_cny: Decimal,
    latency_ms: int,
    success: bool,
    error_code: str | None = None,
    related_entity: str | None = None,
    related_id: int | None = None,
    metadata: dict | None = None,
) -> None:
    """写一行 `llm_calls` 并按 `cost_cny` 实扣余额。

    与 `DBCallLogger` 一样是 best-effort:记账失败只记 WARNING,不能把
    已经成功的上游调用带崩。
    """
    from jobcopilot_api.services import billing_service

    llm_call_id: int | None = None
    try:
        async with get_sessionmaker()() as session:
            record = LlmCall(
                user_id=user_id,
                feature=feature,
                tier="none",
                model=model,
                thinking_mode=False,
                tokens_in=tokens_in,
                cached_tokens=0,
                cache_creation_input_tokens=0,
                tokens_out=tokens_out,
                cost_cny=cost_cny,
                latency_ms=latency_ms,
                success=success,
                error_code=error_code,
                cached=False,
                metadata_json=metadata or {},
                related_entity=related_entity,
                related_id=related_id,
            )
            session.add(record)
            await session.commit()
            llm_call_id = record.id
    except Exception as exc:
        log.warning(
            "usage_log_failed",
            feature=feature,
            model=model,
            cost_cny=str(cost_cny),
            error=str(exc),
        )

    await billing_service.charge(
        user_id=user_id,
        cost_cny=cost_cny,
        channel=channel,
        feature=feature,
        llm_call_id=llm_call_id,
    )
