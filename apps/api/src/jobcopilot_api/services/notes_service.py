"""笔记 CRUD + zip 入库 + 树形导航(M1,2-TECH §5.1 + 4-API_SPEC §3)。

职责(2-TECH §4.3):
- service 层做业务编排 + 事务 + 数据库读写,调 chunk_service 切片
- **不**直接调 LLM(embedding 由 workers/embed_worker 异步补)

唯一约束:`(folder_path, title) WHERE deleted_at IS NULL` 由 alembic 0016
`uq_notes_folder_title` 保证;create / update / move 都靠 IntegrityError
统一映射成 DuplicateFolderTitleError(409 duplicate_folder_title)。
"""

from __future__ import annotations

import io
import zipfile
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timezone

from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from jobcopilot_api.errors import (
    ConflictError,
    JobCopilotError,
    NotFoundError,
    ValidationError,
)
from jobcopilot_api.models.note import Note
from jobcopilot_api.models.note_chunk import NoteChunk
from jobcopilot_api.schemas.notes import (
    NoteCreateIn,
    NoteOut,
    NoteUpdateIn,
    TreeNode,
    UploadZipReport,
)
from jobcopilot_api.services import chunk_service

# zip 上限 50MB(4-API_SPEC §3.1)
MAX_ZIP_BYTES = 50 * 1024 * 1024


# --- 业务错误 ----------------------------------------------------------


class NoteNotFoundError(NotFoundError):
    code = "note_not_found"
    title = "笔记不存在"


class DuplicateFolderTitleError(ConflictError):
    code = "duplicate_folder_title"
    title = "同 folder + title 已存在"


class InvalidZipError(ValidationError):
    code = "invalid_zip"
    title = "zip 文件解析失败"


class ZipTooLargeError(JobCopilotError):
    status_code = 413
    code = "zip_too_large"
    title = "zip 文件过大(上限 50MB)"


# --- ORM <-> Pydantic 转换 ---------------------------------------------


def _to_out(note: Note) -> NoteOut:
    return NoteOut(
        id=note.id,
        folder_path=list(note.folder_path),
        title=note.title,
        content_md=note.content_md,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


async def _get_active_or_404(session: AsyncSession, note_id: int) -> Note:
    note = await session.get(Note, note_id)
    if note is None or note.deleted_at is not None:
        raise NoteNotFoundError(f"note {note_id} not found")
    return note


def _wrap_unique_violation(exc: IntegrityError) -> Exception:
    """uq_notes_folder_title 冲突 → DuplicateFolderTitleError;其他 → 原样。"""
    msg = str(exc.orig) if exc.orig else str(exc)
    if "uq_notes_folder_title" in msg:
        return DuplicateFolderTitleError("同 folder + title 已存在")
    return exc


# --- CRUD --------------------------------------------------------------


async def create_note(
    session: AsyncSession, payload: NoteCreateIn
) -> NoteOut:
    note = Note(
        folder_path=list(payload.folder_path),
        title=payload.title,
        content_md=payload.content_md,
        source="web_editor",
    )
    session.add(note)
    try:
        await session.flush()
    except IntegrityError as e:
        await session.rollback()
        raise _wrap_unique_violation(e) from e

    await chunk_service.rechunk_note(session, note.id)
    return _to_out(note)


async def get_note(session: AsyncSession, note_id: int) -> NoteOut:
    return _to_out(await _get_active_or_404(session, note_id))


async def update_note(
    session: AsyncSession, note_id: int, payload: NoteUpdateIn
) -> NoteOut:
    note = await _get_active_or_404(session, note_id)

    content_changed = False
    folder_or_title_changed = False

    if payload.title is not None and payload.title != note.title:
        note.title = payload.title
        folder_or_title_changed = True

    if payload.folder_path is not None and list(payload.folder_path) != list(
        note.folder_path
    ):
        note.folder_path = list(payload.folder_path)
        folder_or_title_changed = True

    if payload.content_md is not None and payload.content_md != note.content_md:
        note.content_md = payload.content_md
        content_changed = True

    try:
        await session.flush()
    except IntegrityError as e:
        await session.rollback()
        raise _wrap_unique_violation(e) from e

    # content 改了 → 重切;folder 改了 → chunks.folder_path 反规范化也要刷新
    if content_changed or folder_or_title_changed:
        await chunk_service.rechunk_note(session, note.id)

    return _to_out(note)


async def move_note(
    session: AsyncSession, note_id: int, new_folder_path: list[str]
) -> NoteOut:
    note = await _get_active_or_404(session, note_id)
    if list(note.folder_path) == list(new_folder_path):
        return _to_out(note)

    note.folder_path = list(new_folder_path)
    try:
        await session.flush()
    except IntegrityError as e:
        await session.rollback()
        raise _wrap_unique_violation(e) from e

    # chunks.folder_path 反规范化,move 后必须重切
    await chunk_service.rechunk_note(session, note.id)
    return _to_out(note)


async def delete_note(session: AsyncSession, note_id: int) -> None:
    """soft delete + 物理删 chunks(2-TECH §4.3:chunks 是 derived,note 软删后切片不应可见)。

    M1 简化:不支持恢复软删 note;真要恢复走 rechunk_note 重新切。
    """
    note = await _get_active_or_404(session, note_id)
    note.deleted_at = datetime.now(timezone.utc)
    # 物理删 chunks(同事务)
    await session.execute(sql_delete(NoteChunk).where(NoteChunk.note_id == note.id))
    await session.flush()


# --- 树形导航 ----------------------------------------------------------


async def get_tree(session: AsyncSession) -> list[TreeNode]:
    """按 folder_path 聚合所有 active notes 成树。

    简单实现:加载全部 notes(MVP 笔记量 < 200,内存 OK),Python 端按 folder_path
    深度遍历构建。生产量大再切到 SQL 端聚合(WITH RECURSIVE)。
    """
    stmt = (
        select(Note)
        .where(Note.deleted_at.is_(None))
        .order_by(Note.folder_path, Note.title)
    )
    result = await session.execute(stmt)
    notes = result.scalars().all()
    return _build_tree(notes)


def _build_tree(notes: Iterable[Note]) -> list[TreeNode]:
    """递归把 notes 聚合到 folder_path 路径树。

    folder_path 当 key,深层 folder 是浅层的 child;每层 TreeNode.notes 装该
    folder 直挂的 notes(不下钻到子 folder)。
    """
    by_folder: dict[tuple[str, ...], list[Note]] = defaultdict(list)
    all_folders: set[tuple[str, ...]] = set()

    for note in notes:
        path_tuple = tuple(note.folder_path)
        by_folder[path_tuple].append(note)
        # 把所有祖先路径也加入 — 让中间空 folder 也出现在树里
        for i in range(1, len(path_tuple) + 1):
            all_folders.add(path_tuple[:i])

    def build_node(prefix: tuple[str, ...]) -> TreeNode:
        depth = len(prefix)
        children_prefixes = sorted(
            f for f in all_folders if len(f) == depth + 1 and f[:depth] == prefix
        )
        return TreeNode(
            folder_path=list(prefix),
            notes=[_to_out(n) for n in by_folder.get(prefix, [])],
            children=[build_node(c) for c in children_prefixes],
        )

    roots = sorted(f for f in all_folders if len(f) == 1)
    return [build_node(r) for r in roots]


# --- zip 上传 ----------------------------------------------------------


async def upload_zip(
    session: AsyncSession,
    file_bytes: bytes,
    root_folder: str | None,
    overwrite: bool,
) -> UploadZipReport:
    """zip 上传:解压 → 扫 .md → 按相对路径解析 folder_path → 入库。

    错误处理:
    - 文件 > 50MB → ZipTooLargeError(413)
    - zipfile 解析失败 → InvalidZipError(400)
    - 同 folder + title 已存在 + overwrite=False → 跳过 + reason=duplicate_folder_title
    - 非 .md 文件 → 跳过 + reason=non_markdown_file
    """
    if len(file_bytes) > MAX_ZIP_BYTES:
        raise ZipTooLargeError(
            f"zip 大小 {len(file_bytes)} 超上限 {MAX_ZIP_BYTES}"
        )

    try:
        zf = zipfile.ZipFile(io.BytesIO(file_bytes))
    except zipfile.BadZipFile as e:
        raise InvalidZipError(f"zip 解析失败: {e}") from e

    imported = 0
    skipped = 0
    skipped_reasons: list[dict[str, str]] = []
    note_ids: list[int] = []
    prefix = [root_folder] if root_folder else []

    for entry in zf.infolist():
        if entry.is_dir():
            continue
        if not entry.filename.lower().endswith(".md"):
            skipped += 1
            skipped_reasons.append(
                {"path": entry.filename, "reason": "non_markdown_file"}
            )
            continue

        # 解析相对路径 → folder_path + title
        parts = [p for p in entry.filename.replace("\\", "/").split("/") if p]
        if not parts:
            skipped += 1
            skipped_reasons.append(
                {"path": entry.filename, "reason": "empty_path"}
            )
            continue

        title = parts[-1].removesuffix(".md").removesuffix(".MD")
        folder_path = prefix + parts[:-1]

        try:
            content_md = zf.read(entry).decode("utf-8")
        except UnicodeDecodeError:
            skipped += 1
            skipped_reasons.append(
                {"path": entry.filename, "reason": "non_utf8"}
            )
            continue

        # 查重
        existing = await _find_active_by_folder_title(
            session, folder_path, title
        )
        if existing is not None:
            if not overwrite:
                skipped += 1
                skipped_reasons.append(
                    {"path": entry.filename, "reason": "duplicate_folder_title"}
                )
                continue
            existing.content_md = content_md
            await session.flush()
            await chunk_service.rechunk_note(session, existing.id)
            imported += 1
            note_ids.append(existing.id)
            continue

        note = Note(
            folder_path=folder_path,
            title=title,
            content_md=content_md,
            source="zip_upload",
        )
        session.add(note)
        try:
            await session.flush()
        except IntegrityError as e:
            await session.rollback()
            # 并发场景理论上能命中(单用户 MVP 几乎不可能):报跳过
            skipped += 1
            skipped_reasons.append(
                {"path": entry.filename, "reason": "duplicate_folder_title"}
            )
            _ = e
            continue

        await chunk_service.rechunk_note(session, note.id)
        imported += 1
        note_ids.append(note.id)

    return UploadZipReport(
        imported=imported,
        skipped=skipped,
        skipped_reasons=skipped_reasons,
        note_ids=note_ids,
    )


async def _find_active_by_folder_title(
    session: AsyncSession, folder_path: list[str], title: str
) -> Note | None:
    stmt = select(Note).where(
        Note.folder_path == folder_path,
        Note.title == title,
        Note.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()
