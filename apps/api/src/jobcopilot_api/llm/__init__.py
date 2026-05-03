"""LLM abstraction layer (ADR-0004)."""

from __future__ import annotations

from jobcopilot_api.llm.client import (
    BaseLLMClient,
    CallLogger,
    LLMClient,
    LLMRequest,
    LLMResult,
    MemoryCallLogger,
    NoopCallLogger,
    Provider,
    ProviderRequest,
    ProviderResponse,
)
from jobcopilot_api.llm.db_logger import DBCallLogger
from jobcopilot_api.llm.embedders import (
    DashscopeEmbedder,
    DummyEmbedder,
    Embedder,
    EmbeddingResult,
    embed_cost_for,
)
from jobcopilot_api.llm.errors import (
    LLMAuthError,
    LLMError,
    LLMSchemaInvalidError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from jobcopilot_api.llm.pricing import Pricing, cost_for, price_table
from jobcopilot_api.llm.tiers import Tier, tier_to_model

__all__ = [
    "BaseLLMClient",
    "CallLogger",
    "DBCallLogger",
    "DashscopeEmbedder",
    "DummyEmbedder",
    "Embedder",
    "EmbeddingResult",
    "LLMAuthError",
    "LLMClient",
    "LLMError",
    "LLMRequest",
    "LLMResult",
    "LLMSchemaInvalidError",
    "LLMTimeoutError",
    "LLMUpstreamError",
    "MemoryCallLogger",
    "NoopCallLogger",
    "Pricing",
    "Provider",
    "ProviderRequest",
    "ProviderResponse",
    "Tier",
    "cost_for",
    "embed_cost_for",
    "price_table",
    "tier_to_model",
]
