"""CoachChat agent: answer follow-up questions without invoking AnswerJudge."""

from __future__ import annotations

from jobcopilot_api.agents.coach_chat.prompts import (
    PROMPT_NAME,
    SYSTEM,
    render_cache_fallback_user,
    render_task,
)
from jobcopilot_api.agents.context_cache import build_chunk_cache_messages
from jobcopilot_api.infra.llm import get_llm_client
from jobcopilot_api.llm.client import LLMClient, LLMResult
from jobcopilot_api.llm.tiers import Tier
from jobcopilot_api.schemas.agents.coach_chat import (
    CoachChatInput,
    CoachChatOutput,
)

TEMPERATURE = 0.2
MAX_TOKENS = 900


async def run(
    inp: CoachChatInput,
    *,
    llm: LLMClient | None = None,
    user_id: int | None = None,
) -> LLMResult:
    client = llm or get_llm_client()
    messages = [
        *build_chunk_cache_messages(inp.chunks),
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": render_task(inp)},
    ]
    return await client.complete(
        feature=PROMPT_NAME,
        tier=Tier.CHEAP,
        system=SYSTEM,
        user=render_cache_fallback_user(inp),
        messages=messages,
        response_schema=CoachChatOutput,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
        user_id=user_id,
    )
