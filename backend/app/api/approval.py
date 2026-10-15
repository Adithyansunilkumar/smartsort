"""Phase 11 — Approval API router.

Endpoints
---------
GET  /api/plans/{plan_id}/approvals
    Return the aggregate approval status for every operation in the plan.

POST /api/plans/{plan_id}/approvals/{operation_id}
    Record an APPROVED or REJECTED decision for a single operation.
    The client supplies only a decision and optional comment.
    Operation details are always loaded from the database.

POST /api/plans/{plan_id}/approvals/bulk-approve
    Approve all pending eligible operations in the plan at once.

POST /api/plans/{plan_id}/approvals/bulk-reject
    Reject all pending operations in the plan at once.

No Execute/Apply endpoint is provided.  Approval is strictly a human
decision record; it never triggers execution or completes an agent run.

Why approval is not exposed through the Tool System
----------------------------------------------------
The Phase 10 Tool System has no mechanism to verify that a tool invocation
originated from a human user rather than an LLM planner.  Because approval
carries meaningful authorization semantics (a human explicitly consenting
to a future operation), exposing it through the tool bus would create an
ambiguous authorship model — the same tool call path is used for LLM-driven
and UI-driven invocations.  Keeping approval behind this HTTP endpoint
ensures that every decision goes through an explicit browser/UI action,
preserving the human-origin guarantee required by the safety constraints.
"""

from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.organization.approval_models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResponse,
    OperationApprovalHistoryEntry,
    PlanApprovalStatus,
)
from app.organization.approval_service import (
    ApprovalValidationError,
    get_operation_approval_history,
    get_plan_approval_status,
    record_operation_decision,
)
from app.services.folder_access import FolderAccessService, get_folder_access_service

router = APIRouter(prefix="/api/plans", tags=["approvals"])


class BulkDecisionResponse(BaseModel):
    """Response after a bulk approve/reject operation."""
    updated_count: int = Field(..., description="Number of operations updated")
    skipped_count: int = Field(..., description="Number of operations skipped (ineligible or already decided)")
    plan_status: PlanApprovalStatus = Field(..., description="Updated plan approval status")
    message: str = Field(..., description="Confirmation message")


@router.get(
    "/{plan_id}/approvals",
    response_model=PlanApprovalStatus,
    summary="Get approval status for all operations in a plan",
    description=(
        "Returns every operation in the plan with its current approval decision "
        "(PENDING, APPROVED, or REJECTED) and eligibility flags.  "
        "This endpoint is read-only; it does not change any state."
    ),
)
def get_plan_approvals(
    plan_id: str,
    folder_service: FolderAccessService = Depends(get_folder_access_service),
) -> PlanApprovalStatus:
    """Retrieve the aggregate approval status for a plan."""
    try:
        return get_plan_approval_status(plan_id, folder_service)
    except ApprovalValidationError as exc:
        if exc.code == "PLAN_NOT_FOUND":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": exc.code, "message": exc.message},
            )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": exc.message},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "APPROVAL_ERROR", "message": str(exc)},
        )


@router.post(
    "/{plan_id}/approvals/bulk-approve",
    response_model=BulkDecisionResponse,
    summary="Approve all pending eligible operations in a plan",
    description=(
        "Records APPROVED for every eligible PENDING operation in the plan. "
        "Ineligible operations are silently skipped. "
        "IMPORTANT: This records decisions only. No files are changed."
    ),
)
def bulk_approve_operations(
    plan_id: str,
    folder_service: FolderAccessService = Depends(get_folder_access_service),
) -> BulkDecisionResponse:
    """Bulk-approve all pending eligible operations in the plan."""
    try:
        plan_status = get_plan_approval_status(plan_id, folder_service)
    except ApprovalValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND if exc.code == "PLAN_NOT_FOUND" else status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": exc.message},
        )

    updated_count = 0
    skipped_count = 0
    errors = []

    for op in plan_status.operations:
        if op.approval_decision != ApprovalDecision.PENDING:
            skipped_count += 1
            continue
        if not op.is_eligible:
            skipped_count += 1
            continue
        try:
            record_operation_decision(
                plan_id=plan_id,
                operation_id=op.operation_id,
                decision=ApprovalDecision.APPROVED,
                comment=None,
                folder_service=folder_service,
            )
            updated_count += 1
        except Exception as exc:
            errors.append(str(exc))
            skipped_count += 1

    # Refresh plan status after bulk update
    try:
        refreshed_status = get_plan_approval_status(plan_id, folder_service)
    except Exception:
        refreshed_status = plan_status

    if errors:
        message = f"Approved {updated_count} operation(s). {skipped_count} skipped. Some operations had errors."
    elif updated_count == 0:
        message = "No pending eligible operations to approve."
    else:
        message = f"Approved {updated_count} operation(s). No files have been changed."

    return BulkDecisionResponse(
        updated_count=updated_count,
        skipped_count=skipped_count,
        plan_status=refreshed_status,
        message=message,
    )


@router.post(
    "/{plan_id}/approvals/bulk-reject",
    response_model=BulkDecisionResponse,
    summary="Reject all pending operations in a plan",
    description=(
        "Records REJECTED for every PENDING operation in the plan. "
        "Already-approved operations are also changed to REJECTED. "
        "IMPORTANT: This records decisions only. No files are changed. "
        "Rejected operations will be skipped during execution."
    ),
)
def bulk_reject_operations(
    plan_id: str,
    folder_service: FolderAccessService = Depends(get_folder_access_service),
) -> BulkDecisionResponse:
    """Bulk-reject all pending operations in the plan."""
    try:
        plan_status = get_plan_approval_status(plan_id, folder_service)
    except ApprovalValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND if exc.code == "PLAN_NOT_FOUND" else status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": exc.message},
        )

    updated_count = 0
    skipped_count = 0

    for op in plan_status.operations:
        if op.approval_decision == ApprovalDecision.REJECTED:
            skipped_count += 1
            continue
        if not op.is_eligible:
            skipped_count += 1
            continue
        try:
            record_operation_decision(
                plan_id=plan_id,
                operation_id=op.operation_id,
                decision=ApprovalDecision.REJECTED,
                comment=None,
                folder_service=folder_service,
            )
            updated_count += 1
        except Exception:
            skipped_count += 1

    # Refresh plan status after bulk update
    try:
        refreshed_status = get_plan_approval_status(plan_id, folder_service)
    except Exception:
        refreshed_status = plan_status

    if updated_count == 0:
        message = "No pending operations to reject."
    else:
        message = f"Rejected {updated_count} operation(s). No files have been changed."

    return BulkDecisionResponse(
        updated_count=updated_count,
        skipped_count=skipped_count,
        plan_status=refreshed_status,
        message=message,
    )


@router.post(
    "/{plan_id}/approvals/{operation_id}",
    response_model=ApprovalResponse,
    summary="Approve or reject a single operation",
    description=(
        "Record a human APPROVED or REJECTED decision for the specified operation.  "
        "The client supplies ONLY a decision and optional comment.  "
        "Operation source/destination are always loaded from the database — "
        "client-supplied values are never trusted.  "
        "IMPORTANT: Approval is recorded for later reference only.  "
        "No files have been changed."
    ),
)
def decide_operation(
    plan_id: str,
    operation_id: int,
    body: ApprovalRequest,
    folder_service: FolderAccessService = Depends(get_folder_access_service),
) -> ApprovalResponse:
    """Record an approval decision for a single plan operation."""
    # Guard: PENDING is not a valid client-supplied decision
    if body.decision == ApprovalDecision.PENDING:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "INVALID_DECISION",
                "message": "PENDING is not a valid client decision. Submit APPROVED or REJECTED.",
            },
        )

    try:
        updated_op = record_operation_decision(
            plan_id=plan_id,
            operation_id=operation_id,
            decision=body.decision,
            comment=body.comment,
            folder_service=folder_service,
        )
    except ApprovalValidationError as exc:
        http_status = (
            status.HTTP_404_NOT_FOUND
            if exc.code in {"PLAN_NOT_FOUND", "OPERATION_NOT_FOUND"}
            else status.HTTP_422_UNPROCESSABLE_ENTITY
        )
        raise HTTPException(
            status_code=http_status,
            detail={"code": exc.code, "message": exc.message},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "APPROVAL_ERROR", "message": str(exc)},
        )

    verb = "approved" if body.decision == ApprovalDecision.APPROVED else "rejected"
    return ApprovalResponse(
        operation=updated_op,
        message=(
            f"Operation #{operation_id} has been {verb}. "
            "Approved for later execution. No files have been changed."
        ),
    )


@router.get(
    "/{plan_id}/approvals/{operation_id}/history",
    response_model=list[OperationApprovalHistoryEntry],
    summary="Get the full immutable decision history for a single operation",
    description=(
        "Returns every decision change recorded for the specified operation, "
        "in chronological order.  The history is append-only and never modified."
    ),
)
def get_operation_history(
    plan_id: str,
    operation_id: int,
    folder_service: FolderAccessService = Depends(get_folder_access_service),
) -> list[OperationApprovalHistoryEntry]:
    """Retrieve the decision audit history for a single operation."""
    try:
        return get_operation_approval_history(plan_id, operation_id)
    except ApprovalValidationError as exc:
        http_status = (
            status.HTTP_404_NOT_FOUND
            if exc.code in {"PLAN_NOT_FOUND", "OPERATION_NOT_FOUND"}
            else status.HTTP_422_UNPROCESSABLE_ENTITY
        )
        raise HTTPException(
            status_code=http_status,
            detail={"code": exc.code, "message": exc.message},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "APPROVAL_HISTORY_ERROR", "message": str(exc)},
        )
