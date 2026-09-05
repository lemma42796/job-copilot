"""Notes REST 端点(M1,OpenAPI / Pydantic schemas)。

router 自身 prefix `/notes`;main.py include 时挂 `/api` 前缀,实际
端点路径 = `/api/notes/*`。所有端点 application/json。

错误统一抛 JobCopilotError 子类(notes_service 顶部定义),全局
exception handler 转 RFC 7807 problem+json(docs/TECH_DESIGN.md)。

session.commit() 写在 router 层 — service 只 flush(进 SQL 确保唯一约束
能即时校验出 IntegrityError),commit 由路由收尾。infra/db.get_session
不主动 commit,出 async with 时仅 close(异常路径才 rollback)。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from jobcopilot_api.infra.auth import CurrentUserId
from jobcopilot_api.infra.db import get_session
from jobcopilot_api.schemas.notes import (
    BatchImportReport,
    NoteBatchImportIn,
    NoteCreateIn,
    NoteMoveIn,
    NoteOut,
    NoteUpdateIn,
    TreeNode,
)
from jobcopilot_api.services import notes_service

router = APIRouter(tags=["notes"], prefix="/notes")

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post(
    "",
    response_model=NoteOut,
    status_code=201,
    summary="创建笔记(web 编辑器)",
)
async def create_note(
    payload: NoteCreateIn, session: SessionDep, user_id: CurrentUserId
) -> NoteOut:
    note = await notes_service.create_note(session, payload, user_id=user_id)
    await session.commit()
    return note


@router.get("/tree", response_model=list[TreeNode], summary="树形导航")
async def get_tree(session: SessionDep, user_id: CurrentUserId) -> list[TreeNode]:
    return await notes_service.get_tree(session, user_id=user_id)


@router.get("/{note_id}", response_model=NoteOut, summary="笔记详情")
async def get_note(
    note_id: int, session: SessionDep, user_id: CurrentUserId
) -> NoteOut:
    return await notes_service.get_note(session, note_id, user_id=user_id)


@router.put("/{note_id}", response_model=NoteOut, summary="更新笔记")
async def update_note(
    note_id: int,
    payload: NoteUpdateIn,
    session: SessionDep,
    user_id: CurrentUserId,
) -> NoteOut:
    note = await notes_service.update_note(
        session, note_id, payload, user_id=user_id
    )
    await session.commit()
    return note


@router.delete("/{note_id}", status_code=204, summary="软删笔记")
async def delete_note(
    note_id: int, session: SessionDep, user_id: CurrentUserId
) -> None:
    await notes_service.delete_note(session, note_id, user_id=user_id)
    await session.commit()


@router.post(
    "/{note_id}/move",
    response_model=NoteOut,
    summary="移动到新 folder_path",
)
async def move_note(
    note_id: int,
    payload: NoteMoveIn,
    session: SessionDep,
    user_id: CurrentUserId,
) -> NoteOut:
    note = await notes_service.move_note(
        session, note_id, payload.new_folder_path, user_id=user_id
    )
    await session.commit()
    return note


@router.post(
    "/batch-import",
    response_model=BatchImportReport,
    summary="批量入库 — 前端 File System Access API 读完本地 .md 后整批 POST",
)
async def batch_import(
    payload: NoteBatchImportIn, session: SessionDep, user_id: CurrentUserId
) -> BatchImportReport:
    report = await notes_service.batch_import(
        session,
        items=payload.items,
        root_folder=payload.root_folder,
        overwrite=payload.overwrite,
        user_id=user_id,
    )
    await session.commit()
    return report
