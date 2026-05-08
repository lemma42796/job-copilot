"""JdParser prompts — `jd_parser` v1.0(5-AGENT_DESIGN §5.2)。"""

from __future__ import annotations

SYSTEM = ""  # M2.5:从 5-AGENT_DESIGN §5.2 拷入


def render_user(*args, **kwargs) -> str:
    raise NotImplementedError("M2.5")
