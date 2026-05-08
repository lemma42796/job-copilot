"""AnswerJudge 编排(M2)。

config(5-AGENT_DESIGN §2.1 / §2.2):
- model: qwen3.6-flash
- thinking: on(三层评分涉及 reasoning)
- temperature: 0.2
- prompt name/version: answer_judge v1.0
- tool: lookup_in_notes_global(§4.7,反假阳性强化)
"""

from __future__ import annotations

from jobcopilot_api.schemas.agents.answer_judge import (
    AnswerJudgeInput,
    AnswerJudgeOutput,
)

PROMPT_NAME = "answer_judge"
PROMPT_VERSION = "v1.0"


async def run(inp: AnswerJudgeInput) -> AnswerJudgeOutput:
    raise NotImplementedError("M2")
