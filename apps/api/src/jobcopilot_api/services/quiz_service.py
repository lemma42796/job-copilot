"""出题编排 service(M2)。

职责:
- POST /api/quiz/sessions(SSE)入口
- 校验节点 chunks ≥ 5(否则 insufficient_chunks)
- 调 chunk_service 取 chunks → 调 quiz_generator agent → 落 questions / quiz_sessions
- 推 SSE 事件序列(started / progress / question / done)
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from jobcopilot_api.schemas.quiz import QuizSessionCreateIn


async def start_session_sse(
    session: AsyncSession, payload: QuizSessionCreateIn
) -> AsyncIterator[dict]:
    raise NotImplementedError("M2")


async def get_session(session: AsyncSession, session_id: int):
    raise NotImplementedError("M2")


async def list_sessions(
    session: AsyncSession, cursor: int | None, limit: int = 20
):
    raise NotImplementedError("M2")
