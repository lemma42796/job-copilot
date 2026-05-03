"""Unit tests for Profile Pydantic schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jobcopilot_api.schemas.profiles import (
    ProfileParseInput,
    ProfilePatchInput,
    ProfileSource,
    ProfileStatus,
    ProfileStructured,
)


def test_profile_source_enum_values_match_migration_0009() -> None:
    assert {s.value for s in ProfileSource} == {"pdf_upload", "text_paste", "manual"}


def test_profile_status_enum_values_match_migration_0009() -> None:
    assert {s.value for s in ProfileStatus} == {"parsing", "parsed", "parse_failed"}


# ---- ProfileParseInput: text + file_id mutex ----


def test_parse_input_accepts_text_paste() -> None:
    obj = ProfileParseInput(text="some long resume text", source="text_paste")
    assert obj.text == "some long resume text"
    assert obj.file_id is None


def test_parse_input_accepts_pdf_upload() -> None:
    obj = ProfileParseInput(file_id=42, source="pdf_upload")
    assert obj.file_id == 42


def test_parse_input_rejects_both_text_and_file_id() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        ProfileParseInput(text="x", file_id=1, source="text_paste")


def test_parse_input_rejects_neither_text_nor_file_id() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        ProfileParseInput(source="text_paste")


def test_parse_input_text_paste_requires_text() -> None:
    with pytest.raises(ValidationError, match="text_paste requires"):
        ProfileParseInput(file_id=1, source="text_paste")


def test_parse_input_pdf_upload_requires_file_id() -> None:
    with pytest.raises(ValidationError, match="pdf_upload requires"):
        ProfileParseInput(text="x" * 100, source="pdf_upload")


# ---- ProfileStructured: nested defaults + missing fields ----


def test_profile_structured_minimal_object_uses_defaults() -> None:
    obj = ProfileStructured()
    assert obj.full_name is None
    assert obj.experiences == []
    assert obj.skills == []


def test_profile_structured_skill_category_defaults_to_other() -> None:
    obj = ProfileStructured.model_validate({"skills": [{"name": "x", "name_raw": "X"}]})
    assert obj.skills[0].category == "other"


# ---- ProfilePatchInput: extra-forbid ----


def test_patch_input_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ProfilePatchInput.model_validate({"unknown_field": 1})


def test_patch_input_accepts_partial_status_only() -> None:
    obj = ProfilePatchInput(status="parsed")
    assert obj.status == "parsed"
    assert obj.structured is None
