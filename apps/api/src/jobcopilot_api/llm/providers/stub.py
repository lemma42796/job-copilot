"""StubProvider — 压测专用假上游(P8)。

铁律(docs/TASKS.md「压测 stub provider」):压测零真实模型调用、零真实
余额消耗,上游在压测中是**常量不是变量**:

- 固定延迟 `settings.stub_latency_s`
- 固定并发上限 `settings.stub_max_concurrency`(进程级信号量;超限排队
  等待,不报错、不 429)
- 永远返回能通过对应 agent schema 校验的合法响应

按 `ProviderRequest.feature` 分发预构响应,覆盖压测链路会走到的四个
feature:query_rewriter / quiz_generator / answer_judge / coach_chat;
其余 feature 返回通用 JSON,若对应 agent schema 不兼容会在解析层失败
(压测不覆盖 JD 链路)。熔断 / 429 路径的验证不归压测 —— 单测里用
`DummyProvider.queue_error()` 构造。

token 数按内容长度折算(÷4,近似 4 字符 / token),成本走真实计价
公式、扣模拟余额,让记账链路保持满负载。与队列驱动的 `DummyProvider`
(耗尽即 raise,单测专用)不同,本 provider 可无限响应。
"""

from __future__ import annotations

import asyncio
import json
import re

from jobcopilot_api.llm.client import (
    OnTokenCallback,
    Provider,
    ProviderRequest,
    ProviderResponse,
)
from jobcopilot_api.settings import settings

_question_count_re = re.compile(r"要求出 (\d+) 道题")
_query_prefix = "用户 query:"

_COACH_CHAT_CONTENT = json.dumps(
    {"coach_message": "stub 教练回复:压测模式不评内容。"}, ensure_ascii=False
)

_GENERIC_CONTENT = json.dumps(
    {"coach_message": "stub:未知 feature 的通用响应。"}, ensure_ascii=False
)

_ANSWER_JUDGE_CONTENT = json.dumps(
    {
        "coverage_evidence": {
            "points": [
                {"id": "p1", "label": "partial", "user_excerpt": None},
                {"id": "p2", "label": "partial", "user_excerpt": None},
            ],
            "score_raw": 0.5,
            "reasoning": "stub:压测模式不做真实覆盖评估。",
        },
        "fidelity_evidence": {
            "claims": [
                {
                    "text": "stub 论断:压测答案。",
                    "label": "inferred",
                    "supporting_chunk_ids": [1],
                }
            ],
            "score_raw": 0.5,
            "reasoning": "stub:压测模式不做真实忠实性评估。",
        },
        "depth_evidence": {
            "dimensions": {
                "tradeoff": {"covered": False, "excerpt": None},
                "why": {"covered": False, "excerpt": None},
                "boundary": {"covered": False, "excerpt": None},
            },
            "score_raw": 0.0,
            "reasoning": "stub:压测模式不做真实深度评估。",
        },
        "coach_message": "stub 教练消息:压测模式不评内容。",
    },
    ensure_ascii=False,
)

_slot: asyncio.Semaphore | None = None


def _upstream_slot() -> asyncio.Semaphore:
    """进程级信号量模拟上游固定并发上限 U;超限排队,不报错。"""
    global _slot
    if _slot is None:
        _slot = asyncio.Semaphore(settings.stub_max_concurrency)
    return _slot


async def stub_upstream_call() -> None:
    """占用一个上游并发位并模拟固定延迟。rerank / embedding 链路复用。"""
    async with _upstream_slot():
        await asyncio.sleep(settings.stub_latency_s)


def stub_answer_judge_content() -> str:
    """answer_judge tool 循环(不经 Provider 协议)的预构响应。"""
    return _ANSWER_JUDGE_CONTENT


def _tokens(*texts: str) -> int:
    return max(1, sum(len(t) for t in texts) // 4)


def _query_rewriter_response(request: ProviderRequest) -> str:
    # user 模板固定为 "用户 query:{user_query}"(services/query_rewriter.py)。
    query = request.user
    if query.startswith(_query_prefix):
        query = query[len(_query_prefix) :].strip()
    return json.dumps(
        {
            "intent": "topic_interview",
            "core_entities": [],
            "must_keep_terms": [],
            "weighted_queries": [
                {"query": query, "role": "original", "weight": 1.0}
            ],
            "expanded_queries": [query],
            "rationale": "stub:原样返回,不扩展。",
        },
        ensure_ascii=False,
    )


def _quiz_generator_response(request: ProviderRequest) -> str:
    # render_task 末尾固定含 "要求出 {question_count} 道题"。
    match = _question_count_re.search(request.user)
    count = int(match.group(1)) if match else 2
    questions = []
    for i in range(count):
        questions.append(
            {
                "type": "open_ended",
                "prompt": f"stub 题 {i + 1}:压测模式下不做真实出题。",
                "reference_answer": "stub 参考答案 [1]",
                "scoring_points": [
                    {
                        "id": "p1",
                        "text": "stub 采分点一",
                        "weight": 0.5,
                        "supporting_chunk_ids": [1],
                    },
                    {
                        "id": "p2",
                        "text": "stub 采分点二",
                        "weight": 0.5,
                        "supporting_chunk_ids": [1],
                    },
                ],
            }
        )
    return json.dumps(
        {
            "type_mix": {
                "open_ended": count,
                "definition": 0,
                "rationale": "stub:压测固定全 open_ended。",
            },
            "questions": questions,
        },
        ensure_ascii=False,
    )


def _content_for(request: ProviderRequest) -> str:
    if request.feature == "query_rewriter":
        return _query_rewriter_response(request)
    if request.feature == "quiz_generator":
        return _quiz_generator_response(request)
    if request.feature == "answer_judge":
        return _ANSWER_JUDGE_CONTENT
    if request.feature == "coach_chat":
        return _COACH_CHAT_CONTENT
    return _GENERIC_CONTENT


class StubProvider(Provider):
    """可无限响应的压测假上游。挂到 BaseLLMClient 后,闸门 / 记账 /
    缓存 / 重试等真实链路全部照常工作,只有上游调用被替换。"""

    async def complete(
        self,
        request: ProviderRequest,
        *,
        on_token: OnTokenCallback | None = None,
    ) -> ProviderResponse:
        await stub_upstream_call()
        content = _content_for(request)
        if on_token is not None:
            await on_token(content)
        return ProviderResponse(
            content=content,
            tokens_in=_tokens(request.system, request.user),
            tokens_out=_tokens(content),
            cached_tokens=0,
            cache_creation_input_tokens=0,
        )
