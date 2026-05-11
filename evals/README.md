# JobCopilot Evals(v2)

LLM 应用评测套件:检索 / 出题 / Judge / Agent 状态机 / JD 聚合 / 简历诊断。所有 suite 在 `suites/` 下。

## 当前 suites

| suite | 评什么 | 状态 |
|-------|--------|------|
| `hybrid_search/` | 主题 query 到 chunks 的召回质量 | 待 M2 建 |
| `quiz_generator/` | QuizGenerator 出题质量(题目是否基于 chunks / 反幻觉) | 待 M2 建 |
| `answer_judge/` | AnswerJudge 评分跟人工 ground truth 的 Cohen's kappa(≥ 0.7) | 待 M2 建 |
| `interview_coach/` | InterviewCoachAgent 是否走到人工期望分支 | 待 M2.1 建 |
| `jd_aggregator/` | JD 同义合并与频次重算 | 待 M2.5 建 |
| `resume_advisor/` | 简历诊断锚点正确率 + forbidden 文案拦截 | 待 M3 建 |

## 一句话用法

```bash
uv run python -m jobcopilot_api.scripts.eval_answer_judge \
  --suite evals/suites/answer_judge \
  --prompt-version v1.0 \
  --output /tmp/results.json
```

## v1 历史

v1 用 promptfoo + TypeScript 做 jd_extract 评测(M1 S6),后期被 Python(`evals/kappa.py` / `evals/judge.py` / `scripts/judge_eval.py`)取代。
v1 评测套件已从 repo 删除,git history 留档。
v2 全程走 Python,不再引入 Node 工具链。
