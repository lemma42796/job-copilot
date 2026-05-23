# JobCopilot Evals(v2)

LLM 应用评测套件:检索 / 出题 / Judge / Agent 状态机 / JD 覆盖分析。所有 suite 在 `suites/` 下。

## 当前 suites

| suite | 评什么 | 状态 |
|-------|--------|------|
| `hybrid_search/` | 主题 query 到 chunks / notes 的召回质量 | 已有 smoke fixture + note-level dataset + note smoke 脚本 |
| `quiz_generator/` | QuizGenerator 出题质量(题目是否基于 chunks / 反幻觉) | 待 M2 建 |
| `answer_judge/` | AnswerJudge 评分跟人工 ground truth 的 Cohen's kappa(≥ 0.7) | 待 M2 建 |
| `interview_coach/` | InterviewCoachAgent 是否走到人工期望分支 | 已有最小流程型 fixture |
| `jd_coverage/` | JD 要求对用户知识库的覆盖分类与证据排序 | 最小指标脚本已接,待补人工标签 |
| `jd_aggregator/` | JD 同义合并与频次重算 | 暂缓,不作为 M2.5 当前 DoD |

## hybrid_search smoke

当前 `hybrid_search/` 先做轻量 smoke,不冒充正式 baseline:

- `notes_fixture/`:15 篇固定小 fixture,用于后续可回归的最小检索样本。
- `dataset.note_smoke.jsonl`:12 条全库 note-level 标签,用于当前 119 篇 dogfood 全库 smoke。
- `apps/api/scripts/eval_hybrid_search_note_smoke.py`:跑全库 note-level smoke,输出 top notes / hard negative intrusion / zero-hit / 成本;报告写入 `evals/reports/`(gitignore)。
- `expected_chunk_ids` 暂留空;第一版先看 `expected_note_paths` / `hard_negative_note_paths` / `expected_zero_hit`。

当前第一轮结果:12 cases 通过 6/12,非 zero-hit note micro recall 60.0%,zero-hit 0/2。下一步补 chunk/anchor 级报告与标签,再沉淀正式 `dataset.jsonl` / `eval_hybrid_search.py`。

## interview_coach flow smoke

当前 `interview_coach/` 先沉淀 M2.1 状态机行为标签:

- `dataset.flow_smoke.jsonl`:10 条流程型 fixture,覆盖不纠偏、coverage 纠偏、fabricated 纠偏、depth 纠偏、多轮无提升退出、中途恢复、长上下文压缩和 finish summary。
- 暂未接 runner;后续脚本应只验证 harness 分支、context pack 和事件/状态落库,不重新评价 Judge label 质量。

## jd_coverage metrics

当前 `jd_coverage/` 是 M2.5 的最小指标脚本,不调 LLM,只读人工标签和 DB 里的 `jd_analyses.note_match_summary`:

- `dataset.jsonl`:本地手工标签,字段见 `evals/suites/jd_coverage/README.md`。
- `apps/api/scripts/eval_jd_coverage.py`:输出 `coverage_macro_f1`、`missing_recall`、`false_covered_rate`、`evidence_precision@k`、`evidence_recall@k`、`evidence_mrr@k`。
- 首批 dogfood:基于 `analysis#6` 的 10 条标签已跑通,报告 `evals/reports/jd-coverage-20260523-104233.md`;headline 为 macro F1 67.7%、missing recall 100.0%、false covered rate 0.0%、evidence P/R/MRR@5 为 77.5% / 100.0% / 87.5%。

## 一句话用法

```bash
uv run python apps/api/scripts/eval_hybrid_search_note_smoke.py

uv run python apps/api/scripts/eval_jd_coverage.py

uv run python -m jobcopilot_api.scripts.eval_answer_judge \
  --suite evals/suites/answer_judge \
  --prompt-version v1.0 \
  --output /tmp/results.json
```

## v1 历史

v1 用 promptfoo + TypeScript 做 jd_extract 评测(M1 S6),后期被 Python(`evals/kappa.py` / `evals/judge.py` / `scripts/judge_eval.py`)取代。
v1 评测套件已从 repo 删除,git history 留档。
v2 全程走 Python,不再引入 Node 工具链。
