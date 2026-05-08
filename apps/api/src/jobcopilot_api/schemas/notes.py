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


class UploadZipReport(BaseModel):
    imported: int
    skipped: int
    skipped_reasons: list[dict[str, str]] = Field(default_factory=list)
    note_ids: list[int] = Field(default_factory=list)


TreeNode.model_rebuild()
