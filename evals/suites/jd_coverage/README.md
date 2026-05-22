# jd_coverage suite

M2.5 JD-to-Knowledge 覆盖分析的最小指标集。它不调 LLM,只读取:

- 人工标签:`evals/suites/jd_coverage/dataset.jsonl`
- 系统预测:`jd_analyses.note_match_summary`

## Label Schema

```json
{
  "id": "cov_001",
  "analysis_id": 12,
  "req_id": "req_3",
  "expected_status": "partial",
  "expected_evidence_chunk_ids": [9012, 9018],
  "notes": "Redis cluster note partially covers the requirement"
}
```

`expected_status` 只能是:

- `covered`
- `partial`
- `missing`
- `unknown`

`expected_evidence_chunk_ids` 填真正能支持覆盖判断的 `note_chunks.id`;`missing / unknown` 可为空。

## Metrics

- `coverage_macro_f1`
- `missing_recall`
- `false_covered_rate`
- `evidence_precision@k`
- `evidence_recall@k`
- `evidence_mrr@k`

`coverage_accuracy` 只作为诊断指标,不作为 headline。

## Run

```bash
uv run python apps/api/scripts/eval_jd_coverage.py
```

默认报告写入 `evals/reports/jd-coverage-<timestamp>.md`。
