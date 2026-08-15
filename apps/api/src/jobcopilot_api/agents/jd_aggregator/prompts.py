"""JdAggregator prompts — 三阶段共三个 prompt(docs/TECH_DESIGN.md)。

- batch reduce:同义合并 raw skill batch → partial canonical
- merge:跨 batch 合并 partial → unified canonical
- learning_path:Python 重算后的 requirement 列表 → markdown
"""

from __future__ import annotations

import json

from jobcopilot_api.schemas.agents.jd_aggregator import (
    ParsedJdForAggregation,
    Requirement,
    RequirementCandidate,
)

PROMPT_NAME_BATCH = "jd_aggregator_batch"
PROMPT_NAME_MERGE = "jd_aggregator_merge"
PROMPT_NAME_PATH = "jd_aggregator_learning_path"
PROMPT_VERSION = "v1.0"

SYSTEM_BATCH = """你是 JobCopilot 的 JD 技术栈抽取 Agent。任务是阅读一批 JD 原文,找出多个 JD 共同要求的具体技术点,合并同义说法,输出技术栈清单。

硬约束:
1. 只输出至少涉及 2 个 JD 的具体技术点;只在单个 JD 出现的内容不要输出。
2. 最多输出 40 项;优先输出高频、可学习、可面试复习的技术点。
3. canonical_text 必须是具体技术 / 工具 / 框架 / 数据库 / 方法名,如"Python 编程语言"、"MySQL"、"FastAPI"、"RAG"、"向量检索"、"Function Calling"。
4. 不要输出宽泛岗位能力、业务方向、职责句或软技能,如"Python 后端开发"、"AI 产品落地"、"业务系统建设"、"工程质量意识"。
5. 遇到组合表述要拆成具体技术点:如"Python 后端开发"归为"Python 编程语言";若同时明确出现 FastAPI,另列"FastAPI"。
6. category 默认用"硬技能";只有"API 设计"、"系统设计"、"任务编排"这类明确工程技术实践可用"职责";不要输出"软技能 / 经验 / 学历"。
7. raw_phrases 每项最多 5 个代表短语,必须来自 JD 原文。
8. supporting_jd_ids 必须列出涉及该技术点的 JD id,不要编造不存在的 JD id。
9. 不要计算 frequency;后端会按 supporting_jd_ids 重算。
10. 只输出严格 JSON,不要解释。"""

SYSTEM_MERGE = """你是 JobCopilot 的 JD 要求二次合并 Agent。任务是把多个 batch 产出的 canonical requirements 再做跨 batch 同义合并。

硬约束:
1. 只合并输入里已有 canonical / raw_phrases,不要补新要求。
2. raw_phrases 必须来自输入 raw_phrases;supporting_jd_ids 必须来自输入 supporting_jd_ids。
3. 不要计算 frequency;频次由 Python 端按 supporting_jd_ids 重算。
4. category 只能是职责 / 硬技能 / 软技能 / 经验 / 学历。
5. 输出尽量去重,但不要把语义不同的技能硬合一,例如 MySQL 和 Redis 分开。
6. 输出必须是严格 JSON,不要输出解释文字。"""

SYSTEM_LEARNING_PATH = """你是 JobCopilot 的 JD 学习路径生成 Agent。任务是把已经聚合、排序、重算频次的岗位要求转成可执行 markdown。

硬约束:
1. 只根据输入 requirement 写,不要编造课程名、书名、网站、培训资源或外部链接。
2. 按高频(>=0.8)、中频(0.5-0.8)、补充要求(<0.5)分组。
3. 优先展示硬技能和职责,软技能 / 学历 / 经验单独收束。
4. 每条尽量带频次百分比,帮助用户判断优先级。
5. 输出必须是严格 JSON,字段只有 learning_path_md,不要输出解释文字。"""


def render_user_batch(
    *,
    batch_index: int,
    total_batches: int,
    jds: list[ParsedJdForAggregation],
) -> str:
    return (
        f"这是第 {batch_index}/{total_batches} 个 batch。"
        "这里有一组 JD 原文。请找出这些 JD 上相同或相近的技能 / 技术要求,"
        "合并后标注每个技术要求涉及哪些 JD。\n\n"
        "输出示例语义:Python 编程语言【涉及 JD:1,2,3】、MySQL【涉及 JD:2,5,8】。\n"
        "实际输出必须符合 JSON schema。\n\n"
        f"JD 原文:\n{_render_jd_blocks(jds)}"
    )


def render_user_merge(requirements: list[RequirementCandidate]) -> str:
    return (
        "请把以下 partial canonical requirements 做跨 batch 同义合并,"
        "输出全局 requirements。\n\n"
        "输入 partial_requirements TSV,列顺序为 category / canonical_text / raw_phrases / supporting_jd_ids。\n"
        f"{_render_requirement_candidate_tsv(requirements)}"
    )


def render_user_learning_path(
    *,
    requirements: list[Requirement],
    jd_count: int,
) -> str:
    rows = [item.model_dump(mode="json") for item in requirements]
    return (
        f"本次分析覆盖 {jd_count} 条 JD。"
        "请基于以下 requirement 列表生成 markdown 学习路径。\n\n"
        f"requirements JSON:\n{json.dumps(rows, ensure_ascii=False, separators=(',', ':'))}"
    )


def _render_jd_blocks(jds: list[ParsedJdForAggregation]) -> str:
    blocks: list[str] = []
    for jd in jds:
        text = jd.raw_text.strip() or _parsed_jd_text(jd)
        blocks.append(f"### JD {jd.jd_id}\n{text}")
    return "\n\n".join(blocks)


def _parsed_jd_text(jd: ParsedJdForAggregation) -> str:
    parsed = jd.parsed
    lines = [f"岗位:{parsed.title}"]
    if parsed.responsibilities:
        lines.append("职责:" + "; ".join(parsed.responsibilities))
    if parsed.hard_skills:
        lines.append("硬技能:" + ", ".join(parsed.hard_skills))
    if parsed.soft_skills:
        lines.append("软技能:" + ", ".join(parsed.soft_skills))
    if parsed.experience_years:
        lines.append(f"经验:{parsed.experience_years}")
    if parsed.education:
        lines.append(f"学历:{parsed.education}")
    return "\n".join(lines)


def _render_requirement_candidate_tsv(items: list[RequirementCandidate]) -> str:
    rows = ["category\tcanonical_text\traw_phrases\tsupporting_jd_ids"]
    for item in items:
        rows.append(
            "\t".join(
                [
                    item.category,
                    _tsv_cell(item.canonical_text),
                    _tsv_cell(" | ".join(item.raw_phrases)),
                    _join_ids(item.supporting_jd_ids),
                ]
            )
        )
    return "\n".join(rows)


def _join_ids(ids: list[int]) -> str:
    return ",".join(str(item) for item in sorted(set(ids)))


def _tsv_cell(value: str) -> str:
    return " ".join(value.replace("\t", " ").split())
