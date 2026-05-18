"""JdAggregator prompts — 三阶段共三个 prompt(5-AGENT_DESIGN §6.2)。

- batch reduce:同义合并 raw skill batch → partial canonical
- merge:跨 batch 合并 partial → unified canonical
- learning_path:Python 重算后的 requirement 列表 → markdown
"""

from __future__ import annotations

import json

from jobcopilot_api.schemas.agents.jd_aggregator import (
    RawRequirementItem,
    Requirement,
    RequirementCandidate,
)

PROMPT_NAME_BATCH = "jd_aggregator_batch"
PROMPT_NAME_MERGE = "jd_aggregator_merge"
PROMPT_NAME_PATH = "jd_aggregator_learning_path"
PROMPT_VERSION = "v1.0"

SYSTEM_BATCH = """你是 JobCopilot 的 JD 要求聚合 Agent。任务是把一批 JD 原文短语做同义合并。

硬约束:
1. 只合并输入里已有的短语,不要补行业常识,不要新增没出现过的要求。
2. canonical_text 可以选一个更清晰的代表表达,但必须能从 raw_phrases 直接推出。
3. raw_phrases 必须来自输入 text,不要改写;supporting_jd_ids 必须来自输入 jd_id。
4. 不要计算 frequency;频次由 Python 端按 supporting_jd_ids 重算。
5. category 只能用输入里的类别:职责 / 硬技能 / 软技能 / 经验 / 学历。
6. 跨类别不要强行合并;例如"沟通协作"不能跟"Spring Boot"合一。
7. 输出必须是严格 JSON,不要输出解释文字。"""

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
    items: list[RawRequirementItem],
) -> str:
    rows = [item.model_dump(mode="json") for item in items]
    return (
        f"这是第 {batch_index}/{total_batches} 个 batch。"
        "请合并同义 JD 要求,输出 requirements。\n\n"
        f"输入 raw_items JSON:\n{json.dumps(rows, ensure_ascii=False, indent=2)}"
    )


def render_user_merge(requirements: list[RequirementCandidate]) -> str:
    rows = [item.model_dump(mode="json") for item in requirements]
    return (
        "请把以下 partial canonical requirements 做跨 batch 同义合并,"
        "输出全局 requirements。\n\n"
        "输入 partial_requirements JSON:\n"
        f"{json.dumps(rows, ensure_ascii=False, indent=2)}"
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
        f"requirements JSON:\n{json.dumps(rows, ensure_ascii=False, indent=2)}"
    )
