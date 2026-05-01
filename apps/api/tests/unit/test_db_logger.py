"""Unit tests for DBCallLogger (mock session — no DB required).

The end-to-end "log row actually shows up in Postgres" guarantee is
covered by `tests/integration/test_llm_logging.py`. Here we only check
the wiring: every LLMResult turns into one `LlmCall` add+commit, and
any DB failure is swallowed with a single WARNING log.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from jobcopilot_api.llm import db_logger as db_logger_mod
from jobcopilot_api.llm.client import LLMResult
from jobcopilot_api.llm.db_logger import DBCallLogger
from jobcopilot_api.llm.tiers import Tier
from jobcopilot_api.models.llm_call import LlmCall


class _FakeSession:
    def __init__(self, *, commit_raises: BaseException | None = None) -> None:
        self._commit_raises = commit_raises
        self.added: list[Any] = []
        self.commit_count = 0

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commit_count += 1
        if self._commit_raises is not None:
            raise self._commit_raises


def _result(**overrides: Any) -> LLMResult:
    base: dict[str, Any] = dict(
        content="hello",
        parsed=None,
        tokens_in=10,
        tokens_out=4,
        cached_tokens=2,
        cost_cny=Decimal("0.001"),
        latency_ms=123,
        model="qwen3.6-flash",
        feature="jd_parse",
        tier=Tier.CHEAP,
        thinking_mode=False,
        success=True,
        error_code=None,
        user_id=42,
        trace_id="t-1",
        related_entity="jd",
        related_id=7,
        prompt_version_id=None,
    )
    base.update(overrides)
    return LLMResult(**base)


class _RecordingLogger:
    """Minimal stand-in for structlog's BoundLogger surface that
    DBCallLogger touches (`.warning(event, **kw)`)."""

    def __init__(self) -> None:
        self.warnings: list[dict[str, Any]] = []

    def warning(self, event: str, **kwargs: Any) -> None:
        self.warnings.append({"event": event, **kwargs})


async def test_log_writes_one_row_with_all_fields() -> None:
    fake = _FakeSession()
    logger = DBCallLogger(sessionmaker=lambda: fake)  # type: ignore[arg-type]

    await logger.log(_result())

    assert len(fake.added) == 1
    record = fake.added[0]
    assert isinstance(record, LlmCall)
    assert record.feature == "jd_parse"
    assert record.tier == "cheap"  # tier.value, not the enum
    assert record.model == "qwen3.6-flash"
    assert record.tokens_in == 10
    assert record.cached_tokens == 2
    assert record.tokens_out == 4
    assert record.cost_cny == Decimal("0.001")
    assert record.success is True
    assert record.user_id == 42
    assert record.trace_id == "t-1"
    assert record.related_entity == "jd"
    assert record.related_id == 7
    assert fake.commit_count == 1


async def test_log_swallows_db_errors_and_emits_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeSession(commit_raises=RuntimeError("db down"))
    recorder = _RecordingLogger()
    monkeypatch.setattr(db_logger_mod, "log", recorder)

    logger = DBCallLogger(sessionmaker=lambda: fake)  # type: ignore[arg-type]
    # No exception escapes:
    await logger.log(_result())

    assert len(recorder.warnings) == 1
    entry = recorder.warnings[0]
    assert entry["event"] == "llm_call_log_failed"
    assert entry["feature"] == "jd_parse"
    assert "db down" in entry["error"]


async def test_log_records_failed_call_with_error_code() -> None:
    fake = _FakeSession()
    logger = DBCallLogger(sessionmaker=lambda: fake)  # type: ignore[arg-type]

    await logger.log(_result(success=False, error_code="timeout", tokens_in=0, tokens_out=0))

    record = fake.added[0]
    assert record.success is False
    assert record.error_code == "timeout"
    assert record.tokens_in == 0
    assert record.tokens_out == 0
