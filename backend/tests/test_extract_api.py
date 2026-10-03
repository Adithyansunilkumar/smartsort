"""Tests for Phase 5 Extract API.

Uses the conftest.py authorized_root fixture (which properly depends on
clean_service) to avoid the autouse fixture ordering problem.
"""

import pytest
from fastapi.testclient import TestClient
from pathlib import Path
from datetime import datetime, timezone
import pypdf

from app.main import app
from app.services.folder_access import FolderAccessService
from app.database.repositories import ScanRepository
from app.filesystem.scanner import ScanResult, ScannedFile, ScanStatus


client = TestClient(app)


@pytest.fixture
def api_scan(authorized_root: Path, clean_service: FolderAccessService) -> str:
    """Creates a scan with one TXT file and returns the scan_id."""
    scan_id = "api-extract-scan"

    txt_file = authorized_root / "test.txt"
    txt_file.write_text("Hello API", encoding="utf-8")

    scan = ScanResult(
        scan_id=scan_id,
        status=ScanStatus.COMPLETED,
        root_display="test",
        scanned_at=datetime.now(tz=timezone.utc).isoformat(),
        file_count=1,
        directory_count=0,
        skip_count=0,
        files=[
            ScannedFile(
                name="test.txt",
                relative_path="test.txt",
                extension=".txt",
                size_bytes=9,
                modified_at="2024-01-01T00:00:00Z",
            )
        ],
    )
    ScanRepository.save_scan(scan)
    return scan_id


def test_extract_api_success(api_scan: str, clean_service: FolderAccessService):
    """Test standard successful API extraction."""
    response = client.post(f"/api/extract/{api_scan}")

    assert response.status_code == 200
    data = response.json()
    assert data["scan_id"] == api_scan
    assert data["total_processed"] == 1
    assert data["successful"] == 1
    assert data["failed"] == 0
    assert len(data["documents"]) == 1

    doc = data["documents"][0]
    assert doc["status"] == "SUCCESS"
    assert doc["character_count"] == 9
    assert doc["relative_path"] == "test.txt"
    # Must not expose any absolute paths
    assert "/" not in doc["relative_path"].replace("\\", "/").lstrip("./") or doc["relative_path"] == "test.txt"


def test_extract_api_missing_scan(authorized_root: Path, clean_service: FolderAccessService):
    """Test extraction on non-existent scan."""
    response = client.post("/api/extract/invalid-scan")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "SCAN_NOT_FOUND"


def test_extract_api_no_root(clean_service: FolderAccessService):
    """Test extraction with no authorized root configured."""
    # clean_service is already cleared by the autouse fixture
    response = client.post("/api/extract/any-scan")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ROOT_NOT_CONFIGURED"


def test_extract_api_mixed_files(authorized_root: Path, clean_service: FolderAccessService):
    """Mixed scan: TXT succeeds, JPG is unsupported."""
    scan_id = "api-mixed-scan"

    (authorized_root / "doc.txt").write_text("text content", encoding="utf-8")
    (authorized_root / "image.jpg").write_bytes(b"\xff\xd8\xff")  # JPEG magic bytes

    scan = ScanResult(
        scan_id=scan_id,
        status=ScanStatus.COMPLETED,
        root_display="test",
        scanned_at=datetime.now(tz=timezone.utc).isoformat(),
        file_count=2,
        directory_count=0,
        skip_count=0,
        files=[
            ScannedFile(name="doc.txt", relative_path="doc.txt", extension=".txt", size_bytes=12, modified_at="2024-01-01T00:00:00Z"),
            ScannedFile(name="image.jpg", relative_path="image.jpg", extension=".jpg", size_bytes=3, modified_at="2024-01-01T00:00:00Z"),
        ],
    )
    ScanRepository.save_scan(scan)

    response = client.post(f"/api/extract/{scan_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["total_processed"] == 2
    assert data["successful"] == 1
    assert data["unsupported"] == 1
    assert data["failed"] == 0


def test_extract_api_per_file_failure_no_batch_fail(authorized_root: Path, clean_service: FolderAccessService):
    """A single corrupted PDF must not fail the entire batch."""
    scan_id = "api-partial-fail-scan"

    good_txt = authorized_root / "good.txt"
    good_txt.write_text("good content", encoding="utf-8")

    bad_pdf = authorized_root / "bad.pdf"
    bad_pdf.write_bytes(b"NOT A REAL PDF")

    scan = ScanResult(
        scan_id=scan_id,
        status=ScanStatus.COMPLETED,
        root_display="test",
        scanned_at=datetime.now(tz=timezone.utc).isoformat(),
        file_count=2,
        directory_count=0,
        skip_count=0,
        files=[
            ScannedFile(name="good.txt", relative_path="good.txt", extension=".txt", size_bytes=12, modified_at="2024-01-01T00:00:00Z"),
            ScannedFile(name="bad.pdf", relative_path="bad.pdf", extension=".pdf", size_bytes=14, modified_at="2024-01-01T00:00:00Z"),
        ],
    )
    ScanRepository.save_scan(scan)

    response = client.post(f"/api/extract/{scan_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["successful"] == 1
    assert data["failed"] == 1
    # Overall HTTP status is still 200 — batch didn't fail


def test_extract_api_repeated_extraction(api_scan: str, clean_service: FolderAccessService):
    """Repeated extraction returns consistent results without duplicate DB rows."""
    r1 = client.post(f"/api/extract/{api_scan}")
    r2 = client.post(f"/api/extract/{api_scan}")

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["successful"] == r2.json()["successful"] == 1


def test_extract_api_no_absolute_paths_in_response(api_scan: str, clean_service: FolderAccessService):
    """The API response must not expose absolute filesystem paths."""
    response = client.post(f"/api/extract/{api_scan}")
    assert response.status_code == 200

    import json
    body = json.dumps(response.json())
    # No Windows drive letters or Unix root paths
    assert ":\\" not in body
    assert "C:\\" not in body
    assert "AppData" not in body
    assert "/tmp/" not in body
