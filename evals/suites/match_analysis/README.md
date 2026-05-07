# match_analysis 评测(S21 子任务 4-C harness / 4-D 数据集)

两层评测:

1. **整体匹配分**(EVAL_PLAN §6.2):`score_mae` ≤ 8 / `bucket_acc` ≥ 0.85 /
   `hit_skills_precision` ≥ 0.90 / `gap_skills_recall` ≥ 0.85。这一层用规则比
   对,**不**走 Judge。**4-C 不实现** — match_service 输出 ≠ Judge 评的对象,
   评测脚本本切片不做这层(留给 4-D 实施)。
2. **evidence_validity**(§6.3):每条 `(claim, chunk)` 配对让 Judge 判 chunk 是
   否真的支持 claim(supports y/n + reason)。**4-C 实现的就是这层**,目的是
   守门 match_analyst 的 RAG 引用质量(引错 chunk = 用户能看见的产品 bug,
   recall 优先)。

## 数据集格式(本 suite)

每行 JSON 对象,字段:

| 字段 | 必填 | 说明 |
|------|------|------|
| `id` | 是 | case id |
| `claim` | 是 | match_analyst 输出的一条 strength / gap 描述 |
| `chunk` | 是 | 该 claim 引用的简历 chunk 文本 |
| `human_supports` | 否 | `true` / `false`,有则算 Judge 与 human 的 binary kappa |

## 数据来源(4-D 实施)

30 条 `(claim, chunk)` 对,由 multi-persona synthetic JD/profile 跑 match_analyst
后从输出里抽:

- 20 条**真支持**(claim ↔ chunk 直接匹配,Judge 应判 supports=true)
- 10 条**故意错配**(claim 引向另一段不相关 chunk,Judge 应判 supports=false)

错配集种子见 W8 第二轮 dogfood 收集的对抗例(STATUS.md):#18 模糊能力陈述 /
#20 跨 chunk 业务 context 错配 / 凭空捏 "AWS"。

## 跑法

```bash
uv run python apps/api/scripts/judge_eval.py \
    --suite match_analysis \
    --dataset evals/suites/match_analysis/dataset.jsonl \
    --results evals/reports/judge-evidence-$(date +%Y-%m-%d).results.jsonl \
    --report  evals/reports/judge-evidence-$(date +%Y-%m-%d).md
```

## 阈值

| 指标 | 阈值 |
|------|------|
| Judge 与 human 的 binary Cohen's kappa | ≥ 0.7(EVAL_PLAN §6.3) |
| Judge 在错配组 supports=false 的 recall | ≥ 0.90 |
