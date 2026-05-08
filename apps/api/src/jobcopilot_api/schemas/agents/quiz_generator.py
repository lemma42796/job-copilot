"""QuizGenerator IO schema(5-AGENT_DESIGN §3.1 / §3.2)。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class NoteChunkRef(BaseModel):
    """传给 LLM 的 chunk 摘要 — 只含算分需要的字段。"""

    id: int
    folder_path: list[str]
    heading_path: list[str]
    level: int
    content: str


class ReferencePoint(BaseModel):
    """questions.reference_points JSONB(3-DATA_MODEL §6.1)。"""

    id: str  # "p1", "p2"...
    text: str
    weight: float
    evidence_chunk_ids: list[int]


class TypeMix(BaseModel):
    open_ended: int
    definition: int
    rationale: str


class GeneratedQuestion(BaseModel):
    type: Literal["open_ended", "definition"]
    prompt: str
    source_chunk_ids: list[int]
    reference_answer: str
    reference_chunk_ids: list[int]
    reference_points: list[ReferencePoint]


class QuizGenInput(BaseModel):
    node_folder_path: list[str]
    node_heading_path: list[str] = Field(default_factory=list)
    chunks: list[NoteChunkRef]
    question_count: int


class QuizGenOutput(BaseModel):
    type_mix: TypeMix
    questions: list[GeneratedQuestion]
