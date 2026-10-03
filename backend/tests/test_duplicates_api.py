"""Tests for the duplicates API endpoint."""

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.folder_access import FolderAccessService
from app.database.repositories import ScanRepository
from app.filesystem.scanner import ScanResult, ScannedFile, ScanStatus

client = TestClient(app)


def _seed_scan(scan_id: str):
    """Seed the database with a fake scan."""
    result = ScanResult(
        scan_id=scan_id,
        status=ScanStatus.COMPLETED,
        root_display="Root",
        file_count=2,
        directory_count=0,
        skip_count=0,
        scanned_at="2026-01-01T00:00:00Z",
        files=[
            ScannedFile(name="A.txt", relative_path="A.txt", extension=".txt", size_bytes=10, modified_at="2026-01-01T00:00:00Z"),
            ScannedFile(name="B.txt", relative_path="B.txt", extension=".txt", size_bytes=10, modified_at="2026-01-01T00:00:00Z"),
        ],
    )
    ScanRepository.save_scan(result)


def test_duplicates_api_success(authorized_root: Path, clean_database: Path):
    """A valid scan with duplicates returns 200 and structured groups."""
    scan_id = "api-test-001"
    _seed_scan(scan_id)
    
    # Create the actual files so the hasher succeeds
    (authorized_root / "A.txt").write_bytes(b"duplicate!")
    (authorized_root / "B.txt").write_bytes(b"duplicate!")
    
    response = client.post(f"/api/duplicates/{scan_id}")
    
    assert response.status_code == 200
    data = response.json()
    
    assert data["scan_id"] == scan_id
    assert data["total_groups"] == 1
    assert data["total_duplicate_files"] == 2
    
    group = data["groups"][0]
    assert group["size_bytes"] == 10
    assert len(group["files"]) == 2


def test_duplicates_api_missing_scan(authorized_root: Path, clean_database: Path):
    """A missing scan ID returns 404."""
    response = client.post("/api/duplicates/does-not-exist")
    
    assert response.status_code == 404
    data = response.json()
    assert data["detail"]["code"] == "SCAN_NOT_FOUND"


def test_duplicates_api_root_not_configured(clean_service: FolderAccessService):
    """If no root is configured, it returns 409."""
    # Seed DB so scan is found
    _seed_scan("no-root-001")
    
    # The root is intentionally not set (clean_service)
    response = client.post("/api/duplicates/no-root-001")
    
    assert response.status_code == 409
    data = response.json()
    assert data["detail"]["code"] == "ROOT_NOT_CONFIGURED"


def test_duplicates_api_security_violation_skips_file(authorized_root: Path, clean_database: Path):
    """If a security error is raised during validation of a file, the file is skipped (200 OK, 0 groups)."""
    scan_id = "sec-001"
    _seed_scan(scan_id)
    
    # Mock validate_path_in_root to throw a security error for the files
    with patch.object(FolderAccessService, "validate_path_in_root") as mock_validate:
        from app.security.paths import PathSecurityError
        mock_validate.side_effect = PathSecurityError(code="OUTSIDE_ROOT", message="Mock")
        
        response = client.post(f"/api/duplicates/{scan_id}")
        
    assert response.status_code == 200
    data = response.json()
    assert data["total_groups"] == 0


def test_duplicates_api_unexpected_error(authorized_root: Path, clean_database: Path):
    """An unexpected exception returns 500."""
    scan_id = "err-001"
    _seed_scan(scan_id)
    
    with patch("app.services.duplicates.ScanRepository.get_files_by_scan_id") as mock_get:
        mock_get.side_effect = RuntimeError("DB down")
        
        response = client.post(f"/api/duplicates/{scan_id}")
        
    assert response.status_code == 500
    data = response.json()
    assert data["detail"]["code"] == "DUPLICATE_DETECTION_ERROR"
