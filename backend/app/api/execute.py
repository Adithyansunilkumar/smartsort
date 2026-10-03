from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List

from app.services.folder_access import get_folder_access_service, FolderAccessService
from app.organization.execution_models import PlanExecutionStatus, ExecutePlanRequest
from app.organization.execution_service import execute_plan, get_plan_execution_summary

router = APIRouter(prefix="/api")

@router.get(
    "/plans/{plan_id}/executions",
    response_model=PlanExecutionStatus,
    summary="Get plan execution status",
)
def get_executions(
    plan_id: str,
):
    """Retrieve the execution status of operations within a plan."""
    return get_plan_execution_summary(plan_id)

@router.post(
    "/plans/{plan_id}/execute",
    response_model=PlanExecutionStatus,
    summary="Execute approved operations in a plan",
)
def execute_plan_operations(
    plan_id: str,
    request: ExecutePlanRequest,
    folder_service: FolderAccessService = Depends(get_folder_access_service)
):
    """
    Execute all (or specific) approved and pending operations in a plan.
    Requires user confirmation via API boundary. Cannot be called arbitrarily by LLM.
    """
    try:
        folder_service.require_authorized_root()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "ROOT_NOT_CONFIGURED", "message": str(e)},
        )

    execute_plan(plan_id, folder_service, request.operation_ids)
    
    return get_plan_execution_summary(plan_id)
