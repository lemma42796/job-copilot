"""Session recall markdown filesystem persistence.

The database keeps `recall_md_path` as a logical path under `notes/`; this
module maps that path to a local notes root and writes only generated recall
files. It never accepts arbitrary user paths.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from jobcopilot_api.errors import JobCopilotError
from jobcopilot_api.settings import PROJECT_ROOT, settings

LOGICAL_RECALL_ROOT = PurePosixPath("notes") / "_recall"


class RecallWriteFailedError(JobCopilotError):
    status_code = 500
    code = "recall_write_failed"
    title = "写入 session 沉淀文件失败"


def session_recall_logical_path(session_id: int) -> str:
    if session_id <= 0:
        raise RecallWriteFailedError(f"session_id={session_id} 非法")
    return str(LOGICAL_RECALL_ROOT / f"{session_id}.md")


def write_session_summary_markdown(session_id: int, markdown: str) -> str:
    """Atomically write `notes/_recall/{session_id}.md`.

    Returns the logical path stored in `quiz_sessions.recall_md_path`.
    """
    if not markdown.strip():
        raise RecallWriteFailedError("session summary markdown 为空")

    path = _physical_recall_path(session_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.tmp")
        tmp_path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
        os.replace(tmp_path, path)
    except OSError as exc:
        raise RecallWriteFailedError(f"无法写入 {path}") from exc

    return session_recall_logical_path(session_id)


def read_session_summary_markdown(logical_path: str | None) -> str | None:
    """Read a previously written recall markdown file.

    Invalid or missing paths return None so old sessions can still fall back to
    `agent_state.final_summary.markdown`.
    """
    session_id = _session_id_from_logical_path(logical_path)
    if session_id is None:
        return None

    path = _physical_recall_path(session_id)
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _physical_recall_path(session_id: int) -> Path:
    root = _notes_fs_root()
    path = root / "_recall" / f"{session_id}.md"
    _ensure_under_root(root, path)
    return path


def _notes_fs_root() -> Path:
    configured = settings.notes_fs_root.strip()
    if configured:
        root = Path(configured).expanduser()
        if not root.is_absolute():
            root = PROJECT_ROOT / root
        return root.resolve(strict=False)

    dogfood_root = PROJECT_ROOT / "test-notes" / "llm-notes"
    if settings.env == "dev" and dogfood_root.exists():
        return dogfood_root.resolve(strict=False)
    return (PROJECT_ROOT / "notes").resolve(strict=False)


def _ensure_under_root(root: Path, path: Path) -> None:
    root_resolved = root.resolve(strict=False)
    path_resolved = path.resolve(strict=False)
    try:
        path_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise RecallWriteFailedError("recall 写入路径越界") from exc


def _session_id_from_logical_path(logical_path: str | None) -> int | None:
    if not logical_path:
        return None
    path = PurePosixPath(logical_path)
    if len(path.parts) != 3 or path.parts[:2] != LOGICAL_RECALL_ROOT.parts:
        return None
    filename = path.name
    if not filename.endswith(".md"):
        return None
    raw_id = filename.removesuffix(".md")
    if not raw_id.isdigit():
        return None
    session_id = int(raw_id)
    return session_id if session_id > 0 else None
