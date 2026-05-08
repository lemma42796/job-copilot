"""笔记 CRUD + 批量入库 + 树形导航(M1,2-TECH §5.1 + 4-API_SPEC §3)。

职责(2-TECH §4.3):
- service 层做业务编排 + 事务 + 数据库读写,调 chunk_service 切片
- **不**直接调 LLM(embedding 由 workers/embed_worker 异步补)

唯一约束:`(folder_path, title) WHERE deleted_at IS NULL` 由 alembic 0016
`uq_notes_folder_title` 保证;create / update / move 都靠 IntegrityError
统一映射成 DuplicateFolderTitleError(409 duplicate_folder_title)。

批量入库走前端 File System Access API:用户在浏览器选目录 / 选单篇 .md,
前端读出 content + 相对路径后整批 POST。后端只接结构化数据,不解压。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timezone

from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from jobcopilot_api.errors import ConflictError, NotFoundError
from jobcopilot_api.models.note import Note
from jobcopilot_api.models.note_chunk import NoteChunk
from jobcopilot_api.schemas.notes import (
    BatchImportReport,
    NoteBatchImportItem,
    NoteCreateIn,
    NoteOut,
    NoteUpdateIn,
    TreeNode,
)
from jobcopilot_api.services import chunk_service


# --- 业务错误 ----------------------------------------------------------


class NoteNotFoundError(NotFoundError):
    code = "note_not_found"
    title = "笔记不存在"


class DuplicateFolderTitleError(ConflictError):
    code = "duplicate_folder_title"
    title = "同 folder + title 已存在"


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


# --- 批量入库 ----------------------------------------------------------


async def batch_import(
    session: AsyncSession,
    items: list[NoteBatchImportItem],
    root_folder: str | None,
    overwrite: bool,
) -> BatchImportReport:
    """批量入库:逐条查重 + chunk(同事务)。

    错误处理:
    - 同 folder + title 已存在 + overwrite=False → 跳过 + reason=duplicate_folder_title
    - 同 folder + title 已存在 + overwrite=True → 替换 content_md + 重切 chunks
    - root_folder 非空时,所有 item.folder_path 前面拼上 root_folder
    - 单条失败不影响整批(并发场景 IntegrityError 兜底)
    """
    imported = 0
    skipped = 0
    skipped_reasons: list[dict[str, str]] = []
    note_ids: list[int] = []
    prefix = [root_folder] if root_folder else []

    for item in items:
        folder_path = prefix + list(item.folder_path)
        title = item.title
        path_label = "/".join(folder_path + [f"{title}.md"])

        existing = await _find_active_by_folder_title(session, folder_path, title)
        if existing is not None:
            if not overwrite:
                skipped += 1
                skipped_reasons.append(
                    {"path": path_label, "reason": "duplicate_folder_title"}
                )
                continue
            existing.content_md = item.content_md
            await session.flush()
            await chunk_service.rechunk_note(session, existing.id)
            imported += 1
            note_ids.append(existing.id)
            continue

        note = Note(
            folder_path=folder_path,
            title=title,
            content_md=item.content_md,
            source="local_md",
        )
        session.add(note)
        try:
            await session.flush()
        except IntegrityError as e:
            await session.rollback()
            # 并发场景理论上能命中(单用户 MVP 几乎不可能):报跳过
            skipped += 1
            skipped_reasons.append(
                {"path": path_label, "reason": "duplicate_folder_title"}
            )
            _ = e
            continue

        await chunk_service.rechunk_note(session, note.id)
        imported += 1
        note_ids.append(note.id)

    return BatchImportReport(
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
