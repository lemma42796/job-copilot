"""JD 上传(立即解析)+ 一键分析编排 service(M2.5)。

第一刀只把文本 JD 入库闭环打通:
- POST /api/jds:文本入库 → 立即调 jd_parser agent → 落 parsed_payload
- GET/PATCH/DELETE /api/jds:JD 库基础管理

一键分析的 filter 解析和报告表查询也先落骨架;真正 map-reduce 聚合留到
后续切片接 `jd_aggregator`。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobcopilot_api.agents.jd_parser import agent as jd_parser_agent
from jobcopilot_api.agents.jd_parser.prompts import PROMPT_VERSION as JD_PARSE_VERSION
from jobcopilot_api.errors import JobCopilotError, NotFoundError, ValidationError
from jobcopilot_api.llm.errors import LLMError
from jobcopilot_api.models.jd import Jd
from jobcopilot_api.models.jd_analysis import JdAnalysis
from jobcopilot_api.schemas.agents.jd_parser import JdParseInput, JdParseOutput
from jobcopilot_api.schemas.jd import (
    AggregatedRequirement,
    JdAnalysisCreateIn,
    JdAnalysisListItemOut,
    JdAnalysisListOut,
    JdAnalysisOut,
    JdCreateIn,
    JdListItemOut,
    JdListOut,
    JdOut,
    JdPatchIn,
)

JD_RAW_TEXT_MAX_LENGTH = 10_000
JD_ANALYSIS_MAX_COUNT = 200


class JdNotFoundError(NotFoundError):
    code = "jd_not_found"
    title = "JD 不存在"


class JdParseFailedError(JobCopilotError):
    status_code = 500
    code = "jd_parse_failed"
    title = "JD 解析失败"


class JdCountZeroError(JobCopilotError):
    status_code = 422
    code = "jd_count_zero"
    title = "没有命中可分析的 JD"


class JdCountExceedsLimitError(JobCopilotError):
    status_code = 422
    code = "jd_count_exceeds_limit"
    title = "JD 数量超过单次分析上限"


async def upload_jd(session: AsyncSession, payload: JdCreateIn) -> JdOut:
    """上传一条文本 JD,立即解析并落库。"""
    if payload.source != "text_paste":
        raise ValidationError("M2.5 第一刀只支持 source=text_paste")

    raw_text = _normalize_raw_text(payload.raw_text)
    try:
        parse_result = await jd_parser_agent.run(JdParseInput(raw_text=raw_text))
    except LLMError as exc:
        raise JdParseFailedError(f"jd_parser 调用失败:{exc.detail}") from exc

    parsed = parse_result.parsed
    if not isinstance(parsed, JdParseOutput):
        raise JdParseFailedError("jd_parser 未返回合法 parsed payload")

    parsed_payload = _normalize_parsed_payload(parsed, raw_text)
    jd = Jd(
        source=payload.source,
        raw_text=raw_text,
        title=parsed_payload["title"],
        parsed_payload=parsed_payload,
        parse_model=parse_result.model,
        parse_prompt_version=JD_PARSE_VERSION,
        parse_tokens_in=parse_result.tokens_in,
        parse_tokens_out=parse_result.tokens_out,
        parse_cost_cny=parse_result.cost_cny,
    )
    session.add(jd)
    await session.flush()
    return _to_out(jd)


async def list_jds(
    session: AsyncSession,
    cursor: int | None,
    limit: int = 20,
    title: str | None = None,
) -> JdListOut:
    limit = max(1, min(limit, 100))
    title_query = title.strip() if title else ""
    stmt = (
        sa.select(Jd)
        .where(Jd.deleted_at.is_(None))
        .order_by(Jd.id.desc())
        .limit(limit + 1)
    )
    if cursor is not None:
        stmt = stmt.where(Jd.id < cursor)
    if title_query:
        stmt = stmt.where(Jd.title.ilike(f"%{title_query}%"))

    rows = list((await session.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    items = [_to_list_item(jd) for jd in rows[:limit]]
    return JdListOut(
        items=items,
        next_cursor=items[-1].id if has_more and items else None,
        has_more=has_more,
    )


async def get_jd(session: AsyncSession, jd_id: int) -> JdOut:
    return _to_out(await _get_active_or_404(session, jd_id))


async def patch_jd(session: AsyncSession, jd_id: int, payload: JdPatchIn) -> JdOut:
    jd = await _get_active_or_404(session, jd_id)
    if payload.title is not None:
        normalized = payload.title.strip()
        jd.title = _normalize_title(normalized, jd.raw_text)
    await session.flush()
    return _to_out(jd)


async def delete_jd(session: AsyncSession, jd_id: int) -> None:
    jd = await _get_active_or_404(session, jd_id)
    jd.deleted_at = datetime.now(timezone.utc)
    await session.flush()


async def create_analysis_placeholder(
    session: AsyncSession, payload: JdAnalysisCreateIn
) -> JdAnalysis:
    """解析 filter、校验数量,创建一条 in_progress 分析快照。"""
    jd_ids = await _resolve_analysis_jd_ids(session, payload)
    analysis = JdAnalysis(
        jd_ids=jd_ids,
        jd_count=len(jd_ids),
        filter_description=payload.filter_description
        or _describe_analysis_filter(payload),
        status="in_progress",
    )
    session.add(analysis)
    await session.flush()
    return analysis


async def start_analysis_sse(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    analysis_id: int,
    jd_count: int,
) -> AsyncIterator[dict[str, Any]]:
    """M2.5 harness SSE 骨架。

    placeholder 已在返回 EventSourceResponse 前创建,所以 filter 错误仍能按
    REST 语义返回 422。真正的 aggregate/dedupe/frequency/learning_path 后续
    切片接入,当前明确返回 error,避免用户误以为报告已经生成。
    """

    yield _ev(
        "started",
        {
            "job_id": f"jd-analysis-{uuid.uuid4().hex}",
            "resource_id": analysis_id,
            "jd_count": jd_count,
        },
    )
    yield _ev("progress", {"phase": "loading_parsed", "jd_count": jd_count})

    async with sessionmaker() as session:
        analysis = await session.get(JdAnalysis, analysis_id)
        if analysis is not None:
            analysis.status = "failed"
            analysis.failed_at = datetime.now(timezone.utc)
            analysis.failure_reason = "jd_aggregator_not_ready"
            await session.commit()

    yield _ev(
        "error",
        {
            "code": "jd_analysis_not_ready",
            "detail": "JDAnalysisAgent harness 已建立,聚合节点将在后续切片接入",
        },
    )
    yield _ev("done", {"ok": False})


async def list_analyses(
    session: AsyncSession,
    cursor: int | None,
    limit: int = 20,
) -> JdAnalysisListOut:
    limit = max(1, min(limit, 100))
    stmt = sa.select(JdAnalysis).order_by(JdAnalysis.id.desc()).limit(limit + 1)
    if cursor is not None:
        stmt = stmt.where(JdAnalysis.id < cursor)

    rows = list((await session.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    items = [_analysis_to_list_item(analysis) for analysis in rows[:limit]]
    return JdAnalysisListOut(
        items=items,
        next_cursor=items[-1].id if has_more and items else None,
        has_more=has_more,
    )


async def get_analysis(session: AsyncSession, analysis_id: int) -> JdAnalysisOut:
    analysis = await session.get(JdAnalysis, analysis_id)
    if analysis is None:
        raise NotFoundError(f"jd_analysis {analysis_id} 不存在")
    return _analysis_to_out(analysis)


async def _get_active_or_404(session: AsyncSession, jd_id: int) -> Jd:
    jd = await session.get(Jd, jd_id)
    if jd is None or jd.deleted_at is not None:
        raise JdNotFoundError(f"jd {jd_id} not found")
    return jd


def _normalize_raw_text(raw_text: str) -> str:
    normalized = raw_text.strip()
    if not normalized:
        raise ValidationError("raw_text 不能为空")
    if len(normalized) > JD_RAW_TEXT_MAX_LENGTH:
        raise ValidationError(
            f"raw_text 长度 {len(normalized)} > {JD_RAW_TEXT_MAX_LENGTH}"
        )
    return normalized


def _normalize_parsed_payload(
    parsed: JdParseOutput,
    raw_text: str,
) -> dict[str, Any]:
    payload = parsed.model_dump(mode="json")
    title = (payload.get("title") or "").strip()
    payload["title"] = _normalize_title(title, raw_text)
    for key in ("responsibilities", "hard_skills", "soft_skills"):
        payload[key] = _dedupe_non_empty_strings(payload.get(key) or [])
    extras = payload.get("extras")
    payload["extras"] = extras if isinstance(extras, dict) else {}
    return payload


def _dedupe_non_empty_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _fallback_title(raw_text: str) -> str:
    compact = " ".join(raw_text.split())
    if not compact:
        return "未标注岗位"
    return f"{compact[:50]}(未明确岗位)"


def _normalize_title(title: str, raw_text: str) -> str:
    return (title or _fallback_title(raw_text))[:255]


def _to_out(jd: Jd) -> JdOut:
    return JdOut(
        id=jd.id,
        source=jd.source,
        title=jd.title or _fallback_title(jd.raw_text),
        raw_text=jd.raw_text,
        parsed_payload=dict(jd.parsed_payload or {}),
        parse_model=jd.parse_model,
        parse_prompt_version=jd.parse_prompt_version,
        parse_tokens_in=jd.parse_tokens_in,
        parse_tokens_out=jd.parse_tokens_out,
        parse_cost_cny=jd.parse_cost_cny,
        created_at=jd.created_at,
        updated_at=jd.updated_at,
    )


def _to_list_item(jd: Jd) -> JdListItemOut:
    parsed_payload = dict(jd.parsed_payload or {})
    hard_skills = parsed_payload.get("hard_skills") or []
    return JdListItemOut(
        id=jd.id,
        title=jd.title or _fallback_title(jd.raw_text),
        source=jd.source,
        raw_text_preview=_preview(jd.raw_text),
        hard_skills_count=len(hard_skills) if isinstance(hard_skills, list) else 0,
        created_at=jd.created_at,
    )


def _preview(raw_text: str, limit: int = 200) -> str:
    compact = " ".join(raw_text.split())
    return compact[:limit]


def _ev(event: str, data: dict[str, Any]) -> dict[str, str]:
    return {
        "event": event,
        "data": json.dumps(data, ensure_ascii=False, default=str),
    }


async def _resolve_analysis_jd_ids(
    session: AsyncSession,
    payload: JdAnalysisCreateIn,
) -> list[int]:
    jd_filter = payload.filter
    stmt = sa.select(Jd.id).where(Jd.deleted_at.is_(None)).order_by(Jd.id.desc())
    limit = JD_ANALYSIS_MAX_COUNT + 1
    if jd_filter.type == "title":
        if not jd_filter.value or not jd_filter.value.strip():
            raise ValidationError("filter.type=title 时 value 不能为空")
        stmt = stmt.where(Jd.title.ilike(f"%{jd_filter.value.strip()}%"))
    elif jd_filter.type == "ids":
        ids = jd_filter.ids or []
        if not ids:
            raise ValidationError("filter.type=ids 时 ids 不能为空")
        stmt = stmt.where(Jd.id.in_(ids))
    elif jd_filter.type == "recent":
        if jd_filter.n is None:
            raise ValidationError("filter.type=recent 时 n 不能为空")
        limit = min(jd_filter.n, JD_ANALYSIS_MAX_COUNT + 1)
    elif jd_filter.type != "all":
        raise ValidationError(f"不支持的 JD filter type:{jd_filter.type}")

    rows = list((await session.execute(stmt.limit(limit))).scalars())
    if not rows:
        raise JdCountZeroError("filter 没有命中可分析的 JD")
    if len(rows) > JD_ANALYSIS_MAX_COUNT:
        raise JdCountExceedsLimitError(
            f"单次最多分析 {JD_ANALYSIS_MAX_COUNT} 条 JD"
        )
    return rows


def _describe_analysis_filter(payload: JdAnalysisCreateIn) -> str:
    jd_filter = payload.filter
    if jd_filter.type == "all":
        return "全部"
    if jd_filter.type == "title":
        return f"title={jd_filter.value or ''}"
    if jd_filter.type == "ids":
        return f"指定 {len(jd_filter.ids or [])} 条 JD"
    if jd_filter.type == "recent":
        return f"最近 {jd_filter.n} 条"
    return jd_filter.type


def _analysis_to_list_item(analysis: JdAnalysis) -> JdAnalysisListItemOut:
    requirements = analysis.aggregated_requirements or []
    quiz_topics = analysis.quiz_topic_candidates or []
    return JdAnalysisListItemOut(
        id=analysis.id,
        jd_count=analysis.jd_count,
        filter_description=analysis.filter_description,
        status=analysis.status,
        requirement_count=len(requirements) if isinstance(requirements, list) else 0,
        quiz_topic_count=len(quiz_topics) if isinstance(quiz_topics, list) else 0,
        started_at=analysis.started_at,
        completed_at=analysis.completed_at,
        failed_at=analysis.failed_at,
    )


def _analysis_to_out(analysis: JdAnalysis) -> JdAnalysisOut:
    requirements = [
        AggregatedRequirement.model_validate(item)
        for item in (analysis.aggregated_requirements or [])
    ]
    return JdAnalysisOut(
        id=analysis.id,
        jd_ids=list(analysis.jd_ids),
        jd_count=analysis.jd_count,
        filter_description=analysis.filter_description,
        status=analysis.status,
        aggregated_requirements=requirements,
        learning_path_md=analysis.learning_path_md,
        quiz_topic_candidates=list(analysis.quiz_topic_candidates or []),
        note_match_summary=list(analysis.note_match_summary or []),
        total_tokens_in=analysis.total_tokens_in,
        total_tokens_out=analysis.total_tokens_out,
        total_cost_cny=analysis.total_cost_cny,
        cache_hit_rate=analysis.cache_hit_rate,
        started_at=analysis.started_at,
        completed_at=analysis.completed_at,
        failed_at=analysis.failed_at,
        failure_reason=analysis.failure_reason,
    )
