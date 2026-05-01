"""File upload / download / soft-delete service. ADR-0005.

Routers (S3-C) wire FastAPI streams into `upload_file` and translate
the `(File, replayed)` tuple into `FileUploadResponse`. The session is
caller-managed: routers wrap each request in `async with session.begin()`
so the dedup INSERT happens inside an outer transaction (we use
`begin_nested()` for the SAVEPOINT-and-rollback dance on IntegrityError).
"""

from __future__ import annotations

from collections.abc import AsyncIterable

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from jobcopilot_api.errors import NotFoundError
from jobcopilot_api.infra.upload import (
    MAX_UPLOAD_BYTES,
    USER_QUOTA_BYTES,
    QuotaExceededError,
    compute_sha256,
    read_with_size_cap,
    verify_mime_and_magic,
)
from jobcopilot_api.models import File
from jobcopilot_api.schemas.files import FilePurpose


async def upload_file(
    *,
    session: AsyncSession,
    user_id: int,
    purpose: FilePurpose,
    filename: str,
    mime_type: str,
    reader: AsyncIterable[bytes],
) -> tuple[File, bool]:
    """Upload one file, with sha256 dedup. Returns `(file, replayed)`.

    Order of checks (each raises a `JobCopilotError` subclass that
    `install_exception_handlers` translates to RFC 7807):

    1. `read_with_size_cap` → 413 if > `MAX_UPLOAD_BYTES` (D2).
    2. `verify_mime_and_magic` → 415 (D3).
    3. quota — `_user_quota_used + size > USER_QUOTA_BYTES` → 413 (D8).
    4. INSERT inside a SAVEPOINT; on IntegrityError, SELECT the existing
       active row by (user_id, sha256) and return it with replayed=True
       (D5).
    """
    content = await read_with_size_cap(reader, MAX_UPLOAD_BYTES)
    verify_mime_and_magic(mime_type, content)
    sha = compute_sha256(content)
    size = len(content)

    used = await _user_quota_used(session, user_id=user_id)
    if used + size > USER_QUOTA_BYTES:
        raise QuotaExceededError(f"quota {USER_QUOTA_BYTES} bytes; used={used}, new={size}")

    row = File(
        user_id=user_id,
        name=filename,
        mime_type=mime_type,
        size_bytes=size,
        sha256=sha,
        content=content,
        purpose=purpose.value,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        existing = await _find_active_file(session, user_id=user_id, sha256=sha)
        if existing is None:
            # Rare: dedup target was soft-deleted between flush() and SELECT.
            # Bubble up; caller can retry the whole operation.
            raise
        return existing, True
    return row, False


async def get_file_for_download(
    *,
    session: AsyncSession,
    user_id: int,
    file_id: int,
) -> File:
    """Return the file with `content` undeferred, or raise NotFoundError.

    Soft-deleted files / files belonging to another user both 404 — we
    deliberately don't distinguish so callers can't probe existence
    (ADR-0005 D9 / D10).
    """
    cur = await session.execute(
        sa.select(File)
        .where(
            File.id == file_id,
            File.user_id == user_id,
            File.deleted_at.is_(None),
        )
        .options(undefer(File.content))
    )
    row = cur.scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"file {file_id} not found")
    return row


async def soft_delete_file(
    *,
    session: AsyncSession,
    user_id: int,
    file_id: int,
) -> None:
    """ADR-0005 D9. No-op on success; raises NotFoundError if no
    matching live row (already deleted, wrong user, or absent).

    `RETURNING id` is the row-existence check: an empty result set
    means we matched nothing. Avoids the typing wrinkle around
    `Result.rowcount` and gives back a single-statement implementation.
    """
    cur = await session.execute(
        sa.update(File)
        .where(
            File.id == file_id,
            File.user_id == user_id,
            File.deleted_at.is_(None),
        )
        .values(deleted_at=sa.func.now())
        .returning(File.id)
    )
    if cur.scalar_one_or_none() is None:
        raise NotFoundError(f"file {file_id} not found")


async def _user_quota_used(session: AsyncSession, *, user_id: int) -> int:
    cur = await session.execute(
        sa.select(sa.func.coalesce(sa.func.sum(File.size_bytes), 0)).where(
            File.user_id == user_id,
            File.deleted_at.is_(None),
        )
    )
    return int(cur.scalar_one())


async def _find_active_file(
    session: AsyncSession,
    *,
    user_id: int,
    sha256: str,
) -> File | None:
    cur = await session.execute(
        sa.select(File).where(
            File.user_id == user_id,
            File.sha256 == sha256,
            File.deleted_at.is_(None),
        )
    )
    return cur.scalar_one_or_none()
