"""JdAggregator 编排(M2.5)— 三阶段流水线 + 学习路径生成。

流水线(5-AGENT_DESIGN §6.2,单次 ≤ 200 条 JD):
1. 分批 reduce(LLM,thinking off,temperature 0.3)
2. 二次 reduce / merge(LLM)
3. Python 重算频次(frequency.py)
4. 学习路径生成(LLM,temperature 0.5)

总 LLM 调用 ≈ 12,P95 ≤ 60s。
"""

from __future__ import annotations

from jobcopilot_api.schemas.agents.jd_aggregator import (
    JdAggregateInput,
    JdAggregateOutput,
)

PROMPT_NAME_BATCH = "jd_aggregator_batch"
PROMPT_NAME_MERGE = "jd_aggregator_merge"
PROMPT_NAME_PATH = "jd_aggregator_learning_path"
PROMPT_VERSION = "v1.0"


async def run(inp: JdAggregateInput) -> JdAggregateOutput:
    raise NotImplementedError("M2.5")
