"""SQLAlchemy ORM models.

M0 砍 v1 后只剩沿用层(base + LLM cost / cache / prompt 版本表)。
v2 表(notes / note_chunks / questions / quiz_sessions / session_answers /
knowledge_gaps / jds / jd_analyses / resumes / resume_analyses)在 M1 起
按需新建。
"""

from __future__ import annotations

from jobcopilot_api.models.base import Base, IDMixin, TimestampMixin
from jobcopilot_api.models.llm_call import LlmCall
from jobcopilot_api.models.llm_response_cache import LlmResponseCache
from jobcopilot_api.models.prompt_version import PromptVersion

__all__ = [
    "Base",
    "IDMixin",
    "LlmCall",
    "LlmResponseCache",
    "PromptVersion",
    "TimestampMixin",
]
