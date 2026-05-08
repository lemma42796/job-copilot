"""QuizGenerator 编排(M1)。

config(5-AGENT_DESIGN §2.1 / §2.2):
- model: qwen3.6-flash
- thinking: off
- temperature: 0.3
- prompt name/version: quiz_generator v1.0
"""

from __future__ import annotations

from jobcopilot_api.schemas.agents.quiz_generator import QuizGenInput, QuizGenOutput

PROMPT_NAME = "quiz_generator"
PROMPT_VERSION = "v1.0"


async def run(inp: QuizGenInput) -> QuizGenOutput:
    raise NotImplementedError("M1")
