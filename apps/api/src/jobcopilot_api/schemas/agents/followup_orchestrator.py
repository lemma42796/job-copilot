"""FollowupOrchestrator state schema(5-AGENT_DESIGN §8)。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from jobcopilot_api.schemas.agents.answer_judge import AnswerJudgeOutput
from jobcopilot_api.schemas.agents.quiz_generator import GeneratedQuestion


class FollowupState(BaseModel):
    """LangGraph state(M3)。"""

    session_id: int
    current_question: GeneratedQuestion
    user_answers: list[str] = Field(default_factory=list)
    judge_evidences: list[AnswerJudgeOutput] = Field(default_factory=list)
    interviewer_followups: list[str] = Field(default_factory=list)
    final_score: float | None = None
