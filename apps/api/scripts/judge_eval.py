"""LLM-as-Judge 评测脚本(S21 子任务 4-C)。

支持两种 suite:

- `resume_generate`(EVAL_PLAN §7.3):每条输入 = `(jd_text, profile_summary,
  resume_markdown)` + 可选 `human_label_bucket ∈ {high, mid, low}`(用于算 Judge
  自身的 Cohen's kappa)。Judge 输出 6 维 Rubric;Python 端按权重组合
  `total_score`,再按 ≥ 80 high / 60-79 mid / < 60 low 分桶与 human_label 对齐。
- `match_analysis`(EVAL_PLAN §6.3):每条输入 = `(claim, chunk)` + 可选
  `human_supports`(`true`/`false`)。Judge 输出 binary supports + reason;
  与 human 直接算 binary kappa。

跑法(从项目根):

    uv run python apps/api/scripts/judge_eval.py \\
        --suite resume_generate \\
        --dataset evals/suites/resume_generate/dataset.jsonl \\
        --results evals/reports/judge-resume-2026-05.results.jsonl \\
        --report  evals/reports/judge-resume-2026-05.md

环境:连真 DashScope(`JOBCOPILOT_DASHSCOPE_API_KEY`)。Judge 走 Tier.PREMIUM
(qwen3.6-plus thinking on);相同 dataset 重跑命中 4-B response cache,成本
接近 0(`cached=true / cost_cny=0`)。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from jobcopilot_api.evals.judge import JudgeClient
from jobcopilot_api.evals.kappa import cohen_kappa, confusion_matrix
from jobcopilot_api.infra.llm import get_llm_client

Suite = Literal["resume_generate", "match_analysis"]


# ---------- dataset cases ----------


@dataclass
class ResumeCase:
    id: str
    jd_text: str
    profile_summary: str
    resume_markdown: str
    human_label_bucket: str | None = None  # "high" / "mid" / "low" / None


@dataclass
class EvidenceCase:
    id: str
    claim: str
    chunk: str
    human_supports: bool | None = None


# ---------- result rows(写 jsonl)----------


@dataclass
class ResumeResultRow:
    id: str
    total_score: float
    bucket: str
    dimension_scores: dict[str, int]
    dimension_reasons: dict[str, str]
    cost_cny: str  # Decimal → str 便于 json
    latency_ms: int
    cached: bool
    human_label_bucket: str | None


@dataclass
class EvidenceResultRow:
    id: str
    supports: bool
    reason: str
    cost_cny: str
    latency_ms: int
    cached: bool
    human_supports: bool | None


def total_to_bucket(total: float) -> str:
    """≥ 80 → high / 60-79 → mid / < 60 → low(EVAL_PLAN §6.1 同一三档划分)。"""
    if total >= 80:
        return "high"
    if total >= 60:
        return "mid"
    return "low"


# ---------- runners ----------


async def run_resume_suite(
    cases: list[ResumeCase],
    judge: JudgeClient,
) -> list[ResumeResultRow]:
    rows: list[ResumeResultRow] = []
    for case in cases:
        result = await judge.judge_resume_rubric(
            jd_text=case.jd_text,
            profile_summary=case.profile_summary,
            resume_markdown=case.resume_markdown,
            trace_id=f"judge-resume-{case.id}",
        )
        dims = {
            "jd_alignment": result.rubric.jd_alignment,
            "factual_consistency": result.rubric.factual_consistency,
            "structure_readability": result.rubric.structure_readability,
            "quantification": result.rubric.quantification,
            "language_quality": result.rubric.language_quality,
            "length_compliance": result.rubric.length_compliance,
        }
        rows.append(
            ResumeResultRow(
                id=case.id,
                total_score=result.total_score,
                bucket=total_to_bucket(result.total_score),
                dimension_scores={k: v.score for k, v in dims.items()},
                dimension_reasons={k: v.reason for k, v in dims.items()},
                cost_cny=str(result.cost_cny),
                latency_ms=result.latency_ms,
                cached=result.cached,
                human_label_bucket=case.human_label_bucket,
            )
        )
        print(
            f"[{case.id}] total={result.total_score:.1f} "
            f"bucket={total_to_bucket(result.total_score)} "
            f"cached={result.cached} cost={result.cost_cny}"
        )
    return rows


async def run_evidence_suite(
    cases: list[EvidenceCase],
    judge: JudgeClient,
) -> list[EvidenceResultRow]:
    rows: list[EvidenceResultRow] = []
    for case in cases:
        result = await judge.judge_evidence(
            claim=case.claim,
            chunk=case.chunk,
            trace_id=f"judge-evidence-{case.id}",
        )
        rows.append(
            EvidenceResultRow(
                id=case.id,
                supports=result.verdict.supports,
                reason=result.verdict.reason,
                cost_cny=str(result.cost_cny),
                latency_ms=result.latency_ms,
                cached=result.cached,
                human_supports=case.human_supports,
            )
        )
        print(
            f"[{case.id}] supports={result.verdict.supports} "
            f"cached={result.cached} cost={result.cost_cny}"
        )
    return rows


# ---------- dataset I/O ----------


def load_resume_dataset(path: Path) -> list[ResumeCase]:
    cases: list[ResumeCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        c = json.loads(line)
        cases.append(
            ResumeCase(
                id=str(c["id"]),
                jd_text=str(c["jd_text"]),
                profile_summary=str(c["profile_summary"]),
                resume_markdown=str(c["resume_markdown"]),
                human_label_bucket=c.get("human_label_bucket"),
            )
        )
    return cases


def load_evidence_dataset(path: Path) -> list[EvidenceCase]:
    cases: list[EvidenceCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        c = json.loads(line)
        cases.append(
            EvidenceCase(
                id=str(c["id"]),
                claim=str(c["claim"]),
                chunk=str(c["chunk"]),
                human_supports=c.get("human_supports"),
            )
        )
    return cases


def write_results(rows: list[Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")


# ---------- report rendering ----------


def render_resume_report(rows: list[ResumeResultRow]) -> str:
    n = len(rows)
    avg_total = sum(r.total_score for r in rows) / n
    avg_factual = sum(r.dimension_scores["factual_consistency"] for r in rows) / n
    avg_jd = sum(r.dimension_scores["jd_alignment"] for r in rows) / n
    cached_count = sum(1 for r in rows if r.cached)
    total_cost = sum(Decimal(r.cost_cny) for r in rows)

    lines: list[str] = []
    lines.append("# Judge: resume_generate Rubric 报告\n")
    lines.append(f"样本量:{n} 条 | Judge model: qwen3.6-plus(thinking on)\n")
    lines.append("## 总体均值\n")
    lines.append("| 指标 | 值 | EVAL_PLAN 阈值 |")
    lines.append("|------|----|---------------|")
    lines.append(f"| Judge 综合分均值 | {avg_total:.1f} | ≥ 75(初始) |")
    lines.append(f"| 事实一致性均值 | {avg_factual:.1f} | ≥ 85(初始) |")
    lines.append(f"| JD 对齐度均值 | {avg_jd:.1f} | — |")
    p10_total = sorted(r.total_score for r in rows)[max(0, n // 10 - 1)] if n >= 10 else min(r.total_score for r in rows)
    lines.append(f"| Judge 综合分 P10 | {p10_total:.1f} | ≥ 60(初始) |")
    lines.append(f"| Cache 命中数 | {cached_count}/{n} | — |")
    lines.append(f"| 总成本(¥) | {total_cost} | — |")

    labeled = [r for r in rows if r.human_label_bucket is not None]
    if labeled:
        machine = [r.bucket for r in labeled]
        human = [r.human_label_bucket for r in labeled if r.human_label_bucket is not None]
        kappa = cohen_kappa(machine, human)
        cm = confusion_matrix(machine, human)
        lines.append("\n## Judge 自身可靠性(vs human label,bucket = high/mid/low)\n")
        lines.append(f"- 标注样本量:{len(labeled)} 条")
        lines.append(f"- **Cohen's kappa: {kappa:.3f}**(EVAL_PLAN 阈值 ≥ 0.7)")
        lines.append("\n### Confusion matrix `(machine, human) → count`\n")
        lines.append("| machine \\\\ human | high | mid | low |")
        lines.append("|------------------|------|-----|-----|")
        for m_label in ("high", "mid", "low"):
            cells = " | ".join(str(cm.get((m_label, h), 0)) for h in ("high", "mid", "low"))
            lines.append(f"| {m_label} | {cells} |")

    lines.append("\n## Per-case\n")
    lines.append("| ID | total | bucket | factual | jd | cached | human |")
    lines.append("|----|-------|--------|---------|----|--------|-------|")
    for r in rows:
        lines.append(
            f"| {r.id} | {r.total_score:.1f} | {r.bucket} | "
            f"{r.dimension_scores['factual_consistency']} | "
            f"{r.dimension_scores['jd_alignment']} | {r.cached} | "
            f"{r.human_label_bucket or '—'} |"
        )
    return "\n".join(lines) + "\n"


def render_evidence_report(rows: list[EvidenceResultRow]) -> str:
    n = len(rows)
    supports_count = sum(1 for r in rows if r.supports)
    cached_count = sum(1 for r in rows if r.cached)
    total_cost = sum(Decimal(r.cost_cny) for r in rows)

    lines: list[str] = []
    lines.append("# Judge: match_analysis evidence_validity 报告\n")
    lines.append(f"样本量:{n} 条 | Judge model: qwen3.6-plus(thinking on)\n")
    lines.append("## 总体\n")
    lines.append(f"- supports=true 比例:{supports_count}/{n} = {supports_count / n:.2f}")
    lines.append(f"- Cache 命中数:{cached_count}/{n}")
    lines.append(f"- 总成本(¥):{total_cost}")

    labeled = [r for r in rows if r.human_supports is not None]
    if labeled:
        machine = [r.supports for r in labeled]
        human = [r.human_supports for r in labeled if r.human_supports is not None]
        kappa = cohen_kappa(machine, human)
        cm = confusion_matrix(machine, human)
        lines.append("\n## Judge 自身可靠性(vs human supports,binary)\n")
        lines.append(f"- 标注样本量:{len(labeled)} 条")
        lines.append(f"- **Cohen's kappa: {kappa:.3f}**(EVAL_PLAN 阈值 ≥ 0.7)")
        lines.append("\n### Confusion matrix `(machine, human) → count`\n")
        lines.append("| machine \\\\ human | True | False |")
        lines.append("|------------------|------|-------|")
        for m_label in (True, False):
            cells = " | ".join(str(cm.get((m_label, h), 0)) for h in (True, False))
            lines.append(f"| {m_label} | {cells} |")

    lines.append("\n## Per-case\n")
    lines.append("| ID | supports | human | cached | reason |")
    lines.append("|----|----------|-------|--------|--------|")
    for r in rows:
        reason = r.reason.replace("|", "\\|")
        lines.append(
            f"| {r.id} | {r.supports} | "
            f"{r.human_supports if r.human_supports is not None else '—'} | "
            f"{r.cached} | {reason} |"
        )
    return "\n".join(lines) + "\n"


# ---------- main ----------


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", required=True, choices=("resume_generate", "match_analysis"))
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()

    suite: Suite = args.suite
    judge = JudgeClient(get_llm_client())

    if suite == "resume_generate":
        cases_r = load_resume_dataset(args.dataset)
        if not cases_r:
            raise SystemExit("dataset is empty")
        rows_r = await run_resume_suite(cases_r, judge)
        write_results(rows_r, args.results)
        report = render_resume_report(rows_r)
    else:
        cases_e = load_evidence_dataset(args.dataset)
        if not cases_e:
            raise SystemExit("dataset is empty")
        rows_e = await run_evidence_suite(cases_e, judge)
        write_results(rows_e, args.results)
        report = render_evidence_report(rows_e)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")
    print(f"\nresults → {args.results}")
    print(f"report  → {args.report}")


if __name__ == "__main__":
    asyncio.run(main())
