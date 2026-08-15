"""Dashboard REST IO schema(M3,OpenAPI / Pydantic schemas)。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class GapOut(BaseModel):
    id: int
    topic: str
    folder_path: list[str]
    last_score: float
    last_judged_at: datetime
    next_review_at: datetime
    streak: int
    review_count: int


class TodayBucket(BaseModel):
    overdue: list[GapOut]
    due_today: list[GapOut]
    upcoming: list[GapOut]
