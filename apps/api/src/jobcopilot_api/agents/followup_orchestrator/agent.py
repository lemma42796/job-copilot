"""FollowupOrchestrator 编排(M3)— LangGraph state 机。

config(docs/TECH_DESIGN.md):
- model: qwen3.8-flash
- thinking: on(多轮 reasoning 必需)
- temperature: 0.2
- 触发条件: coverage_score < 60 AND ≥1 个 depth 维度 covered=false

Nodes(§8 草图):
  generate_question → wait_user_answer → judge_layer1
     → branch
         ├ generate_followup → wait_user_answer → judge_layer2 → score_aggregate
         └ score_aggregate(单轮)
"""

from __future__ import annotations

from jobcopilot_api.schemas.agents.followup_orchestrator import FollowupState


async def run(state: FollowupState) -> FollowupState:
    raise NotImplementedError("M3")
