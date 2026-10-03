"""Tests for Phase 5 Document Extraction Stale Cache/Freshness handling."""

import os
import pytest
from pathlib import Path
from datetime import datetime, timezone
import docx
import pypdf

from app.services.folder_access import FolderAccessService
from app.services.extraction import extract_scan_documents
from app.database.repositories import ScanRepository, ContentRepository
from app.filesystem.scanner import ScanResult, ScannedFile, ScanStatus
from app.filesystem.hasher import calculate_file_hash


def _make_scan(scan_id: str, files: list[ScannedFile]) -> ScanResult:
    return ScanResult(
        scan_id=scan_id,
        status=ScanStatus.COMPLETED,
        root_display="TestRoot",
        file_count=len(files),
        directory_count=0,
        skip_count=0,
        scanned_at=datetime.now(tz=timezone.utc).isoformat(),
        files=files,
    )


def test_freshness_successful_creates_hash(authorized_root: Path, clean_service: FolderAccessService):
    """Successful extraction creates a source hash."""
    f = authorized_root / "test.txt"
    f.write_text("Hello")
    f_hash = calculate_file_hash(f)

    scan_id = "fresh-success"
    scan = _make_scan(scan_id, [
        ScannedFile(name="test.txt", relative_path="test.txt", extension=".txt",
                    size_bytes=f.stat().st_size, modified_at="2024-01-01T00:00:00Z"),
    ])
    ScanRepository.save_scan(scan)

    extract_scan_documents(scan_id, clean_service)
    
    results = ContentRepository.get_extraction_results_for_scan(scan_id)
    assert len(results) == 1
    assert results[0]["status"] == "SUCCESS"
    assert results[0]["source_hash"] == f_hash


def test_freshness_unchanged_reuses_cache(authorized_root: Path, clean_service: FolderAccessService):
    """Repeated extraction of an unchanged file reuses cached content."""
    f = authorized_root / "reuse.txt"
    f.write_text("Hello")
    f_hash = calculate_file_hash(f)

    scan_id = "fresh-reuse"
    scan = _make_scan(scan_id, [
        ScannedFile(name="reuse.txt", relative_path="reuse.txt", extension=".txt",
                    size_bytes=f.stat().st_size, modified_at="2024-01-01T00:00:00Z"),
    ])
    ScanRepository.save_scan(scan)

    r1 = extract_scan_documents(scan_id, clean_service)
    assert r1.successful == 1

    # Call it again
    r2 = extract_scan_documents(scan_id, clean_service)
    assert r2.successful == 1

    # Should still only be 1 row
    results = ContentRepository.get_extraction_results_for_scan(scan_id)
    assert len(results) == 1
    assert results[0]["source_hash"] == f_hash


def test_freshness_modified_txt_reextracts(authorized_root: Path, clean_service: FolderAccessService):
    """Modifying a TXT file causes re-extraction and updates hash."""
    f = authorized_root / "mod.txt"
    f.write_text("Old")
    
    scan_id = "fresh-mod"
    scan = _make_scan(scan_id, [
        ScannedFile(name="mod.txt", relative_path="mod.txt", extension=".txt",
                    size_bytes=f.stat().st_size, modified_at="2024-01-01T00:00:00Z"),
    ])
    ScanRepository.save_scan(scan)

    extract_scan_documents(scan_id, clean_service)
    old_results = ContentRepository.get_extraction_results_for_scan(scan_id)
    assert old_results[0]["character_count"] == 3

    # Modify the file
    f.write_text("New content!")
    new_hash = calculate_file_hash(f)
    
    extract_scan_documents(scan_id, clean_service)
    
    new_results = ContentRepository.get_extraction_results_for_scan(scan_id)
    assert len(new_results) == 1
    assert new_results[0]["character_count"] == 12
    assert new_results[0]["source_hash"] == new_hash


def test_freshness_unchanged_empty_cached(authorized_root: Path, clean_service: FolderAccessService):
    """Unchanged EMPTY result is cached."""
    f = authorized_root / "empty.txt"
    f.write_text("")
    
    scan_id = "fresh-empty"
    scan = _make_scan(scan_id, [
        ScannedFile(name="empty.txt", relative_path="empty.txt", extension=".txt",
                    size_bytes=0, modified_at="2024-01-01T00:00:00Z"),
    ])
    ScanRepository.save_scan(scan)

    extract_scan_documents(scan_id, clean_service)
    extract_scan_documents(scan_id, clean_service)
    
    results = ContentRepository.get_extraction_results_for_scan(scan_id)
    assert len(results) == 1
    assert results[0]["status"] == "EMPTY"


def test_freshness_changed_empty_reprocessed(authorized_root: Path, clean_service: FolderAccessService):
    """Changed EMPTY file is reprocessed and becomes SUCCESS."""
    f = authorized_root / "was_empty.txt"
    f.write_text("")
    
    scan_id = "fresh-was-empty"
    scan = _make_scan(scan_id, [
        ScannedFile(name="was_empty.txt", relative_path="was_empty.txt", extension=".txt",
                    size_bytes=0, modified_at="2024-01-01T00:00:00Z"),
    ])
    ScanRepository.save_scan(scan)

    extract_scan_documents(scan_id, clean_service)
    
    f.write_text("Now has text")
    extract_scan_documents(scan_id, clean_service)
    
    results = ContentRepository.get_extraction_results_for_scan(scan_id)
    assert len(results) == 1
    assert results[0]["status"] == "SUCCESS"
    assert results[0]["character_count"] == 12


def test_freshness_unchanged_unsupported_cached(authorized_root: Path, clean_service: FolderAccessService):
    """Unchanged UNSUPPORTED result is cached."""
    f = authorized_root / "image.png"
    f.write_bytes(b"\x89PNG")
    
    scan_id = "fresh-unsupported"
    scan = _make_scan(scan_id, [
        ScannedFile(name="image.png", relative_path="image.png", extension=".png",
                    size_bytes=4, modified_at="2024-01-01T00:00:00Z"),
    ])
    ScanRepository.save_scan(scan)

    extract_scan_documents(scan_id, clean_service)
    extract_scan_documents(scan_id, clean_service)
    
    results = ContentRepository.get_extraction_results_for_scan(scan_id)
    assert len(results) == 1
    assert results[0]["status"] == "UNSUPPORTED"


def test_freshness_extension_change_reextracts(authorized_root: Path, clean_service: FolderAccessService):
    """File extension/type change does not reuse incompatible cached content."""
    f = authorized_root / "file.txt"
    f.write_text("Text content")
    
    scan_id = "fresh-ext"
    scan = _make_scan(scan_id, [
        ScannedFile(name="file.txt", relative_path="file.txt", extension=".txt",
                    size_bytes=12, modified_at="2024-01-01T00:00:00Z"),
    ])
    ScanRepository.save_scan(scan)
    extract_scan_documents(scan_id, clean_service)
    
    # Now pretend the same file_id on disk is replaced by an image, but it still has .txt extension
    # Actually, if the file changes to an image (unsupported content but still .txt extension)
    f.write_bytes(b"bad binary data not a valid text maybe")
    # In reality, TXT parser might just read it as weird characters.
    # What if it changes to a valid DOCX?
    doc = docx.Document()
    doc.add_paragraph("DOCX content")
    doc.save(f)
    
    # Run extraction again - it will read as TXT because extension is .txt, but the hash changed!
    # So it MUST re-extract, which will yield garbage text from the zip file.
    extract_scan_documents(scan_id, clean_service)
    results = ContentRepository.get_extraction_results_for_scan(scan_id)
    assert results[0]["character_count"] != 12 # Has changed
    
def test_freshness_missing_source_fails(authorized_root: Path, clean_service: FolderAccessService):
    """Missing/inaccessible source does not return stale content."""
    f = authorized_root / "missing.txt"
    f.write_text("Old text")
    
    scan_id = "fresh-missing"
    scan = _make_scan(scan_id, [
        ScannedFile(name="missing.txt", relative_path="missing.txt", extension=".txt",
                    size_bytes=8, modified_at="2024-01-01T00:00:00Z"),
    ])
    ScanRepository.save_scan(scan)
    extract_scan_documents(scan_id, clean_service)
    
    # Delete the file
    f.unlink()
    
    extract_scan_documents(scan_id, clean_service)
    
    results = ContentRepository.get_extraction_results_for_scan(scan_id)
    assert len(results) == 1
    assert results[0]["status"] == "FAILED"
    
def test_freshness_security_revalidation_occurs(authorized_root: Path, clean_service: FolderAccessService, tmp_path: Path):
    """Security re-validation still occurs before current-file hashing/reading."""
    f = authorized_root / "safe.txt"
    f.write_text("Safe")
    
    scan_id = "fresh-sec"
    scan = _make_scan(scan_id, [
        ScannedFile(name="safe.txt", relative_path="safe.txt", extension=".txt",
                    size_bytes=4, modified_at="2024-01-01T00:00:00Z"),
    ])
    ScanRepository.save_scan(scan)
    extract_scan_documents(scan_id, clean_service)
    
    # Now replace the safe file with a symlink outside the root
    if os.name == 'nt':
        import sys
        if not os.environ.get("SMARTSORT_TEST_SYMLINKS"):
            pytest.skip("Windows symlink test skipped")
            
    f.unlink()
    outside = tmp_path / "outside.txt"
    outside.write_text("Outside")
    try:
        f.symlink_to(outside)
    except OSError:
        pytest.skip("Requires privilege for symlinks")
        
    extract_scan_documents(scan_id, clean_service)
    results = ContentRepository.get_extraction_results_for_scan(scan_id)
    assert results[0]["status"] == "FAILED"
