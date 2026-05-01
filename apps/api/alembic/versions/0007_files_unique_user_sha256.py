"""files: partial unique index (user_id, sha256) WHERE deleted_at IS NULL

Implements ADR-0005 D5: same user uploading the same bytes returns the
existing row (with `replayed: true`); cross-user dedup is intentionally
disabled (each user gets their own row + own quota). The index is
*partial* so a soft-deleted file does not block the user from
re-uploading the same bytes.

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_files_user_sha256",
        "files",
        ["user_id", "sha256"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_files_user_sha256", table_name="files")
