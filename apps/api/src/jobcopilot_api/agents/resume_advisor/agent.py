"""ResumeAdvisor 编排(M3)— JD 通用要求 vs 简历段落,两方锚点严格。

config(docs/TECH_DESIGN.md):
- model: qwen3.8-flash
- thinking: on(综合 JD 通用要求 + 简历段落判断)
- temperature: 0.2
- prompt name/version: resume_advisor v1.0
- 永不输出改写文案(forbidden_patterns 后处理校验)
- DoD: anchored_count / (anchored_count + unanchored_count) ≥ 0.7
"""

from __future__ import annotations

from jobcopilot_api.schemas.agents.resume_advisor import (
    ResumeAdvisorInput,
    ResumeAdvisorOutput,
)

PROMPT_NAME = "resume_advisor"
PROMPT_VERSION = "v1.0"


async def run(inp: ResumeAdvisorInput) -> ResumeAdvisorOutput:
    raise NotImplementedError("M3")
