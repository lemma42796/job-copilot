"""简历入库 + 段落 chunker + 诊断编排 service(M3)。

职责:
- POST /api/resumes:文本入库 → 段落 chunker(按 heading 切 parsed_chunks)
- POST /api/resume-analyses(SSE):读 resume + jd_analysis → 调 resume_advisor → 锚点 / forbidden_pattern 后处理 → 落 resume_analyses + anchored_ratio
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from jobcopilot_api.schemas.resume import ResumeAnalysisCreateIn, ResumeOut


async def upload_resume(session: AsyncSession, raw_text: str, title: str) -> ResumeOut:
    raise NotImplementedError("M3")


async def get_resume(session: AsyncSession, resume_id: int) -> ResumeOut:
    raise NotImplementedError("M3")


async def start_diagnosis_sse(
    session: AsyncSession, payload: ResumeAnalysisCreateIn
) -> AsyncIterator[dict]:
    raise NotImplementedError("M3")
