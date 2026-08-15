"""AnswerJudge 总分计算 — Python SSoT(docs/TECH_DESIGN.md)。

LLM 不算分,Python 算分。权重常量 SSoT 在本文件:
- coverage 0.5 / fidelity 0.4 / depth 0.1
- fabricated > 30% 锁顶 50

阈值依据见 docs/TECH_DESIGN.md。dogfood 后调整只改 0.3 / 50.0 两常数,
不要 bump prompt(算分位置在 Python,不在 LLM)。
"""

from __future__ import annotations

from collections import Counter

from jobcopilot_api.schemas.agents.answer_judge import (
    CoverageEvidence,
    DepthEvidence,
    FidelityEvidence,
    ScoringPoint,
)


def coverage_score(evidence: CoverageEvidence, points: list[ScoringPoint]) -> float:
    """sum(weight * label_score) * 100,label_score = {hit:1.0, partial:0.5, miss:0.0}"""
    label_scores = {"hit": 1.0, "partial": 0.5, "miss": 0.0}
    by_id = {p.id: p for p in points}
    return (
        sum(by_id[e.id].weight * label_scores[e.label] for e in evidence.points) * 100
    )


def fidelity_score(evidence: FidelityEvidence) -> float:
    """(supported + 0.6 * inferred) / total * 100;fabricated > 30% 锁顶 50。"""
    n = len(evidence.claims)
    if n == 0:
        return 100.0
    counts = Counter(c.label for c in evidence.claims)
    raw = (counts["supported"] + 0.6 * counts["inferred"]) / n * 100
    if counts["fabricated"] / n > 0.3:
        raw = min(raw, 50.0)
    return raw


def depth_score(evidence: DepthEvidence) -> float:
    """命中维度数 / 3 * 100。"""
    covered = sum(1 for d in evidence.dimensions.values() if d.covered)
    return covered / 3 * 100


def total_score(coverage: float, fidelity: float, depth: float) -> float:
    """0.5 * Coverage + 0.4 * Fidelity + 0.1 * Depth。"""
    return 0.5 * coverage + 0.4 * fidelity + 0.1 * depth
