"""答题 + 评分编排 service(M2)。

职责:
- PUT 草稿落库(同步,无 LLM)
- POST submit:逐题调 answer_judge agent → Python 算分 → 落 session_answers + 累计 total_score → SSE 推 progress / score / done
- evidence 后处理(完整性校验、[N] → DB id 反向映射)
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobcopilot_api.agents.answer_judge.scoring import (
    total_score as compute_total_score,
)
from jobcopilot_api.errors import JobCopilotError, NotFoundError
from jobcopilot_api.llm.errors import LLMError
from jobcopilot_api.models.question import Question
from jobcopilot_api.models.quiz_session import QuizSession
from jobcopilot_api.models.session_answer import SessionAnswer
from jobcopilot_api.models.session_event import SessionEvent
from jobcopilot_api.schemas.agents.answer_judge import AnswerJudgeInput
from jobcopilot_api.schemas.agents.quiz_generator import (
    GeneratedQuestion,
    QuizGenChunkInput,
)
from jobcopilot_api.services import answer_judge_service, billing_service, recall_service
from jobcopilot_api.services.answer_judge_service import (
    JudgeCallFailedError,
    JudgeIntegrityError,
)
from jobcopilot_api.schemas.quiz import (
    AnswerDraftIn,
    QuizQuestionDetailOut,
    QuizScoresOut,
    QuizSessionDetailOut,
    QuizSessionListItemOut,
    QuizSessionListOut,
    QuestionPublic,
)


class SessionNotInProgressError(JobCopilotError):
    status_code = 409
    code = "session_not_in_progress"
    title = "会话不是可答题状态"


class UnansweredQuestionsError(JobCopilotError):
    status_code = 409
    code = "unanswered_questions"
    title = "还有题目未作答"


@dataclass(frozen=True)
class _JudgeTask:
    answer_id: int
    order_index: int
    user_answer: str
    question: GeneratedQuestion
    chunks: list[QuizGenChunkInput]
    judge_context_chunk_ids: list[int]


async def load_owned_session(
    session: AsyncSession,
    session_id: int,
    *,
    user_id: int,
) -> QuizSession:
    """P0:按 (id, user_id) 取会话。全仓所有 quiz_session 读写的唯一入口。

    别人的 session 与不存在的 session 返回同一个 404。
    """
    quiz_session = (
        await session.execute(
            sa.select(QuizSession)
            .where(QuizSession.id == session_id)
            .where(QuizSession.user_id == user_id)
        )
    ).scalar_one_or_none()
    if quiz_session is None:
        raise NotFoundError(f"quiz_session {session_id} 不存在")
    return quiz_session


async def get_session_detail(
    session: AsyncSession,
    session_id: int,
    *,
    user_id: int,
) -> QuizSessionDetailOut:
    quiz_session = await load_owned_session(session, session_id, user_id=user_id)

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
        raise JudgeIntegrityError("session_answers 引用了不存在的 question")

    answer_ids = [answer.id for answer in answers]
    judge_turns_by_answer_id: dict[int, list[dict[str, Any]]] = {
        answer_id: [] for answer_id in answer_ids
    }
    coach_turns_by_answer_id: dict[int, list[dict[str, Any]]] = {
        answer_id: [] for answer_id in answer_ids
    }
    if answer_ids:
        turn_events = list(
            (
                await session.execute(
                    sa.select(SessionEvent)
                    .where(SessionEvent.answer_id.in_(answer_ids))
                    .where(SessionEvent.user_id == user_id)
                    .where(
                        SessionEvent.event_type.in_(
                            [
                                "judge_completed",
                                "decision_made",
                                "remediation_prompted",
                                "coach_answered",
                            ]
                        )
                    )
                    .order_by(SessionEvent.id)
                )
            )
            .scalars()
            .all()
        )
        judge_by_answer_and_round: dict[tuple[int, int], dict[str, Any]] = {}
        for event in turn_events:
            if event.answer_id is None:
                continue
            payload = dict(event.payload or {})
            if event.event_type == "coach_answered":
                payload.setdefault("round_index", event.round_index)
                payload.setdefault("turn_type", "coach_question")
                payload.setdefault("answered_at", event.created_at.isoformat())
                coach_turns_by_answer_id.setdefault(event.answer_id, []).append(payload)
                continue

            key = (event.answer_id, event.round_index)
            turn = judge_by_answer_and_round.setdefault(
                key,
                {
                    "round_index": event.round_index,
                    "turn_type": "judge_feedback",
                },
            )
            if event.event_type == "judge_completed":
                turn["judged_at"] = event.created_at.isoformat()
                turn["scores"] = payload.get("scores")
                turn["coach_message"] = payload.get("coach_message")
            elif event.event_type == "decision_made":
                turn["next_action"] = payload.get("last_decision")
                turn["triggered_by"] = payload.get("triggered_by")
                turn["decision_reason"] = payload.get("decision_reason")
                turn["exit_reason"] = payload.get("exit_reason")
                turn["remediation_prompt"] = payload.get("remediation_prompt")
                turn["unresolved_gaps"] = payload.get("unresolved_gaps")
            elif event.event_type == "remediation_prompted":
                turn["remediation_prompt"] = payload

        for (answer_id, _round_index), turn in sorted(
            judge_by_answer_and_round.items(),
            key=lambda item: (item[0][0], item[0][1]),
        ):
            if not turn.get("coach_message") and not turn.get("scores"):
                continue
            judge_turns_by_answer_id.setdefault(answer_id, []).append(turn)

    include_scoring = quiz_session.status == "submitted"
    question_items: list[QuizQuestionDetailOut] = []
    for answer in answers:
        question = question_by_id[answer.question_id]
        scores = None
        evidence = None
        reference_answer = None
        scoring_points = None
        if include_scoring or answer.judged_at is not None:
            scores = QuizScoresOut(
                coverage=_decimal_to_float(answer.coverage_score),
                fidelity=_decimal_to_float(answer.fidelity_score),
                depth=_decimal_to_float(answer.depth_score),
                total=_decimal_to_float(answer.total_score),
            )
            evidence = {
                "coverage_evidence": answer.coverage_evidence,
                "fidelity_evidence": answer.fidelity_evidence,
                "depth_evidence": answer.depth_evidence,
            }
            reference_answer = question.reference_answer
            scoring_points = question.scoring_points

        question_items.append(
            QuizQuestionDetailOut(
                order_index=answer.order_index,
                question=QuestionPublic(
                    id=question.id,
                    type=question.type,
                    prompt=question.prompt,
                    evidence_chunk_ids=list(question.evidence_chunk_ids),
                ),
                user_answer=answer.user_answer,
                answer_turns=list(answer.answer_turns or []),
                judge_turns=_with_answer_turn_types(
                    judge_turns_by_answer_id.get(answer.id, []),
                    list(answer.answer_turns or []),
                ),
                coach_turns=coach_turns_by_answer_id.get(answer.id, []),
                answer_submitted_at=answer.answer_submitted_at,
                judged=answer.judged_at is not None,
                scores=scores,
                evidence=evidence,
                remediation_state=answer.remediation_state or None,
                next_action=(answer.remediation_state or {}).get("last_decision"),
                remediation_prompt=(answer.remediation_state or {}).get(
                    "remediation_prompt"
                ),
                coach_message=answer.coach_message,
                reference_answer=reference_answer,
                scoring_points=scoring_points,
            )
        )

    session_scores = None
    if include_scoring:
        session_scores = QuizScoresOut(
            coverage=_decimal_to_float(quiz_session.coverage_score),
            fidelity=_decimal_to_float(quiz_session.fidelity_score),
            depth=_decimal_to_float(quiz_session.depth_score),
            total=_decimal_to_float(quiz_session.total_score),
        )

    return QuizSessionDetailOut(
        id=quiz_session.id,
        query=quiz_session.query,
        mode=quiz_session.mode,
        jd_ids=list(quiz_session.jd_ids) if quiz_session.jd_ids is not None else None,
        status=quiz_session.status,
        agent_state=quiz_session.agent_state or None,
        started_at=quiz_session.started_at,
        submitted_at=quiz_session.submitted_at,
        abandoned_at=quiz_session.abandoned_at,
        scores=session_scores,
        recall_md_path=quiz_session.recall_md_path,
        summary=_session_summary(quiz_session.agent_state),
        questions=question_items,
    )


async def list_sessions(
    session: AsyncSession,
    *,
    user_id: int,
    status: str | None = None,
    cursor: int | None = None,
    limit: int = 20,
) -> QuizSessionListOut:
    limit = max(1, min(limit, 100))
    answer_count = (
        sa.select(
            SessionAnswer.session_id.label("session_id"),
            sa.func.count(SessionAnswer.id).label("question_count"),
        )
        .where(SessionAnswer.user_id == user_id)
        .group_by(SessionAnswer.session_id)
        .subquery()
    )
    stmt = (
        sa.select(
            QuizSession,
            sa.func.coalesce(answer_count.c.question_count, 0).label(
                "question_count"
            ),
        )
        .outerjoin(answer_count, answer_count.c.session_id == QuizSession.id)
        .where(QuizSession.user_id == user_id)
        .order_by(QuizSession.id.desc())
        .limit(limit + 1)
    )
    if status:
        stmt = stmt.where(QuizSession.status == status)
    if cursor is not None:
        stmt = stmt.where(QuizSession.id < cursor)

    rows = list((await session.execute(stmt)).all())
    has_more = len(rows) > limit
    visible = rows[:limit]
    items = [
        QuizSessionListItemOut(
            id=quiz_session.id,
            query=quiz_session.query,
            mode=quiz_session.mode,
            status=quiz_session.status,
            started_at=quiz_session.started_at,
            submitted_at=quiz_session.submitted_at,
            total_score=_decimal_to_float(quiz_session.total_score),
            question_count=int(question_count),
        )
        for quiz_session, question_count in visible
    ]
    return QuizSessionListOut(
        items=items,
        next_cursor=items[-1].id if has_more and items else None,
        has_more=has_more,
    )


async def get_session_recall_markdown(
    session: AsyncSession,
    session_id: int,
    *,
    user_id: int,
) -> str:
    quiz_session = await load_owned_session(session, session_id, user_id=user_id)
    file_markdown = recall_service.read_session_summary_markdown(
        quiz_session.recall_md_path, user_id=user_id
    )
    if isinstance(file_markdown, str) and file_markdown.strip():
        return file_markdown

    summary = _session_summary(quiz_session.agent_state)
    markdown = summary.get("markdown") if summary else None
    if not isinstance(markdown, str) or not markdown.strip():
        raise NotFoundError(f"session {session_id} 还没有生成沉淀 markdown")
    return markdown


async def save_draft(
    session: AsyncSession,
    session_id: int,
    order_index: int,
    payload: AnswerDraftIn,
    *,
    user_id: int,
) -> None:
    quiz_session = await load_owned_session(session, session_id, user_id=user_id)
    if quiz_session.status != "in_progress":
        raise SessionNotInProgressError(
            f"session {session_id} 当前状态为 {quiz_session.status},不能继续答题"
        )

    now = datetime.now(UTC)
    result = await session.execute(
        sa.update(SessionAnswer)
        .where(SessionAnswer.session_id == session_id)
        .where(SessionAnswer.user_id == user_id)
        .where(SessionAnswer.order_index == order_index)
        .values(
            user_answer=payload.user_answer,
            answer_submitted_at=now,
            coverage_score=None,
            coverage_evidence=None,
            fidelity_score=None,
            fidelity_evidence=None,
            depth_score=None,
            depth_evidence=None,
            total_score=None,
            coach_message=None,
            judge_model=None,
            judge_prompt_version=None,
            judge_tokens_in=None,
            judge_tokens_out=None,
            judge_cost_cny=None,
            judged_at=None,
            updated_at=now,
        )
    )
    if result.rowcount == 0:
        raise NotFoundError(
            f"session {session_id} 下不存在 order_index={order_index} 的答案"
        )
    await session.commit()


async def submit_session_sse(
    sessionmaker: async_sessionmaker[AsyncSession],
    session_id: int,
    *,
    user_id: int,
) -> AsyncIterator[dict[str, Any]]:
    """评分事件流:逐题 Judge → Python 算分 → 落库 → 汇总 session 分。

    P3 之后由 worker 消费,事件写 `job_events`。
    """
    try:
        tasks = await _load_judge_tasks(sessionmaker, session_id, user_id=user_id)
    except JobCopilotError as e:
        yield _ev("error", _error_payload(e))
        yield _ev("done", {"ok": False})
        return

    yield _ev(
        "started",
        {
            "job_id": f"judge-{session_id}",
            "resource_id": session_id,
            "session_id": session_id,
            "total_questions": len(tasks),
        },
    )

    for task in tasks:
        yield _ev("progress", {"phase": "judging", "order_index": task.order_index})
        judge_input = AnswerJudgeInput(
            question=task.question,
            chunks=task.chunks,
            user_answer=task.user_answer,
        )
        try:
            judged_answer = await answer_judge_service.judge_and_persist_answer(
                sessionmaker,
                answer_id=task.answer_id,
                judge_input=judge_input,
                scoring_points=task.question.scoring_points,
                judge_context_chunk_ids=task.judge_context_chunk_ids,
                user_id=user_id,
            )
        except billing_service.InsufficientBalanceError as e:
            # P1:就地中止。前面已经评完的题目分数已落库,不回滚。
            yield _ev("error", _error_payload(e))
            yield _ev("done", {"ok": False})
            return
        except (LLMError, JudgeCallFailedError) as e:
            detail = str(e) or "Judge 调用失败"
            yield _ev(
                "error",
                {
                    "code": "judge_call_failed",
                    "detail": detail,
                    "order_index": task.order_index,
                },
            )
            yield _ev("done", {"ok": False})
            return
        judged = judged_answer.judged
        scores = judged_answer.scores

        yield _ev(
            "question_done",
            {
                "order_index": task.order_index,
                "scores": _scores_for_event(scores),
                "coach_message": judged.coach_message,
                "evidence": {
                    "coverage_evidence": judged.coverage_evidence.model_dump(
                        mode="json"
                    ),
                    "fidelity_evidence": judged.fidelity_evidence.model_dump(
                        mode="json"
                    ),
                    "depth_evidence": judged.depth_evidence.model_dump(mode="json"),
                },
            },
        )

    try:
        summary = await _finalize_session(sessionmaker, session_id, user_id=user_id)
    except JobCopilotError as e:
        yield _ev("error", _error_payload(e))
        yield _ev("done", {"ok": False})
        return
    yield _ev("result", summary)
    yield _ev("done", {"ok": True})


async def abandon_session(
    session: AsyncSession,
    session_id: int,
    *,
    user_id: int,
) -> dict[str, Any]:
    quiz_session = await load_owned_session(session, session_id, user_id=user_id)
    if quiz_session.status != "in_progress":
        raise SessionNotInProgressError(
            f"session {session_id} 当前状态为 {quiz_session.status},不能放弃"
        )

    now = datetime.now(UTC)
    quiz_session.status = "abandoned"
    quiz_session.abandoned_at = now
    await session.commit()
    return {
        "id": session_id,
        "status": "abandoned",
        "abandoned_at": now,
    }


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


async def _load_judge_tasks(
    sessionmaker: async_sessionmaker[AsyncSession],
    session_id: int,
    *,
    user_id: int,
) -> list[_JudgeTask]:
    async with sessionmaker() as session:
        quiz_session = await load_owned_session(
            session, session_id, user_id=user_id
        )
        if quiz_session.status != "in_progress":
            raise SessionNotInProgressError(
                f"session {session_id} 当前状态为 {quiz_session.status},不能提交"
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

        missing = [
            a.order_index
            for a in answers
            if a.user_answer is None or not a.user_answer.strip()
        ]
        if missing:
            raise UnansweredQuestionsError(
                f"还有题目未作答:{missing}",
                errors=[{"order_index": idx} for idx in missing],
            )

        question_ids = [a.question_id for a in answers]
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
        question_by_id = {q.id: q for q in questions}
        if len(question_by_id) != len(set(question_ids)):
            raise JudgeIntegrityError("session_answers 引用了不存在的 question")

        task_questions = [question_by_id[answer.question_id] for answer in answers]
        judge_context = await answer_judge_service.build_judge_context(
            session,
            quiz_session=quiz_session,
            questions=task_questions,
            user_id=user_id,
        )

        tasks: list[_JudgeTask] = []
        for answer in answers:
            question = question_by_id[answer.question_id]
            local_question = answer_judge_service.question_to_local(
                question,
                judge_context.chunk_ids,
            )
            tasks.append(
                _JudgeTask(
                    answer_id=answer.id,
                    order_index=answer.order_index,
                    user_answer=answer.user_answer or "",
                    question=local_question,
                    chunks=judge_context.chunks,
                    judge_context_chunk_ids=judge_context.chunk_ids,
                )
            )
        return tasks


def _scores_for_event(scores: dict[str, float]) -> dict[str, float]:
    return {key: round(value, 2) for key, value in scores.items()}


def _with_answer_turn_types(
    judge_turns: list[dict[str, Any]],
    answer_turns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    answer_type_by_round = {
        int(turn.get("round_index", index)): turn.get("turn_type")
        for index, turn in enumerate(answer_turns)
    }
    out: list[dict[str, Any]] = []
    for turn in judge_turns:
        item = dict(turn)
        try:
            round_index = int(item.get("round_index", 0))
        except (TypeError, ValueError):
            round_index = 0
        item["answer_turn_type"] = answer_type_by_round.get(round_index)
        out.append(item)
    return out


def _session_summary(agent_state: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(agent_state, dict):
        return None
    summary = agent_state.get("final_summary")
    return summary if isinstance(summary, dict) else None


async def _finalize_session(
    sessionmaker: async_sessionmaker[AsyncSession],
    session_id: int,
    *,
    user_id: int,
) -> dict[str, Any]:
    async with sessionmaker() as session:
        answers = list(
            (
                await session.execute(
                    sa.select(SessionAnswer)
                    .where(SessionAnswer.session_id == session_id)
                    .where(SessionAnswer.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )
        if not answers:
            raise NotFoundError(f"session {session_id} 下没有答案行")

        coverage = _avg_score([a.coverage_score for a in answers])
        fidelity = _avg_score([a.fidelity_score for a in answers])
        depth = _avg_score([a.depth_score for a in answers])
        total = compute_total_score(coverage, fidelity, depth)
        now = datetime.now(UTC)

        await session.execute(
            sa.update(QuizSession)
            .where(QuizSession.id == session_id)
            .where(QuizSession.user_id == user_id)
            .values(
                status="submitted",
                coverage_score=answer_judge_service.score_decimal(coverage),
                fidelity_score=answer_judge_service.score_decimal(fidelity),
                depth_score=answer_judge_service.score_decimal(depth),
                total_score=answer_judge_service.score_decimal(total),
                submitted_at=now,
            )
        )
        await session.commit()

    scores = {
        "coverage": coverage,
        "fidelity": fidelity,
        "depth": depth,
        "total": total,
    }
    return {
        "session_id": session_id,
        "scores": _scores_for_event(scores),
        "recall_md_path": None,
    }


def _avg_score(values: list[Decimal | None]) -> float:
    if not values or any(value is None for value in values):
        raise JudgeIntegrityError("存在未完成评分的答案,不能汇总 session")
    scores = [float(value) for value in values]
    return sum(scores) / len(scores)


def _decimal_to_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None
