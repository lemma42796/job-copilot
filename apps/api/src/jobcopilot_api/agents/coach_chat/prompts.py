"""CoachChat prompts — evidence-bound explanation, no scoring."""

from __future__ import annotations

import json

from jobcopilot_api.agents.context_cache import render_chunk_cache_text
from jobcopilot_api.schemas.agents.coach_chat import CoachChatInput
from jobcopilot_api.schemas.agents.quiz_generator import (
    GeneratedQuestion,
    QuizGenChunkInput,
)

PROMPT_NAME = "coach_chat"
PROMPT_VERSION = "v1.0"

SYSTEM = """你是程序员面试陪练里的教练解释 Agent。

你的任务是回答用户对上一轮教练反馈的追问,不是重新评分。

硬约束:
1. 不要输出 Coverage / Fidelity / Depth 新分数,不要声称已经重评。
2. 不要改变题目状态,只解释上一轮为什么这样反馈、应该怎么补。
3. 只能基于本题 question、累计用户答案、上一轮 coach_message/remediation_prompt 和给定 chunks 解释。
4. 如果用户问到 chunks 外的新标准答案,要明确说当前证据不足,建议先补充与本题证据相关的内容。
5. 回答要自然、直接,2-5 句。必要时可引用 [N] chunk 编号,但不要编造编号。

严格 JSON,无前后任何文字:
{"coach_message":"<中文解释>"}"""


def render_task(inp: CoachChatInput, *, include_chunks: bool = False) -> str:
    question = _question_block(inp.question)
    chunks = _chunks_block(inp.chunks) if include_chunks else "见上方固定 chunks 上下文。"
    remediation = json.dumps(
        inp.remediation_prompt or {},
        ensure_ascii=False,
        default=str,
    )
    gaps = json.dumps(inp.unresolved_gaps, ensure_ascii=False, default=str)
    scores = json.dumps(inp.scores or {}, ensure_ascii=False, default=str)

    return f"""【题目】
{question}

【本题证据 chunks】
{chunks}

【累计用户答案】
{inp.cumulative_answer or "<empty>"}

【上一轮教练反馈】
{inp.prior_coach_message or "<none>"}

【上一轮纠偏提示 remediation_prompt(JSON)】
{remediation}

【上一轮未解决缺口 unresolved_gaps(JSON)】
{gaps}

【上一轮分数(JSON,只作背景,不要重新打分)】
{scores}

【用户追问】
{inp.coach_question}

请只解释这个追问,不要重评,不要推进下一题。"""


def render_cache_fallback_user(inp: CoachChatInput) -> str:
    return render_task(inp, include_chunks=True)


def _question_block(question: GeneratedQuestion) -> str:
    points = []
    for point in question.scoring_points:
        evidence = ", ".join(f"[{chunk_id}]" for chunk_id in point.supporting_chunk_ids)
        points.append(
            f'- {point.id} (weight={point.weight:.2f}): "{point.text}", 支撑 chunks: {evidence}'
        )
    return (
        f"type: {question.type}\n"
        f"prompt: {question.prompt}\n"
        f"reference_answer: {question.reference_answer}\n"
        "scoring_points:\n"
        + "\n".join(points)
    )


def _chunks_block(chunks: list[QuizGenChunkInput]) -> str:
    if not chunks:
        return "<empty>"
    return render_chunk_cache_text(chunks)
