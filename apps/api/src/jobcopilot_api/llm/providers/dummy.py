"""DummyProvider — fixture-driven, used by every unit test (ADR-0004 D8).

Matching strategy is **explicit scenario keys**, not prompt hashing. The
test arms the provider with one or more scripted responses (or errors)
before invoking the agent under test:

    dummy = DummyProvider()
    dummy.queue(content='{"title":"SWE"}', tokens_in=120, tokens_out=8)
    dummy.queue_error(LLMTimeoutError("simulated"))
    client = BaseLLMClient(provider=dummy, logger=MemoryCallLogger())

The queue is consumed in order. If a test exhausts the queue but the
client makes another call, the provider raises `RuntimeError` so the
test fails loudly rather than silently looping.

Loading from disk (`apps/api/tests/fixtures/llm/<feature>__<scenario>.json`)
is provided via `DummyProvider.from_fixture(path)` for the common case
where a fixture is reused across tests.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from jobcopilot_api.llm.client import Provider, ProviderRequest, ProviderResponse


@dataclass(frozen=True)
class DummyScript:
    """One scripted response or error for the dummy provider."""

    content: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0
    error: BaseException | None = None


_QueueItem = ProviderResponse | BaseException


class DummyProvider(Provider):
    def __init__(self) -> None:
        self._queue: deque[_QueueItem] = deque()
        self.calls: list[ProviderRequest] = []

    def queue(
        self,
        *,
        content: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cached_tokens: int = 0,
    ) -> None:
        self._queue.append(
            ProviderResponse(
                content=content,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cached_tokens=cached_tokens,
            )
        )

    def queue_error(self, exc: BaseException) -> None:
        self._queue.append(exc)

    def queue_script(self, script: DummyScript) -> None:
        if script.error is not None:
            self.queue_error(script.error)
        else:
            self.queue(
                content=script.content,
                tokens_in=script.tokens_in,
                tokens_out=script.tokens_out,
                cached_tokens=script.cached_tokens,
            )

    @classmethod
    def from_fixture(cls, path: Path) -> DummyProvider:
        """Load a fixture file and pre-arm a single scripted response.

        File format (JSON):
            {
              "content": "...",
              "tokens_in": 120,
              "tokens_out": 8,
              "cached_tokens": 0
            }
        """
        data = json.loads(path.read_text(encoding="utf-8"))
        provider = cls()
        provider.queue(
            content=data["content"],
            tokens_in=int(data.get("tokens_in", 0)),
            tokens_out=int(data.get("tokens_out", 0)),
            cached_tokens=int(data.get("cached_tokens", 0)),
        )
        return provider

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        if not self._queue:
            raise RuntimeError(
                "DummyProvider queue exhausted — test forgot to queue() / queue_error() "
                "before another call to provider.complete()."
            )
        item = self._queue.popleft()
        if isinstance(item, BaseException):
            raise item
        return item
