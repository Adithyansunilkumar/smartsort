from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List

from app.services.folder_access import get_folder_access_service, FolderAccessService
from app.organization.verification_models import PlanVerificationStatus
from app.organization.verification_service import verify_plan, get_plan_verification_summary

router = APIRouter(prefix="/api")

@router.get(
    "/plans/{plan_id}/verifications",
    response_model=PlanVerificationStatus,
    summary="Get plan verification status",
)
def get_verifications(
    plan_id: str,
):
    """Retrieve the verification status of operations within a plan."""
    return get_plan_verification_summary(plan_id)

@router.post(
    "/plans/{plan_id}/verify",
    response_model=PlanVerificationStatus,
    summary="Verify executed operations in a plan",
)
def verify_plan_operations(
    plan_id: str,
    folder_service: FolderAccessService = Depends(get_folder_access_service)
):
    """
    Verify the results of executed operations in a plan.
    This is read-only and does not mutate the filesystem or change execution states.
    """
    try:
        folder_service.require_authorized_root()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "ROOT_NOT_CONFIGURED", "message": str(e)},
        )

    try:
        verify_plan(plan_id, folder_service)
    except ValueError as e:
        if str(e) == "Plan not found":
            raise HTTPException(status_code=404, detail="Plan not found")
        raise HTTPException(status_code=400, detail=str(e))
        
    return get_plan_verification_summary(plan_id)
