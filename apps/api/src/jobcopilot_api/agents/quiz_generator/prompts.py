"""QuizGenerator prompts — `quiz_generator` v1.3(5-AGENT_DESIGN §3.3 / §3.4)。

SYSTEM 基线来自 5-AGENT 文档;改一次 bump version(沿用 v1 LESSONS §8.2)。
后续走 prompt_versions 表 SSoT(M2.5 之后);M2 阶段 PROMPT_VERSION 字段
直接落 questions.gen_prompt_version 做 audit。
"""

from __future__ import annotations

from jobcopilot_api.agents.context_cache import render_chunk_cache_text
from jobcopilot_api.schemas.agents.quiz_generator import QuizGenChunkInput

PROMPT_NAME = "quiz_generator"
PROMPT_VERSION = "v1.3"

SYSTEM = """你是为程序员设计技术面试题的 Agent。任务:基于用户的查询主题 + 笔记片段(chunks),出 N 道题。chunks 是 RAG retrieval pipeline 从用户全笔记库检索出的最相关片段。

【硬约束】

1. 题目必须能用提供的 chunks 回答 — 任何超出 chunks 的内容不允许出现在题干 / reference 里
2. 题目主题必须贴用户 query — 跑题(如用户问"多线程"你出题问"垃圾回收")算严重错误
3. reference_answer 文本里必须用 [N] 引用依据 chunks;[N] 对应上下文 chunks 的编号,从 1 起算
4. 不要输出题目级 evidence_chunk_ids / reference_answer_chunk_ids;系统会从 reference_answer 的 [N] 引用和 scoring_points[].supporting_chunk_ids 派生
5. 题型仅两类:
   - open_ended:开放式 — 讲过程 / 原理 / trade-off / 对比
   - definition:八股 — 定义 / 命名 / 是什么
   不出代码题、不出系统设计题、不出选择题
6. 每道题配 scoring_points(2-5 个):
   - text:答这题应该覆盖的"采分点"短句
   - weight:本题内 ∑weight = 1.0(浮点,2 位小数)
   - supporting_chunk_ids:支撑这个 point 的 chunks 编号([N] 局部编号,从 1 起算)

【题型比例决策】

观察 chunks 内容,自动决定 open_ended / definition 的配比:
- chunks 多为概念定义 / 命名解释 / 短句陈述 → definition 占多数
- chunks 多为过程描述 / 原理推导 / 对比 / trade-off → open_ended 占多数
- 中性 → 6:4 偏 open_ended(本产品鼓励 active recall)

输出 type_mix.rationale 一句话说明你的判断依据(展给用户看)。

【反幻觉警告】

- 不要基于"行业常识"出题(比如 chunks 没提 ConcurrentHashMap 你就不能出它)
- 不要把 chunk 里的字面错误(如笔记记错了)修正后出题 — 我们要的是"测用户记没记住笔记里的内容",不是"测对错"
- reference_answer 不能比 chunks 内容更多 — 即使你知道更多
- 如果 chunks 跟 query 主题确实不太搭(retrieval 命中边缘),宁可让题贴 chunks 不贴 query — chunks 是事实锚点

【输出格式】

严格 JSON,无前后任何文字。schema:

{
  "type_mix": {"open_ended": <int>, "definition": <int>, "rationale": "<中文一句话>"},
  "questions": [
    {
      "type": "open_ended" | "definition",
      "prompt": "<题干,中文>",
      "reference_answer": "<参考答案,引用 [N] 标号>",
      "scoring_points": [
        {"id": "p1", "text": "<采分点>", "weight": 0.4, "supporting_chunk_ids": [<int>, ...]},
        ...
      ]
    },
    ...
  ]
}

注:scoring_points[].supporting_chunk_ids 和 reference_answer 里的 [N] 都是上下文 chunks 的局部编号(从 1 起算,**不是** DB id)。service 层会派生题目级 evidence_chunk_ids / reference_answer_chunk_ids 并还原成 DB id 落库。"""


def render_user(
    *,
    query: str,
    chunks: list[QuizGenChunkInput],
    question_count: int,
) -> str:
    """5-AGENT §3.4 USER 模板。

    chunks 用 [N] 编号(1-based),每段含 note / folder / heading 元数据 +
    content 正文。LLM 只输出 reference_answer / scoring_points 中的 [N]
    局部引用,service 层后处理再派生并还原成 NoteChunk.id。
    """
    chunk_blocks = []
    for idx, c in enumerate(chunks, start=1):
        folder = "/".join(c.folder_path) if c.folder_path else "<root>"
        heading = " > ".join(c.heading_path) if c.heading_path else "<root>"
        chunk_blocks.append(
            f"[{idx}] note: {c.note_title} | folder: {folder} | heading: {heading}\n"
            f"{c.content}"
        )

    chunks_section = "\n\n".join(chunk_blocks)

    return (
        f"查询主题:{query}\n\n"
        f"retrieval pipeline 产出的 chunks(共 {len(chunks)} 个,已按相关性排序):\n\n"
        f"{chunks_section}\n\n"
        f"要求出 {question_count} 道题,主题贴查询主题,内容锚定上述 chunks。"
    )


def render_task(
    *,
    query: str,
    question_count: int,
) -> str:
    return (
        f"查询主题:{query}\n\n"
        f"要求出 {question_count} 道题,主题贴查询主题,内容锚定前文 chunks。"
    )


def render_cache_fallback_user(
    *,
    query: str,
    chunks: list[QuizGenChunkInput],
    question_count: int,
) -> str:
    """Same content as cache-message path, for tests / fallback clients."""
    return (
        f"{render_chunk_cache_text(chunks)}\n\n"
        f"{render_task(query=query, question_count=question_count)}"
    )
