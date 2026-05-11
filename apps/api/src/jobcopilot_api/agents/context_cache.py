"""Shared prompt prefix for provider-side Context Cache.

QuizGenerator and AnswerJudge both need the same session chunks. Keeping this
prefix byte-stable lets DashScope reuse the cached prefix while each agent puts
its dynamic task instructions later in the message list.
"""

from __future__ import annotations

from typing import Any

from jobcopilot_api.schemas.agents.quiz_generator import QuizGenChunkInput

CONTEXT_CACHE_MIN_CHARS = 3000

SHARED_CHUNKS_SYSTEM_PREAMBLE = (
    "JobCopilot session fixed note chunks. These chunks are user notes and "
    "the factual context for the following task. Do not answer yet; read and "
    "reuse the chunk ids exactly as written.\n\n"
)


def build_chunk_cache_messages(
    chunks: list[QuizGenChunkInput],
) -> list[dict[str, Any]]:
    """Return the stable message prefix shared by Quiz and Judge."""
    text = render_chunk_cache_text(chunks)
    content: str | list[dict[str, Any]]
    if should_use_explicit_context_cache(text):
        content = [
            {
                "type": "text",
                "text": text,
                "cache_control": {"type": "ephemeral"},
            }
        ]
    else:
        content = text
    return [{"role": "system", "content": content}]


def render_chunk_cache_text(chunks: list[QuizGenChunkInput]) -> str:
    blocks = []
    for idx, chunk in enumerate(chunks, start=1):
        folder = "/".join(chunk.folder_path) if chunk.folder_path else "<root>"
        heading = " > ".join(chunk.heading_path) if chunk.heading_path else "<root>"
        blocks.append(
            f"[{idx}] note: {chunk.note_title} | folder: {folder} | heading: {heading}\n"
            f"{chunk.content}"
        )
    chunks_text = "\n\n".join(blocks)
    return (
        SHARED_CHUNKS_SYSTEM_PREAMBLE
        f"retrieval pipeline chunks(共 {len(chunks)} 个,已按相关性排序):\n\n"
        f"{chunks_text}"
    )


def should_use_explicit_context_cache(text: str) -> bool:
    # DashScope documents the lower bound in tokens; this char threshold is a
    # conservative proxy that avoids marking short prompts.
    return len(text) >= CONTEXT_CACHE_MIN_CHARS
