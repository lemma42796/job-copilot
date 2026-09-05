"""SessionEvent ORM — M2.1 InterviewCoachAgent event log.

Append-only events keep the raw multi-turn interview flow recoverable and
auditable. Current state lives on quiz_sessions.agent_state and
session_answers.remediation_state; this table is the replay trail.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Integer, String, func, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from jobcopilot_api.models.base import Base, IDMixin


class SessionEvent(Base, IDMixin):
    __tablename__ = "session_events"

    # P0 数据隔离:所有业务表按 user_id 归属,service 层每条查询都必须带上它。
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    session_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    answer_id: Mapped[int | None] = mapped_column(BigInteger)
    question_id: Mapped[int | None] = mapped_column(BigInteger)

    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    agent_node: Mapped[str | None] = mapped_column(String(50))
    round_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
