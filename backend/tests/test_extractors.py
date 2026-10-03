"""Tests for Phase 5 Document Extractors.

Covers TXT, PDF, and DOCX extraction per the Phase 5 specification.
"""

import struct
import pytest
from pathlib import Path
import pypdf
import docx
from docx.oxml.ns import qn

from app.content.extractors import extract_txt, extract_pdf, extract_docx


# ============================================================================
# TXT Tests
# ============================================================================

def test_extract_txt_utf8(tmp_path: Path):
    """Standard UTF-8 TXT extraction."""
    path = tmp_path / "hello.txt"
    content = "Hello, world! 🎉"
    path.write_text(content, encoding="utf-8")

    text, chars, pages, error = extract_txt(path)

    assert error is None
    assert text == content
    assert chars == len(content)
    assert pages is None


def test_extract_txt_empty(tmp_path: Path):
    """Empty TXT file returns empty string, zero chars."""
    path = tmp_path / "empty.txt"
    path.write_text("")

    text, chars, pages, error = extract_txt(path)

    assert error is None
    assert text == ""
    assert chars == 0


def test_extract_txt_whitespace_only(tmp_path: Path):
    """TXT file with only whitespace is treated as empty."""
    path = tmp_path / "ws.txt"
    path.write_text("   \n\t  \n")

    text, chars, pages, error = extract_txt(path)

    assert error is None
    assert text == ""
    assert chars == 0


def test_extract_txt_multiline(tmp_path: Path):
    """TXT extraction preserves multiline structure."""
    content = "Line one\nLine two\nLine three"
    path = tmp_path / "multi.txt"
    path.write_text(content, encoding="utf-8")

    text, chars, pages, error = extract_txt(path)

    assert error is None
    assert text == content
    assert chars == len(content)
    assert "Line two" in text


def test_extract_txt_latin1_fallback(tmp_path: Path):
    """File with non-UTF-8 bytes falls back to latin-1 without crashing."""
    path = tmp_path / "latin.txt"
    # Write raw bytes that are valid latin-1 but not valid UTF-8
    path.write_bytes(b"caf\xe9 au lait")

    text, chars, pages, error = extract_txt(path)

    # Must not crash — either decoded via latin-1 or returned with error
    assert error is None or "decode" in (error or "").lower()
    if error is None:
        assert text is not None
        assert "caf" in text


def test_extract_txt_missing_file(tmp_path: Path):
    """Missing file returns error, does not raise."""
    path = tmp_path / "nonexistent.txt"

    text, chars, pages, error = extract_txt(path)

    assert text is None
    assert error is not None


def test_extract_txt_deterministic(tmp_path: Path):
    """Multiple calls on the same file produce identical results."""
    content = "Deterministic content"
    path = tmp_path / "det.txt"
    path.write_text(content, encoding="utf-8")

    r1 = extract_txt(path)
    r2 = extract_txt(path)

    assert r1 == r2


def test_extract_txt_readonly(tmp_path: Path):
    """Extraction does not modify the source file."""
    content = "Do not modify me"
    path = tmp_path / "ro.txt"
    path.write_text(content, encoding="utf-8")
    mtime_before = path.stat().st_mtime
    size_before = path.stat().st_size

    extract_txt(path)

    assert path.stat().st_mtime == mtime_before
    assert path.stat().st_size == size_before
    assert path.read_text(encoding="utf-8") == content


# ============================================================================
# PDF Tests
# ============================================================================

def _make_pdf_with_text(path: Path, pages: list[str]) -> None:
    """Helper: create a PDF with text on each page using reportlab-free approach."""
    writer = pypdf.PdfWriter()
    for page_text in pages:
        # Add a blank page; pypdf writer can't easily add text,
        # so we use a raw PDF structure for small text PDFs.
        writer.add_blank_page(width=612, height=792)
    with open(path, "wb") as f:
        writer.write(f)


def _make_real_text_pdf(path: Path, text: str) -> None:
    """Create a minimal single-page PDF with actual extractable text using raw PDF syntax."""
    content_stream = f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode()
    content_length = len(content_stream)

    pdf_bytes = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]\n"
        b"   /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
        b"4 0 obj\n<< /Length " + str(content_length).encode() + b" >>\nstream\n"
        + content_stream + b"\nendstream\nendobj\n"
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
        b"xref\n0 6\n0000000000 65535 f \n"
        b"0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n"
        b"0000000266 00000 n \n0000000" + str(370 + content_length).encode() + b" 00000 n \n"
        b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
        + str(430 + content_length).encode() + b"\n%%EOF\n"
    )
    path.write_bytes(pdf_bytes)


def test_extract_pdf_blank_page(tmp_path: Path):
    """Blank-page PDF returns EMPTY result (empty string) with correct page count."""
    path = tmp_path / "blank.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with open(path, "wb") as f:
        writer.write(f)

    text, chars, pages, error = extract_pdf(path)

    assert error is None
    assert text == ""
    assert chars == 0
    assert pages == 1


def test_extract_pdf_multi_page_no_text(tmp_path: Path):
    """Multi-page blank PDF returns correct page count."""
    path = tmp_path / "multi_blank.pdf"
    writer = pypdf.PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=612, height=792)
    with open(path, "wb") as f:
        writer.write(f)

    text, chars, pages, error = extract_pdf(path)

    assert error is None
    assert pages == 3
    assert text == ""


def test_extract_pdf_malformed(tmp_path: Path):
    """Malformed (non-PDF) file returns FAILED with error info."""
    path = tmp_path / "malformed.pdf"
    path.write_text("This is definitely not a valid PDF file.")

    text, chars, pages, error = extract_pdf(path)

    assert text is None
    assert error is not None
    assert "PDF extraction failed" in error


def test_extract_pdf_malformed_truncated(tmp_path: Path):
    """Truncated PDF header returns FAILED with error info."""
    path = tmp_path / "truncated.pdf"
    path.write_bytes(b"%PDF-1.4\n1 0 obj\n")  # Incomplete PDF

    text, chars, pages, error = extract_pdf(path)

    assert text is None
    assert error is not None


def test_extract_pdf_encrypted(tmp_path: Path):
    """Encrypted PDF returns FAILED with encryption-specific error info."""
    path = tmp_path / "encrypted.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt("secret_password")
    with open(path, "wb") as f:
        writer.write(f)

    text, chars, pages, error = extract_pdf(path)

    assert text is None
    assert error is not None
    assert "encrypted" in error.lower() or "password" in error.lower()


def test_extract_pdf_deterministic(tmp_path: Path):
    """PDF extraction on the same file returns identical results."""
    path = tmp_path / "det.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with open(path, "wb") as f:
        writer.write(f)

    r1 = extract_pdf(path)
    r2 = extract_pdf(path)

    assert r1 == r2


def test_extract_pdf_readonly(tmp_path: Path):
    """PDF extraction does not modify the source file."""
    path = tmp_path / "ro.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with open(path, "wb") as f:
        writer.write(f)

    size_before = path.stat().st_size
    mtime_before = path.stat().st_mtime
    raw_before = path.read_bytes()

    extract_pdf(path)

    assert path.stat().st_size == size_before
    assert path.stat().st_mtime == mtime_before
    assert path.read_bytes() == raw_before


def test_extract_pdf_missing_file(tmp_path: Path):
    """Missing PDF returns FAILED with error info."""
    path = tmp_path / "nonexistent.pdf"

    text, chars, pages, error = extract_pdf(path)

    assert text is None
    assert error is not None


# ============================================================================
# DOCX Tests
# ============================================================================

def test_extract_docx_single_paragraph(tmp_path: Path):
    """Single paragraph DOCX extraction."""
    path = tmp_path / "single.docx"
    doc = docx.Document()
    doc.add_paragraph("Hello World")
    doc.save(path)

    text, chars, pages, error = extract_docx(path)

    assert error is None
    assert text == "Hello World"
    assert chars == len("Hello World")
    assert pages is None


def test_extract_docx_multiple_paragraphs_order(tmp_path: Path):
    """Paragraphs are extracted in document order."""
    path = tmp_path / "multi.docx"
    doc = docx.Document()
    doc.add_paragraph("First paragraph")
    doc.add_paragraph("Second paragraph")
    doc.add_paragraph("Third paragraph")
    doc.save(path)

    text, chars, pages, error = extract_docx(path)

    assert error is None
    lines = text.splitlines()
    assert lines[0] == "First paragraph"
    assert lines[1] == "Second paragraph"
    assert lines[2] == "Third paragraph"


def test_extract_docx_empty(tmp_path: Path):
    """Empty DOCX returns empty string."""
    path = tmp_path / "empty.docx"
    doc = docx.Document()
    doc.save(path)

    text, chars, pages, error = extract_docx(path)

    assert error is None
    assert text == ""
    assert chars == 0


def test_extract_docx_table_text(tmp_path: Path):
    """Basic table cell text is extracted."""
    path = tmp_path / "table.docx"
    doc = docx.Document()
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Row0Col0"
    table.cell(0, 1).text = "Row0Col1"
    table.cell(1, 0).text = "Row1Col0"
    table.cell(1, 1).text = "Row1Col1"
    doc.save(path)

    text, chars, pages, error = extract_docx(path)

    assert error is None
    assert "Row0Col0" in text
    assert "Row1Col1" in text


def test_extract_docx_malformed(tmp_path: Path):
    """Malformed DOCX (not a valid zip/DOCX) returns FAILED."""
    path = tmp_path / "malformed.docx"
    path.write_bytes(b"This is not a DOCX file at all")

    text, chars, pages, error = extract_docx(path)

    assert text is None
    assert error is not None
    assert "DOCX extraction failed" in error


def test_extract_docx_deterministic(tmp_path: Path):
    """DOCX extraction is deterministic."""
    path = tmp_path / "det.docx"
    doc = docx.Document()
    doc.add_paragraph("Deterministic content")
    doc.save(path)

    r1 = extract_docx(path)
    r2 = extract_docx(path)

    assert r1 == r2


def test_extract_docx_readonly(tmp_path: Path):
    """DOCX extraction does not modify the source file."""
    path = tmp_path / "ro.docx"
    doc = docx.Document()
    doc.add_paragraph("Read only")
    doc.save(path)

    raw_before = path.read_bytes()
    mtime_before = path.stat().st_mtime

    extract_docx(path)

    assert path.read_bytes() == raw_before
    assert path.stat().st_mtime == mtime_before


def test_extract_docx_missing_file(tmp_path: Path):
    """Missing DOCX returns FAILED with error info."""
    path = tmp_path / "nonexistent.docx"

    text, chars, pages, error = extract_docx(path)

    assert text is None
    assert error is not None
