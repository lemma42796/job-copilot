"""ResumeAdvisor prompts — `resume_advisor` v1.0(docs/TECH_DESIGN.md)。"""

from __future__ import annotations

SYSTEM = ""  # M3:从 docs/TECH_DESIGN.md 拷入


def render_user(*args, **kwargs) -> str:
    raise NotImplementedError("M3")
