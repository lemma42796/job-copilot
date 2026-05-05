"""ResumePlannerAgent — JD + chunks (+ optional gap hint) → ResumePlan。

M3 W7 新增:5 节点 graph 的 plan 节点(retrieve → **plan** → draft → review → revise)。

输入:JD + retrieve top-K=20 chunks + optional match hint + candidate
deterministic 字段(profile 顶层 + educations)。
输出:`ResumePlan`(章节计划 + 取舍清单 + 整体策略)。Drafter 据此组织行文,
不再让 drafter 自己又选材又写作。

Pure function: 不读 / 不写 DB。Service 层调度。

Tier=CHEAP 不开 thinking + timeout=60s。Planner 输出 ~500 token JSON,
不需要 STANDARD/PREMIUM(M3 dogfood 后再评估)。
"""

from __future__ import annotations

from typing import Any

from jobcopilot_api.infra.prompts import LoadedPrompt, render_user
from jobcopilot_api.llm.client import LLMClient, LLMResult
from jobcopilot_api.llm.tiers import Tier
from jobcopilot_api.models import Jd, ProfileChunk
from jobcopilot_api.schemas.resumes import ResumePlan

FEATURE = "resume_plan"
RELATED_ENTITY = "resume"

DEFAULT_TIMEOUT_S = 60.0


def _jd_input(jd: Jd) -> dict[str, Any]:
    """Same flatten as drafter — keep the two prompts seeing the same JD shape."""
    return {
        "company": jd.company,
        "title": jd.title,
        "hard_skills": list(jd.hard_skills) if jd.hard_skills else [],
        "soft_skills": list(jd.soft_skills) if jd.soft_skills else [],
        "bonus_skills": list(jd.bonus_skills) if jd.bonus_skills else [],
        "responsibilities": list(jd.responsibilities) if jd.responsibilities else [],
        "years_required": jd.years_required,
        "education": jd.education,
        "job_level": jd.job_level,
    }


def _chunk_inputs(chunks: list[ProfileChunk]) -> list[dict[str, Any]]:
    return [
        {
            "id": c.id,
            "granularity": c.granularity,
            "content": c.content,
        }
        for c in chunks
    ]


async def plan_resume(
    *,
    jd: Jd,
    chunks: list[ProfileChunk],
    hint: str | None,
    candidate: dict[str, Any] | None,
    prompt: LoadedPrompt,
    llm: LLMClient,
    user_id: int | None = None,
    trace_id: str | None = None,
    related_id: int | None = None,
    timeout_s: float | None = DEFAULT_TIMEOUT_S,
) -> LLMResult:
    """Plan section-level emphasis. `result.parsed` is a `ResumePlan`."""
    user_text = render_user(
        prompt.user_template,
        jd=_jd_input(jd),
        chunks=_chunk_inputs(chunks),
        hint=hint,
        candidate=candidate or {},
    )
    return await llm.complete(
        feature=FEATURE,
        tier=Tier.CHEAP,
        system=prompt.system,
        user=user_text,
        response_schema=ResumePlan,
        cache_system=True,
        timeout_s=timeout_s,
        user_id=user_id,
        trace_id=trace_id,
        related_entity=RELATED_ENTITY,
        related_id=related_id,
        prompt_version_id=prompt.id,
    )
