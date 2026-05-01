"""Unit tests for `infra/upload.py`. ADR-0005 D2 / D3."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from jobcopilot_api.infra.upload import (
    ALLOWED_MIME,
    MAX_UPLOAD_BYTES,
    FileTooLargeError,
    UnsupportedMediaError,
    compute_sha256,
    read_with_size_cap,
    verify_mime_and_magic,
)


async def _chunks(data: bytes, chunk_size: int = 1024) -> AsyncIterator[bytes]:
    """Make `data` look like a streaming upload."""
    for i in range(0, len(data), chunk_size):
        yield data[i : i + chunk_size]


# ---------- read_with_size_cap ----------


async def test_read_with_size_cap_under_limit_returns_full_content() -> None:
    data = b"abc" * 100
    out = await read_with_size_cap(_chunks(data), max_bytes=4096)
    assert out == data


async def test_read_with_size_cap_exact_limit_ok() -> None:
    data = b"x" * 100
    out = await read_with_size_cap(_chunks(data), max_bytes=100)
    assert out == data


async def test_read_with_size_cap_over_limit_raises_413() -> None:
    data = b"x" * 1024
    with pytest.raises(FileTooLargeError) as ei:
        await read_with_size_cap(_chunks(data, chunk_size=256), max_bytes=512)
    assert ei.value.status_code == 413
    assert ei.value.code == "PAYLOAD_TOO_LARGE"


async def test_read_with_size_cap_empty_input_returns_empty_bytes() -> None:
    out = await read_with_size_cap(_chunks(b""), max_bytes=10)
    assert out == b""


def test_max_upload_bytes_matches_adr_0005() -> None:
    """20 MiB per D2."""
    assert MAX_UPLOAD_BYTES == 20 * 1024 * 1024


# ---------- verify_mime_and_magic ----------


def test_pdf_magic_ok() -> None:
    verify_mime_and_magic("application/pdf", b"%PDF-1.7\nrest")


def test_png_magic_ok() -> None:
    verify_mime_and_magic("image/png", b"\x89PNG\r\n\x1a\nIHDR...")


def test_jpeg_magic_ok_jfif() -> None:
    verify_mime_and_magic("image/jpeg", b"\xff\xd8\xff\xe0\x00\x10JFIF")


def test_jpeg_magic_ok_exif() -> None:
    # Different APP marker; the 3-byte SOI prefix is what we check.
    verify_mime_and_magic("image/jpeg", b"\xff\xd8\xff\xe1\x00\x10EXIF")


def test_docx_magic_ok() -> None:
    verify_mime_and_magic(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        b"PK\x03\x04rest of zip",
    )


def test_text_plain_skips_magic() -> None:
    # Even completely non-textual bytes go through — text/* has no
    # reliable signature, so we trust the declared mime here.
    verify_mime_and_magic("text/plain", b"\x00\x01\x02hello")


def test_text_markdown_skips_magic() -> None:
    verify_mime_and_magic("text/markdown", b"# hi\n")


def test_unknown_mime_rejected_415() -> None:
    with pytest.raises(UnsupportedMediaError) as ei:
        verify_mime_and_magic("application/x-msdownload", b"MZ\x90\x00")
    assert ei.value.status_code == 415
    assert ei.value.code == "UNSUPPORTED_MEDIA"


def test_pdf_mime_with_png_magic_rejected() -> None:
    with pytest.raises(UnsupportedMediaError):
        verify_mime_and_magic("application/pdf", b"\x89PNG\r\n\x1a\n...")


def test_png_mime_with_pdf_bytes_rejected() -> None:
    with pytest.raises(UnsupportedMediaError):
        verify_mime_and_magic("image/png", b"%PDF-1.7")


def test_allowed_mime_set_matches_adr_0005() -> None:
    assert (
        frozenset(
            {
                "application/pdf",
                "image/png",
                "image/jpeg",
                "text/plain",
                "text/markdown",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            }
        )
        == ALLOWED_MIME
    )


# ---------- compute_sha256 ----------


def test_compute_sha256_known_input() -> None:
    # Reference value from `printf 'hello' | sha256sum`.
    assert (
        compute_sha256(b"hello")
        == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )


def test_compute_sha256_empty() -> None:
    # SHA-256 of the empty string.
    assert compute_sha256(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_compute_sha256_is_64_hex_chars() -> None:
    digest = compute_sha256(b"any payload")
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)
