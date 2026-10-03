"""Tests for Phase 6 File Analysis."""

import pytest
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any

from app.analysis.rules import (
    get_file_category,
    get_age_metrics,
    get_size_bucket,
    parse_filename,
    analyze_path,
    analyze_content,
    ANALYSIS_VERSION
)
from app.analysis.service import analyze_scan_files
from app.analysis.models import AnalysisSummary
from app.database.repositories import ScanRepository, ContentRepository, AnalysisRepository
from app.filesystem.scanner import ScanResult, ScannedFile, ScanStatus
from app.services.folder_access import FolderAccessService


def test_category_mapping():
    assert get_file_category(".pdf", None) == "document"
    assert get_file_category(".jpg", None) == "image"
    assert get_file_category(".exe", None) == "executable"
    assert get_file_category(".py", None) == "code/source"
    assert get_file_category(".unknown", "text/plain") == "plain text"
    assert get_file_category(".xyz", None) == "unknown"


def test_age_metrics():
    # 5 days ago
    assert get_age_metrics("2024-01-05T00:00:00Z", "2024-01-10T00:00:00Z") == (5, "very_recent")
    # 20 days ago
    assert get_age_metrics("2023-12-21T00:00:00Z", "2024-01-10T00:00:00Z") == (20, "recent")
    # 400 days ago
    assert get_age_metrics("2022-12-06T00:00:00Z", "2024-01-10T00:00:00Z") == (400, "very_old")
    # Future modified time (should not be negative age)
    assert get_age_metrics("2024-01-15T00:00:00Z", "2024-01-10T00:00:00Z")[0] == 0


def test_size_bucket():
    mb = 1024 * 1024
    assert get_size_bucket(500) == "tiny"
    assert get_size_bucket(5 * mb) == "small"
    assert get_size_bucket(50 * mb) == "medium"
    assert get_size_bucket(500 * mb) == "large"
    assert get_size_bucket(2 * 1024 * mb) == "very_large"


def test_filename_parsing():
    # Normal file
    t, n, c, tmp = parse_filename("report.pdf", ".pdf")
    assert t == ["report"]
    assert n is None
    assert not c and not tmp
    
    # Numeric suffix
    t, n, c, tmp = parse_filename("image123.jpg", ".jpg")
    assert "image" in t and "123" in t
    assert n == 123
    
    # Copy patterns
    _, _, c, _ = parse_filename("doc - Copy.txt", ".txt")
    assert c
    _, _, c, _ = parse_filename("doc (2).txt", ".txt")
    assert c
    _, _, c, _ = parse_filename("doc_copy.txt", ".txt")
    assert c
    
    # Temp patterns
    _, _, _, tmp = parse_filename("~$word.docx", ".docx")
    assert tmp
    _, _, _, tmp = parse_filename("data.tmp", ".tmp")
    assert tmp


def test_path_analysis():
    # Root level
    d, r, p, t = analyze_path("file.txt")
    assert d == 0
    assert r
    assert p is None
    assert t == []
    
    # Nested
    d, r, p, t = analyze_path("docs/2024/report.pdf")
    assert d == 2
    assert not r
    assert p == "2024"
    assert "docs" in t and "2024" in t


def test_content_analysis():
    text = "Hello world.\nThis is a test document."
    c, w, l, rt = analyze_content(text)
    assert c == len(text)
    assert w == 7
    assert l == 2
    assert rt == 1
    
    # Empty
    c, w, l, rt = analyze_content(None)
    assert c is None


def test_analysis_service_integration(authorized_root: Path, clean_service: FolderAccessService):
    f1 = authorized_root / "test.txt"
    f1.write_text("Hello analysis")
    
    f2 = authorized_root / "image (1).png"
    f2.write_bytes(b"image")
    
    scan_id = "test-analysis-scan"
    scan = ScanResult(
        scan_id=scan_id,
        status=ScanStatus.COMPLETED,
        root_display="TestRoot",
        file_count=2,
        directory_count=0,
        skip_count=0,
        scanned_at=datetime.now(tz=timezone.utc).isoformat(),
        files=[
            ScannedFile(name="test.txt", relative_path="test.txt", extension=".txt",
                        size_bytes=f1.stat().st_size, modified_at="2024-01-01T00:00:00Z"),
            ScannedFile(name="image (1).png", relative_path="image (1).png", extension=".png",
                        size_bytes=f2.stat().st_size, modified_at="2024-01-01T00:00:00Z")
        ]
    )
    ScanRepository.save_scan(scan)
    
    # Insert mock content extraction for f1
    files_in_db = ScanRepository.get_files_by_scan_id(scan_id)
    f1_id = next(f["id"] for f in files_in_db if f["name"] == "test.txt")
    f2_id = next(f["id"] for f in files_in_db if f["name"] == "image (1).png")
    
    ContentRepository.upsert_extraction_result(
        file_id=f1_id, status="SUCCESS", text_content="Hello analysis",
        character_count=14, page_count=1,
        extracted_at=datetime.now(tz=timezone.utc).isoformat(),
        error_info=None, source_hash="hash1"
    )
    
    # Run analysis
    summary = analyze_scan_files(scan_id, clean_service)
    assert summary.total_analyzed == 2
    assert summary.content_analyzed == 1
    assert summary.temp_or_copy_files == 1 # image (1).png is a copy pattern
    
    # Verify persistence
    results = AnalysisRepository.get_analysis_results_for_scan(scan_id)
    assert len(results) == 2
    
    f1_res = next(r for r in results if r["file_id"] == f1_id)
    assert f1_res["category"] == "document"
    assert f1_res["word_count"] == 2
    assert f1_res["is_copy"] == 0
    
    f2_res = next(r for r in results if r["file_id"] == f2_id)
    assert f2_res["category"] == "image"
    assert f2_res["word_count"] is None
    assert f2_res["is_copy"] == 1
    assert f2_res["numeric_suffix"] is None


def test_analysis_stale_cache(authorized_root: Path, clean_service: FolderAccessService):
    f = authorized_root / "stale.txt"
    f.write_text("Old content")
    
    scan_id = "stale-scan"
    scan = ScanResult(
        scan_id=scan_id,
        status=ScanStatus.COMPLETED,
        root_display="TestRoot",
        file_count=1,
        directory_count=0,
        skip_count=0,
        scanned_at=datetime.now(tz=timezone.utc).isoformat(),
        files=[
            ScannedFile(name="stale.txt", relative_path="stale.txt", extension=".txt",
                        size_bytes=f.stat().st_size, modified_at="2024-01-01T00:00:00Z")
        ]
    )
    ScanRepository.save_scan(scan)
    
    # Analyze once
    s1 = analyze_scan_files(scan_id, clean_service)
    
    files_in_db = ScanRepository.get_files_by_scan_id(scan_id)
    file_id = files_in_db[0]["id"]
    
    # Force modify file analysis slightly to verify caching
    AnalysisRepository.upsert_file_analysis({
        **AnalysisRepository.get_analysis_results_for_scan(scan_id)[0],
        "category": "hacked_category"
    })
    
    # Analyze again without changing file -> should load from cache
    s2 = analyze_scan_files(scan_id, clean_service)
    assert s2.documents[0].category == "hacked_category"
    
    # Change file size
    f.write_text("New content that is longer")
    
    # Analyze again -> should recompute (stale)
    s3 = analyze_scan_files(scan_id, clean_service)
    assert s3.documents[0].category == "document"  # Recomputed!
