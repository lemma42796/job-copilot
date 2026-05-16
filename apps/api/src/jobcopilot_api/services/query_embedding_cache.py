"""Query embedding cache for hybrid search.

Chunk embeddings already live on `note_chunks`; this cache is only for the
short query vectors produced before vector search. It reuses
`llm_response_cache` so repeated eval / dogfood queries can skip the upstream
embedding call without adding another table.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import text

from jobcopilot_api.agents.embedder.agent import EMBED_VERSION
from jobcopilot_api.infra.db import get_sessionmaker
from jobcopilot_api.infra.embedder import get_embedder
from jobcopilot_api.llm.embedders import (
    EMBED_DIMENSIONS,
    EmbeddingResult,
)
from jobcopilot_api.settings import settings

log = structlog.get_logger(__name__)

FEATURE = "query_embedding"

_GET_SQL = text(
    """
    UPDATE llm_response_cache
       SET hit_count = hit_count + 1,
           last_hit_at = now()
     WHERE cache_key = :cache_key
       AND feature = :feature
     RETURNING response, model
    """
)

_PUT_SQL = text(
    """
    INSERT INTO llm_response_cache
        (cache_key, model, feature, prompt_version_id, request, response)
    VALUES
        (:cache_key, :model, :feature, NULL,
         CAST(:request AS jsonb), CAST(:response AS jsonb))
    ON CONFLICT (cache_key) DO NOTHING
    """
)


class QueryEmbeddingCacheMissError(RuntimeError):
    """Raised when query embedding cache-only mode cannot satisfy a query."""


async def embed_query_cached(query: str) -> EmbeddingResult:
    """Embed one search query, using a persistent exact-query cache.

    The cache key includes normalized query text, model, embedding version, and
    dimensions. In normal product traffic, cache misses degrade to a live
    embedding call. Eval/smoke can turn on cache-only mode to prevent accidental
    provider calls during repeated runs.
    """
    normalized_query = normalize_query(query)
    embedder = get_embedder()
    model = embedder.model
    dimensions = int(getattr(embedder, "dimensions", EMBED_DIMENSIONS))
    cache_key = compute_query_embedding_cache_key(
        normalized_query=normalized_query,
        model=model,
        dimensions=dimensions,
    )

    if settings.llm_cache_enabled:
        cached = await _get_cached_embedding(
            cache_key=cache_key,
            model=model,
            expected_dimensions=dimensions,
        )
        if cached is not None:
            return cached

    if settings.query_embedding_cache_only:
        raise QueryEmbeddingCacheMissError(
            "query embedding cache miss in cache-only mode: "
            f"query={normalized_query!r}, model={model}, "
            f"dimensions={dimensions}, embed_version={EMBED_VERSION}"
        )

    result = await embedder.embed([normalized_query])
    if settings.llm_cache_enabled and result.vectors:
        await _put_cached_embedding(
            cache_key=cache_key,
            model=result.model,
            raw_query=query,
            normalized_query=normalized_query,
            dimensions=dimensions,
            result=result,
        )
    return result


def normalize_query(query: str) -> str:
    """Collapse accidental whitespace without changing case or wording."""
    return " ".join(query.split())


def compute_query_embedding_cache_key(
    *,
    normalized_query: str,
    model: str,
    dimensions: int,
) -> str:
    payload = {
        "kind": FEATURE,
        "query": normalized_query,
        "model": model,
        "embed_version": EMBED_VERSION,
        "dimensions": dimensions,
    }
    text = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _get_cached_embedding(
    *,
    cache_key: str,
    model: str,
    expected_dimensions: int,
) -> EmbeddingResult | None:
    try:
        async with get_sessionmaker()() as session:
            row = (
                await session.execute(
                    _GET_SQL,
                    {"cache_key": cache_key, "feature": FEATURE},
                )
            ).first()
            await session.commit()
    except Exception as exc:
        log.warning(
            "query_embedding_cache_get_failed",
            cache_key=cache_key,
            error=str(exc),
        )
        return None

    if row is None:
        return None
    response = row[0]
    cached_model = str(row[1] or model)
    try:
        vector = _vector_from_response(response, expected_dimensions)
        tokens_in = int(response.get("tokens_in", 0))
    except (TypeError, ValueError) as exc:
        log.warning(
            "query_embedding_cache_invalid",
            cache_key=cache_key,
            error=str(exc),
        )
        return None
    return EmbeddingResult(
        vectors=[vector],
        tokens_in=tokens_in,
        model=cached_model,
        cost_cny=Decimal("0"),
    )


async def _put_cached_embedding(
    *,
    cache_key: str,
    model: str,
    raw_query: str,
    normalized_query: str,
    dimensions: int,
    result: EmbeddingResult,
) -> None:
    request = {
        "query": raw_query,
        "normalized_query": normalized_query,
        "embed_version": EMBED_VERSION,
        "dimensions": dimensions,
    }
    response = {
        "vector": result.vectors[0],
        "tokens_in": result.tokens_in,
        "cost_cny": str(result.cost_cny),
        "embed_version": EMBED_VERSION,
        "dimensions": dimensions,
    }
    try:
        async with get_sessionmaker()() as session:
            await session.execute(
                _PUT_SQL,
                {
                    "cache_key": cache_key,
                    "model": model,
                    "feature": FEATURE,
                    "request": json.dumps(request, ensure_ascii=False),
                    "response": json.dumps(response, ensure_ascii=False),
                },
            )
            await session.commit()
    except Exception as exc:
        log.warning(
            "query_embedding_cache_put_failed",
            cache_key=cache_key,
            error=str(exc),
        )


def _vector_from_response(
    response: Any,
    expected_dimensions: int,
) -> list[float]:
    if not isinstance(response, dict):
        raise TypeError("cached response is not a JSON object")
    vector = response.get("vector")
    if not isinstance(vector, list):
        raise TypeError("cached vector is not a list")
    if len(vector) != expected_dimensions:
        raise ValueError(f"cached vector dimension={len(vector)}")
    return [float(x) for x in vector]
