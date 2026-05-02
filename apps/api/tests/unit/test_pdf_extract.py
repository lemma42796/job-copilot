"""Unit tests for `infra.pdf.extract_pdf_text`. ADR-0006 D10.

Tests build a minimal valid PDF inline so we don't need a fixture file
or an extra writer dependency. The PDF contains exactly the text we
embed, so we can assert round-trip extraction.
"""

from __future__ import annotations

import pytest

from jobcopilot_api.infra.pdf import PdfExtractionError, extract_pdf_text


def _make_pdf(text: str) -> bytes:
    """Build a single-page PDF whose page contents print `text`.

    Offsets in the xref table are computed from the byte stream so the
    output is always a valid PDFium-loadable document.
    """
    content_stream = f"BT\n/F1 12 Tf\n50 750 Td\n({text}) Tj\nET\n".encode("latin-1")
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(content_stream)).encode()
        + b" >>\nstream\n"
        + content_stream
        + b"endstream",
    ]

    body = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    offsets: list[int] = []
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{idx} 0 obj\n".encode() + obj + b"\nendobj\n"

    xref_offset = len(body)
    xref = b"xref\n0 6\n0000000000 65535 f \n"
    for off in offsets:
        xref += f"{off:010d} 00000 n \n".encode()
    xref += b"trailer\n<< /Size 6 /Root 1 0 R >>\n"
    xref += f"startxref\n{xref_offset}\n%%EOF".encode()
    return body + xref


# ---- happy path ----


def test_extract_returns_embedded_text() -> None:
    pdf = _make_pdf("Senior Backend Engineer at ACME with Python skills")
    text = extract_pdf_text(pdf)
    assert "Senior Backend Engineer" in text
    assert "ACME" in text
    assert "Python" in text


def test_extract_strips_outer_whitespace() -> None:
    pdf = _make_pdf("XYZ company is hiring  ")
    assert extract_pdf_text(pdf) == extract_pdf_text(pdf).strip()


# ---- error paths ----


def test_extract_raises_on_garbage_bytes() -> None:
    with pytest.raises(PdfExtractionError, match="PDF 加载失败"):
        extract_pdf_text(b"this is definitely not a pdf")


def test_extract_raises_on_empty_bytes() -> None:
    with pytest.raises(PdfExtractionError):
        extract_pdf_text(b"")


def test_extract_raises_on_truncated_header() -> None:
    # "%PDF-1.4" alone — header looks right but body is missing.
    with pytest.raises(PdfExtractionError):
        extract_pdf_text(b"%PDF-1.4\n")


# ---- error class wire contract ----


def test_pdf_extraction_error_maps_to_jd_parse_failed() -> None:
    err = PdfExtractionError("bad")
    assert err.status_code == 422
    assert err.code == "JD_PARSE_FAILED"
