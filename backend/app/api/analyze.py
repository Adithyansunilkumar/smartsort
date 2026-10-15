"""API router for File Analysis (Phase 6)."""

from fastapi import APIRouter, HTTPException, Depends
from app.analysis.models import AnalysisSummary
from app.analysis.service import analyze_scan_files
from app.services.folder_access import FolderAccessService, get_folder_access_service
from app.security.paths import PathSecurityError

router = APIRouter(prefix="/api/analyze", tags=["analysis"])


@router.post("/{scan_id}", response_model=AnalysisSummary)
@router.get("/{scan_id}", response_model=AnalysisSummary)
def start_analysis(
    scan_id: str,
    folder_service: FolderAccessService = Depends(get_folder_access_service),
):
    """
    Run deterministic file analysis on the specified scan.
    
    Persists analysis results and skips unchanged files via stale cache detection.
    Returns a structured summary.
    """
    try:
        result = analyze_scan_files(scan_id, folder_service)
        return result
    except ValueError as err:
        raise HTTPException(
            status_code=404,
            detail={"code": "SCAN_NOT_FOUND", "message": str(err)}
        )
    except PathSecurityError as err:
        raise HTTPException(
            status_code=409,
            detail={"code": err.code, "message": str(err)}
        )
    except Exception as err:
        raise HTTPException(
            status_code=500,
            detail={"code": "ANALYSIS_ERROR", "message": str(err)}
        )
