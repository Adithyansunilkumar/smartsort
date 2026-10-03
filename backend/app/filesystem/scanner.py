"""Recursive filesystem scanner for SmartSort Phase 2.

This module is the **sole** implementation of filesystem discovery.
It must not be called with arbitrary paths from the frontend; the scan
root is always obtained from ``FolderAccessService.require_authorized_root()``.

Security contract
-----------------
* The authorized root is fetched via ``service.require_authorized_root()`` —
  never supplied by the caller.
* Symlinks and directory junctions are **never** followed during traversal.
  Each entry is checked with ``entry.is_symlink()`` before any further
  inspection; symlinks are recorded as ``ScanSkip`` entries and skipped.
* All ``stat()`` calls use ``follow_symlinks=False`` to avoid accidentally
  following a link to retrieve metadata.
* Race conditions (files disappearing between ``scandir`` and ``stat``) are
  caught and recorded as ``ScanSkip`` entries.  The scan continues.
* No file contents are read.
* No hashes are calculated.
* No files or directories are created, renamed, moved, or deleted.

Result ordering
---------------
Results are returned in a deterministic order:

1. Directories before files.
2. Alphabetically by ``relative_path`` (case-folded for cross-platform
   reproducibility) within each category.
"""

import mimetypes
import os
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import List, Optional

from pydantic import BaseModel, Field

from app.services.folder_access import FolderAccessService


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class ScanStatus(str, Enum):
    """Outcome of a completed scan operation."""

    COMPLETED = "completed"
    FAILED = "failed"


class ScannedFile(BaseModel):
    """Metadata for a single discovered file."""

    name: str = Field(..., description="Filename including extension")
    relative_path: str = Field(
        ...,
        description="Forward-slash path relative to the authorized root, e.g. 'College/Java/notes.pdf'",
    )
    extension: str = Field(
        ...,
        description="Lowercase file extension including leading dot, e.g. '.pdf'. Empty string if none.",
    )
    size_bytes: int = Field(..., description="File size in bytes")
    modified_at: str = Field(..., description="Last-modified timestamp in ISO-8601 UTC format")
    mime_type: Optional[str] = Field(
        default=None,
        description="Best-guess MIME type from stdlib mimetypes (extension-based). None if unknown.",
    )


class ScannedDirectory(BaseModel):
    """Metadata for a single discovered directory."""

    name: str = Field(..., description="Directory name (basename)")
    relative_path: str = Field(
        ...,
        description="Forward-slash path relative to the authorized root, e.g. 'College/Java'",
    )


class ScanSkip(BaseModel):
    """Record of an entry that was skipped or could not be processed."""

    relative_path: str = Field(
        ..., description="Forward-slash path relative to the authorized root"
    )
    reason: str = Field(
        ...,
        description=(
            "Short machine-readable reason code: SYMLINK, PERMISSION_DENIED, "
            "DISAPPEARED, STAT_FAILED, SECURITY_VIOLATION"
        ),
    )
    detail: Optional[str] = Field(
        default=None, description="Optional human-readable additional context"
    )


class ScanResult(BaseModel):
    """Complete result of a single filesystem scan."""

    scan_id: str = Field(..., description="UUID4 uniquely identifying this scan")
    status: ScanStatus = Field(..., description="Overall outcome of the scan")
    root_display: str = Field(
        ...,
        description="Display name of the scanned root (basename only — not the full absolute path)",
    )
    file_count: int = Field(..., description="Number of files discovered")
    directory_count: int = Field(
        ..., description="Number of descendant directories discovered (excludes the root itself)"
    )
    skip_count: int = Field(..., description="Number of entries skipped or that produced errors")
    scanned_at: str = Field(..., description="ISO-8601 UTC timestamp when the scan was initiated")
    files: List[ScannedFile] = Field(default_factory=list)
    directories: List[ScannedDirectory] = Field(default_factory=list)
    skips: List[ScanSkip] = Field(default_factory=list)
    error: Optional[str] = Field(
        default=None,
        description="Top-level error message when status is FAILED. Not a stack trace.",
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_relative_posix(path: Path, root: Path) -> str:
    """Return a forward-slash relative path string for UI consumption.

    Uses ``PurePosixPath`` to normalise separators regardless of OS.
    """
    try:
        rel = path.relative_to(root)
    except ValueError:
        # Should not happen — callers guarantee path is inside root.
        # Return a safe fallback to avoid crashing the scan.
        return path.name
    return str(PurePosixPath(rel))


def _utc_iso(timestamp: float) -> str:
    """Convert a POSIX timestamp (float) to an ISO-8601 UTC string."""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _guess_mime(name: str) -> Optional[str]:
    """Return a MIME type string using stdlib ``mimetypes``, or None."""
    mime, _ = mimetypes.guess_type(name, strict=False)
    return mime


# ---------------------------------------------------------------------------
# Recursive traversal
# ---------------------------------------------------------------------------


def _scan_directory(
    directory: Path,
    root: Path,
    files_out: List[ScannedFile],
    dirs_out: List[ScannedDirectory],
    skips_out: List[ScanSkip],
) -> None:
    """Recursively scan *directory*, appending results to the output lists.

    Security rules enforced here:
    - Symlinks are detected via ``entry.is_symlink()`` and skipped unconditionally.
    - ``is_dir`` and ``is_file`` are called with ``follow_symlinks=False``.
    - ``stat`` is called with ``follow_symlinks=False``.
    - Each OS call is individually guarded for race conditions.
    """
    try:
        entries = list(os.scandir(directory))
    except PermissionError as err:
        skips_out.append(
            ScanSkip(
                relative_path=_to_relative_posix(directory, root),
                reason="PERMISSION_DENIED",
                detail=str(err),
            )
        )
        return
    except OSError as err:
        skips_out.append(
            ScanSkip(
                relative_path=_to_relative_posix(directory, root),
                reason="DISAPPEARED",
                detail=str(err),
            )
        )
        return

    for entry in entries:
        entry_path = Path(entry.path)
        rel_posix = _to_relative_posix(entry_path, root)

        # ------------------------------------------------------------------
        # 1. Symlink check — must come BEFORE is_dir / is_file checks.
        #    is_symlink() never follows the link, so this is safe.
        # ------------------------------------------------------------------
        try:
            is_link = entry.is_symlink()
        except OSError as err:
            skips_out.append(
                ScanSkip(relative_path=rel_posix, reason="STAT_FAILED", detail=str(err))
            )
            continue

        if is_link:
            skips_out.append(
                ScanSkip(
                    relative_path=rel_posix,
                    reason="SYMLINK",
                    detail="Symlink or junction skipped to prevent authorized-root escape",
                )
            )
            continue

        # ------------------------------------------------------------------
        # 2. Determine type using follow_symlinks=False (link already ruled out).
        # ------------------------------------------------------------------
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
            is_file = entry.is_file(follow_symlinks=False)
        except OSError as err:
            skips_out.append(
                ScanSkip(relative_path=rel_posix, reason="STAT_FAILED", detail=str(err))
            )
            continue

        # ------------------------------------------------------------------
        # 3. Process directory.
        # ------------------------------------------------------------------
        if is_dir:
            dirs_out.append(
                ScannedDirectory(
                    name=entry.name,
                    relative_path=rel_posix,
                )
            )
            _scan_directory(entry_path, root, files_out, dirs_out, skips_out)
            continue

        # ------------------------------------------------------------------
        # 4. Process file.
        # ------------------------------------------------------------------
        if is_file:
            try:
                stat_result = entry.stat(follow_symlinks=False)
            except PermissionError as err:
                skips_out.append(
                    ScanSkip(
                        relative_path=rel_posix,
                        reason="PERMISSION_DENIED",
                        detail=str(err),
                    )
                )
                continue
            except OSError as err:
                # File may have disappeared between scandir and stat (race condition)
                skips_out.append(
                    ScanSkip(relative_path=rel_posix, reason="DISAPPEARED", detail=str(err))
                )
                continue

            suffix = Path(entry.name).suffix.lower()
            files_out.append(
                ScannedFile(
                    name=entry.name,
                    relative_path=rel_posix,
                    extension=suffix,
                    size_bytes=stat_result.st_size,
                    modified_at=_utc_iso(stat_result.st_mtime),
                    mime_type=_guess_mime(entry.name),
                )
            )
            continue

        # ------------------------------------------------------------------
        # 5. Neither a regular file nor a directory (device file, socket, etc.).
        #    Skip silently with a record.
        # ------------------------------------------------------------------
        skips_out.append(
            ScanSkip(
                relative_path=rel_posix,
                reason="STAT_FAILED",
                detail="Entry is neither a regular file nor a directory",
            )
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_scan(service: FolderAccessService) -> ScanResult:
    """Execute a recursive scan of the configured authorized root.

    This is the only public entry point.  It never accepts an arbitrary path
    from the caller — the root is always obtained from
    ``service.require_authorized_root()``.

    Args:
        service: The process-scoped ``FolderAccessService`` singleton.

    Returns:
        A ``ScanResult`` with status ``COMPLETED`` on success or ``FAILED``
        on a catastrophic error that prevents any scanning.

    Raises:
        ``PathSecurityError`` with code ``ROOT_NOT_CONFIGURED`` if no root
        is established.  Callers (API layer) should convert this to a
        structured HTTP 409 response.
    """
    scan_id = str(uuid.uuid4())
    scanned_at = datetime.now(tz=timezone.utc).isoformat()

    # Raises PathSecurityError(ROOT_NOT_CONFIGURED) if unconfigured.
    # Let it propagate — the API layer handles it.
    root: Path = service.require_authorized_root()

    root_display = root.name  # basename only — no absolute path in results

    files: List[ScannedFile] = []
    dirs: List[ScannedDirectory] = []
    skips: List[ScanSkip] = []

    try:
        _scan_directory(root, root, files, dirs, skips)
    except Exception as err:  # noqa: BLE001
        # Catastrophic failure — return a structured FAILED result rather than
        # letting an unhandled exception propagate to the HTTP layer.
        return ScanResult(
            scan_id=scan_id,
            status=ScanStatus.FAILED,
            root_display=root_display,
            file_count=0,
            directory_count=0,
            skip_count=0,
            scanned_at=scanned_at,
            files=[],
            directories=[],
            skips=[],
            error=f"Scan failed unexpectedly: {type(err).__name__}",
        )

    # ------------------------------------------------------------------
    # Sort results deterministically:
    #   1. Directories alphabetically by relative_path (case-folded).
    #   2. Files alphabetically by relative_path (case-folded).
    # ------------------------------------------------------------------
    dirs.sort(key=lambda d: d.relative_path.lower())
    files.sort(key=lambda f: f.relative_path.lower())

    return ScanResult(
        scan_id=scan_id,
        status=ScanStatus.COMPLETED,
        root_display=root_display,
        file_count=len(files),
        directory_count=len(dirs),
        skip_count=len(skips),
        scanned_at=scanned_at,
        files=files,
        directories=dirs,
        skips=skips,
        error=None,
    )
