"""ResumeDrafterAgent — JD + chunks (+ optional gap hint) → markdown 简历。
AGENT_DESIGN §7.3.3。

Pure function: 不读 / 不写 DB。Service 层负责 retrieve(检索 chunks)
+ persist(写 resumes / resume_versions)。

MVP 偏离 AGENT_DESIGN §7.3.3 的两点(S16 归档卡记录):

1. **chunks 在 user 段而非 system 段**:套 match_analyst 模板。§7.3.3 让
   chunks 进 system 是为了 cross-call cache 命中(同 profile 多次生成),
   但 retrieval 在每次 generate 拉的 K=20 chunks 不固定(查询依赖 JD),
   命中率低;放 user 段更直观,system 段保留稳定的"角色 + 风格 + 章节顺
   序",cache 命中目标更纯粹。
2. **不走 response_schema**:简历正文是 ~1000 字 markdown,JSON 包装会让
   LLM 把整段转义到字符串字段,代码块/换行/引号都易出错。直接 plain
   text 把 LLM `content` 当 markdown 收。

Tier=CHEAP 不开 thinking(偏离 STATUS.md D7):S14 dogfood 实测
STANDARD(thinking)+ 大 prompt(JD+10 chunks)在 30s timeout 下 3 次
重试都过不去;drafter 输入更大(JD+20 chunks),保守起见 MVP 都 CHEAP。
M3 GA 阶段评估升 STANDARD/PREMIUM 换质量,届时同步放宽 timeout。

Default timeout 90s: 比 CHEAP 默认 30s 的 3 倍,留余量给 ~3000 token markdown
输出。
"""

from __future__ import annotations

from typing import Any

from jobcopilot_api.infra.prompts import LoadedPrompt, render_user
from jobcopilot_api.llm.client import LLMClient, LLMResult
from jobcopilot_api.llm.tiers import Tier
from jobcopilot_api.models import Jd, ProfileChunk

FEATURE = "resume_draft"
RELATED_ENTITY = "resume"

DEFAULT_TIMEOUT_S = 90.0


def _jd_input(jd: Jd) -> dict[str, Any]:
    """Flatten JD ORM row into the dict shape the prompt expects.
    JSONB 容器原样序列化(同 match_analyst._jd_input)。"""
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


async def draft_resume(
    *,
    jd: Jd,
    chunks: list[ProfileChunk],
    hint: str | None,
    prompt: LoadedPrompt,
    llm: LLMClient,
    user_id: int | None = None,
    trace_id: str | None = None,
    related_id: int | None = None,
    timeout_s: float | None = DEFAULT_TIMEOUT_S,
) -> LLMResult:
    """Generate a markdown resume. `result.content` is the markdown text;
    `result.parsed` is None (no response_schema).

    `hint` 是可选的"历史匹配差距"段(由 service 层从 match.gap_summary +
    missing_skills 拼出,MVP 仅 match 触发链路有值)。空 hint 时 prompt
    不会渲染 hint 段。"""
    user_text = render_user(
        prompt.user_template,
        jd=_jd_input(jd),
        chunks=_chunk_inputs(chunks),
        hint=hint,
    )
    return await llm.complete(
        feature=FEATURE,
        tier=Tier.CHEAP,
        system=prompt.system,
        user=user_text,
        response_schema=None,
        cache_system=True,
        timeout_s=timeout_s,
        user_id=user_id,
        trace_id=trace_id,
        related_entity=RELATED_ENTITY,
        related_id=related_id,
        prompt_version_id=prompt.id,
    )
