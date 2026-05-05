"""ResumeDrafterAgent — JD + chunks (+ optional plan / hint / prev_findings)
→ markdown 简历。AGENT_DESIGN §7.3.3。

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

M3 W7 新增两参(plan / prev_findings):
- `plan` 来自 ResumePlannerAgent — 章节级取舍计划;drafter 据此组织行文,
  不再独自做选材决策。空时 prompt 内分支降级(等价旧 v1.0.3 行为)。
- `prev_findings` 由 graph 的 revise 节点透传 — reviewer 上一轮的 findings
  列表;drafter 必须针对每条具体修订(删除 / 换措辞 / 改数字),不能整
  篇重写否则白费上一轮信号。空时表示首次 draft(非 revise)。
"""

from __future__ import annotations

from typing import Any

from jobcopilot_api.infra.prompts import LoadedPrompt, render_user
from jobcopilot_api.llm.client import LLMClient, LLMResult, OnTokenCallback
from jobcopilot_api.llm.tiers import Tier
from jobcopilot_api.models import Jd, ProfileChunk
from jobcopilot_api.schemas.resumes import ResumePlan, ReviewFinding

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


def _plan_input(plan: ResumePlan | None) -> dict[str, Any] | None:
    """Serialize ResumePlan to the dict shape the prompt iterates over.
    None 时模板 `{% if plan %}` 分支跳过整段。"""
    if plan is None:
        return None
    return plan.model_dump(mode="json")


def _findings_input(findings: list[ReviewFinding] | None) -> list[dict[str, Any]] | None:
    """Serialize previous-round reviewer findings for the revise path.
    None 或空 list 时模板 `{% if prev_findings %}` 分支跳过整段(首次 draft)。"""
    if not findings:
        return None
    return [f.model_dump(mode="json") for f in findings]


async def draft_resume(
    *,
    jd: Jd,
    chunks: list[ProfileChunk],
    hint: str | None,
    prompt: LoadedPrompt,
    llm: LLMClient,
    candidate: dict[str, Any] | None = None,
    plan: ResumePlan | None = None,
    prev_findings: list[ReviewFinding] | None = None,
    user_id: int | None = None,
    trace_id: str | None = None,
    related_id: int | None = None,
    timeout_s: float | None = DEFAULT_TIMEOUT_S,
    on_token: OnTokenCallback | None = None,
) -> LLMResult:
    """Generate a markdown resume. `result.content` is the markdown text;
    `result.parsed` is None (no response_schema).

    `hint` 是可选的"历史匹配差距"段(由 service 层从 match.gap_summary +
    missing_skills 拼出,MVP 仅 match 触发链路有值)。空 hint 时 prompt
    不会渲染 hint 段。

    `candidate` 是 profile 表上**不在 chunks 里**的 deterministic 字段透传
    (drafter v1.0.3+):
    - `full_name` / `email` / `phone` / `location`:基本信息章节直接渲染,
      避免 LLM 写 "[待补充]" 占位符
    - `target_titles`(list[str]):求职意向章节第一来源,避免硬抄 JD title
    - `educations`(list[dict]):教育背景章节直接渲染,绕开 retrieve K=20
      的相似度召回(教育 chunks 可能被 LLM Agent 方向语义挤掉)

    `plan` 是 ResumePlannerAgent 的章节计划(M3 W7+);空时退化为 v1.0.3
    行为。

    `prev_findings` 是 graph 的 revise 节点透传的上一轮 reviewer findings;
    非空时模板渲染"修订指引"段,要求 drafter 针对性改、不要整篇重写。"""
    user_text = render_user(
        prompt.user_template,
        jd=_jd_input(jd),
        chunks=_chunk_inputs(chunks),
        hint=hint,
        candidate=candidate or {},
        plan=_plan_input(plan),
        prev_findings=_findings_input(prev_findings),
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
        on_token=on_token,
    )
