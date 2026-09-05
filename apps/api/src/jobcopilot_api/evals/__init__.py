"""LLM-as-Judge harness + Cohen's kappa(S21 子任务 4-C)。

模块边界:

- `kappa.py` — 纯算法,无外部依赖;`(rater_a, rater_b)` → κ。
- `judge_prompts.py` — Rubric prompt + Pydantic 输出 schema。
- `judge.py` — `JudgeClient`,封装 `LLMClient`(tier=PREMIUM,模型 = qwen3.8-flash
  thinking on)调用 Rubric prompt;agents 端不直接 import 此模块,只评测脚本用。

Judge 模型(plus)与被评模型(flash)分离 — 防"评委即被评者"自评偏高 5-10pp,
EVAL_PLAN §6.3 / §7.3 已记;此处实现固化模型选择。
"""
