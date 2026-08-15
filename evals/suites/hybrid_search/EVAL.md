# Hybrid Search 评测

## 目标

验证主题 query 经过 QueryRewriter、hybrid + weighted RRF、provider rerank challenger、deterministic governance / blend 后,能否保留直接证据、抑制 hard negative,并正确处理 zero-hit。

本 suite 是固定 12 条 smoke,不是通用 RAG benchmark。

## 资产

- Dataset:`evals/suites/hybrid_search/dataset.note_smoke.jsonl`
- 固定语料:`evals/suites/hybrid_search/notes_fixture/`
- Runner:`apps/api/scripts/eval_hybrid_search_note_smoke.py`
- 最近保存报告:`evals/reports/hybrid-search-note-smoke-20260516-100624.md`
- Trace:`evals/reports/hybrid-search-note-smoke-20260516-100624.trace.jsonl`

## Dataset 关键字段

- `direct_evidence_chunk_ids`:能直接回答 query 的证据。
- `necessary_context_chunk_ids`:补齐前提或边界但不单独回答 query 的上下文。
- `expected_note_paths` / `expected_heading_paths`:note / heading 级诊断。
- `hard_negative_note_paths`:语义接近但不应进入最终 context 的干扰项。
- `evidence_anchors`:应在命中内容中出现的关键事实。
- `expected_zero_hit`:该 query 是否应该被 0 命中守门拒绝。
- `risk_tags`:样本覆盖的风险类型。

## 指标

| 指标 | 作用 | 目标 |
|------|------|------|
| `candidate_recall@15` | 粗排紧窗口是否保留直接证据 | ≥ 70% |
| `selected_recall@10` | rerank + governance 后是否保留直接证据 | ≥ 90% |
| `mrr@10` | 直接证据排序位置 | 诊断 |
| `final_context_recall` | 最终上下文直接证据召回 | 诊断 |
| `final_context_precision` | 最终上下文是否干净 | ≥ 70% |
| `zero_hit_precision` | 空主题 / 近邻干扰是否被正确拒绝 | ≥ 90% |
| `hard_negative_intrusion` | hard negative 是否进入最终 context | 越低越好 |

## 最近可信快照

2026-05-16 报告,配置为 provider blend、rerank input 50、selected top-k 8、parent-doc off、query embedding cache-only:

- 旧 note-level pass rule:12/12。
- `candidate_recall@15`:91.67%。
- `selected_recall@10`:90.00%。
- `mrr@10`:68.33%。
- `final_context_recall`:90.00%。
- `final_context_precision`:41.75%,**低于 70% 目标**。
- zero-hit:2/2,100%。
- hard-negative intrusion:0/12。

因此不能只引用“12/12 通过”声称检索质量全部达标;当前明确缺口是 final context 不够干净。

## 运行

```bash
uv run python apps/api/scripts/eval_hybrid_search_note_smoke.py
```

具体 CLI 参数以脚本 `--help` 为准。运行由用户明确触发。
