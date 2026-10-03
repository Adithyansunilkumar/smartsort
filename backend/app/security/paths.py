"""Reusable path validation and security boundary enforcement.

This module is the **single authority** for validating filesystem paths in SmartSort.
All validation must flow through these functions — no caller should implement its own
prefix checks, ``..`` detection, or root comparisons.

Security Principles
-------------------
- All paths are canonicalized and fully resolved before boundary evaluation.
- Prefix checks are strictly component-based (via ``pathlib.Path.is_relative_to``),
  preventing string-prefix confusion (e.g. ``C:\\data-other`` vs ``C:\\data``).
- Traversal components (``..``) are eliminated through canonical resolution.
- Symlinks and Windows directory junctions are resolved to their physical target before
  boundary verification, preventing junction/symlink escape.

  **Windows junction / reparse-point note:**  ``Path.resolve()`` resolves NTFS reparse
  points in Python 3.8+.  If an inaccessible reparse target or network volume prevents
  canonical resolution, validation will reject the path conservatively.

Validation Flow
---------------
The intended security pipeline for all future filesystem operations is::

    authorized root (set via FolderAccessService)
            ↓
    canonical path  (resolved by this module)
            ↓
    protected-path check  [placeholder — Phase 3]
            ↓
    operation permission

Future callers should obtain the authorized root via ``FolderAccessService`` and invoke
``validate_path_in_root()`` through the service's wrapper method, not directly, so that
the service remains the single point of truth.

Nonexistent-path mode
---------------------
``validate_path_in_root()`` supports an ``allow_nonexistent`` flag for operations that
need to validate a *future* path (e.g. a move destination) that does not yet exist on
disk.  When ``allow_nonexistent=True``:

* The candidate's **parent** must exist and resolve inside the root boundary.
* The candidate leaf name may be absent from the filesystem.
* Symlink/traversal escapes through the parent chain are still caught because the parent
  is fully resolved before the containment check.

This mode must **not** be used to validate paths that are required to exist (e.g. source
files for scanning).  Use the default ``allow_nonexistent=False`` for those.
"""

import os
from pathlib import Path
from typing import Union


class PathSecurityError(Exception):
    """Raised when a candidate path violates security boundaries.

    Attributes:
        code:    Machine-readable error identifier.
        message: Human-readable description.  Must not contain sensitive
                 absolute filesystem information that could be forwarded to
                 the frontend verbatim.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class PathValidationError(Exception):
    """Raised when a candidate root directory is invalid or inaccessible.

    Attributes:
        code:    Machine-readable error identifier.
        message: Human-readable description.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def normalize_and_validate_root(candidate_path: Union[str, Path]) -> Path:
    """Normalize and validate a candidate directory for authorized root access.

    This function is called **only** when establishing a new authorized root.
    It performs full filesystem verification: existence, directory type, and
    read access.  The returned path is canonical and ready to be stored by
    ``FolderAccessService``.

    Args:
        candidate_path: Path string or Path object supplied by the caller.

    Returns:
        Canonical, resolved :class:`~pathlib.Path` object.

    Raises:
        PathValidationError: With codes ``EMPTY_PATH``, ``INVALID_PATH``,
            ``PATH_NOT_FOUND``, ``NOT_A_DIRECTORY``, or ``ACCESS_DENIED``.
    """
    if not candidate_path:
        raise PathValidationError(
            code="EMPTY_PATH",
            message="Directory path cannot be empty.",
        )

    if isinstance(candidate_path, str):
        candidate_str = candidate_path.strip()
        if not candidate_str:
            raise PathValidationError(
                code="EMPTY_PATH",
                message="Directory path cannot be blank or whitespace.",
            )
        path_obj = Path(candidate_str)
    else:
        path_obj = candidate_path

    try:
        # Expand user home (~) and resolve symlinks / Windows junctions
        canonical_path = path_obj.expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as err:
        raise PathValidationError(
            code="INVALID_PATH",
            message=f"Path resolution failed: {err}",
        )

    if not canonical_path.exists():
        raise PathValidationError(
            code="PATH_NOT_FOUND",
            message="The specified directory does not exist or could not be found.",
        )

    if not canonical_path.is_dir():
        raise PathValidationError(
            code="NOT_A_DIRECTORY",
            message="The specified path exists but is a file, not a directory.",
        )

    # Verify read access
    try:
        if not os.access(canonical_path, os.R_OK):
            raise PathValidationError(
                code="ACCESS_DENIED",
                message="Read access is denied for the specified directory.",
            )
    except OSError:
        raise PathValidationError(
            code="ACCESS_DENIED",
            message="Could not verify directory access permissions.",
        )

    return canonical_path


def validate_path_in_root(
    candidate_path: Union[str, Path],
    authorized_root: Path,
    allow_root: bool = True,
    allow_nonexistent: bool = False,
) -> Path:
    """Verify that *candidate_path* safely resolves inside *authorized_root*.

    This is the central security gate for all path-based operations.
    Callers should prefer accessing this via ``FolderAccessService.validate_path_in_root()``
    so that the authorized root is never passed around manually.

    Prevents:
    - Path traversal (``..``) escaping the authorized root.
    - Absolute paths outside the authorized root.
    - Sibling directory prefix confusion (e.g. ``C:\\data-other`` vs ``C:\\data``).
    - Symlinks / junctions pointing outside the authorized root boundary.

    Nonexistent-path mode (``allow_nonexistent=True``):
    - The candidate's **parent** is resolved canonically and must be inside the root.
    - The candidate leaf itself may not exist.
    - Use this mode **only** for validating future destination paths (e.g. move targets).
    - Do **not** use it when the path must already exist (e.g. source files).

    Args:
        candidate_path:  Candidate path string or Path object to check.
        authorized_root: Canonical, resolved root directory (from FolderAccessService).
        allow_root:      Whether a candidate equal to the root itself is permitted.
                         Defaults to ``True``.  Set ``False`` for operations that must
                         target a descendant, not the root.
        allow_nonexistent: When ``True``, a candidate whose leaf does not yet exist is
                           accepted provided its parent resolves inside the root.
                           Defaults to ``False`` (strict: candidate must exist).

    Returns:
        Canonical resolved :class:`~pathlib.Path` of the candidate.

    Raises:
        PathValidationError: With code ``EMPTY_PATH`` if the candidate is blank.
        PathSecurityError: With codes:
            - ``ROOT_NOT_CONFIGURED`` — ``authorized_root`` is ``None`` or not a Path.
            - ``ROOT_RESOLUTION_ERROR`` — the stored root cannot be resolved.
            - ``CANONICALIZATION_FAILED`` — the candidate cannot be canonicalized.
            - ``ROOT_NOT_ALLOWED`` — candidate equals root and ``allow_root=False``.
            - ``PATH_OUTSIDE_ROOT`` — candidate resolves outside the authorized root.
    """
    if not candidate_path:
        raise PathValidationError(
            code="EMPTY_PATH",
            message="Candidate path cannot be empty.",
        )

    if not authorized_root or not isinstance(authorized_root, Path):
        raise PathSecurityError(
            code="ROOT_NOT_CONFIGURED",
            message="No valid authorized root is configured for path validation.",
        )

    try:
        canonical_root = authorized_root.resolve()
    except OSError as err:
        raise PathSecurityError(
            code="ROOT_RESOLUTION_ERROR",
            message=f"Failed to resolve authorized root: {err}",
        )

    if isinstance(candidate_path, str):
        candidate_path = Path(candidate_path.strip())

    # -------------------------------------------------------------------------
    # Nonexistent-path mode: resolve the parent, then reattach the leaf name.
    # The parent chain must exist and reside inside the root; the leaf may not.
    # -------------------------------------------------------------------------
    if allow_nonexistent:
        candidate_parent = candidate_path.parent
        candidate_leaf = candidate_path.name

        # Anchor relative paths to the root
        if not candidate_path.is_absolute():
            candidate_parent = canonical_root / candidate_parent

        try:
            canonical_parent = candidate_parent.expanduser().resolve()
        except (OSError, RuntimeError, ValueError) as err:
            raise PathSecurityError(
                code="CANONICALIZATION_FAILED",
                message=f"Failed to canonicalize candidate parent path: {err}",
            )

        if not canonical_parent.exists():
            # The parent itself does not exist — reject conservatively.
            raise PathSecurityError(
                code="PATH_OUTSIDE_ROOT",
                message="Access denied: Candidate parent path does not exist.",
            )

        # The leaf name must not be empty or a traversal component
        if not candidate_leaf or candidate_leaf in (".", ".."):
            raise PathSecurityError(
                code="PATH_OUTSIDE_ROOT",
                message="Access denied: Candidate path has an invalid leaf name.",
            )

        canonical_candidate = canonical_parent / candidate_leaf

        # Containment check on the resolved parent (leaf cannot affect boundary)
        if canonical_candidate == canonical_root:
            if allow_root:
                return canonical_candidate
            raise PathSecurityError(
                code="ROOT_NOT_ALLOWED",
                message="Candidate path cannot be the root directory itself for this operation.",
            )

        if not canonical_parent.is_relative_to(canonical_root):
            raise PathSecurityError(
                code="PATH_OUTSIDE_ROOT",
                message="Access denied: Candidate path resolves outside the authorized root directory.",
            )

        return canonical_candidate

    # -------------------------------------------------------------------------
    # Strict mode (default): candidate must exist and resolve fully inside root.
    # -------------------------------------------------------------------------
    try:
        if not candidate_path.is_absolute():
            candidate_full = canonical_root / candidate_path
        else:
            candidate_full = candidate_path

        canonical_candidate = candidate_full.expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as err:
        raise PathSecurityError(
            code="CANONICALIZATION_FAILED",
            message=f"Failed to canonicalize candidate path: {err}",
        )

    # Check equality with root
    if canonical_candidate == canonical_root:
        if allow_root:
            return canonical_candidate
        raise PathSecurityError(
            code="ROOT_NOT_ALLOWED",
            message="Candidate path cannot be the root directory itself for this operation.",
        )

    # Component-based hierarchy check — immune to string-prefix confusion
    if not canonical_candidate.is_relative_to(canonical_root):
        raise PathSecurityError(
            code="PATH_OUTSIDE_ROOT",
            message="Access denied: Candidate path resolves outside the authorized root directory.",
        )

    return canonical_candidate
