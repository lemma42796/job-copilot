# JobCopilot Evals(v2)

LLM-as-Judge 评测套件 + Cohen's kappa 守门。所有 suite 在 `suites/` 下。

## 当前 suites

| suite | 评什么 | 状态 |
|-------|--------|------|
| `quiz_generate/` | QuizGenerator 出题质量(题目是否基于 chunks / 反幻觉) | 待 M2 建 |
| `answer_judge/` | AnswerJudge 评分跟人工 ground truth 的 Cohen's kappa(≥ 0.7) | 待 M2 建 |

## 一句话用法

```bash
uv run python apps/api/scripts/judge_eval.py --suite answer_judge --dataset evals/suites/answer_judge/dataset.jsonl --output /tmp/results.jsonl
```

## v1 历史

v1 用 promptfoo + TypeScript 做 jd_extract 评测(M1 S6),后期被 Python(`evals/kappa.py` / `evals/judge.py` / `scripts/judge_eval.py`)取代。
v1 评测套件已从 repo 删除,git history 留档。
v2 全程走 Python,不再引入 Node 工具链。
