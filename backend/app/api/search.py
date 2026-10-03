"""API router for Phase 7 Search & Similarity."""

from fastapi import APIRouter, HTTPException

from app.search.models import (
    SearchRequest,
    SearchResponse,
    SimilarFilesRequest,
    SimilarFilesResponse,
)
from app.search.service import search_scan, find_similar_files

router = APIRouter(prefix="/api/search", tags=["search"])


@router.post("", response_model=SearchResponse)
def search(request: SearchRequest) -> SearchResponse:
    """Search a scan using filename, path, and content TF-IDF similarity.

    Returns ranked results with relevance signals.
    """
    try:
        return search_scan(request)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "SCAN_NOT_FOUND", "message": str(exc)},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "SEARCH_ERROR", "message": str(exc)},
        )


@router.post("/similar/{scan_id}/{file_id}", response_model=SimilarFilesResponse)
def similar_files(
    scan_id: str,
    file_id: int,
    request: SimilarFilesRequest = SimilarFilesRequest(),
) -> SimilarFilesResponse:
    """Return files similar to the target using TF-IDF cosine similarity.

    Similarity is evidence-based (tokens, not extension/size).
    Returns an empty list when the target has no searchable content.
    """
    try:
        return find_similar_files(scan_id, file_id, limit=request.limit)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "SCAN_NOT_FOUND", "message": str(exc)},
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "FILE_NOT_FOUND", "message": str(exc)},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "SEARCH_ERROR", "message": str(exc)},
        )
