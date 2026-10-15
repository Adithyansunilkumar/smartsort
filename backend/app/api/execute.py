from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from pathlib import Path

from app.services.folder_access import get_folder_access_service, FolderAccessService
from app.organization.execution_models import PlanExecutionStatus, ExecutePlanRequest
from app.organization.execution_service import execute_plan, get_plan_execution_summary

OUTPUT_FOLDER_NAME = "SmartSort_Organized"

router = APIRouter(prefix="/api")


class OutputFolderInfo(BaseModel):
    output_folder_name: str
    output_folder_path: Optional[str]
    exists: bool
    root_path: Optional[str]


@router.get(
    "/output-folder",
    response_model=OutputFolderInfo,
    summary="Get the SmartSort organized output folder path",
)
def get_output_folder(
    folder_service: FolderAccessService = Depends(get_folder_access_service),
) -> OutputFolderInfo:
    """Return the path to the SmartSort_Organized output folder."""
    try:
        root = folder_service.require_authorized_root()
        output_path = root / OUTPUT_FOLDER_NAME
        return OutputFolderInfo(
            output_folder_name=OUTPUT_FOLDER_NAME,
            output_folder_path=str(output_path),
            exists=output_path.exists(),
            root_path=str(root),
        )
    except Exception:
        return OutputFolderInfo(
            output_folder_name=OUTPUT_FOLDER_NAME,
            output_folder_path=None,
            exists=False,
            root_path=None,
        )


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
    Files are copied to the SmartSort_Organized folder; originals are preserved.
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
