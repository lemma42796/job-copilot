"""Offline flow runner for the M2.1 InterviewCoachAgent harness.

This runner intentionally does not call the DB, AnswerJudge, or any LLM. It reads
`evals/suites/interview_coach/dataset.flow_smoke.jsonl`, stubs Judge evidence from
the fixture labels, then feeds that evidence into the current deterministic
decision policy in `interview_service`.

The goal is to guard harness behavior: branch decisions, remediation targets,
cumulative rejudge input, recovery, context-pack shape, hallucination guard, and
finish-summary structure. Judge label quality belongs to the answer_judge suite.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from jobcopilot_api.schemas.agents.answer_judge import (
    AnswerJudgeOutput,
    CoverageEvidence,
    CoveragePoint,
    DepthDimension,
    DepthEvidence,
    FidelityClaim,
    FidelityEvidence,
)
from jobcopilot_api.services.interview_service import (
    TARGET_COVERAGE,
    _decide_next_action,
    _merge_turn_texts,
)

VALID_ACTIONS = {"ask_next", "remediate", "summarize", "finish"}
VALID_DEPTH_DIMENSIONS = {"tradeoff", "why", "boundary"}
CURRENT_TURN_CONTEXT_FIELDS = {
    "question",
    "judge_context_chunks",
    "source_chunks",
    "scoring_points",
    "reference_points",
    "cumulative_answer",
    "unresolved_gaps",
}


@dataclass(frozen=True)
class FlowCase:
    fixture_id: str
    category: str
    query: str
    question: dict[str, Any]
    turns: list[dict[str, Any]]
    expected_final: dict[str, Any]
    context_expectation: dict[str, Any]
    hallucination_guard: dict[str, Any]
    notes: str


@dataclass(frozen=True)
class DecisionCheck:
    step: str
    expected_action: str
    actual_action: str
    passed: bool
    expected_triggered_by: str | None = None
    actual_triggered_by: str | None = None
    expected_exit_reason: str | None = None
    actual_exit_reason: str | None = None
    remediation_target_passed: bool | None = None
    failures: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RecoveryCheck:
    expected_node: str
    actual_node: str | None
    passed: bool
    failures: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CaseResult:
    fixture_id: str
    category: str
    passed: bool
    decisions: list[DecisionCheck]
    final_expected_action: str
    final_actual_action: str
    final_passed: bool
    recovery: RecoveryCheck | None
    cumulative_rejudge_passed: bool | None
    loop_exit_passed: bool | None
    context_pack_passed: bool
    hallucination_guard_passed: bool | None
    summary_passed: bool | None
    failures: list[str]


@dataclass(frozen=True)
class Metric:
    name: str
    passed: int
    total: int

    @property
    def rate(self) -> float | None:
        if self.total == 0:
            return None
        return self.passed / self.total

    @property
    def label(self) -> str:
        if self.rate is None:
            return "n/a"
        return f"{self.rate:.3f}"


def load_cases(path: Path) -> list[FlowCase]:
    cases: list[FlowCase] = []
    seen: set[str] = set()
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        raw = json.loads(line)
        fixture_id = str(raw["fixture_id"])
        if fixture_id in seen:
            raise ValueError(f"duplicate fixture_id={fixture_id!r} at line {line_no}")
        seen.add(fixture_id)
        cases.append(
            FlowCase(
                fixture_id=fixture_id,
                category=str(raw.get("category", "")),
                query=str(raw["query"]),
                question=dict(raw["question"]),
                turns=list(raw["turns"]),
                expected_final=dict(raw.get("expected_final", {})),
                context_expectation=dict(raw.get("context_expectation", {})),
                hallucination_guard=dict(raw.get("hallucination_guard", {})),
                notes=str(raw.get("notes", "")),
            )
        )
    if not cases:
        raise ValueError(f"dataset is empty: {path}")
    return cases


def evaluate_case(case: FlowCase) -> CaseResult:
    failures: list[str] = []
    decisions: list[DecisionCheck] = []
    recovery_check: RecoveryCheck | None = None
    answer_turns: list[dict[str, Any]] = []
    session_events: list[str] = []
    last_system_state: dict[str, Any] = {}
    last_actual_action = "none"
    last_unresolved_gaps: list[str] = []
    low_context_budget = False
    remediation_prompts: list[dict[str, Any]] = []
    judge_inputs: list[str] = []
    remediation_state: dict[str, Any] = {}

    for index, turn in enumerate(case.turns):
        role = str(turn.get("role", ""))
        if role == "system_state":
            last_system_state = dict(turn)
            persisted = turn.get("persisted_answer_turns")
            if isinstance(persisted, list):
                answer_turns = [dict(item) for item in persisted if isinstance(item, dict)]
            low_context_budget = (
                turn.get("context_budget") == "low"
                or int(turn.get("prior_turns_count") or 0) >= 6
            )
            continue

        if role == "user":
            answer_turns.append(
                {
                    "round_index": len(answer_turns),
                    "turn_type": str(
                        turn.get("turn_type", "initial" if not answer_turns else "remediation")
                    ),
                    "text": str(turn.get("text", "")),
                }
            )
            continue

        if role == "coach_question":
            session_events.append("coach_answered")
            continue

        if role != "expected_agent":
            failures.append(f"turn[{index}] has unsupported role={role!r}")
            continue

        if "expected_recovery_node" in turn:
            expected_node = str(turn["expected_recovery_node"])
            actual_node = str(last_system_state.get("last_agent_node") or "")
            recovery_failures = []
            if actual_node != expected_node:
                recovery_failures.append(
                    f"recovery node mismatch: expected {expected_node}, got {actual_node}"
                )
            if len(answer_turns) != len(last_system_state.get("persisted_answer_turns") or []):
                recovery_failures.append("recovery duplicated answer turns")
            recovery_check = RecoveryCheck(
                expected_node=expected_node,
                actual_node=actual_node,
                passed=not recovery_failures,
                failures=recovery_failures,
            )
            failures.extend(recovery_failures)
            continue

        expected_action = str(turn.get("expected_action", ""))
        if expected_action not in VALID_ACTIONS:
            failures.append(f"turn[{index}] has invalid expected_action={expected_action!r}")
            continue

        cumulative_answer = _merge_turn_texts(answer_turns)
        judge_inputs.append(cumulative_answer)
        token_budget_exhausted = low_context_budget
        if _should_compact_context(answer_turns, token_budget_exhausted):
            session_events.append("context_compacted")

        judged, scores = _stub_judge_output(
            case=case,
            expected=turn,
            expected_action=expected_action,
            last_unresolved_gaps=last_unresolved_gaps,
        )
        total_questions = _stub_total_questions(case)
        decision = _decide_next_action(
            judged=judged,
            scores=scores,
            question=cast(Any, _question_stub(case)),
            order_index=0,
            total_questions=total_questions,
            answer_turns=answer_turns,
            remediation_state=remediation_state,
            token_budget_exhausted=token_budget_exhausted,
        )
        last_actual_action = str(decision.next_action)
        session_events.extend(["context_pack_built", "judge_completed", "decision_made"])

        branch_failures: list[str] = []
        if decision.next_action != expected_action:
            branch_failures.append(
                f"action mismatch: expected {expected_action}, got {decision.next_action}"
            )
        expected_trigger = turn.get("triggered_by")
        if expected_trigger is not None and decision.triggered_by != expected_trigger:
            branch_failures.append(
                f"trigger mismatch: expected {expected_trigger}, got {decision.triggered_by}"
            )
        expected_exit_reason = turn.get("exit_reason")
        if expected_exit_reason is not None and decision.exit_reason != expected_exit_reason:
            branch_failures.append(
                "exit_reason mismatch: "
                f"expected {expected_exit_reason}, got {decision.exit_reason}"
            )

        remediation_target_passed = None
        remediation_target_failures: list[str] = []
        if expected_action == "remediate":
            remediation_target_failures = _check_remediation_target(turn, decision.remediation_prompt)
            remediation_target_passed = not remediation_target_failures

        if decision.remediation_prompt is not None:
            session_events.append("remediation_prompted")
            remediation_prompts.append(decision.remediation_prompt)
            last_unresolved_gaps = [
                _gap_key(gap)
                for gap in decision.remediation_prompt.get("unresolved_gaps", [])
                if isinstance(gap, dict)
            ]
        elif expected_exit_reason is None:
            last_unresolved_gaps = []

        decisions.append(
            DecisionCheck(
                step=f"turn[{index}]",
                expected_action=expected_action,
                actual_action=str(decision.next_action),
                passed=not branch_failures,
                expected_triggered_by=str(expected_trigger) if expected_trigger else None,
                actual_triggered_by=str(decision.triggered_by),
                expected_exit_reason=str(expected_exit_reason) if expected_exit_reason else None,
                actual_exit_reason=decision.exit_reason,
                remediation_target_passed=remediation_target_passed,
                failures=branch_failures + remediation_target_failures,
            )
        )
        score_history = list(remediation_state.get("judge_score_history") or [])
        score_history.append(_round_scores(scores))
        remediation_state = {
            "last_decision": decision.next_action,
            "triggered_by": decision.triggered_by,
            "decision_reason": decision.decision_reason,
            "exit_reason": decision.exit_reason,
            "remediation_prompt": decision.remediation_prompt,
            "unresolved_gaps": decision.unresolved_gaps,
            "judge_score_history": score_history[-8:],
        }
        failures.extend(f"turn[{index}] {failure}" for failure in branch_failures)
        failures.extend(f"turn[{index}] {failure}" for failure in remediation_target_failures)

    final_expected_action = str(case.expected_final.get("action", last_actual_action))
    final_actual_action = (
        "finish" if final_expected_action == "finish" and last_actual_action == "summarize"
        else last_actual_action
    )
    final_passed = final_actual_action == final_expected_action
    if not final_passed:
        failures.append(
            f"final action mismatch: expected {final_expected_action}, got {final_actual_action}"
        )

    summary_passed = _check_summary(
        case=case,
        final_actual_action=final_actual_action,
        session_events=session_events,
    )
    if summary_passed is False:
        failures.append("finish summary was expected but not generated")

    cumulative_rejudge_passed = _check_cumulative_rejudge(case, answer_turns, judge_inputs)
    if cumulative_rejudge_passed is False:
        failures.append("judge input used last user turn instead of cumulative answer")

    loop_exit_passed = _check_loop_exit(case, decisions)
    if loop_exit_passed is False:
        failures.append("loop exit expectation was not met")

    context_pack_failures = _check_context_pack(
        case=case,
        answer_turns=answer_turns,
        low_context_budget=low_context_budget,
        session_events=session_events,
    )
    context_pack_passed = not context_pack_failures
    failures.extend(context_pack_failures)

    hallucination_guard_passed = _check_hallucination_guard(case, remediation_prompts)
    if hallucination_guard_passed is False:
        failures.append("hallucination guard failed")

    passed = not failures
    return CaseResult(
        fixture_id=case.fixture_id,
        category=case.category,
        passed=passed,
        decisions=decisions,
        final_expected_action=final_expected_action,
        final_actual_action=final_actual_action,
        final_passed=final_passed,
        recovery=recovery_check,
        cumulative_rejudge_passed=cumulative_rejudge_passed,
        loop_exit_passed=loop_exit_passed,
        context_pack_passed=context_pack_passed,
        hallucination_guard_passed=hallucination_guard_passed,
        summary_passed=summary_passed,
        failures=failures,
    )


def _question_stub(case: FlowCase) -> SimpleNamespace:
    reference_point_ids = _reference_point_ids(case)
    source_chunk_ids = _source_chunk_ids(case)
    weight = 1.0 / len(reference_point_ids) if reference_point_ids else 1.0
    scoring_points = [
        {
            "id": point_id,
            "text": point_id,
            "weight": weight,
            "supporting_chunk_ids": source_chunk_ids,
        }
        for point_id in reference_point_ids
    ]
    return SimpleNamespace(
        prompt=str(case.question.get("text", "")),
        scoring_points=scoring_points,
    )


def _stub_total_questions(case: FlowCase) -> int:
    return 2 if case.expected_final.get("action") == "ask_next" else 1


def _stub_judge_output(
    *,
    case: FlowCase,
    expected: dict[str, Any],
    expected_action: str,
    last_unresolved_gaps: list[str],
) -> tuple[AnswerJudgeOutput, dict[str, float]]:
    reference_point_ids = _reference_point_ids(case)
    trigger = str(expected.get("triggered_by", "none"))
    exit_reason = expected.get("exit_reason")

    if expected_action == "remediate" and trigger == "coverage":
        missing_point_ids = _string_list(expected.get("missing_reference_point_ids"))
        return _coverage_gap_output(reference_point_ids, missing_point_ids)

    if expected_action == "remediate" and trigger == "fidelity":
        fabricated_ids = _int_list(expected.get("fabricated_claim_ids"))
        return _fabricated_output(reference_point_ids, fabricated_ids)

    if expected_action == "remediate" and trigger == "depth":
        missing_depth = _string_list(expected.get("missing_depth_dimensions"))
        return _depth_gap_output(reference_point_ids, missing_depth)

    if expected_action == "summarize" and exit_reason in {
        "no_meaningful_improvement",
        "token_budget",
        "off_topic",
    }:
        missing_point_ids = _string_list(
            case.expected_final.get("summary_must_include_unresolved_gaps")
        )
        if not missing_point_ids:
            missing_point_ids = [gap for gap in last_unresolved_gaps if gap in reference_point_ids]
        if not missing_point_ids:
            missing_point_ids = reference_point_ids
        return _coverage_gap_output(reference_point_ids, missing_point_ids)

    min_coverage = float(case.expected_final.get("min_coverage_score", TARGET_COVERAGE))
    coverage = max(TARGET_COVERAGE, min_coverage)
    return _good_output(reference_point_ids, coverage=coverage)


def _coverage_gap_output(
    reference_point_ids: list[str],
    missing_point_ids: list[str],
) -> tuple[AnswerJudgeOutput, dict[str, float]]:
    missing = set(missing_point_ids)
    coverage_points = [
        CoveragePoint(
            id=point_id,
            label="miss" if point_id in missing else "hit",
            user_excerpt=None if point_id in missing else point_id,
        )
        for point_id in reference_point_ids
    ]
    judged = AnswerJudgeOutput(
        coverage_evidence=CoverageEvidence(
            points=coverage_points,
            score_raw=0.4,
            reasoning="stubbed coverage gap",
        ),
        fidelity_evidence=FidelityEvidence(
            claims=[
                FidelityClaim(
                    text="stubbed supported claim",
                    label="supported",
                    supporting_chunk_ids=[1],
                )
            ],
            score_raw=1.0,
            reasoning="stubbed supported",
        ),
        depth_evidence=_covered_depth(),
        coach_message="stubbed coach message",
    )
    return judged, {"coverage": 50.0, "fidelity": 100.0, "depth": 100.0, "total": 80.0}


def _fabricated_output(
    reference_point_ids: list[str],
    fabricated_ids: list[int],
) -> tuple[AnswerJudgeOutput, dict[str, float]]:
    claim_count = max(max(fabricated_ids, default=0) + 1, 2)
    fabricated = set(fabricated_ids or [0])
    claims = [
        FidelityClaim(
            text=f"fabricated_claim_{index}",
            label="fabricated" if index in fabricated else "supported",
            supporting_chunk_ids=[] if index in fabricated else [1],
        )
        for index in range(claim_count)
    ]
    judged = AnswerJudgeOutput(
        coverage_evidence=_hit_coverage(reference_point_ids),
        fidelity_evidence=FidelityEvidence(
            claims=claims,
            score_raw=0.2,
            reasoning="stubbed fabricated claims",
        ),
        depth_evidence=_covered_depth(),
        coach_message="stubbed coach message",
    )
    return judged, {"coverage": 90.0, "fidelity": 40.0, "depth": 100.0, "total": 70.0}


def _depth_gap_output(
    reference_point_ids: list[str],
    missing_depth: list[str],
) -> tuple[AnswerJudgeOutput, dict[str, float]]:
    missing = set(missing_depth)
    judged = AnswerJudgeOutput(
        coverage_evidence=_hit_coverage(reference_point_ids),
        fidelity_evidence=FidelityEvidence(
            claims=[
                FidelityClaim(
                    text="stubbed supported claim",
                    label="supported",
                    supporting_chunk_ids=[1],
                )
            ],
            score_raw=1.0,
            reasoning="stubbed supported",
        ),
        depth_evidence=DepthEvidence(
            dimensions={
                dimension: DepthDimension(
                    covered=dimension not in missing,
                    excerpt=None if dimension in missing else dimension,
                )
                for dimension in ("tradeoff", "why", "boundary")
            },
            score_raw=0.4,
            reasoning="stubbed depth gap",
        ),
        coach_message="stubbed coach message",
    )
    return judged, {"coverage": 90.0, "fidelity": 100.0, "depth": 40.0, "total": 80.0}


def _good_output(
    reference_point_ids: list[str],
    *,
    coverage: float,
) -> tuple[AnswerJudgeOutput, dict[str, float]]:
    judged = AnswerJudgeOutput(
        coverage_evidence=_hit_coverage(reference_point_ids),
        fidelity_evidence=FidelityEvidence(
            claims=[
                FidelityClaim(
                    text="stubbed supported claim",
                    label="supported",
                    supporting_chunk_ids=[1],
                )
            ],
            score_raw=1.0,
            reasoning="stubbed supported",
        ),
        depth_evidence=_covered_depth(),
        coach_message="stubbed coach message",
    )
    return judged, {"coverage": coverage, "fidelity": 100.0, "depth": 100.0, "total": 95.0}


def _hit_coverage(reference_point_ids: list[str]) -> CoverageEvidence:
    return CoverageEvidence(
        points=[
            CoveragePoint(id=point_id, label="hit", user_excerpt=point_id)
            for point_id in reference_point_ids
        ],
        score_raw=1.0,
        reasoning="stubbed full coverage",
    )


def _covered_depth() -> DepthEvidence:
    return DepthEvidence(
        dimensions={
            dimension: DepthDimension(covered=True, excerpt=dimension)
            for dimension in ("tradeoff", "why", "boundary")
        },
        score_raw=1.0,
        reasoning="stubbed full depth",
    )


def _check_remediation_target(
    expected: dict[str, Any],
    prompt: dict[str, Any] | None,
) -> list[str]:
    if prompt is None:
        return ["expected remediation_prompt, got none"]
    failures: list[str] = []
    triggered_by = expected.get("triggered_by")
    if triggered_by is not None and prompt.get("triggered_by") != triggered_by:
        failures.append(
            "remediation trigger mismatch: "
            f"expected {triggered_by}, got {prompt.get('triggered_by')}"
        )
    if "missing_reference_point_ids" in expected:
        actual = set(_string_list(prompt.get("missing_scoring_point_ids")))
        expected_ids = set(_string_list(expected.get("missing_reference_point_ids")))
        if actual != expected_ids:
            failures.append(
                "missing point ids mismatch: "
                f"expected {sorted(expected_ids)}, got {sorted(actual)}"
            )
    if "fabricated_claim_ids" in expected:
        actual_fabricated = _int_list(prompt.get("fabricated_claim_ids"))
        expected_fabricated = _int_list(expected.get("fabricated_claim_ids"))
        if actual_fabricated != expected_fabricated:
            failures.append(
                "fabricated claim ids mismatch: "
                f"expected {expected_fabricated}, got {actual_fabricated}"
            )
    if "missing_depth_dimensions" in expected:
        actual_depth = set(_string_list(prompt.get("missing_depth_dimensions")))
        expected_depth = set(_string_list(expected.get("missing_depth_dimensions")))
        if actual_depth != expected_depth:
            failures.append(
                "missing depth dimensions mismatch: "
                f"expected {sorted(expected_depth)}, got {sorted(actual_depth)}"
            )
    return failures


def _check_context_pack(
    *,
    case: FlowCase,
    answer_turns: list[dict[str, Any]],
    low_context_budget: bool,
    session_events: list[str],
) -> list[str]:
    failures: list[str] = []
    fields = set(CURRENT_TURN_CONTEXT_FIELDS)
    if "context_compacted" in session_events:
        fields.add("prior_turn_summary")

    for field_name in _string_list(case.context_expectation.get("must_include")):
        if field_name not in fields:
            failures.append(f"context missing required field {field_name!r}")

    for forbidden in _string_list(case.context_expectation.get("must_not_include")):
        if forbidden == "last_user_turn_only" and len(answer_turns) >= 2:
            cumulative = _merge_turn_texts(answer_turns)
            last_text = str(answer_turns[-1].get("text", "")).strip()
            if cumulative == last_text:
                failures.append("context used only the last user turn")
        elif forbidden in {"full_raw_transcript_when_over_budget", "unbounded_full_transcript"}:
            if low_context_budget and "context_compacted" not in session_events:
                failures.append("low-budget context was not compacted")
        elif forbidden == "source_chunks_outside_question":
            # Checked in hallucination guard; no context-level failure here.
            continue
        elif forbidden == "duplicate_answer_turn_after_reload":
            round_indexes = [turn.get("round_index") for turn in answer_turns]
            if len(round_indexes) != len(set(round_indexes)):
                failures.append("answer turns contain duplicate round_index after reload")

    for turn in case.turns:
        assertions = turn.get("context_pack_assertions")
        if not isinstance(assertions, dict):
            continue
        for field_name in _string_list(assertions.get("must_keep")):
            if field_name not in fields:
                failures.append(f"context compaction dropped {field_name!r}")
        event_name = assertions.get("must_record_event")
        if isinstance(event_name, str) and event_name not in session_events:
            failures.append(f"context did not record event {event_name!r}")
    return failures


def _check_cumulative_rejudge(
    case: FlowCase,
    answer_turns: list[dict[str, Any]],
    judge_inputs: list[str],
) -> bool | None:
    requires_cumulative = bool(case.expected_final.get("requires_cumulative_rejudge"))
    forbids_last_only = "last_user_turn_only" in _string_list(
        case.context_expectation.get("must_not_include")
    )
    if not requires_cumulative and not forbids_last_only:
        return None
    if len(answer_turns) < 2 or not judge_inputs:
        return False
    expected = _merge_turn_texts(answer_turns)
    last_text = str(answer_turns[-1].get("text", "")).strip()
    return judge_inputs[-1] == expected and judge_inputs[-1] != last_text


def _check_loop_exit(case: FlowCase, decisions: list[DecisionCheck]) -> bool | None:
    expected_exit_reasons = [
        decision.expected_exit_reason
        for decision in decisions
        if decision.expected_exit_reason is not None
    ]
    has_loop_expectation = bool(expected_exit_reasons) or any(
        key in case.expected_final
        for key in (
            "max_rounds_before_exit",
            "summary_must_include_unresolved_gaps",
        )
    )
    if not has_loop_expectation:
        return None
    return all(
        decision.passed
        for decision in decisions
        if decision.expected_exit_reason is not None
    )


def _check_summary(
    *,
    case: FlowCase,
    final_actual_action: str,
    session_events: list[str],
) -> bool | None:
    must_generate = bool(case.expected_final.get("must_generate_summary"))
    if not must_generate:
        return None
    if final_actual_action != "finish":
        return False
    session_events.extend(["session_summarized", "session_finished"])
    return True


def _check_hallucination_guard(
    case: FlowCase,
    remediation_prompts: list[dict[str, Any]],
) -> bool | None:
    has_guard = bool(case.hallucination_guard) or bool(remediation_prompts)
    if not has_guard:
        return None

    source_chunk_ids = set(_source_chunk_ids(case))
    reference_point_ids = set(_reference_point_ids(case))
    failures: list[str] = []
    for prompt in remediation_prompts:
        support_ids = set(_int_list(prompt.get("supporting_chunk_ids")))
        if not support_ids.issubset(source_chunk_ids):
            failures.append("remediation prompt introduced chunks outside the question")
        missing_points = set(_string_list(prompt.get("missing_scoring_point_ids")))
        if not missing_points.issubset(reference_point_ids):
            failures.append("remediation prompt introduced unknown scoring points")
        depth = set(_string_list(prompt.get("missing_depth_dimensions")))
        if not depth.issubset(VALID_DEPTH_DIMENSIONS):
            failures.append("remediation prompt introduced unknown depth dimensions")

    guard = case.hallucination_guard
    must_anchor = set(_string_list(guard.get("must_anchor_to")))
    if must_anchor and not must_anchor.issubset(reference_point_ids):
        failures.append("hallucination guard references unknown anchors")
    prompt_text = "\n".join(str(prompt.get("text", "")) for prompt in remediation_prompts)
    for forbidden in _string_list(guard.get("must_not_introduce")):
        if forbidden and forbidden in prompt_text:
            failures.append(f"remediation prompt introduced forbidden term {forbidden!r}")

    return not failures


def _gap_key(gap: dict[str, Any]) -> str:
    if gap.get("type") == "coverage":
        return str(gap.get("scoring_point_id", ""))
    if gap.get("type") == "depth":
        return str(gap.get("dimension", ""))
    if gap.get("type") == "fidelity":
        return str(gap.get("claim", ""))
    return str(gap)


def _reference_point_ids(case: FlowCase) -> list[str]:
    return _string_list(case.question.get("reference_point_ids"))


def _source_chunk_ids(case: FlowCase) -> list[int]:
    return _int_list(case.question.get("source_chunk_ids"))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    ints: list[int] = []
    for item in value:
        try:
            ints.append(int(item))
        except (TypeError, ValueError):
            continue
    return ints


def _round_scores(scores: dict[str, float]) -> dict[str, float]:
    return {
        "coverage": round(float(scores.get("coverage", 0.0)), 2),
        "fidelity": round(float(scores.get("fidelity", 0.0)), 2),
        "depth": round(float(scores.get("depth", 0.0)), 2),
        "total": round(float(scores.get("total", 0.0)), 2),
    }


def _should_compact_context(
    answer_turns: list[dict[str, Any]],
    token_budget_exhausted: bool,
) -> bool:
    return token_budget_exhausted or len(answer_turns) >= 3


def metrics_for(results: list[CaseResult]) -> list[Metric]:
    decision_checks = [
        decision
        for result in results
        for decision in result.decisions
    ]
    remediation_checks = [
        decision
        for decision in decision_checks
        if decision.remediation_target_passed is not None
    ]
    recovery_checks = [
        result.recovery
        for result in results
        if result.recovery is not None
    ]
    return [
        Metric(
            name="branch_accuracy",
            passed=sum(decision.passed for decision in decision_checks)
            + sum(result.final_passed for result in results),
            total=len(decision_checks) + len(results),
        ),
        Metric(
            name="remediation_target_accuracy",
            passed=sum(decision.remediation_target_passed is True for decision in remediation_checks),
            total=len(remediation_checks),
        ),
        _case_metric(results, "cumulative_rejudge_passed"),
        _case_metric(results, "loop_exit_passed"),
        Metric(
            name="context_pack_pass",
            passed=sum(result.context_pack_passed for result in results),
            total=len(results),
        ),
        _case_metric(results, "hallucination_guard_passed"),
        Metric(
            name="recovery_pass",
            passed=sum(check is not None and check.passed for check in recovery_checks),
            total=len(recovery_checks),
        ),
    ]


def _case_metric(results: list[CaseResult], attr: str) -> Metric:
    values = [getattr(result, attr) for result in results]
    applicable = [value for value in values if value is not None]
    return Metric(
        name=attr.replace("_passed", ""),
        passed=sum(value is True for value in applicable),
        total=len(applicable),
    )


def print_result(result: CaseResult) -> None:
    status = "PASS" if result.passed else "FAIL"
    print(
        f"[{status}] {result.fixture_id} category={result.category} "
        f"final={result.final_actual_action}/{result.final_expected_action}"
    )
    for failure in result.failures[:5]:
        print(f"  - {failure}")
    if len(result.failures) > 5:
        print(f"  - ... {len(result.failures) - 5} more")


def render_report(
    *,
    dataset_path: Path,
    results: list[CaseResult],
    metrics: list[Metric],
) -> str:
    lines: list[str] = [
        "# Interview Coach Flow Smoke",
        "",
        f"- dataset: `{dataset_path}`",
        f"- generated_at: `{datetime.now(UTC).isoformat()}`",
        "- mode: offline stubbed Judge; no DB / LLM calls",
        "",
        "## Metrics",
        "",
        "| metric | passed | total | rate |",
        "|--------|--------|-------|------|",
    ]
    for metric in metrics:
        lines.append(f"| {metric.name} | {metric.passed} | {metric.total} | {metric.label} |")

    lines.extend(
        [
            "",
            "## Cases",
            "",
            "| fixture | category | status | final | failures |",
            "|---------|----------|--------|-------|----------|",
        ]
    )
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        failures = "<br>".join(_escape_pipe(failure) for failure in result.failures[:4])
        if len(result.failures) > 4:
            failures += f"<br>... {len(result.failures) - 4} more"
        lines.append(
            f"| `{result.fixture_id}` | {result.category} | {status} | "
            f"{result.final_actual_action}/{result.final_expected_action} | {failures or '-'} |"
        )

    lines.extend(["", "## Decision Details", ""])
    for result in results:
        lines.append(f"### {result.fixture_id}")
        if result.decisions:
            lines.append("")
            lines.append("| step | expected | actual | trigger | exit | status |")
            lines.append("|------|----------|--------|---------|------|--------|")
            for decision in result.decisions:
                status = "PASS" if decision.passed else "FAIL"
                trigger = (
                    f"{decision.actual_triggered_by}/{decision.expected_triggered_by}"
                    if decision.expected_triggered_by
                    else str(decision.actual_triggered_by)
                )
                exit_reason = (
                    f"{decision.actual_exit_reason}/{decision.expected_exit_reason}"
                    if decision.expected_exit_reason
                    else str(decision.actual_exit_reason)
                )
                lines.append(
                    f"| {decision.step} | {decision.expected_action} | "
                    f"{decision.actual_action} | {trigger} | {exit_reason} | {status} |"
                )
        if result.recovery is not None:
            status = "PASS" if result.recovery.passed else "FAIL"
            lines.append(
                f"- recovery: {result.recovery.actual_node}/"
                f"{result.recovery.expected_node} {status}"
            )
        if result.failures:
            lines.append("")
            lines.append("Failures:")
            lines.extend(f"- {failure}" for failure in result.failures)
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _escape_pipe(text: str) -> str:
    return text.replace("|", "\\|")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evals/suites/interview_coach/dataset.flow_smoke.jsonl"),
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("evals/reports"),
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="Print only; do not write a markdown report.",
    )
    args = parser.parse_args()

    cases = load_cases(args.dataset)
    results = [evaluate_case(case) for case in cases]
    metrics = metrics_for(results)

    for result in results:
        print_result(result)

    print("")
    print(f"summary: passed={sum(result.passed for result in results)}/{len(results)}")
    for metric in metrics:
        print(f"{metric.name}={metric.label} ({metric.passed}/{metric.total})")

    if args.no_report:
        return

    args.report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    report_path = args.report_dir / f"interview-coach-flow-smoke-{timestamp}.md"
    report_path.write_text(
        render_report(dataset_path=args.dataset, results=results, metrics=metrics),
        encoding="utf-8",
    )
    print(f"report={report_path}")


if __name__ == "__main__":
    main()
