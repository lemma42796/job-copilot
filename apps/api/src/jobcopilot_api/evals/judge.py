"""JudgeClient(S21 子任务 4-C)— 调 LLMClient 跑 Rubric / evidence 评测。

模型选择:Judge 永远走 `Tier.PREMIUM`(qwen3.6-plus thinking on),被评的
agent 是 qwen3.6-flash;**评委 ≠ 被评者**避免自评偏高 5-10pp(EVAL_PLAN
§6.3)。

Temperature 注:EVAL_PLAN 写 Judge 应当用 0.2,但当前 `LLMClient.complete`
接口未暴露 temperature(走 DashScope SDK 默认),只能用 prompt 端"严格按
锚点打分"约束逼近低 temperature 行为;真要硬控 temperature 需要扩 LLMClient
契约,本切片不做。

Cache 友好:Judge 走非流式 + 固定 prompt + 同 dataset 重跑场景多 — `BaseLLMClient`
的 4-B cache layer 直接命中,评测重跑成本接近 0(命中态 cost_cny=0)。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from jobcopilot_api.evals.judge_prompts import (
    MATCH_EVIDENCE_SYSTEM,
    RESUME_RUBRIC_SYSTEM,
    JudgeEvidenceValidity,
    JudgeResumeRubric,
    render_match_evidence_user,
    render_resume_rubric_user,
    weighted_total,
)
from jobcopilot_api.llm.client import LLMClient
from jobcopilot_api.llm.tiers import Tier


@dataclass(frozen=True)
class JudgeRubricResult:
    rubric: JudgeResumeRubric
    total_score: float
    cost_cny: Decimal
    latency_ms: int
    cached: bool


@dataclass(frozen=True)
class JudgeEvidenceResult:
    verdict: JudgeEvidenceValidity
    cost_cny: Decimal
    latency_ms: int
    cached: bool


class JudgeClient:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def judge_resume_rubric(
        self,
        *,
        jd_text: str,
        profile_summary: str,
        resume_markdown: str,
        trace_id: str | None = None,
    ) -> JudgeRubricResult:
        result = await self._llm.complete(
            feature="judge_resume_rubric",
            tier=Tier.PREMIUM,
            system=RESUME_RUBRIC_SYSTEM,
            user=render_resume_rubric_user(
                jd_text=jd_text,
                profile_summary=profile_summary,
                resume_markdown=resume_markdown,
            ),
            response_schema=JudgeResumeRubric,
            trace_id=trace_id,
        )
        # response_schema 走通后 parsed 一定非 None;LLMClient 失败会 raise
        # `LLMSchemaInvalidError`,让评测脚本看见而非 silent。
        assert isinstance(result.parsed, JudgeResumeRubric)
        rubric = result.parsed
        return JudgeRubricResult(
            rubric=rubric,
            total_score=weighted_total(rubric),
            cost_cny=result.cost_cny,
            latency_ms=result.latency_ms,
            cached=result.cached,
        )

    async def judge_evidence(
        self,
        *,
        claim: str,
        chunk: str,
        trace_id: str | None = None,
    ) -> JudgeEvidenceResult:
        result = await self._llm.complete(
            feature="judge_evidence_validity",
            tier=Tier.PREMIUM,
            system=MATCH_EVIDENCE_SYSTEM,
            user=render_match_evidence_user(claim=claim, chunk=chunk),
            response_schema=JudgeEvidenceValidity,
            trace_id=trace_id,
        )
        assert isinstance(result.parsed, JudgeEvidenceValidity)
        return JudgeEvidenceResult(
            verdict=result.parsed,
            cost_cny=result.cost_cny,
            latency_ms=result.latency_ms,
            cached=result.cached,
        )
