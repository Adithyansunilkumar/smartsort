"""Pydantic models for Phase 8 Organization Engine."""

from typing import List, Optional, Dict
from pydantic import BaseModel, Field


class OrganizationOperation(BaseModel):
    """A proposed filesystem operation."""
    type: str = Field(..., description="Operation type (COPY_FILE, CREATE_FOLDER)")
    source: str = Field(..., description="Source path relative to authorized root")
    destination: str = Field(..., description="Destination path relative to authorized root")
    reason: str = Field(..., description="Human-readable reason for the proposal")
    confidence: str = Field(..., description="Confidence level (HIGH, MEDIUM, LOW)")
    # Content-aware classification fields
    explanation: Optional[str] = Field(
        None,
        description="Detailed explanation of why this destination was chosen"
    )
    folder_exists: Optional[bool] = Field(
        None,
        description="True if the destination folder already exists on disk"
    )
    topic: Optional[str] = Field(
        None,
        description="Detected topic/subject for this file"
    )
    # ── New fields per human-rules spec ──────────────────────────────────
    rule: Optional[str] = Field(
        None,
        description="Machine-readable rule identifier that produced this operation"
    )
    confidence_score: Optional[float] = Field(
        None,
        description="Numeric confidence 0.0–1.0"
    )
    flags: Optional[str] = Field(
        None,
        description="Comma-separated flags (e.g. extension_mismatch, truncated)"
    )
    date_source: Optional[str] = Field(
        None,
        description="Where the date was sourced from (exif, filename_date, ctime, mtime)"
    )
    original_name: Optional[str] = Field(
        None,
        description="Original filename before any renaming"
    )
    duplicate_of: Optional[str] = Field(
        None,
        description="Hash of the file this is a duplicate of"
    )
    likely_newer: Optional[bool] = Field(
        None,
        description="True if filename suggests this is a newer version variant"
    )
    needs_review: Optional[bool] = Field(
        None,
        description="True if confidence < 0.8 or flags suggest manual review"
    )


class OrganizationPlan(BaseModel):
    """A complete organization plan proposal."""
    plan_id: str = Field(..., description="Unique identifier for this plan")
    scan_id: str = Field(..., description="Scan ID this plan is based on")
    objective: Optional[str] = Field(None, description="Optional user objective")
    created_at: str = Field(..., description="ISO timestamp")

    operations: List[OrganizationOperation] = Field(
        default_factory=list, description="List of proposed operations"
    )
    warnings: List[str] = Field(
        default_factory=list,
        description="Non-executable insights (e.g. duplicates, large files)"
    )
    protected_conflicts: List[str] = Field(
        default_factory=list,
        description="Operations rejected due to protected paths"
    )

    summary: Dict[str, int] = Field(
        default_factory=dict,
        description="Counts of operation types (e.g. {'COPY_FILE': 10})"
    )
    # Detected subjects/topics and folder proposal counts
    detected_topics: Dict[str, int] = Field(
        default_factory=dict,
        description="Detected topics and how many files each covers"
    )
    new_folders_proposed: int = Field(
        0,
        description="Number of new folders that will need to be created"
    )
    existing_folders_reused: int = Field(
        0,
        description="Number of destination folders that already exist on disk"
    )
    unclassified_files: int = Field(
        0,
        description="Files that could not be classified with HIGH/MEDIUM confidence"
    )
    # Plan options (echoed back for transparency)
    structure: Optional[str] = Field(
        None,
        description="Structure axis used: auto, type, date, project, type_year"
    )
    rename: bool = Field(
        False,
        description="Whether filenames were normalized/renamed"
    )
    archive_days: Optional[int] = Field(
        None,
        description="If set, files older than N days were routed to _Archive"
    )


class OrganizationRequest(BaseModel):
    """Request payload to generate a new plan."""
    scan_id: Optional[str] = Field(None, description="Optional scan ID when posting to /api/organization/plan")
    objective: Optional[str] = Field(None, description="Optional objective/directive")
    structure: Optional[str] = Field(
        None,
        description="Organization axis: auto (default), type, date, project, type_year"
    )
    rename: Optional[bool] = Field(
        None,
        description="Enable filename normalization/renaming (default False)"
    )
    archive_days: Optional[int] = Field(
        None,
        description="Route files older than N days to _Archive"
    )


class OrganizationResponse(BaseModel):
    """API response wrapping an OrganizationPlan."""
    plan: OrganizationPlan
