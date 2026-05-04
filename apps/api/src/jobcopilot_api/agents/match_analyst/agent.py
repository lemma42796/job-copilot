"""MatchAnalystAgent — JD + profile chunks → MatchResult。AGENT_DESIGN §6。

Pure function: 不读 / 不写 DB,不做 IO。Service 层负责 retrieve(检索 chunks)
+ persist(写 matches 行)。本模块只把 JD 和 chunks 拼成 prompt input、
调 LLMClient,拿到 LLMResult.parsed = MatchResult 之后返回。

Tier=CHEAP: qwen3.6-flash 不带 thinking_mode。MVP 偏离 AGENT_DESIGN §6.2
("STANDARD 起");S14 dogfood 实测 STANDARD(thinking)+ 10 chunks + 多段
输出在 30s 默认 timeout 下打满 3 次重试都过不去。M2 评测扎根阶段再决定
是否升 STANDARD/PREMIUM 换质量,届时也要相应放宽 timeout。

Default timeout 60s: 比 CHEAP tier 默认 30s 翻倍,留余量给大 prompt
(JD + chunks + 评分规则)生成结构化输出的耗时。
"""

from __future__ import annotations

from typing import Any

from jobcopilot_api.infra.prompts import LoadedPrompt, render_user
from jobcopilot_api.llm.client import LLMClient, LLMResult
from jobcopilot_api.llm.tiers import Tier
from jobcopilot_api.models import Jd, ProfileChunk
from jobcopilot_api.schemas.matches import MatchResult

FEATURE = "match_analyze"
RELATED_ENTITY = "match"


def _jd_input(jd: Jd) -> dict[str, Any]:
    """Flatten the JD ORM row into the dict shape the prompt expects.

    `hard_skills` / `responsibilities` 在 ORM 上是 JSONB 容器,直接序列
    化即可;LLM 只读这些字段,没必要走 JDStructured pydantic 一道。"""
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
    """Map ProfileChunk rows → dicts the prompt iterates over.

    `id` 必须是真实 chunk_id(LLM 要在 evidence_chunk_ids 引用它,service
    层会用 LLM 收到的 id set 校验非法引用)。"""
    return [
        {
            "id": c.id,
            "granularity": c.granularity,
            "content": c.content,
        }
        for c in chunks
    ]


DEFAULT_TIMEOUT_S = 60.0


async def analyze_match(
    *,
    jd: Jd,
    chunks: list[ProfileChunk],
    prompt: LoadedPrompt,
    llm: LLMClient,
    user_id: int | None = None,
    trace_id: str | None = None,
    related_id: int | None = None,
    timeout_s: float | None = DEFAULT_TIMEOUT_S,
) -> LLMResult:
    """Run match analysis. `result.parsed` is a `MatchResult` on success.

    Service 层在拿到结果后,要再做一道业务校验:剔除 evidence_chunk_ids
    中不在 input chunks 集合里的 id(AGENT_DESIGN §6.6)。LLM-client 层
    只兜 schema 合法,业务一致性在外面卡。"""
    user_text = render_user(
        prompt.user_template,
        jd=_jd_input(jd),
        chunks=_chunk_inputs(chunks),
    )
    return await llm.complete(
        feature=FEATURE,
        tier=Tier.CHEAP,
        system=prompt.system,
        user=user_text,
        response_schema=MatchResult,
        cache_system=True,
        timeout_s=timeout_s,
        user_id=user_id,
        trace_id=trace_id,
        related_entity=RELATED_ENTITY,
        related_id=related_id,
        prompt_version_id=prompt.id,
    )
