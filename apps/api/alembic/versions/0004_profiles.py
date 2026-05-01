"""profiles + experiences + projects + skills + educations

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SKILL_LEVEL_VALUES = ("beginner", "intermediate", "advanced", "expert")
TABLES_WITH_TRIGGER = (
    "profiles",
    "profile_experiences",
    "profile_projects",
    "profile_skills",
    "profile_educations",
)


def _attach_trigger(table: str) -> None:
    op.execute(
        f"""
        CREATE TRIGGER tg_{table}_set_updated_at
        BEFORE UPDATE ON {table}
        FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()
        """
    )


def upgrade() -> None:
    skill_level = postgresql.ENUM(
        *SKILL_LEVEL_VALUES, name="skill_level", create_type=False
    )
    skill_level.create(op.get_bind(), checkfirst=False)

    op.create_table(
        "profiles",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("full_name", sa.String(100)),
        sa.Column("phone", sa.String(50)),
        sa.Column("email", sa.String(255)),
        sa.Column("location", sa.String(100)),
        sa.Column("summary", sa.Text()),
        sa.Column(
            "target_titles",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "target_locations",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("target_salary_min", sa.Integer()),
        sa.Column("target_salary_max", sa.Integer()),
        sa.Column("raw_file_id", sa.BigInteger()),
        sa.Column("raw_text", sa.Text()),
        sa.Column("parse_model", sa.String(50)),
        sa.Column("parse_tokens", sa.Integer()),
        sa.Column("parse_cost_cny", sa.Numeric(10, 4)),
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
            ["user_id"], ["users.id"], ondelete="CASCADE", name="fk_profiles_user_id"
        ),
        sa.ForeignKeyConstraint(
            ["raw_file_id"],
            ["files.id"],
            ondelete="SET NULL",
            name="fk_profiles_raw_file_id",
        ),
        sa.UniqueConstraint("user_id", name="uq_profiles_user_id"),
    )
    _attach_trigger("profiles")

    op.create_table(
        "profile_experiences",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("profile_id", sa.BigInteger(), nullable=False),
        sa.Column("company", sa.String(200), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("location", sa.String(100)),
        sa.Column("start_date", sa.Date()),
        sa.Column("end_date", sa.Date()),
        sa.Column(
            "is_current",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "bullets",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "tech_stack",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "achievements",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")
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
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["profiles.id"],
            ondelete="CASCADE",
            name="fk_pe_profile_id",
        ),
        sa.CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR start_date <= end_date",
            name="ck_pe_dates",
        ),
    )
    op.create_index(
        "idx_pe_profile_id", "profile_experiences", ["profile_id"]
    )
    _attach_trigger("profile_experiences")

    op.create_table(
        "profile_projects",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("profile_id", sa.BigInteger(), nullable=False),
        sa.Column("experience_id", sa.BigInteger()),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("role", sa.String(100)),
        sa.Column("start_date", sa.Date()),
        sa.Column("end_date", sa.Date()),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "bullets",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "tech_stack",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "achievements",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("repo_url", sa.String(500)),
        sa.Column("demo_url", sa.String(500)),
        sa.Column(
            "sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")
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
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["profiles.id"],
            ondelete="CASCADE",
            name="fk_pp_profile_id",
        ),
        sa.ForeignKeyConstraint(
            ["experience_id"],
            ["profile_experiences.id"],
            ondelete="SET NULL",
            name="fk_pp_experience_id",
        ),
    )
    op.create_index("idx_pp_profile_id", "profile_projects", ["profile_id"])
    _attach_trigger("profile_projects")

    op.create_table(
        "profile_skills",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("profile_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("name_raw", sa.String(100)),
        sa.Column("category", sa.String(50)),
        sa.Column(
            "level",
            postgresql.ENUM(name="skill_level", create_type=False),
        ),
        sa.Column("years", sa.Numeric(4, 1)),
        sa.Column(
            "evidence_project_ids",
            postgresql.ARRAY(sa.BigInteger()),
            nullable=False,
            server_default=sa.text("'{}'::bigint[]"),
        ),
        sa.Column(
            "evidence_experience_ids",
            postgresql.ARRAY(sa.BigInteger()),
            nullable=False,
            server_default=sa.text("'{}'::bigint[]"),
        ),
        sa.Column(
            "sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")
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
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["profiles.id"],
            ondelete="CASCADE",
            name="fk_ps_profile_id",
        ),
        sa.UniqueConstraint("profile_id", "name", name="uq_ps_profile_name"),
    )
    op.create_index(
        "idx_ps_profile_id_category", "profile_skills", ["profile_id", "category"]
    )
    _attach_trigger("profile_skills")

    op.create_table(
        "profile_educations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("profile_id", sa.BigInteger(), nullable=False),
        sa.Column("school", sa.String(200), nullable=False),
        sa.Column("degree", sa.String(50)),
        sa.Column("major", sa.String(100)),
        sa.Column("start_date", sa.Date()),
        sa.Column("end_date", sa.Date()),
        sa.Column("gpa", sa.Numeric(4, 2)),
        sa.Column(
            "honors",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")
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
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["profiles.id"],
            ondelete="CASCADE",
            name="fk_pe2_profile_id",
        ),
    )
    op.create_index("idx_pe2_profile_id", "profile_educations", ["profile_id"])
    _attach_trigger("profile_educations")


def downgrade() -> None:
    for table in reversed(TABLES_WITH_TRIGGER):
        op.execute(f"DROP TRIGGER IF EXISTS tg_{table}_set_updated_at ON {table}")

    op.drop_index("idx_pe2_profile_id", table_name="profile_educations")
    op.drop_table("profile_educations")

    op.drop_index("idx_ps_profile_id_category", table_name="profile_skills")
    op.drop_table("profile_skills")

    op.drop_index("idx_pp_profile_id", table_name="profile_projects")
    op.drop_table("profile_projects")

    op.drop_index("idx_pe_profile_id", table_name="profile_experiences")
    op.drop_table("profile_experiences")

    op.drop_table("profiles")

    postgresql.ENUM(name="skill_level", create_type=False).drop(
        op.get_bind(), checkfirst=False
    )
