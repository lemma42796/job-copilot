"""AnswerJudge IO schema(5-AGENT_DESIGN §4.1 / §4.2 + 3-DATA_MODEL §6.2-§6.4)。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from jobcopilot_api.schemas.agents.quiz_generator import (
    GeneratedQuestion,
    NoteChunkRef,
    ReferencePoint,
)


class CoveragePoint(BaseModel):
    id: str
    label: Literal["hit", "partial", "miss"]
    user_excerpt: str | None


class CoverageEvidence(BaseModel):
    points: list[CoveragePoint]
    score_raw: float
    reasoning: str


class FidelityClaim(BaseModel):
    text: str
    label: Literal["supported", "inferred", "fabricated"]
    chunk_ids: list[int]


class FidelityEvidence(BaseModel):
    claims: list[FidelityClaim]
    score_raw: float
    reasoning: str


class DepthDimension(BaseModel):
    covered: bool
    excerpt: str | None


class DepthEvidence(BaseModel):
    dimensions: dict[str, DepthDimension]  # keys: tradeoff / why / boundary
    score_raw: float
    reasoning: str


class AnswerJudgeInput(BaseModel):
    question: GeneratedQuestion
    chunks: list[NoteChunkRef]
    user_answer: str


class AnswerJudgeOutput(BaseModel):
    coverage_evidence: CoverageEvidence
    fidelity_evidence: FidelityEvidence
    depth_evidence: DepthEvidence


__all__ = [
    "AnswerJudgeInput",
    "AnswerJudgeOutput",
    "CoverageEvidence",
    "CoveragePoint",
    "DepthDimension",
    "DepthEvidence",
    "FidelityClaim",
    "FidelityEvidence",
    "ReferencePoint",
]
