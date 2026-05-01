"""Files router. ADR-0005 / API_SPEC §6.9."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Header, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from jobcopilot_api.errors import ValidationError
from jobcopilot_api.infra.db import get_session
from jobcopilot_api.routers._deps import current_user_id
from jobcopilot_api.schemas.files import FilePurpose, FileUploadResponse
from jobcopilot_api.services.file_service import (
    get_file_for_download,
    soft_delete_file,
    upload_file,
)

router = APIRouter(tags=["files"])

_UPLOAD_CHUNK_SIZE = 64 * 1024


async def _stream(uf: UploadFile, chunk_size: int = _UPLOAD_CHUNK_SIZE) -> AsyncIterator[bytes]:
    """Wrap starlette's `UploadFile` (SpooledTemporaryFile-backed) into an
    async-iterator that the upload pipeline can drain in fixed-size chunks.
    """
    while True:
        chunk = await uf.read(chunk_size)
        if not chunk:
            break
        yield chunk


@router.post(
    "/files/upload",
    response_model=FileUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a file (multipart/form-data)",
)
async def upload(
    response: Response,
    file: UploadFile,
    purpose: Annotated[FilePurpose, Form()],
    user_id: Annotated[int, Depends(current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FileUploadResponse:
    if not file.filename:
        raise ValidationError("file.filename missing")
    if not file.content_type:
        raise ValidationError("file content type missing")

    async with session.begin():
        row, replayed = await upload_file(
            session=session,
            user_id=user_id,
            purpose=purpose,
            filename=file.filename,
            mime_type=file.content_type,
            reader=_stream(file),
        )

    # ADR-0005 D5: dedup hit returns 200 instead of 201 so clients can
    # distinguish "I just stored this" from "I returned what was already there".
    if replayed:
        response.status_code = status.HTTP_200_OK

    return FileUploadResponse(
        id=row.id,
        filename=row.name,
        mime=row.mime_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        url=f"/v1/files/{row.id}",
        replayed=replayed,
    )


@router.get(
    "/files/{file_id}",
    summary="Download a file",
    response_class=Response,
    responses={
        200: {"content": {"application/octet-stream": {}}},
        304: {"description": "Not modified — content matches If-None-Match"},
        404: {"description": "File not found, soft-deleted, or owned by another user"},
    },
)
async def download(
    file_id: int,
    user_id: Annotated[int, Depends(current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> Response:
    row = await get_file_for_download(session=session, user_id=user_id, file_id=file_id)
    etag = f'"{row.sha256}"'

    # ADR-0005 D10: ETag is the sha256 (content is immutable). Clients that
    # cached a prior body under the same ETag get a 304 with no payload.
    if if_none_match == etag:
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED,
            headers={"ETag": etag, "Cache-Control": "private, max-age=86400"},
        )

    # RFC 6266: percent-encoded UTF-8 disposition for non-ASCII filenames.
    disposition = f"attachment; filename*=UTF-8''{quote(row.name)}"

    return Response(
        content=row.content,
        media_type=row.mime_type,
        headers={
            "Content-Disposition": disposition,
            "ETag": etag,
            "Cache-Control": "private, max-age=86400",
        },
    )


@router.delete(
    "/files/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a file",
    response_class=Response,
    responses={
        204: {"description": "Soft-deleted"},
        404: {"description": "File not found, already deleted, or owned by another user"},
    },
)
async def delete(
    file_id: int,
    user_id: Annotated[int, Depends(current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    async with session.begin():
        await soft_delete_file(session=session, user_id=user_id, file_id=file_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
