"""PDF text extraction. ADR-0006 D10.

Pure function: bytes in, str out. Uses `pypdfium2` (STATUS Q1 / ADR-0005
D6); the service layer is responsible for fetching `File.content`
(undeferred) and feeding the bytes here.

Failures (PDFium load error, zero pages) raise `PdfExtractionError`,
which inherits `JobCopilotError` with status 422 and code
`JD_PARSE_FAILED` so the wire response matches ADR-0006 D8 without
service-layer remapping.

A length floor for "extracted but too short to be a real JD" lives in
the service layer (50 chars per ADR-0006 D10), not here — `pdf.py` is
domain-agnostic and just reports what the document actually contains.
"""

from __future__ import annotations

import pypdfium2 as pdfium  # type: ignore[import-untyped]

from jobcopilot_api.errors import JobCopilotError


class PdfExtractionError(JobCopilotError):
    """422 — `JD_PARSE_FAILED`. Used for PDFium load failures and 0-page
    PDFs. Caller may attach more context (e.g., file id) to the detail.
    """

    status_code = 422
    code = "JD_PARSE_FAILED"
    title = "PDF 文本抽取失败"


def extract_pdf_text(content: bytes) -> str:
    """Return the text content of `content` (a PDF byte buffer).

    Pages are joined with `\\f` (form feed). Whitespace at document
    edges is stripped; per-page whitespace is preserved so downstream
    LLM prompts see structure.

    Raises `PdfExtractionError` on PDFium load failure or empty
    document. An image-only / encrypted PDF will load but return an
    empty (or near-empty) string — that's the service layer's call to
    surface as `JD_PARSE_FAILED` based on its length policy.
    """
    try:
        pdf = pdfium.PdfDocument(content)
    except pdfium.PdfiumError as exc:
        raise PdfExtractionError(f"PDF 加载失败:{exc}") from exc

    try:
        n_pages = len(pdf)
        if n_pages == 0:
            raise PdfExtractionError("PDF 文档为空(0 页)")

        chunks: list[str] = []
        for i in range(n_pages):
            page = pdf[i]
            try:
                tp = page.get_textpage()
                try:
                    chunks.append(tp.get_text_range())
                finally:
                    tp.close()
            finally:
                page.close()
        return "\f".join(chunks).strip()
    finally:
        pdf.close()
