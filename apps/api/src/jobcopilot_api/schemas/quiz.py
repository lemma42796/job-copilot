"""答题会话 REST + SSE IO schema(M2,4-API_SPEC §4)。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

QuizMode = Literal["topic", "job", "auto"]


class QuizSessionCreateIn(BaseModel):
    """POST /api/quiz/sessions 请求 body(4-API_SPEC §4.1)。

    M2 仅 mode=topic 可用;router 层把 mode=job/auto 拦截成
    422 mode_not_implemented(M3 启用)。query 长度上限 200(超限 422
    query_too_long)— 422 错误码由 Pydantic 校验生成,router 不重判。
    """

    query: str = ""
    mode: QuizMode = "topic"
    jd_ids: list[int] | None = None
    question_count: int = Field(ge=1, le=5)


class AnswerDraftIn(BaseModel):
    user_answer: str


class AnswerTurnSubmitIn(BaseModel):
    text: str
    turn_type: Literal["initial", "remediation"] = "initial"
    client_turn_id: str | None = None


class QuizSessionOut(BaseModel):
    id: int
    query: str
    mode: QuizMode
    jd_ids: list[int] | None = None
    status: Literal["in_progress", "submitted", "abandoned"]
    started_at: datetime
    submitted_at: datetime | None = None
    total_score: float | None = None


class QuestionReadyOut(BaseModel):
    """SSE event=question_ready 的 data payload(4-API_SPEC §4.1)。

    答题阶段前端拿不到 reference_answer / scoring_points(active recall
    强约束,防作弊)— 见 4-API_SPEC §4.2 的"重要"备注。
    """

    order_index: int
    question: "QuestionPublic"


class QuestionPublic(BaseModel):
    id: int
    type: Literal["open_ended", "definition"]
    prompt: str
    evidence_chunk_ids: list[int]


# 关闭循环引用
QuestionReadyOut.model_rebuild()


class SessionAnswerOut(BaseModel):
    id: int
    order_index: int
    question_id: int
    user_answer: str | None
    coverage_score: float | None
    fidelity_score: float | None
    depth_score: float | None
    total_score: float | None


class QuizScoresOut(BaseModel):
    coverage: float | None = None
    fidelity: float | None = None
    depth: float | None = None
    total: float | None = None


class QuizQuestionDetailOut(BaseModel):
    order_index: int
    question: QuestionPublic
    user_answer: str | None = None
    answer_turns: list[dict] = Field(default_factory=list)
    answer_submitted_at: datetime | None = None
    judged: bool = False
    scores: QuizScoresOut | None = None
    evidence: dict | None = None
    remediation_state: dict | None = None
    next_action: str | None = None
    remediation_prompt: dict | None = None
    coach_message: str | None = None
    reference_answer: str | None = None
    scoring_points: list[dict] | None = None


class QuizSessionDetailOut(BaseModel):
    id: int
    query: str
    mode: QuizMode
    jd_ids: list[int] | None = None
    status: Literal["in_progress", "submitted", "abandoned"]
    agent_state: dict | None = None
    started_at: datetime
    submitted_at: datetime | None = None
    abandoned_at: datetime | None = None
    scores: QuizScoresOut | None = None
    recall_md_path: str | None = None
    questions: list[QuizQuestionDetailOut]


class QuizSessionListItemOut(BaseModel):
    id: int
    query: str
    mode: QuizMode
    status: Literal["in_progress", "submitted", "abandoned"]
    started_at: datetime
    submitted_at: datetime | None = None
    total_score: float | None = None
    question_count: int


class QuizSessionListOut(BaseModel):
    items: list[QuizSessionListItemOut]
    next_cursor: int | None = None
    has_more: bool = False
