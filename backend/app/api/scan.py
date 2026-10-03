"""Scan API endpoint — POST /api/scan.

This module exposes a single endpoint that initiates a recursive filesystem
scan of the currently authorized root.  No scan path is accepted from the
client; the root is always obtained from FolderAccessService.

HTTP contract
-------------
POST /api/scan
  → 200  ScanResult (status "completed" or "failed")
  → 409  {"code": "ROOT_NOT_CONFIGURED", "message": "..."}  — no root set
  → 500  {"code": "SCAN_ERROR", "message": "..."}  — unexpected failure
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.filesystem.scanner import ScanResult, run_scan
from app.security.paths import PathSecurityError
from app.services.folder_access import FolderAccessService, get_folder_access_service
from app.database.repositories import ScanRepository

router = APIRouter(prefix="/api", tags=["scan"])


@router.post(
    "/scan",
    response_model=ScanResult,
    status_code=status.HTTP_200_OK,
    summary="Recursively scan the authorized root directory",
    description=(
        "Initiates a synchronous read-only scan of the currently authorized root. "
        "No path is accepted from the client — the scan root is always the "
        "directory established via POST /api/folder/select."
    ),
)
def start_scan(
    service: FolderAccessService = Depends(get_folder_access_service),
) -> ScanResult:
    """Scan the currently authorized root and return discovered file/folder metadata."""
    try:
        result = run_scan(service)
    except PathSecurityError as err:
        if err.code == "ROOT_NOT_CONFIGURED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "ROOT_NOT_CONFIGURED",
                    "message": "No authorized root is configured. Select a root directory first.",
                },
            )
        # Any other security error — treat as unexpected
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "SCAN_ERROR",
                "message": "Scan could not be started due to a security configuration error.",
            },
        )
    except Exception:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "SCAN_ERROR",
                "message": "An unexpected error occurred while initiating the scan.",
            },
        )

    # Persist the scan result
    try:
        ScanRepository.save_scan(result)
    except Exception as e:
        # If persistence fails, don't return the result as if it succeeded
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "DATABASE_ERROR",
                "message": "The scan completed but could not be saved to the database.",
            },
        )

    return result
