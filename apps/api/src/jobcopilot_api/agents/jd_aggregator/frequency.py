"""JdAggregator 频次重算 — Python SSoT(5-AGENT_DESIGN §6.2 Stage 3)。

不信 LLM 算术,自己 group by canonical 数 supporting jd。落地时:

    for req in unified_canonicals:
        req.supporting_jd_ids = list(set(req.supporting_jd_ids))
        req.frequency = round(len(req.supporting_jd_ids) / len(parsed_jds), 3)
"""

from __future__ import annotations

from jobcopilot_api.schemas.agents.jd_aggregator import Requirement


def recompute_frequency(
    canonicals: list[Requirement], total_jds: int
) -> list[Requirement]:
    raise NotImplementedError("M2.5")
