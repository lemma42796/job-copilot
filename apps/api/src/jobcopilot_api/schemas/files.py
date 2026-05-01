"""File upload / download wire contract. ADR-0005 D4 / API_SPEC §6.9."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class FilePurpose(StrEnum):
    """Allowed values for the `purpose` form field on POST /v1/files/upload.

    The DB column stays VARCHAR(50) so we can extend without ALTER TYPE
    (ADR-0005 D4); strict validation lives at the API boundary.
    """

    JD_PDF = "jd_pdf"
    JD_IMAGE = "jd_image"
    PROFILE_PDF = "profile_pdf"
    PROFILE_DOCX = "profile_docx"
    RESUME_PDF = "resume_pdf"
    OTHER = "other"


class FileUploadResponse(BaseModel):
    """Response body for POST /v1/files/upload (and the dedup-replay path).

    `replayed=True` means the request body matched an existing
    (user_id, sha256) row and we returned the existing file id instead
    of inserting a new one (ADR-0005 D5).
    """

    id: int
    filename: str
    mime: str
    size_bytes: int
    sha256: str = Field(min_length=64, max_length=64)
    url: str
    replayed: bool
