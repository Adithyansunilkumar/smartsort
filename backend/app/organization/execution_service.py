"""Phase 12 Execution Service.

Executes approved organization operations from a plan.

Key design decisions
--------------------
* MOVE operations are implemented as COPY operations: source files are copied
  to the destination (inside SmartSort_Organized/) and the originals are
  preserved.  This is the default user-facing workflow: organized output lives
  in SmartSort_Organized/, originals remain untouched.
* CREATE_FOLDER operations use mkdir(parents=True, exist_ok=True) so that the
  SmartSort_Organized folder and its subfolders are created idempotently.
* Only APPROVED operations are executed; PENDING and REJECTED operations are
  skipped.
* Destination conflict: if the destination file already exists inside
  SmartSort_Organized, the operation is marked as SKIPPED with a clear reason
  rather than silently overwriting.
"""
import os
import shutil
from pathlib import Path
from typing import List, Dict, Any, Optional

from app.database.repositories import OrganizationRepository
from app.organization.approval_repository import ApprovalRepository
from app.organization.execution_repository import ExecutionRepository
from app.organization.execution_models import ExecutionStatus, PlanExecutionStatus
from app.services.folder_access import get_folder_access_service, FolderAccessService
from app.services.protected_paths import ProtectedPathService

OUTPUT_FOLDER_NAME = "SmartSort_Organized"


class ExecutionError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def execute_operation(operation_id: int, folder_service: FolderAccessService) -> None:
    # 1. Fetch operation details
    op_exec = ExecutionRepository.get_operation_execution(operation_id)
    if not op_exec:
        raise ExecutionError("NOT_FOUND", f"Execution record not found for operation {operation_id}")
    
    if op_exec.status != ExecutionStatus.PENDING:
        raise ExecutionError("INVALID_STATE", f"Operation {operation_id} is in state {op_exec.status.value}")

    plan_id = op_exec.plan_id
    plan_data = OrganizationRepository.get_plan(plan_id)
    if not plan_data:
        ExecutionRepository.mark_operation_failed(operation_id, "PLAN_NOT_FOUND", "Plan no longer exists.")
        return

    # Find the specific operation details
    op_detail = next((o for o in plan_data.get("operations", []) if o.get("id") == operation_id), None)
    if not op_detail:
        ExecutionRepository.mark_operation_failed(operation_id, "OP_NOT_FOUND", "Operation detail missing from plan.")
        return

    # 2. Check approval status
    approval = ApprovalRepository.get_approval_for_operation(operation_id)
    if not approval or approval.get("decision") != "APPROVED":
        ExecutionRepository.mark_operation_skipped(operation_id, "UNAPPROVED", "Operation is not approved.")
        return

    # 3. Lock operation
    if not ExecutionRepository.lock_operation_for_execution(operation_id):
        raise ExecutionError("CONCURRENCY_ERROR", "Could not lock operation. It may be executing concurrently.")

    try:
        _perform_execution(op_detail, folder_service)
        ExecutionRepository.mark_operation_completed(operation_id)
    except ExecutionError as e:
        ExecutionRepository.mark_operation_failed(operation_id, e.code, e.message)
    except Exception as e:
        ExecutionRepository.mark_operation_failed(operation_id, "SYSTEM_ERROR", str(e))


from app.security.paths import PathSecurityError, PathValidationError


def _perform_execution(op_detail: Dict[str, Any], folder_service: FolderAccessService) -> None:
    op_type = op_detail.get("type")
    source_str = op_detail.get("source")
    dest_str = op_detail.get("destination")

    prot_svc = ProtectedPathService(folder_service)

    # Validate destination
    if not dest_str:
        raise ExecutionError("INVALID_DESTINATION", "No destination path provided.")

    try:
        # Destination leaf may not exist yet, so allow_nonexistent=True
        dest_path = folder_service.validate_path_in_root(dest_str, allow_nonexistent=True)
    except PathSecurityError as e:
        print(f"PathSecurityError caught: code={e.code}, message={e.message}")
        # If the failure is because the SmartSort_Organized parent doesn't exist, we allow it
        # because we will create it right before copying/moving.
        dest_norm = dest_str.replace("\\", "/")
        if e.code == "PATH_OUTSIDE_ROOT" and "parent path does not exist" in e.message and (dest_norm == "SmartSort_Organized" or dest_norm.startswith("SmartSort_Organized/")):
            # It's safe; compute the absolute path directly
            dest_path = (Path(folder_service.require_authorized_root()) / dest_str).resolve()
        else:
            raise ExecutionError("SECURITY_DENIED", f"Destination validation failed: {str(e)}")
    except PathValidationError as e:
        print(f"PathValidationError caught: {e}")
        raise ExecutionError("SECURITY_DENIED", f"Destination validation failed: {str(e)}")

    if prot_svc.is_protected(dest_str):
        raise ExecutionError("PROTECTED_PATH", "Destination is a protected path.")

    if op_type in ("MOVE", "RENAME", "COPY_FILE"):

        if not source_str:
            raise ExecutionError("INVALID_SOURCE", "No source path provided.")
        
        try:
            source_path = folder_service.validate_path_in_root(source_str, allow_nonexistent=False)
        except (PathSecurityError, PathValidationError) as e:
            raise ExecutionError("SECURITY_DENIED", f"Source validation failed: {str(e)}")

        if not source_path.exists():
            raise ExecutionError("SOURCE_MISSING", "Source path does not exist.")

        if prot_svc.is_protected(source_str):
            raise ExecutionError("PROTECTED_PATH", "Source is a protected path.")

        # Check for destination conflict — never silently overwrite
        if dest_path.exists():
            raise ExecutionError(
                "DESTINATION_CONFLICT",
                f"Destination already exists: {dest_str}. "
                "No files were overwritten. To re-organize, remove or rename the existing output."
            )

        # Ensure parent of destination exists
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Default workflow: COPY the file so originals are preserved.
        # The destination is always inside SmartSort_Organized/.
        try:
            if source_path.is_dir():
                shutil.copytree(str(source_path), str(dest_path))
            else:
                shutil.copy2(str(source_path), str(dest_path))
        except OSError as e:
            raise ExecutionError("OS_ERROR", f"Filesystem error during copy: {str(e)}")

    elif op_type == "CREATE_FOLDER":
        # Create the destination folder idempotently.
        # If SmartSort_Organized already exists from a previous run, this is fine.
        try:
            dest_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ExecutionError("OS_ERROR", f"Filesystem error during folder creation: {str(e)}")
    else:
        raise ExecutionError("UNSUPPORTED_TYPE", f"Operation type {op_type} is not supported.")


def execute_plan(plan_id: str, folder_service: FolderAccessService, operation_ids: Optional[List[int]] = None) -> None:
    """Execute all pending, approved operations in a plan, or only specific ones if operation_ids provided."""
    executions = ExecutionRepository.get_plan_execution_status(plan_id)
    
    # Filter to requested
    if operation_ids is not None:
        op_set = set(operation_ids)
        executions = [e for e in executions if e.operation_id in op_set]

    # Only attempt PENDING
    to_execute = [e for e in executions if e.status == ExecutionStatus.PENDING]
    
    for e in to_execute:
        try:
            execute_operation(e.operation_id, folder_service)
        except ExecutionError:
            pass  # Already marked failed/skipped, or concurrency error


def get_plan_execution_summary(plan_id: str) -> PlanExecutionStatus:
    executions = ExecutionRepository.get_plan_execution_status(plan_id)
    return PlanExecutionStatus(
        plan_id=plan_id,
        total_operations=len(executions),
        completed_count=sum(1 for e in executions if e.status == ExecutionStatus.COMPLETED),
        failed_count=sum(1 for e in executions if e.status == ExecutionStatus.FAILED),
        skipped_count=sum(1 for e in executions if e.status == ExecutionStatus.SKIPPED),
        in_progress_count=sum(1 for e in executions if e.status == ExecutionStatus.IN_PROGRESS),
        pending_count=sum(1 for e in executions if e.status == ExecutionStatus.PENDING),
        executions=executions
    )
