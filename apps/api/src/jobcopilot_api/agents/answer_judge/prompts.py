"""AnswerJudge prompts — `answer_judge` v1.2(5-AGENT_DESIGN §4.3 / §4.4)。"""

from __future__ import annotations

from jobcopilot_api.agents.context_cache import render_chunk_cache_text
from jobcopilot_api.schemas.agents.quiz_generator import (
    GeneratedQuestion,
    QuizGenChunkInput,
)

PROMPT_NAME = "answer_judge"
PROMPT_VERSION = "v1.2"

SYSTEM = """你是评估程序员技术问答的 Judge Agent。三层评分:

【Coverage(覆盖度)】

对照 reference_points 列表,逐 point 判断用户答案是否覆盖:
- hit:完整覆盖该 point(允许同义 / 缩略 / 中英混合 / 顺序不同)
- partial:覆盖了一部分但缺细节 / 缺步骤 / 缺关键术语
- miss:完全没提

每个 point 必须配 user_excerpt:用户答里对应的原文片段(label=miss 时填 null)。

【Fidelity(忠实度)】

把用户答案拆成若干"声明"(claims),每条对照 chunks 判断:
- supported:chunks 里直接或间接支持(同义改写视为支持)
- inferred:chunks 没明说但属于专业常识 / 合理外推 — 算可接受
- fabricated:跟 chunks 矛盾,或 chunks 没说且超出常识范畴

每条 claim 必须配 chunk_ids:支持该声明的 chunks 编号([N] 标号)。fabricated 的 chunk_ids 留空数组。

【Depth(深度)】

判断答案是否覆盖三个深度维度(每个二值 covered: true/false):
- tradeoff:讲了为什么这样设计(优劣 / 取舍 / 替代方案对比)
- why:解释了底层动机 / 设计目标
- boundary:提了适用 / 不适用场景 / 边界条件

每个维度 covered=true 时配 excerpt(用户答的对应片段);false 时填 null。

【硬约束】

1. 不要苛求字面匹配 — 同义 / 缩略 / 中英混合 / 顺序不同视为命中
2. 用户提到的常识(语言 / 框架 / 协议的公认行为)即使 chunks 没明说,标 inferred,不要标 fabricated
3. 你给的 score_raw 是 0-1 浮点自评,Python 端会按 evidence label 重算总分 — 你不用纠结分数精度,只要 evidence label 准
4. 不要"鼓励性"评语 — reasoning 字段直接陈述事实("命中 p1,p2 漏讲触发条件")
5. user_excerpt / chunk_ids 必须是真实存在的引用,不要编造

【输出格式】

严格 JSON,无前后任何文字。schema:

{
  "coverage_evidence": {
    "points": [
      {"id": "<reference_point.id>", "label": "hit"|"partial"|"miss", "user_excerpt": "<...>"|null}
    ],
    "score_raw": <float 0-1>,
    "reasoning": "<中文一句话>"
  },
  "fidelity_evidence": {
    "claims": [
      {"text": "<用户原文片段>", "label": "supported"|"inferred"|"fabricated", "chunk_ids": [<int>, ...]}
    ],
    "score_raw": <float 0-1>,
    "reasoning": "<中文一句话>"
  },
  "depth_evidence": {
    "dimensions": {
      "tradeoff": {"covered": <bool>, "excerpt": "<...>"|null},
      "why":      {"covered": <bool>, "excerpt": "<...>"|null},
      "boundary": {"covered": <bool>, "excerpt": "<...>"|null}
    },
    "score_raw": <float 0-1>,
    "reasoning": "<中文一句话>"
  }
}

注:claims 里的 chunk_ids 默认写上下文 chunks 的 [N] 标号;若采用工具结果作为证据,
写工具返回的 ref_id。不要写数据库 id。"""

TOOL_SYSTEM_APPENDIX = """

【工具使用】

在 fidelity 评分时,任何想标 fabricated 的 claim,必须先调用
lookup_in_notes_global(claim) 验证一次。流程:

1. 看到一条声明,判断它在本题 chunks 里没支撑
2. 调用 lookup_in_notes_global(claim_text)
3. 如果工具返回的 chunks 里有支持该声明的内容(跟用户答的语义对得上),
   标 supported
4. 没匹配上,才标 fabricated

不要为 supported / inferred 的 claim 调工具。每个用户答案最多 5 次工具调用。
前文上下文 chunks 仍用 [N] 编号;工具返回的全库 chunks 带 ref_id。如果你采用
工具结果作为证据,claims[].chunk_ids 写工具返回的 ref_id,不要写 chunk_id。"""

SYSTEM_WITH_LOOKUP_TOOL = f"{SYSTEM}{TOOL_SYSTEM_APPENDIX}"


def render_user(
    *,
    question: GeneratedQuestion,
    chunks: list[QuizGenChunkInput],
    user_answer: str,
) -> str:
    """5-AGENT §4.4 USER 模板。

    chunks 用 [N] 编号(1-based),与 question.source_chunk_ids /
    reference_points.evidence_chunk_ids 使用同一套局部编号。
    """
    points = []
    for p in question.reference_points:
        evidence_ids = ", ".join(f"[{n}]" for n in p.evidence_chunk_ids)
        points.append(
            f'- {p.id} (weight={p.weight:.2f}): "{p.text}",'
            f"支撑 chunks: {evidence_ids}"
        )

    chunk_blocks = []
    for idx, c in enumerate(chunks, start=1):
        folder = "/".join(c.folder_path) if c.folder_path else "<root>"
        heading = " > ".join(c.heading_path) if c.heading_path else "<root>"
        chunk_blocks.append(
            f"[{idx}] note: {c.note_title} | folder: {folder} | heading: {heading}\n"
            f"{c.content}"
        )

    points_section = "\n".join(points)
    chunks_section = "\n\n".join(chunk_blocks)

    return (
        f"题目:{question.prompt}\n"
        f"题型:{question.type}\n\n"
        "reference_points:\n"
        f"{points_section}\n\n"
        "reference_answer:\n"
        f"{question.reference_answer}\n\n"
        f"chunks(共 {len(chunks)} 个):\n\n"
        f"{chunks_section}\n\n"
        "用户答案:\n"
        f"{user_answer}"
    )


def render_task(
    *,
    question: GeneratedQuestion,
    user_answer: str,
) -> str:
    points = []
    for p in question.reference_points:
        evidence_ids = ", ".join(f"[{n}]" for n in p.evidence_chunk_ids)
        points.append(
            f'- {p.id} (weight={p.weight:.2f}): "{p.text}",'
            f"支撑 chunks: {evidence_ids}"
        )

    points_section = "\n".join(points)
    return (
        f"题目:{question.prompt}\n"
        f"题型:{question.type}\n\n"
        "reference_points:\n"
        f"{points_section}\n\n"
        "reference_answer:\n"
        f"{question.reference_answer}\n\n"
        "用户答案:\n"
        f"{user_answer}"
    )


def render_cache_fallback_user(
    *,
    question: GeneratedQuestion,
    chunks: list[QuizGenChunkInput],
    user_answer: str,
) -> str:
    return (
        f"{render_chunk_cache_text(chunks)}\n\n"
        f"{render_task(question=question, user_answer=user_answer)}"
    )
