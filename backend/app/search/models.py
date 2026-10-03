"""Pydantic models for Phase 7 Search & Similarity API."""

from typing import Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class SearchRequest(BaseModel):
    """Request body for POST /api/search."""

    scan_id: str = Field(..., description="ID of the scan to search within.")
    query: str = Field(..., description="Search query string.")
    limit: int = Field(20, ge=1, le=200, description="Maximum number of results to return.")

    # Optional filters
    category: Optional[str] = Field(None, description="Filter by file category.")
    extension: Optional[str] = Field(None, description="Filter by extension (e.g. '.pdf').")
    min_size_bytes: Optional[int] = Field(None, ge=0, description="Minimum file size in bytes.")
    max_size_bytes: Optional[int] = Field(None, ge=0, description="Maximum file size in bytes.")

    @field_validator("query")
    @classmethod
    def query_must_be_nonempty(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("query must not be empty or whitespace-only")
        if len(stripped) > 500:
            raise ValueError("query must not exceed 500 characters")
        return stripped


class SimilarFilesRequest(BaseModel):
    """Request body for POST /api/search/similar/{scan_id}/{file_id}."""

    limit: int = Field(10, ge=1, le=100, description="Maximum number of similar files to return.")


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------

class SearchResult(BaseModel):
    """A single search result with relevance signals."""

    file_id: int = Field(..., description="Internal database ID of the file.")
    scan_id: str = Field(..., description="Scan this file belongs to.")
    name: str = Field(..., description="Filename.")
    relative_path: str = Field(..., description="Path relative to the authorized root.")
    extension: str = Field(..., description="File extension.")
    category: Optional[str] = Field(None, description="Phase 6 file category, if analyzed.")
    size_bytes: int = Field(..., description="File size in bytes.")
    modified_at: str = Field(..., description="ISO-8601 modification timestamp.")

    # Scoring signals
    relevance_score: float = Field(..., description="Combined relevance score (0.0–1.0 scale).")
    similarity_score: float = Field(0.0, description="TF-IDF cosine similarity to query (0.0–1.0).")
    lexical_score: float = Field(0.0, description="Lexical field-match score (0.0–1.0 scale).")

    # Match explanation
    matched_fields: List[str] = Field(
        default_factory=list,
        description="Which fields matched: filename, path, content.",
    )
    matched_terms: List[str] = Field(
        default_factory=list,
        description="Query terms that were found in the result.",
    )

    # Content availability
    content_available: bool = Field(
        False, description="True if Phase 5 extracted text is available for this file."
    )
    has_duplicates: bool = Field(
        False, description="True if Phase 4 found exact duplicates of this file."
    )


class SearchResponse(BaseModel):
    """Response for POST /api/search."""

    scan_id: str
    query: str
    total_results: int
    results: List[SearchResult]


class SimilarFileResponse(BaseModel):
    """A similar file result."""

    file_id: int
    name: str
    relative_path: str
    extension: str
    category: Optional[str] = None
    size_bytes: int
    modified_at: str
    similarity_score: float


class SimilarFilesResponse(BaseModel):
    """Response for POST /api/search/similar/{scan_id}/{file_id}."""

    scan_id: str
    target_file_id: int
    target_name: str
    similar_files: List[SimilarFileResponse]
