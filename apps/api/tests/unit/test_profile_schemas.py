"""Unit tests for Profile Pydantic schemas."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from jobcopilot_api.schemas.profiles import (
    ProfileChunkItem,
    ProfileChunksResponse,
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


# ---- Partial-date normalization (PartialDate) ----


def test_partial_date_year_only_pads_to_jan_1() -> None:
    obj = ProfileStructured.model_validate(
        {"educations": [{"school": "S", "start_date": "2016", "end_date": "2020"}]}
    )
    assert obj.educations[0].start_date == date(2016, 1, 1)
    assert obj.educations[0].end_date == date(2020, 1, 1)


def test_partial_date_year_month_pads_to_first() -> None:
    obj = ProfileStructured.model_validate(
        {
            "experiences": [
                {"company": "C", "title": "T", "start_date": "2020-01", "end_date": "2023-7"}
            ]
        }
    )
    assert obj.experiences[0].start_date == date(2020, 1, 1)
    assert obj.experiences[0].end_date == date(2023, 7, 1)


def test_partial_date_full_iso_passes_through() -> None:
    obj = ProfileStructured.model_validate(
        {"experiences": [{"company": "C", "title": "T", "start_date": "2020-03-15"}]}
    )
    assert obj.experiences[0].start_date == date(2020, 3, 15)


def test_partial_date_empty_string_becomes_none() -> None:
    obj = ProfileStructured.model_validate(
        {"experiences": [{"company": "C", "title": "T", "start_date": "", "end_date": "  "}]}
    )
    assert obj.experiences[0].start_date is None
    assert obj.experiences[0].end_date is None


def test_partial_date_invalid_string_still_rejected() -> None:
    with pytest.raises(ValidationError):
        ProfileStructured.model_validate(
            {"experiences": [{"company": "C", "title": "T", "start_date": "yesterday"}]}
        )


# ---- ProfilePatchInput: extra-forbid ----


def test_patch_input_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ProfilePatchInput.model_validate({"unknown_field": 1})


def test_patch_input_accepts_partial_status_only() -> None:
    obj = ProfilePatchInput(status="parsed")
    assert obj.status == "parsed"
    assert obj.structured is None


# ---------- ProfileChunk wire schemas ----------


def _chunk_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": 100,
        "granularity": "experience",
        "source_table": "profile_experiences",
        "source_id": 10,
        "content": "公司:ACME\n职位:SWE",
        "embed_model": "text-embedding-v4",
        "embed_version": "v1",
        "metadata": {"chunker_version": "v1"},
        "created_at": datetime(2026, 5, 3, tzinfo=UTC),
        "updated_at": datetime(2026, 5, 3, tzinfo=UTC),
    }
    base.update(overrides)
    return base


def test_chunk_item_accepts_all_five_granularities() -> None:
    for g in ("experience", "project", "skill", "summary", "education"):
        ProfileChunkItem.model_validate(_chunk_payload(granularity=g))


def test_chunk_item_rejects_unknown_granularity() -> None:
    with pytest.raises(ValidationError):
        ProfileChunkItem.model_validate(_chunk_payload(granularity="totally_bogus"))


def test_chunk_item_allows_null_embed_model_and_version() -> None:
    obj = ProfileChunkItem.model_validate(_chunk_payload(embed_model=None, embed_version=None))
    assert obj.embed_model is None
    assert obj.embed_version is None


def test_chunk_item_does_not_expose_embedding_field() -> None:
    """The 1024-dim float[] is intentionally absent from the wire shape."""
    assert "embedding" not in ProfileChunkItem.model_fields


def test_chunks_response_wraps_list_and_total() -> None:
    items = [ProfileChunkItem.model_validate(_chunk_payload(id=i)) for i in (1, 2, 3)]
    resp = ProfileChunksResponse(data=items, total=3)
    assert resp.total == 3
    assert [c.id for c in resp.data] == [1, 2, 3]
