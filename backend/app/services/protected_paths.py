"""Service layer for protected path operations.

This module enforces the Phase 1 security contract before persisting
protected paths.  The repository layer handles SQL; this service handles
validation through ``FolderAccessService``.

Protected paths MUST be validated through this service rather than
calling ``ProtectedPathRepository`` directly.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from app.database.repositories import ProtectedPathRepository
from app.services.folder_access import FolderAccessService


class ProtectedPathService:
    """Validates protected paths through FolderAccessService before persistence.

    This service is the ONLY approved entry point for adding protected paths.
    Direct use of ``ProtectedPathRepository.add_protected_path()`` bypasses
    security validation and must never be called from API/application code.
    """

    def __init__(self, folder_service: FolderAccessService) -> None:
        self._folder_service = folder_service

    def add_protected_path(self, candidate_path: str, protection_type: str) -> Dict[str, Any]:
        """Validate and persist a protected path.

        The candidate is validated against the currently authorized root
        using ``FolderAccessService.validate_path_in_root()``.  This ensures:

        - The authorized root must be configured.
        - The path must be a canonical descendant of the root (or the root itself).
        - Traversal attacks (``..``) are rejected.
        - Sibling/prefix collisions are rejected.
        - Symlink escapes are rejected.

        Args:
            candidate_path: The filesystem path to protect.  May be absolute
                or relative; it is canonicalized through the security service.
            protection_type: The type of protection (e.g. ``"ignore"``,
                ``"pin"``, ``"lock"``).

        Returns:
            A dict with ``path`` (the canonical string stored) and
            ``protection_type``.

        Raises:
            PathSecurityError: If the path fails any security check.
        """
        # This call enforces:
        #   1. Root must be configured  (ROOT_NOT_CONFIGURED)
        #   2. Path must exist          (PATH_NOT_FOUND)
        #   3. Path must be inside root (OUTSIDE_ROOT / TRAVERSAL_DETECTED)
        #   4. Symlink escapes blocked  (SYMLINK_ESCAPE)
        canonical: Path = self._folder_service.validate_path_in_root(
            candidate_path, allow_root=True
        )

        # Convert to the relative posix form used in the database
        root = self._folder_service.require_authorized_root()
        relative = canonical.relative_to(root)
        # Use forward slashes for cross-platform consistency
        from pathlib import PurePosixPath

        stored_path = str(PurePosixPath(relative)) if str(relative) != "." else "."

        created_at = datetime.now(tz=timezone.utc).isoformat()

        ProtectedPathRepository.add_protected_path(stored_path, protection_type, created_at)

        return {"path": stored_path, "protection_type": protection_type}

    def get_all_protected_paths(self) -> List[Dict[str, Any]]:
        """Retrieve all protected paths from the database."""
        return ProtectedPathRepository.get_all_protected_paths()

    def is_protected(self, candidate_path: str) -> bool:
        """Check if a path is equal to or a descendant of any protected path.
        Uses canonical component-based path matching, not string prefixes.
        """
        try:
            canonical: Path = self._folder_service.validate_path_in_root(
                candidate_path, allow_root=True
            )
        except Exception:
            return True  # If validation fails, safely block it

        root = self._folder_service.require_authorized_root()
        try:
            relative = canonical.relative_to(root)
        except ValueError:
            return True
            
        from pathlib import PurePosixPath
        candidate_parts = PurePosixPath(relative).parts
        if not candidate_parts:
            candidate_parts = ('.',)
            
        all_protected = self.get_all_protected_paths()
        for p in all_protected:
            prot_str = p.get("path", "")
            prot_parts = PurePosixPath(prot_str).parts
            if not prot_parts:
                prot_parts = ('.',)
                
            if prot_parts == ('.',):
                return True
                
            if len(candidate_parts) >= len(prot_parts) and candidate_parts[:len(prot_parts)] == prot_parts:
                return True
                
        return False

    def remove_protected_path(self, path: str) -> bool:
        """Remove a protected path by its stored relative path.

        Returns True if a row was deleted, False if the path was not found.
        """
        return ProtectedPathRepository.remove_protected_path(path)
