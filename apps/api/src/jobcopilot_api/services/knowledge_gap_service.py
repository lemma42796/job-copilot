"""弱点跟踪 + Spaced Repetition(M3)。

职责:
- 评分完成时 upsert knowledge_gaps(answer_service 调用)
- SR next_review_at 计算(SM-2 lite:streak + last_score 推进 / 重置)
- GET /api/dashboard/gaps + GET /api/dashboard/today
- POST /api/quiz/sessions/from-review:从 due 队列拉题再出 session

SM-2 参数 SSoT 在本文件常量(MULTIPLIERS / MIN_INTERVAL_DAYS),
便于 dogfood 后调动作只改这里。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from jobcopilot_api.schemas.dashboard import GapOut, TodayBucket


async def upsert_gap_from_answer(
    session: AsyncSession,
    answer_id: int,
) -> None:
    raise NotImplementedError("M3")


async def list_gaps(
    session: AsyncSession,
    cursor: int | None,
    limit: int = 20,
) -> list[GapOut]:
    raise NotImplementedError("M3")


async def get_today_bucket(
    session: AsyncSession, now: datetime | None = None
) -> TodayBucket:
    raise NotImplementedError("M3")
