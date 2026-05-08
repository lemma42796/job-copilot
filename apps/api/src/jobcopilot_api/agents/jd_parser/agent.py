"""JdParser 编排(M2.5)— JD 上传时立即调一次。

config(5-AGENT_DESIGN §2.1 / §2.2):
- model: qwen3.6-flash
- thinking: off
- temperature: 0.3
- prompt name/version: jd_parser v1.0
"""

from __future__ import annotations

from jobcopilot_api.schemas.agents.jd_parser import JdParseInput, JdParseOutput

PROMPT_NAME = "jd_parser"
PROMPT_VERSION = "v1.0"


async def run(inp: JdParseInput) -> JdParseOutput:
    raise NotImplementedError("M2.5")
