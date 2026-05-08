"""笔记 REST IO schema(M1,4-API_SPEC §3)。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class NoteCreateIn(BaseModel):
    folder_path: list[str]
    title: str
    content_md: str


class NoteUpdateIn(BaseModel):
    title: str | None = None
    content_md: str | None = None
    folder_path: list[str] | None = None


class NoteMoveIn(BaseModel):
    new_folder_path: list[str]


class NoteOut(BaseModel):
    id: int
    folder_path: list[str]
    title: str
    content_md: str
    created_at: datetime
    updated_at: datetime


class TreeNode(BaseModel):
    """notes 树形导航节点(GET /api/notes/tree 返回的 ne)。"""

    folder_path: list[str]
    notes: list[NoteOut] = Field(default_factory=list)
    children: list["TreeNode"] = Field(default_factory=list)


class NoteBatchImportItem(BaseModel):
    """批量入库的单条笔记(folder_path 是相对 root_folder 的子路径)。"""

    folder_path: list[str]
    title: str
    content_md: str


class NoteBatchImportIn(BaseModel):
    """批量入库请求体 — 前端走 File System Access API 读完本地 .md 后整批 POST。

    items 上限 100,前端遇到大目录自行分批多次 POST。
    """

    items: list[NoteBatchImportItem] = Field(min_length=1, max_length=100)
    root_folder: str | None = None
    overwrite: bool = False


class BatchImportReport(BaseModel):
    imported: int
    skipped: int
    skipped_reasons: list[dict[str, str]] = Field(default_factory=list)
    note_ids: list[int] = Field(default_factory=list)


TreeNode.model_rebuild()
