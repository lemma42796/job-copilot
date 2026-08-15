"""ResumeAdvisor 替写文案漏洞检测正则集(docs/TECH_DESIGN.md)。

LLM 越界写"建议改写为 'XXX'"时 service 层强制重试 / 截断。
dogfood 中持续累积模式。
"""

from __future__ import annotations

import re

FORBIDDEN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"建议改写为"),
    re.compile(r"可以这样写[::]"),
    re.compile(r'^\s*"[^"]+"\s*$'),  # 整段被引号包裹的"文案"
    # dogfood 中持续累积
]


def has_forbidden(text: str | None) -> bool:
    if not text:
        return False
    return any(p.search(text) for p in FORBIDDEN_PATTERNS)
