"""JdAggregator prompts — 三阶段共三个 prompt(5-AGENT_DESIGN §6.2)。

- batch reduce:同义合并 raw skill batch → partial canonical
- merge:跨 batch 合并 partial → unified canonical
- learning_path:Python 重算后的 requirement 列表 → markdown
"""

from __future__ import annotations

SYSTEM_BATCH = ""  # M2.5:见 §6.2 Stage 1
SYSTEM_MERGE = ""  # M2.5:见 §6.2 Stage 2
SYSTEM_LEARNING_PATH = ""  # M2.5:见 §6.2 Stage 4


def render_user_batch(*args, **kwargs) -> str:
    raise NotImplementedError("M2.5")


def render_user_merge(*args, **kwargs) -> str:
    raise NotImplementedError("M2.5")


def render_user_learning_path(*args, **kwargs) -> str:
    raise NotImplementedError("M2.5")
