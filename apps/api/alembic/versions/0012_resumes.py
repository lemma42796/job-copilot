"""resumes + resume_versions 表 + resume_status enum

S16: 简历定制后端骨架。MVP 走 retrieve → draft → review 三函数串调
(D8 拍 A,M3 GA 再升 LangGraph 5 节点)。表 schema 走 DATA_MODEL §3.10/§3.11
完整版,MVP 不用的列(`pdf_file_id` / `template` / `revisions`)建好备用,
免得 M3 再 migrate。

`resume_status` 取 §3.10 的 ('generating','review_failed','ready','failed'):
- generating: phase-1 INSERT 后的中间态(永久约束 #4 SSE 起手要 resource_id)
- ready: review_passed=True
- review_failed: review 节点 high severity finding;markdown 仍存留展示
- failed: retrieve / draft 等 IO 抛错(LLM upstream / schema invalid 等)

resume_versions 设计上不带 updated_at(§3.11 只 created_at),所以不挂
set_updated_at trigger;只 resumes 挂。

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


RESUME_STATUS_VALUES = ("generating", "review_failed", "ready", "failed")


def upgrade() -> None:
    resume_status = postgresql.ENUM(
        *RESUME_STATUS_VALUES, name="resume_status", create_type=False
    )
    resume_status.create(op.get_bind(), checkfirst=False)

    op.create_table(
        "resumes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("jd_id", sa.BigInteger(), nullable=False),
        sa.Column("profile_id", sa.BigInteger(), nullable=False),
        sa.Column("match_id", sa.BigInteger()),
        sa.Column(
            "status",
            postgresql.ENUM(name="resume_status", create_type=False),
            nullable=False,
            server_default="generating",
        ),
        sa.Column("title", sa.String(200)),
        sa.Column("markdown", sa.Text()),
        sa.Column("pdf_file_id", sa.BigInteger()),
        sa.Column("template", sa.String(50), nullable=False, server_default="awesome-cv-zh"),
        sa.Column("generation_model", sa.String(50)),
        sa.Column("review_model", sa.String(50)),
        sa.Column("revisions", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("tokens_in", sa.Integer()),
        sa.Column("tokens_out", sa.Integer()),
        sa.Column("cached_tokens", sa.Integer()),
        sa.Column("cost_cny", sa.Numeric(10, 4)),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("review_passed", sa.Boolean()),
        sa.Column(
            "review_findings",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
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
            ["user_id"], ["users.id"], ondelete="CASCADE", name="fk_resumes_user_id"
        ),
        sa.ForeignKeyConstraint(
            ["jd_id"], ["jds.id"], ondelete="CASCADE", name="fk_resumes_jd_id"
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["profiles.id"],
            ondelete="CASCADE",
            name="fk_resumes_profile_id",
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.id"],
            ondelete="SET NULL",
            name="fk_resumes_match_id",
        ),
        sa.ForeignKeyConstraint(
            ["pdf_file_id"],
            ["files.id"],
            ondelete="SET NULL",
            name="fk_resumes_pdf_file_id",
        ),
    )

    op.create_index(
        "idx_resumes_user_jd",
        "resumes",
        ["user_id", "jd_id", sa.text("created_at DESC")],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.execute(
        """
        CREATE TRIGGER tg_resumes_set_updated_at
        BEFORE UPDATE ON resumes
        FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()
        """
    )

    op.create_table(
        "resume_versions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("resume_id", sa.BigInteger(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("pdf_file_id", sa.BigInteger()),
        sa.Column("edit_type", sa.String(20)),
        sa.Column("edit_note", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["resume_id"],
            ["resumes.id"],
            ondelete="CASCADE",
            name="fk_rv_resume_id",
        ),
        sa.ForeignKeyConstraint(
            ["pdf_file_id"],
            ["files.id"],
            ondelete="SET NULL",
            name="fk_rv_pdf_file_id",
        ),
        sa.UniqueConstraint("resume_id", "version_number", name="uq_rv_resume_version"),
    )

    op.create_index(
        "idx_rv_resume_id",
        "resume_versions",
        ["resume_id", sa.text("version_number DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_rv_resume_id", table_name="resume_versions")
    op.drop_table("resume_versions")
    op.execute("DROP TRIGGER IF EXISTS tg_resumes_set_updated_at ON resumes")
    op.drop_index("idx_resumes_user_jd", table_name="resumes")
    op.drop_table("resumes")
    postgresql.ENUM(name="resume_status", create_type=False).drop(
        op.get_bind(), checkfirst=False
    )
