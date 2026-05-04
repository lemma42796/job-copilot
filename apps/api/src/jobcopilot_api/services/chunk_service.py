"""Chunk service — rebuild a profile's `profile_chunks` rows (S8 C4).

Pipeline
--------

1. **Phase A — read** (own session): load Profile (ownership-checked) +
   experience/project/skill child tables. Educations are intentionally
   skipped: `chunker_version=v1` covers 4 granularities only.
2. **Phase B — build** (pure): `agents.chunker.build_chunks(...)` →
   `list[ChunkInput]`.
3. **Phase C — embed** (no DB connection held): batched at
   `EMBED_BATCH_LIMIT=10`. Embedder runs OUTSIDE any session so we
   never hold a Postgres connection across a slow LLM call.
4. **Phase D — write** (single tx): `DELETE FROM profile_chunks WHERE
   profile_id=?` + bulk `INSERT` of the new rows. Failing mid-write
   rolls back; failing during embed leaves the previous chunks intact.

Why not write inside Phase A's session: rebuild may be triggered after
a long parse. Holding the connection across `embedder.embed()` would
pin a pool slot for hundreds of ms. Splitting also lets us observe
`embed_*` metrics per call before the write commits.

S8 scope: embedding cost is structlog-only — `llm_calls` schema unify
is M2 #12.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobcopilot_api.agents.chunker import CHUNKER_VERSION, ChunkInput, build_chunks
from jobcopilot_api.llm.embedders import EMBED_BATCH_LIMIT, Embedder
from jobcopilot_api.models import ProfileChunk
from jobcopilot_api.services.profile_service import get_children, get_profile

log = structlog.get_logger(__name__)

EMBED_VERSION = CHUNKER_VERSION  # rule version stored on each row; bump together for now


@dataclass(frozen=True)
class RebuildResult:
    """Per-rebuild summary handed back to the SSE router / logs."""

    chunks_written: int
    tokens_in: int
    cost_cny: Decimal
    embed_model: str


def _batched(items: list[ChunkInput], n: int) -> Iterator[list[ChunkInput]]:
    for i in range(0, len(items), n):
        yield items[i : i + n]


async def rebuild_for_profile(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    user_id: int,
    profile_id: int,
    embedder: Embedder,
    trace_id: str | None = None,
) -> RebuildResult:
    """Rebuild all chunks for `profile_id`. Raises `NotFoundError` if the
    profile is missing or owned by a different user.

    Embedder is called OUTSIDE the write transaction so a slow LLM
    request doesn't pin a DB connection. If the embed call raises, the
    previous chunks survive (no DELETE happens until embed succeeds).
    """
    async with sessionmaker() as session:
        profile = await get_profile(session, user_id=user_id, profile_id=profile_id)
        exps, projs, skills, edus = await get_children(session, profile_id=profile.id)

    chunk_inputs = build_chunks(
        profile=profile, experiences=exps, projects=projs, skills=skills, educations=edus
    )

    vectors: list[list[float]] = []
    tokens_in = 0
    cost_cny = Decimal("0")
    if chunk_inputs:
        for batch in _batched(chunk_inputs, EMBED_BATCH_LIMIT):
            result = await embedder.embed([c.content for c in batch])
            vectors.extend(result.vectors)
            tokens_in += result.tokens_in
            cost_cny += result.cost_cny

    embed_model = embedder.model
    async with sessionmaker() as session, session.begin():
        await session.execute(sa.delete(ProfileChunk).where(ProfileChunk.profile_id == profile_id))
        for chunk, vec in zip(chunk_inputs, vectors, strict=True):
            session.add(
                ProfileChunk(
                    profile_id=profile_id,
                    granularity=chunk.granularity,
                    source_table=chunk.source_table,
                    source_id=chunk.source_id,
                    content=chunk.content,
                    embedding=vec,
                    chunk_metadata=dict(chunk.metadata),
                    embed_model=embed_model,
                    embed_version=EMBED_VERSION,
                )
            )

    log.info(
        "chunk_service.rebuild",
        profile_id=profile_id,
        user_id=user_id,
        chunks_written=len(chunk_inputs),
        tokens_in=tokens_in,
        cost_cny=str(cost_cny),
        embed_model=embed_model,
        trace_id=trace_id,
    )
    return RebuildResult(
        chunks_written=len(chunk_inputs),
        tokens_in=tokens_in,
        cost_cny=cost_cny,
        embed_model=embed_model,
    )
