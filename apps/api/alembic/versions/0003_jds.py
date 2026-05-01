"""jds + jd_source/jd_status enums

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JD_SOURCE_VALUES = ("text_paste", "pdf_upload", "image_upload", "extension_paste")
JD_STATUS_VALUES = ("parsing", "parsed", "parse_failed")


def upgrade() -> None:
    jd_source = postgresql.ENUM(
        *JD_SOURCE_VALUES, name="jd_source", create_type=False
    )
    jd_status = postgresql.ENUM(
        *JD_STATUS_VALUES, name="jd_status", create_type=False
    )
    jd_source.create(op.get_bind(), checkfirst=False)
    jd_status.create(op.get_bind(), checkfirst=False)

    op.create_table(
        "jds",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "source",
            postgresql.ENUM(name="jd_source", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="jd_status", create_type=False),
            nullable=False,
            server_default="parsing",
        ),
        sa.Column("raw_text", sa.Text()),
        sa.Column("raw_file_id", sa.BigInteger()),
        sa.Column("company", sa.String(200)),
        sa.Column("title", sa.String(200)),
        sa.Column("location", sa.String(100)),
        sa.Column("salary_min", sa.Integer()),
        sa.Column("salary_max", sa.Integer()),
        sa.Column("salary_currency", sa.String(10), server_default="CNY"),
        sa.Column("salary_period", sa.String(10), server_default="monthly"),
        sa.Column("job_level", sa.String(50)),
        sa.Column("years_required", sa.Integer()),
        sa.Column("education", sa.String(50)),
        sa.Column(
            "hard_skills",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "soft_skills",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "bonus_skills",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "responsibilities",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("description", sa.Text()),
        sa.Column("parse_confidence", sa.Numeric(4, 3)),
        sa.Column("parse_model", sa.String(50)),
        sa.Column("parse_tokens", sa.Integer()),
        sa.Column("parse_cost_cny", sa.Numeric(10, 4)),
        sa.Column(
            "search_tsv",
            postgresql.TSVECTOR,
            sa.Computed(
                "to_tsvector('simple', "
                "coalesce(title,'') || ' ' || coalesce(company,'') || ' ' || coalesce(description,''))",
                persisted=True,
            ),
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
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="fk_jds_user_id"
        ),
        sa.ForeignKeyConstraint(
            ["raw_file_id"],
            ["files.id"],
            ondelete="SET NULL",
            name="fk_jds_raw_file_id",
        ),
        sa.CheckConstraint(
            "salary_min IS NULL OR salary_max IS NULL OR salary_min <= salary_max",
            name="ck_jds_salary_range",
        ),
    )

    op.create_index(
        "idx_jds_user_id_created_at",
        "jds",
        ["user_id", sa.text("created_at DESC")],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_jds_status",
        "jds",
        ["status"],
        postgresql_where=sa.text("status IN ('parsing', 'parse_failed')"),
    )
    op.create_index(
        "idx_jds_search_tsv",
        "jds",
        ["search_tsv"],
        postgresql_using="gin",
    )

    op.execute(
        """
        CREATE TRIGGER tg_jds_set_updated_at
        BEFORE UPDATE ON jds
        FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS tg_jds_set_updated_at ON jds")
    op.drop_index("idx_jds_search_tsv", table_name="jds")
    op.drop_index("idx_jds_status", table_name="jds")
    op.drop_index("idx_jds_user_id_created_at", table_name="jds")
    op.drop_table("jds")

    postgresql.ENUM(name="jd_status", create_type=False).drop(
        op.get_bind(), checkfirst=False
    )
    postgresql.ENUM(name="jd_source", create_type=False).drop(
        op.get_bind(), checkfirst=False
    )
