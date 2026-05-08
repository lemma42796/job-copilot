"""答题会话 REST + SSE IO schema(M2,4-API_SPEC §4)。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class QuizSessionCreateIn(BaseModel):
    folder_path: list[str]
    heading_path: list[str] | None = None
    question_count: int


class AnswerDraftIn(BaseModel):
    user_answer: str


class QuizSessionOut(BaseModel):
    id: int
    folder_path: list[str]
    heading_path: list[str] | None
    question_count: int
    status: Literal["in_progress", "submitted", "abandoned"]
    started_at: datetime
    submitted_at: datetime | None
    total_score: float | None


class QuestionOut(BaseModel):
    id: int
    order_index: int
    type: Literal["open_ended", "definition"]
    prompt: str
    source_chunk_ids: list[int]


class SessionAnswerOut(BaseModel):
    id: int
    order_index: int
    question_id: int
    user_answer: str | None
    coverage_score: float | None
    fidelity_score: float | None
    depth_score: float | None
    total_score: float | None
