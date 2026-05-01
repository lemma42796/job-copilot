"""Unit tests for jobcopilot_api.infra.llm (FastAPI dependency wiring)."""

from __future__ import annotations

import pytest

from jobcopilot_api.infra import llm as infra_llm
from jobcopilot_api.llm.client import BaseLLMClient
from jobcopilot_api.settings import settings


def test_no_api_key_raises_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "dashscope_api_key", "")
    infra_llm.reset_client()
    with pytest.raises(ValueError, match="non-empty api_key"):
        infra_llm.get_llm_client()


def test_get_llm_client_caches_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "dashscope_api_key", "sk-test-fake")
    infra_llm.reset_client()
    a = infra_llm.get_llm_client()
    b = infra_llm.get_llm_client()
    assert a is b
    assert isinstance(a, BaseLLMClient)


def test_reset_client_drops_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "dashscope_api_key", "sk-test-fake")
    infra_llm.reset_client()
    a = infra_llm.get_llm_client()
    infra_llm.reset_client()
    b = infra_llm.get_llm_client()
    assert a is not b
