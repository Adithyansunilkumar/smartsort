"""Tests for Phase 5 Extraction Service."""

import pytest
from pathlib import Path
from datetime import datetime, timezone
import docx

from app.services.folder_access import FolderAccessService, get_folder_access_service
from app.services.extraction import extract_scan_documents
from app.database.repositories import ScanRepository, ContentRepository
from app.filesystem.scanner import ScanResult, ScannedFile, ScanStatus


@pytest.fixture
def extraction_scan(authorized_root: Path, clean_service: FolderAccessService) -> str:
    """Creates a scan with TXT, DOCX, and unsupported files."""
    scan_id = "test-extract-scan"

    txt_file = authorized_root / "test.txt"
    txt_file.write_text("Text content", encoding="utf-8")

    docx_file = authorized_root / "test.docx"
    doc = docx.Document()
    doc.add_paragraph("Docx content")
    doc.save(docx_file)

    jpg_file = authorized_root / "test.jpg"
    jpg_file.write_text("not a real jpg")

    scan = ScanResult(
        scan_id=scan_id,
        status=ScanStatus.COMPLETED,
        root_display="TestRoot",
        file_count=3,
        directory_count=0,
        skip_count=0,
        scanned_at=datetime.now(tz=timezone.utc).isoformat(),
        files=[
            ScannedFile(name="test.txt", relative_path="test.txt", extension=".txt", size_bytes=12, modified_at="2024-01-01T00:00:00Z"),
            ScannedFile(name="test.docx", relative_path="test.docx", extension=".docx", size_bytes=1000, modified_at="2024-01-01T00:00:00Z"),
            ScannedFile(name="test.jpg", relative_path="test.jpg", extension=".jpg", size_bytes=100, modified_at="2024-01-01T00:00:00Z"),
        ],
    )
    ScanRepository.save_scan(scan)
    return scan_id


def test_extraction_service_success(extraction_scan: str, clean_service: FolderAccessService):
    """Standard extraction processes TXT/DOCX as SUCCESS, JPG as UNSUPPORTED."""
    result = extract_scan_documents(extraction_scan, clean_service)

    assert result.scan_id == extraction_scan
    assert result.total_processed == 3
    assert result.successful == 2
    assert result.unsupported == 1
    assert result.failed == 0
    assert result.empty == 0

    db_results = ContentRepository.get_extraction_results_for_scan(extraction_scan)
    assert len(db_results) == 3
    statuses = [r["status"] for r in db_results]
    assert statuses.count("SUCCESS") == 2
    assert statuses.count("UNSUPPORTED") == 1


def test_extraction_service_missing_scan(clean_service: FolderAccessService, authorized_root: Path):
    """Extraction on a non-existent scan raises ValueError."""
    with pytest.raises(ValueError, match="not found"):
        extract_scan_documents("missing-scan", clean_service)


def test_extraction_service_path_escape(authorized_root: Path, clean_service: FolderAccessService):
    """Extraction rejects a stored path that would escape the authorized root."""
    scan_id = "test-escape-scan"

    scan = ScanResult(
        scan_id=scan_id,
        status=ScanStatus.COMPLETED,
        root_display="TestRoot",
        file_count=1,
        directory_count=0,
        skip_count=0,
        scanned_at=datetime.now(tz=timezone.utc).isoformat(),
        files=[
            ScannedFile(
                name="outside.txt",
                relative_path="../outside.txt",
                extension=".txt",
                size_bytes=10,
                modified_at="2024-01-01T00:00:00Z",
            )
        ],
    )
    ScanRepository.save_scan(scan)

    result = extract_scan_documents(scan_id, clean_service)

    assert result.failed == 1
    assert result.documents[0].status == "FAILED"
    assert "Security rejection" in result.documents[0].error_info


def test_extraction_idempotent_no_re_extract(extraction_scan: str, clean_service: FolderAccessService):
    """Running extraction twice does not duplicate or reset SUCCESS records."""
    r1 = extract_scan_documents(extraction_scan, clean_service)
    r2 = extract_scan_documents(extraction_scan, clean_service)

    # Both runs should report the same successful count
    assert r1.successful == r2.successful == 2
    assert r1.unsupported == r2.unsupported == 1

    # DB still has only 3 rows — no duplicates
    db_results = ContentRepository.get_extraction_results_for_scan(extraction_scan)
    assert len(db_results) == 3


def test_extraction_empty_txt(authorized_root: Path, clean_service: FolderAccessService):
    """An empty TXT file is recorded as EMPTY, not SUCCESS."""
    scan_id = "test-empty-txt-scan"
    empty_txt = authorized_root / "empty.txt"
    empty_txt.write_text("")

    scan = ScanResult(
        scan_id=scan_id,
        status=ScanStatus.COMPLETED,
        root_display="TestRoot",
        file_count=1,
        directory_count=0,
        skip_count=0,
        scanned_at=datetime.now(tz=timezone.utc).isoformat(),
        files=[
            ScannedFile(name="empty.txt", relative_path="empty.txt", extension=".txt", size_bytes=0, modified_at="2024-01-01T00:00:00Z"),
        ],
    )
    ScanRepository.save_scan(scan)

    result = extract_scan_documents(scan_id, clean_service)

    assert result.empty == 1
    assert result.successful == 0
