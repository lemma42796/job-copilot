"""笔记 CRUD + zip 入库(M1,2-TECH_DESIGN §5.1 + 4-API_SPEC §3)。

职责:
- 单篇 CRUD(create / get / update / move / soft-delete)
- zip unpack:相对路径 → folder_path,扫 .md → 调 chunk_service 切片入库
- 树形导航:按 folder_path 聚合(GET /api/notes/tree)

不做:LLM 调用 / embedding(embedding 由 workers/embed_worker 异步补)。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from jobcopilot_api.schemas.notes import (
    NoteCreateIn,
    NoteOut,
    NoteUpdateIn,
    TreeNode,
    UploadZipReport,
)


async def create_note(session: AsyncSession, payload: NoteCreateIn) -> NoteOut:
    raise NotImplementedError("M1")


async def get_note(session: AsyncSession, note_id: int) -> NoteOut:
    raise NotImplementedError("M1")


async def update_note(
    session: AsyncSession, note_id: int, payload: NoteUpdateIn
) -> NoteOut:
    raise NotImplementedError("M1")


async def delete_note(session: AsyncSession, note_id: int) -> None:
    raise NotImplementedError("M1")


async def move_note(
    session: AsyncSession, note_id: int, new_folder_path: list[str]
) -> NoteOut:
    raise NotImplementedError("M1")


async def get_tree(session: AsyncSession) -> list[TreeNode]:
    raise NotImplementedError("M1")


async def upload_zip(
    session: AsyncSession,
    file_bytes: bytes,
    root_folder: str | None,
    overwrite: bool,
) -> UploadZipReport:
    raise NotImplementedError("M1")
