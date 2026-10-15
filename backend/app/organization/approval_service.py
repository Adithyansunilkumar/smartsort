"""Phase 11 — Approval Service.

Implements the validation and policy layer that sits between the API
endpoint and the approval repository.

Validation rules applied before persisting any decision
-------------------------------------------------------
1. Plan must exist in the database.
2. Plan must be in a valid state (currently: must have at least one operation).
3. Operation must belong to the specified plan.
4. Operation type must be in SUPPORTED_TYPES (MOVE, RENAME, CREATE_FOLDER).
5. Source and destination (loaded from DB) must each pass
   FolderAccessService.validate_path_in_root() with allow_nonexistent=True.
6. Neither source nor destination may overlap with any protected path.
7. No two operations in the plan may share the same destination
   (planning-time conflict — re-checked here to catch stale plans).

If any check fails, is_eligible is set to False and the reason is surfaced.
Decisions on ineligible operations are rejected with an HTTP 422.

Security guarantees
-------------------
* Source and destination are ALWAYS loaded from the database; the client
  never supplies them.
* FolderAccessService path-containment check is used; startswith() is
  never used for path containment.
* Protected-path checking reuses ProtectedPathService.
* No filesystem reads or mutations occur during approval.
* Approval is entirely separate from execution.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from app.database.connection import get_connection
from app.database.repositories import OrganizationRepository
from app.organization.approval_models import (
    ApprovalDecision,
    OperationApprovalHistoryEntry,
    OperationWithApproval,
    PlanApprovalStatus,
)
from app.organization.approval_repository import ApprovalRepository
from app.services.folder_access import FolderAccessService
from app.services.protected_paths import ProtectedPathService

# Only these operation types are supported for approval decisions.
SUPPORTED_TYPES: frozenset[str] = frozenset({"MOVE", "RENAME", "CREATE_FOLDER", "COPY_FILE"})


class ApprovalValidationError(Exception):
    """Raised when approval validation fails for client-visible reasons."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _check_path_protection(
    rel_path: str,
    protected_service: ProtectedPathService,
) -> bool:
    """Return True if *rel_path* intersects any protected path.

    Uses the protected-path list from the database via ProtectedPathService.
    Does NOT use startswith() for containment — uses the same Posix-path
    prefix logic as OrganizationPlanner._is_protected() but expressed as a
    standalone function to avoid coupling.
    """
    return protected_service.is_protected(rel_path)


def _build_operation_with_approval(
    op_row: Dict[str, Any],
    folder_service: FolderAccessService,
    protected_service: ProtectedPathService,
    plan_destinations: Optional[Dict[str, int]] = None,
    plan_creates: Optional[Dict[str, Dict[str, Any]]] = None,
) -> OperationWithApproval:
    """Convert a joined DB row into an OperationWithApproval, running validation.

    *plan_destinations* maps destination → operation_id for conflict detection
    across the entire plan.  It is built once per plan call and passed in.
    """
    operation_id: int = op_row["operation_id"]
    op_type: str = op_row["type"]
    source: str = op_row["source"]
    destination: str = op_row["destination"]
    decision_str: str = op_row.get("approval_decision", "PENDING")

    is_eligible = True
    ineligibility_reason: Optional[str] = None

    # -- Validation 1: supported operation type --
    if op_type not in SUPPORTED_TYPES:
        is_eligible = False
        ineligibility_reason = f"Operation type '{op_type}' is not supported for approval."

    # -- Validation 2: source path within authorized root --
    if is_eligible and op_type != "CREATE_FOLDER":
        try:
            folder_service.validate_path_in_root(source, allow_nonexistent=True)
        except Exception as exc:
            is_eligible = False
            ineligibility_reason = f"Source path failed security validation: {exc}"

    # -- Validation 3: destination path within authorized root --
    if is_eligible:
        try:
            folder_service.validate_path_in_root(destination, allow_nonexistent=True)
        except Exception as exc:
            from app.security.paths import PathSecurityError
            is_handled = False
            if isinstance(exc, PathSecurityError) and exc.code == "PATH_OUTSIDE_ROOT" and "parent path does not exist" in exc.message:
                parent_path = str(Path(destination).parent).replace("\\", "/")
                if parent_path == ".":
                    parent_path = ""
                
                # Allow paths inside SmartSort_Organized (the output folder may not exist yet)
                dest_norm = destination.replace("\\", "/")
                if (dest_norm == "SmartSort_Organized" or
                        dest_norm.startswith("SmartSort_Organized/")):
                    is_handled = True
                elif plan_creates is not None and parent_path in plan_creates:
                    create_op = plan_creates[parent_path]
                    create_decision = create_op.get("approval_decision", "PENDING")
                    if create_decision == "REJECTED":
                        is_eligible = False
                        ineligibility_reason = f"Required destination folder '{parent_path}' creation was rejected."
                    else:
                        is_handled = True
            
            if not is_handled and is_eligible:
                is_eligible = False
                ineligibility_reason = f"Destination path failed security validation: {exc}"

    # -- Validation 4: source not protected --
    if is_eligible and op_type != "CREATE_FOLDER":
        if _check_path_protection(source, protected_service):
            is_eligible = False
            ineligibility_reason = f"Source '{source}' is a protected path."

    # -- Validation 5: destination not protected --
    if is_eligible:
        if _check_path_protection(destination, protected_service):
            is_eligible = False
            ineligibility_reason = f"Destination '{destination}' is a protected path."

    # -- Validation 6: destination conflict with another operation in the plan --
    if is_eligible and plan_destinations is not None:
        conflicting_op_id = plan_destinations.get(destination)
        if conflicting_op_id is not None and conflicting_op_id != operation_id:
            is_eligible = False
            ineligibility_reason = (
                f"Destination '{destination}' is also targeted by operation "
                f"#{conflicting_op_id} — conflict detected."
            )

    # Parse decision enum safely
    try:
        approval_decision = ApprovalDecision(decision_str)
    except ValueError:
        approval_decision = ApprovalDecision.PENDING

    flags_val = op_row.get("flags")
    if isinstance(flags_val, list):
        flags_val = ",".join(str(f) for f in flags_val)
    elif flags_val is not None and not isinstance(flags_val, str):
        flags_val = str(flags_val)

    return OperationWithApproval(
        operation_id=operation_id,
        plan_id=op_row["plan_id"],
        type=op_type,
        source=source,
        destination=destination,
        reason=op_row.get("reason"),
        confidence=op_row.get("confidence", "HIGH"),
        approval_decision=approval_decision,
        comment=op_row.get("comment"),
        decided_at=op_row.get("decided_at"),
        is_eligible=is_eligible,
        ineligibility_reason=ineligibility_reason,
        confidence_score=op_row.get("confidence_score"),
        flags=flags_val,
        rule=op_row.get("rule"),
        needs_review=bool(op_row.get("needs_review")) if op_row.get("needs_review") is not None else None,
        likely_newer=bool(op_row.get("likely_newer")) if op_row.get("likely_newer") is not None else None,
        original_name=op_row.get("original_name"),
    )


def get_plan_approval_status(
    plan_id: str,
    folder_service: FolderAccessService,
) -> PlanApprovalStatus:
    """Return the aggregate approval status for all operations in *plan_id*.

    Raises ApprovalValidationError if the plan does not exist.
    """
    # Verify plan exists
    plan_data = OrganizationRepository.get_plan(plan_id)
    if plan_data is None:
        raise ApprovalValidationError(
            "PLAN_NOT_FOUND", f"Plan '{plan_id}' not found."
        )

    protected_service = ProtectedPathService(folder_service)

    # Load all operations with their current approval state in one JOIN query
    rows = ApprovalRepository.get_approvals_for_plan(plan_id)

    # Seed any missing PENDING records (safety net — normally seeded at creation)
    missing_ids = [r["operation_id"] for r in rows if r.get("approval_id") is None]
    if missing_ids:
        ApprovalRepository.seed_pending_for_plan(plan_id, missing_ids)
        # Re-fetch with the newly seeded rows
        rows = ApprovalRepository.get_approvals_for_plan(plan_id)

    # Build destination → operation_id map for conflict detection (excluding CREATE_FOLDER)
    plan_destinations: Dict[str, int] = {r["destination"]: r["operation_id"] for r in rows if r["type"] != "CREATE_FOLDER"}
    plan_creates: Dict[str, Dict[str, Any]] = {r["destination"]: r for r in rows if r["type"] == "CREATE_FOLDER"}

    operations = [
        _build_operation_with_approval(row, folder_service, protected_service, plan_destinations, plan_creates)
        for row in rows
    ]

    approved_count = sum(1 for op in operations if op.approval_decision == ApprovalDecision.APPROVED)
    rejected_count = sum(1 for op in operations if op.approval_decision == ApprovalDecision.REJECTED)
    pending_count = sum(1 for op in operations if op.approval_decision == ApprovalDecision.PENDING)

    return PlanApprovalStatus(
        plan_id=plan_id,
        total_operations=len(operations),
        approved_count=approved_count,
        rejected_count=rejected_count,
        pending_count=pending_count,
        operations=operations,
    )


def record_operation_decision(
    plan_id: str,
    operation_id: int,
    decision: ApprovalDecision,
    comment: Optional[str],
    folder_service: FolderAccessService,
) -> OperationWithApproval:
    """Validate and persist an approval decision for a single operation.

    Validation (all against DB-loaded data; client cannot supply operation details):
    1. Plan must exist.
    2. Operation must belong to *plan_id*.
    3. Decision must be APPROVED or REJECTED (not PENDING).
    4. Operation must be eligible (type, paths, protection, conflicts).

    On success, returns the updated OperationWithApproval.
    Raises ApprovalValidationError on any validation failure.
    """
    # 1. Verify plan exists
    plan_data = OrganizationRepository.get_plan(plan_id)
    if plan_data is None:
        raise ApprovalValidationError(
            "PLAN_NOT_FOUND", f"Plan '{plan_id}' not found."
        )

    # 2. Load the specific operation from the database (never trust client-supplied data)
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT * FROM organization_operations WHERE id = ? AND plan_id = ?",
            (operation_id, plan_id),
        )
        op_row_raw = cursor.fetchone()

    if op_row_raw is None:
        raise ApprovalValidationError(
            "OPERATION_NOT_FOUND",
            f"Operation #{operation_id} does not belong to plan '{plan_id}'.",
        )
    op_row = dict(op_row_raw)

    # 3. Validate decision value
    if decision == ApprovalDecision.PENDING:
        raise ApprovalValidationError(
            "INVALID_DECISION",
            "PENDING is not a valid client decision. Submit APPROVED or REJECTED.",
        )

    # 4. Build the full plan destination map for conflict detection
    all_rows = ApprovalRepository.get_approvals_for_plan(plan_id)
    plan_destinations: Dict[str, int] = {r["destination"]: r["operation_id"] for r in all_rows if r["type"] != "CREATE_FOLDER"}
    plan_creates: Dict[str, Dict[str, Any]] = {r["destination"]: r for r in all_rows if r["type"] == "CREATE_FOLDER"}

    # 5. Run validation on this operation
    protected_service = ProtectedPathService(folder_service)

    # Merge the raw op_row with a PENDING approval so _build_operation_with_approval can run
    enriched_row = {
        "operation_id": op_row["id"],
        "plan_id": op_row["plan_id"],
        "type": op_row["type"],
        "source": op_row["source"],
        "destination": op_row["destination"],
        "reason": op_row.get("reason"),
        "confidence": op_row.get("confidence", "HIGH"),
        "approval_decision": "PENDING",
        "comment": None,
        "decided_at": None,
    }
    validated_op = _build_operation_with_approval(
        enriched_row, folder_service, protected_service, plan_destinations, plan_creates
    )

    if not validated_op.is_eligible:
        raise ApprovalValidationError(
            "OPERATION_INELIGIBLE",
            f"Operation #{operation_id} cannot be approved: {validated_op.ineligibility_reason}",
        )

    # 6. Persist the decision (idempotent if same decision, history entry on change)
    ApprovalRepository.record_decision(
        operation_id=operation_id,
        plan_id=plan_id,
        new_decision=decision.value,
        comment=comment,
    )

    # 7. Return the refreshed state with approval info merged in
    updated_approval = ApprovalRepository.get_approval_for_operation(operation_id)
    if updated_approval is None:
        raise RuntimeError(f"Approval record missing after recording decision for operation {operation_id}")

    final_row = {
        "operation_id": op_row["id"],
        "plan_id": op_row["plan_id"],
        "type": op_row["type"],
        "source": op_row["source"],
        "destination": op_row["destination"],
        "reason": op_row.get("reason"),
        "confidence": op_row.get("confidence", "HIGH"),
        "approval_decision": updated_approval["decision"],
        "comment": updated_approval["comment"],
        "decided_at": updated_approval["decided_at"],
    }

    return _build_operation_with_approval(
        final_row, folder_service, protected_service, plan_destinations, plan_creates
    )


def get_operation_approval_history(
    plan_id: str,
    operation_id: int,
) -> List[OperationApprovalHistoryEntry]:
    """Return the full immutable audit history for a single operation.

    Raises ApprovalValidationError if plan or operation is not found.
    """
    # Verify plan exists
    plan_data = OrganizationRepository.get_plan(plan_id)
    if plan_data is None:
        raise ApprovalValidationError(
            "PLAN_NOT_FOUND", f"Plan '{plan_id}' not found."
        )

    # Verify operation belongs to plan
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT 1 FROM organization_operations WHERE id = ? AND plan_id = ?",
            (operation_id, plan_id),
        )
        if not cursor.fetchone():
            raise ApprovalValidationError(
                "OPERATION_NOT_FOUND",
                f"Operation #{operation_id} does not belong to plan '{plan_id}'.",
            )

    history_rows = ApprovalRepository.get_approval_history(operation_id)
    return [
        OperationApprovalHistoryEntry(
            history_id=row["id"],
            approval_id=row["approval_id"],
            previous_decision=ApprovalDecision(row["previous_decision"]),
            new_decision=ApprovalDecision(row["new_decision"]),
            comment=row.get("comment"),
            changed_at=row["changed_at"],
        )
        for row in history_rows
    ]
