"""Extraction API endpoint — POST /api/extract/{scan_id}.

This module exposes an endpoint that processes a previously completed scan
to extract document text (TXT, PDF, DOCX) and persist it to the database.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.security.paths import PathSecurityError
from app.services.extraction import ExtractionResult, extract_scan_documents
from app.services.folder_access import FolderAccessService, get_folder_access_service

router = APIRouter(prefix="/api", tags=["extract"])


@router.post(
    "/extract/{scan_id}",
    response_model=ExtractionResult,
    status_code=status.HTTP_200_OK,
    summary="Extract text content from supported documents in a scan",
    description=(
        "Processes a persisted scan to extract text from supported document types "
        "(.txt, .pdf, .docx). Skips unsupported files and gracefully handles "
        "extraction errors. Extracted text is saved to the database."
    ),
)
def extract_documents(
    scan_id: str,
    service: FolderAccessService = Depends(get_folder_access_service),
) -> ExtractionResult:
    """Extract content for a given scan."""
    try:
        return extract_scan_documents(scan_id, service)
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
                "code": "EXTRACTION_ERROR",
                "message": "An unexpected error occurred while extracting documents.",
            },
        )
