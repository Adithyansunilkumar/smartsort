"""Phase 11 — Approval System Pydantic models.

These models define the API-visible shapes for approval decisions and
approval status responses.  No execution semantics are modelled here;
approval is strictly a persisted human decision.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class ApprovalDecision(str, Enum):
    """The three possible states for an individual operation approval."""

    PENDING = "PENDING"
    """No decision has been recorded yet."""

    APPROVED = "APPROVED"
    """A human user has approved this operation for later execution.
    NOTE: Approval does NOT execute the operation.
    """

    REJECTED = "REJECTED"
    """A human user has explicitly rejected this operation."""


class OperationApprovalRecord(BaseModel):
    """Persisted approval record for a single organization operation."""

    approval_id: int = Field(..., description="Unique approval record ID")
    operation_id: int = Field(..., description="FK → organization_operations.id")
    plan_id: str = Field(..., description="FK → organization_plans.id")
    decision: ApprovalDecision = Field(..., description="PENDING | APPROVED | REJECTED")
    comment: Optional[str] = Field(None, description="Optional human comment")
    decided_at: str = Field(..., description="ISO 8601 timestamp of last decision change")
    created_at: str = Field(..., description="ISO 8601 timestamp of first record creation")


class OperationApprovalHistoryEntry(BaseModel):
    """Single entry in the immutable history of decision changes."""

    history_id: int = Field(..., description="Unique history entry ID")
    approval_id: int = Field(..., description="FK → operation_approvals.id")
    previous_decision: ApprovalDecision = Field(..., description="Decision before this change")
    new_decision: ApprovalDecision = Field(..., description="Decision after this change")
    comment: Optional[str] = Field(None, description="Comment supplied with this change")
    changed_at: str = Field(..., description="ISO 8601 timestamp of this change")


class OperationWithApproval(BaseModel):
    """An organization operation enriched with its current approval status."""

    # Core operation fields (from organization_operations)
    operation_id: int = Field(..., description="Stable operation DB id")
    plan_id: str = Field(..., description="Parent plan ID")
    type: str = Field(..., description="MOVE | RENAME | CREATE_FOLDER")
    source: str = Field(..., description="Source relative path")
    destination: str = Field(..., description="Destination relative path")
    reason: Optional[str] = Field(None, description="Human-readable reason")
    confidence: str = Field(..., description="HIGH | MEDIUM | LOW")

    # Approval enrichment
    approval_decision: ApprovalDecision = Field(
        ApprovalDecision.PENDING,
        description="Current approval decision",
    )
    comment: Optional[str] = Field(None, description="Most recent decision comment")
    decided_at: Optional[str] = Field(None, description="Timestamp of last decision")

    # Validation flags (set by the approval service)
    is_eligible: bool = Field(
        True,
        description=(
            "False when the operation cannot be approved due to a conflict, "
            "protected path, unsupported type, or invalid plan state."
        ),
    )
    ineligibility_reason: Optional[str] = Field(
        None,
        description="Human-readable explanation when is_eligible is False",
    )


class PlanApprovalStatus(BaseModel):
    """Aggregate approval status for all operations in a plan."""

    plan_id: str = Field(..., description="Organization plan ID")
    total_operations: int = Field(..., description="Total number of operations in the plan")
    approved_count: int = Field(..., description="Number of APPROVED operations")
    rejected_count: int = Field(..., description="Number of REJECTED operations")
    pending_count: int = Field(..., description="Number of PENDING operations")
    operations: List[OperationWithApproval] = Field(
        default_factory=list,
        description="Each operation with its current approval status",
    )


class ApprovalRequest(BaseModel):
    """Request body for approving or rejecting an operation.

    The client supplies ONLY a decision and optional comment.
    Operation details are always loaded from the database; this request
    never contains source/destination or any operation metadata.
    """

    decision: ApprovalDecision = Field(
        ...,
        description="APPROVED or REJECTED (PENDING is not a valid client decision)",
    )
    comment: Optional[str] = Field(
        None,
        max_length=500,
        description="Optional human comment (≤500 chars)",
    )


class ApprovalResponse(BaseModel):
    """API response after recording a decision."""

    operation: OperationWithApproval = Field(
        ..., description="The updated operation with its new approval status"
    )
    message: str = Field(
        ...,
        description="Confirmation message. Always states no files have been changed.",
    )
