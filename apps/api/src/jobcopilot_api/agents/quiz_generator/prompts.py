"""QuizGenerator prompts — `quiz_generator` v1.0(5-AGENT_DESIGN §3.3 / §3.4)。

落地时填 SYSTEM 常量 + render_user。SSoT 在 prompt_versions 表
(2-TECH_DESIGN §4.3,加载走 infra/prompts.py LoadedPrompt)。
"""

from __future__ import annotations

SYSTEM = ""  # M1:从 5-AGENT_DESIGN §3.3 拷入


def render_user(*args, **kwargs) -> str:
    raise NotImplementedError("M1")
