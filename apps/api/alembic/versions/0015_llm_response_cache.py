"""llm_response_cache + llm_calls.cached

S21 子任务 4-B(Prompt cache layer)。

DashScope 没有 server-side prompt caching(Anthropic 才有);评测和 dogfood
阶段同 prompt 反复跑成本线性放大,客户端做 response cache 直接降一个数量级,
让 4-C/D 评测跑得起。

设计:
- `llm_response_cache.cache_key` = sha256(model || system || user_augmented ||
  response_format || thinking_mode || prompt_version_id) hex,UNIQUE。
- `request` JSONB 存原始 system / user / response_format / thinking_mode
  (dogfood 阶段无 PII,可观测必备 — 命中错配时能直接看 prompt 漂移在哪)。
- `response` JSONB 存 content / tokens_in / tokens_out / cached_tokens(原
  ProviderResponse 字段)。
- `created_at` / `last_hit_at` / `hit_count` 三个观测字段,put 走 INSERT ...
  ON CONFLICT DO UPDATE 增 hit_count + 推 last_hit_at,并发碰撞与 hit 计数
  统一在 SQL 层做。
- `llm_calls.cached` 新列(默认 false)记录单次调用是否命中 cache,跑完一
  天 dogfood 用 `SELECT feature, AVG(cached::int)` 直接看命中率。

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_response_cache",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("model", sa.String(50), nullable=False),
        sa.Column("feature", sa.String(50), nullable=False),
        sa.Column("prompt_version_id", sa.BigInteger()),
        sa.Column("request", postgresql.JSONB, nullable=False),
        sa.Column("response", postgresql.JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_hit_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "hit_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.UniqueConstraint("cache_key", name="uq_lrc_cache_key"),
        sa.ForeignKeyConstraint(
            ["prompt_version_id"],
            ["prompt_versions.id"],
            ondelete="SET NULL",
            name="fk_lrc_prompt_version_id",
        ),
    )
    op.create_index(
        "idx_lrc_feature_created",
        "llm_response_cache",
        ["feature", sa.text("created_at DESC")],
    )

    op.add_column(
        "llm_calls",
        sa.Column(
            "cached",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "idx_lc_feature_cached",
        "llm_calls",
        ["feature", "cached"],
    )


def downgrade() -> None:
    op.drop_index("idx_lc_feature_cached", table_name="llm_calls")
    op.drop_column("llm_calls", "cached")

    op.drop_index("idx_lrc_feature_created", table_name="llm_response_cache")
    op.drop_table("llm_response_cache")
