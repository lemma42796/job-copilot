# resume_generate 评测(S21 子任务 4-C harness / 4-D 数据集)

LLM-as-Judge 评简历定制端到端产出物的 6 维 Rubric(EVAL_PLAN §7.3):
JD 对齐 30% / 事实一致 30% / 结构 15% / 量化 10% / 语言 10% / 长度 5%。Judge
走 `qwen3.6-plus`(thinking on),被评的 drafter 走 `qwen3.6-flash` —
**评委 ≠ 被评者**避免自评偏高(EVAL_PLAN §6.3)。

## 数据来源(4-D 实施)

25 条 `(jd, profile, resume_markdown)`,其中 15 条与 `match_analysis` 共用
JD/profile。来源 = **multi-persona synthetic**(无真实用户阶段标准做法,
EVAL_PLAN §6.1):8-10 个 personas(应届 / 后端转 AI / 前端中年 / quant 海归 /
PM 跨行 / 算法转 infra 等)入 fixture,每个 persona × 公开脱敏 JD 笛卡尔积,
跑真实 resume_graph 出简历,人工或 Judge 给 bucket label。

## 数据集格式

每行 JSON 对象,字段:

| 字段 | 必填 | 说明 |
|------|------|------|
| `id` | 是 | case id,跨次重跑稳定 |
| `jd_text` | 是 | JD 原文(含公司 / 职位 / 职责) |
| `profile_summary` | 是 | candidate profile 关键字段汇总 — 用于 Judge 做事实一致性核查 |
| `resume_markdown` | 是 | 待评简历 markdown |
| `human_label_bucket` | 否 | `high` / `mid` / `low` 三档,人工评分;有则算 Cohen's kappa |

`profile_summary` 怎么填:把 candidate profile 里**所有信息**(姓名 / 求职意向 /
educations / experiences / projects / skills 各 chunk content)拼成一段,Judge
做"事实一致性"维度时直接读这段,而不是去 retrieve(retrieval 召回不全的洞反
而会让 Judge 误判 fabrication)。

## 跑法

```bash
uv run python apps/api/scripts/judge_eval.py \
    --suite resume_generate \
    --dataset evals/suites/resume_generate/dataset.jsonl \
    --results evals/reports/judge-resume-$(date +%Y-%m-%d).results.jsonl \
    --report  evals/reports/judge-resume-$(date +%Y-%m-%d).md
```

环境:`JOBCOPILOT_DASHSCOPE_API_KEY` 已配。Judge 走 `Tier.PREMIUM`
(qwen3.6-plus thinking on);相同 dataset 重跑命中 4-B response cache,
`cached=true / cost_cny=0`。

## 阈值(EVAL_PLAN §7.5)

| 指标 | 初始 | GA |
|------|------|-----|
| Judge 综合分均值 | ≥ 75 | ≥ 82 |
| Judge 综合分 P10 | ≥ 60 | ≥ 70 |
| 事实一致性维度均值 | ≥ 85 | ≥ 92 |
| Judge 自身 kappa(若有 human label) | ≥ 0.7 | ≥ 0.8 |
