"""Unit tests for the filesystem scanner (app.filesystem.scanner).

These tests exercise the scanner service directly, without going through
the HTTP layer.  All tests use pytest ``tmp_path`` for isolated, temporary
directories.

The ``clean_service`` autouse fixture from conftest.py guarantees that the
authorized root is cleared before and after every test.

Test categories
---------------
- Empty root
- Files (metadata accuracy)
- Nested directories
- Deterministic ordering
- No authorized root
- Root boundary
- External symlink / junction (platform-gated)
- Broken symlink (platform-gated)
- Race condition / metadata failure (simulated)
- Read-only guarantee
"""

import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app.filesystem.scanner import (
    ScanResult,
    ScanStatus,
    ScannedDirectory,
    ScannedFile,
    run_scan,
)
from app.security.paths import PathSecurityError
from app.services.folder_access import FolderAccessService, get_folder_access_service


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_service_with_root(root: Path) -> FolderAccessService:
    """Return the singleton service configured to *root*."""
    service = get_folder_access_service()
    service.set_authorized_root(root.resolve())
    return service


# ---------------------------------------------------------------------------
# Empty root
# ---------------------------------------------------------------------------


class TestEmptyRoot:
    def test_empty_root_completes(self, authorized_root: Path, clean_service: FolderAccessService):
        """An empty authorized root produces a completed scan with zero entries."""
        result = run_scan(clean_service)

        assert isinstance(result, ScanResult)
        assert result.status == ScanStatus.COMPLETED
        assert result.file_count == 0
        assert result.directory_count == 0
        assert result.skip_count == 0
        assert result.files == []
        assert result.directories == []

    def test_empty_root_has_scan_id(self, authorized_root: Path, clean_service: FolderAccessService):
        """Every scan must have a non-empty scan_id."""
        result = run_scan(clean_service)
        assert result.scan_id
        assert len(result.scan_id) == 36  # UUID4 canonical form

    def test_empty_root_has_root_display(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """root_display must be the basename of the authorized root."""
        result = run_scan(clean_service)
        assert result.root_display == authorized_root.name

    def test_empty_root_has_scanned_at(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """scanned_at must be an ISO-8601 string."""
        result = run_scan(clean_service)
        assert "T" in result.scanned_at  # crude ISO-8601 check


# ---------------------------------------------------------------------------
# File metadata accuracy
# ---------------------------------------------------------------------------


class TestFileMetadata:
    def test_file_name(self, authorized_root: Path, clean_service: FolderAccessService):
        """Discovered file name must match the actual filename."""
        (authorized_root / "notes.txt").write_text("hello")
        result = run_scan(clean_service)
        assert result.file_count == 1
        assert result.files[0].name == "notes.txt"

    def test_file_relative_path(self, authorized_root: Path, clean_service: FolderAccessService):
        """Relative path must use forward slashes and exclude the root."""
        (authorized_root / "report.pdf").write_bytes(b"pdf content")
        result = run_scan(clean_service)
        assert result.files[0].relative_path == "report.pdf"
        # Must not be an absolute path
        assert not result.files[0].relative_path.startswith("/")
        assert ":" not in result.files[0].relative_path  # no Windows drive letter

    def test_file_extension_lowercase(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """Extension must be lowercase and include the leading dot."""
        (authorized_root / "IMAGE.PNG").write_bytes(b"\x89PNG")
        result = run_scan(clean_service)
        assert result.files[0].extension == ".png"

    def test_file_extension_none(self, authorized_root: Path, clean_service: FolderAccessService):
        """File with no extension must have empty-string extension."""
        (authorized_root / "makefile").write_text("all:")
        result = run_scan(clean_service)
        assert result.files[0].extension == ""

    def test_file_size(self, authorized_root: Path, clean_service: FolderAccessService):
        """Reported size_bytes must match the actual file size."""
        content = b"A" * 1234
        (authorized_root / "data.bin").write_bytes(content)
        result = run_scan(clean_service)
        assert result.files[0].size_bytes == 1234

    def test_file_size_zero(self, authorized_root: Path, clean_service: FolderAccessService):
        """An empty file must report size_bytes == 0."""
        (authorized_root / "empty.txt").write_bytes(b"")
        result = run_scan(clean_service)
        assert result.files[0].size_bytes == 0

    def test_file_modified_at_is_iso(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """modified_at must be a valid ISO-8601 UTC string."""
        (authorized_root / "doc.md").write_text("# doc")
        result = run_scan(clean_service)
        ts = result.files[0].modified_at
        assert "T" in ts
        assert ts.endswith("+00:00") or ts.endswith("Z")

    def test_file_modified_at_accuracy(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """modified_at must reflect the actual modification time (within 5 seconds)."""
        before = time.time()
        (authorized_root / "timed.txt").write_text("timed")
        result = run_scan(clean_service)
        after = time.time()

        from datetime import datetime, timezone

        reported_ts = datetime.fromisoformat(result.files[0].modified_at).timestamp()
        assert before - 1 <= reported_ts <= after + 1

    def test_mime_type_known(self, authorized_root: Path, clean_service: FolderAccessService):
        """MIME type must be returned for well-known extensions."""
        (authorized_root / "photo.jpg").write_bytes(b"\xff\xd8")
        result = run_scan(clean_service)
        assert result.files[0].mime_type is not None
        assert "image" in result.files[0].mime_type

    def test_mime_type_unknown(self, authorized_root: Path, clean_service: FolderAccessService):
        """MIME type may be None for unknown extensions."""
        (authorized_root / "weird.xyzabc123").write_bytes(b"data")
        result = run_scan(clean_service)
        # May be None or not — just verify the field exists without error
        _ = result.files[0].mime_type

    def test_multiple_files_counted(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """file_count must equal the number of discovered files."""
        for i in range(5):
            (authorized_root / f"file{i}.txt").write_text(f"content {i}")
        result = run_scan(clean_service)
        assert result.file_count == 5
        assert len(result.files) == 5


# ---------------------------------------------------------------------------
# Nested directories
# ---------------------------------------------------------------------------


class TestNestedDirectories:
    def test_nested_file_relative_path(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """Deeply nested file must have the correct forward-slash relative path."""
        nested = authorized_root / "College" / "Java"
        nested.mkdir(parents=True)
        (nested / "notes.txt").write_text("java notes")

        result = run_scan(clean_service)

        file_paths = [f.relative_path for f in result.files]
        assert "College/Java/notes.txt" in file_paths

    def test_nested_directory_relative_path(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """Nested directories must appear with correct relative paths."""
        (authorized_root / "College" / "Java").mkdir(parents=True)

        result = run_scan(clean_service)

        dir_paths = [d.relative_path for d in result.directories]
        assert "College" in dir_paths
        assert "College/Java" in dir_paths

    def test_directory_count_excludes_root(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """directory_count must not include the authorized root itself."""
        (authorized_root / "sub1").mkdir()
        (authorized_root / "sub2").mkdir()

        result = run_scan(clean_service)
        assert result.directory_count == 2

    def test_complex_tree(self, authorized_root: Path, clean_service: FolderAccessService):
        """Complex tree produces correct file and directory counts."""
        (authorized_root / "a").mkdir()
        (authorized_root / "a" / "b").mkdir()
        (authorized_root / "a" / "b" / "deep.txt").write_text("deep")
        (authorized_root / "c.txt").write_text("c")
        (authorized_root / "d.md").write_text("d")

        result = run_scan(clean_service)

        assert result.directory_count == 2  # a, a/b
        assert result.file_count == 3  # a/b/deep.txt, c.txt, d.md


# ---------------------------------------------------------------------------
# Deterministic ordering
# ---------------------------------------------------------------------------


class TestDeterministicOrdering:
    def test_directories_before_files(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """The result must list directories separately from files (not interleaved)."""
        (authorized_root / "z_dir").mkdir()
        (authorized_root / "a_file.txt").write_text("a")

        result = run_scan(clean_service)

        # The result model has separate lists, so this is structurally guaranteed.
        # Verify both are present.
        assert len(result.directories) >= 1
        assert len(result.files) >= 1

    def test_files_alphabetical(self, authorized_root: Path, clean_service: FolderAccessService):
        """Files must be sorted alphabetically by relative_path (case-folded)."""
        (authorized_root / "zebra.txt").write_text("z")
        (authorized_root / "alpha.txt").write_text("a")
        (authorized_root / "Mango.txt").write_text("m")  # uppercase M

        result = run_scan(clean_service)

        paths = [f.relative_path for f in result.files]
        assert paths == sorted(paths, key=str.lower)

    def test_directories_alphabetical(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """Directories must be sorted alphabetically by relative_path (case-folded)."""
        (authorized_root / "Zeta").mkdir()
        (authorized_root / "alpha").mkdir()
        (authorized_root / "Beta").mkdir()

        result = run_scan(clean_service)

        paths = [d.relative_path for d in result.directories]
        assert paths == sorted(paths, key=str.lower)

    def test_stable_across_two_scans(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """Two consecutive scans of the same tree must produce identical ordering."""
        (authorized_root / "b").mkdir()
        (authorized_root / "a").mkdir()
        (authorized_root / "a" / "x.txt").write_text("x")
        (authorized_root / "a" / "y.txt").write_text("y")
        (authorized_root / "z.txt").write_text("z")

        result1 = run_scan(clean_service)
        result2 = run_scan(clean_service)

        assert [f.relative_path for f in result1.files] == [
            f.relative_path for f in result2.files
        ]
        assert [d.relative_path for d in result1.directories] == [
            d.relative_path for d in result2.directories
        ]


# ---------------------------------------------------------------------------
# No authorized root
# ---------------------------------------------------------------------------


class TestNoAuthorizedRoot:
    def test_run_scan_without_root_raises(self, clean_service: FolderAccessService):
        """run_scan must raise PathSecurityError(ROOT_NOT_CONFIGURED) when no root is set."""
        with pytest.raises(PathSecurityError) as exc_info:
            run_scan(clean_service)
        assert exc_info.value.code == "ROOT_NOT_CONFIGURED"


# ---------------------------------------------------------------------------
# Root boundary — only entries inside root are returned
# ---------------------------------------------------------------------------


class TestRootBoundary:
    def test_only_root_descendants_returned(
        self, authorized_root: Path, clean_service: FolderAccessService, tmp_path: Path
    ):
        """Files outside the authorized root must not appear in results."""
        # File inside root
        (authorized_root / "inside.txt").write_text("inside")

        # File outside root (sibling of the root's parent)
        outside_file = tmp_path / "outside.txt"
        outside_file.write_text("outside")

        result = run_scan(clean_service)

        file_paths = [f.relative_path for f in result.files]
        assert "inside.txt" in file_paths
        assert all("outside" not in p for p in file_paths)

    def test_scan_id_unique_per_scan(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """Each scan must produce a different scan_id."""
        r1 = run_scan(clean_service)
        r2 = run_scan(clean_service)
        assert r1.scan_id != r2.scan_id


# ---------------------------------------------------------------------------
# Symlink / junction — platform-gated
# ---------------------------------------------------------------------------


def test_external_symlink_not_traversed(
    authorized_root: Path, clean_service: FolderAccessService, tmp_path: Path
):
    """A symlink inside root pointing outside must not be traversed.

    Creates an external target with a sentinel file, creates a symlink inside
    the root pointing at it, and verifies the sentinel file does NOT appear in
    the scan results.
    """
    # External target with a sentinel file
    outside = tmp_path / "outside_target"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("i am outside")

    # Symlink inside the authorized root pointing to the external target
    link_path = authorized_root / "escape_link"
    try:
        link_path.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip(
            "Symlink creation requires elevated privileges or Developer Mode on Windows. "
            "Security property holds: symlinks that cannot be created cannot be used "
            "to escape the authorized root."
        )

    result = run_scan(clean_service)

    file_paths = [f.relative_path for f in result.files]
    dir_paths = [d.relative_path for d in result.directories]
    skip_reasons = [s.reason for s in result.skips]

    # Sentinel file must NOT be in results
    assert not any("sentinel" in p for p in file_paths)
    # The link itself must NOT appear as a directory entry
    assert "escape_link" not in dir_paths
    # It must be recorded as a SYMLINK skip
    assert "SYMLINK" in skip_reasons


def test_broken_symlink_does_not_crash(
    authorized_root: Path, clean_service: FolderAccessService
):
    """A broken symlink inside root must be recorded as a skip; scan completes."""
    broken_link = authorized_root / "broken_link.txt"
    try:
        broken_link.symlink_to(authorized_root / "nonexistent_target.txt")
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation not permitted on this system")

    # Place a valid file too so we know the scan continued
    (authorized_root / "valid.txt").write_text("valid")

    result = run_scan(clean_service)

    assert result.status == ScanStatus.COMPLETED
    # The broken link should be skipped (it is_symlink() → SYMLINK reason)
    skip_reasons = [s.reason for s in result.skips]
    assert "SYMLINK" in skip_reasons
    # The valid file must still be discovered
    assert any(f.name == "valid.txt" for f in result.files)


# ---------------------------------------------------------------------------
# Race condition / metadata failure simulation
# ---------------------------------------------------------------------------


class TestRaceCondition:
    def test_stat_failure_is_skipped_gracefully(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """If stat() raises OSError for a file, the scan should skip it and continue."""
        (authorized_root / "good.txt").write_text("good content")
        (authorized_root / "bad.txt").write_text("bad content — will fail stat")

        # Patch os.scandir so that stat on "bad.txt" raises OSError
        real_scandir = __import__("os").scandir

        class FakeDirEntry:
            """Wraps a real DirEntry but makes stat raise for the target file."""

            def __init__(self, real_entry):
                self._real = real_entry
                self.name = real_entry.name
                self.path = real_entry.path

            def is_symlink(self):
                return self._real.is_symlink()

            def is_dir(self, follow_symlinks=True):
                return self._real.is_dir(follow_symlinks=follow_symlinks)

            def is_file(self, follow_symlinks=True):
                return self._real.is_file(follow_symlinks=follow_symlinks)

            def stat(self, follow_symlinks=True):
                if self.name == "bad.txt":
                    raise OSError("simulated stat failure")
                return self._real.stat(follow_symlinks=follow_symlinks)

        def patched_scandir(path):
            return [FakeDirEntry(e) for e in real_scandir(path)]

        with patch("app.filesystem.scanner.os.scandir", side_effect=patched_scandir):
            result = run_scan(clean_service)

        assert result.status == ScanStatus.COMPLETED
        file_names = [f.name for f in result.files]
        assert "good.txt" in file_names
        assert "bad.txt" not in file_names
        assert result.skip_count >= 1


# ---------------------------------------------------------------------------
# Read-only guarantee
# ---------------------------------------------------------------------------


class TestReadOnly:
    def test_scan_does_not_mutate_fixture(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """After scanning, the fixture directory structure must be unchanged."""
        # Build a known fixture
        sub = authorized_root / "documents"
        sub.mkdir()
        f1 = authorized_root / "readme.txt"
        f1.write_text("readme content")
        f2 = sub / "report.pdf"
        f2.write_bytes(b"pdf")

        # Record state before scan
        def snapshot(root: Path):
            entries = set()
            for p in root.rglob("*"):
                entries.add((str(p.relative_to(root)), p.is_dir(), p.stat().st_size))
            return entries

        before = snapshot(authorized_root)

        run_scan(clean_service)

        after = snapshot(authorized_root)
        assert before == after, "Scan must not modify the filesystem"
