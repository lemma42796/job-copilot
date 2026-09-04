# Interview Coach 评测

## 目标

验证 `InterviewCoachAgent` 的状态机分支、累计补答、退出条件、context pack、幻觉守门和恢复行为。它不重新评估 AnswerJudge label 质量。

## 资产

- Dataset:`evals/suites/interview_coach/dataset.flow_smoke.jsonl`
- Runner:`apps/api/scripts/eval_interview_coach.py`
- 最新报告(已入库):`evals/reports/interview-coach-flow-smoke-20260517-132154.md`

## 覆盖范围

10 条 fixture 覆盖:

- 好答案直接下一题或结束。
- coverage / fidelity / depth 纠偏。
- 补答后按累计答案重评。
- 连续无明显提升退出。
- 中途恢复。
- 长上下文压缩和 final summary。

`source_chunk_ids` 是 fixture 内证据 id,不是当前 dogfood 数据库的 `note_chunks.id`。

## 指标

- `branch_accuracy`:decision 是否走到人工期望 action。
- `remediation_target_accuracy`:纠偏是否命中期望缺口类型和 id。
- `cumulative_rejudge`:补答后是否使用累计答案。
- `loop_exit`:退出条件是否生效。
- `context_pack_pass`:关键上下文是否保留。
- `hallucination_guard`:是否避免引入 source chunks 外的新标准答案。
- `recovery_pass`:恢复后是否继续期望节点。

## 证据边界

评测使用 offline stubbed Judge,不访问 DB 或真实 LLM。结果只证明固定 harness 行为,不证明真实 Judge 准确率、真实模型稳定性或线上恢复能力。具体数字以最新报告为准。

## 运行

```bash
uv run python apps/api/scripts/eval_interview_coach.py
```

具体 CLI 参数以脚本 `--help` 为准。运行由用户明确触发。
