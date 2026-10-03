"""Pydantic models for Phase 8 Organization Engine."""

from typing import List, Optional, Dict
from pydantic import BaseModel, Field


class OrganizationOperation(BaseModel):
    """A proposed filesystem operation."""
    type: str = Field(..., description="Operation type (MOVE, RENAME, CREATE_FOLDER)")
    source: str = Field(..., description="Source path relative to authorized root")
    destination: str = Field(..., description="Destination path relative to authorized root")
    reason: str = Field(..., description="Human-readable reason for the proposal")
    confidence: str = Field(..., description="Confidence level (HIGH, MEDIUM, LOW)")


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
        default_factory=list, description="Non-executable insights (e.g. duplicates, large files)"
    )
    protected_conflicts: List[str] = Field(
        default_factory=list, description="Operations rejected due to protected paths"
    )
    
    summary: Dict[str, int] = Field(
        default_factory=dict, description="Counts of operation types (e.g. {'MOVE': 10})"
    )


class OrganizationRequest(BaseModel):
    """Request payload to generate a new plan."""
    objective: Optional[str] = Field(None, description="Optional objective/directive")


class OrganizationResponse(BaseModel):
    """API response wrapping an OrganizationPlan."""
    plan: OrganizationPlan
