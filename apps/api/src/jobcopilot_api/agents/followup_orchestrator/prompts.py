"""FollowupOrchestrator prompts(M3)— 追问题干生成。

`followup_question` v1.0:基于 layer1 evidence(coverage miss / depth 漏维度)
生成一道追问,让用户对薄弱处再答一次。
"""

from __future__ import annotations

SYSTEM = ""  # M3


def render_user(*args, **kwargs) -> str:
    raise NotImplementedError("M3")
