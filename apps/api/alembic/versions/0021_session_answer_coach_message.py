"""Persist AnswerJudge coach message.

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("session_answers", sa.Column("coach_message", sa.Text()))


def downgrade() -> None:
    op.drop_column("session_answers", "coach_message")
