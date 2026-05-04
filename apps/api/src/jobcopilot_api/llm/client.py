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

import json
from dataclasses import dataclass, field
from decimal import Decimal
from time import monotonic
from typing import Any, Protocol

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

from jobcopilot_api.llm.errors import (
    LLMAuthError,
    LLMSchemaInvalidError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from jobcopilot_api.llm.pricing import cost_for
from jobcopilot_api.llm.tiers import Tier, tier_to_model

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


@dataclass(frozen=True)
class ProviderResponse:
    content: str
    tokens_in: int
    tokens_out: int
    cached_tokens: int


class Provider(Protocol):
    """SDK-level wrapper. Maps provider errors to LLM* exceptions."""

    async def complete(self, request: ProviderRequest) -> ProviderResponse: ...


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


@dataclass
class LLMRequest:
    """Bundled keyword args for `LLMClient.complete`. Kept as a dataclass so
    callers can build it once and re-use for retry-style flows; agents that
    only call once should use the keyword-only `complete(...)` form."""

    feature: str
    tier: Tier
    system: str
    user: str
    response_schema: type[BaseModel] | None = None
    cache_system: bool = True
    timeout_s: float | None = None
    max_tokens: int | None = None
    user_id: int | None = None
    trace_id: str | None = None
    related_entity: str | None = None
    related_id: int | None = None
    prompt_version_id: int | None = None


class LLMClient(Protocol):
    """Agent-facing surface. Implementations: `BaseLLMClient`."""

    async def complete(
        self,
        *,
        feature: str,
        tier: Tier,
        system: str,
        user: str,
        response_schema: type[BaseModel] | None = None,
        cache_system: bool = True,
        timeout_s: float | None = None,
        max_tokens: int | None = None,
        user_id: int | None = None,
        trace_id: str | None = None,
        related_entity: str | None = None,
        related_id: int | None = None,
        prompt_version_id: int | None = None,
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


def _retry_prompt(user: str, schema: type[BaseModel], bad_content: str) -> str:
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)
    return (
        f"{user}\n\n"
        f"{_SCHEMA_RETRY_PREAMBLE}{schema_json}\n\n"
        f"Your previous (invalid) response was:\n{bad_content}"
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
        retry_attempts: int = DEFAULT_RETRY_ATTEMPTS,
        retry_wait: wait_base = DEFAULT_RETRY_WAIT,
    ) -> None:
        self._provider = provider
        self._logger: CallLogger = logger if logger is not None else NoopCallLogger()
        self._retry_attempts = retry_attempts
        self._retry_wait = retry_wait

    async def complete(
        self,
        *,
        feature: str,
        tier: Tier,
        system: str,
        user: str,
        response_schema: type[BaseModel] | None = None,
        cache_system: bool = True,
        timeout_s: float | None = None,
        max_tokens: int | None = None,
        user_id: int | None = None,
        trace_id: str | None = None,
        related_entity: str | None = None,
        related_id: int | None = None,
        prompt_version_id: int | None = None,
    ) -> LLMResult:
        del cache_system  # ADR-0004 D2: semantic placeholder, no SDK toggle today
        cfg = tier_to_model(tier)
        effective_timeout = timeout_s if timeout_s is not None else cfg.default_timeout_s
        effective_max_tokens = max_tokens if max_tokens is not None else cfg.default_max_tokens
        started = monotonic()
        acc = _CallAccumulator()
        response_format: dict[str, Any] | None = (
            {"type": "json_object"} if response_schema is not None else None
        )

        success = False
        error_code: str | None = None
        try:
            first_user = (
                _augment_with_schema(user, response_schema) if response_schema is not None else user
            )
            resp = await self._call_with_retry(
                ProviderRequest(
                    model=cfg.model,
                    system=system,
                    user=first_user,
                    response_format=response_format,
                    thinking_mode=cfg.thinking_mode,
                    timeout_s=effective_timeout,
                    max_tokens=effective_max_tokens,
                )
            )
            self._absorb(acc, resp)

            if response_schema is not None:
                try:
                    acc.parsed = _parse(acc.content, response_schema)
                except (json.JSONDecodeError, PydanticValidationError):
                    acc.schema_attempt = 1
                    retry_resp = await self._call_with_retry(
                        ProviderRequest(
                            model=cfg.model,
                            system=system,
                            user=_retry_prompt(user, response_schema, acc.content),
                            response_format=response_format,
                            thinking_mode=cfg.thinking_mode,
                            timeout_s=effective_timeout,
                            max_tokens=effective_max_tokens,
                        )
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
            )
            await self._logger.log(result)
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
        )
        await self._logger.log(result)
        return result

    # ---------- internals ----------

    async def _call_with_retry(self, request: ProviderRequest) -> ProviderResponse:
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type((LLMTimeoutError, LLMUpstreamError)),
            stop=stop_after_attempt(self._retry_attempts),
            wait=self._retry_wait,
            reraise=True,
        ):
            with attempt:
                return await self._provider.complete(request)
        # Unreachable: AsyncRetrying with reraise=True always either returns or raises.
        raise RuntimeError("retry loop exited without result")  # pragma: no cover

    @staticmethod
    def _absorb(acc: _CallAccumulator, resp: ProviderResponse) -> None:
        acc.tokens_in += resp.tokens_in
        acc.tokens_out += resp.tokens_out
        acc.cached_tokens += resp.cached_tokens
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
    ) -> LLMResult:
        latency_ms = int((monotonic() - started) * 1000)
        cost = cost_for(
            model=cfg.model,
            tokens_in=acc.tokens_in,
            cached_tokens=acc.cached_tokens,
            tokens_out=acc.tokens_out,
        )
        return LLMResult(
            content=acc.content,
            parsed=acc.parsed,
            tokens_in=acc.tokens_in,
            tokens_out=acc.tokens_out,
            cached_tokens=acc.cached_tokens,
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
        )
