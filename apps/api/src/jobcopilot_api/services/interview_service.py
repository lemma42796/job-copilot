"""InterviewCoachAgent minimal turn orchestration(M2.1).

This service owns the single-question remediation loop endpoint:
submit one answer turn, rebuild a context pack, run AnswerJudge on the
cumulative answer, decide the next action, and persist event/state audit.

P0:每个入口都要求显式 `user_id`,所有 quiz_session / session_answer /
question / session_event / note_chunk 查询都带归属过滤。
P3:这里的生成器由 worker 消费,事件写 `job_events`,在线接口只返回 202。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobcopilot_api.agents.answer_judge.scoring import total_score as compute_total_score
from jobcopilot_api.agents.coach_chat import agent as coach_chat_agent
from jobcopilot_api.errors import JobCopilotError, NotFoundError, ValidationError
from jobcopilot_api.llm.errors import LLMError
from jobcopilot_api.models.note_chunk import NoteChunk
from jobcopilot_api.models.question import Question
from jobcopilot_api.models.quiz_session import QuizSession
from jobcopilot_api.models.session_answer import SessionAnswer
from jobcopilot_api.models.session_event import SessionEvent
from jobcopilot_api.schemas.agents.answer_judge import AnswerJudgeInput, AnswerJudgeOutput
from jobcopilot_api.schemas.agents.coach_chat import CoachChatInput, CoachChatOutput
from jobcopilot_api.schemas.agents.quiz_generator import GeneratedQuestion, QuizGenChunkInput
from jobcopilot_api.schemas.quiz import AnswerTurnSubmitIn
from jobcopilot_api.services import (
    answer_judge_service,
    answer_service,
    billing_service,
    recall_service,
)

DecisionAction = Literal["ask_next", "remediate", "summarize", "finish"]
Trigger = Literal["coverage", "fidelity", "depth", "mixed", "none"]

COVERAGE_REMEDIATE_THRESHOLD = 60.0
TARGET_COVERAGE = 80.0
TARGET_FABRICATED_RATIO = 0.1
FABRICATED_REMEDIATE_RATIO = 0.3
NO_MEANINGFUL_IMPROVEMENT_MIN_TURNS = 3
NO_MEANINGFUL_IMPROVEMENT_DELTA = 5.0
MAX_JUDGE_SCORE_HISTORY = 8
CONTEXT_COMPACTION_TURN_THRESHOLD = 3
TOKEN_BUDGET_EXIT_TURN_THRESHOLD = 6
ANSWER_MARKERS = (
    "我补充",
    "补充一下",
    "我的答案",
    "我重新答",
    "重新回答",
    "这题应该",
    "我认为",
    "我总结一下",
    "可以这样答",
    "答案是",
)
QUESTION_MARKERS = (
    "为什么",
    "什么意思",
    "是什么",
    "怎么理解",
    "能解释",
    "解释一下",
    "举个例子",
    "比如呢",
    "我不懂",
    "没懂",
    "不太懂",
    "区别是什么",
    "哪里错",
    "为什么错",
    "coverage 不够",
)
TECH_TERMS = (
    "事务",
    "幂等",
    "重试",
    "一致性",
    "缓存",
    "索引",
    "队列",
    "锁",
    "延迟",
    "吞吐",
    "边界",
    "取舍",
    "复杂度",
    "可靠性",
    "降级",
    "补偿",
    "异步",
    "hash",
    "hashmap",
    "数组",
)
ANSWER_CONNECTORS = (
    "因为",
    "所以",
    "首先",
    "其次",
    "另外",
    "最后",
    "本质上",
    "核心是",
    "区别在于",
    "适用于",
)


class InvalidTurnTypeError(ValidationError):
    code = "invalid_turn_type"
    title = "答案轮次类型非法"


class ContextPackFailedError(JobCopilotError):
    status_code = 409
    code = "context_pack_failed"
    title = "构造面试上下文失败"


class CoachCallFailedError(JobCopilotError):
    status_code = 502
    code = "coach_call_failed"
    title = "教练解释调用失败"


class SessionNotReadyToFinishError(JobCopilotError):
    status_code = 409
    code = "session_not_ready_to_finish"
    title = "会话还不能结束"


@dataclass(frozen=True)
class _TurnContext:
    quiz_session: QuizSession
    answer: SessionAnswer
    question: Question
    local_question: GeneratedQuestion
    chunks: list[QuizGenChunkInput]
    judge_context_chunk_ids: list[int]
    total_questions: int
    compacted: bool
    prior_turn_summary: str | None
    token_budget_exhausted: bool


@dataclass(frozen=True)
class _Decision:
    next_action: DecisionAction
    triggered_by: Trigger
    decision_reason: str
    exit_reason: str | None
    remediation_prompt: dict[str, Any] | None
    unresolved_gaps: list[dict[str, Any]]


@dataclass(frozen=True)
class _FinishQuestion:
    answer_id: int
    order_index: int
    question_id: int
    question_type: str
    prompt: str
    evidence_chunk_ids: list[int]
    reference_answer: str
    scoring_points: list[dict[str, Any]]
    user_answer: str
    answer_turns: list[dict[str, Any]]
    remediation_state: dict[str, Any]
    coach_message: str | None
    coverage_score: float
    coverage_evidence: dict[str, Any]
    fidelity_score: float
    fidelity_evidence: dict[str, Any]
    depth_score: float
    depth_evidence: dict[str, Any]
    total_score: float
    judge_score_history: list[dict[str, float]]


async def submit_answer_turn_sse(
    sessionmaker: async_sessionmaker[AsyncSession],
    session_id: int,
    order_index: int,
    payload: AnswerTurnSubmitIn,
    *,
    user_id: int,
) -> AsyncIterator[dict[str, Any]]:
    """SSE for POST /quiz/sessions/{id}/answers/{order_index}/turns."""
    if payload.turn_type not in ("auto", "initial", "remediation", "coach_question"):
        exc = InvalidTurnTypeError(f"turn_type={payload.turn_type!r} 非法")
        yield _ev("error", _error_payload(exc))
        yield _ev("done", {"ok": False})
        return
    if not payload.text.strip():
        exc = ValidationError(
            "追问不能为空"
            if payload.turn_type == "coach_question"
            else "内容不能为空"
            if payload.turn_type == "auto"
            else "答案不能为空"
        )
        yield _ev("error", _error_payload(exc))
        yield _ev("done", {"ok": False})
        return
    if payload.turn_type == "auto":
        try:
            payload = payload.model_copy(
                update={
                    "turn_type": await _classify_auto_turn_type(
                        sessionmaker,
                        session_id=session_id,
                        order_index=order_index,
                        text=payload.text,
                        user_id=user_id,
                    )
                }
            )
        except JobCopilotError as e:
            yield _ev("error", _error_payload(e))
            yield _ev("done", {"ok": False})
            return
    if payload.turn_type == "coach_question":
        async for event in _submit_coach_question_sse(
            sessionmaker,
            session_id=session_id,
            order_index=order_index,
            payload=payload,
            user_id=user_id,
        ):
            yield event
        return

    try:
        turn_info = await _append_answer_turn(
            sessionmaker,
            session_id=session_id,
            order_index=order_index,
            payload=payload,
            user_id=user_id,
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
            "turn_type": payload.turn_type,
        },
    )

    try:
        context = await _build_context_pack(
            sessionmaker,
            session_id=session_id,
            order_index=order_index,
            user_id=user_id,
        )
        included = [
            "question",
            "judge_context_chunks",
            "scoring_points",
            "cumulative_answer",
            "unresolved_gaps",
        ]
        if context.compacted:
            included.append("prior_turn_summary")
            await _record_event(
                sessionmaker,
                user_id=user_id,
                session_id=session_id,
                answer_id=answer_id,
                question_id=context.question.id,
                event_type="context_compacted",
                agent_node="build_context_pack",
                round_index=round_index,
                payload={
                    "reason": (
                        "token_budget"
                        if context.token_budget_exhausted
                        else "turn_history"
                    ),
                    "answer_turn_count": len(context.answer.answer_turns or []),
                    "prior_turn_summary": context.prior_turn_summary,
                    "kept_fields": [
                        "question",
                        "judge_context_chunks",
                        "scoring_points",
                        "cumulative_answer",
                        "unresolved_gaps",
                    ],
                    "compacted_fields": [
                        "older_answer_turns",
                        "judge_feedback",
                        "coach_messages",
                    ],
                },
            )
        await _record_event(
            sessionmaker,
            user_id=user_id,
            session_id=session_id,
            answer_id=answer_id,
            question_id=context.question.id,
            event_type="context_pack_built",
            agent_node="build_context_pack",
            round_index=round_index,
            payload={
                "included": included,
                "judge_context_chunk_ids": context.judge_context_chunk_ids,
                "compacted": context.compacted,
                "prior_turn_summary": context.prior_turn_summary,
                "token_budget_exhausted": context.token_budget_exhausted,
            },
        )
        yield _ev(
            "progress",
            {
                "phase": "context_pack_built",
                "included": included,
                "compacted": context.compacted,
            },
        )

        judge_input = AnswerJudgeInput(
            question=context.local_question,
            chunks=context.chunks,
            user_answer=context.answer.user_answer or "",
        )
        judged_answer = await answer_judge_service.judge_and_persist_answer(
            sessionmaker,
            answer_id=answer_id,
            judge_input=judge_input,
            scoring_points=context.local_question.scoring_points,
            judge_context_chunk_ids=context.judge_context_chunk_ids,
            user_id=user_id,
        )
        judged = judged_answer.judged
        scores = judged_answer.scores
        await _record_event(
            sessionmaker,
            user_id=user_id,
            session_id=session_id,
            answer_id=answer_id,
            question_id=context.question.id,
            event_type="judge_completed",
            agent_node="judge_answer",
            round_index=round_index,
            payload={
                "scores": _scores_for_event(scores),
                "coach_message": judged.coach_message,
            },
        )
    except billing_service.InsufficientBalanceError as e:
        # P1:就地中止。本轮答案文本已在 _append_answer_turn 里落库,保留。
        yield _ev("error", _error_payload(e))
        yield _ev("done", {"ok": False})
        return
    except (LLMError, answer_judge_service.JudgeCallFailedError) as e:
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
            "coach_message": judged.coach_message,
            "unresolved_gaps": _unresolved_gaps(judged, scores),
        },
    )

    decision = _decide_next_action(
        judged=judged,
        scores=scores,
        question=context.question,
        order_index=order_index,
        total_questions=context.total_questions,
        answer_turns=list(context.answer.answer_turns or []),
        remediation_state=dict(context.answer.remediation_state or {}),
        token_budget_exhausted=context.token_budget_exhausted,
    )
    await _persist_decision(
        sessionmaker,
        session_id=session_id,
        answer_id=answer_id,
        question_id=context.question.id,
        round_index=round_index,
        decision=decision,
        scores=scores,
        user_id=user_id,
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
            "coach_message": judged.coach_message,
        },
    )
    yield _ev("done", {"ok": True})


async def finish_session_sse(
    sessionmaker: async_sessionmaker[AsyncSession],
    session_id: int,
    *,
    user_id: int,
) -> AsyncIterator[dict[str, Any]]:
    """Finish an M2.1 interview session without re-judging answers."""
    try:
        questions = await _load_finish_questions(
            sessionmaker, session_id, user_id=user_id
        )
    except JobCopilotError as e:
        yield _ev("error", _error_payload(e))
        yield _ev("done", {"ok": False})
        return

    yield _ev(
        "started",
        {
            "job_id": f"finish-session-{session_id}",
            "resource_id": session_id,
            "session_id": session_id,
            "total_questions": len(questions),
        },
    )
    yield _ev(
        "progress",
        {
            "phase": "summarizing",
            "included": [
                "questions",
                "answer_turns",
                "judge_scores",
                "judge_gaps",
                "remediation_state",
            ],
            "compacted": True,
        },
    )

    try:
        result = await _summarize_and_finish_session(
            sessionmaker,
            session_id=session_id,
            questions=questions,
            user_id=user_id,
        )
    except JobCopilotError as e:
        yield _ev("error", _error_payload(e))
        yield _ev("done", {"ok": False})
        return

    yield _ev("result", result)
    yield _ev("done", {"ok": True})


async def _submit_coach_question_sse(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    session_id: int,
    order_index: int,
    payload: AnswerTurnSubmitIn,
    user_id: int,
) -> AsyncIterator[dict[str, Any]]:
    try:
        turn_info = await _record_coach_question(
            sessionmaker,
            session_id=session_id,
            order_index=order_index,
            payload=payload,
            user_id=user_id,
        )
    except JobCopilotError as e:
        yield _ev("error", _error_payload(e))
        yield _ev("done", {"ok": False})
        return

    answer_id = int(turn_info["answer_id"])
    question_id = int(turn_info["question_id"])
    round_index = int(turn_info["round_index"])
    yield _ev(
        "started",
        {
            "job_id": f"coach-question-{session_id}-{order_index}-{round_index}",
            "resource_id": session_id,
            "session_id": session_id,
            "order_index": order_index,
            "round_index": round_index,
            "turn_type": "coach_question",
        },
    )

    try:
        context = await _build_context_pack(
            sessionmaker,
            session_id=session_id,
            order_index=order_index,
            user_id=user_id,
        )
        included = [
            "question",
            "judge_context_chunks",
            "cumulative_answer",
            "previous_coach_message",
            "remediation_prompt",
        ]
        if context.compacted:
            included.append("prior_turn_summary")
        yield _ev(
            "progress",
            {
                "phase": "coach_context_built",
                "included": included,
                "compacted": context.compacted,
            },
        )

        state = context.answer.remediation_state or {}
        coach_input = CoachChatInput(
            question=context.local_question,
            chunks=context.chunks,
            cumulative_answer=context.answer.user_answer or "",
            coach_question=payload.text,
            prior_coach_message=context.answer.coach_message,
            remediation_prompt=state.get("remediation_prompt"),
            unresolved_gaps=[
                item
                for item in state.get("unresolved_gaps") or []
                if isinstance(item, dict)
            ],
            scores=_answer_scores(context.answer),
        )
        llm_result = await coach_chat_agent.run(coach_input, user_id=user_id)
        coach_output = _extract_coach_chat_output(llm_result)

        await _record_event(
            sessionmaker,
            user_id=user_id,
            session_id=session_id,
            answer_id=answer_id,
            question_id=question_id,
            event_type="coach_answered",
            agent_node="coach_question",
            round_index=round_index,
            payload={
                "turn_type": "coach_question",
                "text": payload.text,
                "client_turn_id": payload.client_turn_id,
                "coach_message": coach_output.coach_message,
                "submitted_at": turn_info["submitted_at"],
                "answered_at": datetime.now(UTC).isoformat(),
                "model": llm_result.model,
                "tokens_in": llm_result.tokens_in,
                "tokens_out": llm_result.tokens_out,
                "cost_cny": str(llm_result.cost_cny),
            },
        )
    except billing_service.InsufficientBalanceError as e:
        yield _ev("error", _error_payload(e))
        yield _ev("done", {"ok": False})
        return
    except LLMError as e:
        yield _ev(
            "error",
            {
                "code": "coach_call_failed",
                "detail": str(e) or "教练解释调用失败",
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
        "coach_done",
        {
            "order_index": order_index,
            "round_index": round_index,
            "turn_type": "coach_question",
            "text": payload.text,
            "client_turn_id": payload.client_turn_id,
            "submitted_at": turn_info["submitted_at"],
            "coach_message": coach_output.coach_message,
        },
    )
    yield _ev("done", {"ok": True})


async def _load_owned_answer(
    session: AsyncSession,
    *,
    session_id: int,
    order_index: int,
    user_id: int,
) -> SessionAnswer:
    answer = (
        await session.execute(
            sa.select(SessionAnswer)
            .where(SessionAnswer.session_id == session_id)
            .where(SessionAnswer.user_id == user_id)
            .where(SessionAnswer.order_index == order_index)
        )
    ).scalar_one_or_none()
    if answer is None:
        raise NotFoundError(
            f"session {session_id} 下不存在 order_index={order_index} 的答案"
        )
    return answer


async def _load_owned_question(
    session: AsyncSession, question_id: int, *, user_id: int
) -> Question:
    question = (
        await session.execute(
            sa.select(Question)
            .where(Question.id == question_id)
            .where(Question.user_id == user_id)
        )
    ).scalar_one_or_none()
    if question is None:
        raise ContextPackFailedError("session_answer 引用了不存在的 question")
    return question


async def _record_coach_question(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    session_id: int,
    order_index: int,
    payload: AnswerTurnSubmitIn,
    user_id: int,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with sessionmaker() as session:
        quiz_session = await answer_service.load_owned_session(
            session, session_id, user_id=user_id
        )
        if quiz_session.status != "in_progress":
            raise answer_service.SessionNotInProgressError(
                f"session {session_id} 当前状态为 {quiz_session.status},不能继续答题"
            )

        answer = await _load_owned_answer(
            session,
            session_id=session_id,
            order_index=order_index,
            user_id=user_id,
        )
        if answer.judged_at is None or not (
            answer.coach_message
            or (answer.remediation_state or {}).get("remediation_prompt")
        ):
            raise ValidationError("请先提交本题答案并等教练反馈后再追问")

        question = await _load_owned_question(
            session, answer.question_id, user_id=user_id
        )
        judge_context_chunk_ids = answer_judge_service.judge_context_chunk_ids(
            quiz_session=quiz_session,
            questions=[question],
        )
        await _ensure_judge_context_chunks_exist(
            session, judge_context_chunk_ids, user_id=user_id
        )

        round_index = int(
            (
                await session.execute(
                    sa.select(sa.func.count(SessionEvent.id))
                    .where(SessionEvent.session_id == session_id)
                    .where(SessionEvent.user_id == user_id)
                    .where(SessionEvent.answer_id == answer.id)
                    .where(SessionEvent.event_type == "coach_answered")
                )
            ).scalar_one()
        )
        turn = {
            "round_index": round_index,
            "turn_type": "coach_question",
            "text": payload.text,
            "client_turn_id": payload.client_turn_id,
            "submitted_at": now.isoformat(),
        }
        session.add(
            SessionEvent(
                user_id=user_id,
                session_id=session_id,
                answer_id=answer.id,
                question_id=answer.question_id,
                event_type="coach_question_asked",
                agent_node="coach_question",
                round_index=round_index,
                payload=turn,
            )
        )
        await session.commit()
        return {
            "answer_id": answer.id,
            "question_id": answer.question_id,
            "round_index": round_index,
            "submitted_at": now.isoformat(),
        }


async def _classify_auto_turn_type(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    session_id: int,
    order_index: int,
    text: str,
    user_id: int,
) -> Literal["initial", "remediation", "coach_question"]:
    async with sessionmaker() as session:
        answer = await _load_owned_answer(
            session,
            session_id=session_id,
            order_index=order_index,
            user_id=user_id,
        )
        answer_turns = list(answer.answer_turns or [])
        if not answer_turns or not (answer.user_answer or "").strip():
            return "initial"
        return _classify_text_turn_type(text)


def _classify_text_turn_type(text: str) -> Literal["remediation", "coach_question"]:
    normalized = _normalize_turn_text(text)
    if _has_explicit_answer_marker(normalized):
        return "remediation"
    if _has_explicit_question_marker(normalized):
        return "coach_question"
    if _looks_like_long_technical_answer(normalized):
        return "remediation"
    return "coach_question"


def _normalize_turn_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _has_explicit_answer_marker(text: str) -> bool:
    return any(marker in text for marker in ANSWER_MARKERS)


def _has_explicit_question_marker(text: str) -> bool:
    return (
        "?" in text
        or "？" in text
        or any(marker in text for marker in QUESTION_MARKERS)
    )


def _looks_like_long_technical_answer(text: str) -> bool:
    if len(text) < 80:
        return False
    term_hits = sum(1 for term in TECH_TERMS if term in text)
    connector_hits = sum(1 for connector in ANSWER_CONNECTORS if connector in text)
    return term_hits >= 2 and connector_hits >= 1


async def _append_answer_turn(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    session_id: int,
    order_index: int,
    payload: AnswerTurnSubmitIn,
    user_id: int,
) -> dict[str, int]:
    now = datetime.now(UTC)
    async with sessionmaker() as session:
        quiz_session = await answer_service.load_owned_session(
            session, session_id, user_id=user_id
        )
        if quiz_session.status != "in_progress":
            raise answer_service.SessionNotInProgressError(
                f"session {session_id} 当前状态为 {quiz_session.status},不能继续答题"
            )

        answer = await _load_owned_answer(
            session,
            session_id=session_id,
            order_index=order_index,
            user_id=user_id,
        )
        question = await _load_owned_question(
            session, answer.question_id, user_id=user_id
        )
        judge_context_chunk_ids = answer_judge_service.judge_context_chunk_ids(
            quiz_session=quiz_session,
            questions=[question],
        )
        await _ensure_judge_context_chunks_exist(
            session, judge_context_chunk_ids, user_id=user_id
        )

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
        answer.coach_message = None
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
                user_id=user_id,
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


async def _load_finish_questions(
    sessionmaker: async_sessionmaker[AsyncSession],
    session_id: int,
    *,
    user_id: int,
) -> list[_FinishQuestion]:
    async with sessionmaker() as session:
        quiz_session = await answer_service.load_owned_session(
            session, session_id, user_id=user_id
        )
        if quiz_session.status != "in_progress":
            raise answer_service.SessionNotInProgressError(
                f"session {session_id} 当前状态为 {quiz_session.status},不能结束本场"
            )

        answers = list(
            (
                await session.execute(
                    sa.select(SessionAnswer)
                    .where(SessionAnswer.session_id == session_id)
                    .where(SessionAnswer.user_id == user_id)
                    .order_by(SessionAnswer.order_index)
                )
            )
            .scalars()
            .all()
        )
        if not answers:
            raise NotFoundError(f"session {session_id} 下没有答案行")

        not_ready = [
            answer.order_index
            for answer in answers
            if not (answer.user_answer or "").strip()
            or answer.judged_at is None
            or answer.coverage_score is None
            or answer.fidelity_score is None
            or answer.depth_score is None
            or answer.total_score is None
        ]
        if not_ready:
            raise SessionNotReadyToFinishError(
                "请先逐题发送答案或补答,并等评分完成后再结束本场",
                errors=[{"order_index": idx} for idx in not_ready],
            )

        question_ids = [answer.question_id for answer in answers]
        questions = list(
            (
                await session.execute(
                    sa.select(Question)
                    .where(Question.id.in_(question_ids))
                    .where(Question.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )
        question_by_id = {question.id: question for question in questions}
        if len(question_by_id) != len(set(question_ids)):
            raise ContextPackFailedError("session_answers 引用了不存在的 question")

        judge_events = list(
            (
                await session.execute(
                    sa.select(SessionEvent)
                    .where(SessionEvent.session_id == session_id)
                    .where(SessionEvent.user_id == user_id)
                    .where(SessionEvent.event_type == "judge_completed")
                    .order_by(SessionEvent.id)
                )
            )
            .scalars()
            .all()
        )
        history_by_answer_id: dict[int, list[dict[str, float]]] = {}
        for event in judge_events:
            if event.answer_id is None:
                continue
            scores = event.payload.get("scores") if isinstance(event.payload, dict) else None
            if not isinstance(scores, dict):
                continue
            history_by_answer_id.setdefault(event.answer_id, []).append(
                {
                    "coverage": _score_value(scores.get("coverage")),
                    "fidelity": _score_value(scores.get("fidelity")),
                    "depth": _score_value(scores.get("depth")),
                    "total": _score_value(scores.get("total")),
                }
            )

        out: list[_FinishQuestion] = []
        for answer in answers:
            question = question_by_id[answer.question_id]
            out.append(
                _FinishQuestion(
                    answer_id=answer.id,
                    order_index=answer.order_index,
                    question_id=question.id,
                    question_type=question.type,
                    prompt=question.prompt,
                    evidence_chunk_ids=list(question.evidence_chunk_ids or []),
                    reference_answer=question.reference_answer,
                    scoring_points=list(question.scoring_points or []),
                    user_answer=answer.user_answer or "",
                    answer_turns=list(answer.answer_turns or []),
                    remediation_state=dict(answer.remediation_state or {}),
                    coach_message=answer.coach_message,
                    coverage_score=_score_value(answer.coverage_score),
                    coverage_evidence=dict(answer.coverage_evidence or {}),
                    fidelity_score=_score_value(answer.fidelity_score),
                    fidelity_evidence=dict(answer.fidelity_evidence or {}),
                    depth_score=_score_value(answer.depth_score),
                    depth_evidence=dict(answer.depth_evidence or {}),
                    total_score=_score_value(answer.total_score),
                    judge_score_history=history_by_answer_id.get(answer.id, []),
                )
            )
        return out


async def _summarize_and_finish_session(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    session_id: int,
    questions: list[_FinishQuestion],
    user_id: int,
) -> dict[str, Any]:
    coverage = _average([question.coverage_score for question in questions])
    fidelity = _average([question.fidelity_score for question in questions])
    depth = _average([question.depth_score for question in questions])
    total = compute_total_score(coverage, fidelity, depth)
    scores = {
        "coverage": coverage,
        "fidelity": fidelity,
        "depth": depth,
        "total": total,
    }

    async with sessionmaker() as session:
        quiz_session = await answer_service.load_owned_session(
            session, session_id, user_id=user_id
        )
        if quiz_session.status != "in_progress":
            raise answer_service.SessionNotInProgressError(
                f"session {session_id} 当前状态为 {quiz_session.status},不能结束本场"
            )

        now = datetime.now(UTC)
        summary = _build_session_summary(
            session_id=session_id,
            query=quiz_session.query,
            mode=quiz_session.mode,
            scores=_scores_for_event(scores),
            questions=questions,
            final_context_chunk_ids=list(quiz_session.final_context_chunk_ids or []),
            finished_at=now,
        )
        markdown = summary.get("markdown")
        if not isinstance(markdown, str):
            raise recall_service.RecallWriteFailedError("session summary markdown 缺失")
        recall_md_path = recall_service.write_session_summary_markdown(
            session_id, markdown, user_id=user_id
        )
        state = dict(quiz_session.agent_state or {})
        state.update(
            {
                "last_agent_node": "finish_session",
                "next_action": "finish",
                "current_question_index": questions[-1].order_index if questions else 0,
                "unresolved_gaps": summary.get("recurring_gaps", []),
                "question_summaries": summary.get("question_summaries", []),
                "summary_context_pack": summary.get("context_pack", {}),
                "final_summary": summary,
            }
        )

        quiz_session.status = "submitted"
        quiz_session.coverage_score = answer_judge_service.score_decimal(coverage)
        quiz_session.fidelity_score = answer_judge_service.score_decimal(fidelity)
        quiz_session.depth_score = answer_judge_service.score_decimal(depth)
        quiz_session.total_score = answer_judge_service.score_decimal(total)
        quiz_session.submitted_at = now
        quiz_session.recall_md_path = recall_md_path
        quiz_session.last_agent_node = "finish_session"
        quiz_session.agent_state = state
        quiz_session.updated_at = now
        session.add(
            SessionEvent(
                user_id=user_id,
                session_id=session_id,
                answer_id=None,
                question_id=None,
                event_type="session_summarized",
                agent_node="summarize_session",
                round_index=0,
                payload=summary,
            )
        )
        session.add(
            SessionEvent(
                user_id=user_id,
                session_id=session_id,
                answer_id=None,
                question_id=None,
                event_type="session_finished",
                agent_node="finish_session",
                round_index=0,
                payload={
                    "scores": _scores_for_event(scores),
                    "recall_md_path": recall_md_path,
                    "finished_at": now.isoformat(),
                },
            )
        )
        await session.commit()

    return {
        "session_id": session_id,
        "scores": _scores_for_event(scores),
        "summary": summary,
        "recall_md_path": recall_md_path,
    }


async def _build_context_pack(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    session_id: int,
    order_index: int,
    user_id: int,
) -> _TurnContext:
    async with sessionmaker() as session:
        quiz_session = await answer_service.load_owned_session(
            session, session_id, user_id=user_id
        )
        answer = await _load_owned_answer(
            session,
            session_id=session_id,
            order_index=order_index,
            user_id=user_id,
        )
        question = await _load_owned_question(
            session, answer.question_id, user_id=user_id
        )

        total_questions = int(
            (
                await session.execute(
                    sa.select(sa.func.count(SessionAnswer.id))
                    .where(SessionAnswer.session_id == session_id)
                    .where(SessionAnswer.user_id == user_id)
                )
            ).scalar_one()
        )
        judge_context_chunk_ids = answer_judge_service.judge_context_chunk_ids(
            quiz_session=quiz_session,
            questions=[question],
        )
        await _ensure_judge_context_chunks_exist(
            session, judge_context_chunk_ids, user_id=user_id
        )

        judge_context = await answer_judge_service.load_judge_context(
            session,
            judge_context_chunk_ids,
            user_id=user_id,
        )
        local_question = answer_judge_service.question_to_local(
            question,
            judge_context.chunk_ids,
        )
        answer_turns = list(answer.answer_turns or [])
        remediation_state = dict(answer.remediation_state or {})
        compacted = _should_compact_context(answer_turns)
        token_budget_exhausted = _token_budget_exhausted(answer_turns)
        prior_turn_summary = (
            _build_prior_turn_summary(answer_turns, remediation_state)
            if compacted
            else None
        )

        return _TurnContext(
            quiz_session=quiz_session,
            answer=answer,
            question=question,
            local_question=local_question,
            chunks=judge_context.chunks,
            judge_context_chunk_ids=judge_context.chunk_ids,
            total_questions=total_questions,
            compacted=compacted,
            prior_turn_summary=prior_turn_summary,
            token_budget_exhausted=token_budget_exhausted,
        )


async def _ensure_judge_context_chunks_exist(
    session: AsyncSession,
    judge_context_chunk_ids: list[int],
    *,
    user_id: int,
) -> None:
    if not judge_context_chunk_ids:
        raise ContextPackFailedError(
            "这个 session 没有关联可用于评分的笔记证据,请重新出题后再提交。"
        )
    existing_ids = set(
        (
            await session.execute(
                sa.select(NoteChunk.id)
                .where(NoteChunk.id.in_(judge_context_chunk_ids))
                .where(NoteChunk.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )
    missing = [
        chunk_id
        for chunk_id in judge_context_chunk_ids
        if chunk_id not in existing_ids
    ]
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
    answer_turns: list[dict[str, Any]] | None = None,
    remediation_state: dict[str, Any] | None = None,
    token_budget_exhausted: bool = False,
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

    remediation: _Decision | None = None
    if fabricated_ratio > FABRICATED_REMEDIATE_RATIO:
        remediation = _remediate(
            "fidelity",
            "fabricated ratio 过高",
            _fidelity_prompt(fabricated_claims),
        )
    elif scores["coverage"] < COVERAGE_REMEDIATE_THRESHOLD:
        remediation = _remediate(
            "coverage",
            "coverage 未达阈值",
            _coverage_prompt(question, coverage_gaps),
        )
    elif missing_depth:
        remediation = _remediate(
            "depth",
            "depth 缺少关键维度",
            _depth_prompt(missing_depth),
        )

    if remediation is not None:
        if token_budget_exhausted:
            return _advance_or_summarize(
                order_index,
                total_questions,
                "token_budget",
                triggered_by=remediation.triggered_by,
                decision_reason="上下文已达到预算退出条件,先收住当前题并总结缺口",
                unresolved_gaps=remediation.unresolved_gaps,
            )
        if _should_exit_no_meaningful_improvement(
            scores=scores,
            answer_turns=answer_turns or [],
            remediation_state=remediation_state or {},
            unresolved_gaps=remediation.unresolved_gaps,
        ):
            return _advance_or_summarize(
                order_index,
                total_questions,
                "no_meaningful_improvement",
                triggered_by=remediation.triggered_by,
                decision_reason="连续多轮补答无明显提升,先收住当前题并总结缺口",
                unresolved_gaps=remediation.unresolved_gaps,
            )
        return remediation

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
    *,
    triggered_by: Trigger = "none",
    decision_reason: str | None = None,
    unresolved_gaps: list[dict[str, Any]] | None = None,
) -> _Decision:
    if order_index >= total_questions - 1:
        return _Decision(
            next_action="summarize",
            triggered_by=triggered_by,
            decision_reason=decision_reason or "当前题已达到进入总结条件",
            exit_reason=exit_reason,
            remediation_prompt=None,
            unresolved_gaps=unresolved_gaps or [],
        )
    return _Decision(
        next_action="ask_next",
        triggered_by=triggered_by,
        decision_reason=decision_reason or "当前题已达到进入下一题条件",
        exit_reason=exit_reason,
        remediation_prompt=None,
        unresolved_gaps=unresolved_gaps or [],
    )


def _should_exit_no_meaningful_improvement(
    *,
    scores: dict[str, float],
    answer_turns: list[dict[str, Any]],
    remediation_state: dict[str, Any],
    unresolved_gaps: list[dict[str, Any]],
) -> bool:
    if len(answer_turns) < NO_MEANINGFUL_IMPROVEMENT_MIN_TURNS:
        return False

    history = _score_history_from_state(remediation_state)
    if len(history) < 2:
        return False

    previous_previous_total = _score_value(history[-2].get("total"))
    previous_total = _score_value(history[-1].get("total"))
    current_total = _score_value(scores.get("total"))
    previous_delta = previous_total - previous_previous_total
    current_delta = current_total - previous_total
    if (
        previous_delta >= NO_MEANINGFUL_IMPROVEMENT_DELTA
        or current_delta >= NO_MEANINGFUL_IMPROVEMENT_DELTA
    ):
        return False

    previous_gap_keys = _gap_keys(remediation_state.get("unresolved_gaps"))
    current_gap_keys = _gap_keys(unresolved_gaps)
    if previous_gap_keys and current_gap_keys and current_gap_keys != previous_gap_keys:
        return False

    return True


def _score_history_from_state(state: dict[str, Any]) -> list[dict[str, float]]:
    raw = state.get("judge_score_history")
    if not isinstance(raw, list):
        return []
    history: list[dict[str, float]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        history.append(
            {
                "coverage": _score_value(item.get("coverage")),
                "fidelity": _score_value(item.get("fidelity")),
                "depth": _score_value(item.get("depth")),
                "total": _score_value(item.get("total")),
            }
        )
    return history[-MAX_JUDGE_SCORE_HISTORY:]


def _gap_keys(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    keys: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        gap_type = str(item.get("type") or "")
        if gap_type == "coverage":
            key = item.get("scoring_point_id")
        elif gap_type == "fidelity":
            key = item.get("claim")
        elif gap_type == "depth":
            key = item.get("dimension")
        else:
            key = item
        keys.add(f"{gap_type}:{key}")
    return keys


def _should_compact_context(answer_turns: list[dict[str, Any]]) -> bool:
    return len(answer_turns) >= CONTEXT_COMPACTION_TURN_THRESHOLD


def _token_budget_exhausted(answer_turns: list[dict[str, Any]]) -> bool:
    return len(answer_turns) >= TOKEN_BUDGET_EXIT_TURN_THRESHOLD


def _build_prior_turn_summary(
    answer_turns: list[dict[str, Any]],
    remediation_state: dict[str, Any],
) -> str:
    history = _score_history_from_state(remediation_state)
    first_total = history[0]["total"] if history else None
    last_total = history[-1]["total"] if history else None
    gap_keys = sorted(_gap_keys(remediation_state.get("unresolved_gaps")))

    lines = [f"用户已提交 {len(answer_turns)} 轮答案。"]
    if first_total is not None and last_total is not None:
        delta = last_total - first_total
        lines.append(
            f"最近总分从 {first_total:.2f} 到 {last_total:.2f},变化 {delta:+.2f}。"
        )
    if gap_keys:
        lines.append("当前仍未解决的缺口:" + "、".join(gap_keys))
    lines.append("旧轮次反馈已压缩,不再逐条传入当前上下文。")
    return "".join(lines)


def _coverage_prompt(
    question: Question,
    coverage_gaps: list[Any],
) -> dict[str, Any]:
    point_by_id = {str(point["id"]): point for point in question.scoring_points}
    gap_ids = [point.id for point in coverage_gaps]
    gap_texts = [
        str(point_by_id.get(point_id, {}).get("text", point_id))
        for point_id in gap_ids
    ]
    supporting_chunk_ids = _unique_ids(
        chunk_id
        for point_id in gap_ids
        for chunk_id in point_by_id.get(point_id, {}).get("supporting_chunk_ids", [])
    )
    return {
        "text": "你这题还漏了关键采分点: "
        + "；".join(gap_texts[:3])
        + "。请基于笔记证据补充这些点。",
        "triggered_by": "coverage",
        "missing_scoring_point_ids": gap_ids,
        "fabricated_claim_ids": [],
        "missing_depth_dimensions": [],
        "supporting_chunk_ids": supporting_chunk_ids,
        "unresolved_gaps": [
            {"type": "coverage", "scoring_point_id": point_id}
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
        "missing_scoring_point_ids": [],
        "fabricated_claim_ids": list(range(len(fabricated_claims))),
        "missing_depth_dimensions": [],
        "supporting_chunk_ids": [],
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
        "missing_scoring_point_ids": [],
        "fabricated_claim_ids": [],
        "missing_depth_dimensions": missing_depth,
        "supporting_chunk_ids": [],
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
    scores: dict[str, float],
    user_id: int,
) -> None:
    now = datetime.now(UTC)
    async with sessionmaker() as session:
        answer = (
            await session.execute(
                sa.select(SessionAnswer)
                .where(SessionAnswer.id == answer_id)
                .where(SessionAnswer.user_id == user_id)
            )
        ).scalar_one_or_none()
        previous_state = dict(answer.remediation_state or {}) if answer is not None else {}
        score_history = _score_history_from_state(previous_state)
        score_history.append(_scores_for_event(scores))
        score_history = score_history[-MAX_JUDGE_SCORE_HISTORY:]
        state = {
            "last_decision": decision.next_action,
            "triggered_by": decision.triggered_by,
            "decision_reason": decision.decision_reason,
            "exit_reason": decision.exit_reason,
            "remediation_prompt": decision.remediation_prompt,
            "unresolved_gaps": decision.unresolved_gaps,
            "judge_score_history": score_history,
        }
        await session.execute(
            sa.update(SessionAnswer)
            .where(SessionAnswer.id == answer_id)
            .where(SessionAnswer.user_id == user_id)
            .values(remediation_state=state, updated_at=now)
        )
        quiz_session = (
            await session.execute(
                sa.select(QuizSession)
                .where(QuizSession.id == session_id)
                .where(QuizSession.user_id == user_id)
            )
        ).scalar_one_or_none()
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
                user_id=user_id,
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
                    user_id=user_id,
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
    user_id: int,
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
                user_id=user_id,
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
            {"type": "coverage", "scoring_point_id": point.id}
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


def _answer_scores(answer: SessionAnswer) -> dict[str, float | None]:
    return {
        "coverage": _maybe_float(answer.coverage_score),
        "fidelity": _maybe_float(answer.fidelity_score),
        "depth": _maybe_float(answer.depth_score),
        "total": _maybe_float(answer.total_score),
    }


def _maybe_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _score_value(value: Any) -> float:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _build_session_summary(
    *,
    session_id: int,
    query: str,
    mode: str,
    scores: dict[str, float],
    questions: list[_FinishQuestion],
    final_context_chunk_ids: list[int],
    finished_at: datetime,
) -> dict[str, Any]:
    question_summaries: list[dict[str, Any]] = []
    gap_counts: dict[str, dict[str, Any]] = {}
    remediation_wins: list[str] = []

    for question in questions:
        coverage_gaps = _coverage_gaps(question)
        fabricated_claims = _fabricated_claims(question)
        missing_depth = _missing_depth(question)
        first_total = (
            question.judge_score_history[0]["total"]
            if question.judge_score_history
            else question.total_score
        )
        score_delta = round(question.total_score - first_total, 2)
        improved = len(question.answer_turns) > 1 and score_delta >= 5.0
        if improved:
            remediation_wins.append(
                f"第 {question.order_index + 1} 题补答后总分提升 {score_delta:.0f} 分"
            )

        _count_gap(
            gap_counts,
            key="coverage",
            label="采分点遗漏或只答到部分",
            amount=len(coverage_gaps),
            examples=[gap["text"] for gap in coverage_gaps],
        )
        _count_gap(
            gap_counts,
            key="fidelity",
            label="存在缺少笔记证据的说法",
            amount=len(fabricated_claims),
            examples=fabricated_claims,
        )
        for dimension in missing_depth:
            _count_gap(
                gap_counts,
                key=f"depth:{dimension}",
                label=f"Depth 缺少{_depth_label(dimension)}",
                amount=1,
                examples=[f"第 {question.order_index + 1} 题"],
            )

        question_summaries.append(
            {
                "order_index": question.order_index,
                "question_id": question.question_id,
                "prompt": question.prompt,
                "scores": {
                    "coverage": round(question.coverage_score, 2),
                    "fidelity": round(question.fidelity_score, 2),
                    "depth": round(question.depth_score, 2),
                    "total": round(question.total_score, 2),
                },
                "round_count": len(question.answer_turns),
                "improved_by_remediation": improved,
                "score_delta": score_delta,
                "coverage_gaps": coverage_gaps,
                "fabricated_claims": fabricated_claims,
                "missing_depth_dimensions": missing_depth,
                "coach_message": question.coach_message,
                "status": _question_status(question.total_score, coverage_gaps, fabricated_claims),
            }
        )

    recurring_gaps = [
        {
            "type": key.split(":", 1)[0],
            "key": key,
            "label": value["label"],
            "count": value["count"],
            "examples": value["examples"][:3],
        }
        for key, value in sorted(
            gap_counts.items(),
            key=lambda item: (-int(item[1]["count"]), item[0]),
        )
        if int(value["count"]) > 0
    ]
    strengths = _summary_strengths(scores, questions)
    suggestions = _review_suggestions(recurring_gaps)
    headline = _summary_headline(scores, recurring_gaps)
    context_pack = {
        "version": "session_summary_v1",
        "source": "deterministic_from_judge_events",
        "question_count": len(questions),
        "final_context_chunk_ids": final_context_chunk_ids,
        "compacted_fields": [
            "question_summaries",
            "recurring_gaps",
            "remediation_wins",
            "review_suggestions",
        ],
    }
    summary = {
        "session_id": session_id,
        "query": query,
        "mode": mode,
        "finished_at": finished_at.isoformat(),
        "headline": headline,
        "scores": scores,
        "strengths": strengths,
        "recurring_gaps": recurring_gaps,
        "remediation_wins": remediation_wins,
        "review_suggestions": suggestions,
        "question_summaries": question_summaries,
        "context_pack": context_pack,
    }
    summary["markdown"] = _summary_markdown(summary, questions)
    return summary


def _coverage_gaps(question: _FinishQuestion) -> list[dict[str, Any]]:
    point_by_id = {
        str(point.get("id")): point
        for point in question.scoring_points
        if isinstance(point, dict) and point.get("id") is not None
    }
    points = question.coverage_evidence.get("points")
    if not isinstance(points, list):
        return []
    gaps: list[dict[str, Any]] = []
    for point in points:
        if not isinstance(point, dict) or point.get("label") not in ("partial", "miss"):
            continue
        point_id = str(point.get("id", ""))
        scoring_point = point_by_id.get(point_id, {})
        gaps.append(
            {
                "id": point_id,
                "label": point.get("label"),
                "text": str(scoring_point.get("text") or point_id or "未命名采分点"),
            }
        )
    return gaps


def _fabricated_claims(question: _FinishQuestion) -> list[str]:
    claims = question.fidelity_evidence.get("claims")
    if not isinstance(claims, list):
        return []
    return [
        str(claim.get("text"))
        for claim in claims
        if isinstance(claim, dict) and claim.get("label") == "fabricated" and claim.get("text")
    ]


def _missing_depth(question: _FinishQuestion) -> list[str]:
    dimensions = question.depth_evidence.get("dimensions")
    if not isinstance(dimensions, dict):
        return []
    missing: list[str] = []
    for key, value in dimensions.items():
        if isinstance(value, dict) and value.get("covered") is False:
            missing.append(str(key))
    return missing


def _count_gap(
    gap_counts: dict[str, dict[str, Any]],
    *,
    key: str,
    label: str,
    amount: int,
    examples: list[str],
) -> None:
    if amount <= 0:
        return
    item = gap_counts.setdefault(key, {"label": label, "count": 0, "examples": []})
    item["count"] += amount
    for example in examples:
        text = example.strip()
        if text and text not in item["examples"]:
            item["examples"].append(text)


def _summary_headline(scores: dict[str, float], recurring_gaps: list[dict[str, Any]]) -> str:
    total = scores["total"]
    if total >= 85 and not recurring_gaps:
        return "这场整体很稳:覆盖、依据和深度都达到了继续加难度的水平。"
    if total >= 80:
        return "这场整体达标,主要收益在于把零散答案收束成了更完整的面试表达。"
    if total >= 60:
        return "这场已经能答出主线,下一步要集中补齐反复出现的漏点和深度维度。"
    return "这场暴露了比较明确的复习缺口,建议先回到笔记把核心概念补牢。"


def _summary_strengths(
    scores: dict[str, float],
    questions: list[_FinishQuestion],
) -> list[str]:
    strengths: list[str] = []
    if scores["coverage"] >= 80:
        strengths.append("多数题能覆盖核心采分点。")
    if scores["fidelity"] >= 90:
        strengths.append("答案基本能回到笔记证据,凭空发挥较少。")
    if scores["depth"] >= 80:
        strengths.append("取舍、原因和边界讲得比较完整。")
    best = max(questions, key=lambda question: question.total_score, default=None)
    if best is not None and not strengths:
        strengths.append(
            f"第 {best.order_index + 1} 题表现最好,可以把这题的表达方式迁移到其他题。"
        )
    return strengths


def _review_suggestions(recurring_gaps: list[dict[str, Any]]) -> list[str]:
    if not recurring_gaps:
        return ["保留这次答题节奏,下一轮可以提高题目数量或选择更宽的主题。"]
    suggestions: list[str] = []
    keys = {str(gap.get("key")) for gap in recurring_gaps}
    if "coverage" in keys:
        suggestions.append("复盘每题采分点,把漏答项整理成 3-5 条短句再口述一遍。")
    if "fidelity" in keys:
        suggestions.append("遇到不确定说法时先说明证据来源,不要把行业常识当成本题笔记事实。")
    if any(key.startswith("depth:") for key in keys):
        suggestions.append("每个概念补一层 why / trade-off / boundary,避免只给定义。")
    return suggestions


def _question_status(
    total: float,
    coverage_gaps: list[dict[str, Any]],
    fabricated_claims: list[str],
) -> str:
    if total >= 85 and not coverage_gaps and not fabricated_claims:
        return "strong"
    if total >= 75 and not fabricated_claims:
        return "ok"
    return "needs_review"


def _depth_label(key: str) -> str:
    labels = {
        "tradeoff": "取舍",
        "why": "原因",
        "boundary": "边界",
    }
    return labels.get(key, key)


def _summary_markdown(
    summary: dict[str, Any],
    questions: list[_FinishQuestion],
) -> str:
    lines = [
        f"# 面试练习总结 #{summary['session_id']}",
        "",
        f"- 主题:{summary['query']}",
        f"- 完成时间:{summary['finished_at']}",
        "- 总分:"
        f" Coverage {summary['scores']['coverage']}"
        f" / Fidelity {summary['scores']['fidelity']}"
        f" / Depth {summary['scores']['depth']}"
        f" / Total {summary['scores']['total']}",
        "",
        "## 总结",
        "",
        str(summary["headline"]),
        "",
    ]
    if summary["strengths"]:
        lines.extend(["## 做得好的地方", ""])
        lines.extend(f"- {item}" for item in summary["strengths"])
        lines.append("")
    if summary["recurring_gaps"]:
        lines.extend(["## 反复缺口", ""])
        for gap in summary["recurring_gaps"]:
            lines.append(f"- {gap['label']}: {gap['count']} 处")
        lines.append("")
    if summary["remediation_wins"]:
        lines.extend(["## 补答修正", ""])
        lines.extend(f"- {item}" for item in summary["remediation_wins"])
        lines.append("")
    lines.extend(["## 复习建议", ""])
    lines.extend(f"- {item}" for item in summary["review_suggestions"])
    lines.append("")

    question_by_order = {question.order_index: question for question in questions}
    lines.extend(["## 每题回放", ""])
    for item in summary["question_summaries"]:
        question = question_by_order.get(int(item["order_index"]))
        lines.extend(
            [
                f"### 第 {int(item['order_index']) + 1} 题",
                "",
                f"题目:{item['prompt']}",
                "",
                "分数:"
                f" Coverage {item['scores']['coverage']}"
                f" / Fidelity {item['scores']['fidelity']}"
                f" / Depth {item['scores']['depth']}"
                f" / Total {item['scores']['total']}",
                "",
            ]
        )
        if question is not None:
            lines.extend(
                [
                    "我的累计答案:",
                    "",
                    question.user_answer,
                    "",
                    "Reference:",
                    "",
                    question.reference_answer,
                    "",
                ]
            )
        if item["coverage_gaps"]:
            lines.append("Coverage 缺口:")
            lines.extend(f"- {gap['text']}" for gap in item["coverage_gaps"])
            lines.append("")
        if item["missing_depth_dimensions"]:
            lines.append(
                "Depth 缺口:"
                + "、".join(_depth_label(str(key)) for key in item["missing_depth_dimensions"])
            )
            lines.append("")
        if item.get("coach_message"):
            lines.extend(["教练反馈:", "", str(item["coach_message"]), ""])
    return "\n".join(lines).strip() + "\n"


def _extract_coach_chat_output(llm_result: Any) -> CoachChatOutput:
    if not isinstance(llm_result.parsed, CoachChatOutput):
        raise CoachCallFailedError("coach_chat 没返回有效的 CoachChatOutput")
    message = llm_result.parsed.coach_message.strip()
    if not message:
        raise CoachCallFailedError("coach_message 不能为空")
    return CoachChatOutput(coach_message=message)


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
