# Interview Coach Flow Smoke

- dataset: `evals/suites/interview_coach/dataset.flow_smoke.jsonl`
- generated_at: `2026-05-17T13:21:54.061715+00:00`
- mode: offline stubbed Judge; no DB / LLM calls

## Metrics

| metric | passed | total | rate |
|--------|--------|-------|------|
| branch_accuracy | 29 | 29 | 1.000 |
| remediation_target_accuracy | 9 | 9 | 1.000 |
| cumulative_rejudge | 4 | 4 | 1.000 |
| loop_exit | 2 | 2 | 1.000 |
| context_pack_pass | 10 | 10 | 1.000 |
| hallucination_guard | 7 | 7 | 1.000 |
| recovery_pass | 1 | 1 | 1.000 |

## Cases

| fixture | category | status | final | failures |
|---------|----------|--------|-------|----------|
| `coach_good_001` | good_answer | PASS | finish/finish | - |
| `coach_good_002` | good_answer | PASS | ask_next/ask_next | - |
| `coach_coverage_001` | coverage_remediation | PASS | ask_next/ask_next | - |
| `coach_coverage_002` | coverage_remediation | PASS | finish/finish | - |
| `coach_fabricated_001` | fabricated_remediation | PASS | ask_next/ask_next | - |
| `coach_fabricated_002` | fabricated_remediation | PASS | finish/finish | - |
| `coach_depth_001` | depth_remediation | PASS | ask_next/ask_next | - |
| `coach_loop_exit_001` | loop_exit_no_improvement | PASS | finish/finish | - |
| `coach_recovery_001` | recovery | PASS | ask_next/ask_next | - |
| `coach_context_compaction_001` | context_compaction | PASS | finish/finish | - |

## Decision Details

### coach_good_001

| step | expected | actual | trigger | exit | status |
|------|----------|--------|---------|------|--------|
| turn[1] | summarize | summarize | none/none | target_reached | PASS |

### coach_good_002

| step | expected | actual | trigger | exit | status |
|------|----------|--------|---------|------|--------|
| turn[1] | ask_next | ask_next | none/none | target_reached | PASS |

### coach_coverage_001

| step | expected | actual | trigger | exit | status |
|------|----------|--------|---------|------|--------|
| turn[1] | remediate | remediate | coverage/coverage | None | PASS |
| turn[3] | ask_next | ask_next | none/none | target_reached | PASS |

### coach_coverage_002

| step | expected | actual | trigger | exit | status |
|------|----------|--------|---------|------|--------|
| turn[1] | remediate | remediate | coverage/coverage | None | PASS |
| turn[3] | summarize | summarize | none/none | target_reached | PASS |

### coach_fabricated_001

| step | expected | actual | trigger | exit | status |
|------|----------|--------|---------|------|--------|
| turn[1] | remediate | remediate | fidelity/fidelity | None | PASS |
| turn[3] | ask_next | ask_next | none/none | target_reached | PASS |

### coach_fabricated_002

| step | expected | actual | trigger | exit | status |
|------|----------|--------|---------|------|--------|
| turn[1] | remediate | remediate | fidelity/fidelity | None | PASS |
| turn[3] | summarize | summarize | none/none | target_reached | PASS |

### coach_depth_001

| step | expected | actual | trigger | exit | status |
|------|----------|--------|---------|------|--------|
| turn[1] | remediate | remediate | depth/depth | None | PASS |
| turn[3] | ask_next | ask_next | none/none | target_reached | PASS |

### coach_loop_exit_001

| step | expected | actual | trigger | exit | status |
|------|----------|--------|---------|------|--------|
| turn[1] | remediate | remediate | coverage/coverage | None | PASS |
| turn[3] | remediate | remediate | coverage/coverage | None | PASS |
| turn[5] | summarize | summarize | coverage | no_meaningful_improvement/no_meaningful_improvement | PASS |

### coach_recovery_001

| step | expected | actual | trigger | exit | status |
|------|----------|--------|---------|------|--------|
| turn[3] | ask_next | ask_next | none/none | target_reached | PASS |
- recovery: wait_user_answer/wait_user_answer PASS

### coach_context_compaction_001

| step | expected | actual | trigger | exit | status |
|------|----------|--------|---------|------|--------|
| turn[1] | remediate | remediate | coverage/coverage | None | PASS |
| turn[3] | remediate | remediate | coverage/coverage | None | PASS |
| turn[5] | summarize | summarize | coverage | token_budget/token_budget | PASS |
