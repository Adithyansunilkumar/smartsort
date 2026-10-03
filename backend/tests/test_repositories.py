"""Tests for database repositories."""

from app.database.repositories import (
    ScanRepository,
    PreferenceRepository,
    ProtectedPathRepository,
    OperationRepository,
)
from app.filesystem.scanner import ScanResult, ScannedFile, ScannedDirectory, ScanStatus, ScanSkip
from app.database.connection import get_connection

def test_scan_repository_save_scan():
    """Test saving a complete scan result and retrieving it."""
    
    scan = ScanResult(
        scan_id="test-1234",
        status=ScanStatus.COMPLETED,
        root_display="TestRoot",
        file_count=1,
        directory_count=0,
        skip_count=0,
        scanned_at="2026-09-19T10:00:00Z",
        files=[
            ScannedFile(
                name="test.txt",
                relative_path="test.txt",
                extension=".txt",
                size_bytes=100,
                modified_at="2026-09-19T09:00:00Z",
                mime_type="text/plain",
            )
        ],
        directories=[],
        skips=[],
    )
    
    ScanRepository.save_scan(scan)
    
    with get_connection() as conn:
        # Check scan record
        cursor = conn.execute("SELECT * FROM scans WHERE id = 'test-1234'")
        row = cursor.fetchone()
        assert row is not None
        assert row["root_path"] == "TestRoot"
        assert row["file_count"] == 1
        assert row["status"] == "completed"
        
        # Check file record
        cursor = conn.execute("SELECT * FROM files WHERE scan_id = 'test-1234'")
        file_row = cursor.fetchone()
        assert file_row is not None
        assert file_row["name"] == "test.txt"
        assert file_row["extension"] == ".txt"


def test_preference_repository():
    """Test getting and setting preferences."""
    # Getting unknown preference should return default
    assert PreferenceRepository.get_preference("theme", "light") == "light"
    
    # Set and retrieve
    PreferenceRepository.set_preference("theme", "dark")
    assert PreferenceRepository.get_preference("theme") == "dark"
    
    # Update existing
    PreferenceRepository.set_preference("theme", "system")
    assert PreferenceRepository.get_preference("theme") == "system"
    
    # Structured data
    PreferenceRepository.set_preference("options", {"autoScan": True})
    assert PreferenceRepository.get_preference("options")["autoScan"] is True


def test_protected_path_repository():
    """Test adding and retrieving protected paths."""
    ProtectedPathRepository.add_protected_path("test/path1", "ignore", "2026-09-19T10:00:00Z")
    ProtectedPathRepository.add_protected_path("test/path2", "ignore", "2026-09-19T10:00:00Z")
    
    # Add duplicate should be ignored safely
    ProtectedPathRepository.add_protected_path("test/path1", "ignore", "2026-09-19T10:00:00Z")
    
    paths = ProtectedPathRepository.get_all_protected_paths()
    assert len(paths) == 2
    assert paths[0]["path"] == "test/path1"
    assert paths[1]["path"] == "test/path2"


def test_operation_repository():
    """Test adding operations."""
    OperationRepository.add_operation(
        operation_type="move",
        source="old.txt",
        destination="new.txt",
        status="success",
        created_at="2026-09-19T10:00:00Z",
        reason="organized",
    )
    
    with get_connection() as conn:
        cursor = conn.execute("SELECT * FROM operations")
        rows = cursor.fetchall()
        assert len(rows) == 1
        assert rows[0]["operation_type"] == "move"
        assert rows[0]["source"] == "old.txt"
        assert rows[0]["destination"] == "new.txt"
        assert rows[0]["status"] == "success"
