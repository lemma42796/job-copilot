"""QuizGenerator IO schema(5-AGENT_DESIGN §3.1 / §3.2)。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class QuizGenChunkInput(BaseModel):
    """传给 quiz_generator 的 chunk DTO。

    service 层(M2 第 4 步)负责 RetrievedChunk(retrieval_pipeline 输出)
    → QuizGenChunkInput 的转换:id 是 NoteChunk DB id,渲染 USER 段时用
    [N] 替换显示,LLM 输出回来还是 [N],service 后处理把 [N] 还原成 DB id
    落 questions.source_chunk_ids。
    """

    id: int
    folder_path: list[str]
    heading_path: list[str]
    note_title: str
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
    query: str
    mode: Literal["topic", "job", "auto"] = "topic"
    chunks: list[QuizGenChunkInput]
    question_count: int


class QuizGenOutput(BaseModel):
    type_mix: TypeMix
    questions: list[GeneratedQuestion]
