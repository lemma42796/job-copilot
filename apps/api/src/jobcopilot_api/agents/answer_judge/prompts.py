"""AnswerJudge prompts — `answer_judge` v1.0(5-AGENT_DESIGN §4.3 / §4.4)。

落地时填 SYSTEM 常量(含 §4.7 末尾的工具使用约束段)+ render_user。
"""

from __future__ import annotations

SYSTEM = ""  # M2:从 5-AGENT_DESIGN §4.3 + §4.7 末尾拼入


def render_user(*args, **kwargs) -> str:
    raise NotImplementedError("M2")
