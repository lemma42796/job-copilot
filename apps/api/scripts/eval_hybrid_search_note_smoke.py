"""Run note/chunk-level smoke for the M2 hybrid search pipeline.

This is intentionally smaller than a formal eval:
- reads `evals/suites/hybrid_search/dataset.note_smoke.jsonl`
- runs the same query rewrite -> hybrid search -> optional rerank -> parent-doc path
- prints top notes, top chunks, heading/anchor coverage, hard-negative intrusion,
  rerank movement, formal retrieval metrics, zero-hit behavior, and cost
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
from jobcopilot_api.services.query_rewriter import rewrite_query
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
from jobcopilot_api.services.search_service import global_hybrid_search
from jobcopilot_api.settings import settings


RERANK_DIAGNOSTIC_TOP_K = 50


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
) -> PipelineTrace:
    rewrite_out = await rewrite_query(user_query)
    expanded_queries = rewrite_out.expanded_queries

    hybrid_rankings = []
    for q in expanded_queries:
        ranking = await global_hybrid_search(
            session, q, top_k=HYBRID_TOP_K_PER_QUERY
        )
        hybrid_rankings.append(ranking)
    fused = multi_query_rrf(hybrid_rankings, k=RRF_K)
    rerank_input = fused[:rerank_input_top_k]
    candidate_base = fused[:50]
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
        for chunk in fused[:50]
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
        if r.error:
            lines.append(f"- error: {md_cell(r.error)}")
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
