"""Provider-side context cache contract (ADR-0004 D2).

DashScope's OpenAI-compatible endpoint can reuse a prefix marked with
`cache_control`. The `cache_system: bool` parameter on `LLMClient.complete`
is still a *semantic* placeholder kept for forward compatibility with
future providers.

What actually drives cache hits is the **prefix-stable** rule below. Hits
are reported via `LLMResult.cached_tokens`; first-write cache creation is
reported via `LLMResult.cache_creation_input_tokens` when the provider
includes it.
"""

from __future__ import annotations

PREFIX_STABLE_GUIDANCE = """\
To maximize prompt-cache hits with provider-side caching:

1. Put the large reusable context in the earliest message prefix and mark
   that text with `cache_control` when the provider supports explicit
   context cache.
2. Keep that prefix byte-stable — no timestamps, request IDs, user names,
   or task-specific wording before the cache marker.
3. Put dynamic task instructions after the shared prefix. Even a single
   character drift in the cached prefix kills the hit.

`LLMClient.complete(cache_system=False)` is reserved for the rare case
where the system block legitimately must be unique per call (e.g. an
A/B-tested prompt where each variant is a distinct system) — providers
that expose an off-switch will respect it; providers that don't will
simply behave as if it's True (DashScope today).
"""
