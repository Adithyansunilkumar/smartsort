"""Folder selection and authorized root management API endpoints."""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.security.paths import (
    PathValidationError,
    normalize_and_validate_root,
)
from app.services.folder_access import (
    FolderAccessService,
    get_folder_access_service,
)

router = APIRouter(prefix="/api/folder", tags=["folder"])


class SelectFolderRequest(BaseModel):
    path: str = Field(
        ...,
        description="Local directory path to establish as the authorized root",
        examples=["C:\\Users\\Username\\Documents", "/home/user/documents"],
    )


class SelectFolderResponse(BaseModel):
    status: str = Field(default="success", description="Status indicator")
    authorized_root: str = Field(..., description="Canonical path of the authorized root")
    message: str = Field(..., description="Human-readable result summary")


class FolderStatusResponse(BaseModel):
    is_configured: bool = Field(..., description="True if an authorized root is active")
    authorized_root: Optional[str] = Field(
        default=None,
        description="Canonical path of the current authorized root, or null if none",
    )
    message: str = Field(..., description="Human-readable status summary")


class ClearFolderResponse(BaseModel):
    status: str = Field(default="success", description="Status indicator")
    message: str = Field(default="Authorized root cleared", description="Result summary")


@router.post(
    "/select",
    response_model=SelectFolderResponse,
    status_code=status.HTTP_200_OK,
    summary="Establish an authorized root directory",
)
def select_folder(
    request: SelectFolderRequest,
    service: FolderAccessService = Depends(get_folder_access_service),
) -> SelectFolderResponse:
    """Validate and establish the authorized root directory for SmartSort.

    Validates that the directory exists, is a folder, and is readable before
    storing the canonical path.
    """
    try:
        canonical_root = normalize_and_validate_root(request.path)
    except PathValidationError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": err.code,
                "message": err.message,
            },
        )

    service.set_authorized_root(canonical_root)

    return SelectFolderResponse(
        status="success",
        authorized_root=str(canonical_root),
        message="Authorized root established successfully.",
    )


@router.get(
    "",
    response_model=FolderStatusResponse,
    summary="Get the currently authorized root directory",
)
def get_folder(
    service: FolderAccessService = Depends(get_folder_access_service),
) -> FolderStatusResponse:
    """Return the currently configured authorized root, or indicate unconfigured."""
    current_root = service.get_current_root()
    if current_root is None:
        return FolderStatusResponse(
            is_configured=False,
            authorized_root=None,
            message="No authorized root directory is currently configured.",
        )

    return FolderStatusResponse(
        is_configured=True,
        authorized_root=str(current_root),
        message="Authorized root is active.",
    )


@router.delete(
    "",
    response_model=ClearFolderResponse,
    summary="Clear the current authorized root directory",
)
def clear_folder(
    service: FolderAccessService = Depends(get_folder_access_service),
) -> ClearFolderResponse:
    """Clear the active authorized root directory."""
    service.clear_authorized_root()
    return ClearFolderResponse(
        status="success",
        message="Authorized root cleared successfully.",
    )
