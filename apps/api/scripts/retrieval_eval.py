"""Retrieval ablation 评测脚本(S21 子任务 4-A)。

读 ground-truth JSON Lines(`evals/suites/retrieval/dataset.jsonl`),对每条
(profile_id, query_text, expected_chunk_ids) 跑 v0(纯向量 top-K)+ v1(hybrid
+ RRF)各算 Recall@10 / NDCG@10,输出对比 markdown 报告。

跑法(从项目根):
    uv run python apps/api/scripts/retrieval_eval.py \\
        --dataset evals/suites/retrieval/dataset.jsonl \\
        --report  evals/reports/retrieval-ablation-2026-05.md

环境:连真实 dev DB(`JOBCOPILOT_DATABASE_URL`)+ 真实 embedder
(`JOBCOPILOT_LLM_PROVIDER=dashscope`),会消耗少量 token。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from jobcopilot_api.infra.embedder import get_embedder
from jobcopilot_api.llm.embedders import Embedder
from jobcopilot_api.services.retrieval_service import (
    hybrid_retrieve_for_match,
    retrieve_for_match,
)
from jobcopilot_api.settings import settings


@dataclass
class Case:
    id: str
    category: str
    profile_id: int
    query_text: str
    expected_chunk_ids: list[int]
    notes: str = ""


@dataclass
class CaseMetrics:
    recall_at_10: float
    ndcg_at_10: float
    retrieved_ids: list[int]


def recall_at_k(retrieved: list[int], relevant: set[int], k: int = 10) -> float:
    if not relevant:
        return 0.0
    hits = sum(1 for cid in retrieved[:k] if cid in relevant)
    return hits / len(relevant)


def ndcg_at_k(retrieved: list[int], relevant: set[int], k: int = 10) -> float:
    """二值相关性 NDCG@k:DCG = Σ rel_i / log2(i+1),IDCG = ideal 排序的 DCG。"""
    if not relevant:
        return 0.0
    dcg = sum(
        (1.0 / math.log2(i + 2))
        for i, cid in enumerate(retrieved[:k])
        if cid in relevant
    )
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    return dcg / idcg if idcg > 0 else 0.0


def load_dataset(path: Path) -> list[Case]:
    cases: list[Case] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        c = json.loads(line)
        cases.append(
            Case(
                id=c["id"],
                category=c.get("category", ""),
                profile_id=int(c["profile_id"]),
                query_text=str(c["query_text"]),
                expected_chunk_ids=[int(x) for x in c["expected_chunk_ids"]],
                notes=str(c.get("notes", "")),
            )
        )
    return cases


async def evaluate_case(
    case: Case,
    sessionmaker: async_sessionmaker[AsyncSession],
    embedder: Embedder,
    *,
    k: int = 10,
) -> tuple[CaseMetrics, CaseMetrics]:
    relevant = set(case.expected_chunk_ids)

    v0 = await retrieve_for_match(
        sessionmaker,
        profile_id=case.profile_id,
        query_text=case.query_text,
        embedder=embedder,
        k=k,
    )
    v0_ids = [c.id for c in v0.chunks]
    v0_metrics = CaseMetrics(
        recall_at_10=recall_at_k(v0_ids, relevant, k=k),
        ndcg_at_10=ndcg_at_k(v0_ids, relevant, k=k),
        retrieved_ids=v0_ids,
    )

    v1 = await hybrid_retrieve_for_match(
        sessionmaker,
        profile_id=case.profile_id,
        query_text=case.query_text,
        embedder=embedder,
        k=k,
    )
    v1_ids = [c.id for c in v1.chunks]
    v1_metrics = CaseMetrics(
        recall_at_10=recall_at_k(v1_ids, relevant, k=k),
        ndcg_at_10=ndcg_at_k(v1_ids, relevant, k=k),
        retrieved_ids=v1_ids,
    )
    return v0_metrics, v1_metrics


def render_report(
    cases: list[Case],
    results: list[tuple[CaseMetrics, CaseMetrics]],
) -> str:
    n = len(cases)
    avg_v0_recall = sum(r[0].recall_at_10 for r in results) / n
    avg_v1_recall = sum(r[1].recall_at_10 for r in results) / n
    avg_v0_ndcg = sum(r[0].ndcg_at_10 for r in results) / n
    avg_v1_ndcg = sum(r[1].ndcg_at_10 for r in results) / n

    lines: list[str] = []
    lines.append("# Retrieval Ablation 报告(v0 纯向量 vs v1 hybrid+RRF)\n")
    lines.append(f"样本量:{n} 条 | k = 10\n")
    lines.append("## 总体均值\n")
    lines.append("| 指标 | v0 | v1 | Δ |")
    lines.append("|------|----|----|---|")
    lines.append(
        f"| Recall@10 | {avg_v0_recall:.3f} | {avg_v1_recall:.3f} | "
        f"{avg_v1_recall - avg_v0_recall:+.3f} |"
    )
    lines.append(
        f"| NDCG@10  | {avg_v0_ndcg:.3f} | {avg_v1_ndcg:.3f} | "
        f"{avg_v1_ndcg - avg_v0_ndcg:+.3f} |"
    )

    by_cat: dict[str, list[int]] = {}
    for i, c in enumerate(cases):
        by_cat.setdefault(c.category, []).append(i)
    if len(by_cat) > 1:
        lines.append("\n## 分类 ablation\n")
        lines.append("| 分类 | n | v0 R@10 | v1 R@10 | v0 NDCG | v1 NDCG |")
        lines.append("|------|---|---------|---------|---------|---------|")
        for cat, idxs in sorted(by_cat.items()):
            v0_r = sum(results[i][0].recall_at_10 for i in idxs) / len(idxs)
            v1_r = sum(results[i][1].recall_at_10 for i in idxs) / len(idxs)
            v0_n = sum(results[i][0].ndcg_at_10 for i in idxs) / len(idxs)
            v1_n = sum(results[i][1].ndcg_at_10 for i in idxs) / len(idxs)
            lines.append(
                f"| {cat} | {len(idxs)} | {v0_r:.3f} | {v1_r:.3f} | "
                f"{v0_n:.3f} | {v1_n:.3f} |"
            )

    lines.append("\n## Per-case\n")
    lines.append(
        "| ID | 分类 | v0 R@10 | v1 R@10 | v0 NDCG | v1 NDCG | "
        "v0 retrieved | v1 retrieved |"
    )
    lines.append("|----|------|---------|---------|---------|---------|"
                 "--------------|--------------|")
    for c, (v0m, v1m) in zip(cases, results, strict=True):
        lines.append(
            f"| {c.id} | {c.category} | {v0m.recall_at_10:.2f} | "
            f"{v1m.recall_at_10:.2f} | {v0m.ndcg_at_10:.2f} | "
            f"{v1m.ndcg_at_10:.2f} | {v0m.retrieved_ids[:10]} | "
            f"{v1m.retrieved_ids[:10]} |"
        )
    return "\n".join(lines) + "\n"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()

    cases = load_dataset(args.dataset)
    if not cases:
        raise SystemExit("dataset is empty")

    engine = create_async_engine(settings.database_url, future=True)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    embedder = get_embedder()

    try:
        results: list[tuple[CaseMetrics, CaseMetrics]] = []
        for case in cases:
            v0m, v1m = await evaluate_case(case, sm, embedder)
            results.append((v0m, v1m))
            print(
                f"[{case.id}] v0 R={v0m.recall_at_10:.2f} v1 R={v1m.recall_at_10:.2f}"
            )

        report = render_report(cases, results)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report, encoding="utf-8")
        print(f"\nreport → {args.report}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
