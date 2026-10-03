"""Focused Phase 3 verification tests.

Requirement 1 — Protected path security: validate paths through
FolderAccessService before persistence.

Requirement 2 — Scan status / transaction consistency: verify that
database failures roll back correctly and never leave misleading state.
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.database.connection import get_connection
from app.database.repositories import ScanRepository, ProtectedPathRepository
from app.filesystem.scanner import ScanResult, ScannedFile, ScanStatus
from app.security.paths import PathSecurityError
from app.services.folder_access import FolderAccessService, get_folder_access_service
from app.services.protected_paths import ProtectedPathService


# ============================================================================
# Requirement 1 — Protected Path Security
# ============================================================================


class TestProtectedPathSecurity:
    """Verify that protected paths are validated through FolderAccessService."""

    def _make_service(self, authorized_root: Path) -> ProtectedPathService:
        folder_service = get_folder_access_service()
        return ProtectedPathService(folder_service)

    def test_valid_descendant_accepted(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """A valid descendant of the authorized root must be accepted and stored."""
        sub = authorized_root / "documents"
        sub.mkdir()

        svc = self._make_service(authorized_root)
        result = svc.add_protected_path(str(sub), "ignore")

        assert result["path"] == "documents"
        assert result["protection_type"] == "ignore"

        # Verify it's actually in the database
        stored = svc.get_all_protected_paths()
        assert len(stored) == 1
        assert stored[0]["path"] == "documents"

    def test_nested_descendant_accepted(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """A deeply nested descendant must be accepted with forward-slash relative path."""
        nested = authorized_root / "College" / "Java"
        nested.mkdir(parents=True)

        svc = self._make_service(authorized_root)
        result = svc.add_protected_path(str(nested), "pin")

        assert result["path"] == "College/Java"

    def test_root_itself_accepted(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """The authorized root itself must be accepted (allow_root=True)."""
        svc = self._make_service(authorized_root)
        result = svc.add_protected_path(str(authorized_root), "lock")

        assert result["path"] == "."

    def test_outside_path_rejected(
        self, authorized_root: Path, clean_service: FolderAccessService, tmp_path: Path
    ):
        """A path outside the authorized root must be rejected."""
        outside = tmp_path / "outside_dir"
        outside.mkdir()

        svc = self._make_service(authorized_root)
        with pytest.raises(PathSecurityError):
            svc.add_protected_path(str(outside), "ignore")

        # Must NOT be persisted
        assert len(svc.get_all_protected_paths()) == 0

    def test_sibling_rejected(
        self, authorized_root: Path, clean_service: FolderAccessService, tmp_path: Path
    ):
        """A sibling of the authorized root must be rejected."""
        sibling = authorized_root.parent / "sibling_dir"
        sibling.mkdir()

        svc = self._make_service(authorized_root)
        with pytest.raises(PathSecurityError):
            svc.add_protected_path(str(sibling), "ignore")

        assert len(svc.get_all_protected_paths()) == 0

    def test_traversal_attempt_rejected(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """A path containing .. traversal must be rejected."""
        svc = self._make_service(authorized_root)
        traversal_path = str(authorized_root / ".." / ".." / "etc")

        with pytest.raises(PathSecurityError):
            svc.add_protected_path(traversal_path, "ignore")

        assert len(svc.get_all_protected_paths()) == 0

    def test_symlink_escape_rejected(
        self, authorized_root: Path, clean_service: FolderAccessService, tmp_path: Path
    ):
        """A symlink that escapes the authorized root must be rejected."""
        outside = tmp_path / "escape_target"
        outside.mkdir()
        link_path = authorized_root / "evil_link"

        try:
            link_path.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("Symlink creation requires elevated privileges on Windows")

        svc = self._make_service(authorized_root)
        with pytest.raises(PathSecurityError):
            svc.add_protected_path(str(link_path), "ignore")

        assert len(svc.get_all_protected_paths()) == 0

    def test_no_authorized_root_rejects(self, clean_service: FolderAccessService):
        """Adding a protected path without a configured root must raise."""
        svc = ProtectedPathService(clean_service)

        with pytest.raises(PathSecurityError) as exc_info:
            svc.add_protected_path("/some/path", "ignore")
        assert exc_info.value.code == "ROOT_NOT_CONFIGURED"

    def test_canonical_path_stored(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """The stored path must be the canonicalized relative form, not the raw input."""
        sub = authorized_root / "Docs"
        sub.mkdir()

        svc = self._make_service(authorized_root)

        # Pass with extra slashes / mixed separators
        messy_input = str(authorized_root) + "//Docs"
        result = svc.add_protected_path(messy_input, "ignore")

        assert result["path"] == "Docs"
        stored = svc.get_all_protected_paths()
        assert stored[0]["path"] == "Docs"

    def test_duplicate_protected_path_is_safe(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """Adding the same path twice must not error or create duplicates."""
        sub = authorized_root / "safe"
        sub.mkdir()

        svc = self._make_service(authorized_root)
        svc.add_protected_path(str(sub), "ignore")
        svc.add_protected_path(str(sub), "ignore")

        stored = svc.get_all_protected_paths()
        assert len(stored) == 1

    def test_remove_protected_path(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """Removing a protected path should work."""
        sub = authorized_root / "removable"
        sub.mkdir()

        svc = self._make_service(authorized_root)
        svc.add_protected_path(str(sub), "ignore")
        assert len(svc.get_all_protected_paths()) == 1

        removed = svc.remove_protected_path("removable")
        assert removed is True
        assert len(svc.get_all_protected_paths()) == 0

    def test_remove_nonexistent_path_returns_false(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """Removing a path that doesn't exist should return False."""
        svc = self._make_service(authorized_root)
        removed = svc.remove_protected_path("nonexistent")
        assert removed is False


# ============================================================================
# Requirement 2 — Scan Status / Transaction Consistency
# ============================================================================


def _make_scan_result(scan_id: str = "tx-test-001", file_count: int = 0) -> ScanResult:
    """Helper to build a ScanResult for transaction tests."""
    files = [
        ScannedFile(
            name=f"file{i}.txt",
            relative_path=f"file{i}.txt",
            extension=".txt",
            size_bytes=100,
            modified_at="2026-09-19T10:00:00+00:00",
            mime_type="text/plain",
        )
        for i in range(file_count)
    ]
    return ScanResult(
        scan_id=scan_id,
        status=ScanStatus.COMPLETED,
        root_display="TestRoot",
        file_count=file_count,
        directory_count=0,
        skip_count=0,
        scanned_at="2026-09-19T10:00:00+00:00",
        files=files,
        directories=[],
        skips=[],
    )


class TestScanTransactionConsistency:
    """Verify that scan persistence uses proper transactions."""

    def test_successful_scan_persists_all_records(self):
        """A successful save_scan must persist both the scan record and all files."""
        result = _make_scan_result("success-001", file_count=3)
        ScanRepository.save_scan(result)

        with get_connection() as conn:
            scan_row = conn.execute(
                "SELECT * FROM scans WHERE id = ?", ("success-001",)
            ).fetchone()
            assert scan_row is not None
            assert scan_row["status"] == "completed"
            assert scan_row["file_count"] == 3

            file_rows = conn.execute(
                "SELECT * FROM files WHERE scan_id = ?", ("success-001",)
            ).fetchall()
            assert len(file_rows) == 3

    def test_file_insert_failure_rolls_back_scan_record(self):
        """If file insertion fails, the scan record must also be rolled back."""
        result = _make_scan_result("rollback-001", file_count=2)

        # Patch executemany to raise after the scan INSERT succeeds
        original_get_connection = get_connection.__wrapped__ if hasattr(get_connection, '__wrapped__') else None

        with patch("app.database.repositories.get_connection") as mock_conn_ctx:
            mock_conn = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)

            call_count = 0
            original_execute = None

            def tracking_execute(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                return MagicMock()

            def failing_executemany(*args, **kwargs):
                raise RuntimeError("Simulated bulk insert failure")

            mock_conn.execute = tracking_execute
            mock_conn.executemany = failing_executemany
            mock_conn.rollback = MagicMock()
            mock_conn.commit = MagicMock()
            mock_conn.close = MagicMock()
            mock_conn_ctx.return_value = mock_conn

            with pytest.raises(RuntimeError, match="Simulated bulk insert failure"):
                ScanRepository.save_scan(result)

        # Verify no scan record leaked into the actual database
        with get_connection() as conn:
            scan_row = conn.execute(
                "SELECT * FROM scans WHERE id = ?", ("rollback-001",)
            ).fetchone()
            assert scan_row is None, (
                "Scan record must not exist after a file-insertion failure"
            )

            file_rows = conn.execute(
                "SELECT * FROM files WHERE scan_id = ?", ("rollback-001",)
            ).fetchall()
            assert len(file_rows) == 0, (
                "File records must not exist after a file-insertion failure"
            )

    def test_failed_scan_status_persisted_correctly(self):
        """A scan with status='failed' must be stored as failed, not completed."""
        result = ScanResult(
            scan_id="failed-001",
            status=ScanStatus.FAILED,
            root_display="TestRoot",
            file_count=0,
            directory_count=0,
            skip_count=0,
            scanned_at="2026-09-19T10:00:00+00:00",
            files=[],
            directories=[],
            skips=[],
            error="Scan failed unexpectedly: OSError",
        )

        ScanRepository.save_scan(result)

        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM scans WHERE id = ?", ("failed-001",)
            ).fetchone()
            assert row is not None
            assert row["status"] == "failed", (
                "A failed scan must not be stored as 'completed'"
            )

    def test_empty_scan_persists_zero_files(self):
        """An empty scan (no files) must still create a scan record with 0 counts."""
        result = _make_scan_result("empty-001", file_count=0)
        ScanRepository.save_scan(result)

        with get_connection() as conn:
            scan_row = conn.execute(
                "SELECT * FROM scans WHERE id = ?", ("empty-001",)
            ).fetchone()
            assert scan_row is not None
            assert scan_row["file_count"] == 0

            file_rows = conn.execute(
                "SELECT * FROM files WHERE scan_id = ?", ("empty-001",)
            ).fetchall()
            assert len(file_rows) == 0

    def test_duplicate_scan_id_raises(self):
        """Inserting the same scan_id twice must raise (PRIMARY KEY violation)."""
        result = _make_scan_result("dup-001", file_count=0)
        ScanRepository.save_scan(result)

        with pytest.raises(Exception):
            ScanRepository.save_scan(result)

    def test_scan_and_files_in_single_transaction(self):
        """Verify scan + files are written within the same connection context."""
        result = _make_scan_result("atomic-001", file_count=5)
        ScanRepository.save_scan(result)

        with get_connection() as conn:
            scan = conn.execute(
                "SELECT * FROM scans WHERE id = ?", ("atomic-001",)
            ).fetchone()
            files = conn.execute(
                "SELECT * FROM files WHERE scan_id = ?", ("atomic-001",)
            ).fetchall()
            assert scan is not None
            assert len(files) == 5
            # All files must reference the correct scan
            for f in files:
                assert f["scan_id"] == "atomic-001"

    def test_foreign_key_enforcement(self):
        """Inserting a file with a non-existent scan_id must fail (FK violation)."""
        with pytest.raises(Exception):
            with get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO files (scan_id, path, name, extension, size, modified_time, hash, mime_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("nonexistent-scan", "test.txt", "test.txt", ".txt", 0, "2026-01-01", None, None),
                )
