"""Rename retrieved chunks audit field to final context chunks.

Revision ID: 0022
Revises: 0021
Create Date: 2026-05-17
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "quiz_sessions",
        "retrieved_chunk_ids",
        new_column_name="final_context_chunk_ids",
    )


def downgrade() -> None:
    op.alter_column(
        "quiz_sessions",
        "final_context_chunk_ids",
        new_column_name="retrieved_chunk_ids",
    )
