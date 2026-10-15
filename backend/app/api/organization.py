"""API router for Phase 8 Organization Engine."""

from fastapi import APIRouter, Depends, HTTPException
from typing import Optional

from app.services.folder_access import FolderAccessService, get_folder_access_service
from app.organization.models import OrganizationRequest, OrganizationResponse
from app.organization.service import generate_organization_plan, retrieve_organization_plan

router = APIRouter(prefix="/api/organize", tags=["organization"])
alt_router = APIRouter(prefix="/api/organization", tags=["organization"])


@alt_router.post("/plan", response_model=OrganizationResponse)
@router.post("/plan", response_model=OrganizationResponse)
def create_organization_plan_with_body(
    request: OrganizationRequest,
    folder_service: FolderAccessService = Depends(get_folder_access_service),
) -> OrganizationResponse:
    """Generate a plan when scan_id is in request body (POST /api/organization/plan)."""
    if not request.scan_id:
        raise HTTPException(
            status_code=400,
            detail={"code": "SCAN_ID_REQUIRED", "message": "scan_id must be provided in request body when calling /plan."},
        )
    return create_organization_plan(request.scan_id, request, folder_service)


@router.post("/{scan_id}", response_model=OrganizationResponse)
@alt_router.post("/{scan_id}", response_model=OrganizationResponse)
def create_organization_plan(
    scan_id: str,
    request: Optional[OrganizationRequest] = None,
    folder_service: FolderAccessService = Depends(get_folder_access_service),
) -> OrganizationResponse:
    """Generate a read-only organization plan. Does not mutate the filesystem."""
    try:
        objective = request.objective if request else None
        structure = request.structure if request else None
        rename = bool(request.rename) if request and request.rename is not None else False
        archive_days = request.archive_days if request else None
        plan = generate_organization_plan(
            scan_id, folder_service,
            objective=objective,
            structure=structure,
            rename=rename,
            archive_days=archive_days,
        )
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
