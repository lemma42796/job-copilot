# interview_coach suite

本 suite 守 M2.1 `InterviewCoachAgent` 的流程行为,不是重新评 Judge label 质量。

## 当前内容

- `dataset.flow_smoke.jsonl`:10 条最小流程型 fixture,覆盖不纠偏、coverage 纠偏、fabricated 纠偏、depth 纠偏、多轮无提升退出、中途恢复、长上下文压缩和 finish summary。

## 评测目标

- `branch_accuracy`:decision node 是否走到人工期望 action。
- `remediation_target_accuracy`:纠偏是否命中人工标注的缺口类型和 id。
- `cumulative_rejudge_pass`:补答后 Judge 输入必须是累计答案。
- `loop_exit_pass`:达标 / 无明显提升 / 偏题 / token budget 等退出条件必须生效。
- `context_pack_pass`:context pack 保留当前题、source chunks、reference points、累计答案、unresolved gaps。
- `hallucination_guard_pass`:纠偏 prompt 不引入 source chunks 外的新标准答案来源。
- `recovery_pass`:刷新恢复后能继续同一节点。

## 说明

这些 fixture 先作为 harness 行为标签,后续再接 runner。字段沿用 `docs/6-EVAL_PLAN.md` §8.2,其中 `source_chunk_ids` 是 fixture 内证据 id,不是当前 dogfood DB id。
