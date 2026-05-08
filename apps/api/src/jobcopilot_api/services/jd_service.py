"""JD 上传(立即解析)+ 一键分析编排 service(M2.5)。

职责:
- POST /api/jds:文本入库 → 立即调 jd_parser agent → 落 parsed_payload
- POST /api/jd-analyses(SSE):批量读 jds → 调 jd_aggregator(三阶段 + Python 重算频次 + 学习路径)→ 落 jd_analyses → SSE 进度
- 列表 / 详情 / patch / 软删
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from jobcopilot_api.schemas.jd import JdAnalysisCreateIn, JdCreateIn, JdOut, JdPatchIn


async def upload_jd(session: AsyncSession, payload: JdCreateIn) -> JdOut:
    raise NotImplementedError("M2.5")


async def list_jds(
    session: AsyncSession, cursor: int | None, limit: int = 20
):
    raise NotImplementedError("M2.5")


async def get_jd(session: AsyncSession, jd_id: int) -> JdOut:
    raise NotImplementedError("M2.5")


async def patch_jd(session: AsyncSession, jd_id: int, payload: JdPatchIn) -> JdOut:
    raise NotImplementedError("M2.5")


async def delete_jd(session: AsyncSession, jd_id: int) -> None:
    raise NotImplementedError("M2.5")


async def start_analysis_sse(
    session: AsyncSession, payload: JdAnalysisCreateIn
) -> AsyncIterator[dict]:
    raise NotImplementedError("M2.5")
