"""Duplicate detection API endpoint — POST /api/duplicates/{scan_id}.

This module exposes an endpoint that processes a previously completed scan
to identify exact duplicate files by cryptographically hashing them.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.security.paths import PathSecurityError
from app.services.duplicates import DuplicateResult, find_duplicates
from app.services.folder_access import FolderAccessService, get_folder_access_service

router = APIRouter(prefix="/api", tags=["duplicates"])


@router.post(
    "/duplicates/{scan_id}",
    response_model=DuplicateResult,
    status_code=status.HTTP_200_OK,
    summary="Find exact duplicate files within a scan",
    description=(
        "Processes a persisted scan to group files by exact size, hashes eligible "
        "candidates using SHA-256, and returns groups of strictly identical files. "
        "Hashes are persisted to avoid recalculation."
    ),
)
def detect_duplicates(
    scan_id: str,
    service: FolderAccessService = Depends(get_folder_access_service),
) -> DuplicateResult:
    """Identify duplicate files for a given scan."""
    try:
        return find_duplicates(scan_id, service)
    except PathSecurityError as err:
        if err.code == "ROOT_NOT_CONFIGURED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "ROOT_NOT_CONFIGURED",
                    "message": "No authorized root is configured. Select a root directory first.",
                },
            )
        # Other security errors (like traversal or outside root) during validation
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "SECURITY_VIOLATION",
                "message": "A security violation was detected while accessing files.",
            },
        )
    except ValueError as err:
        # Scan ID not found or empty
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "SCAN_NOT_FOUND",
                "message": str(err),
            },
        )
    except Exception:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "DUPLICATE_DETECTION_ERROR",
                "message": "An unexpected error occurred while detecting duplicates.",
            },
        )
