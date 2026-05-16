"""InterviewCoachAgent minimal turn orchestration(M2.1).

This service owns the single-question remediation loop endpoint:
submit one answer turn, rebuild a context pack, run AnswerJudge on the
cumulative answer, decide the next action, and persist event/state audit.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobcopilot_api.errors import JobCopilotError, NotFoundError, ValidationError
from jobcopilot_api.llm.errors import LLMError
from jobcopilot_api.models.note_chunk import NoteChunk
from jobcopilot_api.models.question import Question
from jobcopilot_api.models.quiz_session import QuizSession
from jobcopilot_api.models.session_answer import SessionAnswer
from jobcopilot_api.models.session_event import SessionEvent
from jobcopilot_api.schemas.agents.answer_judge import AnswerJudgeInput, AnswerJudgeOutput
from jobcopilot_api.schemas.agents.quiz_generator import GeneratedQuestion, QuizGenChunkInput
from jobcopilot_api.schemas.quiz import AnswerTurnSubmitIn
from jobcopilot_api.services import answer_service
from jobcopilot_api.services.retrieval_pipeline import fetch_note_titles

DecisionAction = Literal["ask_next", "remediate", "summarize", "finish"]
Trigger = Literal["coverage", "fidelity", "depth", "mixed", "none"]

COVERAGE_REMEDIATE_THRESHOLD = 60.0
TARGET_COVERAGE = 80.0
TARGET_FABRICATED_RATIO = 0.1
FABRICATED_REMEDIATE_RATIO = 0.3


class InvalidTurnTypeError(ValidationError):
    code = "invalid_turn_type"
    title = "答案轮次类型非法"


class ContextPackFailedError(JobCopilotError):
    status_code = 409
    code = "context_pack_failed"
    title = "构造面试上下文失败"


@dataclass(frozen=True)
class _TurnContext:
    quiz_session: QuizSession
    answer: SessionAnswer
    question: Question
    local_question: GeneratedQuestion
    chunks: list[QuizGenChunkInput]
    prompt_chunk_ids: list[int]
    total_questions: int


@dataclass(frozen=True)
class _Decision:
    next_action: DecisionAction
    triggered_by: Trigger
    decision_reason: str
    exit_reason: str | None
    remediation_prompt: dict[str, Any] | None
    unresolved_gaps: list[dict[str, Any]]


async def submit_answer_turn_sse(
    sessionmaker: async_sessionmaker[AsyncSession],
    session_id: int,
    order_index: int,
    payload: AnswerTurnSubmitIn,
) -> AsyncIterator[dict[str, Any]]:
    """SSE for POST /quiz/sessions/{id}/answers/{order_index}/turns."""
    if payload.turn_type not in ("initial", "remediation"):
        exc = InvalidTurnTypeError(f"turn_type={payload.turn_type!r} 非法")
        yield _ev("error", _error_payload(exc))
        yield _ev("done", {"ok": False})
        return
    if not payload.text.strip():
        exc = ValidationError("答案不能为空")
        yield _ev("error", _error_payload(exc))
        yield _ev("done", {"ok": False})
        return

    try:
        turn_info = await _append_answer_turn(
            sessionmaker,
            session_id=session_id,
            order_index=order_index,
            payload=payload,
        )
    except JobCopilotError as e:
        yield _ev("error", _error_payload(e))
        yield _ev("done", {"ok": False})
        return

    answer_id = int(turn_info["answer_id"])
    round_index = int(turn_info["round_index"])
    yield _ev(
        "started",
        {
            "job_id": f"interview-turn-{session_id}-{order_index}-{round_index}",
            "resource_id": session_id,
            "session_id": session_id,
            "order_index": order_index,
            "round_index": round_index,
        },
    )

    try:
        context = await _build_context_pack(
            sessionmaker,
            session_id=session_id,
            order_index=order_index,
        )
        await _record_event(
            sessionmaker,
            session_id=session_id,
            answer_id=answer_id,
            question_id=context.question.id,
            event_type="context_pack_built",
            agent_node="build_context_pack",
            round_index=round_index,
            payload={
                "included": [
                    "question",
                    "source_chunks",
                    "reference_points",
                    "cumulative_answer",
                    "unresolved_gaps",
                ],
                "chunk_ids": context.prompt_chunk_ids,
                "compacted": False,
            },
        )
        yield _ev(
            "progress",
            {
                "phase": "context_pack_built",
                "included": [
                    "question",
                    "source_chunks",
                    "reference_points",
                    "cumulative_answer",
                    "unresolved_gaps",
                ],
                "compacted": False,
            },
        )

        judge_input = AnswerJudgeInput(
            question=context.local_question,
            chunks=context.chunks,
            user_answer=context.answer.user_answer or "",
        )
        llm_result = await answer_service._run_judge_with_hard_timeout(
            judge_input,
            sessionmaker=sessionmaker,
        )
        judged = answer_service._extract_judge_output(llm_result)
        judged = answer_service._map_and_validate_output(
            output=judged,
            reference_points=context.local_question.reference_points,
            prompt_chunk_ids=context.prompt_chunk_ids,
            lookup_ref_map=answer_service._lookup_ref_map(llm_result),
        )
        scores = answer_service._compute_scores(
            judged,
            context.local_question.reference_points,
        )
        await answer_service._persist_judged_answer(
            sessionmaker,
            answer_id=answer_id,
            judged=judged,
            scores=scores,
            llm_result=llm_result,
        )
        await _record_event(
            sessionmaker,
            session_id=session_id,
            answer_id=answer_id,
            question_id=context.question.id,
            event_type="judge_completed",
            agent_node="judge_answer",
            round_index=round_index,
            payload={"scores": _scores_for_event(scores)},
        )
    except (LLMError, answer_service.JudgeCallFailedError) as e:
        yield _ev(
            "error",
            {
                "code": "judge_call_failed",
                "detail": str(e) or "Judge 调用失败",
                "order_index": order_index,
            },
        )
        yield _ev("done", {"ok": False})
        return
    except JobCopilotError as e:
        yield _ev("error", _error_payload(e))
        yield _ev("done", {"ok": False})
        return

    yield _ev(
        "judge_done",
        {
            "order_index": order_index,
            "round_index": round_index,
            "scores": _scores_for_event(scores),
            "unresolved_gaps": _unresolved_gaps(judged, scores),
        },
    )

    decision = _decide_next_action(
        judged=judged,
        scores=scores,
        question=context.question,
        order_index=order_index,
        total_questions=context.total_questions,
    )
    await _persist_decision(
        sessionmaker,
        session_id=session_id,
        answer_id=answer_id,
        question_id=context.question.id,
        round_index=round_index,
        decision=decision,
    )

    yield _ev(
        "decision_done",
        {
            "next_action": decision.next_action,
            "triggered_by": decision.triggered_by,
            "decision_reason": decision.decision_reason,
            "exit_reason": decision.exit_reason,
        },
    )
    yield _ev(
        "result",
        {
            "session_id": session_id,
            "order_index": order_index,
            "round_index": round_index,
            "next_action": decision.next_action,
            "cumulative_answer": context.answer.user_answer,
            "scores": _scores_for_event(scores),
            "remediation_prompt": decision.remediation_prompt,
        },
    )
    yield _ev("done", {"ok": True})


async def _append_answer_turn(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    session_id: int,
    order_index: int,
    payload: AnswerTurnSubmitIn,
) -> dict[str, int]:
    now = datetime.now(UTC)
    async with sessionmaker() as session:
        quiz_session = await session.get(QuizSession, session_id)
        if quiz_session is None:
            raise NotFoundError(f"quiz_session {session_id} 不存在")
        if quiz_session.status != "in_progress":
            raise answer_service.SessionNotInProgressError(
                f"session {session_id} 当前状态为 {quiz_session.status},不能继续答题"
            )

        answer = (
            await session.execute(
                sa.select(SessionAnswer)
                .where(SessionAnswer.session_id == session_id)
                .where(SessionAnswer.order_index == order_index)
            )
        ).scalar_one_or_none()
        if answer is None:
            raise NotFoundError(
                f"session {session_id} 下不存在 order_index={order_index} 的答案"
            )
        question = await session.get(Question, answer.question_id)
        if question is None:
            raise ContextPackFailedError("session_answer 引用了不存在的 question")
        prompt_chunk_ids = answer_service._prompt_chunk_ids_for_judge(
            quiz_session=quiz_session,
            questions=[question],
        )
        await _ensure_prompt_chunks_exist(session, prompt_chunk_ids)

        turns = list(answer.answer_turns or [])
        round_index = len(turns)
        turn = {
            "round_index": round_index,
            "turn_type": payload.turn_type,
            "text": payload.text,
            "client_turn_id": payload.client_turn_id,
            "submitted_at": now.isoformat(),
        }
        turns.append(turn)
        cumulative_answer = _merge_turn_texts(turns)

        answer.answer_turns = turns
        answer.user_answer = cumulative_answer
        answer.answer_submitted_at = now
        answer.coverage_score = None
        answer.coverage_evidence = None
        answer.fidelity_score = None
        answer.fidelity_evidence = None
        answer.depth_score = None
        answer.depth_evidence = None
        answer.total_score = None
        answer.judge_model = None
        answer.judge_prompt_version = None
        answer.judge_tokens_in = None
        answer.judge_tokens_out = None
        answer.judge_cost_cny = None
        answer.judged_at = None
        answer.updated_at = now
        quiz_session.last_agent_node = "wait_user_answer"
        quiz_session.agent_state = {
            **(quiz_session.agent_state or {}),
            "current_question_index": order_index,
            "last_agent_node": "wait_user_answer",
            "next_action": "judge_answer",
        }
        session.add(
            SessionEvent(
                session_id=session_id,
                answer_id=answer.id,
                question_id=answer.question_id,
                event_type="answer_submitted",
                agent_node="wait_user_answer",
                round_index=round_index,
                payload=turn,
            )
        )
        await session.commit()
        return {"answer_id": answer.id, "round_index": round_index}


async def _build_context_pack(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    session_id: int,
    order_index: int,
) -> _TurnContext:
    async with sessionmaker() as session:
        quiz_session = await session.get(QuizSession, session_id)
        if quiz_session is None:
            raise NotFoundError(f"quiz_session {session_id} 不存在")
        answer = (
            await session.execute(
                sa.select(SessionAnswer)
                .where(SessionAnswer.session_id == session_id)
                .where(SessionAnswer.order_index == order_index)
            )
        ).scalar_one_or_none()
        if answer is None:
            raise NotFoundError(
                f"session {session_id} 下不存在 order_index={order_index} 的答案"
            )
        question = await session.get(Question, answer.question_id)
        if question is None:
            raise ContextPackFailedError("session_answer 引用了不存在的 question")

        total_questions = int(
            (
                await session.execute(
                    sa.select(sa.func.count(SessionAnswer.id)).where(
                        SessionAnswer.session_id == session_id
                    )
                )
            ).scalar_one()
        )
        prompt_chunk_ids = answer_service._prompt_chunk_ids_for_judge(
            quiz_session=quiz_session,
            questions=[question],
        )
        await _ensure_prompt_chunks_exist(session, prompt_chunk_ids)

        chunks = list(
            (
                await session.execute(
                    sa.select(NoteChunk).where(NoteChunk.id.in_(prompt_chunk_ids))
                )
            )
            .scalars()
            .all()
        )
        chunk_by_id = {chunk.id: chunk for chunk in chunks}

        note_titles = await fetch_note_titles(
            session,
            list({chunk.note_id for chunk in chunks}),
        )
        prompt_chunks = [
            answer_service._chunk_to_input(chunk_by_id[chunk_id], note_titles)
            for chunk_id in prompt_chunk_ids
        ]
        local_question = answer_service._question_to_local(question, prompt_chunk_ids)

        return _TurnContext(
            quiz_session=quiz_session,
            answer=answer,
            question=question,
            local_question=local_question,
            chunks=prompt_chunks,
            prompt_chunk_ids=prompt_chunk_ids,
            total_questions=total_questions,
        )


async def _ensure_prompt_chunks_exist(
    session: AsyncSession,
    prompt_chunk_ids: list[int],
) -> None:
    if not prompt_chunk_ids:
        raise ContextPackFailedError(
            "这个 session 没有关联可用于评分的笔记证据,请重新出题后再提交。"
        )
    existing_ids = set(
        (
            await session.execute(
                sa.select(NoteChunk.id).where(NoteChunk.id.in_(prompt_chunk_ids))
            )
        )
        .scalars()
        .all()
    )
    missing = [chunk_id for chunk_id in prompt_chunk_ids if chunk_id not in existing_ids]
    if missing:
        raise ContextPackFailedError(
            "这个 session 引用的笔记证据块已不存在,通常是重新导入或重建笔记库后打开了旧 session。请用同一主题重新出题后再提交本题。",
            errors=[
                {
                    "missing_chunk_count": len(missing),
                    "missing_chunk_ids": missing[:20],
                }
            ],
        )


def _decide_next_action(
    *,
    judged: AnswerJudgeOutput,
    scores: dict[str, float],
    question: Question,
    order_index: int,
    total_questions: int,
) -> _Decision:
    fabricated_claims = [
        claim for claim in judged.fidelity_evidence.claims if claim.label == "fabricated"
    ]
    fabricated_ratio = (
        len(fabricated_claims) / len(judged.fidelity_evidence.claims)
        if judged.fidelity_evidence.claims
        else 0.0
    )
    missing_depth = [
        key
        for key, dimension in judged.depth_evidence.dimensions.items()
        if not dimension.covered
    ]
    coverage_gaps = [
        point
        for point in judged.coverage_evidence.points
        if point.label in ("partial", "miss")
    ]

    if fabricated_ratio > FABRICATED_REMEDIATE_RATIO:
        prompt = _fidelity_prompt(fabricated_claims)
        return _remediate("fidelity", "fabricated ratio 过高", prompt)
    if scores["coverage"] < COVERAGE_REMEDIATE_THRESHOLD:
        prompt = _coverage_prompt(question, coverage_gaps)
        return _remediate("coverage", "coverage 未达阈值", prompt)
    if missing_depth:
        prompt = _depth_prompt(missing_depth)
        return _remediate("depth", "depth 缺少关键维度", prompt)

    if (
        scores["coverage"] >= TARGET_COVERAGE
        and fabricated_ratio <= TARGET_FABRICATED_RATIO
        and not missing_depth
    ):
        return _advance_or_summarize(order_index, total_questions, "target_reached")

    return _advance_or_summarize(order_index, total_questions, "acceptable")


def _remediate(triggered_by: Trigger, reason: str, prompt: dict[str, Any]) -> _Decision:
    return _Decision(
        next_action="remediate",
        triggered_by=triggered_by,
        decision_reason=reason,
        exit_reason=None,
        remediation_prompt=prompt,
        unresolved_gaps=prompt.get("unresolved_gaps", []),
    )


def _advance_or_summarize(
    order_index: int,
    total_questions: int,
    exit_reason: str,
) -> _Decision:
    if order_index >= total_questions - 1:
        return _Decision(
            next_action="summarize",
            triggered_by="none",
            decision_reason="当前题已达到进入总结条件",
            exit_reason=exit_reason,
            remediation_prompt=None,
            unresolved_gaps=[],
        )
    return _Decision(
        next_action="ask_next",
        triggered_by="none",
        decision_reason="当前题已达到进入下一题条件",
        exit_reason=exit_reason,
        remediation_prompt=None,
        unresolved_gaps=[],
    )


def _coverage_prompt(
    question: Question,
    coverage_gaps: list[Any],
) -> dict[str, Any]:
    point_by_id = {str(point["id"]): point for point in question.reference_points}
    gap_ids = [point.id for point in coverage_gaps]
    gap_texts = [
        str(point_by_id.get(point_id, {}).get("text", point_id))
        for point_id in gap_ids
    ]
    evidence_chunk_ids = _unique_ids(
        chunk_id
        for point_id in gap_ids
        for chunk_id in point_by_id.get(point_id, {}).get("evidence_chunk_ids", [])
    )
    return {
        "text": "你这题还漏了关键采分点: "
        + "；".join(gap_texts[:3])
        + "。请基于笔记证据补充这些点。",
        "triggered_by": "coverage",
        "missing_reference_point_ids": gap_ids,
        "fabricated_claim_ids": [],
        "missing_depth_dimensions": [],
        "evidence_chunk_ids": evidence_chunk_ids,
        "unresolved_gaps": [
            {"type": "coverage", "reference_point_id": point_id}
            for point_id in gap_ids
        ],
    }


def _fidelity_prompt(fabricated_claims: list[Any]) -> dict[str, Any]:
    claim_texts = [claim.text for claim in fabricated_claims]
    return {
        "text": "这些说法暂时缺少笔记证据或与证据冲突: "
        + "；".join(claim_texts[:3])
        + "。请说明依据来自哪里,或改回笔记能支撑的表述。",
        "triggered_by": "fidelity",
        "missing_reference_point_ids": [],
        "fabricated_claim_ids": list(range(len(fabricated_claims))),
        "missing_depth_dimensions": [],
        "evidence_chunk_ids": [],
        "unresolved_gaps": [
            {"type": "fidelity", "claim": text} for text in claim_texts
        ],
    }


def _depth_prompt(missing_depth: list[str]) -> dict[str, Any]:
    labels = {
        "tradeoff": "trade-off / 取舍",
        "why": "why / 设计动机",
        "boundary": "boundary / 适用边界",
    }
    readable = [labels.get(key, key) for key in missing_depth]
    return {
        "text": "你的答案还缺少深度维度: "
        + "、".join(readable)
        + "。请补充为什么这样设计、有什么取舍或边界。",
        "triggered_by": "depth",
        "missing_reference_point_ids": [],
        "fabricated_claim_ids": [],
        "missing_depth_dimensions": missing_depth,
        "evidence_chunk_ids": [],
        "unresolved_gaps": [
            {"type": "depth", "dimension": dimension}
            for dimension in missing_depth
        ],
    }


async def _persist_decision(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    session_id: int,
    answer_id: int,
    question_id: int,
    round_index: int,
    decision: _Decision,
) -> None:
    now = datetime.now(UTC)
    state = {
        "last_decision": decision.next_action,
        "triggered_by": decision.triggered_by,
        "decision_reason": decision.decision_reason,
        "exit_reason": decision.exit_reason,
        "remediation_prompt": decision.remediation_prompt,
        "unresolved_gaps": decision.unresolved_gaps,
    }
    async with sessionmaker() as session:
        await session.execute(
            sa.update(SessionAnswer)
            .where(SessionAnswer.id == answer_id)
            .values(remediation_state=state, updated_at=now)
        )
        quiz_session = await session.get(QuizSession, session_id)
        if quiz_session is not None:
            quiz_session.last_agent_node = "decide_next_action"
            quiz_session.agent_state = {
                **(quiz_session.agent_state or {}),
                "last_agent_node": "decide_next_action",
                "next_action": decision.next_action,
                "unresolved_gaps": decision.unresolved_gaps,
            }
        session.add(
            SessionEvent(
                session_id=session_id,
                answer_id=answer_id,
                question_id=question_id,
                event_type="decision_made",
                agent_node="decide_next_action",
                round_index=round_index,
                payload=state,
            )
        )
        if decision.remediation_prompt is not None:
            session.add(
                SessionEvent(
                    session_id=session_id,
                    answer_id=answer_id,
                    question_id=question_id,
                    event_type="remediation_prompted",
                    agent_node="generate_remediation_prompt",
                    round_index=round_index,
                    payload=decision.remediation_prompt,
                )
            )
        await session.commit()


async def _record_event(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    session_id: int,
    answer_id: int | None,
    question_id: int | None,
    event_type: str,
    agent_node: str,
    round_index: int,
    payload: dict[str, Any],
) -> None:
    async with sessionmaker() as session:
        session.add(
            SessionEvent(
                session_id=session_id,
                answer_id=answer_id,
                question_id=question_id,
                event_type=event_type,
                agent_node=agent_node,
                round_index=round_index,
                payload=payload,
            )
        )
        await session.commit()


def _merge_turn_texts(turns: list[dict[str, Any]]) -> str:
    return "\n\n".join(str(turn.get("text", "")).strip() for turn in turns).strip()


def _unresolved_gaps(
    judged: AnswerJudgeOutput,
    scores: dict[str, float],
) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    if scores["coverage"] < COVERAGE_REMEDIATE_THRESHOLD:
        gaps.extend(
            {"type": "coverage", "reference_point_id": point.id}
            for point in judged.coverage_evidence.points
            if point.label in ("partial", "miss")
        )
    gaps.extend(
        {"type": "fidelity", "claim": claim.text}
        for claim in judged.fidelity_evidence.claims
        if claim.label == "fabricated"
    )
    gaps.extend(
        {"type": "depth", "dimension": key}
        for key, dimension in judged.depth_evidence.dimensions.items()
        if not dimension.covered
    )
    return gaps


def _scores_for_event(scores: dict[str, float]) -> dict[str, float]:
    return {key: round(value, 2) for key, value in scores.items()}


def _unique_ids(values: Any) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item not in seen:
            out.append(item)
            seen.add(item)
    return out


def _ev(event: str, data: dict[str, Any]) -> dict[str, str]:
    return {
        "event": event,
        "data": json.dumps(data, ensure_ascii=False, default=str),
    }


def _error_payload(exc: JobCopilotError) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": exc.code, "detail": exc.detail}
    if exc.errors:
        payload["errors"] = exc.errors
    return payload
