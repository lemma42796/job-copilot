"""Combine pending LLM-output jsonl + human evaluation yaml → dataset.jsonl rows.

Inputs:
  - pending jsonl (one row per sample, output of match_eval_batch.py)
  - judgments yaml: a list of human evaluations keyed by sample id

Output:
  - jsonl rows to append to evals/suites/match_analysis/dataset.jsonl

Computed metrics per row:
  - delta_score = abs(llm_score - human_score)
  - bucket_match = (llm_bucket == human_bucket)
  - hit_skills_precision = |llm_hit ∩ human_hit| / |llm_hit|   (1.0 if llm_hit empty)
  - gap_skills_recall    = |llm_gap ∩ human_gap| / |human_gap|  (1.0 if human_gap empty)

Bucket cuts: ≥80 high / 60-79 mid / <60 low (matches EVAL_PLAN §6.1 + judge_prompts)

Usage:
  uv run python apps/api/scripts/match_eval_finalize.py \\
    --pending /tmp/match-pending.jsonl \\
    --judgments /tmp/match-judgments.yaml \\
    --output /tmp/match-final-rows.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml


def _bucket(score: float) -> str:
    if score >= 80:
        return "high"
    if score >= 60:
        return "mid"
    return "low"


def _normalize_skill_set(items: list[str]) -> set[str]:
    """Lowercased + stripped, for set-overlap math."""
    return {s.strip().lower() for s in items if s and s.strip()}


def _precision(predicted: list[str], truth: list[str]) -> float:
    p = _normalize_skill_set(predicted)
    if not p:
        return 1.0  # vacuous — LLM said nothing, can't be wrong
    t = _normalize_skill_set(truth)
    return round(len(p & t) / len(p), 4)


def _recall(predicted: list[str], truth: list[str]) -> float:
    t = _normalize_skill_set(truth)
    if not t:
        return 1.0
    p = _normalize_skill_set(predicted)
    return round(len(p & t) / len(t), 4)


def main(pending_path: Path, judgments_path: Path, output_path: Path) -> None:
    pending: dict[str, dict] = {}
    for line in pending_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        pending[row["id"]] = row

    judgments_data = yaml.safe_load(judgments_path.read_text(encoding="utf-8"))
    judgments_list = judgments_data.get("judgments") or []
    judge_session = judgments_data.get(
        "judge_session", "claude-opus-4-7 in Claude Code session 2026-05-07"
    )

    out_rows: list[dict] = []
    for j in judgments_list:
        sid = j["id"]
        if sid not in pending:
            raise SystemExit(f"sample id {sid} not in pending jsonl")
        pend = pending[sid]
        llm = pend["llm_output"]
        llm_score = float(llm.get("score", 0))
        human_score = float(j["human_score"])
        human_bucket = j.get("human_bucket") or _bucket(human_score)
        llm_hit = [m["name"] for m in (llm.get("matched_skills") or [])]
        llm_gap = [m["name"] for m in (llm.get("missing_skills") or [])]
        human_hit = list(j.get("human_hit_skills") or [])
        human_gap = list(j.get("human_gap_skills") or [])

        out = {
            "id": sid,
            "jd_id": pend["jd_id"],
            "profile_id": pend["profile_id"],
            "persona_label": pend["persona_label"],
            "jd_text": pend["jd_text"],
            "profile_summary": pend["profile_summary"],
            "llm_output": llm,
            "human_score": human_score,
            "human_bucket": human_bucket,
            "human_hit_skills": human_hit,
            "human_gap_skills": human_gap,
            "delta_score": abs(llm_score - human_score),
            "bucket_match": _bucket(llm_score) == human_bucket,
            "hit_skills_precision": _precision(llm_hit, human_hit),
            "gap_skills_recall": _recall(llm_gap, human_gap),
            "key_finding": j["key_finding"],
            "judge_session": judge_session,
        }
        out_rows.append(out)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for r in out_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # Print quick summary
    n = len(out_rows)
    bm = sum(1 for r in out_rows if r["bucket_match"])
    avg_delta = sum(r["delta_score"] for r in out_rows) / n if n else 0
    avg_p = sum(r["hit_skills_precision"] for r in out_rows) / n if n else 0
    avg_r = sum(r["gap_skills_recall"] for r in out_rows) / n if n else 0
    by_bucket: dict[str, int] = {}
    for r in out_rows:
        by_bucket[r["human_bucket"]] = by_bucket.get(r["human_bucket"], 0) + 1
    print(f"wrote {n} rows to {output_path}")
    print(f"  bucket_match     = {bm}/{n} = {bm / n:.2%}")
    print(f"  avg delta_score  = {avg_delta:.2f}")
    print(f"  avg hit precision= {avg_p:.3f}")
    print(f"  avg gap recall   = {avg_r:.3f}")
    print(f"  human bucket dist= {by_bucket}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pending", required=True, type=Path)
    parser.add_argument("--judgments", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    main(args.pending.resolve(), args.judgments.resolve(), args.output.resolve())
