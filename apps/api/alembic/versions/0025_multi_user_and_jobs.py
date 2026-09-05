"""P0-P3: users / balances / jobs + 全业务表 user_id 归属

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-05

三件事:

1. **重建 users**(v1 的 users 在 0016 被 DROP)+ 余额账本两张表。
2. **给 11 张业务表加 `user_id`**。已有数据不能丢,所以分三步:
   先加可空列 → 建一个 legacy 用户并把存量行全部归给它 → 置 NOT NULL。
   全新库里没有存量行,legacy 用户也就不会被创建。
3. **异步任务表** `jobs` / `job_events`。

唯一约束跟着改口径:`uq_notes_folder_title` 是全局唯一的,多用户下会让
两个用户不能有同名笔记 —— 改成 `uq_notes_user_folder_title`。
`uq_kg_path` 同理。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025"
down_revision: str | Sequence[str] | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 加 user_id 的业务表。顺序无关,索引名统一 ix_<table>_user_id。
SCOPED_TABLES: tuple[str, ...] = (
    "notes",
    "note_chunks",
    "questions",
    "quiz_sessions",
    "session_answers",
    "session_events",
    "knowledge_gaps",
    "jds",
    "jd_analyses",
    "resumes",
    "resume_analyses",
)

# 存量数据的兜底归属账号。密码哈希是占位值,不对应任何可登录口令 ——
# 想用这个账号必须先通过 /api/auth 注册流程之外的手段重置密码。
LEGACY_EMAIL = "legacy@jobcopilot.local"


def upgrade() -> None:
    _create_users()
    _create_balances()
    _create_jobs()
    _add_user_id_columns()
    _rescope_unique_constraints()


def _create_users() -> None:
    op.create_table(
        "users",
        sa.Column(
            "id", sa.BigInteger(), sa.Identity(always=True), primary_key=True
        ),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("name", sa.String(100)),
        sa.Column(
            "locale", sa.String(10), nullable=False, server_default="zh-CN"
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "settings",
            postgresql.JSONB(),
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
    )
    # 软删后邮箱可以复用,所以唯一性只约束未删除行。
    op.create_index(
        "uq_users_email",
        "users",
        ["email"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.execute(
        """
        CREATE TRIGGER tg_users_set_updated_at
        BEFORE UPDATE ON users
        FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()
        """
    )


def _create_balances() -> None:
    op.create_table(
        "user_balances",
        sa.Column("user_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "balance_cny", sa.Numeric(14, 6), nullable=False, server_default="0"
        ),
        sa.Column(
            "total_topup_cny", sa.Numeric(14, 6), nullable=False, server_default="0"
        ),
        sa.Column(
            "total_spent_cny", sa.Numeric(14, 6), nullable=False, server_default="0"
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.execute(
        """
        CREATE TRIGGER tg_user_balances_set_updated_at
        BEFORE UPDATE ON user_balances
        FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()
        """
    )

    op.create_table(
        "balance_transactions",
        sa.Column(
            "id", sa.BigInteger(), sa.Identity(always=True), primary_key=True
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("amount_cny", sa.Numeric(14, 6), nullable=False),
        sa.Column("balance_after_cny", sa.Numeric(14, 6), nullable=False),
        sa.Column("feature", sa.String(50)),
        sa.Column("channel", sa.String(20)),
        sa.Column("llm_call_id", sa.BigInteger()),
        sa.Column("job_id", sa.BigInteger()),
        sa.Column("note", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "kind IN ('topup', 'charge', 'adjust')",
            name="ck_balance_transactions_kind",
        ),
    )
    op.create_index(
        "ix_balance_transactions_user_id_id",
        "balance_transactions",
        ["user_id", "id"],
    )
    # 成本归因:按用户 × 功能 × 渠道聚合流水。
    op.create_index(
        "ix_balance_transactions_user_feature",
        "balance_transactions",
        ["user_id", "channel", "feature"],
    )


def _create_jobs() -> None:
    op.create_table(
        "jobs",
        sa.Column(
            "id", sa.BigInteger(), sa.Identity(always=True), primary_key=True
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column(
            "status", sa.String(30), nullable=False, server_default="queued"
        ),
        sa.Column("dedupe_key", sa.String(200)),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("resource_kind", sa.String(40)),
        sa.Column("resource_id", sa.BigInteger()),
        sa.Column("error_code", sa.String(60)),
        sa.Column("error_detail", sa.Text()),
        sa.Column(
            "attempt_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("last_seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deadline_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
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
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', "
            "'insufficient_balance', 'deadline_exceeded')",
            name="ck_jobs_status",
        ),
    )
    op.create_index("ix_jobs_user_id_id", "jobs", ["user_id", "id"])
    # worker 领取用:WHERE status='queued' ORDER BY id。
    op.create_index(
        "ix_jobs_queued",
        "jobs",
        ["id"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    # 队列水位统计 + 超期回收都扫非终态行。
    op.create_index(
        "ix_jobs_active_deadline",
        "jobs",
        ["deadline_at"],
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    # 去重只在非终态行上生效:任务跑完之后同一个键可以再次入队。
    op.create_index(
        "uq_jobs_dedupe_active",
        "jobs",
        ["user_id", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text(
            "dedupe_key IS NOT NULL AND status IN ('queued', 'running')"
        ),
    )
    op.execute(
        """
        CREATE TRIGGER tg_jobs_set_updated_at
        BEFORE UPDATE ON jobs
        FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()
        """
    )

    op.create_table(
        "job_events",
        sa.Column(
            "id", sa.BigInteger(), sa.Identity(always=True), primary_key=True
        ),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(40), nullable=False),
        sa.Column(
            "data",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
    )
    # 订阅按 (job_id, seq) 顺序读;唯一性保证 seq 不会重号。
    op.create_index(
        "uq_job_events_job_seq", "job_events", ["job_id", "seq"], unique=True
    )
    op.create_index("ix_job_events_user_id", "job_events", ["user_id"])


def _add_user_id_columns() -> None:
    for table in SCOPED_TABLES:
        op.add_column(table, sa.Column("user_id", sa.BigInteger(), nullable=True))

    legacy_id = _backfill_legacy_owner()

    for table in SCOPED_TABLES:
        if legacy_id is not None:
            op.execute(
                sa.text(f"UPDATE {table} SET user_id = :uid WHERE user_id IS NULL").bindparams(
                    uid=legacy_id
                )
            )
        op.alter_column(table, "user_id", nullable=False)
        op.create_index(f"ix_{table}_user_id", table, ["user_id"])


def _backfill_legacy_owner() -> int | None:
    """存量数据存在时才建 legacy 用户,并返回它的 id。"""
    bind = op.get_bind()
    has_rows = any(
        bind.execute(
            sa.text(f"SELECT EXISTS (SELECT 1 FROM {table} LIMIT 1)")
        ).scalar()
        for table in SCOPED_TABLES
    )
    if not has_rows:
        return None

    # 占位哈希:格式合法但没有对应明文口令,不构成可登录账号。
    row = bind.execute(
        sa.text(
            """
            INSERT INTO users (email, password_hash, name)
            VALUES (:email, :hash, :name)
            RETURNING id
            """
        ).bindparams(
            email=LEGACY_EMAIL,
            hash="pbkdf2_sha256$240000$00$00",
            name="存量数据归属账号",
        )
    ).scalar_one()
    bind.execute(
        sa.text(
            "INSERT INTO user_balances (user_id) VALUES (:uid)"
        ).bindparams(uid=row)
    )
    return int(row)


def _rescope_unique_constraints() -> None:
    # 笔记同名判重从全局改成按用户,否则两个用户不能有同名笔记。
    op.drop_index("uq_notes_folder_title", table_name="notes")
    op.create_index(
        "uq_notes_user_folder_title",
        "notes",
        ["user_id", "folder_path", "title"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_constraint("uq_kg_path", "knowledge_gaps", type_="unique")
    op.create_unique_constraint(
        "uq_kg_user_path",
        "knowledge_gaps",
        ["user_id", "folder_path", "heading_path"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_kg_user_path", "knowledge_gaps", type_="unique")
    op.create_unique_constraint(
        "uq_kg_path", "knowledge_gaps", ["folder_path", "heading_path"]
    )
    op.drop_index("uq_notes_user_folder_title", table_name="notes")
    op.create_index(
        "uq_notes_folder_title",
        "notes",
        ["folder_path", "title"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    for table in SCOPED_TABLES:
        op.drop_index(f"ix_{table}_user_id", table_name=table)
        op.drop_column(table, "user_id")

    op.drop_index("ix_job_events_user_id", table_name="job_events")
    op.drop_index("uq_job_events_job_seq", table_name="job_events")
    op.drop_table("job_events")
    op.execute("DROP TRIGGER IF EXISTS tg_jobs_set_updated_at ON jobs")
    op.drop_index("uq_jobs_dedupe_active", table_name="jobs")
    op.drop_index("ix_jobs_active_deadline", table_name="jobs")
    op.drop_index("ix_jobs_queued", table_name="jobs")
    op.drop_index("ix_jobs_user_id_id", table_name="jobs")
    op.drop_table("jobs")

    op.drop_index(
        "ix_balance_transactions_user_feature", table_name="balance_transactions"
    )
    op.drop_index(
        "ix_balance_transactions_user_id_id", table_name="balance_transactions"
    )
    op.drop_table("balance_transactions")
    op.execute(
        "DROP TRIGGER IF EXISTS tg_user_balances_set_updated_at ON user_balances"
    )
    op.drop_table("user_balances")
    op.execute("DROP TRIGGER IF EXISTS tg_users_set_updated_at ON users")
    op.drop_index("uq_users_email", table_name="users")
    op.drop_table("users")
