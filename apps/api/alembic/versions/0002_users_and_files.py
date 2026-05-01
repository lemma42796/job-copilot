"""users + files

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("name", sa.String(100)),
        sa.Column("locale", sa.String(10), nullable=False, server_default="zh-CN"),
        sa.Column(
            "settings",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index(
        "idx_users_email",
        "users",
        ["email"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.execute(
        """
        CREATE TRIGGER tg_users_set_updated_at
        BEFORE UPDATE ON users
        FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()
        """
    )

    op.create_table(
        "files",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.CHAR(64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("purpose", sa.String(50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_files_user_id",
        ),
        sa.CheckConstraint("size_bytes <= 100 * 1024 * 1024", name="ck_files_size"),
    )
    op.create_index(
        "idx_files_user_purpose",
        "files",
        ["user_id", "purpose"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("idx_files_sha256", "files", ["sha256"])
    op.execute("ALTER TABLE files ALTER COLUMN content SET COMPRESSION lz4")


def downgrade() -> None:
    op.drop_index("idx_files_sha256", table_name="files")
    op.drop_index("idx_files_user_purpose", table_name="files")
    op.drop_table("files")

    op.execute("DROP TRIGGER IF EXISTS tg_users_set_updated_at ON users")
    op.drop_index("idx_users_email", table_name="users")
    op.drop_table("users")
