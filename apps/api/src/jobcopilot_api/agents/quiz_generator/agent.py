"""QuizGenerator 编排(M2)。

config(docs/TECH_DESIGN.md):
- model: qwen3.6-flash(Tier.CHEAP)
- thinking: off(出题靠 chunks 内容重组,不需复杂推理)
- temperature: 0.3(降随机性,要稳定结构)
- prompt name/version: quiz_generator v1.3(详见 prompts.py)

agent 是薄壳:渲染 USER 段 → 调 LLMClient(BaseLLMClient 内置 retry / cache /
schema 校验 / cost 计算)→ 返回 LLMResult。

`result.parsed` 是 `QuizGenOutput` 实例(LLM 输出经 Pydantic 校验);
service 层(M2 第 4 步)从 reference_answer / scoring_points 派生引用,
再做 [N] → DB id 映射 + 完整性校验
(reference_answer_chunk_ids ⊆ evidence_chunk_ids / weight 之和 ≈ 1.0 等)+ 落库
+ 写 questions.gen_model / gen_prompt_version / gen_tokens_* / gen_cost_cny
audit 字段(从 LLMResult 抽)。
"""

from __future__ import annotations

from jobcopilot_api.agents.quiz_generator.prompts import (
    PROMPT_NAME,
    SYSTEM,
    render_cache_fallback_user,
    render_task,
    render_user,
)
from jobcopilot_api.agents.context_cache import build_chunk_cache_messages
from jobcopilot_api.infra.llm import get_llm_client
from jobcopilot_api.llm.client import LLMClient, LLMResult
from jobcopilot_api.llm.tiers import Tier
from jobcopilot_api.schemas.agents.quiz_generator import (
    QuizGenInput,
    QuizGenOutput,
)

TEMPERATURE = 0.3  # docs/TECH_DESIGN.md


async def run(
    inp: QuizGenInput,
    *,
    llm: LLMClient | None = None,
) -> LLMResult:
    """LLM 出题。

    返回 LLMResult — `.parsed` 是 `QuizGenOutput`(已 Pydantic 校验);
    `.tokens_in / tokens_out / cost_cny / model` 喂 service 层落 audit。
    """
    client = llm or get_llm_client()
    user = render_user(
        query=inp.query,
        chunks=inp.chunks,
        question_count=inp.question_count,
    )
    messages = [
        *build_chunk_cache_messages(inp.chunks),
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": render_task(
                query=inp.query,
                question_count=inp.question_count,
            ),
        },
    ]
    return await client.complete(
        feature=PROMPT_NAME,
        tier=Tier.CHEAP,
        system=SYSTEM,
        user=render_cache_fallback_user(
            query=inp.query,
            chunks=inp.chunks,
            question_count=inp.question_count,
        )
        if llm is None
        else user,
        messages=messages,
        response_schema=QuizGenOutput,
        temperature=TEMPERATURE,
    )
