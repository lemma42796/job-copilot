"""Unit tests for JD Pydantic schemas. ADR-0006 D1 / D5."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jobcopilot_api.schemas.jds import (
    JDParseInput,
    JDPatchInput,
    JdSource,
    JdStatus,
    JDStructured,
)

# ---- Enum values match migration 0003 ----


def test_jd_source_enum_values() -> None:
    assert {s.value for s in JdSource} == {
        "text_paste",
        "pdf_upload",
        "image_upload",
        "extension_paste",
    }


def test_jd_status_enum_values() -> None:
    assert {s.value for s in JdStatus} == {"parsing", "parsed", "parse_failed"}


# ---- JDParseInput: text + file_id mutex (ADR-0006 D1) ----


def test_parse_input_accepts_text_paste() -> None:
    obj = JDParseInput(text="Senior Backend @ XYZ", source="text_paste")
    assert obj.text == "Senior Backend @ XYZ"
    assert obj.file_id is None


def test_parse_input_accepts_pdf_upload() -> None:
    obj = JDParseInput(file_id=42, source="pdf_upload")
    assert obj.file_id == 42
    assert obj.text is None


def test_parse_input_rejects_both_text_and_file_id() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        JDParseInput(text="x", file_id=1, source="text_paste")


def test_parse_input_rejects_neither_text_nor_file_id() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        JDParseInput(source="text_paste")


def test_parse_input_rejects_empty_text_with_text_paste() -> None:
    # Whitespace-only text is treated as missing.
    with pytest.raises(ValidationError, match="exactly one"):
        JDParseInput(text="   \n", source="text_paste")


def test_parse_input_rejects_text_paste_with_only_file_id() -> None:
    with pytest.raises(ValidationError, match="text_paste requires"):
        JDParseInput(file_id=1, source="text_paste")


def test_parse_input_rejects_pdf_upload_with_only_text() -> None:
    with pytest.raises(ValidationError, match="pdf_upload requires"):
        JDParseInput(text="some jd", source="pdf_upload")


def test_parse_input_rejects_unsupported_source() -> None:
    # image_upload / extension_paste are reserved (ADR-0006 D1).
    with pytest.raises(ValidationError):
        JDParseInput(file_id=1, source="image_upload")


# ---- JDStructured: AGENT_DESIGN §3.3 shape ----


def test_jd_structured_minimal() -> None:
    obj = JDStructured(title="Backend Engineer", confidence=0.9)
    assert obj.salary_period == "monthly"  # default
    assert obj.hard_skills == []
    assert obj.description == ""


def test_jd_structured_rejects_missing_title() -> None:
    with pytest.raises(ValidationError):
        JDStructured(confidence=0.9)  # type: ignore[call-arg]


def test_jd_structured_rejects_invalid_education() -> None:
    with pytest.raises(ValidationError):
        JDStructured(title="x", confidence=0.9, education="高中")


def test_jd_structured_confidence_bounds() -> None:
    with pytest.raises(ValidationError):
        JDStructured(title="x", confidence=1.5)
    with pytest.raises(ValidationError):
        JDStructured(title="x", confidence=-0.1)


# ---- JDPatchInput: only structured + status (ADR-0006 D2) ----


def test_patch_input_accepts_structured_only() -> None:
    s = JDStructured(title="x", confidence=0.8)
    obj = JDPatchInput(structured=s)
    assert obj.status is None


def test_patch_input_accepts_status_parsed() -> None:
    obj = JDPatchInput(status="parsed")
    assert obj.structured is None


def test_patch_input_rejects_archived() -> None:
    # archived ENUM extension waits for M4 (ADR-0006 D3).
    with pytest.raises(ValidationError):
        JDPatchInput(status="archived")


def test_patch_input_rejects_parse_failed() -> None:
    # parse_failed is set by service on errors, not by client PATCH.
    with pytest.raises(ValidationError):
        JDPatchInput(status="parse_failed")


def test_patch_input_rejects_unknown_field() -> None:
    # raw_text is not patchable (ADR-0006 D7).
    with pytest.raises(ValidationError):
        JDPatchInput.model_validate({"raw_text": "..."}, strict=False)  # extra field via dict path
