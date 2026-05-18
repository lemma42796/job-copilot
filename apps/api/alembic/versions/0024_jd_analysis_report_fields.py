"""jd_analyses: add report sidecar fields

Revision ID: 0024
Revises: 0023
Create Date: 2026-05-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: str | Sequence[str] | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jd_analyses",
        sa.Column(
            "quiz_topic_candidates",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "jd_analyses",
        sa.Column(
            "note_match_summary",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("jd_analyses", "note_match_summary")
    op.drop_column("jd_analyses", "quiz_topic_candidates")
