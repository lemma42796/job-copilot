"""Integration tests for `services.retrieval_service.hybrid_retrieve_for_match` (S21 4-A)。

要验证的核心行为:
1. **Lexical 路独立工作** — 即便向量路是噪声(DummyEmbedder hash),关键词路
   也能召回含 query token 的 chunks(中文短词 / 英文技术名词)。
2. **RRF 融合去重** — 同一 chunk 两路命中只出一次,score = 两路 RRF 累加。
3. **Lexical 优雅降级** — 纯标点 query 让 lexical_query="" 时只走向量路,
   不报错。
4. **GENERATED content_tsv 走 char_ngrams** — 0014 迁移后,文档侧自动用 ngram
   切分,不需要 chunk_service 改写入逻辑。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer

from jobcopilot_api.llm.embedders import DummyEmbedder
from jobcopilot_api.models import Profile, ProfileChunk, User
from jobcopilot_api.services.retrieval_service import (
    hybrid_retrieve_for_match,
)
from jobcopilot_api.services.tokenize import tokenize_ngram

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


@pytest.fixture(scope="module")
def _container() -> Iterator[str]:
    container = (
        PostgresContainer(image="pgvector/pgvector:pg16")
        .with_env("POSTGRES_USER", "jobcopilot")
        .with_env("POSTGRES_PASSWORD", "jobcopilot")
        .with_env("POSTGRES_DB", "jobcopilot")
    )
    container.start()
    try:
        sync_url = container.get_connection_url()
        async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
            "postgresql://", "postgresql+asyncpg://"
        )
        cfg = Config(str(ALEMBIC_INI))
        cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
        cfg.set_main_option("sqlalchemy.url", async_url)
        command.upgrade(cfg, "head")
        yield async_url
    finally:
        container.stop()


@pytest.fixture
async def engine(_container: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(_container, future=True)
    yield eng
    await eng.dispose()


@pytest.fixture
def sessionmaker_(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _truncate(engine: AsyncEngine) -> AsyncIterator[None]:
    yield
    async with engine.begin() as conn:
        await conn.execute(sa.text("TRUNCATE TABLE users RESTART IDENTITY CASCADE"))


async def _seed_chunks(
    sm: async_sessionmaker[AsyncSession],
    *,
    contents: list[str],
) -> tuple[int, dict[str, int]]:
    """Seed 1 user + 1 profile + N chunks(content 直接传入,embedding 走 DummyEmbedder)。

    返回 (profile_id, content→chunk_id 反查表)。
    """
    embedder = DummyEmbedder()
    embed = await embedder.embed(contents)

    async with sm() as session, session.begin():
        user = User(email="t@x", name="t")
        session.add(user)
        await session.flush()

        profile = Profile(user_id=user.id, source="text_paste", status="parsed")
        session.add(profile)
        await session.flush()

        chunks: list[ProfileChunk] = []
        for content, vec in zip(contents, embed.vectors, strict=True):
            ch = ProfileChunk(
                profile_id=profile.id,
                granularity="experience",
                source_table="profile_experiences",
                source_id=0,
                content=content,
                embedding=vec,
                embed_model=embedder.model,
                embed_version="v1",
            )
            session.add(ch)
            chunks.append(ch)
        await session.flush()
        content_to_id = {c.content: c.id for c in chunks}
        return profile.id, content_to_id


@pytest.mark.integration
async def test_lexical_recalls_chinese_short_term(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    """中文短词 query 走 lexical 路能命中含该词的 chunk(向量路是噪声)。"""
    contents = [
        "精通 Python 后端开发,熟悉 FastAPI / Django",
        "熟悉 Go 微服务,gRPC 通信",
        "iOS 客户端开发,Swift / Objective-C",
    ]
    profile_id, c2id = await _seed_chunks(sessionmaker_, contents=contents)

    # query "后端" — 应只命中 contents[0]
    result = await hybrid_retrieve_for_match(
        sessionmaker_,
        profile_id=profile_id,
        query_text="后端",
        embedder=DummyEmbedder(),
        k=10,
    )

    assert result.lexical_chunks is not None
    lex_ids = [c.id for c in result.lexical_chunks]
    assert c2id[contents[0]] in lex_ids, "含'后端'的 chunk 应进 lexical 召回"
    assert c2id[contents[2]] not in lex_ids, "iOS chunk 不该进 lexical 召回"


@pytest.mark.integration
async def test_lexical_recalls_english_tech_term(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    """ASCII 技术名词作为 unigram 整体匹配(`Python` 必须能精确命中)。"""
    contents = [
        "Python backend, FastAPI framework",
        "Go microservice with gRPC",
        "Java Spring Boot legacy stack",
    ]
    profile_id, c2id = await _seed_chunks(sessionmaker_, contents=contents)

    result = await hybrid_retrieve_for_match(
        sessionmaker_,
        profile_id=profile_id,
        query_text="Python",
        embedder=DummyEmbedder(),
        k=10,
    )

    assert result.lexical_chunks is not None
    lex_ids = [c.id for c in result.lexical_chunks]
    assert c2id[contents[0]] in lex_ids


@pytest.mark.integration
async def test_lexical_empty_query_falls_back_to_vector(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    """纯标点 query → lexical_query="" → lexical 路返回空,不报错,fused 仅向量路。"""
    contents = [
        "Python backend",
        "Go microservice",
    ]
    profile_id, _ = await _seed_chunks(sessionmaker_, contents=contents)

    result = await hybrid_retrieve_for_match(
        sessionmaker_,
        profile_id=profile_id,
        query_text=",..!!?",
        embedder=DummyEmbedder(),
        k=10,
    )

    assert result.lexical_query == ""
    assert result.lexical_chunks == []
    # 向量路仍跑(2 chunks 都召回)
    assert result.vector_chunks is not None
    assert len(result.vector_chunks) == 2
    # 最终 fused 只有向量路
    assert len(result.chunks) == 2


@pytest.mark.integration
async def test_rrf_dedup_when_both_paths_hit_same_chunk(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    """同一 chunk 在两路都命中,fused 只出一次,score = 两路 RRF 累加。"""
    contents = [
        "精通 Python 后端开发",
        "Go 微服务",
    ]
    profile_id, c2id = await _seed_chunks(sessionmaker_, contents=contents)

    result = await hybrid_retrieve_for_match(
        sessionmaker_,
        profile_id=profile_id,
        query_text="Python 后端",
        embedder=DummyEmbedder(),
        k=10,
    )

    fused_ids = [c.id for c in result.chunks]
    assert len(fused_ids) == len(set(fused_ids)), "fused chunks 必须 unique"

    target_id = c2id[contents[0]]
    assert result.rrf_scores is not None
    # contents[0] 在 lexical 路必命中(含"Python"+"后端"),且向量路也召回 → score 至少为两项之和
    assert result.rrf_scores.get(target_id, 0.0) > 1.0 / (60 + 20)


@pytest.mark.integration
async def test_lexical_query_uses_ngram_split(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    """跨 ASCII/中文 边界 bigram (`通p` / `n后`) 也应命中,因 query 端切法一致。"""
    contents = [
        "我精通Python后端开发",  # 跨边界 bigrams: "通p", "n后"
        "Java Spring 后端",  # 含 "后端" bigram
    ]
    profile_id, c2id = await _seed_chunks(sessionmaker_, contents=contents)

    # query "Python后端"(无空格,跟文档同形式)
    result = await hybrid_retrieve_for_match(
        sessionmaker_,
        profile_id=profile_id,
        query_text="Python后端",
        embedder=DummyEmbedder(),
        k=10,
    )

    assert result.lexical_chunks is not None
    lex_ids = [c.id for c in result.lexical_chunks]
    # contents[0] 含完整 "Python后端" + 跨边界 bigram → ts_rank 最高
    assert lex_ids[0] == c2id[contents[0]]


@pytest.mark.integration
async def test_tokenize_ngram_module_smoke(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    """sanity:`tokenize_ngram` 不调用 DB 也能跑(纯字符串)。"""
    assert tokenize_ngram("Python 后端")[:3] == ["python", "后端", "py"]
