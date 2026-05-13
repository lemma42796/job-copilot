"""Run note/chunk-level smoke for the M2 hybrid search pipeline.

This is intentionally smaller than a formal eval:
- reads `evals/suites/hybrid_search/dataset.note_smoke.jsonl`
- runs the same query rewrite -> hybrid search -> rerank -> parent-doc path
- prints top notes, top chunks, heading/anchor coverage, hard-negative intrusion,
  formal retrieval metrics, zero-hit behavior, and cost
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
from jobcopilot_api.infra.db import get_engine
from jobcopilot_api.infra.embedder import get_embedder, reset_embedder
from jobcopilot_api.infra.llm import get_llm_client, reset_client
from jobcopilot_api.models.llm_call import LlmCall
from jobcopilot_api.schemas.retrieval import PipelineResult, RetrievedChunk
from jobcopilot_api.services.query_rewriter import rewrite_query
from jobcopilot_api.services.reranker import rerank, reset_http_client
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
class PipelineTrace:
    result: PipelineResult
    candidate_chunks: list[RetrievedChunk]
    reranked_chunks: list[RetrievedChunk]
    rerank_tokens: int
    rerank_cost_cny: Decimal


@dataclass(frozen=True)
class SmokeResult:
    case: SmokeCase
    passed: bool
    predicted_zero_hit: bool
    expanded_queries: list[str]
    candidate_chunk_ids: list[int]
    rerank_chunk_ids: list[int]
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

    if len(fused) < MIN_CHUNKS_FOR_QUIZ:
        raise NoChunksForQueryError(
            f"query='{user_query}' hit {len(fused)} chunks"
        )

    rerank_result = await rerank(user_query, fused, top_k=RERANK_TOP_K)
    expanded_scored = await expand_to_parent_docs(session, rerank_result.scored)
    all_chunks = [
        *fused[:50],
        *[chunk for chunk, _ in rerank_result.scored],
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
        for chunk, score in rerank_result.scored[:RERANK_TOP_K]
    ]
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
        rerank_tokens=rerank_result.total_tokens,
        rerank_cost_cny=rerank_result.cost_cny,
    )


def note_path(item: RetrievedChunk) -> str:
    return "/".join([*item.folder_path, f"{item.note_title}.md"])


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
    retrieved_chunks: list[RetrievedChunk],
) -> list[ChunkHit]:
    direct_evidence_chunk_ids = set(case.direct_evidence_chunk_ids)
    necessary_context_chunk_ids = set(case.necessary_context_chunk_ids)
    out: list[ChunkHit] = []
    for rank, item in enumerate(retrieved_chunks, start=1):
        chunk_id = int(item.chunk.id)
        current_note_path = note_path(item)
        matched_anchors = [
            anchor
            for anchor in case.evidence_anchors
            if anchor_matches(item.chunk.content, anchor)
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


async def evaluate_case(
    case: SmokeCase,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> SmokeResult:
    try:
        async with sessionmaker() as session:
            trace = await run_pipeline_with_cost(session, case.query)
    except NoChunksForQueryError as exc:
        passed = case.expected_zero_hit
        return SmokeResult(
            case=case,
            passed=passed,
            predicted_zero_hit=True,
            expanded_queries=[case.query],
            candidate_chunk_ids=[],
            rerank_chunk_ids=[],
            top_note_paths=[],
            top_chunks=[],
            candidate_direct_evidence_hits=[],
            rerank_direct_evidence_hits=[],
            expected_hits=[],
            direct_evidence_hits=[],
            necessary_context_hits=[],
            expected_heading_hits=[],
            evidence_anchor_hits=[],
            hard_negative_hits=[],
            hard_negative_ranks={},
            failure_hints=[] if passed else ["unexpected_zero_hit"],
            candidate_recall_at_50=None,
            rerank_recall_at_10=None,
            mrr_at_10=None,
            final_context_recall=None,
            final_context_precision=None,
            retrieved_chunk_count=0,
            rerank_tokens=0,
            rerank_cost_cny=Decimal("0"),
            error=exc.detail,
        )

    result = trace.result
    candidate_chunk_ids = chunk_ids(trace.candidate_chunks)
    rerank_chunk_ids = chunk_ids(trace.reranked_chunks)
    candidate_hits = match_direct_evidence_hits(
        case.direct_evidence_chunk_ids, candidate_chunk_ids
    )
    rerank_hits = match_direct_evidence_hits(
        case.direct_evidence_chunk_ids, rerank_chunk_ids
    )
    top_chunks = build_chunk_hits(case, result.retrieved_chunks)
    top_notes = ordered_unique([note_path(item) for item in result.retrieved_chunks])
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
        expanded_queries=result.expanded_queries,
        candidate_chunk_ids=candidate_chunk_ids,
        rerank_chunk_ids=rerank_chunk_ids,
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
        mrr_at_10=mrr_at_k(case.direct_evidence_chunk_ids, rerank_chunk_ids),
        final_context_recall=recall_ratio(
            case.direct_evidence_chunk_ids, direct_evidence_hits
        ),
        final_context_precision=calculate_final_context_precision(top_chunks),
        retrieved_chunk_count=len(result.retrieved_chunks),
        rerank_tokens=trace.rerank_tokens,
        rerank_cost_cny=trace.rerank_cost_cny,
    )


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


def render_report(results: list[SmokeResult], llm_calls: int, llm_cost: Decimal) -> str:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
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
    lines.append(f"- cases: {total}")
    lines.append(f"- passed: {passed}/{total}")
    lines.append(
        "- pass_rule: non-zero cases need at least one expected note and no "
        "hard-negative note; chunk/heading/anchor fields are diagnostics"
    )
    lines.append(f"- candidate_recall@50: {percent_text(candidate_recall)}")
    lines.append(f"- rerank_recall@10: {percent_text(rerank_recall)}")
    lines.append(f"- mrr@10: {percent_text(mean_mrr)}")
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
        "| ID | Result | candidate@50 | rerank@10 | mrr@10 | final recall | final precision | Zero-hit | Chunks | Failure hints |"
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
            f"rerank_recall@10={percent_text(r.rerank_recall_at_10)}, "
            f"mrr@10={percent_text(r.mrr_at_10)}, "
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


async def close_openai_owner(owner: object) -> None:
    client = getattr(owner, "_client", None)
    close = getattr(client, "close", None) or getattr(client, "aclose", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


async def close_clients() -> None:
    await reset_http_client()
    try:
        await close_openai_owner(get_embedder())
    finally:
        reset_embedder()
    try:
        llm_client = get_llm_client()
        await close_openai_owner(getattr(llm_client, "_provider", None))
    finally:
        reset_client()


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
    args = parser.parse_args()

    started_at = datetime.now(UTC)
    cases = load_cases(args.dataset)
    engine = get_engine()
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    results: list[SmokeResult] = []
    try:
        for case in cases:
            result = await evaluate_case(case, sessionmaker)
            results.append(result)
            status = "PASS" if result.passed else "FAIL"
            print(
                f"[{status}] {case.id} zero_hit={result.predicted_zero_hit} "
                f"hits={len(result.expected_hits)} "
                f"cand@50={percent_text(result.candidate_recall_at_50)} "
                f"rerank@10={percent_text(result.rerank_recall_at_10)} "
                f"mrr@10={percent_text(result.mrr_at_10)} "
                f"final={percent_text(result.final_context_recall)} "
                f"headings={len(result.expected_heading_hits)}/"
                f"{len(case.expected_heading_paths)} "
                f"anchors={len(result.evidence_anchor_hits)}/"
                f"{len(case.evidence_anchors)} "
                f"hard_neg={len(result.hard_negative_hits)} "
                f"chunks={result.retrieved_chunk_count} "
                f"rerank_tokens={result.rerank_tokens}"
            )

        llm_calls, llm_cost = await summarize_llm_costs(sessionmaker, started_at)
        report = render_report(results, llm_calls, llm_cost)
        args.report_dir.mkdir(parents=True, exist_ok=True)
        path = args.report_dir / (
            "hybrid-search-note-smoke-"
            f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.md"
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
    finally:
        await engine.dispose()
        await close_clients()


if __name__ == "__main__":
    asyncio.run(main())
