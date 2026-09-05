"""Unit tests for jobcopilot_api.llm.pricing."""

from __future__ import annotations

from decimal import Decimal

from jobcopilot_api.llm.pricing import cost_for, price_table


def test_flash_cost_uncached() -> None:
    # 1M in @ 1.2 + 1M out @ 7.2 = 8.4 CNY
    cost = cost_for(
        model="qwen3.6-flash",
        tokens_in=1_000_000,
        cached_tokens=0,
        tokens_out=1_000_000,
    )
    assert cost == Decimal("8.4")


def test_flash_cost_with_cache() -> None:
    # 100k uncached in @ 1.2  -> 0.12
    # 900k cached in   @ 0.12 -> 0.108
    # 1M out          @ 7.2  -> 7.2
    # total = 7.428
    cost = cost_for(
        model="qwen3.6-flash",
        tokens_in=1_000_000,
        cached_tokens=900_000,
        tokens_out=1_000_000,
    )
    assert cost == Decimal("7.428")


def test_unknown_model_returns_zero() -> None:
    cost = cost_for(
        model="model-not-in-price-table",
        tokens_in=1000,
        cached_tokens=0,
        tokens_out=500,
    )
    assert cost == Decimal("0")


def test_cached_exceeding_total_clamps_to_zero_uncached() -> None:
    # Defensive: if a provider reports cached > prompt_tokens (impossible
    # but cheap to guard), uncached is clamped to 0 instead of going negative.
    cost = cost_for(
        model="qwen3.6-flash",
        tokens_in=100,
        cached_tokens=200,
        tokens_out=0,
    )
    # 200 cached @ 0.12 / 1M = 0.000024
    assert cost == Decimal("200") * Decimal("0.12") / Decimal("1000000")


def test_price_table_returns_copy() -> None:
    table = price_table()
    table["fake"] = table["qwen3.6-flash"]
    assert "fake" not in price_table()
