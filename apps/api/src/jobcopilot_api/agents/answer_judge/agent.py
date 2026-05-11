"""AnswerJudge 编排(M2)。

config(5-AGENT_DESIGN §2.1 / §2.2):
- model: qwen3.6-flash
- thinking: off(M2 dogfood:thinking on 在 AnswerJudge 长 prompt 下不收尾)
- temperature: 0.2
- prompt name/version: answer_judge v1.2
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import Any

from langfuse.openai import AsyncOpenAI
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobcopilot_api.agents.answer_judge.prompts import (
    PROMPT_NAME,
    SYSTEM,
    SYSTEM_WITH_LOOKUP_TOOL,
    render_cache_fallback_user,
    render_task,
    render_user,
)
from jobcopilot_api.agents.context_cache import build_chunk_cache_messages
from jobcopilot_api.infra.llm import get_llm_client
from jobcopilot_api.llm.client import LLMClient, LLMResult
from jobcopilot_api.llm.db_logger import DBCallLogger
from jobcopilot_api.llm.errors import (
    LLMAuthError,
    LLMSchemaInvalidError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from jobcopilot_api.llm.pricing import cost_for
from jobcopilot_api.llm.providers.dashscope import DASHSCOPE_BASE_URL
from jobcopilot_api.llm.tiers import Tier, tier_to_model
from jobcopilot_api.schemas.agents.answer_judge import (
    AnswerJudgeInput,
    AnswerJudgeOutput,
)
from jobcopilot_api.services.retrieval_pipeline import fetch_note_titles
from jobcopilot_api.services.search_service import global_hybrid_search
from jobcopilot_api.settings import settings

TEMPERATURE = 0.2  # 5-AGENT §2.2
TOOL_MAX_CALLS = 5
MAX_JUDGE_ROUNDS = TOOL_MAX_CALLS * 2 + 4
TOOL_REF_ID_START = 1000
TOOL_NAME_LOOKUP = "lookup_in_notes_global"
CLAIM_JACCARD_THRESHOLD = 0.45
CLAIM_CONTAINMENT_THRESHOLD = 0.70
_tool_client: AsyncOpenAI | None = None

LOOKUP_TOOL_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": TOOL_NAME_LOOKUP,
        "description": "在用户全部笔记库里检索某条 claim,返回 Top-K 相关 chunks 摘要。",
        "parameters": {
            "type": "object",
            "properties": {
                "claim": {
                    "type": "string",
                    "description": "需要验证是否存在于全库笔记中的用户答案声明。",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回 chunk 数,默认 3,最大 3。",
                    "minimum": 1,
                    "maximum": 3,
                },
            },
            "required": ["claim"],
            "additionalProperties": False,
        },
    },
}


@dataclass
class _ToolRunState:
    tool_call_count: int = 0
    lookup_deduped_count: int = 0
    next_ref_id: int = TOOL_REF_ID_START
    ref_to_chunk_id: dict[int, int] = field(default_factory=dict)
    lookup_claims: list[str] = field(default_factory=list)
    unverified_fabricated_claims: list[str] = field(default_factory=list)
    lookup_limit_reached: bool = False


@dataclass
class _UsageAccumulator:
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0
    cache_creation_input_tokens: int = 0
    content: str = ""


async def run(
    inp: AnswerJudgeInput,
    *,
    llm: LLMClient | None = None,
    sessionmaker: async_sessionmaker[AsyncSession] | None = None,
) -> LLMResult:
    """LLM Judge 三层 evidence。

    返回 LLMResult — `.parsed` 是 `AnswerJudgeOutput`(已 Pydantic 校验);
    service 层负责 semantic integrity、[N] → DB id 映射、Python 算分与落库。
    """
    if sessionmaker is not None and llm is None:
        return await _run_with_lookup_tool(inp, sessionmaker=sessionmaker)

    client = llm or get_llm_client()
    user = render_user(
        question=inp.question,
        chunks=inp.chunks,
        user_answer=inp.user_answer,
    )
    messages = [
        *build_chunk_cache_messages(inp.chunks),
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": render_task(
                question=inp.question,
                user_answer=inp.user_answer,
            ),
        },
    ]
    return await client.complete(
        feature=PROMPT_NAME,
        tier=Tier.CHEAP,
        system=SYSTEM,
        user=render_cache_fallback_user(
            question=inp.question,
            chunks=inp.chunks,
            user_answer=inp.user_answer,
        )
        if llm is None
        else user,
        messages=messages,
        response_schema=AnswerJudgeOutput,
        temperature=TEMPERATURE,
    )


async def _run_with_lookup_tool(
    inp: AnswerJudgeInput,
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> LLMResult:
    """AnswerJudge production path with `lookup_in_notes_global` tool use."""
    cfg = tier_to_model(Tier.CHEAP)
    client = _get_tool_client()
    logger = DBCallLogger(sessionmaker=sessionmaker)
    started = monotonic()
    usage = _UsageAccumulator()
    tool_state = _ToolRunState()
    parsed: AnswerJudgeOutput | None = None

    messages: list[dict[str, Any]] = [
        *build_chunk_cache_messages(inp.chunks),
        {"role": "system", "content": SYSTEM_WITH_LOOKUP_TOOL},
        {
            "role": "user",
            "content": _schema_instruction()
            + "\n\n"
            + render_task(
                question=inp.question,
                user_answer=inp.user_answer,
            ),
        },
    ]
    schema_retry_used = False
    lookup_retry_count = 0
    force_lookup_next = False

    try:
        for _round_idx in range(MAX_JUDGE_ROUNDS):
            tool_mode = (
                "forced"
                if force_lookup_next
                else "auto"
                if tool_state.tool_call_count < TOOL_MAX_CALLS
                else "none"
            )
            force_lookup_next = False
            resp = await _create_chat_completion(
                client=client,
                model=cfg.model,
                messages=messages,
                thinking_mode=cfg.thinking_mode,
                tool_mode=tool_mode,
            )
            _absorb_usage(usage, resp)

            message = resp.choices[0].message
            tool_calls = list(getattr(message, "tool_calls", None) or [])
            if tool_calls:
                messages.append(_assistant_tool_message(message, tool_calls))
                for tool_call in tool_calls:
                    result = await _handle_tool_call(
                        tool_call,
                        sessionmaker=sessionmaker,
                        state=tool_state,
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": str(tool_call.id),
                            "content": json.dumps(
                                result,
                                ensure_ascii=False,
                                default=str,
                            ),
                        }
                    )
                continue

            usage.content = message.content or ""
            try:
                parsed = _parse_answer_judge_output(usage.content)
                unverified_fabricated = _refresh_unverified_fabricated_claims(
                    parsed,
                    tool_state,
                )
                if (
                    unverified_fabricated
                    and lookup_retry_count < TOOL_MAX_CALLS
                    and tool_state.tool_call_count < TOOL_MAX_CALLS
                ):
                    lookup_retry_count += 1
                    force_lookup_next = True
                    messages.append({"role": "assistant", "content": usage.content})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "你的上一次输出仍包含尚未工具验证的 fabricated claim。"
                                "下一步必须调用 lookup_in_notes_global,从下列 claims 中选择一条 "
                                "作为 claim 参数。工具返回后再输出最终 JSON。\n"
                                f"{json.dumps(unverified_fabricated, ensure_ascii=False)}"
                            ),
                        }
                    )
                    parsed = None
                    continue
                break
            except (json.JSONDecodeError, PydanticValidationError) as exc:
                if schema_retry_used:
                    raise LLMSchemaInvalidError(
                        f"answer_judge schema validation failed after retry: {exc}"
                    ) from exc
                schema_retry_used = True
                messages.append({"role": "assistant", "content": usage.content})
                messages.append(
                    {
                        "role": "user",
                        "content": _schema_retry_prompt(usage.content),
                    }
                )

        if parsed is None:
            raise LLMSchemaInvalidError("answer_judge did not return final JSON")
        unverified_fabricated = _refresh_unverified_fabricated_claims(
            parsed,
            tool_state,
        )
        if unverified_fabricated and not tool_state.lookup_limit_reached:
            raise LLMSchemaInvalidError(
                "answer_judge still has unverified fabricated claims before "
                "lookup limit was reached"
            )

        result = _build_result(
            usage=usage,
            parsed=parsed,
            started=started,
            model=cfg.model,
            success=True,
            error_code=None,
            tool_state=tool_state,
        )
        await logger.log(result)
        return result
    except (
        LLMTimeoutError,
        LLMUpstreamError,
        LLMAuthError,
        LLMSchemaInvalidError,
    ) as exc:
        result = _build_result(
            usage=usage,
            parsed=None,
            started=started,
            model=cfg.model,
            success=False,
            error_code=_error_code_of(exc),
            tool_state=tool_state,
        )
        await logger.log(result)
        raise


def _get_tool_client() -> AsyncOpenAI:
    global _tool_client
    if _tool_client is None:
        if not settings.dashscope_api_key:
            raise LLMAuthError("AnswerJudge tool path requires DashScope API key")
        _tool_client = AsyncOpenAI(
            api_key=settings.dashscope_api_key,
            base_url=DASHSCOPE_BASE_URL,
        )
    return _tool_client


async def _create_chat_completion(
    *,
    client: AsyncOpenAI,
    model: str,
    messages: Sequence[dict[str, Any]],
    thinking_mode: bool,
    tool_mode: str,
) -> Any:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        "timeout": tier_to_model(Tier.CHEAP).default_timeout_s,
        "max_tokens": tier_to_model(Tier.CHEAP).default_max_tokens,
        "temperature": TEMPERATURE,
        "extra_body": {"enable_thinking": thinking_mode},
    }
    if tool_mode == "auto":
        kwargs["tools"] = [LOOKUP_TOOL_SPEC]
        kwargs["tool_choice"] = "auto"
    elif tool_mode == "forced":
        kwargs["tools"] = [LOOKUP_TOOL_SPEC]
        kwargs["tool_choice"] = {
            "type": "function",
            "function": {"name": TOOL_NAME_LOOKUP},
        }
    else:
        kwargs["response_format"] = {"type": "json_object"}

    try:
        return await client.chat.completions.create(**kwargs)
    except (APITimeoutError, APIConnectionError) as exc:
        raise LLMTimeoutError(str(exc)) from exc
    except RateLimitError as exc:
        raise LLMUpstreamError(str(exc), status_code=429) from exc
    except InternalServerError as exc:
        raise LLMUpstreamError(str(exc), status_code=exc.status_code or 500) from exc
    except (
        AuthenticationError,
        PermissionDeniedError,
        BadRequestError,
        NotFoundError,
        UnprocessableEntityError,
    ) as exc:
        raise LLMAuthError(str(exc)) from exc
    except APIStatusError as exc:
        status = exc.status_code or 0
        if status >= 500 or status == 429:
            raise LLMUpstreamError(str(exc), status_code=status) from exc
        raise LLMAuthError(str(exc)) from exc


async def _handle_tool_call(
    tool_call: Any,
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    state: _ToolRunState,
) -> list[dict[str, Any]]:
    function = getattr(tool_call, "function", None)
    name = getattr(function, "name", "")
    if name != TOOL_NAME_LOOKUP:
        return [{"chunk_id": None, "error": f"unknown tool: {name}"}]
    if state.tool_call_count >= TOOL_MAX_CALLS:
        return [{"chunk_id": None, "error": "tool_call_limit_exceeded"}]

    args = _parse_tool_arguments(getattr(function, "arguments", "") or "{}")
    claim = str(args.get("claim") or "").strip()
    if not claim:
        return [{"chunk_id": None, "error": "empty claim"}]
    top_k = _coerce_top_k(args.get("top_k"))

    state.tool_call_count += 1
    matched_claim = _matched_lookup_claim(claim, state.lookup_claims)
    if matched_claim is not None:
        state.lookup_claims.append(claim)
        state.lookup_deduped_count += 1
        return [
            {
                "chunk_id": None,
                "deduped": True,
                "matched_claim": matched_claim,
                "note": (
                    "This claim is semantically the same as an already "
                    "looked-up claim; reuse the previous tool result."
                ),
            }
        ]

    state.lookup_claims.append(claim)
    try:
        async with sessionmaker() as session:
            chunks = await global_hybrid_search(session, claim, top_k=top_k)
            note_titles = await fetch_note_titles(
                session,
                list({chunk.note_id for chunk in chunks}),
            )
    except Exception as exc:
        return [{"chunk_id": None, "error": f"lookup_failed: {exc}"}]

    if not chunks:
        return [{"chunk_id": None}]

    out: list[dict[str, Any]] = []
    for chunk in chunks:
        ref_id = state.next_ref_id
        state.next_ref_id += 1
        state.ref_to_chunk_id[ref_id] = chunk.id
        out.append(
            {
                "ref_id": ref_id,
                "chunk_id": chunk.id,
                "note_title": note_titles.get(chunk.note_id, ""),
                "folder_path": list(chunk.folder_path),
                "heading_path": list(chunk.heading_path),
                "snippet": _snippet(chunk.content),
            }
        )
    return out


def _parse_tool_arguments(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _coerce_top_k(raw: Any) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 3
    return min(max(value, 1), 3)


def _snippet(content: str, limit: int = 500) -> str:
    collapsed = " ".join(content.split())
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[:limit]}..."


def _assistant_tool_message(message: Any, tool_calls: list[Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": message.content or "",
        "tool_calls": [_tool_call_to_dict(tool_call) for tool_call in tool_calls],
    }


def _tool_call_to_dict(tool_call: Any) -> dict[str, Any]:
    if hasattr(tool_call, "model_dump"):
        return tool_call.model_dump(exclude_none=True)
    function = getattr(tool_call, "function", None)
    return {
        "id": str(getattr(tool_call, "id", "")),
        "type": "function",
        "function": {
            "name": str(getattr(function, "name", "")),
            "arguments": str(getattr(function, "arguments", "{}")),
        },
    }


def _schema_instruction() -> str:
    schema_json = json.dumps(
        AnswerJudgeOutput.model_json_schema(),
        ensure_ascii=False,
        indent=2,
    )
    return f"Respond with a single JSON object that matches this schema:\n{schema_json}"


def _schema_retry_prompt(bad_content: str) -> str:
    schema_json = json.dumps(
        AnswerJudgeOutput.model_json_schema(),
        ensure_ascii=False,
        indent=2,
    )
    return (
        "Your previous response was not valid JSON matching the expected schema. "
        "Return a single JSON object only.\n"
        f"{schema_json}\n\n"
        f"Previous response:\n{bad_content}"
    )


def _parse_answer_judge_output(content: str) -> AnswerJudgeOutput:
    data = json.loads(content)
    return AnswerJudgeOutput.model_validate(data)


def _refresh_unverified_fabricated_claims(
    output: AnswerJudgeOutput,
    state: _ToolRunState,
) -> list[str]:
    out: list[str] = []
    for claim in output.fidelity_evidence.claims:
        if claim.label != "fabricated":
            continue
        if _matched_lookup_claim(claim.text, state.lookup_claims) is not None:
            continue
        out.append(claim.text)
    state.unverified_fabricated_claims = out
    state.lookup_limit_reached = bool(
        out and state.tool_call_count >= TOOL_MAX_CALLS
    )
    return out


def _matched_lookup_claim(claim: str, checked_claims: list[str]) -> str | None:
    for checked in checked_claims:
        if _claims_similar(claim, checked):
            return checked
    return None


def _claims_similar(left: str, right: str) -> bool:
    a = _normalize_claim_text(left)
    b = _normalize_claim_text(right)
    if not a or not b:
        return False
    if a == b:
        return True
    min_len = min(len(a), len(b))
    if min_len >= 8 and (a in b or b in a):
        return True
    a_bigrams = _char_bigrams(a)
    b_bigrams = _char_bigrams(b)
    if not a_bigrams or not b_bigrams:
        return False
    intersection = len(a_bigrams & b_bigrams)
    union = len(a_bigrams | b_bigrams)
    jaccard = intersection / union
    containment = intersection / min(len(a_bigrams), len(b_bigrams))
    return (
        jaccard >= CLAIM_JACCARD_THRESHOLD
        or containment >= CLAIM_CONTAINMENT_THRESHOLD
    )


def _normalize_claim_text(text: str) -> str:
    return "".join(
        ch
        for ch in text.lower()
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"
    )


def _char_bigrams(text: str) -> set[str]:
    if len(text) <= 1:
        return {text}
    return {text[idx : idx + 2] for idx in range(len(text) - 1)}


def _absorb_usage(usage: _UsageAccumulator, resp: Any) -> None:
    resp_usage = getattr(resp, "usage", None)
    if resp_usage is None:
        return
    usage.tokens_in += int(getattr(resp_usage, "prompt_tokens", 0) or 0)
    usage.tokens_out += int(getattr(resp_usage, "completion_tokens", 0) or 0)
    usage.cached_tokens += _read_cached_tokens(resp_usage)
    usage.cache_creation_input_tokens += _read_cache_creation_input_tokens(resp_usage)


def _read_cached_tokens(usage: Any) -> int:
    details = getattr(usage, "prompt_tokens_details", None)
    if details is None:
        return 0
    cached = getattr(details, "cached_tokens", None)
    if cached is None:
        return 0
    return int(cached)


def _read_cache_creation_input_tokens(usage: Any) -> int:
    details = getattr(usage, "prompt_tokens_details", None)
    if details is None:
        return 0
    created = getattr(details, "cache_creation_input_tokens", None)
    if created is None:
        return 0
    return int(created)


def _build_result(
    *,
    usage: _UsageAccumulator,
    parsed: AnswerJudgeOutput | None,
    started: float,
    model: str,
    success: bool,
    error_code: str | None,
    tool_state: _ToolRunState,
) -> LLMResult:
    return LLMResult(
        content=usage.content,
        parsed=parsed,
        tokens_in=usage.tokens_in,
        tokens_out=usage.tokens_out,
        cached_tokens=usage.cached_tokens,
        cache_creation_input_tokens=usage.cache_creation_input_tokens,
        cost_cny=cost_for(
            model=model,
            tokens_in=usage.tokens_in,
            cached_tokens=usage.cached_tokens,
            cache_creation_input_tokens=usage.cache_creation_input_tokens,
            tokens_out=usage.tokens_out,
        ),
        latency_ms=int((monotonic() - started) * 1000),
        model=model,
        feature=PROMPT_NAME,
        tier=Tier.CHEAP,
        thinking_mode=False,
        success=success,
        error_code=error_code,
        user_id=None,
        trace_id=None,
        related_entity=None,
        related_id=None,
        prompt_version_id=None,
        cached=False,
        metadata={
            "lookup_ref_map": {
                str(ref_id): chunk_id
                for ref_id, chunk_id in tool_state.ref_to_chunk_id.items()
            },
            "lookup_tool_call_count": tool_state.tool_call_count,
            "lookup_deduped_count": tool_state.lookup_deduped_count,
            "lookup_claims": list(tool_state.lookup_claims),
            "lookup_limit_reached": tool_state.lookup_limit_reached,
            "unverified_fabricated_claims": list(
                tool_state.unverified_fabricated_claims
            ),
            "finished_at": datetime.now(UTC).isoformat(),
        },
    )


def _error_code_of(exc: BaseException) -> str:
    if isinstance(exc, LLMTimeoutError):
        return "timeout"
    if isinstance(exc, LLMUpstreamError):
        return f"upstream_{exc.upstream_status_code}"
    if isinstance(exc, LLMAuthError):
        return "auth"
    if isinstance(exc, LLMSchemaInvalidError):
        return "schema_invalid"
    return "unknown"
