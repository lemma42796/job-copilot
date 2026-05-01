"""Unit tests for files Pydantic schemas. ADR-0005 D4 / D5."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jobcopilot_api.schemas.files import FilePurpose, FileUploadResponse


def test_file_purpose_values_match_adr_0005() -> None:
    """API_SPEC §6.9 / ADR-0005 D4 list these six values."""
    assert {p.value for p in FilePurpose} == {
        "jd_pdf",
        "jd_image",
        "profile_pdf",
        "profile_docx",
        "resume_pdf",
        "other",
    }


def test_file_purpose_is_strenum() -> None:
    # StrEnum lets routes accept the raw string from multipart form.
    assert FilePurpose.JD_PDF.value == "jd_pdf"
    assert isinstance(FilePurpose.JD_PDF, str)


def test_upload_response_round_trip() -> None:
    payload = {
        "id": 123,
        "filename": "jd_xyz.pdf",
        "mime": "application/pdf",
        "size_bytes": 102400,
        "sha256": "a" * 64,
        "url": "/v1/files/123",
        "replayed": False,
    }
    obj = FileUploadResponse.model_validate(payload)
    assert obj.model_dump() == payload


def test_upload_response_rejects_short_sha256() -> None:
    with pytest.raises(ValidationError):
        FileUploadResponse(
            id=1,
            filename="x.pdf",
            mime="application/pdf",
            size_bytes=1,
            sha256="abc",
            url="/v1/files/1",
            replayed=False,
        )


def test_upload_response_rejects_long_sha256() -> None:
    with pytest.raises(ValidationError):
        FileUploadResponse(
            id=1,
            filename="x.pdf",
            mime="application/pdf",
            size_bytes=1,
            sha256="a" * 65,
            url="/v1/files/1",
            replayed=False,
        )
