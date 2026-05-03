"""profiles: add source + status (mirror jds parse lifecycle); make per-user uniqueness partial

Adds `profile_source` / `profile_status` ENUMs and the corresponding
NOT NULL columns on `profiles`. Brings profiles in line with jds
(0003) so the parse pipeline can INSERT a `status='parsing'` row
before the LLM call (永久约束 4: SSE `started` 必须带 resource_id).

Also replaces the unconditional `UNIQUE (user_id)` constraint with a
partial unique index on `WHERE deleted_at IS NULL`, so a soft-deleted
profile doesn't block the user from re-parsing a new resume (S7 plan
Q2 — same shape as `uq_files_user_sha256` from migration 0007).

Existing rows (none in dev today) are backfilled via a transient
server_default during ADD COLUMN, which is then dropped so future
inserts must set both columns explicitly — same shape as jds.

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PROFILE_SOURCE_VALUES = ("pdf_upload", "text_paste", "manual")
PROFILE_STATUS_VALUES = ("parsing", "parsed", "parse_failed")


def upgrade() -> None:
    profile_source = postgresql.ENUM(
        *PROFILE_SOURCE_VALUES, name="profile_source", create_type=False
    )
    profile_status = postgresql.ENUM(
        *PROFILE_STATUS_VALUES, name="profile_status", create_type=False
    )
    profile_source.create(op.get_bind(), checkfirst=False)
    profile_status.create(op.get_bind(), checkfirst=False)

    op.add_column(
        "profiles",
        sa.Column(
            "source",
            postgresql.ENUM(name="profile_source", create_type=False),
            nullable=False,
            server_default="pdf_upload",
        ),
    )
    op.add_column(
        "profiles",
        sa.Column(
            "status",
            postgresql.ENUM(name="profile_status", create_type=False),
            nullable=False,
            server_default="parsing",
        ),
    )

    # Drop the transient default on `source` so callers must specify it
    # (mirrors jds.source). `status` keeps its `parsing` default — the
    # parse pipeline relies on it for the Phase-1 INSERT.
    op.alter_column("profiles", "source", server_default=None)

    # Convert per-user uniqueness from an unconditional constraint to a
    # partial unique index on `deleted_at IS NULL` so soft-deleted rows
    # don't block re-parse (S7 plan Q2).
    op.drop_constraint("uq_profiles_user_id", "profiles", type_="unique")
    op.create_index(
        "uq_profiles_user_id",
        "profiles",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_profiles_user_id", table_name="profiles")
    op.create_unique_constraint("uq_profiles_user_id", "profiles", ["user_id"])

    op.drop_column("profiles", "status")
    op.drop_column("profiles", "source")

    postgresql.ENUM(name="profile_status", create_type=False).drop(op.get_bind(), checkfirst=False)
    postgresql.ENUM(name="profile_source", create_type=False).drop(op.get_bind(), checkfirst=False)
