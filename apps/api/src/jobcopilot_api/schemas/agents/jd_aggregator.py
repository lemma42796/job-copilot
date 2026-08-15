"""JdAggregator IO schema(docs/TECH_DESIGN.md + SQLAlchemy models / Pydantic schemas)。"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from jobcopilot_api.schemas.agents.jd_parser import JdParseOutput


RequirementCategory = Literal["职责", "硬技能", "软技能", "经验", "学历"]


class ParsedJdForAggregation(BaseModel):
    jd_id: int
    parsed: JdParseOutput
    raw_text: str = ""


class RawRequirementItem(BaseModel):
    jd_id: int
    category: RequirementCategory
    text: str


class RequirementCandidate(BaseModel):
    canonical_text: str
    category: RequirementCategory
    raw_phrases: list[str] = Field(default_factory=list)
    supporting_jd_ids: list[int] = Field(default_factory=list)


class Requirement(BaseModel):
    id: str  # "req_1"
    canonical_text: str
    category: RequirementCategory
    raw_phrases: list[str]
    supporting_jd_ids: list[int]
    frequency: float  # Python 重算 — len(supporting_jd_ids) / len(parsed_jds)


class JdAggregateInput(BaseModel):
    parsed_jds: list[ParsedJdForAggregation]


class JdRequirementReduceOutput(BaseModel):
    requirements: list[RequirementCandidate] = Field(default_factory=list)


class JdLearningPathOutput(BaseModel):
    learning_path_md: str


class JdAggregateOutput(BaseModel):
    aggregated_requirements: list[Requirement]
    learning_path_md: str
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_cost_cny: Decimal = Decimal("0")
    cache_hit_rate: Decimal | None = None
