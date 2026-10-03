import datetime as dt
from typing import Optional, List, Dict, Any

from app.database.repositories import OrganizationRepository
from app.organization.execution_repository import ExecutionRepository
from app.organization.execution_models import ExecutionStatus
from app.organization.verification_repository import VerificationRepository
from app.organization.verification_models import VerificationStatus, PlanVerificationStatus, OperationVerificationState
from app.services.folder_access import FolderAccessService
from app.services.protected_paths import ProtectedPathService
from app.security.paths import PathSecurityError, PathValidationError

class VerificationError(Exception):
    def __init__(self, status: VerificationStatus, message: str):
        self.status = status
        self.message = message
        super().__init__(message)

def verify_operation(operation_id: int, folder_service: FolderAccessService) -> OperationVerificationState:
    op_exec = ExecutionRepository.get_operation_execution(operation_id)
    if not op_exec:
        return VerificationRepository.save_verification(
            operation_id, "unknown", VerificationStatus.INCONCLUSIVE, "Execution record not found."
        )

    plan_id = op_exec.plan_id
    plan_data = OrganizationRepository.get_plan(plan_id)
    if not plan_data:
        return VerificationRepository.save_verification(
            operation_id, plan_id, VerificationStatus.INCONCLUSIVE, "Plan no longer exists."
        )

    op_detail = next((o for o in plan_data.get("operations", []) if o.get("id") == operation_id), None)
    if not op_detail:
        return VerificationRepository.save_verification(
            operation_id, plan_id, VerificationStatus.INCONCLUSIVE, "Operation detail missing from plan."
        )

    # Only completed operations can be positively verified.
    if op_exec.status != ExecutionStatus.COMPLETED:
        return VerificationRepository.save_verification(
            operation_id, plan_id, VerificationStatus.INCONCLUSIVE, f"Operation state is {op_exec.status.value}, not COMPLETED."
        )

    try:
        _perform_verification(op_detail, folder_service)
        return VerificationRepository.save_verification(
            operation_id, plan_id, VerificationStatus.VERIFIED, "Operation successfully verified."
        )
    except VerificationError as e:
        return VerificationRepository.save_verification(
            operation_id, plan_id, e.status, e.message
        )
    except Exception as e:
        return VerificationRepository.save_verification(
            operation_id, plan_id, VerificationStatus.INCONCLUSIVE, f"System error during verification: {str(e)}"
        )

def _perform_verification(op_detail: Dict[str, Any], folder_service: FolderAccessService) -> None:
    op_type = op_detail.get("type")
    source_str = op_detail.get("source")
    dest_str = op_detail.get("destination")

    prot_svc = ProtectedPathService(folder_service)

    if not dest_str:
        raise VerificationError(VerificationStatus.INCONCLUSIVE, "No destination path provided.")

    try:
        dest_path = folder_service.validate_path_in_root(dest_str, allow_nonexistent=True)
    except (PathSecurityError, PathValidationError) as e:
        raise VerificationError(VerificationStatus.INCONCLUSIVE, f"Destination validation failed: {str(e)}")

    if prot_svc.is_protected(dest_str):
        raise VerificationError(VerificationStatus.INCONCLUSIVE, "Destination is a protected path.")

    if op_type in ("MOVE", "RENAME"):
        if not source_str:
            raise VerificationError(VerificationStatus.INCONCLUSIVE, "No source path provided.")
        
        try:
            source_path = folder_service.validate_path_in_root(source_str, allow_nonexistent=True)
        except (PathSecurityError, PathValidationError) as e:
            raise VerificationError(VerificationStatus.INCONCLUSIVE, f"Source validation failed: {str(e)}")

        if prot_svc.is_protected(source_str):
            raise VerificationError(VerificationStatus.INCONCLUSIVE, "Source is a protected path.")

        if source_path.exists():
            raise VerificationError(VerificationStatus.FAILED, "Source path still exists.")
            
        if not dest_path.exists():
            raise VerificationError(VerificationStatus.FAILED, "Destination path does not exist.")

    elif op_type == "CREATE_FOLDER":
        if not dest_path.exists():
            raise VerificationError(VerificationStatus.FAILED, "Destination folder does not exist.")
        if not dest_path.is_dir():
            raise VerificationError(VerificationStatus.FAILED, "Destination exists but is not a folder.")
    else:
        raise VerificationError(VerificationStatus.INCONCLUSIVE, f"Unsupported operation type {op_type}.")

def verify_plan(plan_id: str, folder_service: FolderAccessService) -> PlanVerificationStatus:
    plan_data = OrganizationRepository.get_plan(plan_id)
    if not plan_data:
        raise ValueError("Plan not found")
        
    for op in plan_data.get("operations", []):
        verify_operation(op["id"], folder_service)
        
    return get_plan_verification_summary(plan_id)

def get_plan_verification_summary(plan_id: str) -> PlanVerificationStatus:
    plan_data = OrganizationRepository.get_plan(plan_id)
    total_ops = len(plan_data.get("operations", [])) if plan_data else 0
    
    verifications = VerificationRepository.get_plan_verification_status(plan_id)
    
    verified = sum(1 for v in verifications if v.status == VerificationStatus.VERIFIED)
    failed = sum(1 for v in verifications if v.status == VerificationStatus.FAILED)
    inconclusive = sum(1 for v in verifications if v.status == VerificationStatus.INCONCLUSIVE)
    unverified = total_ops - len(verifications)
    
    return PlanVerificationStatus(
        plan_id=plan_id,
        total_operations=total_ops,
        verified_count=verified,
        failed_count=failed,
        inconclusive_count=inconclusive,
        unverified_count=unverified,
        verifications=verifications
    )
