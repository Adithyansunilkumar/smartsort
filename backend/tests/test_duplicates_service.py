"""Tests for the duplicate detection service orchestration."""

from pathlib import Path
from unittest.mock import patch
import hashlib

import pytest

from app.services.duplicates import find_duplicates
from app.services.folder_access import FolderAccessService, get_folder_access_service
from app.database.repositories import ScanRepository
from app.database.connection import get_connection
from app.filesystem.scanner import ScanResult, ScannedFile, ScanStatus


@pytest.fixture
def populated_scan(authorized_root: Path, clean_database: Path) -> str:
    """Creates a scan with known duplicate and unique files."""
    scan_id = "test-dup-001"
    
    # 1. Unique file (size 10)
    f1 = authorized_root / "unique.txt"
    f1.write_text("0123456789")
    
    # 2. Duplicate group A (size 15, 3 files)
    content_a = b"duplicate_A_123"
    (authorized_root / "dupA1.txt").write_bytes(content_a)
    (authorized_root / "dupA2.txt").write_bytes(content_a)
    (authorized_root / "sub").mkdir()
    (authorized_root / "sub" / "dupA3.txt").write_bytes(content_a)
    
    # 3. Duplicate group B (size 0, 2 files)
    (authorized_root / "empty1.txt").write_bytes(b"")
    (authorized_root / "empty2.txt").write_bytes(b"")
    
    # 4. Same size as Group A, but different content (not a duplicate of A)
    content_b = b"duplicate_B_123"
    (authorized_root / "fakeA.txt").write_bytes(content_b)
    
    # Create the scan result
    files = [
        ScannedFile(name="unique.txt", relative_path="unique.txt", extension=".txt", size_bytes=10, modified_at="2026-01-01T00:00:00Z"),
        ScannedFile(name="dupA1.txt", relative_path="dupA1.txt", extension=".txt", size_bytes=15, modified_at="2026-01-01T00:00:00Z"),
        ScannedFile(name="dupA2.txt", relative_path="dupA2.txt", extension=".txt", size_bytes=15, modified_at="2026-01-01T00:00:00Z"),
        ScannedFile(name="dupA3.txt", relative_path="sub/dupA3.txt", extension=".txt", size_bytes=15, modified_at="2026-01-01T00:00:00Z"),
        ScannedFile(name="empty1.txt", relative_path="empty1.txt", extension=".txt", size_bytes=0, modified_at="2026-01-01T00:00:00Z"),
        ScannedFile(name="empty2.txt", relative_path="empty2.txt", extension=".txt", size_bytes=0, modified_at="2026-01-01T00:00:00Z"),
        ScannedFile(name="fakeA.txt", relative_path="fakeA.txt", extension=".txt", size_bytes=15, modified_at="2026-01-01T00:00:00Z"),
    ]
    
    result = ScanResult(
        scan_id=scan_id,
        status=ScanStatus.COMPLETED,
        root_display="TestRoot",
        file_count=len(files),
        directory_count=1,
        skip_count=0,
        scanned_at="2026-01-01T00:00:00Z",
        files=files,
    )
    
    ScanRepository.save_scan(result)
    return scan_id


def test_find_duplicates_groups_correctly(populated_scan: str, clean_service: FolderAccessService):
    """Duplicates should be grouped correctly by size and hash."""
    result = find_duplicates(populated_scan, clean_service)
    
    # We expect 2 duplicate groups: Group A (3 files), Group B (2 empty files)
    assert result.total_groups == 2
    assert result.total_duplicate_files == 5
    
    groups = result.groups
    # Groups are sorted by size descending (15 > 0)
    assert groups[0].size_bytes == 15
    assert len(groups[0].files) == 3
    assert {f.name for f in groups[0].files} == {"dupA1.txt", "dupA2.txt", "dupA3.txt"}
    
    assert groups[1].size_bytes == 0
    assert len(groups[1].files) == 2
    assert {f.name for f in groups[1].files} == {"empty1.txt", "empty2.txt"}
    
    # unique.txt and fakeA.txt should not be in any group
    all_grouped_names = {f.name for g in groups for f in g.files}
    assert "unique.txt" not in all_grouped_names
    assert "fakeA.txt" not in all_grouped_names


def test_find_duplicates_persists_hashes(populated_scan: str, clean_service: FolderAccessService):
    """Hashes calculated during detection must be saved to the database."""
    # Before detection, hashes are NULL
    with get_connection() as conn:
        null_hashes = conn.execute("SELECT count(*) as c FROM files WHERE hash IS NULL").fetchone()["c"]
        assert null_hashes == 7
        
    find_duplicates(populated_scan, clean_service)
    
    with get_connection() as conn:
        # unique.txt (size 10) was filtered out by size, so it wasn't hashed.
        # dupA1, dupA2, dupA3, fakeA (size 15) were hashed (4 files)
        # empty1, empty2 (size 0) were hashed (2 files)
        # Total hashed = 6. Remaining NULL = 1 (unique.txt).
        null_hashes = conn.execute("SELECT count(*) as c FROM files WHERE hash IS NULL").fetchone()["c"]
        assert null_hashes == 1
        
        # Verify a specific hash
        dupA1 = conn.execute("SELECT hash FROM files WHERE name = 'dupA1.txt'").fetchone()
        expected = hashlib.sha256(b"duplicate_A_123").hexdigest()
        assert dupA1["hash"] == expected


def test_find_duplicates_avoids_recalculating_hashes(populated_scan: str, clean_service: FolderAccessService):
    """If a hash is already in the database, it shouldn't be recalculated."""
    # Run once to populate hashes
    find_duplicates(populated_scan, clean_service)
    
    # Run again, mock the hasher to ensure it's not called
    with patch("app.services.duplicates.calculate_file_hash") as mock_hasher:
        find_duplicates(populated_scan, clean_service)
        mock_hasher.assert_not_called()


def test_find_duplicates_handles_missing_file_gracefully(populated_scan: str, clean_service: FolderAccessService, authorized_root: Path):
    """If a file disappears before hashing, it's skipped without crashing."""
    # Delete one of the duplicates
    (authorized_root / "dupA1.txt").unlink()
    
    result = find_duplicates(populated_scan, clean_service)
    
    # Group A should now only have 2 files (dupA2, dupA3)
    assert result.total_groups == 2
    assert len(result.groups[0].files) == 2
    assert "dupA1.txt" not in {f.name for f in result.groups[0].files}
    
    # Database hash for dupA1 should remain NULL
    with get_connection() as conn:
        hash_val = conn.execute("SELECT hash FROM files WHERE name = 'dupA1.txt'").fetchone()["hash"]
        assert hash_val is None


def test_find_duplicates_revalidates_security(populated_scan: str, clean_service: FolderAccessService):
    """Every file must be re-validated through FolderAccessService before hashing."""
    with patch.object(clean_service, "validate_path_in_root", wraps=clean_service.validate_path_in_root) as mock_validate:
        find_duplicates(populated_scan, clean_service)
        
        # Called 6 times (4 for size 15, 2 for size 0)
        assert mock_validate.call_count == 6


def test_find_duplicates_skips_security_violations(populated_scan: str, clean_service: FolderAccessService):
    """If a path fails security validation, it is skipped."""
    
    original_validate = clean_service.validate_path_in_root
    
    def mock_validate(path, *args, **kwargs):
        # Force a security error on dupA2.txt
        if "dupA2.txt" in str(path):
            from app.security.paths import PathSecurityError
            raise PathSecurityError(code="OUTSIDE_ROOT", message="Mocked")
        return original_validate(path, *args, **kwargs)

    with patch.object(clean_service, "validate_path_in_root", side_effect=mock_validate):
        result = find_duplicates(populated_scan, clean_service)
        
    # dupA2 is skipped, so group A has dupA1 and dupA3 (2 files)
    assert len(result.groups[0].files) == 2
    assert "dupA2.txt" not in {f.name for f in result.groups[0].files}


def test_find_duplicates_missing_scan():
    """Requesting a missing scan_id raises ValueError."""
    service = get_folder_access_service()
    with pytest.raises(ValueError, match="not found"):
        find_duplicates("nonexistent-scan", service)
