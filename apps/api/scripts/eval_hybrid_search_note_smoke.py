"""Run note-level smoke for the M2 hybrid search pipeline.

This is intentionally smaller than a formal eval:
- reads `evals/suites/hybrid_search/dataset.note_smoke.jsonl`
- runs the same query rewrite -> hybrid search -> rerank -> parent-doc path
- prints top note paths, hard-negative intrusion, zero-hit behavior, and cost
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
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
    expected_note_paths: list[str]
    hard_negative_note_paths: list[str]
    expected_zero_hit: bool
    notes: str


@dataclass(frozen=True)
class SmokeResult:
    case: SmokeCase
    passed: bool
    predicted_zero_hit: bool
    expanded_queries: list[str]
    top_note_paths: list[str]
    expected_hits: list[str]
    hard_negative_hits: list[str]
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
        cases.append(
            SmokeCase(
                id=str(obj["id"]),
                query=str(obj["input"]["query"]),
                expected_note_paths=[str(x) for x in gt["expected_note_paths"]],
                hard_negative_note_paths=[
                    str(x) for x in gt["hard_negative_note_paths"]
                ],
                expected_zero_hit=bool(gt["expected_zero_hit"]),
                notes=str(obj.get("notes", "")),
            )
        )
    return cases


async def run_pipeline_with_cost(
    session: AsyncSession,
    user_query: str,
) -> tuple[PipelineResult, int, Decimal]:
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
    note_ids = list({chunk.note_id for chunk, _ in expanded_scored})
    note_titles = await fetch_note_titles(session, note_ids)
    retrieved = [
        RetrievedChunk(
            chunk=chunk,
            folder_path=list(chunk.folder_path),
            heading_path=list(chunk.heading_path),
            note_title=note_titles.get(chunk.note_id, ""),
            rerank_score=score,
        )
        for chunk, score in expanded_scored
    ]
    return (
        PipelineResult(
            expanded_queries=expanded_queries,
            retrieved_chunks=retrieved,
        ),
        rerank_result.total_tokens,
        rerank_result.cost_cny,
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


async def evaluate_case(
    case: SmokeCase,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> SmokeResult:
    try:
        async with sessionmaker() as session:
            result, rerank_tokens, rerank_cost = await run_pipeline_with_cost(
                session, case.query
            )
    except NoChunksForQueryError as exc:
        passed = case.expected_zero_hit
        return SmokeResult(
            case=case,
            passed=passed,
            predicted_zero_hit=True,
            expanded_queries=[case.query],
            top_note_paths=[],
            expected_hits=[],
            hard_negative_hits=[],
            retrieved_chunk_count=0,
            rerank_tokens=0,
            rerank_cost_cny=Decimal("0"),
            error=exc.detail,
        )

    top_notes = ordered_unique([note_path(item) for item in result.retrieved_chunks])
    expected_hits = [p for p in top_notes if p in case.expected_note_paths]
    hard_negative_hits = [
        p for p in top_notes if p in case.hard_negative_note_paths
    ]
    passed = (
        not case.expected_zero_hit
        and bool(expected_hits)
        and not hard_negative_hits
    )
    return SmokeResult(
        case=case,
        passed=passed,
        predicted_zero_hit=False,
        expanded_queries=result.expanded_queries,
        top_note_paths=top_notes,
        expected_hits=expected_hits,
        hard_negative_hits=hard_negative_hits,
        retrieved_chunk_count=len(result.retrieved_chunks),
        rerank_tokens=rerank_tokens,
        rerank_cost_cny=rerank_cost,
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


def render_report(results: list[SmokeResult], llm_calls: int, llm_cost: Decimal) -> str:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    expected_zero = [r for r in results if r.case.expected_zero_hit]
    zero_passed = sum(1 for r in expected_zero if r.passed)
    rerank_tokens = sum(r.rerank_tokens for r in results)
    rerank_cost = sum((r.rerank_cost_cny for r in results), Decimal("0"))
    total_cost = llm_cost + rerank_cost

    lines: list[str] = []
    lines.append("# Hybrid Search Note Smoke Report\n")
    lines.append(f"- generated_at: {datetime.now(UTC).isoformat()}")
    lines.append(f"- database_url: `{settings.database_url}`")
    lines.append(f"- cases: {total}")
    lines.append(f"- passed: {passed}/{total}")
    lines.append(f"- zero_hit_passed: {zero_passed}/{len(expected_zero)}")
    lines.append(f"- llm_calls_since_start: {llm_calls}")
    lines.append(f"- llm_cost_cny: {llm_cost:.6f}")
    lines.append(f"- rerank_tokens: {rerank_tokens}")
    lines.append(f"- rerank_cost_cny: {rerank_cost:.6f}")
    lines.append(f"- observed_cost_cny: {total_cost:.6f}")
    lines.append("\n## Cases\n")
    lines.append(
        "| ID | Result | Expected hits | Hard negatives | Zero-hit | Chunks | Top notes |"
    )
    lines.append("|----|--------|---------------|----------------|----------|--------|-----------|")
    for r in results:
        result = "PASS" if r.passed else "FAIL"
        zero = "yes" if r.predicted_zero_hit else "no"
        hits = "<br>".join(r.expected_hits) if r.expected_hits else "-"
        hard = (
            "<br>".join(r.hard_negative_hits) if r.hard_negative_hits else "-"
        )
        top = "<br>".join(r.top_note_paths[:8]) if r.top_note_paths else "-"
        lines.append(
            f"| {r.case.id} | {result} | {hits} | {hard} | {zero} | "
            f"{r.retrieved_chunk_count} | {top} |"
        )
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
