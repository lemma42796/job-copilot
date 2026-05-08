"""JdAggregator IO schema(5-AGENT_DESIGN §6.1 + 3-DATA_MODEL §6.7)。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from jobcopilot_api.schemas.agents.jd_parser import JdParseOutput


RequirementCategory = Literal["硬技能", "软技能", "经验", "学历"]


class Requirement(BaseModel):
    id: str  # "req_1"
    canonical_text: str
    category: RequirementCategory
    raw_phrases: list[str]
    supporting_jd_ids: list[int]
    frequency: float  # Python 重算 — len(supporting_jd_ids) / len(parsed_jds)


class JdAggregateInput(BaseModel):
    parsed_jds: list[JdParseOutput]


class JdAggregateOutput(BaseModel):
    aggregated_requirements: list[Requirement]
    learning_path_md: str
