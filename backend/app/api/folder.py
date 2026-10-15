import asyncio
import json
import re
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
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


class BrowseFolderResponse(BaseModel):
    status: str = Field(default="success", description="Status indicator")
    path: Optional[str] = Field(default=None, description="Selected canonical directory path, or None if canceled")
    message: str = Field(default="", description="Result message")


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


def _show_native_directory_dialog() -> Optional[str]:
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        selected = filedialog.askdirectory(title="Select Folder to Organize — SmartSort")
        root.destroy()
        return selected if selected else None
    except Exception:
        return None


@router.post(
    "/browse",
    response_model=BrowseFolderResponse,
    status_code=status.HTTP_200_OK,
    summary="Open native system directory picker dialog",
)
async def browse_folder() -> BrowseFolderResponse:
    """Open the native operating system folder selection dialog on the host machine.
    
    Returns the selected folder path, or None if the user cancelled the dialog.
    """
    selected_path = await asyncio.to_thread(_show_native_directory_dialog)
    if not selected_path:
        return BrowseFolderResponse(status="cancelled", path=None, message="Folder selection was cancelled.")
    
    return BrowseFolderResponse(status="success", path=str(Path(selected_path).resolve()), message="Folder selected successfully.")


@router.post(
    "/upload",
    response_model=SelectFolderResponse,
    status_code=status.HTTP_200_OK,
    summary="Upload folder contents and establish authorized root",
)
async def upload_folder(
    files: List[UploadFile] = File(...),
    paths: Optional[List[str]] = Form(default=None),
    relative_paths: Optional[str] = Form(default=None),
    service: FolderAccessService = Depends(get_folder_access_service),
) -> SelectFolderResponse:
    """Receive uploaded folder files, save them locally preserving directory hierarchy,
    and establish the folder as the authorized root for SmartSort.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "EMPTY_UPLOAD", "message": "No files were uploaded."},
        )

    parsed_paths: List[str] = []
    if relative_paths:
        try:
            parsed = json.loads(relative_paths)
            if isinstance(parsed, list):
                parsed_paths = [str(p) for p in parsed]
        except Exception:
            pass
    if not parsed_paths and paths:
        parsed_paths = paths

    # Fallback to file.filename if relative paths are missing
    if len(parsed_paths) < len(files):
        for i in range(len(parsed_paths), len(files)):
            parsed_paths.append(files[i].filename or f"file_{i}")

    # Determine root folder name from relative path (e.g. "MyDocs/a.txt" -> "MyDocs")
    first_path = parsed_paths[0].replace("\\", "/")
    parts = [p for p in first_path.split("/") if p and p != "."]
    folder_name = parts[0] if len(parts) > 1 else "Uploaded_Folder"
    safe_folder_name = re.sub(r'[^\w\-\.\s]', '_', folder_name).strip() or "Uploaded_Folder"

    upload_root = Path("uploaded_folders").resolve()
    upload_root.mkdir(parents=True, exist_ok=True)
    target_dir = (upload_root / safe_folder_name).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    for file, rel in zip(files, parsed_paths):
        norm_rel = rel.replace("\\", "/").lstrip("/")
        rel_parts = [p for p in norm_rel.split("/") if p and p != "."]

        if len(rel_parts) > 1 and rel_parts[0] == folder_name:
            sub_path = Path(*rel_parts[1:])
        elif rel_parts:
            sub_path = Path(*rel_parts)
        else:
            sub_path = Path(file.filename or "file")

        # Security check: verify no path traversal escape
        dest_path = (target_dir / sub_path).resolve()
        if not dest_path.is_relative_to(target_dir):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "PATH_TRAVERSAL", "message": "Illegal path traversal in uploaded folder."},
            )

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        content = await file.read()
        dest_path.write_bytes(content)

    canonical_root = normalize_and_validate_root(target_dir)
    service.set_authorized_root(canonical_root)

    return SelectFolderResponse(
        status="success",
        authorized_root=str(canonical_root),
        message=f"Uploaded {len(files)} files into {safe_folder_name} successfully.",
    )

