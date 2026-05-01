"""Upload primitives: size cap + mime/magic check + sha256.

These are deliberately framework-agnostic: a router (S3-C) wires
FastAPI's `UploadFile` stream into `read_with_size_cap`, but unit
tests can pass any `AsyncIterable[bytes]` (e.g. an async wrapper
around `BytesIO`).

Constants and error classes live alongside the helpers so service
code (`services/file_service.py`) imports from one place.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterable

from jobcopilot_api.errors import JobCopilotError

# ADR-0005 D2: 20MB at the API boundary; the DB CHECK at 100MB is the safety net.
MAX_UPLOAD_BYTES: int = 20 * 1024 * 1024

# ADR-0005 D8: per-user soft cap; dedicated rate limit lands later (M1 末).
USER_QUOTA_BYTES: int = 200 * 1024 * 1024

# ADR-0005 D3.
ALLOWED_MIME: frozenset[str] = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "text/plain",
        "text/markdown",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)

# Mimes absent from this map skip the magic check (text/* are too unstable
# to sniff reliably). Mimes present here MUST start with one of the listed
# prefixes or the upload is rejected as 415.
_MAGIC_PREFIXES: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    # All JFIF / EXIF / raw-DCT JPEGs share this 3-byte SOI + APP marker.
    "image/jpeg": (b"\xff\xd8\xff",),
    # .docx is a ZIP container.
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (b"PK\x03\x04",),
}


class FileTooLargeError(JobCopilotError):
    """Upload exceeded `MAX_UPLOAD_BYTES`."""

    status_code = 413
    code = "PAYLOAD_TOO_LARGE"
    title = "文件过大"


class UnsupportedMediaError(JobCopilotError):
    """MIME not in whitelist OR magic bytes do not match declared MIME."""

    status_code = 415
    code = "UNSUPPORTED_MEDIA"
    title = "不支持的文件格式"


class QuotaExceededError(JobCopilotError):
    """Adding this file would push the user past `USER_QUOTA_BYTES`."""

    status_code = 413
    code = "QUOTA_EXCEEDED"
    title = "用户存储配额已满"


async def read_with_size_cap(reader: AsyncIterable[bytes], max_bytes: int) -> bytes:
    """Drain `reader` into bytes, raising as soon as total > `max_bytes`.

    Streaming check is what keeps a malicious 1GB upload from blowing
    out memory: we never accumulate beyond `max_bytes + last_chunk`.
    """
    buf = bytearray()
    async for chunk in reader:
        buf.extend(chunk)
        if len(buf) > max_bytes:
            raise FileTooLargeError(f"upload exceeds {max_bytes} bytes (got at least {len(buf)})")
    return bytes(buf)


def verify_mime_and_magic(mime: str, content: bytes) -> None:
    """Two-stage check (ADR-0005 D3): mime in whitelist, then magic prefix.

    Raises `UnsupportedMediaError` (415) on any mismatch. text/* mimes
    skip the magic stage by design — there is no reliable signature.
    """
    if mime not in ALLOWED_MIME:
        raise UnsupportedMediaError(f"mime {mime!r} not allowed")
    expected = _MAGIC_PREFIXES.get(mime)
    if expected is None:
        return
    if not any(content.startswith(p) for p in expected):
        raise UnsupportedMediaError(f"content does not match {mime!r} signature")


def compute_sha256(content: bytes) -> str:
    """64-char lowercase hex digest, matching `files.sha256 CHAR(64)`."""
    return hashlib.sha256(content).hexdigest()
