"""LLMClient + Provider Protocols + BaseLLMClient (ADR-0004).

Layering:

  Agent  ──>  LLMClient (Protocol)  ──>  BaseLLMClient (concrete)
                                              │
                                              ├─> Provider (Protocol)
                                              │     └─> Dashscope / Dummy
                                              └─> CallLogger (Protocol)
                                                    └─> Noop / Memory / DB

Agents only see `LLMClient`. Tests inject a `BaseLLMClient` whose Provider
is a `DummyProvider` and whose CallLogger is `MemoryCallLogger` — that way
the entire client logic (retry, JSON repair, cost) is exercised end-to-end
without touching the network.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from time import monotonic
from typing import Any, Protocol

import structlog
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_random,
)
from tenacity.wait import wait_base

from jobcopilot_api.llm.cache_key import compute_cache_key
from jobcopilot_api.llm.cache_store import CacheStore, NoopCacheStore
from jobcopilot_api.llm.errors import (
    LLMAuthError,
    LLMSchemaInvalidError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from jobcopilot_api.llm.pricing import cost_for
from jobcopilot_api.llm.tiers import Tier, tier_to_model

logger = structlog.get_logger(__name__)

# ---------- Streaming ----------

OnTokenCallback = Callable[[str], Awaitable[None]]
"""Per-delta async callback for streaming generation. Provider implementations
that support streaming will invoke this for every token chunk; the final
`ProviderResponse` returned from `Provider.complete` still contains the full
content + usage so existing aggregation (cost / logger) is unaffected.

Used by `resume_drafter` for live preview SSE; other agents pass `None`."""

ChatMessage = dict[str, Any]

# ---------- Provider layer (thin SDK wrapper) ----------


@dataclass(frozen=True)
class ProviderRequest:
    model: str
    system: str
    user: str
    response_format: dict[str, Any] | None  # {"type": "json_object"} or None
    thinking_mode: bool
    timeout_s: float
    max_tokens: int = 4096
    temperature: float | None = None  # docs/TECH_DESIGN.md:agent 显式传不依赖默认
    messages: list[ChatMessage] | None = None


@dataclass(frozen=True)
class ProviderResponse:
    content: str
    tokens_in: int
    tokens_out: int
    cached_tokens: int
    cache_creation_input_tokens: int = 0


class Provider(Protocol):
    """SDK-level wrapper. Maps provider errors to LLM* exceptions.

    `on_token`(可选)非 None 时 provider 走 streaming 路径,每个 delta
    回调一次;返回值仍是完整 `ProviderResponse`(content + usage),与非流
    式语义一致。Provider 不支持流式时**也允许**忽略 `on_token`,但 ADR-0004
    生产路径上的 DashScope 必须实现。"""

    async def complete(
        self,
        request: ProviderRequest,
        *,
        on_token: OnTokenCallback | None = None,
    ) -> ProviderResponse: ...


# ---------- LLMClient layer (agent-facing) ----------


@dataclass(frozen=True)
class LLMResult:
    content: str
    parsed: BaseModel | None
    tokens_in: int
    tokens_out: int
    cached_tokens: int
    cost_cny: Decimal
    latency_ms: int
    model: str
    feature: str
    tier: Tier
    thinking_mode: bool
    success: bool
    error_code: str | None
    user_id: int | None
    trace_id: str | None
    related_entity: str | None
    related_id: int | None
    prompt_version_id: int | None
    cached: bool = False
    """True iff this result came from `CacheStore` (no upstream call). On
    cache hit `cost_cny`/`tokens_*` are zeroed — analytics that want
    "would-have-cost" should reconstruct from `llm_response_cache.response`."""
    cache_creation_input_tokens: int = 0
    """Provider-side explicit cache creation input tokens, if reported."""
    metadata: dict[str, Any] = field(default_factory=dict)
    """Agent-specific runtime metadata that should not be persisted in
    `llm_calls`, e.g. tool ref-id maps used by a service post-processor."""


@dataclass
class LLMRequest:
    """Bundled keyword args for `LLMClient.complete`. Kept as a dataclass so
    callers can build it once and re-use for retry-style flows; agents that
    only call once should use the keyword-only `complete(...)` form."""

    feature: str
    tier: Tier
    system: str
    user: str
    messages: list[ChatMessage] | None = None
    response_schema: type[BaseModel] | None = None
    cache_system: bool = True
    timeout_s: float | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    user_id: int | None = None
    trace_id: str | None = None
    related_entity: str | None = None
    related_id: int | None = None
    prompt_version_id: int | None = None


class LLMClient(Protocol):
    """Agent-facing surface. Implementations: `BaseLLMClient`.

    `on_token` 仅 drafter 链路使用(简历正文,plain text,无 schema);设
    schema + on_token 同时存在的语义未定义(若校验失败二次重试时 token 流
    会再来一遍),agent 层应避免这种组合。"""

    async def complete(
        self,
        *,
        feature: str,
        tier: Tier,
        system: str,
        user: str,
        messages: list[ChatMessage] | None = None,
        response_schema: type[BaseModel] | None = None,
        cache_system: bool = True,
        timeout_s: float | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        user_id: int | None = None,
        trace_id: str | None = None,
        related_entity: str | None = None,
        related_id: int | None = None,
        prompt_version_id: int | None = None,
        on_token: OnTokenCallback | None = None,
    ) -> LLMResult: ...


# ---------- CallLogger (commit D ships Noop + Memory; commit E adds DB) ----------


class CallLogger(Protocol):
    async def log(self, result: LLMResult) -> None: ...


class NoopCallLogger:
    """Default — drops the result. Used in environments where llm_calls is
    not wired (e.g. CLI scripts, ad-hoc REPLs)."""

    async def log(self, result: LLMResult) -> None:
        return None


@dataclass
class MemoryCallLogger:
    """In-memory accumulator for tests."""

    calls: list[LLMResult] = field(default_factory=list)

    async def log(self, result: LLMResult) -> None:
        self.calls.append(result)


# ---------- BaseLLMClient ----------


_SCHEMA_RETRY_PREAMBLE = (
    "Your previous response was not valid JSON matching the expected schema. "
    "Strictly conform to this JSON Schema and return a single JSON object only:\n"
)


def _augment_with_schema(user: str, schema: type[BaseModel]) -> str:
    """Inline the schema into the user message for json_object mode.

    DashScope's OpenAI-compat endpoint (as of 2026-05) does not accept
    `response_format={"type":"json_schema"}`; we ride on `json_object` and
    pass the schema in the prompt. The Pydantic class is the source of
    truth — `model_json_schema()` is regenerated each call to keep this
    in sync if the schema evolves.
    """
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)
    return f"{user}\n\nRespond with a single JSON object that matches this schema:\n{schema_json}"


def _augment_messages_with_schema(
    messages: list[ChatMessage],
    schema: type[BaseModel],
) -> list[ChatMessage]:
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)
    return [
        *messages,
        {
            "role": "user",
            "content": (
                "Respond with a single JSON object that matches this schema:\n"
                f"{schema_json}"
            ),
        },
    ]


def _retry_prompt(user: str, schema: type[BaseModel], bad_content: str) -> str:
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)
    return (
        f"{user}\n\n"
        f"{_SCHEMA_RETRY_PREAMBLE}{schema_json}\n\n"
        f"Your previous (invalid) response was:\n{bad_content}"
    )


def _retry_messages(
    messages: list[ChatMessage],
    schema: type[BaseModel],
    bad_content: str,
) -> list[ChatMessage]:
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)
    return [
        *messages,
        {
            "role": "user",
            "content": (
                f"{_SCHEMA_RETRY_PREAMBLE}{schema_json}\n\n"
                f"Your previous (invalid) response was:\n{bad_content}"
            ),
        },
    ]


def _messages_cache_key_text(messages: list[ChatMessage]) -> str:
    return json.dumps(
        messages,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _parse(content: str, schema: type[BaseModel]) -> BaseModel:
    data = json.loads(content)
    return schema.model_validate(data)


def _error_code_of(exc: BaseException) -> str:
    if isinstance(exc, LLMTimeoutError):
        return "timeout"
    if isinstance(exc, LLMUpstreamError):
        return f"upstream_{exc.upstream_status_code}"
    if isinstance(exc, LLMAuthError):
        return "auth"
    if isinstance(exc, LLMSchemaInvalidError):
        return "schema_invalid"
    return "unknown"


@dataclass
class _CallAccumulator:
    """Mutable scratch space carried through the call so `finally` can build
    the final LLMResult even on failure."""

    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0
    cache_creation_input_tokens: int = 0
    content: str = ""
    parsed: BaseModel | None = None
    schema_attempt: int = 0  # 0 first try, 1 retry-with-schema-reminder


DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_WAIT: wait_base = wait_exponential(multiplier=0.5, max=4) + wait_random(0, 0.5)


class BaseLLMClient:
    """Default `LLMClient` implementation.

    Behavior follows ADR-0004:
    - tier → (model, thinking_mode, timeout) via `tiers.tier_to_model`
    - tenacity retry on `LLMTimeoutError` / `LLMUpstreamError`
      (3 attempts, exp backoff + jitter)
    - JSON-schema repair: 1 single retry with the schema appended,
      *outside* tenacity (re-prompting is not the same as retrying)
    - cost: local computation via `pricing.cost_for`
    - logging: every call writes exactly one row through `CallLogger`,
      regardless of how many tenacity / schema attempts were made

    `retry_attempts` / `retry_wait` are injectable so tests can disable the
    backoff. Production callers should leave them at the ADR-0004 defaults.
    """

    def __init__(
        self,
        *,
        provider: Provider,
        logger: CallLogger | None = None,
        cache_store: CacheStore | None = None,
        retry_attempts: int = DEFAULT_RETRY_ATTEMPTS,
        retry_wait: wait_base = DEFAULT_RETRY_WAIT,
        max_concurrency: int | None = None,
        concurrency_gate_factory: Callable[[], asyncio.Semaphore] | None = None,
    ) -> None:
        if max_concurrency is not None and max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        if max_concurrency is not None and concurrency_gate_factory is not None:
            raise ValueError(
                "pass max_concurrency or concurrency_gate_factory, not both"
            )
        self._provider = provider
        self._logger: CallLogger = logger if logger is not None else NoopCallLogger()
        self._cache_store: CacheStore = (
            cache_store if cache_store is not None else NoopCacheStore()
        )
        self._retry_attempts = retry_attempts
        self._retry_wait = retry_wait
        local_gate = (
            asyncio.Semaphore(max_concurrency) if max_concurrency is not None else None
        )
        self._concurrency_gate_factory = concurrency_gate_factory or (
            (lambda: local_gate) if local_gate is not None else None
        )

    async def complete(
        self,
        *,
        feature: str,
        tier: Tier,
        system: str = "",
        user: str = "",
        messages: list[ChatMessage] | None = None,
        response_schema: type[BaseModel] | None = None,
        cache_system: bool = True,
        timeout_s: float | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        user_id: int | None = None,
        trace_id: str | None = None,
        related_entity: str | None = None,
        related_id: int | None = None,
        prompt_version_id: int | None = None,
        on_token: OnTokenCallback | None = None,
    ) -> LLMResult:
        del cache_system  # ADR-0004 D2: explicit cache markers live in messages.
        cfg = tier_to_model(tier)
        effective_timeout = timeout_s if timeout_s is not None else cfg.default_timeout_s
        effective_max_tokens = max_tokens if max_tokens is not None else cfg.default_max_tokens
        started = monotonic()
        acc = _CallAccumulator()
        response_format: dict[str, Any] | None = (
            {"type": "json_object"} if response_schema is not None else None
        )
        first_messages = (
            _augment_messages_with_schema(messages, response_schema)
            if messages is not None and response_schema is not None
            else messages
        )
        first_user = (
            _augment_with_schema(user, response_schema)
            if messages is None and response_schema is not None
            else user
        )
        cache_key_user = (
            _messages_cache_key_text(first_messages)
            if first_messages is not None
            else first_user
        )
        cache_key = compute_cache_key(
            model=cfg.model,
            system="" if first_messages is not None else system,
            user=cache_key_user,
            response_format=response_format,
            thinking_mode=cfg.thinking_mode,
            prompt_version_id=prompt_version_id,
            temperature=temperature,
        )

        success = False
        error_code: str | None = None
        cached = False
        try:
            # Streaming (on_token != None) skips cache: 半截缓存复杂度不值,
            # 且 drafter 必须真流出来给前端预览;ADR-0004 复用此契约。
            cached_resp = (
                await self._cache_store.get(cache_key) if on_token is None else None
            )
            if cached_resp is not None:
                acc.content = cached_resp.content
                if response_schema is not None:
                    try:
                        acc.parsed = _parse(acc.content, response_schema)
                        cached = True
                    except (json.JSONDecodeError, PydanticValidationError):
                        # Cached payload 与当前 schema 不兼容(写入后 schema
                        # 被加了 required 字段等)→ 降级为 miss,继续走 LLM。
                        acc = _CallAccumulator()
                else:
                    cached = True

            if not cached:
                resp = await self._call_with_admission(
                    ProviderRequest(
                        model=cfg.model,
                        system=system,
                        user=first_user,
                        response_format=response_format,
                        thinking_mode=cfg.thinking_mode,
                        timeout_s=effective_timeout,
                        max_tokens=effective_max_tokens,
                        temperature=temperature,
                        messages=first_messages,
                    ),
                    on_token=on_token,
                )
                self._absorb(acc, resp)

                if response_schema is not None:
                    try:
                        acc.parsed = _parse(acc.content, response_schema)
                    except (json.JSONDecodeError, PydanticValidationError):
                        acc.schema_attempt = 1
                        retry_resp = await self._call_with_admission(
                            ProviderRequest(
                                model=cfg.model,
                                system=system,
                                user=(
                                    _retry_prompt(user, response_schema, acc.content)
                                    if messages is None
                                    else ""
                                ),
                                response_format=response_format,
                                thinking_mode=cfg.thinking_mode,
                                timeout_s=effective_timeout,
                                max_tokens=effective_max_tokens,
                                temperature=temperature,
                                messages=(
                                    _retry_messages(messages, response_schema, acc.content)
                                    if messages is not None
                                    else None
                                ),
                            ),
                            on_token=on_token,
                        )
                        self._absorb(acc, retry_resp)
                        try:
                            acc.parsed = _parse(acc.content, response_schema)
                        except (json.JSONDecodeError, PydanticValidationError) as e2:
                            raise LLMSchemaInvalidError(
                                f"schema validation failed after 1 retry: {e2}"
                            ) from e2

            success = True
        except (
            LLMTimeoutError,
            LLMUpstreamError,
            LLMAuthError,
            LLMSchemaInvalidError,
        ) as exc:
            error_code = _error_code_of(exc)
            result = self._build_result(
                acc=acc,
                cfg=cfg,
                started=started,
                feature=feature,
                tier=tier,
                user_id=user_id,
                trace_id=trace_id,
                related_entity=related_entity,
                related_id=related_id,
                prompt_version_id=prompt_version_id,
                success=False,
                error_code=error_code,
                cached=False,
            )
            await self._logger.log(result)
            logger.warning(
                "llm_call_completed",
                feature=feature,
                model=result.model,
                tier=tier.value,
                trace_id=trace_id,
                related_entity=related_entity,
                related_id=related_id,
                latency_ms=result.latency_ms,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                cost_cny=str(result.cost_cny),
                cached=False,
                success=False,
                error_code=result.error_code,
            )
            raise

        result = self._build_result(
            acc=acc,
            cfg=cfg,
            started=started,
            feature=feature,
            tier=tier,
            user_id=user_id,
            trace_id=trace_id,
            related_entity=related_entity,
            related_id=related_id,
            prompt_version_id=prompt_version_id,
            success=success,
            error_code=None,
            cached=cached,
        )
        await self._logger.log(result)
        logger.info(
            "llm_call_completed",
            feature=feature,
            model=result.model,
            tier=tier.value,
            trace_id=trace_id,
            related_entity=related_entity,
            related_id=related_id,
            latency_ms=result.latency_ms,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            cost_cny=str(result.cost_cny),
            cached=result.cached,
            success=True,
            error_code=None,
        )

        # Cache miss + 成功 → 写入。streaming 不写(on_token 路径下 cached 永
        # 远是 False,但语义上 streaming 不该污染 cache)。
        if not cached and on_token is None:
            await self._cache_store.put(
                cache_key=cache_key,
                model=cfg.model,
                feature=feature,
                prompt_version_id=prompt_version_id,
                request={
                    "system": system if first_messages is None else "",
                    "user": first_user if first_messages is None else "",
                    "messages": first_messages,
                    "response_format": response_format,
                    "thinking_mode": cfg.thinking_mode,
                },
                response={
                    "content": acc.content,
                    "tokens_in": acc.tokens_in,
                    "tokens_out": acc.tokens_out,
                    "cached_tokens": acc.cached_tokens,
                    "cache_creation_input_tokens": acc.cache_creation_input_tokens,
                },
            )

        return result

    # ---------- internals ----------

    async def _call_with_admission(
        self,
        request: ProviderRequest,
        *,
        on_token: OnTokenCallback | None = None,
    ) -> ProviderResponse:
        """Apply process-wide backpressure before touching the provider."""
        if self._concurrency_gate_factory is None:
            return await self._call_with_retry(request, on_token=on_token)
        async with self._concurrency_gate_factory():
            return await self._call_with_retry(request, on_token=on_token)

    async def _call_with_retry(
        self,
        request: ProviderRequest,
        *,
        on_token: OnTokenCallback | None = None,
    ) -> ProviderResponse:
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type((LLMTimeoutError, LLMUpstreamError)),
            stop=stop_after_attempt(self._retry_attempts),
            wait=self._retry_wait,
            reraise=True,
        ):
            with attempt:
                return await self._provider.complete(request, on_token=on_token)
        # Unreachable: AsyncRetrying with reraise=True always either returns or raises.
        raise RuntimeError("retry loop exited without result")  # pragma: no cover

    @staticmethod
    def _absorb(acc: _CallAccumulator, resp: ProviderResponse) -> None:
        acc.tokens_in += resp.tokens_in
        acc.tokens_out += resp.tokens_out
        acc.cached_tokens += resp.cached_tokens
        acc.cache_creation_input_tokens += resp.cache_creation_input_tokens
        acc.content = resp.content

    @staticmethod
    def _build_result(
        *,
        acc: _CallAccumulator,
        cfg: Any,
        started: float,
        feature: str,
        tier: Tier,
        user_id: int | None,
        trace_id: str | None,
        related_entity: str | None,
        related_id: int | None,
        prompt_version_id: int | None,
        success: bool,
        error_code: str | None,
        cached: bool,
    ) -> LLMResult:
        latency_ms = int((monotonic() - started) * 1000)
        cost = cost_for(
            model=cfg.model,
            tokens_in=acc.tokens_in,
            cached_tokens=acc.cached_tokens,
            cache_creation_input_tokens=acc.cache_creation_input_tokens,
            tokens_out=acc.tokens_out,
        )
        return LLMResult(
            content=acc.content,
            parsed=acc.parsed,
            tokens_in=acc.tokens_in,
            tokens_out=acc.tokens_out,
            cached_tokens=acc.cached_tokens,
            cache_creation_input_tokens=acc.cache_creation_input_tokens,
            cost_cny=cost,
            latency_ms=latency_ms,
            model=cfg.model,
            feature=feature,
            tier=tier,
            thinking_mode=cfg.thinking_mode,
            success=success,
            error_code=error_code,
            user_id=user_id,
            trace_id=trace_id,
            related_entity=related_entity,
            related_id=related_id,
            prompt_version_id=prompt_version_id,
            cached=cached,
        )
