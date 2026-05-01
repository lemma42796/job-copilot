"""Sensitive-key redactor + idempotent setup."""

from __future__ import annotations

from jobcopilot_api.infra.logging import REDACTED, _redact, setup_logging


def test_redact__lowercases_key_match__redacts_value() -> None:
    event = {
        "user_name": "alice",
        "api_key": "secret-aaaa",
        "Authorization": "Bearer xyz",
        "raw_text": "全文简历内容",
    }
    out = _redact(None, "info", dict(event))
    assert out["api_key"] == REDACTED
    assert out["Authorization"] == REDACTED
    assert out["raw_text"] == REDACTED
    assert out["user_name"] == "alice"


def test_setup_logging__second_call_is_noop() -> None:
    setup_logging()
    setup_logging()  # idempotent guard
