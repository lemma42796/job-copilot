"""JdParser IO schema(docs/TECH_DESIGN.md + SQLAlchemy models / Pydantic schemas)。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class JdParseInput(BaseModel):
    raw_text: str


class JdParseOutput(BaseModel):
    title: str
    responsibilities: list[str] = Field(default_factory=list)
    hard_skills: list[str] = Field(default_factory=list)
    soft_skills: list[str] = Field(default_factory=list)
    experience_years: str | None = None
    education: str | None = None
    extras: dict[str, Any] = Field(default_factory=dict)
