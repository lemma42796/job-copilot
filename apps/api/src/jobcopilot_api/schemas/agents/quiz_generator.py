"""QuizGenerator IO schema(5-AGENT_DESIGN §3.1 / §3.2)。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class QuizGenChunkInput(BaseModel):
    """传给 quiz_generator 的 chunk DTO。

    service 层(M2 第 4 步)负责 RetrievedChunk(retrieval_pipeline 输出)
    → QuizGenChunkInput 的转换:id 是 NoteChunk DB id,渲染 USER 段时用
    [N] 替换显示。LLM 只输出这些 [N] 局部引用,service 后处理再还原成
    DB id 落 questions.source_chunk_ids / reference_chunk_ids / reference_points。
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


class GeneratedQuestionDraft(BaseModel):
    """QuizGenerator LLM 输出的单题草稿。

    LLM 只负责写题干、reference_answer 里的 [N] 引用,以及每个采分点
    的 evidence_chunk_ids。service 层再派生 source_chunk_ids /
    reference_chunk_ids,避免让模型维护多份引用真相。
    """

    type: Literal["open_ended", "definition"]
    prompt: str
    reference_answer: str
    reference_points: list[ReferencePoint]


class GeneratedQuestion(BaseModel):
    """service 派生后的本地 [N] 编号题目形态。

    AnswerJudge prompt 也复用这个 schema;在 DB 里这些 ids 会被映射成
    note_chunks.id。
    """

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
    questions: list[GeneratedQuestionDraft]
