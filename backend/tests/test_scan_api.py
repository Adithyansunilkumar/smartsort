"""API-level tests for POST /api/scan.

Tests exercise the full HTTP layer using FastAPI's TestClient.
The ``clean_service`` autouse fixture from conftest.py guarantees the
authorized root is cleared before and after each test.
"""

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.folder_access import FolderAccessService

client = TestClient(app)


# ---------------------------------------------------------------------------
# No authorized root
# ---------------------------------------------------------------------------


class TestScanNoRoot:
    def test_scan_without_root_returns_409(self, clean_service: FolderAccessService):
        """POST /api/scan must return 409 when no root is configured."""
        response = client.post("/api/scan")
        assert response.status_code == 409

    def test_scan_without_root_error_structure(self, clean_service: FolderAccessService):
        """409 response detail must have code and message fields."""
        response = client.post("/api/scan")
        detail = response.json()["detail"]
        assert "code" in detail
        assert "message" in detail
        assert isinstance(detail["code"], str)
        assert isinstance(detail["message"], str)

    def test_scan_without_root_error_code(self, clean_service: FolderAccessService):
        """409 error code must be ROOT_NOT_CONFIGURED."""
        response = client.post("/api/scan")
        detail = response.json()["detail"]
        assert detail["code"] == "ROOT_NOT_CONFIGURED"

    def test_scan_without_root_message_has_no_path(self, clean_service: FolderAccessService):
        """409 error message must not expose filesystem paths."""
        response = client.post("/api/scan")
        detail = response.json()["detail"]
        # Message should be a plain human-readable string without path components
        assert "\\" not in detail["message"] or "/" not in detail["message"]


# ---------------------------------------------------------------------------
# Successful scan — schema validation
# ---------------------------------------------------------------------------


class TestScanResponseSchema:
    def test_scan_returns_200(self, authorized_root: Path, clean_service: FolderAccessService):
        """POST /api/scan must return 200 when a root is configured."""
        response = client.post("/api/scan")
        assert response.status_code == 200

    def test_scan_response_has_scan_id(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """Response must include a non-empty scan_id."""
        response = client.post("/api/scan")
        data = response.json()
        assert "scan_id" in data
        assert data["scan_id"]

    def test_scan_id_is_valid_uuid(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """scan_id must be a valid UUID4 string."""
        response = client.post("/api/scan")
        data = response.json()
        # uuid.UUID constructor raises ValueError if invalid
        parsed = uuid.UUID(data["scan_id"])
        assert str(parsed) == data["scan_id"]

    def test_scan_response_has_status(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """Response must include a status field."""
        response = client.post("/api/scan")
        data = response.json()
        assert "status" in data
        assert data["status"] in ("completed", "failed")

    def test_scan_response_has_counts(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """Response must include file_count, directory_count, skip_count."""
        response = client.post("/api/scan")
        data = response.json()
        assert "file_count" in data
        assert "directory_count" in data
        assert "skip_count" in data
        assert isinstance(data["file_count"], int)
        assert isinstance(data["directory_count"], int)
        assert isinstance(data["skip_count"], int)

    def test_scan_response_has_root_display(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """Response must include root_display (basename only)."""
        response = client.post("/api/scan")
        data = response.json()
        assert "root_display" in data
        assert data["root_display"] == authorized_root.name

    def test_scan_response_has_scanned_at(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """Response must include scanned_at timestamp string."""
        response = client.post("/api/scan")
        data = response.json()
        assert "scanned_at" in data
        assert "T" in data["scanned_at"]

    def test_scan_response_has_lists(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """Response must include files, directories, and skips arrays."""
        response = client.post("/api/scan")
        data = response.json()
        assert "files" in data
        assert "directories" in data
        assert "skips" in data
        assert isinstance(data["files"], list)
        assert isinstance(data["directories"], list)
        assert isinstance(data["skips"], list)


# ---------------------------------------------------------------------------
# Counts accuracy
# ---------------------------------------------------------------------------


class TestScanCounts:
    def test_empty_root_counts(self, authorized_root: Path, clean_service: FolderAccessService):
        """An empty root must report zero files and zero directories."""
        response = client.post("/api/scan")
        data = response.json()
        assert data["file_count"] == 0
        assert data["directory_count"] == 0

    def test_file_count_matches_fixture(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """file_count must equal the number of actual files created."""
        (authorized_root / "a.txt").write_text("a")
        (authorized_root / "b.txt").write_text("b")
        (authorized_root / "c.pdf").write_bytes(b"pdf")

        response = client.post("/api/scan")
        data = response.json()
        assert data["file_count"] == 3

    def test_directory_count_matches_fixture(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """directory_count must equal the number of descendant directories."""
        (authorized_root / "sub1").mkdir()
        (authorized_root / "sub2").mkdir()
        (authorized_root / "sub2" / "nested").mkdir()

        response = client.post("/api/scan")
        data = response.json()
        assert data["directory_count"] == 3  # sub1, sub2, sub2/nested

    def test_counts_consistent_with_lists(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """file_count must equal len(files) and directory_count must equal len(directories)."""
        (authorized_root / "f1.txt").write_text("1")
        (authorized_root / "dir1").mkdir()

        response = client.post("/api/scan")
        data = response.json()
        assert data["file_count"] == len(data["files"])
        assert data["directory_count"] == len(data["directories"])
        assert data["skip_count"] == len(data["skips"])


# ---------------------------------------------------------------------------
# File entry schema
# ---------------------------------------------------------------------------


class TestFileEntrySchema:
    def test_file_entry_fields(self, authorized_root: Path, clean_service: FolderAccessService):
        """Each file entry must have the required fields."""
        (authorized_root / "sample.txt").write_text("content")

        response = client.post("/api/scan")
        data = response.json()
        assert data["file_count"] == 1
        file_entry = data["files"][0]

        for field in ("name", "relative_path", "extension", "size_bytes", "modified_at"):
            assert field in file_entry, f"Missing field: {field}"

    def test_file_relative_path_no_drive_letter(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """relative_path must not contain a Windows drive letter or be absolute."""
        (authorized_root / "file.txt").write_text("x")

        response = client.post("/api/scan")
        data = response.json()
        rp = data["files"][0]["relative_path"]
        assert not rp.startswith("/")
        assert ":" not in rp

    def test_nested_file_relative_path(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """Nested file relative_path must use forward slashes."""
        sub = authorized_root / "Docs" / "Reports"
        sub.mkdir(parents=True)
        (sub / "q4.xlsx").write_bytes(b"xlsx")

        response = client.post("/api/scan")
        data = response.json()
        file_paths = [f["relative_path"] for f in data["files"]]
        assert "Docs/Reports/q4.xlsx" in file_paths


# ---------------------------------------------------------------------------
# Directory entry schema
# ---------------------------------------------------------------------------


class TestDirectoryEntrySchema:
    def test_directory_entry_fields(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """Each directory entry must have name and relative_path."""
        (authorized_root / "MyFolder").mkdir()

        response = client.post("/api/scan")
        data = response.json()
        assert data["directory_count"] == 1
        dir_entry = data["directories"][0]

        assert "name" in dir_entry
        assert "relative_path" in dir_entry
        assert dir_entry["name"] == "MyFolder"
        assert dir_entry["relative_path"] == "MyFolder"


# ---------------------------------------------------------------------------
# Scan ID uniqueness
# ---------------------------------------------------------------------------


class TestScanIdUniqueness:
    def test_consecutive_scans_have_different_ids(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """Two consecutive scans of the same root must have different scan_ids."""
        r1 = client.post("/api/scan").json()
        r2 = client.post("/api/scan").json()
        assert r1["scan_id"] != r2["scan_id"]


# ---------------------------------------------------------------------------
# Completed status on successful scan
# ---------------------------------------------------------------------------


class TestScanStatus:
    def test_empty_root_status_completed(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """An empty root must produce status 'completed'."""
        response = client.post("/api/scan")
        assert response.json()["status"] == "completed"

    def test_populated_root_status_completed(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """A populated root must produce status 'completed'."""
        (authorized_root / "file.txt").write_text("data")
        (authorized_root / "sub").mkdir()

        response = client.post("/api/scan")
        assert response.json()["status"] == "completed"


# ---------------------------------------------------------------------------
# Root display — no absolute path leakage
# ---------------------------------------------------------------------------


class TestRootDisplaySecurity:
    def test_root_display_is_basename_only(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """root_display must be the basename of the root, not the full path."""
        response = client.post("/api/scan")
        data = response.json()
        # The root_display must equal the basename
        assert data["root_display"] == authorized_root.name
        # It must not contain directory separators from the full path
        assert "\\" not in data["root_display"]

    def test_root_absolute_path_not_in_file_paths(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """File relative_path values must not contain the absolute root path."""
        (authorized_root / "doc.txt").write_text("hello")

        response = client.post("/api/scan")
        data = response.json()
        root_str = str(authorized_root)
        for f in data["files"]:
            assert root_str not in f["relative_path"]

# ---------------------------------------------------------------------------
# Persistence Verification
# ---------------------------------------------------------------------------

class TestScanPersistence:
    def test_scan_persisted_to_database(
        self, authorized_root: Path, clean_service: FolderAccessService, clean_database: Path
    ):
        """A successful scan must be persisted to the database."""
        (authorized_root / "persisted_doc.txt").write_text("content")

        response = client.post("/api/scan")
        assert response.status_code == 200
        data = response.json()
        scan_id = data["scan_id"]

        from app.database.connection import get_connection

        with get_connection() as conn:
            # Check scan table
            cursor = conn.execute("SELECT * FROM scans WHERE id = ?", (scan_id,))
            scan_row = cursor.fetchone()
            assert scan_row is not None
            assert scan_row["file_count"] == 1
            
            # Check files table
            cursor = conn.execute("SELECT * FROM files WHERE scan_id = ?", (scan_id,))
            file_row = cursor.fetchone()
            assert file_row is not None
            assert file_row["name"] == "persisted_doc.txt"
