"""JdParser 编排(M2.5)— JD 上传时立即调一次。

config(docs/TECH_DESIGN.md):
- model: qwen3.8-flash
- thinking: off
- temperature: 0.3
- prompt name/version: jd_parser v1.0
"""

from __future__ import annotations

from jobcopilot_api.agents.jd_parser.prompts import (
    PROMPT_NAME,
    SYSTEM,
    render_user,
)
from jobcopilot_api.infra.llm import get_llm_client
from jobcopilot_api.llm.client import LLMClient, LLMResult
from jobcopilot_api.llm.tiers import Tier
from jobcopilot_api.schemas.agents.jd_parser import JdParseInput, JdParseOutput

TEMPERATURE = 0.3  # docs/TECH_DESIGN.md


async def run(
    inp: JdParseInput,
    *,
    llm: LLMClient | None = None,
) -> LLMResult:
    """Parse a single JD into `JdParseOutput`.

    返回 LLMResult — `.parsed` 是 `JdParseOutput`;service 层负责 title
    兜底、audit 字段落库和事务提交。
    """
    client = llm or get_llm_client()
    return await client.complete(
        feature=PROMPT_NAME,
        tier=Tier.CHEAP,
        system=SYSTEM,
        user=render_user(inp.raw_text),
        response_schema=JdParseOutput,
        temperature=TEMPERATURE,
    )
