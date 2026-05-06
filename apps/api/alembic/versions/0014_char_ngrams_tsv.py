"""char_ngrams SQL 函数 + 重建 profile_chunks.content_tsv GENERATED 表达式

S21 子任务 4-A(Hybrid Search)第 1 步。

原 0005 把 `content_tsv` 建成 `to_tsvector('simple', content)`,但 `simple`
配置不分词,中文连续文本被当成单一长 lexeme,GIN 索引 `idx_pc_content_tsv`
对中文 lexical 检索基本失效(英文按空格切勉强能用)。

本迁移引入字符 n-gram 切分(bigram + ASCII unigram),源端在 SQL 函数里
做,与 Python 端 `tokenize_ngram` 输出严格一致(测试 `tests/integration/
test_tokenize_ngram_consistency.py` 守护一致性)。

切分规则:
1. lower(s) — `lower()` 在 Postgres 16 是 IMMUTABLE 的,可在 GENERATED 列里安全使用
2. 把所有非 [a-z0-9 中文(U+4E00-U+9FFF)] 字符替换为单空格 + trim
3. 抽取所有 ASCII 单词作为 unigram(`Python` / `LangGraph` 等技术名词整体匹配)
4. 字符滑窗 bigram,跳过含空格的 bigram(不跨"段"产生噪声 bigram)

GENERATED STORED 列在 PG 16 不能直接 ALTER 表达式 — 必须 DROP COLUMN +
ADD COLUMN(GIN 索引连带 drop / 自动按新表达式重算 / 重建)。`profile_chunks`
体量小(单 profile ~50 行,系统总量百级),重算成本可忽略。

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CHAR_NGRAMS_FN_SQL = r"""
CREATE OR REPLACE FUNCTION public.char_ngrams(s text)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $f$
    SELECT trim(coalesce(words.w, '') || ' ' || coalesce(bgs.b, ''))
    FROM (
        SELECT trim(regexp_replace(
            lower(coalesce(s, '')),
            '[^a-z0-9一-鿿]+',
            ' ',
            'g'
        )) AS t
    ) AS norm
    LEFT JOIN LATERAL (
        SELECT string_agg(m[1], ' ') AS w
        FROM regexp_matches(norm.t, '[a-z0-9]+', 'g') AS m
    ) AS words ON true
    LEFT JOIN LATERAL (
        SELECT string_agg(substring(norm.t FROM i FOR 2), ' ') AS b
        FROM generate_series(1, greatest(char_length(norm.t) - 1, 0)) AS i
        WHERE position(' ' IN substring(norm.t FROM i FOR 2)) = 0
          AND char_length(substring(norm.t FROM i FOR 2)) = 2
    ) AS bgs ON true;
$f$;
"""


def upgrade() -> None:
    op.execute(CHAR_NGRAMS_FN_SQL)

    # 1. drop 旧 GIN 索引(STORED GENERATED 列不能 ALTER 表达式 → 必须 drop col)
    op.drop_index(
        "idx_pc_content_tsv", table_name="profile_chunks", postgresql_using="gin"
    )
    op.drop_column("profile_chunks", "content_tsv")

    # 2. 加回 content_tsv,源端走 char_ngrams(content)
    op.add_column(
        "profile_chunks",
        sa.Column(
            "content_tsv",
            postgresql.TSVECTOR,
            sa.Computed(
                "to_tsvector('simple', public.char_ngrams(content))",
                persisted=True,
            ),
        ),
    )

    # 3. 重建 GIN
    op.create_index(
        "idx_pc_content_tsv",
        "profile_chunks",
        ["content_tsv"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_pc_content_tsv", table_name="profile_chunks", postgresql_using="gin"
    )
    op.drop_column("profile_chunks", "content_tsv")
    op.add_column(
        "profile_chunks",
        sa.Column(
            "content_tsv",
            postgresql.TSVECTOR,
            sa.Computed("to_tsvector('simple', content)", persisted=True),
        ),
    )
    op.create_index(
        "idx_pc_content_tsv",
        "profile_chunks",
        ["content_tsv"],
        postgresql_using="gin",
    )
    op.execute("DROP FUNCTION IF EXISTS public.char_ngrams(text)")
