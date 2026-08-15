"""Process-local admission control shared by all text-generation paths."""

from __future__ import annotations

import asyncio

from jobcopilot_api.settings import settings

_gate: asyncio.Semaphore | None = None
_gate_loop: asyncio.AbstractEventLoop | None = None


def get_llm_admission_gate() -> asyncio.Semaphore:
    """Return the shared gate, rebuilding it only when the event loop changes."""
    global _gate, _gate_loop
    loop = asyncio.get_running_loop()
    if _gate is None or _gate_loop is not loop:
        _gate = asyncio.Semaphore(settings.llm_max_concurrency)
        _gate_loop = loop
    return _gate
