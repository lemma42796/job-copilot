# JD Coverage 评测

## 目标

衡量 JD requirement 对用户知识库的覆盖分类和证据排序。脚本不调用 LLM,读取人工标签和 `jd_analyses.note_match_summary`。

## 资产

- Dataset:`evals/suites/jd_coverage/dataset.jsonl`
- Runner:`apps/api/scripts/eval_jd_coverage.py`
- 最新报告(已入库):`evals/reports/jd-coverage-20260523-104233.md`

## Label schema

```json
{
  "id": "cov_001",
  "analysis_id": 6,
  "req_id": "req_3",
  "expected_status": "partial",
  "expected_evidence_chunk_ids": [9012, 9018],
  "notes": "人工判断说明"
}
```

`expected_status` 只能是 `covered / partial / missing / unknown`。`expected_evidence_chunk_ids` 保存真正能支撑覆盖判断的 `note_chunks.id`;`missing / unknown` 可以为空。

## 指标

- `coverage_macro_f1`
- `missing_recall`
- `false_covered_rate`
- `evidence_precision@k`
- `evidence_recall@k`
- `evidence_mrr@k`

`coverage_accuracy` 只作诊断,不作为 headline。

## 证据边界

标签只覆盖 `analysis#6` 的 10 条人工判断(covered 6 / partial 2 / missing 2),只支撑该次分析的最小覆盖证据,不能外推任意 JD 或知识库。具体数字以最新报告为准。

## 运行

```bash
uv run python apps/api/scripts/eval_jd_coverage.py
```

默认报告写入 `evals/reports/jd-coverage-<timestamp>.md`;运行由用户明确触发。
