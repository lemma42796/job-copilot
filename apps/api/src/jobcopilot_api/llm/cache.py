"""System-prompt cache contract (ADR-0004 D2).

DashScope's OpenAI-compatible endpoint enables prompt cache by default and
exposes no SDK toggle, so the `cache_system: bool` parameter on
`LLMClient.complete` is a *semantic* placeholder kept for forward
compatibility with future providers.

What actually drives cache hits is the **prefix-stable** rule below. Hit
rate is reported back via `LLMResult.cached_tokens` (read from
`response.usage.prompt_tokens_details.cached_tokens`).
"""

from __future__ import annotations

PREFIX_STABLE_GUIDANCE = """\
To maximize prompt-cache hits with provider-side caching:

1. Build the system message from constants only — no timestamps, request
   IDs, user names, or any per-call dynamic content.
2. Put dynamic context in the user message instead. The cache key is the
   tokenized system message prefix; even a single character drift kills
   the hit.
3. Order matters. If you must concatenate multiple system fragments, do
   them in a fixed order so the resulting string is byte-identical across
   calls of the same agent.

`LLMClient.complete(cache_system=False)` is reserved for the rare case
where the system block legitimately must be unique per call (e.g. an
A/B-tested prompt where each variant is a distinct system) — providers
that expose an off-switch will respect it; providers that don't will
simply behave as if it's True (DashScope today).
"""
