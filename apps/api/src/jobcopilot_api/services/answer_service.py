"""答题 + 评分编排 service(M2)。

职责:
- PUT 草稿落库(同步,无 LLM)
- POST submit:逐题调 answer_judge agent → Python 算分 → 落 session_answers + 累计 total_score → SSE 推 progress / score / done
- evidence 后处理(完整性校验、[N] → DB id 反向映射)
- 工具调用 trace 校验(§4.7 失败处理:fabricated 必须有 lookup 调用)
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from jobcopilot_api.schemas.quiz import AnswerDraftIn


async def save_draft(
    session: AsyncSession,
    session_id: int,
    order_index: int,
    payload: AnswerDraftIn,
) -> None:
    raise NotImplementedError("M2")


async def submit_session_sse(
    session: AsyncSession, session_id: int
) -> AsyncIterator[dict]:
    raise NotImplementedError("M2")


async def abandon_session(session: AsyncSession, session_id: int) -> None:
    raise NotImplementedError("M2")
