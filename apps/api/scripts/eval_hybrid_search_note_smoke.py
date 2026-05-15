"""Run note/chunk-level smoke for the M2 hybrid search pipeline.

This is intentionally smaller than a formal eval:
- reads `evals/suites/hybrid_search/dataset.note_smoke.jsonl`
- runs the same query rewrite -> hybrid search -> optional rerank -> parent-doc path
- prints top notes, top chunks, heading/anchor coverage, hard-negative intrusion,
  coarse-rank diagnostics, rerank movement, formal retrieval metrics,
  zero-hit behavior, and cost
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobcopilot_api.errors import NoChunksForQueryError
from jobcopilot_api.infra import embedder as embedder_infra
from jobcopilot_api.infra import llm as llm_infra
from jobcopilot_api.infra.db import get_engine
from jobcopilot_api.infra.langfuse import shutdown_langfuse
from jobcopilot_api.models.llm_call import LlmCall
from jobcopilot_api.models.note_chunk import NoteChunk
from jobcopilot_api.schemas.retrieval import PipelineResult, RetrievedChunk
from jobcopilot_api.services.query_rewriter import query_weights, rewrite_query
from jobcopilot_api.services.reranker import (
    QWEN3_RERANK_MAX_DOCUMENTS,
    rerank,
    reset_http_client,
)
from jobcopilot_api.services.retrieval_pipeline import (
    HYBRID_TOP_K_PER_QUERY,
    MIN_CHUNKS_FOR_QUIZ,
    RERANK_TOP_K,
    RRF_K,
    expand_to_parent_docs,
    fetch_note_titles,
    multi_query_rrf,
)
from jobcopilot_api.services.search_service import (
    HybridSearchDiagnostics,
    RRF_K as HYBRID_ROUTE_RRF_K,
    global_hybrid_search_with_diagnostics,
)
from jobcopilot_api.settings import settings


RERANK_DIAGNOSTIC_TOP_K = 50
COARSE_RANK_DIAGNOSTIC_TOP_K = 50
COARSE_RANK_NEIGHBOR_WINDOW = 5
LOW_COARSE_RANK_THRESHOLD = 20
CONTENT_PREVIEW_CHARS = 360
ORIGINAL_QUERY_WEIGHT_SIMULATION = 2.0


@dataclass(frozen=True)
class SmokeCase:
    id: str
    query: str
    direct_evidence_chunk_ids: list[int]
    necessary_context_chunk_ids: list[int]
    expected_note_paths: list[str]
    hard_negative_note_paths: list[str]
    expected_heading_paths: list[list[str]]
    evidence_anchors: list[str]
    expected_zero_hit: bool
    notes: str


@dataclass(frozen=True)
class ChunkHit:
    rank: int
    chunk_id: int
    note_path: str
    heading_path: list[str]
    rerank_score: float
    expected_note: bool
    hard_negative_note: bool
    direct_evidence: bool
    necessary_context: bool
    expected_heading: bool
    matched_anchors: list[str]


@dataclass(frozen=True)
class TraceRerankMovement:
    chunk_id: int
    note_path: str
    heading_path: list[str]
    candidate_rank: int | None
    rerank_rank: int | None
    rank_delta: int | None
    rerank_score: float | None


@dataclass(frozen=True)
class TraceCoarseQueryRank:
    query_index: int
    query: str
    query_weight: float
    query_hybrid_rank: int | None
    query_hybrid_rrf_score: float | None
    base_cross_query_rrf_contribution: float | None
    cross_query_rrf_contribution: float | None
    vector_rank: int | None
    vector_distance: float | None
    vector_rrf_contribution: float | None
    lexical_rank: int | None
    lexical_score: float | None
    lexical_rrf_contribution: float | None


@dataclass(frozen=True)
class TraceCoarseRankDiagnostic:
    chunk_id: int
    note_path: str
    heading_path: list[str]
    candidate_rank: int | None
    cross_query_rrf_score: float | None
    content_preview: str
    query_ranks: list[TraceCoarseQueryRank]


@dataclass(frozen=True)
class RerankMovement:
    chunk_id: int
    note_path: str
    heading_path: list[str]
    candidate_rank: int | None
    rerank_rank: int | None
    rank_delta: int | None
    rerank_score: float | None
    expected_note: bool
    hard_negative_note: bool
    direct_evidence: bool
    necessary_context: bool


@dataclass(frozen=True)
class CoarseQueryRank:
    query_index: int
    query: str
    query_weight: float
    query_hybrid_rank: int | None
    query_hybrid_rrf_score: float | None
    base_cross_query_rrf_contribution: float | None
    cross_query_rrf_contribution: float | None
    vector_rank: int | None
    vector_distance: float | None
    vector_rrf_contribution: float | None
    lexical_rank: int | None
    lexical_score: float | None
    lexical_rrf_contribution: float | None


@dataclass(frozen=True)
class CoarseRankDiagnostic:
    chunk_id: int
    note_path: str
    heading_path: list[str]
    candidate_rank: int | None
    cross_query_rrf_score: float | None
    content_preview: str
    query_ranks: list[CoarseQueryRank]
    expected_note: bool
    hard_negative_note: bool
    direct_evidence: bool
    necessary_context: bool
    reason_hints: list[str]


@dataclass(frozen=True)
class QueryVoteDiagnostic:
    query_index: int
    query: str
    role: str
    query_weight: float
    contributed_candidate_count: int
    relevant_chunk_ids: list[int]
    direct_evidence_chunk_ids: list[int]
    necessary_context_chunk_ids: list[int]
    hard_negative_chunk_ids: list[int]
    best_relevant_rank: int | None
    best_direct_evidence_rank: int | None
    best_hard_negative_rank: int | None
    top_labeled_chunks: list[str]
    hints: list[str]


@dataclass(frozen=True)
class OriginalQueryWeightSimulation:
    chunk_id: int
    note_path: str
    heading_path: list[str]
    original_rank: int | None
    weighted_rank: int | None
    rank_delta: int | None
    original_score: float
    weighted_score: float
    expected_note: bool
    hard_negative_note: bool
    direct_evidence: bool
    necessary_context: bool
    hints: list[str]


@dataclass(frozen=True)
class TraceFinalChunk:
    chunk_id: int
    note_path: str
    heading_path: list[str]
    rerank_score: float
    content: str


@dataclass(frozen=True)
class SmokeTrace:
    case_id: str
    query: str
    predicted_zero_hit: bool
    rerank_mode: str
    rerank_input_top_k: int
    selected_top_k: int
    parent_doc_mode: str
    expanded_queries: list[str]
    candidate_chunk_ids: list[int]
    rerank_chunk_ids: list[int]
    coarse_rank_diagnostics: list[TraceCoarseRankDiagnostic]
    rerank_movements: list[TraceRerankMovement]
    final_chunks: list[TraceFinalChunk]
    rerank_tokens: int
    rerank_cost_cny: Decimal
    error: str = ""


@dataclass(frozen=True)
class PipelineTrace:
    result: PipelineResult
    candidate_chunks: list[RetrievedChunk]
    reranked_chunks: list[RetrievedChunk]
    coarse_rank_diagnostics: list[TraceCoarseRankDiagnostic]
    rerank_movements: list[TraceRerankMovement]
    rerank_mode: str
    rerank_input_top_k: int
    selected_top_k: int
    parent_doc_mode: str
    rerank_tokens: int
    rerank_cost_cny: Decimal
    predicted_zero_hit: bool = False
    error: str = ""


@dataclass(frozen=True)
class SmokeResult:
    case: SmokeCase
    passed: bool
    predicted_zero_hit: bool
    rerank_mode: str
    rerank_input_top_k: int
    selected_top_k: int
    parent_doc_mode: str
    expanded_queries: list[str]
    candidate_chunk_ids: list[int]
    rerank_chunk_ids: list[int]
    coarse_rank_diagnostics: list[CoarseRankDiagnostic]
    rerank_movements: list[RerankMovement]
    top_note_paths: list[str]
    top_chunks: list[ChunkHit]
    candidate_direct_evidence_hits: list[int]
    rerank_direct_evidence_hits: list[int]
    expected_hits: list[str]
    direct_evidence_hits: list[int]
    necessary_context_hits: list[int]
    expected_heading_hits: list[list[str]]
    evidence_anchor_hits: list[str]
    hard_negative_hits: list[str]
    hard_negative_ranks: dict[str, int]
    failure_hints: list[str]
    candidate_recall_at_50: float | None
    rerank_recall_at_10: float | None
    mrr_at_10: float | None
    final_context_recall: float | None
    final_context_precision: float | None
    retrieved_chunk_count: int
    rerank_tokens: int
    rerank_cost_cny: Decimal
    error: str = ""


def load_cases(path: Path) -> list[SmokeCase]:
    cases: list[SmokeCase] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        obj = json.loads(raw)
        gt = obj["ground_truth"]
        direct_evidence_chunk_ids = [
            int(x)
            for x in gt.get("direct_evidence_chunk_ids", [])
        ]
        cases.append(
            SmokeCase(
                id=str(obj["id"]),
                query=str(obj["input"]["query"]),
                direct_evidence_chunk_ids=direct_evidence_chunk_ids,
                necessary_context_chunk_ids=[
                    int(x) for x in gt.get("necessary_context_chunk_ids", [])
                ],
                expected_note_paths=[str(x) for x in gt["expected_note_paths"]],
                hard_negative_note_paths=[
                    str(x) for x in gt["hard_negative_note_paths"]
                ],
                expected_heading_paths=[
                    [str(part) for part in path]
                    for path in gt.get("expected_heading_paths", [])
                ],
                evidence_anchors=[
                    str(anchor) for anchor in gt.get("evidence_anchors", [])
                ],
                expected_zero_hit=bool(gt["expected_zero_hit"]),
                notes=str(obj.get("notes", "")),
            )
        )
    return cases


async def run_pipeline_with_cost(
    session: AsyncSession,
    user_query: str,
    *,
    rerank_mode: str,
    rerank_input_top_k: int,
    selected_top_k: int,
    parent_doc_mode: str,
    diagnostic_chunk_ids: list[int],
) -> PipelineTrace:
    rewrite_out = await rewrite_query(user_query)
    expanded_queries = rewrite_out.expanded_queries
    weights = query_weights(rewrite_out)

    hybrid_rankings = []
    query_diagnostics: list[HybridSearchDiagnostics] = []
    for q in expanded_queries:
        diagnostic = await global_hybrid_search_with_diagnostics(
            session, q, top_k=HYBRID_TOP_K_PER_QUERY
        )
        ranking = diagnostic.fused_chunks
        query_diagnostics.append(diagnostic)
        hybrid_rankings.append(ranking)
    fused = multi_query_rrf(hybrid_rankings, k=RRF_K, weights=weights)
    rerank_input = fused[:rerank_input_top_k]
    candidate_base = fused[:COARSE_RANK_DIAGNOSTIC_TOP_K]
    coarse_rank_diagnostics = await build_trace_coarse_rank_diagnostics(
        session,
        candidate_base,
        query_diagnostics,
        diagnostic_chunk_ids,
        weights,
    )
    diagnostic_top_k = min(
        max(RERANK_DIAGNOSTIC_TOP_K, selected_top_k),
        rerank_input_top_k,
    )

    if len(fused) < MIN_CHUNKS_FOR_QUIZ:
        note_titles = await fetch_note_titles(
            session, list({chunk.note_id for chunk in candidate_base})
        )
        candidate_chunks = [
            RetrievedChunk(
                chunk=chunk,
                folder_path=list(chunk.folder_path),
                heading_path=list(chunk.heading_path),
                note_title=note_titles.get(chunk.note_id, ""),
                rerank_score=0.0,
            )
            for chunk in candidate_base
        ]
        detail = f"query='{user_query}' hit {len(fused)} chunks"
        return PipelineTrace(
            result=PipelineResult(
                expanded_queries=expanded_queries,
                retrieved_chunks=[],
            ),
            candidate_chunks=candidate_chunks,
            reranked_chunks=[],
            coarse_rank_diagnostics=coarse_rank_diagnostics,
            rerank_movements=build_trace_rerank_movements(
                candidate_base,
                candidate_base,
                [],
                note_titles,
            ),
            rerank_mode=rerank_mode,
            rerank_input_top_k=rerank_input_top_k,
            selected_top_k=selected_top_k,
            parent_doc_mode=parent_doc_mode,
            rerank_tokens=0,
            rerank_cost_cny=Decimal("0"),
            predicted_zero_hit=True,
            error=detail,
        )

    if rerank_mode == "provider":
        rerank_result = await rerank(
            user_query,
            rerank_input,
            top_k=diagnostic_top_k,
        )
        diagnostic_scored = rerank_result.scored
        top_scored = rerank_result.scored[:selected_top_k]
        rerank_tokens = rerank_result.total_tokens
        rerank_cost_cny = rerank_result.cost_cny
    elif rerank_mode == "none":
        diagnostic_scored = [
            (chunk, None)
            for chunk in rerank_input[:diagnostic_top_k]
        ]
        top_scored = [(chunk, 0.0) for chunk in rerank_input[:selected_top_k]]
        rerank_tokens = 0
        rerank_cost_cny = Decimal("0")
    else:
        raise ValueError(f"unsupported rerank_mode: {rerank_mode}")

    if parent_doc_mode == "on":
        expanded_scored = await expand_to_parent_docs(session, top_scored)
    elif parent_doc_mode == "off":
        expanded_scored = top_scored
    else:
        raise ValueError(f"unsupported parent_doc_mode: {parent_doc_mode}")
    all_chunks = [
        *candidate_base,
        *[chunk for chunk, _ in diagnostic_scored],
        *[chunk for chunk, _ in expanded_scored],
    ]
    note_ids = list({chunk.note_id for chunk in all_chunks})
    note_titles = await fetch_note_titles(session, note_ids)
    candidate_chunks = [
        RetrievedChunk(
            chunk=chunk,
            folder_path=list(chunk.folder_path),
            heading_path=list(chunk.heading_path),
            note_title=note_titles.get(chunk.note_id, ""),
            rerank_score=0.0,
        )
        for chunk in fused[:COARSE_RANK_DIAGNOSTIC_TOP_K]
    ]
    reranked_chunks = [
        RetrievedChunk(
            chunk=chunk,
            folder_path=list(chunk.folder_path),
            heading_path=list(chunk.heading_path),
            note_title=note_titles.get(chunk.note_id, ""),
            rerank_score=score,
        )
        for chunk, score in top_scored
    ]
    rerank_movements = build_trace_rerank_movements(
        candidate_base,
        rerank_input,
        diagnostic_scored,
        note_titles,
    )
    final_chunks = [
        RetrievedChunk(
            chunk=chunk,
            folder_path=list(chunk.folder_path),
            heading_path=list(chunk.heading_path),
            note_title=note_titles.get(chunk.note_id, ""),
            rerank_score=score,
        )
        for chunk, score in expanded_scored
    ]
    return PipelineTrace(
        result=PipelineResult(
            expanded_queries=expanded_queries,
            retrieved_chunks=final_chunks,
        ),
        candidate_chunks=candidate_chunks,
        reranked_chunks=reranked_chunks,
        coarse_rank_diagnostics=coarse_rank_diagnostics,
        rerank_movements=rerank_movements,
        rerank_mode=rerank_mode,
        rerank_input_top_k=rerank_input_top_k,
        selected_top_k=selected_top_k,
        parent_doc_mode=parent_doc_mode,
        rerank_tokens=rerank_tokens,
        rerank_cost_cny=rerank_cost_cny,
    )


def note_path(item: RetrievedChunk) -> str:
    return "/".join([*item.folder_path, f"{item.note_title}.md"])


def chunk_note_path(chunk: NoteChunk, note_title: str) -> str:
    return "/".join([*chunk.folder_path, f"{note_title}.md"])


def rrf_contribution(rank: int | None, *, k: int) -> float | None:
    if rank is None:
        return None
    return 1.0 / (k + rank)


def normalized_preview(text: str, limit: int = CONTENT_PREVIEW_CHARS) -> str:
    preview = re.sub(r"\s+", " ", text).strip()
    if len(preview) <= limit:
        return preview
    return preview[: limit - 3].rstrip() + "..."


def cross_query_rrf_scores(
    rankings: list[list[NoteChunk]],
    *,
    k: int,
    weights: list[float] | None = None,
) -> dict[int, float]:
    scores: dict[int, float] = {}
    effective_weights = normalize_rrf_weights(weights, len(rankings))
    for ranked, weight in zip(rankings, effective_weights, strict=False):
        if weight <= 0:
            continue
        for rank_idx, chunk in enumerate(ranked, start=1):
            chunk_id = int(chunk.id)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (
                k + rank_idx
            )
    return scores


def normalize_rrf_weights(
    weights: list[float] | None,
    ranking_count: int,
) -> list[float]:
    if weights is None:
        return [1.0] * ranking_count
    normalized = [max(float(weight), 0.0) for weight in weights[:ranking_count]]
    if len(normalized) < ranking_count:
        normalized.extend([1.0] * (ranking_count - len(normalized)))
    return normalized


async def fetch_chunks_by_ids(
    session: AsyncSession,
    chunk_ids: set[int],
) -> list[NoteChunk]:
    if not chunk_ids:
        return []
    stmt = sa.select(NoteChunk).where(NoteChunk.id.in_(list(chunk_ids)))
    return list((await session.execute(stmt)).scalars().all())


async def build_trace_coarse_rank_diagnostics(
    session: AsyncSession,
    candidate_chunks: list[NoteChunk],
    query_diagnostics: list[HybridSearchDiagnostics],
    watched_chunk_ids: list[int],
    query_weights: list[float],
) -> list[TraceCoarseRankDiagnostic]:
    candidate_rank_by_id = {
        int(chunk.id): rank
        for rank, chunk in enumerate(
            candidate_chunks[:COARSE_RANK_DIAGNOSTIC_TOP_K],
            start=1,
        )
    }
    watched_ids = set(watched_chunk_ids)
    diagnostic_ids = set(candidate_rank_by_id) | watched_ids

    by_id: dict[int, NoteChunk] = {
        int(chunk.id): chunk
        for chunk in candidate_chunks[:COARSE_RANK_DIAGNOSTIC_TOP_K]
    }
    for diagnostic in query_diagnostics:
        for chunk in diagnostic.fused_all_chunks:
            chunk_id = int(chunk.id)
            if chunk_id in watched_ids:
                by_id.setdefault(chunk_id, chunk)

    missing_ids = diagnostic_ids - set(by_id)
    for chunk in await fetch_chunks_by_ids(session, missing_ids):
        by_id.setdefault(int(chunk.id), chunk)

    if not by_id:
        return []

    note_titles = await fetch_note_titles(
        session, list({chunk.note_id for chunk in by_id.values()})
    )
    cross_scores = cross_query_rrf_scores(
        [diagnostic.fused_chunks for diagnostic in query_diagnostics],
        k=RRF_K,
        weights=query_weights,
    )
    effective_query_weights = normalize_rrf_weights(
        query_weights, len(query_diagnostics)
    )

    query_maps: list[
        tuple[
            HybridSearchDiagnostics,
            dict[int, tuple[int, float]],
            dict[int, tuple[int, float]],
            dict[int, int],
            dict[int, int],
        ]
    ] = []
    for diagnostic in query_diagnostics:
        vector_by_id = {
            int(hit.chunk.id): (hit.rank, hit.score)
            for hit in diagnostic.vector_hits
        }
        lexical_by_id = {
            int(hit.chunk.id): (hit.rank, hit.score)
            for hit in diagnostic.lexical_hits
        }
        fused_rank_by_id = {
            int(chunk.id): rank
            for rank, chunk in enumerate(diagnostic.fused_all_chunks, start=1)
        }
        production_rank_by_id = {
            int(chunk.id): rank
            for rank, chunk in enumerate(diagnostic.fused_chunks, start=1)
        }
        query_maps.append(
            (
                diagnostic,
                vector_by_id,
                lexical_by_id,
                fused_rank_by_id,
                production_rank_by_id,
            )
        )

    def sort_key(chunk_id: int) -> tuple[int, int]:
        rank = candidate_rank_by_id.get(chunk_id)
        return (rank if rank is not None else 10**9, chunk_id)

    rows: list[TraceCoarseRankDiagnostic] = []
    for chunk_id in sorted(diagnostic_ids, key=sort_key):
        chunk = by_id.get(chunk_id)
        if chunk is None:
            continue
        query_ranks: list[TraceCoarseQueryRank] = []
        for query_index, (
            diagnostic,
            vector_by_id,
            lexical_by_id,
            fused_rank_by_id,
            production_rank_by_id,
        ) in enumerate(query_maps):
            vector_info = vector_by_id.get(chunk_id)
            lexical_info = lexical_by_id.get(chunk_id)
            query_hybrid_rank = fused_rank_by_id.get(chunk_id)
            production_rank = production_rank_by_id.get(chunk_id)
            query_weight = effective_query_weights[query_index]
            base_cross_contribution = rrf_contribution(
                production_rank,
                k=RRF_K,
            )
            cross_contribution = (
                None
                if base_cross_contribution is None
                else query_weight * base_cross_contribution
            )
            vector_rank = vector_info[0] if vector_info is not None else None
            lexical_rank = (
                lexical_info[0] if lexical_info is not None else None
            )
            query_ranks.append(
                TraceCoarseQueryRank(
                    query_index=query_index,
                    query=diagnostic.query,
                    query_weight=query_weight,
                    query_hybrid_rank=query_hybrid_rank,
                    query_hybrid_rrf_score=diagnostic.rrf_scores.get(chunk_id),
                    base_cross_query_rrf_contribution=base_cross_contribution,
                    cross_query_rrf_contribution=cross_contribution,
                    vector_rank=vector_rank,
                    vector_distance=(
                        vector_info[1] if vector_info is not None else None
                    ),
                    vector_rrf_contribution=rrf_contribution(
                        vector_rank,
                        k=HYBRID_ROUTE_RRF_K,
                    ),
                    lexical_rank=lexical_rank,
                    lexical_score=(
                        lexical_info[1] if lexical_info is not None else None
                    ),
                    lexical_rrf_contribution=rrf_contribution(
                        lexical_rank,
                        k=HYBRID_ROUTE_RRF_K,
                    ),
                )
            )
        rows.append(
            TraceCoarseRankDiagnostic(
                chunk_id=chunk_id,
                note_path=chunk_note_path(
                    chunk, note_titles.get(chunk.note_id, "")
                ),
                heading_path=list(chunk.heading_path),
                candidate_rank=candidate_rank_by_id.get(chunk_id),
                cross_query_rrf_score=cross_scores.get(chunk_id),
                content_preview=normalized_preview(chunk.content),
                query_ranks=query_ranks,
            )
        )
    return rows


def build_trace_rerank_movements(
    candidate_chunks: list[NoteChunk],
    rerank_input_chunks: list[NoteChunk],
    rerank_scored: list[tuple[NoteChunk, float | None]],
    note_titles: dict[int, str],
) -> list[TraceRerankMovement]:
    candidate_rank_by_id = {
        int(chunk.id): rank
        for rank, chunk in enumerate(rerank_input_chunks, start=1)
    }
    rerank_by_id = {
        int(chunk.id): (
            rank,
            float(score) if score is not None else None,
        )
        for rank, (chunk, score) in enumerate(
            rerank_scored[:RERANK_DIAGNOSTIC_TOP_K],
            start=1,
        )
    }
    by_id: dict[int, NoteChunk] = {}
    for chunk in candidate_chunks:
        by_id.setdefault(int(chunk.id), chunk)
    for chunk, _ in rerank_scored[:RERANK_DIAGNOSTIC_TOP_K]:
        by_id.setdefault(int(chunk.id), chunk)

    def sort_key(chunk: NoteChunk) -> tuple[int, int, int]:
        chunk_id = int(chunk.id)
        candidate_rank = candidate_rank_by_id.get(chunk_id)
        rerank_info = rerank_by_id.get(chunk_id)
        rerank_rank = rerank_info[0] if rerank_info is not None else None
        return (
            candidate_rank if candidate_rank is not None else 10**9,
            rerank_rank if rerank_rank is not None else 10**9,
            chunk_id,
        )

    movements: list[TraceRerankMovement] = []
    for chunk in sorted(by_id.values(), key=sort_key):
        chunk_id = int(chunk.id)
        candidate_rank = candidate_rank_by_id.get(chunk_id)
        rerank_info = rerank_by_id.get(chunk_id)
        if rerank_info is None:
            rerank_rank = None
            rerank_score = None
        else:
            rerank_rank, rerank_score = rerank_info
        rank_delta = (
            rerank_rank - candidate_rank
            if candidate_rank is not None and rerank_rank is not None
            else None
        )
        movements.append(
            TraceRerankMovement(
                chunk_id=chunk_id,
                note_path=chunk_note_path(
                    chunk, note_titles.get(chunk.note_id, "")
                ),
                heading_path=list(chunk.heading_path),
                candidate_rank=candidate_rank,
                rerank_rank=rerank_rank,
                rank_delta=rank_delta,
                rerank_score=rerank_score,
            )
        )
    return movements


def trace_final_chunk(item: RetrievedChunk) -> TraceFinalChunk:
    return TraceFinalChunk(
        chunk_id=int(item.chunk.id),
        note_path=note_path(item),
        heading_path=list(item.heading_path),
        rerank_score=float(item.rerank_score),
        content=item.chunk.content,
    )


def ordered_unique(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        out.append(item)
        seen.add(item)
    return out


def ordered_unique_int(items: list[int]) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for item in items:
        if item in seen:
            continue
        out.append(item)
        seen.add(item)
    return out


def normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def anchor_matches(content: str, anchor: str) -> bool:
    content_norm = normalize_for_match(content)
    anchor_norm = normalize_for_match(anchor)
    if anchor_norm and anchor_norm in content_norm:
        return True

    content_plain = content_norm.replace("`", "")
    anchor_plain = anchor_norm.replace("`", "")
    return bool(anchor_plain and anchor_plain in content_plain)


def path_is_prefix(prefix: list[str], path: list[str]) -> bool:
    return len(prefix) <= len(path) and path[: len(prefix)] == prefix


def heading_matches(actual: list[str], expected: list[str]) -> bool:
    if not actual or not expected:
        return actual == expected
    actual_norm = [normalize_for_match(part) for part in actual]
    expected_norm = [normalize_for_match(part) for part in expected]
    return path_is_prefix(expected_norm, actual_norm)


def build_chunk_hits(
    case: SmokeCase,
    final_chunks: list[TraceFinalChunk],
) -> list[ChunkHit]:
    direct_evidence_chunk_ids = set(case.direct_evidence_chunk_ids)
    necessary_context_chunk_ids = set(case.necessary_context_chunk_ids)
    out: list[ChunkHit] = []
    for rank, item in enumerate(final_chunks, start=1):
        chunk_id = item.chunk_id
        current_note_path = item.note_path
        matched_anchors = [
            anchor
            for anchor in case.evidence_anchors
            if anchor_matches(item.content, anchor)
        ]
        out.append(
            ChunkHit(
                rank=rank,
                chunk_id=chunk_id,
                note_path=current_note_path,
                heading_path=list(item.heading_path),
                rerank_score=item.rerank_score,
                expected_note=current_note_path in case.expected_note_paths,
                hard_negative_note=(
                    current_note_path in case.hard_negative_note_paths
                ),
                direct_evidence=chunk_id in direct_evidence_chunk_ids,
                necessary_context=chunk_id in necessary_context_chunk_ids,
                expected_heading=any(
                    heading_matches(item.heading_path, expected)
                    for expected in case.expected_heading_paths
                ),
                matched_anchors=matched_anchors,
            )
        )
    return out


def build_rerank_movements(
    case: SmokeCase,
    trace_movements: list[TraceRerankMovement],
) -> list[RerankMovement]:
    direct_evidence_chunk_ids = set(case.direct_evidence_chunk_ids)
    necessary_context_chunk_ids = set(case.necessary_context_chunk_ids)
    return [
        RerankMovement(
            chunk_id=item.chunk_id,
            note_path=item.note_path,
            heading_path=list(item.heading_path),
            candidate_rank=item.candidate_rank,
            rerank_rank=item.rerank_rank,
            rank_delta=item.rank_delta,
            rerank_score=item.rerank_score,
            expected_note=item.note_path in case.expected_note_paths,
            hard_negative_note=item.note_path in case.hard_negative_note_paths,
            direct_evidence=item.chunk_id in direct_evidence_chunk_ids,
            necessary_context=item.chunk_id in necessary_context_chunk_ids,
        )
        for item in trace_movements
    ]


def best_rank(ranks: list[int | None]) -> int | None:
    present = [rank for rank in ranks if rank is not None]
    return min(present) if present else None


def build_coarse_rank_diagnostics(
    case: SmokeCase,
    trace_rows: list[TraceCoarseRankDiagnostic],
) -> list[CoarseRankDiagnostic]:
    direct_evidence_chunk_ids = set(case.direct_evidence_chunk_ids)
    necessary_context_chunk_ids = set(case.necessary_context_chunk_ids)
    preliminary = [
        CoarseRankDiagnostic(
            chunk_id=item.chunk_id,
            note_path=item.note_path,
            heading_path=list(item.heading_path),
            candidate_rank=item.candidate_rank,
            cross_query_rrf_score=item.cross_query_rrf_score,
            content_preview=item.content_preview,
            query_ranks=[
                CoarseQueryRank(
                    query_index=query_rank.query_index,
                    query=query_rank.query,
                    query_weight=query_rank.query_weight,
                    query_hybrid_rank=query_rank.query_hybrid_rank,
                    query_hybrid_rrf_score=(
                        query_rank.query_hybrid_rrf_score
                    ),
                    base_cross_query_rrf_contribution=(
                        query_rank.base_cross_query_rrf_contribution
                    ),
                    cross_query_rrf_contribution=(
                        query_rank.cross_query_rrf_contribution
                    ),
                    vector_rank=query_rank.vector_rank,
                    vector_distance=query_rank.vector_distance,
                    vector_rrf_contribution=(
                        query_rank.vector_rrf_contribution
                    ),
                    lexical_rank=query_rank.lexical_rank,
                    lexical_score=query_rank.lexical_score,
                    lexical_rrf_contribution=(
                        query_rank.lexical_rrf_contribution
                    ),
                )
                for query_rank in item.query_ranks
            ],
            expected_note=item.note_path in case.expected_note_paths,
            hard_negative_note=item.note_path in case.hard_negative_note_paths,
            direct_evidence=item.chunk_id in direct_evidence_chunk_ids,
            necessary_context=item.chunk_id in necessary_context_chunk_ids,
            reason_hints=[],
        )
        for item in trace_rows
    ]
    return [
        CoarseRankDiagnostic(
            chunk_id=item.chunk_id,
            note_path=item.note_path,
            heading_path=item.heading_path,
            candidate_rank=item.candidate_rank,
            cross_query_rrf_score=item.cross_query_rrf_score,
            content_preview=item.content_preview,
            query_ranks=item.query_ranks,
            expected_note=item.expected_note,
            hard_negative_note=item.hard_negative_note,
            direct_evidence=item.direct_evidence,
            necessary_context=item.necessary_context,
            reason_hints=diagnose_coarse_rank(item, preliminary),
        )
        for item in preliminary
    ]


def diagnose_coarse_rank(
    item: CoarseRankDiagnostic,
    all_rows: list[CoarseRankDiagnostic],
) -> list[str]:
    if not item.direct_evidence and not item.necessary_context:
        return []

    hints: list[str] = []
    is_low = (
        item.candidate_rank is None
        or item.candidate_rank > LOW_COARSE_RANK_THRESHOLD
    )
    if item.candidate_rank is None:
        hints.append("missing_from_candidate_top50")
    elif item.candidate_rank > LOW_COARSE_RANK_THRESHOLD:
        hints.append("candidate_rank_after_top20")
    if not is_low:
        return hints

    vector_rank = best_rank([rank.vector_rank for rank in item.query_ranks])
    lexical_rank = best_rank([rank.lexical_rank for rank in item.query_ranks])
    query_hybrid_rank = best_rank(
        [rank.query_hybrid_rank for rank in item.query_ranks]
    )
    if vector_rank is None and lexical_rank is None:
        hints.append("both_routes_missed_top100")
    elif (
        vector_rank is not None
        and vector_rank <= LOW_COARSE_RANK_THRESHOLD
        and (
            lexical_rank is None
            or lexical_rank > LOW_COARSE_RANK_THRESHOLD
        )
    ):
        hints.append("vector_only_good")
    elif (
        lexical_rank is not None
        and lexical_rank <= LOW_COARSE_RANK_THRESHOLD
        and (
            vector_rank is None
            or vector_rank > LOW_COARSE_RANK_THRESHOLD
        )
    ):
        hints.append("lexical_only_good")

    route_good = (
        vector_rank is not None and vector_rank <= LOW_COARSE_RANK_THRESHOLD
    ) or (
        lexical_rank is not None and lexical_rank <= LOW_COARSE_RANK_THRESHOLD
    )
    if route_good and (
        query_hybrid_rank is None
        or query_hybrid_rank > LOW_COARSE_RANK_THRESHOLD
    ):
        hints.append("single_query_rrf_drop")

    if (
        query_hybrid_rank is not None
        and query_hybrid_rank <= LOW_COARSE_RANK_THRESHOLD
        and (
            item.candidate_rank is None
            or item.candidate_rank > LOW_COARSE_RANK_THRESHOLD
        )
    ):
        hints.append("cross_query_rrf_drop")
    elif (
        query_hybrid_rank is not None
        and query_hybrid_rank <= HYBRID_TOP_K_PER_QUERY
        and item.candidate_rank is None
    ):
        hints.append("cross_query_top50_drop")
    elif (
        query_hybrid_rank is not None
        and query_hybrid_rank > HYBRID_TOP_K_PER_QUERY
    ):
        hints.append("per_query_top50_cutoff")

    contributing_queries = sum(
        1
        for rank in item.query_ranks
        if rank.cross_query_rrf_contribution is not None
    )
    if contributing_queries <= 1:
        hints.append("low_query_support")

    if item.candidate_rank is not None and any(
        row.hard_negative_note
        and row.candidate_rank is not None
        and row.candidate_rank < item.candidate_rank
        for row in all_rows
    ):
        hints.append("hard_negative_ahead")

    return ordered_unique(hints)


def matched_expected_headings(
    case: SmokeCase, chunks: list[ChunkHit]
) -> list[list[str]]:
    out: list[list[str]] = []
    for expected in case.expected_heading_paths:
        if any(heading_matches(chunk.heading_path, expected) for chunk in chunks):
            out.append(expected)
    return out


def matched_anchors(chunks: list[ChunkHit]) -> list[str]:
    return ordered_unique(
        [
            anchor
            for chunk in chunks
            for anchor in chunk.matched_anchors
        ]
    )


def first_ranks_by_note(
    chunks: list[ChunkHit],
    note_paths: list[str],
) -> dict[str, int]:
    ranks: dict[str, int] = {}
    wanted = set(note_paths)
    for chunk in chunks:
        if chunk.note_path in wanted and chunk.note_path not in ranks:
            ranks[chunk.note_path] = chunk.rank
    return ranks


def chunk_ids(chunks: list[RetrievedChunk]) -> list[int]:
    return [int(chunk.chunk.id) for chunk in chunks]


def trace_to_json(trace: SmokeTrace) -> dict[str, object]:
    return {
        "case_id": trace.case_id,
        "query": trace.query,
        "predicted_zero_hit": trace.predicted_zero_hit,
        "rerank_mode": trace.rerank_mode,
        "rerank_input_top_k": trace.rerank_input_top_k,
        "selected_top_k": trace.selected_top_k,
        "parent_doc_mode": trace.parent_doc_mode,
        "expanded_queries": trace.expanded_queries,
        "candidate_chunk_ids": trace.candidate_chunk_ids,
        "rerank_chunk_ids": trace.rerank_chunk_ids,
        "coarse_rank_diagnostics": [
            {
                "chunk_id": row.chunk_id,
                "note_path": row.note_path,
                "heading_path": row.heading_path,
                "candidate_rank": row.candidate_rank,
                "cross_query_rrf_score": row.cross_query_rrf_score,
                "content_preview": row.content_preview,
                "query_ranks": [
                    {
                        "query_index": query_rank.query_index,
                        "query": query_rank.query,
                        "query_weight": query_rank.query_weight,
                        "query_hybrid_rank": query_rank.query_hybrid_rank,
                        "query_hybrid_rrf_score": (
                            query_rank.query_hybrid_rrf_score
                        ),
                        "base_cross_query_rrf_contribution": (
                            query_rank.base_cross_query_rrf_contribution
                        ),
                        "cross_query_rrf_contribution": (
                            query_rank.cross_query_rrf_contribution
                        ),
                        "vector_rank": query_rank.vector_rank,
                        "vector_distance": query_rank.vector_distance,
                        "vector_rrf_contribution": (
                            query_rank.vector_rrf_contribution
                        ),
                        "lexical_rank": query_rank.lexical_rank,
                        "lexical_score": query_rank.lexical_score,
                        "lexical_rrf_contribution": (
                            query_rank.lexical_rrf_contribution
                        ),
                    }
                    for query_rank in row.query_ranks
                ],
            }
            for row in trace.coarse_rank_diagnostics
        ],
        "rerank_movements": [
            {
                "chunk_id": movement.chunk_id,
                "note_path": movement.note_path,
                "heading_path": movement.heading_path,
                "candidate_rank": movement.candidate_rank,
                "rerank_rank": movement.rerank_rank,
                "rank_delta": movement.rank_delta,
                "rerank_score": movement.rerank_score,
            }
            for movement in trace.rerank_movements
        ],
        "final_chunks": [
            {
                "chunk_id": chunk.chunk_id,
                "note_path": chunk.note_path,
                "heading_path": chunk.heading_path,
                "rerank_score": chunk.rerank_score,
                "content": chunk.content,
            }
            for chunk in trace.final_chunks
        ],
        "rerank_tokens": trace.rerank_tokens,
        "rerank_cost_cny": str(trace.rerank_cost_cny),
        "error": trace.error,
    }


def trace_from_json(obj: dict[str, object]) -> SmokeTrace:
    return SmokeTrace(
        case_id=str(obj["case_id"]),
        query=str(obj["query"]),
        predicted_zero_hit=bool(obj["predicted_zero_hit"]),
        rerank_mode=str(obj.get("rerank_mode", "provider")),
        rerank_input_top_k=int(
            obj.get("rerank_input_top_k", QWEN3_RERANK_MAX_DOCUMENTS)
        ),
        selected_top_k=int(obj.get("selected_top_k", RERANK_TOP_K)),
        parent_doc_mode=str(obj.get("parent_doc_mode", "on")),
        expanded_queries=[str(x) for x in obj.get("expanded_queries", [])],
        candidate_chunk_ids=[
            int(x) for x in obj.get("candidate_chunk_ids", [])
        ],
        rerank_chunk_ids=[int(x) for x in obj.get("rerank_chunk_ids", [])],
        coarse_rank_diagnostics=[
            TraceCoarseRankDiagnostic(
                chunk_id=int(row["chunk_id"]),
                note_path=str(row["note_path"]),
                heading_path=[
                    str(part) for part in row.get("heading_path", [])
                ],
                candidate_rank=(
                    int(row["candidate_rank"])
                    if row.get("candidate_rank") is not None
                    else None
                ),
                cross_query_rrf_score=(
                    float(row["cross_query_rrf_score"])
                    if row.get("cross_query_rrf_score") is not None
                    else None
                ),
                content_preview=str(row.get("content_preview", "")),
                query_ranks=[
                    TraceCoarseQueryRank(
                        query_index=int(query_rank["query_index"]),
                        query=str(query_rank["query"]),
                        query_weight=float(
                            query_rank.get("query_weight", 1.0)
                        ),
                        query_hybrid_rank=(
                            int(query_rank["query_hybrid_rank"])
                            if (
                                query_rank.get("query_hybrid_rank")
                                is not None
                            )
                            else None
                        ),
                        query_hybrid_rrf_score=(
                            float(query_rank["query_hybrid_rrf_score"])
                            if (
                                query_rank.get("query_hybrid_rrf_score")
                                is not None
                            )
                            else None
                        ),
                        base_cross_query_rrf_contribution=(
                            float(
                                query_rank.get(
                                    "base_cross_query_rrf_contribution",
                                    query_rank.get(
                                        "cross_query_rrf_contribution"
                                    ),
                                )
                            )
                            if (
                                query_rank.get(
                                    "base_cross_query_rrf_contribution",
                                    query_rank.get(
                                        "cross_query_rrf_contribution"
                                    ),
                                )
                                is not None
                            )
                            else None
                        ),
                        cross_query_rrf_contribution=(
                            float(
                                query_rank[
                                    "cross_query_rrf_contribution"
                                ]
                            )
                            if (
                                query_rank.get(
                                    "cross_query_rrf_contribution"
                                )
                                is not None
                            )
                            else None
                        ),
                        vector_rank=(
                            int(query_rank["vector_rank"])
                            if query_rank.get("vector_rank") is not None
                            else None
                        ),
                        vector_distance=(
                            float(query_rank["vector_distance"])
                            if query_rank.get("vector_distance") is not None
                            else None
                        ),
                        vector_rrf_contribution=(
                            float(query_rank["vector_rrf_contribution"])
                            if (
                                query_rank.get("vector_rrf_contribution")
                                is not None
                            )
                            else None
                        ),
                        lexical_rank=(
                            int(query_rank["lexical_rank"])
                            if query_rank.get("lexical_rank") is not None
                            else None
                        ),
                        lexical_score=(
                            float(query_rank["lexical_score"])
                            if query_rank.get("lexical_score") is not None
                            else None
                        ),
                        lexical_rrf_contribution=(
                            float(query_rank["lexical_rrf_contribution"])
                            if (
                                query_rank.get("lexical_rrf_contribution")
                                is not None
                            )
                            else None
                        ),
                    )
                    for query_rank in row.get("query_ranks", [])
                    if isinstance(query_rank, dict)
                ],
            )
            for row in obj.get("coarse_rank_diagnostics", [])
            if isinstance(row, dict)
        ],
        rerank_movements=[
            TraceRerankMovement(
                chunk_id=int(movement["chunk_id"]),
                note_path=str(movement["note_path"]),
                heading_path=[
                    str(part) for part in movement.get("heading_path", [])
                ],
                candidate_rank=(
                    int(movement["candidate_rank"])
                    if movement.get("candidate_rank") is not None
                    else None
                ),
                rerank_rank=(
                    int(movement["rerank_rank"])
                    if movement.get("rerank_rank") is not None
                    else None
                ),
                rank_delta=(
                    int(movement["rank_delta"])
                    if movement.get("rank_delta") is not None
                    else None
                ),
                rerank_score=(
                    float(movement["rerank_score"])
                    if movement.get("rerank_score") is not None
                    else None
                ),
            )
            for movement in obj.get("rerank_movements", [])
            if isinstance(movement, dict)
        ],
        final_chunks=[
            TraceFinalChunk(
                chunk_id=int(chunk["chunk_id"]),
                note_path=str(chunk["note_path"]),
                heading_path=[
                    str(part) for part in chunk.get("heading_path", [])
                ],
                rerank_score=float(chunk.get("rerank_score", 0.0)),
                content=str(chunk.get("content", "")),
            )
            for chunk in obj.get("final_chunks", [])
            if isinstance(chunk, dict)
        ],
        rerank_tokens=int(obj.get("rerank_tokens", 0)),
        rerank_cost_cny=Decimal(str(obj.get("rerank_cost_cny", "0"))),
        error=str(obj.get("error", "")),
    )


def load_traces(path: Path) -> dict[str, SmokeTrace]:
    traces: dict[str, SmokeTrace] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        trace = trace_from_json(json.loads(raw))
        traces[trace.case_id] = trace
    return traces


def write_traces(path: Path, traces: list[SmokeTrace]) -> None:
    path.write_text(
        "\n".join(
            json.dumps(trace_to_json(trace), ensure_ascii=False)
            for trace in traces
        )
        + "\n",
        encoding="utf-8",
    )


def match_direct_evidence_hits(
    direct_evidence_chunk_ids: list[int],
    actual_chunk_ids: list[int],
) -> list[int]:
    expected = set(direct_evidence_chunk_ids)
    return ordered_unique_int(
        [chunk_id for chunk_id in actual_chunk_ids if chunk_id in expected]
    )


def recall_ratio(direct_evidence_chunk_ids: list[int], hits: list[int]) -> float | None:
    if not direct_evidence_chunk_ids:
        return None
    return len(set(hits)) / len(set(direct_evidence_chunk_ids))


def mrr_at_k(
    direct_evidence_chunk_ids: list[int],
    actual_chunk_ids: list[int],
) -> float | None:
    if not direct_evidence_chunk_ids:
        return None
    expected = set(direct_evidence_chunk_ids)
    for rank, chunk_id in enumerate(actual_chunk_ids, start=1):
        if chunk_id in expected:
            return 1.0 / rank
    return 0.0


def calculate_final_context_precision(chunks: list[ChunkHit]) -> float | None:
    if not chunks:
        return None
    relevant_context = sum(
        1
        for chunk in chunks
        if chunk.direct_evidence or chunk.necessary_context
    )
    return relevant_context / len(chunks)


def diagnose_failure(
    case: SmokeCase,
    predicted_zero_hit: bool,
    expected_hits: list[str],
    direct_evidence_hits: list[int],
    necessary_context_hits: list[int],
    expected_heading_hits: list[list[str]],
    evidence_anchor_hits: list[str],
    hard_negative_hits: list[str],
) -> list[str]:
    if case.expected_zero_hit:
        return [] if predicted_zero_hit else ["zero_hit_false_positive"]
    if predicted_zero_hit:
        return ["unexpected_zero_hit"]

    hints: list[str] = []
    if not expected_hits:
        hints.append("expected_note_missing")
    if case.direct_evidence_chunk_ids and not direct_evidence_hits:
        hints.append("direct_evidence_missing")
    if case.necessary_context_chunk_ids and not necessary_context_hits:
        hints.append("necessary_context_missing")
    if case.expected_heading_paths and not expected_heading_hits:
        hints.append("expected_heading_missing")
    if case.evidence_anchors and not evidence_anchor_hits:
        hints.append("evidence_anchor_missing")
    if hard_negative_hits:
        hints.append("hard_negative_intrusion")
    return hints


def score_case_trace(case: SmokeCase, trace: SmokeTrace) -> SmokeResult:
    rerank_movements = build_rerank_movements(case, trace.rerank_movements)
    coarse_rank_diagnostics = build_coarse_rank_diagnostics(
        case, trace.coarse_rank_diagnostics
    )
    if trace.predicted_zero_hit:
        candidate_hits = match_direct_evidence_hits(
            case.direct_evidence_chunk_ids, trace.candidate_chunk_ids
        )
        rerank_hits = match_direct_evidence_hits(
            case.direct_evidence_chunk_ids, trace.rerank_chunk_ids
        )
        final_context_recall = (
            recall_ratio(case.direct_evidence_chunk_ids, [])
            if not case.expected_zero_hit
            else None
        )
        passed = case.expected_zero_hit
        return SmokeResult(
            case=case,
            passed=passed,
            predicted_zero_hit=True,
            rerank_mode=trace.rerank_mode,
            rerank_input_top_k=trace.rerank_input_top_k,
            selected_top_k=trace.selected_top_k,
            parent_doc_mode=trace.parent_doc_mode,
            expanded_queries=trace.expanded_queries or [case.query],
            candidate_chunk_ids=trace.candidate_chunk_ids,
            rerank_chunk_ids=trace.rerank_chunk_ids,
            coarse_rank_diagnostics=coarse_rank_diagnostics,
            rerank_movements=rerank_movements,
            top_note_paths=[],
            top_chunks=[],
            candidate_direct_evidence_hits=candidate_hits,
            rerank_direct_evidence_hits=rerank_hits,
            expected_hits=[],
            direct_evidence_hits=[],
            necessary_context_hits=[],
            expected_heading_hits=[],
            evidence_anchor_hits=[],
            hard_negative_hits=[],
            hard_negative_ranks={},
            failure_hints=[] if passed else ["unexpected_zero_hit"],
            candidate_recall_at_50=recall_ratio(
                case.direct_evidence_chunk_ids, candidate_hits
            ),
            rerank_recall_at_10=recall_ratio(
                case.direct_evidence_chunk_ids, rerank_hits
            ),
            mrr_at_10=mrr_at_k(
                case.direct_evidence_chunk_ids, trace.rerank_chunk_ids
            ),
            final_context_recall=final_context_recall,
            final_context_precision=None,
            retrieved_chunk_count=0,
            rerank_tokens=trace.rerank_tokens,
            rerank_cost_cny=trace.rerank_cost_cny,
            error=trace.error,
        )

    candidate_hits = match_direct_evidence_hits(
        case.direct_evidence_chunk_ids, trace.candidate_chunk_ids
    )
    rerank_hits = match_direct_evidence_hits(
        case.direct_evidence_chunk_ids, trace.rerank_chunk_ids
    )
    top_chunks = build_chunk_hits(case, trace.final_chunks)
    top_notes = ordered_unique([chunk.note_path for chunk in trace.final_chunks])
    expected_hits = [p for p in top_notes if p in case.expected_note_paths]
    direct_evidence_chunk_id_set = set(case.direct_evidence_chunk_ids)
    direct_evidence_hits = ordered_unique_int(
        [
            chunk.chunk_id
            for chunk in top_chunks
            if chunk.chunk_id in direct_evidence_chunk_id_set
        ]
    )
    necessary_context_hits = ordered_unique_int(
        [
            chunk.chunk_id
            for chunk in top_chunks
            if chunk.chunk_id in set(case.necessary_context_chunk_ids)
        ]
    )
    expected_heading_hits = matched_expected_headings(case, top_chunks)
    evidence_anchor_hits = matched_anchors(top_chunks)
    hard_negative_hits = [
        p for p in top_notes if p in case.hard_negative_note_paths
    ]
    hard_negative_ranks = first_ranks_by_note(
        top_chunks, case.hard_negative_note_paths
    )
    # Keep the historical note-level gate stable; chunk/heading/anchor fields are
    # diagnostics for deciding which retrieval stage to tune next.
    passed = (
        not case.expected_zero_hit
        and bool(expected_hits)
        and not hard_negative_hits
    )
    failure_hints = diagnose_failure(
        case,
        predicted_zero_hit=False,
        expected_hits=expected_hits,
        direct_evidence_hits=direct_evidence_hits,
        necessary_context_hits=necessary_context_hits,
        expected_heading_hits=expected_heading_hits,
        evidence_anchor_hits=evidence_anchor_hits,
        hard_negative_hits=hard_negative_hits,
    )
    return SmokeResult(
        case=case,
        passed=passed,
        predicted_zero_hit=False,
        rerank_mode=trace.rerank_mode,
        rerank_input_top_k=trace.rerank_input_top_k,
        selected_top_k=trace.selected_top_k,
        parent_doc_mode=trace.parent_doc_mode,
        expanded_queries=trace.expanded_queries,
        candidate_chunk_ids=trace.candidate_chunk_ids,
        rerank_chunk_ids=trace.rerank_chunk_ids,
        coarse_rank_diagnostics=coarse_rank_diagnostics,
        rerank_movements=rerank_movements,
        top_note_paths=top_notes,
        top_chunks=top_chunks,
        candidate_direct_evidence_hits=candidate_hits,
        rerank_direct_evidence_hits=rerank_hits,
        expected_hits=expected_hits,
        direct_evidence_hits=direct_evidence_hits,
        necessary_context_hits=necessary_context_hits,
        expected_heading_hits=expected_heading_hits,
        evidence_anchor_hits=evidence_anchor_hits,
        hard_negative_hits=hard_negative_hits,
        hard_negative_ranks=hard_negative_ranks,
        failure_hints=failure_hints,
        candidate_recall_at_50=recall_ratio(
            case.direct_evidence_chunk_ids, candidate_hits
        ),
        rerank_recall_at_10=recall_ratio(
            case.direct_evidence_chunk_ids, rerank_hits
        ),
        mrr_at_10=mrr_at_k(case.direct_evidence_chunk_ids, trace.rerank_chunk_ids),
        final_context_recall=recall_ratio(
            case.direct_evidence_chunk_ids, direct_evidence_hits
        ),
        final_context_precision=calculate_final_context_precision(top_chunks),
        retrieved_chunk_count=len(trace.final_chunks),
        rerank_tokens=trace.rerank_tokens,
        rerank_cost_cny=trace.rerank_cost_cny,
        error=trace.error,
    )


async def evaluate_case(
    case: SmokeCase,
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    rerank_mode: str,
    rerank_input_top_k: int,
    selected_top_k: int,
    parent_doc_mode: str,
) -> tuple[SmokeResult, SmokeTrace]:
    try:
        async with sessionmaker() as session:
            trace = await run_pipeline_with_cost(
                session,
                case.query,
                rerank_mode=rerank_mode,
                rerank_input_top_k=rerank_input_top_k,
                selected_top_k=selected_top_k,
                parent_doc_mode=parent_doc_mode,
                diagnostic_chunk_ids=ordered_unique_int(
                    [
                        *case.direct_evidence_chunk_ids,
                        *case.necessary_context_chunk_ids,
                    ]
                ),
            )
    except NoChunksForQueryError as exc:
        smoke_trace = SmokeTrace(
            case_id=case.id,
            query=case.query,
            predicted_zero_hit=True,
            rerank_mode=rerank_mode,
            rerank_input_top_k=rerank_input_top_k,
            selected_top_k=selected_top_k,
            parent_doc_mode=parent_doc_mode,
            expanded_queries=[case.query],
            candidate_chunk_ids=[],
            rerank_chunk_ids=[],
            coarse_rank_diagnostics=[],
            rerank_movements=[],
            final_chunks=[],
            rerank_tokens=0,
            rerank_cost_cny=Decimal("0"),
            error=exc.detail,
        )
        return score_case_trace(case, smoke_trace), smoke_trace

    smoke_trace = SmokeTrace(
        case_id=case.id,
        query=case.query,
        predicted_zero_hit=trace.predicted_zero_hit,
        rerank_mode=trace.rerank_mode,
        rerank_input_top_k=trace.rerank_input_top_k,
        selected_top_k=trace.selected_top_k,
        parent_doc_mode=trace.parent_doc_mode,
        expanded_queries=trace.result.expanded_queries,
        candidate_chunk_ids=chunk_ids(trace.candidate_chunks),
        rerank_chunk_ids=chunk_ids(trace.reranked_chunks),
        coarse_rank_diagnostics=trace.coarse_rank_diagnostics,
        rerank_movements=trace.rerank_movements,
        final_chunks=[
            trace_final_chunk(chunk) for chunk in trace.result.retrieved_chunks
        ],
        rerank_tokens=trace.rerank_tokens,
        rerank_cost_cny=trace.rerank_cost_cny,
        error=trace.error,
    )
    return score_case_trace(case, smoke_trace), smoke_trace


async def summarize_llm_costs(
    sessionmaker: async_sessionmaker[AsyncSession],
    started_at: datetime,
) -> tuple[int, Decimal]:
    async with sessionmaker() as session:
        stmt = sa.select(
            sa.func.count(LlmCall.id),
            sa.func.coalesce(sa.func.sum(LlmCall.cost_cny), 0),
        ).where(LlmCall.created_at >= started_at)
        row = (await session.execute(stmt)).one()
        return int(row[0]), Decimal(str(row[1]))


def ratio_text(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "-"
    return f"{numerator}/{denominator} ({numerator / denominator:.2%})"


def percent_text(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2%}"


def metric_avg(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return sum(present) / len(present)


def md_cell(value: object) -> str:
    text = str(value)
    return text.replace("|", "\\|").replace("\n", "<br>")


def path_text(path: list[str]) -> str:
    return " > ".join(path) if path else "<root>"


def list_text(items: list[str]) -> str:
    return "<br>".join(md_cell(item) for item in items) if items else "-"


def heading_list_text(items: list[list[str]]) -> str:
    return list_text([path_text(item) for item in items])


def int_list_text(items: list[int]) -> str:
    return ", ".join(str(item) for item in items) if items else "-"


def rank_map_text(rank_map: dict[str, int]) -> str:
    if not rank_map:
        return "-"
    return "<br>".join(
        f"{md_cell(path)} @ #{rank}" for path, rank in rank_map.items()
    )


def labels_text(chunk: ChunkHit) -> str:
    labels: list[str] = []
    if chunk.direct_evidence:
        labels.append("direct-evidence")
    if chunk.necessary_context:
        labels.append("necessary-context")
    if chunk.expected_note:
        labels.append("expected-note")
    if chunk.expected_heading:
        labels.append("expected-heading")
    if chunk.hard_negative_note:
        labels.append("hard-negative")
    return ", ".join(labels) if labels else "-"


def movement_labels_text(chunk: RerankMovement) -> str:
    labels: list[str] = []
    if chunk.direct_evidence:
        labels.append("direct-evidence")
    if chunk.necessary_context:
        labels.append("necessary-context")
    if chunk.expected_note:
        labels.append("expected-note")
    if chunk.hard_negative_note:
        labels.append("hard-negative")
    return ", ".join(labels) if labels else "-"


def rank_text(rank: int | None) -> str:
    return f"#{rank}" if rank is not None else "-"


def rank_delta_text(delta: int | None) -> str:
    if delta is None:
        return "-"
    return f"+{delta}" if delta > 0 else str(delta)


def score_text(score: float | None) -> str:
    return f"{score:.4f}" if score is not None else "-"


def coarse_labels_text(chunk: CoarseRankDiagnostic) -> str:
    labels: list[str] = []
    if chunk.direct_evidence:
        labels.append("direct-evidence")
    if chunk.necessary_context:
        labels.append("necessary-context")
    if chunk.expected_note:
        labels.append("expected-note")
    if chunk.hard_negative_note:
        labels.append("hard-negative")
    return ", ".join(labels) if labels else "-"


def coarse_query_rank_text(rank: CoarseQueryRank | None) -> str:
    if rank is None or rank.query_hybrid_rank is None:
        return "-"
    cross = score_text(rank.cross_query_rrf_contribution)
    score = score_text(rank.query_hybrid_rrf_score)
    return (
        f"q{rank.query_index}(w{rank.query_weight:g}) "
        f"#{rank.query_hybrid_rank} "
        f"rrf={score} cross={cross}"
    )


def coarse_vector_rank_text(rank: CoarseQueryRank | None) -> str:
    if rank is None or rank.vector_rank is None:
        return "-"
    return (
        f"q{rank.query_index} #{rank.vector_rank} "
        f"dist={score_text(rank.vector_distance)}"
    )


def coarse_lexical_rank_text(rank: CoarseQueryRank | None) -> str:
    if rank is None or rank.lexical_rank is None:
        return "-"
    return (
        f"q{rank.query_index} #{rank.lexical_rank} "
        f"score={score_text(rank.lexical_score)}"
    )


def per_query_route_ranks_text(row: CoarseRankDiagnostic) -> str:
    return list_text(
        [
            f"q{rank.query_index}(w{rank.query_weight:g}): "
            f"h{rank_text(rank.query_hybrid_rank)} "
            f"v{rank_text(rank.vector_rank)} "
            f"l{rank_text(rank.lexical_rank)}"
            for rank in row.query_ranks
        ]
    )


def best_query_rank_row(
    row: CoarseRankDiagnostic,
) -> CoarseQueryRank | None:
    candidates = [
        rank for rank in row.query_ranks if rank.query_hybrid_rank is not None
    ]
    return min(candidates, key=lambda rank: rank.query_hybrid_rank, default=None)


def best_vector_rank_row(
    row: CoarseRankDiagnostic,
) -> CoarseQueryRank | None:
    candidates = [rank for rank in row.query_ranks if rank.vector_rank is not None]
    return min(candidates, key=lambda rank: rank.vector_rank, default=None)


def best_lexical_rank_row(
    row: CoarseRankDiagnostic,
) -> CoarseQueryRank | None:
    candidates = [
        rank for rank in row.query_ranks if rank.lexical_rank is not None
    ]
    return min(candidates, key=lambda rank: rank.lexical_rank, default=None)


def query_support_count(row: CoarseRankDiagnostic) -> int:
    return sum(
        1
        for rank in row.query_ranks
        if rank.cross_query_rrf_contribution is not None
    )


def select_coarse_rank_rows(
    rows: list[CoarseRankDiagnostic],
) -> list[CoarseRankDiagnostic]:
    by_rank = {
        row.candidate_rank: row
        for row in rows
        if row.candidate_rank is not None
    }
    selected_ids: set[int] = set()
    for row in rows:
        if row.candidate_rank is not None and row.candidate_rank <= 10:
            selected_ids.add(row.chunk_id)
        if row.direct_evidence or row.necessary_context or row.hard_negative_note:
            selected_ids.add(row.chunk_id)
        if row.direct_evidence or row.necessary_context:
            rank = row.candidate_rank
            if rank is None:
                continue
            for neighbor_rank in range(
                max(1, rank - COARSE_RANK_NEIGHBOR_WINDOW),
                rank + COARSE_RANK_NEIGHBOR_WINDOW + 1,
            ):
                neighbor = by_rank.get(neighbor_rank)
                if neighbor is not None:
                    selected_ids.add(neighbor.chunk_id)

    def sort_key(row: CoarseRankDiagnostic) -> tuple[int, int]:
        rank = row.candidate_rank
        return (rank if rank is not None else 10**9, row.chunk_id)

    return sorted(
        [row for row in rows if row.chunk_id in selected_ids],
        key=sort_key,
    )


def coarse_reason_counts(
    results: list[SmokeResult],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        for row in result.coarse_rank_diagnostics:
            if not row.direct_evidence and not row.necessary_context:
                continue
            for reason in row.reason_hints:
                counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def reason_counts_text(counts: dict[str, int]) -> str:
    if not counts:
        return "-"
    return ", ".join(f"{key}={value}" for key, value in counts.items())


def query_rank_for(
    row: CoarseRankDiagnostic,
    query_index: int,
) -> CoarseQueryRank | None:
    for rank in row.query_ranks:
        if rank.query_index == query_index:
            return rank
    return None


def query_indices(rows: list[CoarseRankDiagnostic]) -> list[int]:
    indices = {
        query_rank.query_index
        for row in rows
        for query_rank in row.query_ranks
    }
    return sorted(indices)


def labeled_chunk_text(
    row: CoarseRankDiagnostic,
    query_rank: CoarseQueryRank,
) -> str:
    label = coarse_labels_text(row)
    return (
        f"#{row.chunk_id} "
        f"h{rank_text(query_rank.query_hybrid_rank)} "
        f"v{rank_text(query_rank.vector_rank)} "
        f"l{rank_text(query_rank.lexical_rank)} "
        f"({label})"
    )


def build_query_vote_diagnostics(
    rows: list[CoarseRankDiagnostic],
) -> list[QueryVoteDiagnostic]:
    diagnostics: list[QueryVoteDiagnostic] = []
    for query_index in query_indices(rows):
        query = next(
            (
                query_rank.query
                for row in rows
                for query_rank in row.query_ranks
                if query_rank.query_index == query_index
            ),
            "",
        )
        query_weight = next(
            (
                query_rank.query_weight
                for row in rows
                for query_rank in row.query_ranks
                if query_rank.query_index == query_index
            ),
            1.0,
        )
        contributed_rows: list[
            tuple[CoarseRankDiagnostic, CoarseQueryRank]
        ] = []
        relevant_rows: list[
            tuple[CoarseRankDiagnostic, CoarseQueryRank]
        ] = []
        direct_rows: list[tuple[CoarseRankDiagnostic, CoarseQueryRank]] = []
        necessary_rows: list[tuple[CoarseRankDiagnostic, CoarseQueryRank]] = []
        hard_negative_rows: list[
            tuple[CoarseRankDiagnostic, CoarseQueryRank]
        ] = []
        labeled_rows: list[
            tuple[CoarseRankDiagnostic, CoarseQueryRank]
        ] = []

        for row in rows:
            query_rank = query_rank_for(row, query_index)
            if query_rank is None:
                continue
            if query_rank.cross_query_rrf_contribution is not None:
                contributed_rows.append((row, query_rank))
            is_relevant = row.direct_evidence or row.necessary_context
            is_labeled = is_relevant or row.hard_negative_note
            query_hit = query_rank.query_hybrid_rank is not None
            if is_relevant and query_hit:
                relevant_rows.append((row, query_rank))
            if row.direct_evidence and query_hit:
                direct_rows.append((row, query_rank))
            if row.necessary_context and query_hit:
                necessary_rows.append((row, query_rank))
            if row.hard_negative_note and query_hit:
                hard_negative_rows.append((row, query_rank))
            if is_labeled and query_hit:
                labeled_rows.append((row, query_rank))

        relevant_chunk_ids = ordered_unique_int(
            [row.chunk_id for row, _ in relevant_rows]
        )
        direct_chunk_ids = ordered_unique_int(
            [row.chunk_id for row, _ in direct_rows]
        )
        necessary_chunk_ids = ordered_unique_int(
            [row.chunk_id for row, _ in necessary_rows]
        )
        hard_negative_chunk_ids = ordered_unique_int(
            [row.chunk_id for row, _ in hard_negative_rows]
        )
        best_relevant_rank = best_rank(
            [query_rank.query_hybrid_rank for _, query_rank in relevant_rows]
        )
        best_direct_rank = best_rank(
            [query_rank.query_hybrid_rank for _, query_rank in direct_rows]
        )
        best_hard_negative_rank = best_rank(
            [
                query_rank.query_hybrid_rank
                for _, query_rank in hard_negative_rows
            ]
        )
        sorted_labeled_rows = sorted(
            labeled_rows,
            key=lambda pair: (
                pair[1].query_hybrid_rank
                if pair[1].query_hybrid_rank is not None
                else 10**9,
                pair[0].chunk_id,
            ),
        )

        hints: list[str] = []
        if query_index > 0 and hard_negative_chunk_ids and not relevant_chunk_ids:
            hints.append("rewrite_hard_negative_only")
        if query_index > 0 and contributed_rows and not relevant_chunk_ids:
            hints.append("rewrite_no_relevant_support")
        if best_relevant_rank is not None and best_relevant_rank > HYBRID_TOP_K_PER_QUERY:
            hints.append("relevant_below_query_top50")
        if (
            best_hard_negative_rank is not None
            and best_relevant_rank is not None
            and best_hard_negative_rank < best_relevant_rank
        ):
            hints.append("hard_negative_beats_relevant")
        if query_index == 0 and not relevant_chunk_ids:
            hints.append("original_query_no_relevant_support")
        if query_index > 0 and relevant_chunk_ids:
            hints.append("rewrite_has_relevant_support")

        diagnostics.append(
            QueryVoteDiagnostic(
                query_index=query_index,
                query=query,
                role="original" if query_index == 0 else "rewrite",
                query_weight=query_weight,
                contributed_candidate_count=len(contributed_rows),
                relevant_chunk_ids=relevant_chunk_ids,
                direct_evidence_chunk_ids=direct_chunk_ids,
                necessary_context_chunk_ids=necessary_chunk_ids,
                hard_negative_chunk_ids=hard_negative_chunk_ids,
                best_relevant_rank=best_relevant_rank,
                best_direct_evidence_rank=best_direct_rank,
                best_hard_negative_rank=best_hard_negative_rank,
                top_labeled_chunks=[
                    labeled_chunk_text(row, query_rank)
                    for row, query_rank in sorted_labeled_rows[:5]
                ],
                hints=ordered_unique(hints),
            )
        )
    return diagnostics


def query_vote_reason_counts(results: list[SmokeResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        for diagnostic in build_query_vote_diagnostics(
            result.coarse_rank_diagnostics
        ):
            for hint in diagnostic.hints:
                counts[hint] = counts.get(hint, 0) + 1
    return dict(sorted(counts.items()))


def simulated_cross_query_score(
    row: CoarseRankDiagnostic,
    *,
    original_query_weight: float,
) -> float:
    total = 0.0
    for query_rank in row.query_ranks:
        contribution = query_rank.base_cross_query_rrf_contribution
        if contribution is None:
            continue
        weight = original_query_weight if query_rank.query_index == 0 else 1.0
        total += weight * contribution
    return total


def rank_by_score(scores: dict[int, float]) -> dict[int, int]:
    ranked_chunk_ids = [
        chunk_id
        for chunk_id, score in sorted(
            scores.items(), key=lambda item: (-item[1], item[0])
        )
        if score > 0
    ]
    return {
        chunk_id: rank
        for rank, chunk_id in enumerate(ranked_chunk_ids, start=1)
    }


def build_original_query_weight_simulation(
    rows: list[CoarseRankDiagnostic],
    *,
    original_query_weight: float = ORIGINAL_QUERY_WEIGHT_SIMULATION,
) -> list[OriginalQueryWeightSimulation]:
    original_scores = {
        row.chunk_id: simulated_cross_query_score(
            row,
            original_query_weight=1.0,
        )
        for row in rows
    }
    weighted_scores = {
        row.chunk_id: simulated_cross_query_score(
            row,
            original_query_weight=original_query_weight,
        )
        for row in rows
    }
    original_ranks = rank_by_score(original_scores)
    weighted_ranks = rank_by_score(weighted_scores)

    simulations: list[OriginalQueryWeightSimulation] = []
    for row in rows:
        original_rank = original_ranks.get(row.chunk_id)
        weighted_rank = weighted_ranks.get(row.chunk_id)
        rank_delta = (
            weighted_rank - original_rank
            if original_rank is not None and weighted_rank is not None
            else None
        )
        hints: list[str] = []
        if rank_delta is not None and rank_delta < 0:
            hints.append("moves_up")
        elif rank_delta is not None and rank_delta > 0:
            hints.append("moves_down")
        is_relevant = row.direct_evidence or row.necessary_context
        if is_relevant and rank_delta is not None and rank_delta < 0:
            hints.append("relevant_moves_up")
        elif is_relevant and rank_delta is not None and rank_delta > 0:
            hints.append("relevant_moves_down")
        if row.hard_negative_note and rank_delta is not None and rank_delta < 0:
            hints.append("hard_negative_moves_up")
        elif row.hard_negative_note and rank_delta is not None and rank_delta > 0:
            hints.append("hard_negative_moves_down")
        if is_relevant and original_rank is not None:
            if original_rank > LOW_COARSE_RANK_THRESHOLD and (
                weighted_rank is not None
                and weighted_rank <= LOW_COARSE_RANK_THRESHOLD
            ):
                hints.append("relevant_enters_top20")
        if row.hard_negative_note and original_rank is not None:
            if original_rank <= LOW_COARSE_RANK_THRESHOLD and (
                weighted_rank is None
                or weighted_rank > LOW_COARSE_RANK_THRESHOLD
            ):
                hints.append("hard_negative_leaves_top20")
        simulations.append(
            OriginalQueryWeightSimulation(
                chunk_id=row.chunk_id,
                note_path=row.note_path,
                heading_path=row.heading_path,
                original_rank=original_rank,
                weighted_rank=weighted_rank,
                rank_delta=rank_delta,
                original_score=original_scores.get(row.chunk_id, 0.0),
                weighted_score=weighted_scores.get(row.chunk_id, 0.0),
                expected_note=row.expected_note,
                hard_negative_note=row.hard_negative_note,
                direct_evidence=row.direct_evidence,
                necessary_context=row.necessary_context,
                hints=ordered_unique(hints),
            )
        )
    return simulations


def select_original_query_weight_rows(
    simulations: list[OriginalQueryWeightSimulation],
) -> list[OriginalQueryWeightSimulation]:
    selected = [
        row
        for row in simulations
        if row.direct_evidence
        or row.necessary_context
        or (
            row.hard_negative_note
            and (
                (row.original_rank is not None and row.original_rank <= 20)
                or (row.weighted_rank is not None and row.weighted_rank <= 20)
            )
        )
    ]

    def sort_key(row: OriginalQueryWeightSimulation) -> tuple[int, int, int]:
        label_group = 0 if row.direct_evidence or row.necessary_context else 1
        original_rank = (
            row.original_rank if row.original_rank is not None else 10**9
        )
        return (label_group, original_rank, row.chunk_id)

    return sorted(selected, key=sort_key)


def original_weight_simulation_counts(
    results: list[SmokeResult],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        simulations = build_original_query_weight_simulation(
            result.coarse_rank_diagnostics
        )
        for row in simulations:
            if (
                not row.direct_evidence
                and not row.necessary_context
                and not row.hard_negative_note
            ):
                continue
            for hint in row.hints:
                counts[hint] = counts.get(hint, 0) + 1
    return dict(sorted(counts.items()))


def original_weight_labels_text(
    row: OriginalQueryWeightSimulation,
) -> str:
    labels: list[str] = []
    if row.direct_evidence:
        labels.append("direct-evidence")
    if row.necessary_context:
        labels.append("necessary-context")
    if row.expected_note:
        labels.append("expected-note")
    if row.hard_negative_note:
        labels.append("hard-negative")
    return ", ".join(labels) if labels else "-"


def select_movement_rows(
    movements: list[RerankMovement],
    *,
    selected_top_k: int,
) -> list[RerankMovement]:
    selected = [
        movement
        for movement in movements
        if movement.direct_evidence
        or movement.hard_negative_note
        or (
            movement.rerank_rank is not None
            and movement.rerank_rank <= selected_top_k
        )
    ]

    def priority(movement: RerankMovement) -> tuple[int, int, int]:
        if (
            movement.rerank_rank is not None
            and movement.rerank_rank <= selected_top_k
        ):
            group = 0
        elif movement.direct_evidence:
            group = 1
        elif movement.hard_negative_note:
            group = 2
        else:
            group = 3
        return (
            group,
            movement.rerank_rank
            if movement.rerank_rank is not None
            else 10**9,
            movement.candidate_rank
            if movement.candidate_rank is not None
            else 10**9,
        )

    return sorted(selected, key=priority)


def render_report(
    results: list[SmokeResult],
    llm_calls: int,
    llm_cost: Decimal,
    *,
    score_trace_path: Path | None = None,
    trace_output_path: Path | None = None,
) -> str:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    rerank_modes = ordered_unique([r.rerank_mode for r in results])
    rerank_input_top_ks = ordered_unique_int(
        [r.rerank_input_top_k for r in results]
    )
    selected_top_ks = ordered_unique_int([r.selected_top_k for r in results])
    parent_doc_modes = ordered_unique([r.parent_doc_mode for r in results])
    expected_zero = [r for r in results if r.case.expected_zero_hit]
    zero_passed = sum(1 for r in expected_zero if r.passed)
    rerank_tokens = sum(r.rerank_tokens for r in results)
    rerank_cost = sum((r.rerank_cost_cny for r in results), Decimal("0"))
    total_cost = llm_cost + rerank_cost
    expected_notes_total = sum(len(r.case.expected_note_paths) for r in results)
    expected_notes_hit_total = sum(len(r.expected_hits) for r in results)
    direct_evidence_total = sum(
        len(r.case.direct_evidence_chunk_ids) for r in results
    )
    direct_evidence_hit_total = sum(len(r.direct_evidence_hits) for r in results)
    necessary_context_total = sum(
        len(r.case.necessary_context_chunk_ids) for r in results
    )
    necessary_context_hit_total = sum(
        len(r.necessary_context_hits) for r in results
    )
    expected_headings_total = sum(
        len(r.case.expected_heading_paths) for r in results
    )
    expected_headings_hit_total = sum(
        len(r.expected_heading_hits) for r in results
    )
    evidence_anchors_total = sum(len(r.case.evidence_anchors) for r in results)
    evidence_anchors_hit_total = sum(len(r.evidence_anchor_hits) for r in results)
    hard_negative_cases = sum(
        1 for r in results if r.hard_negative_hits
    )
    candidate_recall = metric_avg(
        [r.candidate_recall_at_50 for r in results]
    )
    rerank_recall = metric_avg([r.rerank_recall_at_10 for r in results])
    mean_mrr = metric_avg([r.mrr_at_10 for r in results])
    final_context_recall = metric_avg(
        [r.final_context_recall for r in results]
    )
    final_context_precision = metric_avg(
        [
            r.final_context_precision
            for r in results
            if not r.case.expected_zero_hit
        ]
    )
    coarse_counts = coarse_reason_counts(results)
    query_vote_counts = query_vote_reason_counts(results)
    original_weight_counts = original_weight_simulation_counts(results)

    lines: list[str] = []
    lines.append("# Hybrid Search Note/Chunk Smoke Report\n")
    lines.append(f"- generated_at: {datetime.now(UTC).isoformat()}")
    lines.append(f"- database_url: `{settings.database_url}`")
    if score_trace_path is not None:
        lines.append("- score_mode: offline_trace")
        lines.append(f"- score_trace: `{score_trace_path}`")
        lines.append(
            "- cost_note: cost fields are carried from the trace run; "
            "this rescore did not call rewrite/rerank/LLM services"
        )
    else:
        lines.append("- score_mode: live_pipeline")
    if trace_output_path is not None:
        lines.append(f"- trace_output: `{trace_output_path}`")
    lines.append(f"- cases: {total}")
    lines.append(f"- passed: {passed}/{total}")
    lines.append(f"- rerank_mode: {', '.join(rerank_modes)}")
    lines.append(f"- rerank_input_top_k: {int_list_text(rerank_input_top_ks)}")
    lines.append(f"- selected_top_k: {int_list_text(selected_top_ks)}")
    lines.append(f"- parent_doc_mode: {', '.join(parent_doc_modes)}")
    lines.append(f"- rerank_diagnostic_top_k: {RERANK_DIAGNOSTIC_TOP_K}")
    lines.append(f"- coarse_rank_diagnostic_top_k: {COARSE_RANK_DIAGNOSTIC_TOP_K}")
    lines.append(
        "- coarse_rank_note: trace records top50 hybrid candidates plus labeled "
        "direct/necessary chunks; report shows top10, labeled chunks, "
        "hard-negatives, and labeled rank neighbors"
    )
    lines.append(
        f"- coarse_rank_reason_counts: {reason_counts_text(coarse_counts)}"
    )
    lines.append(
        f"- query_vote_reason_counts: {reason_counts_text(query_vote_counts)}"
    )
    lines.append(
        "- query_vote_note: per expanded query, report shows whether that query "
        "retrieves labeled relevant chunks or mostly contributes unrelated / "
        "hard-negative evidence into cross-query RRF"
    )
    lines.append(
        f"- original_query_weight_simulation: q0_weight="
        f"{ORIGINAL_QUERY_WEIGHT_SIMULATION:g}, labeled_diagnostics_only"
    )
    lines.append(
        "- original_query_weight_simulation_note: ranks are recomputed from "
        "unweighted base contributions, only over trace diagnostic rows(top50 "
        "hybrid candidates plus labeled watched chunks), so use this as "
        "direction-finding rather than final metrics"
    )
    lines.append(
        "- original_query_weight_simulation_counts: "
        f"{reason_counts_text(original_weight_counts)}"
    )
    lines.append(
        "- pass_rule: non-zero cases need at least one expected note and no "
        "hard-negative note; chunk/heading/anchor fields are diagnostics"
    )
    lines.append(
        "- metric_average_rule: candidate/rerank/final recall and mrr are "
        "macro averages over cases with direct_evidence_chunk_ids; expected "
        "zero-hit cases are excluded, unexpected zero-hit cases count final "
        "context recall as 0"
    )
    lines.append(
        "- rerank_mode_note: selected recall/mrr use provider rerank topK in "
        "`provider` mode and hybrid RRF topK in `none` mode"
    )
    lines.append(f"- candidate_recall@50: {percent_text(candidate_recall)}")
    lines.append(f"- selected_recall@K: {percent_text(rerank_recall)}")
    lines.append(f"- mrr@K: {percent_text(mean_mrr)}")
    lines.append(f"- final_context_recall: {percent_text(final_context_recall)}")
    lines.append(
        f"- final_context_precision: {percent_text(final_context_precision)}"
    )
    lines.append(
        "- final_context_precision_rule: "
        "(direct_evidence_chunk_ids + necessary_context_chunk_ids) / final chunks"
    )
    lines.append(f"- zero_hit_passed: {zero_passed}/{len(expected_zero)}")
    lines.append(
        f"- zero_hit_precision: {ratio_text(zero_passed, len(expected_zero))}"
    )
    lines.append("- unsafe_boundary_rate: N/A (manual boundary labels not present)")
    lines.append(
        f"- expected_note_micro_recall: "
        f"{ratio_text(expected_notes_hit_total, expected_notes_total)}"
    )
    lines.append(
        f"- direct_evidence_coverage: "
        f"{ratio_text(direct_evidence_hit_total, direct_evidence_total)}"
    )
    lines.append(
        f"- necessary_context_coverage: "
        f"{ratio_text(necessary_context_hit_total, necessary_context_total)}"
    )
    lines.append(
        f"- expected_heading_coverage: "
        f"{ratio_text(expected_headings_hit_total, expected_headings_total)}"
    )
    lines.append(
        f"- evidence_anchor_coverage: "
        f"{ratio_text(evidence_anchors_hit_total, evidence_anchors_total)}"
    )
    lines.append(f"- hard_negative_intrusion_cases: {hard_negative_cases}/{total}")
    lines.append(f"- llm_calls_since_start: {llm_calls}")
    lines.append(f"- llm_cost_cny: {llm_cost:.6f}")
    lines.append(f"- rerank_tokens: {rerank_tokens}")
    lines.append(f"- rerank_cost_cny: {rerank_cost:.6f}")
    lines.append(f"- observed_cost_cny: {total_cost:.6f}")
    lines.append("\n## Cases\n")
    lines.append(
        "| ID | Result | candidate@50 | selected@K | mrr@K | final recall | final precision | Zero-hit | Chunks | Failure hints |"
    )
    lines.append(
        "|----|--------|--------------|-----------|--------|--------------|-----------------|----------|--------|---------------|"
    )
    for r in results:
        result = "PASS" if r.passed else "FAIL"
        zero = "yes" if r.predicted_zero_hit else "no"
        hints = ", ".join(r.failure_hints) if r.failure_hints else "-"
        lines.append(
            f"| {r.case.id} | {result} | "
            f"{percent_text(r.candidate_recall_at_50)} | "
            f"{percent_text(r.rerank_recall_at_10)} | "
            f"{percent_text(r.mrr_at_10)} | "
            f"{percent_text(r.final_context_recall)} | "
            f"{percent_text(r.final_context_precision)} | "
            f"{zero} | {r.retrieved_chunk_count} | {hints} |"
        )
    lines.append("\n## Chunk Details\n")
    for r in results:
        result = "PASS" if r.passed else "FAIL"
        lines.append(f"### {r.case.id} {result}\n")
        lines.append(f"- query: {md_cell(r.case.query)}")
        lines.append(f"- notes: {md_cell(r.case.notes) if r.case.notes else '-'}")
        lines.append(f"- expanded_queries: {list_text(r.expanded_queries)}")
        lines.append(f"- failure_hints: {list_text(r.failure_hints)}")
        lines.append(
            f"- metrics: candidate_recall@50={percent_text(r.candidate_recall_at_50)}, "
            f"selected_recall@K={percent_text(r.rerank_recall_at_10)}, "
            f"mrr@K={percent_text(r.mrr_at_10)}, "
            f"final_context_recall={percent_text(r.final_context_recall)}, "
            f"final_context_precision={percent_text(r.final_context_precision)}"
        )
        lines.append(
            f"- candidate_direct_evidence_hits: "
            f"{int_list_text(r.candidate_direct_evidence_hits)}"
        )
        lines.append(
            f"- rerank_direct_evidence_hits: "
            f"{int_list_text(r.rerank_direct_evidence_hits)}"
        )
        lines.append(f"- expected_note_hits: {list_text(r.expected_hits)}")
        missing_expected_notes = [
            path for path in r.case.expected_note_paths if path not in r.expected_hits
        ]
        lines.append(f"- missing_expected_notes: {list_text(missing_expected_notes)}")
        lines.append(
            f"- direct_evidence_hits: {int_list_text(r.direct_evidence_hits)}"
        )
        missing_direct_evidence = [
            chunk_id
            for chunk_id in r.case.direct_evidence_chunk_ids
            if chunk_id not in r.direct_evidence_hits
        ]
        lines.append(
            f"- missing_direct_evidence: {int_list_text(missing_direct_evidence)}"
        )
        lines.append(
            f"- necessary_context_hits: {int_list_text(r.necessary_context_hits)}"
        )
        missing_necessary_context = [
            chunk_id
            for chunk_id in r.case.necessary_context_chunk_ids
            if chunk_id not in r.necessary_context_hits
        ]
        lines.append(
            f"- missing_necessary_context: "
            f"{int_list_text(missing_necessary_context)}"
        )
        lines.append(
            f"- expected_heading_hits: {heading_list_text(r.expected_heading_hits)}"
        )
        missing_headings = [
            path
            for path in r.case.expected_heading_paths
            if path not in r.expected_heading_hits
        ]
        lines.append(
            f"- missing_heading_paths: {heading_list_text(missing_headings)}"
        )
        missing_anchors = [
            anchor
            for anchor in r.case.evidence_anchors
            if anchor not in r.evidence_anchor_hits
        ]
        lines.append(
            f"- evidence_anchor_hits: {list_text(r.evidence_anchor_hits)}"
        )
        lines.append(f"- missing_anchors: {list_text(missing_anchors)}")
        lines.append(
            f"- hard_negative_ranks: {rank_map_text(r.hard_negative_ranks)}"
        )
        labeled_coarse_reasons = {
            row.chunk_id: row.reason_hints
            for row in r.coarse_rank_diagnostics
            if (row.direct_evidence or row.necessary_context)
            and row.reason_hints
        }
        lines.append(
            "- coarse_rank_reason_hints: "
            + (
                "; ".join(
                    f"#{chunk_id}: {', '.join(reasons)}"
                    for chunk_id, reasons in labeled_coarse_reasons.items()
                )
                if labeled_coarse_reasons
                else "-"
            )
        )
        if r.error:
            lines.append(f"- error: {md_cell(r.error)}")
        coarse_rows = select_coarse_rank_rows(r.coarse_rank_diagnostics)
        if coarse_rows:
            lines.append("")
            lines.append(
                "| Chunk | Hybrid rank | Cross RRF | Best query rank | Per-query ranks | Best vector | Best lexical | Query support | Labels | Reason hints | Note | Heading | Preview |"
            )
            lines.append(
                "|-------|-------------|-----------|-----------------|-----------------|-------------|--------------|---------------|--------|--------------|------|---------|---------|"
            )
            for row in coarse_rows:
                lines.append(
                    f"| #{row.chunk_id} | "
                    f"{rank_text(row.candidate_rank)} | "
                    f"{score_text(row.cross_query_rrf_score)} | "
                    f"{md_cell(coarse_query_rank_text(best_query_rank_row(row)))} | "
                    f"{per_query_route_ranks_text(row)} | "
                    f"{md_cell(coarse_vector_rank_text(best_vector_rank_row(row)))} | "
                    f"{md_cell(coarse_lexical_rank_text(best_lexical_rank_row(row)))} | "
                    f"{query_support_count(row)} | "
                    f"{coarse_labels_text(row)} | "
                    f"{list_text(row.reason_hints)} | "
                    f"{md_cell(row.note_path)} | "
                    f"{md_cell(path_text(row.heading_path))} | "
                    f"{md_cell(row.content_preview)} |"
                )
        elif r.coarse_rank_diagnostics:
            lines.append("- coarse_rank_diagnostics: no selected rows")
        else:
            lines.append("- coarse_rank_diagnostics: unavailable in trace")
        query_vote_rows = build_query_vote_diagnostics(r.coarse_rank_diagnostics)
        if query_vote_rows:
            lines.append("")
            lines.append(
                "| Expanded query | Role | Weight | RRF support rows | Relevant chunks | Direct chunks | Necessary chunks | Hard-negative chunks | Best relevant | Best direct | Best hard-negative | Hints | Top labeled chunks |"
            )
            lines.append(
                "|----------------|------|--------|-------------------|-----------------|---------------|------------------|----------------------|---------------|-------------|--------------------|-------|--------------------|"
            )
            for query_vote in query_vote_rows:
                lines.append(
                    f"| q{query_vote.query_index}: {md_cell(query_vote.query)} | "
                    f"{query_vote.role} | "
                    f"{query_vote.query_weight:g} | "
                    f"{query_vote.contributed_candidate_count} | "
                    f"{int_list_text(query_vote.relevant_chunk_ids)} | "
                    f"{int_list_text(query_vote.direct_evidence_chunk_ids)} | "
                    f"{int_list_text(query_vote.necessary_context_chunk_ids)} | "
                    f"{int_list_text(query_vote.hard_negative_chunk_ids)} | "
                    f"{rank_text(query_vote.best_relevant_rank)} | "
                    f"{rank_text(query_vote.best_direct_evidence_rank)} | "
                    f"{rank_text(query_vote.best_hard_negative_rank)} | "
                    f"{list_text(query_vote.hints)} | "
                    f"{list_text(query_vote.top_labeled_chunks)} |"
                )
        elif r.coarse_rank_diagnostics:
            lines.append("- query_vote_diagnostics: no selected rows")
        else:
            lines.append("- query_vote_diagnostics: unavailable in trace")
        original_weight_rows = select_original_query_weight_rows(
            build_original_query_weight_simulation(r.coarse_rank_diagnostics)
        )
        if original_weight_rows:
            lines.append("")
            lines.append(
                f"| Chunk | Original rank | q0x{ORIGINAL_QUERY_WEIGHT_SIMULATION:g} rank | Delta | Original score | Weighted score | Labels | Hints | Note | Heading |"
            )
            lines.append(
                "|-------|---------------|-------------|-------|----------------|----------------|--------|-------|------|---------|"
            )
            for row in original_weight_rows:
                lines.append(
                    f"| #{row.chunk_id} | "
                    f"{rank_text(row.original_rank)} | "
                    f"{rank_text(row.weighted_rank)} | "
                    f"{rank_delta_text(row.rank_delta)} | "
                    f"{score_text(row.original_score)} | "
                    f"{score_text(row.weighted_score)} | "
                    f"{original_weight_labels_text(row)} | "
                    f"{list_text(row.hints)} | "
                    f"{md_cell(row.note_path)} | "
                    f"{md_cell(path_text(row.heading_path))} |"
                )
        elif r.coarse_rank_diagnostics:
            lines.append("- original_query_weight_simulation: no labeled rows")
        else:
            lines.append("- original_query_weight_simulation: unavailable in trace")
        movement_rows = select_movement_rows(
            r.rerank_movements,
            selected_top_k=r.selected_top_k,
        )
        if movement_rows:
            lines.append("")
            lines.append(
                "| Chunk | Hybrid rank | Selected rank | Delta | Rerank score | Labels | Note | Heading |"
            )
            lines.append(
                "|-------|----------------|-------------|-------|--------------|--------|------|---------|"
            )
            for movement in movement_rows:
                lines.append(
                    f"| #{movement.chunk_id} | "
                    f"{rank_text(movement.candidate_rank)} | "
                    f"{rank_text(movement.rerank_rank)} | "
                    f"{rank_delta_text(movement.rank_delta)} | "
                    f"{score_text(movement.rerank_score)} | "
                    f"{movement_labels_text(movement)} | "
                    f"{md_cell(movement.note_path)} | "
                    f"{md_cell(path_text(movement.heading_path))} |"
                )
        elif r.rerank_movements:
            lines.append("- rerank_movement: no top-rerank/direct/hard-negative rows")
        else:
            lines.append("- rerank_movement: unavailable in trace")
        if not r.top_chunks:
            lines.append("\nNo chunks returned.\n")
            continue
        lines.append("")
        lines.append(
            "| Rank | Chunk | Note | Heading | Rerank score | Labels | Anchor hits |"
        )
        lines.append(
            "|------|-------|------|---------|--------------|--------|-------------|"
        )
        for chunk in r.top_chunks:
            lines.append(
                f"| {chunk.rank} | #{chunk.chunk_id} | "
                f"{md_cell(chunk.note_path)} | {md_cell(path_text(chunk.heading_path))} | "
                f"{chunk.rerank_score:.4f} | {labels_text(chunk)} | "
                f"{list_text(chunk.matched_anchors)} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def print_result(result: SmokeResult) -> None:
    status = "PASS" if result.passed else "FAIL"
    print(
        f"[{status}] {result.case.id} zero_hit={result.predicted_zero_hit} "
        f"mode={result.rerank_mode} "
        f"input_k={result.rerank_input_top_k} "
        f"top_k={result.selected_top_k} "
        f"parent_doc={result.parent_doc_mode} "
        f"hits={len(result.expected_hits)} "
        f"cand@50={percent_text(result.candidate_recall_at_50)} "
        f"selected@K={percent_text(result.rerank_recall_at_10)} "
        f"mrr@K={percent_text(result.mrr_at_10)} "
        f"final={percent_text(result.final_context_recall)} "
        f"headings={len(result.expected_heading_hits)}/"
        f"{len(result.case.expected_heading_paths)} "
        f"anchors={len(result.evidence_anchor_hits)}/"
        f"{len(result.case.evidence_anchors)} "
        f"hard_neg={len(result.hard_negative_hits)} "
        f"chunks={result.retrieved_chunk_count} "
        f"rerank_tokens={result.rerank_tokens}"
    )


async def close_openai_owner(owner: object) -> None:
    client = getattr(owner, "_client", None)
    close = getattr(client, "close", None) or getattr(client, "aclose", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        try:
            await asyncio.wait_for(result, timeout=5)
        except TimeoutError:
            print("warning: timed out while closing OpenAI-compatible client")


async def close_clients() -> None:
    await reset_http_client()
    embedder = getattr(embedder_infra, "_embedder", None)
    try:
        if embedder is not None:
            await close_openai_owner(embedder)
    finally:
        embedder_infra.reset_embedder()
    llm_client = getattr(llm_infra, "_client", None)
    try:
        if llm_client is not None:
            await close_openai_owner(getattr(llm_client, "_provider", None))
    finally:
        llm_infra.reset_client()
    shutdown_langfuse()
    try:
        from langfuse.utils.langfuse_singleton import LangfuseSingleton
    except ImportError:
        return
    LangfuseSingleton().reset()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evals/suites/hybrid_search/dataset.note_smoke.jsonl"),
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("evals/reports"),
    )
    parser.add_argument(
        "--score-trace",
        type=Path,
        default=None,
        help=(
            "Re-score a previous trace JSONL against the current dataset "
            "without calling rewrite/search/rerank services."
        ),
    )
    parser.add_argument(
        "--rerank-mode",
        choices=("provider", "none"),
        default="provider",
        help=(
            "provider calls qwen3-rerank; none selects the hybrid RRF order "
            "directly and records zero rerank cost."
        ),
    )
    parser.add_argument(
        "--selected-top-k",
        type=int,
        default=RERANK_TOP_K,
        help="Number of selected seed chunks before optional parent-doc expansion.",
    )
    parser.add_argument(
        "--rerank-input-top-k",
        type=int,
        default=QWEN3_RERANK_MAX_DOCUMENTS,
        help=(
            "Number of hybrid RRF chunks passed to the selected stage. "
            "Use this to simulate coarse topK -> rerank topN."
        ),
    )
    parser.add_argument(
        "--parent-doc-mode",
        choices=("on", "off"),
        default="on",
        help="on expands selected chunks to parent-doc context; off uses seeds only.",
    )
    args = parser.parse_args()
    if args.selected_top_k < 1:
        raise SystemExit("--selected-top-k must be >= 1")
    if args.selected_top_k > QWEN3_RERANK_MAX_DOCUMENTS:
        raise SystemExit(
            f"--selected-top-k must be <= {QWEN3_RERANK_MAX_DOCUMENTS}"
        )
    if args.rerank_input_top_k < 1:
        raise SystemExit("--rerank-input-top-k must be >= 1")
    if args.rerank_input_top_k > QWEN3_RERANK_MAX_DOCUMENTS:
        raise SystemExit(
            f"--rerank-input-top-k must be <= {QWEN3_RERANK_MAX_DOCUMENTS}"
        )
    if args.selected_top_k > args.rerank_input_top_k:
        raise SystemExit("--selected-top-k must be <= --rerank-input-top-k")

    cases = load_cases(args.dataset)
    args.report_dir.mkdir(parents=True, exist_ok=True)

    if args.score_trace is not None:
        traces_by_case = load_traces(args.score_trace)
        missing_case_ids = [
            case.id for case in cases if case.id not in traces_by_case
        ]
        if missing_case_ids:
            raise SystemExit(
                "Trace is missing cases: " + ", ".join(missing_case_ids)
            )

        results = [
            score_case_trace(case, traces_by_case[case.id])
            for case in cases
        ]
        for result in results:
            print_result(result)

        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        path = args.report_dir / f"hybrid-search-note-smoke-rescore-{timestamp}.md"
        report = render_report(
            results,
            llm_calls=0,
            llm_cost=Decimal("0"),
            score_trace_path=args.score_trace,
        )
        path.write_text(report, encoding="utf-8")

        rerank_cost = sum(
            (r.rerank_cost_cny for r in results), Decimal("0")
        )
        print("")
        print(f"summary: passed={sum(r.passed for r in results)}/{len(results)}")
        print("new_llm_calls=0 new_rerank_calls=0")
        print(f"trace_rerank_cost_cny={rerank_cost:.6f}")
        print(f"report={path}")
        return

    started_at = datetime.now(UTC)
    engine = get_engine()
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    results: list[SmokeResult] = []
    traces: list[SmokeTrace] = []
    try:
        for case in cases:
            result, smoke_trace = await evaluate_case(
                case,
                sessionmaker,
                rerank_mode=args.rerank_mode,
                rerank_input_top_k=args.rerank_input_top_k,
                selected_top_k=args.selected_top_k,
                parent_doc_mode=args.parent_doc_mode,
            )
            results.append(result)
            traces.append(smoke_trace)
            print_result(result)

        llm_calls, llm_cost = await summarize_llm_costs(sessionmaker, started_at)
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        path = args.report_dir / (
            f"hybrid-search-note-smoke-{timestamp}.md"
        )
        trace_path = args.report_dir / (
            f"hybrid-search-note-smoke-{timestamp}.trace.jsonl"
        )
        write_traces(trace_path, traces)
        report = render_report(
            results,
            llm_calls,
            llm_cost,
            trace_output_path=trace_path,
        )
        path.write_text(report, encoding="utf-8")

        rerank_cost = sum(
            (r.rerank_cost_cny for r in results), Decimal("0")
        )
        print("")
        print(f"summary: passed={sum(r.passed for r in results)}/{len(results)}")
        print(f"llm_calls={llm_calls} llm_cost_cny={llm_cost:.6f}")
        print(f"rerank_cost_cny={rerank_cost:.6f}")
        print(f"observed_cost_cny={(llm_cost + rerank_cost):.6f}")
        print(f"report={path}")
        print(f"trace={trace_path}")
    finally:
        try:
            await asyncio.wait_for(engine.dispose(), timeout=5)
        except TimeoutError:
            print("warning: timed out while disposing database engine")
        try:
            await asyncio.wait_for(close_clients(), timeout=15)
        except TimeoutError:
            print("warning: timed out while closing smoke eval clients")


if __name__ == "__main__":
    asyncio.run(main())
