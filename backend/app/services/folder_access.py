"""Folder access service managing authorized root state.

This service is the **single gateway** to the authorized root and to all
path-validation operations.  Future filesystem services (scanner, mover, etc.)
must call these methods rather than importing the security functions in
``app.security.paths`` directly or accessing the underlying ``_authorized_root``
attribute.

In-memory state contract
------------------------
* The authorized root is stored **in process memory only**.
* Restarting the backend process clears authorization — the user must re-select
  the root via the API.
* Phase 3 will introduce SQLite-backed persistence without altering this public
  service or API contract.

Security pipeline
-----------------
All path-based operations should flow through::

    FolderAccessService.require_authorized_root()
            ↓
    FolderAccessService.validate_path_in_root(candidate)
            ↓
    [protected-path check — Phase 3 placeholder]
            ↓
    operation permission

Never bypass this service to perform ad-hoc ``startswith()`` or ``..`` checks.
"""

from pathlib import Path
from typing import Optional, Union

from app.security.paths import (
    PathSecurityError,
    validate_path_in_root as _validate_path_in_root,
)


class FolderAccessService:
    """Manages the lifecycle of the active authorized root.

    All path-validation calls should be made through this service so that the
    authorized root is never passed around manually and security logic stays
    centralized in ``app.security.paths``.
    """

    def __init__(self) -> None:
        self._authorized_root: Optional[Path] = None

    # ------------------------------------------------------------------
    # Root lifecycle
    # ------------------------------------------------------------------

    def get_current_root(self) -> Optional[Path]:
        """Return the active authorized root Path, or ``None`` if unconfigured."""
        return self._authorized_root

    def set_authorized_root(self, root_path: Path) -> Path:
        """Store the validated canonical authorized root."""
        self._authorized_root = root_path
        return self._authorized_root

    def clear_authorized_root(self) -> None:
        """Clear the currently authorized root."""
        self._authorized_root = None

    def is_configured(self) -> bool:
        """Return ``True`` if an authorized root has been established."""
        return self._authorized_root is not None

    # ------------------------------------------------------------------
    # Security contract — future filesystem services call these
    # ------------------------------------------------------------------

    def require_authorized_root(self) -> Path:
        """Return the active authorized root or raise a controlled error.

        Use this in any operation that must not proceed without a configured root.
        Raises a :class:`~app.security.paths.PathSecurityError` rather than
        returning ``None``, so callers cannot accidentally dereference ``None``.

        Returns:
            The canonical authorized root :class:`~pathlib.Path`.

        Raises:
            PathSecurityError: With code ``ROOT_NOT_CONFIGURED`` when no root
                has been established.
        """
        if self._authorized_root is None:
            raise PathSecurityError(
                code="ROOT_NOT_CONFIGURED",
                message="No authorized root is configured. Select a root directory first.",
            )
        return self._authorized_root

    def validate_path_in_root(
        self,
        candidate_path: Union[str, Path],
        *,
        allow_root: bool = True,
        allow_nonexistent: bool = False,
    ) -> Path:
        """Validate *candidate_path* against the authorized root.

        This is the preferred entry point for all path-validation operations.
        It automatically supplies the stored authorized root, so callers never
        need to handle the root directly.

        Delegates to :func:`app.security.paths.validate_path_in_root` —
        see that function for full behavioral documentation, including the
        nonexistent-path mode.

        Args:
            candidate_path:    Path to validate (string or :class:`~pathlib.Path`).
            allow_root:        Whether the candidate may equal the root itself.
                               Defaults to ``True``.
            allow_nonexistent: When ``True``, the candidate leaf need not exist;
                               its *parent* is resolved and must be inside the root.
                               Use for validating future destination paths only.
                               Defaults to ``False`` (strict: candidate must exist).

        Returns:
            Canonical resolved :class:`~pathlib.Path` of the candidate.

        Raises:
            PathSecurityError: With code ``ROOT_NOT_CONFIGURED`` if no root is
                set, or with other security codes if validation fails.
            PathValidationError: If the candidate is empty.
        """
        root = self.require_authorized_root()
        return _validate_path_in_root(
            candidate_path,
            root,
            allow_root=allow_root,
            allow_nonexistent=allow_nonexistent,
        )


# ---------------------------------------------------------------------------
# Shared singleton — one instance per process
# ---------------------------------------------------------------------------

_folder_access_service = FolderAccessService()


def get_folder_access_service() -> FolderAccessService:
    """FastAPI dependency provider for :class:`FolderAccessService`.

    Returns the process-scoped singleton.  Because the root is in-memory,
    this singleton is cleared on every process restart.
    """
    return _folder_access_service
