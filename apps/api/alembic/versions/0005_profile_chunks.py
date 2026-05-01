"""profile_chunks (HNSW + GIN) + chunk_granularity enum

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CHUNK_GRANULARITY_VALUES = ("experience", "project", "skill", "summary")


def upgrade() -> None:
    granularity = postgresql.ENUM(
        *CHUNK_GRANULARITY_VALUES, name="chunk_granularity", create_type=False
    )
    granularity.create(op.get_bind(), checkfirst=False)

    op.create_table(
        "profile_chunks",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("profile_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "granularity",
            postgresql.ENUM(name="chunk_granularity", create_type=False),
            nullable=False,
        ),
        sa.Column("source_table", sa.String(50), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "content_tsv",
            postgresql.TSVECTOR,
            sa.Computed("to_tsvector('simple', content)", persisted=True),
        ),
        sa.Column("embedding", Vector(1024)),
        sa.Column(
            "metadata",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("embed_model", sa.String(50)),
        sa.Column("embed_version", sa.String(20)),
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
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["profiles.id"],
            ondelete="CASCADE",
            name="fk_pc_profile_id",
        ),
    )

    op.create_index(
        "idx_pc_embedding_hnsw",
        "profile_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": "16", "ef_construction": "64"},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "idx_pc_content_tsv",
        "profile_chunks",
        ["content_tsv"],
        postgresql_using="gin",
    )
    op.create_index(
        "idx_pc_profile_granularity",
        "profile_chunks",
        ["profile_id", "granularity"],
    )

    op.execute(
        """
        CREATE TRIGGER tg_profile_chunks_set_updated_at
        BEFORE UPDATE ON profile_chunks
        FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS tg_profile_chunks_set_updated_at ON profile_chunks"
    )
    op.drop_index("idx_pc_profile_granularity", table_name="profile_chunks")
    op.drop_index(
        "idx_pc_content_tsv", table_name="profile_chunks", postgresql_using="gin"
    )
    op.drop_index(
        "idx_pc_embedding_hnsw", table_name="profile_chunks", postgresql_using="hnsw"
    )
    op.drop_table("profile_chunks")

    postgresql.ENUM(name="chunk_granularity", create_type=False).drop(
        op.get_bind(), checkfirst=False
    )
