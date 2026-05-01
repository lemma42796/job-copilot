"""Unit tests for jobcopilot_api.llm.cache."""

from __future__ import annotations

from jobcopilot_api.llm.cache import PREFIX_STABLE_GUIDANCE


def test_guidance_documents_prefix_stability() -> None:
    # The guidance string is the canonical reference for "what counts as a
    # cache-friendly prompt"; if we ever rewrite it, this test makes sure
    # the core idea — prefix stability — stays in the doc.
    assert "prefix" in PREFIX_STABLE_GUIDANCE.lower()
    assert "system" in PREFIX_STABLE_GUIDANCE.lower()
