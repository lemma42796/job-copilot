"""Shared AnswerJudge orchestration helpers.

This module is the public service boundary for building Judge context,
running AnswerJudge, validating its chunk references, computing scores, and
persisting the judged answer. `answer_service` and `interview_service` both
use it so the single-session submit path and the single-question remediation
path stay on the same semantics.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
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
from jobcopilot_api.errors import JobCopilotError
from jobcopilot_api.llm.client import LLMResult
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
    ScoringPoint,
)
from jobcopilot_api.services.retrieval_pipeline import fetch_note_titles

REQUIRED_DEPTH_DIMENSIONS = {"tradeoff", "why", "boundary"}
JUDGE_CALL_HARD_TIMEOUT_S = 95.0


class JudgeCallFailedError(JobCopilotError):
    status_code = 502
    code = "judge_call_failed"
    title = "Judge 调用失败"


class JudgeIntegrityError(JudgeCallFailedError):
    title = "Judge 输出不符合完整性约束"


@dataclass(frozen=True)
class JudgeContext:
    chunk_ids: list[int]
    chunks: list[QuizGenChunkInput]


@dataclass(frozen=True)
class JudgedAnswer:
    judged: AnswerJudgeOutput
    scores: dict[str, float]
    llm_result: LLMResult


def judge_context_chunk_ids(
    *,
    quiz_session: QuizSession,
    questions: list[Question],
) -> list[int]:
    """Use the session retrieval order so Judge shares Quiz's cache prefix."""
    judge_context_chunk_ids = unique_int_ids(quiz_session.final_context_chunk_ids or [])
    question_context_chunk_ids = unique_int_ids(
        chunk_id
        for question in questions
        for chunk_id in question_context_chunk_ids_for(question)
    )
    seen = set(judge_context_chunk_ids)
    for chunk_id in question_context_chunk_ids:
        if chunk_id not in seen:
            judge_context_chunk_ids.append(chunk_id)
            seen.add(chunk_id)
    return judge_context_chunk_ids


async def build_judge_context(
    session: AsyncSession,
    *,
    quiz_session: QuizSession,
    questions: list[Question],
) -> JudgeContext:
    chunk_ids = judge_context_chunk_ids(
        quiz_session=quiz_session,
        questions=questions,
    )
    if not chunk_ids:
        raise JudgeIntegrityError(
            "quiz_session.final_context_chunk_ids / questions.evidence_chunk_ids 不能为空"
        )
    return await load_judge_context(session, chunk_ids)


async def load_judge_context(
    session: AsyncSession,
    judge_context_chunk_ids: list[int],
) -> JudgeContext:
    chunks = list(
        (
            await session.execute(
                sa.select(NoteChunk).where(NoteChunk.id.in_(judge_context_chunk_ids))
            )
        )
        .scalars()
        .all()
    )
    chunk_by_id = {chunk.id: chunk for chunk in chunks}
    if len(chunk_by_id) != len(set(judge_context_chunk_ids)):
        raise JudgeIntegrityError(
            "quiz_session.final_context_chunk_ids / questions.evidence_chunk_ids "
            "引用了不存在的 chunk"
        )

    note_titles = await fetch_note_titles(
        session,
        list({chunk.note_id for chunk in chunks}),
    )
    return JudgeContext(
        chunk_ids=judge_context_chunk_ids,
        chunks=[
            chunk_to_input(chunk_by_id[chunk_id], note_titles)
            for chunk_id in judge_context_chunk_ids
        ],
    )


async def judge_and_persist_answer(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    answer_id: int,
    judge_input: AnswerJudgeInput,
    scoring_points: list[ScoringPoint],
    judge_context_chunk_ids: list[int],
) -> JudgedAnswer:
    llm_result = await run_judge_with_hard_timeout(
        judge_input,
        sessionmaker=sessionmaker,
    )
    judged = extract_judge_output(llm_result)
    judged = map_and_validate_output(
        output=judged,
        scoring_points=scoring_points,
        judge_context_chunk_ids=judge_context_chunk_ids,
        lookup_ref_map=lookup_ref_map(llm_result),
    )
    scores = compute_scores(judged, scoring_points)
    await persist_judged_answer(
        sessionmaker,
        answer_id=answer_id,
        judged=judged,
        scores=scores,
        llm_result=llm_result,
    )
    return JudgedAnswer(judged=judged, scores=scores, llm_result=llm_result)


async def run_judge_with_hard_timeout(
    judge_input: AnswerJudgeInput,
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> LLMResult:
    """Run one Judge call with a service-level wall-clock cap."""
    task = asyncio.create_task(
        answer_judge_agent.run(judge_input, sessionmaker=sessionmaker)
    )
    try:
        done, _pending = await asyncio.wait(
            {task},
            timeout=JUDGE_CALL_HARD_TIMEOUT_S,
        )
    except asyncio.CancelledError:
        cancel_and_drain(task)
        raise

    if not done:
        cancel_and_drain(task)
        raise JudgeCallFailedError(
            f"Judge 调用超过 {JUDGE_CALL_HARD_TIMEOUT_S:.0f}s 未返回,已中止"
        )

    return task.result()


def lookup_ref_map(llm_result: LLMResult) -> dict[int, int]:
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


def question_to_local(
    question: Question,
    judge_context_chunk_ids: list[int],
) -> GeneratedQuestion:
    """DB id storage shape -> AnswerJudge prompt-local [N] ids."""
    db_to_local = {
        chunk_id: idx
        for idx, chunk_id in enumerate(judge_context_chunk_ids, start=1)
    }

    def to_local(chunk_id: int) -> int:
        if chunk_id not in db_to_local:
            raise JudgeIntegrityError(
                f"question {question.id} 引用了非 judge context chunk:{chunk_id}"
            )
        return db_to_local[chunk_id]

    points = []
    for raw in question.scoring_points:
        points.append(
            ScoringPoint(
                id=str(raw["id"]),
                text=str(raw["text"]),
                weight=float(raw["weight"]),
                supporting_chunk_ids=[
                    to_local(int(chunk_id))
                    for chunk_id in raw.get("supporting_chunk_ids", [])
                ],
            )
        )

    return GeneratedQuestion(
        type=question.type,
        prompt=question.prompt,
        evidence_chunk_ids=[
            to_local(int(chunk_id)) for chunk_id in question.evidence_chunk_ids
        ],
        reference_answer=question.reference_answer,
        reference_answer_chunk_ids=[
            to_local(int(chunk_id)) for chunk_id in question.reference_answer_chunk_ids
        ],
        scoring_points=points,
    )


def chunk_to_input(
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


def extract_judge_output(llm_result: LLMResult) -> AnswerJudgeOutput:
    if not isinstance(llm_result.parsed, AnswerJudgeOutput):
        raise JudgeIntegrityError("answer_judge 没返回有效的 AnswerJudgeOutput")
    return llm_result.parsed


def map_and_validate_output(
    *,
    output: AnswerJudgeOutput,
    scoring_points: list[ScoringPoint],
    judge_context_chunk_ids: list[int],
    lookup_ref_map: dict[int, int],
) -> AnswerJudgeOutput:
    validate_coverage(output, scoring_points)
    validate_fidelity(output, judge_context_chunk_ids, lookup_ref_map)
    validate_depth(output)

    data = output.model_dump(mode="json")
    coach_message = str(data.get("coach_message") or "").strip()
    if not coach_message:
        raise JudgeIntegrityError("coach_message 不能为空")
    data["coach_message"] = coach_message
    for claim in data["fidelity_evidence"]["claims"]:
        if claim["label"] == "fabricated":
            claim["supporting_chunk_ids"] = []
            continue
        claim["supporting_chunk_ids"] = map_claim_chunk_ids(
            claim.get("supporting_chunk_ids", []),
            judge_context_chunk_ids=judge_context_chunk_ids,
            lookup_ref_map=lookup_ref_map,
        )
    return AnswerJudgeOutput.model_validate(data)


def compute_scores(
    judged: AnswerJudgeOutput,
    scoring_points: list[ScoringPoint],
) -> dict[str, float]:
    coverage = coverage_score(judged.coverage_evidence, scoring_points)
    fidelity = fidelity_score(judged.fidelity_evidence)
    depth = depth_score(judged.depth_evidence)
    total = compute_total_score(coverage, fidelity, depth)
    return {
        "coverage": coverage,
        "fidelity": fidelity,
        "depth": depth,
        "total": total,
    }


async def persist_judged_answer(
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
                coverage_score=score_decimal(scores["coverage"]),
                coverage_evidence=judged.coverage_evidence.model_dump(mode="json"),
                fidelity_score=score_decimal(scores["fidelity"]),
                fidelity_evidence=judged.fidelity_evidence.model_dump(mode="json"),
                depth_score=score_decimal(scores["depth"]),
                depth_evidence=judged.depth_evidence.model_dump(mode="json"),
                total_score=score_decimal(scores["total"]),
                coach_message=judged.coach_message,
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


def validate_coverage(
    output: AnswerJudgeOutput,
    scoring_points: list[ScoringPoint],
) -> None:
    expected = {point.id for point in scoring_points}
    got = [point.id for point in output.coverage_evidence.points]
    if len(got) != len(set(got)):
        raise JudgeIntegrityError("coverage_evidence.points 出现重复 point id")
    if set(got) != expected:
        raise JudgeIntegrityError(
            f"coverage_evidence point ids 不匹配,"
            f"expected={sorted(expected)},got={sorted(got)}"
        )


def validate_fidelity(
    output: AnswerJudgeOutput,
    judge_context_chunk_ids: list[int],
    lookup_ref_map: dict[int, int],
) -> None:
    claims = output.fidelity_evidence.claims
    if not claims:
        raise JudgeIntegrityError("fidelity_evidence.claims 不能为空")

    for claim in claims:
        if claim.label == "fabricated" and claim.supporting_chunk_ids:
            raise JudgeIntegrityError(
                "fabricated claim 的 supporting_chunk_ids 必须为空"
            )
        if claim.label != "fabricated":
            map_claim_chunk_ids(
                claim.supporting_chunk_ids,
                judge_context_chunk_ids=judge_context_chunk_ids,
                lookup_ref_map=lookup_ref_map,
            )


def map_claim_chunk_ids(
    raw_ids: list[int],
    *,
    judge_context_chunk_ids: list[int],
    lookup_ref_map: dict[int, int],
) -> list[int]:
    max_local_id = len(judge_context_chunk_ids)
    lookup_chunk_ids = set(lookup_ref_map.values())
    mapped: list[int] = []
    for raw_id in raw_ids:
        chunk_id = int(raw_id)
        if 1 <= chunk_id <= max_local_id:
            mapped.append(judge_context_chunk_ids[chunk_id - 1])
            continue
        if chunk_id in lookup_ref_map:
            mapped.append(lookup_ref_map[chunk_id])
            continue
        if chunk_id > max_local_id and (
            chunk_id in judge_context_chunk_ids or chunk_id in lookup_chunk_ids
        ):
            mapped.append(chunk_id)
            continue
        raise JudgeIntegrityError(
            f"fidelity claim chunk_id [{chunk_id}] 越界,"
            f"合法范围 1..{max_local_id} 或 lookup ref_id"
        )
    return mapped


def validate_depth(output: AnswerJudgeOutput) -> None:
    got = set(output.depth_evidence.dimensions)
    if got != REQUIRED_DEPTH_DIMENSIONS:
        raise JudgeIntegrityError(
            f"depth_evidence.dimensions keys 不匹配,"
            f"expected={sorted(REQUIRED_DEPTH_DIMENSIONS)},got={sorted(got)}"
        )


def unique_int_ids(values: Iterable[Any]) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            chunk_id = int(value)
        except (TypeError, ValueError) as e:
            raise JudgeIntegrityError(f"chunk id 非法:{value!r}") from e
        if chunk_id not in seen:
            out.append(chunk_id)
            seen.add(chunk_id)
    return out


def question_context_chunk_ids_for(question: Question) -> list[int]:
    ids: list[int] = []
    ids.extend(question.evidence_chunk_ids)
    ids.extend(question.reference_answer_chunk_ids)
    for point in question.scoring_points:
        ids.extend(point.get("supporting_chunk_ids", []))
    return ids


def score_decimal(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def cancel_and_drain(task: asyncio.Task[LLMResult]) -> None:
    task.cancel()
    task.add_done_callback(drain_task_exception)


def drain_task_exception(task: asyncio.Task[LLMResult]) -> None:
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        return None
