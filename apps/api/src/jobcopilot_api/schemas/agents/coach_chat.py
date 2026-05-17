"""Coach chat IO schema for M2.1 interview remediation follow-up."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from jobcopilot_api.schemas.agents.quiz_generator import (
    GeneratedQuestion,
    QuizGenChunkInput,
)


class CoachChatInput(BaseModel):
    question: GeneratedQuestion
    chunks: list[QuizGenChunkInput]
    cumulative_answer: str
    coach_question: str
    prior_coach_message: str | None = None
    remediation_prompt: dict[str, Any] | None = None
    unresolved_gaps: list[dict[str, Any]] = Field(default_factory=list)
    scores: dict[str, float | None] | None = None


class CoachChatOutput(BaseModel):
    coach_message: str = Field(min_length=1, max_length=1200)


__all__ = ["CoachChatInput", "CoachChatOutput"]
