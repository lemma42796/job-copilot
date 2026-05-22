"""Evaluate JD-to-Knowledge coverage labels against saved analysis reports.

This runner does not call LLMs. It reads manual labels from JSONL, loads the
predicted coverage entries from `jd_analyses.note_match_summary`, and writes a
markdown report with the metrics that matter for resume-grade evidence:

- coverage classification accuracy / macro F1
- missing recall
- false covered rate
- evidence precision@k / recall@k / MRR@k

Dataset JSONL shape:
{
  "id": "cov_001",
  "analysis_id": 12,
  "req_id": "req_3",
  "expected_status": "partial",
  "expected_evidence_chunk_ids": [9012, 9018],
  "notes": "Redis cluster note partially covers the requirement"
}

Usage:
  uv run python apps/api/scripts/eval_jd_coverage.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "apps" / "api" / "src"))

from jobcopilot_api.infra.db import get_engine  # noqa: E402
from jobcopilot_api.models.jd_analysis import JdAnalysis  # noqa: E402

COVERAGE_STATUSES = ("covered", "partial", "missing", "unknown")
DEFAULT_K = 5
DEFAULT_DATASET = Path("evals/suites/jd_coverage/dataset.jsonl")
DEFAULT_REPORT_DIR = Path("evals/reports")


@dataclass(frozen=True)
class CoverageLabel:
    id: str
    analysis_id: int
    req_id: str
    expected_status: str
    expected_evidence_chunk_ids: list[int]
    notes: str = ""


@dataclass(frozen=True)
class CoveragePrediction:
    status: str
    evidence_chunk_ids: list[int]
    matched_note_ids: list[int]
    raw: dict[str, Any]


@dataclass(frozen=True)
class CaseResult:
    label: CoverageLabel
    prediction: CoveragePrediction
    status_match: bool
    evidence_precision_at_k: float | None
    evidence_recall_at_k: float | None
    evidence_mrr_at_k: float | None
    failures: list[str]


def load_labels(path: Path) -> list[CoverageLabel]:
    if not path.exists():
        raise FileNotFoundError(
            f"dataset not found: {path}. Create JSONL labels with "
            "id, analysis_id, req_id, expected_status, and "
            "expected_evidence_chunk_ids."
        )
    labels: list[CoverageLabel] = []
    seen: set[str] = set()
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        raw = json.loads(line)
        case_id = str(raw["id"])
        if case_id in seen:
            raise ValueError(f"duplicate id={case_id!r} at line {line_no}")
        seen.add(case_id)
        expected_status = normalize_status(str(raw["expected_status"]))
        labels.append(
            CoverageLabel(
                id=case_id,
                analysis_id=int(raw["analysis_id"]),
                req_id=str(raw["req_id"]),
                expected_status=expected_status,
                expected_evidence_chunk_ids=[
                    int(item) for item in raw.get("expected_evidence_chunk_ids", [])
                ],
                notes=str(raw.get("notes", "")),
            )
        )
    if not labels:
        raise ValueError(f"dataset is empty: {path}")
    return labels


async def load_predictions(
    labels: list[CoverageLabel],
) -> dict[tuple[int, str], CoveragePrediction]:
    analysis_ids = sorted({label.analysis_id for label in labels})
    engine = get_engine()
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.select(JdAnalysis.id, JdAnalysis.note_match_summary).where(
                        JdAnalysis.id.in_(analysis_ids)
                    )
                )
            ).mappings()
            predictions: dict[tuple[int, str], CoveragePrediction] = {}
            for row in rows:
                analysis_id = int(row["id"])
                for item in row["note_match_summary"] or []:
                    if not isinstance(item, dict):
                        continue
                    req_id = str(item.get("req_id") or "")
                    if not req_id:
                        continue
                    predictions[(analysis_id, req_id)] = parse_prediction(item)
            return predictions
    finally:
        await engine.dispose()


def parse_prediction(item: dict[str, Any]) -> CoveragePrediction:
    evidence_chunks = item.get("evidence_chunks") or []
    evidence_chunk_ids = [
        int(chunk["chunk_id"])
        for chunk in evidence_chunks
        if isinstance(chunk, dict) and chunk.get("chunk_id") is not None
    ]
    matched_note_ids = [
        int(note_id)
        for note_id in item.get("matched_note_ids", [])
        if note_id is not None
    ]
    return CoveragePrediction(
        status=normalize_status(str(item.get("status") or "unknown")),
        evidence_chunk_ids=evidence_chunk_ids,
        matched_note_ids=matched_note_ids,
        raw=item,
    )


def evaluate(
    labels: list[CoverageLabel],
    predictions: dict[tuple[int, str], CoveragePrediction],
    *,
    k: int,
) -> list[CaseResult]:
    results: list[CaseResult] = []
    for label in labels:
        prediction = predictions.get(
            (label.analysis_id, label.req_id),
            CoveragePrediction(
                status="unknown",
                evidence_chunk_ids=[],
                matched_note_ids=[],
                raw={},
            ),
        )
        failures: list[str] = []
        if prediction.status != label.expected_status:
            failures.append(
                f"status expected={label.expected_status} predicted={prediction.status}"
            )
        expected_evidence = set(label.expected_evidence_chunk_ids)
        evidence_precision = precision_at_k(
            prediction.evidence_chunk_ids,
            expected_evidence,
            k,
        )
        evidence_recall = recall_at_k(
            prediction.evidence_chunk_ids,
            expected_evidence,
            k,
        )
        evidence_mrr = mrr_at_k(
            prediction.evidence_chunk_ids,
            expected_evidence,
            k,
        )
        if expected_evidence and evidence_recall == 0:
            failures.append("expected evidence not found in predicted top-k chunks")
        results.append(
            CaseResult(
                label=label,
                prediction=prediction,
                status_match=prediction.status == label.expected_status,
                evidence_precision_at_k=evidence_precision,
                evidence_recall_at_k=evidence_recall,
                evidence_mrr_at_k=evidence_mrr,
                failures=failures,
            )
        )
    return results


def precision_at_k(
    predicted_ids: list[int],
    expected_ids: set[int],
    k: int,
) -> float | None:
    if not expected_ids:
        return None
    top = predicted_ids[:k]
    if not top:
        return 0.0
    return len([item for item in top if item in expected_ids]) / len(top)


def recall_at_k(
    predicted_ids: list[int],
    expected_ids: set[int],
    k: int,
) -> float | None:
    if not expected_ids:
        return None
    top = predicted_ids[:k]
    return len([item for item in top if item in expected_ids]) / len(expected_ids)


def mrr_at_k(
    predicted_ids: list[int],
    expected_ids: set[int],
    k: int,
) -> float | None:
    if not expected_ids:
        return None
    for rank, item in enumerate(predicted_ids[:k], start=1):
        if item in expected_ids:
            return 1 / rank
    return 0.0


def render_report(results: list[CaseResult], *, k: int) -> str:
    total = len(results)
    accuracy = sum(1 for result in results if result.status_match) / total
    labels = [result.label.expected_status for result in results]
    predictions = [result.prediction.status for result in results]
    per_label = classification_metrics(labels, predictions)
    macro_f1 = average([item["f1"] for item in per_label.values()])
    missing_recall = per_label.get("missing", {}).get("recall")
    false_covered_rate = calculate_false_covered_rate(labels, predictions)
    evidence_precision = average(
        [
            result.evidence_precision_at_k
            for result in results
            if result.evidence_precision_at_k is not None
        ]
    )
    evidence_recall = average(
        [
            result.evidence_recall_at_k
            for result in results
            if result.evidence_recall_at_k is not None
        ]
    )
    evidence_mrr = average(
        [
            result.evidence_mrr_at_k
            for result in results
            if result.evidence_mrr_at_k is not None
        ]
    )

    lines: list[str] = []
    lines.append("# JD-to-Knowledge Coverage Eval Report")
    lines.append("")
    lines.append(f"- generated_at: {datetime.now(UTC).isoformat()}")
    lines.append(f"- cases: {total}")
    lines.append(f"- evidence_k: {k}")
    lines.append("")
    lines.append("## Headline Metrics")
    lines.append("")
    lines.append(f"- coverage_macro_f1: {percent_text(macro_f1)}")
    lines.append(f"- missing_recall: {percent_text(missing_recall)}")
    lines.append(f"- false_covered_rate: {percent_text(false_covered_rate)}")
    lines.append(f"- evidence_precision@{k}: {percent_text(evidence_precision)}")
    lines.append(f"- evidence_recall@{k}: {percent_text(evidence_recall)}")
    lines.append(f"- evidence_mrr@{k}: {percent_text(evidence_mrr)}")
    lines.append("")
    lines.append("## Diagnostic Metrics")
    lines.append("")
    lines.append(f"- coverage_accuracy: {percent_text(accuracy)}")
    lines.append("")
    lines.append("## Per-label Metrics")
    lines.append("")
    lines.append("| Label | Precision | Recall | F1 | Support |")
    lines.append("|-------|-----------|--------|----|---------|")
    for label in COVERAGE_STATUSES:
        metric = per_label[label]
        lines.append(
            f"| {label} | {percent_text(metric['precision'])} | "
            f"{percent_text(metric['recall'])} | {percent_text(metric['f1'])} | "
            f"{int(metric['support'])} |"
        )
    lines.append("")
    lines.append("## Confusion Matrix")
    lines.append("")
    lines.append("| Expected \\ Predicted | covered | partial | missing | unknown |")
    lines.append("|----------------------|---------|---------|---------|---------|")
    confusion = confusion_matrix(labels, predictions)
    for expected in COVERAGE_STATUSES:
        row = [str(confusion[(expected, predicted)]) for predicted in COVERAGE_STATUSES]
        lines.append(f"| {expected} | {' | '.join(row)} |")
    lines.append("")
    lines.append("## Per-case")
    lines.append("")
    lines.append(
        "| ID | Analysis | Req | Expected | Predicted | "
        "Evidence P@k | Evidence R@k | Evidence MRR@k | Notes |"
    )
    lines.append(
        "|----|----------|-----|----------|-----------|"
        "--------------|--------------|----------------|-------|"
    )
    for result in results:
        failure_text = "; ".join(result.failures) or result.label.notes
        lines.append(
            f"| {result.label.id} | #{result.label.analysis_id} | "
            f"{result.label.req_id} | {result.label.expected_status} | "
            f"{result.prediction.status} | "
            f"{percent_text(result.evidence_precision_at_k)} | "
            f"{percent_text(result.evidence_recall_at_k)} | "
            f"{percent_text(result.evidence_mrr_at_k)} | "
            f"{escape_table(failure_text)} |"
        )
    return "\n".join(lines) + "\n"


def classification_metrics(
    labels: list[str],
    predictions: list[str],
) -> dict[str, dict[str, float]]:
    metrics: dict[str, dict[str, float]] = {}
    for label in COVERAGE_STATUSES:
        tp = sum(1 for y, p in zip(labels, predictions, strict=True) if y == label and p == label)
        fp = sum(1 for y, p in zip(labels, predictions, strict=True) if y != label and p == label)
        fn = sum(1 for y, p in zip(labels, predictions, strict=True) if y == label and p != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        support = sum(1 for y in labels if y == label)
        metrics[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": float(support),
        }
    return metrics


def confusion_matrix(labels: list[str], predictions: list[str]) -> Counter[tuple[str, str]]:
    confusion: Counter[tuple[str, str]] = Counter()
    for expected, predicted in zip(labels, predictions, strict=True):
        confusion[(expected, predicted)] += 1
    return confusion


def calculate_false_covered_rate(labels: list[str], predictions: list[str]) -> float | None:
    noncovered_total = sum(1 for expected in labels if expected != "covered")
    if noncovered_total == 0:
        return None
    false_covered = sum(
        1
        for expected, predicted in zip(labels, predictions, strict=True)
        if expected != "covered" and predicted == "covered"
    )
    return false_covered / noncovered_total


def average(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return sum(present) / len(present)


def normalize_status(status: str) -> str:
    if status not in COVERAGE_STATUSES:
        raise ValueError(f"unsupported coverage status: {status!r}")
    return status


def percent_text(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    args = parser.parse_args()

    labels = load_labels(args.dataset)
    predictions = await load_predictions(labels)
    results = evaluate(labels, predictions, k=args.k)
    report = render_report(results, k=args.k)
    report_path = args.report or default_report_path(args.report_dir)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(f"cases={len(results)}")
    print(f"report={report_path}")


def default_report_path(report_dir: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return report_dir / f"jd-coverage-{timestamp}.md"


if __name__ == "__main__":
    asyncio.run(main())
