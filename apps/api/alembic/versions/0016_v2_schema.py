"""v2 schema 一次性切换:DROP v1 业务表 / ENUM,CREATE v2 全 schema

M0 末批量改造。配套 ORM 文件:apps/api/src/jobcopilot_api/models/{note,
note_chunk, question, quiz_session, session_answer, knowledge_gap, jd,
jd_analysis, resume, resume_analysis}.py。

留下不动:prompt_versions / llm_calls / llm_response_cache(0006 + 0015) +
public.char_ngrams(text) SQL 函数(0014)+ public.set_updated_at() 触发器
函数(0001)+ pgvector / vector_cosine_ops 扩展。

downgrade 不实做 — v2 是单向重构(DATA_MODEL §10);要回 v1 形态走
git checkout v0.1-jobcopilot-v1 重建数据库。

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# v2 ENUM(详见 DATA_MODEL §4)
NOTE_SOURCE_VALUES = (
    # notes 用:
    "local_md",  # File System Access API 选目录 / 选单篇本地 .md 入库
    "web_editor",
    "yuque",
    # jds 复用同一 ENUM(DATA_MODEL §5.7):
    "text_paste",
    "image_upload",
)
QUESTION_TYPE_VALUES = ("open_ended", "definition")
QUIZ_SESSION_STATUS_VALUES = ("in_progress", "submitted", "abandoned")

# updated_at 触发器要装的表(jd_analyses / resume_analyses 是快照表无 updated_at)
TABLES_WITH_UPDATED_AT_TRIGGER = (
    "notes",
    "note_chunks",
    "questions",
    "quiz_sessions",
    "session_answers",
    "knowledge_gaps",
    "jds",
    "resumes",
)


def upgrade() -> None:
    # 1. DROP v1 业务表(CASCADE 处理 FK 链)
    for table in (
        "resume_versions",
        "resumes",
        "matches",
        "profile_chunks",
        "profile_skills",
        "profile_educations",
        "profile_projects",
        "profile_experiences",
        "profiles",
        "jds",
        "files",
        "users",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    # 2. DROP v1 ENUM
    for enum_name in (
        "profile_source",
        "profile_status",
        "skill_level",
        "chunk_granularity",
        "match_status",
        "resume_source",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")

    # 3. CREATE v2 ENUM
    note_source = postgresql.ENUM(
        *NOTE_SOURCE_VALUES, name="note_source", create_type=False
    )
    note_source.create(op.get_bind(), checkfirst=False)

    question_type = postgresql.ENUM(
        *QUESTION_TYPE_VALUES, name="question_type", create_type=False
    )
    question_type.create(op.get_bind(), checkfirst=False)

    quiz_session_status = postgresql.ENUM(
        *QUIZ_SESSION_STATUS_VALUES,
        name="quiz_session_status",
        create_type=False,
    )
    quiz_session_status.create(op.get_bind(), checkfirst=False)

    # 4. CREATE v2 表(顺序按 FK 依赖)

    # 4.1 notes
    op.create_table(
        "notes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("folder_path", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column(
            "source",
            postgresql.ENUM(name="note_source", create_type=False),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(100)),
        sa.Column("external_updated_at", sa.DateTime(timezone=True)),
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
    )
    op.create_index(
        "uq_notes_folder_title",
        "notes",
        ["folder_path", "title"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_notes_folder",
        "notes",
        ["folder_path"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_notes_external_id",
        "notes",
        ["external_id"],
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )

    # 4.2 note_chunks
    op.create_table(
        "note_chunks",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("note_id", sa.BigInteger(), nullable=False),
        sa.Column("folder_path", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("heading_path", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("heading_level", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "content_tsv",
            postgresql.TSVECTOR,
            sa.Computed(
                "to_tsvector('simple', public.char_ngrams(content))",
                persisted=True,
            ),
        ),
        sa.Column("embedding", Vector(1024)),
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
            ["note_id"], ["notes.id"], ondelete="CASCADE", name="fk_nc_note_id"
        ),
    )
    op.create_index("ix_note_chunks_note_id", "note_chunks", ["note_id"])
    op.create_index(
        "ix_note_chunks_folder",
        "note_chunks",
        ["folder_path"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_note_chunks_embedding_hnsw",
        "note_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": "16", "ef_construction": "64"},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "ix_note_chunks_content_tsv",
        "note_chunks",
        ["content_tsv"],
        postgresql_using="gin",
    )

    # 4.3 questions
    op.create_table(
        "questions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "node_folder_path", postgresql.ARRAY(sa.Text()), nullable=False
        ),
        sa.Column(
            "node_heading_path",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "type",
            postgresql.ENUM(name="question_type", create_type=False),
            nullable=False,
        ),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column(
            "source_chunk_ids",
            postgresql.ARRAY(sa.BigInteger()),
            nullable=False,
        ),
        sa.Column("reference_answer", sa.Text(), nullable=False),
        sa.Column(
            "reference_chunk_ids",
            postgresql.ARRAY(sa.BigInteger()),
            nullable=False,
        ),
        sa.Column(
            "reference_points",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("gen_model", sa.String(50)),
        sa.Column("gen_prompt_version", sa.String(20)),
        sa.Column("gen_tokens_in", sa.Integer()),
        sa.Column("gen_tokens_out", sa.Integer()),
        sa.Column("gen_cost_cny", sa.Numeric(10, 6)),
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
    )
    op.create_index(
        "ix_questions_node_folder",
        "questions",
        ["node_folder_path"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_questions_source_chunks",
        "questions",
        ["source_chunk_ids"],
        postgresql_using="gin",
    )

    # 4.4 quiz_sessions
    op.create_table(
        "quiz_sessions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "node_folder_path", postgresql.ARRAY(sa.Text()), nullable=False
        ),
        sa.Column(
            "node_heading_path",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="quiz_session_status", create_type=False),
            nullable=False,
            server_default="in_progress",
        ),
        sa.Column("total_score", sa.Numeric(5, 2)),
        sa.Column("coverage_score", sa.Numeric(5, 2)),
        sa.Column("fidelity_score", sa.Numeric(5, 2)),
        sa.Column("depth_score", sa.Numeric(5, 2)),
        sa.Column("recall_md_path", sa.Text()),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("abandoned_at", sa.DateTime(timezone=True)),
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
    )
    op.create_index(
        "ix_quiz_sessions_status_started",
        "quiz_sessions",
        ["status", sa.text("started_at DESC")],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # 4.5 session_answers
    op.create_table(
        "session_answers",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("question_id", sa.BigInteger(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("user_answer", sa.Text()),
        sa.Column("answer_submitted_at", sa.DateTime(timezone=True)),
        sa.Column("coverage_score", sa.Numeric(5, 2)),
        sa.Column("coverage_evidence", postgresql.JSONB),
        sa.Column("fidelity_score", sa.Numeric(5, 2)),
        sa.Column("fidelity_evidence", postgresql.JSONB),
        sa.Column("depth_score", sa.Numeric(5, 2)),
        sa.Column("depth_evidence", postgresql.JSONB),
        sa.Column("total_score", sa.Numeric(5, 2)),
        sa.Column("judge_model", sa.String(50)),
        sa.Column("judge_prompt_version", sa.String(20)),
        sa.Column("judge_tokens_in", sa.Integer()),
        sa.Column("judge_tokens_out", sa.Integer()),
        sa.Column("judge_cost_cny", sa.Numeric(10, 6)),
        sa.Column("judged_at", sa.DateTime(timezone=True)),
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
            ["session_id"],
            ["quiz_sessions.id"],
            ondelete="CASCADE",
            name="fk_sa_session_id",
        ),
        sa.ForeignKeyConstraint(
            ["question_id"], ["questions.id"], name="fk_sa_question_id"
        ),
        sa.UniqueConstraint(
            "session_id", "question_id", name="uq_session_question"
        ),
        sa.UniqueConstraint(
            "session_id", "order_index", name="uq_session_order"
        ),
    )
    op.create_index(
        "ix_session_answers_session",
        "session_answers",
        ["session_id", "order_index"],
    )

    # 4.6 knowledge_gaps
    op.create_table(
        "knowledge_gaps",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("folder_path", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column(
            "heading_path",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "error_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("last_score", sa.Numeric(5, 2)),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column(
            "prev_interval_days",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("next_review_at", sa.Date()),
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
        sa.UniqueConstraint("folder_path", "heading_path", name="uq_kg_path"),
    )
    op.create_index(
        "ix_kg_next_review",
        "knowledge_gaps",
        ["next_review_at"],
        postgresql_where=sa.text("next_review_at IS NOT NULL"),
    )

    # 4.7 jds(v2 形态;v1 jds 已 DROP)
    op.create_table(
        "jds",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "source",
            postgresql.ENUM(name="note_source", create_type=False),
            nullable=False,
        ),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("title", sa.String(255)),
        sa.Column("parsed_payload", postgresql.JSONB, nullable=False),
        sa.Column("parse_model", sa.String(50)),
        sa.Column("parse_prompt_version", sa.String(20)),
        sa.Column("parse_tokens_in", sa.Integer()),
        sa.Column("parse_tokens_out", sa.Integer()),
        sa.Column("parse_cost_cny", sa.Numeric(10, 6)),
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
    )
    op.create_index(
        "ix_jds_created",
        "jds",
        [sa.text("created_at DESC")],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_jds_title",
        "jds",
        ["title"],
        postgresql_where=sa.text("deleted_at IS NULL AND title IS NOT NULL"),
    )

    # 4.8 jd_analyses
    op.create_table(
        "jd_analyses",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("jd_ids", postgresql.ARRAY(sa.BigInteger()), nullable=False),
        sa.Column("jd_count", sa.Integer(), nullable=False),
        sa.Column("filter_description", sa.String(255)),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="in_progress",
        ),
        sa.Column("aggregated_requirements", postgresql.JSONB),
        sa.Column("learning_path_md", sa.Text()),
        sa.Column("total_tokens_in", sa.Integer()),
        sa.Column("total_tokens_out", sa.Integer()),
        sa.Column("total_cost_cny", sa.Numeric(10, 6)),
        sa.Column("cache_hit_rate", sa.Numeric(4, 3)),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("failure_reason", sa.Text()),
    )
    op.create_index(
        "ix_jd_analyses_started",
        "jd_analyses",
        [sa.text("started_at DESC")],
    )

    # 4.9 resumes(v2 形态;v1 resumes 已 DROP)
    op.create_table(
        "resumes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("title", sa.String(255)),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column(
            "parsed_chunks",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("parse_model", sa.String(50)),
        sa.Column("parse_cost_cny", sa.Numeric(10, 6)),
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
    )
    op.create_index(
        "ix_resumes_created",
        "resumes",
        [sa.text("created_at DESC")],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # 4.10 resume_analyses
    op.create_table(
        "resume_analyses",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("jd_analysis_id", sa.BigInteger(), nullable=False),
        sa.Column("resume_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="in_progress",
        ),
        sa.Column("suggestions", postgresql.JSONB),
        sa.Column("anchored_count", sa.Integer()),
        sa.Column("unanchored_count", sa.Integer()),
        sa.Column("coverage_summary", postgresql.JSONB),
        sa.Column("llm_model", sa.String(50)),
        sa.Column("llm_prompt_version", sa.String(20)),
        sa.Column("total_cost_cny", sa.Numeric(10, 6)),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("failure_reason", sa.Text()),
        sa.ForeignKeyConstraint(
            ["jd_analysis_id"],
            ["jd_analyses.id"],
            name="fk_ra_jd_analysis_id",
        ),
        sa.ForeignKeyConstraint(
            ["resume_id"], ["resumes.id"], name="fk_ra_resume_id"
        ),
    )
    op.create_index(
        "ix_resume_analyses_started",
        "resume_analyses",
        [sa.text("started_at DESC")],
    )
    op.create_index("ix_resume_analyses_resume", "resume_analyses", ["resume_id"])

    # 5. updated_at 触发器(沿用 v1 0001 的 public.set_updated_at() 函数)
    for table in TABLES_WITH_UPDATED_AT_TRIGGER:
        op.execute(
            f"""
            CREATE TRIGGER tg_{table}_set_updated_at
            BEFORE UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()
            """
        )


def downgrade() -> None:
    raise NotImplementedError(
        "M0 v1→v2 切换不支持 downgrade(语义不兼容);"
        "需要回退请走 git checkout v0.1-jobcopilot-v1 重建数据库。"
    )
