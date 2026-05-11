"""AnswerJudge 编排(M2)。

config(5-AGENT_DESIGN §2.1 / §2.2):
- model: qwen3.6-flash
- thinking: on(三层评分涉及 reasoning)
- temperature: 0.2
- prompt name/version: answer_judge v1.0
"""

from __future__ import annotations

from jobcopilot_api.agents.answer_judge.prompts import (
    PROMPT_NAME,
    SYSTEM,
    render_user,
)
from jobcopilot_api.infra.llm import get_llm_client
from jobcopilot_api.llm.client import LLMClient, LLMResult
from jobcopilot_api.llm.tiers import Tier
from jobcopilot_api.schemas.agents.answer_judge import (
    AnswerJudgeInput,
    AnswerJudgeOutput,
)

TEMPERATURE = 0.2  # 5-AGENT §2.2


async def run(
    inp: AnswerJudgeInput,
    *,
    llm: LLMClient | None = None,
) -> LLMResult:
    """LLM Judge 三层 evidence。

    返回 LLMResult — `.parsed` 是 `AnswerJudgeOutput`(已 Pydantic 校验);
    service 层负责 semantic integrity、[N] → DB id 映射、Python 算分与落库。
    """
    client = llm or get_llm_client()
    user = render_user(
        question=inp.question,
        chunks=inp.chunks,
        user_answer=inp.user_answer,
    )
    return await client.complete(
        feature=PROMPT_NAME,
        tier=Tier.STANDARD,
        system=SYSTEM,
        user=user,
        response_schema=AnswerJudgeOutput,
        temperature=TEMPERATURE,
    )
