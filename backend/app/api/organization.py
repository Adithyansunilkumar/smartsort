"""API router for Phase 8 Organization Engine."""

from fastapi import APIRouter, Depends, HTTPException
from typing import Optional

from app.services.folder_access import FolderAccessService, get_folder_access_service
from app.organization.models import OrganizationRequest, OrganizationResponse
from app.organization.service import generate_organization_plan, retrieve_organization_plan

router = APIRouter(prefix="/api/organize", tags=["organization"])


@router.post("/{scan_id}", response_model=OrganizationResponse)
def create_organization_plan(
    scan_id: str,
    request: Optional[OrganizationRequest] = None,
    folder_service: FolderAccessService = Depends(get_folder_access_service),
) -> OrganizationResponse:
    """Generate a read-only organization plan. Does not mutate the filesystem."""
    try:
        objective = request.objective if request else None
        plan = generate_organization_plan(scan_id, folder_service, objective)
        return OrganizationResponse(plan=plan)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "SCAN_NOT_FOUND", "message": str(exc)},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "ORGANIZATION_ERROR", "message": str(exc)},
        )


@router.get("/plan/{plan_id}", response_model=OrganizationResponse)
def get_organization_plan(plan_id: str) -> OrganizationResponse:
    """Retrieve an existing organization plan."""
    plan = retrieve_organization_plan(plan_id)
    if not plan:
        raise HTTPException(
            status_code=404,
            detail={"code": "PLAN_NOT_FOUND", "message": f"Plan '{plan_id}' not found."},
        )
    return OrganizationResponse(plan=plan)
