"""Structlog config: JSON output + sensitive-key redaction.

Per ENGINEERING §2.6: every log line is JSON; `request_id` rides via
contextvars; values whose key matches `REDACTED_KEYS` are blanked before
the renderer sees them.
"""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import Any

import structlog

REDACTED_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "authorization",
        "password",
        "x-dashscope-key",
        "x_dashscope_key",
        "raw_text",
        "resume_text",
        "email",
    }
)

REDACTED = "***"


def _redact(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in list(event_dict):
        if key.lower() in REDACTED_KEYS:
            event_dict[key] = REDACTED
    return event_dict


_CONFIGURED = False


def setup_logging(level: int = logging.INFO) -> None:
    """Idempotent. Configure structlog with a JSON renderer + redactor."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True
