"""答题 + 评分编排 service(M2)。

职责:
- PUT 草稿落库(同步,无 LLM)
- POST submit:逐题调 answer_judge agent → Python 算分 → 落 session_answers + 累计 total_score → SSE 推 progress / score / done
- evidence 后处理(完整性校验、[N] → DB id 反向映射)
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobcopilot_api.agents.answer_judge import agent as answer_judge_agent
from jobcopilot_api.agents.answer_judge.prompts import (
    PROMPT_VERSION as JUDGE_PROMPT_VERSION,
)
from jobcopilot_api.agents.answer_judge.scoring import (
    coverage_score,
    depth_score,
    fidelity_score,
    total_score as compute_total_score,
)
from jobcopilot_api.errors import JobCopilotError, NotFoundError
from jobcopilot_api.llm.client import LLMResult
from jobcopilot_api.llm.errors import LLMError
from jobcopilot_api.models.note_chunk import NoteChunk
from jobcopilot_api.models.question import Question
from jobcopilot_api.models.quiz_session import QuizSession
from jobcopilot_api.models.session_answer import SessionAnswer
from jobcopilot_api.schemas.agents.answer_judge import (
    AnswerJudgeInput,
    AnswerJudgeOutput,
)
from jobcopilot_api.schemas.agents.quiz_generator import (
    GeneratedQuestion,
    QuizGenChunkInput,
    ReferencePoint,
)
from jobcopilot_api.schemas.quiz import AnswerDraftIn
from jobcopilot_api.services.retrieval_pipeline import fetch_note_titles

REQUIRED_DEPTH_DIMENSIONS = {"tradeoff", "why", "boundary"}
# SSE-level escape hatch for one answer, including any lookup tool rounds.
JUDGE_CALL_HARD_TIMEOUT_S = 95.0


class SessionNotInProgressError(JobCopilotError):
    status_code = 409
    code = "session_not_in_progress"
    title = "会话不是可答题状态"


class UnansweredQuestionsError(JobCopilotError):
    status_code = 409
    code = "unanswered_questions"
    title = "还有题目未作答"


class JudgeCallFailedError(JobCopilotError):
    status_code = 502
    code = "judge_call_failed"
    title = "Judge 调用失败"


class JudgeIntegrityError(JudgeCallFailedError):
    title = "Judge 输出不符合完整性约束"


@dataclass(frozen=True)
class _JudgeTask:
    answer_id: int
    order_index: int
    user_answer: str
    question: GeneratedQuestion
    chunks: list[QuizGenChunkInput]
    source_chunk_ids: list[int]


async def save_draft(
    session: AsyncSession,
    session_id: int,
    order_index: int,
    payload: AnswerDraftIn,
) -> None:
    quiz_session = await session.get(QuizSession, session_id)
    if quiz_session is None:
        raise NotFoundError(f"quiz_session {session_id} 不存在")
    if quiz_session.status != "in_progress":
        raise SessionNotInProgressError(
            f"session {session_id} 当前状态为 {quiz_session.status},不能继续答题"
        )

    now = datetime.now(UTC)
    result = await session.execute(
        sa.update(SessionAnswer)
        .where(SessionAnswer.session_id == session_id)
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
) -> AsyncIterator[dict[str, Any]]:
    """评分 SSE:逐题 Judge → Python 算分 → 落库 → 汇总 session 分。"""
    try:
        tasks = await _load_judge_tasks(sessionmaker, session_id)
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
            llm_result = await _run_judge_with_hard_timeout(
                judge_input,
                sessionmaker=sessionmaker,
            )
            judged = _extract_judge_output(llm_result)
            judged = _map_and_validate_output(
                output=judged,
                reference_points=task.question.reference_points,
                source_chunk_ids=task.source_chunk_ids,
                lookup_ref_map=_lookup_ref_map(llm_result),
            )
            scores = _compute_scores(judged, task.question.reference_points)
            await _persist_judged_answer(
                sessionmaker,
                answer_id=task.answer_id,
                judged=judged,
                scores=scores,
                llm_result=llm_result,
            )
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

        yield _ev(
            "question_done",
            {
                "order_index": task.order_index,
                "scores": _scores_for_event(scores),
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
        summary = await _finalize_session(sessionmaker, session_id)
    except JobCopilotError as e:
        yield _ev("error", _error_payload(e))
        yield _ev("done", {"ok": False})
        return
    yield _ev("result", summary)
    yield _ev("done", {"ok": True})


async def abandon_session(
    session: AsyncSession,
    session_id: int,
) -> dict[str, Any]:
    quiz_session = await session.get(QuizSession, session_id)
    if quiz_session is None:
        raise NotFoundError(f"quiz_session {session_id} 不存在")
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


async def _run_judge_with_hard_timeout(
    judge_input: AnswerJudgeInput,
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> LLMResult:
    """Run one Judge call with a service-level wall-clock cap.

    The LLM layer already passes provider timeouts, but SSE must still have a
    final escape hatch when the SDK/upstream hangs without raising.
    """
    task = asyncio.create_task(
        answer_judge_agent.run(judge_input, sessionmaker=sessionmaker)
    )
    try:
        done, _pending = await asyncio.wait(
            {task},
            timeout=JUDGE_CALL_HARD_TIMEOUT_S,
        )
    except asyncio.CancelledError:
        _cancel_and_drain(task)
        raise

    if not done:
        _cancel_and_drain(task)
        raise JudgeCallFailedError(
            f"Judge 调用超过 {JUDGE_CALL_HARD_TIMEOUT_S:.0f}s 未返回,已中止"
        )

    return task.result()


def _cancel_and_drain(task: asyncio.Task[LLMResult]) -> None:
    task.cancel()
    task.add_done_callback(_drain_task_exception)


def _drain_task_exception(task: asyncio.Task[LLMResult]) -> None:
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        return None


def _lookup_ref_map(llm_result: LLMResult) -> dict[int, int]:
    raw = llm_result.metadata.get("lookup_ref_map", {})
    if not isinstance(raw, dict):
        return {}
    out: dict[int, int] = {}
    for ref_id, chunk_id in raw.items():
        try:
            out[int(ref_id)] = int(chunk_id)
        except (TypeError, ValueError):
            continue
    return out


async def _load_judge_tasks(
    sessionmaker: async_sessionmaker[AsyncSession],
    session_id: int,
) -> list[_JudgeTask]:
    async with sessionmaker() as session:
        quiz_session = await session.get(QuizSession, session_id)
        if quiz_session is None:
            raise NotFoundError(f"quiz_session {session_id} 不存在")
        if quiz_session.status != "in_progress":
            raise SessionNotInProgressError(
                f"session {session_id} 当前状态为 {quiz_session.status},不能提交"
            )

        answers = list(
            (
                await session.execute(
                    sa.select(SessionAnswer)
                    .where(SessionAnswer.session_id == session_id)
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
                    sa.select(Question).where(Question.id.in_(question_ids))
                )
            )
            .scalars()
            .all()
        )
        question_by_id = {q.id: q for q in questions}
        if len(question_by_id) != len(set(question_ids)):
            raise JudgeIntegrityError("session_answers 引用了不存在的 question")

        all_chunk_ids = list(
            dict.fromkeys(
                chunk_id
                for q in question_by_id.values()
                for chunk_id in q.source_chunk_ids
            )
        )
        if not all_chunk_ids:
            raise JudgeIntegrityError("questions.source_chunk_ids 不能为空")
        chunks = list(
            (
                await session.execute(
                    sa.select(NoteChunk).where(NoteChunk.id.in_(all_chunk_ids))
                )
            )
            .scalars()
            .all()
        )
        chunk_by_id = {c.id: c for c in chunks}
        if len(chunk_by_id) != len(set(all_chunk_ids)):
            raise JudgeIntegrityError("questions.source_chunk_ids 引用了不存在的 chunk")

        note_ids = list({chunk.note_id for chunk in chunks})
        note_titles = await fetch_note_titles(session, note_ids)

        tasks: list[_JudgeTask] = []
        for answer in answers:
            question = question_by_id[answer.question_id]
            source_chunk_ids = list(question.source_chunk_ids)
            local_question = _question_to_local(question)
            local_chunks = [
                _chunk_to_input(chunk_by_id[chunk_id], note_titles)
                for chunk_id in source_chunk_ids
            ]
            tasks.append(
                _JudgeTask(
                    answer_id=answer.id,
                    order_index=answer.order_index,
                    user_answer=answer.user_answer or "",
                    question=local_question,
                    chunks=local_chunks,
                    source_chunk_ids=source_chunk_ids,
                )
            )
        return tasks


def _question_to_local(question: Question) -> GeneratedQuestion:
    """DB id 存储形态 → AnswerJudge prompt 的局部 [N] 编号形态。"""
    source_chunk_ids = list(question.source_chunk_ids)
    db_to_local = {
        chunk_id: idx for idx, chunk_id in enumerate(source_chunk_ids, start=1)
    }

    def to_local(chunk_id: int) -> int:
        if chunk_id not in db_to_local:
            raise JudgeIntegrityError(
                f"question {question.id} 引用了非 source chunk:{chunk_id}"
            )
        return db_to_local[chunk_id]

    points = []
    for raw in question.reference_points:
        points.append(
            ReferencePoint(
                id=str(raw["id"]),
                text=str(raw["text"]),
                weight=float(raw["weight"]),
                evidence_chunk_ids=[
                    to_local(int(chunk_id))
                    for chunk_id in raw.get("evidence_chunk_ids", [])
                ],
            )
        )

    return GeneratedQuestion(
        type=question.type,
        prompt=question.prompt,
        source_chunk_ids=list(range(1, len(source_chunk_ids) + 1)),
        reference_answer=question.reference_answer,
        reference_chunk_ids=[
            to_local(chunk_id) for chunk_id in question.reference_chunk_ids
        ],
        reference_points=points,
    )


def _chunk_to_input(
    chunk: NoteChunk,
    note_titles: dict[int, str],
) -> QuizGenChunkInput:
    return QuizGenChunkInput(
        id=chunk.id,
        folder_path=list(chunk.folder_path),
        heading_path=list(chunk.heading_path),
        note_title=note_titles.get(chunk.note_id, ""),
        content=chunk.content,
    )


def _extract_judge_output(llm_result: LLMResult) -> AnswerJudgeOutput:
    if not isinstance(llm_result.parsed, AnswerJudgeOutput):
        raise JudgeIntegrityError("answer_judge 没返回有效的 AnswerJudgeOutput")
    return llm_result.parsed


def _map_and_validate_output(
    *,
    output: AnswerJudgeOutput,
    reference_points: list[ReferencePoint],
    source_chunk_ids: list[int],
    lookup_ref_map: dict[int, int],
) -> AnswerJudgeOutput:
    _validate_coverage(output, reference_points)
    _validate_fidelity(output, source_chunk_ids, lookup_ref_map)
    _validate_depth(output)

    data = output.model_dump(mode="json")
    for claim in data["fidelity_evidence"]["claims"]:
        if claim["label"] == "fabricated":
            claim["chunk_ids"] = []
            continue
        claim["chunk_ids"] = _map_claim_chunk_ids(
            claim.get("chunk_ids", []),
            source_chunk_ids=source_chunk_ids,
            lookup_ref_map=lookup_ref_map,
        )
    return AnswerJudgeOutput.model_validate(data)


def _validate_coverage(
    output: AnswerJudgeOutput,
    reference_points: list[ReferencePoint],
) -> None:
    expected = {p.id for p in reference_points}
    got = [p.id for p in output.coverage_evidence.points]
    if len(got) != len(set(got)):
        raise JudgeIntegrityError("coverage_evidence.points 出现重复 point id")
    if set(got) != expected:
        raise JudgeIntegrityError(
            f"coverage_evidence point ids 不匹配,"
            f"expected={sorted(expected)},got={sorted(got)}"
        )


def _validate_fidelity(
    output: AnswerJudgeOutput,
    source_chunk_ids: list[int],
    lookup_ref_map: dict[int, int],
) -> None:
    claims = output.fidelity_evidence.claims
    if not claims:
        raise JudgeIntegrityError("fidelity_evidence.claims 不能为空")

    for claim in claims:
        if claim.label == "fabricated" and claim.chunk_ids:
            raise JudgeIntegrityError("fabricated claim 的 chunk_ids 必须为空")
        if claim.label != "fabricated":
            _map_claim_chunk_ids(
                claim.chunk_ids,
                source_chunk_ids=source_chunk_ids,
                lookup_ref_map=lookup_ref_map,
            )


def _map_claim_chunk_ids(
    raw_ids: list[int],
    *,
    source_chunk_ids: list[int],
    lookup_ref_map: dict[int, int],
) -> list[int]:
    max_local_id = len(source_chunk_ids)
    lookup_chunk_ids = set(lookup_ref_map.values())
    mapped: list[int] = []
    for raw_id in raw_ids:
        chunk_id = int(raw_id)
        if 1 <= chunk_id <= max_local_id:
            mapped.append(source_chunk_ids[chunk_id - 1])
            continue
        if chunk_id in lookup_ref_map:
            mapped.append(lookup_ref_map[chunk_id])
            continue
        if chunk_id > max_local_id and (
            chunk_id in source_chunk_ids or chunk_id in lookup_chunk_ids
        ):
            mapped.append(chunk_id)
            continue
        raise JudgeIntegrityError(
            f"fidelity claim chunk_id [{chunk_id}] 越界,"
            f"合法范围 1..{max_local_id} 或 lookup ref_id"
        )
    return mapped


def _validate_depth(output: AnswerJudgeOutput) -> None:
    got = set(output.depth_evidence.dimensions)
    if got != REQUIRED_DEPTH_DIMENSIONS:
        raise JudgeIntegrityError(
            f"depth_evidence.dimensions keys 不匹配,"
            f"expected={sorted(REQUIRED_DEPTH_DIMENSIONS)},got={sorted(got)}"
        )


def _compute_scores(
    judged: AnswerJudgeOutput,
    reference_points: list[ReferencePoint],
) -> dict[str, float]:
    coverage = coverage_score(judged.coverage_evidence, reference_points)
    fidelity = fidelity_score(judged.fidelity_evidence)
    depth = depth_score(judged.depth_evidence)
    total = compute_total_score(coverage, fidelity, depth)
    return {
        "coverage": coverage,
        "fidelity": fidelity,
        "depth": depth,
        "total": total,
    }


def _scores_for_event(scores: dict[str, float]) -> dict[str, float]:
    return {key: round(value, 2) for key, value in scores.items()}


async def _persist_judged_answer(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    answer_id: int,
    judged: AnswerJudgeOutput,
    scores: dict[str, float],
    llm_result: LLMResult,
) -> None:
    now = datetime.now(UTC)
    async with sessionmaker() as session:
        await session.execute(
            sa.update(SessionAnswer)
            .where(SessionAnswer.id == answer_id)
            .values(
                coverage_score=_score_decimal(scores["coverage"]),
                coverage_evidence=judged.coverage_evidence.model_dump(mode="json"),
                fidelity_score=_score_decimal(scores["fidelity"]),
                fidelity_evidence=judged.fidelity_evidence.model_dump(mode="json"),
                depth_score=_score_decimal(scores["depth"]),
                depth_evidence=judged.depth_evidence.model_dump(mode="json"),
                total_score=_score_decimal(scores["total"]),
                judge_model=llm_result.model,
                judge_prompt_version=JUDGE_PROMPT_VERSION,
                judge_tokens_in=llm_result.tokens_in,
                judge_tokens_out=llm_result.tokens_out,
                judge_cost_cny=llm_result.cost_cny,
                judged_at=now,
                updated_at=now,
            )
        )
        await session.commit()


async def _finalize_session(
    sessionmaker: async_sessionmaker[AsyncSession],
    session_id: int,
) -> dict[str, Any]:
    async with sessionmaker() as session:
        answers = list(
            (
                await session.execute(
                    sa.select(SessionAnswer).where(
                        SessionAnswer.session_id == session_id
                    )
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
            .values(
                status="submitted",
                coverage_score=_score_decimal(coverage),
                fidelity_score=_score_decimal(fidelity),
                depth_score=_score_decimal(depth),
                total_score=_score_decimal(total),
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


def _score_decimal(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))
