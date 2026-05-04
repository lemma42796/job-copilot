"""ResumeReviewerAgent — markdown 草稿 + chunks → ResumeReview。
AGENT_DESIGN §7.3.4。

Pure function: 不读 / 不写 DB。Service 层在拿到结果后:
- `passed=True` → resumes.status='ready'
- `passed=False`(任意 high severity) → resumes.status='review_failed';
  markdown 仍存,前端展示警告条让用户自决(MVP 不 revise,留 M3)

Tier=CHEAP 不开 thinking + timeout=60s:同 drafter 偏离原因(STATUS.md D7
对 reviewer 本就是 CHEAP/flash)。
"""

from __future__ import annotations

from typing import Any

from jobcopilot_api.infra.prompts import LoadedPrompt, render_user
from jobcopilot_api.llm.client import LLMClient, LLMResult
from jobcopilot_api.llm.tiers import Tier
from jobcopilot_api.models import ProfileChunk
from jobcopilot_api.schemas.resumes import ResumeReview

FEATURE = "resume_review"
RELATED_ENTITY = "resume"

DEFAULT_TIMEOUT_S = 60.0


def _chunk_inputs(chunks: list[ProfileChunk]) -> list[dict[str, Any]]:
    return [
        {
            "id": c.id,
            "granularity": c.granularity,
            "content": c.content,
        }
        for c in chunks
    ]


async def review_resume(
    *,
    draft_markdown: str,
    chunks: list[ProfileChunk],
    prompt: LoadedPrompt,
    llm: LLMClient,
    user_id: int | None = None,
    trace_id: str | None = None,
    related_id: int | None = None,
    timeout_s: float | None = DEFAULT_TIMEOUT_S,
) -> LLMResult:
    """Run resume fact-check. `result.parsed` is a `ResumeReview`."""
    user_text = render_user(
        prompt.user_template,
        chunks=_chunk_inputs(chunks),
        draft_markdown=draft_markdown,
    )
    return await llm.complete(
        feature=FEATURE,
        tier=Tier.CHEAP,
        system=prompt.system,
        user=user_text,
        response_schema=ResumeReview,
        cache_system=True,
        timeout_s=timeout_s,
        user_id=user_id,
        trace_id=trace_id,
        related_entity=RELATED_ENTITY,
        related_id=related_id,
        prompt_version_id=prompt.id,
    )
